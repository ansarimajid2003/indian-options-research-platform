from __future__ import annotations

import asyncio
import json
import base64
import subprocess
import struct
import unittest
from datetime import date, datetime, time
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from options_backtest.depth_cache import DepthCache, DepthLevel
from options_backtest.live_resolver import LiveDhanContractResolver
from options_backtest.clock_sync import clock_sync_status
from options_backtest.paper_engine import PaperTradingEngine
from options_backtest.paper_engine import _FEED_URL
from scripts.live.collect_order_book import (
    _depth_collection_settings,
    _iter_packets,
    _parse_packet,
    _write_depth_cache_snapshot,
    _write_collector_state,
    _write_restart_gap_if_needed,
)
from scripts.live.health_monitor import HealthMonitor, _jwt_expiry, _parse_timesync_offset
from scripts.live.paper_json_to_ledger import paper_trades_to_ledger, write_paper_reports
from scripts.live.validate_phase8_9 import (
    large_gap_records,
    secret_leaks,
    validate_depth_budget,
    validate_profile,
)


class _Resp:
    def __init__(self, body: dict) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._body


class LivePaperTests(unittest.TestCase):
    def test_resolver_accepts_current_dhan_option_chain_shape(self) -> None:
        body = {
            "data": {
                "last_price": 20010.0,
                "oc": {
                    "19950.000000": {
                        "ce": {"security_id": 111, "greeks": {"delta": 0.6}, "implied_volatility": 10.5},
                        "pe": {"security_id": 112, "greeks": {"delta": -0.4}, "implied_volatility": 11.5},
                    },
                    "20000.000000": {
                        "ce": {"security_id": 121, "greeks": {"delta": 0.5}, "implied_volatility": 12.5},
                        "pe": {"security_id": 122, "greeks": {"delta": -0.5}, "implied_volatility": 13.5},
                    },
                    "20050.000000": {
                        "ce": {"security_id": 131},
                        "pe": {"security_id": 132},
                    },
                },
            },
            "status": "success",
        }
        resolver = LiveDhanContractResolver(
            symbol="NIFTY",
            access_token="token",
            client_id="client",
            spot_security_id="13",
        )
        with patch("options_backtest.live_resolver.requests.post", return_value=_Resp(body)):
            sids = resolver.refresh_option_chain(date(2026, 5, 12), 13)

        self.assertEqual(len(sids), 6)
        self.assertEqual(resolver.chain_atm_strike(), 20000)
        selected = resolver.security_ids_around_chain_atm(offset_range=1)
        self.assertEqual(selected, ["111", "112", "121", "122", "131", "132"])
        meta = resolver.chain_metadata(["121"])
        self.assertEqual(meta["121"]["iv"], 12.5)
        self.assertEqual(meta["121"]["greeks"]["delta"], 0.5)

    def test_depth_parser_handles_stacked_packets(self) -> None:
        def packet(feed_code: int, sid: int, price: float) -> bytes:
            header = struct.pack("<hBBiI", 332, feed_code, 2, sid, 20)
            levels = b"".join(struct.pack("<dII", price + i, 100 + i, 1) for i in range(20))
            return header + levels

        raw = packet(41, 123, 10.0) + packet(51, 123, 10.5)
        packets = list(_iter_packets(raw))
        self.assertEqual(len(packets), 2)
        first = _parse_packet(packets[0])
        second = _parse_packet(packets[1])
        self.assertEqual(first[0], 41)
        self.assertEqual(second[0], 51)
        self.assertEqual(first[2][0], DepthLevel(price=10.0, quantity=100, orders=1))

    def test_feed_url_uses_client_id_placeholder(self) -> None:
        url = _FEED_URL.format(token="tok", client_id="cid")
        self.assertIn("token=tok", url)
        self.assertIn("clientId=cid", url)

    def test_depth_collection_uses_configured_major_indices(self) -> None:
        profile = {
            "depth_collection": {
                "symbols": ["NIFTY", "MIDCPNIFTY", "SENSEX"],
                "atm_offset_range": 20,
                "max_depth_connections": 5,
            },
            "symbols": {
                "NIFTY": {"trade": True, "depth_source": "dhan_20depth"},
                "MIDCPNIFTY": {"trade": True, "depth_source": "dhan_20depth"},
                "SENSEX": {"trade": True, "depth_source": "top_of_book"},
            },
        }
        symbols, offset_range, max_connections = _depth_collection_settings(profile)
        self.assertEqual(symbols, ["NIFTY", "MIDCPNIFTY"])
        self.assertEqual(offset_range, 20)
        self.assertEqual(max_connections, 5)

    def test_paper_json_writes_report_artifacts(self) -> None:
        root = Path("tmp_live_tests") / "paper_report_case"
        trades_dir = root / "paper_trades"
        trades_dir.mkdir(parents=True, exist_ok=True)
        trade_path = trades_dir / "20260512.json"
        trade_path.write_text(
            json.dumps(
                [
                    {
                        "symbol": "NIFTY",
                        "expiry": "2026-05-12",
                        "strategy": "IronCondorWing6",
                        "entry_time": "2026-05-12T09:20:00+05:30",
                        "exit_time": "2026-05-12T15:20:00+05:30",
                        "entry_reason": "time_entry",
                        "exit_reason": "time_exit",
                        "lot_size": 65,
                        "entry_fills": [
                            {"side": "SELL", "quantity": 65, "price": 100.0, "charges": 10.0, "security_id": "1", "leg_role": "short_call"},
                            {"side": "BUY", "quantity": 65, "price": 20.0, "charges": 10.0, "security_id": "2", "leg_role": "long_call"},
                        ],
                        "exit_fills": [
                            {"side": "BUY", "quantity": 65, "price": 80.0, "charges": 10.0, "security_id": "1", "leg_role": "short_call"},
                            {"side": "SELL", "quantity": 65, "price": 10.0, "charges": 10.0, "security_id": "2", "leg_role": "long_call"},
                        ],
                    }
                ]
            )
        )
        ledger = paper_trades_to_ledger(trade_path, date(2026, 5, 12))
        self.assertEqual(len(ledger), 1)
        self.assertAlmostEqual(float(ledger["net_pnl"].iloc[0]), 610.0)
        md_path, json_path, ledger_path = write_paper_reports(trade_path, date(2026, 5, 12), root)
        self.assertTrue(md_path.exists())
        self.assertTrue(json_path.exists())
        self.assertTrue(ledger_path.exists())

    def test_health_monitor_parses_clock_offset_and_jwt_expiry(self) -> None:
        self.assertAlmostEqual(_parse_timesync_offset("+1500ms"), 1.5)
        self.assertAlmostEqual(_parse_timesync_offset("-250us"), -0.00025)
        self.assertIsNone(_parse_timesync_offset("not-an-offset"))

        payload = {"exp": 1778497200}
        body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
        token = f"header.{body}.sig"
        exp = _jwt_expiry(token)
        self.assertIsNotNone(exp)
        self.assertEqual(exp, datetime.fromtimestamp(1778497200, tz=ZoneInfo("Asia/Kolkata")))

    def test_clock_sync_status_flags_high_drift(self) -> None:
        def runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            if cmd == ["timedatectl", "timesync-status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="       Offset: +2246ms\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        status = clock_sync_status(max_offset_seconds=2.0, runner=runner)
        self.assertFalse(status.healthy)
        self.assertEqual(status.reason, "clock_drift_high")
        self.assertAlmostEqual(status.offset_seconds or 0.0, 2.246)

    def test_clock_sync_status_accepts_small_drift(self) -> None:
        def runner(cmd: list[str], **kwargs) -> subprocess.CompletedProcess[str]:
            if cmd == ["timedatectl", "timesync-status"]:
                return subprocess.CompletedProcess(cmd, 0, stdout="       Offset: -3.5ms\n")
            return subprocess.CompletedProcess(cmd, 0, stdout="")

        status = clock_sync_status(max_offset_seconds=2.0, runner=runner)
        self.assertTrue(status.healthy)
        self.assertEqual(status.reason, "clock_synced")

    def test_depth_cache_snapshot_writer_exports_readiness(self) -> None:
        root = Path("tmp_live_tests") / "depth_snapshot_case"
        root.mkdir(parents=True, exist_ok=True)
        cache = DepthCache()
        now = pd.Timestamp.now(tz="Asia/Kolkata")
        cache.update_bid_packet("1", now, [DepthLevel(price=10.0, quantity=100, orders=1)])
        cache.update_ask_packet("1", now, [DepthLevel(price=10.5, quantity=100, orders=1)])

        _write_depth_cache_snapshot(root, "20260512", cache, ["1"])
        payload = json.loads((root / "snapshots" / "latest_depth_cache.json").read_text())
        self.assertEqual(payload["ready"], 1)
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["ready_pct"], 100.0)

    def test_collector_restart_writes_gap_sentinel(self) -> None:
        root = Path("tmp_live_tests") / "collector_restart_case"
        (root / "snapshots").mkdir(parents=True, exist_ok=True)
        _write_collector_state(root, "20260512", "running", ["1", "2"])

        wrote = _write_restart_gap_if_needed(root, "20260512")
        self.assertTrue(wrote)
        gap_path = root / "alerts" / "20260512_gaps.jsonl"
        record = json.loads(gap_path.read_text().splitlines()[-1])
        self.assertEqual(record["reason"], "process_restart")
        self.assertEqual(record["symbol"], "NSE_MAJOR_INDICES")

    def test_phase8_validator_rejects_large_gap_sentinels(self) -> None:
        root = Path("tmp_live_tests") / "gap_validator_case"
        (root / "alerts").mkdir(parents=True, exist_ok=True)
        (root / "alerts" / "20260512_gaps.jsonl").write_text(
            json.dumps({
                "type": "data_gap",
                "symbol": "NIFTY",
                "gap_start": "2026-05-12T10:00:00+05:30",
                "gap_end": "2026-05-12T10:45:00+05:30",
                "reason": "process_restart",
                "gap_minutes": 45.0,
            }) + "\n"
        )
        bad = large_gap_records(root, "20260512")
        self.assertEqual(len(bad), 1)
        self.assertEqual(bad[0]["gap_minutes"], 45.0)

    def test_phase8_validator_flags_secret_like_strings(self) -> None:
        root = Path("tmp_live_tests") / "secret_scan_case"
        root.mkdir(parents=True, exist_ok=True)
        (root / "sample.log").write_text("token=" + ("A" * 120))
        leaks = secret_leaks([root])
        self.assertEqual(len(leaks), 1)
        self.assertEqual(leaks[0][1], "token_like_string")

    def test_phase9_profile_and_websocket_budget_contract(self) -> None:
        profile = json.loads(Path("configs/live/wing6_4x1_all_vix_filtered.json").read_text())
        self.assertEqual(validate_profile(profile).status, "PASS")
        budget = validate_depth_budget(profile)
        self.assertEqual(budget.status, "PASS")
        self.assertIn("102 instruments", budget.detail)

        too_small = json.loads(json.dumps(profile))
        too_small["depth_collection"]["max_depth_connections"] = 2
        self.assertEqual(validate_depth_budget(too_small).status, "FAIL")

    def test_resume_checkpoint_metadata_reaches_eod_summary(self) -> None:
        root = Path("tmp_live_tests") / "resume_summary_case"
        engine = PaperTradingEngine(
            profile={"profile_name": "test", "symbols": {}, "vix": {}},
            session_date=date(2026, 5, 12),
            depth_cache=DepthCache(),
            access_token="token",
            client_id="client",
            live_root=root,
        )
        checkpoint = {
            "session_date": "2026-05-12",
            "written_at": "2026-05-12T10:00:00+05:30",
            "open_positions": [
                {
                    "symbol": "NIFTY",
                    "expiry": "2026-05-12",
                    "lots": 1,
                    "lot_size": 65,
                    "entry_time": "2026-05-12T09:20:00+05:30",
                    "legs": [],
                    "entry_credit": 0.0,
                    "entry_charges": 0.0,
                }
            ],
        }
        engine.resume_from_checkpoint(checkpoint)
        engine._generate_eod_report()

        summary = json.loads((root / "reports" / "20260512_eod_summary.json").read_text())
        self.assertTrue(summary["resumed_after_crash"])
        self.assertEqual(summary["crash_gap_start"], "2026-05-12T10:00:00+05:30")
        self.assertIsNotNone(summary["gap_minutes"])

    def test_health_monitor_alerts_after_two_bad_freshness_checks(self) -> None:
        async def run_case() -> dict:
            root = Path("tmp_live_tests") / "health_monitor_case"
            (root / "snapshots").mkdir(parents=True, exist_ok=True)
            now = datetime.now(tz=ZoneInfo("Asia/Kolkata")).isoformat()
            (root / "snapshots" / "latest_feed_state.json").write_text(json.dumps({
                "written_at": now,
                "connected": True,
                "quote_freshness_pct": 90.0,
            }))
            monitor = HealthMonitor(profile={}, live_root=root)
            with patch("scripts.live.health_monitor._is_feed_active", return_value=True):
                await monitor._check_quote_freshness()
                await monitor._check_quote_freshness()
            alert_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            return json.loads(alert_path.read_text().splitlines()[-1])

        alert = asyncio.run(run_case())
        self.assertEqual(alert["component"], "quote_freshness")
        self.assertEqual(alert["reason"], "quote_freshness_low")

    def test_health_monitor_flags_missing_vix_warmup(self) -> None:
        async def run_case() -> dict:
            root = Path("tmp_live_tests") / "vix_warmup_case"
            (root / "snapshots").mkdir(parents=True, exist_ok=True)
            (root / "snapshots" / "latest_feed_state.json").write_text(json.dumps({
                "written_at": datetime.now(tz=ZoneInfo("Asia/Kolkata")).isoformat(),
                "connected": True,
                "core_quotes": {"INDIA_VIX": {"seen": False, "fresh": False}},
            }))
            monitor = HealthMonitor(profile={}, live_root=root)
            with patch("scripts.live.health_monitor._is_feed_active", return_value=True):
                await monitor._check_vix_warmup()
            alert_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            return json.loads(alert_path.read_text().splitlines()[-1])

        alert = asyncio.run(run_case())
        self.assertEqual(alert["component"], "vix")
        self.assertEqual(alert["reason"], "vix_quote_missing")

    def test_health_monitor_flags_chain_fetch_failures(self) -> None:
        async def run_case() -> dict:
            root = Path("tmp_live_tests") / "chain_fetch_case"
            (root / "snapshots").mkdir(parents=True, exist_ok=True)
            (root / "snapshots" / "latest_feed_state.json").write_text(json.dumps({
                "written_at": datetime.now(tz=ZoneInfo("Asia/Kolkata")).isoformat(),
                "connected": True,
                "chain_status": {
                    "NIFTY": {"status": "loaded", "instrument_count": 34},
                    "SENSEX": {"status": "failed", "error": "HTTP 429"},
                },
            }))
            profile = {
                "symbols": {
                    "NIFTY": {"trade": True},
                    "SENSEX": {"trade": True},
                    "BANKNIFTY": {"trade": False},
                }
            }
            monitor = HealthMonitor(profile=profile, live_root=root)
            with patch("scripts.live.health_monitor._is_trading_day", return_value=True), \
                    patch("scripts.live.health_monitor._ist_time", return_value=time(9, 18)):
                await monitor._check_chain_fetch()
            alert_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            return json.loads(alert_path.read_text().splitlines()[-1])

        alert = asyncio.run(run_case())
        self.assertEqual(alert["component"], "chain_fetch")
        self.assertEqual(alert["reason"], "chain_not_loaded_sensex")

    def test_health_monitor_flags_stuck_1min_order_book(self) -> None:
        async def run_case() -> dict:
            import pyarrow as pa
            import pyarrow.parquet as pq

            root = Path("tmp_live_tests") / "one_min_stuck_case"
            date_str = datetime.now(tz=ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d")
            out_dir = root / "order_book_1min" / date_str
            out_dir.mkdir(parents=True, exist_ok=True)
            table = pa.table({
                "timestamp": ["2026-05-12T09:15:00+05:30"],
                "open": [1.0],
                "high": [1.0],
                "low": [1.0],
                "close": [1.0],
                "volume": [1],
                "spread_pct_mean": [0.1],
            })
            pq.write_table(table, out_dir / "NIFTY_2026-05-12_25000_CE.parquet")
            monitor = HealthMonitor(profile={}, live_root=root)
            with patch("scripts.live.health_monitor._is_trading_day", return_value=True), \
                    patch("scripts.live.health_monitor._ist_time", return_value=time(9, 31)):
                await monitor._check_1min_ohlcv_progression()
            alert_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            return json.loads(alert_path.read_text().splitlines()[-1])

        alert = asyncio.run(run_case())
        self.assertEqual(alert["component"], "order_book_1min")
        self.assertEqual(alert["reason"], "one_min_stuck")

    def test_health_monitor_alert_state_has_split_uptime(self) -> None:
        root = Path("tmp_live_tests") / "split_uptime_case"
        monitor = HealthMonitor(profile={}, live_root=root)
        monitor._record_uptime("paper_engine", [True, True])
        monitor._record_uptime("depth_collector", [True, False])
        monitor._record_uptime("full_readiness", [True, False])
        monitor._write_alert_state()
        state = json.loads((root / "snapshots" / "latest_alert_state.json").read_text())
        self.assertEqual(state["paper_engine_uptime_pct"], 100.0)
        self.assertEqual(state["depth_collector_uptime_pct"], 0.0)
        self.assertEqual(state["full_readiness_uptime_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()

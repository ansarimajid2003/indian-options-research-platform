from __future__ import annotations

import asyncio
import json
import base64
import shutil
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

    def test_health_monitor_skips_token_expiry_alerts_on_non_trading_day(self) -> None:
        async def run_case() -> tuple[bool | None, bool, dict[str, dict]]:
            root = Path("tmp_live_tests") / "token_non_trading_day"
            shutil.rmtree(root, ignore_errors=True)
            monitor = HealthMonitor(profile={}, live_root=root)
            session_date = date(2026, 5, 16)  # Saturday
            exp = datetime(2026, 5, 16, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
            body = base64.urlsafe_b64encode(json.dumps({"exp": int(exp.timestamp())}).encode()).decode().rstrip("=")
            monitor._access_token = f"header.{body}.sig"
            monitor._session_date = session_date
            monitor._date_str = session_date.strftime("%Y%m%d")
            monitor._alerts_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            with patch("scripts.live.health_monitor._ist_time", return_value=time(16, 9)), \
                    patch("scripts.live.health_monitor._now_ist", return_value=datetime(2026, 5, 16, 16, 9, tzinfo=ZoneInfo("Asia/Kolkata"))):
                result = await monitor._check_token_expiry()
            return result, monitor._alerts_path.exists(), dict(monitor._active_alerts)

        result, alert_file_exists, active = asyncio.run(run_case())
        self.assertIsNone(result)
        self.assertFalse(alert_file_exists)
        self.assertEqual(active, {})

    def test_health_monitor_flags_token_expiry_before_eod_on_trading_day(self) -> None:
        async def run_case() -> dict:
            root = Path("tmp_live_tests") / "token_trading_day"
            shutil.rmtree(root, ignore_errors=True)
            monitor = HealthMonitor(profile={}, live_root=root)
            session_date = date(2026, 5, 15)  # Friday
            exp = datetime(2026, 5, 15, 15, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
            body = base64.urlsafe_b64encode(json.dumps({"exp": int(exp.timestamp())}).encode()).decode().rstrip("=")
            monitor._access_token = f"header.{body}.sig"
            monitor._session_date = session_date
            monitor._date_str = session_date.strftime("%Y%m%d")
            monitor._alerts_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            with patch("scripts.live.health_monitor._ist_time", return_value=time(9, 1)), \
                    patch("scripts.live.health_monitor._now_ist", return_value=datetime(2026, 5, 15, 9, 1, tzinfo=ZoneInfo("Asia/Kolkata"))):
                await monitor._check_token_expiry()
            return json.loads(monitor._alerts_path.read_text().splitlines()[-1])

        alert = asyncio.run(run_case())
        self.assertEqual(alert["component"], "token")
        self.assertEqual(alert["reason"], "token_expires_before_eod")

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
        self.assertIn("246 instruments", budget.detail)

        too_small = json.loads(json.dumps(profile))
        too_small["depth_collection"]["max_depth_connections"] = 4
        self.assertEqual(validate_depth_budget(too_small).status, "FAIL")

    def test_live_feed_skips_nse_options_when_depth_collector_owns_them(self) -> None:
        engine = PaperTradingEngine(
            profile={"profile_name": "test", "symbols": {}, "vix": {}},
            session_date=date(2026, 5, 18),
            depth_cache=DepthCache(),
            access_token="token",
            client_id="client",
            live_root=Path("tmp_live_tests") / "feed_scope_case",
        )
        self.assertFalse(engine._needs_live_option_feed(
            "NIFTY",
            {"depth_source": "dhan_20depth", "min_dte": 1},
            date(2026, 5, 19),
        ))
        self.assertFalse(engine._needs_live_option_feed(
            "SENSEX",
            {"depth_source": "top_of_book", "min_dte": 1, "max_dte": 2},
            date(2026, 5, 21),
        ))
        self.assertTrue(engine._needs_live_option_feed(
            "SENSEX",
            {"depth_source": "top_of_book", "min_dte": 1, "max_dte": 3},
            date(2026, 5, 21),
        ))

    def test_feed_state_treats_zero_option_feed_sample_as_healthy(self) -> None:
        import os
        root = Path("tmp_live_tests") / "feed_state_core_only"
        shutil.rmtree(root, ignore_errors=True)
        old_env = os.environ.pop("IM_SNAPSHOT_DIR", None)
        try:
            engine = PaperTradingEngine(
                profile={"profile_name": "test", "symbols": {}, "vix": {}},
                session_date=date(2026, 5, 18),
                depth_cache=DepthCache(),
                access_token="token",
                client_id="client",
                live_root=root,
            )
            engine._ensure_dirs()
            engine._feed_connected = True
            engine._subscribed_ids = engine._core_subscriptions()
            engine._write_feed_state()
            state = json.loads((root / "snapshots" / "latest_feed_state.json").read_text())
        finally:
            if old_env is None:
                os.environ.pop("IM_SNAPSHOT_DIR", None)
            else:
                os.environ["IM_SNAPSHOT_DIR"] = old_env

        self.assertEqual(state["quote_freshness_pct"], 100.0)
        self.assertEqual(state["quote_freshness_sample_size"], 0)

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


# ── Snapshot directory routing (IM_SNAPSHOT_DIR) ──────────────────────────────


class LivePathsTests(unittest.TestCase):
    """Verify tmpfs/durable snapshot routing fixes WD-fsync false-positives."""

    def setUp(self) -> None:
        import os
        self._old_env = os.environ.pop("IM_SNAPSHOT_DIR", None)

    def tearDown(self) -> None:
        import os
        if self._old_env is None:
            os.environ.pop("IM_SNAPSHOT_DIR", None)
        else:
            os.environ["IM_SNAPSHOT_DIR"] = self._old_env

    def test_default_resolver_returns_live_root_snapshots(self) -> None:
        from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
        root = Path("tmp_live_tests") / "live_paths_default"
        self.assertEqual(resolve_snapshot_dir(root), root / "snapshots")
        self.assertEqual(resolve_durable_dir(root), root / "snapshots")

    def test_env_override_routes_liveness_but_not_durable(self) -> None:
        import os
        from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
        root = Path("tmp_live_tests") / "live_paths_override"
        override = Path("tmp_live_tests") / "im_snapshots_tmpfs"
        os.environ["IM_SNAPSHOT_DIR"] = str(override)
        self.assertEqual(resolve_snapshot_dir(root), override)
        # durable_dir must remain on the configured live_root regardless of env.
        self.assertEqual(resolve_durable_dir(root), root / "snapshots")

    def test_paper_engine_routes_durable_checkpoint_separately(self) -> None:
        import os
        from options_backtest.depth_cache import DepthCache
        from options_backtest.paper_engine import PaperTradingEngine
        root = Path("tmp_live_tests") / "engine_split_dirs"
        override = Path("tmp_live_tests") / "engine_split_dirs_tmpfs"
        # Wipe both so we observe writes cleanly.
        import shutil
        shutil.rmtree(root, ignore_errors=True)
        shutil.rmtree(override, ignore_errors=True)
        os.environ["IM_SNAPSHOT_DIR"] = str(override)
        engine = PaperTradingEngine(
            profile={"profile_name": "test", "symbols": {}, "vix": {}},
            session_date=date(2026, 5, 16),
            depth_cache=DepthCache(),
            access_token="token",
            client_id="client",
            live_root=root,
        )
        engine._ensure_dirs()
        engine._write_process_health()
        engine._write_checkpoint()
        # process_health is a liveness snapshot → tmpfs.
        self.assertTrue((override / "latest_process_health.json").exists())
        self.assertFalse((root / "snapshots" / "latest_process_health.json").exists())
        # checkpoint is durable → WD (live_root/snapshots).
        self.assertTrue((root / "snapshots" / "latest_open_positions.json").exists())
        self.assertFalse((override / "latest_open_positions.json").exists())

    def test_dashboard_bridge_reads_durable_snaps_from_live_root(self) -> None:
        import os
        from options_backtest.dashboard_bridge import DashboardBridge
        root = Path("tmp_live_tests") / "bridge_split_dirs"
        override = Path("tmp_live_tests") / "bridge_split_dirs_tmpfs"
        os.environ["IM_SNAPSHOT_DIR"] = str(override)
        bridge = DashboardBridge(live_root=root)
        # Liveness file resolves to override (tmpfs).
        self.assertEqual(bridge._snap("latest_feed_state.json"), override / "latest_feed_state.json")
        # Durable files resolve to live_root/snapshots, ignoring the override.
        self.assertEqual(
            bridge._snap("latest_open_positions.json"),
            root / "snapshots" / "latest_open_positions.json",
        )
        self.assertEqual(
            bridge._snap("latest_eod_snapshot.json"),
            root / "snapshots" / "latest_eod_snapshot.json",
        )
        self.assertEqual(
            bridge._snap("latest_depth_collector_state.json"),
            root / "snapshots" / "latest_depth_collector_state.json",
        )


# ── Health monitor: engine_stall grouping & no auto-restart ───────────────────


class EngineStallTests(unittest.TestCase):
    """The four staleness symptoms collapse to one engine_stall incident, and
    the monitor never restarts live-paper.service itself."""

    def setUp(self) -> None:
        import os
        import shutil
        self._old_env = os.environ.pop("IM_SNAPSHOT_DIR", None)
        # Each test gets a clean root so we can assert exact JSONL contents.
        shutil.rmtree(Path("tmp_live_tests") / "engine_stall_grouped", ignore_errors=True)
        shutil.rmtree(Path("tmp_live_tests") / "engine_stall_fresh", ignore_errors=True)
        shutil.rmtree(Path("tmp_live_tests") / "engine_stall_no_restart", ignore_errors=True)

    def tearDown(self) -> None:
        import os
        if self._old_env is None:
            os.environ.pop("IM_SNAPSHOT_DIR", None)
        else:
            os.environ["IM_SNAPSHOT_DIR"] = self._old_env

    def _stale_iso(self, seconds: float) -> str:
        now = datetime.now(tz=ZoneInfo("Asia/Kolkata"))
        return (now - pd.Timedelta(seconds=seconds)).isoformat()

    def _fresh_iso(self) -> str:
        return datetime.now(tz=ZoneInfo("Asia/Kolkata")).isoformat()

    def _write_snaps(self, root: Path, process_age: float, feed_age: float, depth_age: float) -> None:
        snap = root / "snapshots"
        snap.mkdir(parents=True, exist_ok=True)
        (snap / "latest_process_health.json").write_text(json.dumps({
            "written_at": self._stale_iso(process_age) if process_age > 0 else self._fresh_iso(),
            "pid": 1234, "phase": "monitoring", "open_positions": 0,
            "session_date": date.today().isoformat(),
        }))
        (snap / "latest_feed_state.json").write_text(json.dumps({
            "written_at": self._stale_iso(feed_age) if feed_age > 0 else self._fresh_iso(),
            "connected": True, "quote_freshness_pct": 99.0,
            "core_quotes": {"INDIA_VIX": {"seen": True, "fresh": True}},
        }))
        (snap / "latest_depth_cache.json").write_text(json.dumps({
            "written_at": self._stale_iso(depth_age) if depth_age > 0 else self._fresh_iso(),
            "ready_pct": 99.0, "ready": 100, "total": 100, "tracked_security_ids": 100,
            "configured_security_ids": 100, "by_symbol": {},
        }))

    def test_engine_stall_collapses_four_staleness_alerts_into_one(self) -> None:
        async def run_case() -> dict:
            import sys as _sys
            root = Path("tmp_live_tests") / "engine_stall_grouped"
            self._write_snaps(root, process_age=120, feed_age=60, depth_age=60)
            monitor = HealthMonitor(profile={}, live_root=root)
            monitor._service_became_active_mono = 0.0
            monitor._service_was_active = True

            def _systemd_active(cmd, *args, **kwargs):
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("scripts.live.health_monitor._is_market_hours", return_value=True), \
                    patch("scripts.live.health_monitor._is_feed_active", return_value=True), \
                    patch("scripts.live.health_monitor._is_trading_day", return_value=True), \
                    patch("scripts.live.health_monitor.shutil.which", return_value="/bin/systemctl"), \
                    patch("scripts.live.health_monitor.subprocess.run", side_effect=_systemd_active), \
                    patch.object(_sys, "platform", "linux"):
                monitor._stall_indicators = {}
                await monitor._check_runner_process()
                await monitor._check_feed_state()
                await monitor._check_depth_snapshot()
                await monitor._check_collector_heartbeat()
                await monitor._emit_engine_stall()
            return {key: rec for key, rec in monitor._active_alerts.items()}

        active = asyncio.run(run_case())
        stall_keys = [k for k in active if "engine_stall" in k]
        self.assertEqual(len(stall_keys), 1, f"expected 1 engine_stall alert, got {list(active)}")
        # Old per-symptom keys must not appear.
        self.assertFalse(any("process_wedged" in k for k in active))
        self.assertFalse(any("feed_state_stale" in k for k in active))
        self.assertFalse(any("depth_snapshot_stale" in k for k in active))
        self.assertFalse(any("collector_heartbeat_stale" in k for k in active))
        # Detail should include the indicator names so an operator can diagnose.
        stall_msg = active[stall_keys[0]]["message"]
        for indicator in ("process_health", "feed_state", "depth_snapshot", "collector_heartbeat"):
            self.assertIn(indicator, stall_msg)

    def test_engine_stall_clears_when_all_snaps_fresh(self) -> None:
        async def run_case() -> dict:
            root = Path("tmp_live_tests") / "engine_stall_fresh"
            self._write_snaps(root, process_age=0, feed_age=0, depth_age=0)
            monitor = HealthMonitor(profile={}, live_root=root)
            monitor._service_became_active_mono = 0.0
            monitor._service_was_active = True
            with patch("scripts.live.health_monitor._is_market_hours", return_value=True), \
                    patch("scripts.live.health_monitor._is_feed_active", return_value=True), \
                    patch("scripts.live.health_monitor._is_trading_day", return_value=True), \
                    patch("shutil.which", return_value=None):
                monitor._stall_indicators = {}
                await monitor._check_runner_process()
                await monitor._check_feed_state()
                await monitor._check_depth_snapshot()
                await monitor._check_collector_heartbeat()
                await monitor._emit_engine_stall()
            return monitor._active_alerts

        active = asyncio.run(run_case())
        self.assertFalse(any("engine_stall" in k for k in active),
                         f"engine_stall should clear when snaps fresh, got {list(active)}")
        self.assertFalse(any("market_data_lag" in k for k in active),
                         f"market_data_lag should clear when snaps fresh, got {list(active)}")

    def test_feed_and_depth_staleness_without_process_stall_is_warning(self) -> None:
        async def run_case() -> dict:
            import sys as _sys
            root = Path("tmp_live_tests") / "market_data_lag_only"
            self._write_snaps(root, process_age=0, feed_age=60, depth_age=60)
            monitor = HealthMonitor(profile={}, live_root=root)
            monitor._service_became_active_mono = 0.0
            monitor._service_was_active = True

            def _systemd_active(cmd, *args, **kwargs):
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("scripts.live.health_monitor._is_market_hours", return_value=True), \
                    patch("scripts.live.health_monitor._is_feed_active", return_value=True), \
                    patch("scripts.live.health_monitor._is_trading_day", return_value=True), \
                    patch("scripts.live.health_monitor.shutil.which", return_value="/bin/systemctl"), \
                    patch("scripts.live.health_monitor.subprocess.run", side_effect=_systemd_active), \
                    patch.object(_sys, "platform", "linux"):
                monitor._stall_indicators = {}
                await monitor._check_runner_process()
                await monitor._check_feed_state()
                await monitor._check_depth_snapshot()
                await monitor._check_collector_heartbeat()
                await monitor._emit_engine_stall()
            return {key: rec for key, rec in monitor._active_alerts.items()}

        active = asyncio.run(run_case())
        lag_keys = [k for k in active if "market_data_lag" in k]
        self.assertEqual(len(lag_keys), 1, f"expected 1 market_data_lag alert, got {list(active)}")
        self.assertEqual(active[lag_keys[0]]["severity"], "warning")
        self.assertFalse(any("engine_stall" in k for k in active))

    def test_alert_storm_simulation_one_alert_zero_restarts(self) -> None:
        """100 critical-check ticks with persistent stale snapshots — the 5/15
        scenario at higher density. Verify the storm produces ≤ 2 JSONL rows
        (one ACTIVE + one RECOVERED after stall clears) and zero restart calls."""
        async def run_case() -> tuple[int, int, int]:
            import sys as _sys
            root = Path("tmp_live_tests") / "engine_stall_storm"
            import shutil as _shutil
            _shutil.rmtree(root, ignore_errors=True)
            self._write_snaps(root, process_age=120, feed_age=120, depth_age=120)
            monitor = HealthMonitor(profile={}, live_root=root)
            monitor._service_became_active_mono = 0.0
            monitor._service_was_active = True
            restart_calls: list[list[str]] = []

            def _mock_run(cmd, *args, **kwargs):
                if "systemctl" in cmd[0] and len(cmd) > 1 and cmd[1] == "restart":
                    restart_calls.append(cmd)
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("scripts.live.health_monitor._is_market_hours", return_value=True), \
                    patch("scripts.live.health_monitor._is_feed_active", return_value=True), \
                    patch("scripts.live.health_monitor._is_trading_day", return_value=True), \
                    patch("scripts.live.health_monitor.shutil.which", return_value="/bin/systemctl"), \
                    patch("scripts.live.health_monitor.subprocess.run", side_effect=_mock_run), \
                    patch.object(_sys, "platform", "linux"):
                # 100 ticks of stale state — simulates ~25 min of bad snapshots
                # at the 15-second critical-check cadence.
                for _ in range(100):
                    monitor._stall_indicators = {}
                    await monitor._check_runner_process()
                    await monitor._check_feed_state()
                    await monitor._check_depth_snapshot()
                    await monitor._check_collector_heartbeat()
                    await monitor._emit_engine_stall()

            alerts_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            jsonl_rows = (
                len([ln for ln in alerts_path.read_text().splitlines() if ln.strip()])
                if alerts_path.exists() else 0
            )
            return jsonl_rows, len(restart_calls), len(monitor._active_alerts)

        rows, restarts, active = asyncio.run(run_case())
        self.assertEqual(restarts, 0, "monitor must never restart live-paper")
        # JSONL rate-limit is 600s (10 min); 25 min of stall could write 3 rows
        # at the rate-limit ceiling. Pre-fix was 100+ rows in this window.
        self.assertLessEqual(rows, 3, f"expected ≤3 JSONL rows under storm, got {rows}")
        self.assertGreaterEqual(rows, 1, "at least the first ACTIVE engine_stall row must be written")
        self.assertEqual(active, 1, f"exactly one active engine_stall alert, got {active}")

    def test_monitor_does_not_restart_live_paper_on_wedge(self) -> None:
        """The 5/15 audit traced 61 restarts to monitor-initiated restarts on
        snapshot staleness. The monitor must never call systemctl restart."""
        async def run_case() -> int:
            root = Path("tmp_live_tests") / "engine_stall_no_restart"
            self._write_snaps(root, process_age=300, feed_age=300, depth_age=300)
            monitor = HealthMonitor(profile={}, live_root=root)
            monitor._service_became_active_mono = 0.0
            monitor._service_was_active = True
            restart_calls: list[list[str]] = []

            def _mock_run(cmd, *args, **kwargs):
                if "systemctl" in cmd[0] and len(cmd) > 1 and cmd[1] == "restart":
                    restart_calls.append(cmd)
                # Default: pretend systemd reports the service is active.
                return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

            with patch("scripts.live.health_monitor._is_market_hours", return_value=True), \
                    patch("scripts.live.health_monitor._is_feed_active", return_value=True), \
                    patch("scripts.live.health_monitor._is_trading_day", return_value=True), \
                    patch("scripts.live.health_monitor.shutil.which", return_value="/bin/systemctl"), \
                    patch("scripts.live.health_monitor.subprocess.run", side_effect=_mock_run):
                # Make platform check think we're on Linux.
                import sys as _sys
                with patch.object(_sys, "platform", "linux"):
                    monitor._stall_indicators = {}
                    await monitor._check_runner_process()
                    await monitor._check_feed_state()
                    await monitor._check_depth_snapshot()
                    await monitor._check_collector_heartbeat()
                    await monitor._emit_engine_stall()
            return len(restart_calls)

        n_restarts = asyncio.run(run_case())
        self.assertEqual(n_restarts, 0, "monitor must never invoke systemctl restart")

    def test_alert_counts_follow_jsonl_rows_not_repeated_checks(self) -> None:
        async def run_case() -> tuple[int, int]:
            root = Path("tmp_live_tests") / "alert_count_jsonl_rows"
            shutil.rmtree(root, ignore_errors=True)
            monitor = HealthMonitor(profile={}, live_root=root)
            with patch("scripts.live.health_monitor._is_preflight_window", return_value=False):
                await monitor._alert("critical", "engine", "engine_stall", "first")
                await monitor._alert("critical", "engine", "engine_stall", "repeat")
            alert_path = root / "alerts" / f"{monitor._date_str}_alerts.jsonl"
            rows = len([line for line in alert_path.read_text().splitlines() if line.strip()])
            return rows, monitor._alert_counts["critical"]

        rows, count = asyncio.run(run_case())
        self.assertEqual(rows, 1)
        self.assertEqual(count, 1)


# ── WebSocket alert payload bounded ───────────────────────────────────────────


class WSAlertBudgetTests(unittest.TestCase):
    def test_ws_alert_tail_is_bounded(self) -> None:
        from scripts.live.api.routes.live import _WS_ALERT_TAIL
        # 5/15 audit: 334 KB frames with full history. 20 rows ≈ a few KB.
        self.assertGreater(_WS_ALERT_TAIL, 0)
        self.assertLessEqual(_WS_ALERT_TAIL, 50)


class SpotHistoryTests(unittest.TestCase):
    def _bars(self, session_date: date, complete: bool = True) -> list[dict]:
        from options_backtest.spot_history import expected_session_index
        idx = expected_session_index(session_date)
        if not complete:
            idx = idx[:10]
        bars = []
        for i, ts in enumerate(idx):
            epoch = int((ts - pd.Timestamp("1970-01-01")) / pd.Timedelta("1s"))
            bars.append({"time": epoch, "open": 100 + i, "high": 101 + i, "low": 99 + i, "close": 100.5 + i})
        return bars

    def test_live_spot_session_appends_only_when_complete(self) -> None:
        from options_backtest.spot_history import append_live_session_if_complete
        root = Path("tmp_live_tests") / "spot_history_complete"
        shutil.rmtree(root, ignore_errors=True)
        spot_dir = root / "processed" / "spot"
        spot_dir.mkdir(parents=True, exist_ok=True)
        (spot_dir / "nifty50_1min_CANONICAL.csv").write_text("datetime,open,high,low,close,volume\n")

        ok, coverage, written = append_live_session_if_complete(
            data_root=root,
            session_date=date(2026, 5, 15),
            live_bars_by_symbol={"NIFTY": self._bars(date(2026, 5, 15), complete=True)},
            symbols=["NIFTY"],
        )
        self.assertTrue(ok)
        self.assertEqual(len(written), 1)
        self.assertTrue(all(c.complete for c in coverage))
        out = pd.read_csv(spot_dir / "nifty50_1min_CANONICAL.csv")
        self.assertEqual(len(out), 375)
        self.assertEqual(out["datetime"].iloc[0], "2026-05-15 09:15:00")
        self.assertEqual(out["datetime"].iloc[-1], "2026-05-15 15:29:00")

    def test_live_spot_session_rejects_incomplete_capture(self) -> None:
        from options_backtest.spot_history import append_live_session_if_complete
        root = Path("tmp_live_tests") / "spot_history_incomplete"
        shutil.rmtree(root, ignore_errors=True)
        spot_dir = root / "processed" / "spot"
        spot_dir.mkdir(parents=True, exist_ok=True)
        (spot_dir / "nifty50_1min_CANONICAL.csv").write_text("datetime,open,high,low,close,volume\n")

        ok, coverage, written = append_live_session_if_complete(
            data_root=root,
            session_date=date(2026, 5, 15),
            live_bars_by_symbol={"NIFTY": self._bars(date(2026, 5, 15), complete=False)},
            symbols=["NIFTY"],
        )
        self.assertFalse(ok)
        self.assertEqual(written, [])
        self.assertEqual(coverage[0].rows, 10)
        out = pd.read_csv(spot_dir / "nifty50_1min_CANONICAL.csv")
        self.assertEqual(len(out), 0)

    def test_paper_engine_writes_one_spot_coverage_alert_when_incomplete(self) -> None:
        root = Path("tmp_live_tests") / "spot_history_alert"
        shutil.rmtree(root, ignore_errors=True)
        engine = PaperTradingEngine(
            profile={"profile_name": "test", "symbols": {}, "vix": {}},
            session_date=date(2026, 5, 15),
            depth_cache=DepthCache(),
            access_token="token",
            client_id="client",
            live_root=root,
        )
        engine._spot_bars["NIFTY"] = self._bars(date(2026, 5, 15), complete=False)
        engine._finalize_spot_history()

        alert_path = root / "alerts" / "20260515_alerts.jsonl"
        rows = [json.loads(line) for line in alert_path.read_text().splitlines()]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["component"], "spot_data")
        self.assertEqual(rows[0]["reason"], "spot_coverage_incomplete")


class WriterThreadTests(unittest.TestCase):
    def test_writer_thread_writes_append_only_parquet(self) -> None:
        from scripts.live.collect_order_book import _WriterThread, _build_depth_row, _instrument_key
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as td:
            live_root = Path(td)
            writer = _WriterThread("20260519", live_root)
            meta = {"symbol": "NIFTY", "expiry": "2026-05-19", "strike": 23650, "option_type": "CE"}
            ts = pd.Timestamp("2026-05-19 09:30:00", tz="Asia/Kolkata")
            row = _build_depth_row(
                [DepthLevel(price=100.0, quantity=10, orders=1)],
                [DepthLevel(price=101.0, quantity=20, orders=2)],
                meta, ts,
            )
            writer.enqueue_norm(_instrument_key(meta), row)
            time.sleep(0.1)
            writer.stop()

            norm_dir = live_root / "order_book" / "20260519"
            files = list(norm_dir.glob("NIFTY_2026-05-19_23650_CE_*.parquet"))
            self.assertTrue(len(files) > 0, "Expected at least one minute-partitioned parquet file")
            df = pd.read_parquet(files[0])
            self.assertIn("mid", df.columns)
            self.assertGreaterEqual(len(df), 1)

    def test_writer_thread_handles_mixed_iso_timestamp_precision(self) -> None:
        from scripts.live.collect_order_book import _WriterThread, _instrument_key
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            live_root = Path(td)
            writer = _WriterThread("20260520", live_root)
            meta = {"symbol": "FINNIFTY", "expiry": "2026-05-26", "strike": 27000, "option_type": "PE"}
            ikey = _instrument_key(meta)
            base = {
                "best_bid": 100.0,
                "best_ask": 101.0,
                "mid": 100.5,
                "spread_pct": 0.01,
                "total_bid_qty": 100,
                "total_ask_qty": 100,
            }
            writer.enqueue_norm(ikey, {"timestamp": "2026-05-20T09:20:43.123456+05:30", **base})
            writer.enqueue_norm(ikey, {"timestamp": "2026-05-20T09:20:43+05:30", **base})
            writer.stop()

            one_min_dir = live_root / "order_book_1min" / "20260520"
            files = list(one_min_dir.glob("FINNIFTY_2026-05-26_27000_PE_*.parquet"))
            self.assertTrue(files, "Expected mixed ISO timestamps to produce 1-minute parquet")
            self.assertIsNone(writer.status()["error"])

    def test_writer_thread_raw_flush(self) -> None:
        from scripts.live.collect_order_book import _WriterThread
        import tempfile
        import time

        with tempfile.TemporaryDirectory() as td:
            live_root = Path(td)
            writer = _WriterThread("20260519", live_root)
            writer.enqueue_raw(b"test_packet")
            time.sleep(0.1)
            writer.stop()

            raw_dir = live_root / "raw_depth_packets" / "20260519"
            files = list(raw_dir.glob("depth_*.bin"))
            self.assertTrue(len(files) > 0, "Expected at least one raw bin file")
            self.assertEqual(files[0].read_bytes(), b"test_packet")


class EntryFillQualityTests(unittest.TestCase):
    def _engine(self, cache: DepthCache) -> PaperTradingEngine:
        return PaperTradingEngine(
            profile={"profile_name": "test", "symbols": {}, "vix": {}},
            session_date=date(2026, 5, 20),
            depth_cache=cache,
            access_token="token",
            client_id="client",
            live_root=Path("tmp_live_tests") / "entry_fill_quality",
        )

    def _contract(self):
        from options_backtest.schemas import Contract, OptionType
        return Contract(expiry=date(2026, 5, 26), strike=27000, option_type=OptionType.PUT, ticker="TEST")

    def test_entry_fill_rejects_stale_depth_even_when_depth_exists(self) -> None:
        from options_backtest.broker_sim import ChargesConfig, FillModel
        from options_backtest.schemas import Side

        cache = DepthCache()
        stale = pd.Timestamp.now(tz="Asia/Kolkata") - pd.Timedelta(seconds=8)
        fresh = pd.Timestamp.now(tz="Asia/Kolkata")
        cache.update_bid_packet("1", stale, [DepthLevel(price=100.0, quantity=100, orders=1)])
        cache.update_ask_packet("1", fresh, [DepthLevel(price=101.0, quantity=100, orders=1)])
        engine = self._engine(cache)

        fill = engine._get_fill(
            sid="1",
            side=Side.SELL,
            quantity=60,
            depth_source="dhan_20depth",
            symbol="FINNIFTY",
            role="short_put",
            contract=self._contract(),
            fill_model=FillModel(charges=ChargesConfig.for_date(date(2026, 5, 20))),
            now_ts=pd.Timestamp.now(tz="Asia/Kolkata"),
            now_dt=datetime.now(tz=ZoneInfo("Asia/Kolkata")),
            strict_entry=True,
            max_quote_age_ms=5000,
            max_spread_pct=20.0,
        )

        self.assertIsNone(fill)

    def test_entry_fill_rejects_wide_depth_spread(self) -> None:
        from options_backtest.broker_sim import ChargesConfig, FillModel
        from options_backtest.schemas import Side

        cache = DepthCache()
        now = pd.Timestamp.now(tz="Asia/Kolkata")
        cache.update_bid_packet("1", now, [DepthLevel(price=10.0, quantity=100, orders=1)])
        cache.update_ask_packet("1", now, [DepthLevel(price=40.0, quantity=100, orders=1)])
        engine = self._engine(cache)

        fill = engine._get_fill(
            sid="1",
            side=Side.SELL,
            quantity=60,
            depth_source="dhan_20depth",
            symbol="FINNIFTY",
            role="short_put",
            contract=self._contract(),
            fill_model=FillModel(charges=ChargesConfig.for_date(date(2026, 5, 20))),
            now_ts=pd.Timestamp.now(tz="Asia/Kolkata"),
            now_dt=datetime.now(tz=ZoneInfo("Asia/Kolkata")),
            strict_entry=True,
            max_quote_age_ms=5000,
            max_spread_pct=20.0,
        )

        self.assertIsNone(fill)

    def test_positive_credit_gate_rejects_debit_iron_condor_entry(self) -> None:
        engine = self._engine(DepthCache())
        self.assertFalse(engine._entry_credit_is_acceptable(-12942.0, require_positive_credit=True))
        self.assertTrue(engine._entry_credit_is_acceptable(12603.5, require_positive_credit=True))


class CollectorStartupTests(unittest.TestCase):
    def test_discover_symbol_with_retry_returns_none_on_failure(self) -> None:
        from scripts.live.collect_order_book import _discover_symbol_with_retry
        import asyncio

        class FailingResolver:
            def fetch_expiry_list(self, *args, **kwargs):
                return [date(2026, 5, 19)]
            def refresh_option_chain(self, *args, **kwargs):
                raise RuntimeError("simulated failure")
            def security_ids_around_chain_atm(self, *args, **kwargs):
                return []
            @property
            def _chain_lock(self):
                import threading
                return threading.Lock()
            @property
            def _chain(self):
                return {}

        resolver = FailingResolver()
        result = asyncio.run(_discover_symbol_with_retry(
            "NIFTY", resolver, date(2026, 5, 19), 13, "IDX_I", atm_offset_range=20, max_retries=2,
        ))
        self.assertIsNone(result)


class ProcessExitTests(unittest.TestCase):
    def test_main_exits_zero_on_dry_run(self) -> None:
        from scripts.live.run_paper_trading import main
        import sys

        with patch.object(sys, "argv", ["run_paper_trading.py", "--dry-run"]):
            with self.assertRaises(SystemExit) as cm:
                main()
            self.assertEqual(cm.exception.code, 0)


class PositionMarkTests(unittest.TestCase):
    def test_write_position_marks_with_no_positions(self) -> None:
        import tempfile
        from options_backtest.paper_engine import PaperTradingEngine

        with tempfile.TemporaryDirectory() as td:
            live_root = Path(td)
            engine = PaperTradingEngine(
                profile={"profile_name": "test", "symbols": {}, "vix": {}},
                session_date=date(2026, 5, 19),
                depth_cache=DepthCache(),
                access_token="token",
                client_id="client",
                live_root=live_root,
            )
            engine._write_position_marks()
            marks_path = live_root / "snapshots" / "latest_position_marks.json"
            self.assertFalse(marks_path.exists(), "No marks file when no open positions")

    def test_write_equity_tick_with_no_positions(self) -> None:
        import tempfile
        from options_backtest.paper_engine import PaperTradingEngine

        with tempfile.TemporaryDirectory() as td:
            live_root = Path(td)
            engine = PaperTradingEngine(
                profile={"profile_name": "test", "symbols": {}, "vix": {}},
                session_date=date(2026, 5, 19),
                depth_cache=DepthCache(),
                access_token="token",
                client_id="client",
                live_root=live_root,
            )
            engine._write_equity_tick()
            tick_path = live_root / "snapshots" / "latest_equity_tick.json"
            self.assertTrue(tick_path.exists())
            data = json.loads(tick_path.read_text())
            self.assertEqual(data["open_positions"], 0)
            self.assertEqual(data["realised_net_pnl"], 0.0)


class OptionChainMetaTests(unittest.TestCase):
    def test_get_option_chain_returns_dict_with_metadata(self) -> None:
        import tempfile
        from options_backtest.dashboard_bridge import DashboardBridge

        with tempfile.TemporaryDirectory() as td:
            live_root = Path(td)
            bridge = DashboardBridge(live_root)
            result = bridge.get_option_chain("NIFTY")
            self.assertIsInstance(result, dict)
            self.assertIn("symbol", result)
            self.assertIn("rows", result)
            self.assertIn("depth_status", result)
            self.assertIn("strike_step", result)
            self.assertIsInstance(result["rows"], list)


if __name__ == "__main__":
    unittest.main()

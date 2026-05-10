from __future__ import annotations

import asyncio
import json
import base64
import struct
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pandas as pd

from options_backtest.depth_cache import DepthCache, DepthLevel
from options_backtest.live_resolver import LiveDhanContractResolver
from options_backtest.paper_engine import _FEED_URL
from scripts.live.collect_order_book import _depth_collection_settings, _iter_packets, _parse_packet, _write_depth_cache_snapshot
from scripts.live.health_monitor import HealthMonitor, _jwt_expiry, _parse_timesync_offset
from scripts.live.paper_json_to_ledger import paper_trades_to_ledger, write_paper_reports


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


if __name__ == "__main__":
    unittest.main()

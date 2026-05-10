from __future__ import annotations

import json
import struct
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from options_backtest.depth_cache import DepthCache, DepthLevel
from options_backtest.live_resolver import LiveDhanContractResolver
from options_backtest.paper_engine import _FEED_URL
from options_backtest.schemas import OptionType
from scripts.live.collect_order_book import _depth_collection_settings, _iter_packets, _parse_packet
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


if __name__ == "__main__":
    unittest.main()

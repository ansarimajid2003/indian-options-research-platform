from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from options_backtest.dashboard_bridge import DashboardBridge


class AccountDashboardBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.account_dir = self.root / "account"
        self.account_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_state(self) -> dict:
        state = {
            "latest_row": {
                "session_date": "2026-06-01",
                "trades": 4,
                "net_pnl": 1234.5,
                "closing_balance": 1001234.5,
                "drawdown_pct": -0.5,
                "peak_buying_power_pct": 18.2,
                "margin_breach": False,
            },
            "all_time_net_pnl": 1234.5,
            "current_balance": 1001234.5,
            "max_drawdown_pct": -0.5,
            "total_sessions": 1,
            "total_trades": 4,
            "margin_source": "calibrated_formula",
        }
        (self.account_dir / "latest_account_state.json").write_text(
            json.dumps(state), encoding="utf-8"
        )
        return state

    def _write_ledger(self) -> list[dict]:
        rows = [
            {"session_date": "2026-05-30", "trades": 0, "net_pnl": 0.0, "closing_balance": 1000000.0},
            {"session_date": "2026-06-01", "trades": 4, "net_pnl": 1234.5, "closing_balance": 1001234.5},
        ]
        path = self.account_dir / "account_ledger.jsonl"
        path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
        return rows

    def test_get_account_state_parses_file(self) -> None:
        expected = self._write_state()
        bridge = DashboardBridge(self.root)
        result = bridge.get_account_state()
        self.assertEqual(result["current_balance"], expected["current_balance"])
        self.assertEqual(result["total_trades"], 4)
        self.assertEqual(result["margin_source"], "calibrated_formula")
        self.assertEqual(result["latest_row"]["session_date"], "2026-06-01")

    def test_get_account_ledger_parses_jsonl(self) -> None:
        expected = self._write_ledger()
        bridge = DashboardBridge(self.root)
        result = bridge.get_account_ledger()
        self.assertEqual(len(result), len(expected))
        self.assertEqual(result[0]["session_date"], "2026-05-30")
        self.assertEqual(result[-1]["closing_balance"], 1001234.5)

    def test_missing_state_returns_safe_default(self) -> None:
        bridge = DashboardBridge(self.root)
        result = bridge.get_account_state()
        self.assertIsNone(result["current_balance"])
        self.assertIsNone(result["starting_capital"])
        self.assertEqual(result["all_time_net_pnl"], 0.0)
        self.assertEqual(result["max_drawdown_pct"], 0.0)
        self.assertEqual(result["total_sessions"], 0)
        self.assertEqual(result["total_trades"], 0)
        self.assertIsNone(result["latest_row"])

    def test_missing_ledger_returns_empty_list(self) -> None:
        bridge = DashboardBridge(self.root)
        self.assertEqual(bridge.get_account_ledger(), [])


class ClosedTradesSinceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.root = Path(tempfile.mkdtemp())
        self.trades_dir = self.root / "paper_trades"
        self.trades_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    def _write_session(self, date_str: str, n: int) -> None:
        trades = []
        for i in range(n):
            trades.append({
                "symbol": "NIFTY",
                "expiry": "2026-06-04",
                "entry_time": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T09:20:00",
                "exit_time": f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:]}T15:20:0{i}",
                "net_pnl": 100.0,
            })
        (self.trades_dir / f"{date_str}.json").write_text(
            json.dumps(trades), encoding="utf-8"
        )

    def test_excludes_sessions_before_start(self) -> None:
        from datetime import date as _date

        self._write_session("20260530", 2)  # pre-canonical, must be dropped
        self._write_session("20260601", 3)
        self._write_session("20260602", 1)
        bridge = DashboardBridge(self.root)
        result = bridge.get_closed_trades_since(_date(2026, 6, 1))
        self.assertEqual(len(result), 4)  # 3 + 1, NOT the 2 from May 30
        dates = {t["session_date"] for t in result}
        self.assertNotIn("2026-05-30", dates)
        self.assertEqual(dates, {"2026-06-01", "2026-06-02"})

    def test_sorted_newest_exit_first(self) -> None:
        from datetime import date as _date

        self._write_session("20260601", 2)
        self._write_session("20260602", 2)
        bridge = DashboardBridge(self.root)
        result = bridge.get_closed_trades_since(_date(2026, 6, 1))
        exits = [t["exit_time"] for t in result]
        self.assertEqual(exits, sorted(exits, reverse=True))

    def test_per_trade_carries_session_date(self) -> None:
        from datetime import date as _date

        self._write_session("20260601", 1)
        bridge = DashboardBridge(self.root)
        result = bridge.get_closed_trades_since(_date(2026, 6, 1))
        self.assertEqual(result[0]["session_date"], "2026-06-01")


if __name__ == "__main__":
    unittest.main()

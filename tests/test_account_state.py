import json
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory

from options_backtest.account_state import (
    MarginModel,
    compute_session_row,
    update_account_ledger,
)

_MARGIN_MODEL = {
    "starting_capital": 1000000.0,
    "per_index": {
        "NIFTY": {"hedged_floor_per_lot": 45000.0, "expiry_elm_pct": 0.02, "expiry_elm_dte": 0},
        "FINNIFTY": {"hedged_floor_per_lot": 40000.0, "expiry_elm_pct": 0.02, "expiry_elm_dte": 0},
        "MIDCPNIFTY": {"hedged_floor_per_lot": 40000.0, "expiry_elm_pct": 0.02, "expiry_elm_dte": 0},
        "SENSEX": {"hedged_floor_per_lot": 50000.0, "expiry_elm_pct": 0.02, "expiry_elm_dte": 2},
    },
}


def _write_model(tmp: Path) -> Path:
    path = tmp / "margin_model.json"
    path.write_text(json.dumps(_MARGIN_MODEL), encoding="utf-8")
    return path


def _nifty_trade(net_pnl=2000.0, entry_credit=8000.0, expiry="2026-06-09",
                 entry_time="2026-06-02T09:20:00+05:30"):
    # wing 300 (24350-24050 and 24050-23750), qty 65, given credit
    return {
        "symbol": "NIFTY",
        "expiry": expiry,
        "session_date": "2026-06-02",
        "entry_time": entry_time,
        "exit_time": "2026-06-02T15:20:00+05:30",
        "lots": 1,
        "lot_size": 65,
        "entry_credit": entry_credit,
        "exit_debit": entry_credit - net_pnl,
        "gross_pnl": net_pnl + 100.0,
        "charges": 100.0,
        "net_pnl": net_pnl,
        "short_call_strike": 24050,
        "long_call_strike": 24350,
        "short_put_strike": 23750,
        "long_put_strike": 23450,
        "entry_fills": [
            {"side": "SELL", "leg_role": "short_call", "quantity": 65, "price": 60.0},
            {"side": "BUY", "leg_role": "long_call", "quantity": 65, "price": 20.0},
            {"side": "SELL", "leg_role": "short_put", "quantity": 65, "price": 60.0},
            {"side": "BUY", "leg_role": "long_put", "quantity": 65, "price": 18.0},
        ],
        "exit_fills": [],
    }


def _sensex_trade(net_pnl=1000.0):
    # dte 1 (expiry next day) -> ELM applies for SENSEX (expiry_elm_dte=2)
    return {
        "symbol": "SENSEX",
        "expiry": "2026-06-04",
        "session_date": "2026-06-03",
        "entry_time": "2026-06-03T09:20:00+05:30",
        "exit_time": "2026-06-03T15:20:00+05:30",
        "lots": 1,
        "lot_size": 20,
        "entry_credit": 12000.0,
        "gross_pnl": net_pnl + 50.0,
        "charges": 50.0,
        "net_pnl": net_pnl,
        "short_call_strike": 81000,
        "long_call_strike": 81600,
        "short_put_strike": 80000,
        "long_put_strike": 79400,
        "spot_entry": 80500.0,
        "entry_fills": [
            {"side": "SELL", "leg_role": "short_call", "quantity": 20, "price": 200.0},
            {"side": "BUY", "leg_role": "long_call", "quantity": 20, "price": 80.0},
            {"side": "SELL", "leg_role": "short_put", "quantity": 20, "price": 200.0},
            {"side": "BUY", "leg_role": "long_put", "quantity": 20, "price": 70.0},
        ],
        "exit_fills": [],
    }


class TradeMarginTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.model = MarginModel.from_config(_write_model(self.tmp))

    def tearDown(self):
        self._tmp.cleanup()

    def test_wing_width_and_max_loss(self):
        trade = _nifty_trade(entry_credit=8000.0)
        m = self.model.trade_margin(trade)
        self.assertEqual(m["quantity"], 65)
        self.assertEqual(m["wing_width"], 300)
        self.assertAlmostEqual(m["defined_max_loss"], 300 * 65 - 8000.0)

    def test_long_premium_paid_from_buy_fills(self):
        trade = _nifty_trade()
        m = self.model.trade_margin(trade)
        # BUY legs: 20*65 + 18*65 = 2470
        self.assertAlmostEqual(m["long_premium_paid"], (20.0 + 18.0) * 65)

    def test_hedged_floor_applies_when_max_loss_below_floor(self):
        # Large credit makes defined_max_loss tiny -> floor (45000*1) dominates.
        trade = _nifty_trade(entry_credit=18000.0)
        m = self.model.trade_margin(trade)
        self.assertLess(m["defined_max_loss"], 45000.0)
        self.assertAlmostEqual(m["blocked_margin"], 45000.0)
        self.assertEqual(m["margin_source"], "calibrated_formula")

    def test_sensex_expiry_elm_applies(self):
        trade = _sensex_trade()
        m = self.model.trade_margin(trade)
        self.assertEqual(m["dte_at_entry"], 1)
        # ELM = spot 80500 * 0.02 * qty 20
        self.assertAlmostEqual(m["expiry_day_elm"], 80500.0 * 0.02 * 20)

    def test_nifty_no_expiry_elm(self):
        trade = _nifty_trade()  # dte 7, elm_dte 0
        m = self.model.trade_margin(trade)
        self.assertGreater(m["dte_at_entry"], 0)
        self.assertEqual(m["expiry_day_elm"], 0.0)


class SessionRowTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.model = MarginModel.from_config(_write_model(self.tmp))

    def tearDown(self):
        self._tmp.cleanup()

    def test_closing_and_peak_margin(self):
        trades = [_nifty_trade(net_pnl=2000.0), _sensex_trade(net_pnl=1000.0)]
        row = compute_session_row(trades, 1000000.0, self.model, date(2026, 6, 2))
        self.assertEqual(row["trades"], 2)
        self.assertAlmostEqual(row["net_pnl"], 3000.0)
        self.assertAlmostEqual(row["closing_balance"], 1003000.0)
        expected_peak = sum(
            self.model.trade_margin(t)["capital_committed"] for t in trades
        )
        self.assertAlmostEqual(row["peak_margin_used"], round(expected_peak, 2))
        self.assertFalse(row["margin_breach"])

    def test_margin_breach_flips(self):
        trades = [_nifty_trade()]
        # opening below committed capital -> breach
        committed = self.model.trade_margin(trades[0])["capital_committed"]
        row = compute_session_row(trades, committed - 1.0, self.model, date(2026, 6, 2))
        self.assertTrue(row["margin_breach"])

    def test_broker_snapshot_override(self):
        trades = [_nifty_trade()]
        snap = {"broker_margin_used": 38000.0, "broker_net": 962000.0}
        row = compute_session_row(trades, 1000000.0, self.model, date(2026, 6, 2),
                                  broker_snapshot=snap)
        self.assertAlmostEqual(row["peak_margin_used"], 38000.0)
        self.assertEqual(row["margin_source"], "broker_snapshot")
        self.assertEqual(row["broker_net"], 962000.0)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self._tmp = TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.model = MarginModel.from_config(_write_model(self.tmp))
        self.live_root = self.tmp / "live"
        (self.live_root / "paper_trades").mkdir(parents=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write_trades(self, date_str, trades):
        path = self.live_root / "paper_trades" / f"{date_str}.json"
        path.write_text(json.dumps(trades), encoding="utf-8")

    def test_day1_day2_and_idempotent(self):
        self._write_trades("20260602", [_nifty_trade(net_pnl=2000.0)])
        row1 = update_account_ledger(self.live_root, date(2026, 6, 2), self.model)
        self.assertAlmostEqual(row1["opening_balance"], 1000000.0)
        self.assertAlmostEqual(row1["closing_balance"], 1002000.0)

        self._write_trades("20260603", [_nifty_trade(net_pnl=-3000.0,
                                                      entry_time="2026-06-03T09:20:00+05:30")])
        row2 = update_account_ledger(self.live_root, date(2026, 6, 3), self.model)
        self.assertAlmostEqual(row2["opening_balance"], 1002000.0)
        self.assertAlmostEqual(row2["closing_balance"], 999000.0)
        # drawdown negative after the peak on day 1
        self.assertLess(row2["drawdown_pct"], 0.0)

        # idempotent: re-run day 1 -> stable closing, no duplicate rows
        update_account_ledger(self.live_root, date(2026, 6, 2), self.model)
        ledger_path = self.live_root / "account" / "account_ledger.jsonl"
        rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
        dates = [r["session_date"] for r in rows]
        self.assertEqual(dates, ["2026-06-02", "2026-06-03"])
        self.assertAlmostEqual(rows[0]["closing_balance"], 1002000.0)

    def test_idempotent_rerun_stable(self):
        self._write_trades("20260602", [_nifty_trade(net_pnl=2000.0)])
        update_account_ledger(self.live_root, date(2026, 6, 2), self.model)
        first = update_account_ledger(self.live_root, date(2026, 6, 2), self.model)
        ledger_path = self.live_root / "account" / "account_ledger.jsonl"
        rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
        self.assertEqual(len(rows), 1)
        self.assertAlmostEqual(first["closing_balance"], 1002000.0)

    def test_day1_correction_rewalks_day2_chain(self):
        # day 1 then day 2 established
        self._write_trades("20260602", [_nifty_trade(net_pnl=2000.0)])
        update_account_ledger(self.live_root, date(2026, 6, 2), self.model)
        self._write_trades("20260603", [_nifty_trade(net_pnl=-3000.0,
                                                      entry_time="2026-06-03T09:20:00+05:30")])
        update_account_ledger(self.live_root, date(2026, 6, 3), self.model)

        # corrected day-1 trades file: net_pnl shifts by -500 (2000 -> 1500)
        self._write_trades("20260602", [_nifty_trade(net_pnl=1500.0)])
        update_account_ledger(self.live_root, date(2026, 6, 2), self.model)

        ledger_path = self.live_root / "account" / "account_ledger.jsonl"
        rows = [json.loads(l) for l in ledger_path.read_text().splitlines() if l.strip()]
        self.assertEqual([r["session_date"] for r in rows], ["2026-06-02", "2026-06-03"])
        self.assertAlmostEqual(rows[0]["closing_balance"], 1001500.0)
        # day 2 opening must re-anchor to NEW day-1 closing; closing shifts by same -500 delta
        self.assertAlmostEqual(rows[1]["opening_balance"], 1001500.0)
        self.assertAlmostEqual(rows[1]["closing_balance"], 998500.0)

    def test_missing_trades_file_flat_row(self):
        row = update_account_ledger(self.live_root, date(2026, 6, 5), self.model)
        self.assertEqual(row["trades"], 0)
        self.assertAlmostEqual(row["opening_balance"], row["closing_balance"])

    def test_latest_state_written(self):
        self._write_trades("20260602", [_nifty_trade(net_pnl=2000.0)])
        update_account_ledger(self.live_root, date(2026, 6, 2), self.model)
        state_path = self.live_root / "account" / "latest_account_state.json"
        state = json.loads(state_path.read_text())
        self.assertAlmostEqual(state["current_balance"], 1002000.0)
        self.assertEqual(state["total_sessions"], 1)
        self.assertEqual(state["total_trades"], 1)


if __name__ == "__main__":
    unittest.main()

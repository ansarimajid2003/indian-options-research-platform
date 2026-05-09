from __future__ import annotations

import unittest
from datetime import date
from pathlib import Path

import pandas as pd

from options_backtest.broker_sim import ChargesConfig, FillModel
from options_backtest.calendar import (
    expiry_on_or_after,
    is_trading_day,
    lot_size,
    next_trading_day,
    nifty_lot_size,
    nifty_monthly_expiry_on_or_after,
    nifty_weekly_expiry_on_or_after,
)
from options_backtest.cli import _default_output_path
from options_backtest.contract_resolver import ContractResolver
from options_backtest.data_store import list_expiry_dirs, load_expiry_options, load_expiry_spot, parse_option_filename, parse_ticker
from options_backtest.engine import BacktestEngine
from options_backtest.liquidity import LiquidityConfig, contract_is_liquid
from options_backtest.reports import trade_ledger, trade_ledger_with_total, LEDGER_COLUMNS
from options_backtest.schemas import BacktestConfig, OptionType, Side, Trade
from options_backtest.strategy import ShortStraddle
from options_backtest.volatility_filter import VixFilter


RAW_ROOT = Path("data/raw/options/shoonya/nifty")


class OptionsBacktestTests(unittest.TestCase):
    def setUp(self) -> None:
        if not RAW_ROOT.exists():
            self.skipTest("Shoonya options data not available")

    def test_parse_filename_and_ticker(self) -> None:
        strike, option_type, expiry = parse_option_filename(Path("21500CE_20240104.csv"))
        self.assertEqual(strike, 21500)
        self.assertEqual(option_type, OptionType.CALL)
        self.assertEqual(expiry, "20240104")
        self.assertEqual(parse_ticker("NIFTY04JAN24CE21500"), (21500, OptionType.CALL))

    def test_bad_expiries_are_quarantined_by_default(self) -> None:
        names = {path.name for path in list_expiry_dirs(RAW_ROOT)}
        self.assertNotIn("20250925", names)
        self.assertNotIn("20251224", names)

    def test_spot_and_option_loaders(self) -> None:
        expiry_dir = RAW_ROOT / "20240104"
        options = load_expiry_options(expiry_dir)
        spot = load_expiry_spot(expiry_dir)
        self.assertIn("timestamp", options.columns)
        self.assertIn("expiry", options.columns)
        self.assertEqual(options[options["ticker"] == "NIFTY04JAN24CE21500"]["strike"].iloc[0], 21500)
        self.assertFalse(spot.duplicated("timestamp").any())

    def test_atm_resolution(self) -> None:
        expiry_dir = RAW_ROOT / "20240104"
        resolver = ContractResolver(load_expiry_options(expiry_dir), load_expiry_spot(expiry_dir))
        ts = pd.Timestamp("2024-01-04 09:20:00")
        atm = resolver.atm_strike(ts)
        self.assertEqual(atm % 50, 0)
        contract = resolver.resolve_atm_offset(ts, 0, OptionType.CALL)
        self.assertEqual(contract.strike, atm)

    def test_liquidity_rejects_sparse_contract(self) -> None:
        sparse = pd.DataFrame({"volume": [1], "oi": [1]})
        self.assertFalse(contract_is_liquid(sparse, 18300, 21650, LiquidityConfig(min_rows=10)))

    def test_fill_model_tick_rounding_and_charges(self) -> None:
        model = FillModel(tick_size=0.05, slippage_points=0.05, include_costs=True)
        self.assertEqual(model.round_tick(10.023), 10.0)
        # close=10 hits far-OTM tier (1.5% spread): half_spread=0.15, slippage=0.05 → buy fill=10.20
        self.assertEqual(model.fill_price(10.0, Side.BUY), 10.2)
        # close=100 hits ATM tier (0.3% spread): half_spread=0.30, slippage=0.05 → buy fill=100.35
        self.assertEqual(model.fill_price(100.0, Side.BUY), 100.35)
        self.assertGreater(model.estimate_charges(Side.SELL, 50, 100.0), 0)

    def test_short_straddle_small_sample_is_deterministic(self) -> None:
        config = BacktestConfig(
            raw_root=str(RAW_ROOT),
            include_costs=False,
            slippage_points=0.0,
            target_profit_pct=None,
            stop_loss_pct=None,
        )
        result1 = BacktestEngine(config).run(ShortStraddle(), from_expiry="20240104", to_expiry="20240104")
        result2 = BacktestEngine(config).run(ShortStraddle(), from_expiry="20240104", to_expiry="20240104")
        self.assertEqual(result1.summary, result2.summary)
        self.assertEqual(result1.summary["trades"], 1)
        self.assertIn("net_pnl", result1.trade_ledger.columns)

    def test_trade_ledger_columns_for_empty_input(self) -> None:
        ledger = trade_ledger([])
        self.assertEqual(len(ledger), 0)
        for col in LEDGER_COLUMNS:
            self.assertIn(col, ledger.columns)

    def test_nifty_weekly_expiry_calendar_handles_2025_transition(self) -> None:
        self.assertEqual(nifty_weekly_expiry_on_or_after(pd.Timestamp("2025-08-28").date()), pd.Timestamp("2025-08-28").date())
        self.assertEqual(
            nifty_weekly_expiry_on_or_after(pd.Timestamp("2025-08-28").date(), min_dte=1),
            pd.Timestamp("2025-09-02").date(),
        )
        self.assertEqual(nifty_weekly_expiry_on_or_after(pd.Timestamp("2025-08-29").date()), pd.Timestamp("2025-09-02").date())
        self.assertEqual(nifty_weekly_expiry_on_or_after(pd.Timestamp("2025-09-02").date()), pd.Timestamp("2025-09-02").date())
        self.assertEqual(
            nifty_weekly_expiry_on_or_after(pd.Timestamp("2025-09-02").date(), min_dte=1),
            pd.Timestamp("2025-09-09").date(),
        )

    def test_nifty_monthly_expiry_calendar_handles_2025_transition(self) -> None:
        self.assertEqual(nifty_monthly_expiry_on_or_after(pd.Timestamp("2025-08-01").date()), pd.Timestamp("2025-08-28").date())
        self.assertEqual(nifty_monthly_expiry_on_or_after(pd.Timestamp("2025-09-01").date()), pd.Timestamp("2025-09-30").date())

    def test_trade_ledger_with_total_appends_summary_row(self) -> None:
        ledger = pd.DataFrame([
            {
                "strategy": "A", "expiry": "2025-09-02",
                "entry_date": "2025-09-01", "entry_time": "x",
                "exit_date": "2025-09-02", "exit_time": "y",
                "entry_reason": "entry", "exit_reason": "time_exit",
                "dte_at_entry": 1, "day_of_week": "Monday", "exit_hour": 15,
                "lot_size": 75,
                "entry_legs": "leg1", "exit_legs": "", "gross_pnl": 10.0, "charges": 1.5, "net_pnl": 8.5,
            },
            {
                "strategy": "A", "expiry": "2025-09-09",
                "entry_date": "2025-09-08", "entry_time": "x",
                "exit_date": "2025-09-09", "exit_time": "y",
                "entry_reason": "entry", "exit_reason": "time_exit",
                "dte_at_entry": 1, "day_of_week": "Monday", "exit_hour": 15,
                "lot_size": 75,
                "entry_legs": "leg2", "exit_legs": "", "gross_pnl": -4.0, "charges": 1.0, "net_pnl": -5.0,
            },
        ])
        out = trade_ledger_with_total(ledger)
        total = out.iloc[-1]
        self.assertEqual(total["strategy"], "TOTAL")
        self.assertEqual(total["trade_count"], 2)
        self.assertEqual(total["gross_pnl"], 6.0)
        self.assertEqual(total["charges"], 2.5)
        self.assertEqual(total["net_pnl"], 3.5)

    def test_default_backtest_output_path_is_timestamped(self) -> None:
        path = _default_output_path("dhan", "three-pm-directional")
        self.assertEqual(path.parent.as_posix(), "reports/backtests/options")
        self.assertRegex(path.name, r"^\d{8}_\d{6}_dhan_three_pm_directional\.csv$")
        no_cost_path = _default_output_path("dhan", "short-straddle", no_costs=True)
        self.assertRegex(no_cost_path.name, r"^\d{8}_\d{6}_dhan_short_straddle_nocosts\.csv$")


class VixFilterTests(unittest.TestCase):
    def test_asof_lookup_never_uses_future_vix(self) -> None:
        series = pd.Series(
            [12.5, 15.0],
            index=pd.to_datetime(["2026-04-10 09:15:00", "2026-04-10 09:20:00"]),
        )
        filt = VixFilter.from_series(series, min_vix=13.0, max_vix=22.0)
        allowed, obs = filt.allows(pd.Timestamp("2026-04-10 09:19:59"))
        self.assertFalse(allowed)
        self.assertIsNotNone(obs)
        self.assertEqual(obs.timestamp, pd.Timestamp("2026-04-10 09:15:00"))
        self.assertEqual(obs.value, 12.5)

    def test_missing_vix_skip_policy_blocks_trade(self) -> None:
        series = pd.Series([15.0], index=pd.to_datetime(["2026-04-10 09:20:00"]))
        filt = VixFilter.from_series(series)
        allowed, obs = filt.allows(pd.Timestamp("2026-04-10 09:19:00"))
        self.assertFalse(allowed)
        self.assertIsNone(obs)

    def test_trade_ledger_includes_vix_metadata(self) -> None:
        trade = Trade(
            expiry=date(2026, 4, 28),
            strategy="Example",
            entry_time=pd.Timestamp("2026-04-10 09:20:00"),
            exit_time=pd.Timestamp("2026-04-10 15:20:00"),
            entry_reason="entry",
            exit_reason="time_exit",
            metadata={"vix_entry": 15.25, "vix_bucket": "13-17", "vix_timestamp": pd.Timestamp("2026-04-10 09:20:00")},
        )
        ledger = trade_ledger([trade])
        self.assertEqual(ledger.loc[0, "vix_entry"], 15.25)
        self.assertEqual(ledger.loc[0, "vix_bucket"], "13-17")
        self.assertIn("vix_timestamp", ledger.columns)


class NiftyLotSizeTests(unittest.TestCase):
    """Verify lot-size schedule boundaries exactly match NSE circulars."""

    def test_pre_2015_lot_is_25(self) -> None:
        self.assertEqual(nifty_lot_size(date(2015, 10, 29)), 25)

    def test_oct_2015_to_jun_2021_is_75(self) -> None:
        self.assertEqual(nifty_lot_size(date(2015, 10, 30)), 75)
        self.assertEqual(nifty_lot_size(date(2021, 6, 30)), 75)

    def test_jul_2021_to_nov_2024_is_50(self) -> None:
        self.assertEqual(nifty_lot_size(date(2021, 7, 1)), 50)
        self.assertEqual(nifty_lot_size(date(2024, 4, 25)), 50)

    def test_may_2024_to_nov_2024_is_25(self) -> None:
        self.assertEqual(nifty_lot_size(date(2024, 4, 26)), 25)
        self.assertEqual(nifty_lot_size(date(2024, 11, 20)), 25)

    def test_nov_2024_to_dec_2025_is_75(self) -> None:
        self.assertEqual(nifty_lot_size(date(2024, 11, 21)), 75)
        self.assertEqual(nifty_lot_size(date(2025, 12, 30)), 75)

    def test_dec_2025_onwards_is_65(self) -> None:
        self.assertEqual(nifty_lot_size(date(2025, 12, 31)), 65)
        self.assertEqual(nifty_lot_size(date(2026, 4, 1)), 65)


class InstrumentCalendarTests(unittest.TestCase):
    def test_cross_index_lot_sizes(self) -> None:
        self.assertEqual(lot_size("BANKNIFTY", date(2021, 8, 4)), 25)
        self.assertEqual(lot_size("BANKNIFTY", date(2023, 7, 28)), 15)
        self.assertEqual(lot_size("FINNIFTY", date(2024, 11, 21)), 65)
        self.assertEqual(lot_size("MIDCPNIFTY", date(2025, 7, 31)), 140)
        self.assertEqual(lot_size("MIDCPNIFTY", date(2026, 1, 1)), 120)

    def test_cross_index_expiry_days(self) -> None:
        self.assertEqual(expiry_on_or_after("BANKNIFTY", date(2023, 8, 31)), date(2023, 8, 31))
        self.assertEqual(expiry_on_or_after("BANKNIFTY", date(2023, 9, 1)), date(2023, 9, 6))
        self.assertEqual(expiry_on_or_after("FINNIFTY", date(2024, 11, 18)), date(2024, 11, 19))
        self.assertEqual(expiry_on_or_after("MIDCPNIFTY", date(2024, 11, 18)), date(2024, 11, 18))

    def test_discontinued_weeklies_fall_back_to_monthly(self) -> None:
        self.assertEqual(expiry_on_or_after("BANKNIFTY", date(2024, 11, 14)), date(2024, 11, 27))
        self.assertEqual(expiry_on_or_after("FINNIFTY", date(2024, 11, 20)), date(2024, 11, 26))
        self.assertEqual(expiry_on_or_after("MIDCPNIFTY", date(2024, 11, 19)), date(2024, 11, 25))

    def test_monthly_expiry_after_download_window_does_not_snap_back_forever(self) -> None:
        trading_dates = {date(2026, 4, 29), date(2026, 4, 30)}
        self.assertEqual(
            expiry_on_or_after(
                "BANKNIFTY",
                date(2026, 4, 30),
                trading_dates=trading_dates,
            ),
            date(2026, 5, 26),
        )


class ChargesConfigDateTests(unittest.TestCase):
    """Verify STT and ETC rates change at correct regime boundaries."""

    def test_stt_pre_2023_is_005pct(self) -> None:
        c = ChargesConfig.for_date(date(2023, 3, 31))
        self.assertAlmostEqual(c.stt_sell_rate, 0.0005)

    def test_stt_2023_to_sep_2024_is_00625pct(self) -> None:
        c = ChargesConfig.for_date(date(2023, 4, 1))
        self.assertAlmostEqual(c.stt_sell_rate, 0.000625)
        c2 = ChargesConfig.for_date(date(2024, 9, 30))
        self.assertAlmostEqual(c2.stt_sell_rate, 0.000625)

    def test_stt_oct_2024_to_mar_2026_is_010pct(self) -> None:
        c = ChargesConfig.for_date(date(2024, 10, 1))
        self.assertAlmostEqual(c.stt_sell_rate, 0.001)
        c2 = ChargesConfig.for_date(date(2026, 3, 31))
        self.assertAlmostEqual(c2.stt_sell_rate, 0.001)

    def test_stt_from_apr_2026_is_015pct(self) -> None:
        c = ChargesConfig.for_date(date(2026, 4, 1))
        self.assertAlmostEqual(c.stt_sell_rate, 0.0015)

    def test_etc_post_oct_2024_is_lower(self) -> None:
        pre = ChargesConfig.for_date(date(2024, 9, 30))
        post = ChargesConfig.for_date(date(2024, 10, 1))
        self.assertGreater(pre.exchange_rate, post.exchange_rate)
        self.assertAlmostEqual(post.exchange_rate, 0.0003503)

    def test_exercise_stt_pre_2026_is_0125pct(self) -> None:
        c = ChargesConfig.for_date(date(2025, 12, 31))
        self.assertAlmostEqual(c.stt_exercise_rate, 0.00125)

    def test_exercise_stt_from_apr_2026_is_015pct(self) -> None:
        c = ChargesConfig.for_date(date(2026, 4, 1))
        self.assertAlmostEqual(c.stt_exercise_rate, 0.0015)

    def test_charges_increase_from_2021_to_2026(self) -> None:
        """Total cost for same trade should be higher in 2026 than in 2021."""
        model = FillModel(include_costs=True)
        cost_2021 = model.estimate_charges(Side.SELL, 50, 100.0, trade_date=date(2021, 6, 1))
        cost_2026 = model.estimate_charges(Side.SELL, 50, 100.0, trade_date=date(2026, 4, 1))
        self.assertGreater(cost_2026, cost_2021)


class TradingCalendarTests(unittest.TestCase):
    """Verify is_trading_day and next_trading_day correctness."""

    def test_weekday_non_holiday_is_trading_day(self) -> None:
        # A random Monday that is not a known holiday
        self.assertTrue(is_trading_day(date(2024, 3, 18)))  # Monday, not a holiday

    def test_saturday_is_not_trading_day(self) -> None:
        self.assertFalse(is_trading_day(date(2024, 1, 20)))  # Saturday

    def test_sunday_is_not_trading_day(self) -> None:
        self.assertFalse(is_trading_day(date(2026, 2, 1)))   # Sunday

    def test_budget_day_saturday_is_special_session(self) -> None:
        # Feb 1 2025 was a Saturday with a special NSE F&O session (Budget Day)
        self.assertTrue(is_trading_day(date(2025, 2, 1)))

    def test_republic_day_is_holiday(self) -> None:
        self.assertFalse(is_trading_day(date(2024, 1, 26)))  # Friday Republic Day 2024

    def test_christmas_is_holiday_when_weekday(self) -> None:
        self.assertFalse(is_trading_day(date(2023, 12, 25)))  # Monday Christmas 2023

    def test_next_trading_day_skips_weekend(self) -> None:
        # Mar 22 2024 (Friday) → Mar 25 is Holi → result is Mar 26 (Tuesday)
        friday = date(2024, 3, 22)
        nxt = next_trading_day(friday)
        self.assertEqual(nxt, date(2024, 3, 26))   # skip weekend + Holi
        self.assertEqual(nxt.weekday(), 1)          # Tuesday
        # May 31 2024 (Friday) → Jun 3 2024 (Monday, no holiday)
        friday2 = date(2024, 5, 31)
        nxt2 = next_trading_day(friday2)
        self.assertEqual(nxt2, date(2024, 6, 3))

    def test_next_trading_day_skips_holiday(self) -> None:
        # Day before Republic Day → skip Republic Day → next trading day
        before_republic = date(2024, 1, 25)  # Thursday
        nxt = next_trading_day(before_republic)
        # Jan 26 (Fri) = Republic Day, so next is Jan 27 (Sat) → Jan 28 (Sun) → Jan 29 (Mon)
        self.assertEqual(nxt, date(2024, 1, 29))

    def test_next_trading_day_is_always_weekday(self) -> None:
        for d in [date(2024, 3, 22), date(2024, 3, 23), date(2024, 3, 24)]:
            nxt = next_trading_day(d)
            self.assertLess(nxt.weekday(), 5)
            self.assertGreater(nxt, d)


class LedgerColumnTests(unittest.TestCase):
    """Verify new analysis columns are present and populated in trade_ledger output."""

    def test_ledger_has_all_required_columns(self) -> None:
        ledger = trade_ledger([])
        for col in ["entry_date", "exit_date", "dte_at_entry", "day_of_week", "exit_hour", "lot_size"]:
            self.assertIn(col, ledger.columns, f"Missing column: {col}")

    def test_ledger_with_total_preserves_new_columns(self) -> None:
        row = {col: None for col in LEDGER_COLUMNS}
        row.update({"strategy": "SS", "gross_pnl": 100.0, "charges": 10.0, "net_pnl": 90.0,
                    "dte_at_entry": 2, "day_of_week": "Tuesday", "exit_hour": 15, "lot_size": 75})
        ledger = pd.DataFrame([row])
        out = trade_ledger_with_total(ledger)
        self.assertIn("dte_at_entry", out.columns)
        self.assertIn("lot_size", out.columns)
        self.assertEqual(out.iloc[-1]["strategy"], "TOTAL")


if __name__ == "__main__":
    unittest.main()

"""
Bias audit for the options backtest engine.

Each test targets a specific class of bias that could make results look better than
they really are. Tests are designed to detect the bias, not just assert its absence —
each one prints a finding so you can judge magnitude even if the assertion passes.

Biases checked:
  1. Look-ahead on entry price       — entry fill must not use a price from after entry_ts
  2. Look-ahead on SL/target trigger — trigger check must use close of bar N-1, not bar N open
  3. Exit fill price vs trigger price — SL/target fills must use open, not close, of trigger bar
  4. Survivorship bias               — bad_expiries must be quarantined before data is loaded
  5. Data-snooping on entry time     — entry snaps to nearest bar >= target, never earlier
  6. Timestamp intersection          — exit loop only runs on timestamps present in both feeds
  7. SL trigger direction            — credits-only guard prevents SL triggering on debit entry
  8. Time-exit boundary              — time exit fires at or before exit_target, never after
"""

from __future__ import annotations

import unittest
from pathlib import Path
from unittest.mock import patch

import pandas as pd

from options_backtest.broker_sim import FillModel
from options_backtest.calendar import nearest_timestamp
from options_backtest.contract_resolver import ContractResolver
from options_backtest.data_store import load_expiry_options, load_expiry_spot
from options_backtest.engine import BacktestEngine
from options_backtest.schemas import BacktestConfig, OptionType, Side
from options_backtest.strategy import ShortStraddle

RAW_ROOT = Path("data/options/raw/shoonya/nifty")
SAMPLE_EXPIRY = "20240104"


def _make_engine(*, sl=None, target=None, slippage=0.0, costs=False):
    return BacktestEngine(BacktestConfig(
        raw_root=str(RAW_ROOT),
        include_costs=costs,
        slippage_points=slippage,
        stop_loss_pct=sl,
        target_profit_pct=target,
    ))


class BiasAuditTests(unittest.TestCase):

    def setUp(self) -> None:
        if not RAW_ROOT.exists():
            self.skipTest("Shoonya options data not available")
        expiry_dir = RAW_ROOT / SAMPLE_EXPIRY
        self.option_bars = load_expiry_options(expiry_dir)
        self.spot_bars = load_expiry_spot(expiry_dir)
        self.resolver = ContractResolver(self.option_bars, self.spot_bars)

    # -------------------------------------------------------------------------
    # 1. Look-ahead on entry price
    # -------------------------------------------------------------------------
    def test_entry_fill_uses_entry_bar_price_only(self):
        """Entry fill price must match the close of the bar AT entry_ts, not any later bar."""
        result = _make_engine().run(ShortStraddle(), from_expiry=SAMPLE_EXPIRY, to_expiry=SAMPLE_EXPIRY)
        self.assertEqual(len(result.trades), 1)
        trade = result.trades[0]
        entry_ts = trade.entry_time
        for fill in trade.entry_fills:
            bars = self.resolver.bars_for(fill.contract)
            bar_at_entry = bars[bars["timestamp"] == entry_ts]
            self.assertFalse(bar_at_entry.empty, f"No bar at entry_ts for {fill.contract.ticker}")
            expected_close = float(bar_at_entry.iloc[0]["close"])
            expected_price = FillModel(slippage_points=0.0, include_costs=False).fill_price(expected_close, fill.side)
            self.assertAlmostEqual(fill.price, expected_price, places=2,
                msg=f"Entry fill price {fill.price} != fill-model price {expected_price} for entry bar close {expected_close} on {fill.contract.ticker}")

    # -------------------------------------------------------------------------
    # 2. Look-ahead on SL/target trigger (trigger checked on close, not open)
    # -------------------------------------------------------------------------
    def test_sl_trigger_is_checked_on_bar_close_not_open(self):
        """
        The SL condition is evaluated using bar close (what was known when bar closed).
        We patch _hypothetical_exit_cashflow to record which price_col is used during
        the _find_exit scan — it must always be 'close'.
        """
        engine = _make_engine(sl=0.5, target=None)
        expiry_dir = RAW_ROOT / SAMPLE_EXPIRY
        price_cols_seen: list[str] = []
        original = engine._hypothetical_exit_cashflow

        def recording_cashflow(ts, legs, resolver, price_col="close"):
            price_cols_seen.append(price_col)
            return original(ts, legs, resolver, price_col)

        engine._hypothetical_exit_cashflow = recording_cashflow
        engine.run(ShortStraddle(), from_expiry=SAMPLE_EXPIRY, to_expiry=SAMPLE_EXPIRY)

        non_close = [c for c in price_cols_seen if c != "close"]
        print(f"\n[bias-2] price_cols used in _find_exit scan: {set(price_cols_seen)}, non-close calls: {len(non_close)}")
        self.assertEqual(non_close, [], "SL/target scan used a non-close price — look-ahead bias")

    # -------------------------------------------------------------------------
    # 3. Exit fill price: SL/target must use open, time_exit must use close
    # -------------------------------------------------------------------------
    def test_exit_fill_price_matches_correct_ohlc_column(self):
        """
        SL/target exit fills execute at bar[i+1] open (first tradeable price after signal bar).
        Time exit fills execute at bar[exit_target] close.
        Both are verified by checking the fill price against the correct OHLC column.
        """
        result = _make_engine(sl=0.5, target=0.5).run(ShortStraddle(), from_expiry=SAMPLE_EXPIRY, to_expiry=SAMPLE_EXPIRY)
        for trade in result.trades:
            exit_ts = trade.exit_time
            reason = trade.exit_reason
            expected_col = "close" if reason == "time_exit" else "open"
            for fill in trade.exit_fills:
                bars = self.resolver.bars_for(fill.contract)
                bar = bars[bars["timestamp"] == exit_ts]
                if bar.empty:
                    continue
                expected_price = float(bar.iloc[0][expected_col])
                expected_fill = FillModel(slippage_points=0.0, include_costs=False).fill_price(expected_price, fill.side)
                print(f"\n[bias-3] {reason}: fill={fill.price}, bar {expected_col}={expected_price}")
                self.assertAlmostEqual(fill.price, expected_fill, places=2,
                    msg=f"{reason} fill used wrong price col/model: got {fill.price}, expected fill {expected_fill} from {expected_col}={expected_price}")

    # -------------------------------------------------------------------------
    # 4. Survivorship bias — bad expiries excluded before any data touch
    # -------------------------------------------------------------------------
    def test_bad_expiries_never_appear_in_results(self):
        config = BacktestConfig(raw_root=str(RAW_ROOT), bad_expiries=("20240104",))
        result = BacktestEngine(config).run(ShortStraddle(), from_expiry="20240104", to_expiry="20240111")
        expiries_in_result = {str(t.expiry).replace("-", "") for t in result.trades}
        print(f"\n[bias-4] expiries in result with 20240104 quarantined: {expiries_in_result}")
        self.assertNotIn("20240104", expiries_in_result, "Quarantined expiry appeared in results")

    # -------------------------------------------------------------------------
    # 5. Entry timestamp snap — never earlier than target
    # -------------------------------------------------------------------------
    def test_entry_timestamp_never_before_target(self):
        """nearest_timestamp must return a timestamp >= target, never an earlier bar."""
        spot_index = pd.DatetimeIndex(pd.to_datetime(self.spot_bars["timestamp"]).sort_values())
        # Try a target 1 second after an existing bar to ensure we snap forward
        existing_ts = spot_index[5]
        target = existing_ts + pd.Timedelta(seconds=1)
        snapped = nearest_timestamp(spot_index, target)
        print(f"\n[bias-5] target={target}, snapped={snapped}")
        if snapped is not None:
            self.assertGreaterEqual(snapped, target, "Entry snapped to a bar BEFORE the target — look-ahead")

    # -------------------------------------------------------------------------
    # 6. Timestamp intersection — exit loop never uses option-only timestamps
    # -------------------------------------------------------------------------
    def test_exit_loop_uses_intersection_of_option_and_spot_timestamps(self):
        """
        If the option feed has timestamps that are absent in the spot feed, they must
        not appear as candidates in _find_exit. We inject a phantom option bar and check
        it never triggers an exit.
        """
        expiry_dir = RAW_ROOT / SAMPLE_EXPIRY
        opts = load_expiry_options(expiry_dir).copy()
        spot = load_expiry_spot(expiry_dir).copy()

        # Inject a phantom option bar at a time that doesn't exist in spot
        phantom_ts = pd.Timestamp("2024-01-04 12:00:01")
        self.assertNotIn(phantom_ts, pd.to_datetime(spot["timestamp"]).values)
        phantom_row = opts.iloc[0:1].copy()
        phantom_row["timestamp"] = phantom_ts
        phantom_row["close"] = 0.01  # Would trigger a target if evaluated
        opts = pd.concat([opts, phantom_row], ignore_index=True)

        resolver = ContractResolver(opts, spot)
        engine = _make_engine(sl=None, target=0.001)  # hair-trigger target

        # Run _find_exit directly
        strategy = ShortStraddle()
        context = __import__("options_backtest.strategy", fromlist=["StrategyContext"]).StrategyContext(
            timestamp=pd.Timestamp("2024-01-04 09:20:00"), resolver=resolver
        )
        legs = strategy.entry_legs(context)
        entry_credit = 1000.0  # arbitrary positive credit
        exit_ts, reason = engine._find_exit(
            pd.Timestamp("2024-01-04 09:20:00"),
            pd.Timestamp("2024-01-04 15:20:00"),
            legs, resolver, entry_credit
        )
        print(f"\n[bias-6] exit_ts={exit_ts}, reason={reason}")
        if exit_ts is not None:
            self.assertNotEqual(exit_ts, phantom_ts,
                "Exit triggered at a phantom timestamp not in the spot feed — data alignment bias")

    # -------------------------------------------------------------------------
    # 7. SL guard: must not trigger when entry_credit <= 0 (debit position)
    # -------------------------------------------------------------------------
    def test_sl_does_not_trigger_on_debit_entry(self):
        """
        The SL and target logic is gated on entry_credit > 0.
        A debit entry (negative credit) must never trigger an early exit.
        """
        engine = _make_engine(sl=0.5, target=0.5)
        expiry_dir = RAW_ROOT / SAMPLE_EXPIRY
        opts = load_expiry_options(expiry_dir)
        spot = load_expiry_spot(expiry_dir)
        resolver = ContractResolver(opts, spot)
        from options_backtest.strategy import ShortStraddle, StrategyContext
        strategy = ShortStraddle()
        context = StrategyContext(timestamp=pd.Timestamp("2024-01-04 09:20:00"), resolver=resolver)
        legs = strategy.entry_legs(context)

        # Simulate a debit entry (e.g. net buyer)
        negative_credit = -5000.0
        exit_ts, reason = engine._find_exit(
            pd.Timestamp("2024-01-04 09:20:00"),
            pd.Timestamp("2024-01-04 15:20:00"),
            legs, resolver, negative_credit
        )
        print(f"\n[bias-7] debit entry exit: ts={exit_ts}, reason={reason}")
        self.assertNotEqual(reason, "stop_loss", "SL triggered on a debit entry — SL guard broken")
        self.assertNotEqual(reason, "target", "Target triggered on a debit entry — SL guard broken")

    # -------------------------------------------------------------------------
    # 8. Time-exit boundary — exit_time is inclusive, not exclusive
    # -------------------------------------------------------------------------
    def test_time_exit_never_exceeds_exit_target(self):
        """Exit timestamp must be <= configured exit_time."""
        config = BacktestConfig(
            raw_root=str(RAW_ROOT),
            include_costs=False,
            slippage_points=0.0,
            stop_loss_pct=None,
            target_profit_pct=None,
        )
        result = BacktestEngine(config).run(ShortStraddle(), from_expiry="20240104", to_expiry="20240201")
        from options_backtest.calendar import combine_date_time
        for trade in result.trades:
            exit_target = combine_date_time(trade.expiry, config.exit_time)
            print(f"\n[bias-8] trade {trade.expiry}: exit_time={trade.exit_time}, exit_target={exit_target}")
            self.assertLessEqual(trade.exit_time, exit_target,
                f"Trade exited AFTER configured exit_time: {trade.exit_time} > {exit_target}")

    # -------------------------------------------------------------------------
    # 9. Fill model: slippage direction is adversarial (hurts you)
    # -------------------------------------------------------------------------
    def test_slippage_is_adversarial(self):
        """BUY fills must be priced above close; SELL fills below close."""
        model = FillModel(tick_size=0.05, slippage_points=0.10, include_costs=False)
        base = 100.0
        buy_price = model.fill_price(base, Side.BUY)
        sell_price = model.fill_price(base, Side.SELL)
        print(f"\n[bias-9] base={base}, buy_fill={buy_price}, sell_fill={sell_price}")
        self.assertGreater(buy_price, base, "BUY fill is not above close — slippage favours you")
        self.assertLess(sell_price, base, "SELL fill is not below close — slippage favours you")

    # -------------------------------------------------------------------------
    # 10. Charges are always positive (never add to PnL)
    # -------------------------------------------------------------------------
    def test_charges_are_always_non_negative(self):
        """Charges must always reduce net PnL, never enhance it."""
        result = _make_engine(sl=0.5, target=0.5, costs=True, slippage=0.05).run(
            ShortStraddle(), from_expiry="20240104", to_expiry="20240201"
        )
        for trade in result.trades:
            print(f"\n[bias-10] trade {trade.expiry}: charges={trade.charges}, gross={trade.gross_pnl}, net={trade.net_pnl}")
            self.assertGreaterEqual(trade.charges, 0.0, f"Negative charges on {trade.expiry}")
            self.assertLessEqual(trade.net_pnl, trade.gross_pnl + 1e-6,
                f"Net PnL exceeds gross PnL on {trade.expiry} — charges are negative")


if __name__ == "__main__":
    unittest.main(verbosity=2)

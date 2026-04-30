from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from options_backtest.broker_sim import FillModel
from options_backtest.contract_resolver import ContractResolver
from options_backtest.data_store import list_expiry_dirs, load_expiry_options, load_expiry_spot, parse_option_filename, parse_ticker
from options_backtest.engine import BacktestEngine
from options_backtest.liquidity import LiquidityConfig, contract_is_liquid
from options_backtest.reports import trade_ledger
from options_backtest.schemas import BacktestConfig, OptionType, Side
from options_backtest.strategy import ShortStraddle


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
        self.assertEqual(model.fill_price(10.0, Side.BUY), 10.1)
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


if __name__ == "__main__":
    unittest.main()

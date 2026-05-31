"""Unit tests for ``options_backtest.live_strategy``."""

from __future__ import annotations

import unittest

from options_backtest.live_strategy import (
    Leg,
    SymbolPolicy,
    Wing6IronCondorStrategy,
)
from options_backtest.schemas import OptionType


class SymbolPolicyTests(unittest.TestCase):
    def test_defaults_match_wing6_4x1(self):
        p = SymbolPolicy(symbol="NIFTY")
        self.assertEqual(p.short_offset_steps, 2)
        self.assertEqual(p.long_offset_steps, 8)
        self.assertEqual(p.min_dte, 1)
        self.assertIsNone(p.max_dte)
        self.assertTrue(p.require_positive_credit)

    def test_legs_returns_four_in_canonical_order(self):
        legs = SymbolPolicy(symbol="NIFTY").legs()
        self.assertEqual([l.role for l in legs], ["short_call", "long_call", "short_put", "long_put"])
        self.assertEqual([l.option_type for l in legs], [OptionType.CALL, OptionType.CALL, OptionType.PUT, OptionType.PUT])
        self.assertEqual([l.side for l in legs], ["SELL", "BUY", "SELL", "BUY"])
        self.assertEqual([l.offset_steps for l in legs], [+2, +8, -2, -8])


class Wing6StrategyTests(unittest.TestCase):
    def setUp(self):
        self.profile = {
            "name": "wing6_4x1_test",
            "symbols": {
                "NIFTY": {
                    "trade": True,
                    "lots": 1,
                    "short_offset_steps": 2,
                    "long_offset_steps": 8,
                    "min_dte": 1,
                    "max_dte": None,
                    "vix_threshold": 13.0,
                },
                "FINNIFTY": {
                    "trade": True,
                    "lots": 1,
                    "min_dte": 1,
                    "max_dte": 7,
                },
                "BANKNIFTY": {"trade": False},
            },
        }

    def test_from_profile_extracts_trading_symbols(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        self.assertIn("NIFTY", strat.policies)
        self.assertIn("FINNIFTY", strat.policies)
        self.assertNotIn("BANKNIFTY", strat.policies)

    def test_symbol_policy_returns_none_for_unknown(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        self.assertIsNone(strat.symbol_policy("NONEXISTENT"))

    def test_should_trade_dte_below_min_skip(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        ok, reason = strat.should_trade_symbol("NIFTY", vix=14.0, dte=0)
        self.assertFalse(ok)
        self.assertEqual(reason, "dte_below_min")

    def test_should_trade_dte_above_max_skip(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        ok, reason = strat.should_trade_symbol("FINNIFTY", vix=None, dte=15)
        self.assertFalse(ok)
        self.assertEqual(reason, "dte_above_max")

    def test_should_trade_vix_below_threshold_skip(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        ok, reason = strat.should_trade_symbol("NIFTY", vix=11.0, dte=2)
        self.assertFalse(ok)
        self.assertEqual(reason, "vix_below_threshold")

    def test_should_trade_vix_stale_skip(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        ok, reason = strat.should_trade_symbol("NIFTY", vix=None, dte=2)
        self.assertFalse(ok)
        self.assertEqual(reason, "vix_stale")

    def test_should_trade_passes_all_gates(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        ok, reason = strat.should_trade_symbol("NIFTY", vix=14.0, dte=2)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_legs_for_returns_canonical_set(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        legs = strat.legs_for("NIFTY")
        self.assertEqual(len(legs), 4)
        self.assertEqual(legs[0].role, "short_call")

    def test_legs_for_unknown_symbol_empty(self):
        strat = Wing6IronCondorStrategy.from_profile(self.profile)
        self.assertEqual(strat.legs_for("NONEXISTENT"), ())


if __name__ == "__main__":
    unittest.main()

"""
Fast diagnostic: compare existing ledgers to find why IC wing 8 (long_offset=10) skips trades.
"""
from __future__ import annotations

import sys
from datetime import date, time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.broker_sim import FillModel, opposite_side
from options_backtest.calendar import combine_date_time, expiry_on_or_after, nearest_timestamp, next_trading_day
from options_backtest.dhan_loader import load_dhan_data, DhanContractResolver
from options_backtest.liquidity import contract_is_liquid, oi_slippage_multiplier
from options_backtest.portfolio import signed_cashflow
from options_backtest.schemas import BacktestConfig, Fill, Leg, Side, Trade
from options_backtest.strategy import IronCondor, StrategyContext, OptionType

DHAN_ROOT = ROOT / "data/processed/options/dhan"
NAKED_LEDGER = ROOT / "reports/backtests/options/focused/20260505_024624_dhan_nifty_x1_short_strangle.csv"
IC_LEDGER = ROOT / "reports/backtests/options/risk_management/20260506_mixed_expiry_ic_wing8_nifty.csv"


def diagnose() -> None:
    print("Reading ledgers...")
    naked = pd.read_csv(NAKED_LEDGER)
    naked = naked[naked["strategy"] != "TOTAL"].copy()
    ic = pd.read_csv(IC_LEDGER)
    ic = ic[ic["strategy"] != "TOTAL"].copy()

    naked_dates = set(pd.to_datetime(naked["entry_time"]).dt.date)
    ic_dates = set(pd.to_datetime(ic["entry_time"]).dt.date)
    skipped = sorted(naked_dates - ic_dates)

    print(f"NIFTY naked trades: {len(naked)}")
    print(f"NIFTY IC wing 8 (long_offset=10) trades: {len(ic)}")
    print(f"Skipped dates: {len(skipped)} ({len(skipped)/max(len(naked_dates),1)*100:.1f}%)")

    print("\nLoading Dhan NIFTY weekly data...")
    data = load_dhan_data(DHAN_ROOT, "week", "NIFTY")
    print(f"Loaded: {len(data.trading_dates)} trading dates")

    config = BacktestConfig(symbol="NIFTY", stop_loss_pct=None, target_profit_pct=None, min_dte=1)
    engine = __import__("options_backtest.engine", fromlist=["BacktestEngine"]).BacktestEngine(config)

    reasons: dict[str, int] = {}
    samples = []

    print(f"\n--- Diagnosing {len(skipped)} skipped dates ---")
    for i, d in enumerate(skipped):
        entry_target = combine_date_time(d, config.entry_time)
        day_ts_index = data.atm_timestamps_by_date.get(d, pd.DatetimeIndex([]))
        entry_ts = nearest_timestamp(day_ts_index, entry_target)
        if entry_ts is None:
            reasons["no_entry_ts"] = reasons.get("no_entry_ts", 0) + 1
            continue

        expiry = expiry_on_or_after("NIFTY", d, expiry_type="week", min_dte=1, trading_dates=data.trading_dates)
        resolver = DhanContractResolver(data, d, d, expiry)

        strategy = IronCondor(short_call_offset=2, long_call_offset=10, short_put_offset=-2, long_put_offset=-10)
        context = StrategyContext(timestamp=entry_ts, resolver=resolver)

        # Try entry_legs
        try:
            legs = strategy.entry_legs(context)
        except KeyError as e:
            reasons["entry_legs_KeyError"] = reasons.get("entry_legs_KeyError", 0) + 1
            if len(samples) < 8:
                samples.append((d, "entry_legs_KeyError", str(e)))
            continue

        # Try liquidity
        atm = resolver.atm_strike(entry_ts)
        liq_fail = False
        for leg in legs:
            bars = resolver.bars_for(leg.contract)
            if not contract_is_liquid(bars, leg.contract.strike, atm):
                reasons["liquidity_fail"] = reasons.get("liquidity_fail", 0) + 1
                liq_fail = True
                if len(samples) < 8:
                    samples.append((d, "liquidity_fail", f"{leg.contract.strike}{leg.contract.option_type.value}"))
                break
        if liq_fail:
            continue

        # Try entry fills
        lot_size = engine._resolve_lot_size(d)
        entry_fills = engine._entry_fills(entry_ts, legs, resolver, lot_size, d)
        if len(entry_fills) != len(legs):
            reasons["entry_fill_fail"] = reasons.get("entry_fill_fail", 0) + 1
            if len(samples) < 8:
                samples.append((d, "entry_fill_fail", f"got {len(entry_fills)} fills for {len(legs)} legs"))
            continue

        entry_credit = sum(signed_cashflow(fill) for fill in entry_fills)

        # Try _find_exit
        exit_target = combine_date_time(d, config.exit_time)
        exit_ts, exit_reason = engine._find_exit(entry_ts, exit_target, legs, resolver, entry_credit, lot_size)
        if exit_ts is None:
            reasons["find_exit_None"] = reasons.get("find_exit_None", 0) + 1
            if len(samples) < 8:
                # Debug: why is it None?
                leg_frames = []
                for leg in legs:
                    key = (leg.contract.strike, leg.contract.option_type.value)
                    frame = resolver._bars_cache.get(key)
                    leg_frames.append((leg, frame))
                timestamps = leg_frames[0][1].index if leg_frames[0][1] is not None else pd.Index([])
                for _, frame in leg_frames[1:]:
                    if frame is None:
                        timestamps = pd.Index([])
                        break
                    timestamps = timestamps.intersection(frame.index)
                timestamps = timestamps.intersection(resolver._spot_index.index).sort_values()
                timestamps = timestamps[(timestamps > entry_ts) & (timestamps <= exit_target)]
                samples.append((d, "find_exit_None", f"intersection_timestamps={len(timestamps)}"))
            continue

        # Try exit fills
        exit_fills = engine._exit_fills(exit_ts, legs, resolver, exit_reason, lot_size, d)
        if len(exit_fills) != len(legs):
            reasons["exit_fill_fail"] = reasons.get("exit_fill_fail", 0) + 1
            if len(samples) < 8:
                samples.append((d, "exit_fill_fail", f"got {len(exit_fills)} fills for {len(legs)} legs at {exit_ts}"))
            continue

        reasons["should_have_traded"] = reasons.get("should_have_traded", 0) + 1
        if len(samples) < 8:
            samples.append((d, "should_have_traded", "all checks passed"))

        if (i + 1) % 100 == 0:
            print(f"  processed {i+1}/{len(skipped)}...")

    print(f"\n--- Skip reason breakdown ---")
    for r, c in sorted(reasons.items(), key=lambda x: -x[1]):
        print(f"  {r:25s}: {c:>4} ({c/max(len(skipped),1)*100:.1f}%)")

    print(f"\n--- Sample skipped dates ---")
    for d, reason, detail in samples:
        print(f"  {d} -> {reason}")
        print(f"    {detail}")


if __name__ == "__main__":
    diagnose()

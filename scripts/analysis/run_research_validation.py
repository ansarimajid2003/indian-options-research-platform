"""
Research Paper Validation Runner
=================================
Recreates the three strategies from Indian_Index_Options_Strategy.txt as
faithfully as possible, extended from the paper's two indices (NIFTY,
BANKNIFTY) to all four (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY).

Strategy A – Short Strangle (Intraday)
  Entry  09:40, exit 15:15 (same day)
  Expiry weekly, DTE 0-3 only (expiry-week trades)
  Stop loss 40% of net premium received per leg
  VIX gate 13 <= VIX < 22   (paper "OPTIMAL VIX RANGE: 13-22")
  Strike ATM+2 / ATM-2

Strategy B – Iron Condor (Positional Monthly)
  One entry per monthly cycle at first day where 18 <= DTE <= 24
  Exit at 50% profit OR 3 trading days before expiry (whichever first)
  Stop loss 200% of net credit received (paper "2× credit = exit")
  VIX gate 15 <= VIX < 25   (paper "OPTIMAL VIX RANGE: 15-25")
  Short ATM±6 / long ATM±10  (≈ paper's 300/500 pt example for NIFTY)
  No adjustment/rolling — not supported by the engine.

Strategy C – Short Straddle (Intraday Monthly-week)
  Monday   entry 09:30, stop loss 15% per leg
  Tue-Fri  entry 09:18, stop loss 70% per leg
  Exit 15:15 (same day), monthly expiry, DTE 0-7 only
  VIX gate 10 <= VIX < 30   (paper Section-7 hard limits)

Paper caveats not reproduced here:
  • Adjustment / roll rules for IC (leg-level delta management)
  • Size reduction at intermediate VIX bands (engine runs fixed 1 lot)
  • Cooling-off days after double-leg SL hits (Strategy C)

This script does NOT modify run_focused_contributors.py or any other runner.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.calendar import (
    combine_date_time,
    get_instrument_spec,
    is_trading_day,
    monthly_expiry_on_or_after,
    nearest_timestamp,
)
from options_backtest.dhan_loader import DhanBacktestEngine, DhanContractResolver, load_dhan_data
from options_backtest.reports import build_result, daily_pnl, equity_curve, summary, trade_ledger_with_total
from options_backtest.schemas import BacktestConfig, Trade
from options_backtest.strategy import IronCondor, ShortStraddle, ShortStrangle
from options_backtest.volatility_filter import VixFilter

# ── constants ──────────────────────────────────────────────────────────────────

INDICES: tuple[str, ...] = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")

# Strategy B IC offsets: (short, long) strike count from ATM.
# NIFTY (50pt step): ATM+6 = 300 pts, ATM+10 = 500 pts  ← paper's exact example
# BANKNIFTY (100pt step): same count → 600/1000 pts (proportionally wider)
# FINNIFTY (50pt step): identical to NIFTY
# MIDCPNIFTY (25pt step): same count → 150/250 pts (Dhan ATM±10 ceiling applies)
IC_OFFSETS: dict[str, tuple[int, int]] = {
    "NIFTY":      (6, 10),
    "BANKNIFTY":  (6, 10),
    "FINNIFTY":   (6, 10),
    "MIDCPNIFTY": (6, 10),
}


# ── helpers ────────────────────────────────────────────────────────────────────

def _out_dir() -> Path:
    return Path("reports/backtests/options/research_validation")


def _ledger_path(run_stamp: str, tag: str, symbol: str) -> Path:
    return _out_dir() / f"{run_stamp}_{tag}_{symbol.lower()}.csv"


def _n_trading_days_before(d: date, n: int) -> date:
    """Return the trading day that is exactly n trading sessions before d."""
    candidate = d
    for _ in range(n):
        candidate -= timedelta(days=1)
        while not is_trading_day(candidate):
            candidate -= timedelta(days=1)
    return candidate


def _filter_dates(
    trading_dates: list[date],
    from_date: str | None,
    to_date: str | None,
) -> list[date]:
    result = trading_dates
    if from_date:
        fd = datetime.strptime(from_date, "%Y-%m-%d").date()
        result = [d for d in result if d >= fd]
    if to_date:
        td = datetime.strptime(to_date, "%Y-%m-%d").date()
        result = [d for d in result if d <= td]
    return result


def _make_vix_filter(vix_path: str | None, vix_min: float, vix_max: float, policy: str) -> VixFilter | None:
    if not vix_path:
        return None
    return VixFilter(vix_path, min_vix=vix_min, max_vix=vix_max, missing_policy=policy)


def _combined_summary(ledgers: list[pd.DataFrame]) -> dict:
    non_empty = [l for l in ledgers if l is not None and not l.empty]
    if not non_empty:
        return {}
    # Cast numeric columns to float before concat to avoid object-dtype pollution
    # from empty ledgers produced by build_result([]) which defaults all dtypes to object.
    numeric_cols = ["gross_pnl", "charges", "net_pnl"]
    coerced = []
    for l in non_empty:
        l = l.copy()
        for col in numeric_cols:
            if col in l.columns:
                l[col] = pd.to_numeric(l[col], errors="coerce")
        coerced.append(l)
    combined = pd.concat(coerced, ignore_index=True).sort_values("exit_time").reset_index(drop=True)
    dpnl = daily_pnl(combined)
    curve = equity_curve(dpnl)
    return summary(combined, curve, dpnl)


def _fmt_money(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{'+'if v >= 0 else ''}INR {v:,.0f}"


def _fmt_pct(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v * 100:+.2f}%"


def _fmt_num(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:.3f}"


# ── Strategy A ─────────────────────────────────────────────────────────────────

def _run_strategy_a(
    args: argparse.Namespace,
    symbol: str,
    run_stamp: str,
) -> tuple[str, dict, Path, pd.DataFrame]:
    """Short Strangle — intraday, expiry-week only (DTE 0-3)."""
    config = BacktestConfig(
        symbol=symbol,
        entry_time=time(9, 40),
        exit_time=time(15, 15),
        stop_loss_pct=0.4,
        target_profit_pct=None,
        min_dte=0,
        max_dte=3,
        vix_path=args.vix_path,
        vix_min=13.0,
        vix_max=22.0,
        vix_missing_policy=args.vix_missing_policy,
    )
    data = load_dhan_data(args.dhan_root, "week", symbol)
    engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type="week")
    result = engine.run(
        ShortStrangle(call_offset=2, put_offset=-2, min_leg_premium=2.0),
        from_date=args.from_date,
        to_date=args.to_date,
        data=data,
    )

    out = _ledger_path(run_stamp, "a_strangle", symbol)
    out.parent.mkdir(parents=True, exist_ok=True)
    trade_ledger_with_total(result.trade_ledger).to_csv(out, index=False)

    ledger = result.trade_ledger.copy()
    ledger.insert(0, "symbol", symbol)
    return symbol, result.summary, out, ledger


# ── Strategy B ─────────────────────────────────────────────────────────────────

def _run_strategy_b(
    args: argparse.Namespace,
    symbol: str,
    run_stamp: str,
) -> tuple[str, dict, Path, pd.DataFrame]:
    """Iron Condor — one entry per monthly cycle at 18-24 DTE, multi-week hold.

    Custom loop required because the standard engine enters a new trade every
    trading day; this strategy opens exactly one position per monthly expiry.
    """
    short_off, long_off = IC_OFFSETS[symbol]
    strategy = IronCondor(
        short_call_offset=short_off,
        long_call_offset=long_off,
        short_put_offset=-short_off,
        long_put_offset=-long_off,
    )
    config = BacktestConfig(
        symbol=symbol,
        entry_time=time(9, 40),
        exit_time=time(15, 15),
        stop_loss_pct=2.0,       # 2× credit received
        target_profit_pct=0.5,   # 50% of credit
        min_dte=0,
        vix_path=args.vix_path,
        vix_min=15.0,
        vix_max=25.0,
        vix_missing_policy=args.vix_missing_policy,
    )
    data = load_dhan_data(args.dhan_root, "month", symbol)
    engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type="month")

    vix_filter = _make_vix_filter(args.vix_path, 15.0, 25.0, args.vix_missing_policy)
    all_dates = _filter_dates(data.trading_dates, args.from_date, args.to_date)

    trades: list[Trade] = []
    entered_expiries: set[date] = set()

    for trade_date in all_dates:
        expiry = monthly_expiry_on_or_after(
            symbol, trade_date, min_dte=0, trading_dates=data.trading_dates
        )
        if expiry in entered_expiries:
            continue  # already entered this monthly cycle

        dte = (expiry - trade_date).days
        if not (18 <= dte <= 24):
            continue  # outside the 18-24 DTE entry window

        entered_expiries.add(expiry)

        # Hold until 50% profit OR 3 trading days before expiry
        exit_date = _n_trading_days_before(expiry, 3)
        if exit_date <= trade_date:
            continue

        entry_target = combine_date_time(trade_date, config.entry_time)
        day_ts_index = data.atm_timestamps_by_date.get(trade_date, pd.DatetimeIndex([]))
        entry_ts = nearest_timestamp(day_ts_index, entry_target)
        if entry_ts is None or entry_ts.date() != trade_date:
            continue

        if vix_filter is not None:
            allowed, _ = vix_filter.allows(entry_ts)
            if not allowed:
                continue

        exit_target = combine_date_time(exit_date, config.exit_time)
        # Build resolver for the full [entry_date, exit_date] window so _find_exit
        # can scan intermediate 1-min bars and detect the 50% target mid-hold.
        resolver = DhanContractResolver(data, trade_date, exit_date, expiry)

        trade = engine._execute_trade(
            expiry_dir=Path(trade_date.strftime("%Y%m%d")),
            entry_ts=entry_ts,
            exit_target=exit_target,
            strategy=strategy,
            resolver=resolver,
            option_bars=pd.DataFrame(),
            spot_bars=resolver.spot_bars,
            expiry=expiry,
        )
        if trade is not None:
            trades.append(trade)

    result = build_result(config, trades)
    out = _ledger_path(run_stamp, "b_ic", symbol)
    out.parent.mkdir(parents=True, exist_ok=True)
    trade_ledger_with_total(result.trade_ledger).to_csv(out, index=False)

    ledger = result.trade_ledger.copy()
    ledger.insert(0, "symbol", symbol)
    return symbol, result.summary, out, ledger


# ── Strategy C ─────────────────────────────────────────────────────────────────

def _run_strategy_c(
    args: argparse.Namespace,
    symbol: str,
    run_stamp: str,
) -> tuple[str, dict, Path, pd.DataFrame]:
    """Short Straddle — intraday, monthly expiry week (DTE 0-7).

    Monday: entry 09:30, SL 15%.  Tue-Fri: entry 09:18, SL 70%.
    Custom loop required to apply different configs per weekday.
    """
    strategy = ShortStraddle()

    # Two configs — selected at runtime per trade_date weekday
    mon_config = BacktestConfig(
        symbol=symbol,
        entry_time=time(9, 30),
        exit_time=time(15, 15),
        stop_loss_pct=0.15,
        target_profit_pct=None,
        min_dte=0,
        vix_path=args.vix_path,
        vix_min=10.0,
        vix_max=30.0,
        vix_missing_policy=args.vix_missing_policy,
    )
    other_config = BacktestConfig(
        symbol=symbol,
        entry_time=time(9, 18),
        exit_time=time(15, 15),
        stop_loss_pct=0.70,
        target_profit_pct=None,
        min_dte=0,
        vix_path=args.vix_path,
        vix_min=10.0,
        vix_max=30.0,
        vix_missing_policy=args.vix_missing_policy,
    )

    data = load_dhan_data(args.dhan_root, "month", symbol)
    mon_engine = DhanBacktestEngine(mon_config, dhan_root=args.dhan_root, expiry_type="month")
    other_engine = DhanBacktestEngine(other_config, dhan_root=args.dhan_root, expiry_type="month")

    vix_filter = _make_vix_filter(args.vix_path, 10.0, 30.0, args.vix_missing_policy)
    all_dates = _filter_dates(data.trading_dates, args.from_date, args.to_date)

    trades: list[Trade] = []

    for trade_date in all_dates:
        expiry = monthly_expiry_on_or_after(
            symbol, trade_date, min_dte=0, trading_dates=data.trading_dates
        )
        dte = (expiry - trade_date).days
        if dte > 7:  # DTE 0-7 only — last week of the monthly cycle
            continue

        is_monday = trade_date.weekday() == 0
        cfg = mon_config if is_monday else other_config
        eng = mon_engine if is_monday else other_engine

        entry_target = combine_date_time(trade_date, cfg.entry_time)
        day_ts_index = data.atm_timestamps_by_date.get(trade_date, pd.DatetimeIndex([]))
        entry_ts = nearest_timestamp(day_ts_index, entry_target)
        if entry_ts is None or entry_ts.date() != trade_date:
            continue

        if vix_filter is not None:
            allowed, _ = vix_filter.allows(entry_ts)
            if not allowed:
                continue

        exit_target = combine_date_time(trade_date, cfg.exit_time)
        resolver = DhanContractResolver(data, trade_date, trade_date, expiry)

        trade = eng._execute_trade(
            expiry_dir=Path(trade_date.strftime("%Y%m%d")),
            entry_ts=entry_ts,
            exit_target=exit_target,
            strategy=strategy,
            resolver=resolver,
            option_bars=pd.DataFrame(),
            spot_bars=resolver.spot_bars,
            expiry=expiry,
        )
        if trade is not None:
            trades.append(trade)

    # Use other_config as the base for build_result (it covers the majority of trades)
    result = build_result(other_config, trades)
    out = _ledger_path(run_stamp, "c_straddle", symbol)
    out.parent.mkdir(parents=True, exist_ok=True)
    trade_ledger_with_total(result.trade_ledger).to_csv(out, index=False)

    ledger = result.trade_ledger.copy()
    ledger.insert(0, "symbol", symbol)
    return symbol, result.summary, out, ledger


# ── reporting ──────────────────────────────────────────────────────────────────

def _strategy_summary_md(
    *,
    run_stamp: str,
    strategy_label: str,
    strategy_tag: str,
    description: str,
    rows: list[tuple[str, dict, Path]],
    combined_stats: dict,
    combined_path: Path,
    summary_path: Path,
    from_date: str | None,
    to_date: str | None,
) -> str:
    lines = [
        f"# {strategy_label} — Research Validation {run_stamp}",
        "",
        description,
        "",
        f"Window: `{from_date or 'first available'}` to `{to_date or 'last available'}`",
        "",
        "## Portfolio",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Trades | {combined_stats.get('trades', 0):,} |",
        f"| Net PnL | {_fmt_money(combined_stats.get('net_pnl'))} |",
        f"| Total return | {_fmt_pct(combined_stats.get('total_return'))} |",
        f"| CAGR | {_fmt_pct(combined_stats.get('cagr'))} |",
        f"| Sharpe | {_fmt_num(combined_stats.get('sharpe'))} |",
        f"| Sortino | {_fmt_num(combined_stats.get('sortino'))} |",
        f"| t-stat | {_fmt_num(combined_stats.get('sharpe_tstat'))} |",
        f"| Max drawdown | {_fmt_money(combined_stats.get('max_drawdown'))} |",
        f"| Max drawdown pct | {_fmt_pct(combined_stats.get('max_drawdown_pct'))} |",
        f"| Profit factor | {_fmt_num(combined_stats.get('profit_factor'))} |",
        "",
        "## Per-index",
        "",
        "| Symbol | Trades | Net PnL | CAGR | Sharpe | t-stat | Max DD% | PF | Win% | Ledger |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for symbol, stats, ledger_path in rows:
        win_pct = stats.get("win_rate")
        win_str = f"{win_pct * 100:.1f}%" if win_pct is not None else "-"
        lines.append(
            f"| {symbol}"
            f" | {stats.get('trades', 0):,}"
            f" | {_fmt_money(stats.get('net_pnl'))}"
            f" | {_fmt_pct(stats.get('cagr'))}"
            f" | {_fmt_num(stats.get('sharpe'))}"
            f" | {_fmt_num(stats.get('sharpe_tstat'))}"
            f" | {_fmt_pct(stats.get('max_drawdown_pct'))}"
            f" | {_fmt_num(stats.get('profit_factor'))}"
            f" | {win_str}"
            f" | `{ledger_path.name}` |"
        )
    lines.extend([
        "",
        "## Files",
        "",
        f"- Combined ledger: `{combined_path.name}`",
        f"- Summary: `{summary_path.name}`",
    ])
    return "\n".join(lines)


def _master_report_md(
    *,
    run_stamp: str,
    from_date: str | None,
    to_date: str | None,
    strategy_blocks: list[tuple[str, list[tuple[str, dict, Path]], dict]],
    notes: list[str],
) -> str:
    lines = [
        f"# Research Paper Validation — {run_stamp}",
        "",
        "Recreates Indian_Index_Options_Strategy.txt for all four indices.",
        f"Window: `{from_date or 'first available'}` to `{to_date or 'last available'}`",
        "",
    ]
    for label, rows, combined in strategy_blocks:
        lines += [
            f"## {label}",
            "",
            "| Symbol | Trades | Net PnL | Sharpe | t-stat | Win% | Max DD% | PF |",
            "|---|---:|---:|---:|---:|---:|---:|---:|",
        ]
        for symbol, stats, _ in rows:
            win_pct = stats.get("win_rate")
            win_str = f"{win_pct * 100:.1f}%" if win_pct is not None else "-"
            lines.append(
                f"| {symbol}"
                f" | {stats.get('trades', 0):,}"
                f" | {_fmt_money(stats.get('net_pnl'))}"
                f" | {_fmt_num(stats.get('sharpe'))}"
                f" | {_fmt_num(stats.get('sharpe_tstat'))}"
                f" | {win_str}"
                f" | {_fmt_pct(stats.get('max_drawdown_pct'))}"
                f" | {_fmt_num(stats.get('profit_factor'))} |"
            )
        if combined:
            lines += [
                "",
                f"**Portfolio** — Trades: {combined.get('trades', 0):,},"
                f" Net PnL: {_fmt_money(combined.get('net_pnl'))},"
                f" Sharpe: {_fmt_num(combined.get('sharpe'))},"
                f" Max DD: {_fmt_pct(combined.get('max_drawdown_pct'))}",
            ]
        lines.append("")

    if notes:
        lines += ["## Notes", ""]
        for note in notes:
            lines.append(f"- {note}")
        lines.append("")

    return "\n".join(lines)


# ── main ───────────────────────────────────────────────────────────────────────

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Research paper validation — three strategies × four indices."
    )
    parser.add_argument("--dhan-root", default="data/processed/options/dhan")
    parser.add_argument("--from-date", default="2022-02-01")
    parser.add_argument("--to-date",   default="2026-04-30")
    parser.add_argument("--run-stamp", help="Output prefix; default = YYYYMMDD_HHMMSS")
    parser.add_argument("--vix-path",  default="data/processed/spot/indiavix_1min_DHAN.csv",
                        help="India VIX 1-min CSV with timestamp and close columns")
    parser.add_argument("--vix-missing-policy", choices=["skip", "allow"], default="skip")
    parser.add_argument(
        "--strategies", nargs="+", choices=["A", "B", "C"], default=["A", "B", "C"],
        help="Which strategies to run (default: all three)",
    )
    args = parser.parse_args()

    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"run_stamp : {run_stamp}")
    print(f"window    : {args.from_date} to {args.to_date}")
    print(f"vix_path  : {args.vix_path}")
    print(f"strategies: {', '.join(args.strategies)}")
    print()

    _out_dir().mkdir(parents=True, exist_ok=True)

    strategy_blocks: list[tuple[str, list[tuple[str, dict, Path]], dict]] = []

    # ── Strategy A ────────────────────────────────────────────────────────────
    if "A" in args.strategies:
        print("=== Strategy A: Short Strangle (DTE 0-3, intraday) ===")
        a_rows: list[tuple[str, dict, Path]] = []
        a_ledgers: list[pd.DataFrame] = []
        for symbol in INDICES:
            sym, stats, out, ledger = _run_strategy_a(args, symbol, run_stamp)
            print(
                f"  {sym:12s} trades={stats.get('trades', 0):>4}"
                f"  net={_fmt_money(stats.get('net_pnl'))}"
                f"  sharpe={_fmt_num(stats.get('sharpe'))}"
                f"  win={stats.get('win_rate', 0) * 100:.1f}%"
            )
            a_rows.append((sym, stats, out))
            a_ledgers.append(ledger)

        a_combined = _combined_summary(a_ledgers)
        a_combined_path = _out_dir() / f"{run_stamp}_a_strangle_combined.csv"
        if a_ledgers:
            pd.concat(a_ledgers, ignore_index=True).to_csv(a_combined_path, index=False)

        a_summary_path = _out_dir() / f"{run_stamp}_a_strangle_summary.md"
        a_desc = (
            "Entry 09:40, exit 15:15, weekly expiry, DTE 0-3, SL 40% of premium, "
            "VIX 13-22, ATM±2 short strangle."
        )
        a_summary_path.write_text(
            _strategy_summary_md(
                run_stamp=run_stamp,
                strategy_label="Strategy A — Short Strangle (Intraday)",
                strategy_tag="a_strangle",
                description=a_desc,
                rows=a_rows,
                combined_stats=a_combined,
                combined_path=a_combined_path,
                summary_path=a_summary_path,
                from_date=args.from_date,
                to_date=args.to_date,
            ),
            encoding="utf-8",
        )
        strategy_blocks.append(("Strategy A — Short Strangle (DTE 0-3, intraday, VIX 13-22)", a_rows, a_combined))
        print(f"  combined: trades={a_combined.get('trades',0)} net={_fmt_money(a_combined.get('net_pnl'))}")
        print()

    # ── Strategy B ────────────────────────────────────────────────────────────
    if "B" in args.strategies:
        print("=== Strategy B: Iron Condor (monthly, 18-24 DTE entry) ===")
        b_rows: list[tuple[str, dict, Path]] = []
        b_ledgers: list[pd.DataFrame] = []
        for symbol in INDICES:
            sym, stats, out, ledger = _run_strategy_b(args, symbol, run_stamp)
            print(
                f"  {sym:12s} trades={stats.get('trades', 0):>4}"
                f"  net={_fmt_money(stats.get('net_pnl'))}"
                f"  sharpe={_fmt_num(stats.get('sharpe'))}"
                f"  win={stats.get('win_rate', 0) * 100:.1f}%"
            )
            b_rows.append((sym, stats, out))
            b_ledgers.append(ledger)

        b_combined = _combined_summary(b_ledgers)
        b_combined_path = _out_dir() / f"{run_stamp}_b_ic_combined.csv"
        if b_ledgers:
            pd.concat(b_ledgers, ignore_index=True).to_csv(b_combined_path, index=False)

        b_summary_path = _out_dir() / f"{run_stamp}_b_ic_summary.md"
        b_desc = (
            "One entry per monthly cycle at first day with 18-24 DTE. "
            "Exit at 50% profit OR 3 trading days before expiry. "
            "SL 200% of credit received. VIX 15-25. "
            "Short ATM±6, long ATM±10. No roll/adjustment."
        )
        b_summary_path.write_text(
            _strategy_summary_md(
                run_stamp=run_stamp,
                strategy_label="Strategy B — Iron Condor (Positional Monthly)",
                strategy_tag="b_ic",
                description=b_desc,
                rows=b_rows,
                combined_stats=b_combined,
                combined_path=b_combined_path,
                summary_path=b_summary_path,
                from_date=args.from_date,
                to_date=args.to_date,
            ),
            encoding="utf-8",
        )
        strategy_blocks.append(("Strategy B — Iron Condor (monthly, 18-24 DTE, VIX 15-25)", b_rows, b_combined))
        print(f"  combined: trades={b_combined.get('trades',0)} net={_fmt_money(b_combined.get('net_pnl'))}")
        print()

    # ── Strategy C ────────────────────────────────────────────────────────────
    if "C" in args.strategies:
        print("=== Strategy C: Short Straddle (monthly expiry week, DTE 0-7) ===")
        c_rows: list[tuple[str, dict, Path]] = []
        c_ledgers: list[pd.DataFrame] = []
        for symbol in INDICES:
            sym, stats, out, ledger = _run_strategy_c(args, symbol, run_stamp)
            print(
                f"  {sym:12s} trades={stats.get('trades', 0):>4}"
                f"  net={_fmt_money(stats.get('net_pnl'))}"
                f"  sharpe={_fmt_num(stats.get('sharpe'))}"
                f"  win={stats.get('win_rate', 0) * 100:.1f}%"
            )
            c_rows.append((sym, stats, out))
            c_ledgers.append(ledger)

        c_combined = _combined_summary(c_ledgers)
        c_combined_path = _out_dir() / f"{run_stamp}_c_straddle_combined.csv"
        if c_ledgers:
            pd.concat(c_ledgers, ignore_index=True).to_csv(c_combined_path, index=False)

        c_summary_path = _out_dir() / f"{run_stamp}_c_straddle_summary.md"
        c_desc = (
            "Monday: entry 09:30, SL 15%. Tue-Fri: entry 09:18, SL 70%. "
            "Exit 15:15, monthly expiry, DTE 0-7. VIX 10-30. ATM short straddle."
        )
        c_summary_path.write_text(
            _strategy_summary_md(
                run_stamp=run_stamp,
                strategy_label="Strategy C — Short Straddle (Intraday Monthly-week)",
                strategy_tag="c_straddle",
                description=c_desc,
                rows=c_rows,
                combined_stats=c_combined,
                combined_path=c_combined_path,
                summary_path=c_summary_path,
                from_date=args.from_date,
                to_date=args.to_date,
            ),
            encoding="utf-8",
        )
        strategy_blocks.append(("Strategy C — Short Straddle (DTE 0-7, Mon SL 15% / Other SL 70%, VIX 10-30)", c_rows, c_combined))
        print(f"  combined: trades={c_combined.get('trades',0)} net={_fmt_money(c_combined.get('net_pnl'))}")
        print()

    # ── Master report ─────────────────────────────────────────────────────────
    notes = [
        "Strategy A VIX gate uses the paper's stated 'OPTIMAL VIX RANGE 13-22'. "
        "The paper's size-reduction bands (10-13 and 22-30) are not modelled — engine runs fixed 1 lot.",
        "Strategy B uses ATM±6/±10 offsets for all indices. "
        "For NIFTY (50pt step) this equals the paper's 300/500 pt example. "
        "BANKNIFTY (100pt step) → 600/1000 pt; MIDCPNIFTY (25pt step) → 150/250 pt.",
        "Strategy B adjustment rules (roll when spot within 100 pt of short strike) are not implemented.",
        "Strategy C cooling-off rule (skip next day after double-leg SL) is not implemented.",
        "2026 STT rates (0.15%) apply — paper assumed 0.10%. ~15-20% higher cost drag on low-premium legs.",
        "All stop-loss fills execute at the open of the bar after the trigger bar, per engine convention.",
    ]
    master_path = _out_dir() / f"{run_stamp}_research_validation_master.md"
    master_path.write_text(
        _master_report_md(
            run_stamp=run_stamp,
            from_date=args.from_date,
            to_date=args.to_date,
            strategy_blocks=strategy_blocks,
            notes=notes,
        ),
        encoding="utf-8",
    )
    print(f"master report -> {master_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

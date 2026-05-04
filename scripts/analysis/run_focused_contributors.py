from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.dhan_loader import DhanBacktestEngine, load_dhan_data
from options_backtest.reports import daily_pnl, equity_curve, summary, trade_ledger_with_total
from options_backtest.schemas import BacktestConfig
from options_backtest.strategy import ShortStrangle


CONTRIBUTORS = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")


def _out_dir() -> Path:
    return Path("reports/backtests/options/focused")


def _ledger_path(run_stamp: str, symbol: str) -> Path:
    return _out_dir() / f"{run_stamp}_dhan_{symbol.lower()}_short_strangle.csv"


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "-"
    sign = "+" if value >= 0 else "-"
    return f"{sign}INR {abs(value):,.0f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value * 100:+.2f}%"


def _fmt_num(value: float | None) -> str:
    if value is None:
        return "-"
    return f"{value:.3f}"


def _run_symbol(args: argparse.Namespace, symbol: str, run_stamp: str) -> tuple[str, dict, Path, pd.DataFrame]:
    config = BacktestConfig(symbol=symbol, stop_loss_pct=None, target_profit_pct=None, min_dte=1)
    data = load_dhan_data(args.dhan_root, args.expiry_type, symbol)
    engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type=args.expiry_type)
    result = engine.run(ShortStrangle(min_leg_premium=2.0), from_date=args.from_date, to_date=args.to_date, data=data)

    output = _ledger_path(run_stamp, symbol)
    output.parent.mkdir(parents=True, exist_ok=True)
    trade_ledger_with_total(result.trade_ledger).to_csv(output, index=False)

    ledger = result.trade_ledger.copy()
    ledger.insert(0, "symbol", symbol)
    return symbol, result.summary, output, ledger


def _combined_summary(ledgers: list[pd.DataFrame]) -> tuple[dict, pd.DataFrame]:
    if not ledgers:
        empty = pd.DataFrame()
        return summary(empty, empty, empty), empty
    combined = pd.concat(ledgers, ignore_index=True)
    combined = combined.sort_values(["exit_time", "symbol"]).reset_index(drop=True)
    daily = daily_pnl(combined)
    curve = equity_curve(daily)
    return summary(combined, curve, daily), combined


def _write_summary(
    *,
    run_stamp: str,
    rows: list[tuple[str, dict, Path]],
    combined_summary: dict,
    combined_path: Path,
    from_date: str | None,
    to_date: str | None,
) -> Path:
    path = _out_dir() / f"{run_stamp}_focused_contributors_summary.md"
    lines = [
        f"# Focused Contributors - {run_stamp}",
        "",
        "Only the contributors that survived the cross-index screen are included.",
        "",
        "Included:",
        "",
        "- NIFTY short strangle",
        "- BANKNIFTY short strangle",
        "- FINNIFTY short strangle",
        "- MIDCPNIFTY short strangle",
        "",
        "Excluded: 3 PM variants, expiry-day long gamma, short straddles, and all timing/target sweeps.",
        "",
        f"Window: `{from_date or 'first available'}` to `{to_date or 'last available'}`",
        "",
        "## Portfolio",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Trades | {combined_summary.get('trades', 0):,} |",
        f"| Net PnL | {_fmt_money(combined_summary.get('net_pnl'))} |",
        f"| Total return | {_fmt_pct(combined_summary.get('total_return'))} |",
        f"| CAGR | {_fmt_pct(combined_summary.get('cagr'))} |",
        f"| Sharpe | {_fmt_num(combined_summary.get('sharpe'))} |",
        f"| Sortino | {_fmt_num(combined_summary.get('sortino'))} |",
        f"| t-stat | {_fmt_num(combined_summary.get('sharpe_tstat'))} |",
        f"| Max drawdown | {_fmt_money(combined_summary.get('max_drawdown'))} |",
        f"| Max drawdown pct | {_fmt_pct(combined_summary.get('max_drawdown_pct'))} |",
        f"| Profit factor | {_fmt_num(combined_summary.get('profit_factor'))} |",
        "",
        "## Contributors",
        "",
        "| Symbol | Trades | Net PnL | CAGR | Sharpe | BnH Sharpe | t-stat | Max DD% | PF | Ledger |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ]

    for symbol, stats, ledger_path in rows:
        lines.append(
            f"| {symbol} | {stats.get('trades', 0):,}"
            f" | {_fmt_money(stats.get('net_pnl'))}"
            f" | {_fmt_pct(stats.get('cagr'))}"
            f" | {_fmt_num(stats.get('sharpe'))}"
            f" | {_fmt_num(stats.get('bnh_sharpe'))}"
            f" | {_fmt_num(stats.get('sharpe_tstat'))}"
            f" | {_fmt_pct(stats.get('max_drawdown_pct'))}"
            f" | {_fmt_num(stats.get('profit_factor'))}"
            f" | `{ledger_path.name}` |"
        )

    lines.extend([
        "",
        "## Files",
        "",
        f"- Combined ledger: `{combined_path.name}`",
        f"- Summary: `{path.name}`",
        "",
        "## Read",
        "",
        "This is still a naked short-premium expression. It is the clean contributor pack for research, not the final live structure.",
        "The next version should convert these contributors into defined-risk credit spreads or iron condors before deployment.",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the focused cross-index contributor pack.")
    parser.add_argument("--dhan-root", default="data/processed/options/dhan")
    parser.add_argument("--expiry-type", choices=["week", "month"], default="week")
    parser.add_argument("--from-date", default="2022-02-01")
    parser.add_argument("--to-date", default="2026-04-30")
    parser.add_argument("--run-stamp", help="Output prefix, default YYYYMMDD_HHMMSS captured once.")
    args = parser.parse_args()

    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"run_stamp: {run_stamp}")
    print(f"contributors: {', '.join(CONTRIBUTORS)}")

    rows: list[tuple[str, dict, Path]] = []
    ledgers: list[pd.DataFrame] = []
    for symbol in CONTRIBUTORS:
        symbol, stats, output, ledger = _run_symbol(args, symbol, run_stamp)
        print(
            f"{symbol}: trades={stats.get('trades', 0):>4} "
            f"net={stats.get('net_pnl', 0):>12,.0f} "
            f"sharpe={stats.get('sharpe')}"
        )
        rows.append((symbol, stats, output))
        ledgers.append(ledger)

    combined_stats, combined = _combined_summary(ledgers)
    combined_path = _out_dir() / f"{run_stamp}_focused_contributors_combined.csv"
    combined.to_csv(combined_path, index=False)
    summary_path = _write_summary(
        run_stamp=run_stamp,
        rows=rows,
        combined_summary=combined_stats,
        combined_path=combined_path,
        from_date=args.from_date,
        to_date=args.to_date,
    )

    print(f"combined: trades={combined_stats.get('trades', 0)} net={combined_stats.get('net_pnl', 0):,.0f}")
    print(f"summary -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

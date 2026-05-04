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


# All (symbol, lots) combinations needed across all portfolios.
SYMBOL_LOTS: list[tuple[str, int]] = [
    ("NIFTY", 1),
    ("BANKNIFTY", 1),
    ("BANKNIFTY", 2),
    ("FINNIFTY", 1),
    ("MIDCPNIFTY", 1),
    ("MIDCPNIFTY", 5),
    ("MIDCPNIFTY", 7),
]

# Three portfolios: baseline all-4 at 1 lot, and two concentrated configurations.
PORTFOLIOS: dict[str, list[tuple[str, int]]] = {
    "baseline_4x1":    [("NIFTY", 1), ("BANKNIFTY", 1), ("FINNIFTY", 1), ("MIDCPNIFTY", 1)],
    "port_A_bn1_mcp7": [("BANKNIFTY", 1), ("MIDCPNIFTY", 7)],
    "port_B_bn2_mcp5": [("BANKNIFTY", 2), ("MIDCPNIFTY", 5)],
}

PORTFOLIO_LABELS: dict[str, str] = {
    "baseline_4x1":    "Baseline (NIFTY 1 + BN 1 + FN 1 + MCP 1)",
    "port_A_bn1_mcp7": "Port A — BANKNIFTY 1 + MIDCPNIFTY 7",
    "port_B_bn2_mcp5": "Port B — BANKNIFTY 2 + MIDCPNIFTY 5",
}


def _out_dir() -> Path:
    return Path("reports/backtests/options/focused")


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


def _run_one(
    args: argparse.Namespace,
    symbol: str,
    lots: int,
    run_stamp: str,
) -> tuple[dict, pd.DataFrame]:
    config = BacktestConfig(symbol=symbol, stop_loss_pct=None, target_profit_pct=None, min_dte=1)
    data = load_dhan_data(args.dhan_root, args.expiry_type, symbol)
    engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type=args.expiry_type)
    result = engine.run(
        ShortStrangle(min_leg_premium=2.0, lots=lots),
        from_date=args.from_date,
        to_date=args.to_date,
        data=data,
    )

    ledger = result.trade_ledger.copy()
    ledger.insert(0, "lots", lots)
    ledger.insert(0, "symbol", symbol)

    out = _out_dir() / f"{run_stamp}_dhan_{symbol.lower()}_x{lots}_short_strangle.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    trade_ledger_with_total(result.trade_ledger).to_csv(out, index=False)

    return result.summary, ledger


def _portfolio_stats(ledgers: list[pd.DataFrame]) -> dict:
    if not ledgers:
        empty = pd.DataFrame()
        return summary(empty, empty, empty)
    combined = pd.concat(ledgers, ignore_index=True)
    combined = combined.sort_values(["exit_time", "symbol"]).reset_index(drop=True)
    daily = daily_pnl(combined)
    curve = equity_curve(daily)
    return summary(combined, curve, daily)


def _write_summary(
    *,
    run_stamp: str,
    results: dict[tuple[str, int], dict],
    portfolio_stats: dict[str, dict],
    from_date: str | None,
    to_date: str | None,
) -> Path:
    path = _out_dir() / f"{run_stamp}_lot_sizing_summary.md"

    lines = [
        f"# Lot Sizing Study — {run_stamp}",
        "",
        f"Window: `{from_date or 'first available'}` to `{to_date or 'last available'}`",
        "",
        "Compares three portfolio configurations to find the optimal lot allocation",
        "for BANKNIFTY and MIDCPNIFTY, which outperform buy-and-hold individually.",
        "",
        "## Portfolio Comparison",
        "",
        "| Metric | " + " | ".join(PORTFOLIO_LABELS[k] for k in PORTFOLIOS) + " |",
        "|---|" + "|".join("---:" for _ in PORTFOLIOS) + "|",
    ]

    metrics = [
        ("Trades",          lambda s: f"{s.get('trades', 0):,}"),
        ("Net PnL",         lambda s: _fmt_money(s.get("net_pnl"))),
        ("Total return",    lambda s: _fmt_pct(s.get("total_return"))),
        ("CAGR",            lambda s: _fmt_pct(s.get("cagr"))),
        ("Sharpe",          lambda s: _fmt_num(s.get("sharpe"))),
        ("Sortino",         lambda s: _fmt_num(s.get("sortino"))),
        ("t-stat",          lambda s: _fmt_num(s.get("sharpe_tstat"))),
        ("Max drawdown",    lambda s: _fmt_money(s.get("max_drawdown"))),
        ("Max DD%",         lambda s: _fmt_pct(s.get("max_drawdown_pct"))),
        ("Profit factor",   lambda s: _fmt_num(s.get("profit_factor"))),
    ]

    for label, fmt in metrics:
        cells = " | ".join(fmt(portfolio_stats[k]) for k in PORTFOLIOS)
        lines.append(f"| {label} | {cells} |")

    lines.extend([
        "",
        "## Individual Positions",
        "",
        "| Symbol | Lots | Trades | Net PnL | CAGR | Sharpe | t-stat | Max DD% | PF |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])

    for (symbol, lots), stats in sorted(results.items()):
        lines.append(
            f"| {symbol} | {lots}"
            f" | {stats.get('trades', 0):,}"
            f" | {_fmt_money(stats.get('net_pnl'))}"
            f" | {_fmt_pct(stats.get('cagr'))}"
            f" | {_fmt_num(stats.get('sharpe'))}"
            f" | {_fmt_num(stats.get('sharpe_tstat'))}"
            f" | {_fmt_pct(stats.get('max_drawdown_pct'))}"
            f" | {_fmt_num(stats.get('profit_factor'))}"
            " |"
        )

    lines.extend([
        "",
        "## Notes",
        "",
        "- All portfolios use `target_profit_pct=None` (pure theta capture, no target lottery).",
        "- `min_dte=1` excludes expiry-day entries.",
        "- `min_leg_premium=2.0` excludes sub-tick MIDCPNIFTY phantoms.",
        "- Port A and B are concentrated 2-symbol books. Add NIFTY/FINNIFTY at 1 lot for diversification.",
        "- These are naked short-premium positions. Convert to defined-risk (iron condors) before live deployment.",
    ])

    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="Lot sizing study: compare portfolio configurations.")
    parser.add_argument("--dhan-root", default="data/processed/options/dhan")
    parser.add_argument("--expiry-type", choices=["week", "month"], default="week")
    parser.add_argument("--from-date", default="2022-02-01")
    parser.add_argument("--to-date", default="2026-04-30")
    parser.add_argument("--run-stamp", help="Output prefix, default YYYYMMDD_HHMMSS captured once.")
    args = parser.parse_args()

    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    print(f"run_stamp: {run_stamp}")
    print(f"runs: {len(SYMBOL_LOTS)}")

    results: dict[tuple[str, int], dict] = {}
    ledgers: dict[tuple[str, int], pd.DataFrame] = {}

    for symbol, lots in SYMBOL_LOTS:
        stats, ledger = _run_one(args, symbol, lots, run_stamp)
        results[(symbol, lots)] = stats
        ledgers[(symbol, lots)] = ledger
        print(
            f"  {symbol} x{lots}: trades={stats.get('trades', 0):>4}"
            f"  net={stats.get('net_pnl', 0):>12,.0f}"
            f"  sharpe={_fmt_num(stats.get('sharpe'))}"
            f"  dd={_fmt_pct(stats.get('max_drawdown_pct'))}"
        )

    portfolio_stats: dict[str, dict] = {}
    for port_key, composition in PORTFOLIOS.items():
        port_ledgers = [ledgers[key] for key in composition]
        stats = _portfolio_stats(port_ledgers)
        portfolio_stats[port_key] = stats
        print(
            f"\n{PORTFOLIO_LABELS[port_key]}:"
            f"  trades={stats.get('trades', 0)}"
            f"  net={stats.get('net_pnl', 0):,.0f}"
            f"  cagr={_fmt_pct(stats.get('cagr'))}"
            f"  sharpe={_fmt_num(stats.get('sharpe'))}"
            f"  dd={_fmt_pct(stats.get('max_drawdown_pct'))}"
        )

    summary_path = _write_summary(
        run_stamp=run_stamp,
        results=results,
        portfolio_stats=portfolio_stats,
        from_date=args.from_date,
        to_date=args.to_date,
    )
    print(f"\nsummary -> {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

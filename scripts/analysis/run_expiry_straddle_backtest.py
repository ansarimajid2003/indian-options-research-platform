from __future__ import annotations

import argparse
import sys
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.dhan_loader import DhanBacktestEngine, load_dhan_data
from options_backtest.reports import batch_summary_md, trade_ledger_with_total
from options_backtest.schemas import BacktestConfig
from options_backtest.strategy import ExpiryDayStraddle, ExpiryDayStrangle

# Entry times to sweep by default.  Override with --entry-times HH:MM,HH:MM,...
DEFAULT_ENTRY_TIMES = ["09:15", "10:00", "11:00", "13:00", "14:00", "14:30", "15:00"]

# Structures available via --structures flag
def _structure(name: str, symbol: str) -> object:
    if name == "strangle1":
        return ExpiryDayStrangle(call_otm_steps=1, put_otm_steps=1, lots=1, symbol=symbol)
    if name == "strangle2":
        return ExpiryDayStrangle(call_otm_steps=2, put_otm_steps=2, lots=1, symbol=symbol)
    return ExpiryDayStraddle(lots=1, symbol=symbol)


STRUCTURE_MAP = {"straddle", "strangle1", "strangle2"}
DEFAULT_STRUCTURES = ["straddle", "strangle1"]


def _path(run_stamp: str, symbol: str, name: str) -> Path:
    return Path("reports/backtests/options") / f"{run_stamp}_dhan_{symbol.lower()}_{name}.csv"


def _make_config(
    *,
    entry_time: time,
    symbol: str,
    trail_trigger_pct: float | None,
    trail_stop_pct: float | None,
    target_profit_pct: float | None,
) -> BacktestConfig:
    return BacktestConfig(
        symbol=symbol,
        entry_time=entry_time,
        exit_time=time(15, 28),
        next_day_exit=False,
        stop_loss_pct=None,
        target_profit_pct=target_profit_pct,
        trail_trigger_pct=trail_trigger_pct,
        trail_stop_pct=trail_stop_pct,
        include_costs=True,
    )


def _run_variant(
    name: str,
    strategy: object,
    config: BacktestConfig,
    args: argparse.Namespace,
    data: object,
    run_stamp: str,
) -> tuple[str, dict, Path]:
    engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type="week")
    result = engine.run(strategy, from_date=args.from_date, to_date=args.to_date, data=data)
    output = _path(run_stamp, config.symbol, name)
    output.parent.mkdir(parents=True, exist_ok=True)
    trade_ledger_with_total(result.trade_ledger).to_csv(output, index=False)
    return name, result.summary, output


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _print_comparison(rows: list[tuple[str, dict]], group_by_entry: bool = True) -> None:
    header = (
        f"{'Variant':<32} {'Trades':>7} {'WinRate':>8} {'NetPnL':>12} "
        f"{'AvgPnL':>9} {'Sharpe':>8} {'PF':>6}"
    )
    sep = "-" * len(header)

    if group_by_entry:
        # Group rows by entry-time token (second underscore-segment)
        from collections import defaultdict
        groups: dict[str, list] = defaultdict(list)
        for name, summary in rows:
            parts = name.split("_")
            entry_key = parts[1] if len(parts) > 1 else "?"
            groups[entry_key].append((name, summary))

        print()
        for entry_key in sorted(groups):
            print(f"  Entry {entry_key}")
            print(sep)
            print(header)
            print(sep)
            for name, summary in groups[entry_key]:
                _print_row(name, summary)
            print()
    else:
        print()
        print(header)
        print(sep)
        for name, summary in rows:
            _print_row(name, summary)
        print()


def _print_row(name: str, summary: dict) -> None:
    trades = summary.get("trades", 0)
    win_rate = summary.get("win_rate", 0.0) * 100
    net_pnl = summary.get("net_pnl", 0.0)
    avg_pnl = summary.get("avg_trade_pnl", 0.0)
    sharpe = summary.get("sharpe", float("nan"))
    pf = summary.get("profit_factor", float("nan"))
    sharpe_str = f"{sharpe:.2f}" if sharpe == sharpe else "   nan"
    pf_str = f"{pf:.2f}" if pf == pf else "  nan"
    print(
        f"{name:<32} {trades:>7} {win_rate:>7.1f}% {net_pnl:>12,.0f} "
        f"{avg_pnl:>9,.0f} {sharpe_str:>8} {pf_str:>6}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run expiry-day straddle/strangle backtest across multiple entry times."
    )
    parser.add_argument("--dhan-root", default="data/processed/options/dhan")
    parser.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--run-stamp", help="Output prefix; defaults to YYYYMMDD_HHMMSS.")
    parser.add_argument(
        "--entry-times",
        default=",".join(DEFAULT_ENTRY_TIMES),
        help="Comma-separated HH:MM entry times to sweep (default: %(default)s).",
    )
    parser.add_argument(
        "--structures",
        default=DEFAULT_STRUCTURES,
        choices=sorted(STRUCTURE_MAP),
        nargs="+",
        help="Structures to include (default: straddle strangle1).",
    )
    args = parser.parse_args()

    entry_times = [_parse_time(t.strip()) for t in args.entry_times.split(",")]
    structures = args.structures

    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    data = load_dhan_data(args.dhan_root, "week", args.symbol)

    # Build cross-product: entry_time × structure × exit_config
    exit_configs = [
        # (label, trail_trigger_pct, trail_stop_pct, target_profit_pct)
        ("target2x", None,  None,  1.0),
        ("target3x", None,  None,  2.0),
        ("trail",    1.0,   0.25,  2.0),
    ]

    variants: list[tuple[str, object, BacktestConfig]] = []
    for entry_t in entry_times:
        hhmm = f"{entry_t.hour:02d}{entry_t.minute:02d}"
        for struct_name in structures:
            for exit_label, trig, stop, tgt in exit_configs:
                name = f"{struct_name}_{hhmm}_{exit_label}"
                strategy = _structure(struct_name, args.symbol)
                config = _make_config(
                    entry_time=entry_t,
                    symbol=args.symbol,
                    trail_trigger_pct=trig,
                    trail_stop_pct=stop,
                    target_profit_pct=tgt,
                )
                variants.append((name, strategy, config))

    total = len(variants)
    print(f"run_stamp : {run_stamp}")
    print(f"entry times: {[str(t) for t in entry_times]}")
    print(f"structures : {structures}")
    print(f"variants   : {total}")
    print()

    summary_pairs: list[tuple[str, dict]] = []
    for i, (name, strategy, config) in enumerate(variants, 1):
        variant_name, summary, output = _run_variant(name, strategy, config, args, data, run_stamp)
        print(
            f"  [{i:>3}/{total}] {variant_name:<36} "
            f"trades={summary.get('trades'):>4}  "
            f"win={summary.get('win_rate', 0)*100:>5.1f}%  "
            f"net={summary.get('net_pnl', 0):>10,.0f}"
        )
        summary_pairs.append((variant_name, summary))

    _print_comparison(summary_pairs, group_by_entry=True)

    summary_path = Path("reports/backtests/options") / f"{run_stamp}_summary.md"
    summary_path.write_text(batch_summary_md(summary_pairs, run_stamp), encoding="utf-8")
    print(f"summary -> {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

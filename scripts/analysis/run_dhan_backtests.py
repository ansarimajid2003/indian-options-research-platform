from __future__ import annotations

import argparse
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.dhan_loader import DhanBacktestEngine, load_dhan_data
from options_backtest.reports import batch_summary_md, trade_ledger_with_total
from options_backtest.schemas import BacktestConfig
from options_backtest.strategy import (
    ShortStraddle,
    ShortStrangle,
    ThreePMDirectional,
    ThreePMV2CallLevelStop,
    ThreePMV2Put,
)


def _path(run_stamp: str, symbol: str, name: str) -> Path:
    return Path("reports/backtests/options") / f"{run_stamp}_dhan_{symbol.lower()}_{name}.csv"


def _config(*, symbol: str, next_day: bool = False, no_costs: bool = False) -> BacktestConfig:
    if next_day:
        return BacktestConfig(
            symbol=symbol,
            include_costs=not no_costs,
            entry_time=time(15, 16),
            exit_time=time(9, 16),
            next_day_exit=True,
        )
    return BacktestConfig(symbol=symbol, include_costs=not no_costs)


def _short_premium_config(*, symbol: str, no_costs: bool = False) -> BacktestConfig:
    return BacktestConfig(symbol=symbol, include_costs=not no_costs, stop_loss_pct=None)


def _run_job(
    name: str,
    strategy: object,
    config: BacktestConfig,
    args: argparse.Namespace,
    data: object,
    run_stamp: str,
) -> tuple[str, dict, Path]:
    engine = DhanBacktestEngine(config, dhan_root=args.dhan_root, expiry_type=args.expiry_type)
    result = engine.run(strategy, from_date=args.from_date, to_date=args.to_date, data=data)
    output = _path(run_stamp, config.symbol, name)
    output.parent.mkdir(parents=True, exist_ok=True)
    trade_ledger_with_total(result.trade_ledger).to_csv(output, index=False)
    return name, result.summary, output


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the standard Dhan NIFTY backtest pack.")
    parser.add_argument("--dhan-root", default="data/processed/options/dhan")
    parser.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"])
    parser.add_argument("--expiry-type", choices=["week", "month"], default="week")
    parser.add_argument("--from-date")
    parser.add_argument("--to-date")
    parser.add_argument("--run-stamp", help="Output prefix, default YYYYMMDD_HHMMSS captured once per run.")
    parser.add_argument("--workers", type=int, help="Parallel strategy workers, default one per strategy.")
    args = parser.parse_args()

    run_stamp = args.run_stamp or datetime.now().strftime("%Y%m%d_%H%M%S")
    data = load_dhan_data(args.dhan_root, args.expiry_type, args.symbol)
    jobs = [
        ("short_straddle", ShortStraddle(), _short_premium_config(symbol=args.symbol)),
        ("short_strangle", ShortStrangle(), _short_premium_config(symbol=args.symbol)),
        ("three_pm_directional", ThreePMDirectional(), _config(symbol=args.symbol, next_day=True)),
        ("three_pm_v2_call", ThreePMV2CallLevelStop(), _config(symbol=args.symbol, next_day=True)),
        ("three_pm_v2_put", ThreePMV2Put(), _config(symbol=args.symbol, next_day=True)),
    ]

    workers = args.workers or len(jobs)
    if workers < 1:
        parser.error("--workers must be >= 1")

    print(f"run_stamp: {run_stamp}")
    results: list[tuple[str, dict, Path] | None] = [None] * len(jobs)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(_run_job, name, strategy, config, args, data, run_stamp): idx
            for idx, (name, strategy, config) in enumerate(jobs)
        }
        for future in as_completed(futures):
            idx = futures[future]
            results[idx] = future.result()

    summary_pairs: list[tuple[str, dict]] = []
    for result in results:
        if result is None:
            continue
        name, summary, output = result
        print(f"{name}: {summary} -> {output}")
        summary_pairs.append((name, summary))

    summary_path = Path("reports/backtests/options") / f"{run_stamp}_summary.md"
    summary_path.write_text(batch_summary_md(summary_pairs, run_stamp), encoding="utf-8")
    print(f"summary -> {summary_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

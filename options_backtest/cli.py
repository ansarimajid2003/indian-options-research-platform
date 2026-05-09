from __future__ import annotations

import argparse
from datetime import datetime, time
from pathlib import Path

from .data_store import normalize_shoonya_expiry
from .dhan_loader import DhanBacktestEngine
from .engine import BacktestEngine
from .reports import trade_ledger_with_total
from .schemas import BacktestConfig
from .strategy import IronCondor, ShortStraddle, ShortStrangle, ThreePMDirectional, ThreePMV2Put, ThreePMV2CallLevelStop
from .validation import audit_raw_shoonya


def _parse_time(s: str) -> time:
    h, m = s.split(":")
    return time(int(h), int(m))


def _default_output_path(vendor: str, strategy: str, *, no_costs: bool = False) -> Path:
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_strategy = strategy.replace("-", "_")
    suffix = "_nocosts" if no_costs else ""
    return Path("reports/backtests/options") / f"{stamp}_{vendor}_{safe_strategy}{suffix}.csv"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Nifty options backtest utilities")
    sub = parser.add_subparsers(dest="command", required=True)

    normalize = sub.add_parser("normalize")
    normalize.add_argument("--raw-root", default="data/raw/options/shoonya/nifty")
    normalize.add_argument("--output-root", default="data/processed/options/normalized")
    normalize.add_argument("--expiry", action="append")
    normalize.add_argument("--limit", type=int)

    audit = sub.add_parser("audit")
    audit.add_argument("--raw-root", default="data/raw/options/shoonya/nifty")

    # Legacy Shoonya backtest — kept for reference only
    run = sub.add_parser("run-backtest-shoonya")
    run.add_argument("--raw-root", default="data/raw/options/shoonya/nifty")
    run.add_argument("--strategy", choices=["short-straddle", "short-strangle", "iron-condor", "three-pm-directional", "three-pm-v2-put", "three-pm-v2-call-level-stop"], default="short-straddle")
    run.add_argument("--from-expiry")
    run.add_argument("--to-expiry")
    run.add_argument("--limit", type=int)
    run.add_argument("--no-costs", action="store_true")
    run.add_argument("--slippage", type=float, default=0.05)
    run.add_argument("--stop-loss-pct", type=float, default=0.5)
    run.add_argument("--target-profit-pct", type=float, default=0.5)
    run.add_argument("--next-day-exit", action="store_true")
    run.add_argument("--entry-time", default=None)
    run.add_argument("--exit-time", default=None)
    run.add_argument("--output")

    # Primary backtest command — Dhan 5-year data
    dhan = sub.add_parser("run-backtest")
    dhan.add_argument("--dhan-root", default="data/processed/options/dhan")
    dhan.add_argument("--symbol", default="NIFTY", choices=["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"])
    dhan.add_argument("--expiry-type", choices=["week", "month"], default="week")
    dhan.add_argument("--strategy", choices=["short-straddle", "short-strangle", "iron-condor", "three-pm-directional", "three-pm-v2-put", "three-pm-v2-call-level-stop"], default="short-straddle")
    dhan.add_argument("--from-date", help="Start date YYYY-MM-DD")
    dhan.add_argument("--to-date", help="End date YYYY-MM-DD")
    dhan.add_argument("--entry-time", default=None)
    dhan.add_argument("--exit-time", default=None)
    dhan.add_argument("--stop-loss-pct", type=float, default=0.5)
    dhan.add_argument("--target-profit-pct", type=float, default=0.5)
    dhan.add_argument("--next-day-exit", action="store_true")
    dhan.add_argument("--no-costs", action="store_true")
    dhan.add_argument("--spot-path", default="data/processed/spot/nifty50_1min_CANONICAL.csv", help="Canonical NIFTY 50 1-min spot CSV for 3PM signal detection")
    dhan.add_argument("--vix-path", help="India VIX CSV with timestamp/datetime and close columns")
    dhan.add_argument("--vix-min", type=float)
    dhan.add_argument("--vix-max", type=float)
    dhan.add_argument("--vix-missing-policy", choices=["skip", "allow"], default="skip")
    dhan.add_argument("--output")

    return parser


def _strategy(name: str):
    if name == "short-strangle":
        return ShortStrangle()
    if name == "iron-condor":
        return IronCondor()
    if name == "three-pm-directional":
        return ThreePMDirectional()
    if name == "three-pm-v2-put":
        return ThreePMV2Put()
    if name == "three-pm-v2-call-level-stop":
        return ThreePMV2CallLevelStop()
    return ShortStraddle()


def main() -> int:
    args = build_parser().parse_args()
    if args.command == "audit":
        print(audit_raw_shoonya(Path(args.raw_root)).to_string(index=False))
        return 0

    if args.command == "normalize":
        raw_root = Path(args.raw_root)
        output_root = Path(args.output_root)
        expiries = sorted(path for path in raw_root.iterdir() if path.is_dir() and path.name.startswith("20"))
        if args.expiry:
            wanted = set(args.expiry)
            expiries = [path for path in expiries if path.name in wanted]
        if args.limit:
            expiries = expiries[: args.limit]
        for expiry_dir in expiries:
            paths = normalize_shoonya_expiry(expiry_dir, output_root)
            print(f"{expiry_dir.name}: {paths.format} {paths.options_path}")
        return 0

    if args.command == "run-backtest-shoonya":
        next_day_exit = args.next_day_exit
        if args.strategy in ("three-pm-directional", "three-pm-v2-put", "three-pm-v2-call-level-stop"):
            entry_time = _parse_time(args.entry_time) if args.entry_time else time(15, 16)
            exit_time = _parse_time(args.exit_time) if args.exit_time else time(9, 16)
            next_day_exit = True
        else:
            entry_time = _parse_time(args.entry_time) if args.entry_time else time(9, 20)
            exit_time = _parse_time(args.exit_time) if args.exit_time else time(15, 20)
        config = BacktestConfig(
            raw_root=args.raw_root,
            include_costs=not args.no_costs,
            slippage_points=args.slippage,
            stop_loss_pct=args.stop_loss_pct,
            target_profit_pct=args.target_profit_pct,
            entry_time=entry_time,
            exit_time=exit_time,
            next_day_exit=next_day_exit,
            vix_path=args.vix_path,
            vix_min=args.vix_min,
            vix_max=args.vix_max,
            vix_missing_policy=args.vix_missing_policy,
        )
        result = BacktestEngine(config).run(_strategy(args.strategy), args.from_expiry, args.to_expiry, args.limit)
        output = Path(args.output) if args.output else _default_output_path("shoonya", args.strategy, no_costs=args.no_costs)
        output.parent.mkdir(parents=True, exist_ok=True)
        trade_ledger_with_total(result.trade_ledger).to_csv(output, index=False)
        print(result.summary)
        print(f"trade ledger: {output}")
        return 0

    if args.command in ("run-backtest", "run-dhan-backtest"):
        next_day_exit = args.next_day_exit
        if args.strategy in ("three-pm-directional", "three-pm-v2-put", "three-pm-v2-call-level-stop"):
            entry_time = _parse_time(args.entry_time) if args.entry_time else time(15, 16)
            exit_time = _parse_time(args.exit_time) if args.exit_time else time(9, 16)
            next_day_exit = True
        else:
            entry_time = _parse_time(args.entry_time) if args.entry_time else time(9, 20)
            exit_time = _parse_time(args.exit_time) if args.exit_time else time(15, 20)
        config = BacktestConfig(
            symbol=args.symbol,
            include_costs=not args.no_costs,
            stop_loss_pct=args.stop_loss_pct,
            target_profit_pct=args.target_profit_pct,
            entry_time=entry_time,
            exit_time=exit_time,
            next_day_exit=next_day_exit,
        )
        result = DhanBacktestEngine(
            config,
            dhan_root=args.dhan_root,
            expiry_type=args.expiry_type,
            spot_path=args.spot_path,
        ).run(
            _strategy(args.strategy),
            from_date=args.from_date,
            to_date=args.to_date,
        )
        output = Path(args.output) if args.output else _default_output_path("dhan", args.strategy, no_costs=args.no_costs)
        output.parent.mkdir(parents=True, exist_ok=True)
        trade_ledger_with_total(result.trade_ledger).to_csv(output, index=False)
        print(result.summary)
        print(f"trade ledger: {output}")
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())

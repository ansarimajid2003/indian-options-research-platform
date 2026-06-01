"""
Backfill the persistent account ledger by replaying all existing paper-trade
sessions in chronological order.

The account ledger (``account/account_ledger.jsonl``) carries a running balance
across every paper-trading session. This script (re)builds it from the durable
``paper_trades/{YYYYMMDD}.json`` files so a freshly deployed tracker starts with
the full history instead of only today.

``update_account_ledger`` is idempotent on ``session_date`` and always re-walks
the opening==prior-closing chain, so this script is safe to re-run. With
``--rebuild`` (default) it truncates the ledger first for a clean replay; pass
``--no-rebuild`` to incrementally fold in any missing sessions instead.

The canonical-start cutoff (``--start-date``, default 2026-06-01) is only
enforced on a full ``--rebuild``. ``--no-rebuild`` folds only sessions on/after
the cutoff into whatever ledger already exists, so any pre-cutoff rows from an
earlier build are left in place; use ``--rebuild`` to re-anchor the period.

Usage:
    python scripts/live/backfill_account_ledger.py
    python scripts/live/backfill_account_ledger.py --live-root data/live
    python scripts/live/backfill_account_ledger.py --no-rebuild
"""

from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

_repo_root = Path(__file__).resolve().parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from options_backtest.account_state import MarginModel, update_account_ledger
from options_backtest.dashboard_bridge import CANONICAL_START


def _session_dates(live_root: Path, start_date: date | None = None) -> list[date]:
    """Return sorted session dates that have a paper_trades JSON file.

    Excludes the ``*_signals.jsonl`` sidecar files; only the daily trade
    arrays (``YYYYMMDD.json``) seed account rows. An empty ``[]`` file still
    produces a flat (0-trade) row to keep the equity chain continuous.

    Sessions strictly before ``start_date`` are dropped — used to anchor the
    canonical paper-trading period (the engine changed substantially through
    the May-2026 refactor, so pre-refactor sessions are not canonical evidence).
    """
    trades_dir = live_root / "paper_trades"
    if not trades_dir.exists():
        return []
    dates: list[date] = []
    for path in trades_dir.glob("*.json"):
        stem = path.stem
        if not (len(stem) == 8 and stem.isdigit()):
            continue  # skip signals / non-date files
        sd = date(int(stem[:4]), int(stem[4:6]), int(stem[6:8]))
        if start_date is not None and sd < start_date:
            continue
        dates.append(sd)
    return sorted(dates)


def backfill(
    live_root: Path,
    margin_config: Path,
    rebuild: bool = True,
    start_date: date | None = None,
) -> list[dict]:
    margin_model = MarginModel.from_config(margin_config)
    account_dir = live_root / "account"
    ledger_path = account_dir / "account_ledger.jsonl"

    if rebuild and ledger_path.exists():
        ledger_path.unlink()

    rows: list[dict] = []
    for session_date in _session_dates(live_root, start_date=start_date):
        row = update_account_ledger(live_root, session_date, margin_model=margin_model)
        rows.append(row)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill the account ledger from paper_trades history")
    parser.add_argument("--live-root", default="data/live", help="Live data root (default: data/live)")
    parser.add_argument(
        "--margin-config",
        default=str(_repo_root / "configs" / "live" / "margin_model.json"),
        help="Path to margin_model.json",
    )
    parser.add_argument(
        "--no-rebuild",
        dest="rebuild",
        action="store_false",
        help="Fold into the existing ledger instead of truncating it first",
    )
    parser.add_argument(
        "--start-date",
        default=CANONICAL_START.isoformat(),
        help=(
            "Canonical paper-trading start (YYYY-MM-DD); sessions before this are "
            f"excluded. Default {CANONICAL_START.isoformat()}. Pass 'all' to include every session."
        ),
    )
    parser.set_defaults(rebuild=True)
    args = parser.parse_args()

    if args.start_date == "all":
        start_date = None
    else:
        start_date = date.fromisoformat(args.start_date)

    live_root = Path(args.live_root)
    rows = backfill(
        live_root, Path(args.margin_config), rebuild=args.rebuild, start_date=start_date
    )

    if not rows:
        print(f"No paper_trades sessions found under {live_root / 'paper_trades'}")
        return

    print(f"{'date':<12}{'trades':>7}{'net_pnl':>12}{'closing_bal':>14}{'dd%':>8}  breach")
    print("-" * 64)
    for r in rows:
        print(
            f"{r['session_date']:<12}{r['trades']:>7}{r['net_pnl']:>12.2f}"
            f"{r['closing_balance']:>14.2f}{r.get('drawdown_pct', 0.0):>8.2f}"
            f"  {'YES' if r.get('margin_breach') else ''}"
        )
    last = rows[-1]
    print("-" * 64)
    print(
        f"Sessions: {len(rows)}  |  Current balance: {last['closing_balance']:.2f}  |  "
        f"All-time net: {last['closing_balance'] - rows[0]['opening_balance']:.2f}"
    )


if __name__ == "__main__":
    main()

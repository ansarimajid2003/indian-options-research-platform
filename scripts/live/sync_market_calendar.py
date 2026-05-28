"""Refresh and inspect the live exchange-calendar cache."""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, datetime
from pathlib import Path

_repo_root = Path(__file__).parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from scripts.live.market_calendar import decision_payload, market_session_decision


def _parse_date(value: str | None) -> date:
    if not value:
        return date.today()
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync NSE/BSE live market calendar cache.")
    parser.add_argument(
        "--live-root",
        default=os.environ.get("LIVE_ROOT", "data/live"),
        help="Live artifact root. Defaults to $LIVE_ROOT or data/live.",
    )
    parser.add_argument("--date", help="Session date to evaluate, YYYY-MM-DD. Defaults to today.")
    parser.add_argument("--exchange", default="NSE", help="Exchange code, currently NSE.")
    parser.add_argument("--segment", default="FO", help="Exchange segment, e.g. FO or CM.")
    parser.add_argument("--timeout", type=float, default=10.0, help="Official API timeout in seconds.")
    parser.add_argument("--no-refresh", action="store_true", help="Read the existing cache only.")
    parser.add_argument(
        "--fail-closed",
        action="store_true",
        help="Return a closed decision when official refresh fails and no fresh cache exists.",
    )
    parser.add_argument(
        "--check-open",
        action="store_true",
        help="Exit 0 only when the evaluated session is open; useful for manual gates.",
    )
    args = parser.parse_args(argv)

    decision = market_session_decision(
        Path(args.live_root),
        _parse_date(args.date),
        exchange=args.exchange,
        segment=args.segment,
        refresh=not args.no_refresh,
        fail_closed=args.fail_closed,
        timeout=args.timeout,
    )
    print(json.dumps(decision_payload(decision), indent=2, sort_keys=True))
    if args.check_open and not decision.is_trading_day:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

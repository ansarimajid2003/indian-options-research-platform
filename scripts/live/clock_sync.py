"""
CLI wrapper for live-paper host clock preflight/remediation.

Usage:
    python scripts/live/clock_sync.py --check
    python scripts/live/clock_sync.py --remediate --wait --timeout 180
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

_repo_root = Path(__file__).parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from options_backtest.clock_sync import clock_sync_status, remediate_clock_sync


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check or repair host clock sync for live paper trading.")
    parser.add_argument("--check", action="store_true", help="Check clock health without remediation")
    parser.add_argument("--remediate", action="store_true", help="Restart systemd-timesyncd if clock health is bad")
    parser.add_argument("--wait", action="store_true", help="Wait until the clock is healthy or timeout expires")
    parser.add_argument("--max-offset", type=float, default=2.0, help="Maximum allowed absolute offset in seconds")
    parser.add_argument("--timeout", type=float, default=90.0, help="Wait/remediation timeout in seconds")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of plain text")
    args = parser.parse_args(argv)

    status = clock_sync_status(max_offset_seconds=args.max_offset)
    if status.healthy is not True and args.remediate:
        wait = args.timeout if args.wait else 5.0
        status = remediate_clock_sync(max_offset_seconds=args.max_offset, timeout_seconds=wait)

    payload = {
        "healthy": status.healthy,
        "reason": status.reason,
        "message": status.message,
        "offset_seconds": status.offset_seconds,
        "synchronized": status.synchronized,
    }
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(f"{status.reason}: {status.message}")

    if status.healthy is True:
        return 0
    if status.healthy is False:
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())

"""
Download Dhan rolling expired options data for NIFTY.

Dhan's /charts/rollingoption endpoint provides up to five years of minute-level
expired options data as rolling strikes such as ATM, ATM+1, and ATM-1. The
script writes one JSON response per request so failed or interrupted backfills
can be resumed without touching completed files.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "options" / "dhan" / "nifty"
DHAN_URL = "https://api.dhan.co/v2/charts/rollingoption"
MAX_DAYS_PER_CALL = 30
NIFTY_SECURITY_ID = 13
DEFAULT_REQUIRED_DATA = ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"]


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def default_from_date() -> date:
    return date.today() - timedelta(days=365 * 5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Backfill Dhan rolling expired NIFTY options data."
    )
    parser.add_argument("--from-date", default=default_from_date().isoformat())
    parser.add_argument("--to-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--security-id", type=int, default=NIFTY_SECURITY_ID)
    parser.add_argument("--exchange-segment", default="NSE_FNO", help="Exchange segment: NSE_FNO, BSE_FNO, etc.")
    parser.add_argument("--instrument", default="OPTIDX", help="Instrument type: OPTIDX, OPTSTK, etc.")
    parser.add_argument("--interval", default="1", help="Dhan interval: 1, 5, 15, 25, or 60.")
    parser.add_argument("--expiry-flag", choices=["WEEK", "MONTH"], default="WEEK")
    parser.add_argument("--expiry-code", type=int, default=1)
    parser.add_argument(
        "--strike-range",
        type=int,
        default=10,
        help="Download ATM plus/minus this many rolling strikes. Default: 10.",
    )
    parser.add_argument(
        "--option-type",
        choices=["CALL", "PUT"],
        action="append",
        help="Option side to fetch. Repeat for both. Default: CALL and PUT.",
    )
    parser.add_argument(
        "--sleep",
        type=float,
        default=0.35,
        help="Delay between API calls. Increase if rate-limited.",
    )
    parser.add_argument("--force", action="store_true", help="Redownload existing JSON files.")
    parser.add_argument(
        "--continue-on-empty-data",
        action="store_true",
        help="Write DH-905 no-data responses and continue instead of aborting the whole backfill.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned request count without calling the API.",
    )
    return parser.parse_args()


def rolling_strikes(width: int) -> list[str]:
    strikes = ["ATM"]
    for offset in range(1, width + 1):
        strikes.append(f"ATM+{offset}")
        strikes.append(f"ATM-{offset}")
    return strikes


def date_windows(start: date, end: date) -> list[tuple[date, date]]:
    windows: list[tuple[date, date]] = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + timedelta(days=MAX_DAYS_PER_CALL), end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def safe_strike_name(strike: str) -> str:
    return strike.replace("+", "p").replace("-", "m")


def output_path(
    output_dir: Path,
    expiry_flag: str,
    expiry_code: int,
    option_type: str,
    strike: str,
    start: date,
    end: date,
) -> Path:
    return (
        output_dir
        / expiry_flag.lower()
        / f"expiry_code_{expiry_code}"
        / option_type.lower()
        / safe_strike_name(strike)
        / f"{start.isoformat()}_{end.isoformat()}.json"
    )


def build_payload(
    args: argparse.Namespace,
    option_type: str,
    strike: str,
    start: date,
    end: date,
) -> dict[str, object]:
    return {
        "exchangeSegment": args.exchange_segment,
        "interval": args.interval,
        "securityId": args.security_id,
        "instrument": args.instrument,
        "expiryFlag": args.expiry_flag,
        "expiryCode": args.expiry_code,
        "strike": strike,
        "drvOptionType": option_type,
        "requiredData": DEFAULT_REQUIRED_DATA,
        "fromDate": start.isoformat(),
        "toDate": end.isoformat(),
    }


def get_access_token() -> str:
    token = os.environ.get("DHAN_ACCESS_TOKEN") or os.environ.get("DHAN_TOKEN")
    if not token:
        raise RuntimeError("Set DHAN_ACCESS_TOKEN before running this downloader.")
    return token


def post_json(payload: dict[str, object], access_token: str) -> dict[str, object]:
    request = Request(
        DHAN_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": access_token,
        },
        method="POST",
    )
    for attempt in range(5):
        try:
            with urlopen(request, timeout=60) as response:
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                wait = 2 ** attempt * 5  # 5, 10, 20, 40, 80 seconds
                print(f"429 rate-limited, waiting {wait}s (attempt {attempt+1}/5)...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("exceeded retry limit after repeated 429s")


def write_json(path: Path, payload: dict[str, object], response: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(".json.part")
    tmp_path.write_text(
        json.dumps({"request": payload, "response": response}, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    tmp_path.replace(path)


def main() -> int:
    args = parse_args()
    start = parse_date(args.from_date)
    end = parse_date(args.to_date)
    if start >= end:
        raise ValueError("--from-date must be earlier than --to-date")

    option_types = args.option_type or ["CALL", "PUT"]
    strikes = rolling_strikes(args.strike_range)
    windows = date_windows(start, end)
    total = len(option_types) * len(strikes) * len(windows)
    print(
        f"planned {total} calls: {len(windows)} windows, "
        f"{len(strikes)} strikes, {len(option_types)} option types"
    )
    if args.dry_run:
        return 0

    access_token = get_access_token()
    completed = 0
    skipped = 0

    for window_start, window_end in windows:
        for strike in strikes:
            for option_type in option_types:
                path = output_path(
                    args.output_dir,
                    args.expiry_flag,
                    args.expiry_code,
                    option_type,
                    strike,
                    window_start,
                    window_end,
                )
                if path.exists() and not args.force:
                    skipped += 1
                    continue

                payload = build_payload(args, option_type, strike, window_start, window_end)
                try:
                    response = post_json(payload, access_token)
                    write_json(path, payload, response)
                    completed += 1
                    print(f"saved {option_type} {strike} {window_start} {window_end}")
                except HTTPError as exc:
                    body = exc.read().decode("utf-8", errors="replace")
                    if args.continue_on_empty_data and exc.code == 400 and "DH-905" in body:
                        try:
                            response = json.loads(body)
                        except json.JSONDecodeError:
                            response = {"error": body}
                        write_json(path, payload, response)
                        completed += 1
                        print(f"saved empty {option_type} {strike} {window_start} {window_end}: DH-905")
                        if args.sleep > 0:
                            time.sleep(args.sleep)
                        continue
                    print(f"HTTP {exc.code} for {option_type} {strike} {window_start}: {body}")
                    return 1
                except (URLError, TimeoutError) as exc:
                    print(f"network error for {option_type} {strike} {window_start}: {exc}")
                    return 1

                if args.sleep > 0:
                    time.sleep(args.sleep)

    print(f"done saved={completed} skipped={skipped}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

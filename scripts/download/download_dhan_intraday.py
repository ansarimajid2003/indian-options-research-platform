"""
Download Dhan intraday (1-min) and daily historical data for index spot and VIX.

Endpoint: POST /charts/intraday  (max 90-day window per call)
          POST /charts/historical (daily, no window limit)

Output layout:
  data/raw/spot/intraday/{symbol}/{YYYY-MM-DD}_{YYYY-MM-DD}.json
  data/raw/spot/daily/{symbol}.json
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
INTRADAY_URL = "https://api.dhan.co/v2/charts/intraday"
DAILY_URL = "https://api.dhan.co/v2/charts/historical"
MAX_DAYS_INTRADAY = 90

INSTRUMENTS = {
    "NIFTY":      {"securityId": "13",  "exchangeSegment": "IDX_I", "instrument": "INDEX"},
    "BANKNIFTY":  {"securityId": "25",  "exchangeSegment": "IDX_I", "instrument": "INDEX"},
    "FINNIFTY":   {"securityId": "27",  "exchangeSegment": "IDX_I", "instrument": "INDEX"},
    "MIDCPNIFTY": {"securityId": "442", "exchangeSegment": "IDX_I", "instrument": "INDEX"},
    "INDIAVIX":   {"securityId": "21",  "exchangeSegment": "IDX_I", "instrument": "INDEX"},
}


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def default_from_date() -> date:
    return date.today() - timedelta(days=365 * 5)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download Dhan intraday/daily spot data.")
    parser.add_argument("--symbols", nargs="+", default=list(INSTRUMENTS.keys()),
                        help="Symbols to download. Default: all.")
    parser.add_argument("--from-date", default=default_from_date().isoformat())
    parser.add_argument("--to-date", default=date.today().isoformat())
    parser.add_argument("--interval", default="1", choices=["1", "5", "15", "25", "60"])
    parser.add_argument("--mode", choices=["intraday", "daily", "both"], default="both")
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "data" / "raw" / "spot")
    parser.add_argument("--sleep", type=float, default=0.25)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def get_access_token() -> str:
    token = os.environ.get("DHAN_ACCESS_TOKEN") or os.environ.get("DHAN_TOKEN")
    if not token:
        raise RuntimeError("Set DHAN_ACCESS_TOKEN before running.")
    return token


def post_json(url: str, payload: dict, token: str) -> dict:
    req = Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "access-token": token,
        },
        method="POST",
    )
    for attempt in range(5):
        try:
            with urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except HTTPError as exc:
            if exc.code == 429:
                wait = 2 ** attempt * 5
                print(f"429 rate-limited, waiting {wait}s (attempt {attempt+1}/5)...")
                time.sleep(wait)
            else:
                raise
    raise RuntimeError("exceeded retry limit after repeated 429s")


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.part")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def date_windows(start: date, end: date, max_days: int) -> list[tuple[date, date]]:
    windows = []
    cursor = start
    while cursor < end:
        window_end = min(cursor + timedelta(days=max_days), end)
        windows.append((cursor, window_end))
        cursor = window_end
    return windows


def download_intraday(symbol: str, meta: dict, args, token: str) -> int:
    start = parse_date(args.from_date)
    end = parse_date(args.to_date)
    windows = date_windows(start, end, MAX_DAYS_INTRADAY)
    saved = skipped = 0
    for ws, we in windows:
        path = args.output_dir / "intraday" / symbol / f"{ws.isoformat()}_{we.isoformat()}.json"
        if path.exists() and not args.force:
            skipped += 1
            continue
        if args.dry_run:
            print(f"  [dry] intraday {symbol} {ws} to {we}")
            continue
        payload = {
            "securityId": meta["securityId"],
            "exchangeSegment": meta["exchangeSegment"],
            "instrument": meta["instrument"],
            "interval": args.interval,
            "oi": False,
            "fromDate": f"{ws.isoformat()} 09:00:00",
            "toDate": f"{we.isoformat()} 15:30:00",
        }
        try:
            resp = post_json(INTRADAY_URL, payload, token)
            ts = resp.get("timestamp", [])
            write_json(path, {"request": payload, "response": resp})
            saved += 1
            print(f"  saved intraday {symbol} {ws} to {we}  ({len(ts)} candles)")
        except HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            print(f"  HTTP {e.code} intraday {symbol} {ws}: {body}")
            return -1
        except (URLError, TimeoutError) as e:
            print(f"  network error {symbol} {ws}: {e}")
            return -1
        if args.sleep > 0:
            time.sleep(args.sleep)
    return saved


def download_daily(symbol: str, meta: dict, args, token: str) -> int:
    path = args.output_dir / "daily" / f"{symbol}.json"
    if path.exists() and not args.force:
        print(f"  skipped daily {symbol} (exists)")
        return 0
    if args.dry_run:
        print(f"  [dry] daily {symbol}")
        return 0
    payload = {
        "securityId": meta["securityId"],
        "exchangeSegment": meta["exchangeSegment"],
        "instrument": meta["instrument"],
        "expiryCode": 0,
        "oi": False,
        "fromDate": args.from_date,
        "toDate": args.to_date,
    }
    try:
        resp = post_json(DAILY_URL, payload, token)
        ts = resp.get("timestamp", [])
        write_json(path, {"request": payload, "response": resp})
        print(f"  saved daily {symbol} ({len(ts)} candles)")
        return 1
    except HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  HTTP {e.code} daily {symbol}: {body}")
        return -1
    except (URLError, TimeoutError) as e:
        print(f"  network error daily {symbol}: {e}")
        return -1


def main() -> int:
    args = parse_args()
    token = get_access_token() if not args.dry_run else "dry"
    symbols = [s.upper() for s in args.symbols]
    unknown = [s for s in symbols if s not in INSTRUMENTS]
    if unknown:
        print(f"Unknown symbols: {unknown}. Available: {list(INSTRUMENTS.keys())}")
        return 1

    for symbol in symbols:
        meta = INSTRUMENTS[symbol]
        print(f"\n=== {symbol} (secId={meta['securityId']}) ===")
        if args.mode in ("intraday", "both"):
            result = download_intraday(symbol, meta, args, token)
            if result == -1:
                return 1
        if args.mode in ("daily", "both"):
            result = download_daily(symbol, meta, args, token)
            if result == -1:
                return 1
            if args.sleep > 0 and not args.dry_run:
                time.sleep(args.sleep)

    print("\ndone")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Scrape NSE NIFTY option chain for live bid/ask, OI, IV, and volume.

NSE serves the option chain from an undocumented but stable JSON endpoint.
The endpoint requires a session cookie obtained by hitting the main page first.
Polls every INTERVAL minutes and appends one NDJSON record per run.

Output:
  data/option_chain/{YYYYMMDD}.ndjson   — one JSON object per scrape per day

Each record contains:
  timestamp, india_vix, underlying_value, and a list of strikes with:
    strikePrice, expiryDate, CE/PE: {openInterest, changeinOpenInterest,
    totalTradedVolume, impliedVolatility, lastPrice, change,
    bidQty, bidPrice, askQty, askPrice, underlyingValue}

Usage:
  python scrape_nse_option_chain.py                   # one-shot scrape
  python scrape_nse_option_chain.py --loop --interval 15  # poll every 15 min (market hours)
  python scrape_nse_option_chain.py --dry-run          # fetch and print, don't save

Notes:
- NSE blocks non-browser requests. The session must be established first.
- If you get repeated 401/403, wait ~60 seconds and re-run; NSE throttles aggressively.
- For intraday live bid/ask series, run via Windows Task Scheduler at 15-min intervals
  between 09:15 and 15:30 IST on market days.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
DEFAULT_OUTPUT_DIR = ROOT / "data" / "option_chain"

NSE_MAIN = "https://www.nseindia.com"
NSE_OPTION_CHAIN = "https://www.nseindia.com/api/option-chain-indices?symbol=NIFTY"
NSE_VIX = "https://www.nseindia.com/api/allIndices"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/option-chain",
    "X-Requested-With": "XMLHttpRequest",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Scrape NSE NIFTY option chain bid/ask.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--interval",
        type=int,
        default=15,
        help="Poll interval in minutes when --loop is set.",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        help="Keep polling until manually stopped (use during market hours).",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print JSON to stdout, don't save.")
    return parser.parse_args()


def establish_session() -> str:
    """
    Hit the NSE main page to obtain a session cookie.
    Returns the Set-Cookie string to use in subsequent API requests.
    """
    req = Request(NSE_MAIN, headers=HEADERS)
    with urlopen(req, timeout=20) as resp:
        raw_cookies = resp.headers.get("Set-Cookie", "")
    if not raw_cookies:
        raise RuntimeError("NSE returned no cookies — site may be down or blocking.")
    # Extract only the key=value pairs (strip Path/Domain/SameSite attributes)
    parts = [c.split(";")[0].strip() for c in raw_cookies.split(",") if "=" in c.split(";")[0]]
    return "; ".join(parts)


def fetch_json(url: str, cookie: str) -> dict:
    headers = {**HEADERS, "Cookie": cookie}
    req = Request(url, headers=headers)
    with urlopen(req, timeout=30) as resp:
        raw = resp.read()
    return json.loads(raw)


def extract_record(chain_json: dict, timestamp: str) -> dict:
    """Flatten the NSE option chain response into a compact record."""
    filtered = chain_json.get("filtered", {})
    underlying = filtered.get("underlyingValue", None)
    india_vix = chain_json.get("records", {}).get("vixClose", None)

    strikes = []
    for row in filtered.get("data", []):
        entry: dict = {
            "strikePrice": row.get("strikePrice"),
            "expiryDate": row.get("expiryDate"),
        }
        for side in ("CE", "PE"):
            leg = row.get(side)
            if leg:
                entry[side] = {
                    "openInterest": leg.get("openInterest"),
                    "changeinOpenInterest": leg.get("changeinOpenInterest"),
                    "totalTradedVolume": leg.get("totalTradedVolume"),
                    "impliedVolatility": leg.get("impliedVolatility"),
                    "lastPrice": leg.get("lastPrice"),
                    "change": leg.get("change"),
                    "bidQty": leg.get("bidQty"),
                    "bidPrice": leg.get("bidprice"),
                    "askQty": leg.get("askQty"),
                    "askPrice": leg.get("askPrice"),
                }
        strikes.append(entry)

    return {
        "timestamp": timestamp,
        "underlyingValue": underlying,
        "indiaVix": india_vix,
        "strikes": strikes,
    }


def save_record(record: dict, output_dir: Path) -> Path:
    today = date.today().strftime("%Y%m%d")
    out = output_dir / f"{today}.ndjson"
    output_dir.mkdir(parents=True, exist_ok=True)
    with out.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, separators=(",", ":")) + "\n")
    return out


def scrape_once(output_dir: Path, dry_run: bool) -> None:
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print(f"[{timestamp}] Establishing NSE session ...", end=" ", flush=True)
    cookie = establish_session()
    time.sleep(1.5)  # NSE needs a brief pause between session and API call

    print("fetching option chain ...", end=" ", flush=True)
    chain_json = fetch_json(NSE_OPTION_CHAIN, cookie)

    record = extract_record(chain_json, timestamp)
    n_strikes = len(record["strikes"])
    vix = record.get("indiaVix")
    spot = record.get("underlyingValue")
    print(f"ok  spot={spot}  vix={vix}  strikes={n_strikes}")

    if dry_run:
        print(json.dumps(record, indent=2)[:2000])
        return

    out = save_record(record, output_dir)
    print(f"  appended to {out}")


def main() -> int:
    args = parse_args()

    if args.loop:
        print(f"Polling NSE option chain every {args.interval} min. Ctrl+C to stop.")
        while True:
            try:
                scrape_once(args.output_dir, args.dry_run)
            except (HTTPError, URLError, RuntimeError) as exc:
                print(f"  WARNING: {exc} — will retry next interval")
            except KeyboardInterrupt:
                print("\nStopped.")
                return 0
            time.sleep(args.interval * 60)
    else:
        try:
            scrape_once(args.output_dir, args.dry_run)
        except (HTTPError, URLError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())

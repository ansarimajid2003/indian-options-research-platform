"""
Download NSE F&O Bhavcopy files (2001-present) for free.

NSE publishes daily F&O Bhavcopy files containing EOD OHLC, LTP, volume,
OI, and settlement price for every listed option and futures contract.

URL pattern (post-July 2024 format, CM-UDiFF zip):
  https://nsearchives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_{DDMMYYYY}_F_0000.csv.zip

Pre-July 2024 (legacy plain CSV zip, still works for older dates):
  https://nsearchives.nseindia.com/content/historical/DERIVATIVES/{YYYY}/{MON}/fo{DD}{MON}{YYYY}bhav.csv.zip

Output layout:
  data/bhavcopy/fo/{YYYY}/{YYYYMMDD}.csv   (extracted, one file per trading day)

Usage:
  python download_nse_bhavcopy.py                          # last 30 days
  python download_nse_bhavcopy.py --from-date 2015-01-01  # full backfill
  python download_nse_bhavcopy.py --from-date 2020-01-01 --to-date 2020-12-31
  python download_nse_bhavcopy.py --dry-run                # count files, no download
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import time
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).parent
DEFAULT_OUTPUT_DIR = ROOT / "data" / "bhavcopy" / "fo"

# NSE requires a browser-like User-Agent; requests without it get 403.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

# New format active from ~July 2024 (NSE Circular 62424).
NEW_FORMAT_CUTOFF = date(2024, 7, 1)


def parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Download NSE F&O Bhavcopy EOD files.")
    parser.add_argument("--from-date", default=(date.today() - timedelta(days=30)).isoformat())
    parser.add_argument("--to-date", default=date.today().isoformat())
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument(
        "--sleep",
        type=float,
        default=1.5,
        help="Delay between requests (seconds). NSE rate-limits aggressive crawlers.",
    )
    parser.add_argument("--force", action="store_true", help="Re-download existing files.")
    parser.add_argument("--dry-run", action="store_true", help="Print URLs without downloading.")
    return parser.parse_args()


def trading_days(start: date, end: date) -> list[date]:
    """Return weekdays between start and end inclusive (NSE closed Sat/Sun at minimum)."""
    days: list[date] = []
    cursor = start
    while cursor <= end:
        if cursor.weekday() < 5:  # Mon–Fri only; holidays will 404 and be skipped
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def new_format_url(d: date) -> str:
    """CM-UDiFF format (July 2024 onward). NSE uses YYYYMMDD in this path."""
    yyyymmdd = d.strftime("%Y%m%d")
    return (
        f"https://nsearchives.nseindia.com/content/fo/"
        f"BhavCopy_NSE_FO_0_0_0_{yyyymmdd}_F_0000.csv.zip"
    )


def legacy_url(d: date) -> str:
    """Legacy plain-CSV zip (pre-July 2024)."""
    mon = d.strftime("%b").upper()
    dd = d.strftime("%d")
    yyyy = d.strftime("%Y")
    return (
        f"https://nsearchives.nseindia.com/content/historical/DERIVATIVES/"
        f"{yyyy}/{mon}/fo{dd}{mon}{yyyy}bhav.csv.zip"
    )


def output_path(output_dir: Path, d: date) -> Path:
    return output_dir / d.strftime("%Y") / f"{d.strftime('%Y%m%d')}.csv"


def fetch_zip(url: str) -> bytes | None:
    """Fetch a URL and return raw bytes, or None on 404."""
    req = Request(url, headers=HEADERS)
    try:
        with urlopen(req, timeout=30) as resp:
            return resp.read()
    except HTTPError as exc:
        if exc.code == 404:
            return None
        raise


def extract_csv_from_zip(raw_bytes: bytes) -> str:
    """Extract the first CSV file from a zip archive."""
    with zipfile.ZipFile(io.BytesIO(raw_bytes)) as zf:
        csv_names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_names:
            raise ValueError("No CSV found inside zip")
        return zf.read(csv_names[0]).decode("utf-8", errors="replace")


def filter_nifty_options(csv_text: str) -> str:
    """
    Keep only rows where the underlying is NIFTY (not BANKNIFTY, FINNIFTY, etc.)
    and the instrument type is OPTIDX.
    Returns a filtered CSV string (header + matching rows).
    """
    reader = csv.DictReader(io.StringIO(csv_text))
    if reader.fieldnames is None:
        return csv_text  # Can't parse — return as-is

    rows = []
    for row in reader:
        symbol = row.get("SYMBOL", row.get("symbol", "")).strip()
        instrument = row.get("INSTRUMENT", row.get("instrument", "")).strip()
        # New format uses different column names; handle both
        if not symbol:
            symbol = row.get("Symbol", "").strip()
        if not instrument:
            instrument = row.get("Instrument", "").strip()
        if symbol == "NIFTY" and instrument in ("OPTIDX", "OPTSTK"):
            rows.append(row)

    if not rows:
        return csv_text  # No NIFTY rows found — return full file rather than empty

    out = io.StringIO()
    writer = csv.DictWriter(out, fieldnames=reader.fieldnames)
    writer.writeheader()
    writer.writerows(rows)
    return out.getvalue()


def download_day(d: date, output_dir: Path, force: bool) -> str:
    """Download bhavcopy for one trading day. Returns status string."""
    out = output_path(output_dir, d)
    if out.exists() and not force:
        return "skip"

    url = new_format_url(d) if d >= NEW_FORMAT_CUTOFF else legacy_url(d)
    raw = fetch_zip(url)
    if raw is None:
        # Try the other format as fallback (NSE transition dates are fuzzy)
        alt_url = legacy_url(d) if d >= NEW_FORMAT_CUTOFF else new_format_url(d)
        raw = fetch_zip(alt_url)
        if raw is None:
            return "404"  # Holiday or non-trading day

    csv_text = extract_csv_from_zip(raw)
    filtered = filter_nifty_options(csv_text)

    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".csv.part")
    tmp.write_text(filtered, encoding="utf-8")
    tmp.replace(out)
    return "ok"


def main() -> int:
    args = parse_args()
    start = parse_date(args.from_date)
    end = parse_date(args.to_date)
    if start > end:
        print("--from-date must not be after --to-date", file=sys.stderr)
        return 1

    days = trading_days(start, end)
    print(f"Candidates: {len(days)} trading days from {start} to {end}")

    if args.dry_run:
        for d in days[:5]:
            print(f"  {d}: {new_format_url(d) if d >= NEW_FORMAT_CUTOFF else legacy_url(d)}")
        if len(days) > 5:
            print(f"  ... ({len(days) - 5} more)")
        return 0

    ok = skipped = missing = errors = 0
    for d in days:
        try:
            status = download_day(d, args.output_dir, args.force)
        except Exception as exc:
            print(f"  ERROR {d}: {exc}")
            errors += 1
            continue

        if status == "ok":
            print(f"  saved {d}")
            ok += 1
        elif status == "skip":
            skipped += 1
        else:
            missing += 1  # holiday / 404

        if args.sleep > 0:
            time.sleep(args.sleep)

    print(f"\ndone  saved={ok}  skipped={skipped}  holidays/404={missing}  errors={errors}")
    return 0 if errors == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

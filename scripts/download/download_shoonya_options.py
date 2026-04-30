"""
Download public Shoonya Nifty expired options ZIPs from a scraped manifest.

The manifest is produced from the Shoonya/Dropbox Firecrawl scrape and contains
one Dropbox URL per line. This script downloads selected expiry ZIPs and can
extract them with the system tar command, which handles the ZIP compression used
by the public files better than PowerShell Expand-Archive.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path
from urllib.error import URLError
from urllib.request import urlretrieve


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "data" / "raw" / "options" / "shoonya" / "nifty" / "zip_links.txt"
DEFAULT_OUTPUT_DIR = ROOT / "data" / "raw" / "options" / "shoonya" / "nifty"
DATE_RE = re.compile(r"/(?P<date>20\d{6})\.zip\?")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Download Shoonya public Nifty expired options ZIP files."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help=f"Path to zip_links.txt. Default: {DEFAULT_MANIFEST}",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=DEFAULT_OUTPUT_DIR,
        help=f"Directory for ZIPs and extracted folders. Default: {DEFAULT_OUTPUT_DIR}",
    )
    parser.add_argument(
        "--date",
        action="append",
        help="Expiry date to download as YYYYMMDD. Repeat for multiple dates.",
    )
    parser.add_argument(
        "--from-date",
        help="First expiry date to include as YYYYMMDD.",
    )
    parser.add_argument(
        "--to-date",
        help="Last expiry date to include as YYYYMMDD.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=1,
        help="Maximum ZIPs to download after filters. Default: 1.",
    )
    parser.add_argument(
        "--extract",
        action="store_true",
        help="Extract each ZIP into a same-named folder after downloading.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redownload existing ZIPs.",
    )
    return parser.parse_args()


def load_manifest(path: Path) -> list[tuple[str, str]]:
    if not path.exists():
        raise FileNotFoundError(f"Manifest not found: {path}")

    rows: list[tuple[str, str]] = []
    for raw_url in path.read_text(encoding="ascii").splitlines():
        url = raw_url.strip()
        if not url:
            continue
        match = DATE_RE.search(url)
        if not match:
            continue
        rows.append((match.group("date"), url))

    return sorted(set(rows))


def filter_rows(rows: list[tuple[str, str]], args: argparse.Namespace) -> list[tuple[str, str]]:
    selected = rows
    if args.date:
        wanted = set(args.date)
        selected = [row for row in selected if row[0] in wanted]
    if args.from_date:
        selected = [row for row in selected if row[0] >= args.from_date]
    if args.to_date:
        selected = [row for row in selected if row[0] <= args.to_date]
    if args.limit and args.limit > 0:
        selected = selected[: args.limit]
    return selected


def direct_download_url(url: str) -> str:
    return url.replace("&dl=0", "&dl=1")


def download_one(date: str, url: str, output_dir: Path, force: bool) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{date}.zip"
    partial = output_dir / f"{date}.zip.part"

    if destination.exists() and not force:
        print(f"exists {destination}")
        return destination

    if partial.exists():
        partial.unlink()

    print(f"download {date} -> {destination}")
    try:
        urlretrieve(direct_download_url(url), partial)
    except URLError as exc:
        if partial.exists():
            partial.unlink()
        raise RuntimeError(f"download failed for {date}: {exc}") from exc

    partial.replace(destination)
    return destination


def extract_zip(zip_path: Path) -> None:
    target = zip_path.with_suffix("")
    target.mkdir(parents=True, exist_ok=True)

    tar = shutil.which("tar")
    if not tar:
        raise RuntimeError("tar command not found; cannot extract this ZIP safely")

    print(f"extract {zip_path.name} -> {target}")
    subprocess.run([tar, "-xf", str(zip_path), "-C", str(target)], check=True)


def main() -> int:
    args = parse_args()
    rows = filter_rows(load_manifest(args.manifest), args)
    if not rows:
        print("No matching ZIP URLs found.")
        return 1

    for date, url in rows:
        zip_path = download_one(date, url, args.output_dir, args.force)
        if args.extract:
            extract_zip(zip_path)

    print(f"done {len(rows)} file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

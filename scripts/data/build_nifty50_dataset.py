"""
Compatibility wrapper for rebuilding canonical NIFTY 50 data.

The raw market archive under data/raw/market_archive is now treated as the
canonical historical source. Running this script delegates to
clean_archive_data.py, which cleans archive files and writes
data/processed/spot/nifty50_1min_CANONICAL.csv.
"""

from __future__ import annotations

import argparse

from clean_archive_data import clean_archive


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Rebuild canonical NIFTY 50 data.")
    parser.add_argument(
        "--no-recent",
        action="store_true",
        help="Do not append rows from data/raw/spot/nifty50_1m_recent_7days.csv after the archive end.",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    clean_archive(include_recent=not args.no_recent)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

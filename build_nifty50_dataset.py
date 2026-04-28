"""
Compatibility wrapper for rebuilding canonical NIFTY 50 data.

The archive folder is now treated as the canonical historical source. Running
this script delegates to clean_archive_data.py, which cleans archive files and
writes nifty50_1min_CANONICAL.csv.
"""

from clean_archive_data import clean_archive


if __name__ == "__main__":
    clean_archive(include_recent=True)

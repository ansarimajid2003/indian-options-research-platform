"""
data_loader.py — single gateway for all market data access
===========================================================

PURPOSE
-------
Enforces a hard in-sample / out-of-sample split.  No script should ever
import the cleaned_archive CSVs directly.  All data access must go through
the functions in this module.

PARTITION
---------
  In-sample (IS)  : 2015-01-09  →  2024-04-07  (inclusive)
  Out-of-sample   : 2024-04-08  →  present      (LOCKED — do not touch)

The OOS boundary is defined once here.  Any accidental attempt to load
OOS data raises an explicit error.  This module is the ONLY file that
knows where the raw CSVs live; every analysis script receives a DataFrame
from here and never touches the archive directly.

USAGE
-----
    from data_loader import load_nifty_15min, load_vix_15min, IS_END, OOS_START

    nifty = load_nifty_15min()          # IS only
    vix   = load_vix_15min()            # IS only

    # peek at the OOS range (read-only, for final eval only)
    nifty_oos = load_nifty_15min(split="oos")   # raises if OOS_UNLOCKED != True
"""

from __future__ import annotations

import os
from pathlib import Path
import pandas as pd

# ── partition boundary ────────────────────────────────────────────────────────
IS_END    = pd.Timestamp("2024-04-07")   # last date in the IS set
OOS_START = pd.Timestamp("2024-04-08")   # first date in the OOS set (LOCKED)

IS_LABEL  = f"2015-01-09 to {IS_END.date()}"
OOS_LABEL = f"{OOS_START.date()} to present"

# ── file registry ─────────────────────────────────────────────────────────────
_ROOT = Path(__file__).parent / "cleaned_archive"

_FILES: dict[str, dict[str, Path]] = {
    "nifty50": {
        "15min": _ROOT / "NIFTY 50_15minute.csv",
        "5min":  _ROOT / "NIFTY 50_5minute.csv",
        "day":   _ROOT / "NIFTY 50_day.csv",
    },
    "vix": {
        "15min": _ROOT / "INDIA VIX_15minute.csv",
        "5min":  _ROOT / "INDIA VIX_5minute.csv",
        "day":   _ROOT / "INDIA VIX_day.csv",
    },
    "banknifty": {
        "15min": _ROOT / "NIFTY BANK_15minute.csv",
        "day":   _ROOT / "NIFTY BANK_day.csv",
    },
}

# ── OOS lock ──────────────────────────────────────────────────────────────────
# To load OOS data you must set the environment variable:
#   OOS_UNLOCKED=1
# This is intentionally inconvenient so it never happens by accident.
_OOS_UNLOCK_VAR = "OOS_UNLOCKED"


def _oos_is_unlocked() -> bool:
    return os.environ.get(_OOS_UNLOCK_VAR, "").strip() in ("1", "true", "yes")


def _assert_oos_unlocked() -> None:
    if not _oos_is_unlocked():
        raise PermissionError(
            "\n"
            "=" * 63 + "\n"
            "  OOS DATA IS LOCKED\n"
            f"  Period: {OOS_LABEL}\n"
            "  This data is reserved for final out-of-sample validation.\n"
            "  Do NOT load it during strategy development or parameter search.\n"
            "\n"
            "  If you genuinely need it for final evaluation, set:\n"
            f"      {_OOS_UNLOCK_VAR}=1  (environment variable)\n"
            + "=" * 63
        )


# ── core loader ───────────────────────────────────────────────────────────────
def _load(
    instrument: str,
    freq: str,
    split: str = "is",
) -> pd.DataFrame:
    """
    Parameters
    ----------
    instrument : "nifty50" | "vix" | "banknifty"
    freq       : "15min" | "5min" | "day"
    split      : "is"   — in-sample only (default, always safe)
                 "oos"  — OOS only (requires OOS_UNLOCKED=1)
                 "all"  — full history (requires OOS_UNLOCKED=1)
    """
    if split in ("oos", "all"):
        _assert_oos_unlocked()

    path = _FILES[instrument][freq]
    if not path.exists():
        raise FileNotFoundError(f"Data file not found: {path}")

    df = pd.read_csv(path, parse_dates=["datetime"]).sort_values("datetime")
    df["date"] = df["datetime"].dt.date

    if split == "is":
        df = df[df["datetime"] <= IS_END.replace(hour=23, minute=59)]
    elif split == "oos":
        df = df[df["datetime"] >= OOS_START]
    # split == "all" → no filter

    if df.empty:
        raise ValueError(
            f"No data returned for instrument={instrument!r}, freq={freq!r}, split={split!r}."
        )

    df = df.reset_index(drop=True)
    return df


# ── public API ────────────────────────────────────────────────────────────────

def load_nifty_15min(split: str = "is") -> pd.DataFrame:
    """15-minute NIFTY 50 bars. Default: IS only."""
    return _load("nifty50", "15min", split)


def load_nifty_5min(split: str = "is") -> pd.DataFrame:
    """5-minute NIFTY 50 bars. Default: IS only."""
    return _load("nifty50", "5min", split)


def load_nifty_day(split: str = "is") -> pd.DataFrame:
    """Daily NIFTY 50 bars. Default: IS only."""
    return _load("nifty50", "day", split)


def load_vix_15min(split: str = "is") -> pd.DataFrame:
    """15-minute India VIX bars. Default: IS only."""
    return _load("vix", "15min", split)


def load_vix_day(split: str = "is") -> pd.DataFrame:
    """Daily India VIX bars. Default: IS only."""
    return _load("vix", "day", split)


def load_banknifty_15min(split: str = "is") -> pd.DataFrame:
    """15-minute NIFTY Bank bars. Default: IS only."""
    return _load("banknifty", "15min", split)


# ── convenience: IS summary ───────────────────────────────────────────────────
def partition_info() -> None:
    """Print a summary of the data partition."""
    nifty_is = load_nifty_15min("is")
    dates_is  = pd.to_datetime(nifty_is["datetime"]).dt.date
    n_days_is = nifty_is.groupby("date").ngroups

    sep = "-" * 59
    print(sep)
    print("  DATA PARTITION SUMMARY")
    print(sep)
    print(f"  In-sample   : {IS_LABEL}")
    print(f"  IS days     : {n_days_is:,}")
    print(f"  IS rows     : {len(nifty_is):,}")
    print(sep)
    print(f"  Out-of-sample: {OOS_LABEL}")
    print(f"  OOS status  : LOCKED -- set OOS_UNLOCKED=1 to access")
    print(sep)


# ── self-test ─────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    partition_info()

    # verify OOS lock is working
    try:
        _load("nifty50", "15min", split="oos")
        print("\nERROR: OOS lock did not fire!")
    except PermissionError as e:
        print(f"\nOOS lock working correctly.")

    # verify IS data ends where it should
    nifty = load_nifty_15min()
    last_ts = nifty["datetime"].max()
    assert last_ts <= IS_END.replace(hour=23, minute=59), \
        f"IS data leaked past cutoff: {last_ts}"
    print(f"IS data ends at: {last_ts}  [OK]")

    vix = load_vix_15min()
    print(f"VIX IS rows: {len(vix):,}  last: {vix['datetime'].max()}")

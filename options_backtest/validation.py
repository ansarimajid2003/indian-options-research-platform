from __future__ import annotations

from pathlib import Path

import pandas as pd

from .data_store import load_expiry_options, load_expiry_spot, list_expiry_dirs


def audit_raw_shoonya(raw_root: Path, bad_expiries: set[str] | None = None) -> pd.DataFrame:
    rows = []
    for expiry_dir in list_expiry_dirs(raw_root, bad_expiries):
        csvs = list(expiry_dir.glob("*.csv"))
        option_files = [path for path in csvs if path.name != "nifty_spot.csv"]
        spot_rows = len(load_expiry_spot(expiry_dir)) if (expiry_dir / "nifty_spot.csv").exists() else 0
        rows.append({
            "expiry": expiry_dir.name,
            "csv_files": len(csvs),
            "option_files": len(option_files),
            "spot_rows": spot_rows,
            "size_mb": round(sum(path.stat().st_size for path in csvs) / 1_048_576, 2),
        })
    return pd.DataFrame(rows)


def validate_ohlc(df: pd.DataFrame) -> int:
    bad = (
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["high"] < df["low"]) |
        (df["low"] > df["open"]) |
        (df["low"] > df["close"]) |
        (df["low"] > df["high"])
    )
    return int(bad.sum())


def audit_expiry_quality(expiry_dir: Path) -> dict[str, int | str]:
    options = load_expiry_options(expiry_dir)
    spot = load_expiry_spot(expiry_dir)
    return {
        "expiry": expiry_dir.name,
        "option_rows": int(len(options)),
        "spot_rows": int(len(spot)),
        "bad_ohlc_rows": validate_ohlc(options),
        "duplicate_option_keys": int(options.duplicated(["timestamp", "strike", "option_type"]).sum()),
        "duplicate_spot_timestamps": int(spot.duplicated(["timestamp"]).sum()),
    }

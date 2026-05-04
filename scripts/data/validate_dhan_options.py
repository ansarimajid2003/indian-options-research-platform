"""
Dhan options data validation, cleaning, and audit.

Reads all JSON files under data/raw/options/dhan/nifty/{week,month}/expiry_code_1/
and produces:
  - data/processed/options/dhan/nifty/{week,month}/expiry_code_1/{side}/{bucket}.parquet
    (one parquet per bucket, all months concatenated, cleaned)
  - data/audit/dhan_options_audit.csv  (per-file audit rows)
  - data/audit/dhan_options_summary.txt (human-readable summary)

Cleaning rules applied:
  1. Timestamps: unix epoch → IST datetime, drop any bar outside 09:15–15:29.
  2. IV: preserve raw IV, flag extreme IV > 200 and IV=0, and write a separate
     same-day-forward-filled iv_clean column. The original iv column is untouched.
  3. OHLC: flag bad rows (high < low etc.) -zero bad rows expected; assert or warn.
  4. Zero/negative close: flag but do NOT drop -may be genuine illiquid bars.
  5. Duplicate timestamps within a file: drop keep='first'.
  6. Sort by timestamp ascending.

Usage:
    python scripts/data/validate_dhan_options.py
    python scripts/data/validate_dhan_options.py --index banknifty --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import textwrap
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "options" / "dhan"
OUT_ROOT = ROOT / "data" / "processed" / "options" / "dhan"
AUDIT_DIR = ROOT / "data" / "audit"

MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:29"
IV_SPIKE_CAP = 200.0  # % -anything above is flagged as an expiry-day artefact

INDICES = ["nifty", "banknifty", "finnifty", "midcpnifty"]
EXPIRY_TYPES = ["week", "month"]
SIDES = ["call", "put"]
CE_KEY = "ce"
PE_KEY = "pe"
SIDE_KEY = {
    "call": CE_KEY,
    "put": PE_KEY,
}


# ---------------------------------------------------------------------------
# JSON → DataFrame
# ---------------------------------------------------------------------------

def _parse_file(path: Path, side: str) -> pd.DataFrame | None:
    """Parse one Dhan JSON file into a clean DataFrame."""
    with path.open() as f:
        raw = json.load(f)

    data_key = SIDE_KEY[side]
    try:
        arrays = raw["response"]["data"][data_key]
    except (KeyError, TypeError):
        return None

    n = len(arrays.get("timestamp", []))
    if n == 0:
        return None

    df = pd.DataFrame({
        "timestamp": pd.to_datetime(arrays["timestamp"], unit="s", utc=True),
        "open": arrays["open"],
        "high": arrays["high"],
        "low": arrays["low"],
        "close": arrays["close"],
        "volume": arrays["volume"],
        "oi": arrays["oi"],
        "iv": arrays["iv"],
        "strike": arrays["strike"],
        "spot": arrays["spot"],
    })
    df["timestamp"] = df["timestamp"].dt.tz_convert("Asia/Kolkata")
    return df


# ---------------------------------------------------------------------------
# Cleaning
# ---------------------------------------------------------------------------

def _clean(df: pd.DataFrame, filename: str) -> tuple[pd.DataFrame, dict]:
    """Apply all cleaning rules. Returns cleaned df and an audit dict."""
    audit: dict = {
        "file": filename,
        "raw_rows": len(df),
        "dropped_outside_hours": 0,
        "dropped_duplicates": 0,
        "bad_ohlc_rows": 0,
        "zero_close_rows": 0,
        "neg_close_rows": 0,
        "iv_spike_capped": 0,
        "iv_zero_ffilled": 0,
        "zero_volume_rows": 0,
        "clean_rows": 0,
        "date_min": "",
        "date_max": "",
    }

    # 1. Filter to market hours
    time_str = df["timestamp"].dt.strftime("%H:%M")
    in_hours = (time_str >= MARKET_OPEN) & (time_str <= MARKET_CLOSE)
    audit["dropped_outside_hours"] = int((~in_hours).sum())
    df = df[in_hours].copy()

    # 2. Drop duplicate timestamps
    dups = df.duplicated("timestamp", keep="first")
    audit["dropped_duplicates"] = int(dups.sum())
    df = df[~dups].copy()

    # 3. Sort ascending
    df = df.sort_values("timestamp").reset_index(drop=True)

    # 4. OHLC integrity
    bad_ohlc = (
        (df["high"] < df["low"]) |
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["low"] > df["open"]) |
        (df["low"] > df["close"])
    )
    audit["bad_ohlc_rows"] = int(bad_ohlc.sum())

    # 5. Zero / negative close
    audit["zero_close_rows"] = int((df["close"] == 0).sum())
    audit["neg_close_rows"] = int((df["close"] < 0).sum())
    audit["zero_volume_rows"] = int((df["volume"] == 0).sum())

    # 6. IV: preserve raw values, flag bad vendor IV, and build a cleaned series.
    df["iv_raw"] = df["iv"]
    iv_spike = df["iv"] > IV_SPIKE_CAP
    audit["iv_spike_capped"] = int(iv_spike.sum())
    df["iv_spike_flag"] = iv_spike

    # Zero IV at end-of-day before expiry is a known Dhan artefact -treat as NaN
    iv_zero = df["iv"] == 0.0
    audit["iv_zero_ffilled"] = int(iv_zero.sum())
    df["iv_zero_flag"] = iv_zero
    df["iv_clean"] = df["iv"].mask(iv_spike | iv_zero)

    # Forward-fill NaN IV within each trading date (no cross-day fill)
    df["_date"] = df["timestamp"].dt.date
    df["iv_clean"] = df.groupby("_date")["iv_clean"].ffill()
    df = df.drop(columns=["_date"])

    if len(df) > 0:
        audit["date_min"] = str(df["timestamp"].min().date())
        audit["date_max"] = str(df["timestamp"].max().date())

    audit["clean_rows"] = len(df)
    return df, audit


# ---------------------------------------------------------------------------
# File-level audit
# ---------------------------------------------------------------------------

def audit_file(path: Path, side: str) -> dict:
    """Parse + clean one file, return audit metrics (no disk write)."""
    try:
        df = _parse_file(path, side)
    except Exception as exc:
        return {"file": path.name, "error": str(exc), "clean_rows": 0}

    if df is None:
        return {"file": path.name, "error": "empty_or_missing_key", "clean_rows": 0}

    _, audit = _clean(df, path.name)
    return audit


# ---------------------------------------------------------------------------
# Bucket-level processing
# ---------------------------------------------------------------------------

def process_bucket(
    index: str,
    expiry_type: str,
    side: str,
    bucket: str,
    dry_run: bool = False,
) -> list[dict]:
    """Process all monthly JSON files for one bucket. Write parquet if not dry_run."""
    bucket_dir = RAW_ROOT / index / expiry_type / "expiry_code_1" / side / bucket
    if not bucket_dir.exists():
        return []

    files = sorted(bucket_dir.glob("*.json"))
    all_dfs: list[pd.DataFrame] = []
    audit_rows: list[dict] = []

    for path in files:
        try:
            df = _parse_file(path, side)
        except Exception as exc:
            audit_rows.append({
                "index": index, "expiry_type": expiry_type,
                "side": side, "bucket": bucket,
                "file": path.name, "error": str(exc),
                "clean_rows": 0,
            })
            continue

        if df is None:
            audit_rows.append({
                "index": index, "expiry_type": expiry_type,
                "side": side, "bucket": bucket,
                "file": path.name, "error": "empty_or_missing_key",
                "clean_rows": 0,
            })
            continue

        df_clean, audit = _clean(df, path.name)
        audit["index"] = index
        audit["expiry_type"] = expiry_type
        audit["side"] = side
        audit["bucket"] = bucket
        audit_rows.append(audit)
        if len(df_clean) > 0:
            all_dfs.append(df_clean)

    if not dry_run and all_dfs:
        combined = pd.concat(all_dfs, ignore_index=True)
        combined = combined.sort_values("timestamp").drop_duplicates("timestamp").reset_index(drop=True)
        out_path = (
            OUT_ROOT / index / expiry_type / "expiry_code_1" / side / f"{bucket}.parquet"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(out_path, index=False, engine="pyarrow")

    return audit_rows


# ---------------------------------------------------------------------------
# Summary report
# ---------------------------------------------------------------------------

def _build_summary(df_audit: pd.DataFrame) -> str:
    total_files = len(df_audit)
    errored = df_audit["error"].notna().sum() if "error" in df_audit.columns else 0
    ok = df_audit[df_audit.get("error", pd.Series(dtype=str)).isna()] if "error" in df_audit.columns else df_audit
    total_clean_rows = int(ok["clean_rows"].sum())
    total_bad_ohlc = int(ok["bad_ohlc_rows"].sum()) if "bad_ohlc_rows" in ok.columns else 0
    total_iv_flagged = int(ok["iv_spike_capped"].sum()) if "iv_spike_capped" in ok.columns else 0
    total_iv_zero = int(ok["iv_zero_ffilled"].sum()) if "iv_zero_ffilled" in ok.columns else 0
    total_dropped_dup = int(ok["dropped_duplicates"].sum()) if "dropped_duplicates" in ok.columns else 0
    total_dropped_hours = int(ok["dropped_outside_hours"].sum()) if "dropped_outside_hours" in ok.columns else 0
    total_zero_close = int(ok["zero_close_rows"].sum()) if "zero_close_rows" in ok.columns else 0
    total_zero_vol = int(ok["zero_volume_rows"].sum()) if "zero_volume_rows" in ok.columns else 0

    if "date_min" in ok.columns:
        date_min_values = ok["date_min"].replace("", pd.NA).dropna()
        date_max_values = ok["date_max"].replace("", pd.NA).dropna()
        date_min = date_min_values.min() if len(date_min_values) else "n/a"
        date_max = date_max_values.max() if len(date_max_values) else "n/a"
    else:
        date_min = date_max = "n/a"

    lines = [
        "=" * 64,
        "DHAN OPTIONS DATA AUDIT SUMMARY",
        "=" * 64,
        f"  Files processed    : {total_files}",
        f"  Files with errors  : {errored}",
        f"  Coverage           : {date_min} to {date_max}",
        f"  Clean bars total   : {total_clean_rows:,}",
        "",
        "CLEANING ACTIONS:",
        f"  Bars outside mkt hours dropped  : {total_dropped_hours:,}",
        f"  Duplicate timestamps dropped    : {total_dropped_dup:,}",
        f"  Bad OHLC rows (flagged)         : {total_bad_ohlc:,}",
        f"  Zero-close bars (kept, flagged) : {total_zero_close:,}",
        f"  Zero-volume bars (kept, flagged): {total_zero_vol:,}",
        f"  IV spikes >200% flagged         : {total_iv_flagged:,}",
        f"  IV=0 bars flagged               : {total_iv_zero:,}",
        "  iv_clean same-day forward-filled for IV research; raw iv is preserved",
        "",
        "PER-BUCKET CLEAN ROW COUNTS:",
    ]

    if "bucket" in ok.columns and len(ok) > 0:
        bucket_summary = (
            ok.groupby(["index", "expiry_type", "side", "bucket"])["clean_rows"].sum()
            .reset_index()
            .sort_values(["index", "expiry_type", "side", "bucket"])
        )
        for _, row in bucket_summary.iterrows():
            label = f"{row['index']}/{row['expiry_type']}/{row['side']}/{row['bucket']}"
            lines.append(f"  {label:36s}: {int(row['clean_rows']):>10,} bars")

    lines += [
        "",
        "QUALITY VERDICT:",
    ]
    if total_bad_ohlc == 0:
        lines.append("  OHLC integrity : PASS -zero bad rows")
    else:
        lines.append(f"  OHLC integrity : WARN -{total_bad_ohlc} bad rows (review audit CSV)")

    if errored == 0:
        lines.append("  Parse errors   : PASS -all files parsed")
    else:
        lines.append(f"  Parse errors   : FAIL -{errored} files failed (check audit CSV 'error' column)")

    if total_iv_flagged > 0:
        lines.append(f"  IV spikes      : WARN -{total_iv_flagged} bars had IV>200% (raw iv preserved; use iv_clean only deliberately)")
    else:
        lines.append("  IV spikes      : PASS")

    lines.append("=" * 64)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and clean Dhan options data")
    parser.add_argument("--index", action="append", choices=INDICES, help="Index to process. Repeatable; default: nifty")
    parser.add_argument("--all-indices", action="store_true", help="Process all indices")
    parser.add_argument("--expiry-type", default=None, choices=EXPIRY_TYPES, help="week or month (default: both)")
    parser.add_argument("--dry-run", action="store_true", help="Audit only, skip parquet writes")
    args = parser.parse_args()

    indices = INDICES if args.all_indices else (args.index or ["nifty"])
    expiry_types = EXPIRY_TYPES if args.expiry_type is None else [args.expiry_type]

    all_audit: list[dict] = []

    for index in indices:
        for expiry_type in expiry_types:
            expiry_root = RAW_ROOT / index / expiry_type / "expiry_code_1"
            if not expiry_root.exists():
                print(f"  [skip] {index}/{expiry_type} -not found")
                continue

            for side in SIDES:
                side_dir = expiry_root / side
                if not side_dir.exists():
                    continue
                buckets = sorted(d.name for d in side_dir.iterdir() if d.is_dir())
                for bucket in buckets:
                    label = f"{index}/{expiry_type}/{side}/{bucket}"
                    print(f"  processing {label} ...", end=" ", flush=True)
                    rows = process_bucket(index, expiry_type, side, bucket, dry_run=args.dry_run)
                    all_audit.extend(rows)
                    ok = sum(1 for r in rows if r.get("error") is None and not r.get("error"))
                    total_clean = sum(r.get("clean_rows", 0) for r in rows)
                    print(f"{ok}/{len(rows)} files OK, {total_clean:,} clean bars")

    if not all_audit:
        print("No data processed.")
        return

    df_audit = pd.DataFrame(all_audit)

    # Save audit CSV
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    scope = "all" if args.all_indices else "_".join(indices)
    audit_csv = AUDIT_DIR / "dhan_options_audit.csv"
    scoped_audit_csv = AUDIT_DIR / f"dhan_options_audit_{scope}.csv"
    df_audit.to_csv(audit_csv, index=False)
    df_audit.to_csv(scoped_audit_csv, index=False)
    print(f"\nAudit CSV -> {audit_csv}")
    print(f"Scoped CSV -> {scoped_audit_csv}")

    # Build and save summary
    summary = _build_summary(df_audit)
    summary_txt = AUDIT_DIR / "dhan_options_summary.txt"
    scoped_summary_txt = AUDIT_DIR / f"dhan_options_summary_{scope}.txt"
    summary_txt.write_text(summary, encoding="utf-8")
    scoped_summary_txt.write_text(summary, encoding="utf-8")
    print(f"Summary   -> {summary_txt}")
    print(f"Scoped summary -> {scoped_summary_txt}")
    print()
    print(summary)


if __name__ == "__main__":
    main()

"""
NSE F&O Bhavcopy validation, cleaning, and consolidation.

Reads all daily CSV files under data/raw/nse/bhavcopy/fo/{year}/YYYYMMDD.csv
and produces:
  - data/processed/nse/bhavcopy/fo/nifty_options_eod.parquet
    (NIFTY index options only, all years, unified schema, one row per
     trade_date / expiry_date / strike / option_type)
  - data/audit/nse_bhavcopy_audit.csv  (per-file audit rows)
  - data/audit/nse_bhavcopy_summary.txt (human-readable summary)

Two raw schemas handled:
  Legacy (2008-07-04 to 2024-07-05):
    INSTRUMENT, SYMBOL, EXPIRY_DT, STRIKE_PR, OPTION_TYP,
    OPEN, HIGH, LOW, CLOSE, SETTLE_PR, CONTRACTS, VAL_INLAKH,
    OPEN_INT, CHG_IN_OI, TIMESTAMP
  UDiFF (2024-07-08 onwards):
    TradDt, BizDt, Sgmt, TckrSymb, XpryDt, StrkPric, OptnTp,
    OpnPric, HghPric, LwPric, ClsPric, SttlmPric, TtlTradgVol,
    TtlTrfVal, OpnIntrst, ChngInOpnIntrst, UndrlygPric, ...

Unified output schema (all dates):
  trade_date    datetime64[ns]    (date only, no time)
  expiry_date   datetime64[ns]
  strike        float64
  option_type   str               CE | PE
  open          float64           0 = no trades that day (NSE convention)
  high          float64
  low           float64
  close         float64           previous settle when no trades
  settle_price  float64
  volume        int64             contracts traded (0 for untouched strikes)
  open_interest int64
  oi_change     int64
  underlying    float64           spot close (UDiFF only; NaN for legacy)
  no_trade      bool              True when OPEN=HIGH=LOW=0 (NSE untouched-strike flag)

Cleaning rules:
  1. Filter to NIFTY index options only (OPTIDX+NIFTY / IDO+NIFTY).
  2. Filter to CE and PE only (drop futures rows that leak through).
  3. Parse and normalise both date formats to datetime64.
  4. Flag no_trade = (open == 0 & high == 0 & low == 0) — NOT dropped; callers filter.
  5. True OHLC violations (non-zero open but high < low): flag and report.
  6. Deduplicate on (trade_date, expiry_date, strike, option_type) keep='first'.
  7. Sort by (trade_date, expiry_date, strike, option_type).

Usage:
    python scripts/data/validate_nse_bhavcopy.py
    python scripts/data/validate_nse_bhavcopy.py --dry-run
    python scripts/data/validate_nse_bhavcopy.py --year 2023
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "nse" / "bhavcopy" / "fo"
OUT_ROOT = ROOT / "data" / "processed" / "nse" / "bhavcopy" / "fo"
AUDIT_DIR = ROOT / "data" / "audit"

# Schema transition: first UDiFF file is 20240708
UDIFF_START = pd.Timestamp("2024-07-08")


# ---------------------------------------------------------------------------
# Schema detection and parsing
# ---------------------------------------------------------------------------

def _detect_schema(df: pd.DataFrame) -> str:
    return "udiff" if "TradDt" in df.columns else "legacy"


def _parse_legacy(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise legacy schema to unified output schema."""
    # Early 2008 files use OPTIONTYPE instead of OPTION_TYP
    if "OPTIONTYPE" in df.columns and "OPTION_TYP" not in df.columns:
        df = df.rename(columns={"OPTIONTYPE": "OPTION_TYP"})

    # Filter to NIFTY index options
    df = df[
        (df["INSTRUMENT"] == "OPTIDX") &
        (df["SYMBOL"] == "NIFTY") &
        df["OPTION_TYP"].isin(["CE", "PE"])
    ].copy()

    if df.empty:
        return pd.DataFrame()

    df["trade_date"] = pd.to_datetime(df["TIMESTAMP"], format="%d-%b-%Y", dayfirst=True)
    df["expiry_date"] = pd.to_datetime(df["EXPIRY_DT"], format="%d-%b-%Y", dayfirst=True)
    df["strike"] = df["STRIKE_PR"].astype(float)
    df["option_type"] = df["OPTION_TYP"]
    df["open"] = df["OPEN"].astype(float)
    df["high"] = df["HIGH"].astype(float)
    df["low"] = df["LOW"].astype(float)
    df["close"] = df["CLOSE"].astype(float)
    df["settle_price"] = df["SETTLE_PR"].astype(float)
    df["volume"] = df["CONTRACTS"].astype(float).fillna(0).astype(int)
    df["open_interest"] = df["OPEN_INT"].astype(float).fillna(0).astype(int)
    df["oi_change"] = df["CHG_IN_OI"].astype(float).fillna(0).astype(int)
    df["underlying"] = np.nan

    return df[[
        "trade_date", "expiry_date", "strike", "option_type",
        "open", "high", "low", "close", "settle_price",
        "volume", "open_interest", "oi_change", "underlying",
    ]]


def _parse_udiff(df: pd.DataFrame) -> pd.DataFrame:
    """Normalise UDiFF schema to unified output schema."""
    # IDO = Index Derivative Option; filter NIFTY only, options only
    df = df[
        (df["TckrSymb"] == "NIFTY") &
        df["OptnTp"].isin(["CE", "PE"])
    ].copy()

    if df.empty:
        return pd.DataFrame()

    df["trade_date"] = pd.to_datetime(df["TradDt"])
    df["expiry_date"] = pd.to_datetime(df["XpryDt"])
    df["strike"] = df["StrkPric"].astype(float)
    df["option_type"] = df["OptnTp"]
    df["open"] = df["OpnPric"].astype(float)
    df["high"] = df["HghPric"].astype(float)
    df["low"] = df["LwPric"].astype(float)
    df["close"] = df["ClsPric"].astype(float)
    df["settle_price"] = df["SttlmPric"].astype(float)
    df["volume"] = df["TtlTradgVol"].astype(float).fillna(0).astype(int)
    df["open_interest"] = df["OpnIntrst"].astype(float).fillna(0).astype(int)
    df["oi_change"] = df["ChngInOpnIntrst"].astype(float).fillna(0).astype(int)
    df["underlying"] = df["UndrlygPric"].astype(float)

    return df[[
        "trade_date", "expiry_date", "strike", "option_type",
        "open", "high", "low", "close", "settle_price",
        "volume", "open_interest", "oi_change", "underlying",
    ]]


def parse_file(path: Path) -> pd.DataFrame | None:
    """Read one bhavcopy CSV and return unified-schema NIFTY options DataFrame."""
    try:
        df_raw = pd.read_csv(path, low_memory=False)
    except Exception:
        return None

    schema = _detect_schema(df_raw)
    if schema == "legacy":
        return _parse_legacy(df_raw)
    else:
        return _parse_udiff(df_raw)


# ---------------------------------------------------------------------------
# Per-file audit
# ---------------------------------------------------------------------------

def _audit_df(df: pd.DataFrame, fname: str, schema: str) -> dict:
    if df is None or df.empty:
        return {
            "file": fname, "schema": schema, "nifty_option_rows": 0,
            "no_trade_rows": 0, "true_bad_ohlc": 0, "duplicates": 0,
            "error": "empty_or_no_nifty",
        }

    no_trade = (df["open"] == 0) & (df["high"] == 0) & (df["low"] == 0)
    active = df[~no_trade]
    true_bad = ((active["high"] < active["low"]) |
                (active["high"] < active["open"]) |
                (active["high"] < active["close"])).sum() if len(active) > 0 else 0
    dups = df.duplicated(["trade_date", "expiry_date", "strike", "option_type"]).sum()

    return {
        "file": fname,
        "schema": schema,
        "nifty_option_rows": len(df),
        "no_trade_rows": int(no_trade.sum()),
        "active_rows": int((~no_trade).sum()),
        "true_bad_ohlc": int(true_bad),
        "duplicates": int(dups),
        "error": None,
    }


# ---------------------------------------------------------------------------
# Main processing
# ---------------------------------------------------------------------------

def process_year(year: str, dry_run: bool = False) -> list[dict]:
    year_dir = RAW_ROOT / year
    if not year_dir.exists():
        return []

    files = sorted(year_dir.glob("*.csv"))
    audit_rows: list[dict] = []
    dfs: list[pd.DataFrame] = []

    for path in files:
        try:
            df_raw = pd.read_csv(path, low_memory=False)
        except Exception as exc:
            audit_rows.append({"file": path.name, "schema": "?", "error": str(exc),
                                "nifty_option_rows": 0})
            continue

        schema = _detect_schema(df_raw)
        try:
            if schema == "legacy":
                df = _parse_legacy(df_raw)
            else:
                df = _parse_udiff(df_raw)
        except Exception as exc:
            audit_rows.append({"file": path.name, "schema": schema, "error": str(exc),
                                "nifty_option_rows": 0})
            continue

        audit = _audit_df(df if df is not None else pd.DataFrame(), path.name, schema)
        audit_rows.append(audit)

        if df is not None and not df.empty:
            dfs.append(df)

    if not dry_run and dfs:
        combined = pd.concat(dfs, ignore_index=True)
        # Flag no-trade rows
        combined["no_trade"] = (
            (combined["open"] == 0) & (combined["high"] == 0) & (combined["low"] == 0)
        )
        # Deduplicate
        combined = combined.drop_duplicates(
            ["trade_date", "expiry_date", "strike", "option_type"], keep="first"
        )
        combined = combined.sort_values(
            ["trade_date", "expiry_date", "strike", "option_type"]
        ).reset_index(drop=True)

        out_path = OUT_ROOT / f"nifty_options_eod_{year}.parquet"
        out_path.parent.mkdir(parents=True, exist_ok=True)
        combined.to_parquet(out_path, index=False, engine="pyarrow")

    return audit_rows


def _build_summary(df_audit: pd.DataFrame, years: list[str]) -> str:
    total_files = len(df_audit)
    errored = df_audit["error"].notna().sum() if "error" in df_audit.columns else 0
    ok = df_audit[df_audit.get("error", pd.Series(dtype=str)).isna()]

    total_rows = int(ok["nifty_option_rows"].sum()) if "nifty_option_rows" in ok.columns else 0
    total_active = int(ok["active_rows"].sum()) if "active_rows" in ok.columns else 0
    total_no_trade = int(ok["no_trade_rows"].sum()) if "no_trade_rows" in ok.columns else 0
    total_bad_ohlc = int(ok["true_bad_ohlc"].sum()) if "true_bad_ohlc" in ok.columns else 0
    total_dups = int(ok["duplicates"].sum()) if "duplicates" in ok.columns else 0

    legacy_count = int((ok.get("schema", pd.Series(dtype=str)) == "legacy").sum())
    udiff_count = int((ok.get("schema", pd.Series(dtype=str)) == "udiff").sum())

    lines = [
        "=" * 64,
        "NSE F&O BHAVCOPY AUDIT SUMMARY (NIFTY OPTIONS)",
        "=" * 64,
        f"  Years processed       : {years[0]} - {years[-1]}",
        f"  Files processed       : {total_files}",
        f"  Files with errors     : {errored}",
        f"  Legacy-schema files   : {legacy_count}",
        f"  UDiFF-schema files    : {udiff_count}",
        "",
        "ROW COUNTS (NIFTY options only):",
        f"  Total rows            : {total_rows:,}",
        f"  Active (OPEN>0) rows  : {total_active:,}",
        f"  No-trade rows (flag)  : {total_no_trade:,}",
        "",
        "QUALITY:",
        f"  True bad OHLC rows    : {total_bad_ohlc:,}",
        f"  Duplicate keys        : {total_dups:,}",
        "",
        "PER-YEAR ROW COUNTS:",
    ]

    if "file" in ok.columns:
        ok = ok.copy()
        ok["year"] = ok["file"].str[:4]
        yr_summary = ok.groupby("year")["nifty_option_rows"].sum().reset_index()
        for _, row in yr_summary.iterrows():
            lines.append(f"  {row['year']}: {int(row['nifty_option_rows']):>10,} NIFTY option rows")

    lines += [
        "",
        "QUALITY VERDICT:",
    ]
    if total_bad_ohlc == 0:
        lines.append("  OHLC integrity : PASS - zero true bad rows")
    else:
        lines.append(f"  OHLC integrity : WARN - {total_bad_ohlc} bad rows (active strikes only)")

    if errored == 0:
        lines.append("  Parse errors   : PASS - all files parsed")
    else:
        lines.append(f"  Parse errors   : FAIL - {errored} files failed")

    if total_dups == 0:
        lines.append("  Duplicates     : PASS - zero duplicate keys")
    else:
        lines.append(f"  Duplicates     : WARN - {total_dups} duplicate (trade_date, expiry, strike, type) keys")

    lines.append("=" * 64)
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and clean NSE F&O bhavcopy data")
    parser.add_argument("--year", default=None, help="Process single year (default: all)")
    parser.add_argument("--dry-run", action="store_true", help="Audit only, no parquet writes")
    args = parser.parse_args()

    if args.year:
        years = [args.year]
    else:
        years = sorted(d.name for d in RAW_ROOT.iterdir() if d.is_dir() and d.name.isdigit())

    all_audit: list[dict] = []

    for year in years:
        year_dir = RAW_ROOT / year
        n_files = len(list(year_dir.glob("*.csv")))
        print(f"  {year}: {n_files} files ...", end=" ", flush=True)
        rows = process_year(year, dry_run=args.dry_run)
        all_audit.extend(rows)
        ok = sum(1 for r in rows if not r.get("error"))
        total_nifty = sum(r.get("nifty_option_rows", 0) for r in rows)
        print(f"{ok}/{len(rows)} OK, {total_nifty:,} NIFTY rows")

    if not all_audit:
        print("No data processed.")
        return

    df_audit = pd.DataFrame(all_audit)

    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    audit_csv = AUDIT_DIR / "nse_bhavcopy_audit.csv"
    df_audit.to_csv(audit_csv, index=False)
    print(f"\nAudit CSV -> {audit_csv}")

    summary = _build_summary(df_audit, years)
    summary_txt = AUDIT_DIR / "nse_bhavcopy_summary.txt"
    summary_txt.write_text(summary, encoding="utf-8")
    print(f"Summary   -> {summary_txt}")
    print()
    print(summary)


if __name__ == "__main__":
    main()

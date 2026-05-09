"""
Validate and clean Dhan 1-minute index spot JSON files.

Input:
  data/raw/spot/intraday/{SYMBOL}/{from}_{to}.json

Output:
  data/processed/spot/{symbol}_1min_DHAN.csv
  data/audit/dhan_spot_audit.csv
  data/audit/dhan_spot_summary.txt
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
RAW_ROOT = ROOT / "data" / "raw" / "spot" / "intraday"
OUT_ROOT = ROOT / "data" / "processed" / "spot"
AUDIT_DIR = ROOT / "data" / "audit"

SYMBOLS = ["BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "INDIAVIX", "SENSEX"]
MARKET_OPEN = "09:15"
MARKET_CLOSE = "15:29"


def _parse_file(path: Path) -> pd.DataFrame | None:
    with path.open(encoding="utf-8") as f:
        raw = json.load(f)
    data = raw.get("response", {})
    ts = data.get("timestamp", [])
    if not ts:
        return None
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata"),
        "open": data.get("open", []),
        "high": data.get("high", []),
        "low": data.get("low", []),
        "close": data.get("close", []),
        "volume": data.get("volume", [0] * len(ts)),
    })
    return df


def _clean(df: pd.DataFrame, symbol: str, file_name: str) -> tuple[pd.DataFrame, dict]:
    audit = {
        "symbol": symbol,
        "file": file_name,
        "raw_rows": len(df),
        "dropped_outside_hours": 0,
        "dropped_duplicates": 0,
        "bad_ohlc_rows": 0,
        "zero_close_rows": 0,
        "clean_rows": 0,
        "date_min": "",
        "date_max": "",
    }
    time_str = df["timestamp"].dt.strftime("%H:%M")
    in_hours = (time_str >= MARKET_OPEN) & (time_str <= MARKET_CLOSE)
    audit["dropped_outside_hours"] = int((~in_hours).sum())
    df = df[in_hours].copy()

    dupes = df.duplicated("timestamp", keep="first")
    audit["dropped_duplicates"] = int(dupes.sum())
    df = df[~dupes].copy()

    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["timestamp", "open", "high", "low", "close"])

    bad_ohlc = (
        (df["high"] < df["low"]) |
        (df["high"] < df["open"]) |
        (df["high"] < df["close"]) |
        (df["low"] > df["open"]) |
        (df["low"] > df["close"])
    )
    audit["bad_ohlc_rows"] = int(bad_ohlc.sum())
    audit["zero_close_rows"] = int((df["close"] == 0).sum())
    df = df.sort_values("timestamp").reset_index(drop=True)
    if not df.empty:
        audit["date_min"] = str(df["timestamp"].min().date())
        audit["date_max"] = str(df["timestamp"].max().date())
    audit["clean_rows"] = len(df)
    df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    return df, audit


def process_symbol(symbol: str, dry_run: bool = False) -> list[dict]:
    source = RAW_ROOT / symbol
    if not source.exists():
        print(f"  [skip] {symbol} - not found")
        return []
    audit_rows: list[dict] = []
    frames: list[pd.DataFrame] = []
    for path in sorted(source.glob("*.json")):
        try:
            df = _parse_file(path)
        except Exception as exc:
            audit_rows.append({"symbol": symbol, "file": path.name, "error": str(exc), "clean_rows": 0})
            continue
        if df is None:
            audit_rows.append({"symbol": symbol, "file": path.name, "error": "empty_response", "clean_rows": 0})
            continue
        clean, audit = _clean(df, symbol, path.name)
        audit_rows.append(audit)
        if not clean.empty:
            frames.append(clean)
    if frames and not dry_run:
        out = pd.concat(frames, ignore_index=True).sort_values("timestamp")
        out = out.drop_duplicates("timestamp", keep="first").reset_index(drop=True)
        OUT_ROOT.mkdir(parents=True, exist_ok=True)
        out.to_csv(OUT_ROOT / f"{symbol.lower()}_1min_DHAN.csv", index=False)
    return audit_rows


def _summary(df: pd.DataFrame) -> str:
    ok = df[df.get("error", pd.Series(dtype=str)).isna()] if "error" in df.columns else df
    lines = [
        "DHAN SPOT DATA AUDIT SUMMARY",
        f"Files processed: {len(df)}",
        f"Files with errors: {int(df['error'].notna().sum()) if 'error' in df.columns else 0}",
        f"Clean rows: {int(ok['clean_rows'].sum()) if 'clean_rows' in ok.columns else 0:,}",
        f"Bad OHLC rows: {int(ok['bad_ohlc_rows'].sum()) if 'bad_ohlc_rows' in ok.columns else 0:,}",
        "",
        "Per-symbol rows:",
    ]
    if len(ok) > 0:
        grouped = ok.groupby("symbol")["clean_rows"].sum().reset_index()
        for row in grouped.itertuples(index=False):
            lines.append(f"  {row.symbol:10s}: {int(row.clean_rows):>10,}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate and clean Dhan spot data")
    parser.add_argument("--symbol", action="append", choices=SYMBOLS, help="Symbol to process. Repeatable; default: all")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    symbols = args.symbol or SYMBOLS
    rows: list[dict] = []
    for symbol in symbols:
        print(f"  processing {symbol} ...", end=" ", flush=True)
        before = len(rows)
        rows.extend(process_symbol(symbol, dry_run=args.dry_run))
        clean_rows = sum(r.get("clean_rows", 0) for r in rows[before:])
        print(f"{len(rows) - before} files, {clean_rows:,} clean rows")

    if not rows:
        print("No data processed.")
        return
    AUDIT_DIR.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(rows)
    audit_csv = AUDIT_DIR / "dhan_spot_audit.csv"
    df.to_csv(audit_csv, index=False)
    summary = _summary(df)
    summary_txt = AUDIT_DIR / "dhan_spot_summary.txt"
    summary_txt.write_text(summary, encoding="utf-8")
    print(f"\nAudit CSV -> {audit_csv}")
    print(f"Summary   -> {summary_txt}")
    print(summary)


if __name__ == "__main__":
    main()

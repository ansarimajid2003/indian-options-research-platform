"""
Clean archive market data and build canonical NIFTY 50 minute data.

Raw files under data/raw/market_archive/ are treated as the source of truth and
are never modified. Cleaned files are written to
data/processed/market_archive_cleaned/, and audit reports are written to
reports/data_quality/.

Cleaning policy:
  - parse and normalize timestamps
  - coerce numeric OHLCV columns
  - drop rows with missing datetime/OHLC or non-positive OHLC
  - repair OHLC envelope errors by setting high=max(OHLC), low=min(OHLC)
  - normalize intraday timestamp seconds while preserving NSE's 09:15 anchor
  - aggregate duplicate normalized timestamps with OHLC semantics
  - report gaps/partial days instead of fabricating missing candles
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parents[2]
ARCHIVE_DIR = ROOT / "data" / "raw" / "market_archive"
CLEAN_DIR = ROOT / "data" / "processed" / "market_archive_cleaned"
REPORT_DIR = ROOT / "reports" / "data_quality"
NIFTY_RECENT_FILE = ROOT / "data" / "raw" / "spot" / "nifty50_1m_recent_7days.csv"
NIFTY_CANONICAL_FILE = ROOT / "data" / "processed" / "spot" / "nifty50_1min_CANONICAL.csv"

OHLC_COLUMNS = ["open", "high", "low", "close"]
OUTPUT_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]


@dataclass
class FileReport:
    file: str
    interval: str
    input_rows: int
    output_rows: int
    start: str
    end: str
    null_datetime_dropped: int
    null_ohlc_dropped: int
    nonpositive_ohlc_dropped: int
    ohlc_repaired_rows: int
    duplicate_timestamp_rows_collapsed: int
    weekend_rows: int
    off_regular_session_rows: int
    trading_days: int
    min_rows_per_day: int
    median_rows_per_day: float
    max_rows_per_day: int
    partial_days_lt_90pct_expected: int
    regular_slot_missing_days: int
    missing_regular_slots: int
    abs_return_gt_5pct: int
    abs_return_gt_10pct: int


def infer_interval(path: Path) -> tuple[str, int | None]:
    stem = path.stem.lower()
    if stem.endswith("_minute"):
        return "1min", 375
    if stem.endswith("_5minute"):
        return "5min", 75
    if stem.endswith("_15minute"):
        return "15min", 25
    if stem.endswith("_30minute"):
        return "30min", 13
    if stem.endswith("_60minute"):
        return "60min", 7
    if stem.endswith("_day"):
        return "day", None
    return "unknown", None


def read_market_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]

    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    elif "date" in df.columns and "time" in df.columns:
        df["datetime"] = pd.to_datetime(
            df["date"].astype(str) + " " + df["time"].astype(str),
            format="%d-%m-%Y %H:%M:%S",
            errors="coerce",
        )
    elif "date" in df.columns:
        df["datetime"] = pd.to_datetime(df["date"], errors="coerce")
    else:
        raise ValueError(f"{path} has no datetime/date column")

    for column in OHLC_COLUMNS:
        if column not in df.columns:
            raise ValueError(f"{path} is missing required column: {column}")
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if "volume" not in df.columns:
        df["volume"] = 0
    df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0)

    return df[OUTPUT_COLUMNS]


def normalize_timestamps(df: pd.DataFrame, interval: str) -> pd.DataFrame:
    if interval == "day":
        df["datetime"] = df["datetime"].dt.normalize()
    else:
        # Some rows have stray seconds such as 15:29:01. Use minute precision,
        # but do not floor to 30/60-minute clock boundaries because NSE bars are
        # anchored at 09:15, not at midnight.
        df["datetime"] = df["datetime"].dt.floor("min")
    return df


def repair_ohlc_envelope(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    row_max = df[OHLC_COLUMNS].max(axis=1)
    row_min = df[OHLC_COLUMNS].min(axis=1)
    bad_envelope = (df["high"] != row_max) | (df["low"] != row_min)
    repaired = int(bad_envelope.sum())
    df.loc[bad_envelope, "high"] = row_max[bad_envelope]
    df.loc[bad_envelope, "low"] = row_min[bad_envelope]
    return df, repaired


def collapse_duplicate_timestamps(df: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    duplicate_rows = int(df.duplicated("datetime").sum())
    if duplicate_rows == 0:
        return df, 0

    collapsed = (
        df.sort_values("datetime")
        .groupby("datetime", as_index=False)
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
    )
    return collapsed, duplicate_rows


def expected_regular_index(day, interval: str) -> pd.DatetimeIndex | None:
    if interval == "1min":
        return pd.date_range(f"{day} 09:15:00", f"{day} 15:29:00", freq="1min")
    if interval == "5min":
        return pd.date_range(f"{day} 09:15:00", f"{day} 15:25:00", freq="5min")
    if interval == "15min":
        return pd.date_range(f"{day} 09:15:00", f"{day} 15:15:00", freq="15min")
    if interval == "30min":
        return pd.date_range(f"{day} 09:15:00", f"{day} 15:15:00", freq="30min")
    if interval == "60min":
        return pd.date_range(f"{day} 09:15:00", f"{day} 15:15:00", freq="60min")
    return None


def coverage_stats(df: pd.DataFrame, interval: str, expected_rows: int | None) -> dict:
    empty = {
        "weekend_rows": 0,
        "off_regular_session_rows": 0,
        "trading_days": 0,
        "min_rows_per_day": 0,
        "median_rows_per_day": 0.0,
        "max_rows_per_day": 0,
        "partial_days_lt_90pct_expected": 0,
        "regular_slot_missing_days": 0,
        "missing_regular_slots": 0,
    }
    if df.empty:
        return empty

    if interval == "day":
        return {
            **empty,
            "weekend_rows": int((df["datetime"].dt.weekday >= 5).sum()),
            "trading_days": int(df["datetime"].dt.date.nunique()),
        }

    times = df["datetime"].dt.strftime("%H:%M")
    by_day = df.groupby(df["datetime"].dt.date).size()
    partial_days = 0
    if expected_rows:
        partial_days = int((by_day < expected_rows * 0.9).sum())

    missing_days = 0
    missing_slots = 0
    if expected_rows:
        for day, group in df.groupby(df["datetime"].dt.date):
            # Special short sessions are deliberately counted as partial days, not
            # as hundreds of missing regular-session slots.
            if len(group) < expected_rows * 0.9:
                continue
            expected = expected_regular_index(day, interval)
            if expected is None:
                continue
            present = set(group["datetime"])
            missing = [ts for ts in expected if ts not in present]
            if missing:
                missing_days += 1
                missing_slots += len(missing)

    return {
        "weekend_rows": int((df["datetime"].dt.weekday >= 5).sum()),
        "off_regular_session_rows": int(
            ((df["datetime"].dt.weekday >= 5) | (times < "09:15") | (times > "15:30")).sum()
        ),
        "trading_days": int(len(by_day)),
        "min_rows_per_day": int(by_day.min()),
        "median_rows_per_day": float(by_day.median()),
        "max_rows_per_day": int(by_day.max()),
        "partial_days_lt_90pct_expected": partial_days,
        "regular_slot_missing_days": missing_days,
        "missing_regular_slots": missing_slots,
    }


def return_anomaly_stats(df: pd.DataFrame) -> tuple[int, int]:
    if len(df) < 2:
        return 0, 0
    returns = df["close"].pct_change()
    return int((returns.abs() > 0.05).sum()), int((returns.abs() > 0.10).sum())


def clean_file(path: Path, output_dir: Path) -> tuple[pd.DataFrame, FileReport]:
    interval, expected_rows = infer_interval(path)
    raw = read_market_csv(path)
    input_rows = len(raw)

    null_datetime = int(raw["datetime"].isna().sum())
    df = raw.dropna(subset=["datetime"]).copy()

    null_ohlc = int(df[OHLC_COLUMNS].isna().any(axis=1).sum())
    df = df.dropna(subset=OHLC_COLUMNS).copy()

    nonpositive = int((df[OHLC_COLUMNS] <= 0).any(axis=1).sum())
    df = df[(df[OHLC_COLUMNS] > 0).all(axis=1)].copy()

    df = normalize_timestamps(df, interval)
    df, repaired = repair_ohlc_envelope(df)
    df, collapsed_dupes = collapse_duplicate_timestamps(df)
    df = df.sort_values("datetime").reset_index(drop=True)

    clean_path = output_dir / path.name
    df.to_csv(clean_path, index=False)

    coverage = coverage_stats(df, interval, expected_rows)
    ret_5, ret_10 = return_anomaly_stats(df)
    report = FileReport(
        file=path.name,
        interval=interval,
        input_rows=input_rows,
        output_rows=len(df),
        start="" if df.empty else str(df["datetime"].min()),
        end="" if df.empty else str(df["datetime"].max()),
        null_datetime_dropped=null_datetime,
        null_ohlc_dropped=null_ohlc,
        nonpositive_ohlc_dropped=nonpositive,
        ohlc_repaired_rows=repaired,
        duplicate_timestamp_rows_collapsed=collapsed_dupes,
        abs_return_gt_5pct=ret_5,
        abs_return_gt_10pct=ret_10,
        **coverage,
    )
    return df, report


def add_source(df: pd.DataFrame, source: str) -> pd.DataFrame:
    out = df.copy()
    out["source"] = source
    return out


def load_recent_yfinance(path: Path) -> pd.DataFrame:
    df = read_market_csv(path)
    df = normalize_timestamps(df, "1min")
    df, _ = repair_ohlc_envelope(df)
    df, _ = collapse_duplicate_timestamps(df)
    return df.sort_values("datetime").reset_index(drop=True)


def build_canonical_nifty(clean_dir: Path, include_recent: bool) -> tuple[pd.DataFrame, dict]:
    archive_nifty_path = clean_dir / "NIFTY 50_minute.csv"
    if not archive_nifty_path.exists():
        raise FileNotFoundError(f"Missing cleaned NIFTY file: {archive_nifty_path}")

    frames = [add_source(pd.read_csv(archive_nifty_path, parse_dates=["datetime"]), "archive")]
    archive_end = frames[0]["datetime"].max()
    recent_rows_used = 0

    if include_recent and NIFTY_RECENT_FILE.exists():
        recent = load_recent_yfinance(NIFTY_RECENT_FILE)
        recent = recent[recent["datetime"] > archive_end].copy()
        recent_rows_used = len(recent)
        if not recent.empty:
            frames.append(add_source(recent, "yfinance_recent"))

    combined = pd.concat(frames, ignore_index=True)
    priority = {"archive": 0, "yfinance_recent": 1}
    combined["_priority"] = combined["source"].map(priority).fillna(99)
    combined = combined.sort_values(["datetime", "_priority"])
    combined = combined.drop_duplicates("datetime", keep="first")
    combined = combined.drop(columns=["_priority"]).sort_values("datetime").reset_index(drop=True)
    NIFTY_CANONICAL_FILE.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(NIFTY_CANONICAL_FILE, index=False)

    counts = combined.groupby(combined["datetime"].dt.date).size()
    quality = {
        "rows": len(combined),
        "start": str(combined["datetime"].min()),
        "end": str(combined["datetime"].max()),
        "archive_end": str(archive_end),
        "recent_rows_used": recent_rows_used,
        "duplicate_timestamps": int(combined["datetime"].duplicated().sum()),
        "source_counts": combined["source"].value_counts().to_dict(),
        "partial_days_lt_300_rows": int((counts < 300).sum()),
    }

    day_counts = counts.rename("rows").reset_index().rename(columns={"datetime": "date"})
    day_counts.to_csv(REPORT_DIR / "nifty50_canonical_day_counts.csv", index=False)
    pd.DataFrame([quality]).to_csv(REPORT_DIR / "nifty50_canonical_summary.csv", index=False)
    return combined, quality


def clean_archive(include_recent: bool) -> None:
    if not ARCHIVE_DIR.exists():
        raise FileNotFoundError(f"Archive directory not found: {ARCHIVE_DIR}")

    CLEAN_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_DIR.mkdir(parents=True, exist_ok=True)

    reports = []
    for path in sorted(ARCHIVE_DIR.glob("*.csv")):
        print(f"Cleaning {path.name}...")
        _, report = clean_file(path, CLEAN_DIR)
        reports.append(asdict(report))

    report_df = pd.DataFrame(reports)
    summary_path = REPORT_DIR / "archive_cleaning_summary.csv"
    report_df.to_csv(summary_path, index=False)

    canonical, quality = build_canonical_nifty(CLEAN_DIR, include_recent=include_recent)
    print()
    print(f"Wrote cleaned archive files to: {CLEAN_DIR}")
    print(f"Wrote archive cleaning report to: {summary_path}")
    print(f"Wrote canonical NIFTY 50 file to: {NIFTY_CANONICAL_FILE}")
    print(
        "Canonical NIFTY rows: {:,} | {} to {} | sources: {}".format(
            len(canonical),
            quality["start"],
            quality["end"],
            quality["source_counts"],
        )
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Clean archive data and build canonical NIFTY 50 data.")
    parser.add_argument(
        "--no-recent",
        action="store_true",
        help="Do not append rows from nifty50_1m_recent_7days.csv after the archive end.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    clean_archive(include_recent=not args.no_recent)

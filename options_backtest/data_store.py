from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from .calendar import parse_expiry_folder
from .schemas import OptionType


BAD_EXPIRIES = {"20250925", "20251224"}
OPTION_FILE_RE = re.compile(r"(?P<strike>\d+)(?P<option_type>CE|PE)_(?P<expiry>\d{8})\.csv$")
TICKER_RE = re.compile(r"NIFTY(?P<day>\d{2})(?P<month>[A-Z]{3})(?P<year>\d{2})(?P<option_type>CE|PE)(?P<strike>\d+)")
RAW_COLUMNS = {
    "Date": "trade_date",
    "Timestamp": "timestamp",
    "Open": "open",
    "High": "high",
    "Low": "low",
    "Close": "close",
    "Volume": "volume",
    "OI": "oi",
    "Ticker": "ticker",
}


@dataclass(frozen=True)
class NormalizedPaths:
    options_path: Path
    spot_path: Path
    format: str


def list_expiry_dirs(raw_root: Path, bad_expiries: set[str] | None = None) -> list[Path]:
    bad = BAD_EXPIRIES if bad_expiries is None else bad_expiries
    return sorted(
        path for path in raw_root.iterdir()
        if path.is_dir() and re.fullmatch(r"20\d{6}", path.name) and path.name not in bad
    )


def parse_option_filename(path: Path) -> tuple[int, OptionType, str]:
    match = OPTION_FILE_RE.match(path.name)
    if not match:
        raise ValueError(f"Not a Shoonya option file: {path}")
    return int(match.group("strike")), OptionType(match.group("option_type")), match.group("expiry")


def parse_ticker(ticker: str) -> tuple[int, OptionType] | None:
    match = TICKER_RE.fullmatch(str(ticker).strip())
    if not match:
        return None
    return int(match.group("strike")), OptionType(match.group("option_type"))


def _read_raw_csv(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df = df.rename(columns=RAW_COLUMNS)
    df["timestamp"] = pd.to_datetime(df["timestamp"], format="%d-%m-%Y %H:%M:%S", errors="coerce")
    df["trade_date"] = pd.to_datetime(df["trade_date"], errors="coerce").dt.date
    numeric_cols = ["open", "high", "low", "close", "volume", "oi"]
    for col in numeric_cols:
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)


def load_option_file(path: Path) -> pd.DataFrame:
    strike, option_type, expiry_name = parse_option_filename(path)
    df = _read_raw_csv(path)
    df["expiry"] = parse_expiry_folder(expiry_name)
    df["strike"] = strike
    df["option_type"] = option_type.value
    return df[
        ["timestamp", "trade_date", "expiry", "strike", "option_type", "open", "high", "low", "close", "volume", "oi", "ticker"]
    ]


def load_spot_file(path: Path) -> pd.DataFrame:
    df = _read_raw_csv(path)
    return df[["timestamp", "trade_date", "open", "high", "low", "close", "volume", "oi", "ticker"]]


def load_expiry_options(expiry_dir: Path, strikes: set[int] | None = None) -> pd.DataFrame:
    """Load all option CSVs in expiry_dir. If strikes is given, only load files for those strikes."""
    frames = [
        load_option_file(path)
        for path in sorted(expiry_dir.glob("*.csv"))
        if path.name != "nifty_spot.csv"
        and (strikes is None or _strike_from_filename(path) in strikes)
    ]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["timestamp", "strike", "option_type"]).reset_index(drop=True)


def _strike_from_filename(path: Path) -> int | None:
    match = OPTION_FILE_RE.match(path.name)
    return int(match.group("strike")) if match else None


def load_expiry_spot(expiry_dir: Path) -> pd.DataFrame:
    return load_spot_file(expiry_dir / "nifty_spot.csv").drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)


def load_deduped_spot(raw_root: Path, bad_expiries: set[str] | None = None) -> pd.DataFrame:
    frames = [load_expiry_spot(path) for path in list_expiry_dirs(raw_root, bad_expiries)]
    if not frames:
        return pd.DataFrame()
    return (
        pd.concat(frames, ignore_index=True)
        .drop_duplicates("timestamp")
        .sort_values("timestamp")
        .reset_index(drop=True)
    )


def _write_frame(df: pd.DataFrame, path_without_suffix: Path) -> tuple[Path, str]:
    try:
        output = path_without_suffix.with_suffix(".parquet")
        df.to_parquet(output, index=False)
        return output, "parquet"
    except Exception:
        output = path_without_suffix.with_suffix(".csv")
        df.to_csv(output, index=False)
        return output, "csv"


def normalize_shoonya_expiry(expiry_dir: Path, output_root: Path, vendor: str = "shoonya", symbol: str = "NIFTY") -> NormalizedPaths:
    expiry = expiry_dir.name
    option_df = load_expiry_options(expiry_dir)
    spot_df = load_expiry_spot(expiry_dir)
    partition = output_root / f"vendor={vendor}" / f"symbol={symbol}" / f"expiry={expiry}"
    partition.mkdir(parents=True, exist_ok=True)
    options_path, fmt = _write_frame(option_df, partition / "options")
    spot_path, _ = _write_frame(spot_df, partition / "spot")
    return NormalizedPaths(options_path=options_path, spot_path=spot_path, format=fmt)

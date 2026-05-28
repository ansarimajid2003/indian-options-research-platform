"""Spot-history validation and static CSV merge helpers.

Live spot bars are useful for the dashboard during market hours, but only a
complete regular-session capture should graduate into the durable processed
spot CSVs used after market close.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


REGULAR_SESSION_START = "09:15"
REGULAR_SESSION_END = "15:29"
EXPECTED_1MIN_ROWS = 375


@dataclass(frozen=True)
class SpotCsvSpec:
    symbol: str
    relative_path: Path
    timestamp_col: str


STATIC_SPOT_FILES: dict[str, SpotCsvSpec] = {
    "NIFTY": SpotCsvSpec("NIFTY", Path("processed/spot/nifty50_1min_CANONICAL.csv"), "datetime"),
    "BANKNIFTY": SpotCsvSpec("BANKNIFTY", Path("processed/spot/banknifty_1min_DHAN.csv"), "timestamp"),
    "FINNIFTY": SpotCsvSpec("FINNIFTY", Path("processed/spot/finnifty_1min_DHAN.csv"), "timestamp"),
    "MIDCPNIFTY": SpotCsvSpec("MIDCPNIFTY", Path("processed/spot/midcpnifty_1min_DHAN.csv"), "timestamp"),
    "SENSEX": SpotCsvSpec("SENSEX", Path("processed/spot/sensex_1min_DHAN.csv"), "timestamp"),
    "INDIAVIX": SpotCsvSpec("INDIAVIX", Path("processed/spot/indiavix_1min_DHAN.csv"), "timestamp"),
}


@dataclass(frozen=True)
class SessionCoverage:
    symbol: str
    session_date: date
    rows: int
    expected_rows: int = EXPECTED_1MIN_ROWS
    missing_minutes: int = 0
    duplicate_timestamps: int = 0
    first: str | None = None
    last: str | None = None
    complete: bool = False
    notes: list[str] = field(default_factory=list)


def expected_session_index(session_date: date) -> pd.DatetimeIndex:
    return pd.date_range(
        f"{session_date.isoformat()} {REGULAR_SESSION_START}:00",
        f"{session_date.isoformat()} {REGULAR_SESSION_END}:00",
        freq="1min",
    )


def _normalize_spot_frame(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return pd.DataFrame({
            "timestamp": pd.Series(dtype="datetime64[ns]"),
            "open": pd.Series(dtype="float64"),
            "high": pd.Series(dtype="float64"),
            "low": pd.Series(dtype="float64"),
            "close": pd.Series(dtype="float64"),
            "volume": pd.Series(dtype="float64"),
        })
    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], errors="coerce")
    for col in ("open", "high", "low", "close"):
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "volume" not in out.columns:
        out["volume"] = 0
    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0)
    out = out.dropna(subset=["timestamp", "open", "high", "low", "close"]).copy()
    out["timestamp"] = out["timestamp"].dt.floor("min")
    in_hours = (
        (out["timestamp"].dt.strftime("%H:%M") >= REGULAR_SESSION_START)
        & (out["timestamp"].dt.strftime("%H:%M") <= REGULAR_SESSION_END)
    )
    out = out[in_hours].copy()
    return (
        out.sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)[["timestamp", "open", "high", "low", "close", "volume"]]
    )


def read_dhan_intraday_json(path: Path) -> pd.DataFrame:
    raw = json.loads(path.read_text(encoding="utf-8"))
    data = raw.get("response", {})
    ts = data.get("timestamp", [])
    if not ts:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame({
        "timestamp": pd.to_datetime(ts, unit="s", utc=True).tz_convert("Asia/Kolkata").tz_localize(None),
        "open": data.get("open", []),
        "high": data.get("high", []),
        "low": data.get("low", []),
        "close": data.get("close", []),
        "volume": data.get("volume", [0] * len(ts)),
    })
    return _normalize_spot_frame(df)


def load_dhan_raw_symbol(raw_root: Path, symbol: str) -> pd.DataFrame:
    source = raw_root / "intraday" / symbol.upper()
    frames = [read_dhan_intraday_json(path) for path in sorted(source.glob("*.json"))] if source.exists() else []
    if not frames:
        return pd.DataFrame(columns=["timestamp", "open", "high", "low", "close", "volume"])
    return _normalize_spot_frame(pd.concat(frames, ignore_index=True))


def live_bars_to_frame(bars: list[dict[str, Any]]) -> pd.DataFrame:
    rows = []
    for bar in bars:
        # Live chart timestamps are display epochs: IST wall-clock time encoded
        # as UTC seconds so LightweightCharts labels 09:15 as 09:15.
        ts = pd.Timestamp(datetime.fromtimestamp(int(bar["time"]), timezone.utc).replace(tzinfo=None))
        rows.append({
            "timestamp": ts,
            "open": bar.get("open"),
            "high": bar.get("high"),
            "low": bar.get("low"),
            "close": bar.get("close"),
            "volume": bar.get("volume", 0),
        })
    return _normalize_spot_frame(pd.DataFrame(rows))


def session_coverage(symbol: str, df: pd.DataFrame, session_date: date) -> SessionCoverage:
    normalized = _normalize_spot_frame(df)
    session = normalized[normalized["timestamp"].dt.date == session_date].copy()
    duplicate_timestamps = int(session.duplicated("timestamp").sum())
    expected = expected_session_index(session_date)
    present = pd.DatetimeIndex(session["timestamp"]) if not session.empty else pd.DatetimeIndex([])
    missing = expected.difference(present)
    notes: list[str] = []
    if missing.empty and len(session) == EXPECTED_1MIN_ROWS:
        complete = True
    else:
        complete = False
        notes.append(f"rows={len(session)} expected={EXPECTED_1MIN_ROWS}")
        if len(missing):
            notes.append(f"missing_minutes={len(missing)}")
    return SessionCoverage(
        symbol=symbol.upper(),
        session_date=session_date,
        rows=len(session),
        duplicate_timestamps=duplicate_timestamps,
        missing_minutes=len(missing),
        first=None if session.empty else str(session["timestamp"].min()),
        last=None if session.empty else str(session["timestamp"].max()),
        complete=complete,
        notes=notes,
    )


def merge_static_csv(data_root: Path, symbol: str, new_rows: pd.DataFrame) -> tuple[Path, int, int]:
    sym = symbol.upper()
    spec = STATIC_SPOT_FILES[sym]
    path = data_root / spec.relative_path
    if not path.exists():
        raise FileNotFoundError(f"missing static spot CSV for {sym}: {path}")

    clean = _normalize_spot_frame(new_rows)
    existing = pd.read_csv(path)
    if spec.timestamp_col not in existing.columns:
        raise ValueError(f"{path} missing timestamp column {spec.timestamp_col!r}")
    existing = existing.rename(columns={spec.timestamp_col: "timestamp"})
    existing = _normalize_spot_frame(existing)
    before = len(existing)

    merged = (
        pd.concat([existing, clean], ignore_index=True)
        .sort_values("timestamp")
        .drop_duplicates("timestamp", keep="last")
        .reset_index(drop=True)
    )
    added = len(merged) - before
    out = merged.rename(columns={"timestamp": spec.timestamp_col})
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    out.to_csv(tmp, index=False)
    tmp.replace(path)
    return path, before, added


def append_live_session_if_complete(
    data_root: Path,
    session_date: date,
    live_bars_by_symbol: dict[str, list[dict[str, Any]]],
    symbols: list[str] | None = None,
) -> tuple[bool, list[SessionCoverage], list[Path]]:
    selected = [s.upper() for s in (symbols or list(live_bars_by_symbol))]
    frames = {sym: live_bars_to_frame(live_bars_by_symbol.get(sym, [])) for sym in selected}
    coverage = [session_coverage(sym, frames[sym], session_date) for sym in selected]
    if not coverage or not all(c.complete for c in coverage):
        return False, coverage, []
    written = [merge_static_csv(data_root, sym, frames[sym])[0] for sym in selected]
    return True, coverage, written

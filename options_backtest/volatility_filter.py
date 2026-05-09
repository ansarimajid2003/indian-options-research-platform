from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd


VIX_BUCKETS: tuple[tuple[float, float, str], ...] = (
    (float("-inf"), 10.0, "<10"),
    (10.0, 13.0, "10-13"),
    (13.0, 17.0, "13-17"),
    (17.0, 22.0, "17-22"),
    (22.0, 30.0, "22-30"),
    (30.0, float("inf"), ">30"),
)


@dataclass(frozen=True)
class VixObservation:
    timestamp: pd.Timestamp
    value: float
    bucket: str


class VixFilter:
    def __init__(
        self,
        path: str | Path,
        *,
        min_vix: float | None = None,
        max_vix: float | None = None,
        missing_policy: str = "skip",
    ) -> None:
        if missing_policy not in {"skip", "allow"}:
            raise ValueError("missing_policy must be 'skip' or 'allow'")
        self.path = Path(path)
        self.min_vix = min_vix
        self.max_vix = max_vix
        self.missing_policy = missing_policy
        self._series = self._load_series(self.path)

    @classmethod
    def from_series(
        cls,
        series: pd.Series,
        *,
        min_vix: float | None = None,
        max_vix: float | None = None,
        missing_policy: str = "skip",
    ) -> "VixFilter":
        obj = cls.__new__(cls)
        obj.path = Path("<memory>")
        obj.min_vix = min_vix
        obj.max_vix = max_vix
        obj.missing_policy = missing_policy
        if missing_policy not in {"skip", "allow"}:
            raise ValueError("missing_policy must be 'skip' or 'allow'")
        clean = series.dropna().astype(float).sort_index()
        clean.index = pd.DatetimeIndex(pd.to_datetime(clean.index))
        obj._series = clean
        return obj

    @staticmethod
    def _load_series(path: Path) -> pd.Series:
        df = pd.read_csv(path)
        df.columns = [str(c).lower() for c in df.columns]
        ts_col = None
        for candidate in ("timestamp", "datetime", "date_time", "time"):
            if candidate in df.columns:
                ts_col = candidate
                break
        if ts_col is None or "close" not in df.columns:
            raise ValueError(f"{path} must contain timestamp/datetime and close columns")
        df[ts_col] = pd.to_datetime(df[ts_col], errors="coerce")
        df["close"] = pd.to_numeric(df["close"], errors="coerce")
        df = df.dropna(subset=[ts_col, "close"]).sort_values(ts_col)
        if df.empty:
            return pd.Series(dtype=float)
        df = df.drop_duplicates(ts_col, keep="last")
        return pd.Series(df["close"].to_numpy(dtype=float), index=pd.DatetimeIndex(df[ts_col]))

    @staticmethod
    def bucket(value: float) -> str:
        for low, high, label in VIX_BUCKETS:
            if low <= value < high:
                return label
        return "unknown"

    def observation_at_or_before(self, timestamp: pd.Timestamp) -> VixObservation | None:
        if self._series.empty:
            return None
        ts = pd.Timestamp(timestamp)
        pos = self._series.index.searchsorted(ts, side="right") - 1
        if pos < 0:
            return None
        obs_ts = pd.Timestamp(self._series.index[pos])
        value = float(self._series.iloc[pos])
        return VixObservation(timestamp=obs_ts, value=value, bucket=self.bucket(value))

    def allows(self, timestamp: pd.Timestamp) -> tuple[bool, VixObservation | None]:
        obs = self.observation_at_or_before(timestamp)
        if obs is None:
            return self.missing_policy == "allow", None
        if self.min_vix is not None and obs.value < self.min_vix:
            return False, obs
        if self.max_vix is not None and obs.value >= self.max_vix:
            return False, obs
        return True, obs

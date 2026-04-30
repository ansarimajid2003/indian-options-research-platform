from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import pandas as pd

from .calendar import nearest_timestamp
from .schemas import Contract, OptionType


@dataclass
class ContractResolver:
    option_bars: pd.DataFrame
    spot_bars: pd.DataFrame
    strike_step: int = 50
    # Internal lookup: (strike, option_type_value) -> DataFrame indexed by timestamp
    _bars_cache: dict = field(default_factory=dict, init=False, repr=False)
    _spot_index: pd.Series = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.option_bars = self.option_bars.copy()
        self.spot_bars = self.spot_bars.copy()
        self.option_bars["timestamp"] = pd.to_datetime(self.option_bars["timestamp"])
        self.spot_bars["timestamp"] = pd.to_datetime(self.spot_bars["timestamp"])
        # Build a timestamp-indexed lookup per (strike, option_type) for O(1) bar access
        self._bars_cache = {}
        for key, grp in self.option_bars.groupby(["strike", "option_type"], observed=True):
            self._bars_cache[key] = grp.set_index("timestamp").sort_index()
        self._spot_index = self.spot_bars.set_index("timestamp").sort_index()

    @property
    def expiry(self) -> date:
        value = self.option_bars["expiry"].iloc[0]
        return pd.Timestamp(value).date() if not isinstance(value, date) else value

    def spot_at_or_after(self, timestamp: pd.Timestamp) -> tuple[pd.Timestamp, float] | None:
        ts = nearest_timestamp(self._spot_index.index, timestamp)
        if ts is None:
            return None
        return ts, float(self._spot_index.loc[ts, "close"])

    def atm_strike(self, timestamp: pd.Timestamp) -> int:
        found = self.spot_at_or_after(timestamp)
        if found is None:
            raise ValueError(f"No spot bar at or after {timestamp}")
        _, spot = found
        return int(round(spot / self.strike_step) * self.strike_step)

    def available_strikes(self, option_type: OptionType | None = None) -> list[int]:
        df = self.option_bars
        if option_type is not None:
            df = df[df["option_type"] == option_type.value]
        return sorted(int(x) for x in df["strike"].dropna().unique())

    def resolve(self, strike: int, option_type: OptionType) -> Contract:
        df = self.option_bars[
            (self.option_bars["strike"] == strike) & (self.option_bars["option_type"] == option_type.value)
        ]
        if df.empty:
            raise KeyError(f"Missing contract {self.expiry} {strike}{option_type.value}")
        ticker = str(df["ticker"].dropna().iloc[0])
        return Contract(expiry=self.expiry, strike=strike, option_type=option_type, ticker=ticker)

    def resolve_atm_offset(self, timestamp: pd.Timestamp, offset_steps: int, option_type: OptionType) -> Contract:
        return self.resolve(self.atm_strike(timestamp) + offset_steps * self.strike_step, option_type)

    def nearest_premium(self, timestamp: pd.Timestamp, option_type: OptionType, target_premium: float) -> Contract:
        bars = self.option_bars[
            (self.option_bars["timestamp"] == timestamp) & (self.option_bars["option_type"] == option_type.value)
        ].copy()
        if bars.empty:
            raise KeyError(f"No option bars at {timestamp} for {option_type.value}")
        bars["distance"] = (bars["close"] - target_premium).abs()
        row = bars.sort_values(["distance", "strike"]).iloc[0]
        return self.resolve(int(row["strike"]), option_type)

    def bars_for(self, contract: Contract) -> pd.DataFrame:
        key = (contract.strike, contract.option_type.value)
        indexed = self._bars_cache.get(key)
        if indexed is None:
            return pd.DataFrame()
        return indexed.reset_index()

    def bar_at(self, contract: Contract, timestamp: pd.Timestamp) -> pd.Series | None:
        """O(log n) single-bar lookup by contract and timestamp."""
        key = (contract.strike, contract.option_type.value)
        indexed = self._bars_cache.get(key)
        if indexed is None or timestamp not in indexed.index:
            return None
        row = indexed.loc[timestamp]
        # Handle duplicate timestamps — take the first row
        return row.iloc[0] if isinstance(row, pd.DataFrame) else row

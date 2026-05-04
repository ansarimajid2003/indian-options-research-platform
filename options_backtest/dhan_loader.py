from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .calendar import (
    combine_date_time,
    expiry_on_or_after,
    get_instrument_spec,
    is_trading_day,
    nearest_timestamp,
    next_trading_day,
)
from .engine import BacktestEngine
from .reports import build_result
from .schemas import BacktestConfig, BacktestResult, Contract, OptionType

logger = logging.getLogger(__name__)

_OFFSET_NAMES: list[str] = (
    ["ATM"]
    + [f"ATMp{i}" for i in range(1, 11)]
    + [f"ATMm{i}" for i in range(1, 11)]
)


def _offset_to_key(offset: int) -> str:
    if offset == 0:
        return "ATM"
    if offset > 0:
        return f"ATMp{offset}"
    return f"ATMm{-offset}"


def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    if pd.api.types.is_datetime64_any_dtype(df["timestamp"]):
        if df["timestamp"].dt.tz is not None:
            df["timestamp"] = df["timestamp"].dt.tz_localize(None)
    else:
        df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False).dt.tz_localize(None)
    return df


def _add_expiry_column(df: pd.DataFrame, symbol: str, expiry_type: str, trading_dates: list[date]) -> pd.DataFrame:
    df = df.copy()
    df["trade_date"] = df["timestamp"].dt.date
    expiry_by_date = {
        day: expiry_on_or_after(symbol, day, expiry_type=expiry_type, min_dte=0, trading_dates=trading_dates)
        for day in df["trade_date"].unique()
    }
    df["expiry"] = df["trade_date"].map(expiry_by_date)
    return df


@dataclass
class DhanOptionData:
    symbol: str
    calls: dict[str, pd.DataFrame]
    puts: dict[str, pd.DataFrame]
    # Full cache: (strike, option_type.value) -> timestamp-indexed DataFrame
    bars_cache: dict[tuple[int, str], pd.DataFrame] = field(default_factory=dict)
    # Date-bucketed cache: date -> {(strike, option_type.value) -> DataFrame}
    # Enables O(1) per-date resolver construction
    bars_cache_by_date: dict[date, dict[tuple[int, str], pd.DataFrame]] = field(default_factory=dict)
    spot_bars_by_date: dict[date, pd.DataFrame] = field(default_factory=dict)
    spot_index: pd.DataFrame = field(default_factory=pd.DataFrame)
    spot_bars: pd.DataFrame = field(default_factory=pd.DataFrame)
    trading_dates: list[date] = field(default_factory=list)
    atm_timestamps_by_date: dict[date, pd.DatetimeIndex] = field(default_factory=dict)
    next_trading_date: dict[date, date] = field(default_factory=dict)
    offset_strike_by_date: dict[tuple[str, str, date, date], int] = field(default_factory=dict)
    atm_strike_by_date: dict[tuple[date, date], int] = field(default_factory=dict)


def _build_bars_cache(calls: dict[str, pd.DataFrame], puts: dict[str, pd.DataFrame]) -> dict[tuple[int, str], pd.DataFrame]:
    """Build (strike, option_type.value) → timestamp-indexed DataFrame once for all data."""
    cache: dict[tuple[int, str], list[pd.DataFrame]] = {}
    for option_type, side_dict in ((OptionType.CALL, calls), (OptionType.PUT, puts)):
        for df in side_dict.values():
            if df.empty:
                continue
            # Each row knows its own absolute strike — group by strike
            for strike_val, sdf in df.groupby("strike"):
                key = (int(strike_val), option_type.value)
                indexed = sdf.set_index("timestamp").sort_index()
                if key not in cache:
                    cache[key] = [indexed]
                else:
                    cache[key].append(indexed)
    # Merge segments for each key
    result: dict[tuple[int, str], pd.DataFrame] = {}
    for key, frames in cache.items():
        merged = pd.concat(frames).sort_index()
        merged = merged[~merged.index.duplicated(keep="first")]
        result[key] = merged
    return result


def load_dhan_data(dhan_root: str | Path, expiry_type: str = "week", symbol: str = "NIFTY") -> DhanOptionData:
    root = Path(dhan_root)
    spec = get_instrument_spec(symbol)
    base = root / spec.dhan_folder / expiry_type / "expiry_code_1"
    calls: dict[str, pd.DataFrame] = {}
    puts: dict[str, pd.DataFrame] = {}
    for key in _OFFSET_NAMES:
        for side_dict, side_name in ((calls, "call"), (puts, "put")):
            p = base / side_name / f"{key}.parquet"
            if not p.exists():
                continue
            df = pd.read_parquet(p)
            df = _strip_tz(df)
            df["timestamp"] = pd.to_datetime(df["timestamp"])
            side_dict[key] = df

    logger.info("Loaded Dhan data: %d call offsets, %d put offsets — building cache...", len(calls), len(puts))

    atm_df = calls.get("ATM", pd.DataFrame())
    if not atm_df.empty:
        spot_series = atm_df.set_index("timestamp")["spot"].sort_index()
        # Proxy spot_index: low=close=ATM mid-price.  DhanBacktestEngine.run()
        # overrides this with canonical spot OHLC when available so that
        # spot-level stop detection uses real intraday lows.
        spot_index = pd.DataFrame({"low": spot_series, "close": spot_series})
        spot_bars_df = atm_df[["timestamp", "spot"]].copy()
        spot_bars_df["open"] = spot_bars_df["spot"]
        spot_bars_df["high"] = spot_bars_df["spot"]
        spot_bars_df["low"] = spot_bars_df["spot"]
        spot_bars_df["close"] = spot_bars_df["spot"]
        spot_bars_df = spot_bars_df.drop(columns=["spot"]).sort_values("timestamp").reset_index(drop=True)
        # Filter to confirmed NSE trading days — removes spurious weekend entries
        # that occasionally appear in Dhan ATM JSON feeds.
        raw_dates = sorted(atm_df["timestamp"].dt.date.unique().tolist())
        trading_dates = [d for d in raw_dates if is_trading_day(d)]
    else:
        spot_index = pd.DataFrame(columns=["low", "close"])
        spot_bars_df = pd.DataFrame(columns=["timestamp", "open", "high", "low", "close"])
        trading_dates = []

    spot_bars_by_date: dict[date, pd.DataFrame] = (
        {d: grp.reset_index(drop=True) for d, grp in spot_bars_df.groupby(spot_bars_df["timestamp"].dt.date)}
        if not spot_bars_df.empty else {}
    )

    bars_cache: dict[tuple[int, str], pd.DataFrame] = {}
    if trading_dates:
        calls = {key: _add_expiry_column(df, spec.symbol, expiry_type, trading_dates) for key, df in calls.items()}
        puts = {key: _add_expiry_column(df, spec.symbol, expiry_type, trading_dates) for key, df in puts.items()}
        bars_cache = _build_bars_cache(calls, puts)

    atm_timestamps_by_date: dict[date, pd.DatetimeIndex] = {}
    atm_df = calls.get("ATM", pd.DataFrame())
    if not atm_df.empty and "trade_date" in atm_df.columns:
        for d, grp in atm_df.groupby("trade_date", sort=True):
            atm_timestamps_by_date[d] = pd.DatetimeIndex(grp["timestamp"].sort_values())
    next_trading_date = {day: trading_dates[i + 1] for i, day in enumerate(trading_dates[:-1])}
    offset_strike_by_date: dict[tuple[str, str, date, date], int] = {}
    for side_name, side_dict in (("call", calls), ("put", puts)):
        for key, df in side_dict.items():
            if df.empty or "trade_date" not in df.columns:
                continue
            compact = df.drop_duplicates(["trade_date", "expiry"])[["trade_date", "expiry", "strike"]]
            for row in compact.itertuples(index=False):
                offset_strike_by_date[(side_name, key, row.trade_date, row.expiry)] = int(row.strike)
    atm_strike_by_date = {
        (trade_date, expiry): strike
        for (side_name, key, trade_date, expiry), strike in offset_strike_by_date.items()
        if side_name == "call" and key == "ATM"
    }

    # Build date-bucketed index: date -> {(strike, otype) -> day-slice DataFrame}
    bars_cache_by_date: dict[date, dict[tuple[int, str], pd.DataFrame]] = {}
    for key, df in bars_cache.items():
        group_key = df["trade_date"] if "trade_date" in df.columns else df.index.date
        for d, day_df in df.groupby(group_key):
            if d not in bars_cache_by_date:
                bars_cache_by_date[d] = {}
            bars_cache_by_date[d][key] = day_df

    logger.info("Cache built: %d (strike, type) pairs, %d trading dates", len(bars_cache), len(trading_dates))
    return DhanOptionData(
        symbol=spec.symbol,
        calls=calls,
        puts=puts,
        bars_cache=bars_cache,
        bars_cache_by_date=bars_cache_by_date,
        spot_bars_by_date=spot_bars_by_date,
        spot_index=spot_index,
        spot_bars=spot_bars_df,
        trading_dates=trading_dates,
        atm_timestamps_by_date=atm_timestamps_by_date,
        next_trading_date=next_trading_date,
        offset_strike_by_date=offset_strike_by_date,
        atm_strike_by_date=atm_strike_by_date,
    )


class DhanContractResolver:
    """
    Resolver backed by pre-built DhanOptionData caches.
    _bars_cache is sliced to [trade_date, exit_date] so _find_exit only iterates
    the relevant session, not the full 5-year dataset.
    """

    def __init__(self, data: DhanOptionData, trade_date: date, exit_date: date | None = None, expiry: date | None = None) -> None:
        self._data = data
        self._trade_date = trade_date
        self.expiry = expiry or expiry_on_or_after(data.symbol, trade_date, expiry_type="week")
        window_end = exit_date if exit_date is not None else trade_date
        # Merge per-date buckets for the window — O(days_in_window) dict lookups
        self._window_dates = [day for day in data.trading_dates if trade_date <= day <= window_end]
        self._bars_cache: dict[tuple[int, str], pd.DataFrame] = {}
        self._spot_index = data.spot_index.loc[
            pd.Timestamp(trade_date):pd.Timestamp(window_end) + pd.Timedelta(days=1)
        ]
        _spot_frames = [data.spot_bars_by_date[d] for d in self._window_dates if d in data.spot_bars_by_date]
        self.spot_bars = pd.concat(_spot_frames, ignore_index=True) if _spot_frames else pd.DataFrame(columns=["timestamp", "open", "close"])

    def _contract_frame(self, key: tuple[int, str]) -> pd.DataFrame | None:
        cached = self._bars_cache.get(key)
        if cached is not None:
            return cached

        frames: list[pd.DataFrame] = []
        for d in self._window_dates:
            df = self._data.bars_cache_by_date.get(d, {}).get(key)
            if df is None or df.empty:
                continue
            if "expiry" in df.columns:
                df = df[df["expiry"] == self.expiry]
            if not df.empty:
                frames.append(df)
        if not frames:
            return None
        merged = pd.concat(frames).sort_index()
        self._bars_cache[key] = merged
        return merged

    def atm_strike(self, timestamp: pd.Timestamp) -> int:
        strike = self._data.atm_strike_by_date.get((timestamp.date(), self.expiry))
        if strike is None:
            raise ValueError(f"No ATM bar on {timestamp.date()} expiry {self.expiry}")
        return strike

    def resolve_atm_offset(self, timestamp: pd.Timestamp, offset_steps: int, option_type: OptionType) -> Contract:
        key = _offset_to_key(offset_steps)
        side_name = "call" if option_type == OptionType.CALL else "put"
        strike = self._data.offset_strike_by_date.get((side_name, key, timestamp.date(), self.expiry))
        if strike is None:
            raise KeyError(f"No Dhan bar for {key} {option_type.value} on {timestamp.date()} expiry {self.expiry}")
        ticker = f"{self._data.symbol}_DHAN_{strike}{option_type.value}"
        return Contract(expiry=self.expiry, strike=strike, option_type=option_type, ticker=ticker)

    def bar_at(self, contract: Contract, timestamp: pd.Timestamp) -> pd.Series | None:
        key = (contract.strike, contract.option_type.value)
        indexed = self._contract_frame(key)
        if indexed is None or timestamp not in indexed.index:
            return None
        row = indexed.loc[timestamp]
        return row.iloc[0] if isinstance(row, pd.DataFrame) else row

    def bars_for(self, contract: Contract) -> pd.DataFrame:
        key = (contract.strike, contract.option_type.value)
        indexed = self._contract_frame(key)
        if indexed is None:
            return pd.DataFrame()
        # _contract_frame already filters by self.expiry and _window_dates ([trade_date, exit_date]).
        # A hardcoded 6-day lookback would cut off all trades entered >6 days before a monthly expiry.
        return indexed.reset_index()


CANONICAL_SPOT_PATH = "data/processed/spot/nifty50_1min_CANONICAL.csv"


def _load_canonical_spot(spot_path: str | Path) -> pd.DataFrame:
    """Load NIFTY 50 1-min canonical spot CSV with real OHLC — used for 3 PM signal detection."""
    df = pd.read_csv(spot_path)
    # Normalise column names — file may use Title or lower case
    df.columns = [c.lower() for c in df.columns]
    for alias in [("datetime", "timestamp"), ("date_time", "timestamp"), ("time", "timestamp")]:
        if alias[0] in df.columns and alias[1] not in df.columns:
            df = df.rename(columns={alias[0]: alias[1]})
    df["timestamp"] = pd.to_datetime(df["timestamp"], errors="coerce")
    df = df.dropna(subset=["timestamp"]).sort_values("timestamp").reset_index(drop=True)
    for col in ["open", "high", "low", "close"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


class DhanBacktestEngine(BacktestEngine):
    def __init__(
        self,
        config: BacktestConfig | None = None,
        liquidity_config: Any | None = None,
        dhan_root: str | Path | None = None,
        expiry_type: str = "week",
        spot_path: str | Path | None = None,
    ) -> None:
        super().__init__(config, liquidity_config)
        self.spec = get_instrument_spec(self.config.symbol)
        if spot_path is None and self.spec.spot_csv and self.config.spot_csv == CANONICAL_SPOT_PATH:
            self.config = replace(self.config, spot_csv=self.spec.spot_csv)
        self.dhan_root = Path(dhan_root or "data/processed/options/dhan")
        self.expiry_type = expiry_type
        self.spot_path = Path(spot_path or self.spec.spot_csv or CANONICAL_SPOT_PATH)

    def run(
        self,
        strategy: Any,
        from_date: str | None = None,
        to_date: str | None = None,
        **kwargs: Any,
    ) -> BacktestResult:
        data = kwargs.get("data")
        if data is None:
            data = load_dhan_data(self.dhan_root, self.expiry_type, self.config.symbol)

        if not data.trading_dates:
            return build_result(self.config, [])

        # Load canonical spot for 3 PM signal strategies — real OHLC bars
        canonical_spot: pd.DataFrame | None = None
        canonical_spot_by_date: dict[date, pd.DataFrame] = {}
        if self.spot_path.exists():
            canonical_spot = _load_canonical_spot(self.spot_path)
            canonical_spot_by_date = {
                d: grp.reset_index(drop=True)
                for d, grp in canonical_spot.groupby(canonical_spot["timestamp"].dt.date, sort=True)
            }
        else:
            logger.warning("Canonical spot file not found at %s — 3PM strategies will fail", self.spot_path)

        all_dates = data.trading_dates
        if from_date:
            fd = datetime.strptime(from_date, "%Y-%m-%d").date()
            all_dates = [d for d in all_dates if d >= fd]
        if to_date:
            td = datetime.strptime(to_date, "%Y-%m-%d").date()
            all_dates = [d for d in all_dates if d <= td]

        if not all_dates:
            return build_result(self.config, [])

        from .schemas import Trade
        trades: list[Trade] = []

        for trade_date in all_dates:
            entry_target = combine_date_time(trade_date, self.config.entry_time)

            day_ts_index = data.atm_timestamps_by_date.get(trade_date, pd.DatetimeIndex([]))
            entry_ts = nearest_timestamp(day_ts_index, entry_target)
            if entry_ts is None or entry_ts.date() != trade_date:
                continue

            if self.config.next_day_exit:
                # Use next_trading_day() from the calendar — avoids using
                # data.next_trading_date which may point to a spurious weekend
                # entry that leaked into the Dhan feed before filtering.
                exit_date = next_trading_day(trade_date)
                exit_target = combine_date_time(exit_date, self.config.exit_time)
            else:
                exit_date = trade_date
                exit_target = combine_date_time(trade_date, self.config.exit_time)

            # Fast pre-filter: skip dates where strategy cannot enter (avoids resolver construction)
            can_enter_fn = getattr(strategy, "can_enter", None)
            if can_enter_fn is not None and not can_enter_fn(trade_date, canonical_spot_by_date):
                continue

            min_dte = max(self.config.min_dte, 1 if self.config.next_day_exit else 0)
            expiry = expiry_on_or_after(
                self.config.symbol,
                trade_date,
                expiry_type=self.expiry_type,
                min_dte=min_dte,
                trading_dates=data.trading_dates,
            )
            resolver = DhanContractResolver(data, trade_date, exit_date, expiry)

            # Override spot_bars with canonical OHLC for 3 PM signal detection.
            # Also update resolver._spot_index with real OHLC so that spot-level
            # stop detection in _find_exit uses actual intraday lows, not the
            # ATM-mid proxy where low == close.
            # Only override when canonical data is actually available for the window;
            # when it's missing, keep the ATM-proxy spot_bars already set by the resolver.
            if canonical_spot is not None:
                frames = [
                    canonical_spot_by_date[d]
                    for d in (trade_date, exit_date)
                    if d in canonical_spot_by_date
                ]
                if frames:
                    canonical_window = pd.concat(frames, ignore_index=True)
                    # _spot_index: canonical-only — real OHLC lows for stop detection
                    if {"low", "close"}.issubset(canonical_window.columns):
                        resolver._spot_index = canonical_window.set_index("timestamp").sort_index()[["low", "close"]]
                    # spot_bars: canonical + ATM-proxy fill for timestamps canonical
                    # doesn't cover (e.g. partial-day files missing the morning session)
                    canonical_ts = set(pd.to_datetime(canonical_window["timestamp"]))
                    atm_proxy = resolver.spot_bars
                    if not atm_proxy.empty:
                        atm_extra = atm_proxy[~pd.to_datetime(atm_proxy["timestamp"]).isin(canonical_ts)]
                        if not atm_extra.empty:
                            extra_cols = [c for c in ["timestamp", "open", "high", "low", "close"] if c in atm_extra.columns]
                            resolver.spot_bars = pd.concat(
                                [canonical_window, atm_extra[extra_cols]],
                                ignore_index=True,
                            ).sort_values("timestamp").reset_index(drop=True)
                        else:
                            resolver.spot_bars = canonical_window
                    else:
                        resolver.spot_bars = canonical_window

            trade = self._execute_trade(
                expiry_dir=Path(trade_date.strftime("%Y%m%d")),
                entry_ts=entry_ts,
                exit_target=exit_target,
                strategy=strategy,
                resolver=resolver,
                option_bars=pd.DataFrame(),
                spot_bars=resolver.spot_bars,
                expiry=expiry,
            )
            if trade is not None:
                trades.append(trade)

        return build_result(self.config, trades)

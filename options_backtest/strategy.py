from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

import pandas as pd

from .contract_resolver import ContractResolver
from .schemas import Leg, OptionType, Side


def _first_bar_at_or_after(spot_df: pd.DataFrame, trade_date: date, clock_time: time) -> pd.Series | None:
    signal_ts = pd.Timestamp(f"{trade_date.isoformat()} {clock_time.hour:02d}:{clock_time.minute:02d}:00")
    eligible = spot_df[spot_df["timestamp"] >= signal_ts].sort_values("timestamp")
    if eligible.empty:
        return None
    row = eligible.iloc[0]
    # Only return bars that are on the same date we asked for
    if pd.Timestamp(row["timestamp"]).date() != trade_date:
        return None
    return row


@dataclass(frozen=True)
class StrategyContext:
    timestamp: pd.Timestamp
    resolver: ContractResolver


class OptionStrategy:
    name = "OptionStrategy"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        raise NotImplementedError


@dataclass(frozen=True)
class SingleLegOption(OptionStrategy):
    option_type: OptionType = OptionType.CALL
    side: Side = Side.BUY
    atm_offset: int = 0
    lots: int = 1
    name: str = "SingleLegOption"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        contract = context.resolver.resolve_atm_offset(context.timestamp, self.atm_offset, self.option_type)
        return [Leg(contract=contract, side=self.side, lots=self.lots)]


@dataclass(frozen=True)
class ShortStraddle(OptionStrategy):
    lots: int = 1
    name: str = "ShortStraddle"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        return [
            Leg(context.resolver.resolve_atm_offset(context.timestamp, 0, OptionType.CALL), Side.SELL, self.lots),
            Leg(context.resolver.resolve_atm_offset(context.timestamp, 0, OptionType.PUT), Side.SELL, self.lots),
        ]


@dataclass(frozen=True)
class ShortStrangle(OptionStrategy):
    call_offset: int = 2
    put_offset: int = -2
    lots: int = 1
    name: str = "ShortStrangle"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        return [
            Leg(context.resolver.resolve_atm_offset(context.timestamp, self.call_offset, OptionType.CALL), Side.SELL, self.lots),
            Leg(context.resolver.resolve_atm_offset(context.timestamp, self.put_offset, OptionType.PUT), Side.SELL, self.lots),
        ]


@dataclass(frozen=True)
class ThreePMDirectional(OptionStrategy):
    """
    Baseline 3 PM candle strategy.

    Reads the spot bar whose timestamp falls at or after signal_time (default 15:00).
    If that candle is bullish (close > open) → buy ATM call.
    If bearish (close < open) → buy ATM put.
    Doji candles (close == open) → no trade (raises KeyError so engine skips).
    """

    signal_time: time = time(15, 0)
    lots: int = 1
    name: str = "ThreePMDirectional"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        spot = context.resolver.spot_bars.copy()
        spot["timestamp"] = pd.to_datetime(spot["timestamp"])
        signal_ts = pd.Timestamp(
            context.timestamp.date().isoformat() + f" {self.signal_time.hour:02d}:{self.signal_time.minute:02d}:00"
        )
        eligible = spot[spot["timestamp"] >= signal_ts].sort_values("timestamp")
        if eligible.empty:
            raise KeyError(f"No spot bar at or after {signal_ts}")
        candle = eligible.iloc[0]
        candle_open = float(candle["open"])
        candle_close = float(candle["close"])

        if candle_close > candle_open:
            option_type = OptionType.CALL
        elif candle_close < candle_open:
            option_type = OptionType.PUT
        else:
            raise KeyError(f"Doji candle at {candle['timestamp']}, skipping")

        contract = context.resolver.resolve_atm_offset(context.timestamp, 0, option_type)
        return [Leg(contract=contract, side=Side.BUY, lots=self.lots)]


@dataclass(frozen=True)
class IronCondor(OptionStrategy):
    short_call_offset: int = 2
    long_call_offset: int = 4
    short_put_offset: int = -2
    long_put_offset: int = -4
    lots: int = 1
    name: str = "IronCondor"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        return [
            Leg(context.resolver.resolve_atm_offset(context.timestamp, self.short_call_offset, OptionType.CALL), Side.SELL, self.lots),
            Leg(context.resolver.resolve_atm_offset(context.timestamp, self.long_call_offset, OptionType.CALL), Side.BUY, self.lots),
            Leg(context.resolver.resolve_atm_offset(context.timestamp, self.short_put_offset, OptionType.PUT), Side.SELL, self.lots),
            Leg(context.resolver.resolve_atm_offset(context.timestamp, self.long_put_offset, OptionType.PUT), Side.BUY, self.lots),
        ]


def _check_three_pm_setup(context: StrategyContext) -> tuple[float, float]:
    """
    Returns (three_pm_close, three_fifteen_close) when the setup conditions hold:
      - 3 PM candle is bullish (close > open)
      - 3:15 candle is bearish (close < open)
    Raises KeyError if either condition fails or bars are missing.
    """
    spot = context.resolver.spot_bars.copy()
    spot["timestamp"] = pd.to_datetime(spot["timestamp"])
    trade_date = context.timestamp.date()

    three_pm_bar = _first_bar_at_or_after(spot, trade_date, time(15, 0))
    if three_pm_bar is None:
        raise KeyError("No 3PM bar")
    three_pm_open = float(three_pm_bar["open"])
    three_pm_close = float(three_pm_bar["close"])
    if three_pm_close <= three_pm_open:
        raise KeyError("3PM not bullish")

    three_fifteen_bar = _first_bar_at_or_after(spot, trade_date, time(15, 15))
    if three_fifteen_bar is None:
        raise KeyError("No 3:15 bar")
    three_fifteen_close = float(three_fifteen_bar["close"])
    three_fifteen_open = float(three_fifteen_bar["open"])
    if three_fifteen_close >= three_fifteen_open:
        raise KeyError("3:15 not bearish")

    return three_pm_close, three_fifteen_close


@dataclass(frozen=True)
class ThreePMV2Put(OptionStrategy):
    """
    Entry: 3PM bullish + 3:15 bearish → BUY ATM PUT at 15:16.
    Direction logic: v2 edge study shows 40% up-day rate for this setup → PUT has ~60% direction win rate.
    No spot-level stop needed.
    """

    lots: int = 1
    name: str = "ThreePMV2Put"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        _check_three_pm_setup(context)
        contract = context.resolver.resolve_atm_offset(context.timestamp, 0, OptionType.PUT)
        return [Leg(contract=contract, side=Side.BUY, lots=self.lots)]

    def get_spot_stop_level(self, context: StrategyContext) -> float | None:
        return None


@dataclass(frozen=True)
class ThreePMV2CallLevelStop(OptionStrategy):
    """
    Entry: 3PM bullish + 3:15 bearish → BUY ATM CALL at 15:16.
    Spot-level stop: if next-day spot touches or falls below the 3PM close price, exit immediately.
    Hypothesis: 74.8% up-day rate when level NOT touched; early stop limits loss on the 73.4% touch cases.
    """

    lots: int = 1
    name: str = "ThreePMV2CallLevelStop"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        _check_three_pm_setup(context)
        contract = context.resolver.resolve_atm_offset(context.timestamp, 0, OptionType.CALL)
        return [Leg(contract=contract, side=Side.BUY, lots=self.lots)]

    def get_spot_stop_level(self, context: StrategyContext) -> float | None:
        three_pm_close, _ = _check_three_pm_setup(context)
        return three_pm_close

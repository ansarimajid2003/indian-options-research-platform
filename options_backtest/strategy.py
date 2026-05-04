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

    def can_enter(self, trade_date: date, spot_by_date: dict) -> bool:
        return True


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
    min_leg_premium: float = 0.0
    name: str = "ShortStrangle"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        call_contract = context.resolver.resolve_atm_offset(context.timestamp, self.call_offset, OptionType.CALL)
        put_contract = context.resolver.resolve_atm_offset(context.timestamp, self.put_offset, OptionType.PUT)
        if self.min_leg_premium > 0.0:
            for contract in (call_contract, put_contract):
                bar = context.resolver.bar_at(contract, context.timestamp)
                if bar is None or float(bar["close"]) < self.min_leg_premium:
                    raise KeyError(f"leg premium below minimum {self.min_leg_premium}")
        return [
            Leg(call_contract, Side.SELL, self.lots),
            Leg(put_contract, Side.SELL, self.lots),
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

    def can_enter(self, trade_date: date, spot_by_date: dict) -> bool:
        day_df = spot_by_date.get(trade_date)
        if day_df is None:
            return False
        bar = _first_bar_at_or_after(day_df, trade_date, self.signal_time)
        if bar is None:
            return False
        return float(bar["close"]) != float(bar["open"])


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

    def can_enter(self, trade_date: date, spot_by_date: dict) -> bool:
        day_df = spot_by_date.get(trade_date)
        if day_df is None:
            return False
        three_pm = _first_bar_at_or_after(day_df, trade_date, time(15, 0))
        if three_pm is None or float(three_pm["close"]) <= float(three_pm["open"]):
            return False
        three_fifteen = _first_bar_at_or_after(day_df, trade_date, time(15, 15))
        return three_fifteen is not None and float(three_fifteen["close"]) < float(three_fifteen["open"])


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

    def can_enter(self, trade_date: date, spot_by_date: dict) -> bool:
        day_df = spot_by_date.get(trade_date)
        if day_df is None:
            return False
        three_pm = _first_bar_at_or_after(day_df, trade_date, time(15, 0))
        if three_pm is None or float(three_pm["close"]) <= float(three_pm["open"]):
            return False
        three_fifteen = _first_bar_at_or_after(day_df, trade_date, time(15, 15))
        return three_fifteen is not None and float(three_fifteen["close"]) < float(three_fifteen["open"])


@dataclass(frozen=True)
class ExpiryDayStraddle(OptionStrategy):
    """
    Buy ATM straddle (call + put) at 3:00 PM on NIFTY expiry day only.

    Expiry-day filter uses nifty_expiry_on_or_after with min_dte=0 so it
    correctly handles the Thursday→Tuesday transition that took effect on
    2025-09-02 (NIFTY_FIRST_TUESDAY_EXPIRY in calendar.py).

    Exit is controlled entirely by BacktestConfig:
      - trail_trigger_pct + trail_stop_pct  -> trailing stop (primary)
      - target_profit_pct                   -> hard take-profit
      - exit_time = 15:28                   -> hard time stop
    """

    lots: int = 1
    symbol: str = "NIFTY"
    name: str = "ExpiryDayStraddle"

    def _true_atm_offsets(self, context: StrategyContext) -> tuple[int, int]:
        """
        Return (call_offset, put_offset) that land on the same absolute strike.

        Dhan call ATM and put ATM are often one 50-pt strike apart (call ATM is
        nearest OTM call, put ATM is nearest OTM put).  Naively using the same
        offset for both sides yields a mismatched straddle.  This method:
          1. Finds the call offset whose absolute strike is closest to spot.
          2. Finds the put offset whose absolute strike equals that same strike
             (or is closest to it, as a fallback).
        """
        resolver = context.resolver
        ts = context.timestamp
        trade_date = ts.date()

        offset_strike_by_date = getattr(getattr(resolver, "_data", None), "offset_strike_by_date", None)
        if not offset_strike_by_date:
            return 0, 0

        spot_bars = getattr(resolver, "spot_bars", None)
        if spot_bars is None or spot_bars.empty:
            return 0, 0

        spot_df = spot_bars.copy()
        spot_df["timestamp"] = pd.to_datetime(spot_df["timestamp"])
        eligible = spot_df[spot_df["timestamp"] <= ts].sort_values("timestamp")
        if eligible.empty:
            return 0, 0
        current_spot = float(eligible.iloc[-1]["close"])

        expiry = resolver.expiry

        def _best_offset(side: str, target_strike: float) -> int:
            best_off = 0
            best_dist = float("inf")
            for n in range(-10, 11):
                if n == 0:
                    key = "ATM"
                elif n > 0:
                    key = f"ATMp{n}"
                else:
                    key = f"ATMm{-n}"
                strike = offset_strike_by_date.get((side, key, trade_date, expiry))
                if strike is None:
                    continue
                dist = abs(strike - target_strike)
                if dist < best_dist:
                    best_dist = dist
                    best_off = n
            return best_off

        call_offset = _best_offset("call", current_spot)

        # Resolve the actual call strike, then match puts to that same strike.
        call_key = "ATM" if call_offset == 0 else (f"ATMp{call_offset}" if call_offset > 0 else f"ATMm{-call_offset}")
        call_strike = offset_strike_by_date.get(("call", call_key, trade_date, expiry), current_spot)
        put_offset = _best_offset("put", call_strike)

        return call_offset, put_offset

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        call_offset, put_offset = self._true_atm_offsets(context)
        return [
            Leg(context.resolver.resolve_atm_offset(context.timestamp, call_offset, OptionType.CALL), Side.BUY, self.lots),
            Leg(context.resolver.resolve_atm_offset(context.timestamp, put_offset, OptionType.PUT), Side.BUY, self.lots),
        ]

    def can_enter(self, trade_date: date, spot_by_date: dict) -> bool:
        # Local import avoids circular import: calendar imports nothing from strategy
        from .calendar import expiry_on_or_after
        expiry = expiry_on_or_after(self.symbol, trade_date, expiry_type="week", min_dte=0)
        return expiry == trade_date


@dataclass(frozen=True)
class ExpiryDayStrangle(OptionStrategy):
    """
    Buy OTM call + OTM put at 3:00 PM on NIFTY expiry day only.

    call_otm_steps / put_otm_steps: how many 50-pt NIFTY strikes above/below
    the true ATM (computed dynamically from spot at entry time) each leg sits.
      1 → Strangle-1 (nearest OTM strike on each side)
      2 → Strangle-2 (two strikes OTM on each side)

    The offset search maps the target absolute strike to whichever Dhan rolling
    offset is closest, so the resulting legs are always balanced around spot.
    """

    call_otm_steps: int = 1
    put_otm_steps: int = 1
    lots: int = 1
    symbol: str = "NIFTY"
    name: str = "ExpiryDayStrangle"

    def _find_offsets(self, context: StrategyContext) -> tuple[int, int]:
        resolver = context.resolver
        ts = context.timestamp
        trade_date = ts.date()

        offset_strike_by_date = getattr(getattr(resolver, "_data", None), "offset_strike_by_date", None)
        if not offset_strike_by_date:
            return self.call_otm_steps, -self.put_otm_steps

        spot_bars = getattr(resolver, "spot_bars", None)
        if spot_bars is None or spot_bars.empty:
            return self.call_otm_steps, -self.put_otm_steps

        spot_df = spot_bars.copy()
        spot_df["timestamp"] = pd.to_datetime(spot_df["timestamp"])
        eligible = spot_df[spot_df["timestamp"] <= ts].sort_values("timestamp")
        if eligible.empty:
            return self.call_otm_steps, -self.put_otm_steps
        current_spot = float(eligible.iloc[-1]["close"])

        expiry = resolver.expiry
        symbol = getattr(getattr(resolver, "_data", None), "symbol", self.symbol)
        from .calendar import get_instrument_spec
        strike_step = get_instrument_spec(symbol).strike_step
        atm_strike = round(current_spot / strike_step) * strike_step
        target_call_strike = float(atm_strike + self.call_otm_steps * strike_step)
        target_put_strike = float(atm_strike - self.put_otm_steps * strike_step)

        def _best_offset(side: str, target: float) -> int:
            best_off = 0
            best_dist = float("inf")
            for n in range(-10, 11):
                key = "ATM" if n == 0 else (f"ATMp{n}" if n > 0 else f"ATMm{-n}")
                strike = offset_strike_by_date.get((side, key, trade_date, expiry))
                if strike is None:
                    continue
                dist = abs(strike - target)
                if dist < best_dist:
                    best_dist = dist
                    best_off = n
            return best_off

        return _best_offset("call", target_call_strike), _best_offset("put", target_put_strike)

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        call_offset, put_offset = self._find_offsets(context)
        return [
            Leg(context.resolver.resolve_atm_offset(context.timestamp, call_offset, OptionType.CALL), Side.BUY, self.lots),
            Leg(context.resolver.resolve_atm_offset(context.timestamp, put_offset, OptionType.PUT), Side.BUY, self.lots),
        ]

    def can_enter(self, trade_date: date, spot_by_date: dict) -> bool:
        from .calendar import expiry_on_or_after
        expiry = expiry_on_or_after(self.symbol, trade_date, expiry_type="week", min_dte=0)
        return expiry == trade_date

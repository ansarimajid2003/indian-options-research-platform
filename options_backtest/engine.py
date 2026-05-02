from __future__ import annotations

import datetime
from pathlib import Path

import pandas as pd

from .broker_sim import FillModel, opposite_side
from .calendar import combine_date_time, nearest_timestamp, nifty_lot_size, parse_expiry_folder
from .contract_resolver import ContractResolver
from .data_store import load_expiry_options, load_expiry_spot, list_expiry_dirs, load_deduped_spot
from .liquidity import LiquidityConfig, contract_is_liquid
from .portfolio import finalize_trade
from .reports import build_result
from .schemas import BacktestConfig, BacktestResult, Fill, Leg, Side, Trade
from .strategy import OptionStrategy, StrategyContext


class BacktestEngine:
    def __init__(self, config: BacktestConfig | None = None, liquidity_config: LiquidityConfig | None = None):
        self.config = config or BacktestConfig()
        self.liquidity_config = liquidity_config or LiquidityConfig()
        self.fill_model = FillModel(
            tick_size=self.config.tick_size,
            slippage_points=self.config.slippage_points,
            include_costs=self.config.include_costs,
        )

    def _resolve_lot_size(self, trade_date: datetime.date) -> int:
        if self.config.lot_size is not None:
            return self.config.lot_size
        return nifty_lot_size(trade_date)

    def run(self, strategy: OptionStrategy, from_expiry: str | None = None, to_expiry: str | None = None, limit: int | None = None) -> BacktestResult:
        raw_root = Path(self.config.raw_root)
        bad = set(self.config.bad_expiries)
        expiry_dirs = list_expiry_dirs(raw_root, bad)
        if from_expiry:
            expiry_dirs = [path for path in expiry_dirs if path.name >= from_expiry]
        if to_expiry:
            expiry_dirs = [path for path in expiry_dirs if path.name <= to_expiry]
        if limit:
            expiry_dirs = expiry_dirs[:limit]

        trades: list[Trade] = []
        if not self.config.next_day_exit:
            for expiry_dir in expiry_dirs:
                trade = self._run_expiry_single(expiry_dir, strategy)
                if trade is not None:
                    trades.append(trade)
            return build_result(self.config, trades)

        # Build signal calendar once across all spot data so each trade_date maps
        # to exactly one expiry folder (nearest weekly with DTE >= 1).
        global_spot = load_deduped_spot(raw_root, bad)
        if global_spot.empty:
            return build_result(self.config, trades)

        global_spot = global_spot.copy()
        global_spot["timestamp"] = pd.to_datetime(global_spot["timestamp"])
        global_spot_index = pd.DatetimeIndex(global_spot["timestamp"].sort_values())
        all_trade_dates = sorted(global_spot["timestamp"].dt.date.unique())

        # For each trade_date find entry_ts; skip dates with no valid entry bar.
        signal_dates: dict[datetime.date, pd.Timestamp] = {}
        for trade_date in all_trade_dates:
            entry_target = combine_date_time(trade_date, self.config.entry_time)
            entry_ts = nearest_timestamp(global_spot_index, entry_target)
            if entry_ts is not None and entry_ts.date() == trade_date:
                signal_dates[trade_date] = entry_ts

        # Map each signal trade_date to one expiry folder: smallest DTE >= 1.
        expiry_folder_map: dict[datetime.date, Path] = {}
        for trade_date in signal_dates:
            best_dir: Path | None = None
            best_dte: int | None = None
            for expiry_dir in expiry_dirs:
                expiry_date = parse_expiry_folder(expiry_dir.name)
                d = (expiry_date - trade_date).days
                if d >= 1 and (best_dte is None or d < best_dte):
                    best_dte = d
                    best_dir = expiry_dir
            if best_dir is not None:
                expiry_folder_map[trade_date] = best_dir

        # Group selected trade_dates by expiry folder, run _run_expiry_daily per folder.
        from collections import defaultdict
        folder_dates: dict[Path, list[datetime.date]] = defaultdict(list)
        for trade_date, expiry_dir in expiry_folder_map.items():
            folder_dates[expiry_dir].append(trade_date)

        for expiry_dir, date_list in folder_dates.items():
            trades.extend(self._run_expiry_daily(expiry_dir, strategy, trade_dates=sorted(date_list)))

        return build_result(self.config, trades)

    # ------------------------------------------------------------------
    # next_day_exit=False: one trade per expiry (original behaviour)
    # ------------------------------------------------------------------

    def _run_expiry_single(self, expiry_dir: Path, strategy: OptionStrategy) -> Trade | None:
        option_bars = load_expiry_options(expiry_dir)
        spot_bars = load_expiry_spot(expiry_dir)
        if option_bars.empty or spot_bars.empty:
            return None

        expiry = parse_expiry_folder(expiry_dir.name)
        entry_target = combine_date_time(expiry, self.config.entry_time)
        exit_target = combine_date_time(expiry, self.config.exit_time)
        resolver = ContractResolver(option_bars=option_bars, spot_bars=spot_bars)
        spot_index = pd.DatetimeIndex(pd.to_datetime(spot_bars["timestamp"]).sort_values())
        entry_ts = nearest_timestamp(spot_index, entry_target)
        if entry_ts is None or entry_ts > exit_target:
            return None

        return self._execute_trade(expiry_dir, entry_ts, exit_target, strategy, resolver, option_bars, spot_bars, expiry)

    # ------------------------------------------------------------------
    # next_day_exit=True: one trade per trading day within the expiry
    # ------------------------------------------------------------------

    def _run_expiry_daily(self, expiry_dir: Path, strategy: OptionStrategy, trade_dates: list[datetime.date] | None = None) -> list[Trade]:
        spot_bars = load_expiry_spot(expiry_dir)
        if spot_bars.empty:
            return []

        expiry = parse_expiry_folder(expiry_dir.name)
        spot_bars = spot_bars.copy()
        spot_bars["timestamp"] = pd.to_datetime(spot_bars["timestamp"])
        spot_index = pd.DatetimeIndex(spot_bars["timestamp"].sort_values())

        # Use only the caller-supplied dates; fall back to all dates in this expiry folder.
        all_trading_days = sorted(spot_bars["timestamp"].dt.date.unique())
        candidate_days = sorted(trade_dates) if trade_dates is not None else all_trading_days

        # Collect ATM strikes for every entry timestamp so we load only the
        # strikes we actually need instead of all 190 files.
        strike_step = 50
        needed_strikes: set[int] = set()
        day_entry_map: dict = {}
        for trade_date in candidate_days:
            # Find the next actual trading day in this expiry's spot data.
            later_days = [d for d in all_trading_days if d > trade_date]
            if not later_days:
                continue
            next_date = later_days[0]
            entry_target = combine_date_time(trade_date, self.config.entry_time)
            exit_target = combine_date_time(next_date, self.config.exit_time)
            entry_ts = nearest_timestamp(spot_index, entry_target)
            if entry_ts is None or entry_ts.date() != trade_date:
                continue
            next_day_bars = spot_bars[spot_bars["timestamp"].dt.date == next_date]
            if next_day_bars.empty:
                continue
            spot_close = float(spot_bars.loc[spot_bars["timestamp"] == entry_ts, "close"].iloc[0])
            atm = int(round(spot_close / strike_step) * strike_step)
            # Load ATM ±2 strikes to cover any offset strategies; baseline only needs ±0
            for offset in range(-2, 3):
                needed_strikes.add(atm + offset * strike_step)
            day_entry_map[trade_date] = (entry_ts, exit_target)

        if not day_entry_map:
            return []

        option_bars = load_expiry_options(expiry_dir, strikes=needed_strikes)
        if option_bars.empty:
            return []

        trades: list[Trade] = []
        for trade_date, (entry_ts, exit_target) in day_entry_map.items():
            resolver = ContractResolver(option_bars=option_bars, spot_bars=spot_bars)
            trade = self._execute_trade(expiry_dir, entry_ts, exit_target, strategy, resolver, option_bars, spot_bars, expiry)
            if trade is not None:
                trades.append(trade)

        return trades

    # ------------------------------------------------------------------
    # Shared execution path
    # ------------------------------------------------------------------

    def _execute_trade(
        self,
        expiry_dir: Path,
        entry_ts: pd.Timestamp,
        exit_target: pd.Timestamp,
        strategy: OptionStrategy,
        resolver: ContractResolver,
        option_bars: pd.DataFrame,
        spot_bars: pd.DataFrame,
        expiry: datetime.date,
    ) -> Trade | None:
        trade_date = entry_ts.date()
        lot_size = self._resolve_lot_size(trade_date)

        context = StrategyContext(timestamp=entry_ts, resolver=resolver)
        try:
            legs = strategy.entry_legs(context)
        except KeyError:
            return None

        atm = resolver.atm_strike(entry_ts)
        for leg in legs:
            bars = resolver.bars_for(leg.contract)
            if not contract_is_liquid(bars, leg.contract.strike, atm, self.liquidity_config):
                return None

        entry_fills = self._entry_fills(entry_ts, legs, resolver, lot_size, trade_date)
        if len(entry_fills) != len(legs):
            return None

        spot_stop_level: float | None = None
        get_stop = getattr(strategy, "get_spot_stop_level", None)
        if get_stop is not None:
            spot_stop_level = get_stop(context)

        trade = Trade(
            expiry=expiry,
            strategy=getattr(strategy, "name", strategy.__class__.__name__),
            entry_time=entry_ts,
            exit_time=None,
            entry_reason="entry",
            exit_reason=None,
            entry_fills=entry_fills,
            metadata={
                "entry_credit": self._close_value(entry_fills, legs),
                "lot_size": lot_size,
            },
        )

        exit_ts, exit_reason = self._find_exit(
            entry_ts, exit_target, legs, resolver,
            trade.metadata["entry_credit"], lot_size,
            spot_stop_level=spot_stop_level,
        )
        if exit_ts is None:
            return None
        trade.exit_time = exit_ts
        trade.exit_reason = exit_reason
        trade.exit_fills = self._exit_fills(exit_ts, legs, resolver, exit_reason, lot_size, trade_date)
        if len(trade.exit_fills) != len(legs):
            return None
        return finalize_trade(trade)

    # kept for backwards-compat — callers that used run_expiry directly
    def run_expiry(self, expiry_dir: Path, strategy: OptionStrategy) -> Trade | None:
        return self._run_expiry_single(expiry_dir, strategy)

    def _entry_fills(
        self,
        timestamp: pd.Timestamp,
        legs: list[Leg],
        resolver: ContractResolver,
        lot_size: int,
        trade_date: datetime.date | None = None,
    ) -> list[Fill]:
        fills = []
        for leg in legs:
            bar = resolver.bar_at(leg.contract, timestamp)
            if bar is None:
                return []
            fills.append(self.fill_model.fill(
                timestamp, leg.contract, leg.side, leg.lots, lot_size,
                float(bar["close"]), "entry", trade_date=trade_date,
            ))
        return fills

    def _exit_fills(
        self,
        timestamp: pd.Timestamp,
        legs: list[Leg],
        resolver: ContractResolver,
        reason: str,
        lot_size: int,
        trade_date: datetime.date | None = None,
    ) -> list[Fill]:
        # SL/target: trigger detected on bar[i] close, fill executes at bar[i+1] open.
        # time_exit: MOC-style, fill at close.
        # next_day_open: fill at open of the first bar on the next day.
        price_col = "open" if reason in ("stop_loss", "target", "next_day_open") else "close"
        fills = []
        for leg in legs:
            bar = resolver.bar_at(leg.contract, timestamp)
            if bar is None:
                return []
            fills.append(self.fill_model.fill(
                timestamp, leg.contract, opposite_side(leg.side), leg.lots, lot_size,
                float(bar[price_col]), reason, trade_date=trade_date,
            ))
        return fills

    def _close_value(self, fills: list[Fill], legs: list[Leg]) -> float:
        value = 0.0
        for fill, leg in zip(fills, legs):
            signed = fill.gross_value if leg.side == Side.SELL else -fill.gross_value
            value += signed
        return value

    def _hypothetical_exit_cashflow(
        self,
        timestamp: pd.Timestamp,
        legs: list[Leg],
        resolver: ContractResolver,
        lot_size: int,
        price_col: str = "close",
    ) -> float | None:
        cashflow = 0.0
        for leg in legs:
            bar = resolver.bar_at(leg.contract, timestamp)
            if bar is None:
                return None
            price = float(bar[price_col]) * leg.lots * lot_size
            exit_side = opposite_side(leg.side)
            signed = price if exit_side == Side.SELL else -price
            cashflow += signed
        return cashflow

    def _find_exit(
        self,
        entry_ts: pd.Timestamp,
        exit_target: pd.Timestamp,
        legs: list[Leg],
        resolver: ContractResolver,
        entry_credit: float,
        lot_size: int,
        spot_stop_level: float | None = None,
    ) -> tuple[pd.Timestamp | None, str | None]:
        # Timestamps where every leg has a bar AND spot has a bar — avoids phantom exits.
        leg_frames = []
        for leg in legs:
            key = (leg.contract.strike, leg.contract.option_type.value)
            frame = resolver._bars_cache.get(key)
            if frame is None:
                return None, None
            leg_frames.append((leg, frame))
        if not leg_frames:
            return None, None
        timestamps = leg_frames[0][1].index
        for _, frame in leg_frames[1:]:
            timestamps = timestamps.intersection(frame.index)
        timestamps = timestamps.intersection(resolver._spot_index.index).sort_values()
        timestamps = timestamps[(timestamps > entry_ts) & (timestamps <= exit_target)]

        close_cashflow = None
        for leg, frame in leg_frames:
            exit_side = opposite_side(leg.side)
            signed = 1.0 if exit_side == Side.SELL else -1.0
            values = frame.loc[timestamps, "close"].astype(float) * leg.lots * lot_size * signed
            close_cashflow = values if close_cashflow is None else close_cashflow + values
        cashflow_values = close_cashflow.to_numpy() if close_cashflow is not None else []

        spot_lows = None
        if spot_stop_level is not None and len(timestamps):
            spot_col = "low" if "low" in resolver._spot_index.columns else "close"
            spot_lows = resolver._spot_index.loc[timestamps, spot_col].astype(float).to_numpy()
        # We evaluate SL/target using bar[i] close. When triggered, the fill happens at
        # bar[i+1] open — the first price observable after the signal bar closed.
        for i, ts in enumerate(timestamps):
            # Spot-level stop: if spot bar touches or falls below the level, exit at next bar open
            if spot_stop_level is not None and ts.date() > entry_ts.date():
                if spot_lows is not None and spot_lows[i] <= spot_stop_level:
                    next_ts = timestamps[i + 1] if i + 1 < len(timestamps) else ts
                    fill_ts = min(next_ts, exit_target)
                    return pd.Timestamp(fill_ts), "spot_level_stop"
            check_cashflow = float(cashflow_values[i])
            pnl = entry_credit + check_cashflow
            if self.config.stop_loss_pct is not None:
                if entry_credit > 0:
                    triggered = pnl <= -entry_credit * self.config.stop_loss_pct
                else:
                    # debit: entry_credit < 0; cost basis is abs(entry_credit)
                    triggered = pnl <= entry_credit * self.config.stop_loss_pct
                if triggered:
                    if i + 1 < len(timestamps):
                        fill_ts = min(timestamps[i + 1], exit_target)
                        return pd.Timestamp(fill_ts), "stop_loss"
                    return pd.Timestamp(exit_target), "time_exit"
            if self.config.target_profit_pct is not None:
                if entry_credit > 0:
                    triggered = pnl >= entry_credit * self.config.target_profit_pct
                else:
                    # debit: target on abs(cost basis)
                    triggered = pnl >= abs(entry_credit) * self.config.target_profit_pct
                if triggered:
                    if i + 1 < len(timestamps):
                        fill_ts = min(timestamps[i + 1], exit_target)
                        return pd.Timestamp(fill_ts), "target"
                    return pd.Timestamp(exit_target), "time_exit"
        if self.config.next_day_exit:
            next_day_ts = [ts for ts in timestamps if ts > entry_ts and ts.date() > entry_ts.date() and ts <= exit_target]
            if next_day_ts:
                return pd.Timestamp(next_day_ts[0]), "next_day_open"
            return None, None
        eligible = [ts for ts in timestamps if entry_ts < ts <= exit_target]
        return (pd.Timestamp(eligible[-1]), "time_exit") if eligible else (None, None)

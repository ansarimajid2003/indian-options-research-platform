from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, time
from enum import Enum
from typing import Any

import pandas as pd


class OptionType(str, Enum):
    CALL = "CE"
    PUT = "PE"


class Side(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass(frozen=True)
class Contract:
    expiry: date
    strike: int
    option_type: OptionType
    ticker: str

    @property
    def key(self) -> tuple[date, int, OptionType]:
        return self.expiry, self.strike, self.option_type


@dataclass(frozen=True)
class Leg:
    contract: Contract
    side: Side
    lots: int = 1


@dataclass(frozen=True)
class Order:
    timestamp: pd.Timestamp
    leg: Leg
    reason: str


@dataclass(frozen=True)
class Fill:
    timestamp: pd.Timestamp
    contract: Contract
    side: Side
    quantity: int
    price: float
    gross_value: float
    charges: float
    reason: str


@dataclass
class Position:
    contract: Contract
    quantity: int = 0
    avg_price: float = 0.0


@dataclass
class Trade:
    expiry: date
    strategy: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp | None
    entry_reason: str
    exit_reason: str | None
    entry_fills: list[Fill] = field(default_factory=list)
    exit_fills: list[Fill] = field(default_factory=list)
    gross_pnl: float = 0.0
    charges: float = 0.0
    net_pnl: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class BacktestConfig:
    raw_root: str = "data/options/raw/shoonya/nifty"
    symbol: str = "NIFTY"
    lot_size: int = 50
    tick_size: float = 0.05
    slippage_points: float = 0.05
    entry_time: time = time(9, 20)
    exit_time: time = time(15, 20)
    # When True, exit on the first bar at/after exit_time on the next calendar day
    next_day_exit: bool = False
    # Exit when loss >= stop_loss_pct * entry_credit (e.g. 0.5 = exit at 50% loss of premium)
    stop_loss_pct: float | None = 0.5
    # Exit when profit >= target_profit_pct * entry_credit (e.g. 0.5 = exit at 50% profit)
    target_profit_pct: float | None = 0.5
    include_costs: bool = True
    bad_expiries: tuple[str, ...] = ("20250925", "20251224")


@dataclass
class BacktestResult:
    config: BacktestConfig
    trades: list[Trade]
    summary: dict[str, Any]
    trade_ledger: pd.DataFrame
    daily_pnl: pd.DataFrame
    equity_curve: pd.DataFrame

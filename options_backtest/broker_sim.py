from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .schemas import Contract, Fill, Side


@dataclass(frozen=True)
class ChargesConfig:
    brokerage_per_order: float = 20.0
    stt_sell_rate: float = 0.0015   # Budget 2026: 0.15% of premium (up from 0.10%)
    exchange_rate: float = 0.0005
    sebi_rate: float = 0.000001
    stamp_buy_rate: float = 0.00003
    gst_rate: float = 0.18


@dataclass(frozen=True)
class FillModel:
    tick_size: float = 0.05
    slippage_points: float = 0.05
    charges: ChargesConfig = ChargesConfig()
    include_costs: bool = True

    def round_tick(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 2)

    def fill_price(self, close: float, side: Side) -> float:
        # Conservative half-spread proxy: 0.3% of mid or 1 tick, whichever is larger.
        # Applied in addition to slippage; replaces mid-price optimism when no bid/ask data.
        half_spread = max(self.tick_size, round(close * 0.003 / self.tick_size) * self.tick_size)
        adjustment = self.slippage_points + half_spread if side == Side.BUY else -(self.slippage_points + half_spread)
        return max(self.tick_size, self.round_tick(close + adjustment))

    def estimate_charges(self, side: Side, quantity: int, price: float) -> float:
        if not self.include_costs:
            return 0.0
        turnover = abs(quantity * price)
        brokerage = self.charges.brokerage_per_order
        stt = turnover * self.charges.stt_sell_rate if side == Side.SELL else 0.0
        exchange = turnover * self.charges.exchange_rate
        sebi = turnover * self.charges.sebi_rate
        stamp = turnover * self.charges.stamp_buy_rate if side == Side.BUY else 0.0
        gst = (brokerage + exchange + sebi) * self.charges.gst_rate
        return round(brokerage + stt + exchange + sebi + stamp + gst, 2)

    def fill(self, timestamp: pd.Timestamp, contract: Contract, side: Side, lots: int, lot_size: int, close: float, reason: str) -> Fill:
        quantity = int(lots * lot_size)
        price = self.fill_price(float(close), side)
        gross_value = quantity * price
        charges = self.estimate_charges(side, quantity, price)
        return Fill(timestamp=timestamp, contract=contract, side=side, quantity=quantity, price=price, gross_value=gross_value, charges=charges, reason=reason)


def opposite_side(side: Side) -> Side:
    return Side.BUY if side == Side.SELL else Side.SELL

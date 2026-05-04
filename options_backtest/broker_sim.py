from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from .schemas import Contract, Fill, Side


# ──────────────────────────────────────────────────────────────────────────────
# Date-sensitive rate schedules
# Each tuple: (effective_from, rate).  Walk from top; first match where
# trade_date >= effective_from is the active rate.
# Sources: Finance Act / Budget circulars; Zerodha charges page; NSE circulars.
# ──────────────────────────────────────────────────────────────────────────────

# Options sell-side STT (charged on premium, seller only)
_STT_SELL_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2026, 4,  1), 0.001500),   # Budget 2026: 0.15%
    (date(2024, 10, 1), 0.001000),   # Budget 2024: 0.10%
    (date(2023, 4,  1), 0.000625),   # Budget 2023: 0.0625%
    (date(2004, 1,  1), 0.000500),   # Original rate: 0.05%
)

# Options exercise STT (ITM options held to expiry, charged on intrinsic value)
_STT_EXERCISE_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2026, 4,  1), 0.001500),   # Budget 2026: 0.15%
    (date(2004, 1,  1), 0.001250),   # Pre-2026: 0.125%
)

# NSE exchange transaction charge (ETC) on options premium, both sides
_ETC_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2024, 10, 1), 0.0003503),  # NSE revised flat rate: 0.03503% (₹35.03/lakh)
    (date(2023, 4,  1), 0.0005000),  # Rolled back to 0.05%
    (date(2022, 1,  1), 0.0005300),  # Jan 2022 increase: ~0.053%
    (date(2004, 1,  1), 0.0005000),  # Base rate: 0.05%
)

# Stamp duty on options buy side (uniform from July 1, 2020; state-wise before)
_STAMP_BUY_SCHEDULE: tuple[tuple[date, float], ...] = (
    (date(2020, 7,  1), 0.00003),    # Uniform 0.003% (Finance Act 2019 amendment)
    (date(2004, 1,  1), 0.00000),    # Pre-July 2020: state-wise; modelled as 0
)


def _rate_for_date(schedule: tuple[tuple[date, float], ...], d: date) -> float:
    """Walk schedule top-to-bottom and return the rate for the first entry where d >= effective_from."""
    for effective_from, rate in schedule:
        if d >= effective_from:
            return rate
    return schedule[-1][1]


@dataclass(frozen=True)
class ChargesConfig:
    # ₹10 flat per executed order (Kotak Neo; ₹20 round-trip per single leg)
    brokerage_per_order: float = 10.0
    stt_sell_rate: float = 0.001500    # options sell STT on premium
    stt_exercise_rate: float = 0.001500  # ITM exercise STT on intrinsic value
    exchange_rate: float = 0.0003503   # NSE ETC (post-Oct 2024)
    sebi_rate: float = 0.000001        # SEBI turnover fee (₹10/crore)
    stamp_buy_rate: float = 0.00003    # stamp duty on buy side
    gst_rate: float = 0.18             # GST on brokerage + ETC + SEBI

    @classmethod
    def for_date(cls, d: date) -> "ChargesConfig":
        """Return a ChargesConfig with rates calibrated to *d*."""
        return cls(
            stt_sell_rate=_rate_for_date(_STT_SELL_SCHEDULE, d),
            stt_exercise_rate=_rate_for_date(_STT_EXERCISE_SCHEDULE, d),
            exchange_rate=_rate_for_date(_ETC_SCHEDULE, d),
            stamp_buy_rate=_rate_for_date(_STAMP_BUY_SCHEDULE, d),
        )


@dataclass(frozen=True)
class FillModel:
    tick_size: float = 0.05
    slippage_points: float = 0.05
    charges: ChargesConfig = ChargesConfig()
    include_costs: bool = True

    def round_tick(self, price: float) -> float:
        return round(round(price / self.tick_size) * self.tick_size, 2)

    def fill_price(
        self,
        close: float,
        side: Side,
        dte: int | None = None,
        slippage_multiplier: float = 1.0,
    ) -> float:
        spread_pct = _tiered_spread_pct(close, dte)
        half_spread = max(self.tick_size, round(close * spread_pct / self.tick_size) * self.tick_size)
        slippage = self.slippage_points * slippage_multiplier
        adjustment = slippage + half_spread if side == Side.BUY else -(slippage + half_spread)
        return max(self.tick_size, self.round_tick(close + adjustment))

    def estimate_charges(
        self,
        side: Side,
        quantity: int,
        price: float,
        trade_date: date | None = None,
        is_exercise: bool = False,
        intrinsic_value: float = 0.0,
    ) -> float:
        if not self.include_costs:
            return 0.0
        charges = ChargesConfig.for_date(trade_date) if trade_date is not None else self.charges
        turnover = abs(quantity * price)
        brokerage = charges.brokerage_per_order
        if side == Side.SELL:
            stt = turnover * charges.stt_sell_rate
            if is_exercise and intrinsic_value > 0:
                # ITM option exercised: additional STT on intrinsic value (buyer pays)
                stt += abs(quantity * intrinsic_value) * charges.stt_exercise_rate
        else:
            stt = 0.0
        exchange = turnover * charges.exchange_rate
        sebi = turnover * charges.sebi_rate
        stamp = turnover * charges.stamp_buy_rate if side == Side.BUY else 0.0
        gst = (brokerage + exchange + sebi) * charges.gst_rate
        return round(brokerage + stt + exchange + sebi + stamp + gst, 2)

    def fill(
        self,
        timestamp: pd.Timestamp,
        contract: Contract,
        side: Side,
        lots: int,
        lot_size: int,
        close: float,
        reason: str,
        trade_date: date | None = None,
        slippage_multiplier: float = 1.0,
    ) -> Fill:
        quantity = int(lots * lot_size)
        dte = (contract.expiry - trade_date).days if (trade_date is not None and contract.expiry is not None) else None
        # Buying to close a short position requires taking liquidity in a moving market —
        # apply a wider slippage floor to reflect the adversity of forced buy-backs.
        effective_multiplier = max(slippage_multiplier, 1.5) if reason != "entry" and side == Side.BUY else slippage_multiplier
        price = self.fill_price(float(close), side, dte=dte, slippage_multiplier=effective_multiplier)
        gross_value = quantity * price
        charges = self.estimate_charges(side, quantity, price, trade_date=trade_date)
        return Fill(
            timestamp=timestamp,
            contract=contract,
            side=side,
            quantity=quantity,
            price=price,
            gross_value=gross_value,
            charges=charges,
            reason=reason,
        )


def opposite_side(side: Side) -> Side:
    return Side.BUY if side == Side.SELL else Side.SELL


def _tiered_spread_pct(close: float, dte: int | None) -> float:
    """Half-spread as fraction of close, tiered by moneyness proxy (close price) and DTE.

    Thresholds calibrated to NIFTY practitioner consensus:
      ATM liquid (close > 80):        0.3%
      near-OTM (close 30–80):         0.5%
      far OTM (close < 30):           1.5%
      expiry-day far OTM (dte=0, <50): 2.0%
    """
    if dte is not None and dte == 0 and close < 50:
        return 0.020
    if close < 30:
        return 0.015
    if close < 80:
        return 0.005
    return 0.003

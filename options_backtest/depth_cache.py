"""
Thread-safe in-memory cache for 20-level Dhan market depth.

DepthCache is the single shared state between the order-book collector
and the paper trading engine. The collector writes via update_bid_packet /
update_ask_packet; the engine reads via snapshot / executable_price.

All internal timestamps are pd.Timestamp with tz=Asia/Kolkata.
"""

from __future__ import annotations

import threading
import time as _time
from dataclasses import dataclass, field
from typing import Optional

import pandas as pd

from .schemas import Side

_IST = "Asia/Kolkata"


@dataclass(frozen=True)
class DepthLevel:
    price: float
    quantity: int
    orders: int


@dataclass
class DepthSnapshot:
    security_id: str
    bid_levels: list[DepthLevel]       # index 0 = best bid (highest price)
    ask_levels: list[DepthLevel]       # index 0 = best ask (lowest price)
    bid_ts: pd.Timestamp
    ask_ts: pd.Timestamp

    @property
    def best_bid(self) -> float:
        return self.bid_levels[0].price if self.bid_levels else 0.0

    @property
    def best_ask(self) -> float:
        return self.ask_levels[0].price if self.ask_levels else 0.0

    @property
    def mid(self) -> float:
        return (self.best_bid + self.best_ask) / 2.0

    @property
    def spread_abs(self) -> float:
        return self.best_ask - self.best_bid

    @property
    def spread_pct(self) -> float:
        if self.mid <= 0:
            return 0.0
        return self.spread_abs / self.mid

    @property
    def total_bid_qty(self) -> int:
        return sum(lv.quantity for lv in self.bid_levels)

    @property
    def total_ask_qty(self) -> int:
        return sum(lv.quantity for lv in self.ask_levels)

    @property
    def imbalance(self) -> float:
        total = self.total_bid_qty + self.total_ask_qty
        if total == 0:
            return 0.0
        return (self.total_bid_qty - self.total_ask_qty) / total


@dataclass
class _SecurityState:
    bid_levels: list[DepthLevel] = field(default_factory=list)
    ask_levels: list[DepthLevel] = field(default_factory=list)
    bid_ts: pd.Timestamp | None = None
    ask_ts: pd.Timestamp | None = None


class DepthCache:
    """Thread-safe 20-level bid/ask depth cache for up to ~200 securities."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._state: dict[str, _SecurityState] = {}

    def _get_or_create(self, security_id: str) -> _SecurityState:
        if security_id not in self._state:
            self._state[security_id] = _SecurityState()
        return self._state[security_id]

    def update_bid_packet(
        self,
        security_id: str,
        packet_ts: pd.Timestamp,
        levels: list[DepthLevel],
    ) -> None:
        with self._lock:
            st = self._get_or_create(security_id)
            st.bid_levels = levels
            st.bid_ts = packet_ts

    def update_ask_packet(
        self,
        security_id: str,
        packet_ts: pd.Timestamp,
        levels: list[DepthLevel],
    ) -> None:
        with self._lock:
            st = self._get_or_create(security_id)
            st.ask_levels = levels
            st.ask_ts = packet_ts

    def snapshot(self, security_id: str) -> DepthSnapshot | None:
        with self._lock:
            st = self._state.get(security_id)
            if st is None or st.bid_ts is None or st.ask_ts is None:
                return None
            if not st.bid_levels or not st.ask_levels:
                return None
            return DepthSnapshot(
                security_id=security_id,
                bid_levels=list(st.bid_levels),
                ask_levels=list(st.ask_levels),
                bid_ts=st.bid_ts,
                ask_ts=st.ask_ts,
            )

    def is_ready(self, security_id: str, max_age_seconds: int = 5) -> bool:
        with self._lock:
            st = self._state.get(security_id)
            if st is None or st.bid_ts is None or st.ask_ts is None:
                return False
            if not st.bid_levels or not st.ask_levels:
                return False
            now = pd.Timestamp.now(tz=_IST)
            bid_age = (now - st.bid_ts).total_seconds()
            ask_age = (now - st.ask_ts).total_seconds()
            return bid_age <= max_age_seconds and ask_age <= max_age_seconds

    def executable_price(
        self, security_id: str, side: Side, quantity: int
    ) -> float | None:
        """VWAP walk through depth levels for the given side and quantity.

        BUY  → walks ask levels (ascending price); returns ask-side VWAP.
        SELL → walks bid levels (descending price); returns bid-side VWAP.

        Returns None if cumulative depth is insufficient to fill quantity.
        """
        snap = self.snapshot(security_id)
        if snap is None:
            return None
        levels = snap.ask_levels if side == Side.BUY else snap.bid_levels
        remaining = quantity
        total_cost = 0.0
        for lv in levels:
            if remaining <= 0:
                break
            fill_qty = min(remaining, lv.quantity)
            total_cost += fill_qty * lv.price
            remaining -= fill_qty
        if remaining > 0:
            return None
        return total_cost / quantity

    def tracked_ids(self) -> list[str]:
        with self._lock:
            return list(self._state.keys())

    def readiness_summary(self, security_ids: list[str], max_age_seconds: int = 5) -> dict:
        ready = sum(1 for sid in security_ids if self.is_ready(sid, max_age_seconds))
        return {
            "total": len(security_ids),
            "ready": ready,
            "ready_pct": ready / len(security_ids) * 100 if security_ids else 0.0,
        }

"""
Unified MarketDataFeed contract (Phase E2).

The current live stack has two parallel websocket plumbings:

* ``PaperTradingEngine._feed_loop`` (live feed, ``api-feed.dhan.co``):
  IDX_I spots + VIX + chain quotes via RequestCode 21 (Full) and
  belt-and-suspenders RequestCode 15 (Ticker).
* ``scripts.live.collect_order_book.DepthCollector`` (20-depth feed,
  ``depth-api-feed.dhan.co/twentydepth``): NSE_FNO option depth via
  RequestCode 23.

They share ``DepthCache`` by reference and coordinate through the
filesystem ``latest_depth_cache.json`` snapshot. The 05-22 / 05-25
``FileNotFoundError`` race, the 05-20 writer-thread crash, and the
recurring "engine says feed connected, collector says not connected"
inconsistencies all trace to the absence of one coordinating layer.

This module **defines the contract** for that coordinating layer. The
full implementation merges the live-feed loop and the depth collector
into a single class with one public API:

    feed = DhanMarketDataFeed(creds)
    feed.subscribe_spots(symbols)
    feed.subscribe_option_chain(symbol, chain_legs)
    feed.subscribe_depth(security_ids)
    ltp = feed.quote(security_id)
    depth = feed.depth(security_id)
    await feed.run(stop_event)

The migration is staged because it touches every hot-path callsite in
the engine and collector. The contract here is **stable now** so
strategy/engine code can be written against it; the implementation
will land in a subsequent commit once each callsite has been migrated.

For Phase E2 of the May-2026 refactor we ship the interface, a
``DhanMarketDataFeedAdapter`` that wraps the existing engine + collector
so callers can opt-in incrementally, and tests that pin the contract.
The full *unified* implementation is tracked as Phase E2.1 (deferred to
a focused session — not safe to land alongside Phase E1 process
collapse on the same deploy).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable, Protocol, runtime_checkable

from .depth_cache import DepthCache

__all__ = [
    "Quote",
    "Depth",
    "MarketDataFeed",
    "DhanMarketDataFeedAdapter",
]

_log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Quote:
    """Last-traded snapshot for one security."""

    security_id: str
    ltp: float
    volume: int
    oi: int
    received_at_iso: str


@dataclass(frozen=True)
class Depth:
    """Top-of-book + total-side quantities for one security."""

    security_id: str
    best_bid: float
    best_ask: float
    bid_qty: int
    ask_qty: int
    snapshot_ts_iso: str
    is_ready: bool


@runtime_checkable
class MarketDataFeed(Protocol):
    """Contract every live market-data source must satisfy.

    The current stack has *two* feeds (live + depth) coordinated only
    by shared mutable state. Consumers that depend on this Protocol
    (Strategy, EquityTickWriter, Dashboard) become indifferent to
    which feeds back them and how many.
    """

    def quote(self, security_id: str) -> Quote | None:
        """Return the most recent quote for ``security_id`` or ``None``."""
        ...

    def depth(self, security_id: str) -> Depth | None:
        """Return the most recent depth snapshot for ``security_id``."""
        ...

    def subscribe(self, security_ids: Iterable[str]) -> None:
        """Add securities to the subscription set. Idempotent."""
        ...

    def is_feed_connected(self) -> bool:
        """Live-feed websocket connection state."""
        ...

    def is_depth_connected(self) -> bool:
        """Depth-feed websocket connection state (or True if no depth needed)."""
        ...


class DhanMarketDataFeedAdapter:
    """Read-only adapter exposing the ``MarketDataFeed`` Protocol over
    the *existing* paper-engine + depth-collector pair.

    Construction parameters:

    :param engine: a ``PaperTradingEngine`` instance (provides spot/VIX
                   quotes via its ``LiveDhanContractResolver`` set and
                   live-feed connection state).
    :param depth_cache: the shared ``DepthCache`` the depth collector
                        writes to.

    This adapter is intentionally *non-mutating*. ``subscribe()`` here
    is a logging no-op — callers that want to subscribe must still go
    through the engine and collector directly. The adapter exists so
    *consumers* (strategies, metrics, dashboard) can depend on the
    Protocol today, and the future ``DhanMarketDataFeed`` can replace
    this adapter without changing any consumer.
    """

    def __init__(self, engine, depth_cache: DepthCache) -> None:
        self._engine = engine
        self._depth = depth_cache

    def quote(self, security_id: str) -> Quote | None:
        sid = str(security_id)
        for resolver in self._engine._resolvers.values():  # type: ignore[attr-defined]
            entry = resolver._quote_cache.get(sid)  # type: ignore[attr-defined]
            if entry is None:
                continue
            return Quote(
                security_id=sid,
                ltp=float(entry.ltp),
                volume=int(entry.volume),
                oi=int(entry.oi),
                received_at_iso=entry.received_at.isoformat() if entry.received_at is not None else "",
            )
        return None

    def depth(self, security_id: str) -> Depth | None:
        sid = str(security_id)
        snap = self._depth.snapshot(sid)
        if snap is None:
            return None
        best_bid = snap.bid_levels[0].price if snap.bid_levels else 0.0
        best_ask = snap.ask_levels[0].price if snap.ask_levels else 0.0
        bid_qty = snap.bid_levels[0].quantity if snap.bid_levels else 0
        ask_qty = snap.ask_levels[0].quantity if snap.ask_levels else 0
        snap_ts = snap.bid_ts if snap.bid_ts is not None else snap.ask_ts
        return Depth(
            security_id=sid,
            best_bid=float(best_bid),
            best_ask=float(best_ask),
            bid_qty=int(bid_qty),
            ask_qty=int(ask_qty),
            snapshot_ts_iso=snap_ts.isoformat() if snap_ts is not None else "",
            is_ready=self._depth.is_ready(sid, max_age_seconds=5),
        )

    def subscribe(self, security_ids: Iterable[str]) -> None:
        # Adapter does not own the subscription side; subscriptions
        # come through the engine's `_subscribed_ids` and the
        # collector's `expand_universe`. Log the intent so the
        # mismatch is discoverable when the unified implementation
        # lands.
        ids = list(security_ids)
        if ids:
            _log.info("DhanMarketDataFeedAdapter.subscribe ignored ids=%d (adapter is read-only)", len(ids))

    def is_feed_connected(self) -> bool:
        return bool(getattr(self._engine, "_feed_connected", False))

    def is_depth_connected(self) -> bool:
        # The depth collector doesn't expose a single boolean; treat
        # "any tracked sid has a fresh snapshot" as connected. Acceptable
        # heuristic for adapter use; the unified implementation will
        # surface an explicit flag.
        try:
            tracked = list(self._depth.tracked_ids())
        except Exception:
            return False
        return any(self._depth.is_ready(sid, max_age_seconds=10) for sid in tracked)

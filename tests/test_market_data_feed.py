"""Tests for the MarketDataFeed Protocol + Dhan adapter."""

from __future__ import annotations

import unittest
from types import SimpleNamespace

import pandas as pd

from options_backtest.depth_cache import DepthCache, DepthLevel
from options_backtest.market_data_feed import (
    DhanMarketDataFeedAdapter,
    Depth,
    MarketDataFeed,
    Quote,
)


class _StubResolver:
    def __init__(self):
        self._quote_cache: dict = {}


class MarketDataFeedAdapterTests(unittest.TestCase):
    def setUp(self):
        self.depth = DepthCache()
        self.resolver = _StubResolver()
        self.engine = SimpleNamespace(
            _resolvers={"NIFTY": self.resolver},
            _feed_connected=True,
        )
        self.feed = DhanMarketDataFeedAdapter(self.engine, self.depth)

    def test_adapter_satisfies_protocol(self):
        self.assertIsInstance(self.feed, MarketDataFeed)

    def test_quote_returns_none_when_no_cache(self):
        self.assertIsNone(self.feed.quote("12345"))

    def test_quote_reads_resolver_cache(self):
        ts = pd.Timestamp("2026-06-01 09:20:00", tz="Asia/Kolkata")
        # Use the resolver's update_quote semantics indirectly: insert
        # a QuoteEntry via the resolver's internal cache.
        from options_backtest.live_resolver import QuoteEntry
        self.resolver._quote_cache["13"] = QuoteEntry(
            ltp=25_000.0, open=24_950.0, high=25_050.0, low=24_900.0,
            close=25_000.0, volume=100_000, oi=50_000, received_at=ts,
        )
        q = self.feed.quote("13")
        self.assertIsNotNone(q)
        self.assertEqual(q.ltp, 25_000.0)
        self.assertEqual(q.security_id, "13")

    def test_depth_returns_none_when_empty(self):
        self.assertIsNone(self.feed.depth("12345"))

    def test_depth_reads_cache_snapshot(self):
        ts = pd.Timestamp.now(tz="Asia/Kolkata")
        self.depth.update_bid_packet("121", ts, [DepthLevel(99.0, 1000, 1)])
        self.depth.update_ask_packet("121", ts, [DepthLevel(101.0, 1000, 1)])
        d = self.feed.depth("121")
        self.assertIsNotNone(d)
        self.assertEqual(d.best_bid, 99.0)
        self.assertEqual(d.best_ask, 101.0)
        self.assertTrue(d.is_ready)

    def test_is_feed_connected_proxies_engine_flag(self):
        self.assertTrue(self.feed.is_feed_connected())
        self.engine._feed_connected = False
        self.assertFalse(self.feed.is_feed_connected())

    def test_subscribe_is_noop_with_log(self):
        # Subscribe is documented as a read-only adapter no-op; should
        # not raise.
        self.feed.subscribe(["1", "2", "3"])


if __name__ == "__main__":
    unittest.main()

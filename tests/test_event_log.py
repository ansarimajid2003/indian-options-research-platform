"""Unit tests for ``options_backtest.live_event_log``."""

from __future__ import annotations

import json
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from options_backtest.live_event_log import EventLog, EventType, read_latest_events


class EventLogBasicTests(unittest.TestCase):
    def test_open_creates_db_and_meta(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "20260601.sqlite"
            log = EventLog.open(path, session_date="2026-06-01")
            self.assertTrue(path.exists())
            log.close()
            # ``with sqlite3.connect(...)`` only commits on exit; we
            # must explicitly close so Windows can unlink the temp dir.
            conn = sqlite3.connect(str(path))
            try:
                rows = conn.execute("SELECT key, value FROM meta ORDER BY key").fetchall()
            finally:
                conn.close()
            self.assertEqual(dict(rows)["session_date"], "2026-06-01")
            self.assertEqual(dict(rows)["schema_version"], "1")

    def test_reopen_with_mismatched_date_raises(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "20260601.sqlite"
            first = EventLog.open(path, session_date="2026-06-01")
            first.close()
            # The mismatched reopen should raise BEFORE leaving a half-
            # initialised connection around — but if construction throws
            # after sqlite_connect has succeeded, the connection leaks.
            # Catch + close defensively so the tempdir teardown can
            # unlink the file on Windows.
            try:
                with self.assertRaises(RuntimeError):
                    EventLog.open(path, session_date="2026-06-02")
            finally:
                # Force-close any sqlite connections opened by the
                # raising EventLog.open(). On CPython refs typically
                # drop here; sqlite releases the file handle when the
                # last connection object is GC'd.
                import gc
                gc.collect()

    def test_append_returns_monotonic_seq(self):
        with TemporaryDirectory() as td:
            log = EventLog.open(Path(td) / "x.sqlite", session_date="2026-06-01")
            s1 = log.append(EventType.PHASE, {"phase": "preopen"})
            s2 = log.append(EventType.PHASE, {"phase": "trading"})
            self.assertEqual(s1, 1)
            self.assertEqual(s2, 2)
            log.close()

    def test_iter_events_round_trip(self):
        with TemporaryDirectory() as td:
            log = EventLog.open(Path(td) / "x.sqlite", session_date="2026-06-01")
            log.log_phase("preopen")
            log.log_entry({"symbol": "NIFTY", "expiry": "2026-06-05", "entry_time": "09:20"})
            log.log_equity_tick({"ts": "09:21", "equity": 1_000_000})
            log.close()
            log = EventLog.open(Path(td) / "x.sqlite", session_date="2026-06-01")
            events = list(log.iter_events())
            self.assertEqual([e["type"] for e in events], ["phase", "entry", "equity_tick"])
            self.assertEqual(events[1]["symbol"], "NIFTY")
            log.close()

    def test_seq_continues_after_reopen(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "x.sqlite"
            log = EventLog.open(path, session_date="2026-06-01")
            log.append(EventType.PHASE, {})
            log.append(EventType.PHASE, {})
            log.close()
            log = EventLog.open(path, session_date="2026-06-01")
            self.assertEqual(log.append(EventType.PHASE, {}), 3)
            log.close()


class EventLogProjectionTests(unittest.TestCase):
    def test_open_positions_projection_excludes_exited(self):
        with TemporaryDirectory() as td:
            log = EventLog.open(Path(td) / "x.sqlite", session_date="2026-06-01")
            log.log_entry({"symbol": "NIFTY", "expiry": "2026-06-05", "entry_time": "09:20"})
            log.log_entry({"symbol": "SENSEX", "expiry": "2026-06-03", "entry_time": "09:20"})
            log.log_exit({"symbol": "NIFTY", "expiry": "2026-06-05", "entry_time": "09:20", "exit_pnl": 12000})
            open_ = log.project_open_positions()
            symbols = sorted(p["symbol"] for p in open_)
            self.assertEqual(symbols, ["SENSEX"])
            log.close()

    def test_equity_projection_returns_ticks_in_order(self):
        with TemporaryDirectory() as td:
            log = EventLog.open(Path(td) / "x.sqlite", session_date="2026-06-01")
            log.log_equity_tick({"ts": "09:20", "equity": 1_000_000})
            log.log_equity_tick({"ts": "09:21", "equity": 1_010_000})
            log.log_equity_tick({"ts": "09:22", "equity": 1_005_000})
            curve = log.project_equity_curve()
            self.assertEqual([t["equity"] for t in curve], [1_000_000, 1_010_000, 1_005_000])
            log.close()

    def test_completed_trades_projection(self):
        with TemporaryDirectory() as td:
            log = EventLog.open(Path(td) / "x.sqlite", session_date="2026-06-01")
            log.log_exit({"symbol": "NIFTY", "exit_pnl": 1000})
            log.log_exit({"symbol": "SENSEX", "exit_pnl": -200})
            trades = log.project_completed_trades()
            self.assertEqual([t["symbol"] for t in trades], ["NIFTY", "SENSEX"])
            log.close()


class EventLogConcurrencyTests(unittest.TestCase):
    def test_concurrent_appends_keep_unique_seq(self):
        with TemporaryDirectory() as td:
            log = EventLog.open(Path(td) / "x.sqlite", session_date="2026-06-01")
            seqs: list[int] = []
            seq_lock = threading.Lock()

            def worker(n: int):
                for _ in range(n):
                    s = log.append(EventType.EQUITY_TICK, {"x": 1})
                    with seq_lock:
                        seqs.append(s)

            threads = [threading.Thread(target=worker, args=(20,)) for _ in range(4)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            log.close()

            self.assertEqual(len(seqs), 80)
            self.assertEqual(len(set(seqs)), 80, "seqs must be unique")
            self.assertEqual(sorted(seqs), list(range(1, 81)))

    def test_wal_permits_concurrent_reader(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "x.sqlite"
            log = EventLog.open(path, session_date="2026-06-01")
            log.append(EventType.PHASE, {"phase": "preopen"})
            # Open a separate read-only connection — must not block on the writer.
            reader = sqlite3.connect(str(path), timeout=2.0)
            try:
                rows = list(reader.execute("SELECT type FROM events"))
            finally:
                reader.close()
            self.assertEqual(rows, [("phase",)])
            log.close()


class HeartbeatLivenessTests(unittest.TestCase):
    """Heartbeat events + the cross-process read_latest_events reader.

    These back the Phase-E1 SQLite liveness migration: the health monitor and
    dashboard read the latest heartbeat from the event log (canonical, survives
    the tmpfs reboot wipe) instead of the tmpfs JSON snapshots.
    """

    def test_log_and_project_latest_heartbeats(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "20260603.sqlite"
            log = EventLog.open(path, session_date="2026-06-03")
            log.log_heartbeat({"written_at": "t1", "phase": "connecting"})
            log.log_heartbeat({"written_at": "t2", "phase": "monitoring"})
            log.log_feed_heartbeat({"written_at": "f1", "connected": True})
            log.log_depth_heartbeat({"written_at": "d1", "ready_pct": 99.0})

            # project_latest returns the most-recent of a type.
            hb = log.project_latest(EventType.HEARTBEAT)
            self.assertIsNotNone(hb)
            self.assertEqual(hb["data"]["phase"], "monitoring")
            self.assertEqual(log.project_latest(EventType.FEED_HEARTBEAT)["data"]["connected"], True)
            self.assertEqual(log.project_latest(EventType.DEPTH_HEARTBEAT)["data"]["ready_pct"], 99.0)
            self.assertIsNone(log.project_latest(EventType.EXIT))
            log.close()

    def test_read_latest_events_reader(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "20260603.sqlite"
            log = EventLog.open(path, session_date="2026-06-03")
            log.log_heartbeat({"written_at": "t2", "phase": "monitoring"})
            log.log_feed_heartbeat({"written_at": "f1", "connected": True})
            log.close()

            out = read_latest_events(
                path,
                (EventType.HEARTBEAT, EventType.FEED_HEARTBEAT, EventType.DEPTH_HEARTBEAT),
            )
            self.assertEqual(out[EventType.HEARTBEAT]["data"]["phase"], "monitoring")
            self.assertEqual(out[EventType.FEED_HEARTBEAT]["data"]["connected"], True)
            # No depth heartbeat written → None, so callers fall back to JSON.
            self.assertIsNone(out[EventType.DEPTH_HEARTBEAT])

    def test_read_latest_events_missing_file_returns_none_entries(self):
        with TemporaryDirectory() as td:
            path = Path(td) / "does_not_exist.sqlite"
            out = read_latest_events(path, (EventType.HEARTBEAT, EventType.FEED_HEARTBEAT))
            self.assertEqual(out, {EventType.HEARTBEAT: None, EventType.FEED_HEARTBEAT: None})

    def test_read_latest_events_does_not_block_writer(self):
        # The reader opens read-only in WAL mode → concurrent with an open writer.
        with TemporaryDirectory() as td:
            path = Path(td) / "20260603.sqlite"
            log = EventLog.open(path, session_date="2026-06-03")
            log.log_heartbeat({"written_at": "t1", "phase": "connecting"})
            # Writer still open while we read.
            out = read_latest_events(path, (EventType.HEARTBEAT,))
            self.assertEqual(out[EventType.HEARTBEAT]["data"]["phase"], "connecting")
            log.append(EventType.PHASE, {"phase": "entry"})  # writer still usable
            log.close()


if __name__ == "__main__":
    unittest.main()

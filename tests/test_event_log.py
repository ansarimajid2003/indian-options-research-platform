"""Unit tests for ``options_backtest.live_event_log``."""

from __future__ import annotations

import json
import sqlite3
import threading
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from options_backtest.live_event_log import EventLog, EventType


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


if __name__ == "__main__":
    unittest.main()

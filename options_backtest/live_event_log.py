"""
Append-only SQLite event log — canonical state for one paper-trading session.

Before this module:
* Open positions lived in ``PaperTradingEngine._open_positions`` (list)
  with checkpoint writes to ``latest_open_positions.json`` on a 5 s
  timer. A crash between mutation and snapshot left the checkpoint
  *stale* and crash recovery silently dropped any state change in the
  gap (entry/exit lines that hadn't yet snapshotted).
* Equity ticks were appended to a JSONL file and de-duped by the
  dashboard after-the-fact.
* Signals appended to a separate JSONL file; restart added duplicate
  ``resumed_after_crash`` lines.
* Three append-only files + one overwrite-on-EOD JSON, no transaction
  boundary.

This module replaces all of that with an append-only event log:

* One row per *event* in a SQLite database, one DB per session
  (``live_root/event_log/YYYYMMDD.sqlite``).
* WAL mode for safe concurrent readers (the dashboard) while the engine
  writes.
* Open positions and the equity curve are **projections** of the log,
  not separately persisted authoritative state.
* On resume, ``replay()`` re-derives in-memory state from the log.

The legacy JSON / JSONL files continue to be written by the engine —
they remain the dashboard's primary read path for now. The event log is
the *authoritative* persistence layer; legacy files are derived views.
A subsequent step can remove them once dashboard + monitor are migrated.

Schema versioning is via a single ``meta`` row; bumps go through
``_MIGRATIONS``.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

__all__ = ["EventLog", "EventType", "read_latest_events"]

_log = logging.getLogger(__name__)

_SCHEMA_VERSION = 1


class EventType:
    """String constants for event ``type`` column. Add cases conservatively."""

    SESSION_OPEN = "session_open"
    SESSION_CLOSE = "session_close"
    PHASE = "phase"

    ENTRY = "entry"
    EXIT = "exit"

    SIGNAL = "signal"          # entry blocked by a gate (logged for traceability)
    EQUITY_TICK = "equity_tick"
    ALERT = "alert"

    RESTART = "restart"

    # Liveness heartbeats — periodic "I am alive and here is my state" events.
    # These make the event log the canonical liveness source so the health
    # monitor and dashboard no longer depend on tmpfs JSON-file mtimes (which
    # are wiped on reboot and were the proximate cause of the 2026-06-02
    # post-outage missing-snapshot alert storm). The payload mirrors the
    # corresponding legacy JSON snapshot so consumers need minimal changes.
    HEARTBEAT = "heartbeat"            # engine process_health snapshot
    FEED_HEARTBEAT = "feed_heartbeat"  # live-feed state snapshot
    DEPTH_HEARTBEAT = "depth_heartbeat"  # depth-collector cache snapshot


_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    type        TEXT NOT NULL,
    symbol      TEXT,
    data        TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS ix_events_type ON events(type);
CREATE INDEX IF NOT EXISTS ix_events_symbol ON events(symbol);
CREATE INDEX IF NOT EXISTS ix_events_ts ON events(ts);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

_MIGRATIONS: list[str] = [
    # Migrations live here as forward-only DDL strings. v1 corresponds to
    # the schema above.
]


class EventLog:
    """Append-only event log for one paper-trading session.

    Thread-safe: a single ``threading.Lock`` serialises writes. SQLite's
    WAL mode permits concurrent readers (the dashboard) without taking
    the writer lock.

    Usage::

        log = EventLog.open(live_root / "event_log" / "20260601.sqlite",
                            session_date="2026-06-01")
        log.append(EventType.ENTRY, position_dict, symbol="NIFTY")
        positions = log.project_open_positions()
        log.close()
    """

    def __init__(self, conn: sqlite3.Connection, path: Path) -> None:
        self._conn = conn
        self._path = path
        self._write_lock = threading.Lock()
        self._seq = self._read_max_seq()

    # ─── construction / lifecycle ─────────────────────────────────────────

    @classmethod
    def open(cls, path: Path, *, session_date: str) -> "EventLog":
        """Open or create a session's event log.

        ``session_date`` is the ISO date string (``YYYY-MM-DD``); it is
        stored in the ``meta`` table and verified on subsequent opens.
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(
            str(path),
            isolation_level=None,    # autocommit; we manage transactions explicitly
            timeout=30.0,            # block up to 30 s on write contention
            check_same_thread=False, # we serialise with our own lock
        )
        # WAL for concurrent readers; synchronous=NORMAL is the standard
        # tradeoff (fsync on commit boundaries, not every write).
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.executescript(_CREATE_SQL)
        for migration in _MIGRATIONS:
            conn.executescript(migration)

        # Stamp / verify session metadata.
        existing = conn.execute(
            "SELECT value FROM meta WHERE key = 'session_date'"
        ).fetchone()
        if existing is None:
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('session_date', ?)",
                (session_date,),
            )
            conn.execute(
                "INSERT INTO meta(key, value) VALUES('schema_version', ?)",
                (str(_SCHEMA_VERSION),),
            )
        elif existing[0] != session_date:
            raise RuntimeError(
                f"event_log session_date mismatch: file={existing[0]} caller={session_date}"
            )
        log = cls(conn, path)
        _log.info("event_log: opened %s (session_date=%s seq=%d)", path, session_date, log._seq)
        return log

    def close(self) -> None:
        with self._write_lock:
            try:
                # Merge the WAL back into the main DB so the OS releases
                # the ``.sqlite-wal`` / ``.sqlite-shm`` sidecar files.
                # Without this, on Windows the tempdir cleanup raises
                # PermissionError ("file is being used by another
                # process").
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except Exception:
                pass
            try:
                self._conn.close()
            except Exception:
                pass

    @property
    def path(self) -> Path:
        return self._path

    # ─── append API ───────────────────────────────────────────────────────

    def append(
        self,
        type_: str,
        data: dict[str, Any] | None = None,
        *,
        symbol: str | None = None,
        ts: str | None = None,
    ) -> int:
        """Append one event row. Returns the new ``seq``.

        ``data`` is JSON-serialised; ``None`` becomes ``{}``. ``ts`` may
        be supplied (ISO 8601 string); otherwise ``datetime.now()`` is
        used.
        """
        payload = json.dumps(data if data is not None else {}, sort_keys=True, default=_json_default)
        ts_iso = ts or datetime.now().isoformat()
        with self._write_lock:
            self._seq += 1
            self._conn.execute(
                "INSERT INTO events(ts, seq, type, symbol, data) VALUES(?, ?, ?, ?, ?)",
                (ts_iso, self._seq, type_, symbol, payload),
            )
            return self._seq

    # Convenience wrappers — semantically meaningful event types.

    def log_session_open(self, profile_name: str, pid: int) -> int:
        return self.append(
            EventType.SESSION_OPEN,
            {"profile": profile_name, "pid": pid},
        )

    def log_session_close(self, phase: str) -> int:
        return self.append(EventType.SESSION_CLOSE, {"phase": phase})

    def log_phase(self, phase: str) -> int:
        return self.append(EventType.PHASE, {"phase": phase})

    def log_entry(self, position: dict[str, Any]) -> int:
        return self.append(EventType.ENTRY, position, symbol=position.get("symbol"))

    def log_exit(self, exit_payload: dict[str, Any]) -> int:
        return self.append(EventType.EXIT, exit_payload, symbol=exit_payload.get("symbol"))

    def log_signal(self, signal_payload: dict[str, Any]) -> int:
        return self.append(
            EventType.SIGNAL,
            signal_payload,
            symbol=signal_payload.get("symbol"),
        )

    def log_equity_tick(self, tick: dict[str, Any]) -> int:
        return self.append(EventType.EQUITY_TICK, tick)

    def log_alert(self, alert: dict[str, Any]) -> int:
        return self.append(EventType.ALERT, alert, symbol=alert.get("symbol"))

    def log_restart(self, reason: str | None, gap_minutes: float | None) -> int:
        return self.append(
            EventType.RESTART,
            {"reason": reason, "gap_minutes": gap_minutes},
        )

    def log_heartbeat(self, snapshot: dict[str, Any]) -> int:
        return self.append(EventType.HEARTBEAT, snapshot)

    def log_feed_heartbeat(self, snapshot: dict[str, Any]) -> int:
        return self.append(EventType.FEED_HEARTBEAT, snapshot)

    def log_depth_heartbeat(self, snapshot: dict[str, Any]) -> int:
        return self.append(EventType.DEPTH_HEARTBEAT, snapshot)

    # ─── read / projection API ────────────────────────────────────────────

    def iter_events(
        self,
        *,
        type_: str | None = None,
        symbol: str | None = None,
    ) -> Iterable[dict[str, Any]]:
        """Yield events in insertion order."""
        sql = "SELECT ts, seq, type, symbol, data FROM events"
        clauses = []
        params: list[Any] = []
        if type_ is not None:
            clauses.append("type = ?")
            params.append(type_)
        if symbol is not None:
            clauses.append("symbol = ?")
            params.append(symbol)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY seq ASC"
        cur = self._conn.execute(sql, params)
        for ts, seq, etype, sym, payload in cur:
            yield {
                "ts": ts,
                "seq": seq,
                "type": etype,
                "symbol": sym,
                "data": json.loads(payload) if payload else {},
            }

    def project_open_positions(self) -> list[dict[str, Any]]:
        """Replay entry/exit events and return the set of open positions.

        A position is identified by ``(symbol, expiry, entry_time)``.
        Exits supersede entries; the result lists only positions that
        have been entered but not exited.
        """
        open_map: dict[tuple[str, str, str], dict[str, Any]] = {}
        for event in self.iter_events():
            if event["type"] == EventType.ENTRY:
                p = event["data"]
                key = (p.get("symbol", ""), str(p.get("expiry", "")), str(p.get("entry_time", "")))
                open_map[key] = p
            elif event["type"] == EventType.EXIT:
                p = event["data"]
                key = (p.get("symbol", ""), str(p.get("expiry", "")), str(p.get("entry_time", "")))
                open_map.pop(key, None)
        return list(open_map.values())

    def project_equity_curve(self) -> list[dict[str, Any]]:
        """Return equity ticks ordered by insertion."""
        return [e["data"] for e in self.iter_events(type_=EventType.EQUITY_TICK)]

    def project_completed_trades(self) -> list[dict[str, Any]]:
        """Return all exit payloads in order."""
        return [e["data"] for e in self.iter_events(type_=EventType.EXIT)]

    def project_latest(self, type_: str) -> dict[str, Any] | None:
        """Return the most-recent event of ``type_`` as ``{ts, seq, data}``.

        Used for liveness reads (latest heartbeat of each kind). Returns
        ``None`` if no such event exists yet.
        """
        row = self._conn.execute(
            "SELECT ts, seq, data FROM events WHERE type = ? ORDER BY seq DESC LIMIT 1",
            (type_,),
        ).fetchone()
        if row is None:
            return None
        ts, seq, payload = row
        return {"ts": ts, "seq": seq, "data": json.loads(payload) if payload else {}}

    # ─── helpers ──────────────────────────────────────────────────────────

    def _read_max_seq(self) -> int:
        cur = self._conn.execute("SELECT COALESCE(MAX(seq), 0) FROM events")
        return int(cur.fetchone()[0])

    @contextmanager
    def transaction(self):
        """Optional explicit transaction for batch inserts.

        Not required for correctness — individual ``append`` calls
        autocommit — but useful when batching equity ticks or a multi-
        leg entry to reduce fsync overhead.
        """
        with self._write_lock:
            self._conn.execute("BEGIN")
            try:
                yield self
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise


def read_latest_events(
    path: Path, types: Iterable[str]
) -> dict[str, dict[str, Any] | None]:
    """Read the latest event of each requested type from a session log, read-only.

    For cross-process consumers (health monitor, dashboard bridge) that must
    NOT take the engine's writer connection. Opens the SQLite file read-only in
    WAL mode (concurrent with the engine's writes) and returns
    ``{type: {ts, seq, data} | None}``. Returns all-``None`` if the file does
    not exist yet (cold-start window before the engine creates the log).

    Never raises on a missing/locked/corrupt file — returns ``None`` entries so
    callers fall back to their legacy JSON-snapshot read path.
    """
    types = list(types)
    result: dict[str, dict[str, Any] | None] = {t: None for t in types}
    if not Path(path).exists():
        return result
    conn: sqlite3.Connection | None = None
    try:
        # immutable=0, read-only; WAL readers don't block the writer.
        conn = sqlite3.connect(
            f"file:{Path(path).as_posix()}?mode=ro", uri=True, timeout=2.0
        )
        conn.execute("PRAGMA busy_timeout=2000")
        for t in types:
            try:
                row = conn.execute(
                    "SELECT ts, seq, data FROM events WHERE type = ? ORDER BY seq DESC LIMIT 1",
                    (t,),
                ).fetchone()
            except sqlite3.Error:
                row = None
            if row is not None:
                ts, seq, payload = row
                result[t] = {
                    "ts": ts,
                    "seq": seq,
                    "data": json.loads(payload) if payload else {},
                }
    except sqlite3.Error as exc:
        _log.debug("read_latest_events: %s unreadable (%r) — falling back", path, exc)
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass
    return result


def _json_default(obj: Any) -> Any:
    """Fallback JSON serialiser for ``date``/``datetime``/``pd.Timestamp``.

    Keeps the schema textual and round-trippable through ``fromisoformat``.
    """
    if hasattr(obj, "isoformat"):
        return obj.isoformat()
    if isinstance(obj, set):
        return sorted(obj)
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serialisable")

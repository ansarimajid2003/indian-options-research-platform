"""
NSE 20-level order-book collector for Wing-6 paper trading.

Manages up to five Dhan 20-depth websocket connections (<=50 instruments each):
  Configured major-index universe: NIFTY + FINNIFTY + MIDCPNIFTY
  Default capture width: ATM +/- 20 strikes = 246 NSE option contracts

Binary packet format (Dhan twentydepth feed):
  Header   : 12 bytes  — <hh i i>  (feed_code int16, msg_len int16, security_id int32, ts_ms int32)
  Per level: 16 bytes  — <f  i i i> (price float32, qty int32, orders int32, reserved int32)
  Bid packet code: 41  (20 bid levels follow header)
  Ask packet code: 51  (20 ask levels follow header)
  Total packet size: 12 + 20 * 16 = 332 bytes

Usage (from run_paper_trading.py):
    await collect_order_book(profile, today, depth_cache)

Standalone dry-run (market closed — writes only gap sentinel and exits):
    python -m scripts.live.collect_order_book --dry-run
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import queue
import struct
import threading
import time as _time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Callable, Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import websockets

_log = logging.getLogger(__name__)

# Adjust import path when running as module vs standalone
try:
    from options_backtest.depth_cache import DepthCache, DepthLevel
    from options_backtest.dhan_client import DhanCredentials, get_dhan_client
    from options_backtest.dhan_instruments import NSE_INDEX_DEPTH_SYMBOLS
    from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
    from options_backtest.live_resolver import LiveDhanContractResolver
    from options_backtest.schemas import OptionType
    from options_backtest.calendar import get_instrument_spec, expiry_on_or_after
    from options_backtest.live_event_log import EventLog
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from options_backtest.depth_cache import DepthCache, DepthLevel
    from options_backtest.dhan_client import DhanCredentials, get_dhan_client
    from options_backtest.dhan_instruments import NSE_INDEX_DEPTH_SYMBOLS
    from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
    from options_backtest.live_resolver import LiveDhanContractResolver
    from options_backtest.schemas import OptionType
    from options_backtest.calendar import get_instrument_spec, expiry_on_or_after
    from options_backtest.live_event_log import EventLog

_IST = "Asia/Kolkata"
_DEPTH_WS_URL = (
    "wss://depth-api-feed.dhan.co/twentydepth"
    "?token={token}&clientId={client_id}&authType=2"
)
_SUB_CODE = 23
_UNSUB_CODE = 12     # RequestCode 12 — clean disconnect (per SDK marketfeed.py:186-194)
_BID_CODE = 41
_ASK_CODE = 51
_DISCONNECT_CODE = 50
_HEADER_FMT = "<hBBiI"   # msg_len(int16), feed_code(uint8), exch_seg(uint8), security_id(int32), reserved/disconnect_code(uint32)
_LEVEL_FMT = "<dII"      # price(float64), qty(uint32), orders(uint32)
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)   # 12
_LEVEL_SIZE = struct.calcsize(_LEVEL_FMT)     # 16
_LEVELS = 20
_PACKET_SIZE = _HEADER_SIZE + _LEVELS * _LEVEL_SIZE   # 332
_MAX_PER_CONN = 50

# Fatal disconnect codes — do NOT reconnect after these. Mirrors the
# live-feed handling in options_backtest/paper_engine.py and the SDK's
# documented codes in third_party/DhanHQ-py/src/dhanhq/fulldepth.py:358-376.
_FATAL_DISCONNECT_CODES = {805, 806, 807, 808, 809}
_DISCONNECT_REASONS = {
    805: "No. of active websocket connections exceeded",
    806: "Subscribe to Data APIs to continue",
    807: "Access token is expired",
    808: "Invalid client id",
    809: "Authentication failed",
}
_DEFAULT_ATM_OFFSET_RANGE = 20
_DEFAULT_MAX_DEPTH_CONNECTIONS = 5
_FLUSH_INTERVAL = 15        # seconds
_RECONNECT_DELAY = 5        # seconds between reconnect attempts
_PING_INTERVAL = 10         # seconds between server pings
_WRITER_QUEUE_MAX_ITEMS = 100_000
_WRITER_STOP_TIMEOUT = 10.0
_WRITER_DROP_LOG_INTERVAL = 30.0
# Hard upper bound on how long the collector teardown (cancel-then-gather of the
# DepthCollector + heartbeat tasks) may take. A non-cancellable `to_thread` write
# can briefly outlive a cancel; this bound guarantees the coroutine returns so the
# supervisor's own teardown + ledger update are never starved (2026-06-08 hang).
_COLLECTOR_TEARDOWN_TIMEOUT = float(os.environ.get("COLLECTOR_TEARDOWN_TIMEOUT", "25.0"))
_ATOMIC_WRITE_LOCKS: dict[Path, threading.Lock] = {}
_ATOMIC_WRITE_LOCKS_GUARD = threading.Lock()

# NSE options universe for 20-depth; concrete width comes from live config.
# Single source of truth lives in options_backtest.dhan_instruments; we
# materialise as ``list`` here because legacy code paths mutate it.
_MAJOR_NSE_INDEX_SYMBOLS = list(NSE_INDEX_DEPTH_SYMBOLS)
_NSE_SYMBOLS = _MAJOR_NSE_INDEX_SYMBOLS  # Backward-compatible alias for the shadowed legacy coroutine.


def _now_ist() -> pd.Timestamp:
    return pd.Timestamp.now(tz=_IST)


# Minimum free space (GB) required at session start before the collector will
# run. Env-overridable (``LIVE_MIN_FREE_GB``) and kept in sync with the health
# monitor's pre-run threshold (``WD_MIN_GB_PRE_RUN``, also 40 GB) so the *warning*
# and the *fatal gate* never disagree — a 2026-06-04 no-trade was caused by the
# monitor warning being relaxed to 40 GB while this gate stayed at a fatal 100 GB.
# A session needs ~15-20 GB; 40 GB keeps ~2-session headroom above the 20 GB
# intraday-critical floor. The old 100 GB was a *month-long-run* sizing number
# wrongly applied as a per-session abort.
_LIVE_MIN_FREE_GB = float(os.environ.get("LIVE_MIN_FREE_GB", "40.0"))


def _live_root() -> Path:
    """Resolve the ``data/live`` symlink and validate it points at the
    configured live-storage mount.

    The live root must match ``LIVE_ROOT`` (the same value systemd passes to the
    engine + monitor) so the collector can never silently write to the SSD/root
    when the symlink is missing or misconfigured. ``LIVE_ROOT`` defaults to the
    historical WD path; switching drives (e.g. WD->Toshiba) is a single env
    change in the systemd unit plus repointing the symlink — no code edit.
    """
    repo_root = Path(__file__).parents[2]
    live_path = (repo_root / "data" / "live").resolve()
    expected = Path(
        os.environ.get("LIVE_ROOT", "/media/WD-Storage/indian-markets-live")
    ).resolve()
    if live_path != expected:
        raise RuntimeError(
            f"data/live resolves to {live_path}, expected {expected} "
            "(from LIVE_ROOT). Collector aborts to protect writable live storage."
        )
    return live_path


def _check_wd_space(live_root: Path, min_gb: float | None = None) -> None:
    """Abort the session if the live-storage mount is below the minimum free
    space. ``min_gb`` defaults to ``_LIVE_MIN_FREE_GB`` (env ``LIVE_MIN_FREE_GB``,
    40 GB) — applies to whichever drive ``live_root`` lives on (WD or Toshiba)."""
    import shutil
    if min_gb is None:
        min_gb = _LIVE_MIN_FREE_GB
    stat = shutil.disk_usage(str(live_root))
    free_gb = stat.free / (1024 ** 3)
    if free_gb < min_gb:
        raise RuntimeError(
            f"live storage has only {free_gb:.1f} GB free at {live_root}; "
            f"need >= {min_gb} GB to start a session."
        )


def _parse_packet(raw: bytes) -> tuple[int, int, list[DepthLevel]] | None:
    """Parse one 332-byte 20-depth packet. Returns (feed_code, security_id, levels) or None.

    Header layout (12 bytes, little-endian):
      offset 0: int16  msg_len
      offset 2: uint8  feed_response_code (41=bid, 51=ask, 50=disconnect)
      offset 3: uint8  exchange_segment
      offset 4: int32  security_id
      offset 8: uint32 reserved (NoofRows for 200-depth)

    Level layout (16 bytes × 20, little-endian):
      offset 0: float64 price
      offset 8: uint32  quantity
      offset 12: uint32 orders
    """
    if len(raw) < _PACKET_SIZE:
        return None
    _msg_len, feed_code, _exch_seg, security_id, _reserved = struct.unpack_from(_HEADER_FMT, raw, 0)
    if feed_code not in (_BID_CODE, _ASK_CODE):
        return None
    levels: list[DepthLevel] = []
    for i in range(_LEVELS):
        offset = _HEADER_SIZE + i * _LEVEL_SIZE
        price, qty, orders = struct.unpack_from(_LEVEL_FMT, raw, offset)
        if price > 0:
            levels.append(DepthLevel(price=round(float(price), 2), quantity=int(qty), orders=int(orders)))
    return feed_code, security_id, levels


def _iter_packets(raw: bytes):
    """Yield individual Dhan depth packets from a possibly stacked message."""
    offset = 0
    raw_len = len(raw)
    while offset + _HEADER_SIZE <= raw_len:
        msg_len = struct.unpack_from("<h", raw, offset)[0]
        packet_len = msg_len if msg_len >= _HEADER_SIZE else _PACKET_SIZE
        if offset + packet_len > raw_len:
            break
        yield raw[offset : offset + packet_len]
        offset += packet_len


def _disconnect_code(raw: bytes) -> int | None:
    """Return the disconnect code from a depth-feed disconnect packet.

    The SDK (``third_party/DhanHQ-py/src/dhanhq/fulldepth.py:358-376``)
    reads the code from the 4-byte ``reserved`` field at byte offset 8
    inside the 12-byte ``<hBBiI>`` header. Previously this implementation
    read a trailing 2-byte int at offset 12, which is into the first
    level's price field and yielded garbage — so 805 ("active websocket
    connections exceeded") was being silently swallowed.

    Returning the code lets callers treat 805/807/808/809 as fatal and
    abort the reconnect loop (mirrors live-feed behaviour in
    ``options_backtest/paper_engine.py``).
    """
    if len(raw) >= _HEADER_SIZE:
        _msg_len, feed_code, _exch_seg, _security_id, reserved = struct.unpack_from(_HEADER_FMT, raw, 0)
        if feed_code == _DISCONNECT_CODE:
            return int(reserved)
    return None


def _instrument_key(meta: dict) -> str:
    return f"{meta['symbol']}_{meta['expiry']}_{meta['strike']}_{meta['option_type']}"


def _build_depth_row(snap_bid: list[DepthLevel], snap_ask: list[DepthLevel], meta: dict, ts: pd.Timestamp) -> dict:
    """Build a normalized depth row dict from bid/ask snapshots."""
    row: dict = {"timestamp": ts.isoformat()}
    for i, lv in enumerate(snap_bid[:20], 1):
        row[f"bid_p{i}"] = lv.price
        row[f"bid_q{i}"] = lv.quantity
        row[f"bid_o{i}"] = lv.orders
    for i in range(len(snap_bid) + 1, 21):
        row[f"bid_p{i}"] = row[f"bid_q{i}"] = row[f"bid_o{i}"] = 0
    for i, lv in enumerate(snap_ask[:20], 1):
        row[f"ask_p{i}"] = lv.price
        row[f"ask_q{i}"] = lv.quantity
        row[f"ask_o{i}"] = lv.orders
    for i in range(len(snap_ask) + 1, 21):
        row[f"ask_p{i}"] = row[f"ask_q{i}"] = row[f"ask_o{i}"] = 0
    best_bid = snap_bid[0].price if snap_bid else 0.0
    best_ask = snap_ask[0].price if snap_ask else 0.0
    mid = (best_bid + best_ask) / 2 if best_bid and best_ask else 0.0
    spread_abs = best_ask - best_bid
    spread_pct = spread_abs / mid if mid > 0 else 0.0
    total_bid = sum(lv.quantity for lv in snap_bid)
    total_ask = sum(lv.quantity for lv in snap_ask)
    total = total_bid + total_ask
    imbalance = (total_bid - total_ask) / total if total > 0 else 0.0
    qty1 = snap_bid[0].quantity if snap_bid else 0
    qty1_ask = snap_ask[0].quantity if snap_ask else 0
    vwap_buy = sum(lv.price * lv.quantity for lv in snap_ask) / total_ask if total_ask else 0.0
    vwap_sell = sum(lv.price * lv.quantity for lv in snap_bid) / total_bid if total_bid else 0.0
    row.update({
        "best_bid": best_bid, "best_ask": best_ask, "mid": mid,
        "spread_abs": spread_abs, "spread_pct": spread_pct,
        "total_bid_qty": total_bid, "total_ask_qty": total_ask, "imbalance": imbalance,
        "depth_vwap_buy_lots_1": vwap_buy, "depth_vwap_sell_lots_1": vwap_sell,
        "depth_qty_at_best_bid": qty1, "depth_qty_at_best_ask": qty1_ask,
    })
    return row


class _WriterThread:
    """Dedicated thread for all blocking disk I/O."""

    def __init__(self, date_str: str, live_root: Path, max_queue_size: int = _WRITER_QUEUE_MAX_ITEMS) -> None:
        self._date_str = date_str
        self._raw_dir = live_root / "raw_depth_packets" / date_str
        self._norm_dir = live_root / "order_book" / date_str
        self._dir_1min = live_root / "order_book_1min" / date_str
        for d in (self._raw_dir, self._norm_dir, self._dir_1min):
            d.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue = queue.Queue(maxsize=max_queue_size)
        self._stop_requested = False
        self._flush_seq = 0
        self._error: BaseException | None = None
        self._dropped_raw = 0
        self._dropped_norm = 0
        self._last_drop_log = 0.0
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        # Unique suffix appended to every parquet temp file. Without
        # this, two ``_WriterThread`` instances writing to the same
        # ``order_book/YYYYMMDD/`` directory race on
        # ``depth_<bucket>_<seq>.parquet.tmp`` and the loser's
        # ``replace()`` raises FileNotFoundError. This was the exact
        # bug observed on 2026-05-22 and 2026-05-25.
        self._tmp_suffix = f".{os.getpid()}.{id(self)}.{_time.time_ns()}.tmp"
        self._thread.start()

    def enqueue_raw(self, raw: bytes) -> bool:
        return self._enqueue(("raw", raw), "raw")

    def enqueue_norm(self, ikey: str, row: dict) -> bool:
        return self._enqueue(("norm", ikey, row), "norm")

    def _enqueue(self, item: tuple, kind: str) -> bool:
        if self._stop_requested or self._error is not None:
            return False
        try:
            self._q.put_nowait(item)
            return True
        except queue.Full:
            with self._lock:
                if kind == "raw":
                    self._dropped_raw += 1
                else:
                    self._dropped_norm += 1
                now = _time.monotonic()
                if now - self._last_drop_log >= _WRITER_DROP_LOG_INTERVAL:
                    self._last_drop_log = now
                    _log.error(
                        "depth_writer: queue full, dropping %s packets raw_dropped=%d norm_dropped=%d qsize=%d",
                        kind,
                        self._dropped_raw,
                        self._dropped_norm,
                        self._safe_qsize(),
                    )
            return False

    def _safe_qsize(self) -> int:
        try:
            return self._q.qsize()
        except NotImplementedError:
            return -1

    def status(self) -> dict:
        with self._lock:
            return {
                "alive": self._thread.is_alive(),
                "queue_size": self._safe_qsize(),
                "queue_max_size": self._q.maxsize,
                "dropped_raw": self._dropped_raw,
                "dropped_norm": self._dropped_norm,
                "error": repr(self._error) if self._error is not None else None,
            }

    def _loop(self) -> None:
        try:
            self._run_loop()
        except Exception as exc:
            self._error = exc
            self._stop_requested = True
            _log.exception("depth_writer: fatal writer thread failure; writer is disabled: %r", exc)

    def _run_loop(self) -> None:
        raw_buffer: list[bytes] = []
        norm_rows: dict[str, list[dict]] = defaultdict(list)
        last_raw_flush = _time.monotonic()
        last_norm_flush = _time.monotonic()

        while True:
            try:
                item = self._q.get(timeout=1.0)
            except queue.Empty:
                item = None

            if item is not None and item[0] == "stop":
                self._stop_requested = True
                break

            if item is not None:
                kind = item[0]
                if kind == "raw":
                    raw_buffer.append(item[1])
                elif kind == "norm":
                    norm_rows[item[1]].append(item[2])

            now = _time.monotonic()
            if now - last_raw_flush >= _FLUSH_INTERVAL:
                if raw_buffer:
                    self._flush_raw(raw_buffer)
                    raw_buffer = []
                last_raw_flush = now
            if now - last_norm_flush >= _FLUSH_INTERVAL:
                if norm_rows:
                    self._flush_norm(norm_rows)
                    norm_rows = defaultdict(list)
                last_norm_flush = now

        # Final flush after stop
        if raw_buffer:
            self._flush_raw(raw_buffer)
        if norm_rows:
            self._flush_norm(norm_rows)

    def _flush_raw(self, buffer: list[bytes]) -> None:
        if not buffer:
            return
        ts_str = datetime.now().strftime("%H%M%S%f")
        out_path = self._raw_dir / f"depth_{ts_str}.bin"
        out_path.write_bytes(b"".join(buffer))

    def _flush_norm(self, rows_by_ikey: dict[str, list[dict]]) -> None:
        if not rows_by_ikey:
            return
        self._flush_seq += 1
        seq = self._flush_seq
        minute_bucket = datetime.now().strftime("%H%M")
        all_rows: list[dict] = []
        for ikey, rows in rows_by_ikey.items():
            for row in rows:
                row_with_key = dict(row)
                row_with_key["instrument_key"] = ikey
                all_rows.append(row_with_key)
        if not all_rows:
            return
        df = pd.DataFrame(all_rows)
        out_path = self._norm_dir / f"depth_{minute_bucket}_{seq:04d}.parquet"
        tmp_path = out_path.with_suffix(out_path.suffix + self._tmp_suffix)
        pq.write_table(pa.Table.from_pandas(df), str(tmp_path), compression="snappy")
        tmp_path.replace(out_path)
        self._write_1min(df, minute_bucket, seq)

    def _write_1min(self, df: pd.DataFrame, minute_bucket: str, seq: int) -> None:
        df2 = df.copy()
        df2["timestamp"] = _parse_depth_timestamps(df2["timestamp"])
        bad_rows = int(df2["timestamp"].isna().sum())
        if bad_rows:
            _log.warning("depth_writer: dropping %d rows with unparseable timestamps", bad_rows)
            df2 = df2.dropna(subset=["timestamp"])
        if df2.empty:
            return
        if "mid" not in df2.columns or "instrument_key" not in df2.columns:
            return
        frames: list[pd.DataFrame] = []
        for ikey, group in df2.groupby("instrument_key", sort=False):
            group = group.set_index("timestamp").sort_index()
            agg = group["mid"].resample("1min").ohlc()
            agg["volume"] = group["total_bid_qty"].resample("1min").sum()
            agg["spread_pct_mean"] = group["spread_pct"].resample("1min").mean()
            agg = agg.reset_index()
            agg["instrument_key"] = ikey
            frames.append(agg)
        if not frames:
            return
        out_df = pd.concat(frames, ignore_index=True)
        out_path = self._dir_1min / f"depth_1min_{minute_bucket}_{seq:04d}.parquet"
        tmp_path = out_path.with_suffix(out_path.suffix + self._tmp_suffix)
        pq.write_table(pa.Table.from_pandas(out_df), str(tmp_path), compression="snappy")
        tmp_path.replace(out_path)

    def stop(self) -> None:
        self._stop_requested = True
        try:
            self._q.put(("stop",), timeout=1.0)
        except queue.Full:
            _log.error("depth_writer: queue still full during stop; leaving daemon writer to terminate with process")
        self._thread.join(timeout=_WRITER_STOP_TIMEOUT)
        if self._thread.is_alive():
            _log.error("depth_writer: writer did not stop within %.1fs status=%s", _WRITER_STOP_TIMEOUT, self.status())


def _parse_depth_timestamps(values: pd.Series) -> pd.Series:
    """Parse Dhan depth timestamps with or without fractional seconds."""
    try:
        return pd.to_datetime(values, format="ISO8601", errors="coerce")
    except (TypeError, ValueError):
        return values.map(_parse_one_depth_timestamp)


def _parse_one_depth_timestamp(value: object) -> pd.Timestamp:
    if value is None:
        return pd.NaT
    try:
        return pd.Timestamp(value)
    except Exception:
        return pd.NaT


def _write_gap_sentinel(live_root: Path, date_str: str, symbol: str, gap_start: str, gap_end: str, reason: str) -> None:
    alerts_dir = live_root / "alerts"
    alerts_dir.mkdir(parents=True, exist_ok=True)
    sentinel = {
        "type": "data_gap",
        "symbol": symbol,
        "gap_start": gap_start,
        "gap_end": gap_end,
        "reason": reason,
        "gap_minutes": round(
            (pd.Timestamp(gap_end) - pd.Timestamp(gap_start)).total_seconds() / 60, 1
        ),
    }
    out_path = alerts_dir / f"{date_str}_gaps.jsonl"
    with open(out_path, "a") as f:
        f.write(json.dumps(sentinel) + "\n")


def _write_atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{_time.time_ns()}.tmp"
    )
    target = path.resolve()
    with _ATOMIC_WRITE_LOCKS_GUARD:
        lock = _ATOMIC_WRITE_LOCKS.setdefault(target, threading.Lock())
    with lock:
        try:
            with tmp.open("w", encoding="utf-8") as f:
                f.write(json.dumps(data, indent=2, default=str))
                f.flush()
            for attempt in range(5):
                try:
                    tmp.replace(path)
                    break
                except PermissionError:
                    if attempt == 4:
                        raise
                    _time.sleep(0.01 * (attempt + 1))
        finally:
            tmp.unlink(missing_ok=True)


def _write_depth_cache_snapshot(
    live_root: Path,
    date_str: str,
    depth_cache: DepthCache,
    security_ids: list[str],
    id_to_meta: dict[str, dict] | None = None,
    event_log: "EventLog | None" = None,
) -> None:
    summary = depth_cache.readiness_summary(security_ids, max_age_seconds=5)
    now = _now_ist()
    tracked = set(depth_cache.tracked_ids())

    # Per-security top-of-book (best bid/ask + qty + age)
    tob_rows: dict[str, dict] = {}
    for sid in tracked:
        snap = depth_cache.snapshot(sid)
        if snap is None:
            continue
        bid_age_ms = int((now - snap.bid_ts).total_seconds() * 1000)
        ask_age_ms = int((now - snap.ask_ts).total_seconds() * 1000)
        tob_rows[sid] = {
            "bid": snap.best_bid,
            "ask": snap.best_ask,
            "bid_qty": snap.total_bid_qty,
            "ask_qty": snap.total_ask_qty,
            "bid_age_ms": bid_age_ms,
            "ask_age_ms": ask_age_ms,
        }

    by_symbol: dict[str, dict[str, float | int]] = {}
    if id_to_meta:
        for sid in security_ids:
            meta = id_to_meta.get(sid, {})
            symbol = str(meta.get("symbol", "UNKNOWN"))
            rec = by_symbol.setdefault(symbol, {"total": 0, "tracked": 0, "ready": 0, "ready_pct": 0.0})
            rec["total"] = int(rec["total"]) + 1
            if sid in tracked:
                rec["tracked"] = int(rec["tracked"]) + 1
            if depth_cache.is_ready(sid, max_age_seconds=5):
                rec["ready"] = int(rec["ready"]) + 1
        for rec in by_symbol.values():
            total = int(rec["total"])
            rec["ready_pct"] = round(float(rec["ready"]) / total * 100, 2) if total else 0.0

    payload = {
        "written_at": now.isoformat(),
        "session_date": date_str,
        "pid": os.getpid(),
        "configured_security_ids": len(security_ids),
        "tracked_security_ids": len(tracked),
        "ready": int(summary["ready"]),
        "total": int(summary["total"]),
        "ready_pct": round(float(summary["ready_pct"]), 2),
        "by_symbol": by_symbol,
        "tob": tob_rows,
    }
    _write_atomic_json(resolve_snapshot_dir(live_root) / "latest_depth_cache.json", payload)
    # Mirror depth liveness to the event log (canonical source; survives tmpfs
    # reboot wipe). Optional — callers that don't own a log pass None (dry-run,
    # tests, reconcile probes).
    if event_log is not None:
        try:
            event_log.log_depth_heartbeat(payload)
        except Exception:
            _log.debug("event_log: log_depth_heartbeat failed", exc_info=True)


def _write_collector_state(
    live_root: Path,
    date_str: str,
    status: str,
    security_ids: list[str],
    configured_symbols: list[str] | None = None,
    failed_symbols: list[str] | None = None,
    writer_status: dict | None = None,
) -> None:
    payload = {
        "written_at": _now_ist().isoformat(),
        "session_date": date_str,
        "pid": os.getpid(),
        "status": status,
        "configured_security_ids": len(security_ids),
        "configured_symbols": configured_symbols or [],
        "failed_symbols": failed_symbols or [],
        "writer_status": writer_status or {},
    }
    _write_atomic_json(resolve_durable_dir(live_root) / "latest_depth_collector_state.json", payload)


def _write_restart_gap_if_needed(live_root: Path, date_str: str) -> bool:
    """Record a restart gap if the previous collector died while marked running."""
    state_path = resolve_durable_dir(live_root) / "latest_depth_collector_state.json"
    if not state_path.exists():
        return False
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return False
    if state.get("session_date") != date_str or state.get("status") != "running":
        return False
    gap_start = state.get("written_at")
    if not gap_start:
        return False
    gap_end = _now_ist().isoformat()
    _write_gap_sentinel(live_root, date_str, "NSE_MAJOR_INDICES", gap_start, gap_end, "process_restart")
    return True


async def _depth_snapshot_loop(
    live_root: Path,
    date_str: str,
    depth_cache: DepthCache,
    security_ids: list[str],
    id_to_meta: dict[str, dict],
    writer_status_getter: Callable[[], dict] | None = None,
    configured_symbols: list[str] | None = None,
    failed_symbols: list[str] | None = None,
    interval_seconds: float = 10.0,
    event_log: "EventLog | None" = None,
) -> None:
    # Cancellation-responsive heartbeat loop. The two `to_thread` writes are NOT
    # interruptible (a `CancelledError` cannot interrupt a running worker thread),
    # so cancellation is only ever delivered at one of the `await` points. We keep
    # the loop bounded by checking for cancellation explicitly and re-raising so a
    # cancel issued by the supervisor at EOD tears this loop down promptly rather
    # than letting it spin forever (the 2026-06-08 hang). See the cancel handler
    # in `collect_order_book` for the companion fix.
    try:
        while True:
            await asyncio.to_thread(_write_depth_cache_snapshot, live_root, date_str, depth_cache, security_ids, id_to_meta, event_log)
            writer_status = writer_status_getter() if writer_status_getter else None
            await asyncio.to_thread(
                _write_collector_state,
                live_root,
                date_str,
                "running",
                security_ids,
                configured_symbols,
                failed_symbols,
                writer_status,
            )
            await asyncio.sleep(interval_seconds)
    except asyncio.CancelledError:
        # Normal shutdown path — stop emitting heartbeats immediately.
        raise


class DepthCollector:
    """
    Manages three 20-depth websocket connections and writes order book data.

    Instantiate once per day. Call run() as an asyncio task.
    """

    def __init__(
        self,
        symbol: str,
        security_ids: list[str],
        id_to_meta: dict[str, dict],
        depth_cache: DepthCache,
        access_token: str,
        client_id: str,
        live_root: Path,
        date_str: str,
    ) -> None:
        self.symbol = symbol
        self._security_ids = security_ids
        self._id_to_meta = id_to_meta
        self._cache = depth_cache
        self._token = access_token
        self._client_id = client_id
        self._live_root = live_root
        self._date_str = date_str
        self._writer = _WriterThread(date_str, live_root)
        self._stop_event = asyncio.Event()
        self._started_at: str | None = None
        self._backpressure_instruments: set[str] = set()
        # Set to True on a fatal disconnect (805/807/808/809). Used by the
        # reconnect loop to abort cleanly instead of looping a 5-second
        # reconnect against an unrecoverable Dhan-side error.
        self._fatal_disconnect: bool = False
        self._fatal_reason: str | None = None
        # Signalled when ``expand_universe`` adds instruments; the outer
        # run loop notices and reconfigures connections cleanly without
        # spawning a second collector instance (which previously raced
        # on shared .tmp paths — see 2026-05-22 / 2026-05-25 audits).
        self._universe_change_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    def fatal_disconnect(self) -> str | None:
        """Return the human-readable fatal disconnect reason, if any."""
        return self._fatal_reason

    def expand_universe(self, security_ids: list[str], id_to_meta: dict[str, dict]) -> int:
        """Add new instruments to the subscription set.

        Triggers a clean reconfigure: ``run`` notices the
        ``_universe_change_event``, gracefully closes its current
        websocket connections (sending RequestCode 12 unsubscribes),
        and reopens with the merged universe. The ``_WriterThread`` and
        ``DepthCache`` persist across reconfigure so no parquet temp
        path is contested.

        Returns the count of newly-added instruments (0 if all already
        subscribed). Idempotent.
        """
        added = 0
        for sid in security_ids:
            if sid in self._id_to_meta:
                continue
            self._security_ids.append(sid)
            self._id_to_meta[sid] = id_to_meta.get(sid, {})
            added += 1
        if added:
            _log.info(
                "depth_collector: expand_universe scheduling reconfigure: +%d instruments (total=%d)",
                added,
                len(self._security_ids),
            )
            self._universe_change_event.set()
        return added

    def writer_status(self) -> dict:
        return self._writer.status()

    async def run(self) -> None:
        self._started_at = _now_ist().isoformat()
        url = _DEPTH_WS_URL.format(token=self._token, client_id=self._client_id)
        try:
            # Outer reconfigure loop. Each iteration starts a fresh set
            # of websocket connections for the current ``_security_ids``
            # universe. On ``expand_universe`` the
            # ``_universe_change_event`` fires and we cancel + rebuild.
            while not self._stop_event.is_set() and not self._fatal_disconnect:
                self._universe_change_event.clear()
                batches = [
                    self._security_ids[i : i + _MAX_PER_CONN]
                    for i in range(0, len(self._security_ids), _MAX_PER_CONN)
                ]
                tasks = [
                    asyncio.create_task(self._connection_loop(url, batch, conn_idx))
                    for conn_idx, batch in enumerate(batches)
                ]
                stop_task = asyncio.create_task(self._stop_event.wait())
                change_task = asyncio.create_task(self._universe_change_event.wait())
                done, _pending = await asyncio.wait(
                    {stop_task, change_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # Tear down current connection set cleanly. ``cancel()``
                # propagates ``CancelledError`` into ``_connection_loop``
                # which exits before sending RequestCode 12; that's fine
                # because the connection itself is closed in the websockets
                # context-manager exit. The connection-cap concern is met
                # by the next iteration's startup grace.
                for t in tasks:
                    t.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                stop_task.cancel()
                change_task.cancel()
                if self._stop_event.is_set() or self._fatal_disconnect:
                    break
                _log.info(
                    "depth_collector: reconfigure complete, new universe size=%d",
                    len(self._security_ids),
                )
        finally:
            self._stop_event.set()
            self._writer.stop()

    async def _connection_loop(self, url: str, sids: list[str], conn_idx: int) -> None:
        """Reconnect loop for one depth websocket connection.

        Aborts on a fatal disconnect (``_fatal_disconnect`` set by
        ``_handle_packet`` when a 805/807/808/809 packet arrives). This
        mirrors the live-feed handling in ``paper_engine.py`` and
        prevents the 5-second reconnect loop from hammering Dhan after an
        unrecoverable error.
        """
        while not self._stop_event.is_set() and not self._fatal_disconnect:
            disconnect_time: str | None = None
            try:
                async with websockets.connect(
                    url,
                    open_timeout=15,
                    close_timeout=5,
                    ping_interval=_PING_INTERVAL,
                ) as ws:
                    await self._subscribe(ws, sids)
                    disconnect_time = None
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            await self._handle_packet(raw)
                        if self._stop_event.is_set() or self._fatal_disconnect:
                            break
                    # Best-effort clean disconnect (RequestCode 12) on
                    # graceful exit. Failures here are non-fatal.
                    if not self._fatal_disconnect:
                        try:
                            await self._send_unsubscribe(ws, sids)
                        except Exception:
                            pass
            except asyncio.CancelledError:
                break
            except Exception as exc:
                disconnect_time = _now_ist().isoformat()
                print(f"[depth_collector] conn={conn_idx} symbol={self.symbol} error={exc!r} — reconnecting in {_RECONNECT_DELAY}s")
            if self._fatal_disconnect:
                print(
                    f"[depth_collector] conn={conn_idx} symbol={self.symbol} "
                    f"fatal disconnect ({self._fatal_reason!r}) — aborting reconnect"
                )
                break
            if not self._stop_event.is_set():
                await asyncio.sleep(_RECONNECT_DELAY)

    async def _send_unsubscribe(self, ws, sids: list[str]) -> None:
        """Send RequestCode=12 unsubscribe before closing.

        Per SDK ``marketfeed.py:186-194`` and ``fulldepth.py:132-137`` the
        client should explicitly disconnect each subscription so Dhan
        releases the server-side connection slot. This matters across
        process restarts: without it, the old sockets stay in CLOSE_WAIT
        for up to ~60 s and the next process hits 805 ("active websocket
        connections exceeded").
        """
        for i in range(0, len(sids), _MAX_PER_CONN):
            batch = sids[i : i + _MAX_PER_CONN]
            payload = {
                "RequestCode": _UNSUB_CODE,
                "InstrumentCount": len(batch),
                "InstrumentList": [
                    {"ExchangeSegment": "NSE_FNO", "SecurityId": sid}
                    for sid in batch
                ],
            }
            await ws.send(json.dumps(payload))

    async def _subscribe(self, ws, sids: list[str]) -> None:
        # 20-depth limit: 50 instruments per subscription batch
        for i in range(0, len(sids), _MAX_PER_CONN):
            batch = sids[i : i + _MAX_PER_CONN]
            sub = {
                "RequestCode": _SUB_CODE,
                "InstrumentCount": len(batch),
                "InstrumentList": [
                    # NSE_FNO = exchange segment for NSE futures and options
                    {"ExchangeSegment": "NSE_FNO", "SecurityId": sid}
                    for sid in batch
                ],
            }
            await ws.send(json.dumps(sub))

    async def _handle_packet(self, raw: bytes) -> None:
        self._writer.enqueue_raw(raw)
        for packet in _iter_packets(raw):
            code = _disconnect_code(packet)
            if code is not None:
                reason = _DISCONNECT_REASONS.get(code, "unknown")
                if code in _FATAL_DISCONNECT_CODES:
                    self._fatal_disconnect = True
                    self._fatal_reason = f"{code}: {reason}"
                    _log.error(
                        "depth_collector: fatal disconnect code=%d (%s) — aborting reconnect",
                        code,
                        reason,
                    )
                else:
                    _log.warning("depth_collector: disconnect code=%d (%s) — will reconnect", code, reason)
                raise RuntimeError(f"Dhan depth disconnect code={code} ({reason})")
            parsed = _parse_packet(packet)
            if parsed is None:
                continue
            feed_code, security_id_int, levels = parsed
            sid = str(security_id_int)
            ts = _now_ist()
            if feed_code == _BID_CODE:
                self._cache.update_bid_packet(sid, ts, levels)
            elif feed_code == _ASK_CODE:
                self._cache.update_ask_packet(sid, ts, levels)
            # Build and store normalized snapshot when both sides are ready
            snap = self._cache.snapshot(sid)
            if snap is not None and sid in self._id_to_meta:
                meta = self._id_to_meta[sid]
                try:
                    row = _build_depth_row(snap.bid_levels, snap.ask_levels, meta, ts)
                    if not self._writer.enqueue_norm(_instrument_key(meta), row) and sid not in self._backpressure_instruments:
                        self._backpressure_instruments.add(sid)
                        print(f"[depth_collector] writer_backpressure sid={sid} status={self._writer.status()}")
                except Exception:
                    if sid not in self._backpressure_instruments:
                        self._backpressure_instruments.add(sid)
                        print(f"[depth_collector] collector_backpressure sid={sid}")




def _build_subscription_universe(
    symbol: str,
    resolver: LiveDhanContractResolver,
    expiry: date,
    scrip_id: int,
    segment: str = "IDX_I",
    atm_offset_range: int = _DEFAULT_ATM_OFFSET_RANGE,
) -> tuple[list[str], dict[str, dict]]:
    """
    Refresh option chain and return (security_ids, id_to_meta) for ATM ±offset_range strikes.
    """
    resolver.refresh_option_chain(expiry, scrip_id, segment)
    selected = set(resolver.security_ids_around_chain_atm(offset_range=atm_offset_range))
    # Only keep ATM ±atm_offset_range strikes — we need spot for ATM but use centre of chain
    # For pre-market setup, use all strikes from chain within ±(atm_offset_range+1) of whatever is
    # in the chain (actual ATM narrowing happens after spot is live).
    id_to_meta: dict[str, dict] = {}
    selected_sids: list[str] = []
    with resolver._chain_lock:
        for (exp_iso, strike, ot), entry in resolver._chain.items():
            sid = entry.security_id
            if sid not in selected:
                continue
            meta = {
                "symbol": symbol,
                "expiry": exp_iso,
                "strike": strike,
                "option_type": ot.value,
                "security_id": sid,
            }
            id_to_meta[sid] = meta
            selected_sids.append(sid)
    return selected_sids, id_to_meta


def _depth_collection_settings(profile: dict) -> tuple[list[str], int, int]:
    cfg = profile.get("depth_collection", {})
    configured = cfg.get("symbols")
    if configured:
        symbols = [str(symbol).upper() for symbol in configured]
    else:
        symbols = []
        for symbol in _MAJOR_NSE_INDEX_SYMBOLS:
            sym_cfg = profile.get("symbols", {}).get(symbol, {})
            if sym_cfg.get("trade", False) and sym_cfg.get("depth_source") == "dhan_20depth":
                symbols.append(symbol)
    symbols = [symbol for symbol in symbols if symbol in _MAJOR_NSE_INDEX_SYMBOLS]
    offset_range = int(cfg.get("atm_offset_range", _DEFAULT_ATM_OFFSET_RANGE))
    max_depth_connections = int(cfg.get("max_depth_connections", _DEFAULT_MAX_DEPTH_CONNECTIONS))
    return symbols, offset_range, max_depth_connections


async def _discover_symbol_with_retry(
    symbol: str,
    resolver: LiveDhanContractResolver,
    expiry: date,
    scrip_id: int,
    segment: str,
    atm_offset_range: int,
    max_retries: int = 3,
) -> tuple[list[str], dict[str, dict]] | None:
    """Build subscription universe for one symbol with exponential backoff."""
    for attempt in range(max_retries):
        try:
            if attempt > 0:
                await asyncio.sleep(2.0 * (2 ** attempt))
            sids, id_to_meta = _build_subscription_universe(
                symbol, resolver, expiry, scrip_id, segment,
                atm_offset_range=atm_offset_range,
            )
            return sids, id_to_meta
        except Exception as exc:
            if attempt < max_retries - 1:
                print(f"[collect_order_book] {symbol} chain discovery attempt {attempt + 1} failed: {exc} — retrying")
            else:
                print(f"[collect_order_book] {symbol} chain discovery failed after {max_retries} attempts: {exc}")
                return None


async def collect_order_book(
    profile: dict,
    session_date: date,
    depth_cache: DepthCache,
    access_token: str,
    client_id: str,
    live_root: Path | None = None,
    dry_run: bool = False,
    reconcile_symbols: list[str] | None = None,
    on_collector_ready: Optional[Callable[["DepthCollector"], None]] = None,
) -> None:
    """Collect configured major-index 20-depth data in pooled 50-instrument batches.

    Args:
        reconcile_symbols: If provided, subscribe depth for these symbols only
            (used by the engine's 09:17 reconciliation pass).
        on_collector_ready: Optional callback invoked once the
            ``DepthCollector`` instance is constructed. The orchestrator
            captures it so the reconciler can call
            ``collector.expand_universe(...)`` instead of spawning a
            second collector.
    """
    if live_root is None:
        live_root = _live_root()

    date_str = session_date.strftime("%Y%m%d")

    if dry_run:
        print("[collect_order_book] DRY RUN - skipping WD space check and websocket connections")
        gap_start = _now_ist().isoformat()
        await asyncio.sleep(1)
        gap_end = _now_ist().isoformat()
        _write_gap_sentinel(live_root, date_str, "DRY_RUN", gap_start, gap_end, "dry_run")
        print("[collect_order_book] gap sentinel written for DRY_RUN")
        return

    _check_wd_space(live_root)  # default _LIVE_MIN_FREE_GB (40 GB, env-overridable)

    depth_symbols, atm_offset_range, max_depth_connections = _depth_collection_settings(profile)
    if not depth_symbols:
        print("[collect_order_book] No major NSE index symbols configured for depth collection")
        return

    if reconcile_symbols is not None:
        # Reconciliation mode: only process symbols the engine asks for
        depth_symbols = [s for s in reconcile_symbols if s in depth_symbols]
        if not depth_symbols:
            print("[collect_order_book] reconcile_symbols has no overlap with configured depth symbols")
            return
        print(f"[collect_order_book] RECONCILIATION mode for symbols: {depth_symbols}")

    all_sids: list[str] = []
    all_meta: dict[str, dict] = {}
    failed_symbols: list[str] = []

    for symbol in depth_symbols:
        sym_cfg = profile.get("symbols", {}).get(symbol, {})
        if sym_cfg.get("depth_source") != "dhan_20depth":
            continue
        scrip_id = sym_cfg["dhan_scrip_id"]
        segment = sym_cfg.get("dhan_segment", "IDX_I")

        resolver = LiveDhanContractResolver(
            symbol=symbol,
            access_token=access_token,
            client_id=client_id,
            spot_security_id=str(scrip_id),
            vix_security_id=str(profile.get("vix", {}).get("dhan_scrip_id", 21)),
        )
        expiries = resolver.fetch_expiry_list(scrip_id, segment)
        valid_expiries = [e for e in expiries if e >= session_date]
        if not valid_expiries:
            print(f"[collect_order_book] SKIP {symbol} - no valid (non-expired) expiries (got: {expiries[:3]})")
            failed_symbols.append(symbol)
            continue
        expiry = valid_expiries[0]

        gap_start = _now_ist().isoformat()
        result = await _discover_symbol_with_retry(
            symbol, resolver, expiry, scrip_id, segment,
            atm_offset_range=atm_offset_range,
        )
        if result is None:
            gap_end = _now_ist().isoformat()
            _write_gap_sentinel(live_root, date_str, symbol, gap_start, gap_end, "chain_discovery_failed")
            failed_symbols.append(symbol)
            continue

        sids, id_to_meta = result
        print(f"[collect_order_book] {symbol}: {len(sids)} instruments for expiry {expiry} ATM +/- {atm_offset_range}")
        for sid in sids:
            if sid in all_meta:
                continue
            all_sids.append(sid)
            all_meta[sid] = id_to_meta[sid]

    configured_symbols = list(depth_symbols)

    if not all_sids:
        print("[collect_order_book] WARNING: no depth instruments discovered — collector has nothing to subscribe")
        if _write_restart_gap_if_needed(live_root, date_str):
            print("[collect_order_book] restart gap sentinel written")
        _write_depth_cache_snapshot(live_root, date_str, depth_cache, all_sids, all_meta)
        _write_collector_state(
            live_root,
            date_str,
            "degraded_no_instruments",
            all_sids,
            configured_symbols,
            failed_symbols,
        )
        # Return instead of running an empty snapshot loop. Reconciliation owns
        # the later recovery collector and must not race another writer.
        return
    else:
        depth_connections = (len(all_sids) + _MAX_PER_CONN - 1) // _MAX_PER_CONN
        if depth_connections > max_depth_connections:
            raise RuntimeError(
                f"Depth universe needs {depth_connections} Dhan 20-depth connections for "
                f"{len(all_sids)} instruments, above configured max_depth_connections={max_depth_connections}. "
                "Reduce depth_collection.symbols or depth_collection.atm_offset_range."
            )

    print(
        f"[collect_order_book] major-index universe: {len(all_sids)} instruments, "
        f"{depth_connections} depth connections, failed={failed_symbols}"
    )
    if _write_restart_gap_if_needed(live_root, date_str):
        print("[collect_order_book] restart gap sentinel written")
    _write_collector_state(live_root, date_str, "running", all_sids, configured_symbols, failed_symbols)

    collector = DepthCollector(
        symbol="NSE_MAJOR_INDICES",
        security_ids=all_sids,
        id_to_meta=all_meta,
        depth_cache=depth_cache,
        access_token=access_token,
        client_id=client_id,
        live_root=live_root,
        date_str=date_str,
    )
    if on_collector_ready is not None:
        try:
            on_collector_ready(collector)
        except Exception:
            _log.exception("collect_order_book: on_collector_ready callback failed")
    # Collector-owned event-log handle for depth-liveness heartbeats. Opened
    # read/write on the same session DB the engine writes (WAL → concurrent
    # writers are safe). Never fatal: a failure here just means depth liveness
    # falls back to the tmpfs JSON snapshot. Closed in the finally below.
    depth_event_log: "EventLog | None" = None
    try:
        depth_event_log = EventLog.open(
            live_root / "event_log" / f"{date_str}.sqlite",
            session_date=session_date.isoformat(),
        )
    except Exception:
        _log.debug("collect_order_book: could not open event log for depth heartbeats", exc_info=True)
        depth_event_log = None

    _write_depth_cache_snapshot(live_root, date_str, depth_cache, all_sids, all_meta, depth_event_log)
    tasks = [
        asyncio.create_task(collector.run()),
        asyncio.create_task(_depth_snapshot_loop(
            live_root,
            date_str,
            depth_cache,
            all_sids,
            all_meta,
            writer_status_getter=collector.writer_status,
            configured_symbols=configured_symbols,
            failed_symbols=failed_symbols,
            event_log=depth_event_log,
        )),
    ]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except asyncio.CancelledError:
        # On cancellation we MUST cancel the child tasks before gathering — the
        # `_depth_snapshot_loop` is a bare `while True:` with no other stop signal
        # (`collector.stop()` only stops the DepthCollector = tasks[0]). Awaiting
        # `gather(*tasks)` without cancelling tasks[1:] first waits for that loop
        # to finish *naturally*, which it never does — it just keeps emitting
        # depth_heartbeat events forever. That is exactly the 2026-06-08 hang:
        # the supervisor cancelled this coroutine at EOD, this handler blocked on
        # the never-ending snapshot loop, and the supervisor's own
        # `gather(*pending)` (api/main.py) never returned → the account-ledger
        # update + next-day re-arm were starved. Cancel everything, then gather
        # with a bound so a stuck `to_thread` can't pin us either.
        # Tasks already cancelled here — they are also cancelled in `finally`,
        # so just propagate; the bounded gather lives in `finally` (single budget)
        # to avoid double-spending the teardown window and overrunning the
        # supervisor's own ENGINE_TEARDOWN_TIMEOUT (reviewer 2026-06-09).
        collector.stop()
        for task in tasks:
            if not task.done():
                task.cancel()
        raise
    finally:
        # Single teardown budget. The whole collector shutdown (collector-task
        # drain + heartbeat-loop cancel + stopped-state write) must finish well
        # inside the supervisor's ENGINE_TEARDOWN_TIMEOUT (default 45s) so the
        # supervisor's bounded gather(*pending) never has to time out and orphan
        # us. We share one wall-clock deadline across the steps below.
        teardown_deadline = _time.monotonic() + _COLLECTOR_TEARDOWN_TIMEOUT

        def _remaining() -> float:
            return max(0.5, teardown_deadline - _time.monotonic())

        collector.stop()
        collector_task = tasks[0]
        if not collector_task.done():
            try:
                await asyncio.wait_for(collector_task, timeout=min(_WRITER_STOP_TIMEOUT + 5.0, _remaining()))
            except asyncio.TimeoutError:
                _log.error("collect_order_book: collector stop timed out; cancelling task")
                collector_task.cancel()
        for task in tasks[1:]:
            if not task.done():
                task.cancel()
        try:
            await asyncio.wait_for(
                asyncio.gather(*tasks, return_exceptions=True),
                timeout=_remaining(),
            )
        except asyncio.TimeoutError:
            _log.error("collect_order_book: finally gather timed out during teardown")
        try:
            await asyncio.wait_for(
                asyncio.to_thread(
                    _write_collector_state,
                    live_root,
                    date_str,
                    "stopped",
                    all_sids,
                    configured_symbols,
                    failed_symbols,
                    collector.writer_status(),
                ),
                timeout=min(5.0, _remaining()),
            )
        except asyncio.TimeoutError:
            _log.error("collect_order_book: stopped-state write timed out")
        if depth_event_log is not None:
            try:
                depth_event_log.close()
            except Exception:
                pass


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Test startup without opening websockets")
    args = parser.parse_args()

    if args.dry_run:
        # Resolve live_root on Windows laptop (skip the WD path check)
        repo_root = Path(__file__).parents[2]
        live_root = repo_root / "data" / "live"
        live_root.mkdir(parents=True, exist_ok=True)
        import json as _json
        cfg_path = repo_root / "configs" / "live" / "wing6_4x1_all_vix_filtered.json"
        profile = _json.loads(cfg_path.read_text())
        token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        cid = os.environ.get("DHAN_CLIENT_ID", "")
        asyncio.run(
            collect_order_book(
                profile=profile,
                session_date=date.today(),
                depth_cache=DepthCache(),
                access_token=token,
                client_id=cid,
                live_root=live_root,
                dry_run=True,
            )
        )
    else:
        print("Run via run_paper_trading.py or use --dry-run for a standalone test.")

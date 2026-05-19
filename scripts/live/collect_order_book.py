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
import os
import queue
import struct
import threading
import time as _time
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import websockets

# Adjust import path when running as module vs standalone
try:
    from options_backtest.depth_cache import DepthCache, DepthLevel
    from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
    from options_backtest.live_resolver import LiveDhanContractResolver
    from options_backtest.schemas import OptionType
    from options_backtest.calendar import get_instrument_spec, expiry_on_or_after
except ImportError:
    import sys
    sys.path.insert(0, str(Path(__file__).parents[2]))
    from options_backtest.depth_cache import DepthCache, DepthLevel
    from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
    from options_backtest.live_resolver import LiveDhanContractResolver
    from options_backtest.schemas import OptionType
    from options_backtest.calendar import get_instrument_spec, expiry_on_or_after

_IST = "Asia/Kolkata"
_DEPTH_WS_URL = (
    "wss://depth-api-feed.dhan.co/twentydepth"
    "?token={token}&clientId={client_id}&authType=2"
)
_SUB_CODE = 23
_BID_CODE = 41
_ASK_CODE = 51
_DISCONNECT_CODE = 50
_HEADER_FMT = "<hBBiI"   # msg_len(int16), feed_code(uint8), exch_seg(uint8), security_id(int32), reserved(uint32)
_LEVEL_FMT = "<dII"      # price(float64), qty(uint32), orders(uint32)
_HEADER_SIZE = struct.calcsize(_HEADER_FMT)   # 12
_LEVEL_SIZE = struct.calcsize(_LEVEL_FMT)     # 16
_LEVELS = 20
_PACKET_SIZE = _HEADER_SIZE + _LEVELS * _LEVEL_SIZE   # 332
_MAX_PER_CONN = 50
_DEFAULT_ATM_OFFSET_RANGE = 20
_DEFAULT_MAX_DEPTH_CONNECTIONS = 5
_FLUSH_INTERVAL = 60        # seconds
_RECONNECT_DELAY = 5        # seconds between reconnect attempts
_PING_INTERVAL = 10         # seconds between server pings

# NSE options universe for 20-depth; concrete width comes from live config.
_MAJOR_NSE_INDEX_SYMBOLS = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
_NSE_SYMBOLS = _MAJOR_NSE_INDEX_SYMBOLS  # Backward-compatible alias for the shadowed legacy coroutine.


def _now_ist() -> pd.Timestamp:
    return pd.Timestamp.now(tz=_IST)


def _live_root() -> Path:
    """Resolve data/live symlink and validate it points to WD storage."""
    repo_root = Path(__file__).parents[2]
    live_path = (repo_root / "data" / "live").resolve()
    expected = Path("/media/WD-Storage/indian-markets-live")
    if live_path != expected:
        raise RuntimeError(
            f"data/live resolves to {live_path}, expected {expected}. "
            "Collector aborts to protect root filesystem."
        )
    return live_path


def _check_wd_space(live_root: Path, min_gb: float = 100.0) -> None:
    import shutil
    stat = shutil.disk_usage(str(live_root))
    free_gb = stat.free / (1024 ** 3)
    if free_gb < min_gb:
        raise RuntimeError(
            f"WD storage has only {free_gb:.1f} GB free; need >= {min_gb} GB for month-long run."
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
    if len(raw) >= 14:
        _msg_len, feed_code, _exch_seg, _security_id, _reserved = struct.unpack_from(_HEADER_FMT, raw, 0)
        if feed_code == _DISCONNECT_CODE:
            return struct.unpack_from("<h", raw, 12)[0]
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

    def __init__(self, date_str: str, live_root: Path) -> None:
        self._date_str = date_str
        self._raw_dir = live_root / "raw_depth_packets" / date_str
        self._norm_dir = live_root / "order_book" / date_str
        self._dir_1min = live_root / "order_book_1min" / date_str
        for d in (self._raw_dir, self._norm_dir, self._dir_1min):
            d.mkdir(parents=True, exist_ok=True)
        self._q: queue.Queue = queue.Queue()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        self._stop_requested = False
        self._flush_seq = 0

    def enqueue_raw(self, raw: bytes) -> None:
        if not self._stop_requested:
            self._q.put(("raw", raw))

    def enqueue_norm(self, ikey: str, row: dict) -> None:
        if not self._stop_requested:
            self._q.put(("norm", ikey, row))

    def _loop(self) -> None:
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
        for ikey, rows in rows_by_ikey.items():
            if not rows:
                continue
            df = pd.DataFrame(rows)
            # Tick-level depth parquet — append-only, no reads during market hours
            out_path = self._norm_dir / f"{ikey}_{minute_bucket}_{seq:04d}.parquet"
            tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
            pq.write_table(pa.Table.from_pandas(df), str(tmp_path), compression="snappy")
            tmp_path.replace(out_path)
            # 1-min OHLCV derived from these ticks — also append-only
            self._write_1min(df, ikey, minute_bucket, seq)

    def _write_1min(self, df: pd.DataFrame, ikey: str, minute_bucket: str, seq: int) -> None:
        df2 = df.copy()
        df2["timestamp"] = pd.to_datetime(df2["timestamp"])
        df2 = df2.set_index("timestamp").sort_index()
        if "mid" not in df2.columns:
            return
        agg = df2["mid"].resample("1min").ohlc()
        agg["volume"] = df2["total_bid_qty"].resample("1min").sum()
        agg["spread_pct_mean"] = df2["spread_pct"].resample("1min").mean()
        out_path = self._dir_1min / f"{ikey}_{minute_bucket}_{seq:04d}.parquet"
        tmp_path = out_path.with_suffix(out_path.suffix + ".tmp")
        pq.write_table(pa.Table.from_pandas(agg.reset_index()), str(tmp_path), compression="snappy")
        tmp_path.replace(out_path)

    def stop(self) -> None:
        self._q.put(("stop",))
        self._thread.join(timeout=30)


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
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, default=str))
        f.flush()
    tmp.replace(path)


def _write_depth_cache_snapshot(
    live_root: Path,
    date_str: str,
    depth_cache: DepthCache,
    security_ids: list[str],
    id_to_meta: dict[str, dict] | None = None,
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


def _write_collector_state(
    live_root: Path,
    date_str: str,
    status: str,
    security_ids: list[str],
    configured_symbols: list[str] | None = None,
    failed_symbols: list[str] | None = None,
) -> None:
    payload = {
        "written_at": _now_ist().isoformat(),
        "session_date": date_str,
        "pid": os.getpid(),
        "status": status,
        "configured_security_ids": len(security_ids),
        "configured_symbols": configured_symbols or [],
        "failed_symbols": failed_symbols or [],
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
    interval_seconds: float = 10.0,
) -> None:
    while True:
        await asyncio.to_thread(_write_depth_cache_snapshot, live_root, date_str, depth_cache, security_ids, id_to_meta)
        await asyncio.to_thread(_write_collector_state, live_root, date_str, "running", security_ids)
        await asyncio.sleep(interval_seconds)


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

    def stop(self) -> None:
        self._stop_event.set()

    async def run(self) -> None:
        self._started_at = _now_ist().isoformat()
        url = _DEPTH_WS_URL.format(token=self._token, client_id=self._client_id)
        # Split security_ids into batches of ≤50
        batches = [
            self._security_ids[i : i + _MAX_PER_CONN]
            for i in range(0, len(self._security_ids), _MAX_PER_CONN)
        ]
        tasks = [
            asyncio.create_task(self._connection_loop(url, batch, conn_idx))
            for conn_idx, batch in enumerate(batches)
        ]
        await self._stop_event.wait()
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        self._writer.stop()

    async def _connection_loop(self, url: str, sids: list[str], conn_idx: int) -> None:
        """Reconnect loop for one depth websocket connection."""
        while not self._stop_event.is_set():
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
                        if self._stop_event.is_set():
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                disconnect_time = _now_ist().isoformat()
                print(f"[depth_collector] conn={conn_idx} symbol={self.symbol} error={exc!r} — reconnecting in {_RECONNECT_DELAY}s")
            if not self._stop_event.is_set():
                await asyncio.sleep(_RECONNECT_DELAY)

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
                raise RuntimeError(f"Dhan depth disconnect code={code}")
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
                    self._writer.enqueue_norm(_instrument_key(meta), row)
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
) -> None:
    """Collect configured major-index 20-depth data in pooled 50-instrument batches.

    Args:
        reconcile_symbols: If provided, subscribe depth for these symbols only
            (used by the engine's 09:17 reconciliation pass).
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

    _check_wd_space(live_root, min_gb=100.0)

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
        _write_collector_state(live_root, date_str, "running", all_sids, configured_symbols, failed_symbols)
        # Keep running so the snapshot loop writes empty depth state; engine may still trade SENSEX
        depth_connections = 0
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
    if not all_sids:
        # Nothing to collect — just run the snapshot loop so depth state stays fresh
        await _depth_snapshot_loop(live_root, date_str, depth_cache, all_sids, all_meta)
        return

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
    _write_depth_cache_snapshot(live_root, date_str, depth_cache, all_sids, all_meta)
    tasks = [
        asyncio.create_task(collector.run()),
        asyncio.create_task(_depth_snapshot_loop(live_root, date_str, depth_cache, all_sids, all_meta)),
    ]
    try:
        done, _pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
    except asyncio.CancelledError:
        collector.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
    finally:
        collector.stop()
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await asyncio.to_thread(_write_collector_state, live_root, date_str, "stopped", all_sids, configured_symbols, failed_symbols)


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

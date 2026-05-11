"""
PaperTradingEngine — daily paper trading loop for Wing-6 Iron Condor.

Connects to Dhan live feed websocket, evaluates per-symbol VIX/DTE filters,
records paper fills using DepthCache executable prices (NSE) or top-of-book
(SENSEX), checkpoints positions, and generates EOD JSON + markdown reports.

Do NOT import this from scripts.live modules — one-way dependency only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import struct
import time as _time
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from .broker_sim import ChargesConfig, FillModel
from .calendar import expiry_on_or_after, lot_size
from .depth_cache import DepthCache
from .live_resolver import LiveDhanContractResolver, StaleQuoteError
from .schemas import Contract, OptionType, Side
from .volatility_filter import VIX_BUCKETS

_IST = ZoneInfo("Asia/Kolkata")
_log = logging.getLogger(__name__)

# Dhan live feed endpoint
_FEED_URL = "wss://api-feed.dhan.co?version=2&token={token}&clientId={client_id}&authType=2"
_REQUEST_CODE_FULL = 21

# Binary packet layout (little-endian)
_TYPE_TICKER = 2
_TYPE_FULL = 8
_TYPE_DISCONNECT = 50

# Fatal codes: do not reconnect
_FATAL_DISCONNECT_CODES = {805, 806, 807, 808, 809}

# Exchange segment strings for live feed subscriptions
_SEG_IDX = "IDX_I"
_SEG_NSE_FNO = "NSE_FNO"
_SEG_BSE_FNO = "BSE_FNO"

# Spot index security IDs (IDX_I segment) → symbol name
_SPOT_SID_TO_SYMBOL: dict[str, str] = {
    "13": "NIFTY", "27": "FINNIFTY", "442": "MIDCPNIFTY", "51": "SENSEX",
}

# Freshness limits (seconds)
_VIX_MAX_AGE = 300
_SPOT_MAX_AGE = 5
_OPTION_MAX_AGE = 5
_DEPTH_MAX_AGE = 5

# Timing constants (IST)
_T_CONNECT = time(9, 0)
_T_CHECK_FEED = time(9, 10)
_T_CHAIN_FETCH = time(9, 15)
_T_RESOLVE_CHECK = time(9, 17)
_T_ENTRY = time(9, 20)
_T_EXIT = time(15, 20)
_T_STALE_DEADLINE = time(15, 25)
_T_EOD = time(15, 31)

# Snapshot interval
_SNAPSHOT_INTERVAL = 10.0


def _now_ist() -> datetime:
    return datetime.now(tz=_IST)


async def _sleep_until(target_time: time, session_date: date) -> None:
    target = datetime.combine(session_date, target_time, tzinfo=_IST)
    now = _now_ist()
    if now < target:
        await asyncio.sleep((target - now).total_seconds())
    else:
        await asyncio.sleep(0)  # yield to scheduler so queued tasks can run


def _ts_str() -> str:
    return _now_ist().isoformat()


def _write_atomic(path: Path, data: dict) -> None:
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=2, default=str))
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _vix_bucket(vix_val: float) -> str:
    for lo, hi, label in VIX_BUCKETS:
        if lo <= vix_val < hi:
            return label
    return "unknown"


def _parse_full_packet(raw: bytes) -> dict | None:
    """Parse a Type-8 (Full, 162B) packet. Returns dict with ltp, security_id, depth."""
    if len(raw) < 162:
        return None
    ptype = raw[0]
    if ptype != _TYPE_FULL:
        return None
    security_id = struct.unpack_from("<I", raw, 4)[0]
    ltp = struct.unpack_from("<f", raw, 8)[0]
    ltt = struct.unpack_from("<I", raw, 14)[0]
    volume = struct.unpack_from("<I", raw, 22)[0]
    oi = struct.unpack_from("<I", raw, 34)[0]
    open_ = struct.unpack_from("<f", raw, 46)[0]
    close_ = struct.unpack_from("<f", raw, 50)[0]
    high = struct.unpack_from("<f", raw, 54)[0]
    low = struct.unpack_from("<f", raw, 58)[0]

    # 5-level depth at offset 62: 5 × 20B each <IIHHff>
    bids = []
    asks = []
    for i in range(5):
        base = 62 + i * 20
        bid_qty, ask_qty, _bid_ord, _ask_ord = struct.unpack_from("<IIHH", raw, base)
        bid_price, ask_price = struct.unpack_from("<ff", raw, base + 12)
        if bid_price > 0:
            bids.append({"price": round(float(bid_price), 2), "qty": int(bid_qty)})
        if ask_price > 0:
            asks.append({"price": round(float(ask_price), 2), "qty": int(ask_qty)})

    return {
        "security_id": str(security_id),
        "ltp": float(ltp),
        "ltt": ltt,
        "volume": int(volume),
        "oi": int(oi),
        "open": float(open_),
        "close": float(close_),
        "high": float(high),
        "low": float(low),
        "bids": bids,
        "asks": asks,
    }


def _parse_ticker_packet(raw: bytes) -> dict | None:
    """Parse a Type-2 (Ticker, 16B) packet."""
    if len(raw) < 16 or raw[0] != _TYPE_TICKER:
        return None
    security_id = struct.unpack_from("<I", raw, 4)[0]
    ltp = struct.unpack_from("<f", raw, 8)[0]
    ltt = struct.unpack_from("<I", raw, 12)[0]
    return {"security_id": str(security_id), "ltp": float(ltp), "ltt": ltt}


def _parse_disconnect_code(raw: bytes) -> int | None:
    if len(raw) >= 10 and raw[0] == _TYPE_DISCONNECT:
        return struct.unpack_from("<H", raw, 8)[0]
    return None


class _OpenPosition:
    """Tracks one live condor (4 legs) entered for a symbol."""

    def __init__(
        self,
        symbol: str,
        expiry: date,
        lots: int,
        lot_sz: int,
        entry_time: datetime,
        legs: list[dict],  # [{leg_role, contract, side, security_id, entry_fill}]
        entry_credit: float,
        entry_charges: float,
    ) -> None:
        self.symbol = symbol
        self.expiry = expiry
        self.lots = lots
        self.lot_size = lot_sz
        self.entry_time = entry_time
        self.legs = legs
        self.entry_credit = entry_credit
        self.entry_charges = entry_charges
        self.exit_fills: list[dict] = []
        self.exit_time: datetime | None = None
        self.exit_reason: str | None = None
        self.forced_stale_exit: bool = False

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "expiry": self.expiry.isoformat(),
            "lots": self.lots,
            "lot_size": self.lot_size,
            "entry_time": self.entry_time.isoformat(),
            "legs": self.legs,
            "entry_credit": self.entry_credit,
            "entry_charges": self.entry_charges,
            "exit_fills": self.exit_fills,
            "exit_time": self.exit_time.isoformat() if self.exit_time else None,
            "exit_reason": self.exit_reason,
            "forced_stale_exit": self.forced_stale_exit,
        }


class PaperTradingEngine:
    """
    Drives the daily paper trading loop for Wing-6 Iron Condor.

    One instance per trading day. Call run() as an asyncio coroutine.
    """

    def __init__(
        self,
        profile: dict,
        session_date: date,
        depth_cache: DepthCache,
        access_token: str,
        client_id: str,
        live_root: Path,
    ) -> None:
        self._profile = profile
        self._session_date = session_date
        self._depth_cache = depth_cache
        self._access_token = access_token
        self._client_id = client_id
        self._live_root = live_root
        self._date_str = session_date.strftime("%Y%m%d")

        # Per-symbol resolvers keyed by symbol name
        self._resolvers: dict[str, LiveDhanContractResolver] = {}

        # One VIX resolver (shared, uses VIX security_id=21)
        vix_cfg = profile.get("vix", {})
        self._vix_security_id = str(vix_cfg.get("dhan_scrip_id", 21))

        # Live feed state
        self._ws = None
        self._feed_connected = False
        self._subscribed_ids: list[dict] = []  # [{ExchangeSegment, SecurityId}]
        self._fatal_disconnect = False
        self._disconnect_event = asyncio.Event()

        # Greeks/IV cache from option chain REST (security_id -> dict)
        self._chain_greeks: dict[str, dict] = {}
        self._chain_status: dict[str, dict] = {}

        # Last parsed type-8 top-of-book per security_id (for SENSEX fallback)
        self._last_tob: dict[str, dict] = {}

        # Live spot bar builder: closed bars + currently-open bar per symbol
        self._spot_bars: dict[str, list[dict]] = {sym: [] for sym in _SPOT_SID_TO_SYMBOL.values()}
        self._spot_open_bar: dict[str, dict | None] = {sym: None for sym in _SPOT_SID_TO_SYMBOL.values()}

        # Open positions list (grows during entry, shrinks during exit)
        self._open_positions: list[_OpenPosition] = []
        self._completed_trades: list[dict] = []

        # Signal log path
        self._signals_path = live_root / "paper_trades" / f"{self._date_str}_signals.jsonl"
        self._trades_path = live_root / "paper_trades" / f"{self._date_str}.json"
        self._report_path = live_root / "reports" / f"{self._date_str}_paper_summary.md"
        self._snapshot_dir = live_root / "snapshots"
        self._log_dir = live_root / "logs"

        self._phase = "init"
        self._stop_event = asyncio.Event()
        self._resumed_after_crash = False
        self._crash_gap_start: str | None = None
        self._crash_gap_end: str | None = None
        self._crash_gap_minutes: float | None = None

        # Initialise per-symbol resolvers
        self._build_resolvers()

    def _build_resolvers(self) -> None:
        for symbol, sym_cfg in self._profile.get("symbols", {}).items():
            if not sym_cfg.get("trade", False):
                continue
            scrip_id = sym_cfg.get("dhan_scrip_id")
            if scrip_id is None:
                continue
            resolver = LiveDhanContractResolver(
                symbol=symbol,
                access_token=self._access_token,
                client_id=self._client_id,
                spot_security_id=str(scrip_id),
                vix_security_id=self._vix_security_id,
            )
            self._resolvers[symbol] = resolver

    def resume_from_checkpoint(self, checkpoint: dict) -> None:
        """Load open positions from a checkpoint dict (crash recovery)."""
        for pos_dict in checkpoint.get("open_positions", []):
            pos = _OpenPosition(
                symbol=pos_dict["symbol"],
                expiry=date.fromisoformat(pos_dict["expiry"]),
                lots=pos_dict["lots"],
                lot_sz=pos_dict["lot_size"],
                entry_time=datetime.fromisoformat(pos_dict["entry_time"]),
                legs=pos_dict["legs"],
                entry_credit=pos_dict["entry_credit"],
                entry_charges=pos_dict["entry_charges"],
            )
            pos.exit_fills = pos_dict.get("exit_fills", [])
            pos.exit_time = (
                datetime.fromisoformat(pos_dict["exit_time"])
                if pos_dict.get("exit_time")
                else None
            )
            pos.exit_reason = pos_dict.get("exit_reason")
            pos.forced_stale_exit = pos_dict.get("forced_stale_exit", False)
            self._open_positions.append(pos)
        self._resumed_after_crash = True
        self._crash_gap_start = checkpoint.get("written_at")
        self._crash_gap_end = _ts_str()
        self._crash_gap_minutes = self._compute_gap_minutes(self._crash_gap_start, self._crash_gap_end)
        _log.info(
            "RESUME MODE: loaded %d open positions from checkpoint gap_start=%s gap_end=%s gap_minutes=%s",
            len(self._open_positions),
            self._crash_gap_start,
            self._crash_gap_end,
            self._crash_gap_minutes,
        )

    # ──────────────────────────────────────────────────────────────────────────
    # Main run loop
    # ──────────────────────────────────────────────────────────────────────────

    async def run(self) -> None:
        self._ensure_dirs()
        self._setup_file_logging()

        _log.info("paper_engine: starting session_date=%s", self._session_date)

        self._phase = "waiting_preopen"
        snapshot_task = asyncio.create_task(self._snapshot_loop())

        await _sleep_until(_T_CONNECT, self._session_date)
        self._phase = "connecting"
        feed_task = asyncio.create_task(self._feed_loop())

        # 09:10 — check feed connection; mid-session restarts need extra time
        # because the websocket task starts from cold after systemd restarts.
        await _sleep_until(_T_CHECK_FEED, self._session_date)
        if not self._feed_connected:
            now = _now_ist()
            entry_cutoff = datetime.combine(self._session_date, _T_ENTRY, tzinfo=_IST)
            grace = 120 if now > entry_cutoff else 45
            _log.info("paper_engine: feed not connected at 09:10 check — waiting up to %ds", grace)
            for _ in range(grace):
                await asyncio.sleep(1)
                if self._feed_connected:
                    _log.info("paper_engine: feed connected during grace period")
                    break
        if not self._feed_connected:
            self._log_signal("skip", "ALL", "feed_not_connected")
            _log.error("paper_engine: feed not connected after grace period — aborting day")
            self._stop_event.set()
            await asyncio.gather(feed_task, snapshot_task, return_exceptions=True)
            return

        # 09:15 — fetch option chains and subscribe
        await _sleep_until(_T_CHAIN_FETCH, self._session_date)
        self._phase = "chain_fetch"
        await self._fetch_chains_and_subscribe()

        # 09:17 — resolve check (log any leg that can't be resolved)
        await _sleep_until(_T_RESOLVE_CHECK, self._session_date)
        await self._resolve_check()

        # 09:20 — entry
        await _sleep_until(_T_ENTRY, self._session_date)
        self._phase = "entry"

        # Skip entry if we resumed from checkpoint, or if restarting mid-session
        # (> 5 min past entry window — fills at this time are not representative)
        _entry_cutoff = datetime.combine(self._session_date, _T_ENTRY, tzinfo=_IST)
        _late_restart = _now_ist() > _entry_cutoff + timedelta(minutes=5)
        if self._open_positions:
            _log.info("paper_engine: resumed from checkpoint — skipping entry phase")
        elif _late_restart:
            _log.info("paper_engine: mid-session restart past entry window — skipping entry, fetching chains for option chain snapshot")
        else:
            await self._enter_all_symbols()

        # 09:21–15:19 — monitor (mark-to-market snapshots only)
        self._phase = "monitoring"
        await _sleep_until(_T_EXIT, self._session_date)

        # 15:20 — time exit
        self._phase = "exit"
        await self._exit_all_positions(reason="time_exit", forced_stale=False)

        # 15:25 — stale exit deadline
        await _sleep_until(_T_STALE_DEADLINE, self._session_date)
        if self._open_positions:
            _log.warning("paper_engine: %d positions still open at 15:25 — forced stale exit", len(self._open_positions))
            await self._exit_all_positions(reason="forced_stale_exit", forced_stale=True)

        # 15:31 — flush + EOD report
        await _sleep_until(_T_EOD, self._session_date)
        self._phase = "eod"
        self._generate_eod_report()
        # Write final EOD snapshots — these persist as the definitive closing chain
        # until the engine restarts next morning.
        await asyncio.to_thread(self._write_feed_state)
        await asyncio.to_thread(self._write_spot_bars)
        await asyncio.to_thread(self._write_eod_snapshot)

        self._stop_event.set()
        await asyncio.gather(feed_task, snapshot_task, return_exceptions=True)
        _log.info("paper_engine: session complete")

    # ──────────────────────────────────────────────────────────────────────────
    # Live feed websocket
    # ──────────────────────────────────────────────────────────────────────────

    async def _feed_loop(self) -> None:
        """Persistent reconnecting websocket loop for the Dhan live feed."""
        import websockets

        url = _FEED_URL.format(token=self._access_token, client_id=self._client_id)
        while not self._stop_event.is_set():
            try:
                async with websockets.connect(url, open_timeout=15, close_timeout=5, ping_interval=20) as ws:
                    self._ws = ws
                    self._feed_connected = True
                    _log.info("live_feed: connected")
                    await self._subscribe_instruments(ws, self._core_subscriptions())
                    if self._subscribed_ids:
                        core_keys = {
                            (inst["ExchangeSegment"], str(inst["SecurityId"]))
                            for inst in self._core_subscriptions()
                        }
                        queued = [
                            inst for inst in self._subscribed_ids
                            if (inst["ExchangeSegment"], str(inst["SecurityId"])) not in core_keys
                        ]
                        if queued:
                            await self._subscribe_instruments(ws, queued)
                    async for raw in ws:
                        if isinstance(raw, bytes):
                            self._handle_feed_packet(raw)
                        if self._stop_event.is_set():
                            break
            except asyncio.CancelledError:
                break
            except Exception as exc:
                self._feed_connected = False
                _log.warning("live_feed: disconnected — %r — reconnecting in 5s", exc)
                if self._fatal_disconnect:
                    _log.error("live_feed: fatal disconnect code — aborting reconnect")
                    break
            self._ws = None
            if not self._stop_event.is_set() and not self._fatal_disconnect:
                await asyncio.sleep(5)

        self._feed_connected = False

    def _handle_feed_packet(self, raw: bytes) -> None:
        if not raw:
            return
        ptype = raw[0]

        if ptype == _TYPE_DISCONNECT:
            code = _parse_disconnect_code(raw)
            if code is not None and code in _FATAL_DISCONNECT_CODES:
                _log.error("live_feed: fatal disconnect code=%d — will not reconnect", code)
                self._fatal_disconnect = True
                self._feed_connected = False
            else:
                _log.warning("live_feed: disconnect code=%s — retryable", code)
                self._feed_connected = False
            return

        if ptype == _TYPE_FULL:
            parsed = _parse_full_packet(raw)
            if parsed is None:
                return
            sid = parsed["security_id"]
            received_at = pd.Timestamp.now(tz="Asia/Kolkata")
            # Update resolver quote cache for all resolvers that track this security_id
            for resolver in self._resolvers.values():
                resolver.update_quote(
                    sid,
                    ltp=parsed["ltp"],
                    open_=parsed["open"],
                    high=parsed["high"],
                    low=parsed["low"],
                    volume=parsed["volume"],
                    oi=parsed["oi"],
                    received_at=received_at,
                )
            # Store top-of-book for SENSEX fallback
            self._last_tob[sid] = {
                "best_bid": parsed["bids"][0]["price"] if parsed["bids"] else 0.0,
                "best_ask": parsed["asks"][0]["price"] if parsed["asks"] else 0.0,
                "ts": received_at,
            }
            # Capture spot index tick for live bar building
            spot_sym = _SPOT_SID_TO_SYMBOL.get(sid)
            if spot_sym:
                self._update_spot_bar(spot_sym, parsed["ltp"], received_at)
            return

        if ptype == _TYPE_TICKER:
            parsed = _parse_ticker_packet(raw)
            if parsed is None:
                return
            sid = parsed["security_id"]
            received_at = pd.Timestamp.now(tz="Asia/Kolkata")
            for resolver in self._resolvers.values():
                resolver.update_quote(sid, ltp=parsed["ltp"], received_at=received_at)
            # Capture spot index tick for live bar building
            spot_sym = _SPOT_SID_TO_SYMBOL.get(sid)
            if spot_sym:
                self._update_spot_bar(spot_sym, parsed["ltp"], received_at)

    async def _subscribe_instruments(self, ws, instruments: list[dict]) -> None:
        """Send RequestCode=21 (Full) subscription for all instruments in batches of 100."""
        batch_size = 100
        for i in range(0, len(instruments), batch_size):
            batch = instruments[i : i + batch_size]
            sub = {
                "RequestCode": _REQUEST_CODE_FULL,
                "InstrumentCount": len(batch),
                "InstrumentList": batch,
            }
            await ws.send(json.dumps(sub))

    def _core_subscriptions(self) -> list[dict]:
        """Spot indices + VIX; subscribe immediately on every websocket connect."""
        spot_ids = {
            "NIFTY": "13",
            "FINNIFTY": "27",
            "MIDCPNIFTY": "442",
            "SENSEX": "51",
            "BANKNIFTY": "25",
        }
        instruments = [
            {"ExchangeSegment": _SEG_IDX, "SecurityId": sid}
            for sid in spot_ids.values()
        ]
        instruments.append({"ExchangeSegment": _SEG_IDX, "SecurityId": self._vix_security_id})
        return instruments

    # ──────────────────────────────────────────────────────────────────────────
    # Chain fetch + subscriptions
    # ──────────────────────────────────────────────────────────────────────────

    async def _fetch_chains_and_subscribe(self) -> None:
        instruments_to_sub: list[dict] = []
        depth_cfg = self._profile.get("depth_collection", {})
        chain_offset_range = int(depth_cfg.get("atm_offset_range", 20))

        # Keep core subscriptions in the persisted subscription set so reconnects
        # after chain fetch restore spot/VIX plus options in one pass.
        instruments_to_sub.extend(self._core_subscriptions())

        for symbol, resolver in self._resolvers.items():
            sym_cfg = self._profile["symbols"][symbol]
            scrip_id = sym_cfg["dhan_scrip_id"]
            segment = sym_cfg.get("dhan_segment", "IDX_I")
            expiry_type = sym_cfg.get("expiry_type", "week")

            try:
                expiry = expiry_on_or_after(symbol, self._session_date, expiry_type=expiry_type)
            except Exception as exc:
                _log.warning("chain_fetch: %s — expiry resolution failed: %r", symbol, exc)
                self._chain_status[symbol] = {
                    "status": "failed",
                    "reason": "expiry_resolution_failed",
                    "error": repr(exc),
                    "checked_at": _ts_str(),
                }
                continue

            self._chain_status[symbol] = {
                "status": "pending",
                "expiry": expiry.isoformat(),
                "checked_at": _ts_str(),
            }
            for attempt in range(3):
                try:
                    if attempt > 0:
                        await asyncio.sleep(2.0 * (2 ** attempt))
                    resolver.refresh_option_chain(expiry, scrip_id, segment)
                    sids = resolver.security_ids_around_chain_atm(offset_range=chain_offset_range)
                    if not sids:
                        raise ValueError(f"no ATM +/- {chain_offset_range} security ids from option chain")
                    self._chain_greeks.update(resolver.chain_metadata(sids))
                    self._chain_status[symbol] = {
                        "status": "loaded",
                        "expiry": expiry.isoformat(),
                        "instrument_count": len(sids),
                        "checked_at": _ts_str(),
                    }
                    break
                except Exception as exc:
                    if attempt < 2:
                        _log.warning("chain_fetch: %s — attempt %d failed: %r — retrying", symbol, attempt + 1, exc)
                    else:
                        _log.warning("chain_fetch: %s — REST failed after 3 attempts: %r", symbol, exc)
                        self._log_signal("skip", symbol, "chain_not_loaded")
                        self._chain_status[symbol] = {
                            "status": "failed",
                            "expiry": expiry.isoformat(),
                            "reason": "chain_not_loaded",
                            "error": repr(exc),
                            "checked_at": _ts_str(),
                        }
                        sids = []
            if not sids:
                continue
            # Restart-time bursts can trip Dhan rate limits; slow down after open.
            chain_delay = 2.0 if _now_ist().time() > _T_CHAIN_FETCH else 0.3
            await asyncio.sleep(chain_delay)

            _log.info("chain_fetch: %s expiry=%s instruments=%d", symbol, expiry, len(sids))

            # Determine exchange segment for options subscriptions
            if symbol == "SENSEX":
                exch_seg = _SEG_BSE_FNO
            else:
                exch_seg = _SEG_NSE_FNO

            for sid in sids:
                instruments_to_sub.append({"ExchangeSegment": exch_seg, "SecurityId": sid})

        self._subscribed_ids = instruments_to_sub
        self._write_instrument_map()

        if self._ws is not None and self._feed_connected:
            await self._subscribe_instruments(self._ws, instruments_to_sub)
            _log.info("live_feed: subscribed %d instruments", len(instruments_to_sub))
        else:
            _log.warning("live_feed: not connected at chain_fetch — subscriptions queued")

    # ──────────────────────────────────────────────────────────────────────────
    # Resolve check (09:17)
    # ──────────────────────────────────────────────────────────────────────────

    async def _resolve_check(self) -> None:
        now_ts = pd.Timestamp.now(tz="Asia/Kolkata")
        for symbol, resolver in self._resolvers.items():
            sym_cfg = self._profile["symbols"][symbol]
            expiry_type = sym_cfg.get("expiry_type", "week")
            strat_cfg = self._profile.get("strategy", {})
            offsets = [
                (strat_cfg.get("short_call_offset", 2), OptionType.CALL, "short_call"),
                (strat_cfg.get("long_call_offset", 8), OptionType.CALL, "long_call"),
                (strat_cfg.get("short_put_offset", -2), OptionType.PUT, "short_put"),
                (strat_cfg.get("long_put_offset", -8), OptionType.PUT, "long_put"),
            ]
            for offset, ot, role in offsets:
                try:
                    contract = resolver.resolve_atm_offset(now_ts, offset, ot)
                    sid = resolver.security_id_for(contract)
                    _log.debug("resolve_check: %s %s offset=%d sid=%s ok", symbol, role, offset, sid)
                except (StaleQuoteError, KeyError) as exc:
                    _log.warning("resolve_check: %s %s FAILED — %r", symbol, role, exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Entry
    # ──────────────────────────────────────────────────────────────────────────

    async def _enter_all_symbols(self) -> None:
        now_ts = pd.Timestamp.now(tz="Asia/Kolkata")
        now_dt = _now_ist()

        # Get VIX once for all symbols
        vix_val: float | None = None
        vix_bucket: str | None = None
        try:
            # Use NIFTY resolver as VIX carrier (all share same VIX security_id)
            any_resolver = next(iter(self._resolvers.values()), None)
            if any_resolver is not None:
                vix_val = any_resolver.vix_ltp()
                vix_bucket = _vix_bucket(vix_val)
        except StaleQuoteError:
            _log.warning("entry: VIX quote stale")

        for symbol, resolver in self._resolvers.items():
            sym_cfg = self._profile["symbols"][symbol]

            # VIX filters
            if vix_val is None:
                self._log_signal("skip", symbol, "vix_stale", vix=None)
                _log.info("entry: %s SKIP vix_stale", symbol)
                continue

            vix_filter = sym_cfg.get("vix_filter")
            if vix_filter and "require_gte" in vix_filter:
                if vix_val < vix_filter["require_gte"]:
                    self._log_signal("skip", symbol, "vix_below_threshold", vix=vix_val, bucket=vix_bucket)
                    _log.info("entry: %s SKIP vix_below_threshold vix=%.2f", symbol, vix_val)
                    continue

            skip_bucket = sym_cfg.get("vix_bucket_skip")
            if skip_bucket and vix_bucket == skip_bucket:
                self._log_signal("skip", symbol, "vix_bucket_skip", vix=vix_val, bucket=vix_bucket)
                _log.info("entry: %s SKIP vix_bucket_skip bucket=%s", symbol, vix_bucket)
                continue

            # DTE filters
            expiry_type = sym_cfg.get("expiry_type", "week")
            try:
                expiry = expiry_on_or_after(symbol, self._session_date, expiry_type=expiry_type)
            except Exception as exc:
                self._log_signal("skip", symbol, "chain_not_loaded", vix=vix_val)
                _log.warning("entry: %s SKIP expiry resolution failed: %r", symbol, exc)
                continue

            dte = (expiry - self._session_date).days
            min_dte = sym_cfg.get("min_dte", 1)
            if dte < min_dte:
                self._log_signal("skip", symbol, "dte_below_min", vix=vix_val, dte=dte, bucket=vix_bucket)
                _log.info("entry: %s SKIP dte_below_min dte=%d", symbol, dte)
                continue

            max_dte = sym_cfg.get("max_dte")
            if max_dte is not None and dte > max_dte:
                self._log_signal("skip", symbol, "dte_above_max", vix=vix_val, dte=dte, bucket=vix_bucket)
                _log.info("entry: %s SKIP dte_above_max dte=%d max=%d", symbol, dte, max_dte)
                continue

            # Resolve all 4 legs
            strat_cfg = self._profile.get("strategy", {})
            leg_specs = [
                (strat_cfg.get("short_call_offset", 2), OptionType.CALL, "short_call", Side.SELL),
                (strat_cfg.get("long_call_offset", 8), OptionType.CALL, "long_call", Side.BUY),
                (strat_cfg.get("short_put_offset", -2), OptionType.PUT, "short_put", Side.SELL),
                (strat_cfg.get("long_put_offset", -8), OptionType.PUT, "long_put", Side.BUY),
            ]
            resolved_legs: list[tuple[Contract, str, Side, str]] = []
            leg_fail = False
            for offset, ot, role, side in leg_specs:
                try:
                    contract = resolver.resolve_atm_offset(now_ts, offset, ot)
                    sid = resolver.security_id_for(contract)
                    if sid is None:
                        raise KeyError(f"no security_id for {contract}")
                    resolved_legs.append((contract, role, side, sid))
                except (StaleQuoteError, KeyError) as exc:
                    _log.warning("entry: %s leg %s failed — %r", symbol, role, exc)
                    leg_fail = True
                    break

            if leg_fail:
                self._log_signal("skip", symbol, "leg_resolution_failed", vix=vix_val, dte=dte, bucket=vix_bucket)
                _log.info("entry: %s SKIP leg_resolution_failed — partial entry blocked", symbol)
                continue

            # Spot staleness check
            try:
                resolver.spot_ltp()
            except StaleQuoteError:
                self._log_signal("skip", symbol, "spot_stale", vix=vix_val, dte=dte, bucket=vix_bucket)
                _log.info("entry: %s SKIP spot_stale", symbol)
                continue

            # Get fills for all legs — if ANY leg fails depth, skip entire symbol
            lots = sym_cfg.get("lots", 1)
            lot_sz = lot_size(symbol, self._session_date)
            quantity = lots * lot_sz
            depth_source = sym_cfg.get("depth_source", "dhan_20depth")
            charges_cfg = ChargesConfig.for_date(self._session_date)
            fill_model = FillModel(charges=charges_cfg)

            entry_fills: list[dict] = []
            entry_credit = 0.0
            entry_charges = 0.0
            fill_fail = False

            for contract, role, side, sid in resolved_legs:
                fill_dict = self._get_fill(
                    sid=sid,
                    side=side,
                    quantity=quantity,
                    depth_source=depth_source,
                    symbol=symbol,
                    role=role,
                    contract=contract,
                    fill_model=fill_model,
                    now_ts=now_ts,
                    now_dt=now_dt,
                )
                if fill_dict is None:
                    fill_fail = True
                    break
                entry_fills.append(fill_dict)

                # entry_credit: SELL legs contribute positive, BUY legs negative
                premium = fill_dict["price"] * quantity
                if side == Side.SELL:
                    entry_credit += premium
                else:
                    entry_credit -= premium
                entry_charges += fill_dict["charges"]

            if fill_fail:
                self._log_signal("skip", symbol, "insufficient_depth", vix=vix_val, dte=dte, bucket=vix_bucket)
                _log.info("entry: %s SKIP insufficient_depth", symbol)
                continue

            # Build leg list for position tracking
            legs = [
                {
                    "leg_role": ef["leg_role"],
                    "side": ef["side"],
                    "security_id": ef["security_id"],
                    "strike": contract.strike,
                    "option_type": contract.option_type.value,
                    "expiry": contract.expiry.isoformat(),
                    "ticker": contract.ticker,
                    "entry_fill": ef,
                }
                for ef, (contract, role, side, sid) in zip(entry_fills, resolved_legs)
            ]

            pos = _OpenPosition(
                symbol=symbol,
                expiry=expiry,
                lots=lots,
                lot_sz=lot_sz,
                entry_time=now_dt,
                legs=legs,
                entry_credit=entry_credit,
                entry_charges=entry_charges,
            )
            self._open_positions.append(pos)
            self._write_checkpoint()
            self._write_signal_record("entry", symbol, vix=vix_val, dte=dte, bucket=vix_bucket)
            _log.info("entry: %s ENTERED expiry=%s dte=%d credit=%.2f legs=%d", symbol, expiry, dte, entry_credit, len(legs))

    def _get_fill(
        self,
        sid: str,
        side: Side,
        quantity: int,
        depth_source: str,
        symbol: str,
        role: str,
        contract: Contract,
        fill_model: FillModel,
        now_ts: pd.Timestamp,
        now_dt: datetime,
    ) -> dict | None:
        """Compute executable fill price and return fill dict. Returns None on insufficient depth."""
        snap = self._depth_cache.snapshot(sid)
        top_bid = 0.0
        top_ask = 0.0
        depth_vwap: float | None = None
        fill_basis = "top_of_book"
        depth_available_qty = 0
        spread_pct = 0.0
        quote_age_ms = 0

        if depth_source == "dhan_20depth":
            exec_price = self._depth_cache.executable_price(sid, side, quantity)
            if exec_price is None:
                return None
            if snap:
                top_bid = snap.best_bid
                top_ask = snap.best_ask
                spread_pct = round(snap.spread_pct * 100, 4)
                depth_available_qty = snap.total_ask_qty if side == Side.BUY else snap.total_bid_qty
                now_pd = pd.Timestamp.now(tz="Asia/Kolkata")
                age = (now_pd - (snap.ask_ts if side == Side.BUY else snap.bid_ts)).total_seconds()
                quote_age_ms = int(age * 1000)
            depth_vwap = exec_price
            fill_basis = "depth_vwap"
            fill_price = exec_price
        else:
            # SENSEX: top-of-book from live feed type-8 or DepthCache if available
            if snap:
                top_bid = snap.best_bid
                top_ask = snap.best_ask
                fill_price = top_ask if side == Side.BUY else top_bid
                fill_basis = "top_of_book_depth_cache"
            elif sid in self._last_tob:
                tob = self._last_tob[sid]
                top_bid = tob["best_bid"]
                top_ask = tob["best_ask"]
                fill_price = top_ask if side == Side.BUY else top_bid
                fill_basis = "top_of_book_feed"
                age = (pd.Timestamp.now(tz="Asia/Kolkata") - tob["ts"]).total_seconds()
                quote_age_ms = int(age * 1000)
            else:
                return None

            if fill_price <= 0:
                return None

        # Mark mid from DepthCache or last_tob
        mark_mid = (top_bid + top_ask) / 2 if top_bid and top_ask else fill_price

        # Charges
        charges = fill_model.estimate_charges(side, quantity, fill_price, trade_date=self._session_date)

        greeks_data = self._chain_greeks.get(sid, {})

        return {
            "timestamp": now_dt.isoformat(),
            "symbol": symbol,
            "security_id": sid,
            "leg_role": role,
            "side": side.value,
            "quantity": quantity,
            "price": round(fill_price, 2),
            "mark_mid": round(mark_mid, 2),
            "top_bid": round(top_bid, 2),
            "top_ask": round(top_ask, 2),
            "depth_vwap": round(depth_vwap, 2) if depth_vwap is not None else None,
            "fill_basis": fill_basis,
            "spread_pct": spread_pct,
            "quote_age_ms": quote_age_ms,
            "depth_available_qty": depth_available_qty,
            "greeks": greeks_data.get("greeks", {}),
            "iv": greeks_data.get("iv"),
            "charges": charges,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Exit
    # ──────────────────────────────────────────────────────────────────────────

    async def _exit_all_positions(self, reason: str, forced_stale: bool) -> None:
        if not self._open_positions:
            return

        now_ts = pd.Timestamp.now(tz="Asia/Kolkata")
        now_dt = _now_ist()
        charges_cfg = ChargesConfig.for_date(self._session_date)
        fill_model = FillModel(charges=charges_cfg)
        still_open: list[_OpenPosition] = []

        for pos in self._open_positions:
            sym_cfg = self._profile["symbols"][pos.symbol]
            depth_source = sym_cfg.get("depth_source", "dhan_20depth")
            exit_fills: list[dict] = []
            exit_fail = False

            for leg in pos.legs:
                # On exit we reverse the entry side
                entry_side = Side(leg["side"])
                exit_side = Side.BUY if entry_side == Side.SELL else Side.SELL
                sid = leg["security_id"]
                role = leg["leg_role"]

                # Rebuild contract from stored data
                contract = Contract(
                    expiry=date.fromisoformat(leg["expiry"]),
                    strike=leg["strike"],
                    option_type=OptionType(leg["option_type"]),
                    ticker=leg["ticker"],
                )

                fill_dict = self._get_fill(
                    sid=sid,
                    side=exit_side,
                    quantity=pos.lots * pos.lot_size,
                    depth_source=depth_source,
                    symbol=pos.symbol,
                    role=role,
                    contract=contract,
                    fill_model=fill_model,
                    now_ts=now_ts,
                    now_dt=now_dt,
                )

                if fill_dict is None:
                    if forced_stale:
                        # Use last known quote as stale exit price
                        fill_dict = self._stale_fill(sid, exit_side, pos, leg, role, fill_model, now_dt)
                        fill_dict["forced_stale_exit"] = True
                    else:
                        exit_fail = True
                        break

                exit_fills.append(fill_dict)

            if exit_fail:
                _log.warning("exit: %s — depth insufficient, deferring to stale deadline", pos.symbol)
                still_open.append(pos)
                continue

            pos.exit_fills = exit_fills
            pos.exit_time = now_dt
            pos.exit_reason = reason
            pos.forced_stale_exit = forced_stale

            trade_dict = self._finalize_trade(pos)
            self._completed_trades.append(trade_dict)
            self._log_signal("exit", pos.symbol, reason=reason)
            _log.info("exit: %s reason=%s net_pnl=%.2f", pos.symbol, reason, trade_dict.get("net_pnl", 0))

        self._open_positions = still_open
        self._write_checkpoint()
        self._flush_trades()

    def _stale_fill(
        self, sid: str, exit_side: Side, pos: _OpenPosition,
        leg: dict, role: str, fill_model: FillModel, now_dt: datetime
    ) -> dict:
        """Last-resort fill using best available quote — price may be stale."""
        resolver = self._resolvers.get(pos.symbol)
        fill_price = 0.05  # absolute floor
        if resolver is not None:
            try:
                contract = Contract(
                    expiry=date.fromisoformat(leg["expiry"]),
                    strike=leg["strike"],
                    option_type=OptionType(leg["option_type"]),
                    ticker=leg["ticker"],
                )
                bar = resolver.bar_at(contract, pd.Timestamp.now(tz="Asia/Kolkata"))
                if bar is not None:
                    fill_price = max(0.05, float(bar["close"]))
            except Exception:
                pass

        quantity = pos.lots * pos.lot_size
        charges = fill_model.estimate_charges(exit_side, quantity, fill_price, trade_date=self._session_date)
        return {
            "timestamp": now_dt.isoformat(),
            "symbol": pos.symbol,
            "security_id": sid,
            "leg_role": role,
            "side": exit_side.value,
            "quantity": quantity,
            "price": round(fill_price, 2),
            "mark_mid": round(fill_price, 2),
            "top_bid": 0.0,
            "top_ask": 0.0,
            "depth_vwap": None,
            "fill_basis": "stale_last_known",
            "spread_pct": 0.0,
            "quote_age_ms": -1,
            "depth_available_qty": 0,
            "greeks": {},
            "iv": None,
            "charges": charges,
            "forced_stale_exit": True,
        }

    def _finalize_trade(self, pos: _OpenPosition) -> dict:
        """Convert closed position to trade dict with PnL."""
        exit_debit = 0.0
        exit_charges = 0.0
        for ef in pos.exit_fills:
            q = ef["quantity"]
            p = ef["price"]
            side = Side(ef["side"])
            if side == Side.BUY:
                exit_debit += p * q
            else:
                exit_debit -= p * q
            exit_charges += ef.get("charges", 0.0)

        gross_pnl = pos.entry_credit - exit_debit
        total_charges = pos.entry_charges + exit_charges
        net_pnl = gross_pnl - total_charges

        # Extract strikes from legs
        strikes: dict[str, int] = {}
        for leg in pos.legs:
            strikes[leg["leg_role"]] = leg["strike"]

        return {
            "symbol": pos.symbol,
            "expiry": pos.expiry.isoformat(),
            "strategy": self._profile.get("report_label", "IronCondorWing6"),
            "session_date": self._session_date.isoformat(),
            "entry_time": pos.entry_time.isoformat(),
            "exit_time": pos.exit_time.isoformat() if pos.exit_time else None,
            "entry_reason": "time_entry",
            "exit_reason": pos.exit_reason,
            "lots": pos.lots,
            "lot_size": pos.lot_size,
            "entry_credit": round(pos.entry_credit, 2),
            "exit_debit": round(exit_debit, 2),
            "gross_pnl": round(gross_pnl, 2),
            "charges": round(total_charges, 2),
            "net_pnl": round(net_pnl, 2),
            "resumed_after_crash": self._resumed_after_crash,
            "crash_gap_start": self._crash_gap_start,
            "crash_gap_end": self._crash_gap_end,
            "gap_minutes": self._crash_gap_minutes,
            "short_call_strike": strikes.get("short_call", 0),
            "long_call_strike": strikes.get("long_call", 0),
            "short_put_strike": strikes.get("short_put", 0),
            "long_put_strike": strikes.get("long_put", 0),
            "forced_stale_exit": pos.forced_stale_exit,
            "entry_fills": [leg["entry_fill"] for leg in pos.legs],
            "exit_fills": pos.exit_fills,
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Snapshots and health
    # ──────────────────────────────────────────────────────────────────────────

    async def _snapshot_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.to_thread(self._write_feed_state)
                await asyncio.to_thread(self._write_spot_bars)
                await asyncio.to_thread(self._write_process_health)
            except Exception as exc:
                _log.warning("snapshot_loop: error — %r", exc)
            await asyncio.sleep(_SNAPSHOT_INTERVAL)

    def _write_feed_state(self) -> None:
        # Quote freshness: fraction of subscribed option IDs with a fresh quote
        sub_sids = [inst["SecurityId"] for inst in self._subscribed_ids
                    if inst.get("ExchangeSegment") in (_SEG_NSE_FNO, _SEG_BSE_FNO)]
        ready = sum(1 for sid in sub_sids if self._is_quote_fresh(sid)) if sub_sids else 0
        freshness_pct = round(ready / len(sub_sids) * 100, 1) if sub_sids else 0.0
        quotes = self._merged_quote_snapshot()

        state = {
            "written_at": _ts_str(),
            "connected": self._feed_connected,
            "subscribed_count": len(self._subscribed_ids),
            "quote_freshness_pct": freshness_pct,
            "core_quotes": self._core_quote_state(quotes),
            "chain_status": dict(self._chain_status),
        }
        _write_atomic(self._snapshot_dir / "latest_feed_state.json", state)

        # Per-security quotes: merge last_tob + resolver quote snapshots + chain greeks
        for sid, tob in self._last_tob.items():
            if sid not in quotes:
                quotes[sid] = {"ltp": tob.get("ltp", 0.0), "oi": 0, "volume": 0, "ts": tob.get("ts", pd.Timestamp.now(tz="Asia/Kolkata")).isoformat()}
        # Attach IV and greeks from chain_greeks cache
        for sid, meta in self._chain_greeks.items():
            if sid in quotes:
                quotes[sid]["iv"] = meta.get("iv")
                quotes[sid]["delta"] = (meta.get("greeks") or {}).get("delta")
                quotes[sid]["theta"] = (meta.get("greeks") or {}).get("theta")
        if quotes:
            _write_atomic(self._snapshot_dir / "latest_quotes.json", {"written_at": _ts_str(), "quotes": quotes})

    def _merged_quote_snapshot(self) -> dict[str, dict]:
        quotes: dict[str, dict] = {}
        for resolver in self._resolvers.values():
            for sid, q in resolver.quote_snapshot().items():
                quotes[sid] = q
        return quotes

    def _core_quote_state(self, quotes: dict[str, dict]) -> dict[str, dict]:
        core_ids = {
            "NIFTY": "13",
            "FINNIFTY": "27",
            "MIDCPNIFTY": "442",
            "SENSEX": "51",
            "BANKNIFTY": "25",
            "INDIA_VIX": self._vix_security_id,
        }
        now = pd.Timestamp.now(tz="Asia/Kolkata")
        out: dict[str, dict] = {}
        for name, sid in core_ids.items():
            quote = quotes.get(str(sid))
            ts = pd.Timestamp(quote["ts"]) if quote and quote.get("ts") else None
            age = (now - ts).total_seconds() if ts is not None else None
            max_age = 60 if name == "INDIA_VIX" else _SPOT_MAX_AGE
            out[name] = {
                "security_id": str(sid),
                "seen": quote is not None,
                "fresh": bool(age is not None and age <= max_age),
                "age_seconds": round(age, 1) if age is not None else None,
                "ltp": quote.get("ltp") if quote else None,
                "ts": quote.get("ts") if quote else None,
                "max_age_seconds": max_age,
            }
        return out

    def _update_spot_bar(self, symbol: str, ltp: float, ts: pd.Timestamp) -> None:
        """Accumulate spot LTP ticks into 1-min OHLCV bars (called from async feed handler)."""
        minute = ts.floor("min")
        bar = self._spot_open_bar[symbol]
        if bar is None or bar["_minute"] != minute:
            if bar is not None:
                # Close the completed bar — compute display epoch (treat IST naive as UTC)
                m = bar["_minute"]
                epoch = int(
                    (pd.Timestamp(m.year, m.month, m.day, m.hour, m.minute)
                     - pd.Timestamp("1970-01-01"))
                    / pd.Timedelta("1s")
                )
                self._spot_bars[symbol].append(
                    {"time": epoch, "open": bar["open"], "high": bar["high"],
                     "low": bar["low"], "close": bar["close"]}
                )
            self._spot_open_bar[symbol] = {
                "_minute": minute, "open": ltp, "high": ltp, "low": ltp, "close": ltp,
            }
        else:
            bar["high"] = max(bar["high"], ltp)
            bar["low"]  = min(bar["low"],  ltp)
            bar["close"] = ltp

    def _write_spot_bars(self) -> None:
        """Write latest_spot_bars.json — closed bars + current open bar for all spot symbols."""
        bars_out: dict[str, list[dict]] = {}
        for sym in _SPOT_SID_TO_SYMBOL.values():
            closed = list(self._spot_bars[sym])
            open_bar = self._spot_open_bar[sym]
            if open_bar is not None:
                m = open_bar["_minute"]
                epoch = int(
                    (pd.Timestamp(m.year, m.month, m.day, m.hour, m.minute)
                     - pd.Timestamp("1970-01-01"))
                    / pd.Timedelta("1s")
                )
                closed = closed + [
                    {"time": epoch, "open": open_bar["open"], "high": open_bar["high"],
                     "low": open_bar["low"], "close": open_bar["close"]}
                ]
            bars_out[sym] = closed
        if any(bars_out.values()):
            _write_atomic(self._snapshot_dir / "latest_spot_bars.json", {
                "written_at": _ts_str(),
                "session_date": self._session_date.isoformat(),
                "bars": bars_out,
            })

    def _write_eod_snapshot(self) -> None:
        """Write latest_eod_snapshot.json at 15:31 — preserves true closing quotes."""
        imap: dict[str, dict] = {}
        for symbol, resolver in self._resolvers.items():
            for sid, info in resolver.instrument_map().items():
                imap[sid] = {"symbol": symbol, **info}
        quotes: dict[str, dict] = {}
        for resolver in self._resolvers.values():
            for sid, q in resolver.quote_snapshot().items():
                quotes[sid] = q
        for sid, tob in self._last_tob.items():
            if sid not in quotes:
                quotes[sid] = {"ltp": tob.get("ltp", 0.0), "oi": 0, "volume": 0, "ts": tob.get("ts", pd.Timestamp.now(tz="Asia/Kolkata")).isoformat()}
        for sid, meta in self._chain_greeks.items():
            if sid in quotes:
                quotes[sid]["iv"] = meta.get("iv")
                quotes[sid]["delta"] = (meta.get("greeks") or {}).get("delta")
                quotes[sid]["theta"] = (meta.get("greeks") or {}).get("theta")
        if imap and quotes:
            _write_atomic(self._snapshot_dir / "latest_eod_snapshot.json", {
                "written_at": _ts_str(),
                "session_date": self._session_date.isoformat(),
                "instruments": imap,
                "quotes": quotes,
            })
            _log.info("eod_snapshot: written %d instruments, %d quotes", len(imap), len(quotes))

    def _write_instrument_map(self) -> None:
        imap: dict[str, dict] = {}
        for symbol, resolver in self._resolvers.items():
            for sid, info in resolver.instrument_map().items():
                imap[sid] = {"symbol": symbol, **info}
        if imap:
            _write_atomic(self._snapshot_dir / "latest_instrument_map.json", {"written_at": _ts_str(), "instruments": imap})

    def _write_process_health(self) -> None:
        health = {
            "written_at": _ts_str(),
            "pid": os.getpid(),
            "phase": self._phase,
            "open_positions": len(self._open_positions),
            "session_date": self._session_date.isoformat(),
            "resumed_after_crash": self._resumed_after_crash,
            "crash_gap_start": self._crash_gap_start,
            "crash_gap_end": self._crash_gap_end,
            "gap_minutes": self._crash_gap_minutes,
        }
        _write_atomic(self._snapshot_dir / "latest_process_health.json", health)

    def _is_quote_fresh(self, sid: str) -> bool:
        # Check depth cache first (NSE symbols), then last_tob fallback (SENSEX)
        if self._depth_cache.is_ready(sid, max_age_seconds=_OPTION_MAX_AGE):
            return True
        tob = self._last_tob.get(sid)
        if tob is not None:
            age = (pd.Timestamp.now(tz="Asia/Kolkata") - tob["ts"]).total_seconds()
            return age <= _OPTION_MAX_AGE
        return False

    # ──────────────────────────────────────────────────────────────────────────
    # Checkpoint and output
    # ──────────────────────────────────────────────────────────────────────────

    def _write_checkpoint(self) -> None:
        checkpoint = {
            "session_date": self._session_date.isoformat(),
            "written_at": _ts_str(),
            "resumed_after_crash": self._resumed_after_crash,
            "crash_gap_start": self._crash_gap_start,
            "crash_gap_end": self._crash_gap_end,
            "gap_minutes": self._crash_gap_minutes,
            "open_positions": [p.to_dict() for p in self._open_positions],
        }
        _write_atomic(self._snapshot_dir / "latest_open_positions.json", checkpoint)

    def _flush_trades(self) -> None:
        self._trades_path.parent.mkdir(parents=True, exist_ok=True)
        self._trades_path.write_text(json.dumps(self._completed_trades, indent=2, default=str), encoding="utf-8")

    def _generate_eod_report(self) -> None:
        self._flush_trades()
        total_net = sum(t.get("net_pnl", 0) for t in self._completed_trades)
        total_gross = sum(t.get("gross_pnl", 0) for t in self._completed_trades)
        total_charges = sum(t.get("charges", 0) for t in self._completed_trades)
        n_trades = len(self._completed_trades)
        wins = sum(1 for t in self._completed_trades if t.get("net_pnl", 0) > 0)
        summary_payload = {
            "session_date": self._session_date.isoformat(),
            "profile": self._profile.get("profile_name", ""),
            "trades": n_trades,
            "wins": wins,
            "gross_pnl": round(total_gross, 2),
            "charges": round(total_charges, 2),
            "net_pnl": round(total_net, 2),
            "resumed_after_crash": self._resumed_after_crash,
            "crash_gap_start": self._crash_gap_start,
            "crash_gap_end": self._crash_gap_end,
            "gap_minutes": self._crash_gap_minutes,
        }

        lines = [
            f"# Paper Trading Report — {self._session_date}",
            "",
            f"**Profile:** {self._profile.get('profile_name', '')}",
            f"**Trades:** {n_trades}",
            f"**Wins:** {wins} / {n_trades}",
            f"**Gross PnL:** ₹{total_gross:,.2f}",
            f"**Charges:** ₹{total_charges:,.2f}",
            f"**Net PnL:** ₹{total_net:,.2f}",
            f"**Resumed after crash:** {str(self._resumed_after_crash).lower()}",
            "",
            "## Trade Detail",
            "",
        ]
        if self._resumed_after_crash:
            lines.extend([
                "## Crash Recovery",
                "",
                "```json",
                json.dumps({
                    "resumed_after_crash": True,
                    "crash_gap_start": self._crash_gap_start,
                    "crash_gap_end": self._crash_gap_end,
                    "gap_minutes": self._crash_gap_minutes,
                }, indent=2),
                "```",
                "",
            ])
        for t in self._completed_trades:
            lines.append(
                f"- **{t['symbol']}** expiry={t['expiry']} credit={t['entry_credit']:.2f} "
                f"exit_debit={t['exit_debit']:.2f} net={t['net_pnl']:.2f} "
                f"exit={t['exit_reason']}"
            )

        self._report_path.parent.mkdir(parents=True, exist_ok=True)
        self._report_path.write_text("\n".join(lines), encoding="utf-8")
        _write_atomic(self._live_root / "reports" / f"{self._date_str}_eod_summary.json", summary_payload)
        _log.info("eod_report: written to %s", self._report_path)

    @staticmethod
    def _compute_gap_minutes(start: str | None, end: str | None) -> float | None:
        if not start or not end:
            return None
        try:
            start_ts = pd.Timestamp(start)
            end_ts = pd.Timestamp(end)
            return round((end_ts - start_ts).total_seconds() / 60.0, 1)
        except Exception:
            return None

    # ──────────────────────────────────────────────────────────────────────────
    # Signal log
    # ──────────────────────────────────────────────────────────────────────────

    def _log_signal(self, event: str, symbol: str, reason: str = "", **kwargs) -> None:
        record: dict = {"ts": _ts_str(), "event": event, "symbol": symbol, "reason": reason}
        record.update({k: v for k, v in kwargs.items() if v is not None})
        self._signals_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._signals_path, "a") as f:
            f.write(json.dumps(record) + "\n")

    def _write_signal_record(self, event: str, symbol: str, **kwargs) -> None:
        self._log_signal(event, symbol, reason=event, **kwargs)

    # ──────────────────────────────────────────────────────────────────────────
    # Setup helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _ensure_dirs(self) -> None:
        for subdir in ("paper_trades", "reports", "logs", "snapshots", "alerts"):
            (self._live_root / subdir).mkdir(parents=True, exist_ok=True)

    def _setup_file_logging(self) -> None:
        log_path = self._log_dir / f"{self._date_str}.log"
        handler = logging.FileHandler(str(log_path))
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
        logging.getLogger().addHandler(handler)
        _log.info("file_logging: writing to %s", log_path)

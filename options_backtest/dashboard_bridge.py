"""
DashboardBridge — read-only data access layer for the FastAPI dashboard.

Reads flushed snapshot files from the live WD storage root. Never opens
websockets, never modifies engine state. All public methods return empty/
default objects when files are missing — the API returns "waiting" state
rather than 500 errors.

TTL cache per data source:
  live snapshots (process health, feed state, depth cache, alert state): 3 s
  paper trades JSON / signals JSONL: 5 s
  historical parquet: 60 s
"""

from __future__ import annotations

import json
import os
import re
import hashlib
import time as _time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import date, datetime, time as _time_cls
from pathlib import Path
from typing import Any

import pandas as pd

from .calendar import is_trading_day as _is_trading_day, get_instrument_spec
from .live_paths import resolve_durable_dir, resolve_snapshot_dir

_REPO_ROOT = Path(__file__).parents[1]
_DATA_ROOT = Path(os.environ.get("MARKET_DATA_ROOT", _REPO_ROOT / "data"))
_REPORT_ROOT = Path(os.environ.get("BACKTEST_REPORT_ROOT", _REPO_ROOT / "reports" / "backtests"))
_SPOT_FILES: dict[str, Path] = {
    "NIFTY":      _DATA_ROOT / "processed" / "spot" / "nifty50_1min_CANONICAL.csv",
    "BANKNIFTY":  _DATA_ROOT / "processed" / "spot" / "banknifty_1min_DHAN.csv",
    "FINNIFTY":   _DATA_ROOT / "processed" / "spot" / "finnifty_1min_DHAN.csv",
    "MIDCPNIFTY": _DATA_ROOT / "processed" / "spot" / "midcpnifty_1min_DHAN.csv",
    "SENSEX":     _DATA_ROOT / "processed" / "spot" / "sensex_1min_DHAN.csv",
}
_VIX_FILES = [
    _DATA_ROOT / "processed" / "spot" / "indiavix_1min_DHAN.csv",
    _DATA_ROOT / "processed" / "market_archive_cleaned" / "INDIA VIX_minute.csv",
    _DATA_ROOT / "processed" / "market_archive_cleaned" / "INDIA VIX_day.csv",
]
_OPTIONS_ROOT = _DATA_ROOT / "processed" / "options" / "dhan"
_BHAVCOPY_ROOT = _DATA_ROOT / "processed" / "nse" / "bhavcopy" / "fo"
_BACKTEST_ROOT = _REPORT_ROOT / "dashboard_runs"
_BACKTEST_INDEX_ROOT = _REPORT_ROOT / "dashboard_index"
_BACKTEST_INDEX_PATH = _BACKTEST_INDEX_ROOT / "backtest_index.json"
_LEGACY_BACKTEST_ROOTS = [
    _REPORT_ROOT / "options" / "focused",
    _REPORT_ROOT / "options" / "risk_management",
    _REPORT_ROOT / "options" / "research_validation",
    _REPORT_ROOT / "options" / "monitoring",
    _REPORT_ROOT / "options" / "legacy",
]
_HIST_ROW_LIMIT = 20_000

_IST_NAME = "Asia/Kolkata"
try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo(_IST_NAME)
except ImportError:
    import pytz
    _IST = pytz.timezone(_IST_NAME)  # type: ignore[assignment]

_TOKEN_RE = re.compile(r"[A-Za-z0-9_\-]{100,}")

# Cache TTLs (seconds)
_TTL_LIVE = 3.0
_TTL_TRADES = 5.0
_TTL_HIST = 60.0
_TTL_BACKTESTS = 120.0
_BACKTEST_INDEX_VERSION = 3


# ── Dataclasses (bridge-internal; mirrored by Pydantic models in api/models.py) ──

@dataclass
class LegFill:
    leg_role: str       # short_call / long_call / short_put / long_put
    strike: int
    option_type: str    # CE / PE
    side: str           # BUY / SELL
    price: float
    mark_mid: float
    top_bid: float
    top_ask: float
    spread_pct: float
    quote_age_ms: int
    depth_available_qty: int
    iv: float | None
    delta: float | None
    theta: float | None
    charges: float


@dataclass
class PositionRow:
    symbol: str
    expiry: str
    lots: int
    lot_size: int
    entry_time: str
    entry_credit: float
    entry_charges: float
    legs: list[LegFill]
    # mark-to-market fields; None until a live quote update is available
    current_mark: float | None
    unrealised_gross_pnl: float | None
    unrealised_net_pnl: float | None


@dataclass
class EquityPoint:
    ts: str                       # ISO-8601 IST
    cumulative_gross_pnl: float
    cumulative_net_pnl: float


@dataclass
class SignalLogEntry:
    ts: str
    event: str          # skip / entry / exit
    symbol: str
    reason: str
    vix: float | None
    dte: int | None
    bucket: str | None


@dataclass
class DepthSummary:
    """Aggregate depth-cache stats from latest_depth_cache.json snapshot.

    Per-instrument rows require direct DepthCache access (not available
    from the snapshot file). Full per-instrument heatmap is a v2 feature
    once the collector exports a richer snapshot.
    """
    written_at: str | None
    configured: int
    tracked: int
    ready: int
    total: int
    ready_pct: float
    snapshot_age_s: float | None    # seconds since file was last written


@dataclass
class StorageHealth:
    wd_mount_ok: bool
    wd_free_gb: float
    raw_packet_flush_age_s: float | None    # seconds since newest raw packet file
    parquet_flush_age_s: float | None       # seconds since newest order-book parquet
    depth_cache_age_s: float | None         # seconds since latest_depth_cache.json


@dataclass
class AlertEntry:
    ts: str
    severity: str       # critical / warning / info
    component: str
    reason: str
    message: str        # scrubbed


@dataclass
class AlertState:
    written_at: str | None
    uptime_pct: float
    active_alerts: list[AlertEntry]
    alert_counts: dict[str, int]
    wd_free_gb: float
    latest_external_heartbeat_at: str | None
    latest_external_heartbeat_status: str | None


@dataclass
class SessionStatus:
    session_date: str           # YYYY-MM-DD
    market_status: str          # PRE_OPEN / OPEN / CLOSED / HOLIDAY
    engine_phase: str           # init/connecting/chain_fetch/entry/monitoring/exit/eod/offline
    engine_pid: int | None
    open_position_count: int
    feed_connected: bool
    feed_subscribed_count: int
    quote_freshness_pct: float
    depth: DepthSummary | None
    process_health_age_s: float | None  # seconds since last process health write
    written_at: str | None


_EMPTY_ALERT_STATE = AlertState(
    written_at=None, uptime_pct=0.0, active_alerts=[],
    alert_counts={}, wd_free_gb=0.0,
    latest_external_heartbeat_at=None, latest_external_heartbeat_status=None,
)
_EMPTY_STORAGE = StorageHealth(
    wd_mount_ok=False, wd_free_gb=0.0,
    raw_packet_flush_age_s=None, parquet_flush_age_s=None,
    depth_cache_age_s=None,
)


# ── Market status helper ─────────────────────────────────────────────────────

def _market_status(today: date) -> str:
    now = datetime.now(tz=_IST)
    if not _is_trading_day(today):
        return "HOLIDAY"
    t = now.time()
    if t < _time_cls(9, 0):
        return "CLOSED"
    if t < _time_cls(9, 15):
        return "PRE_OPEN"
    if t <= _time_cls(15, 30):
        return "OPEN"
    return "CLOSED"


# ── DashboardBridge ──────────────────────────────────────────────────────────

class DashboardBridge:
    """
    Read-only data bridge between the FastAPI layer and the live engine files.

    Instantiate once at app startup; inject via FastAPI app.state.bridge.
    All public get_* methods are synchronous; FastAPI routes wrap them with
    asyncio.get_event_loop().run_in_executor for async contexts.
    """

    def __init__(self, live_root: Path, engine_metrics_url: str | None = None) -> None:
        self._root = live_root
        self._snapshot_dir = resolve_snapshot_dir(live_root)
        self._durable_dir = resolve_durable_dir(live_root)
        # {cache_key: (expiry_monotonic, value)}
        self._cache: dict[str, tuple[float, Any]] = {}
        # ── HTTP path to the engine's in-process /metrics (Phase E1) ─
        # When set, ``_read_engine_metrics`` is preferred over file
        # mtime reads for live state. Falls back to file reads on any
        # error (connection refused, timeout, non-200). Configurable
        # via env so tests and dev can disable.
        self._engine_metrics_url = (
            engine_metrics_url
            or os.environ.get("ENGINE_METRICS_URL")
            or "http://127.0.0.1:8001/metrics"
        )
        self._engine_metrics_enabled = bool(
            os.environ.get("ENGINE_METRICS_ENABLED", "1").strip().lower()
            not in ("0", "false", "no", "")
        )
        self._engine_metrics_cache: tuple[float, dict | None] | None = None
        # Short TTL — the engine writes snapshots on a ~5 s cadence and
        # we want at most one HTTP round trip per route call burst.
        self._engine_metrics_ttl_seconds = 2.0
        self._engine_metrics_timeout_seconds = 0.5

    # ── In-process engine /metrics HTTP probe (Phase E1) ─────────────────────

    def _read_engine_metrics(self) -> dict | None:
        """Return the engine's ``/metrics`` payload, cached for ~2 s.

        Returns ``None`` if the endpoint is unreachable or disabled.
        Importing ``requests`` lazily keeps this module importable in
        environments where ``requests`` isn't installed (the bridge is
        also exercised by lightweight unit tests).
        """
        if not self._engine_metrics_enabled:
            return None
        now = _time.monotonic()
        cached = self._engine_metrics_cache
        if cached is not None and now < cached[0]:
            return cached[1]
        payload: dict | None = None
        try:
            import requests  # local import — cheap when cached
            resp = requests.get(self._engine_metrics_url, timeout=self._engine_metrics_timeout_seconds)
            if resp.status_code == 200:
                data = resp.json()
                if isinstance(data, dict):
                    payload = data
        except Exception:
            payload = None
        self._engine_metrics_cache = (now + self._engine_metrics_ttl_seconds, payload)
        return payload

    # ── Internal helpers ─────────────────────────────────────────────────────

    def _cached(self, key: str, ttl: float, loader) -> Any:
        entry = self._cache.get(key)
        if entry and _time.monotonic() < entry[0]:
            return entry[1]
        try:
            val = loader()
        except Exception:
            # Return stale value if we have one; otherwise None
            val = self._cache[key][1] if key in self._cache else None
        self._cache[key] = (_time.monotonic() + ttl, val)
        return val

    def _read_json(self, path: Path) -> dict | None:
        if not path.exists():
            return None
        for attempt in range(2):
            try:
                return json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                if attempt == 0:
                    _time.sleep(0.1)
        return None

    def _file_age_s(self, path: Path) -> float | None:
        try:
            return _time.time() - path.stat().st_mtime
        except OSError:
            return None

    @staticmethod
    def _scrub(text: str) -> str:
        return _TOKEN_RE.sub("[REDACTED]", str(text))

    # Files that must survive reboot — read from WD live_root/snapshots.
    _DURABLE_SNAPS = frozenset({
        "latest_open_positions.json",
        "latest_eod_snapshot.json",
        "latest_depth_collector_state.json",
    })

    def _snap(self, name: str) -> Path:
        if name in self._DURABLE_SNAPS:
            return self._durable_dir / name
        return self._snapshot_dir / name

    def _read_chain_metadata(self) -> dict:
        return (
            self._read_json(self._snapshot_dir / "latest_chain_metadata.json")
            or self._read_json(self._durable_dir / "latest_chain_metadata.json")
            or {}
        )

    # ── Session status ───────────────────────────────────────────────────────

    def get_session_status(self) -> SessionStatus:
        def _load() -> SessionStatus:
            # Phase E1: prefer the engine's in-process /metrics HTTP
            # endpoint. A response means the engine's asyncio loop is
            # alive — there is no mtime-vs-liveness ambiguity. Fall
            # back to file reads when the endpoint is unreachable.
            engine = self._read_engine_metrics()
            ph = self._read_json(self._snap("latest_process_health.json"))
            fs = self._read_json(self._snap("latest_feed_state.json"))
            dc = self._read_json(self._snap("latest_depth_cache.json"))

            session_date_str = (
                (engine or {}).get("session_date")
                or (ph or {}).get("session_date")
                or date.today().isoformat()
            )
            try:
                today = date.fromisoformat(session_date_str)
            except ValueError:
                today = date.today()

            depth_summary: DepthSummary | None = None
            if dc:
                depth_summary = DepthSummary(
                    written_at=dc.get("written_at"),
                    configured=dc.get("configured_security_ids", 0),
                    tracked=dc.get("tracked_security_ids", 0),
                    ready=dc.get("ready", 0),
                    total=dc.get("total", 0),
                    ready_pct=dc.get("ready_pct", 0.0),
                    snapshot_age_s=self._file_age_s(self._snap("latest_depth_cache.json")),
                )

            engine_phase = (
                (engine or {}).get("phase")
                or (ph or {}).get("phase")
                or "offline"
            )
            engine_pid = (engine or {}).get("pid") if engine else (ph or {}).get("pid")
            open_count = (
                (engine or {}).get("open_positions")
                if engine
                else (ph or {}).get("open_positions", 0)
            )
            feed_connected = (
                (engine or {}).get("feed_connected")
                if engine and "feed_connected" in engine
                else (fs or {}).get("connected", False)
            )
            written_at = (engine or {}).get("written_at") or (ph or {}).get("written_at")

            return SessionStatus(
                session_date=session_date_str,
                market_status=_market_status(today),
                engine_phase=engine_phase,
                engine_pid=engine_pid,
                open_position_count=open_count or 0,
                feed_connected=bool(feed_connected),
                feed_subscribed_count=(fs or {}).get("subscribed_count", 0),
                quote_freshness_pct=(fs or {}).get("quote_freshness_pct", 0.0),
                depth=depth_summary,
                process_health_age_s=self._file_age_s(self._snap("latest_process_health.json")),
                written_at=written_at,
            )

        result = self._cached("session_status", _TTL_LIVE, _load)
        if result is None:
            result = SessionStatus(
                session_date=date.today().isoformat(),
                market_status=_market_status(date.today()),
                engine_phase="offline",
                engine_pid=None,
                open_position_count=0,
                feed_connected=False,
                feed_subscribed_count=0,
                quote_freshness_pct=0.0,
                depth=None,
                process_health_age_s=None,
                written_at=None,
            )
        return result

    # ── Open positions ───────────────────────────────────────────────────────

    def get_open_positions(self) -> list[PositionRow]:
        def _load() -> list[PositionRow]:
            # Phase E1: prefer the engine's in-process /metrics. The
            # ``positions`` array is the same shape as
            # latest_open_positions.json's ``open_positions``.
            engine = self._read_engine_metrics()
            engine_positions = (engine or {}).get("positions") if engine else None
            data: dict
            if engine_positions is not None:
                data = {"open_positions": engine_positions}
            else:
                data = self._read_json(self._snap("latest_open_positions.json")) or {}
            if not data:
                return []
            # Merge with live position marks if available
            marks_data = self._read_json(self._snap("latest_position_marks.json")) or {}
            marks_by_symbol: dict[str, dict] = {}
            for m in marks_data.get("marks", []):
                marks_by_symbol[m.get("symbol", "")] = m

            rows: list[PositionRow] = []
            for pos in data.get("open_positions", []):
                legs = _parse_legs(pos.get("legs", []))
                sym = pos.get("symbol", "")
                mark = marks_by_symbol.get(sym, {})
                stale = mark.get("stale", True)
                rows.append(PositionRow(
                    symbol=sym,
                    expiry=pos.get("expiry", ""),
                    lots=pos.get("lots", 0),
                    lot_size=pos.get("lot_size", 0),
                    entry_time=pos.get("entry_time", ""),
                    entry_credit=pos.get("entry_credit", 0.0),
                    entry_charges=pos.get("entry_charges", 0.0),
                    legs=legs,
                    current_mark=mark.get("current_mark") if not stale else None,
                    unrealised_gross_pnl=mark.get("unrealised_gross_pnl") if not stale else None,
                    unrealised_net_pnl=mark.get("unrealised_net_pnl") if not stale else None,
                ))
            return rows

        result = self._cached("open_positions", _TTL_LIVE, _load)
        return result if result is not None else []

    # ── Intraday equity curve ────────────────────────────────────────────────

    def get_equity_curve(self, session_date: date) -> list[EquityPoint]:
        date_str = session_date.strftime("%Y%m%d")

        def _load() -> list[EquityPoint]:
            points_by_ts: dict[str, EquityPoint] = {}

            def _point_from_tick(tick: dict) -> EquityPoint | None:
                ts = str(tick.get("ts") or "")
                if not ts.startswith(session_date.isoformat()):
                    return None
                total_net = tick.get("total_net_pnl")
                if total_net is None:
                    return None
                total_gross = tick.get("total_gross_pnl")
                if total_gross is None:
                    total_gross = tick.get("realised_gross_pnl", tick.get("realised_net_pnl", 0.0))
                return EquityPoint(
                    ts=ts,
                    cumulative_gross_pnl=round(float(total_gross), 2),
                    cumulative_net_pnl=round(float(total_net), 2),
                )

            # Live history is absolute mark-to-market total PnL per tick.
            history_path = self._root / "equity_ticks" / f"{date_str}.jsonl"
            if history_path.exists():
                try:
                    for line in history_path.read_text(encoding="utf-8").splitlines():
                        if not line.strip():
                            continue
                        point = _point_from_tick(json.loads(line))
                        if point is not None:
                            points_by_ts[point.ts] = point
                except Exception:
                    pass

            # Include the latest tick even if the JSONL history is delayed.
            tick = self._read_json(self._snap("latest_equity_tick.json")) or {}
            point = _point_from_tick(tick)
            if point is not None and tick.get("open_positions", 0) > 0:
                points_by_ts[point.ts] = point

            # Closed trades are realised absolute totals. They complement the
            # live MTM series without being added on top of the latest tick.
            path = self._root / "paper_trades" / f"{date_str}.json"
            if not path.exists():
                return sorted(points_by_ts.values(), key=lambda p: p.ts)
            try:
                trades: list[dict] = json.loads(path.read_text(encoding="utf-8"))
                cum_gross = 0.0
                cum_net = 0.0
                for trade in sorted(trades, key=lambda t: t.get("exit_time") or ""):
                    exit_t = trade.get("exit_time")
                    if not exit_t:
                        continue
                    cum_gross += trade.get("gross_pnl", 0.0)
                    cum_net += trade.get("net_pnl", 0.0)
                    points_by_ts[exit_t] = EquityPoint(
                        ts=exit_t,
                        cumulative_gross_pnl=round(cum_gross, 2),
                        cumulative_net_pnl=round(cum_net, 2),
                    )
            except Exception:
                pass
            return sorted(points_by_ts.values(), key=lambda p: p.ts)

        result = self._cached(f"equity_{date_str}", _TTL_LIVE, _load)
        return result if result is not None else []

    # ── Closed trades (today's completed positions) ──────────────────────────

    def get_closed_trades(self, session_date: date) -> list[dict]:
        date_str = session_date.strftime("%Y%m%d")

        def _load() -> list[dict]:
            path = self._root / "paper_trades" / f"{date_str}.json"
            if not path.exists():
                return []
            try:
                trades: list[dict] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return []
            result = []
            for t in sorted(trades, key=lambda x: x.get("exit_time") or ""):
                if not t.get("exit_time"):
                    continue
                result.append({
                    "symbol": t.get("symbol", ""),
                    "expiry": t.get("expiry", ""),
                    "entry_time": t.get("entry_time", ""),
                    "exit_time": t.get("exit_time", ""),
                    "entry_reason": t.get("entry_reason", ""),
                    "exit_reason": t.get("exit_reason", ""),
                    "lots": t.get("lots", 0),
                    "lot_size": t.get("lot_size", 0),
                    "entry_credit": t.get("entry_credit", 0.0),
                    "exit_debit": t.get("exit_debit", 0.0),
                    "gross_pnl": t.get("gross_pnl", 0.0),
                    "charges": t.get("charges", 0.0),
                    "net_pnl": t.get("net_pnl", 0.0),
                    "short_call_strike": t.get("short_call_strike", 0),
                    "long_call_strike": t.get("long_call_strike", 0),
                    "short_put_strike": t.get("short_put_strike", 0),
                    "long_put_strike": t.get("long_put_strike", 0),
                    "forced_stale_exit": bool(t.get("forced_stale_exit", False)),
                })
            return result

        result = self._cached(f"closed_trades_{date_str}", _TTL_TRADES, _load)
        return result if result is not None else []

    # ── Signal log ───────────────────────────────────────────────────────────

    def get_signal_log(self, session_date: date) -> list[SignalLogEntry]:
        date_str = session_date.strftime("%Y%m%d")

        def _load() -> list[SignalLogEntry]:
            path = self._root / "paper_trades" / f"{date_str}_signals.jsonl"
            if not path.exists():
                return []
            entries: list[SignalLogEntry] = []
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        entries.append(SignalLogEntry(
                            ts=rec.get("ts", ""),
                            event=rec.get("event", ""),
                            symbol=rec.get("symbol", ""),
                            reason=rec.get("reason", ""),
                            vix=rec.get("vix"),
                            dte=rec.get("dte"),
                            bucket=rec.get("bucket"),
                        ))
                    except json.JSONDecodeError:
                        continue
            except OSError:
                pass
            return entries

        result = self._cached(f"signals_{date_str}", _TTL_TRADES, _load)
        return result if result is not None else []

    # ── Option chain ─────────────────────────────────────────────────────────

    def _build_chain_rows(
        self,
        instruments: dict[str, dict],
        quotes: dict[str, dict],
        tob: dict[str, dict],
        symbol: str,
    ) -> list[dict]:
        """Assemble per-strike rows for `symbol` from pre-read snapshot dicts."""
        by_strike: dict[tuple, dict] = {}
        for sid, info in instruments.items():
            if info.get("symbol") != symbol:
                continue
            expiry = info.get("expiry", "")
            strike = info.get("strike", 0)
            otype = info.get("option_type", "").upper()
            if otype not in ("CE", "PE"):
                continue
            q = quotes.get(sid, {})
            d = tob.get(sid, {})
            leg = {
                "ltp":     q.get("ltp", 0.0),
                "iv":      q.get("iv"),
                "delta":   q.get("delta"),
                "theta":   q.get("theta"),
                "oi":      q.get("oi", 0),
                "bid":     d.get("bid", q.get("top_bid", 0.0)),
                "ask":     d.get("ask", q.get("top_ask", 0.0)),
                "bid_qty": d.get("bid_qty", q.get("bid_qty", 0)),
                "ask_qty": d.get("ask_qty", q.get("ask_qty", 0)),
                "age_ms":  max(d.get("bid_age_ms", 0), d.get("ask_age_ms", 0)),
                "sid":     sid,
            }
            key = (expiry, strike)
            if key not in by_strike:
                by_strike[key] = {"strike": strike, "expiry": expiry}
            by_strike[key][otype.lower()] = leg
        return sorted(by_strike.values(), key=lambda r: (r["expiry"], r["strike"]))

    @staticmethod
    def _filter_chain_atm(rows: list[dict], spot: float | None, n: int = 15) -> list[dict]:
        """
        Filter rows to the nearest expiry and ATM ± n strikes.
        spot is derived from live spot bars — falls back to put-call parity on rows.
        """
        if not rows:
            return rows

        # Nearest expiry only
        nearest = rows[0]["expiry"]
        rows = [r for r in rows if r["expiry"] == nearest]

        # Derive spot: prefer caller-supplied value, then put-call parity on non-zero rows
        if spot is None:
            for r in rows:
                ce = (r.get("ce") or {}).get("ltp") or 0
                pe = (r.get("pe") or {}).get("ltp") or 0
                if ce > 0 and pe > 0:
                    spot = r["strike"] + ce - pe
                    break

        if spot is None:
            return rows

        # Compute strike step from sorted unique strikes
        strikes = sorted({r["strike"] for r in rows})
        step = 50
        if len(strikes) > 1:
            diffs = [strikes[i + 1] - strikes[i] for i in range(len(strikes) - 1)]
            step = min(diffs) if diffs else 50

        atm = round(spot / step) * step
        return [r for r in rows if abs(r["strike"] - atm) <= n * step]

    @staticmethod
    def _merge_chain_metadata(quotes: dict[str, dict], chain_data: dict) -> dict[str, dict]:
        contracts = chain_data.get("contracts", chain_data) if isinstance(chain_data, dict) else {}
        if not isinstance(contracts, dict):
            return quotes
        merged = {str(sid): dict(q) for sid, q in quotes.items()}
        for sid, meta in contracts.items():
            if not isinstance(meta, dict):
                continue
            q = merged.setdefault(str(sid), {})
            greeks = meta.get("greeks") or {}
            if q.get("ltp") in (None, 0, 0.0) and meta.get("ltp") is not None:
                q["ltp"] = meta.get("ltp")
            if q.get("oi") in (None, 0) and meta.get("oi") is not None:
                q["oi"] = meta.get("oi")
            if q.get("volume") in (None, 0) and meta.get("volume") is not None:
                q["volume"] = meta.get("volume")
            q["iv"] = meta.get("iv")
            q["delta"] = greeks.get("delta")
            q["theta"] = greeks.get("theta")
            q["gamma"] = greeks.get("gamma")
            q["vega"] = greeks.get("vega")
            q["rho"] = greeks.get("rho")
            if meta.get("top_bid") is not None:
                q.setdefault("top_bid", meta.get("top_bid"))
            if meta.get("top_ask") is not None:
                q.setdefault("top_ask", meta.get("top_ask"))
            if meta.get("bid_qty") is not None:
                q.setdefault("bid_qty", meta.get("bid_qty"))
            if meta.get("ask_qty") is not None:
                q.setdefault("ask_qty", meta.get("ask_qty"))
        return merged

    def get_option_chain(self, symbol: str) -> dict:
        """
        Build a per-strike option chain for `symbol`.

        Returns a dict with metadata (spot, atm_strike, depth_status) and rows.
        """
        from datetime import date as _date
        today = _date.today()

        # Prefer EOD snapshot when market closed
        eod_data = self._read_json(self._snap("latest_eod_snapshot.json")) or {}
        use_eod = eod_data.get("session_date") == today.isoformat() and bool(eod_data.get("instruments"))

        # Strike step from calendar (always needed for metadata)
        try:
            spec = get_instrument_spec(symbol.upper())
            step = spec.strike_step
        except Exception:
            step = 50

        if use_eod:
            instruments = eod_data.get("instruments", {})
            quotes = eod_data.get("quotes", {})
            quotes = self._merge_chain_metadata(quotes, eod_data.get("chain_metadata", {}))
            quotes = self._merge_chain_metadata(
                quotes,
                self._read_chain_metadata(),
            )
            tob: dict[str, dict] = {}
            depth_status = "eod"
        else:
            imap_data = self._read_json(self._snap("latest_instrument_map.json"))
            if not imap_data:
                return {"symbol": symbol, "underlying_spot": None, "strike_step": step, "atm_strike": None, "depth_status": "unavailable", "rows": []}
            instruments = imap_data.get("instruments", {})
            quotes = (self._read_json(self._snap("latest_quotes.json")) or {}).get("quotes", {})
            quotes = self._merge_chain_metadata(
                quotes,
                self._read_chain_metadata(),
            )
            tob = (self._read_json(self._snap("latest_depth_cache.json")) or {}).get("tob", {})
            # Derive depth status from depth cache snapshot
            dc = self._read_json(self._snap("latest_depth_cache.json")) or {}
            by_symbol = dc.get("by_symbol", {})
            sym_depth = by_symbol.get(symbol.upper(), {})
            ready_pct = sym_depth.get("ready_pct", 0.0) if isinstance(sym_depth, dict) else 0.0
            if ready_pct >= 95:
                depth_status = "full"
            elif ready_pct >= 50:
                depth_status = "partial"
            else:
                depth_status = "unavailable"

        rows = self._build_chain_rows(instruments, quotes, tob, symbol.upper())

        # Get spot from live spot bars for accurate ATM
        spot: float | None = None
        live_bars = self._read_json(self._snap("latest_spot_bars.json")) or {}
        if live_bars.get("session_date") == today.isoformat():
            sym_bars = live_bars.get("bars", {}).get(symbol.upper(), [])
            if sym_bars:
                spot = float(sym_bars[-1].get("close", 0)) or None

        filtered_rows = self._filter_chain_atm(rows, spot)

        atm_strike = None
        if spot is not None and step:
            atm_strike = int(round(spot / step) * step)

        return {
            "symbol": symbol.upper(),
            "underlying_spot": spot,
            "strike_step": step,
            "atm_strike": atm_strike,
            "depth_status": depth_status,
            "rows": filtered_rows,
        }

    # ── Depth health ─────────────────────────────────────────────────────────

    def get_depth_summary(self) -> DepthSummary | None:
        """Aggregate depth-cache readiness from the collector snapshot file."""
        dc = self._read_json(self._snap("latest_depth_cache.json"))
        if not dc:
            return None
        return DepthSummary(
            written_at=dc.get("written_at"),
            configured=dc.get("configured_security_ids", 0),
            tracked=dc.get("tracked_security_ids", 0),
            ready=dc.get("ready", 0),
            total=dc.get("total", 0),
            ready_pct=dc.get("ready_pct", 0.0),
            snapshot_age_s=self._file_age_s(self._snap("latest_depth_cache.json")),
        )

    # ── Storage health ───────────────────────────────────────────────────────

    def get_storage_health(self) -> StorageHealth:
        def _load() -> StorageHealth:
            alert_data = self._read_json(self._snap("latest_alert_state.json"))
            wd_free_gb = (alert_data or {}).get("wd_free_gb", 0.0)
            wd_ok = self._root.exists() and os.access(self._root, os.W_OK)

            today_str = date.today().strftime("%Y%m%d")

            # Newest raw packet file age
            raw_dir = self._root / "raw_depth_packets" / today_str
            raw_age: float | None = None
            if raw_dir.exists():
                try:
                    mtimes = [f.stat().st_mtime for f in raw_dir.iterdir() if f.is_file()]
                    if mtimes:
                        raw_age = _time.time() - max(mtimes)
                except OSError:
                    pass

            # Newest order-book parquet age
            ob_dir = self._root / "order_book" / today_str
            parquet_age: float | None = None
            if ob_dir.exists():
                try:
                    mtimes = [
                        f.stat().st_mtime for f in ob_dir.iterdir()
                        if f.suffix == ".parquet"
                    ]
                    if mtimes:
                        parquet_age = _time.time() - max(mtimes)
                except OSError:
                    pass

            return StorageHealth(
                wd_mount_ok=wd_ok,
                wd_free_gb=wd_free_gb,
                raw_packet_flush_age_s=raw_age,
                parquet_flush_age_s=parquet_age,
                depth_cache_age_s=self._file_age_s(self._snap("latest_depth_cache.json")),
            )

        result = self._cached("storage_health", _TTL_LIVE, _load)
        return result if result is not None else _EMPTY_STORAGE

    # ── Alert state ──────────────────────────────────────────────────────────

    def get_alert_state(self) -> AlertState:
        def _load() -> AlertState:
            data = self._read_json(self._snap("latest_alert_state.json"))
            if not data:
                return _EMPTY_ALERT_STATE
            active = [
                AlertEntry(
                    ts=a.get("last_seen", ""),
                    severity=a.get("severity", ""),
                    component=a.get("component", ""),
                    reason=a.get("reason", ""),
                    message=self._scrub(a.get("message", "")),
                )
                for a in data.get("active_alerts", [])
            ]
            return AlertState(
                written_at=data.get("written_at"),
                uptime_pct=data.get("uptime_pct", 0.0),
                active_alerts=active,
                alert_counts=data.get("alert_counts", {}),
                wd_free_gb=data.get("wd_free_gb", 0.0),
                latest_external_heartbeat_at=data.get("latest_external_heartbeat_at"),
                latest_external_heartbeat_status=data.get("latest_external_heartbeat_status"),
            )

        result = self._cached("alert_state", _TTL_LIVE, _load)
        return result if result is not None else _EMPTY_ALERT_STATE

    def get_alert_history(self, session_date: date) -> list[AlertEntry]:
        date_str = session_date.strftime("%Y%m%d")

        def _load() -> list[AlertEntry]:
            path = self._root / "alerts" / f"{date_str}_alerts.jsonl"
            if not path.exists():
                return []
            entries: list[AlertEntry] = []
            try:
                for line in path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        entries.append(AlertEntry(
                            ts=rec.get("ts", ""),
                            severity=rec.get("severity", ""),
                            component=rec.get("component", ""),
                            reason=rec.get("reason", ""),
                            message=self._scrub(rec.get("message", "")),
                        ))
                    except json.JSONDecodeError:
                        continue
            except OSError:
                pass
            return entries

        result = self._cached(f"alert_history_{date_str}", _TTL_TRADES, _load)
        return result if result is not None else []

    # ── Spot chart bars ──────────────────────────────────────────────────────

    def get_spot_bars(self, symbol: str, days: int = 1) -> list[dict]:
        """
        Return 1-min OHLCV bars for the last `days` trading sessions.

        During market hours, today's bars come from latest_spot_bars.json (written every
        10 s by the engine) so the chart is live.  Prior sessions always come from the
        static CSV.  Timestamps are "display epoch": IST naive treated as UTC so that
        LightweightCharts shows 09:15 not 03:45.
        """
        path = _SPOT_FILES.get(symbol.upper())
        if not path or not path.exists():
            return []

        # Live JSON uses _TTL_LIVE; historical CSV uses _TTL_HIST.
        # Use a short TTL whenever live data might be present.
        from datetime import date as _date
        today = _date.today()
        live_path = self._snap("latest_spot_bars.json")
        live_data = self._read_json(live_path) or {}
        has_live = (
            live_data.get("session_date") == today.isoformat()
            and bool(live_data.get("bars", {}).get(symbol.upper()))
        )
        ttl = _TTL_LIVE if has_live else _TTL_HIST
        cache_key = f"spot_{symbol}_{days}"

        def _load() -> list[dict]:
            # ── Live bars from today's engine snapshot ─────────────────────
            sym_up = symbol.upper()
            _live = self._read_json(self._snap("latest_spot_bars.json")) or {}
            _live_bars: list[dict] = []
            if _live.get("session_date") == today.isoformat():
                _live_bars = _live.get("bars", {}).get(sym_up, [])

            # ── CSV for prior sessions ─────────────────────────────────────
            with open(path, encoding="utf-8") as _f:
                _hdr = _f.readline().strip().split(",")
            ts_col = "datetime" if "datetime" in _hdr else "timestamp"
            df = pd.read_csv(
                path,
                usecols=[ts_col, "open", "high", "low", "close"],
                dtype={"open": "float32", "high": "float32",
                       "low": "float32", "close": "float32"},
            )
            df.rename(columns={ts_col: "datetime"}, inplace=True)
            df["datetime"] = pd.to_datetime(df["datetime"])
            df.sort_values("datetime", inplace=True)

            # Exclude today from CSV when live JSON has today's bars
            if _live_bars:
                df = df[df["datetime"].dt.date != today]

            session_dates = sorted(df["datetime"].dt.date.unique())
            csv_sessions_needed = max(days - (1 if _live_bars else 0), 0)

            if csv_sessions_needed > 0 and session_dates:
                target = set(session_dates[-csv_sessions_needed:])
                df = df[df["datetime"].dt.date.isin(target)]
            else:
                df = df.iloc[0:0]

            df["time"] = (
                (df["datetime"] - pd.Timestamp("1970-01-01"))
                // pd.Timedelta("1s")
            )
            hist_bars = [
                {
                    "time":  int(r.time),
                    "open":  round(float(r.open),  2),
                    "high":  round(float(r.high),  2),
                    "low":   round(float(r.low),   2),
                    "close": round(float(r.close), 2),
                }
                for r in df.itertuples(index=False)
            ]
            return hist_bars + _live_bars

        result = self._cached(cache_key, ttl, _load)
        return result if result is not None else []

    # ── Historical data (v2 scope — stubs) ──────────────────────────────────

    def historical_order_book(
        self,
        session_date: date,
        symbol: str,
        expiry: date,
        strike: int,
        opt_type: str,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    # ── Backtest data (v2 scope — stubs) ─────────────────────────────────────

    # v2 historical/backtest implementation. These later definitions replace
    # the narrow stubs above while keeping this block easy to remove if needed.

    @staticmethod
    def _path_source(path: Path) -> str:
        try:
            return str(path.relative_to(_REPO_ROOT))
        except ValueError:
            return str(path)

    @staticmethod
    def _iso_ist(ts: Any) -> str:
        stamp = pd.Timestamp(ts)
        if stamp.tzinfo is None:
            stamp = stamp.tz_localize(_IST_NAME)
        else:
            stamp = stamp.tz_convert(_IST_NAME)
        return stamp.isoformat()

    @staticmethod
    def _date_filter(df: pd.DataFrame, start: date, end: date) -> pd.DataFrame:
        if df.empty:
            return df
        return df.loc[(df.index.date >= start) & (df.index.date <= end)]

    @staticmethod
    def _cap_rows(df: pd.DataFrame, limit: int = _HIST_ROW_LIMIT) -> pd.DataFrame:
        truncated = len(df) > limit
        out = df.iloc[:limit].copy() if truncated else df.copy()
        out.attrs.update(df.attrs)
        out.attrs["truncated"] = truncated
        return out

    @staticmethod
    def _atm_sort_key(stem: str) -> int:
        if stem == "ATM":
            return 0
        if stem.startswith("ATMm"):
            return -int(stem.removeprefix("ATMm"))
        if stem.startswith("ATMp"):
            return int(stem.removeprefix("ATMp"))
        return 999

    @staticmethod
    def _numeric(value: Any) -> float | None:
        try:
            if value is None or pd.isna(value):
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _safe_slug(text: str) -> str:
        slug = re.sub(r"[^A-Za-z0-9_\-]+", "_", text).strip("_").lower()
        return slug[:80] or "backtest"

    @staticmethod
    def _symbol_from_name(name: str) -> str:
        upper = name.upper()
        for sym in ("BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX", "NIFTY"):
            if sym in upper:
                return sym
        return ""

    @staticmethod
    def _run_key_from_path(path: Path) -> str:
        stem = path.stem
        match = re.match(r"^(20\d{6}(?:_\d{6})?)", stem)
        if match:
            return match.group(1)
        parts = [p for p in path.parts if p not in {"options", "legacy"}]
        if len(parts) >= 2:
            return DashboardBridge._safe_slug(parts[-2])
        return DashboardBridge._safe_slug(stem)

    @staticmethod
    def _strategy_family_from_text(*values: Any) -> str:
        text = " ".join(str(v or "") for v in values).lower().replace("-", "_")
        checks = [
            ("iron_condor", ("iron_condor", "ic_", "_ic", "wing")),
            ("short_strangle", ("short_strangle", "strangle")),
            ("short_straddle", ("short_straddle", "straddle")),
            ("three_pm", ("three_pm", "3pm", "three pm")),
            ("risk_management", ("risk_mgmt", "risk_management", "kelly", "cvar")),
            ("focused_contributors", ("focused_contributors", "focused")),
            ("portfolio", ("portfolio", "combined")),
            ("monitoring", ("monitoring", "phase")),
            ("validation", ("validation", "audit")),
        ]
        for family, needles in checks:
            if any(needle in text for needle in needles):
                return family
        return "misc"

    @staticmethod
    def _display_family(family: str) -> str:
        return family.replace("_", " ").title()

    def _attach_backtest_group(self, row: dict, source_path: Path | None = None) -> dict:
        path = source_path or Path(str(row.get("_path", row.get("source", ""))))
        run_key = str(row.get("run_key") or self._run_key_from_path(path))
        family = str(row.get("strategy_family") or self._strategy_family_from_text(
            row.get("strategy"), row.get("name"), path.as_posix()
        ))
        group_key = f"{family}/{run_key}"
        out = dict(row)
        out["run_key"] = run_key
        out["strategy_family"] = family
        out["group_key"] = group_key
        out["group_path"] = group_key
        out["group_label"] = f"{self._display_family(family)} / {run_key}"
        return out

    @staticmethod
    def _sort_backtests(rows: list[dict], sort_by: str = "date", sort_dir: str = "desc") -> list[dict]:
        reverse = sort_dir.lower() != "asc"
        field_map = {
            "date": "created_at",
            "created_at": "created_at",
            "start_date": "start_date",
            "end_date": "end_date",
            "name": "name",
            "group": "group_path",
            "strategy": "strategy_family",
            "symbol": "symbol",
            "net_pnl": "net_pnl",
            "sharpe": "sharpe",
            "sortino": "sortino",
            "calmar": "calmar",
            "max_dd_pct": "max_dd_pct",
            "trades": "trades",
            "win_rate": "win_rate",
            "profit_factor": "profit_factor",
            "t_stat": "t_stat",
        }
        field = field_map.get(sort_by, "created_at")

        numeric_fields = {
            "net_pnl", "sharpe", "sortino", "calmar", "max_dd_pct", "trades",
            "win_rate", "profit_factor", "t_stat",
        }

        def _key(row: dict) -> tuple[Any, str]:
            value = row.get(field)
            if field in numeric_fields:
                num = DashboardBridge._numeric(value)
                return (num if num is not None else 0.0, str(row.get("name", "")))
            if field in {"created_at", "start_date", "end_date"}:
                text = str(value or row.get("end_date") or row.get("start_date") or "")
                return (text, str(row.get("name", "")))
            text = str(value or "").lower()
            return (text, str(row.get("name", "")).lower())

        valid = [row for row in rows if row.get(field) not in (None, "")]
        missing = [row for row in rows if row.get(field) in (None, "")]
        return sorted(valid, key=_key, reverse=reverse) + sorted(missing, key=lambda r: str(r.get("name", "")).lower())

    @staticmethod
    def _first_date(df: pd.DataFrame, candidates: list[str]) -> str | None:
        for col in candidates:
            if col in df.columns:
                s = pd.to_datetime(df[col], errors="coerce").dropna()
                if not s.empty:
                    return pd.Timestamp(s.min()).date().isoformat()
        return None

    @staticmethod
    def _last_date(df: pd.DataFrame, candidates: list[str]) -> str | None:
        for col in candidates:
            if col in df.columns:
                s = pd.to_datetime(df[col], errors="coerce").dropna()
                if not s.empty:
                    return pd.Timestamp(s.max()).date().isoformat()
        return None

    @staticmethod
    def _read_ledger_summary_frame(path: Path) -> pd.DataFrame:
        keep = {
            "symbol", "strategy", "entry_date", "entry_time", "exit_date", "exit_time",
            "gross_pnl", "net_pnl",
        }

        def _use_column(col: str) -> bool:
            return str(col).strip().lower() in keep

        try:
            return pd.read_csv(path, usecols=_use_column)
        except ValueError:
            return pd.read_csv(path)

    @staticmethod
    def _ledger_time_series(df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "net_pnl" not in df.columns:
            return pd.DataFrame(columns=["ts", "net_pnl"])

        out = df.copy()
        source_col = None
        if "exit_time" in out.columns:
            parsed = pd.to_datetime(out["exit_time"], errors="coerce")
            if parsed.notna().any():
                source_col = "exit_time"
                out["_ts"] = parsed
        if source_col is None and "exit_date" in out.columns:
            out["_ts"] = pd.to_datetime(out["exit_date"], errors="coerce")
        elif source_col is None:
            return pd.DataFrame(columns=["ts", "net_pnl"])

        out["net_pnl"] = pd.to_numeric(out["net_pnl"], errors="coerce").fillna(0)
        out = out.dropna(subset=["_ts"])
        if out.empty:
            return pd.DataFrame(columns=["ts", "net_pnl"])

        if source_col != "exit_time":
            out["_ts"] = out["_ts"].dt.normalize()
        grouped = out.groupby("_ts", as_index=False)["net_pnl"].sum().sort_values("_ts")
        return grouped.rename(columns={"_ts": "ts"}).reset_index(drop=True)

    @staticmethod
    def _as_ist_index(values: Any) -> pd.DatetimeIndex:
        raw = pd.to_datetime(values, errors="coerce")
        mask = ~pd.isna(raw)
        idx = pd.DatetimeIndex(raw[mask])
        if idx.tz is None:
            return idx.tz_localize(_IST_NAME)
        return idx.tz_convert(_IST_NAME)

    def _read_ohlcv_csv(self, path: Path, start: date, end: date) -> pd.DataFrame:
        if not path.exists():
            return pd.DataFrame()
        df = pd.read_csv(path)
        df.columns = [str(c).strip().lower() for c in df.columns]
        ts_col = "datetime" if "datetime" in df.columns else "timestamp"
        if ts_col not in df.columns:
            return pd.DataFrame()
        parsed = pd.to_datetime(df[ts_col], errors="coerce")
        mask = ~pd.isna(parsed)
        df = df.loc[mask].copy()
        idx = pd.DatetimeIndex(parsed.loc[mask])
        df.index = idx.tz_localize(_IST_NAME) if idx.tz is None else idx.tz_convert(_IST_NAME)
        for col in ("open", "high", "low", "close"):
            if col not in df.columns:
                df[col] = df["close"] if "close" in df.columns else 0.0
            df[col] = pd.to_numeric(df[col], errors="coerce")
        if "volume" not in df.columns:
            df["volume"] = 0
        df["volume"] = pd.to_numeric(df["volume"], errors="coerce").fillna(0).astype("int64")
        df = df[["open", "high", "low", "close", "volume"]].dropna(subset=["close"]).sort_index()
        return self._date_filter(df, start, end)

    def _resample_ohlcv(self, df: pd.DataFrame, tf_minutes: int) -> pd.DataFrame:
        if df.empty or tf_minutes <= 1:
            return df
        agg: dict[str, str] = {
            "open": "first",
            "high": "max",
            "low": "min",
            "close": "last",
            "volume": "sum",
        }
        for col in ("oi", "iv_clean", "strike", "spot"):
            if col in df.columns:
                agg[col] = "last"
        out = df.resample(f"{tf_minutes}min").agg(agg).dropna(subset=["open", "high", "low", "close"])
        out.attrs.update(df.attrs)
        return out

    def _format_ohlcv(self, df: pd.DataFrame, extras: list[str] | None = None) -> pd.DataFrame:
        extras = extras or []
        if df.empty:
            out = pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume", *extras])
            out.attrs.update(df.attrs)
            return out
        out = df.copy()
        out.insert(0, "ts", [self._iso_ist(ts) for ts in out.index])
        cols = ["ts", "open", "high", "low", "close", "volume", *[c for c in extras if c in out.columns]]
        out = out[cols].reset_index(drop=True)
        out.attrs.update(df.attrs)
        return out

    def historical_spot(self, symbol: str, start: date, end: date, tf_minutes: int = 1) -> pd.DataFrame:
        sym = symbol.upper()
        path = _SPOT_FILES.get(sym)
        if not path:
            return pd.DataFrame()
        cache_key = f"hist_spot_{sym}_{start}_{end}_{tf_minutes}"

        def _load() -> pd.DataFrame:
            df = self._read_ohlcv_csv(path, start, end)
            df = self._resample_ohlcv(df, max(int(tf_minutes), 1))
            df.attrs["source"] = self._path_source(path)
            return self._format_ohlcv(self._cap_rows(df))

        result = self._cached(cache_key, _TTL_HIST, _load)
        return result if result is not None else pd.DataFrame()

    def historical_options_metadata(self, symbol: str) -> dict:
        sym = symbol.upper()
        root = _OPTIONS_ROOT / sym.lower()
        cache_key = f"hist_options_meta_{sym}"

        def _load() -> dict:
            if not root.exists():
                return {}
            files = sorted(root.rglob("*.parquet"))
            if not files:
                return {}
            expiry_types = sorted({p.relative_to(root).parts[0] for p in files if len(p.relative_to(root).parts) >= 4})
            offsets = sorted({p.stem for p in files}, key=self._atm_sort_key)
            opt_types = sorted({p.parent.name for p in files if p.parent.name in {"call", "put"}})
            sample_files = [
                p for p in files
                if p.stem == "ATM" and p.parent.name == "call"
            ] or files[:1]
            ts_parts = []
            for sample_path in sample_files:
                sample = pd.read_parquet(sample_path, columns=["timestamp"])
                ts_parts.append(pd.to_datetime(sample["timestamp"], errors="coerce").dropna())
            ts = pd.concat(ts_parts, ignore_index=True) if ts_parts else pd.Series(dtype="datetime64[ns]")
            return {
                "symbol": sym,
                "date_min": pd.Timestamp(ts.min()).date().isoformat() if not ts.empty else None,
                "date_max": pd.Timestamp(ts.max()).date().isoformat() if not ts.empty else None,
                "expiry_types": expiry_types,
                "atm_offsets": offsets,
                "opt_types": opt_types or ["call", "put"],
                "source": self._path_source(root),
            }

        result = self._cached(cache_key, 300.0, _load)
        return result if result is not None else {}

    def historical_options(
        self,
        symbol: str,
        expiry_type: str,
        atm_offset: str,
        opt_type: str,
        start: date,
        end: date,
    ) -> pd.DataFrame:
        sym = symbol.upper()
        exp = expiry_type.lower()
        side = opt_type.lower()
        if side in {"ce", "c"}:
            side = "call"
        elif side in {"pe", "p"}:
            side = "put"
        path = _OPTIONS_ROOT / sym.lower() / exp / "expiry_code_1" / side / f"{atm_offset}.parquet"
        cache_key = f"hist_options_{sym}_{exp}_{atm_offset}_{side}_{start}_{end}"

        def _load() -> pd.DataFrame:
            if not path.exists():
                return pd.DataFrame()
            cols = ["timestamp", "open", "high", "low", "close", "volume", "oi", "iv_clean", "strike", "spot"]
            df = pd.read_parquet(path, columns=cols)
            df.index = self._as_ist_index(df["timestamp"])
            df = df.drop(columns=["timestamp"]).sort_index()
            df = self._date_filter(df, start, end)
            df.attrs["source"] = self._path_source(path)
            return self._format_ohlcv(self._cap_rows(df), extras=["oi", "iv_clean", "strike", "spot"])

        result = self._cached(cache_key, _TTL_HIST, _load)
        return result if result is not None else pd.DataFrame()

    def historical_vix(self, start: date, end: date, tf_minutes: int = 1) -> pd.DataFrame:
        cache_key = f"hist_vix_{start}_{end}_{tf_minutes}"

        def _load() -> pd.DataFrame:
            chosen = next((p for p in _VIX_FILES if p.exists()), None)
            if chosen is None:
                return pd.DataFrame()
            df = self._read_ohlcv_csv(chosen, start, end)
            if "close" not in df.columns:
                return pd.DataFrame()
            close = pd.to_numeric(df["close"], errors="coerce")
            df = pd.DataFrame({
                "open": close,
                "high": close,
                "low": close,
                "close": close,
                "volume": 0,
            }, index=df.index).dropna(subset=["close"])
            df = self._resample_ohlcv(df, max(int(tf_minutes), 1))
            df.attrs["source"] = self._path_source(chosen)
            df.attrs["warnings"] = ["daily_vix_source"] if chosen.name == "INDIA VIX_day.csv" else []
            return self._format_ohlcv(self._cap_rows(df))

        result = self._cached(cache_key, _TTL_HIST, _load)
        return result if result is not None else pd.DataFrame()

    def historical_bhavcopy(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        sym = symbol.upper()
        cache_key = f"hist_bhavcopy_{sym}_{start}_{end}"

        def _load() -> pd.DataFrame:
            if not _BHAVCOPY_ROOT.exists():
                return pd.DataFrame()
            frames = []
            for year in range(start.year, end.year + 1):
                path = _BHAVCOPY_ROOT / f"nifty_options_eod_{year}.parquet"
                if path.exists():
                    frames.append(pd.read_parquet(path))
            if not frames:
                return pd.DataFrame()
            df = pd.concat(frames, ignore_index=True)
            df.columns = [str(c).strip().lower() for c in df.columns]
            if "symbol" in df.columns:
                df = df[df["symbol"].astype(str).str.upper() == sym]
            date_col = next((c for c in ("timestamp", "datetime", "date", "trad_dt", "trade_date") if c in df.columns), None)
            if date_col is None:
                return pd.DataFrame()
            dt = pd.to_datetime(df[date_col], errors="coerce")
            df = df.loc[(dt.dt.date >= start) & (dt.dt.date <= end)].copy()
            if df.empty:
                return pd.DataFrame()
            def _col(*names: str, default: Any = 0) -> pd.Series:
                for name in names:
                    if name in df.columns:
                        return df[name]
                return pd.Series(default, index=df.index)
            out = pd.DataFrame({
                "ts": [pd.Timestamp(x).date().isoformat() for x in pd.to_datetime(df[date_col], errors="coerce")],
                "expiry": _col("expiry_dt", "expiry", "expiry_date", default=""),
                "strike": pd.to_numeric(_col("strike_pr", "strike"), errors="coerce"),
                "option_type": _col("option_typ", "option_type", default=""),
                "open": pd.to_numeric(_col("open"), errors="coerce"),
                "high": pd.to_numeric(_col("high"), errors="coerce"),
                "low": pd.to_numeric(_col("low"), errors="coerce"),
                "close": pd.to_numeric(_col("close", "close_pr"), errors="coerce"),
                "settle_price": pd.to_numeric(_col("settle_pr", "settle_price"), errors="coerce"),
                "oi": pd.to_numeric(_col("open_int", "open_interest", "oi"), errors="coerce").fillna(0).astype("int64"),
                "volume": pd.to_numeric(_col("contracts", "volume"), errors="coerce").fillna(0).astype("int64"),
            })
            out.attrs["source"] = self._path_source(_BHAVCOPY_ROOT)
            return self._cap_rows(out)

        result = self._cached(cache_key, _TTL_HIST, _load)
        return result if result is not None else pd.DataFrame()

    def _legacy_id(self, path: Path, row_index: int | None = None) -> str:
        try:
            rel = path.relative_to(_REPORT_ROOT)
        except ValueError:
            rel = path
        suffix = "" if row_index is None else f"_{row_index}"
        digest = hashlib.sha1(f"{rel.as_posix()}{suffix}".encode("utf-8")).hexdigest()[:12]
        return f"legacy_{digest}_{self._safe_slug(path.stem)}"

    def _is_ledger_frame(self, df: pd.DataFrame) -> bool:
        cols = {str(c).lower() for c in df.columns}
        return "net_pnl" in cols and bool(cols & {"entry_date", "entry_time", "exit_date", "exit_time"})

    def _ledger_summary(self, df: pd.DataFrame, source_path: Path, backtest_id: str, source_kind: str) -> dict:
        if df.empty:
            return {}
        net = pd.to_numeric(df.get("net_pnl", 0), errors="coerce").fillna(0)
        gross = pd.to_numeric(df.get("gross_pnl", 0), errors="coerce").fillna(0)
        wins = net[net > 0]
        losses = net[net < 0]
        pf = float(wins.sum() / abs(losses.sum())) if abs(losses.sum()) > 0 else None
        equity = 1_000_000 + net.cumsum()
        dd = (equity - equity.cummax()) / equity.cummax() * 100
        by_day = pd.DataFrame({"net_pnl": net, "exit_date": pd.to_datetime(df.get("exit_date", df.get("exit_time")), errors="coerce")})
        daily = by_day.dropna(subset=["exit_date"]).groupby(by_day["exit_date"].dt.date)["net_pnl"].sum()
        sharpe = None
        if len(daily) > 1 and daily.std(ddof=1) != 0:
            sharpe = float((daily.mean() / daily.std(ddof=1)) * (252 ** 0.5))
        first = df.iloc[0]
        symbol = str(first.get("symbol", self._symbol_from_name(source_path.stem)))
        return {
            "id": backtest_id,
            "name": source_path.stem,
            "strategy": str(first.get("strategy", source_path.stem)),
            "symbol": symbol.upper() if symbol else "",
            "start_date": self._first_date(df, ["entry_date", "entry_time"]) or "",
            "end_date": self._last_date(df, ["exit_date", "exit_time"]) or "",
            "trades": int(len(df)),
            "net_pnl": float(net.sum()),
            "gross_pnl": float(gross.sum()),
            "sharpe": sharpe,
            "max_dd_pct": float(dd.min()) if len(dd) else None,
            "profit_factor": pf,
            "win_rate": float((net > 0).mean() * 100) if len(net) else None,
            "created_at": datetime.fromtimestamp(source_path.stat().st_mtime, tz=_IST).isoformat(),
            "source_kind": source_kind,
            "source": self._path_source(source_path),
            "has_ledger": True,
            "has_equity": True,
            "has_decisions": False,
            "_path": str(source_path),
        }

    @staticmethod
    def _jsonable(value: Any) -> Any:
        if value is None:
            return None
        if pd.isna(value):
            return None
        if hasattr(value, "item"):
            return value.item()
        return value

    def _row_summary(self, row: pd.Series, source_path: Path, backtest_id: str, row_index: int) -> dict:
        name = str(row.get("name", row.get("label", source_path.stem)))
        summary_row = {str(k): self._jsonable(v) for k, v in row.to_dict().items()}
        has_reconstructable_ledger = bool(self._legacy_portfolio_components(summary_row, source_path))
        return {
            "id": backtest_id,
            "name": name,
            "strategy": str(row.get("strategy", row.get("source", source_path.stem))),
            "symbol": str(row.get("symbol", row.get("source", ""))).upper(),
            "start_date": str(row.get("start_date", "")),
            "end_date": str(row.get("end_date", "")),
            "trades": int(self._numeric(row.get("trades")) or 0),
            "net_pnl": float(self._numeric(row.get("net_pnl")) or 0.0),
            "cagr": self._numeric(row.get("cagr")),
            "sharpe": self._numeric(row.get("sharpe")),
            "sortino": self._numeric(row.get("sortino")),
            "calmar": self._numeric(row.get("calmar")),
            "max_dd_pct": self._numeric(row.get("max_dd_pct", row.get("max_drawdown_pct"))),
            "profit_factor": self._numeric(row.get("pf", row.get("profit_factor"))),
            "win_rate": self._numeric(row.get("win_rate")),
            "t_stat": self._numeric(row.get("t_stat")),
            "created_at": datetime.fromtimestamp(source_path.stat().st_mtime, tz=_IST).isoformat(),
            "source_kind": "legacy_summary",
            "source": self._path_source(source_path),
            "has_ledger": has_reconstructable_ledger,
            "has_equity": has_reconstructable_ledger,
            "has_decisions": False,
            "_path": str(source_path),
            "_row_index": row_index,
            "_summary_row": summary_row,
        }

    def _canonical_summary(self, path: Path) -> dict:
        data = json.loads(path.read_text(encoding="utf-8"))
        backtest_id = path.name.removesuffix("_summary.json")
        data.setdefault("id", backtest_id)
        data.setdefault("name", backtest_id)
        data.setdefault("source_kind", "canonical")
        data.setdefault("source", self._path_source(path))
        data.setdefault("created_at", datetime.fromtimestamp(path.stat().st_mtime, tz=_IST).isoformat())
        data.setdefault("has_ledger", (_BACKTEST_ROOT / f"{backtest_id}_ledger.csv").exists())
        data.setdefault("has_equity", data["has_ledger"])
        data.setdefault("has_decisions", (_BACKTEST_ROOT / f"{backtest_id}_decisions.csv").exists())
        return self._attach_backtest_group(data, path)

    def _backtest_source_manifest(self) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        if _BACKTEST_ROOT.exists():
            for path in sorted(_BACKTEST_ROOT.glob("*_summary.json")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                items.append({
                    "kind": "canonical",
                    "path": self._path_source(path),
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                })
        for root in _LEGACY_BACKTEST_ROOTS:
            if not root.exists():
                continue
            for path in sorted(root.rglob("*.csv")):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                items.append({
                    "kind": "legacy_csv",
                    "path": self._path_source(path),
                    "mtime_ns": stat.st_mtime_ns,
                    "size": stat.st_size,
                })
        return items

    def _read_persistent_backtest_index(self, manifest: list[dict[str, Any]]) -> dict[str, dict] | None:
        if not _BACKTEST_INDEX_PATH.exists():
            return None
        try:
            data = json.loads(_BACKTEST_INDEX_PATH.read_text(encoding="utf-8"))
        except Exception:
            return None
        if data.get("version") != _BACKTEST_INDEX_VERSION:
            return None
        if data.get("manifest") != manifest:
            return None
        entries = data.get("entries")
        if not isinstance(entries, dict):
            return None
        return {str(k): v for k, v in entries.items() if isinstance(v, dict)}

    def _write_persistent_backtest_index(self, manifest: list[dict[str, Any]], entries: dict[str, dict]) -> None:
        payload = {
            "version": _BACKTEST_INDEX_VERSION,
            "generated_at": datetime.now(tz=_IST).isoformat(),
            "manifest": manifest,
            "entries": entries,
        }
        try:
            _BACKTEST_INDEX_ROOT.mkdir(parents=True, exist_ok=True)
            tmp = _BACKTEST_INDEX_PATH.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            tmp.replace(_BACKTEST_INDEX_PATH)
        except OSError:
            pass

    def _legacy_entries_from_paths(self, paths: list[Path]) -> dict[str, dict]:
        entries: dict[str, dict] = {}

        def _read_path(path: Path) -> dict[str, dict]:
            found: dict[str, dict] = {}
            try:
                df = pd.read_csv(path, nrows=500)
            except Exception:
                return found
            df.columns = [str(c).strip() for c in df.columns]
            if self._is_ledger_frame(df):
                bid = self._legacy_id(path)
                try:
                    full_df = self._read_ledger_summary_frame(path)
                except Exception:
                    return found
                summary = self._ledger_summary(full_df, path, bid, "legacy_ledger")
                if summary:
                    found[bid] = self._attach_backtest_group(summary, path)
                return found
            cols = {str(c).lower() for c in df.columns}
            if "net_pnl" not in cols and "sharpe" not in cols and "trades" not in cols:
                return found
            for i, row in df.head(200).iterrows():
                bid = self._legacy_id(path, int(i))
                found[bid] = self._attach_backtest_group(self._row_summary(row, path, bid, int(i)), path)
            return found

        if not paths:
            return entries
        workers = min(8, max(1, len(paths)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_read_path, path) for path in paths]
            for future in as_completed(futures):
                try:
                    entries.update(future.result())
                except Exception:
                    continue
        return entries

    def _backtest_index(self) -> dict[str, dict]:
        def _load() -> dict[str, dict]:
            manifest = self._backtest_source_manifest()
            cached = self._read_persistent_backtest_index(manifest)
            if cached is not None:
                return cached

            entries: dict[str, dict] = {}
            legacy_paths: list[Path] = []
            if _BACKTEST_ROOT.exists():
                for path in sorted(_BACKTEST_ROOT.glob("*_summary.json")):
                    try:
                        summary = self._canonical_summary(path)
                    except Exception:
                        continue
                    entries[str(summary.get("id", path.stem))] = summary
            for root in _LEGACY_BACKTEST_ROOTS:
                if not root.exists():
                    continue
                legacy_paths.extend(sorted(root.rglob("*.csv")))
            entries.update(self._legacy_entries_from_paths(legacy_paths))
            self._write_persistent_backtest_index(manifest, entries)
            return entries

        result = self._cached("backtest_index", _TTL_BACKTESTS, _load)
        return result if result is not None else {}

    def _legacy_index(self) -> dict[str, dict]:
        return {
            key: row for key, row in self._backtest_index().items()
            if str(row.get("source_kind", "")).startswith("legacy")
        }

    def backtest_list(self, sort_by: str = "date", sort_dir: str = "desc") -> list[dict]:
        def _load() -> list[dict]:
            return list(self._backtest_index().values())

        result = self._cached("backtest_list", _TTL_BACKTESTS, _load)
        rows = result if result is not None else []
        return self._sort_backtests(list(rows), sort_by, sort_dir)

    def backtest_summaries(self, root: Path | None = None) -> list:
        return self.backtest_list()

    def backtest_summary(self, backtest_id: str) -> dict:
        return self._backtest_index().get(backtest_id, {})

    def _normalise_ledger(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty:
            return df
        df = df.copy()
        df.columns = [str(c).strip() for c in df.columns]
        rename = {"dte_at_entry": "dte", "vix_entry": "vix", "entry_credit": "entry_premium"}
        df.rename(columns={k: v for k, v in rename.items() if k in df.columns}, inplace=True)
        if "entry_date" not in df.columns and "entry_time" in df.columns:
            df["entry_date"] = pd.to_datetime(df["entry_time"], errors="coerce").dt.date.astype(str)
        if "exit_date" not in df.columns and "exit_time" in df.columns:
            df["exit_date"] = pd.to_datetime(df["exit_time"], errors="coerce").dt.date.astype(str)
        if "symbol" not in df.columns:
            df["symbol"] = ""
        for col in ("gross_pnl", "charges", "net_pnl", "equity", "dte", "vix"):
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")
        preferred = [
            "symbol", "strategy", "expiry", "entry_date", "entry_time", "exit_date", "exit_time",
            "entry_reason", "exit_reason", "dte", "vix", "vix_bucket", "spot_entry", "spot_exit",
            "entry_legs", "exit_legs", "legs", "gross_pnl", "charges", "net_pnl", "equity",
            "entry_premium", "max_theoretical_loss",
        ]
        cols = [c for c in preferred if c in df.columns] + [c for c in df.columns if c not in preferred]
        return df[cols]

    @staticmethod
    def _parse_lot_spec(value: Any) -> dict[str, float]:
        lots: dict[str, float] = {}
        for token in str(value or "").replace(",", " ").split():
            if ":" not in token:
                continue
            symbol, raw_lots = token.split(":", 1)
            symbol = symbol.strip().upper()
            try:
                qty = float(raw_lots)
            except ValueError:
                continue
            if symbol and qty > 0:
                lots[symbol] = qty
        return lots

    @staticmethod
    def _portfolio_component_symbol_path(path: Path, symbol: str, pattern: str) -> Path:
        return path.parent / pattern.format(symbol=symbol.lower())

    def _legacy_portfolio_components(self, row: dict[str, Any], source_path: Path) -> list[dict[str, Any]]:
        lots = self._parse_lot_spec(row.get("lots"))
        if not lots:
            return []

        source = str(row.get("source", "")).strip().lower()
        stem = source_path.stem
        components: list[dict[str, Any]] = []

        def _add(symbol: str, path: Path, scale: float) -> None:
            if path.exists():
                components.append({"symbol": symbol, "path": str(path), "scale": float(scale)})

        if stem == "20260507_optimized_wing6_portfolio":
            baseline = {
                "NIFTY": "20260506_mixed_expiry_ic_wing6_nifty.csv",
                "BANKNIFTY": "20260506_mixed_expiry_ic_wing6_banknifty.csv",
                "FINNIFTY": "20260506_mixed_expiry_ic_wing6_finnifty.csv",
                "MIDCPNIFTY": "20260506_mixed_expiry_ic_wing6_midcpnifty.csv",
                "SENSEX": "20260506_mixed_expiry_ic_wing6_sensex.csv",
            }
            filtered = {
                "NIFTY": "20260506_vixgt13_ic_wing6_nifty.csv",
                "FINNIFTY": "20260506_dtelt7_ic_wing6_finnifty.csv",
                "MIDCPNIFTY": "20260506_dtelt7_ic_wing6_midcpnifty.csv",
                "SENSEX": "20260506_dtelt2_ic_wing6_sensex.csv",
            }
            mapping = baseline if source == "baseline" else filtered if source == "filtered" else {}
            for symbol, scale in lots.items():
                if symbol in mapping:
                    _add(symbol, source_path.parent / mapping[symbol], scale)
            return components if len(components) == len(lots) else []

        if not stem.endswith("_portfolio_comparison"):
            return []

        run_prefix = stem.removesuffix("_portfolio_comparison")
        if source == "strangle":
            focused_root = _REPORT_ROOT / "options" / "focused"
            source_stamp = "20260505_024624"
            for symbol, scale in lots.items():
                exact = focused_root / f"{source_stamp}_dhan_{symbol.lower()}_x{int(scale)}_short_strangle.csv"
                if float(scale).is_integer() and exact.exists():
                    _add(symbol, exact, 1.0)
                    continue
                base = focused_root / f"{source_stamp}_dhan_{symbol.lower()}_x1_short_strangle.csv"
                if not base.exists():
                    base = focused_root / f"{source_stamp}_dhan_{symbol.lower()}_short_strangle.csv"
                _add(symbol, base, scale)
            return components if len(components) == len(lots) else []

        wing = re.match(r"iron_condor_wing(\d+)", source)
        if wing:
            pattern = f"{run_prefix}_ic_wing{wing.group(1)}_{{symbol}}.csv"
            for symbol, scale in lots.items():
                _add(symbol, self._portfolio_component_symbol_path(source_path, symbol, pattern), scale)
            return components if len(components) == len(lots) else []

        spread = re.match(r"credit_spread_(put|call)_(\d+)x(\d+)", source)
        if spread:
            side, short_offset, long_offset = spread.groups()
            pattern = f"{run_prefix}_cs_{side}_s{short_offset}_l{long_offset}_{{symbol}}.csv"
            for symbol, scale in lots.items():
                _add(symbol, self._portfolio_component_symbol_path(source_path, symbol, pattern), scale)
            return components if len(components) == len(lots) else []

        return []

    def _vix_filter(self):
        def _load():
            chosen = next((p for p in _VIX_FILES if p.exists()), None)
            if chosen is None:
                return None
            try:
                from .volatility_filter import VixFilter
                return VixFilter(chosen, missing_policy="allow")
            except Exception:
                return None

        return self._cached("backtest_vix_filter", 300.0, _load)

    def _annotate_vix_if_needed(self, df: pd.DataFrame) -> pd.DataFrame:
        if df.empty or "vix_bucket" in df.columns or "entry_time" not in df.columns:
            return df
        vix_filter = self._vix_filter()
        if vix_filter is None:
            return df
        out = df.copy()
        values: list[float | None] = []
        buckets: list[str | None] = []
        timestamps: list[Any] = []
        for ts in pd.to_datetime(out["entry_time"], errors="coerce"):
            if pd.isna(ts):
                values.append(None)
                buckets.append(None)
                timestamps.append(None)
                continue
            obs = vix_filter.observation_at_or_before(pd.Timestamp(ts))
            if obs is None:
                values.append(None)
                buckets.append(None)
                timestamps.append(None)
            else:
                values.append(round(float(obs.value), 4))
                buckets.append(obs.bucket)
                timestamps.append(obs.timestamp)
        out["vix_entry"] = values
        out["vix_bucket"] = buckets
        out["vix_timestamp"] = timestamps
        return out

    @staticmethod
    def _apply_legacy_bucket_policy(df: pd.DataFrame, policy: str) -> pd.DataFrame:
        if policy == "all" or df.empty:
            return df.copy()
        out = df.copy()
        if "vix_bucket" not in out.columns:
            return out
        if policy == "skip_10_13":
            return out[out["vix_bucket"] != "10-13"].copy()
        if policy == "half_10_13":
            scale = out["vix_bucket"].eq("10-13").map({True: 0.5, False: 1.0}).astype(float)
            for col in ("gross_pnl", "charges", "net_pnl", "entry_credit", "max_theoretical_loss"):
                if col in out.columns:
                    out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0) * scale
            return out
        if policy == "smart" and "symbol" in out.columns:
            symbol = out["symbol"].astype(str).str.upper()
            return out[
                ~((symbol == "FINNIFTY") & (out["vix_bucket"] == "10-13"))
                & ~((symbol == "MIDCPNIFTY") & (out["vix_bucket"] == "22-30"))
            ].copy()
        return out

    @staticmethod
    def _scale_legacy_lots(df: pd.DataFrame, lots: float) -> pd.DataFrame:
        out = df.copy()
        out["portfolio_lots"] = float(lots)
        if lots == 1:
            return out
        for col in ("gross_pnl", "charges", "net_pnl", "entry_credit", "max_theoretical_loss"):
            if col in out.columns:
                out[col] = pd.to_numeric(out[col], errors="coerce").fillna(0) * lots
        return out

    def _legacy_summary_ledger(self, info: dict[str, Any]) -> pd.DataFrame:
        row = info.get("_summary_row")
        if not isinstance(row, dict):
            return pd.DataFrame()
        source_path = Path(str(info.get("_path", "")))
        components = self._legacy_portfolio_components(row, source_path)
        if not components:
            return pd.DataFrame()

        policy = str(row.get("bucket_policy", "all") or "all")
        portfolio_name = str(row.get("name", row.get("label", source_path.stem)))
        frames: list[pd.DataFrame] = []
        for component in components:
            path = Path(str(component["path"]))
            symbol = str(component["symbol"]).upper()
            try:
                df = pd.read_csv(path)
            except Exception:
                continue
            if df.empty:
                continue
            df.columns = [str(c).strip() for c in df.columns]
            if "strategy" in df.columns:
                df = df[df["strategy"].astype(str).str.upper() != "TOTAL"].copy()
            if "symbol" not in df.columns:
                df.insert(0, "symbol", symbol)
            df = self._annotate_vix_if_needed(df)
            df = self._apply_legacy_bucket_policy(df, policy)
            if df.empty:
                continue
            df = self._scale_legacy_lots(df, float(component["scale"]))
            df["portfolio_name"] = portfolio_name
            frames.append(df)

        if not frames:
            return pd.DataFrame()
        combined = pd.concat(frames, ignore_index=True, sort=False)
        if "exit_time" in combined.columns:
            combined["_sort_ts"] = pd.to_datetime(combined["exit_time"], errors="coerce")
        elif "exit_date" in combined.columns:
            combined["_sort_ts"] = pd.to_datetime(combined["exit_date"], errors="coerce")
        else:
            combined["_sort_ts"] = pd.NaT
        sort_cols = ["_sort_ts"] + (["symbol"] if "symbol" in combined.columns else [])
        combined = combined.sort_values(sort_cols).drop(columns=["_sort_ts"]).reset_index(drop=True)
        if "net_pnl" in combined.columns:
            combined["equity"] = 1_000_000 + pd.to_numeric(combined["net_pnl"], errors="coerce").fillna(0).cumsum()
        return self._normalise_ledger(combined)

    def backtest_ledger(self, backtest_id: str) -> pd.DataFrame:
        def _load() -> pd.DataFrame:
            canonical = _BACKTEST_ROOT / f"{backtest_id}_ledger.csv"
            if canonical.exists():
                try:
                    return self._normalise_ledger(pd.read_csv(canonical))
                except Exception:
                    return pd.DataFrame()
            info = self._legacy_index().get(backtest_id)
            if not info or not info.get("has_ledger"):
                df = pd.DataFrame()
                df.attrs["has_ledger"] = False
                return df
            if str(info.get("source_kind", "")) == "legacy_summary":
                return self._legacy_summary_ledger(info)
            try:
                return self._normalise_ledger(pd.read_csv(info["_path"]))
            except Exception:
                return pd.DataFrame()

        result = self._cached(f"backtest_ledger_{backtest_id}", _TTL_BACKTESTS, _load)
        return result.copy() if result is not None else pd.DataFrame()

    def backtest_equity_curve(self, backtest_id: str) -> pd.DataFrame:
        ledger = self.backtest_ledger(backtest_id)
        if ledger.empty or "net_pnl" not in ledger.columns:
            return pd.DataFrame(columns=["date", "equity", "normalised"])
        pnl_by_time = self._ledger_time_series(ledger)
        if pnl_by_time.empty:
            return pd.DataFrame(columns=["date", "equity", "normalised"])
        equity = 1_000_000 + pnl_by_time["net_pnl"].cumsum()
        return pd.DataFrame({
            "date": pnl_by_time["ts"].map(lambda ts: pd.Timestamp(ts).isoformat()),
            "equity": equity.round(2),
            "normalised": (equity / equity.iloc[0]).round(6),
        }).reset_index(drop=True)

    def backtest_monthly_returns(self, backtest_id: str) -> pd.DataFrame:
        ledger = self.backtest_ledger(backtest_id)
        if ledger.empty or "net_pnl" not in ledger.columns:
            return pd.DataFrame(columns=["year", "month", "net_pnl", "return_pct"])
        pnl_by_time = self._ledger_time_series(ledger)
        if pnl_by_time.empty:
            return pd.DataFrame(columns=["year", "month", "net_pnl", "return_pct"])
        pnl_by_time["year"] = pnl_by_time["ts"].dt.year
        pnl_by_time["month"] = pnl_by_time["ts"].dt.month
        out = pnl_by_time.groupby(["year", "month"], as_index=False)["net_pnl"].sum()
        out["return_pct"] = out["net_pnl"] / 1_000_000 * 100
        return out

    def backtest_drawdown(self, backtest_id: str) -> pd.DataFrame:
        equity = self.backtest_equity_curve(backtest_id)
        if equity.empty:
            return pd.DataFrame(columns=["date", "drawdown_pct"])
        curve = pd.to_numeric(equity["equity"], errors="coerce")
        dd = (curve - curve.cummax()) / curve.cummax() * 100
        return pd.DataFrame({"date": equity["date"], "drawdown_pct": dd.round(4)})

    def backtest_events(self, backtest_id: str) -> list[dict]:
        ledger = self.backtest_ledger(backtest_id)
        if ledger.empty:
            return []
        events: list[dict] = []
        for _, row in ledger.iterrows():
            symbol = str(row.get("symbol", ""))
            entry_ts = row.get("entry_time") or row.get("entry_date")
            exit_ts = row.get("exit_time") or row.get("exit_date")
            if pd.notna(entry_ts):
                events.append({
                    "ts": str(entry_ts),
                    "symbol": symbol,
                    "event_type": "entry",
                    "severity": "info",
                    "label": "ENTRY",
                    "details": str(row.get("entry_reason", "")),
                })
            if pd.notna(exit_ts):
                reason = str(row.get("exit_reason", "exit"))
                severity = "warning" if "stop" in reason.lower() else "info"
                events.append({
                    "ts": str(exit_ts),
                    "symbol": symbol,
                    "event_type": "exit",
                    "severity": severity,
                    "label": reason.upper() if reason else "EXIT",
                    "details": f"net_pnl={row.get('net_pnl', '')}",
                })
        return events

    def backtest_decisions(self, backtest_id: str) -> pd.DataFrame:
        path = _BACKTEST_ROOT / f"{backtest_id}_decisions.csv"
        cols = ["ts", "symbol", "decision", "reason", "vix", "dte", "expiry", "eligible", "selected"]
        if not path.exists():
            out = pd.DataFrame(columns=cols)
            out.attrs["has_decisions"] = False
            return out
        try:
            df = pd.read_csv(path)
        except Exception:
            return pd.DataFrame(columns=cols)
        for col in cols:
            if col not in df.columns:
                df[col] = None
        return df[cols]


# ── Leg parsing helper ────────────────────────────────────────────────────────

def _parse_legs(legs_raw: list[dict]) -> list[LegFill]:
    result = []
    for leg in legs_raw:
        ef = leg.get("entry_fill") or {}
        greeks = ef.get("greeks") or {}
        result.append(LegFill(
            leg_role=leg.get("leg_role", ""),
            strike=leg.get("strike", 0),
            option_type=leg.get("option_type", ""),
            side=ef.get("side", ""),
            price=ef.get("price", 0.0),
            mark_mid=ef.get("mark_mid", 0.0),
            top_bid=ef.get("top_bid", 0.0),
            top_ask=ef.get("top_ask", 0.0),
            spread_pct=ef.get("spread_pct", 0.0),
            quote_age_ms=ef.get("quote_age_ms", 0),
            depth_available_qty=ef.get("depth_available_qty", 0),
            iv=ef.get("iv"),
            delta=greeks.get("delta"),
            theta=greeks.get("theta"),
            charges=ef.get("charges", 0.0),
        ))
    return result

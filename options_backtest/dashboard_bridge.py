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
import time as _time
from dataclasses import dataclass, field
from datetime import date, datetime, time as _time_cls
from pathlib import Path
from typing import Any

import pandas as pd

from .calendar import is_trading_day as _is_trading_day

_REPO_ROOT = Path(__file__).parents[1]
_SPOT_FILES: dict[str, Path] = {
    "NIFTY":      _REPO_ROOT / "data" / "processed" / "spot" / "nifty50_1min_CANONICAL.csv",
    "FINNIFTY":   _REPO_ROOT / "data" / "processed" / "spot" / "finnifty_1min_DHAN.csv",
    "MIDCPNIFTY": _REPO_ROOT / "data" / "processed" / "spot" / "midcpnifty_1min_DHAN.csv",
    "SENSEX":     _REPO_ROOT / "data" / "processed" / "spot" / "sensex_1min_DHAN.csv",
}

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

    def __init__(self, live_root: Path) -> None:
        self._root = live_root
        # {cache_key: (expiry_monotonic, value)}
        self._cache: dict[str, tuple[float, Any]] = {}

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

    def _snap(self, name: str) -> Path:
        return self._root / "snapshots" / name

    # ── Session status ───────────────────────────────────────────────────────

    def get_session_status(self) -> SessionStatus:
        def _load() -> SessionStatus:
            ph = self._read_json(self._snap("latest_process_health.json"))
            fs = self._read_json(self._snap("latest_feed_state.json"))
            dc = self._read_json(self._snap("latest_depth_cache.json"))

            session_date_str = (ph or {}).get("session_date", date.today().isoformat())
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

            return SessionStatus(
                session_date=session_date_str,
                market_status=_market_status(today),
                engine_phase=(ph or {}).get("phase", "offline"),
                engine_pid=(ph or {}).get("pid"),
                open_position_count=(ph or {}).get("open_positions", 0),
                feed_connected=(fs or {}).get("connected", False),
                feed_subscribed_count=(fs or {}).get("subscribed_count", 0),
                quote_freshness_pct=(fs or {}).get("quote_freshness_pct", 0.0),
                depth=depth_summary,
                process_health_age_s=self._file_age_s(self._snap("latest_process_health.json")),
                written_at=(ph or {}).get("written_at"),
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
            data = self._read_json(self._snap("latest_open_positions.json"))
            if not data:
                return []
            rows: list[PositionRow] = []
            for pos in data.get("open_positions", []):
                legs = _parse_legs(pos.get("legs", []))
                rows.append(PositionRow(
                    symbol=pos.get("symbol", ""),
                    expiry=pos.get("expiry", ""),
                    lots=pos.get("lots", 0),
                    lot_size=pos.get("lot_size", 0),
                    entry_time=pos.get("entry_time", ""),
                    entry_credit=pos.get("entry_credit", 0.0),
                    entry_charges=pos.get("entry_charges", 0.0),
                    legs=legs,
                    current_mark=None,
                    unrealised_gross_pnl=None,
                    unrealised_net_pnl=None,
                ))
            return rows

        result = self._cached("open_positions", _TTL_LIVE, _load)
        return result if result is not None else []

    # ── Intraday equity curve ────────────────────────────────────────────────

    def get_equity_curve(self, session_date: date) -> list[EquityPoint]:
        date_str = session_date.strftime("%Y%m%d")

        def _load() -> list[EquityPoint]:
            path = self._root / "paper_trades" / f"{date_str}.json"
            if not path.exists():
                return []
            try:
                trades: list[dict] = json.loads(path.read_text(encoding="utf-8"))
            except Exception:
                return []
            points: list[EquityPoint] = []
            cum_gross = 0.0
            cum_net = 0.0
            for trade in sorted(trades, key=lambda t: t.get("exit_time") or ""):
                exit_t = trade.get("exit_time")
                if not exit_t:
                    continue
                cum_gross += trade.get("gross_pnl", 0.0)
                cum_net += trade.get("net_pnl", 0.0)
                points.append(EquityPoint(
                    ts=exit_t,
                    cumulative_gross_pnl=round(cum_gross, 2),
                    cumulative_net_pnl=round(cum_net, 2),
                ))
            return points

        result = self._cached(f"equity_{date_str}", _TTL_TRADES, _load)
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

        Timestamps are "display epoch": the IST naive datetime is treated as UTC
        so that LightweightCharts (which renders UTC) shows the correct IST labels
        (09:15 appears as 09:15 on the chart).
        """
        path = _SPOT_FILES.get(symbol.upper())
        if not path or not path.exists():
            return []

        cache_key = f"spot_{symbol}_{days}"

        def _load() -> list[dict]:
            # Detect timestamp column name (NIFTY canonical uses "datetime", Dhan files use "timestamp")
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
            session_dates = sorted(df["datetime"].dt.date.unique())
            if not session_dates:
                return []
            target = set(session_dates[-max(days, 1):])
            df = df[df["datetime"].dt.date.isin(target)]
            # "Display epoch": treat IST naive timestamps as UTC so the chart
            # shows IST times (09:15) instead of UTC times (03:45).
            df["time"] = (
                (df["datetime"] - pd.Timestamp("1970-01-01"))
                // pd.Timedelta("1s")
            )
            return [
                {
                    "time":  int(r.time),
                    "open":  round(float(r.open),  2),
                    "high":  round(float(r.high),  2),
                    "low":   round(float(r.low),   2),
                    "close": round(float(r.close), 2),
                }
                for r in df.itertuples(index=False)
            ]

        result = self._cached(cache_key, _TTL_HIST, _load)
        return result if result is not None else []

    # ── Historical data (v2 scope — stubs) ──────────────────────────────────

    def historical_spot(self, symbol: str, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame()

    def historical_options(
        self, symbol: str, expiry: date, strike: int, opt_type: str
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def historical_order_book(
        self,
        session_date: date,
        symbol: str,
        expiry: date,
        strike: int,
        opt_type: str,
    ) -> pd.DataFrame:
        return pd.DataFrame()

    def historical_vix(self, start: date, end: date) -> pd.DataFrame:
        return pd.DataFrame()

    # ── Backtest data (v2 scope — stubs) ─────────────────────────────────────

    def backtest_summaries(self, root: Path) -> list:
        return []

    def backtest_ledger(self, backtest_id: str) -> pd.DataFrame:
        return pd.DataFrame()

    def backtest_equity_curve(self, backtest_id: str) -> list[EquityPoint]:
        return []


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

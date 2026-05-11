"""
Pydantic response models for the Wing-6 dashboard API.

This file is the contract between the FastAPI backend and the React frontend.
Every endpoint response must serialise to one of these models. When Claude Design
builds the frontend (Phase 7b), they consume these models as the authoritative
type spec — do not add undocumented fields to endpoint responses.

All timestamps are ISO-8601 strings in IST (Asia/Kolkata).
All monetary values are in INR.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


# ── Leg-level fill ────────────────────────────────────────────────────────────

class LegFillModel(BaseModel):
    leg_role: str
    """One of: short_call / long_call / short_put / long_put"""

    strike: int
    option_type: str
    """CE or PE"""

    side: str
    """BUY or SELL"""

    price: float
    """Executable fill price (ask for buys, bid for sells)"""

    mark_mid: float
    """(best_bid + best_ask) / 2 at fill time — diagnostic only"""

    top_bid: float
    top_ask: float
    spread_pct: float
    quote_age_ms: int
    """Milliseconds between last quote receipt and fill timestamp"""

    depth_available_qty: int
    """Total quantity available through depth levels for this size"""

    iv: float | None = None
    delta: float | None = None
    theta: float | None = None
    charges: float = 0.0


# ── Open position ─────────────────────────────────────────────────────────────

class PositionModel(BaseModel):
    symbol: str
    """NIFTY / FINNIFTY / MIDCPNIFTY / SENSEX"""

    expiry: str
    """YYYY-MM-DD"""

    lots: int
    lot_size: int
    entry_time: str
    """ISO-8601 IST"""

    entry_credit: float
    """Net premium received at entry (gross, before charges)"""

    entry_charges: float
    legs: list[LegFillModel]

    # Mark-to-market (None when live quotes are unavailable)
    current_mark: float | None = None
    unrealised_gross_pnl: float | None = None
    unrealised_net_pnl: float | None = None


# ── Equity curve ──────────────────────────────────────────────────────────────

class EquityPointModel(BaseModel):
    ts: str
    """ISO-8601 IST — use as the x-axis timestamp in Lightweight Charts"""

    cumulative_gross_pnl: float
    cumulative_net_pnl: float


# ── Signal log ────────────────────────────────────────────────────────────────

class SignalLogModel(BaseModel):
    ts: str
    event: str
    """skip / entry / exit"""

    symbol: str
    reason: str
    """Explicit reason code — see Section 7 of live_paper_trading_plan.md"""

    vix: float | None = None
    dte: int | None = None
    bucket: str | None = None


# ── Depth health ──────────────────────────────────────────────────────────────

class DepthSummaryModel(BaseModel):
    written_at: str | None = None
    configured: int
    """Number of security_ids the collector is configured to track"""

    tracked: int
    """Number of security_ids with at least one packet received"""

    ready: int
    """Number with both bid and ask sides fresh (< 5 s)"""

    total: int
    ready_pct: float
    """ready / total * 100"""

    snapshot_age_s: float | None = None
    """Seconds since the depth_cache snapshot was last written"""


# ── Storage health ────────────────────────────────────────────────────────────

class StorageHealthModel(BaseModel):
    wd_mount_ok: bool
    wd_free_gb: float
    raw_packet_flush_age_s: float | None = None
    """Seconds since the newest raw depth packet file was written"""

    parquet_flush_age_s: float | None = None
    """Seconds since the newest order-book parquet was written"""

    depth_cache_age_s: float | None = None
    """Seconds since latest_depth_cache.json was written"""


# ── Alert entry ───────────────────────────────────────────────────────────────

class AlertEntryModel(BaseModel):
    ts: str
    severity: str
    """critical / warning / info"""

    component: str
    reason: str
    message: str
    """Token-scrubbed alert message"""


# ── Alert state ───────────────────────────────────────────────────────────────

class AlertStateModel(BaseModel):
    written_at: str | None = None
    uptime_pct: float
    active_alerts: list[AlertEntryModel]
    alert_counts: dict[str, int]
    """Keys: critical, warning, info"""

    wd_free_gb: float
    latest_external_heartbeat_at: str | None = None
    latest_external_heartbeat_status: str | None = None


# ── Session status ────────────────────────────────────────────────────────────

class SessionStatusModel(BaseModel):
    session_date: str
    """YYYY-MM-DD"""

    market_status: str
    """PRE_OPEN / OPEN / CLOSED / HOLIDAY"""

    engine_phase: str
    """init / connecting / chain_fetch / entry / monitoring / exit / eod / offline"""

    engine_pid: int | None = None
    open_position_count: int
    feed_connected: bool
    feed_subscribed_count: int
    quote_freshness_pct: float
    """% of subscribed option instruments with a quote age < 5 s"""

    depth: DepthSummaryModel | None = None
    process_health_age_s: float | None = None
    """Seconds since the process health snapshot was last written. None = engine offline."""

    written_at: str | None = None


# ── WebSocket push frame ──────────────────────────────────────────────────────

class LivePushFrame(BaseModel):
    """
    JSON frame pushed over /ws/live every 1 s (market hours) or 5 s (off-hours).

    React client usage pattern:
      - Overwrite session / positions / depth / storage / alerts on every frame.
      - Append equity_tick to local series (never re-fetch the full curve on each tick).
      - Render equity_tick as null when there are no closed trades yet today.
    """

    ts: str
    """ISO-8601 IST timestamp of this push frame"""

    session: SessionStatusModel
    positions: list[PositionModel]

    equity_tick: EquityPointModel | None = None
    """Latest cumulative equity point — append to local chart series"""

    depth: DepthSummaryModel | None = None
    storage: StorageHealthModel
    alerts: AlertStateModel


# ── Stub responses for v2 endpoints ──────────────────────────────────────────

class V2PendingResponse(BaseModel):
    status: str = "v2_scope_pending"
    message: str = "This endpoint is implemented in Phase 7b."


# ── Backtest list (v2 stub shape) ─────────────────────────────────────────────

class BacktestSummaryModel(BaseModel):
    id: str
    strategy: str
    start_date: str
    end_date: str
    trades: int
    net_pnl: float
    sharpe: float | None = None
    max_dd_pct: float | None = None


class BacktestListResponse(BaseModel):
    backtests: list[BacktestSummaryModel] = Field(default_factory=list)


# ── Historical OHLCV (v2 stub shape) ─────────────────────────────────────────

class OHLCVBar(BaseModel):
    ts: str
    open: float
    high: float
    low: float
    close: float
    volume: int
    oi: int | None = None


class OHLCVResponse(BaseModel):
    symbol: str
    bars: list[OHLCVBar] = Field(default_factory=list)
    truncated: bool = False
    """True when the response was capped at the row limit"""

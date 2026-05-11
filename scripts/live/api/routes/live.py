"""
Live monitor API routes — /api/live/* and /ws/live.

All REST endpoints are thin adapters: bridge.get_*() → Pydantic model → JSON.
The WebSocket endpoint pushes a LivePushFrame every 1 s (market hours) or 5 s (off-hours).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import date, datetime, time as _time_cls
from typing import Any

from fastapi import APIRouter, Query, Request, WebSocket, WebSocketDisconnect

from ..models import (
    AlertEntryModel,
    AlertStateModel,
    DepthSummaryModel,
    EquityPointModel,
    LegFillModel,
    LivePushFrame,
    PositionModel,
    SessionStatusModel,
    SignalLogModel,
    StorageHealthModel,
)
from options_backtest.dashboard_bridge import (
    AlertEntry,
    AlertState,
    DepthSummary,
    DashboardBridge,
    EquityPoint,
    LegFill,
    PositionRow,
    SessionStatus,
    SignalLogEntry,
    StorageHealth,
)

router = APIRouter(prefix="/api/live", tags=["live"])
ws_router = APIRouter(tags=["websocket"])

log = logging.getLogger(__name__)

# Market-hours push interval (seconds)
_PUSH_INTERVAL_MARKET = 1.0
_PUSH_INTERVAL_OFF = 5.0

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")  # type: ignore[assignment]


def _is_market_hours() -> bool:
    t = datetime.now(tz=_IST).time()
    return _time_cls(9, 0) <= t <= _time_cls(15, 35)


# ── Bridge accessors → Pydantic model converters ──────────────────────────────

def _leg_model(leg: LegFill) -> LegFillModel:
    return LegFillModel(
        leg_role=leg.leg_role,
        strike=leg.strike,
        option_type=leg.option_type,
        side=leg.side,
        price=leg.price,
        mark_mid=leg.mark_mid,
        top_bid=leg.top_bid,
        top_ask=leg.top_ask,
        spread_pct=leg.spread_pct,
        quote_age_ms=leg.quote_age_ms,
        depth_available_qty=leg.depth_available_qty,
        iv=leg.iv,
        delta=leg.delta,
        theta=leg.theta,
        charges=leg.charges,
    )


def _position_model(pos: PositionRow) -> PositionModel:
    return PositionModel(
        symbol=pos.symbol,
        expiry=pos.expiry,
        lots=pos.lots,
        lot_size=pos.lot_size,
        entry_time=pos.entry_time,
        entry_credit=pos.entry_credit,
        entry_charges=pos.entry_charges,
        legs=[_leg_model(l) for l in pos.legs],
        current_mark=pos.current_mark,
        unrealised_gross_pnl=pos.unrealised_gross_pnl,
        unrealised_net_pnl=pos.unrealised_net_pnl,
    )


def _equity_model(pt: EquityPoint) -> EquityPointModel:
    return EquityPointModel(
        ts=pt.ts,
        cumulative_gross_pnl=pt.cumulative_gross_pnl,
        cumulative_net_pnl=pt.cumulative_net_pnl,
    )


def _signal_model(s: SignalLogEntry) -> SignalLogModel:
    return SignalLogModel(
        ts=s.ts, event=s.event, symbol=s.symbol, reason=s.reason,
        vix=s.vix, dte=s.dte, bucket=s.bucket,
    )


def _depth_model(d: DepthSummary) -> DepthSummaryModel:
    return DepthSummaryModel(
        written_at=d.written_at,
        configured=d.configured,
        tracked=d.tracked,
        ready=d.ready,
        total=d.total,
        ready_pct=d.ready_pct,
        snapshot_age_s=d.snapshot_age_s,
    )


def _storage_model(s: StorageHealth) -> StorageHealthModel:
    return StorageHealthModel(
        wd_mount_ok=s.wd_mount_ok,
        wd_free_gb=s.wd_free_gb,
        raw_packet_flush_age_s=s.raw_packet_flush_age_s,
        parquet_flush_age_s=s.parquet_flush_age_s,
        depth_cache_age_s=s.depth_cache_age_s,
    )


def _alert_entry_model(a: AlertEntry) -> AlertEntryModel:
    return AlertEntryModel(
        ts=a.ts, severity=a.severity, component=a.component,
        reason=a.reason, message=a.message,
    )


def _alert_state_model(a: AlertState) -> AlertStateModel:
    return AlertStateModel(
        written_at=a.written_at,
        uptime_pct=a.uptime_pct,
        active_alerts=[_alert_entry_model(x) for x in a.active_alerts],
        alert_counts=a.alert_counts,
        wd_free_gb=a.wd_free_gb,
        latest_external_heartbeat_at=a.latest_external_heartbeat_at,
        latest_external_heartbeat_status=a.latest_external_heartbeat_status,
    )


def _session_model(s: SessionStatus) -> SessionStatusModel:
    return SessionStatusModel(
        session_date=s.session_date,
        market_status=s.market_status,
        engine_phase=s.engine_phase,
        engine_pid=s.engine_pid,
        open_position_count=s.open_position_count,
        feed_connected=s.feed_connected,
        feed_subscribed_count=s.feed_subscribed_count,
        quote_freshness_pct=s.quote_freshness_pct,
        depth=_depth_model(s.depth) if s.depth else None,
        process_health_age_s=s.process_health_age_s,
        written_at=s.written_at,
    )


def _bridge(request: Request) -> DashboardBridge:
    return request.app.state.bridge


# ── REST endpoints ────────────────────────────────────────────────────────────

@router.get("/session", response_model=SessionStatusModel, summary="Current session and engine status")
def get_session(request: Request) -> SessionStatusModel:
    return _session_model(_bridge(request).get_session_status())


@router.get("/positions", response_model=list[PositionModel], summary="Open paper positions")
def get_positions(request: Request) -> list[PositionModel]:
    return [_position_model(p) for p in _bridge(request).get_open_positions()]


@router.get("/equity-curve", response_model=list[EquityPointModel], summary="Intraday cumulative P&L series")
def get_equity_curve(
    request: Request,
    date: str = Query(default="today", description="YYYYMMDD or 'today'"),
) -> list[EquityPointModel]:
    from datetime import date as _date
    if date == "today":
        session_date = _date.today()
    else:
        try:
            session_date = _date.fromisoformat(f"{date[:4]}-{date[4:6]}-{date[6:]}")
        except (ValueError, IndexError):
            session_date = _date.today()
    return [_equity_model(p) for p in _bridge(request).get_equity_curve(session_date)]


@router.get("/signal-log", response_model=list[SignalLogModel], summary="Entry/skip/exit event log")
def get_signal_log(
    request: Request,
    date: str = Query(default="today", description="YYYYMMDD or 'today'"),
) -> list[SignalLogModel]:
    from datetime import date as _date
    if date == "today":
        session_date = _date.today()
    else:
        try:
            session_date = _date.fromisoformat(f"{date[:4]}-{date[4:6]}-{date[6:]}")
        except (ValueError, IndexError):
            session_date = _date.today()
    return [_signal_model(s) for s in _bridge(request).get_signal_log(session_date)]


@router.get("/depth-health", response_model=DepthSummaryModel | None, summary="Depth cache readiness summary")
def get_depth_health(request: Request) -> DepthSummaryModel | None:
    ds = _bridge(request).get_depth_summary()
    return _depth_model(ds) if ds else None


@router.get("/storage-health", response_model=StorageHealthModel, summary="WD storage and flush health")
def get_storage_health(request: Request) -> StorageHealthModel:
    return _storage_model(_bridge(request).get_storage_health())


@router.get("/alerts", response_model=AlertStateModel, summary="Active alerts and today's alert history")
def get_alerts(
    request: Request,
    date: str = Query(default="today", description="YYYYMMDD or 'today'"),
) -> AlertStateModel:
    from datetime import date as _date
    if date == "today":
        session_date = _date.today()
    else:
        try:
            session_date = _date.fromisoformat(f"{date[:4]}-{date[4:6]}-{date[6:]}")
        except (ValueError, IndexError):
            session_date = _date.today()
    bridge = _bridge(request)
    state = bridge.get_alert_state()
    history = bridge.get_alert_history(session_date)
    state_model = _alert_state_model(state)
    # Return full day history (newest first) for timeline display.
    # Fall back to active alerts when no history file exists yet.
    if history:
        state_model.active_alerts = [_alert_entry_model(a) for a in reversed(history)]
    else:
        state_model.active_alerts = [_alert_entry_model(a) for a in state.active_alerts]
    return state_model


@router.get("/spot/{symbol}", summary="1-min spot OHLCV bars (last N trading sessions)")
def get_spot_bars(
    symbol: str,
    request: Request,
    days: int = Query(default=1, ge=1, le=5, description="Number of sessions"),
) -> list[Any]:
    return _bridge(request).get_spot_bars(symbol.upper(), days)


# ── WebSocket push ────────────────────────────────────────────────────────────

@ws_router.websocket("/ws/live")
async def ws_live(websocket: WebSocket) -> None:
    """
    Push a LivePushFrame JSON object on every tick.
    Tick interval: 1 s during market hours (09:00–15:35 IST), 5 s otherwise.

    React client pattern:
      ws.onmessage = (e) => {
        const frame = JSON.parse(e.data);
        setSession(frame.session);
        setPositions(frame.positions);
        if (frame.equity_tick) appendEquity(frame.equity_tick);
        setDepth(frame.depth);
        setStorage(frame.storage);
        setAlerts(frame.alerts);
      };
    """
    await websocket.accept()
    bridge: DashboardBridge = websocket.app.state.bridge
    loop = asyncio.get_event_loop()

    try:
        while True:
            interval = _PUSH_INTERVAL_MARKET if _is_market_hours() else _PUSH_INTERVAL_OFF

            # Run all bridge reads in executor to avoid blocking the event loop
            from datetime import date as _date
            today = _date.today()

            session, positions, equity_pts, storage, alert_state, alert_history = await asyncio.gather(
                loop.run_in_executor(None, bridge.get_session_status),
                loop.run_in_executor(None, bridge.get_open_positions),
                loop.run_in_executor(None, bridge.get_equity_curve, today),
                loop.run_in_executor(None, bridge.get_storage_health),
                loop.run_in_executor(None, bridge.get_alert_state),
                loop.run_in_executor(None, bridge.get_alert_history, today),
            )

            equity_tick = _equity_model(equity_pts[-1]) if equity_pts else None
            depth_summary = session.depth

            alerts_model = _alert_state_model(alert_state)
            # Include full day history (newest first) so timeline stays current.
            if alert_history:
                alerts_model.active_alerts = [_alert_entry_model(a) for a in reversed(alert_history)]
            # else: keep active_alerts from state (already set by _alert_state_model)

            frame = LivePushFrame(
                ts=datetime.now(tz=_IST).isoformat(),
                session=_session_model(session),
                positions=[_position_model(p) for p in positions],
                equity_tick=equity_tick,
                depth=_depth_model(depth_summary) if depth_summary else None,
                storage=_storage_model(storage),
                alerts=alerts_model,
            )

            await websocket.send_text(frame.model_dump_json())
            await asyncio.sleep(interval)

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        log.warning("ws_live: unexpected error — %r", exc)
        try:
            await websocket.close(code=1011)
        except Exception:
            pass

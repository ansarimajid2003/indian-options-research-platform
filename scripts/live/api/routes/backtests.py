"""
Backtest comparison API routes - /api/backtests/*.

Canonical dashboard runs and read-only legacy reports are both exposed through
the same response contract. Summary-only legacy rows stay visible in comparison
tables but return empty drilldown payloads.
"""

from __future__ import annotations

import asyncio
import math
from datetime import datetime
from functools import partial
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ..models import (
    BacktestListResponse,
    BacktestSummaryModel,
    DecisionLogResponse,
    DecisionLogRow,
    DrawdownPoint,
    EventOverlayPoint,
    LedgerResponse,
    MonthlyReturn,
    OHLCVResponse,
    TradeLedgerRow,
)

router = APIRouter(prefix="/api/backtests", tags=["backtests"])

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")  # type: ignore[assignment]


def _bridge(request: Request):
    return request.app.state.bridge


async def _run_bridge(request: Request, func_name: str, *args: Any) -> Any:
    bridge = _bridge(request)
    func = getattr(bridge, func_name)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args))


def _clean(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _float(value: Any) -> float | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int(value: Any) -> int | None:
    value = _clean(value)
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _summary_model(raw: dict[str, Any]) -> BacktestSummaryModel:
    source_kind = str(raw.get("source_kind", "canonical"))
    return BacktestSummaryModel(
        id=str(raw.get("id", "")),
        name=str(raw.get("name", raw.get("id", ""))),
        strategy=str(raw.get("strategy", "")),
        symbol=str(raw.get("symbol", "")),
        start_date=str(raw.get("start_date", raw.get("date_from", ""))),
        end_date=str(raw.get("end_date", raw.get("date_to", ""))),
        date_from=str(raw.get("date_from", raw.get("start_date", ""))),
        date_to=str(raw.get("date_to", raw.get("end_date", ""))),
        trades=int(_float(raw.get("trades")) or 0),
        net_pnl=float(_float(raw.get("net_pnl")) or 0.0),
        cagr=_float(raw.get("cagr")),
        sharpe=_float(raw.get("sharpe")),
        sortino=_float(raw.get("sortino")),
        calmar=_float(raw.get("calmar")),
        max_dd_pct=_float(raw.get("max_dd_pct", raw.get("max_drawdown_pct"))),
        win_rate=_float(raw.get("win_rate")),
        profit_factor=_float(raw.get("profit_factor", raw.get("pf"))),
        t_stat=_float(raw.get("t_stat")),
        created_at=str(raw.get("created_at", "")),
        source_path=str(raw.get("source", raw.get("source_path", ""))),
        source_kind=source_kind,
        legacy=source_kind.startswith("legacy"),
        compat_version=str(raw.get("compat_version", "v2")),
        has_ledger=bool(raw.get("has_ledger", True)),
        has_equity=bool(raw.get("has_equity", raw.get("has_ledger", True))),
        has_decisions=bool(raw.get("has_decisions", False)),
        warnings=list(raw.get("warnings", [])),
    )


def _stats_from_close(close: pd.Series) -> dict[str, float]:
    if close.empty:
        return {"bars_count": 0.0, "truncated": 0.0}
    return {
        "bars_count": float(len(close)),
        "mean_close": float(close.mean()),
        "min_close": float(close.min()),
        "max_close": float(close.max()),
        "std_close": float(close.std(ddof=0)) if len(close) > 1 else 0.0,
        "total_volume": 0.0,
        "truncated": 0.0,
    }


def _equity_response(backtest_id: str, df: pd.DataFrame) -> OHLCVResponse:
    bars = []
    if not df.empty:
        for rec in df.to_dict(orient="records"):
            equity = float(_float(rec.get("equity")) or 0.0)
            bars.append({
                "ts": str(rec.get("date", "")),
                "open": equity,
                "high": equity,
                "low": equity,
                "close": equity,
                "volume": 0,
            })
    close = pd.Series([b["close"] for b in bars], dtype="float64")
    stats = _stats_from_close(close)
    if not df.empty and "normalised" in df.columns:
        stats["normalised_last"] = float(pd.to_numeric(df["normalised"], errors="coerce").dropna().iloc[-1])
    return OHLCVResponse(
        symbol=backtest_id,
        bars=bars,
        stats=stats,
        generated_at=datetime.now(tz=_IST).isoformat(),
        row_count=len(bars),
    )


def _ledger_row(row: pd.Series, legacy: bool) -> TradeLedgerRow:
    entry_credit = row.get("entry_premium", row.get("entry_credit", 0.0))
    symbol = str(_clean(row.get("symbol")) or "")
    inferred = False
    if not symbol:
        inferred = True
        symbol = ""
    return TradeLedgerRow(
        entry_date=str(_clean(row.get("entry_date")) or ""),
        exit_date=str(_clean(row.get("exit_date")) or ""),
        symbol=symbol,
        expiry=str(_clean(row.get("expiry")) or ""),
        dte=_int(row.get("dte")),
        vix=_float(row.get("vix")),
        vix_bucket=str(_clean(row.get("vix_bucket")) or "") or None,
        entry_credit=float(_float(entry_credit) or 0.0),
        gross_pnl=float(_float(row.get("gross_pnl")) or 0.0),
        net_pnl=float(_float(row.get("net_pnl")) or 0.0),
        exit_reason=str(_clean(row.get("exit_reason")) or ""),
        entry_legs_raw=str(_clean(row.get("entry_legs", row.get("legs"))) or "") or None,
        exit_legs_raw=str(_clean(row.get("exit_legs")) or "") or None,
        legacy=legacy,
        symbol_inferred=inferred,
    )


@router.get("", response_model=BacktestListResponse, summary="[v2] List available backtest runs")
async def list_backtests(request: Request) -> BacktestListResponse:
    rows = await _run_bridge(request, "backtest_list")
    return BacktestListResponse(backtests=[_summary_model(x) for x in rows])


@router.get("/{backtest_id}", response_model=BacktestSummaryModel, summary="[v2] Summary for one backtest")
async def get_backtest(backtest_id: str, request: Request) -> BacktestSummaryModel:
    summary = await _run_bridge(request, "backtest_summary", backtest_id)
    if not summary:
        raise HTTPException(status_code=404, detail=f"unknown backtest_id: {backtest_id}")
    return _summary_model(summary)


@router.get("/{backtest_id}/equity-curve", response_model=OHLCVResponse, summary="[v2] Equity curve")
async def get_backtest_equity(backtest_id: str, request: Request) -> OHLCVResponse:
    df = await _run_bridge(request, "backtest_equity_curve", backtest_id)
    return _equity_response(backtest_id, df)


@router.get("/{backtest_id}/drawdown", response_model=list[DrawdownPoint], summary="[v2] Drawdown curve")
async def get_backtest_drawdown(backtest_id: str, request: Request) -> list[DrawdownPoint]:
    df = await _run_bridge(request, "backtest_drawdown", backtest_id)
    if df.empty:
        return []
    return [
        DrawdownPoint(date=str(row.date), drawdown_pct=float(row.drawdown_pct))
        for row in df.itertuples(index=False)
    ]


@router.get("/{backtest_id}/monthly", response_model=list[MonthlyReturn], summary="[v2] Monthly returns")
async def get_backtest_monthly(backtest_id: str, request: Request) -> list[MonthlyReturn]:
    df = await _run_bridge(request, "backtest_monthly_returns", backtest_id)
    if df.empty:
        return []
    return [
        MonthlyReturn(
            year=int(row.year),
            month=int(row.month),
            net_pnl=float(row.net_pnl),
            return_pct=float(row.return_pct),
        )
        for row in df.itertuples(index=False)
    ]


@router.get("/{backtest_id}/ledger", response_model=LedgerResponse, summary="[v2] Trade ledger")
async def get_backtest_ledger(
    backtest_id: str,
    request: Request,
    page: int = Query(default=0, ge=0),
    size: int = Query(default=25, ge=1, le=500),
    symbol: str | None = Query(default=None),
    exit_reason: str | None = Query(default=None),
) -> LedgerResponse:
    summary = await _run_bridge(request, "backtest_summary", backtest_id)
    source_kind = str(summary.get("source_kind", "canonical")) if summary else "canonical"
    legacy = source_kind.startswith("legacy")
    if summary and not summary.get("has_ledger", True):
        return LedgerResponse(
            total=0,
            page=page,
            size=size,
            rows=[],
            source_kind=source_kind,
            warnings=["summary_only_no_ledger"],
        )
    df = await _run_bridge(request, "backtest_ledger", backtest_id)
    if df.empty:
        return LedgerResponse(total=0, page=page, size=size, rows=[], source_kind=source_kind)
    if symbol and "symbol" in df.columns:
        df = df[df["symbol"].astype(str).str.upper() == symbol.upper()]
    if exit_reason and "exit_reason" in df.columns:
        df = df[df["exit_reason"].astype(str) == exit_reason]
    total = len(df)
    start = page * size
    rows = [_ledger_row(row, legacy) for _, row in df.iloc[start:start + size].iterrows()]
    return LedgerResponse(total=total, page=page, size=size, rows=rows, source_kind=source_kind)


@router.get("/{backtest_id}/events", response_model=list[EventOverlayPoint], summary="[v2] Chart event overlay")
async def get_backtest_events(backtest_id: str, request: Request) -> list[EventOverlayPoint]:
    rows = await _run_bridge(request, "backtest_events", backtest_id)
    events: list[EventOverlayPoint] = []
    for row in rows:
        details = row.get("details", {})
        if not isinstance(details, dict):
            details = {"text": str(details)}
        events.append(EventOverlayPoint(
            ts=str(row.get("ts", "")),
            symbol=str(row.get("symbol", "")),
            event_type=str(row.get("event_type", "")),
            severity=str(row.get("severity", "info")),
            label=str(row.get("label", "")),
            details=details,
        ))
    return events


@router.get("/{backtest_id}/decisions", response_model=DecisionLogResponse, summary="[v2] Decision log")
async def get_backtest_decisions(backtest_id: str, request: Request) -> DecisionLogResponse:
    df = await _run_bridge(request, "backtest_decisions", backtest_id)
    if df.empty:
        return DecisionLogResponse(
            total=0,
            rows=[],
            has_decisions=bool(df.attrs.get("has_decisions", False)),
            warnings=["no_decision_log"],
        )
    rows = [
        DecisionLogRow(
            ts=str(_clean(row.get("ts")) or ""),
            symbol=str(_clean(row.get("symbol")) or ""),
            decision=str(_clean(row.get("decision")) or ""),
            reason=str(_clean(row.get("reason")) or ""),
            vix=_float(row.get("vix")),
            dte=_int(row.get("dte")),
            expiry=str(_clean(row.get("expiry")) or "") or None,
            eligible=bool(row.get("eligible")) if _clean(row.get("eligible")) is not None else None,
            selected=bool(row.get("selected")) if _clean(row.get("selected")) is not None else None,
        )
        for _, row in df.iterrows()
    ]
    return DecisionLogResponse(total=len(rows), rows=rows, has_decisions=True)

"""
Historical data API routes - /api/historical/*.

These endpoints are read-only adapters over DashboardBridge. The bridge owns
file layout and compatibility logic; routes handle request validation and the
stable JSON contract for the React dashboard.
"""

from __future__ import annotations

import asyncio
import math
from datetime import date, datetime
from functools import partial
from typing import Any

import pandas as pd
from fastapi import APIRouter, HTTPException, Query, Request

from ..models import HistoricalMetadata, OHLCVResponse

router = APIRouter(prefix="/api/historical", tags=["historical"])

_VALID_SYMBOLS = {"NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"}

try:
    from zoneinfo import ZoneInfo
    _IST = ZoneInfo("Asia/Kolkata")
except ImportError:
    import pytz
    _IST = pytz.timezone("Asia/Kolkata")  # type: ignore[assignment]


def _bridge(request: Request):
    return request.app.state.bridge


def _parse_date(value: str, name: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=f"{name} must be YYYY-MM-DD") from exc


def _validate_symbol(symbol: str) -> str:
    sym = symbol.upper()
    if sym not in _VALID_SYMBOLS:
        raise HTTPException(status_code=400, detail=f"unknown symbol: {symbol}")
    return sym


def _json_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, pd.Timestamp):
        return value.date().isoformat() if value.time().isoformat() == "00:00:00" else value.isoformat()
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
        return None
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _records(df: pd.DataFrame) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for rec in df.to_dict(orient="records"):
        rows.append({str(k): _json_value(v) for k, v in rec.items()})
    return rows


def _stats(df: pd.DataFrame) -> dict[str, float]:
    if df.empty or "close" not in df.columns:
        return {
            "bars_count": 0.0,
            "truncated": float(bool(df.attrs.get("truncated", False))),
        }
    close = pd.to_numeric(df["close"], errors="coerce")
    volume = pd.to_numeric(df.get("volume", 0), errors="coerce").fillna(0)
    return {
        "bars_count": float(len(df)),
        "mean_close": float(close.mean()) if not close.empty else 0.0,
        "min_close": float(close.min()) if not close.empty else 0.0,
        "max_close": float(close.max()) if not close.empty else 0.0,
        "std_close": float(close.std(ddof=0)) if len(close) > 1 else 0.0,
        "total_volume": float(volume.sum()),
        "truncated": float(bool(df.attrs.get("truncated", False))),
    }


def _data_age_s(df: pd.DataFrame) -> float | None:
    if df.empty or "ts" not in df.columns:
        return None
    ts = pd.to_datetime(df["ts"].iloc[-1], errors="coerce")
    if pd.isna(ts):
        return None
    stamp = pd.Timestamp(ts)
    if stamp.tzinfo is None:
        stamp = stamp.tz_localize(_IST)
    else:
        stamp = stamp.tz_convert(_IST)
    return max((datetime.now(tz=_IST) - stamp.to_pydatetime()).total_seconds(), 0.0)


def _ohlcv_response(symbol: str, df: pd.DataFrame) -> OHLCVResponse:
    return OHLCVResponse(
        symbol=symbol,
        bars=_records(df),
        truncated=bool(df.attrs.get("truncated", False)),
        stats=_stats(df),
        source=str(df.attrs.get("source", "")),
        generated_at=datetime.now(tz=_IST).isoformat(),
        row_count=int(len(df)),
        data_age_s=_data_age_s(df),
        warnings=list(df.attrs.get("warnings", [])),
    )


async def _run_bridge(request: Request, func_name: str, *args: Any) -> Any:
    bridge = _bridge(request)
    func = getattr(bridge, func_name)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(None, partial(func, *args))


@router.get("/spot/{symbol}", response_model=OHLCVResponse, summary="[v2] Spot OHLCV")
async def historical_spot(
    symbol: str,
    request: Request,
    start: str = Query(description="YYYY-MM-DD"),
    end: str = Query(description="YYYY-MM-DD"),
    tf: int = Query(default=1, ge=1, le=390, description="Timeframe in minutes"),
) -> OHLCVResponse:
    sym = _validate_symbol(symbol)
    start_d = _parse_date(start, "start")
    end_d = _parse_date(end, "end")
    df = await _run_bridge(request, "historical_spot", sym, start_d, end_d, tf)
    return _ohlcv_response(sym, df)


@router.get("/vix", response_model=OHLCVResponse, summary="[v2] India VIX OHLCV")
async def historical_vix(
    request: Request,
    start: str = Query(description="YYYY-MM-DD"),
    end: str = Query(description="YYYY-MM-DD"),
    tf: int = Query(default=1, ge=1, le=390, description="Timeframe in minutes"),
) -> OHLCVResponse:
    start_d = _parse_date(start, "start")
    end_d = _parse_date(end, "end")
    df = await _run_bridge(request, "historical_vix", start_d, end_d, tf)
    return _ohlcv_response("VIX", df)


@router.get("/metadata/{symbol}", response_model=HistoricalMetadata, summary="[v2] Options metadata")
async def historical_metadata(symbol: str, request: Request) -> HistoricalMetadata:
    sym = _validate_symbol(symbol)
    meta = await _run_bridge(request, "historical_options_metadata", sym)
    return HistoricalMetadata(
        symbol=sym,
        date_min=meta.get("date_min") or "",
        date_max=meta.get("date_max") or "",
        expiries=meta.get("expiry_types", []),
        strikes=[],
        expiry_types=meta.get("expiry_types", []),
        atm_offsets=meta.get("atm_offsets", []),
        opt_types=meta.get("opt_types", []),
        source=meta.get("source", ""),
    )


@router.get("/options/{symbol}", response_model=OHLCVResponse, summary="[v2] Options OHLCV")
async def historical_options(
    symbol: str,
    request: Request,
    expiry: str = Query(description="expiry type: week or month"),
    strike: str = Query(default="ATM", description="ATM offset: ATM, ATMm1..ATMp10"),
    opt_type: str = Query(description="CE/PE or call/put"),
    start: str = Query(description="YYYY-MM-DD"),
    end: str = Query(description="YYYY-MM-DD"),
) -> OHLCVResponse:
    sym = _validate_symbol(symbol)
    start_d = _parse_date(start, "start")
    end_d = _parse_date(end, "end")
    df = await _run_bridge(request, "historical_options", sym, expiry, strike, opt_type, start_d, end_d)
    return _ohlcv_response(f"{sym}_{expiry}_{strike}_{opt_type}", df)


@router.get("/bhavcopy/{symbol}", response_model=OHLCVResponse, summary="[v2] NSE bhavcopy EOD")
async def historical_bhavcopy(
    symbol: str,
    request: Request,
    start: str = Query(description="YYYY-MM-DD"),
    end: str = Query(description="YYYY-MM-DD"),
) -> OHLCVResponse:
    sym = _validate_symbol(symbol)
    start_d = _parse_date(start, "start")
    end_d = _parse_date(end, "end")
    df = await _run_bridge(request, "historical_bhavcopy", sym, start_d, end_d)
    return _ohlcv_response(f"{sym}_BHAVCOPY", df)

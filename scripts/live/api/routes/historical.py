"""
Historical data API routes — /api/historical/*

All endpoints are v2 scope stubs. They return a pending response today and will
be replaced with real parquet reads once the server runner has survived full
market sessions (Phase 7b+).
"""

from __future__ import annotations

from fastapi import APIRouter, Query

from ..models import OHLCVResponse, V2PendingResponse

router = APIRouter(prefix="/api/historical", tags=["historical"])


@router.get("/spot/{symbol}", response_model=OHLCVResponse, summary="[v2] Spot 1-min OHLCV")
def historical_spot(
    symbol: str,
    start: str = Query(description="YYYY-MM-DD"),
    end: str = Query(description="YYYY-MM-DD"),
) -> OHLCVResponse:
    return OHLCVResponse(symbol=symbol, bars=[])


@router.get(
    "/options/{symbol}/{expiry}/{strike}/{opt_type}",
    response_model=OHLCVResponse,
    summary="[v2] Options 1-min OHLCV",
)
def historical_options(
    symbol: str,
    expiry: str,
    strike: int,
    opt_type: str,
) -> OHLCVResponse:
    return OHLCVResponse(symbol=f"{symbol}_{expiry}_{strike}_{opt_type}", bars=[])


@router.get(
    "/order-book/{date}/{symbol}/{expiry}/{strike}/{opt_type}",
    response_model=OHLCVResponse,
    summary="[v2] Order book 1-min aggregates",
)
def historical_order_book(
    date: str,
    symbol: str,
    expiry: str,
    strike: int,
    opt_type: str,
) -> OHLCVResponse:
    return OHLCVResponse(symbol=f"{symbol}_{strike}_{opt_type}", bars=[])


@router.get("/vix", response_model=OHLCVResponse, summary="[v2] India VIX 1-min")
def historical_vix(
    start: str = Query(description="YYYY-MM-DD"),
    end: str = Query(description="YYYY-MM-DD"),
) -> OHLCVResponse:
    return OHLCVResponse(symbol="VIX", bars=[])

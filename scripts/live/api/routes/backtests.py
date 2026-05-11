"""
Backtest comparison API routes — /api/backtests/*

All endpoints are v2 scope stubs. They return empty responses today and will be
wired to the engine's JSON ledger files once the live paper run is stable.
"""

from __future__ import annotations

from fastapi import APIRouter

from ..models import BacktestListResponse, BacktestSummaryModel, OHLCVResponse, V2PendingResponse

router = APIRouter(prefix="/api/backtests", tags=["backtests"])


@router.get("", response_model=BacktestListResponse, summary="[v2] List available backtest runs")
def list_backtests() -> BacktestListResponse:
    return BacktestListResponse(backtests=[])


@router.get("/{backtest_id}", response_model=V2PendingResponse, summary="[v2] Full ledger for one backtest")
def get_backtest(backtest_id: str) -> V2PendingResponse:
    return V2PendingResponse()


@router.get("/{backtest_id}/equity-curve", response_model=OHLCVResponse, summary="[v2] Equity curve for one backtest")
def get_backtest_equity(backtest_id: str) -> OHLCVResponse:
    return OHLCVResponse(symbol=backtest_id, bars=[])

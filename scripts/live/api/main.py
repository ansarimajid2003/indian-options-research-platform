"""
Wing-6 Dashboard API — FastAPI application entry point.

Usage (on zimaos):
    source .env.live
    .venv/bin/uvicorn scripts.live.api.main:app --host 127.0.0.1 --port 8000

Usage (local dev, laptop):
    LIVE_ROOT=/path/to/wdstorage/indian-markets-live uvicorn scripts.live.api.main:app --reload

Environment variables:
    LIVE_ROOT   — absolute path to the live data root
                  (resolves to /media/WD-Storage/indian-markets-live on zimaos)
                  Defaults to data/live relative to the repo root if not set.

Security:
    - Bind to 127.0.0.1 only; access through SSH tunnel from laptop.
    - CORS allows only the React dev origin and the tunnel origin.
    - This server is read-only. No broker API calls, no order placement.

Phase 7a delivers:
    - /api/live/*   all REST endpoints fully implemented
    - /ws/live      WebSocket push fully implemented
    - /api/historical/* and /api/backtests/*  v2 stubs

Phase 7b (Claude Design):
    - React build deployed to dashboard/dist/
    - FastAPI serves it as static files at the root path
"""

from __future__ import annotations

import logging
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

# Ensure repo root is on sys.path when running as `uvicorn scripts.live.api.main:app`
_repo_root = Path(__file__).parents[3]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from options_backtest.dashboard_bridge import DashboardBridge
from .routes.live import router as live_router, ws_router
from .routes.historical import router as historical_router
from .routes.backtests import router as backtests_router

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
log = logging.getLogger(__name__)


def _resolve_live_root() -> Path:
    env = os.environ.get("LIVE_ROOT", "")
    if env:
        p = Path(env)
        if p.exists():
            return p
        log.warning("LIVE_ROOT=%s does not exist — falling back to repo-relative path", env)
    # Repo-relative fallback: data/live (may be a symlink to WD storage)
    fallback = _repo_root / "data" / "live"
    if not fallback.exists():
        log.warning(
            "data/live does not exist at %s — bridge will return empty data until the path is mounted",
            fallback,
        )
    return fallback


@asynccontextmanager
async def lifespan(app: FastAPI):
    live_root = _resolve_live_root()
    log.info("DashboardBridge: live_root=%s", live_root)
    app.state.bridge = DashboardBridge(live_root)
    yield
    # No cleanup needed; bridge holds no connections


app = FastAPI(
    title="Wing-6 Dashboard API",
    description=(
        "Read-only FastAPI backend for the Wing-6 Iron Condor paper trading dashboard. "
        "All data is served from flushed snapshot files — no Dhan API calls."
    ),
    version="7a",
    lifespan=lifespan,
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# CORS — allow React dev server (localhost:5173) and the SSH-tunnel origin only.
# Never allow '*' in production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",   # Vite React dev server
        "http://localhost:8000",   # SSH tunnel access
        "http://127.0.0.1:5173",
        "http://127.0.0.1:8000",
    ],
    allow_methods=["GET"],         # read-only; no POST/PUT/DELETE
    allow_headers=["*"],
    allow_credentials=False,
)

# API routes
app.include_router(live_router)
app.include_router(ws_router)
app.include_router(historical_router)
app.include_router(backtests_router)

# Static dashboard — served at / from dashboard/ (CDN React, no build step needed).
# API routes take priority because they are registered before this mount.
_dashboard = _repo_root / "dashboard"
if _dashboard.exists():
    app.mount("/", StaticFiles(directory=str(_dashboard), html=True), name="static")
    log.info("Serving dashboard from %s", _dashboard)
else:
    log.info("dashboard/ not found at %s — API-only mode", _dashboard)

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

import asyncio
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


async def _run_integrated_engine(app: FastAPI, live_root: Path) -> None:
    """Optional: run the paper engine inside the FastAPI uvicorn process.

    Enabled by setting ``RUN_ENGINE_IN_PROCESS=1`` in the environment.
    Phase E1 of the May-2026 refactor: collapses ``live-paper.service``
    and ``dashboard-api.service`` into one uvicorn process so the
    dashboard reads engine state directly in-memory and the
    cross-process JSON-file IPC layer is eliminated.

    Outside session hours the supervisor task sleeps until the next
    trading-day pre-open; on a holiday it sleeps until the next
    calendar day. After EOD the engine task exits and the supervisor
    loop sleeps until tomorrow. FastAPI keeps serving the dashboard
    throughout.
    """
    # Lazy imports — keep the dashboard process light when the engine
    # is not enabled.
    from datetime import date as _date, datetime as _datetime, time as _time_cls, timedelta
    from zoneinfo import ZoneInfo

    from options_backtest.depth_cache import DepthCache
    from options_backtest.paper_engine import PaperTradingEngine
    from scripts.live.collect_order_book import collect_order_book

    ist = ZoneInfo("Asia/Kolkata")
    profile_name = os.environ.get("LIVE_PROFILE", "wing6_4x1_all_vix_filtered")

    log.info("integrated_engine: supervisor starting; profile=%s", profile_name)
    while True:
        now = _datetime.now(tz=ist)
        # Wait until 08:45 IST of the next trading day.
        target = _datetime.combine(now.date(), _time_cls(8, 45), tzinfo=ist)
        if now >= target:
            target = target + timedelta(days=1)
        sleep_s = (target - now).total_seconds()
        log.info("integrated_engine: sleeping %.0fs until next pre-open at %s", sleep_s, target.isoformat())
        try:
            await asyncio.sleep(sleep_s)
        except asyncio.CancelledError:
            log.info("integrated_engine: supervisor cancelled")
            return

        # Time to potentially trade. Re-read calendar / token state via
        # the same routines the daily orchestrator uses.
        try:
            from scripts.live.run_paper_trading import (
                _load_profile,
                _load_checkpoint,
                _load_restart_reason,
                _ensure_fresh_token,
            )
            from options_backtest.calendar import is_trading_day
        except Exception:
            log.exception("integrated_engine: failed to import orchestrator helpers; skipping today")
            continue

        today = _date.today()
        if not is_trading_day(today):
            log.info("integrated_engine: %s is not a trading day; sleeping until tomorrow", today)
            continue

        profile = _load_profile(profile_name)
        access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
        client_id = os.environ.get("DHAN_CLIENT_ID", "")
        if not access_token or not client_id:
            log.error("integrated_engine: DHAN_ACCESS_TOKEN or DHAN_CLIENT_ID missing; skipping day")
            continue
        access_token = _ensure_fresh_token(access_token, client_id)

        depth_cache = DepthCache()
        engine = PaperTradingEngine(
            profile=profile,
            session_date=today,
            depth_cache=depth_cache,
            access_token=access_token,
            client_id=client_id,
            live_root=live_root,
        )
        checkpoint = _load_checkpoint(live_root, today)
        if checkpoint is not None:
            engine.resume_from_checkpoint(
                checkpoint,
                restart_reason=_load_restart_reason(live_root, today),
            )

        # Expose the live engine on app.state so dashboard routes can
        # read in-memory state without HTTP round-trips.
        app.state.engine = engine
        try:
            engine_task = asyncio.create_task(engine.run())
            collector_task = asyncio.create_task(
                collect_order_book(
                    profile=profile,
                    session_date=today,
                    depth_cache=depth_cache,
                    access_token=access_token,
                    client_id=client_id,
                    live_root=live_root,
                )
            )
            done, pending = await asyncio.wait(
                {engine_task, collector_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for t in pending:
                t.cancel()
            await asyncio.gather(*pending, return_exceptions=True)
            for t in done:
                exc = t.exception()
                if exc is not None:
                    log.exception("integrated_engine: task failed", exc_info=exc)
        finally:
            try:
                engine.close()
            except Exception:
                pass
            app.state.engine = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    live_root = _resolve_live_root()
    log.info("DashboardBridge: live_root=%s", live_root)
    app.state.bridge = DashboardBridge(live_root)
    app.state.engine = None

    integrated = os.environ.get("RUN_ENGINE_IN_PROCESS", "0").strip().lower() in ("1", "true", "yes")
    engine_task: asyncio.Task | None = None
    if integrated:
        log.info("RUN_ENGINE_IN_PROCESS=1 — starting in-process engine supervisor (Phase E1)")
        engine_task = asyncio.create_task(_run_integrated_engine(app, live_root))
    yield
    if engine_task is not None:
        engine_task.cancel()
        try:
            await engine_task
        except (asyncio.CancelledError, Exception):
            pass


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

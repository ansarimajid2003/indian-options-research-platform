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


# ── Engine progress watchdog (defensive depth for a wedged-but-alive engine) ──
#
# IMPORTANT scope note: this watchdog runs IN-PROCESS, so it cannot recover a
# full host/process freeze (the actual 2026-06-02 cause — the watchdog coroutine
# would freeze too). It defends the *other* class: a wedged-but-alive event loop
# where one coroutine deadlocks/blocks but the loop still schedules others, so
# the engine stops making progress while the process stays up. systemd Restart=
# and a UPS remain the levers for a true host freeze.
#
# The signal is NOT phase-age: the legitimate pre-entry phases each span a full
# 15-min boundary gap (waiting_preopen 08:45→09:00, connecting 09:00→09:15), so
# phase-age would false-fire constantly. Instead we use a DEADLINE: by the
# checkpoint time the engine must have advanced PAST the early phases. If it
# hasn't — or market-data ticks have gone stale after the feed connected — it is
# wedged and a one-shot relaunch can still make the 09:20 entry.

# Max in-process relaunches per session before deferring to systemd.
_ENGINE_MAX_RELAUNCHES = int(os.environ.get("ENGINE_MAX_RELAUNCHES", "1"))

# Wall-clock time by which a healthy engine must have advanced past the early
# (connecting) phases. The engine reaches chain_fetch ~09:15 and writes the
# pre-entry gate at 09:18:30; if by this deadline it is still in an early phase
# OR ticks are stale, it is wedged. Default 09:16 leaves room to relaunch before
# the 09:18:30 gate / 09:20 entry.
_ENGINE_PROGRESS_DEADLINE = os.environ.get("ENGINE_PROGRESS_DEADLINE", "09:16:00")

# After the feed connects, market-data ticks should flow continuously. Ticks
# stale beyond this (once we are in/after the connecting window) indicate a
# wedged feed/loop. Generous to avoid firing on a normal brief gap.
_ENGINE_TICK_STALL_SECONDS = float(os.environ.get("ENGINE_TICK_STALL_SECONDS", "120"))

# Latest time the watchdog may still trigger a relaunch (must leave time to
# re-init before 09:20 entry). After this it disarms.
_ENGINE_WATCHDOG_ARM_UNTIL = os.environ.get("ENGINE_WATCHDOG_ARM_UNTIL", "09:17:00")

# Watchdog polls this often.
_ENGINE_WATCHDOG_POLL_SECONDS = 10.0

# Early phases that must be LEFT by the progress deadline. (chain_fetch is the
# expected phase at the deadline, so it is NOT early — staying in init /
# waiting_preopen / connecting past the deadline is the wedge signal.)
_EARLY_PHASES = frozenset({"init", "waiting_preopen", "connecting"})


def _parse_ist_time(value: str):
    from datetime import time as _time_cls
    h, m, s = (int(x) for x in value.split(":"))
    return _time_cls(h, m, s)


async def _engine_progress_watchdog(engine, session_date) -> bool:
    """Return True once the engine is detected wedged before the entry window.

    Wedge signals (only evaluated at/after the progress deadline, only while
    armed, i.e. before ``_ENGINE_WATCHDOG_ARM_UNTIL``):
      1. still in an early phase (init/waiting_preopen/connecting) past the
         deadline — it should have reached chain_fetch by then; or
      2. the feed has connected but market-data ticks have gone stale beyond
         ``_ENGINE_TICK_STALL_SECONDS`` — a wedged-but-alive loop.

    Returns False if cancelled or once disarmed. The caller treats a True
    return as the one-shot relaunch signal.
    """
    from datetime import datetime as _datetime
    from zoneinfo import ZoneInfo

    ist = ZoneInfo("Asia/Kolkata")
    deadline = _datetime.combine(session_date, _parse_ist_time(_ENGINE_PROGRESS_DEADLINE), tzinfo=ist)
    arm_until = _datetime.combine(session_date, _parse_ist_time(_ENGINE_WATCHDOG_ARM_UNTIL), tzinfo=ist)

    try:
        while True:
            await asyncio.sleep(_ENGINE_WATCHDOG_POLL_SECONDS)
            now = _datetime.now(tz=ist)
            if now > arm_until:
                # Past the recoverable window — disarm. Do NOT return: a
                # completed watchdog task would be the first-completed task in
                # asyncio.wait and would tear down a perfectly healthy engine.
                # Idle until cancelled at EOD teardown instead.
                while True:
                    await asyncio.sleep(3600)
            if now < deadline:
                # Too early to judge progress — pre-entry phases legitimately
                # span 15-min gaps. Wait until the deadline.
                continue
            try:
                snap = engine.health_snapshot()
            except Exception:
                # Couldn't read state at/after the deadline — re-check next poll.
                continue
            phase = snap.get("phase")
            feed_connected = snap.get("feed_connected")
            tick_age = snap.get("last_tick_age_seconds")

            wedged_phase = phase in _EARLY_PHASES
            wedged_ticks = (
                bool(feed_connected)
                and tick_age is not None
                and tick_age >= _ENGINE_TICK_STALL_SECONDS
            )
            if wedged_phase or wedged_ticks:
                log.error(
                    "engine_watchdog: WEDGE detected at %s — phase=%s "
                    "feed_connected=%s last_tick_age=%s (deadline=%s); signalling "
                    "one-shot relaunch",
                    now.time().isoformat(), phase, feed_connected, tick_age,
                    _ENGINE_PROGRESS_DEADLINE,
                )
                return True
    except asyncio.CancelledError:
        return False


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

        # The engine + collector run under a progress watchdog. If the engine
        # wedges before the entry window (a coroutine deadlock: the loop still
        # schedules but the engine stops advancing — distinct from a full host
        # freeze, which an in-process watchdog cannot catch), the watchdog
        # cancels both tasks and we relaunch the pair ONCE, in time to still
        # make the 09:20 entry. A single relaunch is the cap — repeated
        # crash-looping is left to systemd's Restart=on-failure.
        #
        # ``relaunch_reason`` is a dict (the shape resume_from_checkpoint
        # expects: it reads ``.get("reason")``). None on the first attempt so
        # the operator's restart_reason.json (consumed only when a checkpoint
        # is actually applied — see below) is used; a dict on relaunch.
        relaunch_reason: dict | None = None
        for attempt in range(_ENGINE_MAX_RELAUNCHES + 1):
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
                # Consume restart_reason.json (one-shot, unlinks the file) ONLY
                # when there is a checkpoint to apply it to — otherwise the
                # operator's reason would be silently discarded on a normal
                # no-checkpoint startup. On a watchdog relaunch use our own
                # reason dict instead of re-reading the (now-unlinked) file.
                reason = relaunch_reason if relaunch_reason is not None else _load_restart_reason(live_root, today)
                engine.resume_from_checkpoint(
                    checkpoint,
                    restart_reason=reason,
                )

            # Expose the live engine on app.state so dashboard routes can
            # read in-memory state without HTTP round-trips.
            app.state.engine = engine
            stalled = False
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
                watchdog_task = asyncio.create_task(
                    _engine_progress_watchdog(engine, today)
                )
                done, pending = await asyncio.wait(
                    {engine_task, collector_task, watchdog_task},
                    return_when=asyncio.FIRST_COMPLETED,
                )
                # The watchdog completing first (returning True) is the stall
                # signal. Any other task completing first is normal EOD or a
                # crash — either way we tear down and do not relaunch.
                wd_exc = watchdog_task.exception() if watchdog_task in done else None
                stalled = (
                    watchdog_task in done
                    and wd_exc is None
                    and watchdog_task.result() is True
                )
                if wd_exc is not None:
                    # The safety net itself crashed — surface it, otherwise the
                    # watchdog is silently disarmed for the session with no
                    # diagnostic. (stalled stays False → no relaunch.)
                    log.error("integrated_engine: watchdog task crashed", exc_info=wd_exc)
                for t in pending:
                    t.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
                for t in done:
                    if t is watchdog_task:
                        continue
                    exc = t.exception()
                    if exc is not None:
                        log.exception("integrated_engine: task failed", exc_info=exc)
            finally:
                try:
                    engine.close()
                except Exception:
                    pass
                app.state.engine = None

            if not stalled:
                break
            if attempt < _ENGINE_MAX_RELAUNCHES:
                # Mark the relaunch so the fresh engine records it in the event
                # log (restart accounting, Phase C1/C3). MUST be a dict —
                # resume_from_checkpoint does ``(restart_reason or {}).get(...)``.
                relaunch_reason = {"reason": "engine_stall_watchdog"}
                log.warning(
                    "integrated_engine: engine wedged before entry — relaunching "
                    "(attempt %d/%d)", attempt + 1, _ENGINE_MAX_RELAUNCHES,
                )
            else:
                log.error(
                    "integrated_engine: engine stalled again after relaunch — "
                    "giving up for today (systemd Restart handles deeper failures)"
                )


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

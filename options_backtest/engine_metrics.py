"""
In-process HTTP metrics endpoint for the paper engine.

Replaces the previous "control plane is the filesystem" design where the
health monitor checked engine liveness by reading
``latest_process_health.json`` mtime, and the dashboard inferred state
from a half-dozen ``latest_*.json`` files. Every layer of that pattern
that we tried — tmpfs liveness, alert grouping, startup grace,
"engine_stall" semantic split — was a workaround for the basic
falsehood that an atomic-rename mtime is a liveness signal.

This endpoint replaces that with a real one:

* ``GET /health`` — returns 200 with the engine's current health JSON
  when the engine task is alive and the asyncio loop is responsive.
  Returns ``503`` only on explicit shutdown.
* ``GET /metrics`` — richer state: phase, feed connection, last tick
  age, depth status per symbol, open position count, MTM equity.

A failed HTTP response (refused connection, timeout) is now an
unambiguous signal that the engine is dead — the OS layer answers, not
the application. That removes the entire class of false-positive
``engine_stall`` alerts caused by snapshot mtime lag.

The server binds to ``127.0.0.1`` only — it is for in-host consumers
(health monitor and dashboard backend), not external clients.
"""

from __future__ import annotations

import logging
from typing import Any, Callable

from aiohttp import web

__all__ = ["MetricsServer"]

_log = logging.getLogger(__name__)

_DEFAULT_HOST = "127.0.0.1"
_DEFAULT_PORT = 8001


class MetricsServer:
    """Optional in-process HTTP server exposing engine health/metrics.

    Lifecycle is asyncio-native:

        srv = MetricsServer(engine_health_fn=engine.health_snapshot,
                            engine_metrics_fn=engine.metrics_snapshot)
        await srv.start()
        ...
        await srv.stop()

    The engine passes *callables* (not the engine itself) so the metrics
    server has no back-reference to engine internals and stays testable
    in isolation.
    """

    def __init__(
        self,
        *,
        engine_health_fn: Callable[[], dict[str, Any]],
        engine_metrics_fn: Callable[[], dict[str, Any]],
        host: str = _DEFAULT_HOST,
        port: int = _DEFAULT_PORT,
    ) -> None:
        self._health_fn = engine_health_fn
        self._metrics_fn = engine_metrics_fn
        self._host = host
        self._port = port
        self._runner: web.AppRunner | None = None
        self._site: web.BaseSite | None = None

    @property
    def url(self) -> str:
        return f"http://{self._host}:{self._port}"

    async def start(self) -> None:
        if self._runner is not None:
            return
        app = web.Application()
        app.router.add_get("/health", self._handle_health)
        app.router.add_get("/metrics", self._handle_metrics)
        runner = web.AppRunner(app, access_log=None)
        await runner.setup()
        site = web.TCPSite(runner, self._host, self._port)
        await site.start()
        self._runner = runner
        self._site = site
        _log.info("metrics_server: listening on %s", self.url)

    async def stop(self) -> None:
        if self._runner is None:
            return
        try:
            await self._runner.cleanup()
        except Exception:
            _log.exception("metrics_server: cleanup failed")
        finally:
            self._runner = None
            self._site = None

    async def _handle_health(self, request: web.Request) -> web.Response:
        try:
            payload = self._health_fn()
        except Exception:
            _log.exception("metrics_server: health snapshot failed")
            return web.json_response({"status": "error"}, status=500)
        status = 200 if payload.get("alive", True) else 503
        return web.json_response(payload, status=status)

    async def _handle_metrics(self, request: web.Request) -> web.Response:
        try:
            payload = self._metrics_fn()
        except Exception:
            _log.exception("metrics_server: metrics snapshot failed")
            return web.json_response({"status": "error"}, status=500)
        return web.json_response(payload)

"""Tests for the in-process metrics HTTP endpoint."""

from __future__ import annotations

import asyncio
import json
import socket
import unittest
import urllib.request

from options_backtest.engine_metrics import MetricsServer


def _pick_free_port() -> int:
    """Return a port that's free at call time. Best-effort — the OS may
    reassign it before the server binds."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _http_get(url: str, timeout: float = 2.0) -> tuple[int, dict]:
    """Sync GET helper. Returns (status_code, parsed_json)."""
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode("utf-8"))


class MetricsServerTests(unittest.IsolatedAsyncioTestCase):
    async def test_health_returns_200_when_alive(self):
        port = _pick_free_port()
        srv = MetricsServer(
            engine_health_fn=lambda: {"alive": True, "phase": "monitoring"},
            engine_metrics_fn=lambda: {"alive": True, "phase": "monitoring", "open_positions": 0},
            port=port,
        )
        await srv.start()
        try:
            status, body = await asyncio.to_thread(_http_get, f"http://127.0.0.1:{port}/health")
            self.assertEqual(status, 200)
            self.assertEqual(body["phase"], "monitoring")
        finally:
            await srv.stop()

    async def test_health_returns_503_when_engine_marks_not_alive(self):
        port = _pick_free_port()
        srv = MetricsServer(
            engine_health_fn=lambda: {"alive": False, "phase": "complete"},
            engine_metrics_fn=lambda: {},
            port=port,
        )
        await srv.start()
        try:
            status, body = await asyncio.to_thread(_http_get, f"http://127.0.0.1:{port}/health")
            self.assertEqual(status, 503)
        finally:
            await srv.stop()

    async def test_metrics_endpoint_returns_payload(self):
        port = _pick_free_port()
        payload = {"alive": True, "open_positions": 3, "feed_connected": True}
        srv = MetricsServer(
            engine_health_fn=lambda: {"alive": True},
            engine_metrics_fn=lambda: payload,
            port=port,
        )
        await srv.start()
        try:
            status, body = await asyncio.to_thread(_http_get, f"http://127.0.0.1:{port}/metrics")
            self.assertEqual(status, 200)
            self.assertEqual(body, payload)
        finally:
            await srv.stop()

    async def test_health_handler_handles_exception(self):
        def boom():
            raise RuntimeError("simulated engine bug")

        port = _pick_free_port()
        srv = MetricsServer(
            engine_health_fn=boom,
            engine_metrics_fn=lambda: {},
            port=port,
        )
        await srv.start()
        try:
            status, body = await asyncio.to_thread(_http_get, f"http://127.0.0.1:{port}/health")
            self.assertEqual(status, 500)
            self.assertEqual(body.get("status"), "error")
        finally:
            await srv.stop()

    async def test_stop_is_idempotent(self):
        port = _pick_free_port()
        srv = MetricsServer(
            engine_health_fn=lambda: {"alive": True},
            engine_metrics_fn=lambda: {},
            port=port,
        )
        await srv.start()
        await srv.stop()
        await srv.stop()  # must not raise


if __name__ == "__main__":
    unittest.main()

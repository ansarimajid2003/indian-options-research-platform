"""Unit tests for the in-process engine progress watchdog.

The watchdog (``scripts.live.api.main._engine_progress_watchdog``) detects an
engine that is wedged-but-alive before the entry window — a coroutine stall
where the asyncio loop still schedules but the engine stops making progress —
and signals the supervisor to relaunch the engine+collector once.

Detection is DEADLINE-based, not phase-age based: the legitimate pre-entry
phases each span a 15-min boundary gap (waiting_preopen 08:45->09:00,
connecting 09:00->09:15), so a phase-age threshold would false-fire on every
healthy session. The watchdog instead only judges progress at/after a deadline
(by when the engine must have left the early phases), and only while armed.

NOTE on scope: this runs IN-PROCESS and cannot catch a full host/process freeze
(the 2026-06-02 cause — the watchdog coroutine would freeze too). It defends the
coroutine-deadlock class, exercised here.
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import date

import scripts.live.api.main as main


class _FakeEngine:
    """Minimal stand-in exposing health_snapshot() like PaperTradingEngine."""

    def __init__(self, phase: str, feed_connected: bool = False, last_tick_age=None):
        self._snap = {
            "phase": phase,
            "feed_connected": feed_connected,
            "last_tick_age_seconds": last_tick_age,
        }

    def set(self, **kw) -> None:
        self._snap.update(kw)

    def health_snapshot(self) -> dict:
        return dict(self._snap)


class EngineWatchdogTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        # Today's date with a deadline at 00:00 and arm-until at 23:59 means the
        # watchdog is armed AND past the deadline for the entire test run,
        # regardless of wall-clock time — a deterministic "armed & judging"
        # window. Fast poll so the test is quick.
        self._today = date.today()
        self._orig = {
            "poll": main._ENGINE_WATCHDOG_POLL_SECONDS,
            "deadline": main._ENGINE_PROGRESS_DEADLINE,
            "arm": main._ENGINE_WATCHDOG_ARM_UNTIL,
            "tick": main._ENGINE_TICK_STALL_SECONDS,
        }
        main._ENGINE_WATCHDOG_POLL_SECONDS = 0.01
        main._ENGINE_PROGRESS_DEADLINE = "00:00:00"
        main._ENGINE_WATCHDOG_ARM_UNTIL = "23:59:59"
        main._ENGINE_TICK_STALL_SECONDS = 120.0

    def tearDown(self):
        main._ENGINE_WATCHDOG_POLL_SECONDS = self._orig["poll"]
        main._ENGINE_PROGRESS_DEADLINE = self._orig["deadline"]
        main._ENGINE_WATCHDOG_ARM_UNTIL = self._orig["arm"]
        main._ENGINE_TICK_STALL_SECONDS = self._orig["tick"]

    async def test_fires_when_still_in_early_phase_past_deadline(self):
        # Stuck in 'connecting' past the progress deadline → wedge.
        engine = _FakeEngine("connecting", feed_connected=True, last_tick_age=1.0)
        result = await asyncio.wait_for(
            main._engine_progress_watchdog(engine, self._today), timeout=2.0
        )
        self.assertTrue(result)

    async def test_fires_when_ticks_stale_after_feed_connected(self):
        # Advanced past early phases (chain_fetch) but the feed connected and
        # ticks have gone stale → wedged-but-alive loop.
        engine = _FakeEngine("chain_fetch", feed_connected=True, last_tick_age=300.0)
        result = await asyncio.wait_for(
            main._engine_progress_watchdog(engine, self._today), timeout=2.0
        )
        self.assertTrue(result)

    async def test_healthy_past_deadline_does_not_fire(self):
        # Past early phases, feed connected, ticks fresh → healthy, no fire.
        engine = _FakeEngine("chain_fetch", feed_connected=True, last_tick_age=2.0)
        task = asyncio.create_task(main._engine_progress_watchdog(engine, self._today))
        await asyncio.sleep(0.1)
        self.assertFalse(task.done(), "watchdog must not fire for a healthy engine")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_does_not_fire_before_deadline(self):
        # With the deadline set in the future, an early phase must NOT fire —
        # pre-entry phases legitimately last ~15 min.
        main._ENGINE_PROGRESS_DEADLINE = "23:59:58"  # effectively never today
        engine = _FakeEngine("connecting", feed_connected=False, last_tick_age=None)
        task = asyncio.create_task(main._engine_progress_watchdog(engine, self._today))
        await asyncio.sleep(0.1)
        self.assertFalse(task.done(), "watchdog must not judge progress before the deadline")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_post_entry_phase_not_flagged(self):
        # 'monitoring' is not an early phase; with fresh ticks it must not fire.
        engine = _FakeEngine("monitoring", feed_connected=True, last_tick_age=2.0)
        task = asyncio.create_task(main._engine_progress_watchdog(engine, self._today))
        await asyncio.sleep(0.1)
        self.assertFalse(task.done())
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def test_disarms_after_window_without_completing(self):
        # arm_until in the past → disarmed; watchdog must NOT complete (a
        # completed watchdog would tear down a healthy engine in asyncio.wait).
        main._ENGINE_WATCHDOG_ARM_UNTIL = "00:00:01"  # already past today
        engine = _FakeEngine("connecting", feed_connected=True, last_tick_age=9999.0)
        task = asyncio.create_task(main._engine_progress_watchdog(engine, self._today))
        await asyncio.sleep(0.1)
        self.assertFalse(task.done(), "watchdog must idle (not complete) once disarmed")
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


if __name__ == "__main__":
    unittest.main()

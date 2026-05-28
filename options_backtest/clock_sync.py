"""
Host clock sync checks for live paper trading.

The backtest engine is offline, but the live paper runner needs a trustworthy
host clock for broker packet receipt times, TOTP generation, and entry timing.
Unsupported platforms return an unknown status instead of raising.
"""

from __future__ import annotations

import subprocess
import time as _time
from dataclasses import dataclass
from typing import Callable


RunCommand = Callable[..., subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class ClockSyncStatus:
    healthy: bool | None
    reason: str
    message: str
    offset_seconds: float | None = None
    synchronized: bool | None = None


def parse_timesync_offset(val: str) -> float | None:
    text = val.strip()
    if not text:
        return None
    unit = ""
    for candidate in ("ms", "us", "ns", "s"):
        if text.endswith(candidate):
            unit = candidate
            number = text[: -len(candidate)]
            break
    else:
        return None
    try:
        num = float(number)
    except ValueError:
        return None
    return num * {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}[unit]


def _run_command(cmd: list[str], runner: RunCommand) -> subprocess.CompletedProcess[str]:
    return runner(cmd, capture_output=True, text=True, timeout=5)


def clock_sync_status(
    max_offset_seconds: float = 2.0,
    runner: RunCommand = subprocess.run,
) -> ClockSyncStatus:
    try:
        result = _run_command(["timedatectl", "timesync-status"], runner)
    except FileNotFoundError:
        return ClockSyncStatus(None, "clock_check_unavailable", "timedatectl is not installed")
    except Exception as exc:
        return ClockSyncStatus(None, "clock_check_failed", f"timesync-status failed: {exc!r}")

    offset_sec: float | None = None
    for line in result.stdout.splitlines():
        if line.strip().startswith("Offset:"):
            offset_sec = parse_timesync_offset(line.split(":", 1)[1].strip())
            break

    if offset_sec is not None:
        if abs(offset_sec) > max_offset_seconds:
            return ClockSyncStatus(
                False,
                "clock_drift_high",
                f"clock drift {offset_sec:+.3f}s exceeds {max_offset_seconds:.1f}s",
                offset_seconds=offset_sec,
                synchronized=True,
            )
        return ClockSyncStatus(
            True,
            "clock_synced",
            f"clock drift {offset_sec:+.3f}s",
            offset_seconds=offset_sec,
            synchronized=True,
        )

    try:
        result = _run_command(["timedatectl", "show"], runner)
    except Exception as exc:
        return ClockSyncStatus(None, "clock_check_failed", f"timedatectl show failed: {exc!r}")

    synced = any(
        line.startswith("NTPSynchronized=yes") or line.startswith("SystemClockSynchronized=yes")
        for line in result.stdout.splitlines()
    )
    if not synced:
        return ClockSyncStatus(False, "clock_not_synced", "timedatectl reports clock not synchronized", synchronized=False)
    return ClockSyncStatus(True, "clock_synced", "timedatectl reports synchronized", synchronized=True)


def remediate_clock_sync(
    max_offset_seconds: float = 2.0,
    timeout_seconds: float = 90.0,
    runner: RunCommand = subprocess.run,
) -> ClockSyncStatus:
    """Restart systemd-timesyncd and wait for the offset to fall under threshold."""

    for cmd in (
        ["timedatectl", "set-ntp", "true"],
        ["systemctl", "restart", "systemd-timesyncd.service"],
    ):
        try:
            runner(cmd, capture_output=True, text=True, timeout=10)
        except FileNotFoundError:
            return ClockSyncStatus(None, "clock_check_unavailable", f"{cmd[0]} is not installed")
        except Exception:
            # Keep going to the wait loop. The caller will alert if the clock
            # remains unhealthy; some hosts reject one command but accept the other.
            pass

    deadline = _time.monotonic() + timeout_seconds
    latest = clock_sync_status(max_offset_seconds=max_offset_seconds, runner=runner)
    while latest.healthy is not True and _time.monotonic() < deadline:
        _time.sleep(2.0)
        latest = clock_sync_status(max_offset_seconds=max_offset_seconds, runner=runner)
    return latest

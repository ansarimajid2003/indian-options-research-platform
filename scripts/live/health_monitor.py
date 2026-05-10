"""
Independent uptime and integrity monitor for Wing-6 paper trading.

Runs as its own process. Does NOT open Dhan websockets.

Usage:
    python scripts/live/health_monitor.py --profile wing6_4x1_all_vix_filtered

Env vars:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID — Telegram alerts
    EXTERNAL_HEARTBEAT_URL              — Healthchecks.io dead-man-switch
    SENTRY_DSN                          — Optional Sentry integration
    EXPECTED_PUBLIC_IP                  — Optional public IP validation (Linux)
    DHAN_ACCESS_TOKEN                   — For JWT token expiry check
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import time as _time
from datetime import date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo

import aiohttp

_repo_root = Path(__file__).parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from options_backtest.calendar import is_trading_day as _is_trading_day

_IST = ZoneInfo("Asia/Kolkata")
_log = logging.getLogger(__name__)

# Check intervals
_CRITICAL_INTERVAL = 15.0   # seconds
_SLOW_INTERVAL = 60.0       # seconds
_HEARTBEAT_INTERVAL_MARKET = 60.0
_HEARTBEAT_INTERVAL_OFF = 90.0  # must be < HC grace period (3 min); was 300s which caused false down/up alerts

# Alert throttle: (severity, component, reason) -> last_sent_epoch
_ALERT_THROTTLE: dict[tuple, float] = {}

# Market hours
_MARKET_OPEN = time(9, 0)
_MARKET_CLOSE = time(15, 35)
_FEED_ACTIVE_START = time(9, 10)
_FEED_ACTIVE_END = time(15, 31)
_HEARTBEAT_START = time(8, 55)

_WD_MIN_GB_INTRADAY = 20.0
_WD_MIN_GB_PRE_RUN = 100.0


def _now_ist() -> datetime:
    return datetime.now(tz=_IST)


def _ist_time() -> time:
    return _now_ist().time()


def _is_market_hours() -> bool:
    if not _is_trading_day(date.today()):
        return False
    t = _ist_time()
    return _MARKET_OPEN <= t <= _MARKET_CLOSE


def _is_heartbeat_hours() -> bool:
    t = _ist_time()
    return t >= _HEARTBEAT_START and t <= _MARKET_CLOSE


def _is_feed_active() -> bool:
    if not _is_trading_day(date.today()):
        return False
    t = _ist_time()
    return _FEED_ACTIVE_START <= t <= _FEED_ACTIVE_END


def _throttle_ok(severity: str, component: str, reason: str) -> bool:
    """Returns True if this alert should be sent (not throttled)."""
    key = (severity, component, reason)
    now = _time.monotonic()
    last = _ALERT_THROTTLE.get(key, 0.0)
    t = _ist_time()
    # Shorter throttle during critical windows
    if (time(9, 10) <= t <= time(9, 30)) or (time(15, 15) <= t <= time(15, 30)):
        window = 120.0
    else:
        window = 600.0
    if now - last >= window:
        _ALERT_THROTTLE[key] = now
        return True
    return False


def _redact_token(token: str) -> str:
    if len(token) > 8:
        return token[:4] + "[REDACTED]"
    return "[REDACTED]"


def _parse_timesync_offset(val: str) -> float | None:
    """Parse 'timedatectl timesync-status' Offset value to seconds.

    Examples: '+1.283901s', '-234.5ms', '+12us'
    Returns None if the string cannot be parsed.
    """
    m = re.match(r'^([+-]?\d+\.?\d*)(s|ms|us|ns)$', val.strip())
    if not m:
        return None
    num, unit = float(m.group(1)), m.group(2)
    return num * {'s': 1.0, 'ms': 1e-3, 'us': 1e-6, 'ns': 1e-9}[unit]


def _jwt_expiry(token: str) -> datetime | None:
    """Decode JWT expiry without verifying signature."""
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=="  # pad
        payload = json.loads(base64.b64decode(payload_b64))
        exp = payload.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=_IST)
    except Exception:
        return None


def _resolve_live_root() -> Path:
    live_path = _repo_root / "data" / "live"
    if sys.platform != "win32":
        resolved = live_path.resolve()
        if not str(resolved).startswith("/media/WD-Storage"):
            raise RuntimeError(f"data/live resolves to {resolved}, expected /media/WD-Storage/...")
    live_path.mkdir(parents=True, exist_ok=True)
    return live_path


class HealthMonitor:
    """
    Runs critical (15s) and slow (60s) checks, sends Telegram alerts,
    pings Healthchecks.io dead-man-switch, and writes alert state snapshots.
    """

    def __init__(self, profile: dict, live_root: Path) -> None:
        self._profile = profile
        self._live_root = live_root
        self._date_str = date.today().strftime("%Y%m%d")
        self._alerts_path = live_root / "alerts" / f"{self._date_str}_alerts.jsonl"
        self._alert_state_path = live_root / "snapshots" / "latest_alert_state.json"

        self._tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        self._tg_thread = os.environ.get("TELEGRAM_THREAD_ID", "")  # topic ID for forum supergroups
        self._hc_url = os.environ.get("EXTERNAL_HEARTBEAT_URL", "")
        self._expected_ip = os.environ.get("EXPECTED_PUBLIC_IP", "")
        self._access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")

        # Cumulative alert counters
        self._alert_counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        self._stop_event = asyncio.Event()
        self._phase = "startup"
        self._wd_free_gb: float = 0.0
        self._active_alerts: dict[str, str] = {}  # component -> message
        self._log_scan_offset: int = 0  # last byte offset scanned in log file

    async def run(self) -> None:
        (self._live_root / "alerts").mkdir(parents=True, exist_ok=True)
        (self._live_root / "snapshots").mkdir(parents=True, exist_ok=True)

        _log.info("health_monitor: starting for %s", date.today())

        # Optional Sentry init
        if dsn := os.environ.get("SENTRY_DSN"):
            try:
                import sentry_sdk
                sentry_sdk.init(
                    dsn=dsn,
                    environment="production",
                    server_name="zimaos",
                    shutdown_timeout=5,
                    traces_sample_rate=0.0,
                    send_default_pii=False,
                )
                _log.info("health_monitor: Sentry initialised")
            except ImportError:
                _log.warning("health_monitor: sentry_sdk not installed — skipping")

        async with aiohttp.ClientSession() as session:
            self._session = session

            await self._send_telegram(f"Health monitor online for {date.today()}", severity="info")

            # Send /start to Healthchecks.io
            if self._hc_url:
                await self._ping_hc(f"{self._hc_url}/start?session_date={self._date_str}&phase=startup")

            tasks = [
                asyncio.create_task(self._critical_loop()),
                asyncio.create_task(self._slow_loop()),
                asyncio.create_task(self._heartbeat_loop()),
            ]
            await self._stop_event.wait()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        await self._eod_summary()

    # ──────────────────────────────────────────────────────────────────────────
    # Critical checks (every 15s)
    # ──────────────────────────────────────────────────────────────────────────

    async def _critical_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._check_process_alive()
                await self._check_collector_heartbeat()
                await self._check_feed_state()
                await self._check_depth_snapshot()
                await self._check_wd_mount()
                if sys.platform != "win32":
                    await self._check_clock_sync()
                self._write_alert_state()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _log.error("critical_loop: unexpected error — %r", exc)
            await asyncio.sleep(_CRITICAL_INTERVAL)

    async def _check_process_alive(self) -> None:
        if not _is_market_hours():
            return
        health_path = self._live_root / "snapshots" / "latest_process_health.json"
        if not health_path.exists():
            await self._alert("critical", "process", "process_health_missing",
                              "latest_process_health.json does not exist during market hours")
            return

        try:
            data = json.loads(health_path.read_text())
            written_at = datetime.fromisoformat(data["written_at"])
            age = (_now_ist() - written_at).total_seconds()
            if age > 30:
                await self._alert("critical", "process", "process_stale",
                                  f"process health stale: {age:.0f}s old")
            else:
                self._clear_alert("process")
        except Exception as exc:
            await self._alert("warning", "process", "process_health_parse_error", str(exc))

    async def _check_collector_heartbeat(self) -> None:
        health_path = self._live_root / "snapshots" / "latest_process_health.json"
        if not health_path.exists():
            return
        try:
            data = json.loads(health_path.read_text())
            written_at = datetime.fromisoformat(data["written_at"])
            age = (_now_ist() - written_at).total_seconds()
            if age > 30:
                await self._alert("critical", "collector", "collector_heartbeat_stale",
                                  f"collector heartbeat stale: {age:.0f}s old")
            else:
                self._clear_alert("collector")
        except Exception:
            pass

    async def _check_feed_state(self) -> None:
        if not _is_feed_active():
            return
        feed_path = self._live_root / "snapshots" / "latest_feed_state.json"
        if not feed_path.exists():
            await self._alert("critical", "feed", "feed_state_missing",
                              "latest_feed_state.json does not exist during feed-active window")
            return
        try:
            data = json.loads(feed_path.read_text())
            written_at = datetime.fromisoformat(data["written_at"])
            age = (_now_ist() - written_at).total_seconds()
            if age > 15:
                await self._alert("critical", "feed", "feed_state_stale",
                                  f"feed state stale: {age:.0f}s old")
            elif not data.get("connected", False):
                await self._alert("critical", "feed", "feed_disconnected", "live feed disconnected")
            else:
                self._clear_alert("feed")
        except Exception as exc:
            await self._alert("warning", "feed", "feed_state_parse_error", str(exc))

    async def _check_depth_snapshot(self) -> None:
        if not _is_market_hours():
            return
        depth_path = self._live_root / "snapshots" / "latest_depth_cache.json"
        if not depth_path.exists():
            # Only alert if we're well into the session
            t = _ist_time()
            if t >= time(9, 30):
                await self._alert("warning", "depth", "depth_snapshot_missing",
                                  "latest_depth_cache.json not found during market hours")
            return
        try:
            data = json.loads(depth_path.read_text())
            written_at = datetime.fromisoformat(data.get("written_at", "2000-01-01"))
            age = (_now_ist() - written_at).total_seconds()
            if age > 15:
                await self._alert("critical", "depth", "depth_snapshot_stale",
                                  f"depth cache snapshot stale: {age:.0f}s old")
            else:
                self._clear_alert("depth")
        except Exception as exc:
            await self._alert("warning", "depth", "depth_snapshot_parse_error", str(exc))

    async def _check_wd_mount(self) -> None:
        try:
            stat = shutil.disk_usage(str(self._live_root))
            self._wd_free_gb = stat.free / (1024 ** 3)
            if self._wd_free_gb < _WD_MIN_GB_INTRADAY:
                await self._alert("critical", "storage", "wd_low_space_critical",
                                  f"WD free: {self._wd_free_gb:.1f} GB (< {_WD_MIN_GB_INTRADAY} GB)")
            elif self._wd_free_gb < _WD_MIN_GB_PRE_RUN:
                await self._alert("warning", "storage", "wd_low_space_warn",
                                  f"WD free: {self._wd_free_gb:.1f} GB (< {_WD_MIN_GB_PRE_RUN} GB)")
            else:
                self._clear_alert("storage")
        except Exception as exc:
            await self._alert("critical", "storage", "wd_mount_error", str(exc))

    async def _check_clock_sync(self) -> None:
        # Check actual drift from timesync-status rather than the NTPSynchronized
        # flag. The flag goes 'no' whenever timesyncd restarts (ZimaOS does this
        # periodically), even if the clock offset is within acceptable bounds.
        try:
            result = subprocess.run(
                ["timedatectl", "timesync-status"], capture_output=True, text=True, timeout=5
            )
            offset_sec: float | None = None
            for line in result.stdout.splitlines():
                if line.strip().startswith("Offset:"):
                    raw = line.split(":", 1)[1].strip()
                    offset_sec = _parse_timesync_offset(raw)
                    break

            if offset_sec is not None:
                if abs(offset_sec) > 2.0:
                    await self._alert(
                        "critical", "clock", "clock_drift_high",
                        f"Clock drift {offset_sec:+.3f}s exceeds 2s threshold",
                    )
                else:
                    self._clear_alert("clock")
                return

            # timesync-status gave no offset (daemon not yet synced after restart).
            # Fall back to the boolean flag — only warn once, not every 15s.
            result2 = subprocess.run(
                ["timedatectl", "show"], capture_output=True, text=True, timeout=5
            )
            synced = any(
                line.startswith("NTPSynchronized=yes")
                for line in result2.stdout.splitlines()
            )
            if not synced:
                await self._alert("warning", "clock", "clock_not_synced",
                                  "NTPSynchronized=no and no offset available (daemon may be starting)")
            else:
                self._clear_alert("clock")
        except Exception as exc:
            _log.debug("clock_sync check failed: %r", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # Slow checks (every 60s)
    # ──────────────────────────────────────────────────────────────────────────

    async def _slow_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._check_wd_free_space_slow()
                if sys.platform != "win32" and self._expected_ip:
                    await self._check_public_ip()
                await self._check_error_log()
                await self._check_token_expiry()
            except asyncio.CancelledError:
                break
            except Exception as exc:
                _log.error("slow_loop: unexpected error — %r", exc)
            await asyncio.sleep(_SLOW_INTERVAL)

    async def _check_wd_free_space_slow(self) -> None:
        # Critical threshold already checked in _check_wd_mount every 15s
        # Pre-run threshold is only relevant for slow checks
        if self._wd_free_gb < _WD_MIN_GB_PRE_RUN and not _is_market_hours():
            await self._alert("critical", "storage_slow", "wd_pre_run_insufficient",
                              f"Pre-run: only {self._wd_free_gb:.1f} GB free (need >= {_WD_MIN_GB_PRE_RUN} GB)")

    async def _check_public_ip(self) -> None:
        try:
            async with self._session.get("https://api.ipify.org", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ip = (await resp.text()).strip()
            if ip != self._expected_ip:
                await self._alert("warning", "network", "public_ip_mismatch",
                                  f"Public IP {ip!r} != expected {self._expected_ip!r}")
            else:
                self._clear_alert("network")
        except Exception as exc:
            _log.debug("public_ip check failed: %r", exc)

    async def _check_error_log(self) -> None:
        log_path = self._live_root / "logs" / f"{self._date_str}.log"
        if not log_path.exists():
            return
        try:
            with open(log_path, "r", errors="replace") as f:
                f.seek(self._log_scan_offset)
                new_content = f.read()
                self._log_scan_offset = f.tell()

            error_lines = [
                line for line in new_content.splitlines()
                if any(kw in line for kw in ("ERROR", "CRITICAL", "Traceback"))
            ]
            if error_lines:
                sample = error_lines[0][:200]
                await self._alert("warning", "log_errors", "new_error_in_log",
                                  f"{len(error_lines)} new error/traceback lines. First: {sample}")
        except Exception as exc:
            _log.debug("error_log check failed: %r", exc)

    async def _check_token_expiry(self) -> None:
        if not self._access_token:
            return
        exp = _jwt_expiry(self._access_token)
        if exp is None:
            return
        remaining = (exp - _now_ist()).total_seconds()
        if remaining < 0:
            await self._alert("critical", "token", "token_expired",
                              "DHAN_ACCESS_TOKEN has expired")
        elif remaining < 7200:  # < 2 hours
            # Only warn in the pre-market window (07:30–09:30 IST). At other times
            # the 08:30 cron will renew the token before market open, so overnight
            # warnings are noise.
            t = _ist_time()
            if time(7, 30) <= t <= time(9, 30):
                await self._alert("warning", "token", "token_expiring_soon",
                                  f"DHAN_ACCESS_TOKEN expires in {remaining / 60:.0f} minutes")
        else:
            self._clear_alert("token")

    # ──────────────────────────────────────────────────────────────────────────
    # Healthchecks.io dead-man-switch
    # ──────────────────────────────────────────────────────────────────────────

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            in_hours = _is_heartbeat_hours()
            interval = _HEARTBEAT_INTERVAL_MARKET if in_hours else _HEARTBEAT_INTERVAL_OFF
            if self._hc_url:
                phase = self._phase
                url = f"{self._hc_url}?session_date={self._date_str}&phase={phase}"
                await self._ping_hc(url)
            await asyncio.sleep(interval)

    async def _ping_hc(self, url: str) -> None:
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                _log.debug("healthchecks.io ping: %d %s", resp.status, url)
        except Exception as exc:
            _log.warning("healthchecks.io ping failed: %r", exc)

    async def _ping_hc_fail(self) -> None:
        if self._hc_url:
            url = f"{self._hc_url}/fail?session_date={self._date_str}&phase={self._phase}"
            await self._ping_hc(url)

    # ──────────────────────────────────────────────────────────────────────────
    # Alert dispatch
    # ──────────────────────────────────────────────────────────────────────────

    async def _alert(self, severity: str, component: str, reason: str, message: str) -> None:
        self._alert_counts[severity] = self._alert_counts.get(severity, 0) + 1
        self._active_alerts[component] = message

        record = {
            "ts": datetime.now(tz=_IST).isoformat(),
            "severity": severity,
            "component": component,
            "reason": reason,
            "message": message,
        }
        self._alerts_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self._alerts_path, "a") as f:
            f.write(json.dumps(record) + "\n")

        _log.log(
            logging.CRITICAL if severity == "critical" else logging.WARNING,
            "alert: [%s] %s — %s", severity.upper(), component, message,
        )

        if _throttle_ok(severity, component, reason):
            emoji_map = {"critical": "[CRITICAL]", "warning": "[WARNING]", "info": "[INFO]"}
            tag = emoji_map.get(severity, "[ALERT]")
            text = f"{tag} {component}: {message}"
            await self._send_telegram(text, severity=severity)

    def _clear_alert(self, component: str) -> None:
        if component in self._active_alerts:
            prev_msg = self._active_alerts.pop(component)
            _log.info("alert_cleared: %s — was: %s", component, prev_msg)
            asyncio.ensure_future(self._send_telegram(f"[RECOVERED] {component}: {prev_msg}", severity="info"))

    async def _send_telegram(self, text: str, severity: str = "info") -> None:
        if not self._tg_token or not self._tg_chat:
            return
        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        payload: dict = {"chat_id": self._tg_chat, "text": text, "parse_mode": "HTML"}
        # Optional: post to a specific topic in a Telegram forum-group
        if self._tg_thread:
            payload["message_thread_id"] = self._tg_thread
        try:
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _log.warning("telegram: HTTP %d", resp.status)
        except Exception as exc:
            _log.warning("telegram: send failed — %r", exc)

    def _write_alert_state(self) -> None:
        state = {
            "written_at": datetime.now(tz=_IST).isoformat(),
            "phase": self._phase,
            "active_alerts": dict(self._active_alerts),
            "alert_counts": dict(self._alert_counts),
            "wd_free_gb": round(self._wd_free_gb, 2),
        }
        try:
            tmp = self._alert_state_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=2))
            tmp.replace(self._alert_state_path)
        except Exception as exc:
            _log.debug("write_alert_state failed: %r", exc)

    # ──────────────────────────────────────────────────────────────────────────
    # EOD summary
    # ──────────────────────────────────────────────────────────────────────────

    async def _eod_summary(self) -> None:
        trades_path = self._live_root / "paper_trades" / f"{self._date_str}.json"
        report_path = self._live_root / "reports" / f"{self._date_str}_paper_summary.md"

        total_alerts = sum(self._alert_counts.values())
        msg = (
            f"[EOD] Health monitor summary {date.today()}\n"
            f"Alerts: critical={self._alert_counts.get('critical', 0)} "
            f"warning={self._alert_counts.get('warning', 0)}\n"
            f"WD free: {self._wd_free_gb:.1f} GB\n"
            f"Artifacts: {trades_path.name}, {report_path.name}"
        )
        await self._send_telegram(msg, severity="info")
        _log.info("eod_summary: %s", msg.replace("\n", " | "))


async def _alarm_drill() -> bool:
    """
    Test all notification channels and return True if all configured channels pass.

    Run before starting a live session to verify the alert chain:
        python scripts/live/health_monitor.py --test-alerts
    """
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    tg_thread = os.environ.get("TELEGRAM_THREAD_ID", "")
    hc_url = os.environ.get("EXTERNAL_HEARTBEAT_URL", "")
    sentry_dsn = os.environ.get("SENTRY_DSN", "")

    results: dict[str, str] = {}

    async with aiohttp.ClientSession() as session:
        # ── Telegram ──────────────────────────────────────────────────────────
        if tg_token and tg_chat:
            try:
                url = f"https://api.telegram.org/bot{tg_token}/sendMessage"
                payload: dict = {
                    "chat_id": tg_chat,
                    "text": "[TEST] Health monitor alarm drill — Telegram alerts working.",
                    "parse_mode": "HTML",
                }
                if tg_thread:
                    payload["message_thread_id"] = tg_thread
                async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    results["telegram"] = "PASS" if resp.status == 200 else f"FAIL HTTP {resp.status}"
            except Exception as exc:
                results["telegram"] = f"FAIL {exc}"
        else:
            results["telegram"] = "SKIP (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)"

        # ── Healthchecks.io ───────────────────────────────────────────────────
        if hc_url:
            try:
                async with session.get(f"{hc_url}/start", timeout=aiohttp.ClientTimeout(total=10)) as r1:
                    start_ok = r1.status == 200
                await asyncio.sleep(2)
                async with session.get(f"{hc_url}/fail", timeout=aiohttp.ClientTimeout(total=10)) as r2:
                    fail_ok = r2.status == 200
                # Recover immediately so the check goes green again
                await asyncio.sleep(2)
                async with session.get(hc_url, timeout=aiohttp.ClientTimeout(total=10)) as r3:
                    recover_ok = r3.status == 200
                results["healthchecks"] = (
                    f"PASS (start={start_ok} fail={fail_ok} recover={recover_ok})"
                    if start_ok and fail_ok and recover_ok
                    else f"PARTIAL start={start_ok} fail={fail_ok} recover={recover_ok}"
                )
            except Exception as exc:
                results["healthchecks"] = f"FAIL {exc}"
        else:
            results["healthchecks"] = "SKIP (EXTERNAL_HEARTBEAT_URL not set)"

        # ── Sentry ────────────────────────────────────────────────────────────
        if sentry_dsn:
            try:
                import sentry_sdk
                sentry_sdk.init(
                    dsn=sentry_dsn,
                    environment="test",
                    server_name="zimaos",
                    shutdown_timeout=5,
                    traces_sample_rate=0.0,
                    send_default_pii=False,
                )
                sentry_sdk.capture_message("Alarm drill: Sentry connectivity test", level="info")
                sentry_sdk.flush(timeout=5)
                results["sentry"] = "PASS (event sent)"
            except ImportError:
                results["sentry"] = "SKIP (sentry_sdk not installed)"
            except Exception as exc:
                results["sentry"] = f"FAIL {exc}"
        else:
            results["sentry"] = "SKIP (SENTRY_DSN not set)"

    all_ok = True
    for channel, status in results.items():
        prefix = "✓" if status.startswith("PASS") else ("~" if status.startswith("SKIP") else "✗")
        print(f"  {prefix} {channel:15s}: {status}")
        if status.startswith("FAIL"):
            all_ok = False

    return all_ok


def main() -> None:
    parser = argparse.ArgumentParser(description="Wing-6 health monitor")
    parser.add_argument("--profile", default="wing6_4x1_all_vix_filtered")
    parser.add_argument(
        "--test-alerts",
        action="store_true",
        help="Run alarm drill: test Telegram, Healthchecks.io, and Sentry; then exit",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    if args.test_alerts:
        print("Alarm drill — testing all notification channels:")
        ok = asyncio.run(_alarm_drill())
        sys.exit(0 if ok else 1)

    cfg_path = _repo_root / "configs" / "live" / f"{args.profile}.json"
    if not cfg_path.exists():
        _log.error("profile not found: %s", cfg_path)
        sys.exit(1)
    profile = json.loads(cfg_path.read_text())

    try:
        live_root = _resolve_live_root()
    except RuntimeError as exc:
        _log.error("live_root validation failed: %s", exc)
        sys.exit(1)

    monitor = HealthMonitor(profile=profile, live_root=live_root)
    asyncio.run(monitor.run())


if __name__ == "__main__":
    main()

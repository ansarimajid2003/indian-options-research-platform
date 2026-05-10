"""
Independent uptime and integrity monitor for Wing-6 paper trading.

Runs as its own process. Does not open Dhan websockets or place orders.

Usage:
    python scripts/live/health_monitor.py --profile wing6_4x1_all_vix_filtered
    python scripts/live/health_monitor.py --profile wing6_4x1_all_vix_filtered --once
    python scripts/live/health_monitor.py --test-alerts

Env vars:
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID - Telegram alerts
    EXTERNAL_HEARTBEAT_URL              - Healthchecks.io dead-man-switch
    SENTRY_DSN                          - optional Sentry integration
    EXPECTED_PUBLIC_IP                  - optional public IP validation on Linux
    DHAN_ACCESS_TOKEN or DHAN_TOKEN      - JWT token expiry check
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
from urllib.parse import urlencode
from zoneinfo import ZoneInfo

import aiohttp

_repo_root = Path(__file__).parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from options_backtest.calendar import is_trading_day as _is_trading_day

_IST = ZoneInfo("Asia/Kolkata")
_log = logging.getLogger(__name__)

_CRITICAL_INTERVAL = 15.0
_SLOW_INTERVAL = 60.0
_HEARTBEAT_INTERVAL_MARKET = 60.0
# Healthchecks.io currently has this check configured with a 1-minute period.
# Keep off-hours pings comfortably inside that window to avoid UP/DOWN flapping.
_HEARTBEAT_INTERVAL_OFF = 60.0

_MARKET_OPEN = time(9, 0)
_MARKET_CLOSE = time(15, 35)
_FEED_ACTIVE_START = time(9, 10)
_FEED_ACTIVE_END = time(15, 31)
_HEARTBEAT_START = time(8, 55)
_EOD_BUFFER_END = time(16, 5)

_WD_MIN_GB_INTRADAY = 20.0
_WD_MIN_GB_PRE_RUN = 100.0
_QUOTE_FRESHNESS_MIN_PCT = 95.0
_DEPTH_READY_MIN_PCT = 95.0
_RAW_FLUSH_MAX_AGE_SECONDS = 90.0
_PARQUET_FLUSH_MAX_AGE_SECONDS = 180.0
_CLOCK_BOOT_GRACE_SECONDS = 300.0

_ALERT_THROTTLE: dict[tuple[str, str, str], float] = {}


def _now_ist() -> datetime:
    return datetime.now(tz=_IST)


def _today_ist() -> date:
    return _now_ist().date()


def _ist_time() -> time:
    return _now_ist().time()


def _is_market_hours() -> bool:
    if not _is_trading_day(_today_ist()):
        return False
    t = _ist_time()
    return _MARKET_OPEN <= t <= _MARKET_CLOSE


def _is_feed_active() -> bool:
    if not _is_trading_day(_today_ist()):
        return False
    t = _ist_time()
    return _FEED_ACTIVE_START <= t <= _FEED_ACTIVE_END


def _is_heartbeat_hours() -> bool:
    if not _is_trading_day(_today_ist()):
        return False
    t = _ist_time()
    return _HEARTBEAT_START <= t <= _MARKET_CLOSE


def _is_preflight_window() -> bool:
    if not _is_trading_day(_today_ist()):
        return False
    t = _ist_time()
    return _HEARTBEAT_START <= t < _MARKET_OPEN


def _throttle_ok(severity: str, component: str, reason: str) -> bool:
    key = (severity, component, reason)
    now = _time.monotonic()
    last = _ALERT_THROTTLE.get(key, 0.0)
    t = _ist_time()
    window = 120.0 if (time(9, 10) <= t <= time(9, 30)) or (time(15, 15) <= t <= time(15, 30)) else 600.0
    if now - last >= window:
        _ALERT_THROTTLE[key] = now
        return True
    return False


def _scrub_message(text: str) -> str:
    text = re.sub(r"https?://\S+", "[REDACTED_URL]", text)
    text = re.sub(r"(?i)(token|clientId|client_id|access-token)=\S+", r"\1=[REDACTED]", text)
    text = re.sub(r"[A-Za-z0-9_\-]{80,}", "[REDACTED_TOKEN]", text)
    return text


def _parse_timesync_offset(val: str) -> float | None:
    match = re.match(r"^([+-]?\d+\.?\d*)(s|ms|us|ns)$", val.strip())
    if not match:
        return None
    num, unit = float(match.group(1)), match.group(2)
    return num * {"s": 1.0, "ms": 1e-3, "us": 1e-6, "ns": 1e-9}[unit]


def _jwt_expiry(token: str) -> datetime | None:
    try:
        parts = token.split(".")
        if len(parts) < 2:
            return None
        payload_b64 = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_b64.encode("ascii")))
        exp = payload.get("exp")
        if exp is None:
            return None
        return datetime.fromtimestamp(int(exp), tz=_IST)
    except Exception:
        return None


def _write_atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _write_atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
        os.fsync(f.fileno())
    tmp.replace(path)


def _resolve_live_root() -> Path:
    live_path = _repo_root / "data" / "live"
    if sys.platform != "win32":
        resolved = live_path.resolve()
        if not str(resolved).startswith("/media/WD-Storage"):
            raise RuntimeError(f"data/live resolves to {resolved}, expected /media/WD-Storage/...")
    live_path.mkdir(parents=True, exist_ok=True)
    return live_path


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=_IST)
        return parsed.astimezone(_IST)
    except Exception:
        return None


def _newest_file(root: Path, patterns: tuple[str, ...]) -> Path | None:
    newest: Path | None = None
    newest_mtime = -1.0
    if not root.exists():
        return None
    for pattern in patterns:
        for path in root.rglob(pattern):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if mtime > newest_mtime:
                newest = path
                newest_mtime = mtime
    return newest


def _boot_uptime_seconds() -> float | None:
    try:
        return float(Path("/proc/uptime").read_text(encoding="utf-8").split()[0])
    except Exception:
        return None


class HealthMonitor:
    """Runs health checks, alerting, heartbeat pings, and daily uptime summaries."""

    def __init__(self, profile: dict, live_root: Path) -> None:
        self._profile = profile
        self._live_root = live_root
        self._session_date = _today_ist()
        self._date_str = self._session_date.strftime("%Y%m%d")
        self._alerts_path = live_root / "alerts" / f"{self._date_str}_alerts.jsonl"
        self._external_heartbeat_path = live_root / "alerts" / f"{self._date_str}_external_heartbeat.jsonl"
        self._alert_state_path = live_root / "snapshots" / "latest_alert_state.json"
        self._uptime_summary_path = live_root / "reports" / f"{self._date_str}_uptime_summary.md"

        self._tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        self._tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
        self._tg_thread = os.environ.get("TELEGRAM_THREAD_ID", "")
        self._hc_url = os.environ.get("EXTERNAL_HEARTBEAT_URL", "")
        self._expected_ip = os.environ.get("EXPECTED_PUBLIC_IP", "")
        self._access_token = os.environ.get("DHAN_ACCESS_TOKEN", "") or os.environ.get("DHAN_TOKEN", "")

        self._session: aiohttp.ClientSession | None = None
        self._stop_event = asyncio.Event()
        self._phase = "startup"
        self._started_monotonic = _time.monotonic()
        self._good_ticks = 0
        self._total_ticks = 0
        self._wd_free_gb = 0.0
        self._alert_counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        self._active_alerts: dict[str, dict] = {}
        self._consecutive_bad: dict[str, int] = {"quote_freshness": 0, "depth_readiness": 0}
        self._log_scan_offset = 0
        self._backend_open_sent_for: date | None = None
        self._eod_sent_for: date | None = None
        self._heartbeat_started_for: date | None = None
        self._heartbeat_failed_for: date | None = None
        self._last_external_heartbeat_at: str | None = None
        self._last_external_heartbeat_status: str = "not_sent"

    async def run(self) -> None:
        self._ensure_dirs()
        self._init_sentry()
        async with aiohttp.ClientSession() as session:
            self._session = session
            await self._send_telegram(f"Health monitor online for {self._session_date}", severity="info")
            await self._run_start_heartbeat_if_due()
            tasks = [
                asyncio.create_task(self._critical_loop()),
                asyncio.create_task(self._slow_loop()),
                asyncio.create_task(self._heartbeat_loop()),
                asyncio.create_task(self._summary_loop()),
            ]
            await self._stop_event.wait()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

    async def run_once(self) -> None:
        self._ensure_dirs()
        async with aiohttp.ClientSession() as session:
            self._session = session
            await self._run_critical_checks()
            await self._run_slow_checks()
            self._write_alert_state()

    def _ensure_dirs(self) -> None:
        for subdir in ("alerts", "snapshots", "reports", "logs"):
            (self._live_root / subdir).mkdir(parents=True, exist_ok=True)

    def _init_sentry(self) -> None:
        dsn = os.environ.get("SENTRY_DSN")
        if not dsn:
            return
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
            _log.warning("health_monitor: sentry_sdk not installed - skipping")

    def _roll_date_if_needed(self) -> None:
        today = _today_ist()
        if today == self._session_date:
            return
        self._session_date = today
        self._date_str = today.strftime("%Y%m%d")
        self._alerts_path = self._live_root / "alerts" / f"{self._date_str}_alerts.jsonl"
        self._external_heartbeat_path = self._live_root / "alerts" / f"{self._date_str}_external_heartbeat.jsonl"
        self._uptime_summary_path = self._live_root / "reports" / f"{self._date_str}_uptime_summary.md"
        self._alert_counts = {"critical": 0, "warning": 0, "info": 0}
        self._active_alerts = {}
        self._consecutive_bad = {"quote_freshness": 0, "depth_readiness": 0}
        self._good_ticks = 0
        self._total_ticks = 0
        self._started_monotonic = _time.monotonic()
        self._log_scan_offset = 0

    async def _critical_loop(self) -> None:
        while not self._stop_event.is_set():
            self._roll_date_if_needed()
            await self._run_critical_checks()
            await asyncio.sleep(_CRITICAL_INTERVAL)

    async def _slow_loop(self) -> None:
        while not self._stop_event.is_set():
            self._roll_date_if_needed()
            await self._run_slow_checks()
            await asyncio.sleep(_SLOW_INTERVAL)

    async def _summary_loop(self) -> None:
        while not self._stop_event.is_set():
            self._roll_date_if_needed()
            await self._maybe_send_backend_healthy()
            await self._maybe_send_eod_summary()
            self._write_alert_state()
            await asyncio.sleep(30)

    async def _run_critical_checks(self) -> None:
        try:
            results = [
                await self._check_runner_process(),
                await self._check_collector_heartbeat(),
                await self._check_feed_state(),
                await self._check_depth_snapshot(),
                await self._check_quote_freshness(),
                await self._check_depth_readiness(),
                await self._check_wd_mount(),
            ]
            if sys.platform != "win32":
                results.append(await self._check_clock_sync())
            self._total_ticks += 1
            if all(result for result in results if result is not None):
                self._good_ticks += 1
            self._write_alert_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("critical checks failed unexpectedly: %r", exc)

    async def _run_slow_checks(self) -> None:
        try:
            await self._check_raw_packet_flush()
            await self._check_parquet_flush()
            await self._check_wd_free_space_slow()
            if sys.platform != "win32" and self._expected_ip:
                await self._check_public_ip()
            await self._check_token_expiry()
            await self._check_error_log()
            self._write_alert_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("slow checks failed unexpectedly: %r", exc)

    async def _check_runner_process(self) -> bool | None:
        if not _is_market_hours():
            await self._clear_alert("process", "runner_not_active")
            await self._clear_alert("process", "process_health_missing")
            await self._clear_alert("process", "process_stale")
            return None

        systemd_active = False
        if sys.platform != "win32" and shutil.which("systemctl"):
            result = subprocess.run(
                ["systemctl", "is-active", "--quiet", "live-paper.service"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            systemd_active = result.returncode == 0

        snapshot_active = False
        health_path = self._live_root / "snapshots" / "latest_process_health.json"
        if health_path.exists():
            try:
                data = json.loads(health_path.read_text(encoding="utf-8"))
                written_at = _parse_ts(data.get("written_at"))
                if written_at and (_now_ist() - written_at).total_seconds() <= 30:
                    snapshot_active = True
            except Exception as exc:
                await self._alert("warning", "process", "process_health_parse_error", str(exc))

        if systemd_active or snapshot_active:
            await self._clear_alert("process", "runner_not_active")
            await self._clear_alert("process", "process_health_missing")
            await self._clear_alert("process", "process_stale")
            return True

        if not health_path.exists():
            await self._alert(
                "critical",
                "process",
                "process_health_missing",
                "latest_process_health.json missing and live-paper service is not active",
            )
        else:
            await self._alert("critical", "process", "process_stale", "runner process health is stale or inactive")
        return False

    async def _check_collector_heartbeat(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("collector", "collector_heartbeat_missing")
            await self._clear_alert("collector", "collector_heartbeat_stale")
            return None
        path = self._live_root / "snapshots" / "latest_depth_cache.json"
        if not path.exists():
            await self._alert(
                "critical",
                "collector",
                "collector_heartbeat_missing",
                "latest_depth_cache.json missing during feed-active window",
            )
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            written_at = _parse_ts(data.get("written_at"))
            age = (_now_ist() - written_at).total_seconds() if written_at else 999999.0
            if age > 30:
                await self._alert("critical", "collector", "collector_heartbeat_stale", f"collector heartbeat stale: {age:.0f}s old")
                return False
            await self._clear_alert("collector", "collector_heartbeat_missing")
            await self._clear_alert("collector", "collector_heartbeat_stale")
            return True
        except Exception as exc:
            await self._alert("warning", "collector", "collector_heartbeat_parse_error", str(exc))
            return False

    async def _check_feed_state(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("feed", "feed_state_missing")
            await self._clear_alert("feed", "feed_state_stale")
            await self._clear_alert("feed", "feed_disconnected")
            return None
        data = self._load_json_snapshot("latest_feed_state.json")
        if data is None:
            await self._alert("critical", "feed", "feed_state_missing", "latest_feed_state.json missing during feed-active window")
            return False
        written_at = _parse_ts(data.get("written_at"))
        age = (_now_ist() - written_at).total_seconds() if written_at else 999999.0
        if age > 15:
            await self._alert("critical", "feed", "feed_state_stale", f"feed state stale: {age:.0f}s old")
            return False
        if not data.get("connected", False):
            await self._alert("critical", "feed", "feed_disconnected", "live feed disconnected")
            return False
        await self._clear_alert("feed", "feed_state_missing")
        await self._clear_alert("feed", "feed_state_stale")
        await self._clear_alert("feed", "feed_disconnected")
        return True

    async def _check_depth_snapshot(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("depth", "depth_snapshot_missing")
            await self._clear_alert("depth", "depth_snapshot_stale")
            return None
        data = self._load_json_snapshot("latest_depth_cache.json")
        if data is None:
            await self._alert("critical", "depth", "depth_snapshot_missing", "latest_depth_cache.json missing during feed-active window")
            return False
        written_at = _parse_ts(data.get("written_at"))
        age = (_now_ist() - written_at).total_seconds() if written_at else 999999.0
        if age > 15:
            await self._alert("critical", "depth", "depth_snapshot_stale", f"depth cache snapshot stale: {age:.0f}s old")
            return False
        await self._clear_alert("depth", "depth_snapshot_missing")
        await self._clear_alert("depth", "depth_snapshot_stale")
        return True

    async def _check_quote_freshness(self) -> bool | None:
        if not _is_feed_active():
            self._consecutive_bad["quote_freshness"] = 0
            await self._clear_alert("quote_freshness", "quote_freshness_low")
            return None
        data = self._load_json_snapshot("latest_feed_state.json")
        if data is None:
            return False
        pct = float(data.get("quote_freshness_pct", 0.0))
        if pct < _QUOTE_FRESHNESS_MIN_PCT:
            self._consecutive_bad["quote_freshness"] += 1
            if self._consecutive_bad["quote_freshness"] >= 2:
                await self._alert("critical", "quote_freshness", "quote_freshness_low", f"fresh quotes {pct:.1f}% (< 95%) for 2 consecutive checks")
            return False
        self._consecutive_bad["quote_freshness"] = 0
        await self._clear_alert("quote_freshness", "quote_freshness_low")
        return True

    async def _check_depth_readiness(self) -> bool | None:
        if not _is_feed_active():
            self._consecutive_bad["depth_readiness"] = 0
            await self._clear_alert("depth_readiness", "depth_ready_low")
            return None
        data = self._load_json_snapshot("latest_depth_cache.json")
        if data is None:
            return False
        pct = float(data.get("ready_pct", data.get("depth_ready_pct", 0.0)))
        if pct < _DEPTH_READY_MIN_PCT:
            self._consecutive_bad["depth_readiness"] += 1
            if self._consecutive_bad["depth_readiness"] >= 2:
                await self._alert("critical", "depth_readiness", "depth_ready_low", f"depth ready {pct:.1f}% (< 95%) for 2 consecutive checks")
            return False
        self._consecutive_bad["depth_readiness"] = 0
        await self._clear_alert("depth_readiness", "depth_ready_low")
        return True

    async def _check_wd_mount(self) -> bool:
        try:
            if sys.platform != "win32":
                resolved = self._live_root.resolve()
                if not str(resolved).startswith("/media/WD-Storage"):
                    await self._alert("critical", "storage", "wd_mount_invalid", f"data/live resolves to {resolved}")
                    return False
            probe = self._live_root / "snapshots" / ".health_write_probe"
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            stat = shutil.disk_usage(str(self._live_root))
            self._wd_free_gb = stat.free / (1024 ** 3)
            if self._wd_free_gb < _WD_MIN_GB_INTRADAY:
                await self._alert("critical", "storage", "wd_low_space_critical", f"WD free {self._wd_free_gb:.1f} GB (< 20 GB)")
                return False
            await self._clear_alert("storage", "wd_mount_invalid")
            await self._clear_alert("storage", "wd_low_space_critical")
            return True
        except Exception as exc:
            await self._alert("critical", "storage", "wd_mount_error", str(exc))
            return False

    async def _check_clock_sync(self) -> bool | None:
        try:
            result = subprocess.run(["timedatectl", "timesync-status"], capture_output=True, text=True, timeout=5)
            offset_sec: float | None = None
            for line in result.stdout.splitlines():
                if line.strip().startswith("Offset:"):
                    offset_sec = _parse_timesync_offset(line.split(":", 1)[1].strip())
                    break
            if offset_sec is not None:
                if abs(offset_sec) > 2.0:
                    await self._alert("critical", "clock", "clock_drift_high", f"clock drift {offset_sec:+.3f}s exceeds 2s")
                    return False
                await self._clear_alert("clock", "clock_drift_high")
                await self._clear_alert("clock", "clock_not_synced")
                return True

            result = subprocess.run(["timedatectl", "show"], capture_output=True, text=True, timeout=5)
            synced = any(line.startswith("NTPSynchronized=yes") for line in result.stdout.splitlines())
            if not synced:
                uptime = _boot_uptime_seconds()
                if uptime is not None and uptime < _CLOCK_BOOT_GRACE_SECONDS:
                    _log.info("clock sync still settling after boot: uptime=%.0fs", uptime)
                    return None
                await self._alert("critical", "clock", "clock_not_synced", "timedatectl reports NTPSynchronized=no")
                return False
            await self._clear_alert("clock", "clock_not_synced")
            return True
        except FileNotFoundError:
            return None
        except Exception as exc:
            _log.debug("clock sync check failed: %r", exc)
            return None

    async def _check_raw_packet_flush(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("raw_packets", "raw_packet_flush_stale")
            await self._clear_alert("raw_packets", "raw_packet_missing")
            return None
        newest = _newest_file(self._live_root / "raw_depth_packets" / self._date_str, ("*.bin",))
        if newest is None:
            await self._alert("warning", "raw_packets", "raw_packet_missing", "no raw depth packet file found for today")
            return False
        age = _time.time() - newest.stat().st_mtime
        if age > _RAW_FLUSH_MAX_AGE_SECONDS:
            await self._alert("warning", "raw_packets", "raw_packet_flush_stale", f"newest raw packet flush is {age:.0f}s old")
            return False
        await self._clear_alert("raw_packets", "raw_packet_missing")
        await self._clear_alert("raw_packets", "raw_packet_flush_stale")
        return True

    async def _check_parquet_flush(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("parquet", "parquet_flush_stale")
            await self._clear_alert("parquet", "parquet_missing")
            return None
        newest = _newest_file(self._live_root / "order_book" / self._date_str, ("*.parquet",))
        if newest is None:
            await self._alert("warning", "parquet", "parquet_missing", "no normalized order-book parquet file found for today")
            return False
        age = _time.time() - newest.stat().st_mtime
        if age > _PARQUET_FLUSH_MAX_AGE_SECONDS:
            await self._alert("warning", "parquet", "parquet_flush_stale", f"newest parquet flush is {age:.0f}s old")
            return False
        await self._clear_alert("parquet", "parquet_missing")
        await self._clear_alert("parquet", "parquet_flush_stale")
        return True

    async def _check_wd_free_space_slow(self) -> bool:
        if self._wd_free_gb < _WD_MIN_GB_PRE_RUN and not _is_market_hours():
            await self._alert("warning", "storage", "wd_low_space_warn", f"pre-run WD free {self._wd_free_gb:.1f} GB (< 100 GB)")
            return False
        await self._clear_alert("storage", "wd_low_space_warn")
        return True

    async def _check_public_ip(self) -> bool:
        assert self._session is not None
        try:
            async with self._session.get("https://api.ipify.org", timeout=aiohttp.ClientTimeout(total=10)) as resp:
                ip = (await resp.text()).strip()
            if ip != self._expected_ip:
                severity = "critical" if _is_preflight_window() else "warning"
                await self._alert(severity, "network", "public_ip_mismatch", f"public IP {ip!r} does not match expected whitelist")
                return False
            await self._clear_alert("network", "public_ip_mismatch")
            return True
        except Exception as exc:
            await self._alert("warning", "network", "public_ip_check_failed", str(exc))
            return False

    async def _check_token_expiry(self) -> bool | None:
        if not self._access_token:
            return None
        exp = _jwt_expiry(self._access_token)
        if exp is None:
            return None
        eod_buffer = datetime.combine(self._session_date, _EOD_BUFFER_END, tzinfo=_IST)
        remaining = (exp - _now_ist()).total_seconds()
        if exp < eod_buffer:
            await self._alert("critical", "token", "token_expires_before_eod", "Dhan token expires before planned EOD buffer")
            await self._send_preflight_fail_if_needed()
            return False
        if remaining < 7200 and time(7, 30) <= _ist_time() <= time(9, 30):
            await self._alert("warning", "token", "token_expiring_soon", f"Dhan token expires in {remaining / 60:.0f} minutes")
            return False
        await self._clear_alert("token", "token_expires_before_eod")
        await self._clear_alert("token", "token_expiring_soon")
        return True

    async def _check_error_log(self) -> bool | None:
        log_path = self._live_root / "logs" / f"{self._date_str}.log"
        if not log_path.exists():
            return None
        try:
            with log_path.open("r", errors="replace", encoding="utf-8") as f:
                f.seek(self._log_scan_offset)
                new_content = f.read()
                self._log_scan_offset = f.tell()
            error_lines = [line for line in new_content.splitlines() if any(word in line for word in ("ERROR", "CRITICAL", "Traceback"))]
            if error_lines:
                sample = _scrub_message(error_lines[0][:200])
                await self._alert("warning", "log_errors", "new_error_in_log", f"{len(error_lines)} new error lines. First: {sample}")
                return False
            return True
        except Exception as exc:
            _log.debug("error log scan failed: %r", exc)
            return None

    def _load_json_snapshot(self, name: str) -> dict | None:
        path = self._live_root / "snapshots" / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    async def _maybe_send_backend_healthy(self) -> None:
        if self._backend_open_sent_for == self._session_date:
            return
        if not _is_feed_active():
            return
        if self._active_alerts:
            return
        feed = self._load_json_snapshot("latest_feed_state.json") or {}
        depth = self._load_json_snapshot("latest_depth_cache.json") or {}
        if feed.get("connected") and float(feed.get("quote_freshness_pct", 0.0)) >= 95.0 and float(depth.get("ready_pct", 0.0)) >= 95.0:
            await self._send_telegram("Backend healthy at market open", severity="info")
            self._backend_open_sent_for = self._session_date

    async def _maybe_send_eod_summary(self) -> None:
        if self._eod_sent_for == self._session_date:
            return
        if not _is_trading_day(self._session_date) or _ist_time() < _MARKET_CLOSE:
            return
        await self._eod_summary()
        self._eod_sent_for = self._session_date

    async def _heartbeat_loop(self) -> None:
        while not self._stop_event.is_set():
            self._roll_date_if_needed()
            await self._run_start_heartbeat_if_due()
            interval = _HEARTBEAT_INTERVAL_MARKET if _is_heartbeat_hours() else _HEARTBEAT_INTERVAL_OFF
            if self._hc_url:
                await self._ping_hc("success", {"session_date": self._date_str, "phase": self._phase})
            await asyncio.sleep(interval)

    async def _run_start_heartbeat_if_due(self) -> None:
        if not self._hc_url or self._heartbeat_started_for == self._session_date:
            return
        if _is_heartbeat_hours() or _is_preflight_window():
            await self._ping_hc("start", {"session_date": self._date_str, "phase": "startup"})
            self._heartbeat_started_for = self._session_date

    async def _send_preflight_fail_if_needed(self) -> None:
        if not self._hc_url or not _is_preflight_window() or self._heartbeat_failed_for == self._session_date:
            return
        await self._ping_hc("fail", {"session_date": self._date_str, "phase": "preflight"})
        self._heartbeat_failed_for = self._session_date

    async def _ping_hc(self, kind: str, params: dict[str, str] | None = None) -> None:
        if not self._hc_url or self._session is None:
            return
        suffix = "" if kind == "success" else f"/{kind}"
        query = f"?{urlencode(params or {})}" if params else ""
        url = f"{self._hc_url.rstrip('/')}{suffix}{query}"
        status = "failed"
        http_status: int | None = None
        try:
            async with self._session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                http_status = resp.status
                status = "ok" if 200 <= resp.status < 300 else f"http_{resp.status}"
            if status == "ok":
                self._last_external_heartbeat_at = _now_ist().isoformat()
            self._last_external_heartbeat_status = status
        except Exception as exc:
            self._last_external_heartbeat_status = f"failed:{type(exc).__name__}"
            _log.warning("healthchecks.io %s ping failed: %r", kind, exc)
        finally:
            self._write_external_heartbeat_record(kind, status, http_status)

    def _write_external_heartbeat_record(self, kind: str, status: str, http_status: int | None) -> None:
        record = {
            "ts": _now_ist().isoformat(),
            "kind": kind,
            "status": status,
            "http_status": http_status,
            "session_date": self._date_str,
            "phase": self._phase,
        }
        self._external_heartbeat_path.parent.mkdir(parents=True, exist_ok=True)
        with self._external_heartbeat_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

    async def _alert(self, severity: str, component: str, reason: str, message: str) -> None:
        message = _scrub_message(message)
        key = f"{severity}:{component}:{reason}"
        self._alert_counts[severity] = self._alert_counts.get(severity, 0) + 1
        self._active_alerts[key] = {
            "severity": severity,
            "component": component,
            "reason": reason,
            "message": message,
            "last_seen": _now_ist().isoformat(),
        }
        record = {
            "ts": _now_ist().isoformat(),
            "severity": severity,
            "component": component,
            "reason": reason,
            "message": message,
        }
        self._alerts_path.parent.mkdir(parents=True, exist_ok=True)
        with self._alerts_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")

        level = logging.CRITICAL if severity == "critical" else logging.WARNING if severity == "warning" else logging.INFO
        _log.log(level, "alert: [%s] %s %s - %s", severity.upper(), component, reason, message)

        if severity == "critical" and _is_preflight_window():
            await self._send_preflight_fail_if_needed()

        if _throttle_ok(severity, component, reason):
            await self._send_telegram(f"[{severity.upper()}] {component}/{reason}: {message}", severity=severity)

    async def _clear_alert(self, component: str, reason: str) -> None:
        matching = [key for key, record in self._active_alerts.items() if record["component"] == component and record["reason"] == reason]
        for key in matching:
            record = self._active_alerts.pop(key)
            await self._send_telegram(f"[RECOVERED] {component}/{reason}: {record['message']}", severity="info")

    async def _send_telegram(self, text: str, severity: str = "info") -> None:
        if not self._tg_token or not self._tg_chat or self._session is None:
            return
        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        payload: dict[str, str | int] = {
            "chat_id": self._tg_chat,
            "text": _scrub_message(text),
            "parse_mode": "HTML",
        }
        if self._tg_thread:
            payload["message_thread_id"] = int(self._tg_thread)
        try:
            async with self._session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                if resp.status != 200:
                    _log.warning("telegram send failed: HTTP %d", resp.status)
        except Exception as exc:
            _log.warning("telegram send failed: %r", exc)

    def _write_alert_state(self) -> None:
        uptime_pct = round(self._good_ticks / self._total_ticks * 100, 2) if self._total_ticks else 0.0
        state = {
            "written_at": _now_ist().isoformat(),
            "session_date": self._session_date.isoformat(),
            "phase": self._phase,
            "uptime_pct": uptime_pct,
            "active_alerts": list(self._active_alerts.values()),
            "alert_counts": dict(self._alert_counts),
            "wd_free_gb": round(self._wd_free_gb, 2),
            "latest_external_heartbeat_at": self._last_external_heartbeat_at,
            "latest_external_heartbeat_status": self._last_external_heartbeat_status,
            "artifacts": {
                "alerts": str(self._alerts_path),
                "external_heartbeat": str(self._external_heartbeat_path),
                "uptime_summary": str(self._uptime_summary_path),
            },
        }
        _write_atomic_json(self._alert_state_path, state)

    async def _eod_summary(self) -> None:
        uptime_pct = round(self._good_ticks / self._total_ticks * 100, 2) if self._total_ticks else 0.0
        data_gap_minutes = self._data_gap_minutes()
        paper_trades = self._live_root / "paper_trades" / f"{self._date_str}.json"
        paper_summary = self._live_root / "reports" / f"{self._date_str}_paper_summary.md"
        lines = [
            f"# Health Monitor Uptime Summary - {self._session_date}",
            "",
            f"- Uptime: {uptime_pct:.2f}%",
            f"- Alert counts: critical={self._alert_counts.get('critical', 0)}, warning={self._alert_counts.get('warning', 0)}, info={self._alert_counts.get('info', 0)}",
            f"- Data-gap minutes: {data_gap_minutes:.1f}",
            f"- WD free space: {self._wd_free_gb:.1f} GB",
            f"- Latest external heartbeat: {self._last_external_heartbeat_at or 'not sent'} ({self._last_external_heartbeat_status})",
            "",
            "## Artifacts",
            "",
            f"- Alerts: {self._alerts_path}",
            f"- Alert state: {self._alert_state_path}",
            f"- External heartbeat log: {self._external_heartbeat_path}",
            f"- Paper trades: {paper_trades}",
            f"- Paper summary: {paper_summary}",
        ]
        _write_atomic_text(self._uptime_summary_path, "\n".join(lines) + "\n")
        msg = (
            f"[EOD] Health monitor summary {self._session_date}\n"
            f"Uptime: {uptime_pct:.2f}%\n"
            f"Alerts: critical={self._alert_counts.get('critical', 0)} warning={self._alert_counts.get('warning', 0)}\n"
            f"Data gaps: {data_gap_minutes:.1f} min\n"
            f"WD free: {self._wd_free_gb:.1f} GB\n"
            f"Artifacts: {self._uptime_summary_path.name}, {paper_summary.name}"
        )
        await self._send_telegram(msg, severity="info")
        _log.info("eod summary written: %s", self._uptime_summary_path)

    def _data_gap_minutes(self) -> float:
        gap_path = self._live_root / "alerts" / f"{self._date_str}_gaps.jsonl"
        if not gap_path.exists():
            return 0.0
        total = 0.0
        try:
            for line in gap_path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                total += float(json.loads(line).get("gap_minutes", 0.0))
        except Exception:
            return total
        return total


async def _alarm_drill() -> bool:
    tg_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    tg_chat = os.environ.get("TELEGRAM_CHAT_ID", "")
    tg_thread = os.environ.get("TELEGRAM_THREAD_ID", "")
    hc_url = os.environ.get("EXTERNAL_HEARTBEAT_URL", "")
    sentry_dsn = os.environ.get("SENTRY_DSN", "")
    results: dict[str, str] = {}

    async with aiohttp.ClientSession() as session:
        if tg_token and tg_chat:
            try:
                payload: dict[str, str | int] = {
                    "chat_id": tg_chat,
                    "text": "[TEST] Health monitor alarm drill - Telegram alerts working.",
                    "parse_mode": "HTML",
                }
                if tg_thread:
                    payload["message_thread_id"] = int(tg_thread)
                async with session.post(
                    f"https://api.telegram.org/bot{tg_token}/sendMessage",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    results["telegram"] = "PASS" if resp.status == 200 else f"FAIL HTTP {resp.status}"
            except Exception as exc:
                results["telegram"] = f"FAIL {type(exc).__name__}"
        else:
            results["telegram"] = "SKIP (TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set)"

        if hc_url:
            try:
                base = hc_url.rstrip("/")
                async with session.get(f"{base}/start", timeout=aiohttp.ClientTimeout(total=10)) as r1:
                    start_ok = 200 <= r1.status < 300
                await asyncio.sleep(2)
                async with session.get(f"{base}/fail", timeout=aiohttp.ClientTimeout(total=10)) as r2:
                    fail_ok = 200 <= r2.status < 300
                await asyncio.sleep(2)
                async with session.get(base, timeout=aiohttp.ClientTimeout(total=10)) as r3:
                    recover_ok = 200 <= r3.status < 300
                results["healthchecks"] = (
                    f"PASS (start={start_ok} fail={fail_ok} recover={recover_ok})"
                    if start_ok and fail_ok and recover_ok
                    else f"PARTIAL start={start_ok} fail={fail_ok} recover={recover_ok}"
                )
            except Exception as exc:
                results["healthchecks"] = f"FAIL {type(exc).__name__}"
        else:
            results["healthchecks"] = "SKIP (EXTERNAL_HEARTBEAT_URL not set)"

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
                sentry_sdk.capture_exception(RuntimeError("Alarm drill: Sentry connectivity test"))
                sentry_sdk.flush(timeout=5)
                results["sentry"] = "PASS (event sent)"
            except ImportError:
                results["sentry"] = "SKIP (sentry_sdk not installed)"
            except Exception as exc:
                results["sentry"] = f"FAIL {type(exc).__name__}"
        else:
            results["sentry"] = "SKIP (SENTRY_DSN not set)"

    all_ok = True
    for channel, status in results.items():
        prefix = "OK" if status.startswith("PASS") else "SKIP" if status.startswith("SKIP") else "FAIL"
        print(f"  {prefix:4s} {channel:15s}: {status}")
        if status.startswith("FAIL"):
            all_ok = False
    return all_ok


def _load_profile(profile_name: str) -> dict:
    cfg_path = _repo_root / "configs" / "live" / f"{profile_name}.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"profile not found: {cfg_path}")
    return json.loads(cfg_path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Wing-6 health monitor")
    parser.add_argument("--profile", default="wing6_4x1_all_vix_filtered")
    parser.add_argument("--once", action="store_true", help="Run one critical+slow check cycle, write state, then exit")
    parser.add_argument("--test-alerts", action="store_true", help="Test Telegram, Healthchecks.io, and Sentry, then exit")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        stream=sys.stdout,
    )

    if args.test_alerts:
        print("Alarm drill - testing notification channels:")
        ok = asyncio.run(_alarm_drill())
        sys.exit(0 if ok else 1)

    try:
        profile = _load_profile(args.profile)
        live_root = _resolve_live_root()
    except Exception as exc:
        _log.error("%s", exc)
        sys.exit(1)

    monitor = HealthMonitor(profile=profile, live_root=live_root)
    if args.once:
        asyncio.run(monitor.run_once())
    else:
        asyncio.run(monitor.run())


if __name__ == "__main__":
    main()

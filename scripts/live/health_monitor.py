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

from options_backtest.calendar import expiry_on_or_after, is_trading_day as _static_is_trading_day
from options_backtest.clock_sync import (
    clock_sync_status as _clock_sync_status,
    parse_timesync_offset as _parse_timesync_offset,
    remediate_clock_sync as _remediate_clock_sync,
)
from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
from options_backtest.live_event_log import EventType, read_latest_events
from scripts.live.market_calendar import market_session_decision
from scripts.live.renew_token import renew_token as _do_renew_token, write_token_file as _write_token_file

_DEFAULT_TOKEN_FILE = _repo_root / ".env.live"

_IST = ZoneInfo("Asia/Kolkata")
_log = logging.getLogger(__name__)

_CRITICAL_INTERVAL = 15.0
_SLOW_INTERVAL = 60.0
_JSONL_ALERT_REPEAT_INTERVAL = 600.0  # only re-write a recurring alert every 10 min
_RESOLVED_JSONL_MIN_DURATION_SECONDS = 300.0
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
_CHAIN_CHECK_START = time(9, 17)
_ONE_MIN_CHECK_START = time(9, 30)

_WD_MIN_GB_INTRADAY = float(os.environ.get("WD_MIN_GB_INTRADAY", "20.0"))
# Pre-run (off-hours) headroom warning. A session writes ~15-20 GB of raw
# packets + parquet, so 40 GB keeps ~2-session headroom while staying well above
# the 20 GB intraday-critical floor. The previous 100 GB threshold fired every
# off-hours evaluation (every ~10 min) on a 466 GB drive that was merely 81%
# full — pure standing-condition noise, not a per-session risk. Env-overridable.
_WD_MIN_GB_PRE_RUN = float(os.environ.get("WD_MIN_GB_PRE_RUN", "40.0"))
_QUOTE_FRESHNESS_MIN_PCT = 95.0
_DEPTH_READY_MIN_PCT = 95.0
_RAW_FLUSH_MAX_AGE_SECONDS = 90.0
_PARQUET_FLUSH_MAX_AGE_SECONDS = 180.0
_CLOCK_BOOT_GRACE_SECONDS = 300.0
_CLOCK_REMEDIATION_INTERVAL_SECONDS = 1800.0

# In-process /health endpoint exposed by the paper engine. A 200 here is
# an *unambiguous* liveness signal — the engine's asyncio loop responded
# to a real HTTP request. The mtime-of-latest_process_health.json check
# remains as a fallback for the cold-start window before the server has
# bound to its port, and for environments where the monitor and engine
# don't share localhost (e.g. dev).
_ENGINE_HEALTH_URL = os.environ.get("ENGINE_HEALTH_URL", "http://127.0.0.1:8001/health")
_ENGINE_HEALTH_TIMEOUT_SECONDS = 2.0

# systemd units that count as "the engine is supposed to be running". Phase E1
# collapsed live-paper.service + dashboard-api.service into live-stack.service;
# the engine now runs in-process under live-stack. The legacy live-paper.service
# is retained (disabled) only for rollback. We probe live-stack FIRST and treat
# the legacy unit as a fallback so a rollback deploy still reports correctly.
# Checking only live-paper.service (the pre-E1 name) makes is-active always
# False under E1, which silently defeats the engine_stall grouping below and
# turns every stale-snapshot symptom into a standalone critical storm
# (root cause of the 2026-06-02 standalone-critical cascade). Overridable via
# $ENGINE_SYSTEMD_UNITS (comma-separated, first match wins).
_ENGINE_SYSTEMD_UNITS = [
    u.strip()
    for u in os.environ.get(
        "ENGINE_SYSTEMD_UNITS", "live-stack.service,live-paper.service"
    ).split(",")
    if u.strip()
]

_ALERT_THROTTLE: dict[tuple[str, str, str], float] = {}
_LIVE_CALENDAR_ROOT: Path | None = None


def _set_live_calendar_root(live_root: Path) -> None:
    global _LIVE_CALENDAR_ROOT
    _LIVE_CALENDAR_ROOT = live_root


def _now_ist() -> datetime:
    return datetime.now(tz=_IST)


def _today_ist() -> date:
    return _now_ist().date()


def _ist_time() -> time:
    return _now_ist().time()


def _is_trading_day(day: date) -> bool:
    if _LIVE_CALENDAR_ROOT is None:
        return _static_is_trading_day(day)
    decision = market_session_decision(_LIVE_CALENDAR_ROOT, day, refresh=False, fail_closed=False)
    if decision.source == "weekday_fallback":
        return _static_is_trading_day(day)
    return decision.is_trading_day


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


def _append_jsonl_durable(path: Path, record: dict) -> None:
    """Append one JSON record to a JSONL file and fsync it to disk.

    Power-cut durability: the 2026-06-02 outage truncated the alert JSONL tail
    (every pre-outage alert the user saw on Telegram was lost from disk) because
    the writes sat in the OS page cache, unsynced, when power was pulled. Flush
    + fsync on each append so a hard power loss costs at most the in-flight line.
    Best-effort: fsync failures (e.g. on a filesystem that doesn't support it)
    are swallowed — the write itself still happened.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record) + "\n")
        f.flush()
        try:
            os.fsync(f.fileno())
        except OSError:
            pass


def _telegram_text(text: str) -> str:
    text = _scrub_message(text)
    if len(text) <= 4000:
        return text
    return text[:3990] + "\n[truncated]"


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
    tmp.replace(path)


def _write_atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        f.write(text)
        f.flush()
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
        _set_live_calendar_root(live_root)
        self._snapshot_dir = resolve_snapshot_dir(live_root)
        self._durable_dir = resolve_durable_dir(live_root)
        self._session_date = _today_ist()
        self._date_str = self._session_date.strftime("%Y%m%d")
        self._alerts_path = live_root / "alerts" / f"{self._date_str}_alerts.jsonl"
        self._external_heartbeat_path = live_root / "alerts" / f"{self._date_str}_external_heartbeat.jsonl"
        self._alert_state_path = self._snapshot_dir / "latest_alert_state.json"
        self._uptime_summary_path = live_root / "reports" / f"{self._date_str}_uptime_summary.md"
        self._event_log_path = live_root / "event_log" / f"{self._date_str}.sqlite"
        # Per-tick cache of the latest liveness heartbeats from the SQLite event
        # log (canonical source; survives the tmpfs reboot wipe that blinded the
        # 2026-06-02 post-outage checks). Refreshed once per critical tick.
        self._event_liveness: dict[str, dict | None] = {}

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
        self._uptime_counters: dict[str, dict[str, int]] = {
            "paper_engine": {"good": 0, "total": 0},
            "depth_collector": {"good": 0, "total": 0},
            "full_readiness": {"good": 0, "total": 0},
        }
        self._wd_free_gb = 0.0
        self._alert_counts: dict[str, int] = {"critical": 0, "warning": 0, "info": 0}
        self._active_alerts: dict[str, dict] = {}
        self._alert_jsonl_write_times: dict[str, float] = {}
        self._consecutive_bad: dict[str, int] = {"quote_freshness": 0, "depth_readiness": 0}
        self._service_became_active_mono: float = 0.0
        self._service_was_active: bool = False
        # Engine-stall incident grouping: indicators collected during a critical
        # tick. If any are set after all sub-checks ran, one engine_stall alert
        # is emitted (replacing the prior 4-alert spam family).
        self._stall_indicators: dict[str, str] = {}
        self._log_scan_offset = 0
        self._backend_open_sent_for: date | None = None
        self._eod_sent_for: date | None = None
        self._heartbeat_started_for: date | None = None
        self._heartbeat_failed_for: date | None = None
        self._last_external_heartbeat_at: str | None = None
        self._last_external_heartbeat_status: str = "not_sent"
        self._token_renewed_for: date | None = None
        self._token_renewal_attempts_today: int = 0
        self._token_renewal_last_attempt_mono: float | None = None
        self._last_clock_remediation_mono: float = 0.0

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
        self._event_log_path = self._live_root / "event_log" / f"{self._date_str}.sqlite"
        self._event_liveness = {}
        self._alert_counts = {"critical": 0, "warning": 0, "info": 0}
        self._active_alerts = {}
        self._alert_jsonl_write_times = {}
        self._consecutive_bad = {"quote_freshness": 0, "depth_readiness": 0}
        self._good_ticks = 0
        self._total_ticks = 0
        self._uptime_counters = {
            "paper_engine": {"good": 0, "total": 0},
            "depth_collector": {"good": 0, "total": 0},
            "full_readiness": {"good": 0, "total": 0},
        }
        self._started_monotonic = _time.monotonic()
        self._log_scan_offset = 0
        self._token_renewed_for = None
        self._token_renewal_attempts_today = 0
        self._token_renewal_last_attempt_mono = None

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
            # Reset lag/stall indicators at start of each tick; sub-checks below
            # flag indicators and _emit_engine_stall emits one grouped incident.
            self._stall_indicators = {}
            self._refresh_event_liveness()
            runner = await self._check_runner_process()
            collector = await self._check_collector_heartbeat()
            feed = await self._check_feed_state()
            depth_snapshot = await self._check_depth_snapshot()
            quote_freshness = await self._check_quote_freshness()
            vix_warmup = await self._check_vix_warmup()
            chain_fetch = await self._check_chain_fetch()
            depth_readiness = await self._check_depth_readiness()
            storage = await self._check_wd_mount()
            await self._emit_engine_stall()
            results = [
                runner,
                collector,
                feed,
                depth_snapshot,
                quote_freshness,
                vix_warmup,
                chain_fetch,
                depth_readiness,
                storage,
            ]
            if sys.platform != "win32":
                results.append(await self._check_clock_sync())
            self._record_uptime("paper_engine", [runner, feed, quote_freshness, vix_warmup, chain_fetch])
            self._record_uptime("depth_collector", [collector, depth_snapshot, depth_readiness])
            self._total_ticks += 1
            if all(result for result in results if result is not None):
                self._good_ticks += 1
            self._record_uptime("full_readiness", results)
            self._write_alert_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("critical checks failed unexpectedly: %r", exc)

    def _flag_stall(self, name: str, detail: str) -> None:
        """Record a lag/stall indicator collected during the current tick."""
        self._stall_indicators[name] = detail

    async def _emit_engine_stall(self) -> None:
        """
        Collapse staleness symptoms into one operator-facing incident.

        A stale process heartbeat means the runner itself is probably wedged, so
        that remains critical engine_stall. Fresh process heartbeat with stale
        feed/depth/collector snapshots is market-data lag and should not be
        counted as engine death.
        """
        process_detail = self._stall_indicators.get("process_health")
        if process_detail is not None:
            reasons = ", ".join(f"{k}({v})" for k, v in sorted(self._stall_indicators.items()))
            await self._alert(
                "critical",
                "engine",
                "engine_stall",
                f"engine stall - {reasons}",
            )
            await self._clear_alert("engine", "market_data_lag")
        elif self._stall_indicators:
            reasons = ", ".join(f"{k}({v})" for k, v in sorted(self._stall_indicators.items()))
            await self._alert(
                "warning",
                "engine",
                "market_data_lag",
                f"market data lag - {reasons}",
            )
            await self._clear_alert("engine", "engine_stall")
        else:
            await self._clear_alert("engine", "engine_stall")
            await self._clear_alert("engine", "market_data_lag")

    async def _run_slow_checks(self) -> None:
        try:
            await self._check_raw_packet_flush()
            await self._check_parquet_flush()
            await self._check_1min_ohlcv_progression()
            await self._check_wd_free_space_slow()
            if sys.platform != "win32" and self._expected_ip:
                await self._check_public_ip()
            await self._maybe_renew_token()
            await self._check_token_expiry()
            await self._check_error_log()
            self._write_alert_state()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            _log.error("slow checks failed unexpectedly: %r", exc)

    def _refresh_event_liveness(self) -> None:
        """Refresh the per-tick cache of the latest liveness heartbeats.

        Reads the latest heartbeat / feed_heartbeat / depth_heartbeat from the
        session SQLite event log (read-only, never blocks the engine writer).
        This is the canonical liveness source under Phase E1 — unlike the tmpfs
        JSON snapshots it is NOT wiped on reboot, so a post-outage check sees
        the real last-known engine state instead of a missing file.
        """
        try:
            self._event_liveness = read_latest_events(
                self._event_log_path,
                (EventType.HEARTBEAT, EventType.FEED_HEARTBEAT, EventType.DEPTH_HEARTBEAT),
            )
        except Exception:
            self._event_liveness = {}

    def _liveness_age(self, event_type: str) -> tuple[dict | None, float | None]:
        """Return (payload, age_seconds) for the latest heartbeat of a type.

        ``payload`` is the heartbeat's ``data`` dict (mirror of the legacy JSON
        snapshot), ``age_seconds`` the seconds since it was written. Returns
        (None, None) if no such heartbeat exists (cold start / pre-migration
        log) so callers fall back to the JSON-snapshot read path.
        """
        entry = (self._event_liveness or {}).get(event_type)
        if not entry:
            return None, None
        data = entry.get("data") or {}
        written_at = _parse_ts(data.get("written_at"))
        age = (_now_ist() - written_at).total_seconds() if written_at else None
        return data, age

    async def _probe_engine_http_health(self) -> bool:
        """Return True if the engine's /health endpoint returns 200.

        Network errors and non-200 responses both return False — the
        caller treats False as "fall back to mtime check", not "engine
        is dead". A real engine-dead signal comes from the mtime check
        plus systemctl, which is the legacy path.
        """
        timeout = aiohttp.ClientTimeout(total=_ENGINE_HEALTH_TIMEOUT_SECONDS)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(_ENGINE_HEALTH_URL) as resp:
                    return resp.status == 200
        except Exception:
            # Connection refused, DNS, timeout, etc. — all "not reachable".
            return False

    async def _check_runner_process(self) -> bool | None:
        """
        Liveness check. Does NOT restart anything — systemd's Restart=on-failure
        handles real process death. Stale-snapshot symptoms become a single
        engine_stall indicator (grouped in _emit_engine_stall) instead of a
        standalone restart trigger. This breaks the restart-cascade that
        produced 61 restarts on 2026-05-15.
        """
        if not _is_market_hours():
            await self._clear_alert("process", "runner_not_active")
            await self._clear_alert("process", "process_health_missing")
            await self._clear_alert("process", "process_stale")
            return None

        systemd_active = False
        if sys.platform != "win32" and shutil.which("systemctl"):
            # Phase E1: the engine runs under live-stack.service. Probe the
            # configured units in order; any active unit means the engine is
            # supposed to be up. (See _ENGINE_SYSTEMD_UNITS rationale.)
            for unit in _ENGINE_SYSTEMD_UNITS:
                result = subprocess.run(
                    ["systemctl", "is-active", "--quiet", unit],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if result.returncode == 0:
                    systemd_active = True
                    break

        # Track rising edge: service just became active (restart / fresh start)
        now_mono = _time.monotonic()
        if systemd_active and not self._service_was_active:
            self._service_became_active_mono = now_mono
        self._service_was_active = systemd_active

        # First try the in-process /health endpoint. A 200 here is the
        # only liveness signal that is *not* derivable from filesystem
        # mtime — the engine actually answered a network request. If the
        # endpoint is unreachable we fall back to the mtime check, which
        # remains valid during the boot window before the engine has
        # bound its port.
        snapshot_active = await self._probe_engine_http_health()
        snapshot_age: float | None = None
        # Canonical liveness: the SQLite heartbeat (survives tmpfs reboot wipe).
        # Consulted before the JSON file fallback.
        if not snapshot_active:
            _hb, hb_age = self._liveness_age(EventType.HEARTBEAT)
            if hb_age is not None:
                snapshot_age = hb_age
                if hb_age <= 30:
                    snapshot_active = True
        health_path = self._snapshot_dir / "latest_process_health.json"
        if not snapshot_active and health_path.exists():
            try:
                data = json.loads(health_path.read_text(encoding="utf-8"))
                written_at = _parse_ts(data.get("written_at"))
                if written_at:
                    file_age = (_now_ist() - written_at).total_seconds()
                    snapshot_age = file_age if snapshot_age is None else min(snapshot_age, file_age)
                    if file_age <= 30:
                        snapshot_active = True
            except Exception as exc:
                await self._alert("warning", "process", "process_health_parse_error", str(exc))

        if systemd_active and snapshot_active:
            await self._clear_alert("process", "runner_not_active")
            await self._clear_alert("process", "process_health_missing")
            await self._clear_alert("process", "process_stale")
            return True

        if systemd_active and not snapshot_active:
            # Startup grace: new process hasn't written its first snapshot yet.
            # Accounts for RestartSec=45 + ExecStartPre clock-sync + engine init.
            _STARTUP_GRACE = 90.0
            if now_mono - self._service_became_active_mono < _STARTUP_GRACE:
                _log.debug("process wedge suppressed — service started %.0fs ago (grace %ds)", now_mono - self._service_became_active_mono, _STARTUP_GRACE)
                return None

            # Stale snapshot during feed-active hours: flag for engine_stall
            # grouping. Do NOT alert or restart. Other stall indicators
            # (feed_state, depth_snapshot, collector_heartbeat) will join
            # this in the grouped incident emitted at end of critical tick.
            self._flag_stall(
                "process_health",
                f"age={snapshot_age:.0f}s" if snapshot_age is not None else "stale",
            )
            await self._clear_alert("process", "runner_not_active")
            await self._clear_alert("process", "process_health_missing")
            await self._clear_alert("process", "process_stale")
            return False

        if snapshot_active and not systemd_active:
            # Running without systemd (e.g. manual foreground run)
            await self._clear_alert("process", "runner_not_active")
            return True

        # systemd is dead (or unavailable) — this is a real process failure,
        # not a snapshot staleness symptom. Emit standalone alert; systemd's
        # own Restart=on-failure handles bringing the engine back.
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
            await self._clear_alert("collector", "collector_writer_failed")
            await self._clear_alert("collector", "collector_writer_dropped")
            return None
        # Canonical source: SQLite depth_heartbeat (reboot-proof). Fall back to
        # the tmpfs JSON snapshot when the event log has no heartbeat yet.
        data, age = self._liveness_age(EventType.DEPTH_HEARTBEAT)
        # Fall back to the JSON snapshot when there is no heartbeat OR the
        # heartbeat carries no parseable age (a no-age heartbeat must not be
        # trusted as a permanent stall — the JSON copy may have a good ts).
        if data is None or age is None:
            path = self._snapshot_dir / "latest_depth_cache.json"
            if not path.exists():
                if data is not None:
                    # Heartbeat exists but has no usable timestamp and there is
                    # no JSON to corroborate — flag a stall rather than crash.
                    self._flag_stall("collector_heartbeat", "no_timestamp")
                    return False
                await self._alert(
                    "critical",
                    "collector",
                    "collector_heartbeat_missing",
                    "latest_depth_cache.json missing and no depth_heartbeat in event log during feed-active window",
                )
                return False
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                written_at = _parse_ts(data.get("written_at"))
                age = (_now_ist() - written_at).total_seconds() if written_at else 999999.0
            except Exception as exc:
                await self._alert("warning", "collector", "collector_heartbeat_parse_error", str(exc))
                return False
        if age is None:
            age = 999999.0
        if age > 30:
            # Flag for engine_stall grouping rather than firing a standalone alert.
            self._flag_stall("collector_heartbeat", f"age={age:.0f}s")
            return False
        if not await self._check_collector_writer_state():
            return False
        await self._clear_alert("collector", "collector_heartbeat_missing")
        return True

    async def _check_collector_writer_state(self) -> bool:
        state_path = self._durable_dir / "latest_depth_collector_state.json"
        if not state_path.exists():
            await self._clear_alert("collector", "collector_writer_failed")
            await self._clear_alert("collector", "collector_writer_dropped")
            return True
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception as exc:
            await self._alert("warning", "collector", "collector_state_parse_error", str(exc))
            return False
        writer = state.get("writer_status") or {}
        if not writer:
            await self._clear_alert("collector", "collector_writer_failed")
            await self._clear_alert("collector", "collector_writer_dropped")
            return True
        status = state.get("status")
        if status == "running" and (writer.get("error") or writer.get("alive") is False):
            await self._alert("critical", "collector", "collector_writer_failed", f"writer_status={writer}")
            return False
        dropped = int(writer.get("dropped_raw", 0) or 0) + int(writer.get("dropped_norm", 0) or 0)
        if dropped > 0:
            await self._alert("warning", "collector", "collector_writer_dropped", f"dropped writer items={dropped} status={writer}")
            return False
        await self._clear_alert("collector", "collector_writer_failed")
        await self._clear_alert("collector", "collector_writer_dropped")
        return True

    async def _check_feed_state(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("feed", "feed_state_missing")
            await self._clear_alert("feed", "feed_disconnected")
            return None
        # Canonical source: SQLite feed_heartbeat (reboot-proof). Fall back to
        # the tmpfs JSON snapshot when the event log has no heartbeat yet.
        data, age = self._liveness_age(EventType.FEED_HEARTBEAT)
        # Fall back to JSON when there is no heartbeat OR it has no parseable
        # age (a no-age heartbeat must not be trusted; the JSON copy may be
        # fresh and also carries the authoritative `connected` flag).
        if data is None or age is None:
            json_data = self._load_json_snapshot("latest_feed_state.json")
            if json_data is None:
                if data is not None:
                    self._flag_stall("feed_state", "no_timestamp")
                    return False
                await self._alert("critical", "feed", "feed_state_missing", "latest_feed_state.json missing and no feed_heartbeat in event log during feed-active window")
                return False
            data = json_data
            written_at = _parse_ts(data.get("written_at"))
            age = (_now_ist() - written_at).total_seconds() if written_at else 999999.0
        if age is None:
            age = 999999.0
        if age > 15:
            # Flag for engine_stall grouping rather than firing standalone.
            self._flag_stall("feed_state", f"age={age:.0f}s")
            return False
        if not data.get("connected", False):
            # Real connectivity failure (not a wedge): standalone critical alert.
            await self._alert("critical", "feed", "feed_disconnected", "live feed disconnected")
            return False
        await self._clear_alert("feed", "feed_state_missing")
        await self._clear_alert("feed", "feed_disconnected")
        return True

    async def _check_depth_snapshot(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("depth", "depth_snapshot_missing")
            return None
        # Canonical source: SQLite depth_heartbeat (reboot-proof). Fall back to
        # the tmpfs JSON snapshot when the event log has no heartbeat yet.
        data, age = self._liveness_age(EventType.DEPTH_HEARTBEAT)
        # Fall back to JSON when there is no heartbeat OR it has no parseable age.
        if data is None or age is None:
            json_data = self._load_json_snapshot("latest_depth_cache.json")
            if json_data is None:
                if data is not None:
                    self._flag_stall("depth_snapshot", "no_timestamp")
                    return False
                await self._alert("critical", "depth", "depth_snapshot_missing", "latest_depth_cache.json missing and no depth_heartbeat in event log during feed-active window")
                return False
            data = json_data
            written_at = _parse_ts(data.get("written_at"))
            age = (_now_ist() - written_at).total_seconds() if written_at else 999999.0
        if age is None:
            age = 999999.0
        if age > 15:
            # Flag for engine_stall grouping rather than firing standalone.
            self._flag_stall("depth_snapshot", f"age={age:.0f}s")
            return False
        await self._clear_alert("depth", "depth_snapshot_missing")
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

    async def _check_vix_warmup(self) -> bool | None:
        if not _is_feed_active():
            await self._clear_alert("vix", "vix_quote_missing")
            await self._clear_alert("vix", "vix_quote_stale")
            return None
        data = self._load_json_snapshot("latest_feed_state.json")
        if data is None:
            return False
        core_quotes = data.get("core_quotes", {})
        vix = core_quotes.get("INDIA_VIX", {})
        if not vix or not vix.get("seen", False):
            await self._alert("critical", "vix", "vix_quote_missing", "VIX quote not received after feed warm-up window")
            return False
        if not vix.get("fresh", False):
            age = vix.get("age_seconds")
            await self._alert("critical", "vix", "vix_quote_stale", f"VIX quote stale: age={age}s")
            return False
        await self._clear_alert("vix", "vix_quote_missing")
        await self._clear_alert("vix", "vix_quote_stale")
        return True

    async def _check_chain_fetch(self) -> bool | None:
        if not _is_trading_day(self._session_date) or _ist_time() < _CHAIN_CHECK_START or _ist_time() > _FEED_ACTIVE_END:
            await self._clear_chain_alerts()
            return None
        data = self._load_json_snapshot("latest_feed_state.json")
        if data is None:
            return False
        chain_status = data.get("chain_status", {})
        expected = self._expected_trading_symbols()
        if not expected:
            return None
        ok = True
        for symbol in expected:
            if self._symbol_dte_excluded_today(symbol):
                await self._clear_alert("chain_fetch", f"chain_not_loaded_{symbol.lower()}")
                continue
            rec = chain_status.get(symbol, {})
            reason = f"chain_not_loaded_{symbol.lower()}"
            if rec.get("status") != "loaded":
                msg = rec.get("error") or rec.get("reason") or "missing chain status"
                await self._alert("critical", "chain_fetch", reason, f"{symbol} option chain not loaded: {msg}")
                ok = False
            else:
                await self._clear_alert("chain_fetch", reason)
        return ok

    async def _check_depth_readiness(self) -> bool | None:
        if not _is_feed_active():
            self._consecutive_bad["depth_readiness"] = 0
            await self._clear_alert("depth_readiness", "depth_ready_low")
            return None
        data = self._load_json_snapshot("latest_depth_cache.json")
        if data is None:
            return False
        pct = float(data.get("ready_pct", data.get("depth_ready_pct", 0.0)))
        by_symbol = data.get("by_symbol", {})
        sym_configs = self._profile.get("symbols", {})
        depth_cfg = self._profile.get("depth_collection", {})
        aggregate_threshold = float(depth_cfg.get("depth_ready_threshold_pct", _DEPTH_READY_MIN_PCT))
        all_sym_ok = True
        for symbol, rec in by_symbol.items():
            sym_pct = float(rec.get("ready_pct", 0.0))
            reason = f"depth_ready_low_{str(symbol).lower()}"
            # A symbol that is already excluded from today's strategy by its
            # DTE/expiry window will never trade, so its depth readiness is
            # irrelevant for the session. Suppress (and clear) its per-symbol
            # depth alerts to avoid flooding the audit trail with noise about
            # untraded instruments. Mirrors the chain-fetch guard above.
            if self._symbol_dte_excluded_today(symbol):
                await self._clear_alert("depth_readiness", reason)
                continue
            sym_threshold = float(sym_configs.get(symbol, {}).get("depth_ready_threshold_pct", _DEPTH_READY_MIN_PCT))
            if sym_pct < sym_threshold:
                await self._alert(
                    "critical",
                    "depth_readiness",
                    reason,
                    (
                        f"{symbol} depth ready {sym_pct:.1f}% "
                        f"({int(rec.get('ready', 0))}/{int(rec.get('total', 0))}, "
                        f"tracked {int(rec.get('tracked', 0))})"
                    ),
                )
                all_sym_ok = False
            else:
                await self._clear_alert("depth_readiness", reason)
        if pct < aggregate_threshold:
            self._consecutive_bad["depth_readiness"] += 1
            if self._consecutive_bad["depth_readiness"] >= 2:
                await self._alert(
                    "critical", "depth_readiness", "depth_ready_low",
                    f"depth ready {pct:.1f}% (< {aggregate_threshold:.0f}%) for 2 consecutive checks",
                )
            return False
        self._consecutive_bad["depth_readiness"] = 0
        await self._clear_alert("depth_readiness", "depth_ready_low")
        return all_sym_ok

    async def _check_wd_mount(self) -> bool:
        try:
            if sys.platform != "win32":
                resolved = self._live_root.resolve()
                if not str(resolved).startswith("/media/WD-Storage"):
                    await self._alert("critical", "storage", "wd_mount_invalid", f"data/live resolves to {resolved}")
                    return False
            probe = self._durable_dir / ".health_write_probe"
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
        status = _clock_sync_status(max_offset_seconds=2.0)
        if status.healthy is True:
            await self._clear_alert("clock", "clock_drift_high")
            await self._clear_alert("clock", "clock_not_synced")
            return True
        if status.healthy is None:
            _log.debug("clock sync check inconclusive: %s", status.message)
            return None

        uptime = _boot_uptime_seconds()
        if status.reason == "clock_not_synced" and uptime is not None and uptime < _CLOCK_BOOT_GRACE_SECONDS:
            _log.info("clock sync still settling after boot: uptime=%.0fs", uptime)
            return None

        now_mono = _time.monotonic()
        if now_mono - self._last_clock_remediation_mono >= _CLOCK_REMEDIATION_INTERVAL_SECONDS:
            self._last_clock_remediation_mono = now_mono
            _log.warning("clock unhealthy (%s); restarting systemd-timesyncd", status.message)
            status = await asyncio.to_thread(_remediate_clock_sync, 2.0, 45.0)
            if status.healthy is True:
                _log.info("clock remediation succeeded: %s", status.message)
                await self._clear_alert("clock", "clock_drift_high")
                await self._clear_alert("clock", "clock_not_synced")
                return True

        await self._alert("critical", "clock", status.reason, status.message)
        return False

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

    async def _check_1min_ohlcv_progression(self) -> bool | None:
        if not _is_trading_day(self._session_date) or _ist_time() < _ONE_MIN_CHECK_START or _ist_time() > _FEED_ACTIVE_END:
            await self._clear_alert("order_book_1min", "one_min_missing")
            await self._clear_alert("order_book_1min", "one_min_stuck")
            await self._clear_alert("order_book_1min", "one_min_read_error")
            return None
        root = self._live_root / "order_book_1min" / self._date_str
        files = list(root.glob("*.parquet")) if root.exists() else []
        if not files:
            await self._alert("warning", "order_book_1min", "one_min_missing", "no 1-minute order-book parquet files found")
            return False
        try:
            import pyarrow.parquet as pq
        except ImportError:
            await self._alert("warning", "order_book_1min", "one_min_read_error", "pyarrow unavailable for 1-minute parquet row-count check")
            return False
        max_rows = 0
        readable = 0
        for path in files[:200]:
            try:
                max_rows = max(max_rows, pq.ParquetFile(path).metadata.num_rows)
                readable += 1
            except Exception:
                continue
        if readable == 0:
            await self._alert("warning", "order_book_1min", "one_min_read_error", "no readable 1-minute order-book parquets")
            return False
        if max_rows <= 1:
            await self._alert("warning", "order_book_1min", "one_min_stuck", f"1-minute order-book bars stuck at max_rows={max_rows} after {_ONE_MIN_CHECK_START}")
            return False
        await self._clear_alert("order_book_1min", "one_min_missing")
        await self._clear_alert("order_book_1min", "one_min_stuck")
        await self._clear_alert("order_book_1min", "one_min_read_error")
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

    async def _maybe_renew_token(self) -> None:
        """Renew the Dhan access token at midnight. Two attempts; alert after both fail."""
        if not _is_trading_day(self._session_date):
            return
        # Only run in the 15-minute window right after midnight.
        if not (time(0, 0) <= _ist_time() < time(0, 15)):
            return
        if self._token_renewed_for == self._session_date:
            return
        # Both attempts already exhausted — waiting for manual intervention.
        if self._token_renewal_attempts_today >= 2:
            return
        # Enforce 5-minute gap between attempt 1 and attempt 2.
        now_mono = _time.monotonic()
        if self._token_renewal_last_attempt_mono is not None:
            if now_mono - self._token_renewal_last_attempt_mono < 300.0:
                return
        # Already valid for the full session — nothing to do.
        if self._access_token:
            exp = _jwt_expiry(self._access_token)
            eod_buffer = datetime.combine(self._session_date, _EOD_BUFFER_END, tzinfo=_IST)
            if exp is not None and exp >= eod_buffer:
                self._token_renewed_for = self._session_date
                return
        client_id = os.environ.get("DHAN_CLIENT_ID", "")
        pin = os.environ.get("DHAN_PIN", "")
        totp_secret = os.environ.get("DHAN_TOTP_SECRET", "")
        if not all([client_id, pin, totp_secret]):
            return  # Creds not configured — _check_token_expiry will alert at preflight
        self._token_renewal_attempts_today += 1
        self._token_renewal_last_attempt_mono = now_mono
        attempt = self._token_renewal_attempts_today
        _log.info("renewing Dhan access token (attempt %d/2)", attempt)
        try:
            token, expiry = await asyncio.to_thread(_do_renew_token, client_id, pin, totp_secret)
            self._access_token = token
            token_file_path = Path(os.environ.get("DHAN_TOKEN_FILE", str(_DEFAULT_TOKEN_FILE)))
            _write_token_file(token, expiry, token_file_path)
            self._token_renewed_for = self._session_date
            await self._clear_alert("token", "token_expires_before_eod")
            await self._clear_alert("token", "token_expiring_soon")
            await self._clear_alert("token", "token_renewal_failed")
            await self._send_telegram(f"Dhan token renewed — valid until {expiry}", severity="info")
            _log.info("token renewed successfully, expiry=%s", expiry)
        except Exception as exc:
            _log.error("token renewal attempt %d failed: %r", attempt, exc)
            if attempt >= 2:
                await self._alert(
                    "critical", "token", "token_renewal_failed",
                    f"Dhan token renewal failed after 2 attempts — manual intervention required: {exc!s:.100}",
                )

    async def _check_token_expiry(self) -> bool | None:
        if not self._access_token:
            return None
        if not _is_trading_day(self._session_date):
            await self._clear_alert("token", "token_expires_before_eod")
            await self._clear_alert("token", "token_expiring_soon")
            return None
        # Token staleness overnight is expected — only check from preflight window onward.
        if _ist_time() < _HEARTBEAT_START:
            await self._clear_alert("token", "token_expires_before_eod")
            await self._clear_alert("token", "token_expiring_soon")
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
        # latest_open_positions.json lives in durable dir; everything else in liveness dir.
        if name == "latest_open_positions.json":
            path = self._durable_dir / name
        else:
            path = self._snapshot_dir / name
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return None

    def _expected_trading_symbols(self) -> list[str]:
        symbols = self._profile.get("symbols", {})
        expected = [
            str(symbol).upper()
            for symbol, cfg in symbols.items()
            if isinstance(cfg, dict) and cfg.get("trade", False)
        ]
        return sorted(expected)

    def _symbol_dte_excluded_today(self, symbol: str) -> bool:
        cfg = self._profile.get("symbols", {}).get(symbol, {})
        if not isinstance(cfg, dict):
            return False
        try:
            expiry = expiry_on_or_after(
                symbol,
                self._session_date,
                expiry_type=cfg.get("expiry_type", "week"),
            )
        except Exception:
            return False
        dte = (expiry - self._session_date).days
        min_dte = int(cfg.get("min_dte", 1))
        max_dte = cfg.get("max_dte")
        if dte < min_dte:
            return True
        if max_dte is not None and dte > int(max_dte):
            return True
        return False

    async def _clear_chain_alerts(self) -> None:
        for symbol in self._expected_trading_symbols():
            await self._clear_alert("chain_fetch", f"chain_not_loaded_{symbol.lower()}")

    def _record_uptime(self, name: str, results: list[bool | None]) -> None:
        relevant = [result for result in results if result is not None]
        if not relevant:
            return
        counter = self._uptime_counters.setdefault(name, {"good": 0, "total": 0})
        counter["total"] += 1
        if all(relevant):
            counter["good"] += 1

    def _uptime_pct(self, name: str) -> float:
        counter = self._uptime_counters.get(name, {"good": 0, "total": 0})
        total = counter.get("total", 0)
        if not total:
            return 0.0
        return round(counter.get("good", 0) / total * 100, 2)

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
        _append_jsonl_durable(self._external_heartbeat_path, record)

    async def _alert(self, severity: str, component: str, reason: str, message: str) -> None:
        message = _scrub_message(message)
        key = f"{severity}:{component}:{reason}"
        existing = self._active_alerts.get(key, {})
        self._active_alerts[key] = {
            "severity": severity,
            "component": component,
            "reason": reason,
            "message": message,
            "first_seen": existing.get("first_seen", _now_ist().isoformat()),
            "last_seen": _now_ist().isoformat(),
        }
        # Rate-limit JSONL writes — first occurrence then every 10 min per alert key
        now_mono = _time.monotonic()
        last_write = self._alert_jsonl_write_times.get(key, 0.0)
        if now_mono - last_write >= _JSONL_ALERT_REPEAT_INTERVAL:
            record = {
                "ts": _now_ist().isoformat(),
                "severity": severity,
                "component": component,
                "reason": reason,
                "message": message,
            }
            _append_jsonl_durable(self._alerts_path, record)
            self._alert_jsonl_write_times[key] = now_mono
            self._alert_counts[severity] = self._alert_counts.get(severity, 0) + 1

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
            first_seen_str = record.get("first_seen")
            alert_duration = 0.0
            if first_seen_str:
                first_seen_dt = _parse_ts(first_seen_str)
                if first_seen_dt:
                    alert_duration = (_now_ist() - first_seen_dt).total_seconds()
            if alert_duration < _RESOLVED_JSONL_MIN_DURATION_SECONDS:
                continue
            # The stored ``record['message']`` describes the *failure* condition
            # (e.g. "NIFTY depth ready 0.0%"), so echoing it verbatim under a
            # "RECOVERED:" prefix is misleading — it reports the bad value, not
            # the recovered state. Describe the clear as a state transition with
            # how long the condition was active instead.
            recovered_message = (
                f"cleared {reason} after {alert_duration:.0f}s "
                f"(was: {record['message']})"
            )
            resolved = {
                "ts": _now_ist().isoformat(),
                "severity": "resolved",
                "component": component,
                "reason": reason,
                "message": recovered_message,
            }
            _append_jsonl_durable(self._alerts_path, resolved)
            if alert_duration >= _RESOLVED_JSONL_MIN_DURATION_SECONDS:
                await self._send_telegram(f"[RECOVERED] {component}/{reason}: {recovered_message}", severity="info")

    async def _send_telegram(self, text: str, severity: str = "info") -> None:
        if not self._tg_token or not self._tg_chat or self._session is None:
            return
        url = f"https://api.telegram.org/bot{self._tg_token}/sendMessage"
        payload: dict[str, str | int] = {
            "chat_id": self._tg_chat,
            "text": _telegram_text(text),
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
        uptime_components = {
            "paper_engine_uptime_pct": self._uptime_pct("paper_engine"),
            "depth_collector_uptime_pct": self._uptime_pct("depth_collector"),
            "full_readiness_uptime_pct": self._uptime_pct("full_readiness"),
        }
        state = {
            "written_at": _now_ist().isoformat(),
            "session_date": self._session_date.isoformat(),
            "phase": self._phase,
            "uptime_pct": uptime_pct,
            **uptime_components,
            "uptime_counters": self._uptime_counters,
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
            f"- Paper engine uptime: {self._uptime_pct('paper_engine'):.2f}%",
            f"- Depth collector uptime: {self._uptime_pct('depth_collector'):.2f}%",
            f"- Full readiness uptime: {self._uptime_pct('full_readiness'):.2f}%",
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
            f"Paper engine: {self._uptime_pct('paper_engine'):.2f}% | "
            f"Depth collector: {self._uptime_pct('depth_collector'):.2f}% | "
            f"Full readiness: {self._uptime_pct('full_readiness'):.2f}%\n"
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

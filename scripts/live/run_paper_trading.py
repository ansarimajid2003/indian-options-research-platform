"""
Daily orchestrator for Wing-6 Iron Condor paper trading.

Usage:
    python scripts/live/run_paper_trading.py --profile wing6_4x1_all_vix_filtered
    python scripts/live/run_paper_trading.py --profile wing6_4x1_all_vix_filtered --dry-run

Reads credentials from env:
    DHAN_ACCESS_TOKEN    — current access token (auto-renewed if expired or < 2h remaining)
    DHAN_CLIENT_ID       — numeric client ID
    DHAN_PIN             — 6-digit login PIN (required for auto-renewal)
    DHAN_TOTP_SECRET     — base32 TOTP secret (required for auto-renewal)

On Windows: uses data/live directly (no WD-Storage validation).
On Linux: validates data/live symlink resolves to /media/WD-Storage/...
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import threading
from datetime import date
from pathlib import Path

_repo_root = Path(__file__).parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

from options_backtest.calendar import is_trading_day
from options_backtest.depth_cache import DepthCache
from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir
from options_backtest.paper_engine import PaperTradingEngine
from scripts.live.collect_order_book import collect_order_book
from scripts.live.paper_json_to_ledger import write_paper_reports
from scripts.live.renew_token import check_token_expiry, renew_token, write_token_file

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    stream=sys.stdout,
)
_log = logging.getLogger(__name__)
_POST_REPORT_HARD_EXIT_SECONDS = 45.0
_ANCILLARY_SHUTDOWN_TIMEOUT_SECONDS = 20.0
_PAPER_REPORT_TIMEOUT_SECONDS = 30.0


def _resolve_live_root() -> Path:
    live_path = _repo_root / "data" / "live"
    if sys.platform != "win32":
        resolved = live_path.resolve()
        if not str(resolved).startswith("/media/WD-Storage"):
            raise RuntimeError(
                f"data/live resolves to {resolved}, expected /media/WD-Storage/... "
                "Aborting to protect writable live storage."
            )
    live_path.mkdir(parents=True, exist_ok=True)
    return live_path


def _load_profile(profile_name: str) -> dict:
    cfg_path = _repo_root / "configs" / "live" / f"{profile_name}.json"
    if not cfg_path.exists():
        raise FileNotFoundError(f"Profile not found: {cfg_path}")
    return json.loads(cfg_path.read_text())


def _load_checkpoint(live_root: Path, today: date) -> dict | None:
    """Return checkpoint dict if today's file has open positions, else None."""
    ckpt_path = resolve_durable_dir(live_root) / "latest_open_positions.json"
    if not ckpt_path.exists():
        return None
    try:
        ckpt = json.loads(ckpt_path.read_text())
        if ckpt.get("session_date") != today.isoformat():
            return None
        if not ckpt.get("open_positions"):
            return None
        return ckpt
    except Exception as exc:
        _log.warning("checkpoint: could not load — %r", exc)
        return None


def _check_health_monitor_running(live_root: Path) -> bool:
    """Best-effort check: did health monitor write a recent alert-state file?"""
    health_path = resolve_snapshot_dir(live_root) / "latest_alert_state.json"
    return health_path.exists()


def _load_restart_reason(live_root: Path, today: date) -> dict | None:
    """Return a same-day operator restart reason, if one was written before restart."""
    path = resolve_durable_dir(live_root) / "restart_reason.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        _log.warning("restart_reason: could not load - %r", exc)
        return None
    session_date = data.get("session_date")
    if session_date and session_date != today.isoformat():
        return None
    reason = str(data.get("reason") or "").strip()
    if not reason:
        return None
    return data


async def _cancel_tasks(tasks: list[asyncio.Task], timeout: float) -> bool:
    pending = [task for task in tasks if not task.done()]
    for task in pending:
        task.cancel()
    if not pending:
        return True
    try:
        await asyncio.wait_for(asyncio.gather(*pending, return_exceptions=True), timeout=timeout)
        return True
    except asyncio.TimeoutError:
        _log.error("orchestrator: ancillary shutdown timed out after %.1fs", timeout)
        return False


def _start_post_report_hard_exit_timer(enabled: bool, exit_code: int = 0) -> threading.Timer | None:
    if not enabled:
        return None

    def _force_exit() -> None:
        _log.error("orchestrator: post-report hard-exit watchdog fired")
        logging.shutdown()
        os._exit(exit_code)

    timer = threading.Timer(_POST_REPORT_HARD_EXIT_SECONDS, _force_exit)
    timer.daemon = True
    timer.start()
    return timer


def _ensure_fresh_token(access_token: str, client_id: str) -> str:
    """
    Return a valid access token. Renews automatically if expired or < 2 hours remaining.

    Reads DHAN_PIN and DHAN_TOTP_SECRET from env for renewal.
    If renewal env vars are absent, logs a warning and returns the original token.
    """
    remaining, status = check_token_expiry(access_token)
    _log.info("token_check: %s", status)

    needs_renewal = remaining is None or remaining < 7200  # < 2 hours
    if not needs_renewal:
        return access_token

    pin = os.environ.get("DHAN_PIN", "")
    totp_secret = os.environ.get("DHAN_TOTP_SECRET", "")
    if not pin or not totp_secret:
        _log.warning("token_check: token needs renewal but DHAN_PIN/DHAN_TOTP_SECRET not set — proceeding with existing token")
        return access_token

    _log.info("token_check: renewing token (remaining=%s)", f"{int(remaining)}s" if remaining else "N/A")
    try:
        new_token, expiry = renew_token(client_id, pin, totp_secret)
        token_file = _repo_root / ".env.live"
        write_token_file(new_token, expiry, token_file)
        _log.info("token_check: renewed — expiry=%s written to %s", expiry, token_file)
        return new_token
    except RuntimeError as exc:
        _log.error("token_check: renewal failed — %s — proceeding with existing token", exc)
        return access_token


async def _run_dry(profile: dict, today: date, live_root: Path, access_token: str, client_id: str) -> None:
    _log.info("DRY RUN: running for 30 seconds — no websocket connections")
    depth_cache = DepthCache()
    engine = PaperTradingEngine(
        profile=profile,
        session_date=today,
        depth_cache=depth_cache,
        access_token="[REDACTED]",
        client_id="[REDACTED]",
        live_root=live_root,
    )
    _log.info("DRY RUN: symbols=%s", list(
        s for s, c in profile.get("symbols", {}).items() if c.get("trade", False)
    ))
    _log.info("DRY RUN: VIX security_id=%s", profile.get("vix", {}).get("dhan_scrip_id"))
    _log.info("DRY RUN: live_root=%s", live_root)
    await asyncio.sleep(30)
    _log.info("DRY RUN: complete")


async def _reconcile_collector(
    profile: dict,
    today: date,
    live_root: Path,
    depth_cache: DepthCache,
    access_token: str,
    client_id: str,
) -> None:
    """At 09:17, check if collector missed symbols that engine loaded; retry them."""
    from datetime import time as _time, datetime as _datetime
    from options_backtest.live_paths import resolve_durable_dir, resolve_snapshot_dir

    reconcile_at = _time(9, 17)
    now = _datetime.now()
    target = _datetime.combine(today, reconcile_at)
    if now < target:
        await asyncio.sleep((target - now).total_seconds())

    state_path = resolve_durable_dir(live_root) / "latest_depth_collector_state.json"
    if not state_path.exists():
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    failed = set(state.get("failed_symbols", []))
    if not failed:
        return

    imap_path = resolve_snapshot_dir(live_root) / "latest_instrument_map.json"
    if not imap_path.exists():
        return
    try:
        imap = json.loads(imap_path.read_text(encoding="utf-8"))
    except Exception:
        return

    loaded_symbols = set()
    for sid, info in imap.get("instruments", {}).items():
        loaded_symbols.add(info.get("symbol"))
    missing = [s for s in failed if s in loaded_symbols]
    if missing:
        _log.info("orchestrator: reconciling missing collector symbols: %s", missing)
        await collect_order_book(
            profile=profile,
            session_date=today,
            depth_cache=depth_cache,
            access_token=access_token,
            client_id=client_id,
            live_root=live_root,
            reconcile_symbols=missing,
        )
    else:
        _log.info("orchestrator: no reconciliation needed (engine did not load failed symbols)")


async def _run_live(
    profile: dict,
    today: date,
    live_root: Path,
    access_token: str,
    client_id: str,
    hard_exit: bool = False,
) -> None:
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
        _log.info("RESUME MODE: orchestrator loaded checkpoint with %d open positions",
                  len(checkpoint.get("open_positions", [])))
        engine.resume_from_checkpoint(checkpoint, restart_reason=_load_restart_reason(live_root, today))

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
    engine_task = asyncio.create_task(engine.run())
    reconcile_task = asyncio.create_task(
        _reconcile_collector(profile, today, live_root, depth_cache, access_token, client_id)
    )
    all_tasks = [collector_task, engine_task, reconcile_task]

    try:
        while True:
            done, _pending = await asyncio.wait(
                set(all_tasks),
                return_when=asyncio.FIRST_COMPLETED,
            )
            if engine_task in done:
                await engine_task
                watchdog = _start_post_report_hard_exit_timer(hard_exit, exit_code=0)
                keep_watchdog = False
                date_str = today.strftime("%Y%m%d")
                trades_json = live_root / "paper_trades" / f"{date_str}.json"
                if trades_json.exists():
                    _log.info("orchestrator: engine complete; writing paper reports from %s", trades_json)
                    try:
                        await asyncio.wait_for(
                            asyncio.to_thread(write_paper_reports, trades_json, today, live_root),
                            timeout=_PAPER_REPORT_TIMEOUT_SECONDS,
                        )
                        _log.info("orchestrator: paper reports complete")
                    except asyncio.TimeoutError:
                        _log.error("orchestrator: paper report conversion timed out after %.1fs", _PAPER_REPORT_TIMEOUT_SECONDS)
                        keep_watchdog = True
                try:
                    clean_shutdown = await _cancel_tasks(
                        [t for t in all_tasks if t is not engine_task],
                        timeout=_ANCILLARY_SHUTDOWN_TIMEOUT_SECONDS,
                    )
                    if not clean_shutdown:
                        keep_watchdog = True
                finally:
                    if watchdog is not None and not keep_watchdog:
                        watchdog.cancel()
                _log.info("orchestrator: ancillary tasks stopped; live run complete")
                return
            if collector_task in done:
                exc = collector_task.exception()
                if exc is not None:
                    _log.error("orchestrator: collector failed with exception: %r", exc)
                    # Do NOT cancel engine — collector failure is no longer fatal
                else:
                    _log.info("orchestrator: collector exited cleanly — engine continues")
                all_tasks.remove(collector_task)
                continue
            if reconcile_task in done:
                exc = reconcile_task.exception()
                if exc is not None:
                    _log.error("orchestrator: reconcile task failed: %r", exc)
                else:
                    _log.info("orchestrator: reconcile task complete")
                all_tasks.remove(reconcile_task)
                continue
    finally:
        await _cancel_tasks(all_tasks, timeout=_ANCILLARY_SHUTDOWN_TIMEOUT_SECONDS)


def main() -> None:
    parser = argparse.ArgumentParser(description="Wing-6 paper trading orchestrator")
    parser.add_argument("--profile", default="wing6_4x1_all_vix_filtered", help="Profile name (no .json)")
    parser.add_argument("--dry-run", action="store_true", help="30-second dry run, no websockets")
    parser.add_argument("--hard-exit", action="store_true", help="Force interpreter exit after live cleanup for systemd one-shot runs")
    args = parser.parse_args()

    access_token = os.environ.get("DHAN_ACCESS_TOKEN", "")
    client_id = os.environ.get("DHAN_CLIENT_ID", "")

    if not args.dry_run:
        if not access_token:
            _log.error("DHAN_ACCESS_TOKEN not set — aborting")
            sys.exit(1)
        if not client_id:
            _log.error("DHAN_CLIENT_ID not set — aborting")
            sys.exit(1)

    profile = _load_profile(args.profile)
    today = date.today()

    if not args.dry_run and not is_trading_day(today):
        _log.info("orchestrator: %s is not a trading day — exiting", today)
        sys.exit(0)

    live_root = _resolve_live_root()

    # Pre-start duplicate-session guard
    if not args.dry_run:
        ph_path = resolve_snapshot_dir(live_root) / "latest_process_health.json"
        if ph_path.exists():
            try:
                ph = json.loads(ph_path.read_text(encoding="utf-8"))
                if ph.get("session_date") == today.isoformat() and ph.get("phase") == "complete":
                    _log.warning("orchestrator: session for %s already complete — exiting", today)
                    sys.exit(0)
            except Exception:
                pass

    if not args.dry_run:
        if not _check_health_monitor_running(live_root):
            _log.warning("orchestrator: health monitor not detected — proceeding anyway")
        access_token = _ensure_fresh_token(access_token, client_id)

    _log.info("orchestrator: profile=%s date=%s live_root=%s dry_run=%s",
              args.profile, today, live_root, args.dry_run)

    exit_code = 0
    try:
        if args.dry_run:
            asyncio.run(_run_dry(profile, today, live_root, access_token, client_id))
        else:
            asyncio.run(_run_live(profile, today, live_root, access_token, client_id, hard_exit=args.hard_exit))
    except SystemExit:
        raise
    except Exception:
        _log.exception("orchestrator: session failed")
        exit_code = 1
    # Ensure the interpreter exits so systemd marks the service inactive (dead).
    if args.hard_exit and not args.dry_run:
        logging.shutdown()
        os._exit(exit_code)
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

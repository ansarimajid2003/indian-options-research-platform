"""
Offline readiness checks for Phase 8/9 live-paper deployment.

This script does not open Dhan connections and does not mutate broker state. It
validates the local artifact trail that Phase 8/9 depend on: profile shape,
depth websocket budget, restart-gap sentinels, paper-trade accounting, and
secret leakage in logs/snapshots.

Usage:
    python scripts/live/validate_phase8_9.py --date 20260512 --live-root data/live
    python scripts/live/validate_phase8_9.py --date 20260512 --live-root data/live --json
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path

_repo_root = Path(__file__).parents[2]
if str(_repo_root) not in sys.path:
    sys.path.insert(0, str(_repo_root))

TOKEN_LIKE_RE = re.compile(r"(?<![A-Za-z0-9_-])[A-Za-z0-9_-]{100,}(?![A-Za-z0-9_-])")
SENTRY_DSN_VALUE_RE = re.compile(r"https://[0-9a-fA-F]{16,}@[A-Za-z0-9_.-]+/\d+")
MAX_GAP_MINUTES = 30.0
MAX_DHAN_WS_CONNECTIONS = 5
MAX_DEPTH_INSTRUMENTS_PER_CONNECTION = 50

TEXT_SUFFIXES = {
    ".csv",
    ".html",
    ".ini",
    ".json",
    ".jsonl",
    ".jsx",
    ".log",
    ".md",
    ".py",
    ".service",
    ".sh",
    ".txt",
    ".yaml",
    ".yml",
}

CLASSIFIED_SKIP_REASONS = {
    "feed_not_connected",
    "chain_not_loaded",
    "vix_stale",
    "vix_below_threshold",
    "vix_bucket_skip",
    "dte_below_min",
    "dte_above_max",
    "leg_resolution_failed",
    "spot_stale",
    "insufficient_depth",
    "partial_entry_blocked",
    "clock_drift_high",
    "clock_not_synced",
}


@dataclass(frozen=True)
class ValidationResult:
    name: str
    status: str
    detail: str


def _result(name: str, ok: bool, detail: str, pending: bool = False) -> ValidationResult:
    status = "PENDING" if pending else "PASS" if ok else "FAIL"
    return ValidationResult(name=name, status=status, detail=detail)


def load_profile(profile_name: str) -> dict:
    path = _repo_root / "configs" / "live" / f"{profile_name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def expected_depth_universe(profile: dict) -> tuple[int, int]:
    cfg = profile.get("depth_collection", {})
    offset_range = int(cfg.get("atm_offset_range", 20))
    symbols = [str(s).upper() for s in cfg.get("symbols", [])]
    total = 0
    for symbol in symbols:
        sym_cfg = profile.get("symbols", {}).get(symbol, {})
        if sym_cfg.get("trade") and sym_cfg.get("depth_source") == "dhan_20depth":
            total += ((offset_range * 2) + 1) * 2
    connections = math.ceil(total / MAX_DEPTH_INSTRUMENTS_PER_CONNECTION) if total else 0
    return total, connections


def validate_profile(profile: dict) -> ValidationResult:
    expected_symbols = {
        "NIFTY": ("week", None, "dhan_20depth", 1),
        "FINNIFTY": ("month", 7, "dhan_20depth", 1),
        "MIDCPNIFTY": ("month", 7, "dhan_20depth", 1),
        "SENSEX": ("week", 2, "top_of_book", 1),
    }
    strategy = profile.get("strategy", {})
    offsets_ok = (
        strategy.get("type") == "IronCondor"
        and strategy.get("short_call_offset") == 2
        and strategy.get("long_call_offset") == 8
        and strategy.get("short_put_offset") == -2
        and strategy.get("long_put_offset") == -8
    )
    symbol_errors: list[str] = []
    for symbol, (expiry_type, max_dte, depth_source, lots) in expected_symbols.items():
        sym_cfg = profile.get("symbols", {}).get(symbol, {})
        if not sym_cfg.get("trade"):
            symbol_errors.append(f"{symbol}: trade false")
        if sym_cfg.get("expiry_type") != expiry_type:
            symbol_errors.append(f"{symbol}: expiry_type")
        if sym_cfg.get("max_dte") != max_dte:
            symbol_errors.append(f"{symbol}: max_dte")
        if sym_cfg.get("depth_source") != depth_source:
            symbol_errors.append(f"{symbol}: depth_source")
        if sym_cfg.get("lots") != lots:
            symbol_errors.append(f"{symbol}: lots")
    bank_ok = not profile.get("symbols", {}).get("BANKNIFTY", {}).get("trade", True)
    ok = offsets_ok and bank_ok and not symbol_errors
    details = "locked profile matches Section 2" if ok else "; ".join(symbol_errors) or "strategy offsets mismatch"
    return _result("locked_profile", ok, details)


def validate_depth_budget(profile: dict) -> ValidationResult:
    total, connections = expected_depth_universe(profile)
    configured_max = int(profile.get("depth_collection", {}).get("max_depth_connections", MAX_DHAN_WS_CONNECTIONS))
    ok = connections <= configured_max <= MAX_DHAN_WS_CONNECTIONS
    detail = f"{total} instruments require {connections} depth connections; configured max={configured_max}"
    return _result("websocket_budget", ok, detail)


def large_gap_records(live_root: Path, date_str: str, max_gap_minutes: float = MAX_GAP_MINUTES) -> list[dict]:
    gap_path = live_root / "alerts" / f"{date_str}_gaps.jsonl"
    if not gap_path.exists():
        return []
    bad: list[dict] = []
    for line in gap_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if float(record.get("gap_minutes", 0.0)) > max_gap_minutes:
            bad.append(record)
    return bad


def validate_gap_threshold(live_root: Path, date_str: str) -> ValidationResult:
    bad = large_gap_records(live_root, date_str)
    if bad:
        symbols = ", ".join(str(r.get("symbol", "")) for r in bad)
        return _result("gap_threshold", False, f"{len(bad)} gap sentinel(s) > {MAX_GAP_MINUTES} min: {symbols}")
    gap_path = live_root / "alerts" / f"{date_str}_gaps.jsonl"
    return _result("gap_threshold", True, "no >30 minute gaps" if gap_path.exists() else "no gap file")


def _iter_scan_files(roots: list[Path]):
    ignored = {".git", "__pycache__", ".venv", "venv"}
    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            yield root
            continue
        for current, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if d not in ignored]
            for filename in files:
                path = Path(current) / filename
                if path.suffix.lower() in TEXT_SUFFIXES:
                    yield path


def secret_leaks(roots: list[Path]) -> list[tuple[Path, str]]:
    leaks: list[tuple[Path, str]] = []
    for path in _iter_scan_files(roots):
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if TOKEN_LIKE_RE.search(text):
            leaks.append((path, "token_like_string"))
        if SENTRY_DSN_VALUE_RE.search(text):
            leaks.append((path, "sentry_dsn_value"))
    return leaks


def validate_secret_scan(live_root: Path, include_repo: bool = False) -> ValidationResult:
    roots = [
        live_root / "logs",
        live_root / "snapshots",
        live_root / "paper_trades",
        live_root / "alerts",
        live_root / "reports",
    ]
    if include_repo:
        roots.extend([
            _repo_root / "scripts",
            _repo_root / "options_backtest",
            _repo_root / "configs",
            _repo_root / "dashboard",
            _repo_root / "docs",
            _repo_root / "reports",
            _repo_root / "tests",
        ])
    leaks = secret_leaks(roots)
    if leaks:
        sample = ", ".join(f"{path}:{kind}" for path, kind in leaks[:5])
        return _result("secret_scan", False, f"{len(leaks)} possible leak(s): {sample}")
    return _result("secret_scan", True, "no token-like strings or Sentry DSN values found")


def validate_trade_accounting(live_root: Path, date_str: str) -> ValidationResult:
    path = live_root / "paper_trades" / f"{date_str}.json"
    if not path.exists():
        return _result("trade_accounting", False, f"missing {path}", pending=True)
    trades = json.loads(path.read_text(encoding="utf-8"))
    errors: list[str] = []
    mark_mid_diff_seen = False
    for idx, trade in enumerate(trades):
        gross = round(float(trade.get("gross_pnl", 0.0)), 2)
        charges = round(float(trade.get("charges", 0.0)), 2)
        net = round(float(trade.get("net_pnl", 0.0)), 2)
        if round(gross - charges, 2) != net:
            errors.append(f"trade {idx}: net_pnl != gross_pnl - charges")
        for fill in trade.get("entry_fills", []) + trade.get("exit_fills", []):
            if fill.get("mark_mid") is not None and fill.get("price") is not None:
                if round(float(fill["mark_mid"]), 2) != round(float(fill["price"]), 2):
                    mark_mid_diff_seen = True
    if errors:
        return _result("trade_accounting", False, "; ".join(errors[:5]))
    detail = "net formula OK"
    if trades:
        detail += "; executable fill differs from mark_mid" if mark_mid_diff_seen else "; mark_mid equals fill in observed trades"
    return _result("trade_accounting", True, detail)


def validate_signal_reasons(live_root: Path, date_str: str) -> ValidationResult:
    path = live_root / "paper_trades" / f"{date_str}_signals.jsonl"
    if not path.exists():
        return _result("signal_reason_codes", False, f"missing {path}", pending=True)
    bad: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        if record.get("event") == "skip":
            reason = record.get("reason", "")
            if reason not in CLASSIFIED_SKIP_REASONS:
                bad.append(reason)
    if bad:
        return _result("signal_reason_codes", False, f"unclassified skip reasons: {sorted(set(bad))}")
    return _result("signal_reason_codes", True, "all skip reasons classified")


def validate_resume_summary(live_root: Path, date_str: str) -> ValidationResult:
    path = live_root / "reports" / f"{date_str}_eod_summary.json"
    if not path.exists():
        return _result("resume_summary", False, f"missing {path}", pending=True)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not data.get("resumed_after_crash"):
        return _result("resume_summary", True, "session did not resume after crash")
    has_gap = data.get("crash_gap_start") and data.get("crash_gap_end") and data.get("gap_minutes") is not None
    return _result("resume_summary", bool(has_gap), "resume gap present" if has_gap else "resume flag missing gap window")


def run_validations(
    live_root: Path,
    date_str: str,
    profile_name: str,
    include_repo_scan: bool,
) -> list[ValidationResult]:
    profile = load_profile(profile_name)
    return [
        validate_profile(profile),
        validate_depth_budget(profile),
        validate_gap_threshold(live_root, date_str),
        validate_secret_scan(live_root, include_repo=include_repo_scan),
        validate_trade_accounting(live_root, date_str),
        validate_signal_reasons(live_root, date_str),
        validate_resume_summary(live_root, date_str),
    ]


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Phase 8/9 live-paper readiness artifacts")
    parser.add_argument("--date", required=True, help="Session date YYYYMMDD")
    parser.add_argument("--live-root", default="data/live")
    parser.add_argument("--profile", default="wing6_4x1_all_vix_filtered")
    parser.add_argument("--include-repo-scan", action="store_true", help="Also scan repo text files for secret values")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text")
    args = parser.parse_args()

    live_root = Path(args.live_root)
    results = run_validations(live_root, args.date, args.profile, args.include_repo_scan)
    if args.json:
        print(json.dumps([r.__dict__ for r in results], indent=2))
    else:
        for result in results:
            print(f"{result.status:7s} {result.name:22s} {result.detail}")

    failed = [r for r in results if r.status == "FAIL"]
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import shutil
import subprocess
from datetime import datetime, time
from pathlib import Path
from typing import Any

import pandas as pd

from options_backtest.dhan_loader import DhanBacktestEngine
from options_backtest.schemas import BacktestConfig
from options_backtest.strategy import (
    IronCondor,
    ShortStraddle,
    ShortStrangle,
    ThreePMDirectional,
    ThreePMV2CallLevelStop,
    ThreePMV2Put,
)


DEFAULT_OUTPUT_ROOT = Path("reports/backtests/dashboard_runs")
CONFIG_FIELDS = {field.name for field in dataclasses.fields(BacktestConfig)}


def _parse_time(value: str | None) -> time | None:
    if not value:
        return None
    hour, minute = value.split(":", maxsplit=1)
    return time(int(hour), int(minute))


def _safe_id(value: str) -> str:
    cleaned = "".join(ch.lower() if ch.isalnum() else "_" for ch in value).strip("_")
    while "__" in cleaned:
        cleaned = cleaned.replace("__", "_")
    return cleaned[:80] or "dashboard_run"


def _git_commit() -> str | None:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    return proc.stdout.strip() or None


def _sha256(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _strategy(name: str, params: dict[str, Any]):
    key = name.replace("_", "-").lower()
    mapping = {
        "short-straddle": ShortStraddle,
        "short-strangle": ShortStrangle,
        "iron-condor": IronCondor,
        "three-pm-directional": ThreePMDirectional,
        "three-pm-v2-put": ThreePMV2Put,
        "three-pm-v2-call-level-stop": ThreePMV2CallLevelStop,
    }
    if key not in mapping:
        raise ValueError(f"unknown strategy: {name}")
    return mapping[key](**params)


def _backtest_config(config: dict[str, Any]) -> BacktestConfig:
    raw = dict(config.get("backtest_config", {}))
    for key in CONFIG_FIELDS:
        if key in config:
            raw[key] = config[key]
    if "spot_csv" not in raw and "spot_path" in config:
        raw["spot_csv"] = config["spot_path"]
    for key in ("entry_time", "exit_time"):
        if isinstance(raw.get(key), str):
            raw[key] = _parse_time(raw[key])
    return BacktestConfig(**raw)


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime, pd.Timestamp)):
        return value.isoformat()
    if isinstance(value, time):
        return value.strftime("%H:%M")
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "item"):
        return value.item()
    return value


def _summary_for_dashboard(
    run_id: str,
    name: str,
    strategy_name: str,
    symbol: str,
    summary: dict[str, Any],
    created_at: str,
) -> dict[str, Any]:
    out = dict(summary)
    out.update({
        "id": run_id,
        "name": name,
        "strategy": strategy_name,
        "symbol": symbol,
        "created_at": created_at,
        "source_kind": "canonical",
        "compat_version": "v2",
        "has_ledger": True,
        "has_equity": True,
    })
    out.setdefault("start_date", out.get("date_from"))
    out.setdefault("end_date", out.get("date_to"))
    if "max_dd_pct" not in out and "max_drawdown_pct" in out:
        out["max_dd_pct"] = out["max_drawdown_pct"]
    if "t_stat" not in out and "sharpe_tstat" in out:
        out["t_stat"] = out["sharpe_tstat"]
    return out


def write_canonical_run(
    output_root: Path,
    run_id: str,
    summary: dict[str, Any],
    ledger: pd.DataFrame,
    manifest: dict[str, Any],
    decisions: pd.DataFrame | None = None,
    overwrite: bool = False,
) -> dict[str, Path]:
    output_root.mkdir(parents=True, exist_ok=True)
    paths = {
        "summary": output_root / f"{run_id}_summary.json",
        "ledger": output_root / f"{run_id}_ledger.csv",
        "manifest": output_root / f"{run_id}_manifest.json",
    }
    if decisions is not None:
        paths["decisions"] = output_root / f"{run_id}_decisions.csv"

    existing = [path for path in paths.values() if path.exists()]
    if existing and not overwrite:
        names = ", ".join(str(path) for path in existing)
        raise FileExistsError(f"refusing to overwrite existing dashboard artifacts: {names}")

    paths["summary"].write_text(json.dumps(_jsonable(summary), indent=2, sort_keys=True), encoding="utf-8")
    ledger.to_csv(paths["ledger"], index=False)
    paths["manifest"].write_text(json.dumps(_jsonable(manifest), indent=2, sort_keys=True), encoding="utf-8")
    if decisions is not None:
        decisions.to_csv(paths["decisions"], index=False)
    return paths


def run_from_config(config_path: Path, output_root: Path, run_id_override: str | None, overwrite: bool) -> dict[str, Path]:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    strategy_name = str(cfg.get("strategy", "iron-condor"))
    strategy_params = dict(cfg.get("strategy_params", {}))
    strategy = _strategy(strategy_name, strategy_params)
    backtest_config = _backtest_config(cfg)

    run_id = _safe_id(
        run_id_override
        or str(cfg.get("id", ""))
        or f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{backtest_config.symbol}_{strategy_name}"
    )
    name = str(cfg.get("name") or run_id)
    expiry_type = str(cfg.get("expiry_type", "week"))
    dhan_root = str(cfg.get("dhan_root", "data/processed/options/dhan"))
    spot_path = str(cfg.get("spot_path", backtest_config.spot_csv))

    result = DhanBacktestEngine(
        backtest_config,
        dhan_root=dhan_root,
        expiry_type=expiry_type,
        spot_path=spot_path,
    ).run(
        strategy,
        from_date=cfg.get("from_date"),
        to_date=cfg.get("to_date"),
    )

    summary = _summary_for_dashboard(
        run_id,
        name,
        strategy_name,
        backtest_config.symbol,
        result.summary,
        created_at,
    )
    decisions_path = cfg.get("decisions_csv")
    decisions = pd.read_csv(decisions_path) if decisions_path else None
    if decisions is not None:
        summary["has_decisions"] = True

    manifest_path = Path(str(cfg.get("data_manifest_path", "data/manifests/server_data_manifest.json")))
    manifest = {
        "id": run_id,
        "name": name,
        "created_at": created_at,
        "run_command": " ".join(["python", "scripts/save_backtest.py", str(config_path)]),
        "config_path": str(config_path),
        "git_commit": _git_commit(),
        "engine": "DhanBacktestEngine",
        "strategy": strategy_name,
        "strategy_params": strategy_params,
        "symbol_set": [backtest_config.symbol],
        "expiry_type": expiry_type,
        "dhan_root": dhan_root,
        "spot_path": spot_path,
        "from_date": cfg.get("from_date"),
        "to_date": cfg.get("to_date"),
        "backtest_config": dataclasses.asdict(backtest_config),
        "cost_model_version": "options_backtest.broker_sim current",
        "bad_expiries": list(backtest_config.bad_expiries),
        "data_manifest_path": str(manifest_path),
        "data_manifest_sha256": _sha256(manifest_path),
        "source_config": cfg,
    }

    paths = write_canonical_run(output_root, run_id, summary, result.trade_ledger, manifest, decisions, overwrite)
    if decisions_path and decisions is None:
        shutil.copyfile(decisions_path, output_root / f"{run_id}_decisions.csv")
    return paths


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run a Dhan backtest and save canonical dashboard artifacts.")
    parser.add_argument("config", type=Path, help="JSON config describing the strategy/run.")
    parser.add_argument("--output-root", type=Path, default=DEFAULT_OUTPUT_ROOT)
    parser.add_argument("--id", dest="run_id", help="Override artifact id.")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing artifacts for the same id.")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    paths = run_from_config(args.config, args.output_root, args.run_id, args.overwrite)
    for kind, path in paths.items():
        print(f"{kind}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

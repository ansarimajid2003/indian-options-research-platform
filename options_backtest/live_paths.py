"""
Resolve filesystem paths for the live paper stack.

The live stack writes two flavours of "snapshot" file:

  1. **Liveness snapshots** (`latest_feed_state.json`, `latest_depth_cache.json`,
     `latest_process_health.json`, `latest_alert_state.json`, `latest_quotes.json`,
     `latest_spot_bars.json`, `latest_instrument_map.json`).
     Rewritten every ~10s. Pure regenerable state — used only by the health
     monitor and dashboard to detect liveness. If the host reboots these
     disappear and that is fine; the engine repopulates them within one tick.

  2. **Durable snapshots** (`latest_open_positions.json`,
     `latest_eod_snapshot.json`, `latest_depth_collector_state.json`).
     Must survive reboot. Engine checkpoint recovery on next start, EOD
     option-chain closing chain, and collector restart-gap detection all
     depend on these.

When a single fsync to WD-Storage takes >15s (observed 2026-05-14), the
liveness snapshots go stale and the health monitor reads "engine wedged"
even though the engine is fine. Move liveness writes to tmpfs so disk
latency variance can never falsely trip the liveness signal. Durable
writes stay on WD.

Env vars:
    IM_SNAPSHOT_DIR   tmpfs path for liveness snapshots (e.g.
                      /run/im-snapshots). Unset → falls back to
                      live_root/snapshots so local/Windows runs work
                      without configuration.

Public:
    resolve_snapshot_dir(live_root) -> tmpfs (or WD fallback)
    resolve_durable_dir(live_root)  -> always WD live_root/snapshots
"""

from __future__ import annotations

import os
from pathlib import Path


_SNAPSHOT_DIR_ENV = "IM_SNAPSHOT_DIR"


def resolve_snapshot_dir(live_root: Path) -> Path:
    """Liveness-snapshot directory. Prefer tmpfs via $IM_SNAPSHOT_DIR; else WD."""
    override = os.environ.get(_SNAPSHOT_DIR_ENV, "").strip()
    if override:
        path = Path(override)
    else:
        path = Path(live_root) / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path


def resolve_durable_dir(live_root: Path) -> Path:
    """Durable snapshot directory. Always WD live_root/snapshots."""
    path = Path(live_root) / "snapshots"
    path.mkdir(parents=True, exist_ok=True)
    return path

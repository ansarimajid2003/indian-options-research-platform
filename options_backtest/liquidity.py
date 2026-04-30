from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class LiquidityConfig:
    min_rows: int = 50
    min_total_volume: int = 1
    min_max_oi: int = 1
    max_atm_distance: int = 1000


def contract_is_liquid(df: pd.DataFrame, strike: int, atm_strike: int, config: LiquidityConfig | None = None) -> bool:
    cfg = config or LiquidityConfig()
    if df.empty or len(df) < cfg.min_rows:
        return False
    if abs(strike - atm_strike) > cfg.max_atm_distance:
        return False
    volume = pd.to_numeric(df["volume"], errors="coerce").fillna(0).sum()
    oi = pd.to_numeric(df["oi"], errors="coerce").fillna(0).max()
    return volume >= cfg.min_total_volume and oi >= cfg.min_max_oi

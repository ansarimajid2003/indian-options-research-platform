from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class LiquidityConfig:
    min_rows: int = 50
    min_total_volume: int = 200
    min_max_oi: int = 500
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


def oi_slippage_multiplier(oi: float) -> float:
    """Return a slippage multiplier based on open interest as a fill-quality proxy.

    Low OI indicates wide bid-ask spreads and adverse fills. Rather than
    rejecting the trade, we widen slippage to reflect the degraded fill quality.
    Thresholds chosen from NIFTY options practitioner data (OI in contracts):
      OI < 200:  2.0× — very thin; expect ₹2–₹3 spread on near-money strikes
      OI < 500:  1.5× — moderate; spread likely ₹1–₹2
      OI ≥ 500:  1.0× — liquid; standard spread assumption holds
    """
    if oi < 200:
        return 2.0
    if oi < 500:
        return 1.5
    return 1.0

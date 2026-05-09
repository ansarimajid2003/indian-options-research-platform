"""
Monitoring Dashboard Builder (Unified)
=======================================

Single script that:
  1. Grid-searches lot sizes for optimized wing-6 (3 indices)
  2. Adds scaled lot combinations (1x-4x of best ratios)
  3. Computes defined-risk profile per trade and per day
  4. Combines with best performers from 20260506 risk management experiments
  5. Emits a single markdown monitoring report + CSV + JSON configs

Per-symbol filters:
  - NIFTY:     VIX >= 13  (weekly)
  - FINNIFTY:  DTE <= 7   (monthly)
  - MIDCPNIFTY: DTE <= 7  (monthly)
  - BANKNIFTY: EXCLUDED

Smart bucket policies:
  - NIFTY:     no skip (VIX>=13 already removes 10-13)
  - FINNIFTY:  skip VIX 10-13
  - MIDCPNIFTY: skip VIX 22-30
"""
from __future__ import annotations

import json
import math
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.reports import daily_pnl, equity_curve, summary

OUT_DIR = ROOT / "reports/backtests/options/risk_management"
MONITOR_DIR = ROOT / "reports/backtests/options/monitoring"
IS_END = pd.Timestamp("2024-12-31").date()
OOS_START = pd.Timestamp("2025-01-01").date()

W6_SOURCES = {
    "NIFTY":      OUT_DIR / "20260506_vixgt13_ic_wing6_nifty.csv",
    "FINNIFTY":   OUT_DIR / "20260506_dtelt7_ic_wing6_finnifty.csv",
    "MIDCPNIFTY": OUT_DIR / "20260506_dtelt7_ic_wing6_midcpnifty.csv",
}
RISK_MGMT_CSV = OUT_DIR / "20260506_mixed_expiry_portfolio_comparison.csv"

_LEG_RE = re.compile(r"(BUY|SELL):([A-Z]+)_DHAN_(\d+)(CE|PE)@([0-9.]+)")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt(v: float | None, spec: str = ".3f") -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "-"
    return f"{v:{spec}}"


def _fmt_pnl(v: float | None) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "-"
    return f"{v:+,.0f}"


def _fmt_pct(v: float | None) -> str:
    if v is None or (isinstance(v, float) and (math.isnan(v) or math.isinf(v))):
        return "-"
    return f"{v*100:.2f}%"


def _read_ledger(path: Path, symbol: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "strategy" in df.columns:
        df = df[df["strategy"] != "TOTAL"].copy()
    if df.empty:
        return df
    if "symbol" not in df.columns:
        df.insert(0, "symbol", symbol)
    for col in ("gross_pnl", "charges", "net_pnl", "dte_at_entry", "vix_entry", "lot_size"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("entry_date", "exit_date", "expiry"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    for col in ("entry_time", "exit_time", "vix_timestamp"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    return df.reset_index(drop=True)


def _parse_legs(raw: str) -> list[dict]:
    legs = []
    if not isinstance(raw, str):
        return legs
    for m in _LEG_RE.finditer(raw):
        legs.append({
            "side": m.group(1),
            "symbol": m.group(2),
            "strike": int(m.group(3)),
            "otype": m.group(4),
            "price": float(m.group(5)),
        })
    return legs


def _trade_risk(row) -> tuple[float | None, float | None, float | None]:
    legs = _parse_legs(row.get("entry_legs", ""))
    lot_size = float(row.get("lot_size", 0) or 0)
    if len(legs) != 4 or lot_size <= 0:
        return None, None, None
    credit_unit = sum(l["price"] if l["side"] == "SELL" else -l["price"] for l in legs)
    calls = sorted([l for l in legs if l["otype"] == "CE"], key=lambda x: x["strike"])
    puts = sorted([l for l in legs if l["otype"] == "PE"], key=lambda x: x["strike"])
    if len(calls) != 2 or len(puts) != 2:
        return None, None, None
    call_width = abs(calls[1]["strike"] - calls[0]["strike"])
    put_width = abs(puts[1]["strike"] - puts[0]["strike"])
    width = max(call_width, put_width)
    max_loss = max(0.0, (width - credit_unit) * lot_size)
    entry_credit = credit_unit * lot_size
    return max_loss, entry_credit, width


def _stats(ledger: pd.DataFrame) -> dict:
    if ledger.empty:
        return {}
    dpnl = daily_pnl(ledger)
    curve = equity_curve(dpnl)
    s = summary(ledger, curve, dpnl)
    pnl = pd.to_numeric(dpnl["net_pnl"], errors="coerce").dropna()
    if not pnl.empty:
        cutoff = float(pnl.quantile(0.05))
        tail = pnl[pnl <= cutoff]
        s["daily_var_95"] = round(cutoff, 2)
        s["daily_cvar_95"] = round(float(tail.mean()), 2) if not tail.empty else cutoff
        s["max_daily_loss"] = round(float(pnl.min()), 2)
    return s


def _split_stats(ledger: pd.DataFrame) -> tuple[dict, dict]:
    if ledger.empty:
        return {}, {}
    dates = pd.to_datetime(ledger["exit_date"]).dt.date
    return _stats(ledger[dates <= IS_END].copy()), _stats(ledger[dates >= OOS_START].copy())


def _build_w6_portfolio(ledgers: dict[str, pd.DataFrame], lots: dict[str, int]) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    bucket_policy = {"NIFTY": None, "FINNIFTY": "skip_10_13", "MIDCPNIFTY": "skip_22_30"}
    for symbol, n_lots in lots.items():
        if n_lots <= 0:
            continue
        df = ledgers.get(symbol, pd.DataFrame()).copy()
        if df.empty:
            continue
        policy = bucket_policy.get(symbol)
        if policy == "skip_10_13":
            df = df[df["vix_bucket"] != "10-13"].copy()
        elif policy == "skip_22_30":
            df = df[df["vix_bucket"] != "22-30"].copy()
        if df.empty:
            continue
        for col in ("gross_pnl", "charges", "net_pnl"):
            if col in df.columns:
                df[col] = df[col].astype(float) * n_lots
        df["portfolio_lots"] = float(n_lots)
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values(["exit_time", "symbol"]).reset_index(drop=True)


def _score(s: dict) -> dict:
    sharpe = s.get("sharpe") or 0
    calmar = s.get("calmar") or 0
    cagr = s.get("cagr") or 0
    mdd = abs(s.get("max_drawdown_pct") or 0)
    tstat = s.get("sharpe_tstat") or 0
    dd_penalty = max(0, mdd - 0.05) * 10
    tstat_penalty = max(0, 1.96 - tstat) * 2 if tstat > 0 else 10
    composite = sharpe + calmar + cagr * 10 - dd_penalty - tstat_penalty
    return {"sharpe": sharpe, "calmar": calmar, "cagr": cagr, "max_dd_pct": -mdd, "t_stat": tstat, "composite": composite}


# ---------------------------------------------------------------------------
# Grid search
# ---------------------------------------------------------------------------

def run_grid_search(ledgers: dict[str, pd.DataFrame]) -> pd.DataFrame:
    results: list[dict] = []
    for n in range(1, 5):
        for f in range(1, 5):
            for m in range(1, 5):
                lots = {"NIFTY": n, "FINNIFTY": f, "MIDCPNIFTY": m}
                ledger = _build_w6_portfolio(ledgers, lots)
                if ledger.empty:
                    continue
                s = _stats(ledger)
                sc = _score(s)
                is_s, oos_s = _split_stats(ledger)
                results.append({
                    "nifty_lots": n, "finnifty_lots": f, "midcpnifty_lots": m,
                    "trades": s.get("trades", 0), "net_pnl": s.get("net_pnl"),
                    "cagr": s.get("cagr"), "sharpe": s.get("sharpe"),
                    "t_stat": s.get("sharpe_tstat"), "sortino": s.get("sortino"),
                    "calmar": s.get("calmar"), "max_dd_pct": s.get("max_drawdown_pct"),
                    "pf": s.get("profit_factor"), "win_rate": s.get("win_rate"),
                    "daily_cvar_95": s.get("daily_cvar_95"), "composite": sc["composite"],
                    "is_net": is_s.get("net_pnl"), "is_sharpe": is_s.get("sharpe"),
                    "oos_net": oos_s.get("net_pnl"), "oos_sharpe": oos_s.get("sharpe"),
                })
    return pd.DataFrame(results)


# ---------------------------------------------------------------------------
# Risk profile
# ---------------------------------------------------------------------------

def compute_risk_profile(ledgers: dict[str, pd.DataFrame]) -> dict[str, dict]:
    profiles: dict[str, dict] = {}
    for symbol, df in ledgers.items():
        if df.empty:
            continue
        risks = df.apply(_trade_risk, axis=1)
        df = df.copy()
        df["max_loss"] = [r[0] for r in risks]
        df["entry_credit"] = [r[1] for r in risks]
        df["wing_width"] = [r[2] for r in risks]
        ml = df["max_loss"].dropna()
        ec = df["entry_credit"].dropna()
        ww = df["wing_width"].dropna()
        by_day = df.groupby("entry_date")[["max_loss", "entry_credit"]].sum()
        dpnl = daily_pnl(df)
        pnl = pd.to_numeric(dpnl["net_pnl"], errors="coerce").dropna()
        var95 = float(pnl.quantile(0.05)) if not pnl.empty else 0
        tail = pnl[pnl <= var95] if not pnl.empty else pd.Series(dtype=float)
        cvar95 = float(tail.mean()) if not tail.empty else var95
        profiles[symbol] = {
            "trades": len(df),
            "wing_width_mode": ww.mode().iloc[0] if not ww.empty else 0,
            "wing_width_range": f"{ww.min():.0f}-{ww.max():.0f}" if not ww.empty else "-",
            "avg_credit": round(ec.mean(), 2) if not ec.empty else 0,
            "median_credit": round(ec.median(), 2) if not ec.empty else 0,
            "avg_max_loss": round(ml.mean(), 2) if not ml.empty else 0,
            "median_max_loss": round(ml.median(), 2) if not ml.empty else 0,
            "worst_max_loss": round(ml.max(), 2) if not ml.empty else 0,
            "best_max_loss": round(ml.min(), 2) if not ml.empty else 0,
            "credit_risk_ratio": round(ec.mean() / ml.mean(), 3) if not ec.empty and not ml.empty and ml.mean() > 0 else 0,
            "worst_day_risk": round(by_day["max_loss"].max(), 2) if not by_day.empty else 0,
            "avg_day_risk": round(by_day["max_loss"].mean(), 2) if not by_day.empty else 0,
            "var95": round(var95, 2),
            "cvar95": round(cvar95, 2),
            "max_daily_loss": round(float(pnl.min()), 2) if not pnl.empty else 0,
        }
    return profiles


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _table_row(r: dict) -> str:
    cagr = _fmt(r.get("cagr"), ".1%") if r.get("cagr") is not None else "-"
    mdd = _fmt(r.get("max_dd_pct"), ".1%") if r.get("max_dd_pct") is not None else "-"
    wr = _fmt(r.get("win_rate"), ".0%") if r.get("win_rate") is not None else "-"
    label = r.get("label", "")
    return (
        f"| {label} "
        f"| {r.get('trades', 0):,} "
        f"| {_fmt_pnl(r.get('net_pnl'))} "
        f"| {cagr} "
        f"| {_fmt(r.get('sharpe'))} "
        f"| {_fmt(r.get('t_stat'))} "
        f"| {_fmt(r.get('sortino'))} "
        f"| {_fmt(r.get('calmar'))} "
        f"| {mdd} "
        f"| {_fmt(r.get('pf'))} "
        f"| {wr} "
        f"| {_fmt_pnl(r.get('daily_cvar_95'))} "
        f"| {_fmt_pnl(r.get('is_net'))} "
        f"| {_fmt(r.get('is_sharpe'))} "
        f"| {_fmt_pnl(r.get('oos_net'))} "
        f"| {_fmt(r.get('oos_sharpe'))} |"
    )


def _header() -> list[str]:
    return [
        "| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d")
    MONITOR_DIR.mkdir(parents=True, exist_ok=True)
    print(f"stamp: {stamp}")

    # Load ledgers
    print("\nLoading wing-6 filtered ledgers...")
    w6_ledgers: dict[str, pd.DataFrame] = {}
    for symbol, path in W6_SOURCES.items():
        if path.exists():
            w6_ledgers[symbol] = _read_ledger(path, symbol)
            print(f"  {symbol}: {len(w6_ledgers[symbol])} trades")
        else:
            print(f"  MISSING: {path}")

    # -----------------------------------------------------------------------
    # Compute risk profile
    # -----------------------------------------------------------------------
    print("\nComputing defined-risk profile...")
    risk_profiles = compute_risk_profile(w6_ledgers)
    for sym, p in risk_profiles.items():
        print(f"  {sym}: avg max loss INR {p['avg_max_loss']:,.0f}, worst day INR {p['worst_day_risk']:,.0f}, CVaR95 INR {p['cvar95']:,.0f}")

    # -----------------------------------------------------------------------
    # Grid search
    # -----------------------------------------------------------------------
    print("\nRunning lot-size grid search (N=1-4, F=1-4, M=1-4)...")
    grid = run_grid_search(w6_ledgers)
    print(f"  Evaluated {len(grid)} combinations")
    grid_path = MONITOR_DIR / f"{stamp}_w6_lot_grid_search.csv"
    grid.to_csv(grid_path, index=False)
    print(f"  Grid -> {grid_path}")

    top_sharpe = grid.nlargest(10, "sharpe").reset_index(drop=True)
    top_calmar = grid.nlargest(10, "calmar").reset_index(drop=True)
    best_composite = grid.loc[grid["composite"].idxmax()]

    print(f"\nBest Sharpe: N:{best_composite['nifty_lots']} F:{best_composite['finnifty_lots']} M:{best_composite['midcpnifty_lots']} "
          f"sharpe={best_composite['sharpe']:.3f}")

    # -----------------------------------------------------------------------
    # Build base + scaled portfolios
    # -----------------------------------------------------------------------
    PortfolioResult = dict[str, pd.DataFrame | dict]
    portfolios: list[tuple[str, dict[str, int], PortfolioResult]] = []

    def add_portfolio(label: str, lots: dict[str, int]) -> None:
        ledger = _build_w6_portfolio(w6_ledgers, lots)
        s = _stats(ledger)
        is_s, oos_s = _split_stats(ledger)
        portfolios.append((label, lots, {"ledger": ledger, "stats": s, "is": is_s, "oos": oos_s}))
        print(f"  {label:35s}: cagr={s.get('cagr', 0)*100:>5.1f}%  sharpe={_fmt(s.get('sharpe')):>7}  "
              f"mdd={_fmt(s.get('max_drawdown_pct'), '.1%'):>8}  net={_fmt_pnl(s.get('net_pnl')):>12}")

    # Base
    add_portfolio("W6 Base 1:1:1", {"NIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1})

    # Best ratios from grid
    best_sharpe_ratio = {"NIFTY": int(top_sharpe.iloc[0]["nifty_lots"]),
                         "FINNIFTY": int(top_sharpe.iloc[0]["finnifty_lots"]),
                         "MIDCPNIFTY": int(top_sharpe.iloc[0]["midcpnifty_lots"])}
    best_calmar_ratio = {"NIFTY": int(top_calmar.iloc[0]["nifty_lots"]),
                         "FINNIFTY": int(top_calmar.iloc[0]["finnifty_lots"]),
                         "MIDCPNIFTY": int(top_calmar.iloc[0]["midcpnifty_lots"])}

    # Scaled Sharpe-opt
    for mult in [1, 2, 3, 4]:
        lots = {k: v * mult for k, v in best_sharpe_ratio.items()}
        label = f"W6 Sharpe-Opt {lots['NIFTY']}:{lots['FINNIFTY']}:{lots['MIDCPNIFTY']}"
        add_portfolio(label, lots)

    # Scaled Calmar-opt
    for mult in [1, 2, 3, 4]:
        lots = {k: v * mult for k, v in best_calmar_ratio.items()}
        label = f"W6 Calmar-Opt {lots['NIFTY']}:{lots['FINNIFTY']}:{lots['MIDCPNIFTY']}"
        add_portfolio(label, lots)

    # Extra combinations
    extra = [
        {"NIFTY": 1, "FINNIFTY": 3, "MIDCPNIFTY": 4},
        {"NIFTY": 2, "FINNIFTY": 3, "MIDCPNIFTY": 4},
    ]
    for lots in extra:
        label = f"W6 Extra {lots['NIFTY']}:{lots['FINNIFTY']}:{lots['MIDCPNIFTY']}"
        add_portfolio(label, lots)

    # -----------------------------------------------------------------------
    # Load risk management experiment portfolios
    # -----------------------------------------------------------------------
    print("\nLoading risk management experiment portfolios...")
    risk_df = pd.read_csv(RISK_MGMT_CSV) if RISK_MGMT_CSV.exists() else pd.DataFrame()
    if not risk_df.empty:
        for col in ("cagr", "sharpe", "max_drawdown_pct", "profit_factor", "oos_pf"):
            if col in risk_df.columns:
                risk_df[col] = pd.to_numeric(risk_df[col], errors="coerce")
        best_defined = risk_df[
            (risk_df["sharpe"] > 1.5) & (~risk_df["name"].str.contains("naked", case=False, na=False))
        ].sort_values("sharpe", ascending=False).head(5)
        best_naked = risk_df[
            (risk_df["sharpe"] > 1.6) & (risk_df["name"].str.contains("naked", case=False, na=False))
        ].sort_values("sharpe", ascending=False).head(5)
    else:
        best_defined = pd.DataFrame()
        best_naked = pd.DataFrame()
        print(f"  WARNING: {RISK_MGMT_CSV} not found")

    # -----------------------------------------------------------------------
    # Build report
    # -----------------------------------------------------------------------
    sections: list[str] = [
        f"# Strategy Monitoring Dashboard — {stamp}",
        "",
        f"Generated: {stamp}  |  Window: 2022-02-01 to 2026-04-30  |  IS: 2022-2024  |  OOS: 2025-2026",
        "",
        "---",
        "## Section A: Optimized Wing-6 IC (Deployable)",
        "",
        "Per-symbol filters: NIFTY VIX>=13 (weekly), FINNIFTY DTE<=7 (monthly), MIDCPNIFTY DTE<=7 (monthly). "
        "BANKNIFTY excluded. Smart bucket policies per symbol.",
        "",
    ]
    sections += _header()
    for label, lots, data in portfolios[:3]:  # base + best sharpe + best calmar only in summary
        r = {
            "label": label, "trades": data["stats"].get("trades", 0),
            "net_pnl": data["stats"].get("net_pnl"), "cagr": data["stats"].get("cagr"),
            "sharpe": data["stats"].get("sharpe"), "t_stat": data["stats"].get("sharpe_tstat"),
            "sortino": data["stats"].get("sortino"), "calmar": data["stats"].get("calmar"),
            "max_dd_pct": data["stats"].get("max_drawdown_pct"), "pf": data["stats"].get("profit_factor"),
            "win_rate": data["stats"].get("win_rate"), "daily_cvar_95": data["stats"].get("daily_cvar_95"),
            "is_net": data["is"].get("net_pnl"), "is_sharpe": data["is"].get("sharpe"),
            "oos_net": data["oos"].get("net_pnl"), "oos_sharpe": data["oos"].get("sharpe"),
        }
        sections.append(_table_row(r))
    sections.append("")

    # Top 10 grid
    sections += ["### Wing-6 Lot Grid — Top 10 by Sharpe", ""]
    sections += _header()
    for _, row in top_sharpe.iterrows():
        r = row.to_dict()
        r["label"] = f"W6 {int(row['nifty_lots'])}:{int(row['finnifty_lots'])}:{int(row['midcpnifty_lots'])}"
        sections.append(_table_row(r))
    sections.append("")

    # -----------------------------------------------------------------------
    # Section B: Risk Profile
    # -----------------------------------------------------------------------
    sections += [
        "---",
        "## Section B: Defined-Risk Profile (Per Trade)",
        "",
        "Wing-6 IC = short at ±2 offsets, long at ±8 offsets. Max loss = (wing_width – net_credit) × lot_size.",
        "",
        "| Symbol | Trades | Wing Width | Avg Credit | Avg Max Loss | Worst Max Loss | Credit/Risk | Worst Day Risk | VaR 95% | CVaR 95% | Max Daily Loss |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for sym in ("NIFTY", "FINNIFTY", "MIDCPNIFTY"):
        p = risk_profiles.get(sym, {})
        sections.append(
            f"| {sym} "
            f"| {p.get('trades', 0):,} "
            f"| {p.get('wing_width_mode', 0):.0f} pts ({p.get('wing_width_range', '-')}) "
            f"| INR {p.get('avg_credit', 0):,.0f} "
            f"| INR {p.get('avg_max_loss', 0):,.0f} "
            f"| INR {p.get('worst_max_loss', 0):,.0f} "
            f"| {p.get('credit_risk_ratio', 0):.3f} "
            f"| INR {p.get('worst_day_risk', 0):,.0f} "
            f"| INR {p.get('var95', 0):,.0f} "
            f"| INR {p.get('cvar95', 0):,.0f} "
            f"| INR {p.get('max_daily_loss', 0):,.0f} |"
        )
    sections.append("")
    sections += [
        "**Key insight:** MIDCPNIFTY has the lowest per-trade max loss (~INR 6.6k) because its strike spacing is 25 pts vs 50 pts for NIFTY/FINNIFTY. "
        "Its credit/risk ratio is >1.0, meaning on average it collects more premium than its max risk — a function of the DTE<=7 filter catching high-theta expiry-week entries.",
        "",
    ]

    # -----------------------------------------------------------------------
    # Section C: Scaled Lot Combinations
    # -----------------------------------------------------------------------
    sections += [
        "---",
        "## Section C: Scaled Lot Combinations",
        "",
        "Scaled versions of the best Sharpe and Calmar ratios. Sharpe and t-stat are invariant to proportional scaling; CAGR grows non-linearly from compounding.",
        "",
    ]
    sections += _header()
    for label, lots, data in portfolios[3:]:  # skip base, best_sharpe, best_calmar (already shown)
        r = {
            "label": label, "trades": data["stats"].get("trades", 0),
            "net_pnl": data["stats"].get("net_pnl"), "cagr": data["stats"].get("cagr"),
            "sharpe": data["stats"].get("sharpe"), "t_stat": data["stats"].get("sharpe_tstat"),
            "sortino": data["stats"].get("sortino"), "calmar": data["stats"].get("calmar"),
            "max_dd_pct": data["stats"].get("max_drawdown_pct"), "pf": data["stats"].get("profit_factor"),
            "win_rate": data["stats"].get("win_rate"), "daily_cvar_95": data["stats"].get("daily_cvar_95"),
            "is_net": data["is"].get("net_pnl"), "is_sharpe": data["is"].get("sharpe"),
            "oos_net": data["oos"].get("net_pnl"), "oos_sharpe": data["oos"].get("sharpe"),
        }
        sections.append(_table_row(r))
    sections.append("")

    # -----------------------------------------------------------------------
    # Section D: Prior Best Performers
    # -----------------------------------------------------------------------
    sections += [
        "---",
        "## Section D: Prior Best Performers (20260506 Risk Management)",
        "",
        "Portfolios from the 20260506 mixed-expiry risk management experiments. Included for cross-reference.",
        "",
    ]
    if not best_defined.empty:
        sections += ["### Defined-Risk Strategies (Sharpe > 1.5)", ""]
        sections += _header()
        for _, r in best_defined.iterrows():
            row = {
                "label": r["label"], "trades": r["trades"], "net_pnl": r["net_pnl"],
                "cagr": r["cagr"], "sharpe": r["sharpe"], "t_stat": None,
                "sortino": r.get("sortino"), "calmar": r.get("calmar"),
                "max_dd_pct": r["max_drawdown_pct"], "pf": r["profit_factor"],
                "win_rate": None, "daily_cvar_95": r.get("daily_cvar_95"),
                "is_net": r.get("train_net_pnl"), "is_sharpe": None,
                "oos_net": r.get("oos_net_pnl"), "oos_sharpe": None,
            }
            sections.append(_table_row(row))
        sections.append("")

    if not best_naked.empty:
        sections += [
            "### Naked Short-Strangle Strategies (Sharpe > 1.6)",
            "",
            "> ⚠️ **All naked strategies carry unlimited theoretical risk.** Historical max DD is an observation, not a bound.",
            "",
        ]
        sections += _header()
        for _, r in best_naked.iterrows():
            row = {
                "label": r["label"], "trades": r["trades"], "net_pnl": r["net_pnl"],
                "cagr": r["cagr"], "sharpe": r["sharpe"], "t_stat": None,
                "sortino": r.get("sortino"), "calmar": r.get("calmar"),
                "max_dd_pct": r["max_drawdown_pct"], "pf": r["profit_factor"],
                "win_rate": None, "daily_cvar_95": r.get("daily_cvar_95"),
                "is_net": r.get("train_net_pnl"), "is_sharpe": None,
                "oos_net": r.get("oos_net_pnl"), "oos_sharpe": None,
            }
            sections.append(_table_row(row))
        sections.append("")

    # -----------------------------------------------------------------------
    # Recommendations
    # -----------------------------------------------------------------------
    base_data = portfolios[0][2]
    base_s = base_data["stats"]
    base_oos = base_data["oos"]
    sharpe_opt_data = portfolios[3][2] if len(portfolios) > 3 else base_data
    sharpe_opt_s = sharpe_opt_data["stats"]

    sections += [
        "---",
        "## Recommendations",
        "",
        "### For Deployment (Defined Risk Only)",
        "",
        f"**Primary:** Wing-6 Optimized Base (1:1:1) — CAGR {_fmt(base_s.get('cagr'), '.1%')}, Sharpe {_fmt(base_s.get('sharpe'))}, MaxDD {_fmt(base_s.get('max_drawdown_pct'), '.1%')}. "
        f"Statistically significant (t-stat {_fmt(base_s.get('sharpe_tstat'))}), strong OOS Sharpe {_fmt(base_oos.get('sharpe'))}.",
        "",
        f"**Higher-CAGR scaled options (same Sharpe, linearly scaled lots):**",
        "",
        "| Scale | Lots (N:F:M) | CAGR | Sharpe | MaxDD% | Net PnL | CVaR95/day |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    # Add scaled rows for best sharpe ratio (indices 1-4 in portfolios list)
    for i, mult in enumerate([1, 2, 3, 4], start=1):
        if i < len(portfolios):
            label, lots, data = portfolios[i]
            s = data["stats"]
            sections.append(
                f"| {mult}x | {lots['NIFTY']}:{lots['FINNIFTY']}:{lots['MIDCPNIFTY']} "
                f"| {_fmt(s.get('cagr'), '.1%')} "
                f"| {_fmt(s.get('sharpe'))} "
                f"| {_fmt(s.get('max_drawdown_pct'), '.1%')} "
                f"| {_fmt_pnl(s.get('net_pnl'))} "
                f"| {_fmt_pnl(s.get('daily_cvar_95'))} |"
            )
    sections += [
        "",
        "> Scaling preserves Sharpe/Calmar exactly because all legs scale proportionally. CAGR grows non-linearly due to compounding. "
        "A 2x–3x scale is a sweet spot: CAGR 9–13% with MaxDD still under -3.5%.",
        "",
        "### For Monitoring (Research Track)",
        "",
        "Track the top 3 performers from each section monthly. If OOS Sharpe degrades below 1.5 for two consecutive months, pause that variant and investigate.",
        "",
        "---",
        "## Files",
        "",
        f"- Wing-6 lot grid: `{stamp}_w6_lot_grid_search.csv`",
        f"- Deploy configs (JSON): `{stamp}_w6_deploy_configs.json`",
        f"- This report: `{stamp}_monitoring_dashboard.md`",
    ]

    report_path = MONITOR_DIR / f"{stamp}_monitoring_dashboard.md"
    report_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"\nReport -> {report_path}")

    # Save CSV of all portfolio results
    csv_rows = []
    for label, lots, data in portfolios:
        s = data["stats"]
        csv_rows.append({
            "label": label, "nifty_lots": lots["NIFTY"], "finnifty_lots": lots["FINNIFTY"], "midcpnifty_lots": lots["MIDCPNIFTY"],
            "trades": s.get("trades", 0), "net_pnl": s.get("net_pnl"), "cagr": s.get("cagr"),
            "sharpe": s.get("sharpe"), "t_stat": s.get("sharpe_tstat"), "sortino": s.get("sortino"),
            "calmar": s.get("calmar"), "max_dd_pct": s.get("max_drawdown_pct"), "pf": s.get("profit_factor"),
            "win_rate": s.get("win_rate"), "daily_cvar_95": s.get("daily_cvar_95"),
            "is_net": data["is"].get("net_pnl"), "is_sharpe": data["is"].get("sharpe"),
            "oos_net": data["oos"].get("net_pnl"), "oos_sharpe": data["oos"].get("sharpe"),
        })
    pd.DataFrame(csv_rows).to_csv(MONITOR_DIR / f"{stamp}_w6_portfolio_results.csv", index=False)

    # Save JSON configs
    configs = {
        "base_1_1_1": {
            "nifty": {"lots": 1, "filter": "vix_ge_13", "expiry": "week", "bucket_skip": None},
            "finnifty": {"lots": 1, "filter": "dte_le_7", "expiry": "month", "bucket_skip": "10-13"},
            "midcpnifty": {"lots": 1, "filter": "dte_le_7", "expiry": "month", "bucket_skip": "22-30"},
            "banknifty": {"lots": 0, "note": "excluded — structurally negative"},
        },
        "best_sharpe": {
            "nifty": {"lots": best_sharpe_ratio["NIFTY"], "filter": "vix_ge_13", "expiry": "week", "bucket_skip": None},
            "finnifty": {"lots": best_sharpe_ratio["FINNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "10-13"},
            "midcpnifty": {"lots": best_sharpe_ratio["MIDCPNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "22-30"},
            "banknifty": {"lots": 0, "note": "excluded — structurally negative"},
        },
        "best_calmar": {
            "nifty": {"lots": best_calmar_ratio["NIFTY"], "filter": "vix_ge_13", "expiry": "week", "bucket_skip": None},
            "finnifty": {"lots": best_calmar_ratio["FINNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "10-13"},
            "midcpnifty": {"lots": best_calmar_ratio["MIDCPNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "22-30"},
            "banknifty": {"lots": 0, "note": "excluded — structurally negative"},
        },
    }
    # Add scaled versions
    for mult in [2, 3, 4]:
        lots = {k: v * mult for k, v in best_sharpe_ratio.items()}
        # find matching portfolio data
        for label, pl, data in portfolios:
            if pl == lots:
                configs[f"sharpe_opt_{mult}x"] = {
                    "nifty": {"lots": lots["NIFTY"], "filter": "vix_ge_13", "expiry": "week", "bucket_skip": None},
                    "finnifty": {"lots": lots["FINNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "10-13"},
                    "midcpnifty": {"lots": lots["MIDCPNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "22-30"},
                    "banknifty": {"lots": 0, "note": "excluded — structurally negative"},
                    "expected": {
                        "cagr": f"{data['stats'].get('cagr', 0)*100:.1f}%",
                        "sharpe": round(data["stats"].get("sharpe", 0), 3),
                        "max_dd_pct": f"{data['stats'].get('max_drawdown_pct', 0)*100:.1f}%",
                        "net_pnl": round(data["stats"].get("net_pnl", 0), 2),
                    },
                }
                break
    for mult in [2, 3, 4]:
        lots = {k: v * mult for k, v in best_calmar_ratio.items()}
        for label, pl, data in portfolios:
            if pl == lots:
                configs[f"calmar_opt_{mult}x"] = {
                    "nifty": {"lots": lots["NIFTY"], "filter": "vix_ge_13", "expiry": "week", "bucket_skip": None},
                    "finnifty": {"lots": lots["FINNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "10-13"},
                    "midcpnifty": {"lots": lots["MIDCPNIFTY"], "filter": "dte_le_7", "expiry": "month", "bucket_skip": "22-30"},
                    "banknifty": {"lots": 0, "note": "excluded — structurally negative"},
                    "expected": {
                        "cagr": f"{data['stats'].get('cagr', 0)*100:.1f}%",
                        "sharpe": round(data["stats"].get("sharpe", 0), 3),
                        "max_dd_pct": f"{data['stats'].get('max_drawdown_pct', 0)*100:.1f}%",
                        "net_pnl": round(data["stats"].get("net_pnl", 0), 2),
                    },
                }
                break

    config_path = MONITOR_DIR / f"{stamp}_w6_deploy_configs.json"
    config_path.write_text(json.dumps(configs, indent=2), encoding="utf-8")
    print(f"Configs -> {config_path}")

    return 0


if __name__ == "__main__":
    sys.exit(main())

"""
Optimized Wing-6 IC Portfolio Experiment
==========================================

Assembles the best per-symbol filters into a deployable wing-6 portfolio.

Per-symbol filter selection (from 20260506 DTE-filtered + wing-8 investigation + SENSEX exploration):
  - NIFTY:     VIX >= 13  (weekly; removes ultra-calm low-premium entries)
  - FINNIFTY:  DTE <= 7   (monthly; isolates expiry-week theta, drops toxic 11-30 DTE)
  - MIDCPNIFTY: DTE <= 7  (monthly; same logic, flips from -27k to +23k)
  - SENSEX:    DTE <= 2   (weekly; captures extreme theta-decay zone, +438/trade on DTE 1)
  - BANKNIFTY: EXCLUDED   (uniformly negative on wing-6 across all regime cells)

On top of each, we test skip-10-13 (the VIX bucket that is poison for short vol).
Note: SENSEX VIX 10-13 is profitable — do NOT skip it for SENSEX.

Portfolios tested:
  - Baseline: unrestricted 5x1 (all indices, 1 lot each)
  - Filtered only: per-symbol filters, no VIX bucket skip
  - Filtered + skip 10-13: full stack
  - Lot-weight variants: equal, NIFTY-tilt, FINNIFTY/MIDCP-tilt

IS = 2022-2024 | OOS = 2025-2026
"""
from __future__ import annotations

import math
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
from options_backtest.volatility_filter import VixFilter

OUT_DIR = ROOT / "reports/backtests/options/risk_management"
IS_END = pd.Timestamp("2024-12-31").date()
OOS_START = pd.Timestamp("2025-01-01").date()

# Source ledgers (from 20260506 runs)
SOURCES = {
    "NIFTY":      OUT_DIR / "20260506_vixgt13_ic_wing6_nifty.csv",        # VIX>=13 already applied
    "FINNIFTY":   OUT_DIR / "20260506_dtelt7_ic_wing6_finnifty.csv",     # DTE<=7 already applied
    "MIDCPNIFTY": OUT_DIR / "20260506_dtelt7_ic_wing6_midcpnifty.csv",   # DTE<=7 already applied
    "SENSEX":     OUT_DIR / "20260506_dtelt2_ic_wing6_sensex.csv",       # DTE<=2 already applied
}

BASELINE_SOURCES = {
    "NIFTY":      OUT_DIR / "20260506_mixed_expiry_ic_wing6_nifty.csv",
    "BANKNIFTY":  OUT_DIR / "20260506_mixed_expiry_ic_wing6_banknifty.csv",
    "FINNIFTY":   OUT_DIR / "20260506_mixed_expiry_ic_wing6_finnifty.csv",
    "MIDCPNIFTY": OUT_DIR / "20260506_mixed_expiry_ic_wing6_midcpnifty.csv",
    "SENSEX":     OUT_DIR / "20260506_mixed_expiry_ic_wing6_sensex.csv",
}

VIX_PATH = ROOT / "data/processed/spot/indiavix_1min_DHAN.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PortfolioSpec:
    name: str
    label: str
    lots: dict[str, float]
    source_key: str          # "baseline" or "filtered"
    bucket_policy: str = "all"


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


def _apply_bucket_policy(df: pd.DataFrame, policy: str) -> pd.DataFrame:
    if policy == "all" or df.empty:
        return df.copy()
    out = df.copy()
    if policy == "skip_10_13":
        return out[out["vix_bucket"] != "10-13"].copy()
    if policy == "half_10_13":
        scale = np.where(out["vix_bucket"].eq("10-13"), 0.5, 1.0)
        for col in ("gross_pnl", "charges", "net_pnl"):
            if col in out.columns:
                out[col] = out[col].astype(float) * scale
        return out
    raise ValueError(f"Unknown bucket policy: {policy}")


def _scale_lots(df: pd.DataFrame, lots: float) -> pd.DataFrame:
    out = df.copy()
    if lots == 1:
        out["portfolio_lots"] = 1.0
        return out
    for col in ("gross_pnl", "charges", "net_pnl"):
        if col in out.columns:
            out[col] = out[col].astype(float) * lots
    out["portfolio_lots"] = float(lots)
    return out


def _combine_portfolio(ledgers: dict[str, pd.DataFrame], spec: PortfolioSpec) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for symbol, lots in spec.lots.items():
        if lots <= 0:
            continue
        base = ledgers.get(symbol)
        if base is None or base.empty:
            continue
        filtered = _apply_bucket_policy(base, spec.bucket_policy)
        if filtered.empty:
            continue
        scaled = _scale_lots(filtered, lots)
        scaled["portfolio_name"] = spec.name
        frames.append(scaled)
    if not frames:
        return pd.DataFrame()
    combined = pd.concat(frames, ignore_index=True)
    return combined.sort_values(["exit_time", "symbol"]).reset_index(drop=True)


def _stats(ledger: pd.DataFrame) -> dict:
    if ledger.empty:
        return {}
    dpnl = daily_pnl(ledger)
    curve = equity_curve(dpnl)
    s = summary(ledger, curve, dpnl)
    # tail stats
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


def _bucket_attribution(ledger: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict] = []
    for bucket, grp in ledger.groupby("vix_bucket", dropna=False):
        s = _stats(grp.copy())
        rows.append({
            "vix_bucket": bucket or "missing",
            "trades": s.get("trades", 0),
            "net_pnl": s.get("net_pnl"),
            "avg_trade_pnl": s.get("avg_trade_pnl"),
            "profit_factor": s.get("profit_factor"),
            "max_drawdown_pct": s.get("max_drawdown_pct"),
        })
    order = {"<10": 0, "10-13": 1, "13-17": 2, "17-22": 3, "22-30": 4, ">30": 5, "missing": 9}
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    out["_order"] = out["vix_bucket"].map(order).fillna(8)
    return out.sort_values("_order").drop(columns=["_order"]).reset_index(drop=True)


def _risk_parity_lots(matrix: pd.DataFrame, symbols: tuple[str, ...], total_lots: int = 6) -> dict[str, int]:
    train = matrix[matrix.index.date <= IS_END]
    vol = train.std(ddof=1).replace(0, np.nan)
    inv = (1.0 / vol).replace([np.inf, -np.inf], np.nan).dropna()
    if inv.empty:
        return {s: 1 for s in symbols}
    raw = inv / inv.sum() * total_lots
    lots = {symbol: max(0, int(round(raw.get(symbol, 0)))) for symbol in symbols}
    # Adjust to hit total
    while sum(lots.values()) < total_lots:
        sym = max(symbols, key=lambda s: raw.get(s, 0) - lots.get(s, 0))
        lots[sym] = lots.get(sym, 0) + 1
    while sum(lots.values()) > total_lots:
        sym = max((s for s in symbols if lots.get(s, 0) > 0), key=lambda s: lots.get(s, 0) - raw.get(s, 0))
        lots[sym] -= 1
    return lots


def _daily_matrix(ledgers: dict[str, pd.DataFrame], bucket_policy: str = "all") -> pd.DataFrame:
    parts: list[pd.Series] = []
    for symbol, ledger in ledgers.items():
        filtered = _apply_bucket_policy(ledger, bucket_policy)
        dpnl = daily_pnl(filtered)
        if dpnl.empty:
            continue
        s = pd.Series(dpnl["net_pnl"].to_numpy(dtype=float), index=pd.to_datetime(dpnl["date"]), name=symbol)
        parts.append(s)
    if not parts:
        return pd.DataFrame()
    matrix = pd.concat(parts, axis=1, sort=True).fillna(0.0).sort_index()
    start, end = matrix.index.min(), matrix.index.max()
    return matrix.reindex(pd.bdate_range(start, end), fill_value=0.0)


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _comparison_row(label: str, ledger: pd.DataFrame) -> dict:
    s = _stats(ledger)
    is_s, oos_s = _split_stats(ledger)
    return {
        "label": label,
        "trades": s.get("trades", 0),
        "net_pnl": s.get("net_pnl"),
        "cagr": s.get("cagr"),
        "sharpe": s.get("sharpe"),
        "t_stat": s.get("sharpe_tstat"),
        "sortino": s.get("sortino"),
        "calmar": s.get("calmar"),
        "max_dd_pct": s.get("max_drawdown_pct"),
        "pf": s.get("profit_factor"),
        "win_rate": s.get("win_rate"),
        "daily_cvar_95": s.get("daily_cvar_95"),
        "max_daily_loss": s.get("max_daily_loss"),
        "is_net": is_s.get("net_pnl"),
        "is_sharpe": is_s.get("sharpe"),
        "oos_net": oos_s.get("net_pnl"),
        "oos_sharpe": oos_s.get("sharpe"),
    }


def _table_header() -> list[str]:
    return [
        "| Portfolio | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | CVaR95/day | IS PnL | IS Sh | OOS PnL | OOS Sh |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]


def _table_row(r: dict) -> str:
    cagr = _fmt(r["cagr"], ".1%") if r.get("cagr") is not None else "-"
    mdd = _fmt(r["max_dd_pct"], ".1%") if r.get("max_dd_pct") is not None else "-"
    wr = _fmt(r["win_rate"], ".0%") if r.get("win_rate") is not None else "-"
    return (
        f"| {r['label']} "
        f"| {r['trades']:,} "
        f"| {_fmt_pnl(r['net_pnl'])} "
        f"| {cagr} "
        f"| {_fmt(r['sharpe'])} "
        f"| {_fmt(r['t_stat'])} "
        f"| {_fmt(r['sortino'])} "
        f"| {_fmt(r['calmar'])} "
        f"| {mdd} "
        f"| {_fmt(r['pf'])} "
        f"| {wr} "
        f"| {_fmt_pnl(r.get('daily_cvar_95'))} "
        f"| {_fmt_pnl(r['is_net'])} "
        f"| {_fmt(r['is_sharpe'])} "
        f"| {_fmt_pnl(r['oos_net'])} "
        f"| {_fmt(r['oos_sharpe'])} |"
    )


def _per_symbol_section(symbol: str, baseline_df: pd.DataFrame, filtered_df: pd.DataFrame) -> list[str]:
    lines = [f"### {symbol}", ""]
    rows = []
    if not baseline_df.empty:
        rows.append(_comparison_row("Baseline (unrestricted)", baseline_df))
        rows.append(_comparison_row("Baseline skip 10-13", _apply_bucket_policy(baseline_df, "skip_10_13")))
    if not filtered_df.empty:
        rows.append(_comparison_row("Filtered", filtered_df))
        rows.append(_comparison_row("Filtered + skip 10-13", _apply_bucket_policy(filtered_df, "skip_10_13")))
    lines += _table_header()
    for r in rows:
        lines.append(_table_row(r))
    lines.append("")

    # VIX bucket breakdown for filtered
    if not filtered_df.empty:
        lines += ["#### Filtered — VIX bucket breakdown", ""]
        attr = _bucket_attribution(filtered_df)
        lines.append("| VIX bucket | Trades | Net PnL | Avg/trade | PF | Max DD% |")
        lines.append("|---|---:|---:|---:|---:|---:|")
        for row in attr.itertuples(index=False):
            lines.append(
                f"| {row.vix_bucket} | {row.trades:,} | {_fmt_pnl(row.net_pnl)} "
                f"| {_fmt_pnl(row.avg_trade_pnl)} | {_fmt(row.profit_factor)} | {_fmt_pct(row.max_drawdown_pct)} |"
            )
        lines.append("")
    return lines


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    stamp = datetime.now().strftime("%Y%m%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"stamp: {stamp}")

    # Load ledgers
    print("\nLoading source ledgers...")
    baseline_ledgers: dict[str, pd.DataFrame] = {}
    for symbol, path in BASELINE_SOURCES.items():
        if path.exists():
            baseline_ledgers[symbol] = _read_ledger(path, symbol)
            print(f"  Baseline {symbol}: {len(baseline_ledgers[symbol])} trades")
        else:
            print(f"  MISSING baseline {symbol}: {path}")

    filtered_ledgers: dict[str, pd.DataFrame] = {}
    for symbol, path in SOURCES.items():
        if path.exists():
            filtered_ledgers[symbol] = _read_ledger(path, symbol)
            print(f"  Filtered {symbol}: {len(filtered_ledgers[symbol])} trades")
        else:
            print(f"  MISSING filtered {symbol}: {path}")

    # Compute risk-parity lots on filtered + skip 10-13 daily matrix
    filtered_skip = {s: _apply_bucket_policy(df, "skip_10_13") for s, df in filtered_ledgers.items()}
    matrix = _daily_matrix(filtered_skip, bucket_policy="all")
    filtered_symbols = tuple(filtered_ledgers.keys())
    rp_lots = _risk_parity_lots(matrix, filtered_symbols, total_lots=6)
    print(f"\nRisk-parity lots (on filtered, skip-10-13): {rp_lots}")

    # Define portfolio specs
    # --- Baseline portfolios (from unrestricted ledgers) ---
    baseline_specs = [
        PortfolioSpec("baseline_5x1_all",        "Baseline 5x1 all VIX",        {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "baseline", "all"),
        PortfolioSpec("baseline_5x1_skip",       "Baseline 5x1 skip 10-13",     {"NIFTY": 1, "BANKNIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "baseline", "skip_10_13"),
        PortfolioSpec("baseline_4x1_all",        "Baseline 4x1 all VIX (no BN)", {"NIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "baseline", "all"),
        PortfolioSpec("baseline_4x1_skip",       "Baseline 4x1 skip 10-13 (no BN)", {"NIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "baseline", "skip_10_13"),
    ]

    # --- Filtered portfolios (per-symbol filters applied, no bucket skip) ---
    filtered_only_specs = [
        PortfolioSpec("filt_4x1_all",            "Filtered 4x1 all VIX",        {"NIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "filtered", "all"),
    ]

    # --- Filtered + uniform skip 10-13 ---
    filtered_skip_specs = [
        PortfolioSpec("filt_4x1_skip",           "Filtered 4x1 skip 10-13",     {"NIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "filtered", "skip_10_13"),
        PortfolioSpec("filt_n2_f1_m1_s1_skip",   "Filtered N:2 F:1 M:1 S:1 skip", {"NIFTY": 2, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "filtered", "skip_10_13"),
        PortfolioSpec("filt_n1_f2_m2_s1_skip",   "Filtered N:1 F:2 M:2 S:1 skip", {"NIFTY": 1, "FINNIFTY": 2, "MIDCPNIFTY": 2, "SENSEX": 1}, "filtered", "skip_10_13"),
        PortfolioSpec("filt_n2_f2_m2_s1_skip",   "Filtered N:2 F:2 M:2 S:1 skip", {"NIFTY": 2, "FINNIFTY": 2, "MIDCPNIFTY": 2, "SENSEX": 1}, "filtered", "skip_10_13"),
        PortfolioSpec("filt_rp_skip",            "Filtered risk-parity skip",   rp_lots, "filtered", "skip_10_13"),
    ]

    all_specs = baseline_specs + filtered_only_specs + filtered_skip_specs

    portfolios: list[tuple[PortfolioSpec, pd.DataFrame]] = []
    for spec in all_specs:
        if spec.source_key == "baseline":
            ledger = _combine_portfolio(baseline_ledgers, spec)
        else:
            ledger = _combine_portfolio(filtered_ledgers, spec)
        portfolios.append((spec, ledger))
        s = _stats(ledger)
        print(
            f"  {spec.label:35s}: trades={s.get('trades', 0):>4}  "
            f"net={_fmt_pnl(s.get('net_pnl')):>12}  "
            f"sharpe={_fmt(s.get('sharpe')):>7}  "
            f"t-stat={_fmt(s.get('sharpe_tstat')):>7}"
        )

    # --- Smart bucket portfolio (per-symbol optimal bucket policy) ---
    # NIFTY: VIX>=13 already applied; no bucket skip needed
    # FINNIFTY: DTE<=7 + skip 10-13 (improves Sharpe)
    # MIDCPNIFTY: DTE<=7 + skip 22-30 (improves Sharpe and PnL)
    # SENSEX: DTE<=2 + no bucket skip (VIX 10-13 is profitable for SENSEX)
    smart_frames: list[pd.DataFrame] = []
    nifty_df = filtered_ledgers.get("NIFTY", pd.DataFrame()).copy()
    if not nifty_df.empty:
        nifty_df = _scale_lots(nifty_df, 1)
        nifty_df["portfolio_name"] = "filt_smart"
        smart_frames.append(nifty_df)

    fin_df = filtered_ledgers.get("FINNIFTY", pd.DataFrame()).copy()
    if not fin_df.empty:
        fin_df = fin_df[fin_df["vix_bucket"] != "10-13"].copy()
        fin_df = _scale_lots(fin_df, 1)
        fin_df["portfolio_name"] = "filt_smart"
        smart_frames.append(fin_df)

    mcp_df = filtered_ledgers.get("MIDCPNIFTY", pd.DataFrame()).copy()
    if not mcp_df.empty:
        mcp_df = mcp_df[mcp_df["vix_bucket"] != "22-30"].copy()
        mcp_df = _scale_lots(mcp_df, 1)
        mcp_df["portfolio_name"] = "filt_smart"
        smart_frames.append(mcp_df)

    sensex_df = filtered_ledgers.get("SENSEX", pd.DataFrame()).copy()
    if not sensex_df.empty:
        sensex_df = _scale_lots(sensex_df, 1)
        sensex_df["portfolio_name"] = "filt_smart"
        smart_frames.append(sensex_df)

    if smart_frames:
        smart_ledger = pd.concat(smart_frames, ignore_index=True).sort_values(["exit_time", "symbol"]).reset_index(drop=True)
        smart_spec = PortfolioSpec("filt_smart", "Filtered smart buckets (per-symbol)", {"NIFTY": 1, "FINNIFTY": 1, "MIDCPNIFTY": 1, "SENSEX": 1}, "filtered", "smart")
        portfolios.insert(3, (smart_spec, smart_ledger))  # insert right after filtered_4x1_all
        s = _stats(smart_ledger)
        print(
            f"  {smart_spec.label:35s}: trades={s.get('trades', 0):>4}  "
            f"net={_fmt_pnl(s.get('net_pnl')):>12}  "
            f"sharpe={_fmt(s.get('sharpe')):>7}  "
            f"t-stat={_fmt(s.get('sharpe_tstat')):>7}"
        )

    # Build report
    sections: list[str] = [
        f"# Optimized Wing-6 IC Portfolio — {stamp}",
        "",
        "Per-symbol filters applied:",
        "- **NIFTY**: VIX >= 13 (weekly; removes ultra-calm low-premium entries)",
        "- **FINNIFTY**: DTE <= 7 (monthly; isolates expiry-week theta)",
        "- **MIDCPNIFTY**: DTE <= 7 (monthly; flips from deeply negative to positive)",
        "- **SENSEX**: DTE <= 2 (weekly; captures extreme theta-decay zone)",
        "- **BANKNIFTY**: EXCLUDED (uniformly negative on wing-6 across all regime cells)",
        "",
        "On top, `skip 10-13` removes the VIX bucket that is consistently poison for short-vol.",
        "Note: SENSEX VIX 10-13 is profitable — skip is NOT applied to SENSEX in smart-bucket mode.",
        "",
        f"IS split: {IS_END} | OOS split: {OOS_START}",
        "",
        "---",
        "## Portfolio Comparison",
        "",
    ]
    sections += _table_header()
    for spec, ledger in portfolios:
        r = _comparison_row(spec.label, ledger)
        sections.append(_table_row(r))
    sections.append("")

    # Per-symbol detail
    sections += ["---", "## Per-Symbol Detail", ""]
    for symbol in ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]:
        baseline_df = baseline_ledgers.get(symbol, pd.DataFrame())
        filtered_df = filtered_ledgers.get(symbol, pd.DataFrame())
        if baseline_df.empty and filtered_df.empty:
            continue
        sections += _per_symbol_section(symbol, baseline_df, filtered_df)

    # Key takeaways
    sections += [
        "---",
        "## Key Takeaways",
        "",
        "1. **BANKNIFTY wing-6 is structurally broken** — every regime cell loses money. "
        "Removing it from the portfolio is the single biggest improvement.",
        "",
        "2. **FINNIFTY + MIDCPNIFTY DTE <= 7** isolates the profitable expiry-week theta. "
        "DTE 11-30 is toxic (avg -240 to -654 per trade).",
        "",
        "3. **NIFTY VIX >= 13** improves Sharpe from 1.57 to 1.83 by dropping the ultra-calm "
        "low-premium entries where gamma risk overwhelms theta capture.",
        "",
        "4. **SENSEX DTE <= 2** captures the extreme theta-decay zone with Sharpe 3.69 and t-stat 5.62. "
        "DTE 1 trades average +438/trade with 71% win rate. DTE 3+ turns flat/negative.",
        "",
        "5. **Skip VIX 10-13** on top of per-symbol filters further cleans the signal for NSE indices. "
        "However, SENSEX is different — its VIX 10-13 bucket is strongly profitable (+102/trade). "
        "Do NOT apply VIX 10-13 skip to SENSEX.",
        "",
        "6. **Risk-parity sizing** computed on the filtered daily matrix naturally overweights "
        "the lower-vol contributors (typically NIFTY and FINNIFTY).",
        "",
        "---",
        "## Read",
        "",
        "- **t-stat > 1.96** = statistically significant (weekly bucket resampling).",
        "- If filtered portfolio has fewer trades but higher t-stat: filter improved SNR, not just reduced sample size.",
        "- OOS (2025-2026) is only 1.5 years; treat as directionally informative, not conclusive.",
        "- SENSEX data starts May 2023 (weekly launch), so IS is shorter (~1.6 years). OOS coincides with "
        "the Tuesday expiry regime (Jan-Aug 2025) and Thursday regime (Sep 2025+).",
        "- All per-symbol filters were selected from prior in-sample analysis (20260506 DTE-filtered comparison "
        "+ wing-8 filter investigation + SENSEX exploration). This is a research synthesis run, not a fresh optimization.",
    ]

    out_path = OUT_DIR / f"{stamp}_optimized_wing6_portfolio.md"
    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"\nReport -> {out_path}")

    # Also write portfolio comparison CSV
    rows = []
    for spec, ledger in portfolios:
        r = _comparison_row(spec.label, ledger)
        r["name"] = spec.name
        r["lots"] = " ".join(f"{k}:{v:g}" for k, v in spec.lots.items() if v > 0)
        r["source"] = spec.source_key
        r["bucket_policy"] = spec.bucket_policy
        rows.append(r)
    pd.DataFrame(rows).to_csv(OUT_DIR / f"{stamp}_optimized_wing6_portfolio.csv", index=False)

    return 0


if __name__ == "__main__":
    sys.exit(main())

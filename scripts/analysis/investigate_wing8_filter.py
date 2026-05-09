"""
Wing-8 Data-Availability Filter Investigation
==============================================
The wing-8 IC only enters when ATM±10 strike data is available in Dhan.
The audit showed those days have significantly lower VIX and intraday range
(p < 0.001 for NIFTY/BANKNIFTY), and higher W6 PnL per trade than skipped days.

Hypothesis: data availability is a proxy for liquidity at the wings, which in
turn reflects calm market conditions. If we can characterise those conditions
with observable pre-market signals, we have a legit entry filter.

This script:
  1. Classifies every wing-6 entry date as shared (wing-8 also traded) vs
     skipped (wing-8 data missing).
  2. Enriches each date with pre-market-observable features:
       - vix_entry         : VIX at 9:20 AM on entry day (at-entry, same instant
                             as order placement — fine as a filter check)
       - range_prev        : prior trading day's intraday range% (fully pre-market)
       - hv5               : 5-day rolling avg of daily range% over prior 5 sessions
       - dte_at_entry      : days to expiry (from ledger)
       - day_of_week       : Monday=0 … Friday=4 (from ledger)
  3. For each feature finds the threshold that maximises F1 (shared=1) using
     exhaustive search over percentile grid.
  4. Tests filter combinations and shows capture rate + PnL delta vs no filter.
  5. Writes a per-trade table for the actual wing-8 trades with all features.
  6. Outputs a markdown report.

NOTE on causation: if we deploy this filter live, the test must be re-done on
OOS data (2025+). This script only characterises the IS sample.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STAMP      = "20260506_mixed_expiry"
LEDGER_DIR = ROOT / "reports/backtests/options/risk_management"
SPOT_PATHS = {
    "NIFTY":      ROOT / "data/processed/spot/nifty50_1min_CANONICAL.csv",
    "BANKNIFTY":  ROOT / "data/processed/spot/banknifty_1min_DHAN.csv",
    "FINNIFTY":   ROOT / "data/processed/spot/finnifty_1min_DHAN.csv",
    "MIDCPNIFTY": ROOT / "data/processed/spot/midcpnifty_1min_DHAN.csv",
}
INDICES  = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
OUT_DIR  = ROOT / "reports/backtests/options/risk_management"


# ---------------------------------------------------------------------------
# Data loading helpers
# ---------------------------------------------------------------------------

def _load_ledger(wing: int, symbol: str) -> pd.DataFrame:
    path = LEDGER_DIR / f"{STAMP}_ic_wing{wing}_{symbol.lower()}.csv"
    df   = pd.read_csv(path)
    if "strategy" in df.columns:
        df = df[df["strategy"] != "TOTAL"].copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce").dt.date
    df["expiry"]     = pd.to_datetime(df["expiry"],     errors="coerce").dt.date
    df["net_pnl"]    = pd.to_numeric(df["net_pnl"],   errors="coerce")
    df["vix_entry"]  = pd.to_numeric(df["vix_entry"], errors="coerce")
    df["dte_at_entry"] = pd.to_numeric(df.get("dte_at_entry", pd.Series(dtype=float)), errors="coerce")
    return df.reset_index(drop=True)


def _daily_ohlc(symbol: str) -> pd.DataFrame:
    """Load 1-min spot and aggregate to daily OHLC with range%."""
    path = SPOT_PATHS[symbol]
    if not path.exists():
        return pd.DataFrame()
    usecols = lambda c: c in ("datetime", "timestamp", "date", "open", "high", "low", "close")
    raw     = pd.read_csv(path, usecols=usecols)
    ts_col  = next((c for c in ("datetime", "timestamp", "date") if c in raw.columns), None)
    if ts_col is None:
        return pd.DataFrame()
    raw[ts_col] = pd.to_datetime(raw[ts_col], errors="coerce")
    raw["date"] = raw[ts_col].dt.date
    agg = raw.groupby("date").agg(
        day_open  = ("open",  "first"),
        day_high  = ("high",  "max"),
        day_low   = ("low",   "min"),
        day_close = ("close", "last"),
    ).reset_index()
    agg["range_pct"] = (agg["day_high"] - agg["day_low"]) / agg["day_open"] * 100
    agg = agg.sort_values("date").reset_index(drop=True)
    # Prior-day range (fully pre-market)
    agg["range_prev"] = agg["range_pct"].shift(1)
    # 5-day rolling avg of prior ranges (lagged so no lookahead)
    agg["hv5"] = agg["range_pct"].shift(1).rolling(5, min_periods=3).mean()
    return agg


# ---------------------------------------------------------------------------
# Statistical helpers
# ---------------------------------------------------------------------------

def _f1(tp: int, fp: int, fn: int) -> float:
    prec = tp / (tp + fp) if (tp + fp) > 0 else 0.0
    rec  = tp / (tp + fn) if (tp + fn) > 0 else 0.0
    if prec + rec == 0:
        return 0.0
    return 2 * prec * rec / (prec + rec)


def _best_threshold(vals: pd.Series, labels: pd.Series, direction: str = "low") -> tuple[float, float]:
    """
    Find threshold maximising F1 for classifying labels==1.
    direction='low' means predict 1 when val <= threshold (calm = low VIX/range).
    direction='high' means predict 1 when val >= threshold.
    Returns (best_threshold, best_f1).
    """
    vals, labels = vals.dropna(), labels.loc[vals.index]
    candidates = np.percentile(vals, np.arange(10, 91, 5))
    best_t, best_f1 = float("nan"), 0.0
    for t in candidates:
        if direction == "low":
            pred = (vals <= t).astype(int)
        else:
            pred = (vals >= t).astype(int)
        tp = int(((pred == 1) & (labels == 1)).sum())
        fp = int(((pred == 1) & (labels == 0)).sum())
        fn = int(((pred == 0) & (labels == 1)).sum())
        f = _f1(tp, fp, fn)
        if f > best_f1:
            best_f1, best_t = f, t
    return best_t, best_f1


def _filter_stats(df: pd.DataFrame, mask: pd.Series) -> dict:
    """PnL + trade stats for rows matching mask, using wing-6 net_pnl."""
    subset = df[mask]
    if subset.empty:
        return {"n": 0, "pnl_sum": 0.0, "pnl_mean": 0.0, "pnl_pos_rate": 0.0}
    return {
        "n":           len(subset),
        "pnl_sum":     float(subset["net_pnl"].sum()),
        "pnl_mean":    float(subset["net_pnl"].mean()),
        "pnl_pos_rate": float((subset["net_pnl"] > 0).mean()),
    }


# ---------------------------------------------------------------------------
# Per-symbol analysis
# ---------------------------------------------------------------------------

def analyse_symbol(symbol: str) -> dict:
    w6 = _load_ledger(6, symbol)
    w8 = _load_ledger(8, symbol)

    w6_keys = set(zip(w6["entry_date"], w6["expiry"]))
    w8_keys = set(zip(w8["entry_date"], w8["expiry"]))
    shared_keys  = w6_keys & w8_keys
    skipped_keys = w6_keys - w8_keys

    w6["key"]     = list(zip(w6["entry_date"], w6["expiry"]))
    w6["is_shared"] = w6["key"].isin(shared_keys).astype(int)

    # Enrich with spot features
    ohlc = _daily_ohlc(symbol)
    if not ohlc.empty:
        ohlc_map = {
            row["date"]: (row["range_pct"], row["range_prev"], row["hv5"])
            for _, row in ohlc.iterrows()
        }
        def _enrich(d):
            v = ohlc_map.get(d, (np.nan, np.nan, np.nan))
            return v
        w6[["range_today", "range_prev", "hv5"]] = pd.DataFrame(
            [_enrich(d) for d in w6["entry_date"]], index=w6.index
        )
    else:
        w6["range_today"] = np.nan
        w6["range_prev"]  = np.nan
        w6["hv5"]         = np.nan

    # Feature thresholds
    features = {
        "vix_entry":  ("low", "VIX at entry"),
        "range_prev": ("low", "Prior-day range%"),
        "hv5":        ("low", "5-day avg range%"),
        "dte_at_entry": ("any", "DTE at entry"),
    }
    threshold_results = {}
    for feat, (direction, label) in features.items():
        if feat not in w6.columns or w6[feat].isna().all():
            continue
        if direction == "any":
            # Try both directions
            t_lo, f_lo = _best_threshold(w6[feat], w6["is_shared"], "low")
            t_hi, f_hi = _best_threshold(w6[feat], w6["is_shared"], "high")
            if f_hi > f_lo:
                threshold_results[feat] = (t_hi, f_hi, "high", label)
            else:
                threshold_results[feat] = (t_lo, f_lo, "low",  label)
        else:
            t, f = _best_threshold(w6[feat], w6["is_shared"], direction)
            threshold_results[feat] = (t, f, direction, label)

    # Build candidate filters and test them
    # Filter candidates (using best thresholds found above, ±one step)
    filters_tested = {}

    def _apply_filter(df: pd.DataFrame, rules: dict) -> pd.Series:
        mask = pd.Series(True, index=df.index)
        for feat, (thr, direction) in rules.items():
            if feat not in df.columns:
                continue
            if direction == "low":
                mask &= (df[feat] <= thr)
            else:
                mask &= (df[feat] >= thr)
        return mask

    # Single-feature filters at best threshold
    for feat, (t, f1, direction, label) in threshold_results.items():
        if np.isnan(t):
            continue
        mask      = _apply_filter(w6, {feat: (t, direction)})
        stats     = _filter_stats(w6, mask)
        base_stats = _filter_stats(w6, pd.Series(True, index=w6.index))
        filters_tested[f"{label} {direction} {t:.2f}"] = {
            "rules":         {feat: (t, direction)},
            "capture_rate":  round(stats["n"] / base_stats["n"], 3) if base_stats["n"] else 0,
            "true_positive_rate": round(
                w6[mask & (w6["is_shared"] == 1)].shape[0] / max(w6["is_shared"].sum(), 1), 3
            ),
            "false_positive_rate": round(
                w6[mask & (w6["is_shared"] == 0)].shape[0] / max((w6["is_shared"] == 0).sum(), 1), 3
            ),
            "precision": round(
                w6[mask & (w6["is_shared"] == 1)].shape[0] / max(mask.sum(), 1), 3
            ),
            "f1": round(f1, 3),
            "pnl_sum":  stats["pnl_sum"],
            "pnl_mean": stats["pnl_mean"],
            "pnl_pos_rate": stats["pnl_pos_rate"],
            "n_trades": stats["n"],
        }

    # Combination: VIX + range_prev
    if "vix_entry" in threshold_results and "range_prev" in threshold_results:
        t_vix, _, d_vix, _ = threshold_results["vix_entry"]
        t_rng, _, d_rng, _ = threshold_results["range_prev"]
        if not (np.isnan(t_vix) or np.isnan(t_rng)):
            rules = {"vix_entry": (t_vix, d_vix), "range_prev": (t_rng, d_rng)}
            mask  = _apply_filter(w6, rules)
            stats = _filter_stats(w6, mask)
            filters_tested["VIX + prior-range (combined)"] = {
                "rules":         rules,
                "capture_rate":  round(mask.sum() / len(w6), 3),
                "true_positive_rate": round(
                    w6[mask & (w6["is_shared"] == 1)].shape[0] / max(w6["is_shared"].sum(), 1), 3
                ),
                "false_positive_rate": round(
                    w6[mask & (w6["is_shared"] == 0)].shape[0] / max((w6["is_shared"] == 0).sum(), 1), 3
                ),
                "precision": round(
                    w6[mask & (w6["is_shared"] == 1)].shape[0] / max(mask.sum(), 1), 3
                ),
                "f1": 0.0,  # not pre-computed
                "pnl_sum":  stats["pnl_sum"],
                "pnl_mean": stats["pnl_mean"],
                "pnl_pos_rate": stats["pnl_pos_rate"],
                "n_trades": stats["n"],
            }

    # Also VIX + hv5
    if "vix_entry" in threshold_results and "hv5" in threshold_results:
        t_vix, _, d_vix, _ = threshold_results["vix_entry"]
        t_hv5, _, d_hv5, _ = threshold_results["hv5"]
        if not (np.isnan(t_vix) or np.isnan(t_hv5)):
            rules = {"vix_entry": (t_vix, d_vix), "hv5": (t_hv5, d_hv5)}
            mask  = _apply_filter(w6, rules)
            stats = _filter_stats(w6, mask)
            filters_tested["VIX + hv5 (combined)"] = {
                "rules":         rules,
                "capture_rate":  round(mask.sum() / len(w6), 3),
                "true_positive_rate": round(
                    w6[mask & (w6["is_shared"] == 1)].shape[0] / max(w6["is_shared"].sum(), 1), 3
                ),
                "false_positive_rate": round(
                    w6[mask & (w6["is_shared"] == 0)].shape[0] / max((w6["is_shared"] == 0).sum(), 1), 3
                ),
                "precision": round(
                    w6[mask & (w6["is_shared"] == 1)].shape[0] / max(mask.sum(), 1), 3
                ),
                "f1": 0.0,
                "pnl_sum":  stats["pnl_sum"],
                "pnl_mean": stats["pnl_mean"],
                "pnl_pos_rate": stats["pnl_pos_rate"],
                "n_trades": stats["n"],
            }

    # Summary stats for shared vs skipped on each feature
    shared_rows  = w6[w6["is_shared"] == 1]
    skipped_rows = w6[w6["is_shared"] == 0]
    feat_summary = {}
    for feat in ("vix_entry", "range_today", "range_prev", "hv5", "dte_at_entry"):
        if feat not in w6.columns:
            continue
        feat_summary[feat] = {
            "shared_median":  round(shared_rows[feat].median(),  3),
            "skipped_median": round(skipped_rows[feat].median(), 3),
            "shared_p25":     round(shared_rows[feat].quantile(0.25),  3),
            "shared_p75":     round(shared_rows[feat].quantile(0.75),  3),
            "skipped_p25":    round(skipped_rows[feat].quantile(0.25), 3),
            "skipped_p75":    round(skipped_rows[feat].quantile(0.75), 3),
        }

    # Per-trade table for wing-8 actual trades (the "good" days)
    w8_detail = w8.copy()
    if not ohlc.empty:
        ohlc_map2 = {row["date"]: row for _, row in ohlc.iterrows()}
        def _row_features(d):
            r = ohlc_map2.get(d, {})
            return (
                r.get("range_pct", np.nan),
                r.get("range_prev", np.nan),
                r.get("hv5", np.nan),
            )
        w8_detail[["range_today", "range_prev", "hv5"]] = pd.DataFrame(
            [_row_features(d) for d in w8_detail["entry_date"]], index=w8_detail.index
        )

    return {
        "symbol":           symbol,
        "w6":               w6,
        "w8_detail":        w8_detail,
        "n_shared":         len(shared_keys),
        "n_skipped":        len(skipped_keys),
        "availability_pct": round(len(shared_keys) / len(w6_keys) * 100, 1) if w6_keys else 0,
        "feat_summary":     feat_summary,
        "threshold_results": threshold_results,
        "filters_tested":   filters_tested,
        "shared_rows":      shared_rows,
        "skipped_rows":     skipped_rows,
    }


# ---------------------------------------------------------------------------
# Report generation
# ---------------------------------------------------------------------------

def _pnl_fmt(v: float) -> str:
    return f"INR{v:+,.0f}"


def _feat_table(feat_summary: dict) -> str:
    header = ["| Feature | Shared median | Skipped median | Shared IQR | Skipped IQR | Delta |",
              "|---|---:|---:|---|---|---:|"]
    rows = []
    labels = {
        "vix_entry":    "VIX at entry",
        "range_today":  "Intraday range% (same day)",
        "range_prev":   "Prior-day range%",
        "hv5":          "5-day HV (rolling avg range%)",
        "dte_at_entry": "DTE at entry",
    }
    for feat, lbl in labels.items():
        if feat not in feat_summary:
            continue
        s = feat_summary[feat]
        delta = s["shared_median"] - s["skipped_median"]
        rows.append(
            f"| {lbl} | {s['shared_median']:.2f} | {s['skipped_median']:.2f} | "
            f"{s['shared_p25']:.2f}–{s['shared_p75']:.2f} | "
            f"{s['skipped_p25']:.2f}–{s['skipped_p75']:.2f} | "
            f"{delta:+.2f} |"
        )
    return "\n".join(header + rows)


def _threshold_table(threshold_results: dict) -> str:
    header = ["| Feature | Best threshold | Direction | F1 |",
              "|---|---:|---|---:|"]
    rows = []
    for feat, (t, f1, direction, label) in threshold_results.items():
        if np.isnan(t):
            continue
        rows.append(f"| {label} | {t:.2f} | {direction} | {f1:.3f} |")
    return "\n".join(header + rows)


def _filter_table(filters_tested: dict) -> str:
    header = [
        "| Filter | Capture% | TPR | FPR | Precision | W6 PnL/trade | W6 total PnL | Win rate |",
        "|---|---:|---:|---:|---:|---:|---:|---:|"
    ]
    rows = []
    for name, r in filters_tested.items():
        rows.append(
            f"| {name} | {r['capture_rate']*100:.0f}% "
            f"| {r['true_positive_rate']*100:.0f}% "
            f"| {r['false_positive_rate']*100:.0f}% "
            f"| {r['precision']*100:.0f}% "
            f"| {_pnl_fmt(r['pnl_mean'])} "
            f"| {_pnl_fmt(r['pnl_sum'])} "
            f"| {r['pnl_pos_rate']*100:.0f}% |"
        )
    return "\n".join(header + rows)


def _per_trade_sample(w8_detail: pd.DataFrame, n: int = 30) -> str:
    cols = ["entry_date", "expiry", "dte_at_entry", "vix_entry",
            "range_today", "range_prev", "hv5", "net_pnl"]
    avail = [c for c in cols if c in w8_detail.columns]
    df = w8_detail[avail].sort_values("entry_date").copy()
    # Show worst 15 and best 15 by net_pnl
    worst = df.nsmallest(n // 2, "net_pnl") if "net_pnl" in df.columns else df.head(n // 2)
    best  = df.nlargest(n // 2, "net_pnl")  if "net_pnl" in df.columns else df.tail(n // 2)
    combined = pd.concat([worst, best]).drop_duplicates().sort_values("net_pnl")
    header_row = "| " + " | ".join(avail) + " |"
    sep_row    = "|" + "|".join(["---" for _ in avail]) + "|"
    data_rows  = []
    for _, row in combined.iterrows():
        cells = []
        for c in avail:
            v = row[c]
            if c == "net_pnl":
                cells.append(f"{v:+,.0f}" if pd.notna(v) else "-")
            elif isinstance(v, float):
                cells.append(f"{v:.2f}" if pd.notna(v) else "-")
            else:
                cells.append(str(v))
        data_rows.append("| " + " | ".join(cells) + " |")
    return "\n".join([header_row, sep_row] + data_rows)


def _vix_range_crosstab(shared_rows: pd.DataFrame, skipped_rows: pd.DataFrame) -> str:
    """2D crosstab: VIX bucket × range_prev bucket."""
    if "range_prev" not in shared_rows.columns:
        return "(range data not available)"
    all_rows = pd.concat([
        shared_rows.assign(group="shared"),
        skipped_rows.assign(group="skipped"),
    ], ignore_index=True).dropna(subset=["vix_entry", "range_prev"])

    all_rows["vix_bin"]   = pd.cut(all_rows["vix_entry"],
                                    bins=[0, 13, 17, 22, 100],
                                    labels=["<13", "13-17", "17-22", "22+"])
    all_rows["range_bin"] = pd.cut(all_rows["range_prev"],
                                    bins=[0, 0.6, 0.9, 1.3, 100],
                                    labels=["<0.6%", "0.6-0.9%", "0.9-1.3%", ">1.3%"])

    tbl = all_rows.groupby(["vix_bin", "range_bin", "group"]).size().unstack("group", fill_value=0)
    if "shared" not in tbl.columns:  tbl["shared"]  = 0
    if "skipped" not in tbl.columns: tbl["skipped"] = 0
    tbl["total"]      = tbl["shared"] + tbl["skipped"]
    tbl["avail_pct"]  = (tbl["shared"] / tbl["total"] * 100).round(1)
    tbl = tbl.reset_index()

    lines = ["| VIX | Prev-day range | Shared | Skipped | Avail% |",
             "|---|---|---:|---:|---:|"]
    for _, r in tbl.iterrows():
        lines.append(f"| {r['vix_bin']} | {r['range_bin']} | {r['shared']} | {r['skipped']} | {r['avail_pct']}% |")
    return "\n".join(lines)


def _pnl_by_filter_bucket(w6: pd.DataFrame) -> str:
    """Show W6 avg PnL in each (VIX, range_prev) bucket."""
    if "range_prev" not in w6.columns or w6["range_prev"].isna().all():
        return "(range data not available)"
    df = w6.dropna(subset=["vix_entry", "range_prev"]).copy()
    df["vix_bin"]   = pd.cut(df["vix_entry"],
                              bins=[0, 13, 17, 22, 100],
                              labels=["<13", "13-17", "17-22", "22+"])
    df["range_bin"] = pd.cut(df["range_prev"],
                              bins=[0, 0.6, 0.9, 1.3, 100],
                              labels=["<0.6%", "0.6-0.9%", "0.9-1.3%", ">1.3%"])
    tbl = df.groupby(["vix_bin", "range_bin"]).agg(
        n=("net_pnl", "count"),
        pnl_mean=("net_pnl", "mean"),
        pnl_sum=("net_pnl", "sum"),
        win_rate=("net_pnl", lambda x: (x > 0).mean()),
    ).reset_index()
    lines = ["| VIX | Prev-day range | N trades | Avg PnL | Total PnL | Win% |",
             "|---|---|---:|---:|---:|---:|"]
    for _, r in tbl.iterrows():
        lines.append(
            f"| {r['vix_bin']} | {r['range_bin']} | {r['n']} "
            f"| {_pnl_fmt(r['pnl_mean'])} | {_pnl_fmt(r['pnl_sum'])} | {r['win_rate']*100:.0f}% |"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    results = {}
    for sym in INDICES:
        print(f"Analysing {sym}...")
        try:
            results[sym] = analyse_symbol(sym)
        except FileNotFoundError as e:
            print(f"  SKIP — {e}")

    lines: list[str] = [
        "# Wing-8 Filter Investigation",
        "",
        "**Question**: the wing-8 IC only entered on ~18–41% of available days because",
        "ATM±10 strike data was absent on the rest. Those traded days had significantly",
        "lower VIX and intraday range. Can we turn this accidental filter into a",
        "deliberate, pre-market observable filter?",
        "",
        "**Key finding from audit**: skipped days were *worse* for ICs across all 4 indices",
        "(lower W6 PnL, higher range, higher VIX). So the data gap was inadvertently",
        "selecting the *right* days to trade.",
        "",
        "**Filter candidates tested** (all pre-market or at-entry observable):",
        "- `vix_entry`: VIX at 9:20 AM on entry day",
        "- `range_prev`: prior trading day intraday range% (fully pre-market)",
        "- `hv5`: 5-day rolling avg of daily range% over prior 5 sessions",
        "- `dte_at_entry`: days to expiry",
        "",
        "**Terminology**:",
        "- TPR (True Positive Rate): % of actual wing-8 trade days captured by the filter",
        "- FPR (False Positive Rate): % of skipped (bad) days the filter lets through",
        "- Precision: of days the filter passes, % that are genuine wing-8 shared days",
        "- W6 PnL shown (wing-6 is the fill-proxy for would-be wing-8 trades on those days)",
        "",
    ]

    for sym, r in results.items():
        lines += [f"---", f"## {sym}", ""]

        # Feature summary table
        lines += [
            "### Feature Distributions: Shared vs Skipped",
            "",
            f"n_shared={r['n_shared']}, n_skipped={r['n_skipped']}, "
            f"availability={r['availability_pct']}%",
            "",
            _feat_table(r["feat_summary"]),
            "",
        ]

        # Best threshold per feature
        lines += [
            "### Best Single-Feature Thresholds (F1 for shared=1)",
            "",
            _threshold_table(r["threshold_results"]),
            "",
            "> F1 measures how well each single threshold separates traded days from skipped days.",
            "> Perfect = 1.0. Random = ~0.5 at equal class size.",
            "",
        ]

        # 2D VIX × range crosstab
        lines += [
            "### Availability by (VIX, Prior-day Range) Cell",
            "",
            "(Shows what % of entries in each regime cell were shared — the 'calm quadrant')",
            "",
            _vix_range_crosstab(r["shared_rows"], r["skipped_rows"]),
            "",
        ]

        # W6 PnL by regime bucket
        lines += [
            "### W6 PnL by (VIX, Prior-day Range) Regime Cell",
            "",
            "(Wing-6 PnL as proxy — shows which regime cells are actually profitable for ICs)",
            "",
            _pnl_by_filter_bucket(r["w6"]),
            "",
        ]

        # Filter comparison table
        lines += [
            "### Filter Comparison",
            "",
            "(All filters applied to wing-6 universe — shows what W6 would earn if we applied the filter)",
            "",
            _filter_table(r["filters_tested"]),
            "",
        ]

        # Per-trade sample for wing-8 actual trades
        lines += [
            "### Actual Wing-8 Trades — Best/Worst 15 with Market Conditions",
            "",
            _per_trade_sample(r["w8_detail"], n=30),
            "",
        ]

    # Synthesise across symbols
    lines += [
        "---",
        "## Cross-Symbol Synthesis",
        "",
        "### Summary of best thresholds found",
        "",
        "| Symbol | Best VIX threshold | Best range_prev threshold | Best hv5 threshold |",
        "|---|---:|---:|---:|",
    ]
    for sym, r in results.items():
        tr = r["threshold_results"]
        vix_t  = f"{tr['vix_entry'][0]:.1f}"   if "vix_entry"  in tr and not np.isnan(tr["vix_entry"][0])  else "-"
        rng_t  = f"{tr['range_prev'][0]:.2f}%"  if "range_prev" in tr and not np.isnan(tr["range_prev"][0]) else "-"
        hv5_t  = f"{tr['hv5'][0]:.2f}%"         if "hv5"        in tr and not np.isnan(tr["hv5"][0])        else "-"
        lines.append(f"| {sym} | ≤ {vix_t} | ≤ {rng_t} | ≤ {hv5_t} |")

    lines += [
        "",
        "### Practical Filter Recommendation",
        "",
        "If thresholds are consistent across symbols, a single rule set can serve all 4 indices.",
        "The combined (VIX + prior-range) filter precision column shows: of days we'd enter",
        "on, what fraction coincides with the days wing-8 naturally selected (the good days).",
        "",
        "**Critical caveats**:",
        "1. These thresholds are fit on in-sample data (2021–2026). They MUST be validated",
        "   on a held-out period before deployment.",
        "2. The filter's value may already be captured by wing-8's natural selection — running",
        "   wing-8 with explicit data check is equivalent. A filter adds value only if you",
        "   want to trade wing-6/wing-4 but only on the high-quality subset of days.",
        "3. The regime cells with highest W6 PnL (low VIX, low prior-range) represent the",
        "   'calm sell' environment where short-vol is most reliable. This is not news — but",
        "   the specific threshold values are now data-backed for this strategy and dataset.",
        "4. Prior-day range is likely more useful than same-day VIX because it is fully",
        "   pre-market and not subject to gap-open VIX spikes that normalise by 9:20.",
    ]

    report = "\n".join(lines)
    out_path = OUT_DIR / f"{STAMP}_wing8_filter_investigation.md"
    out_path.write_text(report, encoding="utf-8")
    print(f"\nReport -> {out_path}")

    # Quick console summary
    print("\n--- FILTER SUMMARY ---")
    for sym, r in results.items():
        tr = r["threshold_results"]
        vix_t = tr.get("vix_entry",  (float("nan"),))[0]
        rng_t = tr.get("range_prev", (float("nan"),))[0]
        ft = r["filters_tested"]
        combo = ft.get("VIX + prior-range (combined)", {})
        print(
            f"{sym:12s}: VIX<={vix_t:.1f}, range_prev<={rng_t:.2f}%  "
            f"capture={combo.get('capture_rate',0)*100:.0f}%, "
            f"precision={combo.get('precision',0)*100:.0f}%, "
            f"W6_pnl_mean={_pnl_fmt(combo.get('pnl_mean',0))}, "
            f"W6_total={_pnl_fmt(combo.get('pnl_sum',0))}"
        )


if __name__ == "__main__":
    main()

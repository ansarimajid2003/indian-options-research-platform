"""
Wing-8 IC data-availability audit.

Tests whether the days skipped by wing-8 (long at ATM±10, the data boundary)
cluster in high-VIX / high-move regimes — which would mean the IC is only
being backtested on calm days, inflating Sharpe and suppressing MaxDD.

Methodology
-----------
Wing-6 (long at ±8) is used as the universe denominator: if it entered on a
given (entry_date, expiry) pair, the opportunity existed.  Wing-8 entered a
subset of those.  "Skipped" = wing-6 entered, wing-8 did not.

Four bias tests:
  1. VIX bucket distribution — are skipped days systematically low-VIX (calm)?
  2. Wing-6 avg PnL on shared vs skipped — do skipped days turn into winners
     that wing-8 misses, or losers it dodges?
  3. Realized intraday range — are skipped days calm (low H-L range)?
  4. Year distribution — is data coverage improving over time?

Statistical tests: Mann-Whitney U (non-parametric, no normality assumption).
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STAMP = "20260506_mixed_expiry"
LEDGER_DIR = ROOT / "reports/backtests/options/risk_management"
SPOT_PATHS = {
    "NIFTY":      ROOT / "data/processed/spot/nifty50_1min_CANONICAL.csv",
    "BANKNIFTY":  ROOT / "data/processed/spot/banknifty_1min_DHAN.csv",
    "FINNIFTY":   ROOT / "data/processed/spot/finnifty_1min_DHAN.csv",
    "MIDCPNIFTY": ROOT / "data/processed/spot/midcpnifty_1min_DHAN.csv",
}
VIX_PATH = ROOT / "data/processed/spot/indiavix_1min_DHAN.csv"
INDICES = ("NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY")
VIX_BUCKET_ORDER = ["<10", "10-13", "13-17", "17-22", "22-30", ">30"]
OUT_DIR = ROOT / "reports/backtests/options/risk_management"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_ledger(stamp: str, wing: int, symbol: str) -> pd.DataFrame:
    path = LEDGER_DIR / f"{stamp}_ic_wing{wing}_{symbol.lower()}.csv"
    df = pd.read_csv(path)
    if "strategy" in df.columns:
        df = df[df["strategy"] != "TOTAL"].copy()
    df["entry_date"] = pd.to_datetime(df["entry_date"], errors="coerce").dt.date
    df["expiry"]     = pd.to_datetime(df["expiry"],     errors="coerce").dt.date
    df["net_pnl"]    = pd.to_numeric(df["net_pnl"],    errors="coerce")
    df["spot_entry"] = pd.to_numeric(df["spot_entry"], errors="coerce")
    df["vix_entry"]  = pd.to_numeric(df["vix_entry"],  errors="coerce")
    return df.reset_index(drop=True)


def _daily_ohlc(symbol: str) -> pd.DataFrame:
    """Load spot 1-min CSV and aggregate to daily OHLC."""
    path = SPOT_PATHS[symbol]
    if not path.exists():
        return pd.DataFrame()
    raw = pd.read_csv(path, usecols=lambda c: c in ("datetime", "timestamp", "date", "open", "high", "low", "close"))
    # Normalise timestamp column name
    ts_col = next((c for c in ("datetime", "timestamp", "date") if c in raw.columns), None)
    if ts_col is None:
        return pd.DataFrame()
    raw[ts_col] = pd.to_datetime(raw[ts_col], errors="coerce")
    raw["date"] = raw[ts_col].dt.date
    agg = raw.groupby("date").agg(
        day_open=("open", "first"),
        day_high=("high", "max"),
        day_low=("low",  "min"),
        day_close=("close", "last"),
    ).reset_index()
    agg["intraday_range_pct"] = (agg["day_high"] - agg["day_low"]) / agg["day_open"] * 100
    return agg


def _mannwhitney_p(x: np.ndarray, y: np.ndarray) -> float:
    """Two-sided Mann-Whitney U p-value without scipy (normal approximation, valid for n>20)."""
    nx, ny = len(x), len(y)
    if nx == 0 or ny == 0:
        return float("nan")
    combined = np.concatenate([x, y])
    ranks = np.argsort(np.argsort(combined)) + 1.0  # 1-based ranks
    rank_sum_x = ranks[:nx].sum()
    u = rank_sum_x - nx * (nx + 1) / 2.0
    mu_u = nx * ny / 2.0
    sigma_u = np.sqrt(nx * ny * (nx + ny + 1) / 12.0)
    if sigma_u == 0:
        return 1.0
    z = (u - mu_u) / sigma_u
    # Two-sided p from normal CDF approximation
    p = 2 * (1 - _norm_cdf(abs(z)))
    return float(p)


def _norm_cdf(z: float) -> float:
    """Standard normal CDF via error function."""
    import math
    return (1.0 + math.erf(z / math.sqrt(2))) / 2.0


def _mw_summary(a: pd.Series, b: pd.Series, label_a: str, label_b: str) -> str:
    a, b = a.dropna(), b.dropna()
    if len(a) < 3 or len(b) < 3:
        return "  (insufficient data)"
    p = _mannwhitney_p(a.to_numpy(dtype=float), b.to_numpy(dtype=float))
    direction = "higher" if a.median() > b.median() else "lower"
    return (
        f"  {label_a} median={a.median():.2f}, n={len(a)} | "
        f"{label_b} median={b.median():.2f}, n={len(b)} | "
        f"{label_a} is {direction} | MW p={p:.4f}"
    )


# ---------------------------------------------------------------------------
# Per-symbol audit
# ---------------------------------------------------------------------------

def audit_symbol(symbol: str) -> dict:
    w6 = _load_ledger(STAMP, 6, symbol)
    w8 = _load_ledger(STAMP, 8, symbol)

    w6_keys = set(zip(w6["entry_date"], w6["expiry"]))
    w8_keys = set(zip(w8["entry_date"], w8["expiry"]))
    shared_keys  = w6_keys & w8_keys
    skipped_keys = w6_keys - w8_keys

    availability_rate = len(shared_keys) / len(w6_keys) if w6_keys else 0.0

    # Classify wing-6 rows
    w6["key"] = list(zip(w6["entry_date"], w6["expiry"]))
    w6_shared  = w6[w6["key"].isin(shared_keys)].copy()
    w6_skipped = w6[w6["key"].isin(skipped_keys)].copy()

    # Daily spot range
    ohlc = _daily_ohlc(symbol)
    if not ohlc.empty:
        ohlc_map = dict(zip(ohlc["date"], ohlc["intraday_range_pct"]))
        w6["range_pct"] = w6["entry_date"].map(ohlc_map)
        w6_shared  = w6[w6["key"].isin(shared_keys)].copy()
        w6_skipped = w6[w6["key"].isin(skipped_keys)].copy()
    else:
        w6["range_pct"] = np.nan

    return {
        "symbol":            symbol,
        "w6_trades":         len(w6),
        "w8_trades":         len(w8),
        "shared":            len(shared_keys),
        "skipped":           len(skipped_keys),
        "availability_pct":  round(availability_rate * 100, 1),
        # PnL counterfactual
        "w6_pnl_shared_mean":  round(w6_shared["net_pnl"].mean(),  2) if not w6_shared.empty  else None,
        "w6_pnl_skipped_mean": round(w6_skipped["net_pnl"].mean(), 2) if not w6_skipped.empty else None,
        "w6_pnl_shared_sum":   round(w6_shared["net_pnl"].sum(),   2) if not w6_shared.empty  else None,
        "w6_pnl_skipped_sum":  round(w6_skipped["net_pnl"].sum(),  2) if not w6_skipped.empty else None,
        # VIX
        "vix_shared_median":   round(w6_shared["vix_entry"].median(),  2) if not w6_shared.empty  else None,
        "vix_skipped_median":  round(w6_skipped["vix_entry"].median(), 2) if not w6_skipped.empty else None,
        # Range
        "range_shared_median":  round(w6_shared["range_pct"].median(),  3) if not w6_shared.empty  else None,
        "range_skipped_median": round(w6_skipped["range_pct"].median(), 3) if not w6_skipped.empty else None,
        # Raw series for statistical tests
        "_w6_shared":  w6_shared,
        "_w6_skipped": w6_skipped,
        "_w6":         w6,
        "_w8":         w8,
    }


def _vix_bucket_table(shared: pd.DataFrame, skipped: pd.DataFrame) -> str:
    def _counts(df: pd.DataFrame) -> dict:
        if df.empty or "vix_bucket" not in df.columns:
            return {}
        return df["vix_bucket"].value_counts().to_dict()
    s_cnt = _counts(shared)
    k_cnt = _counts(skipped)
    all_buckets = sorted(set(list(s_cnt.keys()) + list(k_cnt.keys())),
                         key=lambda b: VIX_BUCKET_ORDER.index(b) if b in VIX_BUCKET_ORDER else 99)
    lines = ["| VIX bucket | Shared (traded) | Skipped | Skipped% |",
             "|---|---:|---:|---:|"]
    for b in all_buckets:
        s = s_cnt.get(b, 0)
        k = k_cnt.get(b, 0)
        tot = s + k
        pct = f"{k/tot*100:.0f}%" if tot > 0 else "-"
        lines.append(f"| {b} | {s} | {k} | {pct} |")
    return "\n".join(lines)


def _year_table(shared: pd.DataFrame, skipped: pd.DataFrame) -> str:
    def _year_counts(df: pd.DataFrame) -> pd.Series:
        if df.empty:
            return pd.Series(dtype=int)
        return pd.to_datetime(df["entry_date"]).dt.year.value_counts().sort_index()
    s_yr = _year_counts(shared)
    k_yr = _year_counts(skipped)
    all_years = sorted(set(list(s_yr.index) + list(k_yr.index)))
    lines = ["| Year | Shared | Skipped | Availability% |",
             "|---|---:|---:|---:|"]
    for y in all_years:
        s = s_yr.get(y, 0)
        k = k_yr.get(y, 0)
        tot = s + k
        avail = f"{s/tot*100:.0f}%" if tot > 0 else "-"
        lines.append(f"| {y} | {s} | {k} | {avail} |")
    return "\n".join(lines)


def _dte_table(shared: pd.DataFrame, skipped: pd.DataFrame) -> str:
    """Availability by DTE-at-entry bucket."""
    def _bucket(dte: float) -> str:
        if pd.isna(dte): return "unknown"
        if dte <= 2:  return "0-2"
        if dte <= 5:  return "3-5"
        if dte <= 10: return "6-10"
        if dte <= 20: return "11-20"
        return "21+"
    all_df = pd.concat([
        shared.assign(_group="shared"),
        skipped.assign(_group="skipped"),
    ], ignore_index=True)
    if all_df.empty or "dte_at_entry" not in all_df.columns:
        return "(no DTE data)"
    all_df["dte_bucket"] = all_df["dte_at_entry"].apply(_bucket)
    tbl = all_df.groupby(["dte_bucket", "_group"]).size().unstack(fill_value=0)
    if "skipped" not in tbl.columns:
        tbl["skipped"] = 0
    if "shared" not in tbl.columns:
        tbl["shared"] = 0
    tbl["avail_pct"] = (tbl["shared"] / (tbl["shared"] + tbl["skipped"]) * 100).round(1)
    lines = ["| DTE at entry | Shared | Skipped | Availability% |",
             "|---|---:|---:|---:|"]
    for dte_b in ["0-2", "3-5", "6-10", "11-20", "21+"]:
        if dte_b in tbl.index:
            row = tbl.loc[dte_b]
            lines.append(f"| {dte_b} | {row.get('shared',0):.0f} | {row.get('skipped',0):.0f} | {row.get('avail_pct',0):.1f}% |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    results = {}
    for symbol in INDICES:
        print(f"Auditing {symbol}...")
        try:
            results[symbol] = audit_symbol(symbol)
        except FileNotFoundError as e:
            print(f"  SKIP: {e}")

    lines: list[str] = [
        "# Wing-8 IC Data Availability Audit",
        "",
        f"Source: `{STAMP}` IC ledgers (wing-6 and wing-8).",
        "",
        "**Bias question**: is wing-8's high Sharpe (4.198) driven by real edge, or by",
        "only entering on calm days where ATM±10 data is available (skipping the volatile",
        "days that would hit the IC)?",
        "",
        "**Test**: compare days wing-8 skipped (±10 data missing) vs days it entered, using",
        "wing-6 as the universe denominator.  Wing-6 (long at ±8) fills in almost all",
        "cases; its entry dates are the full opportunity set.",
        "",
        "## Availability Summary",
        "",
        "| Symbol | Expiry type | W6 trades | W8 trades | Shared | Skipped | Availability% |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    expiry_label = {"NIFTY": "weekly", "BANKNIFTY": "monthly", "FINNIFTY": "monthly", "MIDCPNIFTY": "monthly"}
    for sym, r in results.items():
        lines.append(
            f"| {sym} | {expiry_label[sym]} "
            f"| {r['w6_trades']} | {r['w8_trades']} "
            f"| {r['shared']} | {r['skipped']} "
            f"| {r['availability_pct']}% |"
        )

    lines += ["", "## PnL Counterfactual", "",
              "Wing-6 PnL split by whether wing-8 also traded that day.",
              "If skipped days are the **losing** days → wing-8 is dodging bad trades (no bias, or anti-bias).",
              "If skipped days are the **winning** days → wing-8 is cherry-picking good trades (upward bias).",
              "",
              "| Symbol | W6 avg PnL (shared) | W6 avg PnL (skipped) | W6 total PnL (shared) | W6 total PnL (skipped) | Verdict |",
              "|---|---:|---:|---:|---:|---|"]
    for sym, r in results.items():
        s_mean = r["w6_pnl_shared_mean"]
        k_mean = r["w6_pnl_skipped_mean"]
        s_sum  = r["w6_pnl_shared_sum"]
        k_sum  = r["w6_pnl_skipped_sum"]
        if s_mean is None or k_mean is None:
            verdict = "insufficient data"
        elif k_mean < 0 and s_mean > 0:
            verdict = "skipped=losers → NO BIAS (wing-8 missing bad days)"
        elif k_mean > s_mean * 1.2:
            verdict = "skipped=winners → UPWARD BIAS (cherry-picking)"
        elif abs(k_mean - s_mean) / max(abs(s_mean), 1) < 0.3:
            verdict = "similar PnL → RANDOM SKIP"
        else:
            verdict = f"skipped {'better' if k_mean > s_mean else 'worse'} → mild {'bias' if k_mean > s_mean else 'anti-bias'}"
        def _fmt(v): return f"INR{v:+,.0f}" if v is not None else "-"
        lines.append(f"| {sym} | {_fmt(s_mean)} | {_fmt(k_mean)} | {_fmt(s_sum)} | {_fmt(k_sum)} | {verdict} |")

    lines += ["", "## VIX Distribution: Shared vs Skipped", "",
              "If skipped days cluster in **low-VIX** buckets, wing-8 is missing calm days (fine — less premium anyway).",
              "If they cluster in **high-VIX**, wing-8 is missing the high-IV profitable entries (anti-bias — result understated).",
              "If they cluster in **high-VIX and high-move**, wing-8 is missing the dangerous days (upward bias).", ""]

    for sym, r in results.items():
        shared  = r["_w6_shared"]
        skipped = r["_w6_skipped"]
        lines.append(f"### {sym}")
        lines.append("")
        lines.append(_vix_bucket_table(shared, skipped))
        lines.append("")
        vix_stat = _mw_summary(shared["vix_entry"], skipped["vix_entry"], "shared", "skipped")
        lines.append(f"VIX MW test: {vix_stat}")
        lines.append("")

    lines += ["## Intraday Range: Shared vs Skipped", "",
              "Daily range = (day_high − day_low) / day_open × 100.",
              "High range → volatile day.  If skipped days have lower range → wing-8 entering on move days → potential loss bias.", ""]
    for sym, r in results.items():
        shared  = r["_w6_shared"]
        skipped = r["_w6_skipped"]
        if "range_pct" not in shared.columns or shared["range_pct"].isna().all():
            lines.append(f"### {sym}\n(spot data not available)\n")
            continue
        lines.append(f"### {sym}")
        lines.append("")
        range_stat = _mw_summary(shared["range_pct"], skipped["range_pct"], "shared", "skipped")
        lines.append(f"Range MW test: {range_stat}")
        lines.append(f"  Shared  range quartiles: {shared['range_pct'].quantile([0.25,0.5,0.75]).round(3).to_dict()}")
        lines.append(f"  Skipped range quartiles: {skipped['range_pct'].quantile([0.25,0.5,0.75]).round(3).to_dict()}")
        lines.append("")

    lines += ["## Availability by Year", ""]
    for sym, r in results.items():
        lines.append(f"### {sym}")
        lines.append("")
        lines.append(_year_table(r["_w6_shared"], r["_w6_skipped"]))
        lines.append("")

    lines += ["## Availability by DTE at Entry", "",
              "Key question: does wing-8 drop out at specific DTE ranges?",
              "Monthly expiries (BN/FN/MCP) enter at DTE 15-30; ±10 data should be",
              "available if Dhan loads full chain for all DTE — or only near expiry.", ""]
    for sym, r in results.items():
        lines.append(f"### {sym}")
        lines.append("")
        lines.append(_dte_table(r["_w6_shared"], r["_w6_skipped"]))
        lines.append("")

    lines += ["## Verdict", ""]
    for sym, r in results.items():
        shared  = r["_w6_shared"]
        skipped = r["_w6_skipped"]
        avail   = r["availability_pct"]
        s_mean  = r["w6_pnl_shared_mean"] or 0
        k_mean  = r["w6_pnl_skipped_mean"] or 0
        vix_shared  = r["vix_shared_median"]  or 0
        vix_skipped = r["vix_skipped_median"] or 0

        bias_indicators = 0
        if avail < 60:
            bias_indicators += 1
        if k_mean > s_mean * 1.1:
            bias_indicators += 1
        if (vix_shared or 0) > (vix_skipped or 0) * 1.1:
            bias_indicators += 1

        if bias_indicators == 0:
            verdict_str = "LOW BIAS RISK — availability is adequate and skipped days are not better days"
        elif bias_indicators == 1:
            verdict_str = "MODERATE CAUTION — one bias indicator triggered; result plausible but needs live validation"
        else:
            verdict_str = "HIGH BIAS RISK — multiple indicators suggest result is inflated by data selection"

        lines.append(f"**{sym}**: availability {avail}%, skipped W6 avg INR{k_mean:+,.0f} vs shared INR{s_mean:+,.0f} → **{verdict_str}**")
        lines.append("")

    report_text = "\n".join(lines)
    out_path = OUT_DIR / f"{STAMP}_wing8_audit.md"
    out_path.write_text(report_text, encoding="utf-8")
    print(f"\nAudit report -> {out_path}")
    print("\n--- QUICK SUMMARY ---")
    for sym, r in results.items():
        print(
            f"{sym:12s}: avail={r['availability_pct']}%  "
            f"W6_shared_avg=INR{(r['w6_pnl_shared_mean'] or 0):+,.0f}  "
            f"W6_skipped_avg=INR{(r['w6_pnl_skipped_mean'] or 0):+,.0f}  "
            f"VIX_shared={r['vix_shared_median']}  VIX_skipped={r['vix_skipped_median']}"
        )


if __name__ == "__main__":
    main()

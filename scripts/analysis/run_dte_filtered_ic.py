"""
DTE-Filtered Iron Condor Experiment
=====================================
Follows from the wing-8 filter investigation (20260506_wing8_filter_investigation.md).

Key findings from that investigation:
  - FINNIFTY / MIDCPNIFTY: DTE ≤ 7 at entry is the strongest single filter
    (F1 > VIX or range filters; W6 PnL/trade jumps from ~+97 to +270 for FINNIFTY)
  - NIFTY: VIX floor (VIX ≥ 13) is the correct filter; VIX <13 cells produced
    flat/negative PnL across all regime cells
  - BANKNIFTY: uniformly negative on W6 across all cells; excluded from this run

This script runs targeted backtests:
  - FINNIFTY  IC wing-{4,6,8}  with max_dte=7, min_dte=1
  - MIDCPNIFTY IC wing-{4,6,8} with max_dte=7, min_dte=1
  - NIFTY     IC wing-{6,8}    with vix_min=13 (VIX floor)
  - NIFTY     IC wing-{6,8}    with max_dte=7  (weekly, so mostly no-op — included for reference)

Each is compared against the unrestricted baseline from 20260506_mixed_expiry.
IS = 2022–2024, OOS = 2025–2026.

Writes per-symbol CSV ledgers + a markdown comparison report.
"""
from __future__ import annotations

import math
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.dhan_loader import DhanBacktestEngine, load_dhan_data
from options_backtest.reports import daily_pnl, equity_curve, summary
from options_backtest.schemas import BacktestConfig
from options_backtest.strategy import IronCondor

BASELINE_STAMP = "20260506_mixed_expiry"
OUT_DIR        = ROOT / "reports/backtests/options/risk_management"
DHAN_ROOT      = ROOT / "data/processed/options/dhan"
VIX_PATH       = ROOT / "data/processed/spot/indiavix_1min_DHAN.csv"
FROM_DATE      = "2022-02-01"
TO_DATE        = "2026-04-30"
IS_END         = pd.Timestamp("2024-12-31").date()
OOS_START      = pd.Timestamp("2025-01-01").date()


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


def _stats(ledger: pd.DataFrame) -> dict:
    if ledger.empty:
        return {}
    dpnl  = daily_pnl(ledger)
    curve = equity_curve(dpnl)
    return summary(ledger, curve, dpnl)


def _split_stats(ledger: pd.DataFrame) -> tuple[dict, dict]:
    if ledger.empty:
        return {}, {}
    dates = pd.to_datetime(ledger["exit_date"]).dt.date
    return (
        _stats(ledger[dates <= IS_END].copy()),
        _stats(ledger[dates >= OOS_START].copy()),
    )


def _load_baseline(symbol: str, wing: int) -> pd.DataFrame:
    path = OUT_DIR / f"{BASELINE_STAMP}_ic_wing{wing}_{symbol.lower()}.csv"
    if not path.exists():
        return pd.DataFrame()
    df = pd.read_csv(path)
    if "strategy" in df.columns:
        df = df[df["strategy"] != "TOTAL"].copy()
    for col in ("gross_pnl", "charges", "net_pnl", "dte_at_entry", "vix_entry"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("entry_date", "exit_date", "expiry"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    return df.reset_index(drop=True)


def _run_ic(
    symbol: str,
    wing: int,
    max_dte: int | None,
    vix_min: float | None,
    expiry_type: str,
    stamp: str,
    label: str,
) -> pd.DataFrame:
    long_offset = 2 + wing
    tag_parts = [label.lower().replace(" ", "_").replace("≤", "le").replace("≥", "ge")]
    config = BacktestConfig(
        symbol=symbol,
        stop_loss_pct=None,
        target_profit_pct=None,
        min_dte=1,
        max_dte=max_dte,
        vix_path=str(VIX_PATH) if VIX_PATH.exists() else None,
        vix_min=vix_min,
        vix_missing_policy="skip",
    )
    data   = load_dhan_data(DHAN_ROOT, expiry_type, symbol)
    engine = DhanBacktestEngine(config, dhan_root=str(DHAN_ROOT), expiry_type=expiry_type)
    result = engine.run(
        IronCondor(
            short_call_offset=2,
            long_call_offset=long_offset,
            short_put_offset=-2,
            long_put_offset=-long_offset,
        ),
        from_date=FROM_DATE,
        to_date=TO_DATE,
        data=data,
    )
    ledger = result.trade_ledger.copy()
    ledger.insert(0, "symbol", symbol)

    # Sanitize label for Windows filesystem (remove/replace special chars)
    safe_label = (label
                  .lower()
                  .replace("≤", "le")
                  .replace("≥", "ge")
                  .replace("<", "lt")
                  .replace(">", "gt")
                  .replace(" ", "_")
                  .replace("=", ""))
    out_path = OUT_DIR / f"{stamp}_{safe_label}_ic_wing{wing}_{symbol.lower()}.csv"
    ledger.to_csv(out_path, index=False)

    s = result.summary
    print(
        f"  {symbol:12s} wing-{wing} {label:30s}: "
        f"trades={s.get('trades', 0):>4}  "
        f"net={_fmt_pnl(s.get('net_pnl')):>12}  "
        f"sharpe={_fmt(s.get('sharpe')):>7}  "
        f"t-stat={_fmt(s.get('sharpe_tstat')):>7}"
    )
    return ledger


# ---------------------------------------------------------------------------
# Report helpers
# ---------------------------------------------------------------------------

def _comparison_row(label: str, ledger: pd.DataFrame) -> dict:
    s = _stats(ledger)
    is_s, oos_s = _split_stats(ledger)
    return {
        "label":       label,
        "trades":      s.get("trades", 0),
        "net_pnl":     s.get("net_pnl"),
        "cagr":        s.get("cagr"),
        "sharpe":      s.get("sharpe"),
        "t_stat":      s.get("sharpe_tstat"),
        "sortino":     s.get("sortino"),
        "calmar":      s.get("calmar"),
        "max_dd_pct":  s.get("max_drawdown_pct"),
        "pf":          s.get("profit_factor"),
        "win_rate":    s.get("win_rate"),
        "is_net":      is_s.get("net_pnl"),
        "is_sharpe":   is_s.get("sharpe"),
        "oos_net":     oos_s.get("net_pnl"),
        "oos_sharpe":  oos_s.get("sharpe"),
    }


def _table_header() -> list[str]:
    return [
        "| Strategy | Trades | Net PnL | CAGR | Sharpe | t-stat | Sortino | Calmar | MaxDD% | PF | Win% | IS PnL | IS Sh | OOS PnL | OOS Sh |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
    ]


def _table_row(r: dict) -> str:
    cagr = _fmt(r["cagr"], ".1%") if r.get("cagr") is not None else "-"
    mdd  = _fmt(r["max_dd_pct"], ".1%") if r.get("max_dd_pct") is not None else "-"
    wr   = _fmt(r["win_rate"], ".0%") if r.get("win_rate") is not None else "-"
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
        f"| {_fmt_pnl(r['is_net'])} "
        f"| {_fmt(r['is_sharpe'])} "
        f"| {_fmt_pnl(r['oos_net'])} "
        f"| {_fmt(r['oos_sharpe'])} |"
    )


def _dte_breakdown(ledger: pd.DataFrame) -> str:
    if ledger.empty or "dte_at_entry" not in ledger.columns:
        return "(no DTE data)"

    def _bucket(d: float) -> str:
        if pd.isna(d): return "unknown"
        if d <= 2:  return "0-2"
        if d <= 5:  return "3-5"
        if d <= 10: return "6-10"
        if d <= 20: return "11-20"
        return "21+"

    ledger = ledger.copy()
    ledger["dte_bucket"] = ledger["dte_at_entry"].apply(_bucket)
    tbl = ledger.groupby("dte_bucket").agg(
        n=("net_pnl", "count"),
        pnl_mean=("net_pnl", "mean"),
        pnl_sum=("net_pnl", "sum"),
        win_rate=("net_pnl", lambda x: (x > 0).mean()),
    )
    lines = ["| DTE | Trades | Avg PnL | Total PnL | Win% |",
             "|---|---:|---:|---:|---:|"]
    for b in ["0-2", "3-5", "6-10", "11-20", "21+"]:
        if b not in tbl.index: continue
        r = tbl.loc[b]
        lines.append(f"| {b} | {r['n']} | {_fmt_pnl(r['pnl_mean'])} | {_fmt_pnl(r['pnl_sum'])} | {r['win_rate']*100:.0f}% |")
    return "\n".join(lines)


def _vix_breakdown(ledger: pd.DataFrame) -> str:
    if ledger.empty or "vix_bucket" not in ledger.columns:
        return "(no VIX data)"
    order = {"<10": 0, "10-13": 1, "13-17": 2, "17-22": 3, "22-30": 4, ">30": 5}
    tbl = ledger.groupby("vix_bucket", dropna=False).agg(
        n=("net_pnl", "count"),
        pnl_mean=("net_pnl", "mean"),
        pnl_sum=("net_pnl", "sum"),
        win_rate=("net_pnl", lambda x: (x > 0).mean()),
    ).reset_index()
    tbl["_ord"] = tbl["vix_bucket"].map(order).fillna(9)
    tbl = tbl.sort_values("_ord")
    lines = ["| VIX | Trades | Avg PnL | Total PnL | Win% |",
             "|---|---:|---:|---:|---:|"]
    for _, r in tbl.iterrows():
        lines.append(f"| {r['vix_bucket']} | {r['n']} | {_fmt_pnl(r['pnl_mean'])} | {_fmt_pnl(r['pnl_sum'])} | {r['win_rate']*100:.0f}% |")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    stamp = datetime.now().strftime("%Y%m%d")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    print(f"stamp: {stamp}")

    # Experiment definitions: (symbol, wings, filter_variants, expiry_type)
    # FINNIFTY/MIDCPNIFTY: expiry_type="month" to match the baseline and isolate the
    # DTE filter effect without inflating trade count via pre-discontinuation weekly entries.
    # Each filter_variant: (label, max_dte, vix_min)
    experiments = [
        ("FINNIFTY",   [4, 6, 8], [("DTE<=7", 7, None)], "month"),
        ("MIDCPNIFTY", [4, 6, 8], [("DTE<=7", 7, None)], "month"),
        ("NIFTY",      [6, 8],    [("VIX>=13", None, 13.0)], "week"),
    ]

    # Store all results in memory for later report generation
    # Key: (symbol, wing, label)
    all_ledgers: dict[tuple[str, int, str], pd.DataFrame] = {}

    for symbol, wings, variants, expiry_type in experiments:
        print(f"\n{'='*60}")
        print(f"  {symbol}  (expiry_type={expiry_type})")
        print(f"{'='*60}")
        for wing in wings:
            # Baseline
            baseline = _load_baseline(symbol, wing)
            if not baseline.empty:
                all_ledgers[(symbol, wing, "baseline")] = baseline
                s = _stats(baseline)
                print(
                    f"  {symbol:12s} wing-{wing} {'baseline (month)':30s}: "
                    f"trades={s.get('trades',0):>4}  "
                    f"net={_fmt_pnl(s.get('net_pnl')):>12}  "
                    f"sharpe={_fmt(s.get('sharpe')):>7}"
                )
            # Filtered variants
            for label, max_dte, vix_min in variants:
                ledger = _run_ic(symbol, wing, max_dte, vix_min, expiry_type, stamp, label)
                all_ledgers[(symbol, wing, label)] = ledger

    # ---------------------------------------------------------------------------
    # Build report
    # ---------------------------------------------------------------------------
    sections: list[str] = [
        "# DTE-Filtered Iron Condor Comparison (Monthly, Apples-to-Apples)",
        "",
        f"Run: `{stamp}`  |  Baseline: `{BASELINE_STAMP}`  |  Window: {FROM_DATE} to {TO_DATE}",
        "",
        "**Setup**: baseline and filtered runs both use `expiry_type=month` for FINNIFTY/MIDCPNIFTY.",
        "The DTE<=7 filter restricts entry to the final 7 calendar days of the monthly expiry cycle.",
        "This isolates the pure filter effect without inflating trade count via pre-discontinuation weekly entries.",
        "",
        "NIFTY uses `expiry_type=week` throughout (always weekly); filter tested: `VIX >= 13` floor.",
        "",
        "IS split: 2022-2024 | OOS split: 2025-2026",
        "",
    ]

    for symbol, wings, variants, expiry_type in experiments:
        sections += [f"---", f"## {symbol}", ""]

        if symbol == "NIFTY":
            sections.append("NIFTY weekly throughout. Filter: `VIX >= 13` floor removes ultra-calm low-premium entries.")
        else:
            sections.append(
                f"Both baseline and filtered use `expiry_type=month` — apples-to-apples comparison. "
                f"`DTE <= 7` = enter only in the final week of the monthly expiry cycle."
            )
        sections.append("")

        for wing in wings:
            sections += [f"### Wing-{wing}", ""]
            rows = []

            baseline = all_ledgers.get((symbol, wing, "baseline"), pd.DataFrame())
            if not baseline.empty:
                rows.append(_comparison_row(f"Baseline (unrestricted, monthly)", baseline))

            for label, _, _ in variants:
                filtered = all_ledgers.get((symbol, wing, label), pd.DataFrame())
                if not filtered.empty:
                    rows.append(_comparison_row(label, filtered))

            sections += _table_header()
            for r in rows:
                sections.append(_table_row(r))
            sections.append("")

            # Detailed breakdowns for each filtered variant
            for label, _, _ in variants:
                filtered = all_ledgers.get((symbol, wing, label), pd.DataFrame())
                if filtered.empty:
                    continue
                sections += [
                    f"#### {label} — DTE breakdown",
                    "",
                    _dte_breakdown(filtered),
                    "",
                    f"#### {label} — VIX bucket breakdown",
                    "",
                    _vix_breakdown(filtered),
                    "",
                ]

    # Also show baseline DTE breakdown for each symbol (wing-6 as representative)
    sections += ["---", "## Baseline DTE Breakdown (wing-6, for reference)", ""]
    for symbol, wings, _, _expiry in experiments:
        rep_wing = 6 if 6 in wings else wings[0]
        baseline = all_ledgers.get((symbol, rep_wing, "baseline"), pd.DataFrame())
        if baseline.empty:
            continue
        sections += [
            f"### {symbol} wing-{rep_wing} baseline",
            "",
            _dte_breakdown(baseline),
            "",
        ]

    sections += [
        "---",
        "## Read",
        "",
        "- **t-stat > 1.96** = statistically significant (weekly bucket resampling).",
        "  t-stat dropping significantly from baseline to filtered = filter removed signal, not noise.",
        "  t-stat increasing = filter improved the signal-to-noise ratio.",
        "- **OOS (2025-2026)**: only 1.5 years; treat as directionally informative.",
        "  Strong OOS degradation when filter applied suggests overfitting to IS thresholds.",
        "- **DTE ≤ 7 on monthly**: enters only in expiry week. 15-30 DTE entries are completely excluded.",
        "  This is structurally different from the unrestricted run — fewer but higher-theta trades.",
        "- **MIDCPNIFTY weekly discontinued Nov 2024**: after that, max_dte=7 means expiry-week monthly",
        "  entries only. Pre-Nov 2024, weekly entries were naturally ≤7 DTE anyway.",
        "- If filter gives fewer trades with similar/better Sharpe AND t-stat stays ≥ 1.96: real edge.",
        "  If t-stat drops below 1.96 on filtered run: statistically indistinguishable from noise.",
    ]

    out_path = OUT_DIR / f"{stamp}_dte_filtered_ic_comparison.md"
    out_path.write_text("\n".join(sections), encoding="utf-8")
    print(f"\nReport -> {out_path}")


if __name__ == "__main__":
    main()

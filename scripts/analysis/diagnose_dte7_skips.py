"""
DTE≤7 Skip Diagnostics
======================
Answers: of all monthly-expiry trading days with DTE ≤ 7, how many did the
engine actually trade and how many were skipped — and why?

Skip categories (checked in order, stops at first match):
  1. atm_missing      — no ATM (or ATMp2 / ATMm2) data for that trade_date × expiry
  2. wing_missing     — ATM present but the relevant long-leg offset missing
                        (ATMp6/ATMm6 for wing-6, ATMp8/ATMm8 for wing-8)
  3. low_oi           — all offsets present but max OI < 500 on entry bar window
                        for at least one leg (engine's liquidity gate)
  4. zero_volume      — OI ok but volume == 0 on every bar in entry window
  5. traded           — appeared in the DTE≤7 ledger
  6. unexplained      — present, liquid, non-zero vol, but still not in ledger

Run after run_dte_filtered_ic.py (monthly mode) so the DTE≤7 ledger CSVs exist.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from options_backtest.calendar import (
    get_instrument_spec,
    is_trading_day,
    monthly_expiry_on_or_after,
)

DHAN_ROOT    = ROOT / "data/processed/options/dhan"
LEDGER_DIR   = ROOT / "reports/backtests/options/risk_management"
LEDGER_STAMP = "20260506"
FROM_DATE    = date(2022, 2, 1)
TO_DATE      = date(2026, 4, 30)
MAX_DTE      = 7
MIN_DTE      = 1
# Engine's LiquidityConfig defaults (liquidity.py)
MIN_ROWS     = 50     # minimum bars in the full day's data for any leg
MIN_VOLUME   = 200    # minimum total all-day volume for any leg
MIN_OI       = 500    # minimum max OI for any leg
# expiry_type used when the engine runs DTE-filtered ICs for these symbols
EXPIRY_TYPE  = "month"

SYMBOLS = ["FINNIFTY", "MIDCPNIFTY"]
WINGS   = [4, 6, 8]            # long-leg offsets to check


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_atm_entry_timestamps(symbol: str) -> dict[date, pd.Timestamp]:
    """
    Replicate engine logic: for each trade_date, find nearest_timestamp in ATM data
    to the configured entry_time (09:15 default).  Returns {trade_date -> entry_ts}.
    Only includes dates where a valid same-day bar is found.
    """
    spec = get_instrument_spec(symbol)
    p = DHAN_ROOT / spec.dhan_folder / f"{EXPIRY_TYPE}/expiry_code_1/call/ATM.parquet"
    if not p.exists():
        return {}
    df = pd.read_parquet(p, columns=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False).dt.tz_localize(None)
    entry_target_time = pd.Timestamp("1900-01-01 09:15:00").time()
    out: dict[date, pd.Timestamp] = {}
    for d, grp in df.groupby(df["timestamp"].dt.date):
        ts_index = pd.DatetimeIndex(grp["timestamp"].sort_values())
        entry_target = pd.Timestamp(datetime.combine(d, entry_target_time))
        # nearest_timestamp from calendar
        nearest = ts_index[abs(ts_index - entry_target).argmin()]
        if nearest.date() == d:
            out[d] = nearest
    return out


def _load_offset_with_expiry(symbol: str, side: str, key: str) -> dict[tuple[date, date], pd.DataFrame]:
    """
    Load the month/ parquet (matching what the engine reads with expiry_type="month"),
    add expiry column, return dict (trade_date, expiry) -> day-slice df.
    """
    spec = get_instrument_spec(symbol)
    p = DHAN_ROOT / spec.dhan_folder / f"{EXPIRY_TYPE}/expiry_code_1" / side / f"{key}.parquet"
    if not p.exists():
        return {}
    df = pd.read_parquet(p)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False).dt.tz_localize(None)
    df["trade_date"] = df["timestamp"].dt.date

    # Build trading date list for expiry resolution
    all_dates = sorted(df["trade_date"].unique())
    expiry_map: dict[date, date] = {}
    for d in all_dates:
        try:
            expiry_map[d] = monthly_expiry_on_or_after(symbol, d, min_dte=0, trading_dates=all_dates)
        except Exception:
            expiry_map[d] = None  # type: ignore
    df["expiry"] = df["trade_date"].map(expiry_map)

    out: dict[tuple[date, date], pd.DataFrame] = {}
    for (td, exp), grp in df.groupby(["trade_date", "expiry"]):
        if exp is None:
            continue
        out[(td, exp)] = grp.reset_index(drop=True)
    return out


def _has_entry_bar(day_df: pd.DataFrame, entry_ts: pd.Timestamp) -> tuple[bool, str]:
    """
    Check that a non-zero-volume bar exists at the exact entry_ts timestamp —
    mirrors engine's bar_at() + zero-volume rejection in _entry_fills().
    """
    if day_df.empty:
        return False, "no_data"
    ts_col = pd.to_datetime(day_df["timestamp"])
    match = day_df[ts_col == entry_ts]
    if match.empty:
        return False, "no_bar_at_entry_time"
    vol = float(pd.to_numeric(match.iloc[0].get("volume", 0), errors="coerce") or 0)
    if vol == 0:
        return False, "zero_vol_at_entry"
    return True, "ok"


def _liquidity_pass(day_df: pd.DataFrame) -> tuple[bool, str]:
    """
    Mirror engine's contract_is_liquid(LiquidityConfig defaults):
      - len(df) >= 50 rows (whole day)
      - total all-day volume >= 200
      - max all-day OI >= 500
    Returns (passes, fail_reason).
    """
    if day_df.empty:
        return False, "no_data"
    if len(day_df) < MIN_ROWS:
        return False, f"sparse_rows({len(day_df)})"
    vol = pd.to_numeric(day_df["volume"], errors="coerce").fillna(0).sum()
    oi  = pd.to_numeric(day_df["oi"],     errors="coerce").fillna(0).max()
    if vol < MIN_VOLUME:
        return False, f"low_volume({int(vol)})"
    if oi < MIN_OI:
        return False, f"low_oi({int(oi)})"
    return True, "ok"


def _load_ledger(symbol: str, wing: int) -> set[date]:
    """Return set of entry_dates that the DTE≤7 engine actually traded."""
    path = LEDGER_DIR / f"{LEDGER_STAMP}_dtelt7_ic_wing{wing}_{symbol.lower()}.csv"
    if not path.exists():
        return set()
    df = pd.read_csv(path)
    if "strategy" in df.columns:
        df = df[df["strategy"] != "TOTAL"]
    return set(pd.to_datetime(df["entry_date"]).dt.date)


def _monthly_expiries(symbol: str) -> list[date]:
    """All unique monthly expiries in range for this symbol."""
    spec = get_instrument_spec(symbol)
    p = DHAN_ROOT / spec.dhan_folder / f"{EXPIRY_TYPE}/expiry_code_1/call/ATM.parquet"
    if not p.exists():
        return []
    df = pd.read_parquet(p, columns=["timestamp"])
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=False).dt.tz_localize(None)
    all_dates = sorted(df["timestamp"].dt.date.unique())
    expiries: set[date] = set()
    for d in all_dates:
        try:
            exp = monthly_expiry_on_or_after(symbol, d, min_dte=0, trading_dates=all_dates)
            if FROM_DATE <= exp <= TO_DATE:
                expiries.add(exp)
        except Exception:
            pass
    return sorted(expiries)


def _candidate_days(symbol: str, expiry: date) -> list[tuple[date, int]]:
    """All trading days with MIN_DTE ≤ DTE ≤ MAX_DTE for this expiry."""
    out = []
    d = expiry - timedelta(days=MAX_DTE)
    while d <= expiry - timedelta(days=MIN_DTE):
        if is_trading_day(d) and FROM_DATE <= d <= TO_DATE:
            dte = (expiry - d).days
            out.append((d, dte))
        d += timedelta(days=1)
    return out


# ---------------------------------------------------------------------------
# Main diagnostic
# ---------------------------------------------------------------------------

def diagnose(symbol: str) -> pd.DataFrame:
    spec = get_instrument_spec(symbol)
    print(f"\n{symbol}: loading {EXPIRY_TYPE}/ parquets...")

    # All parquets read from month/ — matching what the engine uses with expiry_type="month"
    print(f"  Loading ATM entry timestamps...")
    entry_ts_by_date = _load_atm_entry_timestamps(symbol)

    print(f"  Loading ATM, short-leg, and wing data...")
    atm_dict   = _load_offset_with_expiry(symbol, "call", "ATM")
    short_call = _load_offset_with_expiry(symbol, "call", "ATMp2")
    short_put  = _load_offset_with_expiry(symbol, "put",  "ATMm2")

    leg_data: dict[int, dict[str, dict[tuple[date, date], pd.DataFrame]]] = {}
    for w in WINGS:
        leg_data[w] = {
            "call": _load_offset_with_expiry(symbol, "call", f"ATMp{w}"),
            "put":  _load_offset_with_expiry(symbol, "put",  f"ATMm{w}"),
        }

    # Load ledgers for each wing
    traded_dates: dict[int, set[date]] = {w: _load_ledger(symbol, w) for w in WINGS}
    for w in WINGS:
        print(f"  Wing-{w} ledger: {len(traded_dates[w])} traded days")

    expiries = _monthly_expiries(symbol)
    print(f"  {len(expiries)} monthly expiries in range")

    rows = []
    for expiry in expiries:
        candidates = _candidate_days(symbol, expiry)
        for trade_date, dte in candidates:
            row: dict = {
                "symbol":     symbol,
                "expiry":     expiry,
                "trade_date": trade_date,
                "dte":        dte,
            }

            # Check ATM + short-leg data presence
            atm_ok    = (trade_date, expiry) in atm_dict
            sc_slice  = short_call.get((trade_date, expiry), pd.DataFrame())
            sp_slice  = short_put.get((trade_date, expiry), pd.DataFrame())
            short_ok  = not sc_slice.empty and not sp_slice.empty
            row["atm_present"]   = atm_ok
            row["short_present"] = short_ok

            # Short-leg liquidity (engine checks all 4 legs)
            if short_ok:
                sc_pass, sc_reason = _liquidity_pass(sc_slice)
                sp_pass, sp_reason = _liquidity_pass(sp_slice)
                short_liquid = sc_pass and sp_pass
                short_liquid_reason = sc_reason if not sc_pass else sp_reason
            else:
                short_liquid = False
                short_liquid_reason = "no_data"

            row["short_liquid"] = short_liquid

            for w in WINGS:
                call_slice = leg_data[w]["call"].get((trade_date, expiry), pd.DataFrame())
                put_slice  = leg_data[w]["put"].get((trade_date, expiry), pd.DataFrame())
                call_ok    = not call_slice.empty
                put_ok     = not put_slice.empty
                row[f"wing{w}_call_present"] = call_ok
                row[f"wing{w}_put_present"]  = put_ok
                row[f"wing{w}_present"]      = call_ok and put_ok

                # Long-leg liquidity
                if call_ok and put_ok:
                    call_pass, call_reason = _liquidity_pass(call_slice)
                    put_pass,  put_reason  = _liquidity_pass(put_slice)
                    wing_liquid = call_pass and put_pass
                    wing_reason = call_reason if not call_pass else put_reason
                    row[f"wing{w}_call_vol"] = int(pd.to_numeric(call_slice["volume"], errors="coerce").fillna(0).sum())
                    row[f"wing{w}_put_vol"]  = int(pd.to_numeric(put_slice["volume"],  errors="coerce").fillna(0).sum())
                    row[f"wing{w}_call_oi"]  = int(pd.to_numeric(call_slice["oi"],     errors="coerce").fillna(0).max())
                    row[f"wing{w}_put_oi"]   = int(pd.to_numeric(put_slice["oi"],      errors="coerce").fillna(0).max())
                    row[f"wing{w}_call_rows"] = len(call_slice)
                    row[f"wing{w}_put_rows"]  = len(put_slice)
                else:
                    wing_liquid = False
                    wing_reason = "no_data"
                    for k in (f"wing{w}_call_vol", f"wing{w}_put_vol", f"wing{w}_call_oi",
                              f"wing{w}_put_oi", f"wing{w}_call_rows", f"wing{w}_put_rows"):
                        row[k] = None

                # Skip reason — mirrors exact engine gate order
                if not atm_ok or not short_ok:
                    row[f"wing{w}_skip_reason"] = "atm_or_short_missing"
                elif not (call_ok and put_ok):
                    row[f"wing{w}_skip_reason"] = "wing_data_missing"
                elif not short_liquid:
                    row[f"wing{w}_skip_reason"] = f"short_illiquid({short_liquid_reason})"
                elif not wing_liquid:
                    row[f"wing{w}_skip_reason"] = f"wing_illiquid({wing_reason})"
                elif trade_date in traded_dates[w]:
                    row[f"wing{w}_skip_reason"] = "traded"
                else:
                    # Remaining: check entry-bar availability at the exact ATM entry timestamp
                    entry_ts = entry_ts_by_date.get(trade_date)
                    if entry_ts is None:
                        row[f"wing{w}_skip_reason"] = "no_atm_entry_bar"
                    else:
                        sc_bar_ok, sc_bar_reason = _has_entry_bar(sc_slice, entry_ts)
                        sp_bar_ok, sp_bar_reason = _has_entry_bar(sp_slice, entry_ts)
                        cl_bar_ok, cl_bar_reason = _has_entry_bar(call_slice, entry_ts)
                        pu_bar_ok, pu_bar_reason = _has_entry_bar(put_slice, entry_ts)
                        if not sc_bar_ok:
                            row[f"wing{w}_skip_reason"] = f"no_entry_bar(short_call:{sc_bar_reason})"
                        elif not sp_bar_ok:
                            row[f"wing{w}_skip_reason"] = f"no_entry_bar(short_put:{sp_bar_reason})"
                        elif not cl_bar_ok:
                            row[f"wing{w}_skip_reason"] = f"no_entry_bar(long_call:{cl_bar_reason})"
                        elif not pu_bar_ok:
                            row[f"wing{w}_skip_reason"] = f"no_entry_bar(long_put:{pu_bar_reason})"
                        else:
                            row[f"wing{w}_skip_reason"] = "unexplained"

            rows.append(row)

    return pd.DataFrame(rows)


def _summary_table(df: pd.DataFrame, symbol: str, wing: int) -> str:
    col = f"wing{wing}_skip_reason"
    if col not in df.columns:
        return "(no data)"
    total = len(df)
    # Normalise reason to base category for ordering
    def _base(r: str) -> str:
        return r.split("(")[0]
    counts = df[col].value_counts()
    base_counts: dict[str, int] = {}
    for reason, n in counts.items():
        base_counts[_base(reason)] = base_counts.get(_base(reason), 0) + int(n)
    lines = [
        f"### {symbol} wing-{wing} — {total} candidate days (monthly DTE 1–7)",
        "",
        "| Outcome | Days | % |",
        "|---|---:|---:|",
    ]
    order = ["traded", "atm_or_short_missing", "wing_data_missing",
             "short_illiquid", "wing_illiquid",
             "no_atm_entry_bar", "no_entry_bar", "unexplained"]
    for outcome in order:
        n = base_counts.get(outcome, 0)
        if n == 0:
            continue
        # Show detail breakdown for illiquid categories
        detail = ""
        if outcome in ("short_illiquid", "wing_illiquid"):
            sub = {r: int(c) for r, c in counts.items() if _base(r) == outcome}
            if len(sub) > 1:
                detail = " → " + ", ".join(f"{r.split('(')[1].rstrip(')')}: {c}" for r, c in sorted(sub.items()) if "(" in r)
        lines.append(f"| {outcome}{detail} | {n} | {n/total*100:.1f}% |")
    return "\n".join(lines)


def _dte_breakdown(df: pd.DataFrame, symbol: str, wing: int) -> str:
    col = f"wing{wing}_skip_reason"
    if col not in df.columns:
        return "(no data)"
    df2 = df.copy()
    df2[col] = df2[col].str.split("(").str[0]  # normalise to base category
    tbl = df2.groupby(["dte", col]).size().unstack(fill_value=0)
    lines = ["| DTE | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained |",
             "|---|---:|---:|---:|---:|---:|---:|---:|"]
    for dte in sorted(tbl.index):
        row = tbl.loc[dte]
        no_bar = int(row.get("no_atm_entry_bar", 0)) + int(row.get("no_entry_bar", 0))
        lines.append(
            f"| {dte} "
            f"| {row.get('traded', 0)} "
            f"| {row.get('atm_or_short_missing', 0)} "
            f"| {row.get('wing_data_missing', 0)} "
            f"| {row.get('short_illiquid', 0)} "
            f"| {row.get('wing_illiquid', 0)} "
            f"| {no_bar} "
            f"| {row.get('unexplained', 0)} |"
        )
    return "\n".join(lines)


def _year_breakdown(df: pd.DataFrame, symbol: str, wing: int) -> str:
    col = f"wing{wing}_skip_reason"
    if col not in df.columns:
        return "(no data)"
    df2 = df.copy()
    df2["year"] = pd.to_datetime(df2["trade_date"]).dt.year
    df2[col] = df2[col].str.split("(").str[0]  # normalise to base category
    tbl = df2.groupby(["year", col]).size().unstack(fill_value=0)
    lines = ["| Year | traded | atm_missing | wing_missing | short_illiquid | wing_illiquid | no_entry_bar | unexplained | trade_rate% |",
             "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for year in sorted(tbl.index):
        row  = tbl.loc[year]
        tot  = int(row.sum())
        tr   = int(row.get("traded", 0))
        no_bar = int(row.get("no_atm_entry_bar", 0)) + int(row.get("no_entry_bar", 0))
        lines.append(
            f"| {year} "
            f"| {tr} "
            f"| {row.get('atm_or_short_missing', 0)} "
            f"| {row.get('wing_data_missing', 0)} "
            f"| {row.get('short_illiquid', 0)} "
            f"| {row.get('wing_illiquid', 0)} "
            f"| {no_bar} "
            f"| {row.get('unexplained', 0)} "
            f"| {tr/tot*100:.0f}% |"
        )
    return "\n".join(lines)


def main() -> None:
    all_dfs: dict[str, pd.DataFrame] = {}
    for symbol in SYMBOLS:
        df = diagnose(symbol)
        all_dfs[symbol] = df
        csv_path = LEDGER_DIR / f"20260506_{symbol.lower()}_dte7_skip_diagnosis.csv"
        df.to_csv(csv_path, index=False)
        print(f"  Saved {csv_path.name}")

    # Build report
    lines = [
        "# DTE<=7 Monthly IC — Skip Diagnosis (v2)",
        "",
        "Candidate days = all trading days within DTE 1–7 of a monthly expiry, 2022-02 to 2026-04.",
        f"Reads `{EXPIRY_TYPE}/expiry_code_1/` parquets — same path as engine with `expiry_type=month`.",
        "Liquidity gates mirror engine defaults: min_rows=50, min_total_volume=200, min_max_oi=500.",
        "",
        "Skip categories:",
        "- **traded**: appeared in the DTE<=7 ledger (engine executed the trade)",
        "- **atm_or_short_missing**: ATM or ATM+/-2 data absent in month/ parquet for that (date, expiry)",
        "- **wing_data_missing**: ATM/short present but the long-leg offset (ATMp6 etc.) absent",
        "- **short_illiquid**: short legs present but fail liquidity gate (rows/volume/OI)",
        "- **wing_illiquid**: all data present but long legs fail liquidity gate",
        "- **no_entry_bar**: data and liquidity pass but no non-zero-volume bar at exact entry timestamp for one or more legs",
        "  (sub-reason shows which leg: short_call/short_put/long_call/long_put + why: no_bar_at_entry_time or zero_vol_at_entry)",
        "- **unexplained**: all checks pass including entry bar — truly unknown; likely exit bar missing or edge case",
        "",
    ]

    for symbol in SYMBOLS:
        df = all_dfs[symbol]
        lines += [f"---", f"## {symbol}", ""]

        for wing in WINGS:
            lines += [
                _summary_table(df, symbol, wing),
                "",
                "#### By DTE",
                "",
                _dte_breakdown(df, symbol, wing),
                "",
                "#### By Year",
                "",
                _year_breakdown(df, symbol, wing),
                "",
            ]

    # Also add a cross-wing comparison for the dominant wing-6 and wing-8
    lines += ["---", "## Availability at each wing offset (all candidate days)", ""]
    for symbol in SYMBOLS:
        df = all_dfs[symbol]
        total = len(df)
        lines.append(f"### {symbol} — {total} total candidate days")
        lines.append("")
        lines.append("| Metric | Days | % |")
        lines.append("|---|---:|---:|")
        for col, label in [
            ("atm_present",       "ATM data present"),
            ("short_present",     "ATM±2 (short legs) present"),
            ("wing4_present",     "ATM±4 present"),
            ("wing6_present",     "ATM±6 present"),
            ("wing8_present",     "ATM±8 present"),
        ]:
            if col in df.columns:
                n = int(df[col].sum())
                lines.append(f"| {label} | {n} | {n/total*100:.1f}% |")
        lines.append("")

    out_path = LEDGER_DIR / "20260506_dte7_skip_diagnosis.md"
    out_path.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nReport -> {out_path}")


if __name__ == "__main__":
    main()

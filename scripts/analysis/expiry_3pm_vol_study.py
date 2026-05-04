"""
expiry_3pm_vol_study.py

Empirical study: how much do OTM options move in the 15:00–15:28 window on
expiry day? Answers: "If I buy cheap OTM options at 3 PM on expiry day, how
often do they hit 2x / 3x / 5x / 10x before 3:28 PM?"

Instruments: NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY
Data:  NIFTY uses processed parquets; others parsed from raw JSON.
Output: reports/analysis/expiry_3pm_vol_study.csv + console summary table.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Iterator

import numpy as np
import pandas as pd

# ── project root so `options_backtest` is importable ─────────────────────────
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from options_backtest.calendar import is_trading_day  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger(__name__)

# ── constants ─────────────────────────────────────────────────────────────────
QUARANTINE: frozenset[date] = frozenset({
    date(2025, 9, 25),
    date(2025, 12, 24),
})

STUDY_START = date(2021, 1, 1)
STUDY_END   = date(2026, 12, 31)

OFFSETS_NEEDED = ("ATM", "ATMp1", "ATMp2", "ATMm1", "ATMm2")

STRUCTURES = {
    "Straddle":   ("ATM",   "ATM"),
    "Strangle-1": ("ATMp1", "ATMm1"),
    "Strangle-2": ("ATMp2", "ATMm2"),
}

# multiplier thresholds
THRESHOLDS = (2.0, 3.0, 5.0, 10.0)

RAW_ROOT  = ROOT / "data" / "raw"  / "options" / "dhan"
PROC_ROOT = ROOT / "data" / "processed" / "options" / "dhan"
OUT_DIR   = ROOT / "reports" / "analysis"
OUT_CSV   = OUT_DIR / "expiry_3pm_vol_study.csv"


# ── trading-date list (2021-2026) ─────────────────────────────────────────────
def _build_trading_dates() -> list[date]:
    """All NSE trading days 2021-01-01 to 2026-12-31."""
    days: list[date] = []
    d = STUDY_START
    while d <= STUDY_END:
        if is_trading_day(d):
            days.append(d)
        d += timedelta(days=1)
    return days


TRADING_DATES: list[date] = _build_trading_dates()
TRADING_SET:   set[date]  = set(TRADING_DATES)


# ── expiry calendar per instrument ────────────────────────────────────────────
def _next_weekday_on_or_after(d: date, weekday: int) -> date:
    days_ahead = (weekday - d.weekday()) % 7
    return d + timedelta(days=days_ahead)


def _walk_back(d: date) -> date:
    """If d is not a trading day, walk backwards to the nearest prior trading day."""
    while d not in TRADING_SET:
        d -= timedelta(days=1)
    return d


def get_expiry_days(instrument: str) -> set[date]:
    """
    Generate weekly expiry dates for `instrument` over the study window.

    NIFTY     : Thursday until 2025-08-28, Tuesday from 2025-09-02
    BANKNIFTY : Thursday until 2023-05-17, Wednesday from 2023-05-19
    FINNIFTY  : Tuesday always
    MIDCPNIFTY: Monday always
    """
    inst = instrument.upper()
    expiries: set[date] = set()
    d = STUDY_START

    # anchor to first Monday of the study window
    while d <= STUDY_END:
        if inst == "NIFTY":
            if d <= date(2025, 8, 28):
                candidate = _next_weekday_on_or_after(d, 3)  # Thursday
            else:
                candidate = _next_weekday_on_or_after(d, 1)  # Tuesday
        elif inst == "BANKNIFTY":
            if d <= date(2023, 5, 17):
                candidate = _next_weekday_on_or_after(d, 3)  # Thursday
            else:
                candidate = _next_weekday_on_or_after(d, 2)  # Wednesday
        elif inst == "FINNIFTY":
            candidate = _next_weekday_on_or_after(d, 1)  # Tuesday
        elif inst == "MIDCPNIFTY":
            candidate = _next_weekday_on_or_after(d, 0)  # Monday
        else:
            raise ValueError(f"Unknown instrument: {instrument}")

        if candidate > STUDY_END:
            break

        adjusted = _walk_back(candidate)
        if adjusted not in QUARANTINE:
            expiries.add(adjusted)

        # advance to the day after the unadjusted candidate so we get the next week
        d = candidate + timedelta(days=1)

    return expiries


# ── JSON loader (BANKNIFTY / FINNIFTY / MIDCPNIFTY) ──────────────────────────
def _load_json_instrument(
    raw_root: Path,
    instrument: str,
    offset: str,
    side: str,        # "call" or "put"
) -> pd.DataFrame:
    """
    Read all monthly JSON files for one offset+side, concatenate, deduplicate
    by timestamp, sort. Returns DataFrame with columns:
      timestamp (tz-naive IST), open, high, low, close, volume, oi, iv, strike, spot
    Returns empty DataFrame if folder/files are missing.
    """
    folder = raw_root / instrument.lower() / "week" / "expiry_code_1" / side / offset
    if not folder.exists():
        return pd.DataFrame()

    json_key = "ce" if side == "call" else "pe"
    chunks: list[pd.DataFrame] = []

    for fp in sorted(folder.glob("*.json")):
        try:
            with fp.open() as fh:
                raw = json.load(fh)
            arrays = raw["response"]["data"][json_key]
            # some monthly files are empty or null
            if arrays is None or not arrays.get("timestamp"):
                continue
            ts = pd.to_datetime(arrays["timestamp"], unit="s", utc=True)
            ts_ist = ts.tz_convert("Asia/Kolkata").tz_localize(None)
            chunk = pd.DataFrame({
                "timestamp": ts_ist,
                "open":      arrays["open"],
                "high":      arrays["high"],
                "low":       arrays["low"],
                "close":     arrays["close"],
                "volume":    arrays["volume"],
                "oi":        arrays["oi"],
                "iv":        arrays["iv"],
                "strike":    arrays["strike"],
                "spot":      arrays["spot"],
            })
            chunks.append(chunk)
        except Exception as exc:
            log.warning("Skipping %s: %s", fp.name, exc)

    if not chunks:
        return pd.DataFrame()

    df = pd.concat(chunks, ignore_index=True)
    df = df.drop_duplicates(subset="timestamp").sort_values("timestamp").reset_index(drop=True)
    return df


# ── NIFTY parquet loader ──────────────────────────────────────────────────────
def _load_nifty_parquet(offset: str, side: str) -> pd.DataFrame:
    """
    Load a NIFTY processed parquet for one offset+side.
    Strips timezone so between_time() works without tz complications.
    Returns empty DataFrame if file is missing.
    """
    fp = PROC_ROOT / "nifty" / "week" / "expiry_code_1" / side / f"{offset}.parquet"
    if not fp.exists():
        return pd.DataFrame()
    df = pd.read_parquet(fp)
    # parquet timestamps are tz-aware IST; strip timezone using DatetimeIndex
    # (DatetimeIndex.tz_localize(None) strips tz while preserving local wall-clock time)
    df["timestamp"] = pd.DatetimeIndex(df["timestamp"]).tz_localize(None)
    return df


# ── per-expiry computation ────────────────────────────────────────────────────
def _extract_window(df: pd.DataFrame, expiry_date: date) -> pd.DataFrame:
    """
    Return bars for `expiry_date` between 15:00:00 and 15:28:00 inclusive.
    Uses between_time; df["timestamp"] must be tz-naive and set as index.
    """
    day_df = df[df["timestamp"].dt.date == expiry_date]
    if day_df.empty:
        return day_df
    day_df = day_df.set_index("timestamp")
    window = day_df.between_time("15:00", "15:28")
    return window.reset_index()


def _compute_structure(
    call_win: pd.DataFrame,
    put_win:  pd.DataFrame,
) -> dict | None:
    """
    Given window bars for one call leg and one put leg, compute all metrics.
    Returns None if either window is empty or entry price is zero.
    """
    if call_win.empty or put_win.empty:
        return None

    entry_call = call_win.iloc[0]["close"]
    entry_put  = put_win.iloc[0]["close"]

    if entry_call <= 0 or entry_put <= 0:
        return None

    entry_cost = entry_call + entry_put

    call_closes = call_win["close"].values
    put_closes  = put_win["close"].values

    max_call_mult = call_closes.max() / entry_call
    max_put_mult  = put_closes.max()  / entry_put
    best_mult     = max(max_call_mult, max_put_mult)

    final_call_mult = call_closes[-1] / entry_call
    final_put_mult  = put_closes[-1]  / entry_put

    return {
        "entry_cost":       round(entry_cost, 4),
        "best_mult":        round(best_mult, 4),
        "hit_2x":           best_mult >= 2.0,
        "hit_3x":           best_mult >= 3.0,
        "hit_5x":           best_mult >= 5.0,
        "hit_10x":          best_mult >= 10.0,
        "final_call_mult":  round(final_call_mult, 4),
        "final_put_mult":   round(final_put_mult, 4),
    }


def _spot_metrics(call_win: pd.DataFrame) -> dict:
    """Derive spot movement metrics from the call leg window (spot column)."""
    if call_win.empty or "spot" not in call_win.columns:
        return {"spot_at_entry": np.nan, "spot_max_up_pct": np.nan, "spot_max_down_pct": np.nan}

    spot_entry = call_win.iloc[0]["spot"]
    if spot_entry <= 0:
        return {"spot_at_entry": spot_entry, "spot_max_up_pct": np.nan, "spot_max_down_pct": np.nan}

    spots = call_win["spot"].values
    spot_max_up   = (spots.max() - spot_entry) / spot_entry * 100
    spot_max_down = (spot_entry - spots.min()) / spot_entry * 100

    return {
        "spot_at_entry":    round(float(spot_entry), 2),
        "spot_max_up_pct":  round(float(spot_max_up),   4),
        "spot_max_down_pct": round(float(spot_max_down), 4),
    }


# ── data cache so each parquet/JSON is loaded only once per instrument ────────
class _DataCache:
    """Lazy-load and cache full DataFrames per (instrument, offset, side)."""

    def __init__(self) -> None:
        self._cache: dict[tuple, pd.DataFrame] = {}

    def get(self, instrument: str, offset: str, side: str) -> pd.DataFrame:
        key = (instrument.upper(), offset, side)
        if key not in self._cache:
            inst = instrument.upper()
            if inst == "NIFTY":
                df = _load_nifty_parquet(offset, side)
            else:
                df = _load_json_instrument(RAW_ROOT, inst, offset, side)
            self._cache[key] = df
        return self._cache[key]


# ── main study loop ───────────────────────────────────────────────────────────
def run_study() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    instruments = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    all_rows: list[dict] = []

    for instrument in instruments:
        log.info("Processing %s ...", instrument)
        cache = _DataCache()

        expiry_days = sorted(get_expiry_days(instrument))
        log.info("  %s expiry days found for %s", len(expiry_days), instrument)

        for expiry_date in expiry_days:
            # pre-load call/put windows for all needed offsets
            windows: dict[tuple, pd.DataFrame] = {}
            for offset in OFFSETS_NEEDED:
                for side in ("call", "put"):
                    full_df = cache.get(instrument, offset, side)
                    if full_df.empty:
                        windows[(offset, side)] = pd.DataFrame()
                    else:
                        windows[(offset, side)] = _extract_window(full_df, expiry_date)

            # use ATM call window for spot metrics and n_bars
            atm_call_win = windows.get(("ATM", "call"), pd.DataFrame())
            spot_m = _spot_metrics(atm_call_win)
            n_bars = len(atm_call_win)

            for struct_name, (call_offset, put_offset) in STRUCTURES.items():
                call_win = windows.get((call_offset, "call"), pd.DataFrame())
                put_win  = windows.get((put_offset,  "put"),  pd.DataFrame())

                metrics = _compute_structure(call_win, put_win)
                if metrics is None:
                    continue  # missing data for this structure on this day

                row = {
                    "instrument":    instrument,
                    "expiry_date":   expiry_date,
                    "structure":     struct_name,
                    "n_bars":        n_bars,
                    **spot_m,
                    **metrics,
                }
                all_rows.append(row)

        log.info("  Done %s — %d rows collected so far", instrument, len(all_rows))

    if not all_rows:
        log.error("No data collected. Check data paths.")
        return

    df_out = pd.DataFrame(all_rows)
    df_out["expiry_date"] = pd.to_datetime(df_out["expiry_date"])
    df_out.to_csv(OUT_CSV, index=False)
    log.info("CSV written: %s  (%d rows)", OUT_CSV, len(df_out))

    _print_summary(df_out)


# ── console summary ───────────────────────────────────────────────────────────
def _print_summary(df: pd.DataFrame) -> None:
    instruments = ["NIFTY", "BANKNIFTY", "FINNIFTY", "MIDCPNIFTY"]
    struct_order = ["Straddle", "Strangle-1", "Strangle-2"]

    for instrument in instruments:
        for struct in struct_order:
            sub = df[(df["instrument"] == instrument) & (df["structure"] == struct)]
            if sub.empty:
                continue

            n = len(sub)
            avg_cost    = sub["entry_cost"].mean()
            med_cost    = sub["entry_cost"].median()
            hit_2x      = sub["hit_2x"].sum()
            hit_3x      = sub["hit_3x"].sum()
            hit_5x      = sub["hit_5x"].sum()
            hit_10x     = sub["hit_10x"].sum()
            avg_best    = sub["best_mult"].mean()
            med_best    = sub["best_mult"].median()

            # best 5 days by best_mult
            top5 = (
                sub.nlargest(5, "best_mult")[["expiry_date", "best_mult"]]
                .apply(lambda r: f"{r['expiry_date'].date()}={r['best_mult']:.1f}x", axis=1)
                .tolist()
            )
            top5_str = ", ".join(top5)

            print(f"\n-- {instrument} / {struct} {'-' * max(0, 44 - len(instrument) - len(struct))}")
            print(f"  Expiry days analyzed : {n}")
            print(f"  Avg entry cost       : ₹{avg_cost:.1f}")
            print(f"  Median entry cost    : ₹{med_cost:.1f}")
            print(f"  Hit 2x               : {hit_2x:>3} / {n}  ({hit_2x/n*100:.1f}%)")
            print(f"  Hit 3x               : {hit_3x:>3} / {n}  ({hit_3x/n*100:.1f}%)")
            print(f"  Hit 5x               : {hit_5x:>3} / {n}  ({hit_5x/n*100:.1f}%)")
            print(f"  Hit 10x              : {hit_10x:>3} / {n}  ({hit_10x/n*100:.1f}%)")
            print(f"  Avg best_mult        : {avg_best:.1f}x")
            print(f"  Median best_mult     : {med_best:.1f}x")
            print(f"  Best 5 days          : {top5_str}")


if __name__ == "__main__":
    run_study()

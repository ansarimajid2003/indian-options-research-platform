# V2 Dashboard Tabs - Historical Explorer & Backtests

## Context

Tab 1 (Live Monitor) is fully deployed on zimaos. The dashboard is modelled on a Bloomberg Terminal: pure black (`#0a0a0a`) background, JetBrains Mono throughout, 10px uppercase labels, hairline 1px borders at `rgba(255,255,255,0.06)`, no decorative elements. All v2 tabs must match this language exactly - not approximate it.

The backend stubs have been replaced through the bridge, models, and route layers. The remaining task is to wire the frontend tabs, add the tab components, add the canonical new-run save wrapper, and complete browser/server verification.

**Build order:** bridge -> models -> routes -> app.jsx tab wiring -> frontend handoff prompt

---

## Implementation Progress - 2026-05-12

> **Status:** Backend v2 foundation is implemented and locally verified. Canonical save wrapper and v2 shell tab routing are now in place. Full frontend tab components are still pending for handoff.

### Completed

- **Step 1 - Parquet schema probe:** Confirmed Dhan options parquet layout is nested by `{symbol}/{expiry_type}/expiry_code_1/{call|put}/{ATM offset}.parquet`. Confirmed columns: `timestamp`, `open`, `high`, `low`, `close`, `volume`, `oi`, `iv`, `strike`, `spot`, `iv_raw`, `iv_spike_flag`, `iv_zero_flag`, `iv_clean`. `timestamp` is timezone-aware `datetime64[ms, Asia/Kolkata]`.
- **Step 2 - Bridge:** Implemented real historical and backtest readers in `options_backtest/dashboard_bridge.py`.
  - Added `MARKET_DATA_ROOT` and `BACKTEST_REPORT_ROOT` support with repo-relative fallbacks.
  - Extended `_SPOT_FILES` to all 5 symbols including BANKNIFTY.
  - Added ordered VIX fallback sources: Dhan 1-min, cleaned minute archive, cleaned daily archive.
  - Added `historical_spot`, `historical_vix`, `historical_options_metadata`, `historical_options`, and `historical_bhavcopy`.
  - Added canonical backtest roots and legacy roots.
  - Added `backtest_list`, `backtest_summary`, `backtest_ledger`, `backtest_equity_curve`, `backtest_monthly_returns`, `backtest_drawdown`, `backtest_events`, and `backtest_decisions`.
  - Added read-only legacy CSV indexing for ledger-style artifacts and summary-only rows.
  - Fixed actual bhavcopy schema handling: `trade_date`, `expiry_date`, `strike`, `option_type`, `settle_price`, `open_interest`, `volume`.
- **Step 3b - Read-only legacy adapter:** Implemented bridge-side legacy indexing and drilldown support for existing CSV ledgers. Summary-only comparison rows are listed with `has_ledger=false` / `has_equity=false`.
- **Step 4 - Models:** Extended `scripts/live/api/models.py`.
  - Enriched `BacktestSummaryModel` with name/symbol/source/compat/metric/drilldown fields.
  - Enriched `OHLCVResponse` with stats, source, generated time, row count, data age, and warnings.
  - Added `HistoricalMetadata`, `DrawdownPoint`, `MonthlyReturn`, `TradeLedgerRow`, `LedgerResponse`, `EventOverlayPoint`, `DecisionLogRow`, and `DecisionLogResponse`.
- **Step 5 - Routes:** Replaced v2 route stubs with real FastAPI endpoints.
  - `GET /api/historical/spot/{symbol}`
  - `GET /api/historical/vix`
  - `GET /api/historical/metadata/{symbol}`
  - `GET /api/historical/options/{symbol}`
  - `GET /api/historical/bhavcopy/{symbol}`
  - `GET /api/backtests`
  - `GET /api/backtests/{id}`
  - `GET /api/backtests/{id}/equity-curve`
  - `GET /api/backtests/{id}/drawdown`
  - `GET /api/backtests/{id}/monthly`
  - `GET /api/backtests/{id}/ledger`
  - `GET /api/backtests/{id}/events`
  - `GET /api/backtests/{id}/decisions`
- **Step 3a - Canonical save wrapper:** Added `scripts/save_backtest.py` for Dhan backtests that writes `{id}_summary.json`, `{id}_ledger.csv`, `{id}_manifest.json`, and optional `{id}_decisions.csv` into `reports/backtests/dashboard_runs/`.
- **Step 6 - app.jsx shell routing:** Enabled Historical Explorer and Backtests tabs in `dashboard/app.jsx`, added the local-only command box (`LIVE`, `HIST`, `HIST SYMBOL`, `BT`/`BACKTESTS`, `ALRT`), and added safe placeholder shells until `dashboard/historical.jsx` and `dashboard/backtests.jsx` are delivered.

### Verification Completed

- `python -m py_compile options_backtest\dashboard_bridge.py scripts\live\api\models.py scripts\live\api\routes\historical.py scripts\live\api\routes\backtests.py`
- FastAPI `TestClient` smoke covered spot, VIX, options metadata, options OHLCV, bhavcopy, backtest list, summary, equity, drawdown, monthly, ledger, events, and decisions. Re-verified via `tests/test_dashboard_v2.py`.
- Unknown historical symbol returns HTTP 400.
- `python -m unittest discover -s tests -v` passed: 76 tests OK.

### Still Pending

- **Step 7:** Add full `dashboard/historical.jsx`, full `dashboard/backtests.jsx`, final `dashboard/index.html` script tags, and required CSS.
- **Step 8:** Browser/server verification after frontend components land, including zimaos tunnel checks.

---

## Deployment Data Placement DONE DONE - 2026-05-12

> **Status:** Complete. 992 files / 6.4 GB synced to zimaos WD storage. All symlinks live. Manifest on server. No further action needed before starting Step 2.
>
> **What was done:**
> - `scripts/sync_to_zimaos.py` ran clean: 992 uploaded, 0 errors.
> - Symlinks created in zimaos repo: `data/processed/options` -> WD, `data/processed/nse` -> WD, `data/processed/market_archive_cleaned` -> WD, `reports/backtests/options` -> WD.
> - Missing spot files (`banknifty_1min_DHAN.csv`, `indiavix_1min_DHAN.csv`) copied into repo `data/processed/spot/` - now 6/6 present.
> - `reports/backtests/dashboard_runs/` created on WD storage (empty - populated by `scripts/save_backtest.py`).
> - Inventory manifest generated and placed at `data/manifests/server_data_manifest.{json,md}` (laptop + WD + repo on zimaos). 24 datasets with probed actual date ranges.
> - `scripts/generate_data_manifest.py` added for re-generation after future syncs.

V2 Historical Explorer and Backtests must run server-first, because the laptop currently holds the heavy historical data and legacy report archive. Do not design v2 as laptop-local-only.

### Environment variables

Backend resolves roots from env vars first, then repo-relative defaults:

| Env var | Default (repo-relative) | zimaos WD path |
|---|---|---|
| `MARKET_DATA_ROOT` | `data` | `/media/WD-Storage/indian-markets-data` |
| `BACKTEST_REPORT_ROOT` | `reports/backtests` | `/media/WD-Storage/indian-markets-data/reports/backtests` |
| `LIVE_ROOT` | - | `/media/WD-Storage/indian-markets-live` (unchanged) |

On zimaos expose via symlinks:
- `data/processed` -> `/media/WD-Storage/indian-markets-data/processed`
- `reports/backtests` -> `/media/WD-Storage/indian-markets-data/reports/backtests`

Do **not** symlink `data/raw` to the WD drive - raw sources stay on the laptop (see below).

### What to sync to zimaos WD storage

These paths contain everything the v2 API actually reads. Sync these and nothing else.

**Total to sync: ~6.0 GB, 452 files + all of reports/backtests (60 MB, 541 files)**

| Laptop path | zimaos WD path | Format | Files | Size | Date range | Used by |
|---|---|---|---|---|---|---|
| `data/processed/spot/nifty50_1min_CANONICAL.csv` | `.../processed/spot/` | CSV | 1 | 58 MB | 2015-2026 | Spot: NIFTY |
| `data/processed/spot/banknifty_1min_DHAN.csv` | `.../processed/spot/` | CSV | 1 | 27 MB | 2022-2026 | Spot: BANKNIFTY |
| `data/processed/spot/finnifty_1min_DHAN.csv` | `.../processed/spot/` | CSV | 1 | 27 MB | 2022-2026 | Spot: FINNIFTY |
| `data/processed/spot/midcpnifty_1min_DHAN.csv` | `.../processed/spot/` | CSV | 1 | 23 MB | 2023-2026 | Spot: MIDCPNIFTY |
| `data/processed/spot/sensex_1min_DHAN.csv` | `.../processed/spot/` | CSV | 1 | 17 MB | 2023-2026 | Spot: SENSEX |
| `data/processed/spot/indiavix_1min_DHAN.csv` | `.../processed/spot/` | CSV | 1 | 21 MB | 2021-2026 | VIX (primary) |
| `data/processed/options/dhan/` (full subtree) | `.../processed/options/dhan/` | Parquet | 420 | 5.60 GB | 2021-2026 | Options 1-min |
| `data/processed/nse/bhavcopy/fo/` (full subtree) | `.../processed/nse/bhavcopy/fo/` | Parquet | 19 | 80 MB | 2008-2026 | Bhavcopy EOD |
| `data/processed/market_archive_cleaned/INDIA VIX_*.csv` (6 files only) | `.../processed/market_archive_cleaned/` | CSV | 6 | ~100 MB | varies | VIX (fallback) |
| `reports/backtests/options/` (full subtree) | `.../reports/backtests/options/` | CSV/MD/JSON | 541 | 60 MB | 2026-05 | Backtests legacy |

Canonical new-run artifacts (`reports/backtests/dashboard_runs/`) are written directly on zimaos from `scripts/save_backtest.py` - they never need to be synced from laptop.

Suggested rsync (run from laptop, after SSH tunnel is open):
```bash
rsync -avh --progress \
  data/processed/spot/ \
  data/processed/options/dhan/ \
  data/processed/nse/bhavcopy/fo/ \
  "data/processed/market_archive_cleaned/INDIA VIX_minute.csv" \
  "data/processed/market_archive_cleaned/INDIA VIX_day.csv" \
  "data/processed/market_archive_cleaned/INDIA VIX_5minute.csv" \
  "data/processed/market_archive_cleaned/INDIA VIX_15minute.csv" \
  "data/processed/market_archive_cleaned/INDIA VIX_30minute.csv" \
  "data/processed/market_archive_cleaned/INDIA VIX_60minute.csv" \
  reports/backtests/options/ \
  zimaos:/media/WD-Storage/indian-markets-data/
```

### What stays on the laptop only

| Path | Format | Files | Size | Reason |
|---|---|---|---|---|
| `data/raw/options/shoonya/nifty/` | CSV/ZIP | 24,068 | 7.27 GB | Engine input only - per-strike 1-min chains, feeds backtest engine, not dashboard |
| `data/raw/market_archive/` | CSV | 132 | ~1.17 GB | Raw uncleaned copy of market_archive_cleaned - superseded; not needed on server |
| `data/raw/nse/bhavcopy/` | mixed | - | - | Raw bhavcopy ZIPs/CSVs before parquet conversion - superseded |
| `data/processed/market_archive_cleaned/NIFTY *.csv` (126 files) | CSV | 126 | ~1.07 GB | 22 sector indices (NIFTY IT, NIFTY AUTO, etc.) - not exposed by v2 API |

### Data structure notes (critical for bridge implementation)

**Dhan options parquet layout:**
```
data/processed/options/dhan/
  {symbol}/          # nifty | banknifty | finnifty | midcpnifty | sensex
    {expiry_type}/   # week | month
      expiry_code_1/
        call/        # 21 files: ATM.parquet, ATMm1..ATMm10, ATMp1..ATMp10
        put/         # 21 files: same naming
```
Files are ATM-relative offsets, not absolute strikes. `ATMm3` = 3 strikes below ATM at entry; `ATMp5` = 5 strikes above ATM. The parquet itself carries `strike` (absolute) and `spot` columns per row - so absolute values are available, but the file selector is offset-based.

**Dhan parquet schema (confirmed):**
```
timestamp    datetime64[ms, Asia/Kolkata]   <- TZ-aware IST; use as index
open         float64
high         float64
low          float64
close        float64
volume       int64
oi           int64
iv           float64    (cleaned IV; use iv_clean for analysis)
strike       float64    (absolute strike price)
spot         float64    (underlying spot at that bar)
iv_raw       float64
iv_spike_flag bool
iv_zero_flag  bool
iv_clean     float64
```

Bridge read pattern:
```python
df = pd.read_parquet(path)
# index is RangeIndex by default; timestamp is a column
df = df.set_index("timestamp")
df.index = df.index.tz_convert("Asia/Kolkata")
```

**Spot CSV schema (all 6 files):** column name is `datetime` (must confirm with `pd.read_csv(path, nrows=1)`). Filter by `.dt.date` range after parse. The `_SPOT_FILES` bridge dict must use the exact filenames below:
```python
_SPOT_FILES = {
    "NIFTY":      "data/processed/spot/nifty50_1min_CANONICAL.csv",
    "BANKNIFTY":  "data/processed/spot/banknifty_1min_DHAN.csv",
    "FINNIFTY":   "data/processed/spot/finnifty_1min_DHAN.csv",
    "MIDCPNIFTY": "data/processed/spot/midcpnifty_1min_DHAN.csv",
    "SENSEX":     "data/processed/spot/sensex_1min_DHAN.csv",
}
```

**VIX fallback order (corrected filenames):**
```python
_VIX_FILES = [
    _ROOT / "data/processed/spot/indiavix_1min_DHAN.csv",                        # primary - 2021-2026
    _ROOT / "data/processed/market_archive_cleaned/INDIA VIX_minute.csv",        # fallback - broader range
    _ROOT / "data/processed/market_archive_cleaned/INDIA VIX_day.csv",           # daily-only fallback
]
```
Note: actual filename is `INDIA VIX_minute.csv` (not `_1min.csv`).

**Bhavcopy:** current dataset covers NIFTY only - files are `nifty_options_eod_{2008..2026}.parquet`. The `historical_bhavcopy(symbol)` bridge method should filter by the `SYMBOL` column within those files, not by separate per-symbol parquet files (they don't exist yet for BN/FN/MCP/SENSEX).

**NSE Bhavcopy parquet path:** `data/processed/nse/bhavcopy/fo/nifty_options_eod_YYYY.parquet` - read all yearly files for the requested date range using `pd.concat`.

### Overlap / conflict map

| Data | Primary source | Overlap / secondary | Resolution |
|---|---|---|---|
| NIFTY spot 1-min | `data/processed/spot/nifty50_1min_CANONICAL.csv` | `data/processed/market_archive_cleaned/NIFTY 50_minute.csv` | Use canonical exclusively - it merges Shoonya + Dhan; archive version is separate lineage |
| BANKNIFTY spot 1-min | `data/processed/spot/banknifty_1min_DHAN.csv` | `data/processed/market_archive_cleaned/NIFTY BANK_minute.csv` | Use spot/ file; archive has different naming and different cleaning |
| India VIX 1-min | `data/processed/spot/indiavix_1min_DHAN.csv` | `data/processed/market_archive_cleaned/INDIA VIX_minute.csv` | Use Dhan file first (2021+); fall back to archive for pre-2021 queries |
| Options 1-min | `data/processed/options/dhan/` (all 5 symbols) | `data/raw/options/shoonya/nifty/` (NIFTY only, 2024+) | Dashboard uses Dhan parquet only. Shoonya raw CSVs feed the backtest engine, not the API |
| market_archive_cleaned | 22 indices x 6 tf = 132 files | `data/raw/market_archive/` (raw counterpart, same 132 files) | Only the 6 VIX files from the cleaned version go to server. Raw version never needed |

### `historical_options_metadata` - corrected design

Because Dhan files are ATM-offset based (not absolute-strike based), the metadata response must reflect this:

```python
# Return structure
{
    "symbol": "NIFTY",
    "date_min": "2021-01-01",
    "date_max": "2026-05-07",
    "expiry_types": ["week", "month"],
    "atm_offsets": ["ATMm10", "ATMm9", ..., "ATM", ..., "ATMp9", "ATMp10"],  # 21 values
    "opt_types": ["call", "put"],
}
```

The Historical Explorer UI should expose `atm_offsets` in place of `strikes` when dataset = OPTIONS 1-MIN. The selected offset maps directly to the leaf parquet file:
`data/processed/options/dhan/{symbol}/{expiry_type}/expiry_code_1/{opt_type}/{atm_offset}.parquet`

### Migration rule

One-way initial sync from laptop to zimaos WD storage. Verify file counts and total bytes after rsync. Switch `MARKET_DATA_ROOT` / `BACKTEST_REPORT_ROOT` env vars on zimaos. Do not delete the laptop copy until v2 has been verified end-to-end against server data.

---

## Critical Files

| File | Change |
|---|---|
| `options_backtest/dashboard_bridge.py` | DONE 2026-05-12: historical readers, backtest readers, VIX fallback, env roots, bhavcopy schema, legacy adapter |
| `scripts/live/api/models.py` | DONE 2026-05-12: extended backtest/OHLCV models and added historical/backtest drilldown models |
| `scripts/live/api/routes/historical.py` | DONE 2026-05-12: real spot, VIX, metadata, options, and bhavcopy endpoints |
| `scripts/live/api/routes/backtests.py` | DONE 2026-05-12: real list, summary, equity, drawdown, monthly, ledger, events, and decisions endpoints |
| `scripts/save_backtest.py` | DONE 2026-05-12: canonical dashboard-run writer for summary, ledger, manifest, and optional decisions |
| `dashboard/app.jsx` | DONE 2026-05-12: tabs 2 & 3 enabled with local command routing and placeholder shells |
| `dashboard/styles.css` | DONE 2026-05-12: command input + placeholder shell styles |
| `dashboard/historical.jsx` | PENDING: NEW - Tab 2 components |
| `dashboard/backtests.jsx` | PENDING: NEW - Tab 3 components |
| `dashboard/index.html` | PENDING: add 2 new `<script>` tags when the full components are created |

---

## Step 1 - Parquet schema probe (prerequisite) DONE DONE - 2026-05-12

Before writing bridge code, run once to confirm column names and file naming convention:

```python
import pandas as pd
from pathlib import Path
p = next(Path("data/processed/options/dhan/nifty").glob("*.parquet"))
print(p.name)
df = pd.read_parquet(p, engine="pyarrow")
print(df.dtypes, df.head(2))
```

Completed probe result: Dhan options parquet files are nested under `data/processed/options/dhan/{symbol}/{expiry_type}/expiry_code_1/{call|put}/{ATM offset}.parquet`. The timestamp column is `timestamp`, not `datetime`, and is timezone-aware IST. Confirmed columns are `timestamp`, `open`, `high`, `low`, `close`, `volume`, `oi`, `iv`, `strike`, `spot`, `iv_raw`, `iv_spike_flag`, `iv_zero_flag`, and `iv_clean`.

---

## Step 2 - Bridge (`options_backtest/dashboard_bridge.py`) DONE DONE - 2026-05-12

### 2a. Extend `_SPOT_FILES` (lines 31-36)
```python
"BANKNIFTY": _REPO_ROOT / "data" / "processed" / "spot" / "banknifty_1min_DHAN.csv",
```

Add ordered VIX candidates instead of one hardcoded path:
```python
_VIX_FILES = [
    _REPO_ROOT / "data" / "processed" / "spot" / "indiavix_1min_DHAN.csv",
    _REPO_ROOT / "data" / "processed" / "market_archive_cleaned" / "INDIA VIX_minute.csv",
    _REPO_ROOT / "data" / "processed" / "market_archive_cleaned" / "INDIA VIX_day.csv",
]
```
`historical_vix()` must use the first existing candidate and include the chosen path in response metadata/warnings.

### 2b. Add backtest roots (after `_SPOT_FILES`)
```python
_BACKTEST_ROOT = _REPO_ROOT / "reports" / "backtests" / "dashboard_runs"
_LEGACY_BACKTEST_ROOTS = [
    _REPO_ROOT / "reports" / "backtests" / "options" / "focused",
    _REPO_ROOT / "reports" / "backtests" / "options" / "risk_management",
    _REPO_ROOT / "reports" / "backtests" / "options" / "research_validation",
    _REPO_ROOT / "reports" / "backtests" / "options" / "monitoring",
    _REPO_ROOT / "reports" / "backtests" / "options" / "legacy",
]
```

### 2c. `historical_spot(symbol, start, end, tf_minutes=1) -> pd.DataFrame`
Replace stub at line 731.
- Validate symbol in `_SPOT_FILES`; return empty DF if missing
- Read CSV: `pd.read_csv(path, parse_dates=["datetime"], index_col="datetime")`
- Filter: `start <= index.date <= end`
- Resample if `tf_minutes > 1`: `.resample(f"{tf_minutes}T").agg({"open":"first","high":"max","low":"min","close":"last","volume":"sum"}).dropna()`
- Cap at 20,000 rows; set `_truncated=True` attribute on returned DF if capped
- TTL cache key: `hist_spot_{symbol}_{start}_{end}_{tf_minutes}`, TTL = `_TTL_HIST` (60 s)
- Return columns: `ts` (ISO-8601 IST str), `open`, `high`, `low`, `close`, `volume`

### 2d. `historical_vix(start, end, tf_minutes=1) -> pd.DataFrame`
Replace stub at line 749. Same logic, symbol hardcoded to `"VIX"`. Use `_VIX_FILES` in order; `close` is the only meaningful series, so set open/high/low = close for display consistency. Return a warning when the selected source is daily rather than 1-min.

### 2e. `historical_options_metadata(symbol) -> dict` - NEW
- Scan `_REPO_ROOT / "data" / "processed" / "options" / "dhan" / symbol.lower()` recursively for `*.parquet`
- Determine `expiry_types` from the second path component (`week`, `month`)
- Derive `atm_offsets` from the leaf filenames (stems: `ATM`, `ATMm1`..`ATMm10`, `ATMp1`..`ATMp10`)
- Read one representative parquet cheaply to get `date_min`/`date_max` from the `timestamp` column
- Return `{symbol, date_min, date_max, expiry_types: [str], atm_offsets: [str sorted], opt_types: ["call","put"]}`
- TTL: 300 s

### 2f. `historical_options(symbol, expiry_type, atm_offset, opt_type, start, end) -> pd.DataFrame`
Replace stub at line 734.
- Path: `data/processed/options/dhan/{symbol.lower()}/{expiry_type}/expiry_code_1/{opt_type}/{atm_offset}.parquet`
- Read parquet, set `timestamp` as index, filter by date range
- Return OHLCV + `oi` + `iv_clean` + `strike` + `spot` columns. Cap 20,000 rows.

### 2g. `historical_bhavcopy(symbol, start, end) -> pd.DataFrame` - NEW
- Read `_REPO_ROOT / "data" / "processed" / "nse" / "bhavcopy" / "fo"` parquet
- Filter `SYMBOL == symbol` and date range
- Return: `ts, expiry, strike, option_type, open, high, low, close, settle_price, oi, volume`
- TTL: `_TTL_HIST`

### 2h. `backtest_list() -> list[dict]`
Replace `backtest_summaries` stub at line 754. Signature change: no `root` argument (uses `_BACKTEST_ROOT`).
- Scan canonical `_BACKTEST_ROOT` for `*_summary.json`; parse each
- Also scan `_LEGACY_BACKTEST_ROOTS` for old CSV/MD artifacts via the legacy adapter in Step 3b
- Sort by `created_at`/file mtime descending
- Return `[]` if directory missing - not an error
- TTL: 30 s

### 2i. `backtest_summary(backtest_id) -> dict`
- Canonical: read `{_BACKTEST_ROOT}/{backtest_id}_summary.json`
- Legacy: return adapter-derived summary from CSV/MD where possible
- Return `{}` if missing

### 2j. `backtest_ledger(backtest_id) -> pd.DataFrame`
Replace stub at line 757.
- Canonical: read `{_BACKTEST_ROOT}/{backtest_id}_ledger.csv`, parse date columns
- Legacy trade-ledger CSV: normalize old column variants into the v2 ledger schema
- Legacy summary-only CSV/MD: return empty DataFrame and mark `has_ledger=false`

### 2k. `backtest_equity_curve(backtest_id) -> pd.DataFrame`
Replace stub at line 760 (was `-> list[EquityPoint]`, change to `-> pd.DataFrame`).
- Load ledger, sort by `exit_date`
- `equity = 1_000_000 + cumsum(net_pnl)`
- `normalised = equity / equity.iloc[0]`
- Return columns: `date, equity, normalised`

### 2l. `backtest_monthly_returns(backtest_id) -> pd.DataFrame` - NEW
- Load ledger; group by `(exit_date.year, exit_date.month)`; sum `net_pnl`
- Add `return_pct = net_pnl_month / 1_000_000 * 100`
- Return long-form: `year, month, net_pnl, return_pct`

### 2m. `backtest_drawdown(backtest_id) -> pd.DataFrame` - NEW
- From equity curve: `dd_pct = (equity - equity.cummax()) / equity.cummax() * 100`
- Return: `date, drawdown_pct`

### 2n. `backtest_events(backtest_id) -> list[dict]` - NEW
- Build chart markers from trade ledgers: entry, exit, stop_loss, target, time_exit
- For live-session overlays, also read alert/session events when the backtest_id maps to a paper session artifact
- Return: `ts, symbol, event_type, severity, label, details`
- Legacy summary-only artifacts return `[]` with a warning

### 2o. `backtest_decisions(backtest_id) -> pd.DataFrame` - NEW
- Canonical new runs may save `{id}_decisions.csv`; legacy runs usually do not have it
- Return columns: `ts, symbol, decision, reason, vix, dte, expiry, eligible, selected`
- For legacy artifacts without decision logs, return empty DataFrame with `has_decisions=false`

---

## Step 3 - Backtest compatibility + save convention

> **Progress:** Step 3a canonical save wrapper and Step 3b legacy adapter are implemented. Legacy artifacts remain read-only.

### 3a. Canonical new run directory

Directory: `reports/backtests/dashboard_runs/` (add to `.gitignore`).

File pairs per run:
- `{id}_summary.json` - `reports.summary()` dict + `{id, name, symbol, strategy, created_at}`
- `{id}_ledger.csv` - `reports.trade_ledger()` DataFrame
- `{id}_manifest.json` - reproducibility metadata: strategy config, symbol set, lot scale, engine version/git commit if available, cost model version, data manifest path/hash if available, bad_expiries, run command, created_at
- `{id}_decisions.csv` - optional no-trade/skip diagnostics: `ts, symbol, decision, reason, vix, dte, expiry, eligible, selected`

Added `scripts/save_backtest.py`: thin wrapper that loads a config JSON, runs the Dhan engine, writes the canonical files to `reports/backtests/dashboard_runs/`, and refuses to overwrite existing artifacts unless `--overwrite` is passed. One-time CLI tool, not a service.

Minimal config shape:
```json
{
  "id": "wing6_nifty_smoke",
  "name": "Wing-6 NIFTY Smoke",
  "strategy": "iron-condor",
  "strategy_params": {"short_call_offset": 2, "long_call_offset": 8, "short_put_offset": -2, "long_put_offset": -8, "lots": 1},
  "symbol": "NIFTY",
  "expiry_type": "week",
  "from_date": "2025-01-01",
  "to_date": "2025-01-31",
  "entry_time": "09:20",
  "exit_time": "15:20",
  "stop_loss_pct": null,
  "target_profit_pct": null,
  "dhan_root": "data/processed/options/dhan",
  "spot_path": "data/processed/spot/nifty50_1min_CANONICAL.csv",
  "data_manifest_path": "data/manifests/server_data_manifest.json"
}
```

Run:
```bash
python scripts/save_backtest.py configs/dashboard_runs/wing6_nifty_smoke.json
```

### 3b. Read-only legacy adapter DONE DONE - 2026-05-12

Do not move, rewrite, or mutate old backtest artifacts. Add a bridge-side adapter that indexes existing reports and exposes them through the same `/api/backtests` endpoints.

Supported legacy artifact families:
- Trade-ledger CSVs under `reports/backtests/options/focused/`, `risk_management/`, `research_validation/`, `monitoring/`, and `legacy/**`
- Combined portfolio ledgers such as `*_focused_contributors_combined.csv`
- Summary-only comparison CSVs such as `*_portfolio_comparison.csv`
- Human-readable summary MD files such as `*_summary.md` and risk-management experiment reports

Legacy ID rule:
- Use a stable ID derived from the repo-relative path: `legacy:` + lowercase path with path separators replaced by `__` and extension removed
- Include `source_path`, `source_kind`, `compat_version`, and `legacy=true` in every summary
- Never infer a stronger claim than the artifact supports; missing fields stay null

Legacy ledger normalization:
- Accept old column variants: `entry_time`/`exit_time` as full datetimes, or split `entry_date` + `entry_time`, `exit_date` + `exit_time`
- Map `dte_at_entry` -> `dte`, `vix_entry` -> `vix`, `vix_bucket` -> `vix_bucket`
- Add `symbol` from a CSV column when present; otherwise infer from filename tokens (`nifty`, `banknifty`, `finnifty`, `midcpnifty`, `sensex`) and set `symbol_inferred=true`
- Preserve raw leg strings as `entry_legs_raw`/`exit_legs_raw` or `legs_raw`; do not try to parse old leg strings into structured fills in v2
- Drop final `TOTAL` rows from ledger views but use them as a summary hint when present
- If `equity` exists, use it for equity/drawdown; otherwise compute from `initial_capital=1_000_000 + cumsum(net_pnl)`

Legacy summary-only handling:
- `*_portfolio_comparison.csv` rows become listable backtest variants with `has_ledger=false`, `has_equity=false`, `source_kind="legacy_summary_table"`
- These rows appear in the metrics table but ledger/equity/drawdown/monthly endpoints return empty payloads with warnings
- Frontend must display a small `SUMMARY ONLY` badge and disable drilldown panels for these rows

---

## Step 4 - Models (`scripts/live/api/models.py`) DONE DONE - 2026-05-12

### Extend `BacktestSummaryModel` (lines 230-238)
```python
name: str = ""
symbol: str = ""
date_from: str = ""
date_to: str = ""
cagr: float | None = None
sortino: float | None = None
calmar: float | None = None
win_rate: float | None = None
profit_factor: float | None = None
t_stat: float | None = None
created_at: str = ""
source_path: str = ""
source_kind: str = "canonical"
legacy: bool = False
compat_version: str = "v2"
has_ledger: bool = True
has_equity: bool = True
has_decisions: bool = False
warnings: list[str] = Field(default_factory=list)
```

### Extend `OHLCVResponse` (line 257)
```python
stats: dict[str, float] = Field(default_factory=dict)
# keys: bars_count, mean_close, min_close, max_close, std_close, total_volume, truncated(0|1)
source: str = ""
generated_at: str = ""
row_count: int = 0
cache_age_s: float | None = None
data_age_s: float | None = None
warnings: list[str] = Field(default_factory=list)
```

### New models (append after line 261)
```python
class HistoricalMetadata(BaseModel):
    symbol: str
    date_min: str
    date_max: str
    expiries: list[str]
    strikes: list[int]

class DrawdownPoint(BaseModel):
    date: str
    drawdown_pct: float

class MonthlyReturn(BaseModel):
    year: int
    month: int
    net_pnl: float
    return_pct: float

class TradeLedgerRow(BaseModel):
    entry_date: str
    exit_date: str
    symbol: str
    expiry: str
    dte: int | None = None
    vix: float | None = None
    vix_bucket: str | None = None
    entry_credit: float
    gross_pnl: float
    net_pnl: float
    exit_reason: str
    entry_legs_raw: str | None = None
    exit_legs_raw: str | None = None
    legacy: bool = False
    symbol_inferred: bool = False

class LedgerResponse(BaseModel):
    total: int
    page: int
    size: int
    rows: list[TradeLedgerRow]
    source_kind: str = "canonical"
    warnings: list[str] = Field(default_factory=list)

class EventOverlayPoint(BaseModel):
    ts: str
    symbol: str = ""
    event_type: str
    severity: str = "info"
    label: str = ""
    details: dict[str, str | float | int | None] = Field(default_factory=dict)

class DecisionLogRow(BaseModel):
    ts: str
    symbol: str
    decision: str
    reason: str
    vix: float | None = None
    dte: int | None = None
    expiry: str | None = None
    eligible: bool | None = None
    selected: bool | None = None

class DecisionLogResponse(BaseModel):
    total: int
    rows: list[DecisionLogRow]
    has_decisions: bool = False
    warnings: list[str] = Field(default_factory=list)
```

---

## Step 5 - Routes DONE DONE - 2026-05-12

### `routes/historical.py`

```
GET /api/historical/spot/{symbol}
    Query: start (YYYY-MM-DD), end (YYYY-MM-DD), tf (int minutes, default 1)
    -> OHLCVResponse (with stats)

GET /api/historical/vix
    Query: start, end, tf
    -> OHLCVResponse

GET /api/historical/metadata/{symbol}     [NEW]
    -> HistoricalMetadata

GET /api/historical/options/{symbol}      [replace old path-param form]
    Query: expiry, strike, opt_type (CE|PE), start, end
    -> OHLCVResponse (bars include oi field)

GET /api/historical/bhavcopy/{symbol}     [NEW - replaces order-book stub]
    Query: start, end
    -> OHLCVResponse
```

Each handler: parse + validate dates -> call bridge via `run_in_executor` -> serialize to Pydantic. Return HTTP 400 for unknown symbol; empty bars (not 404) for valid symbol with no data in range.

### `routes/backtests.py`

```
GET /api/backtests
    -> BacktestListResponse

GET /api/backtests/{id}
    -> BacktestSummaryModel  (full detail; replace V2PendingResponse)

GET /api/backtests/{id}/equity-curve
    -> OHLCVResponse (close = equity; stats.normalised_last added)

GET /api/backtests/{id}/drawdown         [NEW]
    -> list[DrawdownPoint]
    Empty list when `has_equity=false`; response/path must not fabricate drawdown from summary-only artifacts

GET /api/backtests/{id}/monthly          [NEW]
    -> list[MonthlyReturn]
    Empty list when `has_ledger=false`

GET /api/backtests/{id}/ledger           [NEW]
    Query: page (default 0), size (default 25, max 500), symbol, exit_reason
    -> LedgerResponse
    For legacy summary-only rows: `{total:0, rows:[], source_kind:"legacy_summary_table", warnings:[...]}`

GET /api/backtests/{id}/events           [NEW]
    -> list[EventOverlayPoint]
    Derived from ledger rows and, for paper-session artifacts, alert/session logs

GET /api/backtests/{id}/decisions        [NEW]
    -> DecisionLogResponse
    Empty with `has_decisions=false` when no `{id}_decisions.csv` or legacy equivalent exists
```

---

## Step 6 - app.jsx tab wiring (lines 97-98) DONE DONE - 2026-05-12

Added a compact command bar placeholder in the global topbar before enabling v2 tabs.

Supported v2 shell commands:
- `LIVE` -> activeTab `live`
- `HIST` or `HIST NIFTY` -> activeTab `historical`, optionally set symbol
- `BT` or `BACKTESTS` -> activeTab `backtests`
- `ALRT` -> activeTab `live` and focus/scroll alert timeline

Implementation constraint: this is only local UI routing for v2. Do not add server-side command execution, broker actions, or mutable API calls.

Disabled tabs were replaced with active local routing:
```jsx
// before
<div className="tab disabled">Historical Explorer<span className="pill">v2</span></div>
<div className="tab disabled">Backtests<span className="pill">v2</span></div>

// after
<div className={`tab ${activeTab==='historical'?'active':''}`} onClick={() => onTab('historical')}>
  Historical Explorer
</div>
<div className={`tab ${activeTab==='backtests'?'active':''}`} onClick={() => onTab('backtests')}>
  Backtests
</div>
```

App render now uses conditional content areas:
```jsx
{activeTab === 'historical' && <HistoricalTab />}
{activeTab === 'backtests'  && <BacktestsTab />}
```

`dashboard/app.jsx` safely resolves `window.HistoricalTab` and `window.BacktestsTab` when those files exist. Until the full frontend components land, placeholder shells render instead of throwing.

`index.html`: add two `<script type="text/babel">` tags for `historical.jsx` and `backtests.jsx`, after the existing panel scripts and before `app.jsx`, when Step 7 components are created.

---

## Step 7 - Frontend Design Prompt (handoff)

```text
WING-6 DASHBOARD - V2 TABS: HISTORICAL EXPLORER + BACKTESTS
Frontend handoff prompt

CONTEXT
You are adding two tabs to an existing React 18 + Babel no-build dashboard.
Tab 1, Live Monitor, already ships. Match its visual language exactly: pure black background, dense terminal-style data layout, JetBrains Mono for numbers/labels, thin borders, no decorative UI.

CURRENT IMPLEMENTATION STATE
- Backend v2 routes are real and mounted in FastAPI.
- Server data is WD-backed and resolved through MARKET_DATA_ROOT and BACKTEST_REPORT_ROOT. Do not hardcode laptop-only paths.
- scripts/save_backtest.py writes canonical dashboard runs into reports/backtests/dashboard_runs/.
- dashboard/app.jsx already enables Historical Explorer and Backtests tabs.
- dashboard/app.jsx already has local-only command routing: LIVE, HIST, HIST SYMBOL, BT/BACKTESTS, ALRT.
- Do not add server-side command execution, broker actions, order placement, POST/PUT/DELETE calls, or mutable API calls.
- app.jsx resolves window.HistoricalTab and window.BacktestsTab when the new files export them. Until then it shows placeholder shells.

FILES TO CREATE OR TOUCH
- Create dashboard/historical.jsx.
- Create dashboard/backtests.jsx.
- Update dashboard/index.html so these scripts load after panels.jsx and before app.jsx:
  <script type="text/babel" src="historical.jsx"></script>
  <script type="text/babel" src="backtests.jsx"></script>
- Append only missing styles to dashboard/styles.css. Keep existing command-shell styles.

EXPORT CONTRACT
In historical.jsx:
  Object.assign(window, { HistoricalTab });

In backtests.jsx:
  Object.assign(window, { BacktestsTab });

STYLE RULES
Use the existing CSS variables from styles.css. Do not redeclare them.
- Background: var(--bg), panels: var(--panel), active rows: var(--panel-2), inputs: var(--panel-3).
- Borders: 1px solid var(--border) or var(--border-strong).
- Text: var(--text), var(--text-2), var(--text-3), var(--text-4).
- Semantic colors only: green positive/ok, red negative/alert, amber warning/highlight, cyan accent/link, violet secondary accent.
- No rounded cards except tiny badges already used by the app.
- No gradients, shadows, illustrations, hero sections, marketing copy, or decorative filler.
- Use dense data surfaces: panels, tables, compact controls, charts, badges.
- Controls must be flat, bordered, monospace, uppercase, no border radius.
- Empty states should be small centered text, not large explanatory screens.

EXISTING GLOBALS
From window.WingData:
- fmtINR(v, {dp, noSign})
- fmtNum(v, dp)
- fmtPct(v, dp)
- ist(ts)
- aggregateBars(bars, mins)

From dashboard/components.jsx:
- Icon(name, size, color)
- Sparkline(data, w, h, color)
- Meter(value, max, warnAt, critAt)

Charts:
- TradingView Lightweight Charts v4 is already loaded on the page.
- Use dark chart styling: background #0a0a0a, text #6e6e6e, grid rgba(255,255,255,0.04), scale borders rgba(255,255,255,0.06).

TAB 2: HISTORICAL EXPLORER (dashboard/historical.jsx)

Layout:
- Full-width shell, no sidebar.
- Top control bar: sticky below global topbar, height 48px, background var(--bg), bottom border, compact controls.
- Chart panel: fixed height around 560px.
- Stats row: 5 cells using the same strip-cell visual pattern as the Live Monitor header.
- Data table panel: below stats, max-height around 400px, scrollable.

Controls:
- Dataset select: SPOT, INDIA VIX, OPTIONS 1-MIN, BHAVCOPY EOD.
- Symbol select: NIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, BANKNIFTY. Hide for INDIA VIX.
- Start date and end date inputs.
- Timeframe select: 1m, 5m, 15m, 1H, 1D. Hide for BHAVCOPY EOD.
- For OPTIONS 1-MIN only:
  - Expiry type select from metadata.expiry_types: week/month.
  - ATM offset select from metadata.atm_offsets: ATMm10 ... ATM ... ATMp10.
  - Type select: CE/PE.
- LOAD button and CLEAR button.
- Show a TRUNCATED badge when response.truncated is true.

Important options-data rule:
Dhan options files are ATM-offset based. The UI must label this as ATM OFFSET, not STRIKE. The loaded bars include absolute strike and spot fields, but the request selector is the offset leaf file.

Historical API calls:
- GET /api/historical/spot/{symbol}?start=&end=&tf={min}
- GET /api/historical/vix?start=&end=&tf={min}
- GET /api/historical/options/{symbol}?expiry={expiry_type}&strike={atm_offset}&opt_type={CE|PE}&start=&end=
- GET /api/historical/bhavcopy/{symbol}?start=&end=
- GET /api/historical/metadata/{symbol} on symbol change for options metadata

Historical response contract:
OHLCVResponse includes:
- symbol
- bars
- truncated
- stats
- source
- generated_at
- row_count
- cache_age_s
- data_age_s
- warnings

Bars may include:
- Standard OHLCV: ts, open, high, low, close, volume
- Options: oi, iv_clean, strike, spot
- Bhavcopy: expiry, option_type, settle_price, oi, volume

Chart requirements:
- Candles for Spot and Options in CANDLES mode.
- Line series for VIX and LINE mode.
- Optional OI line for options, using violet.
- Optional volume histogram using cyan-soft.
- Use a compact custom tooltip with OHLCV fields.
- Empty state: SELECT A DATASET AND CLICK LOAD.

Stats row:
- PERIOD: selected start to end, subtext trading-day count if available.
- BARS: response row_count, subtext selected timeframe.
- MEAN CLOSE: stats.mean_close, subtext stats.std_close.
- RANGE H-L: stats.max_close and stats.min_close or computed high/low.
- VOLUME TOTAL: stats.total_volume.

Data table:
- Spot/VIX columns: TIME, OPEN, HIGH, LOW, CLOSE, VOLUME.
- Options columns: TIME, OPEN, HIGH, LOW, CLOSE, VOLUME, OI, IV, STRIKE, SPOT.
- Bhavcopy columns: DATE, EXPIRY, STRIKE, TYPE, OPEN, HIGH, LOW, CLOSE, SETTLE, OI, VOLUME.
- Render first 500 rows and show a small sentinel row that says the full dataset is available through export.
- Add client-side CSV export from loaded bars.

TAB 3: BACKTESTS (dashboard/backtests.jsx)

Layout:
- Horizontal split: fixed 260px left sidebar plus flexible main area.
- Sidebar is sticky below topbar, full viewport height, overflow-y auto.
- Main area is scrollable, padding 12px, vertical stack of panels.

Backtest API calls:
- GET /api/backtests
- GET /api/backtests/{id}
- GET /api/backtests/{id}/equity-curve
- GET /api/backtests/{id}/drawdown
- GET /api/backtests/{id}/monthly
- GET /api/backtests/{id}/ledger?page=&size=&symbol=&exit_reason=
- GET /api/backtests/{id}/events
- GET /api/backtests/{id}/decisions

Backtest list behavior:
- Sidebar lists canonical runs and read-only legacy rows.
- Rows can be checked for comparison overlays.
- Clicking a row focuses it for detail panels.
- Show LEGACY badge when row.legacy is true.
- Show SUMMARY ONLY badge when has_ledger=false and has_equity=false.
- Summary-only rows can appear in the metrics table but must disable chart, monthly, ledger, and decision drilldowns.

Metrics table:
Columns:
NAME, SYMBOL, PERIOD, TRADES, NET PNL, CAGR, SHARPE, SORTINO, CALMAR, MAX DD%, WIN%, PF, T-STAT.

Color rules:
- NET PNL and CAGR: green if positive, red if negative.
- SHARPE/SORTINO/CALMAR: green >= 2, amber 1 to 2, red < 1.
- MAX DD%: green if abs(dd) < 2, amber 2 to 5, red > 5.
- WIN%: green >= 60, amber 50 to 60, red < 50.
- T-STAT: green >= 2, otherwise text-2.
- PF: green >= 1.5, otherwise text-2.

Charts row:
- Equity curves panel: one line per checked backtest. Support absolute and normalised modes.
- Drawdown panel: focused backtest only. Show empty/disabled state for summary-only rows.
- Overlay event markers when /events returns rows.

Analytics row:
- Monthly returns heatmap for focused backtest.
- Trade PNL distribution from focused ledger rows.
- Exit reason breakdown from focused ledger rows.
- If has_decisions=true and decisions endpoint returns rows, expose a compact DECISIONS action that opens a drawer.

Ledger panel:
Columns:
#, ENTRY DATE, EXIT DATE, SYMBOL, EXPIRY, DTE, VIX, VIX BUCKET, ENTRY CREDIT, GROSS PNL, NET PNL, EXIT REASON.

Ledger behavior:
- Filter by symbol and exit_reason.
- Paginate with page and size query params.
- Disable filters/export and show SUMMARY ONLY - NO TRADE LEDGER SAVED when has_ledger=false.
- Use green/red left border for profitable/loss rows.

Decision drawer:
Columns:
TS, SYMBOL, DECISION, REASON, VIX, DTE, EXPIRY, ELIGIBLE, SELECTED.
Purpose: explain trade/skip decisions without mixing skipped signals into executed trade ledgers.

CSS TO ADD ONLY IF MISSING
.hist-shell { display: flex; flex-direction: column; min-height: calc(100vh - 48px); }
.hist-chart-panel { flex-shrink: 0; }
.hist-chart { height: 480px; }
.hist-stats { display: grid; grid-template-columns: repeat(5, 1fr); border-bottom: 1px solid var(--border); background: var(--bg); }
.ctrl-select, .ctrl-date { background: var(--panel-3); border: 1px solid var(--border-strong); color: var(--text); font-family: 'JetBrains Mono', monospace; font-size: 11px; padding: 0 8px; height: 28px; outline: none; border-radius: 0; cursor: pointer; letter-spacing: 0.04em; appearance: none; }
.ctrl-select:hover, .ctrl-date:hover { border-color: rgba(255,255,255,0.18); }
.ctrl-select:focus, .ctrl-date:focus { border-color: var(--cyan); }
.ctrl-btn { background: transparent; border: 1px solid var(--border-strong); color: var(--text-2); font-family: 'JetBrains Mono', monospace; font-size: 10.5px; padding: 0 14px; height: 28px; cursor: pointer; letter-spacing: 0.06em; text-transform: uppercase; border-radius: 0; }
.ctrl-btn:hover { color: var(--text); border-color: rgba(255,255,255,0.18); }
.ctrl-btn:disabled { opacity: 0.4; cursor: not-allowed; }
.ctrl-btn.primary { color: var(--text); }
.seg-ctrl { display: flex; }
.seg-ctrl button { background: transparent; border: 1px solid var(--border-strong); color: var(--text-3); font-family: 'JetBrains Mono', monospace; font-size: 10px; padding: 2px 10px; height: 22px; cursor: pointer; letter-spacing: 0.05em; text-transform: uppercase; border-radius: 0; }
.seg-ctrl button + button { border-left: none; }
.seg-ctrl button.active { background: var(--panel-2); color: var(--text); border-color: var(--border-strong); }
.bt-wrap { display: flex; min-height: calc(100vh - 48px); }
.bt-sidebar { width: 260px; flex-shrink: 0; border-right: 1px solid var(--border); display: flex; flex-direction: column; background: var(--bg); position: sticky; top: 48px; height: calc(100vh - 48px); overflow-y: auto; }
.bt-main { flex: 1; overflow-y: auto; padding: 12px; display: flex; flex-direction: column; gap: 12px; }
.bt-item { padding: 9px 14px; border-bottom: 1px solid var(--border); cursor: pointer; display: flex; flex-direction: column; gap: 3px; position: relative; }
.bt-item:hover { background: var(--row-hover); }
.bt-item.focused { background: var(--panel-2); border-left: 2px solid var(--cyan); padding-left: 12px; }
.bt-item input[type=checkbox] { accent-color: var(--cyan); }
.bt-charts { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }
.bt-analytics { display: grid; grid-template-columns: 40% 1fr 1fr; gap: 12px; }
.bt-heatmap .hm-grid { display: grid; grid-template-columns: 36px repeat(12, 1fr) 42px; gap: 1px; background: var(--border); }
.bt-heatmap .hm-cell { height: 28px; display: flex; align-items: center; justify-content: center; font-family: 'JetBrains Mono', monospace; font-size: 10px; background: var(--panel); cursor: default; }
.bt-heatmap .hm-header { font-size: 9px; text-transform: uppercase; color: var(--text-3); letter-spacing: 0.06em; background: var(--panel); }
.bt-heatmap .hm-year-col { border-left: 1px solid var(--border-strong); font-weight: 600; }
.bt-dist .dist-svg { display: block; width: 100%; }
.page-btn { background: transparent; border: 1px solid var(--border-strong); color: var(--text-3); font-family: 'JetBrains Mono', monospace; font-size: 10px; padding: 3px 10px; cursor: pointer; border-radius: 0; }
.page-btn:hover { color: var(--text); }
.page-btn:disabled { opacity: 0.3; cursor: not-allowed; }
.panel-footer { display: flex; align-items: center; justify-content: space-between; padding: 8px 14px; border-top: 1px solid var(--border); font-size: 11px; min-height: 36px; }

END OF HANDOFF PROMPT
```
---

## Step 8 - Verification

1. **Bridge probe**: Run the parquet schema probe from Step 1 first.

2. **Bridge smoke test**:
   ```python
   python -c "
   from datetime import date
   from options_backtest.dashboard_bridge import DashboardBridge
   b = DashboardBridge('data/live')
   df = b.historical_spot('NIFTY', date(2025,1,2), date(2025,1,31), 15)
   print('spot shape:', df.shape)
   meta = b.historical_options_metadata('NIFTY')
   print('metadata keys:', list(meta.keys()))
   "
   ```

3. **API endpoints**:
   ```bash
   curl "http://localhost:9000/api/historical/spot/NIFTY?start=2025-01-02&end=2025-01-10&tf=5"
   curl "http://localhost:9000/api/historical/metadata/NIFTY"
   curl "http://localhost:9000/api/backtests"
   ```

4. **Backtest tab with canonical data**: Copy a known canonical result pair into `reports/backtests/dashboard_runs/` and re-run `GET /api/backtests` - should return one item with full summary fields.

5. **Legacy compatibility**: Without copying or rewriting old files, `GET /api/backtests` should list existing artifacts from `reports/backtests/options/focused/`, `risk_management/`, and `legacy/**`. Pick one legacy trade-ledger CSV and verify ledger/equity/monthly/drawdown endpoints return normalized data. Pick one `*_portfolio_comparison.csv` row and verify it appears as `SUMMARY ONLY` with empty drilldown responses and warnings.

6. **Frontend tab switching**: Open dashboard, click "Historical Explorer" - control bar loads. Select NIFTY Spot, set dates, click LOAD -> chart + stats + table populate. Click "Backtests" -> sidebar shows canonical runs plus read-only legacy rows, with `LEGACY` / `SUMMARY ONLY` badges where applicable.

7. **Regression**: `python -m unittest discover -s tests -v` - all 76 tests must pass (bridge contract unchanged).

8. **Contract tests**: Add `unittest` coverage for v2 contracts before frontend polish:
   - OHLCV responses include `source`, `generated_at`, `row_count`, `cache_age_s`, `data_age_s`, `stats`, and warnings
   - VIX source fallback chooses the first existing `_VIX_FILES` candidate and warns on daily fallback
   - Unknown symbols return HTTP 400; valid symbols with no rows return empty bars
   - Canonical `{id}_summary.json` + `{id}_ledger.csv` + `{id}_manifest.json` load together
   - Legacy ledger CSVs normalize old date/time and `dte_at_entry`/`vix_entry` columns
   - Legacy summary-only comparison rows set `has_ledger=false`, `has_equity=false`, and empty drilldown endpoints
   - Ledger pagination respects `page`, `size`, `symbol`, and `exit_reason`
   - Event and decision endpoints return empty-but-valid payloads when unsupported by a legacy artifact


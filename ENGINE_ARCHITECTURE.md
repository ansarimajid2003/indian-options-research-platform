# Options Backtest Engine — Architecture Reference

> Single-source-of-truth for the engine. Read this file instead of individual modules.
> Updated: May 2026. Reflects multi-index support (BANKNIFTY/FINNIFTY/MIDCPNIFTY/SENSEX), monthly expiry fallback, min_dte config, min_leg_premium on ShortStrangle, weekly t-stat, and the bars_for() monthly DTE fix.

> **Live infrastructure** (paper engine, live resolver, depth cache, dashboard bridge, health monitor) is documented in `docs/design/wing6_live_deployment_reference.md`. This file covers backtest engine internals only.

---

## Module Inventory

```
options_backtest/
  schemas.py          Core data structures (Contract, Leg, Fill, Trade, BacktestConfig, BacktestResult)
  calendar.py         NSE/BSE trading calendar, instrument metadata, lot-size schedules, expiry logic
  data_store.py       Shoonya CSV reader + normaliser; builds option_bars and spot_bars DataFrames
  contract_resolver.py  Bar lookup (O(log n)), ATM resolution, strike cache — generic/Shoonya path
  liquidity.py        Entry gate (volume/OI thresholds) + OI-based slippage multiplier
  broker_sim.py       Fill price (tiered spread) + charges (STT, ETC, SEBI, stamp, brokerage, GST)
  strategy.py         OptionStrategy base class + all predefined strategies
  engine.py           Main orchestrator: BacktestEngine (Shoonya), two-mode run loop
  dhan_loader.py      Dhan-specific loader + DhanContractResolver + DhanBacktestEngine
  portfolio.py        Signed cashflow + PnL finalisation
  reports.py          Metrics (Sharpe/Sortino/Calmar/t-stat), ledger, equity curve, markdown output
  validation.py       Data quality auditing (standalone; not in hot path)
  cli.py              Command-line entry point
  # Live infrastructure (see docs/design/wing6_live_deployment_reference.md)
  paper_engine.py     Daily paper trading loop: Dhan websocket, entry/exit, checkpointing, EOD reports
  live_resolver.py    Live contract resolver backed by Dhan REST option chain + websocket quote cache
  depth_cache.py      Thread-safe 20-level depth cache shared between collector and paper engine
  dashboard_bridge.py Read-only TTL-cached file bridge for FastAPI dashboard (never opens Dhan sockets)
  live_paths.py       Path resolution helpers (durable vs snapshot dirs, tmpfs fallback)
  clock_sync.py       NTP sync check/remediation for pre-market clock validation
  spot_history.py     Appends live spot sessions to canonical static CSVs
```

---

## Data Flow — End to End

```
Raw data on disk
  Shoonya: data/raw/options/shoonya/nifty/YYYYMMDD/{STRIKE}{CE|PE}_YYYYMMDD.csv + nifty_spot.csv
  Dhan:    data/processed/options/dhan/{nifty|banknifty|finnifty|midcpnifty|sensex}/week/expiry_code_1/{call|put}/{ATM|ATMp1..ATMp10|ATMm1..ATMm10}.parquet
  Spot:    data/processed/spot/{nifty50_1min_CANONICAL|banknifty_1min_DHAN|finnifty_1min_DHAN|midcpnifty_1min_DHAN}.csv

       ↓ data_store.py (Shoonya) or dhan_loader.py (Dhan)

option_bars DataFrame  — columns: timestamp, trade_date, expiry, strike, option_type, open, high, low, close, volume, oi, ticker
spot_bars DataFrame    — columns: timestamp, open, high, low, close

       ↓ ContractResolver / DhanContractResolver (built once per trade window)

resolver  — in-memory cache of (strike, option_type) → timestamp-indexed DataFrame

       ↓ BacktestEngine.run()

For each trade date:
  1. Find entry_ts (nearest bar at/after entry_time in spot index)
  2. Build StrategyContext(timestamp=entry_ts, resolver=resolver)
  3. strategy.entry_legs(context) → list[Leg]         ← strategy plug-in point
  4. liquidity check (contract_is_liquid) per leg → abort if any leg fails
  5. _entry_fills() → list[Fill]                      ← fills entry at bar close
  6. _find_exit() → (exit_ts, reason)                 ← scans forward bar by bar
  7. _exit_fills() → list[Fill]                       ← fills exit at bar open or close
  8. finalize_trade() → Trade (gross_pnl, charges, net_pnl)

       ↓ reports.build_result()

BacktestResult
  .summary         dict — all scalar metrics
  .trade_ledger    DataFrame — one row per trade
  .daily_pnl       DataFrame — summed by exit date
  .equity_curve    DataFrame — cumulative equity
```

---

## Core Data Structures (`schemas.py`)

```python
OptionType(str, Enum)  CALL = "CE" | PUT = "PE"
Side(str, Enum)        BUY = "BUY" | SELL = "SELL"

Contract(frozen)       expiry: date, strike: int, option_type: OptionType, ticker: str
Leg(frozen)            contract: Contract, side: Side, lots: int
Fill(frozen)           timestamp, contract, side, quantity, price, gross_value, charges, reason
Trade                  expiry, strategy, entry_time, exit_time, entry/exit fills, gross/net pnl, metadata
Position               contract, quantity, avg_price  (not used in backtest path; reserved)

BacktestConfig(frozen)
  raw_root          str   — root of Shoonya option CSVs
  symbol            str   — "NIFTY", "BANKNIFTY", "FINNIFTY", or "MIDCPNIFTY"
  lot_size          int|None — None = auto via lot_size(symbol, trade_date)
  tick_size         float  = 0.05
  slippage_points   float  = 0.05   (base; multiplied by OI/exit tier in fill model)
  entry_time        time   = 09:20
  exit_time         time   = 15:20
  next_day_exit     bool   = False   (True = enter day N, exit day N+1 first bar)
  stop_loss_pct     float|None = 0.5  (exit when loss >= X * |entry_credit|)
  target_profit_pct float|None = 0.5  (exit when profit >= X * |entry_credit|)
  trail_trigger_pct float|None        (long positions only; activate trail when pnl >= X * cost_basis)
  trail_stop_pct    float|None        (exit when combined value drops X below its peak)
  include_costs     bool  = True
  bad_expiries      tuple = ("20250925", "20251224")   — quarantined from all runs
  initial_capital   float = 1_000_000
  spot_csv          str   — path to canonical 1-min spot for BnH baseline
  min_dte           int   = 0  — skip entries where (expiry - trade_date).days < min_dte
                                 set to 1 to exclude expiry-day entries (DTE=0 shorts have
                                 different dynamics from theta-carry trades)

BacktestResult
  config, trades, summary (dict), trade_ledger (df), daily_pnl (df), equity_curve (df)
```

---

## Module Details

### `calendar.py`

Key constants:
```python
NIFTY_LAST_THURSDAY_EXPIRY = date(2025, 8, 28)
NIFTY_FIRST_TUESDAY_EXPIRY = date(2025, 9, 2)   # NSE changed NIFTY weekly expiry day
```

#### `InstrumentSpec` — per-symbol metadata

```python
@dataclass(frozen=True)
class InstrumentSpec:
    symbol: str
    dhan_folder: str           # subfolder under data/processed/options/dhan/
    display_name: str
    strike_step: int           # NIFTY=50, BANKNIFTY=100, FINNIFTY=50, MIDCPNIFTY=25
    default_lot_size: int
    lot_size_schedule: tuple[tuple[date, int], ...]
    weekly_discontinued_after: date | None  # None = weekly still active
    spot_csv: str | None       # canonical 1-min spot path for BnH baseline
```

`INSTRUMENT_SPECS` contains entries for all five supported symbols (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX). Key `weekly_discontinued_after` dates:

| Symbol | Weekly expiry weekday | Discontinued after | Falls back to |
|---|---|---|---|
| NIFTY | Thursday (→ Tuesday from 2025-09-02) | — (still active) | — |
| BANKNIFTY | Wednesday (was Thursday pre-Sep 2023) | 2024-11-13 | Monthly (last Thursday/Tuesday) |
| FINNIFTY | Tuesday | 2024-11-19 | Monthly (last Tuesday) |
| MIDCPNIFTY | Monday (was Wednesday pre-Aug 2023) | 2024-11-18 | Monthly (last Monday/Thursday) |
| SENSEX | Friday (→ Tuesday Jan 2025 → Thursday Sep 2025) | — (still active) | — |

After a symbol's `weekly_discontinued_after`, `weekly_expiry_on_or_after()` automatically delegates to `monthly_expiry_on_or_after()`. All calling code is transparent to this — pass `expiry_type="week"` and the correct expiry (weekly or monthly) is returned.

#### Key functions

- `get_instrument_spec(symbol)` → `InstrumentSpec`
- `lot_size(symbol, trade_date)` — date-sensitive lot size from per-symbol schedule
- `nifty_lot_size(trade_date)` — compatibility wrapper (NIFTY only)
- `expiry_on_or_after(symbol, trade_date, expiry_type, min_dte, trading_dates)` — generic resolver for all symbols
- `weekly_expiry_on_or_after(symbol, trade_date, min_dte, trading_dates)` — falls back to monthly when weekly discontinued
- `monthly_expiry_on_or_after(symbol, trade_date, min_dte, trading_dates)` — last Thu/Tue/Mon of month, symbol- and date-aware
- `is_trading_day(d)` — checks weekends, NSE_HOLIDAYS, NSE_SPECIAL_SESSIONS (Budget Day Sat 2025-02-01)
- `next_trading_day(d)` — first NSE trading day strictly after d
- `nifty_weekly_expiry_on_or_after(...)` — NIFTY-specific wrapper, handles Thu→Tue transition
- `nifty_monthly_expiry_on_or_after(...)` — NIFTY monthly wrapper
- `parse_expiry_folder(name)` — "20240104" → date(2024,1,4)
- `combine_date_time(day, clock)` → pd.Timestamp
- `nearest_timestamp(index, target)` — first index entry >= target; returns None if none exists

Holiday list covers 2015–2026. All expiry holiday adjustments move to the previous trading session.

---

### `data_store.py` — Shoonya CSV loader

Reads: `{RAW_ROOT}/YYYYMMDD/{STRIKE}{CE|PE}_YYYYMMDD.csv`

CSV columns: `Date, Timestamp, Open, High, Low, Close, Volume, OI, Ticker`

Output columns per contract: `timestamp, trade_date, expiry, strike, option_type, open, high, low, close, volume, oi, ticker`

- Strike and option_type parsed from filename (e.g. `21500CE_20240104.csv`)
- `list_expiry_dirs(raw_root, bad_expiries)` — sorted list of valid expiry date dirs
- `load_expiry_options(expiry_dir, strikes=None)` — all CSVs in dir; optional strike filter to speed up next_day_exit mode
- `load_expiry_spot(expiry_dir)` — reads `nifty_spot.csv`
- `load_deduped_spot(raw_root, bad)` — loads all spot CSVs, deduplicates timestamps, returns global spot for signal calendar

---

### `contract_resolver.py` — Generic resolver (Shoonya path)

Built once per expiry window from (option_bars, spot_bars).

Internal state:
- `_bars_cache: dict[(strike, option_type.value) → DataFrame(indexed by timestamp)]`
- `_spot_index: DataFrame(indexed by timestamp, cols: open/high/low/close)`

Key methods:
- `atm_strike(timestamp)` → nearest 50-pt boundary to spot close at/after timestamp
- `resolve(strike, option_type)` → Contract (raises KeyError if missing)
- `resolve_atm_offset(timestamp, offset_steps, option_type)` → Contract at ATM + offset*50
- `bar_at(contract, timestamp)` → pd.Series | None (O(log n); handles duplicate timestamps)
- `bars_for(contract)` → full DataFrame for liquidity checks

Strike step: 50 points (hardcoded). ATM = `round(spot / 50) * 50`.

---

### `liquidity.py`

```python
LiquidityConfig(frozen)
  min_rows: int = 50        — minimum 1-min bars in the entire session
  min_total_volume: int = 200
  min_max_oi: int = 500
  max_atm_distance: int = 1000  — reject strikes more than 1000 pts from ATM

contract_is_liquid(df, strike, atm_strike, config) → bool
  — runs at entry; if False the whole trade is aborted

oi_slippage_multiplier(oi: float) → float
  — OI < 200 → 2.0×   (very thin; ₹2–₹3 spread expected)
  — OI < 500 → 1.5×   (moderate)
  — OI ≥ 500 → 1.0×   (standard; normal spread assumption holds)
  — called per fill in engine._entry_fills() and _exit_fills()
```

---

### `broker_sim.py` — Fill model + charges

#### Fill price model

```python
_tiered_spread_pct(close, dte) → float
  dte=0 and close < 50  → 0.020   # expiry-day far-OTM (worst case)
  close < 30            → 0.015   # far OTM
  close < 80            → 0.005   # near-OTM
  close ≥ 80            → 0.003   # ATM liquid (original flat assumption)

FillModel.fill_price(close, side, dte=None, slippage_multiplier=1.0)
  half_spread = max(tick_size, round(close * spread_pct / tick_size) * tick_size)
  slippage    = slippage_points * slippage_multiplier
  BUY:  close + slippage + half_spread  → tick-rounded
  SELL: close - slippage - half_spread  → tick-rounded, floored at tick_size

FillModel.fill(timestamp, contract, side, lots, lot_size, close, reason,
               trade_date=None, slippage_multiplier=1.0) → Fill
  — auto-computes dte = (contract.expiry - trade_date).days
  — if reason != "entry" AND side == BUY: effective_multiplier = max(slippage_multiplier, 1.5)
    (buying to close a short position = hard fill, must take liquidity)
  — OI multiplier passed in from engine; combined as max(oi_mult, 1.5) on exit if short
```

#### Charges model (date-sensitive)

All rates looked up from schedules based on trade_date:

| Component | Rate (post Apr 2026) | Basis |
|---|---|---|
| STT sell | 0.15% | premium × quantity (options seller) |
| STT exercise | 0.15% | intrinsic × quantity (ITM at expiry) |
| ETC (exchange) | 0.03503% | both sides |
| SEBI fee | 0.0001% | both sides |
| Stamp duty | 0.003% | buy side only |
| Brokerage | ₹10/order (flat, Kotak Neo) | both sides |
| GST | 18% | on brokerage + ETC + SEBI |

Pre-2026 rates are different — `ChargesConfig.for_date(d)` returns the correct schedule. STT was 0.10% Oct 2024–Mar 2026, 0.0625% before that.

Break-even premium move: ~₹1/unit. Total round-trip: ~₹50–65/lot at ₹100 premium.

---

### `strategy.py` — Strategy interface and predefined strategies

#### Base class

```python
class OptionStrategy:
    name = "OptionStrategy"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        raise NotImplementedError

    def can_enter(self, trade_date: date, spot_by_date: dict) -> bool:
        return True   # override to add pre-filter (avoids resolver construction)
```

`StrategyContext`:
```python
@dataclass(frozen=True)
class StrategyContext:
    timestamp: pd.Timestamp          # entry timestamp (first bar at/after entry_time)
    resolver: ContractResolver       # or DhanContractResolver — same interface
```

Optional method `get_spot_stop_level(context) → float | None` — return a spot price level; if spot low touches it on a later bar, exit immediately (used for `ThreePMV2CallLevelStop`).

#### Predefined strategies

| Class | Legs | Key params | Signal |
|---|---|---|---|
| `SingleLegOption` | 1 | `option_type`, `side`, `atm_offset`, `lots` | Buy or sell any single leg at ATM ± offset |
| `ShortStraddle` | 2 | `lots` | Sell ATM call + ATM put |
| `ShortStrangle` | 2 | `call_offset=2`, `put_offset=-2`, `lots`, `min_leg_premium=0.0` | Sell OTM call + put; skips entry if either leg close < `min_leg_premium` |
| `IronCondor` | 4 | `short_call_offset=2`, `long_call_offset=4`, `short_put_offset=-2`, `long_put_offset=-4`, `lots` | Sell OTM call/put + buy further OTM call/put. **Note:** class defaults to ±4 wings; the live Wing-6 deployment overrides `long_call_offset=8` and `long_put_offset=-8` |
| `ThreePMDirectional` | 1 | `signal_time`, `lots` | 3 PM bullish → buy call; bearish → buy put |
| `ThreePMV2Put` | 1 | `lots` | 3 PM bullish + 3:15 bearish → buy ATM put |
| `ThreePMV2CallLevelStop` | 1 | `lots` | Same setup → buy ATM call; spot-stop at 3 PM close level |
| `ExpiryDayStraddle` | 2 | `lots`, `symbol` | Buy ATM call + put at 3 PM on expiry day; uses `_true_atm_offsets()` to match Dhan's rolling ATM which may differ by one strike per side |
| `ExpiryDayStrangle` | 2 | `call_otm_steps=1`, `put_otm_steps=1`, `lots`, `symbol` | Buy OTM call + put at 3 PM on expiry day |

**`ShortStrangle.min_leg_premium`**: when > 0, `entry_legs()` calls `resolver.bar_at()` for each leg at entry_ts. If either close is below the threshold, raises `KeyError` → trade skipped. Use `min_leg_premium=2.0` for all index short strangles to eliminate sub-tick phantom entries on near-expiry MIDCPNIFTY (premiums as low as ₹0.10 near expiry — tick noise exceeds the 50% profit target).

**3 PM setup logic** (`_check_three_pm_setup`):
- Reads spot bar at/after 15:00 — must be bullish (close > open)
- Reads spot bar at/after 15:15 — must be bearish (close < open)
- Raises KeyError on failure → engine skips trade silently

**Dhan ATM offset matching** (`ExpiryDayStraddle._true_atm_offsets`, `ExpiryDayStrangle._find_offsets`):
- Dhan's rolling ATM (`expiry_code_1`) may place call ATM and put ATM on different strikes
- Correct approach: find the call offset whose absolute strike is closest to spot, then find the put offset whose absolute strike equals that call strike
- Both methods search `offset_strike_by_date` which maps `(side, key, trade_date, expiry) → int`

---

### `engine.py` — BacktestEngine (Shoonya path)

#### Two run modes

**Mode A: `next_day_exit=False` (default)**
- One trade per expiry folder; `entry_time` on the expiry date itself
- Calls `_run_expiry_single(expiry_dir, strategy)` per folder

**Mode B: `next_day_exit=True`**
- One trade per signal date; each date maps to exactly one expiry (nearest weekly with DTE ≥ 1)
- Builds global signal calendar from all spot data first (`load_deduped_spot`)
- `expiry_folder_map: {trade_date → Path}` — one-to-one, prevents expiry multiplication
- `_run_expiry_daily(expiry_dir, strategy, trade_dates)` — loads only needed strikes

#### Shared execution path (`_execute_trade`)

```
1. _resolve_lot_size(trade_date) → int
2. strategy.entry_legs(context) — KeyError → skip trade
3. contract_is_liquid() per leg — any fail → skip trade
4. _entry_fills() → list[Fill]
     bar = resolver.bar_at(contract, entry_ts)
     if volume == 0 → skip (data artifact)
     oi_mult = oi_slippage_multiplier(bar["oi"])
     fill_model.fill(..., slippage_multiplier=oi_mult)  ← entry at bar CLOSE
5. get_spot_stop_level() if strategy defines it
6. _find_exit(entry_ts, exit_target, legs, resolver, entry_credit, lot_size, spot_stop_level)
7. _exit_fills(exit_ts, legs, resolver, reason, lot_size, trade_date)
     bar = resolver.bar_at(contract, exit_ts)
     if volume == 0 → skip
     oi_mult = oi_slippage_multiplier(bar["oi"])
     fill_model.fill(..., slippage_multiplier=oi_mult)
     — price_col: "open" for SL/target/trail/next_day_open; "close" for time_exit
8. finalize_trade(trade) → sets gross_pnl, charges, net_pnl
```

#### `_find_exit` logic

Scans all timestamps where every leg AND spot have a bar (intersection), strictly after entry and up to exit_target.

Priority order per bar:
1. **Spot-level stop** (only checked on next-day bars): if `spot_low ≤ spot_stop_level` → exit at next bar open
2. **Stop loss** (`stop_loss_pct`):
   - Credit trade: exit if `pnl ≤ -entry_credit * stop_loss_pct`
   - Debit trade: exit if `pnl ≤ entry_credit * stop_loss_pct` (entry_credit < 0)
3. **Target profit** (`target_profit_pct`):
   - Credit: exit if `pnl ≥ entry_credit * target_profit_pct`
   - Debit: exit if `pnl ≥ abs(entry_credit) * target_profit_pct`
4. **Trailing stop** (debit / `entry_credit ≤ 0` only):
   - Activates when `pnl ≥ cost_basis * trail_trigger_pct`
   - Once active: tracks peak combined close value; exits when it drops `trail_stop_pct` below peak
5. **Time exit**: if `next_day_exit=True` → return first bar on next day; else → last bar at/before exit_target

On SL/target/trail trigger: fill at bar[i+1] open (first observable price after signal bar closed). If no next bar exists: fill at exit_target close instead.

`entry_credit` = signed cashflow at entry:
- Net SELL position → positive (credit received)
- Net BUY position → negative (debit paid)

---

### `dhan_loader.py` — Dhan path

#### Data layout on disk

```
data/processed/options/dhan/{nifty|banknifty|finnifty|midcpnifty}/week/expiry_code_1/
  call/
    ATM.parquet       ← ATM call bars (all weekly expiries merged; falls back to monthly post-discontinuation)
    ATMp1.parquet     ← call +1 strike-step from ATM (50pt for NIFTY/FINNIFTY, 100pt for BANKNIFTY, 25pt for MIDCPNIFTY)
    ...ATMp10.parquet
    ATMm1.parquet     ← call -1 strike-step
    ...ATMm10.parquet
  put/
    ATM.parquet
    ...ATMm10.parquet
```

Each parquet file has columns: `timestamp, open, high, low, close, iv, volume, oi, strike, spot`

- `strike` is the absolute index strike value — may change between rows as Dhan rolls the ATM designation
- `iv` (implied volatility) present but not used in fill model (future: dynamic spread scaling)
- `spot` is the contemporaneous index spot close embedded by Dhan in the ATM feed

#### `load_dhan_data(dhan_root, expiry_type="week", symbol="NIFTY")` → `DhanOptionData`

1. Reads all 21 call + 21 put parquet files for the symbol
2. Strips timezone, adds `trade_date` and `expiry` columns via `expiry_on_or_after(symbol, ...)` — **after weekly discontinuation, expiry tags automatically switch to monthly dates**
3. Builds `bars_cache: {(strike, option_type.value) → timestamp-indexed DataFrame}` — all strikes, full history
4. Builds `bars_cache_by_date: {date → {(strike, otype) → day-slice}}` — for O(1) resolver construction
5. Builds `offset_strike_by_date: {(side, key, trade_date, expiry) → int}` — resolves rolling offset to absolute strike
6. Builds `atm_strike_by_date: {(trade_date, expiry) → int}` — from call ATM only
7. Builds `spot_bars_by_date` and `spot_index` from the ATM call's embedded spot array
8. Filters to confirmed trading days (`is_trading_day`) to remove spurious weekend entries in feed

#### `DhanContractResolver`

Wraps `DhanOptionData`; sliced to `[trade_date, exit_date]` window.

- `atm_strike(timestamp)` — looks up `atm_strike_by_date[(ts.date(), self.expiry)]`
- `resolve_atm_offset(timestamp, offset_steps, option_type)` — looks up `offset_strike_by_date` directly; no arithmetic — uses Dhan's actual rolling ATM offset
- `bar_at(contract, timestamp)` — slices `_contract_frame(key)` on timestamp
- `bars_for(contract)` — returns all bars for contract scoped to `[trade_date, exit_date]` and filtered by `self.expiry`

**`bars_for()` scoping**: `_contract_frame()` already (a) filters rows by `expiry == self.expiry` and (b) limits to `_window_dates = [trade_date, exit_date]`. No additional date window is applied. A previous hardcoded `expiry - 6 days` cutoff was removed — it caused all trades >6 DTE from a monthly expiry to be silently skipped (liquidity check saw empty bars → aborted trade).

#### `DhanBacktestEngine`

Subclass of `BacktestEngine`. Overrides `run()` entirely:

1. Loads `DhanOptionData` once (or accepts pre-loaded via `data=` kwarg)
2. Auto-selects canonical spot CSV from `InstrumentSpec.spot_csv` for the symbol (BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX each have their own spot file)
3. Iterates `all_dates` (filtered by from_date/to_date)
4. For each date:
   - `strategy.can_enter(trade_date, canonical_spot_by_date)` — fast pre-filter
   - Resolves expiry: `min_dte = max(config.min_dte, 1 if next_day_exit else 0)` — respects `BacktestConfig.min_dte`
   - For post-discontinuation dates, `expiry_on_or_after` returns a monthly expiry automatically
   - Constructs `DhanContractResolver(data, trade_date, exit_date, expiry)`
   - **Overrides resolver spot** with canonical OHLC — required for real intraday lows in spot-stop detection; ATM-proxy has `low == close` (underestimates touches)
   - Calls `self._execute_trade(...)` — same shared path as Shoonya engine
5. Returns `build_result(config, trades)`

---

### `portfolio.py`

```python
signed_cashflow(fill) → float
  SELL fill → +gross_value   (premium received)
  BUY fill  → -gross_value   (premium paid)

finalize_trade(trade) → Trade
  gross_pnl = sum(signed_cashflow) over entry + exit fills
  charges   = sum(fill.charges) over all fills
  net_pnl   = gross_pnl - charges
```

---

### `reports.py`

#### `trade_ledger(trades, initial_capital)` → DataFrame

Columns: strategy, expiry, entry_date, entry_time, exit_date, exit_time, entry_reason, exit_reason, dte_at_entry, day_of_week, exit_hour, lot_size, spot_entry, spot_exit, entry_legs, exit_legs, gross_pnl, charges, net_pnl, equity

`entry_legs` / `exit_legs` are formatted as `"BUY:NIFTY_21500CE@100.35,SELL:..."` strings.

#### `summary(ledger, curve, daily, initial_capital, bnh_return, bnh_cagr)` → dict

All metrics:

| Key | Description |
|---|---|
| trades | total trade count |
| net_pnl | sum of net_pnl |
| gross_pnl | sum of gross_pnl |
| charges | sum of all charges |
| win_rate | trades with net_pnl > 0 / total |
| max_drawdown | peak-to-trough in ₹ |
| max_drawdown_pct | max_drawdown / initial_capital |
| sharpe | annualised Sharpe (daily return / std × √252, zeros on non-trade days) |
| sortino | annualised Sortino (daily mean / downside-std × √252) |
| sharpe_tstat | Weekly-bucket t-stat: resample daily returns to `W-FRI` → (weekly_mean / weekly_std) × √n_weeks; |t| > 1.96 = p < 0.05. Uses weekly not daily N because intra-week options trades share the same expiry and are not independent observations. |
| calmar | CAGR / abs(max_drawdown_pct) |
| cagr | compounded annual growth rate (requires ≥ 30 days, ≥ 2 trade days) |
| total_return | net_pnl / initial_capital |
| profit_factor | gross wins / |gross losses| |
| avg_trade_pnl | mean net_pnl per trade |
| bnh_return | buy-and-hold NIFTY return over same period |
| bnh_cagr | buy-and-hold NIFTY CAGR |
| date_from | first exit date |
| date_to | last exit date |
| initial_capital | as configured |
| regime_break_in_sample | True if backtest spans 2025-09-02 (NSE expiry day change Thu→Tue) |

#### `batch_summary_md(results, run_stamp)` → str

Markdown report with:
- Comparison table: all strategies side-by-side with Sharpe, t-stat, Sortino, Calmar, Max DD%, Profit Factor
- ⚠️ marker on any strategy where `regime_break_in_sample = True`
- Per-strategy detail: PnL breakdown, Returns vs BnH, Risk Metrics (including Sortino, Calmar, Sharpe p-value interpretation)

---

## Writing a New Strategy

### Minimal strategy

```python
from dataclasses import dataclass
from options_backtest.strategy import OptionStrategy, StrategyContext
from options_backtest.schemas import Leg, OptionType, Side

@dataclass(frozen=True)
class MyStrategy(OptionStrategy):
    lots: int = 1
    name: str = "MyStrategy"

    def entry_legs(self, context: StrategyContext) -> list[Leg]:
        # context.timestamp  = pd.Timestamp of the entry bar
        # context.resolver   = ContractResolver (or DhanContractResolver)
        call = context.resolver.resolve_atm_offset(context.timestamp, 0, OptionType.CALL)
        put  = context.resolver.resolve_atm_offset(context.timestamp, 0, OptionType.PUT)
        return [
            Leg(call, Side.SELL, self.lots),
            Leg(put,  Side.SELL, self.lots),
        ]
```

### Rules when writing strategies

1. **`entry_legs` must be pure given context** — no global state, no side effects
2. **Raise `KeyError` to skip a trade** — engine catches it silently and moves on
3. **Never loop over expiry folders inside a strategy** — one call → one set of legs
4. **`can_enter(trade_date, spot_by_date)` is a fast pre-filter** — called before resolver construction; use it for signal filters that only need spot data (e.g. 3 PM candle direction). Saves resolver construction cost.
5. **`get_spot_stop_level(context) → float | None`** — optional; return a spot price level; engine exits when spot low touches it on a next-day bar. Return None to disable.
6. **Offset arithmetic uses ContractResolver.resolve_atm_offset()** — offset_steps is in 50-pt increments (positive = OTM call / ITM put; negative = ITM call / OTM put). Never compute strike arithmetic yourself.
7. **Dhan-specific offset matching** — for Dhan data, `resolve_atm_offset` translates offset_steps to `offset_strike_by_date` lookup, not arithmetic. The rolling ATM may not land on exactly `round(spot/50)*50`. Use `ExpiryDayStraddle._true_atm_offsets()` pattern for expiry-day strategies where matched strike matters.

### Accessing spot data inside a strategy

```python
spot = context.resolver.spot_bars.copy()
spot["timestamp"] = pd.to_datetime(spot["timestamp"])
bar = spot[spot["timestamp"] >= context.timestamp].sort_values("timestamp").iloc[0]
# bar["open"], bar["close"], bar["high"], bar["low"]
```

Use `_first_bar_at_or_after(spot_df, trade_date, clock_time)` from `strategy.py` for timed lookups.

### BacktestConfig settings by strategy type

| Strategy type | Suggested config |
|---|---|
| Short premium theta-carry (straddle/strangle) | `stop_loss_pct=None, target_profit_pct=None, min_dte=1, next_day_exit=False` — no exits other than time; DTE=0 excluded because expiry-day dynamics differ structurally |
| Short premium with risk limits | `stop_loss_pct=2.0, target_profit_pct=None, min_dte=1` — 2× credit stop only, no target (target creates a lottery not theta capture) |
| Long debit (put/call buy) | `stop_loss_pct=0.5, target_profit_pct=1.0, trail_trigger_pct=0.5, trail_stop_pct=0.3, next_day_exit=True` |
| Expiry-day gamma scalp | `stop_loss_pct=None, target_profit_pct=None, trail_trigger_pct=0.3, trail_stop_pct=0.2, exit_time=15:28` |

---

## Key Invariants

1. **One signal → one expiry.** `expiry_folder_map` guarantees this in next_day_exit mode. In single-mode, one expiry dir = one trade.
2. **Fill price is tick-rounded.** `round_tick()` applied after all adjustments; floored at tick_size (never zero or negative).
3. **Entry fills at bar CLOSE; SL/target/trail fills at next bar OPEN; time_exit at bar CLOSE.** Never looks forward within the same bar.
4. **Zero-volume bars are rejected.** `volume == 0` → return [] → trade skipped. Prevents fills on data artifacts.
5. **Exit scan uses timestamp intersection.** Only timestamps where every leg AND spot have a bar are evaluated. Missing any bar silently defers the exit to the next valid timestamp.
6. **Costs tracked separately.** `gross_pnl` and `charges` are always separate in `Fill`, `Trade`, and `summary()`. Never netted silently.
7. **Deterministic.** Same config + same data = same output. No randomness anywhere.
8. **Quarantined expiries always excluded.** `bad_expiries = ("20250925", "20251224")` in every BacktestConfig.
9. **Weekly expiry fallback is transparent.** After BANKNIFTY/FINNIFTY/MIDCPNIFTY weekly discontinuation (Nov 2024), `expiry_on_or_after(..., expiry_type="week")` automatically returns monthly dates. All data tags, resolver lookups, and DTE calculations are consistent — no caller change needed.
10. **min_dte skips expiry-day entries at the engine level.** Checked immediately before resolver construction; expiry-day trades cannot leak through even if the strategy does not filter them.

---

## Data Quality Notes

- **No bid/ask data.** Fill proxy: tiered spread on close price (see broker_sim.py).
- **Volume and OI may be zero** in Shoonya data for low-activity bars — zero-volume bars are now rejected at fill time.
- **Dhan ATM offset ≠ arithmetic ATM.** The rolling ATM (`expiry_code_1`) is Dhan's internal designation; it may differ by one strike from `round(spot/strike_step)*strike_step`. Always use `offset_strike_by_date` for Dhan, never recompute. Strike steps differ per symbol (NIFTY=50, BANKNIFTY=100, FINNIFTY=50, MIDCPNIFTY=25).
- **Dhan IV is available but unused.** `iv` column is loaded in parquet files and passed through `bars_cache` but discarded before `fill()`. Future work: use IV to dynamically scale half-spread.
- **Spot proxy in Dhan:** `spot_bars` defaults to the ATM call's embedded `spot` array (close only; `low == close`). DhanBacktestEngine overrides with canonical OHLC for spot-stop detection. Without this override, `_find_exit` underestimates spot low touches.
- **Near-expiry sub-tick MIDCPNIFTY premiums:** On DTE=0 MIDCPNIFTY, the ATM±2 strikes can have premiums of ₹0.10–₹0.75. A 50% target threshold falls within tick noise (₹0.05 tick size). Use `ShortStrangle(min_leg_premium=2.0)` to exclude these entries. Confirmed: without the filter, a ₹0.10 entry can "hit target" from a single tick move, generating phantom profits.
- **Expiry regime break: 2025-09-02.** NSE changed NIFTY weekly expiry Thursday → Tuesday. Pre and post data have different expiry-day dynamics (liquidity, OI rolloff, gamma). `summary()` flags `regime_break_in_sample: True` when backtest spans this date. Split analysis before pooling.
- **Weekly discontinuation (non-NIFTY, Nov 2024):** BANKNIFTY weekly ended 2024-11-13, FINNIFTY 2024-11-19, MIDCPNIFTY 2024-11-18. Trades after these dates use monthly expiries (DTE 15–30 at entry). The engine handles this transparently but the strategy character changes — monthly short strangles carry delta risk for longer. Mixing pre- and post-discontinuation data in the same Sharpe is an unresolved regime mix.
- **Quarantine dates:** `20250925` and `20251224` have confirmed data issues. Excluded in BacktestConfig defaults.
- **Calendar accuracy:** NSE holidays in `calendar.py` cover 2015–2026. Approximate dates (e.g. Eid, Bakri Id) may need verification; fixed-date holidays (Republic Day, Independence Day, Christmas, Ambedkar Jayanti) are exact.

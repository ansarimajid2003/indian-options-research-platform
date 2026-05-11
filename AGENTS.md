<!-- From: c:\Users\ansar\Documents\indian markets\AGENTS.md -->
# Indian Markets — AGENTS.md

## Project Overview

Multi-index options backtest engine + research pipeline for Indian equity indices (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX).

Current phase: defined-risk short-vol deployment → live monitoring / scaling.

The codebase is organised into four layers:
- **Engine** (`options_backtest/`): broker-neutral backtest engine with tiered fill model, cost accounting, and strategy plug-ins.
- **Data & research** (`market_data/`, `scripts/`, `data/`, `reports/`): data loaders, validation pipelines, analysis notebooks, and backtest result stores.
- **Live infrastructure** (`scripts/live/`): Dhan market-data collector, paper-trading engine runner, health monitor, and dashboard API — all deployed on zimaos.
- **Tests** (`tests/`): unit tests and bias audits.

## Technology Stack

- **Language**: Python 3.11+ (currently running on Python 3.14.3)
- **Core dependencies**: pandas, numpy, pyarrow
- **Data sources**:
  - Shoonya 1-min OHLCV (primary, NIFTY only, full strike chain)
  - Dhan rolling ATM JSON/parquet (2021–2026, ATM±10, embedded IV, all 5 indices)
  - NSE Bhavcopy EOD (2008–2026, long-run validation)
- **No package manager files**: there is no `pyproject.toml`, `requirements.txt`, `setup.py`, `setup.cfg`, `Makefile`, `package.json`, or `Cargo.toml`. Dependencies are managed manually. Inspect imports to infer what is needed.
- **No CI/CD**: no GitHub Actions, Docker, pre-commit hooks, or automated linting/formatting configs (no black, ruff, flake8, mypy configs present).

## Project Structure

```
.
├── options_backtest/          # Backtest engine (15 modules)
│   ├── __init__.py
│   ├── __main__.py            # CLI entry: python -m options_backtest
│   ├── cli.py                 # argparse subcommands (normalize, audit, run-backtest, run-backtest-shoonya)
│   ├── engine.py              # BacktestEngine (Shoonya path)
│   ├── dhan_loader.py         # DhanBacktestEngine + DhanContractResolver
│   ├── strategy.py            # OptionStrategy base + predefined strategies
│   ├── broker_sim.py          # Fill model + charges (STT, ETC, SEBI, GST, etc.)
│   ├── calendar.py            # NSE trading calendar, expiry logic, lot sizes
│   ├── contract_resolver.py   # Bar lookup, ATM resolution, strike cache
│   ├── data_store.py          # Shoonya CSV reader + normaliser
│   ├── liquidity.py           # Volume/OI entry gate + slippage multiplier
│   ├── portfolio.py           # Signed cashflow + PnL finalisation
│   ├── reports.py             # Metrics (Sharpe/Sortino/Calmar/t-stat), ledger, equity curve
│   ├── schemas.py             # Core data structures (Contract, Leg, Fill, Trade, BacktestConfig)
│   ├── validation.py          # Data quality auditing (standalone)
│   └── volatility_filter.py   # VIX-based entry filter
├── market_data/               # Secondary data-access layer
│   ├── __init__.py
│   └── data_loader.py         # IS/OOS gatekeeper for cleaned market-archive CSVs
├── scripts/                   # Analysis, data processing, download, and live scripts
│   ├── analysis/              # Backtest runners, PnL attribution, research studies
│   ├── data/                  # Data inventory, validation, cleaning builders
│   ├── download/              # Scrapers and downloaders (Dhan, Shoonya, NSE bhavcopy)
│   └── live/                  # Live-market infrastructure (deployed on zimaos)
│       ├── api/               # FastAPI dashboard backend
│       │   ├── main.py        # App entry point; mounts static dashboard/
│       │   ├── models.py      # Pydantic response models (LivePushFrame, etc.)
│       │   └── routes/
│       │       ├── live.py    # /api/live/* REST + /ws/live WebSocket
│       │       ├── historical.py
│       │       └── backtests.py
│       ├── systemd/           # dashboard-api.service, health-monitor.service
│       ├── health_monitor.py  # Writes alert/storage snapshots; Telegram alerts
│       └── dhan_connection_check.py
├── dashboard/                 # CDN React 18 frontend (no build step)
│   ├── index.html             # Loads scripts in strict order
│   ├── app.jsx                # Root component; REST fetch + WS wiring; adapter fns
│   ├── panels.jsx             # SpotChartCard, EquityCurvePanel, PositionsPanel, etc.
│   ├── components.jsx         # Shared: Icon, MarketBadge, Sparkline, Meter, useClock
│   ├── data.jsx               # window.WingData mock data (fallback / offline)
│   ├── tweaks-panel.jsx       # Dev tweaks panel
│   └── styles.css
├── tests/                     # Unit tests and bias audits
│   ├── test_options_backtest.py
│   └── test_bias_audit.py
├── data/                      # Market data (largely gitignored; see .gitignore)
│   ├── raw/                   # Shoonya raw CSVs
│   ├── processed/             # Dhan parquet, canonical spot CSVs, bhavcopy
│   ├── manifests/             # Data inventory manifest
│   └── audit/                 # Validation outputs
├── docs/                      # Design docs, setup guides, research notes
│   ├── design/                # Architecture plans, live-trading designs
│   ├── research/              # Data audits, KB reviews
│   ├── setup/                 # Broker setup guides
│   └── DATA_LAYOUT.md         # Canonical data layout reference
├── reports/                   # Generated backtest and analysis outputs
│   ├── backtests/
│   ├── analysis/
│   ├── data_quality/
│   └── research/
├── logs/                      # Download and run logs
├── ENGINE_ARCHITECTURE.md     # Single-source-of-truth for engine internals
├── AGENTS.md                  # This file
├── .gitignore                 # Excludes data/, reports/, logs/, venvs
└── CLAUDE.md                  # Claude-specific project context + dashboard access
```

## Build and Run Commands

There is no formal build step. The project is run directly as Python modules and scripts.

### CLI entry point

```bash
# Primary backtest command (Dhan 5-year data)
python -m options_backtest run-backtest \
  --symbol NIFTY \
  --strategy short-straddle \
  --from-date 2021-01-01 \
  --to-date 2026-04-21

# Available strategies: short-straddle, short-strangle, iron-condor,
#   three-pm-directional, three-pm-v2-put, three-pm-v2-call-level-stop

# Legacy Shoonya backtest (kept for reference)
python -m options_backtest run-backtest-shoonya \
  --strategy short-straddle \
  --from-expiry 20240104 \
  --to-expiry 20240131

# Data normalisation
python -m options_backtest normalize --raw-root data/raw/options/shoonya/nifty

# Raw data audit
python -m options_backtest audit --raw-root data/raw/options/shoonya/nifty
```

### Running analysis scripts

Scripts in `scripts/analysis/` and `scripts/data/` are executed directly:

```bash
python scripts/analysis/run_optimized_wing6_portfolio.py
python scripts/data/validate_dhan_options.py
```

### Data loader self-test

```bash
python market_data/data_loader.py
```

## Testing Instructions

**Framework**: `unittest` only — no pytest.

### Run all tests

```bash
python -m unittest discover -s tests -v
```

### Run individual test files

```bash
python -m unittest tests.test_options_backtest -v
python -m unittest tests.test_bias_audit -v
```

### Run specific test classes

```bash
python -m unittest tests.test_bias_audit.BiasAuditTests -v
```

### Test data requirement

`test_options_backtest.py` and `test_bias_audit.py` require Shoonya NIFTY options data at `data/raw/options/shoonya/nifty/`. If the directory does not exist, the tests skip with `self.skipTest("Shoonya options data not available")`.

### Test coverage

- `test_options_backtest.py`: parsers, loaders, ATM resolution, liquidity gating, fill model tick rounding, charges, short-straddle determinism, calendar correctness (NIFTY Thu→Tue transition, cross-index lot sizes, discontinued weeklies), ledger columns, VIX filter, STT regime boundaries.
- `test_bias_audit.py`: look-ahead bias on entry/exit prices, SL/target trigger semantics, survivorship bias, timestamp intersection, debit-entry handling, time-exit boundaries, adversarial slippage direction, non-negative charges.

## Code Style Guidelines

- **No automated formatter/linter is configured.** Follow the style already present in the codebase.
- Use `from __future__ import annotations` at the top of every module.
- Use type hints for function signatures and dataclasses.
- Prefer `pathlib.Path` over string paths.
- Prefer frozen dataclasses (`@dataclass(frozen=True)`) for value objects (see `schemas.py`, `calendar.py`).
- Use `pd.Timestamp` for timestamps, `datetime.date` for calendar dates.
- Module docstrings are concise; inline comments explain *why*, not *what*.
- Keep the engine deterministic: no randomness, no global mutable state.

## Engine Reference

**Before touching any engine code or writing strategies, read `ENGINE_ARCHITECTURE.md` first.**
It is a single-file reference covering all 13 modules, the complete data flow, fill model tiers, strategy interface, how-to-write-strategies guide, and key invariants. Reading it replaces ~15 individual file reads. Always load it at the start of any engine-related session.

## Active Alpha: Wing-6 Optimized Iron Condor

Defined-risk iron condor across NIFTY, FINNIFTY, MIDCPNIFTY, SENSEX. BANKNIFTY excluded.
Short at ±2 offsets, long at ±8 offsets (300-pt wings for NIFTY/FINNIFTY, 125–175 pt for MIDCPNIFTY, 600-pt for SENSEX).

Per-symbol filters: NIFTY VIX>=13 (weekly), FINNIFTY DTE<=7 (monthly), MIDCPNIFTY DTE<=7 (monthly), SENSEX DTE<=2 (weekly). Smart bucket policies per symbol.

**Primary deployable (1:1:1:1 lots N:F:M:S, filtered 4x1 all VIX):**
- 901 trades | Net PnL +195,631 | CAGR 4.3% | Sharpe 2.746 | t-stat 5.302
- Sortino 3.999 | Calmar 4.175 | MaxDD -1.0% | PF 1.988 | Win% 60%
- IS: PnL +68,317, Sharpe 2.064 | OOS: PnL +127,314, Sharpe 3.892

**Scaled deployment grid (same Sharpe/Calmar, linearly scaled risk):**

| Scale | Lots (N:F:M:S) | CAGR | Sharpe | MaxDD% | Net PnL | CVaR95/day |
|---|---:|---:|---:|---:|---:|---:|
| 1x | 1:1:1:1 | 4.3% | 2.746 | -1.0% | +195,631 | -2,080 |
| 2x | 2:2:2:2 | 8.1% | 2.746 | -2.0% | +391,262 | -4,160 |
| 3x | 3:3:3:3 | 11.6% | 2.746 | -3.0% | +586,893 | -6,240 |
| 4x | 4:4:4:4 | 14.9% | 2.746 | -4.0% | +782,524 | -8,320 |

Sweet spot: 2x–3x scale → CAGR 8–12% with MaxDD under -3.5%.

**All engine bugs fixed (May 2026).** Includes: expiry, calendar, stop gate, tiered slippage, OI liquidity thresholds, asymmetric exit fills, Sortino/Calmar metrics. Additional bias fixes (May 2026): `bars_for()` monthly DTE window removed (was silently skipping all trades >6 DTE from monthly expiry), `min_dte` config field added, weekly t-stat resampling, `ShortStrangle.min_leg_premium` for sub-tick MIDCPNIFTY filtering. Re-run with new config before drawing conclusions.

## Code Rules

- One signal → one expiry, always. Never loop signals inside expiry folders.
- Fill model: tiered slippage by moneyness proxy (ATM 0.3%, near-OTM 0.5%, far-OTM 1.5%, expiry-day OTM 2.0%); 1.5× multiplier on short exits; OI-scaled multiplier. Tick-round after slippage.
- Costs tracked separately from gross PnL; never net silently.
- Deterministic: same config → same output.
- No ML until 3+ years of clean walk-forward OOS exists.
- Short strangle runs: always set `stop_loss_pct=None, target_profit_pct=None, min_dte=1, ShortStrangle(min_leg_premium=2.0)`. Target exits create a lottery not theta carry; DTE=0 trades are a structurally different strategy.
- After weekly expiry discontinuation (BANKNIFTY/FINNIFTY/MIDCPNIFTY Nov 2024), `expiry_type="week"` still works — it falls back to monthly automatically. Do not switch to `expiry_type="month"` unless you want to exclude pre-discontinuation data.

## Cost Model (2026 STT — effective April 1, 2026)

| Item | Rate |
|---|---|
| Options sell STT | 0.15% of premium (was 0.10%) |
| Options exercise STT | 0.15% of intrinsic (was 0.125%) |
| Futures sell STT | 0.05% of contract (was 0.02%) |
| Exchange (NSE ETC) | 0.03503% of premium, both sides |
| SEBI fee | 0.0001% of premium, both sides |
| Brokerage (Kotak Neo flat) | ₹10/order (~₹20 round-trip) |
| GST on brokerage + ETC + SEBI | 18% |
| **Total round-trip** | **₹50–65/lot at ₹100 premium** |

Break-even move: ~₹1/unit. All pre-2026 backtests are **invalid** until re-run with new rates.

## Roadmap (in order)

1. ~~**Fix bugs + cost model**~~ ✓ Done May 2026.
2. ~~**Bias fixes**~~ ✓ Done May 2026.
3. ~~**Short-vol contributor validation**~~ ✓ Done May 2026.
4. ~~**Structure upgrade**~~ ✓ Done May 2026 — Wing-6 IC validated: defined-risk iron condor across NIFTY/FINNIFTY/MIDCPNIFTY. Primary 1:2:2 lots, Sharpe 2.454, MaxDD -1.0%, t-stat 4.798.
5. ~~**SENSEX integration**~~ ✓ Done May 2026 — SENSEX added to engine (calendar.py, cli.py), Dhan data backfilled (May 2023–May 2026, 84 parquet files, 19M bars). Wing-6 IC DTE<=2 filter: Sharpe 3.69, t-stat 5.62, 141 trades. Expanded portfolio (N:F:M:S 1:1:1:1): Sharpe 2.746, t-stat 5.30.
6. ~~**Web dashboard**~~ ✓ Done May 2026 — FastAPI backend + CDN React frontend deployed on zimaos; real spot charts (4 indices), alert timeline, storage health, equity curve, positions panel. See § Web Dashboard above.
7. **Live deployment prep** — broker integration, order sizing, daily signal generation.
8. **OOS monitoring** — track top performers monthly; pause variant if OOS Sharpe < 1.5 for two consecutive months.
9. **Data validation** — cross-check Shoonya vs Dhan, confirm MIDCPNIFTY/BANKNIFTY/FINNIFTY spot CSV coverage post-2024.
10. **ML layer** — only after 3+ years clean OOS; turnover-regularized model with DTE/moneyness/IV/volume/OI.

## Data

| Source | Coverage | Role | Location |
|---|---|---|---|
| Shoonya options 1-min | 2024-01-04 – 2026-04-21 | Primary; full strike chain (NIFTY only) | `data/raw/options/shoonya/nifty/` |
| Dhan options 1-min | 2021–2026 (NIFTY) / 2022–2026 (BN/FN) / 2023–2026 (MCP/SX) | Extended history; ATM±10; embedded IV; all 5 indices | `data/processed/options/dhan/{nifty\|banknifty\|finnifty\|midcpnifty\|sensex}/` |
| NSE Bhavcopy EOD | 2008 – 2026 | Long-run validation; 8.07M rows | `data/processed/nse/bhavcopy/fo/` |
| NIFTY spot 1-min | 2015 – 2026 | Signal + calendar source | `data/processed/spot/nifty50_1min_CANONICAL.csv` |
| BANKNIFTY spot 1-min | 2022 – 2026 | BnH baseline + canonical fills for BANKNIFTY | `data/processed/spot/banknifty_1min_DHAN.csv` |
| FINNIFTY spot 1-min | 2022 – 2026 | BnH baseline + canonical fills for FINNIFTY | `data/processed/spot/finnifty_1min_DHAN.csv` |
| MIDCPNIFTY spot 1-min | 2023 – 2026 | BnH baseline + canonical fills for MIDCPNIFTY | `data/processed/spot/midcpnifty_1min_DHAN.csv` |
| SENSEX spot 1-min | 2023 – 2026 | BnH baseline + canonical fills for SENSEX | `data/processed/spot/sensex_1min_DHAN.csv` |
| India VIX 1-min/daily | 2015 – 2026 | Regime gate | `data/processed/market_archive_cleaned/INDIA VIX_*.csv` |

- **No bid/ask anywhere.** Fill proxy: tiered spread on close (ATM 0.3% → expiry-day OTM 2.0%) + OI-scaled slippage multiplier. Zero-volume bars rejected as data artifacts.
- **Weekly expiry discontinuation (non-NIFTY):** BANKNIFTY weekly ended 2024-11-13, FINNIFTY 2024-11-19, MIDCPNIFTY 2024-11-18. Engine falls back to monthly expiry automatically — post-discontinuation trades have DTE 15–30 at entry and different risk character.
- **NIFTY expiry regime break:** NSE changed weekly expiry Thursday → Tuesday on 2025-09-02. `calendar.py` handles this. `summary()` flags `regime_break_in_sample: true` for any backtest spanning this date.
- **SENSEX expiry regime breaks:** BSE launched weekly options May 2023 (Friday expiry); shifted to Tuesday Jan 2025; shifted to Thursday Sep 2025. `calendar.py` handles all three transitions. Lot size changed 10→20 on Jan 10 2025.
- **Quarantine:** `20250925`, `20251224` excluded from all runs.
- Data manifest: `data/manifests/data_inventory_manifest.csv` (502 datasets, 30 GB).

## Web Dashboard

Live read-only monitoring dashboard deployed on zimaos. Shows real-time positions, equity curve, spot charts, alert timeline, and storage health.

**Access from laptop:**
```bash
ssh -L 9000:127.0.0.1:8000 zimaos   # keep terminal open
# open http://localhost:9000/ in browser
```
(Local port 8000 is occupied; use 9000.)

**Services on zimaos (auto-start on boot):**
- `dashboard-api.service` — FastAPI/uvicorn, `127.0.0.1:8000`, `LIVE_ROOT=/media/WD-Storage/indian-markets-live`
- `health-monitor.service` — writes alert/storage snapshots (~60 s off-hours, ~30 s during market)

**Restart command:** `ssh zimaos "systemctl restart dashboard-api health-monitor"`

**Wiring summary:**

| Layer | Key files |
|---|---|
| Backend entry | `scripts/live/api/main.py` — FastAPI app; mounts `dashboard/` as static root |
| Routes | `scripts/live/api/routes/live.py` — all `/api/live/*` REST + `/ws/live` WS push |
| Data bridge | `options_backtest/dashboard_bridge.py` — TTL-cached file reader (3 s live / 5 s trades / 60 s hist); never opens Dhan sockets |
| Frontend | `dashboard/app.jsx` — REST fetch on mount; WS for live updates; `adaptPosition()`, `adaptEquityPoint()`, `adaptAlert()` shape API→UI |
| Mock fallback | `dashboard/data.jsx` (`window.WingData`) — used when API data not yet loaded |
| Spot charts | `/api/live/spot/{symbol}?days=N` → last N sessions from `data/processed/spot/*.csv`; timestamps are "display epoch" (IST naive treated as UTC so chart shows IST labels) |
| WS frame | `LivePushFrame` in `scripts/live/api/models.py`; pushed every 1 s (market hours) / 5 s (off-hours) |

**Market-closed behaviour:** spot charts show last session data with "LAST SESSION" badge; option chain shows "MARKET CLOSED" overlay; storage and alert panels always show live values from WD drive.

**Full design spec:** `docs/design/live_paper_trading_plan.md` § 13 (Web Dashboard).

## Security Considerations

- **No secrets in repo**: API keys, broker credentials, and `.env` files are gitignored. Live broker integration scripts (e.g. `scripts/live/kotak_neo_live_collector.py`) must read credentials from environment variables or local `.env` files never committed to version control.
- **OOS data lock**: `market_data/data_loader.py` enforces a hard in-sample / out-of-sample split. Loading OOS data requires setting `OOS_UNLOCKED=1`. This prevents accidental data snooping during strategy development.
- **Data integrity**: Quarantined dates (`20250925`, `20251224`) are excluded from all engine runs via `BacktestConfig.bad_expiries`. Zero-volume bars are rejected at fill time to prevent phantom trades on data artifacts.
- **No network calls in engine**: the backtest engine is purely offline. Only download scripts in `scripts/download/` make external network requests.

## Subagents

| Agent | When to use |
|---|---|
| `researcher.md` | Before implementing any strategy — KB + web evidence first |
| `bias-auditor.md` | Before trusting any backtest result |
| `coder.md` | Engine/strategy implementation and bug fixes |
| `reviewer.md` | After any code change, before running a backtest |
| `trade-analyzer.md` | After a clean backtest run — PnL attribution |

## Research KB

Qdrant+PostgreSQL at `100.101.17.114:8765` via `kb` MCP. 1,868 quant papers.
Search before implementing. Key topic filters: `options_derivatives`, `backtesting`, `risk_management`, `market_microstructure`, `kelly_criterion`.

Key papers already indexed and relevant:
- `Baltussen 2025` — EOD reversal; 73.4% touch rate; reversal in 1–2 h
- `Lou/Polk/Skouras 2019` — momentum profits: overnight OR intraday, not both
- `2508.16598` — hybrid Kelly × VIX sizing for short-vol; 0–5 DTE far OTM optimal
- `2407.21791` — turnover regularization; high-churn option strategies destroyed by costs
- `2207.02989` — mid-price calibration biased with wide spreads; Shoonya OHLC not trustworthy for live fills

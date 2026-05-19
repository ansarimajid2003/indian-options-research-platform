<!-- From: c:\Users\ansar\Documents\indian markets\AGENTS.md -->
# Indian Markets — AGENTS.md

## Project Overview

Multi-index options backtest engine + research pipeline for Indian equity indices (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX).

Current phase: **live paper trading** on zimaos (Wing-6 Iron Condor). No real orders placed. See § Live Deployment below.

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
- **No formal package manager files**: there is no `pyproject.toml`, root `requirements.txt`, `setup.py`, `setup.cfg`, `Makefile`, `package.json`, or `Cargo.toml`. `requirements-live.txt` exists only as the live-server dependency list. For research/backtest scripts, inspect imports to infer what is needed.
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
├── reports/                   # Generated backtest, analysis, live-audit outputs
│   ├── backtests/
│   ├── analysis/
│   ├── data_quality/
│   ├── live/
│   │   └── session_audits/    # Daily live-paper session audits
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

**Before touching backtest engine code or writing strategies, read `ENGINE_ARCHITECTURE.md` first.**
It covers the backtest internals: all modules, data flow, fill model tiers, strategy interface, how-to-write-strategies guide, and key invariants. Reading it replaces ~15 individual file reads.

**Before touching live infrastructure (paper engine, dashboard API, health monitor, systemd services), read `docs/design/wing6_live_deployment_reference.md` first.**

| Topic | Read this |
|---|---|
| Backtest engine, strategies, Dhan loader, reports | `ENGINE_ARCHITECTURE.md` |
| Live paper trading, zimaos services, dashboard, entry gates, crash recovery | `docs/design/wing6_live_deployment_reference.md` |

## Active Alpha: Wing-6 Optimized Iron Condor

Defined-risk iron condor across NIFTY, FINNIFTY, MIDCPNIFTY, SENSEX. BANKNIFTY excluded.
Short at ±2 offsets, long at ±8 offsets.

Per-symbol filters: NIFTY VIX>=13 (weekly), FINNIFTY DTE<=7 + skip VIX 10-13 (monthly), MIDCPNIFTY DTE<=7 + skip VIX 22-30 (monthly), SENSEX DTE<=2 (weekly).

**Primary deployable (1:1:1:1 lots N:F:M:S, filtered 4x1):**
- 901 trades | Net PnL +195,631 | CAGR 4.3% | Sharpe 2.746 | t-stat 5.302
- Sortino 3.999 | Calmar 4.175 | MaxDD -1.0% | PF 1.988 | Win% 60%
- IS: PnL +68,317, Sharpe 2.064 | OOS: PnL +127,314, Sharpe 3.892

Sweet spot: 2x–3x scale → CAGR 8–12% with MaxDD under -3.5%.

**Full live setup** (services, disk layout, daily timeline, entry gates, fill model, crash recovery) → `docs/design/wing6_live_deployment_reference.md`.

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
6. ~~**Web dashboard**~~ ✓ Done May 2026 — FastAPI backend + CDN React frontend deployed on zimaos; real spot charts (4 indices), alert timeline, storage health, equity curve, positions panel. See § Live Deployment below.
7. ~~**Live deployment prep**~~ ✓ Done May 2026 — live paper trading active on zimaos (timer-driven daily, Dhan websocket + REST, crash recovery, health monitor, dashboard API).
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

- **Historical backtest datasets have no bid/ask.** Offline fills use the tiered spread proxy on close (ATM 0.3% → expiry-day OTM 2.0%) + OI-scaled slippage multiplier. Live paper trading separately records executable bid/ask/depth where available. Zero-volume bars are rejected as data artifacts.
- **Weekly expiry discontinuation (non-NIFTY):** BANKNIFTY weekly ended 2024-11-13, FINNIFTY 2024-11-19, MIDCPNIFTY 2024-11-18. Engine falls back to monthly expiry automatically — post-discontinuation trades have DTE 15–30 at entry and different risk character.
- **NIFTY expiry regime break:** NSE changed weekly expiry Thursday → Tuesday on 2025-09-02. `calendar.py` handles this. `summary()` flags `regime_break_in_sample: true` for any backtest spanning this date.
- **SENSEX expiry regime breaks:** BSE launched weekly options May 2023 (Friday expiry); shifted to Tuesday Jan 2025; shifted to Thursday Sep 2025. `calendar.py` handles all three transitions. Lot size changed 10→20 on Jan 10 2025.
- **Quarantine:** `20250925`, `20251224` excluded from all runs.
- Data manifest: `data/manifests/data_inventory_manifest.csv` (502 datasets, 30 GB).

## Live Deployment

Active paper-trading profile: `wing6_4x1_all_vix_filtered`. Runs on `zimaos` (ZimaOS Linux, public IP 183.83.38.115 via ACT).

**Systemd-managed runtime:**
- `live-paper-daily.timer` — active timer; starts `live-paper.service` Mon-Fri 08:45 IST
- `live-paper.service` — one-shot daily paper engine from pre-open through EOD; can be inactive/dead outside the session window
- `dashboard-api.service` — FastAPI/uvicorn daemon on `127.0.0.1:8000`
- `health-monitor.service` — independent uptime/integrity monitor daemon

**Disk layout:**
- `/DATA` (SSD, 222G) — repo, venv, small caches
- `/media/WD-Storage` (btrfs, 466G) — live artifacts, order book, paper outputs
- `data/live` → symlink to `/media/WD-Storage/indian-markets-live`

**Dashboard access:**
```bash
ssh -L 9000:127.0.0.1:8000 zimaos
# open http://localhost:9000/
```

**Service restart:** `ssh zimaos "systemctl restart dashboard-api health-monitor"`

**Full live reference** (timeline, entry gates, fill model, crash recovery, env vars, data artifacts) → `docs/design/wing6_live_deployment_reference.md`.

## Session Audits

Canonical local audit folder: `reports/live/session_audits/`.

Every live-paper session should get a dated post-session audit named `YYYYMMDD_session_audit.md` in that folder, even for no-trade days. Use zimaos artifacts under `/media/WD-Storage/indian-markets-live` as the source of truth, then write the local audit report with:
- session verdict and whether it is usable paper-trading evidence
- trade outcome and signal skips from `paper_trades/YYYYMMDD*.json*`
- alert counts, external heartbeat status, and dashboard/API health
- Dhan transport checks split by REST, live-feed websocket, and 20-depth websocket
- root cause, fixed-vs-open issues, and safe-now vs after-hours actions

Existing audits:
- `reports/live/session_audits/20260515_session_audit.md`
- `reports/live/session_audits/20260518_session_audit.md`

## Best Practices (Project-Specific)

### Before touching anything live
- **Read `ENGINE_ARCHITECTURE.md` first** before modifying any engine code. It replaces ~15 individual file reads.
- **Read `docs/design/wing6_live_deployment_reference.md`** before touching live infrastructure, configs, or systemd services.
- **Verify zimaos state live** — never assume server state from old context. SSH and check `systemctl status`, logs, or disk state when describing what is running.

### Data & facts
- **Never assume data exists.** The `data/` directory is largely gitignored. Always check `Path.exists()` before reading.
- **Never assume the calendar.** NSE has had multiple regime breaks (NIFTY Thu→Tue 2025, SENSEX Fri→Tue→Thu, weekly discontinuations Nov 2024, lot size changes). Always check `calendar.py` or expiry logic before making claims about a symbol's expiry day.
- **Never hardcode cost rates.** STT changed April 2026 (0.10% → 0.15%). Costs are date-sensitive via `ChargesConfig.for_date()`. Pre-2026 backtests are invalid until re-run.
- **Check `OOS_UNLOCKED=1`** is required to load out-of-sample data. Don't accidentally snoop OOS.

### Code & configs
- **Never modify a test to make it pass.** Fix the engine/strategy code. Tests are the ground truth.
- **Never commit secrets.** `.env.live`, tokens, and API keys are gitignored. Redact token-like strings from all logs and markdown.
- **Never make network calls in engine code.** The backtest engine (`options_backtest/`) is purely offline. Only `scripts/download/` makes external requests.
- **Always use `pathlib.Path`**, never string paths.
- **Always preserve determinism.** No `random`, no global mutable state, no unseeded numpy calls.
- **Check subdirectory `AGENTS.md` files** — deeper directories may override parent guidance.

### Live infra
- **Never touch `configs/live/` without reading the full gate logic.** Entry has 9 sequential checks; changing one field can silently skip all trades.
- **Read the latest session audit before diagnosing live issues.** The current audit trail lives in `reports/live/session_audits/`; preserve the distinction between startup failures, host outages, Dhan transport issues, dashboard/runtime issues, and paper-engine/health-monitor behavior.
- **Never assume the laptop and zimaos are in sync.** The server has a separate bare repo (`/DATA/live-paper/indian-markets.git`). Code must be pushed and pulled before it runs on zimaos.
- **Never run git mutations** (`git commit`, `git push`, `git reset`, `git rebase`) without explicit user confirmation each time.

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


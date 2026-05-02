# Indian Markets — CLAUDE.md

## Project

NIFTY 50 options backtest engine + research pipeline.
Phase: bug fixes → exit window validation → structure upgrade (debit spreads).

## Stack

- Python 3.11+, pandas, numpy, pyarrow
- Engine: `options_backtest/` (15 modules, custom, broker-neutral)
- Data: Shoonya 1-min OHLCV (primary), Dhan rolling ATM JSON (2021–2026, cross-check + IV), NSE Bhavcopy EOD (2008–2026)
- Tests: `tests/`, `unittest` only — no pytest

## Active Alpha: 3 PM Setup

Bullish 3 PM candle → bearish 3:15 candle → gap-up next day → fade intraday.

- 462 observations, 2015–2024; 40% next-day up (vs 46.9% baseline) — bearish lean
- 73.4% touch-rate of prior 3 PM close; when touched: 27.4% up, avg O-C –0.30%
- Stable across sub-periods (2015–18 / 2019–22 / 2023+)
- **Academic anchor:** Baltussen et al. (2025) — EOD reversal 3.78–6.86 bps/day; Lou et al. (2019) — momentum profits are overnight OR intraday, never both
- **Exit window:** reversal completes in first 1–2 hours; current EOD exit is wrong — test 10:00 AM, 11:00 AM, and touch-of-prior-3PM-close exits

**All current backtest variants are negative.** Root causes: three correctness bugs + pre-2026 STT rates.

## Three Bugs — Fix Before Anything Else

1. **Expiry multiplication** — one signal fires across multiple expiry folders → inflates trade count. Fix: one signal → nearest weekly expiry with DTE ≥ 1.
2. **next_day_exit** — uses `+ timedelta(days=1)` not next trading day. Fix: use `calendar.next_trading_day()`.
3. **Long option stops** — `entry_credit > 0` gate skips debit positions. Fix: gate on `entry_credit <= 0` for debit stops.

## Code Rules

- No new features until all three bugs are fixed.
- One signal → one expiry, always. Never loop signals inside expiry folders.
- Fill model: `close ± slippage`, tick = 0.05, lots = 75. Tick-round after slippage.
- Costs tracked separately from gross PnL; never net silently.
- Deterministic: same config → same output.
- No ML until 3+ years of clean walk-forward OOS exists.

## Cost Model (2026 STT — effective April 1, 2026)

| Item | Rate |
|---|---|
| Options sell STT | 0.15% of premium (was 0.10%) |
| Options exercise STT | 0.15% of intrinsic (was 0.125%) |
| Futures sell STT | 0.05% of contract (was 0.02%) |
| Exchange + SEBI | ~₹5/lot |
| Brokerage (flat) | ~₹40/lot |
| GST on brokerage | 18% |
| **Total round-trip** | **₹65–80/lot at ₹100 premium** |

Break-even move: ~₹1/unit. All pre-2026 backtests are **invalid** until re-run with new rates.

## Roadmap (in order)

1. **Fix three bugs** (expiry, calendar, stop gate) + update STT in `broker_sim.py`
2. **Exit window study** — compare 10 AM / 11 AM / touch-of-3PM-close vs EOD across Dhan 2021–2026
3. **Structure upgrade** — put debit spread (replaces naked long put), call credit spread; add VIX regime gate
4. **Data validation** — cross-check Shoonya vs Dhan, resolve Dhan ATM offset → absolute strike, validate VIX timestamp alignment
5. **Short-vol track** — iron condor / short strangle with hybrid Kelly × VIX sizing (paper: `2508.16598`)
6. **ML layer** — only after 3+ years clean OOS; turnover-regularized model with DTE/moneyness/IV/volume/OI

## Data

| Source | Coverage | Role | Location |
|---|---|---|---|
| Shoonya options 1-min | 2024-01-04 – 2026-04-21 | Primary; full strike chain | `data/raw/options/shoonya/nifty/` |
| Dhan options 1-min | 2021 – 2026 | Extended history; ATM±10; embedded IV | `data/raw/options/dhan/nifty/` |
| NSE Bhavcopy EOD | 2008 – 2026 | Long-run validation; 8.07M rows | `data/processed/nse/bhavcopy/fo/` |
| NIFTY spot 1-min | 2015 – 2026 | Signal + calendar source | `data/processed/spot/nifty50_1min_CANONICAL.csv` |
| India VIX 1-min/daily | 2015 – 2026 | Regime gate | `data/processed/market_archive_cleaned/INDIA VIX_*.csv` |

- **No bid/ask anywhere.** Fill proxy: `close ± max(0.05, close × 0.003)`.
- **Quarantine:** `20250925`, `20251224` excluded from all runs.
- Data manifest: `data/manifests/data_inventory_manifest.csv` (502 datasets, 30 GB).

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

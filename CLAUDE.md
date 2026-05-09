# Indian Markets — CLAUDE.md

## Project

Multi-index options backtest engine + research pipeline (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX).
Phase: defined-risk short-vol deployment → live monitoring / scaling.

## Stack

- Python 3.11+, pandas, numpy, pyarrow
- Engine: `options_backtest/` (15 modules, custom, broker-neutral)
- Data: Shoonya 1-min OHLCV (primary), Dhan rolling ATM JSON (2021–2026, cross-check + IV), NSE Bhavcopy EOD (2008–2026)
- Tests: `tests/`, `unittest` only — no pytest

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
6. **Live deployment prep** — broker integration, order sizing, daily signal generation.
6. **OOS monitoring** — track top performers monthly; pause variant if OOS Sharpe < 1.5 for two consecutive months.
7. **Data validation** — cross-check Shoonya vs Dhan, confirm MIDCPNIFTY/BANKNIFTY/FINNIFTY spot CSV coverage post-2024.
8. **ML layer** — only after 3+ years clean OOS; turnover-regularized model with DTE/moneyness/IV/volume/OI.

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

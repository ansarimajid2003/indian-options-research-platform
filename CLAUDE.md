# Indian Markets — CLAUDE.md

## Project

Multi-index options backtest engine + research pipeline (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY).
Phase: short-vol contributor validation → defined-risk structure (credit spreads / iron condors).

## Stack

- Python 3.11+, pandas, numpy, pyarrow
- Engine: `options_backtest/` (15 modules, custom, broker-neutral)
- Data: Shoonya 1-min OHLCV (primary), Dhan rolling ATM JSON (2021–2026, cross-check + IV), NSE Bhavcopy EOD (2008–2026)
- Tests: `tests/`, `unittest` only — no pytest

## Engine Reference

**Before touching any engine code or writing strategies, read `ENGINE_ARCHITECTURE.md` first.**
It is a single-file reference covering all 13 modules, the complete data flow, fill model tiers, strategy interface, how-to-write-strategies guide, and key invariants. Reading it replaces ~15 individual file reads. Always load it at the start of any engine-related session.

## Active Alpha: 3 PM Setup

Bullish 3 PM candle → bearish 3:15 candle → gap-up next day → fade intraday.

- 462 observations, 2015–2024; 40% next-day up (vs 46.9% baseline) — bearish lean
- 73.4% touch-rate of prior 3 PM close; when touched: 27.4% up, avg O-C –0.30%
- Stable across sub-periods (2015–18 / 2019–22 / 2023+)
- **Academic anchor:** Baltussen et al. (2025) — EOD reversal 3.78–6.86 bps/day; Lou et al. (2019) — momentum profits are overnight OR intraday, never both
- **Exit window:** reversal completes in first 1–2 hours; current EOD exit is wrong — test 10:00 AM, 11:00 AM, and touch-of-prior-3PM-close exits

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

1. ~~**Fix bugs + cost model**~~ ✓ Done May 2026 (expiry, calendar, stop gate, tiered slippage, OI thresholds, Sortino/Calmar, asymmetric fills).
2. ~~**Bias fixes**~~ ✓ Done May 2026 (`bars_for()` monthly DTE window, `min_dte` field, `min_leg_premium`, weekly t-stat, `target_profit_pct=None` for theta-carry).
3. ~~**Short-vol contributor validation**~~ ✓ Done May 2026 — bias-clean focused contributors pack: MIDCPNIFTY (Sharpe 2.216, t-stat 3.95, MaxDD -2%), BANKNIFTY (Sharpe 0.738, t-stat 1.58, MaxDD -7.8%), FINNIFTY (Sharpe 0.703), NIFTY (Sharpe 0.224, underperforms BnH). Lot sizing sweet spot: MIDCPNIFTY 7 lots + BANKNIFTY 1–2 lots within 15% DD limit. **Next: convert to defined-risk structures before deployment.**
4. **Structure upgrade** — convert naked short strangles to credit spreads or iron condors; add VIX regime gate (paper: `2508.16598`); re-run contributor validation on defined-risk legs.
5. **Exit window study** (3 PM track) — compare 10 AM / 11 AM / touch-of-prior-3PM-close vs EOD across Dhan 2021–2026.
6. **Data validation** — cross-check Shoonya vs Dhan, validate VIX timestamp alignment, confirm MIDCPNIFTY/BANKNIFTY/FINNIFTY spot CSV coverage post-2024.
7. **ML layer** — only after 3+ years clean OOS; turnover-regularized model with DTE/moneyness/IV/volume/OI.

## Data

| Source | Coverage | Role | Location |
|---|---|---|---|
| Shoonya options 1-min | 2024-01-04 – 2026-04-21 | Primary; full strike chain (NIFTY only) | `data/raw/options/shoonya/nifty/` |
| Dhan options 1-min | 2021–2026 (NIFTY) / 2022–2026 (BN/FN) / 2023–2026 (MCP) | Extended history; ATM±10; embedded IV; all 4 indices | `data/processed/options/dhan/{nifty\|banknifty\|finnifty\|midcpnifty}/` |
| NSE Bhavcopy EOD | 2008 – 2026 | Long-run validation; 8.07M rows | `data/processed/nse/bhavcopy/fo/` |
| NIFTY spot 1-min | 2015 – 2026 | Signal + calendar source | `data/processed/spot/nifty50_1min_CANONICAL.csv` |
| BANKNIFTY spot 1-min | 2022 – 2026 | BnH baseline + canonical fills for BANKNIFTY | `data/processed/spot/banknifty_1min_DHAN.csv` |
| FINNIFTY spot 1-min | 2022 – 2026 | BnH baseline + canonical fills for FINNIFTY | `data/processed/spot/finnifty_1min_DHAN.csv` |
| MIDCPNIFTY spot 1-min | 2023 – 2026 | BnH baseline + canonical fills for MIDCPNIFTY | `data/processed/spot/midcpnifty_1min_DHAN.csv` |
| India VIX 1-min/daily | 2015 – 2026 | Regime gate | `data/processed/market_archive_cleaned/INDIA VIX_*.csv` |

- **No bid/ask anywhere.** Fill proxy: tiered spread on close (ATM 0.3% → expiry-day OTM 2.0%) + OI-scaled slippage multiplier. Zero-volume bars rejected as data artifacts.
- **Weekly expiry discontinuation (non-NIFTY):** BANKNIFTY weekly ended 2024-11-13, FINNIFTY 2024-11-19, MIDCPNIFTY 2024-11-18. Engine falls back to monthly expiry automatically — post-discontinuation trades have DTE 15–30 at entry and different risk character.
- **NIFTY expiry regime break:** NSE changed weekly expiry Thursday → Tuesday on 2025-09-02. `calendar.py` handles this. `summary()` flags `regime_break_in_sample: true` for any backtest spanning this date.
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

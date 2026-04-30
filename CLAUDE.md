# Indian Markets — CLAUDE.md

## Project

NIFTY 50 options trading research and backtest engine.
Current phase: pattern discovery → backtest correctness → strategy expansion.

## Stack

- Python 3.11+, pandas, numpy, pyarrow/parquet
- Data: Shoonya 1-min OHLCV, Dhan expired options (pending), NSE spot/VIX CSV
- Backtest engine: `options_backtest/` (custom, broker-neutral)
- Live collector: `scripts/live/kotak_neo_live_collector.py` (Kotak Neo API)
- No ML frameworks until backtest is validated

## Active Alpha

**3 PM setup:** bullish 3 PM candle → bearish 3:15 candle → gap-up next day → fade intraday.
- 462 observations, 2015–2024
- 40% next-day up rate (vs 46.9% baseline) — bearish lean
- 73.4% touch-rate of prior 3 PM close; when touched: 27.4% up, avg O-C –0.30%
- Stable across sub-periods (2015–18 / 2019–22 / 2023+)

Current backtest results: **all variants negative** (charges dominate, expiry bug present).

## Critical Bugs To Fix First

1. **Expiry multiplication** — one signal date fires across multiple expiry folders; fix: one signal → one expiry via explicit DTE-bucket rule.
2. **next_day_exit** — uses calendar day not next trading day; fix: use spot calendar.
3. **Long option stops** — `entry_credit > 0` gate skips debit strategies; fix: premium-based stops for long options.

## Code Rules

- No new features until the three bugs above are fixed.
- One signal → one expiry, always. Never loop signals inside expiry folders.
- All fills: `close ± slippage`, NSE tick = 0.05, lots round to 75.
- Costs tracked separately from gross PnL; never net them silently.
- Backtest runs must be deterministic for the same config.
- No ML, no curve-fitting until 3+ years of clean walk-forward OOS exists.

## Strategy Expansion Order (after bug fixes)

1. Put debit spread (replace naked long put as default expression)
2. Call credit spread (define-risk short)
3. India VIX / ATM straddle IV regime filter on all entries
4. Short OTM strangle / iron condor (VRP harvesting, defined risk only)
5. IV surface snapshot + skew mean-reversion signals

## Cost Model (updated April 2026)

**Budget 2026 STT hike effective April 1, 2026:**
- Options sell: 0.15% of premium (was 0.10%) — 50% increase
- Options exercise (ITM): 0.15% of intrinsic value (was 0.125%)
- Futures sell: 0.05% of contract value (was 0.02%) — 150% increase

**Full cost stack per NIFTY option round-trip (1 lot, ₹100 premium):**
- STT on sell: ₹11.25 (0.15% × ₹7,500 lot value)
- Exchange fee + SEBI: ~₹5
- Brokerage: ~₹40 flat (Zerodha/Kotak)
- GST 18% on brokerage: ~₹7
- Total per round-trip: ~₹65–80
- Break-even move needed: ~₹1 per unit (65/75 lots)

**All historical backtests used pre-2026 STT — re-run with new rates before any live deployment.**

## Data Rules

- Shoonya 1-min OHLC only — no bid/ask, no depth. Model fills conservatively.
- Cross-validate any clean-looking result against Dhan or NSE samples before trusting.
- Quarantine folders: `20250925`, `20251224` excluded by default.
- Never treat backtest fill as live fill without spread proxy and impact adjustment.

## Subagents

Use subagents in `.claude/agents/` for scoped tasks:
- `researcher.md` — KB + web research for strategy ideas
- `bias-auditor.md` — statistical and backtest bias checks
- `coder.md` — engine/strategy implementation
- `reviewer.md` — code review and correctness gate
- `trade-analyzer.md` — PnL dissection and edge attribution

## Research KB

Qdrant+PostgreSQL at `100.101.17.114:8765` via `kb` MCP. 1,868 quant papers.
Always search before implementing a new strategy idea. Topic filters: `options_derivatives`, `backtesting`, `kelly_criterion`, `risk_management`, `market_microstructure`.

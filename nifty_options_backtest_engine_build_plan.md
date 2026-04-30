# Nifty Options Backtest Engine Build Plan

## Summary

Build a Nifty-native options backtest engine around the collected Shoonya data first, with Dhan support kept as a second vendor path once Data API access is enabled. The core stays broker-neutral and intentionally custom because Nifty expiry rules, strike spacing, charges, sparse strikes, and one-minute OHLC execution assumptions need local control.

Borrow concepts, not code:

- Optopsy: strategy vocabulary, spread-style strategy API, result summaries.
- Options Portfolio Backtester: multi-leg position lifecycle, portfolio accounting, allocation framing.
- NautilusTrader: event-driven loop, deterministic sequencing, bar-based fill philosophy.
- SPX multi-leg backtester: option-chain normalization, DTE/expiry filtering, forward-start robustness checks.

## Implementation Modules

- `options_backtest.data_store`: parse raw Shoonya CSV folders, quarantine bad folders, normalize option and spot bars, write Parquet when available and CSV fallback otherwise.
- `options_backtest.schemas`: canonical dataclasses for contracts, legs, orders, fills, trades, positions, configs, and results.
- `options_backtest.calendar`: Nifty session constants, expiry parsing, DTE, and expiry selection helpers.
- `options_backtest.contract_resolver`: resolve ATM, ATM +/- N, nearest premium, and exact strike contracts from an expiry chain.
- `options_backtest.liquidity`: reject sparse/deep contracts by row count, volume, OI, and distance from ATM.
- `options_backtest.strategy`: pure strategy definitions for single-leg, short straddle, short strangle, and iron condor.
- `options_backtest.engine`: one-minute deterministic backtest loop over spot and option bars with no lookahead.
- `options_backtest.broker_sim`: fill prices, slippage, tick rounding, lot rounding, and transaction-cost model.
- `options_backtest.portfolio`: multi-leg trade accounting and realized P&L.
- `options_backtest.reports`: stable trade ledger, daily P&L, equity curve, and summary metrics.
- `options_backtest.validation`: raw data audit and future cross-vendor comparison hooks.
- `options_backtest.cli`: `normalize`, `audit`, and `run-backtest` commands.

## Data And Execution Rules

Normalize Shoonya option bars to:

`timestamp, trade_date, expiry, strike, option_type, open, high, low, close, volume, oi, ticker`

Normalize spot bars to:

`timestamp, trade_date, open, high, low, close, volume, oi, ticker`

Default exclusions:

- `20250925`
- `20251224`

Default fill model:

- Buy fills at `close + slippage`.
- Sell fills at `close - slippage`.
- Tick rounding uses NSE option tick size `0.05`.
- Quantity rounds to lot size from config.
- If stop and target are both possible in a candle, stop wins by default.
- Costs are tracked separately from gross P&L.

## Initial Acceptance Criteria

- A short straddle can run across valid Shoonya expiries.
- Output includes trade ledger, daily P&L, equity curve, and summary metrics.
- Runs are deterministic for the same config.
- Costs/slippage can be set to zero or enabled and are clearly reflected in net P&L.
- Bad folders are excluded by default.

# Nifty Options Data Audit

Audit date: 2026-04-28

## Source

- Provider: Shoonya public expired Nifty options dataset
- Local path: `data/options/raw/shoonya/nifty/`
- Format: one folder per expiry, strike-wise `CE`/`PE` CSV files plus `nifty_spot.csv`
- Columns observed: `Date, Timestamp, Open, High, Low, Close, Volume, OI, Ticker`

## Inventory

- Expiry folders downloaded: 123
- CSV files: 23,943
- Extracted CSV size: 7.12 GB
- ZIP size: 687.6 MB
- Date range in folder names: `20240104` to `20260421`
- Typical folder size: median around 195 CSVs

## Quality Findings

- Normal sampled expiries have valid OHLC relationships, no duplicate timestamps, and parseable timestamps.
- Near-ATM strike coverage is good in sampled expiries: ATM +/- 500 had 21 strikes at 50-point intervals.
- Deep ITM/OTM files are often sparse; some contracts have only a few rows. Strategies should filter for liquidity.
- `nifty_spot.csv` is repeated inside every expiry folder. It must be deduplicated when building a canonical store.
- Two folders are incomplete/special and should be excluded from default weekly backtests:
  - `20250925`: spot data ends `2025-07-31`, 56 days before expiry.
  - `20251224`: spot data ends `2025-07-31`, 146 days before expiry.

## Suitability

This dataset is enough to build and debug a Nifty options backtest engine, including:

- option-chain loading by expiry
- strike and side resolution
- ATM/OTM strategy selection
- one-minute candle simulation
- stop-loss/target/time-exit logic
- basic liquidity filters using volume and OI

It is good enough for first-pass research on liquid intraday Nifty option strategies from 2024 onward.

It is not enough by itself to fully trust live-trading P&L because:

- coverage is about 2.3 years, not 5 years
- there is no bid/ask spread or market depth
- there is no tick-level path inside each minute
- execution fills must be modeled conservatively
- source should be cross-validated against Dhan/NSE/Kotak for sampled contracts

## Required Before Trusting Strategy Results

1. Build a normalized Parquet store from the raw CSVs.
2. Quarantine incomplete/special folders by default.
3. Filter contracts by minimum rows, volume, OI, and distance from ATM.
4. Use conservative fills: buy at high/close plus slippage, sell at low/close minus slippage, or bar-based pessimistic stop logic.
5. Add brokerage, STT, exchange charges, GST, stamp duty, and tick rounding.
6. Backfill 2021-2023 with Dhan expired options API or another reliable vendor.
7. Cross-check a sample of expiry days against an independent source before treating results as production-grade.

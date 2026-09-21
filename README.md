# Indian Index Options Research Platform

An auditable Python platform for researching and backtesting index-options strategies across NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, and SENSEX.

## What it demonstrates

- Instrument-aware calendars, changing expiry rules, strike steps, and lot-size schedules.
- Normalized option and spot-data loading, contract resolution, liquidity gates, and data-quality audits.
- Explicit trade domain models and broker-style fill, spread, slippage, and charge accounting.
- Strategy plug-ins, trade ledgers, equity curves, risk metrics, and bias-audit tests.
- A separate paper-trading support layer with market-data collection, depth caching, health monitoring, and a read-only dashboard bridge.

## Architecture

```text
Option and spot data
  -> normalization and validation
  -> contract and expiry resolution
  -> strategy legs and liquidity gate
  -> simulated fills and exit logic
  -> trade ledger, P&L, metrics, and reports
```

The historical backtest path remains explicit and separate from paper-trading support. This repository does not claim live trading, real-order execution, or investment performance.

## Getting started

This is a research workspace rather than a packaged distribution. Use Python 3.11+ and install the libraries imported by the script or module you intend to run.

```powershell
python -m options_backtest --help
python -m unittest discover -s tests -v
```

Market data, local credentials, logs, and generated outputs are intentionally excluded from version control. See [ENGINE_ARCHITECTURE.md](ENGINE_ARCHITECTURE.md) and [docs/DATA_LAYOUT.md](docs/DATA_LAYOUT.md) for the system and data layout.

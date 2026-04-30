# Kotak Neo Backtest and Live Deployment Design

Date: 2026-04-28

## What Kotak Neo Gives Us

Kotak Neo API v2 is useful for live execution, live market data, account state,
and order state. It should not be treated as the source of historical backtest
data.

Key API capabilities:

- Authentication: TOTP login creates a view token/session, then MPIN validation
  creates the trade token/session used by post-login APIs.
- Instruments: scrip master downloads map `pSymbol`, `pExchSeg`, `pTrdSymbol`,
  lot size, and expiry metadata. This should be cached daily.
- Quotes: live quote endpoint supports `all`, `ltp`, `ohlc`, `depth`, `oi`,
  `52W`, `circuit_limits`, and `scrip_details`.
- Market websocket: streams live ticks for symbols and indices. Current docs
  state 16 channels and 200 scrip subscriptions at a time.
- Trading: place, modify, cancel, bracket exit, and cover exit APIs.
- Reports: order book, order history, trade book, positions, holdings, limits,
  and margin checks.
- Order/position websocket: pushes order lifecycle and position updates.

Important constraints:

- Kotak support says historical data is not available through Neo Trade API.
- From 2026-04-01, order APIs require static IP whitelisting. The session must be
  created from the same whitelisted IP that sends order requests.
- Static IP validation applies to place/modify/cancel, not to login, data,
  websocket, report, or portfolio APIs.
- Retail algo market orders are discouraged/restricted by the static IP guide;
  live deployment should use controlled limit orders.
- Order APIs are documented as rate limited to 10 orders per second.

## Backtest Engine Shape

Backtesting should be broker-neutral. Kotak should not appear inside strategy
logic. The engine should consume normalized candles and emit normalized orders.

Recommended modules:

- `market_data/`: local historical OHLCV loaders, resampling, corporate/session
  calendar handling, and live-captured candle storage.
- `strategies/`: pure signal code. Example: the current NIFTY 3 PM setup reads
  15-minute bars and emits next-session intent.
- `backtest/engine.py`: event loop over bars, orders, fills, positions, cash,
  and mark-to-market.
- `backtest/broker_sim.py`: slippage, brokerage/fees, partial fills, lot-size
  rounding, stop/limit behavior, and gap handling.
- `execution/models.py`: shared dataclasses for `Signal`, `OrderIntent`,
  `OrderRequest`, `Fill`, `Position`, and `RiskDecision`.
- `reports/`: equity curve, drawdown, trade ledger, daily PnL, hit-rate, MAE/MFE,
  and out-of-sample summaries.

For the current 3 PM candidate:

- The strategy should evaluate the prior day's 15:00 and 15:15 candles after
  market close.
- At next open, it should inspect whether the day opened gap-up.
- The backtest should model a short/fade only after the gap-up condition is
  known. Avoid using future intraday touch data as an entry filter unless the
  same rule can be observed live before entry.
- The prior 3 PM close can be used as an execution/risk level, because it is
  known before the next session.

## Live Deployment Shape

Live should reuse the same strategy code but replace the simulated broker with a
Kotak adapter.

Recommended process:

1. Pre-market bootstrap
   - Verify public IP equals whitelisted IP.
   - Authenticate with Kotak using TOTP and MPIN.
   - Download/cache scrip master.
   - Resolve instruments and lot sizes.
   - Pull limits/margin where relevant.
   - Load yesterday's finalized candles from local storage.

2. Market data service
   - Subscribe to the relevant index/symbol websocket feed.
   - Aggregate ticks into 1-minute candles.
   - Resample to 5-minute/15-minute bars as needed.
   - Persist raw ticks or at least finalized candles.
   - Reconcile first bar/open with quote API if websocket starts late.

3. Strategy runner
   - Runs on finalized bars only.
   - Emits `OrderIntent`, not Kotak-specific payloads.
   - Stores every signal decision with input bar timestamps and values.

4. Risk gate
   - Enforce max daily loss, max trades/day, max open exposure, symbol allowlist,
     quantity/lot-size rounding, price bands, and kill switch.
   - Convert market-like intent into protected limit order.
   - Check margin before sending orders.

5. Kotak execution adapter
   - Convert normalized requests to SDK fields:
     `exchange_segment`, `product`, `price`, `order_type`, `quantity`,
     `validity`, `trading_symbol`, `transaction_type`, `amo`,
     `disclosed_quantity`, `market_protection`, `trigger_price`.
   - Persist client order id, Kotak `nOrdNo`, request payload, response, and
     timestamp.
   - Subscribe to order feed and maintain an order state machine.
   - Poll order/trade report as a fallback reconciliation path.

6. End-of-day
   - Cancel stale open orders.
   - Verify flat/expected positions.
   - Merge live candles into local history.
   - Save trade ledger and compare intended fills versus actual fills.

## Immediate Repo Recommendations

1. Update `docs/setup/kotak_neo_setup.md` and `scripts/live/kotak_neo_live_collector.py` to use the v2
   install path:

   ```text
   pip install "git+https://github.com/Kotak-Neo/Kotak-neo-api-v2.git@v2.0.1#egg=neo_api_client"
   ```

2. Split the current live collector into three pieces:

   - `kotak/auth.py`
   - `kotak/market_data.py`
   - `live/candle_store.py`

3. Add a backtest package before adding live order placement:

   - First reproduce the current NIFTY 3 PM report from the engine.
   - Then add trade simulation and a daily trade ledger.
   - Only then wire the same signal into paper/live execution.

4. Keep historical backtests on the local cleaned archive under `data/processed/market_archive_cleaned/` plus any other approved
   historical provider. Use Kotak live quotes/websocket to extend the dataset
   from today onward.

5. Treat live deployment as two stages:

   - Paper mode: real Kotak data, no order placement, simulated fills.
   - Guarded live mode: real Kotak data, real limit orders, strict daily kill
     switch, order-feed reconciliation, and manual start/stop.

## Source Pointers

- Kotak Neo API v2 SDK: https://github.com/Kotak-Neo/Kotak-neo-api-v2
- Kotak historical data support answer:
  https://www.kotakneo.com/support/how-do-i-get-historical-data/
- Kotak client documentation root:
  https://www.notion.so/Client-documentation-236da70d37e280b3a979fc7be7b003bc
- Scraped local docs are in `.firecrawl/`.

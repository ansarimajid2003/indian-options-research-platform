# DhanHQ-py Data Extraction Review

Date: 2026-05-06

Scope: Review `dhan-oss/DhanHQ-py` v2.2.0 and DhanHQ v2 data APIs for datasets that can improve the Indian index options research pipeline.

Sources:
- DhanHQ-py GitHub README: https://github.com/dhan-oss/DhanHQ-py
- Expired Options Data: https://dhanhq.co/docs/v2/expired-options-data/
- Option Chain: https://dhanhq.co/docs/v2/option-chain/
- Market Quote: https://dhanhq.co/docs/v2/market-quote/
- Live Market Feed: https://dhanhq.co/docs/v2/live-market-feed/
- Full Market Depth: https://dhanhq.co/docs/v2/full-market-depth/
- Historical Data: https://dhanhq.co/docs/v2/historical-data/

## Verdict

Dhan is useful in two different ways:

1. Historical research source: `rollingoption` expired options data remains the core backtest dataset. It gives 1-minute OHLCV, OI, IV, rolling strike, and spot for ATM-relative options. It does not provide historical bid/ask depth.
2. Live forward-collection source: option chain, market quote, live market feed, and full market depth can build a new forward dataset with top bid/ask, 5-level book, 20-level book, or 200-level book snapshots. That data is the best path to calibrating slippage and fill realism, but it only exists from the point we start collecting it.

## Extractable Data

### 1. Expired Options Data

Endpoint/library method:
- REST: `POST /charts/rollingoption`
- Python: `dhan.expired_options_data(...)`

Useful fields:
- `open`, `high`, `low`, `close`
- `volume`
- `oi`
- `iv`
- `strike`
- `spot`
- `timestamp`

Limits and shape:
- Up to 5 years.
- Up to 30 days per call.
- Intervals: `1`, `5`, `15`, `25`, `60`.
- Index options near expiry support `ATM`, `ATM+1..ATM+10`, and `ATM-1..ATM-10`.
- Other contracts are narrower, generally `ATM+/-3`.

Research use:
- Keep as the main historical backtest source.
- Use `strike` to resolve Dhan rolling ATM offset into absolute strikes.
- Use `iv_raw` and `iv_clean` for volatility regime and spread selection.
- Use `oi` and `volume` for liquidity filtering and slippage multipliers.

Current repo status:
- `scripts/download/download_dhan_expired_options.py` already uses this endpoint safely through `DHAN_ACCESS_TOKEN`.
- `scripts/data/validate_dhan_options.py` already preserves raw IV and adds `iv_clean`, `iv_spike_flag`, and `iv_zero_flag`.
- Coverage currently exists for NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY in the processed Dhan layout.

Gap:
- No historical bid/ask or depth is available from this endpoint.

### 2. Option Chain Snapshots

Endpoint/library method:
- REST: `POST /optionchain`
- Python: `dhan.option_chain(...)`

Useful fields by strike and side:
- `security_id`
- `last_price`
- `top_bid_price`, `top_bid_quantity`
- `top_ask_price`, `top_ask_quantity`
- `oi`, `previous_oi`
- `volume`, `previous_volume`
- `implied_volatility`
- greeks: `delta`, `theta`, `gamma`, `vega`
- `average_price`
- `previous_close_price`

Limits:
- One unique option-chain request every 3 seconds.
- Returns the whole chain for one underlying and one expiry.

Research use:
- Forward live chain archive for all active expiries.
- Intraday spread surface: bid/ask width by DTE, moneyness, premium, OI, volume, time of day.
- Vendor Greeks/IV sanity checks against Dhan expired IV.
- Strike universe discovery: map absolute option security IDs needed for market quote/depth capture.

Best repo integration:
- New collector under `scripts/download/download_dhan_option_chain.py`.
- Store append-only NDJSON under `data/raw/options/dhan_live/option_chain/{symbol}/{YYYYMMDD}.ndjson`.
- Poll every 1-5 minutes during market hours for active research symbols.

### 3. Market Quote Snapshots

Endpoint/library methods:
- REST: `/marketfeed/ltp`, `/marketfeed/ohlc`, `/marketfeed/quote`
- Python: `ticker_data`, `ohlc_data`, `quote_data`

Useful quote fields:
- `last_price`, `last_quantity`, `last_trade_time`
- `average_price`
- `volume`
- `oi`, `oi_day_high`, `oi_day_low`
- `buy_quantity`, `sell_quantity`
- `depth.buy[]` and `depth.sell[]`: price, quantity, orders
- `ohlc`
- circuit limits and net change

Limits:
- Up to 1000 instruments per request.
- Rate limit: 1 request per second.

Research use:
- Snapshot-based 5-level book archive for selected option instruments.
- Better than option-chain top-of-book when we want multi-level executable size.
- Useful for forward calibration of fill assumptions without opening websocket infrastructure.

Best repo integration:
- Use option-chain `security_id` discovery first.
- Select ATM +/- N active strikes, then call `quote_data({"NSE_FNO": [...]})`.
- Store normalized book snapshots keyed by `symbol`, `expiry`, `strike`, `option_type`, `security_id`, and timestamp.

### 4. Live Market Feed

Endpoint/library class:
- WebSocket: `wss://api-feed.dhan.co`
- Python: `MarketFeed`

Subscription modes:
- `Ticker`: LTP and last trade time.
- `Quote`: LTP, last quantity, ATP, volume, total buy/sell quantity, OHLC; OI arrives as an additional packet for derivatives.
- `Full`: quote + OI + 5-level market depth in one packet.

Limits:
- Up to 5 WebSocket connections per user.
- Up to 5000 instruments per connection.
- Subscription messages are batched up to 100 instruments.

Research use:
- Best forward source for event-style live market data across many option contracts.
- Practical for collecting selected NIFTY/BANKNIFTY/FINNIFTY/MIDCPNIFTY chains every tick.
- Use `Full` mode if we want 5-level bid/ask and OI together.

Best repo integration:
- New live collector with reconnect, heartbeat, append-only raw packets, and normalized parquet/CSV rollups.
- Use low-frequency periodic snapshots from normalized stream for backtest calibration, not tick-level replay at first.

### 5. Full Market Depth

Endpoint/library class:
- 20 level: `wss://depth-api-feed.dhan.co/twentydepth`
- 200 level: `wss://full-depth-api.dhan.co/twohundreddepth`
- Python: `FullDepth`

Depth structure:
- 20 level: bid and ask packets, 20 rows each; each row has price, quantity, number of orders.
- 200 level: bid and ask packets, 200 rows each; each row has price, quantity, number of orders.

Limits:
- Only NSE equity and derivatives segments are enabled.
- 20-level depth: up to 50 instruments in one connection.
- 200-level depth: only 1 instrument per connection.
- More than 5 websockets can trigger server-side disconnection.

Research use:
- 20-level depth is the practical research feed for option execution calibration.
- 200-level depth is useful for focused experiments on one contract at a time, not for whole-chain collection.
- Build metrics: top spread, weighted spread for target lot size, depth imbalance, price levels needed for 1x/5x/7x lots, queue concentration, liquidity cliff distance.

Best repo integration:
- Start with 20-level depth for selected contracts, not 200-level.
- Use 200-level only for high-value focused days such as expiry-day ATM options or the exact entry/exit legs of the active alpha.

### 6. Historical Spot, VIX, Futures, and Active Instruments

Endpoint/library methods:
- REST: `/charts/intraday`, `/charts/historical`
- Python: `intraday_minute_data`, `historical_daily_data`

Useful fields:
- `timestamp`, `open`, `high`, `low`, `close`, `volume`
- `open_interest` when requested for F&O instruments

Limits:
- Intraday supports 1/5/15/25/60 minute intervals.
- Intraday API windows can be large; docs recommend storing data locally.

Research use:
- Continue using this for index spot and India VIX refresh.
- Can add active futures OHLC/OI to support basis, futures-led regime features, and cross-checks against spot.

## Recommended Extraction Priority

1. Keep and harden expired-options backfill.
   - This is still the only historical minute-level options source we have from Dhan.
   - Add SDK parity only if it reduces maintenance; the current raw REST scripts are already clear and resumable.

2. Add Dhan option-chain collector.
   - Lowest complexity path to top bid/ask, Greeks, IV, OI, and absolute `security_id`.
   - It replaces the unofficial NSE scraper for most live chain needs.

3. Add Dhan market quote collector for selected contracts.
   - Gives 5-level book snapshots and executable-size information for up to 1000 instruments per REST call.
   - Best next step for calibrating the engine's current tiered spread model.

4. Add Dhan 20-level FullDepth collector.
   - Use for active alpha legs only.
   - Store as forward evidence for slippage and liquidity realism.

5. Use 200-level FullDepth only for narrow experiments.
   - One instrument per connection makes it too expensive for broad chain capture.

## Backtest Engine Impact

Near-term:
- Do not rewrite historical fills to use bid/ask until we actually have forward bid/ask samples.
- Add a `spread_calibration` research dataset from option-chain/quote/depth snapshots.
- Use observed live spreads to tune `broker_sim.py` tiers by moneyness, DTE, premium, OI, volume, and time-of-day.

Medium-term:
- Add optional columns to Dhan processed/live normalized schema:
  - `bid_price_1`, `ask_price_1`, `bid_qty_1`, `ask_qty_1`
  - `depth_buy_levels`, `depth_sell_levels` as nested JSON/parquet list columns or separate long tables
  - `spread_abs`, `spread_pct`, `mid`, `microprice`
  - `executable_buy_price_lots_1/5/7`, `executable_sell_price_lots_1/5/7`

High risk:
- Mixing live post-2026 bid/ask calibration into 2021-2026 historical backtests can create regime drift. Treat it as execution model calibration, not as exact historical reconstruction.

## Credential Handling

Do not commit tokens. Keep using:

```powershell
$env:DHAN_ACCESS_TOKEN = "<token>"
```

The token supplied for this review was not written into repo files.

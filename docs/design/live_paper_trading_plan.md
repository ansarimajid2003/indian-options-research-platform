# Live Paper Trading + Order Book Collection - Design Plan

**Date:** 2026-05-10
**Status:** Revised after Dhan connectivity test and zimaos deployment review
**Author:** Ansari

---

## 1. Context & Motivation

Wing-6 Iron Condor across NIFTY / FINNIFTY / MIDCPNIFTY / SENSEX has been
validated with all engine bias fixes applied:

| Metric | Value |
|---|---:|
| Trades | 901 |
| Net PnL | +Rs 195,631 |
| CAGR | 4.3% at 1x lots |
| Sharpe | 2.746 |
| t-stat | 5.302 |
| MaxDD | -1.0% |
| Win% | 60% |

Next phase: 1-month live paper trading run to:
1. Validate that real-market executable fills are close enough to the backtest slippage model.
2. Collect forward order book / top-of-book data for microstructure research.
3. Build the operational scaffold for live deployment.

No real orders are placed. All fill logic runs inside our engine against live market data.

This run is for execution validation, not statistical validation. One month is not enough to
trust live Sharpe.

### 1.1 Dhan Connectivity Test

Authenticated Dhan connectivity was tested after market close on 2026-05-09 with the supplied
token, without writing the token to disk.

| Test | Result | Notes |
|---|---|---|
| Token decode | PASS | Token expiry: 2026-05-10 12:56:28 UTC |
| REST `POST /optionchain/expirylist` | PASS | NIFTY returned `status=success`, 18 active expiries, first `2026-05-12`, last `2030-12-31` |
| Live feed websocket | PASS | `wss://api-feed.dhan.co?...` opened and remained open after a small IDX_I/NIFTY subscription |
| 20-depth websocket | PASS | `wss://depth-api-feed.dhan.co/twentydepth?...` opened and remained open after a small NSE_EQ subscription |

Because the market was closed, this proves auth/connectivity only. It does not prove live tick
delivery, packet parsing, quote freshness, or sustained session stability.

Do not persist Dhan tokens in repo files, logs, markdown, JSON, parquet metadata, shell history
artifacts, or reports. Live scripts must read `DHAN_ACCESS_TOKEN` / `DHAN_TOKEN` and redact all
token-like strings from logs.

### 1.2 Server Deployment Environment Check

The intended always-on collection host is reachable as `zimaos`.

Observed on 2026-05-10:

| Check | Value |
|---|---|
| Hostname | `ZimaOS` |
| OS/kernel | Linux 6.12.25 x86_64 |
| Uptime at check | about 1 day 4 hours |
| Timezone | `Asia/Kolkata` |
| Clock sync | `systemd-timesyncd` enabled and synchronized |
| LAN IP | `192.168.0.254` |
| Public outbound IPv4 | `183.83.38.115` |
| Public IP owner | ACT / Atria Convergence Technologies |
| VPN/overlay interface | `ztsxxcg7jv` at `10.244.0.1` |
| Host-level Tailscale binary | not present |
| Host-level Tailscale `100.x` IP | not present |
| Default route | `eth0` via `192.168.0.1` |
| SSD data mount | `/DATA`, ext4 on `sda8`, 222 GB total, 174 GB free |
| WD hard-drive mount | `/media/WD-Storage`, btrfs on `sdc`, 466 GB total, 216 GB free |
| Alternate WD path | `/DATA/.media/WD-Storage` |
| Other hard-drive mount | `/media/Toshiba-Storage`, btrfs on `sdb`, 466 GB total, 462 GB free |
| Root filesystem | `/`, 1.2 GB total, 100% used |
| Python | `/usr/bin/python3`, Python 3.12.12 |
| Python packages | trading/dashboard stack not installed yet (`pandas`, `numpy`, `pyarrow`, `websockets`, `streamlit` missing) |
| Deployment tools | `git`, `rsync`, `curl`, `systemctl`, `tmux`, `docker`, `ss`, `lsof` available |

Dhan whitelist must use the **public outbound IPv4** seen by Dhan, not the private VPN/overlay
address. For the current server route, that is `183.83.38.115`.

Do not whitelist `10.244.0.1`, `192.168.0.254`, or any Tailscale/ZeroTier/private overlay IP;
Dhan's public API servers will not see those as the source address.

Before real order API use, confirm with the ISP/router that `183.83.38.115` is static. If it is
dynamic, either buy a static public IP from the ISP, host the collector on a VPS with a static
public IP, or route all Dhan API traffic through a known static egress host. Re-check
`curl -4 https://api.ipify.org` from `zimaos` before setting or relying on the Dhan whitelist.

Because `/` is full, **nothing live should write to root-filesystem paths**. The SSD-backed
`/DATA` mount is suitable for the repo checkout, virtual environment, and small operational
state. Large live-market artifacts should go to the WD hard drive. Preferred layout:

```text
/DATA/live-paper/indian-markets/              # repo, venv, configs, small caches
/media/WD-Storage/indian-markets-live/        # raw packets, order book parquet, paper outputs
```

The repo-local `data/live/` path should be a symlink or config alias pointing to
`/media/WD-Storage/indian-markets-live/`. Preflight must fail if that path resolves to `/`,
`/root`, `/tmp`, or the SSD `/DATA` tree for large raw/depth output. Use
`/DATA/.media/WD-Storage` only if `/media/WD-Storage` is unavailable; both point to the same WD
drive on the current ZimaOS mount layout.

### 1.3 Deployment Role Split

The live paper run is server-primary.

| Machine | Role | Allowed responsibilities | Forbidden responsibilities |
|---|---|---|---|
| `zimaos` server | Primary backend runner | Dhan websocket connections, option-chain REST discovery, order-book collection, paper strategy execution, daily JSON/markdown/parquet outputs, dashboard process | Real broker order placement, unredacted token logging, writing to root filesystem |
| Laptop | Control room and development box | Code development, SSH access, dashboard viewing, log inspection, post-run analysis, emergency manual supervision | Primary market-hours data collection, duplicate Dhan websocket collectors, authoritative paper fills |

Operational rules:

- The server owns the market-hours loop from pre-open through EOD flush.
- The laptop may disconnect, sleep, reboot, or move networks without affecting collection.
- The dashboard is a viewer of server-side files; it must not become a required part of the trading loop.
- Only one authoritative collector may run per day. Do not run laptop and server collectors at the
  same time unless a future failover protocol explicitly elects one active writer.
- Laptop fallback is manual and exceptional: stop the server runner first, record the cutover time,
  then start a laptop runner that writes to a separate fallback session directory.

---

## 2. Locked Deployment Profile

The live paper run must test the validated alpha, not a nearby variant.

Strategy implementation:

```python
IronCondor(
    short_call_offset=2,
    long_call_offset=8,
    short_put_offset=-2,
    long_put_offset=-8,
)
```

Report label: `IronCondorWing6`.

Global trade rules:

| Field | Value |
|---|---|
| Entry time | 09:20 IST |
| Exit time | 15:20 IST |
| Exit style | Same-day time exit only |
| `stop_loss_pct` | `None` |
| `target_profit_pct` | `None` |
| `trail_trigger_pct` | `None` |
| `trail_stop_pct` | `None` |
| `min_dte` | 1 |
| Costs | Full `ChargesConfig.for_date(today)` |
| Partial structure | Forbidden; never enter fewer than 4 legs |
| Substitute expiry/strike | Forbidden; skip instead |

Per-symbol locked profile:

| Symbol | Trade? | Expiry rule | Entry filter | VIX bucket rule | Lots | Live depth source |
|---|---:|---|---|---|---:|---|
| NIFTY | Yes | `expiry_type="week"` | `min_dte=1`, no `max_dte` | require VIX >= 13 | 1 | Dhan 20-depth |
| FINNIFTY | Yes | `expiry_type="month"` | `min_dte=1`, `max_dte=7` | skip bucket `10-13` | 1 | Dhan 20-depth |
| MIDCPNIFTY | Yes | `expiry_type="month"` | `min_dte=1`, `max_dte=7` | skip bucket `22-30` | 1 | Dhan 20-depth |
| SENSEX | Yes | `expiry_type="week"` | `min_dte=1`, `max_dte=2` | no bucket skip | 1 | top-of-book only |
| BANKNIFTY | No | n/a | excluded | excluded | 0 | no collection |

Profile name: `wing6_4x1_all_vix_filtered`.

Any future scaled profile must be a separate named config. Do not use a global `--lots` argument
for this deployment profile.

---

## 3. Dhan API Behavior

### 3.1 REST Option Chain

Official behavior used by this plan:

| Endpoint | Purpose | Required headers | Rate limit |
|---|---|---|---|
| `POST /optionchain/expirylist` | Fetch active expiries for an underlying | `access-token`, `client-id` | one unique request every 3 seconds |
| `POST /optionchain` | Fetch full chain for an underlying + expiry | `access-token`, `client-id` | one unique request every 3 seconds |

Request body shape:

```json
{
  "UnderlyingScrip": 13,
  "UnderlyingSeg": "IDX_I"
}
```

Option chain provides top bid/ask, OI, volume, LTP, IV, Greeks, and option `security_id`.
It is the required security-id discovery layer for live collection.

### 3.2 Live Market Feed

Endpoint:

```text
wss://api-feed.dhan.co?version=2&token=<redacted>&clientId=<client_id>&authType=2
```

Rules:

| Rule | Value |
|---|---|
| Max connections | 5 per user |
| Instruments per connection | 5,000 |
| Subscribe batch size | 100 instruments per JSON message |
| Server ping | every 10 seconds |
| Disconnect if silent | about 40 seconds |
| Response format | binary packets |

Use this feed for spot index LTP, VIX, SENSEX top-of-book support, and fallback quote
information. If top-of-book is needed from this feed, use `Full` mode where available.

### 3.3 20-Level Full Market Depth

Endpoint:

```text
wss://depth-api-feed.dhan.co/twentydepth?token=<redacted>&clientId=<client_id>&authType=2
```

Hard constraints:

| Rule | Value |
|---|---|
| Scope | NSE Equity and NSE Derivatives only |
| Instruments per connection | 50 |
| Subscribe request code | 23 |
| Response format | binary packets |
| Header size | 12 bytes |
| Level size | 16 bytes |
| Bid packet code | 41 |
| Ask packet code | 51 |
| Levels | 20 bid levels and 20 ask levels, sent separately |

SENSEX/BSE options are not eligible for Dhan 20-depth. SENSEX paper fills must use
top-of-book from option-chain/live-feed until a separate BSE depth source is proven.

### 3.4 Connection Budget

| Feed | Endpoint | Instruments | Connections |
|---|---|---:|---:|
| Quote/full feed | `wss://api-feed.dhan.co` | spot indices, VIX, active options, SENSEX top-of-book support | 1 |
| 20-depth NSE options | `wss://depth-api-feed.dhan.co/twentydepth` | 102 NSE option contracts | 3 |
| Reserved diagnostic socket | either | emergency only | 1 |
| Total budget | | | 5 |

The runner must refuse to start if another process already consumes the Dhan websocket budget.

---

## 4. Live Fill Policy

Paper PnL must use executable prices, not midpoint marks.

| Price field | Use |
|---|---|
| `mark_mid` | Diagnostics only: `(best_bid + best_ask) / 2` |
| `top_executable_price` | BUY at best ask, SELL at best bid |
| `depth_vwap` | VWAP through 20-depth levels for the required quantity |
| `paper_fill_price` | `depth_vwap` when available; otherwise top executable price |

Execution rules:

- BUY uses ask-side depth.
- SELL uses bid-side depth.
- If available cumulative depth cannot fill the required quantity, skip the symbol trade.
- If one leg is stale or missing, skip the full 4-leg condor for that symbol.
- Never value paper PnL from `mark_mid`.
- Store both mark and executable fields for every fill.

Fill JSON must include:

```json
{
  "timestamp": "2026-05-12T09:20:03.412+05:30",
  "symbol": "NIFTY",
  "security_id": "12345",
  "side": "BUY",
  "quantity": 65,
  "price": 125.40,
  "mark_mid": 124.95,
  "top_bid": 124.50,
  "top_ask": 125.40,
  "depth_vwap": 125.40,
  "fill_basis": "top_ask",
  "spread_pct": 0.72,
  "quote_age_ms": 742,
  "depth_available_qty": 1800
}
```

---

## 5. Architecture

### 5.1 Overview

```text
scripts/live/run_paper_trading.py
  |
  +-- scripts/live/collect_order_book.py
  |     |
  |     +-- Dhan 20-depth WS x 3
  |     +-- writes raw depth packets + normalized parquet
  |     +-- updates DepthCache
  |
  +-- options_backtest/paper_engine.py
        |
        +-- LiveDhanContractResolver
        +-- DepthCache
        +-- Dhan live feed WS x 1
        +-- locked profile config
        +-- paper fills + EOD report
```

### 5.2 Reused Engine Modules

| Module | File | Reuse |
|---|---|---|
| Strategy | `options_backtest/strategy.py` | `IronCondor(...)` with wing-6 offsets |
| Calendar | `options_backtest/calendar.py` | `expiry_on_or_after()`, `is_trading_day()`, lot size schedules |
| Charges | `options_backtest/broker_sim.py` | `ChargesConfig.for_date()` only |
| Portfolio | `options_backtest/portfolio.py` | `finalize_trade()` |
| Reports | `options_backtest/reports.py` | `summary()`, `daily_pnl()`, `equity_curve()` after JSON-to-ledger conversion |
| Schemas | `options_backtest/schemas.py` | `Trade`, `Fill`, `Contract`, `Side`, `Leg` |
| VIX | `options_backtest/volatility_filter.py` | bucket definitions and no-lookahead semantics |

Historical backtest behavior must not change while implementing live paper trading.

---

## 6. New Modules

### 6.1 `options_backtest/live_resolver.py` - `LiveDhanContractResolver`

Purpose: live resolver with the same conceptual interface as `DhanContractResolver`, backed by
websocket quote state and option-chain security-id discovery.

Required interface:

```python
class LiveDhanContractResolver:
    def atm_strike(self, timestamp: pd.Timestamp) -> int: ...
    def resolve_atm_offset(self, timestamp, offset_steps: int, option_type: OptionType) -> Contract: ...
    def bar_at(self, contract: Contract, timestamp: pd.Timestamp) -> pd.Series | None: ...
    def bars_for(self, contract: Contract) -> pd.DataFrame: ...
```

Rules:

- `atm_strike()` uses fresh spot LTP, rounded by `get_instrument_spec(symbol).strike_step`.
- `resolve_atm_offset()` must resolve through option-chain `security_id`.
- `bar_at()` returns latest quote state with `open`, `high`, `low`, `close`, `volume`, and `oi`.
- `close` means live LTP for compatibility with existing engine code.
- `bars_for()` returns the rolling intraday quote deque for liquidity diagnostics.

Freshness limits:

| Data | Max age |
|---|---:|
| Spot quote | 5 seconds |
| Option quote | 5 seconds |
| VIX quote | 60 seconds |
| Depth snapshot | 5 seconds |

If data is stale, skip the affected symbol. Do not fall back to old prices for entry.

### 6.2 `options_backtest/depth_cache.py` - Shared Depth Cache

Purpose: one in-memory source of truth for current bid/ask/depth, shared by collector and paper
engine.

Required interface:

```python
class DepthCache:
    def update_bid_packet(self, security_id: str, packet_ts: pd.Timestamp, levels: list[DepthLevel]) -> None: ...
    def update_ask_packet(self, security_id: str, packet_ts: pd.Timestamp, levels: list[DepthLevel]) -> None: ...
    def snapshot(self, security_id: str) -> DepthSnapshot | None: ...
    def is_ready(self, security_id: str, max_age_seconds: int = 5) -> bool: ...
    def executable_price(self, security_id: str, side: Side, quantity: int) -> float | None: ...
```

Concurrency:

- Thread-safe updates and reads.
- Bid and ask packets are timestamped independently.
- A snapshot is ready only when both sides are present and fresh.

### 6.3 `options_backtest/paper_engine.py` - `PaperTradingEngine`

Daily flow:

```text
09:00  Connect live feed and depth collectors.
09:10  Abort/skip decisions if required feeds are not connected.
09:15  Fetch option-chain expiry/security-id map for all profile symbols.
09:17  Confirm all intended legs can be resolved.
09:20  Evaluate per-symbol VIX/DTE/bucket filters and enter eligible condors.
09:21  Monitor open paper positions using live quotes.
15:20  Force time exit for all open positions.
15:25  Final stale-exit deadline if fresh exit quotes were not available at 15:20.
15:31  Flush collectors and generate EOD report.
```

No stop-loss, no target, and no DTE=1 forced exit are allowed for this profile.

Output:

```text
data/live/paper_trades/{YYYYMMDD}.json
data/live/reports/{YYYYMMDD}_paper_summary.md
data/live/logs/{YYYYMMDD}.log
```

On `zimaos`, these logical repo paths must resolve to the WD storage root:

```text
/media/WD-Storage/indian-markets-live/paper_trades/{YYYYMMDD}.json
/media/WD-Storage/indian-markets-live/reports/{YYYYMMDD}_paper_summary.md
/media/WD-Storage/indian-markets-live/logs/{YYYYMMDD}.log
```

### 6.4 `scripts/live/collect_order_book.py`

Purpose: record 20-level bid/ask depth for eligible NSE option contracts and update
`DepthCache`.

20-depth universe:

| Symbol | Strike step | ATM +/- 8 strikes | CE+PE | Contracts |
|---|---:|---:|---:|---:|
| NIFTY | 50 | 17 | 2 | 34 |
| FINNIFTY | 50 | 17 | 2 | 34 |
| MIDCPNIFTY | 25 | 17 | 2 | 34 |
| Total | | | | 102 |

SENSEX is not in this collector.

Storage:

```text
data/live/raw_depth_packets/{YYYYMMDD}/
data/live/order_book/{YYYYMMDD}/{symbol}_{expiry}_{strike}_{CE|PE}.parquet
data/live/order_book_1min/{YYYYMMDD}/{symbol}_{expiry}_{strike}_{CE|PE}.parquet
```

On `zimaos`, these directories must resolve to:

```text
/media/WD-Storage/indian-markets-live/raw_depth_packets/{YYYYMMDD}/
/media/WD-Storage/indian-markets-live/order_book/{YYYYMMDD}/{symbol}_{expiry}_{strike}_{CE|PE}.parquet
/media/WD-Storage/indian-markets-live/order_book_1min/{YYYYMMDD}/{symbol}_{expiry}_{strike}_{CE|PE}.parquet
```

Normalized columns:

```text
timestamp
bid_p1..bid_p20, bid_q1..bid_q20, bid_o1..bid_o20
ask_p1..ask_p20, ask_q1..ask_q20, ask_o1..ask_o20
best_bid, best_ask, mid, spread_abs, spread_pct
total_bid_qty, total_ask_qty, imbalance
depth_vwap_buy_lots_1, depth_vwap_sell_lots_1
depth_qty_at_best_bid, depth_qty_at_best_ask
```

Storage rules:

- Require at least 100 GB free on `/media/WD-Storage` before starting a month-long collection run.
- Abort startup if `data/live` does not resolve to `/media/WD-Storage/indian-markets-live`.
- Flush raw and normalized data at least every 60 seconds.
- Never drop raw packets silently.
- If writer backpressure occurs, log `collector_backpressure` and mark affected instruments
  unusable for fill validation during that interval.

### 6.5 `scripts/live/run_paper_trading.py`

Single entry point:

```bash
python scripts/live/run_paper_trading.py --profile wing6_4x1_all_vix_filtered
```

Process flow:

```python
if not calendar.is_trading_day(today):
    log("Market holiday - skipping")
    sys.exit(0)

profile = load_locked_profile("wing6_4x1_all_vix_filtered")
depth_cache = DepthCache()

await asyncio.gather(
    collect_order_book(profile, today, depth_cache),
    paper_trading_engine(profile, today, depth_cache),
)

generate_eod_report(today)
```

### 6.6 `scripts/live/dhan_connection_check.py`

Purpose: repeatable smoke test for credentials and Dhan connectivity.

Checks:

1. Decode token expiry locally, without printing token.
2. Call `POST /optionchain/expirylist`.
3. Open live-feed websocket and send one small subscription.
4. Open 20-depth websocket and send one small NSE subscription.
5. Print redacted PASS/FAIL lines only.

This script must never place, modify, cancel, or inspect real orders.

### 6.7 `scripts/live/health_monitor.py`

Purpose: independent uptime and integrity monitor for the server-side live paper stack.

This monitor runs as its own process on `zimaos`. It does not open Dhan websockets, does not place
orders, and does not share in-memory state with the collector. It watches process state, snapshot
freshness, output-file growth, disk health, clock sync, and public IP drift, then sends push alerts
when the backend is unhealthy.

Alert transport:

| Channel | Use | Secrets |
|---|---|---|
| Telegram bot | Primary alert channel to the user's phone | `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` |
| Sentry | Python exception capture with full tracebacks and local variable state; fires Telegram alert on unhandled exception | `SENTRY_DSN` |
| Local log | Durable alert trail for post-day diagnosis | no secrets |
| Dashboard snapshot | Read-only current alert state | no secrets |

Checks:

| Check | Healthy condition | Alert severity |
|---|---|---|
| Runner process | `run_paper_trading.py` or its `systemd` unit active during market hours | critical |
| Collector heartbeat | `latest_process_health.json` updated within 30 seconds | critical |
| Feed state snapshot | `latest_feed_state.json` updated within 15 seconds during open market | critical |
| Depth snapshot | `latest_depth_cache.json` updated within 15 seconds for active NSE symbols | critical |
| Raw packet flush | newest raw packet file mtime < 90 seconds during open market | warning |
| Parquet flush | newest normalized parquet mtime < 180 seconds during open market | warning |
| Quote freshness | less than 95% fresh instruments for 2 consecutive checks | critical |
| Depth readiness | less than 95% ready channels for 2 consecutive checks after 09:10 | critical |
| WD mount | `/media/WD-Storage` mounted and writable | critical |
| WD free space | free space >= 20 GB intraday, >= 100 GB pre-run | critical/warning |
| Clock sync | `timedatectl` synchronized and drift <= 2 seconds | critical |
| Public IP | `curl -4 https://api.ipify.org` matches expected whitelisted IP | critical before start, warning intraday |
| Token expiry | decoded token expiry is after planned EOD plus buffer | critical before start |
| Error log scan | new unclassified error/traceback lines in live log | warning |

Poll interval:

| Check category | Interval |
|---|---|
| Critical checks (process alive, feed freshness, quote freshness, depth readiness, WD mount, clock sync) | Every 15 seconds |
| Slow checks (disk free space, public IP, error log scan) | Every 60 seconds |

Notification rules:

- Send one startup "monitor online" message before market preflight.
- Send one "backend healthy at market open" message after all required feeds are connected.
- Send alerts immediately for critical failures.
- Throttle repeated alerts by `(severity, component, reason)`: 2 minutes during entry/exit windows (09:10–09:30 and 15:15–15:30); 10 minutes otherwise.
- Send recovery messages when a previously unhealthy component becomes healthy again.
- Send an EOD summary with uptime percentage, alert counts, data-gap minutes, WD free space, and
  final artifact paths.
- Never include access tokens, client IDs, full request URLs, or raw packet payloads in messages.

Sentry exception integration:

Initialize at process startup:

```python
import sentry_sdk
sentry_sdk.init(
    dsn=os.environ["SENTRY_DSN"],
    environment="production",
    server_name="zimaos",
    shutdown_timeout=5,
    traces_sample_rate=0.0,  # no tracing; preserve free error quota
    send_default_pii=False,
)
```

All unhandled Python exceptions are captured automatically with full tracebacks and local
variable state. Sentry routes these to Telegram via its own native Telegram integration,
separate from the Healthchecks.io dead-man-switch channel. `SENTRY_DSN` must live in server
environment variables only — never in repo files, logs, or markdown.

Outputs:

```text
data/live/alerts/{YYYYMMDD}_alerts.jsonl
data/live/snapshots/latest_alert_state.json
data/live/reports/{YYYYMMDD}_uptime_summary.md
```

On `zimaos`, these logical paths must resolve to `/media/WD-Storage/indian-markets-live/...`.

### 6.8 External Server-Down Heartbeat

Purpose: detect complete `zimaos` failure: power loss, ISP outage, OS freeze, SSH unreachable,
or a crash that prevents local scripts from running.

The local `health_monitor.py` cannot report a total server outage because it dies with the server.
Add an external dead-man-switch monitor in a different failure domain. `zimaos` sends a heartbeat
every 60 seconds; if the external service does not receive heartbeats within the configured grace
window, it sends a Telegram alert.

Recommended v1 — two-layer approach:

| Layer | Tool | Purpose | Rationale |
|---|---|---|---|
| Dead-man-switch | Hosted Healthchecks.io | Server-down detection | Purpose-built heartbeat; 20 free monitors; simple `GET` ping; fires alert when `zimaos` is unreachable from internet |
| Exception capture | Sentry hosted (free tier) | Python exception tracking | Automatic traceback capture on unhandled errors; Telegram alert integration; 5,000 errors/month free; covers software failures that don't kill the process |
| Ping source | `health_monitor.py` side task | Sends heartbeats from `zimaos` | Stops pinging if server or process dies |
| Alert channel | Telegram (both services) | User's phone | Healthchecks.io → Telegram for server-down; Sentry → Telegram for exceptions |
| Grace window | 3 minutes | Jitter tolerance | Allows 3 missed pings before alerting |

Sentry vs Healthchecks.io for dead-man-switch specifically:

| Feature | Healthchecks.io | Sentry Crons |
|---|---|---|
| Free monitors | 20 | 1 |
| Integration | One-line `requests.get(url)` | Requires `sentry-sdk` + `capture_checkin()` |
| Telegram | Via webhook | Native integration |
| Exception tracking | No | Yes |
| Self-hostable | Yes (lightweight) | Yes (requires 16 GB RAM — too heavy for `zimaos`) |

**Verdict:** use Healthchecks.io for dead-man-switch (20 free monitors, no SDK dependency). Use
Sentry for Python exception tracking (automatic, free 5k errors/month, native Telegram). Do not
replace Healthchecks.io with Sentry Crons — the free tier only includes 1 cron monitor, and its
SDK must be running to send pings, which defeats the purpose of an external dead-man-switch when
the process itself is frozen or the OS has crashed.

Self-hosted alternatives:

| Option | When to use | Caveat |
|---|---|---|
| Healthchecks self-hosted on a cheap VPS | Best open-source self-owned version | Must not run on `zimaos`; otherwise it cannot detect `zimaos` death |
| Uptime Kuma Push monitor on a cheap VPS / separate always-on device | Good UI and many notification integrations | Also must run outside `zimaos`; if hosted on the same server it is only local monitoring |
| Laptop-hosted monitor | Not recommended | Laptop sleep/shutdown recreates the same reliability problem |

Heartbeat behavior:

- `zimaos` sends `GET $EXTERNAL_HEARTBEAT_URL` every 60 seconds from 08:55 to 15:35 IST on
  trading days; every 5 minutes at all other times, 7 days a week. The off-hours ping catches
  weekend crashes before Monday preflight fails.
- Use Healthchecks.io start/fail URL suffixes: send `$EXTERNAL_HEARTBEAT_URL/start` at 08:55
  when the monitoring window opens; send `$EXTERNAL_HEARTBEAT_URL/fail` before any intentional
  shutdown (e.g., preflight abort). The plain `GET $EXTERNAL_HEARTBEAT_URL` is the per-minute
  success ping.
- Append session metadata as query parameters for diagnostics (does not affect alerting logic):
  `?session_date=20260514&open_positions=4&last_flush_ts=1715671892`
- The ping URL is secret and must live only in server environment variables or an untracked env
  file as `EXTERNAL_HEARTBEAT_URL`.
- Missing heartbeat for 3 minutes triggers Telegram alert: `zimaos heartbeat missed`.
- Recovery heartbeat triggers Telegram recovery message.
- The local health monitor also writes the latest successful external heartbeat timestamp to
  `data/live/snapshots/latest_alert_state.json`.
- Never put the heartbeat UUID/URL in git, logs, markdown reports, screenshots, or dashboard text.

Manual intervention runbook:

When the user receives a "zimaos heartbeat missed" alert during market hours:

| Elapsed since alert | Action |
|---|---|
| < 5 min | Wait — transient network jitter; monitor for recovery message |
| 5–15 min | SSH to `zimaos`; run `systemctl status live-paper` and `systemctl status health-monitor`; restart whichever process is dead |
| > 15 min, SSH reachable | Check `latest_open_positions.json` for today's date; restart engine in resume mode (it detects the checkpoint automatically); record cutover time in the day's log |
| > 15 min, SSH unreachable | Power-cycle `zimaos` via router/IPMI if available; then allow systemd auto-restart; verify checkpoint loaded on restart |
| Cannot restart by 12:30 IST | Do not restart the paper engine; remaining session data is too incomplete for fill validation; mark day as `partial_session: true` in post-run analysis |

**Critical rule before any manual restart:** confirm today's `latest_open_positions.json` exists
before starting `run_paper_trading.py`. A restart without a checkpoint enters new positions on
top of ghost positions — the day becomes unanalyzable. The engine detects and loads the checkpoint
automatically (see Section 6.9); do not bypass this check.

Outputs:

```text
data/live/alerts/{YYYYMMDD}_external_heartbeat.jsonl
```

This external heartbeat is mandatory before relying on unattended market-hours collection.

### 6.9 Crash Recovery Protocol

Purpose: define how the system handles a mid-session server crash or process kill, so that resumed
data and positions are analyzable rather than silently corrupt.

#### Position State Checkpointing

`PaperTradingEngine` writes a position checkpoint after every fill event, not only at EOD.

File path: `data/live/snapshots/latest_open_positions.json` (logical path on WD storage).

Write pattern: write to `latest_open_positions.tmp` → flush → atomically rename to
`latest_open_positions.json`. On Linux, rename is atomic; a crash during write always leaves
the `.tmp` file, which is safe to discard on startup.

Checkpoint content:

```json
{
  "session_date": "2026-05-14",
  "written_at": "2026-05-14T11:03:05.412+05:30",
  "open_positions": [
    {
      "symbol": "NIFTY",
      "expiry": "2026-05-15",
      "short_call_strike": 24200,
      "long_call_strike": 24600,
      "short_put_strike": 23800,
      "long_put_strike": 23400,
      "entry_time": "2026-05-14T09:20:03.412+05:30",
      "entry_fills": {}
    }
  ]
}
```

#### Startup Resume Detection

On startup, `run_paper_trading.py` checks for a same-day checkpoint before entering the pre-open
phase:

```python
checkpoint = load_today_position_checkpoint()  # None if no same-day file
if checkpoint and checkpoint.has_open_positions:
    log("RESUME MODE: loaded open positions from interrupted session")
    engine.resume_from_checkpoint(checkpoint)
    # Skip entry phase; proceed directly to position monitoring
else:
    engine.fresh_start()
```

If the engine restarts after 15:20 IST and finds open positions in the checkpoint, it exits all
positions immediately using last available quotes and flags `forced_stale_exit=true` on all
resumed fills.

The EOD summary includes a crash-gap block when resume mode was used:

```json
{
  "resumed_after_crash": true,
  "crash_gap_start": "2026-05-14T11:03:12+05:30",
  "crash_gap_end": "2026-05-14T11:58:44+05:30",
  "gap_minutes": 55
}
```

Days with `gap_minutes > 30` are excluded from spread and liquidity statistics in the post-month
fill validation (Section 8). Days with `gap_minutes <= 30` may be included with a
`data_quality: partial` flag.

#### Data Gap Annotation

When `collect_order_book.py` starts or restarts mid-session, it writes a gap sentinel record to
the affected instrument JSONL before resuming normal writes:

```json
{"type": "data_gap", "symbol": "NIFTY", "gap_start": "2026-05-14T11:03:12+05:30", "gap_end": "2026-05-14T11:58:44+05:30", "reason": "process_restart", "gap_minutes": 55}
```

Without this sentinel, a parquet file that appears continuous may contain a silent hole. All
downstream analysis scripts must reject files that contain gap sentinels exceeding the
`gap_minutes > 30` threshold before computing spread or depth statistics.

#### Systemd Restart Policy

Both managed processes declare explicit restart behaviour in their unit files:

```ini
# health-monitor.service
[Service]
Restart=on-failure
RestartSec=10
StartLimitIntervalSec=300
StartLimitBurst=5

# live-paper.service
[Service]
Restart=on-failure
RestartSec=45
StartLimitIntervalSec=600
StartLimitBurst=5
```

`RestartSec=45` for the paper engine gives the network time to stabilize before reconnecting to
Dhan websockets. The engine then self-recovers via the position checkpoint. `RestartSec=10` for
the health monitor because it has no Dhan connections to re-establish.

The `StartLimitBurst=5` guard prevents restart storms: if the process fails 5 times within the
interval, systemd stops retrying. The process-alive check in `health_monitor.py` catches this
state and fires a critical Telegram alert.

---

## 7. Hard Failure And Skip Rules

| Situation | Behavior |
|---|---|
| REST auth fails before market open | Abort the day |
| Option-chain expiry/security-id discovery fails by 09:17 | Skip affected symbol |
| Live-feed websocket not connected by 09:10 | Abort the day |
| NSE 20-depth websocket not connected by 09:10 | Skip NIFTY/FINNIFTY/MIDCPNIFTY; SENSEX may still run if healthy |
| Spot quote stale at entry | Skip affected symbol |
| VIX stale/missing | Skip symbols whose rule requires VIX |
| Any leg quote stale/missing at entry | Skip affected symbol |
| Any leg lacks executable depth/size | Skip affected symbol |
| One leg enters but another leg fails | Invalid state; abort symbol and log `partial_entry_blocked` |
| Exit quote stale at 15:20 | Wait for next fresh quote until 15:25 |
| Exit still stale at 15:25 | Use last executable quote and flag `forced_stale_exit=true` |
| Websocket reconnect during open position | Continue monitoring; fill only when all legs are fresh |
| Computer clock drift > 2 seconds | Abort entries until clock is corrected |
| WD free space < 20 GB during session | Stop raw collection after flush, keep paper engine running, flag data-quality failure |
| Health monitor not running by 09:00 | Abort live paper start until monitor is restored |
| Telegram alert test fails before 09:00 | Abort live paper start unless explicitly waived and logged |
| Health snapshots stop updating intraday | Health monitor sends critical alert; paper engine continues using its own internal state |
| External heartbeat test fails before 09:00 | Abort live paper start unless explicitly waived and logged |
| External heartbeat missed intraday | External service sends Telegram alert; local system may be unreachable |
| Engine restarts mid-session without a same-day position checkpoint | Abort restart; do not enter fresh positions; wait for manual intervention |
| Same-day position checkpoint found on startup | Load checkpoint; enter resume mode; skip entry phase; log `resumed_after_crash=true` |
| Collector restart creates a data gap > 30 minutes | Write gap sentinel; mark day `data_quality: partial`; exclude from spread/liquidity stats |

Every skip must have one of these explicit reason codes. No unclassified skips.

---

## 8. Post-Month Analysis Plan

At end of the run, produce a fill model validation report:

| Comparison | Source A | Source B | Method |
|---|---|---|---|
| PnL attribution | Paper executable net PnL | Backtest net PnL on same dates | Difference by symbol, leg, date, and reason |
| Spread accuracy | Observed executable spread | Backtest tier assumption | MAE by premium/DTE/moneyness/OI bucket |
| Depth sufficiency | Available executable quantity | Required profile quantity | Pass/fail by leg |
| OI slippage proxy | Observed depth and spread | `oi_slippage_multiplier()` | Correlation plus bucket hit rate |
| Entry timing | 09:20 fills | 09:21 hypothetical fills | Mean premium and spread difference |
| Staleness | quote/depth gaps | entry/exit windows | gap rate and affected PnL |

Deployment gates:

- At least 95% of intended entries either filled or skipped for a classified reason.
- At least 99% quote freshness compliance during entry/exit windows.
- Median executable spread no worse than current backtest tier assumption per bucket.
- No unclassified websocket gaps during entry/exit windows.
- Paper-vs-backtest PnL difference is at least 90% explained by spread, stale quote, skipped
  trade, or contract-resolution categories.

Do not approve live scale-up from one-month Sharpe.

---

## 9. Implementation Schedule

| Week | Dates | Work |
|---|---|---|
| Week 1 | May 12-16 | `dhan_connection_check.py`, locked profile config, `DepthCache`, live security-id discovery |
| Week 2 | May 19-23 | `live_resolver.py`, `collect_order_book.py`, packet parsing, raw/normalized writes |
| Week 3 | May 26-30 | `paper_engine.py`, JSON-to-ledger adapter, replay/dry-run verification |
| Week 4 | Jun 2-6 | First full live paper sessions; monitor gaps and fill anomalies |
| Post-run | Jun 9+ | Fill validation report and deployment readiness verdict |

---

## 10. Files To Create

| File | Purpose |
|---|---|
| `configs/live/wing6_4x1_all_vix_filtered.json` | Locked profile config |
| `scripts/live/dhan_connection_check.py` | Redacted REST + websocket smoke test |
| `options_backtest/live_resolver.py` | Live quote/contract resolver |
| `options_backtest/depth_cache.py` | Shared bid/ask/depth cache and executable VWAP logic |
| `options_backtest/paper_engine.py` | Live paper trading engine |
| `scripts/live/collect_order_book.py` | NSE 20-depth recorder |
| `scripts/live/run_paper_trading.py` | Daily runner |
| `scripts/live/health_monitor.py` | Independent uptime/data-integrity monitor with Telegram alerts |
| `scripts/live/paper_json_to_ledger.py` | Adapter from paper JSON to reports-compatible ledger |

Files with zero intended behavior changes:

- `options_backtest/engine.py`
- `options_backtest/dhan_loader.py`
- `options_backtest/reports.py`
- `options_backtest/broker_sim.py`
- `options_backtest/strategy.py`
- `options_backtest/calendar.py`

If one of those files must change, implementation pauses for a focused review.

---

## 11. Server Deployment Layout

The deployment target is `zimaos`. The laptop is not part of the market-hours critical path.

Recommended server layout:

```text
/DATA/live-paper/indian-markets/                  # repo checkout
/DATA/live-paper/indian-markets/.venv/            # Python runtime
/DATA/live-paper/indian-markets/configs/live/     # locked profile config
/media/WD-Storage/indian-markets-live/            # large live artifacts
/media/WD-Storage/indian-markets-live/raw_depth_packets/
/media/WD-Storage/indian-markets-live/order_book/
/media/WD-Storage/indian-markets-live/order_book_1min/
/media/WD-Storage/indian-markets-live/paper_trades/
/media/WD-Storage/indian-markets-live/reports/
/media/WD-Storage/indian-markets-live/logs/
/media/WD-Storage/indian-markets-live/snapshots/
/media/WD-Storage/indian-markets-live/alerts/
```

Runtime setup prerequisites:

- Create the repo checkout and virtual environment on `/DATA`, not under `/root`.
- Install the Python runtime stack into `.venv`; the current server Python has no `pandas`,
  `numpy`, `pyarrow`, `websockets`, `streamlit`, or `plotly`.
- Configure `data/live` as a symlink or explicit runtime path to
  `/media/WD-Storage/indian-markets-live`.
- Keep Dhan credentials in server environment variables or an untracked local env file loaded by
  the process manager.
- Keep Telegram credentials in server environment variables or the same untracked local env file:
  `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.
- Keep the off-server heartbeat ping URL in the same secret environment layer as
  `EXTERNAL_HEARTBEAT_URL`; never write it to repo docs or logs.
- Keep the Sentry DSN in the same secret environment layer as `SENTRY_DSN`; never write it to
  repo files, logs, or markdown.
- Run collector/engine, health monitor, and dashboard under `systemd` or `tmux`; prefer `systemd`
  once the commands are stable. Set `Restart=on-failure`, `RestartSec=45` for the paper engine
  and `RestartSec=10` for the health monitor; add `StartLimitBurst=5` guards on both (see
  Section 6.9 for full unit file snippets).

Server preflight must verify:

- `/media/WD-Storage` is mounted, writable, and has at least 100 GB free before the month-long run.
- `data/live` resolves to `/media/WD-Storage/indian-markets-live`.
- `/DATA` has enough free space for the repo, `.venv`, Streamlit cache, and small logs.
- `timedatectl` reports `System clock synchronized: yes`.
- `curl -4 https://api.ipify.org` still returns the whitelisted public IP.
- No other Dhan collector process is consuming the websocket budget.
- Telegram test alert succeeds before the market session starts.
- `health_monitor.py` is running before `run_paper_trading.py` starts.
- External heartbeat test succeeds before the market session starts.

---

## 12. Web Dashboard

A single-page Streamlit dashboard provides real-time visibility into the server-side live paper run.
It is read-only: no orders are placed, no Dhan websocket is opened, and no engine state is
modified. The dashboard is useful for monitoring, but the collector and paper engine must continue
normally if the dashboard is closed or the laptop disconnects.

### 12.1 Technology Choice

| Layer | Choice | Rationale |
|---|---|---|
| Framework | Streamlit | Pure Python; no separate JS build; native pandas/plotly support; fits the project's manual-dependency workflow |
| Data bridge | `options_backtest/dashboard_bridge.py` | Isolates Streamlit from live websocket threads; reads only flushed files and snapshot exports |
| Storage root | `/media/WD-Storage/indian-markets-live` | Keeps large live artifacts on the WD hard drive rather than SSD/root |
| Alerts | `scripts/live/health_monitor.py` + Telegram | Pushes backend failures to the user even when the laptop/dashboard is offline |
| Server-down monitor | External Healthchecks.io heartbeat | Alerts when `zimaos` itself stops sending heartbeats |
| Caching | `st.cache_data` + short TTL | Live snapshots refreshed every 5-10 s; no high-frequency tick rendering |

No new JavaScript build step, no npm, no React/Vue. The dashboard is launched as a standard Python script.

### 12.2 Dashboard Layout

Version 1 is a live-ops dashboard only. Historical exploration and broad backtest comparison are
deferred until the server runner has survived full market sessions.

```text
┌─────────────────────────────────────────────────────────────┐
│  Indian Markets Dashboard — Wing-6 Paper Live Ops             │
├─────────────────────────────────────────────────────────────┤
│  [ Live Paper Trading ] [ Server Health ] [ Storage ]        │
└─────────────────────────────────────────────────────────────┘
```

#### Tab 1 — Live Paper Trading

Purpose: monitor the current session as it runs.

**Header tiles (auto-refresh 5 s):**
- Session date, market status (`PRE_OPEN / OPEN / CLOSED / HOLIDAY`)
- Server hostname, process status, current public outbound IP
- WD free space and last successful flush time
- Open P&L (gross and net, today)
- Quote freshness (% of watched instruments with age < 5 s)
- Depth readiness (% of 102 NSE depth channels ready)

**Main panels:**
- **Open Positions table**: symbol, expiry, strikes (CE/PE short/long), entry time, entry premium, current mark, current spread, unrealised gross/net PnL, quote age per leg.
- **Intraday Equity Curve**: live updating line chart of today's cumulative net PnL vs time (09:20–15:20).
- **Signal Log**: scrollable feed of every skip/entry/exit with timestamp, symbol, reason code, and filter state (VIX, DTE, bucket).
- **Depth Health**: per-symbol heatmap showing quote age and available depth quantity for each of the four legs.
- **Storage / Writer Health**: latest raw packet flush, latest parquet flush, backpressure flag,
  WD free space, and unresolved write errors.
- **Alert State**: active alerts, last Telegram notification status, alert counts by severity,
  and recovery timestamps.

**Sidebar:**
- Select profile (`wing6_4x1_all_vix_filtered` locked by default)
- Refresh interval (5 s / 10 s / 30 s / manual)
- Audio alert toggle on `partial_entry_blocked` or `forced_stale_exit`

Data sources:
- `data/live/paper_trades/{YYYYMMDD}.json` (logical path on WD storage)
- `DepthCache` snapshot export → `data/live/snapshots/depth_cache_{HHMMSS}.json`
- Live feed state export → `data/live/snapshots/feed_state_{HHMMSS}.json`

- `data/live/snapshots/latest_alert_state.json`
- `data/live/alerts/{YYYYMMDD}_alerts.jsonl`

#### Future Tab 2 - Historical Data Explorer

Historical Data Explorer and Backtests are version-2 tabs. They should be built only after the
server runner is stable for full market sessions, and should read canonical JSON/CSV/parquet
artifacts rather than parsing markdown reports as primary data.

Purpose: browse and visualise any archived dataset without writing analysis scripts.

**Sidebar:**
- Dataset type: `Spot 1-min`, `Dhan Options 1-min`, `Order Book (raw depth)`, `Order Book 1-min`, `Bhavcopy EOD`, `India VIX`
- Symbol filter: `NIFTY`, `BANKNIFTY`, `FINNIFTY`, `MIDCPNIFTY`, `SENSEX`
- Date range picker
- For options: expiry selector, strike selector, CE/PE toggle
- For order book: depth level slider (1–20), show spread/imbalance toggle

**Main panels:**
- **Time-series chart**: OHLC or line, with optional volume/OI overlay. Uses plotly for zoom/pan.
- **Data table**: paginated view of the underlying DataFrame (first 1,000 rows to keep render fast).
- **Quick stats**: mean, min, max, std of close, total volume, mean spread, mean imbalance for the selected window.
- **Download**: CSV export of the current filtered view.

Data sources:
- `data/processed/spot/*_1min_*.csv`
- `data/processed/options/dhan/{symbol}/`
- `data/live/order_book/` (logical path on WD storage)
- `data/processed/nse/bhavcopy/fo/`
- `data/processed/market_archive_cleaned/INDIA VIX_*.csv`

#### Future Tab 3 - Backtests

Purpose: compare backtest results, inspect trade ledgers, and validate live paper sessions against historical runs.

**Sidebar:**
- Backtest directory: `reports/backtests/` or `data/live/reports/`
- Select one or more backtest JSON/ledger files (multi-select)
- Filter: symbol, date range, strategy

**Main panels:**
- **Metrics comparison table**: Trades, Net PnL, CAGR, Sharpe, Sortino, Calmar, MaxDD, Win%, PF, t-stat. One row per selected backtest.
- **Equity-curve overlay**: normalised equity curves for all selected backtests on the same axis.
- **Trade distribution**: histogram of per-trade net PnL; optional symbol facet.
- **Underwater / drawdown chart**: peak-to-trough drawdown over time.
- **Monthly returns heatmap**: rows = years, columns = months, cells = net return %.
- **Trade ledger drill-down**: click a backtest row to expand a sortable/filterable trade table (entry/exit date, symbol, strikes, gross, charges, net, reason).

Data sources:
- `reports/backtests/*.json` (engine summary + ledger)
- `data/live/reports/{YYYYMMDD}_paper_summary.json` (canonical machine-readable summary)
- `data/live/paper_trades/*.json` (converted to ledger format on the fly)

### 12.3 Dashboard Bridge (`options_backtest/dashboard_bridge.py`)

The bridge enforces a strict read-only boundary between the dashboard and live engine state.

```python
class DashboardBridge:
    def live_paper_ledger(self, session_date: date) -> pd.DataFrame: ...
    def depth_health_snapshot(self) -> pd.DataFrame: ...
    def feed_health_snapshot(self) -> pd.DataFrame: ...
    def process_health_snapshot(self) -> dict: ...
    def alert_state(self) -> dict: ...
    def alert_history(self, session_date: date) -> pd.DataFrame: ...
    def signal_log(self, session_date: date) -> pd.DataFrame: ...
    def backtest_summaries(self, root: Path) -> pd.DataFrame: ...
    def historical_spot(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def historical_options(self, symbol: str, expiry: date, strike: int, opt_type: str) -> pd.DataFrame: ...
    def historical_order_book(self, session_date: date, symbol: str, expiry: date, strike: int, opt_type: str) -> pd.DataFrame: ...
```

Rules:
- Live methods poll the filesystem; they never hold websocket connections.
- File reads use `pd.read_json(..., lines=True)` or `pd.read_parquet` with `columns=` projection to minimise I/O.
- If a live file is locked by the writer, the bridge retries once after 100 ms and returns the last known good snapshot rather than crashing the dashboard.
- Snapshot writers must write `*.tmp`, flush, then atomically rename to `latest_*.json`.
- All timestamps are rendered in IST (`Asia/Kolkata`).

### 12.4 Files To Create

| File | Purpose |
|---|---|
| `scripts/live/dashboard.py` | Streamlit entry point; page layout, widgets, and chart rendering |
| `options_backtest/dashboard_bridge.py` | Read-only data accessors; isolates Streamlit from engine internals |

### 12.5 Launch Commands

```bash
# On zimaos: live paper trading + order book collector
cd /DATA/live-paper/indian-markets
.venv/bin/python scripts/live/run_paper_trading.py --profile wing6_4x1_all_vix_filtered

# On zimaos: independent health monitor and Telegram alerts
.venv/bin/python scripts/live/health_monitor.py --profile wing6_4x1_all_vix_filtered

# On zimaos: dashboard bound to localhost only
.venv/bin/streamlit run scripts/live/dashboard.py --server.port 8501 --server.address 127.0.0.1

# On laptop: view dashboard through SSH tunnel
ssh -L 8501:127.0.0.1:8501 zimaos
```

On `zimaos`, the collector/paper engine and dashboard should be separate managed processes.
Stopping the dashboard must never affect the collector or paper engine. Laptop disconnection must
only close the tunnel/browser view.

### 12.6 Security & Safety

- Dashboard is read-only. No broker API calls, no token storage, no order placement.
- Bind Streamlit to `127.0.0.1` by default and access it through SSH tunneling.
- If the dashboard is exposed beyond localhost, run it behind a reverse proxy with authentication
  or a private overlay network; do not expose raw Streamlit to the public internet.
- Redact token-like strings from any log or snapshot file that the dashboard might display.

---

## 13. Implementation Checklist

Step-by-step build and deployment tracker. Work left of the dashed line in each phase before starting the next.

Legend: `[ ]` = not started · `[~]` = in progress · `[x]` = done

---

### Phase 0 — Server Preparation ✓ DONE 2026-05-10

- [x] SSH into `zimaos` and confirm Python 3.12, git, rsync, docker, tmux, systemctl are present
- [x] Confirm `/media/WD-Storage` is mounted (`df -h`) and has >= 100 GB free — **216 GB free**
- [x] Confirm `/DATA` has >= 10 GB free for repo + venv — **173 GB free**
- [x] `curl -4 https://api.ipify.org` from `zimaos` → **183.83.38.115** — confirm matches Dhan whitelist or request update
- [ ] Confirm ISP assigns a static IP to `zimaos` (or plan VPS/static egress alternative) — **pending manual ISP check**
- [x] Create directory `/DATA/live-paper/` on `zimaos` (not under `/root`)
- [x] `git clone` repo into `/DATA/live-paper/indian-markets/` — bare remote + working checkout via `git push zimaos main`
- [x] `python3 -m venv /DATA/live-paper/indian-markets/.venv`
- [x] Install runtime stack: `pandas` 3.0.2, `numpy` 2.4.4, `pyarrow` 24.0.0, `websockets` 16.0, `requests` 2.33.1, `sentry-sdk` 2.59.0, `streamlit` 1.57.0, `plotly` 6.7.0
- [x] Create WD storage layout:
  ```
  /media/WD-Storage/indian-markets-live/{raw_depth_packets,order_book,order_book_1min,paper_trades,reports,logs,snapshots,alerts}/
  ```
- [x] Create repo-level symlink: `data/live` → `/media/WD-Storage/indian-markets-live`
- [x] Verify symlink resolves correctly: `readlink -f data/live` → `/media/WD-Storage/indian-markets-live` ✓

---

### Phase 1 — Credentials and External Services

> **Static IP outstanding:** `183.83.38.115` is likely a dynamic IP (ACT residential default). Buy the ACT static IP addon (~₹230/month) before relying on the Dhan whitelist. Call 1800-266-1111 or use the MyACT app. Re-verify public IP with `curl -4 https://api.ipify.org` from `zimaos` after activation before submitting Dhan whitelist request.

- [ ] Create Telegram bot via BotFather → record `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in server env file (not in repo)
- [ ] Create Healthchecks.io account (free tier) → create one monitor → set grace to 3 minutes → enable Telegram notification → record ping UUID as `EXTERNAL_HEARTBEAT_URL` in server env file
- [ ] Create Sentry account (free tier) → create Python project → enable Telegram native integration → record DSN as `SENTRY_DSN` in server env file
- [ ] Write server-side untracked env file `/DATA/live-paper/indian-markets/.env.live` with:
  ```
  DHAN_ACCESS_TOKEN=...
  DHAN_CLIENT_ID=...
  TELEGRAM_BOT_TOKEN=...
  TELEGRAM_CHAT_ID=...
  SENTRY_DSN=...
  EXTERNAL_HEARTBEAT_URL=...
  ```
- [ ] Add `.env.live` to `.gitignore`
- [ ] Confirm none of the above secrets appear in `git status` or `git diff`
- [ ] Send a manual test Telegram message using `curl` to confirm bot and chat ID are correct

---

### Phase 2 — Locked Profile Config and Smoke Test ✓ DONE 2026-05-10

- [x] Create `configs/live/` directory
- [x] Write `configs/live/wing6_4x1_all_vix_filtered.json` — matches Section 2 exactly; scrip IDs confirmed from existing downloader (NIFTY=13, FINNIFTY=27, MIDCPNIFTY=442, SENSEX=51, VIX=21)
- [x] Write `scripts/live/dhan_connection_check.py` (Section 6.6):
  - [x] Token decode + expiry print (redacted; shows remaining days and masked client_id)
  - [x] `POST /optionchain/expirylist` for NIFTY → expect `status=success`
  - [x] Open `wss://api-feed.dhan.co` → send one IDX_I/NIFTY subscription → confirm packet received
  - [x] Open `wss://depth-api-feed.dhan.co/twentydepth` → send one IDX_I/NIFTY subscription → confirm packet received
  - [x] All output lines are PASS/FAIL only; no token or client-ID printed
- [ ] Run `dhan_connection_check.py` from `zimaos` → all four checks PASS — **pending: add DHAN credentials to `.env.live` on server first**

---

### Phase 3 — Depth Cache and Live Resolver (Week 2, May 19–23)

- [ ] Write `options_backtest/depth_cache.py` (Section 6.2):
  - [ ] `DepthLevel` dataclass
  - [ ] `DepthSnapshot` dataclass (bid levels, ask levels, timestamps, `best_bid`, `best_ask`, `spread_pct`)
  - [ ] `DepthCache` class — thread-safe; separate bid/ask timestamping
  - [ ] `update_bid_packet()` and `update_ask_packet()` methods
  - [ ] `snapshot()` returns `None` if either side is missing
  - [ ] `is_ready()` checks both sides present and within `max_age_seconds`
  - [ ] `executable_price()` — VWAP walk through ask levels for BUY, bid levels for SELL; returns `None` if cumulative quantity insufficient
  - [ ] Unit tests: freshness expiry, VWAP calculation, partial-depth skip
- [ ] Write `options_backtest/live_resolver.py` (Section 6.1):
  - [ ] `LiveDhanContractResolver` class
  - [ ] `atm_strike()` — uses fresh spot LTP; rounds by instrument strike step
  - [ ] `resolve_atm_offset()` — resolves through option-chain `security_id`; raises if stale
  - [ ] `bar_at()` — returns latest quote as `pd.Series` with `open`, `high`, `low`, `close`, `volume`, `oi`; `close` = live LTP
  - [ ] `bars_for()` — returns rolling intraday quote deque
  - [ ] Freshness limits enforced (spot 5 s, option 5 s, VIX 60 s, depth 5 s)
  - [ ] Returns `None` / raises `StaleQuoteError` when stale; no fallback to old prices
- [ ] Write `scripts/live/collect_order_book.py` (Section 6.4):
  - [ ] Manages 3× `wss://depth-api-feed.dhan.co/twentydepth` connections (≤50 instruments each)
  - [ ] Subscribes to 102 NSE option contracts (NIFTY 34 + FINNIFTY 34 + MIDCPNIFTY 34)
  - [ ] Binary packet parser: 12-byte header, 16-byte levels, bid code 41, ask code 51
  - [ ] Calls `depth_cache.update_bid_packet()` and `update_ask_packet()` on every packet
  - [ ] Writes raw packets to `data/live/raw_depth_packets/{YYYYMMDD}/`
  - [ ] Writes normalized parquet (all 67 columns per Section 6.4) to `data/live/order_book/{YYYYMMDD}/`
  - [ ] Writes 1-min OHLCV aggregates to `data/live/order_book_1min/{YYYYMMDD}/`
  - [ ] Flushes every 60 seconds
  - [ ] Writes gap sentinel to JSONL on restart (Section 6.9)
  - [ ] Logs `collector_backpressure` and marks instruments unusable on write lag
  - [ ] Aborts startup if `data/live` does not resolve to `/media/WD-Storage/indian-markets-live`
  - [ ] Aborts startup if WD free space < 100 GB at start of month-long run

---

### Phase 4 — Paper Engine and Runner (Week 3, May 26–30)

- [ ] Write `options_backtest/paper_engine.py` — `PaperTradingEngine` (Section 6.3):
  - [ ] Daily flow: connect → preflight → security-id discovery → VIX/DTE/bucket filter → entry at 09:20 → monitoring → time exit at 15:20 → stale deadline 15:25 → EOD flush
  - [ ] Entry: evaluates all four symbols; logs skip with explicit reason code for each filter miss
  - [ ] Fill: uses `depth_cache.executable_price()` for VWAP fill; falls back to `top_executable_price`; stores both `mark_mid` and executable fields
  - [ ] Position checkpoint: writes `latest_open_positions.tmp` → flush → `os.rename()` to `latest_open_positions.json` after every fill
  - [ ] Resume detection: loads same-day checkpoint on startup; skips entry phase if open positions found
  - [ ] Forced stale exit: if quote still stale at 15:25, marks `forced_stale_exit=true`
  - [ ] No stop-loss, no target, no DTE=0 exits allowed for this profile
  - [ ] Fill JSON has all fields from Section 4 schema
  - [ ] Outputs: `data/live/paper_trades/{YYYYMMDD}.json`, `data/live/logs/{YYYYMMDD}.log`
- [ ] Write `scripts/live/paper_json_to_ledger.py` (Section 10):
  - [ ] Reads `{YYYYMMDD}.json` → converts to `reports`-compatible ledger format
  - [ ] Passes through `reports.summary()` and `reports.daily_pnl()` without modifying engine internals
  - [ ] Outputs: `data/live/reports/{YYYYMMDD}_paper_summary.md` and `_paper_summary.json`
- [ ] Write `scripts/live/run_paper_trading.py` — single entry point (Section 6.5):
  - [ ] Holiday check via `calendar.is_trading_day(today)`
  - [ ] Loads locked profile from `configs/live/wing6_4x1_all_vix_filtered.json`
  - [ ] Initialises `DepthCache`, starts `collect_order_book` and `paper_trading_engine` via `asyncio.gather`
  - [ ] Calls `generate_eod_report()` after market close
  - [ ] Hard failure rules from Section 7 all enforced
  - [ ] Preflight: WD mount check, `data/live` symlink check, clock sync check, public IP check, websocket budget check, Telegram test alert, external heartbeat test, health monitor running check
- [ ] Dry-run test on `zimaos` (no market hours): run the full startup sequence and verify each preflight gate fires correctly on a simulated failure

---

### Phase 5 — Health Monitor and External Heartbeat

- [ ] Write `scripts/live/health_monitor.py` (Section 6.7):
  - [ ] All 13 checks from Section 6.7 implemented
  - [ ] 15-second poll for critical checks; 60-second poll for slow checks
  - [ ] Telegram alert delivery with throttling by `(severity, component, reason)`
  - [ ] 2-minute throttle during entry/exit windows (09:10–09:30, 15:15–15:30); 10-minute otherwise
  - [ ] "Monitor online" startup message
  - [ ] "Backend healthy at market open" message after all feeds connected
  - [ ] Recovery messages when previously unhealthy component recovers
  - [ ] EOD uptime summary with uptime %, alert counts, data-gap minutes, WD free space, artifact paths
  - [ ] Sends Healthchecks.io `$EXTERNAL_HEARTBEAT_URL/start` at 08:55, plain ping every 60 s during market hours, every 5 min off-hours, `$EXTERNAL_HEARTBEAT_URL/fail` on preflight abort
  - [ ] Sentry `sentry_sdk.init()` at process startup with `send_default_pii=False`
  - [ ] Outputs: `data/live/alerts/{YYYYMMDD}_alerts.jsonl`, `data/live/snapshots/latest_alert_state.json`, `data/live/reports/{YYYYMMDD}_uptime_summary.md`
  - [ ] Snapshot files written atomically (`.tmp` → rename)
- [ ] Test: kill the health monitor mid-run → confirm it restarts cleanly
- [ ] Test: block outbound Healthchecks.io ping for 3 minutes → confirm Telegram "heartbeat missed" alert arrives
- [ ] Test: send a `sentry_sdk.capture_exception()` from `zimaos` → confirm Sentry captures it and Telegram alert fires

---

### Phase 6 — systemd Services

- [ ] Create `/etc/systemd/system/live-paper.service`:
  ```ini
  [Unit]
  Description=Wing-6 Iron Condor Paper Trading Engine
  After=network-online.target
  Wants=network-online.target

  [Service]
  User=<service_user>
  WorkingDirectory=/DATA/live-paper/indian-markets
  EnvironmentFile=/DATA/live-paper/indian-markets/.env.live
  ExecStart=/DATA/live-paper/indian-markets/.venv/bin/python scripts/live/run_paper_trading.py --profile wing6_4x1_all_vix_filtered
  Restart=on-failure
  RestartSec=45
  StartLimitIntervalSec=600
  StartLimitBurst=5

  [Install]
  WantedBy=multi-user.target
  ```
- [ ] Create `/etc/systemd/system/health-monitor.service`:
  ```ini
  [Unit]
  Description=Live Paper Health Monitor
  After=network-online.target
  Wants=network-online.target

  [Service]
  User=<service_user>
  WorkingDirectory=/DATA/live-paper/indian-markets
  EnvironmentFile=/DATA/live-paper/indian-markets/.env.live
  ExecStart=/DATA/live-paper/indian-markets/.venv/bin/python scripts/live/health_monitor.py --profile wing6_4x1_all_vix_filtered
  Restart=on-failure
  RestartSec=10
  StartLimitIntervalSec=300
  StartLimitBurst=5

  [Install]
  WantedBy=multi-user.target
  ```
- [ ] `systemctl daemon-reload`
- [ ] `systemctl enable health-monitor.service live-paper.service`
- [ ] `systemctl start health-monitor.service` → confirm `systemctl status` shows active
- [ ] Test: `systemctl kill health-monitor.service` → verify it restarts within 10 s
- [ ] Test: `systemctl kill live-paper.service` → verify it restarts within 45 s

---

### Phase 7 — Dashboard

- [ ] Write `options_backtest/dashboard_bridge.py` (Section 12.3):
  - [ ] All 11 public methods implemented
  - [ ] Live methods poll filesystem; no websocket connections held
  - [ ] File reads use `columns=` projection
  - [ ] On file-lock collision: retry once after 100 ms, then return last good snapshot
  - [ ] All timestamps rendered in IST
- [ ] Write `scripts/live/dashboard.py` (Section 12.2):
  - [ ] Tab 1 — Live Paper Trading: header tiles, open positions table, intraday equity curve, signal log, depth health heatmap, storage/writer health, alert state
  - [ ] Auto-refresh configurable (5 s / 10 s / 30 s / manual)
  - [ ] Sidebar: profile lock, refresh interval, audio alert toggle
  - [ ] Binds to `127.0.0.1:8501` by default; does not open any Dhan connection
  - [ ] Tabs 2 and 3 (Historical Explorer, Backtests) stubbed with "coming in v2" placeholder
- [ ] Create `systemd` unit or `tmux` session for dashboard on `zimaos`
- [ ] Test: open SSH tunnel from laptop (`ssh -L 8501:127.0.0.1:8501 zimaos`)
- [ ] Test: open `http://localhost:8501` on laptop → confirm Tab 1 renders without errors
- [ ] Test: stop the dashboard → confirm paper engine and collector continue running

---

### Phase 8 — Integration and Crash-Recovery Testing (before first live session)

- [ ] **Dry-run full session**: run all three processes (`health-monitor`, `run_paper_trading`, `dashboard`) on a non-trading day (Saturday) for 30 minutes → check logs for any unexpected errors
- [ ] **Crash-recovery test**:
  - [ ] Start `run_paper_trading.py` in dry-run mode with synthetic positions injected
  - [ ] Kill the process after checkpoint is written
  - [ ] Restart → confirm `RESUME MODE` log line and entry phase is skipped
  - [ ] Confirm EOD summary shows `resumed_after_crash: true` and correct gap window
- [ ] **Collector restart test**:
  - [ ] Start `collect_order_book.py`
  - [ ] Kill and restart mid-session
  - [ ] Confirm gap sentinel appears in JSONL with correct `gap_start` / `gap_end`
  - [ ] Confirm downstream analysis script rejects the file when `gap_minutes > 30`
- [ ] **Websocket budget test**: try opening a 6th Dhan websocket → confirm runner refuses to start
- [ ] **Storage path guard test**: point `data/live` at an invalid path → confirm both scripts abort with clear error
- [ ] **Token-redaction test**: scan all log files and snapshot JSON for token-like strings (regex `[A-Za-z0-9_\-]{100,}`) → confirm zero matches

---

### Phase 9 — First Live Paper Sessions (Week 4, Jun 2–6)

- [ ] **Session 1 preflight** (manually step through before 09:00 on first trading day):
  - [ ] `curl -4 https://api.ipify.org` from `zimaos` matches whitelisted IP
  - [ ] `timedatectl` shows `System clock synchronized: yes`
  - [ ] `/media/WD-Storage` mounted, writable, >= 100 GB free
  - [ ] `data/live` symlink resolves correctly
  - [ ] `dhan_connection_check.py` all 4 checks PASS
  - [ ] `health_monitor.service` running and "monitor online" Telegram message received
  - [ ] Telegram test alert passes and does not expose secrets
  - [ ] Healthchecks.io `$EXTERNAL_HEARTBEAT_URL/start` sent at 08:55
  - [ ] No prior Dhan websocket connections consuming the budget
  - [ ] Token expiry > today's EOD
- [ ] **Session 1 market open** (09:00–09:20):
  - [ ] `live-paper.service` starts; "backend healthy at market open" Telegram message received
  - [ ] `dashboard.py` reachable from laptop via SSH tunnel
  - [ ] Depth health tile shows >= 95% of 102 NSE channels ready by 09:15
  - [ ] Option-chain security-id discovery completes for all active symbols by 09:17
  - [ ] VIX quote is fresh and passes/fails NIFTY filter correctly
- [ ] **Session 1 entry** (09:20):
  - [ ] Each entered condor logged with fill JSON matching Section 4 schema
  - [ ] `mark_mid` ≠ `paper_fill_price` for at least one leg (confirms executable fill is used)
  - [ ] Each skipped symbol has an explicit reason code
  - [ ] `latest_open_positions.json` updated within 10 s of first fill
- [ ] **Session 1 exit** (15:20):
  - [ ] All open positions exited with fresh executable quotes
  - [ ] EOD JSON generated with `net_pnl = gross_pnl - charges`
  - [ ] `{YYYYMMDD}_paper_summary.md` generated via `paper_json_to_ledger.py`
  - [ ] Uptime summary Telegram message received with uptime %, alert counts, artifact paths
- [ ] Repeat preflight for Sessions 2–5; note any anomalies in the day log

---

### Phase 10 — Month-End Deployment Tracking

| Date | Session | Status | Notes |
|---|---|---|---|
| 2026-06-02 | Session 1 | `[ ]` | |
| 2026-06-03 | Session 2 | `[ ]` | |
| 2026-06-04 | Session 3 | `[ ]` | |
| 2026-06-05 | Session 4 | `[ ]` | |
| 2026-06-06 | Session 5 | `[ ]` | |
| 2026-06-09 | Session 6 | `[ ]` | |
| 2026-06-10 | Session 7 | `[ ]` | |
| 2026-06-11 | Session 8 | `[ ]` | |
| 2026-06-12 | Session 9 | `[ ]` | |
| 2026-06-13 | Session 10 | `[ ]` | |
| 2026-06-16 | Session 11 | `[ ]` | |
| 2026-06-17 | Session 12 | `[ ]` | |
| 2026-06-18 | Session 13 | `[ ]` | |
| 2026-06-19 | Session 14 | `[ ]` | |
| 2026-06-20 | Session 15 | `[ ]` | |
| 2026-06-23 | Session 16 | `[ ]` | |
| 2026-06-24 | Session 17 | `[ ]` | |
| 2026-06-25 | Session 18 | `[ ]` | |
| 2026-06-26 | Session 19 | `[ ]` | |
| 2026-06-27 | Session 20 | `[ ]` | |
| **Post-run** | **Fill validation report** | `[ ]` | Jun 30+ |
| **Post-run** | **Deployment readiness verdict** | `[ ]` | Jun 30+ |

---

### Phase 11 — Post-Month Fill Validation (Jun 9+)

- [ ] Run `paper_json_to_ledger.py` over full month → produce consolidated ledger
- [ ] Compute all six comparison tables from Section 8 (PnL attribution, spread accuracy, depth sufficiency, OI slippage proxy, entry timing, staleness)
- [ ] Check all five deployment gates from Section 8:
  - [ ] >= 95% intended entries filled or skipped with classified reason
  - [ ] >= 99% quote freshness compliance in entry/exit windows
  - [ ] Median executable spread no worse than backtest tier assumption per bucket
  - [ ] Zero unclassified websocket gaps in entry/exit windows
  - [ ] Paper vs backtest PnL difference >= 90% explained
- [ ] Flag any session days with `gap_minutes > 30` and exclude from spread/liquidity stats
- [ ] Write final fill validation report to `reports/backtests/options/research_validation/`
- [ ] Record deployment readiness verdict (pass / conditional-pass / fail + blocker) in the report

---

## 14. Verification Checklist

- [ ] `dhan_connection_check.py` passes REST `optionchain/expirylist`.
- [ ] `dhan_connection_check.py` opens live-feed websocket and survives a small subscription.
- [ ] `dhan_connection_check.py` opens 20-depth websocket and survives a small NSE subscription.
- [ ] Server repo and `.venv` live under `/DATA/live-paper/indian-markets`, not under `/root`.
- [ ] `/media/WD-Storage` is mounted and has at least 100 GB free before starting the month-long run.
- [ ] `data/live` resolves to `/media/WD-Storage/indian-markets-live`.
- [ ] `health_monitor.py` starts before `run_paper_trading.py`.
- [ ] Telegram test alert succeeds before 09:00 and does not expose secrets.
- [ ] Alert throttling and recovery messages work in a dry-run monitor test.
- [ ] External heartbeat monitor is hosted outside `zimaos`.
- [ ] `zimaos` sends external heartbeat every 60 seconds during market hours; every 5 minutes off-hours, 7 days a week.
- [ ] Healthchecks.io `/start` suffix sent at 08:55; `/fail` sent on preflight abort.
- [ ] Missed external heartbeat triggers Telegram alert within 3 minutes.
- [ ] External heartbeat recovery message arrives after heartbeat resumes.
- [ ] Sentry captures a test exception from `zimaos` and fires Telegram alert.
- [ ] `SENTRY_DSN` is not present in any repo file, log, markdown, or parquet metadata.
- [ ] `latest_open_positions.json` is written atomically after the first paper fill.
- [ ] Engine restarted mid-session in a dry-run test correctly loads position checkpoint and skips entry phase.
- [ ] Data gap sentinel appears in JSONL when `collect_order_book.py` is restarted mid-session.
- [ ] EOD summary includes `resumed_after_crash: true` and gap window when resume mode was used.
- [ ] systemd `Restart=on-failure` confirmed for both `live-paper.service` and `health-monitor.service`.
- [ ] Dashboard is accessed from the laptop via SSH tunnel and Streamlit binds to `127.0.0.1`.
- [ ] No access token appears in git diff, logs, markdown, JSON, or parquet metadata.
- [ ] Locked profile config matches Section 2 exactly.
- [ ] `live_resolver.atm_strike()` matches independent live ATM check at 09:25.
- [ ] `live_resolver.resolve_atm_offset()` resolves the exact four Wing-6 legs for each active symbol.
- [ ] SENSEX is absent from 20-depth collector and uses top-of-book path explicitly.
- [ ] Order book collector populates 20 bid + 20 ask levels for all NSE instruments during market hours.
- [ ] Paper fills use executable bid/ask/depth, not midpoint.
- [ ] Midpoint is stored only as `mark_mid`.
- [ ] End-of-day JSON has `net_pnl = gross_pnl - charges`.
- [ ] Paper JSON converts through adapter before `reports.summary()`.
- [ ] All skip reasons use the explicit reason-code table.
- [ ] All 4 planned connections stay stable for a full 6.5-hour live session.
- [ ] Uptime summary reports backend uptime, snapshot gaps, alert counts, and final artifact paths.
- [ ] Dashboard renders live PnL, open positions, and depth health without errors.

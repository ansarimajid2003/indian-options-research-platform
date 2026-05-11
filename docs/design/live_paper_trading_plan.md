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
| `POST /v2/optionchain/expirylist` | Fetch active expiries for an underlying | `access-token`, `client-id` | one unique request every 3 seconds |
| `POST /v2/optionchain` | Fetch full chain for an underlying + expiry | `access-token`, `client-id` | one unique request every 3 seconds |

> **v1 vs v2:** Use the `/v2/` prefix. `/v1/optionchain/expirylist` returns HTML, not JSON. Confirmed in Phase 1 connectivity test.

Request body shape (expirylist):

```json
{ "UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I" }
```

Response (`expirylist`): `{"data": ["2026-05-12", "2026-05-19", ...]}` — flat list of `YYYY-MM-DD` strings.

Request body shape (chain):

```json
{ "UnderlyingScrip": 13, "UnderlyingSeg": "IDX_I", "Expiry": "2026-05-12" }
```

Response (`optionchain`) — current live v2 shape verified on `zimaos` 2026-05-10:

```json
{
  "data": {
    "last_price": 24176.15,
    "oc": {
      "24200.000000": {
        "ce": {
          "security_id": 49081,
          "last_price": 146.60,
          "top_bid_price": 146.65,
          "top_ask_price": 147.20,
          "top_bid_quantity": 1820,
          "top_ask_quantity": 910,
          "volume": 52000,
          "oi": 7455955,
          "implied_volatility": 16.36,
          "greeks": { "delta": 0.52, "gamma": 0.002, "theta": -8.5, "vega": 18.2 }
        },
        "pe": { "security_id": 49082, "last_price": 136.00 }
      }
    }
  },
  "status": "success"
}
```

`security_id` is an integer in the response; convert to `str` before using as websocket subscription key.
The live resolver accepts this documented lower-case `data.oc.{strike}.ce/pe` shape and also keeps a
defensive parser for older flat `CallOption` / `PutOption` responses.

Option chain provides top bid/ask, OI, volume, LTP, IV, Greeks, and option `SecurityId`.
It is the required security-id discovery layer for live collection. Greeks (Delta, Gamma, Theta, Vega)
are stored alongside entry fills for post-month spread and risk analysis.

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
| Response format | binary packets (little-endian) |

**Subscription request codes (v2):**

| Code | Mode | Use in this project |
|---:|---|---|
| 15 | Ticker | LTP only — not used (too sparse) |
| 17 | Quote | OHLC + volume — not used (no OI, no depth) |
| 21 | Full | OHLC + volume + OI + 5-level depth — **use this** |

Always subscribe with `RequestCode: 21` (Full) for all instruments. This gives OHLC, volume, OI,
and the top 5 bid/ask levels in one binary packet — enough for SENSEX top-of-book fills.

**Exchange segment strings for subscriptions:**

| String | Numeric | Instruments |
|---|---:|---|
| `IDX_I` | 0 | Index spot (NIFTY, BANKNIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, VIX) |
| `NSE_FNO` | 2 | NSE futures and options |
| `BSE_FNO` | 8 | BSE futures and options (SENSEX options) |

SENSEX spot uses `IDX_I` (scrip_id=51). SENSEX options use `BSE_FNO`. This distinction matters
for the live-feed subscription: subscribe the SENSEX index via `IDX_I` for LTP and the SENSEX
options via `BSE_FNO` for top-of-book fills.

**Binary response packet types (all little-endian):**

All packets start with a 1-byte packet type, 2-byte message length, 1-byte exchange segment, then 4-byte security_id.

| Type (byte 0) | Name | Size | Key fields |
|---:|---|---:|---|
| 2 | Ticker | 16 B | LTP (float32), LTT epoch-s (uint32) |
| 3 | MarketDepth (5-level) | 112 B | LTP + 5 levels: `<IIHHff>` each (bid_qty, ask_qty, bid_ord, ask_ord, bid_price, ask_price) |
| 4 | Quote | 50 B | LTP, LTQ, LTT, avg_price, volume, sell_qty, buy_qty, open, close, high, low (all float32/uint32) |
| 5 | OI | 12 B | open_interest (uint32) |
| 6 | PrevClose | 16 B | prev_close (float32), prev_oi (uint32) |
| 8 | Full | 162 B | Quote (50 B) + OI fields + MarketDepth (100 B) — **parse this for OHLC+OI+depth** |
| 50 | Disconnect | 10 B | disconnect_code (uint16) — see hard failure rules |

All timestamps in live feed packets are EPOCH seconds (`uint32`). Convert with
`datetime.fromtimestamp(ltt, tz=IST)`. Do NOT treat them as milliseconds.

**Disconnect codes (both live feed and 20-depth):**

| Code | Meaning | Action |
|---:|---|---|
| 805 | Active websocket connections exceeded | Fatal — abort day; reduce connection count |
| 806 | Not subscribed to Data APIs | Fatal — check Dhan plan subscription |
| 807 | Access token expired | Fatal — abort day; renew token before next session |
| 808 | Invalid client ID | Fatal — check `DHAN_CLIENT_ID` env var |
| 809 | Authentication failed | Fatal — abort day; re-check token and client ID |

Codes 805, 807, 808, 809 must not trigger a reconnect. Log the code and abort the day.
Code 806 is also fatal. Any other disconnect (network drop, server restart) is retryable.

Use this feed for spot index LTP, VIX, SENSEX top-of-book support, and fallback quote
information.

### 3.3 20-Level Full Market Depth

Endpoint:

```text
wss://depth-api-feed.dhan.co/twentydepth?token=<redacted>&clientId=<client_id>&authType=2
```

Hard constraints:

| Rule | Value |
|---|---|
| Scope | NSE Equity (`NSE_EQ`) and NSE Derivatives (`NSE_FNO`) only — BSE instruments excluded |
| Instruments per connection | 50 (subscribe in batches of 50) |
| Subscribe request code | 23 |
| Response format | binary packets (little-endian) |
| Packet size | 332 bytes (12 header + 20 × 16 levels) |
| Bid packet feed code | 41 |
| Ask packet feed code | 51 |
| Disconnect packet feed code | 50 (same disconnect codes 805–809 as live feed) |
| Levels | 20 bid levels and 20 ask levels, sent as separate packets |

**Exact binary format (verified against DhanHQ-py SDK source):**

Header (12 bytes, struct `<hBBiI`):

| Offset | Type | Field |
|---:|---|---|
| 0 | int16 | `msg_len` — message length |
| 2 | uint8 | `feed_code` — 41=bid, 51=ask, 50=disconnect |
| 3 | uint8 | `exch_seg` — exchange segment numeric code |
| 4 | int32 | `security_id` — Dhan security ID (same as from option chain `SecurityId`) |
| 8 | uint32 | `reserved` (NoofRows field; 20 for 20-depth) |

Each level (16 bytes, struct `<dII`):

| Offset | Type | Field |
|---:|---|---|
| 0 | **float64** | `price` — **double, not float32** |
| 8 | uint32 | `quantity` |
| 12 | uint32 | `orders` |

Prices in the 20-depth feed are float64 (8 bytes). Prices in the live feed are float32 (4 bytes).
Do not interchange the two struct formats.

Subscription ExchangeSegment for NSE options must be `"NSE_FNO"`, not `"IDX_I"`.

SENSEX/BSE options are not eligible for Dhan 20-depth (BSE instruments excluded).
SENSEX paper fills must use top-of-book from the live-feed Full packet (type 8, 5-level depth)
until a separate BSE depth source is proven.

### 3.4 Connection Budget

| Feed | Endpoint | Instruments | Connections |
|---|---|---:|---:|
| Quote/full feed | `wss://api-feed.dhan.co` | spot indices, VIX, active options, SENSEX top-of-book support | 1 |
| 20-depth NSE options | `wss://depth-api-feed.dhan.co/twentydepth` | 246 configured major-index NSE option contracts | 5 |
| Reserved diagnostic socket | either | disabled during full ATM +/- 20 capture | 0 |
| Depth endpoint budget | | | 5 |

The runner must refuse to start if another process already consumes the Dhan websocket budget. During
full ATM +/- 20 capture, do not open a diagnostic socket. If live-feed and 20-depth sockets prove to
share one global account-level cap in production, reduce `depth_collection.symbols` to two indices or
lower `depth_collection.atm_offset_range` for that session.

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
  "security_id": "49081",
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
  "depth_available_qty": 1800,
  "greeks": { "delta": 0.52, "gamma": 0.002, "theta": -8.5, "vega": 18.2 },
  "iv": 12.5
}
```

`greeks` and `iv` are populated from the option chain snapshot fetched at 09:15 (pre-entry discovery).
They are diagnostics only — they do not affect fill pricing or paper PnL calculations.
`security_id` matches the integer `SecurityId` from the Dhan option chain response, stored as a string.

---

## 5. Architecture

### 5.1 Overview

```text
scripts/live/run_paper_trading.py
  |
  +-- scripts/live/collect_order_book.py
  |     |
  |     +-- Dhan 20-depth WS x 5
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
    def update_quote(self, security_id: str, ltp: float, open_: float, high: float,
                     low: float, volume: int, oi: int, received_at: pd.Timestamp) -> None: ...
```

Rules:

- `atm_strike()` uses fresh spot LTP, rounded by `get_instrument_spec(symbol).strike_step`.
- `resolve_atm_offset()` must resolve through option-chain `security_id` / `SecurityId` (integer in REST response; stored as str internally).
- `bar_at()` returns latest quote state with `open`, `high`, `low`, `close`, `volume`, and `oi`.
- `close` means live LTP for compatibility with existing engine code.
- `bars_for()` returns the rolling intraday quote deque for liquidity diagnostics.

**Live feed binary packet parsing (required for `update_quote` population):**

The paper engine's live feed listener receives binary packets from `wss://api-feed.dhan.co` and
must call `update_quote()` for each received instrument. Subscribe with `RequestCode: 21` (Full)
to receive type-8 packets (162 bytes) containing OHLC, volume, OI, and 5-level depth in one frame.

Type-8 Full packet key fields (struct `<BHBIfHIfIIIIIIffff100s`, little-endian):

| Offset | Type | Field |
|---:|---|---|
| 0 | uint8 | packet_type = 8 |
| 1 | uint16 | msg_len |
| 3 | uint8 | exch_seg |
| 4 | uint32 | security_id |
| 8 | float32 | **LTP** |
| 12 | uint16 | LTQ (last traded qty) |
| 14 | uint32 | LTT (last traded time — **EPOCH seconds**, not ms) |
| 18 | float32 | avg_price |
| 22 | uint32 | **volume** |
| 26 | uint32 | sell_qty |
| 30 | uint32 | buy_qty |
| 34 | uint32 | **OI** |
| 38 | uint32 | oi_day_high |
| 42 | uint32 | oi_day_low |
| 46 | float32 | **open** |
| 50 | float32 | **close** (previous day close) |
| 54 | float32 | **high** |
| 58 | float32 | **low** |
| 62 | 100 bytes | 5-level depth: 5 × `<IIHHff>` (bid_qty, ask_qty, bid_ord, ask_ord, bid_price, ask_price) |

Timestamps: `ltt` is EPOCH seconds (`uint32`). Convert with `datetime.fromtimestamp(ltt, tz=IST)`.
Do not treat as milliseconds.

Type-50 (disconnect) packet from the live feed carries a `uint16` disconnect code (bytes 8–9).
On codes 805, 807, 808, 809 — do not reconnect; abort and log `fatal_ws_disconnect:{code}`.

For SENSEX top-of-book: subscribe the SENSEX index (`scrip_id=51`, `IDX_I`) with Full mode.
The 5-level depth inside the type-8 packet provides best bid/ask for SENSEX option fill pricing.

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

| Symbol | Strike step | ATM +/- 20 strikes | CE+PE | Contracts |
|---|---:|---:|---:|---:|
| NIFTY | 50 | 41 | 2 | 82 |
| FINNIFTY | 50 | 41 | 2 | 82 |
| MIDCPNIFTY | 25 | 41 | 2 | 82 |
| Total | | | | 246 |

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
  trading days. The current Healthchecks.io monitor is configured with a 1-minute period and
  3-minute grace, so off-hours pings also run every 60 seconds to avoid UP/DOWN flapping. If the
  hosted monitor period is changed to 5 minutes later, the off-hours cadence can be relaxed.
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
| Disconnect code 805 received on any websocket | Fatal — too many active connections; abort day; do not reconnect |
| Disconnect code 807 received on any websocket | Fatal — access token expired; abort day; renew token before next session |
| Disconnect code 808 received on any websocket | Fatal — invalid client ID; abort day; check `DHAN_CLIENT_ID` env var |
| Disconnect code 809 received on any websocket | Fatal — authentication failed; abort day; re-check token and credentials |
| Disconnect code 806 received on any websocket | Fatal — API plan not subscribed; abort day; check Dhan account |
| Any other websocket disconnection (no code, or code not in 805–809) | Retryable — reconnect with `RestartSec=45` back-off; log `ws_reconnect` |

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

| Phase | Dates | Work | Status |
|---|---|---|---|
| Phase 1 | May 12-16 | `dhan_connection_check.py`, locked profile config, `DepthCache`, live security-id discovery | ✓ Done May 2026 |
| Phase 2 | May 19-23 | `live_resolver.py`, `collect_order_book.py`, packet parsing, raw/normalized writes | ✓ Done May 2026 |
| Phase 3 | May 26-30 | `paper_engine.py`, `run_paper_trading.py`, `health_monitor.py`, `paper_json_to_ledger.py`, `renew_token.py` | ✓ Done May 2026 |
| Phase 4 | Jun 2-6 | First full live paper sessions; monitor gaps and fill anomalies | In progress |
| Phase 5 | May 11 | Health monitor external heartbeat, alerting, and restart drills | Done May 2026 |
| Phase 6 | May 11 | `systemd` services for health monitor and paper engine | Done May 2026 |
| Post-run | Jun 9+ | Fill validation report and deployment readiness verdict | Pending |

---

## 10. Files To Create

| File | Purpose | Status |
|---|---|---|
| `configs/live/wing6_4x1_all_vix_filtered.json` | Locked profile config | ✓ Done |
| `scripts/live/dhan_connection_check.py` | Redacted REST + websocket smoke test | ✓ Done |
| `options_backtest/live_resolver.py` | Live quote/contract resolver | ✓ Done |
| `options_backtest/depth_cache.py` | Shared bid/ask/depth cache and executable VWAP logic | ✓ Done |
| `options_backtest/paper_engine.py` | Live paper trading engine | ✓ Done |
| `scripts/live/collect_order_book.py` | NSE 20-depth recorder | ✓ Done |
| `scripts/live/run_paper_trading.py` | Daily runner with auto token renewal | ✓ Done |
| `scripts/live/health_monitor.py` | Independent uptime/data-integrity monitor with Telegram alerts | ✓ Done |
| `scripts/live/paper_json_to_ledger.py` | Adapter from paper JSON to reports-compatible ledger | ✓ Done |
| `scripts/live/renew_token.py` | Headless daily token renewal via PIN + TOTP | ✓ Done |
| `scripts/live/systemd/health-monitor.service` | `systemd` unit for independent health monitoring | Done |
| `scripts/live/systemd/live-paper.service` | `systemd` unit for paper engine restart recovery | Done |
| `scripts/live/systemd/install_services.sh` | Server-side installer for units, cron, enablement, and monitor start | Done |
| `scripts/live/systemd/dhan-token-renewal` | Cron entry for 08:30 IST token renewal | Done |

Files with zero intended behavior changes:

- `options_backtest/engine.py`
- `options_backtest/dhan_loader.py`
- `options_backtest/reports.py`
- `options_backtest/broker_sim.py`
- `options_backtest/strategy.py`
- `options_backtest/calendar.py`

If one of those files must change, implementation pauses for a focused review.

---

## 11. Daily Token Renewal

Dhan access tokens expire daily. The live paper stack must not require manual token renewal
before each market session.

### 11.1 Mechanism

Token renewal is fully headless — no browser, no OAuth redirect. The flow:

```
POST https://auth.dhan.co/app/generateAccessToken
  ?dhanClientId=<client_id>
  &pin=<6-digit login PIN>
  &totp=<current 6-digit TOTP from authenticator secret>
→ {"accessToken": "eyJ...", "expiryTime": "..."}
```

TOTP is generated from `DHAN_TOTP_SECRET` using `pyotp.TOTP(secret).now()`.
Three TOTP window offsets (0s, +30s, −30s) are tried to survive clock drift and
window-boundary errors, which are a known intermittent Dhan API issue.

### 11.2 `scripts/live/renew_token.py`

```bash
# Renew and write to .env.live
python scripts/live/renew_token.py

# Check current token expiry (no HTTP call)
python scripts/live/renew_token.py --check

# Print current TOTP code only (no HTTP call)
python scripts/live/renew_token.py --dry-run
```

Required env vars for renewal:

| Var | Value |
|---|---|
| `DHAN_CLIENT_ID` | `1111444766` |
| `DHAN_PIN` | 6-digit Dhan login PIN |
| `DHAN_TOTP_SECRET` | base32 TOTP secret from Dhan 2FA enrollment |

`DHAN_API_KEY` and `DHAN_API_SECRET` (present in `.env.live`) are used only by the
consent-based OAuth flow and are NOT needed for headless PIN+TOTP renewal.

### 11.3 Auto-renewal in `run_paper_trading.py`

On every startup, `run_paper_trading.py` calls `_ensure_fresh_token()` which:

1. Decodes the JWT expiry of the current `DHAN_ACCESS_TOKEN`.
2. If remaining validity < 2 hours (or token is expired), renews via PIN+TOTP.
3. Writes the new token to `.env.live` (merge-safe — only replaces the
   `DHAN_ACCESS_TOKEN=` line, preserving all other vars).
4. Uses the fresh token for the day's session.

If `DHAN_PIN` or `DHAN_TOTP_SECRET` are absent, a warning is logged and the
existing token is used as-is.

### 11.4 Recommended Daily Cron (zimaos)

Run renewal once at 08:30 IST — before the 08:55 heartbeat start — so the token
is always fresh for the health monitor and paper engine:

```bash
# /etc/cron.d/dhan-token-renewal  (on zimaos)
30 8 * * 1-5  root  cd /DATA/live-paper/indian-markets && \
  source .env.live && \
  .venv/bin/python scripts/live/renew_token.py >> /DATA/live-paper/renewal.log 2>&1
```

The auto-renewal in `run_paper_trading.py` is a fallback for days when the cron
did not run. Running both is redundant but harmless.

---

## 12. Server Deployment Layout

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

## 13. Web Dashboard

A multi-tab web dashboard provides real-time visibility into the live paper run plus historical
data exploration and backtest comparison. It is read-only: no orders are placed, no Dhan websocket
is opened, and no engine state is modified. The collector and paper engine must continue normally
if the dashboard is closed or the laptop disconnects.

### 13.1 Technology Choice

| Layer | Choice | Rationale |
|---|---|---|
| Backend API | FastAPI (Python, async) | Stays in the project's Python stack; native async/WebSocket support; Pydantic response models double as the React frontend contract; `uvicorn` runs as a lightweight server process |
| Data bridge | `options_backtest/dashboard_bridge.py` | Isolates the API from live engine threads; reads only flushed snapshot files; TTL-cached so every API call doesn't re-read disk |
| Financial charts | TradingView Lightweight Charts (open source) | TradingView-quality OHLC charts — candlestick, line, histogram, crosshair sync across panes; handles millions of bars with level-of-detail decimation |
| Option chain / data grids | AG Grid (free Community tier) | Industry-standard virtualised grid; renders 500+ strike rows at 60 fps; sortable, filterable columns; same grid Kotak Neo uses |
| UI framework | React + Tailwind CSS + shadcn/ui | Dark-theme financial design; full layout control (no Streamlit grid constraints); Claude Design owns this layer |
| Real-time transport | FastAPI WebSocket → browser | True push (~10 ms latency) instead of 5 s poll; depth cache can stream ticks directly |
| Storage root | `/media/WD-Storage/indian-markets-live` | Keeps large live artifacts on the WD hard drive rather than SSD/root |
| Alerts | `scripts/live/health_monitor.py` + Telegram | Pushes backend failures to user's phone even when the laptop/dashboard is offline |
| Server-down monitor | External Healthchecks.io heartbeat | Alerts when `zimaos` itself stops sending heartbeats |

**Why not Streamlit:** Streamlit re-runs the entire Python script on every user interaction (wrong execution model for a trading dashboard), has no native WebSocket push, cannot produce TradingView-quality charts without a custom component, and cannot render the Kotak Neo-style option chain grid (calls \| strikes \| puts, ITM colour coding, Greeks columns) with its native table widget.

**Implementation split:**
- Phase 7a (this project): FastAPI backend + `dashboard_bridge.py` — all Python; produces clean JSON API and WebSocket contract that Claude Design builds against.
- Phase 7b (Claude Design): React frontend — TradingView Lightweight Charts, AG Grid, dark theme, full Kotak Neo-style layout.

### 13.2 Dashboard Layout

The dashboard has three tabs. Tab 1 ships in Phase 7. Tabs 2 and 3 are v2 scope — built only after the server runner has survived full market sessions.

```text
┌─────────────────────────────────────────────────────────────────────┐
│  Indian Markets  ·  Wing-6 Live Paper                               │
├─────────────────────────────────────────────────────────────────────┤
│  [ Live Monitor ]  [ Historical Explorer ]  [ Backtests ]           │
└─────────────────────────────────────────────────────────────────────┘
```

Design reference: Kotak Neo — dark background (`#0d0d0d`), card-based layout, colour-coded P&L (green/red), subtle borders, monospaced numbers.

#### Tab 1 — Live Monitor

Purpose: monitor the current session in real time.

**Header strip (WebSocket push, ~1 s refresh):**
- Session date + market status badge (`PRE_OPEN` / `OPEN` / `CLOSED` / `HOLIDAY`)
- Server process status, uptime, public outbound IP
- WD free space + last flush time
- Today's gross P&L and net P&L (colour coded)
- Quote freshness % (instruments with age < 5 s)
- Depth readiness % (of 246 NSE depth channels)

**Main panels (4-column grid, dark cards):**
- **Open Positions** (AG Grid): symbol, expiry, short/long CE and PE strikes, entry time, entry premium, current mark, current spread, unrealised gross / net P&L, quote age per leg. Colour rows green/red on P&L sign.
- **Intraday Equity Curve** (Lightweight Charts area chart): cumulative net P&L vs time, 09:20–15:20 IST. Real-time tick append via WebSocket.
- **Signal Log** (virtual scrolling list): every skip/entry/exit event — timestamp, symbol, reason code, VIX/DTE/bucket filter state.
- **Depth Health** (heatmap grid): quote age and available depth quantity for each of the four legs per symbol. Red cell = stale or insufficient.
- **Storage / Writer Health** (status cards): raw packet flush age, parquet flush age, backpressure flag, WD free space trend.
- **Alert State** (timeline): active alerts, last Telegram delivery timestamp, alert counts by severity, recovery events.

**Sidebar:**
- Profile indicator (locked to `wing6_4x1_all_vix_filtered`)
- WebSocket connection status indicator
- Audio alert toggle on `partial_entry_blocked` or `forced_stale_exit`

API data sources (all served by FastAPI):
- `GET /api/live/session` → session status
- `GET /api/live/positions` → open positions
- `GET /api/live/equity-curve` → intraday P&L series
- `GET /api/live/signal-log` → event log
- `GET /api/live/depth-health` → per-leg freshness
- `GET /api/live/storage-health` → writer status
- `GET /api/live/alerts` → alert state and history
- `WS /ws/live` → combined real-time push (positions + equity tick + depth health update)

#### Tab 2 — Historical Explorer (v2 scope)

Purpose: browse and visualise any archived dataset without writing analysis scripts.

**Controls (top bar):**
- Dataset type: `Spot 1-min`, `Dhan Options 1-min`, `Order Book`, `Bhavcopy EOD`, `India VIX`
- Symbol, date range, expiry, strike, CE/PE (context-sensitive)
- Depth level slider (1–20) and spread/imbalance overlays for order book data

**Main panels:**
- **OHLC/line chart** (Lightweight Charts): zoom/pan, volume and OI overlays, crosshair.
- **Data table** (AG Grid): virtualised, 10,000+ rows at 60 fps, column filters, CSV export.
- **Quick stats**: mean, min, max, std, total volume, mean spread, mean imbalance.

API data sources:
- `GET /api/historical/spot/{symbol}?start=&end=`
- `GET /api/historical/options/{symbol}/{expiry}/{strike}/{opt_type}`
- `GET /api/historical/order-book/{date}/{symbol}/{expiry}/{strike}/{opt_type}`
- `GET /api/historical/bhavcopy/{symbol}?start=&end=`
- `GET /api/historical/vix?start=&end=`

#### Tab 3 — Backtests (v2 scope)

Purpose: compare backtest results, inspect trade ledgers, and validate live paper sessions against historical runs.

**Controls (sidebar):**
- Multi-select backtest JSON/ledger files from `reports/backtests/` or `data/live/reports/`
- Symbol, date range, strategy filters

**Main panels:**
- **Metrics comparison table** (AG Grid): Trades, Net PnL, CAGR, Sharpe, Sortino, Calmar, MaxDD, Win%, PF, t-stat. One row per selected backtest.
- **Equity-curve overlay** (Lightweight Charts): normalised cumulative curves on the same axis.
- **Trade P&L distribution** (histogram): per-trade net PnL, optional symbol facet.
- **Drawdown chart** (Lightweight Charts): peak-to-trough over time.
- **Monthly returns heatmap**: rows = years, columns = months, colour-coded return %.
- **Trade ledger drill-down** (AG Grid): click a backtest row → expandable sortable/filterable trade table.

API data sources:
- `GET /api/backtests` → list of available backtest summaries
- `GET /api/backtests/{id}` → full ledger for one backtest
- `GET /api/backtests/{id}/equity-curve`

### 13.3 Dashboard Bridge (`options_backtest/dashboard_bridge.py`)

The bridge enforces a strict read-only boundary between the FastAPI layer and the live engine state. It owns all file I/O and TTL caching; the API routes call bridge methods and return Pydantic models.

```python
class DashboardBridge:
    # --- Live monitor ---
    def get_session_status(self) -> SessionStatus: ...          # process health + market status
    def get_open_positions(self) -> list[PositionRow]: ...      # from latest_open_positions.json
    def get_equity_curve(self, session_date: date) -> list[EquityPoint]: ...  # from paper_trades JSON
    def get_signal_log(self, session_date: date) -> list[SignalLogEntry]: ... # from paper_trades JSON
    def get_depth_health(self) -> list[DepthHealthRow]: ...     # from latest_depth_cache.json
    def get_storage_health(self) -> StorageHealth: ...          # file mtime checks + WD free space
    def get_alert_state(self) -> AlertState: ...                # from latest_alert_state.json
    def get_alert_history(self, session_date: date) -> list[AlertEntry]: ...  # from alerts JSONL

    # --- Historical (v2) ---
    def historical_spot(self, symbol: str, start: date, end: date) -> pd.DataFrame: ...
    def historical_options(self, symbol: str, expiry: date, strike: int, opt_type: str) -> pd.DataFrame: ...
    def historical_order_book(self, session_date: date, symbol: str, expiry: date, strike: int, opt_type: str) -> pd.DataFrame: ...
    def historical_vix(self, start: date, end: date) -> pd.DataFrame: ...

    # --- Backtests (v2) ---
    def backtest_summaries(self, root: Path) -> list[BacktestSummary]: ...
    def backtest_ledger(self, backtest_id: str) -> pd.DataFrame: ...
    def backtest_equity_curve(self, backtest_id: str) -> list[EquityPoint]: ...
```

Rules:
- All methods are synchronous; FastAPI wraps them with `run_in_executor` for the async endpoints.
- Live methods poll the filesystem; they never hold websocket connections.
- File reads use `pd.read_parquet(..., columns=[...])` projection to minimise I/O on large parquet files.
- Each data source has its own TTL cache (live snapshots: 3 s; paper trades JSON: 5 s; historical parquet: 60 s).
- If a live file is locked by the writer (OSError/PermissionError on Windows, or mtime unchanged mid-write on Linux), the bridge retries once after 100 ms and returns the last known good snapshot rather than crashing the API.
- All timestamps are returned as ISO-8601 strings in IST (`Asia/Kolkata`) for JSON serialisation.
- Returns empty/default objects (never raises) when files are missing — dashboard renders "waiting for data" state rather than 500 errors.

### 13.4 FastAPI Application (`scripts/live/api/`)

```text
scripts/live/api/
    __init__.py
    main.py          # FastAPI app, CORS, lifespan, static file mount for React build
    models.py        # All Pydantic response models — this is the React contract
    routes/
        live.py      # /api/live/* REST + /ws/live WebSocket
        historical.py  # /api/historical/* (v2 scope, stubbed)
        backtests.py   # /api/backtests/* (v2 scope, stubbed)
```

**WebSocket push protocol (`/ws/live`):**

The server sends a JSON frame every second during market hours and every 5 s otherwise:

```json
{
  "ts": "2026-06-03T09:21:05.412+05:30",
  "session": { ... },
  "positions": [ ... ],
  "equity_tick": { "ts": "...", "net_pnl": 1240.50 },
  "depth_health": [ ... ],
  "storage_health": { ... },
  "alerts": { ... }
}
```

The React client appends `equity_tick` to its local series (no full re-fetch on every tick).

### 13.5 Files To Create

| File | Purpose | Phase |
|---|---|---|
| `options_backtest/dashboard_bridge.py` | Read-only data accessors with TTL cache; isolates API from engine internals | 7a |
| `scripts/live/api/__init__.py` | Package marker | 7a |
| `scripts/live/api/main.py` | FastAPI app; CORS; lifespan; mounts static React build at `/` | 7a |
| `scripts/live/api/models.py` | All Pydantic response models — the React frontend contract | 7a |
| `scripts/live/api/routes/live.py` | `/api/live/*` REST endpoints + `/ws/live` WebSocket | 7a |
| `scripts/live/api/routes/historical.py` | `/api/historical/*` endpoints (stubbed, v2) | 7a |
| `scripts/live/api/routes/backtests.py` | `/api/backtests/*` endpoints (stubbed, v2) | 7a |
| `scripts/live/systemd/dashboard-api.service` | systemd unit for FastAPI server on zimaos | 7a |
| `dashboard/` | React app (Vite + Tailwind + shadcn/ui + LightweightCharts + AG Grid) | 7b |

### 13.6 Launch Commands

```bash
# On zimaos: live paper trading + order book collector
cd /DATA/live-paper/indian-markets
.venv/bin/python scripts/live/run_paper_trading.py --profile wing6_4x1_all_vix_filtered

# On zimaos: independent health monitor and Telegram alerts
.venv/bin/python scripts/live/health_monitor.py --profile wing6_4x1_all_vix_filtered

# On zimaos: dashboard API (FastAPI + uvicorn), bound to localhost only
.venv/bin/uvicorn scripts.live.api.main:app --host 127.0.0.1 --port 8000

# On laptop: access API and React dev server through SSH tunnel
ssh -L 8000:127.0.0.1:8000 zimaos

# React dev server (laptop, during Phase 7b development)
cd dashboard && npm run dev   # proxies /api/* and /ws/* to localhost:8000
```

On `zimaos`, the API process is a separate systemd-managed process. Stopping the dashboard API must never affect the collector or paper engine. The React build (`dashboard/dist/`) is served as static files by the FastAPI app — no separate web server needed in production.

### 13.7 Security & Safety

- Dashboard API is read-only. No broker API calls, no token storage, no order placement.
- Bind `uvicorn` to `127.0.0.1` by default and access through SSH tunneling.
- CORS is configured to allow only the React dev origin (`localhost:5173`) and the tunnel origin (`localhost:8000`) — never `*`.
- If the API is exposed beyond localhost, place it behind a reverse proxy (nginx) with HTTP Basic Auth or mTLS; do not expose it directly to the public internet.
- The bridge redacts token-like strings (regex `[A-Za-z0-9_\-]{100,}`) from any log line or snapshot field before returning it to the API.
- Python dependencies for Phase 7a: `fastapi`, `uvicorn[standard]`, `websockets` (already installed).

---

## 14. Implementation Checklist

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
- [x] Install runtime stack: `pandas` 3.0.2, `numpy` 2.4.4, `pyarrow` 24.0.0, `websockets` 16.0, `requests` 2.33.1, `sentry-sdk` 2.59.0
- [ ] Install dashboard API stack (Phase 7a): `fastapi`, `uvicorn[standard]`
- [x] Create WD storage layout:
  ```
  /media/WD-Storage/indian-markets-live/{raw_depth_packets,order_book,order_book_1min,paper_trades,reports,logs,snapshots,alerts}/
  ```
- [x] Create repo-level symlink: `data/live` → `/media/WD-Storage/indian-markets-live`
- [x] Verify symlink resolves correctly: `readlink -f data/live` → `/media/WD-Storage/indian-markets-live` ✓

---

### Phase 1 — Credentials and External Services ✓ DONE 2026-05-10

> **Static IP outstanding:** `183.83.38.115` is likely a dynamic IP (ACT residential default). Buy the ACT static IP addon (~₹230/month) before relying on the Dhan whitelist. Call 1800-266-1111 or use the MyACT app. Re-verify public IP with `curl -4 https://api.ipify.org` from `zimaos` after activation before submitting Dhan whitelist request.

> **Daily token renewal required:** Dhan access token expires every 24 hours. `DHAN_API_KEY` and `DHAN_API_SECRET` are stored in `.env.live` alongside the token. A `scripts/live/renew_dhan_token.py` script must be written and scheduled (cron or systemd timer) before Phase 9 live sessions begin. The Dhan v2 REST endpoint for token generation is `POST https://api.dhan.co/v2/auth/token`.

- [x] Create Telegram bot via BotFather → bot: `@myzimaserverbot`, `TELEGRAM_CHAT_ID=1425784560`, written to `.env.live`
- [x] Create Healthchecks.io account (free tier) → monitor created, grace 3 min → `EXTERNAL_HEARTBEAT_URL` in `.env.live` → ping from `zimaos` returned `OK`
- [x] Create Sentry account → Python project created → `SENTRY_DSN` in `.env.live` → test event `8fef914f` captured from `zimaos`
- [x] Write server-side untracked env file `/DATA/live-paper/indian-markets/.env.live`:
  - [x] `DHAN_CLIENT_ID=1111444766`
  - [x] `DHAN_ACCESS_TOKEN` set (expires daily — must be renewed)
  - [x] `DHAN_API_KEY` and `DHAN_API_SECRET` set (for token renewal script)
  - [x] `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` set
  - [x] `SENTRY_DSN` set
  - [x] `EXTERNAL_HEARTBEAT_URL` set
- [x] `.env.live` covered by `.env.*` pattern already in `.gitignore`
- [x] Confirmed `.env.live` does not appear in `git status`
- [x] Manual Telegram test alert → `SENT OK`
- [x] Healthchecks.io ping from `zimaos` → `OK`
- [x] Sentry test event from `zimaos` → captured (event `8fef914f`)
- [x] `dhan_connection_check.py` from `zimaos` → **ALL PASS (4/4)** — token valid 1.0 days, 18 NIFTY expiries, live-feed WS open, 20-depth WS open

---

### Phase 2 — Locked Profile Config and Smoke Test ✓ DONE 2026-05-10

- [x] Create `configs/live/` directory
- [x] Write `configs/live/wing6_4x1_all_vix_filtered.json` — matches Section 2 exactly; scrip IDs confirmed from existing downloader (NIFTY=13, FINNIFTY=27, MIDCPNIFTY=442, SENSEX=51, VIX=21)
- [x] Write `scripts/live/dhan_connection_check.py` (Section 6.6):
  - [x] Token decode + expiry print (redacted; shows remaining days and masked client_id)
  - [x] `POST /v2/optionchain/expirylist` for NIFTY → 18 expiries returned (v2 endpoint; v1 returns HTML)
  - [x] Open `wss://api-feed.dhan.co` → send one IDX_I/NIFTY subscription → confirm packet received
  - [x] Open `wss://depth-api-feed.dhan.co/twentydepth` → send one IDX_I/NIFTY subscription → confirm packet received
  - [x] All output lines are PASS/FAIL only; no token or client-ID printed
- [x] Run `dhan_connection_check.py` from `zimaos` → **ALL PASS (4/4)** — 2026-05-10

---

### Phase 3 — Depth Cache and Live Resolver (Week 2, May 19–23) ✓ DONE 2026-05-10

> **Protocol verified against official DhanHQ-py SDK** (github.com/dhan-oss/DhanHQ-py):
> Header `<hBBiI` (12 bytes): msg_len, feed_code, exch_seg, security_id, reserved.
> Level `<dII` (16 bytes): price as float64, qty uint32, orders uint32.
> Option chain response verified live as `data.oc.{strike}.ce/pe` with lower-case keys; parser also accepts legacy flat `CallOption` / `PutOption`.
> Subscription ExchangeSegment for NSE options: `"NSE_FNO"`.

- [x] Write `options_backtest/depth_cache.py` (Section 6.2):
  - [x] `DepthLevel` dataclass
  - [x] `DepthSnapshot` dataclass (bid levels, ask levels, timestamps, `best_bid`, `best_ask`, `spread_pct`)
  - [x] `DepthCache` class — thread-safe; separate bid/ask timestamping
  - [x] `update_bid_packet()` and `update_ask_packet()` methods
  - [x] `snapshot()` returns `None` if either side is missing
  - [x] `is_ready()` checks both sides present and within `max_age_seconds`
  - [x] `executable_price()` — VWAP walk through ask levels for BUY, bid levels for SELL; returns `None` if cumulative quantity insufficient
  - [x] Inline tests: VWAP, freshness, insufficient-depth → None — all PASS
- [x] Write `options_backtest/live_resolver.py` (Section 6.1):
  - [x] `LiveDhanContractResolver` class
  - [x] `atm_strike()` — uses fresh spot LTP; rounds by instrument strike step
  - [x] `resolve_atm_offset()` — resolves through option-chain `security_id`; raises if stale
  - [x] `bar_at()` — returns latest quote as `pd.Series` with `open`, `high`, `low`, `close`, `volume`, `oi`; `close` = live LTP
  - [x] `bars_for()` — returns rolling intraday quote deque
  - [x] Freshness limits enforced (spot 5 s, option 5 s, VIX 60 s, depth 5 s)
  - [x] Returns `None` / raises `StaleQuoteError` when stale; no fallback to old prices
  - [x] Option chain parser corrected for current `data.oc.{strike}.ce/pe` shape and legacy `CallOption`/`PutOption`
  - [x] Expiry list parser handles flat list (v2) and `{Expirylist:[...]}` (legacy) defensively
- [x] Write `scripts/live/collect_order_book.py` (Section 6.4):
  - [x] Manages up to 5× `wss://depth-api-feed.dhan.co/twentydepth` connections (≤50 instruments each)
  - [x] Subscribes to NSE option contracts (NIFTY + FINNIFTY + MIDCPNIFTY) with `ExchangeSegment="NSE_FNO"`
  - [x] Binary packet parser: exact `<hBBiI` header + `<dII` levels; bid=41, ask=51 — verified against SDK
  - [x] Calls `depth_cache.update_bid_packet()` and `update_ask_packet()` on every packet
  - [x] Writes raw packets to `data/live/raw_depth_packets/{YYYYMMDD}/`
  - [x] Writes normalized parquet (all 67 columns per Section 6.4) to `data/live/order_book/{YYYYMMDD}/`
  - [x] Writes 1-min OHLCV aggregates to `data/live/order_book_1min/{YYYYMMDD}/`
  - [x] Flushes every 60 seconds
  - [x] Writes gap sentinel to JSONL on restart (Section 6.9)
  - [x] Logs `collector_backpressure` on write lag
  - [x] Aborts startup if `data/live` does not resolve to `/media/WD-Storage/indian-markets-live`
  - [x] Aborts startup if WD free space < 100 GB at start of month-long run
- [x] Write `scripts/live/sample_option_chain.py` — one-shot REST snapshot for all 4 symbols; run from zimaos to verify live data and exact JSON field names

---

### Phase 4 — Paper Engine and Runner (Week 3, May 26–30) ✓ CODE COMPLETE 2026-05-10

- [x] Write `options_backtest/paper_engine.py` — `PaperTradingEngine` (Section 6.3):
  - [x] Daily flow: connect → preflight → security-id discovery → VIX/DTE/bucket filter → entry at 09:20 → monitoring → time exit at 15:20 → stale deadline 15:25 → EOD flush
  - [x] Entry: evaluates all four symbols; logs skip with explicit reason code for each filter miss
  - [x] Fill: uses `depth_cache.executable_price()` for VWAP fill; falls back to `top_executable_price`; stores both `mark_mid` and executable fields
  - [x] Position checkpoint: writes `latest_open_positions.tmp` → flush/fsync → atomic rename to `latest_open_positions.json` after every fill
  - [x] Resume detection: loads same-day checkpoint on startup; skips entry phase if open positions found
  - [x] Forced stale exit: if quote still stale at 15:25, marks `forced_stale_exit=true`
  - [x] No stop-loss, no target, no DTE=0 exits allowed for this profile
  - [x] Fill JSON has all fields from Section 4 schema
  - [x] Outputs: `data/live/paper_trades/{YYYYMMDD}.json`, `data/live/logs/{YYYYMMDD}.log`
- [x] Write `scripts/live/paper_json_to_ledger.py` (Section 10):
  - [x] Reads `{YYYYMMDD}.json` → converts to `reports`-compatible ledger format
  - [x] Passes through `reports.summary()` and `reports.daily_pnl()` without modifying engine internals
  - [x] Outputs: `data/live/reports/{YYYYMMDD}_paper_summary.md`, `_paper_summary.json`, and `_paper_ledger.csv`
- [x] Write `scripts/live/run_paper_trading.py` — single entry point (Section 6.5):
  - [x] Holiday check via `calendar.is_trading_day(today)`; `--dry-run` allowed on non-trading days
  - [x] Loads locked profile from `configs/live/wing6_4x1_all_vix_filtered.json`
  - [x] Initialises `DepthCache`, starts collector and paper engine as coordinated tasks, and cancels collector cleanly after EOD
  - [x] Calls the paper JSON → ledger/report adapter after market close
  - [x] Fixes live-feed URL formatting and prevents collector hang after engine completion
  - [ ] Remaining preflight hardening: public IP whitelist comparison, websocket-budget probe, Telegram test alert, external heartbeat test, and strict health-monitor gating
- [x] Dry-run test on `zimaos` (no market hours): `run_paper_trading.py --dry-run` completed on 2026-05-10
- [ ] Simulated preflight failure tests remain before first live session

---

### Phase 5 — Health Monitor and External Heartbeat ✓ DONE 2026-05-11

- [x] Write `scripts/live/health_monitor.py` (Section 6.7):
  - [x] All 13 checks from Section 6.7 implemented
  - [x] 15-second poll for critical checks; 60-second poll for slow checks
  - [x] Telegram alert delivery with throttling by `(severity, component, reason)`
  - [x] 2-minute throttle during entry/exit windows (09:10–09:30, 15:15–15:30); 10-minute otherwise
  - [x] "Monitor online" startup message
  - [x] "Backend healthy at market open" message after all feeds connected
  - [x] Recovery messages when previously unhealthy component recovers
  - [x] EOD uptime summary with uptime %, alert counts, data-gap minutes, WD free space, artifact paths
  - [x] Sends Healthchecks.io `$EXTERNAL_HEARTBEAT_URL/start` at 08:55, plain ping every 60 s during market hours and off-hours, `$EXTERNAL_HEARTBEAT_URL/fail` on preflight abort
  - [x] Sentry `sentry_sdk.init()` at process startup with `send_default_pii=False`
  - [x] Outputs: `data/live/alerts/{YYYYMMDD}_alerts.jsonl`, `data/live/snapshots/latest_alert_state.json`, `data/live/reports/{YYYYMMDD}_uptime_summary.md`
  - [x] Snapshot files written atomically (`.tmp` → rename)
- [x] Test: kill the health monitor mid-run → confirmed systemd restart (`PID 1047503 -> 1048154`, restart counter `1`)
- [x] Test: block outbound Healthchecks.io ping for 3 minutes → confirmed blocked pings, external heartbeat failure, and recovery ping
- [x] Test: send a `sentry_sdk.capture_exception()` from `zimaos` → Sentry accepted event `26ea3315`

Validation artifact: `reports/backtests/options/monitoring/20260511_phase5_health_monitor_validation.md`.

---

### Phase 6 — systemd Services

- [x] Create `/etc/systemd/system/live-paper.service`:
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
- [x] Create `/etc/systemd/system/health-monitor.service`:
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
- [x] `systemctl daemon-reload`
- [x] `systemctl enable health-monitor.service live-paper.service`
- [x] `systemctl start health-monitor.service` → confirm `systemctl status` shows active
- [x] Test: `systemctl kill health-monitor.service` → verify it restarts within 10 s
- [x] Test: `systemctl kill live-paper.service` → verify it restarts within 45 s

Validation artifact: `reports/backtests/options/monitoring/20260511_phase6_systemd_validation.md`.

---

### Phase 7a — Dashboard Backend + Bridge

- [ ] Install additional Python deps in `.venv`: `fastapi`, `uvicorn[standard]`
- [ ] Write `options_backtest/dashboard_bridge.py` (Section 13.3):
  - [ ] All dataclasses: `SessionStatus`, `PositionRow`, `EquityPoint`, `SignalLogEntry`, `DepthHealthRow`, `StorageHealth`, `AlertState`, `AlertEntry`
  - [ ] All live methods implemented: `get_session_status`, `get_open_positions`, `get_equity_curve`, `get_signal_log`, `get_depth_health`, `get_storage_health`, `get_alert_state`, `get_alert_history`
  - [ ] Historical methods stubbed: `historical_spot`, `historical_options`, `historical_order_book`, `historical_vix`
  - [ ] Backtest methods stubbed: `backtest_summaries`, `backtest_ledger`, `backtest_equity_curve`
  - [ ] Per-source TTL cache (live snapshots 3 s, paper trades JSON 5 s, historical parquet 60 s)
  - [ ] Missing file → returns empty/default object, never raises
  - [ ] File-lock collision → retry once after 100 ms, return last good snapshot
  - [ ] All timestamps as ISO-8601 IST strings in output dataclasses
  - [ ] Token-like strings redacted from log/snapshot content before return
- [ ] Write `scripts/live/api/models.py` (Section 13.4):
  - [ ] Pydantic models for every response type matching the bridge dataclasses
  - [ ] WebSocket push frame model (`LivePushFrame`)
  - [ ] This file is the contract handed to Claude Design for Phase 7b
- [ ] Write `scripts/live/api/routes/live.py`:
  - [ ] `GET /api/live/session`
  - [ ] `GET /api/live/positions`
  - [ ] `GET /api/live/equity-curve?date=YYYYMMDD`
  - [ ] `GET /api/live/signal-log?date=YYYYMMDD`
  - [ ] `GET /api/live/depth-health`
  - [ ] `GET /api/live/storage-health`
  - [ ] `GET /api/live/alerts?date=YYYYMMDD`
  - [ ] `WS /ws/live` — pushes `LivePushFrame` every 1 s (market hours) or 5 s (off-hours)
- [ ] Write `scripts/live/api/routes/historical.py` — all endpoints return `{"status": "v2_scope_pending"}` stub
- [ ] Write `scripts/live/api/routes/backtests.py` — all endpoints return `{"status": "v2_scope_pending"}` stub
- [ ] Write `scripts/live/api/main.py`:
  - [ ] CORS: allow `localhost:5173` (React dev) and `localhost:8000` (tunnel) only
  - [ ] Lifespan: instantiate `DashboardBridge` once; inject via `app.state`
  - [ ] Mount React `dashboard/dist/` as static files at `/` (fallback to `index.html`)
  - [ ] Include all three route modules
- [ ] Write `scripts/live/systemd/dashboard-api.service` — `uvicorn` bound to `127.0.0.1:8000`
- [ ] Smoke test: `uvicorn scripts.live.api.main:app` locally → `curl http://127.0.0.1:8000/api/live/session` returns valid JSON
- [ ] Smoke test: open WS to `/ws/live` → confirm push frames arrive every second
- [ ] Test: stop the API → confirm paper engine and collector continue running unaffected

### Phase 7b — Dashboard Frontend (Claude Design handoff)

Handoff deliverables from Phase 7a: `scripts/live/api/models.py` (full Pydantic contract) + sample JSON responses for every endpoint + `scripts/live/api/main.py` (working FastAPI app).

- [ ] Scaffold `dashboard/` with Vite + React + TypeScript + Tailwind CSS + shadcn/ui
- [ ] Install TradingView Lightweight Charts and AG Grid Community
- [ ] Tab 1 — Live Monitor: all panels from Section 13.2 implemented and connected to live API + WebSocket
- [ ] Tab 2 — Historical Explorer: connected to `/api/historical/*` (activates when Phase 7a historical methods are implemented)
- [ ] Tab 3 — Backtests: connected to `/api/backtests/*` (activates when Phase 7a backtest methods are implemented)
- [ ] Build: `npm run build` → `dashboard/dist/`; confirm FastAPI serves it correctly
- [ ] Test: open SSH tunnel → `http://localhost:8000` renders full dashboard without console errors
- [ ] Test: stop the dashboard → confirm paper engine and health monitor continue running

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
  - [ ] Depth health tile shows >= 95% of 246 configured NSE channels ready by 09:15
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

## 15. Verification Checklist

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
- [ ] `zimaos` sends external heartbeat every 60 seconds while the Healthchecks.io monitor period is configured at 1 minute.
- [ ] Healthchecks.io `/start` suffix sent at 08:55; `/fail` sent on preflight abort.
- [ ] Missed external heartbeat triggers Telegram alert within 3 minutes.
- [ ] External heartbeat recovery message arrives after heartbeat resumes.
- [ ] Sentry captures a test exception from `zimaos` and fires Telegram alert.
- [ ] `SENTRY_DSN` is not present in any repo file, log, markdown, or parquet metadata.
- [ ] `latest_open_positions.json` is written atomically after the first paper fill.
- [ ] Engine restarted mid-session in a dry-run test correctly loads position checkpoint and skips entry phase.
- [ ] Data gap sentinel appears in JSONL when `collect_order_book.py` is restarted mid-session.
- [ ] EOD summary includes `resumed_after_crash: true` and gap window when resume mode was used.
- [x] systemd `Restart=on-failure` confirmed for both `live-paper.service` and `health-monitor.service`.
- [ ] Dashboard API (`uvicorn`) binds to `127.0.0.1:8000` on `zimaos`; accessed from laptop via SSH tunnel.
- [ ] `GET /api/live/session` returns valid JSON when paper engine is running.
- [ ] `WS /ws/live` delivers push frames on the correct interval (1 s market hours, 5 s off-hours).
- [ ] React build served correctly from FastAPI static mount at `/`.
- [ ] Stopping the dashboard API does not affect the paper engine or health monitor.
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

# DhanHQ-py SDK Review Against Live Paper Issues - 2026-05-14

## Scope

Cloned the official Dhan SDK into:

`third_party/DhanHQ-py`

SDK snapshot inspected:

`06c830c 2026-04-24 17:09:28 +0530 v2.2.0 - default`

This review compares the SDK against our current Dhan integration in:

- `options_backtest/paper_engine.py`
- `options_backtest/live_resolver.py`
- `scripts/live/collect_order_book.py`
- `scripts/live/dhan_connection_check.py`
- `scripts/live/sample_option_chain.py`
- `scripts/live/renew_token.py`
- `scripts/download/download_dhan_expired_options.py`

No broker calls were made for this review.

## Confirmed Standard Dhan Behaviors

The SDK confirms these are standard API surfaces, not local inventions:

| Area | SDK surface | Our current equivalent | Verdict |
|---|---|---|---|
| Shared credentials | `DhanContext(client_id, access_token)` | raw `client_id` / token passed through scripts | We should simplify around a small context wrapper. |
| REST option expiries | `dhan.expiry_list(under_security_id, under_exchange_segment)` | manual `POST /optionchain/expirylist` | Our endpoint and payload are standard. |
| REST option chain | `dhan.option_chain(under_security_id, under_exchange_segment, expiry)` | manual `POST /optionchain` | Our endpoint and payload are standard. |
| REST LTP fallback | `dhan.ticker_data({"IDX_I": [...]})` -> `/marketfeed/ltp` | `_fetch_core_ltp_rest()` | Our fallback is a normal Dhan API use, not a hack. |
| Live feed websocket | `MarketFeed(..., version="v2")` | manual `websockets.connect("wss://api-feed.dhan.co?version=2&token=...")` | URL shape and JSON subscription are standard. |
| Live feed batches | SDK batches live-feed subscriptions at 100 instruments | `_subscribe_instruments(... batch_size=100)` | Our batching matches SDK behavior. |
| 20-depth websocket | `FullDepth(..., depth_level=20)` | `collect_order_book.py` direct websocket | Endpoint, request code 23, and 50-instrument batches match SDK behavior. |
| 200-depth websocket | `FullDepth(..., depth_level=200)` | not used | Possible future tool, but likely not needed for current 4-leg execution. |
| Expired options | `dhan.expired_options_data(...)` -> `/charts/rollingoption` | `download_dhan_expired_options.py` | Our historical backfill uses the standard SDK endpoint. |
| PIN/TOTP token generation | `DhanLogin.generate_token(pin, totp)` | `scripts/live/renew_token.py` | Endpoint and query-parameter shape match SDK. |
| IP management | `set_ip`, `modify_ip`, `get_ip` | no first-class wrapper | SDK can replace ad hoc IP tooling if needed. |

## Important Corrections To Recent Root-Cause Theories

The live collector and paper engine are not separate Python processes in our current deployment. In `scripts/live/run_paper_trading.py`, `collect_order_book(...)` and `engine.run()` are launched as two `asyncio` tasks in the same `run_paper_trading.py` process.

That means simultaneous `collector_heartbeat_stale` and `process_wedged` alerts do not prove a WD drive stall. One event-loop/process stall, a blocking synchronous call, or task starvation can freeze both snapshot families.

The SDK design also supports this interpretation: it uses separate websocket helper classes, but does not imply separate OS processes for feed and depth.

## Where Our Custom Code Is Justified

These local additions are not over-engineering; they encode trading-specific safety or production evidence requirements that the SDK does not provide:

1. Freshness gates for entry and exit.

   SDK websocket helpers parse packets but do not enforce max quote age, no-stale-entry rules, or stale-exit flags. Our `LiveDhanContractResolver`, `DepthCache`, and paper engine gates are necessary.

2. Depth readiness by strategy leg.

   SDK `FullDepth` parses bid and ask packets. It does not know that Wing-6 needs all four legs executable per symbol before entry, nor does it track per-symbol readiness percentages.

3. Raw packet capture and normalized parquet depth snapshots.

   SDK emits parsed data to the caller. Our `collect_order_book.py` writes raw packets, normalized book snapshots, gap sentinels, and dashboard snapshots for auditability.

4. Current and older option-chain response compatibility.

   SDK sends the option-chain request but does not normalize response shape. Our resolver accepts the current `data.oc.{strike}.ce/pe` shape and older flat `CallOption` / `PutOption` shapes. Keeping this defensive parser is reasonable.

5. Mid-session restart rules and no-trade behavior.

   SDK reconnects websockets, but does not know our 09:20 entry, 15:20 exit, DTE filters, VIX gates, or "restart after entry window means no new entries" rule.

6. Operational monitoring.

   SDK has no health monitor, external heartbeat, dashboard snapshots, gap accounting, or Telegram alert policy.

## Overly Complex Or Duplicated Areas

These are places where we are duplicating SDK-standard behavior and should simplify.

### 1. Dhan HTTP boilerplate is repeated across scripts

We manually build headers and `requests.post()` calls in multiple places:

- `options_backtest/live_resolver.py`
- `options_backtest/paper_engine.py`
- `scripts/live/dhan_connection_check.py`
- `scripts/live/sample_option_chain.py`
- `scripts/download/download_dhan_expired_options.py`

The SDK already centralizes:

- base URL
- `access-token`
- `client-id`
- content headers
- response wrapper
- shared `requests.Session`

Recommended fix: create one local adapter, for example `options_backtest/dhan_client.py`, that wraps SDK calls but preserves our stricter error handling and payload logging policy. Do not import SDK directly everywhere.

### 2. Option-chain and expiry-list helper duplication

`LiveDhanContractResolver`, `sample_option_chain.py`, and `dhan_connection_check.py` each implement expiry/chain fetch logic.

Recommended fix: move expiry list, option chain fetch, response-shape normalization, and rate-limit sleep into one shared module. Keep `sample_option_chain.py` as a CLI around that module.

### 3. REST core LTP fallback can use the SDK method shape

`_fetch_core_ltp_rest()` manually calls `/marketfeed/ltp`. This is equivalent to SDK `ticker_data({"IDX_I": [...]})`.

Recommended fix: either use SDK through the local adapter or mirror its method name and payload shape. Keep the fallback itself; it is useful because IDX/VIX websocket delivery has been unreliable.

### 4. Token renewal duplicates SDK `DhanLogin.generate_token`

Our renewal endpoint and params match SDK behavior. The local script adds important production features: TOTP window offsets, expiry decoding, `.env.live` writing, and systemd compatibility.

Recommended fix: do not delete `renew_token.py`, but consider using `DhanLogin.generate_token()` underneath after verifying the SDK returns the same `accessToken` / `expiryTime` shape. Keep the offset retries and safe file-writing locally.

### 5. Websocket subscription construction is duplicated

The SDK already maps numeric exchange constants to segment strings and validates live-feed request types. Our code manually builds:

- live feed request 21 Full
- extra IDX ticker request 15
- depth request 23
- batching at 100 and 50

Recommended fix: do not replace the paper engine websocket loop wholesale yet. Instead, extract local subscription builders and packet parsers into a small module tested against SDK constants. The SDK's `MarketFeed` owns its own event loop, which does not fit cleanly inside our existing `asyncio` service.

## Why Not Replace Our Live Engine With SDK Classes Directly

Direct replacement would be risky right now.

The SDK websocket classes are convenient examples, but they are not drop-in production components for this engine:

- `MarketFeed` creates and owns a new event loop in `__init__`.
- `FullDepth` also creates its own event loop and prints subscription messages.
- The SDK parser returns one parsed message at a time and leaves application state management to the caller.
- It does not implement our raw capture, depth cache, stale quote policy, restart gates, or dashboard snapshot schema.

The right move is an adapter layer, not a wholesale rewrite.

## Current Issue Interpretation Against SDK

The SDK does not support the idea that our recent alert spam is caused by two independent Python processes failing together. In our code, collector and paper engine are same-process tasks.

The more likely issue family remains:

1. The single live-paper process stops refreshing snapshot tasks.
2. Health monitor sees stale process/feed/depth snapshots.
3. Health monitor auto-restarts live-paper.
4. Each restart triggers fresh chain fetches, subscriptions, Dhan 429s, depth warm-up gaps, and another alert wave.

The SDK comparison suggests the next diagnostic should be local instrumentation, not an SDK rewrite:

- event-loop lag metric in `run_paper_trading.py`
- per-snapshot write duration for `latest_process_health.json`, `latest_feed_state.json`, and `latest_depth_cache.json`
- separate timing for websocket receive, packet parse, raw flush, normalized parquet flush, and REST calls
- a restart cap after entry when `open_positions == 0`
- incident-based alert paging so one stale snapshot family is one incident, not dozens of phone alerts

## Changes Implemented (2026-05-14 / 2026-05-15)

### Done

#### 1. WD drive fsync hang — monitoring snapshot writes (2026-05-14, commit 3416739)

Root cause: `os.fsync()` inside thread-pool workers blocked indefinitely on WD external drive
I/O hiccups, stalling all asyncio tasks sharing the thread pool.

- `options_backtest/paper_engine.py` — `_write_atomic()` gained `sync: bool = True` param.
  All monitoring snapshot writes (`latest_feed_state.json`, `latest_quotes.json`,
  `latest_spot_bars.json`, `latest_process_health.json`, `latest_instrument_map.json`)
  changed to `sync=False`. Critical files (`latest_open_positions.json`, EOD summary) keep
  `sync=True`. Both `_write_checkpoint()` call sites converted to `asyncio.to_thread()`.
- `scripts/live/collect_order_book.py` — removed `os.fsync()` from `_write_atomic_json()`
  (used for `latest_depth_cache.json` and `latest_depth_collector_state.json`).
- `scripts/live/health_monitor.py` — removed `os.fsync()` from `_write_atomic_json()` and
  `_write_atomic_text()`.

#### 2. Startup wedge false positive and RECOVERED spam (2026-05-14, commit 9dec7de)

Root cause: health monitor fired `process_wedged` within seconds of a service restart (before
the new process wrote its first snapshot), then sent a burst of RECOVERED Telegram messages when
the engine came back.

- `scripts/live/health_monitor.py`:
  - Track rising edge (`_service_became_active_mono`, `_service_was_active`) when
    `live-paper.service` transitions from inactive to active. Suppress wedge detection for 90 s
    after the rising edge (accounts for `RestartSec=45` + `ExecStartPre` clock-sync + engine
    init time).
  - Store `first_seen` timestamp on each active alert record (preserved across repeat calls).
    RECOVERED Telegram is suppressed if the alert was active for less than 5 minutes.

#### 3. Per-symbol FINNIFTY depth threshold (2026-05-14, commit 3416739)

- `configs/live/wing6_4x1_all_vix_filtered.json` — added `depth_ready_threshold_pct: 60.0` for
  FINNIFTY (structural monthly illiquidity post weekly discontinuation, Nov 2024; observed
  readiness ~55–62 %, well above 60 % threshold).
- `scripts/live/health_monitor.py` — `_check_depth_readiness()` reads per-symbol and aggregate
  thresholds from profile instead of a single global constant.

#### 4. Auto-restart cap: post-entry window, no open positions (2026-05-15, this session)

Root cause: health monitor repeatedly restarted `live-paper.service` after the entry window
(09:20) even when no positions were open, creating a wedge → restart → chain-fetch 429 →
warm-up → wedge cycle with zero trading benefit.

- `scripts/live/health_monitor.py` — before triggering auto-restart, reads
  `latest_open_positions.json` and checks the current time against `global.entry_time` from the
  profile (with a 5-minute grace). If past entry and `open_positions == 0`, logs the suppression
  and skips restart. The `process_wedged` alert is still raised; only the restart is suppressed.

#### 5. Independent process-health watchdog loop (2026-05-15, this session)

Root cause: `_snapshot_loop` ran `_write_feed_state`, `_write_spot_bars`, and
`_write_process_health` sequentially via `asyncio.to_thread`. A WD drive stall blocking
`_write_feed_state` (large JSON) would delay `_write_process_health` by minutes, causing
`process_wedged` even though the engine was alive.

- `options_backtest/paper_engine.py`:
  - New `_process_health_loop()` coroutine writes `latest_process_health.json` (and
    `latest_instrument_map.json`) on its own independent 10 s cadence via `asyncio.to_thread`.
    This file is what the wedge watchdog checks; decoupling it ensures freshness even when
    the larger snapshot writes are slow.
  - `_snapshot_loop()` now runs `_write_feed_state` and `_write_spot_bars` concurrently via
    `asyncio.gather(..., return_exceptions=True)`.
  - `health_task` added to both shutdown `asyncio.gather` calls in `run()`.

### Remaining / Deferred

#### A. Snapshot files on WD drive — buffered-write stalls (not yet fixed)

Even without `fsync`, `os.rename()` (the atomic-swap step in `_write_atomic`) can block for
3–4 minutes if the kernel's write-back queue to the USB drive is saturated. This is the likely
remaining cause of periodic stale alerts after fix #1 above. The independent process-health loop
(fix #5) reduces the symptom (no more false `process_wedged`) but does not eliminate the
underlying stall.

Proposed fix: redirect monitoring snapshot files to a RAM-backed tmpfs (`/run/im-snapshots/`).
Only `latest_open_positions.json` and EOD summary files need WD persistence. Requires:
- `SNAPSHOT_DIR` env var in `.env.live` pointing to `/run/im-snapshots`
- `options_backtest/paper_engine.py`, `scripts/live/collect_order_book.py`, and
  `scripts/live/health_monitor.py` to respect `SNAPSHOT_DIR` when set
- `systemd-tmpfiles` config `d /run/im-snapshots 0755 root root -` on zimaos

#### B. Event-loop lag instrumentation (not yet added)

Add timing around key operations to confirm the stall source before further runtime changes:
- Per-snapshot write duration logged at DEBUG level
- Event-loop lag probe (periodic `asyncio.sleep(0)` latency measurement)
- Per-operation timing for websocket receive, packet parse, raw flush, parquet flush, REST calls

#### C. Incident-based alert grouping (not yet added)

One stale snapshot family should produce one Telegram incident, not one message per check cycle.
Group `feed_state_stale`, `depth_snapshot_stale`, `process_wedged`, `collector_heartbeat_stale`
under a single "engine stalled" incident key with a single open/recover notification.

#### D. HTTP boilerplate consolidation — `dhan_client.py` (deferred)

Five files manually build Dhan REST headers and sessions:
`live_resolver.py`, `paper_engine.py`, `dhan_connection_check.py`, `sample_option_chain.py`,
`download_dhan_expired_options.py`. Consolidate into `options_backtest/dhan_client.py` as a
thin adapter. Low risk, no behavioral change.

#### E. Shared option-chain fetch/parse module (deferred)

`LiveDhanContractResolver`, `sample_option_chain.py`, and `dhan_connection_check.py` each
implement expiry list + option chain fetch + response normalization. Move into one shared
module. Keep `sample_option_chain.py` as a CLI wrapper.

## Recommended Next Implementation Plan

1. ~~Add restart cap after 09:25 with no open positions.~~ ✓ Done (fix #4 above).
2. ~~Decouple `_write_process_health` from slow snapshot writes.~~ ✓ Done (fix #5 above).
3. Add event-loop/write-latency diagnostics (item B above) to confirm remaining stall source.
4. Redirect monitoring snapshots to tmpfs (item A above) once the stall source is confirmed.
5. Add `options_backtest/dhan_client.py` adapter (item D above).
6. Move shared option-chain parsing into shared module (item E above).
7. Incident-based alert grouping (item C above).

## Bottom Line

The SDK confirms that our endpoint choices are mostly correct and standard. The biggest simplification opportunity is reducing duplicated REST/header/payload code, not replacing the live engine.

The custom parts that matter for trading safety should stay: freshness checks, depth readiness, no-stale-entry behavior, raw/audit capture, restart rules, and dashboard health snapshots.

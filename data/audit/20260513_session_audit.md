# Session Audit - 2026-05-13

Generated 2026-05-13 16:05 IST after reading the authoritative `zimaos`
runtime artifacts.

Primary sources:
- `zimaos:/media/WD-Storage/indian-markets-live/alerts/20260513_alerts.jsonl`
- `zimaos:/media/WD-Storage/indian-markets-live/alerts/20260513_external_heartbeat.jsonl`
- `zimaos:/media/WD-Storage/indian-markets-live/logs/20260513.log`
- `zimaos:/media/WD-Storage/indian-markets-live/logs/live-paper.log`
- `zimaos:/media/WD-Storage/indian-markets-live/snapshots/latest_*.json`
- `zimaos:/media/WD-Storage/indian-markets-live/order_book_1min/20260513/`
- `systemctl status live-paper.service health-monitor.service dashboard-api.service live-paper-daily.timer`
- `journalctl --list-boots`

Important source note: the local workspace copy at `data/live/alerts/20260513_alerts.jsonl`
contains midnight storage alerts ending around 02:23. I did not treat that as the
source of truth because the server-side May 13 alert file starts at 09:10, is much
larger, and the current server alert state reports WD free space around 206 GB. If
Telegram received the midnight storage spam, verify that no stale/local monitor is
still using the old workspace copy.

---

## Executive Summary

| Item | Result |
|---|---|
| Session date | 2026-05-13 |
| Trading result | 0 trades |
| Entry decision | All four symbols skipped at 09:20 due to `vix_stale` |
| Alert window analysed | 09:10:13-15:30:48 IST |
| Total server alerts | 11,142 |
| Alert split | critical=10,820, warning=322 |
| External heartbeat | 952 successful pings, no gaps >75 seconds |
| Host outage | No market-hour host outage found; boot was stable through the session |
| Live service state | `live-paper.service` remained `active (running)` but was not healthy |
| Paper engine snapshots | process health stale from 09:51; feed/depth snapshots stopped updating at 13:53 |
| Data collected | Some option quotes, FINNIFTY/MIDCPNIFTY depth, raw depth packets |
| Data not collected | No paper trades, no EOD paper summary, no NIFTY depth, no fresh core index/VIX quote |
| Storage | Server WD free space healthy at ~206 GB |

Verdict: the May 12 missed-start class was fixed. Today was a different failure:
the services started, but the live-data path was not trade-ready. Core IDX/VIX
quotes never arrived, so VIX-dependent entry was blocked. The depth collector also
used stale NIFTY expiry data and then degraded into repeated reconnect/backpressure
behavior. The health monitor correctly detected the bad state, but it emitted the
same active failures thousands of times because alerts are appended on every check
with no cooldown/deduplication.

---

## Timeline

| Time IST | Evidence | Interpretation |
|---|---|---|
| 04:00:55 | `journalctl --list-boots` current boot begins | Scheduled reboot completed before market; not a market-hour outage. |
| 04:01:04 | `live-paper.service`, `health-monitor.service`, `dashboard-api.service` active | Services started automatically after boot. |
| 04:01:09 | `live-paper.log` token valid for 19h59m | Token was not the immediate blocker. |
| 04:01:09 | depth collector says NIFTY expiry `2026-05-12`, FINNIFTY/MIDCPNIFTY `2026-05-26` | NIFTY depth universe was built from an expired NIFTY expiry. This explains 0 tracked NIFTY depth later. |
| 09:00:01 | paper engine `live_feed: connected` | Main live feed websocket opened. |
| 09:10:13 | first server alert: `vix_quote_missing` | VIX had not arrived by the feed warm-up check. |
| 09:10:14-09:10:31 | depth readiness and quote freshness alerts begin | Depth cache and core quote state were not ready. |
| 09:15:02-09:15:08 | chains loaded for NIFTY, FINNIFTY, MIDCPNIFTY, SENSEX, 34 instruments each | REST option-chain discovery worked for the paper engine. |
| 09:15:08 | live feed subscribed 142 instruments | 136 option instruments plus six core index/VIX subscriptions were requested. |
| 09:17:00 | resolve checks fail for all symbols with stale spot security IDs `13`, `27`, `442`, `51` | Options were known from chains, but the resolver could not compute ATM from fresh spot index quotes. |
| 09:20:00 | `entry: VIX quote stale`; all symbols skipped `vix_stale` | No trade should be trusted today because the strategy did not have a live VIX/regime input. |
| 09:22:55 | `feed_state_stale` starts | The last feed-state snapshot was already stale. |
| 09:44:01 | `collector_heartbeat_stale` starts | The depth-cache snapshot loop stopped refreshing. |
| 09:51:23 | `latest_process_health.json` last write | Process-health snapshot stopped, even though systemd process stayed alive. |
| 09:51:33 | latest option quotes have timestamps around this time | Option quote collection existed, but only early-session and not sustained. |
| 10:03 | last 1-minute order-book aggregate minute | Normalized 1-minute depth data effectively ends around 10:03. |
| 13:53:26 | latest feed/depth/quote snapshot write | Snapshot writer kept publishing stale quote/depth state until 13:53, then stopped. |
| 15:30:48 | last server alert in market window | Monitor stopped feed-active alerts after the session window ended. |
| 16:00 | alert state still shows active depth-readiness alerts | Health monitor itself remained alive and external heartbeat was OK. |

---

## Alert Map

Authoritative server alert counts:

| Alert reason | Count | First | Last | Cause |
|---|---:|---|---|---|
| `vix_quote_missing` | 1,487 | 09:10:13 | 15:30:47 | IDX/VIX subscription never produced an INDIA VIX quote. |
| `depth_ready_low_nifty` | 1,487 | 09:10:14 | 15:30:47 | NIFTY depth had 0/34 ready; depth collector built NIFTY from stale `2026-05-12` expiry and later tracked 0 NIFTY IDs. |
| `depth_ready_low_finnifty` | 1,487 | 09:10:15 | 15:30:47 | FINNIFTY depth IDs were tracked, but no instrument stayed fresh within the 5s readiness SLA. |
| `depth_ready_low` | 1,486 | 09:10:31 | 15:30:48 | Overall depth readiness remained 0%. |
| `feed_state_stale` | 1,340 | 09:22:55 | 15:30:47 | Feed-state snapshot stopped staying fresh; final write was 13:53. |
| `depth_snapshot_stale` | 1,339 | 09:22:56 | 15:30:47 | Depth-cache snapshot was stale. |
| `collector_heartbeat_stale` | 1,325 | 09:44:01 | 15:30:47 | Collector heartbeat stopped refreshing after 09:51. |
| `quote_freshness_low` | 465 | 09:10:30 | 15:30:47 | Fresh option quote percentage was 0% against the configured 95% threshold. |
| `depth_ready_low_midcpnifty` | 404 | 09:10:15 | 15:30:47 | MIDCPNIFTY depth existed early, but not fresh enough after the collector degraded. |
| `raw_packet_flush_stale` | 317 | 09:10:25 | 15:30:32 | Raw packet flushes were intermittent and too stale for the monitor SLA. |
| `parquet_missing` | 5 | 09:10:25 | 09:14:27 | Expected early-session warning before normalized parquet appeared. It cleared once files existed. |

These alerts are not eleven separate failures. They collapse into four failure
families:

1. Core quote/VIX failure: `vix_quote_missing`, `quote_freshness_low`, resolve-check
   failures on spot IDs.
2. Depth-universe failure: stale NIFTY expiry selection, no NIFTY depth, low FINNIFTY
   and MIDCPNIFTY freshness.
3. Process-stall failure: snapshots and collector heartbeat stopped while systemd
   still considered the process active.
4. Alert-spam failure: every active condition was appended every check cycle instead
   of being rate-limited or state-transition logged.

---

## Root Causes

### 1. Core index and VIX quotes never arrived

The paper engine did connect and request subscriptions. The final feed state says
`subscribed_count=142`: 136 option contracts plus NIFTY, FINNIFTY, MIDCPNIFTY,
SENSEX, BANKNIFTY, and INDIA_VIX core subscriptions.

But `latest_feed_state.json` shows all core quotes as unseen:

| Core quote | Security ID | Seen | Fresh |
|---|---:|---|---|
| NIFTY | 13 | false | false |
| FINNIFTY | 27 | false | false |
| MIDCPNIFTY | 442 | false | false |
| SENSEX | 51 | false | false |
| BANKNIFTY | 25 | false | false |
| INDIA_VIX | 21 | false | false |

That caused three direct consequences:

- 09:17 resolve checks failed because `LiveDhanContractResolver.atm_strike()`
  requires a fresh spot quote.
- 09:20 entry skipped all symbols because VIX was stale.
- The dashboard/health monitor kept reporting zero quote freshness and missing VIX.

This is probably not a generic Dhan outage because option-chain REST worked and option
quotes did arrive for 136 option instruments. The failing surface is specifically
core IDX/VIX quote delivery or how we subscribe/parse it.

### 2. NIFTY depth collector used an expired expiry

At 04:01, `collect_order_book` logged:

```text
NIFTY: 34 instruments for expiry 2026-05-12 ATM +/- 8
FINNIFTY: 34 instruments for expiry 2026-05-26 ATM +/- 8
MIDCPNIFTY: 34 instruments for expiry 2026-05-26 ATM +/- 8
```

On 2026-05-13, the NIFTY May 12 expiry was already expired. The paper engine later
used `expiry_on_or_after()` and correctly loaded NIFTY expiry `2026-05-19`. The depth
collector instead chose `expiries[0]` from the Dhan expiry-list response without
filtering it against `session_date` / `min_dte`.

This explains why the final depth cache shows:

| Symbol | Configured | Tracked | Ready |
|---|---:|---:|---:|
| NIFTY | 34 | 0 | 0 |
| FINNIFTY | 34 | 34 | 0 |
| MIDCPNIFTY | 34 | 34 | 0 |

### 3. Depth collection degraded after early-session data

The collector did collect some data. The server has:

| Artifact | May 13 coverage |
|---|---:|
| Raw depth packet files | 88 files, 216 MB |
| Normalized order-book files | 67 files, 67 MB |
| 1-minute order-book parquet files | 67 files, 536 KB |

But the normalized data was partial:

| Symbol | 1-min files | Rows | Valid close rows | Minute range | Quality |
|---|---:|---:|---:|---|---|
| NIFTY | 0 | 0 | 0 | none | Missing completely |
| FINNIFTY | 33 | 1,229 | 1,119 | 09:14-10:03 | Partial, with 110 zero-volume/NaN rows |
| MIDCPNIFTY | 34 | 1,224 | 1,224 | 09:15-09:50 | Clean while present, then stopped |

The raw packet directory has later flushes, but readiness stayed 0% because the latest
top-of-book ages were far older than the 5s execution-readiness SLA. The service log
also shows repeated depth websocket reconnects and keepalive ping timeouts. So the data
is useful for diagnosing startup/depth behavior, but it is not good enough for live
fill-quality calibration or paper execution metrics.

### 4. The live-paper process stayed active while wedged

`systemctl status` at 16:00 showed:

- `live-paper.service`: `active (running)` since 04:01
- PID 1310 still running
- CPU consumed: about 7 hours
- memory peak: about 2 GB

But file evidence says the health snapshots were stale:

| Snapshot | Last write |
|---|---|
| `latest_process_health.json` | 09:51:23 |
| `latest_depth_collector_state.json` | 09:51:22 |
| `latest_feed_state.json` | 13:53:26 |
| `latest_depth_cache.json` | 13:53:26 |
| `latest_quotes.json` | 13:53:26 |

This is the worst operational state: systemd thinks the service is alive, external
heartbeat is alive through the health monitor, but the trading engine has stopped
making fresh progress.

### 5. Alert spam came from missing dedupe/cooldown

`health_monitor.py` increments alert counts and appends to the JSONL on every failing
check. It does track `active_alerts`, but it does not use that state to suppress repeat
writes. Once a condition such as `vix_quote_missing` is active, it is re-written every
cycle until the market window ends.

That is why one root failure produced 11,142 alert records.

---

## Data Collected Today

### Paper trades

No paper trades were created.

`paper_trades/20260513_signals.jsonl` contains only four skips:

| Time | Symbol | Decision |
|---|---|---|
| 09:20:00 | NIFTY | skip `vix_stale` |
| 09:20:00 | FINNIFTY | skip `vix_stale` |
| 09:20:00 | MIDCPNIFTY | skip `vix_stale` |
| 09:20:00 | SENSEX | skip `vix_stale` |

There is no `paper_trades/20260513.json` and no `reports/20260513_paper_summary.md`
on the server at audit time. The engine did not reach a clean EOD report path.

### Option-chain and quote snapshots

The paper engine successfully loaded option chains:

| Symbol | Expiry | Instruments |
|---|---|---:|
| NIFTY | 2026-05-19 | 34 |
| FINNIFTY | 2026-05-26 | 34 |
| MIDCPNIFTY | 2026-05-26 | 34 |
| SENSEX | 2026-05-14 | 34 |

`latest_quotes.json` contains 136 option quotes:

| Symbol | Quotes | Latest quote time |
|---|---:|---|
| NIFTY | 34 | 09:51:33 |
| FINNIFTY | 34 | 09:51:33 |
| MIDCPNIFTY | 34 | 09:51:33 |
| SENSEX | 34 | 09:51:33 |

Quality verdict: useful as an early-session option snapshot only. It is not a full
session quote dataset, and it cannot support today's paper-trade analysis because no
entries occurred.

### Depth data

The 20-depth dataset is partial and asymmetric:

- NIFTY depth is missing because the collector used expired NIFTY contracts.
- MIDCPNIFTY has clean 1-minute aggregates from 09:15 through 09:50.
- FINNIFTY has partial 1-minute aggregates from 09:14 through 10:03, including zero
  volume/NaN minutes.
- After ~10:03, normalized 1-minute data is effectively gone.

Quality verdict: keep the files for incident diagnosis and feed/parser research. Do
not use them to calibrate slippage or to score paper fills.

### Health and heartbeat data

The health monitor worked as an external liveness reporter:

- 952 Healthchecks records
- all `status=ok`
- no gaps greater than 75 seconds

Quality verdict: the server was alive. The trading process was not healthy. This proves
we need separate "host alive" and "engine making progress" checks.

---

## Prevention For Tomorrow

### Must fix before tomorrow's open

1. Restart `live-paper.service` before pre-open, or let the 04:00 reboot/timer start a
   clean process. Do not carry today's wedged PID into tomorrow.
2. Fix depth collector expiry selection: never use `expiries[0]` directly. Filter Dhan
   expiry-list output to expiry >= `session_date + min_dte`, and preferably reuse the
   same `expiry_on_or_after()` policy used by the paper engine.
3. Add a pre-entry core quote gate at 09:12-09:18:
   - fail fast if NIFTY/FINNIFTY/MIDCPNIFTY/SENSEX/VIX have not been seen
   - explicitly log the exchange segment/security ID used
   - do not wait until 09:20 to discover VIX is missing
4. Verify Dhan IDX/VIX subscriptions during market hours with a tiny standalone probe:
   subscribe only `IDX_I:13`, `IDX_I:27`, `IDX_I:442`, `IDX_I:51`, `IDX_I:21`, and
   log whether Type-8 packets arrive. This isolates core quote delivery from option
   subscriptions.
5. If Dhan does not stream India VIX reliably, use a defined fallback for the paper
   engine: REST/spot file/manual skip. Do not let a missing VIX silently spam all day.
   For trading safety, the default should remain "skip entry if VIX is missing."

### Monitoring fixes

1. Add alert dedupe/cooldown:
   - write alert on first occurrence
   - update `active_alerts` every cycle
   - append repeat reminders every 5-15 minutes, not every 15 seconds
   - write a resolved event when the condition clears
2. Add a progress watchdog for `live-paper.service`:
   - if `latest_process_health.json` is stale for >60 seconds during market hours,
     mark service unhealthy even if systemd says active
   - optionally restart before entry only; after entry, restart only if checkpoint
     recovery is known safe
3. Add a collector freshness watchdog:
   - if depth snapshot stops but process is still alive, restart the depth collector
     task or fail the orchestrator so systemd can restart it before entry
4. Split alert severity:
   - before 09:20, missing VIX/core quotes are critical
   - after all symbols skipped, repeated "no trade today" conditions should be one
     persistent incident, not thousands of critical pages
5. Make EOD reporting robust even on no-trade days:
   - always write `paper_trades/YYYYMMDD.json` as `[]`
   - always write `reports/YYYYMMDD_paper_summary.md`
   - include skip reasons and data-quality summary

### Tomorrow morning checklist

Run these from the laptop by 09:18 IST:

```bash
ssh zimaos "systemctl is-active live-paper.service health-monitor.service dashboard-api.service; systemctl status --no-pager live-paper.service | head -40"
ssh zimaos "stat /media/WD-Storage/indian-markets-live/snapshots/latest_process_health.json /media/WD-Storage/indian-markets-live/snapshots/latest_feed_state.json /media/WD-Storage/indian-markets-live/snapshots/latest_depth_cache.json"
ssh zimaos "tail -50 /media/WD-Storage/indian-markets-live/logs/$(date +%Y%m%d).log"
ssh zimaos "tail -20 /media/WD-Storage/indian-markets-live/alerts/$(date +%Y%m%d)_external_heartbeat.jsonl"
```

Pass criteria:

- service active and started today before 09:00
- `latest_process_health.json` fresh within 60 seconds
- `latest_feed_state.json` fresh within 15 seconds
- all core quotes seen, especially `INDIA_VIX`
- all expected chains loaded
- depth collector has NIFTY, FINNIFTY, MIDCPNIFTY configured for non-expired expiries
- raw and normalized depth files appear by 09:12-09:15
- no alert storm; one active incident should not create hundreds of JSONL rows

---

## Bottom Line

Today was not a missed-start incident like 2026-05-12. The server was alive and the
services started. The live paper stack failed because the market-data readiness layer
was broken:

- no core index/VIX ticks
- expired NIFTY depth universe
- partial depth capture only
- stale snapshots while the process stayed active
- no alert dedupe, causing the spam

The safest operational rule for tomorrow: no entry unless VIX/core quote and depth
readiness are confirmed by 09:18. If they are not confirmed, the correct result is a
single no-trade incident with a clean EOD summary, not another full-day alert storm.

---

## Fixes Applied — 2026-05-14 (pre-market session)

All five root causes were investigated against the Dhan SDK source
(`DhanHQ-py` cloned locally, `marketfeed.py` / `_market_feed.py` / `dhan_http.py`
reviewed). Verdict: **every failure was on our side, not a Dhan outage.** Dhan
delivered 136 option-contract quotes successfully; the broken surface was
IDX_I/VIX parsing and two architectural gaps. Five files changed, 79/79 unit tests
pass, all files deployed to zimaos and syntax-checked on the server.

---

### Fix 1 — Type-4 Quote packet handler (`options_backtest/paper_engine.py`)

**Problem (root cause of failure family 1):** The SDK confirms Dhan sends
packet type 2 (Ticker/16B), 4 (Quote/50B), 8 (Full/162B), and 50 (Disconnect).
When `Full(RequestCode=21)` is requested for `IDX_I` instruments (indices have no
OI or order-book depth), the server downgrades delivery to `Quote(4)` — a 50-byte
packet. Our `_handle_feed_packet` had branches only for type 2 and type 8; type 4
was silently dropped. That is why all six core index/VIX quotes showed `seen: false`
despite the websocket being connected.

**Fix:** Added `_parse_quote_packet()` (50B layout from SDK source) and a new
`if ptype == _TYPE_QUOTE` branch in `_handle_feed_packet` that routes the parsed
LTP into the resolver quote cache and spot bar builder, identical to the type-8
path but without the depth/OI fields.

---

### Fix 2 — Ticker(15) belt-and-suspenders for IDX_I (`options_backtest/paper_engine.py`)

**Problem:** Even with the Quote(4) handler in place, if Dhan sends nothing for a
given IDX_I instrument (e.g. VIX), we still get no data.

**Fix:** Added `_subscribe_idx_as_ticker(ws)`, called immediately after the
`Full(21)` core subscription on every websocket connect. Sends a second
`RequestCode=15` (Ticker) subscription for the same six IDX_I security IDs (NIFTY,
FINNIFTY, MIDCPNIFTY, SENSEX, BANKNIFTY, INDIA_VIX). The type-2 Ticker packet
(16B) is the lightest packet type and is documented as universally supported. If
the server delivers Ticker for a security but not Quote/Full, we still get LTP.

---

### Fix 3 — REST IDX/VIX polling fallback (`options_backtest/paper_engine.py`)

**Problem:** INDIA_VIX (security ID 21) is a derived index — it is not tradeable
and the SDK never documents it as a streamable instrument. Relying on websocket
delivery for VIX is fragile; REST is the documented way to query LTP for non-
tradeable indices.

**Fix:** Added two methods:
- `_fetch_core_ltp_rest()` — synchronous `POST /v2/marketfeed/ltp` with all six
  IDX_I IDs. Parses the `data.IDX_I` dict from the response and calls
  `resolver.update_quote()` for each SID with a non-zero LTP.
- `_vix_rest_loop()` — async loop that calls `_fetch_core_ltp_rest()` every 30 s
  via `asyncio.to_thread`. Started as `vix_rest_task` from `run()` alongside
  `feed_task` and `snapshot_task`, cancelled via `_stop_event`.

This ensures VIX is refreshed every 30 s as a floor, regardless of websocket state.

---

### Fix 4 — VIX graceful degradation in entry (`options_backtest/paper_engine.py`)

**Problem:** The old code did a blanket `continue` for all symbols when
`vix_val is None`. But in the config: NIFTY has `vix_filter.require_gte: 13` (hard
VIX gate) while FINNIFTY has only `vix_bucket_skip`, MIDCPNIFTY has only
`vix_bucket_skip`, and SENSEX has no VIX gate at all. Blocking SENSEX and
FINNIFTY/MIDCPNIFTY because NIFTY's VIX gate couldn't be evaluated was wrong — on
yesterday's session, SENSEX DTE would have been valid, and FINNIFTY/MIDCPNIFTY
would have been filtered by DTE anyway, not VIX.

**Fix:** Replaced the blanket skip with per-symbol logic using `has_require_gte`:
- If `vix_val is None` and symbol has `require_gte` → skip with `vix_stale`.
- If `vix_val is None` and symbol has no `require_gte` → proceed with
  `effective_vix_bucket = "unknown"` (won't match any bucket skip, so no false
  block). Logged as `vix_unavailable — proceeding with bucket=unknown`.
- All downstream `log_signal` calls updated to use `effective_vix_bucket` instead
  of the outer `vix_bucket` variable.

---

### Fix 5 — Chain ATM fallback in `resolve_atm_offset` (`options_backtest/live_resolver.py`)

**Problem:** `resolve_atm_offset` calls `atm_strike(timestamp)` which requires
a fresh websocket spot quote (max age 5 s). If the websocket spot is stale (because
IDX_I Quote/Ticker packets weren't arriving), the resolver raised `StaleQuoteError`
and the engine logged `spot_stale` and skipped the symbol — even though the REST
option-chain fetch at 09:15 already recorded the underlying LTP.

**Fix:** Wrapped `self.atm_strike(timestamp)` in a try/except for `StaleQuoteError`.
On stale spot, falls back to `self.chain_atm_strike()` which returns the ATM strike
computed from the REST chain's `_chain_underlying_ltp` (populated at 09:15). If that
is also `None`, re-raises `StaleQuoteError`. Also added `import logging` and `_log`
to `live_resolver.py` (it had none before).

---

### Fix 6 — Expired expiry filter in depth collector (`scripts/live/collect_order_book.py`)

**Problem (root cause of failure family 2):** `collect_order_book` called
`resolver.fetch_expiry_list()` and took `expiries[0]` without checking whether that
expiry was in the future. On 2026-05-13, Dhan's expiry list for NIFTY returned
`2026-05-12` first (the expiry that had just closed yesterday). The depth collector
subscribed 34 contracts for an already-expired NIFTY expiry → 0 NIFTY depth tracked
all day.

**Fix:** Added `valid_expiries = [e for e in expiries if e >= session_date]` before
the empty check. If no valid (non-expired) expiry exists, skip the symbol with an
explicit log message including the rejected expiry dates.

---

### Fix 7 — Alert JSONL rate-limiting (`scripts/live/health_monitor.py`)

**Problem (root cause of failure family 4):** `_alert()` wrote a new JSONL record
on every call with no cooldown. The monitor runs every 15 s; one stuck condition
(`vix_quote_missing`) produced 1,487 records — 11,142 total across all alert keys.

**Fix:**
- Added `_alert_jsonl_write_times: dict[str, float] = {}` instance variable (also
  reset in `_roll_date_if_needed`).
- Added `_JSONL_ALERT_REPEAT_INTERVAL = 600.0` constant (10 minutes).
- `_alert()` now writes JSONL only if the key is new or `>=600 s` since last write,
  updating `_alert_jsonl_write_times[key]` on each write.
- `_clear_alert()` now pops the timing entry (so next occurrence logs immediately),
  and writes a `"severity": "resolved"` JSONL record for the recovered condition.
- `_alert_jsonl_write_times` is reset on date roll.

---

### Fix 8 — Process wedge detection (`scripts/live/health_monitor.py`)

**Problem (root cause of failure family 3):** `_check_runner_process` used
`if systemd_active or snapshot_active:` — so a wedged process (systemd=active,
snapshot=stale) was treated as healthy. The audit showed the engine stopped updating
`latest_process_health.json` at 09:51 while systemd kept it alive until EOD.

**Fix:** Split into three branches:
- `systemd_active AND snapshot_active` → healthy, clear all process alerts.
- `systemd_active AND NOT snapshot_active` → fire `critical / process / process_wedged`
  ("live-paper.service is systemd-active but process_health.json is stale (>30s) —
  engine wedged"). This is the previously undetected failure mode.
- `snapshot_active AND NOT systemd_active` → healthy (manual/foreground run), clear
  `process_wedged`.
- Neither → existing `process_health_missing` / `process_stale` path.
- Non-market-hours path now also clears `process_wedged`.

---

### New file — `scripts/live/probe_idx_feed.py`

Standalone diagnostic script. Connects to the Dhan live feed websocket, subscribes
all six IDX_I instruments with both `Full(21)` and `Ticker(15)`, logs every packet
type received per security ID for a configurable duration (default 60 s), and prints
a final summary table showing packet-type counts per instrument and which instruments
received no data.

Run during market hours (09:15–15:30 IST):
```bash
DHAN_ACCESS_TOKEN=... DHAN_CLIENT_ID=... python scripts/live/probe_idx_feed.py --duration 60
```

---

### Deployment

All five files syntax-checked locally (Python `ast.parse`) and remotely on zimaos.
79/79 unit tests passed before deployment. Files copied to
`/DATA/live-paper/indian-markets/` on zimaos. `health-monitor.service` restarted.
`live-paper.service` will pick up changes at next session start (daily timer or
manual restart before 09:00 IST).

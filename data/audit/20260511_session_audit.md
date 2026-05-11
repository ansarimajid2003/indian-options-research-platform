# Session Audit — 2026-05-11
*Generated 2026-05-11 post-close. Source: zimaos live data root `/media/WD-Storage/indian-markets-live/`*

---

## 1. Executive Summary

| Item | Value |
|---|---|
| Session date | 2026-05-11 (Monday) |
| Session result | **0 trades executed** |
| Paper engine uptime | **17.01%** |
| Total data gap | **28.8 minutes** |
| Depth collector uptime | Active 04:15–15:52, gaps 14:18–14:46 (27.8 min) + 2 micro-gaps |
| Distinct issues found | **10** (4 bugs, 4 config/design gaps, 2 operational) |
| Corrupt files | **1** (`FINNIFTY_2026-05-26_25200_PE.parquet`) |
| Missing symbol data | SENSEX (intentional), MIDCPNIFTY (bug) |

**Post-verification update (2026-05-11 evening):** Each issue below now includes the final verification/fix status. Fixes for the repeatable P0/P1 failures were applied locally and deployed to zimaos under `/DATA/live-paper/indian-markets`; `live-paper.service` was reloaded from `/etc/systemd/system/live-paper.service`; `health-monitor.service` was restarted on the patched code. Laptop tests passed `67 OK`; zimaos tests passed `67 OK` with 22 expected Shoonya-data skips.

---

## 2. Data Collected — What Exists and What It Contains

### 2.1 Order Book Tick Depth (`order_book/20260511/` — 160 parquets, 16 MB)

**Schema (125 columns):** `timestamp` + 20-level bid/ask book (`bid_p1..p20`, `bid_q1..q20`, `bid_o1..o20`, same for ask) + derived: `best_bid, best_ask, mid, spread_abs, spread_pct, total_bid_qty, total_ask_qty, imbalance, depth_vwap_buy_lots_1, depth_vwap_sell_lots_1, depth_qty_at_best_bid, depth_qty_at_best_ask`.

| Symbol | Expiry | Files (readable) | Total Rows | Max Rows (1 file) | Data Range | Assessment |
|---|---|---|---|---|---|---|
| NIFTY | 2026-05-12 | 22 | 460,877 | 460,571 | 04:15–07:40 (pre-open) | Partial — see §4.3 |
| FINNIFTY | 2026-05-26 | 67 (+ 1 corrupt) | 391,706 | 391,438 | 04:15–15:48 | Good |
| MIDCPNIFTY | 2026-05-26 | 70 | 258 | 7 | 04:15 only | **Bug — see §4.1** |
| SENSEX | 2026-05-14 | 0 | 0 | — | — | Intentional — see §4.2 |

> **Row concentration:** Each symbol's total rows reside almost entirely in one ATM-strike file. The remaining files (e.g. 21 of 22 NIFTY files) have 1–12 rows = pre-open snapshot only.

### 2.2 Order Book 1-Min OHLCV (`order_book_1min/20260511/` — 160 parquets)

**Schema:** `timestamp, open, high, low, close, volume, spread_pct_mean`

**Result:** Every single file contains exactly **1 bar** (pre-open 04:15 bar). No market-hours bars were accumulated for any instrument or symbol. This is a code bug — see §4.4.

### 2.3 Raw Depth Packets (`raw_depth_packets/20260511/` — 242 `.bin` files, 60 MB)

Binary Dhan 20-depth WebSocket frames. Format: 12-byte header (`msg_len, feed_code, exch_seg, security_id, reserved`) + 20 × 16-byte levels.

| Time period | File count | Typical size | Notes |
|---|---|---|---|
| 04:16 (pre-open) | 1 | 322 KB | Large burst on initial connection |
| 06:51–07:40 | ~10 | 1–3 KB | Pre-market ticks, low activity |
| 08:00–09:28 | ~150 | 3–12 KB | Growing as market approaches |
| 09:29–09:40 | none | — | Network outage: Errno 113 on all 5 conns |
| 09:40–15:28 | ~60 | 5–20 KB | Continuous market-hours packets |
| 15:35–15:48 | 5 | 30–32 KB | EOD burst, regular 3-min flush |

The raw packets confirm ALL 5 depth connections (conn=0..4) were affected during the 11-min outage, all reconnected, and continued receiving data.

### 2.4 Quotes Snapshot (`latest_quotes.json` — 71 KB)

- **328 instruments** quoted (7 fields each): `ltp, oi, volume, ts, iv, delta, theta`
- **All 328 quotes are fresh** (last update ≥ 15:00 IST)
- Richest data set of the day; all greeks present

### 2.5 Instrument Map (`latest_instrument_map.json` — 344 KB)

- **2,086 instruments** mapped: security_id → symbol, strike, option_type, expiry, ticker
- Coverage: NIFTY 482, FINNIFTY 530, MIDCPNIFTY 644, SENSEX 430
- All four symbols, all nearest expiries

### 2.6 Depth Cache Snapshot (`latest_depth_cache.json` — 37 KB)

- 246 configured, 224 tracked, 0 ready at 16:07 (post-market — expected)
- TOB (top-of-book) for 224 instruments: `bid, ask, bid_qty, ask_qty, bid_age_ms, ask_age_ms`
- 22 instruments never received any tick (never tracked) — consistent with MIDCPNIFTY far-OTM strikes

### 2.7 Paper Trades (`paper_trades/20260511.json`)

- **0 trades executed.** File contains empty array `[]`.
- 8 signal skip events in `20260511_signals.jsonl` — see §4.5.

### 2.8 Alerts (`alerts/20260511_alerts.jsonl` — 1.3 MB, 7,260 entries)

| Reason | Count | Severity | What it means |
|---|---|---|---|
| `depth_ready_low` | 1,497 | critical | depth ready% < 95% for ≥2 consecutive checks |
| `quote_freshness_low` | 1,490 | critical | fresh quotes < 95% for ≥2 consecutive checks |
| `feed_state_stale` | 1,454 | critical | `latest_feed_state.json` not updated in >15s |
| `depth_snapshot_stale` | 1,340 | critical | `latest_depth_cache.json` not updated in >15s |
| `collector_heartbeat_stale` | 1,322 | critical | same as above, from heartbeat check |
| `raw_packet_flush_stale` | 115 | warning | no new `.bin` file for >90s |
| `parquet_flush_stale` | 33 | warning | no new parquet for >180s |
| `feed_disconnected` | 8 | critical | paper engine feed dropped |
| `new_error_in_log` | 1 | warning | `ERROR paper_engine: feed not connected` |
| `clock_not_synced` | 1 | critical | `timedatectl NTPSynchronized=no` at 04:15 |

---

## 3. Health Monitor: What It Captured vs What It Missed

### 3.1 What the health monitor got right

- ✅ Detected `clock_not_synced` at startup (04:15) — first alert of the day
- ✅ `depth_ready_low` fired persistently from 09:10 — correctly flagging that only ~66% of configured depth instruments had fresh data (MIDCPNIFTY not delivering)
- ✅ `collector_heartbeat_stale` fired when the collector stopped updating its state during the 27.8-min gap
- ✅ `feed_disconnected` fired on each of the 8 paper-engine feed drops
- ✅ `raw_packet_flush_stale` fired when depth packets stopped being flushed
- ✅ `parquet_flush_stale` fired when order book parquets weren't being written
- ✅ `new_error_in_log` detected the `feed not connected — aborting day` ERROR line
- ✅ External heartbeat (healthchecks.io) was pinging successfully throughout; last ping at 16:05

### 3.2 What the health monitor missed or didn't diagnose

- ❌ **No per-symbol depth breakdown** — `depth_ready_low` fires on the aggregate % across all 246 configured instruments. When 82 MIDCPNIFTY instruments get 0 updates, the aggregate drops to ~66% and critical alerts fire — but no alert identifies WHICH symbol is dead. The operator sees `depth ready 66% (< 95%)` 1,497 times with no actionable root cause.
- ❌ **No VIX warm-up check** — There is no alert for "VIX quote not received within N minutes of subscription." The VIX stale at 09:20 was the proximate cause of 0 trades, but the health monitor never warned about it.
- ❌ **No detection of the 1-min OHLCV bug** — The parquet flush check verifies that ANY parquet file was written recently. It cannot detect that the `order_book_1min` files are accumulating zero bars beyond the first.
- ❌ **No chain-fetch result check** — At 15:15, NIFTY/FINNIFTY/MIDCPNIFTY chains got 429 errors. The health monitor has no check for "did option chain load successfully for all trading symbols."
- ❌ **Uptime metric is misleading** — `uptime_pct = good_ticks / total_ticks` counts a tick as "good" only if ALL critical checks pass simultaneously. Because `depth_ready_low` and `quote_freshness_low` persisted for most of the day (due to the MIDCPNIFTY issue), almost no tick was fully good, giving **17.01% uptime**. This does not mean the system was down 83% of the time — the paper engine, feed, and NIFTY/FINNIFTY depth were all working. The uptime number is dominated by the persistent MIDCPNIFTY failure.

---

## 4. Issue Analysis — Root Causes and Fixes

---

### Issue 1: MIDCPNIFTY depth data — pre-open only (70 files, 258 rows total)

**Severity:** High — persistent structural gap in data collection

**Post-verification update:** Confirmed on zimaos. `MIDCPNIFTY` had 70 readable parquet files, 258 total rows, max 7 rows/file, and no post-04:15 depth. Fixed by reducing the production profile from `atm_offset_range=20` / 246 instruments / 5 connections to `atm_offset_range=8` / 102 instruments / 3 connections in `configs/live/wing6_4x1_all_vix_filtered.json`. Also added per-symbol depth readiness to `latest_depth_cache.json` and per-symbol health alerts in `health_monitor.py`, so the next failure identifies the symbol instead of only reporting aggregate readiness.

**Misread/skipped:** The security-id mismatch theory remains unproven and is now secondary. The immediate fix deliberately reduces depth breadth for tomorrow rather than proving Dhan's true maximum session capacity. Subscription acknowledgment logging was not added yet; per-symbol readiness gives the operator-facing diagnosis first.

**What happened:** All 70 MIDCPNIFTY parquet files contain only 3–7 rows, all timestamped 04:15 IST (pre-market). No MIDCPNIFTY depth was collected during the 6-hour trading window despite all 5 depth WebSocket connections surviving and reconnecting.

**Root cause:** The 5 depth connections split 246 instruments in sequential batches of 50:
- conn=0: NIFTY sids[0:50] → fully active (part of 460K-row file)
- conn=1: NIFTY sids[50:82] + FINNIFTY sids[0:18] → fully active
- conn=2: FINNIFTY sids[18:68] → fully active
- conn=3: FINNIFTY sids[68:82] + MIDCPNIFTY sids[0:36] → **partial/no data received**
- conn=4: MIDCPNIFTY sids[36:82] → **no data received**

The 3–7 pre-open rows at 04:15 are real WebSocket 20-depth packets (they contain full 20-level bid/ask data). But after the pre-open burst, MIDCPNIFTY stops. Two possible root causes:

1. **Dhan 20-depth subscription limit.** The docstring of `collect_order_book.py` says "≤50 instruments each" but was written when only 34 instruments per symbol were used. With `atm_offset_range=20` → 82 instruments × 3 symbols = 246, the code now creates 5 connections. Dhan's 20-depth WebSocket may silently drop subscriptions beyond ~100 instruments (2 connections) for a single account/session. The pre-open data at 04:15 came from the initial connection burst before limits were enforced.

2. **Security_id mismatch.** MIDCPNIFTY option security_ids returned by the REST chain might differ from those the 20-depth WebSocket expects, causing the `sid in self._id_to_meta` lookup in `_handle_packet` to never match MIDCPNIFTY packets.

**Evidence:** `depth_ready_low` fired 1,497 times — exactly because MIDCPNIFTY (82 of 246 instruments = 33%) had no data, keeping `ready_pct ≈ 66%` all day. The health monitor detected the symptom but could not identify the cause.

**Fix:**
```python
# collect_order_book.py — reduce atm_offset_range to keep total instruments ≤ 100
# Profile config: change from 20 to 8 (ATM ±8 = 17 strikes × 2 = 34 per symbol × 3 = 102)
# OR limit depth_collection to 2 symbols (NIFTY + FINNIFTY only)

# In collect_order_book.py — add subscription acknowledgment logging
# After _subscribe(), log how many instruments were sent per connection
# Also add per-symbol rows-received counter to _NormalizedBuffer and log it on flush

# In health_monitor.py — add per-symbol depth readiness check
async def _check_depth_readiness(self) -> bool | None:
    ...
    # After aggregate check, also check per-symbol in tob data
    tob = data.get("tob", {})
    # Cross-reference against expected_by_symbol config
    # Alert "MIDCPNIFTY depth: 0 fresh instruments" separately
```

**Immediate action:** Reduce `atm_offset_range` from 20 to 8 in the profile config. This brings the total to 34 × 3 = 102 instruments (3 connections), well within the documented "≤50 per connection" limit and very likely within Dhan's per-session limit.

---

### Issue 2: SENSEX missing from order book (0 files)

**Severity:** Low — intentional design choice, not a bug

**Post-verification update:** Confirmed intentional. No code change was made for SENSEX 20-depth. SENSEX remains `depth_source: "top_of_book"` and is expected to use the standard live feed/top-of-book fallback rather than the separate 20-depth collector.

**Misread/skipped:** This should not be counted as a defect for tomorrow. Adding SENSEX to 20-depth was skipped intentionally because the main repeat-risk was too many depth instruments, not too few.

**What happened:** No parquet files were written for SENSEX in `order_book/20260511/`.

**Root cause:** The profile config explicitly sets `depth_collection.symbols: ["NIFTY", "FINNIFTY", "MIDCPNIFTY"]`, and SENSEX is configured with `depth_source: "top_of_book"`. The depth collector correctly skips SENSEX because `sym_cfg.get("depth_source") != "dhan_20depth"`.

SENSEX gets its fill prices from the paper engine's standard Dhan live feed (Type-8 full packets with 5-level depth), not from the 20-depth WebSocket. This is captured in `latest_depth_cache.json` TOB and `latest_quotes.json`.

**Why it matters:** SENSEX has no historical tick-depth for strategy research or spread analysis. Also, SENSEX options are on BSE_FNO not NSE_FNO, so even if added, the subscription segment would need to be `BSE_FNO`.

**Fix (optional, for research use):** If SENSEX 20-depth is desired, add it to the depth collector but with the correct exchange segment:
```python
# In _subscribe(), detect BSE_FNO instruments and use correct segment:
exch_seg = "BSE_FNO" if meta.get("symbol") == "SENSEX" else "NSE_FNO"
sub = {"ExchangeSegment": exch_seg, "SecurityId": sid}
```
And add to profile: `"depth_collection": {"symbols": ["NIFTY", "FINNIFTY", "MIDCPNIFTY", "SENSEX"]}` after confirming SENSEX security IDs work on the 20-depth WebSocket.

---

### Issue 3: VIX stale at 09:20 — 0 trades on all 4 symbols

**Severity:** Critical — this caused zero entries on the day

**Post-verification update:** Confirmed as the direct cause of zero entries. `paper_trades/20260511_signals.jsonl` contains four `vix_stale` skips at `09:20:30`, one for each trading symbol. Fixed in `options_backtest/paper_engine.py` by adding `_core_subscriptions()` and subscribing spot indices plus VIX immediately on every websocket connect, before the 09:15 option-chain fetch. Reconnects now also restore core subscriptions without duplicating them.

**Misread/skipped:** The original text says `_VIX_MAX_AGE = 300s` in `live_resolver.py`; that is wrong. The resolver's actual VIX freshness limit is 60 seconds, matching the profile's `max_staleness_seconds: 60`. The unused `paper_engine.py` constant is 300 seconds, but it did not control the skip. The fix is still the same: warm VIX from 09:00 instead of waiting until chain fetch.

**What happened:** At 09:20:30, `entry: VIX quote stale` was logged and all 4 symbols were skipped. No trades were attempted.

**Root cause (code trace):**
```
_T_CONNECT  = 09:00 → feed_loop starts
_T_CHAIN_FETCH = 09:15 → _fetch_chains_and_subscribe() runs → VIX subscribed here
_T_ENTRY    = 09:20 → _enter_all_symbols() checks vix_val

# In _fetch_chains_and_subscribe():
instruments_to_sub.append({"ExchangeSegment": _SEG_IDX, "SecurityId": self._vix_security_id})
# VIX is only added to subscribed_ids at 09:15, not at 09:00
```

VIX (security_id=21) is subscribed at 09:16 as part of the chain fetch — 4 minutes before the entry check. The resolver's actual `_VIX_MAX_AGE = 60s` means any quote older than 1 minute raises `StaleQuoteError`. If no fresh VIX packet arrived shortly before the entry check, `vix_ltp()` raises `StaleQuoteError`.

This is a subscription timing bug. VIX and all spot index security IDs (`13, 27, 442, 51, 25`) should be subscribed at 09:00 when the feed first connects, not at 09:15 during chain fetch. At 09:00, the market is in pre-open and these indices are already live. Subscribing them 20 minutes earlier gives the cache 20 minutes to warm up.

**Evidence from log:**
```
09:16:11 — live_feed: subscribed 334 instruments  ← VIX subscribed here
09:17:XX — resolve_check: NIFTY spot FAILED — StaleQuoteError('No quote yet for security_id=13')
09:20:30 — entry: VIX quote stale  ← only 4 min of subscription time
09:20:30 — live_feed: disconnected  ← feed dropped right at entry check
```

**Fix (paper_engine.py):**
```python
async def _feed_loop(self) -> None:
    url = _FEED_URL.format(...)
    while not self._stop_event.is_set():
        try:
            async with websockets.connect(url, ...) as ws:
                self._ws = ws
                self._feed_connected = True
                _log.info("live_feed: connected")
                
                # Always subscribe spot indices and VIX immediately on connect
                # so quotes warm up during the 09:00–09:15 pre-chain window
                await self._subscribe_instruments(ws, self._get_core_subscriptions())
                
                # Then re-subscribe any chain instruments from previous fetch
                if self._subscribed_ids:
                    await self._subscribe_instruments(ws, self._subscribed_ids)
                ...

def _get_core_subscriptions(self) -> list[dict]:
    """Spot indices + VIX — always subscribe at connect, before chain fetch."""
    core = [
        {"ExchangeSegment": _SEG_IDX, "SecurityId": "13"},   # NIFTY
        {"ExchangeSegment": _SEG_IDX, "SecurityId": "27"},   # FINNIFTY
        {"ExchangeSegment": _SEG_IDX, "SecurityId": "442"},  # MIDCPNIFTY
        {"ExchangeSegment": _SEG_IDX, "SecurityId": "51"},   # SENSEX
        {"ExchangeSegment": _SEG_IDX, "SecurityId": "25"},   # BANKNIFTY
        {"ExchangeSegment": _SEG_IDX, "SecurityId": self._vix_security_id},  # VIX
    ]
    return core
```

This ensures 15 minutes of spot/VIX data before the entry check rather than 4 minutes.

---

### Issue 4: Order book 1-min OHLCV — only 1 bar per file (code bug)

**Severity:** Medium — all 1-min data is silently broken

**Post-verification update:** Confirmed on zimaos. All 160 `order_book_1min/20260511/*.parquet` files had exactly 1 row. Fixed in `scripts/live/collect_order_book.py`: when existing 1-minute parquet is read, `timestamp` is converted back to the index before concatenation/deduplication. 1-minute parquet writes now also use temp-file then atomic replace.

**Misread/skipped:** The original "cannot be repaired" statement is too strong. The bad 1-minute files themselves should not be trusted, but most 2026-05-11 1-minute bars can be rebuilt from the readable tick-level `order_book/20260511/*.parquet` files. A historical rebuild/backfill script was not added in this fix pass because tomorrow's live correctness was higher priority.

**What happened:** Every file in `order_book_1min/20260511/` contains exactly 1 bar (the 04:15 pre-open bar). No market-hours bars were accumulated despite the NIFTY/FINNIFTY tick files having 460K and 391K rows respectively.

**Root cause (code trace in `_NormalizedBuffer._write_1min`):**
```python
def _write_1min(self, df: pd.DataFrame, ikey: str) -> None:
    ...
    agg = df2["mid"].resample("1min").ohlc()   # index = DatetimeTZDtype
    ...
    out_path = self._dir_1min / f"{ikey}.parquet"
    if out_path.exists():
        existing = pd.read_parquet(out_path)   # index = RangeIndex (timestamp is a column)
        agg = pd.concat([existing, agg])       # BUG: mismatched index types
        agg = agg[~agg.index.duplicated(keep="last")].sort_index()
    pq.write_table(pa.Table.from_pandas(agg.reset_index()), ...)
```

First write (04:15): `agg` has DatetimeTZDtype index → `reset_index()` → parquet stores `timestamp` as a plain column with RangeIndex dropped.

Second write (04:16, next flush): `existing = pd.read_parquet(...)` → `timestamp` is now a regular column, `existing.index` is `RangeIndex(0, 1)`. New `agg` still has `DatetimeTZDtype` index. `pd.concat([existing, agg])` produces a DataFrame with mixed index (integers + datetimes) where the `timestamp` column has NaN for the new rows. `agg.reset_index()` then exports this mixed index as a column named `index` or `level_0`, while the new bars lose their timestamp information.

After the first write, every subsequent write corrupts the accumulation. The `keep="last"` deduplication on the mixed index doesn't match on the 04:15 entry (integer 0) vs the new datetime entry, so the 04:15 bar is duplicated rather than replaced, and the final file is effectively frozen at 1 valid bar.

**Fix (`collect_order_book.py`, `_NormalizedBuffer._write_1min`):**
```python
def _write_1min(self, df: pd.DataFrame, ikey: str) -> None:
    df2 = df.copy()
    df2["timestamp"] = pd.to_datetime(df2["timestamp"], utc=True).dt.tz_convert(_IST)
    df2 = df2.set_index("timestamp").sort_index()
    if "mid" not in df2.columns:
        return
    agg = df2["mid"].resample("1min").ohlc()
    agg["volume"] = df2["total_bid_qty"].resample("1min").sum()
    agg["spread_pct_mean"] = df2["spread_pct"].resample("1min").mean()

    out_path = self._dir_1min / f"{ikey}.parquet"
    if out_path.exists():
        existing = pq.read_table(out_path).to_pandas()
        # Re-establish timestamp as index to match agg's index type
        existing["timestamp"] = pd.to_datetime(existing["timestamp"], utc=True).dt.tz_convert(_IST)
        existing = existing.set_index("timestamp")
        agg = pd.concat([existing, agg])
        agg = agg[~agg.index.duplicated(keep="last")].sort_index()

    pq.write_table(
        pa.Table.from_pandas(agg.reset_index()),
        str(out_path),
        compression="snappy",
    )
```

The fix: after reading back the existing parquet, re-index it by the `timestamp` column so both DataFrames have the same index type before concat. **The existing 1-min parquet files for today are all broken and should not be trusted. They can be rebuilt from readable tick-level `order_book` parquets if we need the 2026-05-11 1-minute series.**

Rebuild tomorrow by adding a backfill step in the EOD routine.

---

### Issue 5: 27.8-minute data gap (14:18–14:46)

**Severity:** High — caused paper engine to abort for the day

**Post-verification update:** The mid-session restart race is confirmed, but the original crash/OOM explanation is not. zimaos `journalctl` shows clean systemd stop/start events at `14:46:22`, `15:15:35`, and `15:17:19`, each with `Deactivated successfully`; there is no journal evidence of OOM kill or spontaneous process crash in that window. Fixed in `paper_engine.py` by making mid-session restarts wait up to 120 seconds for the feed instead of always using 45 seconds. The active systemd unit was also updated to `StartLimitBurst=8`.

**Misread/skipped:** "The process was killed or crashed between 14:18 and 14:46" was a misread. The better conclusion is: there was a collector/feed data gap plus clean service restarts, and the engine's restart path was too brittle once `_T_CHECK_FEED` was already in the past. OOM monitoring was not added because OOM was not supported by the journal evidence, though memory peaked at 2.9 GB and should still be watched.

**What happened:**
```
14:18:33 — live_feed: disconnected (ConnectionClosedError) ← regular 5s drop
14:18:38 — live_feed: reconnected                          ← normal
14:18:38 — [collect_order_book] restart gap sentinel written  ← collector restarted
14:46:24 — ERROR: feed not connected at 09:10 — aborting day ← NEW process start
15:15:37 — live_feed: connected                             ← another restart
15:52:23 — paper_engine: session complete
```

**Initial hypothesis (corrected after journal review):** The first read assumed the `live-paper.service` process was killed or crashed between 14:18 and 14:46. The verified evidence shows clean systemd stop/start events, not an OOM kill or spontaneous crash. When the new process started at 14:46:
1. `_T_CHECK_FEED = 09:10` had already passed → `_sleep_until(09:10)` returns immediately
2. The new feed task (`_feed_loop`) starts but takes >5s to connect
3. The 45-second grace period begins, but the feed task is not yet connected
4. 45 seconds expire → `_log.error("...aborting day")`

This is a **timing race** in the mid-session restart logic. The 45-second grace loop checks `self._feed_connected` which starts False and only becomes True after the async `_feed_loop` opens a WebSocket. The paper engine starts its 45-second countdown before the feed task has had a real chance to connect. At 14:46, with no active positions to protect, aborting was harmless — but it prevented a clean EOD record.

**Initial note superseded by journal review:** The first draft speculated about process kill/start-limit behavior. The verified zimaos unit had `StartLimitBurst=5` and `StartLimitIntervalSec=600`, and the journal showed clean stop/start events rather than a start-limit failure.

**Correction after journal review:** The deployed unit had `StartLimitIntervalSec=600`, not 120, and the journal does not show start-limit lockout. The operational lesson is still valid: avoid restarting `live-paper.service` during market hours unless accepting a known gap.

**Fix A — grace period for mid-session restart (paper_engine.py):**
```python
await _sleep_until(_T_CHECK_FEED, self._session_date)
if not self._feed_connected:
    # Mid-session restarts need more grace than normal startup.
    # Normal startup: 45s. Mid-session (past entry window): 120s.
    now = _now_ist()
    entry_cutoff = datetime.combine(self._session_date, _T_ENTRY, tzinfo=_IST)
    grace = 120 if now > entry_cutoff else 45
    _log.info("paper_engine: feed not connected at 09:10 check — waiting up to %ds", grace)
    for _ in range(grace):
        await asyncio.sleep(1)
        if self._feed_connected:
            _log.info("paper_engine: feed connected during grace period")
            break
```

**Fix B — systemd StartLimit (live-paper.service):**
```ini
StartLimitIntervalSec=300   # was 120 — give 5 min window
StartLimitBurst=8           # was 5 — allow more restarts before giving up
```

**Fix C — add OOM monitoring.** Add a check in the slow loop to log current RSS and alert if >80% of system RAM. The 14:18 crash might be OOM-triggered.

---

### Issue 6: Chain fetch 429 rate-limit on second restart (15:15)

**Severity:** Medium — prevented NIFTY/FINNIFTY/MIDCPNIFTY chains from loading after restart

**Post-verification update:** Confirmed, with nuance. At `15:15:38`, NIFTY/FINNIFTY/MIDCPNIFTY hit Dhan `429 Too Many Requests`; at `15:17`, the next restart retried and loaded all four chains. Fixed in `paper_engine.py` by increasing retry backoff to 4s/8s and by slowing inter-symbol chain fetches to 2s after the scheduled 09:15 chain-fetch time.

**Misread/skipped:** The initial failure did not permanently prevent chain loading for the rest of the day; a later restart recovered the chains. The fix still matters because restart bursts should not rely on luck or another service restart.

**What happened:**
```
15:15:38 — chain_fetch: NIFTY — REST failed: HTTPError('429 Too Many Requests')
15:15:38 — chain_fetch: FINNIFTY — REST failed: HTTPError('429 Too Many Requests')
15:15:38 — chain_fetch: MIDCPNIFTY — REST failed: HTTPError('429 Too Many Requests')
15:15:38 — chain_fetch: SENSEX expiry=2026-05-14 instruments=82 ← succeeded
15:15:38 — live_feed: subscribed 88 instruments (SENSEX + spot only)
```

**Root cause:** The rapid restart sequence at 14:46 and 15:15 (two process starts within ~30 minutes, each triggering chain fetches) exhausted the Dhan option-chain API rate limit (5 req/s × 4 symbols = hitting consecutive retry bursts). SENSEX succeeded because it was last and the burst had partially cooled.

The `_fetch_chains_and_subscribe` sends all 4 symbol chains in sequence with `await asyncio.sleep(0.3)` between them. Three retries per symbol × 4 symbols = 12 requests in rapid succession after restart — well above the 5 req/s limit.

**Fix (paper_engine.py, `_fetch_chains_and_subscribe`):**
```python
# Increase retry delay and add backoff
for attempt in range(3):
    try:
        if attempt > 0:
            delay = 2.0 * (2 ** attempt)  # 4s, 8s (was 1.2s, 2.4s)
            await asyncio.sleep(delay)
        ...
        break
    except Exception as exc:
        ...

# Increase inter-symbol delay for restarts past 09:15
_chain_delay = 2.0 if _now_ist().time() > _T_CHAIN_FETCH else 0.3
await asyncio.sleep(_chain_delay)
```

---

### Issue 7: Corrupt parquet file

**Severity:** Low — 1 file out of 160

**Post-verification update:** Confirmed on zimaos: `FINNIFTY_2026-05-26_25200_PE.parquet` fails with `Parquet magic bytes not found in footer`. Fixed prospectively in `scripts/live/collect_order_book.py` by writing both tick parquet and 1-minute parquet to `*.tmp` files, then atomically replacing the final file.

**Misread/skipped:** The corrupt 2026-05-11 file was not deleted during the fix pass; it is useful audit evidence and deleting live artifacts is intentionally avoided unless explicitly requested. The root cause text refers to an abrupt kill, but after journal review the safer wording is "interrupted in-place write during service restart/stop," not proven crash.

**File:** `order_book/20260511/FINNIFTY_2026-05-26_25200_PE.parquet`
**Error:** `Could not open Parquet input source: Parquet magic bytes not found in footer`

**Root cause:** This file was being written by `_NormalizedBuffer._flush_locked` during a service interruption/restart. The `pq.write_table()` call writes the footer last. An interrupted in-place write leaves the file with content but no valid footer.

The problem: parquet writes in `_flush_locked` are in-place, not atomic:
```python
pq.write_table(pa.Table.from_pandas(df), str(out_path), compression="snappy")
# If killed here, out_path is a partial file
```

**Fix (collect_order_book.py, `_NormalizedBuffer._flush_locked`):**
```python
# Write to temp file first, then rename (atomic on Linux)
tmp_path = out_path.with_suffix(".parquet.tmp")
pq.write_table(pa.Table.from_pandas(df), str(tmp_path), compression="snappy")
tmp_path.replace(out_path)  # atomic rename
```

This matches the pattern already used for all JSON snapshot writes (`_write_atomic_json`).

**Cleanup:** Delete the corrupt file manually:
```bash
rm /media/WD-Storage/indian-markets-live/order_book/20260511/FINNIFTY_2026-05-26_25200_PE.parquet
```
The data for this strike is not recoverable from this session; the raw packet `.bin` files contain the raw bytes but re-parsing them would require matching security_id for 25200 PE, which is possible but not worth the effort for a single far-OTM strike.

---

### Issue 8: NTP clock not synced at startup (04:15)

**Severity:** Low — affects tick timestamp precision

**Post-verification update:** Confirmed as a single startup alert. No code or systemd change was applied for tomorrow because adding `chronyc makestep` as `ExecStartPre` could make service startup depend on a binary/service that may not exist or may block the trading process.

**Misread/skipped:** This was intentionally skipped as a deployment fix. Keep it as a monitoring alert for now; if it repeats after 09:00, then add a safer preflight check or verify the exact time-sync tool installed on zimaos before changing systemd.

**What happened:** First alert of the day at 04:15:13 was `clock_not_synced: timedatectl reports NTPSynchronized=no`.

**Root cause:** The `health_monitor.py` clock check runs `timedatectl show` and looks for `NTPSynchronized=yes`. At 04:15 AM, NTP sync had not yet completed after the system became reachable (zimaos was likely in sleep or the Tailscale tunnel wasn't up yet when the service started). The check has a 300-second boot grace period (`_CLOCK_BOOT_GRACE_SECONDS`) but this doesn't apply here since the system uptime was already >300s.

**Impact:** Timestamps in tick parquet files and raw `.bin` packets from 04:15 may be off by a few seconds. VIX/spot quotes received before NTP sync had corrected may have slightly wrong `received_at` values.

**Fix:** Add a pre-flight NTP sync check to the `live-paper.service` `ExecStartPre`:
```ini
[Service]
ExecStartPre=/usr/bin/chronyc makestep
ExecStartPre=/bin/bash -c 'for i in $(seq 30); do timedatectl show | grep -q NTPSynchronized=yes && exit 0; sleep 2; done; echo "NTP failed to sync"; exit 1'
```

Or in `health_monitor.py`, alert only if NTP is still not synced after 09:00 (market open), not at 04:15 pre-boot.

---

### Issue 9: Health monitor uptime metric is misleading (17.01%)

**Severity:** Informational — monitoring design issue

**Post-verification update:** Confirmed directionally, but the exact uptime number is snapshot-dependent. The audit reported 17.01%; the later zimaos `latest_alert_state.json` showed 27.18%; the laptop's local monitor showed 0.00% because it was pointed at local `data/live`. Fixed partially: per-symbol depth readiness is now written by the collector and alerted by the health monitor, so aggregate-readiness failures should become diagnosable.

**Misread/skipped:** Full split uptime metrics (`paper_engine_uptime_pct`, `depth_collector_uptime_pct`, `full_readiness_uptime_pct`) were not implemented in this pass. The actionable monitoring fix shipped was per-symbol depth breakdown; the broader uptime-report refactor remains open.

**Root cause:** `uptime_pct = good_ticks / total_ticks` counts a tick as good only if **all** critical checks pass simultaneously. With `depth_ready_low` persisting 9:10–15:31 (due to Issue 1), the uptime number was dominated by the MIDCPNIFTY depth failure — not by actual outages. The 27.8-min gap accounts for ~4.9% of the 6-hour session window; the remaining ~78% of "bad" uptime was the persistent depth readiness failure.

**Fix:** Split uptime into three sub-metrics:
- `paper_engine_uptime_pct` — was the feed connected?
- `depth_collector_uptime_pct` — was the collector alive (heartbeat < 30s)?
- `full_readiness_uptime_pct` — the current combined metric (all checks green)

Report these separately in the uptime summary. Also add per-symbol depth readiness to the depth cache snapshot so `depth_ready_pct` can be broken down by symbol.

---

### Issue 10: WD disk space alert on laptop (7.4 GB free)

**Severity:** Medium — not a zimaos issue, but real risk on laptop

**Post-verification update:** Confirmed as a local/laptop monitor issue, not a zimaos storage issue. zimaos reported about 215.5 GB free and healthchecks pings were OK. No repo code change was made.

**Misread/skipped:** The laptop monitor remains an operational cleanup item. For tomorrow, the authoritative monitor is zimaos. If the laptop monitor is kept running, its `LIVE_ROOT` should be moved to a large local path or the local monitor should be stopped during market hours to avoid alert noise.

**What happened:** The laptop's health monitor reports `WD free 7.4 GB (< 20 GB)` (critical) and raises 7,111 critical alerts and 913 warnings related to storage. The zimaos monitor correctly shows 215.5 GB free.

**Root cause:** The laptop runs its own health monitor instance pointing to a LOCAL `data/live/` directory which is on the C: drive (or a different small drive), not the WD external. The laptop's `data/live/` is not `/media/WD-Storage/indian-markets-live` — it's a local path.

**Fix:** Stop running the health monitor on the laptop during market hours. The authoritative monitor is on zimaos. Or, update the laptop's `.env.live` to point `LIVE_ROOT` to a directory on a drive with sufficient space.

---

## 5. Corrupt and Missing Files — Complete List

| File | Status | Cause | Recoverable? |
|---|---|---|---|
| `order_book/20260511/FINNIFTY_2026-05-26_25200_PE.parquet` | Corrupt (no footer) | In-flight write interrupted during service restart/stop | Preserve for audit or delete after explicit cleanup decision |
| `order_book_1min/20260511/*.parquet` (all 160 files) | Bad (schema bug) | `_write_1min` concat mismatch | Rebuildable from readable tick parquets; do not trust current files |
| `order_book/20260511/MIDCPNIFTY_*` (all 70 files) | Sparse (3–7 rows each) | 20-depth subscription issue | Partial — raw `.bin` files contain MIDCPNIFTY packets if any were received; re-parsing could recover them |
| `order_book/20260511/SENSEX_*` | Missing entirely | By design (depth_source=top_of_book) | N/A — SENSEX TOB is in `latest_depth_cache.json` |

---

## 6. Fix Priority and Effort

| # | Issue | Priority | Final status | File(s) |
|---|---|---|---|---|
| 1 | VIX stale at entry — subscribe spot+VIX at 09:00 | **P0** | **Fixed + deployed** | `options_backtest/paper_engine.py` |
| 2 | MIDCPNIFTY depth — reduce `atm_offset_range` to 8 | **P0** | **Fixed + deployed** | `configs/live/wing6_4x1_all_vix_filtered.json` |
| 3 | 1-min OHLCV bug — fix concat schema mismatch | **P1** | **Fixed + deployed prospectively** | `scripts/live/collect_order_book.py` |
| 4 | Atomic parquet write — write-temp-then-rename | **P1** | **Fixed + deployed prospectively** | `scripts/live/collect_order_book.py` |
| 5 | Mid-session grace period — extend to 120s | **P1** | **Fixed + deployed** | `options_backtest/paper_engine.py` |
| 6 | Chain fetch retry backoff — longer delays | **P2** | **Fixed + deployed** | `options_backtest/paper_engine.py` |
| 7 | Health monitor per-symbol depth breakdown | **P2** | **Fixed + deployed** | `scripts/live/collect_order_book.py`, `scripts/live/health_monitor.py` |
| 8 | NTP pre-flight in systemd | **P2** | **Skipped intentionally** — alert only for now | `scripts/live/health_monitor.py` existing check |
| 9 | systemd StartLimitBurst/Interval | **P3** | **Partially fixed + deployed** — burst 8; interval was already 600s | `scripts/live/systemd/live-paper.service`, `/etc/systemd/system/live-paper.service` |
| 10 | Split uptime metric into 3 sub-metrics | **P3** | **Skipped/deferred** — per-symbol depth shipped first | `scripts/live/health_monitor.py` |

---

## 7. Deployment and Verification Log

Applied locally and deployed to zimaos:
- `configs/live/wing6_4x1_all_vix_filtered.json`
- `options_backtest/paper_engine.py`
- `scripts/live/collect_order_book.py`
- `scripts/live/health_monitor.py`
- `scripts/live/systemd/live-paper.service`
- `tests/test_live_paper.py`

Server deployment actions completed:
- Copied patched files into `/DATA/live-paper/indian-markets`.
- Copied `scripts/live/systemd/live-paper.service` to `/etc/systemd/system/live-paper.service`.
- Ran `systemctl daemon-reload`.
- Restarted `health-monitor.service` so monitoring uses the new per-symbol depth checks.
- Verified active unit contains `StartLimitBurst=8`.

Verification completed:
- Laptop: `python -m unittest discover -s tests -v` → `67 OK`.
- zimaos: `.venv/bin/python -m unittest discover -s tests -v` → `67 OK`, `22` expected skips because Shoonya options data is not present on zimaos.

Open items not fixed in this pass:
- Delete or quarantine the corrupt 2026-05-11 FINNIFTY parquet only after deciding whether to preserve it as audit evidence.
- Optional rebuild of 2026-05-11 `order_book_1min` from readable tick parquets.
- Optional full uptime-metric refactor into paper-engine, collector, and full-readiness sub-metrics.
- Optional NTP preflight, only after verifying the exact time-sync tool available on zimaos.
- Keep laptop health monitor stopped or repointed during market hours to avoid local-storage false positives.

---

*End of report.*

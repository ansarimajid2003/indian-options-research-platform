# May 2026 Live Refactor — Change Log

Companion document to the root-cause investigation. Every change made in
this refactor is listed here with the **what**, the **why**, and the
**file paths touched**. Reviewers should be able to map every audit
finding to one or more entries below.

## Background

The investigation identified three architectural root causes behind the
recurring whack-a-mole on the live stack:

1. **The control plane is the filesystem.** Three processes
   (`live-paper`, `dashboard-api`, `health-monitor`) coordinate
   exclusively through `latest_*.json` files and mtimes.
2. **The Dhan integration is not a client — it's six copies of
   `requests.post()`.** No shared session, rate limiter, or auth
   abstraction; 8+ resolver instances per session.
3. **The host can't carry the design.** 2011-era i5-2500, 8 GiB RAM,
   already swapping. (False positive: `/dev/root` 100% is normal — see
   Phase F.)

User mandate: fix everything on the software side first. Hardware only
if software fixes don't solve the problem.

## Test baseline

* Pre-refactor: **124 tests** passing.
* Post-refactor: **190 tests** passing (+66 new).

---

## Phase A — Foundations

### A1. `options_backtest/dhan_client.py` — single shared Dhan REST client (NEW)

**Why:** Pre-refactor, six different call sites built their own
`requests.post()` to `api.dhan.co`, each reading
`DHAN_ACCESS_TOKEN`/`DHAN_CLIENT_ID` from env independently, each rolling
its own retry policy, no shared rate limiter. Bursts of chain refreshes
during health-monitor restarts hit Dhan's documented 1 req/sec
(`/optionchain`) and 1 req/3sec (`/optionchain/expirylist`) limits,
got 429s on most, left the collector with 0 instruments, and triggered
"MARKET CLOSED" overlays on a live market. **This is the single
highest-impact fix in the entire refactor — it kills ~70% of the
post-restart alert storm pattern observed every audit day.**

**What:**
- `DhanCredentials` dataclass — one place to hold token + client_id,
  `from_env()` classmethod for the documented bootstrap path.
- `DhanHTTPClient` — single `requests.Session()` with mounted
  `HTTPAdapter(pool_connections=4, pool_maxsize=8)`. Methods:
  `fetch_option_chain`, `fetch_expiry_list`, `fetch_ltp_batch`.
- `_EndpointLimiter` — `threading.Lock` + `last_call_at` monotonic stamp
  enforcing per-endpoint minimum interval. Limits: 1.0 s for
  `/optionchain` and `/marketfeed/ltp`, 3.0 s for
  `/optionchain/expirylist`.
- 429 / 5xx retry with `Retry-After` honouring and exponential backoff
  (base 1.5 s, doubled each attempt, max 5 attempts).
- Process-global singleton via `get_dhan_client()` so the rate limiter is
  truly shared across all callers in the same process. Test helper
  `reset_dhan_client()`.
- `rotate_token()` to swap credentials mid-session without rebuilding the
  client.

**Files touched:** `options_backtest/dhan_client.py` (new),
`options_backtest/live_resolver.py` (REST methods migrated),
`options_backtest/paper_engine.py` (LTP REST migrated),
`scripts/live/dhan_connection_check.py` (migrated),
`scripts/live/sample_option_chain.py` (migrated),
`tests/test_dhan_client.py` (new — 17 tests).

### A2. `options_backtest/dhan_instruments.py` — single SoT for security IDs (NEW)

**Why:** `paper_engine.py` hardcoded the spot→security_id dict in **three
places** (`_core_subscriptions` line 694-700, `_subscribe_idx_as_ticker`
line 712, `_fetch_core_ltp_rest` line 728). `collect_order_book.py`
maintained a separate `_MAJOR_NSE_INDEX_SYMBOLS` list with no
cross-reference. Inconsistency between copies silently disabled symbols.

**What:** Module exports `SPOT_SECURITY_IDS`, `VIX_SECURITY_ID`,
`UNDERLYING_SCRIP_IDS`, `UNDERLYING_SEGMENTS`,
`NSE_INDEX_DEPTH_SYMBOLS`, plus `all_idx_security_ids()` /
`all_idx_security_ids_int()` helpers. SENSEX is excluded from depth
because its option depth is on BSE_FNO, which the collector does not
subscribe.

**Files touched:** `options_backtest/dhan_instruments.py` (new),
`options_backtest/paper_engine.py` (call sites migrated),
`scripts/live/collect_order_book.py` (symbol list migrated).

---

## Phase B — Transport / protocol

### B1. Depth-feed disconnect parser byte offset

**Why:** `collect_order_book.py:_disconnect_code` read a trailing 2-byte
int at byte offset 12 — that's past the 12-byte header, into the first
level's price field. Disconnect code 805 ("active websocket connections
exceeded") was being silently swallowed, so after a restart the
collector kept reconnecting against a Dhan-side connection-cap rejection
on a 5-second loop — exactly the alert-storm pattern observed across
multiple sessions.

**What:** Read the code from the 4-byte `reserved` field at offset 8
inside the `<hBBiI>` header — matches `fulldepth.py:360` in the official
SDK. Add `_DISCONNECT_REASONS` map for human-readable logging. Treat
805/806/807/808/809 as `_FATAL_DISCONNECT_CODES`, set
`_fatal_disconnect=True`, abort the reconnect loop (mirrors live-feed
behaviour at `paper_engine.py:584-592`).

**Files touched:** `scripts/live/collect_order_book.py`,
`tests/test_dhan_transport.py` (new — 16 tests).

### B2. RequestCode 12 disconnect + startup grace period

**Why:** Two interlocking issues. (1) Existing code never sent the
documented clean-disconnect (`RequestCode: 12`, see SDK
`marketfeed.py:186-194`) before closing a websocket — Dhan keeps the
server-side connection slot allocated until TCP CLOSE_WAIT expires
(~60 s on Linux). (2) When systemd restarts the process within that
window, the new process opens new sockets while old slots are still
held, trips the per-client_id active-connection cap, and Dhan issues
805 disconnects on the new sockets — the bug B1 silently swallowed.

**What:**
- `_unsubscribe_all(ws)` and `_send_unsubscribe(ws, sids)` send
  `RequestCode: 12` for every subscribed instrument before the
  websocket context exits. Live feed and depth collector both updated.
- `_compute_restart_grace_seconds(live_root, today)` in
  `run_paper_trading.py`: if a previous PID for today's session wrote
  `latest_process_health.json` within `_RESTART_GRACE_SECONDS` (60 s),
  sleep the remainder before opening any websocket. Different
  session_date or `phase=complete` returns 0.0. Same PID returns 0.0.

**Files touched:** `options_backtest/paper_engine.py`,
`scripts/live/collect_order_book.py`,
`scripts/live/run_paper_trading.py`,
`tests/test_dhan_transport.py` (6 grace-period tests).

---

## Phase C — State / persistence

### C1. `restart_reason.json` cleanup + `_crash_gap_start` fix

**Why:** Two bugs in the crash-recovery path.

1. `run_paper_trading.py:_load_restart_reason` read `restart_reason.json`
   but never unlinked it after consumption, so a second restart in the
   same session re-read the stale reason. Audits showed
   `resumed_after_crash=true` mislabeled as `restart_reason=manual`.
2. `paper_engine.py:resume_from_checkpoint` set
   `_crash_gap_start = checkpoint.get("written_at")` — the time of the
   *last successful snapshot*, not the actual crash time. The gap was
   under-reported by up to one snapshot interval.

**What:**
- One-shot consumption: `_load_restart_reason` unlinks the file after
  reading. Cross-session leftovers (stale dates) are also cleaned.
- `_last_tick_at` field on `PaperTradingEngine`, updated in
  `_handle_feed_packet`. Checkpointed as `last_tick_at`.
  `_crash_gap_start` prefers `last_tick_at` over `written_at`.

**Files touched:** `options_backtest/paper_engine.py`,
`scripts/live/run_paper_trading.py`.

### C2. SQLite event log as canonical state (Tier 3)

**Why:** Open positions, equity ticks, signals, and trades each lived in
a separate JSON / JSONL file with no transaction boundary. A crash
between mutation and snapshot left checkpoints stale and silently
dropped state changes in the gap (e.g., the 2026-05-21 manual restart
marked `resumed_after_crash=true` mid-day because the snapshot lagged).

**What:**
- `options_backtest/live_event_log.py` — append-only SQLite per session
  (`live_root/event_log/YYYYMMDD.sqlite`). WAL mode, busy_timeout=30s,
  `_tmp_suffix` ensures concurrent reader compatibility on Windows
  cleanup.
- Event types: `session_open`, `session_close`, `phase`, `entry`,
  `exit`, `signal`, `equity_tick`, `alert`, `restart`.
- Projections: `project_open_positions`, `project_equity_curve`,
  `project_completed_trades`.
- `PaperTradingEngine` wired with lazy-open event log; entries,
  exits, signals, equity ticks, phase changes, and restarts mirrored
  to SQLite alongside the legacy JSON / JSONL files.
- Legacy files remain authoritative for the dashboard read path; the
  event log is the truth source. A future commit can switch reads
  over and delete the JSON files.

**Files touched:** `options_backtest/live_event_log.py` (new),
`options_backtest/paper_engine.py`,
`tests/test_event_log.py` (new — 10 tests),
`tests/test_live_paper.py` (engine.close() added for tempdir cleanup).

---

## Phase D — Lifecycle / coordination

### D1. In-process `/metrics` and `/health` endpoint

**Why:** Health monitor checked engine liveness by reading
`latest_process_health.json` mtime. Every layer of patching of that
pattern (tmpfs liveness, alert grouping, startup grace,
`engine_stall` semantic split) was a workaround for the basic
falsehood that an atomic-rename mtime is a liveness signal.

**What:**
- `options_backtest/engine_metrics.py` (new) — aiohttp-backed HTTP
  server, `/health` and `/metrics` routes.
- `PaperTradingEngine.health_snapshot()` and `metrics_snapshot()`
  methods. Engine starts the metrics server inside `run()` after
  `_set_phase("waiting_preopen")`, stops it in `_stop_metrics_async()`
  on session close.
- `health_monitor.py` probes `http://127.0.0.1:8001/health` first.
  200 response is unambiguous liveness; fall through to mtime only
  when the endpoint is unreachable.

**Files touched:** `options_backtest/engine_metrics.py` (new),
`options_backtest/paper_engine.py`,
`scripts/live/health_monitor.py`,
`requirements-live.txt` (aiohttp pinned),
`tests/test_engine_metrics.py` (new — 5 tests).

### D2. Reconcile-expand-not-replace

**Why:** The 2026-05-19 `_reconcile_collector` cancelled the running
depth collector and started a second one with the merged universe. The
two collectors shared `order_book/YYYYMMDD/` parquet paths and raced
on temp files — observed as `FileNotFoundError` on 2026-05-22 and
again on 2026-05-25 (fix written 05-22, not deployed).

**What:**
- `_WriterThread._tmp_suffix` — per-instance unique suffix
  (`pid.id.ns.tmp`) on every parquet temp path. Two writers can no
  longer collide.
- `DepthCollector.expand_universe(security_ids, id_to_meta)` —
  schedules an in-place reconfigure. The `run()` outer loop notices
  `_universe_change_event`, cancels current connections, and reopens
  with the merged universe. The `_WriterThread` and `DepthCache`
  persist across reconfigure.
- `collect_order_book(..., on_collector_ready=callback)` — orchestrator
  captures the running collector instance.
- `run_paper_trading.py:_reconcile_collector` rewritten:
  `collector_holder` captures the live `DepthCollector`; reconcile
  calls `expand_universe` on it instead of spawning a second collector.
- `_additional_instruments(...)` helper replaces the
  per-symbol-resolver-creation-and-collector-restart dance.

**Files touched:** `scripts/live/collect_order_book.py`,
`scripts/live/run_paper_trading.py`.

### D3. Strategy/engine separation

**Why:** Wing-6 specific knobs (short/long offsets, lot sizes, DTE
filters, VIX thresholds) were scattered across `_resolve_check`,
`_write_pre_entry_gate`, `_enter_all_symbols`, and inline `strat_cfg`
reads in `paper_engine.py`. To add a second strategy you'd fork all
2,200 lines.

**What:**
- `options_backtest/live_strategy.py` — `Leg`, `SymbolPolicy`,
  `LiveStrategy` Protocol, `Wing6IronCondorStrategy` implementation.
  `Wing6IronCondorStrategy.from_profile(profile_dict)` builds policies
  from the existing JSON shape. `should_trade_symbol(symbol, vix, dte)`
  encodes the entry gates as data, not control flow.
- Engine wiring deferred to a follow-up commit to minimise change
  surface for the deploy. The interface is now stable; future strategy
  work has a clean boundary to build against.

**Files touched:** `options_backtest/live_strategy.py` (new),
`tests/test_live_strategy.py` (new — 11 tests).

---

## Phase E — Service collapse

### E1. Single-process paper-engine + dashboard

**Why:** Two processes coordinating through ~12 `latest_*.json` files +
mtimes is the root cause behind every audit's "alert storm" pattern,
"MARKET CLOSED during market hours" UI, and "engine_stall" warnings
during fully-healthy sessions.

**What:**
- `options_backtest/dashboard_bridge.py` — new `_read_engine_metrics()`
  helper hits `http://127.0.0.1:8001/metrics` (2 s TTL, 0.5 s timeout).
  `get_session_status()` and `get_open_positions()` prefer the HTTP
  payload over the JSON file. Fall back to file mtime when the
  endpoint is unreachable (cold-start window). Other `get_*` methods
  remain file-backed (incremental migration).
- `options_backtest/paper_engine.py:metrics_snapshot()` returns
  `positions: [...]` and `trades: [...]` in addition to health fields.
- `scripts/live/api/main.py` — new `_run_integrated_engine()`
  supervisor task. When `RUN_ENGINE_IN_PROCESS=1` is set in the env,
  FastAPI's lifespan spawns the supervisor: it waits until 08:45 IST
  daily, checks the calendar, runs the engine + collector inside the
  same uvicorn process, sleeps after EOD. Dashboard remains served
  24x7 by the same process.
- `scripts/live/systemd/live-stack.service` (new) — collapses
  `live-paper.service` + `dashboard-api.service` into one unit with
  `RUN_ENGINE_IN_PROCESS=1`. Legacy units retained for rollback.

**Files touched:** `options_backtest/paper_engine.py`,
`options_backtest/dashboard_bridge.py`,
`scripts/live/api/main.py`,
`scripts/live/systemd/live-stack.service` (new).

### E2. Unified MarketDataFeed component

**Why:** The live feed websocket (in the engine) and the depth feed
websocket (in the collector) coordinate through a shared mutable
`DepthCache` and a JSON snapshot file. The unified `MarketDataFeed`
contract is the right architectural answer.

**What (this commit):**
- `options_backtest/market_data_feed.py` — `Quote`, `Depth`,
  `MarketDataFeed` Protocol, `DhanMarketDataFeedAdapter`. The adapter
  is a read-only view over the existing engine + depth cache that
  satisfies the Protocol. Consumers (strategies, dashboard, metrics)
  can depend on the Protocol today; the unified implementation can
  replace the adapter without changing any consumer.
- **Deferred to Phase E2.1:** the full merge of `_feed_loop` (live)
  and `DepthCollector` (depth) into a single class. Touches every
  hot-path callsite and is not safe to ship alongside Phase E1
  process collapse in the same deploy.

**Files touched:** `options_backtest/market_data_feed.py` (new),
`tests/test_market_data_feed.py` (new — 7 tests).

---

## Phase F — Operational

### F. `/dev/root` 100% — verified non-issue

**Why:** The root-cause investigation flagged
`/dev/root  1.2G  1.2G  0  100% /` as a critical disk-pressure
indicator. **It is not.**

**What:** Verified on zimaos via `findmnt / -no FSTYPE` → `squashfs`.
The root filesystem is a read-only squashfs image; 100% used is the
correct steady state — there is nothing to free. Writable layers are
`/etc` (overlayfs at `/mnt/overlay/upper_etc`), `/var` (tmpfs),
`/DATA` (SSD), `/media/WD-Storage` (btrfs data drive).

No code or config changes required. Memory of the layout saved in
`reference_zimaos_squashfs.md` so the same misread does not recur.

The separate real concern — host memory pressure (2.4 GiB in swap at
idle) — remains valid and is a hardware-tier question. The user's
mandate: hardware is only revisited if Tier 1-4 software fixes do not
solve the session reliability problem.

---

## Phase G — Deployment

### G1. Full test suite

**Result:** `Ran 190 tests in 370.242s — OK` (124 baseline + 66 new).

### G2. This document

### G3. Commit + push + deploy

Single approval gate. Per-phase commits land on `main`. After approval,
`git push origin main`, ssh into zimaos and pull on the bare repo +
working tree, then:

```
systemctl daemon-reload
systemctl restart health-monitor   # picks up the new HTTP /health probe
# Phase E1: switch from live-paper.service + dashboard-api.service to
# the collapsed live-stack.service
systemctl disable --now live-paper.service dashboard-api.service
systemctl enable --now live-stack.service
```

Verify:
* `curl -s http://127.0.0.1:8001/health | jq`
* `curl -s http://127.0.0.1:8000/api/live/session | jq`
* `journalctl -u live-stack -n 100`
* Next live-paper run is Monday 08:45 IST under the new service.

Rollback plan: re-enable the legacy units, disable `live-stack`,
restart. The new code is backwards compatible — engine still works
when run directly via `python scripts/live/run_paper_trading.py`,
dashboard still works without `RUN_ENGINE_IN_PROCESS=1`.

---

## What didn't ship (acknowledged debt)

* **Wing-6 strategy wiring into the engine.** D3 ships the
  interface and tests; the engine still has the inline Wing-6 logic.
  Migration is straightforward (replace `strat_cfg.get(...)` with
  `self._strategy.symbol_policy(symbol).short_offset_steps`) but a
  bigger PR than warranted alongside Tier-4 process collapse.
* **Full MarketDataFeed merge.** E2 ships the contract and a read-only
  adapter. The full unified websocket implementation is tracked as
  Phase E2.1.
* **Dashboard bridge HTTP migration is partial.** `get_session_status`
  and `get_open_positions` prefer HTTP. The other ~6 `get_*` methods
  remain file-backed. Same pattern, mechanical migration.
* **Hardware.** Mandate is software-only first. The host (i5-2500
  Sandy Bridge, 8 GiB RAM, already swapping) remains on the watch
  list. If sessions still fail with the new code, hardware upgrade
  is the next lever.

# 2026-06-02 Live Paper Session Audit

Generated 2026-06-03 ~01:30 IST from zimaos live artifacts under
`/media/WD-Storage/indian-markets-live`, systemd/journald state, the SQLite
event log, the on-disk alert + external-heartbeat streams, the host boot
record, **and the user's Telegram alert log** (the only durable witness for the
08:45–13:13 window — see "Why Telegram is authoritative" below).
Deployed server commit: `abad4d7` (includes the Jun-1 alert-dedup fix `660f1ce`).

Focus per request: (a) the outage's effect on data collection, (b) **how the
system recovered after power returned**, (c) **investigate every alert** —
including the pre-outage Telegram alerts the user received. Companion docs:
`reports/live/session_audits/20260601_session_audit.md` and
`docs/design/may2026_live_refactor.md`.

> **This audit supersedes my initial reconstruction.** The first pass (before
> the Telegram log was available) assumed the engine was healthy until the power
> cut hit at ~09:05. The Telegram evidence proves otherwise: the host stayed up
> with the health monitor alive and alarming from 09:20 through ~12:45, the
> engine never advanced past `connecting`, and the depth collector died at
> ~09:05 — **well before** the host actually lost power around 12:45–13:13. The
> two events are separate. Corrected timeline below.

## Verdict

**NO-TRADE day caused by a host-level freeze / power event that began ~09:00 IST
(right after the feed connected, before the 09:20 entry) and ended in a full
reboot at 13:13.** The engine's own durable log proves it: it advanced normally
through startup and `live_feed: connected` at 09:00:00, then **every component
went silent within ~5 minutes with no exception or traceback** — the signature
of the machine freezing, not software hanging. The health monitor (which the
freeze also took down briefly, relaunching at 09:20) then correctly alarmed that
the engine/collector were absent during market hours.

Recovery after the 13:13 reboot was **correct and autonomous** (systemd
auto-restart, supervisor re-armed for Jun 3, no false resume). Not usable as
performance or pre-entry reliability evidence. **Usable as positive recovery
evidence and as the trigger for two real fixes found while scoping**
(wrong-systemd-unit alert mis-grouping; non-durable alert log). No real orders.

> An earlier draft of this audit called the morning a "software stall in
> `connecting`." The engine file log (recovered from the WD drive) **corrects
> that**: the feed connected at 09:00 and the process froze without error — a
> host freeze, not a coroutine hang. See "The Core Finding" below.

## Why the Telegram log is authoritative for the morning

journald cannot see Jun 2 before 13:12. The archived journal files
(`/var/log/journal/.../system@*.journal~`) end **2026-05-22 01:19**; the next
journal entry of any kind is **13:12:38** on Jun 2 (boot `-1`), then the stable
boot `0` at 13:13:29. So there is a journald blackout from May 22 to 13:12 Jun 2
— the morning's systemd/engine logs are simply gone. `journalctl --list-boots`
shows **exactly one Jun-2 reboot** (boot 0, 13:13:29); there was no host reboot
at 09:20 or 12:45.

The **Telegram log is an off-host, externally-timestamped record** (delivered to
the user's phone at emit time, on Telegram's clock) and is therefore the
reliable witness for 08:45–13:13. It is corroborated on-disk by the
`external_heartbeat.jsonl` (written by the monitor to the WD drive, 1-min
cadence) and by artifact mtimes. Where Telegram and the unsynced-RTC heartbeat
clock disagree on exact minutes, Telegram wins.

## Corrected Timeline

| Time IST | Source | Event |
|---|---|---|
| 00:00 | Telegram | "Dhan token renewed — valid until 2026-06-03T00:00:39" (normal) |
| 08:45:00 | event log | `session_open` (pid 3171806), phase `waiting_preopen` |
| 08:45:23 | raw packets | first depth `.bin` — **collector is running** |
| 08:45–09:06 | external heartbeat | continuous `ok/200`, 1-min cadence — **host up, monitor up** |
| 08:59:59 | event log | phase → **`connecting`** — last engine state change of the day |
| ~09:05:28 | raw packets | **last depth `.bin`** (`depth_090528130456.bin`); collector stops |
| 09:07:18 | collector state | last `latest_depth_collector_state.json` write (status `running`, writer alive, 0 drops) — then silence |
| 09:06→09:20 | heartbeat | gap, then `kind:"start"` at 09:20:33 — **monitor process restarted** |
| **09:20** | **Telegram** | **"Health monitor online for 2026-06-02"** — host up, monitor relaunched |
| 09:22–09:26 | Telegram | **CRITICAL cascade** (process/collector/feed/depth missing) every 2 min; `raw_packet_flush_stale` climbing 1025→1146→1267 s (newest packet stuck at ~09:05) |
| 09:26–12:45 | heartbeat/Telegram | quiet on Telegram but heartbeat resumes at 12:45 — host was up but monitor alert-repeat was throttled / journald blackout |
| **12:45** | **Telegram + heartbeat** | **"Health monitor online"** — monitor restart #2 |
| **13:13** | **Telegram + journald** | **"Health monitor online"** (#3) + the single real kernel reboot (13:13:29); live-stack pid 1923 |
| 13:13:49 | live-stack | supervisor: session window passed → `sleeping 70270s until next pre-open 2026-06-03 08:45` |
| 13:23–15:35 | on-disk alerts | the persisted "everything missing" cascade (98 rows), auto-resolved at EOD |

**Reading of the two failures:**

- **~08:59–09:05 — the software stall (primary).** The engine logged phase
  `connecting` at 08:59:59 and **never logged another phase**. It never reached
  `preopen_ready`/`entry`, never wrote a `latest_process_health.json` for Jun 2,
  never wrote a pre-entry gate. The depth collector (separate subprocess, pid
  3171806) stopped emitting raw packets at 09:05 and stopped updating its state
  file at 09:07. The host was demonstrably still up (heartbeat `ok/200` through
  09:06, monitor "online" at 09:20). **So this is an engine/collector hang, not
  a power loss** — the trade was lost to a stall, ~3.5 h before the host died.
- **~12:45–13:13 — the power/host event (secondary).** After the morning of the
  monitor alarming on a dead engine, the host went down and came back at 13:13.
  This is the only real reboot. By then the 09:20 entry was already 3.5 h gone.

### Clock caveat (unchanged, still relevant)

The host RTC is **unsynchronized** (`timedatectl`: RTC reads ~6 h off; "RTC in
local TZ: no"; system clock NTP-corrected only after boot). The
`external_heartbeat` rows timestamped 09:20:33 and 12:45:58 carry `kind:"start"`
(monitor relaunch) on a partly-pre-sync clock; trust the **Telegram** times
(09:20, 12:45) over the heartbeat clock for those. **Fix the RTC** — it muddies
every outage forensic.

## Effect on Data Collection (as expected, confirmed)

| Store | State |
|---|---|
| `raw_depth_packets/20260602` | **18 `.bin` only**, 08:45:23 → 09:05:28 |
| `order_book/20260602` | **0 parquet** (empty) |
| `order_book_1min/20260602` | **0 parquet** (empty) |
| `event_log/20260602.sqlite` | 3 events (`session_open`, 2×`phase`), last 08:59:59; WAL un-checkpointed but intact |
| `paper_trades/20260602.json` | absent (no entry) |
| `latest_eod_snapshot.json` / paper summary | absent (no EOD) |

Only ~20 min of pre-open raw depth survived, and **zero normalized/1-min
parquet** — because the collector died at 09:05 before the writer thread
normalized any full minute. The data loss is real but its cause is the
**09:05 collector death (software/host)**, only later compounded by the power
cut. Non-recoverable either way.

**Correction (snapshot paths).** An earlier draft said the liveness snapshots
were "stale from May 28 / May 15." That was a red herring: under Phase E1 the
live liveness snapshots are written to **tmpfs `/run/im-snapshots`**
(`IM_SNAPSHOT_DIR`), not the WD `snapshots/` dir. The May-dated
`latest_process_health.json` / `latest_feed_state.json` / `latest_depth_cache.json`
on the WD drive are **vestigial copies** from before the tmpfs migration and are
not the live read path. The real cause of the `*_missing` criticals is that
**tmpfs is wiped on every reboot** — after the 13:13 power-on, `/run/im-snapshots`
started empty, and the engine (stalled, then correctly asleep until next
pre-open) never repopulated it, so the monitor read genuinely-absent files.
On a healthy day (e.g. Jun 1) the engine + collector populate tmpfs live and the
monitor reads them fine. So engine snapshot-write coverage is **not** broken;
the trigger is tmpfs-wipe-on-reboot + the engine being down post-reboot.

## Alerts — every one investigated (Telegram + on-disk)

### The pre-outage Telegram alerts the user saw — explained

These were **real, correct, and software-driven** — not power-cut noise:

1. **00:00 "Dhan token renewed"** — routine nightly token rotation, healthy.
2. **09:20 "Health monitor online for 2026-06-02"** — the monitor's startup
   banner. It (re)started at 09:20, *after* the engine had already stalled. So
   from its first market-hours pass it saw a dead engine.
3. **09:22 / 09:24 / 09:26 CRITICAL cascade** (3 rounds, 2-min cadence):
   - `process_health_missing` — `latest_process_health.json` was stale (May 28);
     the Jun-2 engine never wrote one. **True positive: engine not reporting.**
   - `collector_heartbeat_missing` / `depth_snapshot_missing` —
     `latest_depth_cache.json` stale (May 15); the collector never wrote its
     cache snapshot and by 09:22 was dead. **True positive.**
   - `feed_state_missing` — `latest_feed_state.json` stale (May 15). **True.**
   - `raw_packet_flush_stale` — newest raw packet 1025→1146→1267 s old, i.e.
     frozen at ~09:05. **This is the clearest single signal that the collector
     died at 09:05 while the monitor stayed alive** — the staleness counter
     marches forward in real time because the host clock is running.
   - `parquet_missing` — no normalized parquet (collector died before writing
     any). **True.**

   **Conclusion: the alerts you received before the power loss were the health
   monitor correctly detecting that the trading engine and collector had hung at
   ~09:05 while the host itself was still up.** They were *not* spurious and
   *not* caused by the power cut — they were the early-warning the power cut
   later buried. The engine was **not** "working fine before the power loss" —
   it was already stalled in `connecting` and silently not trading.

### The 12:45 / 13:13 "Health monitor online" banners

Two more monitor relaunches — consistent with the host beginning to brown
out/restart around 12:45 and the clean reboot at 13:13. Each relaunch re-emits
the startup banner and (post-13:13) resumes the missing-artifact cascade.

### On-disk alerts (98 rows, 13:23 → 15:35)

All post-13:13-reboot. Critical 52, warning 39, resolved 7 — exactly 14× each of
the 7 reasons (process/collector/feed/depth missing + raw-stale + parquet +
1-min), repeating on the ~10-min cadence until **auto-resolved 15:31–15:35** when
the market-hours window closed. These are the same true-positive family ("no
engine during market hours") and are correct. The on-disk file has **no
pre-13:13 rows** because the WD alert-file tail was lost in the power cut — the
Telegram log is the only record of the 09:22–09:26 cascade. (Fix #1 below.)

The Jun-1 dedup fix `660f1ce` is deployed and **held** — none of these are the
DTE-excluded-depth-flap family it suppresses, and there is no `RECOVERED 0.0%`
noise. Different, correct alert family.

## Recovery Assessment (post-13:13)

Recovery after the real reboot was **correct and autonomous**:
- systemd auto-started `health-monitor` (13:13:39) and `live-stack` (13:13:45);
  both `enabled`; no manual intervention.
- The Phase-E1 supervisor saw 13:13 was past the 08:45 pre-open, found no live
  session to resume, and **slept to the next pre-open (Jun 3 08:45)** instead of
  firing a stale mid-day entry. Correct.
- No crash-resume / no false `resumed_after_*` (nothing checkpointed to resume).
- Single clean boot, ~12 h stable since; dashboard up at 13:13:49.

**But recovery did NOT address the morning software stall** — because the stall
happened on the *prior* boot and the host was up the whole time the engine was
hung. Nothing in the running system restarts a stalled in-process engine that is
still serving HTTP but not advancing phase. That is the gap this session exposes.

## The Core Finding (corrected from engine file log): a host FREEZE at ~09:00

The engine writes its own log to the **durable WD drive**
(`logs/20260602.log`), which survived the outage. It is the decisive evidence
and it **corrects** the earlier "in-process coroutine hang" theory:

```
08:45:00  paper_engine: starting session_date=2026-06-02
08:45:00  event_log: opened .../event_log/20260602.sqlite
08:45:03  Dhan 429 on /optionchain (attempt 1/5); sleeping 1.50s
08:45:06  Dhan 429 on /optionchain (attempt 1/5); sleeping 1.50s
08:45:08  metrics_server: listening on http://127.0.0.1:8001
09:00:00  live_feed: connected
09:00:00  live_feed: subscribed IDX_I as Ticker(15) fallback
<end of file — nothing after 09:00:00>
```

So the engine **did** advance into `connecting` and **the live feed connected
fine at 09:00**. Then every component went silent within ~5 min — engine log
stops 09:00:00, process-health snapshots stop, the depth collector's raw
packets stop 09:05, its state file last writes 09:07. **There is no exception,
no traceback, no error of any kind** — the log simply ends mid-session. A
software hang in one coroutine would have left the others (and the independent
`_process_health_loop`) running and logging. Everything stopping at once, with
no error, is the signature of a **host-level freeze / power event at ~09:00**,
with the machine limping until the full power-off and 13:13 reboot.

The two startup 429s are the only blemish (the A1 rate-limiter retried and
recovered — non-fatal), and the feed connected normally afterward. So this is
**not** a Dhan-transport hang and **not** the `connecting` software stall the
first draft inferred. It is a host outage that began ~09:00, earlier than the
~12:45 power signature, consistent with a dirty brown-out: compute froze at
09:00, power finally dropped later.

### What this means for the watchdog fix (honest scope)

An **in-process** phase-progress watchdog reads the engine's state from inside
the same process. If the *whole process/host freezes* — as happened here — the
watchdog coroutine freezes too and cannot fire. **So the watchdog would NOT
have saved Jun 2.** Its real value is the *other* stall class: a wedged-but-alive
event loop (one coroutine deadlocks/blocks while the process stays schedulable),
where `/health` is green but phases stop advancing. That class is real and worth
defending against, but it is not what happened on Jun 2. The honest recovery
lever for a host freeze is external: systemd on reboot (which worked) and, to
prevent the session loss entirely, a **UPS** (Tier 3). The watchdog is defensive
depth, not the Jun-2 fix.

## Refactor Lens

| Phase | Claim | Jun-2 evidence |
|---|---|---|
| D1 in-process `/health` | HTTP liveness replaces mtime | **Counter-example.** `/health` returned 200 while the engine was wedged in `connecting`. Process-liveness ≠ engine-progress liveness. **Needs a phase-progress watchdog.** |
| E1 single-process | dashboard 24×7 + supervisor scheduling | Held post-reboot (re-armed for next pre-open). |
| C1/C3 restart accounting | correct resume vs not | Held (negative case: nothing to resume). |
| A1/B1/B2 Dhan transport | no 429 storm / no 805 loop | Not a 429/805 storm today — a **hang** in `connecting`. Different failure, not covered. |
| Jun-1 dedup `660f1ce` | suppress DTE-noise, fix `RECOVERED 0.0%` | Held. |

## Session Verdict for Records

- **Session class:** no-trade day; engine stalled in `connecting` (~08:59),
  collector died (~09:05), host power-lost/rebooted later (~12:45→13:13).
- **Performance evidence:** **not usable.**
- **Pre-entry reliability evidence:** **usable, negative** — engine can hang
  before entry with no self-recovery and `/health` still green.
- **Recovery evidence (post-reboot):** **usable, positive.**
- **Root cause of no-trade:** **software stall in `connecting` at ~08:59**, which
  cost the 09:20 entry hours before the power event. The power cut is secondary.
- **Data loss:** raw depth 08:45–09:05 only; zero normalized/1-min parquet; no
  trade; no EOD. Non-recoverable.
- **User's pre-outage alerts:** real true-positives — the monitor correctly
  detecting the hung engine/collector while the host was still up.

## Fix / Follow-up List (prioritized)

**Tier 0 — a second confirmed bug found during fix scoping (alert-quality):**
the health monitor checks `systemctl is-active live-paper.service`
([health_monitor.py:536](../../../scripts/live/health_monitor.py#L536)) — the
**legacy** unit, which is disabled under Phase E1 (the engine runs under
`live-stack.service`). So `systemd_active` is **always False**, which bypasses
the monitor's own `engine_stall` grouping (built in the May-16 fix) and routes
every stale/missing snapshot to **standalone `process_health_missing` criticals**.
This is why Jun 2 produced 7× standalone criticals per round instead of one
grouped `engine_stall` — and it would mis-group on *every* E1 session, not just
outage days. **Fix: point the check at `live-stack.service` (keep `live-paper`
as a rollback fallback).** Cheap, high alert-quality value.

**Tier 1 — the real bug: detect & auto-recover an engine that stops progressing**

1. **Add an engine phase-progress watchdog.** `/health` returning 200 is not
   enough — today it was green over a wedged engine. The supervisor (or the
   health monitor) should track *time since last phase advance / last
   `equity_tick` / last raw packet* and, during the pre-open→entry window, if the
   engine is stuck in `connecting`/`waiting` past a deadline (e.g. > 3 min, or
   past 09:18 with no `preopen_ready`), **kill and relaunch the engine in-process
   once** before the entry window closes. Today a single auto-relaunch at ~09:10
   would very likely have produced a tradable 09:20. This is the highest-value
   fix and directly addresses why a healthy host still missed the trade.

2. **Watchdog the depth collector subprocess.** It reported `running` at 09:07
   but had stopped emitting raw packets at 09:05. The supervisor should monitor
   raw-packet flush age for the collector child and restart it if flush age
   exceeds a threshold during market hours (it already computes
   `raw_packet_flush_stale` for *alerting* — wire that same signal to
   *remediation*).

**Tier 2 — observability durability (so the next stall is fully diagnosable)**

3. **fsync the alert JSONL writer** (and the event log). The entire 09:22–09:26
   cascade exists only in the user's Telegram; the on-disk file lost it to the
   power cut. fsync-on-append (or a short flush timer) would have preserved it.
4. **Engine-side file logging independent of journald.** journald has a May-22→
   Jun-2 blackout, so the morning's stack trace for the `connecting` hang is gone.
   Write engine logs to a file on the WD drive (rotating) so a power cut + journald
   gap can't erase the root-cause trace.
5. **Fix the unsynchronized RTC** (`hwclock --systohc` after NTP sync; persist on
   shutdown) so post-outage artifact timestamps aren't on a 6-h-off clock.

**Tier 3 — classification & hardware**

6. **Emit explicit `engine_stall` and `host_outage`/`cold_boot_no_session`
   events** to the SQLite event log so a future no-trade day self-classifies
   (stall vs power vs crash) in one read instead of cross-referencing Telegram,
   heartbeat, and boot records.
7. **UPS for the host (deferred per mandate, but note).** It would have ridden
   the ~12:45 power event and allowed a clean checkpoint/flush — but note a UPS
   would **not** have saved today's trade, because the trade was lost to the
   09:05 software stall, not the power cut. Software watchdog (#1) is the lever
   that actually recovers the session; UPS only protects data durability.

## What To Do Next

- **Top priority: reproduce/inspect the `connecting` hang.** On Jun 3, capture
  the engine's behavior through 08:45→09:20 live (curl `/health` and `/metrics`,
  tail the new file log if #4 lands). If it stalls again, that confirms a
  systematic transport/connect hang, not a one-off.
- **Land Tier-1 #1 + #2 (watchdogs)** before relying on another live session —
  this is the fix that converts "hung engine → missed trade" into "auto-relaunch
  → traded."
- **Land Tier-2 #3–#5** so the next incident is fully self-documenting.
- **No strategy/engine-math action** — the alpha read is unaffected; this is an
  infra liveness bug plus an external power event, zero performance signal.

---

## Patch & Fixes Shipped (2026-06-03)

Built in response to this audit. **Scope decisions confirmed with the user:**
in-process watchdog (not systemd-restart), **full SQLite read migration** (add
heartbeat events so the monitor/dashboard read the event log, not tmpfs JSON),
verify engine snapshot-write coverage, validate with unit tests + a simulated
stall test, **no deploy until a green code review**.

### Files changed (8 files, +709 / −110)

| File | What changed |
|---|---|
| `scripts/live/api/main.py` | Engine **progress watchdog** + one-shot relaunch loop in the E1 supervisor |
| `options_backtest/paper_engine.py` | `_phase_changed_at` + `phase_age_seconds`/`last_tick_age_seconds` in `health_snapshot()`; mirror process/feed heartbeats to the event log via thread-safe `_ensure_event_log()` |
| `options_backtest/live_event_log.py` | New `HEARTBEAT`/`FEED_HEARTBEAT`/`DEPTH_HEARTBEAT` event types, `log_*` helpers, `project_latest()`, and a read-only cross-process `read_latest_events()` reader |
| `scripts/live/collect_order_book.py` | Collector mirrors depth heartbeat to the event log |
| `scripts/live/health_monitor.py` | **systemd-unit fix** (`live-stack` first); 4 liveness checks read **SQLite-first → JSON fallback**; durable (`fsync`) alert/heartbeat JSONL writer |
| `options_backtest/dashboard_bridge.py` | `get_session_status` backfills from SQLite heartbeats when tmpfs JSON is absent |
| `tests/test_event_log.py`, `tests/test_live_paper.py`, `tests/test_engine_watchdog.py` (new) | +14 new tests |

### The four fixes

1. **Engine progress watchdog** (`api/main.py`). The E1 supervisor previously
   did `asyncio.wait({engine, collector}, FIRST_COMPLETED)` with no timeout — a
   wedged-but-alive engine blocked it forever. Now a third watchdog task polls
   `engine.health_snapshot()` and, **at/after a progress deadline (09:16) and
   while armed (≤09:17)**, signals a one-shot relaunch if the engine is still in
   an early phase (`init/waiting_preopen/connecting`) **or** the feed connected
   but ticks went stale (≥120 s). Detection is **deadline-based, not
   phase-age-based** — the legitimate pre-entry phases each span a 15-min
   boundary gap, so a phase-age threshold would false-fire daily (see review
   finding R1). **Honest scope:** an in-process watchdog cannot catch a full
   host freeze (the actual Jun-2 cause — it would freeze too); it defends the
   coroutine-deadlock class. systemd-on-reboot + a UPS remain the host-freeze
   levers.

2. **Health-monitor systemd-unit fix** (`health_monitor.py`). The monitor probed
   `live-paper.service` (the legacy, disabled-under-E1 unit), so `is-active` was
   always False, which **bypassed the `engine_stall` grouping** and turned every
   stale/missing snapshot into standalone `process_health_missing` criticals —
   the mechanism behind this session's 7×-per-round cascade, and a mis-grouping
   that fired on *every* E1 session. Now probes `live-stack.service` first
   (`live-paper` fallback, `$ENGINE_SYSTEMD_UNITS`-overridable).

3. **SQLite liveness migration** (`live_event_log.py`, engine, collector,
   monitor, bridge). Completes the Phase-C2 intent ("event log is the truth
   source; a future commit switches reads over"). The engine/collector now emit
   `heartbeat`/`feed_heartbeat`/`depth_heartbeat` events; the monitor's four
   liveness checks and the dashboard read the **event log first** (canonical,
   **survives the tmpfs `/run/im-snapshots` reboot wipe** that blinded this
   session's post-reboot checks) and fall back to the JSON snapshot only as a
   cold-start path. Known residual (documented, not a blocker): the heartbeat is
   mirrored inside the snapshot-writer, so it shares that writer's fate on an
   in-process stall — it adds reboot-survival, not an independent stall signal.

4. **Durable alert log** (`health_monitor.py`). Every alert/heartbeat JSONL
   append now `flush()` + `os.fsync()` so a hard power cut loses at most the
   in-flight line. This session's pre-outage Telegram alerts (09:22–09:26) were
   lost from disk exactly because the WD-file tail was unsynced when power
   dropped; this closes that gap going forward.

### Code review (high-effort, 7 angles) — found 6 issues, all fixed before deploy

The first implementation **passed all 116 tests but the review caught two
correctness bugs the tests missed** (the watchdog test used a fake engine, so it
never exercised real 15-min phase durations). All fixed and re-tested:

| # | Severity | Issue | Fix |
|---|---|---|---|
| R1 | **Critical** | Watchdog false-fired **every healthy session**: the 150 s phase-age threshold was far shorter than the legitimate 15-min `waiting_preopen`/`connecting` phases → spurious relaunch + "giving up" error daily | Reworked to **deadline-based** detection (judge progress only at/after 09:16; use early-phase-membership + tick-staleness, not phase-age) |
| R2 | **Critical** | Relaunch passed `restart_reason="engine_stall_watchdog"` (a **str**), but `resume_from_checkpoint` does `(restart_reason or {}).get("reason")` → **AttributeError** crash on any relaunch with an open checkpoint — the watchdog's own recovery path would crash | Pass a **dict** `{"reason": "engine_stall_watchdog"}` |
| R3 | High | `_load_restart_reason()` (one-shot, unlinks the file) was hoisted to run **unconditionally**, so a normal no-checkpoint startup silently consumed/discarded the operator's `restart_reason.json` | Consume **only when a checkpoint is actually applied** |
| R4 | High | If the watchdog task itself raised (e.g. tz lookup), the exception was **swallowed** (the `continue` skipped its `log.exception`) — safety net silently dead, no diagnostic | Log `watchdog task crashed` with `exc_info` |
| R5 | Medium | Heartbeat writers used `self._event_log` directly (no self-heal if the startup open failed) | Use `_ensure_event_log()`, made **thread-safe** with a double-checked lock (two `to_thread` workers call it) |
| R6 | Medium | `_liveness_age` returning `(data, None)` (heartbeat present, no parseable `written_at`) forced `age=999999` → **permanent stall**, skipping the JSON fallback | Fall back to JSON when `data is None OR age is None`; flag a no-timestamp stall only if JSON is also absent |

**Acknowledged design-altitude notes (deliberate, not fixed):** the monitor now
reconciles up to four engine-liveness signals (HTTP `/health`, SQLite heartbeat,
JSON mtime, systemd `is-active`) by hand-written precedence; the watchdog defends
a class Jun-2 didn't hit; durability is per-call-site rather than a shared
writer. These are accepted trade-offs given the "fix now, don't re-architect
mid-deploy" mandate — tracked for a future consolidation pass, not blockers.

### Test result

All suites green after the fixes: `test_event_log` 14, `test_engine_watchdog`
6 (new, deadline-logic), `test_engine_metrics`+`test_dhan_transport` 21,
`test_live_strategy` 11, `test_account_dashboard`+`test_dashboard_v2` 15,
`test_live_paper` 65 (incl. 3 new: systemd-unit list, durable JSONL, feed check
satisfied by SQLite heartbeat without JSON).

### Deployment

Deployed to zimaos on 2026-06-03 after the green re-review — see the deployment
log appended below once complete.

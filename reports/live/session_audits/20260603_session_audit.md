# 2026-06-03 Live Paper Session Audit

Generated 2026-06-03 ~17:15 IST from zimaos live artifacts under
`/media/WD-Storage/indian-markets-live`, systemd state, the EOD reports, the
on-disk alert + external-heartbeat streams, the engine WD file log, the signal
log, and the account ledger. Deployed server commit: **`df0b08d`** — the first
live session running the **2026-06-03 patch set** (engine progress watchdog,
health-monitor systemd-unit fix, SQLite liveness migration, durable alert log).

Companion docs: `20260601_session_audit.md` (first clean session),
`20260602_session_audit.md` (host freeze / no-trade + the patch this session
exercises), and `docs/design/wing6_live_deployment_reference.md`.

Focus per request: (a) full session verdict; (b) **did the 2026-06-03 patches
work as intended on their first live run?**; (c) **two account-state bugs the
user observed** — margin not updating on buy/sell, and EOD balance / all-time
PnL not rolling forward.

## Verdict

**PASS — the cleanest tradable session in the chain to date, and the first
2-symbol day.** NIFTY and SENSEX both entered full 4-leg Wing-6 iron condors at
09:20 and time-exited cleanly at 15:20. FINNIFTY/MIDCPNIFTY skipped for
strategy-correct reasons (`dte_above_max`, DTE 27). No collector race, no
`insufficient_depth` skip, no host outage, no manual restart, no crash resume,
**no watchdog relaunch needed** (it stayed armed and quiet — the correct
behavior on a healthy day). Both trades lost small amounts (net −₹931.09) — a
one-day strategy outcome, not an infra failure.

The 2026-06-03 patch set had its first live exercise and **held**: the
health-monitor systemd-unit fix produced a single grouped `engine_stall`
(post-session, cosmetic) instead of the Jun-2 7×-standalone-critical cascade;
the watchdog armed without false-firing; criticals fell to 6.

**However, this session also surfaced a real, confirmed defect** the user
reported: the **EOD account-ledger update never runs under Phase E1**, so the
persistent balance / all-time PnL / margin are frozen at the 2026-06-01 manual
backfill. Today's two trades did NOT roll into the account state. Root-caused
below (Tier 1). No real orders.

## Comparison to Prior Sessions

| Dimension | Jun 1 | Jun 2 | **Jun 3** |
|---|---|---|---|
| Verdict | PASS, 1 trade | NO-TRADE (host freeze) | **PASS, 2 trades** |
| Symbols traded | NIFTY | none | **NIFTY + SENSEX** |
| Trades opened | 1 | 0 | **2** |
| Net PnL | −₹2,717.81 | — | **−₹931.09** |
| Watchdog relaunch | n/a (not deployed) | n/a (would not help host freeze) | **armed, did not fire (correct)** |
| Critical alerts | 61 | (Telegram only) 7×/round | **6** |
| Engine-stall grouping | n/a | broken (probed legacy unit) | **fixed — 1 grouped row** |
| Account ledger updated at EOD? | manual backfill | no EOD | **NO (bug — see Tier 1)** |
| Deployed commit | `603f1d1`/`660f1ce` | `abad4d7`→`df0b08d` (deployed after close) | **`df0b08d`** |

## Final Runtime State (sampled 2026-06-03 ~17:14 IST)

| Check | Result |
|---|---|
| `live-stack.service` | active/running since 07:49:53 IST |
| `live-stack` MainPID | 1921, **NRestarts=0** |
| `health-monitor.service` | active/running since 07:49:46 IST |
| `health-monitor` MainPID | 1306, **NRestarts=0** |
| Deployed commit | `df0b08d` (2026-06-03 patch set) |
| Clock | Asia/Kolkata, synced (17:13 IST) |
| WD free | 92.8 GB |
| Engine final phase | `complete` (uvicorn stays up by E1 design) |

NRestarts=0 on both units across the whole session confirms no crash loop and no
systemd-driven restart — the relaunch this session needed was zero.

## Trading Outcome

| Metric | Value |
|---|---:|
| Trades | 2 |
| Wins | 0 |
| Gross PnL | −₹639.00 |
| Charges | ₹292.09 |
| Net PnL | **−₹931.09** |
| Resumed after crash | false |
| Resumed after manual restart | false |
| Gap minutes | null |

### Trade detail

| Symbol | Expiry | DTE | Credit | Exit debit | Net | Exit |
|---|---|---:|---:|---:|---:|---|
| NIFTY | 2026-06-09 | 6 | ₹11,791.00 | ₹12,155.00 | **−₹526.11** | time_exit |
| SENSEX | 2026-06-04 | 1 | ₹6,912.00 | ₹7,187.00 | **−₹404.98** | time_exit |

Both small losers via clean 15:20 time exit; no forced stale exit. NIFTY traded
at DTE 6 (weekly, VIX 16.37 ≥ 13 — passes). SENSEX traded at DTE 1 (≤ 2 filter).
Charges (₹292) were ~31% of the gross loss — a higher cost ratio than Jun 1
because two symbols' round-trips were paid for two small-magnitude trades; not a
fill/slippage anomaly, just small gross relative to fixed per-leg cost.

### Signal decisions

| Time IST | Symbol | Decision | Reason | VIX | DTE |
|---:|---|---|---|---:|---:|
| 09:20:01 | NIFTY | **Entry** | `entry` | 16.37 | 6 |
| 09:20:01 | FINNIFTY | Skip | `dte_above_max` | 16.37 | 27 |
| 09:20:01 | MIDCPNIFTY | Skip | `dte_above_max` | 16.37 | 27 |
| 09:20:02 | SENSEX | **Entry** | `entry` | 16.37 | 1 |
| 15:20:00 | NIFTY | Exit | `time_exit` | — | — |
| 15:20:00 | SENSEX | Exit | `time_exit` | — | — |

Both skips strategy-correct (FINNIFTY/MIDCPNIFTY monthly, DTE 27 ≫ max 7).

## Patch Verification — Did the 2026-06-03 Fixes Work?

This session is the first live run of commit `df0b08d`. Mapping evidence to each
fix:

| Fix | Claim | Verified today? |
|---|---|---|
| **#1 Engine progress watchdog** (`api/main.py`) | Deadline-based; arms ≤09:17, relaunches once if stuck in early phase / ticks stale | **Yes (negative case).** Engine advanced normally; watchdog did **not** fire (no `relaunch`/`wedged`/`giving up` in the log), NRestarts=0. The R1 review fix (deadline- not phase-age-based) held — **no spurious daily relaunch**, which was the critical regression risk. First proof the watchdog is quiet on a healthy day. |
| **#2 Health-monitor systemd-unit fix** | Probe `live-stack` not legacy `live-paper`; restores `engine_stall` grouping | **Yes.** The only `engine_stall` row (15:32:00, post-`complete`) was a **single grouped critical**, not the Jun-2 7×-standalone cascade. The systemd `is-active` check now resolves true for the running unit, so stale snapshots group correctly. |
| **#3 SQLite liveness migration** | Heartbeat events; monitor/dashboard read event log first, survive tmpfs reboot wipe | **Partially.** Event log is 72 MB and healthy; no reboot occurred this session so the reboot-survival path was not exercised. Reads succeeded (no `*_missing` storm during market hours). Full reboot-survival proof still pending a reboot day. |
| **#4 Durable (fsync) alert log** | fsync-on-append so a power cut loses ≤1 line | **Indirectly.** `20260603_alerts.jsonl` written through the day, intact, no truncation. No power cut to stress it; the durability guarantee is structural and present in deployed code. |

**Net: the patch set worked as intended on its first live run.** The two
highest-risk items — watchdog false-firing every session (R1) and the
unit-probe mis-grouping — are both confirmed fixed in production. No new
regression introduced by the patch.

## Account-State Bugs (user-reported) — ROOT-CAUSED

The user reported two symptoms: **(A)** margin not updating when option
contracts are bought/sold, and **(B)** after EOD, today's trades not reflected
in current balance / all-time PnL. Both are real. They are **two different
layers**, with one shared root cause for the EOD symptom.

### Evidence

`/media/WD-Storage/indian-markets-live/account/` contains exactly two files,
**both last written Jun 2 03:24** (the manual backfill timestamp):

- `account_ledger.jsonl` — **one row only, `session_date: 2026-06-01`.**
- `latest_account_state.json` — `current_balance: 997282.19`,
  `all_time_net_pnl: -2717.81`, `total_trades: 1`, `total_sessions: 1`.

Jun-2 (no-trade, no EOD) is legitimately absent. But **Jun-3 had a full EOD**
(reports written 15:31) and still produced **no new ledger row** and **no
account-state update**. Grep of today's engine log for `account ledger` →
**empty**: `update_account_ledger` was never called.

### Root cause (symptom B — EOD not rolling forward)

The EOD account-ledger hook lives **only** in the legacy daily orchestrator
`scripts/live/run_paper_trading.py` (lines 659–673: after the engine task
completes, it calls `update_account_ledger(live_root, today)`).

Under **Phase E1**, production no longer runs `run_paper_trading.py`. The engine
runs **in-process** under `live-stack.service` → `scripts/live/api/main.py`'s
`_run_integrated_engine` supervisor. That supervisor:

- imports only `_load_profile`, `_load_checkpoint`, `_load_restart_reason`,
  `_ensure_fresh_token` from `run_paper_trading` — **not** `update_account_ledger`
  (nor `write_paper_reports`);
- after the engine task completes (normal EOD), it calls `engine.close()` and
  breaks the relaunch loop — **there is no post-engine EOD ledger step at all.**

The EOD reports (`eod_summary.json`, `paper_summary.md`, spot bars, EOD
snapshot) all still appear because **the engine writes those itself**
(`paper_engine._generate_eod_report` etc.). But the **account-ledger update is
NOT an engine EOD step** — it was always an orchestrator-level post-step, and
E1 dropped the orchestrator. So the ledger has been frozen since the only time
`update_account_ledger` ran (the manual `backfill_account_ledger.py` on Jun 2).

`update_account_ledger` itself is correct, self-contained, and idempotent: it
reads `paper_trades/{date}.json`, re-walks the opening==prior-closing chain, and
rewrites both `account_ledger.jsonl` and `latest_account_state.json`. **It just
is never invoked at EOD under E1.** This is committed-code divergence (the E1
supervisor predates / never adopted the account-tracker EOD hook), not server
drift.

### Root cause (symptom A — no margin update on buy/sell intraday)

This is a **separate, by-design gap**, not the same bug. The account-state
tracker is **EOD-only**: one ledger row per session, with
`peak_margin_used` / `capital_committed` computed once inside
`compute_session_row` from the *closed* trades. There is **no intraday
mark-to-market of margin** anywhere — the dashboard `/api/live/account` reads the
EOD-written `latest_account_state.json` straight through the bridge
(`get_account_state`), which has no live/open-position margin path.

So when a position is opened or closed intraday, nothing updates the margin
panel — because the tracker was scoped to settle once at EOD, and the margin
numbers are explicitly **uncalibrated estimates** (per the design doc's deferred
list: `hedged_floor_per_lot` are estimates pending a Kotak SPAN calibration).

**Conclusion:** symptom A (intraday) is a *design limitation* (no live margin
marking yet); symptom B (EOD) is a *real bug* (the EOD hook isn't wired into the
E1 supervisor). Fixing B will make the panel update once per day after close.
Fixing A is a larger feature (live open-position margin marking) and is gated on
margin calibration — defer it.

## Alerts (6 critical / 30 warning / 3 resolved)

| Reason | Count | Severity | Assessment |
|---|---:|---|---|
| `market_data_lag` | 19 | mixed | Intraday quote-lag blips 09:24→15:21; consistent with 96.5% readiness; both trades filled fine. Low-grade, non-fatal. |
| `wd_low_space_warn` | 10 | warning | All **after 15:35 (off-hours)**, message "pre-run WD free 92.8 GB (< 100 GB)". The 100 GB threshold is the *pre-run* gate; 92.8 GB is well above the 20 GB *intraday* floor. Off-hours noise, not a real disk problem. |
| `depth_ready_low_nifty` / `depth_ready_low` | 3 / 3 | critical | Brief NIFTY depth dips; recovered; did not block the 09:20 entry. |
| `parquet_missing` | 2 | critical | Startup window only. |
| `engine_stall` | 1 | critical | **15:32:00, post-`complete`** — process_health age=35s after the engine stopped writing snapshots at EOD. This is the systemd-unit fix **working**: a single grouped row, not the Jun-2 cascade. Cosmetic post-session false-positive. |
| `quote_freshness_low` | 1 | warning | Single transient dip. |

Health-monitor EOD uptime: **overall 96.49%, paper-engine 97.67%, depth
collector 97.24%, data-gap 0.0 min, latest external heartbeat 15:34:43 `ok`.**
A strong, continuous-liveness day.

## Dhan Transport Split

- **REST option-chain:** Healthy. Both NIFTY and SENSEX chains resolved; both
  entered at 09:20 with positive credit. No 429/500 cascade in the entry window.
- **Live-feed websocket:** Healthy. Quote freshness ~97%; one transient
  `quote_freshness_low`.
- **20-depth websocket (NIFTY):** Functional; brief `depth_ready_low_nifty` dips
  that recovered before/at entry. SENSEX uses top-of-book (no depth subscription)
  and filled fine.

## Data Integrity

| Artifact | State |
|---|---|
| `raw_depth_packets/20260603` | 1,520 `.bin` |
| `order_book_1min/20260603` | 1,500 parquet (still ~5 fragments/instrument-minute — known, deferred from Jun 1) |
| `paper_trades/20260603.json` | 12.6 KB, 2 trades, full leg fills |
| `event_log/20260603.sqlite` | 72 MB (+WAL) — healthy, heartbeat events now included |
| reports (eod/paper/uptime) | all 3 present |
| `account/` | **STALE — frozen at Jun 1 (bug, see Tier 1)** |

## Session Verdict for Records

- **Session class:** clean tradable live-paper day (2 trades).
- **Performance evidence:** **usable** (2nd and 3rd live data points: NIFTY
  −₹526.11, SENSEX −₹404.98). Live OOS row #2.
- **Reliability evidence:** **usable and positive** — first 2-symbol day, watchdog
  quiet on a healthy run, NRestarts=0, criticals down to 6, engine-stall
  grouping fixed.
- **Patch evidence:** **usable and positive** — the 2026-06-03 patch set held on
  first live exercise; the two highest-risk fixes (watchdog false-fire,
  unit-probe mis-grouping) confirmed working.
- **Trade result:** −₹931.09 (both small directional/cost losers, defined-risk,
  clean time exits — not an infra defect).
- **Open defect (new, confirmed):** EOD account-ledger update is not wired into
  the Phase-E1 supervisor → persistent balance / all-time PnL / margin frozen at
  Jun 1. Intraday margin marking absent by design.

## Fix / Follow-up List (prioritized)

**Tier 1 — the real new bug: wire the EOD account-ledger update into E1**

1. **Call `update_account_ledger(live_root, today)` after the engine completes
   in `api/main.py`'s `_run_integrated_engine`** — mirror the legacy
   orchestrator block (`run_paper_trading.py:659–673`): on normal (non-stalled)
   engine completion, once `paper_trades/{date}.json` exists, run
   `update_account_ledger` in a thread with the same timeout/except guard. This
   is the single fix that makes balance/all-time-PnL/drawdown roll forward at
   EOD. It is idempotent, so re-running is safe. **After landing, backfill the
   missing Jun-3 row** (`backfill_account_ledger.py` or a one-shot
   `update_account_ledger` for 2026-06-03) so the ledger catches up without
   waiting for the next session.
2. **Consider also wiring `write_paper_reports`** into the same E1 post-step for
   parity/robustness — though the engine currently writes its own EOD reports,
   the orchestrator's version is a belt-and-suspenders path the legacy code had.
   Lower priority; verify no double-write conflict first.

**Tier 2 — margin observability (the intraday symptom)**

3. **Intraday open-position margin marking** is a feature, not a bug fix, and is
   **gated on margin calibration** (the `hedged_floor_per_lot` values are
   estimates pending a Kotak SPAN snapshot). Defer until (a) Tier 1 lands and
   (b) margin is calibrated from a real Kotak `limits()`/SPAN snapshot. Until
   then the panel can only honestly show EOD margin estimates.
4. **Add a "last updated" / `session_date` freshness indicator** to the account
   panel so a frozen ledger is visually obvious (it currently shows Jun-1 numbers
   with no staleness cue — which is how this bug went unnoticed).

**Tier 3 — alert-quality polish (cheap)**

5. **Suppress the off-hours `wd_low_space_warn`** that compares against the
   100 GB *pre-run* threshold after the session has ended — or relabel it so a
   post-close 92.8 GB doesn't emit a warning every 10 min off-hours.
6. **Suppress / down-rank the post-`complete` `engine_stall`** at ~15:32: once
   the engine reaches `phase=complete`, stale `process_health` is expected (the
   engine intentionally stops writing snapshots), so it should not raise a
   market-hours-style critical. Gate the stall check on `phase != complete`.

**Tier 4 — still-pending verification from prior audits**

7. **Reboot-survival of the SQLite liveness migration (#3)** was not exercised
   (no reboot today). Confirm on the next reboot day that the monitor/dashboard
   read heartbeat events from the event log when tmpfs `/run/im-snapshots` is
   wiped.
8. **1-min parquet fragmentation** (~5 fragments/instrument-minute) still
   present; deferred read-side dedup decision from Jun 1 stands.

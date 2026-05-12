# Session Audit - 2026-05-12

Generated 2026-05-12 after reconnecting to `zimaos`.

Primary sources:
- `zimaos:/media/WD-Storage/indian-markets-live/alerts/20260512_alerts.jsonl`
- `zimaos:/media/WD-Storage/indian-markets-live/alerts/20260512_external_heartbeat.jsonl`
- `journalctl --list-boots`
- `journalctl -b -1`
- `systemctl status/cat live-paper.service live-paper-daily.timer`
- `zimaos:/media/WD-Storage/indian-markets-live/logs/live-paper.log`

Important source note: the local workspace copy at `data/live/alerts/20260512_alerts.jsonl`
was not treated as authoritative. The server alert file was 402 KB and ended at
09:54:48 IST. The local copy was larger and contained stale storage alerts that did not
match the current server state.

---

## Executive Summary

| Item | Result |
|---|---|
| Session date | 2026-05-12 |
| Trading result | 0 trades |
| Live-paper pre-market status | Not running before 09:10 |
| Alert window analysed | 09:10:02-09:54:48 IST |
| Host outage | Previous boot ended 09:54:18; current boot began 15:51:12 |
| External heartbeat gap | 09:54:15 -> 15:51:33, 357.3 minutes |
| Live service after reboot | Started at 15:51 and again at 16:22, but skipped entry because restart was past entry window |
| New timer | `live-paper-daily.timer`, enabled, next trigger 2026-05-13 08:45 IST |
| Storage now | Healthy on server: WD free 206.7 GB |

Verdict: the new 08:45 systemd timer fixes the missed pre-market service launch that
created the 09:10 alerts. It does not fix a whole-host outage during market hours, and it
does not reconstruct lost intraday progress if the server is manually rebooted after the
entry window.

---

## Timeline

| Time IST | Evidence | Interpretation |
|---|---|---|
| 00:00-01:20 | 81 `token_expires_before_eod` alerts | Dhan token was considered unsafe for EOD buffer overnight. This cleared later after token renewal or service restart. |
| 04:15-07:33 | 789 `clock_drift_high` alerts | Server clock drift was +3.149s, above the 2s threshold. |
| 09:10:02 | `raw_packet_missing`, `parquet_missing` begin | Depth collector had not produced any 20260512 raw or normalized depth files. |
| 09:10:12 | `feed_state_stale`, `vix_quote_missing` begin | The live paper engine/feed was not producing fresh feed state or VIX quotes. |
| 09:10:28 | `quote_freshness_low`, `depth_ready_low` begin | No fresh quotes and 0% depth readiness. |
| 09:17:08 | Chain alerts begin for all four symbols | NIFTY, FINNIFTY, MIDCPNIFTY, and SENSEX option chains were never loaded. |
| 09:30:13 | `one_min_missing` begins | No 1-minute order-book files existed for the day. |
| 09:54:18 | Last previous-boot host log | Host stopped abruptly; no graceful shutdown lines were present in the inspected window. |
| 09:54:48 | Last server alert | Alerts stopped with stale feed/quotes/VIX/chains/depth still active. |
| 15:51:12 | Current boot starts | Manual/later reboot brought the host back. |
| 15:51:27 | `live-paper.service` starts | Service started after reboot, connected feed, fetched chains, wrote EOD/paper summary, skipped entry because it was past entry window. |
| 16:09:15 | `live-paper-daily.timer` enabled | Daily 08:45 pre-market timer became active. |
| 16:22:17 | `live-paper.service` restarted | Service active; timer now shown as trigger owner. |

---

## Alerts From 09:10

Authoritative server alert counts after 09:10:

| Alert reason | Count | First | Last | Cause |
|---|---:|---|---|---|
| `feed_state_stale` | 174 | 09:10:12 | 09:54:48 | No live feed state was being written. |
| `vix_quote_missing` | 174 | 09:10:12 | 09:54:48 | VIX never arrived because the feed was not running. |
| `quote_freshness_low` | 173 | 09:10:28 | 09:54:48 | 0% fresh quotes. |
| `depth_ready_low` | 173 | 09:10:28 | 09:54:48 | 0% depth readiness. |
| `chain_not_loaded_finnifty` | 147 | 09:17:08 | 09:54:48 | Chain fetch never ran for FINNIFTY. |
| `chain_not_loaded_midcpnifty` | 147 | 09:17:08 | 09:54:48 | Chain fetch never ran for MIDCPNIFTY. |
| `chain_not_loaded_nifty` | 147 | 09:17:09 | 09:54:48 | Chain fetch never ran for NIFTY. |
| `chain_not_loaded_sensex` | 147 | 09:17:09 | 09:54:48 | Chain fetch never ran for SENSEX. |
| `raw_packet_missing` | 45 | 09:10:02 | 09:54:19 | No raw depth packet file existed for 20260512. |
| `parquet_missing` | 45 | 09:10:02 | 09:54:19 | No normalized order-book parquet existed for 20260512. |
| `one_min_missing` | 25 | 09:30:13 | 09:54:19 | No 1-minute order-book parquet existed for 20260512. |

These are not independent bugs. They collapse to one primary failure:
`live-paper.service` and its depth collector were not actually running before market open.

---

## Root Cause

### Primary failure: live paper service was not running in pre-market

There were no `live-paper.service` journal entries on the previous boot for
2026-05-12 after 2026-05-11 15:17. The health alerts from 09:10 onward are exactly what
the monitor should emit when the live engine is absent:

- no `latest_feed_state.json` updates
- no VIX quote
- no fresh quotes
- no option chain status
- no raw depth packet files
- no normalized depth parquet
- no 1-minute depth parquet

The runner log after the later reboot confirms the intended behavior if started late:
it connected, fetched chains, then logged `mid-session restart past entry window - skipping entry`.

### Secondary failure: host went offline at 09:54

`journalctl --list-boots` shows:

- previous boot: ended `2026-05-12 09:54:18 IST`
- current boot: started `2026-05-12 15:51:12 IST`

The external heartbeat log confirms the outage with a 357.3 minute gap:
`09:54:15 -> 15:51:33`.

The inspected journal window around 09:54 had no graceful shutdown sequence. That looks
like an abrupt host/power/kernel-level stop or manual power event, not a normal
`systemctl reboot`/scheduled shutdown.

### Non-issue: storage alert was stale locally, not current on zimaos

The server now reports WD free space at 206.7 GB and the live alert state has no active
storage alert. Local workspace files still showed `wd_low_space_critical`, but that did
not match the authoritative server state.

---

## Does The 08:45 Timer Fix It?

| Issue | Fixed by `live-paper-daily.timer`? | Notes |
|---|---|---|
| Live paper service not started before market | Yes | Timer is enabled and will trigger `live-paper.service` at Mon-Fri 08:45 IST. |
| Feed stale from 09:10 | Yes, if Dhan/auth/network are healthy | Starting the service before 09:10 should create fresh feed state. |
| VIX missing | Yes, if the service starts and subscribes correctly | This is downstream of the feed being absent today. |
| Quotes fresh 0% | Yes, if the service starts and chain subscriptions succeed | Downstream of missing live engine. |
| Chain not loaded for all symbols | Yes, if service reaches chain-fetch time | Timer gives the runner time to reach the normal chain-fetch path. |
| Depth 0%, raw packets missing, parquet missing, 1-min missing | Yes, if depth collector remains healthy | Timer starts the orchestrator/depth collector before market. |
| Host outage at 09:54 | No | A timer cannot keep a powered-off or crashed host alive. |
| 357-minute external heartbeat gap | No | Needs host reliability or out-of-band recovery/alerting, not a service start timer. |
| Lost intraday progress after manual reboot | No | Persistent timer can start missed jobs after reboot, but if reboot is after entry it correctly skips entry. |
| Clock drift high | No | Needs time-sync hardening/check before market. |
| Token expiry overnight alert | Not directly | Token renewal is separate; verify `/etc/cron.d/dhan-token-renewal` and freshness before 08:45. |

Bottom line: the timer fixes today's first problem, not today's second problem.

---

## Current Verified State

- `live-paper.service`: enabled and active at 16:29, running PID 80379.
- `live-paper-daily.timer`: enabled and active, next trigger `2026-05-13 08:45:00 IST`.
- Timer definition:

```ini
[Timer]
OnCalendar=Mon-Fri 08:45:00 Asia/Kolkata
Persistent=true
Unit=live-paper.service
```

- `health-monitor.service`: enabled and active since 15:51.
- `dashboard-api.service`: enabled and active since 15:51.
- `zimaos-scheduled-reboot.timer`: next trigger `2026-05-13 04:00:00 IST`.
- Live root is correctly symlinked:
  `/DATA/live-paper/indian-markets/data/live -> /media/WD-Storage/indian-markets-live`.
- Current live alert state: no active alerts, external heartbeat OK, WD free 206.7 GB.

---

## Remaining Gaps Before Trusting Tomorrow

1. Confirm at 08:46 tomorrow that `systemctl status live-paper.service` shows it was
   started by `live-paper-daily.timer`.
2. Confirm by 09:05 that `latest_process_health.json` is fresh and phase is
   `waiting_preopen` or feed warm-up, not absent.
3. Confirm by 09:12 that `latest_feed_state.json`, `latest_depth_cache.json`, raw depth
   packets, and order-book parquet are fresh.
4. Confirm by 09:18 that all four option chains are loaded.
5. Confirm by 09:20 that VIX is fresh and no `vix_quote_missing` alert exists.
6. Treat any external heartbeat gap over 2 minutes during market hours as a host-level
   incident, not a live-paper service incident.

Recommended hardening: add an out-of-band host watchdog/recovery path, or at minimum a
pre-market checklist command that asserts service active, fresh health snapshots, token
validity, clock sync, and heartbeat success before 09:10.

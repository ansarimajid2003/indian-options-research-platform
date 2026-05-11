# Phase 7 Dashboard + Live Readiness Verification - 2026-05-11

## Verdict

PASS for pre-open readiness.

Phase 7 dashboard/API is deployed on `zimaos`, the dashboard is serving from
FastAPI, the live runner and health monitor are active under `systemd`, and
Dhan connectivity passes with the real `.env.live` environment.

## Evidence

Verification time: 2026-05-11 07:29 IST.

### Local repo

- `python -m unittest discover -s tests -v` passed: 62 tests OK.
- `python -m unittest tests.test_live_paper -v` passed after the pre-open
  dashboard-status fix: 8 tests OK.
- Phase 7 route contract is present:
  - `/api/live/session`
  - `/api/live/positions`
  - `/api/live/equity-curve`
  - `/api/live/signal-log`
  - `/api/live/depth-health`
  - `/api/live/storage-health`
  - `/api/live/alerts`
  - `/api/live/spot/{symbol}`
  - `/ws/live`

### zimaos services

All expected services are active and enabled:

| Service | Status | Notes |
|---|---|---|
| `dashboard-api.service` | active/running | FastAPI on `127.0.0.1:8000` |
| `health-monitor.service` | active/running | Writes alert/storage snapshots |
| `live-paper.service` | active/running | Paper runner only; no real orders |
| `zimaos-scheduled-reboot.timer` | active/waiting | Next reboot: 2026-05-13 04:00 IST |

### Dashboard API

`GET /api/live/session` after the fix:

```json
{
  "session_date": "2026-05-11",
  "market_status": "CLOSED",
  "engine_phase": "waiting_preopen",
  "engine_pid": 436579,
  "open_position_count": 0,
  "feed_connected": false,
  "feed_subscribed_count": 0,
  "quote_freshness_pct": 0.0,
  "process_health_age_s": 3.86
}
```

This is the correct pre-open state: the runner is alive and waiting, but the
market feed is not expected to produce packets while closed.

`GET /api/live/storage-health`:

```json
{
  "wd_mount_ok": true,
  "wd_free_gb": 215.56
}
```

`GET /` returns the dashboard HTML. `/api/openapi.json` returns the Phase 7a
OpenAPI contract.

### Dhan connectivity

Ran on `zimaos` with `.env.live` loaded:

```text
PASS  token_expiry  : valid for 0.8 days
PASS  rest_optchain : 18 expiries  first=2026-05-12
PASS  live_feed_ws  : connected  no packet in 5 s (market closed)
PASS  depth_ws      : connected  no packet in 5 s (market closed)

ALL PASS  (4/4)
```

## Fixes Applied During Verification

1. `options_backtest/paper_engine.py`
   - The paper runner now starts its snapshot loop immediately.
   - Before 09:00 it publishes `engine_phase="waiting_preopen"` plus fresh
     `latest_process_health.json` and `latest_feed_state.json`.
   - This prevents the dashboard from incorrectly showing `offline` while the
     runner is alive and waiting for market open.

2. `scripts/live/systemd/dashboard-api.service`
   - Moved `StartLimitIntervalSec` and `StartLimitBurst` from `[Service]` to
     `[Unit]`.
   - This removes the systemd warning:
     `Unknown key 'StartLimitIntervalSec' in section [Service]`.

Both fixes were deployed to `zimaos`, `systemctl daemon-reload` was run, and
`dashboard-api.service` plus `live-paper.service` were restarted cleanly.

## Current Caveats

- The market is closed at verification time, so `feed_connected=false`,
  `ready_pct=0.0`, and `tracked=0` are expected until the feed starts.
- `/api/live/alerts` currently uses `active_alerts` as the timeline field. It
  may show the earlier `clock_not_synced` boot artifact from 04:15 IST, but the
  raw `latest_alert_state.json` has `active_alerts: []` and `alert_counts: 0`.
- Final live-readiness must be checked again after 09:00 IST and especially
  before the 09:20 entry window.

## Market-Open Watchpoints

At or after 09:00 IST:

- `/api/live/session.engine_phase` should move from `waiting_preopen` to
  `connecting`.
- `latest_feed_state.json` should remain fresh.
- `feed_connected` should become `true`.

By 09:10 IST:

- If `feed_connected=false`, the paper engine should abort the day and log
  `feed_not_connected`.

By 09:15-09:20 IST:

- Option-chain discovery should complete.
- `feed_subscribed_count` should rise.
- Depth readiness should start climbing toward the health-monitor threshold.
- Any entry/skip event should appear in the signal log.

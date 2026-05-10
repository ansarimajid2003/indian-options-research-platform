# Phase 5 Health Monitor Validation - 2026-05-11

## Verdict

PASS. Phase 5 health-monitor implementation and server-side drills completed on `zimaos`.

## Code Validated

- `scripts/live/health_monitor.py`
- `scripts/live/collect_order_book.py`
- `tests/test_live_paper.py`

## Server Checks

| Check | Result | Evidence |
|---|---:|---|
| Focused live-paper tests on `zimaos` | PASS | `8` tests passed |
| Full unittest suite on `zimaos` | PASS | `62` tests passed, `22` skipped because Shoonya raw data is not present on server |
| Health monitor one-shot run | PASS | `latest_alert_state.json` written at `2026-05-11T02:52:12+05:30`; active alerts `0`; WD free `215.57 GB` |
| Telegram alarm drill | PASS | `health_monitor.py --test-alerts` returned `telegram: PASS` |
| Healthchecks.io drill | PASS | `--test-alerts` returned `start=True fail=True recover=True` |
| Sentry drill | PASS | explicit `capture_exception()` sent event `26ea3315` |
| systemd restart recovery | PASS | `SIGKILL` changed monitor PID `1047503 -> 1048154`; restart counter `1`; service active/running |
| External heartbeat block | PASS | pre-ping OK; `3` outbound HTTPS REJECT rules added; `4` blocked heartbeat attempts failed over ~3.7 min; recovery ping OK |
| Monitor heartbeat recovery | PASS | monitor log recorded failed heartbeat at `2026-05-11T02:50:36+05:30` and OK heartbeat at `2026-05-11T02:53:01+05:30` |
| Secret hygiene | PASS | No token, client ID, DSN, or heartbeat URL printed in validation output |

## Operational Notes

- `.env.live` permissions on `zimaos` were tightened to `600`.
- Temporary firewall rules were removed after the heartbeat block drill.
- Temporary `/tmp/heartbeat_block_test.sh` was removed after the drill.
- `health-monitor.service` was restarted after the drill and ended active.
- Hotfix after validation: Healthchecks.io was configured with a 1-minute period, while off-hours
  monitor pings were running every 5 minutes. This caused repeated DOWN/UP alerts. Off-hours
  pings are now every 60 seconds in `scripts/live/health_monitor.py` and the service was restarted.
- Follow-up incident: a later Healthchecks.io DOWN around `04:00` was a real host outage/reboot, not
  heartbeat flapping. `zimaos` booted at `2026-05-11 04:14 IST`; local heartbeat log has a gap from
  `03:59:13` to `04:15:14`. A transient `clock_not_synced` alert during the first minute after boot
  recovered normally; `health_monitor.py` now suppresses that startup-only clock-sync noise for the
  first 5 minutes while still alerting on real clock drift or persistent unsynced state.

## Remaining Human Confirmation

The server can verify that Telegram, Healthchecks.io, and Sentry accepted the test events. It cannot read the user's phone notifications from Healthchecks.io or Sentry because no service API token is configured in `.env.live`.

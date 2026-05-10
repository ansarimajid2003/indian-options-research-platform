# Phase 6 systemd Validation - 2026-05-11

## Verdict

PASS. `health-monitor.service` and `live-paper.service` are installed, enabled,
active, and restart under `systemd` supervision on `zimaos`.

## Installed Units

| Unit | Installed path | Local template hash matched server? | Enabled | Active after drill |
|---|---|---:|---:|---:|
| `health-monitor.service` | `/etc/systemd/system/health-monitor.service` | yes, SHA256 `c277655325b39d664a92861c4e4e144c11231d04239d4d56e12f971f4eda4dbe` | yes | yes |
| `live-paper.service` | `/etc/systemd/system/live-paper.service` | yes, SHA256 `805ddae7427c684488c719d0e941f8ee5616a7fc394f46fb522ac391914f53fd` | yes | yes |

## Restart Drills

| Drill | Expected restart window | Evidence |
|---|---:|---|
| `systemctl kill -s SIGKILL health-monitor.service` | within 10 s | Restart counter `1`; restarted at `2026-05-11 04:41:01 IST`; active/running with PID `66537` |
| `systemctl kill -s SIGKILL live-paper.service` | within 45 s | Restart counter `1`; restarted at `2026-05-11 04:41:48 IST`; active/running with PID `68191` |

## Commands Run

```bash
systemctl status health-monitor.service --no-pager -l
systemctl status live-paper.service --no-pager -l
systemctl is-enabled health-monitor.service live-paper.service
systemctl kill -s SIGKILL health-monitor.service
systemctl kill -s SIGKILL live-paper.service
systemctl show health-monitor.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStartTimestamp
systemctl show live-paper.service -p ActiveState -p SubState -p MainPID -p NRestarts -p ExecMainStartTimestamp
sha256sum /etc/systemd/system/health-monitor.service /etc/systemd/system/live-paper.service
```

## Notes

- Both units run from `/DATA/live-paper/indian-markets` and read
  `/DATA/live-paper/indian-markets/.env.live`.
- `health-monitor.service` writes logs under `/DATA/live-paper/logs`.
- `live-paper.service` writes logs under `/media/WD-Storage/indian-markets-live/logs`.
- `live-paper.service` remains enabled and active on the server. It is still a paper runner only;
  no real broker orders are placed by this stack.

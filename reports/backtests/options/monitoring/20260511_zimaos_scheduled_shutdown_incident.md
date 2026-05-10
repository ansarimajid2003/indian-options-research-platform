# ZimaOS Scheduled Shutdown Incident - 2026-05-11

## Summary

The server did not crash. ZimaOS executed a configured scheduled shutdown at
04:00 IST on Monday 2026-05-11.

The intended behavior was a scheduled restart, but the ZimaOS setting in
`/etc/casaos/zimaos.conf` was `ScheduledOff`, which powers the host off.

## Evidence

- Previous boot ended: 2026-05-11 04:00:15 IST
- Current boot began: 2026-05-11 04:14:59 IST
- Journal evidence:
  - `2026-05-11 04:00:02` - `zimaos`: `Executing scheduled shutdown`
  - `2026-05-11 04:00:03` - `systemd-logind`: `The system will power off now!`
  - `2026-05-11 04:00:03` - `systemd-logind`: `System is powering down.`
- ZimaOS config before fix:
  - `/etc/casaos/zimaos.conf`: `ScheduledOff = 0 4 * * MON,WED,FRI`

## Fix Applied

Disabled the ZimaOS scheduled shutdown line:

```ini
ScheduledOff =
```

Created a dedicated systemd scheduled reboot timer:

```ini
[Timer]
OnCalendar=Mon,Wed,Fri *-*-* 04:00:00
Persistent=false
RandomizedDelaySec=0
AccuracySec=1min
Unit=zimaos-scheduled-reboot.service
```

The service executes:

```ini
ExecStart=/usr/bin/systemctl --no-block reboot
```

Backup created on the server:

```text
/etc/casaos/zimaos.conf.bak.20260511_0438
```

## Verification

- `zimaos.service`: active
- `zimaos-scheduled-reboot.timer`: active
- `health-monitor.service`: active
- `live-paper.service`: active
- Next scheduled reboot:
  - 2026-05-13 04:00:00 IST

## Notes

The earlier Healthchecks DOWN/UP alerts were expected after the host powered
off. The subsequent `clock_not_synced` recovery alert was a boot-time NTP
settling artifact after the host came back online.

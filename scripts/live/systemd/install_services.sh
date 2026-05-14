#!/usr/bin/env bash
# Deploy Wing-6 live paper trading services to zimaos.
# Run as root from the repo root: bash scripts/live/systemd/install_services.sh
set -euo pipefail

REPO=/DATA/live-paper/indian-markets
SERVICE_DIR="$REPO/scripts/live/systemd"

echo "=== Wing-6 service install ==="

# ── Sanity checks ──────────────────────────────────────────────────────────────
if [[ $EUID -ne 0 ]]; then
    echo "ERROR: must run as root (sudo bash $0)" >&2
    exit 1
fi
if [[ ! -d "$REPO" ]]; then
    echo "ERROR: repo not found at $REPO" >&2
    exit 1
fi
if [[ ! -f "$REPO/.env.live" ]]; then
    echo "ERROR: $REPO/.env.live not found — create it before installing services" >&2
    exit 1
fi
if [[ ! -f "$REPO/.venv/bin/python" ]]; then
    echo "ERROR: venv not found at $REPO/.venv — run: python -m venv .venv && .venv/bin/pip install -r requirements-live.txt" >&2
    exit 1
fi
if ! mountpoint -q /media/WD-Storage; then
    echo "ERROR: /media/WD-Storage is not mounted" >&2
    exit 1
fi

# ── Create log directories ─────────────────────────────────────────────────────
mkdir -p /DATA/live-paper/logs
mkdir -p /media/WD-Storage/indian-markets-live/logs

echo "Log dirs: OK"

# ── Run alarm drill before installing services ─────────────────────────────────
echo "Running alarm drill (tests Telegram, Healthchecks.io, Sentry)..."
set -a
# shellcheck disable=SC1091
. "$REPO/.env.live"
set +a
if "$REPO/.venv/bin/python" scripts/live/health_monitor.py --test-alerts; then
    echo "Alarm drill: PASS"
else
    echo "WARNING: one or more notification channels failed the drill."
    read -rp "Continue installing services anyway? [y/N] " yn
    [[ "${yn,,}" == "y" ]] || { echo "Aborted."; exit 1; }
fi

# ── Install systemd units ──────────────────────────────────────────────────────
cp "$SERVICE_DIR/health-monitor.service" /etc/systemd/system/health-monitor.service
cp "$SERVICE_DIR/live-paper.service"     /etc/systemd/system/live-paper.service
chmod 644 /etc/systemd/system/health-monitor.service
chmod 644 /etc/systemd/system/live-paper.service
echo "Systemd units: installed"

mkdir -p /etc/systemd/timesyncd.conf.d
cp "$SERVICE_DIR/zz-live-paper-timesyncd.conf" /etc/systemd/timesyncd.conf.d/zz-live-paper.conf
chmod 644 /etc/systemd/timesyncd.conf.d/zz-live-paper.conf
systemctl restart systemd-timesyncd.service
echo "Timesyncd: installed live-paper polling override"

# ── Install cron ───────────────────────────────────────────────────────────────
cp "$SERVICE_DIR/dhan-token-renewal" /etc/cron.d/dhan-token-renewal
chmod 644 /etc/cron.d/dhan-token-renewal
echo "Cron: installed (token renewal at 08:30 IST Mon-Fri)"

# ── Enable and start ───────────────────────────────────────────────────────────
systemctl daemon-reload
systemctl enable health-monitor.service live-paper.service
echo "Services: enabled (auto-start on boot)"

systemctl start health-monitor.service
echo "health-monitor: started"

echo ""
echo "=== Done ==="
echo "Check status:  systemctl status health-monitor live-paper"
echo "Watch logs:    journalctl -fu health-monitor"
echo "Start engine:  systemctl start live-paper"
echo ""
echo "NOTE: live-paper.service is enabled but NOT started here."
echo "Start it manually on the first trading morning after verifying health-monitor is healthy."

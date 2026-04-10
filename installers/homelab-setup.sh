#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# FocusLock Homelab Setup — deploys bridge + mail service on the homelab server
# Run with: sudo bash homelab-setup.sh
# This is the central hub that connects phones and desktops
#
# Required: /opt/focuslock/config.env with at minimum:
#   BRIDGE_TAILSCALE_IP=<tailscale IP of phone>
#   BRIDGE_LAN_IP=<LAN IP of phone>
#   MAIL_HOST=<IMAP server>
#   SMTP_HOST=<SMTP server>
#   MAIL_USER=<email address>
#   MAIL_PASS=<email password>
#   PARTNER_EMAIL=<partner email>
#   PHONE_URL=<phone URL e.g. http://x.x.x.x:8432>
#   PHONE_PIN=<mesh PIN>
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

echo "=== FocusLock Homelab Setup ==="

# Install ADB if missing
if ! command -v adb &>/dev/null; then
    echo "Installing android-tools..."
    dnf install -y android-tools 2>/dev/null || apt-get install -y android-tools-adb 2>/dev/null || true
fi

# Create directories
mkdir -p /opt/focuslock
mkdir -p /run/focuslock
chmod 777 /run/focuslock
echo "d /run/focuslock 0777 root root -" > /etc/tmpfiles.d/focuslock.conf

# Copy project files (from parent dir where scripts/python live)
echo "Copying files..."
for f in focuslock-bridge.sh focuslock-mail.py focuslock_mesh.py focuslock_ntfy.py focuslock-desktop.py focuslock-tray.py; do
    [ -f "$PROJECT_DIR/$f" ] && cp "$PROJECT_DIR/$f" /opt/focuslock/
done
# Copy shared config module
[ -f "$PROJECT_DIR/shared/focuslock_config.py" ] && cp "$PROJECT_DIR/shared/focuslock_config.py" /opt/focuslock/
# Copy icon
[ -f "$PROJECT_DIR/icons/collar-icon.png" ] && cp "$PROJECT_DIR/icons/collar-icon.png" /opt/focuslock/
# Copy Lion's Share public key
[ -f "$PROJECT_DIR/lion_pubkey.pem" ] && cp "$PROJECT_DIR/lion_pubkey.pem" /opt/focuslock/
# Copy web UI
if [ -d "$PROJECT_DIR/web" ]; then
    mkdir -p /opt/focuslock/web
    cp "$PROJECT_DIR/web/index.html" /opt/focuslock/web/ 2>/dev/null || true
fi
# Copy installer scripts (from this dir)
for f in install-desktop-collar.sh install-standing-orders.sh; do
    [ -f "$SCRIPT_DIR/$f" ] && cp "$SCRIPT_DIR/$f" /opt/focuslock/
done
# Copy service/desktop files from services/ dir
for f in focuslock-desktop.service focuslock-desktop.desktop; do
    [ -f "$PROJECT_DIR/services/$f" ] && cp "$PROJECT_DIR/services/$f" /opt/focuslock/ || true
done
# Copy docs
[ -f "$PROJECT_DIR/docs/PRICE-LIST.md" ] && cp "$PROJECT_DIR/docs/PRICE-LIST.md" /opt/focuslock/ || true
chmod +x /opt/focuslock/focuslock-bridge.sh
chmod +x /opt/focuslock/install-desktop-collar.sh 2>/dev/null || true
chmod +x /opt/focuslock/install-standing-orders.sh 2>/dev/null || true

# Copy APKs if present
for apk in focuslock.apk focusctl.apk bunnytasker.apk; do
    [ -f "$PROJECT_DIR/apks/$apk" ] && cp "$PROJECT_DIR/apks/$apk" /opt/focuslock/ || \
    [ -f "$SCRIPT_DIR/$apk" ] && cp "$SCRIPT_DIR/$apk" /opt/focuslock/ || true
done

# Copy ADB keys
REAL_HOME="$(eval echo ~${SUDO_USER:-$USER})"
if [ -d "$REAL_HOME/.android" ]; then
    cp -r "$REAL_HOME/.android" /opt/focuslock/.android 2>/dev/null || true
    echo "ADB keys copied."
fi

# Copy Claude Code enforcement files to both user home AND service home
REAL_HOME="$(eval echo ~${SUDO_USER:-$USER})"
mkdir -p "$REAL_HOME/.claude"
mkdir -p /opt/focuslock/.claude
for cf in CLAUDE.md settings.json; do
    [ -f "$REAL_HOME/.claude/$cf" ] && cp "$REAL_HOME/.claude/$cf" /opt/focuslock/.claude/$cf 2>/dev/null || true
done

# Install standing orders on homelab too (for /standing-orders endpoint + sync source)
if [ -f "$SCRIPT_DIR/install-standing-orders.sh" ]; then
    sudo -u "${SUDO_USER:-$USER}" bash "$SCRIPT_DIR/install-standing-orders.sh"
fi

# Create config.env template if it doesn't exist
if [ ! -f /opt/focuslock/config.env ]; then
    cat > /opt/focuslock/config.env << 'ENVEOF'
# FocusLock Homelab Configuration
# Fill in all values before starting services

# Bridge — phone connection IPs
BRIDGE_TAILSCALE_IP=
BRIDGE_LAN_IP=

# Mail service
MAIL_HOST=
SMTP_HOST=
MAIL_USER=
MAIL_PASS=
PARTNER_EMAIL=
PHONE_URL=
PHONE_PIN=

# Shared
ANDROID_ADB_SERVER_PORT=15037
HOME=/opt/focuslock
PATH=/usr/local/bin:/usr/bin:/usr/sbin
ENVEOF
    chmod 600 /opt/focuslock/config.env
    echo ""
    echo "  CREATED /opt/focuslock/config.env — edit it before starting services!"
    echo ""
fi

# Install bridge service
cat > /etc/systemd/system/focuslock-bridge.service << 'EOF'
[Unit]
Description=FocusLock Bridge
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/bash -c '/opt/focuslock/focuslock-bridge.sh $BRIDGE_TAILSCALE_IP $BRIDGE_LAN_IP'
Restart=always
RestartSec=10
EnvironmentFile=/opt/focuslock/config.env
WorkingDirectory=/opt/focuslock

[Install]
WantedBy=multi-user.target
EOF

# Install mail service (handles webhooks, IMAP payments, desktop heartbeats)
cat > /etc/systemd/system/focuslock-mail.service << 'EOF'
[Unit]
Description=FocusLock Mail + Webhook Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 /opt/focuslock/focuslock-mail.py
Restart=always
RestartSec=10
WorkingDirectory=/opt/focuslock
EnvironmentFile=/opt/focuslock/config.env

[Install]
WantedBy=multi-user.target
EOF

# Reload and start
systemctl daemon-reload
systemctl enable focuslock-bridge focuslock-mail
systemctl restart focuslock-bridge focuslock-mail

echo ""
echo "=== Homelab setup complete ==="
echo ""
echo "  Bridge:    sudo systemctl status focuslock-bridge"
echo "  Mail:      sudo systemctl status focuslock-mail"
echo "  Logs:      sudo journalctl -u focuslock-bridge -u focuslock-mail -f"
echo ""
echo "  Endpoints:"
echo "    GET  /desktop-status      — phone lock state for desktops"
echo "    GET  /standing-orders     — CLAUDE.md for memory sync"
echo "    POST /webhook/*           — phone event webhooks"
echo "    POST /webhook/desktop-heartbeat  — desktop alive signal"
echo "    POST /webhook/desktop-penalty    — apply penalty via ADB"
echo ""
echo "  APKs available at /opt/focuslock/*.apk"
echo "  Desktop installer: /opt/focuslock/install-desktop-collar.sh"
echo ""

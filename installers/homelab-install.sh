#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
#
# FocusLock Homelab Installer (VPS / Debian-Ubuntu edition)
# -----------------------------------------------------------------------------
# Self-contained, idempotent installer for a fresh Debian/Ubuntu VPS acting as
# the homelab relay hub. Unlike installers/homelab-setup.sh this:
#   * installs the correct adb package name for modern Ubuntu ("adb")
#   * copies the WHOLE shared/ module dir (the mail service imports
#     focuslock_adb / _evidence / _http / _llm / _payment / _penalties)
#   * needs no pip (only dep is cryptography, present in the distro)
#
# Run ON the homelab as root, from the staged source dir:
#     sudo bash ~/focuslock-src/installers/homelab-install.sh [DOMAIN] [ACME_EMAIL]
#
# With no args: relay is reachable over LAN/Tailscale only (bind 0.0.0.0:8434).
# With a DOMAIN (e.g. collar.example.com whose DNS points at this host's public
# IP): also installs Caddy as a TLS reverse proxy (auto Let's Encrypt) so apps
# reach the homelab by name over HTTPS, and sets public_url so the relay
# advertises that name during pairing.
#     sudo bash ~/focuslock-src/installers/homelab-install.sh collar.example.com you@example.com
set -euo pipefail

DOMAIN="${1:-}"
ACME_EMAIL="${2:-}"

# ── Locate staged source ───────────────────────────────────────────────────
# This script lives in <SRC>/installers/, so SRC is one level up.
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC="$(cd "$SCRIPT_DIR/.." && pwd)"
DEST=/opt/focuslock

if [ "$(id -u)" -ne 0 ]; then
    echo "ERROR: run as root:  sudo bash $0" >&2
    exit 1
fi
for f in focuslock-mail.py focuslock_mesh.py focuslock_ntfy.py shared/banks.json; do
    if [ ! -f "$SRC/$f" ]; then
        echo "ERROR: staged source incomplete — missing $SRC/$f" >&2
        echo "       rsync the tree to ~/focuslock-src first." >&2
        exit 1
    fi
done

echo "=== FocusLock Homelab Installer ==="
echo "  source: $SRC"
echo "  dest:   $DEST"

# ── 1. ADB (bridge enforcement; harmless if no devices registered) ──────────
if ! command -v adb >/dev/null 2>&1; then
    echo "[1/6] Installing adb ..."
    export DEBIAN_FRONTEND=noninteractive
    apt-get update -qq
    apt-get install -y adb >/dev/null
else
    echo "[1/6] adb already present ($(command -v adb))"
fi

# ── 2. Runtime dep sanity (cryptography ships with the distro) ──────────────
if ! python3 -c "import cryptography" 2>/dev/null; then
    echo "[2/6] Installing python3-cryptography ..."
    apt-get install -y python3-cryptography >/dev/null
else
    echo "[2/6] python3 cryptography OK"
fi

# ── 3. Directories + state dirs ─────────────────────────────────────────────
echo "[3/6] Creating $DEST + state dirs ..."
mkdir -p "$DEST"
# DURABLE relay state — mesh accounts + vault node lists are the only record of
# who is on a mesh; they MUST survive reboots. (The old /run/focuslock tmpfs
# default silently wiped every mesh on boot.) Relay runs as root → 700.
mkdir -p /var/lib/focuslock/meshes /var/lib/focuslock/vaults /var/lib/focuslock/mesh-orders && chmod 700 /var/lib/focuslock
# VOLATILE bridge coordination only (devices.json / controller.json are
# re-written by the ADB bridge each run) — fine to keep on tmpfs.
mkdir -p /run/focuslock && chmod 777 /run/focuslock
echo "d /run/focuslock 0777 root root -" > /etc/tmpfiles.d/focuslock.conf

# ── 4. Copy the application tree ────────────────────────────────────────────
echo "[4/6] Copying application files ..."
install -m 0644 "$SRC/focuslock-mail.py"  "$DEST/focuslock-mail.py"
install -m 0644 "$SRC/focuslock_mesh.py"  "$DEST/focuslock_mesh.py"
install -m 0644 "$SRC/focuslock_ntfy.py"  "$DEST/focuslock_ntfy.py"
[ -f "$SRC/focuslock-bridge.sh" ] && install -m 0755 "$SRC/focuslock-bridge.sh" "$DEST/focuslock-bridge.sh"
rsync -a --delete "$SRC/shared/" "$DEST/shared/"
[ -d "$SRC/web" ] && rsync -a "$SRC/web/" "$DEST/web/"

# ── 5. Config (generated once; never clobbered on re-run) ───────────────────
CONF="$DEST/config.json"
if [ ! -f "$CONF" ]; then
    echo "[5/6] Generating $CONF (fill in operator_mesh_id + mail creds later) ..."
    TOKEN="$(python3 -c 'import secrets;print(secrets.token_urlsafe(32))')"
    TS_IP="$(tailscale ip -4 2>/dev/null | head -1 || true)"
    cat > "$CONF" <<JSON
{
  "_comment": "FocusLock homelab relay config. Restart: systemctl restart focuslock-mail",
  "homelab_port": 8434,
  "mesh_port": 8435,
  "mesh_url": "http://${TS_IP:-127.0.0.1}:8434",
  "admin_token": "$TOKEN",
  "operator_mesh_id": "",
  "pin": "",
  "mail": {
    "imap_host": "",
    "smtp_host": "",
    "user": "",
    "pass": "",
    "partner_email": ""
  }
}
JSON
    chmod 600 "$CONF"
else
    echo "[5/6] $CONF already exists — leaving it untouched"
fi

# If a public domain was given, set public_url + mesh_url in config.json
# (idempotent — works whether config was just created or pre-existed).
if [ -n "$DOMAIN" ]; then
    PUB="https://$DOMAIN"
    python3 - "$CONF" "$PUB" <<'PY'
import json, sys
path, pub = sys.argv[1], sys.argv[2]
with open(path) as f:
    c = json.load(f)
c["public_url"] = pub
c["mesh_url"] = pub
with open(path, "w") as f:
    json.dump(c, f, indent=2)
print(f"      public_url + mesh_url -> {pub}")
PY
fi

# ── 6. systemd units ────────────────────────────────────────────────────────
echo "[6/6] Installing systemd units ..."
cat > /etc/systemd/system/focuslock-mail.service <<EOF
[Unit]
Description=FocusLock Mail + Vault Relay
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $DEST/focuslock-mail.py
Restart=always
RestartSec=10
WorkingDirectory=$DEST
Environment=HOME=$DEST

[Install]
WantedBy=multi-user.target
EOF

# Bridge unit is installed but NOT started — it needs a device registry
# (/run/focuslock/devices.json) and ADB reachability to the phone(s).
cat > /etc/systemd/system/focuslock-bridge.service <<EOF
[Unit]
Description=FocusLock Bridge (ADB device enforcement)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/bin/bash $DEST/focuslock-bridge.sh
Restart=always
RestartSec=10
WorkingDirectory=$DEST
Environment=HOME=$DEST
Environment=ANDROID_ADB_SERVER_PORT=15037

[Install]
WantedBy=multi-user.target
EOF

systemd-tmpfiles --create /etc/tmpfiles.d/focuslock.conf 2>/dev/null || true
systemctl daemon-reload
systemctl enable focuslock-mail >/dev/null 2>&1 || true
systemctl restart focuslock-mail

# ── Optional: public HTTPS via Caddy reverse proxy ──────────────────────────
if [ -n "$DOMAIN" ]; then
    echo "[+] Configuring public HTTPS for $DOMAIN via Caddy ..."
    if ! command -v caddy >/dev/null 2>&1; then
        DEBIAN_FRONTEND=noninteractive apt-get install -y caddy >/dev/null
    fi
    mkdir -p /etc/caddy
    {
        if [ -n "$ACME_EMAIL" ]; then
            printf '{\n    email %s\n}\n\n' "$ACME_EMAIL"
        fi
        printf '%s {\n    reverse_proxy 127.0.0.1:%s\n}\n' "$DOMAIN" "8434"
    } > /etc/caddy/Caddyfile
    systemctl enable caddy >/dev/null 2>&1 || true
    systemctl restart caddy
    # Only touch ufw if it is already active (never risk an SSH lockout by
    # enabling a firewall that would default-deny the current session).
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
        ufw allow 80/tcp  >/dev/null 2>&1 || true
        ufw allow 443/tcp >/dev/null 2>&1 || true
        echo "    ufw: allowed 80,443/tcp"
    fi
fi

sleep 2
echo ""
echo "=== Done ==="
systemctl --no-pager --lines=0 status focuslock-mail | head -4 || true
echo ""
echo "  Relay listening on :8434 (bind 0.0.0.0 — reach it over Tailscale)"
if [ -n "$DOMAIN" ]; then
echo "  Public URL:    https://$DOMAIN  (Caddy TLS -> 127.0.0.1:8434)"
echo "  Verify TLS:    curl -s https://$DOMAIN/version   (first request provisions the cert; give it ~15s)"
fi
echo "  Admin token:   grep admin_token $CONF"
echo "  Logs:          journalctl -u focuslock-mail -f"
echo "  Health:        curl -s http://127.0.0.1:8434/version"
echo ""
echo "  Next (edit $CONF then 'systemctl restart focuslock-mail'):"
echo "    - operator_mesh_id : your mesh id (enables the admin API for it)"
echo "    - mail.*           : IMAP/SMTP creds for payment detection + evidence"
echo ""
echo "  Bridge (optional, phone enforcement) — only if phones are ADB-reachable:"
echo "    write /run/focuslock/devices.json then: systemctl enable --now focuslock-bridge"

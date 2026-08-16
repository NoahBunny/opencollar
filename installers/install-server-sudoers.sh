#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# One-time relay setup so re-enslave-server.sh runs unattended.
#
# Run this ON THE RELAY, once, with sudo. Afterwards every deploy is
# passwordless: the deploy user owns /opt/focuslock and writes it directly, and
# the only privileged step left — restarting focuslock-mail — is covered by a
# two-command sudoers rule.
#
# Usage (on the relay):
#   sudo ./install-server-sudoers.sh              # grants to $SUDO_USER
#   sudo ./install-server-sudoers.sh --user alice
#   sudo ./install-server-sudoers.sh --revert     # undo: root reclaims the dir,
#                                                 # sudoers rule removed
#
# ── What this grants, stated plainly ──
# focuslock-mail.service runs as root (no User=), so anything that can replace
# /opt/focuslock/*.py without a password can run code as root on the next
# restart. That is inherent to unattended deployment, not a flaw in how this is
# written: a root-owned staging helper would be no stronger, since the code it
# installs is what root then executes. Grant this only to the operator's own
# account, on a relay where that account is already trusted, and only if you
# want deploys that don't stop to ask for a password. Anyone who can SSH as
# that user effectively owns the relay from this point on.
#
# What it deliberately does NOT do: config.json (admin_token, mail credentials)
# stays root:root 0600 and is never chowned. That is defence in depth against a
# careless read or a stray backup — not against someone who can already replace
# relay code, who could simply read it from inside the service.
set -euo pipefail

SUDOERS_FILE="/etc/sudoers.d/focuslock-server"
INSTALL_DIR="/opt/focuslock"
STATE_DIR="/var/lib/focuslock"
SERVICE="focuslock-mail"

TARGET_USER=""
REVERT=0
while [ $# -gt 0 ]; do
    case "$1" in
        --user) TARGET_USER="${2:-}"; shift 2 ;;
        --revert) REVERT=1; shift ;;
        -h|--help) sed -n '4,30p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

[ "$(id -u)" -eq 0 ] || { echo "ERROR: run this with sudo, on the relay." >&2; exit 1; }

TARGET_USER="${TARGET_USER:-${SUDO_USER:-}}"
[ -n "$TARGET_USER" ] || { echo "ERROR: no target user (pass --user NAME)." >&2; exit 1; }
id "$TARGET_USER" >/dev/null 2>&1 || { echo "ERROR: no such user: $TARGET_USER" >&2; exit 1; }

if [ "$REVERT" = 1 ]; then
    rm -f "$SUDOERS_FILE"
    chown -R root:root "$INSTALL_DIR"
    echo "Reverted: $INSTALL_DIR is root-owned again, $SUDOERS_FILE removed."
    echo "Deploys will prompt for a password from now on."
    exit 0
fi

[ -d "$INSTALL_DIR" ] || { echo "ERROR: $INSTALL_DIR not found — is this the relay?" >&2; exit 1; }
systemctl list-unit-files "$SERVICE.service" >/dev/null 2>&1 || \
    echo "WARN: $SERVICE.service not found — installing the rule anyway."

SYSTEMCTL="$(command -v systemctl)"
[ -n "$SYSTEMCTL" ] || { echo "ERROR: systemctl not found." >&2; exit 1; }

echo "=== Handing $INSTALL_DIR to $TARGET_USER ==="
chown -R "$TARGET_USER" "$INSTALL_DIR"
chmod 755 "$INSTALL_DIR"
# Secrets stay root-only. Note the caveat in the header: a user who owns the
# directory can still replace these files, they just cannot read the originals.
for secret in "$INSTALL_DIR/config.json" "$INSTALL_DIR"/*.pem; do
    [ -e "$secret" ] || continue
    chown root:root "$secret"
    chmod 600 "$secret"
    echo "  kept root-only: $secret"
done

# Pre-create the durable state dirs so a deploy never needs privilege for them.
# These hold the only server-side record of mesh membership — root-owned, 0700.
echo "=== Ensuring $STATE_DIR ==="
mkdir -p "$STATE_DIR/meshes" "$STATE_DIR/vaults" "$STATE_DIR/mesh-orders"
chown -R root:root "$STATE_DIR"
chmod 700 "$STATE_DIR"

echo "=== Installing $SUDOERS_FILE ==="
TMP_SUDOERS="$(mktemp)"
cat > "$TMP_SUDOERS" <<SUDOERS_EOF
# FocusLock relay deploy — installed by installers/install-server-sudoers.sh
# $TARGET_USER owns $INSTALL_DIR and writes it directly, so the only privileged
# step a deploy needs is bouncing the service. Exact argument forms only: no
# wildcards, so this cannot be widened into "restart anything".
$TARGET_USER ALL=(root) NOPASSWD: $SYSTEMCTL restart $SERVICE
$TARGET_USER ALL=(root) NOPASSWD: $SYSTEMCTL restart $SERVICE.service
$TARGET_USER ALL=(root) NOPASSWD: $SYSTEMCTL is-active $SERVICE
$TARGET_USER ALL=(root) NOPASSWD: $SYSTEMCTL is-active $SERVICE.service
SUDOERS_EOF

# Never install a sudoers file that doesn't parse — a broken one can lock
# every user out of sudo on this host.
if ! visudo -cf "$TMP_SUDOERS" >/dev/null; then
    rm -f "$TMP_SUDOERS"
    echo "ERROR: generated sudoers file failed validation — nothing installed." >&2
    exit 1
fi
install -m 0440 -o root -g root "$TMP_SUDOERS" "$SUDOERS_FILE"
rm -f "$TMP_SUDOERS"

echo
echo "=== Done ==="
echo "  $TARGET_USER now owns $INSTALL_DIR and may restart $SERVICE without a password."
echo "  Everything else still requires a password, and secrets stayed root-only."
echo "  Undo with: sudo $0 --revert"
echo
echo "  Deploys from the workstation are now unattended:"
echo "    bash installers/re-enslave-server.sh"

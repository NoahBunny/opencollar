#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
#
# One-shot installer that pre-configures the desktop collar for a specific
# mesh (mesh_id + mesh_url + vault + ntfy push) and then runs the platform
# installer. Skips the post-consent "where do I enter the homelab info"
# question entirely.
#
# Usage (mesh_id and mesh_url are required — pass via flags or env vars):
#   ./install-mesh.sh --mesh-id <your-mesh-id> --mesh-url https://your.relay.example
#   FOCUSLOCK_MESH_ID=<your-mesh-id> FOCUSLOCK_MESH_URL=https://your.relay.example \
#       ./install-mesh.sh
#   ./install-mesh.sh --mesh-id <your-mesh-id> --mesh-url URL --no-ntfy
#   ./install-mesh.sh --mesh-id <your-mesh-id> --mesh-url URL --reset-keys
#
# Flags:
#   --mesh-id ID    mesh identifier (base64url) issued by your relay; required
#   --mesh-url URL  full https URL of your relay; required
#   --no-ntfy       skip ntfy push subscription (defaults to subscribing)
#   --reset-keys    force a fresh vault keypair (which forces a new register-
#                   node-request and re-approval)
#
# Idempotent: re-running updates config.json + redeploys files. Existing
# vault keypair is preserved (so Lion-approved registration sticks); pass
# --reset-keys to force a fresh keypair.

set -euo pipefail

MESH_ID="${FOCUSLOCK_MESH_ID:-}"
MESH_URL="${FOCUSLOCK_MESH_URL:-}"
NTFY_ENABLED=1
RESET_KEYS=0

usage() {
    sed -n '4,26p' "$0" | sed 's/^# \?//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --mesh-id)   MESH_ID="$2"; shift 2 ;;
        --mesh-url)  MESH_URL="$2"; shift 2 ;;
        --no-ntfy)   NTFY_ENABLED=0; shift ;;
        --reset-keys) RESET_KEYS=1; shift ;;
        -h|--help)   usage; exit 0 ;;
        *) echo "Unknown arg: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -z "$MESH_ID" ] || [ -z "$MESH_URL" ]; then
    echo "error: --mesh-id and --mesh-url are required (or set FOCUSLOCK_MESH_ID + FOCUSLOCK_MESH_URL)" >&2
    usage >&2
    exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

CONFIG_DIR="$HOME/.config/focuslock"
mkdir -p "$CONFIG_DIR"

# Reset vault keypair only if explicitly asked. Default preserves the key so
# Lion's prior approval keeps working — registering a fresh key forces a new
# pending request that needs Lion to approve again.
if [ "$RESET_KEYS" = 1 ]; then
    rm -f "$CONFIG_DIR/node_privkey.pem" "$CONFIG_DIR/node_pubkey.pem"
    rm -f "$CONFIG_DIR/relay_privkey.pem" "$CONFIG_DIR/relay_pubkey.pem"
    echo "Vault keypair reset — daemon will generate fresh keys + post a new register-node-request."
fi

# Write config.json. Truncates any prior config — this script's whole point is
# to be authoritative about which mesh this device joins.
NTFY_LINE=""
if [ "$NTFY_ENABLED" = 1 ]; then
    NTFY_LINE='
  "ntfy_enabled": true,
  "ntfy_server": "https://ntfy.sh",'
fi

cat > "$CONFIG_DIR/config.json" <<EOF
{
  "mesh_url": "$MESH_URL",
  "mesh_id": "$MESH_ID",
  "vault_mode": true,$NTFY_LINE
  "mesh_port": 8435,
  "poll_interval": 5
}
EOF
chmod 600 "$CONFIG_DIR/config.json"
echo "Wrote $CONFIG_DIR/config.json (mesh=$MESH_ID via $MESH_URL)"

# Hand off to the platform installer. install-desktop-collar.sh sees the
# config.json now exists with mesh_url set and skips the homelab prompt
# (consumer / mesh-relay path).
exec bash "$SCRIPT_DIR/install-desktop-collar.sh" "$@"

#!/usr/bin/env bash
# Start a staging FocusLock relay on 127.0.0.1:8435 against isolated state.
#
# Prerequisites:
#   1. staging/config.json exists (copy from staging/config.json.template and fill in).
#   2. staging/lion_privkey.pem + lion_pubkey.pem exist (throwaway test keypair).
#   3. Python + cryptography installed (same as production server).
#
# See docs/STAGING.md for full setup instructions.

set -eu

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
STAGING_STATE_DIR="${FOCUSLOCK_STAGING_STATE_DIR:-/tmp/focuslock-staging}"

# ── Sanity checks ─────────────────────────────────────────────

if [ ! -f "$SCRIPT_DIR/config.json" ]; then
    echo "ERROR: $SCRIPT_DIR/config.json not found."
    echo "       Copy staging/config.json.template → staging/config.json and fill in secrets."
    echo "       See docs/STAGING.md."
    exit 1
fi

if [ ! -f "$SCRIPT_DIR/lion_privkey.pem" ]; then
    echo "ERROR: $SCRIPT_DIR/lion_privkey.pem not found."
    echo "       Generate a throwaway Lion keypair (see docs/STAGING.md step 4)."
    exit 1
fi

# Guard against prod secret reuse — staging mesh_id must not match any prod-like value
mesh_id=$(python3 -c "import json,sys; print(json.load(open('$SCRIPT_DIR/config.json')).get('mesh_id', ''))")
if [ -z "$mesh_id" ] || [ "$mesh_id" = "REPLACE_WITH_RANDOM_BASE64URL_12_BYTES" ]; then
    echo "ERROR: mesh_id in $SCRIPT_DIR/config.json is the template placeholder."
    echo "       Generate a fresh one (see docs/STAGING.md step 2)."
    exit 1
fi

# ── Prepare isolated state dir ────────────────────────────────

mkdir -p "$STAGING_STATE_DIR"
echo "Staging state dir: $STAGING_STATE_DIR"

# ── Env isolation ─────────────────────────────────────────────
# Override config path so focuslock-mail.py reads staging/config.json.
# Redirect runtime-state paths to the staging dir.

export FOCUSLOCK_CONFIG="$SCRIPT_DIR/config.json"
export FOCUSLOCK_STATE_DIR="$STAGING_STATE_DIR"  # respected by future refactors; for now /run/focuslock is hardcoded in a few places — see docs/STAGING.md "Gotchas"

# Unset any production environment variables that might leak in
unset FOCUSLOCK_ADMIN_TOKEN FOCUSLOCK_OPERATOR_MESH_ID FOCUSLOCK_HOMELAB_URL

# ── Warn if /run/focuslock is prod-owned ──────────────────────
# Current focuslock-mail.py hardcodes /run/focuslock paths for some state.
# Ideally these would respect FOCUSLOCK_STATE_DIR; until then, warn the operator
# so they know the staging run may read/write production state files.

if [ -d /run/focuslock ] && [ -n "$(ls -A /run/focuslock 2>/dev/null)" ]; then
    echo ""
    echo "WARNING: /run/focuslock is non-empty and is hardcoded in a few places."
    echo "         A staging run may touch files in there. Consider:"
    echo "         1. sudo mv /run/focuslock /run/focuslock.prod"
    echo "         2. Restore after staging: sudo mv /run/focuslock.prod /run/focuslock"
    echo ""
    read -r -p "Continue anyway? (y/N) " ans
    [ "$ans" = "y" ] || [ "$ans" = "Y" ] || { echo "Aborted."; exit 1; }
fi

# ── Launch ────────────────────────────────────────────────────

cd "$REPO_ROOT"
echo "Starting staging relay on 127.0.0.1:8435 (Ctrl-C to stop)..."
exec python3 focuslock-mail.py

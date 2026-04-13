#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Re-enslave the homelab server (focuslock-mail.py + vault + icons).
# Idempotent: only redeploys files that have changed, only restarts the service
# if focuslock-mail.py changed. Verifies endpoints post-deploy.
#
# Usage:
#   ./re-enslave-server.sh                # Deploy everything that changed
#   ./re-enslave-server.sh --force-restart  # Always restart the service
#   ./re-enslave-server.sh --dry-run      # Print what would change
#
# Requires: ssh + passwordless sudo on the homelab as $USER (or $DEPLOY_USER).
# Set FOCUSLOCK_HOMELAB_SSH=hostname or FOCUSLOCK_HOMELAB_HOST=tailscale-name
# in your environment, or in ~/.config/focuslock/re-enslave.config.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RE_LOG_PREFIX="server"
# shellcheck source=re-enslave-lib.sh
source "$SCRIPT_DIR/re-enslave-lib.sh"

DRY_RUN=0
FORCE_RESTART=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --force-restart) FORCE_RESTART=1 ;;
        -h|--help)
            sed -n '4,16p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) fail "Unknown arg: $arg" ;;
    esac
done

check_paywall
discover_paths
load_config

DEPLOY_USER="${DEPLOY_USER:-$USER}"
HOMELAB_SSH=$(resolve_homelab_ssh) || \
    fail "Set FOCUSLOCK_HOMELAB_SSH or FOCUSLOCK_HOMELAB_HOST to reach the homelab."

section "Re-enslave server: $HOMELAB_SSH"
log "Source root: $LS"
log "User on remote: $DEPLOY_USER"
[ "$DRY_RUN" = 1 ] && log "DRY RUN — no changes will be made"

# Reachability probe (10s)
if ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$DEPLOY_USER@$HOMELAB_SSH" 'echo ok' >/dev/null 2>&1; then
    fail "SSH to $DEPLOY_USER@$HOMELAB_SSH failed."
fi

# Build a list of (local_path, remote_path) tuples for files we deploy.
declare -a DEPLOY_PAIRS=()
for f in "${SERVER_FILES[@]}"; do
    if [ -f "$LS/$f" ]; then
        DEPLOY_PAIRS+=("$LS/$f|/opt/focuslock/$f")
    else
        warn "Source missing: $LS/$f (skipping)"
    fi
done
# Icon (canonical lion icon — fixed 2026-04-07e)
if [ -f "$ICONS/$SERVER_ICON" ]; then
    DEPLOY_PAIRS+=("$ICONS/$SERVER_ICON|/opt/focuslock/$SERVER_ICON")
fi
# Web UI (relay HTML pages + JS assets)
for wf in index.html signup.html cost.html trust.html qrcode.min.js; do
    if [ -f "$LS/web/$wf" ]; then
        DEPLOY_PAIRS+=("$LS/web/$wf|/opt/focuslock/web/$wf")
    fi
done
# Shared Python modules
for src in "$LS"/shared/focuslock_*.py; do
    [ -f "$src" ] || continue
    # Deploy to both root and shared/ — server imports from shared/ but some
    # modules are also imported at top level
    DEPLOY_PAIRS+=("$src|/opt/focuslock/$(basename "$src")")
    DEPLOY_PAIRS+=("$src|/opt/focuslock/shared/$(basename "$src")")
done
# Banks file (used by payment detection)
if [ -f "$LS/shared/banks.json" ]; then
    DEPLOY_PAIRS+=("$LS/shared/banks.json|/opt/focuslock/shared/banks.json")
fi
# Latest APKs (so the bridge / homelab has them staged for sideload)
for apk in "$TARGET_SLAVE_APK" "$TARGET_CONTROLLER_APK" "$TARGET_COMPANION_APK"; do
    for cand in "$HOME/Desktop/$apk" "$APKS/$apk"; do
        if [ -f "$cand" ]; then
            DEPLOY_PAIRS+=("$cand|/opt/focuslock/$apk")
            break
        fi
    done
done

# Compute which files actually need pushing by hashing both sides
log "Comparing ${#DEPLOY_PAIRS[@]} candidate files…"
declare -a PUSH_LIST=()
NEEDS_RESTART=0

# Get all remote hashes in one ssh round-trip — much faster than per-file.
# Note: the homelab login shell may not be bash (e.g. fish), so we wrap the
# remote command in `bash -c` to keep the path-list parsing portable. We also
# collapse newlines to spaces so the whole thing is one argv array.
remote_paths=$(printf '%s\n' "${DEPLOY_PAIRS[@]}" | awk -F'|' '{print $2}' | tr '\n' ' ')
remote_hashes=$(ssh -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" \
    bash -c "'sudo md5sum $remote_paths 2>/dev/null || true'")

for pair in "${DEPLOY_PAIRS[@]}"; do
    local_path="${pair%%|*}"
    remote_path="${pair##*|}"
    local_hash=$(md5sum "$local_path" | awk '{print $1}')
    remote_hash=$(printf '%s\n' "$remote_hashes" | awk -v p="$remote_path" '$2==p {print $1; exit}')
    if [ "$local_hash" != "$remote_hash" ]; then
        PUSH_LIST+=("$pair")
        log "  CHANGED: $(basename "$local_path") (local=$local_hash remote=${remote_hash:-MISSING})"
        # Only focuslock-mail.py changes trigger an automatic service restart
        if [ "$(basename "$local_path")" = "focuslock-mail.py" ]; then
            NEEDS_RESTART=1
        fi
    fi
done

if [ "${#PUSH_LIST[@]}" -eq 0 ]; then
    log "Everything already up to date."
else
    log "${#PUSH_LIST[@]} file(s) to push."
fi

if [ "$DRY_RUN" = 1 ]; then
    log "Dry run — exiting before push."
    exit 0
fi

# Push files via scp + sudo cp (the homelab paths are root-owned)
if [ "${#PUSH_LIST[@]}" -gt 0 ]; then
    section "Pushing files"
    TMPDIR_REMOTE=$(ssh -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" 'mktemp -d')
    for pair in "${PUSH_LIST[@]}"; do
        local_path="${pair%%|*}"
        remote_path="${pair##*|}"
        bn=$(basename "$local_path")
        scp -o ConnectTimeout=10 -q "$local_path" "$DEPLOY_USER@$HOMELAB_SSH:$TMPDIR_REMOTE/$bn"
        ssh -t -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" \
            "sudo install -D -m 644 $TMPDIR_REMOTE/$bn '$remote_path'"
        log "  pushed: $bn → $remote_path"
    done
    ssh -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" "rm -rf $TMPDIR_REMOTE"
fi

# Ensure runtime dirs exist (vault store + meshes + per-mesh orders)
ssh -t -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" \
    'sudo mkdir -p /run/focuslock/meshes /run/focuslock/vaults /run/focuslock/mesh-orders && sudo chmod 755 /run/focuslock/meshes /run/focuslock/vaults /run/focuslock/mesh-orders' || \
    warn "Could not create /run/focuslock dirs"

# Write git commit hash for /version transparency (P3)
GIT_COMMIT=$(git -C "$LS" rev-parse HEAD 2>/dev/null || echo "")
if [ -n "$GIT_COMMIT" ]; then
    echo "$GIT_COMMIT" | ssh -t -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" \
        "sudo tee /opt/focuslock/.git_commit > /dev/null"
    log "  git commit: $GIT_COMMIT"
fi

# Restart service if focuslock-mail.py changed (or --force-restart)
if [ "$NEEDS_RESTART" = 1 ] || [ "$FORCE_RESTART" = 1 ]; then
    section "Restarting focuslock-mail.service"
    ssh -t -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" \
        'sudo systemctl restart focuslock-mail && sleep 3 && sudo systemctl is-active focuslock-mail' \
        || fail "Service failed to come back up — check journalctl -u focuslock-mail"
    log "Service restarted clean."
else
    log "No service restart needed."
fi

# Post-deploy verification: standing-orders + vault since 0 + journal sanity
section "Verifying server health"
MESH_URL="${FOCUSLOCK_MESH_URL:?FOCUSLOCK_MESH_URL must be set in ~/.config/focuslock/re-enslave.config}"
http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$MESH_URL/standing-orders" || echo "000")
if [ "$http_code" = "200" ]; then
    log "  /standing-orders: 200 OK"
else
    warn "  /standing-orders: HTTP $http_code"
fi

# Check journal for errors in the last 2 minutes
err_count=$(ssh -t -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" \
    'sudo journalctl -u focuslock-mail --since "2 minutes ago" --no-pager 2>/dev/null | grep -ciE "error|exception|traceback" || true' \
    2>/dev/null | tr -d '\r')
if [ "${err_count:-0}" -gt 0 ]; then
    warn "  $err_count error/exception line(s) in last 2 min — investigate with: journalctl -u focuslock-mail --since '2 minutes ago'"
else
    log "  Journal: clean (no errors in last 2 min)"
fi

section "Server re-enslave complete"

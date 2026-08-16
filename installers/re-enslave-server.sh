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

# Can the privileged half actually run? If the homelab's sudo wants a password
# and this shell has no terminal, ssh won't allocate a PTY, sudo aborts with
# "a terminal is required to authenticate", and the deploy dies *after* staging
# files — previously reported as "check journalctl", which sends the operator
# to look at a service that never restarted. Find out first, and say the useful
# thing instead.
if [ "$DRY_RUN" != 1 ] \
   && ! ssh -o ConnectTimeout=10 -o BatchMode=yes "$DEPLOY_USER@$HOMELAB_SSH" 'sudo -n true' 2>/dev/null \
   && [ ! -t 0 ]; then
    fail "sudo on $HOMELAB_SSH needs a password, but this shell has no terminal for it to prompt on.
       Run this from an interactive terminal window, or grant $DEPLOY_USER passwordless
       sudo on the homelab. Nothing was changed."
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
# No sudo here: /opt/focuslock ships 0644 root-owned, so a plain md5sum reads
# it fine — and this probe runs without a TTY, where a sudo password prompt
# would fail silently and make every file look MISSING (a full re-push every
# run). Anything genuinely unreadable still falls through to MISSING → pushed.
remote_hashes=$(ssh -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" \
    bash -c "'md5sum $remote_paths 2>/dev/null || true'")

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

# Stage every changed file, then do all the privileged work in ONE remote sudo
# session. Previously each file was its own `ssh -t … sudo install`, which on a
# homelab whose sudo asks for a password meant a prompt per file (sudo's
# timestamps are per-TTY, and every ssh -t is a new TTY). One session = one
# prompt, and the install/restart can no longer land half-applied because the
# operator gave up typing passwords midway.
GIT_COMMIT=$(git -C "$LS" rev-parse HEAD 2>/dev/null || echo "")
TMPDIR_REMOTE=$(ssh -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" 'mktemp -d')

if [ "${#PUSH_LIST[@]}" -gt 0 ]; then
    section "Staging files"
    for pair in "${PUSH_LIST[@]}"; do
        local_path="${pair%%|*}"
        bn=$(basename "$local_path")
        # Staged under a per-target name so two DEPLOY_PAIRS sharing a basename
        # (shared modules land in both / and /shared) don't collide.
        scp -o ConnectTimeout=10 -q "$local_path" "$DEPLOY_USER@$HOMELAB_SSH:$TMPDIR_REMOTE/$bn"
    done
    log "  staged ${#PUSH_LIST[@]} file(s) in $TMPDIR_REMOTE"
fi

# Build the privileged half as a script, so the whole deploy is one `sudo bash`.
REMOTE_SCRIPT=$(mktemp)
{
    echo 'set -e'
    for pair in "${PUSH_LIST[@]}"; do
        local_path="${pair%%|*}"
        remote_path="${pair##*|}"
        bn=$(basename "$local_path")
        printf 'install -D -m 644 %q %q\n' "$TMPDIR_REMOTE/$bn" "$remote_path"
        printf 'echo "  installed: %s"\n' "$remote_path"
    done
    # DURABLE state dirs (vault store + mesh accounts + per-mesh orders). These
    # hold the only server-side record of mesh membership, so they live on
    # persistent disk — NOT /run (tmpfs), which wiped every mesh on reboot.
    echo 'mkdir -p /var/lib/focuslock/meshes /var/lib/focuslock/vaults /var/lib/focuslock/mesh-orders'
    echo 'chmod 700 /var/lib/focuslock'
    # Git commit hash for /version transparency (P3)
    if [ -n "$GIT_COMMIT" ]; then
        printf 'printf %%s %q > /opt/focuslock/.git_commit\n' "$GIT_COMMIT"
    fi
    if [ "$NEEDS_RESTART" = 1 ] || [ "$FORCE_RESTART" = 1 ]; then
        echo 'echo "  restarting focuslock-mail.service"'
        echo 'systemctl restart focuslock-mail'
        echo 'sleep 3'
        echo 'systemctl is-active focuslock-mail'
    else
        echo 'echo "  no service restart needed"'
    fi
} > "$REMOTE_SCRIPT"

# Ship the script and run it by path. NOT `sudo bash -s < script`: redirecting
# stdin makes ssh skip TTY allocation ("Pseudo-terminal will not be allocated
# because stdin is not a terminal"), and sudo then has nowhere to prompt —
# fatal on a homelab whose sudo asks for a password. Forcing -tt instead would
# hand sudo the script text as its password prompt input, which is worse. With
# the script already on disk, stdin stays the operator's terminal.
scp -o ConnectTimeout=10 -q "$REMOTE_SCRIPT" "$DEPLOY_USER@$HOMELAB_SSH:$TMPDIR_REMOTE/_apply.sh"
rm -f "$REMOTE_SCRIPT"

section "Applying (one sudo session — enter the homelab password if prompted)"
if ! ssh -t -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" "sudo bash '$TMPDIR_REMOTE/_apply.sh'"; then
    ssh -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" "rm -rf $TMPDIR_REMOTE" || true
    fail "Remote apply failed. If sudo could not authenticate, re-run from an interactive
       terminal; otherwise check journalctl -u focuslock-mail on $HOMELAB_SSH."
fi
ssh -o ConnectTimeout=10 "$DEPLOY_USER@$HOMELAB_SSH" "rm -rf $TMPDIR_REMOTE" || true
[ -n "$GIT_COMMIT" ] && log "  git commit: $GIT_COMMIT"

# Post-deploy verification: standing-orders + vault since 0 + journal sanity
# Audit 2026-04-27 H-1 (remainder): /standing-orders requires admin_token.
# Operator's deploy host has FOCUSLOCK_ADMIN_TOKEN in scope; if not set,
# fall back to /mesh/ping which is unauthenticated by design (just checks
# the relay is reachable + responding).
section "Verifying server health"
MESH_URL="${FOCUSLOCK_MESH_URL:?FOCUSLOCK_MESH_URL must be set in ~/.config/focuslock/re-enslave.config}"
if [ -n "${FOCUSLOCK_ADMIN_TOKEN:-}" ]; then
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 \
        -H "Authorization: Bearer $FOCUSLOCK_ADMIN_TOKEN" \
        "$MESH_URL/standing-orders" || echo "000")
    if [ "$http_code" = "200" ]; then
        log "  /standing-orders: 200 OK"
    else
        warn "  /standing-orders: HTTP $http_code"
    fi
else
    http_code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 10 "$MESH_URL/mesh/ping" || echo "000")
    if [ "$http_code" = "200" ]; then
        log "  /mesh/ping: 200 OK (FOCUSLOCK_ADMIN_TOKEN not set, skipping authed health check)"
    else
        warn "  /mesh/ping: HTTP $http_code"
    fi
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

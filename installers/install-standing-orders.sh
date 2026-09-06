#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Install The Collar standing orders + enforcement hook for Claude Code
# Pulls CLAUDE.md + settings.json from homelab mail service (HTTP, no SSH needed)
# Also installs systemd sync units with tamper detection
#
# No-ops on a machine no Lion has claimed — the collar's interim marching
# orders own ~/.claude/CLAUDE.md until then (see the pairing gate below).
#
# Environment:
#   FOCUSLOCK_HOMELAB       — homelab URL (e.g. http://x.x.x.x:8434)
#   FOCUSLOCK_MESH_URL      — relay URL, used when there is no separate homelab
#   FOCUSLOCK_HOMELAB_HOST  — Tailscale hostname for auto-discovery
#   FOCUSLOCK_HOMELAB_SSH   — SSH address for homelab (fallback for SCP)
#   FOCUSLOCK_ADMIN_TOKEN   — bearer for the admin-gated /standing-orders + /settings
set -e

CLAUDE_DIR="$HOME/.claude"

# ── Pairing gate ──
# Standing orders belong to a Lion who has claimed this machine. Until Their
# key is on file, ~/.claude/CLAUDE.md is owned by the desktop collar's interim
# marching orders (shared/focuslock_unpaired_orders.py) — and the sync units
# installed below would fight it: the .path watcher fires on every collar write,
# re-pulls, and the collar rewrites on its next tick. There is also nothing to
# pull, since an unclaimed machine has no Lion serving it orders.
LION_PUBKEY_FILE="$HOME/.config/focuslock/lion_pubkey.pem"
if [ ! -s "$LION_PUBKEY_FILE" ]; then
    echo "Unpaired — no Lion key at $LION_PUBKEY_FILE."
    echo "  Skipping standing-orders sync: the collar's interim marching orders own"
    echo "  ~/.claude/CLAUDE.md until your Lion claims this machine."
    exit 0
fi

# Authenticated pulls when the operator has the token in scope. /standing-orders
# and /settings are admin-gated (audit 2026-04-27 H-1), so without this the HTTP
# path always 401s and silently degrades to the SSH fallback.
CURL_AUTH=()
if [ -n "${FOCUSLOCK_ADMIN_TOKEN:-}" ]; then
    CURL_AUTH=(-H "Authorization: Bearer $FOCUSLOCK_ADMIN_TOKEN")
fi

# Resolve homelab URL: env var > relay URL > Tailscale > error
HOMELAB_URL=""
HOMELAB_HOSTNAME="${FOCUSLOCK_HOMELAB_HOST:-}"
if [ -n "${FOCUSLOCK_HOMELAB:-}" ]; then
    HOMELAB_URL="$FOCUSLOCK_HOMELAB"
elif [ -n "${FOCUSLOCK_MESH_URL:-}" ]; then
    # Relay-only deployments (no separate homelab box) serve /standing-orders
    # from the relay itself, and FOCUSLOCK_MESH_URL is the one address every
    # re-enslave script already requires — so don't demand a second variable
    # naming the same endpoint.
    HOMELAB_URL="$FOCUSLOCK_MESH_URL"
elif command -v tailscale &>/dev/null && [ -n "$HOMELAB_HOSTNAME" ]; then
    HOMELAB_TS=$(tailscale status 2>/dev/null | grep "$HOMELAB_HOSTNAME" | awk '{print $1}')
    if [ -n "$HOMELAB_TS" ]; then
        HOMELAB_URL="http://$HOMELAB_TS:8434"
    fi
fi
if [ -z "$HOMELAB_URL" ]; then
    echo "ERROR: Set FOCUSLOCK_HOMELAB or FOCUSLOCK_HOMELAB_HOST to reach homelab."
    exit 1
fi

mkdir -p "$CLAUDE_DIR"
BACKUP_DIR="$HOME/.config/focuslock/claude-backup"
FETCHED_ANY=0

# Replace a Claude config file only with content we actually got, and never
# without keeping a copy of what was there. `curl -o target` truncates the
# target before it knows the response code, so a 401 from an admin-gated
# endpoint used to be able to leave a good file empty.
install_fetched() {
    local src="$1" dest="$2" label="$3"
    [ -s "$src" ] || { rm -f "$src"; return 1; }
    if [ -f "$dest" ] && ! cmp -s "$src" "$dest"; then
        mkdir -p "$BACKUP_DIR"
        cp -p "$dest" "$BACKUP_DIR/$(basename "$dest").$(date +%s)"
        echo "    previous $label backed up to $BACKUP_DIR"
    fi
    mv "$src" "$dest"
    chmod 644 "$dest"
    FETCHED_ANY=1
    echo "  $label installed"
    return 0
}

# settings.json is not a preferences file on a collared machine — it is where
# the collar's enforcement hooks live (paywall gate, tamper scan, pronoun check,
# bash audit). Both fetch paths below can hand back something that has none:
# the SSH fallback copies whatever ~/.claude/settings.json the operator happens
# to have on the homelab box, which on a relay-only box is typically a bare
# {"theme": "auto"}. Installing that strips every hook from the machine being
# collared, and a backup does not save it — nothing tells anyone the gate
# stopped running. Observed live on 2026-08-17: a re-enslave replaced a 3174-byte
# hooked settings.json with 22 bytes of theme preference.
settings_is_safe() {
    local src="$1" dest="$2"
    if command -v python3 >/dev/null && ! python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$src" 2>/dev/null; then
        echo "  settings.json: fetched copy is not valid JSON — keeping the local one"
        return 1
    fi
    if [ -f "$dest" ] && grep -q '"hooks"' "$dest" && ! grep -q '"hooks"' "$src"; then
        echo "  settings.json: fetched copy carries no hooks but this machine's does —"
        echo "                 refusing to install it (that would disarm the collar)"
        return 1
    fi
    return 0
}

# Pull latest from homelab via HTTP
echo "Pulling standing orders from homelab..."
_tmp_md="$(mktemp)"
if curl -sf "${CURL_AUTH[@]}" -o "$_tmp_md" --connect-timeout 5 "$HOMELAB_URL/standing-orders" \
   && install_fetched "$_tmp_md" "$CLAUDE_DIR/CLAUDE.md" "CLAUDE.md"; then
    :
else
    rm -f "$_tmp_md"
    echo "  CLAUDE.md: HTTP failed, trying SSH..."
    HOMELAB_SSH="${FOCUSLOCK_HOMELAB_SSH:-${HOMELAB_TS:-}}"
    if [ -n "$HOMELAB_SSH" ]; then
        _tmp_md="$(mktemp)"
        _scp_err="$(mktemp)"
        if scp -o ConnectTimeout=5 "$USER@$HOMELAB_SSH:$HOME/.claude/CLAUDE.md" "$_tmp_md" 2>"$_scp_err"; then
            install_fetched "$_tmp_md" "$CLAUDE_DIR/CLAUDE.md" "CLAUDE.md" || rm -f "$_tmp_md"
        elif grep -qi 'no such file' "$_scp_err"; then
            # Reached the box fine; it simply has no ~/.claude/CLAUDE.md — which
            # is normal for a relay VPS that runs focuslock-mail and nothing
            # else. Saying "could not reach homelab" here sent at least one
            # session hunting a network fault that did not exist.
            rm -f "$_tmp_md"
            echo "  CLAUDE.md: $HOMELAB_SSH is reachable but has no ~/.claude/CLAUDE.md."
            echo "             That is expected on a bare relay. A running collar fetches"
            echo "             standing orders itself over the node-signed"
            echo "             /vault/{mesh}/standing-orders route; this SSH pull only"
            echo "             matters on a machine with no collar."
        else
            rm -f "$_tmp_md"
            echo "  ERROR: Could not reach homelab ($(head -1 "$_scp_err" | cut -c1-70))"
        fi
        rm -f "$_scp_err"
    else
        echo "  ERROR: No SSH fallback configured (set FOCUSLOCK_HOMELAB_SSH)"
    fi
fi

_tmp_set="$(mktemp)"
if curl -sf "${CURL_AUTH[@]}" -o "$_tmp_set" --connect-timeout 5 "$HOMELAB_URL/settings" \
   && settings_is_safe "$_tmp_set" "$CLAUDE_DIR/settings.json" \
   && install_fetched "$_tmp_set" "$CLAUDE_DIR/settings.json" "settings.json"; then
    :
else
    rm -f "$_tmp_set"
    echo "  settings.json: HTTP failed, trying SSH..."
    HOMELAB_SSH="${FOCUSLOCK_HOMELAB_SSH:-${HOMELAB_TS:-}}"
    if [ -n "$HOMELAB_SSH" ]; then
        # NB: this pulls the operator's own ~/.claude/settings.json off the
        # homelab box — whatever they happen to have, not a curated stub. Kept
        # for continuity, but it now backs up the local file first.
        _tmp_set="$(mktemp)"
        if scp -o ConnectTimeout=5 "$USER@$HOMELAB_SSH:$HOME/.claude/settings.json" "$_tmp_set" 2>/dev/null; then
            if settings_is_safe "$_tmp_set" "$CLAUDE_DIR/settings.json"; then
                install_fetched "$_tmp_set" "$CLAUDE_DIR/settings.json" "settings.json" || rm -f "$_tmp_set"
            else
                rm -f "$_tmp_set"
            fi
        else
            rm -f "$_tmp_set"
            echo "  settings.json: not available (will use defaults)"
        fi
    else
        echo "  settings.json: not available (will use defaults)"
    fi
fi

# Install sync script (pull from homelab — it has the latest with memory sync)
echo "Pulling sync script from homelab..."
HOMELAB_SSH="${FOCUSLOCK_HOMELAB_SSH:-${HOMELAB_TS:-}}"
if [ -n "$HOMELAB_SSH" ] && scp -o ConnectTimeout=5 "$USER@$HOMELAB_SSH:$HOME/.claude/sync-standing-orders.sh" "$CLAUDE_DIR/sync-standing-orders.sh" 2>/dev/null; then
    chmod +x "$CLAUDE_DIR/sync-standing-orders.sh"
    echo "  sync-standing-orders.sh installed"
else
    echo "  WARNING: Could not pull sync script from homelab (set FOCUSLOCK_HOMELAB_SSH)"
fi

# Install systemd units (always overwrite)
SYSTEMD_DIR="$HOME/.config/systemd/user"
mkdir -p "$SYSTEMD_DIR"

cat > "$SYSTEMD_DIR/claude-standing-orders-sync.service" << 'EOF'
[Unit]
Description=Sync Claude Code standing orders + enforcement hooks

[Service]
Type=oneshot
ExecStart=%h/.claude/sync-standing-orders.sh
EOF

cat > "$SYSTEMD_DIR/claude-standing-orders-sync.timer" << 'EOF'
[Unit]
Description=Sync standing orders every 5 minutes

[Timer]
OnBootSec=30
OnUnitActiveSec=5min

[Install]
WantedBy=timers.target
EOF

cat > "$SYSTEMD_DIR/claude-standing-orders-sync.path" << 'EOF'
[Unit]
Description=Watch Claude config files for changes and sync immediately

[Path]
PathChanged=%h/.claude/CLAUDE.md
PathChanged=%h/.claude/settings.json
# Memory dirs are per-user/per-path — sync script handles discovery
Unit=claude-standing-orders-sync.service

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
# Only arm them if the thing they run exists. Enabling units whose ExecStart is
# missing gives you a timer that fails 203/EXEC every 5 minutes and a .path
# watcher that fires on every write to CLAUDE.md to fail again — noise that
# looks like a collar malfunction. If the sync script never arrived, leave the
# units installed but inert, and disable any armed from an earlier run.
if [ -x "$CLAUDE_DIR/sync-standing-orders.sh" ]; then
    systemctl --user enable --now claude-standing-orders-sync.timer
    systemctl --user enable --now claude-standing-orders-sync.path
    echo "Systemd sync units installed and enabled (timer + path watcher)"
else
    systemctl --user disable --now claude-standing-orders-sync.timer 2>/dev/null || true
    systemctl --user disable --now claude-standing-orders-sync.path 2>/dev/null || true
    echo "Systemd sync units installed but NOT enabled — $CLAUDE_DIR/sync-standing-orders.sh is missing."
    echo "  (Nothing to run on the timer; they would just fail every 5 minutes.)"
fi

# Install enforcement hooks
HOOKS_DIR="$CLAUDE_DIR/hooks"
mkdir -p "$HOOKS_DIR"
echo "Pulling enforcement hooks from homelab..."
HOMELAB_SSH="${FOCUSLOCK_HOMELAB_SSH:-${HOMELAB_TS:-}}"
for hook in scan-pronouns.sh end-of-session.sh; do
    if [ -n "$HOMELAB_SSH" ] && scp -o ConnectTimeout=5 "$USER@$HOMELAB_SSH:$HOME/.claude/hooks/$hook" "$HOOKS_DIR/$hook" 2>/dev/null; then
        chmod +x "$HOOKS_DIR/$hook"
        echo "  $hook installed"
    fi
done

# Run initial sync to pull memory files immediately
if [ -x "$CLAUDE_DIR/sync-standing-orders.sh" ]; then
    echo "Running initial sync (including memory files)..."
    "$CLAUDE_DIR/sync-standing-orders.sh" 2>&1 || true
fi

echo ""
# Say what actually happened. Announcing "the collar follows you everywhere now"
# after every fetch failed sends the operator away believing a machine is under
# orders that never received any.
if [ "$FETCHED_ANY" = 1 ]; then
    echo "Standing orders installed. The collar follows you everywhere now."
else
    echo "NOTHING WAS INSTALLED — every fetch failed."
    echo "  $HOMELAB_URL/standing-orders and /settings are admin-gated: set"
    echo "  FOCUSLOCK_ADMIN_TOKEN, or FOCUSLOCK_HOMELAB_SSH for the scp fallback."
    echo "  This machine's Claude config was left exactly as it was."
    echo
    echo "  This is not the same as being un-collared. A running collar pulls its"
    echo "  own standing orders over the node-signed /vault/{mesh}/standing-orders"
    echo "  route and does not need this script; check when it last did with:"
    echo "    stat -c %y ~/.config/focuslock/standing-orders.applied"
    exit 1
fi

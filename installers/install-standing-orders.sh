#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Install The Collar standing orders + enforcement hook for Claude Code
# Pulls CLAUDE.md + settings.json from homelab mail service (HTTP, no SSH needed)
# Also installs systemd sync units with tamper detection
#
# Environment:
#   FOCUSLOCK_HOMELAB       — homelab URL (e.g. http://x.x.x.x:8434)
#   FOCUSLOCK_HOMELAB_HOST  — Tailscale hostname for auto-discovery
#   FOCUSLOCK_HOMELAB_SSH   — SSH address for homelab (fallback for SCP)
set -e

CLAUDE_DIR="$HOME/.claude"

# Resolve homelab URL: env var > Tailscale > error
HOMELAB_HOSTNAME="${FOCUSLOCK_HOMELAB_HOST:-}"
if [ -n "$FOCUSLOCK_HOMELAB" ]; then
    HOMELAB_URL="$FOCUSLOCK_HOMELAB"
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

# Pull latest from homelab via HTTP
echo "Pulling standing orders from homelab..."
if curl -sf -o "$CLAUDE_DIR/CLAUDE.md" --connect-timeout 5 "$HOMELAB_URL/standing-orders"; then
    chmod 644 "$CLAUDE_DIR/CLAUDE.md"
    echo "  CLAUDE.md installed"
else
    echo "  CLAUDE.md: HTTP failed, trying SSH..."
    HOMELAB_SSH="${FOCUSLOCK_HOMELAB_SSH:-${HOMELAB_TS:-}}"
    if [ -n "$HOMELAB_SSH" ]; then
        scp -o ConnectTimeout=5 "$USER@$HOMELAB_SSH:$HOME/.claude/CLAUDE.md" "$CLAUDE_DIR/CLAUDE.md" 2>/dev/null || \
            echo "  ERROR: Could not reach homelab"
    else
        echo "  ERROR: No SSH fallback configured (set FOCUSLOCK_HOMELAB_SSH)"
    fi
fi

if curl -sf -o "$CLAUDE_DIR/settings.json" --connect-timeout 5 "$HOMELAB_URL/settings"; then
    chmod 644 "$CLAUDE_DIR/settings.json"
    echo "  settings.json installed"
else
    echo "  settings.json: HTTP failed, trying SSH..."
    HOMELAB_SSH="${FOCUSLOCK_HOMELAB_SSH:-${HOMELAB_TS:-}}"
    if [ -n "$HOMELAB_SSH" ]; then
        scp -o ConnectTimeout=5 "$USER@$HOMELAB_SSH:$HOME/.claude/settings.json" "$CLAUDE_DIR/settings.json" 2>/dev/null || \
            echo "  settings.json: not available (will use defaults)"
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
systemctl --user enable --now claude-standing-orders-sync.timer
systemctl --user enable --now claude-standing-orders-sync.path
echo "Systemd sync units installed and enabled (timer + path watcher)"

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
echo "Running initial sync (including memory files)..."
"$CLAUDE_DIR/sync-standing-orders.sh" 2>&1 || true
echo ""
echo "Standing orders installed. The collar follows you everywhere now."

#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
#
# Local teardown — removes the Linux Desktop Collar and all its traces
# without needing a signed Release order from the mesh. Use this when
# the Lion phone / private key is lost and you need to rescue a desktop.
#
# What it removes:
#   - systemd user units: focuslock-desktop, focuslock-tray,
#     claude-standing-orders-sync (.service/.timer/.path)
#   - autostart entries under ~/.config/autostart
#   - /opt/focuslock (binaries, web UI, icons, pubkey)
#   - ~/.config/focuslock (vault keys, peers, orders, pairing codes)
#   - ~/.local/share/focuslock, ~/collar-files (if present)
#   - /run/focuslock + /etc/tmpfiles.d/focuslock.conf
#   - /etc/sudoers.d/focuslock
#   - ~/.claude/sync-standing-orders.sh (collar sync helper only)
#
# What it leaves alone:
#   - ~/.claude/CLAUDE.md and ~/.claude/settings.json — these may hold
#     user-authored content layered on top of the collar's standing
#     orders. Review and edit them manually.
#   - ~/.claude/hooks/ — same reason.
#
# Requires sudo for /opt, /etc, /run cleanup. Restores the original
# wallpaper if one was saved. Safe to re-run.

set -u

RED=$'\033[0;31m'; GREEN=$'\033[0;32m'; YELLOW=$'\033[0;33m'; BOLD=$'\033[1m'; OFF=$'\033[0m'
info()  { echo "${GREEN}[+]${OFF} $*"; }
warn()  { echo "${YELLOW}[!]${OFF} $*"; }
err()   { echo "${RED}[x]${OFF} $*"; }
step()  { echo ""; echo "${BOLD}=== $* ===${OFF}"; }

if [ "$(id -u)" -eq 0 ]; then
    err "Run as your normal user, not root. The script will call sudo when needed."
    exit 1
fi

cat <<EOF
${BOLD}FocusLock Desktop Collar — Local Uninstall${OFF}

This will permanently remove the collar from this machine. It does NOT
notify the mesh (no signed Release order is sent). You are responsible
for any other devices still on the mesh.

EOF
read -r -p "Type 'release' to proceed: " CONFIRM
if [ "$CONFIRM" != "release" ]; then
    warn "Aborted."
    exit 0
fi

# ─────────────────────────────────────────────────────────────
# 1. Stop + disable systemd units BEFORE killing processes, so
#    the watchdog-style Restart=always cannot respawn them.
# ─────────────────────────────────────────────────────────────
step "Stopping systemd user units"
UNITS=(
    focuslock-desktop.service
    focuslock-tray.service
    claude-standing-orders-sync.service
    claude-standing-orders-sync.timer
    claude-standing-orders-sync.path
)
for u in "${UNITS[@]}"; do
    if systemctl --user list-unit-files "$u" &>/dev/null; then
        systemctl --user disable --now "$u" 2>/dev/null && info "disabled $u" || true
    fi
done

# ─────────────────────────────────────────────────────────────
# 2. Kill stragglers. The in-process liberation path can be
#    interrupted; pkill makes sure nothing survives.
# ─────────────────────────────────────────────────────────────
step "Killing any running collar processes"
for pat in focuslock-desktop.py focuslock-tray.py; do
    if pgrep -f "$pat" >/dev/null 2>&1; then
        pkill -TERM -f "$pat" 2>/dev/null || true
        sleep 1
        pkill -KILL -f "$pat" 2>/dev/null || true
        info "killed $pat"
    fi
done

# ─────────────────────────────────────────────────────────────
# 3. Restore wallpaper if we saved one. Best-effort — we try
#    GNOME (gsettings) and KDE Plasma (plasma-apply-wallpaperimage)
#    because those are the two desktop collar targets.
# ─────────────────────────────────────────────────────────────
step "Restoring wallpaper"
WP_SAVE="$HOME/.config/focuslock/original-wallpaper"
if [ -f "$WP_SAVE" ]; then
    ORIG_WP="$(cat "$WP_SAVE" 2>/dev/null | head -n1 | tr -d '[:space:]')"
    if [ -n "$ORIG_WP" ] && [ -f "$ORIG_WP" ]; then
        if command -v gsettings >/dev/null 2>&1; then
            gsettings set org.gnome.desktop.background picture-uri "file://$ORIG_WP" 2>/dev/null || true
            gsettings set org.gnome.desktop.background picture-uri-dark "file://$ORIG_WP" 2>/dev/null || true
        fi
        if command -v plasma-apply-wallpaperimage >/dev/null 2>&1; then
            plasma-apply-wallpaperimage "$ORIG_WP" 2>/dev/null || true
        fi
        info "wallpaper restored: $ORIG_WP"
    else
        warn "saved wallpaper path missing or unreadable — skipping restore"
    fi
else
    warn "no saved wallpaper (collar may never have locked this session)"
fi

# ─────────────────────────────────────────────────────────────
# 4. Remove autostart + systemd unit files.
# ─────────────────────────────────────────────────────────────
step "Removing autostart + unit files"
USER_FILES=(
    "$HOME/.config/autostart/focuslock-desktop.desktop"
    "$HOME/.config/autostart/focuslock-tray.desktop"
    "$HOME/.config/systemd/user/focuslock-desktop.service"
    "$HOME/.config/systemd/user/focuslock-tray.service"
    "$HOME/.config/systemd/user/claude-standing-orders-sync.service"
    "$HOME/.config/systemd/user/claude-standing-orders-sync.timer"
    "$HOME/.config/systemd/user/claude-standing-orders-sync.path"
)
for f in "${USER_FILES[@]}"; do
    [ -e "$f" ] && rm -f "$f" && info "removed $f"
done

# Collar's standing-orders sync helper (clearly collar-authored).
# Leave CLAUDE.md / settings.json / hooks alone — user may have
# edited them.
if [ -f "$HOME/.claude/sync-standing-orders.sh" ]; then
    rm -f "$HOME/.claude/sync-standing-orders.sh"
    info "removed ~/.claude/sync-standing-orders.sh"
fi

systemctl --user daemon-reload 2>/dev/null || true

# ─────────────────────────────────────────────────────────────
# 5. Remove per-user config (vault privkey, peers, orders, etc).
# ─────────────────────────────────────────────────────────────
step "Removing per-user config"
for d in "$HOME/.config/focuslock" "$HOME/.local/share/focuslock" "$HOME/collar-files"; do
    if [ -e "$d" ]; then
        rm -rf "$d" && info "removed $d"
    fi
done

# Lockscreen leftovers in /tmp
rm -f /tmp/focuslock-lock.html /tmp/focuslock-kwin-enforce.js 2>/dev/null || true
[ -d /tmp/focuslock-winmount ] && rm -rf /tmp/focuslock-winmount 2>/dev/null || true

# ─────────────────────────────────────────────────────────────
# 6. System paths — need sudo. Do these last so a sudo prompt
#    failure doesn't block per-user cleanup above.
# ─────────────────────────────────────────────────────────────
step "Removing system paths (sudo)"
SYSTEM_PATHS=(
    /opt/focuslock
    /run/focuslock
    /etc/tmpfiles.d/focuslock.conf
    /etc/sudoers.d/focuslock
)
NEED_SUDO=0
for p in "${SYSTEM_PATHS[@]}"; do
    [ -e "$p" ] && NEED_SUDO=1 && break
done

if [ "$NEED_SUDO" -eq 1 ]; then
    sudo -v || { err "sudo required to finish system cleanup"; exit 1; }
    for p in "${SYSTEM_PATHS[@]}"; do
        if [ -e "$p" ]; then
            sudo rm -rf "$p" && info "removed $p"
        fi
    done
    # Reload tmpfiles so the /run/focuslock entry doesn't get recreated on boot
    if command -v systemd-tmpfiles >/dev/null 2>&1; then
        sudo systemd-tmpfiles --remove 2>/dev/null || true
    fi
else
    info "nothing to remove under /opt, /run, /etc"
fi

# ─────────────────────────────────────────────────────────────
# 7. Report.
# ─────────────────────────────────────────────────────────────
step "Done"
cat <<EOF
The Desktop Collar is gone from this machine.

Still on disk (intentionally, review manually):
  ~/.claude/CLAUDE.md          — may contain your own content
  ~/.claude/settings.json      — may contain your own settings
  ~/.claude/hooks/             — may contain your own hooks

Log out or reboot to be sure no stale in-memory state lingers.
EOF

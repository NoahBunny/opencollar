#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Re-enslave linux desktop collars (lockscreen + tray).
# Pushes the latest focuslock-desktop.py, focuslock_mesh.py, focuslock_ntfy.py, focuslock-tray.py,
# canonical icons, web UI, autostart entries, then restarts the user-mode
# collar service. Discovers remote desktops via the mesh node list.
#
# Usage:
#   ./re-enslave-desktops.sh                # local + all reachable peers
#   ./re-enslave-desktops.sh --local-only   # this machine only
#   ./re-enslave-desktops.sh --dry-run      # show what would change
#
# Requires: ssh + passwordless sudo on remote desktops as $DEPLOY_USER (default $USER).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RE_LOG_PREFIX="desktops"
# shellcheck source=re-enslave-lib.sh
source "$SCRIPT_DIR/re-enslave-lib.sh"

LOCAL_ONLY=0
DRY_RUN=0
for arg in "$@"; do
    case "$arg" in
        --local-only) LOCAL_ONLY=1 ;;
        --dry-run) DRY_RUN=1 ;;
        -h|--help) sed -n '4,16p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) fail "Unknown arg: $arg" ;;
    esac
done

check_paywall
discover_paths
load_config

DEPLOY_USER="${DEPLOY_USER:-$USER}"

# ── Local deploy ──
# Two-phase: user-side first (icons, autostart, lion_pubkey to ~/.config —
# always succeeds), then system-side (/opt/focuslock — needs sudo, soft-fails
# if not available so the user-side install still completes). Ordering matters:
# the crown tray icon is the visible "is this thing alive" signal, so getting
# it into ~/.config/focuslock/icons/ unconditionally beats aborting halfway
# through on a machine that hasn't been pre-sudoers'd.
deploy_local() {
    section "Local: $(hostname)"
    [ "$DRY_RUN" = 1 ] && log "DRY RUN — no changes will be made"

    # Probe sudo once. Re-enslave can run user-side-only on machines that
    # haven't been bootstrapped via install-desktop-collar.sh (no sudoers
    # rule yet) — we just skip /opt/focuslock writes and warn.
    local SUDO_OK=0
    if [ "$DRY_RUN" = 1 ]; then
        SUDO_OK=1  # dry-run pretends sudo works so we log all the steps
    elif sudo -n true 2>/dev/null; then
        SUDO_OK=1
    else
        warn "  sudo not available — system-side ops (/opt/focuslock) will be skipped"
        warn "  user-side install (icons + autostart) will still complete"
    fi

    # ── Phase 1: user-side (no sudo, never fatal) ──

    # Tray dependency check (best effort, never fails the script)
    if ! python3 -c "import gi; gi.require_version('AppIndicator3', '0.1')" 2>/dev/null; then
        if [ "$SUDO_OK" = 1 ] && [ "$DRY_RUN" != 1 ]; then
            log "Installing AppIndicator3 typelib (sudo)…"
            if command -v dnf &>/dev/null; then sudo dnf install -y libappindicator-gtk3 >/dev/null 2>&1 || true
            elif command -v pacman &>/dev/null; then sudo pacman -S --needed --noconfirm libappindicator-gtk3 >/dev/null 2>&1 || true
            elif command -v apt-get &>/dev/null; then sudo apt-get install -y gir1.2-appindicator3-0.1 >/dev/null 2>&1 || true
            fi
        else
            warn "  AppIndicator3 typelib missing — tray crown won't render until you install it"
        fi
    fi

    # Crown tray icons + collar lockscreen icons → ~/.config/focuslock/icons/
    # This is the FIX for "tray crown doesn't show up": the icons need to be
    # present at $ICON_LOCKED / $ICON_UNLOCKED in focuslock-tray.py; missing
    # icons → AppIndicator silently has nothing to render.
    if [ "$DRY_RUN" != 1 ]; then
        mkdir -p ~/.config/focuslock/icons ~/.local/share/focuslock
    fi
    local icon_copied=0
    for icon in "$SERVER_ICON" collar-icon-gold.png crown-gold.png crown-gray.png; do
        src="$ICONS/$icon"
        if [ ! -f "$src" ]; then
            warn "  icons/$icon missing in source ($ICONS) — skipped"
            continue
        fi
        log "  icons/$icon"
        if [ "$DRY_RUN" != 1 ]; then
            cp "$src" ~/.config/focuslock/icons/ && icon_copied=$((icon_copied + 1))
            if [ "$icon" = "$SERVER_ICON" ] || [ "$icon" = "collar-icon-gold.png" ]; then
                cp "$src" ~/.local/share/focuslock/ 2>/dev/null || true
            fi
        fi
    done
    if [ "$DRY_RUN" != 1 ] && [ "$icon_copied" = 0 ]; then
        warn "  no icons copied — tray crown won't render. Check that $ICONS exists."
    fi

    # Lion pubkey (signature verification for orders) → user copy
    for src in "$LS/lion_pubkey.pem" "$FL/keys/lion_pubkey.pem" /opt/focuslock/lion_pubkey.pem; do
        [ -f "$src" ] || continue
        log "  lion_pubkey.pem (user)"
        [ "$DRY_RUN" = 1 ] || cp "$src" ~/.config/focuslock/lion_pubkey.pem 2>/dev/null || true
        break
    done

    # Autostart entries
    if [ "$DRY_RUN" != 1 ]; then
        mkdir -p ~/.config/autostart
        cat > ~/.config/autostart/focuslock-desktop.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=FocusLock Desktop Collar
Exec=python3 -u /opt/focuslock/focuslock-desktop.py
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
EOF
        cat > ~/.config/autostart/focuslock-tray.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=FocusLock Tray
Exec=python3 /opt/focuslock/focuslock-tray.py
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
EOF
    fi
    log "  autostart entries"

    # Reset stale mesh state — collar will re-fetch from mesh on first sync
    [ "$DRY_RUN" = 1 ] || rm -f ~/.config/focuslock/orders.json ~/.config/focuslock/peers.json
    log "  cleared stale orders + peers cache"

    # ── Phase 2: system-side (/opt/focuslock — needs sudo) ──

    if [ "$SUDO_OK" = 1 ]; then
        [ "$DRY_RUN" = 1 ] || sudo mkdir -p /opt/focuslock /opt/focuslock/web

        # Core files (Lion's Share = canonical Python tree)
        for f in "${DESKTOP_FILES[@]}"; do
            if [ -f "$LS/$f" ]; then
                log "  $f"
                [ "$DRY_RUN" = 1 ] || sudo install -D -m 755 "$LS/$f" "/opt/focuslock/$f"
            fi
        done

        # Shared modules
        for src in "$LS"/shared/focuslock_*.py; do
            [ -f "$src" ] || continue
            bn=$(basename "$src")
            log "  shared/$bn"
            [ "$DRY_RUN" = 1 ] || sudo install -D -m 644 "$src" "/opt/focuslock/$bn"
        done

        # Web UI
        if [ -f "$LS/web/index.html" ]; then
            log "  web/index.html"
            [ "$DRY_RUN" = 1 ] || sudo install -D -m 644 "$LS/web/index.html" /opt/focuslock/web/index.html
        fi

        # Lockscreen icons (system path — used by FocusActivity equivalent)
        for icon in "$SERVER_ICON" collar-icon-gold.png; do
            src="$ICONS/$icon"
            [ -f "$src" ] || continue
            [ "$DRY_RUN" = 1 ] || sudo install -D -m 0644 "$src" "/opt/focuslock/$icon" 2>/dev/null || true
        done

        # Lion pubkey (system copy for the desktop daemon)
        for src in "$LS/lion_pubkey.pem" "$FL/keys/lion_pubkey.pem" /opt/focuslock/lion_pubkey.pem; do
            [ -f "$src" ] || continue
            log "  lion_pubkey.pem (system)"
            [ "$DRY_RUN" = 1 ] || sudo install -D -m 0644 "$src" /opt/focuslock/lion_pubkey.pem 2>/dev/null || true
            break
        done
    fi

    # Standing orders sync (delegates to install-standing-orders.sh if present)
    if [ -f "$SCRIPT_DIR/install-standing-orders.sh" ] && [ "$DRY_RUN" != 1 ]; then
        bash "$SCRIPT_DIR/install-standing-orders.sh" 2>&1 | sed 's/^/  /' || \
            warn "  standing orders sync failed (non-fatal)"
    fi

    # Restart collar (best effort — needs an active desktop session)
    if [ "$DRY_RUN" = 1 ]; then return 0; fi

    pkill -f focuslock-desktop.py 2>/dev/null || true
    pkill -f focuslock-tray.py 2>/dev/null || true
    sleep 1

    # Resolve the script path: prefer /opt/focuslock when present, fall back to
    # the canonical source tree so user-only installs still launch.
    local desktop_py="/opt/focuslock/focuslock-desktop.py"
    local tray_py="/opt/focuslock/focuslock-tray.py"
    [ -f "$desktop_py" ] || desktop_py="$LS/focuslock-desktop.py"
    [ -f "$tray_py" ]    || tray_py="$LS/focuslock-tray.py"

    if systemctl --user restart focuslock-desktop.service 2>/dev/null; then
        log "  restarted via systemd --user"
    elif [ -n "${DISPLAY:-}" ] || [ -n "${WAYLAND_DISPLAY:-}" ]; then
        nohup python3 -u "$desktop_py" >/tmp/focuslock-collar.log 2>&1 &
        nohup python3 "$tray_py" >/dev/null 2>&1 &
        log "  started directly (logs: /tmp/focuslock-collar.log)"
    else
        warn "  not restarted — no DISPLAY/WAYLAND_DISPLAY (restart from desktop session)"
    fi

    # Verify mesh is responding within 5s
    sleep 5
    if curl -sf --max-time 3 http://localhost:8435/mesh/ping >/dev/null 2>&1; then
        ver=$(curl -s --max-time 3 http://localhost:8435/mesh/ping \
            | python3 -c 'import sys,json;print(json.load(sys.stdin).get("orders_version",0))' 2>/dev/null || echo '?')
        log "  mesh: CONNECTED (v$ver)"
    else
        warn "  mesh: not responding on :8435 (may need a desktop session)"
    fi
}

# ── Remote deploy via SSH ──
deploy_remote() {
    local name="$1" addr="$2"
    section "Remote: $name ($addr)"

    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$DEPLOY_USER@$addr" 'echo ok' >/dev/null 2>&1; then
        warn "  unreachable (SSH failed)"
        return 0
    fi

    if [ "$DRY_RUN" = 1 ]; then
        log "  DRY RUN — would push files via scp + sudo install"
        return 0
    fi

    # Push everything from /opt/focuslock that exists locally — relies on
    # local being current first (deploy_local always runs before remote loop).
    local files_to_push=()
    for f in "${DESKTOP_FILES[@]}"; do
        [ -f "/opt/focuslock/$f" ] && files_to_push+=("/opt/focuslock/$f")
    done
    for f in /opt/focuslock/focuslock_*.py; do
        [ -f "$f" ] && files_to_push+=("$f")
    done
    [ -f "/opt/focuslock/$SERVER_ICON" ] && files_to_push+=("/opt/focuslock/$SERVER_ICON")
    [ -f "/opt/focuslock/lion_pubkey.pem" ] && files_to_push+=("/opt/focuslock/lion_pubkey.pem")
    [ -f "/opt/focuslock/web/index.html" ] && files_to_push+=("/opt/focuslock/web/index.html")
    [ -f "$HOME/.config/focuslock/config.json" ] && files_to_push+=("$HOME/.config/focuslock/config.json")

    if [ "${#files_to_push[@]}" -eq 0 ]; then
        warn "  nothing to push from /opt/focuslock — run --local-only first?"
        return 0
    fi

    scp -o ConnectTimeout=5 -q "${files_to_push[@]}" "$DEPLOY_USER@$addr:/tmp/" 2>/dev/null

    ssh -o ConnectTimeout=5 "$DEPLOY_USER@$addr" "bash -s" << REMOTE_EOF
set -e
sudo mkdir -p /opt/focuslock /opt/focuslock/web
for f in focuslock-desktop.py focuslock_mesh.py focuslock_*.py $SERVER_ICON lion_pubkey.pem; do
    [ -f /tmp/\$f ] && sudo install -D -m 644 /tmp/\$f /opt/focuslock/\$f && rm -f /tmp/\$f
done
# chmod no longer needed separately — `sudo install -D -m 0755` above
# already sets the mode on each file. Sudoers no longer grants wildcard chmod.
[ -f /tmp/index.html ] && sudo install -D -m 644 /tmp/index.html /opt/focuslock/web/index.html && rm -f /tmp/index.html
[ -f /tmp/config.json ] && mkdir -p ~/.config/focuslock && cp /tmp/config.json ~/.config/focuslock/config.json && rm -f /tmp/config.json
mkdir -p ~/.local/share/focuslock
[ -f /opt/focuslock/$SERVER_ICON ] && cp /opt/focuslock/$SERVER_ICON ~/.local/share/focuslock/ 2>/dev/null || true
rm -f ~/.config/focuslock/orders.json ~/.config/focuslock/peers.json
pkill -f focuslock-desktop.py 2>/dev/null || true
pkill -f focuslock-tray.py 2>/dev/null || true
echo "  $name: files updated, restart from desktop session"
REMOTE_EOF
}

# ── Main ──
deploy_local

if [ "$LOCAL_ONLY" = 1 ]; then
    section "Skipping remote (--local-only)"
    exit 0
fi

section "Discovering remote desktop peers"
PEERS=$(curl -s --max-time 3 http://localhost:8435/mesh/status 2>/dev/null | python3 -c "
import sys, json
try: d = json.load(sys.stdin)
except: sys.exit(0)
me = '$(hostname)'
for nid, info in (d.get('nodes') or {}).items():
    if info.get('type') == 'desktop' and nid != me:
        addrs = info.get('addresses') or []
        if addrs:
            print(f'{nid}|{addrs[0]}')
" 2>/dev/null || true)

if [ -z "$PEERS" ]; then
    log "No remote desktop peers found in mesh."
else
    while IFS= read -r peer; do
        [ -z "$peer" ] && continue
        deploy_remote "${peer%%|*}" "${peer##*|}"
    done <<< "$PEERS"
fi

section "Desktop re-enslave complete"

#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Install FocusLock Desktop Collar
# Works on: Fedora, Bazzite, Ubuntu, Garuda (Arch), any systemd + Wayland/KDE
# Mirrors phone lock state to desktop via loginctl session lock
set -e

# --non-interactive: fail fast instead of waiting for stdin prompts. Useful
# for re-enslave-watcher + automated deploys (CLAUDE.md). Requires
# FOCUSLOCK_HOMELAB or FOCUSLOCK_HOMELAB_HOST (+ Tailscale discovery) to be set.
NON_INTERACTIVE=0
for arg in "$@"; do
    case "$arg" in
        --non-interactive|-n) NON_INTERACTIVE=1 ;;
    esac
done
if [ "$NON_INTERACTIVE" = 0 ] && [ ! -t 0 ]; then
    # No TTY attached — assume automated. Avoid blocking on prompts.
    NON_INTERACTIVE=1
fi

echo "=== FocusLock Desktop Collar Installer ==="
echo ""

# Pre-cache sudo so later sudo calls don't prompt mid-install. Fails fast
# instead of hanging on a password prompt the user can't see.
if ! sudo -n true 2>/dev/null; then
    if [ "$NON_INTERACTIVE" = 1 ]; then
        echo "ERROR: sudo requires a password and stdin isn't a TTY. Aborting." >&2
        exit 1
    fi
    echo "sudo needs your password (will be cached for the rest of this install):"
    sudo -v
fi

# Detect distro
if [ -f /etc/os-release ]; then
    . /etc/os-release
    DISTRO="$ID"
    DISTRO_LIKE="$ID_LIKE"
else
    echo "Cannot detect distro. Aborting."
    exit 1
fi

echo "Detected: $NAME ($DISTRO)"

# Install dependencies
echo ""
echo "=== Installing dependencies ==="

if echo "$DISTRO $DISTRO_LIKE" | grep -qi "arch"; then
    sudo pacman -S --needed --noconfirm python-gobject python-cairo gtk4 webkitgtk-6.0 2>/dev/null || true
elif echo "$DISTRO $DISTRO_LIKE" | grep -qi "fedora"; then
    if command -v rpm-ostree &>/dev/null; then
        echo "Immutable OS detected. Checking deps..."
        /usr/bin/python3 -c "import gi; gi.require_version('Gtk', '4.0')" 2>/dev/null && \
        /usr/bin/python3 -c "import cairo" 2>/dev/null || {
            echo "Missing deps. Run: rpm-ostree install python3-gobject python3-cairo gtk4"
            echo "Then reboot and re-run this installer."
            exit 1
        }
    else
        sudo dnf install -y python3-gobject python3-cairo gtk4 webkitgtk6.0 2>/dev/null || true
    fi
elif echo "$DISTRO $DISTRO_LIKE" | grep -qi "ubuntu\|debian"; then
    sudo apt-get update -qq
    sudo apt-get install -y python3-gi python3-cairo gir1.2-gtk-4.0 gir1.2-webkit-6.0 2>/dev/null || true
else
    echo "Unknown distro: $DISTRO. Trying to proceed..."
fi

# Verify deps
/usr/bin/python3 -c "import gi; gi.require_version('Gtk', '4.0'); import cairo; print('Deps OK')" || {
    echo "FATAL: Python GTK4 or cairo bindings not working."
    exit 1
}

# Verify loginctl
command -v loginctl &>/dev/null || {
    echo "FATAL: loginctl not found. systemd-logind required."
    exit 1
}

# Install Lexend font if missing
if ! fc-list | grep -qi lexend; then
    echo "=== Installing Lexend font ==="
    mkdir -p ~/.local/share/fonts/l
    # -m 15: hard timeout so a slow/offline GitHub doesn't block install forever.
    curl -sL -m 15 "https://github.com/googlefonts/lexend/raw/main/fonts/variable/Lexend%5BHEXP%2Cwght%5D.ttf" \
        -o ~/.local/share/fonts/l/Lexend.ttf 2>/dev/null && fc-cache -f ~/.local/share/fonts/ 2>/dev/null
fi

# Install files
echo ""
echo "=== Installing daemon ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
sudo mkdir -p /opt/focuslock /opt/focuslock/web

# Daemon entrypoint + tray + root-level mesh / ntfy modules
for f in focuslock-desktop.py focuslock-tray.py focuslock_mesh.py focuslock_ntfy.py; do
    if [ ! -f "$PROJECT_DIR/$f" ]; then
        echo "FATAL: $PROJECT_DIR/$f missing — repo is incomplete." >&2
        exit 1
    fi
    sudo cp "$PROJECT_DIR/$f" /opt/focuslock/
done
sudo chmod 755 /opt/focuslock/focuslock-desktop.py /opt/focuslock/focuslock-tray.py

# Shared modules — focuslock-desktop.py imports focuslock_http, focuslock_sync,
# focuslock_config; deploy the whole shared/focuslock_*.py family so future
# import additions don't silently crashloop the daemon.
shared_count=0
for src in "$PROJECT_DIR/shared/"focuslock_*.py; do
    [ -f "$src" ] || continue
    sudo cp "$src" /opt/focuslock/
    shared_count=$((shared_count + 1))
done
echo "  $shared_count shared modules installed"

# Icons (collar lockscreen + tray)
sudo cp "$PROJECT_DIR/icons/collar-icon.png" /opt/focuslock/ 2>/dev/null || \
    sudo cp "$PROJECT_DIR/collar-icon.png" /opt/focuslock/ 2>/dev/null || true
sudo cp "$PROJECT_DIR/icons/collar-icon-gold.png" /opt/focuslock/ 2>/dev/null || true

# Crown tray icons (per-user)
mkdir -p ~/.config/focuslock/icons
for icon in crown-gold.png crown-gray.png; do
    cp "$PROJECT_DIR/icons/$icon" ~/.config/focuslock/icons/ 2>/dev/null || true
done

# Install Lion's Share public key (needed for signature verification)
if [ ! -f ~/.config/focuslock/lion_pubkey.pem ]; then
    for src in "$PROJECT_DIR/lion_pubkey.pem" /opt/focuslock/lion_pubkey.pem; do
        if [ -f "$src" ]; then
            cp "$src" ~/.config/focuslock/lion_pubkey.pem
            sudo cp "$src" /opt/focuslock/lion_pubkey.pem 2>/dev/null || true
            echo "  lion_pubkey.pem installed"
            break
        fi
    done
fi

# Install web UI (enables desktop as mesh server)
if [ -f "$PROJECT_DIR/web/index.html" ]; then
    sudo cp "$PROJECT_DIR/web/index.html" /opt/focuslock/web/
    echo "  Web UI installed"
fi

# Install systemd user service
echo "=== Installing systemd user service ==="
mkdir -p ~/.config/systemd/user

# Detect homelab — env var, then Tailscale discovery. Optional: consumer
# installs against a public mesh relay use ~/.config/focuslock/config.json
# (mesh_url + mesh_id + vault_mode) and don't need a homelab at all. Only
# the operator-side ADB bridge / standing-orders sync features require it.
HOMELAB="${FOCUSLOCK_HOMELAB:-}"
HOMELAB_HOSTNAME="${FOCUSLOCK_HOMELAB_HOST:-}"
if [ -z "$HOMELAB" ] && command -v tailscale &>/dev/null && [ -n "$HOMELAB_HOSTNAME" ]; then
    TS_IP=$(tailscale status 2>/dev/null | grep "$HOMELAB_HOSTNAME" | awk '{print $1}')
    if [ -n "$TS_IP" ]; then
        if curl -s -m 3 "http://$TS_IP:8434/desktop-status" >/dev/null 2>&1; then
            HOMELAB="http://$TS_IP:8434"
            echo "Found homelab via Tailscale: $HOMELAB"
        fi
    fi
fi
if [ -z "$HOMELAB" ]; then
    if [ -f ~/.config/focuslock/config.json ] && grep -q '"mesh_url"' ~/.config/focuslock/config.json; then
        echo "No homelab configured — using ~/.config/focuslock/config.json (consumer mesh-relay install)."
    elif [ "$NON_INTERACTIVE" = 1 ]; then
        echo "No homelab and no ~/.config/focuslock/config.json — daemon will start but won't pair." >&2
        echo "Set FOCUSLOCK_HOMELAB or write a config.json with mesh_url/mesh_id before pairing." >&2
    else
        echo ""
        echo "No homelab detected. Two paths:"
        echo "  [1] consumer / mesh-relay install (recommended) — pair via Bunny Tasker QR later"
        echo "  [2] homelab install — ADB bridge, standing-orders sync, etc."
        read -p "Homelab URL (blank for [1]): " HOMELAB
    fi
fi

ENV_HOMELAB_LINE=""
if [ -n "$HOMELAB" ]; then
    ENV_HOMELAB_LINE="Environment=FOCUSLOCK_HOMELAB=$HOMELAB"
fi

cat > ~/.config/systemd/user/focuslock-desktop.service << EOF
[Unit]
Description=FocusLock Desktop Collar
After=graphical-session.target
PartOf=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 -u /opt/focuslock/focuslock-desktop.py
Restart=always
RestartSec=1
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)
$ENV_HOMELAB_LINE

[Install]
WantedBy=graphical-session.target
EOF

cat > ~/.config/systemd/user/focuslock-tray.service << EOF
[Unit]
Description=FocusLock System Tray
After=graphical-session.target focuslock-desktop.service
PartOf=graphical-session.target
# Tray is non-essential — if it fails repeatedly we shouldn't bring down
# the whole user session, so the failure mode is "indicator missing"
# rather than "daemon-reload loop".

[Service]
Type=simple
ExecStart=/usr/bin/python3 -u /opt/focuslock/focuslock-tray.py
Restart=on-failure
RestartSec=5
Environment=DISPLAY=:0
Environment=WAYLAND_DISPLAY=wayland-0
Environment=XDG_RUNTIME_DIR=/run/user/$(id -u)

[Install]
WantedBy=graphical-session.target
EOF

# Enable and start
systemctl --user daemon-reload
systemctl --user enable focuslock-desktop.service
systemctl --user start focuslock-desktop.service
systemctl --user enable focuslock-tray.service 2>/dev/null || true
systemctl --user start focuslock-tray.service 2>/dev/null || true

# XDG autostart fallback
echo "=== Installing autostart entry ==="
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/focuslock-desktop.desktop << 'EOF'
[Desktop Entry]
Type=Application
Name=FocusLock Desktop Collar
Exec=/usr/bin/python3 -u /opt/focuslock/focuslock-desktop.py
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
EOF

# Runtime directory
echo "=== Creating runtime directory ==="
sudo mkdir -p /run/focuslock 2>/dev/null
sudo chmod 777 /run/focuslock 2>/dev/null
echo "d /run/focuslock 0777 root root -" | sudo tee /etc/tmpfiles.d/focuslock.conf >/dev/null 2>/dev/null || true

# Sudoers: allow passwordless deploy to /opt/focuslock/ and service restarts.
# Tightened 2026-04-17 — the previous rules used wildcards for `cp` and
# `chmod` which enabled (a) arbitrary-file exfiltration via
# `sudo cp /etc/shadow /opt/focuslock/x` and (b) privilege escalation
# via `sudo chmod 4755 /opt/focuslock/<user-writable-file>`. The new
# rules require the `install` command with explicit modes (no setuid
# bits) and filename patterns starting with the focuslock/lion/collar
# prefixes; callers must use `sudo install` instead of `sudo cp`/`chmod`.
echo "=== Configuring sudoers for FocusLock deployment ==="
SUDOERS_FILE="/etc/sudoers.d/focuslock"
CURRENT_USER="$(whoami)"
cat << SUDOERS_EOF | sudo tee "$SUDOERS_FILE" >/dev/null
# FocusLock — allow collar deployment without password.
# Allowed modes restricted to non-setuid, non-setgid, non-sticky:
#   0644 (data), 0755 (executables). Wildcards in the source are scoped
#   to paths whose basename begins with an allowed prefix, so
#   /etc/shadow etc. cannot be passed.
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/install -D -m 0755 /*/focuslock*.py /opt/focuslock/focuslock*.py
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/install -D -m 0644 /*/focuslock_*.py /opt/focuslock/focuslock_*.py
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/install -D -m 0644 /*/lion_pubkey.pem /opt/focuslock/lion_pubkey.pem
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/install -D -m 0644 /*/collar-icon*.png /opt/focuslock/collar-icon*.png
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/install -D -m 0644 /*/crown-*.png /opt/focuslock/crown-*.png
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/install -D -m 0644 /*/web/index.html /opt/focuslock/web/index.html
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart focuslock-desktop.service
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart focuslock-tray.service
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart focuslock-bridge.service
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart focuslock-mail.service
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart claude-standing-orders-sync.service
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/mkdir -p /opt/focuslock
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/mkdir -p /opt/focuslock/web
SUDOERS_EOF
sudo chmod 440 "$SUDOERS_FILE"
echo "  Sudoers rule installed: $SUDOERS_FILE"

# Standing orders (homelab-only — pulls Claude Code config from operator homelab)
if [ -n "$HOMELAB" ]; then
    echo ""
    echo "=== Installing standing orders ==="
    FOCUSLOCK_HOMELAB="$HOMELAB" bash "$SCRIPT_DIR/install-standing-orders.sh" || \
        echo "  standing-orders sync failed (non-fatal — collar daemon still installed)"
fi

echo ""
echo "=== Desktop collar installed ==="
echo ""
if [ -n "$HOMELAB" ]; then
    echo "  Homelab: $HOMELAB"
fi
echo "  Status:  systemctl --user status focuslock-desktop"
echo "  Logs:    journalctl --user -u focuslock-desktop -f"
echo ""
echo "  Lock method: loginctl (compositor-native, inescapable)"
echo "  Wallpaper: custom cairo-generated lock screen"
if [ -n "$HOMELAB" ]; then
    echo "  Heartbeat: every 30s to homelab"
    echo "  Standing orders: synced from homelab every 5 min"
else
    echo "  Mesh: configure ~/.config/focuslock/config.json (mesh_url + mesh_id + vault_mode)"
    echo "        then restart: systemctl --user restart focuslock-desktop"
fi
echo ""

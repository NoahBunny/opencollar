#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Install FocusLock Desktop Collar
# Works on: Fedora, Bazzite, Ubuntu, Garuda (Arch), any systemd + Wayland/KDE
# Mirrors phone lock state to desktop via loginctl session lock
set -e

echo "=== FocusLock Desktop Collar Installer ==="
echo ""

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
    curl -sL "https://github.com/googlefonts/lexend/raw/main/fonts/variable/Lexend%5BHEXP%2Cwght%5D.ttf" \
        -o ~/.local/share/fonts/l/Lexend.ttf 2>/dev/null && fc-cache -f ~/.local/share/fonts/ 2>/dev/null
fi

# Install files
echo ""
echo "=== Installing daemon ==="

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
sudo mkdir -p /opt/focuslock
sudo cp "$PROJECT_DIR/focuslock-desktop.py" /opt/focuslock/
sudo cp "$PROJECT_DIR/focuslock_mesh.py" /opt/focuslock/
sudo cp "$PROJECT_DIR/focuslock-tray.py" /opt/focuslock/ 2>/dev/null || true
sudo cp "$PROJECT_DIR/icons/collar-icon.png" /opt/focuslock/ 2>/dev/null || \
    sudo cp "$PROJECT_DIR/collar-icon.png" /opt/focuslock/ 2>/dev/null || true
sudo chmod 755 /opt/focuslock/focuslock-desktop.py /opt/focuslock/focuslock-tray.py 2>/dev/null

# Install crown tray icons
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
sudo mkdir -p /opt/focuslock/web
if [ -f "$PROJECT_DIR/web/index.html" ]; then
    sudo cp "$PROJECT_DIR/web/index.html" /opt/focuslock/web/
    echo "  Web UI installed"
fi

# Install shared config module
sudo cp "$PROJECT_DIR/shared/focuslock_config.py" /opt/focuslock/ 2>/dev/null || true

# Tray autostart
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/focuslock-tray.desktop << 'TRAYEOF'
[Desktop Entry]
Type=Application
Name=FocusLock Tray
Exec=/usr/bin/python3 /opt/focuslock/focuslock-tray.py
Hidden=false
NoDisplay=true
X-GNOME-Autostart-enabled=true
X-KDE-autostart-after=panel
TRAYEOF

# Install systemd user service
echo "=== Installing systemd user service ==="
mkdir -p ~/.config/systemd/user

# Detect homelab — try env var, then Tailscale discovery, then prompt
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
    read -p "Homelab URL (e.g. http://x.x.x.x:8434): " HOMELAB
    if [ -z "$HOMELAB" ]; then
        echo "ERROR: Homelab URL is required."
        exit 1
    fi
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
Environment=FOCUSLOCK_HOMELAB=$HOMELAB

[Install]
WantedBy=graphical-session.target
EOF

# Enable and start
systemctl --user daemon-reload
systemctl --user enable focuslock-desktop.service
systemctl --user start focuslock-desktop.service

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

# Sudoers: allow passwordless deploy to /opt/focuslock/ and service restarts
# This lets Claude Code deploy updates without requiring interactive sudo
echo "=== Configuring sudoers for FocusLock deployment ==="
SUDOERS_FILE="/etc/sudoers.d/focuslock"
CURRENT_USER="$(whoami)"
cat << SUDOERS_EOF | sudo tee "$SUDOERS_FILE" >/dev/null
# FocusLock — allow collar deployment without password
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/cp * /opt/focuslock/*
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart *focuslock*
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/systemctl restart *claude*
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/mkdir -p /opt/focuslock
$CURRENT_USER ALL=(root) NOPASSWD: /usr/bin/chmod * /opt/focuslock/*
SUDOERS_EOF
sudo chmod 440 "$SUDOERS_FILE"
echo "  Sudoers rule installed: $SUDOERS_FILE"

# Standing orders
echo ""
echo "=== Installing standing orders ==="
FOCUSLOCK_HOMELAB="$HOMELAB" bash "$SCRIPT_DIR/install-standing-orders.sh"

echo ""
echo "=== Desktop collar installed ==="
echo ""
echo "  Homelab: $HOMELAB"
echo "  Status:  systemctl --user status focuslock-desktop"
echo "  Logs:    journalctl --user -u focuslock-desktop -f"
echo ""
echo "  Lock method: loginctl (compositor-native, inescapable)"
echo "  Wallpaper: custom cairo-generated lock screen"
echo "  Heartbeat: every 30s to homelab"
echo "  Standing orders: synced from homelab every 5 min"
echo ""

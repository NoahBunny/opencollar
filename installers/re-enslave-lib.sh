# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Shared library for re-enslave-*.sh scripts.
# Source this with: source "$(dirname "$0")/re-enslave-lib.sh"
# shellcheck shell=bash

# ── Version constants ──
# Update these when bumping APK / desktop versions. The watcher daemon and
# all sub-scripts read from here so there's exactly one place to change.
TARGET_SLAVE_VERSIONCODE=54
TARGET_SLAVE_APK="focuslock-v54.apk"
TARGET_CONTROLLER_VERSIONCODE=59
TARGET_CONTROLLER_APK="focusctl-v59.apk"
TARGET_COMPANION_VERSIONCODE=41
TARGET_COMPANION_APK="bunnytasker-v41.apk"

# Server-side files that re-enslave-server.sh deploys
SERVER_FILES=(
    "focuslock-mail.py"
    "focuslock_mesh.py"
)
SERVER_ICON="collar-icon.png"

# Desktop collar files that re-enslave-desktops.sh deploys
DESKTOP_FILES=(
    "focuslock-desktop.py"
    "focuslock_mesh.py"
    "focuslock-tray.py"
)

# ── Logging helpers ──
RE_LOG_PREFIX="${RE_LOG_PREFIX:-re-enslave}"
log() { printf '[%s] %s\n' "$RE_LOG_PREFIX" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$RE_LOG_PREFIX" "$*" >&2; }
fail() { printf '[%s] ERROR: %s\n' "$RE_LOG_PREFIX" "$*" >&2; exit 1; }
section() { printf '\n=== %s ===\n' "$*"; }

# ── Path discovery ──
# Find the canonical Nextcloud root + project subdirs. Sets:
#   NC          — Nextcloud root
#   FL          — FocusLock dir (Android source)
#   LS          — Lion's Share + Bunny Tasker dir (Python server, icons, installers)
#   ICONS       — $LS/icons
#   APKS        — $LS/apks (or $FL/apks as fallback)
discover_paths() {
    NC=""
    for p in ~/Nextcloud ~/rclone_mounts/Nextcloud /mnt/CargoBay8/NC-BFC; do
        if [ -d "$p/Scripts/FocusLock" ] || [ -d "$p/Scripts/Lion's Share + Bunny Tasker" ]; then
            NC="$p"
            break
        fi
    done
    [ -z "$NC" ] && fail "Nextcloud not found. Checked ~/Nextcloud, ~/rclone_mounts/Nextcloud, /mnt/CargoBay8/NC-BFC."

    FL="$NC/Scripts/FocusLock"
    LS="$NC/Scripts/Lion's Share + Bunny Tasker"
    ICONS="$LS/icons"
    if [ -d "$FL/apks" ]; then APKS="$FL/apks"
    elif [ -d "$LS/apks" ]; then APKS="$LS/apks"
    else APKS="$HOME/Desktop"
    fi

    [ -d "$LS" ] || fail "Lion's Share + Bunny Tasker not found at: $LS"
}

# ── Paywall check (standing-orders enforcement) ──
# Refuses to run if any non-zero paywall is set on the mesh. Same logic Claude
# uses — re-enslave is a tool, tools should obey the mesh too.
#
# Self-sufficient: if FOCUSLOCK_MESH_URL isn't in the environment yet, we
# source ~/.config/focuslock/re-enslave.config ourselves before checking. This
# lets callers do `check_paywall` first without having to remember to call
# `load_config` ahead of it (the previous order was a foot-gun in all 4
# callers).
check_paywall() {
    if [ -z "${FOCUSLOCK_MESH_URL:-}" ] && [ -f "$HOME/.config/focuslock/re-enslave.config" ]; then
        # shellcheck disable=SC1090
        source "$HOME/.config/focuslock/re-enslave.config"
    fi
    local mesh_url="${FOCUSLOCK_MESH_URL:-}"
    if [ -z "$mesh_url" ]; then
        fail "FOCUSLOCK_MESH_URL is unset. Set it in ~/.config/focuslock/re-enslave.config (see installers/re-enslave.config.example)."
    fi
    local pw
    local admin_token="${FOCUSLOCK_ADMIN_TOKEN:-}"
    if [ -z "$admin_token" ]; then
        # Try to read from config.json
        admin_token=$(python3 -c 'import json; print(json.load(open("'$HOME'/.config/focuslock/config.json")).get("admin_token",""))' 2>/dev/null || true)
    fi
    local status_url="$mesh_url/admin/status?admin_token=$admin_token"
    pw=$(curl -s --max-time 5 "$status_url" 2>/dev/null \
        | python3 -c 'import sys,json
try: d=json.load(sys.stdin)
except: print("",end=""); sys.exit(0)
o=d.get("orders",{}) or {}
print(o.get("paywall","") or "")' 2>/dev/null)
    if [ -n "$pw" ] && [ "$pw" != "0" ] && [ "$pw" != "null" ]; then
        fail "Paywall is \$$pw — re-enslave refuses to run until it clears."
    fi
}

# ── Per-user config loader ──
# Reads ~/.config/focuslock/re-enslave.config if present. This is where users
# put hostnames, IPs, and SSH targets that aren't safe to commit to git.
RE_CONFIG="$HOME/.config/focuslock/re-enslave.config"
load_config() {
    if [ -f "$RE_CONFIG" ]; then
        # shellcheck disable=SC1090
        source "$RE_CONFIG"
        log "Loaded config: $RE_CONFIG"
    fi
}

# ── Homelab address resolution ──
# Tries (in order): $FOCUSLOCK_HOMELAB_SSH env, Tailscale lookup of
# $FOCUSLOCK_HOMELAB_HOST. Set one of these in ~/.config/focuslock/re-enslave.config.
resolve_homelab_ssh() {
    if [ -n "$FOCUSLOCK_HOMELAB_SSH" ]; then
        echo "$FOCUSLOCK_HOMELAB_SSH"
        return 0
    fi
    if command -v tailscale &>/dev/null && [ -n "${FOCUSLOCK_HOMELAB_HOST:-}" ]; then
        local ts
        ts=$(tailscale status 2>/dev/null | awk -v h="$FOCUSLOCK_HOMELAB_HOST" '$2==h {print $1; exit}')
        if [ -n "$ts" ]; then echo "$ts"; return 0; fi
    fi
    return 1
}

# ── Phone target parser ──
# Reads PHONE_TARGETS array from the config (set by load_config). Each entry:
#   "name:adb_address:role"
#   role ∈ {bunny, lion}
# Example: "bunny-phone:192.0.2.42:33583:bunny"
# Outputs (one per line, tab-separated): name<TAB>address<TAB>role
list_phone_targets() {
    if [ "${#PHONE_TARGETS[@]}" -eq 0 ]; then
        return 0
    fi
    local entry name addr role rest
    for entry in "${PHONE_TARGETS[@]}"; do
        # Split on the LAST colon to separate role; the address may contain
        # a colon (host:port) so we can't naively split on first :
        role="${entry##*:}"
        rest="${entry%:*}"
        addr="${rest#*:}"
        name="${rest%%:*}"
        printf '%s\t%s\t%s\n' "$name" "$addr" "$role"
    done
}

# ── ADB helpers ──
# Adb server port — the homelab typically runs on 15037 to avoid clashing with
# a local desktop adb (default 5037). Override via ANDROID_ADB_SERVER_PORT.
adb_cmd() {
    if [ -n "${ANDROID_ADB_SERVER_PORT:-}" ]; then
        ANDROID_ADB_SERVER_PORT="$ANDROID_ADB_SERVER_PORT" adb "$@"
    else
        adb "$@"
    fi
}

# Returns the installed versionCode for $1 on the device $ADB_DEV (e.g.
# 192.0.2.42:33583). Empty string on error or not-installed.
get_installed_version_code() {
    local pkg="$1"
    adb_cmd -s "$ADB_DEV" shell "dumpsys package $pkg 2>/dev/null | grep -m1 versionCode" 2>/dev/null \
        | sed -nE 's/.*versionCode=([0-9]+).*/\1/p'
}

# Try connecting adb to the device. Returns 0 if `adb devices` shows it as
# `device`, 1 otherwise. Sets $ADB_DEV as a side-effect.
adb_try_connect() {
    ADB_DEV="$1"
    adb_cmd disconnect "$ADB_DEV" >/dev/null 2>&1 || true
    adb_cmd connect "$ADB_DEV" 2>&1 | grep -qE 'connected|already connected' || return 1
    sleep 1
    adb_cmd devices 2>/dev/null | awk -v d="$ADB_DEV" '$1==d && $2=="device" {found=1} END {exit !found}'
}

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Shared library for re-enslave-*.sh scripts.
# Source this with: source "$(dirname "$0")/re-enslave-lib.sh"
# shellcheck shell=bash

# ── Version constants ──
# Update these when bumping APK / desktop versions. The watcher daemon and
# all sub-scripts read from here so there's exactly one place to change.
TARGET_SLAVE_VERSIONCODE=85
TARGET_SLAVE_APK="focuslock-v85.apk"
TARGET_CONTROLLER_VERSIONCODE=84
TARGET_CONTROLLER_APK="focusctl-v84.apk"
TARGET_COMPANION_VERSIONCODE=68
TARGET_COMPANION_APK="bunnytasker-v68.apk"

# Server-side files that re-enslave-server.sh deploys
SERVER_FILES=(
    "focuslock-mail.py"
    "focuslock_mesh.py"
    "focuslock_ntfy.py"
)
SERVER_ICON="collar-icon.png"

# Desktop collar files that re-enslave-desktops.sh deploys.
# focuslock_ntfy.py is also deployed via the shared/focuslock_*.py glob in
# re-enslave-desktops.sh — listed explicitly here so the entrypoint deploy
# loop picks it up even on installs that skip the shared glob.
# focuslock-tray.py is NOT covered by that glob (hyphen, not underscore), so
# leaving it out of this list meant the crown was never deployed by a
# re-enslave at all — not locally, and not pushed to peers either, since the
# remote loop builds its file list from this same array. Every tray fix since
# the original install-desktop-collar.sh run stayed on the operator's disk
# while the header comment claimed otherwise.
DESKTOP_FILES=(
    "focuslock-desktop.py"
    "focuslock-tray.py"
    "focuslock_mesh.py"
    "focuslock_ntfy.py"
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
#   APKS        — $LS/apks (preferred) or $FL/apks (legacy fallback)
discover_paths() {
    # Explicit source override, from the environment or re-enslave.config.
    # Autodiscovery picks the *Nextcloud* copy, which on a machine with more
    # than one checkout is not necessarily the tree the operator has been
    # working in — and a re-enslave silently sourced from a stale checkout
    # rolls production back to whatever that copy last held. Set FOCUSLOCK_SRC
    # to the checkout you actually mean and this stops being a coin flip.
    if [ -n "${FOCUSLOCK_SRC:-}" ]; then
        [ -d "$FOCUSLOCK_SRC" ] || fail "FOCUSLOCK_SRC is not a directory: $FOCUSLOCK_SRC"
        [ -f "$FOCUSLOCK_SRC/focuslock-mail.py" ] || \
            fail "FOCUSLOCK_SRC does not look like the project (no focuslock-mail.py): $FOCUSLOCK_SRC"
        LS="$FOCUSLOCK_SRC"
        NC="$(dirname "$(dirname "$LS")")"
        FL="$NC/Scripts/FocusLock"
        ICONS="$LS/icons"
        if [ -d "$LS/apks" ]; then APKS="$LS/apks"
        elif [ -d "$FL/apks" ]; then APKS="$FL/apks"
        else APKS="$_REAL_HOME/Desktop"
        fi
        log "Source override: FOCUSLOCK_SRC=$LS"
        return 0
    fi

    # Gather every candidate rather than stopping at the first: ~/Desktop is a
    # normal place to keep the working checkout, and a machine that has both it
    # and a Nextcloud copy gets whichever this list names first. FOCUSLOCK_SRC
    # above is the fix and it returns before we ever reach here — this only
    # runs unpinned, so say out loud which tree won and what it beat. Warn
    # rather than fail: one checkout is still the common case, and a deploy
    # that refuses to run is worse than one that announces its choice.
    local _candidates=()
    NC=""
    for p in "$_REAL_HOME/Nextcloud" "$_REAL_HOME/rclone_mounts/Nextcloud" \
             /mnt/CargoBay8/NC-BFC "$_REAL_HOME/Desktop"; do
        if [ -d "$p/Scripts/FocusLock" ] || [ -d "$p/Scripts/Lion's Share + Bunny Tasker" ]; then
            [ -z "$NC" ] && NC="$p"
            _candidates+=("$p")
        fi
    done
    [ -z "$NC" ] && fail "Project not found. Checked ~/Nextcloud, ~/rclone_mounts/Nextcloud, /mnt/CargoBay8/NC-BFC, ~/Desktop."

    if [ "${#_candidates[@]}" -gt 1 ]; then
        warn "More than one checkout present and FOCUSLOCK_SRC is not pinned."
        warn "  deploying from: $NC"
        for p in "${_candidates[@]}"; do
            [ "$p" = "$NC" ] || warn "  ignoring:       $p"
        done
        warn "  pin FOCUSLOCK_SRC in ~/.config/focuslock/re-enslave.config to choose deliberately."
    fi

    FL="$NC/Scripts/FocusLock"
    LS="$NC/Scripts/Lion's Share + Bunny Tasker"
    ICONS="$LS/icons"
    # APK location preference: LS/apks first (current build output target),
    # FL/apks as fallback for legacy machines. Historically FL/apks was
    # canonical per project_source_layout.md but recent build workflow writes
    # version-bumped APKs into LS/apks instead — preferring LS/apks self-heals
    # the drift so re-enslave-phones.sh picks up the freshest file.
    if [ -d "$LS/apks" ]; then APKS="$LS/apks"
    elif [ -d "$FL/apks" ]; then APKS="$FL/apks"
    else APKS="$_REAL_HOME/Desktop"
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
    # Try both $HOME and the invoking user's home (sudo changes $HOME to /root)
    local _real_home="${SUDO_USER:+$(eval echo "~$SUDO_USER")}"
    _real_home="${_real_home:-$HOME}"
    if [ -z "${FOCUSLOCK_MESH_URL:-}" ] && [ -f "$_real_home/.config/focuslock/re-enslave.config" ]; then
        # shellcheck disable=SC1090
        source "$_real_home/.config/focuslock/re-enslave.config"
    elif [ -z "${FOCUSLOCK_MESH_URL:-}" ] && [ -f "$HOME/.config/focuslock/re-enslave.config" ]; then
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
# Use SUDO_USER's home when running under sudo (sudo changes $HOME to /root).
_REAL_HOME="${SUDO_USER:+$(eval echo "~${SUDO_USER}")}"
_REAL_HOME="${_REAL_HOME:-$HOME}"
RE_CONFIG="$_REAL_HOME/.config/focuslock/re-enslave.config"
load_config() {
    if [ -f "$RE_CONFIG" ]; then
        # shellcheck disable=SC1090
        source "$RE_CONFIG"
        # Sourcing sets shell variables, which child processes do not inherit —
        # so install-standing-orders.sh (invoked as a separate bash) saw none of
        # this and failed with "Set FOCUSLOCK_HOMELAB…" on a machine whose
        # config named the relay perfectly well. Export the ones sub-scripts read.
        export FOCUSLOCK_SRC FOCUSLOCK_MESH_URL FOCUSLOCK_ADMIN_TOKEN \
               FOCUSLOCK_HOMELAB FOCUSLOCK_HOMELAB_SSH FOCUSLOCK_HOMELAB_HOST DEPLOY_USER
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

#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
export ANDROID_ADB_SERVER_PORT=15037
# FocusLock Bridge — multi-device enforcement via ADB
# Manages multiple phones with per-device state

POLL=2
DEVICE_REGISTRY="/run/focuslock/devices.json"

# ── Device definitions (fallback if no registry) ──
# Format: NAME|LAN_IP|ADB_PORT[|TAILSCALE_IP]
DEFAULT_DEVICES=(
    # Add your devices here: "NAME|LAN_IP|ADB_PORT[|TAILSCALE_IP]"
    # Example: "pixel|192.168.1.50|5555|100.x.y.z"
)

# ── Per-device state (bash associative arrays) ──
declare -A DEV_TARGET       # current ADB target string (ip:port)
declare -A DEV_LOCKED       # was_locked flag
declare -A DEV_FAILSAFE     # failsafe counter
declare -A DEV_LAUNCHER     # detected default launcher activity
declare -A DEV_LAUNCHER_PKG # launcher package name
declare -A DEV_IN_CALL      # call state tracking
declare -A DEV_RECORD_TRY   # auto-record attempt counter
declare -A DEV_LAN_IP
declare -A DEV_TS_IP
declare -A DEV_ADB_PORT
DEV_NAMES=()     # ordered device name list (indexed array, NOT associative)

# ── Load devices from registry or defaults ──
load_devices() {
    DEV_NAMES=()

    if [ -f "$DEVICE_REGISTRY" ]; then
        local names
        names=$(python3 -c "
import json, sys
d = json.load(open('$DEVICE_REGISTRY'))
for name, cfg in d.items():
    print(f\"{name}|{cfg.get('lan_ip','')}|{cfg.get('adb_port','5555')}|{cfg.get('tailscale_ip','')}\")
" 2>/dev/null)
        if [ -n "$names" ]; then
            while IFS= read -r line; do
                _init_device "$line"
            done <<< "$names"
            return
        fi
    fi

    # Fallback to defaults
    for entry in "${DEFAULT_DEVICES[@]}"; do
        _init_device "$entry"
    done
}

_init_device() {
    local entry="$1"
    IFS='|' read -r name lan_ip adb_port ts_ip <<< "$entry"
    [ -z "$name" ] && return

    DEV_NAMES+=("$name")
    DEV_LAN_IP[$name]="$lan_ip"
    DEV_ADB_PORT[$name]="${adb_port:-5555}"
    DEV_TS_IP[$name]="$ts_ip"

    # Initialize state only if not already set (preserve across registry reload)
    [ -z "${DEV_LOCKED[$name]+x}" ] && DEV_LOCKED[$name]=0
    [ -z "${DEV_FAILSAFE[$name]+x}" ] && DEV_FAILSAFE[$name]=0
    [ -z "${DEV_IN_CALL[$name]+x}" ] && DEV_IN_CALL[$name]=0
    [ -z "${DEV_RECORD_TRY[$name]+x}" ] && DEV_RECORD_TRY[$name]=0
}

# ── ADB helpers ──
adb_dev() {
    local name="$1"; shift
    adb -s "${DEV_TARGET[$name]}" shell "$@" 2>/dev/null
}

reconnect_device() {
    local name="$1"
    local lan="${DEV_LAN_IP[$name]}"
    local ts="${DEV_TS_IP[$name]}"
    local port="${DEV_ADB_PORT[$name]}"

    for ip in $lan $ts; do
        [ -z "$ip" ] && continue
        adb connect "$ip:$port" 2>/dev/null
        if adb -s "$ip:$port" shell echo ok 2>/dev/null | grep -q ok; then
            DEV_TARGET[$name]="$ip:$port"
            echo "[$(date +%H:%M:%S)] [$name] Connected: $ip:$port"

            # Detect launcher (once per connection)
            if [ -z "${DEV_LAUNCHER[$name]}" ]; then
                local launcher
                launcher=$(adb -s "$ip:$port" shell cmd package resolve-activity --brief -c android.intent.category.HOME 2>/dev/null | tail -1 | tr -d '\r')
                if [ -z "$launcher" ] || echo "$launcher" | grep -q focuslock; then
                    launcher="com.android.launcher3/.uioverrides.QuickstepLauncher"
                fi
                DEV_LAUNCHER[$name]="$launcher"
                DEV_LAUNCHER_PKG[$name]=$(echo "$launcher" | cut -d'/' -f1)
                echo "[$(date +%H:%M:%S)] [$name] Launcher: $launcher (pkg: ${DEV_LAUNCHER_PKG[$name]})"
            fi

            # Check current state on connect
            local flag
            flag=$(adb -s "$ip:$port" shell settings get global focus_lock_active 2>/dev/null)
            if [ "$flag" = "0" ] || [ "$flag" = "null" ] || [ -z "$flag" ]; then
                echo "[$(date +%H:%M:%S)] [$name] Unlocked on connect — ensuring launcher"
                adb -s "$ip:$port" shell cmd statusbar disable-for-setup false 2>/dev/null
                adb -s "$ip:$port" shell pm enable --user 0 "${DEV_LAUNCHER_PKG[$name]}" 2>/dev/null
                adb -s "$ip:$port" shell settings put global user_switcher_enabled 1 2>/dev/null
                DEV_LOCKED[$name]=0
            else
                DEV_LOCKED[$name]=1
            fi
            return 0
        fi
    done

    DEV_TARGET[$name]=""
    return 1
}

# ── Auto-record (per-device) ──
try_auto_record() {
    local name="$1"
    adb_dev "$name" uiautomator dump /data/local/tmp/ui_dump.xml >/dev/null 2>&1
    local btn
    btn=$(adb_dev "$name" cat /data/local/tmp/ui_dump.xml 2>/dev/null | grep -o 'resource-id="com.android.dialer:id/record_button"[^/]*')
    adb_dev "$name" rm -f /data/local/tmp/ui_dump.xml 2>/dev/null

    [ -z "$btn" ] && return 1
    echo "$btn" | grep -q 'checked="true"' && return 0

    local bounds left top right bottom
    bounds=$(echo "$btn" | grep -o 'bounds="\[[0-9]*,[0-9]*\]\[[0-9]*,[0-9]*\]"')
    left=$(echo "$bounds" | sed 's/.*\[\([0-9]*\),.*/\1/')
    top=$(echo "$bounds" | sed 's/.*,\([0-9]*\)\]\[.*/\1/')
    right=$(echo "$bounds" | sed 's/.*\]\[\([0-9]*\),.*/\1/')
    bottom=$(echo "$bounds" | sed 's/.*,\([0-9]*\)\]".*/\1/')

    if [ -n "$left" ] && [ -n "$top" ] && [ -n "$right" ] && [ -n "$bottom" ]; then
        local cx=$(( (left + right) / 2 ))
        local cy=$(( (top + bottom) / 2 ))
        echo "[$(date +%H:%M:%S)] [$name] AUTO-RECORD: Tapping ($cx, $cy)"
        adb_dev "$name" input tap $cx $cy
        return 0
    fi
    return 1
}

# ── Per-device poll cycle ──
poll_device() {
    local name="$1"
    local target="${DEV_TARGET[$name]}"
    local launcher_pkg="${DEV_LAUNCHER_PKG[$name]}"
    local default_launcher="${DEV_LAUNCHER[$name]}"

    # Not connected? Try reconnect
    if [ -z "$target" ]; then
        reconnect_device "$name"
        [ -z "${DEV_TARGET[$name]}" ] && return
    fi

    # Read lock flag
    local flag
    flag=$(adb_dev "$name" settings get global focus_lock_active)

    if [ -z "$flag" ]; then
        echo "[$(date +%H:%M:%S)] [$name] Connection lost"
        DEV_TARGET[$name]=""
        DEV_LAUNCHER[$name]=""
        DEV_LAUNCHER_PKG[$name]=""
        return
    fi

    # Lock/unlock transitions
    if [ "$flag" = "1" ] && [ "${DEV_LOCKED[$name]}" = "0" ]; then
        echo "[$(date +%H:%M:%S)] [$name] LOCKING"
        adb_dev "$name" cmd statusbar disable-for-setup true
        adb_dev "$name" pm disable-user --user 0 "$launcher_pkg"
        adb_dev "$name" settings put global user_switcher_enabled 0
        adb_dev "$name" am start -n com.focuslock/.FocusActivity
        DEV_LOCKED[$name]=1
        DEV_FAILSAFE[$name]=0
    elif [ "$flag" = "0" ] && [ "${DEV_LOCKED[$name]}" = "1" ]; then
        echo "[$(date +%H:%M:%S)] [$name] UNLOCKING"
        adb_dev "$name" cmd statusbar disable-for-setup false
        adb_dev "$name" pm enable --user 0 "$launcher_pkg"
        adb_dev "$name" settings put global user_switcher_enabled 1
        adb_dev "$name" cmd package set-home-activity "$default_launcher"
        adb_dev "$name" input keyevent KEYCODE_HOME
        # Restore saved volumes per-stream
        local saved_music saved_ring saved_notif
        saved_music=$(adb_dev "$name" settings get global focus_lock_saved_volume)
        saved_ring=$(adb_dev "$name" settings get global focus_lock_saved_volume_ring)
        saved_notif=$(adb_dev "$name" settings get global focus_lock_saved_volume_notif)
        [ -n "$saved_music" ] && [ "$saved_music" != "null" ] && [ "$saved_music" -gt 0 ] 2>/dev/null && adb_dev "$name" media volume --stream 3 --set "$saved_music" 2>/dev/null
        [ -n "$saved_ring" ] && [ "$saved_ring" != "null" ] && [ "$saved_ring" -gt 0 ] 2>/dev/null && adb_dev "$name" media volume --stream 2 --set "$saved_ring" 2>/dev/null
        [ -n "$saved_notif" ] && [ "$saved_notif" != "null" ] && [ "$saved_notif" -gt 0 ] 2>/dev/null && adb_dev "$name" media volume --stream 5 --set "$saved_notif" 2>/dev/null
        echo "[$(date +%H:%M:%S)] [$name] Volumes restored"
        DEV_LOCKED[$name]=0
        DEV_FAILSAFE[$name]=0
    fi

    # Bridge heartbeat
    adb_dev "$name" settings put global focus_lock_bridge_heartbeat "$(date +%s)000" 2>/dev/null

    # Bunny Tasker enforcement
    local bunny_disabled
    bunny_disabled=$(adb_dev "$name" pm list packages -d 2>/dev/null | grep bunnytasker)
    if [ -n "$bunny_disabled" ]; then
        echo "[$(date +%H:%M:%S)] [$name] TAMPER: Bunny Tasker disabled — re-enabling"
        adb_dev "$name" pm enable --user 0 com.bunnytasker 2>/dev/null
        adb_dev "$name" dpm set-active-admin com.bunnytasker/.AdminReceiver 2>/dev/null
    fi
    local bunny_check
    bunny_check=$(adb_dev "$name" pm list packages -e 2>/dev/null | grep bunnytasker)
    if [ -z "$bunny_check" ]; then
        echo "[$(date +%H:%M:%S)] [$name] WARNING: Bunny Tasker not installed!"
    fi

    # ControlService integrity check
    local focuslock_service
    focuslock_service=$(adb_dev "$name" dumpsys activity services com.focuslock 2>/dev/null | grep ServiceRecord)
    if [ -z "$focuslock_service" ]; then
        echo "[$(date +%H:%M:%S)] [$name] TAMPER: ControlService not running — restarting"
        adb_dev "$name" am start-foreground-service -n com.focuslock/.ControlService 2>/dev/null
    fi

    # Failsafe: every ~60s, if unlocked, ensure launcher is enabled
    DEV_FAILSAFE[$name]=$(( ${DEV_FAILSAFE[$name]} + 1 ))
    if [ "${DEV_FAILSAFE[$name]}" -ge 30 ]; then
        DEV_FAILSAFE[$name]=0
        if [ "$flag" = "0" ] || [ "$flag" = "null" ]; then
            local launcher_state
            launcher_state=$(adb_dev "$name" pm list packages -d 2>/dev/null | grep "$launcher_pkg")
            if [ -n "$launcher_state" ]; then
                echo "[$(date +%H:%M:%S)] [$name] FAILSAFE: Launcher disabled while unlocked — re-enabling"
                adb_dev "$name" cmd statusbar disable-for-setup false
                adb_dev "$name" pm enable --user 0 "$launcher_pkg"
                adb_dev "$name" settings put global user_switcher_enabled 1
                adb_dev "$name" cmd package set-home-activity "$default_launcher"
                adb_dev "$name" input keyevent KEYCODE_HOME
            fi
        fi
    fi

    # Auto-record: monitor call state
    local call_state
    call_state=$(adb_dev "$name" dumpsys telephony.registry 2>/dev/null | grep -m1 'mCallState=' | tr -d '[:space:]')
    if [ "$call_state" = "mCallState=2" ]; then
        if [ "${DEV_IN_CALL[$name]}" = "0" ]; then
            DEV_IN_CALL[$name]=1
            DEV_RECORD_TRY[$name]=0
            echo "[$(date +%H:%M:%S)] [$name] Call detected — will attempt auto-record"
        fi
        if [ "${DEV_RECORD_TRY[$name]}" -lt 5 ]; then
            if try_auto_record "$name"; then
                DEV_RECORD_TRY[$name]=5
            else
                DEV_RECORD_TRY[$name]=$(( ${DEV_RECORD_TRY[$name]} + 1 ))
            fi
        fi
    else
        if [ "${DEV_IN_CALL[$name]}" = "1" ]; then
            echo "[$(date +%H:%M:%S)] [$name] Call ended"
        fi
        DEV_IN_CALL[$name]=0
        DEV_RECORD_TRY[$name]=0
    fi
}

# ── Main ──
load_devices

echo "FocusLock Bridge (multi-device)"
echo "  Device count: ${#DEV_NAMES[@]}"
echo "  Devices: ${DEV_NAMES[*]}"
for name in "${DEV_NAMES[@]}"; do
    echo "    $name: LAN=${DEV_LAN_IP[$name]}:${DEV_ADB_PORT[$name]} TS=${DEV_TS_IP[$name]}"
done
echo "  Polling every ${POLL}s"
echo ""

# Initial connect attempt for all devices
for name in "${DEV_NAMES[@]}"; do
    reconnect_device "$name"
done

registry_counter=0

while true; do
    # Poll each device
    for name in "${DEV_NAMES[@]}"; do
        poll_device "$name"
    done

    # Reload device registry every ~60s (allows hot-adding devices)
    registry_counter=$((registry_counter + 1))
    if [ "$registry_counter" -ge 30 ]; then
        registry_counter=0
        load_devices
    fi

    sleep $POLL
done

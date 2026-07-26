#!/usr/bin/env bash
# device-qa-waydroid.sh
# ---------------------------------------------------------------------------
# On-device QA for the covert-removal + safety-floor work, in waydroid.
# Validates what CAN be checked programmatically; prints a PASS/FAIL summary
# and flags the interactive items (pairing / safeword UI / bridge re-lock)
# that need a human or a separate harness.
#
# Run as your NORMAL user (NOT root). waydroid session + app install are
# user-level; only the on-device shell probes use sudo, so you'll be prompted
# for a password once. Re-running is safe.
#
# Usage:
#   bash scripts/device-qa-waydroid.sh                 # session + install + checks
#   bash scripts/device-qa-waydroid.sh --no-install    # skip install (already installed)
#   bash scripts/device-qa-waydroid.sh --device-owner  # ALSO set com.focuslock as
#                                                       # device-owner and re-check that
#                                                       # factory reset stays available
#   bash scripts/device-qa-waydroid.sh --static-only   # APK permission audit only, no device
# ---------------------------------------------------------------------------
set -uo pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
ANDROID_SDK="${ANDROID_SDK:-$HOME/android-sdk}"
SLAVE_APK="${SLAVE_APK:-$REPO_DIR/android/slave/focuslock-signed.apk}"
LION_APK="${LION_APK:-$REPO_DIR/android/controller/focusctl-signed.apk}"
BUNNY_APK="${BUNNY_APK:-$REPO_DIR/android/companion/bunnytasker-signed.apk}"
ADMIN_COMPONENT="com.focuslock/.AdminReceiver"

DO_INSTALL=1; DO_DEVICE=1; DO_DEVICE_OWNER=0
for arg in "$@"; do
    case "$arg" in
        --no-install)   DO_INSTALL=0 ;;
        --device-owner) DO_DEVICE_OWNER=1 ;;
        --static-only)  DO_DEVICE=0; DO_INSTALL=0 ;;
        -h|--help) grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

c_b="\033[1m"; c_g="\033[1;32m"; c_y="\033[1;33m"; c_r="\033[1;31m"; c_0="\033[0m"
say()  { echo -e "\n${c_b}==>${c_0} $*"; }
PASS=0; FAIL=0; INFOC=0
pass() { echo -e "  ${c_g}PASS${c_0} $*"; PASS=$((PASS+1)); }
fail() { echo -e "  ${c_r}FAIL${c_0} $*"; FAIL=$((FAIL+1)); }
info() { echo -e "  ${c_y}INFO${c_0} $*"; INFOC=$((INFOC+1)); }

if [ "$(id -u)" = 0 ]; then
    echo "Run as your normal user, not root (waydroid session/app install are user-level)." >&2
    exit 1
fi

# Resolve aapt2 from any installed build-tools.
AAPT2=""
for v in 35.0.0 36.0.0; do
    [ -x "$ANDROID_SDK/build-tools/$v/aapt2" ] && AAPT2="$ANDROID_SDK/build-tools/$v/aapt2" && break
done

# ── on-device shell helper (root, via sudo) ────────────────────────────────
# `waydroid shell` needs root. Pass the command as DIRECT args (not `sh -c` —
# that dropped /system/bin from PATH so pm/dumpsys silently returned nothing).
# Strip the CRs waydroid's pty adds.
wsh() { sudo waydroid shell "$@" 2>/dev/null | tr -d '\r'; }

# The container idles into a FROZEN state; the first shell call thaws it and can
# come back empty. Poll `pm list packages` until it returns real data, so no
# downstream check ever interprets an empty (frozen) shell as a real result.
warm_up_device() {
    local out i
    for i in $(seq 1 15); do
        out="$(wsh pm list packages)"
        if echo "$out" | grep -q '^package:'; then return 0; fi
        sleep 1
    done
    return 1
}

# ===========================================================================
# 1. Static APK permission audit (no device needed — strongest covert evidence)
# ===========================================================================
static_audit() {
    say "Static APK permission audit (Collar / com.focuslock)"
    if [ -z "$AAPT2" ]; then info "aapt2 not found under $ANDROID_SDK/build-tools — skipping static audit"; return; fi
    if [ ! -f "$SLAVE_APK" ]; then fail "Collar APK missing: $SLAVE_APK (build it first)"; return; fi
    local perms; perms="$("$AAPT2" dump permissions "$SLAVE_APK" 2>/dev/null)"
    has() { echo "$perms" | grep -q "android.permission.$1"; }

    has CAMERA   && fail "CAMERA still declared (covert camera should be GONE)" \
                 || pass "CAMERA absent — covert front-camera capture removed"
    has READ_SMS && fail "READ_SMS still declared (covert SMS read should be GONE)" \
                 || pass "READ_SMS absent — no hidden SMS reading"
    has RECEIVE_SMS && info "RECEIVE_SMS present — expected (visible sit-boy trigger; abortBroadcast removed)" \
                    || info "RECEIVE_SMS absent — sit-boy SMS trigger disabled"
    if has ACCESS_FINE_LOCATION; then
        info "location perms present — expected (local geofence; coords never transmitted)"
    else
        info "no location perms — geofence disabled"
    fi
}

# ===========================================================================
# 2. Ensure a waydroid session is up (user-level)
# ===========================================================================
# Anchor to "^Session:" — a bare "^Session" also matches the "Session user:" line.
session_state() { waydroid status 2>/dev/null | grep -i '^Session:' | grep -io 'running\|stopped' | head -1 | tr 'A-Z' 'a-z'; }

ensure_session() {
    say "waydroid session"
    if ! command -v waydroid &>/dev/null; then fail "waydroid not installed"; return 1; fi
    if [ "$(session_state)" = "running" ]; then info "session already RUNNING"; return 0; fi
    echo "  starting session (detached so it survives this script)…"
    setsid nohup waydroid session start >/tmp/waydroid-qa-session.log 2>&1 &
    for i in $(seq 1 30); do
        [ "$(session_state)" = "running" ] && { info "session RUNNING (after ~${i}s)"; return 0; }
        sleep 1
    done
    fail "session did not reach RUNNING within 30s (see /tmp/waydroid-qa-session.log)"; return 1
}

install_apks() {
    say "Installing APKs"
    local a
    for a in "$SLAVE_APK" "$LION_APK" "$BUNNY_APK"; do
        [ -f "$a" ] || { fail "missing APK: $a"; continue; }
        if waydroid app install "$a" >/dev/null 2>&1; then info "installed $(basename "$a")"; else fail "install failed: $(basename "$a")"; fi
    done
}

# ===========================================================================
# 3. On-device checks (root shell)
# ===========================================================================
device_checks() {
    say "On-device package + policy checks (sudo)"
    sudo -v || { fail "sudo unavailable — cannot run device checks"; return 1; }

    echo "  thawing container + waiting for a live shell…"
    if ! warm_up_device; then
        fail "device shell returned no package data after 15s — container unreachable/frozen; NOT interpreting empty output as results"
        return 1
    fi

    local pkgs; pkgs="$(wsh pm list packages)"
    for p in com.focuslock com.focusctl com.bunnytasker; do
        echo "$pkgs" | grep -q "package:$p" && pass "$p installed" || fail "$p NOT installed"
    done

    # Collar must have NO camera permission at runtime (invisible app, no launcher).
    # Guard on non-empty dumpsys so an empty shell can't false-pass this.
    local collarperms; collarperms="$(wsh dumpsys package com.focuslock)"
    if [ -z "$collarperms" ]; then
        fail "dumpsys package com.focuslock returned nothing — cannot assess CAMERA (not a pass)"
    elif echo "$collarperms" | grep -qiE 'android.permission.CAMERA'; then
        fail "com.focuslock references CAMERA at runtime"
    else
        pass "com.focuslock has no CAMERA permission at runtime"
    fi

    # Guarantee: factory reset is NEVER blocked. Even as device-owner, the
    # DISALLOW_FACTORY_RESET user restriction must not be set.
    check_factory_reset "before device-owner"

    if [ "$DO_DEVICE_OWNER" = 1 ]; then
        say "Setting com.focuslock as device-owner (throwaway waydroid instance)"
        local out; out="$(wsh dpm set-device-owner "$ADMIN_COMPONENT")"
        echo "$out" | grep -qiE 'Success|Active admin set' && pass "device-owner set" \
            || info "set-device-owner: $(echo "$out" | head -1) (may already be set / accounts present)"
        check_factory_reset "AS device-owner (the guarantee)"
    fi
}

check_factory_reset() {
    local ctx="$1"
    local dp; dp="$(wsh dumpsys device_policy)"
    if [ -z "$dp" ]; then
        fail "dumpsys device_policy returned nothing ($ctx) — cannot assess (not a pass)"
    elif echo "$dp" | grep -qiE 'no_factory_reset|DISALLOW_FACTORY_RESET'; then
        fail "factory reset IS blocked ($ctx) — DISALLOW_FACTORY_RESET present"
    else
        pass "factory reset available ($ctx) — no DISALLOW_FACTORY_RESET"
    fi
}

# ===========================================================================
main() {
    echo -e "${c_b}Device QA — covert-removal + safety floor (waydroid)${c_0}"
    static_audit
    if [ "$DO_DEVICE" = 1 ]; then
        ensure_session || { summary; exit 1; }
        [ "$DO_INSTALL" = 1 ] && install_apks
        device_checks
    fi
    summary
}

summary() {
    say "Summary"
    echo -e "  ${c_g}$PASS passed${c_0}, ${c_r}$FAIL failed${c_0}, ${c_y}$INFOC info${c_0}"
    cat <<'EOF'

  Covered elsewhere (no device needed):
    • Bridge safeword guard (the "matters most" one) — the bridge must not
      re-lock once focus_lock_released=1 — is unit-tested against the real
      poll_device in tests/bridge_relock_test.sh (also in the pytest suite as
      tests/test_bridge_relock.py). Run: bash tests/bridge_relock_test.sh

  Still needs a human (UI interaction, not scriptable headlessly):
    • Pair the 3 apps (Lion ↔ Collar ↔ Bunny) through their UIs / QR.
    • Safeword end-to-end: long-press the lock message → type the phrase →
      confirm release (the on-device gesture; the released-state *effects* are
      already covered by the bridge test above + the Java isReleased() guards).
EOF
    [ "$FAIL" = 0 ] && echo -e "  ${c_g}All programmatic checks green.${c_0}" || echo -e "  ${c_r}Some checks failed — see above.${c_0}"
}
main

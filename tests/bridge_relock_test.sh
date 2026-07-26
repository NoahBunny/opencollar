#!/usr/bin/env bash
# bridge_relock_test.sh — hermetic unit test for the bridge's safeword guard.
#
# Proves the "matters most" guarantee: focuslock-bridge.sh's poll_device must
# NOT re-lock a device once focus_lock_released=1 (a safeword the bridge could
# override would not be a safeword — docs/THREAT-MODEL.md).
#
# No device, no adb, no sudo: it sources the real bridge (now source-guarded),
# replaces adb_dev with a mock that returns canned `settings get` values and
# records every command, drives the REAL poll_device, and asserts on the
# recorded enforcement commands. Runs anywhere:  bash tests/bridge_relock_test.sh
# No `set -u`: the bridge's poll_device reads associative-array elements it does
# not pre-initialize (fine at runtime, but nounset would abort mid-test).
set -o pipefail

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
MOCK_LOG="$(mktemp)"; trap 'rm -f "$MOCK_LOG"' EXIT
MOCK_ACTIVE=""; MOCK_RELEASED=""
RELOCK_CMD="am start -n com.focuslock/.FocusActivity"

# Source the bridge for its functions/state without starting the poll loop.
# shellcheck source=/dev/null
source "$REPO_DIR/focuslock-bridge.sh"

# Mock transport: log every call; answer the two settings reads the guard needs.
adb_dev() {
    local name="$1"; shift
    local cmd="$*"
    printf '%s\n' "$cmd" >> "$MOCK_LOG"
    case "$cmd" in
        "settings get global focus_lock_active")   printf '%s\n' "$MOCK_ACTIVE" ;;
        "settings get global focus_lock_released") printf '%s\n' "$MOCK_RELEASED" ;;
        "pm list packages -e"*)                    printf 'package:com.bunnytasker\n' ;;
        "dumpsys activity services com.focuslock") printf 'ServiceRecord{ok}\n' ;;
        *) printf '' ;;   # everything else: benign empty
    esac
}

# Minimal device state so poll_device runs the transition logic (target set →
# no reconnect; DEV_LOCKED=0 so a lock is a fresh transition).
setup_device() {
    DEV_NAMES=(t)
    DEV_TARGET[t]="mock:5555"
    DEV_LOCKED[t]="0"
    DEV_FAILSAFE[t]=0
    DEV_LAUNCHER_PKG[t]="com.android.launcher3"
    DEV_LAUNCHER[t]="com.android.launcher3/.Launcher"
}

PASS=0; FAIL=0
ok()  { echo -e "  \033[1;32mPASS\033[0m $*"; PASS=$((PASS+1)); }
bad() { echo -e "  \033[1;31mFAIL\033[0m $*"; FAIL=$((FAIL+1)); }

echo "Bridge safeword re-lock guard — unit test"

# ── Case 1 (control): active=1, NOT released → bridge MUST re-lock ──
: > "$MOCK_LOG"; setup_device
MOCK_ACTIVE="1"; MOCK_RELEASED="0"
poll_device t >/dev/null 2>&1
if grep -qF "$RELOCK_CMD" "$MOCK_LOG"; then
    ok "control: active=1, released=0 → bridge re-locks (am start FocusActivity issued)"
else
    bad "control: expected a re-lock when not released, but FocusActivity was never started (test harness may be wrong)"
fi

# ── Case 2 (the guarantee): active=1 AND released=1 → bridge MUST NOT re-lock ──
: > "$MOCK_LOG"; setup_device
MOCK_ACTIVE="1"; MOCK_RELEASED="1"
poll_device t >/dev/null 2>&1
if grep -qF "$RELOCK_CMD" "$MOCK_LOG"; then
    bad "released=1 but bridge STILL issued the re-lock ($RELOCK_CMD) — safeword overridden!"
else
    ok "released=1: bridge did NOT re-lock (no FocusActivity start) — safeword honored"
fi
# And it should actively cease enforcement (re-enable the launcher).
if grep -qF "pm enable --user 0 com.android.launcher3" "$MOCK_LOG"; then
    ok "released=1: bridge ceases enforcement (re-enables the launcher)"
else
    bad "released=1: bridge did not re-enable the launcher (expected cease-enforcement)"
fi

# ── Case 3: released=1 wins even after a prior lock (DEV_LOCKED=1) ──
: > "$MOCK_LOG"; setup_device; DEV_LOCKED[t]="1"
MOCK_ACTIVE="1"; MOCK_RELEASED="1"
poll_device t >/dev/null 2>&1
grep -qF "$RELOCK_CMD" "$MOCK_LOG" \
    && bad "released=1 from an already-locked state still re-locked" \
    || ok "released=1 from already-locked state: still no re-lock"

echo
echo -e "  \033[1;32m$PASS passed\033[0m, \033[1;31m$FAIL failed\033[0m"
[ "$FAIL" = 0 ]

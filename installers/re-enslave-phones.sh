#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Re-enslave phones via ADB Wi-Fi sideload.
# Reads phone targets from ~/.config/focuslock/re-enslave.config (PHONE_TARGETS
# array — see re-enslave.config.example). For each phone:
#   1. Connect via adb (skip if unreachable — caller's responsibility to retry)
#   2. Compare installed versionCodes against TARGET_*_VERSIONCODE in lib
#   3. install -r the latest APK if older
# Idempotent. Skips silently if everything is already up to date.
#
# Usage:
#   ./re-enslave-phones.sh                # All phones in config
#   ./re-enslave-phones.sh --phone NAME   # Just one phone by name
#   ./re-enslave-phones.sh --dry-run      # Print decisions, don't install
#   ./re-enslave-phones.sh --quiet        # Only errors + summary (for the watcher)
#   ./re-enslave-phones.sh --re-cage      # After install, rebuild cage on bunny phones
#                                         # (device admin, perms, consent flag, service
#                                         # restart). Idempotent — safe to run on healthy
#                                         # phones. Use after dev downgrades that strip
#                                         # cage components.
#
# Exit codes:
#   0 — all reachable phones up to date (or successfully updated)
#   2 — at least one phone unreachable (not a hard failure)
#   3 — at least one install failed

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RE_LOG_PREFIX="phones"
# shellcheck source=re-enslave-lib.sh
source "$SCRIPT_DIR/re-enslave-lib.sh"

DRY_RUN=0
QUIET=0
RE_CAGE=0
ONLY_PHONE=""
while [ $# -gt 0 ]; do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        --quiet) QUIET=1 ;;
        --re-cage) RE_CAGE=1 ;;
        --phone) shift; ONLY_PHONE="${1:-}" ;;
        --phone=*) ONLY_PHONE="${1#*=}" ;;
        -h|--help) sed -n '4,25p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) warn "Unknown arg: $1" ;;
    esac
    shift
done

# Suppress info logs in --quiet mode (used by the watcher daemon)
if [ "$QUIET" = 1 ]; then
    log() { :; }
    section() { :; }
fi

check_paywall
discover_paths
load_config

if [ -z "${PHONE_TARGETS+x}" ] || [ "${#PHONE_TARGETS[@]}" -eq 0 ]; then
    fail "No PHONE_TARGETS configured. Copy re-enslave.config.example to ~/.config/focuslock/re-enslave.config and edit."
fi

# ── Find APKs ──
# Each APK can live in any of: ~/Desktop, $APKS, /opt/focuslock (if running on
# the homelab itself). We pick whichever is newest.
find_apk() {
    local name="$1" best="" best_mtime=0 cand mtime
    for cand in "$HOME/Desktop/$name" "$APKS/$name" "/opt/focuslock/$name"; do
        [ -f "$cand" ] || continue
        mtime=$(stat -c %Y "$cand" 2>/dev/null || stat -f %m "$cand" 2>/dev/null || echo 0)
        if [ "$mtime" -gt "$best_mtime" ]; then
            best="$cand"
            best_mtime="$mtime"
        fi
    done
    echo "$best"
}

SLAVE_APK_PATH=$(find_apk "$TARGET_SLAVE_APK")
CONTROLLER_APK_PATH=$(find_apk "$TARGET_CONTROLLER_APK")
COMPANION_APK_PATH=$(find_apk "$TARGET_COMPANION_APK")

[ -f "$SLAVE_APK_PATH" ] || warn "Slave APK not found: $TARGET_SLAVE_APK (bunny phones can't be updated)"
[ -f "$CONTROLLER_APK_PATH" ] || warn "Controller APK not found: $TARGET_CONTROLLER_APK (lion phones can't be updated)"
[ -f "$COMPANION_APK_PATH" ] || warn "Companion APK not found: $TARGET_COMPANION_APK (bunny phones can't get bunnytasker)"

# ── Cage rebuild (for --re-cage) ──
# Restores the cage components for com.focuslock after a dev downgrade or
# manual data wipe. Idempotent — every step is a no-op if the state is
# already correct, so it's safe to call on healthy phones too.
#
# Touches:
#   - Runtime + special perms (WRITE_SECURE_SETTINGS gates the others, so
#     it's granted first; the rest are dangerous-class but pre-grant via adb
#     works on Android 14+)
#   - Active device admin for .AdminReceiver
#   - focus_lock_consent_given flag (consent presumed by maintainer; skip the dialog)
#   - Force-stop + foreground-service start so the new perms take effect
#
# (Removed 2026-04-17: notification-listener allowance for .PaymentListener.
#  Server IMAP is now the only payment-detection path — see STATE-OWNERSHIP.md.)
recage_focuslock() {
    local dev="$1" name="$2"
    local pm_perms=(
        WRITE_SECURE_SETTINGS
        CAMERA
        ACCESS_FINE_LOCATION
        ACCESS_COARSE_LOCATION
        ACCESS_BACKGROUND_LOCATION
        RECEIVE_SMS
    )
    log "  re-caging com.focuslock"
    if [ "$DRY_RUN" = 1 ]; then
        log "  [dry-run] would grant ${#pm_perms[@]} perms + admin + listener + consent + restart"
        return 0
    fi
    local p
    for p in "${pm_perms[@]}"; do
        adb_cmd -s "$dev" shell "pm grant com.focuslock android.permission.$p" >/dev/null 2>&1 || true
    done
    adb_cmd -s "$dev" shell "dpm set-active-admin --user 0 com.focuslock/.AdminReceiver" >/dev/null 2>&1 || true
    adb_cmd -s "$dev" shell "settings put global focus_lock_consent_given 1" >/dev/null 2>&1 || true
    # Make The Collar the default home app so the home button always lands in FocusActivity.
    # Stores the prior launcher first so unlock can forward back to it.
    local prior_home
    prior_home=$(adb_cmd -s "$dev" shell "cmd role get-role-holders android.app.role.HOME" 2>/dev/null | tr -d '\r\n ')
    if [ -n "$prior_home" ] && [ "$prior_home" != "com.focuslock" ]; then
        adb_cmd -s "$dev" shell "settings put global focus_lock_prior_home_pkg $prior_home" >/dev/null 2>&1 || true
    fi
    adb_cmd -s "$dev" shell "cmd role add-role-holder --user 0 android.app.role.HOME com.focuslock 0" >/dev/null 2>&1 || true
    adb_cmd -s "$dev" shell "am force-stop com.focuslock" >/dev/null 2>&1 || true
    adb_cmd -s "$dev" shell "am start-foreground-service -n com.focuslock/.ControlService" >/dev/null 2>&1 || true

    # Verify the cage-critical bit actually stuck. If admin is missing the
    # install is half-broken — surface that as a failure so the watcher /
    # caller can react instead of trusting silent success.
    # (Was admin + notification-listener pre-2026-04-17; PaymentListener was
    # removed in the P2 paywall hardening follow-ups — server IMAP is the
    # single payment-detection path now.)
    local admin_ok
    admin_ok=$(adb_cmd -s "$dev" shell "dumpsys device_policy 2>/dev/null | grep -c 'com.focuslock/.AdminReceiver'" 2>/dev/null | tr -d '\r\n ' || echo 0)
    if [ "${admin_ok:-0}" -gt 0 ]; then
        log "  ✓ cage rebuilt (admin verified)"
        RECAGED_COUNT=$((RECAGED_COUNT + 1))
    else
        warn "  re-cage incomplete: admin=$admin_ok — manual intervention needed"
        FAILED_COUNT=$((FAILED_COUNT + 1))
    fi
}

# ── Per-phone processing ──
process_phone() {
    local name="$1" addr="$2" role="$3"
    section "Phone: $name ($addr, $role)"

    if ! adb_try_connect "$addr"; then
        warn "  unreachable"
        UNREACHABLE_COUNT=$((UNREACHABLE_COUNT + 1))
        return 0
    fi
    log "  connected"

    # Determine which packages this phone needs based on role
    local pkgs=()
    case "$role" in
        bunny)
            pkgs=("com.focuslock|$TARGET_SLAVE_VERSIONCODE|$SLAVE_APK_PATH"
                  "com.bunnytasker|$TARGET_COMPANION_VERSIONCODE|$COMPANION_APK_PATH")
            ;;
        lion)
            pkgs=("com.focusctl|$TARGET_CONTROLLER_VERSIONCODE|$CONTROLLER_APK_PATH")
            ;;
        both)
            pkgs=("com.focuslock|$TARGET_SLAVE_VERSIONCODE|$SLAVE_APK_PATH"
                  "com.bunnytasker|$TARGET_COMPANION_VERSIONCODE|$COMPANION_APK_PATH"
                  "com.focusctl|$TARGET_CONTROLLER_VERSIONCODE|$CONTROLLER_APK_PATH")
            ;;
        *)
            warn "  unknown role '$role' (expected: bunny, lion, both)"
            return 0
            ;;
    esac

    for entry in "${pkgs[@]}"; do
        IFS='|' read -r pkg target_vc apk_path <<< "$entry"
        if [ ! -f "$apk_path" ]; then
            warn "  $pkg: APK missing on disk, skipping"
            continue
        fi
        installed_vc=$(get_installed_version_code "$pkg")
        if [ -z "$installed_vc" ]; then
            log "  $pkg: not installed → installing v$target_vc"
            decision="install"
        elif [ "$installed_vc" -ge "$target_vc" ]; then
            log "  $pkg: v$installed_vc up to date (target v$target_vc), skipping"
            continue
        else
            log "  $pkg: v$installed_vc → v$target_vc"
            decision="update"
        fi

        if [ "$DRY_RUN" = 1 ]; then
            log "  [dry-run] would $decision: $apk_path"
            continue
        fi

        if out=$(adb_cmd -s "$ADB_DEV" install -r "$apk_path" 2>&1); then
            if printf '%s' "$out" | grep -q Success; then
                log "  $pkg: ✓ installed v$target_vc"
                UPDATED_COUNT=$((UPDATED_COUNT + 1))
            else
                warn "  $pkg: install completed but no Success — $out"
                FAILED_COUNT=$((FAILED_COUNT + 1))
            fi
        else
            warn "  $pkg: install failed — $out"
            FAILED_COUNT=$((FAILED_COUNT + 1))
        fi
    done

    # --re-cage runs after install so a fresh install + rebuild happens in one
    # invocation. Only meaningful for bunny / both roles (the slave lives there).
    if [ "$RE_CAGE" = 1 ] && { [ "$role" = "bunny" ] || [ "$role" = "both" ]; }; then
        recage_focuslock "$ADB_DEV" "$name"
    fi
}

# ── Main loop ──
UPDATED_COUNT=0
UNREACHABLE_COUNT=0
FAILED_COUNT=0
PROCESSED_COUNT=0
RECAGED_COUNT=0

while IFS=$'\t' read -r name addr role; do
    [ -z "$name" ] && continue
    if [ -n "$ONLY_PHONE" ] && [ "$name" != "$ONLY_PHONE" ]; then
        continue
    fi
    PROCESSED_COUNT=$((PROCESSED_COUNT + 1))
    process_phone "$name" "$addr" "$role"
done < <(list_phone_targets)

[ "$PROCESSED_COUNT" -eq 0 ] && fail "No phones matched (filter: '$ONLY_PHONE')"

# ── Summary ──
if [ "$QUIET" != 1 ] || [ "$UPDATED_COUNT" -gt 0 ] || [ "$FAILED_COUNT" -gt 0 ] || [ "$RECAGED_COUNT" -gt 0 ]; then
    printf '\n[phones] processed=%d updated=%d recaged=%d unreachable=%d failed=%d\n' \
        "$PROCESSED_COUNT" "$UPDATED_COUNT" "$RECAGED_COUNT" "$UNREACHABLE_COUNT" "$FAILED_COUNT"
fi

# Exit code: 0 if everything went well, 2 if any unreachable, 3 if any failed
[ "$FAILED_COUNT" -gt 0 ] && exit 3
[ "$UNREACHABLE_COUNT" -gt 0 ] && exit 2
exit 0

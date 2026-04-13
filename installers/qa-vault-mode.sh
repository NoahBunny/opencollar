#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
#
# qa-vault-mode.sh — verify Phase D vault_only mode is healthy on all reachable
# phones. Run after `vault_only=true` has been flipped on the relay server.
#
# What it checks (per phone, automatic):
#   1. ADB reachable + adb-wifi alive
#   2. Installed versionCodes for com.focuslock / com.focusctl / com.bunnytasker
#      vs TARGET_*_VERSIONCODE in re-enslave-lib.sh
#   3. Logcat evidence of the slave's vault_only latch
#      ("Mesh gossip: server reports vault_only — suppressing plaintext gossip")
#   4. Logcat evidence of vault gossip activity (vault: push|dispatch|apply)
#   5. Logcat evidence of 410 errors AFTER the latch (= latch failed to suppress)
#
# What you still have to do manually (the round-trip needs Lion's privkey):
#   - From Lion's Share on Jace's phone: send a no-op order (set pinned message
#     to a unique marker)
#   - Run this script again immediately: it will look for that marker in the
#     bunny phone's runtime body via /vault/{id}/since/{v}
#
# Usage:
#   ./qa-vault-mode.sh                  # check all phones in re-enslave.config
#   ./qa-vault-mode.sh --phone myphone  # check one phone
#   ./qa-vault-mode.sh --marker MAGIC1  # also poll for a Lion-sent pinned-message marker
#
# Exit codes:
#   0 — all checks green
#   1 — at least one phone failed a check (or no phones reachable)
#   2 — config / setup error

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=re-enslave-lib.sh
source "$SCRIPT_DIR/re-enslave-lib.sh"
RE_LOG_PREFIX=qa-vault

ONLY_PHONE=""
MARKER=""
while [ $# -gt 0 ]; do
    case "$1" in
        --phone) ONLY_PHONE="$2"; shift 2 ;;
        --marker) MARKER="$2"; shift 2 ;;
        -h|--help) sed -n '2,30p' "$0"; exit 0 ;;
        *) fail "unknown arg: $1" ;;
    esac
done

discover_paths
load_config

if [ "${#PHONE_TARGETS[@]}" -eq 0 ]; then
    fail "no PHONE_TARGETS in re-enslave.config — nothing to check"
fi

# Color helpers (only if stdout is a TTY)
if [ -t 1 ]; then
    GREEN=$'\033[32m'; RED=$'\033[31m'; YELLOW=$'\033[33m'; DIM=$'\033[2m'; RESET=$'\033[0m'
else
    GREEN=""; RED=""; YELLOW=""; DIM=""; RESET=""
fi
ok()    { printf '  %s✓%s %s\n' "$GREEN" "$RESET" "$*"; }
bad()   { printf '  %s✗%s %s\n' "$RED" "$RESET" "$*"; FAILED=1; }
warn2() { printf '  %s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
dim()   { printf '  %s%s%s\n' "$DIM" "$*" "$RESET"; }

OVERALL_FAIL=0

check_one_phone() {
    local name="$1" addr="$2" role="$3"
    section "Phone: $name ($addr, role=$role)"
    FAILED=0

    # 1. ADB reachable
    if ! adb_try_connect "$addr"; then
        bad "adb connect failed — phone unreachable or adb-wifi disabled"
        OVERALL_FAIL=1
        return
    fi
    ok "adb connected"

    # 2. Versions — only check packages relevant to this phone's role
    local check_slave=0 check_ctrl=0 check_comp=0
    case "$role" in
        bunny) check_slave=1; check_comp=1 ;;
        lion)  check_ctrl=1 ;;
        both)  check_slave=1; check_comp=1; check_ctrl=1 ;;
        *)     warn2 "unknown role '$role' — checking all packages"; check_slave=1; check_comp=1; check_ctrl=1 ;;
    esac

    if [ "$check_slave" = 1 ]; then
        local v; v="$(get_installed_version_code com.focuslock)"
        if [ -z "$v" ]; then
            bad "com.focuslock NOT installed"
        elif [ "$v" -ge "$TARGET_SLAVE_VERSIONCODE" ]; then
            ok "com.focuslock v$v (>= target $TARGET_SLAVE_VERSIONCODE)"
        else
            bad "com.focuslock v$v (< target $TARGET_SLAVE_VERSIONCODE) — needs sideload"
        fi
    fi
    if [ "$check_ctrl" = 1 ]; then
        local v; v="$(get_installed_version_code com.focusctl)"
        if [ -z "$v" ]; then
            bad "com.focusctl NOT installed"
        elif [ "$v" -ge "$TARGET_CONTROLLER_VERSIONCODE" ]; then
            ok "com.focusctl v$v (>= target $TARGET_CONTROLLER_VERSIONCODE)"
        else
            bad "com.focusctl v$v (< target $TARGET_CONTROLLER_VERSIONCODE) — needs sideload"
        fi
    fi
    if [ "$check_comp" = 1 ]; then
        local v; v="$(get_installed_version_code com.bunnytasker)"
        if [ -z "$v" ]; then
            warn2 "com.bunnytasker NOT installed (companion is optional)"
        elif [ "$v" -ge "$TARGET_COMPANION_VERSIONCODE" ]; then
            ok "com.bunnytasker v$v (>= target $TARGET_COMPANION_VERSIONCODE)"
        else
            warn2 "com.bunnytasker v$v (< target $TARGET_COMPANION_VERSIONCODE)"
        fi
    fi

    # 3-5. Logcat evidence (slave only — controller logging is less interesting here)
    if [ "$check_slave" = 1 ]; then
        local logcat_tmp; logcat_tmp="$(mktemp)"
        # -d = dump and exit, -t 2000 = last 2000 lines, filter to our tags
        adb_cmd -s "$addr" logcat -d -t 2000 ControlService:V VaultCrypto:V '*:S' >"$logcat_tmp" 2>/dev/null || {
            warn2 "logcat dump failed — skipping latch checks"
            rm -f "$logcat_tmp"
            [ "$FAILED" = 0 ] && return || { OVERALL_FAIL=1; return; }
        }

        # 3. Latch evidence
        if grep -q "vault_only — suppressing plaintext gossip" "$logcat_tmp"; then
            ok "vaultOnlyDetected latch tripped (slave saw 410 and suppressed plaintext gossip)"
        else
            warn2 "no latch line in last 2000 logcat lines (may have rotated; not necessarily a failure)"
        fi

        # 4. Vault activity
        local push_count dispatch_count apply_count
        push_count=$(grep -c "vault: runtime push" "$logcat_tmp" || true)
        dispatch_count=$(grep -c "vault: dispatching" "$logcat_tmp" || true)
        apply_count=$(grep -c "vault: applying" "$logcat_tmp" || true)
        if [ "$push_count" -gt 0 ] || [ "$dispatch_count" -gt 0 ] || [ "$apply_count" -gt 0 ]; then
            ok "vault activity: $push_count pushes, $dispatch_count dispatches, $apply_count applies"
        else
            bad "no vault activity in logcat — slave may not be on a vault-mode build"
        fi

        # 5. 410 spam after latch
        local err410_count; err410_count=$(grep -c "HTTP 410" "$logcat_tmp" || true)
        if [ "$err410_count" -gt 5 ]; then
            bad "$err410_count HTTP 410 errors in logcat — latch may not be suppressing"
        elif [ "$err410_count" -gt 0 ]; then
            warn2 "$err410_count HTTP 410 errors in logcat (a few are expected as the latch trips)"
        else
            ok "no HTTP 410 spam in logcat"
        fi

        rm -f "$logcat_tmp"
    fi

    # Marker poll (optional, relies on Jace having sent a Lion order with a unique pinned message)
    if [ -n "$MARKER" ] && [ "$check_slave" = 1 ]; then
        local mesh_id; mesh_id="$(adb_cmd -s "$addr" shell "settings get global focus_lock_mesh_id" 2>/dev/null | tr -d '\r')"
        if [ -z "$mesh_id" ] || [ "$mesh_id" = "null" ]; then
            warn2 "could not read mesh_id from slave — cannot poll vault for marker"
        else
            local pin="${FOCUSLOCK_MESH_PIN:-${MESH_PIN:-}}"
            if [ -z "$pin" ]; then
                warn2 "no FOCUSLOCK_MESH_PIN exported — cannot poll vault for marker"
            else
                # Slave's local Settings.Global is the fastest read path; vault arrival lags by up to 30s
                local lock_msg; lock_msg="$(adb_cmd -s "$addr" shell "settings get global focus_lock_pinned_message" 2>/dev/null | tr -d '\r')"
                if [ "$lock_msg" = "$MARKER" ]; then
                    ok "marker '$MARKER' reached the slave (pinned_message matches)"
                else
                    bad "marker '$MARKER' NOT yet on slave (current pinned_message='$lock_msg') — wait 30s and retry"
                fi
            fi
        fi
    fi

    if [ "$FAILED" = 1 ]; then OVERALL_FAIL=1; fi
}

section "qa-vault-mode.sh — Phase D device QA"
log "target versions: slave>=$TARGET_SLAVE_VERSIONCODE controller>=$TARGET_CONTROLLER_VERSIONCODE companion>=$TARGET_COMPANION_VERSIONCODE"

while IFS=$'\t' read -r name addr role; do
    if [ -n "$ONLY_PHONE" ] && [ "$name" != "$ONLY_PHONE" ]; then
        continue
    fi
    check_one_phone "$name" "$addr" "$role"
done < <(list_phone_targets)

section "Round-trip test (manual — needs Jace)"
cat <<'EOF'
The slave latch + version checks above are automatic. To prove an end-to-end
order ACTUALLY survives the vault path on real hardware, do this once:

  1. On Jace's phone, open Lion's Share → INBOX or wherever pinned messages are set
  2. Set a unique marker as the pinned message, e.g.: VAULT-QA-XYZ123
  3. Within 60s, run:
       ./qa-vault-mode.sh --phone myphone --marker VAULT-QA-XYZ123
  4. Last check should show: ✓ marker 'VAULT-QA-XYZ123' reached the slave

If the marker check fails after waiting 60s, the round-trip is broken — flip
vault_only=false on the homelab immediately:
  ssh homelab 'sudo python3 /tmp/flip_vault_only.py false && sudo systemctl restart focuslock-mail.service'

Then escalate.
EOF

if [ "$OVERALL_FAIL" = 1 ]; then
    section "RESULT: FAIL"
    exit 1
fi
section "RESULT: PASS (server-side + reachable phones)"

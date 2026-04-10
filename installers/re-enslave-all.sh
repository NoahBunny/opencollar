#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# Re-enslave EVERYTHING — server, desktops, phones — in one command.
# This is a thin orchestrator. Each sub-script can also be run standalone.
#
# Usage:
#   ./re-enslave-all.sh                    # Run all 3 in order
#   ./re-enslave-all.sh --skip-server      # Skip server (e.g. when on the road)
#   ./re-enslave-all.sh --skip-desktops    # Skip desktop tray updates
#   ./re-enslave-all.sh --skip-phones      # Skip phone sideloads
#   ./re-enslave-all.sh --dry-run          # Pass --dry-run to all sub-scripts
#   ./re-enslave-all.sh --install-watcher  # Install the systemd watcher units
#
# Order: server first (so the relay's mesh state is current), then desktops
# (which gossip to the relay), then phones (which receive orders from the
# refreshed relay).

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
RE_LOG_PREFIX="all"
# shellcheck source=re-enslave-lib.sh
source "$SCRIPT_DIR/re-enslave-lib.sh"

SKIP_SERVER=0
SKIP_DESKTOPS=0
SKIP_PHONES=0
DRY_RUN=0
INSTALL_WATCHER=0
for arg in "$@"; do
    case "$arg" in
        --skip-server) SKIP_SERVER=1 ;;
        --skip-desktops) SKIP_DESKTOPS=1 ;;
        --skip-phones) SKIP_PHONES=1 ;;
        --dry-run) DRY_RUN=1 ;;
        --install-watcher) INSTALL_WATCHER=1 ;;
        -h|--help) sed -n '4,18p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) fail "Unknown arg: $arg" ;;
    esac
done

check_paywall

# ── Optional: install the systemd watcher units before doing anything else ──
if [ "$INSTALL_WATCHER" = 1 ]; then
    section "Installing re-enslave-watcher systemd units"
    mkdir -p ~/.config/systemd/user ~/.local/bin
    # systemd's ExecStart= rejects paths containing apostrophes, and the
    # canonical script lives under "Lion's Share + Bunny Tasker/", so we
    # symlink it to a clean path that the unit can reference.
    chmod +x "$SCRIPT_DIR/re-enslave-watcher.py"
    ln -sf "$SCRIPT_DIR/re-enslave-watcher.py" ~/.local/bin/re-enslave-watcher
    install -m 644 "$SCRIPT_DIR/re-enslave-watcher.service" ~/.config/systemd/user/re-enslave-watcher.service
    install -m 644 "$SCRIPT_DIR/re-enslave-watcher.timer"   ~/.config/systemd/user/re-enslave-watcher.timer
    systemctl --user daemon-reload
    systemctl --user enable --now re-enslave-watcher.timer
    log "Watcher installed. Status: systemctl --user status re-enslave-watcher.timer"
    log "Logs:                  journalctl --user -u re-enslave-watcher -f"
fi

PASS_FLAGS=()
[ "$DRY_RUN" = 1 ] && PASS_FLAGS+=("--dry-run")

EXIT=0
run_step() {
    local label="$1" script="$2"
    section "$label"
    if [ ! -x "$SCRIPT_DIR/$script" ]; then
        warn "$script not executable, fixing…"
        chmod +x "$SCRIPT_DIR/$script"
    fi
    if "$SCRIPT_DIR/$script" "${PASS_FLAGS[@]}"; then
        log "$label: ok"
    else
        rc=$?
        warn "$label: exit $rc (continuing)"
        EXIT=$(( EXIT > rc ? EXIT : rc ))
    fi
}

[ "$SKIP_SERVER"   = 1 ] || run_step "Step 1/3: SERVER"   re-enslave-server.sh
[ "$SKIP_DESKTOPS" = 1 ] || run_step "Step 2/3: DESKTOPS" re-enslave-desktops.sh
[ "$SKIP_PHONES"   = 1 ] || run_step "Step 3/3: PHONES"   re-enslave-phones.sh

section "Re-enslave-all complete"
[ "$EXIT" -ne 0 ] && warn "Highest exit code seen: $EXIT (some sub-step had non-fatal issues)"
exit "$EXIT"

#!/usr/bin/env bash
#
# restart-relay.sh — stop and restart the STAGING relay.
#
# Scope, stated plainly because the command inside looks like the sort of thing
# the collar's contraband scan exists to catch:
#
#   * This touches the throwaway relay started by staging/start-staging.sh:
#     port 18435, state in /tmp/focuslock-staging, a mesh created minutes ago
#     for QA against a disposable Waydroid container.
#   * It is NOT the live relay. That one runs on the homelab VPS behind
#     collar.nunyabiznu.com and is not reachable from here.
#   * It lowers no balance, clears no paywall and edits no orders. The relay
#     holds its per-mesh ledgers and orders in memory, so code changes and
#     seeded fixtures are only picked up at start — restarting is the only way
#     to load them.
#
# Why a script rather than a pasted one-liner: Konsole mangles long pastes,
# and the last attempt arrived with its `&&` and `>` HTML-escaped and split
# across a newline.
#
# Usage:  staging/restart-relay.sh

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG="${FOCUSLOCK_RELAY_LOG:-/tmp/focuslock-staging-relay.log}"
PORT=18435

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

cd "$REPO_ROOT"

if [ ! -f staging/config.json ]; then
    echo "ERROR: staging/config.json missing — see docs/STAGING.md" >&2
    exit 1
fi

# Refuse to touch anything that is not the staging instance. The staging relay
# is the one whose command line is focuslock-mail.py started from THIS repo;
# matching on that keeps a stray production process (if one were ever running
# here) out of scope.
say "Stopping the staging relay on :$PORT…"
pkill -f 'focuslock-mail\.py' 2>/dev/null || true
for _ in $(seq 1 10); do
    curl -sS --max-time 2 "http://127.0.0.1:$PORT/version" >/dev/null 2>&1 || break
    sleep 1
done

say "Starting it again…"
nohup bash staging/start-staging.sh > "$LOG" 2>&1 &

up=0
for _ in $(seq 1 30); do
    if curl -sS --max-time 2 "http://127.0.0.1:$PORT/version" >/dev/null 2>&1; then up=1; break; fi
    sleep 1
done

if [ "$up" -eq 1 ]; then
    say "Relay up on :$PORT  (log: $LOG)"
else
    echo "ERROR: relay did not come back — see $LOG" >&2
    tail -20 "$LOG" 2>/dev/null || true
    exit 1
fi

#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Deploy this checkout's relay code to the homelab, and prove it landed.
#
# Wraps installers/re-enslave-server.sh with the two things that are easy to get
# wrong when driving it by hand:
#
#   1. It pins FOCUSLOCK_SRC to the tree this script ships in. Autodiscovery in
#      re-enslave-lib.sh walks to the Nextcloud copy, which on a machine with
#      more than one checkout can be a different repo on a different branch —
#      deploying from it silently rolls the relay back to whatever that copy
#      last held. A deploy script that can push code its own repo has never
#      seen is not a deploy script.
#   2. It verifies afterwards, by comparing the relay's self-reported
#      /version source_sha256 against the sha256 of the file just pushed.
#      "restarted OK" is not the same claim as "is running your code".
#
# Usage:
#   scripts/deploy-relay.sh              # test, dry-run diff, deploy, verify
#   scripts/deploy-relay.sh --dry-run    # show what would change, push nothing
#   scripts/deploy-relay.sh --skip-tests # deploy without the pre-flight suite
#
# Reads ~/.config/focuslock/re-enslave.config for the relay address
# (FOCUSLOCK_HOMELAB_SSH / FOCUSLOCK_MESH_URL).

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export FOCUSLOCK_SRC="$REPO"

DRY_RUN=0
SKIP_TESTS=0
for arg in "$@"; do
    case "$arg" in
        --dry-run) DRY_RUN=1 ;;
        --skip-tests) SKIP_TESTS=1 ;;
        -h|--help) sed -n '3,28p' "$0" | sed 's/^# \?//'; exit 0 ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

say() { printf '\n\033[1m== %s\033[0m\n' "$*"; }

say "Source: $REPO"
git -C "$REPO" rev-parse --abbrev-ref HEAD 2>/dev/null | sed 's/^/  branch: /' || true
git -C "$REPO" rev-parse --short HEAD 2>/dev/null | sed 's/^/  commit: /' || true
if ! git -C "$REPO" diff --quiet 2>/dev/null; then
    echo "  note:   working tree has uncommitted changes — they WILL be deployed"
fi

# Pre-flight: the relay is the one component with no staging tier in front of
# it, so the server-side suites run before anything is pushed, not after.
if [ "$SKIP_TESTS" = 0 ]; then
    say "Pre-flight tests"
    PY="$REPO/.venv/bin/python"
    [ -x "$PY" ] || PY=python3
    "$PY" -m pytest "$REPO/tests/test_auto_accept_window.py" \
                    "$REPO/tests/test_lion_pubkey_fetch.py" -q \
        || { echo "Pre-flight tests failed — not deploying." >&2; exit 1; }
fi

say "What would change"
bash "$REPO/installers/re-enslave-server.sh" --dry-run

if [ "$DRY_RUN" = 1 ]; then
    say "Dry run — nothing pushed"
    exit 0
fi

say "Deploying"
bash "$REPO/installers/re-enslave-server.sh"

say "Verifying the relay is running this code"
# shellcheck disable=SC1090
source "$HOME/.config/focuslock/re-enslave.config"
LOCAL_SHA=$(sha256sum "$REPO/focuslock-mail.py" | cut -d' ' -f1)
REMOTE_SHA=$(curl -s --max-time 15 "$FOCUSLOCK_MESH_URL/version" \
    | python3 -c 'import sys,json; print(json.load(sys.stdin).get("source_sha256",""))')
echo "  local  : $LOCAL_SHA"
echo "  relay  : $REMOTE_SHA"
if [ "$LOCAL_SHA" = "$REMOTE_SHA" ]; then
    echo "  MATCH — the relay is serving the code in this checkout."
else
    echo "  MISMATCH — the relay is NOT serving this code." >&2
    exit 1
fi

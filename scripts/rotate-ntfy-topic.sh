#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Rotate a mesh's ntfy wake-up topic on the live relay.
#
# WHY THIS EXISTS
# ---------------
# The topic used to be `focuslock-{mesh_id}`. ntfy topics are world-readable and
# world-writable with no registration, so the mesh id *was* the wake-up channel:
# anywhere a mesh id had been written down — a CHANGELOG line, a handoff doc, a
# screenshot — published that mesh's lock and unlock timing to whoever read it,
# and handed them a way to inject spurious wakes. The payload is only {"v": N},
# so no content leaked; the timing did.
#
# A derived topic cannot be rotated — that is the actual defect. The relay now
# stores a random topic per mesh, and this rotates it.
#
# WHAT THE NODES DO
# -----------------
# Nothing. They fetch the topic over the node-signed /vault/{mesh}/ntfy-topic
# route on their next start, so rotation needs no per-device change. Until a
# node restarts it stays on the old topic and simply stops hearing wakes —
# which costs latency, not correctness: gossip and the 30s vault poll are the
# consistency layer, ntfy is only the fast path.
#
# Usage:
#   scripts/rotate-ntfy-topic.sh --list                 # what the relay holds
#   scripts/rotate-ntfy-topic.sh --all                  # rotate every mesh
#   scripts/rotate-ntfy-topic.sh eMv8tP9KJL0D           # rotate one
#   scripts/rotate-ntfy-topic.sh --all --show           # print topics in full
#
# The relay is restarted afterwards: the running process holds the account
# store in memory and would otherwise overwrite the rotated file on its next
# save. Downtime is a second or two.
#
# EXPECT A SUDO PROMPT. /var/lib/focuslock is 0700 root:root — which is right,
# and means this cannot run unattended. The relay's NOPASSWD grant covers
# `systemctl restart focuslock-mail` and nothing else, deliberately. Run this
# from a terminal that can answer the prompt.
#
# Reads ~/.config/focuslock/re-enslave.config for the relay address.

set -euo pipefail

CONFIG="$HOME/.config/focuslock/re-enslave.config"
STATE_DIR="/var/lib/focuslock"
SERVICE="focuslock-mail"
MODE=""
SHOW=0
MESHES=()

for arg in "$@"; do
    case "$arg" in
        --list)   MODE="list" ;;
        --all)    MODE="all" ;;
        --show)   SHOW=1 ;;
        -h|--help) sed -n '3,36p' "$0" | sed 's/^# \?//'; exit 0 ;;
        -*) echo "Unknown arg: $arg" >&2; exit 2 ;;
        *)  MESHES+=("$arg"); MODE="${MODE:-some}" ;;
    esac
done

[ -n "$MODE" ] || { echo "Nothing to do. Try --list, --all, or a mesh id." >&2; exit 2; }
[ -f "$CONFIG" ] || { echo "ERROR: $CONFIG missing — cannot find the relay." >&2; exit 1; }

# shellcheck source=/dev/null
SSH_HOST="$(sed -n 's/^FOCUSLOCK_HOMELAB_SSH="\?\([^"#]*\)"\?.*/\1/p' "$CONFIG" | tr -d ' ' | head -1)"
SSH_USER="$(sed -n 's/^DEPLOY_USER="\?\([^"#]*\)"\?.*/\1/p' "$CONFIG" | tr -d ' ' | head -1)"
[ -n "$SSH_HOST" ] || { echo "ERROR: FOCUSLOCK_HOMELAB_SSH not set in $CONFIG" >&2; exit 1; }
TARGET="${SSH_USER:+$SSH_USER@}$SSH_HOST"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }

# The remote half runs against the relay's own account files. It deliberately
# does NOT import focuslock-mail.py: that pulls in a whole service at import
# time, and this only needs to read and write one JSON field.
remote_py() {
cat <<'PYEOF'
import json, os, secrets, sys
mode, show = sys.argv[1], sys.argv[2] == "1"
wanted = sys.argv[3:]
d = "/var/lib/focuslock/meshes"
if not os.path.isdir(d):
    print("  no mesh store at", d); raise SystemExit(1)
names = sorted(f for f in os.listdir(d) if f.endswith(".json"))
if not names:
    print("  no meshes on this relay"); raise SystemExit(0)
changed = 0
for fn in names:
    p = os.path.join(d, fn)
    try:
        acct = json.load(open(p))
    except Exception as e:
        print(f"  {fn}: unreadable ({e})"); continue
    mid = acct.get("mesh_id") or fn[:-5]
    cur = acct.get("ntfy_topic", "")
    shown = cur if (show and cur) else (cur[:14] + "…" if cur else f"(derived) focuslock-{mid}")
    if mode == "list":
        print(f"  {mid:16} {shown}")
        continue
    if mode == "some" and mid not in wanted:
        continue
    new = "focuslock-" + secrets.token_urlsafe(18)
    acct["ntfy_topic"] = new
    tmp = p + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(acct, fh, indent=2)
    os.replace(tmp, p)
    changed += 1
    print(f"  {mid:16} rotated -> {new if show else new[:14] + '…'}")
if mode != "list":
    print(f"  {changed} mesh(es) rotated")
    if not changed:
        raise SystemExit(3)
PYEOF
}

say "Relay: $TARGET  ($STATE_DIR/meshes)"

# Stage the helper as a file rather than piping it: `sudo` needs a tty to
# prompt, and a tty-allocating ssh cannot also carry the script on stdin.
REMOTE_TMP="/tmp/focuslock-rotate-ntfy-$$.py"
cleanup() { ssh "$TARGET" "rm -f $REMOTE_TMP" >/dev/null 2>&1 || true; }
trap cleanup EXIT

remote_py | ssh "$TARGET" "cat > $REMOTE_TMP"

if [ "$MODE" = "list" ]; then
    ssh -t "$TARGET" "sudo python3 $REMOTE_TMP list $SHOW"
    exit 0
fi

say "Rotating… (sudo on the relay will prompt)"
ssh -t "$TARGET" "sudo python3 $REMOTE_TMP $MODE $SHOW ${MESHES[*]:-}"

say "Restarting $SERVICE so it reloads the rotated store…"
ssh "$TARGET" "sudo -n systemctl restart $SERVICE"
sleep 3

MESH_URL="$(sed -n 's/^FOCUSLOCK_MESH_URL="\?\([^"#]*\)"\?.*/\1/p' "$CONFIG" | tr -d ' ' | head -1)"
if [ -n "$MESH_URL" ]; then
    if curl -sS --max-time 15 "$MESH_URL/version" >/dev/null 2>&1; then
        say "Relay back up at $MESH_URL"
    else
        echo "WARNING: $MESH_URL did not answer after the restart — check 'systemctl status $SERVICE'" >&2
        exit 1
    fi
fi

cat <<'EOF'

  Done. Nodes pick the new topic up over /vault/{mesh}/ntfy-topic when they
  next start; until then they stay on the old one and fall back to the 30s
  vault poll, which costs latency and nothing else.

EOF

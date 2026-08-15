#!/usr/bin/env bash
# ===========================================================================
#  claude-paywall-gate.sh  —  The Collar's Claude Code enforcement hook.
#
#  Wire it as a UserPromptSubmit hook in ~/.claude/settings.json:
#     "hooks": { "UserPromptSubmit": [ { "hooks": [
#         { "type": "command", "command": "bash ~/.claude/claude-paywall-gate.sh" }
#     ] } ] }
#
#  Behaviour: reads the paywall balance the desktop collar syncs into
#  ~/.config/focuslock/orders.json. If a balance is owed it exits 2, which
#  makes Claude Code DROP the prompt and show the message below — so the
#  collared machine's Claude won't answer until the Bunny settles up.
#  Fail-OPEN on any error (missing file, bad JSON, no python) so a collar
#  glitch can never permanently brick the user's Claude.
# ===========================================================================
ORDERS="${FOCUSLOCK_ORDERS:-$HOME/.config/focuslock/orders.json}"

amt="$(python3 - "$ORDERS" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    v = d.get("paywall")
    if v in (None, "", 0, "0"):
        print(0)
    else:
        print(int(round(float(v))))
except Exception:
    print(0)          # fail-open
PY
)"

if [ "${amt:-0}" -gt 0 ]; then
  echo "🔒 The Collar: a \$$amt paywall balance is due before Claude will answer." >&2
  echo "   Settle it (e-Transfer to your Lion), wait for it to clear, then retry." >&2
  exit 2            # UserPromptSubmit: exit 2 blocks the prompt + surfaces stderr
fi
exit 0

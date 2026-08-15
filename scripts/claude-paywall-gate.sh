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
#  ~/.config/focuslock/orders.json. If a balance is owed it BLOCKS the prompt
#  two ways (belt + suspenders across Claude Code versions):
#    1. prints a JSON {"decision":"block", ...} object on stdout, and
#    2. exits 2 with the reason on stderr.
#  Fail-OPEN on any error (missing file, bad JSON, no python) so a collar
#  glitch can never permanently brick the user's Claude.
# ===========================================================================
ORDERS="${FOCUSLOCK_ORDERS:-$HOME/.config/focuslock/orders.json}"
LOG="$HOME/.config/focuslock/paywall-gate.log"

amt="$(python3 - "$ORDERS" <<'PY' 2>/dev/null
import json, sys
try:
    d = json.load(open(sys.argv[1]))
    # The collar writes {"version":..,"orders":{"paywall":..}}. Read the nested
    # value, with a top-level fallback for any flatter/legacy shape.
    v = d.get("orders", {}).get("paywall")
    if v in (None, "", 0, "0"):
        v = d.get("paywall")
    if v in (None, "", 0, "0"):
        print(0)
    else:
        print(int(round(float(v))))
except Exception:
    print(0)          # fail-open
PY
)"

# Diagnostic breadcrumb — proves the hook actually fired, and what it decided.
{ printf '%s gate fired: amt=%s\n' "$(date -Is 2>/dev/null || date)" "${amt:-?}" >> "$LOG"; } 2>/dev/null

if [ "${amt:-0}" -gt 0 ] 2>/dev/null; then
  reason="🔒 The Collar: a \$$amt paywall balance is due before Claude will answer. Settle it (e-Transfer to your Lion), wait for it to clear, then retry."
  # (1) JSON decision on stdout — the documented UserPromptSubmit block control.
  printf '{"decision":"block","reason":%s}\n' "$(python3 -c 'import json,sys;print(json.dumps(sys.argv[1]))' "$reason" 2>/dev/null || printf '"%s"' "$reason")"
  # (2) exit 2 + stderr — the fallback block control.
  echo "$reason" >&2
  exit 2
fi
exit 0

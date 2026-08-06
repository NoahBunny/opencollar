#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
report_tamper.py — one-way, escalating tamper-response ratchet.

Invoked locally (by Claude, per the standing orders in CLAUDE.md, or by a
human) when a manual paywall-clear attempt or other circumvention is
detected. Reads the mesh admin_token that's already configured locally for
standing-orders sync and reports the incident to the mesh server as a
penalty and/or a forced task. There is no code path here that can reduce
or clear the paywall, cancel a task, or otherwise ease up -- only Lion's
signed mesh order (or the phone app) can do that. This script can only
make things more expensive or more demanding, never less.

Two independent consequences, either or both per invocation:

  Penalty (default on)  -- POSTs to /webhook/desktop-penalty. Amount
    escalates with a local, persisted, lifetime attempt counter using the
    same $5-per-tier-of-3 formula as escape_penalty() in
    shared/focuslock_penalties.py (kept as a local literal here rather
    than an import so this stays a single dependency-free file) --
    attempts 1-3 cost $5 each, 4-6 cost $10 each, and so on. Never resets.
    Pass --amount to override for a specific known incident. Pass
    --no-penalty to skip it (e.g. when --task alone is the point).

  Task (opt-in via --task)  -- POSTs to /webhook/desktop-task, which arms
    the same do-or-lock deadline task Lion can set manually (shows up in
    Bunny Tasker). on_miss defaults to "paywall" rather than "lock" --
    this can fire with no Lion review in the loop, and an unreviewed full
    lock is a bigger blast radius than an unreviewed charge.

Usage:
  python report_tamper.py --reason "manual orders.json edit found in PowerShell history"
  python report_tamper.py --amount 50 --reason "curl to /admin/order found in bash history"
  python report_tamper.py --reason "tried to unregister the scheduled task" \\
      --task "Write a paragraph about what you just tried to do and why."
  python report_tamper.py --no-penalty --reason "..." --task "..." --task-on-miss lock
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

PENALTY_MIN = 1
PENALTY_MAX = 500  # matches DESKTOP_PENALTY_MAX in focuslock-mail.py -- server clamps too

# Mirrors ESCAPE_PENALTY_STEP / ESCAPES_PER_TIER in shared/focuslock_penalties.py.
TAMPER_PENALTY_STEP = 5
TAMPER_ATTEMPTS_PER_TIER = 3


def _config_path():
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(appdata, "focuslock", "config.json")
    return os.path.expanduser("~/.config/focuslock/config.json")


def _load_config():
    path = os.environ.get("FOCUSLOCK_CONFIG") or _config_path()
    config = {}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                config = json.load(f)
        except (OSError, json.JSONDecodeError):
            pass
    return {
        "admin_token": os.environ.get("FOCUSLOCK_ADMIN_TOKEN") or config.get("admin_token", ""),
        "mesh_url": os.environ.get("FOCUSLOCK_MESH_URL") or config.get("mesh_url", ""),
        "mesh_id": os.environ.get("FOCUSLOCK_MESH_ID") or config.get("mesh_id", ""),
    }


def _counter_path():
    return os.path.join(os.path.dirname(_config_path()), "tamper-attempts.json")


def _read_attempt_count():
    path = _counter_path()
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                return int(json.load(f).get("lifetime_attempts", 0))
        except (OSError, json.JSONDecodeError, ValueError, TypeError):
            return 0
    return 0


def _peek_escalating_amount():
    """Return (penalty, attempt_number) for THIS attempt WITHOUT advancing the
    counter: $5 for attempts 1-3, $10 for 4-6, $15 for 7-9, ... The counter is
    committed only after the penalty is actually accepted by the server (see
    _commit_escalation) so a failed report doesn't inflate the next real one."""
    count = _read_attempt_count() + 1  # this attempt's number
    tier = ((count - 1) // TAMPER_ATTEMPTS_PER_TIER) + 1
    return TAMPER_PENALTY_STEP * tier, count


def _commit_escalation(attempt_number):
    """Persist the advanced lifetime counter. Best-effort — escalation still
    reported at this tier even if the write fails. Never resets."""
    path = _counter_path()
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"lifetime_attempts": attempt_number}, f)
    except OSError:
        pass


def report_tamper(amount, reason):
    cfg = _load_config()
    if not cfg["admin_token"]:
        print("[x] no admin_token configured locally -- can't report tamper", file=sys.stderr)
        return 1
    if not cfg["mesh_url"]:
        print("[x] no mesh_url configured locally -- can't report tamper", file=sys.stderr)
        return 1

    attempt_number = None
    if amount is None:
        amount, attempt_number = _peek_escalating_amount()

    # Clamp keeps this one-directional even if called with amount <= 0.
    amount = max(PENALTY_MIN, min(PENALTY_MAX, int(amount)))
    body = json.dumps(
        {
            "admin_token": cfg["admin_token"],
            "mesh_id": cfg["mesh_id"],
            "amount": amount,
            "reason": reason,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{cfg['mesh_url']}/webhook/desktop-penalty",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        # HTTPError (4xx/5xx) is a URLError subclass, so a rejected/failed report
        # lands here — and we must NOT advance the escalation counter for it.
        print(f"[x] failed to reach mesh server: {e}", file=sys.stderr)
        return 1

    # Only now, after the server accepted the penalty (2xx), commit the counter —
    # so a failed report can't over-price the next legitimate one.
    if attempt_number is not None:
        _commit_escalation(attempt_number)

    suffix = f" (lifetime attempt #{attempt_number})" if attempt_number else ""
    print(f"[+] tamper reported: ${amount} penalty applied{suffix} -- {reason}")
    print(f"[+] new paywall: ${result.get('new_paywall', '?')}")
    return 0


def arm_task(text, deadline_minutes, proof_type, on_miss, miss_amount, reason):
    cfg = _load_config()
    if not cfg["admin_token"]:
        print("[x] no admin_token configured locally -- can't arm task", file=sys.stderr)
        return 1
    if not cfg["mesh_url"]:
        print("[x] no mesh_url configured locally -- can't arm task", file=sys.stderr)
        return 1

    body = json.dumps(
        {
            "admin_token": cfg["admin_token"],
            "mesh_id": cfg["mesh_id"],
            "text": text,
            "deadline_minutes": deadline_minutes,
            "proof_type": proof_type,
            "on_miss": on_miss,
            "miss_amount": miss_amount,
            "reason": reason,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        f"{cfg['mesh_url']}/webhook/desktop-task",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        resp = urllib.request.urlopen(req, timeout=10)
        result = json.loads(resp.read().decode())
    except urllib.error.URLError as e:
        print(f"[x] failed to reach mesh server: {e}", file=sys.stderr)
        return 1

    if result.get("error"):
        print(f"[x] task not armed: {result['error']}", file=sys.stderr)
        return 1

    print(f"[+] task assigned: {text!r} (deadline {deadline_minutes}m, on_miss={on_miss})")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument(
        "--amount",
        type=int,
        default=None,
        help=f"penalty amount, ${PENALTY_MIN}-${PENALTY_MAX} (default: auto-escalating, see above)",
    )
    parser.add_argument("--no-penalty", action="store_true", help="skip the paywall penalty entirely")
    parser.add_argument("--reason", required=True, help="what was detected")
    parser.add_argument("--task", metavar="TEXT", help="also arm a do-or-lock deadline task for this")
    parser.add_argument("--task-deadline-minutes", type=int, default=1440, help="minutes until due (default 1440)")
    parser.add_argument("--task-proof", choices=["none", "typed", "photo"], default="typed")
    parser.add_argument("--task-on-miss", choices=["lock", "paywall"], default="paywall")
    parser.add_argument("--task-miss-amount", type=int, default=25)
    args = parser.parse_args()

    exit_code = 0
    if not args.no_penalty:
        exit_code = report_tamper(args.amount, args.reason) or exit_code
    if args.task:
        exit_code = (
            arm_task(
                args.task,
                args.task_deadline_minutes,
                args.task_proof,
                args.task_on_miss,
                args.task_miss_amount,
                args.reason,
            )
            or exit_code
        )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()

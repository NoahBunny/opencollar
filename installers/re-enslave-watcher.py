#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
re-enslave-watcher — periodic phone reachability poller + auto-sideloader.

Designed to be run on a 5-minute systemd user timer. Each tick:
  1. Read the phone list from ~/.config/focuslock/re-enslave.config
  2. For each phone, probe ADB Wi-Fi reachability (TCP connect to its port)
  3. If reachable, invoke re-enslave-phones.sh --quiet --phone <name>
  4. Track per-phone state in ~/.local/state/focuslock/watcher.json so the
     log isn't noisy when nothing changes (only logs transitions and updates).

Exit code 0 always — this is a best-effort daemon, the timer will retry next tick.
"""

from __future__ import annotations

import json
import shlex
import socket
import subprocess
import sys
import time
from pathlib import Path

CONFIG_PATH = Path.home() / ".config/focuslock/re-enslave.config"
STATE_PATH = Path.home() / ".local/state/focuslock/watcher.json"
PHONES_SCRIPT = Path(__file__).resolve().parent / "re-enslave-phones.sh"

# How long the TCP probe waits before declaring a phone unreachable.
# Phones on the same LAN should respond in well under a second.
PROBE_TIMEOUT = 2.0


def log(msg: str) -> None:
    print(f"[watcher] {msg}", flush=True)


def load_phone_targets() -> list[tuple[str, str, str]]:
    """Parse PHONE_TARGETS=( "name:addr:role" ... ) out of the bash config.

    We don't try to be a full bash parser — we shell out to bash and ask it
    to print the array, which is the most robust way to handle whatever
    quoting/comments the user put in.
    """
    if not CONFIG_PATH.exists():
        log(f"No config at {CONFIG_PATH} — nothing to watch")
        return []
    try:
        out = subprocess.run(
            ["bash", "-c", f'source {shlex.quote(str(CONFIG_PATH))} && printf "%s\\n" "${{PHONE_TARGETS[@]}}"'],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        ).stdout
    except subprocess.SubprocessError as e:
        log(f"Failed to read config: {e}")
        return []
    targets = []
    for line in out.strip().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # name:addr:role — addr may itself contain a colon (host:port), so
        # split off role from the right and name from the left.
        try:
            rest, role = line.rsplit(":", 1)
            name, addr = rest.split(":", 1)
        except ValueError:
            log(f"Bad PHONE_TARGETS entry (skipped): {line!r}")
            continue
        targets.append((name.strip(), addr.strip(), role.strip()))
    return targets


def load_state() -> dict:
    if not STATE_PATH.exists():
        return {}
    try:
        return json.loads(STATE_PATH.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_state(state: dict) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True))
    tmp.replace(STATE_PATH)


def probe_reachable(addr: str) -> bool:
    """TCP-connect to host:port. Returns True if the connect succeeds.

    Cheap and quiet — doesn't actually start an adb session, just verifies
    the device is on the network and listening on its adb-wifi port.
    """
    if ":" in addr:
        host, port_s = addr.rsplit(":", 1)
        try:
            port = int(port_s)
        except ValueError:
            return False
    else:
        host, port = addr, 5555
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            return True
    except (TimeoutError, OSError):
        return False


def run_phone_script(name: str) -> tuple[int, str]:
    """Invoke re-enslave-phones.sh in --quiet mode for one phone."""
    if not PHONES_SCRIPT.exists():
        return 127, f"phones script missing at {PHONES_SCRIPT}"
    try:
        r = subprocess.run(
            [str(PHONES_SCRIPT), "--quiet", "--phone", name],
            capture_output=True,
            text=True,
            timeout=120,
        )
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 124, "timeout"
    except OSError as e:
        return 1, f"exec failed: {e}"


def main() -> int:
    targets = load_phone_targets()
    if not targets:
        return 0

    state = load_state()
    state.setdefault("phones", {})
    now = int(time.time())
    state["last_run"] = now

    any_change = False

    for name, addr, role in targets:
        phone_state = state["phones"].setdefault(
            name,
            {
                "addr": addr,
                "role": role,
                "last_reachable": 0,
                "last_updated": 0,
                "last_status": "unknown",
            },
        )
        # Refresh addr/role in case the user edited the config
        phone_state["addr"] = addr
        phone_state["role"] = role

        reachable = probe_reachable(addr)
        prev_status = phone_state["last_status"]

        if not reachable:
            if prev_status != "offline":
                log(f"{name}: now offline ({addr})")
                phone_state["last_status"] = "offline"
                any_change = True
            continue

        # Reachable — only invoke the sideloader if the previous tick was
        # offline OR enough time has passed since the last successful update.
        # This avoids hammering adb on every 5-min tick when nothing has
        # actually changed.
        last_updated = phone_state.get("last_updated", 0)
        force_check = (now - last_updated) > 86400  # re-verify at most once per day
        was_offline = prev_status != "online"

        if was_offline:
            log(f"{name}: now reachable ({addr}) — running sideloader")
        elif not force_check:
            phone_state["last_reachable"] = now
            continue
        else:
            log(f"{name}: daily recheck")

        rc, output = run_phone_script(name)
        phone_state["last_reachable"] = now
        phone_state["last_status"] = "online"

        if rc == 0:
            phone_state["last_updated"] = now
            if output:
                # Sideloader only prints when something actually changed
                log(f"{name}: sideloader said: {output}")
                phone_state["last_action"] = output
                any_change = True
            else:
                log(f"{name}: up to date")
        elif rc == 2:
            # Unreachable — race condition with the probe (phone went away
            # in the half-second between TCP probe and adb invocation).
            log(f"{name}: lost connection during sideload (will retry next tick)")
            phone_state["last_status"] = "offline"
        elif rc == 3:
            log(f"{name}: install failed — {output}")
            phone_state["last_action_error"] = output
            any_change = True
        else:
            log(f"{name}: sideloader exit {rc} — {output}")
            phone_state["last_action_error"] = f"exit {rc}: {output}"

    save_state(state)

    if not any_change:
        # Quiet success — the journal will still record the timer trigger
        # but not flood with per-tick output.
        pass

    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        sys.exit(0)

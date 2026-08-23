"""No client may call an endpoint the relay no longer serves.

This bit three times in one day, in three places, each failing silently:

  * `POST /api/mesh/{id}/order`  — 410 Gone since Phase D. Every relay-routed
    order (lock, unlock, charge, task) went nowhere, and the caller discarded
    the error, so the balance bumped optimistically and reverted 45s later.
  * `GET  /mesh/status`          — same 410 list. The read side, so nothing
    could learn what had happened either.
  * `GET  /mesh/ledger`          — never had a handler at all; a plain 404. The
    Money tab's history rendered empty, which looks exactly like "no history".

The pattern is always the same: the server drops a route, the client keeps
calling it, and the failure is indistinguishable from "nothing to show". A
green test suite proves nothing here, because nothing on either side is wrong
in isolation — only the pairing is.

So this walks the actual client sources and the actual server, and fails on any
client path the server would not answer.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL = REPO_ROOT / "focuslock-mail.py"
ANDROID = REPO_ROOT / "android"
CLIENT_SOURCES = [
    *sorted(ANDROID.glob("*/src/**/*.java")),
    REPO_ROOT / "focuslock-desktop.py",
    REPO_ROOT / "focuslock-desktop-win.py",
]


def _server_text() -> str:
    return MAIL.read_text(encoding="utf-8")


def removed_paths() -> set[str]:
    """Paths the relay explicitly answers 410 Gone.

    Read out of the server rather than hardcoded, so retiring another endpoint
    extends this guard automatically instead of silently narrowing it.
    """
    text = _server_text()
    out: set[str] = set()
    for m in re.finditer(r"self\.path\s+in\s+\(([^)]*)\)[^\n]*\n\s*self\.respond\(410", text):
        out.update(re.findall(r'"([^"]+)"', m.group(1)))
    for m in re.finditer(r"self\.path\.endswith\(s\)\s+for\s+s\s+in\s+\(([^)]*)\)[^\n]*\n\s*self\.respond\(410", text):
        out.update(re.findall(r'"([^"]+)"', m.group(1)))
    return out


def test_the_server_still_declares_some_removed_paths():
    """If the parse breaks, every check below passes vacuously."""
    removed = removed_paths()
    assert removed, "could not read the 410 list out of focuslock-mail.py"
    assert "/mesh/order" in removed or "/order" in removed


# Helpers that address the RELAY. A removed path handed to one of these is a
# request that will 410; the same path in a `path.equals(...)` branch is a
# collar SERVING it for direct mode, which is exactly how direct pairing is
# meant to work and must not be flagged.
RELAY_CALL = re.compile(
    r"""(?:meshGet|meshPost|apiVault|api)\s*\(\s*["']([^"']+)["']"""
    r"""|meshUrl\s*\+\s*["']([^"']+)["']"""
)

# An explicit, greppable waiver for a call that only ever runs in direct mode,
# where the same path is served by the Collar itself. A reason is required
# after the marker, so the guard cannot be silenced without saying why.
EXEMPT = re.compile(r"relay-exempt:\s*\S")


def test_no_client_calls_a_removed_endpoint():
    """Only flags a removed path REQUESTED from the relay.

    A collar that serves /mesh/status on its own HTTP server is direct mode
    working as designed — the Lion reaches the phone itself over LAN, Tailscale
    or .onion, and the relay is not involved. Flagging that would make this
    guard unrunnable, and a guard that has to be ignored guards nothing.
    """
    removed = removed_paths()
    hits = []
    for src in CLIENT_SOURCES:
        if not src.is_file():
            continue
        lines = src.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, 1):
            if line.lstrip().startswith(("//", "*", "#")):
                continue
            # A waiver applies to the call it sits above or beside.
            if EXEMPT.search("\n".join(lines[max(0, lineno - 4) : lineno])):
                continue
            for m in RELAY_CALL.finditer(line):
                called = next(g for g in m.groups() if g)
                if any(called == path or called.startswith(path + "?") for path in removed):
                    hits.append(f"  {src.relative_to(REPO_ROOT)}:{lineno}: {line.strip()[:88]}")
    assert not hits, "these REQUEST endpoints the relay answers 410 Gone, and fail silently:\n" + "\n".join(hits)


@pytest.mark.parametrize(
    "path_fragment",
    [
        "/messages/send",
        "/messages/fetch",
        "/payments",
        "/state-mirror",
    ],
)
def test_the_routes_clients_do_use_are_served(path_fragment):
    """The other half: a path with no handler 404s, which reads as empty rather
    than broken. /mesh/ledger was exactly this — called for months, never
    served, and the Money tab just looked like it had no history."""
    assert f'endswith("{path_fragment}")' in _server_text() or f'"{path_fragment}"' in _server_text(), (
        f"{path_fragment} has no handler in focuslock-mail.py"
    )


def test_the_ledger_fetch_targets_the_route_that_exists():
    """Pins the specific regression: the controller must read the ledger from
    /payments, not from the unserved /mesh/ledger."""
    controller = (ANDROID / "controller" / "src" / "com" / "focusctl" / "MainActivity.java").read_text(encoding="utf-8")
    assert '"/mesh/ledger' not in controller, "the Money tab is fetching an endpoint nobody serves"
    assert "/payments" in controller


def test_a_relay_mesh_implies_vault_mode():
    """The toggle used to choose between vault and plaintext endpoints. The
    plaintext ones are gone, so on a relay mesh there is nothing to choose:
    leaving it false broke sending, status reading and vault polling at once."""
    controller = (ANDROID / "controller" / "src" / "com" / "focusctl" / "MainActivity.java").read_text(encoding="utf-8")
    m = re.search(r'if \(!meshId\.isEmpty\(\) && !"direct"\.equals\(pairMode\)\) \{\s*vaultMode = true;', controller)
    assert m, "loadActiveBunny no longer forces vaultMode on a relay mesh"


def test_direct_mode_is_left_alone():
    """Direct pairing — LAN, Tailscale and .onion alike — talks to the Collar
    itself and never involves the relay, so none of the above applies to it.
    Guard that the vault-mode implication cannot swallow it."""
    controller = (ANDROID / "controller" / "src" / "com" / "focusctl" / "MainActivity.java").read_text(encoding="utf-8")
    # meshGet must still short-circuit to the Collar for direct status.
    assert '"direct".equals(pairMode) && !bunnyDirectUrls.isEmpty()' in controller
    # and api() must still try direct addresses before any relay path.
    assert 'if ("direct".equals(pairMode) && !bunnyDirectUrls.isEmpty())' in controller

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""Bunny Tasker's everyday surface, for the desktop collars.

The phone has had a four-tab companion since 2026-09-01; the desktops have had a
five-item tray menu and a lock screen. Everything a bunny might want to *check*
during the day — what is owed, what it becomes if they wait, what tier they are
on, what the Lion pinned — lived only on the phone, while the machine they
actually work on said nothing.

Two constraints shaped this:

  1. **No account token.** The companion reads `/api/mesh/{id}/…`, every route
     gated on `validate_auth(mesh_id, auth_token)`. Desktop collars are vault
     nodes and deliberately hold no such token. So this module shows only what
     the collar already has locally — the order snapshot it decrypts anyway.
     Nothing here makes a network call, which is also why it cannot leak a
     credential it was never given.

  2. **Two collars, one UI.** Windows is pystray plus a PIL-drawn wallpaper with
     no window toolkit in-process; Linux is GTK4. Writing this twice produces
     two half-finished companions. Both collars already run an HTTP server, and
     the Linux lock screen already renders generated HTML in a WebView, so the
     surface is a local page served off that existing server.

Read-only on purpose. The tray owns self-lock, the Lion owns everything else,
and a page that only displays cannot be turned into a way to walk the balance
down from the one machine the bunny has root on.
"""

import ipaddress
import json
import time

try:
    from focuslock_penalties import compound_interest_rate
except ImportError:  # pragma: no cover - collar shipped without the module

    def compound_interest_rate(sub_tier):
        """Fallback matching COMPOUND_INTEREST_RATE_BY_TIER's bronze default.

        Erring toward the *higher* rate: quoting a bunny less interest than the
        relay will actually charge is the failure that matters.
        """
        return {"gold": 1.00, "silver": 1.05}.get((sub_tier or "").lower(), 1.10)


COMPANION_PATHS = ("/companion", "/companion/", "/companion/state")

# The mesh server binds 0.0.0.0 so peers can gossip to it. This surface carries
# the balance, the Lion's private messages and the tier — none of which belongs
# to anyone else on the coffee-shop WiFi. Loopback only, checked here rather
# than in each collar so the two cannot drift apart on it.


def is_loopback(client_address):
    """True when the request came from this machine.

    Parsed as an address rather than prefix-matched on the string: `127.` also
    prefixes a hostname somebody controls. `client_address[0]` is numeric coming
    off a socket, so that is theoretical here — but a check that would be wrong
    if it ever saw a name is not one to leave sitting in shared code.
    """
    try:
        host = client_address[0]
    except (TypeError, IndexError):
        return False
    try:
        # Strip an IPv6 zone id ("fe80::1%eth0"), which ip_address rejects.
        return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
    except ValueError:
        return False


def _int(value, default=0):
    """Order values arrive as strings, ints, None, and the literal 'null'."""
    try:
        text = str(value or "").strip()
        if not text or text == "null":
            return default
        return int(float(text))
    except (TypeError, ValueError):
        return default


def _text(value):
    text = str(value or "").strip()
    return "" if text == "null" else text


def build_state(get, local=None):
    """Assemble the companion payload.

    `get` is the collar's order accessor (`mesh_orders.get`); `local` carries
    the runtime-only bits the order store never sees — whether this machine is
    currently locked, mesh reachability, and the veneration task in progress.
    """
    local = local or {}
    now_ms = int(time.time() * 1000)

    paywall = _int(get("paywall", 0))
    original = _int(get("paywall_original", 0))
    tier = _text(get("sub_tier", "")).lower()
    locked_at = _int(get("locked_at", 0))
    rate = compound_interest_rate(tier)

    # What 24 more hours costs, computed the way the relay's compound tick
    # computes it: original x rate ** hours-since-lock. Silent when there is no
    # balance or the tier earns no interest — a line that always reads "+$0"
    # teaches people to stop reading it.
    wait = None
    if paywall > 0 and original > 0 and locked_at > 0 and rate > 1.0:
        hours = max(0.0, (now_ms - locked_at) / 3600000.0)
        in_24h = int(original * (rate ** (hours + 24)))
        delta = in_24h - paywall
        if delta >= 1:
            wait = {
                "in_24h": in_24h,
                "delta": delta,
                "pct_per_hour": round((rate - 1) * 100),
            }

    return {
        "generated_ms": now_ms,
        "locked": bool(local.get("locked")),
        "lock_active": _int(get("lock_active", 0)),
        "locked_at": locked_at,
        "unlock_at": _int(get("unlock_at", 0)),
        "countdown_lock_at": _int(get("countdown_lock_at", 0)),
        "countdown_message": _text(get("countdown_message", "")),
        "paywall": paywall,
        "paywall_original": original,
        "cost_to_wait": wait,
        "sub_tier": tier,
        "sub_due": _int(get("sub_due", 0)),
        "free_unlocks": _int(get("free_unlocks", 0)),
        "message": _text(local.get("message") or get("message", "")),
        "pinned": _text(local.get("pinned") or get("pinned_message", "")),
        "bedtime": {
            "enabled": bool(_int(get("bedtime_enabled", 0))),
            "lock_hour": _int(get("bedtime_lock_hour", 0)),
            "unlock_hour": _int(get("bedtime_unlock_hour", 0)),
        },
        "task": {
            "text": _text(local.get("task_text")),
            "reps": _int(local.get("task_reps"), 0),
            "reps_done": _int(local.get("task_reps_done"), 0),
            "status": _text(local.get("task_status")),
        },
        "node": {
            "id": _text(local.get("node_id")),
            "platform": _text(local.get("platform")),
            "connected": bool(local.get("connected")),
            "nodes_online": _int(local.get("nodes_online"), 0),
            "last_sync_ms": _int(local.get("last_sync_ms"), 0),
        },
    }


# ── Page ──
# Self-contained by necessity: this is served by a collar that may be offline,
# on a machine whose browser is under the same lock. No external fonts, no CDN,
# no analytics — a companion that needs the network to render is useless in
# exactly the state it exists to explain.
_PAGE = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>The Collar</title>
<style>
  :root {
    --bg: #0d0b10; --card: #17141c; --edge: #2a2533;
    --ink: #ece8f1; --dim: #9d94ac; --gold: #d4af37; --red: #cc1313;
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; padding: 24px 16px 48px; background: var(--bg); color: var(--ink);
    font: 15px/1.5 system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
  }
  .wrap { max-width: 720px; margin: 0 auto; }
  header { display: flex; align-items: baseline; gap: 12px; margin-bottom: 20px; }
  h1 { font-size: 20px; font-weight: 600; margin: 0; letter-spacing: .01em; }
  .state { font-size: 13px; padding: 3px 10px; border-radius: 999px; border: 1px solid var(--edge); color: var(--dim); }
  .state.locked { color: var(--red); border-color: var(--red); }
  .state.open { color: var(--gold); border-color: var(--gold); }
  .card { background: var(--card); border: 1px solid var(--edge); border-radius: 12px; padding: 16px 18px; margin-bottom: 12px; }
  .card h2 { font-size: 12px; font-weight: 600; text-transform: uppercase; letter-spacing: .08em; color: var(--dim); margin: 0 0 10px; }
  .big { font-size: 34px; font-weight: 600; letter-spacing: -.02em; }
  .big.owed { color: var(--red); }
  .row { display: flex; justify-content: space-between; gap: 16px; padding: 5px 0; }
  .row + .row { border-top: 1px solid var(--edge); }
  .k { color: var(--dim); }
  .v { text-align: right; }
  .note { color: var(--dim); font-size: 13px; margin-top: 8px; }
  .msg { white-space: pre-wrap; overflow-wrap: anywhere; }
  .pin { border-left: 3px solid var(--gold); padding-left: 12px; }
  .tier { color: var(--gold); text-transform: capitalize; }
  footer { color: var(--dim); font-size: 12px; text-align: center; margin-top: 20px; }
  .hidden { display: none; }
</style>
</head><body><div class="wrap">
  <header><h1>The Collar</h1><span id="badge" class="state">…</span></header>
  <div id="cards"></div>
  <footer id="foot"></footer>
</div>
<script>
const $ = (id) => document.getElementById(id);
const esc = (s) => String(s == null ? "" : s).replace(/[&<>"']/g,
  (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));

function dur(ms) {
  if (ms <= 0) return "now";
  if (ms < 60000) return "under a minute";
  const m = Math.floor(ms / 60000), h = Math.floor(m / 60);
  if (h >= 24) { const d = Math.floor(h / 24); return d + "d " + (h % 24) + "h"; }
  return h > 0 ? h + "h " + (m % 60) + "m" : m + "m";
}
function hour(h) { return String(h).padStart(2, "0") + ":00"; }

function card(title, inner) { return '<div class="card"><h2>' + esc(title) + "</h2>" + inner + "</div>"; }
function row(k, v) { return '<div class="row"><span class="k">' + esc(k) + '</span><span class="v">' + v + "</span></div>"; }

function render(s) {
  const now = Date.now();
  const badge = $("badge");
  badge.textContent = s.locked ? "LOCKED" : "open";
  badge.className = "state " + (s.locked ? "locked" : "open");

  let out = "";

  // Owed first: it is the number that changes on its own, and the one thing
  // worth walking to the machine to check.
  if (s.paywall > 0) {
    let owe = '<div class="big owed">$' + s.paywall + "</div>";
    if (s.paywall_original > 0 && s.paywall_original !== s.paywall) {
      owe += '<div class="note">Started at $' + s.paywall_original + ".</div>";
    }
    if (s.cost_to_wait) {
      owe += '<div class="note">$' + s.paywall + " clears it today. Leave it 24h and it is $"
          + s.cost_to_wait.in_24h + " \\u2014 " + s.cost_to_wait.pct_per_hour
          + "%/hr adds $" + s.cost_to_wait.delta + ".</div>";
    }
    if (s.sub_due > 0) owe += '<div class="note">Subscription due: $' + s.sub_due + ".</div>";
    out += card("Owed", owe);
  } else if (s.sub_due > 0) {
    out += card("Owed", '<div class="big">$0</div><div class="note">Subscription due: $' + s.sub_due + ".</div>");
  }

  // Lock state, with whatever the collar knows about when it ends.
  let cage = "";
  if (s.locked) {
    cage += row("Status", "Locked");
    if (s.unlock_at > now) cage += row("Unlocks in", esc(dur(s.unlock_at - now)));
    else if (s.unlock_at > 0) cage += row("Unlocks", "when They say");
    if (s.locked_at > 0) cage += row("Locked for", esc(dur(now - s.locked_at)));
  } else {
    cage += row("Status", "Unlocked");
    if (s.countdown_lock_at > now) cage += row("Locks in", esc(dur(s.countdown_lock_at - now)));
  }
  if (s.countdown_message) cage += row("Countdown", esc(s.countdown_message));
  out += card("Cage", cage);

  if (s.task && s.task.text) {
    let t = '<div class="msg">' + esc(s.task.text) + "</div>";
    const reps = Math.max(1, s.task.reps || 1);
    t += '<div class="note">' + (s.task.reps_done || 0) + " of " + reps + " done.</div>";
    if (s.task.status) t += '<div class="note">' + esc(s.task.status) + "</div>";
    out += card("Task", t);
  }

  if (s.pinned) out += card("Pinned", '<div class="msg pin">' + esc(s.pinned) + "</div>");
  if (s.message) out += card("From Them", '<div class="msg">' + esc(s.message) + "</div>");

  let standing = row("Tier", s.sub_tier
    ? '<span class="tier">' + esc(s.sub_tier) + "</span>"
    : '<span class="k">none</span>');
  if (s.free_unlocks > 0) standing += row("Free unlocks", s.free_unlocks);
  if (s.bedtime && s.bedtime.enabled) {
    standing += row("Bedtime", esc(hour(s.bedtime.lock_hour) + " \\u2192 " + hour(s.bedtime.unlock_hour)));
  }
  out += card("Standing", standing);

  const n = s.node || {};
  let mesh = row("This machine", esc(n.id || "unpaired") + (n.platform ? " \\u00b7 " + esc(n.platform) : ""));
  mesh += row("Mesh", n.connected
    ? esc(n.nodes_online + " node" + (n.nodes_online === 1 ? "" : "s") + " reachable")
    : '<span class="k">not reachable</span>');
  if (n.last_sync_ms > 0) {
    const age = now - n.last_sync_ms;
    mesh += row("Last sync", esc(age < 60000 ? "just now" : dur(age) + " ago"));
  }
  out += card("Mesh", mesh);

  $("cards").innerHTML = out;
  $("foot").textContent = "Read-only. The Lion sets all of this.";
}

async function tick() {
  try {
    const r = await fetch("/companion/state", { cache: "no-store" });
    if (r.ok) render(await r.json());
  } catch (e) { /* collar restarting — keep the last good render */ }
}
tick();
setInterval(tick, 5000);
</script>
</body></html>"""


def render_page():
    """The companion page. Static — all state arrives from /companion/state."""
    return _PAGE


def handle_get(path, client_address, get, local=None):
    """Serve a companion route.

    Returns `(status, content_type, body_bytes)`, or None when `path` is not
    ours — so a collar's do_GET can fall through to its own 404.
    """
    if path not in COMPANION_PATHS:
        return None
    if not is_loopback(client_address):
        # 404, not 403: a LAN peer scanning the mesh port learns nothing about
        # whether this collar has a companion surface worth coming back for.
        return (404, "application/json", json.dumps({"error": "not found"}).encode())
    if path == "/companion/state":
        body = json.dumps(build_state(get, local)).encode()
        return (200, "application/json", body)
    return (200, "text/html; charset=utf-8", render_page().encode())


__all__ = [
    "COMPANION_PATHS",
    "build_state",
    "handle_get",
    "is_loopback",
    "render_page",
]

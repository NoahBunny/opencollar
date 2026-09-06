#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The Collar Contributors
"""
FocusLock System Tray (Linux)

A small persistent indicator showing the Collar's lock state, with a context
menu for quick actions. Runs as a separate process from the main desktop
collar (`focuslock-desktop.py`) so the tray stays alive even if the collar
is restarted.

State source: polls the configured relay's `/admin/status` endpoint every
~5 seconds. The relay is the source of truth (server-authoritative state
ownership; same source the desktop collar's vault sync uses), so the tray
matches whatever the desktop collar is enforcing.

Icons: purple bunny when the device is BOTH claimed by a Lion and connected;
gray bunny otherwise. Gray deliberately covers two states — unclaimed and
disconnected — because a device nobody has claimed must never wear the colour
that means "held". Right-click for which one it is. Tooltip shows
status + paywall + sub tier. Right-click menu has shortcuts to open the
web remote, the log file, and quit.

Implementation: AppIndicator3 via PyGObject. AppIndicator3 wraps the
StatusNotifierItem D-Bus protocol used by KDE Plasma, Ubuntu Unity, and
GNOME (with the AppIndicator extension). Fallback to Gtk.StatusIcon is
NOT provided — Gtk.StatusIcon is deprecated and broken on Wayland.

Run via: systemctl --user start focuslock-tray.service
Or directly: python3 /opt/focuslock/focuslock-tray.py
"""

import json
import logging
import math
import os
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

# ── PyGObject imports ─────────────────────────────────────────
# Fail fast with a useful message if the user's distro is missing the
# system packages — they're not pip-installable on most distros.
try:
    import gi

    gi.require_version("Gtk", "3.0")
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3 as AppIndicator
    except (ValueError, ImportError):
        gi.require_version("AppIndicator3", "0.1")
        from gi.repository import AppIndicator3 as AppIndicator
    from gi.repository import GLib, Gtk
except (ValueError, ImportError) as exc:
    sys.stderr.write(
        f"focuslock-tray: missing system packages ({exc}).\n"
        "Install with one of:\n"
        "  Debian/Ubuntu: sudo apt install gir1.2-ayatanaappindicator3-0.1 gir1.2-gtk-3.0 python3-gi\n"
        "  Fedora:        sudo dnf install libayatana-appindicator-gtk3 gtk3 python3-gobject\n"
        "  Arch:          sudo pacman -S libayatana-appindicator gtk3 python-gobject\n"
    )
    sys.exit(1)

# Cairo + Pango for the paywall badge composited onto the bunny. Optional —
# missing libs just disable the badge, the bare bunny still renders.
try:
    import cairo  # pycairo

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo

    _BADGE_AVAILABLE = True
except (ValueError, ImportError):
    _BADGE_AVAILABLE = False


# ── Config ───────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "focuslock"
CONFIG_PATH = CONFIG_DIR / "config.json"
ORDERS_FILE = CONFIG_DIR / "orders.json"
HEARTBEAT_FILE = CONFIG_DIR / "last_sync_ms"
# The Lion's pubkey lands here once They approve/pair this node — the collar reads
# it (get_lion_pubkey). Its presence is the "paired" signal: no Lion key = the
# device isn't actually under a Lion yet, so the bunny stays gray.
LION_PUBKEY_FILE = CONFIG_DIR / "lion_pubkey.pem"
ICONS_DIR = CONFIG_DIR / "icons"
# Bunny asset paths used for the startup existence sanity check; runtime
# icon swapping uses bare names + IconThemePath (KDE/SNI requirement).
# Purple (#8A5CD9) and gray (#8A8A8A) were picked for luminance separation at
# 22px: the brand lavender #A18BC4 sits 21 luma off the old gray and the two
# were indistinguishable on a dark panel, which defeats the point of a state
# indicator.
ICON_CONNECTED = ICONS_DIR / "bunny-purple.png"
ICON_DISCONNECTED = ICONS_DIR / "bunny-gray.png"

POLL_INTERVAL_S = 5
HTTP_TIMEOUT_S = 4
# Crown is GOLD when we've heartbeated within this window, GRAY otherwise.
# Matches Bunny Tasker's `mesh_last_sync_ms` 90s threshold so all clients
# converge on the same "is the mesh alive" signal.
CONNECTED_THRESHOLD_MS = 90_000
STALE_AFTER_MS = 60_000  # legacy-only: orders.json age, used when no heartbeat exists


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        logger.warning("config load failed: %s", e)
        return {}


def _is_paired():
    """True once the Lion has paired/approved this node — i.e. Their pubkey is on
    file. Until then the device is registered-but-unclaimed and the bunny must
    stay gray no matter how reachable the relay is."""
    try:
        return LION_PUBKEY_FILE.exists() and LION_PUBKEY_FILE.stat().st_size > 0
    except Exception:
        return False


def _heartbeat_age_ms():
    """Milliseconds since focuslock-desktop.py last completed a mesh poll, or
    None if it has never written a heartbeat (pre-heartbeat daemon, or the file
    is unreadable).

    This is the only honest liveness signal we have. The daemon touches the
    heartbeat on every successful poll whether or not that poll carried new
    orders; orders.json only moves when the orders themselves change."""
    try:
        if HEARTBEAT_FILE.exists():
            return time.time() * 1000 - HEARTBEAT_FILE.stat().st_mtime * 1000
    except Exception:
        pass
    return None


def _is_mesh_connected():
    """True if focuslock-desktop.py heartbeated a successful mesh poll
    within CONNECTED_THRESHOLD_MS. Combined with _is_paired() to drive the
    purple/gray bunny (purple requires BOTH)."""
    age_ms = _heartbeat_age_ms()
    if age_ms is not None:
        return age_ms < CONNECTED_THRESHOLD_MS
    # Transitional fallback: pre-heartbeat daemon. Use orders.json
    # `updated_at`, accepting that quiet meshes register false-disconnected.
    try:
        if ORDERS_FILE.exists():
            doc = json.loads(ORDERS_FILE.read_text())
            updated_ms = doc.get("updated_at") or 0
            return updated_ms and (time.time() * 1000 - updated_ms) < CONNECTED_THRESHOLD_MS
    except Exception:
        pass
    return False


# ── Paywall badge rendering ──────────────────────────────────


def _format_badge_text(amount):
    """Compact dollar-amount string fit for a tray badge (≤4 chars).
    Returns None when no badge should render (zero or negative)."""
    try:
        a = max(0, round(float(amount)))
    except (TypeError, ValueError):
        return None
    if a == 0:
        return None
    if a < 1000:
        return f"${a}"  # $1..$999
    if a < 100_000:
        return f"${a // 1000}k"  # $1k..$99k
    return "$$$"


def _rounded_rect(ctx, x, y, w, h, r):
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2)
    ctx.arc(x + r, y + h - r, r, math.pi / 2, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3 * math.pi / 2)
    ctx.close_path()


def _render_badged_icon(base_png, out_png, text):
    """Composite `text` as a red badge in the bottom-right of `base_png`,
    write the result to `out_png`. Caller decides when to invoke."""
    base = cairo.ImageSurface.create_from_png(str(base_png))
    w, h = base.get_width(), base.get_height()
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, w, h)
    ctx = cairo.Context(surface)
    ctx.set_source_surface(base, 0, 0)
    ctx.paint()

    # Badge geometry. The base PNG is wide (16:9-ish) but SNI hosts often
    # square it via fit-or-crop, so we anchor at bottom-right of the canvas
    # and size the badge generously so it stays legible after downscale.
    bw = int(w * 0.42)
    bh = int(h * 0.42)
    bx = w - bw - int(w * 0.02)
    by = h - bh - int(h * 0.02)
    radius = int(min(bw, bh) * 0.28)

    ctx.set_source_rgba(0, 0, 0, 0.55)
    _rounded_rect(ctx, bx - 6, by - 6, bw + 12, bh + 12, radius + 4)
    ctx.fill()

    ctx.set_source_rgba(0.86, 0.14, 0.14, 1.0)
    _rounded_rect(ctx, bx, by, bw, bh, radius)
    ctx.fill()

    layout = PangoCairo.create_layout(ctx)
    font_px = max(12, int(bh * 0.72))
    desc = Pango.FontDescription(f"Sans Bold {font_px}px")
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    tw, th = layout.get_pixel_size()
    while tw > bw - int(bw * 0.12) and font_px > 8:
        font_px -= 2
        desc = Pango.FontDescription(f"Sans Bold {font_px}px")
        layout.set_font_description(desc)
        tw, th = layout.get_pixel_size()

    ctx.set_source_rgb(1, 1, 1)
    ctx.move_to(bx + (bw - tw) / 2, by + (bh - th) / 2)
    PangoCairo.show_layout(ctx, layout)
    surface.write_to_png(str(out_png))


# ── Status polling ───────────────────────────────────────────


class StatusPoller:
    """Polls /admin/status on the relay; emits state to a callback.

    Network calls happen on a background thread so a slow / hung relay
    never blocks the GTK main loop. Results are marshalled back via
    GLib.idle_add so the tray-update happens on the GTK thread."""

    def __init__(self, on_state):
        self.on_state = on_state
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="focuslock-tray-poll")

    def start(self):
        self._thread.start()

    def stop(self):
        self._stop.set()

    def _loop(self):
        while not self._stop.is_set():
            try:
                state = self._fetch_once()
            except Exception:
                logger.exception("status fetch failed")
                state = None
            GLib.idle_add(self.on_state, state)
            # Use Event.wait() so stop() interrupts cleanly
            if self._stop.wait(POLL_INTERVAL_S):
                break

    def _fetch_once(self):
        # Slave/desktop-collar path: read orders.json that focuslock-desktop.py
        # writes locally on every mesh poll. No network call needed — the
        # daemon already did the work and persisted authoritative state.
        # The previous /admin/status path required an admin_token that
        # slaves never have, so the tray was permanently "Offline (config-missing)".
        if ORDERS_FILE.exists():
            try:
                doc = json.loads(ORDERS_FILE.read_text())
            except Exception as e:
                return {"error": f"parse-{type(e).__name__}"}
            # Stale guard: if the daemon died, orders.json freezes and we must
            # not show a phantom-fresh state. But orders.json ALSO freezes on a
            # perfectly healthy quiet mesh — `updated_at` only advances on
            # bump_version(), and the vault poll returns early ("if not blobs")
            # when the relay has nothing new. Judging liveness by it therefore
            # reported "stale" on any mesh the Lion had simply left alone for a
            # minute. Ask the heartbeat instead, and keep the same threshold the
            # bunny uses so the icon and the label can never disagree.
            age_ms = _heartbeat_age_ms()
            if age_ms is not None:
                if age_ms > CONNECTED_THRESHOLD_MS:
                    return {"error": "stale"}
                return doc
            # No heartbeat at all: pre-heartbeat daemon, so fall back to
            # orders.json age and accept the quiet-mesh false positive.
            updated_ms = doc.get("updated_at") or 0
            if updated_ms and (time.time() * 1000 - updated_ms) > STALE_AFTER_MS:
                return {"error": "stale"}
            return doc
        # Fallback for operator PCs that run the tray against a relay.
        cfg = load_config()
        mesh_url = (cfg.get("mesh_url") or "").rstrip("/")
        token = cfg.get("admin_token") or os.environ.get("FOCUSLOCK_ADMIN_TOKEN", "")
        if not mesh_url or not token:
            return {"error": "no-orders-file"}
        mesh_id = cfg.get("mesh_id", "")
        params = {"admin_token": token}
        if mesh_id:
            params["mesh_id"] = mesh_id
        url = f"{mesh_url}/admin/status?{urllib.parse.urlencode(params)}"
        try:
            with urllib.request.urlopen(url, timeout=HTTP_TIMEOUT_S) as resp:
                if resp.status != 200:
                    return {"error": f"http-{resp.status}"}
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            return {"error": f"http-{e.code}"}
        except Exception as e:
            return {"error": f"net-{type(e).__name__}"}


# ── Tray indicator ───────────────────────────────────────────


class FocusLockTray:
    APP_ID = "focuslock-tray"

    def __init__(self):
        # AppIndicator/SNI icons must be theme NAMES, not absolute paths —
        # plasmashell (KDE) and Ayatana hosts treat the second arg of
        # Indicator.new() as a freedesktop icon name and look it up via
        # IconThemePath. Passing a path renders blank on KDE. Set theme
        # path to our icons dir, then reference by basename without ext.
        self.indicator = AppIndicator.Indicator.new(
            self.APP_ID,
            "bunny-gray",
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_icon_theme_path(str(ICONS_DIR))
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("The Collar")
        self.menu = Gtk.Menu()
        self.menu_status = Gtk.MenuItem(label="Connecting…")
        self.menu_status.set_sensitive(False)
        self.menu.append(self.menu_status)
        self.menu.append(Gtk.SeparatorMenuItem())

        item_companion = Gtk.MenuItem(label="Open Companion")
        item_companion.connect("activate", self._on_open_companion)
        self.menu.append(item_companion)

        item_open = Gtk.MenuItem(label="Open Web Remote")
        item_open.connect("activate", self._on_open_web)
        self.menu.append(item_open)

        item_log = Gtk.MenuItem(label="Open Log")
        item_log.connect("activate", self._on_open_log)
        self.menu.append(item_log)

        item_mesh = Gtk.MenuItem(label="Join / Configure Mesh…")
        item_mesh.connect("activate", self._on_configure_mesh)
        self.menu.append(item_mesh)

        self.menu.append(Gtk.SeparatorMenuItem())
        item_quit = Gtk.MenuItem(label="Quit Tray")
        item_quit.connect("activate", self._on_quit)
        self.menu.append(item_quit)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        # Badge rotation state. SNI hosts cache icons by name, so each new
        # amount needs a fresh filename. Counter increments on every change;
        # we keep at most the previous file alive (deleted on next render).
        self._badge_counter = 0
        self._badge_key = None  # (base_name, badge_text) currently shown
        self._badge_path = None  # Path to delete on next rotation
        # Best-effort sweep of leftover badge files from prior runs.
        try:
            for stale in ICONS_DIR.glob("bunny-*-b*.png"):
                stale.unlink()
        except Exception:
            pass

        self.poller = StatusPoller(self._on_state)
        self.poller.start()

    def _select_icon(self, connected, amount):
        """Return icon-name to pass to set_icon_full. Renders a badged
        variant if (badge libs available) and (amount > 0); otherwise
        falls back to the bare bunny. Caller still owns the purple/gray
        selection — we just composite on top."""
        base = "bunny-purple" if connected else "bunny-gray"
        if not _BADGE_AVAILABLE:
            return base
        text = _format_badge_text(amount)
        if text is None:
            return base
        key = (base, text)
        if key == self._badge_key and self._badge_path and self._badge_path.exists():
            return self._badge_path.stem
        # New amount or new connectivity → render fresh.
        self._badge_counter += 1
        out_name = f"{base}-b{self._badge_counter}"
        out_path = ICONS_DIR / f"{out_name}.png"
        base_path = ICONS_DIR / f"{base}.png"
        if not base_path.exists():
            return base
        try:
            _render_badged_icon(base_path, out_path, text)
        except Exception:
            logger.exception("badge render failed; falling back to bare bunny")
            return base
        # Delete previous badge file (only one outstanding at a time).
        if self._badge_path and self._badge_path != out_path:
            try:
                self._badge_path.unlink()
            except Exception:
                pass
        self._badge_key = key
        self._badge_path = out_path
        return out_name

    def _on_state(self, state):
        """Called on the GTK thread by GLib.idle_add.

        Icon = bunny, PURPLE only when the device is BOTH paired (the Lion's
        pubkey is on file) AND connected (recent mesh heartbeat); GRAY otherwise
        — so an unpaired-but-reachable node reads gray, not purple. A red paywall
        badge is composited over purple when an amount is owed.
        """
        paired = _is_paired()
        connected = _is_mesh_connected()
        live = paired and connected
        # Shared status suffix for the accessibility/hover text.
        astext = "under-lion" if live else ("unpaired" if not paired else "disconnected")

        if state is None or "error" in (state or {}):
            err = (state or {}).get("error", "no-data")
            icon_name = self._select_icon(live, 0)
            self.indicator.set_icon_full(icon_name, astext)
            if not paired:
                label = "Not paired — waiting for your Lion"
            elif not connected:
                label = "Paired · disconnected"
            else:
                label = f"Connected · reading state ({err})"
            self.menu_status.set_label(label)
            return False  # don't reschedule (we're polling on a thread)

        orders = state.get("orders") or {}
        locked = state.get("locked") or str(orders.get("lock_active", "0")) == "1"
        paywall = orders.get("paywall", "0")
        try:
            paywall_f = float(paywall or 0)
        except (TypeError, ValueError):
            paywall_f = 0.0
        tier = orders.get("sub_tier") or ""

        icon_name = self._select_icon(live, paywall_f)
        self.indicator.set_icon_full(icon_name, astext)

        if not paired:
            # Registered but not yet claimed by a Lion — nothing enforces yet.
            label = "Not paired — waiting for your Lion"
        else:
            prefix = "Locked" if locked else "Unlocked"
            if not connected:
                prefix = f"{prefix} · disconnected"
            label = f"{prefix}  ·  ${paywall_f:.2f}"
            if tier:
                label += f"  ·  {tier.title()}"
        self.menu_status.set_label(label)
        return False

    # ── Menu callbacks ──

    def _on_open_companion(self, _item):
        """Open the collar's local companion page.

        Loopback, and the collar's own mesh port — the page is served by the
        running collar, so it works with no relay and no network at all, which
        is exactly the state a bunny is most likely to be checking it from.
        """
        port = load_config().get("mesh_port", 8435)
        try:
            subprocess.Popen(["xdg-open", f"http://127.0.0.1:{port}/companion"], close_fds=True)
        except Exception:
            logger.exception("xdg-open companion failed")

    def _on_open_web(self, _item):
        cfg = load_config()
        url = cfg.get("mesh_url") or ""
        if not url:
            return
        # Use xdg-open so we don't have to know the user's preferred browser.
        try:
            subprocess.Popen(["xdg-open", url.rstrip("/") + "/"], close_fds=True)
        except Exception:
            logger.exception("xdg-open failed")

    def _on_open_log(self, _item):
        log_path = "/tmp/focuslock-collar.log"
        if not os.path.exists(log_path):
            log_path = str(Path.home() / ".local/share/focuslock/collar.log")
        try:
            subprocess.Popen(["xdg-open", log_path], close_fds=True)
        except Exception:
            logger.exception("xdg-open log failed")

    def _on_configure_mesh(self, _item):
        """Let the wearer enter the mesh their Lion created (mesh id + relay URL),
        write it to config.json, and restart the collar so it re-registers. This
        is the desktop counterpart to Bunny Tasker's 'Join Mesh' — without it the
        only way to set/change the mesh was re-running the installer."""
        cfg = load_config()
        dialog = Gtk.Dialog(title="Join / Configure Mesh")
        dialog.set_modal(True)
        dialog.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dialog.add_button("Save & Connect", Gtk.ResponseType.OK)
        dialog.set_default_response(Gtk.ResponseType.OK)
        box = dialog.get_content_area()
        box.set_spacing(10)
        box.set_property("margin", 14)

        intro = Gtk.Label(
            label="Enter the mesh your Lion created.\nThey give you the Mesh ID; "
            "the Relay URL is usually already filled in."
        )
        intro.set_xalign(0.0)
        box.add(intro)

        grid = Gtk.Grid(column_spacing=10, row_spacing=8)
        lbl_id = Gtk.Label(label="Mesh ID:")
        lbl_id.set_xalign(0.0)
        grid.attach(lbl_id, 0, 0, 1, 1)
        mesh_entry = Gtk.Entry()
        mesh_entry.set_text(cfg.get("mesh_id", "") or "")
        mesh_entry.set_placeholder_text("e.g. Ab3dEf6hJ9kL")
        mesh_entry.set_hexpand(True)
        mesh_entry.set_activates_default(True)
        grid.attach(mesh_entry, 1, 0, 1, 1)

        lbl_url = Gtk.Label(label="Relay URL:")
        lbl_url.set_xalign(0.0)
        grid.attach(lbl_url, 0, 1, 1, 1)
        url_entry = Gtk.Entry()
        # Prefill only what this machine is already configured with. This used to
        # fall back to the author's own relay, so every fresh install offered a
        # stranger's host as the default and one Enter accepted it. A placeholder
        # shows the shape without putting a value in the field.
        url_entry.set_text(cfg.get("mesh_url", "") or "")
        url_entry.set_placeholder_text("https://collar.example.com")
        url_entry.set_hexpand(True)
        url_entry.set_activates_default(True)
        grid.attach(url_entry, 1, 1, 1, 1)
        box.add(grid)

        dialog.show_all()
        resp = dialog.run()
        mesh_id = mesh_entry.get_text().strip()
        mesh_url = url_entry.get_text().strip().rstrip("/")
        dialog.destroy()
        if resp == Gtk.ResponseType.OK and mesh_id and mesh_url:
            self._save_mesh_config(mesh_id, mesh_url)

    def _save_mesh_config(self, mesh_id, mesh_url):
        """Merge mesh id/url into config.json (preserving other keys) and restart
        the collar daemon so it re-registers against the new mesh."""
        cfg = load_config()
        cfg["mesh_id"] = mesh_id
        cfg["mesh_url"] = mesh_url
        cfg["vault_mode"] = True
        try:
            CONFIG_DIR.mkdir(parents=True, exist_ok=True)
            tmp = CONFIG_PATH.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(cfg, indent=2))
            os.chmod(tmp, 0o600)
            os.replace(tmp, CONFIG_PATH)
            logger.info("Mesh config updated: %s @ %s", mesh_id, mesh_url)
        except Exception:
            logger.exception("Could not write config.json")
            return
        # Restart the collar daemon (not the tray) so it reloads config + re-registers.
        try:
            subprocess.Popen(
                ["systemctl", "--user", "restart", "focuslock-desktop.service"],
                close_fds=True,
            )
            self.menu_status.set_label(f"Reconnecting to {mesh_id}…")
        except Exception:
            logger.exception("Could not restart collar service")

    def _on_quit(self, _item):
        self.poller.stop()
        if self._badge_path:
            try:
                self._badge_path.unlink()
            except Exception:
                pass
        Gtk.main_quit()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not ICON_CONNECTED.exists() and not ICON_DISCONNECTED.exists():
        logger.warning(
            "Neither %s nor %s found — install bunny icons or run installers/install-desktop-collar.sh",
            ICON_CONNECTED,
            ICON_DISCONNECTED,
        )
    FocusLockTray()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

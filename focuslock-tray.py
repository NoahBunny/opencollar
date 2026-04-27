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

Icons: gold crown when locked, gray crown when unlocked. Tooltip shows
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
import os
import subprocess
import sys
import threading
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


# ── Config ───────────────────────────────────────────────────

CONFIG_DIR = Path.home() / ".config" / "focuslock"
CONFIG_PATH = CONFIG_DIR / "config.json"
ICONS_DIR = CONFIG_DIR / "icons"
ICON_LOCKED = ICONS_DIR / "crown-gold.png"
ICON_UNLOCKED = ICONS_DIR / "crown-gray.png"

POLL_INTERVAL_S = 5
HTTP_TIMEOUT_S = 4


def load_config():
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        logger.warning("config load failed: %s", e)
        return {}


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
        cfg = load_config()
        mesh_url = (cfg.get("mesh_url") or "").rstrip("/")
        token = cfg.get("admin_token") or os.environ.get("FOCUSLOCK_ADMIN_TOKEN", "")
        mesh_id = cfg.get("mesh_id", "")
        if not mesh_url or not token:
            return {"error": "config-missing"}
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
        # Pick the gray icon as the boot state — first poll updates it.
        initial_icon = str(ICON_UNLOCKED if ICON_UNLOCKED.exists() else ICON_LOCKED)
        self.indicator = AppIndicator.Indicator.new(
            self.APP_ID,
            initial_icon,
            AppIndicator.IndicatorCategory.APPLICATION_STATUS,
        )
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
        self.indicator.set_title("The Collar")
        self.menu = Gtk.Menu()
        self.menu_status = Gtk.MenuItem(label="Connecting…")
        self.menu_status.set_sensitive(False)
        self.menu.append(self.menu_status)
        self.menu.append(Gtk.SeparatorMenuItem())

        item_open = Gtk.MenuItem(label="Open Web Remote")
        item_open.connect("activate", self._on_open_web)
        self.menu.append(item_open)

        item_log = Gtk.MenuItem(label="Open Log")
        item_log.connect("activate", self._on_open_log)
        self.menu.append(item_log)

        self.menu.append(Gtk.SeparatorMenuItem())
        item_quit = Gtk.MenuItem(label="Quit Tray")
        item_quit.connect("activate", self._on_quit)
        self.menu.append(item_quit)

        self.menu.show_all()
        self.indicator.set_menu(self.menu)

        self.poller = StatusPoller(self._on_state)
        self.poller.start()

    def _on_state(self, state):
        """Called on the GTK thread by GLib.idle_add."""
        if state is None or "error" in (state or {}):
            self.indicator.set_icon_full(str(ICON_UNLOCKED), "no signal")
            err = (state or {}).get("error", "no-data")
            self.menu_status.set_label(f"Offline ({err})")
            return False  # don't reschedule (we're polling on a thread)

        orders = state.get("orders") or {}
        locked = state.get("locked") or str(orders.get("lock_active", "0")) == "1"
        paywall = orders.get("paywall", "0")
        try:
            paywall_f = float(paywall or 0)
        except (TypeError, ValueError):
            paywall_f = 0.0
        tier = orders.get("sub_tier") or ""

        icon = ICON_LOCKED if locked else ICON_UNLOCKED
        self.indicator.set_icon_full(str(icon), "locked" if locked else "unlocked")

        if locked:
            label = f"Locked  ·  ${paywall_f:.2f}"
        else:
            label = f"Unlocked  ·  ${paywall_f:.2f}"
        if tier:
            label += f"  ·  {tier.title()}"
        self.menu_status.set_label(label)
        return False

    # ── Menu callbacks ──

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

    def _on_quit(self, _item):
        self.poller.stop()
        Gtk.main_quit()


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    if not ICON_UNLOCKED.exists() and not ICON_LOCKED.exists():
        logger.warning(
            "Neither %s nor %s found — install crown icons or run installers/install-desktop-collar.sh",
            ICON_UNLOCKED,
            ICON_LOCKED,
        )
    FocusLockTray()
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

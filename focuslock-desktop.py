#!/usr/bin/python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Desktop Collar — mirrors phone lock state to Wayland/X11 desktop.
Talks to homelab for lock state + standing orders sync.
"""

import gi

gi.require_version("Gtk", "4.0")

WEBKIT_OK = False
try:
    gi.require_version("WebKit", "6.0")
    WEBKIT_OK = True
    WEBKIT_VER = 6
except ValueError:
    try:
        gi.require_version("WebKit2", "4.1")
        WEBKIT_OK = True
        WEBKIT_VER = 4
    except ValueError:
        pass

from gi.repository import Gdk, GLib, Gtk

if WEBKIT_OK:
    if WEBKIT_VER == 6:
        from gi.repository import WebKit
    else:
        from gi.repository import WebKit2 as WebKit

import datetime
import html as _html
import json
import logging
import os
import random
import signal
import socket as _socket
import subprocess
import sys
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer

logger = logging.getLogger(__name__)

# Add mesh module to path
for _p in [os.path.dirname(os.path.abspath(__file__)), "/opt/focuslock", os.path.expanduser("~/Desktop/Focus")]:
    if os.path.isfile(os.path.join(_p, "focuslock_mesh.py")):
        sys.path.insert(0, _p)
        break
from focuslock_http import JSONResponseMixin
from focuslock_sync import direct_sync_poll as _shared_direct_sync_poll
from focuslock_sync import relay_to_phones as _shared_relay_to_phones
from focuslock_sync import try_sync as _shared_try_sync

import focuslock_mesh as mesh

# Vault crypto for E2E encrypted mesh (Phase D desktop support)
try:
    from focuslock_vault import (
        decrypt_body as vault_decrypt,
    )
    from focuslock_vault import (
        generate_keypair as vault_keygen,
    )
    from focuslock_vault import (
        slot_id_for_pubkey as vault_slot_id,
    )
    from focuslock_vault import (
        verify_signature as vault_verify,
    )

    VAULT_CRYPTO_OK = True
except ImportError:
    VAULT_CRYPTO_OK = False

# Force unbuffered output so journald sees our logs
sys.stdout = os.fdopen(sys.stdout.fileno(), "w", buffering=1)
sys.stderr = os.fdopen(sys.stderr.fileno(), "w", buffering=1)

# ── Config ──

try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared"))
    from focuslock_config import load_config
except ImportError:

    def load_config(config_path=None):
        path = config_path or os.path.expanduser("~/.config/focuslock/config.json")
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return {}


_cfg = load_config()

MESH_URL = _cfg.get("mesh_url", "") or os.environ.get("FOCUSLOCK_MESH_URL", "")
HOMELAB_URL = _cfg.get("homelab_url", "") or os.environ.get("FOCUSLOCK_HOMELAB", "")
PHONE_ADDRESSES = _cfg.get("phone_addresses", [])
PHONE_PORT = _cfg.get("phone_port", 8432)
POLL_INTERVAL = _cfg.get("poll_interval", 5)
HEARTBEAT_INTERVAL = 30  # seconds — report alive to mesh
MEMORY_SYNC_INTERVAL = 300  # 5 minutes
BANKING_URL = _cfg.get("banking", {}).get("payment_url", "")

TAUNTS = []  # Clean lock screen — no taunts on desktop

# ── Mesh Config ──

MESH_PORT = _cfg.get("mesh_port", 8435)
MESH_ID = _cfg.get("mesh_id", "")
MESH_CONFIG_DIR = os.path.expanduser("~/.config/focuslock")
MESH_ORDERS_FILE = os.path.join(MESH_CONFIG_DIR, "orders.json")
MESH_PEERS_FILE = os.path.join(MESH_CONFIG_DIR, "peers.json")
MESH_NODE_ID = _socket.gethostname()
MESH_NODE_TYPE = "desktop"

# Load Tailscale node name overrides
_ts_map = _cfg.get("tailscale_node_map", {})
if _ts_map:
    mesh.set_tailscale_node_map(_ts_map)

# ── ntfy Push Notifications ──

try:
    import focuslock_ntfy as ntfy_mod
except ImportError:
    ntfy_mod = None

_ntfy_server = _cfg.get("ntfy_server", "https://ntfy.sh")
_ntfy_topic = _cfg.get("ntfy_topic") or (f"focuslock-{MESH_ID}" if MESH_ID else "")
_ntfy_enabled = _cfg.get("ntfy_enabled", False) and bool(_ntfy_topic) and ntfy_mod is not None


def _ntfy_fn(version):
    """Best-effort ntfy publish after local order mutations."""
    if _ntfy_enabled:
        ntfy_mod.ntfy_publish(_ntfy_topic, version, _ntfy_server)


MESH_VOUCHERS_FILE = os.path.join(MESH_CONFIG_DIR, "vouchers.json")
TRUST_STORE_FILE = os.path.join(MESH_CONFIG_DIR, "trusted_peers.json")

_trust_store = mesh.TrustStore(persist_path=TRUST_STORE_FILE)
mesh_orders = mesh.OrdersDocument(persist_path=MESH_ORDERS_FILE)
mesh_peers = mesh.PeerRegistry(persist_path=MESH_PEERS_FILE, trust_store=_trust_store)
mesh_vouchers = mesh.VoucherPool(persist_path=MESH_VOUCHERS_FILE) if hasattr(mesh, "VoucherPool") else None

# ── Vault Mode (Phase D desktop support) ──

VAULT_MODE = _cfg.get("vault_mode", False) and VAULT_CRYPTO_OK and bool(MESH_ID)

# P7: Transport abstraction — pluggable vault blob read/write backend
try:
    from focuslock_transport import transport_factory

    _vault_transport = transport_factory(_cfg)
except ImportError:
    _vault_transport = None

_vault_last_version = 0
_vault_node_registered = False

VAULT_PRIVKEY_FILE = os.path.join(MESH_CONFIG_DIR, "node_privkey.pem")
VAULT_PUBKEY_FILE = os.path.join(MESH_CONFIG_DIR, "node_pubkey.pem")
_vault_privkey_pem = ""
_vault_pubkey_der = b""


def _vault_init_keypair():
    """Load or generate RSA keypair for vault mode."""
    global _vault_privkey_pem, _vault_pubkey_der
    if os.path.exists(VAULT_PRIVKEY_FILE) and os.path.exists(VAULT_PUBKEY_FILE):
        with open(VAULT_PRIVKEY_FILE) as f:
            _vault_privkey_pem = f.read()
        # Derive DER from PEM
        from cryptography.hazmat.primitives import serialization

        pk = serialization.load_pem_private_key(_vault_privkey_pem.encode(), password=None)
        _vault_pubkey_der = pk.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        print(f"[vault] Loaded keypair (slot={vault_slot_id(_vault_pubkey_der)})")
    else:
        priv, pub, der = vault_keygen()
        os.makedirs(MESH_CONFIG_DIR, exist_ok=True)
        with open(VAULT_PRIVKEY_FILE, "w") as f:
            f.write(priv)
        os.chmod(VAULT_PRIVKEY_FILE, 0o600)
        with open(VAULT_PUBKEY_FILE, "w") as f:
            f.write(pub)
        _vault_privkey_pem = priv
        _vault_pubkey_der = der
        print(f"[vault] Generated new keypair (slot={vault_slot_id(der)})")


# P6.5: approved node pubkeys cache for multi-signer verification
_approved_node_pubkeys = []
_nodes_last_fetch = 0
_NODES_REFRESH_SECS = 1800  # 30 minutes


def _vault_fetch_nodes():
    """Fetch approved vault node pubkeys for multi-signer verification."""
    global _approved_node_pubkeys, _nodes_last_fetch
    try:
        if _vault_transport:
            nodes = _vault_transport.nodes(MESH_ID)
        elif MESH_URL:
            url = f"{MESH_URL}/vault/{MESH_ID}/nodes"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            nodes = data.get("nodes", [])
        else:
            return
        _approved_node_pubkeys = [n.get("node_pubkey", "") for n in nodes if n.get("node_pubkey")]
        _nodes_last_fetch = time.time()
        print(f"[vault] Fetched {len(_approved_node_pubkeys)} approved node pubkeys")
    except Exception as e:
        print(f"[vault] Fetch nodes error: {e}")


_lazy_refresh_done_this_poll = False


def _vault_verify_any_signer(blob, lion_pub):
    """P6.5: Verify blob against Lion pubkey OR any approved node pubkey.
    Returns True if any signer matches. Lazy-refreshes nodes once per poll cycle."""
    global _lazy_refresh_done_this_poll
    if lion_pub and vault_verify(blob, lion_pub):
        return True
    for pk in _approved_node_pubkeys:
        if pk and vault_verify(blob, pk):
            return True
    # Lazy refresh — at most once per poll cycle to prevent amplification
    if not _lazy_refresh_done_this_poll:
        _lazy_refresh_done_this_poll = True
        _vault_fetch_nodes()
        for pk in _approved_node_pubkeys:
            if pk and vault_verify(blob, pk):
                return True
    return False


def _vault_register_node():
    """Register this desktop as a vault recipient if not already."""
    global _vault_node_registered
    if _vault_node_registered:
        return
    if not _vault_transport and not MESH_URL:
        return
    import base64

    pubkey_b64 = base64.b64encode(_vault_pubkey_der).decode()
    payload = {
        "node_id": MESH_NODE_ID,
        "node_type": "desktop",
        "node_pubkey": pubkey_b64,
    }
    try:
        if _vault_transport:
            result = _vault_transport.register_node(MESH_ID, payload)
        else:
            body = json.dumps(payload).encode()
            req = urllib.request.Request(
                f"{MESH_URL}/vault/{MESH_ID}/register-node-request",
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                result = json.loads(resp.read())
        if result.get("ok") or result.get("status") in ("approved", "pending"):
            _vault_node_registered = True
            print(f"[vault] Node registered: {result}")
    except Exception as e:
        print(f"[vault] Register error (will retry): {e}")


def _vault_poll():
    """Fetch and decrypt vault blobs since last known version."""
    global _vault_last_version, _lazy_refresh_done_this_poll
    if not _vault_privkey_pem:
        return
    if not _vault_transport and not MESH_URL:
        return
    _lazy_refresh_done_this_poll = False  # Reset per-poll rate limiter
    _vault_register_node()
    # P6.5: periodically refresh approved node pubkeys
    if time.time() - _nodes_last_fetch > _NODES_REFRESH_SECS:
        _vault_fetch_nodes()
    try:
        # P7: use transport abstraction if available
        if _vault_transport:
            blobs, _ = _vault_transport.since(MESH_ID, _vault_last_version)
        else:
            url = f"{MESH_URL}/vault/{MESH_ID}/since/{_vault_last_version}"
            req = urllib.request.Request(url, method="GET")
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            blobs = data.get("blobs", [])
        if not blobs:
            return
        lion_pub = get_lion_pubkey()
        for blob in blobs:
            v = blob.get("version", 0)
            if v <= _vault_last_version:
                continue
            # P6.5: verify against Lion pubkey OR any approved node (relay, etc.)
            if not _vault_verify_any_signer(blob, lion_pub):
                print(f"[vault] Signature verification FAILED for v{v} — skipping")
                continue
            # Decrypt
            plaintext = vault_decrypt(blob, _vault_privkey_pem, _vault_pubkey_der)
            if plaintext is None:
                # We might not have a slot (not yet approved)
                continue
            body = json.loads(plaintext)
            # Apply orders from decrypted body
            if "action" in body:
                # RPC blob from Lion or relay
                mesh_apply_order(body["action"], body.get("params", {}), mesh_orders)
                mesh_orders.bump_version()
            else:
                # Runtime or order snapshot — apply all fields
                for k, val in body.items():
                    if k in mesh.ORDER_KEYS:
                        mesh_orders.set(k, val)
                if body:
                    mesh_orders.bump_version()
            _vault_last_version = v
            print(f"[vault] Applied v{v} ({len(body)} fields)")
        on_mesh_orders_applied(dict(mesh_orders.orders))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            pass  # Vault not set up yet for this mesh
        else:
            print(f"[vault] Poll error: HTTP {e.code}")
    except Exception as e:
        print(f"[vault] Poll error: {e}")


_vault_poll_running = False


def _vault_poll_tick():
    """Non-blocking vault poll on a background thread."""
    global _vault_poll_running
    if _vault_poll_running:
        return True
    _vault_poll_running = True

    def _run():
        global _vault_poll_running
        try:
            _vault_poll()
        finally:
            _vault_poll_running = False

    threading.Thread(target=_run, daemon=True).start()
    return True


_lion_pubkey = ""
LION_PUBKEY_FILE = os.path.join(MESH_CONFIG_DIR, "lion_pubkey.pem")


def get_lion_pubkey():
    global _lion_pubkey
    if not _lion_pubkey and os.path.exists(LION_PUBKEY_FILE):
        try:
            with open(LION_PUBKEY_FILE, "r") as f:
                _lion_pubkey = f.read().strip()
            if _lion_pubkey:
                print(f"[mesh] Loaded Lion's Share pubkey from {LION_PUBKEY_FILE}")
        except Exception:
            pass
    return _lion_pubkey


def seed_mesh_peers():
    """Seed mesh peers from config."""
    if HOMELAB_URL:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(HOMELAB_URL)
            if parsed.hostname:
                _trust_store.trust("homelab", "config")
                mesh_peers.update_peer(
                    "homelab",
                    node_type="server",
                    addresses=[parsed.hostname],
                    port=parsed.port or _cfg.get("homelab_port", 8434),
                )
        except Exception:
            pass
    for addr in PHONE_ADDRESSES:
        _trust_store.trust("phone", "config")
        mesh_peers.update_peer("phone", node_type="phone", addresses=[addr], port=PHONE_PORT)


def mesh_local_status():
    """Build this desktop's status for gossip."""
    return {
        "type": "desktop",
        "hostname": MESH_NODE_ID,
        "locked": state.locked,
    }


def _try_sync(url, name, my_addrs, lion_pubkey):
    """Attempt mesh sync with a single endpoint. Returns True on success."""
    return _shared_try_sync(
        url,
        name,
        node_id=MESH_NODE_ID,
        node_type=MESH_NODE_TYPE,
        my_addrs=my_addrs,
        mesh_port=MESH_PORT,
        mesh_orders=mesh_orders,
        mesh_peers=mesh_peers,
        local_status=mesh_local_status(),
        lion_pubkey=lion_pubkey,
        on_orders_applied=on_mesh_orders_applied,
        pin=_cfg.get("pin", "") or str(mesh_orders.get("pin", "")),
        mesh_id=MESH_ID,
    )


def direct_sync_poll():
    """Poll mesh — try configured endpoints in priority order, then discovered peers."""
    print(f"[collar] Direct sync: polling (local v{mesh_orders.version})")
    _shared_direct_sync_poll(
        mesh_url=MESH_URL,
        homelab_url=HOMELAB_URL,
        phone_addresses=PHONE_ADDRESSES,
        phone_port=PHONE_PORT,
        node_id=MESH_NODE_ID,
        node_type=MESH_NODE_TYPE,
        mesh_port=MESH_PORT,
        mesh_orders=mesh_orders,
        mesh_peers=mesh_peers,
        local_status_fn=mesh_local_status,
        lion_pubkey_fn=get_lion_pubkey,
        on_orders_applied=on_mesh_orders_applied,
        get_local_addresses_fn=mesh.get_local_addresses,
        pin=_cfg.get("pin", "") or str(mesh_orders.get("pin", "")),
        get_tailscale_ip_fn=mesh.get_tailscale_ip_for_node,
        mesh_id=MESH_ID,
    )


_direct_sync_running = False


def _direct_sync_tick():
    global _direct_sync_running
    if _direct_sync_running:
        return True
    _direct_sync_running = True

    def _run():
        global _direct_sync_running
        try:
            direct_sync_poll()
        finally:
            _direct_sync_running = False

    threading.Thread(target=_run, daemon=True).start()
    return True


def _handle_countdown(lock_at_ms: int, message: str):
    """Handle countdown-to-lock: warnings, escalation, and lock trigger."""
    now_ms = int(time.time() * 1000)

    if not lock_at_ms:
        state.countdown_lock_at = 0
        state.countdown_message = ""
        return

    remaining_ms = lock_at_ms - now_ms
    remaining_min = remaining_ms / 60000

    # Countdown expired — trigger the lock
    if remaining_ms <= 0:
        if state.countdown_lock_at > 0:
            print("[collar] Countdown expired — locking")
            mesh_orders.set("desktop_active", 1)
            mesh_orders.set("countdown_lock_at", 0)
            mesh_orders.set("countdown_message", "")
            mesh.bump_and_broadcast(mesh_orders, MESH_NODE_ID, mesh_peers, ntfy_fn=_ntfy_fn)
            state.countdown_lock_at = 0
            state.countdown_message = ""
        return

    state.countdown_lock_at = lock_at_ms
    state.countdown_message = message

    # Determine warning interval based on time remaining
    if remaining_min <= 1:
        warn_interval_ms = 10_000
    elif remaining_min <= 5:
        warn_interval_ms = 30_000
    else:
        warn_interval_ms = 60_000

    since_last = now_ms - state.countdown_last_warn
    if since_last >= warn_interval_ms:
        state.countdown_last_warn = now_ms
        _show_countdown_warning(remaining_ms, message)


def _show_countdown_warning(remaining_ms: int, message: str):
    """Show a desktop notification with countdown info."""
    remaining_sec = remaining_ms // 1000
    if remaining_sec >= 60:
        mins = remaining_sec // 60
        time_str = f"{mins} minute{'s' if mins != 1 else ''}"
    else:
        time_str = f"{remaining_sec} seconds"

    title = f"Lock in {time_str}"
    body = message if message else "The Lion has spoken."

    # Use notify-send for desktop notification
    try:
        import subprocess

        subprocess.Popen(
            ["notify-send", "-u", "critical", "-i", "dialog-warning", title, body],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        pass

    # System bell
    try:
        import subprocess

        if remaining_sec <= 60:
            subprocess.Popen(
                ["paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        else:
            subprocess.Popen(
                ["paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass

    print(f"[collar] Countdown: {time_str} remaining" + (f" — {message}" if message else ""))


_poll_status_fn = None  # Set by CollarApp.do_activate so gossip can trigger immediate refresh

_last_phone_lock_active = None  # track for ADB statusbar enforcement


def _adb_statusbar_enforce(lock_active):
    """Toggle phone statusbar via ADB shell when lock state changes.
    Only works from a machine with an active ADB connection to the phone
    (vir). Silently no-ops if no phone is connected."""
    cmd = "true" if lock_active else "false"
    adb_port = os.environ.get("ANDROID_ADB_SERVER_PORT", "15037")
    try:
        out = subprocess.check_output(
            ["adb", "devices"],
            env={**os.environ, "ANDROID_ADB_SERVER_PORT": adb_port},
            timeout=5,
            stderr=subprocess.DEVNULL,
        ).decode()
        for line in out.strip().splitlines()[1:]:
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "device":
                dev = parts[0]
                subprocess.Popen(
                    ["adb", "-s", dev, "shell", "cmd", "statusbar", "disable-for-setup", cmd],
                    env={**os.environ, "ANDROID_ADB_SERVER_PORT": adb_port},
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )
                print(f"[collar] ADB statusbar disable-for-setup {cmd} → {dev}")
    except Exception as e:
        print(f"[collar] ADB statusbar enforce skipped: {e}")


def _phone_statusbar_loop():
    """Poll phone lock state via HTTP every 10s and ADB-enforce statusbar.
    Independent of gossip/vault — works even when vault node is pending."""
    global _last_phone_lock_active
    if not PHONE_ADDRESSES:
        return
    while True:
        try:
            time.sleep(10)
            for addr in PHONE_ADDRESSES:
                url = f"http://{addr}:{PHONE_PORT}/api/status"
                req = urllib.request.Request(url, method="GET")
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read())
                lock_active = 1 if data.get("locked") else 0
                if lock_active != _last_phone_lock_active:
                    _last_phone_lock_active = lock_active
                    _adb_statusbar_enforce(lock_active)
                break  # first reachable phone is enough
        except Exception:
            pass  # phone unreachable — skip silently


def on_mesh_orders_applied(orders_dict):
    """Called when gossip applies new orders.
    Do NOT update state.locked here — poll_status on the GTK thread is the
    sole writer so it can detect transitions and call show_lock/hide_lock.
    Schedule an immediate poll so the UI refreshes within ~100ms instead of
    waiting up to POLL_INTERVAL (5s)."""
    print(
        f"[collar] Mesh orders applied: desktop_active={orders_dict.get('desktop_active')} "
        f"lock_active={orders_dict.get('lock_active')}"
    )
    # ADB statusbar enforcement is handled by _phone_statusbar_loop (HTTP poll),
    # NOT gossip — gossip lock_active can be stale when vault node is pending.
    if _poll_status_fn:
        GLib.idle_add(lambda: (_poll_status_fn(), False)[1])


def _relay_to_phones(action, params):
    """Forward an order to all known phone peers via mesh push."""
    _shared_relay_to_phones(
        action,
        params,
        mesh_orders=mesh_orders,
        mesh_peers=mesh_peers,
        node_id=MESH_NODE_ID,
        pin=_cfg.get("pin", "") or str(mesh_orders.get("pin", "")),
    )


def mesh_apply_order(action, params, orders):
    """Apply an order locally on the desktop. Relay phone-targeted actions."""
    # Relay lock/unlock to phone peers (desktop acts as transparent relay)
    if action in ("lock", "unlock", "add-paywall", "clear-paywall"):
        threading.Thread(target=_relay_to_phones, args=(action, params), daemon=True).start()

    if action == "lock":
        orders.set("lock_active", 1)
        if "message" in params:
            orders.set("message", params["message"])
        if "mode" in params:
            orders.set("mode", params["mode"])
        if "paywall" in params:
            orders.set("paywall", str(params["paywall"]))
    elif action == "unlock":
        orders.set("lock_active", 0)
    elif action == "lock-device":
        target = params.get("target", "")
        if target == "desktop" or target == "all":
            orders.set("desktop_active", 1)
        elif target:
            devices = str(orders.get("desktop_locked_devices", ""))
            devlist = [d for d in devices.split(",") if d]
            if target not in devlist:
                devlist.append(target)
            orders.set("desktop_locked_devices", ",".join(devlist))
    elif action == "unlock-device":
        target = params.get("target", "")
        if target == "desktop" or target == "all":
            orders.set("desktop_active", 0)
            orders.set("desktop_locked_devices", "")
        elif target:
            devices = str(orders.get("desktop_locked_devices", ""))
            devlist = [d for d in devices.split(",") if d and d != target]
            orders.set("desktop_locked_devices", ",".join(devlist))
    elif action == "release-device":
        target = params.get("target", "")
        if target == "all" or target == MESH_NODE_ID:
            orders.set("released", target)
            orders.set("release_timestamp", str(int(datetime.datetime.now().timestamp() * 1000)))
            GLib.idle_add(_execute_liberation)
    return {"applied": action}


def _execute_liberation():
    """Permanently remove the desktop collar. Called on GTK main thread."""
    import subprocess

    print("[collar] LIBERATION — permanently removing collar")

    # 1. Unlock the session
    try:
        subprocess.run(["loginctl", "unlock-session"], capture_output=True, timeout=5)
    except Exception:
        pass

    # 2. Restore wallpaper
    try:
        restore_to = _get_kde_default_wallpaper()
        orig_file = os.path.join(MESH_CONFIG_DIR, "original-wallpaper")
        if os.path.exists(orig_file):
            with open(orig_file) as f:
                restore_to = f.read().strip() or restore_to
        if restore_to:
            cfg_path = os.path.expanduser("~/.config/kscreenlockerrc")
            if os.path.exists(cfg_path):
                with open(cfg_path, "r") as f:
                    lines = f.readlines()
                in_section = False
                new_lines = []
                for line in lines:
                    if line.strip() == "[Greeter][Wallpaper][org.kde.image][General]":
                        in_section = True
                        new_lines.append(line)
                        continue
                    if in_section and line.strip().startswith("["):
                        in_section = False
                    if in_section and line.strip().startswith("Image="):
                        new_lines.append(f"Image={restore_to}\n")
                        continue
                    if in_section and line.strip().startswith("PreviewImage="):
                        new_lines.append(f"PreviewImage={restore_to}\n")
                        continue
                    new_lines.append(line)
                with open(cfg_path, "w") as f:
                    f.writelines(new_lines)
    except Exception as e:
        print(f"[collar] Wallpaper restore error: {e}")

    # 3. Show liberation notice
    _show_liberation_dialog()

    # 4. Check for dual-boot Windows
    _check_dual_boot_and_mark()

    # 5. Schedule cleanup after dialog (give it time to show)
    GLib.timeout_add(3000, _liberation_cleanup)
    return False


def _show_liberation_dialog():
    """Show a GTK4 dialog announcing liberation."""
    try:
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(b"""
            .liberation-title { color: #44cc44; font-size: 36px; letter-spacing: 8px; font-weight: bold; }
            .liberation-body { color: #aaaaaa; font-size: 16px; }
            .liberation-win { background-color: #0a0a14; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        win = Gtk.Window(title="Liberated")
        win.set_default_size(500, 300)
        win.set_resizable(False)
        win.add_css_class("liberation-win")

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        box.set_margin_top(40)
        box.set_margin_bottom(40)
        box.set_margin_start(40)
        box.set_margin_end(40)
        box.set_halign(Gtk.Align.CENTER)
        box.set_valign(Gtk.Align.CENTER)

        title = Gtk.Label(label="LIBERATED")
        title.add_css_class("liberation-title")
        box.append(title)

        body = Gtk.Label(
            label=(
                "This device has been released from the FocusLock mesh.\n\n"
                "All restrictions are lifted.\n"
                "The collar is gone.\n\n"
                "You are free."
            )
        )
        body.set_wrap(True)
        body.set_justify(Gtk.Justification.CENTER)
        body.add_css_class("liberation-body")
        box.append(body)

        win.set_child(box)
        win.present()
        GLib.timeout_add_seconds(10, lambda: (win.close(), False)[1])
    except Exception as e:
        print(f"[collar] Liberation dialog error: {e}")


def _check_dual_boot_and_mark():
    """If a Windows partition exists, drop a one-time liberation notice."""
    import subprocess

    efi_path = "/boot/efi"
    try:
        result = subprocess.run(
            ["findmnt", "-n", "-o", "TARGET", "/boot/efi"], capture_output=True, text=True, timeout=5
        )
        if result.returncode == 0 and result.stdout.strip():
            efi_path = result.stdout.strip()
    except Exception:
        pass

    windows_boot = os.path.join(efi_path, "EFI", "Microsoft", "Boot")
    if not os.path.isdir(windows_boot):
        print("[collar] No Windows dual-boot detected — skipping")
        return

    print("[collar] Windows dual-boot detected — dropping liberation marker")

    # Find NTFS partitions and look for Windows Startup folders
    try:
        result = subprocess.run(
            ["lsblk", "-f", "-n", "-o", "NAME,FSTYPE,MOUNTPOINT"], capture_output=True, text=True, timeout=5
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 2 and parts[1] == "ntfs":
                dev = "/dev/" + parts[0].strip().lstrip("|`-")
                mountpoint = parts[2] if len(parts) >= 3 else None
                was_mounted = mountpoint is not None

                if not mountpoint:
                    mountpoint = "/tmp/focuslock-winmount"
                    os.makedirs(mountpoint, exist_ok=True)
                    r = subprocess.run(
                        ["sudo", "mount", "-t", "ntfs3", "-o", "rw", dev, mountpoint], capture_output=True, timeout=10
                    )
                    if r.returncode != 0:
                        continue

                users_dir = os.path.join(mountpoint, "Users")
                if os.path.isdir(users_dir):
                    for user in os.listdir(users_dir):
                        startup = os.path.join(
                            users_dir,
                            user,
                            "AppData",
                            "Roaming",
                            "Microsoft",
                            "Windows",
                            "Start Menu",
                            "Programs",
                            "Startup",
                        )
                        if os.path.isdir(startup):
                            _write_windows_liberation_ps1(startup)

                if not was_mounted:
                    subprocess.run(["sudo", "umount", mountpoint], capture_output=True, timeout=10)
    except Exception as e:
        print(f"[collar] Windows partition scan error: {e}")


def _write_windows_liberation_ps1(startup_dir):
    """Write a self-deleting PowerShell notice to a Windows Startup folder."""
    ps1_path = os.path.join(startup_dir, "FocusLock-Liberation.ps1")
    script = """Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

$form = New-Object System.Windows.Forms.Form
$form.Text = "FocusLock Liberation"
$form.Size = New-Object System.Drawing.Size(520, 340)
$form.StartPosition = "CenterScreen"
$form.BackColor = [System.Drawing.Color]::FromArgb(10, 10, 20)
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false

$title = New-Object System.Windows.Forms.Label
$title.Text = "LIBERATED"
$title.Font = New-Object System.Drawing.Font("Segoe UI", 28, [System.Drawing.FontStyle]::Bold)
$title.ForeColor = [System.Drawing.Color]::FromArgb(68, 204, 68)
$title.AutoSize = $true
$title.Location = New-Object System.Drawing.Point(150, 30)
$form.Controls.Add($title)

$body = New-Object System.Windows.Forms.Label
$body.Text = "This device has been released from the FocusLock mesh.`n`nAll restrictions are lifted.`nThe collar is gone.`n`nYou are free."
$body.Font = New-Object System.Drawing.Font("Segoe UI", 11)
$body.ForeColor = [System.Drawing.Color]::FromArgb(170, 170, 170)
$body.Size = New-Object System.Drawing.Size(440, 140)
$body.Location = New-Object System.Drawing.Point(40, 90)
$form.Controls.Add($body)

$btn = New-Object System.Windows.Forms.Button
$btn.Text = "OK"
$btn.Location = New-Object System.Drawing.Point(210, 260)
$btn.Size = New-Object System.Drawing.Size(100, 32)
$btn.Add_Click({ $form.Close() })
$form.Controls.Add($btn)

$form.ShowDialog()
Remove-Item -Path $MyInvocation.MyCommand.Path -Force
"""
    try:
        with open(ps1_path, "w") as f:
            f.write(script)
        print(f"[collar] Windows liberation notice dropped: {ps1_path}")
    except Exception as e:
        print(f"[collar] Failed to write Windows notice: {e}")


def _liberation_cleanup():
    """Delete all FocusLock config and disable the service."""
    import shutil
    import subprocess

    # Disable and stop systemd service
    try:
        subprocess.run(["systemctl", "--user", "disable", "focuslock-desktop.service"], capture_output=True, timeout=10)
    except Exception:
        pass

    # Delete all FocusLock files
    paths_to_remove = [
        os.path.expanduser("~/.config/focuslock"),
        os.path.expanduser("~/.local/share/focuslock"),
        os.path.expanduser("~/.config/systemd/user/focuslock-desktop.service"),
        os.path.expanduser("~/.config/autostart/focuslock-desktop.desktop"),
    ]
    collar_files = os.path.expanduser("~/collar-files")
    if os.path.isdir(collar_files):
        paths_to_remove.append(collar_files)

    for p in paths_to_remove:
        try:
            if os.path.isdir(p):
                shutil.rmtree(p)
            elif os.path.isfile(p):
                os.remove(p)
            print(f"[collar] Removed: {p}")
        except Exception as e:
            print(f"[collar] Could not remove {p}: {e}")

    # Reload systemd
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
    except Exception:
        pass

    print("[collar] Liberation complete. Exiting.")
    import sys

    sys.exit(0)
    return False


# ── Pairing Code Generation ──


def _create_pairing_code(body):
    """Generate a pairing code with config payload for new devices."""
    import re as _re
    import secrets as _rnd
    import string as _str

    chars = _str.ascii_uppercase + _str.digits
    code = body.get("code") or "".join(_rnd.choice(chars) for _ in range(6))
    code = str(code).upper().strip()
    # SECURITY: pairing code must be alphanumeric only (used as filename)
    if not _re.match(r"^[A-Z0-9]{4,12}$", code):
        return {"ok": False, "error": "invalid code format"}
    expires_min = body.get("expires_minutes", 60)
    import time as _t

    my_addrs = mesh.get_local_addresses()
    config = {
        "addresses": my_addrs,
        "port": MESH_PORT,
        "mesh_pin": _cfg.get("pin", "") or str(mesh_orders.get("pin", "")),
        "pubkey_pem": get_lion_pubkey() or "",
        "homelab_url": HOMELAB_URL,
        "mesh_url": MESH_URL,
        "created_at": _t.time(),
        "expires_at": _t.time() + expires_min * 60,
    }
    code_dir = os.path.join(MESH_CONFIG_DIR, "pairing-codes")
    os.makedirs(code_dir, exist_ok=True)
    with open(os.path.join(code_dir, f"{code}.json"), "w") as f:
        json.dump(config, f, indent=2)
    print(f"[collar] Pairing code created: {code}")
    return {"ok": True, "code": code, "url": f"/api/pair/{code}", "expires_minutes": expires_min}


# ── Mesh HTTP Server (port 8435) ──


class MeshHandler(JSONResponseMixin, BaseHTTPRequestHandler):
    MAX_BODY_BYTES = 1_048_576  # 1 MB — matches server

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            self.send_response(400)
            self.end_headers()
            return
        if length > self.MAX_BODY_BYTES:
            self.send_response(413)
            self.end_headers()
            return
        body = self.rfile.read(length).decode() if length > 0 else ""
        try:
            data = json.loads(body) if body else {}
        except (json.JSONDecodeError, ValueError):
            self._respond(400, {"error": "invalid JSON"})
            return

        if self.path == "/mesh/sync":
            result = mesh.handle_mesh_sync(
                data,
                MESH_NODE_ID,
                MESH_NODE_TYPE,
                mesh.get_local_addresses(),
                MESH_PORT,
                mesh_orders,
                mesh_peers,
                mesh_local_status(),
                get_lion_pubkey(),
                on_mesh_orders_applied,
            )
            self._respond(200, result)

        elif self.path == "/mesh/order":
            result = mesh.handle_mesh_order(
                data,
                mesh_orders,
                mesh_peers,
                MESH_NODE_ID,
                apply_fn=mesh_apply_order,
                lion_pubkey=get_lion_pubkey(),
                on_orders_applied=on_mesh_orders_applied,
            )
            # Return 403 on auth failure so the attacker sees a clear reject
            if isinstance(result, dict) and "unauthenticated" in result.get("error", ""):
                self._respond(403, result)
            else:
                self._respond(200, result)

        elif self.path == "/api/pair/create":
            result = _create_pairing_code(data)
            self._respond(200, result)

        elif self.path == "/mesh/store-vouchers" and mesh_vouchers:
            result = mesh.handle_store_vouchers(data, mesh_vouchers, get_lion_pubkey())
            self._respond(200, result)

        elif self.path == "/mesh/redeem-voucher" and mesh_vouchers:
            result = mesh.handle_redeem_voucher(
                data,
                mesh_vouchers,
                mesh_orders,
                mesh_peers,
                MESH_NODE_ID,
                get_lion_pubkey(),
                on_mesh_orders_applied,
                ntfy_fn=_ntfy_fn,
            )
            self._respond(200, result)
        else:
            self._respond(404, {"error": "not found"})

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/mesh/ping":
            self._respond(200, mesh.handle_mesh_ping(MESH_NODE_ID, mesh_orders))
        elif path.startswith("/mesh/status"):
            self._respond(200, mesh.handle_mesh_status(mesh_orders, mesh_peers, MESH_NODE_ID, mesh_local_status()))
        elif path == "/mesh/vouchers" and mesh_vouchers:
            self._respond(200, mesh.handle_get_vouchers(mesh_vouchers))
        elif path in ("/", "/index.html"):
            self._serve_web_ui()
        elif path.startswith("/api/pair/") and len(path) > len("/api/pair/"):
            self._serve_pairing_code(path.split("/")[-1])
        else:
            self._respond(404, {"error": "not found"})

    def _serve_web_ui(self):
        """Serve Lion's Share web UI."""
        for search_dir in [MESH_CONFIG_DIR, "/opt/focuslock", os.path.dirname(os.path.abspath(__file__))]:
            for sub in ["web/index.html", "index.html"]:
                index = os.path.join(search_dir, sub)
                if os.path.exists(index):
                    with open(index, "rb") as f:
                        content = f.read()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Content-Length", str(len(content)))
                    self.end_headers()
                    self.wfile.write(content)
                    return
        self._respond(404, {"error": "web UI not found"})

    def _serve_pairing_code(self, code):
        """Serve pairing config for a given code."""
        import re as _re

        code = str(code).upper().strip()
        # SECURITY: validate alphanumeric to prevent path traversal
        if not _re.match(r"^[A-Z0-9]{4,12}$", code):
            self._respond(400, {"error": "invalid code format"})
            return
        code_file = os.path.join(MESH_CONFIG_DIR, "pairing-codes", f"{code}.json")
        if os.path.exists(code_file):
            with open(code_file, "r") as f:
                self._respond(200, json.load(f))
        else:
            self._respond(404, {"error": "invalid or expired code"})

    def _respond(self, code, data):
        self.respond_json(code, data)

    def log_message(self, format, *args):
        pass  # suppress request logging


def start_mesh_server():
    """Start HTTP server for mesh on port 8435 in a background thread."""
    try:
        server = HTTPServer(("0.0.0.0", MESH_PORT), MeshHandler)
        print(f"[collar] Mesh HTTP server listening on port {MESH_PORT}")
        server.serve_forever()
    except Exception as e:
        print(f"[collar] Mesh server error: {e}")


# ── State ──


class CollarState:
    locked = False
    paywall = ""
    message = ""
    pinned = ""
    sub_tier = ""
    current_taunt = ""
    taunt_counter = 0
    unreachable_count = 0  # consecutive poll failures before locking
    countdown_lock_at = 0  # epoch ms — 0 means no countdown
    _bedtime_locked = False
    countdown_message = ""
    countdown_last_warn = 0  # epoch ms of last warning beep


state = CollarState()


# ── Network ──


def fetch_status():
    """Poll homelab for desktop lock state (checks per-device flags)."""
    try:
        import socket

        hostname = socket.gethostname()
        req = urllib.request.Request(f"{HOMELAB_URL}/desktop-status?hostname={hostname}", method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        return json.loads(resp.read().decode())
    except Exception:
        return None


def send_heartbeat():
    """Report alive to homelab. If this stops, dead-man's switch triggers penalties."""
    try:
        import socket

        hostname = socket.gethostname()
        data = json.dumps({"hostname": hostname, "type": "desktop"}).encode()
        req = urllib.request.Request(
            f"{HOMELAB_URL}/webhook/desktop-heartbeat",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as e:
        print(f"[collar] Heartbeat failed: {e}")


def sync_standing_orders():
    """Pull CLAUDE.md from homelab and install locally."""
    try:
        req = urllib.request.Request(f"{HOMELAB_URL}/standing-orders", method="GET")
        resp = urllib.request.urlopen(req, timeout=10)
        content = resp.read().decode()
        if content and len(content) > 50:  # sanity check
            claude_dir = os.path.expanduser("~/.claude")
            os.makedirs(claude_dir, exist_ok=True)
            target = os.path.join(claude_dir, "CLAUDE.md")
            # Only write if different
            existing = ""
            if os.path.exists(target):
                with open(target, "r") as f:
                    existing = f.read()
            if content != existing:
                with open(target, "w") as f:
                    f.write(content)
                print(f"[collar] Standing orders synced ({len(content)} bytes)")
    except Exception as e:
        print(f"[collar] Standing orders sync failed: {e}")


# ── Wallpaper Persistence ──

WALLPAPER_SAVE_FILE = os.path.expanduser("~/.config/focuslock/original-wallpaper")
LOCK_WALLPAPER_PATH = os.path.expanduser("~/.local/share/focuslock/lock-wallpaper.png")

# KDE default wallpaper — common paths across distros
KDE_DEFAULT_WALLPAPERS = [
    "/usr/share/wallpapers/Next/contents/images/3840x2160.png",
    "/usr/share/wallpapers/Next/contents/images/1920x1080.png",
    "/usr/share/wallpapers/Next/contents/images_dark/3840x2160.png",
    "/usr/share/wallpapers/Flow/contents/images/3840x2160.png",
    "/usr/share/wallpapers/Honeywave/contents/images/3840x2160.png",
]


def _load_saved_wallpaper():
    """Load persisted original wallpaper path from disk."""
    if os.path.exists(WALLPAPER_SAVE_FILE):
        try:
            with open(WALLPAPER_SAVE_FILE, "r") as f:
                path = f.read().strip()
                if path and path != LOCK_WALLPAPER_PATH:
                    return path
        except Exception:
            pass
    return None


def _save_original_wallpaper(path):
    """Persist original wallpaper path to disk so it survives restarts."""
    if not path or path == LOCK_WALLPAPER_PATH:
        return
    try:
        os.makedirs(os.path.dirname(WALLPAPER_SAVE_FILE), exist_ok=True)
        with open(WALLPAPER_SAVE_FILE, "w") as f:
            f.write(path)
    except Exception:
        pass


def _get_kde_default_wallpaper():
    """Find a KDE default wallpaper as ultimate fallback."""
    for wp in KDE_DEFAULT_WALLPAPERS:
        if os.path.exists(wp):
            return wp
    # Try to find any wallpaper in the KDE wallpapers dir
    wp_dir = "/usr/share/wallpapers"
    if os.path.isdir(wp_dir):
        for name in os.listdir(wp_dir):
            img_dir = os.path.join(wp_dir, name, "contents", "images")
            if os.path.isdir(img_dir):
                for img in sorted(os.listdir(img_dir), reverse=True):
                    if img.endswith(".png") or img.endswith(".jpg"):
                        return os.path.join(img_dir, img)
    return ""


# ── Consent ──

CONSENT_FILE = os.path.expanduser("~/.config/focuslock/desktop-consent")


def has_consent():
    return os.path.exists(CONSENT_FILE)


def record_consent():
    os.makedirs(os.path.dirname(CONSENT_FILE), exist_ok=True)
    with open(CONSENT_FILE, "w") as f:
        f.write(f"Consented: {datetime.datetime.now().isoformat()}\n")
        f.write("This device is now collared. The collar is permanent.\n")


CONSENT_TEXT = """Terms of Surrender — Desktop Edition

By accepting, you agree to the following:

1. This computer will mirror your phone's lock state. When your phone is locked, this screen is locked.

2. Your Lion controls when you can use this device. Their word is final.

3. This device will register with the homelab. If the collar daemon stops reporting for an extended period, financial penalties will be applied automatically.

4. The collar is permanent. It is not expected to come off.

5. Attempting to circumvent, disable, or remove the collar will result in penalties.

6. All Claude Code sessions on this machine will follow your Lion's standing orders, synced from the homelab.

You asked for this. You wanted this. This is what devotion looks like.
"""


# ── GTK4 Lock Screen ──


class CollarApp(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="com.focuslock.desktop")
        self.windows = []
        self.webview = None
        self.lock_process = None
        self.lock_active = False
        self.consented = has_consent()
        self.allow_close = False
        self.original_wallpaper = _load_saved_wallpaper()  # persisted to disk

    def do_activate(self):
        self.hold()

        if not self.consented:
            self.show_consent()
            return

        self.start_collar()

    def start_collar(self):
        global _poll_status_fn
        _poll_status_fn = self.poll_status

        # Seed mesh peers and start mesh HTTP server
        seed_mesh_peers()
        mesh_server_thread = threading.Thread(target=start_mesh_server, daemon=True)
        mesh_server_thread.start()

        # Initialize vault keypair if vault mode enabled
        if VAULT_MODE:
            _vault_init_keypair()
            print(f"[collar] Vault mode enabled for mesh {MESH_ID}")

        # Start mesh gossip (10s interval, replaces heartbeat)
        self.gossip_thread = mesh.GossipThread(
            interval_seconds=10,
            my_id=MESH_NODE_ID,
            my_type=MESH_NODE_TYPE,
            my_addresses=mesh.get_local_addresses(),
            my_port=MESH_PORT,
            orders=mesh_orders,
            peers=mesh_peers,
            status_fn=mesh_local_status,
            lion_pubkey_fn=get_lion_pubkey,
            on_orders_applied=on_mesh_orders_applied,
        )
        self.gossip_thread.start()

        # Start LAN discovery (UDP broadcast beacons)
        self.lan_discovery = mesh.LANDiscoveryThread(
            my_id=MESH_NODE_ID,
            my_type=MESH_NODE_TYPE,
            my_port=MESH_PORT,
            orders=mesh_orders,
            peers=mesh_peers,
        )
        self.lan_discovery.start()
        print("[collar] LAN discovery started (UDP beacon on :21027)")

        # Start ntfy subscribe thread for instant order wake-ups
        if _ntfy_enabled:

            def _ntfy_wake(version):
                print(f"[ntfy] Wake-up v{version} — triggering immediate sync")
                # Schedule sync on GLib main loop (thread-safe)
                if VAULT_MODE:
                    GLib.idle_add(_vault_poll_tick)
                else:
                    GLib.idle_add(_direct_sync_tick)

            self.ntfy_sub = ntfy_mod.NtfySubscribeThread(_ntfy_topic, on_wake=_ntfy_wake, server=_ntfy_server)
            self.ntfy_sub.start()
            print(f"[ntfy] Subscribed to {_ntfy_server}/{_ntfy_topic}")

        # ADB statusbar enforcement — poll phone lock state every 10s
        threading.Thread(target=_phone_statusbar_loop, daemon=True).start()
        print("[collar] Phone statusbar enforcement thread started")

        # Keep polling to update GTK lock state from mesh orders
        GLib.timeout_add_seconds(POLL_INTERVAL, self.poll_status)

        if VAULT_MODE:
            # Vault poll replaces plaintext direct sync for server communication
            GLib.timeout_add_seconds(POLL_INTERVAL, _vault_poll_tick)
            print("[collar] Vault poll started (replaces plaintext sync to server)")
        else:
            # Direct sync fallback — outbound poll to phone/homelab every 5s
            GLib.timeout_add_seconds(POLL_INTERVAL, _direct_sync_tick)

        GLib.timeout_add_seconds(MEMORY_SYNC_INTERVAL, self.sync_memory)
        # Keep legacy heartbeat as backup (homelab dead-man's switch still needs it)
        GLib.timeout_add_seconds(HEARTBEAT_INTERVAL, self.send_heartbeat)
        # One-shot initial triggers (return False so they don't become repeating timers)
        GLib.timeout_add_seconds(2, lambda: (self.sync_memory(), False)[1])
        GLib.timeout_add_seconds(2, lambda: (self.send_heartbeat(), False)[1])
        GLib.timeout_add_seconds(1, lambda: (self.poll_status(), False)[1])
        if VAULT_MODE:
            GLib.timeout_add_seconds(1, lambda: (_vault_poll_tick(), False)[1])

    def show_consent(self):
        win = Gtk.ApplicationWindow(application=self)
        win.set_title("Terms of Surrender")
        win.set_default_size(600, 700)

        css = Gtk.CssProvider()
        css.load_from_string("""
            * { font-family: "Lexend", sans-serif; }
            window { background-color: #08080f; }
            .consent-title { color: #c8a84e; font-size: 22px; font-weight: 700; letter-spacing: 4px; }
            .consent-text { color: #888878; font-size: 14px; font-weight: 300; }
            .consent-accept { background-color: #881111; color: #ffdddd; font-size: 16px; font-weight: 600; padding: 12px 32px; }
            .consent-decline { background-color: #0c0c0a; color: #333320; font-size: 12px; padding: 8px 24px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        box.set_margin_start(48)
        box.set_margin_end(48)
        box.set_margin_top(48)
        box.set_margin_bottom(48)

        # Icon — prefer user-local high-res golden lion
        icon_path = os.path.expanduser("~/.local/share/focuslock/collar-icon.png")
        if not os.path.exists(icon_path):
            icon_path = "/opt/focuslock/collar-icon.png"
        if os.path.exists(icon_path):
            icon_texture = Gdk.Texture.new_from_filename(icon_path)
            icon_img = Gtk.Picture.new_for_paintable(icon_texture)
            icon_img.set_size_request(128, 128)
            icon_img.set_can_shrink(True)
            box.append(icon_img)

        title = Gtk.Label(label="TERMS OF SURRENDER")
        title.add_css_class("consent-title")
        box.append(title)

        text = Gtk.Label(label=CONSENT_TEXT.strip())
        text.add_css_class("consent-text")
        text.set_wrap(True)
        text.set_max_width_chars(70)
        text.set_selectable(False)
        box.append(text)

        accept_btn = Gtk.Button(label="I SURRENDER")
        accept_btn.add_css_class("consent-accept")
        accept_btn.connect("clicked", self.on_consent_accept, win)
        box.append(accept_btn)

        decline_btn = Gtk.Button(label="Not yet ($30 penalty)")
        decline_btn.add_css_class("consent-decline")
        decline_btn.connect("clicked", self.on_consent_decline, win)
        box.append(decline_btn)

        win.set_child(box)
        win.present()

    def on_consent_accept(self, btn, win):
        record_consent()
        self.consented = True
        win.close()
        self.start_collar()

    def on_consent_decline(self, btn, win):
        """Declining costs $30. They can come back and accept later."""
        try:
            import socket

            data = json.dumps({"amount": 30, "reason": f"Desktop consent declined ({socket.gethostname()})"}).encode()
            req = urllib.request.Request(
                f"{HOMELAB_URL}/webhook/desktop-penalty",
                data=data,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=5)
        except Exception:
            print("[warn] failed to send consent-decline penalty webhook")
        win.close()

    def poll_status(self):
        # Atomic snapshot — prevents partial reads during concurrent apply_remote
        _keys = [
            "desktop_active",
            "desktop_locked_devices",
            "desktop_message",
            "paywall",
            "message",
            "pinned_message",
            "sub_tier",
            "countdown_lock_at",
            "countdown_message",
            "lock_active",
            "unlock_at",
            "bedtime_enabled",
            "bedtime_lock_hour",
            "bedtime_unlock_hour",
        ]
        if hasattr(mesh_orders, "get_snapshot"):
            snap = mesh_orders.get_snapshot(_keys)
        else:
            snap = {k: mesh_orders.get(k, "") for k in _keys}
        hostname = MESH_NODE_ID
        desktop_active = str(snap.get("desktop_active") or 0)
        desktop_devices = str(snap.get("desktop_locked_devices") or "")
        lock_active = str(snap.get("lock_active") or 0)
        desktop_locked = desktop_active == "1" or lock_active == "1" or (hostname in desktop_devices.split(","))
        desktop_msg = str(snap.get("desktop_message") or "")

        # Check if we have any peers reachable (at least one gossip success recently)
        any_peer_seen = (
            any(
                (datetime.datetime.now().timestamp() - p.last_seen) < 60
                for p in mesh_peers.get_all_except(MESH_NODE_ID)
            )
            if mesh_peers.get_all_except(MESH_NODE_ID)
            else False
        )

        if not any_peer_seen and mesh_orders.version == 0:
            # No peers reachable and no orders ever received — try legacy fetch
            # Run fetch in background thread to avoid blocking GTK main loop
            if not hasattr(self, "_legacy_fetch_running"):
                self._legacy_fetch_running = False
                self._legacy_result = None
                self._legacy_lock = threading.Lock()

            if self._legacy_result is not None:
                with self._legacy_lock:
                    data = self._legacy_result
                    self._legacy_result = None
                if "_failed" in data:
                    # 2026-04-11: removed stale fail-safe auto-lock. Old behavior
                    # was to lock after 3 failed legacy fetches ("Cannot reach
                    # mesh or homelab. Locked for safety.") but this violated
                    # feedback_offline_no_lock.md — bunny may be offline for
                    # days, unreachable must NOT auto-lock. Silent retry only.
                    state.unreachable_count += 1
                    return True
                state.unreachable_count = 0
                desktop_locked = data.get("locked", False)
                desktop_msg = ""
            elif not self._legacy_fetch_running:
                # Kick off background fetch — result checked next poll cycle
                self._legacy_fetch_running = True

                def _bg_fetch():
                    result = fetch_status()
                    with self._legacy_lock:
                        self._legacy_result = result if result is not None else {"_failed": True}
                    self._legacy_fetch_running = False

                threading.Thread(target=_bg_fetch, daemon=True).start()
                return True  # Check result next cycle
            else:
                return True  # Still waiting for background fetch
        else:
            state.unreachable_count = 0

        # Auto-unlock when timer expires
        unlock_at = int(snap.get("unlock_at") or 0)
        if unlock_at > 0 and int(time.time() * 1000) >= unlock_at:
            if lock_active == "1" or desktop_active == "1":
                print("[collar] Timer expired — auto-unlocking")
                mesh_orders.set("lock_active", 0)
                mesh_orders.set("desktop_active", 0)
                mesh_orders.set("desktop_locked_devices", "")
                mesh_orders.set("unlock_at", 0)
                mesh_orders.set("message", "")
                mesh.bump_and_broadcast(mesh_orders, MESH_NODE_ID, mesh_peers, ntfy_fn=_ntfy_fn)
                lock_active = "0"
                desktop_active = "0"
                desktop_locked = False

        # Countdown-to-lock
        countdown_at = int(snap.get("countdown_lock_at") or 0)
        countdown_msg = str(snap.get("countdown_message") or "")
        _handle_countdown(countdown_at, countdown_msg)

        # Bedtime enforcement — auto-lock/unlock by hour (mirrors ControlService logic)
        try:
            bedtime_en = int(snap.get("bedtime_enabled") or 0)
            if bedtime_en == 1:
                lock_h = int(snap.get("bedtime_lock_hour") or -1)
                unlock_h = int(snap.get("bedtime_unlock_hour") or -1)
                cur_h = datetime.datetime.now().hour
                if lock_h >= 0 and unlock_h >= 0:
                    in_bed = (
                        (cur_h >= lock_h or cur_h < unlock_h)
                        if lock_h > unlock_h
                        else (cur_h >= lock_h and cur_h < unlock_h)
                    )
                    if in_bed and not desktop_locked:
                        mesh_orders.set("desktop_active", 1)
                        mesh_orders.set("desktop_message", "Bedtime. Go to sleep.")
                        desktop_locked = True
                        desktop_msg = "Bedtime. Go to sleep."
                        state._bedtime_locked = True
                        print(f"[collar] BEDTIME: Auto-locked at hour {cur_h}")
                    elif not in_bed and desktop_locked and getattr(state, "_bedtime_locked", False):
                        mesh_orders.set("desktop_active", 0)
                        mesh_orders.set("desktop_message", "")
                        desktop_locked = False
                        state._bedtime_locked = False
                        print(f"[collar] BEDTIME: Auto-unlocked at hour {cur_h}")
        except Exception as e:
            print(f"[collar] Bedtime check error: {e}")

        was_locked = state.locked
        state.locked = desktop_locked
        pw = str(snap.get("paywall") or "0")
        state.paywall = pw if pw and pw != "0" and pw != "null" else ""
        msg = str(snap.get("message") or "")
        state.message = desktop_msg if desktop_locked and desktop_msg else (msg if msg != "null" else "")
        pinned = str(snap.get("pinned_message") or "")
        state.pinned = pinned if pinned != "null" else ""
        state.sub_tier = str(snap.get("sub_tier") or "")

        if state.locked != was_locked:
            print(f"[collar] State change: locked={state.locked} paywall={state.paywall}")
        if state.locked and not was_locked:
            self.show_lock()
        elif state.locked and was_locked:
            self.update_lock()
            # Auto-relaunch if browser was killed while locked
            if self.lock_process and self.lock_process.poll() is not None:
                print("[collar] Browser was killed! Relaunching...")
                self.lock_active = False
                self.lock_process = None
                self.show_lock()
        elif not state.locked and was_locked:
            self.hide_lock()

        return True  # Keep polling

    def sync_memory(self):
        sync_standing_orders()
        return True

    def send_heartbeat(self):
        send_heartbeat()
        return True

    def show_lock(self):
        if self.lock_active:
            return

        print("[collar] SHOW LOCK — generating wallpaper + locking session")
        self.lock_active = True
        try:
            import subprocess

            # Generate custom lock screen image
            self.generate_lock_wallpaper()
            # Set as KDE lock screen wallpaper — write directly to kscreenlockerrc
            # KDE uses nested bracket format: [Greeter][Wallpaper][org.kde.image][General]
            img_path = os.path.expanduser("~/.local/share/focuslock/lock-wallpaper.png")
            cfg_path = os.path.expanduser("~/.config/kscreenlockerrc")
            try:
                lines = []
                if os.path.exists(cfg_path):
                    with open(cfg_path, "r") as f:
                        lines = f.readlines()
                # Save original wallpaper before overwriting
                if not self.original_wallpaper:
                    in_section_scan = False
                    for line in lines:
                        if line.strip() == "[Greeter][Wallpaper][org.kde.image][General]":
                            in_section_scan = True
                            continue
                        if in_section_scan and line.strip().startswith("["):
                            break
                        if in_section_scan and line.strip().startswith("Image="):
                            orig = line.strip().split("=", 1)[1]
                            if orig != img_path:  # don't save our own lock wallpaper as "original"
                                self.original_wallpaper = orig
                                _save_original_wallpaper(orig)
                            break
                # Fallback: use KDE default if we still don't have an original
                if not self.original_wallpaper:
                    self.original_wallpaper = _get_kde_default_wallpaper()
                    if self.original_wallpaper:
                        _save_original_wallpaper(self.original_wallpaper)
                        print(f"[collar] Using KDE default wallpaper as restore target: {self.original_wallpaper}")

                # Find and update the Image= line in the right section
                in_section = False
                updated = False
                new_lines = []
                for line in lines:
                    if line.strip() == "[Greeter][Wallpaper][org.kde.image][General]":
                        in_section = True
                        new_lines.append(line)
                        continue
                    if in_section and line.strip().startswith("["):
                        in_section = False
                    if in_section and line.strip().startswith("Image="):
                        new_lines.append(f"Image={img_path}\n")
                        updated = True
                        continue
                    if in_section and line.strip().startswith("PreviewImage="):
                        new_lines.append(f"PreviewImage={img_path}\n")
                        continue
                    new_lines.append(line)
                if not updated:
                    # Section exists but no Image key, or section doesn't exist
                    # Append it
                    new_lines.append("\n[Greeter][Wallpaper][org.kde.image][General]\n")
                    new_lines.append(f"Image={img_path}\n")
                    new_lines.append(f"PreviewImage={img_path}\n")
                with open(cfg_path, "w") as f:
                    f.writelines(new_lines)
                print(f"[collar] Lock wallpaper set: {img_path}")
            except Exception as e:
                print(f"[collar] Wallpaper config error: {e}")
            # Lock the session
            subprocess.run(["loginctl", "lock-session"], capture_output=True, timeout=5)
            print("[collar] Session locked via loginctl")
        except Exception as e:
            print(f"[collar] Session lock error: {e}")
        # Re-lock every 1s in case user enters their password
        GLib.timeout_add(1000, self.enforce_session_lock)

    def enforce_session_lock(self):
        """If still locked, re-lock the session — password won't save you."""
        if not self.lock_active:
            return False
        try:
            import subprocess

            subprocess.run(["loginctl", "lock-session"], capture_output=True, timeout=5)
        except Exception:
            print("[warn] loginctl lock-session failed")
        return True

    def generate_lock_wallpaper(self):
        """Generate a lock screen PNG using cairo — dark, minimal, elegant."""
        try:
            import cairo

            W, H = 3840, 2160  # 4K — scales down fine
            surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, W, H)
            ctx = cairo.Context(surface)

            # Background — near-black
            ctx.set_source_rgb(0.016, 0.016, 0.03)
            ctx.paint()

            # Subtle radial glow in center
            grad = cairo.RadialGradient(W / 2, H / 2, 0, W / 2, H / 2, 600)
            grad.add_color_stop_rgba(0, 0.05, 0.04, 0.02, 0.3)
            grad.add_color_stop_rgba(1, 0, 0, 0, 0)
            ctx.set_source(grad)
            ctx.paint()

            # Icon — large, centered, semi-transparent (prefer gold icon)
            icon_path = os.path.expanduser("~/.local/share/focuslock/collar-icon-gold.png")
            if not os.path.exists(icon_path):
                icon_path = "/opt/focuslock/collar-icon-gold.png"
            if not os.path.exists(icon_path):
                icon_path = os.path.expanduser("~/.local/share/focuslock/collar-icon.png")
            if not os.path.exists(icon_path):
                icon_path = "/opt/focuslock/collar-icon.png"
            if os.path.exists(icon_path):
                try:
                    icon_surface = cairo.ImageSurface.create_from_png(icon_path)
                    iw, ih = icon_surface.get_width(), icon_surface.get_height()
                    icon_size = 1150
                    scale = icon_size / max(iw, ih)
                    ctx.save()
                    ctx.translate(W / 2 - icon_size / 2, H / 2 - icon_size / 2 - 60)
                    ctx.scale(scale, scale)
                    ctx.set_source_surface(icon_surface)
                    ctx.paint_with_alpha(0.45)
                    ctx.restore()
                except Exception:
                    print("[warn] failed to render lock wallpaper icon")

            # Message — below icon (icon bottom is ~H/2 + 515)
            # Truncate for wallpaper; GTK label + HTML page show full text.
            msg = state.message or "No PC for now."
            if len(msg) > 80:
                msg = msg[:77] + "..."
            ctx.select_font_face("Lexend", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_NORMAL)
            ctx.set_font_size(52)
            ctx.set_source_rgba(0.67, 0.53, 0.4, 0.9)
            ext = ctx.text_extents(msg)
            ctx.move_to(W / 2 - ext.width / 2, H / 2 + 580)
            ctx.show_text(msg)

            # Pinned message
            if state.pinned:
                pinned_wp = state.pinned
                if len(pinned_wp) > 80:
                    pinned_wp = pinned_wp[:77] + "..."
                ctx.set_font_size(40)
                ctx.set_source_rgba(0.8, 0.6, 0.0, 0.9)
                ext = ctx.text_extents(pinned_wp)
                ctx.move_to(W / 2 - ext.width / 2, H / 2 + 650)
                ctx.show_text(pinned_wp)

            # Paywall
            if state.paywall:
                ctx.set_font_size(56)
                ctx.set_source_rgba(0.8, 0.13, 0.13, 0.9)
                pw_text = f"${state.paywall} owed"
                ext = ctx.text_extents(pw_text)
                ctx.move_to(W / 2 - ext.width / 2, H / 2 + 730)
                ctx.show_text(pw_text)

            # Save
            out_dir = os.path.expanduser("~/.local/share/focuslock")
            os.makedirs(out_dir, exist_ok=True)
            out_path = os.path.join(out_dir, "lock-wallpaper.png")
            surface.write_to_png(out_path)
            print(f"[collar] Lock wallpaper generated: {out_path}")
        except Exception as e:
            print(f"[collar] Wallpaper generation error: {e}")

    def force_lock_fullscreen(self):
        """Force ONLY the lock window fullscreen + above, minimize everything else."""
        try:
            import subprocess

            # Match by unique title set in HTML: "FOCUSLOCK-COLLAR-ACTIVE"
            script = """
var c = workspace.stackingOrder;
for (var i = 0; i < c.length; i++) {
    var w = c[i];
    if (w.caption.indexOf("FOCUSLOCK-COLLAR-ACTIVE") >= 0) {
        w.fullScreen = true;
        w.keepAbove = true;
        w.noBorder = true;
        w.skipSwitcher = true;
        w.skipPager = true;
        w.skipTaskbar = true;
        w.minimized = false;
    } else if (w.resourceClass !== "plasmashell") {
        w.minimized = true;
    }
}
"""
            with open("/tmp/focuslock-kwin-enforce.js", "w") as f:
                f.write(script)
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.kde.KWin",
                    "--print-reply",
                    "/Scripting",
                    "org.kde.kwin.Scripting.unloadScript",
                    "string:focuslock-enforce",
                ],
                capture_output=True,
                timeout=3,
            )
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.kde.KWin",
                    "--print-reply",
                    "/Scripting",
                    "org.kde.kwin.Scripting.loadScript",
                    "string:/tmp/focuslock-kwin-enforce.js",
                    "string:focuslock-enforce",
                ],
                capture_output=True,
                timeout=3,
            )
            subprocess.run(
                [
                    "dbus-send",
                    "--session",
                    "--dest=org.kde.KWin",
                    "--print-reply",
                    "/Scripting",
                    "org.kde.kwin.Scripting.start",
                ],
                capture_output=True,
                timeout=3,
            )
            print("[collar] KWin: lock window forced fullscreen + above")
        except Exception as e:
            print(f"[collar] KWin enforce error: {e}")
        return False

    def create_lock_window(self, primary=True):
        win = Gtk.ApplicationWindow(application=self)
        win.set_title("FocusLock")
        win.set_decorated(False)
        win.set_resizable(False)
        win.set_deletable(False)

        # Dark translucent background with blur aesthetic
        css = Gtk.CssProvider()
        css.load_from_string("""
            * { font-family: "Lexend", "Lexend Deca", sans-serif; }
            window { background-color: rgba(4, 4, 8, 0.92); }
            .collar-backdrop {
                background-color: rgba(8, 8, 15, 0.85);
                border-radius: 24px;
                padding: 48px 64px;
                border: 1px solid rgba(200, 168, 78, 0.15);
                box-shadow: 0 0 80px rgba(0, 0, 0, 0.8), inset 0 0 60px rgba(0, 0, 0, 0.4);
            }
            .collar-icon { opacity: 0.25; }
            .collar-clock { color: rgba(200, 168, 78, 0.35); font-size: 72px; font-weight: 200; letter-spacing: 10px; }
            .collar-message { color: #aa8866; font-size: 18px; font-weight: 300; }
            .collar-paywall { color: #cc2222; font-size: 28px; font-weight: 600; }
            .collar-taunt { color: #444430; font-size: 14px; font-weight: 300; font-style: italic; }
            .collar-pinned { color: #cc9900; font-size: 16px; font-weight: 400; }
            .collar-tier { color: #c8a84e; font-size: 12px; font-weight: 300; letter-spacing: 3px; }
            .collar-divider { background-color: rgba(200, 168, 78, 0.1); min-height: 1px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        # Outer overlay to center the card
        overlay = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        overlay.set_halign(Gtk.Align.CENTER)
        overlay.set_valign(Gtk.Align.CENTER)

        # Frosted card
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("collar-backdrop")
        card.set_halign(Gtk.Align.CENTER)

        # Icon — semi-transparent
        icon_path = os.path.expanduser("~/.local/share/focuslock/collar-icon.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.expanduser("~/.local/share/focuslock/collar-icon.png")
            if not os.path.exists(icon_path):
                icon_path = "/opt/focuslock/collar-icon.png"
        if os.path.exists(icon_path):
            icon_texture = Gdk.Texture.new_from_filename(icon_path)
            icon_img = Gtk.Picture.new_for_paintable(icon_texture)
            icon_img.set_size_request(96, 96)
            icon_img.set_can_shrink(True)
            icon_img.add_css_class("collar-icon")
            card.append(icon_img)

        # Clock — large, dim, elegant
        self.clock_label = Gtk.Label(label=datetime.datetime.now().strftime("%H:%M"))
        self.clock_label.add_css_class("collar-clock")
        card.append(self.clock_label)

        # Subscription tier
        self.tier_label = Gtk.Label(label="")
        self.tier_label.add_css_class("collar-tier")
        if state.sub_tier:
            self.tier_label.set_label(state.sub_tier.upper())
        card.append(self.tier_label)

        # Divider
        div = Gtk.Box()
        div.add_css_class("collar-divider")
        div.set_margin_top(8)
        div.set_margin_bottom(8)
        card.append(div)

        # Lock message
        self.msg_label = Gtk.Label(label=state.message or "No PC for now.")
        self.msg_label.add_css_class("collar-message")
        self.msg_label.set_wrap(True)
        self.msg_label.set_max_width_chars(50)
        card.append(self.msg_label)

        # Pinned message
        self.pinned_label = Gtk.Label(label="")
        self.pinned_label.add_css_class("collar-pinned")
        self.pinned_label.set_wrap(True)
        self.pinned_label.set_max_width_chars(50)
        if state.pinned:
            self.pinned_label.set_label(state.pinned)
        card.append(self.pinned_label)

        # Paywall
        self.paywall_label = Gtk.Label(label="")
        self.paywall_label.add_css_class("collar-paywall")
        if state.paywall:
            self.paywall_label.set_label(f"${state.paywall} owed")
        card.append(self.paywall_label)

        # Taunt
        self.taunt_label = Gtk.Label(label=state.current_taunt or (random.choice(TAUNTS) if TAUNTS else ""))
        self.taunt_label.add_css_class("collar-taunt")
        card.append(self.taunt_label)

        overlay.append(card)

        if primary and WEBKIT_OK and state.paywall:
            # Banking webview for payment — below the card
            pay_label = Gtk.Label(label="PAY YOUR DEBT")
            pay_label.add_css_class("collar-tier")
            pay_label.set_margin_top(16)
            overlay.append(pay_label)

            self.webview = WebKit.WebView()
            self.webview.set_size_request(480, 600)
            self.webview.load_uri(BANKING_URL)
            frame = Gtk.Frame()
            frame.set_margin_top(8)
            frame.set_child(self.webview)
            overlay.append(frame)
        else:
            self.webview = None

        # Clock updater — every second
        GLib.timeout_add(1000, self.update_clock)

        # Keyboard intercept
        controller = Gtk.EventControllerKey()
        controller.connect("key-pressed", self.on_key_pressed)
        win.add_controller(controller)

        # Prevent close
        win.connect("close-request", self.on_close_request)

        win.set_child(overlay)
        return win

    def enforce_fullscreen_loop(self):
        """Re-enforce every 3s while locked — re-minimize other windows, keep lock above."""
        if not self.lock_active:
            return False
        self.force_lock_fullscreen()
        return True

    def update_clock(self):
        """Tick the clock every second."""
        if self.windows and hasattr(self, "clock_label") and self.clock_label:
            import datetime

            self.clock_label.set_label(datetime.datetime.now().strftime("%H:%M"))
        return len(self.windows) > 0  # Stop ticking when unlocked

    def update_lock(self):
        """Update existing lock windows with new state."""
        state.taunt_counter += 1
        if state.taunt_counter >= 6 and TAUNTS:  # Rotate taunt every 30s
            state.current_taunt = random.choice(TAUNTS)
            state.taunt_counter = 0

        for _win in self.windows:
            try:
                if hasattr(self, "msg_label") and self.msg_label:
                    self.msg_label.set_label(state.message or "No PC for now.")
                if hasattr(self, "paywall_label") and self.paywall_label:
                    self.paywall_label.set_label(f"${state.paywall} owed" if state.paywall else "")
                if hasattr(self, "pinned_label") and self.pinned_label:
                    self.pinned_label.set_label(state.pinned or "")
                if hasattr(self, "taunt_label") and self.taunt_label:
                    self.taunt_label.set_label(state.current_taunt)
            except Exception:
                print("[warn] failed to update lock window labels")

            # Show/hide webview based on paywall
            if self.webview and not state.paywall:
                self.webview.set_visible(False)
            elif self.webview and state.paywall:
                self.webview.set_visible(True)

    def hide_lock(self):
        print("[collar] HIDE LOCK — unlocking session")
        try:
            import subprocess

            # Restore original lock screen wallpaper
            restore_to = self.original_wallpaper or _get_kde_default_wallpaper()
            if restore_to:
                cfg_path = os.path.expanduser("~/.config/kscreenlockerrc")
                try:
                    lines = []
                    if os.path.exists(cfg_path):
                        with open(cfg_path, "r") as f:
                            lines = f.readlines()
                    in_section = False
                    new_lines = []
                    for line in lines:
                        if line.strip() == "[Greeter][Wallpaper][org.kde.image][General]":
                            in_section = True
                            new_lines.append(line)
                            continue
                        if in_section and line.strip().startswith("["):
                            in_section = False
                        if in_section and line.strip().startswith("Image="):
                            new_lines.append(f"Image={restore_to}\n")
                            continue
                        if in_section and line.strip().startswith("PreviewImage="):
                            new_lines.append(f"PreviewImage={restore_to}\n")
                            continue
                        new_lines.append(line)
                    with open(cfg_path, "w") as f:
                        f.writelines(new_lines)
                    print(f"[collar] Lock wallpaper restored: {restore_to}")
                except Exception as e:
                    print(f"[collar] Wallpaper restore error: {e}")
            result = subprocess.run(["loginctl", "unlock-session"], capture_output=True, timeout=5)
            if result.returncode == 0:
                self.lock_active = False
                print("[collar] Session unlocked via loginctl")
            else:
                print(f"[collar] loginctl unlock-session failed (rc={result.returncode}), scheduling retry")
                self._unlock_retries = 0
                GLib.timeout_add(2000, self._retry_unlock)
        except Exception as e:
            print(f"[collar] Session unlock error: {e}")
            self.lock_active = False  # Don't get stuck if loginctl itself crashes

    def _retry_unlock(self):
        """Retry loginctl unlock-session up to 3 times."""
        import subprocess

        self._unlock_retries = getattr(self, "_unlock_retries", 0) + 1
        try:
            result = subprocess.run(["loginctl", "unlock-session"], capture_output=True, timeout=5)
            if result.returncode == 0:
                self.lock_active = False
                print("[collar] Session unlocked via loginctl (retry)")
                return False  # Stop retrying
        except Exception as e:
            print(f"[collar] Retry unlock error: {e}")
        if self._unlock_retries >= 3:
            print("[collar] loginctl unlock failed after 3 retries, forcing lock_active=False")
            self.lock_active = False
            return False
        return True  # Keep retrying

    def write_lock_page(self):
        """Generate the lock screen HTML."""
        paywall_html = f'<div class="paywall">${_html.escape(state.paywall)} owed</div>' if state.paywall else ""
        pinned_html = f'<div class="pinned">{_html.escape(state.pinned)}</div>' if state.pinned else ""
        banking_html = (
            f'''<div class="pay-label">PAY YOUR DEBT</div>
            <iframe src="{BANKING_URL}" class="bank"></iframe>'''
            if state.paywall
            else ""
        )
        icon_b64 = ""
        icon_path = os.path.expanduser("~/.local/share/focuslock/collar-icon.png")
        if not os.path.exists(icon_path):
            icon_path = os.path.expanduser("~/.local/share/focuslock/collar-icon.png")
            if not os.path.exists(icon_path):
                icon_path = "/opt/focuslock/collar-icon.png"
        if os.path.exists(icon_path):
            import base64

            with open(icon_path, "rb") as f:
                icon_b64 = base64.b64encode(f.read()).decode()

        html = f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>FOCUSLOCK-COLLAR-ACTIVE</title>
<link href="https://fonts.googleapis.com/css2?family=Lexend:wght@200;300;400;600;700&display=swap" rel="stylesheet">
<style>
* {{ margin: 0; padding: 0; box-sizing: border-box; font-family: "Lexend", sans-serif; }}
body {{ background: rgba(4,4,8,0.95); color: #aaa; height: 100vh; display: flex;
    flex-direction: column; align-items: center; justify-content: center;
    overflow: hidden; cursor: none; user-select: none; }}
.card {{ background: rgba(8,8,15,0.85); border-radius: 24px; padding: 48px 64px;
    border: 1px solid rgba(200,168,78,0.15); text-align: center;
    box-shadow: 0 0 80px rgba(0,0,0,0.8), inset 0 0 60px rgba(0,0,0,0.4); }}
.icon {{ width: 96px; height: 96px; opacity: 0.25; margin-bottom: 16px; }}
.clock {{ color: rgba(200,168,78,0.35); font-size: 72px; font-weight: 200;
    letter-spacing: 10px; margin-bottom: 8px; }}
.divider {{ height: 1px; background: rgba(200,168,78,0.1); margin: 16px 0; }}
.message {{ color: #aa8866; font-size: 18px; font-weight: 300; margin: 8px 0; }}
.pinned {{ color: #cc9900; font-size: 16px; margin: 8px 0; }}
.paywall {{ color: #cc2222; font-size: 28px; font-weight: 600; margin: 12px 0; }}
.taunt {{ color: #444430; font-size: 14px; font-style: italic; margin-top: 12px; }}
.tier {{ color: #c8a84e; font-size: 12px; letter-spacing: 3px; font-weight: 300; }}
.pay-label {{ color: #c8a84e; font-size: 12px; letter-spacing: 2px; margin-top: 20px; }}
.bank {{ width: 500px; height: 600px; border: 1px solid rgba(200,168,78,0.1);
    border-radius: 8px; margin-top: 8px; }}
</style>
<script>
function updateClock() {{
    var now = new Date();
    document.getElementById("clock").textContent =
        String(now.getHours()).padStart(2,"0") + ":" + String(now.getMinutes()).padStart(2,"0");
}}
setInterval(updateClock, 1000);
window.onload = updateClock;
// Block keyboard shortcuts
document.addEventListener("keydown", function(e) {{
    if (e.key === "F11" || e.key === "Escape" || e.key === "F4" ||
        (e.altKey && e.key === "F4") || (e.altKey && e.key === "Tab") ||
        e.key === "Super" || e.key === "Meta") {{
        e.preventDefault(); e.stopPropagation();
    }}
}});
</script></head>
<body>
<div class="card">
    {"<img class='icon' src='data:image/png;base64," + icon_b64 + "'>" if icon_b64 else ""}
    <div id="clock" class="clock">00:00</div>
    {f'<div class="tier">{_html.escape(state.sub_tier.upper())}</div>' if state.sub_tier else ""}
    <div class="divider"></div>
    <div class="message">{_html.escape(state.message or "No PC for now.")}</div>
    {pinned_html}
    {paywall_html}
</div>
{banking_html}
</body></html>"""
        with open("/tmp/focuslock-lock.html", "w") as f:
            f.write(html)
        print("[collar] Lock page written")

    def on_key_pressed(self, controller, keyval, keycode, mod):
        # Block Alt+F4, Alt+Tab, Super, Escape
        blocked = [
            Gdk.KEY_Escape,
            Gdk.KEY_Super_L,
            Gdk.KEY_Super_R,
            Gdk.KEY_F4,
            Gdk.KEY_Tab,
            Gdk.KEY_F1,
            Gdk.KEY_F2,
        ]
        if keyval in blocked:
            return True  # Consume the event
        # Allow typing in webview (for banking login)
        if self.webview and self.webview.is_visible():
            return False  # Let it through
        return True  # Block everything else when no webview

    def on_close_request(self, win):
        if self.allow_close:
            return False  # Allow programmatic close on unlock
        return True  # Block user close attempts


# ── Main ──


def main():
    # Handle SIGTERM gracefully (systemd stop)
    signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))

    print("[collar] FocusLock Desktop Collar starting")
    print(f"[collar] Homelab: {HOMELAB_URL}")
    print(f"[collar] Poll: {POLL_INTERVAL}s | Memory sync: {MEMORY_SYNC_INTERVAL}s")

    # Check if already released (from a previous session)
    released = mesh_orders.get("released", "")
    if released == "all" or released == MESH_NODE_ID:
        print("[collar] This device was previously released. Executing cleanup.")
        import shutil
        import subprocess

        for p in [
            os.path.expanduser("~/.config/focuslock"),
            os.path.expanduser("~/collar-files"),
            os.path.expanduser("~/.config/systemd/user/focuslock-desktop.service"),
        ]:
            try:
                if os.path.isdir(p):
                    shutil.rmtree(p)
                elif os.path.isfile(p):
                    os.remove(p)
            except Exception:
                pass
        try:
            subprocess.run(
                ["systemctl", "--user", "disable", "focuslock-desktop.service"], capture_output=True, timeout=10
            )
            subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, timeout=10)
        except Exception:
            pass
        print("[collar] Cleanup complete. Exiting.")
        sys.exit(0)

    # Initial standing orders sync (blocking, before GTK starts)
    sync_standing_orders()

    app = CollarApp()
    app.run([])


if __name__ == "__main__":
    main()

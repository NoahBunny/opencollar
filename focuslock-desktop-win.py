#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Desktop Collar — Windows Edition.
Mesh node + system tray crown + session lock enforcement.
Node ID: {hostname}-win (distinct from Linux collar on same machine).

Dependencies: pystray, Pillow, cryptography (optional for RSA verify)
Build: pyinstaller --onefile --noconsole --icon=crown-gold.ico focuslock-desktop-win.py
"""

import ctypes
import datetime
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import winreg

logger = logging.getLogger(__name__)

# ── Paths ──

APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "focuslock")
CONFIG_DIR = APPDATA
ORDERS_FILE = os.path.join(CONFIG_DIR, "orders.json")
PEERS_FILE = os.path.join(CONFIG_DIR, "peers.json")
LION_PUBKEY_FILE = os.path.join(CONFIG_DIR, "lion_pubkey.pem")
ICONS_DIR = os.path.join(CONFIG_DIR, "icons")
LOCK_WALLPAPER = os.path.join(CONFIG_DIR, "lock-wallpaper.png")
ORIGINAL_WALLPAPER_FILE = os.path.join(CONFIG_DIR, "original-wallpaper")
CONSENT_FILE = os.path.join(CONFIG_DIR, "desktop-consent")
FIRST_RUN_FILE = os.path.join(CONFIG_DIR, ".initialized")

os.makedirs(CONFIG_DIR, exist_ok=True)
os.makedirs(ICONS_DIR, exist_ok=True)

# ── Mesh Module ──
# Import from bundled or local focuslock_mesh.py and config loader
for _p in [
    os.path.dirname(os.path.abspath(__file__)),
    CONFIG_DIR,
    "C:\\focuslock",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared"),
]:
    mesh_path = os.path.join(_p, "focuslock_mesh.py")
    if os.path.isfile(mesh_path):
        sys.path.insert(0, _p)
        break
try:
    import focuslock_mesh as mesh
except ImportError:
    logger.error("focuslock_mesh.py not found. Place it next to this script or in %%APPDATA%%\\focuslock\\")
    sys.exit(1)

from focuslock_http import JSONResponseMixin
from focuslock_sync import direct_sync_poll as _shared_direct_sync_poll
from focuslock_sync import relay_to_phones as _shared_relay_to_phones
from focuslock_sync import try_sync as _shared_try_sync

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

try:
    from focuslock_config import load_config
except ImportError:
    # Fallback inline loader if shared module not bundled
    def load_config(config_path=None):
        path = config_path or os.path.join(CONFIG_DIR, "config.json")
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
        return {}

# ── Config ──

_cfg = load_config()

MESH_PORT = _cfg.get("mesh_port", 8435)
MESH_ID = _cfg.get("mesh_id", "")
MESH_NODE_ID = socket.gethostname().lower() + "-win"
MESH_NODE_TYPE = "desktop"
POLL_INTERVAL = _cfg.get("poll_interval", 5)
GOSSIP_INTERVAL = _cfg.get("gossip_interval", 10)

MESH_URL = _cfg.get("mesh_url", "")
HOMELAB_URL = _cfg.get("homelab_url", "")
PHONE_ADDRESSES = _cfg.get("phone_addresses", [])
PHONE_PORT = _cfg.get("phone_port", 8432)
ADMIN_TOKEN = _cfg.get("admin_token", "") or os.environ.get("FOCUSLOCK_ADMIN_TOKEN", "")

# Load Tailscale node name overrides (mesh node ID -> Tailscale hostname)
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


TRUST_STORE_FILE = os.path.join(CONFIG_DIR, "trusted_peers.json")
_trust_store = mesh.TrustStore(persist_path=TRUST_STORE_FILE)
mesh_orders = mesh.OrdersDocument(persist_path=ORDERS_FILE)
mesh_peers = mesh.PeerRegistry(persist_path=PEERS_FILE, trust_store=_trust_store)

# ── Lion's Share Pubkey ──

_lion_pubkey = ""


def get_lion_pubkey():
    global _lion_pubkey
    if not _lion_pubkey and os.path.exists(LION_PUBKEY_FILE):
        try:
            with open(LION_PUBKEY_FILE, "r") as f:
                _lion_pubkey = f.read().strip()
            if _lion_pubkey:
                logger.info("Loaded Lion's Share pubkey")
        except Exception:
            logger.warning("Failed to load Lion pubkey from %s", LION_PUBKEY_FILE)
    return _lion_pubkey


# ── Vault Mode (Phase D desktop support) ──

VAULT_MODE = _cfg.get("vault_mode", False) and VAULT_CRYPTO_OK and bool(MESH_ID)

# P7: Transport abstraction — pluggable vault blob read/write backend
try:
    from focuslock_transport import transport_factory

    _vault_transport = transport_factory(_cfg)
except ImportError:
    _vault_transport = None

_VAULT_VERSION_FILE = os.path.join(CONFIG_DIR, "vault_last_version")


def _load_vault_last_version():
    try:
        with open(_VAULT_VERSION_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return 0


def _save_vault_last_version(v):
    try:
        with open(_VAULT_VERSION_FILE, "w") as f:
            f.write(str(v))
    except Exception:
        pass


_vault_last_version = _load_vault_last_version()
_vault_node_registered = False

VAULT_PRIVKEY_FILE = os.path.join(CONFIG_DIR, "node_privkey.pem")
VAULT_PUBKEY_FILE = os.path.join(CONFIG_DIR, "node_pubkey.pem")
_vault_privkey_pem = ""
_vault_pubkey_der = b""


def _vault_init_keypair():
    """Load or generate RSA keypair for vault mode."""
    global _vault_privkey_pem, _vault_pubkey_der
    if os.path.exists(VAULT_PRIVKEY_FILE) and os.path.exists(VAULT_PUBKEY_FILE):
        with open(VAULT_PRIVKEY_FILE) as f:
            _vault_privkey_pem = f.read()
        from cryptography.hazmat.primitives import serialization

        pk = serialization.load_pem_private_key(_vault_privkey_pem.encode(), password=None)
        _vault_pubkey_der = pk.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        logger.info("Loaded vault keypair (slot=%s)", vault_slot_id(_vault_pubkey_der))
    else:
        priv, pub, der = vault_keygen()
        with open(VAULT_PRIVKEY_FILE, "w") as f:
            f.write(priv)
        with open(VAULT_PUBKEY_FILE, "w") as f:
            f.write(pub)
        _vault_privkey_pem = priv
        _vault_pubkey_der = der
        logger.info("Generated new vault keypair (slot=%s)", vault_slot_id(der))


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
        logger.info("Fetched %d approved node pubkeys", len(_approved_node_pubkeys))
    except Exception as e:
        logger.warning("Fetch vault nodes error: %s", e)


_lazy_refresh_done_this_poll = False


def _vault_verify_any_signer(blob, lion_pub):
    """P6.5: Verify blob against Lion pubkey OR any approved node pubkey."""
    global _lazy_refresh_done_this_poll
    if lion_pub and vault_verify(blob, lion_pub):
        return True
    for pk in _approved_node_pubkeys:
        if pk and vault_verify(blob, pk):
            return True
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
            logger.info("Vault node registered: %s", result)
    except Exception as e:
        logger.warning("Vault register error (will retry): %s", e)


def _vault_poll():
    """Fetch and decrypt vault blobs since last known version."""
    global _vault_last_version, _lazy_refresh_done_this_poll
    if not _vault_privkey_pem:
        return
    if not _vault_transport and not MESH_URL:
        return
    _lazy_refresh_done_this_poll = False
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
        # During catchup (large batch), skip action blobs — they are deltas
        # whose effects are already folded into subsequent runtime snapshots.
        # Replaying them double-counts (e.g. add-paywall accumulates again on
        # top of a snapshot that already reflects the addition).  In real-time
        # (small batch, ≤3 blobs), apply everything so the UI updates promptly
        # before the next phone snapshot arrives.
        catchup = len(blobs) > 3
        for blob in blobs:
            v = blob.get("version", 0)
            if v <= _vault_last_version:
                continue
            # P6.5: verify against Lion pubkey OR any approved node
            if not _vault_verify_any_signer(blob, lion_pub):
                logger.warning("Vault signature verification FAILED for v%s — skipping", v)
                continue
            plaintext = vault_decrypt(blob, _vault_privkey_pem, _vault_pubkey_der)
            if plaintext is None:
                continue
            body = json.loads(plaintext)
            if "action" in body:
                if catchup:
                    # Skip action deltas during catchup — snapshots are authoritative
                    _vault_last_version = v
                    continue
                mesh_apply_order(body["action"], body.get("params", {}), mesh_orders)
                mesh_orders.bump_version()
            else:
                for k, val in body.items():
                    if k in mesh.ORDER_KEYS:
                        mesh_orders.set(k, val)
                if body:
                    mesh_orders.bump_version()
            _vault_last_version = v
            logger.info("Vault applied v%s (%d fields)", v, len(body))
        _save_vault_last_version(_vault_last_version)
        on_mesh_orders_applied(dict(mesh_orders.orders))
    except urllib.error.HTTPError as e:
        if e.code != 404:
            logger.warning("Vault poll error: HTTP %s", e.code)
    except Exception as e:
        logger.warning("Vault poll error: %s", e)


# ── State ──


class CollarState:
    locked = False
    paywall = ""
    message = ""
    pinned = ""
    sub_tier = ""
    connected = False
    nodes_online = 0
    last_sync = 0
    missed_syncs = 0
    countdown_lock_at = 0  # epoch ms — 0 means no countdown
    countdown_message = ""
    countdown_last_warn = 0  # epoch ms of last warning beep
    _bedtime_locked = False


state = CollarState()


# ── Plaintext signed state mirror (vault-mode only) ──
# See focuslock-desktop.py for the design rationale; this is the Windows
# port of the same flow, hooked into the existing background-loop pattern
# instead of GLib timers.
_state_mirror_last_hash = ""


def _state_mirror_fields():
    """Whitelist + extraction. Keep in sync with STATE_MIRROR_FIELDS in
    focuslock-mail.py and the Java side in ControlService.stateMirrorPush."""
    return {
        "paywall": str(mesh_orders.get("paywall", "") or ""),
        "paywall_original": str(mesh_orders.get("paywall_original", "") or ""),
        "sub_tier": str(mesh_orders.get("sub_tier", "") or ""),
        "sub_due": int(mesh_orders.get("sub_due", 0) or 0),
        "lock_active": int(mesh_orders.get("lock_active", 0) or 0),
        "locked_at": int(mesh_orders.get("locked_at", 0) or 0),
        "unlock_at": int(mesh_orders.get("unlock_at", 0) or 0),
        "free_unlocks": int(mesh_orders.get("free_unlocks", 0) or 0),
    }


def _state_mirror_push():
    """Sign + POST current state to /api/mesh/{id}/state-mirror. Idempotent
    on hash; skipped when MESH_URL/MESH_ID/vault keypair aren't set."""
    global _state_mirror_last_hash
    if not MESH_URL or not MESH_ID or not _vault_privkey_pem:
        return
    try:
        import base64 as _b64
        import hashlib as _hashlib
        import urllib.error as _urlerr
        import urllib.request as _urlreq

        from cryptography.hazmat.primitives import hashes as _hh
        from cryptography.hazmat.primitives import serialization as _ser
        from cryptography.hazmat.primitives.asymmetric import padding as _pad

        state_dict = _state_mirror_fields()
        state_canonical = json.dumps(state_dict, sort_keys=True, separators=(",", ":")).encode("utf-8")
        state_hash_hex = _hashlib.sha256(state_canonical).hexdigest()
        if state_hash_hex == _state_mirror_last_hash:
            return
        ts_ms = int(time.time() * 1000)
        payload = f"{MESH_ID}|{MESH_NODE_ID}|state-mirror|{ts_ms}|{state_hash_hex}"
        priv = _ser.load_pem_private_key(_vault_privkey_pem.encode(), password=None)
        sig_bytes = priv.sign(payload.encode("utf-8"), _pad.PKCS1v15(), _hh.SHA256())
        signature = _b64.b64encode(sig_bytes).decode("ascii")
        body = json.dumps(
            {
                "node_id": MESH_NODE_ID,
                "ts": ts_ms,
                "state": state_dict,
                "signature": signature,
            }
        ).encode("utf-8")
        req = _urlreq.Request(
            f"{MESH_URL}/api/mesh/{MESH_ID}/state-mirror",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _urlreq.urlopen(req, timeout=10) as resp:
                if resp.status == 200:
                    _state_mirror_last_hash = state_hash_hex
                else:
                    logger.warning("state-mirror POST returned %s", resp.status)
        except _urlerr.HTTPError as e:
            if e.code == 403:
                logger.debug("state-mirror: node not yet registered (HTTP 403)")
            else:
                logger.warning("state-mirror HTTP %s: %s", e.code, e.reason)
    except Exception as e:
        logger.warning("state-mirror push failed: %s", e)


def _seed_configured_peers():
    """Seed mesh peers from config (homelab, phone addresses)."""
    if HOMELAB_URL:
        try:
            from urllib.parse import urlparse

            parsed = urlparse(HOMELAB_URL)
            if parsed.hostname:
                _trust_store.trust(parsed.hostname, "config")
                mesh_peers.update_peer(
                    "homelab",
                    node_type="server",
                    addresses=[parsed.hostname],
                    port=parsed.port or _cfg.get("homelab_port", 8434),
                )
        except Exception as e:
            logger.debug("Homelab URL parse failed: %s", e)
    for addr in PHONE_ADDRESSES:
        _trust_store.trust("phone", "config")
        mesh_peers.update_peer("phone", node_type="phone", addresses=[addr], port=PHONE_PORT)


# ── First Run: Unpair & Fresh Start ──


def first_run_check():
    """On first run, wipe any stale config and start fresh."""
    if os.path.exists(FIRST_RUN_FILE):
        return  # Already initialized

    logger.info("First run detected — wiping stale config for fresh start")
    for f in [ORDERS_FILE, PEERS_FILE, LION_PUBKEY_FILE, CONSENT_FILE, LOCK_WALLPAPER, ORIGINAL_WALLPAPER_FILE]:
        if os.path.exists(f):
            os.remove(f)
            logger.info("Removed: %s", os.path.basename(f))

    # Re-initialize mesh objects with clean state
    global mesh_orders, mesh_peers
    mesh_orders = mesh.OrdersDocument(persist_path=ORDERS_FILE)
    mesh_peers = mesh.PeerRegistry(persist_path=PEERS_FILE, trust_store=_trust_store)

    # Seed configured peers
    _seed_configured_peers()

    # Try to reach any configured endpoint
    reached = False
    for url in (
        ([MESH_URL] if MESH_URL else [])
        + ([HOMELAB_URL] if HOMELAB_URL else [])
        + [f"http://{a}:{PHONE_PORT}" for a in PHONE_ADDRESSES]
    ):
        try:
            req = urllib.request.Request(f"{url}/mesh/ping")
            urllib.request.urlopen(req, timeout=3)
            logger.info("Mesh reachable via %s, will get pubkey via gossip", url)
            reached = True
            break
        except Exception:
            logger.warning("Mesh unreachable at %s", url)
    if not reached:
        logger.info("Homelab unreachable — pubkey will be fetched on first gossip")

    # Mark initialized
    with open(FIRST_RUN_FILE, "w") as f:
        f.write(str(int(time.time())))
    logger.info("Fresh start complete")


# ── First-Run Config ──

CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")


def needs_first_run_config():
    """Check if we need to collect config from the user."""
    if os.path.exists(CONFIG_FILE):
        return False
    # Also skip if PIN already set via env var
    if os.environ.get("FOCUSLOCK_PIN") or os.environ.get("PHONE_PIN"):
        return False
    return not _cfg.get("pin")


def show_first_run_config():
    """Show first-run config dialog to collect PIN and optional endpoints."""
    try:
        import tkinter as tk
        from tkinter import messagebox, simpledialog
    except ImportError:
        # No tkinter — fall back to simple input box
        # Can't do text input with just MessageBox — save empty config and let user edit
        logger.warning("No tkinter available. Please edit config.json manually.")
        return False

    root = tk.Tk()
    root.withdraw()

    messagebox.showinfo(
        "FocusLock Setup",
        "First-time setup.\n\n"
        "You need a mesh PIN (shared secret between all your devices).\n"
        "Optionally configure your homelab URL or phone IP.",
    )

    pin = simpledialog.askstring("Mesh PIN", "Enter mesh PIN (required):", parent=root)
    if not pin:
        messagebox.showerror("Setup", "PIN is required. Exiting.")
        root.destroy()
        return False

    homelab = (
        simpledialog.askstring(
            "Homelab URL",
            "Homelab URL (optional — leave empty for P2P only):\ne.g. http://192.168.1.100:8434",
            parent=root,
        )
        or ""
    )

    phone_ip = (
        simpledialog.askstring("Phone IP", "Phone LAN IP (optional if homelab set):\ne.g. 192.168.1.50", parent=root)
        or ""
    )

    config = {
        "pin": pin,
        "homelab_url": homelab,
        "phone_addresses": [phone_ip] if phone_ip else [],
    }

    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)
    logger.info("Config saved to %s", CONFIG_FILE)

    # Reload config
    global _cfg, MESH_URL, HOMELAB_URL, PHONE_ADDRESSES
    _cfg = load_config()
    MESH_URL = _cfg.get("mesh_url", "")
    HOMELAB_URL = _cfg.get("homelab_url", "")
    PHONE_ADDRESSES = _cfg.get("phone_addresses", [])

    root.destroy()
    return True


# ── Consent ──


def has_consent():
    return os.path.exists(CONSENT_FILE)


def show_consent():
    """Show consent dialog via Windows MessageBox."""
    import ctypes

    MB_YESNO = 0x04
    MB_ICONWARNING = 0x30
    IDYES = 6

    text = (
        "TERMS OF SURRENDER\n\n"
        "By accepting, you surrender control of this desktop\n"
        "to your designated partner \u2014 the Lion.\n\n"
        "This software will:\n"
        "- Lock your Windows session on command\n"
        "- Display a custom lock screen\n"
        "- Report status to the enforcement mesh\n"
        "- Show a crown icon in your system tray\n\n"
        "This is consensual. You can be released at any time\n"
        "by the Lion via Lion's Share.\n\n"
        "Do you accept these terms?"
    )
    result = ctypes.windll.user32.MessageBoxW(0, text, "The Collar \u2014 Desktop", MB_YESNO | MB_ICONWARNING)
    if result == IDYES:
        with open(CONSENT_FILE, "w") as f:
            f.write(str(int(time.time())))
        logger.info("Consent granted")
        return True
    else:
        logger.info("Consent declined")
        return False


# ── Mesh Local Status ──


def mesh_local_status():
    return {
        "type": "desktop",
        "hostname": MESH_NODE_ID,
        "locked": state.locked,
    }


# ── Mesh Order Handler ──


def mesh_apply_order(action, params, orders):
    """Apply an order locally on this Windows desktop. Relay phone-targeted actions."""
    # Relay lock/unlock to phone peers (desktop acts as transparent relay)
    if action in ("lock", "unlock", "add-paywall", "clear-paywall"):
        threading.Thread(target=_relay_to_phones, args=(action, params), daemon=True).start()

    if action == "lock":
        orders.set("lock_active", 1)
        orders.set("locked_at", int(time.time() * 1000))
        if "message" in params:
            orders.set("message", params["message"])
        if "mode" in params:
            orders.set("mode", params["mode"])
        if "paywall" in params:
            orders.set("paywall", str(params["paywall"]))
        if "timer" in params:
            orders.set("unlock_at", int(time.time() * 1000) + int(params["timer"]) * 60000)
        else:
            orders.set("unlock_at", 0)
        if "desktop" in params or "target" in params:
            target = params.get("target", params.get("desktop", "all"))
            if target == "all":
                orders.set("desktop_active", 1)
    elif action == "unlock":
        orders.set("lock_active", 0)
        orders.set("desktop_active", 0)
        orders.set("desktop_locked_devices", "")
    elif action == "lock-device":
        target = params.get("target", "all")
        if target == "all" or target == MESH_NODE_ID:
            orders.set("desktop_active", 1)
    elif action == "unlock-device":
        target = params.get("target", "all")
        if target == "all" or target == MESH_NODE_ID:
            orders.set("desktop_active", 0)
            orders.set("desktop_locked_devices", "")
    elif action == "add-paywall":
        current = int(orders.get("paywall", 0) or 0)
        amount = int(params.get("amount", 0))
        orders.set("paywall", str(current + amount))
    elif action == "clear-paywall":
        orders.set("paywall", "0")
    elif action == "release-device":
        target = params.get("target", "")
        if target == "all" or target == MESH_NODE_ID:
            execute_liberation()


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


def on_mesh_orders_applied(orders_dict):
    """Called when gossip applies new orders.
    Trigger an immediate poll so the UI refreshes within ~100ms instead of
    waiting up to POLL_INTERVAL (5s)."""
    logger.info(
        "Mesh orders applied: desktop_active=%s lock_active=%s",
        orders_dict.get("desktop_active"),
        orders_dict.get("lock_active"),
    )
    try:
        poll_status()
    except Exception:
        pass  # best effort — next tick will catch up


# ── Direct Sync Fallback ──


def _try_sync(url, name, my_addrs, lion_pubkey):
    """Attempt mesh sync with a single endpoint. Returns True on success."""
    ok = _shared_try_sync(
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
    if ok:
        state.last_sync = time.time()
    return ok


def direct_sync_poll():
    """Poll mesh — try configured endpoints in priority order, then discovered peers."""
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
        get_local_addresses_fn=get_local_addresses,
        pin=_cfg.get("pin", "") or str(mesh_orders.get("pin", "")),
        get_tailscale_ip_fn=mesh.get_tailscale_ip_for_node,
        mesh_id=MESH_ID,
    )


def get_local_addresses():
    """Get local IPv4 addresses (LAN + Tailscale) — delegates to mesh module."""
    addrs = mesh.get_local_addresses()
    return addrs or ["0.0.0.0"]


# ── Windows Session Lock ──


def lock_workstation():
    """Lock the Windows session."""
    ctypes.windll.user32.LockWorkStation()
    logger.info("Windows session locked")


def set_lock_wallpaper():
    """Generate and set the lock screen wallpaper."""
    try:
        generate_lock_wallpaper()
        # Set as Windows lock screen via registry
        # HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization
        # LockScreenImage = path
        try:
            key = winreg.CreateKeyEx(
                winreg.HKEY_LOCAL_MACHINE,
                r"SOFTWARE\Policies\Microsoft\Windows\Personalization",
                0,
                winreg.KEY_SET_VALUE,
            )
            winreg.SetValueEx(key, "LockScreenImage", 0, winreg.REG_SZ, LOCK_WALLPAPER)
            winreg.CloseKey(key)
            logger.info("Lock screen wallpaper set via registry")
        except PermissionError:
            # Fallback: set desktop wallpaper (visible after unlock attempt)
            ctypes.windll.user32.SystemParametersInfoW(20, 0, LOCK_WALLPAPER, 3)
            logger.info("Set as desktop wallpaper (no admin for lock screen registry)")
    except Exception as e:
        logger.warning("Wallpaper error: %s", e)


def generate_lock_wallpaper():
    """Generate lock screen PNG using Pillow."""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        logger.warning("Pillow not installed, skipping wallpaper generation")
        return

    W, H = 3840, 2160
    img = Image.new("RGB", (W, H), (4, 4, 8))
    draw = ImageDraw.Draw(img)

    # Radial glow (approximation with ellipse)
    for r in range(600, 0, -2):
        color = (int(0.05 * 255), int(0.04 * 255), int(0.02 * 255))
        draw.ellipse([W // 2 - r, H // 2 - r, W // 2 + r, H // 2 + r], fill=color)

    # Icon overlay
    icon_path = os.path.join(CONFIG_DIR, "collar-icon-gold.png")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collar-icon-gold.png")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(CONFIG_DIR, "collar-icon.png")
    if not os.path.exists(icon_path):
        icon_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collar-icon.png")
    if os.path.exists(icon_path):
        try:
            icon = Image.open(icon_path).convert("RGBA")
            icon_size = 1150
            icon = icon.resize((icon_size, icon_size), Image.LANCZOS)
            # Semi-transparent overlay
            alpha_icon = icon.copy()
            alpha_icon.putalpha(Image.eval(icon.split()[3], lambda a: int(a * 0.45)))
            paste_x = W // 2 - icon_size // 2
            paste_y = H // 2 - icon_size // 2 - 60
            img.paste(alpha_icon, (paste_x, paste_y), alpha_icon)
        except Exception:
            logger.warning("Failed to load collar icon from %s", icon_path)

    # Try to load a nice font, fall back to default
    font_msg = None
    font_pinned = None
    font_paywall = None
    for font_name in ["seguisb.ttf", "segoeui.ttf", "arial.ttf", "calibri.ttf"]:
        try:
            font_msg = ImageFont.truetype(font_name, 52)
            font_pinned = ImageFont.truetype(font_name, 40)
            font_paywall = ImageFont.truetype(font_name, 56)
            break
        except Exception:
            continue
    if not font_msg:
        font_msg = ImageFont.load_default()
        font_pinned = font_msg
        font_paywall = font_msg

    # Message — truncate for wallpaper; the overlay window shows full text.
    msg = state.message or "Locked out by your Lion."
    if len(msg) > 80:
        msg = msg[:77] + "..."
    bbox = draw.textbbox((0, 0), msg, font=font_msg)
    tw = bbox[2] - bbox[0]
    draw.text((W // 2 - tw // 2, H // 2 + 560), msg, fill=(171, 136, 102), font=font_msg)

    # Pinned message
    if state.pinned:
        pinned_wp = state.pinned
        if len(pinned_wp) > 80:
            pinned_wp = pinned_wp[:77] + "..."
        bbox = draw.textbbox((0, 0), pinned_wp, font=font_pinned)
        tw = bbox[2] - bbox[0]
        draw.text((W // 2 - tw // 2, H // 2 + 630), pinned_wp, fill=(204, 153, 0), font=font_pinned)

    # Paywall
    if state.paywall and state.paywall != "0":
        pw_text = f"${state.paywall} owed"
        bbox = draw.textbbox((0, 0), pw_text, font=font_paywall)
        tw = bbox[2] - bbox[0]
        draw.text((W // 2 - tw // 2, H // 2 + 710), pw_text, fill=(204, 19, 19), font=font_paywall)

    img.save(LOCK_WALLPAPER, "PNG")
    logger.info("Lock wallpaper generated: %s", LOCK_WALLPAPER)


# ── Lock Enforcement ──

_lock_enforcer_running = False


def start_lock_enforcement():
    """Re-lock every 2 seconds while locked."""
    global _lock_enforcer_running
    if _lock_enforcer_running:
        return
    _lock_enforcer_running = True

    def _enforce():
        global _lock_enforcer_running
        while state.locked:
            lock_workstation()
            time.sleep(2)
        _lock_enforcer_running = False

    threading.Thread(target=_enforce, daemon=True).start()


def show_lock():
    """Lock the session."""
    if state.locked:
        return
    logger.info("SHOW LOCK: %s", state.message)
    state.locked = True
    set_lock_wallpaper()
    lock_workstation()
    start_lock_enforcement()


def hide_lock():
    """Unlock — stop enforcement."""
    if not state.locked:
        return
    logger.info("HIDE LOCK — session released")
    state.locked = False
    # Restore original wallpaper
    if os.path.exists(ORIGINAL_WALLPAPER_FILE):
        try:
            with open(ORIGINAL_WALLPAPER_FILE) as f:
                orig = f.read().strip()
            if orig and os.path.exists(orig):
                ctypes.windll.user32.SystemParametersInfoW(20, 0, orig, 3)
                logger.info("Wallpaper restored: %s", orig)
        except Exception:
            logger.warning("Failed to restore original wallpaper on unlock")


# ── Liberation (Permanent Removal) ──


def execute_liberation():
    """Permanent removal — clean up everything and exit."""
    logger.warning("LIBERATION — removing collar permanently")
    state.locked = False

    # Restore wallpaper
    hide_lock()

    # Remove autostart
    try:
        startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
        for f in os.listdir(startup):
            if "focuslock" in f.lower() or "collar" in f.lower():
                os.remove(os.path.join(startup, f))
                logger.info("Removed startup entry: %s", f)
    except Exception:
        logger.warning("Failed to remove startup entries during liberation")

    # Clean config
    import shutil

    try:
        shutil.rmtree(CONFIG_DIR, ignore_errors=True)
        logger.info("Config directory removed")
    except Exception:
        logger.warning("Failed to remove config directory during liberation")

    # Show farewell
    ctypes.windll.user32.MessageBoxW(
        0,
        "All restrictions lifted.\nThe collar is gone. You are free.",
        "LIBERATED",
        0x40,  # MB_ICONINFORMATION
    )
    os._exit(0)


# ── Poll Status (Main Loop) ──


def poll_status():
    """Check mesh orders and enforce lock state."""
    # Update online peer count for tray icon
    peers = mesh_peers.get_all_except(MESH_NODE_ID)
    now = time.time()
    state.nodes_online = sum(1 for p in peers if (now - p.last_seen) < 120)
    state.connected = state.nodes_online > 0

    snap = {}
    for k in [
        "desktop_active",
        "desktop_locked_devices",
        "desktop_message",
        "paywall",
        "message",
        "pinned_message",
        "sub_tier",
        "lock_active",
        "unlock_at",
        "countdown_lock_at",
        "countdown_message",
        "bedtime_enabled",
        "bedtime_lock_hour",
        "bedtime_unlock_hour",
    ]:
        snap[k] = mesh_orders.get(k, "")

    hostname = MESH_NODE_ID
    desktop_active = str(snap.get("desktop_active") or 0)
    desktop_devices = str(snap.get("desktop_locked_devices") or "")
    lock_active = str(snap.get("lock_active") or 0)
    desktop_locked = desktop_active == "1" or lock_active == "1" or (hostname in desktop_devices.split(","))

    # Auto-unlock when timer expires
    unlock_at = int(snap.get("unlock_at") or 0)
    if unlock_at > 0 and int(time.time() * 1000) >= unlock_at:
        if lock_active == "1" or desktop_active == "1":
            logger.info("Timer expired — auto-unlocking")
            mesh_orders.set("lock_active", 0)
            mesh_orders.set("desktop_active", 0)
            mesh_orders.set("desktop_locked_devices", "")
            mesh_orders.set("unlock_at", 0)
            mesh_orders.set("message", "")
            mesh.bump_and_broadcast(mesh_orders, MESH_NODE_ID, mesh_peers, ntfy_fn=_ntfy_fn)
            # Re-read after unlock
            lock_active = "0"
            desktop_active = "0"
            desktop_locked = False

    # Countdown-to-lock
    countdown_at = int(snap.get("countdown_lock_at") or 0)
    countdown_msg = str(snap.get("countdown_message") or "")
    _handle_countdown(countdown_at, countdown_msg)

    # Bedtime enforcement — auto-lock/unlock by hour
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
                    state._bedtime_locked = True
                    logger.info("BEDTIME: Auto-locked at hour %s", cur_h)
                elif not in_bed and desktop_locked and getattr(state, "_bedtime_locked", False):
                    mesh_orders.set("desktop_active", 0)
                    mesh_orders.set("desktop_message", "")
                    desktop_locked = False
                    state._bedtime_locked = False
                    logger.info("BEDTIME: Auto-unlocked at hour %s", cur_h)
    except Exception as e:
        logger.warning("Bedtime check error: %s", e)

    was_locked = state.locked
    state.locked = desktop_locked

    pw = str(snap.get("paywall") or "0")
    state.paywall = pw if pw and pw != "0" and pw != "null" else ""
    desktop_msg = str(snap.get("desktop_message") or "")
    msg = str(snap.get("message") or "")
    state.message = desktop_msg if desktop_locked and desktop_msg else (msg if msg != "null" else "")
    pinned = str(snap.get("pinned_message") or "")
    state.pinned = pinned if pinned != "null" else ""
    state.sub_tier = str(snap.get("sub_tier") or "")

    if state.locked and not was_locked:
        show_lock()
    elif not state.locked and was_locked:
        hide_lock()
    elif state.locked and was_locked:
        # Refresh wallpaper if message/pinned/paywall changed mid-lock
        _cur = (state.message, state.pinned, state.paywall)
        if not hasattr(poll_status, "_prev_display") or poll_status._prev_display != _cur:
            set_lock_wallpaper()
    if state.locked:
        poll_status._prev_display = (state.message, state.pinned, state.paywall)


def _handle_countdown(lock_at_ms: int, message: str):
    """Handle countdown-to-lock: warnings, escalation, and lock trigger."""
    now_ms = int(time.time() * 1000)

    # No countdown or already passed and handled
    if not lock_at_ms:
        state.countdown_lock_at = 0
        state.countdown_message = ""
        return

    remaining_ms = lock_at_ms - now_ms
    remaining_min = remaining_ms / 60000

    # Countdown expired — trigger the lock
    if remaining_ms <= 0:
        if state.countdown_lock_at > 0:
            logger.info("Countdown expired — locking")
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
        warn_interval_ms = 10_000  # every 10s in last minute
    elif remaining_min <= 5:
        warn_interval_ms = 30_000  # every 30s in last 5 minutes
    else:
        warn_interval_ms = 60_000  # every 60s otherwise

    # Check if we should warn now
    since_last = now_ms - state.countdown_last_warn
    if since_last >= warn_interval_ms:
        state.countdown_last_warn = now_ms
        _show_countdown_warning(remaining_ms, message)


def _show_countdown_warning(remaining_ms: int, message: str):
    """Show a Windows toast notification with countdown info + system beep."""
    remaining_sec = remaining_ms // 1000
    if remaining_sec >= 60:
        mins = remaining_sec // 60
        time_str = f"{mins} minute{'s' if mins != 1 else ''}"
    else:
        time_str = f"{remaining_sec} seconds"

    title = f"Lock in {time_str}"
    body = message if message else "The Lion has spoken."

    # System beep — escalating urgency
    import winsound

    if remaining_sec <= 60:
        winsound.Beep(1000, 300)  # high pitch, last minute
        time.sleep(0.1)
        winsound.Beep(1000, 300)
    elif remaining_sec <= 300:
        winsound.Beep(800, 200)  # medium pitch, last 5 minutes
    else:
        winsound.Beep(600, 150)  # low pitch, normal warning

    # Windows toast notification via PowerShell.
    # SECURITY: title/body come from mesh orders (user-controlled). We escape
    # single quotes (PowerShell's literal-string escape) to prevent command
    # injection. PowerShell uses '' to escape a single quote inside a '...' string.
    def _ps_escape(s):
        return str(s).replace("'", "''")

    title_esc = _ps_escape(title)
    body_esc = _ps_escape(body)
    try:
        ps_cmd = (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, "
            "ContentType = WindowsRuntime] > $null; "
            "$xml = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent("
            "[Windows.UI.Notifications.ToastTemplateType]::ToastText02); "
            "$texts = $xml.GetElementsByTagName('text'); "
            f"$texts[0].AppendChild($xml.CreateTextNode('{title_esc}')) > $null; "
            f"$texts[1].AppendChild($xml.CreateTextNode('{body_esc}')) > $null; "
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($xml); "
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('FocusLock').Show($toast)"
        )
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps_cmd],
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    except Exception as e:
        logger.debug("Toast notification failed: %s", e)

    logger.debug("Countdown: %s remaining%s", time_str, f" — {message}" if message else "")


# ── Mesh HTTP Server ──

from http.server import BaseHTTPRequestHandler, HTTPServer


def _create_pairing_code(body):
    """Generate a pairing code with config payload for new devices."""
    import re as _re
    import secrets
    import string

    chars = string.ascii_uppercase + string.digits
    code = body.get("code") or "".join(secrets.choice(chars) for _ in range(6))
    code = str(code).upper().strip()
    # SECURITY: pairing code must be alphanumeric only (used as filename)
    if not _re.match(r"^[A-Z0-9]{4,12}$", code):
        return {"ok": False, "error": "invalid code format"}
    expires_min = body.get("expires_minutes", 60)
    my_addrs = get_local_addresses()
    config = {
        "addresses": my_addrs,
        "port": MESH_PORT,
        "mesh_pin": _cfg.get("pin", "") or str(mesh_orders.get("pin", "")),
        "pubkey_pem": get_lion_pubkey() or "",
        "homelab_url": HOMELAB_URL,
        "mesh_url": MESH_URL,
        "created_at": time.time(),
        "expires_at": time.time() + expires_min * 60,
    }
    code_dir = os.path.join(CONFIG_DIR, "pairing-codes")
    os.makedirs(code_dir, exist_ok=True)
    with open(os.path.join(code_dir, f"{code}.json"), "w") as f:
        json.dump(config, f, indent=2)
    logger.info("Pairing code created: %s", code)
    return {"ok": True, "code": code, "url": f"/api/pair/{code}", "expires_minutes": expires_min}


class MeshHandler(JSONResponseMixin, BaseHTTPRequestHandler):
    MAX_BODY_BYTES = 1_048_576  # 1 MB — matches server

    def log_message(self, format, *args):
        pass  # Suppress HTTP logs

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
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except (json.JSONDecodeError, ValueError):
            self.respond_json(400, {"error": "invalid JSON"})
            return
        path = self.path

        if path == "/mesh/sync":
            resp = mesh.handle_mesh_sync(
                body,
                MESH_NODE_ID,
                MESH_NODE_TYPE,
                get_local_addresses(),
                MESH_PORT,
                mesh_orders,
                mesh_peers,
                mesh_local_status(),
                get_lion_pubkey(),
                on_orders_applied=on_mesh_orders_applied,
            )
        elif path == "/mesh/order":
            resp = mesh.handle_mesh_order(
                body,
                mesh_orders,
                mesh_peers,
                MESH_NODE_ID,
                apply_fn=mesh_apply_order,
                lion_pubkey=get_lion_pubkey(),
                on_orders_applied=on_mesh_orders_applied,
            )
            if isinstance(resp, dict) and "unauthenticated" in resp.get("error", ""):
                self.respond_json(403, resp, cors=True)
                return
        elif path == "/api/pair/create":
            # Audit 2026-04-27 M-6: gate on admin_token. Endpoint binds
            # 0.0.0.0:8435, so any LAN caller could otherwise harvest
            # {mesh_pin, pubkey_pem, homelab_url, mesh_url}.
            import hmac as _hmac

            token = body.get("admin_token", "") or body.get("auth_token", "")
            if not ADMIN_TOKEN:
                self.respond_json(503, {"error": "admin_token not configured"}, cors=True)
                return
            if not token or not _hmac.compare_digest(token, ADMIN_TOKEN):
                self.respond_json(403, {"error": "invalid admin_token"}, cors=True)
                return
            resp = _create_pairing_code(body)
        else:
            self.respond_json(404, {"error": "not found"}, cors=True)
            return

        self.respond_json(200, resp, cors=True)

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/mesh/ping":
            resp = mesh.handle_mesh_ping(MESH_NODE_ID, mesh_orders)
        elif path == "/mesh/status":
            resp = mesh.handle_mesh_status(mesh_orders, mesh_peers, MESH_NODE_ID, mesh_local_status())
        elif path in ("/", "/index.html"):
            self._serve_web_ui()
            return
        elif path.startswith("/api/pair/") and len(path) > len("/api/pair/"):
            self._serve_pairing_code(path.split("/")[-1])
            return
        else:
            self.respond_json(404, {"error": "not found"}, cors=True)
            return

        self.respond_json(200, resp, cors=True)

    def _serve_web_ui(self):
        """Serve Lion's Share web UI from install dir."""
        for search_dir in [
            CONFIG_DIR,
            os.path.join(CONFIG_DIR, "web"),
            os.path.dirname(os.path.abspath(__file__)),
            INSTALL_DIR_SYSTEM,
        ]:
            index = os.path.join(search_dir, "index.html")
            if not os.path.exists(index):
                index = os.path.join(search_dir, "web", "index.html")
            if os.path.exists(index):
                with open(index, "rb") as f:
                    content = f.read()
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(content)))
                self.end_headers()
                self.wfile.write(content)
                return
        self.respond_json(404, {"error": "web UI not found"})

    def _serve_pairing_code(self, code):
        """Serve pairing config for a given code."""
        import re as _re

        code = str(code).upper().strip()
        # SECURITY: validate alphanumeric to prevent path traversal
        if not _re.match(r"^[A-Z0-9]{4,12}$", code):
            self.respond_json(400, {"error": "invalid code format"})
            return
        code_file = os.path.join(CONFIG_DIR, "pairing-codes", f"{code}.json")
        if os.path.exists(code_file):
            with open(code_file, "r") as f:
                self.respond_json(200, json.load(f))
        else:
            self.respond_json(404, {"error": "invalid or expired code"})


HEALTH_PORT = 8436  # Watchdog checks this to know we're alive


class HealthHandler(BaseHTTPRequestHandler):
    """Minimal health endpoint for the watchdog on port 8436."""

    def do_GET(self):
        if self.path == "/health":
            data = json.dumps(
                {
                    "alive": True,
                    "role": "collar",
                    "pid": os.getpid(),
                    "connected": state.connected,
                    "nodes_online": state.nodes_online,
                }
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass


def start_mesh_server():
    # Health endpoint for watchdog (port 8436)
    try:
        health_server = HTTPServer(("127.0.0.1", HEALTH_PORT), HealthHandler)
        threading.Thread(target=health_server.serve_forever, daemon=True).start()
        logger.info("Health endpoint on :%s", HEALTH_PORT)
    except Exception as e:
        logger.warning("Health endpoint failed (port %s in use?): %s", HEALTH_PORT, e)

    # Main mesh server (port 8435)
    server = HTTPServer(("0.0.0.0", MESH_PORT), MeshHandler)
    logger.info("Mesh HTTP server listening on port %s", MESH_PORT)
    server.serve_forever()


# ── Tray Icon (pystray) ──


def create_tray_icon():
    """Create the system tray icon with gold/gray crown."""
    try:
        import pystray
        from PIL import Image
    except ImportError:
        logger.warning("pystray or Pillow not installed — no tray icon")
        return None

    # Load or generate crown icons
    gold_path = os.path.join(ICONS_DIR, "crown-gold.png")
    gray_path = os.path.join(ICONS_DIR, "crown-gray.png")

    # Try to find icons from known locations
    for icon_name, dest in [("crown-gold.png", gold_path), ("crown-gray.png", gray_path)]:
        if not os.path.exists(dest):
            for src_dir in [
                os.path.dirname(os.path.abspath(__file__)),
                os.path.join(os.path.dirname(os.path.abspath(__file__)), "icons"),
                CONFIG_DIR,
            ]:
                src = os.path.join(src_dir, icon_name)
                if os.path.exists(src):
                    import shutil

                    shutil.copy2(src, dest)
                    break

    # Generate fallback icons if missing
    if not os.path.exists(gold_path):
        img = Image.new("RGBA", (64, 64), (200, 168, 78, 255))
        img.save(gold_path)
    if not os.path.exists(gray_path):
        img = Image.new("RGBA", (64, 64), (100, 100, 100, 255))
        img.save(gray_path)

    icon_gold = Image.open(gold_path)
    icon_gray = Image.open(gray_path)

    def get_icon():
        return icon_gold if state.connected else icon_gray

    def get_title():
        if state.connected:
            tip = f"The Collar \u2014 {state.nodes_online} peer{'s' if state.nodes_online != 1 else ''}"
            if state.sub_tier:
                tip += f" | {state.sub_tier.upper()}"
            if state.locked:
                tip += " | LOCKED"
            if state.countdown_lock_at:
                remaining = (state.countdown_lock_at - int(time.time() * 1000)) // 60000
                if remaining > 0:
                    tip += f" | Lock in {remaining}m"
            if state.paywall:
                tip += f" | ${state.paywall} owed"
            return tip
        return "The Collar \u2014 Disconnected (0 peers)"

    def on_self_lock(mins):
        def _lock(icon, item):
            try:
                data = json.dumps(
                    {
                        "action": "lock",
                        "params": {
                            "message": f"Self-locked from desktop for {mins} minutes",
                            "timer": str(mins),
                            "mode": "basic",
                            "target": "phone",
                        },
                    }
                ).encode()
                req = urllib.request.Request(
                    f"http://localhost:{MESH_PORT}/mesh/order", data=data, headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=5)
            except Exception as e:
                logger.warning("Self-lock failed: %s", e)

        return _lock

    menu = pystray.Menu(
        pystray.MenuItem("Status", lambda icon, item: None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Self-lock 15m", on_self_lock(15)),
        pystray.MenuItem("Self-lock 30m", on_self_lock(30)),
        pystray.MenuItem("Self-lock 60m", on_self_lock(60)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit tray", lambda icon, item: icon.stop()),
    )

    icon = pystray.Icon(
        "focuslock-collar",
        get_icon(),
        get_title(),
        menu,
    )

    # Background updater — only set icon when state changes to force Win32 redraw
    _prev = [None, None]  # [connected, title]

    def _update_loop():
        while True:
            try:
                new_connected = state.connected
                new_title = get_title()
                if new_connected != _prev[0]:
                    _prev[0] = new_connected
                    # Assign a fresh copy to ensure pystray detects the change
                    icon.icon = get_icon().copy()
                if new_title != _prev[1]:
                    _prev[1] = new_title
                    icon.title = new_title
            except Exception:
                logger.warning("Tray icon update failed")
            time.sleep(3)

    threading.Thread(target=_update_loop, daemon=True).start()

    return icon


# ── Self-Install ──

INSTALL_DIR_SYSTEM = r"C:\focuslock"


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def get_exe_path():
    """Get path to current executable (None if running as script)."""
    if getattr(sys, "frozen", False):
        return os.path.abspath(sys.executable)
    return None


def needs_install():
    """Check if self-installation is needed."""
    exe = get_exe_path()
    if not exe:
        return False  # Script mode
    installed_exe = os.path.join(INSTALL_DIR_SYSTEM, os.path.basename(exe))
    if os.path.normcase(os.path.abspath(exe)) == os.path.normcase(os.path.abspath(installed_exe)):
        return False  # Already running from install dir
    if not os.path.exists(installed_exe):
        return True
    # Update if different size (new build)
    try:
        return os.path.getsize(exe) != os.path.getsize(installed_exe)
    except Exception:
        return True


def self_install():
    """Install collar to C:\\focuslock with scheduled tasks, firewall, ACLs."""
    import shutil
    import subprocess

    exe = get_exe_path()
    exe_name = os.path.basename(exe)
    exe_dir = os.path.dirname(exe)
    installed_exe = os.path.join(INSTALL_DIR_SYSTEM, exe_name)

    logger.info("Installing to %s...", INSTALL_DIR_SYSTEM)
    os.makedirs(INSTALL_DIR_SYSTEM, exist_ok=True)

    # Copy collar exe
    shutil.copy2(exe, installed_exe)
    logger.info("Copied %s", exe_name)

    # Copy watchdog if next to the exe
    for wd_name in ["FocusLock-Watchdog.exe"]:
        wd_src = os.path.join(exe_dir, wd_name)
        if os.path.exists(wd_src):
            shutil.copy2(wd_src, os.path.join(INSTALL_DIR_SYSTEM, wd_name))
            logger.info("Copied %s", wd_name)

    # Copy icons to appdata
    os.makedirs(ICONS_DIR, exist_ok=True)
    for icon_name in ["crown-gold.png", "crown-gray.png", "collar-icon.png"]:
        for search_dir in [exe_dir, os.path.join(exe_dir, "icons"), os.path.join(exe_dir, "..", "icons")]:
            src = os.path.join(search_dir, icon_name)
            if os.path.exists(src):
                dest_dir = ICONS_DIR if "crown" in icon_name else CONFIG_DIR
                shutil.copy2(src, os.path.join(dest_dir, icon_name))
                break

    # Copy web UI if available
    web_dest = os.path.join(CONFIG_DIR, "web")
    os.makedirs(web_dest, exist_ok=True)
    for search_dir in [exe_dir, os.path.join(exe_dir, ".."), os.path.join(exe_dir, "..", "web")]:
        src = os.path.join(search_dir, "web", "index.html")
        if not os.path.exists(src):
            src = os.path.join(search_dir, "index.html")
        if os.path.exists(src) and os.path.getsize(src) > 1000:
            shutil.copy2(src, os.path.join(web_dest, "index.html"))
            logger.info("Web UI copied")
            break

    # Firewall rule for mesh port
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", "name=FocusLock Mesh (TCP 8435)"], capture_output=True
    )
    subprocess.run(
        [
            "netsh",
            "advfirewall",
            "firewall",
            "add",
            "rule",
            "name=FocusLock Mesh (TCP 8435)",
            "dir=in",
            "protocol=tcp",
            "localport=8435",
            "action=allow",
        ],
        capture_output=True,
    )
    logger.info("Firewall rule set")

    # Scheduled tasks via PowerShell (restart on failure, no time limit, admin)
    ps_collar = f'''
$a = New-ScheduledTaskAction -Execute '"{installed_exe}"'
$t = New-ScheduledTaskTrigger -AtLogOn
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365) -MultipleInstances IgnoreNew
$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask -TaskName "FocusLockCollar" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "FocusLockCollar" -Action $a -Trigger $t -Settings $s -Principal $p -Description "FocusLock Desktop Collar"
'''
    subprocess.run(["powershell", "-Command", ps_collar], capture_output=True)
    logger.info("Scheduled task: FocusLockCollar")

    watchdog_exe = os.path.join(INSTALL_DIR_SYSTEM, "FocusLock-Watchdog.exe")
    if os.path.exists(watchdog_exe):
        ps_watchdog = f'''
$a = New-ScheduledTaskAction -Execute '"{watchdog_exe}"'
$t = New-ScheduledTaskTrigger -AtLogOn
$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Days 365) -MultipleInstances IgnoreNew
$p = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
Unregister-ScheduledTask -TaskName "FocusLockWatchdog" -Confirm:$false -ErrorAction SilentlyContinue
Register-ScheduledTask -TaskName "FocusLockWatchdog" -Action $a -Trigger $t -Settings $s -Principal $p -Description "FocusLock Watchdog"
'''
        subprocess.run(["powershell", "-Command", ps_watchdog], capture_output=True)
        logger.info("Scheduled task: FocusLockWatchdog")

    # Registry Run key as fallback
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE
        )
        winreg.SetValueEx(key, "FocusLockCollar", 0, winreg.REG_SZ, f'"{installed_exe}"')
        winreg.CloseKey(key)
        logger.info("Registry Run key set")
    except Exception:
        logger.warning("Failed to set Registry Run key for autostart")

    # ACL lockdown — user gets Read+Execute only
    subprocess.run(
        [
            "icacls",
            INSTALL_DIR_SYSTEM,
            "/inheritance:r",
            "/grant:r",
            "SYSTEM:(OI)(CI)F",
            "/grant:r",
            "*S-1-5-32-544:(OI)(CI)F",  # BUILTIN\Administrators
            "/grant:r",
            f"{os.environ.get('USERNAME', 'User')}:(OI)(CI)RX",
        ],
        capture_output=True,
    )
    logger.info("ACL lockdown applied")

    # Standing orders sync
    try:
        claude_dir = os.path.join(os.environ.get("USERPROFILE", ""), ".claude")
        os.makedirs(claude_dir, exist_ok=True)
        for endpoint, filename in [("/standing-orders", "CLAUDE.md"), ("/settings", "settings.json")]:
            try:
                req = urllib.request.Request(f"{MESH_URL}{endpoint}")
                resp = urllib.request.urlopen(req, timeout=10)
                content = resp.read().decode()
                if len(content) > 50:
                    with open(os.path.join(claude_dir, filename), "w", encoding="utf-8") as f:
                        f.write(content)
                    logger.info("Standing orders: %s", filename)
            except Exception:
                logger.warning("Failed to fetch standing orders: %s", endpoint)
    except Exception:
        logger.warning("Failed to set up standing orders sync")

    # Remove old startup entries (from legacy installers)
    startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
    for f in os.listdir(startup):
        if "focuslock" in f.lower() or "collar" in f.lower():
            try:
                os.remove(os.path.join(startup, f))
                logger.info("Removed legacy startup: %s", f)
            except Exception:
                logger.warning("Failed to remove legacy startup entry: %s", f)

    logger.info("Installation complete — launching from install dir")

    # Launch collar + watchdog from installed location
    subprocess.Popen([installed_exe], creationflags=subprocess.DETACHED_PROCESS)
    if os.path.exists(watchdog_exe):
        subprocess.Popen([watchdog_exe], creationflags=subprocess.DETACHED_PROCESS)
    sys.exit(0)


# ── Startup Cleanup ──


def _should_be_locked() -> bool:
    """Check persisted orders to determine if desktop should be locked.
    Called before killing old processes to ensure no enforcement gap."""
    hostname = MESH_NODE_ID
    desktop_active = str(mesh_orders.get("desktop_active") or 0)
    desktop_devices = str(mesh_orders.get("desktop_locked_devices") or "")
    return desktop_active == "1" or (hostname in desktop_devices.split(","))


def _kill_old_processes():
    """Kill any stale collar/watchdog processes from previous versions.
    Skips our own PID."""
    my_pid = os.getpid()
    for exe_name in ["FocusLock-Paired.exe", "FocusLock.exe", "FocusLock-Watchdog.exe"]:
        try:
            result = subprocess.run(
                ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            for line in result.stdout.strip().split("\n"):
                if not line.strip() or "INFO:" in line:
                    continue
                parts = line.strip().strip('"').split('","')
                if len(parts) >= 2:
                    pid = int(parts[1])
                    if pid != my_pid:
                        try:
                            subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True, timeout=5)
                            logger.info("Killed old %s (PID %s)", exe_name, pid)
                        except Exception:
                            pass
        except Exception:
            pass


# ── Main ──


def _is_another_instance_running() -> bool:
    """Check if a healthy collar instance is already running on this machine."""
    try:
        req = urllib.request.Request("http://127.0.0.1:8436/health")
        resp = urllib.request.urlopen(req, timeout=2)
        data = json.loads(resp.read().decode())
        if data.get("alive") and data.get("pid") != os.getpid():
            return True
    except Exception:
        pass
    return False


def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    logger.info("FocusLock Desktop Collar (Windows) starting")
    logger.info("Node ID: %s", MESH_NODE_ID)
    logger.info("Config: %s", CONFIG_DIR)

    # Instance guard — if another collar is already running and healthy, exit
    if _is_another_instance_running():
        logger.warning("Another instance is already running — exiting")
        sys.exit(0)

    # Check persisted orders BEFORE killing old processes — if the desktop
    # was locked, lock the session immediately so there's no enforcement gap
    # during the restart. Don't set state.locked here — let poll_status()
    # detect the transition and call show_lock() with full enforcement.
    was_collared = _should_be_locked()
    if was_collared:
        logger.info("Desktop was locked — locking session immediately")
        lock_workstation()

    # Kill stale collar/watchdog processes from previous versions
    _kill_old_processes()

    # First run check
    first_run_check()

    # Consent (before elevation — runs in user session)
    if not has_consent():
        if not show_consent():
            logger.info("No consent — exiting")
            sys.exit(0)

    # First-run config (collect PIN, homelab URL, phone IP)
    if needs_first_run_config():
        if not show_first_run_config():
            logger.info("No config — exiting")
            sys.exit(0)

    # Self-install if needed (exe mode only)
    if needs_install():
        if is_admin():
            self_install()  # Installs and exits
        else:
            # UAC elevate — re-launch same exe as admin
            exe = get_exe_path()
            logger.info("Requesting admin for installation...")
            ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, "", os.path.dirname(exe), 0)
            sys.exit(0)

    # Seed mesh peers from config
    _seed_configured_peers()

    # Initialize vault keypair if vault mode enabled
    if VAULT_MODE:
        _vault_init_keypair()
        logger.info("Vault mode enabled for mesh %s", MESH_ID)

    # Save original wallpaper
    if not os.path.exists(ORIGINAL_WALLPAPER_FILE):
        try:
            buf = ctypes.create_unicode_buffer(260)
            ctypes.windll.user32.SystemParametersInfoW(0x0073, 260, buf, 0)  # SPI_GETDESKWALLPAPER
            if buf.value:
                with open(ORIGINAL_WALLPAPER_FILE, "w") as f:
                    f.write(buf.value)
                logger.info("Saved original wallpaper: %s", buf.value)
        except Exception:
            logger.warning("Failed to save original wallpaper")

    # Start mesh HTTP server
    threading.Thread(target=start_mesh_server, daemon=True).start()

    # Start gossip thread
    gossip = mesh.GossipThread(
        interval_seconds=GOSSIP_INTERVAL,
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
    gossip.start()

    # Start LAN discovery (UDP broadcast beacons)
    lan_discovery = mesh.LANDiscoveryThread(
        my_id=MESH_NODE_ID,
        my_type=MESH_NODE_TYPE,
        my_port=MESH_PORT,
        orders=mesh_orders,
        peers=mesh_peers,
    )
    lan_discovery.start()
    logger.info("LAN discovery started (UDP beacon on :21027)")

    # Start ntfy subscribe thread for instant order wake-ups
    if _ntfy_enabled:

        def _ntfy_wake(version):
            logger.debug("Wake-up v%s — triggering immediate sync", version)
            try:
                if VAULT_MODE:
                    _vault_poll()
                else:
                    direct_sync_poll()
            except Exception:
                pass

        ntfy_sub = ntfy_mod.NtfySubscribeThread(_ntfy_topic, on_wake=_ntfy_wake, server=_ntfy_server)
        ntfy_sub.start()
        logger.info("Subscribed to ntfy %s/%s", _ntfy_server, _ntfy_topic)

    if VAULT_MODE:
        # Vault poll replaces plaintext direct sync for server communication
        def _vault_poll_loop():
            while True:
                try:
                    _vault_poll()
                except Exception:
                    logger.exception("Vault poll loop error")
                time.sleep(POLL_INTERVAL)

        threading.Thread(target=_vault_poll_loop, daemon=True).start()
        logger.info("Vault poll started (replaces plaintext sync to server)")

        # Plaintext signed state mirror — keeps the server's _orders_registry
        # coherent for compound-interest + IMAP scanners on vault-mode meshes.
        # 30s cadence matches the phone Collar's stateMirrorPush.
        def _state_mirror_loop():
            while True:
                try:
                    _state_mirror_push()
                except Exception:
                    logger.exception("State-mirror loop error")
                time.sleep(30)

        threading.Thread(target=_state_mirror_loop, daemon=True).start()
        logger.info("State-mirror push started (30s interval, vault-mode only)")
    else:
        # Start direct sync fallback loop
        def _direct_sync_loop():
            while True:
                try:
                    direct_sync_poll()
                except Exception:
                    logger.exception("Direct sync loop error")
                time.sleep(POLL_INTERVAL)

        threading.Thread(target=_direct_sync_loop, daemon=True).start()

    # Start poll status loop
    def _poll_loop():
        while True:
            try:
                poll_status()
            except Exception as e:
                logger.warning("Poll error: %s", e)
            time.sleep(POLL_INTERVAL)

    threading.Thread(target=_poll_loop, daemon=True).start()

    # Create and run tray icon (blocks on main thread)
    icon = create_tray_icon()
    if icon:
        logger.info("Tray icon started — gold crown visible in system tray")
        icon.run()
    else:
        # No pystray — just run forever
        logger.info("Running without tray icon (install pystray for crown)")
        try:
            while True:
                time.sleep(60)
        except KeyboardInterrupt:
            pass


if __name__ == "__main__":
    main()

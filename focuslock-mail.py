#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Mail Service — runs on homelab
1. IMAP: Checks email for Interac e-Transfer notifications → triggers unlock
2. SMTP: Receives webhook when compliment is completed → sends evidence email
3. HTTP server on port 8433 for webhooks from FocusLock
"""

import smtplib
import json
import time
import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.base import MIMEBase
from email import encoders
from datetime import datetime
import base64
import hmac
import random
import secrets
import subprocess
import socket
import sys

# Add mesh module to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import focuslock_mesh as mesh

# ── Service version metadata (P3 audit transparency) ──
# Bumped manually on every meaningful release. The /version endpoint exposes
# this string + the sha256 of this file + (optionally) a deploy-injected git
# commit, so anyone can verify the running binary matches a public source tree.
# See docs/VAULT-DESIGN.md "Trust model" and the roadmap P3 entry.
__version__ = "phase-d.1"
SERVICE_START_TIME = time.time()

# ── Web Session Auth (QR code login for Lion's Share web UI) ──
# Ephemeral sessions: phone scans QR → approves session → web auto-logs in.
_web_sessions = {}  # session_id -> {secret, approved, created_at}
_WEB_SESSION_TTL = 300  # 5 minutes (approval flow window)

# P0 fix: scoped session tokens issued INSTEAD of ADMIN_TOKEN.
# The web UI receives one of these after Lion's signed approval. It behaves
# like ADMIN_TOKEN for authorization but has a TTL — if stolen, the damage
# window is bounded. Never hand the master ADMIN_TOKEN to any web client.
_active_session_tokens = {}  # session_token -> {issued_at, expires_at, session_id}
_SESSION_TOKEN_TTL = 8 * 3600  # 8 hours — long enough for a work session
_session_tokens_lock = threading.Lock()

def _issue_session_token(session_id):
    """Mint a new scoped session token tied to a web session."""
    with _session_tokens_lock:
        # Prune expired tokens
        now_ts = time.time()
        stale = [t for t, v in _active_session_tokens.items() if v["expires_at"] < now_ts]
        for t in stale:
            del _active_session_tokens[t]
        token = secrets.token_urlsafe(32)
        _active_session_tokens[token] = {
            "issued_at": now_ts,
            "expires_at": now_ts + _SESSION_TOKEN_TTL,
            "session_id": session_id,
        }
        return token

def _is_valid_admin_auth(token):
    """Check if a token is either the master ADMIN_TOKEN or a live session token.
    Constant-time comparison to prevent timing attacks."""
    if not token or not ADMIN_TOKEN:
        return False
    if hmac.compare_digest(token, ADMIN_TOKEN):
        return True
    with _session_tokens_lock:
        entry = _active_session_tokens.get(token)
        if not entry:
            return False
        if entry["expires_at"] < time.time():
            del _active_session_tokens[token]
            return False
        return True


def _compute_source_sha256():
    """sha256 of this module's source file (the running mail service).

    Computed once at module load. Auditors compare this against the sha256
    of the file at the published commit hash to confirm the relay is running
    unmodified open-source code.
    """
    import hashlib
    try:
        with open(os.path.abspath(__file__), "rb") as f:
            return hashlib.sha256(f.read()).hexdigest()
    except Exception:
        return None


def _read_deploy_git_commit():
    """Read deploy-time git commit from /opt/focuslock/.git_commit if present.

    The deploy script (or future CI pipeline) is expected to write the commit
    hash to this file alongside the .py at install time. Returns None if the
    file is missing or unreadable, in which case /version reports null and
    auditors fall back to comparing source_sha256 against published builds.
    """
    for candidate in ("/opt/focuslock/.git_commit",
                      os.path.join(os.path.dirname(os.path.abspath(__file__)), ".git_commit")):
        try:
            with open(candidate, "r") as f:
                commit = f.read().strip()
                if commit:
                    return commit
        except Exception:
            continue
    return None


SOURCE_SHA256 = _compute_source_sha256()
DEPLOY_GIT_COMMIT = _read_deploy_git_commit()

# ── Config ──
# Load from config.json with env var overrides
try:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared"))
    from focuslock_config import load_config
    _cfg = load_config()
except ImportError:
    _cfg = {}

IMAP_HOST = os.environ.get("MAIL_HOST", _cfg.get("mail", {}).get("imap_host", ""))
SMTP_HOST = os.environ.get("SMTP_HOST", _cfg.get("mail", {}).get("smtp_host", ""))
MAIL_USER = os.environ.get("MAIL_USER", _cfg.get("mail", {}).get("user", ""))
MAIL_PASS = os.environ.get("MAIL_PASS", _cfg.get("mail", {}).get("pass", ""))
PARTNER_EMAIL = os.environ.get("PARTNER_EMAIL", _cfg.get("mail", {}).get("partner_email", ""))
PHONE_PIN = os.environ.get("PHONE_PIN", _cfg.get("pin", ""))
IMAP_CHECK_INTERVAL = 30  # seconds
WEBHOOK_PORT = _cfg.get("homelab_port", 8434)
IP_REGISTRY_FILE = "/run/focuslock/phone-ips.json"

# Phone URL — from config or env
_phone_addrs = _cfg.get("phone_addresses", [])
_phone_port = _cfg.get("phone_port", 8432)
PHONE_URL = os.environ.get("PHONE_URL",
    f"http://{_phone_addrs[0]}:{_phone_port}" if _phone_addrs else "")

# ── Multi-device ADB targets ──
from focuslock_adb import ADBBridge
adb = ADBBridge(
    devices=[f"{addr}:5555" for addr in _phone_addrs] if _phone_addrs else [],
)

# ── Mesh State ──

MESH_ORDERS_FILE = "/run/focuslock/orders.json"
MESH_PEERS_FILE = "/run/focuslock/peers.json"
MESH_NODE_ID = socket.gethostname().lower()
MESH_NODE_TYPE = "server"
MESH_PORT = WEBHOOK_PORT  # 8434

# Load Tailscale node name overrides
_ts_map = _cfg.get("tailscale_node_map", {})
if _ts_map:
    mesh.set_tailscale_node_map(_ts_map)

mesh_orders = mesh.OrdersDocument(persist_path=MESH_ORDERS_FILE)
mesh_peers = mesh.PeerRegistry(persist_path=MESH_PEERS_FILE)

# ── Per-Mesh Orders Registry (multi-tenant isolation) ──

def _safe_mesh_id_static(mesh_id):
    """Module-level mesh_id validator (used before _safe_mesh_id method is defined).
    Allow only [A-Za-z0-9_-] and max 64 chars."""
    if not mesh_id or not isinstance(mesh_id, str) or len(mesh_id) > 64:
        return False
    return all(c.isalnum() or c in "-_" for c in mesh_id)


class MeshOrdersRegistry:
    """Maps mesh_id -> OrdersDocument. Every mesh — including the operator's
    own — gets its own OrdersDocument persisted under base_dir. Prior to
    2026-04-11 the operator's mesh was a special case that shared the
    legacy global ``mesh_orders`` pointing at /run/focuslock/orders.json;
    see ``_init_orders_registry()`` for the migration story."""

    def __init__(self, base_dir="/run/focuslock/mesh-orders"):
        self.base_dir = base_dir
        self.docs = {}  # mesh_id -> OrdersDocument
        os.makedirs(base_dir, exist_ok=True)
        self._load_all()

    def _load_all(self):
        import glob as globmod
        for f in globmod.glob(os.path.join(self.base_dir, "*.json")):
            mid = os.path.splitext(os.path.basename(f))[0]
            self.docs[mid] = mesh.OrdersDocument(persist_path=f)

    def get(self, mesh_id):
        return self.docs.get(mesh_id)

    def get_or_create(self, mesh_id):
        # SECURITY: defense-in-depth — validate mesh_id before using in path
        if not _safe_mesh_id_static(mesh_id):
            raise ValueError(f"invalid mesh_id: {mesh_id!r}")
        if mesh_id not in self.docs:
            path = os.path.join(self.base_dir, f"{mesh_id}.json")
            self.docs[mesh_id] = mesh.OrdersDocument(persist_path=path)
        return self.docs[mesh_id]

_orders_registry = MeshOrdersRegistry()
# OPERATOR_MESH_ID is defined later (after config load) —
# _init_orders_registry() is called from there to register the operator's mesh.

def _init_orders_registry():
    """Make the operator's mesh use a per-mesh file like every other mesh.

    Prior to 2026-04-11, ``mesh_orders`` pointed at the legacy
    ``/run/focuslock/orders.json`` and the operator's mesh was a special
    case throughout _resolve_orders(). This meant rotating the operator
    mesh_id required direct file surgery — writing to the per-mesh file
    had no effect because the admin API kept reading the legacy singleton.

    The fix:
      1. If OPERATOR_MESH_ID is empty (public relay with no admin API),
         leave ``mesh_orders`` alone — it's never read anyway.
      2. Otherwise, on first run after the fix, atomically rename the
         legacy file to the per-mesh path (one-shot migration). Skipped
         if the per-mesh file already exists; the legacy is then orphaned
         and a warning is logged.
      3. Rebind the module-level ``mesh_orders`` global to the per-mesh
         OrdersDocument. Safe to rebind because every call site in this
         module looks up ``mesh_orders`` by name and this runs at module
         import time, before any function has been invoked.
    """
    global mesh_orders
    if not OPERATOR_MESH_ID:
        return  # public relay — nothing to do

    target_path = os.path.join(_orders_registry.base_dir, f"{OPERATOR_MESH_ID}.json")

    # One-shot migration: legacy /run/focuslock/orders.json → per-mesh file.
    # Only migrate if the target doesn't already exist, to avoid clobbering
    # state the registry just loaded.
    if not os.path.exists(target_path) and os.path.exists(MESH_ORDERS_FILE):
        try:
            os.rename(MESH_ORDERS_FILE, target_path)
            print(f"[orders] Migrated legacy {MESH_ORDERS_FILE} → {target_path}")
        except OSError as e:
            print(f"[orders] WARN: legacy migration failed: {e}")
    elif os.path.exists(target_path) and os.path.exists(MESH_ORDERS_FILE):
        # Both exist — compare updated_at/version and keep the newer one.
        # This avoids the 2026-04-11 deploy incident where the per-mesh file
        # was a stale snapshot and clobbered the authoritative legacy state.
        try:
            import json as _j
            with open(MESH_ORDERS_FILE) as _f:
                _legacy = _j.load(_f)
            with open(target_path) as _f:
                _permesh = _j.load(_f)
            _leg_ts = _legacy.get("updated_at", 0)
            _pm_ts = _permesh.get("updated_at", 0)
            if _leg_ts > _pm_ts:
                import shutil
                shutil.copy2(MESH_ORDERS_FILE, target_path)
                print(f"[orders] WARN: legacy file is NEWER (legacy={_leg_ts} vs per-mesh={_pm_ts})"
                      f" — copied legacy → per-mesh to preserve authoritative state")
            else:
                print(f"[orders] legacy {MESH_ORDERS_FILE} is orphaned "
                      f"(per-mesh is newer: {_pm_ts} >= {_leg_ts}) — ignoring")
        except Exception as _e:
            print(f"[orders] WARN: could not compare both-exist files: {_e} "
                  f"— keeping per-mesh as-is")

    # Get (from registry's _load_all) or create the per-mesh doc, and rebind
    # the global. Every subsequent reference to ``mesh_orders`` — including
    # the ones passed to GossipThread/LANDiscoveryThread/mail-loop in main()
    # — resolves to this doc.
    mesh_orders = _orders_registry.get_or_create(OPERATOR_MESH_ID)

def _resolve_orders(mesh_id=None):
    """Get OrdersDocument for mesh_id, or the operator's default orders."""
    if not mesh_id:
        return mesh_orders
    doc = _orders_registry.get(mesh_id)
    return doc if doc else mesh_orders

# ── ntfy Push Notifications ──

try:
    import focuslock_ntfy as ntfy_mod
except ImportError:
    ntfy_mod = None

# Auto-derive topic from first known mesh_id, or use explicit config
_ntfy_server = _cfg.get("ntfy_server", "https://ntfy.sh")
_ntfy_topic = _cfg.get("ntfy_topic", "")
_ntfy_enabled = _cfg.get("ntfy_enabled", False) and ntfy_mod is not None

def _get_ntfy_topic():
    """Resolve ntfy topic — may depend on mesh_id created after startup."""
    if _ntfy_topic:
        return _ntfy_topic
    # Auto-derive from first mesh account if available
    if _mesh_accounts and _mesh_accounts.meshes:
        mid = next(iter(_mesh_accounts.meshes))
        return f"focuslock-{mid}"
    return ""

def ntfy_fn(version):
    """Best-effort ntfy publish. Called after order mutations."""
    if not _ntfy_enabled:
        return
    topic = _get_ntfy_topic()
    if topic:
        ntfy_mod.ntfy_publish(topic, version, _ntfy_server)

# Lion's public key for signature verification — loaded from phone on first sync
_lion_pubkey = ""

def get_lion_pubkey():
    global _lion_pubkey
    if not _lion_pubkey:
        pk = adb.get("focus_lock_lion_pubkey")
        if pk and pk != "null":
            _lion_pubkey = pk
    return _lion_pubkey

def init_mesh_from_adb():
    """Bootstrap mesh orders from phone's current ADB state on startup."""
    if mesh_orders.version > 0:
        print(f"[mesh] Orders already loaded (v{mesh_orders.version}), skipping ADB bootstrap")
        return
    print("[mesh] Bootstrapping orders from ADB...")
    # Map mesh order keys to focus_lock_* ADB keys
    key_map = {k: f"focus_lock_{k}" for k in mesh.ORDER_KEYS}
    # Also bootstrap lock_active (not a mesh order key, but homelab needs it for status)
    key_map["lock_active"] = "focus_lock_active"

    for mesh_key, adb_key in key_map.items():
        val = adb.get(adb_key)
        if val and val != "null":
            # Try to convert to int for numeric fields
            default = mesh.ORDER_KEYS.get(mesh_key, "")
            if isinstance(default, int):
                try:
                    val = int(val)
                except (ValueError, TypeError):
                    pass
            mesh_orders.set(mesh_key, val)

    mesh_orders.bump_version()
    print(f"[mesh] Bootstrapped orders v{mesh_orders.version} from ADB")

def mesh_local_status():
    """Build server's local status for gossip."""
    return {
        "type": "server",
        "hostname": MESH_NODE_ID,
        "services": ["mail", "bridge", "mesh"],
    }

def _admin_order_to_vault_blob(action, params, mesh_id=None):
    """Write an admin order as a relay-signed vault RPC blob so vault-mode slaves pick it up.
    Uses the RELAY's private key (P6.5 zero-knowledge compliance — Lion's key never on server).
    Only works for the operator's mesh (admin API is operator-scoped)."""
    if not RELAY_PRIVKEY_PEM:
        print(f"[admin] vault blob write skipped: no relay keypair")
        return
    try:
        from focuslock_vault import encrypt_body
    except ImportError:
        try:
            sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared"))
            from focuslock_vault import encrypt_body
        except ImportError:
            print(f"[admin] vault blob write skipped: focuslock_vault not available")
            return
    mid = mesh_id or OPERATOR_MESH_ID
    if not mid:
        return
    # Get registered nodes (recipients for encryption)
    nodes = _vault_store.get_nodes(mid)
    if not nodes:
        print(f"[admin] vault blob write skipped: no registered vault nodes")
        return
    recipients = [(n.get("node_id", ""), n.get("node_pubkey", "")) for n in nodes if n.get("node_pubkey")]
    if not recipients:
        return
    # Build RPC body (same format as Lion's Share apiVault)
    body = {"action": action, "params": params or {}}
    version = _vault_store.current_version(mid) + 1
    created_at = int(time.time() * 1000)
    blob = encrypt_body(mid, version, created_at, body, recipients, RELAY_PRIVKEY_PEM)
    ver, err = _vault_store.append(mid, blob)
    if err:
        print(f"[admin] vault blob append error: {err}")
    else:
        print(f"[admin] vault blob written: v{ver} action={action} (relay-signed)")

def on_mesh_orders_applied(orders_dict):
    """Called when mesh gossip applies new orders.
    Do NOT write back to phone via ADB — the phone is its own source of truth
    and has the mesh endpoints to receive orders directly. Writing via ADB
    creates a feedback loop that can overwrite the phone's current state."""
    print(f"[mesh] Orders applied locally: desktop={orders_dict.get('desktop_active')}")

def mesh_apply_order(action, params, orders):
    """Apply an order action on server. Mostly passes through to ADB."""
    # For now, server just updates the orders doc and syncs to ADB
    # The phone will handle enforcement-specific logic when it receives the gossip
    if action == "lock":
        # Set lock_active in orders so gossip carries it to all nodes
        orders.set("lock_active", 1)
        orders.set("message", params.get("message", ""))
        orders.set("mode", params.get("mode", "basic"))
        if "paywall" in params:
            orders.set("paywall", str(params["paywall"]))
        if "timer" in params:
            import time as t
            orders.set("unlock_at", int(t.time() * 1000) + int(params["timer"]) * 60000)
        # Forward lock command to phone directly
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{PHONE_URL}/api/lock",
                data=json.dumps({"pin": PHONE_PIN, **params}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
            print(f"[mesh] Direct push succeeded (lock)")
        except Exception as e:
            print(f"[mesh] Direct push failed (gossip will deliver): {e}")
    elif action == "unlock":
        orders.set("lock_active", 0)
        orders.set("message", params.get("message", "Unlocked"))
        # Forward unlock to phone directly
        try:
            import urllib.request
            req = urllib.request.Request(
                f"{PHONE_URL}/api/unlock",
                data=json.dumps({"pin": PHONE_PIN, **params}).encode(),
                headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=3)
            print(f"[mesh] Direct push succeeded (unlock)")
        except Exception as e:
            print(f"[mesh] Direct push failed (gossip will deliver): {e}")
    elif action == "set-geofence":
        orders.set("geofence_lat", params.get("lat", ""))
        orders.set("geofence_lon", params.get("lon", ""))
        orders.set("geofence_radius_m", params.get("radius", "100"))
    elif action == "clear-geofence":
        orders.set("geofence_lat", "")
        orders.set("geofence_lon", "")
        orders.set("geofence_radius_m", "")
    elif action == "set-curfew":
        orders.set("curfew_enabled", 1)
        orders.set("curfew_confine_hour", int(params.get("confine_hour", -1)))
        orders.set("curfew_release_hour", int(params.get("release_hour", -1)))
        orders.set("curfew_radius_m", int(params.get("radius", 100)))
        orders.set("curfew_lat", params.get("lat", ""))
        orders.set("curfew_lon", params.get("lon", ""))
    elif action == "clear-curfew":
        orders.set("curfew_enabled", 0)
    elif action == "set-bedtime":
        orders.set("bedtime_enabled", 1)
        orders.set("bedtime_lock_hour", int(params.get("lock_hour", -1)))
        orders.set("bedtime_unlock_hour", int(params.get("unlock_hour", -1)))
    elif action == "clear-bedtime":
        orders.set("bedtime_enabled", 0)
    elif action == "set-screen-time":
        orders.set("screen_time_quota_minutes", int(params.get("quota_minutes", 0)))
        orders.set("screen_time_reset_hour", int(params.get("reset_hour", 0)))
    elif action == "clear-screen-time":
        orders.set("screen_time_quota_minutes", 0)
    elif action == "add-paywall":
        current = orders.get("paywall", "0")
        try:
            current = int(current)
        except:
            current = 0
        orders.set("paywall", str(current + int(params.get("amount", 0))))
    elif action == "clear-paywall":
        orders.set("paywall", "0")
    elif action == "send-message":
        # Update orders.message so gossip carries the text to desktop collars
        # and the web UI status display.  The vault blob (written separately)
        # carries the full payload to the slave's send-message handler.
        msg = params.get("text") or params.get("message", "")
        if msg:
            orders.set("message", msg)
        if params.get("pinned"):
            orders.set("pinned_message", msg)
    elif action == "pin-message":
        orders.set("pinned_message", params.get("message", ""))
    elif action == "clear-pinned-message":
        orders.set("pinned_message", "")
    elif action == "pin-lion-message":
        orders.set("lion_pinned_message", params.get("message", ""))
    elif action == "clear-lion-pinned-message":
        orders.set("lion_pinned_message", "")
    elif action == "set-checkin":
        orders.set("checkin_deadline", int(params.get("deadline", -1)))
    elif action == "set-tribute":
        import time as t0
        orders.set("tribute_active", 1)
        orders.set("tribute_amount", int(params.get("amount", 1)))
        orders.set("tribute_last_applied", int(t0.time() * 1000))
    elif action == "clear-tribute":
        orders.set("tribute_active", 0)
    elif action == "start-streak":
        import time as t1
        orders.set("streak_enabled", 1)
        orders.set("streak_start", int(t1.time() * 1000))
        orders.set("streak_escapes_at_start", int(params.get("escapes", 0)))
        orders.set("streak_7d_claimed", 0)
        orders.set("streak_30d_claimed", 0)
    elif action == "stop-streak":
        orders.set("streak_enabled", 0)
    elif action == "start-fine":
        import time as t
        orders.set("fine_active", 1)
        orders.set("fine_amount", int(params.get("amount", 10)))
        orders.set("fine_interval_m", int(params.get("interval", 60)))
        orders.set("fine_last_applied", int(t.time() * 1000))
    elif action == "stop-fine":
        orders.set("fine_active", 0)
    elif action == "lock-desktop":
        orders.set("desktop_active", 1)
        if "message" in params:
            orders.set("desktop_message", params["message"])
        if "devices" in params:
            orders.set("desktop_locked_devices", params["devices"])
    elif action == "unlock-desktop":
        orders.set("desktop_active", 0)
        orders.set("desktop_message", "")
        orders.set("desktop_locked_devices", "")
    elif action == "start-body-check":
        orders.set("body_check_active", 1)
        orders.set("body_check_area", params.get("area", "body"))
        orders.set("body_check_interval_h", int(params.get("interval_h", 12)))
        import time as t2
        orders.set("body_check_last", int(t2.time() * 1000))
    elif action == "stop-body-check":
        orders.set("body_check_active", 0)
    elif action == "set-countdown":
        import time as t_cd
        minutes = int(params.get("minutes", 30))
        lock_at = int(t_cd.time() * 1000) + (minutes * 60_000)
        orders.set("countdown_lock_at", lock_at)
        orders.set("countdown_message", params.get("message", ""))
        return {"applied": action, "lock_at": lock_at, "minutes": minutes}
    elif action == "cancel-countdown":
        orders.set("countdown_lock_at", 0)
        orders.set("countdown_message", "")
    elif action == "release-device":
        target = params.get("target", "")
        orders.set("released", target)
        import time as t_rel
        orders.set("release_timestamp", str(int(t_rel.time() * 1000)))
        # Forward release to phone directly via API
        if target == "all" or target == "pixel":
            try:
                import urllib.request
                req = urllib.request.Request(
                    f"{PHONE_URL}/api/release-forever",
                    data=json.dumps({"pin": PHONE_PIN}).encode(),
                    headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=3)
                print(f"[mesh] Direct push succeeded (release)")
            except Exception as e:
                print(f"[mesh] Direct push failed (gossip will deliver): {e}")
        # Clean up bridge device registry
        if target == "all":
            for reg in ["/run/focuslock/devices.json", "/run/focuslock/controller.json"]:
                try:
                    os.remove(reg)
                    print(f"[mesh] Removed: {reg}")
                except FileNotFoundError:
                    pass
        elif target:
            try:
                reg = "/run/focuslock/devices.json"
                with open(reg, "r") as f:
                    devices = json.load(f)
                if target in devices:
                    del devices[target]
                    with open(reg, "w") as f:
                        json.dump(devices, f)
                    print(f"[mesh] Removed {target} from device registry")
            except Exception:
                pass
    return {"applied": action}

# Seed peers from config
def seed_mesh_peers():
    """Add configured devices as initial mesh peers."""
    for addr in _phone_addrs:
        mesh_peers.update_peer("phone", node_type="phone",
                               addresses=[addr], port=_phone_port)
    # Desktop collars self-register via gossip


DESKTOP_REGISTRY_FILE = "/run/focuslock/desktop-heartbeats.json"
DESKTOP_WARN_DAYS = 7         # 1 week — notify Lion via Lion's Share
DESKTOP_PENALTY_DAYS = 14     # 2 weeks — $50 penalty, first offense
DESKTOP_ESCALATE_DAYS = 7     # every week after that — another $50

# ── IMAP: Payment Verification ──

from focuslock_payment import (
    load_payment_providers, load_iso_codes,
    check_payment_emails, extract_amount, get_body,
    unlock_phone, reduce_paywall, score_payment_email,
)

_banks_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared", "banks.json")
DEFAULT_PAYMENT_PROVIDERS = load_payment_providers(_banks_path)
_ISO_CODES = load_iso_codes(_banks_path)
MIN_PAYMENT = float(_cfg.get("banking", {}).get("min_payment", 0.01))
MAX_PAYMENT = float(_cfg.get("banking", {}).get("max_payment", 10000))

_LEDGER_PATH = os.path.join(os.path.dirname(MESH_ORDERS_FILE), "payment_ledger.json")
payment_ledger = mesh.PaymentLedger(persist_path=_LEDGER_PATH)


def enforce_jail():
    """Immediately enforce jail via ADB — called on entrap webhook."""
    print(f"[{now()}] ENTRAP — enforcing jail immediately via ADB")
    cmds = [
        "cmd statusbar disable-for-setup true",
        "pm disable-user --user 0 com.android.launcher3",
        "pm disable-user --user 0 com.android.settings",
        "settings put global user_switcher_enabled 0",
        "am start -n com.focuslock/.FocusActivity",
    ]
    for cmd in cmds:
        adb.shell_all(cmd)
    print(f"[{now()}] Jail enforced")


# ── SMTP: Compliment Evidence ──

from focuslock_evidence import send_evidence as _send_evidence_impl, get_notif_pref

def send_evidence(text, evidence_type="compliment"):
    """Convenience wrapper capturing module-level config."""
    _send_evidence_impl(
        text, evidence_type,
        mesh_orders=mesh_orders, adb=adb,
        partner_email=PARTNER_EMAIL, smtp_host=SMTP_HOST,
        mail_user=MAIL_USER, mail_pass=MAIL_PASS,
    )


from focuslock_llm import verify_photo_with_llm, generate_task_with_llm
from focuslock_http import JSONResponseMixin


# ── Desktop Dead-Man's Switch ──

def check_desktop_heartbeats():
    """Check registered desktops. If a collared PC goes silent for 2 weeks, penalize."""
    while True:
        try:
            if os.path.exists(DESKTOP_REGISTRY_FILE):
                with open(DESKTOP_REGISTRY_FILE, "r") as f:
                    registry = json.load(f)

                now_ts = time.time()
                changed = False
                for hostname, info in registry.items():
                    last_ts = info.get("last_seen_ts", 0)
                    if last_ts == 0:
                        continue
                    silence_days = (now_ts - last_ts) / 86400

                    # 1 week — warn Lion via pinned message on phone
                    if silence_days >= DESKTOP_WARN_DAYS and not info.get("warned", False):
                        print(f"[{now()}] DESKTOP WARNING: {hostname} silent for {silence_days:.0f} days")
                        adb.put("focus_lock_pinned_message",
                               f"Desktop collar offline: {hostname} ({silence_days:.0f} days)")
                        info["warned"] = True
                        changed = True

                    # 2 weeks — penalty
                    if silence_days >= DESKTOP_PENALTY_DAYS:
                        last_penalty = info.get("last_penalty_ts", 0)
                        days_since_penalty = (now_ts - last_penalty) / 86400 if last_penalty else 999
                        if days_since_penalty >= DESKTOP_ESCALATE_DAYS:
                            print(f"[{now()}] DESKTOP PENALTY: {hostname} silent {silence_days:.0f} days — adding $50")
                            # Add $50 to paywall
                            pw_str = adb.get("focus_lock_paywall")
                            pw = 0
                            try:
                                pw = int(pw_str) if pw_str and pw_str != "null" else 0
                            except:
                                pass
                            pw += 50
                            adb.put("focus_lock_paywall", str(pw))
                            adb.put_str("focus_lock_message",
                                        f"Desktop collar offline: {hostname}. $50 penalty applied.")
                            info["last_penalty_ts"] = now_ts
                            changed = True

                if changed:
                    tmp = DESKTOP_REGISTRY_FILE + ".tmp"
                    with open(tmp, "w") as f:
                        json.dump(registry, f, indent=2)
                    os.rename(tmp, DESKTOP_REGISTRY_FILE)

        except Exception as e:
            print(f"[{now()}] Desktop heartbeat checker error: {e}")

        time.sleep(3600)  # Check hourly


# ── Daily Tribute + Fine Enforcement ──

def check_tributes_and_fines():
    """Periodic check for daily tribute (cost of freedom while unlocked)
    and recurring fines. Runs every 5 minutes to stay responsive."""
    while True:
        try:
            now_ms = int(time.time() * 1000)

            # Daily tribute — accrues while phone is UNLOCKED
            tribute_active = mesh_orders.get("tribute_active", 0)
            if str(tribute_active) == "1":
                lock_active = mesh_orders.get("lock_active", 0)
                if str(lock_active) != "1":  # unlocked → tribute accrues
                    last = int(mesh_orders.get("tribute_last_applied", 0))
                    elapsed_ms = now_ms - last if last else 0
                    if elapsed_ms >= 86400000:  # 24 hours
                        amount = int(mesh_orders.get("tribute_amount", 1))
                        current_pw = 0
                        try:
                            current_pw = int(mesh_orders.get("paywall", "0"))
                        except (ValueError, TypeError):
                            pass
                        mesh_orders.set("paywall", str(current_pw + amount))
                        mesh_orders.set("tribute_last_applied", now_ms)
                        mesh_orders.bump_version()
                        mesh.push_to_peers(MESH_NODE_ID, mesh_orders, mesh_peers)
                        if ntfy_fn:
                            try: ntfy_fn(mesh_orders.version)
                            except: pass
                        print(f"[{now()}] Daily tribute: +${amount} (unlocked for 24h+)")

            # Recurring fine — accrues regardless of lock state
            fine_active = mesh_orders.get("fine_active", 0)
            if str(fine_active) == "1":
                fine_amount = int(mesh_orders.get("fine_amount", 10))
                fine_interval = int(mesh_orders.get("fine_interval_m", 60))
                last_fine = int(mesh_orders.get("fine_last_applied", 0))
                elapsed_ms = now_ms - last_fine if last_fine else 0
                if elapsed_ms >= fine_interval * 60000:
                    current_pw = 0
                    try:
                        current_pw = int(mesh_orders.get("paywall", "0"))
                    except (ValueError, TypeError):
                        pass
                    mesh_orders.set("paywall", str(current_pw + fine_amount))
                    mesh_orders.set("fine_last_applied", now_ms)
                    mesh_orders.bump_version()
                    push_to_peers(MESH_NODE_ID, mesh_orders, mesh_peers)
                    if ntfy_fn:
                        try: ntfy_fn(mesh_orders.version)
                        except: pass
                    print(f"[{now()}] Fine applied: +${fine_amount}")

            # Streak bonuses — 7d clean = -$5, 30d clean = -$25
            streak_enabled = mesh_orders.get("streak_enabled", 0)
            if str(streak_enabled) == "1":
                streak_start = int(mesh_orders.get("streak_start", 0))
                escapes_at_start = int(mesh_orders.get("streak_escapes_at_start", 0))
                # Read current escapes from the runtime body (set by slave)
                current_escapes = 0
                try:
                    current_escapes = int(mesh_orders.get("escapes", 0))
                except (ValueError, TypeError):
                    pass
                streak_broken = current_escapes > escapes_at_start

                if streak_broken:
                    mesh_orders.set("streak_enabled", 0)
                    mesh_orders.bump_version()
                    mesh.push_to_peers(MESH_NODE_ID, mesh_orders, mesh_peers)
                    print(f"[{now()}] Streak broken: escapes {escapes_at_start} → {current_escapes}")
                elif streak_start > 0:
                    elapsed_days = (now_ms - streak_start) / 86400000

                    if elapsed_days >= 7 and str(mesh_orders.get("streak_7d_claimed", 0)) != "1":
                        current_pw = 0
                        try:
                            current_pw = int(mesh_orders.get("paywall", "0"))
                        except (ValueError, TypeError):
                            pass
                        new_pw = max(0, current_pw - 5)
                        mesh_orders.set("paywall", str(new_pw))
                        mesh_orders.set("streak_7d_claimed", 1)
                        mesh_orders.bump_version()
                        mesh.push_to_peers(MESH_NODE_ID, mesh_orders, mesh_peers)
                        print(f"[{now()}] Streak bonus: 7d clean → -$5 (paywall ${current_pw} → ${new_pw})")

                    if elapsed_days >= 30 and str(mesh_orders.get("streak_30d_claimed", 0)) != "1":
                        current_pw = 0
                        try:
                            current_pw = int(mesh_orders.get("paywall", "0"))
                        except (ValueError, TypeError):
                            pass
                        new_pw = max(0, current_pw - 25)
                        mesh_orders.set("paywall", str(new_pw))
                        mesh_orders.set("streak_30d_claimed", 1)
                        mesh_orders.bump_version()
                        mesh.push_to_peers(MESH_NODE_ID, mesh_orders, mesh_peers)
                        print(f"[{now()}] Streak bonus: 30d clean → -$25 (paywall ${current_pw} → ${new_pw})")

        except Exception as e:
            print(f"[{now()}] Tribute/fine/streak checker error: {e}")

        time.sleep(300)  # Check every 5 minutes


# ── Webhook HTTP Server ──

class PairingRegistry:
    """Persisted pairing registry for relay-based key exchange."""
    def __init__(self, persist_path="/run/focuslock/pairing-registry.json"):
        self.path = persist_path
        self.lock = threading.Lock()
        self.entries = {}  # passphrase -> {bunny_pubkey, bunny_node_id, lion_pubkey, lion_node_id, paired, expires_at}
        self._load()

    def _load(self):
        try:
            if os.path.exists(self.path):
                with open(self.path) as f:
                    self.entries = json.load(f)
        except: pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.entries, f)
            os.replace(tmp, self.path)
        except: pass

    def register(self, passphrase, bunny_pubkey, node_id):
        with self.lock:
            self.entries[passphrase.upper()] = {
                "bunny_pubkey": bunny_pubkey,
                "bunny_node_id": node_id,
                "lion_pubkey": None,
                "lion_node_id": None,
                "paired": False,
                "expires_at": time.time() + 3600,
            }
            self._save()

    def claim(self, passphrase, lion_pubkey, lion_node_id):
        with self.lock:
            key = passphrase.upper()
            entry = self.entries.get(key)
            if not entry or time.time() > entry["expires_at"]:
                return None
            entry["lion_pubkey"] = lion_pubkey
            entry["lion_node_id"] = lion_node_id
            entry["paired"] = True
            self._save()
            return entry

    def get_pending_pairing(self, node_id):
        """Check if node_id has a pairing waiting (Lion claimed but Bunny hasn't received yet)."""
        with self.lock:
            for phrase, entry in self.entries.items():
                if (entry.get("bunny_node_id") == node_id
                    and entry.get("lion_pubkey")
                    and not entry.get("delivered")):
                    return entry
            return None

    def mark_delivered(self, node_id):
        """Mark pairing as delivered to Bunny."""
        with self.lock:
            for entry in self.entries.values():
                if entry.get("bunny_node_id") == node_id and entry.get("lion_pubkey"):
                    entry["delivered"] = True
                    self._save()
                    break

    def status(self, passphrase):
        with self.lock:
            entry = self.entries.get(passphrase.upper())
            if not entry or time.time() > entry["expires_at"]:
                return None
            return entry

    def cleanup(self):
        with self.lock:
            now = time.time()
            self.entries = {k: v for k, v in self.entries.items() if now < v["expires_at"]}
            self._save()

_pairing_registry = PairingRegistry()


# ── Mesh Account Store ──

_INVITE_WORDS = [
    "WOLF", "BEAR", "LION", "HAWK", "DEER", "CROW", "FROG", "LYNX",
    "SEAL", "DOVE", "WREN", "NEWT", "MOTH", "WASP", "TOAD", "PIKE",
    "LARK", "SWAN", "MINK", "BOAR", "COLT", "MARE", "BULL", "GOAT",
    "HARE", "KITE", "IBIS", "ORCA", "PUMA", "MOLE",
]


class MeshAccountStore:
    """Manages mesh accounts for account-based pairing.
    Per-mesh orders are stored in MeshOrdersRegistry (see above)."""

    # Signup rate limiting: max_creates per window_s per IP
    RATE_LIMIT_MAX = 3
    RATE_LIMIT_WINDOW_S = 3600  # 1 hour
    # Invite code TTL (seconds)
    INVITE_TTL_S = 86400  # 24 hours
    # Per-mesh quotas
    DEFAULT_MAX_BLOBS_PER_DAY = 5000
    DEFAULT_MAX_TOTAL_BYTES_MB = 100

    def __init__(self, persist_dir="/run/focuslock/meshes"):
        self.persist_dir = persist_dir
        os.makedirs(persist_dir, exist_ok=True)
        self.lock = threading.Lock()
        self.meshes = {}  # mesh_id -> account dict
        self._create_rate = {}  # ip -> [timestamp, ...]
        self._load_all()

    def _load_all(self):
        try:
            for fname in os.listdir(self.persist_dir):
                if fname.endswith(".json"):
                    path = os.path.join(self.persist_dir, fname)
                    with open(path) as f:
                        account = json.load(f)
                    self.meshes[account["mesh_id"]] = account
        except Exception:
            pass

    def _save(self, mesh_id):
        account = self.meshes.get(mesh_id)
        if not account:
            return
        path = os.path.join(self.persist_dir, f"{mesh_id}.json")
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(account, f, indent=2)
        os.replace(tmp, path)

    def check_rate_limit(self, client_ip):
        """Return True if the IP is within rate limits, False if exceeded."""
        now_t = time.time()
        cutoff = now_t - self.RATE_LIMIT_WINDOW_S
        timestamps = self._create_rate.get(client_ip, [])
        timestamps = [t for t in timestamps if t > cutoff]
        self._create_rate[client_ip] = timestamps
        return len(timestamps) < self.RATE_LIMIT_MAX

    def _record_create(self, client_ip):
        ts = self._create_rate.get(client_ip, [])
        ts.append(time.time())
        self._create_rate[client_ip] = ts

    def create(self, lion_pubkey, pin="", client_ip=""):
        with self.lock:
            if client_ip:
                self._record_create(client_ip)
            mesh_id = base64.urlsafe_b64encode(os.urandom(9)).decode().rstrip("=")
            auth_token = base64.urlsafe_b64encode(os.urandom(32)).decode().rstrip("=")
            invite_code = self._gen_invite_code()
            if not pin:
                pin = str(secrets.randbelow(9000) + 1000)
            account = {
                "mesh_id": mesh_id,
                "lion_pubkey": lion_pubkey,
                "auth_token": auth_token,
                "invite_code": invite_code,
                "invite_expires_at": int(time.time()) + self.INVITE_TTL_S,
                "invite_consumed": False,
                "pin": pin,
                "created_at": int(time.time()),
                "nodes": {},
                "vault_only": False,
                "max_blobs_per_day": self.DEFAULT_MAX_BLOBS_PER_DAY,
                "max_total_bytes_mb": self.DEFAULT_MAX_TOTAL_BYTES_MB,
            }
            self.meshes[mesh_id] = account
            self._save(mesh_id)
            return account

    def join(self, invite_code, node_id, node_type, bunny_pubkey=""):
        with self.lock:
            account = self._find_by_invite(invite_code)
            if not account:
                return None, "invalid invite code"
            # Check expiry
            expires = account.get("invite_expires_at", 0)
            if expires and time.time() > expires:
                return None, "invite code expired"
            # Check one-time use
            if account.get("invite_consumed"):
                return None, "invite code already used"
            account["invite_consumed"] = True
            account["nodes"][node_id] = {
                "type": node_type,
                "joined_at": int(time.time()),
                "bunny_pubkey": bunny_pubkey,
            }
            self._save(account["mesh_id"])
            return account, None

    def get(self, mesh_id):
        return self.meshes.get(mesh_id)

    def validate_auth(self, mesh_id, auth_token):
        account = self.meshes.get(mesh_id)
        if not account:
            return False
        expected = account.get("auth_token", "")
        return hmac.compare_digest(expected, auth_token)

    def update_node(self, mesh_id, node_id, **fields):
        with self.lock:
            account = self.meshes.get(mesh_id)
            if account and node_id in account.get("nodes", {}):
                account["nodes"][node_id].update(fields)
                self._save(mesh_id)

    def is_vault_only(self, mesh_id):
        """Phase D gate. When true, plaintext /api/mesh/{id}/{status,sync,order}
        endpoints return 410 Gone. Lion's Share and slaves must use /vault/*."""
        account = self.meshes.get(mesh_id)
        return bool(account and account.get("vault_only", False))

    def list_mesh_ids(self):
        """Snapshot of all known mesh_ids. Used by /version for the
        vault_only_meshes counter exposed in the audit transparency response."""
        with self.lock:
            return list(self.meshes.keys())

    def set_vault_only(self, mesh_id, value):
        with self.lock:
            account = self.meshes.get(mesh_id)
            if not account:
                return False
            account["vault_only"] = bool(value)
            self._save(mesh_id)
            return True

    def _find_by_invite(self, invite_code):
        code = invite_code.upper().strip()
        for account in self.meshes.values():
            if account.get("invite_code", "").upper() == code:
                return account
        return None

    def _gen_invite_code(self):
        w1 = secrets.choice(_INVITE_WORDS)
        num = secrets.randbelow(90) + 10
        w2 = secrets.choice(_INVITE_WORDS)
        return f"{w1}-{num}-{w2}"


_mesh_accounts = MeshAccountStore()


# ── Vault storage (zero-knowledge mesh) ──
# See docs/VAULT-DESIGN.md for protocol spec.

VAULT_MODE_ALLOWED = _cfg.get("vault_mode_allowed", True)
VAULT_RETENTION_DAYS = _cfg.get("vault_retention_days", 7)

# ── Admin API (enforcement infrastructure) ──
# Separate from mesh PIN — used by sync-standing-orders.sh and CLAUDE.md
# paywall checks.  Requests without mesh_id operate on the operator's own
# mesh (backwards compat).  For other meshes that are vault_only the admin
# API returns metadata only — never plaintext orders.
ADMIN_TOKEN = _cfg.get("admin_token", "") or os.environ.get("FOCUSLOCK_ADMIN_TOKEN", "")
OPERATOR_MESH_ID = _cfg.get("operator_mesh_id", "") or os.environ.get("FOCUSLOCK_OPERATOR_MESH_ID", "")
_init_orders_registry()  # Now that OPERATOR_MESH_ID is known, register operator's mesh

# ── Relay Keypair (P6.5 zero-knowledge compliance) ──
# The relay signs admin-originated vault blobs with its OWN key, not Lion's.
# For public hosted relays without OPERATOR_MESH_ID, this keypair exists but
# is never used for signing (admin API is operator-only).
RELAY_PRIVKEY_PEM = ""
RELAY_PUBKEY_PEM = ""
RELAY_PUBKEY_DER_B64 = ""

def _init_relay_keypair():
    """Generate or load the relay's RSA-2048 keypair.
    Stored alongside config at ~/.config/focuslock/relay_{priv,pub}key.pem."""
    global RELAY_PRIVKEY_PEM, RELAY_PUBKEY_PEM, RELAY_PUBKEY_DER_B64
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives import serialization
    except ImportError:
        print("[relay] cryptography not available — relay keypair disabled")
        return
    config_dir = os.path.expanduser("~/.config/focuslock")
    os.makedirs(config_dir, mode=0o700, exist_ok=True)
    priv_path = os.path.join(config_dir, "relay_privkey.pem")
    pub_path = os.path.join(config_dir, "relay_pubkey.pem")
    if os.path.exists(priv_path) and os.path.exists(pub_path):
        # Enforce private key file permissions on load
        mode = os.stat(priv_path).st_mode & 0o777
        if mode & 0o077:
            print(f"[relay] WARNING: {priv_path} is group/world-readable (mode {oct(mode)}), fixing")
            os.chmod(priv_path, 0o600)
        with open(priv_path) as f:
            RELAY_PRIVKEY_PEM = f.read().strip()
        with open(pub_path) as f:
            RELAY_PUBKEY_PEM = f.read().strip()
    else:
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        RELAY_PRIVKEY_PEM = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode()
        RELAY_PUBKEY_PEM = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        with open(priv_path, "w") as f:
            f.write(RELAY_PRIVKEY_PEM)
        os.chmod(priv_path, 0o600)
        with open(pub_path, "w") as f:
            f.write(RELAY_PUBKEY_PEM)
        print(f"[relay] Generated new RSA-2048 keypair at {priv_path}")
    # Compute base64 DER for node registration
    try:
        pk = serialization.load_pem_public_key(RELAY_PUBKEY_PEM.encode())
        der = pk.public_bytes(serialization.Encoding.DER,
                              serialization.PublicFormat.SubjectPublicKeyInfo)
        RELAY_PUBKEY_DER_B64 = base64.b64encode(der).decode()
        print(f"[relay] Keypair loaded (pubkey {len(der)} bytes)")
    except Exception as e:
        print(f"[relay] Keypair DER export failed: {e}")

_init_relay_keypair()


def _safe_mesh_id(mesh_id):
    """Allow only [A-Za-z0-9_-] in mesh_id (matches base64url alphabet)."""
    return _safe_mesh_id_static(mesh_id)


class VaultStore:
    """Opaque encrypted blob storage for vault-mode meshes.
    The server stores ciphertext blobs and verifies Lion signatures
    but cannot decrypt order contents."""

    def __init__(self, base_dir="/run/focuslock/vaults"):
        self.base_dir = base_dir
        os.makedirs(base_dir, exist_ok=True)
        self.lock = threading.Lock()

    def _mesh_dir(self, mesh_id):
        if not _safe_mesh_id(mesh_id):
            return None
        return os.path.join(self.base_dir, mesh_id)

    def _ensure_mesh(self, mesh_id):
        d = self._mesh_dir(mesh_id)
        if d is None:
            return None
        os.makedirs(os.path.join(d, "blobs"), exist_ok=True)
        return d

    def _list_blob_versions(self, mesh_id):
        d = self._mesh_dir(mesh_id)
        if not d:
            return []
        blobs_dir = os.path.join(d, "blobs")
        if not os.path.exists(blobs_dir):
            return []
        versions = []
        for fname in os.listdir(blobs_dir):
            if not fname.endswith(".json"):
                continue
            try:
                versions.append(int(fname[:-5]))
            except ValueError:
                continue
        versions.sort()
        return versions

    def current_version(self, mesh_id):
        versions = self._list_blob_versions(mesh_id)
        return versions[-1] if versions else 0

    def total_bytes(self, mesh_id):
        """Total bytes of all stored blobs for this mesh."""
        d = self._mesh_dir(mesh_id)
        if not d:
            return 0
        blobs_dir = os.path.join(d, "blobs")
        if not os.path.exists(blobs_dir):
            return 0
        total = 0
        for fname in os.listdir(blobs_dir):
            try:
                total += os.path.getsize(os.path.join(blobs_dir, fname))
            except OSError:
                pass
        return total

    def append(self, mesh_id, blob):
        """Append a blob. Returns (version, error). Blob version must be > current."""
        d = self._ensure_mesh(mesh_id)
        if d is None:
            return 0, "invalid mesh_id"
        with self.lock:
            current = self.current_version(mesh_id)
            version = blob.get("version", 0)
            if not isinstance(version, int):
                return 0, "version must be int"
            if version <= current:
                return current, f"version {version} not greater than current {current}"
            path = os.path.join(d, "blobs", f"{version:08d}.json")
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(blob, f, separators=(",", ":"))
            os.replace(tmp, path)
            # Lazy GC on append
            try:
                self.gc(mesh_id)
            except Exception as e:
                print(f"[vault] lazy gc error: {e}")
            return version, None

    def since(self, mesh_id, version):
        """Return (blobs_list, current_version). Blobs sorted ascending."""
        d = self._mesh_dir(mesh_id)
        if not d:
            return [], 0
        blobs_dir = os.path.join(d, "blobs")
        if not os.path.exists(blobs_dir):
            return [], 0
        versions = self._list_blob_versions(mesh_id)
        if not versions:
            return [], 0
        result = []
        for v in versions:
            if v > version:
                try:
                    with open(os.path.join(blobs_dir, f"{v:08d}.json")) as f:
                        result.append(json.load(f))
                except Exception as e:
                    print(f"[vault] read error v{v}: {e}")
        return result, versions[-1]

    def gc(self, mesh_id, retention_days=None):
        """Delete blobs older than retention_days. Always retain the latest."""
        if retention_days is None:
            retention_days = VAULT_RETENTION_DAYS
        d = self._mesh_dir(mesh_id)
        if not d:
            return 0
        blobs_dir = os.path.join(d, "blobs")
        if not os.path.exists(blobs_dir):
            return 0
        versions = self._list_blob_versions(mesh_id)
        if len(versions) <= 1:
            return 0
        cutoff = time.time() - retention_days * 86400
        removed = 0
        # Keep the latest always; consider older ones for deletion
        for v in versions[:-1]:
            path = os.path.join(blobs_dir, f"{v:08d}.json")
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    removed += 1
            except FileNotFoundError:
                pass
            except Exception as e:
                print(f"[vault] gc error v{v}: {e}")
        return removed

    def _read_json(self, mesh_id, fname, default):
        d = self._mesh_dir(mesh_id)
        if not d:
            return default
        path = os.path.join(d, fname)
        if not os.path.exists(path):
            return default
        try:
            with open(path) as f:
                return json.load(f)
        except Exception:
            return default

    def _write_json(self, mesh_id, fname, value):
        d = self._ensure_mesh(mesh_id)
        if d is None:
            return False
        path = os.path.join(d, fname)
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(value, f, indent=2)
        os.replace(tmp, path)
        return True

    def get_nodes(self, mesh_id):
        return self._read_json(mesh_id, "nodes.json", [])

    def add_node(self, mesh_id, node_entry):
        with self.lock:
            nodes = self.get_nodes(mesh_id)
            nodes = [n for n in nodes if n.get("node_id") != node_entry.get("node_id")]
            nodes.append(node_entry)
            return self._write_json(mesh_id, "nodes.json", nodes)

    def get_pending_nodes(self, mesh_id):
        return self._read_json(mesh_id, "nodes_pending.json", [])

    def add_pending_node(self, mesh_id, request):
        with self.lock:
            pending = self.get_pending_nodes(mesh_id)
            pending = [p for p in pending if p.get("node_id") != request.get("node_id")]
            pending.append(request)
            return self._write_json(mesh_id, "nodes_pending.json", pending)

    def remove_pending_node(self, mesh_id, node_id):
        with self.lock:
            pending = self.get_pending_nodes(mesh_id)
            pending = [p for p in pending if p.get("node_id") != node_id]
            self._write_json(mesh_id, "nodes_pending.json", pending)

    @staticmethod
    def _rejection_key(node_pubkey):
        """Hash a pubkey to a stable rejection identifier.
        Tied to the cryptographic identity, not the (mutable) node_id alias —
        so a slave that regenerates its keypair becomes a new identity and
        can re-request, while a slave reusing a rejected key stays blocked."""
        if not node_pubkey:
            return ""
        import hashlib
        return hashlib.sha256(node_pubkey.encode("utf-8")).hexdigest()[:24]

    def get_rejected_nodes(self, mesh_id):
        return self._read_json(mesh_id, "nodes_rejected.json", [])

    def is_rejected(self, mesh_id, node_pubkey):
        key = self._rejection_key(node_pubkey)
        if not key:
            return False
        for entry in self.get_rejected_nodes(mesh_id):
            if entry.get("key") == key:
                return True
        return False

    def add_rejected_node(self, mesh_id, node_id, node_pubkey, reason=""):
        key = self._rejection_key(node_pubkey)
        if not key:
            return False
        with self.lock:
            rejected = self.get_rejected_nodes(mesh_id)
            rejected = [r for r in rejected if r.get("key") != key]
            rejected.append({
                "key": key,
                "node_id": node_id,
                "rejected_at": int(time.time()),
                "reason": reason or "",
            })
            return self._write_json(mesh_id, "nodes_rejected.json", rejected)

    def clear_rejection(self, mesh_id, node_pubkey):
        """Drop a pubkey from the rejected list. Used when Lion explicitly
        approves a node whose key was previously rejected."""
        key = self._rejection_key(node_pubkey)
        if not key:
            return False
        with self.lock:
            rejected = self.get_rejected_nodes(mesh_id)
            new_rejected = [r for r in rejected if r.get("key") != key]
            if len(new_rejected) == len(rejected):
                return False
            self._write_json(mesh_id, "nodes_rejected.json", new_rejected)
            return True


_vault_store = VaultStore()


def _relay_self_register():
    """Auto-register the relay as a vault node for the operator's mesh (P6.5).
    Only runs if OPERATOR_MESH_ID is set and relay has a keypair.
    No Lion approval needed — the server IS the operator's infrastructure."""
    if not OPERATOR_MESH_ID or not RELAY_PUBKEY_DER_B64:
        return
    nodes = _vault_store.get_nodes(OPERATOR_MESH_ID)
    for n in nodes:
        if n.get("node_id") == "relay":
            # Already registered — check if pubkey changed (key rotation)
            if n.get("node_pubkey") == RELAY_PUBKEY_DER_B64:
                print(f"[relay] Already registered as vault node for {OPERATOR_MESH_ID}")
                return
            print(f"[relay] Key rotated — re-registering vault node")
            break
    _vault_store.add_node(OPERATOR_MESH_ID, {
        "node_id": "relay",
        "node_type": "relay",
        "node_pubkey": RELAY_PUBKEY_DER_B64,
        "registered_at": int(time.time()),
    })
    print(f"[relay] Registered as vault node for operator mesh {OPERATOR_MESH_ID}")

_relay_self_register()


# In-memory daily blob counter per mesh — resets on date change.
# Key: (mesh_id, "YYYYMMDD"), Value: count.
_daily_blob_counts: dict = {}
_daily_blob_lock = threading.Lock()


def _daily_blob_count(mesh_id: str) -> int:
    """Return today's blob count for a mesh, pruning stale dates."""
    today = time.strftime("%Y%m%d")
    with _daily_blob_lock:
        # Prune old dates (max 1 stale key per mesh)
        stale = [k for k in _daily_blob_counts if k[0] == mesh_id and k[1] != today]
        for k in stale:
            del _daily_blob_counts[k]
        return _daily_blob_counts.get((mesh_id, today), 0)


def _daily_blob_increment(mesh_id: str):
    """Increment today's blob count for a mesh."""
    today = time.strftime("%Y%m%d")
    with _daily_blob_lock:
        key = (mesh_id, today)
        _daily_blob_counts[key] = _daily_blob_counts.get(key, 0) + 1


def _load_lion_pubkey_obj(key_str):
    """Load Lion pubkey from PEM or bare-base64-DER format.
    Returns a cryptography PublicKey object, or None on failure."""
    if not key_str:
        return None
    try:
        from cryptography.hazmat.primitives import serialization
        if "BEGIN PUBLIC KEY" in key_str:
            return serialization.load_pem_public_key(key_str.encode("utf-8"))
        # Bare base64 DER (SubjectPublicKeyInfo)
        cleaned = "".join(key_str.split())
        der = base64.b64decode(cleaned)
        return serialization.load_der_public_key(der)
    except Exception as e:
        print(f"[vault] pubkey load error: {e}")
        return None


def _verify_signed_payload(payload, signature_b64, lion_pubkey_str, quiet=False):
    """Verify RSA-PKCS1v15-SHA256 signature over canonical_json(payload).
    The 'signature' key is excluded from the canonicalized payload."""
    if not signature_b64:
        return False
    pubkey = _load_lion_pubkey_obj(lion_pubkey_str)
    if pubkey is None:
        return False
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
        signed = {k: v for k, v in payload.items() if k != "signature"}
        data = mesh.canonical_json(signed)
        sig = base64.b64decode(signature_b64)
        pubkey.verify(sig, data, asym_padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception as e:
        if not quiet:
            print(f"[vault] sig verify failed: {e}")
        return False


def _verify_blob_two_writer(blob, lion_pubkey, registered_nodes):
    """Multi-writer verification. Try Lion pubkey first (order blobs from
    controller), then iterate registered node pubkeys (slave runtime pushes,
    relay admin orders, desktop collars). Returns (writer_role, writer_id)
    on success or (None, None) on failure.
    writer_role is "lion" or "node" (includes relay nodes)."""
    sig = blob.get("signature", "")
    if not sig:
        return None, None
    if lion_pubkey and _verify_signed_payload(blob, sig, lion_pubkey, quiet=True):
        return "lion", "lion"
    for node in registered_nodes or []:
        npub = node.get("node_pubkey", "")
        if npub and _verify_signed_payload(blob, sig, npub, quiet=True):
            return "node", node.get("node_id", "")
    return None, None


def _vault_resolve_mesh(mesh_id):
    """Look up the mesh account by mesh_id. Returns (account, lion_pubkey_str) or (None, None)."""
    account = _mesh_accounts.get(mesh_id)
    if not account:
        return None, None
    return account, account.get("lion_pubkey", "")


class WebhookHandler(JSONResponseMixin, BaseHTTPRequestHandler):
    MAX_BODY_BYTES = 1_048_576  # 1 MB

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
            self.respond(400, {"error": "invalid JSON"})
            return

        if self.path == "/webhook/compliment":
            text = data.get("text", "")
            send_evidence(text, "compliment")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/gratitude":
            entries = data.get("entries", [])
            text = "\n".join(f"{i+1}. {e}" for i, e in enumerate(entries))
            send_evidence(text, "gratitude")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/love_letter":
            text = data.get("text", "")
            send_evidence(text, "love letter")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/entrap":
            enforce_jail()
            send_evidence("Phone has been ENTRAPPED.", "entrap")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/offer":
            text = data.get("offer", "")
            send_evidence(text, "negotiation offer")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/location":
            lat = data.get("lat", 0)
            lon = data.get("lon", 0)
            print(f"[{now()}] Location: {lat}, {lon}")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/geofence-breach":
            lat = data.get("lat", 0)
            lon = data.get("lon", 0)
            distance = data.get("distance", 0)
            print(f"[{now()}] GEOFENCE BREACH: {distance:.0f}m from center at {lat},{lon}")
            send_evidence(
                f"GEOFENCE BREACH\n\nDistance from center: {distance:.0f}m\n"
                f"Location: {lat}, {lon}\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"
                f"Phone has been auto-locked with $100 paywall.",
                "geofence breach"
            )
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/evidence-photo":
            photo_b64 = data.get("photo", "")
            evidence_type = data.get("type", "obedience")
            text = data.get("text", "")
            print(f"[{now()}] Evidence photo received ({evidence_type})")
            if photo_b64 and PARTNER_EMAIL:
                try:
                    photo_bytes = base64.b64decode(photo_b64)
                    msg = MIMEMultipart()
                    msg["From"] = MAIL_USER
                    msg["To"] = PARTNER_EMAIL
                    msg["Subject"] = f"Lion's Share — {evidence_type.title()} Photo Evidence"
                    body_text = (
                        f"Lion's Share — {evidence_type.title()} Photo\n\n"
                        f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                        f"Type: {evidence_type}\n"
                    )
                    if text:
                        body_text += f"\nContent:\n{text}\n"
                    body_text += "\n---\nSelfie taken automatically on task completion.\n"
                    msg.attach(MIMEText(body_text, "plain"))
                    attachment = MIMEBase("image", "jpeg")
                    attachment.set_payload(photo_bytes)
                    encoders.encode_base64(attachment)
                    attachment.add_header("Content-Disposition", "attachment",
                        filename=f"evidence_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jpg")
                    msg.attach(attachment)
                    with smtplib.SMTP(SMTP_HOST, 587) as server:
                        server.starttls()
                        server.login(MAIL_USER, MAIL_PASS)
                        server.send_message(msg)
                    print(f"[{now()}] Evidence photo email sent to {PARTNER_EMAIL}")
                except Exception as e:
                    print(f"[{now()}] Evidence photo email error: {e}")
            elif not photo_b64:
                send_evidence(text or "Photo capture failed", evidence_type)
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/verify-photo":
            photo_b64 = data.get("photo", "")
            task_text = data.get("task", "")
            print(f"[{now()}] Photo verification: {task_text[:50]}")
            result = verify_photo_with_llm(photo_b64, task_text, on_evidence=send_evidence)
            print(f"[{now()}] Verification result: {result}")
            self.respond(200, result)

        elif self.path == "/webhook/generate-task":
            category = data.get("category", "general")
            result = generate_task_with_llm(category)
            print(f"[{now()}] Generated task: {result}")
            self.respond(200, result)

        elif self.path == "/webhook/subscription-charge":
            tier = data.get("tier", "unknown")
            amount = data.get("amount", 0)
            print(f"[{now()}] Subscription charge: ${amount} ({tier})")
            send_evidence(
                f"Weekly subscription charge: ${amount} ({tier.upper()})\n"
                f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"This amount has been added to the paywall.",
                "subscription charge"
            )
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/bunny-message":
            text = data.get("text", "")
            msg_type = data.get("type", "message")
            print(f"[{now()}] Bunny message ({msg_type}): {text}")
            if msg_type == "self-lock":
                send_evidence(f"Bunny self-locked: {text}", "self-lock")
            else:
                send_evidence(f"Message from bunny: {text}", "bunny message")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/desktop-penalty":
            amount = data.get("amount", 30)
            reason = data.get("reason", "Desktop penalty")
            print(f"[{now()}] DESKTOP PENALTY: ${amount} — {reason}")
            pw_str = adb.get("focus_lock_paywall")
            pw = 0
            try:
                pw = int(pw_str) if pw_str and pw_str != "null" else 0
            except:
                pass
            pw += amount
            adb.put("focus_lock_paywall", str(pw))
            adb.put_str("focus_lock_message", f"{reason}. ${amount} added.")
            send_evidence(f"{reason}: ${amount} penalty applied. New paywall: ${pw}", "desktop penalty")
            self.respond(200, {"ok": True, "new_paywall": pw})

        # ── Admin API (enforcement infrastructure) ──
        # Without mesh_id: operates on operator's mesh (backwards compat).
        # With mesh_id on a vault_only mesh that isn't the operator's: refused.
        elif self.path == "/admin/order":
            if not ADMIN_TOKEN:
                self.respond(503, {"error": "admin_token not configured"})
                return
            token = data.get("admin_token", "")
            if not _is_valid_admin_auth(token):
                self.respond(403, {"error": "invalid admin_token"})
                return
            req_mesh_id = data.get("mesh_id", "")
            action = data.get("action", "")

            # set-vault-only is a mesh-account-level operation, not an order.
            if action == "set-vault-only":
                target = req_mesh_id or OPERATOR_MESH_ID
                value = bool(data.get("params", {}).get("enabled", True))
                ok = _mesh_accounts.set_vault_only(target, value)
                self.respond(200 if ok else 404, {
                    "ok": ok,
                    "mesh_id": target,
                    "vault_only": value,
                })
                return

            if req_mesh_id and req_mesh_id != OPERATOR_MESH_ID:
                if _mesh_accounts.is_vault_only(req_mesh_id):
                    self.respond(403, {"error": "vault_only mesh — admin plaintext orders refused"})
                    return
            result = mesh.handle_mesh_order(
                data, mesh_orders, mesh_peers, MESH_NODE_ID,
                apply_fn=mesh_apply_order,
                lion_pubkey=get_lion_pubkey(),
                on_orders_applied=on_mesh_orders_applied,
                ntfy_fn=None,  # Don't fire ntfy here — vault blob isn't written yet
            )
            # Write vault blob FIRST, then fire ntfy so the slave sees
            # the new blob when it wakes up (fixes ntfy race condition).
            action = data.get("action", "")
            params = data.get("params", {})
            if action:
                try:
                    _admin_order_to_vault_blob(action, params, req_mesh_id or OPERATOR_MESH_ID)
                except Exception as e:
                    print(f"[admin] vault blob write failed: {e}")
            if ntfy_fn:
                try: ntfy_fn(mesh_orders.version)
                except Exception: pass
            self.respond(200, result)

        # ── Account-Based Mesh API ──
        elif self.path == "/api/mesh/create":
            lion_pubkey = data.get("lion_pubkey", "")
            if not lion_pubkey:
                self.respond(400, {"error": "lion_pubkey required"})
                return
            client_ip = self.client_address[0] if self.client_address else ""
            if not _mesh_accounts.check_rate_limit(client_ip):
                self.respond(429, {"error": "rate limit exceeded — max 3 meshes per hour"})
                return
            account = _mesh_accounts.create(lion_pubkey, client_ip=client_ip)
            new_mesh_id = account["mesh_id"]
            # Create a per-mesh OrdersDocument (isolated from operator's mesh)
            new_orders = _orders_registry.get_or_create(new_mesh_id)
            new_orders.set("pin", account["pin"])
            new_orders.bump_version()
            # If this is the first mesh and no operator mesh exists, adopt it
            global _lion_pubkey
            if lion_pubkey and not _lion_pubkey:
                _lion_pubkey = lion_pubkey
            print(f"[{now()}] Mesh created: {new_mesh_id} invite={account['invite_code']}")
            self.respond(200, {
                "mesh_id": new_mesh_id,
                "invite_code": account["invite_code"],
                "auth_token": account["auth_token"],
                "pin": account["pin"],
            })

        elif self.path == "/api/mesh/join":
            invite_code = data.get("invite_code", "")
            node_id = data.get("node_id", "")
            node_type = data.get("node_type", "phone")
            bunny_pubkey = data.get("bunny_pubkey", "")
            if not invite_code:
                self.respond(400, {"error": "invite_code required"})
                return
            if not node_id:
                self.respond(400, {"error": "node_id required"})
                return
            account, err = _mesh_accounts.join(invite_code, node_id, node_type, bunny_pubkey)
            if err:
                self.respond(404, {"error": err})
                return
            # Register as a mesh peer so gossip reaches them
            addrs = data.get("addresses", [])
            port = data.get("port", 8432 if node_type == "phone" else 8435)
            if addrs:
                mesh_peers.update_peer(node_id, node_type=node_type,
                                       addresses=addrs, port=port)
            print(f"[{now()}] Node joined mesh: {node_id} ({node_type}) mesh={account['mesh_id']}")
            self.respond(200, {
                "ok": True,
                "mesh_id": account["mesh_id"],
                "lion_pubkey": account.get("lion_pubkey", ""),
                "pin": account["pin"],
            })

        # ── Vault endpoints (zero-knowledge mesh) ──
        # See docs/VAULT-DESIGN.md.
        elif self.path.startswith("/vault/"):
            if not VAULT_MODE_ALLOWED:
                self.respond(404, {"error": "vault mode disabled"})
                return
            parts = self.path.strip("/").split("/")
            # Expected: ["vault", "{mesh_id}", "{action}"]
            if len(parts) < 3:
                self.respond(400, {"error": "bad vault path"})
                return
            mesh_id = parts[1]
            action = parts[2]
            if not _safe_mesh_id(mesh_id):
                self.respond(400, {"error": "invalid mesh_id"})
                return
            account, lion_pubkey = _vault_resolve_mesh(mesh_id)
            if not account:
                self.respond(404, {"error": "mesh not found"})
                return

            if action == "append":
                # Two-writer vault blob append (Phase D).
                # Order blobs are signed by Lion. Runtime blobs are signed by a
                # registered slave node. Server verifies whichever signs and
                # stores the encrypted blob opaquely — recipients decide trust
                # by inspecting which key signed once they decrypt.
                blob = data
                if not isinstance(blob, dict):
                    self.respond(400, {"error": "blob must be object"})
                    return
                if blob.get("mesh_id") != mesh_id:
                    self.respond(400, {"error": "blob.mesh_id mismatch"})
                    return
                # Per-mesh quota check (skip for operator's mesh)
                if mesh_id != OPERATOR_MESH_ID:
                    max_bytes = account.get("max_total_bytes_mb", 100) * 1024 * 1024
                    store_size = _vault_store.total_bytes(mesh_id)
                    if store_size >= max_bytes:
                        self.respond(429, {"error": f"vault quota exceeded ({max_bytes // (1024*1024)}MB)"})
                        return
                    max_daily = account.get("max_blobs_per_day", 5000)
                    if _daily_blob_count(mesh_id) >= max_daily:
                        self.respond(429, {"error": f"daily blob limit reached ({max_daily}/day)"})
                        return
                if not lion_pubkey:
                    self.respond(403, {"error": "no lion_pubkey on file for this mesh"})
                    return
                registered_nodes = _vault_store.get_nodes(mesh_id)
                writer_role, writer_id = _verify_blob_two_writer(
                    blob, lion_pubkey, registered_nodes)
                if writer_role is None:
                    self.respond(403, {"error": "invalid signature"})
                    return
                version, err = _vault_store.append(mesh_id, blob)
                if err:
                    self.respond(409, {"error": err, "current_version": version})
                    return
                _daily_blob_increment(mesh_id)
                print(f"[{now()}] Vault append: mesh={mesh_id} v={version} "
                      f"writer={writer_role}:{writer_id} "
                      f"slots={len(blob.get('slots', {}))} ct_bytes={len(blob.get('ciphertext', ''))}")
                self.respond(200, {"ok": True, "version": version})

            elif action == "register-node":
                # Lion-signed node registration → moves directly into the active node list
                if not lion_pubkey:
                    self.respond(403, {"error": "no lion_pubkey on file for this mesh"})
                    return
                if not _verify_signed_payload(data, data.get("signature", ""), lion_pubkey):
                    self.respond(403, {"error": "invalid signature"})
                    return
                node_id = data.get("node_id", "")
                node_type = data.get("node_type", "unknown")
                node_pubkey = data.get("node_pubkey", "")
                if not node_id or not node_pubkey:
                    self.respond(400, {"error": "node_id and node_pubkey required"})
                    return
                _vault_store.add_node(mesh_id, {
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_pubkey": node_pubkey,
                    "registered_at": int(time.time()),
                })
                _vault_store.remove_pending_node(mesh_id, node_id)
                # Lion explicitly approving a previously rejected key clears the rejection
                _vault_store.clear_rejection(mesh_id, node_pubkey)
                print(f"[{now()}] Vault register-node: mesh={mesh_id} node={node_id} ({node_type})")
                self.respond(200, {"ok": True})

            elif action == "reject-node-request":
                # Lion-signed rejection. Drops the pending entry and adds the
                # pubkey hash to a deny list so the slave's hourly retry doesn't
                # keep refilling the queue. The slave can recover by regenerating
                # its keypair (which produces a new rejection key).
                if not lion_pubkey:
                    self.respond(403, {"error": "no lion_pubkey on file for this mesh"})
                    return
                if not _verify_signed_payload(data, data.get("signature", ""), lion_pubkey):
                    self.respond(403, {"error": "invalid signature"})
                    return
                node_id = data.get("node_id", "")
                node_pubkey = data.get("node_pubkey", "")
                reason = data.get("reason", "")
                if not node_pubkey:
                    self.respond(400, {"error": "node_pubkey required"})
                    return
                _vault_store.add_rejected_node(mesh_id, node_id, node_pubkey, reason)
                if node_id:
                    _vault_store.remove_pending_node(mesh_id, node_id)
                print(f"[{now()}] Vault reject-node-request: mesh={mesh_id} node={node_id} reason={reason!r}")
                self.respond(200, {"ok": True})

            elif action == "register-node-request":
                # Slave-initiated, unsigned. Goes into pending queue for Lion approval.
                # Auto-approves if node_id matches an already-approved node (key rotation).
                node_id = data.get("node_id", "")
                node_type = data.get("node_type", "unknown")
                node_pubkey = data.get("node_pubkey", "")
                if not node_id or not node_pubkey:
                    self.respond(400, {"error": "node_id and node_pubkey required"})
                    return
                if _vault_store.is_rejected(mesh_id, node_pubkey):
                    print(f"[{now()}] Vault register-node-request DENIED (rejected): mesh={mesh_id} node={node_id}")
                    self.respond(403, {"error": "node rejected"})
                    return
                # Security: key rotation (same node_id, new pubkey) goes to pending
                # queue like any new node. Lion must approve. Auto-approve was removed
                # because it allowed unauthenticated pubkey replacement — an attacker
                # who knew a mesh_id + node_id could replace any node's key.
                _vault_store.add_pending_node(mesh_id, {
                    "node_id": node_id,
                    "node_type": node_type,
                    "node_pubkey": node_pubkey,
                    "requested_at": int(time.time()),
                })
                print(f"[{now()}] Vault register-node-request (pending): mesh={mesh_id} node={node_id}")
                self.respond(200, {"ok": True, "status": "pending"})

            else:
                self.respond(404, {"error": f"unknown vault action: {action}"})

        elif self.path == "/webhook/controller-register":
            ts_ip = data.get("tailscale_ip", "")
            if ts_ip:
                # Store controller address for release script queries
                reg_file = "/run/focuslock/controller.json"
                try:
                    reg = {"tailscale_ip": ts_ip, "last_seen": time.time(),
                           "last_seen_str": datetime.now().isoformat()}
                    os.makedirs(os.path.dirname(reg_file), exist_ok=True)
                    with open(reg_file + ".tmp", "w") as f:
                        json.dump(reg, f)
                    os.replace(reg_file + ".tmp", reg_file)
                    # Also update mesh peers so other nodes know about the controller
                    mesh_peers.update_peer("lions-share", node_type="controller",
                                           addresses=[ts_ip], port=0)
                except Exception as e:
                    print(f"[{now()}] Controller register error: {e}")
            self.respond(200, {"ok": True})

        elif self.path == "/api/pair/register":
            # Bunny registers for relay-based pairing
            passphrase = data.get("passphrase", "").strip()
            bunny_pubkey = data.get("pubkey", data.get("bunny_pubkey", ""))
            node_id = data.get("node_id", "")
            if not passphrase:
                self.respond(400, {"error": "passphrase required"})
                return
            _pairing_registry.register(passphrase, bunny_pubkey, node_id)
            print(f"[{now()}] Pair register: {passphrase.upper()} node={node_id}")
            self.respond(200, {"ok": True, "passphrase": passphrase.upper()})

        elif self.path == "/api/pair/claim":
            # Lion claims a pairing by passphrase
            passphrase = data.get("passphrase", "").strip()
            lion_pubkey = data.get("lion_pubkey", "")
            lion_node_id = data.get("lion_node_id", "")
            if not passphrase or not lion_pubkey:
                self.respond(400, {"error": "passphrase and lion_pubkey required"})
                return
            entry = _pairing_registry.claim(passphrase, lion_pubkey, lion_node_id)
            if not entry:
                self.respond(404, {"error": "passphrase not found or expired"})
                return
            print(f"[{now()}] Pair claimed: {passphrase.upper()} by {lion_node_id}")
            self.respond(200, {"ok": True, "paired": True, "bunny_pubkey": entry.get("bunny_pubkey", "")})

        elif self.path == "/api/pair/lookup":
            # Backward compat — redirect to status
            passphrase = data.get("passphrase", "").strip()
            entry = _pairing_registry.status(passphrase)
            if entry:
                self.respond(200, {"ip": "", "port": 0, "pubkey": entry.get("bunny_pubkey", ""), "paired": entry.get("paired", False), "lion_pubkey": entry.get("lion_pubkey") or ""})
            else:
                self.respond(404, {"error": "not found"})

        elif self.path == "/api/pair/create":
            # Lion's Share creates a pairing code for desktop enrollment
            token = data.get("admin_token", "") or data.get("auth_token", "")
            if not _is_valid_admin_auth(token):
                self.respond(403, {"error": "invalid admin_token"})
                return
            import string, re
            code = data.get("code", "").upper().strip()
            if not code:
                chars = string.ascii_uppercase + string.digits
                code = "".join(secrets.choice(chars) for _ in range(6))
            # SECURITY: pairing code must be alphanumeric only (used as filename)
            if not re.match(r'^[A-Z0-9]{4,12}$', code):
                self.respond(400, {"error": "invalid code format (4-12 alphanumeric)"})
                return
            expires_min = data.get("expires_minutes", 60)
            # Build config payload
            local_addrs = mesh.get_local_addresses()
            homelab_ip = local_addrs[0] if local_addrs else "127.0.0.1"
            config = {
                "homelab_url": f"http://{homelab_ip}:{WEBHOOK_PORT}",
                "mesh_pin": str(mesh_orders.get("pin", "")),
                "pubkey_pem": get_lion_pubkey() or "",
                "mesh_port": _cfg.get("mesh_port", 8435),
            }
            pair_dir = "/opt/focuslock/pairing-codes"
            os.makedirs(pair_dir, exist_ok=True)
            pair_file = os.path.join(pair_dir, f"{code}.json")
            with open(pair_file, "w") as f:
                json.dump({"config": config, "expires_at": time.time() + expires_min * 60}, f)
            pair_url = f"http://{homelab_ip}:{WEBHOOK_PORT}/api/pair/{code}"
            print(f"[{now()}] Pairing code created: {code} (expires {expires_min}min)")
            self.respond(200, {"ok": True, "code": code, "url": pair_url,
                               "expires_minutes": expires_min})

        elif self.path in ("/api/web-session", "/admin/web-session"):
            action = data.get("action", "create")
            if action == "create":
                # Create ephemeral web session for QR code login
                now_ts = time.time()
                expired = [k for k, v in _web_sessions.items() if now_ts - v["created_at"] > _WEB_SESSION_TTL]
                for k in expired:
                    del _web_sessions[k]
                session_id = secrets.token_urlsafe(16)
                _web_sessions[session_id] = {
                    "approved": False,
                    "created_at": now_ts,
                }
                scheme = "https" if self.headers.get("X-Forwarded-Proto") == "https" else "http"
                host = self.headers.get("Host", "focus.wildhome.ca")
                qr_url = f"{scheme}://{host}/web-login?s={session_id}"
                self.respond(200, {"session_id": session_id, "qr_url": qr_url})
            elif action == "approve":
                # Lion's Share app signs the session_id with Lion's RSA private key
                session_id = data.get("session_id", "")
                signature = data.get("signature", "")
                session = _web_sessions.get(session_id)
                if not session or time.time() - session["created_at"] > _WEB_SESSION_TTL:
                    self.respond(404, {"error": "session expired or not found"})
                    return
                if session["approved"]:
                    self.respond(200, {"ok": True, "status": "already_approved"})
                    return
                # Verify Lion's RSA signature on the session_id
                lion_pub = get_lion_pubkey()
                if not lion_pub:
                    # Try mesh account lion_pubkey for operator mesh
                    if OPERATOR_MESH_ID:
                        acct = _mesh_accounts.meshes.get(OPERATOR_MESH_ID)
                        if acct:
                            lion_pub = acct.get("lion_pubkey", "")
                if not lion_pub:
                    self.respond(500, {"error": "no lion pubkey configured"})
                    return
                try:
                    verified = mesh.verify_signature(
                        {"session_id": session_id}, signature, lion_pub)
                except Exception:
                    verified = False
                if not verified:
                    print(f"[{now()}] Web session approve DENIED (bad signature): {session_id[:8]}...")
                    self.respond(403, {"error": "invalid signature — must be signed by Lion's private key"})
                    return
                session["approved"] = True
                print(f"[{now()}] Web session approved (signature verified): {session_id[:8]}...")
                self.respond(200, {"ok": True, "status": "approved"})
            else:
                self.respond(400, {"error": "unknown action"})

        elif self.path == "/webhook/desktop-heartbeat":
            hostname = data.get("hostname", "unknown")
            print(f"[{now()}] Desktop heartbeat: {hostname}")
            try:
                registry = {}
                if os.path.exists(DESKTOP_REGISTRY_FILE):
                    with open(DESKTOP_REGISTRY_FILE, "r") as f:
                        registry = json.load(f)
                registry[hostname] = {
                    "last_seen": datetime.now().isoformat(),
                    "last_seen_ts": time.time(),
                    "warned": registry.get(hostname, {}).get("warned", False),
                    "last_penalty_ts": registry.get(hostname, {}).get("last_penalty_ts", 0),
                    "name": registry.get(hostname, {}).get("name", hostname),
                }
                tmp = DESKTOP_REGISTRY_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(registry, f, indent=2)
                os.rename(tmp, DESKTOP_REGISTRY_FILE)
                # Push desktop info to phone so Lion's Share can see it
                # Format: hostname:name:online;hostname:name:online
                import re as _re
                parts = []
                for k, v in registry.items():
                    safe_k = _re.sub(r'[^a-zA-Z0-9._-]', '', k)
                    name = _re.sub(r'[^a-zA-Z0-9._\- ]', '', v.get("name", k))
                    online = "1" if (time.time() - v.get("last_seen_ts", 0)) < 60 else "0"
                    parts.append(f"{safe_k}:{name}:{online}")
                desktop_summary = ";".join(parts)
                for dev in adb.devices:
                    subprocess.run(
                        ['adb', '-s', dev, 'shell', 'settings', 'put', 'global',
                         'focus_lock_desktops', desktop_summary],
                        timeout=10, capture_output=True
                    )
            except Exception as e:
                print(f"[{now()}] Desktop heartbeat registry error: {e}")
            self.respond(200, {"ok": True})

        elif self.path == "/webhook/register":
            # Phone reports its current IPs
            lan_ip = data.get("lan_ip", "")
            tailscale_ip = data.get("tailscale_ip", "")
            device_id = data.get("device_id", "unknown")
            print(f"[{now()}] Phone registered: LAN={lan_ip} TS={tailscale_ip} device={device_id}")
            try:
                registry = {}
                if os.path.exists(IP_REGISTRY_FILE):
                    with open(IP_REGISTRY_FILE, "r") as f:
                        registry = json.load(f)
                registry[device_id] = {
                    "lan_ip": lan_ip,
                    "tailscale_ip": tailscale_ip,
                    "last_seen": datetime.now().isoformat(),
                }
                tmp = IP_REGISTRY_FILE + ".tmp"
                with open(tmp, "w") as f:
                    json.dump(registry, f, indent=2)
                os.rename(tmp, IP_REGISTRY_FILE)
            except Exception as e:
                print(f"[{now()}] Registry write error: {e}")
            self.respond(200, {"ok": True})

        else:
            self.respond(404, {"error": "not found"})

    def do_GET(self):
        # ── Mesh GET Endpoints ──
        if self.path == "/mesh/ping":
            self.respond(200, mesh.handle_mesh_ping(MESH_NODE_ID, mesh_orders))
            return

        # ── Audit transparency: /version (P3) ──
        # Public, unauthenticated. Lets anyone verify which build is running
        # without needing the mesh PIN or an auth token. Returns the service
        # version string, the sha256 of this file, the deploy-injected git
        # commit (if any), and minimal vault-mode counters so the trust page
        # can show "X meshes are currently in vault_only mode."
        elif self.path == "/version":
            try:
                vault_only_count = sum(
                    1 for mid in _mesh_accounts.list_mesh_ids()
                    if _mesh_accounts.is_vault_only(mid)
                )
            except Exception:
                vault_only_count = None
            self.respond(200, {
                "service": "focuslock-mail",
                "version": __version__,
                "source_sha256": SOURCE_SHA256,
                "git_commit": DEPLOY_GIT_COMMIT,
                "vault_mode_allowed": VAULT_MODE_ALLOWED,
                "vault_only_meshes": vault_only_count,
                "uptime_s": int(time.time() - SERVICE_START_TIME),
            })
            return

        # ── Web Session QR Login ──
        elif self.path.startswith("/api/web-session/") or self.path.startswith("/admin/web-session/"):
            # Poll session status: GET /api/web-session/<session_id>
            session_id = self.path.split("/")[-1]
            session = _web_sessions.get(session_id)
            if not session or time.time() - session["created_at"] > _WEB_SESSION_TTL:
                self.respond(404, {"error": "session expired or not found"})
                return
            if session["approved"]:
                # P0 fix: issue a scoped session token instead of the master
                # ADMIN_TOKEN. The session token expires after 8 hours and can
                # be revoked server-side without rotating the real admin_token.
                scoped_token = _issue_session_token(session_id)
                self.respond(200, {
                    "approved": True,
                    "session_token": scoped_token,
                    "expires_in": _SESSION_TOKEN_TTL,
                })
                # One-time use: delete after successful retrieval
                del _web_sessions[session_id]
            else:
                self.respond(200, {"approved": False})
            return

        elif self.path.startswith("/web-login"):
            # Info page shown when QR URL is opened in a browser.
            # This does NOT approve the session — approval requires Lion's RSA signature
            # via POST /admin/web-session {action: "approve", session_id, signature}.
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            session_id = params.get("s", [""])[0]
            session = _web_sessions.get(session_id)
            if not session or time.time() - session["created_at"] > _WEB_SESSION_TTL:
                msg = b"<h2>Session expired</h2><p>Refresh the web UI for a new QR code.</p>"
            elif session["approved"]:
                msg = b"<h2>Already approved</h2><p>You can close this tab.</p>"
            else:
                msg = (b"<h2>Use Lion's Share to approve</h2>"
                       b"<p>Open the <b>Lion's Share</b> app and tap <b>Web Remote</b> in the Advanced tab, "
                       b"then scan this QR code.</p>"
                       b"<p style='margin-top:1rem;font-size:0.85rem;color:#888'>Only the Lion's private key can approve web sessions.</p>")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("X-Frame-Options", "DENY")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(
                b"<html><body style='background:#0a0a14;color:#b8860b;font-family:sans-serif;text-align:center;padding:4rem'>"
                + msg + b"</body></html>")
            return

        # ── Admin GET endpoints (enforcement) ──
        # Without mesh_id: full plaintext for operator's mesh (backwards compat).
        # With mesh_id on a non-operator vault_only mesh: metadata only.
        elif self.path.startswith("/admin/status"):
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            params = urllib.parse.parse_qs(parsed.query)
            token = params.get("admin_token", [""])[0]
            if not ADMIN_TOKEN:
                self.respond(503, {"error": "admin_token not configured"})
                return
            if not _is_valid_admin_auth(token):
                self.respond(403, {"error": "invalid admin_token"})
                return
            req_mesh_id = params.get("mesh_id", [""])[0]
            if req_mesh_id and req_mesh_id != OPERATOR_MESH_ID and _mesh_accounts.is_vault_only(req_mesh_id):
                # Vault-only non-operator mesh: metadata only, no plaintext orders
                _morders = _resolve_orders(req_mesh_id)
                self.respond(200, {
                    "orders_version": _morders.version,
                    "vault_only": True,
                    "uptime_s": int(time.time() - SERVICE_START_TIME),
                    "nodes": len(mesh_peers.peers),
                })
                return
            _morders = _resolve_orders(req_mesh_id) if req_mesh_id else mesh_orders
            self.respond(200, mesh.handle_mesh_status(
                _morders, mesh_peers, MESH_NODE_ID, mesh_local_status()))
            return

        # ── Vault GET endpoints (zero-knowledge mesh) ──
        elif self.path.startswith("/vault/"):
            if not VAULT_MODE_ALLOWED:
                self.respond(404, {"error": "vault mode disabled"})
                return
            import urllib.parse
            parsed = urllib.parse.urlparse(self.path)
            parts = parsed.path.strip("/").split("/")
            # Expected forms:
            #   /vault/{mesh_id}/since/{version}
            #   /vault/{mesh_id}/nodes
            #   /vault/{mesh_id}/nodes-pending
            if len(parts) < 3:
                self.respond(400, {"error": "bad vault path"})
                return
            mesh_id = parts[1]
            action = parts[2]
            if not _safe_mesh_id(mesh_id):
                self.respond(400, {"error": "invalid mesh_id"})
                return
            account, lion_pubkey = _vault_resolve_mesh(mesh_id)
            if not account:
                self.respond(404, {"error": "mesh not found"})
                return

            if action == "since":
                # /vault/{mesh_id}/since/{version}
                if len(parts) < 4:
                    self.respond(400, {"error": "version required"})
                    return
                try:
                    version = int(parts[3])
                except ValueError:
                    self.respond(400, {"error": "version must be int"})
                    return
                blobs, current = _vault_store.since(mesh_id, version)
                self.respond(200, {
                    "current_version": current,
                    "blobs": blobs,
                })

            elif action == "nodes":
                self.respond(200, {"nodes": _vault_store.get_nodes(mesh_id)})

            elif action == "nodes-pending":
                # Lion polls for pending registrations. Requires auth_token.
                qparams = urllib.parse.parse_qs(parsed.query)
                auth_token = qparams.get("auth_token", [""])[0]
                if not auth_token:
                    auth_header = self.headers.get("Authorization", "")
                    if auth_header.startswith("Bearer "):
                        auth_token = auth_header[7:]
                if not _mesh_accounts.validate_auth(mesh_id, auth_token):
                    self.respond(403, {"error": "invalid auth"})
                    return
                self.respond(200, {"pending": _vault_store.get_pending_nodes(mesh_id)})

            else:
                self.respond(404, {"error": f"unknown vault action: {action}"})
            return

        elif self.path == "/controller":
            # Return Lion's Share controller's last known address
            reg_file = "/run/focuslock/controller.json"
            try:
                if os.path.exists(reg_file):
                    with open(reg_file, "r") as f:
                        self.respond(200, json.load(f))
                else:
                    self.respond(404, {"error": "no controller registered yet"})
            except Exception as e:
                print(f"[{now()}] /controller error: {e}")
                self.respond(500, {"error": "internal error"})

        elif self.path == "/standing-orders":
            # Serve CLAUDE.md for desktop collar memory sync.
            # SECURITY: scrub the admin_token from the served content — clients
            # get the rules text for Claude Code enforcement, but the actual
            # token must be installed separately (via initial setup, not sync).
            try:
                claude_md = os.path.expanduser("~/.claude/CLAUDE.md")
                if os.path.exists(claude_md):
                    with open(claude_md, "r") as f:
                        content = f.read()
                    if ADMIN_TOKEN and ADMIN_TOKEN in content:
                        content = content.replace(ADMIN_TOKEN, "<REDACTED>")
                    self.send_response(200)
                    self.send_header("Content-Type", "text/plain")
                    self.end_headers()
                    self.wfile.write(content.encode())
                else:
                    self.respond(404, {"error": "no standing orders found"})
            except Exception as e:
                print(f"[{now()}] /standing-orders error: {e}")
                self.respond(500, {"error": "internal error"})

        elif self.path == "/settings":
            # Serve settings.json (enforcement hooks) for sync.
            # SECURITY: scrub the admin_token from the served content.
            try:
                settings = os.path.expanduser("~/.claude/settings.json")
                if os.path.exists(settings):
                    with open(settings, "r") as f:
                        content = f.read()
                    if ADMIN_TOKEN and ADMIN_TOKEN in content:
                        content = content.replace(ADMIN_TOKEN, "<REDACTED>")
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(content.encode())
                else:
                    self.respond(404, {"error": "no settings found"})
            except Exception as e:
                print(f"[{now()}] /settings error: {e}")
                self.respond(500, {"error": "internal error"})

        elif self.path == "/pubkey":
            # Serve Lion's RSA public key for mesh signature verification
            pk = get_lion_pubkey()
            if pk:
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
                self.end_headers()
                self.wfile.write(pk.encode())
            else:
                self.respond(404, {"error": "no lion pubkey available — check focus_lock_lion_pubkey on phone"})

        elif self.path.startswith("/api/pair/status/"):
            # Poll pairing status by passphrase
            passphrase = self.path.split("/")[-1].strip().upper()
            entry = _pairing_registry.status(passphrase)
            if not entry:
                self.respond(404, {"error": "not found"})
                return
            self.respond(200, {
                "paired": entry.get("paired", False),
                "bunny_pubkey": entry.get("bunny_pubkey", ""),
                "lion_pubkey": entry.get("lion_pubkey") or "",
            })

        elif self.path.startswith("/api/pair/"):
            # Desktop pairing — look up a 6-char code
            import re
            code = self.path.split("/")[-1].upper().strip()
            # SECURITY: pairing code must be alphanumeric only (used as filename)
            if not re.match(r'^[A-Z0-9]{4,12}$', code):
                self.respond(400, {"error": "invalid pairing code"})
                return
            pair_dir = "/opt/focuslock/pairing-codes"
            pair_file = os.path.join(pair_dir, f"{code}.json")
            if os.path.exists(pair_file):
                try:
                    with open(pair_file, "r") as f:
                        pair_data = json.load(f)
                    if pair_data.get("expires_at", 0) > time.time():
                        self.respond(200, pair_data.get("config", {}))
                    else:
                        os.remove(pair_file)
                        self.respond(410, {"error": "pairing code expired"})
                except Exception as e:
                    print(f"[{now()}] /api/pair/{code} error: {e}")
                    self.respond(500, {"error": "internal error"})
            else:
                self.respond(404, {"error": "invalid pairing code"})

        elif self.path == "/api/paywall" or self.path == "/api/paywall?raw":
            # Lightweight paywall check — no PIN needed (value is visible on lock screen)
            # Used by Claude Code PreToolUse hook via mesh URL
            pw = str(mesh_orders.get("paywall", "0"))
            if pw in ("null", "None", ""):
                pw = "0"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(pw.encode())

        elif self.path == "/memory":
            # Memory bundle endpoint for sync-standing-orders.sh.
            #
            # Resolution order: $MEMORY_DIR env var → ~/.claude/enforcement-memory.
            # The env var override exists because the systemd unit on pegasus sets
            # HOME=/opt/focuslock for sandboxing, which would otherwise resolve
            # the tilde to a non-existent dir under /opt and silently 404 the
            # endpoint (forcing sync clients onto the slower rsync fallback).
            mem_dir = os.environ.get("MEMORY_DIR") or os.path.expanduser("~/.claude/enforcement-memory")
            if os.path.isdir(mem_dir):
                import hashlib
                bundle = {}
                for f in sorted(os.listdir(mem_dir)):
                    if f.endswith(".md"):
                        with open(os.path.join(mem_dir, f)) as fh:
                            bundle[f] = fh.read()
                content = json.dumps(bundle).encode()
                bundle["__hash__"] = hashlib.md5(content).hexdigest()
                self.respond(200, bundle)
            else:
                self.respond(404, {"error": "no memory dir", "checked": mem_dir})

        elif self.path in ("/", "/index.html", "/signup", "/signup.html",
                           "/cost", "/cost.html", "/trust", "/trust.html"):
            # Serve web UI (index, signup, cost, or trust)
            web_dir = "/opt/focuslock/web"
            if self.path.startswith("/signup"):
                fname = "signup.html"
            elif self.path.startswith("/cost"):
                fname = "cost.html"
            elif self.path.startswith("/trust"):
                fname = "trust.html"
            else:
                fname = "index.html"
            page = os.path.join(web_dir, fname)
            if os.path.exists(page):
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                with open(page, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.respond(404, {"error": "web UI not deployed"})

        elif self.path == "/qrcode.min.js":
            web_dir = "/opt/focuslock/web"
            js_path = os.path.join(web_dir, "qrcode.min.js")
            if os.path.exists(js_path):
                self.send_response(200)
                self.send_header("Content-Type", "application/javascript; charset=utf-8")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(js_path, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.respond(404, {"error": "qrcode.min.js not deployed"})

        elif self.path == "/manifest.json":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({
                "name": "Lion's Share",
                "short_name": "Lion's Share",
                "start_url": "/",
                "display": "standalone",
                "background_color": "#0a0a14",
                "theme_color": "#0a0a14",
                "icons": [{"src": "/collar-icon.png", "sizes": "512x512", "type": "image/png"}]
            }).encode())

        elif self.path == "/collar-icon.png":
            icon = "/opt/focuslock/collar-icon.png"
            if os.path.exists(icon):
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "public, max-age=86400")
                self.end_headers()
                with open(icon, "rb") as f:
                    self.wfile.write(f.read())
            else:
                self.send_response(404)
                self.end_headers()

        else:
            self.respond(404, {"error": "not found"})

    def respond(self, code, data):
        # SECURITY: omit CORS * on /admin/* paths. Admin endpoints must not
        # be callable from arbitrary third-party origins in a browser context.
        # Other endpoints keep CORS * for the mesh/vault use case (phones
        # and desktops making cross-origin requests via fetch()).
        is_admin = self.path.startswith("/admin/") or self.path.startswith("/api/web-session")
        self.respond_json(code, data, cors=not is_admin)

    def log_message(self, format, *args):
        print(f"[{now()}] Webhook: {args[0]}")


def now():
    return datetime.now().strftime("%H:%M:%S")


# ── Main ──

if __name__ == "__main__":
    print(f"FocusLock Mail Service")
    print(f"  IMAP: {MAIL_USER}@{IMAP_HOST} (check every {IMAP_CHECK_INTERVAL}s)")
    print(f"  SMTP: {SMTP_HOST}")
    print(f"  Partner: {PARTNER_EMAIL}")
    print(f"  Phone: {PHONE_URL}")
    print(f"  Webhook: port {WEBHOOK_PORT}")

    # Initialize mesh — bootstrap from ADB if no persisted state
    seed_mesh_peers()
    init_mesh_from_adb()
    print(f"  Mesh: node={MESH_NODE_ID} v{mesh_orders.version} peers={len(mesh_peers.peers)}")

    # Start mesh gossip thread (10s interval)
    gossip = mesh.GossipThread(
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
    print(f"  LAN discovery started (UDP beacon on :{mesh.LAN_DISCOVERY_PORT})")

    # Start IMAP checker in background
    imap_thread = threading.Thread(
        target=check_payment_emails,
        kwargs={
            "imap_host": IMAP_HOST, "mail_user": MAIL_USER, "mail_pass": MAIL_PASS,
            "check_interval": IMAP_CHECK_INTERVAL, "adb": adb,
            "mesh_orders": mesh_orders, "payment_ledger": payment_ledger,
            "providers": DEFAULT_PAYMENT_PROVIDERS, "iso_codes": _ISO_CODES,
            "min_payment": MIN_PAYMENT, "max_payment": MAX_PAYMENT,
            "phone_url": PHONE_URL, "phone_pin": str(_cfg.get("pin", "")),
        },
        daemon=True,
    )
    imap_thread.start()

    # Start desktop dead-man's switch checker
    desktop_thread = threading.Thread(target=check_desktop_heartbeats, daemon=True)
    desktop_thread.start()

    # Start tribute/fine enforcement checker
    tribute_thread = threading.Thread(target=check_tributes_and_fines, daemon=True)
    tribute_thread.start()

    # Start webhook server
    server = HTTPServer(("0.0.0.0", WEBHOOK_PORT), WebhookHandler)
    print(f"[{now()}] Listening on port {WEBHOOK_PORT}")
    server.serve_forever()

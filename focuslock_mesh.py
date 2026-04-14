#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Mesh — P2P enforcement mesh protocol.

Every slave device (phone, desktop, homelab) is a mesh node that stores,
serves, and propagates Lion's orders. RSA-signed, version-numbered,
gossip-replicated. Any node Lion's Share can reach is sufficient.

Shared between server (focuslock-mail.py) and desktop collar (focuslock-desktop.py).
"""

import base64
import json
import logging
import os
import socket
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)

# On Windows, subprocess calls need CREATE_NO_WINDOW to avoid console flashes
_SUBPROCESS_FLAGS = {}
if sys.platform == "win32":
    _SUBPROCESS_FLAGS["creationflags"] = 0x08000000  # CREATE_NO_WINDOW

# ── RSA Signature Verification ──

try:
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding as asym_padding

    HAS_CRYPTO = True
except ImportError:
    HAS_CRYPTO = False


def canonical_json(orders: dict) -> bytes:
    """Deterministic JSON for consistent hashing — sorted keys, no whitespace."""
    return json.dumps(orders, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def verify_signature(orders: dict, signature_b64: str, pubkey_pem: str) -> bool:
    """Verify RSA-SHA256 signature on an orders document."""
    if not HAS_CRYPTO:
        logger.warning("cryptography library unavailable — rejecting signed orders")
        return False
    if not pubkey_pem or not signature_b64:
        return False
    try:
        pubkey = serialization.load_pem_public_key(pubkey_pem.encode("utf-8"))
        sig = base64.b64decode(signature_b64)
        data = canonical_json(orders)
        pubkey.verify(sig, data, asym_padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception as e:
        logger.warning("Signature verification failed: %s", e)
        return False


def sign_orders(orders: dict, privkey_pem: str) -> str:
    """Sign orders document with RSA private key. Returns base64 signature."""
    if not HAS_CRYPTO:
        return ""
    privkey = serialization.load_pem_private_key(privkey_pem.encode("utf-8"), password=None)
    data = canonical_json(orders)
    sig = privkey.sign(data, asym_padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode("utf-8")


# ── Orders Document ──

# All order keys with defaults — these replicate across all nodes
ORDER_KEYS = {
    "lock_active": 0,
    "desktop_active": 0,
    "desktop_locked_devices": "",
    "message": "",
    "desktop_message": "",
    "task_text": "",
    "task_orig": "",
    "task_randcaps": 0,
    "task_reps": 0,
    "task_done": 0,
    "mode": "basic",
    "paywall": "0",
    "paywall_original": "0",
    "compliment": "",
    "word_min": 50,
    "exercise": "Do 20 pushups",
    "vibrate": 0,
    "penalty": 0,
    "shame": 0,
    "dim": 0,
    "mute": 0,
    "unlock_at": 0,
    "locked_at": 0,
    "offer": "",
    "offer_status": "",
    "offer_response": "",
    "offer_time": 0,
    "geofence_lat": "",
    "geofence_lon": "",
    "geofence_radius_m": "",
    "pinned_message": "",
    "lion_pinned_message": "",
    "sub_tier": "",
    "sub_due": 0,
    "sub_total_owed": 0,
    "checkin_deadline": -1,
    "free_unlocks": 0,
    "free_unlock_reset": 0,
    "settings_allowed": 0,
    "pin": "",
    "notif_email_evidence": 1,
    "notif_email_escape": 1,
    "notif_email_breach": 1,
    "photo_task": "",
    "photo_hint": "",
    # Countdown-to-lock
    "countdown_lock_at": 0,  # epoch ms — when lock activates (0 = no countdown)
    "countdown_message": "",  # optional message from Lion shown during countdown
    # Curfew fields
    "curfew_enabled": 0,
    "curfew_confine_hour": -1,
    "curfew_release_hour": -1,
    "curfew_radius_m": 100,
    "curfew_lat": "",
    "curfew_lon": "",
    # Bedtime enforcement (separate from curfew — curfew=geofence, bedtime=lock)
    "bedtime_enabled": 0,
    "bedtime_lock_hour": -1,  # hour of day (0-23), -1 = not set
    "bedtime_unlock_hour": -1,  # auto-unlock hour
    # Body check
    "body_check_active": 0,
    "body_check_area": "",
    "body_check_interval_h": 12,
    "body_check_last": 0,
    "body_check_streak": 0,
    "body_check_last_result": "",
    "body_check_baseline": "",
    # Screen time leash
    "screen_time_quota_minutes": 0,  # 0 = disabled
    "screen_time_reset_hour": 0,  # hour of day to reset counter (default midnight)
    # Release
    "released": "",  # device target for release-forever
    "release_timestamp": "",  # epoch ms string
    "entrapped": 0,  # scrambled PIN, no escape
    # Daily tribute (cost of freedom — accrues while unlocked)
    "tribute_active": 0,
    "tribute_amount": 0,  # $/day added to paywall while unlocked
    "tribute_last_applied": 0,  # epoch ms when last applied
    # Fine (recurring penalty — accrues regardless of lock state)
    "fine_active": 0,
    "fine_amount": 0,  # $ per interval
    "fine_interval_m": 60,  # minutes between charges
    "fine_last_applied": 0,  # epoch ms
    # Streak bonuses (positive reinforcement — Lion enables, server tracks)
    "streak_enabled": 0,
    "streak_start": 0,  # epoch ms when current streak began
    "streak_escapes_at_start": 0,  # escape count when streak began
    "streak_7d_claimed": 0,  # 1 if 7d bonus already applied this streak
    "streak_30d_claimed": 0,  # 1 if 30d bonus already applied this streak
    # Payment email — Lion's IMAP creds (set via Lion's Share)
    "payment_imap_host": "",
    "payment_imap_user": "",
    "payment_imap_pass": "",
}


class OrdersDocument:
    """Versioned, signed orders document replicated across all mesh nodes."""

    def __init__(self, persist_path=None):
        self.version = 0
        self.updated_at = 0
        self.signature = ""
        self.orders = dict(ORDER_KEYS)
        self.persist_path = persist_path
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        if self.persist_path and os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r") as f:
                    data = json.load(f)
                self.version = data.get("version", 0)
                self.updated_at = data.get("updated_at", 0)
                self.signature = data.get("signature", "")
                stored = data.get("orders", {})
                for k in ORDER_KEYS:
                    if k in stored:
                        self.orders[k] = stored[k]
                logger.info("Loaded orders v%s from %s", self.version, self.persist_path)
            except Exception as e:
                logger.warning("Failed to load orders: %s", e)

    def save(self):
        if not self.persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.to_dict(), f, indent=2)
            os.replace(tmp, self.persist_path)
        except Exception as e:
            logger.warning("Failed to save orders: %s", e)

    def to_dict(self) -> dict:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "signature": self.signature,
            "orders": dict(self.orders),
        }

    def apply_remote(self, doc: dict, lion_pubkey: str) -> bool:
        """Apply a remote orders document if it has a higher version.
        Returns True if applied, False if rejected."""
        remote_version = doc.get("version", 0)
        if not isinstance(remote_version, int) or remote_version <= self.version:
            return False

        remote_orders = doc.get("orders", {})
        remote_sig = doc.get("signature", "")

        # SECURITY: if we have Lion's pubkey configured, REQUIRE a valid
        # signature on remote orders. The previous behavior accepted unsigned
        # orders for legacy phone push, which let any LAN peer spoof orders.
        # Nodes without lion_pubkey yet (uninitialized) still use the
        # permissive path so initial setup can complete.
        if lion_pubkey:
            if not remote_sig:
                logger.warning("REJECTED orders v%s — unsigned (lion_pubkey configured)", remote_version)
                return False
            if not verify_signature(remote_orders, remote_sig, lion_pubkey):
                logger.warning("REJECTED orders v%s — invalid signature", remote_version)
                return False

        with self.lock:
            self.version = remote_version
            self.updated_at = doc.get("updated_at", int(time.time() * 1000))
            self.signature = remote_sig
            for k in ORDER_KEYS:
                if k in remote_orders:
                    self.orders[k] = remote_orders[k]
            self.save()

        logger.info("Applied orders v%s", self.version)
        return True

    def bump_version(self, privkey_pem: str = ""):
        """Increment version after a local order change. Optionally sign."""
        with self.lock:
            self.version += 1
            self.updated_at = int(time.time() * 1000)
            if privkey_pem:
                self.signature = sign_orders(self.orders, privkey_pem)
            self.save()

    def get(self, key: str, default=None):
        return self.orders.get(key, default)

    def set(self, key: str, value):
        with self.lock:
            self.orders[key] = value


# ── Peer Registry ──


class PeerInfo:
    def __init__(
        self,
        node_id: str,
        node_type: str = "unknown",
        addresses: list | None = None,
        port: int = 8434,
        last_seen: float = 0,
        orders_version: int = 0,
        status: dict | None = None,
    ):
        self.node_id = node_id
        self.node_type = node_type
        self.addresses = addresses or []
        self.port = port
        self.last_seen = last_seen
        self.orders_version = orders_version
        self.status = status or {}

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "type": self.node_type,
            "addresses": self.addresses,
            "port": self.port,
            "last_seen": self.last_seen,
            "orders_version": self.orders_version,
            "status": self.status,
        }

    @staticmethod
    def from_dict(d: dict) -> "PeerInfo":
        return PeerInfo(
            node_id=d.get("node_id", ""),
            node_type=d.get("type", "unknown"),
            addresses=d.get("addresses", []),
            port=d.get("port", 8434),
            last_seen=d.get("last_seen", 0),
            orders_version=d.get("orders_version", 0),
            status=d.get("status", {}),
        )


_DEFAULT_WHITELIST = {
    # Generic seed IDs (used by _seed_configured_peers in desktop collars)
    "phone",
    "homelab",
}


def _load_warren_whitelist():
    """Load warren whitelist from config, falling back to defaults.
    Users add their own node IDs via config.json "warren_whitelist" key."""
    try:
        _cfg_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shared")
        if _cfg_dir not in sys.path:
            sys.path.insert(0, _cfg_dir)
        from focuslock_config import load_config

        cfg = load_config()
        custom = set(cfg.get("warren_whitelist", []))
        return _DEFAULT_WHITELIST | custom if custom else _DEFAULT_WHITELIST
    except Exception:
        return set(_DEFAULT_WHITELIST)


WARREN_WHITELIST = _load_warren_whitelist()


class TrustStore:
    """Tracks which peers are trusted and why."""

    def __init__(self, persist_path=None):
        self.trusted = {}  # node_id -> {"reason": str, "at": float}
        self.persist_path = persist_path
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        if self.persist_path and os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r") as f:
                    self.trusted = json.load(f)
            except Exception as e:
                logger.debug("Warren trust load failed: %s", e)

    def save(self):
        if not self.persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.trusted, f, indent=2)
            os.replace(tmp, self.persist_path)
        except Exception as e:
            logger.debug("Warren trust save failed: %s", e)

    def trust(self, node_id: str, reason: str = ""):
        with self.lock:
            self.trusted[node_id] = {"reason": reason, "at": time.time()}
            self.save()

    def is_trusted(self, node_id: str) -> bool:
        return node_id in self.trusted or node_id in WARREN_WHITELIST


class PeerRegistry:
    """Tracks all known mesh nodes."""

    def __init__(self, persist_path=None, trust_store=None):
        self.peers = {}  # node_id -> PeerInfo
        self.lock = threading.Lock()
        self.persist_path = persist_path
        self.trust_store = trust_store
        self._load()
        self._prune_stale()

    def _load(self):
        if self.persist_path and os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r") as f:
                    data = json.load(f)
                for node_id, info in data.items():
                    self.peers[node_id] = PeerInfo.from_dict(info)
                logger.info("Loaded %d peers from %s", len(self.peers), self.persist_path)
            except Exception as e:
                logger.warning("Failed to load peers: %s", e)

    def _prune_stale(self):
        """Remove any peers not in the warren whitelist."""
        with self.lock:
            stale = [nid for nid in self.peers if nid not in WARREN_WHITELIST]
            if stale:
                for nid in stale:
                    del self.peers[nid]
                logger.info("Pruned non-warren peers: %s", stale)
                self.save()

    def save(self):
        if not self.persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({nid: p.to_dict() for nid, p in self.peers.items()}, f, indent=2)
            os.replace(tmp, self.persist_path)
        except Exception as e:
            logger.warning("Failed to save peers: %s", e)

    def update_peer(
        self,
        node_id: str,
        node_type: str | None = None,
        addresses: list | None = None,
        port: int | None = None,
        orders_version: int | None = None,
        status: dict | None = None,
    ):
        if node_id not in WARREN_WHITELIST:
            return
        with self.lock:
            peer = self.peers.get(node_id)
            if peer is None:
                peer = PeerInfo(node_id)
                self.peers[node_id] = peer
                logger.info("Discovered new peer: %s", node_id)
            if node_type:
                peer.node_type = node_type
            if addresses:
                existing = set(peer.addresses)
                for a in addresses:
                    existing.add(a)
                peer.addresses = list(existing)
            if port is not None:
                peer.port = port
            if orders_version is not None:
                peer.orders_version = orders_version
            if status is not None:
                peer.status = status
            peer.last_seen = time.time()
            self.save()

    def learn_from_known_nodes(self, known_nodes: dict):
        """Learn about peers from a sync response's known_nodes field."""
        for node_id, info in known_nodes.items():
            if node_id not in WARREN_WHITELIST:
                continue
            if node_id not in self.peers:
                self.update_peer(
                    node_id,
                    node_type=info.get("type", "unknown"),
                    addresses=info.get("addresses", []),
                    port=info.get("port", 8434),
                    orders_version=info.get("orders_version", 0),
                )

    def get_all_except(self, my_id: str) -> list:
        """Return all peers except self."""
        with self.lock:
            return [p for nid, p in self.peers.items() if nid != my_id]

    def to_known_nodes(self, my_id: str) -> dict:
        """Build known_nodes dict for sync responses."""
        with self.lock:
            return {
                nid: {
                    "type": p.node_type,
                    "addresses": p.addresses,
                    "port": p.port,
                    "orders_version": p.orders_version,
                    "last_seen": p.last_seen,
                }
                for nid, p in self.peers.items()
                if nid != my_id
            }


# ── Gossip Protocol ──


def _http_post(url: str, data: dict, timeout: float = 5.0) -> dict:
    """POST JSON to a URL, return parsed response or None."""
    try:
        body = json.dumps(data).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


def _http_get(url: str, timeout: float = 5.0) -> dict:
    """GET JSON from a URL, return parsed response or None."""
    try:
        req = urllib.request.Request(url, method="GET")
        resp = urllib.request.urlopen(req, timeout=timeout)
        return json.loads(resp.read().decode("utf-8"))
    except Exception:
        return None


MAX_PEER_ADDRESSES = 4  # Cap stored addresses per peer to avoid timeout storms


def _try_peer_addrs(peer: PeerInfo, path: str, data: dict | None = None, timeout: float = 3.0) -> dict:
    """Try all addresses for a peer until one works.
    Tries known addresses first, then falls back to Tailscale IP lookup.
    Promotes working address to front; caps list to avoid timeout storms."""
    # Build candidate list: existing addresses + Tailscale IP if not already known
    candidates = list(peer.addresses)
    ts_ip = get_tailscale_ip_for_node(peer.node_id)
    if ts_ip and ts_ip not in candidates:
        candidates.append(ts_ip)
    for addr in candidates:
        url = f"http://{addr}:{peer.port}{path}"
        if data is not None:
            result = _http_post(url, data, timeout=timeout)
        else:
            result = _http_get(url, timeout=timeout)
        if result is not None:
            # Promote working address to front and cap the list
            new_addrs = [addr] + [a for a in peer.addresses if a != addr]
            peer.addresses = new_addrs[:MAX_PEER_ADDRESSES]
            return result
    return None


def _gossip_one_peer(peer, sync_payload, orders, peers, lion_pubkey, on_orders_applied):
    """Contact a single peer for gossip. Called from parallel threads."""
    resp = _try_peer_addrs(peer, "/mesh/sync", sync_payload, timeout=5.0)
    if resp is None:
        return

    # Update peer info
    peers.update_peer(
        resp.get("node_id", peer.node_id),
        node_type=resp.get("type", peer.node_type),
        addresses=resp.get("addresses", peer.addresses),
        port=resp.get("port", peer.port),
        orders_version=resp.get("orders_version", peer.orders_version),
        status=resp.get("status"),
    )

    # Learn about other nodes
    known = resp.get("known_nodes", {})
    if known:
        peers.learn_from_known_nodes(known)

    # Accept orders if remote has higher version
    if resp.get("orders_version", 0) > orders.version and "orders" in resp:
        applied = orders.apply_remote(
            {
                "version": resp["orders_version"],
                "updated_at": resp.get("updated_at", 0),
                "signature": resp.get("signature", ""),
                "orders": resp["orders"],
            },
            lion_pubkey,
        )
        if applied and on_orders_applied:
            on_orders_applied(orders.orders)


def gossip_tick(
    my_id: str,
    my_type: str,
    my_addresses: list,
    my_port: int,
    orders: OrdersDocument,
    peers: PeerRegistry,
    local_status: dict,
    lion_pubkey: str,
    on_orders_applied=None,
):
    """One round of gossip — contact all peers in parallel, exchange state.

    Args:
        on_orders_applied: Optional callback(orders_dict) when remote orders are applied.
    """
    my_peers = peers.get_all_except(my_id)
    if not my_peers:
        return

    sync_payload = {
        "pin": str(orders.get("pin", "")),
        "node_id": my_id,
        "type": my_type,
        "addresses": my_addresses,
        "port": my_port,
        "orders_version": orders.version,
        "status": local_status,
    }

    # Contact all peers in parallel — one thread each, join with timeout
    threads = []
    for peer in my_peers:
        t = threading.Thread(
            target=_gossip_one_peer,
            args=(peer, sync_payload, orders, peers, lion_pubkey, on_orders_applied),
            daemon=True,
        )
        t.start()
        threads.append(t)

    # Wait for all threads with a reasonable timeout (don't block forever)
    deadline = time.time() + 10.0
    for t in threads:
        remaining = max(0.1, deadline - time.time())
        t.join(timeout=remaining)


def push_to_peers(my_id: str, orders: OrdersDocument, peers: PeerRegistry):
    """Push current orders to all peers that are behind. Fire-and-forget threads."""
    my_peers = peers.get_all_except(my_id)
    doc = orders.to_dict()
    pin = str(orders.get("pin", ""))

    for peer in my_peers:
        if peer.orders_version >= orders.version:
            continue

        def _push(p=peer):
            payload = {
                "pin": pin,
                "node_id": my_id,
                "type": "push",
                "orders_version": doc["version"],
                "orders": doc["orders"],
                "signature": doc.get("signature", ""),
                "updated_at": doc.get("updated_at", 0),
                "status": {},
            }
            _try_peer_addrs(p, "/mesh/sync", payload, timeout=5.0)

        threading.Thread(target=_push, daemon=True).start()


def bump_and_broadcast(
    orders: OrdersDocument, my_id: str, peers: PeerRegistry, privkey_pem: str = "", ntfy_fn=None, on_orders_applied=None
):
    """Bump version, push to peers, and optionally notify via ntfy.

    Consolidates the common bump_version() + push_to_peers() pattern.
    ntfy_fn(version) is called best-effort after push.
    """
    orders.bump_version(privkey_pem)
    if on_orders_applied:
        on_orders_applied(orders.orders)
    push_to_peers(my_id, orders, peers)
    if ntfy_fn:
        try:
            ntfy_fn(orders.version)
        except Exception:
            pass  # ntfy is best-effort


# ── Mesh HTTP Handler Helpers ──


def handle_mesh_sync(
    body: dict,
    my_id: str,
    my_type: str,
    my_addresses: list,
    my_port: int,
    orders: OrdersDocument,
    peers: PeerRegistry,
    local_status: dict,
    lion_pubkey: str,
    on_orders_applied=None,
    ledger=None,
    messages=None,
) -> dict:
    """Handle POST /mesh/sync — the core gossip endpoint."""
    remote_id = body.get("node_id", "")
    remote_version = body.get("orders_version", 0)

    # Update peer info from request
    if remote_id:
        peers.update_peer(
            remote_id,
            node_type=body.get("type", "unknown"),
            addresses=body.get("addresses", []),
            port=body.get("port", my_port),
            orders_version=remote_version,
            status=body.get("status"),
        )

    # Accept orders if remote has higher version
    if remote_version > orders.version and "orders" in body:
        applied = orders.apply_remote(
            {
                "version": remote_version,
                "updated_at": body.get("updated_at", 0),
                "signature": body.get("signature", ""),
                "orders": body["orders"],
            },
            lion_pubkey,
        )
        if applied and on_orders_applied:
            on_orders_applied(orders.orders)

    # Build response
    response = {
        "node_id": my_id,
        "type": my_type,
        "addresses": my_addresses,
        "port": my_port,
        "orders_version": orders.version,
        "updated_at": orders.updated_at,
        "signature": orders.signature,
        "status": local_status,
        "known_nodes": peers.to_known_nodes(my_id),
    }

    # Include full orders if the requester is behind
    if remote_version < orders.version:
        response["orders"] = dict(orders.orders)

    return response


def handle_mesh_order(
    body: dict,
    orders: OrdersDocument,
    peers: PeerRegistry,
    my_id: str,
    apply_fn=None,
    lion_pubkey: str = "",
    on_orders_applied=None,
    ntfy_fn=None,
) -> dict:
    """Handle POST /mesh/order — receive an order from Lion's Share.

    Args:
        apply_fn: Callback(action, params, orders) to apply locally. Returns result dict.
        on_orders_applied: Callback(orders_dict) after orders are updated.
        ntfy_fn: Optional callback(version) to publish ntfy wake-up after push.
    """
    action = body.get("action", "")
    params = body.get("params", {})
    signature = body.get("signature", "")

    if not action:
        return {"error": "action required"}

    # SECURITY: require either a valid PIN or a valid Lion signature.
    # Without this any LAN peer could POST to /mesh/order and unlock the collar.
    expected_pin = str(orders.get("pin", ""))
    pin_ok = validate_pin(body, orders) if expected_pin else False
    sig_ok = False
    if signature and lion_pubkey:
        try:
            # Signature covers canonical({action, params}) — minimal envelope
            sig_payload = {"action": action, "params": params}
            sig_ok = verify_signature(sig_payload, signature, lion_pubkey)
        except Exception as e:
            logger.warning("Mesh order signature check raised: %s", e)
            sig_ok = False
    # If neither auth method configured (PIN and lion_pubkey both missing),
    # this is an uninitialized node — fall through legacy permissive behavior
    # so initial setup can complete. Otherwise require one to pass.
    if expected_pin or lion_pubkey:
        if not pin_ok and not sig_ok:
            return {"error": "unauthenticated — missing valid pin or signature"}

    # Apply the action locally
    result = {}
    if apply_fn:
        result = apply_fn(action, params, orders) or {}

    # If the order came with a pre-signed orders document, apply it directly
    if "orders" in body and "orders_version" in body:
        remote_doc = {
            "version": body["orders_version"],
            "updated_at": body.get("updated_at", int(time.time() * 1000)),
            "signature": signature,
            "orders": body["orders"],
        }
        applied = orders.apply_remote(remote_doc, lion_pubkey)
        if applied and on_orders_applied:
            on_orders_applied(orders.orders)
    else:
        orders.bump_version()
        if on_orders_applied:
            on_orders_applied(orders.orders)

    # Push to all peers + ntfy wake-up
    push_to_peers(my_id, orders, peers)
    if ntfy_fn:
        try:
            ntfy_fn(orders.version)
        except Exception:
            pass  # ntfy is best-effort; gossip handles consistency

    return {
        "ok": True,
        "action": action,
        "orders_version": orders.version,
        **result,
    }


def handle_mesh_status(orders: OrdersDocument, peers: PeerRegistry, my_id: str, local_status: dict) -> dict:
    """Handle GET /mesh/status — aggregated mesh state for Lion's Share."""
    nodes = {}
    for nid, peer in peers.peers.items():
        nodes[nid] = {
            "type": peer.node_type,
            "online": (time.time() - peer.last_seen) < 60,
            "last_seen": peer.last_seen,
            "orders_version": peer.orders_version,
            "status": peer.status,
            "addresses": peer.addresses,
            "port": peer.port,
        }
    nodes[my_id] = {
        "type": "self",
        "online": True,
        "last_seen": time.time(),
        "orders_version": orders.version,
        "status": local_status,
    }
    return {
        "orders_version": orders.version,
        "orders": dict(orders.orders),
        "signature": orders.signature,
        "nodes": nodes,
        # Convenience fields for Lion's Share status polling
        "locked": orders.get("lock_active") == 1 or str(orders.get("lock_active")) == "1",
        "escapes": orders.get("escapes", 0),
        "paywall": str(orders.get("paywall", "0")),
        "timer_remaining_ms": max(0, int(orders.get("unlock_at", 0)) - int(time.time() * 1000))
        if orders.get("unlock_at")
        else 0,
        "task_reps": orders.get("task_reps", 0),
        "task_done": orders.get("task_done", 0),
        "offer": str(orders.get("offer", "")),
        "offer_status": str(orders.get("offer_status", "")),
        "sub_tier": str(orders.get("sub_tier", "")),
    }


def handle_mesh_ping(my_id: str, orders: OrdersDocument) -> dict:
    """Handle GET /mesh/ping — lightweight health check."""
    return {
        "ok": True,
        "node_id": my_id,
        "orders_version": orders.version,
        "timestamp": int(time.time() * 1000),
    }


# ── Gossip Loop Thread ──


class GossipThread(threading.Thread):
    """Background thread that runs gossip on a fixed interval."""

    def __init__(
        self,
        interval_seconds: int,
        my_id: str,
        my_type: str,
        my_addresses: list,
        my_port: int,
        orders: OrdersDocument,
        peers: PeerRegistry,
        status_fn=None,
        lion_pubkey_fn=None,
        on_orders_applied=None,
        ledger=None,
        messages=None,
    ):
        super().__init__(daemon=True)
        self.interval = interval_seconds
        self.my_id = my_id
        self.my_type = my_type
        self.my_addresses = my_addresses
        self.my_port = my_port
        self.orders = orders
        self.peers = peers
        self.status_fn = status_fn or (lambda: {})
        self.lion_pubkey_fn = lion_pubkey_fn or (lambda: "")
        self.on_orders_applied = on_orders_applied
        self.running = True

    def run(self):
        time.sleep(3)  # let service start up
        while self.running:
            try:
                # Re-resolve addresses each tick (DHCP renewal, WiFi roaming, TS reconnect)
                fresh_addrs = get_local_addresses()
                gossip_tick(
                    self.my_id,
                    self.my_type,
                    fresh_addrs,
                    self.my_port,
                    self.orders,
                    self.peers,
                    self.status_fn(),
                    self.lion_pubkey_fn(),
                    self.on_orders_applied,
                )
            except Exception as e:
                logger.warning("Gossip error: %s", e)
                traceback.print_exc()
            time.sleep(self.interval)

    def stop(self):
        self.running = False


# ── Utility: PIN Validation ──


def validate_pin(body: dict, orders: OrdersDocument) -> bool:
    """Check PIN from request body against orders document.
    Uses timing-safe comparison to prevent pin guessing via response time."""
    import hmac

    pin = str(body.get("pin", ""))
    expected = str(orders.get("pin", ""))
    if not pin or not expected:
        return False
    return hmac.compare_digest(pin, expected)


# ── Utility: Get Local Addresses ──


def get_local_addresses() -> list:
    """Get this machine's non-loopback IP addresses (LAN + Tailscale)."""
    addrs = []
    # Method 1: Linux ip command
    try:
        import subprocess

        result = subprocess.run(
            ["ip", "-4", "-o", "addr", "show"],
            capture_output=True,
            text=True,
            timeout=5,
            **_SUBPROCESS_FLAGS,
        )
        for line in result.stdout.strip().split("\n"):
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "inet":
                    addr = parts[i + 1].split("/")[0]
                    if not addr.startswith("127."):
                        addrs.append(addr)
    except Exception:
        pass
    # Method 2: Windows — enumerate all interfaces via getaddrinfo
    if not addrs:
        try:
            hostname = socket.gethostname()
            for info in socket.getaddrinfo(hostname, None, socket.AF_INET):
                addr = info[4][0]
                if not addr.startswith("127."):
                    addrs.append(addr)
        except Exception:
            pass
    # Method 3: Tailscale CLI — get Tailscale IP if not already found
    ts_addrs = _get_tailscale_addresses()
    for a in ts_addrs:
        if a not in addrs:
            addrs.append(a)
    # Method 4: UDP socket fallback
    if not addrs:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            addrs.append(s.getsockname()[0])
            s.close()
        except Exception:
            pass
    return addrs


def _get_tailscale_addresses() -> list:
    """Get Tailscale IPs from the tailscale CLI."""
    try:
        import subprocess

        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            capture_output=True,
            text=True,
            timeout=5,
            **_SUBPROCESS_FLAGS,
        )
        if result.returncode == 0:
            return [ip.strip() for ip in result.stdout.strip().split("\n") if ip.strip()]
    except Exception:
        pass
    return []


# ── Tailscale Hostname Resolution ──

# Cached tailnet name (discovered once, reused)
_tailnet_name = None
_tailnet_lock = threading.Lock()


def _get_tailnet_name() -> str:
    """Discover the MagicDNS tailnet name (e.g. 'tail12345.ts.net')."""
    global _tailnet_name
    if _tailnet_name is not None:
        return _tailnet_name
    with _tailnet_lock:
        if _tailnet_name is not None:
            return _tailnet_name
        try:
            import subprocess

            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
                **_SUBPROCESS_FLAGS,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                dns_name = data.get("Self", {}).get("DNSName", "")
                # DNSName is like "myhost.tail12345.ts.net." — extract tailnet
                parts = dns_name.rstrip(".").split(".")
                if len(parts) >= 3:
                    _tailnet_name = ".".join(parts[1:])  # "tail12345.ts.net"
                    logger.info("Discovered tailnet: %s", _tailnet_name)
                    return _tailnet_name
        except Exception:
            pass
        _tailnet_name = ""  # Cache the failure too
        return ""


# Map mesh node IDs to Tailscale hostnames (from `tailscale status`)
_ts_hostname_map = {}
_ts_hostname_lock = threading.Lock()
_ts_hostname_last_refresh = 0
_TS_HOSTNAME_REFRESH_INTERVAL = 60  # refresh every 60s (was 300 — too slow for CGNAT rotations)


def _refresh_tailscale_hosts():
    """Build a map of Tailscale IP -> hostname from `tailscale status --json`."""
    global _ts_hostname_map, _ts_hostname_last_refresh
    now = time.time()
    if now - _ts_hostname_last_refresh < _TS_HOSTNAME_REFRESH_INTERVAL:
        return
    with _ts_hostname_lock:
        if now - _ts_hostname_last_refresh < _TS_HOSTNAME_REFRESH_INTERVAL:
            return
        try:
            import subprocess

            result = subprocess.run(
                ["tailscale", "status", "--json"],
                capture_output=True,
                text=True,
                timeout=5,
                **_SUBPROCESS_FLAGS,
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                new_map = {}
                for _node_id, peer_info in data.get("Peer", {}).items():
                    ts_ips = peer_info.get("TailscaleIPs", [])
                    hostname = peer_info.get("HostName", "").lower()
                    for ip in ts_ips:
                        if ":" not in ip:  # IPv4 only
                            new_map[hostname] = ip
                # Also include self
                self_info = data.get("Self", {})
                self_ips = self_info.get("TailscaleIPs", [])
                self_host = self_info.get("HostName", "").lower()
                for ip in self_ips:
                    if ":" not in ip:
                        new_map[self_host] = ip
                _ts_hostname_map = new_map
                _ts_hostname_last_refresh = now
        except Exception:
            pass


def get_tailscale_ip_for_node(node_id: str) -> str:
    """Resolve a mesh node ID to a Tailscale IP address.
    Handles mismatches between mesh node IDs and Tailscale hostnames:
      - 'myhost-win' -> tries 'myhost' in Tailscale
      - 'pixel' -> matches 'pixel 10' (prefix match)
      - Explicit overrides via set_tailscale_node_map()
    """
    _refresh_tailscale_hosts()
    # Check explicit overrides first
    if node_id in _ts_node_overrides and _ts_node_overrides[node_id] in _ts_hostname_map:
        return _ts_hostname_map[_ts_node_overrides[node_id]]
    # Also check if override maps directly to an IP
    if node_id in _ts_node_overrides:
        override = _ts_node_overrides[node_id]
        for _ts_host, ts_ip in _ts_hostname_map.items():
            if override == ts_ip:
                return ts_ip
    # Direct match
    if node_id in _ts_hostname_map:
        return _ts_hostname_map[node_id]
    # Strip -win suffix (mesh uses 'myhost-win', Tailscale uses 'myhost')
    base = node_id.rsplit("-win", 1)[0] if node_id.endswith("-win") else node_id
    if base in _ts_hostname_map:
        return _ts_hostname_map[base]
    # Prefix match — 'pixel' matches 'pixel 10', etc.
    for ts_host, ts_ip in _ts_hostname_map.items():
        normalized = ts_host.lower().replace(" ", "").replace("'", "").replace("-", "")
        base_normalized = base.lower().replace(" ", "").replace("'", "").replace("-", "")
        if normalized.startswith(base_normalized) or base_normalized.startswith(normalized):
            return ts_ip
    return ""


# Explicit mesh node ID -> Tailscale hostname overrides
_ts_node_overrides = {}


def set_tailscale_node_map(mapping: dict):
    """Set explicit mesh node ID -> Tailscale hostname mappings.
    Example: {"my-phone": "pixel-8", "controller": "oneplus-13"}
    Called from config loading."""
    global _ts_node_overrides
    _ts_node_overrides = {k.lower(): v.lower() for k, v in mapping.items()}


# ── UDP LAN Discovery ──

LAN_DISCOVERY_PORT = 21037  # NOT 21027 — that's Syncthing's Local Discovery port
LAN_DISCOVERY_MAGIC = b"FOCUSLOCK-MESH-V1"
LAN_BEACON_INTERVAL = 30  # seconds


def _build_beacon(node_id: str, node_type: str, port: int, orders_version: int) -> bytes:
    """Build a UDP discovery beacon packet."""
    payload = json.dumps(
        {
            "magic": "FOCUSLOCK-MESH-V1",
            "node_id": node_id,
            "type": node_type,
            "port": port,
            "orders_version": orders_version,
        },
        separators=(",", ":"),
    ).encode("utf-8")
    return payload


def _parse_beacon(data: bytes) -> dict:
    """Parse a UDP discovery beacon. Returns dict or None."""
    try:
        msg = json.loads(data.decode("utf-8"))
        if msg.get("magic") != "FOCUSLOCK-MESH-V1":
            return None
        return msg
    except Exception:
        return None


class LANDiscoveryThread(threading.Thread):
    """Broadcasts and listens for mesh peer beacons on the LAN via UDP.

    Beacon sent every 30s on UDP broadcast (255.255.255.255:21027).
    Listens for beacons from other nodes and adds them to the peer registry.
    """

    def __init__(
        self,
        my_id: str,
        my_type: str,
        my_port: int,
        orders: OrdersDocument,
        peers: PeerRegistry,
        beacon_interval: int = LAN_BEACON_INTERVAL,
    ):
        super().__init__(daemon=True)
        self.my_id = my_id
        self.my_type = my_type
        self.my_port = my_port
        self.orders = orders
        self.peers = peers
        self.beacon_interval = beacon_interval
        self.running = True
        self._sock = None

    def run(self):
        """Run both beacon sender and listener."""
        # Start listener in a sub-thread
        listener = threading.Thread(target=self._listen_loop, daemon=True)
        listener.start()
        # Beacon sender loop on this thread
        time.sleep(2)  # let listener bind first
        self._send_loop()

    def _send_loop(self):
        """Periodically broadcast our beacon."""
        while self.running:
            try:
                beacon = _build_beacon(
                    self.my_id,
                    self.my_type,
                    self.my_port,
                    self.orders.version,
                )
                sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                sock.settimeout(2)
                try:
                    sock.sendto(beacon, ("255.255.255.255", LAN_DISCOVERY_PORT))
                finally:
                    sock.close()
            except Exception as e:
                logger.warning("LAN beacon send error: %s", e)
            time.sleep(self.beacon_interval)

    def _listen_loop(self):
        """Listen for beacons from other nodes."""
        try:
            self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            # SO_REUSEPORT not available on Windows
            try:
                self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except (AttributeError, OSError):
                pass
            self._sock.bind(("0.0.0.0", LAN_DISCOVERY_PORT))
            self._sock.settimeout(5)
            logger.info("LAN discovery listening on UDP :%s", LAN_DISCOVERY_PORT)
        except Exception as e:
            logger.error("LAN discovery bind failed: %s", e)
            return

        while self.running:
            try:
                data, (sender_ip, _sender_port) = self._sock.recvfrom(4096)
                msg = _parse_beacon(data)
                if msg is None:
                    continue
                remote_id = msg.get("node_id", "")
                if not remote_id or remote_id == self.my_id:
                    continue  # ignore our own beacons
                if remote_id not in WARREN_WHITELIST:
                    continue  # ignore unknown nodes

                # Register/update peer with discovered LAN address
                self.peers.update_peer(
                    remote_id,
                    node_type=msg.get("type", "unknown"),
                    addresses=[sender_ip],
                    port=msg.get("port", 8434),
                    orders_version=msg.get("orders_version", 0),
                )
                # Also inject Tailscale IP if we know it
                ts_ip = get_tailscale_ip_for_node(remote_id)
                if ts_ip:
                    self.peers.update_peer(remote_id, addresses=[ts_ip])

            except TimeoutError:
                continue
            except Exception as e:
                if self.running:
                    logger.warning("LAN discovery listen error: %s", e)
                time.sleep(1)

    def stop(self):
        self.running = False
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass


# ── VoucherPool ──


class VoucherPool:
    """Stores signed penalty vouchers for offline enforcement."""

    def __init__(self, persist_path=None):
        self.vouchers = []  # list of voucher dicts
        self.persist_path = persist_path
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        if self.persist_path and os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r") as f:
                    self.vouchers = json.load(f)
            except Exception as e:
                logger.warning("Failed to load vouchers: %s", e)

    def save(self):
        if not self.persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.vouchers, f)
            os.replace(tmp, self.persist_path)
        except Exception as e:
            logger.warning("Failed to save vouchers: %s", e)

    def get_available(self) -> list:
        with self.lock:
            now_ms = int(time.time() * 1000)
            return [v for v in self.vouchers if not v.get("redeemed") and v.get("expires", 0) > now_ms]

    def store(self, vouchers: list, pubkey: str = ""):
        with self.lock:
            existing_ids = {v.get("id") for v in self.vouchers}
            for v in vouchers:
                if v.get("id") not in existing_ids:
                    self.vouchers.append(v)
            self.save()

    def cleanup_expired(self):
        with self.lock:
            now_ms = int(time.time() * 1000)
            self.vouchers = [v for v in self.vouchers if v.get("expires", 0) > now_ms or v.get("redeemed")]
            self.save()

    def redeem(self, voucher_id: str) -> dict:
        with self.lock:
            for v in self.vouchers:
                if v.get("id") == voucher_id and not v.get("redeemed"):
                    v["redeemed"] = True
                    v["redeemed_at"] = int(time.time() * 1000)
                    self.save()
                    return v
        return None


# ── PaymentLedger ──


class PaymentLedger:
    """Tracks payments and charges for paywall balance calculation."""

    def __init__(self, persist_path=None):
        self.entries = []
        self.imap_epoch = 0
        self.persist_path = persist_path
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        if self.persist_path and os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r") as f:
                    data = json.load(f)
                self.entries = data.get("entries", [])
                self.imap_epoch = data.get("imap_epoch", 0)
            except Exception as e:
                logger.warning("Failed to load ledger: %s", e)

    def save(self):
        if not self.persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"entries": self.entries, "imap_epoch": self.imap_epoch}, f)
            os.replace(tmp, self.persist_path)
        except Exception as e:
            logger.warning("Failed to save ledger: %s", e)

    def balance(self) -> float:
        """Net balance: positive = bunny owes, negative = credit."""
        with self.lock:
            total = 0.0
            for e in self.entries:
                if e.get("type") == "payment":
                    total -= e.get("amount", 0)
                else:
                    total += e.get("amount", 0)
            return total

    def add_entry(self, entry_type: str, amount: float, source: str = "", description: str = "") -> dict:
        with self.lock:
            # Dedup by source
            if source:
                for e in self.entries:
                    if e.get("source") == source:
                        return {"error": "duplicate", "source": source}
            entry = {
                "type": entry_type,
                "amount": amount,
                "source": source,
                "description": description,
                "timestamp": int(time.time() * 1000),
            }
            self.entries.append(entry)
            self.save()
            return {"ok": True, "entry": entry}

    def set_imap_epoch(self, epoch: int):
        with self.lock:
            self.imap_epoch = epoch
            self.save()

    def get_entries(self, limit: int = 50) -> list:
        with self.lock:
            return list(reversed(self.entries[-limit:]))


# ── MessageStore ──


class MessageStore:
    """Stores messages between bunny and lion."""

    def __init__(self, persist_path=None):
        self.messages = []
        self.persist_path = persist_path
        self.lock = threading.Lock()
        self._load()

    def _load(self):
        if self.persist_path and os.path.exists(self.persist_path):
            try:
                with open(self.persist_path, "r") as f:
                    self.messages = json.load(f)
            except Exception as e:
                logger.warning("Failed to load messages: %s", e)

    def save(self):
        if not self.persist_path:
            return
        try:
            os.makedirs(os.path.dirname(self.persist_path), exist_ok=True)
            tmp = self.persist_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(self.messages, f)
            os.replace(tmp, self.persist_path)
        except Exception as e:
            logger.warning("Failed to save messages: %s", e)

    def add(self, msg: dict) -> dict:
        with self.lock:
            msg["ts"] = msg.get("ts", int(time.time() * 1000))
            msg["id"] = msg.get("id", f"{msg['ts']}_{len(self.messages)}")
            self.messages.append(msg)
            # Cap at 500
            if len(self.messages) > 500:
                self.messages = self.messages[-500:]
            self.save()
            return msg

    def get(self, reader: str = "", limit: int = 50) -> list:
        with self.lock:
            return list(reversed(self.messages[-limit:]))

    def mark_read(self, message_id: str, reader: str) -> dict:
        with self.lock:
            for m in self.messages:
                if m.get("id") == message_id:
                    m.setdefault("read_by", [])
                    if reader not in m["read_by"]:
                        m["read_by"].append(reader)
                    self.save()
                    return {"ok": True}
        return {"error": "not found"}

    def mark_replied(self, message_id: str) -> dict:
        with self.lock:
            for m in self.messages:
                if m.get("id") == message_id:
                    m["replied"] = True
                    self.save()
                    return {"ok": True}
        return {"error": "not found"}


# ── Voucher/Ledger/Message Handlers ──


def handle_store_vouchers(body: dict, pool: VoucherPool, lion_pubkey: str = "") -> dict:
    vouchers = body.get("vouchers", [])
    if not vouchers:
        return {"error": "no vouchers"}
    pool.store(vouchers, lion_pubkey)
    return {"ok": True, "stored": len(vouchers)}


def handle_get_vouchers(pool: VoucherPool) -> dict:
    return {"vouchers": pool.get_available()}


def handle_redeem_voucher(
    body: dict,
    pool: VoucherPool,
    orders: OrdersDocument,
    peers: PeerRegistry,
    my_id: str,
    lion_pubkey: str = "",
    on_orders_applied=None,
    ntfy_fn=None,
) -> dict:
    vid = body.get("id", "")
    if not vid:
        return {"error": "id required"}
    voucher = pool.redeem(vid)
    if not voucher:
        return {"error": "voucher not found or already redeemed"}
    # Apply the voucher action (e.g., add-paywall)
    action = voucher.get("action", "")
    amount = voucher.get("amount", 0)
    if action == "add-paywall" and amount > 0:
        current = int(float(orders.get("paywall", "0") or "0"))
        orders.set("paywall", str(current + amount))
        bump_and_broadcast(orders, my_id, peers, on_orders_applied=on_orders_applied, ntfy_fn=ntfy_fn)
    return {"ok": True, "redeemed": voucher}


def handle_ledger_entry(
    body: dict,
    ledger: PaymentLedger,
    orders: OrdersDocument,
    peers: PeerRegistry,
    my_id: str,
    on_orders_applied=None,
    ntfy_fn=None,
) -> dict:
    entry_type = body.get("type", "charge")
    amount = float(body.get("amount", 0))
    source = body.get("source", "")
    description = body.get("description", "")
    result = ledger.add_entry(entry_type, amount, source, description)
    if "ok" in result:
        new_balance = max(0, ledger.balance())
        orders.set("paywall", str(int(new_balance)))
        bump_and_broadcast(orders, my_id, peers, on_orders_applied=on_orders_applied, ntfy_fn=ntfy_fn)
    return result


def handle_set_imap_epoch(body: dict, ledger: PaymentLedger) -> dict:
    epoch = int(body.get("epoch", 0))
    if epoch <= 0:
        return {"error": "invalid epoch"}
    ledger.set_imap_epoch(epoch)
    return {"ok": True, "imap_epoch": epoch}


def handle_send_message(body: dict, store: MessageStore) -> dict:
    text = body.get("text", "")
    from_who = body.get("from", "system")
    if not text:
        return {"error": "text required"}
    msg = store.add({"from": from_who, "text": text, "pinned": body.get("pinned", False)})
    return {"ok": True, "message": msg}


def handle_mark_read(body: dict, store: MessageStore) -> dict:
    msg_id = body.get("id", "")
    reader = body.get("reader", "")
    if not msg_id:
        return {"error": "id required"}
    return store.mark_read(msg_id, reader)


def handle_mark_replied(body: dict, store: MessageStore) -> dict:
    msg_id = body.get("id", "")
    if not msg_id:
        return {"error": "id required"}
    return store.mark_replied(msg_id)


def handle_get_messages(store: MessageStore, reader: str = "", limit: int = 50) -> dict:
    return {"messages": store.get(reader, limit)}


def handle_get_ledger(ledger: PaymentLedger, limit: int = 50) -> dict:
    return {"entries": ledger.get_entries(limit), "balance": ledger.balance(), "imap_epoch": ledger.imap_epoch}

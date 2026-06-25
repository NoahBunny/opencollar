# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Direct Sync — shared mesh sync polling logic.

Extracted from both focuslock-desktop.py (Linux) and
focuslock-desktop-win.py (Windows) to eliminate code duplication.
Handles endpoint priority (mesh URL > homelab > phone > Tailscale peers),
peer discovery, and order application with signature verification.
"""

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)


def try_sync(
    url,
    name,
    *,
    node_id,
    node_type,
    my_addrs,
    mesh_port,
    mesh_orders,
    mesh_peers,
    local_status,
    lion_pubkey,
    on_orders_applied=None,
    pin="",
    mesh_id="",
    timeout=5,
):
    """Attempt mesh sync with a single endpoint.

    Sends local state, receives remote state, updates peers, and applies
    newer orders if the remote has a higher version.

    Args:
        url: Base URL of the endpoint (e.g. "http://192.168.1.5:8432").
        name: Human-readable label for logging.
        node_id: This node's mesh ID.
        node_type: This node's type (e.g. "desktop").
        my_addrs: List of this node's reachable addresses.
        mesh_port: This node's mesh port.
        mesh_orders: MeshOrders instance.
        mesh_peers: MeshPeers instance.
        local_status: Dict of local status fields to send.
        lion_pubkey: Lion's RSA public key string for signature verification.
        on_orders_applied: Optional callback(orders_dict) after applying.
        pin: Mesh PIN for authentication.
        mesh_id: Account-based mesh ID. If set, uses /api/mesh/{id}/sync.

    Returns:
        True if the endpoint responded (even if no new orders), False on error.
    """
    try:
        payload = json.dumps(
            {
                "pin": pin,
                "node_id": node_id,
                "type": node_type,
                "addresses": my_addrs,
                "port": mesh_port,
                "orders_version": mesh_orders.version,
                "status": local_status,
            }
        ).encode()
        # Use account-based endpoint if mesh_id is configured
        sync_path = f"/api/mesh/{mesh_id}/sync" if mesh_id else "/mesh/sync"
        req = urllib.request.Request(f"{url}{sync_path}", data=payload, headers={"Content-Type": "application/json"})
        resp = urllib.request.urlopen(req, timeout=timeout)
        data = json.loads(resp.read())

        # Update peer info from response
        resp_id = data.get("node_id", name)
        mesh_peers.update_peer(
            resp_id,
            node_type=data.get("type", "unknown"),
            addresses=data.get("addresses", []),
            port=data.get("port", 0),
            orders_version=data.get("orders_version", 0),
            status=data.get("status"),
        )
        known = data.get("known_nodes", {})
        if known:
            mesh_peers.learn_from_known_nodes(known)

        # Apply newer orders if available
        remote_ver = data.get("orders_version", 0)
        has_orders = "orders" in data
        logger.debug("%s responded v%s, has_orders=%s, local v%s", name, remote_ver, has_orders, mesh_orders.version)
        if remote_ver > mesh_orders.version and has_orders:
            logger.info("Applying v%s from %s (local was v%s)", remote_ver, name, mesh_orders.version)
            applied = mesh_orders.apply_remote(
                {
                    "version": remote_ver,
                    "updated_at": data.get("updated_at", 0),
                    "signature": data.get("signature", ""),
                    "orders": data["orders"],
                },
                lion_pubkey,
            )
            if applied:
                logger.info("Now at v%s lock_active=%s", mesh_orders.version, mesh_orders.orders.get("lock_active"))
                if on_orders_applied:
                    on_orders_applied(mesh_orders.orders)
                return True
            else:
                logger.warning("apply_remote returned False for v%s", remote_ver)
        return True
    except Exception as e:
        logger.warning("%s sync error: %s", name, e)
        return False


def direct_sync_poll(
    *,
    mesh_url,
    homelab_url,
    phone_addresses,
    phone_port,
    node_id,
    node_type,
    mesh_port,
    mesh_orders,
    mesh_peers,
    local_status_fn,
    lion_pubkey_fn,
    on_orders_applied=None,
    get_local_addresses_fn,
    pin="",
    get_tailscale_ip_fn=None,
    mesh_id="",
    preferred_endpoint=None,
    on_preferred_endpoint=None,
    direct_timeout=2.5,
    relay_timeout=5,
):
    """Poll mesh endpoints DIRECT-FIRST, then fall back to the relay.

    Endpoint priority (homelab demoted to optional last; direct is primary):
        1. Configured phone addresses (direct LAN)
        2. Tailscale IPs for known peers (direct, cross-network P2P)
        3. (A3) .onion addresses via the local Tor SOCKS proxy
        4. HTTPS mesh URL (shared cloud relay — fallback)
        5. Homelab (optional self-hosted server — last)

    A sticky last-good endpoint (preferred_endpoint) is tried first so we don't
    pay a direct-probe timeout on every tick when off-network; the endpoint that
    succeeds is reported back via on_preferred_endpoint. Direct probes use a
    short timeout (direct_timeout) so the fall-through to the relay stays snappy.

    Stops after the first successful sync.

    Args:
        mesh_url: Cloud mesh relay URL (or "").
        homelab_url: Homelab server URL (or "").
        phone_addresses: List of phone LAN IP strings.
        phone_port: Phone mesh port.
        node_id: This node's mesh ID.
        node_type: This node's type.
        mesh_port: This node's mesh port.
        mesh_orders: MeshOrders instance.
        mesh_peers: MeshPeers instance.
        local_status_fn: Callable returning local status dict.
        lion_pubkey_fn: Callable returning Lion's pubkey string.
        on_orders_applied: Optional callback(orders_dict).
        get_local_addresses_fn: Callable returning list of local addresses.
        pin: Mesh PIN.
        get_tailscale_ip_fn: Optional callable(node_id) -> IP string.
        mesh_id: Account-based mesh ID for server endpoints.
        preferred_endpoint: Last-good endpoint URL to try first (or None).
        on_preferred_endpoint: Optional callback(url_or_None) recording the
            endpoint that just succeeded (or None when all failed).
        direct_timeout: Per-probe timeout (s) for direct LAN/Tailscale/onion.
        relay_timeout: Per-probe timeout (s) for the relay/homelab.

    Returns:
        True if any endpoint responded, False if all failed.
    """
    my_addrs = get_local_addresses_fn()
    lion_pubkey = lion_pubkey_fn()
    local_status = local_status_fn()

    common = dict(
        node_id=node_id,
        node_type=node_type,
        my_addrs=my_addrs,
        mesh_port=mesh_port,
        mesh_orders=mesh_orders,
        mesh_peers=mesh_peers,
        local_status=local_status,
        lion_pubkey=lion_pubkey,
        on_orders_applied=on_orders_applied,
        pin=pin,
    )

    # Build the ordered candidate list as (url, name, mesh_id, timeout) tuples.
    # Direct paths first (short timeout); relay/homelab last (longer timeout).
    candidates = []
    for addr in phone_addresses:
        candidates.append((f"http://{addr}:{phone_port}", "phone", "", direct_timeout))
    if get_tailscale_ip_fn:
        for peer in mesh_peers.get_all_except(node_id):
            ts_ip = get_tailscale_ip_fn(peer.node_id)
            if ts_ip:
                candidates.append((f"http://{ts_ip}:{peer.port}", f"ts:{peer.node_id}", "", direct_timeout))
    # A3 inserts .onion endpoints here (direct, via the Tor SOCKS proxy).
    if mesh_url:
        candidates.append((mesh_url, "mesh", mesh_id, relay_timeout))
    if homelab_url:
        candidates.append((homelab_url, "homelab", mesh_id, relay_timeout))

    # Sticky last-good: promote the preferred endpoint to the front so we skip
    # dead direct probes when we already know what works.
    if preferred_endpoint:
        promoted = [c for c in candidates if c[0] == preferred_endpoint]
        if promoted:
            rest = [c for c in candidates if c[0] != preferred_endpoint]
            candidates = promoted + rest

    for url, name, mid, timeout in candidates:
        if try_sync(url, name, mesh_id=mid, timeout=timeout, **common):
            if on_preferred_endpoint:
                on_preferred_endpoint(url)
            return True

    if on_preferred_endpoint:
        on_preferred_endpoint(None)
    return False


def relay_to_phones(action, params, *, mesh_orders=None, mesh_peers, node_id, pin=""):
    """Forward an order to all known phone peers via mesh push.

    Args:
        action: Order action string (e.g. "lock", "unlock").
        params: Order params dict.
        mesh_orders: MeshOrders instance (used for PIN fallback, optional).
        mesh_peers: MeshPeers instance.
        node_id: This node's mesh ID (to exclude self from peers).
        pin: Mesh PIN.  If empty and mesh_orders provided, reads from orders.
    """
    phone_peers = [p for p in mesh_peers.get_all_except(node_id) if p.node_type == "phone"]
    if not pin and mesh_orders:
        pin = str(mesh_orders.get("pin", ""))
    for peer in phone_peers:
        payload = json.dumps(
            {
                "action": action,
                "params": params,
                "pin": pin,
            }
        ).encode()
        for addr in peer.addresses:
            try:
                req = urllib.request.Request(
                    f"http://{addr}:{peer.port}/mesh/order", data=payload, headers={"Content-Type": "application/json"}
                )
                urllib.request.urlopen(req, timeout=3)
                logger.info("Forwarded %s to phone %s:%s", action, addr, peer.port)
                break
            except Exception:
                continue

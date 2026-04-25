"""End-to-end HTTP-level QA for the multi-tenant correctness routes.

Three slices land here:

- `/api/mesh/{id}/auto-accept` — Lion-signed flag toggle. When ON, a
  subsequent /vault/{id}/register-node-request lands directly in the
  approved list instead of pending. Key rotation (existing node_id, new
  pubkey) still requires explicit Lion approval to close the takeover
  vector documented at docs/VAULT-DESIGN.md:266.
- `/api/mesh/{id}/state-mirror` — Bunny-signed (or vault-node-signed)
  plaintext state mirror so server-side scanners (compound interest,
  IMAP payment crediting) operate on live values rather than stale
  pre-vault-mode orders. Whitelisted to 8 fields.
- `_relay_backfill_consumer_meshes()` + `_ensure_relay_node_registered()`
  — auto-register the relay's pubkey as an approved vault node on every
  consumer mesh so relay-signed state-derived blobs (subscribe,
  compound-interest, payment-received) pass the slave's signature check.

Signed payload layouts:
    auto-accept:  "{mesh_id}|auto-accept|{state}|{ts}"
    state-mirror: "{mesh_id}|{node_id}|state-mirror|{ts}|{state_sha256_hex}"
"""

import base64
import hashlib
import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_admin_e2e", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_admin_e2e"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def live_server(mail_module):
    server = HTTPServer(("127.0.0.1", 0), mail_module.WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _http_post(url, body, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(pub_der).decode()


def _sign(priv, payload):
    sig = priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


@pytest.fixture
def seeded_mesh(mail_module):
    mesh_id = "admin-" + str(int(time.time() * 1000000))
    node_id = "bunny-node-1"
    bunny_priv, bunny_pub = _keypair()
    lion_priv, lion_pub = _keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": lion_pub,
        "auth_token": "test-token",
        "invite_code": "",
        "invite_expires_at": 0,
        "invite_uses": 0,
        "pin": "0000",
        "created_at": int(time.time()),
        "nodes": {
            node_id: {
                "node_id": node_id,
                "bunny_pubkey": bunny_pub,
                "registered_at": int(time.time()),
            },
        },
        "vault_only": False,
        "max_blobs_per_day": 5000,
        "max_total_bytes_mb": 100,
    }
    mail_module._orders_registry.get_or_create(mesh_id)
    try:
        yield {
            "mesh_id": mesh_id,
            "node_id": node_id,
            "bunny_priv": bunny_priv,
            "bunny_pub": bunny_pub,
            "lion_priv": lion_priv,
            "lion_pub": lion_pub,
        }
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)
        mail_module._orders_registry.docs.pop(mesh_id, None)


# ── /api/mesh/{id}/auto-accept ──


class TestAutoAcceptToggle:
    def _toggle(self, live_server, seeded, state, signer_priv=None, ts=None):
        signer = signer_priv if signer_priv is not None else seeded["lion_priv"]
        ts_ms = ts if ts is not None else int(time.time() * 1000)
        payload = f"{seeded['mesh_id']}|auto-accept|{state}|{ts_ms}"
        sig = _sign(signer, payload)
        return _http_post(
            f"{live_server}/api/mesh/{seeded['mesh_id']}/auto-accept",
            {"state": state, "ts": ts_ms, "signature": sig},
        )

    def test_lion_signed_on_flips_flag(self, live_server, seeded_mesh, mail_module):
        status, resp = self._toggle(live_server, seeded_mesh, "on")
        assert status == 200
        assert resp["auto_accept_nodes"] is True
        assert mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_nodes"] is True

    def test_lion_signed_off_clears_flag(self, live_server, seeded_mesh, mail_module):
        # Pre-set the flag to verify "off" actually clears it.
        mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_nodes"] = True
        status, resp = self._toggle(live_server, seeded_mesh, "off")
        assert status == 200
        assert resp["auto_accept_nodes"] is False

    def test_invalid_state_returns_400(self, live_server, seeded_mesh):
        status, resp = self._toggle(live_server, seeded_mesh, "maybe")
        assert status == 400
        assert "state" in resp.get("error", "")

    def test_stale_ts_returns_403(self, live_server, seeded_mesh):
        stale_ts = int(time.time() * 1000) - 10 * 60 * 1000
        status, resp = self._toggle(live_server, seeded_mesh, "on", ts=stale_ts)
        assert status == 403
        assert "ts" in resp.get("error", "")

    def test_bad_signature_returns_403(self, live_server, seeded_mesh):
        # Sign with a fresh keypair the mesh doesn't know about.
        rogue_priv, _ = _keypair()
        status, resp = self._toggle(live_server, seeded_mesh, "on", signer_priv=rogue_priv)
        assert status == 403
        assert "signature" in resp.get("error", "")

    def test_auto_accept_on_then_register_node_lands_approved(self, live_server, seeded_mesh, mail_module):
        """Concrete behavioral test of the toggle: with auto-accept ON,
        the very next /vault/{id}/register-node-request lands in the
        approved list rather than pending."""
        status, _ = self._toggle(live_server, seeded_mesh, "on")
        assert status == 200
        # Now register a fresh node via the vault path.
        _, fresh_pub = _keypair()
        fresh_node_id = "fresh-desktop-" + str(int(time.time() * 1000000))
        status, resp = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node-request",
            {
                "node_id": fresh_node_id,
                "node_type": "desktop",
                "node_pubkey": fresh_pub,
            },
        )
        assert status == 200
        assert resp["status"] == "approved"
        assert resp.get("auto_accepted") is True
        approved_nodes = mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
        assert any(n["node_id"] == fresh_node_id for n in approved_nodes)

    def test_unknown_mesh_returns_404(self, live_server, seeded_mesh):
        ts_ms = int(time.time() * 1000)
        payload = f"ghost|auto-accept|on|{ts_ms}"
        sig = _sign(seeded_mesh["lion_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/ghost/auto-accept",
            {"state": "on", "ts": ts_ms, "signature": sig},
        )
        assert status == 404
        assert resp.get("error") == "mesh not found"


# ── /api/mesh/{id}/state-mirror ──


def _state_payload(mesh_id, node_id, state, ts):
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    state_hash = hashlib.sha256(canonical).hexdigest()
    return f"{mesh_id}|{node_id}|state-mirror|{ts}|{state_hash}"


class TestStateMirror:
    def test_bunny_signed_mirror_writes_whitelisted_fields(self, live_server, seeded_mesh, mail_module):
        ts_ms = int(time.time() * 1000)
        state = {
            "paywall": "150",
            "paywall_original": "100",
            "lock_active": True,
            "ignored_field": "should-not-apply",
        }
        payload = _state_payload(seeded_mesh["mesh_id"], seeded_mesh["node_id"], state, ts_ms)
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/state-mirror",
            {
                "node_id": seeded_mesh["node_id"],
                "ts": ts_ms,
                "state": state,
                "signature": sig,
            },
        )
        assert status == 200
        assert set(resp["applied"]) == {"paywall", "paywall_original", "lock_active"}
        assert resp["signer"] == "bunny"
        # _orders_registry doc reflects the applied values.
        orders = mail_module._orders_registry.get(seeded_mesh["mesh_id"])
        assert orders.get("paywall", "") == "150"
        assert orders.get("paywall_original", "") == "100"
        # bool coerced to "1" string.
        assert orders.get("lock_active", "") == "1"

    def test_bad_signature_returns_403(self, live_server, seeded_mesh):
        ts_ms = int(time.time() * 1000)
        state = {"paywall": "50"}
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/state-mirror",
            {
                "node_id": seeded_mesh["node_id"],
                "ts": ts_ms,
                "state": state,
                "signature": "AAAA" + "=" * 340,
            },
        )
        assert status == 403
        assert "signature" in resp.get("error", "")

    def test_cross_mesh_signer_rejected(self, live_server, seeded_mesh, mail_module):
        # Stand up a second mesh; sign the first mesh's state-mirror with
        # the second mesh's bunny key — verifier picks the seeded_mesh's
        # bunny pubkey and rejects.
        other_mesh = "other-" + str(int(time.time() * 1000000))
        other_node_id = "bunny-other"
        other_bunny_priv, other_bunny_pub = _keypair()
        mail_module._mesh_accounts.meshes[other_mesh] = {
            "mesh_id": other_mesh,
            "lion_pubkey": "",
            "auth_token": "x",
            "invite_code": "",
            "invite_expires_at": 0,
            "invite_uses": 0,
            "pin": "0000",
            "created_at": int(time.time()),
            "nodes": {
                other_node_id: {
                    "node_id": other_node_id,
                    "bunny_pubkey": other_bunny_pub,
                    "registered_at": int(time.time()),
                },
            },
            "vault_only": False,
            "max_blobs_per_day": 5000,
            "max_total_bytes_mb": 100,
        }
        try:
            ts_ms = int(time.time() * 1000)
            state = {"paywall": "1"}
            payload = _state_payload(seeded_mesh["mesh_id"], seeded_mesh["node_id"], state, ts_ms)
            sig = _sign(other_bunny_priv, payload)
            status, _ = _http_post(
                f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/state-mirror",
                {
                    "node_id": seeded_mesh["node_id"],
                    "ts": ts_ms,
                    "state": state,
                    "signature": sig,
                },
            )
            assert status == 403
        finally:
            mail_module._mesh_accounts.meshes.pop(other_mesh, None)

    def test_stale_ts_returns_403(self, live_server, seeded_mesh):
        stale_ts = int(time.time() * 1000) - 10 * 60 * 1000
        state = {"paywall": "10"}
        payload = _state_payload(seeded_mesh["mesh_id"], seeded_mesh["node_id"], state, stale_ts)
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/state-mirror",
            {
                "node_id": seeded_mesh["node_id"],
                "ts": stale_ts,
                "state": state,
                "signature": sig,
            },
        )
        assert status == 403
        assert "ts" in resp.get("error", "")

    def test_missing_state_object_returns_400(self, live_server, seeded_mesh):
        ts_ms = int(time.time() * 1000)
        # state must be a dict; passing a non-dict triggers the early validator.
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/state-mirror",
            {
                "node_id": seeded_mesh["node_id"],
                "ts": ts_ms,
                "state": "not-an-object",
                "signature": "x",
            },
        )
        assert status == 400
        assert "state" in resp.get("error", "")

    def test_unknown_node_returns_403(self, live_server, seeded_mesh):
        ts_ms = int(time.time() * 1000)
        state = {"paywall": "1"}
        payload = _state_payload(seeded_mesh["mesh_id"], "ghost-node", state, ts_ms)
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/state-mirror",
            {
                "node_id": "ghost-node",
                "ts": ts_ms,
                "state": state,
                "signature": sig,
            },
        )
        assert status == 403
        assert "pubkey" in resp.get("error", "") or "signature" in resp.get("error", "")


# ── _relay_backfill_consumer_meshes / _ensure_relay_node_registered ──


@pytest.fixture
def relay_pubkey(mail_module):
    """Inject a stable relay pubkey for backfill tests. Some import-time
    code paths leave RELAY_PUBKEY_DER_B64 empty if the relay keypair file
    isn't on disk; for these tests we just need a non-empty marker the
    backfill code can see."""
    original = mail_module.RELAY_PUBKEY_DER_B64
    if not original:
        # Generate a throwaway DER-encoded pubkey.
        _, pub_b64 = _keypair()
        mail_module.RELAY_PUBKEY_DER_B64 = pub_b64
    yield mail_module.RELAY_PUBKEY_DER_B64
    mail_module.RELAY_PUBKEY_DER_B64 = original


class TestEnsureRelayNodeRegistered:
    def test_fresh_mesh_gets_relay_appended(self, mail_module, seeded_mesh, relay_pubkey):
        # Strip any pre-existing relay registration the import-time path
        # might have written before this test ran.
        mesh_id = seeded_mesh["mesh_id"]
        nodes = mail_module._vault_store.get_nodes(mesh_id)
        nodes = [n for n in nodes if n.get("node_id") != "relay"]
        mail_module._vault_store._write_json(mesh_id, "nodes.json", nodes)

        ok = mail_module._ensure_relay_node_registered(mesh_id)
        assert ok is True
        approved = mail_module._vault_store.get_nodes(mesh_id)
        relay_nodes = [n for n in approved if n.get("node_id") == "relay"]
        assert len(relay_nodes) == 1
        assert relay_nodes[0]["node_pubkey"] == relay_pubkey
        assert relay_nodes[0]["node_type"] == "server"

    def test_idempotent_no_duplicate_appended(self, mail_module, seeded_mesh, relay_pubkey):
        mesh_id = seeded_mesh["mesh_id"]
        mail_module._ensure_relay_node_registered(mesh_id)
        # Second call must not duplicate the entry (add_node already
        # filters by node_id, but the helper should also early-return
        # when the same key is already registered).
        mail_module._ensure_relay_node_registered(mesh_id)
        approved = mail_module._vault_store.get_nodes(mesh_id)
        relay_nodes = [n for n in approved if n.get("node_id") == "relay"]
        assert len(relay_nodes) == 1

    def test_key_rotation_replaces_entry(self, mail_module, seeded_mesh, relay_pubkey):
        mesh_id = seeded_mesh["mesh_id"]
        # Register with a stale pubkey first.
        _, stale_pub = _keypair()
        mail_module._vault_store.add_node(
            mesh_id,
            {
                "node_id": "relay",
                "node_type": "server",
                "node_pubkey": stale_pub,
                "registered_at": int(time.time()) - 3600,
            },
        )
        # Now run the registrar — should detect rotation and update.
        ok = mail_module._ensure_relay_node_registered(mesh_id)
        assert ok is True
        approved = mail_module._vault_store.get_nodes(mesh_id)
        relay_nodes = [n for n in approved if n.get("node_id") == "relay"]
        assert len(relay_nodes) == 1
        assert relay_nodes[0]["node_pubkey"] == relay_pubkey

    def test_no_relay_pubkey_short_circuits(self, mail_module, seeded_mesh):
        # Empty RELAY_PUBKEY_DER_B64 → helper returns False without
        # mutating the store.
        original = mail_module.RELAY_PUBKEY_DER_B64
        mail_module.RELAY_PUBKEY_DER_B64 = ""
        try:
            ok = mail_module._ensure_relay_node_registered(seeded_mesh["mesh_id"])
            assert ok is False
        finally:
            mail_module.RELAY_PUBKEY_DER_B64 = original


class TestRelayBackfill:
    def test_backfills_two_consumer_meshes(self, mail_module, relay_pubkey):
        """Seed two consumer meshes (neither matching OPERATOR_MESH_ID),
        ensure neither has the relay registered, then call the backfill
        helper and confirm both end up with exactly one relay entry."""
        seeded_ids = []
        for n in range(2):
            mid = "backfill-" + str(int(time.time() * 1000000)) + f"-{n}"
            mail_module._mesh_accounts.meshes[mid] = {
                "mesh_id": mid,
                "lion_pubkey": "",
                "auth_token": "x",
                "invite_code": "",
                "invite_expires_at": 0,
                "invite_uses": 0,
                "pin": "0000",
                "created_at": int(time.time()),
                "nodes": {},
                "vault_only": False,
                "max_blobs_per_day": 5000,
                "max_total_bytes_mb": 100,
            }
            # Strip any auto-registered relay entry.
            mail_module._vault_store._write_json(mid, "nodes.json", [])
            seeded_ids.append(mid)
        try:
            mail_module._relay_backfill_consumer_meshes()
            for mid in seeded_ids:
                approved = mail_module._vault_store.get_nodes(mid)
                relay_nodes = [n for n in approved if n.get("node_id") == "relay"]
                assert len(relay_nodes) == 1
                assert relay_nodes[0]["node_pubkey"] == relay_pubkey
        finally:
            for mid in seeded_ids:
                mail_module._mesh_accounts.meshes.pop(mid, None)

    def test_backfill_skips_operator_mesh(self, mail_module, relay_pubkey):
        """OPERATOR_MESH_ID is handled by _relay_self_register; the
        consumer-mesh backfill must skip it explicitly."""
        original_op = mail_module.OPERATOR_MESH_ID
        op_mid = "operator-" + str(int(time.time() * 1000000))
        mail_module.OPERATOR_MESH_ID = op_mid
        mail_module._mesh_accounts.meshes[op_mid] = {
            "mesh_id": op_mid,
            "lion_pubkey": "",
            "auth_token": "x",
            "invite_code": "",
            "invite_expires_at": 0,
            "invite_uses": 0,
            "pin": "0000",
            "created_at": int(time.time()),
            "nodes": {},
            "vault_only": False,
            "max_blobs_per_day": 5000,
            "max_total_bytes_mb": 100,
        }
        mail_module._vault_store._write_json(op_mid, "nodes.json", [])
        try:
            mail_module._relay_backfill_consumer_meshes()
            approved = mail_module._vault_store.get_nodes(op_mid)
            # Operator mesh got skipped — relay node not present from
            # this code path.
            relay_nodes = [n for n in approved if n.get("node_id") == "relay"]
            assert relay_nodes == []
        finally:
            mail_module._mesh_accounts.meshes.pop(op_mid, None)
            mail_module.OPERATOR_MESH_ID = original_op

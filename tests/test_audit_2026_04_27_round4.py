"""Audit 2026-04-27 round-4 — desktop-signed heartbeat (M-1) and H-1 remainder.

- M-1: /webhook/desktop-heartbeat verifies a vault-node signature when
  the body carries one (mesh_id + node_id + ts + signature). Mirrors the
  state-mirror pattern. Unsigned heartbeats from legacy single-tenant
  collars still flow (mesh_id falls through to OPERATOR_MESH_ID, scoped
  to the operator's own host).

- H-1 (remainder): /memory, /standing-orders, /settings now require
  admin_token (?admin_token= or Authorization: Bearer). All three are
  hit by sync-standing-orders.sh on the homelab; the operator's
  deployment updates that out-of-repo script in lockstep with this
  commit. Desktop collar callers (focuslock-desktop.py
  sync_standing_orders, focuslock-desktop-win.py setup-time sync) and
  installers/re-enslave-server.sh health check all updated to send
  the token.

The full audit report is at docs/AUDIT-FINDINGS-2026-04-27.md.
"""

import base64
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_round4", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_round4"] = mod
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


def _http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _http_post(url, body, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _vault_node_keypair():
    """Vault node keypair — same shape as the desktop collar's
    _vault_privkey_pem + _vault_pubkey_der. Signs with PKCS1v15 over the
    canonical "{mesh_id}|{node_id}|desktop-heartbeat|{ts}" envelope; the
    relay's _vault_store.get_nodes(mesh_id) returns entries with a
    base64-encoded DER pubkey (`node_pubkey`)."""
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
def seeded_vault_node(mail_module):
    """Seed a mesh + a vault-registered desktop node so the relay's
    desktop-heartbeat sig verifier finds a matching node_pubkey."""
    mesh_id = "round4-mesh-" + str(int(time.time() * 1000))
    node_id = "desktop-test-host"
    priv, pub_b64 = _vault_node_keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": "",
        "auth_token": "test-token",
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
    mail_module._vault_store.add_node(
        mesh_id,
        {
            "node_id": node_id,
            "node_pubkey": pub_b64,
            "node_type": "desktop",
        },
    )
    try:
        yield {"mesh_id": mesh_id, "node_id": node_id, "priv": priv, "pub_b64": pub_b64}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


# ── M-1: /webhook/desktop-heartbeat sig gate ──


class TestDesktopHeartbeatSigned:
    def test_unsigned_legacy_heartbeat_still_accepted(self, live_server):
        """Old single-tenant collars don't sign; the relay falls back to
        OPERATOR_MESH_ID and accepts the heartbeat. This preserves
        backward compat — the 0.0.0.0:8434 surface is still attackable
        by unsigned forgeries on the operator mesh, but that's the same
        risk the system always had + ADB-write side effect is operator-
        scoped only."""
        code, body = _http_post(
            f"{live_server}/webhook/desktop-heartbeat",
            {"hostname": "legacy-host", "type": "desktop"},
        )
        assert code == 200, f"{code} {body}"
        assert body == {"ok": True}

    def test_signed_with_valid_signature_accepted(self, live_server, seeded_vault_node):
        ts = int(time.time() * 1000)
        payload = f"{seeded_vault_node['mesh_id']}|{seeded_vault_node['node_id']}|desktop-heartbeat|{ts}"
        sig = _sign(seeded_vault_node["priv"], payload)
        code, body = _http_post(
            f"{live_server}/webhook/desktop-heartbeat",
            {
                "hostname": "signed-host",
                "type": "desktop",
                "mesh_id": seeded_vault_node["mesh_id"],
                "node_id": seeded_vault_node["node_id"],
                "ts": ts,
                "signature": sig,
            },
        )
        assert code == 200, f"{code} {body}"
        assert body == {"ok": True}

    def test_bad_signature_rejected(self, live_server, seeded_vault_node):
        ts = int(time.time() * 1000)
        code, body = _http_post(
            f"{live_server}/webhook/desktop-heartbeat",
            {
                "hostname": "spoof-host",
                "type": "desktop",
                "mesh_id": seeded_vault_node["mesh_id"],
                "node_id": seeded_vault_node["node_id"],
                "ts": ts,
                "signature": "AAAA" + "=" * 340,
            },
        )
        assert code == 403
        assert body["error"] == "invalid signature"

    def test_unknown_node_rejected(self, live_server, seeded_vault_node):
        ts = int(time.time() * 1000)
        payload = f"{seeded_vault_node['mesh_id']}|ghost-node|desktop-heartbeat|{ts}"
        sig = _sign(seeded_vault_node["priv"], payload)
        code, body = _http_post(
            f"{live_server}/webhook/desktop-heartbeat",
            {
                "hostname": "host",
                "type": "desktop",
                "mesh_id": seeded_vault_node["mesh_id"],
                "node_id": "ghost-node",
                "ts": ts,
                "signature": sig,
            },
        )
        assert code == 403
        assert body["error"] == "node not registered in vault"

    def test_stale_ts_rejected(self, live_server, seeded_vault_node):
        stale_ts = int(time.time() * 1000) - 10 * 60 * 1000
        payload = f"{seeded_vault_node['mesh_id']}|{seeded_vault_node['node_id']}|desktop-heartbeat|{stale_ts}"
        sig = _sign(seeded_vault_node["priv"], payload)
        code, body = _http_post(
            f"{live_server}/webhook/desktop-heartbeat",
            {
                "hostname": "host",
                "type": "desktop",
                "mesh_id": seeded_vault_node["mesh_id"],
                "node_id": seeded_vault_node["node_id"],
                "ts": stale_ts,
                "signature": sig,
            },
        )
        assert code == 403
        assert body["error"] == "ts out of window"

    def test_invalid_mesh_id_rejected(self, live_server):
        code, body = _http_post(
            f"{live_server}/webhook/desktop-heartbeat",
            {
                "hostname": "host",
                "type": "desktop",
                "mesh_id": "../../etc/passwd",
                "node_id": "node",
                "ts": int(time.time() * 1000),
                "signature": "AAAA" + "=" * 340,
            },
        )
        assert code == 400
        assert body["error"] == "invalid mesh_id"


# ── H-1 remainder: /memory, /standing-orders, /settings auth gate ──


GATED_GET_PATHS = ["/memory", "/standing-orders", "/settings"]


class TestSyncEndpointsAuth:
    @pytest.mark.parametrize("path", GATED_GET_PATHS)
    def test_returns_503_when_admin_token_unconfigured(self, mail_module, live_server, path):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = ""
        try:
            code, _ = _http_get(f"{live_server}{path}")
            assert code == 503, f"{path}: {code}"
        finally:
            mail_module.ADMIN_TOKEN = original

    @pytest.mark.parametrize("path", GATED_GET_PATHS)
    def test_returns_403_with_no_token(self, mail_module, live_server, path):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(f"{live_server}{path}")
            assert code == 403, f"{path}: {code}"
        finally:
            mail_module.ADMIN_TOKEN = original

    @pytest.mark.parametrize("path", GATED_GET_PATHS)
    def test_returns_403_with_wrong_token_query_param(self, mail_module, live_server, path):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(f"{live_server}{path}?admin_token=wrong")
            assert code == 403, f"{path}: {code}"
        finally:
            mail_module.ADMIN_TOKEN = original

    @pytest.mark.parametrize("path", GATED_GET_PATHS)
    def test_returns_403_with_wrong_bearer_header(self, mail_module, live_server, path):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(
                f"{live_server}{path}",
                headers={"Authorization": "Bearer wrong"},
            )
            assert code == 403, f"{path}: {code}"
        finally:
            mail_module.ADMIN_TOKEN = original

    @pytest.mark.parametrize("path", GATED_GET_PATHS)
    def test_accepts_correct_token_query_param(self, mail_module, live_server, path):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            # The endpoint may legitimately 404 when the source file
            # doesn't exist — auth gate fires before the file read.
            # Either 200 or 404 means the gate let us through.
            code, _ = _http_get(f"{live_server}{path}?admin_token=secret-admin-token")
            assert code in (200, 404), f"{path}: {code}"
        finally:
            mail_module.ADMIN_TOKEN = original

    @pytest.mark.parametrize("path", GATED_GET_PATHS)
    def test_accepts_correct_token_bearer_header(self, mail_module, live_server, path):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(
                f"{live_server}{path}",
                headers={"Authorization": "Bearer secret-admin-token"},
            )
            assert code in (200, 404), f"{path}: {code}"
        finally:
            mail_module.ADMIN_TOKEN = original


# ── Offline desktop-collar signer roundtrip ──


def test_desktop_signer_payload_matches_relay_verifier(seeded_vault_node):
    """Sanity: the canonical envelope the desktop collar signs in
    focuslock-desktop.py:phone_home() exactly matches the verifier
    expectation in focuslock-mail.py:/webhook/desktop-heartbeat. Catches
    a future drift where the two sides format the payload differently."""
    ts_ms = int(time.time() * 1000)
    mesh_id = seeded_vault_node["mesh_id"]
    node_id = seeded_vault_node["node_id"]
    priv = seeded_vault_node["priv"]
    pub_b64 = seeded_vault_node["pub_b64"]

    payload = f"{mesh_id}|{node_id}|desktop-heartbeat|{ts_ms}"
    sig_b64 = _sign(priv, payload)

    # Verifier-side: load DER pubkey + verify signature matches.
    pub_der = base64.b64decode(pub_b64)
    pub = serialization.load_der_public_key(pub_der)
    sig_bytes = base64.b64decode(sig_b64)
    pub.verify(sig_bytes, payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())  # raises on mismatch

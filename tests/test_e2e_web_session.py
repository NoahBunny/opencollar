# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""End-to-end HTTP-level tests for the Web Remote QR login flow.

The flow has three round-trips:

    1. POST /admin/web-session {action: "create"} → {session_id, qr_url}
    2. POST /admin/web-session {action: "approve", session_id, signature}
       — Lion's Share scans the QR, signs `{"session_id": ...}` with the
       Lion private key, and POSTs back. Pre-2026-04-24 (commit 6d47d1e),
       this only verified against `get_lion_pubkey()` (the operator mesh's
       Lion). A consumer-mesh Lion's signature always failed → user saw
       "wrong key". Post-fix, the server iterates every mesh's lion_pubkey
       until one verifies, then binds the session to that mesh.
    3. GET /admin/web-session/<session_id> — polled by the web UI; once
       approved, returns a scoped session_token bound to the matched mesh.
       That token authorizes `/admin/order` only against the bound mesh
       (master ADMIN_TOKEN still crosses all meshes).

These tests pin the per-mesh signature-match matrix and the scoped-token
isolation property — i.e. that a token issued to mesh A's Lion cannot
authorize an /admin/order call against mesh B.

The companion unit-level surface (`_issue_session_token`,
`_is_valid_admin_auth(token, mesh_id=...)`) is exercised through the
HTTP poll path; we do not call those helpers directly.
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_websession_e2e", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_websession_e2e"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def admin_token(mail_module):
    """Force a non-empty ADMIN_TOKEN so _is_valid_admin_auth treats
    session tokens as valid. Pre-condition for any scoped-token check —
    `if not ADMIN_TOKEN: return False` short-circuits otherwise."""
    original = mail_module.ADMIN_TOKEN
    mail_module.ADMIN_TOKEN = "test-admin-token-websession"
    try:
        yield mail_module.ADMIN_TOKEN
    finally:
        mail_module.ADMIN_TOKEN = original


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


def _http_get(url):
    req = urllib.request.Request(url, method="GET")
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


def _sign_session(priv, session_id):
    """Lion's Share signs `{"session_id": <id>}` with PKCS1v15+SHA256.
    Server verifies via mesh.verify_signature, which canonicalizes the
    payload dict — so we sign the canonical JSON form as well."""
    payload = {"session_id": session_id}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = priv.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


def _seed_mesh(mail_module, mesh_id, lion_pub):
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": lion_pub,
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
    mail_module._orders_registry.get_or_create(mesh_id)


@pytest.fixture
def two_meshes(mail_module):
    """Seed mesh A (the operator) + mesh B (a consumer) with distinct
    Lion keypairs. Both should be reachable by the approve path."""
    mid_a = "ws-op-" + str(int(time.time() * 1000000))
    mid_b = "ws-consumer-" + str(int(time.time() * 1000000))
    a_priv, a_pub = _keypair()
    b_priv, b_pub = _keypair()
    _seed_mesh(mail_module, mid_a, a_pub)
    _seed_mesh(mail_module, mid_b, b_pub)
    original_op = mail_module.OPERATOR_MESH_ID
    mail_module.OPERATOR_MESH_ID = mid_a
    try:
        yield {
            "operator_mesh_id": mid_a,
            "operator_priv": a_priv,
            "operator_pub": a_pub,
            "consumer_mesh_id": mid_b,
            "consumer_priv": b_priv,
            "consumer_pub": b_pub,
        }
    finally:
        mail_module.OPERATOR_MESH_ID = original_op
        mail_module._mesh_accounts.meshes.pop(mid_a, None)
        mail_module._mesh_accounts.meshes.pop(mid_b, None)
        mail_module._orders_registry.docs.pop(mid_a, None)
        mail_module._orders_registry.docs.pop(mid_b, None)


def _create_session(live_server):
    status, resp = _http_post(f"{live_server}/admin/web-session", {"action": "create"})
    assert status == 200, resp
    return resp["session_id"]


# ── Per-mesh approval matrix (the "wrong key" fix) ──


class TestWebSessionApprovePerMesh:
    """The `/admin/web-session action=approve` path must iterate every
    mesh's lion_pubkey and bind the session to whichever one verifies.
    Pre-2026-04-24 this only checked `get_lion_pubkey()` → consumer
    meshes always saw 'invalid signature' ('wrong key' on the QR)."""

    def test_operator_lion_signature_matches_and_binds_operator_mesh(self, live_server, two_meshes, mail_module):
        sid = _create_session(live_server)
        sig = _sign_session(two_meshes["operator_priv"], sid)
        status, resp = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        assert status == 200
        assert resp["status"] == "approved"
        # Internal state: session must be flagged + bound to operator mesh.
        sess = mail_module._web_sessions[sid]
        assert sess["approved"] is True
        assert sess["mesh_id"] == two_meshes["operator_mesh_id"]

    def test_consumer_lion_signature_matches_and_binds_consumer_mesh(self, live_server, two_meshes, mail_module):
        """The literal regression test for the 'wrong key' bug — a
        consumer-mesh Lion approves a web session and the server
        accepts the signature instead of rejecting it as 'invalid
        signature — must be signed by Lion's private key'."""
        sid = _create_session(live_server)
        sig = _sign_session(two_meshes["consumer_priv"], sid)
        status, resp = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        assert status == 200
        assert resp["status"] == "approved"
        sess = mail_module._web_sessions[sid]
        assert sess["approved"] is True
        assert sess["mesh_id"] == two_meshes["consumer_mesh_id"]

    def test_unrelated_keypair_rejected_403_after_iterating_all_meshes(self, live_server, two_meshes):
        sid = _create_session(live_server)
        rogue_priv, _ = _keypair()
        sig = _sign_session(rogue_priv, sid)
        status, resp = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        assert status == 403
        assert "no Lion key matched" in resp["error"]

    def test_mesh_with_empty_lion_pubkey_skipped_during_iteration(self, live_server, two_meshes, mail_module):
        """A consumer mesh that's been seeded but doesn't yet have a
        lion_pubkey (mid-bootstrap) shouldn't crash the approve path —
        it just gets skipped while the iteration continues."""
        empty_mid = "ws-empty-" + str(int(time.time() * 1000000))
        _seed_mesh(mail_module, empty_mid, "")
        try:
            sid = _create_session(live_server)
            sig = _sign_session(two_meshes["consumer_priv"], sid)
            status, resp = _http_post(
                f"{live_server}/admin/web-session",
                {"action": "approve", "session_id": sid, "signature": sig},
            )
            assert status == 200
            assert resp["status"] == "approved"
        finally:
            mail_module._mesh_accounts.meshes.pop(empty_mid, None)
            mail_module._orders_registry.docs.pop(empty_mid, None)


# ── Session lifecycle (create / approve / poll) ──


class TestWebSessionLifecycle:
    def test_unknown_session_id_returns_404_on_approve(self, live_server, two_meshes):
        sig = _sign_session(two_meshes["operator_priv"], "nonexistent-session")
        status, _resp = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": "nonexistent-session", "signature": sig},
        )
        assert status == 404

    def test_already_approved_session_returns_200_idempotent(self, live_server, two_meshes, mail_module):
        sid = _create_session(live_server)
        sig = _sign_session(two_meshes["operator_priv"], sid)
        status, _ = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        assert status == 200
        # Second approve hit must be idempotent.
        status, resp = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        assert status == 200
        assert resp["status"] == "already_approved"

    def test_expired_session_returns_404_on_approve(self, live_server, two_meshes, mail_module):
        sid = _create_session(live_server)
        # Force-age the session past the 5-minute TTL.
        mail_module._web_sessions[sid]["created_at"] = time.time() - mail_module._WEB_SESSION_TTL - 10
        sig = _sign_session(two_meshes["operator_priv"], sid)
        status, _resp = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        assert status == 404

    def test_unknown_action_returns_400(self, live_server):
        status, _resp = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "frobnicate"},
        )
        assert status == 400

    def test_create_response_includes_qr_url_with_session_param(self, live_server, mail_module):
        status, resp = _http_post(f"{live_server}/admin/web-session", {"action": "create"})
        assert status == 200
        assert "session_id" in resp
        assert "qr_url" in resp
        assert f"s={resp['session_id']}" in resp["qr_url"]
        # Internal state must record the session as un-approved.
        assert mail_module._web_sessions[resp["session_id"]]["approved"] is False


# ── Poll endpoint ──


class TestWebSessionPoll:
    def test_poll_before_approve_returns_approved_false(self, live_server):
        sid = _create_session(live_server)
        status, resp = _http_get(f"{live_server}/admin/web-session/{sid}")
        assert status == 200
        assert resp["approved"] is False

    def test_poll_after_approve_returns_scoped_token_and_mesh_id(
        self, live_server, two_meshes, admin_token, mail_module
    ):
        sid = _create_session(live_server)
        sig = _sign_session(two_meshes["consumer_priv"], sid)
        _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        status, resp = _http_get(f"{live_server}/admin/web-session/{sid}")
        assert status == 200
        assert resp["approved"] is True
        assert resp["mesh_id"] == two_meshes["consumer_mesh_id"]
        assert isinstance(resp["session_token"], str) and len(resp["session_token"]) > 16
        assert resp["expires_in"] == mail_module._SESSION_TOKEN_TTL
        # One-time use: subsequent poll on same id is gone.
        status2, _ = _http_get(f"{live_server}/admin/web-session/{sid}")
        assert status2 == 404

    def test_poll_unknown_session_returns_404(self, live_server):
        status, _resp = _http_get(f"{live_server}/admin/web-session/never-existed")
        assert status == 404


# ── Scoped-token isolation: the issued token authorizes its own mesh, not others ──


class TestScopedSessionTokenIsolation:
    def _approve_and_get_token(self, live_server, mail_module, signer_priv, expected_mid):
        sid = _create_session(live_server)
        sig = _sign_session(signer_priv, sid)
        status, _ = _http_post(
            f"{live_server}/admin/web-session",
            {"action": "approve", "session_id": sid, "signature": sig},
        )
        assert status == 200
        status, resp = _http_get(f"{live_server}/admin/web-session/{sid}")
        assert status == 200
        assert resp["mesh_id"] == expected_mid
        return resp["session_token"]

    def test_consumer_session_token_authorizes_consumer_mesh(self, live_server, two_meshes, admin_token, mail_module):
        token = self._approve_and_get_token(
            live_server, mail_module, two_meshes["consumer_priv"], two_meshes["consumer_mesh_id"]
        )
        # The token's bound mesh check is enforced at the helper level.
        assert mail_module._is_valid_admin_auth(token, mesh_id=two_meshes["consumer_mesh_id"])

    def test_consumer_session_token_rejected_for_operator_mesh(self, live_server, two_meshes, admin_token, mail_module):
        """The actual cross-mesh isolation property: a token issued to
        the consumer Lion cannot authorize an order against the operator
        mesh. Master ADMIN_TOKEN bypasses this; scoped tokens do not."""
        token = self._approve_and_get_token(
            live_server, mail_module, two_meshes["consumer_priv"], two_meshes["consumer_mesh_id"]
        )
        assert not mail_module._is_valid_admin_auth(token, mesh_id=two_meshes["operator_mesh_id"])

    def test_operator_session_token_authorizes_only_operator_mesh(
        self, live_server, two_meshes, admin_token, mail_module
    ):
        token = self._approve_and_get_token(
            live_server, mail_module, two_meshes["operator_priv"], two_meshes["operator_mesh_id"]
        )
        assert mail_module._is_valid_admin_auth(token, mesh_id=two_meshes["operator_mesh_id"])
        assert not mail_module._is_valid_admin_auth(token, mesh_id=two_meshes["consumer_mesh_id"])

    def test_master_admin_token_bypasses_mesh_scope(self, live_server, two_meshes, admin_token, mail_module):
        # The master ADMIN_TOKEN must work against any mesh — the operator
        # controls every mesh, by definition.
        assert mail_module._is_valid_admin_auth(admin_token, mesh_id=two_meshes["operator_mesh_id"])
        assert mail_module._is_valid_admin_auth(admin_token, mesh_id=two_meshes["consumer_mesh_id"])

    def test_garbage_token_rejected_regardless_of_mesh_id(self, live_server, two_meshes, admin_token, mail_module):
        assert not mail_module._is_valid_admin_auth("not-a-real-token", mesh_id=two_meshes["operator_mesh_id"])
        assert not mail_module._is_valid_admin_auth("not-a-real-token", mesh_id=two_meshes["consumer_mesh_id"])

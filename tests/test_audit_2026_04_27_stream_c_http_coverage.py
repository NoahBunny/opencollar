"""Audit 2026-04-27 Stream C — focuslock-mail.py HTTP-route coverage push.

The Stream A round files (round1-4 + h2 + round2-round4) covered the
audit fixes themselves. This file fills in the long tail of route
shape + error contracts that the audit plan flagged as ~1100 uncovered
lines: pair handlers, admin handlers, vault routes, memory bundle, and
do_GET dispatch.

Reuses the proven `live_server` + `mail_module` fixture pattern from
`tests/test_audit_2026_04_27_round1.py:34-52`.

Coverage targets:
- Pair handlers — /api/pair/{register, claim, lookup, create, status, code}
- Admin handlers — /admin/disposal-token (lifecycle + cap)
- Vault routes — /vault/{mesh_id}/{since, nodes, nodes-pending,
  register-node, reject-node-request, register-node-request, append}
- Memory bundle — /memory bundle shape + MEMORY_DIR override
- do_GET dispatch — /version, /pubkey, /manifest.json, /qrcode.min.js,
  /collar-icon.png, web UI pages, legacy 410 routes, /web-login,
  /api/paywall, /controller

Non-overlap with: tests/test_pairing.py (PairingRegistry internals,
TTL math, claim_or_reason matrix, vault-status endpoint, claim HTTP
shape, pubkey-fingerprint helpers), tests/test_e2e_admin_routes.py
(auto-accept toggle, state-mirror, ensure-relay-node-registered, relay
backfill), tests/test_e2e_web_session.py (web-session approve + poll),
tests/test_audit_2026_04_27_*.py (the audit fixes themselves).
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_stream_c", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_stream_c"] = mod
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
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def _http_post(url, body, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _rsa_keypair():
    """Generate an RSA-2048 keypair, returning (priv_obj, der_b64_pubkey)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(pub_der).decode()


def _sign_canonical(priv, payload_dict):
    """Sign canonical_json(payload) with PKCS1v15 + SHA256, returning b64 sig.

    Matches focuslock-mail.py's `_verify_signed_payload` which strips the
    `signature` key + canonicalizes the rest with sort_keys=True,
    separators=(",", ":")."""
    canonical = json.dumps(
        {k: v for k, v in payload_dict.items() if k != "signature"},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    sig = priv.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


@pytest.fixture
def seeded_mesh(mail_module):
    """Seed a mesh with a Lion keypair on file. Yields the mesh_id +
    Lion priv obj + DER-b64 pubkey."""
    mesh_id = "stream-c-mesh-" + str(int(time.time() * 1000))
    priv, pub_b64 = _rsa_keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": pub_b64,
        "auth_token": "test-mesh-token-" + mesh_id,
        "invite_code": "",
        "invite_expires_at": 0,
        "invite_uses": 0,
        "pin": "1234",
        "created_at": int(time.time()),
        "nodes": {},
        "vault_only": False,
        "max_blobs_per_day": 5000,
        "max_total_bytes_mb": 100,
    }
    try:
        yield {"mesh_id": mesh_id, "lion_priv": priv, "lion_pub_b64": pub_b64}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


# ───────────────────────────────────────────────────────────────────────
# /api/pair/register — Bunny registers for relay-mediated pairing
# ───────────────────────────────────────────────────────────────────────


class TestPairRegister:
    def test_missing_passphrase_returns_400(self, live_server):
        code, body = _http_post(
            f"{live_server}/api/pair/register",
            {"pubkey": "stub", "mesh_id": "test"},
        )
        assert code == 400
        assert body["error"] == "passphrase required"

    def test_missing_mesh_id_returns_400(self, live_server):
        code, body = _http_post(
            f"{live_server}/api/pair/register",
            {"passphrase": "WORD-12-WORD", "pubkey": "stub"},
        )
        assert code == 400
        assert body["error"] == "mesh_id required"

    def test_happy_path_registers_and_returns_uppercase_passphrase(self, live_server):
        passphrase = "wave-42-storm"
        code, body = _http_post(
            f"{live_server}/api/pair/register",
            {
                "passphrase": passphrase,
                "pubkey": "fakebunnypubkey",
                "node_id": "phone-1",
                "mesh_id": "test-mesh",
            },
        )
        assert code == 200
        assert body == {"ok": True, "passphrase": passphrase.upper()}

    def test_pubkey_alias_bunny_pubkey_accepted(self, live_server):
        """The handler accepts either `pubkey` or `bunny_pubkey` as the key field."""
        code, body = _http_post(
            f"{live_server}/api/pair/register",
            {
                "passphrase": "alpha-99-beta",
                "bunny_pubkey": "via-alias",
                "node_id": "phone-2",
                "mesh_id": "test-mesh",
            },
        )
        assert code == 200
        assert body["ok"] is True


# ───────────────────────────────────────────────────────────────────────
# /api/pair/lookup — backward-compat status alias
# ───────────────────────────────────────────────────────────────────────


class TestPairLookup:
    def test_unknown_passphrase_returns_404(self, live_server):
        code, body = _http_post(
            f"{live_server}/api/pair/lookup",
            {"passphrase": "NEVER-00-EXISTED"},
        )
        assert code == 404
        assert body["error"] == "not found"

    def test_known_passphrase_returns_pubkey_and_paired_state(self, live_server, mail_module):
        """Lookup endpoint passes empty mesh_id to status(), which keys by
        bare uppercased passphrase only — register with mesh_id='' so the
        legacy lookup path can find the entry."""
        passphrase = "lookup-13-test"
        mail_module._pairing_registry.register(passphrase, "lookup-bunny-pubkey", "node-l", mesh_id="")
        try:
            code, body = _http_post(
                f"{live_server}/api/pair/lookup",
                {"passphrase": passphrase},
            )
            assert code == 200
            assert body["pubkey"] == "lookup-bunny-pubkey"
            assert body["paired"] is False  # not yet claimed
            assert body["lion_pubkey"] == ""
            assert body["ip"] == ""  # legacy fields preserved
            assert body["port"] == 0
        finally:
            mail_module._pairing_registry.entries.pop(passphrase.upper(), None)


# ───────────────────────────────────────────────────────────────────────
# /api/pair/create — Lion's Share creates desktop pairing code
# ───────────────────────────────────────────────────────────────────────


class TestPairCreate:
    def test_missing_admin_token_returns_403(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-a"
        try:
            code, body = _http_post(
                f"{live_server}/api/pair/create",
                {"code": "TESTAB"},
            )
            assert code == 403
            assert body["error"] == "invalid admin_token"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_invalid_code_format_returns_400(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-b"
        try:
            code, body = _http_post(
                f"{live_server}/api/pair/create",
                {"code": "no-lowercase!", "admin_token": "secret-b"},
            )
            assert code == 400
            assert "invalid code format" in body["error"]
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_auto_generated_code_when_blank(self, live_server, mail_module, tmp_path, monkeypatch):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-c"
        # Redirect /opt/focuslock/pairing-codes write to tmp_path.
        original_makedirs = mail_module.os.makedirs
        original_open = open

        def safe_makedirs(p, *a, **kw):
            if p == "/opt/focuslock/pairing-codes":
                p = str(tmp_path / "pairing-codes")
            return original_makedirs(p, *a, **kw)

        def safe_open(p, *a, **kw):
            if isinstance(p, str) and p.startswith("/opt/focuslock/pairing-codes/"):
                redirected = str(tmp_path / "pairing-codes" / Path(p).name)
                return original_open(redirected, *a, **kw)
            return original_open(p, *a, **kw)

        monkeypatch.setattr(mail_module.os, "makedirs", safe_makedirs)
        monkeypatch.setattr("builtins.open", safe_open)
        try:
            code, body = _http_post(
                f"{live_server}/api/pair/create",
                {"admin_token": "secret-c"},
            )
            assert code == 200
            assert body["ok"] is True
            assert len(body["code"]) == 6
            assert body["code"].isalnum() and body["code"].isupper()
            assert body["url"].endswith(f"/api/pair/{body['code']}")
            assert body["expires_minutes"] == 60
        finally:
            mail_module.ADMIN_TOKEN = original


# ───────────────────────────────────────────────────────────────────────
# /api/pair/status/<passphrase> — Lion polls pairing status
# ───────────────────────────────────────────────────────────────────────


class TestPairStatus:
    def test_unknown_passphrase_returns_404(self, live_server):
        code, _h, _r = _http_get(f"{live_server}/api/pair/status/NEVER-EXISTED")
        assert code == 404

    def test_pre_claim_status_shows_unpaired(self, live_server, mail_module):
        """Status endpoint passes empty mesh_id to status() — register with
        mesh_id='' so the lookup finds the entry (same legacy path as lookup)."""
        passphrase = "STATUS-77-CHECK"
        mail_module._pairing_registry.register(passphrase, "bp", "n1", mesh_id="")
        try:
            code, _h, raw = _http_get(f"{live_server}/api/pair/status/{passphrase}")
            assert code == 200
            body = json.loads(raw.decode())
            assert body["paired"] is False
            assert body["bunny_pubkey"] == "bp"
            assert body["lion_pubkey"] == ""
        finally:
            mail_module._pairing_registry.entries.pop(passphrase.upper(), None)

    def test_status_is_case_insensitive_via_path(self, live_server, mail_module):
        passphrase = "CASE-22-INS"
        mail_module._pairing_registry.register(passphrase, "bp2", "n2", mesh_id="")
        try:
            code, _, _ = _http_get(f"{live_server}/api/pair/status/{passphrase.lower()}")
            assert code == 200
        finally:
            mail_module._pairing_registry.entries.pop(passphrase.upper(), None)


# ───────────────────────────────────────────────────────────────────────
# /api/pair/<code> — desktop pairing-code retrieval
# ───────────────────────────────────────────────────────────────────────


class TestPairCodeFetch:
    def test_invalid_code_format_returns_400(self, live_server):
        code, _, raw = _http_get(f"{live_server}/api/pair/!@#$")
        assert code == 400
        body = json.loads(raw.decode())
        assert body["error"] == "invalid pairing code"

    def test_unknown_code_returns_404(self, live_server):
        code, _, raw = _http_get(f"{live_server}/api/pair/AAAA11")
        assert code == 404
        body = json.loads(raw.decode())
        assert body["error"] == "invalid pairing code"


# ───────────────────────────────────────────────────────────────────────
# /admin/disposal-token — single-use scoped paywall-add tokens
# ───────────────────────────────────────────────────────────────────────


class TestDisposalToken:
    def test_no_admin_token_configured_returns_503(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = ""
        try:
            code, body = _http_post(f"{live_server}/admin/disposal-token", {})
            assert code == 503
            assert body["error"] == "admin_token not configured"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_missing_token_returns_403(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "disp-secret"
        try:
            code, body = _http_post(f"{live_server}/admin/disposal-token", {})
            assert code == 403
            assert body["error"] == "invalid admin_token"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_wrong_token_returns_403(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "disp-secret"
        try:
            code, _ = _http_post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "wrong"},
            )
            assert code == 403
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_happy_path_returns_token_and_metadata(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "disp-secret"
        try:
            code, body = _http_post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "disp-secret", "max_amount": 30, "ttl": 1800},
            )
            assert code == 200
            assert "disposal_token" in body
            assert len(body["disposal_token"]) > 30
            assert body["max_amount"] == 30
            assert body["expires_in"] == 1800
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_max_amount_capped_at_200(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "disp-secret"
        try:
            code, body = _http_post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "disp-secret", "max_amount": 9999, "ttl": 600},
            )
            assert code == 200
            assert body["max_amount"] == 200
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_ttl_capped_at_7200(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "disp-secret"
        try:
            code, body = _http_post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "disp-secret", "ttl": 99999},
            )
            assert code == 200
            assert body["expires_in"] == 7200
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_default_max_amount_and_ttl(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "disp-secret"
        try:
            code, body = _http_post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "disp-secret"},
            )
            assert code == 200
            assert body["max_amount"] == 50  # default
            assert body["expires_in"] == 3600  # default
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_token_lands_in_active_disposal_dict(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "disp-secret"
        try:
            code, body = _http_post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "disp-secret"},
            )
            assert code == 200
            tk = body["disposal_token"]
            assert tk in mail_module._disposal_tokens
            entry = mail_module._disposal_tokens[tk]
            assert entry["used"] is False
            assert entry["max_amount"] == 50
            assert entry["expires"] > time.time()
        finally:
            mail_module.ADMIN_TOKEN = original
            mail_module._disposal_tokens.pop(body.get("disposal_token", ""), None)


# ───────────────────────────────────────────────────────────────────────
# /vault/{mesh_id}/since/{version} — GET, blob delta
# ───────────────────────────────────────────────────────────────────────


class TestVaultSinceGET:
    def test_unknown_mesh_returns_404(self, live_server):
        code, _, raw = _http_get(f"{live_server}/vault/unknown-mesh/since/0")
        assert code == 404
        body = json.loads(raw.decode())
        assert body["error"] == "mesh not found"

    def test_invalid_mesh_id_returns_400(self, live_server):
        code, _h, _r = _http_get(f"{live_server}/vault/..%2F..%2Fetc/since/0")
        assert code == 400

    def test_missing_version_returns_400(self, live_server, seeded_mesh):
        code, _, raw = _http_get(f"{live_server}/vault/{seeded_mesh['mesh_id']}/since")
        assert code == 400
        body = json.loads(raw.decode())
        assert body["error"] == "version required"

    def test_non_int_version_returns_400(self, live_server, seeded_mesh):
        code, _, raw = _http_get(f"{live_server}/vault/{seeded_mesh['mesh_id']}/since/notanint")
        assert code == 400
        body = json.loads(raw.decode())
        assert body["error"] == "version must be int"

    def test_empty_vault_returns_zero_current_version(self, live_server, seeded_mesh):
        code, _, raw = _http_get(f"{live_server}/vault/{seeded_mesh['mesh_id']}/since/0")
        assert code == 200
        body = json.loads(raw.decode())
        assert body["current_version"] == 0
        assert body["blobs"] == []


# ───────────────────────────────────────────────────────────────────────
# /vault/{mesh_id}/nodes — GET, list approved nodes
# ───────────────────────────────────────────────────────────────────────


class TestVaultNodesGET:
    def test_unknown_mesh_returns_404(self, live_server):
        code, _, _ = _http_get(f"{live_server}/vault/unknown-mesh/nodes")
        assert code == 404

    def test_seeded_mesh_returns_empty_list(self, live_server, seeded_mesh):
        code, _, raw = _http_get(f"{live_server}/vault/{seeded_mesh['mesh_id']}/nodes")
        assert code == 200
        body = json.loads(raw.decode())
        assert body == {"nodes": []}

    def test_seeded_mesh_returns_added_node(self, live_server, seeded_mesh, mail_module):
        mail_module._vault_store.add_node(
            seeded_mesh["mesh_id"],
            {"node_id": "phone-x", "node_pubkey": "pkx", "node_type": "slave"},
        )
        code, _, raw = _http_get(f"{live_server}/vault/{seeded_mesh['mesh_id']}/nodes")
        assert code == 200
        body = json.loads(raw.decode())
        assert len(body["nodes"]) == 1
        assert body["nodes"][0]["node_id"] == "phone-x"


# ───────────────────────────────────────────────────────────────────────
# /vault/{mesh_id}/nodes-pending — GET, requires mesh auth_token
# ───────────────────────────────────────────────────────────────────────


class TestVaultNodesPendingGET:
    def test_no_auth_returns_403(self, live_server, seeded_mesh):
        code, _, raw = _http_get(f"{live_server}/vault/{seeded_mesh['mesh_id']}/nodes-pending")
        assert code == 403
        body = json.loads(raw.decode())
        assert body["error"] == "invalid auth"

    def test_correct_auth_token_returns_200(self, live_server, seeded_mesh, mail_module):
        mail_module._vault_store.add_pending_node(
            seeded_mesh["mesh_id"],
            {"node_id": "pending-1", "node_pubkey": "ppk1"},
        )
        token = mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auth_token"]
        code, _, raw = _http_get(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/nodes-pending?auth_token={token}",
        )
        assert code == 200
        body = json.loads(raw.decode())
        assert len(body["pending"]) == 1
        assert body["pending"][0]["node_id"] == "pending-1"

    def test_bearer_header_auth_works(self, live_server, seeded_mesh, mail_module):
        token = mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auth_token"]
        code, _, _ = _http_get(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/nodes-pending",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert code == 200


# ───────────────────────────────────────────────────────────────────────
# /vault/{mesh_id}/register-node-request — POST, slave self-registers
# ───────────────────────────────────────────────────────────────────────


class TestVaultRegisterNodeRequest:
    def test_missing_node_id_returns_400(self, live_server, seeded_mesh):
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node-request",
            {"node_pubkey": "stub"},
        )
        assert code == 400
        assert "required" in body["error"]

    def test_missing_node_pubkey_returns_400(self, live_server, seeded_mesh):
        code, _body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node-request",
            {"node_id": "phone-1"},
        )
        assert code == 400

    def test_lands_in_pending_queue_by_default(self, live_server, seeded_mesh, mail_module):
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node-request",
            {"node_id": "phone-pending", "node_pubkey": "phk1", "node_type": "slave"},
        )
        assert code == 200
        assert body["status"] == "pending"
        pending = mail_module._vault_store.get_pending_nodes(seeded_mesh["mesh_id"])
        assert any(n["node_id"] == "phone-pending" for n in pending)

    def test_rejected_pubkey_returns_403(self, live_server, seeded_mesh, mail_module):
        mail_module._vault_store.add_rejected_node(seeded_mesh["mesh_id"], "ghost", "rejected-key", "test")
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node-request",
            {"node_id": "phone-r", "node_pubkey": "rejected-key"},
        )
        assert code == 403
        assert body["error"] == "node rejected"

    def test_auto_accept_lands_in_approved_directly(self, live_server, seeded_mesh, mail_module):
        mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_nodes"] = True
        try:
            code, body = _http_post(
                f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node-request",
                {"node_id": "phone-auto", "node_pubkey": "ahk1"},
            )
            assert code == 200
            assert body["status"] == "approved"
            assert body["auto_accepted"] is True
            nodes = mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
            assert any(n["node_id"] == "phone-auto" for n in nodes)
        finally:
            mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_nodes"] = False

    def test_auto_accept_with_key_rotation_falls_to_pending(self, live_server, seeded_mesh, mail_module):
        """Auto-accept does NOT silently replace an existing node's key —
        rotations still require manual Lion approval."""
        # Pre-register node-auto with one key
        mail_module._vault_store.add_node(
            seeded_mesh["mesh_id"],
            {"node_id": "rotate-me", "node_pubkey": "old-key", "node_type": "slave"},
        )
        mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_nodes"] = True
        try:
            code, body = _http_post(
                f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node-request",
                {"node_id": "rotate-me", "node_pubkey": "new-key"},
            )
            assert code == 200
            assert body["status"] == "pending"
            assert "key rotation" in body["reason"]
        finally:
            mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_nodes"] = False


# ───────────────────────────────────────────────────────────────────────
# /vault/{mesh_id}/register-node — POST, Lion-signed approval
# ───────────────────────────────────────────────────────────────────────


class TestVaultRegisterNode:
    def test_no_lion_pubkey_returns_403(self, live_server, mail_module):
        # Mesh without a lion_pubkey
        mid = "no-lion-" + str(int(time.time() * 1000))
        mail_module._mesh_accounts.meshes[mid] = {
            "mesh_id": mid,
            "lion_pubkey": "",
            "auth_token": "x",
            "pin": "0",
            "created_at": int(time.time()),
            "nodes": {},
            "vault_only": False,
            "max_blobs_per_day": 5000,
            "max_total_bytes_mb": 100,
        }
        try:
            code, body = _http_post(
                f"{live_server}/vault/{mid}/register-node",
                {"node_id": "n1", "node_pubkey": "pk1", "signature": "x"},
            )
            assert code == 403
            assert "lion_pubkey" in body["error"]
        finally:
            mail_module._mesh_accounts.meshes.pop(mid, None)

    def test_bad_signature_returns_403(self, live_server, seeded_mesh):
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node",
            {"node_id": "n1", "node_pubkey": "pk1", "signature": "AAAA" + "=" * 340},
        )
        assert code == 403
        assert body["error"] == "invalid signature"

    def test_lion_signed_happy_path_lands_in_approved(self, live_server, seeded_mesh, mail_module):
        payload = {
            "node_id": "lion-approved",
            "node_type": "slave",
            "node_pubkey": "approved-pk",
        }
        payload["signature"] = _sign_canonical(seeded_mesh["lion_priv"], payload)
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node",
            payload,
        )
        assert code == 200
        assert body == {"ok": True}
        nodes = mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
        assert any(n["node_id"] == "lion-approved" for n in nodes)

    def test_missing_node_id_returns_400(self, live_server, seeded_mesh):
        payload = {"node_pubkey": "pk", "node_type": "slave"}
        payload["signature"] = _sign_canonical(seeded_mesh["lion_priv"], payload)
        code, _body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node",
            payload,
        )
        assert code == 400


# ───────────────────────────────────────────────────────────────────────
# /vault/{mesh_id}/reject-node-request — POST, Lion-signed denial
# ───────────────────────────────────────────────────────────────────────


class TestVaultRejectNodeRequest:
    def test_no_lion_pubkey_returns_403(self, live_server, mail_module):
        mid = "reject-no-lion-" + str(int(time.time() * 1000))
        mail_module._mesh_accounts.meshes[mid] = {
            "mesh_id": mid,
            "lion_pubkey": "",
            "auth_token": "x",
            "pin": "0",
            "created_at": int(time.time()),
            "nodes": {},
            "vault_only": False,
            "max_blobs_per_day": 5000,
            "max_total_bytes_mb": 100,
        }
        try:
            code, _ = _http_post(
                f"{live_server}/vault/{mid}/reject-node-request",
                {"node_pubkey": "pk", "signature": "x"},
            )
            assert code == 403
        finally:
            mail_module._mesh_accounts.meshes.pop(mid, None)

    def test_bad_signature_returns_403(self, live_server, seeded_mesh):
        code, _body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/reject-node-request",
            {"node_pubkey": "pk", "signature": "AAAA" + "=" * 340},
        )
        assert code == 403

    def test_missing_node_pubkey_returns_400(self, live_server, seeded_mesh):
        payload = {"node_id": "n1", "reason": "test"}
        payload["signature"] = _sign_canonical(seeded_mesh["lion_priv"], payload)
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/reject-node-request",
            payload,
        )
        assert code == 400
        assert body["error"] == "node_pubkey required"

    def test_lion_signed_happy_path_adds_to_rejection_list(self, live_server, seeded_mesh, mail_module):
        payload = {
            "node_id": "rejected-1",
            "node_pubkey": "rejected-pk",
            "reason": "untrusted",
        }
        payload["signature"] = _sign_canonical(seeded_mesh["lion_priv"], payload)
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/reject-node-request",
            payload,
        )
        assert code == 200
        assert body == {"ok": True}
        assert mail_module._vault_store.is_rejected(seeded_mesh["mesh_id"], "rejected-pk")


# ───────────────────────────────────────────────────────────────────────
# /vault/{mesh_id}/append — POST, two-writer signed blob append
# ───────────────────────────────────────────────────────────────────────


class TestVaultAppend:
    def test_non_dict_blob_returns_400(self, live_server, seeded_mesh):
        # The handler json-loads the body — to send a non-object we'd
        # normally need to bypass the JSON encoder. Easiest: send "[]"
        # as a raw POST body.
        url = f"{live_server}/vault/{seeded_mesh['mesh_id']}/append"
        req = urllib.request.Request(
            url,
            data=b"[]",  # JSON array, not object
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                code = r.status
                body = json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            code = e.code
            body = json.loads(e.read().decode())
        assert code == 400
        assert body["error"] == "blob must be object"

    def test_mesh_id_mismatch_in_blob_returns_400(self, live_server, seeded_mesh):
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/append",
            {"mesh_id": "different-mesh", "ciphertext": "x"},
        )
        assert code == 400
        assert "mesh_id mismatch" in body["error"]

    def test_unsigned_blob_returns_403(self, live_server, seeded_mesh):
        code, body = _http_post(
            f"{live_server}/vault/{seeded_mesh['mesh_id']}/append",
            {"mesh_id": seeded_mesh["mesh_id"], "ciphertext": "x", "version": 1},
        )
        assert code == 403
        assert body["error"] == "invalid signature"


# ───────────────────────────────────────────────────────────────────────
# /memory — admin-gated bundle of enforcement-memory .md files
# (auth gate already covered in round4; here we cover the bundle shape.)
# ───────────────────────────────────────────────────────────────────────


class TestMemoryBundle:
    def test_empty_dir_returns_404(self, live_server, mail_module, tmp_path, monkeypatch):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "mem-secret"
        # Point MEMORY_DIR at a path that doesn't exist
        monkeypatch.setenv("MEMORY_DIR", str(tmp_path / "nonexistent"))
        try:
            code, _, raw = _http_get(
                f"{live_server}/memory?admin_token=mem-secret",
            )
            assert code == 404
            body = json.loads(raw.decode())
            assert body["error"] == "no memory dir"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_populated_dir_returns_bundle_with_md5_hash(self, live_server, mail_module, tmp_path, monkeypatch):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "mem-secret"
        mem_dir = tmp_path / "memory"
        mem_dir.mkdir()
        (mem_dir / "rule_one.md").write_text("# Rule one\nFollow it.")
        (mem_dir / "rule_two.md").write_text("# Rule two\nAlso.")
        (mem_dir / "skipme.txt").write_text("not markdown")
        monkeypatch.setenv("MEMORY_DIR", str(mem_dir))
        try:
            code, _, raw = _http_get(
                f"{live_server}/memory?admin_token=mem-secret",
            )
            assert code == 200
            body = json.loads(raw.decode())
            assert "rule_one.md" in body
            assert body["rule_one.md"] == "# Rule one\nFollow it."
            assert "rule_two.md" in body
            assert "skipme.txt" not in body
            assert "__hash__" in body
            assert len(body["__hash__"]) == 32  # md5 hex
        finally:
            mail_module.ADMIN_TOKEN = original


# ───────────────────────────────────────────────────────────────────────
# do_GET dispatch — long tail of GET routes
# ───────────────────────────────────────────────────────────────────────


class TestVersionEndpoint:
    def test_returns_service_metadata(self, live_server, mail_module):
        code, _, raw = _http_get(f"{live_server}/version")
        assert code == 200
        body = json.loads(raw.decode())
        assert body["service"] == "focuslock-mail"
        assert body["version"] == mail_module.__version__
        assert "source_sha256" in body
        assert "git_commit" in body
        assert isinstance(body["uptime_s"], int)
        assert body["uptime_s"] >= 0


class TestPubkeyEndpoint:
    def test_returns_404_when_no_lion_pubkey(self, live_server, mail_module, monkeypatch):
        monkeypatch.setattr(mail_module, "get_lion_pubkey", lambda: "")
        code, _, raw = _http_get(f"{live_server}/pubkey")
        assert code == 404
        body = json.loads(raw.decode())
        assert "no lion pubkey" in body["error"]

    def test_returns_pubkey_text_when_set(self, live_server, mail_module, monkeypatch):
        monkeypatch.setattr(mail_module, "get_lion_pubkey", lambda: "STUB-PUBKEY-TEXT")
        code, headers, raw = _http_get(f"{live_server}/pubkey")
        assert code == 200
        assert "text/plain" in headers.get("Content-Type", "")
        assert raw.decode() == "STUB-PUBKEY-TEXT"


class TestManifestJson:
    def test_returns_pwa_manifest_shape(self, live_server):
        code, headers, raw = _http_get(f"{live_server}/manifest.json")
        assert code == 200
        assert "application/json" in headers.get("Content-Type", "")
        body = json.loads(raw.decode())
        assert body["name"] == "Lion's Share"
        assert body["short_name"] == "Lion's Share"
        assert body["start_url"] == "/"
        assert body["display"] == "standalone"
        assert isinstance(body["icons"], list)
        assert body["icons"][0]["src"] == "/collar-icon.png"


class TestQrcodeMinJs:
    def test_returns_404_when_web_dir_empty(self, live_server, monkeypatch, tmp_path):
        monkeypatch.setenv("FOCUSLOCK_WEB_DIR", str(tmp_path / "empty-web"))
        code, _, raw = _http_get(f"{live_server}/qrcode.min.js")
        assert code == 404
        body = json.loads(raw.decode())
        assert "qrcode.min.js" in body["error"]

    def test_returns_js_content_with_correct_headers_when_present(self, live_server, monkeypatch, tmp_path):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        js_content = b"// stub qrcode.min.js"
        (web_dir / "qrcode.min.js").write_bytes(js_content)
        monkeypatch.setenv("FOCUSLOCK_WEB_DIR", str(web_dir))
        code, headers, raw = _http_get(f"{live_server}/qrcode.min.js")
        assert code == 200
        assert "application/javascript" in headers.get("Content-Type", "")
        assert "max-age=86400" in headers.get("Cache-Control", "")
        assert raw == js_content


class TestCollarIcon:
    def test_returns_404_when_icon_missing(self, live_server, monkeypatch):
        monkeypatch.setattr(
            "os.path.exists",
            lambda p: False if p == "/opt/focuslock/collar-icon.png" else True,
        )
        code, _, _ = _http_get(f"{live_server}/collar-icon.png")
        assert code == 404


class TestWebUIPages:
    @pytest.mark.parametrize(
        "path,fname",
        [
            ("/", "index.html"),
            ("/index.html", "index.html"),
            ("/signup", "signup.html"),
            ("/signup.html", "signup.html"),
            ("/cost", "cost.html"),
            ("/cost.html", "cost.html"),
            ("/trust", "trust.html"),
            ("/trust.html", "trust.html"),
        ],
    )
    def test_returns_html_when_web_dir_has_file(self, live_server, monkeypatch, tmp_path, path, fname):
        web_dir = tmp_path / "web"
        web_dir.mkdir()
        content = f"<html>{fname}</html>".encode()
        (web_dir / fname).write_bytes(content)
        monkeypatch.setenv("FOCUSLOCK_WEB_DIR", str(web_dir))
        code, headers, raw = _http_get(f"{live_server}{path}")
        assert code == 200, f"{path}: {code}"
        assert "text/html" in headers.get("Content-Type", "")
        assert headers.get("X-Frame-Options") == "DENY"
        assert headers.get("X-Content-Type-Options") == "nosniff"
        assert raw == content

    def test_returns_404_when_web_dir_missing(self, live_server, monkeypatch, tmp_path):
        monkeypatch.setenv("FOCUSLOCK_WEB_DIR", str(tmp_path / "no-such-web"))
        code, _, raw = _http_get(f"{live_server}/")
        assert code == 404
        body = json.loads(raw.decode())
        assert "web UI not deployed" in body["error"]


class TestLegacyMeshGoneRoutes:
    @pytest.mark.parametrize(
        "path",
        [
            "/api/mesh/test/sync",
            "/api/mesh/test/order",
            "/api/mesh/test/status",
            "/mesh/sync",
            "/mesh/order",
            "/mesh/status",
        ],
    )
    def test_post_returns_410(self, live_server, path):
        code, body = _http_post(f"{live_server}{path}", {})
        assert code == 410
        assert "gone" in body["error"]

    @pytest.mark.parametrize(
        "path",
        [
            "/api/mesh/test/sync",
            "/api/mesh/test/order",
            "/api/mesh/test/status",
            "/mesh/sync",
            "/mesh/order",
            "/mesh/status",
        ],
    )
    def test_get_returns_410(self, live_server, path):
        code, _, raw = _http_get(f"{live_server}{path}")
        assert code == 410
        body = json.loads(raw.decode())
        assert "gone" in body["error"]


class TestWebLoginInfoPage:
    def test_unknown_session_returns_expired_html(self, live_server):
        code, headers, raw = _http_get(f"{live_server}/web-login?s=ghost")
        assert code == 200
        assert "text/html" in headers.get("Content-Type", "")
        assert headers.get("X-Frame-Options") == "DENY"
        assert b"Session expired" in raw

    def test_unapproved_session_returns_use_lions_share_html(self, live_server, mail_module):
        sid = "session-test-" + str(int(time.time() * 1000))
        mail_module._web_sessions[sid] = {"approved": False, "created_at": time.time()}
        try:
            code, _h, raw = _http_get(f"{live_server}/web-login?s={sid}")
            assert code == 200
            assert b"Use Lion's Share to approve" in raw
        finally:
            mail_module._web_sessions.pop(sid, None)

    def test_approved_session_returns_already_approved_html(self, live_server, mail_module):
        sid = "session-approved-" + str(int(time.time() * 1000))
        mail_module._web_sessions[sid] = {"approved": True, "created_at": time.time()}
        try:
            code, _, raw = _http_get(f"{live_server}/web-login?s={sid}")
            assert code == 200
            assert b"Already approved" in raw
        finally:
            mail_module._web_sessions.pop(sid, None)


class TestApiPaywall:
    def test_returns_paywall_value_as_text(self, live_server, mail_module):
        """mesh_orders is an OrdersDocument — use its set/get API, not item assignment."""
        original = mail_module.mesh_orders.get("paywall")
        mail_module.mesh_orders.set("paywall", "42")
        try:
            code, headers, raw = _http_get(f"{live_server}/api/paywall")
            assert code == 200
            assert "text/plain" in headers.get("Content-Type", "")
            assert headers.get("Access-Control-Allow-Origin") == "*"
            assert raw.decode() == "42"
        finally:
            mail_module.mesh_orders.set("paywall", original if original is not None else "0")

    def test_null_paywall_renders_as_zero(self, live_server, mail_module):
        original = mail_module.mesh_orders.get("paywall")
        mail_module.mesh_orders.set("paywall", "null")
        try:
            code, _, raw = _http_get(f"{live_server}/api/paywall")
            assert code == 200
            assert raw.decode() == "0"
        finally:
            mail_module.mesh_orders.set("paywall", original if original is not None else "0")


class TestController:
    def test_returns_404_when_no_controller_registered(self, live_server, monkeypatch):
        # /controller reads /run/focuslock/controller.json — point at a path
        # that doesn't exist via os.path.exists patch
        monkeypatch.setattr(
            "os.path.exists",
            lambda p: False if p == "/run/focuslock/controller.json" else True,
        )
        code, _, raw = _http_get(f"{live_server}/controller")
        assert code == 404
        body = json.loads(raw.decode())
        assert "no controller registered" in body["error"]

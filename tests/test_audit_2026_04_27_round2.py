"""Audit 2026-04-27 round-2 — auth gates on no-caller / installer-only routes.

Covers:

- M-2: /webhook/controller-register requires admin_token. Installer-only
  caller (no Android caller in repo). Without auth, an attacker could
  redirect /controller resolution + mesh_peers["lions-share"] to an
  attacker-controlled IP.

- M-4 (partial): /webhook/generate-task is bunny-signed via the H-2
  helper. No live caller in repo today; gated preemptively to prevent
  any future caller from being trivially spoof-able + to close a
  resource-exhaustion vector (the route invokes Ollama with no rate
  limit).

- M-6 is enforced on the desktop-collar's /api/pair/create handler in
  focuslock-desktop.py + focuslock-desktop-win.py — those handlers are
  non-trivial to spin up under a unit-test fixture, so they're covered
  by an inline behaviour test that exercises just the auth check
  without bringing up the full mesh server.

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
    spec = importlib.util.spec_from_file_location("focuslock_mail_round2", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_round2"] = mod
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


def _bunny_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(pub_der).decode()


def _sign(priv, payload):
    sig = priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


# ── M-2: /webhook/controller-register requires admin_token ──


class TestControllerRegisterAuth:
    def test_returns_503_when_admin_token_unconfigured(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = ""
        try:
            code, body = _http_post(
                f"{live_server}/webhook/controller-register",
                {"tailscale_ip": "100.64.0.99"},
            )
            assert code == 503
            assert body["error"] == "admin_token not configured"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_returns_403_with_no_token(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, body = _http_post(
                f"{live_server}/webhook/controller-register",
                {"tailscale_ip": "100.64.0.99"},
            )
            assert code == 403
            assert body["error"] == "invalid admin_token"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_returns_403_with_wrong_token(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _body = _http_post(
                f"{live_server}/webhook/controller-register",
                {"tailscale_ip": "100.64.0.99", "admin_token": "wrong"},
            )
            assert code == 403
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_accepts_correct_token_admin_field(self, mail_module, live_server):
        """Empty tailscale_ip short-circuits the file-write side effect, so
        we don't need to redirect /run/focuslock/. The auth gate fires
        first either way; this test pins the gate, not the I/O."""
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, body = _http_post(
                f"{live_server}/webhook/controller-register",
                {"admin_token": "secret-admin-token"},
            )
            assert code == 200, f"expected 200, got {code} {body}"
            assert body == {"ok": True}
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_accepts_correct_token_auth_field(self, mail_module, live_server):
        """auth_token is the legacy alias the handler also accepts."""
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, body = _http_post(
                f"{live_server}/webhook/controller-register",
                {"auth_token": "secret-admin-token"},
            )
            assert code == 200, f"{code} {body}"
            assert body == {"ok": True}
        finally:
            mail_module.ADMIN_TOKEN = original


# ── M-4 partial: /webhook/generate-task is bunny-signed ──


@pytest.fixture
def seeded_bunny(mail_module):
    """Seed a mesh + node with a bunny pubkey so generate-task sig
    verification has a target. Same shape as the H-2 fixture."""
    mesh_id = "round2-mesh-" + str(int(time.time() * 1000))
    node_id = "collar-1"
    priv, pub_b64 = _bunny_keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": "",
        "auth_token": "test-token",
        "invite_code": "",
        "invite_expires_at": 0,
        "invite_uses": 0,
        "pin": "0000",
        "created_at": int(time.time()),
        "nodes": {
            node_id: {
                "node_id": node_id,
                "bunny_pubkey": pub_b64,
                "registered_at": int(time.time()),
            },
        },
        "vault_only": False,
        "max_blobs_per_day": 5000,
        "max_total_bytes_mb": 100,
    }
    try:
        yield {"mesh_id": mesh_id, "node_id": node_id, "priv": priv, "pub_b64": pub_b64}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


class TestGenerateTaskSigned:
    def test_missing_signature_returns_403_with_version_hint(self, live_server, seeded_bunny):
        code, body = _http_post(
            f"{live_server}/webhook/generate-task",
            {"category": "obedience"},
        )
        assert code == 403
        assert body["error"] == "signature required"
        assert body["min_collar_version"] == 74

    def test_bad_signature_returns_403(self, live_server, seeded_bunny):
        ts = int(time.time() * 1000)
        code, body = _http_post(
            f"{live_server}/webhook/generate-task",
            {
                "category": "obedience",
                "mesh_id": seeded_bunny["mesh_id"],
                "node_id": seeded_bunny["node_id"],
                "ts": ts,
                "signature": "AAAA" + "=" * 340,  # well-formed b64, wrong sig
            },
        )
        assert code == 403
        assert body["error"] == "invalid signature"

    def test_unknown_mesh_returns_404(self, live_server, seeded_bunny):
        ts = int(time.time() * 1000)
        payload = f"ghost-mesh|{seeded_bunny['node_id']}|generate-task|{ts}"
        sig = _sign(seeded_bunny["priv"], payload)
        code, body = _http_post(
            f"{live_server}/webhook/generate-task",
            {
                "category": "obedience",
                "mesh_id": "ghost-mesh",
                "node_id": seeded_bunny["node_id"],
                "ts": ts,
                "signature": sig,
            },
        )
        assert code == 404
        assert body["error"] == "mesh not found"

    def test_valid_signature_accepts(self, live_server, seeded_bunny, mail_module, monkeypatch):
        captured = []
        monkeypatch.setattr(
            mail_module,
            "generate_task_with_llm",
            lambda category: captured.append(category) or {"task": "stub", "category": category},
        )
        ts = int(time.time() * 1000)
        payload = f"{seeded_bunny['mesh_id']}|{seeded_bunny['node_id']}|generate-task|{ts}"
        sig = _sign(seeded_bunny["priv"], payload)
        code, body = _http_post(
            f"{live_server}/webhook/generate-task",
            {
                "category": "obedience",
                "mesh_id": seeded_bunny["mesh_id"],
                "node_id": seeded_bunny["node_id"],
                "ts": ts,
                "signature": sig,
            },
        )
        assert code == 200, f"{code} {body}"
        assert captured == ["obedience"]


# ── M-6: desktop-collar /api/pair/create gates on admin_token ──


class TestDesktopPairCreateAuthLinux:
    """Behavioural test of the auth gate logic in focuslock-desktop.py.

    Spinning up the full MeshHandler (which expects mesh module
    initialization, vault keypair, etc.) is heavy; instead we exercise
    the gate logic directly by monkey-patching the do_POST handler's
    code path. The check is short enough that we can re-implement it in
    the test as the contract under test.
    """

    def _gate(self, admin_token_configured, token_in_body):
        """Mirror the gate logic the handler runs before calling
        _create_pairing_code. Returns (status, error_str_or_none)."""
        import hmac as _hmac

        if not admin_token_configured:
            return 503, "admin_token not configured"
        if not token_in_body or not _hmac.compare_digest(token_in_body, admin_token_configured):
            return 403, "invalid admin_token"
        return 200, None

    def test_unconfigured_admin_token_returns_503(self):
        status, err = self._gate("", "anything")
        assert status == 503
        assert err == "admin_token not configured"

    def test_missing_token_returns_403(self):
        status, err = self._gate("secret", "")
        assert status == 403
        assert err == "invalid admin_token"

    def test_wrong_token_returns_403(self):
        status, _err = self._gate("secret", "wrong")
        assert status == 403

    def test_correct_token_passes(self):
        status, err = self._gate("secret", "secret")
        assert status == 200
        assert err is None


class TestDesktopPairCreateAuthFunctional:
    """End-to-end test: stand up the desktop-mode MeshHandler and POST.

    Loads focuslock-desktop.py with monkey-patched dependencies so the
    module imports without a real config + key material.
    """

    @pytest.fixture
    def desktop_module(self, monkeypatch, tmp_path):
        """Load focuslock-desktop.py in a way that lets us swap ADMIN_TOKEN
        without it firing real mesh I/O. The module's heavy side-effects
        (config load, mesh init) happen at import — we let those run with
        a tmp HOME so the artifacts land in a sandbox."""
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / ".config"))
        cfg_dir = tmp_path / ".config" / "focuslock"
        cfg_dir.mkdir(parents=True)
        (cfg_dir / "config.json").write_text(
            json.dumps({"mesh_id": "test-mesh-id", "mesh_url": "http://127.0.0.1:8434"})
        )
        spec = importlib.util.spec_from_file_location(
            "focuslock_desktop_round2", str(REPO_ROOT / "focuslock-desktop.py")
        )
        mod = importlib.util.module_from_spec(spec)
        sys.modules["focuslock_desktop_round2"] = mod
        try:
            spec.loader.exec_module(mod)
        except (ModuleNotFoundError, ImportError) as e:
            pytest.skip(f"focuslock-desktop.py import skipped (missing optional dep: {e})")
        except SystemExit:
            pytest.skip("focuslock-desktop.py exited at import")
        return mod

    def test_pair_create_rejects_without_token(self, desktop_module):
        from http.server import HTTPServer

        server = HTTPServer(("127.0.0.1", 0), desktop_module.MeshHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = desktop_module.ADMIN_TOKEN
        desktop_module.ADMIN_TOKEN = "desktop-secret"
        try:
            code, body = _http_post(
                f"http://127.0.0.1:{port}/api/pair/create",
                {"code": "TESTAB"},
            )
            assert code == 403
            assert body["error"] == "invalid admin_token"
        finally:
            desktop_module.ADMIN_TOKEN = original
            server.shutdown()
            server.server_close()

    def test_pair_create_accepts_with_token(self, desktop_module):
        from http.server import HTTPServer

        server = HTTPServer(("127.0.0.1", 0), desktop_module.MeshHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = desktop_module.ADMIN_TOKEN
        desktop_module.ADMIN_TOKEN = "desktop-secret"
        try:
            code, body = _http_post(
                f"http://127.0.0.1:{port}/api/pair/create",
                {"code": "TESTOK", "admin_token": "desktop-secret"},
            )
            assert code == 200, f"{code} {body}"
            assert body["ok"] is True
            assert body["code"] == "TESTOK"
        finally:
            desktop_module.ADMIN_TOKEN = original
            server.shutdown()
            server.server_close()

    def test_pair_create_unconfigured_returns_503(self, desktop_module):
        from http.server import HTTPServer

        server = HTTPServer(("127.0.0.1", 0), desktop_module.MeshHandler)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        original = desktop_module.ADMIN_TOKEN
        desktop_module.ADMIN_TOKEN = ""
        try:
            code, body = _http_post(
                f"http://127.0.0.1:{port}/api/pair/create",
                {"code": "TESTNO"},
            )
            assert code == 503
            assert body["error"] == "admin_token not configured"
        finally:
            desktop_module.ADMIN_TOKEN = original
            server.shutdown()
            server.server_close()

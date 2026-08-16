"""Stream C — Smoke tests for the small public/static routes that have
no direct test coverage. These exist to catch dispatch regressions
(typos in the elif chain, accidental gating of a public route) rather
than deep behavior. Each route gets a 200/404 expectation matching its
current contract, with content-shape assertions where they're cheap.

Routes covered:
  GET /version       (focuslock-mail.py:5211) — public, returns build info
  GET /mesh/ping     (focuslock-mail.py:5201) — public mesh discovery
  GET /pubkey        (focuslock-mail.py:5550) — Lion pubkey or 404
  GET /api/paywall   (focuslock-mail.py:5655) — text/plain paywall amount
  GET /web-login     (focuslock-mail.py:5262) — info HTML page (always 200)
  POST /api/logout   (focuslock-mail.py:3362) — revokes session token
"""

import importlib.util
import json
import sys
import threading
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_public", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_public"] = mod
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


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            return r.status, r.headers.get("Content-Type", ""), r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Content-Type", "") if e.headers else "", e.read()


def _post(url, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestVersion:
    def test_returns_200_with_build_info(self, live_server, mail_module):
        status, ctype, body = _get(f"{live_server}/version")
        assert status == 200
        assert "json" in ctype.lower()
        data = json.loads(body.decode())
        assert data["service"] == "focuslock-mail"
        # Mandatory fields per the audit-transparency contract.
        for key in ("version", "source_sha256", "git_commit", "vault_mode_allowed", "uptime_s"):
            assert key in data, f"missing {key} in /version response"
        assert isinstance(data["uptime_s"], int)


class TestMeshPing:
    def test_returns_200(self, live_server):
        status, _ctype, body = _get(f"{live_server}/mesh/ping")
        assert status == 200
        # mesh.handle_mesh_ping returns a JSON dict.
        data = json.loads(body.decode())
        assert isinstance(data, dict)


class TestPubkey:
    def test_returns_404_when_no_lion_pubkey(self, live_server, mail_module, monkeypatch):
        monkeypatch.setattr(mail_module, "get_lion_pubkey", lambda: None)
        status, _ctype, body = _get(f"{live_server}/pubkey")
        assert status == 404
        assert b"lion pubkey" in body.lower() or b"no" in body.lower()

    def test_returns_200_text_plain_when_lion_pubkey_present(self, live_server, mail_module, monkeypatch):
        fake_pem = "-----BEGIN PUBLIC KEY-----\nFAKE\n-----END PUBLIC KEY-----\n"
        monkeypatch.setattr(mail_module, "get_lion_pubkey", lambda: fake_pem)
        status, ctype, body = _get(f"{live_server}/pubkey")
        assert status == 200
        assert "text/plain" in ctype.lower()
        assert body.decode() == fake_pem


class TestApiPaywall:
    def test_returns_text_plain_paywall_amount(self, live_server, mail_module):
        # Set the operator orders' paywall to a known value, then read it.
        from focuslock_mail_public import mesh_orders  # type: ignore[attr-defined]

        original = mesh_orders.get("paywall", "0")
        mesh_orders.set("paywall", "42.50")
        try:
            status, ctype, body = _get(f"{live_server}/api/paywall")
            assert status == 200
            assert "text/plain" in ctype.lower()
            assert body.decode() == "42.50"
        finally:
            mesh_orders.set("paywall", original)

    def test_normalizes_null_to_zero(self, live_server, mail_module):
        from focuslock_mail_public import mesh_orders  # type: ignore[attr-defined]

        original = mesh_orders.get("paywall", "0")
        mesh_orders.set("paywall", "null")
        try:
            status, _ctype, body = _get(f"{live_server}/api/paywall")
            assert status == 200
            assert body.decode() == "0"
        finally:
            mesh_orders.set("paywall", original)


class TestWebLogin:
    def test_returns_200_with_session_expired_when_no_session(self, live_server):
        status, _ctype, body = _get(f"{live_server}/web-login?s=nope")
        # The handler always returns 200 for this surface — even on
        # missing/expired session it returns an info HTML page.
        assert status == 200
        assert b"<" in body  # some HTML
        assert b"Session" in body or b"session" in body or b"refresh" in body.lower()


class TestControllerIsNoLongerPublic:
    """`/controller` was moved out of this file's contract on 2026-08-17.

    It returns the Lion's controller address on the mesh. The write side has
    been admin-gated since audit 2026-04-27 M-2 — an unauth caller must not get
    to choose the address controller resolution hands back — but the read side
    stayed open, serving that address to anyone who could reach the relay, and
    the relay is on public HTTPS. The 404 this test used to assert was not a
    contract; it was `controller.json` living on tmpfs and a reboot having
    wiped it.
    """

    def _no_registry_file(self, monkeypatch):
        import os as _os

        real_exists = _os.path.exists

        def patched_exists(p):
            if p == "/run/focuslock/controller.json":
                return False
            return real_exists(p)

        monkeypatch.setattr(_os.path, "exists", patched_exists)

    def test_unauthenticated_read_is_refused(self, live_server, mail_module, monkeypatch):
        """The point of the change: no token, no address."""
        self._no_registry_file(monkeypatch)
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, _ctype, body = _get(f"{live_server}/controller")
            assert status == 403
            assert json.loads(body.decode())["error"] == "invalid admin_token"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_wrong_token_is_refused(self, live_server, mail_module, monkeypatch):
        self._no_registry_file(monkeypatch)
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, _ctype, body = _get(f"{live_server}/controller?admin_token=wrong")
            assert status == 403
            assert json.loads(body.decode())["error"] == "invalid admin_token"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_503_when_admin_token_unconfigured(self, live_server, mail_module, monkeypatch):
        """Fails closed rather than falling open to the old public behavior."""
        self._no_registry_file(monkeypatch)
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = ""
        try:
            status, _ctype, body = _get(f"{live_server}/controller")
            assert status == 503
            assert json.loads(body.decode())["error"] == "admin_token not configured"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_query_token_still_reaches_the_handler(self, live_server, mail_module, monkeypatch):
        """The path now carries a query string — the dispatch had to stop
        comparing the raw path or the route would 404 on every authed call."""
        self._no_registry_file(monkeypatch)
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, _ctype, body = _get(f"{live_server}/controller?admin_token=secret-admin-token")
            assert status == 404
            assert "no controller" in json.loads(body.decode())["error"].lower()
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_bearer_header_is_accepted(self, live_server, mail_module, monkeypatch):
        self._no_registry_file(monkeypatch)
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            req = urllib.request.Request(
                f"{live_server}/controller",
                headers={"Authorization": "Bearer secret-admin-token"},
            )
            try:
                with urllib.request.urlopen(req, timeout=5) as r:
                    status, payload = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, payload = e.code, e.read()
            assert status == 404
            assert "no controller" in json.loads(payload.decode())["error"].lower()
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_registered_address_is_served_to_an_authed_caller(self, live_server, mail_module, monkeypatch, tmp_path):
        """Gating must not break the caller it exists for (scripts/release.sh)."""
        import os as _os

        reg = tmp_path / "controller.json"
        reg.write_text(json.dumps({"tailscale_ip": "100.64.0.99", "last_seen": 1}))
        real_exists, real_open = _os.path.exists, open

        def patched_exists(p):
            return True if p == "/run/focuslock/controller.json" else real_exists(p)

        def patched_open(p, *a, **kw):
            if p == "/run/focuslock/controller.json":
                return real_open(reg, *a, **kw)
            return real_open(p, *a, **kw)

        monkeypatch.setattr(_os.path, "exists", patched_exists)
        monkeypatch.setattr("builtins.open", patched_open)
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, _ctype, body = _get(f"{live_server}/controller?admin_token=secret-admin-token")
            assert status == 200
            assert json.loads(body.decode())["tailscale_ip"] == "100.64.0.99"
        finally:
            mail_module.ADMIN_TOKEN = original


class TestApiLogout:
    def test_logout_returns_ok_with_token(self, live_server, mail_module):
        # Issue a session token, then revoke it via /api/logout.
        token = mail_module._issue_session_token("sess-test", "")
        assert token in mail_module._active_session_tokens
        status, body = _post(f"{live_server}/api/logout", {"token": token})
        assert status == 200
        assert body["ok"] is True
        assert token not in mail_module._active_session_tokens

    def test_logout_returns_ok_without_token(self, live_server):
        # Empty body still returns 200 (idempotent — nothing to revoke).
        status, body = _post(f"{live_server}/api/logout", {})
        assert status == 200
        assert body["ok"] is True

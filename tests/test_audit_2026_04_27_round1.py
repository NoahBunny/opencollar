"""Regression tests for the 2026-04-27 audit, round-1 fixes.

Covers:
- H-1: /enforcement-orders requires admin_token (503 if unconfigured,
  403 if wrong, 200 if right). The route's own contract claims it
  serves the master admin token; the gate is defense-in-depth so the
  endpoint isn't reachable purely via reverse-proxy posture.
- L-3: OPERATOR_MESH_ID is validated by _safe_mesh_id_static (the
  module raises at import if the configured id contains '..' / '/'
  etc.). Tested via direct call to the validator since the import is
  module-load.
- M-3: /webhook/register rejects invalid device_id (path-shaped strings,
  newlines, empty, too long) since the value lands as a JSON key in
  IP_REGISTRY_FILE.

The full audit report is at docs/AUDIT-FINDINGS-2026-04-27.md.
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_round1", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_round1"] = mod
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


# ── H-1: /enforcement-orders auth gate ──


class TestEnforcementOrdersAuth:
    def test_returns_503_when_admin_token_unconfigured(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = ""
        try:
            code, _ = _http_get(f"{live_server}/enforcement-orders")
            assert code == 503
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_returns_403_with_no_token(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(f"{live_server}/enforcement-orders")
            assert code == 403
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_returns_403_with_wrong_token_query_param(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(f"{live_server}/enforcement-orders?admin_token=wrong")
            assert code == 403
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_returns_403_with_wrong_bearer_header(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(
                f"{live_server}/enforcement-orders",
                headers={"Authorization": "Bearer wrong"},
            )
            assert code == 403
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_accepts_correct_token_query_param(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            # CLAUDE.md may or may not exist locally. The auth gate fires
            # before the file read either way — we only assert the gate
            # didn't reject; the body itself can be plain text.
            code, _ = _http_get(f"{live_server}/enforcement-orders?admin_token=secret-admin-token")
            assert code == 200
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_accepts_correct_token_bearer_header(self, mail_module, live_server):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            code, _ = _http_get(
                f"{live_server}/enforcement-orders",
                headers={"Authorization": "Bearer secret-admin-token"},
            )
            assert code == 200
        finally:
            mail_module.ADMIN_TOKEN = original


# ── L-3: OPERATOR_MESH_ID validation ──


class TestOperatorMeshIdValidator:
    def test_safe_mesh_id_static_rejects_path_traversal(self, mail_module):
        assert not mail_module._safe_mesh_id_static("../../etc/passwd")
        assert not mail_module._safe_mesh_id_static("..")
        assert not mail_module._safe_mesh_id_static("foo/bar")
        assert not mail_module._safe_mesh_id_static("foo\\bar")

    def test_safe_mesh_id_static_rejects_newlines(self, mail_module):
        assert not mail_module._safe_mesh_id_static("foo\nbar")
        assert not mail_module._safe_mesh_id_static("foo\rbar")
        assert not mail_module._safe_mesh_id_static("foo\x00bar")

    def test_safe_mesh_id_static_rejects_overlong(self, mail_module):
        assert not mail_module._safe_mesh_id_static("a" * 65)

    def test_safe_mesh_id_static_accepts_legitimate_shapes(self, mail_module):
        # Real mesh IDs are base64url-unpadded sha256 prefixes, ≤ 16 chars
        assert mail_module._safe_mesh_id_static("DNfs4xCZM-HY1234")
        assert mail_module._safe_mesh_id_static("abc_def-123")
        assert mail_module._safe_mesh_id_static("a")

    def test_safe_mesh_id_static_rejects_empty_or_nonstring(self, mail_module):
        assert not mail_module._safe_mesh_id_static("")
        assert not mail_module._safe_mesh_id_static(None)
        assert not mail_module._safe_mesh_id_static(123)


# ── M-3: /webhook/register device_id validation ──


class TestRegisterDeviceIdValidator:
    @pytest.mark.parametrize(
        "bad_device_id",
        [
            "../../etc/passwd",
            "../escape",
            "name with spaces",
            "name\nwith\nnewlines",
            "name\x00with\x00null",
            "name\rwith\rcr",
            "",
            "a" * 65,  # too long
            None,
            123,
        ],
    )
    def test_rejects_unsafe_device_id(self, live_server, bad_device_id):
        code, body = _http_post(
            f"{live_server}/webhook/register",
            {"lan_ip": "10.0.0.1", "tailscale_ip": "100.64.0.1", "device_id": bad_device_id},
        )
        assert code == 400, f"expected 400 for device_id={bad_device_id!r}, got {code} {body}"

    @pytest.mark.parametrize(
        "good_device_id",
        [
            "Pixel-7",
            "Galaxy.S23",
            "phone_main",
            "abc-123",
            "a",
            "ABCD-1234-5678-9012",
        ],
    )
    def test_accepts_safe_device_id(self, live_server, good_device_id):
        code, body = _http_post(
            f"{live_server}/webhook/register",
            {"lan_ip": "10.0.0.1", "tailscale_ip": "100.64.0.1", "device_id": good_device_id},
        )
        assert code == 200, f"expected 200 for device_id={good_device_id!r}, got {code} {body}"
        assert body.get("ok") is True

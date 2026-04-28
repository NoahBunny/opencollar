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
# The parametrized validator matrix moved to
# tests/test_audit_2026_04_27_round3.py::TestRegisterDeviceIdValidatorSigned
# when round-3 added the bunny-sig gate to /webhook/register. The
# validator is still in force as defense-in-depth on top of the sig
# check; the matrix exercises both layers together.

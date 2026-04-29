"""Stream C — Coverage push for /admin/disposal-token (focuslock-mail.py:3171).

The disposal-token endpoint mints single-use tokens that can ONLY drive
the `add-paywall` action up to a per-token max amount. They are the
enforcement surface for the watchdog timer's "Claude went silent →
$25 fine" path. Three invariants matter:

1. Admin-token gated (master + scoped session both work via _is_valid_admin_auth).
2. max_amount is clamped to 200 server-side; ttl clamped to 7200.
3. Each issued token is independently usable; expired/used entries get
   GC'd opportunistically on each new mint.

This file pins the mint side. The use side (single-use burn,
add-paywall-only invariant) lives in tests/test_paywall_hardening.py
where the watchdog flow is already exercised end-to-end.
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_disposal", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_disposal"] = mod
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


class TestDisposalTokenMint:
    def test_503_when_admin_token_unconfigured(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = ""
        try:
            status, body = _post(f"{live_server}/admin/disposal-token", {"admin_token": "x"})
            assert status == 503
            assert body["error"] == "admin_token not configured"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_403_on_bad_token(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, body = _post(f"{live_server}/admin/disposal-token", {"admin_token": "wrong"})
            assert status == 403
            assert body["error"] == "invalid admin_token"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_valid_request_mints_token(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, body = _post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "secret-admin-token", "max_amount": 25, "ttl": 600},
            )
            assert status == 200
            assert body["disposal_token"]
            assert len(body["disposal_token"]) >= 32  # secrets.token_urlsafe(32)
            assert body["max_amount"] == 25
            assert body["expires_in"] == 600
            # Token is now in the registry.
            assert body["disposal_token"] in mail_module._disposal_tokens
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_max_amount_clamped_to_200(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, body = _post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "secret-admin-token", "max_amount": 9999, "ttl": 60},
            )
            assert status == 200
            assert body["max_amount"] == 200, "max_amount must clamp to 200"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_ttl_clamped_to_7200(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, body = _post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "secret-admin-token", "max_amount": 10, "ttl": 99999},
            )
            assert status == 200
            assert body["expires_in"] == 7200, "ttl must clamp to 7200"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_defaults_when_max_amount_and_ttl_omitted(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, body = _post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "secret-admin-token"},
            )
            assert status == 200
            # Defaults: max_amount=50, ttl=3600 per source line 3179-3180.
            assert body["max_amount"] == 50
            assert body["expires_in"] == 3600
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_two_mints_yield_distinct_tokens(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            _, body1 = _post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "secret-admin-token"},
            )
            _, body2 = _post(
                f"{live_server}/admin/disposal-token",
                {"admin_token": "secret-admin-token"},
            )
            assert body1["disposal_token"] != body2["disposal_token"]
        finally:
            mail_module.ADMIN_TOKEN = original

"""Stream C — Coverage push for two webhooks not pinned by the
2026-04-27 audit suite:

  POST /webhook/entrap     (focuslock-mail.py:2934) — admin_token-gated
  POST /webhook/location   (focuslock-mail.py:2958) — currently public,
                              just logs lat/lon. Audit L-2 (deferred)
                              tracks tightening this to a signed
                              /api/location route. The smoke test here
                              pins current behavior so any future gating
                              change shows up as an explicit test diff.

The other previously-uncovered webhooks (subscription-charge,
bunny-message, controller-register) are already pinned by
test_audit_2026_04_27_h2_evidence_webhooks.py +
test_audit_2026_04_27_round2.py + tests/test_pairing.py.
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_uncov_webhooks", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_uncov_webhooks"] = mod
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


# ── /webhook/entrap (admin-token gated) ───────────────────────────────────


class TestEntrapWebhook:
    def test_503_when_admin_token_unconfigured(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = ""
        try:
            status, body = _post(f"{live_server}/webhook/entrap", {"admin_token": "x"})
            assert status == 503
            assert body["error"] == "admin_token not configured"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_403_on_bad_token(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, body = _post(f"{live_server}/webhook/entrap", {"admin_token": "wrong"})
            assert status == 403
            assert body["error"] == "invalid admin_token"
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_403_on_missing_token(self, live_server, mail_module):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        try:
            status, _body = _post(f"{live_server}/webhook/entrap", {})
            assert status == 403
        finally:
            mail_module.ADMIN_TOKEN = original

    def test_valid_token_fires_jail_and_evidence(self, live_server, mail_module, monkeypatch):
        original = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "secret-admin-token"
        jail_calls = []
        evidence_calls = []
        monkeypatch.setattr(mail_module, "enforce_jail", lambda: jail_calls.append(True))
        monkeypatch.setattr(mail_module, "send_evidence", lambda body, kind: evidence_calls.append((body, kind)))
        try:
            status, body = _post(f"{live_server}/webhook/entrap", {"admin_token": "secret-admin-token"})
            assert status == 200, f"got {status} {body}"
            assert body["ok"] is True
            assert len(jail_calls) == 1, "enforce_jail should have fired exactly once"
            assert len(evidence_calls) == 1
            assert evidence_calls[0][1] == "entrap"
        finally:
            mail_module.ADMIN_TOKEN = original


# ── /webhook/location (currently public — pin behavior) ───────────────────


class TestLocationWebhook:
    def test_accepts_unsigned_lat_lon(self, live_server, mail_module, monkeypatch):
        # Pin current "public, just logs" behavior. Audit L-2 (deferred)
        # tracks tightening this to a signed /api/location path.
        log_calls = []
        monkeypatch.setattr(mail_module.logger, "info", lambda msg, *a: log_calls.append((msg, a)))
        status, body = _post(f"{live_server}/webhook/location", {"lat": 40.7, "lon": -74.0})
        assert status == 200
        assert body["ok"] is True

    def test_accepts_empty_body(self, live_server, mail_module):
        # Defaults to 0,0 — handler doesn't validate; this pin catches if
        # someone adds validation without updating the contract.
        status, body = _post(f"{live_server}/webhook/location", {})
        assert status == 200
        assert body["ok"] is True

"""Server-authoritative tamper ratchet (deferred follow-up from the
2026-08-07 ecosystem-review fix-forward).

`report_tamper.py` used to hold its escalating-penalty counter in a file
next to the desktop collar's config — on a machine the bunny has root on.
`rm ~/.config/focuslock/tamper-attempts.json` walked every future tamper
penalty back to the $5 tier-1 floor. The authoritative counter now lives on
the relay, per mesh; the client's count is only a hint that can fast-forward
the server, never lower it, and a client-supplied `amount` is a floor rather
than a discount.

These tests pin the ratchet's one-directionality end-to-end through real
HTTP, plus the client-side payload contract that carries it.
"""

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

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_tamper", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_tamper"] = mod
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


@pytest.fixture
def penalty_mesh(mail_module, monkeypatch, tmp_path):
    """A mesh whose paywall starts at $0, a known ADMIN_TOKEN, and a tamper
    counter directory scoped to this test."""
    store = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "accounts"))
    acct = store.create("lion-tamper", pin="4242")
    mesh_id = acct["mesh_id"]
    orders = mail_module._orders_registry.get_or_create(mesh_id)
    orders.set("paywall", "0")

    token = "test-tamper-" + str(int(time.time() * 1000))
    monkeypatch.setattr(mail_module, "ADMIN_TOKEN", token)
    monkeypatch.setattr(mail_module, "_mesh_accounts", store)
    monkeypatch.setattr(mail_module, "_TAMPER_TIERS_DIR", str(tmp_path / "tamper_tiers"))
    try:
        yield {"mesh_id": mesh_id, "token": token}
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)


def _report(live_server, penalty_mesh, **extra):
    body = {
        "admin_token": penalty_mesh["token"],
        "mesh_id": penalty_mesh["mesh_id"],
        "reason": "test tamper",
    }
    body.update(extra)
    return _post(f"{live_server}/webhook/desktop-penalty", body)


# ── Tier formula ──────────────────────────────────────────────────────────


class TestTamperPenaltyFormula:
    def test_tiers_of_three_at_five_dollars_a_step(self):
        from focuslock_penalties import tamper_penalty

        assert [tamper_penalty(n) for n in (1, 2, 3)] == [5, 5, 5]
        assert [tamper_penalty(n) for n in (4, 5, 6)] == [10, 10, 10]
        assert tamper_penalty(7) == 15
        assert tamper_penalty(0) == 0

    def test_matches_the_escape_formula_it_mirrors(self):
        from focuslock_penalties import escape_penalty, tamper_penalty

        assert [tamper_penalty(n) for n in range(1, 13)] == [escape_penalty(n) for n in range(1, 13)]


# ── Counter store ─────────────────────────────────────────────────────────


class TestTamperCounterStore:
    def test_bump_increments_and_persists(self, mail_module, monkeypatch, tmp_path):
        monkeypatch.setattr(mail_module, "_TAMPER_TIERS_DIR", str(tmp_path))
        assert mail_module._read_tamper_attempts("mesh-a") == 0
        assert mail_module._bump_tamper_attempts("mesh-a") == 1
        assert mail_module._bump_tamper_attempts("mesh-a") == 2
        assert mail_module._read_tamper_attempts("mesh-a") == 2

    def test_counters_are_per_mesh(self, mail_module, monkeypatch, tmp_path):
        monkeypatch.setattr(mail_module, "_TAMPER_TIERS_DIR", str(tmp_path))
        mail_module._bump_tamper_attempts("mesh-a")
        mail_module._bump_tamper_attempts("mesh-a")
        assert mail_module._read_tamper_attempts("mesh-b") == 0

    def test_client_claim_fast_forwards_a_relay_that_lost_state(self, mail_module, monkeypatch, tmp_path):
        """Relay reinstall wiped the counter but the desktop still remembers
        9 attempts — the ratchet must resume at 9, not restart at 1."""
        monkeypatch.setattr(mail_module, "_TAMPER_TIERS_DIR", str(tmp_path))
        assert mail_module._bump_tamper_attempts("mesh-a", at_least=9) == 9
        assert mail_module._read_tamper_attempts("mesh-a") == 9

    def test_client_claim_never_lowers_the_counter(self, mail_module, monkeypatch, tmp_path):
        monkeypatch.setattr(mail_module, "_TAMPER_TIERS_DIR", str(tmp_path))
        for _ in range(5):
            mail_module._bump_tamper_attempts("mesh-a")
        # Bunny wiped their local file and now claims this is attempt #1.
        assert mail_module._bump_tamper_attempts("mesh-a", at_least=1) == 6

    def test_unsafe_mesh_id_does_not_write_outside_the_directory(self, mail_module, monkeypatch, tmp_path):
        monkeypatch.setattr(mail_module, "_TAMPER_TIERS_DIR", str(tmp_path))
        assert mail_module._tamper_counter_path("../../etc/passwd") is None
        # Still returns a usable attempt number rather than raising.
        assert mail_module._bump_tamper_attempts("../../etc/passwd") == 1
        assert list(tmp_path.glob("*")) == []


# ── Endpoint behaviour ────────────────────────────────────────────────────


class TestDesktopPenaltyRatchet:
    def test_escalates_even_when_the_client_counter_is_wiped_every_time(self, live_server, penalty_mesh):
        """The regression this whole change exists for: a bunny who deletes
        tamper-attempts.json before each attempt still climbs the tiers."""
        charged = []
        for _ in range(4):
            status, body = _report(live_server, penalty_mesh, tamper=True, attempt=1, amount=5)
            assert status == 200, body
            charged.append(body["amount"])
        assert charged == [5, 5, 5, 10]

    def test_attempt_number_is_reported_back(self, live_server, penalty_mesh):
        _report(live_server, penalty_mesh, tamper=True, attempt=1, amount=5)
        status, body = _report(live_server, penalty_mesh, tamper=True, attempt=1, amount=5)
        assert status == 200
        assert body["tamper_attempt"] == 2

    def test_paywall_receives_the_server_priced_amount(self, live_server, penalty_mesh, mail_module):
        for _ in range(4):
            _report(live_server, penalty_mesh, tamper=True, attempt=1, amount=5)
        pw = int(mail_module._orders_registry.get(penalty_mesh["mesh_id"]).get("paywall", "0"))
        assert pw == 5 + 5 + 5 + 10

    def test_caller_amount_is_a_floor_not_a_discount(self, live_server, penalty_mesh):
        # Climb to tier 2 ($10), then ask to be charged $1.
        for _ in range(3):
            _report(live_server, penalty_mesh, tamper=True, attempt=1, amount=5)
        status, body = _report(live_server, penalty_mesh, tamper=True, attempt=1, amount=1)
        assert status == 200
        assert body["amount"] == 10

    def test_caller_amount_can_still_price_a_known_incident_higher(self, live_server, penalty_mesh):
        status, body = _report(live_server, penalty_mesh, tamper=True, attempt=1, amount=50)
        assert status == 200
        assert body["amount"] == 50  # tier 1 would be $5

    def test_absurd_client_claim_is_clamped(self, live_server, penalty_mesh, mail_module):
        """A corrupt/hostile client can fast-forward the ratchet, but only by
        a bounded jump — it can't pin the mesh at the $500 cap forever."""
        status, body = _report(live_server, penalty_mesh, tamper=True, attempt=10**9)
        assert status == 200
        assert body["tamper_attempt"] == 100

    def test_untagged_penalty_does_not_touch_the_ratchet(self, live_server, penalty_mesh, mail_module):
        """The desktop collar's flat $30 consent-decline penalty is not a
        tamper attempt and must neither escalate nor be escalated."""
        status, body = _report(live_server, penalty_mesh, amount=30)
        assert status == 200
        assert body["amount"] == 30
        assert body["tamper_attempt"] is None
        assert mail_module._read_tamper_attempts(penalty_mesh["mesh_id"]) == 0

    def test_amountless_tamper_report_is_priced_entirely_by_the_server(self, live_server, penalty_mesh):
        status, body = _report(live_server, penalty_mesh, tamper=True)
        assert status == 200
        assert body["amount"] == 5  # not the historical amount-less default of $30

    def test_ratchet_respects_the_penalty_ceiling(self, live_server, penalty_mesh, mail_module):
        mail_module._bump_tamper_attempts(penalty_mesh["mesh_id"], at_least=100_000)
        status, body = _report(live_server, penalty_mesh, tamper=True, attempt=1)
        assert status == 200
        assert body["amount"] == 500  # DESKTOP_PENALTY_MAX, not 5 x tier


# ── Client payload contract ───────────────────────────────────────────────


class TestReportTamperClient:
    @pytest.fixture
    def tamper_cli(self, monkeypatch, tmp_path):
        import report_tamper

        monkeypatch.setattr(report_tamper, "_config_path", lambda: str(tmp_path / "config.json"))
        monkeypatch.setenv("FOCUSLOCK_ADMIN_TOKEN", "tok")
        monkeypatch.setenv("FOCUSLOCK_MESH_URL", "http://relay.invalid")
        monkeypatch.setenv("FOCUSLOCK_MESH_ID", "mesh-x")
        return report_tamper

    def _capture(self, monkeypatch, tamper_cli, response):
        sent = {}

        class _Resp:
            def read(self):
                return json.dumps(response).encode()

        def _fake_urlopen(req, timeout=None):
            sent["url"] = req.full_url
            sent["body"] = json.loads(req.data.decode())
            return _Resp()

        monkeypatch.setattr(tamper_cli.urllib.request, "urlopen", _fake_urlopen)
        return sent

    def test_marks_the_report_as_tamper_and_carries_the_local_count(self, monkeypatch, tamper_cli):
        sent = self._capture(monkeypatch, tamper_cli, {"new_paywall": 5, "amount": 5, "tamper_attempt": 1})
        assert tamper_cli.report_tamper(None, "test") == 0
        assert sent["body"]["tamper"] is True
        assert sent["body"]["attempt"] == 1
        assert sent["body"]["amount"] == 5  # local computation, for older relays

    def test_explicit_amount_still_rides_the_ratchet(self, monkeypatch, tamper_cli):
        sent = self._capture(monkeypatch, tamper_cli, {"new_paywall": 50, "amount": 50, "tamper_attempt": 1})
        assert tamper_cli.report_tamper(50, "test") == 0
        assert sent["body"]["tamper"] is True
        assert sent["body"]["attempt"] == 1
        assert sent["body"]["amount"] == 50
        # …and the incident is recorded locally, so the next auto report is #2.
        assert tamper_cli._read_attempt_count() == 1

    def test_adopts_the_server_attempt_number_when_it_is_ahead(self, monkeypatch, tamper_cli):
        self._capture(monkeypatch, tamper_cli, {"new_paywall": 20, "amount": 20, "tamper_attempt": 11})
        assert tamper_cli.report_tamper(None, "test") == 0
        assert tamper_cli._read_attempt_count() == 11

    def test_local_counter_never_walks_backwards(self, monkeypatch, tamper_cli):
        self._capture(monkeypatch, tamper_cli, {"new_paywall": 20, "amount": 20, "tamper_attempt": 11})
        tamper_cli.report_tamper(None, "test")
        # An older relay echoes nothing; the local count must not regress to 12→2.
        self._capture(monkeypatch, tamper_cli, {"new_paywall": 25})
        tamper_cli.report_tamper(None, "test")
        assert tamper_cli._read_attempt_count() == 12

    def test_failed_report_does_not_advance_the_counter(self, monkeypatch, tamper_cli):
        def _boom(req, timeout=None):
            raise urllib.error.URLError("relay down")

        monkeypatch.setattr(tamper_cli.urllib.request, "urlopen", _boom)
        assert tamper_cli.report_tamper(None, "test") == 1
        assert tamper_cli._read_attempt_count() == 0

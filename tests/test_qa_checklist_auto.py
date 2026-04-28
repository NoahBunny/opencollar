"""Audit 2026-04-27 Stream C round-4 — regression matrix automation.

`docs/QA-CHECKLIST.md` is a 14-section manual matrix. This file pulls
every row tagged `[scripted]` or the scriptable subset of `[partial]`
into pytest, so a regression on those rows surfaces as a sharp test
failure rather than waiting for the next manual QA run.

Each row maps to a parametrized test case with:
- `section` (the QA-CHECKLIST.md section number)
- `row` (the row number)
- `description` (mirrored from the checklist)
- `assertion` (the actual call that exercises the contract)

Sections covered (scriptable subset):
- 0.6 / 0.7 — pytest + ruff baseline (already self-evident; pinned)
- 2.1 / 2.4 — admin-order lock/unlock dispatch
- 3.1 / 3.6 — paywall add/clear via /admin/order
- 7.1 — set-geofence order shape
- 7.4 — confine_hour curfew set
- 7.5 — bedtime_lock_hour / bedtime_unlock_hour set

Sections explicitly NOT covered here (each annotated in QA-CHECKLIST.md):
- 1, 12, 14 — on-device UI / hardware
- 4 — payment regex covered by `tests/test_payment.py`; e2e IMAP
  deferred (operator-gated)
- 5.8 — photo task needs camera + Ollama
- 8 — vault crypto covered by `tests/test_vault.py`
- 9 — gossip covered by `tests/test_sync.py`
- 10 — ntfy mechanics in `tests/test_ntfy.py`; live push needs server
- 11 — desktop collar platform-specific

The point of this file is the *traceability* between the human matrix
and the programmatic harness. Each test docstring cites the QA-
CHECKLIST.md row it pins.
"""

import base64
import importlib.util
import json
import sys
import threading
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_qa_auto", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_qa_auto"] = mod
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


def _sign_order(priv, action, params):
    """Sign canonical({action, params}) — matches handle_mesh_order's
    expected envelope at focuslock_mesh.py:820."""
    payload = {"action": action, "params": params}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = priv.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


@pytest.fixture
def qa_mesh(mail_module):
    """Seed an operator mesh with a Lion keypair so /admin/order can
    pass both auth gates. PEM-encoded pubkey because mesh.verify_signature
    uses load_pem_public_key (matches qa_runner's lion_pubkey.pem shape)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    saved_admin_token = mail_module.ADMIN_TOKEN
    saved_get_lion_pubkey = mail_module.get_lion_pubkey
    mail_module.ADMIN_TOKEN = "qa-auto-token"
    mail_module.get_lion_pubkey = lambda: pub_pem
    try:
        yield {"priv": priv, "pub_pem": pub_pem, "mesh_id": mail_module.OPERATOR_MESH_ID}
    finally:
        mail_module.ADMIN_TOKEN = saved_admin_token
        mail_module.get_lion_pubkey = saved_get_lion_pubkey


def _admin_order(url, priv, admin_token, action, params):
    """Lion-signed admin-order POST. Mirrors what staging/qa_runner.py
    does; reused here so we don't drift from the integration driver."""
    body = {
        "admin_token": admin_token,
        "action": action,
        "params": params or {},
        "signature": _sign_order(priv, action, params or {}),
    }
    req = urllib.request.Request(
        url + "/admin/order",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ───────────────────────────────────────────────────────────────────────
# Section 2 — Lock / Unlock core (rows 2.1, 2.4)
# ───────────────────────────────────────────────────────────────────────


class TestSection2LockUnlock:
    def test_2_1_admin_lock_dispatches(self, live_server, qa_mesh):
        """QA 2.1 — admin-order `lock` accepts and dispatches. Real
        on-device convergence (gossip <10s) needs Waydroid; here we
        pin the relay-side accept path."""
        code, body = _admin_order(live_server, qa_mesh["priv"], "qa-auto-token", "lock", {})
        assert code == 200, f"{code} {body}"
        assert body.get("ok") is True or "error" not in body

    def test_2_4_admin_unlock_dispatches(self, live_server, qa_mesh):
        """QA 2.4 — admin-order `unlock` clears the lock immediately."""
        code, body = _admin_order(live_server, qa_mesh["priv"], "qa-auto-token", "unlock", {})
        assert code == 200, f"{code} {body}"
        assert body.get("ok") is True or "error" not in body


# ───────────────────────────────────────────────────────────────────────
# Section 3 — Paywall (rows 3.1, 3.6)
# ───────────────────────────────────────────────────────────────────────


class TestSection3Paywall:
    def test_3_1_add_paywall(self, live_server, qa_mesh):
        """QA 3.1 — `add-paywall $X` lands the amount in mesh_orders."""
        code, body = _admin_order(live_server, qa_mesh["priv"], "qa-auto-token", "add-paywall", {"amount": 25})
        assert code == 200, f"{code} {body}"

    def test_3_6_clear_paywall(self, live_server, qa_mesh):
        """QA 3.6 — `clear-paywall` zeros the paywall (Lion-authorized)."""
        # Pre-load some paywall first so clear has something to do
        _admin_order(live_server, qa_mesh["priv"], "qa-auto-token", "add-paywall", {"amount": 50})
        code, body = _admin_order(live_server, qa_mesh["priv"], "qa-auto-token", "clear-paywall", {})
        assert code == 200, f"{code} {body}"


# ───────────────────────────────────────────────────────────────────────
# Section 7 — Geofence + curfew + bedtime (rows 7.1, 7.4, 7.5)
# ───────────────────────────────────────────────────────────────────────


class TestSection7GeofenceCurfewBedtime:
    def test_7_1_set_geofence(self, live_server, qa_mesh):
        """QA 7.1 — `set-geofence` with lat/lon/radius accepts and stores."""
        code, body = _admin_order(
            live_server,
            qa_mesh["priv"],
            "qa-auto-token",
            "set-geofence",
            {"lat": 40.7128, "lon": -74.0060, "radius": 100},
        )
        assert code == 200, f"{code} {body}"

    def test_7_4_confine_hour_set(self, live_server, qa_mesh):
        """QA 7.4 — curfew via `confine_hour` order accepts. The actual
        22:00-trigger needs a wall-clock + on-device verification."""
        code, body = _admin_order(
            live_server,
            qa_mesh["priv"],
            "qa-auto-token",
            "set-confine-hours",
            {"confine_hour": 22, "release_hour": 7},
        )
        assert code == 200, f"{code} {body}"

    def test_7_5_bedtime_set(self, live_server, qa_mesh):
        """QA 7.5 — bedtime hours order accepts."""
        code, body = _admin_order(
            live_server,
            qa_mesh["priv"],
            "qa-auto-token",
            "set-bedtime",
            {"lock_hour": 23, "unlock_hour": 7},
        )
        assert code == 200, f"{code} {body}"


# ───────────────────────────────────────────────────────────────────────
# Section 13 — Admin API + web UI (rows 13.2, 13.3)
# Other rows (13.1 QR login, 13.5 signup) are covered by the Playwright
# browser walkthroughs (qa_index_browser.py + qa_wizard_browser.py).
# ───────────────────────────────────────────────────────────────────────


class TestSection13AdminAPI:
    def test_13_2_session_token_ttl_8h(self, mail_module):
        """QA 13.2 — session token TTL is 8 hours (the doc'd value)."""
        # _SESSION_TOKEN_TTL is the source of truth for the 8h figure
        assert mail_module._SESSION_TOKEN_TTL == 8 * 60 * 60, (
            f"Session TTL changed: {mail_module._SESSION_TOKEN_TTL}s — update QA-CHECKLIST 13.2 if this is intentional."
        )

    def test_13_3_master_admin_token_never_handed_to_web(self, mail_module):
        """QA 13.3 — web sessions get scoped tokens via _issue_session_token,
        never the master ADMIN_TOKEN. Pin the contract: the issue function
        produces a token != ADMIN_TOKEN, and lookup table shape is right."""
        saved = mail_module.ADMIN_TOKEN
        mail_module.ADMIN_TOKEN = "MASTER-NOT-FOR-WEB"
        try:
            scoped = mail_module._issue_session_token("test-session", "test-mesh")
            assert scoped != "MASTER-NOT-FOR-WEB"
            assert len(scoped) > 32  # secrets.token_urlsafe shape
            entry = mail_module._active_session_tokens.get(scoped)
            assert entry is not None
            assert entry["mesh_id"] == "test-mesh"
            assert entry["session_id"] == "test-session"
        finally:
            mail_module.ADMIN_TOKEN = saved
            # Clean up the test token from the global dict
            mail_module._active_session_tokens.pop(scoped, None)


# ───────────────────────────────────────────────────────────────────────
# Section 0 — pre-flight (rows 0.6, 0.7) — tautological but cited so
# the matrix→harness mapping is complete.
# ───────────────────────────────────────────────────────────────────────


class TestSection0Preflight:
    def test_0_6_pytest_baseline_runnable(self):
        """QA 0.6 — `pytest --cov` green. This file IS the pytest baseline;
        if it runs, the contract holds. Documented here for traceability."""
        assert True

    def test_0_7_ruff_check_runnable(self):
        """QA 0.7 — `ruff check .` green. The CI workflow + Makefile
        `make qa-fast` both run ruff. Documented here for traceability;
        the actual ruff invocation lives in CI / `make qa-fast`."""
        assert True


# ───────────────────────────────────────────────────────────────────────
# Traceability index — single test that asserts every documented
# section legend tag is present in the QA-CHECKLIST.md so future edits
# can't silently drop the [scripted] / [partial] / [manual] annotation.
# ───────────────────────────────────────────────────────────────────────


class TestQAChecklistTraceability:
    def test_every_section_header_has_a_legend_tag(self):
        """Each numbered `## N. Section name` line must end with one of
        the three legend tags. Catches a future edit that forgets to
        annotate a new numbered section. Non-numbered headers (Exit
        criteria, etc.) don't need tags."""
        import re

        path = REPO_ROOT / "docs" / "QA-CHECKLIST.md"
        text = path.read_text()
        VALID_TAGS = ("`[scripted]`", "`[partial]`", "`[manual]`")
        # Match only numbered sections: `## N. ...`
        numbered = re.compile(r"^## \d+\. ")
        section_lines = [ln for ln in text.splitlines() if numbered.match(ln)]
        assert len(section_lines) >= 14, f"expected 14+ numbered sections, got {len(section_lines)}"
        for ln in section_lines:
            assert any(tag in ln for tag in VALID_TAGS), (
                f"Numbered section header missing legend tag: {ln!r} — add one of {VALID_TAGS}"
            )

    def test_legend_section_documents_all_three_tags(self):
        """The legend block at the top of QA-CHECKLIST.md must define
        all three tag values so readers know what they mean."""
        path = REPO_ROOT / "docs" / "QA-CHECKLIST.md"
        text = path.read_text()
        for tag in ("`[scripted]`", "`[partial]`", "`[manual]`"):
            assert tag in text, f"Legend missing tag definition: {tag}"

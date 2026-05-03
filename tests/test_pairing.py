"""Tests for mesh-pair strengthening: PairingRegistry TTL + reason codes,
vault-status diagnostic endpoint, register-node-request logging, and the
pair-claim HTTP response shape."""

import importlib.util
import json
import logging
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_pairing", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_pairing"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def registry(mail_module, tmp_path):
    """Fresh PairingRegistry backed by a per-test file so claim/expire
    scenarios don't leak across tests."""
    return mail_module.PairingRegistry(persist_path=str(tmp_path / "pair.json"))


@pytest.fixture
def admin_token(mail_module, monkeypatch):
    """Install a known admin token on the module for the duration of a test.
    Tests that hit admin-gated endpoints use this to drive valid + invalid
    auth without needing a real config.json."""
    token = "test-admin-token-" + str(int(time.time() * 1000))
    monkeypatch.setattr(mail_module, "ADMIN_TOKEN", token)
    return token


@pytest.fixture
def live_server(mail_module):
    """Boot mail_module.WebhookHandler on a random port in a daemon thread.
    Yields the base URL so tests can drive real HTTP round-trips through
    the dispatch + admin-auth path — catching regressions that a direct
    call into the handler methods would miss (header parsing, status codes,
    Content-Length, etc.)."""
    server = HTTPServer(("127.0.0.1", 0), mail_module.WebhookHandler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        server.shutdown()
        server.server_close()


def _http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _http_post(url, body, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


class TestPairingRegistryTTL:
    def test_ttl_dropped_from_one_hour_to_ten_minutes(self, mail_module):
        assert mail_module.PairingRegistry.TTL_SECONDS == 600

    def test_fresh_register_uses_class_ttl(self, registry):
        registry.register("WOLF-42-BEAR", "bunny-pk", "bun-1")
        entry = registry.entries["WOLF-42-BEAR"]
        remaining = entry["expires_at"] - time.time()
        # Allow a small window for test-runner wall clock drift
        assert 595 <= remaining <= 600


class TestClaimOrReason:
    def test_happy_path_returns_entry_and_ok(self, registry):
        registry.register("WOLF-42-BEAR", "bunny-pk", "bun-1")
        entry, reason = registry.claim_or_reason("WOLF-42-BEAR", "lion-pk", "lion-1")
        assert reason == "ok"
        assert entry is not None
        assert entry["paired"] is True
        assert entry["lion_pubkey"] == "lion-pk"

    def test_unknown_passphrase_reason_not_registered(self, registry):
        entry, reason = registry.claim_or_reason("NEVER-BEEN-HERE", "lion-pk", "lion-1")
        assert entry is None
        assert reason == "not_registered"

    def test_expired_passphrase_reason_expired(self, registry):
        registry.register("WOLF-42-BEAR", "bunny-pk", "bun-1")
        # Force expiry by rewriting expires_at into the past
        registry.entries["WOLF-42-BEAR"]["expires_at"] = time.time() - 1
        entry, reason = registry.claim_or_reason("WOLF-42-BEAR", "lion-pk", "lion-1")
        assert entry is None
        assert reason == "expired"

    def test_claim_method_still_returns_entry_only_for_backwards_compat(self, registry):
        """Legacy callers use claim() and rely on the 2-state None-or-entry contract.
        claim_or_reason is additive — we must not break the older method."""
        registry.register("WOLF-42-BEAR", "bunny-pk", "bun-1")
        entry = registry.claim("WOLF-42-BEAR", "lion-pk", "lion-1")
        assert entry is not None
        assert entry["paired"] is True

    def test_case_insensitive_passphrase(self, registry):
        registry.register("wolf-42-bear", "bunny-pk", "bun-1")
        entry, reason = registry.claim_or_reason("WOLF-42-BEAR", "lion-pk", "lion-1")
        assert reason == "ok"
        assert entry is not None


class TestVaultStoreDiagnosticAccessors:
    """The /api/pair/vault-status/<mesh_id> endpoint reads exclusively
    through these three accessors — pinning them lets future refactors
    fail loudly rather than silently breaking the diagnostic surface."""

    def test_get_nodes_returns_empty_list_for_unknown_mesh(self, mail_module):
        assert mail_module._vault_store.get_nodes("nonexistent-mesh") == []

    def test_get_pending_nodes_returns_empty_list_for_unknown_mesh(self, mail_module):
        assert mail_module._vault_store.get_pending_nodes("nonexistent-mesh") == []

    def test_get_rejected_nodes_returns_empty_list_for_unknown_mesh(self, mail_module):
        assert mail_module._vault_store.get_rejected_nodes("nonexistent-mesh") == []

    def test_add_then_read_pending_node(self, mail_module):
        mesh_id = "test-pair-diag-" + str(int(time.time() * 1000))
        mail_module._vault_store.add_pending_node(
            mesh_id,
            {
                "node_id": "pixel",
                "node_type": "phone",
                "node_pubkey": "base64-encoded-der-goes-here",
                "requested_at": int(time.time()),
            },
        )
        pending = mail_module._vault_store.get_pending_nodes(mesh_id)
        assert len(pending) == 1
        assert pending[0]["node_id"] == "pixel"
        assert pending[0]["node_pubkey"] == "base64-encoded-der-goes-here"

    def test_rejection_hash_shape_matches_endpoint(self, mail_module):
        """The diagnostic endpoint hashes pubkeys with sha256()[:16] before
        returning them; the rejection key uses sha256()[:24]. Different
        lengths on purpose — rejection keys need to be collision-resistant
        across the whole fleet, display hashes are for cross-referencing
        one mesh's pending/approved entries."""
        import hashlib

        pk = "some-der-base64"
        assert mail_module.VaultStore._rejection_key(pk) == hashlib.sha256(pk.encode()).hexdigest()[:24]


class TestVaultStatusEndpoint:
    """Drive GET /api/pair/vault-status/<mesh_id> through the real HTTP
    dispatch so auth, path parsing, and pubkey-hash masking all exercise
    together. A direct handler-method call would miss urllib header
    parsing and Content-Length boundaries."""

    def _mesh_id(self, tag):
        # Unique per-test mesh id keeps parallel runs independent and
        # prevents state leaking through the singleton _vault_store.
        return f"test-vault-status-{tag}-{int(time.time() * 1000)}"

    def test_no_token_rejects_403(self, live_server, admin_token):
        mesh = self._mesh_id("noauth")
        status, body = _http_get(f"{live_server}/api/pair/vault-status/{mesh}")
        assert status == 403
        assert body.get("error") == "invalid admin_token"

    def test_wrong_token_rejects_403(self, live_server, admin_token):
        mesh = self._mesh_id("wrongauth")
        url = f"{live_server}/api/pair/vault-status/{mesh}?admin_token=not-the-real-token"
        status, _body = _http_get(url)
        assert status == 403

    def test_valid_token_unknown_mesh_returns_empty_lists(self, live_server, admin_token):
        mesh = self._mesh_id("empty")
        url = f"{live_server}/api/pair/vault-status/{mesh}?admin_token={admin_token}"
        status, body = _http_get(url)
        assert status == 200
        assert body["mesh_id"] == mesh
        assert body["approved"] == []
        assert body["pending"] == []
        assert body["rejected"] == []
        assert body["counts"] == {"approved": 0, "pending": 0, "rejected": 0}

    def test_bearer_header_auth_also_works(self, live_server, admin_token, mail_module):
        """Query-string auth is convenient for curl; Bearer auth is what
        Lion's Share and the web UI use. Both paths must accept the token."""
        mesh = self._mesh_id("bearer")
        mail_module._vault_store.add_pending_node(
            mesh,
            {
                "node_id": "bearer-node",
                "node_type": "phone",
                "node_pubkey": "bearer-test-pubkey",
                "requested_at": int(time.time()),
            },
        )
        url = f"{live_server}/api/pair/vault-status/{mesh}"
        status, body = _http_get(url, headers={"Authorization": f"Bearer {admin_token}"})
        assert status == 200
        assert body["counts"]["pending"] == 1

    def test_seeded_mesh_returns_counts_and_strips_pubkeys(self, live_server, admin_token, mail_module):
        """Approved + pending entries come back with pubkey_hash replacing
        node_pubkey so a diagnostic dump pasted into a support thread can't
        leak the full key. Rejected entries keep their original shape
        (pubkey is already hashed at rejection time)."""
        mesh = self._mesh_id("seeded")
        mail_module._vault_store.add_node(
            mesh,
            {
                "node_id": "approved-phone",
                "node_type": "phone",
                "node_pubkey": "approved-full-pubkey-der",
                "registered_at": int(time.time()),
            },
        )
        mail_module._vault_store.add_pending_node(
            mesh,
            {
                "node_id": "pending-desktop",
                "node_type": "desktop",
                "node_pubkey": "pending-full-pubkey-der",
                "requested_at": int(time.time()),
            },
        )
        mail_module._vault_store.add_rejected_node(mesh, "rejected-phone", "rejected-full-pubkey-der", reason="test")

        url = f"{live_server}/api/pair/vault-status/{mesh}?admin_token={admin_token}"
        status, body = _http_get(url)
        assert status == 200
        assert body["counts"] == {"approved": 1, "pending": 1, "rejected": 1}

        approved = body["approved"][0]
        assert approved["node_id"] == "approved-phone"
        assert "node_pubkey" not in approved  # stripped
        assert len(approved["pubkey_hash"]) == 16
        assert all(c in "0123456789abcdef" for c in approved["pubkey_hash"])

        pending = body["pending"][0]
        assert pending["node_id"] == "pending-desktop"
        assert "node_pubkey" not in pending
        assert len(pending["pubkey_hash"]) == 16

        # Cross-check: the two entries hash differently (distinct pubkeys)
        assert approved["pubkey_hash"] != pending["pubkey_hash"]


class TestPairClaimHTTPShape:
    """Pin the user-facing response shape of POST /api/pair/claim. The
    reason + hint fields feed directly into Lion's Share's AlertDialog
    (Bunny-Tasker-hint text) so any shape drift is a UX regression."""

    def test_happy_path_returns_bunny_pubkey(self, live_server, mail_module):
        # Seed a fresh registration via the server's PairingRegistry singleton.
        # Multi-tenant (2026-04-24): register + claim both require mesh_id.
        passphrase = "HAPPY-PATH-CLAIM"
        mesh_id = "test-mesh-happy"
        mail_module._pairing_registry.register(passphrase, "bunny-pk-happy", "bun-happy", mesh_id=mesh_id)
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {
                "passphrase": passphrase,
                "lion_pubkey": "lion-pk-happy",
                "lion_node_id": "lion-happy",
                "mesh_id": mesh_id,
            },
        )
        assert status == 200
        assert body == {"ok": True, "paired": True, "bunny_pubkey": "bunny-pk-happy"}

    def test_unknown_passphrase_returns_404_with_hint(self, live_server):
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {
                "passphrase": "NEVER-BEEN-REGISTERED-XYZ",
                "lion_pubkey": "lion-pk",
                "lion_node_id": "lion-1",
                "mesh_id": "test-mesh-unk",
            },
        )
        assert status == 404
        assert body["reason"] == "not_registered"
        assert "hint" in body
        # Hint must reference Bunny Tasker's flow so Lion can self-serve
        assert "Join Mesh" in body["hint"]

    def test_expired_passphrase_returns_410_with_ttl_hint(self, live_server, mail_module):
        passphrase = "EXPIRED-PASS-CLAIM"
        mesh_id = "test-mesh-exp"
        mail_module._pairing_registry.register(passphrase, "bunny-pk-exp", "bun-exp", mesh_id=mesh_id)
        # Force expiry — composite key is "{mesh_id}:{PASSPHRASE}"
        key = f"{mesh_id}:{passphrase.upper()}"
        mail_module._pairing_registry.entries[key]["expires_at"] = time.time() - 1
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {
                "passphrase": passphrase,
                "lion_pubkey": "lion-pk-exp",
                "lion_node_id": "lion-exp",
                "mesh_id": mesh_id,
            },
        )
        assert status == 410
        assert body["reason"] == "expired"
        # TTL in minutes must appear in the hint so the user knows why their
        # code went stale (dropped from 60 min → 10 min in this branch)
        ttl_min = mail_module.PairingRegistry.TTL_SECONDS // 60
        assert f"{ttl_min} min" in body["hint"]

    def test_missing_passphrase_returns_400(self, live_server):
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {"lion_pubkey": "lion-pk"},
        )
        assert status == 400
        assert "error" in body


class TestPairingRegistryPersistence:
    """A crash-restart of focuslock-mail.py must not invalidate codes Bunny
    just generated. The registry persists to JSON; on load, TTL math is
    still anchored to wall-clock expires_at (not recomputed relative to
    load time)."""

    def test_register_survives_reload(self, mail_module, tmp_path):
        persist = str(tmp_path / "persist.json")
        r1 = mail_module.PairingRegistry(persist_path=persist)
        r1.register("SURVIVE-ME", "bunny-pk-persist", "bun-persist")
        original_expires = r1.entries["SURVIVE-ME"]["expires_at"]

        # Simulate process restart
        r2 = mail_module.PairingRegistry(persist_path=persist)
        assert "SURVIVE-ME" in r2.entries
        # Wall-clock expiry preserved — not refreshed on load
        assert r2.entries["SURVIVE-ME"]["expires_at"] == original_expires
        assert r2.entries["SURVIVE-ME"]["bunny_pubkey"] == "bunny-pk-persist"

    def test_expired_entry_after_reload_still_reports_expired(self, mail_module, tmp_path):
        """If the relay was down past a code's TTL, claim_or_reason must
        still return `expired` after reload — not a false `not_registered`."""
        persist = str(tmp_path / "persist2.json")
        r1 = mail_module.PairingRegistry(persist_path=persist)
        r1.register("STALE-CODE", "bunny-pk-stale", "bun-stale")
        r1.entries["STALE-CODE"]["expires_at"] = time.time() - 10
        r1._save()

        r2 = mail_module.PairingRegistry(persist_path=persist)
        entry, reason = r2.claim_or_reason("STALE-CODE", "lion-pk", "lion-1")
        assert entry is None
        assert reason == "expired"


def _seed_mesh_account(mail_module, mesh_id):
    """Inject a minimal mesh account so /vault/<mesh_id>/... routes resolve.
    Without this, _vault_resolve_mesh returns None and the endpoint short-
    circuits with 404 before the register-node-request branch runs."""
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": "",
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


class TestVaultRegisterNodeRequestLogs:
    """The register-node-request handler logs pubkey_hash=<16 hex> on
    BAD_REQUEST, DENIED, and PENDING paths. Operators grep these logs to
    correlate a stuck slave with its key without the relay ever storing
    plaintext keys in access logs. A silent refactor dropping the hash
    would blind the debug flow."""

    def test_bad_request_log_includes_pubkey_hash_placeholder(self, live_server, mail_module, caplog):
        """Missing node_pubkey → BAD_REQUEST with empty pubkey_hash."""
        mesh = f"test-logs-badreq-{int(time.time() * 1000)}"
        _seed_mesh_account(mail_module, mesh)
        with caplog.at_level(logging.INFO, logger=mail_module.logger.name):
            status, _ = _http_post(
                f"{live_server}/vault/{mesh}/register-node-request",
                {"node_id": "no-key-node"},
            )
        assert status == 400
        matches = [r for r in caplog.records if "BAD_REQUEST" in r.getMessage()]
        assert matches, "expected a BAD_REQUEST log line"
        assert "pubkey_hash=" in matches[-1].getMessage()

    def test_denied_log_includes_pubkey_hash(self, live_server, mail_module, caplog):
        """Rejected pubkey → DENIED log with populated 16-char hash."""
        mesh = f"test-logs-denied-{int(time.time() * 1000)}"
        _seed_mesh_account(mail_module, mesh)
        mail_module._vault_store.add_rejected_node(mesh, "rej-node", "rejected-pubkey-blob", reason="test")
        with caplog.at_level(logging.WARNING, logger=mail_module.logger.name):
            status, _ = _http_post(
                f"{live_server}/vault/{mesh}/register-node-request",
                {"node_id": "rej-node", "node_pubkey": "rejected-pubkey-blob"},
            )
        assert status == 403
        matches = [r for r in caplog.records if "DENIED" in r.getMessage()]
        assert matches, "expected a DENIED log line"
        msg = matches[-1].getMessage()
        # Hash must be non-empty 16 hex chars
        import re

        m = re.search(r"pubkey_hash=([0-9a-f]{16})\b", msg)
        assert m, f"expected 16-hex pubkey_hash, got: {msg}"

    def test_pending_log_includes_pubkey_hash_and_type(self, live_server, mail_module, caplog):
        """Accepted pending → PENDING log with 16-char hash + node type."""
        mesh = f"test-logs-pending-{int(time.time() * 1000)}"
        _seed_mesh_account(mail_module, mesh)
        with caplog.at_level(logging.INFO, logger=mail_module.logger.name):
            status, body = _http_post(
                f"{live_server}/vault/{mesh}/register-node-request",
                {
                    "node_id": "fresh-phone",
                    "node_type": "phone",
                    "node_pubkey": "fresh-pubkey-blob-der",
                },
            )
        assert status == 200
        assert body.get("status") == "pending"
        matches = [r for r in caplog.records if "PENDING" in r.getMessage()]
        assert matches, "expected a PENDING log line"
        msg = matches[-1].getMessage()
        import re

        assert re.search(r"pubkey_hash=[0-9a-f]{16}\b", msg)
        assert "type=phone" in msg


class TestLogSanitizationHelpers:
    """Regression cover for _sanitize_log + _pubkey_fingerprint.

    CodeQL py/log-injection originally flagged the register-node-request
    and pair log sites; _sanitize_log is the surgical fix. If it stops
    escaping CR/LF the alerts resurface on the next scan.

    _pubkey_fingerprint replaces the earlier passphrase-based log
    correlation: pubkeys are public material, so hashing them for log
    grep doesn't raise py/weak-sensitive-data-hashing the way hashing
    the passphrase did. The pair endpoints now log bunny_pubkey_hash
    instead of any passphrase-derived token.
    """

    def test_sanitize_escapes_newlines(self, mail_module):
        assert mail_module._sanitize_log("abc\ndef") == "abc\\ndef"

    def test_sanitize_escapes_carriage_returns(self, mail_module):
        assert mail_module._sanitize_log("abc\rdef") == "abc\\rdef"

    def test_sanitize_escapes_nul(self, mail_module):
        assert mail_module._sanitize_log("abc\x00def") == "abc\\0def"

    def test_sanitize_handles_none(self, mail_module):
        assert mail_module._sanitize_log(None) == "<none>"

    def test_sanitize_coerces_non_string(self, mail_module):
        assert mail_module._sanitize_log(42) == "42"

    def test_sanitize_passes_clean_strings_through(self, mail_module):
        assert mail_module._sanitize_log("normal-id-123") == "normal-id-123"

    def test_sanitize_blocks_log_forgery(self, mail_module):
        # Classic attack: attacker-controlled id ends a line early + injects
        # a forged "APPROVED" line the operator might trust.
        malicious = "bogus-node\nVault register-node-request APPROVED: mesh=x"
        out = mail_module._sanitize_log(malicious)
        assert "\n" not in out
        assert out.startswith("bogus-node\\nVault")

    def test_pubkey_fingerprint_is_16_hex(self, mail_module):
        h = mail_module._pubkey_fingerprint("MIIBIjANBgkqhkiG9w0BAQEFA...")
        assert len(h) == 16
        assert all(c in "0123456789abcdef" for c in h)

    def test_pubkey_fingerprint_matches_vault_status_shape(self, mail_module):
        # /api/pair/vault-status masks pubkeys the same way; logs must use
        # identical shape so grep correlates between API + log output.
        pub = "some-pubkey-blob"
        import hashlib

        expected = hashlib.sha256(pub.encode("utf-8")).hexdigest()[:16]
        assert mail_module._pubkey_fingerprint(pub) == expected

    def test_pubkey_fingerprint_does_not_echo_input(self, mail_module):
        pub = "AAAABBBBCCCCDDDD"
        h = mail_module._pubkey_fingerprint(pub)
        assert pub not in h

    def test_pubkey_fingerprint_different_keys_different_hashes(self, mail_module):
        a = mail_module._pubkey_fingerprint("key-a")
        b = mail_module._pubkey_fingerprint("key-b")
        assert a != b

    def test_pubkey_fingerprint_handles_empty(self, mail_module):
        assert mail_module._pubkey_fingerprint("") == "<empty>"
        assert mail_module._pubkey_fingerprint(None) == "<empty>"


class TestInviteCodeReusable:
    """Invite codes are reusable within the TTL so one Lion can onboard
    multiple slaves (additional phones, desktops, household devices) with
    a single code. TTL is the only rate-limit; invite_uses is tracked for
    diagnostics + future rate-limit hooks but not enforced today."""

    def test_two_different_nodes_can_join_same_invite(self, mail_module):

        # Fresh MeshAccounts so this test doesn't collide with operator data
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="invite_reuse_")
        fresh = mail_module.MeshAccountStore(persist_dir=tmpdir)
        try:
            account = fresh.create("lion-pub-xyz", pin="1111")
            code = account["invite_code"]

            # First slave joins
            got1, err1 = fresh.join(code, "phone-a", "phone", "bunny-pub-a")
            assert err1 is None, f"first join failed: {err1}"
            assert got1 is not None
            assert "phone-a" in got1["nodes"]
            assert got1["invite_uses"] == 1

            # Second slave joins with the SAME code — must succeed, no
            # "invite code already used" rejection.
            got2, err2 = fresh.join(code, "desktop-b", "desktop", "bunny-pub-b")
            assert err2 is None, f"second join failed: {err2}"
            assert got2 is not None
            assert "desktop-b" in got2["nodes"]
            # Both nodes now on the account.
            assert set(got2["nodes"].keys()) == {"phone-a", "desktop-b"}
            assert got2["invite_uses"] == 2
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_expired_invite_still_rejected(self, mail_module):
        """TTL remains the hard gate — reusable doesn't mean forever."""

        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="invite_expired_")
        fresh = mail_module.MeshAccountStore(persist_dir=tmpdir)
        try:
            account = fresh.create("lion-pub-exp", pin="2222")
            # Force expiry
            fresh.meshes[account["mesh_id"]]["invite_expires_at"] = int(time.time()) - 60
            got, err = fresh.join(account["invite_code"], "phone-x", "phone", "bunny-pub-x")
            assert got is None
            assert err == "invite code expired"
        finally:
            import shutil

            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_invalid_invite_still_rejected(self, mail_module):
        """Unknown code is still a 400 — reuse doesn't loosen that."""
        got, err = mail_module._mesh_accounts.join("NEVER-ISSUED-ZZZ", "phone-y", "phone", "bunny-pub-y")
        assert got is None
        assert err == "invalid invite code"


class TestPairingRegistryMeshScoped:
    """Multi-tenant server: passphrases are scoped per-mesh so two Lions
    independently generating the same short code can't cross-wire their
    Bunnies' pubkeys. Audit 2026-04-24 HIGH #4. Registration without a
    mesh_id is rejected by the HTTP endpoint; PairingRegistry itself
    accepts empty mesh_id for legacy compat, but the claim path refuses
    to cross the scope."""

    def test_same_passphrase_two_meshes_does_not_collide(self, mail_module):
        reg = mail_module.PairingRegistry(persist_path=None)
        phrase = "WOLF-42-BEAR"
        reg.register(phrase, "bunny-A-pubkey", "bunny-A-node", mesh_id="mesh-A")
        reg.register(phrase, "bunny-B-pubkey", "bunny-B-node", mesh_id="mesh-B")

        # Lion A claims in mesh A's scope — gets Bunny A's pubkey
        entry_a, reason_a = reg.claim_or_reason(phrase, "lion-A-pk", "lion-A-node", mesh_id="mesh-A")
        assert reason_a == "ok"
        assert entry_a["bunny_pubkey"] == "bunny-A-pubkey"

        # Lion B claims in mesh B's scope — gets Bunny B's pubkey (not A's)
        entry_b, reason_b = reg.claim_or_reason(phrase, "lion-B-pk", "lion-B-node", mesh_id="mesh-B")
        assert reason_b == "ok"
        assert entry_b["bunny_pubkey"] == "bunny-B-pubkey"

    def test_claim_wrong_mesh_returns_not_registered(self, mail_module):
        reg = mail_module.PairingRegistry(persist_path=None)
        reg.register("SHARED-CODE", "bunny-X-pk", "bunny-X", mesh_id="mesh-X")
        # Trying to claim the same passphrase under a different mesh must
        # look empty to Lion-Y, regardless of Lion-Y's own signature.
        entry, reason = reg.claim_or_reason("SHARED-CODE", "lion-Y-pk", "lion-Y", mesh_id="mesh-Y")
        assert entry is None
        assert reason == "not_registered"

    def test_http_register_requires_mesh_id(self, live_server):
        status, body = _http_post(
            f"{live_server}/api/pair/register",
            {"passphrase": "NEEDS-MESH", "bunny_pubkey": "x", "node_id": "n"},
        )
        assert status == 400
        assert "mesh_id" in body.get("error", "")

    def test_http_claim_requires_mesh_id(self, live_server):
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {"passphrase": "NEEDS-MESH", "lion_pubkey": "x", "lion_node_id": "n"},
        )
        assert status == 400
        assert "mesh_id" in body.get("error", "")


class TestAdminOrderMeshRouting:
    """Multi-tenant routing fix (audit 2026-04-24 BLOCKER #1): `/admin/order`
    must mutate the mesh named in `mesh_id`, not the operator's globals.
    Pre-fix every non-operator Lion's admin action silently wrote to the
    operator mesh's orders doc."""

    @pytest.fixture
    def seeded(self, mail_module, monkeypatch):
        import tempfile

        # Fresh MeshAccountStore so test meshes don't leak
        tmpdir = tempfile.mkdtemp(prefix="admin_route_")
        original = mail_module._mesh_accounts
        store = mail_module.MeshAccountStore(persist_dir=tmpdir)

        # Two meshes, each with its own orders doc
        acct_a = store.create("lion-a-pubkey", pin="1111")
        acct_b = store.create("lion-b-pubkey", pin="2222")
        orders_a = mail_module._orders_registry.get_or_create(acct_a["mesh_id"])
        orders_b = mail_module._orders_registry.get_or_create(acct_b["mesh_id"])
        orders_a.set("paywall", "0")
        orders_b.set("paywall", "0")

        # Bind a known ADMIN_TOKEN and swap in the fresh store
        token = "test-admin-route-" + str(int(time.time() * 1000))
        monkeypatch.setattr(mail_module, "ADMIN_TOKEN", token)
        monkeypatch.setattr(mail_module, "_mesh_accounts", store)
        monkeypatch.setattr(mail_module, "OPERATOR_MESH_ID", acct_a["mesh_id"])

        try:
            yield {
                "admin_token": token,
                "mesh_a": acct_a["mesh_id"],
                "mesh_b": acct_b["mesh_id"],
            }
        finally:
            import shutil

            mail_module._orders_registry.docs.pop(acct_a["mesh_id"], None)
            mail_module._orders_registry.docs.pop(acct_b["mesh_id"], None)
            monkeypatch.setattr(mail_module, "_mesh_accounts", original)
            shutil.rmtree(tmpdir, ignore_errors=True)

    def test_order_on_mesh_b_does_not_affect_mesh_a(self, live_server, seeded, mail_module):
        status, _ = _http_post(
            f"{live_server}/admin/order",
            {
                "admin_token": seeded["admin_token"],
                "mesh_id": seeded["mesh_b"],
                "action": "add-paywall",
                "params": {"amount": 42},
            },
        )
        assert status == 200
        pw_a = int(mail_module._orders_registry.get(seeded["mesh_a"]).get("paywall", "0"))
        pw_b = int(mail_module._orders_registry.get(seeded["mesh_b"]).get("paywall", "0"))
        assert pw_b == 42, "mesh B's paywall should have received the add"
        assert pw_a == 0, "mesh A (operator) must not leak mesh B's admin order"


class TestPerMeshPaymentLedger:
    """Multi-tenant server (focus.example.com hosting many Lion meshes)
    must isolate payment history per mesh. Pre-2026-04-24 the ledger was
    a singleton — a Bunny on mesh X would fetch the Bunny-on-mesh-Y's
    history and see entries from the old operator mesh even after a
    fresh mesh create + uninstall-reinstall of both apps. Fixed with
    `_get_payment_ledger(mesh_id)` and per-file persistence under
    `_LEDGERS_DIR`. The operator mesh still reads/writes the legacy
    `payment_ledger.json` for historical continuity."""

    def test_fresh_mesh_starts_with_empty_ledger(self, mail_module):
        """A new mesh_id returns an empty ledger — no bleed from any
        existing singleton or other mesh."""
        mesh_id = "fresh-mesh-" + str(int(time.time() * 1000))
        ledger = mail_module._get_payment_ledger(mesh_id)
        assert ledger.entries == []

    def test_writes_to_one_mesh_invisible_to_another(self, mail_module):
        mesh_a = "iso-mesh-a-" + str(int(time.time() * 1000))
        mesh_b = "iso-mesh-b-" + str(int(time.time() * 1000))
        ledger_a = mail_module._get_payment_ledger(mesh_a)
        ledger_b = mail_module._get_payment_ledger(mesh_b)
        ledger_a.add_entry("payment", 50.0, source="bank-ref-mesh-a", description="Test deposit A")
        # Same instance returned on re-fetch (cached)
        assert mail_module._get_payment_ledger(mesh_a) is ledger_a
        # Different mesh sees nothing
        assert ledger_b.entries == []
        # Re-fetch mesh_a still has the entry
        again_a = mail_module._get_payment_ledger(mesh_a)
        assert len(again_a.entries) == 1
        assert again_a.entries[0]["amount"] == 50.0

    def test_operator_mesh_uses_legacy_path_for_continuity(self, mail_module, monkeypatch):
        """Operator mesh keeps reading/writing the legacy
        payment_ledger.json so historic entries don't disappear when the
        per-mesh refactor lands. Non-operator meshes go to _LEDGERS_DIR."""
        monkeypatch.setattr(mail_module, "OPERATOR_MESH_ID", "the-operator")
        # Clear cache so the factory re-evaluates OPERATOR_MESH_ID
        with mail_module._payment_ledgers_lock:
            mail_module._payment_ledgers.clear()
        op_ledger = mail_module._get_payment_ledger("the-operator")
        other_ledger = mail_module._get_payment_ledger("some-tenant")
        assert op_ledger.persist_path == mail_module._LEDGER_PATH
        assert other_ledger.persist_path != mail_module._LEDGER_PATH
        assert mail_module._LEDGERS_DIR in other_ledger.persist_path


class TestPerMeshDesktopRegistry:
    """Multi-tenant desktop heartbeat isolation (audit 2026-04-24 HIGH #2+#3).
    A desktop collar on mesh B's heartbeat must register to mesh B — not
    the server-wide singleton — and its $50 silent-for-2-weeks penalty
    must land on mesh B's paywall, not on the operator ADB-connected
    phone. Pre-fix both flows went to `desktop_registry` + `adb.put(
    'focus_lock_paywall')` regardless of which mesh the collar belonged to."""

    def test_fresh_mesh_has_empty_desktop_registry(self, mail_module):
        mesh_id = "desktop-fresh-" + str(int(time.time() * 1000))
        reg = mail_module._get_desktop_registry(mesh_id)
        assert reg.snapshot() == {}

    def test_heartbeat_isolation_between_meshes(self, mail_module):
        mesh_a = "desktop-iso-a-" + str(int(time.time() * 1000))
        mesh_b = "desktop-iso-b-" + str(int(time.time() * 1000))
        reg_a = mail_module._get_desktop_registry(mesh_a)
        reg_b = mail_module._get_desktop_registry(mesh_b)
        reg_a.heartbeat("workstation-a", name="A-lab")
        assert "workstation-a" in reg_a.snapshot()
        assert reg_b.snapshot() == {}  # mesh B sees nothing from mesh A
        # Re-fetching returns the cached instance
        assert mail_module._get_desktop_registry(mesh_a) is reg_a

    def test_penalty_webhook_routes_to_request_mesh(self, live_server, mail_module, monkeypatch):
        """/webhook/desktop-penalty with a mesh_id must mutate that mesh's
        orders doc, not the operator's ADB global."""
        import tempfile

        tmpdir = tempfile.mkdtemp(prefix="desktop_pen_")
        store = mail_module.MeshAccountStore(persist_dir=tmpdir)
        acct = store.create("lion-desktop-pen", pin="3333")
        mesh_id = acct["mesh_id"]
        orders = mail_module._orders_registry.get_or_create(mesh_id)
        orders.set("paywall", "0")

        token = "test-desktop-pen-" + str(int(time.time() * 1000))
        monkeypatch.setattr(mail_module, "ADMIN_TOKEN", token)
        monkeypatch.setattr(mail_module, "_mesh_accounts", store)

        status, body = _http_post(
            f"{live_server}/webhook/desktop-penalty",
            {
                "admin_token": token,
                "mesh_id": mesh_id,
                "amount": 42,
                "reason": "test desktop offline",
            },
        )
        try:
            assert status == 200
            assert body["mesh_id"] == mesh_id
            pw = int(mail_module._orders_registry.get(mesh_id).get("paywall", "0"))
            assert pw == 42
        finally:
            import shutil

            mail_module._orders_registry.docs.pop(mesh_id, None)
            shutil.rmtree(tmpdir, ignore_errors=True)


class TestBunnyMessageSigned:
    """/webhook/bunny-message now requires a bunny-signed payload. Pins the
    auth gate (missing/bad/expired-ts/unknown-node/unknown-mesh → 403 or 404)
    and the happy path (200 + send_evidence invoked) so an unauth regression
    fails loudly rather than silently reopening the spoofable-evidence
    channel the 2026-04-17 hardening commit deferred.

    Canonical payload: "{mesh_id}|{node_id}|bunny-message|{ts_i}" signed
    with SHA256withRSA + PKCS1v15, DER-pubkey base64'd into the mesh account
    node record. Matches the format already used by /api/mesh/{id}/gamble
    + /api/mesh/{id}/escape-event."""

    @staticmethod
    def _bunny_keypair():
        """Fresh RSA-2048 keypair for one test. Returns (privkey_obj,
        pubkey_b64) — privkey usable for signing, pubkey_b64 ready to
        drop into the mesh account node record."""
        import base64

        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric import rsa

        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub_der = priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        return priv, base64.b64encode(pub_der).decode()

    @staticmethod
    def _sign(priv, payload):
        import base64

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        sig = priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
        return base64.b64encode(sig).decode()

    @pytest.fixture
    def seeded(self, mail_module):
        """Seed a mesh with a single node holding a known bunny pubkey.
        Yields the pieces the test needs to construct signed requests."""
        mesh_id = "bunnymsg-mesh-" + str(int(time.time() * 1000))
        node_id = "bunny-phone-1"
        priv, pub_b64 = self._bunny_keypair()
        _seed_mesh_account(mail_module, mesh_id)
        mail_module._mesh_accounts.meshes[mesh_id]["nodes"][node_id] = {
            "node_id": node_id,
            "bunny_pubkey": pub_b64,
            "registered_at": int(time.time()),
        }
        try:
            yield {"mesh_id": mesh_id, "node_id": node_id, "priv": priv, "pub_b64": pub_b64}
        finally:
            mail_module._mesh_accounts.meshes.pop(mesh_id, None)

    def _post(self, url, body):
        import urllib.error

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

    def test_missing_signature_returns_403_with_version_hint(self, live_server, seeded):
        """No signature field → 403 + min_companion_version hint so older
        Bunny Taskers know to update rather than silently fail."""
        status, body = self._post(
            f"{live_server}/webhook/bunny-message",
            {"text": "hi", "type": "message"},
        )
        assert status == 403
        assert body["error"] == "signature required"
        assert body["min_companion_version"] == 53

    def test_bad_signature_returns_403(self, live_server, seeded):
        ts = int(time.time() * 1000)
        status, body = self._post(
            f"{live_server}/webhook/bunny-message",
            {
                "text": "hi",
                "type": "message",
                "mesh_id": seeded["mesh_id"],
                "node_id": seeded["node_id"],
                "ts": ts,
                "signature": "AAAA" + "=" * 340,  # valid base64, wrong sig
            },
        )
        assert status == 403
        assert body["error"] == "invalid signature"

    def test_ts_out_of_window_returns_403(self, live_server, seeded):
        # Sign correctly but against a stale timestamp; server must reject
        # before even checking the signature.
        stale_ts = int(time.time() * 1000) - 10 * 60 * 1000  # 10 min ago
        payload = f"{seeded['mesh_id']}|{seeded['node_id']}|bunny-message|{stale_ts}"
        sig = self._sign(seeded["priv"], payload)
        status, body = self._post(
            f"{live_server}/webhook/bunny-message",
            {
                "text": "old news",
                "type": "message",
                "mesh_id": seeded["mesh_id"],
                "node_id": seeded["node_id"],
                "ts": stale_ts,
                "signature": sig,
            },
        )
        assert status == 403
        assert body["error"] == "ts out of window"

    def test_unknown_node_returns_403(self, live_server, seeded):
        ts = int(time.time() * 1000)
        payload = f"{seeded['mesh_id']}|not-a-real-node|bunny-message|{ts}"
        sig = self._sign(seeded["priv"], payload)
        status, body = self._post(
            f"{live_server}/webhook/bunny-message",
            {
                "text": "hi",
                "type": "message",
                "mesh_id": seeded["mesh_id"],
                "node_id": "not-a-real-node",
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 403
        assert body["error"] == "node not registered in mesh"

    def test_unknown_mesh_returns_404(self, live_server, seeded):
        ts = int(time.time() * 1000)
        payload = f"ghost-mesh|{seeded['node_id']}|bunny-message|{ts}"
        sig = self._sign(seeded["priv"], payload)
        status, body = self._post(
            f"{live_server}/webhook/bunny-message",
            {
                "text": "hi",
                "type": "message",
                "mesh_id": "ghost-mesh",
                "node_id": seeded["node_id"],
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 404
        assert body["error"] == "mesh not found"

    def test_valid_signature_fires_evidence(self, live_server, seeded, mail_module, monkeypatch):
        """Happy path: correct sig → 200 + send_evidence invoked with the
        self-lock framing. The send_evidence stub captures args so the test
        can confirm routing (self-lock vs plain message) is preserved."""
        captured = []
        monkeypatch.setattr(mail_module, "send_evidence", lambda body, kind: captured.append((body, kind)))
        ts = int(time.time() * 1000)
        payload = f"{seeded['mesh_id']}|{seeded['node_id']}|bunny-message|{ts}"
        sig = self._sign(seeded["priv"], payload)
        status, body = self._post(
            f"{live_server}/webhook/bunny-message",
            {
                "text": "locked for 30 min",
                "type": "self-lock",
                "mesh_id": seeded["mesh_id"],
                "node_id": seeded["node_id"],
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200
        assert body == {"ok": True}
        assert len(captured) == 1
        evidence_body, evidence_kind = captured[0]
        assert evidence_kind == "self-lock"
        assert "locked for 30 min" in evidence_body

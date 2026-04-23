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
        # Seed a fresh registration via the server's PairingRegistry singleton
        passphrase = "HAPPY-PATH-CLAIM"
        mail_module._pairing_registry.register(passphrase, "bunny-pk-happy", "bun-happy")
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {"passphrase": passphrase, "lion_pubkey": "lion-pk-happy", "lion_node_id": "lion-happy"},
        )
        assert status == 200
        assert body == {"ok": True, "paired": True, "bunny_pubkey": "bunny-pk-happy"}

    def test_unknown_passphrase_returns_404_with_hint(self, live_server):
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {"passphrase": "NEVER-BEEN-REGISTERED-XYZ", "lion_pubkey": "lion-pk", "lion_node_id": "lion-1"},
        )
        assert status == 404
        assert body["reason"] == "not_registered"
        assert "hint" in body
        # Hint must reference Bunny Tasker's flow so Lion can self-serve
        assert "Join Mesh" in body["hint"]

    def test_expired_passphrase_returns_410_with_ttl_hint(self, live_server, mail_module):
        passphrase = "EXPIRED-PASS-CLAIM"
        mail_module._pairing_registry.register(passphrase, "bunny-pk-exp", "bun-exp")
        # Force expiry (claim_or_reason will see time.time() > expires_at)
        mail_module._pairing_registry.entries[passphrase]["expires_at"] = time.time() - 1
        status, body = _http_post(
            f"{live_server}/api/pair/claim",
            {"passphrase": passphrase, "lion_pubkey": "lion-pk-exp", "lion_node_id": "lion-exp"},
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
        "invite_consumed": True,
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

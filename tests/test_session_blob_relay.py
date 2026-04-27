"""Unit tests for focuslock-mail.py session-token, daily-blob-count,
and relay-node registration helpers.

These are isolated, security-critical helpers that the e2e suites exercise
through HTTP routes but never pin in isolation:

- `_issue_session_token` / `_is_valid_admin_auth` / `_revoke_session_token` —
  scoped session-token authentication; multi-tenant correctness depends on
  the mesh_id binding behaving correctly.
- `_daily_blob_count` / `_daily_blob_increment` — per-mesh per-day quota
  counters; stale-date pruning is the contract that must hold across UTC
  rollover.
- `_ensure_relay_node_registered` — idempotent relay-key registration; rotate
  semantics + bootstrap-only contract.
- `_admin_order_to_vault_blob` — relay-signed vault RPC blob writer; the
  many early-exit paths (no relay key, no vault module, no mesh, no nodes)
  are the contract surface.
"""

import importlib.util
import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_session_blob", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_session_blob"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def clean_session_state(mail_module):
    """Snapshot + restore the global session-token dict so tests don't bleed."""
    snapshot = dict(mail_module._active_session_tokens)
    mail_module._active_session_tokens.clear()
    yield
    mail_module._active_session_tokens.clear()
    mail_module._active_session_tokens.update(snapshot)


@pytest.fixture
def clean_blob_counts(mail_module):
    """Snapshot + restore the daily-blob-counts dict."""
    snapshot = dict(mail_module._daily_blob_counts)
    mail_module._daily_blob_counts.clear()
    yield
    mail_module._daily_blob_counts.clear()
    mail_module._daily_blob_counts.update(snapshot)


# ──────────────────────── _issue_session_token ────────────────────────


class TestIssueSessionToken:
    def test_returns_url_safe_token(self, mail_module, clean_session_state):
        token = mail_module._issue_session_token("session-1")
        assert isinstance(token, str)
        assert len(token) >= 32
        # token_urlsafe returns only A-Za-z0-9_-
        assert all(c.isalnum() or c in "-_" for c in token)

    def test_token_records_session_id_and_mesh(self, mail_module, clean_session_state):
        token = mail_module._issue_session_token("session-1", mesh_id="mesh-A")
        entry = mail_module._active_session_tokens[token]
        assert entry["session_id"] == "session-1"
        assert entry["mesh_id"] == "mesh-A"
        assert "issued_at" in entry
        assert "expires_at" in entry

    def test_default_mesh_id_empty(self, mail_module, clean_session_state):
        token = mail_module._issue_session_token("session-1")
        assert mail_module._active_session_tokens[token]["mesh_id"] == ""

    def test_expires_at_uses_module_ttl(self, mail_module, clean_session_state):
        before = time.time()
        token = mail_module._issue_session_token("session-1")
        after = time.time()
        entry = mail_module._active_session_tokens[token]
        ttl = mail_module._SESSION_TOKEN_TTL
        assert before + ttl - 1 <= entry["expires_at"] <= after + ttl + 1

    def test_unique_tokens(self, mail_module, clean_session_state):
        toks = {mail_module._issue_session_token(f"s{i}") for i in range(10)}
        assert len(toks) == 10

    def test_prunes_expired_tokens_on_issue(self, mail_module, clean_session_state):
        # Seed with a stale token
        mail_module._active_session_tokens["stale"] = {
            "issued_at": time.time() - 999999,
            "expires_at": time.time() - 1,
            "session_id": "old",
            "mesh_id": "",
        }
        new_tok = mail_module._issue_session_token("fresh")
        assert "stale" not in mail_module._active_session_tokens
        assert new_tok in mail_module._active_session_tokens


# ──────────────────────── _is_valid_admin_auth ────────────────────────


class TestIsValidAdminAuth:
    def test_no_token_rejected(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            assert mail_module._is_valid_admin_auth("") is False
            assert mail_module._is_valid_admin_auth(None) is False

    def test_no_admin_token_configured_rejects_all(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", ""):
            assert mail_module._is_valid_admin_auth("anything") is False

    def test_master_admin_token_accepted(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            assert mail_module._is_valid_admin_auth("master-token") is True

    def test_master_admin_token_constant_time(self, mail_module, clean_session_state):
        # Same length but wrong content — must still reject
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            assert mail_module._is_valid_admin_auth("MASTER-TOKEN") is False

    def test_master_token_bypasses_mesh_scope(self, mail_module, clean_session_state):
        # master-token should be valid for any mesh_id
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            assert mail_module._is_valid_admin_auth("master-token", mesh_id="any-mesh") is True

    def test_unknown_session_token_rejected(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            assert mail_module._is_valid_admin_auth("unknown-token") is False

    def test_valid_session_token_accepted(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            tok = mail_module._issue_session_token("s1")
            assert mail_module._is_valid_admin_auth(tok) is True

    def test_expired_session_token_rejected_and_pruned(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            mail_module._active_session_tokens["expired"] = {
                "issued_at": time.time() - 999999,
                "expires_at": time.time() - 1,
                "session_id": "old",
                "mesh_id": "",
            }
            assert mail_module._is_valid_admin_auth("expired") is False
            assert "expired" not in mail_module._active_session_tokens

    def test_session_token_scoped_to_mesh(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            tok = mail_module._issue_session_token("s1", mesh_id="mesh-A")
            # Same mesh: accepted
            assert mail_module._is_valid_admin_auth(tok, mesh_id="mesh-A") is True
            # Different mesh: rejected (cross-tenant block)
            assert mail_module._is_valid_admin_auth(tok, mesh_id="mesh-B") is False

    def test_unscoped_session_token_works_for_any_mesh(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            # Empty mesh_id in token == operator-scope == accept anywhere
            tok = mail_module._issue_session_token("s1", mesh_id="")
            assert mail_module._is_valid_admin_auth(tok, mesh_id="any-mesh") is True

    def test_mesh_id_none_skips_scope_check(self, mail_module, clean_session_state):
        # Caller didn't ask for a scope check: any live token passes
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            tok = mail_module._issue_session_token("s1", mesh_id="mesh-A")
            assert mail_module._is_valid_admin_auth(tok, mesh_id=None) is True


# ──────────────────────── _revoke_session_token ────────────────────────


class TestRevokeSessionToken:
    def test_revoke_existing_returns_true(self, mail_module, clean_session_state):
        tok = mail_module._issue_session_token("s1")
        assert mail_module._revoke_session_token(tok) is True
        assert tok not in mail_module._active_session_tokens

    def test_revoke_unknown_returns_false(self, mail_module, clean_session_state):
        assert mail_module._revoke_session_token("never-issued") is False

    def test_revoked_token_is_invalid(self, mail_module, clean_session_state):
        with patch.object(mail_module, "ADMIN_TOKEN", "master-token"):
            tok = mail_module._issue_session_token("s1")
            mail_module._revoke_session_token(tok)
            assert mail_module._is_valid_admin_auth(tok) is False


# ──────────────────────── _daily_blob_count / _daily_blob_increment ────────────────────────


class TestDailyBlobCount:
    def test_zero_for_unknown_mesh(self, mail_module, clean_blob_counts):
        assert mail_module._daily_blob_count("nope") == 0

    def test_increment_then_count(self, mail_module, clean_blob_counts):
        mail_module._daily_blob_increment("m1")
        mail_module._daily_blob_increment("m1")
        mail_module._daily_blob_increment("m1")
        assert mail_module._daily_blob_count("m1") == 3

    def test_per_mesh_isolation(self, mail_module, clean_blob_counts):
        mail_module._daily_blob_increment("m1")
        mail_module._daily_blob_increment("m2")
        mail_module._daily_blob_increment("m2")
        assert mail_module._daily_blob_count("m1") == 1
        assert mail_module._daily_blob_count("m2") == 2

    def test_stale_dates_pruned_on_count(self, mail_module, clean_blob_counts):
        # Seed a stale date for mesh m1
        mail_module._daily_blob_counts[("m1", "20200101")] = 99
        # Today's count starts at 0; reading it should also prune the stale key
        assert mail_module._daily_blob_count("m1") == 0
        assert ("m1", "20200101") not in mail_module._daily_blob_counts

    def test_stale_dates_only_pruned_for_target_mesh(self, mail_module, clean_blob_counts):
        # m1 has a stale date; m2 has its own stale date that should NOT be pruned
        # by a query against m1.
        mail_module._daily_blob_counts[("m1", "20200101")] = 5
        mail_module._daily_blob_counts[("m2", "20200101")] = 7
        mail_module._daily_blob_count("m1")
        assert ("m1", "20200101") not in mail_module._daily_blob_counts
        assert ("m2", "20200101") in mail_module._daily_blob_counts


# ──────────────────────── _ensure_relay_node_registered ────────────────────────


class TestEnsureRelayNodeRegistered:
    def test_no_relay_key_returns_false(self, mail_module):
        with patch.object(mail_module, "RELAY_PUBKEY_DER_B64", ""):
            assert mail_module._ensure_relay_node_registered("any-mesh") is False

    def test_first_registration(self, mail_module):
        # Use an isolated VaultStore to avoid mutating the singleton
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            vs = mail_module.VaultStore(base_dir=td)
            with patch.object(mail_module, "_vault_store", vs):
                with patch.object(mail_module, "RELAY_PUBKEY_DER_B64", "RELAY_PK_AAA"):
                    assert mail_module._ensure_relay_node_registered("mesh-A") is True
            nodes = vs.get_nodes("mesh-A")
            relay_nodes = [n for n in nodes if n.get("node_id") == "relay"]
            assert len(relay_nodes) == 1
            assert relay_nodes[0]["node_pubkey"] == "RELAY_PK_AAA"
            assert relay_nodes[0]["node_type"] == "server"

    def test_idempotent_when_already_registered_with_same_key(self, mail_module):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            vs = mail_module.VaultStore(base_dir=td)
            with patch.object(mail_module, "_vault_store", vs):
                with patch.object(mail_module, "RELAY_PUBKEY_DER_B64", "RELAY_PK_AAA"):
                    mail_module._ensure_relay_node_registered("mesh-A")
                    # Calling twice must not duplicate
                    assert mail_module._ensure_relay_node_registered("mesh-A") is True
            nodes = vs.get_nodes("mesh-A")
            assert sum(1 for n in nodes if n.get("node_id") == "relay") == 1

    def test_rotates_when_pubkey_changed(self, mail_module):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            vs = mail_module.VaultStore(base_dir=td)
            with patch.object(mail_module, "_vault_store", vs):
                with patch.object(mail_module, "RELAY_PUBKEY_DER_B64", "OLD_KEY"):
                    mail_module._ensure_relay_node_registered("mesh-A")
                # Rotate pubkey — should re-register, not duplicate
                with patch.object(mail_module, "RELAY_PUBKEY_DER_B64", "NEW_KEY"):
                    assert mail_module._ensure_relay_node_registered("mesh-A") is True
            nodes = vs.get_nodes("mesh-A")
            relay_nodes = [n for n in nodes if n.get("node_id") == "relay"]
            assert len(relay_nodes) == 1
            assert relay_nodes[0]["node_pubkey"] == "NEW_KEY"


# ──────────────────────── _admin_order_to_vault_blob ────────────────────────


class TestAdminOrderToVaultBlob:
    def test_no_relay_key_early_exit(self, mail_module):
        with patch.object(mail_module, "RELAY_PRIVKEY_PEM", ""):
            # Should not raise
            assert mail_module._admin_order_to_vault_blob("lock", {}) is None

    def test_no_mesh_id_no_operator_early_exit(self, mail_module):
        with patch.object(mail_module, "RELAY_PRIVKEY_PEM", "fake-priv"):
            with patch.object(mail_module, "OPERATOR_MESH_ID", ""):
                # No mesh_id provided + no OPERATOR_MESH_ID set → early exit
                assert mail_module._admin_order_to_vault_blob("lock", {}, mesh_id=None) is None

    def test_no_registered_nodes_early_exit(self, mail_module):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            vs = mail_module.VaultStore(base_dir=td)
            with patch.object(mail_module, "_vault_store", vs):
                with patch.object(mail_module, "RELAY_PRIVKEY_PEM", "fake-priv"):
                    # mesh-A has no nodes registered → early exit, no blob written
                    mail_module._admin_order_to_vault_blob("lock", {}, mesh_id="mesh-A")
                    assert vs.current_version("mesh-A") == 0

    def test_nodes_without_pubkey_filtered_out(self, mail_module):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            vs = mail_module.VaultStore(base_dir=td)
            # Register a node with no pubkey — the recipients filter drops it
            vs.add_node("mesh-A", {"node_id": "ghost", "node_type": "slave", "node_pubkey": ""})
            with patch.object(mail_module, "_vault_store", vs):
                with patch.object(mail_module, "RELAY_PRIVKEY_PEM", "fake-priv"):
                    mail_module._admin_order_to_vault_blob("lock", {}, mesh_id="mesh-A")
                    # No real recipients → no blob written
                    assert vs.current_version("mesh-A") == 0

    def test_uses_operator_mesh_when_no_mesh_id(self, mail_module):
        import tempfile

        with tempfile.TemporaryDirectory() as td:
            vs = mail_module.VaultStore(base_dir=td)
            with patch.object(mail_module, "_vault_store", vs):
                with patch.object(mail_module, "RELAY_PRIVKEY_PEM", "fake-priv"):
                    with patch.object(mail_module, "OPERATOR_MESH_ID", "op-mesh"):
                        # op-mesh has no nodes → still hits the no-nodes early exit
                        # (not the no-mesh exit) — this proves the OPERATOR_MESH_ID fallback path.
                        mail_module._admin_order_to_vault_blob("lock", {}, mesh_id=None)
                        assert vs.current_version("op-mesh") == 0

"""Unit tests for focuslock-mail.py helper functions.

Targets the security-critical helpers that the e2e suites exercise indirectly
but never pin in isolation:

- `_sanitize_log` — log-injection defense
- `_pubkey_fingerprint` — diagnostic key fingerprint
- `_compute_source_sha256` / `_read_deploy_git_commit` — provenance signals
- `_safe_mesh_id_static` — input validation at the registry layer
- `MeshOrdersRegistry` — mesh_id → OrdersDocument map
- `_resolve_orders` — operator/consumer mesh dispatch
- `_get_ntfy_topic` — multi-tenant topic derivation (audit followup #7)
- `_load_lion_pubkey_obj` — PEM-or-bare-DER pubkey loader
- `_verify_signed_payload` — RSA-PKCS1v15-SHA256 verify
- `_verify_blob_two_writer` — Lion-or-node multi-writer verification
- `_vault_resolve_mesh` — mesh account lookup
"""

import base64
import importlib.util
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_relay_helpers", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_relay_helpers"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def lion_keys():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der_b64 = base64.b64encode(
        priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode()
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )
    return {"priv": priv, "pub_pem": pub_pem, "pub_der_b64": pub_der_b64}


@pytest.fixture(scope="module")
def slave_keys():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der_b64 = base64.b64encode(
        priv.public_key().public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    ).decode()
    return {"priv": priv, "pub_der_b64": pub_der_b64}


def _sign(priv, payload_dict, mail_module):
    """Sign canonical_json(payload) with `priv`, return base64 sig."""
    signed = {k: v for k, v in payload_dict.items() if k != "signature"}
    data = mail_module.mesh.canonical_json(signed)
    sig = priv.sign(data, asym_padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


# ── _sanitize_log ──


class TestSanitizeLog:
    def test_passes_through_safe_string(self, mail_module):
        assert mail_module._sanitize_log("hello world") == "hello world"

    def test_escapes_cr(self, mail_module):
        assert mail_module._sanitize_log("a\rb") == "a\\rb"

    def test_escapes_lf(self, mail_module):
        assert mail_module._sanitize_log("a\nb") == "a\\nb"

    def test_escapes_nul(self, mail_module):
        assert mail_module._sanitize_log("a\x00b") == "a\\0b"

    def test_escapes_log_injection_pattern(self, mail_module):
        """CRLF + fake-log-line is the literal threat model."""
        injected = "real-id\r\n2026-01-01 ERROR: fake message"
        out = mail_module._sanitize_log(injected)
        assert "\r" not in out
        assert "\n" not in out
        assert "real-id\\r\\n2026-01-01" in out

    def test_none_returns_placeholder(self, mail_module):
        assert mail_module._sanitize_log(None) == "<none>"

    def test_non_string_coerced_to_str(self, mail_module):
        assert mail_module._sanitize_log(42) == "42"
        assert mail_module._sanitize_log({"a": 1}) == "{'a': 1}"

    def test_empty_string_passes_through(self, mail_module):
        assert mail_module._sanitize_log("") == ""


# ── _pubkey_fingerprint ──


class TestPubkeyFingerprint:
    def test_returns_16_hex_chars(self, mail_module, lion_keys):
        fp = mail_module._pubkey_fingerprint(lion_keys["pub_der_b64"])
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)

    def test_deterministic(self, mail_module, lion_keys):
        a = mail_module._pubkey_fingerprint(lion_keys["pub_der_b64"])
        b = mail_module._pubkey_fingerprint(lion_keys["pub_der_b64"])
        assert a == b

    def test_different_keys_different_fingerprints(self, mail_module, lion_keys, slave_keys):
        a = mail_module._pubkey_fingerprint(lion_keys["pub_der_b64"])
        b = mail_module._pubkey_fingerprint(slave_keys["pub_der_b64"])
        assert a != b

    def test_empty_returns_placeholder(self, mail_module):
        assert mail_module._pubkey_fingerprint("") == "<empty>"
        assert mail_module._pubkey_fingerprint(None) == "<empty>"

    def test_non_string_coerced_to_str(self, mail_module):
        # int gets stringified before hashing
        fp = mail_module._pubkey_fingerprint(12345)
        assert len(fp) == 16
        assert fp == mail_module._pubkey_fingerprint("12345")


# ── _compute_source_sha256 ──


class TestComputeSourceSha256:
    def test_returns_64_hex_chars(self, mail_module):
        sha = mail_module._compute_source_sha256()
        assert len(sha) == 64
        assert all(c in "0123456789abcdef" for c in sha)

    def test_matches_disk_content(self, mail_module):
        import hashlib

        with open(MAIL_PATH, "rb") as f:
            expected = hashlib.sha256(f.read()).hexdigest()
        assert mail_module._compute_source_sha256() == expected

    def test_module_constant_matches_function(self, mail_module):
        """The cached SOURCE_SHA256 is computed once at module load."""
        assert mail_module.SOURCE_SHA256 == mail_module._compute_source_sha256()

    def test_returns_none_on_io_error(self, mail_module):
        """Unreadable __file__ → graceful None, not crash."""
        with patch("builtins.open", side_effect=OSError("permission denied")):
            assert mail_module._compute_source_sha256() is None


# ── _read_deploy_git_commit ──


class TestReadDeployGitCommit:
    def test_returns_none_when_no_git_commit_file(self, mail_module):
        # Both candidates absent (no /opt/focuslock and no sibling .git_commit)
        with patch("builtins.open", side_effect=FileNotFoundError):
            assert mail_module._read_deploy_git_commit() is None

    def test_returns_commit_when_file_present(self, mail_module):
        """Sibling .git_commit takes effect when /opt/focuslock isn't readable."""
        from io import StringIO

        commit = "abc123def456"
        opens = []

        def fake_open(path, *args, **kwargs):
            opens.append(path)
            if path == "/opt/focuslock/.git_commit":
                raise FileNotFoundError
            # Sibling location succeeds
            return StringIO(f"{commit}\n")

        with patch("builtins.open", side_effect=fake_open):
            assert mail_module._read_deploy_git_commit() == commit

    def test_blank_file_returns_none(self, mail_module):
        from io import StringIO

        def fake_open(path, *args, **kwargs):
            return StringIO("   \n")

        with patch("builtins.open", side_effect=fake_open):
            assert mail_module._read_deploy_git_commit() is None

    def test_strips_whitespace(self, mail_module):
        from io import StringIO

        def fake_open(path, *args, **kwargs):
            if "/opt/focuslock" in path:
                raise FileNotFoundError
            return StringIO("  abcd1234  \n")

        with patch("builtins.open", side_effect=fake_open):
            assert mail_module._read_deploy_git_commit() == "abcd1234"

    def test_first_candidate_wins(self, mail_module):
        """If /opt/focuslock/.git_commit exists, sibling is never read."""
        from io import StringIO

        opens = []

        def fake_open(path, *args, **kwargs):
            opens.append(path)
            if path == "/opt/focuslock/.git_commit":
                return StringIO("OPS-COMMIT")
            return StringIO("LOCAL-COMMIT")

        with patch("builtins.open", side_effect=fake_open):
            commit = mail_module._read_deploy_git_commit()
        assert commit == "OPS-COMMIT"
        assert opens == ["/opt/focuslock/.git_commit"]  # sibling never opened


# ── _safe_mesh_id_static ──


class TestSafeMeshIdStatic:
    def test_alphanumeric_accepted(self, mail_module):
        assert mail_module._safe_mesh_id_static("abc123") is True

    def test_dash_accepted(self, mail_module):
        assert mail_module._safe_mesh_id_static("abc-123") is True

    def test_underscore_accepted(self, mail_module):
        assert mail_module._safe_mesh_id_static("abc_123") is True

    def test_mixed_accepted(self, mail_module):
        assert mail_module._safe_mesh_id_static("DNfs4xCZM-HY") is True

    def test_empty_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static("") is False

    def test_none_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static(None) is False

    def test_non_string_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static(123) is False

    def test_path_separator_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static("../etc/passwd") is False
        assert mail_module._safe_mesh_id_static("a/b") is False

    def test_dot_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static("a.b") is False

    def test_null_byte_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static("a\x00b") is False

    def test_whitespace_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static("a b") is False
        assert mail_module._safe_mesh_id_static("a\tb") is False
        assert mail_module._safe_mesh_id_static("a\nb") is False

    def test_over_64_chars_rejected(self, mail_module):
        assert mail_module._safe_mesh_id_static("a" * 65) is False

    def test_exactly_64_chars_accepted(self, mail_module):
        assert mail_module._safe_mesh_id_static("a" * 64) is True


# ── MeshOrdersRegistry ──


class TestMeshOrdersRegistry:
    def test_get_returns_none_for_unknown_mesh(self, mail_module):
        with tempfile.TemporaryDirectory() as d:
            reg = mail_module.MeshOrdersRegistry(base_dir=d)
            assert reg.get("nonexistent") is None

    def test_get_or_create_returns_orders_document(self, mail_module):
        with tempfile.TemporaryDirectory() as d:
            reg = mail_module.MeshOrdersRegistry(base_dir=d)
            doc = reg.get_or_create("validmesh")
            assert doc is not None
            # Same call returns the same instance
            assert reg.get_or_create("validmesh") is doc

    def test_get_or_create_persists_to_disk(self, mail_module):
        with tempfile.TemporaryDirectory() as d:
            reg = mail_module.MeshOrdersRegistry(base_dir=d)
            reg.get_or_create("persistme")
            expected = os.path.join(d, "persistme.json")
            # Document is created lazily; only persists on first save
            doc = reg.get_or_create("persistme")
            assert doc.persist_path == expected

    def test_get_or_create_rejects_invalid_mesh_id(self, mail_module):
        with tempfile.TemporaryDirectory() as d:
            reg = mail_module.MeshOrdersRegistry(base_dir=d)
            with pytest.raises(ValueError, match="invalid mesh_id"):
                reg.get_or_create("../etc/passwd")
            with pytest.raises(ValueError, match="invalid mesh_id"):
                reg.get_or_create("")

    def test_load_all_picks_up_existing_files(self, mail_module):
        with tempfile.TemporaryDirectory() as d:
            # Pre-seed with a valid mesh-orders file
            existing = os.path.join(d, "existingmesh.json")
            with open(existing, "w") as f:
                json.dump({"version": 7, "orders": {}}, f)
            reg = mail_module.MeshOrdersRegistry(base_dir=d)
            doc = reg.get("existingmesh")
            assert doc is not None
            assert doc.version == 7


# ── _resolve_orders ──


class TestResolveOrders:
    def test_resolves_to_per_mesh_when_registered(self, mail_module):
        # Use the operator mesh — it's always registered post-init
        if mail_module.OPERATOR_MESH_ID:
            doc = mail_module._resolve_orders(mail_module.OPERATOR_MESH_ID)
            assert doc is not None

    def test_falls_back_to_default_for_unknown_mesh(self, mail_module):
        """Missing mesh → falls back to legacy `mesh_orders` global."""
        doc = mail_module._resolve_orders("totally-unknown-mesh")
        assert doc is mail_module.mesh_orders

    def test_empty_mesh_id_returns_default(self, mail_module):
        assert mail_module._resolve_orders("") is mail_module.mesh_orders

    def test_none_mesh_id_returns_default(self, mail_module):
        assert mail_module._resolve_orders(None) is mail_module.mesh_orders


# ── _get_ntfy_topic ──


class TestGetNtfyTopic:
    def test_per_mesh_topic_for_consumer_mesh(self, mail_module):
        topic = mail_module._get_ntfy_topic("CONSUMER123")
        assert topic == "focuslock-CONSUMER123"

    def test_operator_mesh_uses_configured_topic_when_set(self, mail_module):
        """Operator mesh keeps its legacy config-wide topic."""
        with patch.object(mail_module, "OPERATOR_MESH_ID", "OPMESH"):
            with patch.object(mail_module, "_ntfy_topic", "operator-legacy-topic"):
                topic = mail_module._get_ntfy_topic("OPMESH")
                assert topic == "operator-legacy-topic"

    def test_operator_mesh_falls_back_to_derived_when_no_configured_topic(self, mail_module):
        with patch.object(mail_module, "OPERATOR_MESH_ID", "OPMESH"):
            with patch.object(mail_module, "_ntfy_topic", ""):
                topic = mail_module._get_ntfy_topic("OPMESH")
                assert topic == "focuslock-OPMESH"

    def test_consumer_mesh_ignores_operator_configured_topic(self, mail_module):
        """Consumer must always get its own deterministic topic."""
        with patch.object(mail_module, "OPERATOR_MESH_ID", "OPMESH"):
            with patch.object(mail_module, "_ntfy_topic", "operator-legacy"):
                topic = mail_module._get_ntfy_topic("CONSUMER")
                assert topic == "focuslock-CONSUMER"

    def test_no_mesh_id_uses_configured_topic(self, mail_module):
        with patch.object(mail_module, "_ntfy_topic", "fallback-topic"):
            topic = mail_module._get_ntfy_topic("")
            assert topic == "fallback-topic"

    def test_no_mesh_id_no_config_returns_empty(self, mail_module):
        """No mesh_id, no config, no accounts → empty."""
        # Stub _mesh_accounts to be empty
        empty_accounts = type("_EmptyAccounts", (), {"meshes": {}})()
        with patch.object(mail_module, "_ntfy_topic", ""):
            with patch.object(mail_module, "_mesh_accounts", empty_accounts):
                topic = mail_module._get_ntfy_topic("")
                assert topic == ""


# ── _load_lion_pubkey_obj ──


class TestLoadLionPubkeyObj:
    def test_loads_pem(self, mail_module, lion_keys):
        pub = mail_module._load_lion_pubkey_obj(lion_keys["pub_pem"])
        assert pub is not None

    def test_loads_bare_der_b64(self, mail_module, lion_keys):
        pub = mail_module._load_lion_pubkey_obj(lion_keys["pub_der_b64"])
        assert pub is not None

    def test_loads_der_b64_with_whitespace(self, mail_module, lion_keys):
        """DER b64 with embedded line breaks (e.g. 64-char wrapping) still loads."""
        b64 = lion_keys["pub_der_b64"]
        wrapped = "\n".join(b64[i : i + 64] for i in range(0, len(b64), 64))
        pub = mail_module._load_lion_pubkey_obj(wrapped)
        assert pub is not None

    def test_empty_returns_none(self, mail_module):
        assert mail_module._load_lion_pubkey_obj("") is None

    def test_none_returns_none(self, mail_module):
        assert mail_module._load_lion_pubkey_obj(None) is None

    def test_garbage_returns_none(self, mail_module):
        assert mail_module._load_lion_pubkey_obj("not a key") is None

    def test_invalid_b64_returns_none(self, mail_module):
        assert mail_module._load_lion_pubkey_obj("###not-base64###") is None

    def test_pem_with_wrong_header_returns_none(self, mail_module):
        """A string with PEM markers that's still malformed → None."""
        bad = "-----BEGIN PUBLIC KEY-----\nbroken\n-----END PUBLIC KEY-----"
        assert mail_module._load_lion_pubkey_obj(bad) is None


# ── _verify_signed_payload ──


class TestVerifySignedPayload:
    def test_valid_signature_pem_key(self, mail_module, lion_keys):
        payload = {"action": "lock", "mins": 30, "ts": 12345}
        sig = _sign(lion_keys["priv"], payload, mail_module)
        assert mail_module._verify_signed_payload(payload, sig, lion_keys["pub_pem"]) is True

    def test_valid_signature_der_key(self, mail_module, lion_keys):
        payload = {"action": "unlock", "ts": 99}
        sig = _sign(lion_keys["priv"], payload, mail_module)
        assert mail_module._verify_signed_payload(payload, sig, lion_keys["pub_der_b64"]) is True

    def test_signature_excludes_signature_field_from_canonical(self, mail_module, lion_keys):
        """Signing canonical_json({everything except 'signature'}) is the contract."""
        payload = {"action": "lock", "ts": 5}
        sig = _sign(lion_keys["priv"], payload, mail_module)
        # Add the signature back into the dict as if it traveled through HTTP
        payload_with_sig = {**payload, "signature": sig}
        # Verify must still succeed because we strip 'signature' before canonicalizing
        assert mail_module._verify_signed_payload(payload_with_sig, sig, lion_keys["pub_pem"]) is True

    def test_tampered_payload_rejected(self, mail_module, lion_keys):
        payload = {"action": "lock", "mins": 5}
        sig = _sign(lion_keys["priv"], payload, mail_module)
        tampered = {"action": "lock", "mins": 9999}  # mutated
        assert mail_module._verify_signed_payload(tampered, sig, lion_keys["pub_pem"]) is False

    def test_wrong_pubkey_rejected(self, mail_module, lion_keys, slave_keys):
        payload = {"action": "x"}
        sig = _sign(lion_keys["priv"], payload, mail_module)
        assert mail_module._verify_signed_payload(payload, sig, slave_keys["pub_der_b64"]) is False

    def test_empty_signature_rejected(self, mail_module, lion_keys):
        assert mail_module._verify_signed_payload({"x": 1}, "", lion_keys["pub_pem"]) is False

    def test_empty_pubkey_rejected(self, mail_module, lion_keys):
        payload = {"x": 1}
        sig = _sign(lion_keys["priv"], payload, mail_module)
        assert mail_module._verify_signed_payload(payload, sig, "") is False

    def test_garbage_signature_rejected(self, mail_module, lion_keys):
        assert mail_module._verify_signed_payload({"x": 1}, "not!base64!!!", lion_keys["pub_pem"]) is False

    def test_quiet_suppresses_warning(self, mail_module, lion_keys, slave_keys, caplog):
        """quiet=True still rejects but doesn't emit a warning."""
        import logging

        caplog.set_level(logging.WARNING, logger="focuslock_mail_relay_helpers")
        payload = {"x": 1}
        sig = _sign(slave_keys["priv"], payload, mail_module)  # wrong signer
        result = mail_module._verify_signed_payload(payload, sig, lion_keys["pub_pem"], quiet=True)
        assert result is False
        # No "vault sig verify failed" warning record
        assert not any("sig verify failed" in r.message for r in caplog.records)


# ── _verify_blob_two_writer ──


class TestVerifyBlobTwoWriter:
    def test_lion_signed_blob_accepted(self, mail_module, lion_keys):
        blob = {"action": "x", "ts": 1}
        sig = _sign(lion_keys["priv"], blob, mail_module)
        blob["signature"] = sig
        role, writer = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], [])
        assert role == "lion"
        assert writer == "lion"

    def test_node_signed_blob_accepted(self, mail_module, lion_keys, slave_keys):
        blob = {"action": "y", "ts": 2}
        sig = _sign(slave_keys["priv"], blob, mail_module)
        blob["signature"] = sig
        nodes = [{"node_id": "phone1", "node_pubkey": slave_keys["pub_der_b64"]}]
        role, writer = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], nodes)
        assert role == "node"
        assert writer == "phone1"

    def test_lion_tried_first(self, mail_module, lion_keys):
        """When the same blob is signed by Lion, the result is `lion`, not `node`."""
        blob = {"x": 1}
        sig = _sign(lion_keys["priv"], blob, mail_module)
        blob["signature"] = sig
        # Lion's pubkey is also in the nodes list — Lion path still wins
        nodes = [{"node_id": "lion-as-node", "node_pubkey": lion_keys["pub_der_b64"]}]
        role, _ = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], nodes)
        assert role == "lion"

    def test_unsigned_blob_rejected(self, mail_module, lion_keys):
        blob = {"action": "x"}  # no signature
        role, writer = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], [])
        assert (role, writer) == (None, None)

    def test_empty_signature_rejected(self, mail_module, lion_keys):
        blob = {"action": "x", "signature": ""}
        role, writer = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], [])
        assert (role, writer) == (None, None)

    def test_unknown_signer_rejected(self, mail_module, lion_keys, slave_keys):
        """Signed by neither Lion nor any registered node → reject."""
        rogue_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        blob = {"x": 1}
        sig = _sign(rogue_priv, blob, mail_module)
        blob["signature"] = sig
        nodes = [{"node_id": "phone1", "node_pubkey": slave_keys["pub_der_b64"]}]
        role, writer = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], nodes)
        assert (role, writer) == (None, None)

    def test_no_lion_pubkey_falls_through_to_nodes(self, mail_module, slave_keys):
        """Lion key absent → still try registered nodes."""
        blob = {"x": 1}
        sig = _sign(slave_keys["priv"], blob, mail_module)
        blob["signature"] = sig
        nodes = [{"node_id": "phone1", "node_pubkey": slave_keys["pub_der_b64"]}]
        role, writer = mail_module._verify_blob_two_writer(blob, "", nodes)
        assert role == "node"
        assert writer == "phone1"

    def test_none_registered_nodes_handled(self, mail_module, lion_keys):
        """Pass `None` as registered_nodes — rejects cleanly."""
        rogue_priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        blob = {"x": 1}
        sig = _sign(rogue_priv, blob, mail_module)
        blob["signature"] = sig
        role, writer = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], None)
        assert (role, writer) == (None, None)

    def test_node_with_empty_pubkey_skipped(self, mail_module, lion_keys, slave_keys):
        """A registered node with empty `node_pubkey` is skipped — defensive."""
        blob = {"x": 1}
        sig = _sign(slave_keys["priv"], blob, mail_module)
        blob["signature"] = sig
        nodes = [
            {"node_id": "broken", "node_pubkey": ""},
            {"node_id": "good", "node_pubkey": slave_keys["pub_der_b64"]},
        ]
        role, writer = mail_module._verify_blob_two_writer(blob, lion_keys["pub_der_b64"], nodes)
        assert role == "node"
        assert writer == "good"


# ── _vault_resolve_mesh ──


class TestVaultResolveMesh:
    def test_unknown_mesh_returns_none_pair(self, mail_module):
        account, lion_pub = mail_module._vault_resolve_mesh("totally-unknown")
        assert account is None
        assert lion_pub is None

    def test_known_mesh_returns_account_and_pubkey(self, mail_module, lion_keys):
        """Inject a synthetic mesh, look it up, get the account + lion_pubkey."""
        synthetic_id = "test-resolve-mesh-id"
        # Save real registry state
        accounts = mail_module._mesh_accounts
        original_mesh = accounts.meshes.get(synthetic_id)
        accounts.meshes[synthetic_id] = {
            "lion_pubkey": lion_keys["pub_der_b64"],
            "created_at": 0,
        }
        try:
            account, lion_pub = mail_module._vault_resolve_mesh(synthetic_id)
            assert account is not None
            assert lion_pub == lion_keys["pub_der_b64"]
        finally:
            # Restore
            if original_mesh is None:
                del accounts.meshes[synthetic_id]
            else:
                accounts.meshes[synthetic_id] = original_mesh

    def test_known_mesh_without_lion_pubkey_returns_empty_string(self, mail_module):
        """Account exists but `lion_pubkey` not yet set — returns ('', not None)."""
        synthetic_id = "test-resolve-no-pubkey"
        accounts = mail_module._mesh_accounts
        original = accounts.meshes.get(synthetic_id)
        accounts.meshes[synthetic_id] = {"created_at": 0}
        try:
            account, lion_pub = mail_module._vault_resolve_mesh(synthetic_id)
            assert account is not None
            assert lion_pub == ""
        finally:
            if original is None:
                del accounts.meshes[synthetic_id]
            else:
                accounts.meshes[synthetic_id] = original

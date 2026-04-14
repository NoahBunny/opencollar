"""Tests for shared/focuslock_vault.py — RSA+AES-GCM encrypted mesh blobs."""

import base64
import json

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from focuslock_vault import (
    _load_privkey,
    _load_pubkey_der,
    _strip_pem,
    canonical_json,
    decrypt_body,
    encrypt_body,
    generate_keypair,
    sign_blob,
    slot_id_for_pubkey,
    verify_signature,
)

# ── canonical_json ──


class TestCanonicalJson:
    def test_key_order_deterministic(self):
        a = canonical_json({"b": 1, "a": 2})
        b = canonical_json({"a": 2, "b": 1})
        assert a == b == b'{"a":2,"b":1}'

    def test_no_whitespace(self):
        out = canonical_json({"x": [1, 2, 3], "y": {"nested": True}})
        assert b" " not in out
        assert out == b'{"x":[1,2,3],"y":{"nested":true}}'

    def test_ascii_only_unicode_escaped(self):
        out = canonical_json({"emoji": "\u26a1"})
        assert b"\xe2" not in out
        assert b"\\u26a1" in out

    def test_returns_bytes(self):
        assert isinstance(canonical_json({}), bytes)


# ── slot_id_for_pubkey ──


class TestSlotId:
    def test_format_is_12_hex(self, lion_keypair):
        sid = slot_id_for_pubkey(lion_keypair["pub_der"])
        assert len(sid) == 12
        assert all(c in "0123456789abcdef" for c in sid)

    def test_deterministic_for_same_key(self, lion_keypair):
        sid1 = slot_id_for_pubkey(lion_keypair["pub_der"])
        sid2 = slot_id_for_pubkey(lion_keypair["pub_der"])
        assert sid1 == sid2

    def test_different_for_different_keys(self, lion_keypair, slave_keypair):
        assert slot_id_for_pubkey(lion_keypair["pub_der"]) != slot_id_for_pubkey(slave_keypair["pub_der"])


# ── generate_keypair ──


class TestGenerateKeypair:
    def test_returns_three_values(self):
        priv, pub, der = generate_keypair()
        assert priv.startswith("-----BEGIN PRIVATE KEY-----")
        assert pub.startswith("-----BEGIN PUBLIC KEY-----")
        assert isinstance(der, bytes)
        assert len(der) > 100  # RSA-2048 SPKI is ~294 bytes

    def test_keys_are_unique(self):
        _, _, der1 = generate_keypair()
        _, _, der2 = generate_keypair()
        assert der1 != der2


# ── _strip_pem ──


class TestStripPem:
    def test_strips_public_headers(self):
        pem = "-----BEGIN PUBLIC KEY-----\nABC123\n-----END PUBLIC KEY-----\n"
        assert _strip_pem(pem) == "ABC123"

    def test_strips_private_headers_pkcs8(self):
        pem = "-----BEGIN PRIVATE KEY-----\nXYZ\n-----END PRIVATE KEY-----"
        assert _strip_pem(pem) == "XYZ"

    def test_strips_private_headers_rsa(self):
        pem = "-----BEGIN RSA PRIVATE KEY-----\nABC\n-----END RSA PRIVATE KEY-----"
        assert _strip_pem(pem) == "ABC"

    def test_strips_whitespace_and_crlf(self):
        assert _strip_pem("A B\r\nC\n D") == "ABCD"

    def test_passthrough_raw_base64(self):
        assert _strip_pem("PureBase64==") == "PureBase64=="


# ── _load_pubkey_der ──


class TestLoadPubkeyDer:
    def test_loads_from_pem(self, lion_keypair):
        pk, der = _load_pubkey_der(lion_keypair["pub_pem"])
        assert der == lion_keypair["pub_der"]
        assert pk is not None

    def test_loads_from_raw_base64(self, lion_keypair):
        raw_b64 = base64.b64encode(lion_keypair["pub_der"]).decode()
        _pk, der = _load_pubkey_der(raw_b64)
        assert der == lion_keypair["pub_der"]


# ── _load_privkey ──


class TestLoadPrivkey:
    def test_loads_from_pem(self, lion_keypair):
        pk = _load_privkey(lion_keypair["priv_pem"])
        assert pk is not None

    def test_loads_from_raw_base64(self, lion_keypair):
        # Strip headers, pass bare b64 — should re-wrap as PKCS8 PEM
        raw = _strip_pem(lion_keypair["priv_pem"])
        pk = _load_privkey(raw)
        assert pk is not None


# ── sign_blob + verify_signature ──


class TestSignature:
    def test_sign_produces_b64_string(self, lion_keypair):
        sig = sign_blob({"hello": "world"}, lion_keypair["priv_pem"])
        assert isinstance(sig, str)
        # must be valid base64
        base64.b64decode(sig)

    def test_signature_excludes_signature_key(self, lion_keypair):
        s1 = sign_blob({"a": 1}, lion_keypair["priv_pem"])
        s2 = sign_blob({"a": 1, "signature": "stale"}, lion_keypair["priv_pem"])
        assert s1 == s2  # signing ignores existing signature field

    def test_verify_accepts_own_signature(self, lion_keypair):
        blob = {"action": "lock", "minutes": 5}
        blob["signature"] = sign_blob(blob, lion_keypair["priv_pem"])
        assert verify_signature(blob, lion_keypair["pub_pem"]) is True

    def test_verify_rejects_tampered_payload(self, lion_keypair):
        blob = {"action": "lock", "minutes": 5}
        blob["signature"] = sign_blob(blob, lion_keypair["priv_pem"])
        blob["minutes"] = 9999  # tamper after signing
        assert verify_signature(blob, lion_keypair["pub_pem"]) is False

    def test_verify_rejects_wrong_pubkey(self, lion_keypair, slave_keypair):
        blob = {"action": "lock"}
        blob["signature"] = sign_blob(blob, lion_keypair["priv_pem"])
        assert verify_signature(blob, slave_keypair["pub_pem"]) is False

    def test_verify_rejects_missing_signature_field(self, lion_keypair):
        assert verify_signature({"action": "lock"}, lion_keypair["pub_pem"]) is False

    def test_verify_rejects_empty_signature(self, lion_keypair):
        assert verify_signature({"action": "lock", "signature": ""}, lion_keypair["pub_pem"]) is False

    def test_verify_rejects_garbage_signature(self, lion_keypair):
        blob = {"action": "lock", "signature": "not-valid-b64!!!"}
        assert verify_signature(blob, lion_keypair["pub_pem"]) is False


# ── encrypt_body + decrypt_body ──


class TestRoundTrip:
    def test_single_recipient_roundtrip(self, lion_keypair, slave_keypair):
        body = {"action": "lock", "minutes": 30}
        blob = encrypt_body(
            mesh_id="test_mesh",
            version=1,
            created_at=1234567890,
            body=body,
            recipients=[("slave", slave_keypair["pub_pem"])],
            signer_privkey_pem=lion_keypair["priv_pem"],
        )
        out = decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"])
        assert out is not None
        assert json.loads(out) == body

    def test_multi_recipient_each_can_decrypt(self, lion_keypair, slave_keypair, desktop_keypair):
        body = {"order": "unlock"}
        blob = encrypt_body(
            mesh_id="mesh_multi",
            version=2,
            created_at=1,
            body=body,
            recipients=[
                ("slave", slave_keypair["pub_pem"]),
                ("desktop", desktop_keypair["pub_pem"]),
            ],
            signer_privkey_pem=lion_keypair["priv_pem"],
        )
        for kp in (slave_keypair, desktop_keypair):
            out = decrypt_body(blob, kp["priv_pem"], kp["pub_der"])
            assert out is not None
            assert json.loads(out) == body

    def test_non_recipient_gets_none(self, lion_keypair, slave_keypair, desktop_keypair):
        blob = encrypt_body(
            mesh_id="m",
            version=1,
            created_at=0,
            body={"x": 1},
            recipients=[("slave", slave_keypair["pub_pem"])],
            signer_privkey_pem=lion_keypair["priv_pem"],
        )
        # desktop is NOT a recipient — no slot for its pubkey
        assert decrypt_body(blob, desktop_keypair["priv_pem"], desktop_keypair["pub_der"]) is None

    def test_signature_present_after_encrypt(self, lion_keypair, slave_keypair):
        blob = encrypt_body(
            mesh_id="m",
            version=1,
            created_at=0,
            body={},
            recipients=[("slave", slave_keypair["pub_pem"])],
            signer_privkey_pem=lion_keypair["priv_pem"],
        )
        assert "signature" in blob
        assert verify_signature(blob, lion_keypair["pub_pem"]) is True

    def test_tampered_ciphertext_fails_decrypt(self, lion_keypair, slave_keypair):
        blob = encrypt_body(
            mesh_id="m",
            version=1,
            created_at=0,
            body={"secret": "hello"},
            recipients=[("slave", slave_keypair["pub_pem"])],
            signer_privkey_pem=lion_keypair["priv_pem"],
        )
        # Flip a byte in the AES-GCM ciphertext — auth tag will fail
        ct = bytearray(base64.b64decode(blob["ciphertext"]))
        ct[0] ^= 0xFF
        blob["ciphertext"] = base64.b64encode(bytes(ct)).decode()
        assert decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"]) is None


class TestDecryptFailureModes:
    def _make_blob(self, lion_kp, slave_kp):
        return encrypt_body(
            mesh_id="m",
            version=1,
            created_at=0,
            body={"a": 1},
            recipients=[("slave", slave_kp["pub_pem"])],
            signer_privkey_pem=lion_kp["priv_pem"],
        )

    def test_missing_slots_returns_none(self, lion_keypair, slave_keypair):
        blob = self._make_blob(lion_keypair, slave_keypair)
        blob.pop("slots")
        assert decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"]) is None

    def test_missing_encrypted_key_returns_none(self, lion_keypair, slave_keypair):
        blob = self._make_blob(lion_keypair, slave_keypair)
        sid = next(iter(blob["slots"]))
        blob["slots"][sid].pop("encrypted_key")
        assert decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"]) is None

    def test_missing_iv_returns_none(self, lion_keypair, slave_keypair):
        blob = self._make_blob(lion_keypair, slave_keypair)
        sid = next(iter(blob["slots"]))
        blob["slots"][sid].pop("iv")
        assert decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"]) is None

    def test_missing_ciphertext_returns_none(self, lion_keypair, slave_keypair):
        blob = self._make_blob(lion_keypair, slave_keypair)
        blob.pop("ciphertext")
        assert decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"]) is None

    def test_corrupt_wrapped_key_returns_none(self, lion_keypair, slave_keypair):
        blob = self._make_blob(lion_keypair, slave_keypair)
        sid = next(iter(blob["slots"]))
        ek = bytearray(base64.b64decode(blob["slots"][sid]["encrypted_key"]))
        ek[10] ^= 0xFF  # flip a byte in the OAEP wrapped key
        blob["slots"][sid]["encrypted_key"] = base64.b64encode(bytes(ek)).decode()
        assert decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"]) is None


# ── MGF1-SHA256 fallback (pre-v57 compatibility) ──


class TestMgf1Fallback:
    def test_sha256_mgf1_blob_decrypts_via_fallback(self, lion_keypair, slave_keypair):
        """Simulate a pre-v57 peer that encrypted with MGF1-SHA256 throughout.

        decrypt_body must fall back to SHA256 when the SHA1 path fails."""
        import os

        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        body = {"legacy": True}
        plaintext = canonical_json(body)
        aes_key = os.urandom(32)
        iv = os.urandom(12)
        aesgcm = AESGCM(aes_key)
        ciphertext = aesgcm.encrypt(iv, plaintext, None)

        # Wrap the AES key with MGF1-SHA256 instead of MGF1-SHA1.
        pk, der = _load_pubkey_der(slave_keypair["pub_pem"])
        wrapped = pk.encrypt(
            aes_key,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        sid = slot_id_for_pubkey(der)
        blob = {
            "mesh_id": "legacy",
            "version": 1,
            "created_at": 0,
            "slots": {sid: {"encrypted_key": base64.b64encode(wrapped).decode(), "iv": base64.b64encode(iv).decode()}},
            "ciphertext": base64.b64encode(ciphertext).decode(),
        }
        blob["signature"] = sign_blob(blob, lion_keypair["priv_pem"])

        out = decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"])
        assert out is not None
        assert json.loads(out) == body


# ── Key rotation ──


class TestKeyRotation:
    def test_old_key_still_decrypts_until_reencrypt(self, lion_keypair, slave_keypair):
        """A blob encrypted to an old pubkey is unreadable by a rotated key."""
        blob = encrypt_body(
            mesh_id="m",
            version=1,
            created_at=0,
            body={"x": 1},
            recipients=[("slave", slave_keypair["pub_pem"])],
            signer_privkey_pem=lion_keypair["priv_pem"],
        )
        # Rotate: new keypair for same node — the old blob cannot be read.
        new_priv, _, new_der = generate_keypair()
        assert decrypt_body(blob, new_priv, new_der) is None
        # But the original keypair still works.
        assert decrypt_body(blob, slave_keypair["priv_pem"], slave_keypair["pub_der"]) is not None

    def test_signer_rotation_invalidates_old_signatures(self, lion_keypair, slave_keypair):
        """If Lion rotates, old Lion-signed blobs no longer verify under the new pubkey."""
        _new_priv, new_pub, _ = generate_keypair()
        blob = encrypt_body(
            mesh_id="m",
            version=1,
            created_at=0,
            body={},
            recipients=[("slave", slave_keypair["pub_pem"])],
            signer_privkey_pem=lion_keypair["priv_pem"],
        )
        assert verify_signature(blob, new_pub) is False
        assert verify_signature(blob, lion_keypair["pub_pem"]) is True

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
Python VaultCrypto — mirrors Java VaultCrypto for desktop collar vault support.

Encryption:  AES-256-GCM body, RSA-OAEP key wrap per recipient.
             Main OAEP hash:  SHA-256
             MGF1 hash:       SHA-1  (see landmine #1 in project_collar_landmines.md)
Signatures:  RSA-PKCS1v15-SHA256 over canonical JSON (sort_keys, no whitespace).

MGF1 uses SHA-1 because AndroidKeyStore's RSA-OAEP provider internally calls
MGF1 with SHA-1 regardless of what OAEPParameterSpec you pass — it only
honors the parameter if SHA-1 is explicitly authorized in setDigests(). To
make hardware-backed node keys decryptable by the same ciphertext a Python
or Java software key would produce, the whole protocol uses MGF1-SHA1.

This has no practical security cost: MGF1 uses the hash as a PRF, where
SHA-1's collision weakness is not relevant.

Decrypt has a fallback to MGF1-SHA256 for blobs produced by un-upgraded
pre-v57 clients during the transition window. Remove once all peers are on
the new protocol.

The canonical JSON format must be byte-identical to the Java canonicalJson()
and Python json.dumps(sort_keys=True, separators=(",",":"), ensure_ascii=True).
"""

import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding as asym_padding
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def canonical_json(obj):
    """Canonical JSON bytes — matches Java and Python mesh canonical_json."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")


def slot_id_for_pubkey(pubkey_der):
    """SHA256(pubkey_DER)[:12] hex — the slot identifier for a node."""
    return hashlib.sha256(pubkey_der).hexdigest()[:12]


def _strip_pem(key_str):
    """Strip PEM headers and whitespace, return raw base64 string."""
    for hdr in (
        "-----BEGIN PUBLIC KEY-----",
        "-----END PUBLIC KEY-----",
        "-----BEGIN PRIVATE KEY-----",
        "-----END PRIVATE KEY-----",
        "-----BEGIN RSA PRIVATE KEY-----",
        "-----END RSA PRIVATE KEY-----",
    ):
        key_str = key_str.replace(hdr, "")
    return key_str.replace("\n", "").replace("\r", "").replace(" ", "")


def _load_pubkey_der(pubkey_b64_or_pem):
    """Return (PublicKey, der_bytes) from a base64 or PEM public key."""
    raw = _strip_pem(pubkey_b64_or_pem)
    der = base64.b64decode(raw)
    pk = serialization.load_der_public_key(der)
    return pk, der


def _load_privkey(privkey_pem):
    """Load an RSA private key from PEM string."""
    if "-----" not in privkey_pem:
        # Raw base64 — wrap as PKCS8 PEM
        privkey_pem = "-----BEGIN PRIVATE KEY-----\n" + privkey_pem + "\n-----END PRIVATE KEY-----"
    return serialization.load_pem_private_key(privkey_pem.encode(), password=None)


# ── Signature ──


def verify_signature(blob, lion_pubkey_str):
    """Verify RSA-PKCS1v15-SHA256 signature on a vault blob.
    Returns True if valid, False otherwise."""
    sig_b64 = blob.get("signature")
    if not sig_b64:
        return False
    try:
        pk, _ = _load_pubkey_der(lion_pubkey_str)
        signed = {k: v for k, v in blob.items() if k != "signature"}
        data = canonical_json(signed)
        sig = base64.b64decode(sig_b64)
        pk.verify(sig, data, asym_padding.PKCS1v15(), hashes.SHA256())
        return True
    except Exception:
        return False


def sign_blob(blob, privkey_pem):
    """Sign canonical_json(blob minus signature) with an RSA private key.
    Returns base64 signature string."""
    pk = _load_privkey(privkey_pem)
    signed = {k: v for k, v in blob.items() if k != "signature"}
    data = canonical_json(signed)
    sig = pk.sign(data, asym_padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


# ── Decryption ──


def decrypt_body(blob, my_privkey_pem, my_pubkey_der):
    """Decrypt a vault blob's body using the node's RSA private key.
    Returns the decrypted JSON string, or None on failure.

    blob must have: slots -> {slotId: {encrypted_key, iv}}, ciphertext
    """
    try:
        my_slot_id = slot_id_for_pubkey(my_pubkey_der)
        slots = blob.get("slots", {})
        slot = slots.get(my_slot_id)
        if not slot:
            return None

        ek_b64 = slot.get("encrypted_key")
        iv_b64 = slot.get("iv")
        ct_b64 = blob.get("ciphertext")
        if not all((ek_b64, iv_b64, ct_b64)):
            return None

        privkey = _load_privkey(my_privkey_pem)
        ek_bytes = base64.b64decode(ek_b64)
        # Try MGF1-SHA1 first (v57+ protocol — compatible with AndroidKeyStore).
        # Fall back to MGF1-SHA256 for blobs produced by pre-v57 peers during
        # the transition window; remove once all peers have upgraded.
        try:
            aes_key = privkey.decrypt(
                ek_bytes,
                asym_padding.OAEP(
                    mgf=asym_padding.MGF1(algorithm=hashes.SHA1()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )
        except Exception:
            aes_key = privkey.decrypt(
                ek_bytes,
                asym_padding.OAEP(
                    mgf=asym_padding.MGF1(algorithm=hashes.SHA256()),
                    algorithm=hashes.SHA256(),
                    label=None,
                ),
            )

        iv = base64.b64decode(iv_b64)
        ciphertext = base64.b64decode(ct_b64)
        aesgcm = AESGCM(aes_key)
        plaintext = aesgcm.decrypt(iv, ciphertext, None)
        return plaintext.decode("utf-8")
    except Exception:
        return None


# ── Encryption ──


def encrypt_body(mesh_id, version, created_at, body, recipients, signer_privkey_pem):
    """Encrypt a body dict into a vault blob and sign it.

    recipients: list of (node_id, pubkey_b64_or_pem) tuples.
    Returns the complete blob dict with signature.
    """
    plaintext = canonical_json(body)

    # Fresh AES-256 key + 12-byte IV
    aes_key = os.urandom(32)
    iv = os.urandom(12)
    aesgcm = AESGCM(aes_key)
    ciphertext = aesgcm.encrypt(iv, plaintext, None)

    slots = {}
    for _node_id, pubkey_str in recipients:
        pk, der = _load_pubkey_der(pubkey_str)
        wrapped_key = pk.encrypt(
            aes_key,
            asym_padding.OAEP(
                mgf=asym_padding.MGF1(algorithm=hashes.SHA1()),
                algorithm=hashes.SHA256(),
                label=None,
            ),
        )
        sid = slot_id_for_pubkey(der)
        slots[sid] = {
            "encrypted_key": base64.b64encode(wrapped_key).decode(),
            "iv": base64.b64encode(iv).decode(),
        }

    blob = {
        "mesh_id": mesh_id,
        "version": version,
        "created_at": created_at,
        "slots": slots,
        "ciphertext": base64.b64encode(ciphertext).decode(),
    }
    blob["signature"] = sign_blob(blob, signer_privkey_pem)
    return blob


# ── Key generation ──


def generate_keypair():
    """Generate an RSA-2048 keypair. Returns (privkey_pem, pubkey_pem, pubkey_der)."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    privkey_pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    pub = key.public_key()
    pubkey_pem = pub.public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode()
    pubkey_der = pub.public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return privkey_pem, pubkey_pem, pubkey_der

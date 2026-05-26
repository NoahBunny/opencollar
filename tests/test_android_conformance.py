"""Java ↔ Python conformance spec for the security-critical wire formats.

The Android apps reimplement, in Java, three formats that MUST match the Python
server byte-for-byte or signatures silently fail to verify:

  1. canonical_json  — sorted keys, no whitespace, ASCII-escaped. Used as the
     signing input for vault blobs AND gossip orders. (controller/slave
     VaultCrypto.canonicalJson ↔ focuslock_mesh.canonical_json ↔
     shared/focuslock_vault.canonical_json)
  2. the orders signature — sign_orders/verify_signature over canonical_json.
     This is exactly what the Phase-2 Collar /mesh/sync fix must reproduce to
     verify gossip orders without bricking delivery.
  3. the message pipe-payload — "{mesh}|{node}|{from}|{text}|{pinned}|{mandatory}|{ts}"
     signed on /messages/send (focuslock-mail.py) and rebuilt by both clients.

This module has two halves:

  * TestCanonicalFormsSpec / TestOrdersSignatureSpec / TestMessagePayloadSpec —
    pure Python golden vectors. They ALWAYS run and serve as the executable
    contract the Java side is held to.
  * TestJavaConformance — runs a compiled Java conformance CLI and compares it
    to the Python golden. Skipped unless ANDROID_CONFORMANCE_CLI points at a
    runnable CLI (so it's a no-op locally / on the JRE-only dev box, and wired
    up in CI's JDK-equipped build-android job). Mirrors the skip pattern in
    tests/ui/conftest.py.
"""

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))


# A representative object that exercises every drift point: key sorting at two
# levels, a non-ASCII char (must become \uXXXX under ensure_ascii), an int, and
# a bool (must serialize as lowercase `true`).
GOLDEN_OBJ = {"b": 1, "a": "x", "u": "⚡", "n": {"z": True, "y": 2}}
GOLDEN_CANONICAL = '{"a":"x","b":1,"n":{"y":2,"z":true},"u":"\\u26a1"}'


def _pipe_send_payload(mesh, node, who, text, pinned, mandatory, ts):
    """Mirror of focuslock-mail.py's /messages/send signed payload."""
    return f"{mesh}|{node}|{who}|{text}|{'1' if pinned else '0'}|{'1' if mandatory else '0'}|{ts}"


GOLDEN_SEND_PAYLOAD = "m1|controller|lion|hi|0|1|123"


# ──────────────────────── canonical_json (the signing input) ────────────────────────


class TestCanonicalFormsSpec:
    def test_mesh_canonical_json_matches_golden(self):
        from focuslock_mesh import canonical_json

        assert canonical_json(GOLDEN_OBJ).decode("utf-8") == GOLDEN_CANONICAL

    def test_vault_canonical_json_matches_golden(self):
        from focuslock_vault import canonical_json as vault_canonical

        assert vault_canonical(GOLDEN_OBJ).decode("utf-8") == GOLDEN_CANONICAL

    def test_mesh_and_vault_canonical_agree(self):
        from focuslock_vault import canonical_json as vault_canonical

        from focuslock_mesh import canonical_json as mesh_canonical

        assert mesh_canonical(GOLDEN_OBJ) == vault_canonical(GOLDEN_OBJ)

    def test_non_ascii_is_escaped(self):
        """ensure_ascii=True — the Java side must \\u-escape non-ASCII too."""
        from focuslock_mesh import canonical_json

        assert "\\u26a1" in canonical_json(GOLDEN_OBJ).decode("utf-8")
        assert "⚡" not in canonical_json(GOLDEN_OBJ).decode("utf-8")


# ──────────────────────── orders signature (the Collar /mesh/sync fix target) ────────────────────────


class TestOrdersSignatureSpec:
    def test_sign_verify_roundtrip(self, lion_keypair):
        from focuslock_mesh import sign_orders, verify_signature

        orders = {"paywall": "25", "active": "1"}
        sig = sign_orders(orders, lion_keypair["priv_pem"])
        assert sig
        assert verify_signature(orders, sig, lion_keypair["pub_pem"]) is True

    def test_signature_is_deterministic(self, lion_keypair):
        """PKCS1v15 is deterministic — a Java signer over the same canonical
        bytes must produce the identical signature."""
        from focuslock_mesh import sign_orders

        orders = {"paywall": "25", "active": "1"}
        assert sign_orders(orders, lion_keypair["priv_pem"]) == sign_orders(orders, lion_keypair["priv_pem"])

    def test_tampered_orders_rejected(self, lion_keypair):
        from focuslock_mesh import sign_orders, verify_signature

        sig = sign_orders({"paywall": "25"}, lion_keypair["priv_pem"])
        assert verify_signature({"paywall": "999"}, sig, lion_keypair["pub_pem"]) is False


# ──────────────────────── message pipe-payload ────────────────────────


class TestMessagePayloadSpec:
    def test_send_payload_format(self):
        assert _pipe_send_payload("m1", "controller", "lion", "hi", False, True, 123) == GOLDEN_SEND_PAYLOAD

    def test_send_payload_signature_verifies(self, lion_keypair):
        """Build the payload, sign with the Lion key the way the clients do, and
        verify the way focuslock-mail.py's /messages/send does."""
        import base64

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        payload = _pipe_send_payload("m1", "controller", "lion", "hi", False, True, 123)
        priv = serialization.load_pem_private_key(lion_keypair["priv_pem"].encode(), password=None)
        sig = base64.b64encode(priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()

        pub = serialization.load_pem_public_key(lion_keypair["pub_pem"].encode())
        # Must not raise:
        pub.verify(base64.b64decode(sig), payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())


# ──────────────────────── Java half (CI only) ────────────────────────

# shlex.split so the command can carry a quoted classpath with spaces (the repo
# path contains spaces: "Lion's Share + Bunny Tasker").
_CLI = os.environ.get("ANDROID_CONFORMANCE_CLI", "")
_CLI_ARGV = shlex.split(_CLI) if _CLI else []
_java_cli_available = bool(_CLI_ARGV) and shutil.which(_CLI_ARGV[0]) is not None

_CLI_SLAVE = os.environ.get("ANDROID_CONFORMANCE_CLI_SLAVE", "")
_CLI_SLAVE_ARGV = shlex.split(_CLI_SLAVE) if _CLI_SLAVE else []
_slave_cli_available = bool(_CLI_SLAVE_ARGV) and shutil.which(_CLI_SLAVE_ARGV[0]) is not None


@pytest.mark.skipif(
    not _java_cli_available,
    reason="ANDROID_CONFORMANCE_CLI not set / not runnable — Java conformance runs in CI's build-android job",
)
class TestJavaConformance:
    """Drives a compiled Java conformance CLI (android/controller/test/.../ConformanceCli.java).
    Contract: subcommand on argv, input on stdin, result on stdout.
        canonical                 stdin=JSON object  -> canonical_json string
        sign-string <privKeyPem>  stdin=payload      -> base64 RSA-SHA256 signature
    See QA Layer 2/3 plan + docs/ANDROID-CONFORMANCE.md for how to build it."""

    def _run(self, *args, stdin=""):
        cmd = _CLI_ARGV + list(args)
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=30, check=True).stdout

    def test_java_canonical_matches_python(self):
        out = self._run("canonical", stdin=json.dumps(GOLDEN_OBJ)).strip()
        assert out == GOLDEN_CANONICAL

    def test_java_message_signature_verifies_in_python(self, lion_keypair):
        """Java VaultCrypto.signString over the pipe-payload produces a signature
        the Python server accepts — i.e. a Lion's Share send will verify on
        /messages/send (focuslock-mail.py:4775-4783)."""
        import base64

        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding

        sig_b64 = self._run("sign-string", lion_keypair["priv_pem"], stdin=GOLDEN_SEND_PAYLOAD).strip()
        pub = serialization.load_pem_public_key(lion_keypair["pub_pem"].encode())
        pub.verify(
            base64.b64decode(sig_b64),
            GOLDEN_SEND_PAYLOAD.encode("utf-8"),
            padding.PKCS1v15(),
            hashes.SHA256(),
        )

    def test_java_order_signature_accepted_by_mesh_verify(self, lion_keypair):
        """The Phase-2 Collar /mesh/sync fix gate: a Java-signed orders document
        must pass the SAME Python verify the Collar will mirror. Java signs the
        canonical bytes of the orders; Python's mesh verify_signature re-canon-
        icalizes and accepts. If the canonical forms ever drift, this fails."""
        from focuslock_mesh import canonical_json, verify_signature

        orders = {"paywall": "25", "active": "1", "pinned_message": "be good"}
        canonical = canonical_json(orders).decode("utf-8")
        sig_b64 = self._run("sign-string", lion_keypair["priv_pem"], stdin=canonical).strip()
        assert verify_signature(orders, sig_b64, lion_keypair["pub_pem"]) is True


# ──────────────────────── Collar (slave) /mesh/sync verification ────────────────────────


def _sign_orders_py(orders, priv_pem):
    """Sign orders exactly as the relay/Lion does: RSA-SHA256 over canonical_json."""
    import base64

    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    from focuslock_mesh import canonical_json

    priv = serialization.load_pem_private_key(priv_pem.encode(), password=None)
    return base64.b64encode(priv.sign(canonical_json(orders), padding.PKCS1v15(), hashes.SHA256())).decode()


@pytest.mark.skipif(
    not _slave_cli_available,
    reason="ANDROID_CONFORMANCE_CLI_SLAVE not set / not runnable — runs in CI's build-android job",
)
class TestSlaveCollarConformance:
    """Proves the Collar's NEW /mesh/sync verification (ControlService.verifyMeshOrdersSignature,
    via slave VaultCrypto) accepts genuine Lion/relay-signed orders and rejects
    forgeries — i.e. the CRITICAL gossip-auth fix works against the real signer
    and can't silently brick gossip delivery."""

    def _run(self, *args, stdin=""):
        cmd = _CLI_SLAVE_ARGV + list(args)
        return subprocess.run(cmd, input=stdin, capture_output=True, text=True, timeout=30, check=True).stdout

    def test_slave_canonical_matches_python(self):
        assert self._run("canonical", stdin=json.dumps(GOLDEN_OBJ)).strip() == GOLDEN_CANONICAL

    def test_collar_accepts_lion_signed_orders(self, lion_keypair):
        orders = {"active": "1", "paywall": "500", "pinned_message": "no phone"}
        sig = _sign_orders_py(orders, lion_keypair["priv_pem"])
        out = self._run("verify-orders", sig, lion_keypair["pub_pem"], stdin=json.dumps(orders)).strip()
        assert out == "ok"

    def test_collar_rejects_forged_orders(self, lion_keypair, slave_keypair):
        """An attacker without Lion's key (e.g. signing with another key) is
        rejected — closes the unauthenticated-gossip lock/paywall hole."""
        orders = {"active": "1", "paywall": "999999"}
        forged = _sign_orders_py(orders, slave_keypair["priv_pem"])  # wrong key
        out = self._run("verify-orders", forged, lion_keypair["pub_pem"], stdin=json.dumps(orders)).strip()
        assert out == "fail"

    def test_collar_rejects_tampered_orders(self, lion_keypair):
        """Signature is valid but the orders were altered in flight (e.g. paywall
        bumped) — canonical bytes change, so verification fails."""
        signed = {"active": "1", "paywall": "25"}
        sig = _sign_orders_py(signed, lion_keypair["priv_pem"])
        tampered = {"active": "1", "paywall": "999999"}
        out = self._run("verify-orders", sig, lion_keypair["pub_pem"], stdin=json.dumps(tampered)).strip()
        assert out == "fail"

    # ── direct-mode /mesh/status signature (A2) ──

    @staticmethod
    def _status_core(paywall="0", locked=False, escapes=0):
        """The exact flat field set ControlService.handleMeshStatus signs and
        MainActivity.verifyStatusSignature rebuilds (native bool/int/str types)."""
        return {
            "locked": locked,
            "escapes": escapes,
            "paywall": paywall,
            "timer_remaining_ms": 0,
            "task_reps": 0,
            "task_done": 0,
            "offer": "",
            "offer_status": "",
            "sub_tier": "",
            "orders_version": 1,
        }

    def test_collar_signed_status_verifies_in_python(self, slave_keypair):
        """The Collar signs the direct-mode /mesh/status core with its bunny key;
        Lion's Share verifies with the stored bunny pubkey. Java (the Collar)
        signs, Python (standing in for the controller's VaultCrypto) accepts —
        proving the new direct-status auth can't silently reject genuine status."""
        from focuslock_mesh import verify_signature

        core = self._status_core(paywall="25", locked=True, escapes=2)
        sig = self._run("sign-status", slave_keypair["priv_pem"], stdin=json.dumps(core)).strip()
        assert verify_signature(core, sig, slave_keypair["pub_pem"]) is True

    def test_spoofed_status_field_rejected(self, slave_keypair):
        """A LAN MITM clears the paywall after signing — canonical bytes change,
        so the controller drops the status and keeps its last-good snapshot."""
        from focuslock_mesh import verify_signature

        core = self._status_core(paywall="25", locked=True)
        sig = self._run("sign-status", slave_keypair["priv_pem"], stdin=json.dumps(core)).strip()
        tampered = self._status_core(paywall="0", locked=False)  # attacker forges "unlocked, no fee"
        assert verify_signature(tampered, sig, slave_keypair["pub_pem"]) is False

    def test_collar_rejects_empty_signature(self, lion_keypair):
        orders = {"active": "1"}
        out = self._run("verify-orders", "", lion_keypair["pub_pem"], stdin=json.dumps(orders)).strip()
        assert out == "fail"

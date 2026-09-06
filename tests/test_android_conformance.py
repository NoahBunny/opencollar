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


# ──────────────────────── direct-mode /mesh/status wire format ────────────────────────

# The ten fields ControlService.handleMeshStatus signs with the bunny key.
STATUS_CORE_KEYS = (
    "locked",
    "escapes",
    "paywall",
    "timer_remaining_ms",
    "task_reps",
    "task_done",
    "offer",
    "offer_status",
    "sub_tier",
    "orders_version",
)

# Core names that the embedded orders document ALSO carries — earlier in the
# byte stream, because handleMeshStatus emits "orders" before the status fields.
SHADOWED_CORE_KEYS = ("paywall", "task_reps", "task_done", "offer", "offer_status", "sub_tier")


def _status_core(
    locked=True,
    escapes=0,
    paywall="0",
    timer_remaining_ms=900_000,
    task_reps=0,
    task_done=0,
    offer="",
    offer_status="",
    sub_tier="",
    orders_version=3,
):
    """Exactly what handleMeshStatus signs — native bool/int/str types."""
    return {
        "locked": locked,
        "escapes": escapes,
        "paywall": paywall,
        "timer_remaining_ms": timer_remaining_ms,
        "task_reps": task_reps,
        "task_done": task_done,
        "offer": offer,
        "offer_status": offer_status,
        "sub_tier": sub_tier,
        "orders_version": orders_version,
    }


def _status_wire(core, signature="SIG", orders_paywall=""):
    """Mirror of the /mesh/status body: orders_version, the embedded orders
    document, the signature, then the signed status fields, then nodes.

    orders_paywall defaults to "" — the real divergence. buildOrdersJson emits
    the raw Settings.Global row, while handleMeshStatus defaults an unset
    paywall to "0", so on a freshly paired Collar the two copies differ.
    """
    orders = (
        '{"lock_active":'
        + ("1" if core["locked"] else "0")
        + ',"message":"","task_text":""'
        + ',"task_reps":'
        + str(core["task_reps"])
        + ',"task_done":'
        + str(core["task_done"])
        + ',"mode":"basic"'
        + ',"paywall":"'
        + orders_paywall
        + '","paywall_original":""'
        + ',"unlock_at":0,"locked_at":0'
        + ',"offer":"'
        + core["offer"]
        + '"'
        + ',"offer_status":"'
        + core["offer_status"]
        + '"'
        + ',"sub_tier":"'
        + core["sub_tier"]
        + '"'
        + ',"released":""}'
    )
    nodes = (
        ',"nodes":{"sm-s908w":{"type":"phone","online":true,"orders_version":'
        + str(core["orders_version"])
        + ',"status":{"escapes":'
        + str(core["escapes"])
        + "}}}}"
    )
    return (
        '{"orders_version":'
        + str(core["orders_version"])
        + ',"orders":'
        + orders
        + ',"signature":"'
        + signature
        + '"'
        + ',"locked":'
        + ("true" if core["locked"] else "false")
        + ',"escapes":'
        + str(core["escapes"])
        + ',"paywall":"'
        + core["paywall"]
        + '"'
        + ',"timer_remaining_ms":'
        + str(core["timer_remaining_ms"])
        + ',"task_reps":'
        + str(core["task_reps"])
        + ',"task_done":'
        + str(core["task_done"])
        + ',"offer":"'
        + core["offer"]
        + '"'
        + ',"offer_status":"'
        + core["offer_status"]
        + '"'
        + ',"sub_tier":"'
        + core["sub_tier"]
        + '"'
        + ',"addresses":["192.168.199.42"],"port":8435'
        + nodes
    )


def _first_match_core(body):
    """The rebuild Lion's Share shipped before StatusCore: MainActivity's
    indexOf-based parseJson* helpers, which take the FIRST match anywhere in
    the body. Kept as an executable record of the bug this format now guards."""

    def _str(key):
        i = body.find(f'"{key}":"')
        if i < 0:
            return ""
        i += len(f'"{key}":"')
        return body[i : body.find('"', i)]

    def _num(key):
        i = body.find(f'"{key}":')
        if i < 0:
            return 0
        i = body.find(":", i) + 1
        e = i
        while e < len(body) and body[e] not in ",}":
            e += 1
        return int(body[i:e].strip())

    def _bool(key):
        i = body.find(f'"{key}":')
        return i >= 0 and body[i + len(f'"{key}":') :].lstrip().startswith("true")

    return {
        "locked": _bool("locked"),
        "escapes": _num("escapes"),
        "paywall": _str("paywall"),
        "timer_remaining_ms": _num("timer_remaining_ms"),
        "task_reps": _num("task_reps"),
        "task_done": _num("task_done"),
        "offer": _str("offer"),
        "offer_status": _str("offer_status"),
        "sub_tier": _str("sub_tier"),
        "orders_version": _num("orders_version"),
    }


class TestStatusWireSpec:
    """The direct-mode /mesh/status contract: the signed core must be rebuilt
    from the TOP-LEVEL object only.

    The body embeds the whole orders document, which repeats six of the ten core
    key names before the signed ones appear. Reading the first match picked up
    the orders copies; five agreed by luck (same Settings.Global row) but
    "paywall" did not, so every status from a Collar with no balance charged was
    rejected as forged and Lion's Share froze on its last-good snapshot. This is
    the executable contract StatusCore.fromWire is held to.
    """

    def test_orders_document_still_repeats_core_key_names(self):
        """If this ever stops being true the hazard is gone — but until then,
        scoping is load-bearing, not defensive tidiness."""
        body = _status_wire(_status_core())
        orders = json.loads(body)["orders"]
        assert set(SHADOWED_CORE_KEYS) <= set(orders), "orders no longer shadows the core"
        for key in SHADOWED_CORE_KEYS:
            assert body.index(f'"{key}"') < body.index('"signature"'), f"{key} shadow is not first"

    def test_top_level_scope_rebuilds_the_signed_core(self):
        core = _status_core(paywall="0")
        parsed = json.loads(_status_wire(core))
        assert {k: parsed[k] for k in STATUS_CORE_KEYS} == core

    def test_first_match_rebuild_reads_the_orders_copy(self):
        """The shipped bug, pinned: unset paywall signs as "0" but scans as ""."""
        core = _status_core(paywall="0")
        assert _first_match_core(_status_wire(core, orders_paywall=""))["paywall"] == ""
        assert core["paywall"] == "0"

    def test_first_match_rebuild_breaks_the_signature(self, slave_keypair):
        from focuslock_mesh import sign_orders, verify_signature

        core = _status_core(paywall="0")
        sig = sign_orders(core, slave_keypair["priv_pem"])
        body = _status_wire(core, signature=sig, orders_paywall="")
        assert verify_signature(_first_match_core(body), sig, slave_keypair["pub_pem"]) is False
        parsed = json.loads(body)
        scoped = {k: parsed[k] for k in STATUS_CORE_KEYS}
        assert verify_signature(scoped, sig, slave_keypair["pub_pem"]) is True

    def test_scoped_rebuild_verifies_across_collar_states(self, slave_keypair):
        from focuslock_mesh import sign_orders, verify_signature

        for core in (
            _status_core(),  # fresh pair, no balance
            _status_core(paywall="25", escapes=2),  # after a charge
            _status_core(
                paywall="25", task_reps=3, task_done=1, offer="unlock", offer_status="pending", sub_tier="silver"
            ),
            _status_core(locked=False, timer_remaining_ms=0),  # released
        ):
            sig = sign_orders(core, slave_keypair["priv_pem"])
            parsed = json.loads(_status_wire(core, signature=sig, orders_paywall=core["paywall"]))
            scoped = {k: parsed[k] for k in STATUS_CORE_KEYS}
            assert verify_signature(scoped, sig, slave_keypair["pub_pem"]) is True, core

    def test_rewriting_only_the_orders_copies_cannot_move_the_core(self, slave_keypair):
        """The inverse attack: a LAN MITM edits the shadow copies hoping the
        controller reads those. Scoped parsing leaves the signed core intact."""
        from focuslock_mesh import sign_orders, verify_signature

        core = _status_core(paywall="25", escapes=2)
        sig = sign_orders(core, slave_keypair["priv_pem"])
        body = _status_wire(core, signature=sig, orders_paywall="0")  # attacker clears the fee
        parsed = json.loads(body)
        scoped = {k: parsed[k] for k in STATUS_CORE_KEYS}
        assert scoped["paywall"] == "25"
        assert verify_signature(scoped, sig, slave_keypair["pub_pem"]) is True

    def test_tampered_top_level_field_still_rejected(self, slave_keypair):
        from focuslock_mesh import sign_orders, verify_signature

        signed = _status_core(locked=True, paywall="25", escapes=2)
        sig = sign_orders(signed, slave_keypair["priv_pem"])
        forged = json.loads(_status_wire(_status_core(locked=False, paywall="0"), signature=sig))
        scoped = {k: forged[k] for k in STATUS_CORE_KEYS}
        assert verify_signature(scoped, sig, slave_keypair["pub_pem"]) is False


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
        status-core               stdin=/mesh/status body -> canonical_json of the
                                                       rebuilt status core
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

    def test_java_status_core_is_scoped_to_top_level(self):
        """StatusCore.fromWire (what MainActivity feeds to verifySignature) must
        canonicalize to the bytes the Collar signed — i.e. it reads the signed
        top-level fields, not the same-named copies inside the embedded orders
        document. The unset-paywall case is the one that shipped broken."""
        from focuslock_mesh import canonical_json

        core = _status_core(paywall="0")
        out = self._run("status-core", stdin=_status_wire(core, orders_paywall=""))
        assert out == canonical_json(core).decode("utf-8")

    def test_java_status_core_ignores_rewritten_orders_copies(self):
        from focuslock_mesh import canonical_json

        core = _status_core(
            paywall="25", escapes=2, task_reps=3, task_done=1, offer="unlock", offer_status="pending", sub_tier="silver"
        )
        out = self._run("status-core", stdin=_status_wire(core, orders_paywall="999"))
        assert out == canonical_json(core).decode("utf-8")

    def test_java_signed_status_verifies_end_to_end(self, slave_keypair):
        """Python stands in for the Collar's signer: sign the core, ship it in a
        real wire body, and let the REAL Java rebuild reproduce the signing
        bytes. Any drift in fields, types or scoping shows up here."""
        from focuslock_mesh import canonical_json, sign_orders, verify_signature

        core = _status_core()
        sig = sign_orders(core, slave_keypair["priv_pem"])
        body = _status_wire(core, signature=sig, orders_paywall="")
        rebuilt_canonical = self._run("status-core", stdin=body)
        assert rebuilt_canonical == canonical_json(core).decode("utf-8")
        assert verify_signature(core, sig, slave_keypair["pub_pem"]) is True

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

    def test_slave_direct_post_canonicalize_matches_python(self):
        """SigVerifier.canonicalize (the RSA-SHA256 signing input for the Collar's
        direct /api/* POSTs, Audit C1) must match the Python reference
        c1_canonicalize byte-for-byte — drift there silently breaks every
        direct-post signature. Feed the golden vectors through BOTH and compare."""
        try:
            from test_http import c1_canonicalize
        except ImportError:
            from tests.test_http import c1_canonicalize

        # The same vectors pinned in tests/test_http.py and SigVerifierTest.java,
        # incl. the malformed-JSON _raw fallback and a non-ASCII (UTF-8) value.
        vectors = [
            ("/api/lock", '{"duration_min":60,"mode":"basic"}', 1_700_000_000_000, "abc12345"),
            ("/api/unlock", "", 1_700_000_000_000, "abc12345"),
            ("/api/speak", "hello world", 111, "nonce"),
            ("/api/lock", '{"zebra":1,"apple":2,"mango":3}', 0, "n"),
            ("/api/lock", '{"shame":true,"silent":false}', 0, "n"),
            ("/api/set-volume", '{"level":3.0}', 0, "n"),
            ("/api/set-geofence", '{"lat":40.7128}', 0, "n"),
            ("/api/lock", '{"duration_min":60,"message":null,"mode":"basic"}', 0, "n"),
            ("/api/message", '{"text":"hi & bye | done"}', 0, "n"),
            ("/api/message", '{"text":"héllo"}', 0, "n"),
            ("/api/add-paywall", '{"amount":500}', 42, "nn"),
            ("/x", "{bad", 0, "n"),
        ]
        for path, body, ts, nonce in vectors:
            cmd = [*_CLI_SLAVE_ARGV, "canonicalize", path, str(ts), nonce]
            # Body goes on stdin verbatim (no added newline); force UTF-8 so the
            # non-ASCII vector is byte-stable regardless of the runner's locale.
            out = subprocess.run(cmd, input=body, capture_output=True, encoding="utf-8", timeout=30, check=True).stdout
            expected = c1_canonicalize(path, body, ts, nonce)
            assert out == expected, f"{path} {body!r}: Java {out!r} != Python {expected!r}"

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


class TestInterestRateConformance:
    """The compound-interest rate table, mirrored into Java by hand.

    `refreshCostToWait()` in Bunny Tasker exists to tell a bunny what waiting
    costs. It cannot import `shared/focuslock_penalties.py`, so the rates are
    retyped as a ternary chain — and they drifted: bronze read 1.08 in Java from
    the day the screen shipped while the relay's `check_compound_interest()`
    charged 1.10. The one number the bunny was given to act on was low, on the
    tier most likely to be carrying a balance.

    A source-text check rather than a golden vector, because the defect is
    literally a mistyped constant. No JDK needed, so unlike TestJavaConformance
    this always runs.
    """

    COMPANION_MAIN = REPO_ROOT / "android" / "companion" / "src" / "com" / "bunnytasker" / "MainActivity.java"

    def _java_rates(self):
        """Parse the tier→rate ternary chain out of refreshCostToWait()."""
        import re

        source = self.COMPANION_MAIN.read_text()
        match = re.search(r"double rate = ([^;]+);", source)
        assert match, "refreshCostToWait() no longer declares `double rate = ...`"
        expr = match.group(1)
        rates = {tier: float(rate) for tier, rate in re.findall(r'"(\w+)"\.equals\(tier\)\s*\?\s*([\d.]+)', expr)}
        trailing = re.search(r":\s*([\d.]+)\s*$", expr)
        assert trailing, f"no default branch in: {expr}"
        rates[""] = float(trailing.group(1))  # unsubscribed falls through to the default
        return rates

    def test_java_rates_match_the_python_table(self):
        from focuslock_penalties import COMPOUND_INTEREST_RATE_BY_TIER

        java = self._java_rates()
        for tier, expected in COMPOUND_INTEREST_RATE_BY_TIER.items():
            assert tier in java, f"Java has no branch for tier {tier!r}"
            assert java[tier] == pytest.approx(expected), (
                f"tier {tier!r}: Java quotes {java[tier]}, relay charges {expected}"
            )

    def test_java_defines_no_tier_the_relay_does_not_price(self):
        """A tier in Java but not in the table is a rate nothing enforces."""
        from focuslock_penalties import COMPOUND_INTEREST_RATE_BY_TIER

        extra = set(self._java_rates()) - set(COMPOUND_INTEREST_RATE_BY_TIER)
        assert not extra, f"Java prices tiers the relay does not: {sorted(extra)}"

    def test_unknown_tier_is_not_quoted_a_discount(self):
        """`compound_interest_rate()` defaults an unrecognised tier to bronze.
        Java's trailing branch must be at least as expensive, or an unknown tier
        is quoted cheaper than it will be charged."""
        from focuslock_penalties import compound_interest_rate

        assert self._java_rates()[""] >= compound_interest_rate("some-unknown-tier")

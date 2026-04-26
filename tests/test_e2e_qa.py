"""End-to-end HTTP-level QA for the enforcement hot-path endpoints.

The existing suite covers each action's apply_fn at the unit level
(`mesh_apply_order(action, params, orders)`). This file drives the same
actions through the full HTTP dispatch — live `HTTPServer` + real JSON
bodies + real signature verification — so that wire-format drift (header
parsing, canonical payload layout, Content-Length, response shape) is
caught by CI rather than on a phone.

Three endpoints covered here — each is the server-side half of a
bunny/lion-signed event the Android apps fire:

- `/api/mesh/{id}/gamble` (bunny-signed) — Collar's `doGamble` proxies
  to this. Admin-authed gamble via `/admin/order` is covered separately
  in `test_paywall_hardening.py::TestAdminGambleEndpoint`.
- `/api/mesh/{id}/escape-event` (bunny-signed) — central P2-paywall
  write path for `escape`, `tamper_attempt`, `geofence`, `sit_boy`.
- `/api/mesh/{id}/messages/send` (lion OR bunny signed depending on
  `from`) — C4 audit fix path.
"""

import base64
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
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_e2eqa", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_e2eqa"] = mod
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


def _http_post(url, body, headers=None):
    data = json.dumps(body).encode()
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(url, data=data, headers=h, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(pub_der).decode()


def _sign(priv, payload):
    sig = priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


@pytest.fixture
def seeded_mesh(mail_module):
    """Seed a mesh with one bunny-keyed node + one lion pubkey. Returns
    the handles tests need to sign realistic requests. Tears down after
    each test so state doesn't leak across classes."""
    mesh_id = "e2e-" + str(int(time.time() * 1000000))
    node_id = "bunny-node-1"
    bunny_priv, bunny_pub = _keypair()
    lion_priv, lion_pub = _keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": lion_pub,
        "auth_token": "test-token",
        "invite_code": "",
        "invite_expires_at": 0,
        "invite_uses": 0,
        "pin": "0000",
        "created_at": int(time.time()),
        "nodes": {
            node_id: {
                "node_id": node_id,
                "bunny_pubkey": bunny_pub,
                "registered_at": int(time.time()),
            },
        },
        "vault_only": False,
        "max_blobs_per_day": 5000,
        "max_total_bytes_mb": 100,
    }
    # Also register an orders doc so _resolve_orders returns something
    # usable for paywall reads.
    mail_module._orders_registry.get_or_create(mesh_id)
    try:
        yield {
            "mesh_id": mesh_id,
            "node_id": node_id,
            "bunny_priv": bunny_priv,
            "bunny_pub": bunny_pub,
            "lion_priv": lion_priv,
            "lion_pub": lion_pub,
        }
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)
        mail_module._orders_registry.docs.pop(mesh_id, None)


# ── /api/mesh/{id}/gamble — bunny-signed, Collar's proxy path ──


class TestGambleSignedE2E:
    """Bunny-signed gamble HTTP round-trip. The admin-authed equivalent
    (web-UI relay path) is in `TestAdminGambleEndpoint` — this covers
    the phone-initiated path via the Collar's `doGamble`, which signs
    with the bunny privkey and POSTs to /api/mesh/{id}/gamble."""

    def test_missing_signature_returns_400(self, live_server, seeded_mesh):
        """The endpoint declares signature as required — bare request
        must 400 on missing fields rather than silently no-op."""
        mesh = seeded_mesh["mesh_id"]
        status, body = _http_post(f"{live_server}/api/mesh/{mesh}/gamble", {"node_id": seeded_mesh["node_id"]})
        assert status == 400
        assert "signature" in body.get("error", "")

    def test_ts_out_of_window_returns_403(self, live_server, seeded_mesh):
        stale_ts = int(time.time() * 1000) - 10 * 60 * 1000
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|gamble|{stale_ts}"
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, body = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/gamble",
            {"node_id": seeded_mesh["node_id"], "ts": stale_ts, "signature": sig},
        )
        assert status == 403
        assert body.get("error") == "ts out of window"

    def test_bad_signature_returns_403(self, live_server, seeded_mesh):
        mesh = seeded_mesh["mesh_id"]
        node = seeded_mesh["node_id"]
        ts = int(time.time() * 1000)
        status, body = _http_post(
            f"{live_server}/api/mesh/{mesh}/gamble",
            {"node_id": node, "ts": ts, "signature": "AAAA" + "=" * 340},
        )
        assert status == 403
        assert body.get("error") == "invalid signature"

    def test_empty_paywall_returns_409(self, live_server, seeded_mesh, mail_module):
        mesh = seeded_mesh["mesh_id"]
        node = seeded_mesh["node_id"]
        mail_module._orders_registry.get(mesh).set("paywall", "0")
        ts = int(time.time() * 1000)
        payload = f"{mesh}|{node}|gamble|{ts}"
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, body = _http_post(
            f"{live_server}/api/mesh/{mesh}/gamble",
            {"node_id": node, "ts": ts, "signature": sig},
        )
        assert status == 409
        assert body.get("error") == "no paywall to gamble"

    def test_valid_signed_gamble_applies_and_returns_outcome(self, live_server, seeded_mesh, mail_module):
        """Walks a few flips to force both heads and tails, pins the
        response shape and the applied-state round-trip."""
        mesh = seeded_mesh["mesh_id"]
        node = seeded_mesh["node_id"]
        seen = set()
        for _ in range(40):
            mail_module._orders_registry.get(mesh).set("paywall", "100")
            ts = int(time.time() * 1000)
            payload = f"{mesh}|{node}|gamble|{ts}"
            sig = _sign(seeded_mesh["bunny_priv"], payload)
            status, body = _http_post(
                f"{live_server}/api/mesh/{mesh}/gamble",
                {"node_id": node, "ts": ts, "signature": sig},
            )
            assert status == 200
            assert body["result"] in ("heads", "tails")
            assert body["old_paywall"] == 100
            expected = 50 if body["result"] == "heads" else 200
            assert body["new_paywall"] == expected
            applied = int(mail_module._orders_registry.get(mesh).get("paywall", "0"))
            assert applied == expected
            seen.add(body["result"])
            if seen >= {"heads", "tails"}:
                break
        assert seen == {"heads", "tails"}


# ── /api/mesh/{id}/escape-event — central P2 paywall write path ──


class TestEscapeEventE2E:
    """Covers the P2 paywall-hardening event types routed through the
    bunny-signed /escape-event endpoint: escape (tiered), tamper_attempt
    ($500), geofence_breach ($100 + paywall_original seed), sit_boy
    (SMS-capped at $500). Auth gate identical across all of them;
    semantics tests per-type below.

    The server's field layout (focuslock-mail.py:3254 ff):
      - `event_type` is the type selector (NOT `type`)
      - sit_boy's amount is string-encoded in `details`, not `amount`
      - geofence type is `geofence_breach` (underscore)"""

    def _valid_body(self, seeded, event_type, extra=None):
        mesh = seeded["mesh_id"]
        node = seeded["node_id"]
        ts = int(time.time() * 1000)
        payload = f"{mesh}|{node}|{event_type}|{ts}"
        sig = _sign(seeded["bunny_priv"], payload)
        body = {"event_type": event_type, "node_id": node, "ts": ts, "signature": sig}
        if extra:
            body.update(extra)
        return body

    def test_invalid_event_type_returns_400(self, live_server, seeded_mesh):
        mesh = seeded_mesh["mesh_id"]
        body = self._valid_body(seeded_mesh, "escape")
        body["event_type"] = "not-a-real-type"
        status, resp = _http_post(f"{live_server}/api/mesh/{mesh}/escape-event", body)
        assert status == 400
        assert "event_type" in resp.get("error", "")

    def test_unsigned_returns_400_or_403(self, live_server, seeded_mesh):
        mesh = seeded_mesh["mesh_id"]
        status, _ = _http_post(
            f"{live_server}/api/mesh/{mesh}/escape-event",
            {"event_type": "escape", "node_id": seeded_mesh["node_id"]},
        )
        assert status in (400, 403)

    def test_escape_applies_tier_one_penalty(self, live_server, seeded_mesh, mail_module):
        mesh = seeded_mesh["mesh_id"]
        mail_module._orders_registry.get(mesh).set("paywall", "0")
        status, _ = _http_post(
            f"{live_server}/api/mesh/{mesh}/escape-event",
            self._valid_body(seeded_mesh, "escape"),
        )
        assert status == 200
        applied = int(mail_module._orders_registry.get(mesh).get("paywall", "0"))
        assert applied == 5  # tier 1 (first 3 escapes)

    def test_tamper_attempt_applies_500(self, live_server, seeded_mesh, mail_module):
        mesh = seeded_mesh["mesh_id"]
        mail_module._orders_registry.get(mesh).set("paywall", "0")
        status, _ = _http_post(
            f"{live_server}/api/mesh/{mesh}/escape-event",
            self._valid_body(seeded_mesh, "tamper_attempt"),
        )
        assert status == 200
        applied = int(mail_module._orders_registry.get(mesh).get("paywall", "0"))
        assert applied == 500

    def test_geofence_applies_100_and_seeds_original(self, live_server, seeded_mesh, mail_module):
        mesh = seeded_mesh["mesh_id"]
        orders = mail_module._orders_registry.get(mesh)
        orders.set("paywall", "0")
        orders.set("paywall_original", "0")
        status, _ = _http_post(
            f"{live_server}/api/mesh/{mesh}/escape-event",
            self._valid_body(seeded_mesh, "geofence_breach"),
        )
        assert status == 200
        assert int(orders.get("paywall", "0")) == 100
        assert int(orders.get("paywall_original", "0")) > 0

    def test_sit_boy_clamps_to_500(self, live_server, seeded_mesh, mail_module):
        """Server-clamps sit_boy amount to $500 — hijacked controller SIM
        can't drain via an inflated `details` field."""
        mesh = seeded_mesh["mesh_id"]
        mail_module._orders_registry.get(mesh).set("paywall", "0")
        body = self._valid_body(seeded_mesh, "sit_boy", {"details": "999999"})
        status, _ = _http_post(f"{live_server}/api/mesh/{mesh}/escape-event", body)
        assert status == 200
        applied = int(mail_module._orders_registry.get(mesh).get("paywall", "0"))
        assert applied <= 500

    def test_unknown_node_rejected(self, live_server, seeded_mesh):
        mesh = seeded_mesh["mesh_id"]
        ts = int(time.time() * 1000)
        payload = f"{mesh}|ghost-node|escape|{ts}"
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, _ = _http_post(
            f"{live_server}/api/mesh/{mesh}/escape-event",
            {"event_type": "escape", "node_id": "ghost-node", "ts": ts, "signature": sig},
        )
        assert status == 403


# ── /api/mesh/{id}/messages/send — lion-signed, C4 audit path ──


class TestMessagesSendE2E:
    """Covers the server half of the C4 audit fix — the lion signs a
    message + the server preserves the signature into the store so the
    bunny can later verify authenticity before the mandatory-reply
    auto-lock path fires.

    Signed payload layout (per focuslock-mail.py:3554):
        "{mesh_id}|{node_id}|{from_who}|{text}|{pinned}|{mandatory}|{ts}"
    where pinned/mandatory are '0'/'1' string-encoded."""

    def _send(self, seeded, text, pinned=False, mandatory=False, from_who="lion"):
        mesh = seeded["mesh_id"]
        node = seeded["node_id"]
        ts = int(time.time() * 1000)
        pinned_s = "1" if pinned else "0"
        mandatory_s = "1" if mandatory else "0"
        payload = f"{mesh}|{node}|{from_who}|{text}|{pinned_s}|{mandatory_s}|{ts}"
        # Lion signs when from=lion; bunny signs otherwise (server picks
        # the verifier key based on `from`).
        signer = seeded["lion_priv"] if from_who == "lion" else seeded["bunny_priv"]
        sig = _sign(signer, payload)
        return {
            "op": "send",
            "node_id": node,
            "from": from_who,
            "text": text,
            "pinned": pinned,
            "mandatory_reply": mandatory,
            "ts": ts,
            "signature": sig,
        }

    def test_unsigned_send_rejected(self, live_server, seeded_mesh):
        mesh = seeded_mesh["mesh_id"]
        status, _ = _http_post(
            f"{live_server}/api/mesh/{mesh}/messages/send",
            {"op": "send", "node_id": seeded_mesh["node_id"], "from": "lion", "text": "hi"},
        )
        assert status in (400, 403)

    def test_bad_signature_rejected(self, live_server, seeded_mesh):
        mesh = seeded_mesh["mesh_id"]
        body = self._send(seeded_mesh, "hello")
        body["signature"] = "AAAA" + "=" * 340  # valid b64, wrong sig
        status, _ = _http_post(f"{live_server}/api/mesh/{mesh}/messages/send", body)
        assert status == 403

    def test_valid_lion_send_stores_message(self, live_server, seeded_mesh, mail_module):
        mesh = seeded_mesh["mesh_id"]
        body = self._send(seeded_mesh, "test message 123", pinned=True)
        status, _ = _http_post(f"{live_server}/api/mesh/{mesh}/messages/send", body)
        assert status == 200
        # Verify the message was stored.
        store = mail_module._get_message_store(mesh)
        messages = store.get(limit=10)
        assert len(messages) >= 1
        match = [m for m in messages if m.get("text") == "test message 123"]
        assert len(match) == 1
        # C4 audit fix: signature + client ts preserved for recipient
        # verification (field is stored as `ts`, sharing the name with
        # server-assigned ts that MessageStore.add would otherwise default).
        assert match[0].get("signature") == body["signature"]
        assert match[0].get("ts") == body["ts"]

    def test_mandatory_reply_flag_preserved(self, live_server, seeded_mesh, mail_module):
        """The mandatory_reply flag is security-sensitive (bunny auto-
        locks on overdue). Pin that the bit is stored as signed."""
        mesh = seeded_mesh["mesh_id"]
        body = self._send(seeded_mesh, "must reply 456", mandatory=True)
        status, _ = _http_post(f"{live_server}/api/mesh/{mesh}/messages/send", body)
        assert status == 200
        store = mail_module._get_message_store(mesh)
        match = [m for m in store.get(limit=10) if m.get("text") == "must reply 456"]
        assert len(match) == 1
        assert match[0].get("mandatory_reply") is True

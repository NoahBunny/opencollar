"""Audit 2026-04-27 H-2 — slave-signed evidence webhooks.

The 2026-04-17 audit closed /webhook/bunny-message against forged
evidence emails by requiring a bunny-signed payload. Seven sibling
webhooks fire `send_evidence()` with the same shape and were missed:

  /webhook/compliment        /webhook/gratitude
  /webhook/love_letter       /webhook/offer
  /webhook/geofence-breach   /webhook/evidence-photo
  /webhook/subscription-charge

Each now requires (mesh_id, node_id, ts, signature) with canonical
payload "{mesh_id}|{node_id}|<webhook-type>|{ts_i}". Server returns
403 + min_collar_version=74 hint on missing/bad signature.

This file pins:
- missing-signature → 403 + min_collar_version=74 hint (per endpoint)
- bad-signature     → 403 invalid signature (per endpoint)
- ts-out-of-window  → 403 ts out of window (per endpoint)
- unknown-node      → 403 node not registered (per endpoint)
- unknown-mesh      → 404 mesh not found (per endpoint)
- valid sig         → 200 + the side effect (send_evidence) actually fires

The bunny-message regression shape lives in
tests/test_pairing.py::TestBunnyMessageSigned and continues to use
min_companion_version=53 (Bunny Tasker, not the Collar slave).
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

# (path-suffix, route-path) — the type embedded in the canonical
# signing payload matches the path suffix exactly.
EVIDENCE_WEBHOOKS = [
    ("compliment", "/webhook/compliment"),
    ("gratitude", "/webhook/gratitude"),
    ("love_letter", "/webhook/love_letter"),
    ("offer", "/webhook/offer"),
    ("geofence-breach", "/webhook/geofence-breach"),
    ("evidence-photo", "/webhook/evidence-photo"),
    ("subscription-charge", "/webhook/subscription-charge"),
]


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_h2", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_h2"] = mod
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


def _bunny_keypair():
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
def seeded(mail_module):
    """Seed a mesh + node with a bunny pubkey on file. The seven webhooks
    all verify against the same node.bunny_pubkey, so one fixture covers
    all of them."""
    mesh_id = "h2-mesh-" + str(int(time.time() * 1000))
    node_id = "collar-1"
    priv, pub_b64 = _bunny_keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": "",
        "auth_token": "test-token",
        "invite_code": "",
        "invite_expires_at": 0,
        "invite_uses": 0,
        "pin": "0000",
        "created_at": int(time.time()),
        "nodes": {
            node_id: {
                "node_id": node_id,
                "bunny_pubkey": pub_b64,
                "registered_at": int(time.time()),
            },
        },
        "vault_only": False,
        "max_blobs_per_day": 5000,
        "max_total_bytes_mb": 100,
    }
    try:
        yield {"mesh_id": mesh_id, "node_id": node_id, "priv": priv, "pub_b64": pub_b64}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


def _post(url, body):
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


# ── Missing signature → 403 + min_collar_version hint ──


@pytest.mark.parametrize("webhook_type,path", EVIDENCE_WEBHOOKS)
def test_missing_signature_returns_403_with_version_hint(live_server, seeded, webhook_type, path):
    status, body = _post(f"{live_server}{path}", {"text": "fake"})
    assert status == 403, f"{webhook_type} should reject unsigned: {status} {body}"
    assert body["error"] == "signature required"
    assert body["min_collar_version"] == 74


# ── Bad signature → 403 invalid signature ──


@pytest.mark.parametrize("webhook_type,path", EVIDENCE_WEBHOOKS)
def test_bad_signature_returns_403(live_server, seeded, webhook_type, path):
    ts = int(time.time() * 1000)
    status, body = _post(
        f"{live_server}{path}",
        {
            "text": "fake",
            "mesh_id": seeded["mesh_id"],
            "node_id": seeded["node_id"],
            "ts": ts,
            "signature": "AAAA" + "=" * 340,  # well-formed b64, wrong sig
        },
    )
    assert status == 403, f"{webhook_type}: {status} {body}"
    assert body["error"] == "invalid signature"


# ── Stale ts → 403 ts out of window ──


@pytest.mark.parametrize("webhook_type,path", EVIDENCE_WEBHOOKS)
def test_ts_out_of_window_returns_403(live_server, seeded, webhook_type, path):
    stale_ts = int(time.time() * 1000) - 10 * 60 * 1000  # 10 min ago
    payload = f"{seeded['mesh_id']}|{seeded['node_id']}|{webhook_type}|{stale_ts}"
    sig = _sign(seeded["priv"], payload)
    status, body = _post(
        f"{live_server}{path}",
        {
            "text": "old",
            "mesh_id": seeded["mesh_id"],
            "node_id": seeded["node_id"],
            "ts": stale_ts,
            "signature": sig,
        },
    )
    assert status == 403, f"{webhook_type}: {status} {body}"
    assert body["error"] == "ts out of window"


# ── Unknown node → 403 node not registered ──


@pytest.mark.parametrize("webhook_type,path", EVIDENCE_WEBHOOKS)
def test_unknown_node_returns_403(live_server, seeded, webhook_type, path):
    ts = int(time.time() * 1000)
    payload = f"{seeded['mesh_id']}|ghost-node|{webhook_type}|{ts}"
    sig = _sign(seeded["priv"], payload)
    status, body = _post(
        f"{live_server}{path}",
        {
            "text": "fake",
            "mesh_id": seeded["mesh_id"],
            "node_id": "ghost-node",
            "ts": ts,
            "signature": sig,
        },
    )
    assert status == 403, f"{webhook_type}: {status} {body}"
    assert body["error"] == "node not registered in mesh"


# ── Unknown mesh → 404 mesh not found ──


@pytest.mark.parametrize("webhook_type,path", EVIDENCE_WEBHOOKS)
def test_unknown_mesh_returns_404(live_server, seeded, webhook_type, path):
    ts = int(time.time() * 1000)
    payload = f"ghost-mesh|{seeded['node_id']}|{webhook_type}|{ts}"
    sig = _sign(seeded["priv"], payload)
    status, body = _post(
        f"{live_server}{path}",
        {
            "text": "fake",
            "mesh_id": "ghost-mesh",
            "node_id": seeded["node_id"],
            "ts": ts,
            "signature": sig,
        },
    )
    assert status == 404, f"{webhook_type}: {status} {body}"
    assert body["error"] == "mesh not found"


# ── Valid sig → 200 + send_evidence fires ──
# (exception: /webhook/evidence-photo doesn't go through send_evidence
# when photo+PARTNER_EMAIL are present — it builds its own multipart
# email. We just assert 200 in that case rather than a captured call.)


@pytest.mark.parametrize("webhook_type,path", EVIDENCE_WEBHOOKS)
def test_valid_signature_accepts(live_server, seeded, mail_module, monkeypatch, webhook_type, path):
    captured = []
    monkeypatch.setattr(mail_module, "send_evidence", lambda body, kind: captured.append((body, kind)))
    ts = int(time.time() * 1000)
    payload = f"{seeded['mesh_id']}|{seeded['node_id']}|{webhook_type}|{ts}"
    sig = _sign(seeded["priv"], payload)
    body_payload = {
        "mesh_id": seeded["mesh_id"],
        "node_id": seeded["node_id"],
        "ts": ts,
        "signature": sig,
    }
    # Per-endpoint inner content. send_evidence always runs except for
    # evidence-photo when both photo + PARTNER_EMAIL are set, in which
    # case the handler builds its own multipart email.
    if webhook_type == "compliment":
        body_payload["text"] = "good boy"
    elif webhook_type == "gratitude":
        body_payload["entries"] = ["one", "two"]
    elif webhook_type == "love_letter":
        body_payload["text"] = "dear lion"
    elif webhook_type == "offer":
        body_payload["offer"] = "30 min"
    elif webhook_type == "geofence-breach":
        body_payload.update({"lat": 40.0, "lon": -74.0, "distance": 250})
    elif webhook_type == "evidence-photo":
        body_payload.update({"photo": "", "type": "obedience", "text": "task done"})
    elif webhook_type == "subscription-charge":
        body_payload.update({"tier": "gold", "amount": 50})

    status, response = _post(f"{live_server}{path}", body_payload)
    assert status == 200, f"{webhook_type}: {status} {response}"
    if webhook_type != "evidence-photo":
        # send_evidence should have fired exactly once
        assert len(captured) == 1, f"{webhook_type} did not fire send_evidence"


# ── Round-trip safety: bunny-message regression test still passes ──
# (The refactor in this commit moved bunny-message onto the shared
# _verify_slave_signed_webhook helper. The original suite at
# tests/test_pairing.py::TestBunnyMessageSigned still owns that
# regression; this one-liner just confirms the helper carries the
# companion version field correctly.)


def test_bunny_message_still_uses_min_companion_version(live_server):
    status, body = _post(f"{live_server}/webhook/bunny-message", {"text": "x"})
    assert status == 403
    assert body["error"] == "signature required"
    assert body["min_companion_version"] == 53
    assert "min_collar_version" not in body

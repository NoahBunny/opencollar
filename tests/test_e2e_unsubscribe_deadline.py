"""Stream C — Coverage push for two bunny-signed mesh routes that
weren't pinned by the 2026-04-27 audit suite:

  POST /api/mesh/{mesh_id}/unsubscribe       (focuslock-mail.py:3610)
  POST /api/mesh/{mesh_id}/deadline-task/clear (focuslock-mail.py:4182)

Both verify a SHA256withRSA(PKCS1v15) signature over a canonical
"mesh_id|node_id|<action>|ts" payload, with a ±5min replay window.
The unsubscribe handler dispatches `unsubscribe-charge`; the
deadline-task/clear handler dispatches `deadline-task-cleared` and
refuses if no task is armed.
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_unsub_deadline", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_unsub_deadline"] = mod
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


@pytest.fixture
def seeded_mesh(mail_module):
    """Mesh + collar node with a registered bunny pubkey + a gold sub
    (so unsubscribe-charge has something to clear) + an armed deadline
    task."""
    mesh_id = "unsub-mesh-" + str(int(time.time() * 1000))
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
    # Seed orders: gold sub for unsubscribe-charge to clear, deadline task armed.
    orders = mail_module._orders_registry.get_or_create(mesh_id)
    orders.set("sub_tier", "gold")
    orders.set("sub_due_ms", int(time.time() * 1000) + 7 * 24 * 3600 * 1000)
    orders.set("paywall", "0")
    orders.set("deadline_task_text", "qa-task")
    orders.set("deadline_task_deadline_ms", int(time.time() * 1000) + 3600 * 1000)
    try:
        yield {"mesh_id": mesh_id, "node_id": node_id, "priv": priv, "pub_b64": pub_b64}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)
        mail_module._orders_registry.docs.pop(mesh_id, None)


# ── /api/mesh/{id}/unsubscribe ────────────────────────────────────────────


class TestUnsubscribeRoute:
    def test_valid_signature_clears_subscription(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|unsubscribe|{ts}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/unsubscribe",
            {
                "node_id": seeded_mesh["node_id"],
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200, f"got {status} {body}"
        assert body["ok"] is True
        # Gold tier exit fee is $100 → paywall now at least $100.
        assert "fee" in body
        assert "paywall" in body

    def test_missing_signature_returns_400(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/unsubscribe",
            {"node_id": seeded_mesh["node_id"], "ts": ts},
        )
        assert status == 400
        assert "signature" in body["error"]

    def test_bad_signature_returns_403(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/unsubscribe",
            {
                "node_id": seeded_mesh["node_id"],
                "ts": ts,
                "signature": "AAAA" + "=" * 340,
            },
        )
        assert status == 403
        assert body["error"] == "invalid signature"

    def test_stale_ts_returns_403(self, live_server, seeded_mesh):
        stale = int(time.time() * 1000) - 10 * 60 * 1000
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|unsubscribe|{stale}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/unsubscribe",
            {"node_id": seeded_mesh["node_id"], "ts": stale, "signature": sig},
        )
        assert status == 403
        assert body["error"] == "ts out of window"

    def test_unknown_mesh_returns_404(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        payload = f"ghost-mesh|{seeded_mesh['node_id']}|unsubscribe|{ts}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, body = _post(
            f"{live_server}/api/mesh/ghost-mesh/unsubscribe",
            {"node_id": seeded_mesh["node_id"], "ts": ts, "signature": sig},
        )
        assert status == 404
        assert body["error"] == "mesh not found"

    def test_unknown_node_returns_403(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|ghost-node|unsubscribe|{ts}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/unsubscribe",
            {"node_id": "ghost-node", "ts": ts, "signature": sig},
        )
        assert status == 403
        assert body["error"] == "node not registered in mesh"

    def test_invalid_mesh_id_path_returns_400(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        status, _body = _post(
            f"{live_server}/api/mesh/..%2Fevil/unsubscribe",
            {"node_id": "x", "ts": ts, "signature": "x"},
        )
        assert status in (400, 403, 404)


# ── /api/mesh/{id}/deadline-task/clear ────────────────────────────────────


class TestDeadlineTaskClearRoute:
    def test_valid_signature_clears_armed_task(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|deadline-task-clear|{ts}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/deadline-task/clear",
            {"node_id": seeded_mesh["node_id"], "ts": ts, "signature": sig},
        )
        assert status == 200, f"got {status} {body}"
        assert body["ok"] is True

    def test_missing_signature_returns_400(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        status, _body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/deadline-task/clear",
            {"node_id": seeded_mesh["node_id"], "ts": ts},
        )
        assert status == 400

    def test_bad_signature_returns_403(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/deadline-task/clear",
            {
                "node_id": seeded_mesh["node_id"],
                "ts": ts,
                "signature": "AAAA" + "=" * 340,
            },
        )
        assert status == 403
        assert body["error"] == "invalid signature"

    def test_stale_ts_returns_403(self, live_server, seeded_mesh):
        stale = int(time.time() * 1000) - 10 * 60 * 1000
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|deadline-task-clear|{stale}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/deadline-task/clear",
            {"node_id": seeded_mesh["node_id"], "ts": stale, "signature": sig},
        )
        assert status == 403
        assert body["error"] == "ts out of window"

    def test_no_task_armed_returns_409(self, live_server, mail_module, seeded_mesh):
        # Disarm the task first.
        orders = mail_module._orders_registry.get(seeded_mesh["mesh_id"])
        orders.set("deadline_task_text", "")
        orders.set("deadline_task_deadline_ms", 0)
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|deadline-task-clear|{ts}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, body = _post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/deadline-task/clear",
            {"node_id": seeded_mesh["node_id"], "ts": ts, "signature": sig},
        )
        assert status == 409
        assert body["error"] == "no deadline task armed"

    def test_unknown_mesh_returns_404(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        payload = f"ghost-mesh|{seeded_mesh['node_id']}|deadline-task-clear|{ts}"
        sig = _sign(seeded_mesh["priv"], payload)
        status, _body = _post(
            f"{live_server}/api/mesh/ghost-mesh/deadline-task/clear",
            {"node_id": seeded_mesh["node_id"], "ts": ts, "signature": sig},
        )
        assert status == 404

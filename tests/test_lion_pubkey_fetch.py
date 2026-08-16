"""`POST /vault/{mesh_id}/lion-pubkey` — a node claiming its Lion.

A desktop collar could join a mesh and stay unclaimed forever: registration
doesn't return the Lion's public key, and only the invite-code join and the
passphrase pairing flow ever handed it over. The result was a perfectly good
mesh member with a gray crown, no standing orders, and no way to verify a
Lion-signed order until a human hand-copied a PEM onto the machine.

The endpoint hands over the account's authoritative `lion_pubkey` to a caller
that proves possession of an approved node key. Deriving it client-side from
the `controller` row instead would not do: `node_type` is self-asserted at
registration, so anything that registered as a controller could poison a
collar's trust anchor.

Signed payload: "{mesh_id}|{node_id}|lion-pubkey|{ts}"
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_lionkey", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_lionkey"] = mod
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


def _http_post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return priv, base64.b64encode(pub_der).decode()


def _sign(priv, payload):
    return base64.b64encode(priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()


@pytest.fixture
def mesh(mail_module):
    mesh_id = "lionkey-" + str(int(time.time() * 1000000))
    _, lion_pub = _keypair()
    node_priv, node_pub = _keypair()
    node_id = "desk-" + str(int(time.time() * 1000000))
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": lion_pub,
        "auth_token": "test-token",
        "invite_code": "",
        "invite_expires_at": 0,
        "invite_uses": 0,
        "pin": "0000",
        "created_at": int(time.time()),
        "nodes": {},
        "vault_only": False,
        "max_blobs_per_day": 5000,
        "max_total_bytes_mb": 100,
    }
    mail_module._vault_store.add_node(
        mesh_id,
        {
            "node_id": node_id,
            "node_type": "desktop",
            "node_pubkey": node_pub,
            "registered_at": int(time.time()),
            "auto_accepted": True,  # the normal self-registration path
        },
    )
    try:
        yield {
            "mesh_id": mesh_id,
            "node_id": node_id,
            "node_priv": node_priv,
            "node_pub": node_pub,
            "lion_pub": lion_pub,
        }
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


def _claim(live_server, mesh, node_id=None, priv=None, ts=None):
    node_id = node_id or mesh["node_id"]
    priv = priv or mesh["node_priv"]
    ts_ms = ts if ts is not None else int(time.time() * 1000)
    sig = _sign(priv, f"{mesh['mesh_id']}|{node_id}|lion-pubkey|{ts_ms}")
    return _http_post(
        f"{live_server}/vault/{mesh['mesh_id']}/lion-pubkey",
        {"node_id": node_id, "ts": ts_ms, "signature": sig},
    )


class TestLionPubkeyFetch:
    def test_approved_node_gets_the_authoritative_key(self, live_server, mesh):
        status, resp = _claim(live_server, mesh)
        assert status == 200
        assert resp["lion_pubkey"] == mesh["lion_pub"]
        assert resp["format"] == "der-b64"
        # Usable as a verification key without further massaging.
        serialization.load_der_public_key(base64.b64decode(resp["lion_pubkey"]))

    def test_unconfirmed_auto_accepted_node_is_still_served(self, live_server, mesh, mail_module):
        """Deliberate: it is a public verification key, and a node that adopts
        it only becomes more obedient — it starts enforcing Lion-signed orders
        it would otherwise ignore. Withholding it would leave devices unclaimed,
        which is the failure this endpoint exists to end."""
        row = next(
            n for n in mail_module._vault_store.get_nodes(mesh["mesh_id"])
            if n["node_id"] == mesh["node_id"]
        )
        assert row.get("auto_accepted") and not row.get("lion_confirmed")
        assert _claim(live_server, mesh)[0] == 200

    def test_unknown_node_rejected(self, live_server, mesh):
        status, resp = _claim(live_server, mesh, node_id="not-a-member")
        assert status == 403
        assert "not registered" in resp.get("error", "")

    def test_rogue_key_rejected(self, live_server, mesh):
        rogue_priv, _ = _keypair()
        status, resp = _claim(live_server, mesh, priv=rogue_priv)
        assert status == 403
        assert "signature" in resp.get("error", "")

    def test_replay_outside_the_window_rejected(self, live_server, mesh):
        status, resp = _claim(live_server, mesh, ts=int(time.time() * 1000) - 10 * 60 * 1000)
        assert status == 403
        assert "ts" in resp.get("error", "")

    def test_signature_is_bound_to_this_mesh(self, live_server, mesh, mail_module):
        """A signature lifted from another mesh's request must not work here —
        the mesh_id is inside the signed payload."""
        ts_ms = int(time.time() * 1000)
        sig = _sign(mesh["node_priv"], f"other-mesh|{mesh['node_id']}|lion-pubkey|{ts_ms}")
        status, _ = _http_post(
            f"{live_server}/vault/{mesh['mesh_id']}/lion-pubkey",
            {"node_id": mesh["node_id"], "ts": ts_ms, "signature": sig},
        )
        assert status == 403

    def test_mesh_without_a_lion_key_404s(self, live_server, mesh, mail_module):
        mail_module._mesh_accounts.meshes[mesh["mesh_id"]]["lion_pubkey"] = ""
        status, resp = _claim(live_server, mesh)
        assert status == 404
        assert "lion_pubkey" in resp.get("error", "")

    def test_missing_fields_400(self, live_server, mesh):
        status, resp = _http_post(
            f"{live_server}/vault/{mesh['mesh_id']}/lion-pubkey", {"node_id": mesh["node_id"]}
        )
        assert status == 400
        assert "signature" in resp.get("error", "")

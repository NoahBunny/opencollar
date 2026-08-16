"""HTTP-level QA for the node-signed standing-orders fetch.

Background: GET /standing-orders is admin-gated (audit 2026-04-27 H-1), so the
only way a desktop collar could read the Lion's orders was to hold ADMIN_TOKEN —
a credential for the entire admin API — on the machine the collar exists to
constrain. POST /vault/{mesh_id}/standing-orders asks for the same text and
proves only membership: a registered node signs with its own key, exactly as it
does for lion-pubkey.

What these tests pin, beyond the happy path:
  * the stub is served, never /enforcement-orders (token + penalty amounts);
  * ADMIN_TOKEN is redacted out of whatever is served;
  * an unconfirmed auto-accepted node IS served (deliberate — orders only make
    a machine more governed), but a stranger, a rogue key, a stale timestamp and
    a signature lifted from another mesh are all refused;
  * the admin gate on GET /standing-orders is untouched.

Signed payload layout:  "{mesh_id}|{node_id}|standing-orders|{ts}"
"""

import base64
import hashlib
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_so_fetch", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_so_fetch"] = mod
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


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(pub_der).decode()


def _post(url, body):
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


ORDERS_TEXT = (
    "# The Collar — standing orders\n\n"
    "This machine belongs to an adult in a consensual power-exchange dynamic.\n\n"
    "## Safewords\n\n"
    '- **"Yellow card"** — pause the dynamic.\n'
    '- **"Red card"** — full stop.\n'
)


@pytest.fixture
def orders_on_disk(mail_module, tmp_path, monkeypatch):
    """Point _read_standing_orders at a temp stub instead of the operator's own
    ~/.claude, so the suite never depends on (or reads) the real one."""
    stub = tmp_path / "CLAUDE-stub.md"
    stub.write_text(ORDERS_TEXT, encoding="utf-8")
    real_expand = mail_module.os.path.expanduser

    def fake_expand(path):
        if path.endswith("~/.claude/CLAUDE-stub.md") or path == "~/.claude/CLAUDE-stub.md":
            return str(stub)
        if path == "~/.claude/CLAUDE.md":
            return str(tmp_path / "CLAUDE.md")
        return real_expand(path)

    monkeypatch.setattr(mail_module.os.path, "expanduser", fake_expand)
    return stub


@pytest.fixture
def node_mesh(mail_module):
    """A mesh with one registered vault node, holding its private key."""
    mesh_id = "son-" + str(int(time.time() * 1000000))
    node_id = "desk-1"
    node_priv, node_pub = _keypair()
    lion_priv, lion_pub = _keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": lion_pub,
        "auth_token": "test-token",
        "created_at": int(time.time()),
        "nodes": {},
        "auto_accept_grandfathered_at": int(time.time()),
    }
    mail_module._vault_store.add_node(
        mesh_id,
        {
            "node_id": node_id,
            "node_type": "desktop",
            "node_pubkey": node_pub,
            "registered_at": int(time.time()),
        },
    )
    try:
        yield {
            "mesh_id": mesh_id,
            "node_id": node_id,
            "node_priv": node_priv,
            "node_pub": node_pub,
            "lion_priv": lion_priv,
        }
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


def _fetch(live_server, mesh_id, node_id, priv, ts_ms=None, payload_mesh=None):
    ts_ms = int(time.time() * 1000) if ts_ms is None else ts_ms
    payload = f"{payload_mesh or mesh_id}|{node_id}|standing-orders|{ts_ms}"
    sig = base64.b64encode(
        priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    ).decode()
    return _post(
        f"{live_server}/vault/{mesh_id}/standing-orders",
        {"node_id": node_id, "ts": ts_ms, "signature": sig},
    )


class TestNodeSignedFetch:
    def test_registered_node_gets_the_orders(self, live_server, node_mesh, orders_on_disk):
        status, body = _fetch(live_server, node_mesh["mesh_id"], node_mesh["node_id"], node_mesh["node_priv"])
        assert status == 200, body
        assert body["content"] == ORDERS_TEXT
        assert body["sha256"] == hashlib.sha256(ORDERS_TEXT.encode()).hexdigest()

    def test_the_safeword_clause_survives_the_trip(self, live_server, node_mesh, orders_on_disk):
        """The whole point of restoring this pipe is that what lands on the
        collared machine still contains the way out."""
        _, body = _fetch(live_server, node_mesh["mesh_id"], node_mesh["node_id"], node_mesh["node_priv"])
        assert "Yellow card" in body["content"]
        assert "Red card" in body["content"]

    def test_admin_token_is_redacted_from_what_is_served(
        self, live_server, node_mesh, orders_on_disk, mail_module, monkeypatch
    ):
        monkeypatch.setattr(mail_module, "ADMIN_TOKEN", "s3cret-admin-token")
        orders_on_disk.write_text(ORDERS_TEXT + "\ntoken: s3cret-admin-token\n", encoding="utf-8")
        _, body = _fetch(live_server, node_mesh["mesh_id"], node_mesh["node_id"], node_mesh["node_priv"])
        assert "s3cret-admin-token" not in body["content"]
        assert "<REDACTED>" in body["content"]

    def test_unconfirmed_auto_accepted_node_is_still_served(
        self, live_server, node_mesh, orders_on_disk, mail_module
    ):
        """Deliberate, same reasoning as lion-pubkey: a node that starts obeying
        the Lion's orders becomes more governed, not less. Withholding them
        would leave a machine on the mesh with nothing telling it what it is."""
        mesh_id, node_id = node_mesh["mesh_id"], "auto-node"
        priv, pub = _keypair()
        mail_module._vault_store.add_node(
            mesh_id,
            {
                "node_id": node_id,
                "node_type": "desktop",
                "node_pubkey": pub,
                "registered_at": int(time.time()),
                "auto_accepted": True,
            },
        )
        assert mail_module._node_awaiting_confirmation(
            next(n for n in mail_module._vault_store.get_nodes(mesh_id) if n["node_id"] == node_id)
        )
        status, body = _fetch(live_server, mesh_id, node_id, priv)
        assert status == 200, body
        assert body["content"] == ORDERS_TEXT


class TestNodeSignedFetchRefusals:
    def test_unknown_node_is_refused(self, live_server, node_mesh, orders_on_disk):
        priv, _ = _keypair()
        status, body = _fetch(live_server, node_mesh["mesh_id"], "not-a-member", priv)
        assert status == 403
        assert "not registered" in body["error"]

    def test_rogue_key_for_a_real_node_is_refused(self, live_server, node_mesh, orders_on_disk):
        rogue, _ = _keypair()
        status, body = _fetch(live_server, node_mesh["mesh_id"], node_mesh["node_id"], rogue)
        assert status == 403
        assert body["error"] == "invalid signature"

    def test_the_lions_own_key_is_not_a_node_key(self, live_server, node_mesh, orders_on_disk):
        """The Lion signs with a key registered on the account, not on a vault
        node row — this route accepts node keys only, so it must refuse."""
        status, _ = _fetch(live_server, node_mesh["mesh_id"], node_mesh["node_id"], node_mesh["lion_priv"])
        assert status == 403

    def test_stale_timestamp_is_refused(self, live_server, node_mesh, orders_on_disk):
        old = int(time.time() * 1000) - 10 * 60 * 1000
        status, body = _fetch(
            live_server, node_mesh["mesh_id"], node_mesh["node_id"], node_mesh["node_priv"], ts_ms=old
        )
        assert status == 403
        assert body["error"] == "ts out of window"

    def test_signature_bound_to_another_mesh_is_refused(self, live_server, node_mesh, orders_on_disk):
        """A valid signature from this node on some other mesh's payload must
        not unlock this mesh's orders."""
        status, _ = _fetch(
            live_server,
            node_mesh["mesh_id"],
            node_mesh["node_id"],
            node_mesh["node_priv"],
            payload_mesh="some-other-mesh",
        )
        assert status == 403

    def test_missing_fields_are_a_400(self, live_server, node_mesh, orders_on_disk):
        status, _ = _post(
            f"{live_server}/vault/{node_mesh['mesh_id']}/standing-orders", {"node_id": node_mesh["node_id"]}
        )
        assert status == 400

    def test_no_orders_on_file_is_a_404(self, live_server, node_mesh, tmp_path, mail_module, monkeypatch):
        monkeypatch.setattr(
            mail_module.os.path, "expanduser", lambda p: str(tmp_path / "nothing-here.md")
        )
        status, _ = _fetch(live_server, node_mesh["mesh_id"], node_mesh["node_id"], node_mesh["node_priv"])
        assert status == 404


class TestAdminGateUnchanged:
    def test_get_standing_orders_still_refuses_an_anonymous_caller(
        self, live_server, node_mesh, orders_on_disk, mail_module, monkeypatch
    ):
        monkeypatch.setattr(mail_module, "ADMIN_TOKEN", "s3cret-admin-token")
        req = urllib.request.Request(f"{live_server}/standing-orders", method="GET")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                pytest.fail(f"anonymous GET should not have succeeded: {r.status}")
        except urllib.error.HTTPError as e:
            assert e.code == 403

    def test_enforcement_orders_has_no_node_signed_route(self, live_server, node_mesh, orders_on_disk):
        """The tactical orders — penalty amounts, the admin token itself — are
        not reachable by proving membership. Only the stub is."""
        ts_ms = int(time.time() * 1000)
        payload = f"{node_mesh['mesh_id']}|{node_mesh['node_id']}|enforcement-orders|{ts_ms}"
        sig = base64.b64encode(
            node_mesh["node_priv"].sign(payload.encode(), padding.PKCS1v15(), hashes.SHA256())
        ).decode()
        status, body = _post(
            f"{live_server}/vault/{node_mesh['mesh_id']}/enforcement-orders",
            {"node_id": node_mesh["node_id"], "ts": ts_ms, "signature": sig},
        )
        assert status in (400, 404)  # no such vault action
        assert "content" not in body

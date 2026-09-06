"""`GET /vault/{mesh_id}/nodes` — the bunny's E2EE key reaching the Lion.

Lion's Share reads the bunny's messaging pubkey out of this response and, until
it has one, the Inbox sits behind a "not encrypted" banner. It looks in two
places: the per-node `bunny_pubkey` on the row matching the slot's node_id, and
— for the ordinary one-bunny pairing, where a slot has no node_id — the
mesh-level `bunny_pubkey`.

The mesh-level field was built only from the mesh *account* store, which is
populated by `/api/mesh/join` (Bunny Tasker, invite code). A Collar that
enrolled the modern way, through `register-node-request`, carries its
bunny_pubkey on the *vault node row* and never calls join — so the account had
no node to harvest, the mesh-level field was absent, and the Lion's app found
nothing. The key was on the relay the whole time, one dict away from the
response, and the two apps never exchanged messaging keys.
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
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_bunnykey", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_bunnykey"] = mod
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


def _pubkey_b64():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    der = priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(der).decode()


def _get(url, token=None):
    req = urllib.request.Request(url, method="GET")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


@pytest.fixture
def mesh(mail_module):
    """A mesh whose only bunny enrolled through register-node-request: the key
    lives on the vault node row, and the mesh account has no nodes at all."""
    mesh_id = "bunnykey-" + str(int(time.time() * 1000000))
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": _pubkey_b64(),
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
    bunny_pub = _pubkey_b64()
    mail_module._vault_store.add_node(
        mesh_id,
        {
            "node_id": "collar-phone",
            "node_type": "phone",
            "node_pubkey": _pubkey_b64(),
            "bunny_pubkey": bunny_pub,
            "registered_at": int(time.time()),
            "auto_accepted": True,
        },
    )
    try:
        yield {"mesh_id": mesh_id, "bunny_pubkey": bunny_pub, "token": "test-token"}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


class TestMeshLevelBunnyPubkey:
    def test_vault_registered_collar_key_reaches_the_lion(self, live_server, mesh):
        """The regression: this used to come back with no mesh-level key, and
        the Lion's Inbox stayed on 'not encrypted' forever."""
        status, resp = _get(f"{live_server}/vault/{mesh['mesh_id']}/nodes", token=mesh["token"])
        assert status == 200
        assert resp.get("bunny_pubkey") == mesh["bunny_pubkey"]

    def test_key_is_also_on_the_node_row(self, live_server, mesh):
        """The per-node path a multi-bunny slot uses."""
        _, resp = _get(f"{live_server}/vault/{mesh['mesh_id']}/nodes", token=mesh["token"])
        row = next(n for n in resp["nodes"] if n["node_id"] == "collar-phone")
        assert row["bunny_pubkey"] == mesh["bunny_pubkey"]

    def test_account_join_key_still_wins_at_mesh_level(self, live_server, mesh, mail_module):
        """The vault-row lookup is a *fallback*. An invite-code join records the
        key on the account, and that remains the mesh-level answer — the new
        path must not displace the one that already worked."""
        acct_key = _pubkey_b64()
        mail_module._mesh_accounts.meshes[mesh["mesh_id"]]["nodes"]["tasker-phone"] = {
            "type": "phone",
            "joined_at": int(time.time()),
            "bunny_pubkey": acct_key,
            "display_name": "bunny",
        }
        _, resp = _get(f"{live_server}/vault/{mesh['mesh_id']}/nodes", token=mesh["token"])
        assert resp["bunny_pubkey"] == acct_key

    def test_account_key_backfills_a_bare_vault_row(self, live_server, mesh, mail_module):
        """A vault row registered before register-node-request carried E2EE keys
        has none of its own; the account's key for that same node_id fills it in
        so the per-node path works for invite-code bunnies too."""
        acct_key = _pubkey_b64()
        mail_module._vault_store.add_node(
            mesh["mesh_id"],
            {
                "node_id": "legacy-node",
                "node_type": "phone",
                "node_pubkey": _pubkey_b64(),
                "registered_at": int(time.time()),
            },
        )
        mail_module._mesh_accounts.meshes[mesh["mesh_id"]]["nodes"]["legacy-node"] = {
            "type": "phone",
            "joined_at": int(time.time()),
            "bunny_pubkey": acct_key,
        }
        _, resp = _get(f"{live_server}/vault/{mesh['mesh_id']}/nodes", token=mesh["token"])
        row = next(n for n in resp["nodes"] if n["node_id"] == "legacy-node")
        assert row["bunny_pubkey"] == acct_key

    def test_account_key_never_overwrites_the_approved_vault_row(self, live_server, mesh, mail_module):
        """The vault row is what the Lion approved; the account row is what an
        invite-code holder self-asserted. A mismatch must not silently swap the
        key the Lion vouched for."""
        impostor = _pubkey_b64()
        mail_module._mesh_accounts.meshes[mesh["mesh_id"]]["nodes"]["collar-phone"] = {
            "type": "phone",
            "joined_at": int(time.time()),
            "bunny_pubkey": impostor,
        }
        _, resp = _get(f"{live_server}/vault/{mesh['mesh_id']}/nodes", token=mesh["token"])
        row = next(n for n in resp["nodes"] if n["node_id"] == "collar-phone")
        assert row["bunny_pubkey"] == mesh["bunny_pubkey"]
        assert row["bunny_pubkey"] != impostor

    def test_mesh_level_key_stays_behind_the_auth_gate(self, live_server, mesh):
        """Enrichment is Lion-authenticated; an unauthenticated caller gets the
        base node list and no mesh-level convenience field."""
        status, resp = _get(f"{live_server}/vault/{mesh['mesh_id']}/nodes")
        assert status == 200
        assert "bunny_pubkey" not in resp

    def test_no_key_anywhere_yields_no_field(self, live_server, mail_module):
        mesh_id = "bunnykey-empty-" + str(int(time.time() * 1000000))
        mail_module._mesh_accounts.meshes[mesh_id] = {
            "mesh_id": mesh_id,
            "lion_pubkey": _pubkey_b64(),
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
                "node_id": "vault-only-desktop",
                "node_type": "desktop",
                "node_pubkey": _pubkey_b64(),
                "registered_at": int(time.time()),
            },
        )
        try:
            _, resp = _get(f"{live_server}/vault/{mesh_id}/nodes", token="test-token")
            assert "bunny_pubkey" not in resp
        finally:
            mail_module._mesh_accounts.meshes.pop(mesh_id, None)

    def test_most_recently_registered_row_wins(self, live_server, mesh, mail_module):
        newer = _pubkey_b64()
        mail_module._vault_store.add_node(
            mesh["mesh_id"],
            {
                "node_id": "collar-tablet",
                "node_type": "phone",
                "node_pubkey": _pubkey_b64(),
                "bunny_pubkey": newer,
                "registered_at": int(time.time()) + 60,
            },
        )
        _, resp = _get(f"{live_server}/vault/{mesh['mesh_id']}/nodes", token=mesh["token"])
        assert resp["bunny_pubkey"] == newer

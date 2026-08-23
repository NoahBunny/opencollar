"""The wake-up topic must not be computable from the mesh id.

Found during a privacy sweep of the public repo on 2026-08-23. The topic was
`focuslock-{mesh_id}`, and ntfy topics are world-readable and world-writable
with no registration. So the mesh id *was* the wake-up channel: every place a
mesh id had ever been written down — a CHANGELOG line, a handoff doc, a support
thread, a screenshot — published a feed of that mesh's lock and unlock timing to
anyone who read it, and a way to inject spurious wakes.

`focuslock-DNfs4xCZM-HY` was in the repo's own changelog, spelled out as a full
ntfy.sh URL, and had been public for months.

The payload is only `{"v": N}`, so no content leaks — that part of the
zero-knowledge design held. What leaked was timing, which for this system is
when a person got locked and for how long.

Rotating the mesh id would have fixed it by re-pairing every node on the mesh.
A stored random topic fixes it without touching mesh identity, and can be
rotated again the moment one leaks. Nodes learn it over a node-signed route,
because the collar — the node that most needs it — deliberately holds no
Lion auth token.

Signed payload layout:  "{mesh_id}|{node_id}|ntfy-topic|{ts}"
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_ntfy_topic", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_ntfy_topic"] = mod
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


def _sign(priv, payload):
    return base64.b64encode(priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()


def _post(url, body):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


@pytest.fixture
def mesh(mail_module, monkeypatch, tmp_path):
    store = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "accounts"))
    _, lion_pub = _keypair()
    acct = store.create(lion_pub, pin="4242")
    mesh_id = acct["mesh_id"]
    node_id = "collar-1"
    node_priv, node_pub = _keypair()
    mail_module._vault_store.add_node(
        mesh_id,
        {
            "node_id": node_id,
            "node_type": "phone",
            "node_pubkey": node_pub,
            "registered_at": int(time.time()),
        },
    )
    monkeypatch.setattr(mail_module, "_mesh_accounts", store)
    monkeypatch.setattr(mail_module, "OPERATOR_MESH_ID", "")
    monkeypatch.setattr(mail_module, "_ntfy_topic", "")
    return {"mesh_id": mesh_id, "node_id": node_id, "priv": node_priv, "store": store}


def _fetch(live_server, mesh, *, priv=None, node_id=None, ts_ms=None, signed_as=None, omit=()):
    ts = int(time.time() * 1000) if ts_ms is None else ts_ms
    nid = mesh["node_id"] if node_id is None else node_id
    payload = signed_as if signed_as is not None else f"{mesh['mesh_id']}|{nid}|ntfy-topic|{ts}"
    body = {"node_id": nid, "ts": ts, "signature": _sign(priv or mesh["priv"], payload)}
    for k in omit:
        body.pop(k, None)
    return _post(f"{live_server}/vault/{mesh['mesh_id']}/ntfy-topic", body)


# ── resolution ────────────────────────────────────────────────────────


def test_derives_when_nothing_stored(mail_module, mesh):
    """Back-compat: a mesh that has never rotated keeps the old topic, so
    upgrading the relay does not silently stop waking existing nodes."""
    assert mail_module._get_ntfy_topic(mesh["mesh_id"]) == f"focuslock-{mesh['mesh_id']}"


def test_stored_beats_derived(mail_module, mesh):
    new = mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    assert mail_module._get_ntfy_topic(mesh["mesh_id"]) == new


def test_rotated_topic_is_not_derivable_from_the_mesh_id(mail_module, mesh):
    """The whole point. Knowing the mesh id must no longer yield the channel."""
    new = mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    assert mesh["mesh_id"] not in new
    assert new != f"focuslock-{mesh['mesh_id']}"
    assert len(new) > len("focuslock-") + 16


def test_rotation_invalidates_the_previous_topic(mesh):
    first = mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    second = mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    assert first != second, "a leaked topic is only dead once rotation actually changes it"


def test_rotation_persists_across_a_reload(mail_module, mesh, tmp_path):
    new = mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    reloaded = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "accounts"))
    assert reloaded.get_ntfy_topic(mesh["mesh_id"]) == new


def test_rotating_an_unknown_mesh_is_not_an_error(mesh):
    assert mesh["store"].rotate_ntfy_topic("no-such-mesh") == ""


def test_operator_config_still_wins(mail_module, mesh, monkeypatch):
    """The operator's explicitly-configured topic is a continuity guarantee and
    outranks rotation — otherwise upgrading would move the operator's own feed
    out from under every device already subscribed to it."""
    mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    monkeypatch.setattr(mail_module, "OPERATOR_MESH_ID", mesh["mesh_id"])
    monkeypatch.setattr(mail_module, "_ntfy_topic", "legacy-operator-topic")
    assert mail_module._get_ntfy_topic(mesh["mesh_id"]) == "legacy-operator-topic"


# ── the node-signed route ─────────────────────────────────────────────


def test_registered_node_learns_the_rotated_topic(live_server, mesh):
    new = mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    status, body = _fetch(live_server, mesh)
    assert status == 200
    assert body["topic"] == new


def test_route_serves_the_derived_topic_before_any_rotation(live_server, mesh):
    status, body = _fetch(live_server, mesh)
    assert status == 200
    assert body["topic"] == f"focuslock-{mesh['mesh_id']}"


def test_unregistered_node_is_refused(live_server, mesh):
    stranger_priv, _ = _keypair()
    status, body = _fetch(live_server, mesh, priv=stranger_priv, node_id="not-on-this-mesh")
    assert status == 403
    assert "not approved" in body["error"]


def test_a_stranger_holding_the_mesh_id_cannot_read_the_topic(live_server, mesh):
    """The mesh id is in a public changelog. It must not be enough."""
    mesh["store"].rotate_ntfy_topic(mesh["mesh_id"])
    stranger_priv, _ = _keypair()
    status, _ = _fetch(live_server, mesh, priv=stranger_priv)
    assert status == 403, "a valid signature from the wrong key must not open this"


def test_signature_must_cover_the_claim(live_server, mesh):
    """Signed over a different payload than the one sent — the signature has to
    authenticate the request, not merely accompany it."""
    status, _ = _fetch(live_server, mesh, signed_as="something-else-entirely")
    assert status == 403


def test_stale_timestamp_is_refused(live_server, mesh):
    old = int(time.time() * 1000) - 10 * 60 * 1000
    status, body = _fetch(live_server, mesh, ts_ms=old)
    assert status == 403
    assert "ts out of window" in body["error"]


def test_missing_fields_are_rejected(live_server, mesh):
    for missing in ("node_id", "signature"):
        status, _ = _fetch(live_server, mesh, omit=(missing,))
        assert status == 400, f"omitting {missing} should be a 400"

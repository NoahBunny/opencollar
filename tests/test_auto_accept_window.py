"""HTTP-level QA for the auto-accept onboarding window and the Lion-confirmation
gate on state-mirror.

Background: auto-accept used to be a sticky boolean, default ON for every new
mesh. Anything that learned a mesh_id became an approved vault node — a
recipient of every future Lion blob — and, once registration started carrying
bunny_pubkey, could also sign `/api/mesh/{id}/state-mirror` and write paywall /
sub_due / lock_active into the registry the server-side scanners bill from. Two
changes close that without putting an approval tap in front of normal
onboarding:

- the toggle opens a *time-boxed* window (`auto_accept_until`) that expires on
  the relay's clock, and accounts with no window recorded fail closed;
- a node that walked in through that window is a read-only member until the
  Lion vouches for it via `/vault/{id}/confirm-node`; state-mirror refuses it
  until then.

Signed payload layouts:
    auto-accept:  "{mesh_id}|auto-accept|{state}|{ts}"
    state-mirror: "{mesh_id}|{node_id}|state-mirror|{ts}|{state_sha256_hex}"
    confirm-node: canonical_json(body minus "signature")
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_auto_accept", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_auto_accept"] = mod
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


def _http_get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {}, method="GET")
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
    return _sign_bytes(priv, payload.encode("utf-8"))


def _sign_bytes(priv, data):
    return base64.b64encode(priv.sign(data, padding.PKCS1v15(), hashes.SHA256())).decode()


def _state_payload(mesh_id, node_id, state, ts):
    canonical = json.dumps(state, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return f"{mesh_id}|{node_id}|state-mirror|{ts}|{hashlib.sha256(canonical).hexdigest()}"


@pytest.fixture
def seeded_mesh(mail_module):
    """Mesh with an invite-joined bunny (account-level node) and no auto-accept
    window — the post-deploy resting state."""
    mesh_id = "aaw-" + str(int(time.time() * 1000000))
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


def _toggle(live_server, seeded, state):
    ts_ms = int(time.time() * 1000)
    payload = f"{seeded['mesh_id']}|auto-accept|{state}|{ts_ms}"
    return _http_post(
        f"{live_server}/api/mesh/{seeded['mesh_id']}/auto-accept",
        {"state": state, "ts": ts_ms, "signature": _sign(seeded["lion_priv"], payload)},
    )


def _register(live_server, mesh_id, node_id, node_pubkey, node_type="desktop"):
    return _http_post(
        f"{live_server}/vault/{mesh_id}/register-node-request",
        {"node_id": node_id, "node_type": node_type, "node_pubkey": node_pubkey},
    )


def _confirm(live_server, mesh_id, node_id, signer_priv, mail_module):
    body = {"node_id": node_id, "ts": int(time.time() * 1000)}
    body["signature"] = _sign_bytes(signer_priv, mail_module.mesh.canonical_json(body))
    return _http_post(f"{live_server}/vault/{mesh_id}/confirm-node", body)


# ── The window itself ──


class TestAutoAcceptWindow:
    def test_toggle_on_returns_a_deadline_not_a_sticky_flag(self, live_server, seeded_mesh, mail_module):
        status, resp = _toggle(live_server, seeded_mesh, "on")
        assert status == 200
        assert resp["auto_accept_nodes"] is True
        assert resp["expires_in_s"] == mail_module._mesh_accounts.AUTO_ACCEPT_WINDOW_S
        acct = mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]
        assert acct["auto_accept_until"] > int(time.time())
        assert mail_module._auto_accept_active(acct) is True

    def test_open_window_auto_accepts(self, live_server, seeded_mesh, mail_module):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        _, pub = _keypair()
        node_id = "desk-open-" + str(int(time.time() * 1000000))
        status, resp = _register(live_server, seeded_mesh["mesh_id"], node_id, pub)
        assert status == 200
        assert resp["status"] == "approved"
        assert resp["auto_accepted"] is True

    def test_expired_window_queues_for_approval(self, live_server, seeded_mesh, mail_module):
        acct = mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]
        acct["auto_accept_nodes"] = True
        acct["auto_accept_until"] = int(time.time()) - 1
        _, pub = _keypair()
        node_id = "desk-expired-" + str(int(time.time() * 1000000))
        status, resp = _register(live_server, seeded_mesh["mesh_id"], node_id, pub)
        assert status == 200
        assert resp["status"] == "pending"
        approved = mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
        assert not any(n["node_id"] == node_id for n in approved)

    def test_legacy_sticky_flag_fails_closed(self, live_server, seeded_mesh, mail_module):
        """An account persisted before the window existed carries
        auto_accept_nodes=True and no deadline. That used to mean "open
        forever"; it must now mean "closed until the Lion re-opens it"."""
        acct = mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]
        acct["auto_accept_nodes"] = True
        acct.pop("auto_accept_until", None)
        assert mail_module._auto_accept_active(acct) is False
        _, pub = _keypair()
        node_id = "desk-legacy-" + str(int(time.time() * 1000000))
        status, resp = _register(live_server, seeded_mesh["mesh_id"], node_id, pub)
        assert status == 200
        assert resp["status"] == "pending"

    def test_toggle_off_closes_immediately(self, live_server, seeded_mesh, mail_module):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        status, resp = _toggle(live_server, seeded_mesh, "off")
        assert status == 200
        assert resp["auto_accept_nodes"] is False
        assert resp["auto_accept_until"] == 0
        _, pub = _keypair()
        node_id = "desk-closed-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], node_id, pub)[1]["status"] == "pending"

    def test_new_mesh_opens_a_window_that_expires(self, mail_module):
        """Signup keeps its frictionless first-device onboarding — but as a
        window, not a permanent door."""
        _, lion_pub = _keypair()
        acct = mail_module._mesh_accounts.create(lion_pub, pin="1234")
        try:
            assert acct["auto_accept_nodes"] is True
            assert mail_module._auto_accept_active(acct) is True
            window = mail_module._mesh_accounts.AUTO_ACCEPT_WINDOW_S
            assert acct["auto_accept_until"] <= int(time.time()) + window
            # Same account, clock advanced past the deadline → shut.
            acct["auto_accept_until"] = int(time.time()) - 1
            assert mail_module._auto_accept_active(acct) is False
        finally:
            mail_module._mesh_accounts.meshes.pop(acct["mesh_id"], None)

    def test_key_rotation_still_needs_approval_inside_an_open_window(
        self, live_server, seeded_mesh, mail_module
    ):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        _, pub1 = _keypair()
        node_id = "desk-rotate-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], node_id, pub1)[1]["status"] == "approved"
        _, pub2 = _keypair()
        status, resp = _register(live_server, seeded_mesh["mesh_id"], node_id, pub2)
        assert status == 200
        assert resp["status"] == "pending"
        assert "rotation" in resp.get("reason", "")


# ── /vault/{id}/nodes exposure ──


class TestNodesGetExposure:
    def _seed_auto_accepted(self, live_server, seeded_mesh):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        _, pub = _keypair()
        node_id = "desk-exposure-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], node_id, pub)[1]["status"] == "approved"
        return node_id

    def test_authed_get_reports_live_window_state(self, live_server, seeded_mesh, mail_module):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        url = f"{live_server}/vault/{seeded_mesh['mesh_id']}/nodes"
        status, resp = _http_get(url, {"Authorization": "Bearer test-token"})
        assert status == 200
        assert resp["auto_accept"] is True
        assert resp["auto_accept_until"] > int(time.time())
        # Expire it: the flag is still True but the toggle must read "off",
        # because that is what register-node-request enforces.
        mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_until"] = int(time.time()) - 1
        status, resp = _http_get(url, {"Authorization": "Bearer test-token"})
        assert resp["auto_accept"] is False

    def test_trust_fields_are_lion_only(self, live_server, seeded_mesh):
        node_id = self._seed_auto_accepted(live_server, seeded_mesh)
        url = f"{live_server}/vault/{seeded_mesh['mesh_id']}/nodes"

        _, anon = _http_get(url)
        row = next(n for n in anon["nodes"] if n["node_id"] == node_id)
        assert "auto_accepted" not in row
        assert "lion_confirmed" not in row
        assert "auto_accept" not in anon
        # Bootstrap data an unauthenticated collar still needs.
        assert row["node_pubkey"]

        _, authed = _http_get(url, {"Authorization": "Bearer test-token"})
        row = next(n for n in authed["nodes"] if n["node_id"] == node_id)
        assert row["auto_accepted"] is True


# ── /vault/{id}/confirm-node ──


class TestConfirmNode:
    def _seed(self, live_server, seeded_mesh):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        priv, pub = _keypair()
        node_id = "desk-confirm-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], node_id, pub)[1]["status"] == "approved"
        return node_id, priv

    def test_lion_signature_marks_the_node(self, live_server, seeded_mesh, mail_module):
        node_id, _ = self._seed(live_server, seeded_mesh)
        status, resp = _confirm(
            live_server, seeded_mesh["mesh_id"], node_id, seeded_mesh["lion_priv"], mail_module
        )
        assert status == 200
        assert resp["lion_confirmed"] is True
        row = next(
            n for n in mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
            if n["node_id"] == node_id
        )
        assert row["lion_confirmed"] is True
        assert row["confirmed_at"] > 0

    def test_rogue_signature_rejected(self, live_server, seeded_mesh, mail_module):
        node_id, _ = self._seed(live_server, seeded_mesh)
        rogue_priv, _ = _keypair()
        status, resp = _confirm(live_server, seeded_mesh["mesh_id"], node_id, rogue_priv, mail_module)
        assert status == 403
        assert "signature" in resp.get("error", "")
        row = next(
            n for n in mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
            if n["node_id"] == node_id
        )
        assert not row.get("lion_confirmed")

    def test_unknown_node_returns_404(self, live_server, seeded_mesh, mail_module):
        status, resp = _confirm(
            live_server, seeded_mesh["mesh_id"], "no-such-node", seeded_mesh["lion_priv"], mail_module
        )
        assert status == 404
        assert "node" in resp.get("error", "")


# ── one-shot grandfather migration ──


class TestGrandfatherMigration:
    def test_sweeps_pre_existing_nodes_once_and_only_once(self, live_server, seeded_mesh, mail_module):
        """Devices enrolled under the old rules are swept in on the deploy that
        introduced the gate. The per-mesh marker is what stops a later restart
        from silently confirming whatever auto-accepted in the meantime."""
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        priv, pub = _keypair()
        old_node = "desk-old-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], old_node, pub)[1]["status"] == "approved"

        mail_module._grandfather_auto_accepted_nodes()
        row = next(
            n for n in mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
            if n["node_id"] == old_node
        )
        assert row["lion_confirmed"] is True
        assert row["confirmed_by"] == "grandfathered"
        assert mail_module._mesh_accounts.meshes[seeded_mesh["mesh_id"]]["auto_accept_grandfathered_at"] > 0
        # The grandfathered node can write state, like any confirmed device.
        ts_ms = int(time.time() * 1000)
        sig = _sign(priv, _state_payload(seeded_mesh["mesh_id"], old_node, {"paywall": "30"}, ts_ms))
        status, _ = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/state-mirror",
            {"node_id": old_node, "ts": ts_ms, "state": {"paywall": "30"}, "signature": sig},
        )
        assert status == 200

        # A device that shows up AFTER the sweep is not swept in by a re-run.
        _, newcomer_pub = _keypair()
        newcomer = "desk-new-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], newcomer, newcomer_pub)[1]["status"] == "approved"
        mail_module._grandfather_auto_accepted_nodes()
        row = next(
            n for n in mail_module._vault_store.get_nodes(seeded_mesh["mesh_id"])
            if n["node_id"] == newcomer
        )
        assert not row.get("lion_confirmed")


# ── state-mirror confirmation gate ──


class TestStateMirrorConfirmationGate:
    def _mirror(self, live_server, mesh_id, node_id, signer_priv, state):
        ts_ms = int(time.time() * 1000)
        sig = _sign(signer_priv, _state_payload(mesh_id, node_id, state, ts_ms))
        return _http_post(
            f"{live_server}/api/mesh/{mesh_id}/state-mirror",
            {"node_id": node_id, "ts": ts_ms, "state": state, "signature": sig},
        )

    def test_unconfirmed_auto_accepted_node_cannot_write_state(
        self, live_server, seeded_mesh, mail_module
    ):
        """The whole point: holding the mesh_id gets you membership, not the
        ability to zero the Lion's paywall."""
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        priv, pub = _keypair()
        node_id = "rogue-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], node_id, pub)[1]["status"] == "approved"

        orders = mail_module._orders_registry.get_or_create(seeded_mesh["mesh_id"])
        orders.set("paywall", "200")
        status, resp = self._mirror(
            live_server, seeded_mesh["mesh_id"], node_id, priv, {"paywall": "0"}
        )
        assert status == 403
        assert "confirmation" in resp.get("error", "")
        assert mail_module._orders_registry.get(seeded_mesh["mesh_id"]).get("paywall", "") == "200"

    def test_confirmation_unlocks_the_same_node(self, live_server, seeded_mesh, mail_module):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        priv, pub = _keypair()
        node_id = "desk-unlock-" + str(int(time.time() * 1000000))
        assert _register(live_server, seeded_mesh["mesh_id"], node_id, pub)[1]["status"] == "approved"
        assert (
            _confirm(live_server, seeded_mesh["mesh_id"], node_id, seeded_mesh["lion_priv"], mail_module)[0]
            == 200
        )
        status, resp = self._mirror(
            live_server, seeded_mesh["mesh_id"], node_id, priv, {"paywall": "75"}
        )
        assert status == 200
        assert resp["applied"] == ["paywall"]
        assert mail_module._orders_registry.get(seeded_mesh["mesh_id"]).get("paywall", "") == "75"

    def test_invite_joined_bunny_is_unaffected(self, live_server, seeded_mesh, mail_module):
        """Account-level members came in through an invite code the Lion handed
        out, so they were already vouched for — the gate must not touch them."""
        status, resp = self._mirror(
            live_server,
            seeded_mesh["mesh_id"],
            seeded_mesh["node_id"],
            seeded_mesh["bunny_priv"],
            {"paywall": "42"},
        )
        assert status == 200
        assert resp["signer"] == "bunny"

    def test_unconfirmed_node_cannot_set_payer_identity(self, live_server, seeded_mesh, mail_module):
        """Same gate on the sibling plaintext writes. `node_type` is
        self-asserted at registration, so the route's "phone node required"
        check is no barrier to a stranger holding the mesh_id — this is."""
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        priv, pub = _keypair()
        node_id = "rogue-phone-" + str(int(time.time() * 1000000))
        assert (
            _register(live_server, seeded_mesh["mesh_id"], node_id, pub, node_type="phone")[1]["status"]
            == "approved"
        )
        ts_ms = int(time.time() * 1000)
        allow = ["attacker@example.test"]
        body_hash = hashlib.sha256("\n".join(allow).encode("utf-8")).hexdigest()
        sig = _sign(priv, f"{seeded_mesh['mesh_id']}|{node_id}|set-payer-identity|{ts_ms}|{body_hash}")
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/set-payer-identity",
            {"node_id": node_id, "ts": ts_ms, "allow": allow, "signature": sig},
        )
        assert status == 403
        assert "confirmation" in resp.get("error", "")

        assert (
            _confirm(live_server, seeded_mesh["mesh_id"], node_id, seeded_mesh["lion_priv"], mail_module)[0]
            == 200
        )
        ts_ms = int(time.time() * 1000)
        sig = _sign(priv, f"{seeded_mesh['mesh_id']}|{node_id}|set-payer-identity|{ts_ms}|{body_hash}")
        status, _ = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/set-payer-identity",
            {"node_id": node_id, "ts": ts_ms, "allow": allow, "signature": sig},
        )
        assert status == 200

    def test_unconfirmed_node_cannot_rename_itself(self, live_server, seeded_mesh, mail_module):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        priv, pub = _keypair()
        node_id = "rogue-name-" + str(int(time.time() * 1000000))
        assert (
            _register(live_server, seeded_mesh["mesh_id"], node_id, pub, node_type="phone")[1]["status"]
            == "approved"
        )
        ts_ms = int(time.time() * 1000)
        name = "Lion's laptop"
        body_hash = hashlib.sha256(name.encode("utf-8")).hexdigest()
        sig = _sign(priv, f"{seeded_mesh['mesh_id']}|{node_id}|set-display-name|{ts_ms}|{body_hash}")
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/set-display-name",
            {"node_id": node_id, "ts": ts_ms, "display_name": name, "signature": sig},
        )
        assert status == 403
        assert "confirmation" in resp.get("error", "")

    def test_lion_approved_node_is_unaffected(self, live_server, seeded_mesh, mail_module):
        """Approved out of the pending queue (register-node) → never carried the
        auto_accepted stamp, so no confirmation is required."""
        priv, pub = _keypair()
        node_id = "desk-approved-" + str(int(time.time() * 1000000))
        body = {"node_id": node_id, "node_type": "desktop", "node_pubkey": pub}
        body["signature"] = _sign_bytes(
            seeded_mesh["lion_priv"], mail_module.mesh.canonical_json(body)
        )
        assert _http_post(f"{live_server}/vault/{seeded_mesh['mesh_id']}/register-node", body)[0] == 200
        status, resp = self._mirror(
            live_server, seeded_mesh["mesh_id"], node_id, priv, {"sub_due": "10"}
        )
        assert status == 200
        assert resp["applied"] == ["sub_due"]


# ── Restart re-registration ──


def _register_full(live_server, mesh_id, node_id, node_pubkey, bunny_pubkey=None, node_type="desktop"):
    body = {"node_id": node_id, "node_type": node_type, "node_pubkey": node_pubkey}
    if bunny_pubkey is not None:
        body["bunny_pubkey"] = bunny_pubkey
    return _http_post(f"{live_server}/vault/{mesh_id}/register-node-request", body)


class TestIdempotentReRegistration:
    """A collar re-posts register-node-request on every restart (the guard in
    `_vault_register_node` is in-process). With the window shut that queued an
    established member again and alerted the Lion, burying real requests. An
    exact-key repost is now answered "you are already in" — without touching the
    pending queue, because pending is keyed by node_id and node_pubkeys are
    public, so clearing here would let anyone strand a node mid-rotation."""

    def _admit(self, live_server, seeded_mesh, node_id, pub, bunny=None):
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        status, resp = _register_full(live_server, seeded_mesh["mesh_id"], node_id, pub, bunny)
        assert (status, resp.get("status")) == (200, "approved"), resp
        assert _toggle(live_server, seeded_mesh, "off")[0] == 200

    def _row(self, mail_module, mesh_id, node_id):
        for n in mail_module._vault_store.get_nodes(mesh_id):
            if n.get("node_id") == node_id:
                return n
        return None

    def test_restart_repost_is_a_noop_not_a_join_request(self, live_server, seeded_mesh, mail_module):
        mesh_id = seeded_mesh["mesh_id"]
        node_id = "desk-restart-" + str(int(time.time() * 1000000))
        _, pub = _keypair()
        self._admit(live_server, seeded_mesh, node_id, pub)
        registered_at = self._row(mail_module, mesh_id, node_id)["registered_at"]

        status, resp = _register_full(live_server, mesh_id, node_id, pub)

        assert status == 200
        assert resp["status"] == "approved"
        assert resp["already_registered"] is True
        assert [p for p in mail_module._vault_store.get_pending_nodes(mesh_id) if p["node_id"] == node_id] == []
        # The membership row is untouched — no churned join time.
        assert self._row(mail_module, mesh_id, node_id)["registered_at"] == registered_at

    def test_noop_does_not_alert_the_lion(self, live_server, seeded_mesh, mail_module, monkeypatch):
        mesh_id = seeded_mesh["mesh_id"]
        node_id = "desk-quiet-" + str(int(time.time() * 1000000))
        _, pub = _keypair()
        self._admit(live_server, seeded_mesh, node_id, pub)

        fired = []
        monkeypatch.setattr(mail_module, "_node_join_ntfy", lambda *a, **k: fired.append(a))
        assert _register_full(live_server, mesh_id, node_id, pub)[0] == 200
        assert fired == []
        # A device that really is new still rings.
        _, other = _keypair()
        _register_full(live_server, mesh_id, "desk-stranger-" + str(int(time.time() * 1000000)), other)
        assert len(fired) == 1

    def test_noop_reports_whether_the_lion_has_confirmed(self, live_server, seeded_mesh, mail_module):
        mesh_id = seeded_mesh["mesh_id"]
        node_id = "desk-conf-" + str(int(time.time() * 1000000))
        _, pub = _keypair()
        self._admit(live_server, seeded_mesh, node_id, pub)

        assert _register_full(live_server, mesh_id, node_id, pub)[1]["lion_confirmed"] is False
        assert _confirm(live_server, mesh_id, node_id, seeded_mesh["lion_priv"], mail_module)[0] == 200
        assert _register_full(live_server, mesh_id, node_id, pub)[1]["lion_confirmed"] is True

    def test_a_different_key_under_the_same_id_still_queues(self, live_server, seeded_mesh, mail_module):
        mesh_id = seeded_mesh["mesh_id"]
        node_id = "desk-rot-" + str(int(time.time() * 1000000))
        _, pub = _keypair()
        self._admit(live_server, seeded_mesh, node_id, pub)

        _, rotated = _keypair()
        status, resp = _register_full(live_server, mesh_id, node_id, rotated)

        assert (status, resp["status"]) == (200, "pending")
        assert self._row(mail_module, mesh_id, node_id)["node_pubkey"] == pub
        assert [p["node_pubkey"] for p in mail_module._vault_store.get_pending_nodes(mesh_id) if p["node_id"] == node_id] == [rotated]

    def test_a_new_bunny_pubkey_is_not_installed_without_the_lion(self, live_server, seeded_mesh, mail_module):
        mesh_id = seeded_mesh["mesh_id"]
        node_id = "desk-bk-" + str(int(time.time() * 1000000))
        _, pub = _keypair()
        _, bunny_a = _keypair()
        self._admit(live_server, seeded_mesh, node_id, pub, bunny=bunny_a)

        _, bunny_b = _keypair()
        status, resp = _register_full(live_server, mesh_id, node_id, pub, bunny_pubkey=bunny_b)

        assert (status, resp["status"]) == (200, "pending")
        assert self._row(mail_module, mesh_id, node_id)["bunny_pubkey"] == bunny_a

    def test_replaying_the_current_key_cannot_cancel_a_rotation(self, live_server, seeded_mesh, mail_module):
        """node_pubkeys are readable anonymously, so the no-op path must not be a
        way to delete another device's pending rotation and strand it on the old key."""
        mesh_id = seeded_mesh["mesh_id"]
        node_id = "desk-strand-" + str(int(time.time() * 1000000))
        _, pub = _keypair()
        self._admit(live_server, seeded_mesh, node_id, pub)
        _, rotated = _keypair()
        assert _register_full(live_server, mesh_id, node_id, rotated)[1]["status"] == "pending"

        assert _register_full(live_server, mesh_id, node_id, pub)[1]["already_registered"] is True

        pending = [p for p in mail_module._vault_store.get_pending_nodes(mesh_id) if p["node_id"] == node_id]
        assert [p["node_pubkey"] for p in pending] == [rotated]


# ── the persisted flag vs. the gate that enforces it ──


class TestExpiredWindowReconcile:
    """`_auto_accept_active()` already fails closed on an expired or missing
    deadline, so these accounts were never actually accepting anyone. What was
    wrong is that `auto_accept_nodes` stayed `true` on disk, and the account
    JSON is what an operator reads on the relay to answer "is the door open?".
    Observed on the live mesh: flag on, no deadline, door shut."""

    def test_expired_window_flag_is_reconciled_off(self, live_server, seeded_mesh, mail_module):
        mesh_id = seeded_mesh["mesh_id"]
        account = mail_module._mesh_accounts.meshes[mesh_id]
        account["auto_accept_nodes"] = True
        account["auto_accept_until"] = int(time.time()) - 60  # deadline passed

        mail_module._close_expired_auto_accept_windows()

        assert account["auto_accept_nodes"] is False
        assert account["auto_accept_until"] == 0

    def test_legacy_sticky_flag_with_no_deadline_is_reconciled_off(
        self, live_server, seeded_mesh, mail_module
    ):
        """The shape the live mesh was actually in: flag true, no deadline at
        all — an account persisted before the window existed."""
        mesh_id = seeded_mesh["mesh_id"]
        account = mail_module._mesh_accounts.meshes[mesh_id]
        account["auto_accept_nodes"] = True
        account.pop("auto_accept_until", None)

        mail_module._close_expired_auto_accept_windows()

        assert account["auto_accept_nodes"] is False
        assert account["auto_accept_until"] == 0

    def test_open_window_is_left_alone(self, live_server, seeded_mesh, mail_module):
        """Reconciling must never shorten a window the Lion deliberately opened."""
        mesh_id = seeded_mesh["mesh_id"]
        assert _toggle(live_server, seeded_mesh, "on")[0] == 200
        account = mail_module._mesh_accounts.meshes[mesh_id]
        until_before = account["auto_accept_until"]
        assert until_before > int(time.time())

        mail_module._close_expired_auto_accept_windows()

        assert account["auto_accept_nodes"] is True
        assert account["auto_accept_until"] == until_before
        assert mail_module._auto_accept_active(account) is True

    def test_reconcile_survives_a_restart_and_stays_put(
        self, live_server, seeded_mesh, mail_module
    ):
        """Idempotent, and the reconciled state is what a re-read sees — the
        point of the pass is that the file on disk stops disagreeing."""
        mesh_id = seeded_mesh["mesh_id"]
        account = mail_module._mesh_accounts.meshes[mesh_id]
        account["auto_accept_nodes"] = True
        account["auto_accept_until"] = int(time.time()) - 1

        mail_module._close_expired_auto_accept_windows()
        mail_module._close_expired_auto_accept_windows()

        assert account["auto_accept_nodes"] is False
        persisted = json.loads(
            (Path(mail_module._mesh_accounts.persist_dir) / f"{mesh_id}.json").read_text()
        )
        assert persisted["auto_accept_nodes"] is False
        assert persisted["auto_accept_until"] == 0

    def test_reconcile_does_not_reopen_the_door(self, live_server, seeded_mesh, mail_module):
        """A registration after the reconcile still queues for approval."""
        mesh_id = seeded_mesh["mesh_id"]
        account = mail_module._mesh_accounts.meshes[mesh_id]
        account["auto_accept_nodes"] = True
        account["auto_accept_until"] = int(time.time()) - 1
        mail_module._close_expired_auto_accept_windows()

        _, pub = _keypair()
        node_id = "desk-after-reconcile-" + str(int(time.time() * 1000000))
        status, body = _register(live_server, mesh_id, node_id, pub)
        assert status == 200
        assert body["status"] != "approved"

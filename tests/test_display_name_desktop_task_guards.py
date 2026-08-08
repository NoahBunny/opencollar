"""Endpoint-level regression tests for the guards added in the 2026-08-07
fix-forward (deferred follow-up: the core `join()` preservation was unit-
tested, but these three HTTP surfaces were not).

  1. `POST /api/mesh/{id}/set-display-name` — bunny-signed rename, added so
     an already-provisioned device can set a name at all. The signature
     covers the name, so a relay can't swap it.
  2. `POST /webhook/desktop-task` — refuses non-operator meshes (the phone
     would never see the task, but the miss penalty would still fire) and
     refuses to clobber a live/armed task.
  3. `GET /vault/{id}/nodes` — the base list stays anonymous for E2EE
     bootstrap; the display name (PII), mesh-level bunny pubkey, and
     auto_accept flag are Lion-authenticated only.
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_guards", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_guards"] = mod
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


def _post(url, body, headers=None):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
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
    return base64.b64encode(priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()


def _name_payload(mesh_id, node_id, ts, display_name):
    body_hash = hashlib.sha256(display_name.encode("utf-8")).hexdigest()
    return f"{mesh_id}|{node_id}|set-display-name|{ts}|{body_hash}"


@pytest.fixture
def bunny_mesh(mail_module, monkeypatch, tmp_path):
    """A mesh with one joined bunny node whose key is registered in BOTH the
    account store (join) and the vault node store (what set-display-name
    verifies against)."""
    store = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "accounts"))
    acct = store.create("lion-pub", pin="1111")
    mesh_id = acct["mesh_id"]
    node_id = "sm-s908"
    priv, pub_b64 = _keypair()
    store.join(acct["invite_code"], node_id, "phone", bunny_pubkey=pub_b64, display_name="")
    mail_module._vault_store.add_node(
        mesh_id,
        {"node_id": node_id, "node_type": "phone", "node_pubkey": pub_b64},
    )
    monkeypatch.setattr(mail_module, "_mesh_accounts", store)
    try:
        yield {
            "mesh_id": mesh_id,
            "node_id": node_id,
            "priv": priv,
            "pub_b64": pub_b64,
            "auth_token": acct["auth_token"],
            "store": store,
        }
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)


@pytest.fixture
def bunny_mesh_distinct_keys(mail_module, monkeypatch, tmp_path):
    """Real-device shape: the account's bunny_pubkey (the companion's
    PairingManager key) and the vault node_pubkey (the Collar's ControlService
    key) are DISTINCT, because two separate apps generate them independently on
    the same phone. Bunny Tasker signs set-display-name with the *bunny* key.

    Regression guard: the server used to verify the rename only against the vault
    node_pubkey, so on real hardware (bunny key != node key) every rename 403'd.
    The `bunny_mesh` fixture above masked it by reusing one key for both stores."""
    store = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "accounts_distinct"))
    acct = store.create("lion-pub", pin="2222")
    mesh_id = acct["mesh_id"]
    node_id = "sm-s908w"
    bunny_priv, bunny_pub = _keypair()   # companion PairingManager key (signs renames)
    node_priv, node_pub = _keypair()     # Collar ControlService key — different key
    store.join(acct["invite_code"], node_id, "phone", bunny_pubkey=bunny_pub, display_name="")
    mail_module._vault_store.add_node(
        mesh_id,
        {"node_id": node_id, "node_type": "phone", "node_pubkey": node_pub},
    )
    monkeypatch.setattr(mail_module, "_mesh_accounts", store)
    try:
        yield {
            "mesh_id": mesh_id,
            "node_id": node_id,
            "priv": bunny_priv,       # _rename() signs with this (the bunny key)
            "pub_b64": bunny_pub,
            "node_priv": node_priv,
            "node_pub": node_pub,
            "auth_token": acct["auth_token"],
            "store": store,
        }
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)


def _rename(live_server, bunny_mesh, display_name, **overrides):
    ts = overrides.pop("ts", int(time.time() * 1000))
    node_id = overrides.pop("node_id", bunny_mesh["node_id"])
    signed_name = overrides.pop("signed_name", display_name)
    sig = overrides.pop(
        "signature",
        _sign(bunny_mesh["priv"], _name_payload(bunny_mesh["mesh_id"], node_id, ts, signed_name)),
    )
    mesh_id = overrides.pop("mesh_id", bunny_mesh["mesh_id"])
    return _post(
        f"{live_server}/api/mesh/{mesh_id}/set-display-name",
        {"node_id": node_id, "ts": ts, "display_name": display_name, "signature": sig},
    )


# ── /api/mesh/{id}/set-display-name ───────────────────────────────────────


class TestSetDisplayName:
    def test_signed_rename_is_stored_on_the_account_node(self, live_server, bunny_mesh):
        status, body = _rename(live_server, bunny_mesh, "Bunny")
        assert status == 200, body
        assert bunny_mesh["store"].get(bunny_mesh["mesh_id"])["nodes"][bunny_mesh["node_id"]]["display_name"] == "Bunny"

    def test_rename_is_visible_to_the_authed_lion(self, live_server, bunny_mesh):
        _rename(live_server, bunny_mesh, "Bunny")
        status, body = _get(
            f"{live_server}/vault/{bunny_mesh['mesh_id']}/nodes",
            {"Authorization": "Bearer " + bunny_mesh["auth_token"]},
        )
        assert status == 200
        assert body["bunny_display_name"] == "Bunny"
        assert body["nodes"][0]["display_name"] == "Bunny"

    def test_bunny_key_rename_when_node_key_differs(self, live_server, bunny_mesh_distinct_keys):
        # The real-device case: companion signs with the account bunny key while
        # the vault node_pubkey is a different Collar key. Must still succeed —
        # this is the regression that shipped (verified only against node_pubkey).
        status, body = _rename(live_server, bunny_mesh_distinct_keys, "RealBunny")
        assert status == 200, body
        acct = bunny_mesh_distinct_keys["store"].get(bunny_mesh_distinct_keys["mesh_id"])
        assert acct["nodes"][bunny_mesh_distinct_keys["node_id"]]["display_name"] == "RealBunny"

    def test_impostor_still_rejected_with_distinct_keys(self, live_server, bunny_mesh_distinct_keys):
        # Accepting the bunny key must not accept an unrelated key: a signature
        # matching neither the account bunny_pubkey nor the vault node_pubkey 403s.
        other_priv, _ = _keypair()
        ts = int(time.time() * 1000)
        sig = _sign(
            other_priv,
            _name_payload(bunny_mesh_distinct_keys["mesh_id"], bunny_mesh_distinct_keys["node_id"], ts, "Impostor"),
        )
        status, body = _rename(live_server, bunny_mesh_distinct_keys, "Impostor", signature=sig, ts=ts)
        assert status == 403, body

    def test_bad_signature_rejected(self, live_server, bunny_mesh):
        other_priv, _ = _keypair()
        ts = int(time.time() * 1000)
        sig = _sign(other_priv, _name_payload(bunny_mesh["mesh_id"], bunny_mesh["node_id"], ts, "Impostor"))
        status, body = _rename(live_server, bunny_mesh, "Impostor", ts=ts, signature=sig)
        assert status == 403
        assert "signature" in body["error"]

    def test_name_cannot_be_swapped_after_signing(self, live_server, bunny_mesh):
        """The signature covers sha256(display_name) — a relay that rewrites
        the name in flight must not be able to reuse the signature."""
        status, _ = _rename(live_server, bunny_mesh, "Rewritten", signed_name="Bunny")
        assert status == 403

    def test_stale_timestamp_rejected(self, live_server, bunny_mesh):
        status, body = _rename(live_server, bunny_mesh, "Bunny", ts=int(time.time() * 1000) - 6 * 60 * 1000)
        assert status == 403
        assert "ts" in body["error"]

    def test_node_not_in_vault_rejected(self, live_server, bunny_mesh):
        status, body = _rename(live_server, bunny_mesh, "Bunny", node_id="never-registered")
        assert status == 403
        assert "not registered" in body["error"]

    def test_unknown_mesh_is_404(self, live_server, bunny_mesh):
        status, _ = _rename(live_server, bunny_mesh, "Bunny", mesh_id="no-such-mesh")
        assert status == 404

    def test_unsafe_mesh_id_is_400(self, live_server, bunny_mesh):
        status, _ = _rename(live_server, bunny_mesh, "Bunny", mesh_id="..%2Fetc")
        assert status == 400

    def test_name_is_bounded_to_forty_chars(self, live_server, bunny_mesh):
        long_name = "B" * 80
        # Client and server must agree on the truncation, so sign the truncated form.
        status, _ = _rename(live_server, bunny_mesh, long_name, signed_name=long_name[:40])
        assert status == 200
        stored = bunny_mesh["store"].get(bunny_mesh["mesh_id"])["nodes"][bunny_mesh["node_id"]]["display_name"]
        assert stored == "B" * 40


# ── GET /vault/{id}/nodes enrichment auth ─────────────────────────────────


class TestNodesEnrichmentIsLionOnly:
    def test_anonymous_caller_gets_the_bootstrap_list_only(self, live_server, bunny_mesh):
        _rename(live_server, bunny_mesh, "Bunny")
        status, body = _get(f"{live_server}/vault/{bunny_mesh['mesh_id']}/nodes")
        assert status == 200
        # E2EE bootstrap still works…
        assert body["nodes"][0]["node_pubkey"] == bunny_mesh["pub_b64"]
        # …but none of the Lion-only enrichment leaks.
        assert "display_name" not in body["nodes"][0]
        assert "bunny_display_name" not in body
        assert "bunny_pubkey" not in body
        assert "auto_accept" not in body

    def test_wrong_auth_token_is_treated_as_anonymous(self, live_server, bunny_mesh):
        _rename(live_server, bunny_mesh, "Bunny")
        status, body = _get(
            f"{live_server}/vault/{bunny_mesh['mesh_id']}/nodes",
            {"Authorization": "Bearer not-the-token"},
        )
        assert status == 200
        assert "bunny_display_name" not in body

    def test_authed_lion_sees_auto_accept_and_bunny_key(self, live_server, bunny_mesh):
        status, body = _get(f"{live_server}/vault/{bunny_mesh['mesh_id']}/nodes?auth_token={bunny_mesh['auth_token']}")
        assert status == 200
        assert body["bunny_pubkey"] == bunny_mesh["pub_b64"]
        assert body["auto_accept"] is True


# ── /webhook/desktop-task guards ──────────────────────────────────────────


@pytest.fixture
def task_mesh(mail_module, monkeypatch, tmp_path):
    store = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "task-accounts"))
    acct = store.create("lion-task", pin="2222")
    mesh_id = acct["mesh_id"]
    token = "test-task-admin-" + str(int(time.time() * 1000))
    monkeypatch.setattr(mail_module, "ADMIN_TOKEN", token)
    monkeypatch.setattr(mail_module, "_mesh_accounts", store)
    monkeypatch.setattr(mail_module, "OPERATOR_MESH_ID", mesh_id)
    mail_module._orders_registry.get_or_create(mesh_id)
    try:
        yield {"mesh_id": mesh_id, "token": token}
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)


def _arm(live_server, task_mesh, **extra):
    body = {
        "admin_token": task_mesh["token"],
        "mesh_id": task_mesh["mesh_id"],
        "text": "Write a paragraph about what you tried to do.",
        "reason": "test",
    }
    body.update(extra)
    return _post(f"{live_server}/webhook/desktop-task", body)


class TestDesktopTaskGuards:
    def test_arms_a_task_on_the_operator_mesh(self, live_server, task_mesh, mail_module):
        status, body = _arm(live_server, task_mesh)
        assert status == 200, body
        orders = mail_module._orders_registry.get(task_mesh["mesh_id"])
        assert orders.get("deadline_task_text") == "Write a paragraph about what you tried to do."
        assert int(orders.get("deadline_task_deadline_ms", 0)) > int(time.time() * 1000)

    def test_non_operator_mesh_is_refused(self, live_server, task_mesh, mail_module, monkeypatch):
        """The Collar's vault-RPC dispatch has no set-deadline-task case, so on
        a non-operator mesh the task never reaches the phone — but the miss
        penalty would still fire. Refuse instead of charging for an invisible
        task."""
        monkeypatch.setattr(mail_module, "OPERATOR_MESH_ID", "some-other-mesh")
        status, body = _arm(live_server, task_mesh)
        assert status == 409
        assert "operator-mesh only" in body["error"]
        assert not mail_module._orders_registry.get(task_mesh["mesh_id"]).get("deadline_task_text")

    def test_will_not_clobber_an_armed_task(self, live_server, task_mesh):
        assert _arm(live_server, task_mesh)[0] == 200
        status, body = _arm(live_server, task_mesh, text="A second, different task")
        assert status == 409
        assert "already armed" in body["error"]

    def test_will_not_clobber_a_miss_lock_in_progress(self, live_server, task_mesh, mail_module):
        """An expired task that locked the bunny still owns the slot — arming
        over it would strand them in an unclearable lock."""
        orders = mail_module._orders_registry.get_or_create(task_mesh["mesh_id"])
        orders.set("deadline_task_deadline_ms", str(int(time.time() * 1000) - 60_000))
        orders.set("deadline_task_locked_by_miss", "1")
        status, body = _arm(live_server, task_mesh)
        assert status == 409
        assert "already armed" in body["error"]

    def test_bad_admin_token_is_403(self, live_server, task_mesh):
        status, _ = _arm(live_server, task_mesh, admin_token="wrong")
        assert status == 403

    def test_empty_text_is_400(self, live_server, task_mesh):
        status, _ = _arm(live_server, task_mesh, text="   ")
        assert status == 400

    def test_deadline_beyond_a_week_is_400(self, live_server, task_mesh):
        status, _ = _arm(live_server, task_mesh, deadline_minutes=8 * 24 * 60)
        assert status == 400

    def test_invalid_on_miss_is_400(self, live_server, task_mesh):
        status, _ = _arm(live_server, task_mesh, on_miss="factory-reset")
        assert status == 400

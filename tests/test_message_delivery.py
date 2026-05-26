"""Regression coverage for the message-delivery fixes (Phase 1).

Pins the two server-side guarantees the client retry logic now depends on:

  D3  unique, monotonic message ids that survive the size-cap — the old
      `{ts}_{len(messages)}` scheme stopped being unique once trimming pinned
      len() at MAX_MESSAGES, so same-millisecond sends collided and
      mark_read/edit/delete could hit the wrong message.

  D4  `/messages/send` idempotency via `client_msg_id` — a retried send (network
      blip / app restart) must dedup to a single stored message so the client
      can safely retry without spamming Bunny's inbox.

Store-level tests exercise MessageStore directly; the HTTP block reuses the
live-relay pattern from test_e2e_messages_admin.py to prove the wire path.
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
sys.path.insert(0, str(REPO_ROOT))
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture
def store(tmp_path):
    from focuslock_mesh import MessageStore

    return MessageStore(persist_path=str(tmp_path / "msgs.json"))


# ──────────────────────── D3 — unique ids across the cap ────────────────────────


class TestIdUniqueness:
    def test_ids_unique_for_same_ms_after_cap(self, store):
        """The old len()-based id collided once the cap started trimming: every
        post-cap add saw len()==MAX_MESSAGES, so two sends in the same
        millisecond got identical ids. With a monotonic counter they don't."""
        cap = store.MAX_MESSAGES
        ids = []
        # Fill past the cap, then keep adding — all with one fixed ts so the
        # only thing keeping ids apart is the counter, not the clock.
        for _ in range(cap + 50):
            ids.append(store.add({"from": "lion", "text": "x", "ts": 999})["id"])
        assert len(ids) == len(set(ids)), "duplicate message ids issued after cap"
        # Survivors in the trimmed store are also internally unique.
        live_ids = [m["id"] for m in store.messages]
        assert len(live_ids) == len(set(live_ids))

    def test_seq_resumes_after_reload(self, tmp_path):
        """A store reopened from disk must not re-issue an id it already handed
        out before the process restarted."""
        from focuslock_mesh import MessageStore

        path = str(tmp_path / "msgs.json")
        s1 = MessageStore(persist_path=path)
        first_ids = {s1.add({"from": "lion", "text": f"m{i}", "ts": 1000})["id"] for i in range(5)}

        s2 = MessageStore(persist_path=path)
        second_ids = {s2.add({"from": "lion", "text": f"n{i}", "ts": 1000})["id"] for i in range(5)}

        assert first_ids.isdisjoint(second_ids)

    def test_mark_read_hits_only_its_own_message_after_cap(self, store):
        """User-visible symptom of D3: a message that 'won't mark read' / keeps
        re-notifying because mark_read matched a colliding id. With unique ids,
        marking one id never touches another."""
        cap = store.MAX_MESSAGES
        for _ in range(cap):
            store.add({"from": "lion", "text": "filler", "ts": 999})
        a = store.add({"from": "lion", "text": "A", "ts": 999})
        b = store.add({"from": "lion", "text": "B", "ts": 999})
        assert a["id"] != b["id"]

        store.mark_read(a["id"], "bunny")
        a_live = next(m for m in store.messages if m["id"] == a["id"])
        b_live = next(m for m in store.messages if m["id"] == b["id"])
        assert a_live.get("read_by") == ["bunny"]
        assert "read_by" not in b_live  # untouched


# ──────────────────────── D4 — client_msg_id idempotency ────────────────────────


class TestClientMsgIdDedup:
    def test_same_client_msg_id_dedups(self, store):
        first = store.add({"from": "bunny", "text": "hi", "client_msg_id": "cm-1"})
        second = store.add({"from": "bunny", "text": "hi", "client_msg_id": "cm-1"})
        assert len(store.messages) == 1
        assert second["id"] == first["id"]
        assert second is first

    def test_dedup_returns_original_not_the_retry_body(self, store):
        """The retry can carry slightly different incidental fields; dedup must
        return the already-stored canonical message, ignoring the retry body."""
        store.add({"from": "bunny", "text": "original", "client_msg_id": "cm-2"})
        retry = store.add({"from": "bunny", "text": "tampered", "client_msg_id": "cm-2"})
        assert retry["text"] == "original"
        assert len(store.messages) == 1

    def test_distinct_client_msg_ids_both_stored(self, store):
        store.add({"from": "bunny", "text": "a", "client_msg_id": "cm-a"})
        store.add({"from": "bunny", "text": "b", "client_msg_id": "cm-b"})
        assert len(store.messages) == 2

    def test_missing_client_msg_id_always_appends(self, store):
        """Backward compatibility: clients that don't send a client_msg_id keep
        the old append-every-time behavior."""
        store.add({"from": "bunny", "text": "a"})
        store.add({"from": "bunny", "text": "a"})
        assert len(store.messages) == 2


# ──────────────────────── HTTP wire path ────────────────────────


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_delivery", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_delivery"] = mod
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
def seeded_mesh(mail_module):
    mesh_id = "msgdel-" + str(int(time.time() * 1000000))
    node_id = "bunny-node-1"
    bunny_priv, bunny_pub = _keypair()
    _lion_priv, lion_pub = _keypair()
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
        yield {"mesh_id": mesh_id, "node_id": node_id, "bunny_priv": bunny_priv}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)
        mail_module._orders_registry.docs.pop(mesh_id, None)
        try:
            mail_module._message_stores.pop(mesh_id, None)
        except AttributeError:
            pass


def _bunny_send_body(seeded, text, client_msg_id=None):
    mesh, node = seeded["mesh_id"], seeded["node_id"]
    ts = int(time.time() * 1000)
    payload = f"{mesh}|{node}|bunny|{text}|0|0|{ts}"
    body = {
        "op": "send",
        "node_id": node,
        "from": "bunny",
        "text": text,
        "ts": ts,
        "signature": _sign(seeded["bunny_priv"], payload),
    }
    if client_msg_id:
        body["client_msg_id"] = client_msg_id
    return body


class TestSendIdempotencyHTTP:
    def test_retry_with_same_client_msg_id_stores_once(self, live_server, seeded_mesh, mail_module):
        url = f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/send"
        # Same body twice — exactly what the client retry does (same ts +
        # signature + client_msg_id so the signature still verifies).
        body = _bunny_send_body(seeded_mesh, "hello", client_msg_id="retry-xyz")
        s1, r1 = _http_post(url, body)
        s2, r2 = _http_post(url, body)
        assert s1 == 200 and s2 == 200
        assert r1["message"]["id"] == r2["message"]["id"]
        store = mail_module._get_message_store(seeded_mesh["mesh_id"])
        assert len(store.messages) == 1

    def test_retry_without_client_msg_id_appends(self, live_server, seeded_mesh, mail_module):
        """No idempotency key → existing behavior (two distinct sends)."""
        url = f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/send"
        s1, _ = _http_post(url, _bunny_send_body(seeded_mesh, "a"))
        time.sleep(0.002)
        s2, _ = _http_post(url, _bunny_send_body(seeded_mesh, "b"))
        assert s1 == 200 and s2 == 200
        store = mail_module._get_message_store(seeded_mesh["mesh_id"])
        assert len(store.messages) == 2

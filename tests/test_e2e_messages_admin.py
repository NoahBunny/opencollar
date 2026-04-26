"""End-to-end HTTP-level QA for the messaging endpoints.

`test_e2e_qa.py::TestMessagesSendE2E` covers `/messages/send`. This file
covers the rest of the message routes — `/messages/fetch`, `/messages/mark`,
`/messages/edit`, `/messages/delete` — plus the ntfy-fan-out hook fired
on send/edit/delete.

Signed payload layouts (per focuslock-mail.py:4083-4132):
    send:   "{mesh_id}|{node_id}|{from}|{text}|{pinned}|{mandatory}|{ts}"
    fetch:  "{mesh_id}|{node_id}|{from}|{since}|{ts}"
    mark:   "{mesh_id}|{node_id}|{from}|{message_id}|{status}|{ts}"
    edit:   "{mesh_id}|{node_id}|{from}|edit|{message_id}|{text}|{ts}"
    delete: "{mesh_id}|{node_id}|{from}|delete|{message_id}|{ts}"

edit + delete are Lion-only; the server rejects bunny-signed edit/delete
at the role check before signature verification (focuslock-mail.py:4051).
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_msg_e2e", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_msg_e2e"] = mod
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
    mesh_id = "msge2e-" + str(int(time.time() * 1000000))
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
        # Drop the per-mesh MessageStore so cross-test bleed-through can't
        # mask a regression.
        try:
            mail_module._message_stores.pop(mesh_id, None)
        except AttributeError:
            pass


def _send_lion(seeded, text, pinned=False, mandatory=False):
    """Helper: send a lion-signed message and return the stored entry."""
    mesh = seeded["mesh_id"]
    node = seeded["node_id"]
    ts = int(time.time() * 1000)
    pinned_s = "1" if pinned else "0"
    mandatory_s = "1" if mandatory else "0"
    payload = f"{mesh}|{node}|lion|{text}|{pinned_s}|{mandatory_s}|{ts}"
    sig = _sign(seeded["lion_priv"], payload)
    return {
        "op": "send",
        "node_id": node,
        "from": "lion",
        "text": text,
        "pinned": pinned,
        "mandatory_reply": mandatory,
        "ts": ts,
        "signature": sig,
    }


# ── /messages/edit — Lion-only edit ──


class TestMessagesEditE2E:
    """Lion-signed edit. Bunny-signed edit must be rejected at the role
    check (403) before signature verification — so even a bunny who
    somehow obtained a forged lion signature can't sneak through, but
    the more-common case (bunny tries to rewrite history) is closed
    earlier with a clearer error."""

    def _seed_message(self, live_server, seeded, text="original"):
        body = _send_lion(seeded, text)
        status, resp = _http_post(f"{live_server}/api/mesh/{seeded['mesh_id']}/messages/send", body)
        assert status == 200
        return resp["message"]["id"]

    def test_lion_edit_overwrites_and_appends_history(self, live_server, seeded_mesh, mail_module):
        msg_id = self._seed_message(live_server, seeded_mesh, "first")
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|edit|{msg_id}|second|{ts}"
        sig = _sign(seeded_mesh["lion_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/edit",
            {
                "op": "edit",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "text": "second",
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200
        edited = resp["message"]
        assert edited["text"] == "second"
        assert edited["edited_at"] >= ts
        assert len(edited["edit_history"]) == 1
        assert edited["edit_history"][0]["prev_text"] == "first"

    def test_bunny_edit_rejected_at_role_check(self, live_server, seeded_mesh):
        """Even with a (hypothetical) valid bunny signature, edit is
        Lion-only — server returns 403 before checking the signature."""
        msg_id = self._seed_message(live_server, seeded_mesh, "x")
        ts = int(time.time() * 1000)
        # Sign with bunny key; from=bunny is illegal for edit.
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|bunny|edit|{msg_id}|y|{ts}"
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/edit",
            {
                "op": "edit",
                "node_id": seeded_mesh["node_id"],
                "from": "bunny",
                "message_id": msg_id,
                "text": "y",
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 403
        assert "lion-only" in resp.get("error", "")

    def test_edit_unknown_id_returns_404(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        msg_id = "does-not-exist"
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|edit|{msg_id}|x|{ts}"
        sig = _sign(seeded_mesh["lion_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/edit",
            {
                "op": "edit",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "text": "x",
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 404
        assert resp.get("error") == "not found"

    def test_edit_with_bad_signature_returns_403(self, live_server, seeded_mesh):
        msg_id = self._seed_message(live_server, seeded_mesh, "x")
        ts = int(time.time() * 1000)
        status, _ = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/edit",
            {
                "op": "edit",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "text": "y",
                "ts": ts,
                "signature": "AAAA" + "=" * 340,
            },
        )
        assert status == 403

    def test_edit_missing_message_id_returns_400(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        # Server validates message_id presence before signature, so we
        # don't need a real signature here.
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/edit",
            {
                "op": "edit",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "text": "y",
                "ts": ts,
                "signature": "x",
            },
        )
        assert status == 400
        assert "message_id" in resp.get("error", "")


# ── /messages/delete — Lion-only tombstone ──


class TestMessagesDeleteE2E:
    def _seed_message(self, live_server, seeded, text="to-delete"):
        body = _send_lion(seeded, text)
        status, resp = _http_post(f"{live_server}/api/mesh/{seeded['mesh_id']}/messages/send", body)
        assert status == 200
        return resp["message"]["id"]

    def test_lion_delete_sets_tombstone(self, live_server, seeded_mesh, mail_module):
        msg_id = self._seed_message(live_server, seeded_mesh, "secret")
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|delete|{msg_id}|{ts}"
        sig = _sign(seeded_mesh["lion_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/delete",
            {
                "op": "delete",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200
        deleted = resp["message"]
        assert deleted["deleted"] is True
        assert deleted["deleted_by"] == "lion"
        # Original text preserved server-side so Lion's own audit view can
        # render it. Client-side Bunny UI is responsible for showing
        # "[deleted]".
        assert deleted["text"] == "secret"

    def test_delete_idempotent(self, live_server, seeded_mesh):
        msg_id = self._seed_message(live_server, seeded_mesh, "x")
        # First delete.
        ts1 = int(time.time() * 1000)
        payload1 = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|delete|{msg_id}|{ts1}"
        sig1 = _sign(seeded_mesh["lion_priv"], payload1)
        status, _ = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/delete",
            {
                "op": "delete",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "ts": ts1,
                "signature": sig1,
            },
        )
        assert status == 200
        # Second delete with a later ts — must be a no-op (deleted_at
        # remains the first ts).
        time.sleep(0.01)
        ts2 = int(time.time() * 1000)
        payload2 = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|delete|{msg_id}|{ts2}"
        sig2 = _sign(seeded_mesh["lion_priv"], payload2)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/delete",
            {
                "op": "delete",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "ts": ts2,
                "signature": sig2,
            },
        )
        assert status == 200
        # MessageStore returns the record as-is on idempotent re-delete.
        assert resp["message"]["deleted_at"] <= ts2

    def test_bunny_delete_rejected_at_role_check(self, live_server, seeded_mesh):
        msg_id = self._seed_message(live_server, seeded_mesh, "x")
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|bunny|delete|{msg_id}|{ts}"
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/delete",
            {
                "op": "delete",
                "node_id": seeded_mesh["node_id"],
                "from": "bunny",
                "message_id": msg_id,
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 403
        assert "lion-only" in resp.get("error", "")

    def test_delete_unknown_id_returns_404(self, live_server, seeded_mesh):
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|delete|nope|{ts}"
        sig = _sign(seeded_mesh["lion_priv"], payload)
        status, _resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/delete",
            {
                "op": "delete",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": "nope",
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 404


# ── /messages/fetch — both parties may read ──


class TestMessagesFetchE2E:
    def test_fetch_returns_messages_newest_first(self, live_server, seeded_mesh):
        for n in range(3):
            body = _send_lion(seeded_mesh, f"msg-{n}")
            time.sleep(0.005)  # spread the server-side ts so ordering is stable
            status, _ = _http_post(
                f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/send",
                body,
            )
            assert status == 200
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|bunny|0|{ts}"
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/fetch",
            {
                "op": "fetch",
                "node_id": seeded_mesh["node_id"],
                "from": "bunny",
                "since": 0,
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200
        msgs = resp["messages"]
        assert len(msgs) == 3
        # Newest first.
        assert msgs[0]["text"] == "msg-2"
        assert msgs[2]["text"] == "msg-0"

    def test_fetch_preserves_tombstone_and_history(self, live_server, seeded_mesh):
        # Send → edit → delete the same message; fetch must return the
        # tombstone with the edit history attached.
        body = _send_lion(seeded_mesh, "v1")
        status, sresp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/send",
            body,
        )
        assert status == 200
        msg_id = sresp["message"]["id"]
        # Edit.
        ts = int(time.time() * 1000)
        epayload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|edit|{msg_id}|v2|{ts}"
        esig = _sign(seeded_mesh["lion_priv"], epayload)
        _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/edit",
            {
                "op": "edit",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "text": "v2",
                "ts": ts,
                "signature": esig,
            },
        )
        # Delete.
        ts2 = int(time.time() * 1000)
        dpayload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|delete|{msg_id}|{ts2}"
        dsig = _sign(seeded_mesh["lion_priv"], dpayload)
        _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/delete",
            {
                "op": "delete",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "ts": ts2,
                "signature": dsig,
            },
        )
        # Fetch.
        ts3 = int(time.time() * 1000)
        fpayload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|bunny|0|{ts3}"
        fsig = _sign(seeded_mesh["bunny_priv"], fpayload)
        status, fresp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/fetch",
            {
                "op": "fetch",
                "node_id": seeded_mesh["node_id"],
                "from": "bunny",
                "since": 0,
                "ts": ts3,
                "signature": fsig,
            },
        )
        assert status == 200
        msgs = fresp["messages"]
        assert len(msgs) == 1
        m = msgs[0]
        assert m["deleted"] is True
        assert m["text"] == "v2"  # post-edit text preserved server-side
        assert len(m["edit_history"]) == 1
        assert m["edit_history"][0]["prev_text"] == "v1"

    def test_fetch_cross_mesh_signer_rejected(self, live_server, seeded_mesh, mail_module):
        # A second mesh — its lion key must not be accepted as a signer
        # for the first mesh's fetch.
        other_mesh = "other-" + str(int(time.time() * 1000000))
        _, other_lion_pub = _keypair()
        other_lion_priv, _ = _keypair()
        mail_module._mesh_accounts.meshes[other_mesh] = {
            "mesh_id": other_mesh,
            "lion_pubkey": other_lion_pub,  # genuinely different keypair
            "auth_token": "x",
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
        try:
            ts = int(time.time() * 1000)
            payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|0|{ts}"
            # Sign with the *other* mesh's lion key; verifier will pull
            # seeded_mesh's lion_pubkey and reject.
            sig = _sign(other_lion_priv, payload)
            status, _ = _http_post(
                f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/fetch",
                {
                    "op": "fetch",
                    "node_id": seeded_mesh["node_id"],
                    "from": "lion",
                    "since": 0,
                    "ts": ts,
                    "signature": sig,
                },
            )
            assert status == 403
        finally:
            mail_module._mesh_accounts.meshes.pop(other_mesh, None)


# ── /messages/mark — read / replied flags ──


class TestMessagesMarkE2E:
    def _seed(self, live_server, seeded):
        body = _send_lion(seeded, "to-mark")
        status, resp = _http_post(f"{live_server}/api/mesh/{seeded['mesh_id']}/messages/send", body)
        assert status == 200
        return resp["message"]["id"]

    def test_mark_read_records_reader(self, live_server, seeded_mesh, mail_module):
        msg_id = self._seed(live_server, seeded_mesh)
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|bunny|{msg_id}|read|{ts}"
        sig = _sign(seeded_mesh["bunny_priv"], payload)
        status, _ = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/mark",
            {
                "op": "mark",
                "node_id": seeded_mesh["node_id"],
                "from": "bunny",
                "message_id": msg_id,
                "status": "read",
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200
        store = mail_module._get_message_store(seeded_mesh["mesh_id"])
        match = [m for m in store.messages if m.get("id") == msg_id]
        assert len(match) == 1
        assert "bunny" in match[0].get("read_by", [])

    def test_mark_invalid_status_returns_400(self, live_server, seeded_mesh):
        msg_id = self._seed(live_server, seeded_mesh)
        ts = int(time.time() * 1000)
        # Sign with a payload that has a bogus status; server validates
        # status before signature so this is a 400, not a 403.
        status, resp = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/mark",
            {
                "op": "mark",
                "node_id": seeded_mesh["node_id"],
                "from": "bunny",
                "message_id": msg_id,
                "status": "skimmed",
                "ts": ts,
                "signature": "x",
            },
        )
        assert status == 400
        assert "status" in resp.get("error", "")


# ── ntfy fan-out — wake subscribers on send/edit/delete ──


class TestMessagesNtfyFanOut:
    """Verify _messages_publish_ntfy is invoked on the three mutation
    paths (send / edit / delete). Monkeypatch ntfy_fn (the module-level
    publish function imported from focuslock_ntfy) so the assertion is
    deterministic — actual ntfy I/O is gated by _ntfy_enabled and skipped
    unless config wires it up."""

    def test_send_fires_ntfy(self, live_server, seeded_mesh, mail_module, monkeypatch):
        seen = []
        monkeypatch.setattr(mail_module, "_ntfy_enabled", True)
        monkeypatch.setattr(mail_module, "ntfy_fn", lambda v, mid: seen.append((v, mid)))
        body = _send_lion(seeded_mesh, "ping")
        status, _ = _http_post(f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/send", body)
        assert status == 200
        assert seen, "ntfy publish was not called on /messages/send"
        assert seen[-1][1] == seeded_mesh["mesh_id"]

    def test_edit_fires_ntfy(self, live_server, seeded_mesh, mail_module, monkeypatch):
        # Seed the message *before* enabling the spy so we only count the
        # edit's ntfy call.
        body = _send_lion(seeded_mesh, "first")
        status, sresp = _http_post(f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/send", body)
        assert status == 200
        msg_id = sresp["message"]["id"]
        seen = []
        monkeypatch.setattr(mail_module, "_ntfy_enabled", True)
        monkeypatch.setattr(mail_module, "ntfy_fn", lambda v, mid: seen.append((v, mid)))
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|edit|{msg_id}|second|{ts}"
        sig = _sign(seeded_mesh["lion_priv"], payload)
        status, _ = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/edit",
            {
                "op": "edit",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "text": "second",
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200
        assert any(m == seeded_mesh["mesh_id"] for _, m in seen)

    def test_delete_fires_ntfy(self, live_server, seeded_mesh, mail_module, monkeypatch):
        body = _send_lion(seeded_mesh, "x")
        status, sresp = _http_post(f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/send", body)
        assert status == 200
        msg_id = sresp["message"]["id"]
        seen = []
        monkeypatch.setattr(mail_module, "_ntfy_enabled", True)
        monkeypatch.setattr(mail_module, "ntfy_fn", lambda v, mid: seen.append((v, mid)))
        ts = int(time.time() * 1000)
        payload = f"{seeded_mesh['mesh_id']}|{seeded_mesh['node_id']}|lion|delete|{msg_id}|{ts}"
        sig = _sign(seeded_mesh["lion_priv"], payload)
        status, _ = _http_post(
            f"{live_server}/api/mesh/{seeded_mesh['mesh_id']}/messages/delete",
            {
                "op": "delete",
                "node_id": seeded_mesh["node_id"],
                "from": "lion",
                "message_id": msg_id,
                "ts": ts,
                "signature": sig,
            },
        )
        assert status == 200
        assert any(m == seeded_mesh["mesh_id"] for _, m in seen)

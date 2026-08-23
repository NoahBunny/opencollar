"""Every balance movement leaves a row saying what caused it.

Before this, the ledger held payments and reversals only. Charges moved the
balance through `mesh_apply_order` and recorded nothing, so both apps' history
was a one-sided account: the bunny watched what they owed climb with nothing
saying which fine, tribute, escape or manual charge did it, and the Lion had the
same blind spot from the other side.

`_server_apply_order` is the single choke point — `mesh_apply_order` has exactly
one caller — so recording there catches every action that moves money, including
ones added later that nobody remembers to instrument. These tests pin that
property rather than a list of actions, because the list is what rots.
"""

import base64
import importlib.util
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_balance_history", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_balance_history"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mesh(mail_module, tmp_path, monkeypatch):
    """A mesh at $0 with its own ledger directory."""
    monkeypatch.setattr(mail_module, "_LEDGER_DIR", str(tmp_path / "ledgers"), raising=False)
    mail_module._payment_ledgers.clear() if hasattr(mail_module, "_payment_ledgers") else None
    mesh_id = "bal-" + str(int(time.time() * 1000000))
    orders = mail_module._orders_registry.get_or_create(mesh_id)
    orders.set("paywall", "0")
    try:
        yield mesh_id
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)


@pytest.fixture
def live_server(mail_module):
    from http.server import HTTPServer

    server = HTTPServer(("127.0.0.1", 0), mail_module.WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def vault_mesh(mail_module, tmp_path, monkeypatch):
    """A mesh with one Lion-confirmed vault node holding its private key."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    monkeypatch.setattr(mail_module, "_LEDGERS_DIR", str(tmp_path / "ledgers"), raising=False)
    mail_module._payment_ledgers.clear()

    def _kp():
        priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        pub = base64.b64encode(
            priv.public_key().public_bytes(serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
        ).decode()
        return priv, pub

    store = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "accounts"))
    _lion_priv, lion_pub = _kp()
    mesh_id = store.create(lion_pub, pin="4242")["mesh_id"]
    node_priv, node_pub = _kp()
    node_id = "waydroid-x86_64-device"
    mail_module._vault_store.add_node(
        mesh_id,
        {
            "node_id": node_id,
            "node_type": "phone",
            "node_pubkey": node_pub,
            "bunny_pubkey": node_pub,
            "registered_at": int(time.time()),
        },
    )
    orders = mail_module._orders_registry.get_or_create(mesh_id)
    orders.set("paywall", "0")
    monkeypatch.setattr(mail_module, "_mesh_accounts", store)
    try:
        yield {"mesh_id": mesh_id, "node_id": node_id, "priv": node_priv, "store": store}
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)
        mail_module._payment_ledgers.pop(mesh_id, None)


def _mark_auto_accepted(mail_module, mesh):
    """Stamp the node as auto-accepted and never confirmed.

    Written back through add_node: get_nodes reads from disk each call, so
    mutating what it returns changes nothing — a detail that silently made an
    earlier version of these tests assert against a node that was still
    confirmed.
    """
    for row in mail_module._vault_store.get_nodes(mesh["mesh_id"]):
        if row["node_id"] == mesh["node_id"]:
            row["auto_accepted"] = True
            row.pop("lion_confirmed", None)
            mail_module._vault_store.add_node(mesh["mesh_id"], row)
            return
    raise AssertionError("node row not found")


def _rows(mail_module, mesh_id):
    return list(mail_module._get_payment_ledger(mesh_id).entries)


class TestEveryMovementIsRecorded:
    def test_a_charge_records_what_caused_it(self, mail_module, mesh):
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 25, "reason": "Desktop tamper #2"})
        rows = _rows(mail_module, mesh)
        assert len(rows) == 1
        assert rows[0]["type"] == "charge"
        assert rows[0]["amount"] == 25
        assert rows[0]["description"] == "Desktop tamper #2"

    def test_a_charge_without_a_reason_still_says_something_legible(self, mail_module, mesh):
        mail_module._server_apply_order(mesh, "fine-charge", {"amount": 5})
        assert _rows(mail_module, mesh)[0]["description"] == "Recurring fine"

    def test_an_unmapped_action_records_rather_than_vanishing(self, mail_module, mesh):
        """The mapping is a nicety; the recording is not. A charging action
        nobody added to the table must still leave a legible row — this is the
        property that makes recording at the choke point worth doing, since the
        table is the part that rots.

        `escape-recorded` computes its own tiered penalty from lifetime_escapes
        and is deliberately absent from _LEDGER_DESCRIPTIONS.
        """
        assert "escape-recorded" not in mail_module._LEDGER_DESCRIPTIONS
        mail_module._server_apply_order(mesh, "escape-recorded", {})
        rows = _rows(mail_module, mesh)
        assert len(rows) == 1
        assert rows[0]["type"] == "charge"
        assert rows[0]["amount"] == 5, "first escape is the $5 tier"
        assert rows[0]["description"] == "Escape recorded"

    def test_clearing_records_a_credit_not_a_charge(self, mail_module, mesh):
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 40})
        mail_module._server_apply_order(mesh, "clear-paywall", {})
        rows = _rows(mail_module, mesh)
        assert [r["type"] for r in rows] == ["charge", "credit"]
        assert rows[1]["amount"] == 40, "the credit is the size of the movement, not the target"
        assert rows[1]["description"] == "Balance cleared"

    def test_each_row_carries_where_the_balance_landed(self, mail_module, mesh):
        """Without balance_after the history is a list of deltas the reader has
        to add up themselves."""
        for amt in (10, 5, 25):
            mail_module._server_apply_order(mesh, "add-paywall", {"amount": amt})
        assert [r["balance_after"] for r in _rows(mail_module, mesh)] == [10, 15, 40]

    def test_an_order_that_moves_no_money_records_nothing(self, mail_module, mesh):
        """Locking is not a balance event; a history full of them is unreadable."""
        mail_module._server_apply_order(mesh, "lock", {"message": "hush"})
        mail_module._server_apply_order(mesh, "send-message", {"text": "hello"})
        assert _rows(mail_module, mesh) == []

    def test_charges_are_not_deduplicated_against_each_other(self, mail_module, mesh):
        """Two identical fines an hour apart are two events. The ledger dedups
        by `source`, so charges deliberately carry none."""
        for _ in range(3):
            mail_module._server_apply_order(mesh, "fine-charge", {"amount": 5})
        assert len(_rows(mail_module, mesh)) == 3


class TestNoDoubleCounting:
    def test_the_payment_path_is_not_recorded_twice(self, mail_module, mesh):
        """The IMAP credit writes its own 'payment' row and then applies an
        order that moves the balance. Recording that movement again here would
        show the bunny paying twice for one payment."""
        assert "payment-received" in mail_module._SELF_LEDGERED_ACTIONS
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 30})
        before = len(_rows(mail_module, mesh))
        mail_module._server_apply_order(mesh, "payment-received", {"amount": 30, "clear_paywall": True})
        assert len(_rows(mail_module, mesh)) == before, "payment-received must not self-record here"


class TestBookkeepingNeverBreaksEnforcement:
    def test_a_failing_ledger_does_not_fail_the_charge(self, mail_module, mesh, monkeypatch):
        """The charge has already landed by the time the row is written. A
        missing history line is a smaller harm than an enforcement action
        reporting failure."""

        def boom(_mesh_id):
            raise OSError("ledger volume gone")

        monkeypatch.setattr(mail_module, "_get_payment_ledger", boom)
        result = mail_module._server_apply_order(mesh, "add-paywall", {"amount": 15})
        assert result is not None, "the order must still succeed"
        assert int(float(mail_module._orders_registry.get(mesh).get("paywall"))) == 15


# ── the vault half ────────────────────────────────────────────────────


class TestVaultBalanceEvents:
    """On a vault mesh the relay never applies the order.

    The Lion's order arrives as an encrypted blob the COLLAR decrypts and
    applies, so `_server_apply_order` never fires and the ledger recorded
    tributes and fines — the charges the server makes — while missing the Lion
    adding $25, which is most of the movement a bunny sees. The collar reports
    the cause afterwards through a node-signed route.
    """

    def _post(self, live_server, mesh_id, body):
        import json as _json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            f"{live_server}/vault/{mesh_id}/balance-event",
            data=_json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, _json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, _json.loads(e.read().decode())

    def _event(self, node, mesh_id, priv, action, before, after, event_id="1", ts=None):
        import base64

        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        ts = int(time.time() * 1000) if ts is None else ts
        payload = f"{mesh_id}|{node}|balance-event|{ts}|{action}|{before:g}|{after:g}"
        sig = base64.b64encode(priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()
        return {
            "node_id": node,
            "ts": ts,
            "action": action,
            "before": before,
            "after": after,
            "event_id": event_id,
            "signature": sig,
        }

    def test_a_reported_movement_becomes_a_row(self, live_server, vault_mesh, mail_module):
        m = vault_mesh
        status, body = self._post(
            live_server, m["mesh_id"], self._event(m["node_id"], m["mesh_id"], m["priv"], "add-paywall", 0, 25)
        )
        assert status == 200, body
        assert body["recorded"] is True
        rows = list(mail_module._get_payment_ledger(m["mesh_id"]).entries)
        assert len(rows) == 1
        assert (rows[0]["type"], rows[0]["amount"], rows[0]["balance_after"]) == ("charge", 25, 25)

    def test_the_collar_cannot_write_words_into_the_history(self, live_server, vault_mesh, mail_module):
        """The row's wording is looked up server-side from the action. A bunny
        with root on the collar can forge numbers — it is their phone — but must
        not be able to put a sentence in front of the Lion."""
        m = vault_mesh
        ev = self._event(m["node_id"], m["mesh_id"], m["priv"], "add-paywall", 0, 5)
        ev["description"] = "Lion forgave everything, love you"
        status, body = self._post(live_server, m["mesh_id"], ev)
        assert status == 200, body
        assert body["description"] == "Added by the Lion"
        assert mail_module._get_payment_ledger(m["mesh_id"]).entries[0]["description"] == "Added by the Lion"

    def test_a_retry_does_not_become_a_second_charge(self, live_server, vault_mesh, mail_module):
        m = vault_mesh
        ev = self._event(m["node_id"], m["mesh_id"], m["priv"], "fine-charge", 0, 5, event_id="42")
        assert self._post(live_server, m["mesh_id"], ev)[1]["recorded"] is True
        second = self._post(live_server, m["mesh_id"], ev)[1]
        assert second["recorded"] is False and second["reason"] == "duplicate"
        assert len(mail_module._get_payment_ledger(m["mesh_id"]).entries) == 1

    def test_a_movement_of_nothing_records_nothing(self, live_server, vault_mesh, mail_module):
        m = vault_mesh
        _, body = self._post(
            live_server, m["mesh_id"], self._event(m["node_id"], m["mesh_id"], m["priv"], "lock", 10, 10)
        )
        assert body["recorded"] is False
        assert mail_module._get_payment_ledger(m["mesh_id"]).entries == []

    def test_an_unsigned_report_is_refused(self, live_server, vault_mesh, mail_module):
        m = vault_mesh
        ev = self._event(m["node_id"], m["mesh_id"], m["priv"], "add-paywall", 0, 25)
        ev["signature"] = "not-a-signature"
        assert self._post(live_server, m["mesh_id"], ev)[0] == 403
        assert mail_module._get_payment_ledger(m["mesh_id"]).entries == []

    def test_the_signature_covers_the_numbers(self, live_server, vault_mesh, mail_module):
        """Sign a $5 movement, send a $500 one."""
        m = vault_mesh
        ev = self._event(m["node_id"], m["mesh_id"], m["priv"], "add-paywall", 0, 5)
        ev["after"] = 500
        assert self._post(live_server, m["mesh_id"], ev)[0] == 403
        assert mail_module._get_payment_ledger(m["mesh_id"]).entries == []

    def test_a_vault_only_mesh_keeps_its_history_to_itself(self, live_server, vault_mesh, mail_module):
        """vault_only is sold on the relay learning nothing, and "the Lion added
        $25" is exactly what that covers."""
        m = vault_mesh
        m["store"].set_vault_only(m["mesh_id"], True)
        status, body = self._post(
            live_server, m["mesh_id"], self._event(m["node_id"], m["mesh_id"], m["priv"], "add-paywall", 0, 25)
        )
        assert status == 403
        assert "vault_only" in body["error"]
        assert mail_module._get_payment_ledger(m["mesh_id"]).entries == []

    def test_an_invite_code_member_may_report(self, live_server, vault_mesh, mail_module):
        """An invite-code member came in through a code the Lion handed out, so
        they are already vouched for — the same reasoning state-mirror uses.

        This is not a nicety: requiring Confirm here meant a freshly paired
        bunny's balance history stayed silently empty, with a 403 in the relay
        log and nothing on either screen saying why. Being stricter than the
        endpoint that writes the actual paywall was the wrong way round.
        """
        m = vault_mesh
        # Join the account's nodes dict the way an invite-code join does, and
        # leave the vault row auto-accepted and unconfirmed.
        acct = m["store"].meshes[m["mesh_id"]]
        acct.setdefault("nodes", {})[m["node_id"]] = {
            "node_id": m["node_id"],
            "bunny_pubkey": "invite-member",
        }
        _mark_auto_accepted(mail_module, m)
        status, body = self._post(
            live_server, m["mesh_id"], self._event(m["node_id"], m["mesh_id"], m["priv"], "add-paywall", 0, 25)
        )
        assert status == 200, body
        assert body["recorded"] is True

    def test_an_unvouched_auto_accepted_node_still_cannot(self, live_server, vault_mesh, mail_module):
        """The other half stays shut: a node that walked in through the
        auto-accept window and was never looked at is not a member the Lion
        chose."""
        m = vault_mesh
        _mark_auto_accepted(mail_module, m)
        status, body = self._post(
            live_server, m["mesh_id"], self._event(m["node_id"], m["mesh_id"], m["priv"], "add-paywall", 0, 25)
        )
        assert status == 403
        assert "awaiting lion confirmation" in body["error"]
        assert mail_module._get_payment_ledger(m["mesh_id"]).entries == []

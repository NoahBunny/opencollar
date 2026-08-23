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

import importlib.util
import sys
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

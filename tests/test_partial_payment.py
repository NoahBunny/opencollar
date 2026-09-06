"""A payment that doesn't cover the whole balance still has to move it.

The reported symptom: "I have paid my Lion twice now but am unable to get the
balance down." Both payments were detected and both bumped the lifetime
`total_paid_cents` counter, but `payment-received` only ever touched `paywall`
when the payment covered the balance in full. Anything short of that credited
nothing the bunny could see, so the balance sat exactly where it started and
paying more only made the discrepancy bigger.

A partial branch did exist (`focuslock_payment.reduce_paywall`) but it wrote
straight to the phone over ADB, which only homelab deployments have, and the
next vault sync overwrote it from the orders doc anyway. Vault-mode meshes —
the normal setup — had nothing debiting the balance at all.

These tests pin the debit itself, the principal it also has to take (or the
next interest tick undoes the payment), and the rounding direction.
"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "shared"))

from focuslock_payment import debit_balance

MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_partial_payment", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_partial_payment"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mesh(mail_module, tmp_path, monkeypatch):
    monkeypatch.setattr(mail_module, "_LEDGERS_DIR", str(tmp_path / "ledgers"), raising=False)
    mail_module._payment_ledgers.clear()
    mesh_id = "pay-" + str(int(time.time() * 1000000))
    orders = mail_module._orders_registry.get_or_create(mesh_id)
    orders.set("paywall", "0")
    orders.set("paywall_original", "0")
    orders.set("total_paid_cents", 0)
    try:
        yield mesh_id
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)
        mail_module._payment_ledgers.pop(mesh_id, None)


class _Orders:
    """The get/set surface debit_balance needs, nothing more."""

    def __init__(self, **kw):
        self.store = {k: str(v) for k, v in kw.items()}

    def get(self, k, default=None):
        return self.store.get(k, default)

    def set(self, k, v):
        self.store[k] = v


class TestDebitBalance:
    def test_a_partial_payment_leaves_the_remainder(self):
        o = _Orders(paywall=100, paywall_original=100)
        assert debit_balance(o, 4000) == 60
        assert o.get("paywall") == "60"

    def test_the_principal_takes_the_same_debit(self):
        """`paywall_original` is what compound interest accrues on
        (compounded = paywall_original * rate**hours, applied whenever it
        exceeds the current balance). Debiting only `paywall` would let the
        next hourly tick recompute from the un-paid principal and hand the
        bunny's payment straight back to the balance."""
        o = _Orders(paywall=100, paywall_original=100)
        debit_balance(o, 4000)
        assert o.get("paywall_original") == "60"

    def test_an_unseeded_principal_is_not_seeded_by_a_payment(self):
        """paywall_original 0 means interest is off for this lock. Paying
        should not switch it on."""
        o = _Orders(paywall=100, paywall_original=0)
        debit_balance(o, 4000)
        assert o.get("paywall_original") == "0"

    def test_paying_it_all_clears_both(self):
        o = _Orders(paywall=50, paywall_original=50)
        assert debit_balance(o, 5000) == 0
        assert o.get("paywall") == "0"
        assert o.get("paywall_original") == "0"

    def test_overpayment_floors_at_zero(self):
        o = _Orders(paywall=50, paywall_original=50)
        assert debit_balance(o, 999999) == 0
        assert o.get("paywall") == "0"

    def test_a_fractional_remainder_rounds_up(self):
        """Balances are whole-dollar strings on both sides of the wire, and
        rounding the remainder down would credit more than was sent."""
        o = _Orders(paywall=100, paywall_original=100)
        assert debit_balance(o, 1050) == 90  # $10.50 off $100 leaves $89.50 -> $90

    def test_clear_forces_zero_regardless_of_amount(self):
        o = _Orders(paywall=100, paywall_original=100)
        assert debit_balance(o, 100, clear=True) == 0
        assert o.get("paywall") == "0"

    def test_a_zero_amount_leaves_the_balance_alone(self):
        o = _Orders(paywall=100, paywall_original=100)
        assert debit_balance(o, 0) == 100
        assert o.get("paywall") == "100"

    def test_an_unparseable_balance_does_not_throw(self):
        o = _Orders(paywall="", paywall_original="null")
        assert debit_balance(o, 2500) == 0


class TestPaymentReceivedMovesTheBalance:
    def test_paying_part_of_it_reduces_what_is_owed(self, mail_module, mesh):
        orders = mail_module._orders_registry.get(mesh)
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 100})
        mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 4000})
        assert orders.get("paywall") == "60"

    def test_two_partial_payments_accumulate(self, mail_module, mesh):
        """The reported bug, exactly: paid twice, balance never moved."""
        orders = mail_module._orders_registry.get(mesh)
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 100})
        mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 2000})
        mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 3000})
        assert orders.get("paywall") == "50"

    def test_a_payment_covering_it_clears_and_reports_cleared(self, mail_module, mesh):
        orders = mail_module._orders_registry.get(mesh)
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 40})
        result = mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 4000})
        assert orders.get("paywall") == "0"
        assert result["cleared"] is True
        assert result["paywall"] == 0

    def test_the_lifetime_counter_keeps_the_exact_cents(self, mail_module, mesh):
        """The balance rounds to whole dollars; the lifetime total must not."""
        orders = mail_module._orders_registry.get(mesh)
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 100})
        mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 1050})
        assert int(orders.get("total_paid_cents")) == 1050

    def test_a_payment_against_no_balance_is_harmless(self, mail_module, mesh):
        orders = mail_module._orders_registry.get(mesh)
        result = mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 2500})
        assert orders.get("paywall") == "0"
        assert result["cleared"] is True
        assert int(orders.get("total_paid_cents")) == 2500

    def test_the_movement_is_recorded_once_not_twice(self, mail_module, mesh):
        """The IMAP scanner writes its own 'payment' row before crediting, so
        _server_apply_order must not add a second one for the same money."""
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 100})
        before = len(mail_module._get_payment_ledger(mesh).entries)
        mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 4000})
        assert len(mail_module._get_payment_ledger(mesh).entries) == before


class TestTheCollarIsToldTheAnswer:
    def test_the_vault_blob_carries_the_servers_number(self, mail_module, mesh, monkeypatch):
        """Every device must apply the balance the relay computed rather than
        re-deriving it from whatever state it last managed to sync."""
        seen = {}

        def capture(action, params, mesh_id=None):
            seen["action"] = action
            seen["params"] = dict(params or {})

        monkeypatch.setattr(mail_module, "_admin_order_to_vault_blob", capture)
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 100})
        mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 4000})
        assert seen["action"] == "payment-received"
        assert seen["params"]["new_paywall"] == 60
        assert seen["params"]["clear_paywall"] is False

    def test_a_clearing_payment_says_so_in_the_blob(self, mail_module, mesh, monkeypatch):
        seen = {}
        monkeypatch.setattr(
            mail_module,
            "_admin_order_to_vault_blob",
            lambda action, params, mesh_id=None: seen.update(params or {}),
        )
        mail_module._server_apply_order(mesh, "add-paywall", {"amount": 40})
        mail_module._server_apply_order(mesh, "payment-received", {"amount_cents": 4000})
        assert seen["new_paywall"] == 0
        assert seen["clear_paywall"] is True

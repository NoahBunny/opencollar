"""Tests for the signup-wizard /api/mesh/create initial_config extension.

The wizard collects optional initial config (IMAP, tribute, subscription,
bedtime, screen-time) and posts it alongside lion_pubkey. The server applies
each present key via _server_apply_order, returning the list of applied
actions so the wizard can confirm what stuck.

This file pins the contract:

- Each recognized key produces the corresponding order
- Absent keys produce no orders
- Partial config is OK — invalid values for one key don't block others
- Orders are persisted into the new mesh's OrdersDocument, not the operator's
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_initcfg", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_initcfg"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mesh_id(mail_module):
    """Create a fresh per-test mesh in the registry and return its id."""
    import os
    import secrets

    mid = secrets.token_urlsafe(8).rstrip("=").replace("-", "_")[:12]
    mail_module._orders_registry.get_or_create(mid)
    yield mid
    # Best-effort cleanup
    docs = getattr(mail_module._orders_registry, "docs", {})
    docs.pop(mid, None)
    state_dir = getattr(mail_module, "_STATE_DIR", "")
    if state_dir:
        candidates = [
            os.path.join(state_dir, f"orders-{mid}.json"),
            os.path.join(state_dir, "orders", f"{mid}.json"),
        ]
        for p in candidates:
            try:
                os.remove(p)
            except FileNotFoundError:
                pass


# ──────────────────────── empty + non-dict ────────────────────────


class TestEmptyConfig:
    def test_empty_dict_returns_empty_list(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {}) == []

    def test_non_dict_returns_empty_list(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, None) == []
        assert mail_module._apply_initial_mesh_config(mesh_id, "not a dict") == []
        assert mail_module._apply_initial_mesh_config(mesh_id, 42) == []

    def test_unknown_keys_ignored(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"random_key": "value"}) == []


# ──────────────────────── set-payment-email ────────────────────────


class TestPaymentEmail:
    def test_all_three_imap_fields_required(self, mail_module, mesh_id):
        # Missing host
        assert mail_module._apply_initial_mesh_config(mesh_id, {"imap_user": "x@y.z", "imap_pass": "p"}) == []
        # Missing user
        assert mail_module._apply_initial_mesh_config(mesh_id, {"imap_host": "h", "imap_pass": "p"}) == []
        # Missing pass
        assert mail_module._apply_initial_mesh_config(mesh_id, {"imap_host": "h", "imap_user": "x@y.z"}) == []

    def test_all_three_present_applies(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(
            mesh_id,
            {"imap_host": "imap.test", "imap_user": "lion@test", "imap_pass": "secret"},
        )
        assert "set-payment-email" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert orders.get("payment_imap_host") == "imap.test"
        assert orders.get("payment_imap_user") == "lion@test"
        assert orders.get("payment_imap_pass") == "secret"

    def test_whitespace_stripped(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(
            mesh_id,
            {"imap_host": "  h  ", "imap_user": "  u  ", "imap_pass": "p"},
        )
        assert "set-payment-email" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert orders.get("payment_imap_host") == "h"
        assert orders.get("payment_imap_user") == "u"


# ──────────────────────── set-tribute ────────────────────────


class TestTribute:
    def test_zero_skipped(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"tribute_amount": 0}) == []

    def test_negative_skipped(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"tribute_amount": -5}) == []

    def test_positive_applies(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(mesh_id, {"tribute_amount": 3})
        assert "set-tribute" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert int(orders.get("tribute_amount", 0)) == 3
        assert int(orders.get("tribute_active", 0)) == 1

    def test_string_number_coerced(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(mesh_id, {"tribute_amount": "5"})
        assert "set-tribute" in applied

    def test_non_numeric_skipped(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"tribute_amount": "abc"}) == []


# ──────────────────────── subscribe ────────────────────────


class TestSubscribe:
    def test_unknown_tier_skipped(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"sub_tier": "platinum"}) == []

    def test_empty_skipped(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"sub_tier": ""}) == []

    def test_bronze(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(mesh_id, {"sub_tier": "bronze"})
        assert "subscribe" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert orders.get("sub_tier") == "bronze"
        assert int(orders.get("sub_due", 0)) > 0  # default now+7d

    def test_silver(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(mesh_id, {"sub_tier": "silver"})
        assert "subscribe" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert orders.get("sub_tier") == "silver"

    def test_gold_uppercase_normalized(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(mesh_id, {"sub_tier": "GOLD"})
        assert "subscribe" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert orders.get("sub_tier") == "gold"


# ──────────────────────── set-bedtime ────────────────────────


class TestBedtime:
    def test_both_hours_required(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"bedtime_lock_hour": 23}) == []
        assert mail_module._apply_initial_mesh_config(mesh_id, {"bedtime_unlock_hour": 7}) == []

    def test_out_of_range_skipped(self, mail_module, mesh_id):
        # 24 is invalid (must be 0..23)
        assert (
            mail_module._apply_initial_mesh_config(mesh_id, {"bedtime_lock_hour": 24, "bedtime_unlock_hour": 7}) == []
        )
        # negative
        assert (
            mail_module._apply_initial_mesh_config(mesh_id, {"bedtime_lock_hour": -1, "bedtime_unlock_hour": 7}) == []
        )

    def test_valid_hours_applies(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(mesh_id, {"bedtime_lock_hour": 23, "bedtime_unlock_hour": 7})
        assert "set-bedtime" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert int(orders.get("bedtime_enabled", 0)) == 1
        assert int(orders.get("bedtime_lock_hour", -1)) == 23
        assert int(orders.get("bedtime_unlock_hour", -1)) == 7


# ──────────────────────── set-screen-time ────────────────────────


class TestScreenTime:
    def test_zero_skipped(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"screen_time_quota_minutes": 0}) == []

    def test_negative_skipped(self, mail_module, mesh_id):
        assert mail_module._apply_initial_mesh_config(mesh_id, {"screen_time_quota_minutes": -10}) == []

    def test_positive_applies(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(mesh_id, {"screen_time_quota_minutes": 120})
        assert "set-screen-time" in applied
        orders = mail_module._orders_registry.get(mesh_id)
        assert int(orders.get("screen_time_quota_minutes", 0)) == 120


# ──────────────────────── combined / partial / order ────────────────────────


class TestCombined:
    def test_all_keys_at_once(self, mail_module, mesh_id):
        applied = mail_module._apply_initial_mesh_config(
            mesh_id,
            {
                "imap_host": "h",
                "imap_user": "u",
                "imap_pass": "p",
                "tribute_amount": 2,
                "sub_tier": "silver",
                "bedtime_lock_hour": 23,
                "bedtime_unlock_hour": 7,
                "screen_time_quota_minutes": 90,
            },
        )
        assert set(applied) == {
            "set-payment-email",
            "set-tribute",
            "subscribe",
            "set-bedtime",
            "set-screen-time",
        }

    def test_partial_config_each_key_independent(self, mail_module, mesh_id):
        # Invalid bedtime + valid tribute → only tribute applies
        applied = mail_module._apply_initial_mesh_config(
            mesh_id,
            {"bedtime_lock_hour": 99, "tribute_amount": 5},
        )
        assert applied == ["set-tribute"]

    def test_does_not_mutate_other_meshes(self, mail_module, mesh_id):
        # Create a second mesh; configure only the first; verify second is untouched
        other_id = "other_test_mesh"
        other_orders = mail_module._orders_registry.get_or_create(other_id)
        try:
            mail_module._apply_initial_mesh_config(mesh_id, {"tribute_amount": 7})
            assert int(other_orders.get("tribute_amount", 0)) == 0
            assert int(other_orders.get("tribute_active", 0)) == 0
        finally:
            mail_module._orders_registry.docs.pop(other_id, None)


# ──────────────────────── privacy isolation regression ────────────────────────
# Three adversarial audits (2026-06-25) found Lion's IMAP email+password leaked
# to the Bunny's apps via set-payment-email -> ORDER_KEYS -> gossip/vault. These
# pin the fix so it can't silently regress.


class TestPaymentEmailIsolation:
    def test_order_keys_has_no_payment_imap(self):
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("fl_mesh_iso", str(REPO_ROOT / "focuslock_mesh.py"))
        mesh_mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mesh_mod)
        for k in ("payment_imap_host", "payment_imap_user", "payment_imap_pass"):
            assert k not in mesh_mod.ORDER_KEYS, f"{k} must not be in ORDER_KEYS (leaks to Bunny)"

    def test_orders_doc_never_serializes_imap_creds(self):
        # Even a hostile remote doc carrying payment_imap_* must not be copied in.
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("fl_mesh_iso2", str(REPO_ROOT / "focuslock_mesh.py"))
        mesh_mod = _ilu.module_from_spec(spec)
        spec.loader.exec_module(mesh_mod)
        doc = mesh_mod.OrdersDocument()
        doc.apply_remote(
            {"version": 5, "orders": {"payment_imap_user": "lion@secret", "payment_imap_pass": "pw"}},
            "",  # no lion_pubkey → permissive path, still must not copy the keys
        )
        ser = doc.to_dict()["orders"]
        assert "payment_imap_user" not in ser
        assert "payment_imap_pass" not in ser
        assert "lion@secret" not in str(ser)

    def test_vault_blob_refuses_sensitive_actions(self, mail_module):
        # _admin_order_to_vault_blob must fail-closed on payment-cred orders.
        # Count vault appends; a refused order produces ZERO appends.
        mid = "iso_vault_test"
        mail_module._orders_registry.get_or_create(mid)
        try:
            calls = {"n": 0}
            orig = mail_module._vault_store.append
            mail_module._vault_store.append = lambda *a, **k: (calls.__setitem__("n", calls["n"] + 1), (1, None))[1]
            try:
                mail_module._admin_order_to_vault_blob(
                    "set-payment-email", {"imap_host": "h", "user": "lion@x", "pass": "p"}, mid
                )
                mail_module._admin_order_to_vault_blob(
                    "some-other-action", {"payee_email": "lion@x"}, mid
                )
            finally:
                mail_module._vault_store.append = orig
            assert calls["n"] == 0, "sensitive payment-cred orders must never be vault-broadcast"
        finally:
            mail_module._orders_registry.docs.pop(mid, None)

    def test_resolve_imap_ignores_vault_orders(self):
        # focuslock_payment._resolve_imap_creds must not read creds from the
        # shared orders/vault — only from the server-only PaymentIdentity.
        import importlib.util as _ilu

        spec = _ilu.spec_from_file_location("fl_pay_iso", str(REPO_ROOT / "shared" / "focuslock_payment.py"))
        pay = _ilu.module_from_spec(spec)
        spec.loader.exec_module(pay)

        class _FakeOrders:
            def get(self, k, default=""):
                return {"payment_imap_host": "evil", "payment_imap_user": "evil@x", "payment_imap_pass": "evil"}.get(
                    k, default
                )

        host, user, pwd = pay._resolve_imap_creds(_FakeOrders(), static_fallback=None, identity=None)
        assert (host, user, pwd) == ("", "", ""), "must not source creds from the shared orders/vault"


# ──────────────────────── server email support (account + evidence) ────────────────────────
# Per-mesh account email + evidence-recipient email, both server-only (Lion's
# own emails — never exposed to the Bunny's apps).


class TestServerEmailSupport:
    def test_evidence_email_persists_server_only(self, mail_module, mesh_id):
        ident = mail_module._get_payment_identity(mesh_id)
        summary = ident.set_evidence_email("lion-evidence@example.com")
        assert summary["evidence_configured"] is True
        assert ident.evidence_email == "lion-evidence@example.com"
        # Reload from disk → still there (and isolated to the server-only file).
        import focuslock_mesh as _fm

        reloaded = _fm.PaymentIdentity(persist_path=ident.persist_path)
        assert reloaded.evidence_email == "lion-evidence@example.com"

    def test_mesh_create_stores_account_email_and_pass_hash(self, mail_module):
        acct = mail_module._mesh_accounts.create(
            "LIONPUB", account_email="  lion@acct.com  ", account_pass_hash="deadbeef"
        )
        try:
            assert acct["account_email"] == "lion@acct.com"  # stripped
            assert acct["account_pass_hash"] == "deadbeef"
        finally:
            mail_module._mesh_accounts.meshes.pop(acct["mesh_id"], None)

    def test_send_evidence_prefers_per_mesh_email(self, mail_module, mesh_id, monkeypatch):
        mail_module._get_payment_identity(mesh_id).set_evidence_email("permesh@lion.com")
        captured = {}
        monkeypatch.setattr(
            mail_module, "_send_evidence_impl", lambda *a, **k: captured.update(k)
        )
        mail_module.send_evidence("compliment text", "compliment", mesh_id=mesh_id)
        assert captured.get("partner_email") == "permesh@lion.com"
        # Without a mesh_id → falls back to the operator PARTNER_EMAIL.
        captured.clear()
        mail_module.send_evidence("x", "compliment")
        assert captured.get("partner_email") == mail_module.PARTNER_EMAIL

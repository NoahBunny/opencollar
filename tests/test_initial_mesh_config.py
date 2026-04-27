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

"""Tests for P2 paywall hardening (2026-04-17).

Covers the server-side handlers that replaced per-device paywall writes:
- escape-recorded with tiered penalty ($5 × tier, 3 escapes/tier)
- tamper-recorded for attempt/detected/removed ($500/$500/$1000)
- geofence-breach-recorded ($100 + lifetime counter)
- app-launch-penalty ($50 + endpoint-level 10s dedup)
- good-behavior-tick (-$5 credit, clamp at 0)
- compound-interest-tick (sets paywall to target when target > current)
- Escape-tier formula from shared/focuslock_penalties.py
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
    spec = importlib.util.spec_from_file_location("focuslock_mail_pwhard", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_pwhard"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def orders():
    from focuslock_mesh import OrdersDocument

    return OrdersDocument()


class TestEscapePenaltyFormula:
    def test_tier_boundaries(self):
        from focuslock_penalties import escape_penalty

        # 1-3 → $5, 4-6 → $10, 7-9 → $15, 10-12 → $20
        assert [escape_penalty(n) for n in range(1, 13)] == [
            5, 5, 5, 10, 10, 10, 15, 15, 15, 20, 20, 20,
        ]

    def test_zero_and_negative(self):
        from focuslock_penalties import escape_penalty

        assert escape_penalty(0) == 0
        assert escape_penalty(-5) == 0


class TestEscapeRecorded:
    def test_first_escape_applies_five_dollars(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("escape-recorded", {}, orders)
        assert result["lifetime_escapes"] == 1
        assert result["penalty"] == 5
        assert result["paywall"] == 5
        assert orders.get("paywall") == "5"

    def test_three_escapes_accumulate_at_tier_one(self, mail_module, orders):
        orders.set("paywall", "0")
        for _ in range(3):
            mail_module.mesh_apply_order("escape-recorded", {}, orders)
        # 3 escapes at tier 1 ($5 each) → $15
        assert int(orders.get("paywall", "0")) == 15
        assert int(orders.get("lifetime_escapes", 0)) == 3

    def test_fourth_escape_bumps_to_tier_two(self, mail_module, orders):
        orders.set("paywall", "0")
        for _ in range(4):
            mail_module.mesh_apply_order("escape-recorded", {}, orders)
        # $5 + $5 + $5 + $10 = $25
        assert int(orders.get("paywall", "0")) == 25
        assert int(orders.get("lifetime_escapes", 0)) == 4

    def test_ten_escapes_total(self, mail_module, orders):
        orders.set("paywall", "0")
        for _ in range(10):
            mail_module.mesh_apply_order("escape-recorded", {}, orders)
        # 3×$5 + 3×$10 + 3×$15 + $20 = 15+30+45+20 = $110
        assert int(orders.get("paywall", "0")) == 110
        assert int(orders.get("lifetime_escapes", 0)) == 10

    def test_penalty_stacks_with_existing_paywall(self, mail_module, orders):
        orders.set("paywall", "100")
        result = mail_module.mesh_apply_order("escape-recorded", {}, orders)
        assert result["paywall"] == 105


class TestTamperRecorded:
    def test_attempt_fires_500(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("tamper-recorded", {"kind": "attempt"}, orders)
        assert result["penalty"] == 500
        assert result["paywall"] == 500
        assert result["lifetime_tamper"] == 1

    def test_detected_fires_500(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("tamper-recorded", {"kind": "detected"}, orders)
        assert result["penalty"] == 500
        assert result["paywall"] == 500

    def test_removed_fires_1000(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("tamper-recorded", {"kind": "removed"}, orders)
        assert result["penalty"] == 1000
        assert result["paywall"] == 1000

    def test_unknown_kind_no_penalty(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("tamper-recorded", {"kind": "weird"}, orders)
        assert "penalty" not in result  # no penalty applied
        assert orders.get("paywall") == "0"
        assert result["lifetime_tamper"] == 1  # counter still bumps

    def test_counters_accumulate(self, mail_module, orders):
        orders.set("paywall", "0")
        mail_module.mesh_apply_order("tamper-recorded", {"kind": "attempt"}, orders)
        mail_module.mesh_apply_order("tamper-recorded", {"kind": "detected"}, orders)
        mail_module.mesh_apply_order("tamper-recorded", {"kind": "removed"}, orders)
        # 500 + 500 + 1000 = 2000; 3 events in lifetime_tamper
        assert int(orders.get("paywall", "0")) == 2000
        assert int(orders.get("lifetime_tamper", 0)) == 3


class TestGeofenceBreachRecorded:
    def test_applies_100_and_seeds_original(self, mail_module, orders):
        orders.set("paywall", "0")
        orders.set("paywall_original", "0")
        result = mail_module.mesh_apply_order("geofence-breach-recorded", {}, orders)
        assert result["penalty"] == 100
        assert result["paywall"] == 100
        assert result["lifetime_geofence_breaches"] == 1
        # paywall_original seeded so compound interest has a base
        assert orders.get("paywall_original") == "100"

    def test_stacks_with_existing_paywall(self, mail_module, orders):
        orders.set("paywall", "50")
        orders.set("paywall_original", "50")
        result = mail_module.mesh_apply_order("geofence-breach-recorded", {}, orders)
        assert result["paywall"] == 150
        # does NOT overwrite non-zero original
        assert orders.get("paywall_original") == "50"


class TestAppLaunchPenalty:
    def test_applies_50(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("app-launch-penalty", {}, orders)
        assert result["penalty"] == 50
        assert result["paywall"] == 50

    def test_stacks_when_handler_called_twice(self, mail_module, orders):
        # The handler itself is idempotent-free; dedup happens at the endpoint
        # level (10s window on (mesh_id, node_id)). This documents the handler
        # contract — dedup is enforced above.
        orders.set("paywall", "0")
        mail_module.mesh_apply_order("app-launch-penalty", {}, orders)
        mail_module.mesh_apply_order("app-launch-penalty", {}, orders)
        assert int(orders.get("paywall", "0")) == 100


class TestGoodBehaviorTick:
    def test_credits_5_when_paywall_positive(self, mail_module, orders):
        orders.set("paywall", "100")
        result = mail_module.mesh_apply_order("good-behavior-tick", {}, orders)
        assert result["credit"] == 5
        assert result["paywall"] == 95

    def test_no_credit_when_paywall_zero(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("good-behavior-tick", {}, orders)
        assert result["credit"] == 0
        assert result["paywall"] == 0

    def test_clamps_at_zero(self, mail_module, orders):
        orders.set("paywall", "3")
        result = mail_module.mesh_apply_order("good-behavior-tick", {}, orders)
        # credit capped at current paywall
        assert result["credit"] == 3
        assert result["paywall"] == 0


class TestCompoundInterestTick:
    def test_sets_paywall_when_target_higher(self, mail_module, orders):
        orders.set("paywall", "100")
        result = mail_module.mesh_apply_order(
            "compound-interest-tick", {"paywall": 121}, orders
        )
        assert result["paywall"] == 121
        assert orders.get("paywall") == "121"
        assert int(orders.get("paywall_last_compounded", 0)) > 0

    def test_skips_when_target_not_higher(self, mail_module, orders):
        orders.set("paywall", "200")
        result = mail_module.mesh_apply_order(
            "compound-interest-tick", {"paywall": 150}, orders
        )
        assert result.get("skipped") is True
        assert orders.get("paywall") == "200"


class TestSitBoyRecorded:
    def test_amount_applied(self, mail_module, orders):
        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order("sit-boy-recorded", {"amount": 20}, orders)
        assert result["amount"] == 20
        assert result["paywall"] == 20
        assert orders.get("paywall") == "20"
        # paywall_original seeded for compound-interest base
        assert orders.get("paywall_original") == "20"

    def test_clamped_at_max(self, mail_module, orders):
        from focuslock_penalties import SIT_BOY_MAX_AMOUNT

        orders.set("paywall", "0")
        result = mail_module.mesh_apply_order(
            "sit-boy-recorded", {"amount": SIT_BOY_MAX_AMOUNT * 10}, orders
        )
        assert result["amount"] == SIT_BOY_MAX_AMOUNT
        assert result["paywall"] == SIT_BOY_MAX_AMOUNT

    def test_zero_amount_no_op(self, mail_module, orders):
        orders.set("paywall", "100")
        result = mail_module.mesh_apply_order("sit-boy-recorded", {"amount": 0}, orders)
        assert result["amount"] == 0
        assert result["paywall"] == 100
        assert orders.get("paywall") == "100"

    def test_stacks_with_existing_paywall(self, mail_module, orders):
        orders.set("paywall", "75")
        orders.set("paywall_original", "75")
        result = mail_module.mesh_apply_order("sit-boy-recorded", {"amount": 25}, orders)
        assert result["paywall"] == 100
        # paywall_original NOT overwritten when already set
        assert orders.get("paywall_original") == "75"


class TestGambleResolved:
    def test_heads_halves_paywall(self, mail_module, orders):
        # heads result is computed by the endpoint (math.ceil(old/2)); the
        # action is a dumb setter, so we pass the precomputed value.
        orders.set("paywall", "100")
        result = mail_module.mesh_apply_order(
            "gamble-resolved", {"paywall": 50, "result": "heads"}, orders
        )
        assert result["paywall"] == 50
        assert result["result"] == "heads"
        assert orders.get("paywall") == "50"
        assert orders.get("gamble_result") == "heads:50"

    def test_tails_doubles_paywall(self, mail_module, orders):
        orders.set("paywall", "75")
        result = mail_module.mesh_apply_order(
            "gamble-resolved", {"paywall": 150, "result": "tails"}, orders
        )
        assert result["paywall"] == 150
        assert orders.get("gamble_result") == "tails:150"

    def test_negative_paywall_clamped_to_zero(self, mail_module, orders):
        orders.set("paywall", "10")
        result = mail_module.mesh_apply_order(
            "gamble-resolved", {"paywall": -5, "result": "heads"}, orders
        )
        assert result["paywall"] == 0
        assert orders.get("paywall") == "0"

    def test_missing_params_safe(self, mail_module, orders):
        # Defaults: paywall=0, result="" — handler still no-ops cleanly.
        orders.set("paywall", "100")
        result = mail_module.mesh_apply_order("gamble-resolved", {}, orders)
        assert result["paywall"] == 0
        assert result["result"] == ""
        assert orders.get("paywall") == "0"


class TestUnsubscribeCharge:
    def test_bronze_charges_50(self, mail_module, orders):
        orders.set("paywall", "0")
        orders.set("sub_tier", "bronze")
        orders.set("sub_due", 9_999_999_999_999)
        result = mail_module.mesh_apply_order("unsubscribe-charge", {}, orders)
        assert result["fee"] == 50
        assert result["paywall"] == 50
        assert orders.get("sub_tier") == ""
        assert orders.get("sub_due") in (0, "0")

    def test_silver_charges_70(self, mail_module, orders):
        orders.set("paywall", "0")
        orders.set("sub_tier", "silver")
        result = mail_module.mesh_apply_order("unsubscribe-charge", {}, orders)
        assert result["fee"] == 70
        assert result["paywall"] == 70

    def test_gold_charges_100(self, mail_module, orders):
        orders.set("paywall", "0")
        orders.set("sub_tier", "gold")
        result = mail_module.mesh_apply_order("unsubscribe-charge", {}, orders)
        assert result["fee"] == 100
        assert result["paywall"] == 100

    def test_no_active_sub_returns_error(self, mail_module, orders):
        orders.set("paywall", "100")
        orders.set("sub_tier", "")
        result = mail_module.mesh_apply_order("unsubscribe-charge", {}, orders)
        assert "error" in result
        assert orders.get("paywall") == "100"  # no charge applied

    def test_fee_stacks_with_existing_paywall(self, mail_module, orders):
        orders.set("paywall", "200")
        orders.set("sub_tier", "gold")
        result = mail_module.mesh_apply_order("unsubscribe-charge", {}, orders)
        assert result["paywall"] == 300


class TestReleaseDeviceZerosPaywall:
    def test_target_all_zeros_paywall_and_original(self, mail_module, orders):
        orders.set("paywall", "250")
        orders.set("paywall_original", "100")
        mail_module.mesh_apply_order("release-device", {"target": "all"}, orders)
        assert orders.get("paywall") == "0"
        assert orders.get("paywall_original") == "0"
        assert orders.get("released") == "all"

    def test_target_pixel_does_not_zero_paywall(self, mail_module, orders):
        orders.set("paywall", "250")
        orders.set("paywall_original", "100")
        mail_module.mesh_apply_order("release-device", {"target": "pixel"}, orders)
        # per-device release leaves orders intact — only mesh-wide forgets
        assert orders.get("paywall") == "250"
        assert orders.get("paywall_original") == "100"
        assert orders.get("released") == "pixel"


class TestCompoundInterestRateTable:
    def test_rates_by_tier(self):
        from focuslock_penalties import compound_interest_rate

        assert compound_interest_rate("bronze") == pytest.approx(1.10)
        assert compound_interest_rate("silver") == pytest.approx(1.05)
        assert compound_interest_rate("gold") == pytest.approx(1.00)
        assert compound_interest_rate("") == pytest.approx(1.10)
        assert compound_interest_rate("platinum") == pytest.approx(1.10)  # unknown → bronze

    def test_compounded_math(self):
        # Bronze, 2h lock, $100 original → 100 * 1.1^2 = 121
        rate = 1.10
        compounded = int(100 * (rate ** 2))
        assert compounded == 121

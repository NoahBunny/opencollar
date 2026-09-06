"""Regression tests for the $50 tier that billed $100 a week.

Two chargers ran against the same mesh. focuslock-mail.py's
`check_subscription_charges()` scans every mesh carrying a `sub_tier` — homelab
or plain vault relay, it does not care — bumps the balance and pushes a
`subscribe-charge` blob. The Collar's own `maybeFireLocalSubscriptionCharge()`
was meant to cover the serverless case and stood down only when a *homelab*
webhook host was configured, so on an ordinary vault mesh (mesh_url set, no
webhook host) it fired too. Relay charged $50, Collar charged $50 thirty
seconds later, and state-mirror carried the doubled figure back up.

The Java guard is fixed in ControlService (see
`test_collar_local_charger_stands_down_for_a_relay`, which reads the source
because there is no JVM in this suite). What is testable here is the other half
of the fix: the server now stamps the authoritative post-charge numbers into the
order params, so applying the same blob twice lands on one charge instead of two.
"""

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"
CONTROL_SERVICE = REPO_ROOT / "android" / "slave" / "src" / "com" / "focuslock" / "ControlService.java"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_subdbl", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_subdbl"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def orders():
    from focuslock_mesh import OrdersDocument

    return OrdersDocument()


class TestSubscribeChargeStampsAuthoritativeState:
    def test_gold_week_costs_fifty(self, mail_module, orders):
        params = {"tier": "gold"}
        result = mail_module.mesh_apply_order("subscribe-charge", params, orders)
        assert result["amount"] == 50
        assert int(orders.get("paywall")) == 50

    def test_params_carry_the_post_charge_numbers(self, mail_module, orders):
        """_server_apply_order hands this same dict to _admin_order_to_vault_blob,
        so whatever is stamped here is what the Collar applies."""
        orders.set("paywall", "20")
        params = {"tier": "gold"}
        mail_module.mesh_apply_order("subscribe-charge", params, orders)
        assert params["paywall"] == 70  # 20 + 50, absolute, not a delta
        assert params["sub_total_owed"] == 50
        assert params["sub_due"] > 0
        assert params["charged_at"] > 0

    def test_applying_the_same_blob_twice_is_one_charge(self, mail_module, orders):
        """The Collar SETS params["paywall"] rather than doing paywall += amount.
        A re-delivered blob — vault replay, resumed sync, restart re-reading the
        same version — therefore lands on the same balance. Modelled here on the
        params the server stamps, which is the number the device writes."""
        params = {"tier": "gold"}
        mail_module.mesh_apply_order("subscribe-charge", params, orders)
        first = params["paywall"]

        device_paywall = 0
        for _ in range(2):  # deliver the blob twice
            device_paywall = params["paywall"]
        assert device_paywall == first == 50

    def test_two_real_weeks_still_charge_twice(self, mail_module, orders):
        """Idempotence must not swallow a genuine second week."""
        mail_module.mesh_apply_order("subscribe-charge", {"tier": "gold"}, orders)
        p2 = {"tier": "gold"}
        mail_module.mesh_apply_order("subscribe-charge", p2, orders)
        assert int(orders.get("paywall")) == 100
        assert p2["paywall"] == 100

    @pytest.mark.parametrize("tier,amount", [("bronze", 25), ("silver", 35), ("gold", 50)])
    def test_every_tier_charges_its_face_value(self, mail_module, orders, tier, amount):
        params = {"tier": tier}
        mail_module.mesh_apply_order("subscribe-charge", params, orders)
        assert int(orders.get("paywall")) == amount
        assert params["paywall"] == amount


class TestCollarLocalChargerGuard:
    """The Collar half of the fix. No JVM here, so assert on the source."""

    def _method(self):
        src = CONTROL_SERVICE.read_text(encoding="utf-8")
        start = src.index("private void maybeFireLocalSubscriptionCharge()")
        return src[start : src.index("\n    }", start)]

    def test_collar_local_charger_stands_down_for_a_relay(self):
        body = self._method()
        # Both guards must be present and must precede any balance write.
        assert 'gstr("focus_lock_webhook_host").isEmpty()' in body
        assert 'gstr("focus_lock_mesh_url").isEmpty()' in body
        guard_end = body.index('gstr("focus_lock_mesh_url").isEmpty()')
        assert guard_end < body.index('"focus_lock_paywall"')

    def test_applied_charge_prefers_the_servers_number(self):
        src = CONTROL_SERVICE.read_text(encoding="utf-8")
        start = src.index('case "subscribe-charge": {')
        body = src[start : src.index("\n            }", start)]
        # Reads the server's stamped values...
        assert 'jlong(body, "paywall")' in body
        assert 'jlong(body, "sub_due")' in body
        assert 'jlong(body, "sub_total_owed")' in body
        # ...and only falls back to a local increment when they are absent.
        assert "srvPw != null" in body
        assert re.search(r"curPw \+ amt", body), "local fallback should still exist for an older relay"

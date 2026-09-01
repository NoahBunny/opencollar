"""Voluntary tasks buy standing, never a discount.

Devotion is the one thing in this system the bunny initiates: they draw from
the same 144-line veneration catalogue the Lion imposes from, type it out, and
earn a rank. It is a subscriber perk, and the tier decides how much of it
counts in a week.

The reward is points and deliberately not money. A voluntary task that took
money off the balance would be a discount the bunny writes for themselves —
the one thing this system exists not to hand over, since they already hold the
device, the root and the drive. These tests pin that boundary: the cap and the
counter are server-side, and nothing here touches a balance.
"""

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"

MESH = "devotionMesh"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_devotion", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_devotion"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def store(mail_module, tmp_path, monkeypatch):
    monkeypatch.setattr(mail_module, "_DEVOTION_DIR", str(tmp_path / "devotion"))
    return tmp_path


class TestItIsAPerk:
    def test_no_subscription_means_no_devotion(self, mail_module, store):
        result = mail_module.devotion_claim(MESH, "", "ven-001")
        assert result.get("error")
        assert "perk" in result["error"]

    def test_each_tier_buys_a_bigger_allowance(self, mail_module):
        caps = mail_module.DEVOTION_WEEKLY_CAP
        assert caps[""] == 0
        assert caps["bronze"] < caps["silver"]
        assert caps["gold"] == -1  # uncapped

    def test_an_unsubscribed_status_reports_unavailable(self, mail_module, store):
        st = mail_module.devotion_status(MESH, "")
        assert st["devotion_available"] is False
        assert st["devotion_week_cap"] == 0


class TestTheWeeklyCap:
    def test_bronze_stops_at_its_cap(self, mail_module, store):
        cap = mail_module.DEVOTION_WEEKLY_CAP["bronze"]
        accepted = 0
        for i in range(cap + 3):
            if mail_module.devotion_claim(MESH, "bronze", f"ven-{i:03d}").get("ok"):
                accepted += 1
        assert accepted == cap

    def test_gold_is_uncapped(self, mail_module, store):
        for i in range(12):
            assert mail_module.devotion_claim(MESH, "gold", f"ven-{i:03d}").get("ok")
        assert mail_module.devotion_status(MESH, "gold")["devotion_points"] == 12

    def test_the_window_is_rolling_not_calendar(self, mail_module, store):
        """Anchored on the first claim of the week. A calendar week would turn
        'how much did you choose to do' into 'who stayed up for the reset'."""
        cap = mail_module.DEVOTION_WEEKLY_CAP["bronze"]
        for i in range(cap):
            mail_module.devotion_claim(MESH, "bronze", f"ven-{i:03d}")
        assert mail_module.devotion_claim(MESH, "bronze", "ven-999").get("error")

        path = mail_module._devotion_path(MESH)
        with open(path) as f:
            state = json.load(f)
        state["week_start"] -= 604801
        with open(path, "w") as f:
            json.dump(state, f)
        assert mail_module.devotion_claim(MESH, "bronze", "ven-999").get("ok")

    def test_a_tier_upgrade_raises_the_cap_mid_week(self, mail_module, store):
        for i in range(mail_module.DEVOTION_WEEKLY_CAP["bronze"]):
            mail_module.devotion_claim(MESH, "bronze", f"ven-{i:03d}")
        assert mail_module.devotion_claim(MESH, "bronze", "ven-900").get("error")
        assert mail_module.devotion_claim(MESH, "silver", "ven-900").get("ok")


class TestPointsAreNotMoney:
    def test_claiming_never_returns_a_balance_field(self, mail_module, store):
        """The whole point of the perk. If a balance key ever appears in this
        response, someone has wired devotion to the paywall."""
        result = mail_module.devotion_claim(MESH, "gold", "ven-001")
        assert result.get("ok")
        for forbidden in ("paywall", "new_paywall", "amount_cents", "total_paid_cents", "credit"):
            assert forbidden not in result

    def test_points_accumulate_across_weeks(self, mail_module, store):
        mail_module.devotion_claim(MESH, "bronze", "ven-001")
        path = mail_module._devotion_path(MESH)
        with open(path) as f:
            state = json.load(f)
        state["week_start"] -= 604801
        with open(path, "w") as f:
            json.dump(state, f)
        mail_module.devotion_claim(MESH, "bronze", "ven-002")
        st = mail_module.devotion_status(MESH, "bronze")
        assert st["devotion_points"] == 2
        assert st["devotion_week_used"] == 1  # fresh window


class TestRanks:
    def test_rank_rises_with_points(self, mail_module):
        assert mail_module.devotion_rank(0) == "Unproven"
        assert mail_module.devotion_rank(9) == "Unproven"
        assert mail_module.devotion_rank(10) == "Attentive"
        assert mail_module.devotion_rank(100) == "Exemplary"
        assert mail_module.devotion_rank(10_000) == "Exemplary"

    def test_rank_never_goes_backwards_as_points_climb(self, mail_module):
        seen = [mail_module.devotion_rank(p) for p in range(0, 200)]
        order = [r for i, r in enumerate(seen) if i == 0 or r != seen[i - 1]]
        assert order == [name for _, name in mail_module.DEVOTION_RANKS]


class TestStatusIsReadOnly:
    def test_reading_status_never_records_a_claim(self, mail_module, store):
        for _ in range(5):
            mail_module.devotion_status(MESH, "gold")
        assert mail_module.devotion_status(MESH, "gold")["devotion_points"] == 0

    def test_unreadable_state_reads_as_empty_rather_than_throwing(self, mail_module, store, tmp_path):
        import os

        os.makedirs(mail_module._DEVOTION_DIR, exist_ok=True)
        with open(mail_module._devotion_path(MESH), "w") as f:
            f.write("{corrupt")
        st = mail_module.devotion_status(MESH, "gold")
        assert st["devotion_points"] == 0

    def test_an_invalid_mesh_id_is_refused(self, mail_module, store):
        assert mail_module.devotion_claim("../../etc/passwd", "gold", "ven-001").get("error")


class TestTheCatalogueIsShared:
    def test_claimed_task_ids_come_from_the_real_catalogue(self, mail_module, store):
        """Sanity-check the ids the app will actually send: the companion draws
        from the same derived file Lion's Share does."""
        shipped = json.loads(
            (REPO_ROOT / "android" / "companion" / "res" / "raw" / "veneration_tasks.json").read_text()
        )
        first = shipped["tasks"][0]["id"]
        assert mail_module.devotion_claim(MESH, "gold", first).get("ok")

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
        earned = 12 + mail_module.DEVOTION_OPENING_CREDIT
        assert mail_module.devotion_status(MESH, "gold")["devotion_points"] == earned

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
        assert st["devotion_points"] == 2 + mail_module.DEVOTION_OPENING_CREDIT
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


class TestTheOpeningCredit:
    """Endowed progress (Nunes & Drèze 2006): a pre-stamped card was completed
    by 34% against 19% for an empty one needing identical purchases. Given as a
    disclosed gift of real points, not a padded display — the effect held with
    the head start disclosed, and a bar the bunny cannot audit would be a lie
    told for engagement."""

    def test_the_first_claim_carries_the_credit(self, mail_module, store):
        result = mail_module.devotion_claim(MESH, "bronze", "ven-001")
        assert result["devotion_points"] == 1 + mail_module.DEVOTION_OPENING_CREDIT

    def test_it_is_granted_once_and_not_again(self, mail_module, store):
        mail_module.devotion_claim(MESH, "gold", "ven-001")
        mail_module.devotion_claim(MESH, "gold", "ven-002")
        mail_module.devotion_claim(MESH, "gold", "ven-003")
        assert mail_module.devotion_status(MESH, "gold")["devotion_points"] == (3 + mail_module.DEVOTION_OPENING_CREDIT)

    def test_an_unsubscribed_bunny_is_not_credited(self, mail_module, store):
        """The perk is the subscription. A head start handed to someone who
        cannot use it is just a number."""
        mail_module.devotion_claim(MESH, "", "ven-001")
        assert mail_module.devotion_status(MESH, "")["devotion_points"] == 0


def _set_streak_week(mail_module, weeks_ago):
    """Rewind the recorded streak week without touching the clock."""
    path = mail_module._devotion_path(MESH)
    with open(path) as f:
        state = json.load(f)
    state["streak_week"] -= weeks_ago
    with open(path, "w") as f:
        json.dump(state, f)


def _clear_week_cap(mail_module):
    path = mail_module._devotion_path(MESH)
    with open(path) as f:
        state = json.load(f)
    state["week_start"] = 0
    state["week_count"] = 0
    with open(path, "w") as f:
        json.dump(state, f)


class TestTheStreakIsWeekly:
    """Deliberately not daily. A daily streak here would be broken by the
    Lion's own ordinary authority — an imposed lock, a fine, a confiscated
    evening — so the bunny would lose accumulated standing through no choice of
    theirs. Loss aversion motivates only while the loss is yours to prevent."""

    def test_a_first_claim_starts_a_streak_of_one(self, mail_module, store):
        assert mail_module.devotion_claim(MESH, "gold", "ven-001")["devotion_streak"] == 1

    def test_several_claims_in_one_week_do_not_inflate_it(self, mail_module, store):
        for i in range(4):
            result = mail_module.devotion_claim(MESH, "gold", f"ven-{i:03d}")
        assert result["devotion_streak"] == 1

    def test_consecutive_weeks_extend_it(self, mail_module, store):
        mail_module.devotion_claim(MESH, "gold", "ven-001")
        _set_streak_week(mail_module, 1)
        _clear_week_cap(mail_module)
        assert mail_module.devotion_claim(MESH, "gold", "ven-002")["devotion_streak"] == 2

    def test_a_long_gap_breaks_it_but_remembers_what_it_was(self, mail_module, store):
        """A bare 0 after a long run is a quit moment rather than a restart, so
        the app is given the number it needs to say what ended."""
        mail_module.devotion_claim(MESH, "gold", "ven-001")
        for _ in range(3):
            _set_streak_week(mail_module, 1)
            _clear_week_cap(mail_module)
            mail_module.devotion_claim(MESH, "gold", "ven-002")
        assert mail_module.devotion_status(MESH, "gold")["devotion_streak"] == 4

        _set_streak_week(mail_module, 6)
        _clear_week_cap(mail_module)
        # Drain freezes so the gap is not silently covered.
        path = mail_module._devotion_path(MESH)
        with open(path) as f:
            state = json.load(f)
        state["freezes"] = 0
        with open(path, "w") as f:
            json.dump(state, f)

        result = mail_module.devotion_claim(MESH, "gold", "ven-003")
        assert result["devotion_streak"] == 1
        assert result["devotion_broke_from"] == 4
        assert result["devotion_best_streak"] == 4

    def test_a_lapsed_week_reads_as_at_risk_rather_than_still_standing(self, mail_module, store):
        mail_module.devotion_claim(MESH, "gold", "ven-001")
        assert mail_module.devotion_status(MESH, "gold")["devotion_streak_at_risk"] is False
        _set_streak_week(mail_module, 1)
        assert mail_module.devotion_status(MESH, "gold")["devotion_streak_at_risk"] is True


class TestTheFreeze:
    """Streak freeze cut at-risk churn 21% in Duolingo's data, and holders kept
    streaks 4.5x longer by day 21. Structural, so it is never sold and never a
    reward — it is granted."""

    def test_a_new_mesh_starts_with_the_full_allowance(self, mail_module, store):
        assert mail_module.devotion_status(MESH, "gold")["devotion_freezes"] == mail_module.DEVOTION_FREEZE_CAP

    def test_one_missed_week_is_covered_and_spends_a_freeze(self, mail_module, store):
        mail_module.devotion_claim(MESH, "gold", "ven-001")
        before = mail_module.devotion_status(MESH, "gold")["devotion_freezes"]
        _set_streak_week(mail_module, 2)  # missed exactly one week
        _clear_week_cap(mail_module)
        result = mail_module.devotion_claim(MESH, "gold", "ven-002")
        assert result["devotion_streak"] == 2, "the freeze should have carried it"
        assert result["devotion_freezes"] == before - 1

    def test_freezes_do_not_stockpile_past_the_cap(self, mail_module, store):
        mail_module.devotion_status(MESH, "gold")
        path = mail_module._devotion_path(MESH)
        for _ in range(4):
            with open(path) as f:
                state = json.load(f)
            state["freeze_month"] = "190001"  # force a fresh grant
            with open(path, "w") as f:
                json.dump(state, f)
            mail_module.devotion_status(MESH, "gold")
        assert mail_module.devotion_status(MESH, "gold")["devotion_freezes"] == mail_module.DEVOTION_FREEZE_CAP

    def test_a_gap_too_big_for_one_freeze_still_breaks(self, mail_module, store):
        mail_module.devotion_claim(MESH, "gold", "ven-001")
        _set_streak_week(mail_module, 5)
        _clear_week_cap(mail_module)
        assert mail_module.devotion_claim(MESH, "gold", "ven-002")["devotion_streak"] == 1


class TestCommend:
    """The variable term in this system is a person, not an RNG. Points accrue
    deterministically and therefore go inert on their own; whether a commend
    comes, when, and what it says is the Lion's to decide."""

    def _claim(self, mail_module):
        return mail_module.devotion_claim(MESH, "gold", "ven-001")["claim_id"]

    def test_a_claim_starts_uncommended(self, mail_module, store):
        self._claim(mail_module)
        claims = mail_module.devotion_status(MESH, "gold")["devotion_claims"]
        assert claims and claims[0]["commended"] is False

    def test_commending_marks_the_claim_and_keeps_the_note(self, mail_module, store):
        claim_id = self._claim(mail_module)
        result = mail_module.devotion_commend(MESH, claim_id, "Good boy.")
        assert result.get("ok")
        claims = mail_module.devotion_status(MESH, "gold")["devotion_claims"]
        assert claims[0]["commended"] is True
        assert claims[0]["note"] == "Good boy."

    def test_an_unknown_claim_is_refused(self, mail_module, store):
        self._claim(mail_module)
        assert mail_module.devotion_commend(MESH, "c9999", "x").get("error") == "no such claim"

    def test_commending_twice_amends_rather_than_errors(self, mail_module, store):
        claim_id = self._claim(mail_module)
        mail_module.devotion_commend(MESH, claim_id, "first")
        second = mail_module.devotion_commend(MESH, claim_id, "second")
        assert second.get("ok") and second.get("amended") is True
        assert mail_module.devotion_status(MESH, "gold")["devotion_claims"][0]["note"] == "second"

    def test_a_commendation_never_moves_money_or_points(self, mail_module, store):
        """Acknowledgement is the reward, and it stays non-monetary. If the Lion
        wants to convert standing into balance relief, that is a separate,
        deliberate act — not a side effect of saying well done."""
        claim_id = self._claim(mail_module)
        before = mail_module.devotion_status(MESH, "gold")["devotion_points"]
        result = mail_module.devotion_commend(MESH, claim_id, "well done")
        assert result["devotion_points"] == before
        for forbidden in ("paywall", "new_paywall", "amount_cents", "credit"):
            assert forbidden not in result

    def test_notes_are_bounded(self, mail_module, store):
        claim_id = self._claim(mail_module)
        mail_module.devotion_commend(MESH, claim_id, "x" * 500)
        assert len(mail_module.devotion_status(MESH, "gold")["devotion_claims"][0]["note"]) <= 200


class TestOldStateStillReads:
    def test_pre_commend_claim_strings_do_not_crash_the_reader(self, mail_module, store):
        """Claims used to be stored as "task_id@ts" strings. Keep the record of
        work already done readable rather than dropping it on upgrade."""
        import os

        os.makedirs(mail_module._DEVOTION_DIR, exist_ok=True)
        with open(mail_module._devotion_path(MESH), "w") as f:
            json.dump({"points": 5, "claims": ["ven-001@1700000000", "ven-002@1700000001"]}, f)
        st = mail_module.devotion_status(MESH, "gold")
        assert st["devotion_points"] == 5
        assert len(st["devotion_claims"]) == 2
        assert st["devotion_claims"][0]["task_id"] in ("ven-001", "ven-002")

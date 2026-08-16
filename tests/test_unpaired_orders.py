"""Interim marching orders for a collared-but-unpaired desktop.

A collar with no Lion on the other end used to leave Claude Code sessions on
that machine completely un-collared — nothing to distinguish it from a bare PC,
so pairing slid to "later". `shared/focuslock_unpaired_orders.py` supplies the
floor that applies immediately: the Lion/bunny frame plus a standing push to
pair, escalating the longer the machine goes unclaimed.

What these tests pin is mostly boundaries and safety properties, not prose:
the overlay must be recognisable (so it never clobbers a bunny's own
CLAUDE.md), must escalate, must not claim enforcement powers the unpaired
collar does not have, and must not pretend to speak the Lion's real orders.
"""

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MOD_PATH = REPO_ROOT / "shared" / "focuslock_unpaired_orders.py"


@pytest.fixture(scope="module")
def uo():
    spec = importlib.util.spec_from_file_location("focuslock_unpaired_orders_t", str(MOD_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_unpaired_orders_t"] = mod
    spec.loader.exec_module(mod)
    return mod


class TestOverlayIdentity:
    def test_carries_a_marker_the_collar_can_recognise(self, uo):
        doc = uo.unpaired_orders()
        assert uo.MARKER in doc
        assert uo.is_our_overlay(doc)

    def test_a_bunnys_own_claude_md_is_not_mistaken_for_ours(self, uo):
        assert not uo.is_our_overlay("# My project notes\n\nRun the tests with pytest.")
        assert not uo.is_our_overlay("")
        assert not uo.is_our_overlay(None)

    def test_marker_match_survives_a_version_bump(self, uo):
        """is_our_overlay matches the family, not the exact version — a collar
        must still recognise (and replace) an overlay an older build wrote."""
        assert uo.is_our_overlay("<!-- focuslock:unpaired-orders v0 -->\n# older")


class TestEscalation:
    @pytest.mark.parametrize(
        "hours,expected",
        [
            (0, "settling-in"),
            (5.9, "settling-in"),
            (6, "insistent"),
            (23, "insistent"),
            (24, "pointed"),
            (71, "pointed"),
            (72, "unignorable"),
            (24 * 30, "unignorable"),
        ],
    )
    def test_tier_boundaries(self, uo, hours, expected):
        assert uo.nudge_tier(hours) == expected

    def test_pressure_grows_with_time(self, uo):
        """The fresh install asks once a session; the week-old one leads every
        response. If that ordering ever inverts, the nudge is broken."""
        fresh = uo.unpaired_orders(hours_unpaired=0.5)
        stale = uo.unpaired_orders(hours_unpaired=24 * 7)
        assert "first response of every session" in fresh
        assert "EVERY response" in stale
        assert len(stale) > len(fresh) / 2  # sanity: the escalated copy is real content

    def test_elapsed_time_is_named_once_it_matters(self, uo):
        assert "less than an hour" in uo.unpaired_orders(hours_unpaired=0.2)
        assert "about 5 hours" in uo.unpaired_orders(hours_unpaired=5.4)
        assert "about 1 hour" in uo.unpaired_orders(hours_unpaired=1.2)
        assert "about 3 days" in uo.unpaired_orders(hours_unpaired=24 * 3 + 2)


class TestContentContract:
    def test_states_it_is_not_the_lions_real_orders(self, uo):
        doc = uo.unpaired_orders()
        assert "not** the Lion's standing orders" in doc
        assert "this overlay speaks for the collar, never for the Lion" in doc

    def test_claims_no_enforcement_and_preserves_the_consent_floor(self, uo):
        """An unpaired collar has consented to nothing yet — the overlay must
        not imply otherwise, and must point at the Terms of Surrender."""
        doc = uo.unpaired_orders()
        assert "It enforces nothing." in doc
        assert "Terms of Surrender" in doc
        assert "backed up" in doc
        # No punishment/withholding directives — this is a nudge, not a lock.
        low = doc.lower()
        assert "you do not punish, threaten, bill, restrict, or withhold work" in low
        for tier_hours in (0.5, 12, 48, 24 * 7):
            assert "You are pressing, not withholding." in uo.unpaired_orders(hours_unpaired=tier_hours)

    def test_join_hint_and_hostname_are_carried_through(self, uo):
        doc = uo.unpaired_orders(hostname="charizard-garuda", join_hint="1. Do the thing.")
        assert "charizard-garuda" in doc
        assert "1. Do the thing." in doc

    def test_registered_but_unclaimed_asks_for_confirmation_instead(self, uo):
        """mesh_id set + no Lion key is a different problem from no mesh at all:
        the bunny has done their half and the Lion has to confirm the node."""
        no_mesh = uo.unpaired_orders(mesh_configured=False)
        joined = uo.unpaired_orders(mesh_configured=True)
        assert "belongs to no mesh" in no_mesh
        assert "Get this machine onto the Lion's mesh." in no_mesh
        assert "has not claimed it yet" in joined
        assert "Vault Nodes" in joined

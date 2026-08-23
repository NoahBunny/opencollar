"""Offline spec for the adb UI driver's parsing half.

The driver's whole job is to turn a `uiautomator dump` into "where do I tap",
and getting that wrong is worse than it failing outright: a mismatched needle
taps a real, wrong control. On a controller app whose buttons include Lock,
Unlock All, Clear and Entrap, a driver that grabs the wrong node does damage
rather than reporting a failure.

Everything here runs without a device — that is the point of splitting the
parsing out of `uiauto.UiDevice`.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "staging"))

from uiauto import escape_input_text, match, parse_nodes

# A cut-down dump shaped like Lion's Share's Lock tab: a "Lock" tab button and
# a "Lock all devices" primary, which is precisely the ambiguity that makes
# exact-match-first matter.
DUMP = """<?xml version='1.0' encoding='UTF-8'?>
<hierarchy rotation="0">
  <node index="0" text="" resource-id="" class="android.widget.FrameLayout" bounds="[0,0][1080,2400]">
    <node index="1" text="Lock" resource-id="com.focusctl:id/tab_lock" class="android.widget.Button" enabled="true" bounds="[20,300][280,336]" />
    <node index="2" text="Rules" resource-id="com.focusctl:id/tab_rules" class="android.widget.Button" enabled="true" bounds="[284,300][544,336]" />
    <node index="3" text="Lock all devices" resource-id="com.focusctl:id/btn_lock" class="android.widget.Button" enabled="true" bounds="[20,900][1060,960]" />
    <node index="4" text="" resource-id="com.focusctl:id/message_input" class="android.widget.EditText" enabled="true" bounds="[20,500][1060,550]" />
    <node index="5" text="Taunt" resource-id="com.focusctl:id/toggle_shame" class="android.widget.ToggleButton" checked="true" enabled="true" bounds="[20,600][350,642]" />
    <node index="6" text="Mute" resource-id="com.focusctl:id/toggle_mute" class="android.widget.ToggleButton" checked="false" enabled="true" bounds="[360,600][690,642]" />
  </node>
</hierarchy>"""


@pytest.fixture
def nodes():
    return parse_nodes(DUMP)


class TestParsing:
    def test_reads_every_node_that_has_bounds(self, nodes):
        # The root FrameLayout has bounds too, so 7 nodes, not 6.
        assert len(nodes) == 7

    def test_computes_the_centre_point_to_tap(self, nodes):
        lock = match(nodes, "id/btn_lock")
        assert (lock["cx"], lock["cy"]) == (540, 930)

    def test_carries_toggle_state(self, nodes):
        assert match(nodes, "id/toggle_shame")["checked"] is True
        assert match(nodes, "id/toggle_mute")["checked"] is False

    def test_a_node_without_bounds_is_skipped_rather_than_crashing(self):
        assert parse_nodes('<node text="orphan" />') == []

    def test_empty_or_garbage_input_yields_nothing(self):
        assert parse_nodes("") == []
        assert parse_nodes("not xml at all") == []


class TestMatching:
    def test_an_exact_text_match_beats_a_substring_hit(self, nodes):
        """The one that matters. "Lock" must find the TAB, not the primary
        button — tapping the wrong one here locks the bunny during a test that
        meant to switch tabs."""
        assert match(nodes, "Lock")["id"].endswith("tab_lock")

    def test_substring_still_works_when_nothing_matches_exactly(self, nodes):
        assert match(nodes, "all devices")["id"].endswith("btn_lock")

    def test_a_needle_with_a_slash_matches_ids_only(self, nodes):
        """Guards the other direction: an id-shaped needle must never land on a
        label that happens to contain the same characters."""
        assert match(nodes, "id/tab_lock")["text"] == "Lock"
        assert match(nodes, "id/nonexistent") is None

    def test_matching_is_case_insensitive(self, nodes):
        assert match(nodes, "LOCK ALL DEVICES")["id"].endswith("btn_lock")

    def test_a_miss_is_none_rather_than_a_wrong_tap(self, nodes):
        assert match(nodes, "Release Forever") is None
        assert match([], "anything") is None
        assert match(nodes, "") is None


class TestInputEscaping:
    def test_spaces_become_the_percent_s_adb_expects(self):
        assert escape_input_text("hello world") == "hello%sworld"

    def test_shell_metacharacters_are_escaped(self):
        # `input text` goes through a shell on the device; an unescaped
        # quote or ampersand truncates the string or forks a command.
        for ch in "()<>|;&*":
            assert "\\" + ch in escape_input_text(f"a{ch}b")

    def test_a_literal_percent_survives(self):
        assert escape_input_text("100%") == "100%%"

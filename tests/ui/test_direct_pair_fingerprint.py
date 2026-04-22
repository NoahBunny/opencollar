# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""§1a direct-pair fingerprint pin regression test (Audit C5).

Three assertions cover the fingerprint-verify gate in
MainActivity.pairDirect:

  1. correct fp → PAIRED direct (fingerprint verified): <url>
  2. wrong fp   → Pair ABORTED: fingerprint mismatch. …
                  AND bunny's focus_lock_lion_pubkey is still empty
  3. blank fp   → PAIRED direct (UNVERIFIED fp=<fp>): <url>
                  (the transient "VERIFY" message is overwritten —
                  poll on the terminal regex only)

If a regression deletes the fingerprint check, test #2 must go red BOTH
on the status-text assertion AND on the negative Settings.Global oracle.
Either failure alone is a test bug worth investigating; neither is a
real C5 regression.
"""

from __future__ import annotations

import pytest

from tests.ui.conftest import wait_for_status
from tests.ui.pages import MainPage

BUNNY_URL = "http://127.0.0.1:8432"  # emulator-local — all three APKs are on the same AVD


@pytest.fixture
def pair_dialog(controller):
    return MainPage(controller).open_setup().tap_pair_direct()


def test_direct_pair_happy_path(controller, pair_dialog, bunny_fingerprint, lion_pubkey_on_bunny):
    assert lion_pubkey_on_bunny == "", "pre-condition: bunny should have no lion_pubkey yet"
    pair_dialog.set_ip("127.0.0.1").set_port("8432").set_fingerprint(bunny_fingerprint).tap_pair()
    status = wait_for_status(controller, r"^PAIRED direct \(fingerprint verified\): " + BUNNY_URL + r"$")
    assert bunny_fingerprint not in status  # verified path does NOT echo fp into status


def test_direct_pair_fingerprint_mismatch(controller, pair_dialog, bunny_fingerprint, adb_device):
    wrong_fp = "deadbeefdeadbeef"
    assert wrong_fp != bunny_fingerprint, "fixture sanity: bunny's real fp must not be deadbeef"
    pair_dialog.set_ip("127.0.0.1").set_port("8432").set_fingerprint(wrong_fp).tap_pair()
    status = wait_for_status(
        controller,
        r"^Pair ABORTED: fingerprint mismatch\. expected=" + wrong_fp + r" got=" + bunny_fingerprint,
    )
    assert "Possible MITM" in status
    # Negative oracle: the bunny must not have stored our pubkey on the reject.
    from tests.ui.conftest import LION_PUBKEY_KEY, _adb

    out = _adb("-s", adb_device, "shell", "settings", "get", "global", LION_PUBKEY_KEY).stdout.strip()
    assert out in ("", "null"), f"C5 regression: bunny stored lion_pubkey despite mismatch. got={out!r}"


def test_direct_pair_blank_fingerprint(controller, pair_dialog, bunny_fingerprint):
    pair_dialog.set_ip("127.0.0.1").set_port("8432").set_fingerprint("").tap_pair()
    # Poll on the terminal state only; the transient
    # "PAIRED without fingerprint check — VERIFY …" string is overwritten
    # by the final status write at MainActivity.java:2309.
    status = wait_for_status(
        controller,
        r"^PAIRED direct \(UNVERIFIED fp=" + bunny_fingerprint + r"\): " + BUNNY_URL + r"$",
    )
    assert "UNVERIFIED" in status

"""The coin flip has to be bounded, not just tilted.

Heads halves the balance, tails doubles it — one flip is +25% EV for the
Lion, which is why the bet is safe to offer. Unlimited flips are a different
game: the bunny is not playing for the average, they are buying tickets
against a balance that only has to reach zero once, and enough attempts
clears any balance. That turns a Lion-favourable bet into an escape hatch.

Both bounds therefore live on the relay. These tests pin that they are
consumed, persisted, and — the part that matters when the state file is on a
machine somebody wants to delete things from — that they fail CLOSED.
"""

import importlib.util
import json
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_gamble", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_gamble"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def limits_dir(mail_module, tmp_path, monkeypatch):
    d = tmp_path / "gamble_limits"
    monkeypatch.setattr(mail_module, "_GAMBLE_LIMITS_DIR", str(d))
    return d


MESH = "gambleMesh01"


def _age_last_flip(mail_module, seconds):
    """Rewind the recorded flip time, so elapsed-time behaviour can be tested
    without touching the clock."""
    path = mail_module._gamble_state_path(MESH)
    with open(path) as f:
        state = json.load(f)
    state["last_ms"] -= seconds * 1000
    with open(path, "w") as f:
        json.dump(state, f)


class TestTheFlipIsConsumed:
    def test_the_first_flip_is_allowed(self, mail_module, limits_dir):
        assert mail_module._gamble_check_and_record(MESH)["ok"] is True

    def test_a_second_flip_straight_after_is_refused(self, mail_module, limits_dir):
        mail_module._gamble_check_and_record(MESH)
        second = mail_module._gamble_check_and_record(MESH)
        assert second["ok"] is False
        assert "cool" in second["error"].lower()
        assert second["retry_after"] > 0

    def test_the_cooldown_expires(self, mail_module, limits_dir):
        mail_module._gamble_check_and_record(MESH)
        _age_last_flip(mail_module, mail_module.GAMBLE_COOLDOWN_S + 1)
        assert mail_module._gamble_check_and_record(MESH)["ok"] is True

    def test_the_daily_cap_holds_once_the_cooldown_stops_helping(self, mail_module, limits_dir):
        """Waiting out the cooldown must not be an unlimited supply."""
        allowed = 0
        for _ in range(mail_module.GAMBLE_MAX_PER_DAY + 3):
            if mail_module._gamble_check_and_record(MESH)["ok"]:
                allowed += 1
                _age_last_flip(mail_module, mail_module.GAMBLE_COOLDOWN_S + 1)
        assert allowed == mail_module.GAMBLE_MAX_PER_DAY

    def test_the_window_is_rolling_not_calendar(self, mail_module, limits_dir):
        """Anchored on the first flip of the window. A calendar day would hand
        a fresh allowance to anyone willing to wait up for midnight."""
        for _ in range(mail_module.GAMBLE_MAX_PER_DAY):
            mail_module._gamble_check_and_record(MESH)
            _age_last_flip(mail_module, mail_module.GAMBLE_COOLDOWN_S + 1)
        assert mail_module._gamble_check_and_record(MESH)["ok"] is False

        path = mail_module._gamble_state_path(MESH)
        with open(path) as f:
            state = json.load(f)
        state["day_start"] -= 86401
        with open(path, "w") as f:
            json.dump(state, f)
        assert mail_module._gamble_check_and_record(MESH)["ok"] is True


class TestItFailsClosed:
    def test_an_unreadable_state_file_refuses_the_flip(self, mail_module, limits_dir):
        """Corrupt or truncated state is what a half-deleted file looks like.
        'I cannot tell how many times you have flipped' must not mean 'go on'."""
        os.makedirs(limits_dir, exist_ok=True)
        with open(mail_module._gamble_state_path(MESH), "w") as f:
            f.write("{not json")
        result = mail_module._gamble_check_and_record(MESH)
        assert result["ok"] is False

    def test_an_unwritable_store_refuses_the_flip(self, mail_module, monkeypatch, tmp_path):
        """If the attempt cannot be recorded it cannot be bounded."""
        monkeypatch.setattr(mail_module, "_GAMBLE_LIMITS_DIR", str(tmp_path / "nope"))

        def boom(*a, **k):
            raise OSError("read-only volume")

        monkeypatch.setattr(mail_module.os, "makedirs", boom)
        assert mail_module._gamble_check_and_record(MESH)["ok"] is False

    def test_an_invalid_mesh_id_is_refused(self, mail_module, limits_dir):
        assert mail_module._gamble_check_and_record("../../etc/passwd")["ok"] is False


class TestStatusIsReadOnly:
    def test_reading_the_status_does_not_consume_an_attempt(self, mail_module, limits_dir):
        for _ in range(5):
            mail_module._gamble_status(MESH)
        assert mail_module._gamble_check_and_record(MESH)["ok"] is True

    def test_status_reports_the_cooldown_and_remaining(self, mail_module, limits_dir):
        mail_module._gamble_check_and_record(MESH)
        st = mail_module._gamble_status(MESH)
        assert st["gamble_cooldown_s"] > 0
        assert st["gamble_remaining_today"] == mail_module.GAMBLE_MAX_PER_DAY - 1
        assert st["gamble_max_per_day"] == mail_module.GAMBLE_MAX_PER_DAY

    def test_status_on_a_mesh_that_never_flipped_is_a_full_budget(self, mail_module, limits_dir):
        st = mail_module._gamble_status("neverFlipped1")
        assert st["gamble_cooldown_s"] == 0
        assert st["gamble_remaining_today"] == mail_module.GAMBLE_MAX_PER_DAY

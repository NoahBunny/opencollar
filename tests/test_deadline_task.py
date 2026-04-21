"""Tests for deadline-bound task actions in focuslock-mail.mesh_apply_order.

Covers: set → armed → pre-deadline clear (rolls forward) → miss (locks) →
post-miss clear (releases lock) → one-shot completion drops the task.
"""

import importlib.util
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


# focuslock-mail.py isn't importable by name (hyphen). Load it under an alias
# so we can call mesh_apply_order directly without running the HTTP server.
@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_test", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def orders():
    from focuslock_mesh import OrdersDocument

    return OrdersDocument()


class TestSetDeadlineTask:
    def test_armed_with_deadline_minutes(self, mail_module, orders):
        result = mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "Clean the sink", "deadline_minutes": 240, "interval_days": 3},
            orders,
        )
        assert result["applied"] == "set-deadline-task"
        assert orders.get("deadline_task_text") == "Clean the sink"
        assert orders.get("deadline_task_interval_ms") == 3 * 86400000
        assert orders.get("deadline_task_deadline_ms") > int(time.time() * 1000)

    def test_rejects_empty_text(self, mail_module, orders):
        r = mail_module.mesh_apply_order("set-deadline-task", {"deadline_minutes": 10}, orders)
        assert "error" in r

    def test_rejects_missing_deadline(self, mail_module, orders):
        r = mail_module.mesh_apply_order("set-deadline-task", {"text": "x"}, orders)
        assert "error" in r

    def test_rejects_past_deadline(self, mail_module, orders):
        past = int(time.time() * 1000) - 60000
        r = mail_module.mesh_apply_order("set-deadline-task", {"text": "x", "deadline_ms": past}, orders)
        assert "error" in r

    def test_rejects_bad_proof_type(self, mail_module, orders):
        r = mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "x", "deadline_minutes": 10, "proof_type": "hologram"},
            orders,
        )
        assert "error" in r

    def test_rejects_bad_on_miss(self, mail_module, orders):
        r = mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "x", "deadline_minutes": 10, "on_miss": "banish"},
            orders,
        )
        assert "error" in r


class TestDeadlineTaskCleared:
    def test_early_clear_rolls_forward_when_interval_set(self, mail_module, orders):
        mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "Clean sink", "deadline_minutes": 120, "interval_days": 3},
            orders,
        )
        result = mail_module.mesh_apply_order("deadline-task-cleared", {}, orders)
        assert result["cleared"] is False
        next_ms = result["next_deadline_ms"]
        # New deadline is 3 days from now (not from the old deadline)
        expected = int(time.time() * 1000) + 3 * 86400000
        assert abs(next_ms - expected) < 5000  # ±5s
        assert orders.get("deadline_task_text") == "Clean sink"  # task still armed

    def test_one_shot_clear_drops_task(self, mail_module, orders):
        mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "Devotional text", "deadline_minutes": 240},  # no interval
            orders,
        )
        result = mail_module.mesh_apply_order("deadline-task-cleared", {}, orders)
        assert result["cleared"] is True
        assert result["next_deadline_ms"] == 0
        assert orders.get("deadline_task_text") == ""
        assert orders.get("deadline_task_deadline_ms") == 0


class TestDeadlineTaskMissed:
    def test_miss_locks_when_on_miss_is_lock(self, mail_module, orders):
        mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "Sink", "deadline_minutes": 10, "on_miss": "lock"},
            orders,
        )
        result = mail_module.mesh_apply_order("deadline-task-missed", {"on_miss": "lock"}, orders)
        assert result["on_miss"] == "lock"
        assert str(orders.get("lock_active")) == "1"
        assert str(orders.get("deadline_task_locked_by_miss")) == "1"
        assert int(orders.get("deadline_task_missed_at_ms")) > 0

    def test_miss_bumps_paywall_when_on_miss_is_paywall(self, mail_module, orders):
        orders.set("paywall", "5")
        mail_module.mesh_apply_order(
            "set-deadline-task",
            {
                "text": "Sink",
                "deadline_minutes": 10,
                "on_miss": "paywall",
                "miss_amount": 25,
            },
            orders,
        )
        result = mail_module.mesh_apply_order("deadline-task-missed", {"on_miss": "paywall"}, orders)
        assert result["on_miss"] == "paywall"
        assert result["amount"] == 25
        assert orders.get("paywall") == "30"  # 5 + 25
        # Paywall path does NOT auto-lock
        assert str(orders.get("lock_active")) != "1"


class TestDeadlineTaskClearedAfterMiss:
    def test_clear_after_miss_releases_lock(self, mail_module, orders):
        mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "Sink", "deadline_minutes": 10, "interval_days": 3},
            orders,
        )
        # Simulate miss
        mail_module.mesh_apply_order("deadline-task-missed", {"on_miss": "lock"}, orders)
        assert str(orders.get("lock_active")) == "1"
        # Bunny clears
        result = mail_module.mesh_apply_order("deadline-task-cleared", {}, orders)
        assert result["released_lock"] is True
        assert str(orders.get("lock_active")) == "0"
        assert str(orders.get("deadline_task_locked_by_miss")) == "0"
        assert int(orders.get("deadline_task_missed_at_ms")) == 0


class TestClearDeadlineTask:
    def test_lion_cancel_resets_state(self, mail_module, orders):
        mail_module.mesh_apply_order(
            "set-deadline-task",
            {"text": "Sink", "deadline_minutes": 10, "interval_days": 3},
            orders,
        )
        mail_module.mesh_apply_order("deadline-task-missed", {"on_miss": "lock"}, orders)
        assert str(orders.get("lock_active")) == "1"
        result = mail_module.mesh_apply_order("clear-deadline-task", {}, orders)
        assert result["released_lock"] is True
        assert orders.get("deadline_task_text") == ""
        assert orders.get("deadline_task_deadline_ms") == 0
        assert str(orders.get("lock_active")) == "0"

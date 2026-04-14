"""Tests for shared/focuslock_adb.py — safe subprocess-based ADB wrapper."""

from unittest.mock import MagicMock

from focuslock_adb import ADBBridge


class TestADBBridgeInit:
    def test_no_devices_gives_empty_primary(self):
        b = ADBBridge()
        assert b.devices == []
        assert b.primary == ""

    def test_first_device_is_primary_by_default(self):
        b = ADBBridge(devices=["10.0.0.1:5555", "10.0.0.2:5555"])
        assert b.primary == "10.0.0.1:5555"

    def test_explicit_primary_overrides(self):
        b = ADBBridge(devices=["a:5555", "b:5555"], primary="b:5555")
        assert b.primary == "b:5555"


class TestADBBridgeGet:
    def test_returns_empty_when_no_device(self):
        b = ADBBridge()
        assert b.get("some_key") == ""

    def test_invokes_adb_with_safe_args(self, monkeypatch):
        captured = {}

        def fake_run(args, capture_output=False, text=False, timeout=None):
            captured["args"] = args
            result = MagicMock()
            result.stdout = "some_value\n"
            return result

        monkeypatch.setattr("subprocess.run", fake_run)
        b = ADBBridge(devices=["10.0.0.1:5555"])
        out = b.get("focus_lock_paywall")
        assert out == "some_value"
        # Verify args — no shell=True, literal key not interpolated
        assert captured["args"] == [
            "adb",
            "-s",
            "10.0.0.1:5555",
            "shell",
            "settings",
            "get",
            "global",
            "focus_lock_paywall",
        ]

    def test_exception_swallowed_returns_empty(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", MagicMock(side_effect=OSError("adb missing")))
        b = ADBBridge(devices=["10.0.0.1:5555"])
        assert b.get("any") == ""

    def test_uses_explicit_device_param(self, monkeypatch):
        captured = {}

        def fake_run(args, **kw):
            captured["args"] = args
            return MagicMock(stdout="ok")

        monkeypatch.setattr("subprocess.run", fake_run)
        b = ADBBridge(devices=["a:5555", "b:5555"])
        b.get("k", device="b:5555")
        assert "b:5555" in captured["args"]


class TestADBBridgePut:
    def test_pushes_to_all_devices(self, monkeypatch):
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            return MagicMock()

        monkeypatch.setattr("subprocess.run", fake_run)
        b = ADBBridge(devices=["a:5555", "b:5555", "c:5555"])
        b.put("focus_lock_active", 1)
        assert len(calls) == 3
        # All targeted at settings put with the same key
        for c in calls:
            assert c[:3] == ["adb", "-s", c[2]]
            assert c[3:] == ["shell", "settings", "put", "global", "focus_lock_active", "1"]

    def test_value_coerced_to_string(self, monkeypatch):
        calls = []
        monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args) or MagicMock())
        b = ADBBridge(devices=["a:5555"])
        b.put("x", 42)
        assert calls[0][-1] == "42"

    def test_exception_does_not_abort_other_devices(self, monkeypatch):
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            if args[2] == "bad:5555":
                raise OSError("connection lost")
            return MagicMock()

        monkeypatch.setattr("subprocess.run", fake_run)
        b = ADBBridge(devices=["good:5555", "bad:5555", "other:5555"])
        b.put("x", "y")  # should not raise
        assert len(calls) == 3  # all 3 attempted


class TestADBBridgePutStr:
    def test_no_shell_interpolation(self, monkeypatch):
        calls = []
        monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args) or MagicMock())
        b = ADBBridge(devices=["a:5555"])
        # Malicious value with shell metacharacters — must be passed as arg, not shell-interpolated
        b.put_str("message", "$(rm -rf /)")
        # The value is the last arg — no shell expansion occurs because no shell=True
        assert calls[0][-1] == "$(rm -rf /)"


class TestADBBridgeShell:
    def test_no_op_when_no_device(self, monkeypatch):
        calls = []
        monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args) or MagicMock())
        b = ADBBridge()  # no devices
        b.shell("echo hi")
        assert calls == []

    def test_splits_cmd_on_whitespace(self, monkeypatch):
        calls = []
        monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args) or MagicMock())
        b = ADBBridge(devices=["a:5555"])
        b.shell("am force-stop com.example")
        assert calls[0][-3:] == ["am", "force-stop", "com.example"]

    def test_exception_swallowed(self, monkeypatch):
        monkeypatch.setattr("subprocess.run", MagicMock(side_effect=RuntimeError("oops")))
        b = ADBBridge(devices=["a:5555"])
        b.shell("cmd")  # must not raise

    def test_explicit_device(self, monkeypatch):
        calls = []
        monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args) or MagicMock())
        b = ADBBridge(devices=["primary:5555", "secondary:5555"])
        b.shell("ls", device="secondary:5555")
        assert "secondary:5555" in calls[0]
        assert "primary:5555" not in calls[0]


class TestADBBridgeShellAll:
    def test_runs_on_all_devices(self, monkeypatch):
        calls = []
        monkeypatch.setattr("subprocess.run", lambda args, **kw: calls.append(args) or MagicMock())
        b = ADBBridge(devices=["a:5555", "b:5555"])
        b.shell_all("input keyevent 26")
        assert len(calls) == 2
        for c in calls:
            assert c[-3:] == ["input", "keyevent", "26"]

    def test_exception_continues(self, monkeypatch):
        calls = []

        def fake_run(args, **kw):
            calls.append(args)
            if "bad" in args[2]:
                raise RuntimeError()
            return MagicMock()

        monkeypatch.setattr("subprocess.run", fake_run)
        b = ADBBridge(devices=["good:5555", "bad:5555"])
        b.shell_all("ls")
        assert len(calls) == 2  # both attempted

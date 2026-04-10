# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock ADB Bridge — safe subprocess-based ADB wrapper.

Replaces os.system/os.popen calls with subprocess.run to eliminate
command injection vulnerabilities in shell-interpolated ADB commands.
"""

import subprocess


class ADBBridge:
    """Manage ADB communication with one or more Android devices."""

    def __init__(self, devices=None, primary=None):
        self.devices = list(devices or [])
        self.primary = primary or (self.devices[0] if self.devices else "")

    def get(self, key, device=None):
        """Read a settings global value from a device (default: primary)."""
        dev = device or self.primary
        if not dev:
            return ""
        try:
            result = subprocess.run(
                ["adb", "-s", dev, "shell", "settings", "get", "global", key],
                capture_output=True, text=True, timeout=10)
            return result.stdout.strip()
        except Exception:
            return ""

    def put(self, key, value):
        """Write a settings global value to ALL devices."""
        for dev in self.devices:
            try:
                subprocess.run(
                    ["adb", "-s", dev, "shell", "settings", "put", "global",
                     key, str(value)],
                    capture_output=True, timeout=10)
            except Exception:
                pass

    def put_str(self, key, value):
        """Write a string settings value (safe — no shell interpolation)."""
        for dev in self.devices:
            try:
                subprocess.run(
                    ["adb", "-s", dev, "shell", "settings", "put", "global",
                     key, str(value)],
                    capture_output=True, timeout=10)
            except Exception:
                pass

    def shell(self, cmd, device=None):
        """Run a shell command on one device (default: primary)."""
        dev = device or self.primary
        if not dev:
            return
        try:
            subprocess.run(
                ["adb", "-s", dev, "shell"] + cmd.split(),
                capture_output=True, timeout=10)
        except Exception:
            pass

    def shell_all(self, cmd):
        """Run a shell command on ALL devices."""
        for dev in self.devices:
            try:
                subprocess.run(
                    ["adb", "-s", dev, "shell"] + cmd.split(),
                    capture_output=True, timeout=10)
            except Exception:
                pass

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""Pytest fixtures for the §1a direct-pair UI automation suite.

The suite is OPT-IN. Default `pytest` runs (CI + local) skip this
directory entirely — fixtures require adb, Appium, and a running
Android emulator with com.focusctl / com.focuslock / com.bunnytasker
installed.

Opt in by setting env var `RUN_UI_TESTS=1`. The .github/workflows/ui.yml
job sets this explicitly; nothing else should.

Day-1 scope is §1a only. §1b (QR-pair) and §4b (mandatory-reply
auto-lock) each introduce their own fixtures in their own PR — do
not pre-build them here.
"""

from __future__ import annotations

import os
import re
import subprocess
import time

import pytest

if not os.environ.get("RUN_UI_TESTS"):
    # Keep the default `pytest` invocation fast and free of Appium deps.
    # When unset we also skip importing appium below, so missing deps on
    # a vanilla contributor box do not break collection.
    collect_ignore_glob = ["test_*.py"]

# Guarded import — only required when the suite actually runs.
if os.environ.get("RUN_UI_TESTS"):
    appium_webdriver = pytest.importorskip(
        "appium.webdriver",
        reason="UI suite requires appium-python-client (install via pip install -e '.[ui]')",
    )
    from appium.options.android import UiAutomator2Options

from tests.ui.fingerprint import compute_fingerprint

CONTROLLER_PKG = "com.focusctl"
COLLAR_PKG = "com.focuslock"
COMPANION_PKG = "com.bunnytasker"
BUNNY_PUBKEY_KEY = "focus_lock_bunny_pubkey"
LION_PUBKEY_KEY = "focus_lock_lion_pubkey"
APPIUM_SERVER = os.environ.get("APPIUM_SERVER", "http://127.0.0.1:4723")


def _adb(*args: str, timeout: int = 15) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["adb", *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


@pytest.fixture(scope="session")
def adb_device() -> str:
    """Pick the first connected adb device; fail loudly if none."""
    out = _adb("devices").stdout
    devices = [line.split()[0] for line in out.splitlines()[1:] if "\tdevice" in line]
    if not devices:
        pytest.fail("No adb device connected. Start an emulator or connect a phone.")
    return devices[0]


@pytest.fixture(scope="session")
def bunny_pubkey_b64(adb_device: str) -> str:
    """Read the bunny's public key from Settings.Global, seeding it if missing.

    Bunny Tasker generates the keypair lazily on MainActivity.onCreate, so a
    fresh install returns `null` from Settings.Global until the activity has
    been started once. Poll for up to 10s after a cold start.
    """
    for _ in range(20):
        out = _adb("-s", adb_device, "shell", "settings", "get", "global", BUNNY_PUBKEY_KEY).stdout.strip()
        if out and out != "null":
            return out
        _adb("-s", adb_device, "shell", "monkey", "-p", COMPANION_PKG, "-c", "android.intent.category.LAUNCHER", "1")
        time.sleep(0.5)
    pytest.fail(f"Bunny Tasker never produced {BUNNY_PUBKEY_KEY} in Settings.Global after 10s of polling.")


@pytest.fixture(scope="session")
def bunny_fingerprint(bunny_pubkey_b64: str) -> str:
    return compute_fingerprint(bunny_pubkey_b64)


@pytest.fixture(autouse=True)
def clean_pairing_state(adb_device: str):
    """Reset pairing state between tests.

    Required because:
      1. addBunnySlot() in MainActivity.java accumulates — each successful
         pair creates a new slot. Without pm clear the 5th test sees slot
         bunny_5 in a UI the test does not expect.
      2. The mismatch test's negative oracle checks that focus_lock_lion_pubkey
         is still empty on bunny. If a prior happy-path test left it set,
         the negative oracle trivially fails.
    """
    _adb("-s", adb_device, "shell", "pm", "clear", CONTROLLER_PKG)
    _adb("-s", adb_device, "shell", "settings", "delete", "global", LION_PUBKEY_KEY)
    yield


@pytest.fixture
def lion_pubkey_on_bunny(adb_device: str) -> str:
    """Read focus_lock_lion_pubkey from Settings.Global as the negative oracle."""
    out = _adb("-s", adb_device, "shell", "settings", "get", "global", LION_PUBKEY_KEY).stdout.strip()
    return "" if out == "null" else out


@pytest.fixture(scope="session")
def appium_driver(adb_device: str):
    options = UiAutomator2Options()
    options.platform_name = "Android"
    options.udid = adb_device
    options.app_package = CONTROLLER_PKG
    options.app_activity = f"{CONTROLLER_PKG}.MainActivity"
    options.no_reset = False
    options.full_reset = False
    options.auto_grant_permissions = True
    options.new_command_timeout = 120
    driver = appium_webdriver.Remote(APPIUM_SERVER, options=options)
    try:
        yield driver
    finally:
        driver.quit()


@pytest.fixture
def controller(appium_driver, adb_device: str):
    """Fresh launch of Lion's Share on each test — pm clear has cleared state."""
    _adb("-s", adb_device, "shell", "am", "start", "-n", f"{CONTROLLER_PKG}/.MainActivity", timeout=20)
    time.sleep(1.5)
    return appium_driver


def wait_for_status(driver, pattern: str, timeout: float = 15.0) -> str:
    """Poll the @+id/status TextView until its text matches `pattern` (regex)."""
    from appium.webdriver.common.appiumby import AppiumBy  # local import keeps global scope cheap

    regex = re.compile(pattern)
    deadline = time.monotonic() + timeout
    last = ""
    while time.monotonic() < deadline:
        try:
            el = driver.find_element(AppiumBy.ACCESSIBILITY_ID, "status_text")
            last = el.text or ""
            if regex.search(last):
                return last
        except Exception:
            pass
        time.sleep(0.4)
    raise AssertionError(f"status never matched {pattern!r} within {timeout}s. last={last!r}")

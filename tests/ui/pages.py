# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""Page objects for the Lion's Share direct-pair flow.

Selector policy — DO NOT "clean up" without reading this first:

- Field widgets (IP / port / fingerprint EditTexts) use Appium's
  ACCESSIBILITY_ID (= Android content-description). The controller sets
  these in doPairDirect() before the dialog is shown, so lookup is
  race-free.

- The Pair button is the AlertDialog's BUTTON_POSITIVE. Its label is
  declared via setPositiveButton("Pair", ...) which ships the visible
  text synchronously — before show() returns. Selecting by text is
  therefore more reliable than a content-desc set post-show().

- The "Pair Direct (LAN)" entry point is the setup dialog's neutral
  button — same reasoning: visible-text selector is stable, content-desc
  is not set.
"""

from __future__ import annotations

import time

from appium.webdriver.common.appiumby import AppiumBy


def _by_text(driver, text: str, timeout: float = 5.0):
    """Find by exact visible text via UiAutomator2 string match."""
    deadline = time.monotonic() + timeout
    selector = f'new UiSelector().text("{text}")'
    last = None
    while time.monotonic() < deadline:
        try:
            return driver.find_element(AppiumBy.ANDROID_UIAUTOMATOR, selector)
        except Exception as e:
            last = e
            time.sleep(0.3)
    raise AssertionError(f"element with text={text!r} not found in {timeout}s ({last})")


def _by_desc(driver, desc: str, timeout: float = 5.0):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            return driver.find_element(AppiumBy.ACCESSIBILITY_ID, desc)
        except Exception as e:
            last = e
            time.sleep(0.3)
    raise AssertionError(f"element with content-desc={desc!r} not found in {timeout}s ({last})")


class MainPage:
    def __init__(self, driver):
        self.driver = driver

    def open_setup(self) -> SetupDialog:
        # btn_setup is declared as @+id/btn_setup in activity_main.xml — the
        # resource-id lookup is stable. Fall back to visible text if a future
        # layout change drops the id.
        try:
            el = self.driver.find_element(AppiumBy.ID, "com.focusctl:id/btn_setup")
        except Exception:
            el = _by_text(self.driver, "Setup")
        el.click()
        return SetupDialog(self.driver)


class SetupDialog:
    def __init__(self, driver):
        self.driver = driver

    def tap_pair_direct(self) -> PairDialog:
        _by_text(self.driver, "Pair Direct (LAN)").click()
        return PairDialog(self.driver)


class PairDialog:
    def __init__(self, driver):
        self.driver = driver
        # Fields have stable content-descriptions set in MainActivity.doPairDirect().
        self.ip = _by_desc(driver, "pair_dlg_ip")
        self.port = _by_desc(driver, "pair_dlg_port")
        self.fingerprint = _by_desc(driver, "pair_dlg_fingerprint")

    def set_ip(self, ip: str) -> PairDialog:
        self.ip.clear()
        self.ip.send_keys(ip)
        return self

    def set_port(self, port: str) -> PairDialog:
        self.port.clear()
        self.port.send_keys(port)
        return self

    def set_fingerprint(self, fp: str) -> PairDialog:
        self.fingerprint.clear()
        if fp:
            self.fingerprint.send_keys(fp)
        return self

    def tap_pair(self) -> None:
        _by_text(self.driver, "Pair").click()

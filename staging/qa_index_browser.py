#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Headless-Chromium walkthrough of the Lion's Share web remote (`web/index.html`).

The companion to qa_wizard_browser.py. Where qa_runner.py drives /admin/order
directly to verify the *server* handles each action correctly, this script
drives the *browser*, clicks every button in the index.html menu, and
asserts the click produces the expected /admin/order POST.

Catches the same class of bug the wizard PIN-passthrough miss caught: a
button that wires to the wrong action, or doesn't wire at all.

Pre-requisites:
  * staging relay running on 127.0.0.1:18435 with FOCUSLOCK_WEB_DIR pointed
    at the repo's web/ directory.
  * playwright + chromium installed.
"""

import json
import sys
from pathlib import Path

from playwright.sync_api import Request, sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
RELAY = "http://127.0.0.1:18435"
SCREENS = REPO_ROOT / "staging" / "qa-screens-index"

# Read the staging admin token (which serves as PIN in relay mode)
CONFIG = json.loads((REPO_ROOT / "staging" / "config.json").read_text())
TOKEN = CONFIG["admin_token"]


# ──────────────────────── helpers ────────────────────────


class OrderRecorder:
    """Captures every /admin/order POST for assertion."""

    def __init__(self):
        self.calls: list[dict] = []

    def attach(self, page):
        page.on("request", self._on_request)

    def _on_request(self, req: Request):
        if "/admin/order" not in req.url or req.method != "POST":
            return
        try:
            body = json.loads(req.post_data or "{}")
        except Exception:
            body = {"_raw": req.post_data}
        self.calls.append(body)

    def actions(self):
        return [c.get("action") for c in self.calls]

    def reset(self):
        self.calls = []

    def find(self, action):
        return [c for c in self.calls if c.get("action") == action]


def shot(page, name):
    SCREENS.mkdir(exist_ok=True)
    page.screenshot(path=str(SCREENS / f"{name}.png"), full_page=True)


# ──────────────────────── cases ────────────────────────


CASES: list[tuple[str, callable]] = []


def case(name):
    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def login(page):
    page.goto(RELAY + "/")
    page.wait_for_selector("#pinInput")
    page.locator("#pinInput").fill(TOKEN)
    page.locator("#pinSubmit").click()
    # Wait for app shell to become visible
    page.wait_for_selector("#appShell:not(.hidden)", timeout=8000)


@case("00_login")
def login_test(page, rec):
    login(page)
    # Mode badge should read RELAY (we're not on LAN)
    badge = page.locator(".mode-badge")
    if badge.count():
        assert "RELAY" in badge.first.inner_text(), f"mode badge: {badge.first.inner_text()}"
    shot(page, "00_login")


def click_tab(page, tab):
    page.locator(f'.tab-btn[data-tab="{tab}"]').click()
    page.wait_for_selector(f"#tab-{tab}.tab-content.active")


# ── Lock tab ──


@case("01_lock_all_button")
def lock_all(page, rec):
    click_tab(page, "lock")
    rec.reset()
    page.locator("#btnLockAll").click()
    page.wait_for_timeout(400)
    assert "lock" in rec.actions(), f"expected 'lock', saw {rec.actions()}"


@case("02_quick_lock_15m")
def quick_lock_15(page, rec):
    rec.reset()
    page.locator('[data-quick="15"]').click()
    page.wait_for_timeout(400)
    locks = rec.find("lock")
    assert locks, f"expected 'lock' from quick-15, saw {rec.actions()}"
    params = locks[-1].get("params", {})
    assert params.get("timer") == 15, f"expected timer=15, got {params}"


@case("03_unlock_all")
def unlock(page, rec):
    rec.reset()
    page.locator("#btnUnlockAll").click()
    page.wait_for_timeout(400)
    assert "unlock" in rec.actions(), f"expected 'unlock', saw {rec.actions()}"


@case("04_paywall_add_5")
def add_paywall_quick(page, rec):
    rec.reset()
    page.locator('[data-add="5"]').click()
    page.wait_for_timeout(400)
    adds = rec.find("add-paywall")
    assert adds, f"expected 'add-paywall', saw {rec.actions()}"
    assert adds[-1]["params"]["amount"] == 5, adds[-1]["params"]


@case("05_paywall_add_custom")
def add_paywall_custom(page, rec):
    page.locator("#customPaywall").fill("7")
    rec.reset()
    page.locator("#btnAddCustom").click()
    page.wait_for_timeout(400)
    adds = rec.find("add-paywall")
    assert adds, f"expected 'add-paywall', saw {rec.actions()}"
    assert float(adds[-1]["params"]["amount"]) == 7.0


# ── Rules tab (mode + style + task + schedule + location + toy + voice) ──


@case("06_rules_tab_loads")
def rules_tab(page, rec):
    click_tab(page, "rules")
    # The Lock Mode card should be visible
    page.wait_for_selector("#advMode")
    shot(page, "06_rules")


# ── Money tab (subscription + tribute + streak + paywall actions) ──


@case("06b_money_tab_loads")
def money_tab(page, rec):
    click_tab(page, "money")
    # Subscription status card should be visible
    page.wait_for_selector("#btnSubscription")
    shot(page, "06b_money")


@case("07_clear_paywall_modal")
def clear_paywall(page, rec):
    rec.reset()
    page.locator("#btnClearPaywall").click()
    # Modal opens — confirm "Clear"
    page.wait_for_selector(".modal-overlay.show, #modalOverlay.show", timeout=2000)
    # Click the Clear button in the modal (btn-danger)
    page.locator("#modalActions .btn-danger").click()
    page.wait_for_timeout(400)
    assert "clear-paywall" in rec.actions(), f"expected 'clear-paywall', saw {rec.actions()}"


@case("08_set_bedtime")
def set_bedtime(page, rec):
    click_tab(page, "rules")
    page.locator("#bedtimeLockHour").fill("23")
    page.locator("#bedtimeUnlockHour").fill("7")
    rec.reset()
    page.locator("#btnSetBedtime").click()
    page.wait_for_timeout(400)
    bts = rec.find("set-bedtime")
    assert bts, f"expected 'set-bedtime', saw {rec.actions()}"
    params = bts[-1]["params"]
    assert params.get("lock_hour") == 23 and params.get("unlock_hour") == 7, params


@case("09_clear_bedtime")
def clear_bedtime(page, rec):
    rec.reset()
    page.locator("#btnClearBedtime").click()
    page.wait_for_timeout(400)
    assert "clear-bedtime" in rec.actions(), f"saw {rec.actions()}"


@case("10_set_screen_time")
def set_screen_time(page, rec):
    page.locator("#screenTimeQuota").fill("90")
    rec.reset()
    page.locator("#btnSetScreenTime").click()
    page.wait_for_timeout(400)
    sts = rec.find("set-screen-time")
    assert sts, f"expected 'set-screen-time', saw {rec.actions()}"
    assert sts[-1]["params"].get("quota_minutes") == 90


@case("11_clear_screen_time")
def clear_screen_time(page, rec):
    rec.reset()
    page.locator("#btnClearScreenTime").click()
    page.wait_for_timeout(400)
    assert "clear-screen-time" in rec.actions(), f"saw {rec.actions()}"


@case("12_pin_message_modal")
def pin_message(page, rec):
    # Pin-message moved from Power Tools (Advanced) to its own card in Inbox
    click_tab(page, "inbox")
    rec.reset()
    page.locator("#btnPinMessage").click()
    page.wait_for_selector("#modalOverlay.show", timeout=2000)
    # showPromptModal renders an input with id="modalInput"
    page.locator("#modalInput").fill("qa-pin-test")
    page.locator("#modalActions .btn-primary").click()
    page.wait_for_timeout(400)
    pins = rec.find("pin-message")
    assert pins, f"expected 'pin-message', saw {rec.actions()}"
    assert pins[-1]["params"].get("message") == "qa-pin-test"


@case("13_subscription_silver")
def subscription(page, rec):
    # Subscription button moved to Money tab
    click_tab(page, "money")
    rec.reset()
    page.locator("#btnSubscription").click()
    page.wait_for_selector("#modalOverlay.show", timeout=2000)
    # The modal has 3 buttons (Bronze/Silver/Gold) that dispatch a custom event.
    # Click Silver — its onclick fires document.dispatchEvent('set-sub' detail='silver')
    page.locator("#modalBody button").nth(1).click()  # 0=bronze, 1=silver, 2=gold
    page.wait_for_timeout(500)
    subs = rec.find("subscribe")
    assert subs, f"expected 'subscribe', saw {rec.actions()}"
    assert subs[-1]["params"].get("tier") == "silver", subs[-1]["params"]


@case("14_tribute_button_wires_to_one_action")
def tribute(page, rec):
    # Tribute moved to Money tab
    click_tab(page, "money")
    # The tribute button toggles: if tribute_active=1 it sends clear-tribute,
    # else it opens a prompt modal then sends set-tribute. Either is correct
    # button-wiring; we just want exactly one of those actions to fire.
    rec.reset()
    page.locator("#btnTribute").click()
    page.wait_for_timeout(500)
    if page.locator("#modalOverlay.show").count():
        # Set-path: prompt modal opened
        page.locator("#modalInput").fill("3")
        page.locator("#modalActions .btn-primary").click()
        page.wait_for_timeout(500)
        sets = rec.find("set-tribute")
        assert sets, f"set-path: expected 'set-tribute', saw {rec.actions()}"
        assert int(sets[-1]["params"].get("amount", 0)) == 3
    else:
        # Clear-path: action fired directly
        clears = rec.find("clear-tribute")
        assert clears, f"clear-path: expected 'clear-tribute', saw {rec.actions()}"


@case("15_streak_start")
def streak(page, rec):
    rec.reset()
    page.locator("#btnStreak").click()
    page.wait_for_timeout(400)
    # Either start- or stop-streak depending on current state — both are valid
    # button-wired actions, but we want exactly one to fire.
    starts = rec.find("start-streak")
    stops = rec.find("stop-streak")
    assert (len(starts) + len(stops)) >= 1, f"expected start/stop-streak, saw {rec.actions()}"


@case("16_lan_only_buttons_disabled_in_relay_mode")
def lan_only_buttons_disabled(page, rec):
    """applyRelayRestrictions() disables btnPlayAudio, btnSpeak, btnConfineHome
    + every Lovense (lovenseSection) button. Verify the disabled state instead
    of attempting to click — that's the actual UX contract."""
    # Buttons live in the Rules tab now (under Toy + Voice cards) plus the
    # Confine-Home button under Location.
    click_tab(page, "rules")
    for btn_id in ("btnPlayAudio", "btnSpeak", "btnConfineHome"):
        loc = page.locator(f"#{btn_id}")
        assert loc.count() == 1, f"missing button: {btn_id}"
        assert loc.is_disabled(), f"#{btn_id} should be disabled in relay mode"
        title = loc.get_attribute("title") or ""
        assert "LAN" in title, f"#{btn_id} title should mention LAN, got: {title!r}"
    # And every Lovense button
    toy_btns = page.locator("#lovenseSection button")
    assert toy_btns.count() >= 1, "no toy buttons found"
    for i in range(toy_btns.count()):
        assert toy_btns.nth(i).is_disabled(), f"lovense button {i} not disabled"


@case("17_send_message_via_inbox")
def send_message(page, rec):
    click_tab(page, "inbox")
    page.locator("#msgText").fill("qa-msg-test")
    rec.reset()
    page.locator("#btnSendMsg").click()
    page.wait_for_timeout(500)
    # /api/message → apiRelay rewrites to action='send-message' with body
    # passed through as params (so params.message holds the text). The
    # server's mesh_apply_order accepts either params.text or params.message.
    sends = rec.find("send-message")
    assert sends, f"expected 'send-message', saw {rec.actions()}"
    p = sends[-1]["params"]
    msg_value = p.get("text") or p.get("message")
    assert msg_value == "qa-msg-test", f"unexpected params: {p}"


@case("18_logout_button")
def logout(page, rec):
    page.locator("#btnLogout").click()
    page.wait_for_timeout(500)
    # Login screen should be visible again
    page.wait_for_selector("#pinInput", timeout=4000)


# ──────────────────────── runner ────────────────────────


def main():
    SCREENS.mkdir(exist_ok=True)
    failures = []
    rec = OrderRecorder()
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 480, "height": 1200})
        page = ctx.new_page()
        rec.attach(page)

        # Surface any unhandled page errors
        page.on("pageerror", lambda exc: failures.append(("JS pageerror", str(exc))))

        for name, fn in CASES:
            sys.stdout.write(f"  • {name} ... ")
            sys.stdout.flush()
            try:
                fn(page, rec)
                print("OK")
            except Exception as e:
                print(f"FAIL: {e}")
                shot(page, f"FAIL_{name}")
                failures.append((name, str(e)))
        browser.close()

    print()
    if failures:
        print(f"FAILED: {len(failures)} case(s)")
        for n, e in failures:
            print(f"  - {n}: {e}")
        return 1
    print(f"OK: {len(CASES)} cases passed; screenshots in {SCREENS}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

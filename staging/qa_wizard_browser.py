#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Headless-Chromium walkthrough of the signup wizard.

Drives every step of web/signup.html via Playwright and asserts:

- Each of the 7 steps renders correctly
- Validation rejects bad input (empty pubkey, bad PIN format, bad PEM)
- Skip paths leave initial_config cleanly empty for those keys
- The IMAP toggle reveals/hides credentials fields
- The subscription radio updates state
- The review screen shows what we entered
- The result screen renders the QR + invite code returned by the API

Captures a PNG per step into staging/qa-screens/. Exits 0 on full pass.

Pre-requisites:
  * staging relay running on 127.0.0.1:18435 with FOCUSLOCK_WEB_DIR pointed
    at the repo's web/ directory.
  * playwright installed: .venv/bin/python -m playwright install chromium
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
RELAY = "http://127.0.0.1:18435"
SCREENS = REPO_ROOT / "staging" / "qa-screens"
PUBKEY = (REPO_ROOT / "staging" / "lion_pubkey.pem").read_text()


CASES = []


def case(name):
    """Decorator: register a test function."""

    def deco(fn):
        CASES.append((name, fn))
        return fn

    return deco


def shot(page, name):
    SCREENS.mkdir(exist_ok=True)
    page.screenshot(path=str(SCREENS / f"{name}.png"), full_page=True)


def assert_step_active(page, step_data):
    visible = page.locator(f'.step.active[data-step="{step_data}"]')
    assert visible.count() == 1, (
        f"expected step={step_data} active, got: {page.locator('.step.active').get_attribute('data-step')}"
    )


# ──────────────────────── cases ────────────────────────


@case("01_welcome_renders")
def welcome_renders(page):
    page.goto(RELAY + "/signup")
    page.wait_for_selector(".step.active")
    assert_step_active(page, "welcome")
    assert "Welcome, Lion" in page.locator(".step.active .step-title").inner_text()
    assert page.locator("#stepNum").inner_text() == "1"
    assert page.locator("#stepTotal").inner_text() == "7"
    shot(page, "01_welcome")
    page.locator(".step.active .btn-primary").click()


@case("02_key_step_validation")
def key_step_validation(page):
    # We're now on key step
    assert_step_active(page, "key")

    # Empty key → error
    page.locator(".step.active .btn-primary").click()
    err = page.locator("#errKey")
    assert err.is_visible() and "required" in err.inner_text().lower()

    # Garbage key (not PEM) → error
    page.locator("#pubkey").fill("not a real PEM key")
    page.locator(".step.active .btn-primary").click()
    assert "PEM" in err.inner_text()

    # Bad PIN (3 digits) → error
    page.locator("#pubkey").fill(PUBKEY)
    page.locator("#pin").fill("999")
    page.locator(".step.active .btn-primary").click()
    assert "PIN" in err.inner_text()

    # Valid: real key + 4-digit PIN → advances
    page.locator("#pin").fill("1234")
    shot(page, "02_key_filled")
    page.locator(".step.active .btn-primary").click()


@case("03_imap_toggle_off_then_on")
def imap_toggle(page):
    assert_step_active(page, "imap")
    fields = page.locator("#imapFields")
    # Default = off, fields hidden
    assert not fields.is_visible()

    # Turn on → fields appear
    page.locator('[data-choice-imap="on"]').click()
    assert fields.is_visible()
    page.locator("#imapHost").fill("imap.test")
    page.locator("#imapUser").fill("lion@qa.test")
    page.locator("#imapPass").fill("apppass-qa")
    shot(page, "03_imap_on")
    page.locator(".step.active .btn-primary").click()


@case("04_rules_step")
def rules_step(page):
    assert_step_active(page, "rules")
    page.locator("#tribute").fill("3")
    page.locator("#bedLock").fill("23")
    page.locator("#bedUnlock").fill("7")
    page.locator("#screenTime").fill("90")
    shot(page, "04_rules_filled")
    page.locator(".step.active .btn-primary").click()


@case("05_subscription_select_silver")
def sub_select(page):
    assert_step_active(page, "sub")
    page.locator('[data-choice-sub="silver"]').click()
    # Scope the .selected lookup to the active step so we don't pick up
    # the imap-step's selected choice, which is also still in the DOM.
    selected = page.locator(".step.active .choice.selected")
    assert selected.get_attribute("data-choice-sub") == "silver"
    shot(page, "05_sub_silver")
    page.locator(".step.active .btn-primary").click()


@case("06_review_shows_collected")
def review_step(page):
    assert_step_active(page, "review")
    body = page.locator("#reviewBody").inner_text()
    # Each line should reflect what we entered
    assert "$3/day" in body, f"tribute missing in review: {body}"
    assert "23:00" in body and "7:00" in body, f"bedtime missing in review: {body}"
    assert "90 min/day" in body, f"screen-time missing in review: {body}"
    assert "Silver" in body, f"sub tier missing in review: {body}"
    assert "lion@qa.test" in body, f"IMAP user missing: {body}"
    shot(page, "06_review")
    page.locator("#btnCreate").click()


@case("07_result_shows_invite_qr")
def result_step(page):
    # Wait for the result step to become active
    page.wait_for_selector('.step.active[data-step="result"]', timeout=10000)
    # Invite code should be a non-empty WORD-NN-WORD shape
    invite = page.locator("#rInvite").inner_text()
    assert invite and "-" in invite, f"invite missing: {invite!r}"
    # Mesh ID, auth, PIN all populated
    assert page.locator("#rMeshId").inner_text(), "mesh_id missing"
    assert page.locator("#rAuth").inner_text(), "auth_token missing"
    assert page.locator("#rPin").inner_text() == "1234", "PIN didn't pass through"
    # QR canvas rendered
    qr_canvases = page.locator("#qrWrap canvas")
    assert qr_canvases.count() == 1, f"expected 1 QR canvas, got {qr_canvases.count()}"
    # Applied summary lists the 5 actions
    applied = page.locator("#appliedSummary").inner_text()
    for action in ("set payment email", "set tribute", "subscribe", "set bedtime", "set screen time"):
        assert action in applied, f"applied summary missing {action!r}: {applied}"
    shot(page, "07_result")

    # Sanity: also extract mesh_id + invite for the next QA stage
    mesh_id = page.locator("#rMeshId").inner_text()
    invite_code = page.locator("#rInvite").inner_text()
    auth_token = page.locator("#rAuth").inner_text()
    Path("/tmp/qa-mesh.txt").write_text(f"{mesh_id}\n{invite_code}\n{auth_token}\n")


@case("08_skip_path_clean")
def skip_path(page):
    """Run the wizard again, skip every optional step. Only Lion key is required."""
    page.goto(RELAY + "/signup")
    page.wait_for_selector(".step.active")
    page.locator(".step.active .btn-primary").click()  # welcome → key
    page.locator("#pubkey").fill(PUBKEY)
    page.locator(".step.active .btn-primary").click()  # key → imap (skip default)
    # IMAP default is "off" — just continue
    page.locator(".step.active .btn-primary").click()  # imap → rules (all blank)
    page.locator(".step.active .btn-primary").click()  # rules → sub (none)
    # Sub default is "" (none)
    page.locator(".step.active .btn-primary").click()  # sub → review
    body = page.locator("#reviewBody").inner_text()
    # Every optional line should say "Skipped" or "None"
    assert body.count("Skipped") + body.count("None") + body.count("Auto-generated") >= 5, (
        f"expected 5+ skip markers in {body}"
    )
    shot(page, "08_review_all_skipped")
    page.locator("#btnCreate").click()
    page.wait_for_selector('.step.active[data-step="result"]', timeout=10000)
    applied = page.locator("#appliedSummary").inner_text()
    # No initial config applied → applied summary should be empty
    assert "set" not in applied.lower(), f"expected no applied actions, got: {applied}"
    shot(page, "09_result_skip_path")


# ──────────────────────── runner ────────────────────────


def main():
    SCREENS.mkdir(exist_ok=True)
    failures = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 480, "height": 900})
        page = ctx.new_page()

        # Surface JS console errors
        page.on("pageerror", lambda exc: failures.append(("JS pageerror", str(exc))))
        page.on(
            "console",
            lambda msg: print(f"  [console.{msg.type}] {msg.text}") if msg.type in ("error", "warning") else None,
        )

        for name, fn in CASES:
            sys.stdout.write(f"  • {name} ... ")
            sys.stdout.flush()
            try:
                fn(page)
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

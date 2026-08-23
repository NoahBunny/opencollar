#!/usr/bin/env python3
"""Programmatic QA for the Lion's Share 83 tab reorganisation.

Drives the real APK on a real Android (Waydroid or a handset) through the
structural half of `docs/QA-lions-share-83.md` §3 — the rows that do not need a
paired Collar. Those are also the rows that cover what 83 actually changed:
where every control lives, what is hidden until relevant, and the three traps
found in review (the compliment field, the money duplicate, the collapsed
sections).

    python3 staging/qa_lions_share_83.py --serial <serial> [--install] [--out dir]

Exits non-zero if any check fails. Screenshots of each tab land in --out.

Deliberately NOT uiautomator2: that wedges Waydroid's adbd and is why
`docs/UI-AUTOMATION-DECISION.md` shelved UI automation. This talks to the
`uiautomator` binary already on the device — see `staging/uiauto.py`.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from uiauto import UiDevice

REPO = Path(__file__).resolve().parent.parent
APK = REPO / "android" / "controller" / "focusctl-signed.apk"
PKG = "com.focusctl"
ACTIVITY = ".MainActivity"

results: list[dict] = []


def check(row: str, what: str, ok: bool, detail: str = "") -> bool:
    results.append({"row": row, "check": what, "ok": bool(ok), "detail": detail})
    print(f"  {'PASS' if ok else 'FAIL'}  {row:6} {what}" + (f"  — {detail}" if detail else ""))
    return ok


def ids_on_screen(d: UiDevice) -> set[str]:
    return {n["id"].split("/")[-1] for n in d.nodes() if n["id"]}


def goto_tab(d: UiDevice, tab_id: str) -> None:
    d.tap(f"id/{tab_id}")
    time.sleep(1.0)


# ─────────────────────────────────────────────────────────────────────────


class Abort(Exception):
    """The app is not on screen; every later check would fail for that one
    reason and bury it under fifty lines of noise."""


def step_launch(d: UiDevice) -> None:
    d.force_stop(PKG)
    up = d.launch(PKG, ACTIVITY, settle=6)
    act = d.current_activity()
    if not check("3.1", "app launches", up, act or "no resumed activity"):
        raise Abort(f"app never came to the foreground (resumed: {act or 'nothing'})")

    # A modal (mesh-created, pair conflict) covers the whole activity and would
    # read as "every control is missing".
    for _ in range(3):
        if d.find("android:id/alertTitle") is None:
            break
        title = d.find("android:id/alertTitle")
        check("3.1", f"dismissed a modal: {title['text'][:40]}", d.tap("android:id/button1", settle=2))

    present = ids_on_screen(d)
    if "btn_onboard_next" in present or d.visible("Welcome"):
        check("3.1", "onboarding shown on a fresh install", True, "walking past it")
        for _ in range(8):
            if not (d.tap("Next") or d.tap("Skip") or d.tap("Continue")):
                break

    present = ids_on_screen(d)
    check(
        "3.1",
        "four tabs, named by verb",
        {"tab_lock", "tab_rules", "tab_money", "tab_inbox"} <= present,
        ",".join(sorted(i for i in present if i.startswith("tab_"))) or "none",
    )
    check("3.1", "old power-level tab names are gone", not ({"tab_simple", "tab_advanced"} & present))
    check("3.1", "lands on Lock", "btn_lock" in present)
    check("3.1", "kebab present in the header", "btn_kebab" in present)
    check("3.1", "status bar renders", "status" in present)


def step_lock_tab(d: UiDevice) -> None:
    goto_tab(d, "tab_lock")
    present = ids_on_screen(d)
    for wanted in (
        "message_input",
        "timer_input",
        "paywall_amount",
        "btn_lock",
        "btn_lock_15",
        "btn_lock_30",
        "btn_lock_60",
        "btn_lock_120",
        "btn_unlock",
        "btn_unlock_device",
    ):
        check("3.2", f"Lock has {wanted}", wanted in present)

    check(
        "3.2",
        "the lock-order paywall field moved off the money row",
        "paywall_amount" in present and "balance_display" not in present,
        "it stages the NEXT lock's balance, not the current one",
    )

    # Live Pokes: collapsed by default, opens on tap, and the toy row stays
    # hidden without a Lovense.
    check("3.8", "Live Pokes collapsed on arrival", "btn_play_audio" not in present)
    opened = d.tap("id/sec_pokes_head")
    after = ids_on_screen(d)
    check("3.8", "Live Pokes opens when tapped", opened and "btn_play_audio" in after and "btn_speak" in after)
    check("3.8", "toy row stays hidden with no Lovense", "btn_toy_pulse" not in after)
    d.tap("id/sec_pokes_head")

    check("3.12", "Body Check hidden with no homelab attached", "btn_body_check_start" not in ids_on_screen(d))


def step_rules_tab(d: UiDevice) -> None:
    goto_tab(d, "tab_rules")
    present = ids_on_screen(d)
    for wanted in (
        "mode_spinner",
        "task_input",
        "task_reps",
        "btn_task",
        "btn_deadline_task",
        "btn_set_geofence",
        "btn_confine_home",
        "btn_entrap_adv",
    ):
        check("3.6", f"Rules has {wanted}", wanted in present)
    check(
        "3.6",
        "the whole compose-a-lock flow is on one tab",
        {"mode_spinner", "task_input", "btn_task"} <= present,
        "this used to span Advanced -> Control",
    )

    # Modifiers: collapsed, summary tracks what is on.
    check("3.7", "Modifiers collapsed on arrival", "toggle_shame" not in present)
    d.tap("id/sec_mods_head")
    after = ids_on_screen(d)
    check("3.7", "Modifiers opens when tapped", {"toggle_shame", "toggle_mute"} <= after)
    d.tap("id/toggle_shame")
    d.tap("id/toggle_mute")
    d.tap("id/sec_mods_head")
    summary = d.find("id/sec_mods_summary")
    check(
        "3.7",
        "collapsed summary names what is switched on",
        summary is not None and "Taunt" in summary["text"] and "Mute" in summary["text"],
        summary["text"] if summary else "summary not found",
    )
    d.tap("id/sec_mods_head")
    d.tap("id/toggle_shame")
    d.tap("id/toggle_mute")
    d.tap("id/sec_mods_head")


def step_compliment_reveal(d: UiDevice) -> None:
    """The trap review found: the Compliment mode's only input was hidden."""
    goto_tab(d, "tab_rules")
    check("3.10b", "compliment field hidden for other modes", "compliment_prompt" not in ids_on_screen(d))

    if not d.tap("id/mode_spinner"):
        check("3.10b", "mode spinner opens", False)
        return
    time.sleep(1.2)
    if not d.tap("Compliment"):
        check("3.10b", "Compliment selectable", False)
        d.back()
        return
    time.sleep(1.2)
    check("3.10b", "picking Compliment reveals its field", "compliment_prompt" in ids_on_screen(d))

    d.tap("id/mode_spinner")
    time.sleep(1.2)
    d.tap("Basic lock")
    time.sleep(1.2)
    check("3.10b", "switching away hides it again", "compliment_prompt" not in ids_on_screen(d))


def step_money_tab(d: UiDevice) -> None:
    goto_tab(d, "tab_money")
    present = ids_on_screen(d)
    for wanted in (
        "balance_display",
        "btn_clear_balance",
        "balance_set_input",
        "btn_set_balance",
        "btn_add_1",
        "btn_add_5",
        "btn_add_10",
        "btn_add_25",
        "btn_add_50",
        "btn_start_fine",
        "btn_stop_fine",
        "btn_force_sub",
        "sub_status_text",
        "btn_gamble",
    ):
        check("3.9", f"Money has {wanted}", wanted in present)

    # `lion_payment_history` is filled programmatically, so while it is empty it
    # has zero height and never appears in a uiautomator dump. Assert the
    # section header, which always renders, and prove the container itself
    # separately by putting a row in it (below).
    check("3.9", "Money has the PAYMENT HISTORY section", d.find("PAYMENT HISTORY") is not None)

    check(
        "3.9",
        "the duplicate clear is gone",
        "btn_clear_paywall" not in present,
        "two buttons used to POST the identical clear",
    )

    # ── the regression review caught: Money's ledger and subscription were
    # populated only from refreshInbox(), so landing on Money showed the
    # layout defaults forever. Charging $5 must make history appear WITHOUT
    # navigating away and back.
    before = d.find("id/balance_display")
    before_txt = before["text"] if before else "?"
    if d.tap("id/btn_add_5", settle=3):
        time.sleep(4)  # scheduleMoneyRefresh coalesces on 800ms, then fetches
        after = d.find("id/balance_display")
        after_txt = after["text"] if after else "?"
        check("3.10d", "charging $5 moves the balance", before_txt != after_txt, f"{before_txt} -> {after_txt}")
        populated = d.find("id/lion_payment_history") is not None
        check(
            "3.10d",
            "payment history populates without leaving the tab",
            populated,
            "this is what regressed: the widget moved to Money, its refresh stayed on Inbox",
        )
    else:
        check("3.10d", "+$5 tappable", False)

    # The clear must confirm before doing anything.
    d.tap("id/btn_clear_balance")
    time.sleep(1.0)
    confirmed = d.visible("Clear the balance?") or d.visible("cannot be undone")
    check("3.9", "clearing the balance asks first", confirmed)
    d.tap("Cancel") or d.back()


def step_inbox_tab(d: UiDevice) -> None:
    goto_tab(d, "tab_inbox")
    present = ids_on_screen(d)
    for wanted in (
        "inbox_message_input",
        "btn_send_message",
        "btn_schedule_message",
        "btn_pin_message",
        "toggle_pin_notif",
        "toggle_mandatory",
    ):
        check("3.11", f"Inbox has {wanted}", wanted in present)
    # Same zero-height caveat as lion_payment_history: with nothing paired,
    # device_cards_container has no children and is absent from the dump.
    check("3.11", "Inbox has the COLLARED DEVICES section", d.find("COLLARED DEVICES") is not None)
    check("3.11", "subscription/history no longer live on Inbox", "sub_status_text" not in present)


def step_kebab(d: UiDevice) -> None:
    goto_tab(d, "tab_lock")
    if not d.tap("id/btn_kebab"):
        check("3.12", "kebab opens", False)
        return
    time.sleep(1.2)
    check("3.12", "kebab opens", True)
    for title in ("Bunnies", "Vault Nodes", "Web Remote", "Setup", "App PIN"):
        check("3.12", f"kebab offers {title}", d.visible(title))
    check("3.12", "Payment Email hidden with no homelab", not d.visible("Payment Email"))
    check("3.13", "Release Forever still reachable", d.visible("RELEASE FOREVER"))

    d.tap("RELEASE FOREVER")
    time.sleep(1.5)
    dialog = d.nodes()
    asks = any("cancel" in n["text"].lower() for n in dialog)
    check("3.13", "Release Forever confirms before doing anything", asks)
    if not d.tap("Cancel"):
        d.back()
    # One more back closes the popup itself; if that pops the Activity too,
    # bring it straight back rather than leaving the launcher on screen.
    d.back()
    if PKG not in d.current_activity():
        d.launch(PKG, ACTIVITY, settle=5)


def step_screens(d: UiDevice, out: Path) -> None:
    out.mkdir(parents=True, exist_ok=True)
    # Re-assert the app is in front. Dismissing the kebab's confirm dialog can
    # leave the launcher resumed, and a screenshot taken then is a picture of
    # the home screen — which is exactly what the first run produced.
    if PKG not in d.current_activity():
        d.launch(PKG, ACTIVITY, settle=6)
    check("3.17", "app is in front for screenshots", PKG in d.current_activity(), d.current_activity())
    for tab, name in (("tab_lock", "lock"), ("tab_rules", "rules"), ("tab_money", "money"), ("tab_inbox", "inbox")):
        goto_tab(d, tab)
        time.sleep(1.0)
        ok = d.screenshot(out / f"lions-share-83-{name}.png")
        check("3.17", f"screenshot {name}", ok, str(out / f"lions-share-83-{name}.png"))


STEPS = [
    ("launch", step_launch),
    ("lock", step_lock_tab),
    ("rules", step_rules_tab),
    ("compliment", step_compliment_reveal),
    ("money", step_money_tab),
    ("inbox", step_inbox_tab),
    ("kebab", step_kebab),
]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--serial", required=True)
    ap.add_argument("--install", action="store_true", help="reinstall the APK first")
    ap.add_argument("--out", default=str(REPO / "docs" / "Screenshots"))
    ap.add_argument("--json", default=str(REPO / "staging" / "qa-83-result.json"))
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    d = UiDevice(args.serial, verbose=args.verbose)

    if args.install:
        print(f"== installing {APK.name} ==")
        r = subprocess.run(
            ["adb", "-s", args.serial, "install", "-r", "-g", str(APK)], capture_output=True, text=True, timeout=300
        )
        if "Success" not in r.stdout:
            print(f"  install failed: {r.stdout.strip()} {r.stderr.strip()}")
            return 2
        print("  installed")

    for name, fn in STEPS:
        print(f"\n== {name} ==")
        try:
            fn(d)
        except Abort as e:
            check(name, "prerequisite", False, str(e))
            print("\n  ABORTED — the app is not on screen; later checks would all fail for that one reason.")
            break
        except Exception as e:  # a step blowing up must not hide the rows that passed
            check(name, "step completed", False, f"{type(e).__name__}: {e}")

    print("\n== screenshots ==")
    try:
        step_screens(d, Path(args.out))
    except Exception as e:
        check("3.17", "screenshots", False, str(e))

    passed = sum(1 for r in results if r["ok"])
    failed = [r for r in results if not r["ok"]]
    Path(args.json).write_text(json.dumps({"passed": passed, "failed": len(failed), "results": results}, indent=2))

    print(f"\n{'=' * 60}\n  {passed} passed, {len(failed)} failed")
    for r in failed:
        print(f"    FAIL {r['row']:6} {r['check']}" + (f"  — {r['detail']}" if r["detail"] else ""))
    print(f"  report: {args.json}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

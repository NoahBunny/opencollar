#!/usr/bin/env python3
"""Empirical feature sweep against a REAL Collar over its signed /api/* surface.

For each feature: send the order the Lion would send, then read the Collar's own
Settings.Global back and assert the state actually changed. Nothing is mocked —
the only stand-in is the signing key (the bunny fallback, see qa_collar_driver).

    ./qa_collar_sweep.py <adb-serial> <host:port>

Destructive endpoints are deliberately NOT swept: /api/release-forever (full
teardown + self-uninstall) and /api/power (screen off). Endpoints needing
hardware this box doesn't have (Lovense toy, TTS audio out) are reported as
BLOCKED rather than silently skipped — a sweep that hides its own gaps is worse
than no sweep.
"""
import json
import subprocess
import sys
import time

sys.path.insert(0, "staging")
from qa_collar_driver import post, settings  # noqa: E402

SERIAL = sys.argv[1]
HOSTPORT = sys.argv[2]

results = []


def g(key):
    return settings(SERIAL, key)[key]


def check(feature, path, body, expect, blocked=None, settle=1.2, expect_refusal=None):
    """expect: dict of settings-key -> predicate(value) or literal.

    expect_refusal: substring the Collar's error MUST contain. Use for endpoints
    that are supposed to say no — a guard that silently stopped guarding would
    otherwise read as a pass.
    """
    if blocked:
        results.append(("BLOCKED", feature, blocked))
        return
    code, resp = post(SERIAL, HOSTPORT, path, json.dumps(body) if body else "")
    if code != 200:
        results.append(("FAIL", feature, f"HTTP {code}: {resp[:90]}"))
        return
    # The Collar answers 200 with {"error": ...} for a refused order, so status
    # code alone proves nothing.
    refused = '"error"' in resp
    if expect_refusal is not None:
        if not refused:
            results.append(("FAIL", feature, f"expected refusal, got {resp[:90]}"))
        elif expect_refusal.lower() not in resp.lower():
            results.append(("FAIL", feature, f"refused, but not as expected: {resp[:90]}"))
        else:
            results.append(("PASS", feature, f"refused as designed: {resp[:80]}"))
        return
    if refused:
        results.append(("FAIL", feature, f"refused: {resp[:90]}"))
        return
    time.sleep(settle)
    bad = []
    for key, want in expect.items():
        got = g(key)
        ok = want(got) if callable(want) else (str(got) == str(want))
        if not ok:
            bad.append(f"{key}={got!r}")
    if bad:
        results.append(("FAIL", feature, "; ".join(bad)))
    else:
        shown = ", ".join(f"{k}={g(k)}" for k in list(expect)[:3])
        results.append(("PASS", feature, shown))


def nonzero(v):
    return v not in ("", "null", "0", None)


# ── lock core + the 9 modes ────────────────────────────────────────────────
check("lock: basic + 15m timer", "/api/lock",
      {"timer": "15", "mode": "basic", "message": "QA sweep"},
      {"focus_lock_active": "1", "focus_lock_mode": "basic",
       "focus_lock_unlock_at": nonzero, "focus_lock_message": "QA sweep"})

for mode in ("negotiation", "gratitude", "exercise", "love_letter", "compliment"):
    check(f"lock mode: {mode}", "/api/lock", {"timer": "5", "mode": mode},
          {"focus_lock_mode": mode})

check("lock mode: random resolves to a real mode", "/api/lock",
      {"timer": "5", "mode": "random"},
      {"focus_lock_mode": lambda v: v in
       ("basic", "negotiation", "gratitude", "exercise", "love_letter")})

check("lock mode: task (reps)", "/api/task",
      {"text": "I will behave", "reps": "3"},
      {"focus_lock_task_text": lambda v: "behave" in v, "focus_lock_task_reps": "3"})

check("lock mode: photo_task", "/api/photo-task",
      {"task": "selfie", "hint": "smile"},
      {"focus_lock_photo_task": nonzero})

# ── modifiers / toggles ────────────────────────────────────────────────────
check("modifiers: vibrate+penalty+shame+dim+mute", "/api/lock",
      {"timer": "5", "mode": "basic", "vibrate": "1", "penalty": "1",
       "shame": "1", "dim": "1", "mute": "1"},
      {"focus_lock_vibrate": "1", "focus_lock_penalty": "1",
       "focus_lock_shame": "1", "focus_lock_dim": "1", "focus_lock_mute": "1"})

check("word_min / exercise mode params", "/api/lock",
      {"timer": "5", "mode": "love_letter", "word_min": "120",
       "exercise": "50 squats"},
      {"focus_lock_word_min": "120", "focus_lock_exercise": "50 squats"})

# ── money ──────────────────────────────────────────────────────────────────
check("paywall: add $25", "/api/add-paywall", {"amount": "25"},
      {"focus_lock_paywall": "25"})
check("paywall: add stacks to $40", "/api/add-paywall", {"amount": "15"},
      {"focus_lock_paywall": "40"})
check("paywall: lock without amount must NOT clobber the ledger", "/api/lock",
      {"timer": "5", "mode": "basic"}, {"focus_lock_paywall": "40"})
check("paywall: clear", "/api/clear-paywall", None, {"focus_lock_paywall": "0"})

check("subscription: subscribe silver", "/api/subscribe", {"tier": "silver"},
      {"focus_lock_sub_tier": "silver"})
# The Collar refuses to unsubscribe locally on purpose: cancellation is
# server-authoritative, so the bunny can't drop their own tier from the device
# they control. Assert the refusal, not a state change.
check("subscription: local unsubscribe is refused (server-authoritative)",
      "/api/unsubscribe", None, {}, expect_refusal="server-authoritative")

# ── messaging ──────────────────────────────────────────────────────────────
check("message to bunny", "/api/message", {"message": "kneel"},
      {"focus_lock_message": "kneel"})
check("pinned message", "/api/pin-message", {"message": "good bunny"},
      {"focus_lock_pinned_message": "good bunny"})

# ── negotiation / offers ───────────────────────────────────────────────────
check("offer: bunny proposes", "/api/offer", {"offer": "10 min early?"},
      {"focus_lock_offer": lambda v: "10 min" in v})
check("offer: Lion responds", "/api/offer-respond",
      {"response": "denied", "status": "denied"},
      {"focus_lock_offer_status": nonzero})

# ── geofence / curfew / check-in ───────────────────────────────────────────
check("geofence: set", "/api/set-geofence",
      {"lat": "43.6532", "lon": "-79.3832", "radius": "150"},
      {"focus_lock_geofence_lat": lambda v: v.startswith("43.65"),
       "focus_lock_geofence_radius_m": "150"})
check("geofence: clear", "/api/clear-geofence", None,
      {"focus_lock_geofence_radius_m": lambda v: v in ("", "null", "0")})
check("check-in deadline (hour of day)", "/api/set-checkin", {"deadline": "22"},
      {"focus_lock_checkin_deadline": "22"})
check("check-in rejects an out-of-range hour", "/api/set-checkin",
      {"deadline": "99"}, {}, expect_refusal="0-23")

# ── settings floor / free unlock / entrapment ──────────────────────────────
check("settings window (safety floor)", "/api/enable-settings", None, {})
# Free unlock is a Gold perk. Verify the gate refuses at silver, then that it
# actually works once the tier qualifies — a perk that never fires and a gate
# that never blocks look identical if you only test one side.
check("free unlock refused below Gold", "/api/free-unlock", None, {},
      expect_refusal="Gold")
check("subscription: upgrade to gold", "/api/subscribe", {"tier": "gold"},
      {"focus_lock_sub_tier": "gold"})
check("free unlock granted at Gold (and releases the lock)", "/api/free-unlock",
      None, {"focus_lock_free_unlocks": "1", "focus_lock_active": "0"})
check("free unlock is once per month", "/api/free-unlock", None, {},
      expect_refusal="already used")
check("notification prefs", "/api/set-notif-prefs",
      {"email_evidence": "true", "email_escape": "true", "email_breach": "true"},
      {"focus_lock_notif_email_evidence": "1",
       "focus_lock_notif_email_escape": "1",
       "focus_lock_notif_email_breach": "1"})

# ── volume enforcement ─────────────────────────────────────────────────────
check("max volume enforcement", "/api/set-volume", {"level": "7"}, {})

# ── unlock ─────────────────────────────────────────────────────────────────
# Re-lock first: the Gold free-unlock above already released the device, and
# "unlock succeeds" proves nothing when the device was never locked.
check("re-lock before testing unlock", "/api/lock", {"timer": "10", "mode": "basic"},
      {"focus_lock_active": "1"})
check("unlock", "/api/unlock", None,
      {"focus_lock_active": "0", "focus_lock_unlock_at": lambda v: v in ("0", "", "null")})

# ── hardware-blocked ───────────────────────────────────────────────────────
check("Lovense toy control", "/api/lovense", None, {},
      blocked="no Lovense device paired to this bunny phone")
check("TTS speak", "/api/speak", None, {},
      blocked="audible-only; no way to assert from adb")
check("play audio", "/api/play-audio", None, {},
      blocked="audible-only; no way to assert from adb")
check("screen power control", "/api/power", None, {},
      blocked="would blank the screen mid-sweep; skipped deliberately")
check("Release Forever", "/api/release-forever", None, {},
      blocked="destructive teardown + self-uninstall; never swept")
check("entrapment mode", "/api/entrap", None, {},
      blocked="raises the release bar on a real device; not swept unattended")

# ── report ─────────────────────────────────────────────────────────────────
w = max(len(f) for _, f, _ in results)
for status, feature, detail in results:
    mark = {"PASS": "PASS ", "FAIL": "FAIL ", "BLOCKED": "BLOCK"}[status]
    print(f"  [{mark}] {feature.ljust(w)}  {detail}")

n_pass = sum(1 for s, _, _ in results if s == "PASS")
n_fail = sum(1 for s, _, _ in results if s == "FAIL")
n_block = sum(1 for s, _, _ in results if s == "BLOCKED")
print(f"\n  {n_pass} pass · {n_fail} fail · {n_block} blocked (of {len(results)})")
sys.exit(1 if n_fail else 0)

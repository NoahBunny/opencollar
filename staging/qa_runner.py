#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
Staging QA Runner — drives mesh order flows against the staging relay.

Acts as a scripted Lion: hits /admin/order with the staging admin_token to
exercise lock/unlock/paywall/subscribe/message paths. Verifies state
transitions via /admin/status. No GUI required.

This complements but does NOT replace on-device QA (see docs/MANUAL-QA.md).
The radio/sensor flows (SMS, Lovense, GPS) are inherently device-level.

Usage:
    python3 staging/qa_runner.py [--relay URL] [--config staging/config.json]

Exits 0 on full pass, 1 on any failure.
"""

import argparse
import base64
import json
import os
import sys
import time
import urllib.error
import urllib.request

# canonical_json + signing for the post-audit-C1 mesh-order auth gate.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import focuslock_mesh as _mesh

try:
    from cryptography.hazmat.primitives import hashes as _hashes
    from cryptography.hazmat.primitives import serialization as _serialization
    from cryptography.hazmat.primitives.asymmetric import padding as _asym_padding
except ImportError:
    _serialization = None  # type: ignore[assignment]


_LION_PRIVKEY = None


def _load_lion_privkey(path):
    global _LION_PRIVKEY
    if _serialization is None:
        return None
    if _LION_PRIVKEY is None and os.path.exists(path):
        with open(path, "rb") as f:
            _LION_PRIVKEY = _serialization.load_pem_private_key(f.read(), password=None)
    return _LION_PRIVKEY


def _sign_action(action, params):
    """Sign canonical({action, params}) — matches mesh.handle_mesh_order's gate."""
    if _LION_PRIVKEY is None:
        return ""
    data = _mesh.canonical_json({"action": action, "params": params or {}})
    sig = _LION_PRIVKEY.sign(data, _asym_padding.PKCS1v15(), _hashes.SHA256())
    return base64.b64encode(sig).decode()


def _post(relay, path, body, timeout=5):
    req = urllib.request.Request(
        relay + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            err_body = json.loads(e.read().decode())
        except Exception:
            err_body = {"raw": "could-not-decode"}
        return e.code, err_body


def _get(relay, path, timeout=5):
    try:
        with urllib.request.urlopen(relay + path, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, {"error": str(e)}


def _admin_order(relay, token, action, params=None):
    body = {
        "admin_token": token,
        "action": action,
        "params": params or {},
    }
    # Post-audit-C1 the mesh-order handler requires PIN-or-Lion-signature
    # whenever the orders doc has either set. Sign with the staging Lion
    # privkey if available so /admin/order can drive the mesh handler.
    sig = _sign_action(action, params or {})
    if sig:
        body["signature"] = sig
    return _post(relay, "/admin/order", body)


def _status(relay, token):
    return _get(relay, f"/admin/status?admin_token={token}")


# ── Test scaffold ─────────────────────────────────────────────


class _Result:
    __slots__ = ("detail", "name", "ok")

    def __init__(self, name, ok, detail=""):
        self.name = name
        self.ok = ok
        self.detail = detail

    def __str__(self):
        marker = "PASS" if self.ok else "FAIL"
        return f"  [{marker}] {self.name}{(' — ' + self.detail) if self.detail else ''}"


def _run_test(name, fn):
    try:
        ok, detail = fn()
        return _Result(name, ok, detail)
    except Exception as e:
        return _Result(name, False, f"raised {type(e).__name__}: {e}")


# ── Tests ─────────────────────────────────────────────────────


def tests_relay_health(relay, token):
    """Section 0 — staging relay health."""

    def t_version():
        code, body = _get(relay, "/version")
        if code != 200:
            return False, f"HTTP {code}"
        if body.get("service") != "focuslock-mail":
            return False, f"unexpected service: {body.get('service')}"
        return True, f"v{body.get('version')}, uptime {body.get('uptime_s')}s"

    def t_admin_status_authenticated():
        code, body = _status(relay, token)
        if code != 200:
            return False, f"HTTP {code}: {body}"
        if "orders" not in body:
            return False, "no 'orders' field"
        return True, f"orders v{body.get('orders_version')}"

    def t_admin_status_unauthenticated():
        code, _body = _get(relay, "/admin/status?admin_token=wrong")
        if code != 403:
            return False, f"expected 403, got {code}"
        return True, "unauthenticated correctly rejected"

    return [
        _run_test("0.1 /version responds", t_version),
        _run_test("0.2 /admin/status with valid token", t_admin_status_authenticated),
        _run_test("0.3 /admin/status with bad token rejected", t_admin_status_unauthenticated),
    ]


def tests_lock_unlock(relay, token):
    """Section 2 — lock/unlock core."""

    def t_lock():
        code, body = _admin_order(relay, token, "lock", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.5)
        _, st = _status(relay, token)
        is_locked = st.get("locked") or str(st.get("orders", {}).get("lock_active")) == "1"
        if not is_locked:
            return False, f"lock_active not 1: {st.get('orders', {}).get('lock_active')}"
        return True, "lock_active=1"

    def t_unlock():
        code, body = _admin_order(relay, token, "unlock", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.5)
        _, st = _status(relay, token)
        if st.get("locked"):
            return False, "still locked after unlock"
        return True, "lock_active=0"

    return [
        _run_test("2.1 lock order applies", t_lock),
        _run_test("2.4 unlock order clears", t_unlock),
    ]


def tests_paywall(relay, token):
    """Section 3 — paywall add/clear."""

    def _current_paywall():
        _, st = _status(relay, token)
        return float(st.get("orders", {}).get("paywall", 0) or 0)

    def t_add_paywall():
        before = _current_paywall()
        code, body = _admin_order(relay, token, "add-paywall", {"amount": 25})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.5)
        after = _current_paywall()
        if after != before + 25:
            return False, f"expected {before + 25}, got {after}"
        return True, f"{before} → {after}"

    def t_clear_paywall():
        code, body = _admin_order(relay, token, "clear-paywall", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.5)
        cur = _current_paywall()
        if cur != 0:
            return False, f"expected 0, got {cur}"
        return True, "paywall cleared"

    def t_negative_amount_safe():
        # add-paywall with negative should not turn paywall negative
        before = _current_paywall()
        _admin_order(relay, token, "add-paywall", {"amount": -50})
        time.sleep(0.3)
        after = _current_paywall()
        if after < 0:
            return False, f"paywall went negative: {after}"
        return True, f"{before} → {after} (clamped or no-op)"

    return [
        _run_test("3.1 add-paywall increases", t_add_paywall),
        _run_test("3.6 clear-paywall zeros", t_clear_paywall),
        _run_test("3.x negative amount doesn't go negative", t_negative_amount_safe),
    ]


def tests_subscription(relay, token):
    """Section 6 — subscriptions."""

    def t_subscribe_gold():
        code, body = _admin_order(relay, token, "subscribe", {"tier": "gold"})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.5)
        _, st = _status(relay, token)
        tier = st.get("orders", {}).get("sub_tier") or st.get("sub_tier")
        if tier != "gold":
            return False, f"tier not gold: {tier}"
        sub_due = st.get("orders", {}).get("sub_due", 0)
        # sub_due should be ~now + 7d (in ms)
        if not sub_due or sub_due < int(time.time() * 1000):
            return False, f"sub_due not set or in the past: {sub_due}"
        return True, f"tier=gold, sub_due in {(sub_due - int(time.time() * 1000)) // (1000 * 86400)}d"

    return [
        _run_test("6.1 subscribe to gold", t_subscribe_gold),
    ]


def tests_messaging(relay, token):
    """Section 13 — pinned messages."""

    def t_pin_message():
        msg = f"qa-test-pin-{int(time.time())}"
        code, body = _admin_order(relay, token, "pin-message", {"message": msg})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        pinned = st.get("orders", {}).get("pinned_message", "")
        if msg not in pinned:
            return False, f"pinned message mismatch: {pinned!r}"
        return True, "message pinned"

    def t_clear_pinned():
        code, body = _admin_order(relay, token, "clear-pinned-message", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        pinned = st.get("orders", {}).get("pinned_message", "")
        if pinned:
            return False, f"pinned not cleared: {pinned!r}"
        return True, "pinned cleared"

    return [
        _run_test("13.x pin-message", t_pin_message),
        _run_test("13.x clear-pinned-message", t_clear_pinned),
    ]


def tests_release_safety(relay, token):
    """Spot-check: release-forever requires explicit confirmation, doesn't fire on stray call."""

    def t_release_protected():
        # Without a target device or confirmation field, release should be safe
        code, body = _admin_order(relay, token, "release-device", {})
        # Either error response or ok with no-op — the test is that we don't crash and don't
        # accidentally release everything.
        if code >= 500:
            return False, f"server error on release call: {code} {body}"
        return True, f"safe response ({code})"

    return [
        _run_test("12.x release-device call doesn't 500", t_release_protected),
    ]


def tests_lock_modes(relay, token):
    """Every lock mode the controller exposes — verify each lands the mode in orders."""

    modes = [
        "",  # basic
        "negotiation",
        "task",
        "compliment",
        "quiz",
        "gratitude",
        "exercise",
        "love_letter",
        "random",
    ]

    def make_check(mode):
        def t():
            code, body = _admin_order(relay, token, "lock", {"mode": mode, "message": f"qa-mode-{mode}"})
            if code != 200 or not body.get("ok"):
                return False, f"order failed: {code} {body}"
            time.sleep(0.3)
            _, st = _status(relay, token)
            applied_mode = st.get("orders", {}).get("mode", "")
            # Server stores empty mode for "basic"
            expected = mode
            if applied_mode != expected:
                return False, f"mode={applied_mode!r}, expected {expected!r}"
            return True, f"mode={applied_mode or '(basic)'}"

        return t

    return [_run_test(f"2.{i + 2} lock mode={(m or 'basic')}", make_check(m)) for i, m in enumerate(modes)]


def tests_scheduling(relay, token):
    """Bedtime + screen-time set/clear round-trip via mesh actions."""

    def t_set_bedtime():
        code, body = _admin_order(relay, token, "set-bedtime", {"lock_hour": 23, "unlock_hour": 7})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        o = st.get("orders", {})
        if (
            int(o.get("bedtime_enabled", 0)) != 1
            or int(o.get("bedtime_lock_hour", -1)) != 23
            or int(o.get("bedtime_unlock_hour", -1)) != 7
        ):
            return (
                False,
                f"bedtime fields wrong: {o.get('bedtime_enabled')}/{o.get('bedtime_lock_hour')}/{o.get('bedtime_unlock_hour')}",
            )
        return True, "bedtime 23:00→7:00"

    def t_clear_bedtime():
        code, body = _admin_order(relay, token, "clear-bedtime", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if int(st.get("orders", {}).get("bedtime_enabled", 1)) != 0:
            return False, "bedtime still enabled"
        return True, "bedtime cleared"

    def t_set_screen_time():
        code, body = _admin_order(relay, token, "set-screen-time", {"quota_minutes": 90, "reset_hour": 4})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if int(st.get("orders", {}).get("screen_time_quota_minutes", 0)) != 90:
            return False, f"quota_minutes wrong: {st.get('orders', {}).get('screen_time_quota_minutes')}"
        return True, "quota=90"

    def t_clear_screen_time():
        code, body = _admin_order(relay, token, "clear-screen-time", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if int(st.get("orders", {}).get("screen_time_quota_minutes", 1)) != 0:
            return False, "screen-time still set"
        return True, "screen-time cleared"

    return [
        _run_test("4.1 set-bedtime 23→7", t_set_bedtime),
        _run_test("4.2 clear-bedtime", t_clear_bedtime),
        _run_test("4.3 set-screen-time 90m", t_set_screen_time),
        _run_test("4.4 clear-screen-time", t_clear_screen_time),
    ]


def tests_geofence(relay, token):
    """set-geofence stores lat/lon/radius; basic round-trip."""

    def t_set_geofence():
        params = {"lat": 45.5017, "lon": -73.5673, "radius": 250}
        code, body = _admin_order(relay, token, "set-geofence", params)
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        o = st.get("orders", {})
        # set-geofence writes geofence_lat / geofence_lon / geofence_radius
        keys_present = sum(1 for k in ("geofence_lat", "geofence_lon", "geofence_radius_m") if k in o)
        if keys_present != 3:
            return False, f"only {keys_present}/3 geofence keys present: {[k for k in o if k.startswith('geofence_')]}"
        return (
            True,
            f"geofence written (lat={o.get('geofence_lat')}, lon={o.get('geofence_lon')}, r={o.get('geofence_radius_m')}m)",
        )

    return [_run_test("5.1 set-geofence", t_set_geofence)]


def tests_tribute(relay, token):
    """set-tribute / clear-tribute via /mesh/order action."""

    def t_set_tribute():
        code, body = _admin_order(relay, token, "set-tribute", {"amount": 5})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if (
            int(st.get("orders", {}).get("tribute_amount", 0)) != 5
            or int(st.get("orders", {}).get("tribute_active", 0)) != 1
        ):
            return (
                False,
                f"tribute fields wrong: amount={st.get('orders', {}).get('tribute_amount')} active={st.get('orders', {}).get('tribute_active')}",
            )
        return True, "tribute=5/day, active"

    def t_clear_tribute():
        code, body = _admin_order(relay, token, "clear-tribute", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if int(st.get("orders", {}).get("tribute_active", 1)) != 0:
            return False, "tribute still active"
        return True, "tribute cleared"

    return [
        _run_test("7.1 set-tribute $5/day", t_set_tribute),
        _run_test("7.2 clear-tribute", t_clear_tribute),
    ]


def tests_streak(relay, token):
    """start-streak / stop-streak."""

    def t_start_streak():
        code, body = _admin_order(relay, token, "start-streak", {"escapes": 0})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if int(st.get("orders", {}).get("streak_enabled", 0)) != 1:
            return False, "streak not enabled"
        return True, "streak started"

    def t_stop_streak():
        code, body = _admin_order(relay, token, "stop-streak", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if int(st.get("orders", {}).get("streak_enabled", 1)) != 0:
            return False, "streak still enabled"
        return True, "streak stopped"

    return [
        _run_test("8.1 start-streak (escapes=0)", t_start_streak),
        _run_test("8.2 stop-streak", t_stop_streak),
    ]


def tests_lion_pinned(relay, token):
    """pin-lion-message / clear-lion-pinned-message — Lion's private notes overlay."""

    def t_pin_lion():
        msg = f"qa-lion-pin-{int(time.time())}"
        code, body = _admin_order(relay, token, "pin-lion-message", {"message": msg})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        pinned = st.get("orders", {}).get("lion_pinned_message", "")
        if msg not in pinned:
            return False, f"lion-pinned mismatch: {pinned!r}"
        return True, "lion-pinned set"

    def t_clear_lion():
        code, body = _admin_order(relay, token, "clear-lion-pinned-message", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if st.get("orders", {}).get("lion_pinned_message", ""):
            return False, "lion-pinned not cleared"
        return True, "lion-pinned cleared"

    return [
        _run_test("13.3 pin-lion-message", t_pin_lion),
        _run_test("13.4 clear-lion-pinned-message", t_clear_lion),
    ]


def tests_send_message(relay, token):
    """send-message — text + pinned + mandatory_reply combinations."""

    def t_send_plain():
        msg = f"qa-msg-{int(time.time())}"
        code, body = _admin_order(relay, token, "send-message", {"text": msg})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.3)
        _, st = _status(relay, token)
        if msg not in st.get("orders", {}).get("message", ""):
            return False, f"message field mismatch: {st.get('orders', {}).get('message')!r}"
        return True, "message stored"

    def t_send_pinned_mandatory():
        msg = f"qa-mandatory-{int(time.time())}"
        code, body = _admin_order(
            relay,
            token,
            "send-message",
            {"text": msg, "pinned": True, "mandatory_reply": True},
        )
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        return True, "pinned + mandatory accepted"

    return [
        _run_test("13.1 send-message plain", t_send_plain),
        _run_test("13.2 send-message pinned+mandatory", t_send_pinned_mandatory),
    ]


def tests_gamble(relay, token):
    """admin-authed gamble (web UI relay-mode entry point)."""

    def t_gamble_no_paywall():
        # /admin/order's gamble path requires paywall>0; with 0 it should 409
        _admin_order(relay, token, "clear-paywall", {})
        time.sleep(0.3)
        code, body = _admin_order(relay, token, "gamble", {})
        if code == 409:
            return True, "no paywall → 409 as expected"
        # Some paths may return 200 with error
        if "error" in body:
            return True, f"safe response: {body.get('error')}"
        return False, f"unexpected: {code} {body}"

    def t_gamble_with_paywall():
        _admin_order(relay, token, "add-paywall", {"amount": 100})
        time.sleep(0.3)
        code, body = _admin_order(relay, token, "gamble", {})
        if code != 200:
            return False, f"order failed: {code} {body}"
        result = body.get("result", "")
        if result not in ("heads", "tails"):
            return False, f"unexpected result: {result}"
        return True, f"resolved={result}, new_paywall={body.get('paywall', '?')}"

    return [
        _run_test("9.1 gamble with no paywall is safe", t_gamble_no_paywall),
        _run_test("9.2 gamble with paywall flips coin", t_gamble_with_paywall),
    ]


def tests_paywall_paths(relay, token):
    """Variations: small/large/negative/non-numeric add-paywall."""

    def _current():
        _, st = _status(relay, token)
        return float(st.get("orders", {}).get("paywall", 0) or 0)

    def t_small():
        _admin_order(relay, token, "clear-paywall", {})
        time.sleep(0.2)
        code, body = _admin_order(relay, token, "add-paywall", {"amount": 1})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.2)
        if _current() != 1:
            return False, f"expected 1.0, got {_current()}"
        return True, "$1 add OK"

    def t_large():
        _admin_order(relay, token, "clear-paywall", {})
        time.sleep(0.2)
        code, body = _admin_order(relay, token, "add-paywall", {"amount": 1000})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        time.sleep(0.2)
        if _current() != 1000:
            return False, f"expected 1000, got {_current()}"
        return True, "$1000 add OK"

    def t_non_numeric():
        before = _current()
        # Server should clamp to 0
        _admin_order(relay, token, "add-paywall", {"amount": "not-a-number"})
        time.sleep(0.2)
        after = _current()
        if after != before:
            return False, f"non-numeric mutated paywall: {before} → {after}"
        return True, f"non-numeric ignored ({before})"

    return [
        _run_test("3.x add-paywall $1", t_small),
        _run_test("3.x add-paywall $1000", t_large),
        _run_test("3.x add-paywall non-numeric ignored", t_non_numeric),
    ]


def tests_subscription_tiers(relay, token):
    """All three subscription tiers."""

    def make(tier):
        def t():
            code, body = _admin_order(relay, token, "subscribe", {"tier": tier})
            if code != 200 or not body.get("ok"):
                return False, f"order failed: {code} {body}"
            time.sleep(0.3)
            _, st = _status(relay, token)
            actual = st.get("orders", {}).get("sub_tier", "")
            if actual != tier:
                return False, f"sub_tier={actual!r}, expected {tier!r}"
            return True, f"tier={tier}"

        return t

    def t_unsubscribe():
        code, body = _admin_order(relay, token, "unsubscribe-charge", {})
        # Response either ok or "no active subscription"; both are valid
        if code != 200:
            return False, f"order failed: {code} {body}"
        return True, body.get("error") or f"fee={body.get('fee', 0)}"

    return [
        _run_test("6.2 subscribe bronze", make("bronze")),
        _run_test("6.3 subscribe silver", make("silver")),
        _run_test("6.4 subscribe gold", make("gold")),
        _run_test("6.5 unsubscribe-charge", t_unsubscribe),
    ]


def tests_lan_only_rejected_in_relay_mode(relay, token):
    """Sanity: LAN-only actions don't have orders mappings on the relay path.
    They're defined as web-UI client-side rejections, so the relay just receives
    no-op-style action='play-audio' etc. — confirm they don't 500 the server."""

    def make(action, params):
        def t():
            code, body = _admin_order(relay, token, action, params)
            if code >= 500:
                return False, f"server error: {code} {body}"
            return True, f"safe ({code})"

        return t

    return [
        _run_test("11.1 play-audio safe", make("play-audio", {"url": "https://example.test/a.mp3"})),
        _run_test("11.2 speak safe", make("speak", {"text": "hello"})),
        _run_test("11.3 toy safe", make("toy", {"action": "pulse"})),
    ]


def tests_entrap_safety(relay, token):
    """entrap is destructive — the action must not silently fire on bare admin call.
    The web UI always wraps it in a confirm dialog. The relay just routes the order;
    if it does fire, ensure the response is well-formed (not a 500)."""

    def t_entrap():
        code, body = _admin_order(relay, token, "entrap", {})
        if code >= 500:
            return False, f"server error: {code} {body}"
        return True, f"safe response ({code})"

    return [_run_test("10.x entrap call doesn't 500", t_entrap)]


def tests_deadline_task(relay, token):
    """set-deadline-task / deadline-task-cleared."""

    def t_set():
        deadline = int(time.time() * 1000) + 24 * 3600 * 1000  # 24h from now
        code, body = _admin_order(
            relay,
            token,
            "set-deadline-task",
            {"text": "qa-deadline-task", "deadline_ms": deadline, "on_miss": "lock"},
        )
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        return True, "deadline task set"

    def t_clear():
        code, body = _admin_order(relay, token, "deadline-task-cleared", {})
        if code != 200 or not body.get("ok"):
            return False, f"order failed: {code} {body}"
        return True, "deadline task cleared"

    return [
        _run_test("14.1 set-deadline-task", t_set),
        _run_test("14.2 deadline-task-cleared", t_clear),
    ]


# ── Main ──────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="Staging QA runner — scripted Lion driver")
    ap.add_argument(
        "--relay", default="http://127.0.0.1:18435", help="Staging relay base URL (default: http://127.0.0.1:18435)"
    )
    ap.add_argument(
        "--config",
        default=os.path.join(os.path.dirname(__file__), "config.json"),
        help="Path to staging/config.json (for admin_token)",
    )
    args = ap.parse_args()

    # Load admin_token from config — never accept it as a CLI arg (avoids ps leakage)
    if not os.path.exists(args.config):
        print(f"ERROR: {args.config} not found. See docs/STAGING.md.", file=sys.stderr)
        return 2
    with open(args.config) as f:
        cfg = json.load(f)
    token = cfg.get("admin_token", "")
    if not token or token.startswith("REPLACE_"):
        print(f"ERROR: admin_token in {args.config} is unset or template placeholder.", file=sys.stderr)
        return 2

    # Load Lion privkey for the mesh-handler signature gate (post-audit-C1).
    privkey_path = os.path.join(os.path.dirname(args.config), "lion_privkey.pem")
    if _load_lion_privkey(privkey_path) is None:
        print(f"WARN: {privkey_path} not loaded — order-dispatch tests may fail the auth gate.", file=sys.stderr)

    print(f"Staging QA Runner — driving {args.relay}")
    print(f"Mesh: {cfg.get('mesh_id', '?')}  Vault mode: {cfg.get('vault_mode', False)}")
    print()

    sections = [
        ("0. Relay health", tests_relay_health),
        ("2. Lock / unlock", tests_lock_unlock),
        ("2b. Lock modes (9)", tests_lock_modes),
        ("3. Paywall", tests_paywall),
        ("3b. Paywall variations", tests_paywall_paths),
        ("4. Scheduling (bedtime + screen-time)", tests_scheduling),
        ("5. Geofence", tests_geofence),
        ("6. Subscription", tests_subscription),
        ("6b. Subscription tiers", tests_subscription_tiers),
        ("7. Tribute", tests_tribute),
        ("8. Streak bonus", tests_streak),
        ("9. Gamble", tests_gamble),
        ("10. Entrap safety", tests_entrap_safety),
        ("11. LAN-only actions (no-op via relay)", tests_lan_only_rejected_in_relay_mode),
        ("12. Release safety", tests_release_safety),
        ("13. Pinned messages", tests_messaging),
        ("13b. Lion-only pinned", tests_lion_pinned),
        ("13c. send-message", tests_send_message),
        ("14. Deadline task", tests_deadline_task),
    ]

    total = 0
    failed = 0
    for label, fn in sections:
        print(f"## {label}")
        results = fn(args.relay, token)
        for r in results:
            print(r)
            total += 1
            if not r.ok:
                failed += 1
        print()

    print(f"Summary: {total - failed}/{total} passed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

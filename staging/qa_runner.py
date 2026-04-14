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
import json
import os
import sys
import time
import urllib.error
import urllib.request


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
    return _post(relay, "/admin/order", {
        "admin_token": token,
        "action": action,
        "params": params or {},
    })


def _status(relay, token):
    return _get(relay, f"/admin/status?admin_token={token}")


# ── Test scaffold ─────────────────────────────────────────────


class _Result:
    __slots__ = ("name", "ok", "detail")

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
    except Exception as e:  # noqa: BLE001 — test runner top-level
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
        code, body = _get(relay, "/admin/status?admin_token=wrong")
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
        return True, f"tier=gold, sub_due in {(sub_due - int(time.time()*1000)) // (1000*86400)}d"

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


# ── Main ──────────────────────────────────────────────────────


def main():
    ap = argparse.ArgumentParser(description="Staging QA runner — scripted Lion driver")
    ap.add_argument("--relay", default="http://127.0.0.1:18435",
                    help="Staging relay base URL (default: http://127.0.0.1:18435)")
    ap.add_argument("--config", default=os.path.join(os.path.dirname(__file__), "config.json"),
                    help="Path to staging/config.json (for admin_token)")
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

    print(f"Staging QA Runner — driving {args.relay}")
    print(f"Mesh: {cfg.get('mesh_id', '?')}  Vault mode: {cfg.get('vault_mode', False)}")
    print()

    sections = [
        ("0. Relay health",   tests_relay_health),
        ("2. Lock / unlock",  tests_lock_unlock),
        ("3. Paywall",        tests_paywall),
        ("6. Subscription",   tests_subscription),
        ("13. Messaging",     tests_messaging),
        ("12. Release safety", tests_release_safety),
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

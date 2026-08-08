#!/usr/bin/env python3
"""Drive a real Collar's signed /api/* surface from the laptop, for device QA.

The Collar authenticates direct POSTs with the Audit-C1 scheme (SigVerifier):

    focusctl|<path>|<ts>|<nonce>|<k1=v1&k2=v2...>

signed RSA-PKCS1v15-SHA256, headers X-FL-Ts / X-FL-Nonce / X-FL-Sig. It tries
the Lion pubkey first and falls back to the bunny pubkey — the same-phone
companion signs with its own key. This driver takes the bunny privkey straight
off the device over adb and uses that fallback, so a whole feature sweep can be
run without the Lion's private key (which lives in app-private prefs).

    ./qa_collar_driver.py <adb-serial> <host:port> <path> [json-body]
    ./qa_collar_driver.py R5CT339K1ZL 192.168.199.109:8432 /api/lock \
        '{"duration_min":15,"mode":"basic"}'

Read-only by design about *its own* state: it never writes to the device, it
only exercises the Collar's own endpoints and prints the response.
"""
import base64
import json
import subprocess
import sys
import textwrap
import time
import urllib.request

sys.path.insert(0, "shared")
sys.path.insert(0, ".")
sys.path.insert(0, "tests")

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

from test_http import c1_canonicalize  # the reference canonicalizer


def bunny_privkey_pem(serial):
    """Pull the Collar's own RSA privkey (base64 DER) out of Settings.Global."""
    b64 = subprocess.run(
        ["adb", "-s", serial, "shell", "settings", "get", "global", "focus_lock_bunny_privkey"],
        capture_output=True, text=True, check=True).stdout.strip()
    if not b64 or b64 == "null":
        raise SystemExit("no focus_lock_bunny_privkey on device")
    return ("-----BEGIN PRIVATE KEY-----\n"
            + "\n".join(textwrap.wrap(b64, 64))
            + "\n-----END PRIVATE KEY-----\n")


def post(serial, hostport, path, body="", timeout=10):
    priv = serialization.load_pem_private_key(bunny_privkey_pem(serial).encode(), password=None)
    ts = int(time.time() * 1000)
    nonce = base64.urlsafe_b64encode(ts.to_bytes(8, "big")).decode().rstrip("=")
    canonical = c1_canonicalize(path, body, ts, nonce)
    sig = base64.b64encode(
        priv.sign(canonical.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()

    req = urllib.request.Request(
        f"http://{hostport}{path}", data=body.encode(), method="POST",
        headers={"Content-Type": "application/json", "X-FL-Ts": str(ts),
                 "X-FL-Nonce": nonce, "X-FL-Sig": sig})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def settings(serial, *keys):
    """Snapshot Settings.Global values for the given focus_lock_* keys."""
    out = {}
    for k in keys:
        v = subprocess.run(
            ["adb", "-s", serial, "shell", "settings", "get", "global", k],
            capture_output=True, text=True).stdout.strip()
        out[k] = v
    return out


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        raise SystemExit(2)
    code, resp = post(sys.argv[1], sys.argv[2], sys.argv[3],
                      sys.argv[4] if len(sys.argv) > 4 else "")
    print(f"HTTP {code}  {resp[:400]}")

#!/usr/bin/env python3
"""Empirical messaging QA against a REAL relay, signed with a REAL device key.

Exercises /api/mesh/{id}/messages/{send,fetch,edit,delete} the way the Bunny
Tasker client does — RSA-PKCS1v15-SHA256 over the pipe payload

    {mesh}|{node}|{from}|{text}|{pinned}|{mandatory}|{ts}

signed with the bunny privkey pulled off the device over adb. Beyond the happy
path it probes the guards that actually matter: can the Bunny forge a message
from the Lion, rewrite history, replay, or slip a tampered body past the
signature?

    ./qa_messaging.py <adb-serial> <relay> <mesh_id> <node_id>
    ./qa_messaging.py R5CT339K1ZL http://127.0.0.1:18435 PRnLhTZI6Ec7 sm-s908w
"""
import base64
import json
import subprocess
import sys
import textwrap
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

SERIAL, RELAY, MESH, NODE = sys.argv[1:5]
results = []


def bunny_priv():
    b64 = subprocess.run(
        ["adb", "-s", SERIAL, "shell", "settings", "get", "global", "focus_lock_bunny_privkey"],
        capture_output=True, text=True, check=True).stdout.strip()
    pem = "-----BEGIN PRIVATE KEY-----\n" + "\n".join(textwrap.wrap(b64, 64)) + "\n-----END PRIVATE KEY-----\n"
    return serialization.load_pem_private_key(pem.encode(), password=None)


PRIV = bunny_priv()


def sign(payload):
    return base64.b64encode(
        PRIV.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()


def call(op, body):
    req = urllib.request.Request(
        f"{RELAY}/api/mesh/{MESH}/messages/{op}", data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        try:
            return e.code, json.loads(e.read() or b"{}")
        except Exception:
            return e.code, {}
    except Exception as e:
        return 0, {"error": f"{type(e).__name__}: {e}"}


def record(name, ok, detail):
    results.append(("PASS" if ok else "FAIL", name, detail))


def send(text, pinned=False, mandatory=False, ts=None, from_who="bunny", corrupt=False):
    ts = ts if ts is not None else int(time.time() * 1000)
    payload = f"{MESH}|{NODE}|{from_who}|{text}|{'1' if pinned else '0'}|{'1' if mandatory else '0'}|{ts}"
    body = {"node_id": NODE, "from": from_who, "text": text, "pinned": pinned,
            "mandatory_reply": mandatory, "ts": ts, "signature": sign(payload)}
    if corrupt:
        body["text"] = text + " (tampered in flight)"
    return call("send", body)


def fetch(since=0):
    ts = int(time.time() * 1000)
    payload = f"{MESH}|{NODE}|bunny|{since}|{ts}"
    return call("fetch", {"node_id": NODE, "from": "bunny", "since": since,
                          "ts": ts, "signature": sign(payload)})


# ── happy path ─────────────────────────────────────────────────────────────
stamp = int(time.time())
text = f"Bunny reporting in, my Lion. [{stamp}]"
code, resp = send(text)
record("bunny → Lion: send", code == 200 and resp.get("ok"), f"HTTP {code} {str(resp)[:70]}")

code, resp = fetch()
msgs = resp.get("messages", []) if isinstance(resp, dict) else []
found = any(text in json.dumps(m) for m in msgs)
record("thread round-trip: message is retrievable", code == 200 and found,
       f"HTTP {code}, {len(msgs)} msg(s) in thread")

code, resp = send("Pinned by the bunny", pinned=True)
record("pinned flag accepted", code == 200, f"HTTP {code} {str(resp)[:60]}")

# ── the guards ─────────────────────────────────────────────────────────────
code, resp = send("I am totally the Lion", from_who="lion")
record("bunny CANNOT forge a message from the Lion", code == 403,
       f"HTTP {code} {resp.get('error', '')[:60]}")

ts = int(time.time() * 1000)
payload = f"{MESH}|{NODE}|lion|whatever|0|0|{ts}"
code, resp = call("edit", {"node_id": NODE, "from": "lion", "message_id": "1",
                           "text": "rewritten", "ts": ts, "signature": sign(payload)})
record("bunny CANNOT edit history (Lion-only)", code == 403,
       f"HTTP {code} {resp.get('error', '')[:60]}")

code, resp = call("delete", {"node_id": NODE, "from": "bunny", "message_id": "1",
                             "ts": ts, "signature": sign(payload)})
record("bunny CANNOT delete history (Lion-only)", code == 403,
       f"HTTP {code} {resp.get('error', '')[:60]}")

code, resp = send("tamper target", corrupt=True)
record("tampered body fails signature", code == 403,
       f"HTTP {code} {resp.get('error', '')[:60]}")

code, resp = send("stale replay", ts=int(time.time() * 1000) - 10 * 60 * 1000)
record("stale timestamp rejected (replay window)", code == 403,
       f"HTTP {code} {resp.get('error', '')[:60]}")

code, resp = send("x" * 4001)
record("oversize message rejected", code == 413, f"HTTP {code} {resp.get('error', '')[:60]}")

code, resp = send("")
record("empty message rejected", code == 400, f"HTTP {code} {resp.get('error', '')[:60]}")

# unknown node → no bunny_pubkey on file
ts = int(time.time() * 1000)
payload = f"{MESH}|ghost-node|bunny|hello|0|0|{ts}"
code, resp = call("send", {"node_id": "ghost-node", "from": "bunny", "text": "hello",
                           "ts": ts, "signature": sign(payload)})
record("unregistered node rejected", code == 403, f"HTTP {code} {resp.get('error', '')[:60]}")

w = max(len(n) for _, n, _ in results)
for status, name, detail in results:
    print(f"  [{status}] {name.ljust(w)}  {detail}")
n_fail = sum(1 for s, _, _ in results if s == "FAIL")
print(f"\n  {len(results) - n_fail} pass · {n_fail} fail")
sys.exit(1 if n_fail else 0)

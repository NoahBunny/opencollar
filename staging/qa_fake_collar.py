#!/usr/bin/env python3
"""A second, controllable Collar for device QA.

Speaks just enough of the Collar's direct-mode HTTP surface for Lion's Share to
pair with it and poll it:

  POST /api/pair     -> {"ok":true,"action":"paired","bunny_pubkey":...}
  GET  /mesh/status  -> the real wire shape, bunny-signed

Its whole point is being *dishonest on demand*: /control lets the test flip it
into signing with the wrong key, dropping the signature, or serving a state the
Lion should refuse to believe. That covers the cases a real Collar can't be made
to produce without staging a LAN MITM.

  ./fake_collar.py <bind-ip> <port>
  curl -s localhost:<port+1>/control?mode=forged   # sign with a foreign key
  curl -s localhost:<port+1>/control?mode=unsigned # no signature at all
  curl -s localhost:<port+1>/control?mode=honest
  curl -s localhost:<port+1>/control?locked=1&paywall=7&timer_ms=600000
"""
import base64
import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, "shared")
sys.path.insert(0, ".")

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from focuslock_mesh import sign_orders

BIND = sys.argv[1] if len(sys.argv) > 1 else "0.0.0.0"
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 8433


def _keypair():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv_pem = k.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption()).decode()
    pub_der = k.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo)
    pub_pem = k.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo).decode()
    return priv_pem, pub_pem, base64.b64encode(pub_der).decode()


# The identity this fake Collar pairs with, and a foreign one for forgery tests.
PRIV_PEM, PUB_PEM, PUB_B64 = _keypair()
FOREIGN_PRIV_PEM, _, _ = _keypair()

STATE = {
    "mode": "honest",       # honest | forged | unsigned
    "locked": False,
    "escapes": 0,
    "paywall": "0",
    "timer_ms": 0,
    "task_reps": 0,
    "task_done": 0,
    "offer": "",
    "offer_status": "",
    "sub_tier": "",
    "orders_version": 3,
    "hits": 0,
}
LOCK = threading.Lock()


def status_body():
    with LOCK:
        s = dict(STATE)
        STATE["hits"] += 1

    core = {
        "locked": s["locked"], "escapes": s["escapes"], "paywall": s["paywall"],
        "timer_remaining_ms": s["timer_ms"], "task_reps": s["task_reps"],
        "task_done": s["task_done"], "offer": s["offer"],
        "offer_status": s["offer_status"], "sub_tier": s["sub_tier"],
        "orders_version": s["orders_version"],
    }
    if s["mode"] == "unsigned":
        sig = ""
    elif s["mode"] == "forged":
        sig = sign_orders(core, FOREIGN_PRIV_PEM)   # valid signature, wrong key
    else:
        sig = sign_orders(core, PRIV_PEM)

    # Mirror ControlService.handleMeshStatus's field order exactly: orders_version,
    # the embedded orders doc (with its shadowing key names), signature, then the
    # signed status fields.
    orders = {
        "lock_active": 1 if s["locked"] else 0, "message": "", "task_text": "",
        "task_reps": s["task_reps"], "task_done": s["task_done"], "mode": "basic",
        # deliberately the RAW row, i.e. "" when unset — this is the shadow that
        # broke the first-match rebuild.
        "paywall": "" if s["paywall"] == "0" else s["paywall"],
        "paywall_original": "", "unlock_at": 0, "locked_at": 0,
        "offer": s["offer"], "offer_status": s["offer_status"],
        "sub_tier": s["sub_tier"], "released": "",
    }
    parts = [
        f'"orders_version":{s["orders_version"]}',
        '"orders":' + json.dumps(orders, separators=(",", ":")),
        '"signature":' + json.dumps(sig),
        '"locked":' + ("true" if s["locked"] else "false"),
        f'"escapes":{s["escapes"]}',
        '"paywall":' + json.dumps(s["paywall"]),
        f'"timer_remaining_ms":{s["timer_ms"]}',
        f'"task_reps":{s["task_reps"]}',
        f'"task_done":{s["task_done"]}',
        '"offer":' + json.dumps(s["offer"]),
        '"offer_status":' + json.dumps(s["offer_status"]),
        '"sub_tier":' + json.dumps(s["sub_tier"]),
        f'"addresses":["{BIND}"]',
        '"tailscale_ip":""', '"onion":""', f'"direct_port":{PORT}',
        '"nodes":{"fake-collar":{"type":"phone","online":true,"orders_version":'
        + f'{s["orders_version"]},"status":{{"escapes":{s["escapes"]}}}}}}}',
    ]
    return "{" + ",".join(parts) + "}"


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *a):
        print("REQ " + (fmt % a), flush=True)

    def _send(self, body, code=200):
        raw = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/mesh/status":
            self._send(status_body())
        elif path == "/mesh/ping":
            self._send('{"ok":true,"node_id":"fake-collar","orders_version":3}')
        else:
            self._send('{"error":"not found"}', 404)

    def do_POST(self):
        path = urlparse(self.path).path
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        if path == "/api/pair":
            self._send(json.dumps({
                "ok": True, "action": "paired", "bunny_pubkey": PUB_B64,
                "sms_token": "faketoken", "addresses": [BIND],
                "tailscale_ip": "", "onion": "", "direct_port": PORT,
            }))
        else:
            # Accept and no-op every order so the Lion's send path succeeds.
            self._send('{"ok":true}')


class Control(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def do_GET(self):
        q = parse_qs(urlparse(self.path).query)
        with LOCK:
            for k, v in q.items():
                if k == "mode":
                    STATE["mode"] = v[0]
                elif k in ("locked",):
                    STATE["locked"] = v[0] == "1"
                elif k in ("escapes", "timer_ms", "task_reps", "task_done", "orders_version"):
                    STATE[k] = int(v[0])
                elif k in ("paywall", "offer", "offer_status", "sub_tier"):
                    STATE[k] = v[0]
            snap = dict(STATE)
        body = json.dumps(snap)
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body.encode())


if __name__ == "__main__":
    import hashlib
    fp = hashlib.sha256(base64.b64decode(PUB_B64)).hexdigest()[:16]
    print(f"fake Collar on {BIND}:{PORT}  control on 127.0.0.1:{PORT + 1}")
    print(f"fingerprint: {fp}", flush=True)
    threading.Thread(
        target=lambda: HTTPServer(("127.0.0.1", PORT + 1), Control).serve_forever(),
        daemon=True).start()
    HTTPServer((BIND, PORT), Handler).serve_forever()

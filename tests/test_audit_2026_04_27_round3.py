"""Audit 2026-04-27 round-3 — slave-signed webhooks (M-3 full + M-4 full).

Two endpoints with live slave / companion callers gain a bunny-sig
gate via the existing _verify_slave_signed_webhook helper. Slave APK
bumps v74 → v75 (companion v56 → v57); the relay's 403 hint reports
min_collar_version=75 on these two routes (the other H-2 routes stay
at the default 74 since their signing path didn't change).

- M-3 (full): /webhook/register — slave's phone-home thread
  (ControlService.java:2061+2079) signs the LAN/Tailscale IP report.
  Round-1 added device_id shape validation; this round adds the sig
  gate. Pre-fix, any LAN caller could pollute IP_REGISTRY_FILE with
  spoofed entries.

- M-4 (full): /webhook/verify-photo — slave (FocusActivity, photo
  task) and companion (deadline-task photo proof) both sign on the
  way out. Pre-fix, any caller could submit base64 photos for LLM
  verification — burns operator GPU/CPU + can be used to fingerprint
  the model + can spoof a "passed" verdict.

Test shape mirrors test_audit_2026_04_27_h2_evidence_webhooks.py:
parametrized matrix of {missing sig, bad sig, expired ts, unknown
node, unknown mesh, valid sig} per endpoint.
"""

import base64
import importlib.util
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import HTTPServer
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"

# (canonical-payload-suffix, route-path) — the type embedded in the
# canonical signing payload matches the path suffix exactly. min_version
# for these two is 75 (slave APK bump), not the H-2 default 74.
SIGNED_WEBHOOKS = [
    ("verify-photo", "/webhook/verify-photo"),
    ("register", "/webhook/register"),
]


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_round3", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_round3"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def live_server(mail_module):
    server = HTTPServer(("127.0.0.1", 0), mail_module.WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _bunny_keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(pub_der).decode()


def _sign(priv, payload):
    sig = priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


@pytest.fixture
def seeded(mail_module):
    """Seed a mesh + node with a bunny pubkey on file."""
    mesh_id = "round3-mesh-" + str(int(time.time() * 1000))
    node_id = "collar-1"
    priv, pub_b64 = _bunny_keypair()
    mail_module._mesh_accounts.meshes[mesh_id] = {
        "mesh_id": mesh_id,
        "lion_pubkey": "",
        "auth_token": "test-token",
        "invite_code": "",
        "invite_expires_at": 0,
        "invite_uses": 0,
        "pin": "0000",
        "created_at": int(time.time()),
        "nodes": {
            node_id: {
                "node_id": node_id,
                "bunny_pubkey": pub_b64,
                "registered_at": int(time.time()),
            },
        },
        "vault_only": False,
        "max_blobs_per_day": 5000,
        "max_total_bytes_mb": 100,
    }
    try:
        yield {"mesh_id": mesh_id, "node_id": node_id, "priv": priv, "pub_b64": pub_b64}
    finally:
        mail_module._mesh_accounts.meshes.pop(mesh_id, None)


def _post(url, body):
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode())


# ── Inner-payload templates per endpoint ──


def _inner_for(webhook_type):
    """The non-sig fields each endpoint expects in the body."""
    if webhook_type == "verify-photo":
        return {"photo": "stub-base64", "task": "wash dishes"}
    if webhook_type == "register":
        return {"lan_ip": "10.0.0.5", "tailscale_ip": "100.64.0.5", "device_id": "Pixel-7"}
    return {}


# ── Missing signature → 403 + min_collar_version=75 ──


@pytest.mark.parametrize("webhook_type,path", SIGNED_WEBHOOKS)
def test_missing_signature_returns_403_with_version_hint(live_server, seeded, webhook_type, path):
    status, body = _post(f"{live_server}{path}", _inner_for(webhook_type))
    assert status == 403, f"{webhook_type}: {status} {body}"
    assert body["error"] == "signature required"
    assert body["min_collar_version"] == 75


# ── Bad signature → 403 invalid signature ──


@pytest.mark.parametrize("webhook_type,path", SIGNED_WEBHOOKS)
def test_bad_signature_returns_403(live_server, seeded, webhook_type, path):
    ts = int(time.time() * 1000)
    body = _inner_for(webhook_type)
    body.update(
        {
            "mesh_id": seeded["mesh_id"],
            "node_id": seeded["node_id"],
            "ts": ts,
            "signature": "AAAA" + "=" * 340,  # well-formed b64, wrong sig
        }
    )
    status, response = _post(f"{live_server}{path}", body)
    assert status == 403, f"{webhook_type}: {status} {response}"
    assert response["error"] == "invalid signature"


# ── Stale ts → 403 ts out of window ──


@pytest.mark.parametrize("webhook_type,path", SIGNED_WEBHOOKS)
def test_ts_out_of_window_returns_403(live_server, seeded, webhook_type, path):
    stale_ts = int(time.time() * 1000) - 10 * 60 * 1000
    payload = f"{seeded['mesh_id']}|{seeded['node_id']}|{webhook_type}|{stale_ts}"
    sig = _sign(seeded["priv"], payload)
    body = _inner_for(webhook_type)
    body.update(
        {
            "mesh_id": seeded["mesh_id"],
            "node_id": seeded["node_id"],
            "ts": stale_ts,
            "signature": sig,
        }
    )
    status, response = _post(f"{live_server}{path}", body)
    assert status == 403, f"{webhook_type}: {status} {response}"
    assert response["error"] == "ts out of window"


# ── Unknown node → 403 node not registered ──


@pytest.mark.parametrize("webhook_type,path", SIGNED_WEBHOOKS)
def test_unknown_node_returns_403(live_server, seeded, webhook_type, path):
    ts = int(time.time() * 1000)
    payload = f"{seeded['mesh_id']}|ghost-node|{webhook_type}|{ts}"
    sig = _sign(seeded["priv"], payload)
    body = _inner_for(webhook_type)
    body.update(
        {
            "mesh_id": seeded["mesh_id"],
            "node_id": "ghost-node",
            "ts": ts,
            "signature": sig,
        }
    )
    status, response = _post(f"{live_server}{path}", body)
    assert status == 403, f"{webhook_type}: {status} {response}"
    assert response["error"] == "node not registered in mesh"


# ── Unknown mesh → 404 mesh not found ──


@pytest.mark.parametrize("webhook_type,path", SIGNED_WEBHOOKS)
def test_unknown_mesh_returns_404(live_server, seeded, webhook_type, path):
    ts = int(time.time() * 1000)
    payload = f"ghost-mesh|{seeded['node_id']}|{webhook_type}|{ts}"
    sig = _sign(seeded["priv"], payload)
    body = _inner_for(webhook_type)
    body.update(
        {
            "mesh_id": "ghost-mesh",
            "node_id": seeded["node_id"],
            "ts": ts,
            "signature": sig,
        }
    )
    status, response = _post(f"{live_server}{path}", body)
    assert status == 404, f"{webhook_type}: {status} {response}"
    assert response["error"] == "mesh not found"


# ── Valid sig → 200 + the side effect actually fires ──


def test_verify_photo_valid_signature_invokes_llm(live_server, seeded, mail_module, monkeypatch):
    captured = []
    monkeypatch.setattr(
        mail_module,
        "verify_photo_with_llm",
        lambda photo, task, on_evidence=None: captured.append((photo, task)) or {"passed": True, "reason": "ok"},
    )
    ts = int(time.time() * 1000)
    payload = f"{seeded['mesh_id']}|{seeded['node_id']}|verify-photo|{ts}"
    sig = _sign(seeded["priv"], payload)
    status, response = _post(
        f"{live_server}/webhook/verify-photo",
        {
            "photo": "stub-base64",
            "task": "wash dishes",
            "mesh_id": seeded["mesh_id"],
            "node_id": seeded["node_id"],
            "ts": ts,
            "signature": sig,
        },
    )
    assert status == 200, f"{status} {response}"
    assert response["passed"] is True
    assert captured == [("stub-base64", "wash dishes")]


def test_register_valid_signature_writes_registry(live_server, seeded, mail_module, monkeypatch, tmp_path):
    """The handler writes IP_REGISTRY_FILE on success — redirect into
    tmp_path so the test stays in user-writable space."""
    ip_registry = tmp_path / "ip_registry.json"
    monkeypatch.setattr(mail_module, "IP_REGISTRY_FILE", str(ip_registry))
    ts = int(time.time() * 1000)
    payload = f"{seeded['mesh_id']}|{seeded['node_id']}|register|{ts}"
    sig = _sign(seeded["priv"], payload)
    status, response = _post(
        f"{live_server}/webhook/register",
        {
            "lan_ip": "10.0.0.5",
            "tailscale_ip": "100.64.0.5",
            "device_id": "Pixel-7",
            "mesh_id": seeded["mesh_id"],
            "node_id": seeded["node_id"],
            "ts": ts,
            "signature": sig,
        },
    )
    assert status == 200, f"{status} {response}"
    assert response == {"ok": True}
    assert ip_registry.exists()
    saved = json.loads(ip_registry.read_text())
    assert "Pixel-7" in saved
    assert saved["Pixel-7"]["lan_ip"] == "10.0.0.5"


def _signed_register_post(live_server, seeded, device_id):
    """POST /webhook/register with a valid bunny signature, varying only
    the device_id. Exercises the round-1 device_id validator on top of
    the round-3 sig gate."""
    ts = int(time.time() * 1000)
    payload = f"{seeded['mesh_id']}|{seeded['node_id']}|register|{ts}"
    sig = _sign(seeded["priv"], payload)
    return _post(
        f"{live_server}/webhook/register",
        {
            "lan_ip": "10.0.0.5",
            "tailscale_ip": "100.64.0.5",
            "device_id": device_id,
            "mesh_id": seeded["mesh_id"],
            "node_id": seeded["node_id"],
            "ts": ts,
            "signature": sig,
        },
    )


class TestRegisterDeviceIdValidatorSigned:
    """Parametrized device_id validator matrix — moved from round-1 to
    round-3 because /webhook/register now requires a bunny signature
    before reaching the validator. The validator is defense-in-depth:
    sig covers spoofing, this gate covers a bug in a future signer that
    emits a path-shaped device_id."""

    @pytest.mark.parametrize(
        "bad_device_id",
        [
            "../../etc/passwd",
            "../escape",
            "name with spaces",
            "name\nwith\nnewlines",
            "name\x00with\x00null",
            "name\rwith\rcr",
            "",
            "a" * 65,  # too long
            None,
            123,
        ],
    )
    def test_rejects_unsafe_device_id(self, live_server, seeded, bad_device_id):
        code, body = _signed_register_post(live_server, seeded, bad_device_id)
        assert code == 400, f"expected 400 for device_id={bad_device_id!r}, got {code} {body}"
        assert body["error"] == "invalid device_id"

    @pytest.mark.parametrize(
        "good_device_id",
        [
            "Pixel-7",
            "Galaxy.S23",
            "phone_main",
            "abc-123",
            "a",
            "ABCD-1234-5678-9012",
        ],
    )
    def test_accepts_safe_device_id(self, live_server, seeded, mail_module, monkeypatch, tmp_path, good_device_id):
        ip_registry = tmp_path / f"ip_registry_{good_device_id}.json"
        monkeypatch.setattr(mail_module, "IP_REGISTRY_FILE", str(ip_registry))
        code, body = _signed_register_post(live_server, seeded, good_device_id)
        assert code == 200, f"expected 200 for device_id={good_device_id!r}, got {code} {body}"
        assert body == {"ok": True}

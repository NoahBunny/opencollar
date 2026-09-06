"""HTTP-level QA for the node-signed penalty report.

Background: `/webhook/desktop-penalty` is admin-gated, so the only collar that
could ever report a fine-bearing incident was one holding ADMIN_TOKEN — a
credential for the whole admin API, on the very machine the collar exists to
constrain. A vault-mode collar deliberately holds no such token, so every
penalty it detected died in a log line, the $30 consent decline included.

`POST /vault/{mesh_id}/penalty` gives it a door that needs only its own
registered node key. The collar reports the EVENT; it gets no say in the price:

  * ``veneration-paste``  — armed and priced by the Lion, in this mesh's orders
  * ``consent-decline``   — a constant that lives on the relay, not the collar
  * ``desktop-tamper``    — tiered off the relay's own per-mesh lifetime counter

So a bunny with root on the collar can forge a report and still cannot set what
it costs, cannot charge at all unless the Lion armed it, and gains nothing by
lying about the count.

Unlike standing-orders and lion-pubkey — which only ever make a machine *more*
governed and so are served to any member — this route moves money, and is
therefore gated on Lion confirmation as well as membership.

Signed payload layout:  "{mesh_id}|{node_id}|penalty|{ts}|{kind}|{count}"
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

CONSENT_DECLINE_FEE = 30


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_vault_penalty", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_vault_penalty"] = mod
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


def _keypair():
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_der = priv.public_key().public_bytes(
        serialization.Encoding.DER,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return priv, base64.b64encode(pub_der).decode()


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


@pytest.fixture
def collar_mesh(mail_module, monkeypatch, tmp_path):
    """A mesh with a paywall at $0, one Lion-registered desktop node holding its
    private key, and a tamper counter directory scoped to this test."""
    store = mail_module.MeshAccountStore(persist_dir=str(tmp_path / "accounts"))
    _, lion_pub = _keypair()
    acct = store.create(lion_pub, pin="4242")
    mesh_id = acct["mesh_id"]

    orders = mail_module._orders_registry.get_or_create(mesh_id)
    orders.set("paywall", "0")

    node_id = "desk-1"
    node_priv, node_pub = _keypair()
    mail_module._vault_store.add_node(
        mesh_id,
        {
            "node_id": node_id,
            "node_type": "desktop",
            "node_pubkey": node_pub,
            "registered_at": int(time.time()),
        },
    )

    monkeypatch.setattr(mail_module, "_mesh_accounts", store)
    monkeypatch.setattr(mail_module, "_TAMPER_TIERS_DIR", str(tmp_path / "tamper_tiers"))
    try:
        yield {
            "mesh_id": mesh_id,
            "node_id": node_id,
            "node_priv": node_priv,
            "orders": orders,
            "store": store,
        }
    finally:
        mail_module._orders_registry.docs.pop(mesh_id, None)


def _sign(priv, payload):
    return base64.b64encode(priv.sign(payload.encode("utf-8"), padding.PKCS1v15(), hashes.SHA256())).decode()


def _report(
    live_server,
    collar_mesh,
    kind,
    count=0,
    *,
    priv=None,
    node_id=None,
    ts_ms=None,
    signed_as=None,
    extra=None,
):
    """POST a node-signed penalty report.

    `signed_as` overrides the string that gets SIGNED without changing what is
    SENT — the only way to test that the signature covers the claim rather than
    merely accompanying it.
    """
    mesh_id = collar_mesh["mesh_id"]
    node_id = node_id or collar_mesh["node_id"]
    priv = priv or collar_mesh["node_priv"]
    ts_ms = int(time.time() * 1000) if ts_ms is None else ts_ms
    payload = signed_as or f"{mesh_id}|{node_id}|penalty|{ts_ms}|{kind}|{count}"
    body = {"node_id": node_id, "ts": ts_ms, "kind": kind, "count": count, "signature": _sign(priv, payload)}
    if extra:
        body.update(extra)
    return _post(f"{live_server}/vault/{mesh_id}/penalty", body)


def _paywall(collar_mesh):
    return float(collar_mesh["orders"].get("paywall", 0) or 0)


# ── The door itself ───────────────────────────────────────────────────────


class TestNodeKeyIsEnough:
    def test_a_collar_holding_no_admin_token_can_report(self, live_server, collar_mesh):
        """The whole point: no admin_token in the body, and it still charges."""
        collar_mesh["orders"].set("paste_fine_active", "1")
        collar_mesh["orders"].set("paste_fine_amount", "7")
        status, body = _report(live_server, collar_mesh, "veneration-paste", 1)
        assert status == 200, body
        assert body["armed"] is True
        assert body["amount"] == 7
        assert _paywall(collar_mesh) == 7

    def test_the_charge_lands_on_the_paywall_not_just_in_the_reply(self, live_server, collar_mesh):
        status, body = _report(live_server, collar_mesh, "consent-decline", 1)
        assert status == 200, body
        assert _paywall(collar_mesh) == CONSENT_DECLINE_FEE
        # The reply must carry the number that landed, not None: `add-paywall`
        # used to fall through to a bare `{"applied": ...}` with no paywall key.
        assert float(body["paywall"]) == CONSENT_DECLINE_FEE

    def test_the_reply_tracks_the_paywall_across_repeat_reports(self, live_server, collar_mesh):
        first = _report(live_server, collar_mesh, "consent-decline", 1)[1]
        second = _report(live_server, collar_mesh, "consent-decline", 2)[1]
        assert float(first["paywall"]) == CONSENT_DECLINE_FEE
        assert float(second["paywall"]) == 2 * CONSENT_DECLINE_FEE
        assert _paywall(collar_mesh) == 2 * CONSENT_DECLINE_FEE


# ── The collar reports the event; the relay sets the price ────────────────


class TestTheCollarDoesNotSetThePrice:
    def test_an_amount_in_the_body_is_ignored(self, live_server, collar_mesh):
        """A rooted collar can put any number it likes in the request. The
        consent decline costs $30 because the relay says so."""
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, extra={"amount": 1})
        assert status == 200, body
        assert body["amount"] == CONSENT_DECLINE_FEE
        assert _paywall(collar_mesh) == CONSENT_DECLINE_FEE

    def test_an_unarmed_kind_charges_nothing_and_is_not_an_error(self, live_server, collar_mesh):
        """The collar is right to report it; the Lion simply has not armed it.
        A 200 makes it log rather than retry forever."""
        status, body = _report(live_server, collar_mesh, "veneration-paste", 3)
        assert status == 200, body
        assert body["armed"] is False
        assert body["amount"] == 0
        assert _paywall(collar_mesh) == 0

    def test_armed_but_priced_at_zero_charges_nothing(self, live_server, collar_mesh):
        collar_mesh["orders"].set("paste_fine_active", "1")
        collar_mesh["orders"].set("paste_fine_amount", "0")
        status, body = _report(live_server, collar_mesh, "veneration-paste", 1)
        assert status == 200, body
        assert body["amount"] == 0
        assert _paywall(collar_mesh) == 0

    def test_the_lions_price_is_capped_at_five_hundred(self, live_server, collar_mesh):
        collar_mesh["orders"].set("paste_fine_active", "1")
        collar_mesh["orders"].set("paste_fine_amount", "9999")
        status, body = _report(live_server, collar_mesh, "veneration-paste", 1)
        assert status == 200, body
        assert body["amount"] == 500
        assert _paywall(collar_mesh) == 500

    def test_an_unknown_kind_never_reaches_a_default(self, live_server, collar_mesh):
        status, body = _report(live_server, collar_mesh, "make-something-up", 1)
        assert status == 400, body
        assert "unknown penalty kind" in body["error"]
        assert _paywall(collar_mesh) == 0


# ── The tamper ratchet, server-side ───────────────────────────────────────


class TestTamperRatchet:
    def test_first_tamper_report_charges_the_tier_one_floor(self, live_server, collar_mesh):
        status, body = _report(live_server, collar_mesh, "desktop-tamper", 0)
        assert status == 200, body
        assert body["attempt"] == 1
        assert body["amount"] == 5
        assert _paywall(collar_mesh) == 5

    def test_the_counter_is_the_relays_and_only_goes_up(self, live_server, collar_mesh):
        attempts = []
        for _ in range(4):
            _, body = _report(live_server, collar_mesh, "desktop-tamper", 0)
            attempts.append(body["attempt"])
        assert attempts == [1, 2, 3, 4]
        # Tiers of three at $5 a step: the fourth report crosses into $10.
        assert _paywall(collar_mesh) == 5 + 5 + 5 + 10

    def test_a_collar_that_remembers_more_fast_forwards_the_relay(self, live_server, collar_mesh):
        _, body = _report(live_server, collar_mesh, "desktop-tamper", 7)
        assert body["attempt"] == 7
        assert body["amount"] == 15

    def test_a_collar_that_remembers_fewer_cannot_walk_the_tier_back(self, live_server, collar_mesh):
        _report(live_server, collar_mesh, "desktop-tamper", 7)
        _, body = _report(live_server, collar_mesh, "desktop-tamper", 0)
        assert body["attempt"] == 8
        assert body["amount"] == 15

    def test_a_wild_claim_is_clamped_rather_than_believed(self, live_server, collar_mesh):
        """+100 over the recorded count is the ceiling, the same bound
        /webhook/desktop-penalty carries."""
        _, body = _report(live_server, collar_mesh, "desktop-tamper", 5000)
        assert body["attempt"] == 100
        assert body["amount"] == 170  # ((100-1)//3 + 1) * 5

    def test_a_negative_count_is_refused_outright(self, live_server, collar_mesh):
        """The server floors the count to 0 *before* rebuilding the payload it
        verifies, so a report claiming -50 cannot match any signature the collar
        could produce. Fails closed, which is the right side to fail on: a
        negative lifetime count is nonsense and charging nothing beats trusting
        it. Pinned so the normalize-then-verify order is not quietly swapped.
        """
        status, body = _report(live_server, collar_mesh, "desktop-tamper", -50)
        assert status == 403, body
        assert _paywall(collar_mesh) == 0


# ── Who gets to be believed ───────────────────────────────────────────────


class TestRefusals:
    def test_node_id_and_signature_are_required(self, live_server, collar_mesh):
        status, body = _post(
            f"{live_server}/vault/{collar_mesh['mesh_id']}/penalty",
            {"kind": "consent-decline", "count": 1, "ts": int(time.time() * 1000)},
        )
        assert status == 400, body
        assert _paywall(collar_mesh) == 0

    def test_a_stranger_holding_the_mesh_id_is_refused(self, live_server, collar_mesh):
        priv, _ = _keypair()
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, priv=priv, node_id="not-a-member")
        assert status == 403, body
        assert "not approved" in body["error"]
        assert _paywall(collar_mesh) == 0

    def test_a_member_signing_with_the_wrong_key_is_refused(self, live_server, collar_mesh):
        rogue, _ = _keypair()
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, priv=rogue)
        assert status == 403, body
        assert "signature" in body["error"]
        assert _paywall(collar_mesh) == 0

    def test_a_stale_timestamp_is_refused(self, live_server, collar_mesh):
        stale = int(time.time() * 1000) - 6 * 60 * 1000
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, ts_ms=stale)
        assert status == 403, body
        assert "ts out of window" in body["error"]
        assert _paywall(collar_mesh) == 0

    def test_the_signature_covers_the_kind_it_is_sent_with(self, live_server, collar_mesh):
        """Signing the $0 unarmed kind and sending the $30 one must not work."""
        ts = int(time.time() * 1000)
        signed = f"{collar_mesh['mesh_id']}|{collar_mesh['node_id']}|penalty|{ts}|veneration-paste|1"
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, ts_ms=ts, signed_as=signed)
        assert status == 403, body
        assert _paywall(collar_mesh) == 0

    def test_the_signature_covers_the_count_it_is_sent_with(self, live_server, collar_mesh):
        ts = int(time.time() * 1000)
        signed = f"{collar_mesh['mesh_id']}|{collar_mesh['node_id']}|penalty|{ts}|desktop-tamper|1"
        status, body = _report(live_server, collar_mesh, "desktop-tamper", 99, ts_ms=ts, signed_as=signed)
        assert status == 403, body
        assert _paywall(collar_mesh) == 0

    def test_a_signature_lifted_from_another_mesh_is_refused(self, live_server, collar_mesh):
        ts = int(time.time() * 1000)
        signed = f"some-other-mesh|{collar_mesh['node_id']}|penalty|{ts}|consent-decline|1"
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, ts_ms=ts, signed_as=signed)
        assert status == 403, body
        assert _paywall(collar_mesh) == 0

    def test_an_auto_accepted_node_the_lion_never_looked_at_cannot_spend(self, live_server, collar_mesh, mail_module):
        """This is where the route parts company with standing-orders and
        lion-pubkey, which serve any member: those only make a machine more
        governed, this one moves money."""
        mesh_id, node_id = collar_mesh["mesh_id"], "auto-node"
        priv, pub = _keypair()
        mail_module._vault_store.add_node(
            mesh_id,
            {
                "node_id": node_id,
                "node_type": "desktop",
                "node_pubkey": pub,
                "registered_at": int(time.time()),
                "auto_accepted": True,
            },
        )
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, priv=priv, node_id=node_id)
        assert status == 403, body
        assert "awaiting lion confirmation" in body["error"]
        assert _paywall(collar_mesh) == 0

    def test_the_same_node_can_spend_once_the_lion_confirms_it(self, live_server, collar_mesh, mail_module):
        mesh_id, node_id = collar_mesh["mesh_id"], "auto-node-2"
        priv, pub = _keypair()
        mail_module._vault_store.add_node(
            mesh_id,
            {
                "node_id": node_id,
                "node_type": "desktop",
                "node_pubkey": pub,
                "registered_at": int(time.time()),
                "auto_accepted": True,
                "lion_confirmed": True,
            },
        )
        status, body = _report(live_server, collar_mesh, "consent-decline", 1, priv=priv, node_id=node_id)
        assert status == 200, body
        assert body["amount"] == CONSENT_DECLINE_FEE

    def test_a_vault_only_mesh_refuses_the_route_outright(self, live_server, collar_mesh, mail_module):
        """The same guard /webhook/desktop-penalty carries. Without it this is a
        way around the property vault_only is sold on."""
        collar_mesh["store"].set_vault_only(collar_mesh["mesh_id"], True)
        monkey_mesh = collar_mesh["mesh_id"]
        assert monkey_mesh != mail_module.OPERATOR_MESH_ID
        status, body = _report(live_server, collar_mesh, "consent-decline", 1)
        assert status == 403, body
        assert "vault_only" in body["error"]
        assert _paywall(collar_mesh) == 0


# ── The route's own surface ───────────────────────────────────────────────


class TestRouteSurface:
    def test_penalty_is_listed_in_the_bad_path_help(self, live_server, collar_mesh):
        """A collar that gets the path shape wrong should be told the route
        exists — the old help text predated it."""
        status, body = _post(f"{live_server}/vault/{collar_mesh['mesh_id']}", {})
        assert status == 400, body
        assert "penalty" in body["error"]

    def test_an_unknown_action_is_a_404_not_a_charge(self, live_server, collar_mesh):
        status, body = _post(f"{live_server}/vault/{collar_mesh['mesh_id']}/not-a-real-action", {})
        assert status == 404, body
        assert _paywall(collar_mesh) == 0

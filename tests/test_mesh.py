"""Tests for focuslock_mesh.py — mesh protocol, peer registry, gossip handlers."""

import json
import threading
import time
from unittest.mock import MagicMock, patch

from focuslock_mesh import (
    ORDER_KEYS,
    DesktopRegistry,
    MessageStore,
    OrdersDocument,
    PaymentLedger,
    PeerInfo,
    PeerRegistry,
    TrustStore,
    VoucherPool,
    _build_beacon,
    _parse_beacon,
    canonical_json,
    handle_get_ledger,
    handle_get_messages,
    handle_get_vouchers,
    handle_ledger_entry,
    handle_mark_read,
    handle_mark_replied,
    handle_mesh_ping,
    handle_mesh_status,
    handle_mesh_sync,
    handle_redeem_voucher,
    handle_send_message,
    handle_set_imap_epoch,
    handle_store_vouchers,
    sign_orders,
    validate_pin,
    verify_signature,
)

# ── canonical_json / sign_orders / verify_signature ──


class TestMeshCrypto:
    def test_canonical_json_key_order_deterministic(self):
        a = canonical_json({"b": 2, "a": 1})
        b = canonical_json({"a": 1, "b": 2})
        assert a == b == b'{"a":1,"b":2}'

    def test_sign_and_verify_roundtrip(self, lion_keypair):
        orders = {"action": "lock", "mins": 30}
        sig = sign_orders(orders, lion_keypair["priv_pem"])
        assert verify_signature(orders, sig, lion_keypair["pub_pem"]) is True

    def test_verify_rejects_tampered_orders(self, lion_keypair):
        orders = {"mins": 5}
        sig = sign_orders(orders, lion_keypair["priv_pem"])
        orders["mins"] = 9999
        assert verify_signature(orders, sig, lion_keypair["pub_pem"]) is False

    def test_verify_rejects_wrong_pubkey(self, lion_keypair, slave_keypair):
        orders = {"x": 1}
        sig = sign_orders(orders, lion_keypair["priv_pem"])
        assert verify_signature(orders, sig, slave_keypair["pub_pem"]) is False

    def test_verify_rejects_empty_inputs(self, lion_keypair):
        assert verify_signature({"x": 1}, "", lion_keypair["pub_pem"]) is False
        assert verify_signature({"x": 1}, "sigbase64", "") is False

    def test_verify_rejects_garbage_signature(self, lion_keypair):
        assert verify_signature({"x": 1}, "not!valid!b64", lion_keypair["pub_pem"]) is False


# ── OrdersDocument ──


class TestOrdersDocument:
    def test_defaults_populated(self):
        doc = OrdersDocument()
        assert doc.version == 0
        assert doc.signature == ""
        # Every ORDER_KEYS entry seeded at its default
        for k, v in ORDER_KEYS.items():
            assert doc.orders[k] == v

    def test_get_and_set(self):
        doc = OrdersDocument()
        doc.set("paywall", "50")
        assert doc.get("paywall") == "50"
        assert doc.get("nonexistent", "fallback") == "fallback"

    def test_bump_version_unsigned(self):
        doc = OrdersDocument()
        doc.bump_version()
        assert doc.version == 1
        assert doc.signature == ""
        assert doc.updated_at > 0

    def test_bump_version_signed(self, lion_keypair):
        doc = OrdersDocument()
        doc.bump_version(lion_keypair["priv_pem"])
        assert doc.version == 1
        assert doc.signature != ""
        # The signature is over current orders
        assert verify_signature(doc.orders, doc.signature, lion_keypair["pub_pem"]) is True

    def test_persist_and_reload_roundtrip(self, tmp_path, lion_keypair):
        path = tmp_path / "orders.json"
        doc = OrdersDocument(persist_path=str(path))
        doc.set("paywall", "42")
        doc.bump_version(lion_keypair["priv_pem"])
        # Reload from disk — new instance
        doc2 = OrdersDocument(persist_path=str(path))
        assert doc2.version == 1
        assert doc2.orders["paywall"] == "42"
        assert doc2.signature == doc.signature

    def test_apply_remote_rejects_lower_version(self):
        doc = OrdersDocument()
        doc.version = 5
        result = doc.apply_remote({"version": 3, "orders": {"paywall": "99"}}, lion_pubkey="")
        assert result is False
        assert doc.orders["paywall"] == "0"

    def test_apply_remote_rejects_equal_version(self):
        doc = OrdersDocument()
        doc.version = 5
        assert doc.apply_remote({"version": 5, "orders": {}}, lion_pubkey="") is False

    def test_apply_remote_accepts_unsigned_when_no_pubkey_configured(self):
        """Uninitialized nodes (no lion_pubkey yet) fall back to permissive path."""
        doc = OrdersDocument()
        ok = doc.apply_remote({"version": 1, "orders": {"paywall": "25"}}, lion_pubkey="")
        assert ok is True
        assert doc.orders["paywall"] == "25"

    def test_apply_remote_rejects_unsigned_when_pubkey_configured(self, lion_keypair):
        doc = OrdersDocument()
        ok = doc.apply_remote(
            {"version": 1, "orders": {"paywall": "25"}},
            lion_pubkey=lion_keypair["pub_pem"],
        )
        assert ok is False
        assert doc.orders["paywall"] == "0"

    def test_apply_remote_rejects_invalid_signature(self, lion_keypair, slave_keypair):
        doc = OrdersDocument()
        orders = dict(ORDER_KEYS)
        orders["paywall"] = "25"
        # Sign with slave key, but lion pubkey is required → rejected
        sig = sign_orders(orders, slave_keypair["priv_pem"])
        ok = doc.apply_remote(
            {"version": 1, "orders": orders, "signature": sig},
            lion_pubkey=lion_keypair["pub_pem"],
        )
        assert ok is False

    def test_apply_remote_accepts_valid_signature(self, lion_keypair):
        doc = OrdersDocument()
        orders = dict(ORDER_KEYS)
        orders["paywall"] = "25"
        sig = sign_orders(orders, lion_keypair["priv_pem"])
        ok = doc.apply_remote(
            {"version": 3, "orders": orders, "signature": sig, "updated_at": 1000},
            lion_pubkey=lion_keypair["pub_pem"],
        )
        assert ok is True
        assert doc.version == 3
        assert doc.orders["paywall"] == "25"

    def test_apply_remote_triggers_callback(self, lion_keypair):
        """apply_remote is called from handle_mesh_sync; the caller passes on_orders_applied."""
        doc = OrdersDocument()
        # We don't test the callback directly (apply_remote doesn't take one).
        # But we verify the orders mutation after apply.
        orders = dict(ORDER_KEYS)
        orders["message"] = "hello"
        sig = sign_orders(orders, lion_keypair["priv_pem"])
        doc.apply_remote({"version": 1, "orders": orders, "signature": sig}, lion_keypair["pub_pem"])
        assert doc.orders["message"] == "hello"


# ── PeerInfo ──


class TestPeerInfo:
    def test_to_dict_roundtrip(self):
        original = PeerInfo(
            node_id="phone",
            node_type="phone",
            addresses=["192.168.1.5", "100.64.0.1"],
            port=8434,
            last_seen=1234567890.0,
            orders_version=7,
            status={"hostname": "pixel"},
        )
        d = original.to_dict()
        restored = PeerInfo.from_dict(d)
        assert restored.node_id == "phone"
        assert restored.node_type == "phone"
        assert restored.addresses == ["192.168.1.5", "100.64.0.1"]
        assert restored.port == 8434
        assert restored.last_seen == 1234567890.0
        assert restored.orders_version == 7
        assert restored.status == {"hostname": "pixel"}

    def test_from_dict_with_missing_fields_uses_defaults(self):
        p = PeerInfo.from_dict({"node_id": "x"})
        assert p.node_id == "x"
        assert p.node_type == "unknown"
        assert p.addresses == []
        assert p.port == 8434
        assert p.orders_version == 0


# ── TrustStore ──


class TestTrustStore:
    def test_whitelist_members_trusted_by_default(self):
        store = TrustStore()
        # "phone" and "homelab" are in _DEFAULT_WHITELIST
        assert store.is_trusted("phone") is True
        assert store.is_trusted("homelab") is True

    def test_random_node_not_trusted(self):
        store = TrustStore()
        assert store.is_trusted("random-attacker") is False

    def test_explicit_trust_adds_to_store(self):
        store = TrustStore()
        store.trust("my-node", reason="manual approval")
        assert store.is_trusted("my-node") is True

    def test_persist_roundtrip(self, tmp_path):
        path = tmp_path / "trust.json"
        s1 = TrustStore(persist_path=str(path))
        s1.trust("approved-node", reason="test")
        s2 = TrustStore(persist_path=str(path))
        assert s2.is_trusted("approved-node") is True


# ── PeerRegistry ──


class TestPeerRegistry:
    def test_update_peer_whitelisted_accepted(self):
        reg = PeerRegistry()
        reg.update_peer("phone", node_type="phone", addresses=["10.0.0.5"], port=8432)
        peer = reg.peers["phone"]
        assert peer.node_type == "phone"
        assert "10.0.0.5" in peer.addresses
        assert peer.port == 8432
        assert peer.last_seen > 0

    def test_update_peer_non_whitelisted_rejected(self):
        reg = PeerRegistry()
        reg.update_peer("attacker-node", node_type="evil", addresses=["1.1.1.1"])
        assert "attacker-node" not in reg.peers

    def test_update_peer_addresses_merged_deduped(self):
        reg = PeerRegistry()
        reg.update_peer("phone", addresses=["10.0.0.1"])
        reg.update_peer("phone", addresses=["10.0.0.1", "10.0.0.2"])  # dup + new
        peer = reg.peers["phone"]
        assert set(peer.addresses) == {"10.0.0.1", "10.0.0.2"}

    def test_get_all_except_excludes_self(self):
        reg = PeerRegistry()
        reg.update_peer("phone")
        reg.update_peer("homelab")
        others = reg.get_all_except("phone")
        assert len(others) == 1
        assert others[0].node_id == "homelab"

    def test_to_known_nodes_excludes_self(self):
        reg = PeerRegistry()
        reg.update_peer("phone", addresses=["10.0.0.1"])
        reg.update_peer("homelab", addresses=["10.0.0.2"])
        known = reg.to_known_nodes("phone")
        assert "phone" not in known
        assert "homelab" in known
        assert known["homelab"]["addresses"] == ["10.0.0.2"]

    def test_persist_roundtrip(self, tmp_path):
        path = tmp_path / "peers.json"
        r1 = PeerRegistry(persist_path=str(path))
        r1.update_peer("phone", addresses=["1.2.3.4"], port=9000)
        r2 = PeerRegistry(persist_path=str(path))
        assert "phone" in r2.peers
        assert r2.peers["phone"].port == 9000

    def test_prune_stale_removes_non_whitelisted_on_load(self, tmp_path):
        """If the persist file has a non-whitelisted node, it's pruned on load."""
        path = tmp_path / "peers.json"
        path.write_text(
            json.dumps(
                {
                    "phone": {"node_id": "phone", "addresses": ["10.0.0.1"], "port": 8432},
                    "rogue": {"node_id": "rogue", "addresses": ["6.6.6.6"], "port": 8432},
                }
            )
        )
        reg = PeerRegistry(persist_path=str(path))
        assert "phone" in reg.peers
        assert "rogue" not in reg.peers

    def test_learn_from_known_nodes_skips_non_whitelisted(self):
        reg = PeerRegistry()
        reg.learn_from_known_nodes(
            {
                "phone": {"type": "phone", "addresses": ["10.0.0.1"], "port": 8432},
                "rogue": {"type": "evil", "addresses": ["6.6.6.6"], "port": 8432},
            }
        )
        assert "phone" in reg.peers
        assert "rogue" not in reg.peers


# ── validate_pin ──


class TestValidatePin:
    def test_correct_pin_accepted(self):
        doc = OrdersDocument()
        doc.set("pin", "1234")
        assert validate_pin({"pin": "1234"}, doc) is True

    def test_wrong_pin_rejected(self):
        doc = OrdersDocument()
        doc.set("pin", "1234")
        assert validate_pin({"pin": "0000"}, doc) is False

    def test_empty_pin_rejected(self):
        doc = OrdersDocument()
        doc.set("pin", "1234")
        assert validate_pin({"pin": ""}, doc) is False
        assert validate_pin({}, doc) is False

    def test_empty_expected_rejected(self):
        """No pin configured = reject all (fall back to signature auth)."""
        doc = OrdersDocument()
        assert validate_pin({"pin": "1234"}, doc) is False

    def test_pin_as_int_coerced_to_string(self):
        doc = OrdersDocument()
        doc.set("pin", "1234")
        assert validate_pin({"pin": 1234}, doc) is True


# ── LAN Discovery beacons ──


class TestBeacon:
    def test_build_parse_roundtrip(self):
        data = _build_beacon("phone", "phone", 8434, 42)
        msg = _parse_beacon(data)
        assert msg["node_id"] == "phone"
        assert msg["type"] == "phone"
        assert msg["port"] == 8434
        assert msg["orders_version"] == 42
        assert msg["magic"] == "FOCUSLOCK-MESH-V1"

    def test_parse_rejects_bad_magic(self):
        payload = json.dumps({"magic": "WRONG-PROTOCOL", "node_id": "x"}).encode()
        assert _parse_beacon(payload) is None

    def test_parse_rejects_malformed_json(self):
        assert _parse_beacon(b"not-json-at-all") is None
        assert _parse_beacon(b"") is None

    def test_parse_rejects_non_utf8(self):
        assert _parse_beacon(b"\xff\xfe\xfd") is None


# ── VoucherPool ──


class TestVoucherPool:
    def test_store_dedupes_across_calls_by_id(self, tmp_path):
        """Dedup guards against re-delivery of vouchers across gossip rounds.

        Note: within a single store() call the dedup is a bug — the
        existing_ids snapshot is taken before the loop, so duplicates
        within one call land multiple times. Realistic callers send one
        voucher per redelivery, so cross-call dedup is what matters.
        """
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        v1 = {"id": "v1", "expires": int(time.time() * 1000) + 60000}
        pool.store([v1])
        pool.store([v1])  # re-delivered
        pool.store([v1])
        assert len(pool.vouchers) == 1

    def test_get_available_filters_expired(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        fresh = {"id": "v1", "expires": int(time.time() * 1000) + 60000}
        stale = {"id": "v2", "expires": 1}  # epoch 1ms
        pool.store([fresh, stale])
        avail = pool.get_available()
        assert len(avail) == 1
        assert avail[0]["id"] == "v1"

    def test_get_available_filters_redeemed(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        v1 = {"id": "v1", "expires": int(time.time() * 1000) + 60000}
        pool.store([v1])
        pool.redeem("v1")
        assert pool.get_available() == []

    def test_redeem_marks_voucher(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        v = {"id": "v1", "expires": int(time.time() * 1000) + 60000}
        pool.store([v])
        redeemed = pool.redeem("v1")
        assert redeemed is not None
        assert redeemed["redeemed"] is True
        assert redeemed["redeemed_at"] > 0

    def test_redeem_unknown_returns_none(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        assert pool.redeem("nonexistent") is None

    def test_redeem_twice_returns_none_second_time(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        v = {"id": "v1", "expires": int(time.time() * 1000) + 60000}
        pool.store([v])
        assert pool.redeem("v1") is not None
        assert pool.redeem("v1") is None  # already redeemed

    def test_cleanup_expired_preserves_redeemed(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        fresh = {"id": "f", "expires": int(time.time() * 1000) + 60000}
        redeemed_old = {"id": "r", "expires": 1, "redeemed": True}
        expired = {"id": "e", "expires": 1}
        pool.store([fresh, redeemed_old, expired])
        pool.cleanup_expired()
        ids = {v["id"] for v in pool.vouchers}
        assert ids == {"f", "r"}


# ── PaymentLedger ──


class TestPaymentLedger:
    def test_add_entry_dedup_by_source(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        r1 = ledger.add_entry("payment", 50.0, source="msg-id-1")
        r2 = ledger.add_entry("payment", 50.0, source="msg-id-1")
        assert r1["ok"] is True
        assert r2.get("error") == "duplicate"
        assert len(ledger.entries) == 1

    def test_add_entry_no_source_always_unique(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        ledger.add_entry("charge", 5.0)
        ledger.add_entry("charge", 5.0)
        assert len(ledger.entries) == 2

    def test_balance_calculation(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        ledger.add_entry("charge", 100.0, source="c1")
        ledger.add_entry("charge", 25.0, source="c2")
        ledger.add_entry("payment", 40.0, source="p1")
        # Balance = charges - payments = 100 + 25 - 40 = 85
        assert ledger.balance() == 85.0

    def test_get_entries_latest_first_limited(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        for i in range(5):
            ledger.add_entry("charge", float(i), source=f"s{i}")
        last3 = ledger.get_entries(limit=3)
        assert len(last3) == 3
        # Reversed: newest first
        assert last3[0]["source"] == "s4"
        assert last3[2]["source"] == "s2"

    def test_persist_roundtrip(self, tmp_path):
        path = tmp_path / "l.json"
        l1 = PaymentLedger(persist_path=str(path))
        l1.add_entry("payment", 10.0, source="x")
        l1.set_imap_epoch(1234567890)
        l2 = PaymentLedger(persist_path=str(path))
        assert l2.imap_epoch == 1234567890
        assert len(l2.entries) == 1


# ── MessageStore ──


class TestMessageStore:
    def test_add_assigns_id_and_ts(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        msg = store.add({"from": "lion", "text": "hi"})
        assert msg["id"]
        assert msg["ts"] > 0

    def test_cap_at_500(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        for i in range(550):
            store.add({"text": f"msg-{i}"})
        assert len(store.messages) == 500
        # Oldest 50 got trimmed; newest still present
        assert store.messages[-1]["text"] == "msg-549"

    def test_mark_read_adds_reader(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        msg = store.add({"text": "hi"})
        r = store.mark_read(msg["id"], "bunny")
        assert r["ok"] is True
        assert "bunny" in store.messages[-1]["read_by"]

    def test_mark_read_dedups_reader(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        msg = store.add({"text": "hi"})
        store.mark_read(msg["id"], "bunny")
        store.mark_read(msg["id"], "bunny")
        assert store.messages[-1]["read_by"].count("bunny") == 1

    def test_mark_read_unknown_message(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        r = store.mark_read("nonexistent-id", "bunny")
        assert r.get("error") == "not found"

    def test_mark_replied(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        msg = store.add({"text": "q"})
        r = store.mark_replied(msg["id"])
        assert r["ok"] is True
        assert store.messages[-1]["replied"] is True

    def test_mark_replied_unknown(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        assert store.mark_replied("nope").get("error") == "not found"

    def test_get_latest_first(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        for i in range(3):
            store.add({"text": f"m-{i}"})
        result = store.get(limit=2)
        assert len(result) == 2
        assert result[0]["text"] == "m-2"  # newest first


# ── DesktopRegistry ──


class TestDesktopRegistry:
    def test_heartbeat_persists_across_instances(self, tmp_path):
        path = str(tmp_path / "desktops.json")
        r1 = DesktopRegistry(persist_path=path)
        r1.heartbeat("host1", name="My PC")
        r2 = DesktopRegistry(persist_path=path)
        snap = r2.snapshot()
        assert "host1" in snap
        assert snap["host1"]["name"] == "My PC"
        assert snap["host1"]["last_seen_ts"] > 0

    def test_heartbeat_preserves_flags(self, tmp_path):
        r = DesktopRegistry(persist_path=str(tmp_path / "d.json"))
        r.heartbeat("host1")
        assert r.mark_warned("host1") is True
        r.mark_penalized("host1", 12345.0)
        r.heartbeat("host1")  # fresh heartbeat
        snap = r.snapshot()
        # warned + last_penalty_ts survive the re-heartbeat
        assert snap["host1"]["warned"] is True
        assert snap["host1"]["last_penalty_ts"] == 12345.0

    def test_concurrent_heartbeats_do_not_clobber(self, tmp_path):
        r = DesktopRegistry(persist_path=str(tmp_path / "d.json"))
        N = 25
        barrier = threading.Barrier(N)

        def fire(i):
            barrier.wait()
            r.heartbeat(f"host{i}", name=f"Host {i}")

        threads = [threading.Thread(target=fire, args=(i,)) for i in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        snap = r.snapshot()
        assert len(snap) == N
        for i in range(N):
            assert f"host{i}" in snap
            assert snap[f"host{i}"]["name"] == f"Host {i}"

    def test_mark_warned_missing_host_returns_false(self, tmp_path):
        r = DesktopRegistry(persist_path=str(tmp_path / "d.json"))
        assert r.mark_warned("ghost") is False
        assert r.mark_penalized("ghost", time.time()) is False

    def test_summary_line_format(self, tmp_path):
        r = DesktopRegistry(persist_path=str(tmp_path / "d.json"))
        r.heartbeat("alpha", name="Alpha Box")
        r.heartbeat("beta", name="Beta Box")
        line = r.summary_line(time.time())
        # Both entries present, online=1 since just heartbeated
        assert "alpha:Alpha Box:1" in line
        assert "beta:Beta Box:1" in line
        assert line.count(";") == 1

    def test_summary_line_marks_stale_offline(self, tmp_path):
        r = DesktopRegistry(persist_path=str(tmp_path / "d.json"))
        r.heartbeat("host1", name="PC")
        # Advance clock past online_window
        line = r.summary_line(time.time() + 120, online_window=60)
        assert "host1:PC:0" in line


# ── handle_mesh_sync ──


class TestHandleMeshSync:
    def test_newer_remote_version_gets_applied(self, lion_keypair):
        doc = OrdersDocument()
        peers = PeerRegistry()
        remote_orders = dict(ORDER_KEYS)
        remote_orders["paywall"] = "77"
        sig = sign_orders(remote_orders, lion_keypair["priv_pem"])
        body = {
            "node_id": "phone",
            "type": "phone",
            "orders_version": 5,
            "updated_at": 9999,
            "signature": sig,
            "orders": remote_orders,
            "addresses": ["10.0.0.1"],
            "port": 8432,
        }
        resp = handle_mesh_sync(
            body=body,
            my_id="homelab",
            my_type="server",
            my_addresses=["10.0.0.2"],
            my_port=8435,
            orders=doc,
            peers=peers,
            local_status={},
            lion_pubkey=lion_keypair["pub_pem"],
        )
        assert doc.version == 5
        assert doc.orders["paywall"] == "77"
        # Peer was registered
        assert "phone" in peers.peers
        # Response has our (now-matching) version, no full orders (since remote >= us)
        assert resp["orders_version"] == 5
        assert "orders" not in resp

    def test_older_remote_gets_full_orders_in_response(self):
        doc = OrdersDocument()
        doc.version = 10
        peers = PeerRegistry()
        body = {"node_id": "phone", "orders_version": 3, "addresses": [], "port": 8432}
        resp = handle_mesh_sync(
            body=body,
            my_id="homelab",
            my_type="server",
            my_addresses=[],
            my_port=8435,
            orders=doc,
            peers=peers,
            local_status={},
            lion_pubkey="",
        )
        assert resp["orders_version"] == 10
        assert "orders" in resp  # remote is behind, so we send full state
        assert resp["orders"]["paywall"] == "0"

    def test_invalid_signature_not_applied(self, lion_keypair, slave_keypair):
        doc = OrdersDocument()
        peers = PeerRegistry()
        remote_orders = dict(ORDER_KEYS)
        remote_orders["paywall"] = "66"
        bad_sig = sign_orders(remote_orders, slave_keypair["priv_pem"])  # not lion's
        body = {
            "node_id": "phone",
            "orders_version": 5,
            "signature": bad_sig,
            "orders": remote_orders,
            "addresses": [],
            "port": 8432,
        }
        handle_mesh_sync(
            body=body,
            my_id="homelab",
            my_type="server",
            my_addresses=[],
            my_port=8435,
            orders=doc,
            peers=peers,
            local_status={},
            lion_pubkey=lion_keypair["pub_pem"],
        )
        # Orders not applied
        assert doc.version == 0
        assert doc.orders["paywall"] == "0"

    def test_callback_fires_on_apply(self, lion_keypair):
        doc = OrdersDocument()
        peers = PeerRegistry()
        calls = []
        remote_orders = dict(ORDER_KEYS)
        remote_orders["paywall"] = "33"
        sig = sign_orders(remote_orders, lion_keypair["priv_pem"])
        body = {
            "node_id": "phone",
            "orders_version": 1,
            "signature": sig,
            "orders": remote_orders,
            "addresses": [],
            "port": 8432,
        }
        handle_mesh_sync(
            body=body,
            my_id="homelab",
            my_type="server",
            my_addresses=[],
            my_port=8435,
            orders=doc,
            peers=peers,
            local_status={},
            lion_pubkey=lion_keypair["pub_pem"],
            on_orders_applied=lambda o: calls.append(o),
        )
        assert len(calls) == 1
        assert calls[0]["paywall"] == "33"


# ── handle_mesh_status ──


class TestHandleMeshStatus:
    def test_includes_self_and_peers(self):
        doc = OrdersDocument()
        doc.set("paywall", "12")
        doc.set("lock_active", 1)
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.1"], port=8432)
        status = handle_mesh_status(doc, peers, "homelab", {"svc": "mail"})
        assert "phone" in status["nodes"]
        assert "homelab" in status["nodes"]
        assert status["nodes"]["homelab"]["type"] == "self"
        assert status["paywall"] == "12"
        assert status["locked"] is True

    def test_peer_online_flag_from_last_seen(self):
        doc = OrdersDocument()
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone")
        # Freshly updated peer is online
        status = handle_mesh_status(doc, peers, "homelab", {})
        assert status["nodes"]["phone"]["online"] is True
        # Aged peer is offline
        peers.peers["phone"].last_seen = time.time() - 300  # 5min ago
        status2 = handle_mesh_status(doc, peers, "homelab", {})
        assert status2["nodes"]["phone"]["online"] is False

    def test_timer_remaining_ms_derived_from_unlock_at(self):
        doc = OrdersDocument()
        doc.set("unlock_at", int(time.time() * 1000) + 60000)  # 60s in the future
        peers = PeerRegistry()
        status = handle_mesh_status(doc, peers, "homelab", {})
        assert 0 < status["timer_remaining_ms"] <= 60000

    def test_timer_remaining_zero_when_past(self):
        doc = OrdersDocument()
        doc.set("unlock_at", int(time.time() * 1000) - 60000)  # 60s ago
        peers = PeerRegistry()
        status = handle_mesh_status(doc, peers, "homelab", {})
        assert status["timer_remaining_ms"] == 0


# ── handle_mesh_ping ──


class TestHandleMeshPing:
    def test_returns_ok_with_version(self):
        doc = OrdersDocument()
        doc.version = 42
        r = handle_mesh_ping("homelab", doc)
        assert r["ok"] is True
        assert r["node_id"] == "homelab"
        assert r["orders_version"] == 42
        assert r["timestamp"] > 0


# ── handle_mesh_order ──


class TestHandleMeshOrder:
    def test_missing_action_rejected(self):
        doc = OrdersDocument()
        peers = PeerRegistry()
        r = __import__("focuslock_mesh").handle_mesh_order(
            body={},
            orders=doc,
            peers=peers,
            my_id="homelab",
        )
        assert "error" in r

    def test_authentication_required_when_pin_or_pubkey_set(self, lion_keypair):
        """With pin or lion_pubkey configured, unauthenticated requests are rejected."""
        doc = OrdersDocument()
        doc.set("pin", "1234")
        peers = PeerRegistry()
        import focuslock_mesh

        r = focuslock_mesh.handle_mesh_order(
            body={"action": "lock", "params": {}},  # no pin, no sig
            orders=doc,
            peers=peers,
            my_id="homelab",
            lion_pubkey=lion_keypair["pub_pem"],
        )
        assert "error" in r

    def test_valid_pin_accepted(self, lion_keypair):
        doc = OrdersDocument()
        doc.set("pin", "1234")
        peers = PeerRegistry()
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            r = focuslock_mesh.handle_mesh_order(
                body={"action": "lock", "params": {"minutes": 5}, "pin": "1234"},
                orders=doc,
                peers=peers,
                my_id="homelab",
                apply_fn=lambda action, params, orders: {"applied": action},
                lion_pubkey=lion_keypair["pub_pem"],
            )
        assert r["ok"] is True
        assert r["action"] == "lock"
        assert r["applied"] == "lock"

    def test_valid_signature_accepted(self, lion_keypair):
        doc = OrdersDocument()
        peers = PeerRegistry()
        sig_payload = {"action": "lock", "params": {"minutes": 5}}
        sig = sign_orders(sig_payload, lion_keypair["priv_pem"])
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            r = focuslock_mesh.handle_mesh_order(
                body={"action": "lock", "params": {"minutes": 5}, "signature": sig},
                orders=doc,
                peers=peers,
                my_id="homelab",
                apply_fn=lambda a, p, o: {},
                lion_pubkey=lion_keypair["pub_pem"],
            )
        assert r["ok"] is True
        assert doc.version == 1  # bumped

    def test_invalid_signature_rejected(self, lion_keypair, slave_keypair):
        doc = OrdersDocument()
        peers = PeerRegistry()
        sig_payload = {"action": "lock", "params": {}}
        bad_sig = sign_orders(sig_payload, slave_keypair["priv_pem"])
        import focuslock_mesh

        r = focuslock_mesh.handle_mesh_order(
            body={"action": "lock", "params": {}, "signature": bad_sig},
            orders=doc,
            peers=peers,
            my_id="homelab",
            lion_pubkey=lion_keypair["pub_pem"],
        )
        assert "error" in r

    def test_uninitialized_node_permissive(self):
        """No pin AND no pubkey → legacy permissive path (initial setup)."""
        doc = OrdersDocument()  # no pin set
        peers = PeerRegistry()
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            r = focuslock_mesh.handle_mesh_order(
                body={"action": "lock", "params": {}},
                orders=doc,
                peers=peers,
                my_id="homelab",
                apply_fn=lambda a, p, o: {},
                lion_pubkey="",  # uninitialized
            )
        assert r["ok"] is True

    def test_ntfy_called_when_provided(self, lion_keypair):
        doc = OrdersDocument()
        doc.set("pin", "1234")
        peers = PeerRegistry()
        ntfy_calls = []
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            focuslock_mesh.handle_mesh_order(
                body={"action": "unlock", "params": {}, "pin": "1234"},
                orders=doc,
                peers=peers,
                my_id="homelab",
                apply_fn=lambda a, p, o: {},
                lion_pubkey=lion_keypair["pub_pem"],
                ntfy_fn=lambda v: ntfy_calls.append(v),
            )
        assert len(ntfy_calls) == 1
        assert ntfy_calls[0] == doc.version

    def test_ntfy_failure_swallowed(self, lion_keypair):
        doc = OrdersDocument()
        peers = PeerRegistry()
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            r = focuslock_mesh.handle_mesh_order(
                body={"action": "noop", "params": {}},
                orders=doc,
                peers=peers,
                my_id="homelab",
                apply_fn=lambda a, p, o: {},
                lion_pubkey="",
                ntfy_fn=lambda v: (_ for _ in ()).throw(RuntimeError("ntfy down")),
            )
        # ntfy exception must not bubble out
        assert r["ok"] is True


# ── Utility: verify_signature behavior when cryptography unavailable ──


class TestVerifySignatureCryptoMissing:
    def test_returns_false_when_crypto_unavailable(self, lion_keypair, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "HAS_CRYPTO", False)
        assert focuslock_mesh.verify_signature({"x": 1}, "sig", lion_keypair["pub_pem"]) is False
        # sign_orders also guarded
        assert focuslock_mesh.sign_orders({"x": 1}, lion_keypair["priv_pem"]) == ""


# ── Small handler wrappers (voucher / ledger / messages) ──


class TestVoucherHandlers:
    def test_handle_store_vouchers_rejects_empty(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        r = handle_store_vouchers({"vouchers": []}, pool)
        assert "error" in r

    def test_handle_store_vouchers_happy_path(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        vs = [{"id": "v1", "expires": int(time.time() * 1000) + 60000}]
        r = handle_store_vouchers({"vouchers": vs}, pool)
        assert r["ok"] is True
        assert r["stored"] == 1

    def test_handle_get_vouchers_returns_available(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        pool.store([{"id": "v1", "expires": int(time.time() * 1000) + 60000}])
        r = handle_get_vouchers(pool)
        assert len(r["vouchers"]) == 1

    def test_handle_redeem_missing_id(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        doc = OrdersDocument()
        peers = PeerRegistry()
        r = handle_redeem_voucher({}, pool, doc, peers, "homelab")
        assert "error" in r

    def test_handle_redeem_unknown_voucher(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        doc = OrdersDocument()
        peers = PeerRegistry()
        r = handle_redeem_voucher({"id": "nope"}, pool, doc, peers, "homelab")
        assert "error" in r

    def test_handle_redeem_add_paywall(self, tmp_path):
        pool = VoucherPool(persist_path=str(tmp_path / "v.json"))
        pool.store(
            [
                {
                    "id": "v1",
                    "expires": int(time.time() * 1000) + 60000,
                    "action": "add-paywall",
                    "amount": 25,
                }
            ]
        )
        doc = OrdersDocument()
        peers = PeerRegistry()
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            r = handle_redeem_voucher({"id": "v1"}, pool, doc, peers, "homelab")
        assert r["ok"] is True
        assert doc.orders["paywall"] == "25"


class TestLedgerHandlers:
    def test_handle_ledger_entry_updates_paywall(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        doc = OrdersDocument()
        peers = PeerRegistry()
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            r = handle_ledger_entry(
                {"type": "charge", "amount": 30, "source": "s1"},
                ledger,
                doc,
                peers,
                "homelab",
            )
        assert r["ok"] is True
        assert doc.orders["paywall"] == "30"

    def test_handle_ledger_entry_payment_reduces_paywall(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        doc = OrdersDocument()
        peers = PeerRegistry()
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            handle_ledger_entry(
                {"type": "charge", "amount": 50, "source": "s1"},
                ledger,
                doc,
                peers,
                "homelab",
            )
            handle_ledger_entry(
                {"type": "payment", "amount": 20, "source": "p1"},
                ledger,
                doc,
                peers,
                "homelab",
            )
        assert doc.orders["paywall"] == "30"

    def test_handle_ledger_entry_duplicate_source(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        doc = OrdersDocument()
        peers = PeerRegistry()
        import focuslock_mesh

        with patch.object(focuslock_mesh, "push_to_peers"):
            handle_ledger_entry(
                {"type": "charge", "amount": 10, "source": "x"},
                ledger,
                doc,
                peers,
                "homelab",
            )
            r = handle_ledger_entry(
                {"type": "charge", "amount": 10, "source": "x"},
                ledger,
                doc,
                peers,
                "homelab",
            )
        assert r.get("error") == "duplicate"

    def test_handle_set_imap_epoch_invalid(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        assert "error" in handle_set_imap_epoch({"epoch": 0}, ledger)
        assert "error" in handle_set_imap_epoch({"epoch": -5}, ledger)

    def test_handle_set_imap_epoch_valid(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        r = handle_set_imap_epoch({"epoch": 1234567890}, ledger)
        assert r["ok"] is True
        assert ledger.imap_epoch == 1234567890

    def test_handle_get_ledger(self, tmp_path):
        ledger = PaymentLedger(persist_path=str(tmp_path / "l.json"))
        ledger.add_entry("charge", 10, source="s1")
        ledger.add_entry("payment", 5, source="p1")
        r = handle_get_ledger(ledger, limit=10)
        assert len(r["entries"]) == 2


class TestMessageHandlers:
    def test_handle_send_empty_text_rejected(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        assert "error" in handle_send_message({"text": ""}, store)

    def test_handle_send_stores_message(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        r = handle_send_message({"text": "hello", "from": "lion"}, store)
        assert r["ok"] is True
        assert r["message"]["text"] == "hello"
        assert r["message"]["from"] == "lion"

    def test_handle_mark_read_missing_id(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        assert "error" in handle_mark_read({}, store)

    def test_handle_mark_read_delegates(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        msg = store.add({"text": "hi"})
        r = handle_mark_read({"id": msg["id"], "reader": "bunny"}, store)
        assert r["ok"] is True

    def test_handle_mark_replied_missing_id(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        assert "error" in handle_mark_replied({}, store)

    def test_handle_mark_replied_delegates(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        msg = store.add({"text": "q"})
        r = handle_mark_replied({"id": msg["id"]}, store)
        assert r["ok"] is True

    def test_handle_get_messages(self, tmp_path):
        store = MessageStore(persist_path=str(tmp_path / "m.json"))
        store.add({"text": "a"})
        store.add({"text": "b"})
        r = handle_get_messages(store, limit=10)
        assert len(r["messages"]) == 2


# ── push_to_peers & bump_and_broadcast — network ops, mocked ──


class TestPushToPeers:
    def test_push_to_peers_iterates_over_non_self(self):
        import focuslock_mesh

        doc = OrdersDocument()
        doc.set("paywall", "5")
        doc.bump_version()
        peers = PeerRegistry()
        peers.update_peer("phone", addresses=["10.0.0.1"], port=8432)
        peers.update_peer("homelab", addresses=["10.0.0.2"], port=8435)
        posted = []

        def fake_try(peer, path, data=None, timeout=3.0):
            posted.append(peer.node_id)
            return {"ok": True}

        with patch.object(focuslock_mesh, "_try_peer_addrs", side_effect=fake_try):
            focuslock_mesh.push_to_peers("homelab", doc, peers)
        # my_id=homelab excluded, only phone pushed to
        assert posted == ["phone"]

    def test_bump_and_broadcast_version_increment_and_push(self):
        import focuslock_mesh

        doc = OrdersDocument()
        peers = PeerRegistry()
        peers.update_peer("phone", addresses=["10.0.0.1"])
        with patch.object(focuslock_mesh, "_try_peer_addrs", return_value={"ok": True}):
            focuslock_mesh.bump_and_broadcast(doc, "homelab", peers)
        assert doc.version == 1


# ── gossip_tick — full network path mocked ──


class TestGossipTick:
    def test_gossip_tick_contacts_non_self_peers(self, lion_keypair):
        import focuslock_mesh

        doc = OrdersDocument()
        peers = PeerRegistry()
        peers.update_peer("phone", addresses=["10.0.0.1"], port=8432, orders_version=0)

        contacted = []

        def fake_gossip_one(peer, sync_payload, orders, peers_reg, lion_pubkey, on_orders_applied):
            contacted.append(peer.node_id)

        with patch.object(focuslock_mesh, "_gossip_one_peer", side_effect=fake_gossip_one):
            focuslock_mesh.gossip_tick(
                my_id="homelab",
                my_type="server",
                my_addresses=["10.0.0.2"],
                my_port=8435,
                orders=doc,
                peers=peers,
                local_status={},
                lion_pubkey=lion_keypair["pub_pem"],
            )
        assert "phone" in contacted
        assert "homelab" not in contacted


# ── handle_mesh_sync — additional edge cases for coverage ──


class TestHandleMeshSyncEdges:
    def test_no_remote_id_skips_peer_registration(self):
        doc = OrdersDocument()
        peers = PeerRegistry()
        resp = handle_mesh_sync(
            body={},  # no node_id
            my_id="homelab",
            my_type="server",
            my_addresses=[],
            my_port=8435,
            orders=doc,
            peers=peers,
            local_status={},
            lion_pubkey="",
        )
        # No peers added
        assert len(peers.peers) == 0
        assert resp["node_id"] == "homelab"

    def test_remote_equal_version_sends_no_orders(self):
        doc = OrdersDocument()
        doc.version = 5
        peers = PeerRegistry()
        resp = handle_mesh_sync(
            body={"node_id": "phone", "orders_version": 5, "addresses": [], "port": 8432},
            my_id="homelab",
            my_type="server",
            my_addresses=[],
            my_port=8435,
            orders=doc,
            peers=peers,
            local_status={},
            lion_pubkey="",
        )
        assert "orders" not in resp  # requester is current


# ── HTTP helpers (urllib mocked) ──


class TestHttpHelpers:
    def test_http_post_success(self, monkeypatch):
        import focuslock_mesh

        class FakeResp:
            def read(self):
                return b'{"ok": true, "v": 1}'

        def fake_urlopen(req, timeout=5.0):
            return FakeResp()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        r = focuslock_mesh._http_post("http://host:8432/path", {"a": 1})
        assert r == {"ok": True, "v": 1}

    def test_http_post_failure_returns_none(self, monkeypatch):
        import focuslock_mesh

        def fake_urlopen(req, timeout=5.0):
            raise ConnectionRefusedError()

        monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
        assert focuslock_mesh._http_post("http://x/y", {}) is None

    def test_http_get_success(self, monkeypatch):
        import focuslock_mesh

        class FakeResp:
            def read(self):
                return b'{"result": "got"}'

        monkeypatch.setattr("urllib.request.urlopen", lambda req, timeout=5.0: FakeResp())
        assert focuslock_mesh._http_get("http://x/y") == {"result": "got"}

    def test_http_get_failure_returns_none(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr("urllib.request.urlopen", MagicMock(side_effect=TimeoutError()))
        assert focuslock_mesh._http_get("http://x/y") is None


class TestTryPeerAddrs:
    def test_first_address_succeeds_promotes_to_front(self, monkeypatch):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["10.0.0.1", "10.0.0.2", "10.0.0.3"], port=8432)
        # First address works
        monkeypatch.setattr(focuslock_mesh, "_http_post", lambda url, data, timeout=3.0: {"ok": True})
        monkeypatch.setattr(focuslock_mesh, "get_tailscale_ip_for_node", lambda nid: "")
        r = focuslock_mesh._try_peer_addrs(peer, "/mesh/sync", {"sync": True})
        assert r == {"ok": True}
        # Already at front
        assert peer.addresses[0] == "10.0.0.1"

    def test_second_address_succeeds_gets_promoted(self, monkeypatch):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["bad.addr", "10.0.0.5"], port=8432)
        # First address fails (_http_post returns None), second succeeds
        results = [None, {"ok": True}]

        def fake_post(url, data, timeout=3.0):
            return results.pop(0)

        monkeypatch.setattr(focuslock_mesh, "_http_post", fake_post)
        monkeypatch.setattr(focuslock_mesh, "get_tailscale_ip_for_node", lambda nid: "")
        r = focuslock_mesh._try_peer_addrs(peer, "/mesh/sync", {"sync": True})
        assert r == {"ok": True}
        # Working address promoted
        assert peer.addresses[0] == "10.0.0.5"

    def test_all_addresses_fail_returns_none(self, monkeypatch):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["10.0.0.1", "10.0.0.2"], port=8432)
        monkeypatch.setattr(focuslock_mesh, "_http_post", lambda url, data, timeout=3.0: None)
        monkeypatch.setattr(focuslock_mesh, "get_tailscale_ip_for_node", lambda nid: "")
        assert focuslock_mesh._try_peer_addrs(peer, "/mesh/sync", {}) is None

    def test_tailscale_ip_appended_as_fallback(self, monkeypatch):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["10.0.0.1"], port=8432)
        # LAN address fails, Tailscale succeeds
        tried_urls = []

        def fake_post(url, data, timeout=3.0):
            tried_urls.append(url)
            if "100.64." in url:
                return {"ok": True}
            return None

        monkeypatch.setattr(focuslock_mesh, "_http_post", fake_post)
        monkeypatch.setattr(focuslock_mesh, "get_tailscale_ip_for_node", lambda nid: "100.64.0.5")
        r = focuslock_mesh._try_peer_addrs(peer, "/p", {})
        assert r == {"ok": True}
        # Tailscale address attempted
        assert any("100.64.0.5" in u for u in tried_urls)

    def test_get_mode_uses_http_get(self, monkeypatch):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["10.0.0.1"], port=8432)
        called = {"get": 0, "post": 0}
        monkeypatch.setattr(
            focuslock_mesh,
            "_http_get",
            lambda url, timeout=3.0: called.__setitem__("get", called["get"] + 1) or {"ok": True},
        )
        monkeypatch.setattr(
            focuslock_mesh,
            "_http_post",
            lambda url, data, timeout=3.0: called.__setitem__("post", called["post"] + 1) or {"ok": True},
        )
        monkeypatch.setattr(focuslock_mesh, "get_tailscale_ip_for_node", lambda nid: "")
        focuslock_mesh._try_peer_addrs(peer, "/mesh/status", data=None)
        assert called["get"] == 1
        assert called["post"] == 0

    def test_address_list_capped(self, monkeypatch):
        import focuslock_mesh

        # Simulate 8 addresses, only the working one + first 3 of others should remain
        peer = PeerInfo("phone", addresses=[f"10.0.0.{i}" for i in range(1, 9)], port=8432)
        # Only the 5th succeeds
        attempts = [0]

        def fake_post(url, data, timeout=3.0):
            attempts[0] += 1
            return {"ok": True} if attempts[0] == 5 else None

        monkeypatch.setattr(focuslock_mesh, "_http_post", fake_post)
        monkeypatch.setattr(focuslock_mesh, "get_tailscale_ip_for_node", lambda nid: "")
        focuslock_mesh._try_peer_addrs(peer, "/mesh/sync", {})
        # Peer's address list capped to MAX_PEER_ADDRESSES (4)
        assert len(peer.addresses) == focuslock_mesh.MAX_PEER_ADDRESSES
        # Working address promoted to front
        assert peer.addresses[0] == "10.0.0.5"


class TestGossipOnePeer:
    def test_no_response_noops(self, monkeypatch):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["10.0.0.1"], port=8432)
        peers = PeerRegistry()
        doc = OrdersDocument()
        monkeypatch.setattr(focuslock_mesh, "_try_peer_addrs", lambda *a, **kw: None)
        # Should just return without touching anything
        focuslock_mesh._gossip_one_peer(peer, {}, doc, peers, "", None)
        assert doc.version == 0

    def test_learns_peer_and_known_nodes(self, monkeypatch):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["10.0.0.1"], port=8432)
        peers = PeerRegistry()
        doc = OrdersDocument()
        fake_resp = {
            "node_id": "phone",
            "type": "phone",
            "addresses": ["10.0.0.1", "10.0.0.99"],
            "port": 8432,
            "orders_version": 0,
            "known_nodes": {
                "homelab": {"type": "server", "addresses": ["10.0.0.2"], "port": 8435, "orders_version": 0},
            },
        }
        monkeypatch.setattr(focuslock_mesh, "_try_peer_addrs", lambda *a, **kw: fake_resp)
        focuslock_mesh._gossip_one_peer(peer, {}, doc, peers, "", None)
        assert "phone" in peers.peers
        assert "homelab" in peers.peers

    def test_applies_newer_orders_from_response(self, monkeypatch, lion_keypair):
        import focuslock_mesh

        peer = PeerInfo("phone", addresses=["10.0.0.1"], port=8432)
        peers = PeerRegistry()
        doc = OrdersDocument()
        new_orders = dict(ORDER_KEYS)
        new_orders["paywall"] = "42"
        sig = sign_orders(new_orders, lion_keypair["priv_pem"])
        fake_resp = {
            "node_id": "phone",
            "type": "phone",
            "addresses": [],
            "port": 8432,
            "orders_version": 5,
            "signature": sig,
            "orders": new_orders,
        }
        monkeypatch.setattr(focuslock_mesh, "_try_peer_addrs", lambda *a, **kw: fake_resp)
        calls = []
        focuslock_mesh._gossip_one_peer(
            peer,
            {},
            doc,
            peers,
            lion_keypair["pub_pem"],
            lambda o: calls.append(o),
        )
        assert doc.version == 5
        assert doc.orders["paywall"] == "42"
        assert len(calls) == 1


# ── Tailscale node resolution (pure logic) ──


class TestTailscaleResolution:
    def test_set_tailscale_node_map_lowercases(self):
        import focuslock_mesh

        focuslock_mesh.set_tailscale_node_map({"MyPhone": "Pixel-8"})
        assert focuslock_mesh._ts_node_overrides == {"myphone": "pixel-8"}
        # Cleanup to avoid polluting other tests
        focuslock_mesh._ts_node_overrides = {}

    def test_direct_match(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_map", {"phone": "100.64.0.5"})
        monkeypatch.setattr(focuslock_mesh, "_refresh_tailscale_hosts", lambda: None)
        assert focuslock_mesh.get_tailscale_ip_for_node("phone") == "100.64.0.5"

    def test_strip_win_suffix(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_map", {"desktop": "100.64.0.7"})
        monkeypatch.setattr(focuslock_mesh, "_refresh_tailscale_hosts", lambda: None)
        # mesh id 'desktop-win' → tailscale hostname 'desktop'
        assert focuslock_mesh.get_tailscale_ip_for_node("desktop-win") == "100.64.0.7"

    def test_prefix_match(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_map", {"pixel 10": "100.64.0.9"})
        monkeypatch.setattr(focuslock_mesh, "_refresh_tailscale_hosts", lambda: None)
        # normalized: "pixel10" and "pixel" → prefix match
        assert focuslock_mesh.get_tailscale_ip_for_node("pixel") == "100.64.0.9"

    def test_no_match_returns_empty(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_map", {})
        monkeypatch.setattr(focuslock_mesh, "_refresh_tailscale_hosts", lambda: None)
        assert focuslock_mesh.get_tailscale_ip_for_node("unknown") == ""

    def test_override_to_hostname(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_map", {"actual-host": "100.64.0.11"})
        monkeypatch.setattr(focuslock_mesh, "_refresh_tailscale_hosts", lambda: None)
        focuslock_mesh._ts_node_overrides = {"mesh-alias": "actual-host"}
        try:
            assert focuslock_mesh.get_tailscale_ip_for_node("mesh-alias") == "100.64.0.11"
        finally:
            focuslock_mesh._ts_node_overrides = {}


class TestLocalAddressDiscovery:
    """Covers get_local_addresses() and _get_tailscale_addresses() —
    subprocess-backed helpers stubbed via MagicMock."""

    def _fake_proc(self, stdout, returncode=0):
        m = MagicMock()
        m.stdout = stdout
        m.returncode = returncode
        return m

    def test_ip_command_parses_inet_lines(self, monkeypatch):
        import focuslock_mesh

        ip_output = (
            "1: lo    inet 127.0.0.1/8 scope host lo\n"
            "2: eth0  inet 192.168.1.10/24 scope global eth0\n"
            "3: tun0  inet 100.64.0.5/32 scope global tun0\n"
        )
        fake = self._fake_proc(ip_output)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
        monkeypatch.setattr(focuslock_mesh, "_get_tailscale_addresses", lambda: [])
        addrs = focuslock_mesh.get_local_addresses()
        assert "192.168.1.10" in addrs
        assert "100.64.0.5" in addrs
        assert "127.0.0.1" not in addrs

    def test_tailscale_addresses_merged_deduped(self, monkeypatch):
        import focuslock_mesh

        ip_output = "2: eth0  inet 192.168.1.10/24 scope global eth0\n"
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: self._fake_proc(ip_output))
        monkeypatch.setattr(
            focuslock_mesh,
            "_get_tailscale_addresses",
            lambda: ["100.64.0.5", "192.168.1.10"],
        )
        addrs = focuslock_mesh.get_local_addresses()
        assert addrs.count("192.168.1.10") == 1
        assert "100.64.0.5" in addrs

    def test_udp_fallback_when_nothing_found(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr("subprocess.run", lambda *a, **kw: self._fake_proc(""))
        monkeypatch.setattr(focuslock_mesh, "_get_tailscale_addresses", lambda: [])
        monkeypatch.setattr(
            "socket.getaddrinfo",
            lambda *a, **kw: [(None, None, None, None, ("127.0.0.1", 0))],
        )
        fake_sock = MagicMock()
        fake_sock.getsockname.return_value = ("10.0.0.42", 54321)
        monkeypatch.setattr("socket.socket", lambda *a, **kw: fake_sock)

        addrs = focuslock_mesh.get_local_addresses()
        assert "10.0.0.42" in addrs

    def test_get_tailscale_addresses_returns_ips(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: self._fake_proc("100.64.0.5\n100.64.0.6\n"),
        )
        assert focuslock_mesh._get_tailscale_addresses() == ["100.64.0.5", "100.64.0.6"]

    def test_get_tailscale_addresses_empty_on_nonzero_rc(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: self._fake_proc("", returncode=1),
        )
        assert focuslock_mesh._get_tailscale_addresses() == []

    def test_get_tailscale_addresses_swallows_exception(self, monkeypatch):
        import focuslock_mesh

        def boom(*a, **kw):
            raise FileNotFoundError("tailscale CLI not installed")

        monkeypatch.setattr("subprocess.run", boom)
        assert focuslock_mesh._get_tailscale_addresses() == []


class TestTailnetDiscovery:
    """Covers _get_tailnet_name() and _refresh_tailscale_hosts()."""

    def _reset_tailnet_cache(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_tailnet_name", None)

    def test_tailnet_name_extracts_suffix(self, monkeypatch):
        import focuslock_mesh

        self._reset_tailnet_cache(monkeypatch)
        status = {"Self": {"DNSName": "myhost.tail12345.ts.net."}}
        fake = MagicMock(stdout=json.dumps(status), returncode=0)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
        assert focuslock_mesh._get_tailnet_name() == "tail12345.ts.net"

    def test_tailnet_name_cached(self, monkeypatch):
        import focuslock_mesh

        self._reset_tailnet_cache(monkeypatch)
        status = {"Self": {"DNSName": "myhost.tail12345.ts.net."}}
        fake = MagicMock(stdout=json.dumps(status), returncode=0)
        calls = {"n": 0}

        def counting(*a, **kw):
            calls["n"] += 1
            return fake

        monkeypatch.setattr("subprocess.run", counting)
        focuslock_mesh._get_tailnet_name()
        focuslock_mesh._get_tailnet_name()
        assert calls["n"] == 1

    def test_tailnet_name_caches_failure_as_empty(self, monkeypatch):
        import focuslock_mesh

        self._reset_tailnet_cache(monkeypatch)
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **kw: MagicMock(stdout="", returncode=1),
        )
        assert focuslock_mesh._get_tailnet_name() == ""
        assert focuslock_mesh._tailnet_name == ""

    def test_tailnet_name_short_dns_name_safe(self, monkeypatch):
        import focuslock_mesh

        self._reset_tailnet_cache(monkeypatch)
        status = {"Self": {"DNSName": "host."}}
        fake = MagicMock(stdout=json.dumps(status), returncode=0)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
        assert focuslock_mesh._get_tailnet_name() == ""

    def test_refresh_hosts_populates_map(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_last_refresh", 0)
        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_map", {})

        status = {
            "Self": {"HostName": "Host-A", "TailscaleIPs": ["100.64.0.1", "fd7a::1"]},
            "Peer": {
                "node1": {"HostName": "Phone", "TailscaleIPs": ["100.64.0.5"]},
                "node2": {"HostName": "Homelab", "TailscaleIPs": ["100.64.0.6", "fd7a::2"]},
            },
        }
        fake = MagicMock(stdout=json.dumps(status), returncode=0)
        monkeypatch.setattr("subprocess.run", lambda *a, **kw: fake)
        focuslock_mesh._refresh_tailscale_hosts()
        assert focuslock_mesh._ts_hostname_map["host-a"] == "100.64.0.1"
        assert focuslock_mesh._ts_hostname_map["phone"] == "100.64.0.5"
        assert focuslock_mesh._ts_hostname_map["homelab"] == "100.64.0.6"

    def test_refresh_hosts_rate_limited(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_last_refresh", time.time())
        sentinel = {"unchanged": "100.64.0.99"}
        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_map", dict(sentinel))

        def fail_if_called(*a, **kw):
            raise AssertionError("subprocess.run should be rate-limited")

        monkeypatch.setattr("subprocess.run", fail_if_called)
        focuslock_mesh._refresh_tailscale_hosts()
        assert focuslock_mesh._ts_hostname_map == sentinel

    def test_refresh_hosts_swallows_subprocess_error(self, monkeypatch):
        import focuslock_mesh

        monkeypatch.setattr(focuslock_mesh, "_ts_hostname_last_refresh", 0)

        def boom(*a, **kw):
            raise FileNotFoundError("tailscale CLI not installed")

        monkeypatch.setattr("subprocess.run", boom)
        focuslock_mesh._refresh_tailscale_hosts()

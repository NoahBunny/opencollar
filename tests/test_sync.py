"""Tests for shared/focuslock_sync.py — desktop collar mesh sync polling."""

import json
from unittest.mock import MagicMock, patch

from focuslock_sync import direct_sync_poll, relay_to_phones, try_sync

from focuslock_mesh import OrdersDocument, PeerRegistry, sign_orders


def _mock_response(payload: dict) -> MagicMock:
    """Build a mock urlopen response object whose .read() yields JSON-encoded payload."""
    resp = MagicMock()
    resp.read.return_value = json.dumps(payload).encode()
    return resp


# ── try_sync ──


class TestTrySyncSuccessPath:
    def test_uses_account_endpoint_when_mesh_id_set(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response({"node_id": "mesh", "orders_version": 0})
            ok = try_sync(
                "https://relay.example",
                "mesh",
                node_id="phone",
                node_type="phone",
                my_addrs=["10.0.0.5"],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={"lock_active": False},
                lion_pubkey="",
                mesh_id="abc123",
            )
        assert ok is True
        req = urlopen.call_args.args[0]
        assert req.full_url == "https://relay.example/api/mesh/abc123/sync"

    def test_uses_legacy_endpoint_when_mesh_id_absent(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response({"orders_version": 0})
            try_sync(
                "http://192.168.1.5:8432",
                "phone",
                node_id="homelab",
                node_type="desktop",
                my_addrs=["192.168.1.10"],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
                mesh_id="",
            )
        req = urlopen.call_args.args[0]
        assert req.full_url == "http://192.168.1.5:8432/mesh/sync"

    def test_payload_structure(self):
        orders = OrdersDocument()
        orders.version = 7
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response({"orders_version": 0})
            try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=["1.2.3.4", "5.6.7.8"],
                mesh_port=9999,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={"lock_active": True, "v": 1},
                lion_pubkey="",
                pin="hunter2",
                mesh_id="",
            )
        req = urlopen.call_args.args[0]
        body = json.loads(req.data.decode())
        assert body == {
            "pin": "hunter2",
            "node_id": "phone",
            "type": "phone",
            "addresses": ["1.2.3.4", "5.6.7.8"],
            "port": 9999,
            "orders_version": 7,
            "status": {"lock_active": True, "v": 1},
        }
        assert req.headers.get("Content-type") == "application/json"

    def test_updates_peer_info_from_response(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        # remote_id "homelab" is whitelisted; "stranger" would be silently ignored
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "node_id": "homelab",
                    "type": "desktop",
                    "addresses": ["10.0.0.50"],
                    "port": 8434,
                    "orders_version": 3,
                    "status": {"lock_active": False},
                }
            )
            try_sync(
                "http://homelab.local",
                "homelab",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
            )
        snap = peers.snapshot()
        assert "homelab" in snap
        assert snap["homelab"].node_type == "desktop"
        assert snap["homelab"].addresses == ["10.0.0.50"]
        assert snap["homelab"].orders_version == 3

    def test_falls_back_to_name_when_response_omits_node_id(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response({"orders_version": 0})
            try_sync(
                "http://x",
                "phone",  # name used when node_id absent
                node_id="homelab",
                node_type="desktop",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
            )
        # "phone" is whitelisted, so it should appear in registry
        assert "phone" in peers.snapshot()

    def test_learns_from_known_nodes(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "node_id": "phone",
                    "orders_version": 0,
                    "known_nodes": {
                        "homelab": {
                            "type": "desktop",
                            "addresses": ["10.0.0.99"],
                            "port": 8433,
                            "orders_version": 5,
                        },
                    },
                }
            )
            try_sync(
                "http://x",
                "x",
                node_id="self-node",
                node_type="desktop",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
            )
        # both peer (from response) AND known_nodes peer learned
        snap = peers.snapshot()
        assert "phone" in snap
        assert "homelab" in snap
        assert snap["homelab"].port == 8433


# ── try_sync — order application ──


class TestTrySyncOrderApplication:
    def test_applies_newer_orders(self, lion_keypair):
        orders = OrdersDocument()
        peers = PeerRegistry()
        remote_orders = {"lock_active": 1, "unlock_at": 1700000000, "mode": "task"}
        sig = sign_orders(remote_orders, lion_keypair["priv_pem"])
        applied_callbacks = []

        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "orders_version": 1,
                    "updated_at": 1234567890,
                    "signature": sig,
                    "orders": remote_orders,
                }
            )
            ok = try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey=lion_keypair["pub_pem"],
                on_orders_applied=applied_callbacks.append,
            )
        assert ok is True
        assert orders.version == 1
        assert orders.get("lock_active") == 1
        assert orders.get("unlock_at") == 1700000000
        assert orders.get("mode") == "task"
        # callback fired exactly once with the applied orders snapshot
        assert len(applied_callbacks) == 1
        assert applied_callbacks[0]["lock_active"] == 1

    def test_skip_apply_when_remote_version_not_higher(self):
        orders = OrdersDocument()
        orders.version = 5
        peers = PeerRegistry()
        applied_callbacks = []
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "orders_version": 5,  # equal — no apply
                    "orders": {"lock_active": 1},
                }
            )
            ok = try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
                on_orders_applied=applied_callbacks.append,
            )
        assert ok is True  # endpoint responded
        assert orders.version == 5
        assert orders.get("lock_active") == 0  # default — never overwritten
        assert applied_callbacks == []

    def test_skip_apply_when_orders_field_absent(self):
        """remote_ver > local but no `orders` field — peer info still updates, no apply."""
        orders = OrdersDocument()
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "orders_version": 99,  # higher
                    # no "orders" key
                }
            )
            ok = try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
            )
        assert ok is True
        assert orders.version == 0

    def test_apply_remote_returns_false_signature_mismatch(self, lion_keypair, slave_keypair):
        """Signed by wrong key → apply_remote rejects, but try_sync still returns True (endpoint did respond)."""
        orders = OrdersDocument()
        peers = PeerRegistry()
        remote_orders = {"lock_active": 1}
        bad_sig = sign_orders(remote_orders, slave_keypair["priv_pem"])  # wrong signer
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "orders_version": 1,
                    "signature": bad_sig,
                    "orders": remote_orders,
                }
            )
            ok = try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey=lion_keypair["pub_pem"],
            )
        assert ok is True
        assert orders.version == 0  # rejected

    def test_apply_with_no_callback_still_returns_true(self, lion_keypair):
        """on_orders_applied=None must not break the apply path."""
        orders = OrdersDocument()
        peers = PeerRegistry()
        remote_orders = {"lock_active": 1}
        sig = sign_orders(remote_orders, lion_keypair["priv_pem"])
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "orders_version": 1,
                    "signature": sig,
                    "orders": remote_orders,
                }
            )
            ok = try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey=lion_keypair["pub_pem"],
                on_orders_applied=None,  # explicit
            )
        assert ok is True
        assert orders.version == 1

    def test_apply_callback_not_invoked_on_signature_failure(self, lion_keypair, slave_keypair):
        orders = OrdersDocument()
        peers = PeerRegistry()
        remote_orders = {"lock_active": 1}
        bad_sig = sign_orders(remote_orders, slave_keypair["priv_pem"])
        called = []
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = _mock_response(
                {
                    "orders_version": 1,
                    "signature": bad_sig,
                    "orders": remote_orders,
                }
            )
            try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey=lion_keypair["pub_pem"],
                on_orders_applied=called.append,
            )
        assert called == []


# ── try_sync — error paths ──


class TestTrySyncErrors:
    def test_http_exception_returns_false(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=OSError("connection refused")):
            ok = try_sync(
                "http://dead",
                "dead",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
            )
        assert ok is False

    def test_malformed_json_response_returns_false(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        bad_resp = MagicMock()
        bad_resp.read.return_value = b"not valid json"
        with patch("focuslock_sync.urllib.request.urlopen", return_value=bad_resp):
            ok = try_sync(
                "http://x",
                "x",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
            )
        assert ok is False

    def test_timeout_returns_false(self):

        orders = OrdersDocument()
        peers = PeerRegistry()
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=TimeoutError("read timed out")):
            ok = try_sync(
                "http://slow",
                "slow",
                node_id="phone",
                node_type="phone",
                my_addrs=[],
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status={},
                lion_pubkey="",
            )
        assert ok is False


# ── direct_sync_poll ──


class TestDirectSyncPollPriority:
    def _common_kwargs(self, **overrides):
        orders = OrdersDocument()
        peers = PeerRegistry()
        defaults = dict(
            node_id="phone",
            node_type="phone",
            mesh_port=8432,
            mesh_orders=orders,
            mesh_peers=peers,
            local_status_fn=lambda: {},
            lion_pubkey_fn=lambda: "",
            get_local_addresses_fn=lambda: ["10.0.0.5"],
            phone_port=8432,
        )
        defaults.update(overrides)
        return defaults

    def test_mesh_url_first_success_short_circuits(self):
        ok_resp = _mock_response({"orders_version": 0})
        with patch("focuslock_sync.urllib.request.urlopen", return_value=ok_resp) as urlopen:
            ok = direct_sync_poll(
                mesh_url="https://relay.example",
                homelab_url="http://homelab",
                phone_addresses=["10.0.0.5"],
                mesh_id="meshA",
                **self._common_kwargs(),
            )
        assert ok is True
        # exactly one HTTP call — mesh_url succeeded, others skipped
        assert urlopen.call_count == 1
        assert urlopen.call_args.args[0].full_url == "https://relay.example/api/mesh/meshA/sync"

    def test_homelab_tried_when_mesh_url_fails(self):
        ok_resp = _mock_response({"orders_version": 0})
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=[OSError(), ok_resp]) as urlopen:
            ok = direct_sync_poll(
                mesh_url="https://relay.example",
                homelab_url="http://homelab",
                phone_addresses=[],
                mesh_id="meshA",
                **self._common_kwargs(),
            )
        assert ok is True
        assert urlopen.call_count == 2
        assert urlopen.call_args_list[1].args[0].full_url == "http://homelab/api/mesh/meshA/sync"

    def test_phones_tried_in_order_after_http_failures(self):
        ok_resp = _mock_response({"orders_version": 0})
        # 1st (mesh_url) fail, 2nd (homelab) fail, 3rd (first phone) fail, 4th (second phone) success
        with patch(
            "focuslock_sync.urllib.request.urlopen",
            side_effect=[OSError(), OSError(), OSError(), ok_resp],
        ) as urlopen:
            ok = direct_sync_poll(
                mesh_url="https://relay",
                homelab_url="http://homelab",
                phone_addresses=["10.0.0.5", "10.0.0.6"],
                mesh_id="",
                **self._common_kwargs(phone_port=8432),
            )
        assert ok is True
        assert urlopen.call_count == 4
        # phone calls use legacy /mesh/sync (no mesh_id)
        assert urlopen.call_args_list[2].args[0].full_url == "http://10.0.0.5:8432/mesh/sync"
        assert urlopen.call_args_list[3].args[0].full_url == "http://10.0.0.6:8432/mesh/sync"

    def test_tailscale_tried_when_provided_and_other_endpoints_fail(self):
        peers = PeerRegistry()
        peers.update_peer("homelab", node_type="desktop", addresses=[], port=8433)
        ok_resp = _mock_response({"orders_version": 0})
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=[OSError(), ok_resp]) as urlopen:
            ok = direct_sync_poll(
                mesh_url="",
                homelab_url="",
                phone_addresses=["10.0.0.5"],
                mesh_id="",
                get_tailscale_ip_fn=lambda nid: "100.64.1.2" if nid == "homelab" else None,
                **self._common_kwargs(mesh_peers=peers),
            )
        assert ok is True
        # phone failed, tailscale succeeded
        assert urlopen.call_count == 2
        assert urlopen.call_args_list[1].args[0].full_url == "http://100.64.1.2:8433/mesh/sync"

    def test_tailscale_skipped_when_fn_not_provided(self):
        peers = PeerRegistry()
        peers.update_peer("homelab", addresses=[], port=8433)
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=OSError()) as urlopen:
            ok = direct_sync_poll(
                mesh_url="",
                homelab_url="",
                phone_addresses=["10.0.0.5"],
                mesh_id="",
                get_tailscale_ip_fn=None,
                **self._common_kwargs(mesh_peers=peers),
            )
        assert ok is False
        # only the one phone call — tailscale path not entered
        assert urlopen.call_count == 1

    def test_tailscale_skips_peer_when_ip_fn_returns_none(self):
        peers = PeerRegistry()
        peers.update_peer("homelab", addresses=[], port=8433)
        peers.update_peer("phone", addresses=[], port=8432)
        # self_id excluded; remaining peer "homelab" returns None → never gets HTTP call
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=OSError()) as urlopen:
            ok = direct_sync_poll(
                mesh_url="",
                homelab_url="",
                phone_addresses=[],
                mesh_id="",
                get_tailscale_ip_fn=lambda nid: None,
                **self._common_kwargs(mesh_peers=peers, node_id="phone"),
            )
        assert ok is False
        assert urlopen.call_count == 0

    def test_tailscale_continues_after_first_peer_sync_fails(self):
        """Two peers both have tailscale IPs; first try_sync fails, second succeeds."""
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=[], port=8432)
        peers.update_peer("homelab", node_type="desktop", addresses=[], port=8433)
        ok_resp = _mock_response({"orders_version": 0})
        with patch(
            "focuslock_sync.urllib.request.urlopen",
            side_effect=[OSError("first ts dead"), ok_resp],
        ) as urlopen:
            ok = direct_sync_poll(
                mesh_url="",
                homelab_url="",
                phone_addresses=[],
                mesh_id="",
                get_tailscale_ip_fn=lambda nid: f"100.64.0.{1 if nid == 'phone' else 2}",
                **self._common_kwargs(mesh_peers=peers, node_id="self-desktop"),
            )
        assert ok is True
        assert urlopen.call_count == 2

    def test_all_endpoints_fail_returns_false(self):
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=OSError()):
            ok = direct_sync_poll(
                mesh_url="https://relay",
                homelab_url="http://homelab",
                phone_addresses=["10.0.0.5"],
                mesh_id="",
                **self._common_kwargs(),
            )
        assert ok is False

    def test_no_endpoints_configured_returns_false(self):
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            ok = direct_sync_poll(
                mesh_url="",
                homelab_url="",
                phone_addresses=[],
                mesh_id="",
                **self._common_kwargs(),
            )
        assert ok is False
        assert urlopen.call_count == 0

    def test_mesh_url_skipped_when_empty(self):
        ok_resp = _mock_response({"orders_version": 0})
        with patch("focuslock_sync.urllib.request.urlopen", return_value=ok_resp) as urlopen:
            direct_sync_poll(
                mesh_url="",  # skipped
                homelab_url="http://homelab",
                phone_addresses=[],
                mesh_id="meshX",
                **self._common_kwargs(),
            )
        # only homelab tried
        assert urlopen.call_count == 1
        assert urlopen.call_args.args[0].full_url == "http://homelab/api/mesh/meshX/sync"


class TestDirectSyncPollFnDispatch:
    """Verify the *_fn parameters are evaluated each poll, not captured at module load."""

    def test_local_addresses_fn_invoked_each_call(self):
        orders = OrdersDocument()
        peers = PeerRegistry()
        addr_calls = []

        def addr_fn():
            addr_calls.append(1)
            return ["10.0.0.5"]

        ok_resp = _mock_response({"orders_version": 0})
        with patch("focuslock_sync.urllib.request.urlopen", return_value=ok_resp):
            direct_sync_poll(
                mesh_url="https://r",
                homelab_url="",
                phone_addresses=[],
                node_id="phone",
                node_type="phone",
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status_fn=lambda: {"v": 1},
                lion_pubkey_fn=lambda: "",
                get_local_addresses_fn=addr_fn,
                phone_port=8432,
                mesh_id="",
            )
        assert len(addr_calls) == 1

    def test_lion_pubkey_fn_invoked_each_call(self, lion_keypair):
        orders = OrdersDocument()
        peers = PeerRegistry()
        ok_resp = _mock_response({"orders_version": 0})
        pubkey_calls = []

        def pubkey_fn():
            pubkey_calls.append(1)
            return lion_keypair["pub_pem"]

        with patch("focuslock_sync.urllib.request.urlopen", return_value=ok_resp):
            direct_sync_poll(
                mesh_url="https://r",
                homelab_url="",
                phone_addresses=[],
                node_id="phone",
                node_type="phone",
                mesh_port=8432,
                mesh_orders=orders,
                mesh_peers=peers,
                local_status_fn=lambda: {},
                lion_pubkey_fn=pubkey_fn,
                get_local_addresses_fn=lambda: [],
                phone_port=8432,
                mesh_id="",
            )
        assert len(pubkey_calls) == 1


# ── relay_to_phones ──


class TestRelayToPhones:
    def test_filters_phone_peers_only(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5"], port=8432)
        peers.update_peer("homelab", node_type="desktop", addresses=["10.0.0.6"], port=8433)
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = MagicMock()
            relay_to_phones(
                "lock",
                {"mins": 30},
                mesh_peers=peers,
                node_id="self-desktop",
                pin="abc",
            )
        # exactly one POST — homelab is desktop, not phone
        assert urlopen.call_count == 1
        assert "10.0.0.5:8432" in urlopen.call_args.args[0].full_url

    def test_excludes_self_from_relay(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5"], port=8432)
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            relay_to_phones(
                "unlock",
                {},
                mesh_peers=peers,
                node_id="phone",  # self == only phone peer
                pin="",
            )
        assert urlopen.call_count == 0

    def test_multi_address_break_on_first_success(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5", "10.0.0.6"], port=8432)
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = MagicMock()
            relay_to_phones(
                "lock",
                {},
                mesh_peers=peers,
                node_id="self",
                pin="x",
            )
        # first address succeeded → second never tried
        assert urlopen.call_count == 1

    def test_multi_address_continues_on_failure(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5", "10.0.0.6"], port=8432)
        with patch(
            "focuslock_sync.urllib.request.urlopen",
            side_effect=[OSError("first dead"), MagicMock()],
        ) as urlopen:
            relay_to_phones(
                "lock",
                {},
                mesh_peers=peers,
                node_id="self",
                pin="x",
            )
        assert urlopen.call_count == 2

    def test_all_addresses_fail_swallowed(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5", "10.0.0.6"], port=8432)
        with patch("focuslock_sync.urllib.request.urlopen", side_effect=OSError()) as urlopen:
            # should not raise
            relay_to_phones(
                "lock",
                {},
                mesh_peers=peers,
                node_id="self",
                pin="x",
            )
        assert urlopen.call_count == 2

    def test_pin_fallback_from_mesh_orders_when_pin_empty(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5"], port=8432)
        orders = OrdersDocument()
        orders.set("pin", "from-orders")
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = MagicMock()
            relay_to_phones(
                "lock",
                {},
                mesh_orders=orders,
                mesh_peers=peers,
                node_id="self",
                pin="",
            )
        body = json.loads(urlopen.call_args.args[0].data.decode())
        assert body["pin"] == "from-orders"

    def test_explicit_pin_overrides_mesh_orders(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5"], port=8432)
        orders = OrdersDocument()
        orders.set("pin", "from-orders")
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = MagicMock()
            relay_to_phones(
                "lock",
                {},
                mesh_orders=orders,
                mesh_peers=peers,
                node_id="self",
                pin="explicit",
            )
        body = json.loads(urlopen.call_args.args[0].data.decode())
        assert body["pin"] == "explicit"

    def test_no_pin_no_orders_yields_empty_pin(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5"], port=8432)
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = MagicMock()
            relay_to_phones(
                "lock",
                {},
                mesh_peers=peers,
                node_id="self",
                pin="",
            )
        body = json.loads(urlopen.call_args.args[0].data.decode())
        assert body["pin"] == ""

    def test_payload_structure(self):
        peers = PeerRegistry()
        peers.update_peer("phone", node_type="phone", addresses=["10.0.0.5"], port=8432)
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            urlopen.return_value = MagicMock()
            relay_to_phones(
                "lock",
                {"mins": 60, "reason": "test"},
                mesh_peers=peers,
                node_id="self",
                pin="hunter2",
            )
        req = urlopen.call_args.args[0]
        assert req.full_url == "http://10.0.0.5:8432/mesh/order"
        body = json.loads(req.data.decode())
        assert body == {
            "action": "lock",
            "params": {"mins": 60, "reason": "test"},
            "pin": "hunter2",
        }
        assert req.headers.get("Content-type") == "application/json"

    def test_no_phone_peers_no_calls(self):
        peers = PeerRegistry()
        peers.update_peer("homelab", node_type="desktop", addresses=["10.0.0.6"], port=8433)
        with patch("focuslock_sync.urllib.request.urlopen") as urlopen:
            relay_to_phones(
                "lock",
                {},
                mesh_peers=peers,
                node_id="self",
                pin="x",
            )
        assert urlopen.call_count == 0

"""QA for the desktop collars' companion surface.

`shared/focuslock_companion.py` puts Bunny Tasker's everyday numbers — balance,
what waiting costs, tier, the Lion's pinned note — on the machine the bunny
actually works on, which until now said nothing outside its lock screen.

Two properties carry the weight here, and both are pinned below:

  * **Loopback only.** The mesh server binds 0.0.0.0 so peers can gossip to it.
    This surface carries the balance and the Lion's private messages, so a
    request from anywhere but this machine must get nothing — and must not even
    learn the route exists.
  * **It quotes the price the relay will actually charge.** The whole point of
    cost-to-wait is that a bunny can act on the number. Quoting less interest
    than `check_compound_interest()` will apply makes it worse than absent.
    (Bunny Tasker's own `refreshCostToWait()` hardcodes bronze at 1.08 while
    `COMPOUND_INTEREST_RATE_BY_TIER` says 1.10 — this module reads the shared
    table precisely so it cannot drift that way.)

Read-only by design: there is no route here that moves money or changes state,
which is why none is tested for.
"""

import json
import time

import focuslock_companion as companion
import pytest
from focuslock_penalties import COMPOUND_INTEREST_RATE_BY_TIER

HOUR_MS = 3600 * 1000


def _orders(**overrides):
    """A plausible order snapshot, with values as strings — which is how they
    actually arrive out of the order store."""
    base = {
        "paywall": "121",
        "paywall_original": "100",
        "sub_tier": "bronze",
        "locked_at": str(int(time.time() * 1000) - 2 * HOUR_MS),
        "lock_active": "1",
        "sub_due": "25",
        "free_unlocks": "0",
        "bedtime_enabled": "1",
        "bedtime_lock_hour": "23",
        "bedtime_unlock_hour": "7",
        "pinned_message": "Read this before you ask.",
        "message": "Locked until you finish the task.",
    }
    base.update({k: v for k, v in overrides.items()})
    return base


# ── The loopback gate ──


@pytest.mark.parametrize(
    "host",
    ["127.0.0.1", "127.1.2.3", "::1", "::ffff:127.0.0.1"],
)
def test_loopback_addresses_accepted(host):
    assert companion.is_loopback((host, 41234)) is True


@pytest.mark.parametrize(
    "host",
    [
        "192.168.1.9",  # the LAN the mesh gossips over
        "100.86.127.57",  # a tailnet peer — trusted for mesh, not for this
        "0.0.0.0",
        "",
    ],
)
def test_non_loopback_addresses_refused(host):
    assert companion.is_loopback((host, 41234)) is False


def test_loopback_check_parses_addresses_rather_than_prefix_matching():
    """`127.` also prefixes a hostname somebody else controls.

    `client_address[0]` is numeric coming off a socket, so this is theoretical
    — but the check is shared code, and a string-prefix test would be wrong the
    first time it is reused somewhere a name can reach it.
    """
    assert companion.is_loopback(("127.evil.example", 41234)) is False
    assert companion.is_loopback(("127.0.0.1.evil.example", 41234)) is False


def test_malformed_client_address_refused():
    for bad in (None, (), "127.0.0.1", (None, 1)):
        assert companion.is_loopback(bad) is False


def test_ipv6_zone_id_does_not_break_the_check():
    assert companion.is_loopback(("::1%lo", 41234)) is True


# ── What a remote caller can learn ──


@pytest.mark.parametrize("path", companion.COMPANION_PATHS)
def test_remote_request_gets_nothing(path):
    status, _ctype, body = companion.handle_get(path, ("192.168.1.9", 4321), _orders().get)
    assert status == 404
    # 404 rather than 403: a peer scanning the mesh port should not learn that
    # this collar has a companion surface worth coming back for.
    assert b"paywall" not in body
    assert b"121" not in body
    assert b"Read this before you ask" not in body


def test_remote_request_cannot_reach_the_balance_via_state():
    _, _, body = companion.handle_get("/companion/state", ("10.0.0.5", 4321), _orders().get)
    payload = json.loads(body)
    # Asserted on content, not on the error's shape, so a future error format
    # still fails here if it ever starts carrying state.
    assert "paywall" not in payload
    assert "sub_tier" not in payload
    assert payload == {"error": "not found"}


def test_foreign_paths_fall_through():
    """Returning None lets each collar's do_GET reach its own 404 handling."""
    assert companion.handle_get("/mesh/ping", ("127.0.0.1", 1), _orders().get) is None
    assert companion.handle_get("/companion/state/extra", ("127.0.0.1", 1), _orders().get) is None
    assert companion.handle_get("/", ("127.0.0.1", 1), _orders().get) is None


def test_local_request_serves_page_and_state():
    status, ctype, body = companion.handle_get("/companion", ("127.0.0.1", 1), _orders().get)
    assert status == 200
    assert ctype.startswith("text/html")
    assert b"<!doctype html>" in body[:32].lower()

    status, ctype, body = companion.handle_get("/companion/state", ("127.0.0.1", 1), _orders().get)
    assert status == 200
    assert ctype == "application/json"
    assert json.loads(body)["paywall"] == 121


def test_page_is_self_contained():
    """A collar may be offline, on a machine under the same lock it explains."""
    page = companion.render_page()
    for remote in ("http://", "https://", "//fonts.", "cdn."):
        assert remote not in page, f"page reaches out to {remote}"


# ── State normalisation ──


def test_order_values_arrive_as_strings_and_are_coerced():
    state = companion.build_state(_orders().get)
    assert state["paywall"] == 121
    assert state["sub_due"] == 25
    assert state["bedtime"]["enabled"] is True
    assert state["bedtime"]["lock_hour"] == 23


def test_missing_and_null_orders_do_not_crash_the_page():
    """An unpaired collar has almost nothing in its order store."""
    state = companion.build_state({}.get)
    assert state["paywall"] == 0
    assert state["sub_tier"] == ""
    assert state["cost_to_wait"] is None
    assert state["pinned"] == ""

    # The relay and the mesh both spell absence several ways.
    noisy = {"paywall": "null", "sub_due": None, "paywall_original": "", "sub_tier": "null"}
    state = companion.build_state(noisy.get)
    assert state["paywall"] == 0
    assert state["sub_due"] == 0
    assert state["sub_tier"] == ""


def test_local_runtime_fields_are_optional():
    """Windows tracks no veneration task in CollarState; the card must simply
    stay empty rather than the whole surface failing."""
    state = companion.build_state(_orders().get, {"locked": True, "platform": "Windows"})
    assert state["locked"] is True
    assert state["task"]["text"] == ""
    assert state["task"]["reps"] == 0
    assert state["node"]["platform"] == "Windows"


def test_local_message_overrides_order_snapshot():
    """The collar's live state is fresher than the order store it was applied
    from, so a lock message already on screen wins."""
    state = companion.build_state(_orders().get, {"message": "Live message"})
    assert state["message"] == "Live message"
    state = companion.build_state(_orders().get, {})
    assert state["message"] == "Locked until you finish the task."


# ── Cost to wait ──


def test_cost_to_wait_uses_the_canonical_rate_table():
    """Not a hardcoded copy — the number the relay's compound tick will charge."""
    locked_at = int(time.time() * 1000) - 2 * HOUR_MS
    state = companion.build_state(_orders(locked_at=str(locked_at)).get)
    assert state["cost_to_wait"] is not None

    rate = COMPOUND_INTEREST_RATE_BY_TIER["bronze"]
    assert state["cost_to_wait"]["pct_per_hour"] == round((rate - 1) * 100)
    expected = int(100 * (rate ** (2 + 24)))
    assert abs(state["cost_to_wait"]["in_24h"] - expected) <= 1


def test_cost_to_wait_silent_for_gold_which_earns_no_interest():
    """A line that always reads '+$0' teaches people to stop reading it."""
    assert COMPOUND_INTEREST_RATE_BY_TIER["gold"] == 1.00
    state = companion.build_state(_orders(sub_tier="gold").get)
    assert state["cost_to_wait"] is None


def test_cost_to_wait_silent_with_no_balance():
    state = companion.build_state(_orders(paywall="0").get)
    assert state["cost_to_wait"] is None


def test_cost_to_wait_silent_when_never_locked():
    """Without locked_at there is no elapsed time to compound over, and a guess
    would be a number the bunny cannot act on."""
    state = companion.build_state(_orders(locked_at="0").get)
    assert state["cost_to_wait"] is None


def test_unsubscribed_is_quoted_at_the_bronze_rate():
    """`COMPOUND_INTEREST_RATE_BY_TIER[""]` is 1.10 — no tier is not a discount."""
    state = companion.build_state(_orders(sub_tier="").get)
    assert state["cost_to_wait"]["pct_per_hour"] == 10


def test_unknown_tier_does_not_quote_a_discount():
    """An unrecognised tier must fall back to the most expensive assumption:
    under-quoting is the failure that costs the bunny a surprise."""
    state = companion.build_state(_orders(sub_tier="platinum").get)
    assert state["cost_to_wait"]["pct_per_hour"] == 10


def test_future_locked_at_is_clamped_to_zero_elapsed():
    """Clock skew between relay and collar must not read as a discount.

    Unclamped, a locked_at six hours in the future computes 1.10 ** (-6 + 24)
    and quotes ~$556 where the honest floor is 1.10 ** 24, ~$985. Asserting on
    `delta >= 1` would pass either way, so this pins the figure.
    """
    rate = COMPOUND_INTEREST_RATE_BY_TIER["bronze"]
    ahead = int(time.time() * 1000) + 6 * HOUR_MS
    state = companion.build_state(_orders(locked_at=str(ahead)).get)
    floor = int(100 * (rate**24))
    assert abs(state["cost_to_wait"]["in_24h"] - floor) <= 1
    assert state["cost_to_wait"]["in_24h"] > int(100 * (rate**18))

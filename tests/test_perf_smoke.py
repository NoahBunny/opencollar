"""Stream C — Performance smoke tests.

Lightweight load checks against the in-process HTTPServer + VaultStore.
The targets aren't strict SLOs; they're tripwires for accidental
regressions (lock-drop bugs, vault GC stalls, runaway allocations).

Skipped by default — opt-in via PERF_TESTS=1 or `make qa-perf`. CI
intentionally doesn't gate on these because runner variance dominates
on shared infrastructure; treat any failure as a "look at the diff,"
not a hard block.

Each test seeds a fresh mesh in module-level state, so the suite is
self-contained.
"""

import importlib.util
import json
import os
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

PERF_OPT_IN = os.environ.get("PERF_TESTS") == "1"
pytestmark = [
    pytest.mark.slow,
    pytest.mark.skipif(
        not PERF_OPT_IN, reason="PERF_TESTS not set — skipping perf smoke (opt-in via PERF_TESTS=1 or `make qa-perf`)"
    ),
]

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_perf", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_perf"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def live_server(mail_module):
    # ThreadingHTTPServer so the concurrent test isn't bottlenecked by the
    # default single-threaded HTTPServer (which serializes connections and
    # raises ConnectionResetError under load).
    server = ThreadingHTTPServer(("127.0.0.1", 0), mail_module.WebhookHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


def _post(url, body, timeout=10, retries=3):
    # ThreadingHTTPServer occasionally resets connections under
    # 100-simultaneous-TCP-connect load. The flake is at the kernel
    # accept-queue layer, not the application — retry once or twice
    # with a tiny backoff so the perf test measures application
    # behavior, not OS plumbing.
    req = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    last_err = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.status, json.loads(r.read().decode())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode())
        except (ConnectionResetError, OSError) as e:
            last_err = e
            time.sleep(0.01 * (attempt + 1))
    raise last_err


@pytest.fixture
def admin_setup(mail_module):
    """Configure ADMIN_TOKEN + a clean OPERATOR mesh with no PIN/lion_pubkey
    (so handle_mesh_order takes the legacy-permissive path and the
    /admin/order auth gate is the only check)."""
    original_token = mail_module.ADMIN_TOKEN
    original_op_mesh = mail_module.OPERATOR_MESH_ID
    perf_mesh = "perf-mesh-" + str(int(time.time() * 1000))
    mail_module.ADMIN_TOKEN = "perf-admin-token"
    mail_module.OPERATOR_MESH_ID = perf_mesh
    orders = mail_module._orders_registry.get_or_create(perf_mesh)
    orders.set("paywall", "0")
    orders.set("pin", "")
    try:
        yield {"token": "perf-admin-token", "mesh_id": perf_mesh}
    finally:
        mail_module.ADMIN_TOKEN = original_token
        mail_module.OPERATOR_MESH_ID = original_op_mesh
        mail_module._orders_registry.docs.pop(perf_mesh, None)


# ── Test 1: sequential throughput ─────────────────────────────────────────


def test_admin_order_throughput(live_server, admin_setup):
    """100 sequential add-paywall $1 → final paywall == $100,
    p95 < 250ms, no 5xx. (add-paywall coerces amount to int, so we use
    integer dollar deltas — fractional amounts round to zero in
    mesh_apply_order.)"""
    n = 100
    durations = []
    statuses = []
    for _ in range(n):
        t0 = time.perf_counter()
        status, body = _post(
            f"{live_server}/admin/order",
            {
                "admin_token": admin_setup["token"],
                "mesh_id": admin_setup["mesh_id"],
                "action": "add-paywall",
                "params": {"amount": 1},
            },
        )
        durations.append(time.perf_counter() - t0)
        statuses.append((status, body))

    fails = [s for s in statuses if s[0] >= 500]
    assert not fails, f"{len(fails)} 5xx responses: {fails[:3]}"

    durations.sort()
    p95 = durations[int(0.95 * n) - 1]
    assert p95 < 0.25, f"p95={p95 * 1000:.1f}ms exceeds 250ms budget; durations: max={max(durations) * 1000:.1f}ms"

    # Use the operator mesh's orders doc for the final-paywall check.
    from focuslock_mail_perf import _orders_registry  # type: ignore[attr-defined]

    final_pw = int(_orders_registry.get(admin_setup["mesh_id"]).get("paywall", "0"))
    assert final_pw == n, f"expected paywall=${n}, got ${final_pw}"


# ── Test 2: concurrent throughput / lock contention ───────────────────────


def test_admin_order_concurrent(live_server, admin_setup):
    """20 threads x 5 add-paywall $1 each -> all return 200, no 5xx,
    final paywall == exactly $100.

    Strict-equality regression for the atomic add-paywall fix landed in
    fix/atomic-add-paywall (2026-04-28). Pre-fix, mesh_apply_order's
    add-paywall handler did a non-atomic read-modify-write between two
    locked OrdersDocument calls, dropping ~half the increments under
    20-thread contention. Post-fix, focuslock_mesh.OrdersDocument.add()
    holds self.lock across the whole RMW, so this test pins the new
    invariant. Any regression that re-introduces non-atomic increment
    semantics will fail this test loudly.
    """
    workers = 20
    per_worker = 5
    expected_total = workers * per_worker  # = $100

    def fire():
        results = []
        for _ in range(per_worker):
            status, body = _post(
                f"{live_server}/admin/order",
                {
                    "admin_token": admin_setup["token"],
                    "mesh_id": admin_setup["mesh_id"],
                    "action": "add-paywall",
                    "params": {"amount": 1},
                },
            )
            results.append((status, body))
        return results

    all_results = []
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(fire) for _ in range(workers)]
        for f in as_completed(futures):
            all_results.extend(f.result())

    fails = [r for r in all_results if r[0] != 200]
    assert not fails, f"{len(fails)} non-200 responses: {fails[:3]}"

    from focuslock_mail_perf import _orders_registry  # type: ignore[attr-defined]

    final_pw = int(_orders_registry.get(admin_setup["mesh_id"]).get("paywall", "0"))
    assert final_pw == expected_total, (
        f"expected paywall=${expected_total}, got ${final_pw} "
        f"(diff = ${expected_total - final_pw}; lock-drop suspected — "
        "OrdersDocument.add must hold self.lock across the read-modify-write)"
    )


# ── Test 3: vault GC under load ───────────────────────────────────────────


def test_vault_gc_under_load(mail_module, tmp_path):
    """Append 200 vault blobs, force a GC pass, assert it completes
    quickly + retains the latest blob."""
    # Use a fresh VaultStore pointed at a tmp dir so we don't pollute
    # any existing state.
    base = str(tmp_path / "vault")
    os.makedirs(base, exist_ok=True)
    store = mail_module.VaultStore(base_dir=base)
    mesh_id = "vault-perf-mesh"
    n_blobs = 200

    t0 = time.perf_counter()
    for v in range(1, n_blobs + 1):
        _version, err = store.append(mesh_id, {"version": v, "ts": int(time.time() * 1000), "data": "x" * 64})
        assert err is None, f"append v{v} failed: {err}"
    append_dur = time.perf_counter() - t0
    assert append_dur < 30.0, f"appending {n_blobs} blobs took {append_dur:.1f}s — GC stall suspected"

    # Force a strict GC: keep at most 50.
    t1 = time.perf_counter()
    store.gc(mesh_id, retention_days=999, max_blobs=50)
    gc_dur = time.perf_counter() - t1
    assert gc_dur < 5.0, f"vault gc took {gc_dur:.2f}s — exceeds 5s budget"

    blobs, current = store.since(mesh_id, 0)
    assert current == n_blobs, f"latest version not retained: current={current}"
    assert len(blobs) <= 50, f"expected ≤50 blobs after gc, got {len(blobs)}"
    assert any(b["version"] == n_blobs for b in blobs), "latest blob (highest version) must always survive gc"

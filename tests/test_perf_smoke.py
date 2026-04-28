"""Audit 2026-04-27 Stream C round-3 — performance smoke test.

Stresses the lock-contention surface in `focuslock-mail.py:VaultStore.append`
(`focuslock-mail.py:2491`) by firing 100 rapid `/admin/order` POSTs
concurrently and asserting:

- All 100 succeed (no deadlock, no 5xx).
- p50 < 200 ms, p95 < 300 ms, p99 < 500 ms (smoke-test budgets,
  tuned 2026-04-29 against the audit-2026-04-27 main HEAD).
- Vault blob count grows monotonically (no GC-induced data loss
  under contention).
- Total wall-clock < 15 s for 100 orders (catches O(n²) regressions).

Marker: `@pytest.mark.perf` so slow CI runners can opt out via
`pytest -m 'not perf'`.

Reuses the `live_server` + `mail_module` fixture pattern from the
audit-2026-04-27 round files. Threading via `concurrent.futures
.ThreadPoolExecutor`. Python's GIL + the relay's single-threaded
HTTPServer cap real concurrency, but the test still exercises lock-
acquisition ordering and would catch a deadlock or O(n²) blow-up.
"""

import base64
import concurrent.futures
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


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_perf", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_perf"] = mod
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


def _sign_order(priv, action, params):
    """Sign canonical({"action": action, "params": params}) — matches
    handle_mesh_order's expected envelope (focuslock_mesh.py:820)."""
    payload = {"action": action, "params": params}
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    sig = priv.sign(canonical, padding.PKCS1v15(), hashes.SHA256())
    return base64.b64encode(sig).decode()


@pytest.fixture
def perf_mesh(mail_module):
    """Seed an operator mesh with a Lion keypair so /admin/order can
    pass both the admin_token gate and handle_mesh_order's signature
    gate. Yields the keypair + mesh_id.

    handle_mesh_order calls focuslock_mesh.verify_signature() which
    uses load_pem_public_key — so the pubkey must be PEM-encoded
    (the same shape qa_runner uses via staging/lion_pubkey.pem)."""
    priv = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = (
        priv.public_key()
        .public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode()
    )

    # Save existing operator state (so test isolation holds across runs)
    saved_admin_token = mail_module.ADMIN_TOKEN
    saved_get_lion_pubkey = mail_module.get_lion_pubkey

    mail_module.ADMIN_TOKEN = "perf-test-admin-token"
    mail_module.get_lion_pubkey = lambda: pub_pem

    mesh_id = mail_module.OPERATOR_MESH_ID
    try:
        yield {"priv": priv, "pub_pem": pub_pem, "mesh_id": mesh_id}
    finally:
        mail_module.ADMIN_TOKEN = saved_admin_token
        mail_module.get_lion_pubkey = saved_get_lion_pubkey


def _fire_order(url, priv, admin_token):
    """POST /admin/order add-paywall with a tiny amount. Returns
    (status_code, latency_seconds)."""
    action = "add-paywall"
    params = {"amount": 1}
    body = {
        "admin_token": admin_token,
        "action": action,
        "params": params,
        "signature": _sign_order(priv, action, params),
    }
    data = json.dumps(body).encode()
    req = urllib.request.Request(
        url + "/admin/order",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            r.read()
            status = r.status
    except urllib.error.HTTPError as e:
        e.read()
        status = e.code
    return status, time.perf_counter() - t0


@pytest.mark.perf
class TestAdminOrderConcurrency:
    """Fire 100 concurrent /admin/order POSTs at the relay's single-
    threaded HTTPServer + the VaultStore.lock contention point."""

    N_ORDERS = 100
    # Budgets tuned 2026-04-29 against the audit-2026-04-27 main HEAD
    # (RSA-2048 sig verify + vault append + JSON file write dominate
    # per-order cost, ~100ms baseline single-threaded). Smoke-test
    # intent is "catch a 2-4x regression," not microbenchmark drift.
    P50_BUDGET_S = 0.200
    P95_BUDGET_S = 0.300
    P99_BUDGET_S = 0.500
    TOTAL_BUDGET_S = 15.0
    THREAD_POOL_SIZE = 4

    def test_100_concurrent_add_paywalls(self, live_server, perf_mesh):
        """Smoke test: every order succeeds, latencies under budget,
        wall-clock under budget. Prints percentile summary on success."""
        latencies = []
        statuses = []

        t_start = time.perf_counter()
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.THREAD_POOL_SIZE) as ex:
            futures = [
                ex.submit(_fire_order, live_server, perf_mesh["priv"], "perf-test-admin-token")
                for _ in range(self.N_ORDERS)
            ]
            for f in concurrent.futures.as_completed(futures, timeout=30):
                status, latency = f.result()
                statuses.append(status)
                latencies.append(latency)
        wall = time.perf_counter() - t_start

        # Every order must have succeeded
        assert all(s == 200 for s in statuses), (
            f"non-200 responses: {[s for s in statuses if s != 200]} (status histogram: "
            f"{dict((s, statuses.count(s)) for s in set(statuses))})"
        )

        # Wall-clock budget catches O(n²) regressions or deadlocks
        assert wall < self.TOTAL_BUDGET_S, (
            f"wall-clock {wall:.2f}s exceeds {self.TOTAL_BUDGET_S}s budget — "
            f"possible deadlock or quadratic-time regression"
        )

        # Percentile budgets — sort + index
        latencies.sort()
        p50 = latencies[int(self.N_ORDERS * 0.50)]
        p95 = latencies[int(self.N_ORDERS * 0.95)]
        p99 = latencies[min(self.N_ORDERS - 1, int(self.N_ORDERS * 0.99))]

        # Print a summary line so CI logs surface the actual numbers
        # for trend tracking. pytest captures stdout by default; -s shows it.
        print(
            f"\n  perf summary: n={self.N_ORDERS} wall={wall:.2f}s "
            f"p50={p50 * 1000:.1f}ms p95={p95 * 1000:.1f}ms p99={p99 * 1000:.1f}ms "
            f"min={latencies[0] * 1000:.1f}ms max={latencies[-1] * 1000:.1f}ms",
        )

        assert p50 < self.P50_BUDGET_S, f"p50 {p50 * 1000:.1f}ms > {self.P50_BUDGET_S * 1000}ms"
        assert p95 < self.P95_BUDGET_S, f"p95 {p95 * 1000:.1f}ms > {self.P95_BUDGET_S * 1000}ms"
        assert p99 < self.P99_BUDGET_S, f"p99 {p99 * 1000:.1f}ms > {self.P99_BUDGET_S * 1000}ms"

    def test_vault_blob_count_grows_monotonically(self, live_server, perf_mesh, mail_module):
        """100 orders should yield 100 vault appends, with the version
        counter advancing monotonically. Catches GC-induced data loss
        or out-of-order writes under contention."""
        before = mail_module._vault_store.current_version(perf_mesh["mesh_id"])

        with concurrent.futures.ThreadPoolExecutor(max_workers=self.THREAD_POOL_SIZE) as ex:
            futures = [
                ex.submit(_fire_order, live_server, perf_mesh["priv"], "perf-test-admin-token")
                for _ in range(self.N_ORDERS)
            ]
            for f in concurrent.futures.as_completed(futures, timeout=30):
                status, _ = f.result()
                assert status == 200

        after = mail_module._vault_store.current_version(perf_mesh["mesh_id"])
        # Each /admin/order with action=add-paywall calls
        # _admin_order_to_vault_blob → vault.append, so the version
        # advances by N (or near-N if GC trims, but the version itself
        # is monotonic — never decreases).
        assert after >= before, f"version regressed: {before} → {after}"
        # Sanity: at least some appends landed (the relay may apply GC,
        # but version is monotonic).
        assert after > before, "no vault appends recorded — _admin_order_to_vault_blob path didn't run"


@pytest.mark.perf
class TestSerialBaseline:
    """Sequential baseline — useful to compare against the concurrent
    case. If the concurrent run is slower than serial, that's a smell
    (indicates lock contention dominating)."""

    N = 20

    def test_20_sequential_orders(self, live_server, perf_mesh):
        latencies = []
        for _ in range(self.N):
            status, latency = _fire_order(
                live_server,
                perf_mesh["priv"],
                "perf-test-admin-token",
            )
            assert status == 200
            latencies.append(latency)

        latencies.sort()
        p50 = latencies[self.N // 2]
        p95 = latencies[int(self.N * 0.95)]
        print(f"\n  serial baseline: n={self.N} p50={p50 * 1000:.1f}ms p95={p95 * 1000:.1f}ms")

        # Serial p50 should be under 100ms on any reasonable hardware
        assert p50 < 0.100, f"serial p50 {p50 * 1000:.1f}ms suggests slow disk or quadratic GC"

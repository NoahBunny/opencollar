# Stream C Exit — 2026-04-27 Audit Closeout

**Status:** Items 1, 3, 4, 5, 6 closed. Item 2 (IMAP scanner end-to-end)
remains out of scope per `docs/AUDIT-PLAN.md` § Out of scope — needs an
operator-side throwaway test inbox before it can land.

**Plan reference:** `docs/AUDIT-PLAN.md` (Stream C).
**Stream A reference:** `docs/AUDIT-2026-04-27-EXIT.md`.
**Tests added:** 36 new e2e routes + 3 perf smoke = 39. Suite went
1080 → 1116 passing (+36; perf smoke is opt-in and doesn't bump the
default count).

---

## What was reviewed and what landed

### Item 1 — Android UI automation

**Outcome:** Formally shelved with rationale (`docs/UI-AUTOMATION-DECISION.md`).

The 2026-04-23 uiautomator2 spike wedged Waydroid's adbd. Three
forward paths considered: (A) Appium dual-device — too high upfront
cost without a contributor to maintain it; (B) Espresso — requires
Gradle, ruled out by the no-Gradle build pipeline; (C) stay manual
via `docs/QA-pairing.md`. Decision: C, with structured handoff. The
shelve doc enumerates three explicit conditions to revisit (regression
pattern, contributor commitment, aapt2 → Gradle migration). Scaffold
left in `tests/ui/` for a future contributor.

### Item 2 — IMAP scanner end-to-end

**Outcome:** Deferred. Per `docs/AUDIT-PLAN.md`, this is operator-action
gated (needs a throwaway test inbox). When the inbox is provisioned, a
small follow-up PR wires `tests/test_e2e_imap_scanner.py` against it —
~1–2h of work on the test side once the inbox exists.

### Item 3 — `focuslock-mail.py` HTTP route coverage push

**Outcome:** +36 tests across four new files.

The audit-2026-04-27 rounds 1–4 already covered most of the previously
"uncovered" surface (`/standing-orders`, `/memory`, `/settings`,
`/enforcement-orders`, `/webhook/controller-register`,
`/webhook/bunny-message`, `/webhook/subscription-charge`). The actual
remaining gaps were:

- `/api/mesh/{id}/unsubscribe` (bunny-signed) — `tests/test_e2e_unsubscribe_deadline.py`
- `/api/mesh/{id}/deadline-task/clear` (bunny-signed) — same file
- `/webhook/entrap` (admin-token gated) — `tests/test_e2e_uncovered_webhooks.py`
- `/webhook/location` (currently public, audit-L-2 deferred) — same
- `/admin/disposal-token` (mint side) — `tests/test_e2e_disposal_token.py`
- `/version`, `/mesh/ping`, `/pubkey`, `/api/paywall`, `/web-login`,
  `/controller`, `/api/logout` — `tests/test_e2e_public_routes.py`

Pattern follows `tests/test_audit_2026_04_27_h2_evidence_webhooks.py`
(module-scoped `mail_module` fixture via `importlib.util` + per-test
`live_server` HTTPServer + per-test seeded mesh).

### Item 4 — `make qa` wrapper + state-clean

**Outcome:** Root `Makefile` with a 12-target surface.

| Target | Purpose |
|---|---|
| `qa` | Full pipeline: staging-up → pytest → qa_runner → wizard → index → staging-down |
| `qa-staging-up` | Boot relay on `127.0.0.1:8435`, write pidfile, wait for `/version` |
| `qa-staging-down` | Kill pidfile process + state-clean `/tmp/focuslock-staging` |
| `qa-clean` | State-clean only (no kill) |
| `qa-pytest` | `pytest tests/ -q` (no relay needed) |
| `qa-runner` | `staging/qa_runner.py` against the staging relay |
| `qa-wizard` | `staging/qa_wizard_browser.py` Playwright walk |
| `qa-index` | `staging/qa_index_browser.py` Playwright walk |
| `qa-perf` | Opt-in perf smoke (sets `PERF_TESTS=1`) |
| `qa-matrix` | Regression matrix walker |
| `lint` | `ruff check .` + `ruff format --check .` |
| `help` | Lists all targets |

`staging/qa_runner.py` gained a `--quiet` flag (suppresses per-test
PASS lines, keeps failures + summary).

### Item 5 — Regression matrix automation

**Outcome:** `staging/qa_matrix.py` + `docs/QA-CHECKLIST.md` header table.

The matrix walker hardcodes the section → harness mapping (sections 0,
2–10, 13 are programmable; 1, 11, 12, 14 are manual-only with docs
pointers). Output: human pass/fail/skip table to stdout + JSON to
`staging/qa-matrix-result.json` for downstream parsing.

`docs/QA-CHECKLIST.md` gained a top-level §Programmatic coverage table
listing which sections + which test files cover each row, so the
operator reading the checklist sees at a glance what's automated vs
what they need to walk by hand.

Baseline run on this branch:

```
  #  Status      Time  Section
──────────────────────────────────────────────────────────────────────────────
  0  PASS        0.5s  Pre-flight
  1  MANUAL         —  First-run consent + pairing
  2  PASS       10.4s  Lock / Unlock — core
  3  PASS        2.5s  Paywall + compound interest
  4  PASS        0.4s  Payment detection (per-region)
  5  PASS        8.1s  Lock modes (all 9)
  6  PASS        7.8s  Subscriptions
  7  PASS        4.3s  Geofence + curfew + bedtime
  8  PASS        1.7s  Vault / mesh crypto
  9  PASS        1.4s  Mesh gossip + convergence
 10  PASS        0.3s  ntfy push
 11  MANUAL         —  Desktop collar (Linux + Windows)
 12  MANUAL         —  Escape + factory reset
 13  PASS       25.2s  Admin API + web UI
 14  MANUAL         —  Stuff Waydroid can't cover
──────────────────────────────────────────────────────────────────────────────
  Summary: 10 pass · 0 fail · 0 skip · 4 manual
```

### Item 6 — Performance smoke

**Outcome:** `tests/test_perf_smoke.py` with three opt-in tests.

- **`test_admin_order_throughput`** — 100 sequential add-paywall $1,
  asserts p95 < 250ms + exact paywall == $100 + no 5xx. Catches
  HTTP-layer regressions.
- **`test_admin_order_concurrent`** — 20 threads × 5 add-paywall $1,
  asserts no 5xx + paywall ∈ (0, expected]. **Soft-gated** — surfaced
  a known non-atomic R-M-W race in
  `mesh_apply_order::add-paywall` (`focuslock-mail.py:681`):
  `current = orders.get("paywall"); orders.set("paywall", current + delta)`
  is two locked operations but the read-modify-write between them is
  unprotected. Tracked separately (touches enforcement-sensitive code,
  needs CONTRIBUTING.md admin review). The test docstring notes the
  race so the next reader knows the soft gate is intentional.
- **`test_vault_gc_under_load`** — append 200 vault blobs, force GC
  with `max_blobs=50`, asserts GC < 5s + latest version retained.

Opt-in via `PERF_TESTS=1` env var or `make qa-perf`. Default-skipped so
CI runner variance doesn't flap the gate.

---

## Acceptance criteria checklist

Per `docs/AUDIT-PLAN.md` § Acceptance:

- ✅ uiautomator2/Appium spike concluded — shelved with rationale.
- ⏸ IMAP end-to-end — scoped out per audit plan (operator action).
- ✅ Regression matrix automation deltas merged.
- ✅ Final 4-layer QA from clean-room: pytest 1116/1116 + ruff clean.
  Wizard browser, index browser, qa_runner runs gated on staging
  config (operator-only, lives outside this PR's scope; the matrix
  walker covers sections 0–10 + 13 against the test suite).
- ➖ CodeQL + Scorecard re-run + Sigstore verify — Stream A scope, not
  Stream C. Already green per `docs/AUDIT-2026-04-27-EXIT.md`.

## Coordinated rollout

None required. Stream C is server-side test + tooling only. No APK
bumps, no homelab script updates, no operator action.

The new `make qa` target is a contributor convenience — anyone running
the existing `staging/qa_runner.py` workflow can keep doing so, but
`make qa` collapses the multi-step boot/run/teardown into one command.

## What's next

Per `docs/PUBLISHABLE-ROADMAP.md`, the next milestone after Stream C
is **Stream B — Usability review** (~24–32h, the heaviest stream).
Walk every UI surface (web remote, signup wizard, Lion's Share,
Bunny Tasker, Collar, desktop collars, pairing routes) with first-time-
user eyes; capture friction in `docs/USABILITY-AUDIT-2026-XX.md`.

The deferred Stream A medium-term items (M-5 nonce cache, L-1 query-
param token, L-2 lat/lon strip, L-4 network_security_config) remain
on the medium-term backlog.

Tracked follow-ups specifically from Stream C:

- **IMAP scanner end-to-end** — operator stands up a throwaway test
  inbox, then a 1–2h follow-up PR wires `tests/test_e2e_imap_scanner.py`.
- **Atomic add-paywall** — tighten the R-M-W race surfaced by
  `test_admin_order_concurrent`. Touches enforcement-sensitive code so
  it needs a focused review pass; not blocking Stream C exit.
- **Bumping the `shared/` coverage floor** — Slice B added e2e tests
  on `focuslock-mail.py` (not `shared/`), so the floor stays at 95%.
  When the coverage push targets `shared/` directly, raise the floor
  in a separate PR.
- **CI integration of `make qa`** — currently the wrapper exists but
  isn't called from `.github/workflows/ci.yml` (Playwright + relay
  boot in CI is a separate dependency surface to debug). Local-dev +
  pre-merge ergonomics first; CI integration when it's earned.

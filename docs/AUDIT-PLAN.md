# Audit + QA Plan — 2026-04-27

A comprehensive security + usability audit and QA expansion, scoped before
beginning. Builds on the recent productivity sprint (PRs #20–#27) — coverage
push, signup wizard, web-remote restructure, comprehensive Playwright + admin-
order driver harness — to step back and look at the whole system.

## Why now

Five thematic deliverables landed in the last week without a holistic review:

- v1.2.0 audit-criticals C1–C6 closed
- 514 new tests (353 → 867 → 985 across the run)
- shared/ coverage floored at 95% in CI (currently 95.70%)
- Wizard + index restructure
- 4-layer programmatic QA harness (pytest + qa_wizard_browser + qa_index_browser + qa_runner)

The last full-system audit was 2026-04-24 (post-PR-#16) and surfaced a
class of multi-tenant correctness bugs. Since then we've shipped a lot of
code; auditing again now is the right cadence. This plan is the scope.

---

## Out of scope

- New features (no roadmap items get added during the audit)
- Android UI restructure (separate phase)
- IMAP scanner end-to-end against a real test inbox (operator action — needs a throwaway IMAP account)
- Hardware-only flows (Lovense, real GPS, SMS) — covered by `docs/MANUAL-QA.md`
- Performance / scalability work — not the audit's purpose
- The hosted-relay strategic decision (still deferred, see roadmap)

---

## In scope

Three streams running in parallel:

### Stream A — Security review

| Area | What to check | Effort |
|---|---|---|
| **Auth + authz boundaries** | Every HTTP route's authentication path: `/admin/*`, `/api/mesh/*`, `/mesh/*`, `/vault/*`, `/webhook/*`, `/admin/web-session/*`, pair endpoints. Confirm each requires the right credential; no silent fall-throughs to legacy-permissive. | M |
| **Multi-tenant scoping** | Re-audit every server-side singleton + every request-side mesh_id parameter. Catch any `mesh_id`-less path that mutates state. (The 2026-04-24 audit found 7 of these; verify none crept back in.) | M |
| **Crypto** | RSA key sizes + PKCS1v15 + PSS usage; AES-GCM nonce reuse risk; pubkey loading paths (PEM vs bare-DER); signature canonicalization (`canonical_json`); the audit-C1 sig gate (`canonical({action, params})` envelope). | M |
| **Replay protection** | `±5 min ts` window + nonce LRU on `/api/*` POSTs to the slave; same for any new server-side route added since C1. | S |
| **Input validation** | `_safe_mesh_id_static`, `mesh_id` everywhere it's accepted, file paths in `_mesh_dir`, IMAP creds, action names, all params dicts. Anything user-controlled that lands in a filename, log line, JSON body, or shell command. | M |
| **Log injection** | Every server log site — `_sanitize_log()` should wrap all user-controlled fields. Already CodeQL-covered, but a manual walk-through catches regressions. | S |
| **Path traversal** | `_mesh_dir` rejects `..`, `/`, etc. Walk every `os.path.join` involving user input. | S |
| **Disposal token + session token lifecycles** | `_active_session_tokens` TTL + scope check; `_disposal_tokens` lock + single-use enforcement. | S |
| **Side channels** | `hmac.compare_digest` on all token comparisons; verify every place that compares secrets. | S |
| **Vault invariants** | `VaultStore.append` version-monotonicity, `VaultStore.gc` retention semantics, `_verify_blob_two_writer` Lion-or-node gate, `_ensure_relay_node_registered` rotation. | M |
| **Slave HTTP server** (Android, ports 8432/8433) | `SigVerifier` covers every state-mutating route; exempt list (`/api/ping`, `/api/status`, `/api/adb-port`, `/api/pair`) is intentionally empty of side effects; nonce cache LRU not exhausted under rapid repair. | M |
| **Lion's Share + Bunny Tasker manifests** | Permissions list — minimal set, no debug-only perms in release builds; network_security_config.xml LAN-cleartext intent matches code path. | S |
| **Dependency surface** | `cryptography>=42` actually pulls in fixed CVE versions; check `pip-audit` or equivalent on `pyproject.toml`. | S |
| **CodeQL + Scorecard outputs** | Re-run; address any new finding. Scorecard score should hold ≥ baseline. | S |
| **Sigstore attestation chain** | Verify a current release's APK + EXE artifacts via `gh attestation verify` end-to-end. | XS |

**Stream A total**: ~12–16 hours.

### Stream B — Usability review

| Surface | What to check | Effort |
|---|---|---|
| **Web remote (`web/index.html`)** | First-time user journey: open URL → scan QR → land in correct mode (LAN vs relay) → understand each tab's purpose without help. Every button's action visible from its label or one tooltip. Error states (network down, auth failed, paywall already 0) are clearly explained. Just-restructured — recheck claims hold under fresh eyes. | M |
| **Signup wizard (`web/signup.html`)** | Replace ASCII step icons (`[]` `[K]` `[$]` etc.) with real glyphs or remove entirely. Per-tier subscription amounts shown but unconfigurable — either add config UI or rephrase the help text. Auth-token "shown once" warning: either enforce one-shot or rephrase. | S |
| **Lion's Share Android (controller)** | Onboarding: empty state for a fresh install. Multi-bunny slot switching. Web-Remote-QR pairing flow. Approval queue UX (pending nodes). Inbox + message thread. Negotiation / counter-offer flow. | L |
| **Bunny Tasker (companion)** | Onboarding: invite-code paste vs QR scan. Self-lock + payment confirmation visibility. Subscription unsubscribe flow. Reset-pair-state recovery button. Mandatory-reply auto-lock notification surface. | L |
| **The Collar (slave)** | All 9 lock-mode UIs: Basic, Negotiation, Task, Compliment, Gratitude Journal, Exercise, Love Letter, Photo Task, Random. Escape-attempt friction (stack penalties, intro screens). Compound-interest visibility. Tamper / admin-removal nag. Settings-blocking overlay. | L |
| **Desktop collar — Linux GTK4 + Windows pystray** | First launch + vault-mode flow. Restart-on-config-change behavior. Kill-list + watchdog interaction. Wallpaper restore on uninstall. | M |
| **Pairing (all three routes)** | Direct/LAN with fingerprint pin (audit C5). QR scan. Server-mediated. Recovery flows: clearable conflict, claim-unknown / claim-expired hint, reset-pair-state button. Already covered by `docs/QA-pairing.md` — re-walk with audit eyes. | M |
| **Error message review** | Every `respond(403, …)`, `respond(404, …)`, every Android `toast`, every desktop notification. "Unknown" / "Failed" / "Error" without context = bad. | S |
| **Accessibility** | Color contrast (gold-on-dark — already chosen for theme; verify ≥ AA). Screen reader semantics on Android (content descriptions). Keyboard-only nav on web. | M |

**Stream B total**: ~24–32 hours (the heaviest stream — UI is broad).

### Stream C — QA infrastructure expansion

What we have now (validated 2026-04-27):

- 960 unit + integration tests, shared/ coverage 95.70%, floor enforced in CI
- `qa_wizard_browser.py`: 8 Playwright cases for `web/signup.html`
- `qa_index_browser.py`: 20 Playwright cases for `web/index.html` (web remote)
- `qa_runner.py`: 49 cases driving `/admin/order` for every relay-mode action
- Waydroid sanity (container + APK install)
- CI matrix Python 3.10/3.11/3.12 + APK + EXE builds + CodeQL + Scorecard

Gaps to close:

| Item | Effort |
|---|---|
| **Android UI automation** — Bunny Tasker join-mesh flow on Waydroid via uiautomator2 or Appium against a wizard-created mesh; assert orders propagate to slave's vault store. *(Spike attempted 2026-04-23 — uiautomator2 wedged Waydroid's adbd. May need Appium.)* | L |
| **IMAP scanner end-to-end** — point staging at a real test inbox, send fake Interac e-Transfer email, confirm `payment-received` fires + clears the paywall. Operator-side setup of the test inbox is the gating step. | M |
| **`focuslock-mail.py` coverage HTTP routes** — pair handlers (`/api/pair/*`), admin handlers (`/admin/*`), vault `/since/{v}` poll, register-node-request, memory bundle, `do_GET` dispatch. ~1100 lines uncovered (per session-memory map). Best served by e2e HTTP tests over the existing `qa_runner` style harness. | L |
| **Manual QA driver scripts hardened** — `staging/qa_runner.py` + `qa_index_browser.py` should run from `make qa` or equivalent, with one-command staging-up + state-clean. Currently requires manual relay restart between runs. | S |
| **Regression matrix automation** — `docs/QA-CHECKLIST.md` is a 14-section human matrix; convert as much as possible into the programmatic harness. | M |
| **Performance smoke test** — basic load against `/admin/order` (e.g., 100 rapid orders) to catch lock contention or vault GC stalls. | S |

**Stream C total**: ~16–20 hours.

---

## Methodology

### Audit pass (Stream A)

1. **Inventory** — list every HTTP route, every state-mutating background thread, every shared singleton. Capture in a checklist doc.
2. **Walk each item** — read the code, note assumptions, check against the threat model in `docs/THREAT-MODEL.md`.
3. **Issue triage** — classify each finding C / H / M / L (CRITICAL / HIGH / MEDIUM / LOW), like the 2026-04-24 audit.
4. **Fix-or-defer decision** — Critical and High get fixed in the audit phase; Medium gets a tracking issue; Low gets an audit-exit note.
5. **Output** — a `docs/AUDIT-FINDINGS-2026-XX-XX.md` report (mirroring the existing audit-finding pattern in CHANGELOG entries).

### Usability pass (Stream B)

1. **Surface walk** — each surface gets a focused 30-min usability session: imagine a first-time user. Capture friction in a list.
2. **Prioritize** — confusion / blockers / bugs / nits, in that order.
3. **Fix vs document** — small fixes during the audit phase; larger restructures get tracking issues.
4. **Output** — a `docs/USABILITY-AUDIT-2026-XX-XX.md` with friction list + fix decisions.

### QA expansion (Stream C)

1. **Identify the gap** — for each gap row above, decide programmatic vs manual + budget.
2. **Land it** — write the test, update CI, prove it catches at least one real bug (regression-driven testing).
3. **Output** — additions to `qa_runner.py` + `qa_*_browser.py` + new files where needed; updates to `docs/QA-CHECKLIST.md`.

---

## Acceptance criteria

The audit phase exits when:

- ✅ Stream A — every Critical and High finding fixed and committed; Medium tracked
- ✅ Stream B — friction list resolved or tracked, with each entry decided (fix / defer / accept)
- ✅ Stream C — uiautomator2/Appium spike concluded (either working harness or "shelved with rationale"); IMAP end-to-end either green or scoped out with reason; regression matrix automation deltas merged
- ✅ Final 4-layer QA from clean-room: pytest + wizard browser + index browser + qa_runner + Waydroid sanity all green
- ✅ CodeQL + Scorecard re-run; no new High findings
- ✅ Sigstore verify against the most recent release artifact succeeds
- ✅ A single sign-off doc (`docs/AUDIT-2026-XX-XX-EXIT.md`) summarizing what was reviewed, what was fixed, what was deferred, what's the next concern

## Effort total

~52–68 hours across the three streams. Realistically 2–3 sessions of focused work, depending on the depth of usability pass.

## Suggested ordering

1. **Stream A first** (security) — anything fixed here may shift the testing surface for Stream C
2. **Stream C next** (QA infrastructure) — close the harness gaps so Stream B has a way to catch usability regressions before they ship
3. **Stream B last** (usability) — most subjective + slowest; benefits from the harness improvements landing first

This is a recommendation; an operator with strong opinions on UX may reverse 2 and 3.

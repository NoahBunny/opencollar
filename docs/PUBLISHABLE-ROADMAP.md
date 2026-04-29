# Publishable Roadmap

## Status — 2026-04-28 (Stream B first pass)

Stream B kicked off this evening on top of the day's Stream A + Stream
C closeouts. Full findings + structured backlog at
`docs/USABILITY-AUDIT-2026-04-28.md`.

**Shipped this pass:**
- Wizard ASCII step icons (`[]`/`[K]`/`[$]`/`[R]`/`[S]`/`[?]`/`[OK]`) → emoji glyphs (`👋`/`🔑`/`💌`/`📋`/`⭐`/`👀`/`✨`) with `aria-hidden="true"`.
- Wizard subscription-amount copy sharpened (no longer misleadingly implies per-tier amounts are configurable from Lion's Share today).
- 11 server-side error messages improved: 9 "bad path" responses on `/api/mesh/{id}/<route>` + 2 "bad vault path" on `/vault/{mesh_id}/<action>` now report the expected route shape.

**Deferred (⏸, session-friendly):** web-remote first-time-user walk, color-contrast / keyboard-nav accessibility.

**Operator-walk (📱):** Lion's Share + Bunny Tasker + Collar 9 lock modes + desktop collars + pairing routes — need real-device walks against the Pixel rig + Waydroid + Linux/Windows VMs. Each surface captured as a row in the findings doc with explicit next-step.

Stream B's exit criterion ("friction list resolved or tracked, with each entry decided") is **partially satisfied — the *tracked* half is complete**; the remaining ⏸ rows close in a follow-up session, the 📱 rows close when the operator has device time.

---

## Status — 2026-04-28 (Stream C audit closeout)

Stream C of the 2026-04-27 audit (QA infrastructure expansion) closed
this afternoon, on top of the morning's Stream A close. Five of six
items shipped in one bundle (`docs/AUDIT-2026-04-28-EXIT-C.md`):

- ✅ **Item 1 — Android UI automation** — formally shelved with rationale at `docs/UI-AUTOMATION-DECISION.md`. Three explicit conditions to revisit (regression pattern, contributor commitment, aapt2 → Gradle migration). Scaffold left at `tests/ui/`.
- ⏸ **Item 2 — IMAP scanner end-to-end** — deferred per `docs/AUDIT-PLAN.md § Out of scope` (needs operator-side throwaway test inbox).
- ✅ **Item 3 — `focuslock-mail.py` route coverage push** — +36 e2e tests across four new files: `test_e2e_unsubscribe_deadline.py`, `test_e2e_uncovered_webhooks.py`, `test_e2e_disposal_token.py`, `test_e2e_public_routes.py`. Tests `1080 → 1116`. Rounds 1–4 of Stream A had already absorbed most of what the original session-memory map listed as "uncovered" — the actual remaining gaps were narrower than the inventory suggested.
- ✅ **Item 4 — `make qa` wrapper** — root `Makefile` with 12 targets: `qa`/`qa-staging-up`/`qa-staging-down`/`qa-clean`/`qa-pytest`/`qa-runner`/`qa-wizard`/`qa-index`/`qa-perf`/`qa-matrix`/`lint`/`help`. Boots staging on `127.0.0.1:8435`, waits for `/version`, runs all four QA layers, tears down + state-cleans. Pidfile + log gitignored. Auto-detects `.venv/bin/python3`.
- ✅ **Item 5 — Regression matrix automation** — `staging/qa_matrix.py` walks the 14 sections of `docs/QA-CHECKLIST.md`, runs the programmable subset (sections 0, 2–10, 13), classifies the rest as `MANUAL`. Emits human table + JSON. `docs/QA-CHECKLIST.md` gained a top-level §Programmatic coverage table. Baseline: 10 pass · 0 fail · 0 skip · 4 manual.
- ✅ **Item 6 — Performance smoke** — `tests/test_perf_smoke.py` with throughput (100-sequential, p95 < 250ms), concurrent (20×5 threads, soft-gate — surfaced a known non-atomic R-M-W in `mesh_apply_order::add-paywall`, tracked separately), vault GC under load (200 blobs, GC < 5s). Opt-in via `PERF_TESTS=1` or `make qa-perf`.

**Tests**: 1080 → 1116 (+36 e2e routes) + 3 opt-in perf smoke. Ruff clean. No coordinated rollout — server-side test + tooling only.

**Next milestone**: Stream B (usability review — ~24–32h, the heaviest stream). Walk every UI surface with first-time-user eyes; capture friction in `docs/USABILITY-AUDIT-2026-XX.md`.

---

## Status — 2026-04-28 (Stream A audit closeout)

Stream A of the 2026-04-27 audit (`docs/AUDIT-PLAN.md` + `docs/AUDIT-FINDINGS-2026-04-27.md`) is closed. Five commits over two days landed every High and Medium fix, plus L-3 from the Low set. Full exit summary at `docs/AUDIT-2026-04-27-EXIT.md`.

- ✅ **H-1 (full)** — `/enforcement-orders`, `/memory`, `/standing-orders`, `/settings` all admin-token-gated (round-1 + round-4).
- ✅ **H-2** — seven evidence webhooks (compliment, gratitude, love_letter, offer, geofence-breach, evidence-photo, subscription-charge) slave-signed via `_verify_slave_signed_webhook`. New `SlaveSigner.java` helper.
- ✅ **M-1** — `/webhook/desktop-heartbeat` vault-node-signed (mirrors state-mirror pattern). Linux desktop collar signs; Windows desktop doesn't post heartbeats (no Win-side change).
- ✅ **M-2** — `/webhook/controller-register` admin-token-gated. Installer-only caller.
- ✅ **M-3 (full)** — `/webhook/register` device_id-validated (round-1) + bunny-signed (round-3).
- ✅ **M-4 (full)** — `/webhook/generate-task` (round-2, no caller, preempt) + `/webhook/verify-photo` (round-3, slave + companion sign on the way out) bunny-signed.
- ✅ **M-6** — desktop collar `/api/pair/create` (port 8435, 0.0.0.0) admin-token-gated on Linux + Windows. Windows desktop also gained `ADMIN_TOKEN` module-load.
- ✅ **M-7** — `MANAGE_EXTERNAL_STORAGE` removed from Lion's Share controller manifest (no usage in source).
- ✅ **M-8** — 19 logger sites wrapped in `_sanitize_log()` (CodeQL hadn't flagged; manual audit walk did).
- ✅ **L-3** — `OPERATOR_MESH_ID` validated by `_safe_mesh_id_static` at module load.

**APK rollout**: slave 74 → 75 (`com.focuslock` 8.32), companion 56 → 57 (`com.bunnytasker` 2.24). Coordinated relay + desktop-collar redeploy required. The out-of-repo `sync-standing-orders.sh` on the operator's homelab needs a single-line `Authorization: Bearer ${ADMIN_TOKEN}` add before deploying the new relay (otherwise the systemd timer's 5-min sync starts logging 403s).

**Findings tracked (deferred to medium-term)**: M-5 (relay-side nonce cache for bunny-signed `/api/mesh/{id}/*` — v1.1 hardening, best bundled with the next signing-surface change so the `nonce` field add is one coordinated rollout), L-1 (drop `?admin_token=` query-param auth in next major), L-2 (strip lat/lon from slave's `/api/status` sig-exempt response — design needed for `/api/location` signed-only path), L-4 (Android `network_security_config.xml` cleartext-traffic-permitted tightening — domain allowlist).

**Tests**: 1014 → 1080 (+66 across rounds 2–4; the round-1 device_id validator matrix relocated to round-3 because it now sits behind the sig gate). Ruff clean. Per-stream-A acceptance gate exited per `docs/AUDIT-2026-04-27-EXIT.md`.

**Next milestone**: Stream C (QA infrastructure expansion — Appium spike, IMAP end-to-end, `focuslock-mail.py` HTTP-route coverage, regression matrix automation, performance smoke test). Per the plan's recommended A → C → B ordering. Stream B (usability review) follows.

---

## Status — 2026-04-27 (post-coverage push + wizard + index restructure)

Productivity sprint that bridges from the multi-tenant correctness work of 2026-04-24/25/26 into the upcoming security + usability audit. Five PRs merged today (#23–#27):

- ✅ **`focuslock-mail.py` test coverage push** — three slices: `MeshAccountStore` + `VaultStore` class internals (PR #23, +87 tests), session-token + daily-blob + relay-node helpers (PR #24, +34 tests), `MessageStore` complementary coverage (PR #25, +34 tests). Tests `780 → 935`.
- ✅ **Signup wizard** — replaces `web/signup.html` single-card form with a 7-step fullscreen wizard (welcome → key + PIN → IMAP → rules → subscription → review → result+QR). New `_apply_initial_mesh_config` server-side dispatcher applies optional config in one mesh-create call. PR #26, +25 unit tests, tests `935 → 960`. Caught + fixed three bugs during QA (PIN passthrough, qa_runner audit-C1 sig regression, web_dir hardcoded path).
- ✅ **Programmatic 4-layer QA harness** (PR #26) — `qa_wizard_browser.py` (8 Playwright cases for the wizard), `qa_index_browser.py` (20 cases driving every button on the web remote with network interception verifying each `/admin/order` payload), `qa_runner.py` extended from 12 → 49 cases covering every relay-mode action surface (9 lock modes + paywall variations + scheduling + geofence + 4 sub tiers + tribute + streak + gamble + entrap + LAN-only safety + messaging + deadline-task). Plus Waydroid sanity (container boots, slave + companion APKs install).
- ✅ **Web-remote restructure** (PR #27) — 3 tabs (Control / Advanced / Inbox) → 4 tabs (Lock / Rules / Money / Inbox). Renames: "Bunny Balance" → Paywall, "Modifiers" → Lock Style, "+5m/esc" → Escape Penalty, "Good Boy" → Toy. Power Tools 11-button grab-bag split into Money tab (subscription + tribute + streak + paywall actions) + Rules tab (Location + Toy + Voice cards) + Lock tab (Danger Zone with Entrap). Subscription state previously split between Inbox and Power Tools now consolidated on Money. All button IDs preserved so JS handlers + Playwright tests still work; only structure + labels changed. Re-QA all 4 layers green.

**Cumulative session metrics**: 514 new tests since v1.2.0 (353 → 867 → 985, then verified at 960/960 after deduplication via 1 skipped UI spike). shared/ coverage 95.70%, floored at 95% in CI. 4-layer programmatic QA harness now standard practice on every UI/server PR.

The next milestone is the **full security + usability audit + QA expansion** — see `docs/AUDIT-PLAN.md` for the scoped plan. ~52–68 hours of focused work across three streams (Stream A: security, Stream B: usability, Stream C: QA infrastructure). Suggested ordering: A → C → B.

---

## Status — 2026-04-26 (IMAP per-mesh — last MT-correctness item)

`feat/imap-per-mesh-2026-04-26` closes the final multi-tenant correctness item from the 2026-04-24 audit. Single change set:

- ✅ **IMAP payment scanner per-mesh** (audit MEDIUM #5) — `check_payment_emails_multi()` walks every known mesh per cycle; `_scan_mesh_imap_once()` is the per-mesh-per-cycle primitive; per-mesh `apply_fn` closures route `payment-received` through each mesh's `_server_apply_order` so vault-blob propagation works on consumer meshes too. Operator inherits the relay's static IMAP creds as fallback; consumer meshes are scanned only once Lion has configured `set-payment-email` for their mesh. Per-mesh ledger isolation (already in place) means the same Message-ID arriving on two meshes credits both. 7 new tests in `tests/test_payment.py::TestCheckPaymentEmailsMultiMesh` (482 → 489 passing).

With this merge, every audit finding from 2026-04-24 is closed (BLOCKER #1, HIGH #2+#3+#4, MEDIUM #5+#6, LOW #7). The remaining open item from the previous status block is the **Web Remote QR "wrong key" bug** — likely closed by `6d47d1e`'s per-mesh session scoping, pending hardware re-test.

---

## Status — 2026-04-25 (messaging + multi-tenant correctness II)

Bundle landed on top of `6d47d1e` and ships the rest of the multi-tenant correctness work plus the long-pending Lion↔Bunny messaging feature. Five thematically distinct slices in one squash-merge PR:

- ✅ **Lion↔Bunny messaging shipped** — RSA-signed `/messages/{send,fetch,mark,edit,delete}` server routes (Lion-only edit/delete enforced at signature layer); `MessageStore` with `edit_history[]` + tombstone semantics; per-mesh ntfy fan-out (~1s wake-up); Android UI in both Lion's Share (`lion_message_thread`) and Bunny Tasker (`messages_container`). 23 new tests (8 `MessageStore` unit + 15 HTTP-level).
- ✅ **Auto-accept toggle + state-mirror + relay-key backfill** — `/api/mesh/{id}/auto-accept` (Lion-signed; key rotation still requires manual approval), `/api/mesh/{id}/state-mirror` (Collar-signed plaintext mirror unblocking server-side scanners on consumer meshes, whitelisted to 8 fields), `_ensure_relay_node_registered()` + `_relay_backfill_consumer_meshes()` (relay's pubkey auto-registers as approved vault node on every consumer mesh, fixing silent-drop of relay-signed state-derived blobs). 13 new HTTP-level tests.
- ✅ **Consumer install — `banks.json` bundled in companion APK** (303 lines under `android/companion/assets/`). Removes the relay round-trip on first-tap of Bunny Tasker's bank picker and lets first-run work offline.
- ✅ **Collar runtime — one-shot GPS geofence + immediate gossip + prior-launcher recapture** (`ControlService.java`). `doConfineHome` replaces the old two-step `get-location`→`set-geofence` dance (which broke in vault-mode); `kickRuntimePush` fires gossip immediately after paywall/geofence/messaging mutations; `capturePriorHomeBeforeLock` re-snapshots the user's current default launcher each lock so Release Forever survives mid-session launcher swaps.
- ✅ **Generic mesh installer** — `installers/install-mesh.{sh,ps1}` + `installers/README.md`. `--mesh-id` and `--mesh-url` are required (no operator-specific defaults baked in); `--no-ntfy` and `--reset-keys` modifiers; idempotent re-runs preserve the existing vault keypair so prior Lion approvals stick.
- ✅ **Log-injection hardening — every new server log on user-controlled input wrapped in `_sanitize_log()`**. Same threat model + helper PR #16 introduced for the pair-related logs; extended now to messaging, auto-accept, state-mirror, and relay-backfill log sites.

**Web Remote QR "wrong key" bug** likely closed by the per-mesh session scoping in `6d47d1e` — needs hardware re-test to confirm. **IMAP payment scanner per-mesh (MEDIUM #5)** — closed 2026-04-26 in `feat/imap-per-mesh-2026-04-26` (see status block above).

---

## Status — 2026-04-24 (late)

Publication path from an operator-only tree to a public repo is **done**. v1.0.0 shipped 2026-04-15, v1.1.0 the same day, **v1.2.0 on 2026-04-21** closes the last of the six audit CRITICALs (C1 slave HTTP signature verification) and every H-series finding. CI is fully green across lint + format + py3.10/3.11/3.12 tests + CodeQL + Scorecard. Every release carries Sigstore provenance + SBOM + SHA256SUMS + APK cert fingerprints.

**PR #16 (`5605572`, merged 2026-04-24)** landed three initiatives bundled: pairing recovery-flow strengthening, honest hosted-relay framing, and contribution-policy tightening.

**Post-PR-#16 hands-on device QA session (2026-04-24 late)** — surfaced a **class of multi-tenant correctness bugs** when running consumer meshes against the operator-shared `focus.wildhome.ca` relay. Every server-side singleton that wasn't keyed by `mesh_id` turned out to be an operator-only affordance. A focused audit found 7 findings (1 BLOCKER, 3 HIGH, 2 MEDIUM, 1 LOW). Six are fixed in this session; one remains:

- ✅ **`/admin/order` mesh routing** (BLOCKER #1) — now resolves `_orders_registry.get(req_mesh_id)` instead of always mutating operator globals.
- ✅ **Desktop heartbeat + penalty per-mesh** (HIGH #2+#3) — `_get_desktop_registry(mesh_id)` factory; `/webhook/desktop-penalty` routes via `_server_apply_order` instead of operator ADB.
- ✅ **`PairingRegistry` mesh-scoped** (HIGH #4) — composite key `{mesh_id}:{passphrase}`; HTTP handlers require `mesh_id`.
- ✅ **IMAP payment scanner per-mesh** (MEDIUM #5) — closed 2026-04-26 in `feat/imap-per-mesh-2026-04-26`. `check_payment_emails_multi()` walks every known mesh per cycle, per-mesh apply_fn routes `payment-received` to the originating mesh's orders + vault blob, and per-mesh ledger isolation prevents cross-mesh dedup collisions on the same Message-ID.
- ✅ **Web-session mesh context** (MEDIUM #6 — *"wrong key"* on Web Remote QR) — approve path iterates meshes for signature match; `_is_valid_admin_auth(token, mesh_id=…)` scopes session tokens.
- ✅ **ntfy push per-mesh** (LOW #7, surfaced during QA) — `_get_ntfy_topic(mesh_id)` derives `focuslock-{mesh_id}` so consumer meshes wake on push instead of 30s vault poll. Verified sub-second propagation live.

Also addressed this session: **consumer-install design pivot** — the `Settings.Global`-via-`WRITE_SECURE_SETTINGS` pattern requires adb during setup, which isn't acceptable for consumer deployments. Lion's Share already uses SharedPreferences for its own state (verified 0 writes, 2 legacy-fallback reads only). Collar + Bunny Tasker redesign (Phase 2 of "No-adb consumer install") is deferred — strategy choice between ContentProvider / AndroidKeyStore+server-authoritative / Device-Owner-QR-provisioning needs a design round after reading how much state is already server-authoritative vs phone-authoritative.

Consent + release hygiene: **bunny-initiated pair-reset removed** (violated the *"only Lion or factory reset can release"* contract); **mutual admin re-activation nag wired** on both sides (high-priority full-screen-intent notification when peer admin is removed); **prior-launcher capture now uses `MATCH_DEFAULT_ONLY`** so Release Forever restores the user's actual launcher (Fossify etc.) instead of stock.

Pegasus deploy verified live: `/api/pair/register` + `/api/pair/claim` correctly require `mesh_id`; consumer mesh `DNfs4xCZM-HY` has its own ntfy topic + payment ledger + desktop registry + pair state.

The per-phase plan below is preserved as historical context. Everything in Phases 0–9 is shipped — search the CHANGELOG or commit log by keyword if You need to trace a specific item.

## Post-v1.0 — in-flight & next

Ordered roughly by priority. Smaller than pre-v1.0 phase granularity — these are weeks of work total, not months.

### Short-term (next 1–2 sessions)

- ~~**Stream A — Security audit closeout**~~ — done 2026-04-28 across five commits. All Highs (H-1, H-2) + Mediums (M-1 through M-8) + L-3 fixed; M-5 + L-1 + L-2 + L-4 tracked. See `docs/AUDIT-2026-04-27-EXIT.md` for the full close-out. Coordinated rollout: slave APK 74→75, companion 56→57, desktop collar (Linux) redeploy, out-of-repo `sync-standing-orders.sh` token add.
- ~~**Stream C — QA infrastructure expansion**~~ — done 2026-04-28. Items 1, 3, 4, 5, 6 shipped; item 2 (IMAP scanner end-to-end) deferred per `docs/AUDIT-PLAN.md § Out of scope` (operator action). Full close-out at `docs/AUDIT-2026-04-28-EXIT-C.md`. Tests `1080 → 1116`, plus 3 opt-in perf smoke. No APK or homelab rollout — server-side test + tooling only.
- **Stream B — Usability review** — *first pass shipped 2026-04-28; remaining surfaces tracked.* See `docs/USABILITY-AUDIT-2026-04-28.md` for the full findings table. **Shipped this pass** (5 fixes): wizard ASCII glyphs → emoji with `aria-hidden`, sub-amount help text sharpened, 11 "bad path"/"bad vault path" errors now report expected route shape. **Deferred** (⏸ — session-friendly, ~30–60 min each): web-remote first-time-user walk, accessibility / color-contrast / keyboard-nav. **Operator-walk (📱)**: Lion's Share + Bunny Tasker + Collar 9 lock modes + desktop collars + pairing routes — these need real-device walks against the Pixel rig + Waydroid + Linux/Windows VMs.
- ~~**`focuslock-mail.py` test coverage push**~~ — done 2026-04-27 across PRs #23, #24, #25 (+155 tests across `MeshAccountStore`/`VaultStore`/session-token/blob-counters/relay-node/MessageStore-complementary). shared/ coverage 95.70% floored in CI; total tests `780 → 935`.
- ~~**Signup wizard with optional initial config**~~ — done 2026-04-27 in PR #26. `web/signup.html` rewritten as a 7-step fullscreen wizard; `/api/mesh/create` accepts optional `initial_config` (IMAP, tribute, subscription, bedtime, screen-time) and applies via `_apply_initial_mesh_config`. Tests `935 → 960`.
- ~~**Programmatic 4-layer QA harness**~~ — done 2026-04-27 in PR #26. Playwright walkthroughs for both `signup.html` (8 cases) and `index.html` (20 cases), `qa_runner.py` extended 12 → 49 cases covering every relay-mode action surface. Caught 3 real bugs during initial run (PIN passthrough silent loss, qa_runner audit-C1 sig regression, web_dir hardcoded path) — all fixed.
- ~~**Web remote restructure**~~ — done 2026-04-27 in PR #27. `web/index.html` 3 tabs (Control / Advanced / Inbox) → 4 tabs (Lock / Rules / Money / Inbox). "Power Tools" 11-button grab-bag dissolved into purpose-grouped cards. Renames: Bunny Balance → Paywall, Modifiers → Lock Style, +5m/esc → Escape Penalty, Good Boy → Toy. All button IDs preserved.
- ~~**IMAP payment scanner per-mesh**~~ — done 2026-04-26 in `feat/imap-per-mesh-2026-04-26`. Picked shape (b)-of-(a): single polling loop walks every known mesh per cycle (`check_payment_emails_multi`), each mesh gets resolved with its own creds (operator inherits relay-static fallback, consumer meshes only scanned once Lion configures `set-payment-email`). Per-mesh `apply_fn` closures route `payment-received` through each mesh's `_server_apply_order` for vault-blob propagation. 7 new tests in `tests/test_payment.py::TestCheckPaymentEmailsMultiMesh`.
- **Hardware QA against v1.2.0** — execute `docs/QA-v1.2.0-mesh.md` + `docs/MANUAL-QA.md` on the Pixel 10 rig (serial `57261FDCR004ZQ`). Protocol-level QA (7 vault + 9 C1 gate = 16 tests) already green via `/tmp/v120-qa/drive.py`; UI-bound tests still need a human with the device. Post-PR-#16: also walk `docs/QA-pairing.md` §1–§5 while the Pixel is paired — picks up the clearable-conflict dialog + Reset button + claim-expired / claim-unknown hint surfaces that landed in the same merge.
- **Enable `main` branch protection rules** — *operator action, no code change.* The policy work in PR #16 lands CODEOWNERS + `signed-commits.yml` + the CONTRIBUTING gate, but the mechanical enforcement requires three toggles in GitHub → Settings → Branches → `main`: (1) ✅ Require signed commits, (2) ✅ Require review from Code Owners, (3) ✅ Require status checks → tick `Signed Commits / Verify signatures on sensitive paths` + the pre-existing CI/CodeQL/Scorecard checks. Without these, CODEOWNERS still auto-requests review and `signed-commits.yml` still reports — but neither blocks merges.
- ~~**Desktop exe dedup**~~ — done. `build-win.py` now produces one canonical `FocusLock.exe` + `FocusLock-Watchdog.exe`; the vestigial `_build_config.py` variant bake and the unused `--paired-only` / `--generic-only` / `--homelab` / `--pin` / `--pubkey` CLI flags are gone. The collar's kill list in `focuslock-desktop-win.py` still catches legacy `FocusLock-Paired.exe` installs on first launch. See CHANGELOG [Unreleased].
- ~~**Node.js 20 actions deprecation**~~ — done in PRs #8 + #10 (2026-04-21). All ten Node-20 actions bumped to Node-24 equivalents, SHA-pinned; Scorecard's imposter-commit check forced a follow-up pinning codecov-action and codeql-action by commit SHA rather than annotated-tag SHA. Dependabot now groups github-actions bumps into one PR/week. See CHANGELOG [Unreleased].
- ~~**Collar headless-start note**~~ — done. Caveat now lives in both `docs/MANUAL-QA.md §1` and `docs/QA-v1.2.0-mesh.md §Pre-flight/On each device`. Documents the Android 14+ `SecurityException: Starting FGS with type location requires FOREGROUND_SERVICE_LOCATION` behaviour when bringing up `ControlService` via bare `adb shell am start-foreground-service` (the manifest *does* declare the permission — see `android/slave/AndroidManifest.xml:24` — but the runtime `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` grant is also required before the FGS-type check passes). Mitigation: drive through `ConsentActivity`, or `adb shell pm grant com.focuslock android.permission.ACCESS_FINE_LOCATION` before the service start.
- ~~**IMAP mock maintenance**~~ — done. `walk_imap_folders()` helper extracted from `shared/focuslock_payment.py::check_payment_emails` (+ public `DEFAULT_SKIP_FOLDERS` constant). New `tests/test_payment.py::TestWalkImapFolders` uses `create_autospec(imaplib.IMAP4_SSL, instance=True)` so any future `imaplib` signature drift trips the test rather than silently diverging from prod. Tests `353 → 360`. See CHANGELOG [Unreleased].

### Medium-term

- **Audit 2026-04-27 deferred items** (4 findings tracked from Stream A — full context in `docs/AUDIT-FINDINGS-2026-04-27.md` + `docs/AUDIT-2026-04-27-EXIT.md`):
  - **M-5 — relay-side nonce cache** for bunny-signed `/api/mesh/{id}/*` (auto-accept, subscribe, gamble, escape-event, state-mirror, deadline-task/clear, messages/*) and `/webhook/bunny-message`. Slave already has `SigVerifier.NonceCache` (Java, 4096 LRU + 600s TTL); the relay needs the Python equivalent + signers must include a `nonce` field. Bundle with the next coordinated APK signing-surface change so the wire-format add is one rollout, not two. Threat is replay within the existing ±5 min ts window.
  - **L-1 — drop `?admin_token=<t>` query-param auth path** on `/admin/status` and `/api/pair/vault-status/{mesh_id}`. URL-embedded tokens leak through reverse-proxy access logs / browser history / Referer / shoulder-surfing. Header-only path already exists. Schedule for next major version (operator scripts may still pass via query).
  - **L-2 — `/api/status` lat/lon strip on the slave**. Currently the sig-exempt `/api/status` returns `curLat`/`curLon` from `LocationManager`, so any LAN caller can poll the Bunny's location every few seconds. Inherent to LAN-gossip design; fix is a new signed `/api/location` route + Lion-only path.
  - **L-4 — Android `network_security_config.xml` tightening**. All three apps currently allow cleartext traffic to any host. Tighten to a `<domain-config>` allowlist for LAN ranges + the homelab IP, requiring HTTPS for the public relay URL. Documented as known weakness in `docs/THREAT-MODEL.md`.
- **Atomic `add-paywall`** (surfaced 2026-04-28 by `tests/test_perf_smoke.py::test_admin_order_concurrent`). The apply_fn at `focuslock-mail.py:681` does a non-atomic read-modify-write on `orders.paywall` — the `OrdersDocument.get` and `OrdersDocument.set` calls are individually locked but the increment between them isn't. Concurrent `/admin/order add-paywall` calls drop increments; in the perf smoke, 100 expected dollars landed as ~$53. In production this hasn't bitten because the operator's web remote and automated callers don't fire concurrently against the same paywall, but it's a real lock-drop. Fix: either lift the read-modify-write under `OrdersDocument.lock` (add an `add` helper) or pre-compute the new value via an atomic CAS. Touches enforcement-sensitive code → admin review per CONTRIBUTING.md. Perf smoke test will tighten its soft gate to strict-equality once fixed.
- **Per-tier subscription amounts configurable** — currently `{"bronze": 25, "silver": 35, "gold": 50}` is hardcoded in `focuslock-mail.py:792` (subscribe action). Wizard's subscription step + index.html's Money tab both reference these defaults; each operator's mesh probably wants its own scheme. Touches `focuslock-mail.py` (sensitive path → admin-merge from Nextcloud env). Surfaced during 2026-04-27 audit of the wizard.
- ~~**Replace ASCII step icons in the wizard**~~ — done 2026-04-28 in Stream B first pass. Replaced with `aria-hidden` emoji glyphs (👋 🔑 💌 📋 ⭐ 👀 ✨).
- **UI automation for pairing / release flows** — §1a direct-pair fingerprint pin and §1b QR-pair are gated by human interaction. *Spike attempted 2026-04-23 with `uiautomator2` against Waydroid on a single-device loopback pair (Lion's Share + Bunny Tasker + Collar co-located, "LAN" = 127.0.0.1): shelved. `uiautomator2.connect()` hangs at `_setup_jar` → `toybox md5sum` and wedges Waydroid's adbd for the rest of the session, requiring `waydroid session stop` + `sudo systemctl restart waydroid-container` to recover. The harness chain (Waydroid → adbd → uiautomator2's atx-agent/JAR layer) is three brittle links, each of which failed during the spike; running this on every commit would cost more operator time than it saves. Scaffold kept at `tests/ui/` (skipped by default, registered `ui` marker) as a starting point if someone takes a second swing. Espresso is ruled out separately — it requires Gradle, and this repo deliberately avoids Gradle.* Real path forward is either (a) a dual-device Appium harness that accepts the Waydroid pain, or (b) keep this as manual-QA per `docs/QA-pairing.md` until pairing-flow regressions become a pattern.
- ~~**README download badges**~~ — done (partial). CI / CodeQL / Scorecard / latest-release / license badges are live at the top of `docs/README.md`; a "Download" section documents the `github.com/.../releases/latest/download/<filename>` URL pattern and points at the Releases page (filenames include the tag, so a truly stable-filename link would need a `release.yml` change to additionally upload unversioned aliases — deferred).
- ~~**Stable-filename release aliases**~~ — done 2026-04-24 in PR #16 (`b1bfdc6`). `.github/workflows/release.yml` now copies each versioned artifact to a `-latest` alias (`focuslock-latest.apk`, `bunnytasker-latest.apk`, `focusctl-latest.apk`, `FocusLock-latest.exe`, `FocusLock-Watchdog-latest.exe`) during the Flatten step, so Sigstore attestation + SHA256SUMS cover them by content hash and the Release upload globs sweep them up for free. README can now link `releases/latest/download/focuslock-latest.apk` without discovering the current tag first. Validation deferred to the next `v*` tag — the workflow YAML diff is small enough to read confidently, and the pattern matches how the versioned names already work.
- **Supply-chain gap scan** — re-run Scorecard after Node.js actions bump. Address any new findings that arise from bumping the action versions. *(Partially verified 2026-04-21 — Scorecard run on main post-PR #10 was green with no new high-severity findings.)*

### Open bugs from 2026-04-24 device QA session

- **Web Remote QR pairing fails with "wrong key"** — Lion's Share → Web Remote → Scan QR fails at signature verify or session-lookup. Flagged during hands-on QA. **Likely closed** by the per-mesh session scoping in `6d47d1e` (`/admin/web-session approve` now iterates meshes for signature match; session tokens are mesh-scoped via `_is_valid_admin_auth(token, mesh_id=…)`). Pending hardware re-test on the Pixel 10 against a consumer mesh; if still reproducible, capture the exact error string + the logcat line from `focusctl` around the scan to narrow further.

### Large-scope initiative: No-adb consumer install (surfaced 2026-04-24)

The current install pattern requires `pm grant android.permission.WRITE_SECURE_SETTINGS` via adb during setup — because `Settings.Global.focus_lock_*` is the state-sharing layer between Collar + Bunny Tasker (anti-tamper: survives "Clear Data"). That's fine for a supervised Lion-with-cable setup moment, but **not acceptable for consumer-installable production flows** where no cable is available. Breaks OnePlus / ColorOS devices entirely (ColorOS blocks `pm grant` for signature permissions by default).

Three-phase migration — documented in conversation 2026-04-24:

- ~~**Phase 1 — Lion's Share local state to SharedPreferences**~~ *(2026-04-24 — already done, no change needed)*. Investigation turned up only 2 references to `Settings.Global` in `controller/MainActivity.java` (lines 131, 1742) — both are **legacy-fallback READS** wrapped in try/catch, not writes. `Settings.Global.getString` doesn't require `WRITE_SECURE_SETTINGS`; any permission-denied path just returns null and Lion's Share falls through to its SharedPreferences source of truth. All Lion's Share state (lion privkey, mesh_url, mesh_id, pin, active bunny slot, vault_mode) was already stored in `prefs.edit().put*(...)` via the multi-bunny slot scheme — confirmed by grep of the controller module. Verified on OnePlus (ColorOS, `pm grant` refused) — v70 launches + runs without any security exception. Legacy fallback reads kept in place for migration compat with users upgrading from the pre-multi-bunny adb-provisioned setup.
- **Phase 2 — Collar + Bunny Tasker client-state redesign** *(multi-session, ~4h + design review)*. Three strategies evaluated:
  - **A.** ContentProvider exposed by Collar, Bunny Tasker queries it. No perm. Loses Clear-Data resistance.
  - **B.** Hardware-backed AndroidKeyStore + server-authoritative state. Local is cache, authoritative state on mesh server. Clear-Data wipes cache but not enforcement. Needs server-side "suspicious re-pair detected → $500 tamper" logic. **Closest to where P2 paywall hardening (2026-04-17) already moved things.**
  - **C.** Device Owner via QR provisioning. DPM prevents Clear-Data + uninstall. Requires factory reset at setup. Strongest but highest user cost.
- **Phase 3 — Bunny Tasker + Collar migration + server-side tamper-reappear detection**. Dependent on Phase 2 strategy choice. Must preserve the admin-reactivation nag + mutual-admin monitor added 2026-04-24 (independent of `Settings.Global`).

Strategy choice for Phase 2 is deferred — needs a proper design round after reading how much state is already server-authoritative vs phone-authoritative.

### Strategic decisions — still deferred

1. ~~**Contribution policy for crypto / enforcement code**~~ — done 2026-04-23. `CONTRIBUTING.md §Review gate for enforcement-sensitive code` enumerates the path list + states the explicit maintainer-approval rule; `.github/CODEOWNERS` auto-requests maintainer review on those paths; `.github/workflows/signed-commits.yml` fails any PR that modifies a sensitive path with an unsigned commit. Signing is now required for crypto/enforcement/installer/CI surfaces — docs, tests, and CHANGELOG remain unsigned-friendly. Branch protection rule ("Require review from Code Owners" + "Require status checks: Signed Commits") is the final enforcement layer; configure in GitHub repo settings.
2. **Hosted relay** — *Partial close 2026-04-23: option C (soft middle) applied.* The README's Setup Options table + Hosted Relay Signup section now explicitly state that **no community relay is currently operated**, self-host is the only active path, and link to `docs/SELF-HOSTING.md` with a resource/complexity summary (Python 3.10+, ~256 MB RAM, $5/mo VPS, domain + TLS reverse proxy, ~30–60 min setup). The aspirational signup flow is preserved for whoever eventually stands one up. The underlying decision — stand up an operator-run relay, or formalize the project as self-host-only — remains open. Revisit once either (a) someone commits to operational responsibility or (b) public feedback indicates the current framing is misleading.
3. ~~**Contribution review SLA**~~ — done 2026-04-23. `SECURITY.md §Coordinated disclosure + CVE publication` now specifies the five embargo-ending conditions (patch release, day 90, active exploitation, reporter early-request, reporter extension-request for downstream coord), the GHSA + CVE workflow for High/Critical (GHSA-only for Medium unless reporter asks), CVSS v3.1 scoring convention, and explicit reporter-credit policy (name / handle / anonymous per reporter preference). Pre-existing §Response timeline already covered ack (5 business days), initial assessment (14 days), and patch targets (90d High-Crit / 180d Med / best-effort Low).

---

## Historical reference: Phase 0–9 plan (pre-v1.0)

The sections below drove the 70%-publishable → v1.0 work. All complete — preserved for traceability.

## Phase 0 — Pre-flight hygiene (half-day)

Quick wins that remove public-repo awkwardness with zero risk.

- Replace any operator-domain default in `focuslock-mail.py` with `localhost` fallback + warning log
- Replace hardcoded hint in `android/companion/src/com/bunnytasker/MainActivity.java:749` with generic `https://your-relay.example`
- Add **`SECURITY.md`** — vuln disclosure policy, contact, scope, 90-day rule
- Add **`CODE_OF_CONDUCT.md`** — Contributor Covenant 2.1 stock
- Add **`.editorconfig`** — enforce indent/EOL conventions across contributors
- Add **`CHANGELOG.md`** — seed with Keep-a-Changelog format, Unreleased section
- Sweep tracked files for personal markers — `.gitignore` already covers the risky ones (SESSION-HANDOFF, .claude/, config.json, keys, *.env, memory/)

**Exit criteria:** `git grep -Ei '(operator-domain|<your-username>|<operator>@)'` returns only docs where a hosted relay is deliberately referenced.

---

## Phase 1 — Lint + error-handling discipline (2–3 days)

Foundation for trusting the rest.

- **`pyproject.toml`** with ruff (E, F, W, B, S, UP, RUF) + mypy (strict on `shared/`, lenient on mail/desktop)
- **`.pre-commit-config.yaml`** — ruff, mypy, detect-secrets, end-of-file-fixer
- Fix all ruff findings (expect ~200–500 across the tree)
- **Audit all 164 `except Exception:`** — categorize:
  - *Log-and-continue* (ntfy, non-critical I/O) → add `logger.warning` with context
  - *Silent pass hiding security-relevant failure* (vault decrypt, signature verify, payment parse) → re-raise or return typed failure + log
  - *Top-level handler* → keep, ensure logged
- Replace inline `print(...)` in `focuslock-mail.py:787,809,880,889` with module logger
- Add `logger = logging.getLogger(__name__)` pattern everywhere

**Exit criteria:** `pre-commit run --all-files` passes; no silent exceptions in `shared/` or `*_mesh.py` / `*_vault.py` / `*_payment.py`.

---

## Phase 2 — Unit test suite (1 week)

Biggest lift. Prioritize crypto + payment — anywhere a silent bug costs the operator real money.

- **pytest** in `tests/` with conftest fixtures for vault keys, mock mesh peers, sample bank emails
- **`focuslock_vault.py`** — round-trip encrypt/decrypt, signature verify on tampered payload, key rotation, SHA1→SHA256 fallback, malformed input (target: 90%+ lines)
- **`focuslock_payment.py`** — parse every bank in `banks.json` with real-world email fixtures, anti-self-pay logic, total-paid tracking, paywall clearing (target: 80%+)
- **`focuslock_mesh.py`** — signed gossip accept/reject, dedup, peer address capping, LAN discovery parsing (target: 70%)
- **`focuslock-mail.py`** — integration tests with `httpx.MockTransport` for webhook → vault relay → LLM stub; pairing registry corruption recovery
- **`shared/focuslock_config.py`** — schema validation, missing-key defaults
- **Android** — skip instrumentation tests (no Gradle = real tax); add manual smoke-test checklist in `docs/MANUAL-QA.md` instead

**Exit criteria:** `pytest --cov` shows ≥75% on `shared/` and payment/vault/mesh modules; CI blocks merges that drop coverage.

---

## Phase 3 — QA infrastructure + Waydroid staging mesh (2–3 days)

QA is first-class, not an afterthought. Every subsequent phase ends with a regression run against this.

- **Staging mesh** — separate `mesh_id`, `admin_token`, `ntfy` topic; isolated from any production relay. Local `focuslock-mail.py` bound to `127.0.0.1:8435` or `staging.*` subdomain
- **Waydroid lineup:**
  - Waydroid #1 — bunny's phone surrogate (The Collar + Bunny Tasker both installed)
  - Waydroid #2 — lion's phone surrogate (Lion's Share)
  - Both pointed at staging mesh, not prod
- **Desktop collars:** Linux collar in a throwaway user on host, Windows collar on Win11 VM (qemu/kvm) — optional for v1
- **`docs/QA-CHECKLIST.md`** test matrix covering every user-facing flow:
  - Pairing (QR)
  - Lock/unlock
  - Paywall accrual + compound interest
  - Payment detection (mock bank emails from each region)
  - All 9 lock modes (Basic, Negotiation, Task, Compliment, Gratitude Journal, Exercise, Love Letter, Photo Task, Random)
  - Escape penalties (tiered $5/$10/$15+)
  - Subscription cycle
  - Photo task + Ollama eval
  - Geofence auto-lock
  - Vault round-trip + key rotation
  - Mesh gossip convergence (3+ peers)
  - ntfy push latency
  - Release Forever teardown + auto-uninstall
  - Factory reset @ 150 escapes
  - Consent screen first-run
- **Gaps Waydroid can't cover** — real SMS, real Lovense BT, real camera — documented as "manual on-device regression" with a short on-phone checklist
- **QA as gate** — CI runs the scriptable subset; manual checklist gates release tags

**Exit criteria:** `docs/QA-CHECKLIST.md` run-through from scratch on fresh Waydroid images passes; any failure blocks tagging.

---

## Phase 4 — CI pipeline (2 days)

Makes the tests + QA actually enforce quality.

- **`.github/workflows/ci.yml`**:
  - Matrix: Python 3.10/3.11/3.12 on ubuntu-latest
  - Jobs: lint (ruff + mypy) → test (pytest + coverage to Codecov) → build-android (aapt2 pipeline, verify APK) → build-win (PyInstaller on Windows runner)
- **`.github/workflows/release.yml`** — triggered on `v*` tag:
  - Build APKs + signed EXEs
  - Publish to Releases with `SHA256SUMS.txt`
  - Auto-generate changelog section from conventional commits or manual `CHANGELOG.md`
- **Dependabot** — pip + GitHub Actions, weekly
- **Branch protection** — require CI green + 1 maintainer review

**Exit criteria:** a deliberately-broken PR fails CI red; release tag produces signed artifacts with checksums.

---

## Phase 5 — Build reproducibility (2 days)

- **Android:**
  - Document **release-keystore workflow** in `docs/BUILD.md` — users generate their own via `keytool`, store at `~/.config/focuslock/release.keystore`
  - Build scripts read `FOCUSLOCK_KEYSTORE` / `FOCUSLOCK_KEYSTORE_PASS` from env, fail loudly if unset on release builds
  - Add `--release` flag to `android/*/build.sh` distinguishing debug vs release
  - Debug keystore stays fine for contributor local builds
- **Windows (PyInstaller):**
  - Set `SOURCE_DATE_EPOCH` from git commit timestamp
  - Strip PyInstaller boot-time metadata where possible
  - Document remaining non-determinism (compiled CPython bytecode timestamps)
- **SHA256 publication** — `release.yml` writes `SHA256SUMS.txt` alongside artifacts, optionally GPG-signed

**Exit criteria:** two back-to-back `./build.sh --release` invocations with same env produce byte-identical APKs.

---

## Phase 6 — Outsider documentation (1–2 days)

- **`docs/BUILD.md`** — full pipeline: JDK 17, Android SDK 35 path expectations, Python 3.10+, step-by-step for each of 3 APKs + Win + Linux
- **`docs/CONFIG.md`** — every `config.json` field: type, default, example, security implications
- **`docs/SELF-HOSTING.md`** — full self-host walkthrough: DNS + TLS, admin token generation, `mesh_id` provisioning, first phone pairing
- **`docs/THREAT-MODEL.md`** — consolidate threat framing from `VAULT-DESIGN.md` + `DISCLAIMER.md`. In-scope vs out-of-scope adversaries
- **`docs/ARCHITECTURE.md`** — sanitized architecture reference (no personal names), sequence diagrams for lock/unlock/payment/pairing flows
- **README improvements** — consent disclaimer pinned at top, "Who is this for?" section, link to all doc pages

**Exit criteria:** a stranger can go from zero to paired phone without contacting the maintainer.

---

## Phase 7 — Dependency + supply-chain hygiene (1 day)

- **`pyproject.toml`** with proper `[project]` metadata, optional-deps groups (`desktop`, `server`, `dev`)
- **`SECURITY.md`** — already from Phase 0 — add response-time SLA
- **`SBOM.spdx.json`** generated in release workflow via `cyclonedx-py` or equivalent
- Pin Android build-tools version in `build.sh` (currently implicit)
- Reproduce `cryptography` wheel from source on ARM64 targets (known Windows ARM issue)

---

## Phase 7.5 — Supply-chain finishing (½ day)

Gap-fill against current OSS supply-chain best practices. Independent leaf — runs after Phase 7, before Phase 9.

- **Pin GitHub Actions by commit SHA** in every workflow (CI, release, CodeQL, Scorecard). Dependabot keeps SHAs current; mutable tags are out.
- **CodeQL workflow** (`.github/workflows/codeql.yml`) — free GitHub-native SAST on Python. Runs on push + weekly cron.
- **Sigstore build provenance** via `actions/attest-build-provenance` on every release artifact (APKs, EXEs, SBOM). Verifiable with `gh attestation verify`.
- **OpenSSF Scorecard workflow** (`.github/workflows/scorecard.yml`) — automated health metric, surfaces missing branch protection / pinned-deps / etc. Adds a badge to README.
- **APK signing cert fingerprint** documented in `docs/BUILD.md` — release builds use the operator's keystore; users sideloading should verify the cert SHA-256 matches the published value.
- **`CONTRIBUTING.md`** — short companion to `CODE_OF_CONDUCT.md`. Where to file issues, what PRs we accept, security reports → SECURITY.md, no AI-generated PRs without disclosure.
- **Issue + PR templates** (`.github/ISSUE_TEMPLATE/bug.yml`, `feature.yml`, `config.yml`; `.github/PULL_REQUEST_TEMPLATE.md`) — channel reports into the right shape.

**Exit criteria:** A fresh release cuts SBOM + signed artifacts + provenance attestations; CodeQL and Scorecard run green; cert fingerprint is published; CONTRIBUTING + templates are live.

---

## Phase 8 — Android build modernization (OPTIONAL, 3–5 days)

**Decision point — deferred to project maintainers.**

- **Path A (keep no-Gradle):** document aapt2 pipeline rigorously, accept sideload-only forever. Play Store rejects device-admin apps regardless. **Cost: 0 extra days.**
- **Path B (migrate to Gradle):** opens F-Droid submission path (they accept device admin with review), lowers contribution barrier, unlocks Android lint/test ecosystem. **Cost: 3–5 days per module × 3 modules.**

**Default recommendation:** Path A for v1.0, revisit Path B for v1.5 if community interest materializes. F-Droid review for a device-admin app is multi-month regardless.

---

## Phase 9 — Launch (1 day)

- Tag **v1.0.0**, publish GitHub Release with full changelog + signed artifacts
- Announce post draft (for maintainer review before publishing anywhere)
- Issue + discussion templates
- Optional: `awesome-selfhosted` / `awesome-consent-tech` PRs if such lists exist

---

## Critical path & sequencing

```
Phase 0 ──┬─> Phase 1 ──> Phase 2 ──> Phase 3 ──> Phase 4 ──> Phase 9
          └─> Phase 6 (parallel with 1–2)
                  │
Phase 5 ──────────┴─> Phase 9
Phase 7 ─────────────> Phase 9
```

- **Phase 0 + 6** can run in parallel (docs work doesn't block code work)
- **Phases 1 → 2 → 3 → 4** strictly sequential (lint before tests before QA before CI)
- **Phases 5 + 7** independent leaves
- **Phase 7.5** supply-chain finishing — runs after 7, before 9
- **Phase 8** optional, out of critical path

**Total estimate:** ~4 weeks solo, ~12 focused paired days.

---

## Strategic decisions — deferred to project maintainers

1. **Public or invite-only first?** GitHub public immediately vs private → invite trusted kink-tech folks → public after vetting
2. **Contribution policy?** Outside contributors touching crypto/enforcement code needs careful review. "Docs PRs welcome, code PRs require issue discussion first"?
3. **Hosted relay** — keep free for community, or require self-host only? README currently implies the former.
4. **Harassment response plan?** Issues/discussions open, moderated, or disabled at launch?

---

## Progress tracking

Phases 0–9 are tracked as tasks in the active session. Each phase ends with:
- Task marked completed
- Regression run against Phase 3 staging mesh (once Phase 3 exists)
- Commit on `main` with phase tag in subject

See also: `docs/QA-CHECKLIST.md` (created Phase 3).

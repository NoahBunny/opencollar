# Publishable Roadmap

## Status — 2026-04-25 (messaging + multi-tenant correctness II)

Bundle landed on top of `6d47d1e` and ships the rest of the multi-tenant correctness work plus the long-pending Lion↔Bunny messaging feature. Five thematically distinct slices in one squash-merge PR:

- ✅ **Lion↔Bunny messaging shipped** — RSA-signed `/messages/{send,fetch,mark,edit,delete}` server routes (Lion-only edit/delete enforced at signature layer); `MessageStore` with `edit_history[]` + tombstone semantics; per-mesh ntfy fan-out (~1s wake-up); Android UI in both Lion's Share (`lion_message_thread`) and Bunny Tasker (`messages_container`). 23 new tests (8 `MessageStore` unit + 15 HTTP-level).
- ✅ **Auto-accept toggle + state-mirror + relay-key backfill** — `/api/mesh/{id}/auto-accept` (Lion-signed; key rotation still requires manual approval), `/api/mesh/{id}/state-mirror` (Collar-signed plaintext mirror unblocking server-side scanners on consumer meshes, whitelisted to 8 fields), `_ensure_relay_node_registered()` + `_relay_backfill_consumer_meshes()` (relay's pubkey auto-registers as approved vault node on every consumer mesh, fixing silent-drop of relay-signed state-derived blobs). 13 new HTTP-level tests.
- ✅ **Consumer install — `banks.json` bundled in companion APK** (303 lines under `android/companion/assets/`). Removes the relay round-trip on first-tap of Bunny Tasker's bank picker and lets first-run work offline.
- ✅ **Collar runtime — one-shot GPS geofence + immediate gossip + prior-launcher recapture** (`ControlService.java`). `doConfineHome` replaces the old two-step `get-location`→`set-geofence` dance (which broke in vault-mode); `kickRuntimePush` fires gossip immediately after paywall/geofence/messaging mutations; `capturePriorHomeBeforeLock` re-snapshots the user's current default launcher each lock so Release Forever survives mid-session launcher swaps.
- ✅ **Generic mesh installer** — `installers/install-mesh.{sh,ps1}` + `installers/README.md`. `--mesh-id` and `--mesh-url` are required (no operator-specific defaults baked in); `--no-ntfy` and `--reset-keys` modifiers; idempotent re-runs preserve the existing vault keypair so prior Lion approvals stick.
- ✅ **Log-injection hardening — every new server log on user-controlled input wrapped in `_sanitize_log()`**. Same threat model + helper PR #16 introduced for the pair-related logs; extended now to messaging, auto-accept, state-mirror, and relay-backfill log sites.

**Web Remote QR "wrong key" bug** likely closed by the per-mesh session scoping in `6d47d1e` — needs hardware re-test to confirm. **IMAP payment scanner per-mesh (MEDIUM #5)** is unchanged and remains the top priority for the next session (~90 min, deferred per operator decision 2026-04-25).

---

## Status — 2026-04-24 (late)

Publication path from an operator-only tree to a public repo is **done**. v1.0.0 shipped 2026-04-15, v1.1.0 the same day, **v1.2.0 on 2026-04-21** closes the last of the six audit CRITICALs (C1 slave HTTP signature verification) and every H-series finding. CI is fully green across lint + format + py3.10/3.11/3.12 tests + CodeQL + Scorecard. Every release carries Sigstore provenance + SBOM + SHA256SUMS + APK cert fingerprints.

**PR #16 (`5605572`, merged 2026-04-24)** landed three initiatives bundled: pairing recovery-flow strengthening, honest hosted-relay framing, and contribution-policy tightening.

**Post-PR-#16 hands-on device QA session (2026-04-24 late)** — surfaced a **class of multi-tenant correctness bugs** when running consumer meshes against the operator-shared `focus.wildhome.ca` relay. Every server-side singleton that wasn't keyed by `mesh_id` turned out to be an operator-only affordance. A focused audit found 7 findings (1 BLOCKER, 3 HIGH, 2 MEDIUM, 1 LOW). Six are fixed in this session; one remains:

- ✅ **`/admin/order` mesh routing** (BLOCKER #1) — now resolves `_orders_registry.get(req_mesh_id)` instead of always mutating operator globals.
- ✅ **Desktop heartbeat + penalty per-mesh** (HIGH #2+#3) — `_get_desktop_registry(mesh_id)` factory; `/webhook/desktop-penalty` routes via `_server_apply_order` instead of operator ADB.
- ✅ **`PairingRegistry` mesh-scoped** (HIGH #4) — composite key `{mesh_id}:{passphrase}`; HTTP handlers require `mesh_id`.
- ❌ **IMAP payment scanner per-mesh** (MEDIUM #5) — still scans only operator's configured IMAP, applies via `OPERATOR_MESH_ID`. Non-operator meshes' `set-payment-email` silently ignored. ~90 min, needs per-mesh scanner thread or per-mesh polling loop. **Next session's top priority. Deferred to next session per operator decision (2026-04-25).**
- ✅ **Web-session mesh context** (MEDIUM #6 — *"wrong key"* on Web Remote QR) — approve path iterates meshes for signature match; `_is_valid_admin_auth(token, mesh_id=…)` scopes session tokens.
- ✅ **ntfy push per-mesh** (LOW #7, surfaced during QA) — `_get_ntfy_topic(mesh_id)` derives `focuslock-{mesh_id}` so consumer meshes wake on push instead of 30s vault poll. Verified sub-second propagation live.

Also addressed this session: **consumer-install design pivot** — the `Settings.Global`-via-`WRITE_SECURE_SETTINGS` pattern requires adb during setup, which isn't acceptable for consumer deployments. Lion's Share already uses SharedPreferences for its own state (verified 0 writes, 2 legacy-fallback reads only). Collar + Bunny Tasker redesign (Phase 2 of "No-adb consumer install") is deferred — strategy choice between ContentProvider / AndroidKeyStore+server-authoritative / Device-Owner-QR-provisioning needs a design round after reading how much state is already server-authoritative vs phone-authoritative.

Consent + release hygiene: **bunny-initiated pair-reset removed** (violated the *"only Lion or factory reset can release"* contract); **mutual admin re-activation nag wired** on both sides (high-priority full-screen-intent notification when peer admin is removed); **prior-launcher capture now uses `MATCH_DEFAULT_ONLY`** so Release Forever restores the user's actual launcher (Fossify etc.) instead of stock.

Pegasus deploy verified live: `/api/pair/register` + `/api/pair/claim` correctly require `mesh_id`; consumer mesh `DNfs4xCZM-HY` has its own ntfy topic + payment ledger + desktop registry + pair state.

The per-phase plan below is preserved as historical context. Everything in Phases 0–9 is shipped — search the CHANGELOG or commit log by keyword if You need to trace a specific item.

## Post-v1.0 — in-flight & next

Ordered roughly by priority. Smaller than pre-v1.0 phase granularity — these are weeks of work total, not months.

### Short-term (next 1–2 sessions)

- **IMAP payment scanner per-mesh** (audit MEDIUM #5) — *next session's top priority, ~90 min*. The scanner still runs only against the operator's configured IMAP and credits payments to `OPERATOR_MESH_ID`; non-operator meshes' `set-payment-email` is silently ignored. Two viable shapes: (a) one scanner thread per mesh that has a `set-payment-email` configured, or (b) a single polling loop that walks `_mesh_accounts.meshes` per cycle and fetches each mesh's IMAP credentials. (b) is simpler if Bunnies share a single mailbox+filter setup; (a) is correct if each mesh wants independent credentials. Tests: extend `tests/test_payment.py::TestWalkImapFolders` with multi-mesh fixtures.
- **Hardware QA against v1.2.0** — execute `docs/QA-v1.2.0-mesh.md` + `docs/MANUAL-QA.md` on the Pixel 10 rig (serial `57261FDCR004ZQ`). Protocol-level QA (7 vault + 9 C1 gate = 16 tests) already green via `/tmp/v120-qa/drive.py`; UI-bound tests still need a human with the device. Post-PR-#16: also walk `docs/QA-pairing.md` §1–§5 while the Pixel is paired — picks up the clearable-conflict dialog + Reset button + claim-expired / claim-unknown hint surfaces that landed in the same merge.
- **Enable `main` branch protection rules** — *operator action, no code change.* The policy work in PR #16 lands CODEOWNERS + `signed-commits.yml` + the CONTRIBUTING gate, but the mechanical enforcement requires three toggles in GitHub → Settings → Branches → `main`: (1) ✅ Require signed commits, (2) ✅ Require review from Code Owners, (3) ✅ Require status checks → tick `Signed Commits / Verify signatures on sensitive paths` + the pre-existing CI/CodeQL/Scorecard checks. Without these, CODEOWNERS still auto-requests review and `signed-commits.yml` still reports — but neither blocks merges.
- ~~**Desktop exe dedup**~~ — done. `build-win.py` now produces one canonical `FocusLock.exe` + `FocusLock-Watchdog.exe`; the vestigial `_build_config.py` variant bake and the unused `--paired-only` / `--generic-only` / `--homelab` / `--pin` / `--pubkey` CLI flags are gone. The collar's kill list in `focuslock-desktop-win.py` still catches legacy `FocusLock-Paired.exe` installs on first launch. See CHANGELOG [Unreleased].
- ~~**Node.js 20 actions deprecation**~~ — done in PRs #8 + #10 (2026-04-21). All ten Node-20 actions bumped to Node-24 equivalents, SHA-pinned; Scorecard's imposter-commit check forced a follow-up pinning codecov-action and codeql-action by commit SHA rather than annotated-tag SHA. Dependabot now groups github-actions bumps into one PR/week. See CHANGELOG [Unreleased].
- ~~**Collar headless-start note**~~ — done. Caveat now lives in both `docs/MANUAL-QA.md §1` and `docs/QA-v1.2.0-mesh.md §Pre-flight/On each device`. Documents the Android 14+ `SecurityException: Starting FGS with type location requires FOREGROUND_SERVICE_LOCATION` behaviour when bringing up `ControlService` via bare `adb shell am start-foreground-service` (the manifest *does* declare the permission — see `android/slave/AndroidManifest.xml:24` — but the runtime `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` grant is also required before the FGS-type check passes). Mitigation: drive through `ConsentActivity`, or `adb shell pm grant com.focuslock android.permission.ACCESS_FINE_LOCATION` before the service start.
- ~~**IMAP mock maintenance**~~ — done. `walk_imap_folders()` helper extracted from `shared/focuslock_payment.py::check_payment_emails` (+ public `DEFAULT_SKIP_FOLDERS` constant). New `tests/test_payment.py::TestWalkImapFolders` uses `create_autospec(imaplib.IMAP4_SSL, instance=True)` so any future `imaplib` signature drift trips the test rather than silently diverging from prod. Tests `353 → 360`. See CHANGELOG [Unreleased].

### Medium-term

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

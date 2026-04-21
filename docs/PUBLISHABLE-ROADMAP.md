# Publishable Roadmap

## Status — 2026-04-21

Publication path from an operator-only tree to a public repo is **done**. v1.0.0 shipped 2026-04-15, v1.1.0 the same day, **v1.2.0 on 2026-04-21** closes the last of the six audit CRITICALs (C1 slave HTTP signature verification) and every H-series finding. CI is fully green across lint + format + py3.10/3.11/3.12 tests + CodeQL + Scorecard. Every release carries Sigstore provenance + SBOM + SHA256SUMS + APK cert fingerprints.

The per-phase plan below is preserved as historical context. Everything in Phases 0–9 is shipped — search the CHANGELOG or commit log by keyword if You need to trace a specific item.

## Post-v1.0 — in-flight & next

Ordered roughly by priority. Smaller than pre-v1.0 phase granularity — these are weeks of work total, not months.

### Short-term (next 1–2 sessions)

- **Hardware QA against v1.2.0** — execute `docs/QA-v1.2.0-mesh.md` + `docs/MANUAL-QA.md` on the Pixel 10 rig (serial `57261FDCR004ZQ`). Protocol-level QA (7 vault + 9 C1 gate = 16 tests) already green via `/tmp/v120-qa/drive.py`; UI-bound tests still need a human with the device.
- ~~**Desktop exe dedup**~~ — done. `build-win.py` now produces one canonical `FocusLock.exe` + `FocusLock-Watchdog.exe`; the vestigial `_build_config.py` variant bake and the unused `--paired-only` / `--generic-only` / `--homelab` / `--pin` / `--pubkey` CLI flags are gone. The collar's kill list in `focuslock-desktop-win.py` still catches legacy `FocusLock-Paired.exe` installs on first launch. See CHANGELOG [Unreleased].
- ~~**Node.js 20 actions deprecation**~~ — done in PRs #8 + #10 (2026-04-21). All ten Node-20 actions bumped to Node-24 equivalents, SHA-pinned; Scorecard's imposter-commit check forced a follow-up pinning codecov-action and codeql-action by commit SHA rather than annotated-tag SHA. Dependabot now groups github-actions bumps into one PR/week. See CHANGELOG [Unreleased].
- ~~**Collar headless-start note**~~ — done. Caveat now lives in both `docs/MANUAL-QA.md §1` and `docs/QA-v1.2.0-mesh.md §Pre-flight/On each device`. Documents the Android 14+ `SecurityException: Starting FGS with type location requires FOREGROUND_SERVICE_LOCATION` behaviour when bringing up `ControlService` via bare `adb shell am start-foreground-service` (the manifest *does* declare the permission — see `android/slave/AndroidManifest.xml:24` — but the runtime `ACCESS_FINE_LOCATION` / `ACCESS_COARSE_LOCATION` grant is also required before the FGS-type check passes). Mitigation: drive through `ConsentActivity`, or `adb shell pm grant com.focuslock android.permission.ACCESS_FINE_LOCATION` before the service start.
- **IMAP mock maintenance** — the `_install_fake_imap` helper in `tests/test_payment.py` now mocks `list / select / search / fetch`. Any future change to `shared/focuslock_payment.py`'s IMAP walk shape needs to update this helper in the same commit. Consider extracting the folder-walk into a small helper + test against a real `imaplib` spec object.

### Medium-term

- **UI automation for pairing / release flows** — §1a direct-pair fingerprint pin and §1b QR-pair are gated by human interaction. Appium + an espresso harness could drive them, but that's 1–2 days of scaffolding per app × 3 apps. Only worth it if pairing-flow regressions become a pattern.
- **README download badges** — point at `/releases/latest/download/<filename>` for APKs + EXEs. Optional polish.
- **Supply-chain gap scan** — re-run Scorecard after Node.js actions bump. Address any new findings that arise from bumping the action versions.

### Strategic decisions — still deferred

1. **Contribution policy for crypto / enforcement code** — outsider PRs touching vault, pairing, or paywall need a structured review process. `CONTRIBUTING.md` hand-waves this; firm it up when the first outside PR lands.
2. **Hosted relay** — README currently implies a community-hostable relay exists. Either stand one up (operational burden) or drop the implication and go fully self-host-only in `docs/SELF-HOSTING.md`.
3. **Contribution review SLA** — expect a public security report within 90 days of v1.0 going public; draft a triage workflow before it arrives, not after.

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

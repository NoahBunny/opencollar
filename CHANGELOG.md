# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
starting with v1.0.0.

## [Unreleased]

### Added
- `SECURITY.md` — vulnerability disclosure policy
- `CODE_OF_CONDUCT.md` — custom code of conduct tailored to the power-exchange context
- `.editorconfig` — shared editor conventions
- `CHANGELOG.md` — this file
- `docs/PUBLISHABLE-ROADMAP.md` — phased plan to v1.0.0
- `pyproject.toml` — ruff + mypy configuration (Phase 1a)
- `.pre-commit-config.yaml` — ruff, ruff-format, mypy on shared/, hygiene hooks (Phase 1a)
- `.git-blame-ignore-revs` — skip mechanical reformat commits in `git blame` (Phase 1a)
- `logger = logging.getLogger(__name__)` module pattern in `shared/focuslock_vault.py`, `shared/focuslock_payment.py`, `focuslock_mesh.py`, `focuslock-mail.py` (Phase 1b-core)
- `logger = logging.getLogger(__name__)` extended to the remaining 11 modules: `focuslock-desktop{,-win}.py`, `focuslock_ntfy.py`, `installers/re-enslave-watcher.py`, and the shared/ helpers (`focuslock_adb`, `focuslock_config`, `focuslock_evidence`, `focuslock_http`, `focuslock_llm`, `focuslock_sync`, `focuslock_transport`) (Phase 1b-tail)
- `logging.basicConfig` wired at startup in the three entry-points (`focuslock-mail.py`, `focuslock-desktop.py`, `focuslock-desktop-win.py`) — format `%(asctime)s %(levelname)-7s %(name)s: %(message)s`, datefmt `%Y-%m-%d %H:%M:%S`. Library modules inherit from the root logger (Phase 1b-tail)
- **Phase 2 — unit test suite.** `tests/` with 266 tests covering the shared/ security-critical surface. Coverage: `focuslock_vault.py` 100%, `focuslock_payment.py` 92%, `focuslock_mesh.py` 70%, `focuslock_config.py` 98%, `focuslock_http.py` 100%, `focuslock_adb.py` 96% — 78% combined (≥75% exit criterion met). `[tool.pytest.ini_options]` + `[tool.coverage.*]` added to `pyproject.toml`. Invoke with `uv run --with pytest --with pytest-cov --with cryptography pytest tests/`. Also documents a within-call dedup quirk in `VoucherPool.store` (cross-call dedup, which is what matters in production, works correctly).
- **Phase 3 — QA infrastructure.** `docs/QA-CHECKLIST.md` (14-section regression matrix), `docs/STAGING.md` (isolated staging mesh setup with Waydroid), `docs/MANUAL-QA.md` (on-device checklist for radios Waydroid can't emulate). `staging/config.json.template` + `staging/start-staging.sh` for a scriptable staging relay bound to 127.0.0.1. `staging/qa_runner.py` — scripted Lion that drives the admin API through lock/unlock/paywall/subscribe/message flows and verifies state transitions. On first run, surfaced two real bugs in `focuslock-mail.py` (see Fixed).
- **Lion's Share controller v63** — new **Payment Email** button on the main screen (`android/controller/res/layout/activity_main.xml`) opens a dialog for IMAP host / email / app-password, saves to prefs, and POSTs `/api/set-payment-email`. Version bumped 62 → 63 (`AndroidManifest.xml`), `installers/re-enslave-lib.sh` retargeted to `focusctl-v63.apk`. Deployed to Jace's phone 2026-04-14 with data preserved (`-r` install, no re-pair).
- **Phase 4 — CI pipeline.** `.github/workflows/ci.yml` (lint + ruff format + mypy + pytest matrix on Python 3.10/3.11/3.12, build all 3 APKs, build Windows EXEs, verify APK signatures) and `.github/workflows/release.yml` (tag-driven release with auto-generated `SHA256SUMS.txt`, optional release-keystore secret, GitHub Release publication). `.github/dependabot.yml` for weekly pip + GitHub Actions updates. Concurrency cancellation on stale PRs. RUF005 added to `build-win.py` per-file-ignores; one stale `body` → `_body` cleanup in `staging/qa_runner.py`. Residual `ruff format` drift on 10 previously-unformatted files (`focuslock-mail.py`, desktop collars, `shared/focuslock_{payment,sync}.py`, all `tests/test_*.py`) applied to make Phase 4 CI green.
- **Phase 5 — Build reproducibility.** `--release` flag on all three Android `build.sh` scripts. Release builds require `FOCUSLOCK_KEYSTORE` + `FOCUSLOCK_KEYSTORE_PASS` env vars and fail loudly if unset; debug auto-keystore stays for contributor builds. `SOURCE_DATE_EPOCH` set from git commit timestamp in the release workflow before PyInstaller runs. `--release` and `--debug` are the only accepted flags; unknown args fail fast.
- **Phase 6 — Outsider documentation.** `docs/BUILD.md` (toolchain matrix, per-component build commands, release-keystore generation, reproducibility notes), `docs/CONFIG.md` (every config field with type/default/security implications + 3 example configs), `docs/SELF-HOSTING.md` (DNS → TLS → server → first pairing in 8 steps + ops + backup checklist), `docs/THREAT-MODEL.md` (in-scope vs out-of-scope adversaries, two trust tiers, known v1 weaknesses), `docs/ARCHITECTURE.md` (sanitized component map, source layout table, sequence diagrams for lock/payment/pairing/subscription, onboarding checklist). README rewritten with a pinned consent disclaimer banner, "Who is this for?" section, and a documentation index linking every doc page.
- **Phase 7 — Dependency + supply-chain hygiene.** `pyproject.toml` now ships proper `[project]` metadata (PEP 621): name `focuslock`, version `0.9.0`, GPL-3.0-or-later, classifiers, dependency on `cryptography>=42`, and `[project.optional-dependencies]` groups `desktop-win` (pystray + pillow), `server` (reserved), `dev` (pytest + pytest-cov + ruff + mypy). Project installs as metadata-only (`py-modules = []`) until a future src/ migration. Wheel builds clean (`uv build --wheel`). New `sbom` job in `release.yml` generates `SBOM.cdx.json` (CycloneDX 1.5) from `requirements.txt` via `cyclonedx-bom`; SBOM is uploaded as a release artifact, hashed in `SHA256SUMS.txt`, and attached to the GitHub Release. Android build-tools already pinned at `35.0.0` in every `build.sh` (verified). `SECURITY.md` already had the response SLA from Phase 0 (no change needed). ARM64 cryptography wheel-from-source caveat documented in `docs/BUILD.md`. `.gitignore` covers `*.egg-info/` from local wheel builds.

### Changed
- `focuslock-mail.py` — default `Host` header fallback changed from operator's personal domain to `localhost`
- `android/companion/.../MainActivity.java` — server URL input hint changed from operator's personal domain to a generic example
- Python codebase reformatted with `ruff format` (Phase 1a — mechanical, skipped in `git blame` via `.git-blame-ignore-revs`)
- Security-critical exception handlers now use structured logging at appropriate severity (Phase 1b-core):
  - Vault signature-verify and decrypt failures → `logger.warning`
  - Payment email parse + IMAP loop errors → `logger.warning`/`logger.error`
  - Mesh signature verify + state I/O (orders, peers, vouchers) → `logger.warning`
  - Roadmap-called-out `[warn]` prints in `focuslock-mail.py` (paywall parse, ntfy push, pairing registry) → logger
- All remaining ~300 diagnostic `print(...)` calls across `focuslock-mail.py` (~90), `focuslock-desktop.py` (~75), `focuslock-desktop-win.py` (~91), `focuslock_mesh.py` (~14), `focuslock_ntfy.py`, `shared/focuslock_payment.py` (~9 missed in Phase 1b-core), and smaller modules migrated to `logger.{info,warning,exception,debug}` with `%-format` lazy formatting (Phase 1b-tail)
- Silent `except Exception: pass` blocks triaged (Phase 1b-tail) — ADB wrapper, mesh trust I/O, mesh account load, homelab URL parse, Lion pubkey load now leave a debug breadcrumb. Tailscale/DNS probes and liberation cleanup stay intentionally silent.

### Fixed
- **Real bugs surfaced by lint (Phase 1a):**
  - `focuslock-desktop-win.py` was missing `import subprocess` (5 crash sites: bedtime check + 4 process-management paths) and `import datetime` (1 bedtime check site)
  - `focuslock-mail.py:806` called bare `push_to_peers(...)` instead of `mesh.push_to_peers(...)` — fine-application mesh push was broken
- `set-payment-email` feature completed with missing `ORDER_KEYS` schema entries and `focuslock_payment.py` consumer (hot-swappable IMAP creds via Lion's Share app)
- **QA-surfaced bugs (Phase 3):**
  - `focuslock-mail.py:530` — `add-paywall` accepted negative amounts and allowed the paywall to go negative. Now clamps the result to `max(0, current + delta)` and catches non-integer amount values.
  - `focuslock-mail.py:598-612` — `subscribe` with no explicit `due` param set `sub_due` to `now` instead of the documented `now + 7d`; the `now + 7d` branch was unreachable. Rewrote so the default is `now + 7d`, explicit `"now"` is still honored, and explicit ms values pass through — consistent with `project_sub_due_cap.md` ("pre-pay forfeits remainder").

### Security
- Payment security: anti-self-pay + recipient verification (prior work)
- Production hardening: crash safety, security, observability (prior work)

## [0.x] — pre-release

Development history prior to v1.0.0 is recorded in the git log. Notable milestones:

- **Phase 4D** — legacy plaintext mesh endpoints removed; server speaks vault only
- **Phase 6.5 / 7 / 8** — multi-signer classification, transport abstraction, trust page, 16 security fixes
- **Phase 5 / 6** — AndroidKeyStore integration, bedtime mode, screen time, ntfy push, QR web login
- **Multi-tenant isolation** — operator mesh scoping for hosted relay deployments
- **Vault design** — E2E encryption (AES-256-GCM + RSA-OAEP), RSA-signed orders, zero-knowledge relay

See `git log` for full commit history since `0de5fd9` (initial public repo push, 2026-04-09).

[Unreleased]: https://github.com/OWNER/REPO/compare/main...HEAD

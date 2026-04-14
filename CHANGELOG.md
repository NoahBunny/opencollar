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

### Changed
- `focuslock-mail.py` — default `Host` header fallback changed from operator's personal domain to `localhost`
- `android/companion/.../MainActivity.java` — server URL input hint changed from operator's personal domain to a generic example
- Python codebase reformatted with `ruff format` (Phase 1a — mechanical, skipped in `git blame` via `.git-blame-ignore-revs`)
- Security-critical exception handlers now use structured logging at appropriate severity (Phase 1b-core):
  - Vault signature-verify and decrypt failures → `logger.warning`
  - Payment email parse + IMAP loop errors → `logger.warning`/`logger.error`
  - Mesh signature verify + state I/O (orders, peers, vouchers) → `logger.warning`
  - Roadmap-called-out `[warn]` prints in `focuslock-mail.py` (paywall parse, ntfy push, pairing registry) → logger

### Fixed
- **Real bugs surfaced by lint (Phase 1a):**
  - `focuslock-desktop-win.py` was missing `import subprocess` (5 crash sites: bedtime check + 4 process-management paths) and `import datetime` (1 bedtime check site)
  - `focuslock-mail.py:806` called bare `push_to_peers(...)` instead of `mesh.push_to_peers(...)` — fine-application mesh push was broken
- `set-payment-email` feature completed with missing `ORDER_KEYS` schema entries and `focuslock_payment.py` consumer (hot-swappable IMAP creds via Lion's Share app)

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

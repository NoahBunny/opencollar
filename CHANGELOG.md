# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
starting with v1.0.0.

## [Unreleased]

### Added
- `SECURITY.md` — vulnerability disclosure policy
- `CODE_OF_CONDUCT.md` — Contributor Covenant 2.1
- `.editorconfig` — shared editor conventions
- `CHANGELOG.md` — this file
- `docs/PUBLISHABLE-ROADMAP.md` — phased plan to v1.0.0

### Changed
- `focuslock-mail.py` — default `Host` header fallback changed from operator's personal domain to `localhost`
- `android/companion/.../MainActivity.java` — server URL input hint changed from operator's personal domain to a generic example

### Security
- Payment security: anti-self-pay + recipient verification
- Production hardening: crash safety, security, observability

## [0.x] — pre-release

Development history prior to v1.0.0 is recorded in the git log. Notable milestones:

- **Phase 4D** — legacy plaintext mesh endpoints removed; server speaks vault only
- **Phase 6.5 / 7 / 8** — multi-signer classification, transport abstraction, trust page, 16 security fixes
- **Phase 5 / 6** — AndroidKeyStore integration, bedtime mode, screen time, ntfy push, QR web login
- **Multi-tenant isolation** — operator mesh scoping for hosted relay deployments
- **Vault design** — E2E encryption (AES-256-GCM + RSA-OAEP), RSA-signed orders, zero-knowledge relay

See `git log` for full commit history since `0de5fd9` (initial public repo push, 2026-04-09).

[Unreleased]: https://github.com/OWNER/REPO/compare/main...HEAD

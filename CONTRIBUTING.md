# Contributing

Thanks for your interest. A few ground rules before you open an issue or PR.

---

## Before you start

- **Read [`DISCLAIMER.md`](DISCLAIMER.md) and [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md) first.** This project handles consent, enforcement, and real money. Contributors who don't take that framing seriously will have their PRs closed.
- **Security-sensitive change?** Don't open a public issue. Use the process in [`SECURITY.md`](SECURITY.md).
- **New feature?** Open an issue with the `feature` template *before* writing code. For anything touching crypto, enforcement, payment parsing, or consent flow, expect design discussion before implementation.

## What we accept

| PRs welcome | Discuss first | Politely declined |
|-------------|---------------|-------------------|
| Bug fixes with tests | New lock modes | Features to weaken consent/safety (Release Forever, factory reset, escape penalties) |
| Documentation improvements | New bank fingerprints in `shared/banks.json` | Anything that makes the tool usable without the consent screen |
| Test coverage additions | New platform ports | Anti-forensic / stealth install modes |
| Lint / format cleanups | UI reworks | Features designed to deceive the device owner |
| Dependency bumps | New enforcement integrations (Lovense, etc.) | Removing the 150-escape factory-reset safety valve |

## Process

1. **Fork** the repo, create a topic branch from `main`.
2. **Install dev deps**: `pip install -e '.[dev]'` — gives you ruff, mypy, pytest.
3. **Run locally before pushing**: `ruff check . && ruff format --check . && mypy shared && pytest tests/`. CI runs the same checks.
4. **Write tests** for any code change in `shared/` or the enforcement path. Coverage is currently 78% on `shared/`; we don't accept drops.
5. **Keep commits focused.** One logical change per commit. Conventional subject lines preferred (`fix:`, `feat:`, `docs:`, `refactor:`, `test:`, `chore:`).
6. **Open a PR** against `main`. Fill out the PR template honestly. Link the issue you're closing.

## AI-assisted contributions

This is a consent-heavy project and AI assistants do not understand consent frameworks the way humans do.

- You may use AI coding assistants, but **you are responsible for every line you submit**.
- **Disclose AI use in the PR description.** "I used Claude/Copilot/Cursor for X" is fine. Undisclosed AI-generated PRs that turn out to be slop will be closed without review.
- Do not paste output from an AI directly into a crypto, payment, or enforcement-path change. Review, understand, test.

## Review gate for enforcement-sensitive code

PRs that touch any of the following paths require **explicit approval from the maintainer** before merge. CI-green is necessary but not sufficient — the `CODEOWNERS` file automatically requests maintainer review, and the branch protection rule on `main` blocks merge until it arrives.

- `shared/focuslock_vault.py`, `shared/focuslock_mesh.py`, `focuslock_mesh.py`, `focuslock_ntfy.py` — crypto + mesh protocol
- `shared/focuslock_payment.py`, `shared/banks.json` — payment parsing (moves real money)
- `shared/focuslock_penalties.py` — paywall + enforcement math
- `shared/focuslock_config.py`, `shared/focuslock_sync.py` — config + trust-boundary validation
- `focuslock-mail.py` — relay, admin API, pairing registry
- `focuslock-desktop.py`, `focuslock-desktop-win.py`, `watchdog-win.pyw`, `build-win.py` — desktop collars
- `android/{slave,controller,companion}/` — device admin, enforcement, HTTP signature gate
- `installers/` — sudoers rules, systemd units, privilege boundaries
- `.github/workflows/`, `.github/dependabot.yml`, `.github/CODEOWNERS` — CI + supply-chain surface

Expect review turnaround in days, not hours. Complex changes may go through multiple review rounds; plan for that rather than expecting a first-pass merge. See [`CODEOWNERS`](.github/CODEOWNERS) for the full path-to-owner mapping.

## Commit signing

Commits on the above paths **must** be GPG or SSH signed (`git commit -S`). The [`signed-commits` CI check](.github/workflows/signed-commits.yml) runs on every PR and fails if any commit in the PR range modifies a sensitive path without a valid signature.

- **First-time setup:** `git config --global commit.gpgsign true` + `git config --global user.signingkey <KEY>` (GPG) or `git config --global gpg.format ssh` + `git config --global user.signingkey <path-to-ssh-pub>` (SSH). Upload the corresponding public key to your GitHub account's "SSH and GPG keys" settings so the signature shows as "Verified" in the commit view.
- **Why:** the crypto + enforcement surface is the adversary's lever for turning this tool against its users. A signed commit is a committed-to identity, which makes supply-chain tampering (e.g., a compromised contributor account pushing an unreviewed change) materially harder.
- **Unsigned commits on non-sensitive paths** (docs, tests, CHANGELOG) are accepted. The CI check is path-scoped precisely so that drive-by documentation improvements don't trip the signing requirement.

To retroactively sign a commit you've already made, rebase it: `git rebase --exec 'git commit --amend --no-edit -S' <upstream>..HEAD`.

## Android specifics

Android modules have no Gradle — builds go through `aapt2 → javac → d8 → apksigner`. If you're used to Gradle + AGP + `gradlew`, the `android/*/build.sh` scripts are what you run instead. See [`docs/BUILD.md`](docs/BUILD.md).

Don't add a `build.gradle` in a drive-by PR. Gradle migration is Phase 8 in the roadmap and is a deliberate decision, not a drive-by cleanup.

## Getting unstuck

- Architecture and sequence diagrams: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)
- Crypto design: [`docs/VAULT-DESIGN.md`](docs/VAULT-DESIGN.md)
- Config field reference: [`docs/CONFIG.md`](docs/CONFIG.md)
- Self-hosting walkthrough: [`docs/SELF-HOSTING.md`](docs/SELF-HOSTING.md)
- Build guide: [`docs/BUILD.md`](docs/BUILD.md)

For questions that don't fit the issue tracker, open a GitHub Discussion (once they're enabled).

## License

By contributing, you agree that your contributions will be licensed under GPL-3.0-or-later.

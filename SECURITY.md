# Security Policy

## Reporting a vulnerability

Please report security vulnerabilities **privately** — do not open a public issue.

**Contact:** open a GitHub private vulnerability report via the Security tab of this repository, or email the maintainer listed in the repository metadata.

Please include:
- The component affected (The Collar / Lion's Share / Bunny Tasker / desktop collar / mail relay / mesh protocol)
- Version(s) affected (git SHA or release tag)
- Reproduction steps, including minimum viable proof-of-concept if possible
- Your assessment of impact (what can an attacker do?)

## Scope

### In scope
- The three Android apps (`android/slave/`, `android/companion/`, `android/controller/`)
- Desktop collars (`focuslock-desktop.py`, `focuslock-desktop-win.py`, `watchdog-win.pyw`)
- Mail relay (`focuslock-mail.py`)
- Shared modules (`shared/focuslock_vault.py`, `shared/focuslock_payment.py`, `shared/focuslock_config.py`, `shared/focuslock_sync.py`)
- Mesh protocol (`focuslock_mesh.py`)
- Installer scripts (`installers/`)

### Out of scope
- Any specific hosted relay deployment (personal infrastructure — report directly to its operator)
- Third-party dependencies (report upstream; we will track CVEs and bump)
- Social-engineering the power dynamic itself (that is between participants)
- Denial-of-service via resource exhaustion on self-hosted deployments where the operator has sized their server inappropriately
- Issues requiring physical access to an unlocked device logged in as the Lion

## Severity guidelines

**Critical** — remote code execution, vault key extraction, paywall bypass without Lion consent, unauthorized unlock without Lion signature, mesh impersonation without private key
**High** — privilege escalation, cross-mesh data leak, signature verification bypass, RSA/AES misuse
**Medium** — information disclosure of non-sensitive data, DoS against a specific mesh, client-side crash exploitable remotely
**Low** — UX issues with security implications, missing hardening, timing attacks on non-secret data

## Response timeline

- **Acknowledgment:** within 5 business days of report
- **Initial assessment:** within 14 days
- **Patch target:** 90 days for High/Critical; 180 days for Medium; best-effort for Low
- **Coordinated disclosure:** 90 days after initial report, or on patch release, whichever comes first

If a report is critical and under active exploitation, expedited timelines apply. Reporters are credited in the release notes unless they request otherwise.

## What is not a vulnerability

This is a **consensual** device-restriction tool. The following are intended behavior:
- Device admin privileges preventing app uninstall
- Screen lock persistence
- Payment detection via email scanning (with user's own Gmail credentials)
- Compound-interest paywall accrual
- Factory reset at 150 escape attempts
- SMS trigger commands
- Geofence-based auto-lock

These features are documented in `DISCLAIMER.md` and the Terms of Surrender consent screen. Reports claiming these are vulnerabilities will be closed as intended behavior.

## No bug bounty

We do not offer monetary rewards. Credit in release notes is available on request.

## Safe harbor

Good-faith security research is welcomed. We will not pursue legal action against researchers who:
- Report vulnerabilities privately per this policy
- Avoid accessing data belonging to other users
- Do not disrupt production systems (test against self-hosted instances)
- Give us reasonable time to patch before public disclosure

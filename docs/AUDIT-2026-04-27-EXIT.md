# Stream A Exit — 2026-04-27 Audit Closeout

**Status:** All Highs and Mediums fixed-and-committed. Lows tracked in roadmap.
**Plan reference:** `docs/AUDIT-PLAN.md` (Stream A).
**Findings reference:** `docs/AUDIT-FINDINGS-2026-04-27.md`.
**Total commits:** 5 (`09d73be`, `44c5fe4`, `4db89e2`, `ac16335`, `1c2c0aa`).
**Tests added:** 102 (1014 → 1080 passing, includes the 16-case
parametrized validator matrix relocated from round-1 to round-3).
**APK rollouts required:** slave 74 → 75 (8.31 → 8.32), companion
56 → 57 (2.23 → 2.24); desktop collar (Linux) needs redeploy via
`installers/re-enslave-desktops.sh`.
**Out-of-repo operator action required:** update `sync-standing-orders.sh`
on the homelab to send `Authorization: Bearer ${ADMIN_TOKEN}` on its
`/standing-orders`, `/settings`, `/memory` GETs before deploying the
new relay. Single-line change per endpoint.

---

## What was reviewed

The Stream A inventory walk produced `docs/AUDIT-FINDINGS-2026-04-27.md`
covering:

- **Relay (`focuslock-mail.py`, port 8434)** — 41 routes, every state-
  mutating handler, every shared singleton.
- **Slave (`ControlService.java`, ports 8432/8433)** — all
  SigVerifier-gated routes plus the four exempt routes (`/api/ping`,
  `/api/status`, `/api/adb-port`, `/api/pair`).
- **Desktop collars (`focuslock-desktop.py` / `-win.py`, port 8435)** —
  `/mesh/*` (PIN+RSA-signed via `mesh.handle_mesh_*`), `/api/pair/*`,
  `/`, `/index.html`.
- **Crypto** — RSA key sizes, PKCS1v15 / OAEP usage, AES-GCM nonce
  handling, pubkey loading paths (PEM vs DER-b64), canonicalization,
  the audit-C1 envelope shape.
- **Multi-tenant scoping** — re-audit of the 7 findings closed
  2026-04-24. No regressions.
- **Replay protection** — slave nonce cache, ts windows, server-side
  status (M-5 tracked).
- **Input validation** — `_safe_mesh_id`, file paths, IMAP creds,
  action names.
- **Log injection** — manual walk found 19 sites CodeQL hadn't flagged
  (M-8, all wrapped in `_sanitize_log()` round-1).
- **Vault invariants** — `VaultStore.append` monotonicity, `gc`
  retention, `_verify_blob_two_writer` Lion/node gate.
- **Slave HTTP exempt list** — confirmed read-only / bootstrap routes
  only; L-2 (lat/lon in `/api/status`) noted but not fixed.
- **Manifests** — Lion's Share controller had `MANAGE_EXTERNAL_STORAGE`
  with no usage (M-7, dropped round-1).
- **Dependency surface** — `cryptography>=42`, no new CVEs.
- **CodeQL + Scorecard** — both green pre-audit (L-6 transient flagged).
- **Sigstore attestation** — confirmed present on v1.2.0; verify
  command failed on flag syntax (L-7, operator action).

---

## Findings closed

| ID  | Severity | Title                                                                            | Commit      |
| --- | -------- | -------------------------------------------------------------------------------- | ----------- |
| H-1 | High     | `/enforcement-orders`, `/memory`, `/standing-orders`, `/settings` admin-gated   | `09d73be` (partial) + `1c2c0aa` (remainder) |
| H-2 | High     | Seven evidence webhooks slave-signed (compliment, gratitude, love_letter, offer, geofence-breach, evidence-photo, subscription-charge) | `44c5fe4` |
| M-1 | Medium   | `/webhook/desktop-heartbeat` vault-node-signed                                  | `1c2c0aa`   |
| M-2 | Medium   | `/webhook/controller-register` admin-token-gated                                | `4db89e2`   |
| M-3 | Medium   | `/webhook/register` device_id validated + bunny-signed                          | `09d73be` (validation) + `ac16335` (sig) |
| M-4 | Medium   | `/webhook/verify-photo` + `/webhook/generate-task` bunny-signed                 | `4db89e2` (generate-task) + `ac16335` (verify-photo) |
| M-6 | Medium   | Desktop `/api/pair/create` admin-token-gated (Linux + Windows)                  | `4db89e2`   |
| M-7 | Medium   | Lion's Share controller manifest dropped `MANAGE_EXTERNAL_STORAGE`              | `09d73be`   |
| M-8 | Medium   | 19 logger sites wrapped in `_sanitize_log()`                                    | `09d73be`   |
| L-3 | Low      | `OPERATOR_MESH_ID` validated by `_safe_mesh_id_static` at startup               | `09d73be`   |

**Acceptance gate per `docs/AUDIT-PLAN.md`:** every Critical and High
fixed-and-committed (✅ — 0 Criticals, 2 Highs both closed); Mediums
fixed or tracked (✅ — 7 fixed, M-5 tracked); Lows fixed or tracked
(✅ — L-3 fixed, L-1/L-2/L-4/L-6/L-7 tracked).

---

## Findings tracked (deferred)

| ID  | Title                                                                            | Reason for deferral                                                                                                                                                                                  |
| --- | -------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| M-5 | Relay-side nonce cache for bunny-signed `/api/mesh/{id}/*` and `/webhook/bunny-message` | v1.1 hardening. Requires coordinated client + server change; the slave's `SigVerifier.NonceCache` (Java, 4096 LRU + 600 s TTL) covers slave-side; the relay needs a Python equivalent + signers must include a `nonce` field. Threat is replay within the existing ±5 min ts window. |
| L-1 | `/admin/status` accepts `admin_token` as URL query parameter                    | Reverse-proxy access logs leak the token even though the relay's own log redacts it via `log_message` override. Header-only path exists; deprecation requires migrating any operator scripts that pass via query. Schedule for next major version.   |
| L-2 | Slave's `/api/status` (sig-exempt) exposes lat/lon to LAN callers                | Inherent to the LAN-gossip design; the slave's status response is broadcast to anyone the Bunny shares LAN with. Stripping lat/lon would break the in-app location-pin status display the Lion uses. Roadmap for `/api/location` signed-only redesign. |
| L-4 | Cleartext-traffic-permitted unconditional in all three Android apps             | Documented as known weakness in `docs/THREAT-MODEL.md`. LAN gossip is HTTP-only by design; tightening would require domain-config allowlist for LAN ranges + homelab IP, with HTTPS for the public relay URL. Worth doing at next minor version.    |
| L-6 | Scorecard CI run 25008444226 transient GraphQL infra error                      | GitHub-side, not a Scorecard regression. Two prior runs the same day succeeded. Re-run on next push.                                                                                                |
| L-7 | `gh attestation verify` failed during this audit on flag syntax                 | Operator should re-run with the documented v1.2.0 invocation: `gh attestation verify <asset> --owner NoahBunny --repo opencollar`. Sigstore artifacts confirmed present on the release.            |

---

## Coordinated rollout summary

The closeout commits cross APK + relay + desktop-collar + installer
boundaries. Rollout order matters; doing them out of order produces
403 / version-mismatch errors during the gap.

1. **Build slave APK v75** (`com.focuslock` 8.32) and companion APK v57
   (`com.bunnytasker` 2.24). Sideload to all paired phones.
2. **Update sync-standing-orders.sh on the homelab** (out-of-repo, lives
   on the operator's homelab and is distributed via
   `installers/install-standing-orders.sh:66` scp). Add
   `Authorization: Bearer ${ADMIN_TOKEN}` to its `/standing-orders`,
   `/settings`, and `/memory` GETs.
3. **Deploy the new relay** via `installers/re-enslave-server.sh`.
4. **Redeploy desktop collars** via `installers/re-enslave-desktops.sh`
   so the new `phone_home` signing block + `sync_standing_orders` Bearer
   header land in production.
5. **Verify on the operator's Pixel 10** that `/webhook/verify-photo`,
   `/webhook/register`, evidence webhooks (compliment et al.), and the
   slave's photo-task flow all still succeed against the new relay.

If the homelab `sync-standing-orders.sh` isn't updated, the systemd
timer's 5-min sync starts logging 403s — the cure is the single-line
header add, no code change.

---

## What passed (no findings)

Documented in `docs/AUDIT-FINDINGS-2026-04-27.md` §"What passed" — crypto
primitives, canonical JSON parity across all four implementations
(verifier + slave/controller/companion signers), multi-tenant scoping
(no 2026-04-24 regressions), path traversal gates, vault invariants,
side channels (`hmac.compare_digest`), token lifecycles, slave
exempt-list design, slave nonce cache, replay protection on Lion-signed
slave POSTs, multi-tenant message store + payment ledger + desktop
registry isolation. CodeQL latest-on-`main` (run 25008444246) green.

---

## Next concern

Per `docs/AUDIT-PLAN.md` recommended ordering A → C → B, the next
milestone is **Stream C — QA infrastructure expansion** (~16–20 hours):

- Android UI automation second swing (Appium spike, given the
  uiautomator2 wedge from 2026-04-23).
- IMAP scanner end-to-end against a real test inbox (operator-side
  setup gating).
- `focuslock-mail.py` HTTP-route coverage — pair handlers, admin
  handlers, vault `/since/{v}` poll, `register-node-request`, memory
  bundle, `do_GET` dispatch (~1100 uncovered lines).
- Manual QA driver scripts hardened (`make qa` style entry point).
- Regression matrix automation (`docs/QA-CHECKLIST.md` 14-section human
  matrix → programmatic).
- Performance smoke test (rapid `/admin/order` load).

Followed by **Stream B — Usability review** (~24–32 hours).

The M-5 relay nonce cache is best landed alongside the next companion +
slave APK signing surface change so the `nonce` field add is one
coordinated rollout, not two.

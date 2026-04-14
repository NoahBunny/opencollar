# Threat Model

What this software defends against, what it doesn't, and what's out of scope.

For the deep cryptographic threat table see `docs/VAULT-DESIGN.md §2`. For the consent and harm-reduction framing see `DISCLAIMER.md`. This document orients you to both.

---

## In scope

The system is designed to keep working even when:

| Adversary | Defense |
|-----------|---------|
| **Public relay operator** | Vault encryption — server stores ciphertext only; cannot read orders, paywall, messages. No Lion privkey on server (P6.5). |
| **Self-hosted relay operator** running other tenants | `operator_mesh_id` scopes the admin API to a single mesh. Cross-mesh tampering blocked. |
| **Compromised bunny node** | Cannot forge orders to other nodes (no Lion privkey). Cannot promote itself to controller. Cannot take over another node's identity (key rotation requires Lion approval). |
| **LAN peer without credentials** | Mesh gossip is PIN-authed and RSA-signed. Unauthed POSTs to `/mesh/order` rejected. |
| **Network attacker** | TLS at the transport layer + vault layer below it (defense in depth). |
| **Browser XSS / clickjacking against web UI** | Mesh-controlled strings HTML-escaped, `X-Frame-Options: DENY`, CORS scoped out of `/admin/*`. |
| **Stolen admin web session token** | Scoped session tokens with 8h TTL. Master `ADMIN_TOKEN` never reaches the browser. New approvals require fresh Lion RSA signature. |
| **Lost Lion phone** | Recovery via Release Forever or factory reset — same paths as a deliberate teardown. |
| **Self-pay attempts by bunny** | Anti-self-pay logic + recipient verification on payment emails. |
| **Tampered payment email** | RSA payment notifications verified against the bank's keyword fingerprint and amount sanity range. |

---

## Out of scope

These are deliberate non-goals or deferred to future work.

| Threat | Why out of scope |
|--------|------------------|
| Forward secrecy / per-message keys | Deferred to v2 (Double Ratchet over the same vault transport). v1 uses RSA-OAEP key wrap per blob. |
| Hiding `mesh_id` from the server | Would require Tor / mix-net routing; future work. |
| Anti-traffic-analysis padding | Blob sizes are observable. Deferred. |
| Coercion of either participant | This is a power-exchange tool. It cannot tell consent from coercion at the technical layer. **Read `DISCLAIMER.md`.** |
| Physical access to an unlocked bunny phone | If bunny willingly hands over their phone, they can disable the cage — that's a consent boundary, not a vulnerability. |
| Compromised host OS | Root malware on Linux or unflashed BIOS-level malware on Windows is out of scope. |
| Side-channel timing attacks against the relay | Not a goal of v1. |
| State-level adversaries with rubber-hose access | This is consent tech, not opsec tooling. |

---

## Trust tiers

The system runs in two distinct trust modes. Choose deliberately.

### Public relay (zero-knowledge)

- Operator runs `focuslock-mail.py` with **no `operator_mesh_id`**.
- Admin API is disabled — relay cannot drive enforcement.
- Relay sees: `mesh_id`, version numbers, blob sizes, timing, registration metadata.
- Relay cannot: read orders, forge orders (no Lion privkey), promote nodes.
- Use this when bunny and lion don't trust the relay operator at all.

### Self-hosted (operator IS the lion)

- Operator runs `focuslock-mail.py` with `operator_mesh_id = <their mesh>`.
- Admin API enabled, scoped to that single mesh.
- Relay has its own keypair (`/opt/focuslock/relay_privkey.pem`) and signs admin orders.
- Trust domain: the operator IS the Lion. Compromising the relay = compromising the Lion's enforcement, but no other mesh.
- Use this when you want the full feature set (paywall, subscriptions, evidence emails).

**Mixed deployment** (one server hosts other people's vaults *and* the operator's own mesh) is supported. The `operator_mesh_id` field is the boundary.

---

## Cryptographic primitives at a glance

| Layer | Choice |
|-------|--------|
| Body encryption | AES-256-GCM (fresh key per blob) |
| Key wrapping (per recipient) | RSA-2048 OAEP-SHA256 |
| Signatures | RSA-PKCS1v15-SHA256 (PSS planned for v2) |
| Canonical encoding | `json.dumps(sort_keys=True, separators=(",",":"))` |
| Mesh ID | `base64url_unpadded(SHA256(lion_pubkey_DER))[:16]` — deterministic, no auth_token to leak |
| Pairing | QR code carries relay URL + Lion pubkey + invite code; bunny verifies `mesh_id == hash(pubkey)` on first sync |

Full design rationale: `docs/VAULT-DESIGN.md`.

---

## Known weaknesses (v1, not blocking)

These are tracked openly so contributors don't trip on them.

- **PKCS1v15 signatures** rather than RSA-PSS. Acceptable because the signed payload is already a canonical-JSON representation of an encrypted blob (no message-malleability surface), but PSS is preferred and on the v2 list.
- **No key rotation cadence** for the relay keypair. Manual rotation works (re-register the new pubkey on each node) but is operator-initiated, not automatic.
- **ntfy push notifications** leak topic existence and message timing to the ntfy operator (payload is only `{"v":N}`, but presence is visible). Self-host ntfy if this matters to you.
- **Android sideload only.** No Play Store, no F-Droid in v1. APK signature pinning is on the user. Verify `SHA256SUMS.txt` against the signed GitHub Release attestation.
- **Bunny Tasker applies mesh display keys without per-blob signature verification.** The companion consumes the legacy `/mesh/sync` path (not vault). An in-scope `MESH_DISPLAY_KEYS` allowlist (`sub_tier`, `pinned_message`, `mode`, etc.) blocks enforcement-key writes — an attacker on the mesh cannot lock the phone, clear the paywall, or modify penalties via the companion. They *can* spoof display-only state (subscription info, pinned messages, streak counts, mode name). The Collar (invisible enforcer) continues to verify every vault blob by signature; it ignores the companion's mesh path. Full signature verification in the companion is a v1.1 follow-up.
- **Cleartext traffic permitted** in all three Android manifests for LAN gossip (direct phone IPs on port 8432, homelab on port 8434). The relay URL should always be HTTPS; operator discipline enforces this. A misconfigured operator pointing `mesh_url` at an HTTP relay leaks the PIN and gossip metadata — vault content stays encrypted by the layer below.

---

## Reporting vulnerabilities

See `SECURITY.md`. **Do not** open public issues for security bugs. Disclosure SLA is documented there.

---

## Consent and harm reduction

The hardest threats this project faces are not technical. Read `DISCLAIMER.md` and the consent screen embedded in every install. The Release Forever button and 150-escape factory-reset path exist as safety valves precisely because consent can be withdrawn at any time.

If a dynamic becomes harmful, **stop using the software and seek appropriate support.** Crypto can't fix that. Neither can we.

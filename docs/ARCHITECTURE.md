# Architecture Reference

A sanitized map of the codebase for new contributors. Sequence diagrams for the four flows that matter: lock, unlock, payment, pairing.

For the user-facing tour, start with the README. For crypto detail, see `docs/VAULT-DESIGN.md`. This document is for people about to touch the code.

---

## Components

```
┌─────────────────────────┐    ┌─────────────────────────┐    ┌──────────────────────────┐
│ Lion's Share            │    │ Relay (focuslock-mail)  │    │ The Collar (slave)       │
│  com.focusctl           │    │                         │    │  com.focuslock           │
│  Android controller     │◄──►│  Vault store + signup   │◄──►│  Invisible Android app   │
│  RSA keypair (Lion)     │    │  Admin API (operator)   │    │  Device admin + HTTP svr │
└────────────┬────────────┘    │  Mail/IMAP/Ollama opt   │    └──────────┬───────────────┘
             │                 └──────────┬──────────────┘               │
             │                            │                              │
             │                            │                              │
             │                 ┌──────────▼──────────┐    ┌──────────────▼───────────┐
             │                 │ Homelab (optional)  │    │ Bunny Tasker             │
             │                 │  ADB bridge         │    │  com.bunnytasker         │
             │                 │  Mail/Ollama        │    │  Visible companion app   │
             │                 │  Tamper detection   │    └──────────────────────────┘
             │                 └─────────────────────┘
             │
             │     Mesh gossip (P2P)
             ▼
┌────────────────────────┐
│ Desktop collars        │
│  Linux: GTK4 + systemd │
│  Win:   pystray + .exe │
│  Vault mode (E2E)      │
└────────────────────────┘
```

Every node in the mesh holds the same RSA-signed, AES-encrypted "orders" document and converges via gossip.

---

## Source layout

| Path | Component | Notes |
|------|-----------|-------|
| `android/slave/` | The Collar | Java, no Gradle. `aapt2 → javac → d8 → apksigner`. |
| `android/companion/` | Bunny Tasker | Same. |
| `android/controller/` | Lion's Share | Same. |
| `focuslock-desktop.py` | Linux desktop collar | GTK4 + systemd. Imports `shared/`. |
| `focuslock-desktop-win.py` | Windows desktop collar | pystray. Bundled by `build-win.py` into `.exe`. |
| `focuslock-mail.py` | Relay server | HTTP server (BaseHTTPRequestHandler) + IMAP + optional Ollama. |
| `focuslock_mesh.py` | Mesh protocol | Gossip, signing, peer discovery. Imported everywhere. |
| `focuslock_ntfy.py` | ntfy push wake-up | Optional latency optimization. |
| `shared/focuslock_vault.py` | Vault crypto | AES-256-GCM, RSA-OAEP, RSA-PKCS1v15. **Most security-critical module.** |
| `shared/focuslock_payment.py` | Payment email parsing | 145+ banks, anti-self-pay. |
| `shared/focuslock_config.py` | Config loader | JSON + env var overrides. |
| `shared/focuslock_http.py` | JSON response mixin | Sets `X-Frame-Options: DENY`, `nosniff`. |
| `shared/focuslock_transport.py` | Pluggable vault transport | `http` (default) or `syncthing`. |
| `shared/focuslock_sync.py` | Mesh sync helpers | Reused by desktop + server. |
| `shared/focuslock_adb.py` | ADB wrapper | Used by homelab bridge. |
| `shared/banks.json` | Bank fingerprint dictionary | 145+ banks across 21 regions. |
| `web/` | Lion's Share web UI | XSS-hardened static HTML+JS. Served by `focuslock-mail.py`. |
| `installers/` | Deployment scripts | Re-enslave server/desktops/phones; homelab setup. |
| `tests/` | Pytest suite | 266 tests, 78% coverage on `shared/`. |
| `staging/` | QA harness | Scripted Lion driver against an isolated relay. |

---

## Mesh protocol in 150 words

Every node holds the same "orders" document — a JSON dict of state (paywall, lock_active, sub_due, etc.) — versioned with a monotonic integer.

Every 10 seconds, each node:

1. Picks its peers (LAN-discovered + configured + Tailscale-resolved)
2. Sends them its current `orders_version`
3. If a peer has a higher version, downloads the encrypted vault blob, decrypts via its RSA slot, verifies the signature against the cached approved-pubkey list, and applies

Conflicts are resolved last-write-wins on `version`, but in practice writes are serialized through the Lion (controller) or the relay (operator-mode admin orders). The relay signs with its own pubkey; both are in the approved-signers list.

LAN discovery: UDP broadcast on port **21037** (deliberately not 21027 — that's Syncthing).

ntfy push (optional): when a node bumps the version, it publishes `{"v": N}` to the ntfy topic. Other nodes wake immediately rather than waiting for the gossip tick. Payload carries no order content.

---

## Sequence: Lock

```
Lion taps "Lock 30 min" in Lion's Share
        │
        ▼
Lion's Share constructs new orders dict (lock_active=true, lock_until=now+30m)
        │
        ▼
Signs orders blob with Lion's RSA privkey (PKCS1v15-SHA256)
        │
        ▼
Encrypts AES-256-GCM body, wraps the AES key once per recipient pubkey (RSA-OAEP)
        │
        ▼
POST /vault/{mesh_id}/blob to relay
        │
        ▼
Relay verifies signature against approved-signers cache, stores blob, increments version
        │
        ▼
Relay publishes {"v": N+1} to ntfy topic (if enabled)
        │
        ├──► Bunny phone (The Collar) wakes, fetches blob, decrypts, applies → device admin invokes lockNow()
        │
        ├──► Bunny phone (Bunny Tasker) wakes, fetches blob, decrypts, updates UI
        │
        └──► Desktop collars (Linux/Win) wake, fetch blob, decrypt, invoke loginctl lock-session / LockWorkStation
```

Same path, in reverse, for unlock — except the relay can independently `add-paywall` via the admin API on the operator mesh.

---

## Sequence: Payment

```
Bunny pays via banking app
        │
        ▼
Bank sends confirmation email
        │
        ▼
focuslock-mail.py IMAP poller (every poll_interval)
        │
        ▼
focuslock_payment.py.parse_payment_email
  ├── Match against banks.json fingerprint
  ├── Extract amount, recipient
  ├── Anti-self-pay check
  └── Sanity range check (min_payment ≤ amt ≤ max_payment)
        │
        ▼
If valid: subtract from paywall via admin order
        │
        ▼
New orders blob signed by relay → propagates over mesh
        │
        ▼
Bunny's phone + desktop collars see paywall=0 → unlock allowed
        │
        ▼
Evidence email sent to lion (partner_email)
```

---

## Sequence: Pairing (QR)

```
Lion (in Lion's Share)
  ├── Generate (or load) RSA keypair
  ├── Construct QR payload: { mesh_url, lion_pubkey_pem, invite_code }
  └── Display QR
        │
        ▼
Bunny (in Bunny Tasker)
  ├── Scan QR
  ├── Compute mesh_id = base64url(SHA256(lion_pubkey_DER))[:16]
  ├── Generate own RSA keypair
  └── POST /vault/{mesh_id}/register-node-request { node_id, node_pubkey, invite_code }
        │
        ▼
Relay
  ├── Validate invite_code (one-shot, 24h TTL)
  ├── Mark request as pending
  └── Notify Lion via push (ntfy)
        │
        ▼
Lion approves in Lion's Share
  └── POST /vault/{mesh_id}/approve-node-request signed by Lion privkey
        │
        ▼
Relay marks node as approved → bunny's first vault blob arrives → mesh is live
```

---

## Sequence: Subscription auto-charge

Operator mode only.

```
focuslock-mail.py daily check
        │
        ▼
For each mesh: if sub_due is in the past → trigger charge
        │
        ▼
admin order: add-paywall amount=<weekly_rate> + extend sub_due += 7d
        │
        ▼
Mesh propagates → Bunny gets the paywall bump → must pay or escalate
```

`sub_due` is always set to `now + 7d` on every charge — pre-paying does not extend the cycle. (See `project_sub_due_cap.md` in maintainer notes.)

---

## Where the security-critical code lives

| Module | What to be careful about |
|--------|--------------------------|
| `shared/focuslock_vault.py` | Crypto correctness. Test coverage 100%. Don't change without round-trip tests. |
| `shared/focuslock_payment.py` | Anti-self-pay logic. Bank fingerprint matching. Coverage 92%. |
| `focuslock_mesh.py` | Signature verification, peer trust list. Coverage 70%. |
| `focuslock-mail.py` `WebhookHandler` | Admin API auth, vault blob signature verification. |

When in doubt, write a test before touching these.

---

## Build and CI

See `docs/BUILD.md` for build commands. CI workflows in `.github/workflows/`:

| Workflow | Trigger | Outputs |
|----------|---------|---------|
| `ci.yml` | push, PR | Lint, tests across Python 3.10/3.11/3.12, debug APKs, debug Windows EXEs |
| `release.yml` | tag `v*` | Release-signed APKs (if keystore secret set), Windows EXEs, `SHA256SUMS.txt`, GitHub Release |

---

## Onboarding checklist for new contributors

1. Read this file end-to-end.
2. Read `docs/VAULT-DESIGN.md §1–4` for crypto context.
3. Skim `shared/focuslock_vault.py` and run `pytest tests/test_vault.py`.
4. Build all three APKs once (`cd android/{slave,companion,controller} && bash build.sh`) to verify your toolchain.
5. Spin up the staging mesh per `docs/STAGING.md` and run `python staging/qa_runner.py` against it.

If all five pass, you can read any open issue in context.

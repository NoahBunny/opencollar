# The Collar + Lion's Share + Bunny Tasker

A multi-platform ecosystem for consensual remote device restriction. The Lion controls. The Bunny obeys. The Collar enforces.

## Architecture

```
Lion's Device               Relay Server                    Bunny's Phone (Android)
+------------------+       +----------------------+       +-------------------------+
| Lion's Share     |       | focuslock-mail.py    |       | The Collar (invisible)  |
|  (app or web UI) |       |                      |       |  - HTTP server          |
|                  |       |  Vault Store          |       |  - Device admin         |
| Control tab:     |       |   AES-256-GCM blobs  |       |  - Payment listener     |
|  Lock/Unlock     |       |   RSA-OAEP key wrap  |       |  - SMS receiver         |
|  Timer/Paywall   |       |   Can't read content  |       |  - Lovense bridge       |
|  Quick locks     |       |                      |       |  - Camera2 selfie       |
|                  |       |  Admin API (operator) |       |  - Geofence monitor     |
| Advanced tab:    |       |  Signup (/signup)     |       |                         |
|  9 lock modes    |       |  Multi-tenant         |       | Bunny Tasker (visible)  |
|  Modifiers       |       |                      |       |  - Stats dashboard      |
|  Photo tasks     |       +----------------------+       |  - Subscriptions        |
|  Lovense control |                                       |  - Self-lock            |
|  Geofence        |       Homelab (optional)              |  - Messaging            |
|                  |       +----------------------+       |  - QR pairing           |
| Inbox tab:       |       | Bridge (ADB)         |       |  - Balance notification |
|  Messages        |       |  - Jail enforcement   |       |  - Pay button           |
|  Device cards    |       |  - Volume enforcement |       +-------------------------+
|  Notifications   |       |  - Tamper detection   |
+------------------+       | Mail Service          |       Desktop Collar
                           |  - IMAP payment check |       +---------------------------+
   P2P Vault Mesh          |  - Evidence emails    |       | System tray crown icon    |
   RSA-signed orders       |  - Photo verification |       | Vault mode E2E encrypted  |
   E2E encrypted           |  - Ollama LLM eval    |       | Session lock enforcement  |
   Zero-knowledge relay    +----------------------+       | Mesh node (gossip P2P)    |
                                                           +---------------------------+
```

## Zero-Knowledge Vault (Phase D)

All order content is **end-to-end encrypted**. The relay server stores opaque ciphertext blobs and verifies RSA signatures but **cannot read** lock commands, paywall amounts, messages, geofence coordinates, or any other order content.

| What the server sees | What the server can't see |
|---------------------|--------------------------|
| mesh_id, version numbers | Lock/unlock commands |
| Blob sizes, timing | Paywall amounts |
| Node IDs, slot counts | Messages, task text |
| RSA signatures (verified, not decrypted) | Geofence coordinates |
| Registration metadata | Subscription details |

### Cryptography

| Primitive | Choice |
|-----------|--------|
| Body encryption | AES-256-GCM (fresh key per blob) |
| Key wrapping | RSA-2048 OAEP-SHA256 (per recipient) |
| Signatures | RSA-PKCS1v15-SHA256 |
| Canonical encoding | JSON sort_keys, no whitespace, ensure_ascii |

Legacy plaintext endpoints (`/mesh/sync`, `/mesh/order`, `/mesh/status`, `/desktop-status`) have been **removed**. The server only speaks vault. See `docs/VAULT-DESIGN.md` for the full threat model.

## Apps

| App | Package | Runs On | Visible |
|-----|---------|---------|---------|
| **The Collar** | com.focuslock | Bunny's phone | No -- invisible, no launcher icon |
| **Bunny Tasker** | com.bunnytasker | Bunny's phone | Yes -- companion with stats |
| **Lion's Share** | com.focusctl | Lion's phone | Yes -- gold-themed controller |

## Setup Options

When setting up a new mesh, the Lion chooses how orders are relayed:

| Option | What it is | Privacy | Cost |
|--------|-----------|---------|------|
| **Hosted relay** | Orders encrypted, stored on a shared server | Server can't read content | Free (community server) |
| **Self-host** | Your own relay on a VPS or home server | You control everything | $5-10/mo VPS or free on home hardware |
| **P2P (Tailscale)** | Direct phone-to-phone, no relay | No third party at all | Free (Tailscale free tier) |

### Hosted Relay Signup

Visit `/signup` on the relay server. Paste your Lion public key, get an invite code. Tell your Bunny the invite code and the relay URL. Done.

- Rate limited: 3 meshes per hour per IP
- Invite codes expire after 24 hours (one-time use)
- Per-mesh vault quotas: 100MB storage default
- Per-mesh order isolation: your orders are separate from everyone else's

### Self-Hosted Homelab (optional, adds features)

The homelab adds enforcement depth that the relay alone can't provide:

- **ADB Bridge**: Jail re-engagement on reboot, volume enforcement, launcher lockdown, tamper detection
- **Mail Service**: IMAP payment detection (145+ banks, 21 regions), evidence emails
- **LLM Integration**: Photo task verification (Ollama minicpm-v), task generation
- **Subscription auto-charge**: Weekly billing with overdue enforcement

## Mesh Enforcement

All devices form a P2P enforcement mesh. Orders are RSA-signed, version-numbered, and vault-encrypted. Any node can propagate orders to any other -- no single point of failure.

- **Phone <-> Desktop <-> Relay** -- all mesh peers
- **HTTPS-first sync** with LAN/Tailscale fallback
- **Homelab is optional** -- mesh works purely P2P between phone and desktops
- **ntfy push notifications** -- instant order delivery via ntfy.sh (zero-knowledge: payload is just a version number)

### Desktop Collar (Windows & Linux)

- **Windows**: Self-installing exe -- consent -> UAC -> fully enslaved
- **Linux**: systemd service + GTK4 tray icon
- **Vault mode**: Keypair generation, node registration, E2E encrypted vault poll
- Gold/gray crown tray icon (gold = connected)
- Session lock enforcement via `LockWorkStation` / `loginctl`

## Features

### Lock Modes
Basic, Negotiation, Task, Compliment, Gratitude Journal, Exercise, Love Letter, Photo Task, Random

### Enforcement
- Paywall with compound interest (10%/hr, reduced by subscription tier)
- Tiered escape penalties ($5/$10/$15+ per attempt, stacking)
- Admin tamper: +$500 attempt, +$1000 removal (stacking)
- Public shame notification after 5 escapes
- Lovense integration (escape buzz, lock pulse, task reward)
- Geofence auto-lock ($100 paywall on breach)
- SMS trigger: "sit-boy [mins] [$amount]"
- Camera2 silent selfie on task completion

### Photo Tasks (LLM-verified)
- Lion assigns or LLM generates tasks
- Bunny takes live photo with camera preview
- Photo verified by Ollama (minicpm-v vision model)
- Pass = unlock. Fail = "Try again."

### Subscriptions

| Tier | Cost | Perks |
|------|------|-------|
| Bronze | $25/wk | Stats + messaging |
| Silver | $35/wk | Reduced interest (5%/hr) |
| Gold | $50/wk | No interest + 1 free unlock/month |

### Payment Detection
145+ banks across 21 regions (full list in `shared/banks.json`). Multi-language keyword matching.

### Safety
- Terms of Surrender consent screen on first install
- Release Forever button (Lion only) -- full teardown + auto-uninstall
- Factory reset at 150 escapes
- System is consensual. Power dynamic is not.

## Files

### Android (`android/`)
- `slave/` -- The Collar (bunny's phone, invisible)
- `controller/` -- Lion's Share (controller phone)
- `companion/` -- Bunny Tasker (bunny's phone, visible companion)

### Server
- `focuslock-mail.py` -- Vault relay + webhook + mail service
- `focuslock_mesh.py` -- Shared mesh gossip protocol
- `focuslock_ntfy.py` -- ntfy push notifications (zero-knowledge)
- `focuslock-bridge.sh` -- ADB bridge enforcement (homelab)
- `web/index.html` -- Lion's Share web UI
- `web/signup.html` -- Self-service mesh signup
- `web/cost.html` -- Cost and trust transparency page
- `web/qrcode.min.js` -- QR code library for signup

### Desktop
- `focuslock-desktop.py` -- Linux collar (GTK4 + vault mode)
- `focuslock-desktop-win.py` -- Windows collar (pystray + vault mode)
- `watchdog-win.pyw` -- Windows process watchdog
- `build-win.py` -- PyInstaller build script

### Shared (`shared/`)
- `focuslock_vault.py` -- Python VaultCrypto (encrypt/decrypt/sign/verify)
- `focuslock_config.py` -- Config loader
- `focuslock_sync.py` -- Mesh sync helpers
- `banks.json` -- Payment detection keywords (145+ banks, 21 regions)

### Installers (`installers/`)
- `homelab-setup.sh` -- Full homelab deployment
- `install-desktop-collar.sh` -- Linux first-time install
- `re-enslave-server.sh` -- Server update (hash-diff, auto-restart)
- `re-enslave-desktops.sh` -- Desktop collar update
- `re-enslave-phones.sh` -- Phone APK sideload via ADB

### Docs
- `docs/VAULT-DESIGN.md` -- Cryptographic design + threat model
- `docs/MANUAL-LION.md` -- Controller manual (cheeky)
- `docs/MANUAL-BUNNY.md` -- Target manual (cheekier)
- `docs/PRICE-LIST.md` -- Penalty and pricing reference

## Build

### Android
No Gradle. Uses aapt2/javac/d8/apksigner directly.
```bash
cd android/slave && bash build.sh
# Requires: JDK 17+, Android SDK build-tools, android.jar (API 34)
```

### Windows Desktop
```bash
python build-win.py              # Builds FocusLock.exe + Watchdog
python build-win.py --skip-sign  # Skip code signing
```

### Linux Desktop
No build needed -- runs as Python script via systemd.
```bash
bash installers/install-desktop-collar.sh
```

## Audit

The relay server exposes a public `/version` endpoint:
```json
{
  "service": "focuslock-mail",
  "version": "phase-d.1",
  "source_sha256": "...",
  "git_commit": "39ac1f6...",
  "vault_mode_allowed": true,
  "vault_only_meshes": 1,
  "uptime_s": 3600
}
```

Auditors can: read source on GitHub -> compute hash -> `curl /version` -> verify match.

## License

GPL-3.0-or-later. See `LICENSE`.

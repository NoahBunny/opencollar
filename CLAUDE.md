# The Collar + Lion's Share + Bunny Tasker

Consensual remote device restriction ecosystem. Lion controls, Bunny obeys, Collar enforces.

## Architecture

- **The Collar** (`com.focuslock`) — invisible app on Bunny's phone (HTTP server, device admin, payment listener, SMS, Lovense, camera, geofence)
- **Bunny Tasker** (`com.bunnytasker`) — visible companion on Bunny's phone (stats, subscriptions, self-lock, messaging, QR pairing)
- **Lion's Share** (`com.focusctl`) — controller app on Lion's phone (lock/unlock, timer, paywall, photo tasks, Lovense, geofence, inbox)
- **Desktop Collar** — Windows (`FocusLock-Paired.exe`) and Linux (`focuslock-desktop.py`) system tray enforcement, vault mode
- **Bridge** (optional homelab) — ADB enforcement, mail service, Ollama LLM eval, subscription tracking
- **Vault Mesh** — E2E encrypted (AES-256-GCM + RSA-OAEP), RSA-signed orders, zero-knowledge relay
- **Legacy plaintext endpoints removed** — server only speaks vault

## Key Files

### Android (`android/` — no Gradle — aapt2/javac/d8/apksigner)
- `android/slave/` — The Collar
- `android/companion/` — Bunny Tasker
- `android/controller/` — Lion's Share

### Desktop
- `focuslock-desktop-win.py` — Windows collar (pystray, vault mode, session lock)
- `focuslock-desktop.py` — Linux collar (GTK4, vault mode, loginctl)
- `focuslock_mesh.py` — Shared mesh protocol
- `focuslock_ntfy.py` — ntfy push notifications (zero-knowledge wake-up signals)
- `watchdog-win.pyw` — Windows process watchdog
- `build-win.py` — PyInstaller build script for Windows exes

### Server
- `focuslock-mail.py` — Vault relay + webhook + mail + LLM
- `focuslock-bridge.sh` — ADB bridge (homelab)
- `web/index.html` — Lion's Share web UI
- `web/signup.html` — Self-service mesh creation

### Shared (`shared/`)
- `focuslock_vault.py` — Python VaultCrypto (encrypt/decrypt/sign/verify)
- `focuslock_config.py` — Config loader
- `focuslock_sync.py` — Mesh sync helpers
- `banks.json` — Payment detection keywords (145+ banks)

### Installers
- `installers/install-desktop-collar.sh` — Linux first-time install
- `installers/re-enslave-server.sh` — Server update (hash-diff, git commit transparency)
- `installers/re-enslave-desktops.sh` — Desktop collar update
- `installers/re-enslave-phones.sh` — Phone APK sideload
- `installers/homelab-setup.sh` — Homelab server setup

## Build

### Android
```bash
aapt2 compile -> aapt2 link -> javac -> d8 -> zip -> zipalign -> apksigner
# Requires: JDK 17+, Android SDK build-tools, android.jar (API 34)
```

### Windows Desktop
```bash
python build-win.py              # Builds FocusLock.exe, FocusLock-Paired.exe, FocusLock-Watchdog.exe
python build-win.py --skip-sign  # Skip code signing
```

## Enforcement

- 9 lock modes: Basic, Negotiation, Task, Compliment, Gratitude Journal, Exercise, Love Letter, Photo Task, Random
- Paywall with compound interest (10%/hr, reduced by subscription)
- Tiered escape penalties ($5/$10/$15+ stacking)
- Admin tamper: +$500 attempt, +$1000 removal (stacking)
- Lovense integration, max volume enforcement, geofence auto-lock
- SMS trigger: "sit-boy [mins] [$amount]"
- Photo tasks verified by Ollama (minicpm-v)
- 145+ banks worldwide supported for payment detection (21 regions)

## Config

Runtime config: `~/.config/focuslock/config.json` (Linux) or `%APPDATA%\focuslock\config.json` (Windows)
- `mesh_id` — mesh identifier (base64url)
- `mesh_url` — relay server URL
- `vault_mode` — `true` to enable E2E encrypted vault poll (desktop collars)
- `homelab_url` — homelab endpoint (optional, for ADB bridge features)
- `phone_addresses` — LAN IPs for direct phone communication
- `mesh_port` — default 8435
- `operator_mesh_id` — (server only) scopes admin API to this mesh
- `admin_token` — (server only) admin API auth
- `ntfy_enabled`, `ntfy_server`, `ntfy_topic` — push notifications

## Mesh Protocol

- **Shared module**: `focuslock_mesh.py` — imported by both desktop collars and homelab mail service
- **Gossip interval**: 10s (parallel — all peers contacted simultaneously)
- **LAN discovery**: UDP broadcast on port **21037** (NOT 21027 — that's Syncthing)
- **Peer addresses**: Capped at 4 per peer, working address promoted to front
- **Address refresh**: Re-resolved each gossip tick (handles DHCP/WiFi/Tailscale changes)
- **Tailscale cache**: 60s hostname refresh interval
- **WARREN_WHITELIST**: All trusted node IDs including generic seed IDs (`phone`, `homelab`)
- **Deployment**: `focuslock_mesh.py` + `focuslock_ntfy.py` need updating — desktop collars import both. Windows .exe must be rebuilt via `build-win.py`.
- **ntfy push**: Optional instant order delivery via ntfy.sh (or self-hosted). Payload is only `{"v": N}` — zero-knowledge by construction. Config: `ntfy_enabled`, `ntfy_server`, `ntfy_topic` in config.json. Topic auto-derived from `mesh_id`. Gossip remains the consistency layer; ntfy is a latency optimization.

## Safety

- Terms of Surrender consent screen on first install
- Release Forever button (Lion only) — full teardown + auto-uninstall
- Factory reset at 150 escapes
- System is consensual. Power dynamic is not.

# The Collar + Lion's Share + Bunny Tasker

A multi-platform ecosystem for consensual remote device restriction. The Lion controls. The Bunny obeys. The Collar enforces.

## Architecture

```
Lion's Device               Homelab (optional)              Bunny's Phone (Android)
┌──────────────────┐       ┌──────────────────────┐       ┌─────────────────────────┐
│ Lion's Share     │       │ Bridge (systemd)     │──ADB─▶│ The Collar (invisible)  │
│  (app or web UI) │       │  - Jail enforcement   │       │  - HTTP server          │
│                  │       │  - Volume enforcement │       │  - Device admin         │
│ Control tab:     │       │  - Tamper detection   │       │  - Payment listener     │
│  Lock/Unlock     │       │  - Launcher control   │       │  - SMS receiver         │
│  Timer/Paywall   │       │                       │       │  - Lovense bridge       │
│  Quick locks     │       │ Mail Service          │       │  - Camera2 selfie       │
│                  │       │  - IMAP payment check │       │  - Geofence monitor     │
│ Advanced tab:    │       │  - Evidence emails    │       │                         │
│  9 lock modes    │       │  - Photo verification │       │ Bunny Tasker (visible)  │
│  Modifiers       │       │  - Ollama LLM eval    │       │  - Stats dashboard      │
│  Photo tasks     │       │  - Subscription track │       │  - Subscriptions        │
│  Lovense control │       │                       │       │  - Self-lock            │
│  Geofence        │       └──────────────────────┘       │  - Messaging            │
│                  │                                       │  - QR pairing           │
│ Inbox tab:       │       P2P Enforcement Mesh            │  - Balance notification │
│  Messages        │◀═══════ RSA-signed orders ═══════════▶│  - Pay button           │
│  Device cards    │         (works without homelab)       └─────────────────────────┘
│  Notifications   │
└──────────────────┘       Desktop Collar (Windows/Linux)
                           ┌──────────────────────────┐
                           │ System tray crown icon    │
                           │ Session lock enforcement  │
                           │ Mesh node (gossip P2P)    │
                           │ Self-installing (Windows)  │
                           └──────────────────────────┘
```

## Apps

| App | Package | Runs On | Visible |
|-----|---------|---------|---------|
| **The Collar** | com.focuslock | Bunny's phone | No — invisible, no launcher icon |
| **Bunny Tasker** | com.bunnytasker | Bunny's phone | Yes — companion with stats |
| **Lion's Share** | com.focusctl | Lion's phone | Yes — gold-themed controller |

## Mesh Enforcement

All devices form a P2P enforcement mesh. Orders are RSA-signed, version-numbered, and gossip-replicated. Any node can propagate orders to any other — no single point of failure.

- **Phone ↔ Desktop ↔ Homelab** — all mesh peers
- **HTTPS-first sync** with LAN/Tailscale fallback
- **Homelab is optional** — mesh works purely P2P between phone and desktops
- **Lion-only messages** — auth challenges visible only on Lion's Share, not bunny devices

### Desktop Collar (Windows & Linux)
- **Windows**: Self-installing exe — double-click → consent → UAC → fully enslaved (scheduled tasks, watchdog, ACL lockdown, firewall, standing orders)
- **Linux**: systemd service + AppIndicator tray icon
- Gold/gray crown tray icon (gold = connected, gray after 3 missed sync cycles)
- Session lock enforcement via `LockWorkStation` / `loginctl`
- Custom lock screen wallpaper generation

## Features (v28)

### Lock Modes
Basic, Negotiation, Task, Compliment, Gratitude Journal, Exercise, Love Letter, Photo Task, Random

### Enforcement
- Paywall with compound interest (10%/hr, reduced by subscription tier)
- Tiered escape penalties ($5/$10/$15+ per attempt, stacking)
- Admin tamper: +$500 attempt, +$1000 removal (stacking)
- Public shame notification after 5 escapes
- 32 taunts (Portal, Pokemon, Severance, Frieren, LOTR, Star Wars, Lion's Share)
- Progressive buzzer + vibration on escape
- Lovense integration (escape buzz, lock pulse, task reward)
- Max volume enforcement via bridge
- Geofence auto-lock ($100 paywall on breach)
- SMS trigger: "sit-boy [mins] [$amount]"
- Camera2 silent selfie on task completion

### Photo Tasks (LLM-verified)
- Lion assigns task: "Clean the kitchen", "Do 20 pushups"
- Or LLM generates tasks (Chore/Exercise/Service/Creative categories)
- Bunny takes live photo with camera preview (back camera)
- Photo sent to Ollama (minicpm-v) for AI verification
- Pass → unlock. Fail → "Try again."
- Evidence email to Lion regardless

### Subscriptions
| Tier | Cost | Perks |
|------|------|-------|
| Bronze | $25/wk | Stats + messaging |
| Silver | $35/wk | Reduced interest (5%/hr) |
| Gold | $50/wk | No interest + 1 free unlock/month |

Cancel fee: 2x one period. Overdue: warnings at 1hr/24hr, auto-lock at 48hr.

### Payment Detection
145+ banks across 21 regions supported (full list in `shared/banks.json`). Detects bank app launches and payment notifications. Multi-language keyword matching (English, French, etc.).

### Pairing
QR code + RSA 2048 key exchange. Bunny generates QR in Bunny Tasker, Lion scans with Lion's Share. No manual IP entry.

### Safety
- Terms of Surrender consent screen on first install
- Release Forever button (Lion only) — full teardown + auto-uninstall
- Factory reset at 150 escapes
- System is consensual. Power dynamic is not.

## Files

### Android
- `focuslock.apk` — The Collar (bunny's phone, invisible)
- `bunnytasker.apk` — Bunny Tasker (bunny's phone, visible companion)
- `focusctl.apk` — Lion's Share (controller phone)

### Desktop
- `focuslock-desktop-win.py` — Windows collar (builds to self-installing exe via `build-win.py`)
- `focuslock-desktop.py` — Linux collar (GTK4 + loginctl)
- `focuslock_mesh.py` — Shared mesh protocol module
- `watchdog-win.pyw` — Windows process watchdog

### Server (optional)
- `focuslock-bridge.sh` — ADB bridge enforcement
- `focuslock-mail.py` — Mail + webhook + LLM service
- `web/index.html` — Lion's Share web UI

### Installers
- `installers/install-desktop-collar.sh` — Linux first-time install
- `installers/re-enslave-all.sh` — Update all Linux desktops via SSH
- `installers/homelab-setup.sh` — Homelab server setup
- `installers/install-standing-orders.sh` — Claude Code standing orders

### Docs
- `docs/MANUAL-LION.md` — Controller manual
- `docs/MANUAL-BUNNY.md` — Target manual
- `docs/PRICE-LIST.md` — Penalty and pricing reference

## Build

### Android
No Gradle. Uses aapt2/javac/d8/apksigner directly.
```bash
# Requires: JDK 17+, Android SDK build-tools, android.jar (API 34)
aapt2 compile → aapt2 link → javac → d8 → zip → zipalign → apksigner
```

### Windows Desktop
```bash
# Requires: Python 3.10+, Windows
python build-win.py          # Builds FocusLock.exe, FocusLock-Paired.exe, FocusLock-Watchdog.exe
python build-win.py --skip-sign  # Skip code signing (no Windows SDK needed)
```

### Linux Desktop
No build needed — runs as Python script via systemd.
```bash
bash installers/install-desktop-collar.sh
```

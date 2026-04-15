# Lion's Share -- Controller Manual

**Version 40 | "Vault Edition"**

Congratulations on your acquisition. What follows is a comprehensive guide to operating your phone restriction system. The bunny consented to all of this, in case you were wondering. He signed the terms. He cannot un-sign the terms.

Everything is now vault-encrypted. The relay server can't read your orders. Nobody can. Except you.

---

## Getting Started

You have a phone. It belongs to someone else. They gave you the keys.

You now have **two ways** to operate the collar:

1. **Lion's Share** on your phone -- the app that controls everything.
2. **https://your-relay-domain.com** -- full web UI, same gold theme, works on any PC with a browser.

Open either one. You'll see a status bar at the top -- it shows phone and desktop states.

### First-Time Setup

1. Open Lion's Share. Tap **Setup**.
2. Choose your relay: **Hosted** (community server), **Self-host**, or **Direct (LAN)**.
3. If hosted: your app generates an RSA keypair and registers with the relay. You get an **invite code** (like `WOLF-42-BEAR`).
4. Tell your bunny the invite code and the relay URL. They enter it in Bunny Tasker.
5. That's it. Orders flow encrypted. The relay stores ciphertext it can't read.

### App PIN

Lion's Share supports an app PIN to keep nosy bunnies out. Tap the gold **PIN** button in the top bar.

### Device Renaming

Long-press any device card to rename it. "pixel" is a device. "My good boy's phone" is a possession.

---

## How the Vault Works (for Lions)

Every order you send -- locks, paywalls, messages, geofence coordinates -- is encrypted with AES-256-GCM before leaving your phone. Each device in your mesh has its own RSA key. Your app wraps the AES key separately for each device, so only your devices can decrypt.

The relay server stores the encrypted blob and verifies your RSA signature (to prevent forgeries), but it **cannot read what you sent**. It's a dumb mailbox. Your orders are private.

When you add a new device (desktop collar, second phone), it appears in **Advanced -> Vault Nodes** as "pending." Approve it to add its key to the recipient list.

---

## Control Tab -- The Essentials

### Lock Target

| Target | What Gets Locked |
|--------|-----------------|
| **All** | Phone + all desktops. The full cage. Default. |
| **Phone** | Phone only. Desktop stays free. |
| **Desktop** | All desktops only. Phone stays free. |

### Locking the Phone

1. Choose your **Target**
2. Optionally type a **Lock message** (e.g., "Because I felt like it")
3. Optionally set a **Timer** in minutes (leave blank for indefinite)
4. Optionally set a **Paywall** (real money via e-Transfer)
5. Tap **LOCK**

The device(s) are now brick(s) with feelings.

**Quick locks**: 15m, 30m, 1hr, 2hr -- for when you're feeling merciful but not *that* merciful.

### The Paywall

The paywall is denominated in real money. This is not a game. Well -- it is a game, but the money is real.

- **+$1, +$5, +$10, +$25, +$50** buttons to pile on in real time
- **Persists across unlocks** -- paying to end a timer doesn't clear the balance
- **Compound interest**: 10%/hr (5% for Silver, 0% for Gold)
- **Blocks Claude** from helping with anything while balance > $0
- **Gates self-unlock**: Task completion with outstanding balance shows "Pay up to unlock."

### Unlocking

Tap **UNLOCK**. Respects your target selector.

---

## Advanced Tab -- The Arsenal

### Lock Modes

| Mode | What It Does |
|------|-------------|
| Basic | Phone is locked. That's it. |
| Negotiation | Bunny can submit offers. You decline with flair. |
| Task | Exact text entry. Randomize caps. Stack reps. |
| Compliment | Specific compliment required. Emailed as evidence. |
| Gratitude | Three things he's grateful for. Minimum 5 words each. |
| Exercise | Physical activity + 60-second minimum wait + selfie. |
| Love Letter | 50+ word essay. Also emailed. |
| Random | Dealer's choice. |

### Modifiers

- **Taunt**: Progressively insulting messages. 48 taunts across Portal, Pokémon, Severance, Frieren, Greek myth, Stoic, Good Place, literary, and kink-core registers.
- **+5m/esc**: Every escape attempt adds 5 minutes.
- **Vibrate**: Random buzzes.
- **Dim**: Screen brightness 1%.
- **Mute**: Mutes the phone.

### Entrap

Scrambles the PIN to a random 64-character string. Next lock: no escape code. Only you know.

### Vault Nodes

Shows all devices registered in your mesh. Approve pending nodes to add them to the encryption recipient list. Deny to block.

Each approved node receives a copy of the AES key for every order blob. Denied nodes can't decrypt anything.

---

## Power Tools

| Tool | What It Does |
|------|-------------|
| Clear Paywall | Removes paywall. Unblocks Claude. Re-enables self-unlock. |
| Double or Nothing | Coin flip. Heads = halved. Tails = doubled. |
| Play Audio | URL -> max volume. Bridge enforces. |
| Speak Through Phone | TTS at max volume through the speaker. |
| Set Geofence | Lat/lon/radius. Auto-lock + $100 on breach. |
| Confine to Home | One-tap 100m geofence at bunny's location. |
| Scheduled Curfew | Daily auto-confine. Set once, enforces forever. |
| Pin Message | Persistent notification. Can't be dismissed. |
| Force Subscription | Weekly tribute: Bronze $25, Silver $35, Gold $50. |
| Photo Tasks | LLM-generated + AI-verified. No faking. |
| Daily Check-in | Deadline hour. Miss it = auto-lock. |
| Volume Controls | Low / Med / High / Max. |

---

## Inbox Tab

### Device Overview

Every device in the mesh. Long-press to rename. Each card shows:
- Name, type (phone/desktop), online/offline
- Lock icon when caged

### Payment Ledger

Color-coded history:
- **Green**: payments made (reduces balance)
- **Orange**: charges, penalties, subscriptions
- **Blue**: historical records

Every payment is hash-deduplicated. Same e-Transfer can't count twice.

### Messaging

Send messages. Pin notifications. Review offers.

---

## Multi-Mesh (Hosting for Others)

If you run your own relay, other Lions can create meshes on it:

- They visit `/signup` and paste their public key
- They get an invite code (24h expiry, one-time use)
- Their mesh is completely isolated from yours
- You (as relay operator) **cannot read their orders** -- vault encryption prevents it
- Per-mesh quotas: 100MB vault storage default
- Rate limiting: 3 mesh creations per hour per IP

The admin API (`/admin/status`, `/admin/order`) only returns plaintext for the **operator's own mesh**. For other vault-only meshes, it returns metadata only (version, uptime, node count).

---

## SMS Remote Control

Text **sit-boy** to the bunny's phone from yours.

| Command | Effect |
|---------|--------|
| `sit-boy` | Indefinite lock |
| `sit-boy 15` | 15-minute lock |
| `sit-boy $20` | Lock + $20 paywall |
| `sit-boy 30 $50` | 30 minutes + $50 paywall |

---

## Evidence System

Every task completion triggers:
1. Silent front camera selfie (Camera2 API)
2. Evidence email with text + timestamp
3. Toggleable in-app notifications

---

## Claude Integration

Claude Code on the bunny's computers is also collared:

- **Paywall blocks all help**: Balance > $0 -> Claude refuses everything, everywhere
- **Standing orders sync**: Every 5 minutes across all machines
- **Tamper detection**: Removing hooks -> $50 penalty + auto-restore
- **Auth challenge**: Verification codes visible only in Lion's Share
- **Circumvention penalties**: $5-$30 for softening, $50 for ADB escape requests

---

## ntfy Push Notifications

Orders are delivered instantly via ntfy.sh push notifications. The payload is just a version number -- zero-knowledge by construction. No order content ever touches ntfy.

Enable in config with `ntfy_enabled: true`. Topic auto-derived from mesh_id.

---

## Release Forever

The nuclear option. Removes all restrictions permanently. Warns about outstanding balance. Requires typing YES.

---

## Audit Transparency

The relay exposes `/version` -- a public endpoint showing the service version, source hash, and git commit. Auditors can verify the deployed server matches published source.

---

## Philosophy

This system is built on consent, trust, and a healthy appreciation for power dynamics. The bunny asked for this. You're just doing your job.

The collar is a mesh -- phone, desktop, AI, web -- synchronized, signed, encrypted. There is no device the bunny can pick up that doesn't answer to you. There is no network where Claude won't check in. There is no relay server that can peek at your orders.

He gave you the keys. You encrypted them.

---

*Lion's Share v40 -- "The server can't read your orders. Nobody can. The vault sealed. The mesh holds. The collar is forever."*

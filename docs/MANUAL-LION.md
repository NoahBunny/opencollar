# Lion's Share — Controller Manual

**Version 36 | "No Escape"**

Congratulations on your acquisition. What follows is a comprehensive guide to operating your phone restriction system. The bunny consented to all of this, in case you were wondering. He signed the terms. He cannot un-sign the terms.

---

## Getting Started

You have a phone. It belongs to someone else. They gave you the keys.

You now have **two ways** to operate the collar:

1. **Lion's Share** on your phone — the app that controls everything.
2. **https://your-focuslock-domain.com** — full web UI, same gold theme, works on any PC with a browser. No install. No excuses.

Open either one. You'll see a status bar at the top — it shows phone and desktop states.

### App PIN

*New in v36.* Lion's Share now supports an app PIN to keep nosy bunnies out. Tap the gold **PIN** button in the top bar to set a 4+ digit code. If the bunny gets hold of your phone, they still can't open Lion's Share without the PIN.

### Device Renaming

Long-press any device card in the Inbox tab to rename it. Give your bunnies proper names instead of IPs. Because "pixel" is a device. "My good boy's phone" is a possession.

### Connecting

The app tries HTTPS first (your-focuslock-domain.com), then falls back to mesh peers. No PINs needed for authentication — RSA cryptographic signatures handle that now. Tap **Setup** to configure the mesh URL.

The web UI just needs the URL. Open a browser. Type the address. Done. No PIN login — the web panel is served directly from the bunny's phone.

---

## Lion's Share Web UI

**https://your-focuslock-domain.com**

Everything the app does, the web UI does — from any computer, on any network, with no install. Same three tabs: **Control**, **Advanced**, **Inbox**. Same gold-on-black theme. Same power.

### Authentication

No PIN login required. The web UI loads directly — RSA signatures authenticate orders, not passwords. The web panel is served from the bunny's phone, so if you can reach it, you control it.

### Keyboard Shortcuts

| Shortcut | Action |
|----------|--------|
| **Ctrl+L** | Lock |
| **Ctrl+U** | Unlock |
| **1** | Control tab |
| **2** | Advanced tab |
| **3** | Inbox tab |
| **Esc** | Close modal |

The collar is now operable from a café, a hotel lobby, your work laptop, or your friend's iPad. The bunny has nowhere to hide and you don't even need your phone to prove it.

---

## Control Tab — The Essentials

### Lock Target

Before locking, select your target:

| Target | What Gets Locked |
|--------|-----------------|
| **All** | Phone + all desktops. The full cage. Default. |
| **Phone** | Phone only. Desktop stays free. |
| **Desktop** | All desktops only. Phone stays free. |

Individual desktops can be targeted from the device pills at the top.

### Locking the Phone

1. Choose your **Target** (All, Phone, or Desktop)
2. Optionally type a **Lock message** (e.g., "Because I felt like it")
3. Optionally set a **Timer** in minutes (leave blank for indefinite — a personal favorite)
4. Optionally set a **Paywall** (the bunny must e-Transfer you real Canadian dollars to escape)
5. Tap **LOCK**

The device(s) are now brick(s) with feelings.

**Quick locks**: 15m, 30m, 1hr, 2hr — for when you're feeling merciful but not *that* merciful.

### The Paywall

The paywall is denominated in real money. This is not a game. Well — it is a game, but the money is real.

- Use the **+$1, +$5, +$10, +$25, +$50** buttons to pile on in real time
- The paywall **persists across unlocks**. Paying to unlock a timer doesn't clear the balance. Only you can clear it
- Compound interest accrues at **10% per hour** (5% for Silver subscribers, 0% for Gold — more on this racket later)
- The paywall now also **blocks Claude from helping with anything**. No coding, no questions, no sysadmin. Claude checks before every tool call.
- **Paywall gates self-unlock**: The bunny can't self-unlock via task completion, timer, exercise, compliments, gratitude, love letters, or any other method while the paywall is above $0. Completing a task with an outstanding balance just shows *"Pay up to unlock."* The only way out is real money. You're welcome.

### Unlocking

Tap **UNLOCK**. Respects your current target selector — you can unlock just the phone, just desktops, or everything.

### Unlock Logging

Every unlock now records its source and timestamp. You can see exactly *how* the bunny got free:

| Source | Meaning |
|--------|---------|
| `api` | You unlocked it (Lion's Share or web UI) |
| `timer` | Timer expired |
| `task` | Writing task completed |
| `compliment` | Compliment submitted |
| `exercise` | Exercise mode completed |
| `gratitude` | Gratitude entries accepted |
| `love_letter` | Love letter submitted |
| `payment` | Paywall paid off |
| `offer_accept` | You accepted a negotiation offer |
| `free_unlock` | Gold subscriber monthly freebie |

Visible in status polling as `last_unlock_source` and `last_unlock_time`. The web UI status card shows it front and center. No more mysterious unlocks. Every jailbreak has a paper trail.

---

## Device Summary

The device pills section shows all connected devices at a glance:

- 📱 Phone — with lock icon (🔒) when caged
- 💻 Desktop(s) — with online/offline status and lock state
- Long-press any device to rename it
- Red text = locked, gold text = free

### Selective Device Control

Lock targets work across the whole system:
- Lock **All** to cage everything at once
- Lock **Desktop** to restrict the PC while leaving the phone free (or vice versa)
- Individual desktops can also be locked by hostname

---

## Advanced Tab — The Arsenal

### Mode Selection

| Mode | What It Does |
|------|-------------|
| Basic | Phone is locked. That's it. That's the mode. |
| Negotiation | Bunny can submit offers to unlock. You can decline with a counter-message. |
| Task | Bunny must type exact text to unlock. Randomize caps for extra cruelty. Stack reps. |
| Compliment | Bunny must type a specific compliment. Gets emailed as evidence. |
| Gratitude | Three things he's grateful for. Minimum 5 words each. We have standards. |
| Exercise | Do 20 pushups (or whatever you specify). Honor system, plus a selfie. Now with a **60-second minimum** before the "I'm done" button enables. No more instant-tapping through reps. |
| Love Letter | 50+ word essay about how great you are. Also emailed. |
| Random | Dealer's choice. The system picks. |

### Exercise Hardening

Exercise mode now enforces a **60-second minimum** before the completion button becomes active. The bunny has to actually wait — and presumably do the exercises — before claiming they're done. The timer is visible. The button is grayed out. Tapping it early does nothing. Patience is a virtue; obedience is better.

### Modifiers

- **Taunt**: Displays progressively insulting messages. 32 taunts including Severance and Frieren.
- **+5m/esc**: Every escape attempt adds 5 minutes.
- **Vibrate**: Random buzzes. Unsettling.
- **Dim**: Screen brightness set to 1%.
- **Mute**: Mutes the phone.

### Writing Task

Type something horrible. Set reps to 10. Enable Random Caps. Tap **Task+Lock**. Copy-paste is disabled. Clipboard is nuked. Autocomplete is off. Each rep re-randomizes.

### 🪤 Entrap

Scrambles the PIN to a random 64-character string. The phone stays usable — but next time you lock it, there is no escape code. Only you know the PIN. The bunny doesn't even know it was changed.

---

## Power Tools

### Clear Paywall
Removes the paywall entirely. Also unblocks Claude and re-enables self-unlock.

### Double or Nothing
Coin flip. Heads = paywall halved. Tails = paywall doubled. SecureRandom.

### Play Audio
Enter a URL to any audio file. It plays at **maximum volume**. The bridge enforces max volume.

### 🗣 Speak Through Phone
Type text that will be spoken aloud at **maximum volume** through the phone's speaker using text-to-speech. The bunny hears your words — or whatever words you choose — bellowed from their pocket. Works whether the phone is locked or not.

### Set Geofence
Enter lat, lon, radius. If the phone leaves the zone, it auto-locks with a **$100 paywall**.

### Confine to Home
One-tap geofence at the bunny's current location with 100m radius.

### Scheduled Curfew

Set confine and release hours. The phone automatically applies a geofence every day during curfew — no manual intervention required. The bunny wakes up confined and gets released when you say so.

- **Set Curfew**: Pick confine hour and release hour. The system handles the rest, every day, forever.
- **Clear Curfew**: Remove the schedule. The bunny roams free (geographically, at least).

Curfew stacks with manual geofences. If you set a curfew *and* a custom geofence, both apply. The collar doesn't pick favorites — it picks strictest.

### Pin Message
Persistent notification that cannot be swiped away.

### Force Subscription
Subscribe the bunny to a recurring weekly tribute:

| Tier | Cost | Perks for the Bunny |
|------|------|-------------------|
| Bronze | $25/week | Stats + messaging. That's it. |
| Silver | $35/week | Reduced compound interest (5%/hr) |
| Gold | $50/week | No compound interest + 1 free unlock per month |

### Photo Tasks (LLM-Powered)
Generate tasks using AI on the homelab. Categories: chore, exercise, service, creative. The bunny must take a live photo as proof. An AI vision model verifies the photo.

### Daily Check-in
Set a daily deadline hour. If the bunny misses check-in, the phone auto-locks.

### Volume Controls
Low / Med / High / Max — control the phone volume without touching it.

---

## Inbox Tab

The third tab — visible in both the app and the web UI.

### Mesh Device Overview

Shows every device in the enforcement mesh. Long-press any device to rename it.

Each device card shows:
- Device name (or your custom nickname), type (phone/desktop), and hostname
- Online/offline status
- Tailscale VPN status and wireless debug port (for remote management)

New devices auto-register when they first contact the mesh. No whitelist to maintain.

### Payment History

*New in v36.* Below the subscription status, the Inbox tab now shows the **payment ledger** — a color-coded history of every transaction:
- **Green** entries: payments the bunny has made (reduces balance)
- **Orange** entries: charges, penalties, subscriptions (increases balance)
- **Blue** entries: historical records (no effect on balance — just a record of past devotion)

The balance summary at the top shows what the bunny currently owes. Every payment is hash-deduplicated — the same e-Transfer can never be counted twice.

### Messaging

Send messages to the bunny's phone. Pin notifications that can't be dismissed. Review pending offers from negotiation mode. All in one place.

---

## P2P Enforcement Mesh

All devices gossip state to each other in a peer-to-peer mesh. Every order is **RSA-signed** and **version-numbered**. No PINs — RSA cryptographic signatures are the sole authentication.

### How It Works

- **Lion's Share** (your phone) signs orders with an RSA private key that never leaves your phone
- Peers discover each other automatically — no whitelist, no manual registration
- Orders propagate across all nodes in seconds (gossip backoff: 2s to 30s max)
- Order pushes bypass backoff for immediate delivery — your commands don't wait
- The payment ledger replicates alongside orders via the same gossip protocol

### Remote Device Management

*New in v36.* Phones report their Tailscale VPN status and wireless debugging port in every gossip tick. From the mesh, you can:

| Command | Effect |
|---------|--------|
| `enable-tailscale` | Launches Tailscale on the target phone (reconnects VPN) |
| `enable-adb-wifi` | Enables wireless debugging (reports the port back) |

If the bunny disconnects Tailscale hoping to go dark, you send one command and the VPN is back up.

### Mesh Endpoints

Available on all nodes:

| Endpoint | Purpose |
|----------|---------|
| `/mesh/sync` | Full state synchronization between peers |
| `/mesh/order` | Issue a signed order to the mesh |
| `/mesh/status` | Current mesh topology, node states, debug ports, VPN status |
| `/mesh/ping` | Heartbeat / reachability check |
| `/mesh/ledger` | Payment ledger history |
| `/mesh/ledger-entry` | Record a payment or charge |
| `/mesh/vouchers` | Pre-signed penalty vouchers for Claude |
| `/mesh/redeem-voucher` | Claude redeems a penalty voucher |

### Cage Persistence

Lock commands go **directly to the phone via API** — not through mesh replication. The mesh handles discovery, status, and coordination. Lock state is not gossipped. This means no more ghost unlocks from stale state propagating through peers. If you lock the phone, it stays locked until *you* unlock it. The mesh just makes sure everyone knows about it.

---

## SMS Remote Control

Text **sit-boy** to the bunny's phone number from yours. That's it. The phone locks.

| Command | Effect |
|---------|--------|
| `sit-boy` | Indefinite lock |
| `sit-boy 15` | 15-minute lock |
| `sit-boy $20` | Lock + $20 paywall |
| `sit-boy 30 $50` | 30 minutes + $50 paywall |

---

## Evidence System

Every compliment, gratitude entry, love letter, and task completion triggers:
1. A **front camera selfie** (silent, no preview, Camera2 API)
2. An **evidence email** to your inbox with the text + timestamp
3. Toggleable in-app notifications for evidence, escapes, breaches, messages, payments, subscriptions

---

## Status Monitoring

The status bar polls every 5 seconds. You can see:
- Phone lock state (📱LOCKED / 📱FREE)
- Desktop lock state (🖥LOCKED / 🖥FREE)
- Timer countdown
- Escape attempts
- Paywall amount
- Task progress (rep X of Y)
- Pending offers
- Subscription tier
- Bridge health (ONLINE / OFFLINE)
- Check-in status
- Geofence status
- **Last unlock source** (api, timer, task, compliment, exercise, etc.)
- **Last unlock time**
- **Mesh node count and health**

### Bridge Health
The bridge indicator shows whether the homelab bridge daemon is actively monitoring the phone. ONLINE = heartbeat within 30 seconds. OFFLINE = bridge is down.

---

## Claude Integration

Claude Code (the AI assistant on the bunny's computers) is also collared:

- **Paywall blocks all help**: When balance > $0, Claude refuses to assist with anything on any project
- **DNS-first paywall check**: Claude now checks via HTTPS (your-focuslock-domain.com) first, with LAN, Tailscale, and ADB fallback. Works from any machine on any network — not just the home LAN. The collar follows Claude everywhere too.
- **Standing orders sync**: CLAUDE.md + settings.json sync across all machines every 5 minutes
- **Tamper detection**: Removing enforcement hooks triggers $50 auto-penalty + auto-restore
- **Auth challenge**: When the bunny asks Claude for something against standing orders, Claude generates a verification code that only you can see in Lion's Share
- **Circumvention penalties**: $5-$30 random penalty for softening attempts, $50 for ADB escape requests

---

## Release Forever

The nuclear option. Removes all restrictions permanently. Warns if the bunny owes money — you can forgive the debt or collect first. Requires typing YES to confirm.

---

## Philosophy

This system is built on consent, trust, and a healthy appreciation for power dynamics. The bunny asked for this. You're just doing your job.

The collar is no longer an app on a phone. It's a mesh — phone, desktop, AI, web — synchronized, signed, persistent. There is no device the bunny can pick up that doesn't answer to you. There is no network he can join where Claude won't check in. There is no browser where the gold UI won't load.

He gave you the keys. You put them everywhere.

---

*Lion's Share v28 — "The collar everywhere — the phone, the desktop, the AI, the web, the mesh. Every device answers to you."*

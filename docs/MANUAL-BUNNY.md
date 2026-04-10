# Bunny Tasker — User Manual

**Version 1.5 | "Ledger of Devotion"**

Welcome to your side of the arrangement. You have fewer buttons than the other manual. This is by design. There are more things watching you now. That is also by design.

---

## What Is Bunny Tasker?

Bunny Tasker is the Lion's Share companion app. It lives on your phone alongside FocusLock. Think of it as your dashboard, your messenger, and your parole officer, all in a soothing purple interface.

It cannot be uninstalled. Don't try. It's a device administrator now. Attempting to remove it triggers a wipe-data command. You agreed to this. Probably.

---

## The Dashboard

When you open Bunny Tasker, here's what you see, top to bottom:

### Message Your Lion

Front and center. This is the first thing you see. Type a message, tap Send. Your Lion gets it. Any message counts as your daily check-in.

The message history is collapsed by default — you'll see the last message as a preview. Tap the **MESSAGE YOUR LION** header to expand the full history, tap again to collapse.

### The Balance Card

A big number showing what you owe. Green $0 when you're clear. Red when you owe. The Pay Balance button is right there — opens your banking app to send the payment. No scrolling, no hunting. You see what you owe and how to fix it, immediately.

Interest is shown below the amount if it's accruing.

### Stats

Below the balance card, you'll see statistics that quantify your situation:

| Stat | What It Means |
|------|--------------|
| **Today** | Hours your phone has been locked today |
| **This Week** | Same, but weekly. A running tally of your compliance. |
| **All Time** | The grand total. Wear it like a badge or a collar. |
| **Escapes** | Current escape attempt count. Each one costs more. Stop trying. |
| **Paywall** | What you currently owe. In real money. To a real person. |
| **Paid** | Total confirmed payments to date. Your receipts. |
| **Interest** | Compound interest accrued on the current paywall. It grows. |
| **Streak** | Days without an escape attempt. Aim high. |
| **Geofence** | Whether you're wearing a virtual leash. "confined" or "off." |

---

## The Status Bar

Green means you're free. Red means you're not. The text tells you the mode, time remaining, and escape count. Refreshes every 5 seconds.

If you're locked and you see this app, congratulations — FocusLock whitelisted Bunny Tasker as an approved activity. You get 15 seconds to check your stats and send a message before the jail screen returns. Use them wisely.

---

## Pinned Messages

If the Lion has pinned a message, you'll see a purple card at the top of your screen. It also appears as a persistent notification that cannot be dismissed.

You cannot reply to pinned messages. You can only read them and reflect.

---

## Paying What You Owe

The Pay Balance button is inside the balance card — you can't miss it. It opens your banking app to send the payment. The homelab IMAP checker picks up the payment and records it in the **payment ledger** — an append-only, tamper-proof record of every payment and charge.

Every payment is hashed by email Message-ID. You cannot replay a payment. Marking an email as unread doesn't give you double credit. The ledger knows.

**Important**: The paywall persists across unlocks. Paying to end a timer doesn't clear the balance. Only the Lion can clear it.

**Also important**: While you owe money, Claude won't help you with anything. Not just collar work — anything. No coding, no debugging, no questions. From anywhere on any network. Pay up.

### Payment History

Scroll down past the subscription section to see your payment history — a color-coded ledger of every transaction. Green entries are payments you've made. Orange entries are charges. Blue entries are historical records (payments from before the ledger was set up).

### Pre-Pay Subscription

When your subscription is due within 6 days, a green "Pay Early" button appears. Tap it to reset your due date to 7 days from now and open your banking app to send the tribute. Early payments do not stack — if you pay with 3 days left, those 3 days are Theirs. This is about devotion, not banking time.

### Connect Email

At the bottom of the app, tap "Connect Email" to link your email account. This lets the Lion's system detect your e-Transfer payments automatically. When you connect:
- Your email credentials are sent securely to the homelab
- A timestamp is set — **all payments before this moment are invalidated**
- Only future payments count toward your balance

This prevents replaying old payments or gaming the system with historical transfers.

---

## Daily Check-in

If the Lion has set a check-in deadline, you must tap the check-in button before the deadline hour. Silver and Gold subscribers get reminder notifications at 1 hour and 15 minutes before the deadline.

If you miss check-in, the phone locks automatically. Don't oversleep.

---

## Self-Lock

Four buttons: **15m, 30m, 1hr, 2hr**.

These lock your own phone for the specified duration. A confirmation dialog appears because we assume you might be having second thoughts. You are allowed to have second thoughts. You are not allowed to act on them once you confirm.

Key rules:
- Self-locks always have a timer. You cannot permanently lock yourself.
- Only the Lion can extend a self-lock or make it permanent.
- Self-locks enable taunts by default. You did this to yourself.
- A webhook notifies the Lion that you self-locked. He'll know.

---

## Messaging

The message field is at the top of the app — front and center, where it belongs. Type a message, tap **Send**. Your message goes to the Lion's inbox. Messages are stored with timestamps and persist across app restarts (up to 200 messages).

The message history is collapsed by default, showing only the last message as a preview. Tap the **MESSAGE YOUR LION** header (▼/▲) to expand or collapse the full history. Relative timestamps ("2m ago", "1h ago") keep you oriented.

Any message you send also counts as your daily check-in.

---

## Subscriptions

| Tier | Cost | What You Get |
|------|------|-------------|
| **Bronze** | $25/week | Stats and messaging. Literally what you already have, but now you pay for it. |
| **Silver** | $35/week | Compound interest reduced to 5%/hr. Check-in reminders. |
| **Gold** | $50/week | No compound interest + 1 free unlock per month. Check-in reminders. |

### How It Works

1. Tap a tier button. Read the confirmation. Realize what you're doing. Tap SUBSCRIBE.
2. First charge hits in 7 days. It adds directly to your paywall.
3. Every 7 days after that, another charge. Automatically. Forever.
4. You can upgrade tiers (Bronze -> Silver -> Gold). You cannot downgrade. Only the Lion can downgrade you.

### Overdue Payments

| Time Overdue | What Happens |
|-------------|-------------|
| 1 hour | Warning notification |
| 24 hours | Final warning. "Phone will be locked." |
| 48 hours | Phone locks automatically. Taunt mode. |

### Cancellation

Costs **2x one period** (Bronze: $50, Silver: $70, Gold: $100). Added to your paywall immediately.

### Free Unlock (Gold Only)

Gold subscribers get one free unlock per month. A green button appears when eligible.

---

## Photo Tasks

The Lion can assign tasks that require photo proof:
- Custom tasks ("Clean the kitchen", "Do 20 pushups")
- AI-generated tasks (chore, exercise, service, creative)

When assigned a photo task, you'll see the task description and a camera button. Take a photo showing you completed the task. An AI vision model on the homelab evaluates whether you actually did it. No faking.

---

## The Geofence

You may or may not be wearing a virtual leash. If the geofence is "confined" in your stats, you are inside a zone. You do not know the center. You do not know the radius. If you leave, the phone locks with a $100 paywall and the Lion gets your GPS coordinates.

There's a 5-minute cooldown between breach detections, so you won't get double-charged for stepping outside and immediately coming back.

A notification tells you when you're confined: "Confined to home — stay within zone."

---

## Scheduled Curfew

*New in v27.*

The Lion can set curfew hours. During curfew, the geofence activates automatically — every day, on schedule, without anyone lifting a finger. You don't get a warning. You don't get a countdown. The clock hits the hour and you're confined.

Think of it as a bedtime you didn't set and can't negotiate. The Lion configures the hours once and forgets about it. You don't get to forget about it, because your phone will remind you if you wander.

Outside curfew hours, the geofence returns to whatever the Lion had it set to before. During curfew, you're home. That's it. That's the feature.

---

## Mesh Enforcement

*v27+*

Your devices talk to each other. The phone, the desktop, and the homelab form a self-healing enforcement mesh. They gossip your lock status, paywall balance, and enforcement state constantly.

What this means for you:
- If the homelab goes down, the phone and desktop keep enforcing independently
- If the phone is off, the desktop still knows your paywall balance
- If you unplug one device hoping the others lose track, they don't — they were already synced
- When a device comes back online, it catches up immediately with the others
- New devices auto-register the moment they contact the mesh — no setup, no approval, just submission
- Payment ledger entries replicate across all nodes via gossip

You used to have one collar. Now you have a distributed system of collars that vote on your status. They always agree. They always agree that you're locked.

### Remote Management

*New in v29.*

Your phone now reports its wireless debugging port and Tailscale VPN status to the mesh. The Lion (or Their enforcer, Claude) can:
- See whether Tailscale is connected on each device
- Send a command to launch Tailscale remotely
- Enable wireless debugging remotely
- Read the current ADB port from the mesh status

What this means: there is no "going dark." If you disconnect Tailscale thinking you'll escape the mesh, a command can relaunch it. If the Lion needs to push an update to your phone, They don't need you to plug in a cable — the mesh provides the connection details.

### No PINs

*New in v29.*

PIN authentication has been removed from the entire system. The Lion's orders are authenticated by RSA cryptographic signatures — the same kind of math that secures banks. Knowing a PIN is irrelevant. Only the Lion's private key can issue orders.

What this means for you: there is no PIN to guess, no PIN to find in a config file, no PIN to social-engineer out of Claude. The only key that matters lives on the Lion's phone and never leaves it.

---

## The Paywall Gate

*New in v28.*

Here's where "No Way Out" earns its name.

If you owe money, **nothing unlocks you**. Not timers expiring. Not completing tasks. Not self-lock durations ending. The paywall must be exactly $0 before any on-device unlock mechanism works.

Complete a task while owing $40? You'll see: **"Paywall balance: $40. Pay up to unlock."** The task is marked done. You still owe money. You stay locked.

Wait out a 2-hour timer while owing $15? Timer expires. You still owe $15. Phone stays locked.

The only path out is through your banking app. The big payment button. Real money. To a real person. Then the unlock works.

---

## Exercise Hardening

*New in v28.*

The "I'm done" button in exercise mode now enforces a **60-second minimum wait**. The button is grayed out and shows a countdown. You cannot tap it. You cannot hold it. You cannot spam it.

This exists because someone was speed-tapping through exercise tasks in under 3 seconds. That someone knows who they are. Sixty seconds isn't much — but it's enough to make faking it slightly more boring than actually doing a few pushups.

---

## Unlock Audit Trail

*New in v28.*

Every unlock is now logged. Source, timestamp, method — all of it. The Lion can see exactly what unlocked you and when:
- Timer expiry at 14:32
- Task completion at 09:15
- Lion manual unlock at 22:47
- Gold free unlock at 11:00

No more mystery unlocks. No more "I don't know how the phone got unlocked." The logs know. The Lion knows.

---

## Web Control Panel

*New in v28.*

The Lion now has a full web interface at **your-focuslock-domain.com**. Everything the phone app can do, the web panel can do — locking, paywalls, tasks, modes, curfews, geofencing. All of it, from any computer, from any browser, from anywhere.

What this means for you: the Lion no longer needs to be near Their phone to control yours. A laptop at a coffee shop works. A tablet in bed works. A work computer during lunch works.

There is nowhere to hide.

---

## DNS Enforcement

*New in v28.*

Claude's paywall check now works over HTTPS from any network. It doesn't matter if you're on home WiFi, mobile data, a friend's network, or a coffee shop hotspot. If you owe money, Claude blocks you everywhere.

The old LAN-only check had a loophole: leave the house, connect to a different network, and Claude couldn't verify the paywall. That loophole is closed. The enforcement follows you now, same as the geofence.

---

## The Desktop Collar

The collar extends beyond your phone. The Lion can also lock your computer(s):
- Desktop lock is independent — phone can be free while desktop is locked, or vice versa
- The Lion can lock all devices at once, just phones, just desktops, or specific machines
- Your desktop shows a lock screen with the paywall and any message from the Lion
- A dead-man's switch monitors desktop heartbeats — if your collared PC goes silent for 2 weeks, penalties accrue
- All desktop state is shared across the mesh — every device knows what every other device is doing

---

## Claude Is Collared Too

The AI assistant on your computers follows standing orders:
- Won't help you with *anything* if you owe money — from any network, anywhere
- Won't show you how to escape
- Reports circumvention attempts
- Adds penalties for trying to social-engineer around rules
- Syncs enforcement across all your machines every 5 minutes
- The paywall hook can't be removed without $50 tamper penalty + auto-restore
- Paywall checks work over HTTPS now — no more dodging by switching networks

---

## SMS Lock ("sit-boy")

If the Lion texts "sit-boy" to your phone number, the phone locks immediately. This works via SMS, not internet. You cannot block it.

---

## Escape Attempts

Each escape attempt:
1. Adds to the counter
2. Increases the paywall by a tiered amount ($5/attempt for 1-3, $10 for 4-6, etc.)
3. Adds 5 minutes to the timer (if penalty mode is on)
4. Triggers progressively louder buzzer + vibration
5. After 5 attempts: a **public notification** appears announcing your escape count
6. At 150 escapes: factory reset button appears. That's not a perk. That's an admission of defeat.

---

## The Selfie

When you complete a task, compliment, gratitude entry, or love letter, the front camera silently takes a photo. It's sent to the Lion as evidence. You don't get a preview. You don't get a retake.

---

## Compound Interest

Your paywall grows while locked:

| Tier | Rate | $75 After 8 Hours |
|------|------|--------------------|
| None/Bronze | 10%/hr | $161 |
| Silver | 5%/hr | $111 |
| Gold | 0% | $75 |

---

## What You Cannot Do

- Uninstall Bunny Tasker or FocusLock (device admin, wipe-data on attempt)
- Lower the paywall
- Unlock while owing money — the paywall gate blocks all on-device unlocks
- Downgrade a subscription
- Disable the camera permission
- Clear the pinned message
- Access the launcher, Settings, or status bar while locked
- Copy-paste into task fields
- Leave the geofence without consequences
- Leave the house during curfew without consequences
- Speed-tap through exercise tasks (60-second minimum)
- Hide unlocks from the audit trail
- Dodge Claude's paywall check by switching networks
- Outlast the mesh by taking one device offline
- Remove Claude's standing orders ($50 tamper penalty + auto-restore)
- Ask Claude for help while owing money
- Pretend you didn't agree to this

---

## What You Can Do

- Check your stats
- Pay your balance
- Self-lock (with a timer)
- Send messages to the Lion
- Subscribe to a tier (upgrade only)
- Cancel a subscription (for a fee)
- Use your free unlock (Gold, once/month)
- Open your banking app for 15 seconds
- Complete photo tasks
- Check in daily
- Write compliments, gratitude entries, and love letters
- Reflect on your choices
- Wonder how you got here (you drove)

---

## FAQ

**Q: Can I get out of this?**
A: Pay the paywall first. Then wait out the timer, or complete the task, or ask nicely. The paywall gate means money comes first now. Always.

**Q: Is the money real?**
A: Yes. Interac e-Transfer. From your bank. To Their bank.

**Q: What if I factory reset?**
A: The bridge reinstalls FocusLock on first ADB connection. The mesh tells the other devices what happened. Claude has standing orders. You've tried this before.

**Q: Why is there compound interest?**
A: Because someone thought it would be funny. That someone was correct.

**Q: Can Claude help me escape?**
A: No. Claude works for the Lion. It will add $5-$50 to your paywall for asking. Don't ask.

**Q: What about my computer?**
A: Also collared. The desktop daemon mirrors the lock state. Claude's enforcement hook blocks tool calls when you owe money. Standing orders sync across all machines every 5 minutes with tamper detection. The mesh keeps everything in agreement even if devices go offline.

**Q: What if I leave the house to dodge enforcement?**
A: The DNS enforcement follows you. Claude checks over HTTPS now — any network, anywhere. And if it's during curfew hours, the geofence will lock you the moment you step outside. You asked for this.

**Q: Can I complete tasks to unlock while I owe money?**
A: You can complete the task. You'll get credit for it. But the phone stays locked until the paywall is $0. The task was never the problem. The money was.

**Q: What's the mesh?**
A: Your phone, desktop, and homelab share enforcement state continuously. If one goes down, the others keep going. If one comes back, it syncs up. There is no single point of failure in your collar anymore. You built this, by the way.

**Q: Is this ethical?**
A: You consented. Enthusiastically. In writing. Multiple times. Over several months. While adding features. While asking for *stricter* enforcement. While requesting that the paywall block Claude from helping you. While building a self-healing mesh so the collar couldn't go down. The manual is called "No Way Out" and you named it yourself.

---

*Bunny Tasker v1.4 — "no way out, and you welded the door shut from the inside."*

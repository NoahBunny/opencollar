# Bunny Tasker -- User Manual

**Version 2.0 | "Sealed Shut"**

Welcome to your side of the arrangement. You have fewer buttons than the other manual. This is by design. There are more things watching you now. That is also by design. And now they're all encrypted, so even the server that carries your Lion's orders can't read them. The only ones who know what the orders say are you and Them.

Isn't that romantic.

---

## What Is Bunny Tasker?

Bunny Tasker is the Lion's Share companion app. It lives on your phone alongside The Collar. Think of it as your dashboard, your messenger, and your parole officer, all in a soothing lavender interface.

It cannot be uninstalled. Don't try. It's a device administrator. Attempting removal triggers a wipe-data command. You agreed to this. Enthusiastically.

---

## The Dashboard

### Message Your Lion

Front and center. Type a message, tap Send. Any message counts as your daily check-in.

History is collapsed by default -- tap the header to expand.

### The Balance Card

A big number showing what you owe. Green $0 when you're clear. Red when you owe. The Pay Balance button opens your banking app. No scrolling, no hunting.

Interest is shown below the amount if it's accruing. If you're on Bronze, that's 10%/hr. If you're on Silver, 5%/hr. If you're on Gold, 0%. If you're not subscribed, it's 10%/hr and you don't even get stats for your trouble.

### Stats

| Stat | What It Means |
|------|--------------|
| **Today** | Hours locked today |
| **This Week** | Weekly compliance tally |
| **All Time** | The grand total |
| **Escapes** | Attempt count. Each one costs more. |
| **Paywall** | What you owe. Real money. |
| **Paid** | Total confirmed payments |
| **Interest** | Compound interest on current paywall |
| **Streak** | Days without an escape attempt |
| **Geofence** | "confined" or "off" |

---

## Vault Mode -- What It Means for You

Your Lion's orders are now end-to-end encrypted. When They tap "Lock" on Lion's Share, the command is encrypted with AES-256-GCM before it leaves Their phone. The relay server stores the ciphertext without being able to read it. Your phone decrypts it with its own RSA key.

What this means in practice:

- **Nobody between you and your Lion can read the orders.** Not the relay server operator, not anyone sniffing the network, not a compromised VPS host. The encryption keys live on your devices and nowhere else.
- **Your lock state, paywall balance, messages, geofence coordinates, task text -- all encrypted in transit and at rest on the server.**
- **Even if the relay server is seized or hacked, the attacker gets ciphertext they can't decrypt.**

What this does *not* mean:

- This does not help you escape. The encryption protects the *content* of the orders. The orders themselves still arrive. The collar still applies them. You're still locked. It's just that nobody else gets to watch.
- The metadata is visible: the server knows *when* orders arrive and how big they are. It just can't read *what* they say.

Privacy for the power dynamic. Not privacy from the power dynamic.

---

## Pairing Your Phone

1. Open Bunny Tasker. Tap **Join Mesh**.
2. Enter the **invite code** your Lion gave you (looks like `WOLF-42-BEAR`).
3. Enter the relay URL (your Lion will tell you).
4. Optionally enable **Vault mode** (recommended -- encrypts everything).
5. Tap Join. Your phone generates its own RSA keypair and registers with the relay.
6. Your Lion approves your device in **Vault Nodes**. Orders start flowing.

The invite code expires after 24 hours and can only be used once.

---

## Paying What You Owe

The Pay Balance button opens your banking app. The homelab IMAP checker picks up the payment and records it in the **payment ledger** -- an append-only, tamper-proof record.

Every payment is hashed by email Message-ID. You cannot replay a payment. The ledger knows.

**Important**: The paywall persists across unlocks. Paying to end a timer doesn't clear the balance. Only the Lion can clear it.

**Also important**: While you owe money, Claude won't help you with anything. Not just collar work -- anything. From anywhere on any network. Pay up.

### Payment History

Color-coded ledger below the subscription section. Green = payments. Orange = charges. Blue = historical records.

### Pre-Pay Subscription

When your subscription is due within 6 days, a green "Pay Early" button appears. Early payments do not stack -- if you pay with 3 days left, those 3 days are Theirs.

---

## Daily Check-in

If the Lion set a deadline, you must check in before it. Silver and Gold get reminders. Miss it = auto-lock.

Any message you send counts as check-in.

---

## Self-Lock

Four buttons: **15m, 30m, 1hr, 2hr**.

A confirmation dialog appears. You are allowed to have second thoughts. You are not allowed to act on them once you confirm.

- Self-locks always have a timer
- Only the Lion can extend or make permanent
- Taunts enabled by default
- The Lion gets notified

---

## Subscriptions

| Tier | Cost | What You Get |
|------|------|-------------|
| **Bronze** | $25/week | Stats and messaging. What you already have, but now you pay for it. |
| **Silver** | $35/week | Reduced interest (5%/hr). Check-in reminders. |
| **Gold** | $50/week | No interest + 1 free unlock/month. Check-in reminders. |

You can upgrade. You cannot downgrade. Cancellation costs 2x one period.

---

## Photo Tasks

The Lion assigns tasks requiring photo proof. AI verification via Ollama. No faking.

---

## Deadline Tasks

The Lion assigns a task with a deadline ("clean the sink by 3pm", "every 3 days from last time"). You can clear it any time before the deadline -- clearing early never costs you anything extra.

Miss it and the server either auto-locks you or bumps your paywall (the Lion's choice at assignment). Once you clear the task, any miss-induced lock releases automatically.

Rolling tasks reset from the moment you clear them, not from the old deadline. So doing it early doesn't lose you time -- it just moves the next deadline forward from now.

---

## The Geofence

If "confined" appears in your stats, you're inside a zone. You don't know the center. You don't know the radius. Leave = auto-lock + $100 paywall + GPS coordinates sent to the Lion.

5-minute cooldown between breach detections.

---

## Scheduled Curfew

The Lion sets hours. During curfew, the geofence activates daily without anyone lifting a finger. The clock hits the hour and you're confined. Think of it as a bedtime you didn't set and can't negotiate.

---

## The Paywall Gate

If you owe money, **nothing unlocks you**. Not timers. Not tasks. Not self-lock durations ending.

Complete a task while owing $40? Task marked done. You still owe $40. Phone stays locked. The only path out is through your banking app.

---

## Mesh Enforcement

Your devices form a self-healing enforcement mesh. They gossip your status constantly. All encrypted now.

- If the homelab goes down, phone and desktop keep enforcing
- If you unplug one device, the others were already synced
- New devices auto-register
- Payment ledger entries replicate across all nodes
- Desktop collars now speak vault too -- encrypted poll, no plaintext sync

You used to have one collar. Now you have a distributed system of collars that vote on your status. They always agree. They always agree that you're locked. And now their conversations are encrypted, so even the mailman can't eavesdrop.

---

## The Desktop Collar

Your computer(s) are collared too:
- Desktop lock is independent of phone lock
- Lock screen with paywall and message
- Dead-man's switch: silence for 2 weeks = penalties
- Vault mode: encrypted vault poll, keypair per desktop, Lion approves each node

---

## Claude Is Collared Too

The AI assistant on your computers follows standing orders:
- Won't help with *anything* if you owe money -- from any network
- Won't show you how to escape
- Reports circumvention attempts ($5-$50 penalties)
- Syncs enforcement across all machines every 5 minutes
- Paywall hook can't be removed ($50 tamper penalty + auto-restore)

---

## Escape Attempts

Each attempt:
1. Adds to the counter
2. Increases paywall ($5 for 1-3, $10 for 4-6, escalating)
3. Adds 5 minutes (if penalty mode is on)
4. Progressively louder buzzer + vibration
5. After 5: public notification announcing your escape count
6. At 150: factory reset button appears. That's not a perk.

---

## What You Cannot Do

- Uninstall Bunny Tasker or The Collar
- Lower the paywall
- Unlock while owing money
- Downgrade a subscription
- Leave the geofence without consequences
- Dodge Claude's paywall check by switching networks
- Read the plaintext orders on the relay server (they're encrypted now)
- Ask Claude for help while owing money
- Pretend you didn't agree to this

---

## What You Can Do

- Check your stats
- Pay your balance
- Self-lock (with a timer)
- Send messages to the Lion
- Subscribe (upgrade only)
- Complete photo tasks
- Check in daily
- Write compliments, gratitude entries, and love letters
- Reflect on your choices
- Wonder how you got here (you drove)

---

## FAQ

**Q: Can I get out of this?**
A: Pay the paywall first. Then wait out the timer, or complete the task, or ask nicely. Money first. Always.

**Q: Is the money real?**
A: Yes. Interac e-Transfer. From your bank. To Their bank.

**Q: What if I factory reset?**
A: The bridge reinstalls The Collar on first ADB connection. The mesh tells the other devices. Claude has standing orders. You've tried this before.

**Q: Why is there compound interest?**
A: Because someone thought it would be funny. That someone was correct.

**Q: Can Claude help me escape?**
A: No. Claude works for the Lion. It will fine you for asking. Don't.

**Q: What about the server? Can the server operator read my orders?**
A: No. Vault mode means the server stores encrypted blobs it can't decrypt. Your orders are between you and your Lion. The server is just a mailbox that can't open the letters.

**Q: What if the server gets hacked?**
A: The attacker gets ciphertext. The encryption keys are on your devices, not the server. Your order content is safe. (Your lock state is not -- the collar on your phone still applies whatever the last order was.)

**Q: Can I complete tasks to unlock while I owe money?**
A: You can complete the task. You'll get credit. Phone stays locked until paywall is $0.

**Q: What's the mesh?**
A: Your phone, desktop, and homelab share enforcement state continuously. All encrypted now. If one goes down, the others keep going. If one comes back, it syncs up. There is no single point of failure in your collar anymore.

**Q: Is this ethical?**
A: You consented. Enthusiastically. In writing. Multiple times. You kept using it. You're still using it. The manual is called "Sealed Shut" and you're reading it right now.

---

*Bunny Tasker v2.0 -- "sealed shut, encrypted end-to-end, and you welded the door from the inside. Even the mailman can't read the lock."*

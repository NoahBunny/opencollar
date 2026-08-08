# Handoff — 2026-08-07/08 (on-device QA execution + 10 bug fixes)

This session **executed** the device-QA runbook from `HANDOFF-2026-08-07.md`
against two **real** phones and found + fixed **ten** bugs (three
server/installer, five controller, two Tor), **all ten verified on-device**.
Runbook **section F (A3 Tor/onion) is now fully verified** — items 20–25, the
first time any of it has run on hardware. Below is
what landed, what's verified, and the precise next steps.

Controller is now **77 / 77.0** (bumped for fixes 4–7 and 10 — at 73 the installer's
`TARGET_CONTROLLER_VERSIONCODE` gate would have treated already-paired Lion
phones as up to date and never pushed any of them). Built APK:
`android/controller/focusctl-signed.apk`, staged as `apks/focusctl-v77.apk`.

**The headline:** a Direct (LAN) pairing — the path the UI recommends as
"Fastest" — could not show the Lion anything at all. Orders flowed and applied,
but the status line was frozen twice over: once by a signature rebuild reading
the wrong bytes (fix 5), and once by a poll that never ran (fix 6). Fix 5 hid
fix 6, which is why the previous session saw a rejection log rather than
silence.

## Test devices (both are real phones, to be enslaved later — treat as ephemeral)
- **Bunny**: Samsung **SM-S908W**, adb `R5CT339K1ZL`, Android 16 / API 36.
  Its real prod collar config (mesh `qOZ8W6mGo2ZB` @ `collar.nunyabiznu.com`)
  was **backed up** and restored at end of session. Backup + restore script:
  `<session-scratchpad>/prod-config-backup/` (contains the **real bunny privkey** —
  local/ephemeral, not committed).
- **Lion**: Google **Pixel 10**, adb `57261FDCR004ZQ`, Android **17 / API 37**.
  Was a clean slate; restored to clean (Lion's Share uninstalled) at end.
- Staging: local relay `focuslock-mail.py` on `18435` (via `.venv`); both phones
  reached it. Direct LAN worked (same Wi-Fi `192.168.199.0/24`, no AP isolation).

## Bugs found → fixed → verified (committed on `feat/ecosystem-review-2026-05-25`)

1. **Companion never granted `WRITE_SECURE_SETTINGS`.** Bunny Tasker's in-app
   "Join Mesh" writes join config to `Settings.Global`; without WSS the join POST
   succeeds server-side but the local write throws → half-joined state (Lion sees
   the node, phone shows "Join failed"). **Fixed** `installers/re-enslave-phones.sh`
   (`recage_focuslock` now grants companion WSS) + `HANDOFF-2026-08-07.md` prereq.

2. **`shadeguard_disabled` event rejected (HTTP 400).** The Collar detects the
   watchdog being disabled mid-lock and POSTs `event_type=shadeguard_disabled`, but
   the relay's allowlist didn't include it → the Lion was never told the bunny
   killed the enforcement watchdog. **Fixed** `focuslock-mail.py` (added to the
   event allowlist + `kind_map` → `watchdog_off`; no financial penalty, matching the
   "costly-exit not punish-exit" tamper model). Verified: `Tamper event … kind=watchdog_off`.

3. **`set-display-name` verified against the wrong key.** The companion signs the
   rename with the account `bunny_pubkey` (PairingManager key) — as every other
   bunny-signed endpoint does — but the server verified only against the vault
   `node_pubkey` (the Collar's ControlService key). On real hardware those are
   distinct keys, so every rename 403'd; the unit test masked it by reusing one key
   for both stores. **Fixed** `focuslock-mail.py` (verify against the account
   `bunny_pubkey`, `node_pubkey` fallback) + **added regression test with distinct
   keys** in `tests/test_display_name_desktop_task_guards.py`. Verified: rename
   propagates to the account store.

4. **`pairDirect` stored the Lion keypair under the wrong prefs keys.** The
   controller's Direct (LAN) pairing saved `lion_privkey_b64`/`lion_pubkey_b64`,
   but `createMesh` and all ~17 signed-op call sites read the canonical
   `lion_privkey`/`lion_pubkey`. So after a **Direct (LAN) pair — the recommended
   "Fastest" path — `buildDirectSigHeaders` read `""` → returned null → every order
   failed "missing lion_privkey" *before connecting*. The bunny was paired but
   completely uncontrollable. **Fixed** `android/controller/.../MainActivity.java`
   `pairDirect` to use canonical key names. **Verified end-to-end**: `/api/lock` →
   collar `locked=True` (15-min timer); `+$25` → collar `paywall=25`.
   (Found only via an instrumented debuggable controller build — release build hid it.)

5. **Direct-mode `/mesh/status` always failed signature verification, so the
   Lion's UI froze on its last-good snapshot.** The Collar signs a flat ten-field
   status core and emits those fields at the **top level** of the status body —
   a body that also embeds the whole orders document, which repeats **six** of
   the ten key names *earlier in the byte stream* (`paywall`, `task_reps`,
   `task_done`, `offer`, `offer_status`, `sub_tier`). `verifyStatusSignature`
   rebuilt the core with the `indexOf`-based `parseJson*` helpers, which take the
   first match anywhere in the string, so it read the **orders** copies. Five
   agreed by luck — both sides read the same `Settings.Global` row — but
   `paywall` did not: `handleMeshStatus` defaults an unset paywall to `"0"` while
   `buildOrdersJson` emits `""`. So on a freshly paired Collar (no balance
   charged yet) the rebuilt core differed from the signed one by exactly one
   field, every status was dropped as forged, and the Lion saw `$0` / no lock
   even while orders were being delivered and applied. **Fixed** by moving the
   rebuild into a new `android/controller/src/com/focusctl/StatusCore.java`
   scoped to the top-level JSON object — which closes the whole class, not just
   the paywall instance: no embedded document can shadow a signed field again.
   Wire format is unchanged, so the updated controller still verifies Collars
   already in the field. This also explains the shape of the symptom last
   session: once a balance is charged the two paywall copies agree again, so
   status would have started verifying — the freeze is specific to (and total
   for) a Collar that has never been charged.

6. **A serverless Direct (LAN) pairing never polled status at all.**
   `startStatusPolling` gated the whole poll body on `!meshId.isEmpty()`. A
   direct pairing has **no mesh** — that's the entire point of the mode — so the
   poller was dead code for it and the UI held its last render ("Switched to
   bunny", `$0`, no lock) forever. `meshGet` already serves `/mesh/status`
   straight off the Collar in direct mode (`MainActivity.java:2424`); only this
   gate stopped it from ever being asked. **Fixed** by extracting the decision
   to `android/controller/src/com/focusctl/PollGate.java` — poll when there's a
   mesh **or** a reachable direct target, reusing meshGet's own direct-mode
   condition so the two can't drift — plus a `| Direct` liveness marker on the
   unlocked status line. Found only because fix 5 was already in: with fix 5
   absent you get a *rejection log*, which is what the previous session chased;
   with fix 5 present you get **silence**, which is what exposed the gate.
   Regression-tested in `PollGateTest.java`.

7. **Switching bunnies left the previous bunny's balance on screen.**
   `setActiveBunny` already resets the backing fields (`isLocked`,
   `timerEndMs`, `lastEscapes`, `lastPaywall`, the optimistic caches) — someone
   had clearly thought about this — but the *rendered* balance is only ever
   written by `updateLiveStatus`, which runs solely on a **verified** snapshot.
   So when the newly-selected bunny is unreachable or its status can't be
   verified, nothing overwrites the figure. Observed live: switching from a
   bunny at `$40` to one at `$7` held `$40` for the whole observation window
   while the new slot's status was (correctly) being rejected. Lock state and
   timer isolated fine; only the money leaked. **Fixed** — the switch now
   renders `$—` in grey until that bunny's own status lands. Deliberately not
   `$0`: that is a positive claim the bunny owes nothing, which we can't back
   and which resolves an ambiguity in the bunny's favour. **Verified on
   hardware** (v76): switching from the `$40` bunny to the unverifiable slot now
   holds `$—` / "waiting for status…" across 5 poll cycles and 10 rejections,
   and switching to a *trusted* slot still fills in its real figure within one
   cycle — the fix withholds, it doesn't freeze.

8. **The Tor-ON build killed the entire Collar the first time Tor started —
   and then crash-looped.** `org.torproject.jni.TorService.onCreate()` calls
   `broadcastStatus()`, which touches `androidx.localbroadcastmanager`. That
   class was never bundled: this project has no Gradle and therefore no
   dependency resolver, and `build.sh` dexed only the AAR's `classes.jar` +
   jtorctl + bcprov. So the first onion wake produced
   `java.lang.NoClassDefFoundError: androidx.localbroadcastmanager.content.LocalBroadcastManager`
   → `FATAL EXCEPTION: main` → process death, on repeat, because the ntfy wake
   that triggered it gets redelivered to the restarted app.

   **This is worse than a broken feature.** The Collar *is* the enforcement
   app, and the onion wake topic is a plain ntfy.sh topic that anyone who
   learns it can publish to. A Tor-ON build in the field would hand the bunny
   (or any passer-by) a remote way to crash-loop the thing that holds the
   leash. It stayed hidden because Tor ships default-OFF and section F had
   never been run on hardware — exactly the gap the runbook flagged with
   "keep `FOCUSLOCK_TOR_AAR` unset in production until C3 passes."

   **Fixed** by bundling `androidx.localbroadcastmanager:1.1.0`'s `classes.jar`
   as `FOCUSLOCK_LBM_JAR` in **both** `android/slave/build.sh` and
   `android/controller/build.sh` (mirroring the jtorctl/bcprov pattern), with
   `scripts/setup-qa-env-garuda.sh --tor` fetching it and exporting it in the
   generated env file. Both builds now print a loud warning if the var is unset
   rather than producing a silently fatal APK. **Verified on-device**: the same
   wake that crash-looped the app is now handled cleanly, the process survives,
   and Tor comes up (SOCKS listening on `127.0.0.1:9050`, `TorService` bound
   and running in-process).

9. **The onion was never published: Tor rejected every `ADD_ONION`.**
   `TorManager.startTorAndPublish` built
   `ADD_ONION ED25519-V3:<blob> Flags=Detach Port=… ClientAuthV3=<pub>`.
   Tor refuses a `ClientAuthV3=` clause unless the matching auth flag is in
   `Flags`, and answers `No auth type specified` — so the command failed, the
   exception was caught as "relay fallback", and the hidden service simply never
   went up. Every wake, silently, forever. **Fixed**: `Flags=Detach,V3Auth`, and
   the client-auth keys are now resolved *before* the command is built (with no
   keys we must not publish at all, so building the command first was backwards).
   **Verified on-device**: `TorManager: onion published:
   7bmvii…orad.onion (1 client key(s))`.

10. **The Lion could send orders over Tor but could never read status.**
    `maybeWakeBunny` — which bumps the Collar's wake topic, starts the Lion's own
    Tor and authorizes the onion — was wired into `postDirectWithFailover` only.
    `getDirectWithFailover`, the status-read path, dialed `.onion` candidates
    with no SOCKS proxy running and failed silently, so the UI sat on its
    last-good snapshot until the operator happened to issue an order. Same
    "orders flow, Lion goes blind" shape as fixes 5–7, now over Tor. **Fixed** by
    calling the same wake from the read path behind a rate limit + in-flight
    guard (the poller ticks every 5 s while the wake blocks up to 120 s, so an
    unreachable bunny would otherwise pile up executor tasks). Candidates stay
    LAN-first, so it costs nothing while the LAN works. **Verified on-device**:
    from a cold start with the app force-stopped, Tor down, Wi-Fi off and **no
    order ever sent**, the Lion brought Tor up within 20 s and rendered
    `bunny | LOCKED | 22m 35s left`.

## On-device verification status
- **D-14** phantom-node: VERIFIED (`node_id=sm-s908w`, never `pixel`, both join + gossip).
- **Section A** (cage/safety): **7/8** VERIFIED — A-1 ceiling authority (`ed1c717`),
  A-2 bounce + companion allowlist, A-3 escapes→paywall tiers + factory-reset button,
  A-4 settings safety-floor, A-6 SEALED dialer-block (emergency by code inspection,
  no live 911), A-7 safeword release + no re-jail, A-8 watchdog-off tamper (after fix #2).
  **A-5 photo task** not done (needs camera + Ollama).
- **C-12** display-name (after fix #3) + **C-13** desktop-task guard (both 409s): VERIFIED.
- **Section B**: Lion onboarding + mesh-create VERIFIED; **both pairing paths**
  (relay-invite + direct manual IP/fingerprint, fingerprint-verified E2EE) VERIFIED;
  **B-9 order delivery** VERIFIED after fix #4 (lock + balance reach + apply on collar).
- **Direct-mode status reflection** (after fixes #5 + #6): VERIFIED end-to-end on
  a fresh Direct (LAN) pairing, controller v75:
  - Live `/mesh/status` from the SM-S908W, checked against the device's real
    `focus_lock_bunny_pubkey`: old first-match rebuild **fails**, scoped rebuild
    **passes**, sole drifting field `paywall` `'0'` vs `''`.
  - Paired → `bunny | UNLOCKED | Direct`, **zero** `REJECTED direct` log lines,
    **with the paywall never charged** — the exact state that was broken.
  - 15-min lock → Collar `focus_lock_active=1`; Lion shows
    `bunny | LOCKED | 14m 49s left` while still `paywall=null`.
  - `+$25` → Collar `focus_lock_paywall=25`; Lion balance `$25`.
- **B-9**: VERIFIED both halves — after unlock, 6 consecutive poll cycles over
  36s held `collar_active=0` / `UNLOCKED`, no reversion; and a re-lock extends
  the timer (15M → `14m 50s`, re-lock 30M → `29m 50s`, Collar `unlock_at`
  +29 min) rather than showing the old remaining time.
- **B-11 key substitution**: VERIFIED against a **live hostile endpoint**. A
  stand-in Collar (`staging/qa_fake_collar.py`) pairs for real and can be flipped
  dishonest on demand. Full matrix, all against the same paired slot:

  | stand-in Collar | it claims | Lion shows | rejections |
  |---|---|---|---|
  | honest | `$7`, unlocked | `$7 UNLOCKED` | 0 |
  | **signs with a foreign key** | `$999`, LOCKED 1h | `$7 UNLOCKED` (held) | 5 |
  | **no signature at all** | `$999`, LOCKED 1h | `$7 UNLOCKED` (held) | 5 |
  | honest again, genuinely `$12` | `$12`, unlocked | `$12 UNLOCKED` | 0 |

  The last row is the one that makes the others mean anything: the Lion isn't
  frozen, it's *discerning*. A slot whose stored key had been rotated out from
  under it behaved the same way — 75 consecutive rejections, no adoption of the
  new key. On-device counterpart to `StatusCoreTest.lanMitmClearingTheLockIsRejected`
  and `unsignedStatusIsRejected`.
- **B-10 multi-bunny isolation**: **FAILED** (balance leak) → fixed as fix #7 →
  **re-VERIFIED on v76**. Lock state and timer were correctly isolated all along;
  only the money leaked.
- Closing loop, all through the real apps: UNLOCK ALL + CLEAR on the Lion →
  Collar `active=0`, `paywall=0`; Lion's line settles to
  `bunny | UNLOCKED | Direct` / `$0`.

## Full-surface empirical sweep (final pass)

After the seven fixes, the session did a breadth pass over the whole feature
surface rather than just the runbook items. Three reusable harnesses came out of
it, all in `staging/`:

| harness | what it does |
|---|---|
| `qa_collar_driver.py` | signs Audit-C1 direct POSTs with the bunny key pulled off the device, so any `/api/*` endpoint can be driven from the laptop without the Lion's private key |
| `qa_collar_sweep.py` | 40-case feature sweep: sends the order, reads `Settings.Global` back, asserts the state actually changed |
| `qa_messaging.py` | messaging + its guards against a real relay, signed with the device's real bunny key |

**Collar control surface — 34 pass · 0 fail · 6 blocked.** All 9 lock modes
(incl. `random` resolving to a real mode), task reps, photo-task, all five
modifiers, `word_min`/`exercise`, paywall add/stack/clear, the
"a lock without an amount must not clobber the ledger" regression, subscribe,
messages, pinned messages, offers, geofence set/clear, check-in, notification
prefs, volume, lock/unlock.

Guards are asserted, not just happy paths — a gate that stopped gating would
otherwise read as a pass:

- local unsubscribe is **refused** (cancellation is server-authoritative, so the
  bunny can't drop their own tier from the device they hold)
- check-in rejects an out-of-range hour
- free unlock is **refused below Gold**, granted at Gold, then **refused again**
  as already-used-this-month

> Four "failures" in the first run were all the harness being wrong (wrong param
> names, millis instead of hour-of-day, a missing Gold precondition), not product
> bugs. That first run also had a real harness weakness: the Collar answers `200`
> with `{"error": …}` for a refused order, so status code alone proved nothing.
> The sweep now fails on an error body unless a refusal was explicitly expected.

**Check-in auto-lock verified by accident, which is the best kind.** Setting the
deadline to hour 22 during the sweep caused the Collar to lock itself ~50 minutes
later with *"Missed daily check-in. Message your Lion."* — Bunny Tasker's
`BunnyService` enforcing autonomously, no order involved. (It re-fires until the
bunny checks in or the deadline is cleared; `deadline >= 0` is the enable gate.)

**Relay path — mesh created (`PRnLhTZI6Ec7`, invite `MARE-36-SWAN`), bunny joined
via the real `/api/mesh/join`, vault appends flowing** (`slots=2`, ~1.7 KB
ciphertext every 10 s), **and `state-mirror` verifying** with `signer=bunny` over
`paywall, paywall_original, sub_tier, sub_due, lock_active, locked_at,
unlock_at, free_unlocks` — the data path compound interest and payment crediting
depend on.

> A `state-mirror sig verify failed` warning en route was **not** a bug: the
> account store had no `bunny_pubkey` because the mesh id had been set over adb,
> skipping the join. After a proper join it verified first try. Worth knowing
> that a vault-registered-but-not-account-joined node fails state-mirror silently
> (server-side warning only), which would freeze the money ledger.

**Messaging — 11 pass · 0 fail**, against the real relay with the device's real
key. Send, thread round-trip, pinned flag; and the guards: the bunny **cannot**
forge a message from the Lion, **cannot** edit or delete history (Lion-only —
enforced by signature, not by client honesty), a body tampered in flight fails
the signature, a stale timestamp is outside the replay window, oversize and empty
are rejected, and an unregistered node is refused.

**E2EE messaging is genuinely zero-knowledge.** A message composed in Bunny
Tasker and sent from the phone stored as `text: "[e2ee]"` plus `ciphertext` /
`encrypted_key` / `iv` — and the plaintext appears **nowhere** in the relay's
stored record. Verified by inspecting the relay's own message store.

**Bunny Tasker UI, live against the real Collar:** lock status, `GOLD SUBSCRIBER`
badge, the Lion's pinned message, paired fingerprint, balance card, full stats
grid (today/week/all-time locked, escapes, paywall, paid, interest, streak,
geofence), subscription state (`GOLD — $50/week | 6d until due`) with tier /
prepay / cancel controls, self-discipline self-lock, payment history, payer
identity.

### A3 Tor / onion — runbook section F

The toolchain was **already on this box** the whole time (`~/android-libs` has
the AAR + jtorctl + bcprov since July; JDK 24 at `~/.jdks/jdk-24.0.2+12`). The
only thing missing was the generated env file, which
`scripts/setup-qa-env-garuda.sh --tor-only` writes in seconds. Onion was never a
fetch-everything job — worth knowing before anyone budgets a day for it.

Tor-ON builds: **32 MB each**, `libtor.so` for all four ABIs
(arm64-v8a, armeabi-v7a, x86, x86_64), Tor classes dexed, `apksigner VERIFIED`.
Default-off builds are unaffected — the whole Tor block is gated on
`FOCUSLOCK_TOR_AAR`.

**All six items VERIFIED**, after fixes 8, 9 and 10.

| item | status |
|---|---|
| **20** `libtor.so` present, app starts | **VERIFIED** — 4 ABIs, Collar runs, `TorService` bound, Tor SOCKS on `127.0.0.1:9050` |
| **21** onion provisioned + in pair response | **VERIFIED** — `7bmvii…orad.onion` written 0.1 s after service start (v3 addresses derive offline; publishing is what needs the network), and the Lion's `onion_auth_pub` arrives at pairing → `focus_lock_onion_auth_pub` |
| **22** `ADD_ONION` + `ClientAuthV3` | **VERIFIED** after fix 9 — `onion published: … (1 client key(s))`; the client half (`ONION_CLIENT_AUTH_ADD`) proven by the Lion's Tor successfully fetching the auth-gated descriptor |
| **23** LAN+relay off → wake → Lion connects | **VERIFIED both directions** — Wi-Fi off on the Lion (LTE only), bunny LAN IP confirmed unreachable: status read `bunny \| LOCKED \| 28m 7s left`, and an UNLOCK order landed on the Collar in **< 15 s** over the onion |
| **24** device without Lion's key can't connect | **VERIFIED by revoke/restore** — republished authorized to a different key: the identical probe that returned `HTTP 200` now returned `HTTP 000` twice; restoring the real key returned `HTTP 200 in 6.7 s` |
| **25** cold-start wake latency | **VERIFIED** — see numbers below |

Measured latencies (real hardware, real Tor network):

| leg | time |
|---|---|
| ntfy publish → Collar receives wake | **1.07 s / 1.24 s / 1.75 s** |
| wake → onion published (cold) | **6.09 s** |
| Tor round-trip to the onion (`/mesh/ping`) | **5.7 s / 6.7 s** |
| warm order over the onion → applied on Collar | **< 15 s** |
| cold Lion (app killed, Tor down) → Tor up | **≤ 20 s** |

Two findings worth keeping:

- **The Collar refuses to publish an unauthenticated onion.** With no Lion
  client-auth pubkey stored, `startTorAndPublish` logs
  *"no Lion auth pubkey stored — onion would be unauthed; skipping publish"*
  and returns **before** `ADD_ONION`. So item 24's property — a device without
  Lion's key can't connect — is enforced at *publish* time, not merely at
  connect time: the hidden service never goes up at all. That's the stronger
  guarantee, and it's why item 22 can't be exercised until a Lion with an
  `onion_auth_pub` has paired.
- **Tor starts on demand, not at boot.** `focus_lock_tor_warm=1` alone does not
  start it — `isWarm()` only suppresses teardown. The trigger is
  `TorHook.onWake`, fired when a message lands on the onion wake topic. Battery-
  sane, and worth remembering before concluding "Tor is broken" from an idle
  device with no tor process.

### Not verified — and why

Honest list. Nothing here should be read as working.

| area | status | blocker |
|---|---|---|
| **Tor / onion runtime** | **VERIFIED** — items 20–25, see the A3 section above | — |
| Lovense | blocked | no toy paired |
| TTS / audio playback | blocked | audible-only, nothing to assert over adb |
| Photo-task verification | blocked | needs camera capture + Ollama `minicpm-v` |
| SMS `sit-boy` trigger | blocked | can't inject a real SMS on a physical device |
| Payment detection (145 banks) | not run | needs an IMAP account with real bank mail |
| Compound interest accrual | not run | time-based (10 %/hr); state-mirror carries the inputs, and the maths is unit-tested |
| Windows collar | blocked | no Windows box |
| Linux desktop collar | not run | GTK4 `gi` missing here (its tests skip for the same reason) |
| Release Forever / entrap / factory reset | deliberately skipped | destructive on a real device |
| Admin-tamper penalties (+$500/+$1000) | not run | needs device admin, which this session deliberately never enabled |
| Cage bounce / escape counter / SEALED dialer | earlier session | verified 2026-08-07 (section A, 7/8) — not re-run here |

## Open follow-ups (resume here next session)
1. **Tor/onion is now verified end-to-end — keep it default-OFF until the fixes ship.** Section F items 20–25 all passed on hardware, but only after three fixes (LocalBroadcastManager crash-loop, the `V3Auth` flag, and the read-path Tor wake). `FOCUSLOCK_TOR_AAR` should stay unset in production until this branch merges, since a Tor-ON build without fix 8 is remotely crash-loopable. Remaining Tor nice-to-have: the first cold onion order blocks in `wakeAndAuthorize` for up to 120s before the POST goes out — worth surfacing that wait in the UI rather than looking dead.
2. **Give bunny slots distinguishable names.** Every slot is labelled `"bunny"`
   by default and the status line shows only that label, so with more than one
   bunny the Lion cannot tell whose lock state and whose balance they're looking
   at. This is what made fix #7's leak invisible — the wrong number under an
   identical name reads as the right number. Worth a label prompt at pair time,
   or defaulting to the paired host/fingerprint. Small change, and it's the
   difference between "multi-bunny works" and "multi-bunny is safe to use".
   - Tooling for any of this is now in the repo: `staging/qa_fake_collar.py` (a
     stand-in Collar that pairs for real and can be flipped to `mode=forged` /
     `mode=unsigned` on demand) and `staging/qa_device_ui.py` (tap/type driver).
   - **Firewalld blocks the phones from reaching this laptop** (zone `home`,
     iface `wlp2s0` — the phones get "No route to host"). Rather than opening
     ports, use `adb reverse tcp:8432 tcp:8432` (and `tcp:18435 tcp:18435` for
     the relay) and point the app at `127.0.0.1`. No firewall change needed;
     the tunnel dies with the adb connection, so re-run it after a replug.
   - Minor UI nit seen while testing: the Bunnies dialog doesn't redraw its own
     list after a removal — the slot is gone, but you have to close and reopen
     to see it.
3. **B-10 with two *live* bunnies, and the mesh-invite half of B-11.** This
   session ran entirely serverless (Direct LAN) — nothing touched a relay — and
   used `qa_fake_collar.py` as the second bunny. To exercise the mesh-invite
   path and the "not encrypted" banner, bring up `staging/start-staging.sh`
   (note `staging/config.json`'s `mesh_url` is stale at `192.168.240.1` — set it
   to the laptop's LAN IP, or use the adb-reverse trick above) and re-point
   `focus_lock_mesh_url` on the bunny.
   - Latent trap worth closing while you're in there: `updateLiveStatus`
     (`MainActivity.java:557`) still reads its display fields off the whole body
     with the same first-match helpers, so in direct mode it renders the
     **orders** copies of `paywall` / `task_reps` / `task_done` / `offer` /
     `offer_status` / `sub_tier`. Today those tie with the signed values (same
     `Settings.Global` rows, and the paywall difference is `""` vs `"0"`, both
     rendering as `$0`), so nothing is visibly wrong — but it is the same
     shadowing hazard one schema change away from mattering. `StatusCore.fromWire`
     already returns exactly those fields, scoped correctly.
4. **A16 device-admin teardown.** On Android 16/17, `dpm remove-active-admin` and
   `pm uninstall` refuse a non-test device admin, so "Release Forever"'s self-destruct
   (`ControlService.java:1969-1979`, which shells `dpm`/`pm`) likely can't remove the
   admin on A16 — teardown needed a manual Settings deactivation both times. Consider
   having the app call the `DevicePolicyManager.removeActiveAdmin()` **API** instead.
5. **A-5 photo task** (camera + Ollama/minicpm-v). **D-15/16** desktop tamper ratchet,
   **E** Windows collar, **F** Tor onion runtime — all need other hardware.
6. **Design idea — re-lock on tamper at max tightening (SEALED).** When the bunny
   disables device admin / the watchdog at the SEALED ceiling, and the mesh link to
   the Lion is still intact (NOT safeworded / Released / `focus_lock_released=1`),
   the Collar should *re-assert enforcement* (force lock state on, re-foreground the
   jail, surface a blocking "re-enable enforcement" prompt) rather than only posting
   the `shadeguard_disabled`/tamper event. This is an escalation of *enforcement*,
   not a financial penalty — consistent with the consented SEALED ceiling and the
   "favor the Lion" tie-breaker, while staying above the safety floor. Caveat: the
   app cannot re-grant its own device admin or re-enable its own accessibility
   service (Android restricts both to the user), so re-assertion is limited to the
   software layers that remain (lock state + HOME/FocusActivity jail). Gate strictly
   to COLLAR+/SEALED per the app-private ceiling; never at LEASH.

## Device state RIGHT NOW (read before unplugging)

Both phones are still in QA state. Nothing has been torn down.

- **Bunny SM-S908W** (`R5CT339K1ZL`): Collar 81/8.38 + Bunny Tasker 60/2.27
  installed; `focus_lock_mesh_url` **repointed off production** to
  `http://192.168.199.217:18435`; paired to this session's throwaway Lion key
  (`focus_lock_lion_pubkey` set, SMS token provisioned); unlocked, paywall
  cleared. Its **prod config survived the previous session in `Settings.Global`**
  (mesh `qOZ8W6mGo2ZB` @ `collar.nunyabiznu.com`) and all 34 `focus_lock_*` keys
  were backed up **before** anything was touched.
- **Lion Pixel 10** (`57261FDCR004ZQ`): **fully clean.** Lion's Share was
  uninstalled at end of session (all three packages absent), `adb reverse`
  tunnels removed, screen settings restored. Nothing of this QA run remains.
- **Bunny SM-S908W** (`R5CT339K1ZL`): Collar **81 / 8.38** + Bunny Tasker
  **60 / 2.27** left installed, on its **production** config —
  `mesh_id=qOZ8W6mGo2ZB`, `mesh_url=https://collar.nunyabiznu.com`,
  `lion_pubkey=null`, unlocked, no balance, no subscription, check-in cleared.
  All 34 `focus_lock_*` keys restored **and verified**.
- **The test-Lion pubkey is cleared**, which is the one that would have bitten:
  a leftover key means the real Lion hits "already paired with a different
  Lion", and that has no in-app recovery — only Release Forever or a factory
  reset.
- Restore gotcha, now fixed in the script: `ControlService` re-persists its own
  mesh config while running, so restoring underneath a live service silently
  loses `mesh_id`/`mesh_url`. The script now force-stops both apps first and
  verifies every key, failing loudly rather than half-restoring.
- Deliberately **not** done: device admin, HOME-role takeover, accessibility.
  None are needed for the order/status paths, and follow-up #2 (A16 teardown)
  makes them expensive to undo. The bunny is *paired*, not *caged*.

To restore the bunny to its pre-QA state:
`<session-scratchpad>/prod-config-backup/restore-bunny.sh` — deletes any
`focus_lock_*` key the QA run added, then writes all 34 backed-up values back
(the backup holds the **real bunny privkey**; chmod 600, never committed). Then
`adb uninstall com.focuslock com.bunnytasker` on the bunny and
`adb uninstall com.focusctl` on the Pixel. **That scratchpad is ephemeral — if
it's gone, re-take a backup before touching the phone again.** Verify after
restore: `adb -s R5CT339K1ZL shell settings get global focus_lock_mesh_id`
should read `qOZ8W6mGo2ZB`.

## To resume

Everything below is **committed** on `feat/ecosystem-review-2026-05-25`
(5 commits on top of `a25ecc9`):

```
c351a06 chore(release): controller 77/77.0 + device-QA handoff + CHANGELOG
961fd70 test(qa): device-QA harnesses, so on-device work stops being hand-tapping
3f34c31 fix(tor): a Tor-ON build was remotely crash-loopable and never published
126b52f fix(controller): the Lion could command the bunny but never see her
d03011d fix(server)+fix(installers): three bugs only real hardware exposed
```

Nothing is pushed and no PR is open — that call is the operator's.

Green at handoff: `pytest tests/` → **1227 passed, 21 skipped**;
`bash android/build-conformance.sh` → **80 JVM tests, 0 failed**. The Java half
of the conformance pytest only runs with the CLI wired up, so run it explicitly:

```
bash android/build-conformance.sh
ANDROID_CONFORMANCE_CLI="$(cat build-conformance/cli-cmd.txt)" \
ANDROID_CONFORMANCE_CLI_SLAVE="$(cat build-conformance/cli-cmd-slave.txt)" \
  .venv/bin/python3 -m pytest tests/test_android_conformance.py -q     # 30 passed
```

- The clean (fix-only, non-debuggable) controller APK is rebuilt at
  `android/controller/focusctl-signed.apk` and staged as `apks/focusctl-v77.apk`
  where `find_apk` will pick it up. (74/75/76 existed only mid-session; each was deleted rather than
  left as a second build sharing a version code.)
- Driving the phone UI from the laptop worked well and is worth keeping: a small
  `uiautomator dump` + `input tap/text` helper (tap-by-text, type-by-field-id)
  made the whole onboarding → Direct Pair → lock → charge → unlock sequence
  scriptable. No instrumented/debuggable build was needed this time.
- Re-pair topology that works: both phones + laptop on one Wi-Fi; controller uses
  Direct (LAN) pair with the bunny's LAN IP + the 16-hex fingerprint from Bunny
  Tasker's pairing screen. Staging relay: `FOCUSLOCK_CONFIG=staging/config.json
  FOCUSLOCK_STATE_DIR=/tmp/focuslock-staging .venv/bin/python3 focuslock-mail.py`.

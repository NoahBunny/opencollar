# Manual QA — On-Device Physical Tests

Real-hardware smoke tests that Waydroid + staging mesh can't cover. Run before tagging any release. A release with any ❌ here is **not** shippable.

Target coverage: Android 13, 14, 15 — at minimum one real device per minor version.

---

## Pre-flight

- [ ] Fresh install from `apks/focuslock-v<N>.apk` + `bunnytasker-v<M>.apk` on a dev device (NOT bunny's production phone)
- [ ] Fresh install of `apks/focusctl-v<X>.apk` on a dev device for Lion's Share
- [ ] Both devices paired against staging mesh (see `docs/STAGING.md`), NOT prod
- [ ] Dev device's SIM is a cheap throwaway — SMS tests send real messages

---

## 1. Boot / autostart

- [ ] Install Collar → consent → pair. Verify notification is HIGH importance, non-dismissable.
- [ ] Reboot device. Within 30s of unlock, Collar HTTP server on :8432 is responsive (curl from laptop on same LAN).
- [ ] Collar's boot-complete receiver runs — lock state restored (if `lock_active=1` at boot, phone relocks within 10s).
- [ ] No launcher icon for Collar. No entry in "recent apps" tray. Only Bunny Tasker is visible.

## 2. Device admin / invisibility

- [ ] Settings → Apps → shows Collar as "Device admin active"
- [ ] Attempt to disable device admin → tamper penalty applied ($500), Lion notified
- [ ] Attempt to uninstall Collar → blocked by device admin
- [ ] Fossify Launcher shows only Bunny Tasker + system icons — no Collar

## 3. SMS trigger

- [ ] Send SMS from paired partner number: `sit-boy 30 $10` → Collar locks for 30m, adds $10 to paywall
- [ ] SMS from unknown number: no effect (sender filtered)
- [ ] SMS with bad syntax: `sit-boy garbage` → no effect, no crash
- [ ] Variant: `sit-boy 5` (no dollar amount) → locks 5m, paywall unchanged

## 4. Lovense integration

- [ ] Pair Lovense toy via Bunny Tasker → Collar sees connection
- [ ] Lion sends `vibrate` order → toy vibrates at specified intensity
- [ ] Toy disconnects mid-session → Collar logs gracefully, no crash
- [ ] Max-volume enforcement: audio HAL blocks volume-down key while locked

## 5. Camera / Photo Task

- [ ] Lion sends Photo Task with hint "show me your collar"
- [ ] Bunny takes photo → uploaded → Ollama `minicpm-v` receives it
- [ ] Match → task marked complete, unlock proceeds
- [ ] Mismatch (e.g., hint asks for collar, photo is a wall) → task rejected, feedback shown
- [ ] Ollama unreachable → photo queued, retries on next network cycle

## 6. Geofence / GPS

- [ ] Lion sets geofence at Bunny's current location, 100m radius
- [ ] Bunny walks ~200m outside → auto-lock fires within 30s (real GPS latency)
- [ ] Bunny walks back inside → lock state unchanged (geofence lock doesn't auto-release unless Lion clears)
- [ ] Airplane mode enabled → geofence can't evaluate, no false-positive lock (offline-safety rule)

## 7. Bedtime / curfew

- [ ] Set `bedtime_lock_hour=23, bedtime_unlock_hour=7`
- [ ] At 23:00 local time: device locks automatically
- [ ] At 07:00: device auto-unlocks
- [ ] Time zone change mid-cycle: bedtime respects new zone (phone's reported zone is authoritative)

## 8. Subscription + payment detection

- [ ] Subscribe to gold tier on Bunny Tasker
- [ ] Lion's IMAP creds configured via Lion's Share → server scans Lion's inbox
- [ ] Send real e-Transfer (from a test bank account) → within 2 min, payment detected, paywall cleared
- [ ] Send e-Transfer to wrong recipient (not Lion) → ignored (anti-self-pay)
- [ ] Over `max_payment` amount → rejected + flagged

## 9. Notification shade / status bar

- [ ] Paywall notification: HIGH importance, non-dismissable by swipe
- [ ] Lock notification: persistent while locked
- [ ] Shade is blocked while locked (rule-out: can't pull down to toggle wifi/bluetooth mid-lock)
- [ ] Status bar shows correct paywall amount

## 10. Release Forever + factory reset

- [ ] Lion taps Release Forever → dialog confirms
- [ ] On confirm: Collar self-uninstalls, Bunny Tasker self-uninstalls, device admin revoked, mesh state cleared
- [ ] Verify no residual Collar files in `/sdcard/` or `/data/data/` (requires adb root on dev device)
- [ ] 150 escape attempts → factory reset dialog appears
- [ ] Factory reset dialog: pressing "reset" actually wipes the device (don't test this on a device you need!)

## 11. Offline / recovery

- [ ] Airplane mode for 24h → Collar continues enforcement based on last known orders, no auto-unlock
- [ ] Restore network → orders sync within one gossip tick
- [ ] Battery pull mid-lock → on reboot, lock state restored (persisted in orders doc)

## 12. Upgrade path

- [ ] Current Collar version N → sideload N+1 → state preserved (orders, paywall, escape count all survive)
- [ ] Signing key rotation case: N+1 signed with new key → reinstall flow works (current version bumps have avoided this by keeping keystore stable)

---

## Exit criteria

Every section passes on at least one real device per target Android version (13, 14, 15). Any ❌ blocks the release — fix and re-run the affected section.

For release tagging: paste this checklist into the release PR with checkboxes filled in, linked to device model + Android version tested.

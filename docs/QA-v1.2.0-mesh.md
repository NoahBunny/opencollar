# v1.2.0 Mesh QA — 1 Lion + 3 Slaves

Thorough end-to-end test for the v1.2.0 release. Focus areas: **pairing / releasing, order propagation, lock / unlock, messaging**. Targets the audit C1 + C4 + C5 Android-side fixes.

This is the manual complement to the automated suite (353 pytest + 16 C1 parity) and to `docs/MANUAL-QA.md` (single-device fundamentals). **Do not** skip the single-device QA — this doc assumes those boxes are already ticked.

Release is shippable when every ☐ in §Release Criteria is ✓.

---

## Topology

One throwaway mesh, four Android surfaces:

| Role | Label | Preferred surface | Fallback |
|---|---|---|---|
| Lion | **L** | Physical phone (Pixel) with Lion's Share v68 | Waydroid instance |
| Slave 1 | **S1** | Physical phone (Pixel 10 `57261FDCR004ZQ` if available) with Collar v71 + Bunny Tasker v52 | Waydroid |
| Slave 2 | **S2** | Waydroid instance #1 | AVD Pixel_6_API_34 |
| Slave 3 | **S3** | AVD or second Waydroid | Anything sideloadable |

Why 3 slaves: order propagation, multi-recipient vault encryption, and gossip all need ≥3 nodes to exercise "not just direct sender + receiver." Two are insufficient — a bug that always routes via direct gossip to the same peer would pass a 2-node test.

Different Android versions between S1/S2/S3 is a bonus (each minor SDK has its own Settings.Global quirks). `adb shell getprop ro.build.version.release` per device, record in §Environment.

---

## Pre-flight

### Local workstation

1. Release APKs staged:
   ```
   mkdir -p /tmp/v120-qa && cd /tmp/v120-qa
   for APK in focuslock-v1.2.0.apk focusctl-v1.2.0.apk bunnytasker-v1.2.0.apk; do
     curl -sLO "https://github.com/NoahBunny/opencollar/releases/download/v1.2.0/$APK"
   done
   curl -sL https://github.com/NoahBunny/opencollar/releases/download/v1.2.0/APK-CERTS.txt
   ```
   Verify: `apksigner verify --print-certs *.apk` — cert SHA-256 matches `APK-CERTS.txt` on all three.

2. Throwaway mesh server running locally (do NOT hit prod):
   ```
   export FOCUSLOCK_STATE_DIR=/tmp/v120-qa/state
   export FOCUSLOCK_CONFIG=/tmp/v120-qa/config.json
   # write minimal config: see docs/STAGING.md "Gotchas" section
   .venv/bin/python focuslock-mail.py &  # or run in tmux
   curl -fsS http://127.0.0.1:8434/version  # smoke-test
   ```

3. Network: all 4 devices must reach the workstation on 8434 (mesh relay) and reach each other on 8432 (direct-HTTP). Waydroid's `192.168.240.x` subnet is bridged — confirm with `adb shell ip addr`.

### On each device

Factory reset or `pm clear` the three packages before starting:
```
adb -s <SERIAL> shell pm uninstall com.focuslock || true
adb -s <SERIAL> shell pm uninstall com.bunnytasker || true
adb -s <SERIAL> shell pm uninstall com.focusctl || true
```

Install v1.2.0 APKs per role:
- **L**: `adb -s <L> install focusctl-v1.2.0.apk`
- **S1–S3**: `adb -s <S> install focuslock-v1.2.0.apk && adb -s <S> install bunnytasker-v1.2.0.apk`

Grant device admin on slaves (manual tap, or for Waydroid: `adb shell dpm set-device-admin com.focuslock/.AdminReceiver`).

---

## §Environment (record before starting)

| Device | Role | Serial | Android | IP | Version installed |
|---|---|---|---|---|---|
| L | Lion | | | | focusctl v68 (68.0) |
| S1 | Slave 1 | | | | focuslock v71 (8.28) + bunnytasker v52 (2.19) |
| S2 | Slave 2 | | | | " |
| S3 | Slave 3 | | | | " |

---

## 1. Pairing

### 1a. Direct-pair fingerprint pin (Audit C5 regression)

1. On L: Setup → Add Bunny → Direct mode. Enter S1's IP + `:8432`. Enter S1's fingerprint (read from S1's Bunny Tasker pairing screen).
2. Result on L: `PAIRED direct (verified fp=<16 hex chars>)`.
3. **Negative test**: Retry pair with a wrong fingerprint (flip one hex char). Expected: L aborts pairing, shows both expected + received fingerprint. No `lion_pubkey` stored on S1.
   ```
   adb -s <S1> shell settings get global focus_lock_lion_pubkey  # expect "null" or empty
   ```
4. Clear and retry with blank fingerprint field. Expected: pair completes but status reads `PAIRED direct (UNVERIFIED fp=…)`.

☐ 1a passes

### 1b. QR-pair S2

1. On L: Add Bunny → QR scan. Point camera at S2's pairing screen.
2. Expected: pair succeeds, fingerprint verified from the QR payload itself (C5 bunny side was always well-designed).

☐ 1b passes

### 1c. Server-mode pair for S3

1. On L: Add Bunny → Server mode. Enter the local relay URL (`http://<workstation-lan-ip>:8434`) + mesh-create.
2. On S3: Bunny Tasker → Settings → Join mesh → paste invite.
3. Expected: both sides show joined, `focus_lock_mesh_id` set on S3.
   ```
   adb -s <S3> shell settings get global focus_lock_mesh_id
   ```

☐ 1c passes

---

## 2. Order propagation

### 2a. Lock order reaches every slave

L sends Lock 30m to S1 via direct HTTP:

1. On L: select S1 from bunny picker. Tap Lock → 30m → Basic mode → "test lock 2a".
2. Expected **within ~1 s** on S1: phone locks, `focus_lock_active=1`, timer ticks down.
   ```
   adb -s <S1> shell settings get global focus_lock_active          # "1"
   adb -s <S1> shell settings get global focus_lock_unlock_at        # 30*60*1000 in future
   adb -s <S1> shell settings get global focus_lock_message          # "test lock 2a"
   ```
3. Expected **within ~30 s (vault cycle)** on S2 and S3: same lock state propagates via vault gossip. Check the same three Settings.Global values on both.

☐ 2a passes, S1 within 1s, S2+S3 within 30s

### 2b. meshVersion monotonic + convergent

After 2a, capture `focus_lock_mesh_version` on all three slaves. Expected: same integer. If any slave lags ≥ 2 versions behind, gossip is broken.
```
for S in <S1> <S2> <S3>; do
  adb -s $S shell settings get global focus_lock_mesh_version
done
```

☐ 2b: all three match

### 2c. Unlock propagates

On L: Unlock S1. Repeat propagation check.
```
# all three should show focus_lock_active=0 within 30s
```

☐ 2c passes

### 2d. Multi-recipient vault write

Lock S1, immediately (<1s later) also send a Lock to S2. Expected: both locks commit, S3 sees both order blobs, no "lost update." Capture gossip log:
```
adb -s <S3> logcat -d 'FocusLock:I *:S' | grep 'vault: applied' | tail -20
```

☐ 2d: S3 log shows both applies

---

## 3. Lock / Unlock edge cases

### 3a. C1 signature gate (the big one)

All three of these must return `403 {"error":"signature required","min_controller_version":68}`:

```
# from workstation, hitting S1 on LAN
curl -sS -X POST http://<S1-IP>:8432/api/lock -H 'Content-Type: application/json' \
  -d '{"duration_min":60,"message":"attacker"}'

# from S1 itself via adb — localhost loopback
adb -s <S1> shell 'curl -sS -X POST http://127.0.0.1:8432/api/lock \
  -H "Content-Type: application/json" -d "{\"duration_min\":60}"'

# from S2 hitting S1 on LAN
adb -s <S2> shell 'curl -sS -X POST http://<S1-IP>:8432/api/release-forever \
  -H "Content-Type: application/json" -d "{}"'
```

☐ 3a: all three rejected with 403 signature required

### 3b. C1 replay protection

1. On L: Lock S1 for 5m with message "replay-source".
2. Proxy-capture (via `mitmproxy` / `tcpdump`) the signed POST. Extract the three `X-FL-*` headers.
3. Replay the exact same request within 30s → expected `403 replay`.
4. Wait 6 min and replay same headers → expected `403 stale_ts`.

☐ 3b: both negative cases fire

### 3c. Read-only endpoints still answer unsigned

```
curl -sS http://<S1-IP>:8432/api/ping          # {"ok":true}
curl -sS http://<S1-IP>:8432/api/status        # full JSON
curl -sS http://<S1-IP>:8432/api/adb-port      # int
```

☐ 3c: all three return 200, no signature needed

### 3d. Bunny-key signs through the same gate (same-phone path)

Trigger Bunny Tasker self-lock on S1 (tap self-lock → 5m). Expected:
- Lock fires (focus_lock_active=1)
- Collar logcat shows a C1-gate pass via the bunny pubkey branch (not lion):
  ```
  adb -s <S1> logcat -d FocusLock:I *:S | grep 'C1:' | tail -5
  # should NOT show "rejected"; absence of rejection = accepted
  ```

☐ 3d passes

---

## 4. Messaging

### 4a. Lion → S1 message round-trip

1. On L: open S1's inbox → send "hello S1".
2. Expected on S1: Bunny Tasker inbox shows the message within ~30s, `from=lion`, signature verified.
3. On S1, Bunny Tasker replies "ack from S1".
4. Expected on L: reply appears in lion's inbox within ~30s.

☐ 4a passes

### 4b. Mandatory-reply auto-lock (Audit C4 regression)

1. On L: send to S1 with "mandatory reply" checked, timer 4h. Set `ts` 5h in past (developer UI, or adb-post a crafted order).
2. Expected on S1: auto-lock fires on next vault tick because message is overdue.
3. **Negative**: craft a POST directly to the relay `/api/mesh/{id}/messages/send` with `from:"lion", mandatory:true, ts:old` but no signature (or wrong signature). Expected: relay rejects on receive (server already verifies). If somehow it did land, S1 must NOT auto-lock — `verifyLionMessageSignature` rejects before auto-lock.

☐ 4b both paths pass

### 4c. Message history isolation per-bunny

On L: switch bunny picker to S2. Expected: S1's messages are NOT visible in S2's thread.

☐ 4c passes

---

## 5. Releasing

### 5a. Per-device release

1. Pre-state: all 3 slaves paired to the mesh, paywall > 0 on each.
2. On L: Release S1 (target=pixel/device).
3. Expected: S1's `focus_lock_paywall` zeroed, `focus_lock_active=0`, but S2 and S3 unchanged.

☐ 5a passes

### 5b. Release-forever target=all (C2 + paywall-zero regression)

1. Set paywall to $100 on S2 and S3 (via Lion's Share → Add Paywall).
2. On L: Release Forever → target=all → confirm.
3. Expected: S2 and S3 both zero paywall, teardown proceeds, Collar APK uninstalls after delay. Orders doc on relay also shows `paywall=0` and `paywall_original=0` (not the pre-teardown value).
   ```
   curl -sS http://127.0.0.1:8434/api/mesh/<MESH_ID>/status -H "Authorization: Bearer <admin_token>" | jq '.paywall, .paywall_original'
   ```

☐ 5b passes

### 5c. Re-pair after release

After 5b, factory-reset S2 → re-install v71 → pair into same mesh. Expected: pairing succeeds, `lifetime_escapes` etc. reset to 0 (new device identity, new node_id).

☐ 5c passes

---

## 6. Regression sanity (P2 paywall hardening)

Fast tour — skip only if explicitly re-pairing a prior QA mesh.

- [ ] Escape x1 on S1 → `paywall +$5` within 30s (server-applied)
- [ ] Escape x4 total → `paywall = $25` ($5 + $5 + $5 + $10)
- [ ] Tamper attempt (deactivate admin on S1) → lock + `+$500`
- [ ] Geofence breach (spoof GPS on S2) → lock + `+$100` + `paywall_original=100`
- [ ] App-launch penalty: open Collar from launcher while locked → `+$50`, dedup within 10s
- [ ] Good-behavior: unlock with paywall>0, wait 10 min no escapes → `-$5`
- [ ] Compound interest: bronze tier, `paywall_original=100`, wait 2h → ~$121
- [ ] SMS `sit-boy 5 $20` from controller number → lock + `+$20`. `sit-boy 5 $9999` → clamped to `$500`
- [ ] Gamble from web UI → server RNG decides, result propagates
- [ ] Unsubscribe bronze from Bunny Tasker → `+$50`, `sub_tier` cleared

☐ All 10 pass

---

## §Release criteria

Every checkbox in §§1–6 is ✓. Any ❌ in §§1–3 (pairing / propagation / C1 gate) blocks the release — revert tag and cut a v1.2.1. ❌ in §§4–6 is investigated but may ship if a known issue is documented in CHANGELOG.

## §Teardown

```
# uninstall everything on test devices
for S in <L> <S1> <S2> <S3>; do
  for P in com.focuslock com.bunnytasker com.focusctl; do
    adb -s $S shell pm uninstall $P 2>/dev/null
  done
done

# nuke throwaway mesh state
rm -rf /tmp/v120-qa
kill $(pgrep -f 'python.*focuslock-mail')
```

If anything in §§1–3 failed, capture `logcat -d` from the failing device + the relay's stdout/stderr and attach to a new GitHub issue tagged `release-blocker`.

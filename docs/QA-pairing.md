# Pairing QA — Full Matrix

Ongoing regression suite for every pairing route. Run against a throwaway
mesh (see `docs/STAGING.md`). A pairing bug is the single most-user-
hostile failure mode in the stack — Bunny can't un-pair without Lion, Lion
can't unlock without pairing — so any ❌ here blocks merge of a branch
touching pairing code.

Complement to `docs/QA-v1.2.0-mesh.md §1` (release-specific snapshot) and
`docs/MANUAL-QA.md` (single-device fundamentals).

> **Why this is manual.** Programmatic UI automation for pairing was
> spiked in 2026-04 and shelved — see `docs/UI-AUTOMATION-DECISION.md`
> for the full rationale and the conditions under which we'd revisit.
> Until then, this checklist is the gate.

---

## Pre-flight

1. Staging mesh running on `127.0.0.1:8434` via throwaway state dir
   (`FOCUSLOCK_STATE_DIR=/tmp/pairing-qa/state`). `docs/STAGING.md` covers
   the full setup; the abbreviated form is:
   ```
   mkdir -p /tmp/pairing-qa/state && cd /tmp/pairing-qa
   export FOCUSLOCK_STATE_DIR=/tmp/pairing-qa/state
   export FOCUSLOCK_ADMIN_TOKEN="qa-admin-$(openssl rand -hex 8)"
   .venv/bin/python focuslock-mail.py &
   curl -fsS http://127.0.0.1:8434/version  # smoke
   ```
2. Two Android surfaces minimum:
   - **L** — Lion's Share (physical or Waydroid)
   - **S1** — Collar + Bunny Tasker (physical preferred for §4 + §6)
3. All devices reach the workstation on 8434 and each other on 8432.
4. `adb -s <S1> shell settings delete global focus_lock_lion_pubkey` (or
   reinstall the Collar APK) before §1 — a leftover pairing state from a
   previous run will fail the "fresh pair" happy path.

---

## §1 Direct / LAN pair

Lion's Share POSTs to Collar's `/api/pair` directly. No relay involved.

### 1a. Fresh pair happy path (C5 fingerprint pin)

1. On L: Add Bunny → "Pair Direct (LAN)". Enter S1's IP + `:8432`. Read
   S1's fingerprint off Bunny Tasker's pairing screen. Paste it.
2. Expected L status: `PAIRED direct (fingerprint verified): http://…`
3. Verify on S1:
   ```
   adb -s <S1> shell settings get global focus_lock_lion_pubkey
   ```
   Non-empty base64 DER matching L's `lion_pubkey`.

☐ 1a passes

### 1b. Fingerprint mismatch (MITM simulation)

1. Wipe `focus_lock_lion_pubkey` on S1 (`adb shell settings delete global …`).
2. On L: retry direct pair with one hex char of the fingerprint flipped.
3. Expected: L shows `Pair ABORTED: fingerprint mismatch. expected=… got=…`.
4. `focus_lock_lion_pubkey` on S1 must remain empty.

☐ 1b passes

### 1c. Blank fingerprint (backwards compat, warn-don't-abort)

1. Wipe `focus_lock_lion_pubkey` on S1.
2. On L: retry direct pair with the fingerprint field empty.
3. Expected: L shows `PAIRED direct (UNVERIFIED fp=<16 hex>): http://…`
   and logcat carries a `pairDirect: no expected fingerprint given` warning.

☐ 1c passes

### 1d. Idempotent re-pair (strengthening — new on this branch)

1. After §1a, immediately retry the same direct-pair flow on L (same
   fingerprint, same URL).
2. Expected: L status: `RE-CONFIRMED direct (fingerprint verified): …`
   (not "PAIRED" — the `"action":"already-paired"` branch on the Collar
   flipped the prefix). `focus_lock_lion_pubkey` on S1 unchanged.
3. Collar logcat: `doPair: idempotent re-pair from same lion key`.

☐ 1d passes

### 1e. Conflicting re-pair + Reset recovery (strengthening)

1. After §1a, on L go to Advanced → Bunnies and generate a **fresh**
   lion keypair (or use a second controller device).
2. On L: retry direct pair against S1 with the new lion pubkey.
3. Expected: L shows the `Already paired — reset required` AlertDialog
   with the Collar's hint text. Status line: `Pair blocked: Collar paired
   with another Lion key`. No change to `focus_lock_lion_pubkey`.
4. On S1's Bunny Tasker: tap the "Reset" button next to Join Mesh.
   Confirm the dialog. Status flips to `Pair state reset — Lion can now
   pair again`. Verify:
   ```
   adb -s <S1> shell settings get global focus_lock_lion_pubkey  # empty
   adb -s <S1> shell settings get global focus_lock_lion_last_seen  # 0
   ```
5. Retry the direct pair from L with the new lion pubkey → expected
   success `PAIRED direct (fingerprint verified): …`.

☐ 1e passes end-to-end

---

## §2 Server / mesh pair

Invite-code-based. Lion creates mesh, Bunny joins via Bunny Tasker's
"Join Mesh" dialog. This route uses `/api/mesh/{create,join}`, **not**
`/api/pair/{register,claim}` (those routes exist on the relay for
future / out-of-band consumers — see §2d for curl coverage of their
strengthened error responses).

### 2a. Happy path

1. On L: Setup → Create Mesh → staging URL. Copy the invite code.
2. On S1: Bunny Tasker → Join Mesh → paste invite. Enable vault if
   desired. Submit.
3. Expected: L + S1 both transition to paired UI within ~5s. Verify:
   ```
   adb -s <S1> shell settings get global focus_lock_mesh_id        # base64url
   adb -s <S1> shell settings get global focus_lock_mesh_node_id   # device model slug
   ```

☐ 2a passes

### 2b. Invite code already consumed

1. After §2a, reuse the same invite code on a second Bunny Tasker.
2. Expected: Join fails with `Error: {"error":"invite code already used"}`
   or similar 4xx body. S1's pairing state unchanged.

☐ 2b passes

### 2c. Invite code expired

1. Create a mesh on L and wait > 24h (or force expiry by editing the
   mesh account JSON in `/tmp/pairing-qa/state/meshes/<mesh_id>.json`
   and setting `invite_expires_at` to a past unix timestamp).
2. Attempt to join.
3. Expected: Join fails with `invite code expired`.

☐ 2c passes

### 2d. `/api/pair/claim` strengthened error shapes (curl-driven)

The relay's `/api/pair/{register,claim}` route has no Android consumer
today, but the response shapes are what a future Lion's Share client
(or a self-service script) will rely on. Drive with curl against the
local staging relay.

1. Unknown passphrase → 404 with `reason:"not_registered"` + hint
   mentioning Join Mesh:
   ```
   curl -sS -X POST http://127.0.0.1:8434/api/pair/claim \
     -H 'Content-Type: application/json' \
     -d '{"passphrase":"NEVER-BEEN-HERE","lion_pubkey":"x"}' | jq
   # expect: status 404, reason="not_registered", hint contains "Join Mesh"
   ```
2. Expired passphrase → 410 with `reason:"expired"` + hint naming the
   10-minute TTL:
   ```
   # register a fresh code:
   curl -sS -X POST http://127.0.0.1:8434/api/pair/register \
     -H 'Content-Type: application/json' \
     -d '{"passphrase":"TEST-TTL-ONE","pubkey":"b"}' | jq
   # force-expire by editing /tmp/pairing-qa/state/pair-registry.json
   # (or: wait 11 min)
   curl -sS -X POST http://127.0.0.1:8434/api/pair/claim \
     -H 'Content-Type: application/json' \
     -d '{"passphrase":"TEST-TTL-ONE","lion_pubkey":"x"}' | jq
   # expect: status 410, reason="expired", hint mentions "10 min"
   ```

☐ 2d: both 404 + 410 response bodies match the shape

### 2e. Case-insensitive passphrase

```
curl -sS -X POST http://127.0.0.1:8434/api/pair/register -H 'Content-Type: application/json' -d '{"passphrase":"wolf-42-bear","pubkey":"bp"}'
curl -sS -X POST http://127.0.0.1:8434/api/pair/claim    -H 'Content-Type: application/json' -d '{"passphrase":"WOLF-42-BEAR","lion_pubkey":"lp"}' | jq
# expect: ok=true, paired=true
```

☐ 2e passes

---

## §3 QR pair

Bunny Tasker displays a QR payload embedding `{bunny_pubkey, ip, port,
fingerprint}`; Lion's Share scans. This branch does NOT touch QR code.
See `docs/QA-v1.2.0-mesh.md §1b` for the QR flow — no new surfaces to
regress here.

☐ 3 confirmed still passing (linked to QA-v1.2.0 §1b)

---

## §4 Vault node approval

Once a mesh-paired Collar boots, it posts `POST
/vault/{mesh_id}/register-node-request` on every vault-sync tick until
Lion approves. Strengthening on this branch replaces the old flat 1h
throttle with an exponential backoff `[0, 1m, 5m, 15m, 60m]` and adds
the `/api/pair/vault-status/<mesh_id>` admin diagnostic.

### 4a. Slave registers after joinMesh → Lion approves → sync clears backoff

1. On S1 after §2a: vault-sync fires on the Collar (~30s cycle). Collar
   posts register-node-request.
2. Check server diagnostic:
   ```
   curl -sS "http://127.0.0.1:8434/api/pair/vault-status/<MESH_ID>?admin_token=$FOCUSLOCK_ADMIN_TOKEN" | jq
   # expect: counts.pending == 1, counts.approved == 0
   # pending[0].node_id matches S1's device slug (NOT "pixel" — see §4c)
   # pending[0].pubkey_hash is 16 hex chars
   # pending[0] does NOT contain node_pubkey field (stripped for support-thread safety)
   ```
3. On L: inbox → approve S1's vault node request.
4. Verify approval landed:
   ```
   curl -sS "http://127.0.0.1:8434/api/pair/vault-status/<MESH_ID>?admin_token=$FOCUSLOCK_ADMIN_TOKEN" | jq
   # expect: counts.approved == 1, counts.pending == 0
   ```
5. On S1 within the next vault-sync tick (≤30s):
   ```
   adb -s <S1> logcat -d FocusLock:I *:S | grep -E 'register backoff cleared|vault: applied' | tail -5
   # expect: "vault: register backoff cleared (slot landed)"
   ```

☐ 4a full round-trip passes

### 4b. Diagnostic auth gate

```
# no token → 403
curl -i http://127.0.0.1:8434/api/pair/vault-status/<MESH_ID> | head -1
# wrong token → 403
curl -i "http://127.0.0.1:8434/api/pair/vault-status/<MESH_ID>?admin_token=nope" | head -1
# Bearer header path also works
curl -sS -H "Authorization: Bearer $FOCUSLOCK_ADMIN_TOKEN" "http://127.0.0.1:8434/api/pair/vault-status/<MESH_ID>" | jq .counts
```

☐ 4b: 403 on bad auth, 200 + counts on Bearer header

### 4c. `node_id` sanity — no "pixel" fallback (strengthening)

Before this branch, a Collar that hadn't yet called joinMesh registered
under the hard-coded node_id `"pixel"`, collapsing every un-joined phone
onto one server-side identity. Now the fallback is gone.

1. `adb -s <S1> shell pm clear com.focuslock` (wipes Settings.Global).
2. Start the Collar's vaultSync without a mesh join (either via adb
   intent or by letting the boot receiver trigger it).
3. Logcat should show (within 30s):
   ```
   vault: no focus_lock_mesh_node_id set — skipping register-node-request …
   ```
4. The `/api/pair/vault-status/<any-mesh>` endpoint should NOT show a
   new "pixel" pending entry.

☐ 4c: no phantom `pixel` identity leaks

---

## §5 Recovery flows

### 5a. Half-completed pair (Lion's Share crashes mid-response)

1. On L: start direct-pair against S1. Before the response lands, kill
   Lion's Share (`adb -s <L> shell am force-stop com.focusctl`).
2. Verify S1 has the lion_pubkey stored but L has not committed:
   ```
   adb -s <S1> shell settings get global focus_lock_lion_pubkey   # set
   adb -s <L> shell run-as com.focusctl cat /data/data/com.focusctl/shared_prefs/focusctl_prefs.xml 2>/dev/null | grep lion_pubkey_b64
   ```
3. Re-open L and retry pair with SAME lion key → `RE-CONFIRMED direct`
   (§1d). Or retry with a different lion key → `Already paired — reset
   required` (§1e).
4. Reset on S1 recovers.

☐ 5a recovery paths work without reinstalling the Collar

### 5b. Passphrase expires on the relay

Covered by §2d. Observable recovery: Bunny generates a fresh code via
Bunny Tasker → Join Mesh.

☐ 5b: no orphan `expires_at < now` entries linger in the pairing
registry after claim_or_reason sees them (manually inspect
`/tmp/pairing-qa/state/pair-registry.json` → expired entries should be
cleaned on next registry `_save()`).

### 5c. Desktop collar pair into a different mesh after §4c change

Regression test for the `"pixel"` nodeId removal — desktops are not
affected (they always have an explicit node_id from `MESH_NODE_ID`), but
confirm:

1. Install Linux desktop collar via `installers/install-desktop-collar.sh`
   against the staging relay.
2. Verify its `register-node-request` shows up in the vault-status
   diagnostic under the expected node_id (hostname-based, not "pixel").
3. Approve from L → desktop collar sees vault blobs decrypt on next
   poll.

☐ 5c passes

---

## §6 Backoff schedule

The Collar's register-node-request is gated by
`VAULT_REGISTER_BACKOFF_MS = [0, 1m, 5m, 15m, 60m]`. Each failed attempt
walks one step up; a successful vault blob decrypt (meaning Lion has
approved us) clears the counter via `vaultClearRegisterBackoff`.

1. On S1 after wiping pairing state and re-joining a mesh, tail logcat:
   ```
   adb -s <S1> logcat -c  # flush
   adb -s <S1> logcat FocusLock:I *:S | grep 'vault: posted register-node-request'
   ```
2. **Do not approve yet on L.** Observe timestamp on consecutive log
   lines. The lines should include a `nextGapMin=` field. Sequence:
   - attempt=1, nextGapMin=1
   - attempt=2, nextGapMin=5 (fires ~1 min after attempt 1)
   - attempt=3, nextGapMin=15 (fires ~5 min after attempt 2)
   - attempt=4, nextGapMin=60 (fires ~15 min after attempt 3)
   - attempt=5+, nextGapMin=60 (caps at 60 min gaps)
3. Now approve on L → within one vault-sync cycle, logcat shows:
   `vault: register backoff cleared (slot landed)`.

☐ 6: gap progression is monotonic and caps at 60 min; approval clears

---

## Exit criteria

Every ☐ in §§1, 2, 4, 5, 6 ticked on at least one physical slave + one
Waydroid slave. §3 is linked to the existing v1.2.0 doc. Any ❌ blocks
merging a PR that touches pairing code.

For release-tagging: paste the filled-in checkboxes into the PR along
with device model + Android version tested.

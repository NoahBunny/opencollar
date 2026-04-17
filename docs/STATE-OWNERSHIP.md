# State Ownership Matrix

**Status:** DRAFT 2026-04-15 — landing zone for the "move more state server-side" initiative triggered by the subscription fix (landmine #20).

**Goal:** one row per piece of runtime state, making it obvious who owns the write path, what propagates via the mesh, what's phone-local on purpose, and what's a candidate for migration. Future sessions pick rows off the "migrate" column.

## Reading guide

Columns:

| Column | Meaning |
|---|---|
| **Field** | Canonical name. For Settings.Global, the `focus_lock_` prefix is dropped; for server, the bare orders-doc key. |
| **Writer** | The component that *truthfully* generates the value. "Phone" = Collar or Bunny Tasker. "Server" = focuslock-mail.py. "Lion" = Lion's Share via server. "Bunny" = Bunny Tasker user action. |
| **Reader(s)** | Who enforces or displays it. |
| **Propagation** | `vault` (encrypted vault blob), `gossip` (legacy /mesh/sync plaintext, now mostly 410'd), `phone-local`, `server-only`, `direct-IP` (phone's own HTTP endpoint). |
| **Target** | Where we think it *should* live. `✓ current` = good as-is. `→ server` = migrate authority to server. `→ hybrid` = server-replicated state + phone offline-safe writes. |
| **Priority** | P0 (data loss bug today), P1 (meaningful UX gap), P2 (nice-to-have), skip (deliberately phone-local). |

**Global constraints that shape every decision:**

- **Vault quota**: 100 MB per mesh, 5 000 blobs/day. Tiny fields (timestamps, counters, tier enums) are fine. Message bodies, photos, logs — NOT candidates. See `VAULT-DESIGN.md`.
- **Offline tolerance**: phone may be offline for days (`feedback_offline_no_lock.md`). Enforcement state that *acts autonomously* (geofence breach, countdown lock, bedtime) MUST work offline → it can be mesh-replicated but can't be server-only. Server-only works for state the bunny *requests* (subscribe, pay) where the write can wait.
- **Direct-IP fallback**: Lion's Share and admin tools should prefer `https://focus.wildhome.ca` (canonical), then fall back to the phone's own `:8432` HTTP server for reads when the relay is down. The `feedback_https_first.md` rule still holds — we only drop to direct-IP when focus.wildhome.ca is unreachable.
- **Signed orders**: any mesh-authoritative field must be written by a signer the readers trust (Lion, relay, or an approved vault node). The subscription migration uses the relay key; bunny-initiated writes sign with the bunny's registered `bunny_pubkey`.

---

## Worked example — subscription (fixed 2026-04-15, landmine #20)

Used here as the template for future migrations.

| Field | Before | After |
|---|---|---|
| `sub_tier` | Bunny Tasker wrote Settings.Global only | Server writes orders doc + vault blob; Collar projects |
| `sub_due` | Bunny Tasker wrote +7d locally | Server `subscribe-charge` advances +7d each charge |
| `sub_total_owed` | Not tracked | Server cumulative, replicated to phone |
| `sub_last_charged` | Not tracked | Server dedup guard (1 h minimum between charges) |
| Charge trigger | Collar poll loop (duplicated per device) | Server 60 s thread, single source |
| Subscribe action | `Settings.Global.putString` | `POST /api/mesh/{id}/subscribe` signed by `bunny_pubkey` |

**Template for "→ server":** (1) add server action in `mesh_apply_order`, (2) route server-initiated writes through `_server_apply_order()` (updates orders doc + writes vault blob + gossips to operator-mesh peers), (3) add handler case in Collar's `handleMeshOrder()` so vault RPC blobs project correctly, (4) for bunny-initiated writes, add a `/api/mesh/{id}/{action}` endpoint that verifies against `bunny_pubkey`, (5) delete the phone-local write path.

---

## Category A — Billing & payment

| Field | Writer | Readers | Propagation | Target | Priority | Notes |
|---|---|---|---|---|---|---|
| `sub_tier` | Server | Collar, Bunny, Lion | vault | ✓ current | done | |
| `sub_due` | Server | Collar, Bunny, Lion | vault | ✓ current | done | |
| `sub_total_owed` | Server | Collar, Bunny | vault | ✓ current | done | |
| `sub_last_charged` | Server | Server | server-only | ✓ current | done | Intentionally not on phone — no enforcement use. |
| `paywall` | Server | All | vault | ✓ current | done | **Migrated 2026-04-17**: server applies all enforcement-driven increments (escape tier, tamper attempt/detected/removed, geofence breach, app-launch penalty, good-behavior reward, compound interest). Phone is a pure event reporter — see row 8 of the migration roadmap. |
| `paywall_original` | Server | Collar | vault | ✓ current | skip | Snapshot on lock engage; already server-authored. |
| `total_paid_cents` | Server | All | vault | ✓ current | done | **Migrated 2026-04-15**: added to `ORDER_KEYS` + Collar's `MESH_ORDER_KEYS`; new `payment-received` action increments it + optionally clears paywall; `check_payment_emails` wired via `apply_fn` kwarg to `_server_apply_order`. Smoke-tested. |
| `payment_ledger.json` | Server | Server | server-only | → replicate | P1 | Full history lives at `/run/focuslock/payment_ledger.json` on pegasus. Add a bunny-authed `GET /api/mesh/{id}/payments?since=...` endpoint so Bunny Tasker can show "Payments" tab with history. No write path needed from phone; IMAP bot is the sole writer. |
| `banking_app` | Bunny | Bunny | phone-local | ✓ current | skip | Routing for the tribute button — phone-local is fine. |
| `bank_packages` / `payment_keywords` | Collar | Collar | phone-local | ✓ current | skip | SMS/email filters for payment detection; per-locale, can stay local. |

## Category B — Lock state & enforcement

All already in MESH_ORDER_KEYS (server-authoritative, vault-propagated). Keeping here for completeness — no migration needed, but worth auditing occasionally for dual-write bugs.

| Field | Writer | Notes |
|---|---|---|
| `lock_active`, `locked_at`, `unlock_at` | Server (via Lion) | ✓ |
| `mode`, `message`, `compliment`, `exercise`, `word_min` | Server | ✓ |
| `task_text`, `task_orig`, `task_randcaps`, `task_reps`, `task_done` | Server (set on lock); Collar mutates `task_done` | ⚠ Collar writes `task_done=1` on completion — verify this writes back to mesh via `meshBumpAndPush()`. |
| `photo_task`, `photo_hint` | Server (via Lion) | Prompt text only — *photo blobs* are out-of-band via `/webhook/evidence-photo`, NOT vault (correct — too big). |
| `entrapped`, `released`, `release_timestamp` | Server | ✓ |
| `free_unlocks`, `free_unlock_reset` | Server | ✓ |
| `desktop_active`, `desktop_locked_devices`, `desktop_message` | Server (Lion) | ✓ |
| `settings_allowed` | Server (Lion) | ✓ |
| `vibrate`, `penalty`, `shame`, `dim`, `mute` | Server (Lion) | Behavioral flags; ✓ |

### Sub-category B1 — Escape counter & paywall penalties

| Field | Writer | Readers | Propagation | Target | Priority | Notes |
|---|---|---|---|---|---|---|
| `escapes` | Collar | Collar, Bunny | phone-local | → hybrid | P2 | Count increments locally on escape detection (fast, offline). Push event + count to server periodically so Lion sees it even if phone goes offline. Similar to how `sub_last_charged` is server-side bookkeeping. |
| `admin_tamper`, `admin_removed` | Collar | Collar, Lion (via status poll) | phone-local + direct | → hybrid | P1 | Tamper events must reach Lion even if the phone is trying to hide. Phone pushes an event blob on tamper detection; server logs. No reverse flow. |
| `auth_challenge`, `auth_challenge_desc` | Collar | Collar | phone-local | ✓ current | skip | Short-lived challenge state — phone-local is correct. |

## Category C — Messaging

| Field | Writer | Readers | Propagation | Target | Priority | Notes |
|---|---|---|---|---|---|---|
| `message_history` (JSON array) | Collar | Collar | phone-local | → server | P1 | Currently Settings.Global JSON blob, capped at 200 msgs. Migrate to a server-side log file (append-only per mesh); Lion's Share and Bunny Tasker both fetch via paged API. Individual messages already propagate encrypted via vault RPC (send-message action); the HISTORY is the gap. |
| `bunny_last_msg` | Collar | Collar | phone-local | → server | P1 | Included in message-history migration. |
| `pinned_message`, `lion_pinned_message` | Server (Lion) | All | vault | ✓ current | skip | Small, singleton, already correct. |

**Vault size check:** 200 messages × ~200 chars avg = 40 KB. Well under per-blob limit. But if messages become a history-as-orders field, every message mutates orders_version → lots of churn. Better: store history in a *separate* per-mesh append log on the server, fetched on demand. Orders doc just holds the most recent message for display.

## Category D — Streaks

All currently in MESH_ORDER_KEYS — **server-authoritative on paper**. But the escape-triggered reset (which is the whole point of a streak) happens *on the phone* (`ControlService` decrements streak on escape). The server doesn't know the streak broke until the phone gossips.

| Field | Writer | Readers | Propagation | Target | Priority | Notes |
|---|---|---|---|---|---|---|
| `streak_enabled`, `streak_start`, `streak_escapes_at_start` | Server (Lion sets; Collar resets on escape) | All | vault | → server | P1 | Migrate reset logic to server. Phone posts escape events via a bunny-authed `/api/mesh/{id}/escape-event` endpoint; server decides whether to reset streak. Avoids divergence when phone is offline during escape but streak bonus should still fire on the server's schedule. |
| `streak_7d_claimed`, `streak_30d_claimed` | Server (bonus loop) | All | vault | ✓ current | skip | Needs a server-side streak bonus thread (analogous to `check_subscription_charges`) that credits paywall on 7-day/30-day anniversaries. Part of P1 above. |

## Category E — Geofence, curfew, bedtime

Enforcement-critical and must work offline.

| Field | Writer | Readers | Propagation | Target | Priority | Notes |
|---|---|---|---|---|---|---|
| `geofence_lat`, `geofence_lon`, `geofence_radius_m` | Server (Lion) | Collar | vault | ✓ current | skip | Configuration only — Collar enforces locally from its GPS. |
| `geofence_breach_at` | Collar | Collar, Lion (status poll) | phone-local | → hybrid | P2 | Breach events should reach Lion. Like escape events — phone pushes event blob to server, server logs, Lion's Share shows a timeline. |
| `curfew_*` (6 fields) | Server (Lion) | Collar | vault | ✓ current | skip | Config; Collar enforces. |
| `bedtime_*` (3 fields) | Server (Lion) | Collar | vault | ✓ current | skip | Config; Collar enforces. |
| `bedtime_locked` | Collar | Collar | phone-local | ✓ current | skip | Edge-triggered flag, clears on exit window. |

## Category F — Fines, tributes, body checks

| Field | Writer | Notes |
|---|---|---|
| `fine_active`, `fine_amount`, `fine_interval_m`, `fine_last_applied` | Server | ✓ — server thread `check_tributes_and_fines` already charges. Confirm it writes vault blob under vault_only (may be gossip-only path currently — see #12 history). |
| `tribute_active`, `tribute_amount`, `tribute_last_applied` | Server | Same thread; same verification needed. |
| `body_check_*` (7 fields) | Server (Lion sets schedule); Collar writes `body_check_last_result` after photo verify | Mixed. Phone→server flow for result + streak is needed — verify it's pushing via mesh, not just local. |

**Action:** audit `check_tributes_and_fines()` for vault blob writes under `vault_only` meshes. Probably a hidden #12-style landmine — logs may show orders.set + bump but no `_admin_order_to_vault_blob` call.

## Category G — Device admin, screen-time, counters

| Field | Writer | Target | Priority | Notes |
|---|---|---|---|---|
| `screen_time_quota_minutes`, `reset_hour` | Server (Lion config) | ✓ current | skip | Config replicated. |
| `screen_time_used_today`, `last_check`, `reset_date` | Collar | ✓ current | skip | Counter that increments locally; daily reset is a local cron. Pushing to server would be cheap but serves no cross-device value unless the bunny has 2 phones. |
| `free_unlocks` | Server (bonus thread resets weekly) | ✓ current | skip | Already in MESH_ORDER_KEYS. Verify weekly reset thread exists on server. |
| `gamble_result` | Collar (Double or Nothing RNG) | phone-local | skip | Local; ephemeral. |
| `countdown_*_warn_tier`, `countdown_last_warn` | Collar | phone-local | skip | Escalation state for countdown notifications; local is correct. |
| `lovense_available` | Collar | phone-local | skip | Hardware detection. |

## Category H — Mesh plumbing, pairing, crypto

| Field | Writer | Readers | Target | Notes |
|---|---|---|---|---|
| `mesh_id`, `mesh_url`, `pin` | Bunny (on join), then immutable | All | ✓ current | Pairing token. Only rewritten on re-enslave. |
| `mesh_node_id` | Bunny | All | ✓ current | Must be stable for slot tracking. |
| `mesh_version` | Collar | Collar | ✓ current | Local counter tracking last applied orders version. |
| `mesh_peers` | Collar | Collar | phone-local | ✓ current | Peer discovery cache; rebuilt from gossip. |
| `node_pubkey`, `node_privkey`, `node_privkey_legacy` | Collar (KeyStore + Settings.Global fallback) | Collar | phone-local | ✓ current | Per-device signing keypair. Regenerated on factory reset. |
| `bunny_pubkey`, `bunny_privkey` | Bunny Tasker | Bunny + server (pubkey only) | phone-local privkey | ✓ current | Privkey must not leave phone. Server has pubkey via join flow — used now for `/api/mesh/{id}/subscribe` verification. Potential privkey-in-Settings.Global concern flagged in landmine #9. |
| `lion_pubkey` | Bunny (stored on join) | All | ✓ current | Trust anchor. |
| `vault_mode`, `vault_last_register_req` | Bunny | Collar | phone-local | ✓ current | Per-device opt-in + throttle. |

## Category I — Config and metadata (intentionally phone-local)

Skip column — these stay phone-local on purpose.

`banking_app`, `bank_packages`, `payment_keywords`, `adb_wifi_port`, `controller_number`, `consent_time`, `consented`, `ntfy_server`, `ntfy_topic`, `webhook_host`, `prior_home_pkg`, `saved_volume_ring/notif/generic`, `release_authorized`, `bridge_heartbeat`

Rationale: per-device config, no cross-device value, often populated during install/consent from local environment (launcher detection, volume levels pre-lock).

---

## Migration roadmap (suggested order)

Each row is one session's work, following the Category-A worked example as the template.

1. ✅ **[P0] `total_paid_cents` → server-authoritative** — DONE 2026-04-15.

2. **[P1] Payment history API**. New `GET /api/mesh/{id}/payments?since=...` endpoint, bunny-authed. Bunny Tasker gets a payments timeline. No orders-doc changes; separate endpoint because history is large. One session.

3. **[P1] Audit & fix `check_tributes_and_fines()` for vault blob writes**. Probably a #12-style landmine. Grep, add `_admin_order_to_vault_blob()` calls, verify propagation end-to-end. Half-session.

4. **[P1] Escape & tamper event push**. Phone-side `/api/mesh/{id}/escape-event` (bunny-authed). Server logs, increments a `lifetime_escapes` counter in orders, optionally triggers escalations. One session.

5. **[P1] Streak bonuses fire server-side**. New background thread like `check_subscription_charges` — credits paywall on 7-day/30-day anniversaries, resets streak on escape event received. Requires #4 first. One session.

6. **[P1] Message history server-side**. Append-only log per mesh. New endpoint for paged fetch. Bunny Tasker and Lion's Share both rewire. Two sessions (one server, one clients).

7. **[P2] Geofence breach event push**. Piggy-backs on #4's event infrastructure. Half-session.

8. ✅ **[P2] Harden `paywall` writes to server-only** — DONE 2026-04-17. Collar + companion no longer mutate `focus_lock_paywall` on escape / tamper (attempt / detected / removed) / geofence breach / app-launch / good-behavior / compound-interest; server applies amounts via `escape-recorded` (tiered $5×tier), `tamper-recorded` (attempt/detected $500, removed $1000), `geofence-breach-recorded` (+$100), `app-launch-penalty` (+$50 with 10s endpoint dedup), `good-behavior-tick` (-$5 in the tribute/fine loop), and a new `check_compound_interest()` thread that fires `compound-interest-tick`. Penalty amounts live in `shared/focuslock_penalties.py` for a single source of truth. Slave v69, companion v49. Follow-ups deferred: SMS sit-boy, local `/api/paywall` HTTP route, gamble/unsubscribe bunny-initiated paths, Release-Forever zeroing.

## Known non-candidates (never migrate)

- Photo blobs — vault quota prohibits. Already go out-of-band via `/webhook/evidence-photo`.
- Video/audio artifacts — same.
- Per-device config (Category I).
- Node-specific keypairs (privkey must stay on device).

## Open questions

- **Conflict policy for dual-written fields (`paywall`):** last-writer-wins by `updated_at`, or single-writer? Leaning single-writer (server) per Category B1.
- **Offline write queue:** if phone posts an event while relay is unreachable, do we buffer + replay on next connect, or drop? Leaning buffer-then-replay with a cap (10 events, oldest dropped) so catastrophic offline periods don't blow up the first-reconnect sync.
- **Multi-bunny**: matrix assumes one bunny per mesh. If a mesh ever has 2+ bunny devices, Category A-C assumptions change (per-bunny subscription, per-bunny streaks).

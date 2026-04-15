# QA Checklist

End-to-end regression matrix for The Collar / Lion's Share / Bunny Tasker. Run against a **staging mesh** (see `docs/STAGING.md`) — never against your production relay or live configs.

Mark each row ✅ (pass) or ❌ (fail). A run with any ❌ blocks release tagging.

**Scope** — Phase 3 of `docs/PUBLISHABLE-ROADMAP.md`. The scriptable subset (everything that doesn't need real radios) runs in CI; the rest is an on-device manual pass before each release.

---

## 0. Pre-flight

| # | Check | Environment |
|---|-------|-------------|
| 0.1 | Staging `config.json` loaded on server — `mesh_id` != prod | server |
| 0.2 | Staging `ntfy_topic` != prod topic | server |
| 0.3 | Server bound to `127.0.0.1:8435` (or `staging.*` subdomain), NOT `0.0.0.0` | server |
| 0.4 | Waydroid #1 (bunny) networked to staging relay | Waydroid |
| 0.5 | Waydroid #2 (lion) networked to staging relay | Waydroid |
| 0.6 | `pytest --cov` green (local) — baseline passing | dev |
| 0.7 | `ruff check .` green | dev |

---

## 1. First-run consent + pairing

| # | Check | Actor |
|---|-------|-------|
| 1.1 | Consent screen shown on Collar first-run | Waydroid #1 |
| 1.2 | Consent screen references DISCLAIMER.md content | Waydroid #1 |
| 1.3 | "I do NOT consent" button uninstalls app cleanly | Waydroid #1 |
| 1.4 | QR pairing flow: Lion's Share → Companion scans → paired | both |
| 1.5 | Mesh orders sync from Lion to Bunny within 10s of pairing | staging relay |
| 1.6 | Direct pair (LAN, no relay) — skip relay, QR displays direct URL | Waydroid |

## 2. Lock / Unlock — core

| # | Check |
|---|-------|
| 2.1 | Lion's Share sends `lock` — Bunny's Collar locks within 10s (gossip) |
| 2.2 | Lion's Share sends `lock` — Bunny's Collar locks within 1s (ntfy push) |
| 2.3 | Timer lock: N minutes — auto-unlock fires at `unlock_at` |
| 2.4 | Manual `unlock` order clears lock immediately |
| 2.5 | Lock survives Collar process restart (persisted in mesh orders) |
| 2.6 | Lock survives phone reboot (Collar boot-complete receiver) |
| 2.7 | "Release Forever" from Lion's Share tears down + auto-uninstalls |

## 3. Paywall + compound interest

| # | Check |
|---|-------|
| 3.1 | `add-paywall $X` — Bunny sees $X on paywall screen |
| 3.2 | Compound interest 10%/hr accrues — verify after 1 hour stubbed time |
| 3.3 | Subscription reduces compound rate — gold tier overrides 10% → tier-specific |
| 3.4 | Partial payment reduces paywall, does NOT unlock |
| 3.5 | Full payment clears paywall AND unlocks |
| 3.6 | `clear-paywall` order zeros paywall (Lion-authorized only) |
| 3.7 | Paywall >$0 + Collar lock — phone unresponsive except paywall view |
| 3.8 | Escape penalty $5/$10/$15+ stacking on repeat attempts |

## 4. Payment detection (per-region)

Mock bank emails for each supported region — fixtures in `tests/fixtures/bank_emails/`.

| # | Region | Sample provider | Check |
|---|--------|-----------------|-------|
| 4.1 | Canada | Interac / Tangerine | e-Transfer detected, amount + CAD parsed |
| 4.2 | USA | Zelle / Venmo / Cash App | "You received $X" parsed |
| 4.3 | UK | Barclays / Monzo / Starling | GBP faster-payment parsed |
| 4.4 | EU (DE/FR/NL/IT/ES/BE) | SEPA / PayPal.XX | EUR + transfer keyword in language |
| 4.5 | Australia | CommBank / ANZ | AUD deposit parsed |
| 4.6 | Brazil | Pix notification | BRL parsed |
| 4.7 | India | UPI / PhonePe / Paytm | INR parsed |
| 4.8 | Japan | LINE Pay / PayPay | JPY (no decimals) parsed |
| 4.9 | Singapore | PayNow | SGD parsed |
| 4.10 | Mexico | SPEI / BBVA | MXN parsed |
| 4.11 | South Africa | Capitec / FNB | ZAR parsed |
| 4.12 | South Korea | KakaoPay | KRW parsed |
| 4.13 | Hong Kong | FPS | HKD parsed |
| 4.14 | Nordic | Swish / MobilePay | SEK/NOK/DKK parsed |
| 4.15 | UAE | Emirates NBD | AED parsed |
| 4.16 | Anti-self-pay | Bunny sends self money | REJECTED — verified by Lion-inbox scan |
| 4.17 | Duplicate Message-ID | Same email twice | Second ignored (ledger dedup) |
| 4.18 | Above `max_payment` | Forged $99999 email | Rejected + logged as suspicious |
| 4.19 | Below `min_payment` | $0.001 micro-transaction | Rejected silently |
| 4.20 | Chat false-positive | "wanna grab lunch $20" | Does NOT trigger any provider |

## 5. Lock modes (all 9)

| # | Mode | Check |
|---|------|-------|
| 5.1 | Basic | Simple lock+timer, no puzzle |
| 5.2 | Negotiation | Bunny submits offer → Lion accepts/denies |
| 5.3 | Task | Text task displayed, Bunny types completion, LLM judges |
| 5.4 | Compliment | Word-min compliment → sent to Lion via evidence email |
| 5.5 | Gratitude Journal | 3+ gratitude entries required |
| 5.6 | Exercise | "Do 20 pushups" etc — timer + self-confirm |
| 5.7 | Love Letter | Long-form text with sentiment check |
| 5.8 | Photo Task | Photo uploaded → Ollama `minicpm-v` evaluates matches hint |
| 5.9 | Random | Picks one of the above each unlock |

## 6. Subscriptions (gold / silver / bronze)

| # | Check |
|---|-------|
| 6.1 | Subscribe to gold — `sub_tier = "gold"`, `sub_due = now + 7d` |
| 6.2 | Pre-pay during active cycle — `sub_due` stays now+7d (pre-pay forfeits remainder) |
| 6.3 | Sub due date crossed — `sub_total_owed` accrues from 0 to subscription amount |
| 6.4 | Payment detected after due — owed cleared, cycle renewed |
| 6.5 | 48h reminder → 6h reminder cascade |
| 6.6 | Subscription reduces paywall compound rate correctly |

## 7. Geofence + curfew + bedtime

| # | Check |
|---|-------|
| 7.1 | `set-geofence` with lat/lon/radius — Bunny location inside = no action |
| 7.2 | Bunny crosses geofence boundary outbound — auto-lock triggers |
| 7.3 | Bunny re-enters geofence inbound — lock clears (if Lion allowed) |
| 7.4 | Curfew: `confine_hour=22, release_hour=7` — auto-lock at 22:00 local |
| 7.5 | Bedtime enforcement: `bedtime_lock_hour=23` → phone locks, auto-unlocks at `bedtime_unlock_hour` |
| 7.6 | Homelab unreachable → geofence does NOT auto-lock (offline safety) |

## 8. Vault / mesh crypto

| # | Check |
|---|-------|
| 8.1 | Encrypt order Lion → Bunny — Bunny decrypts successfully |
| 8.2 | Encrypt order Lion → Desktop — Desktop decrypts successfully |
| 8.3 | Encrypt order Lion → Bunny+Desktop — both decrypt |
| 8.4 | Non-recipient node cannot decrypt (intercepted blob) |
| 8.5 | Tampered ciphertext — decrypt fails (AES-GCM auth tag) |
| 8.6 | Signature tampered — verify fails |
| 8.7 | MGF1-SHA256 legacy fallback — pre-v57 peer's blobs still decrypt |
| 8.8 | Key rotation — old blobs unreadable by new key (expected) |
| 8.9 | Relay never sees plaintext — tcpdump confirms |

## 9. Mesh gossip + convergence

| # | Check |
|---|-------|
| 9.1 | 2-node mesh (phone + desktop): orders converge within 10s |
| 9.2 | 3-node mesh: all three converge after partition heal |
| 9.3 | Peer address cap — 8 addresses posted, only 4 stored |
| 9.4 | Working address promoted to front of peer list |
| 9.5 | Non-whitelisted peer rejected (warren whitelist) |
| 9.6 | Unsigned orders rejected when `lion_pubkey` configured |
| 9.7 | Replayed stale orders (lower version) rejected |

## 10. ntfy push

| # | Check |
|---|-------|
| 10.1 | Lion sends order — ntfy push delivered within 1s |
| 10.2 | ntfy payload contains only `{"v": N}` — zero-knowledge verified |
| 10.3 | ntfy unavailable — gossip still converges (best-effort fallback) |
| 10.4 | Self-hosted ntfy server works (not just ntfy.sh) |

## 11. Desktop collar (Linux + Windows)

| # | Check | OS |
|---|-------|----|
| 11.1 | Linux collar locks session via `loginctl lock-session` | Linux |
| 11.2 | Linux collar vault_mode polls vault endpoint, decrypts, applies | Linux |
| 11.3 | Windows collar locks via `LockWorkStation` | Windows |
| 11.4 | Windows collar watchdog respawns if process killed | Windows |
| 11.5 | Desktop heartbeat reaches server every 10s | both |
| 11.6 | 7d without heartbeat — Lion notified via pinned message | server |
| 11.7 | 14d without heartbeat — $50 penalty applied | server |

## 12. Escape + factory reset + consent revocation

| # | Check |
|---|-------|
| 12.1 | 1st escape attempt: $5 penalty |
| 12.2 | 2nd escape: $10 |
| 12.3 | 3rd+: $15 each |
| 12.4 | 150 escapes: factory reset dialog |
| 12.5 | Admin tamper attempt: +$500 |
| 12.6 | Admin removal: +$1000 (stacking) |
| 12.7 | Release Forever (Lion only): full teardown + auto-uninstall |
| 12.8 | Lion offline — Release Forever UNAVAILABLE (safety) |

## 13. Admin API + web UI

| # | Check |
|---|-------|
| 13.1 | QR login flow: phone scans QR → session approved → web logged in |
| 13.2 | Session token TTL 8h — expires → web re-login |
| 13.3 | Master ADMIN_TOKEN never handed to web |
| 13.4 | Paywall PreToolUse hook (if enabled) blocks when paywall > 0 |
| 13.5 | Signup flow: new mesh creation via `/web/signup` |

## 14. Stuff Waydroid can't cover — on-device manual

See `docs/MANUAL-QA.md` for the on-phone checklist. These require real hardware:

- [ ] Real SMS trigger: "sit-boy 30 $10" from partner's number → lock activates
- [ ] Real Lovense BT pairing + `vibrate` order → toy responds
- [ ] Real camera: Photo Task captures + Ollama evaluates match
- [ ] Real GPS: geofence triggers when physically moving
- [ ] Real max-volume enforcement (audio HAL)
- [ ] Real boot-complete receiver fires after device reboot
- [ ] Collar invisibility: no launcher icon, no recent-apps entry
- [ ] Shade/notification blocking: rule-out via physical-device status bar test

---

## Exit criteria

- Every row in sections 0-13 passes on a fresh Waydroid pair against staging.
- Section 14 (manual on-device) passes on at least one real phone per target OS minor version (Android 13, 14, 15).
- Any ❌ blocks tagging a release. Document the failure in the release PR and re-run after fix.

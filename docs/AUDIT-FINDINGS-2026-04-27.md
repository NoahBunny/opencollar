# Stream A — Security Audit Findings (2026-04-27)

Owner: this session.
Scope: `docs/AUDIT-PLAN.md` Stream A (security review).
Methodology: Inventory → walk against threat model (`docs/THREAT-MODEL.md`) → triage → this report.

The 2026-04-17 audit closed C1–C6. The 2026-04-24 hands-on QA closed a class of multi-tenant correctness bugs. This pass re-audits the codebase ten days later and surfaces both **regressions of past concerns** and **previously-unenumerated issues** in the same bug class.

---

## Surface inventory

**Relay (`focuslock-mail.py`, port 8434, binds 0.0.0.0)** — 41 routes:
- POST: `/webhook/{compliment, gratitude, love_letter, entrap, offer, location, geofence-breach, evidence-photo, verify-photo, generate-task, subscription-charge, bunny-message, desktop-penalty, controller-register, desktop-heartbeat, register}`; `/admin/{disposal-token, order}`; `/api/{logout, mesh/create, mesh/join, pair/register, pair/claim, pair/lookup, pair/create, web-session}`; `/admin/web-session`; `/api/mesh/{id}/{auto-accept, subscribe, unsubscribe, gamble, payments, escape-event, state-mirror, deadline-task/clear, messages/{send, fetch, mark, edit, delete}}`; `/vault/{id}/{append, register-node, reject-node-request, register-node-request}`.
- GET: `/mesh/ping`, `/version`, `/api/web-session/{id}`, `/admin/web-session/{id}`, `/web-login`, `/admin/status`, `/vault/{id}/{since, nodes, nodes-pending}`, `/controller`, `/standing-orders`, `/enforcement-orders`, `/settings`, `/pubkey`, `/api/pair/{status, vault-status, code}`, `/api/paywall`, `/memory`, `/`, `/index.html`, `/signup`, `/cost`, `/trust`, `/qrcode.min.js`, `/manifest.json`, `/collar-icon.png`.

**Slave (Android, `ControlService.java`, port 8432/8433, binds wildcard)** — `/api/{ping, status, adb-port, lock, unlock, message, task, power, offer, offer-respond, add-paywall, enable-settings, entrap, clear-paywall, gamble, play-audio, set-geofence, pin-message, subscribe, unsubscribe, free-unlock, lovense, photo-task, release-forever, pair, set-checkin, clear-geofence, set-volume, set-notif-prefs, lock-device, unlock-device, speak}`; `/mesh/{sync, order, status, ping}`. SigVerifier-gated except `/api/{ping, status, adb-port, pair}` and the static UI routes.

**Desktop collars (`focuslock-desktop.py` / `focuslock-desktop-win.py`, port 8435)** — `/mesh/{sync, order, status, ping, vouchers, store-vouchers, redeem-voucher}`, `/api/pair/{create, code}`, `/`, `/index.html`. Mesh routes go through `mesh.handle_mesh_*` (PIN + RSA-signed); `/api/pair/create` is unauth.

---

## Findings — triage

Severity follows the 2026-04-24 audit conventions: **Critical** (immediate user harm or full compromise), **High** (significant impact, fix this audit phase), **Medium** (track + fix), **Low** (note + accept or backlog).

### HIGH

#### H-1 — `/enforcement-orders` and `/memory` are unauthenticated and the comment claims `/enforcement-orders` includes the admin token

`focuslock-mail.py:5195-5232` (`/enforcement-orders`), `:5371-5392` (`/memory`).

The endpoint reads `~/.claude/CLAUDE.md` from the operator's home directory and serves the entire content. The route's own header comment at line 5197 says:

> *"Full enforcement orders — includes admin token, penalty amounts, tactical memories. Fetched by Claude at session start, never stored on client disk."*

There is no token check. Public-internet exposure depends entirely on the reverse-proxy allowlist (`docs/SELF-HOSTING.md` mentions Tailscale/nginx allowlists but does not require them). On a default Caddy/nginx setup the relay's HTTP server is reachable for every path; an operator who doesn't read the self-hosting doc carefully will publish this endpoint.

`/memory` has the same shape — unauth, returns every `*.md` file under `~/.claude/enforcement-memory` (or `MEMORY_DIR`).

**Why High, not Critical:** the actual content of CLAUDE.md is operator-controlled and may or may not contain the admin_token in practice. The code's own contract claims it does. Defense-in-depth requires server-side auth regardless of reverse-proxy posture.

**Fix:** require `_is_valid_admin_auth(token)` on both `/enforcement-orders` and `/memory`. Reuse the URL-query-param pattern from `/admin/status` or add a `Bearer` header path. Consider gating `/standing-orders` and `/settings` similarly even though they redact `ADMIN_TOKEN` — they leak operational structure.

---

#### H-2 — Class of unauthenticated webhooks fires `send_evidence()` with caller-supplied content; same bug class the 2026-04-17 audit closed for `/webhook/bunny-message` but missed for the siblings

`focuslock-mail.py`: `/webhook/compliment` (`:2821`), `/webhook/gratitude` (`:2826`), `/webhook/love_letter` (`:2832`), `/webhook/offer` (`:2848`), `/webhook/geofence-breach` (`:2859`), `/webhook/evidence-photo` (`:2873`), `/webhook/subscription-charge` (`:2928`). All seven are unauth. Each calls `send_evidence(text, type)` (or sends a photo email directly), which composes a message and SMTPs it to `PARTNER_EMAIL` — the Lion's email of record.

The 2026-04-17 audit fixed exactly this class for `/webhook/bunny-message` (`:2940`, now bunny-signed under `(mesh_id, node_id)`). The CHANGELOG explicitly named *only* three remaining unauth webhooks (`register`, `controller-register`, `desktop-heartbeat`) and called them "lower-risk (informational-only peer/heartbeat registries, no evidence-email side effect)". The seven webhooks above all DO have evidence-email side effect and were not enumerated.

The slave APK actively calls these unsigned (`android/slave/src/com/focuslock/FocusActivity.java:132,409,960,1140` etc.; `ControlService.java:1990,2005`; `AdminReceiver.java:99`). The relay binds 0.0.0.0:8434, so any caller reaching the relay's port can spoof:

- "Bunny self-locked", fake compliments / gratitude entries / love letters
- Fake "geofence breach" emails with attacker-supplied lat/lon
- Arbitrary photo email attachments to `PARTNER_EMAIL`
- Fake subscription-charge evidence

The Lion ends up reading attacker-authored evidence in their email and may form false beliefs about the Bunny.

**Why High, not Critical:** the impact is social-confusion / trust-undermining, not direct enforcement compromise (no paywall changes, no lock state change). But evidence emails are exactly what the Lion relies on to validate the Bunny's behavior; corrupting them breaks the trust the system advertises.

**Fix:** mirror the `/webhook/bunny-message` pattern. Require `(mesh_id, node_id, ts, signature)` with the canonical payload `"{mesh_id}|{node_id}|{type}|{ts}"`, verified against the registered `bunny_pubkey`. Slave APK + Bunny Tasker need to sign these on the way out — same `sendSignedBunnyWebhook` helper Bunny Tasker already uses for `bunny-message`. Bump min-collar-version + min-companion-version on the 403 response.

---

### MEDIUM

#### M-1 — `/webhook/desktop-heartbeat` accepts a caller-controlled `mesh_id` without authentication and runs `adb shell` for the operator mesh case

`focuslock-mail.py:4890-4928`. The route:
1. Reads `mesh_id` from the request body (falls back to `OPERATOR_MESH_ID`).
2. Calls `_get_desktop_registry(mesh_id).heartbeat(hostname, name=…)`.
3. If `mesh_id == OPERATOR_MESH_ID`, runs `subprocess.run(["adb", "-s", dev, "shell", "settings", "put", "global", "focus_lock_desktops", desktop_summary])` for every connected ADB device.

Problems: anyone on the relay's network can (a) fabricate desktop heartbeats so the dead-man's switch in `check_desktop_heartbeats()` thinks a non-existent collar is alive (delaying penalty firing for a real silent collar) or (b) run an adb shell write to `Settings.Global.focus_lock_desktops` on the operator's phone with caller-supplied content. `desktop_summary` is built from `reg.summary_line(...)` which itself reads attacker-supplied hostname strings from prior heartbeats — so the attacker controls what gets pushed to the phone.

The 2026-04-17 changelog notes this as deferred, citing "no evidence-email side effect". The ADB write is a state-change side effect.

**Fix:** require a node-signed heartbeat — the desktop collar already has a `node_pubkey` registered in the vault. Add a `(hostname, mesh_id, ts, signature)` payload signed with the desktop's `node_privkey` and verify against `_vault_store.get_nodes(mesh_id)`. This brings parity with `state-mirror` (`:3921`) which already does this for slave runtime mirrors.

---

#### M-2 — `/webhook/controller-register` accepts an unauthenticated `tailscale_ip`, writes `/run/focuslock/controller.json`, and updates `mesh_peers`

`focuslock-mail.py:4667-4682`. Anyone reaching the relay can write a chosen tailscale IP into the controller-registry file (consumed by `/controller` GET and any `release` script that queries it) and into `mesh_peers` under node_id `lions-share`. Other LAN peers gossiping with the relay then learn that the controller is at the attacker's IP.

**Fix:** require an admin-signed payload (or operator-mesh-only with admin_token), or scope writes by Lion signature against the operator's `lion_pubkey`.

---

#### M-3 — `/webhook/register` writes IP_REGISTRY_FILE without authentication

`focuslock-mail.py:4930-4952`. Caller-supplied `device_id` becomes a key in `IP_REGISTRY_FILE`; caller-supplied `lan_ip` and `tailscale_ip` are stored. Any caller can pollute the registry; the `device_id` field is unvalidated (could be `..` or empty), though it's used as a JSON key, not a path. Mostly an integrity/log-tampering concern.

**Fix:** at minimum, validate `device_id` shape; ideally require a node signature so only registered phones can self-register their IPs.

---

#### M-4 — `/webhook/verify-photo` and `/webhook/generate-task` invoke local Ollama without authentication

`focuslock-mail.py:2914-2926`. Anyone can submit base64 photos for LLM verification or generate arbitrary tasks. Burns operator GPU/CPU + can be used to fingerprint the model. Resource-exhaustion vector with no rate limit.

**Fix:** sign with bunny key as in M-1 / H-2.

---

#### M-5 — Server-side has no nonce cache for bunny-signed `/api/mesh/{id}/*` and `/webhook/bunny-message`; only ts ±5 min window

Routes affected (all signed with `(mesh_id, node_id, action, ts)` only):
- `/api/mesh/{id}/{auto-accept, subscribe, unsubscribe, gamble, payments, escape-event, state-mirror, deadline-task/clear, messages/{send, fetch, mark, edit, delete}}`
- `/webhook/bunny-message`

The slave's `SigVerifier.NonceCache` (Android, `SigVerifier.java:44-68`, MAX 4096 / TTL 600s) prevents replays on the slave. The relay does not have an equivalent. Within the 5-minute window an attacker who captured a signed request (over plain HTTP, via a misconfigured operator using HTTP for `mesh_url`, or via a compromised intermediate proxy) can re-fire it.

Per-action analysis:
- `/gamble` — non-idempotent. Replay re-rolls, doubling/halving paywall again.
- `/messages/send` — re-posts the same message (visible duplicate in inbox + ntfy fan-out). Annoyance + potential evidence-confusion.
- `/messages/edit` and `/delete` — idempotent on the same message_id; replay re-applies the same change.
- `/escape-event` (`escape`, `tamper_*`, `geofence_breach`) — these are idempotent for tamper-recorded but increment lifetime counters for `escape` (which feeds factory-reset-at-150 behavior). Replay can inflate escape count. `app_launch_penalty` already has a dedicated dedup window (`APP_LAUNCH_DEDUP_WINDOW_MS`) — confirms the project recognizes this class is an issue.
- `/sit-boy` — non-idempotent (paywall add).
- `/subscribe` / `/unsubscribe` — idempotent at steady-state; first call wins.
- `/state-mirror` — overwrites whitelisted fields with the signed state; replay overwrites again with the same values.

`/webhook/bunny-message` — re-fires `send_evidence` (re-emails the Lion). Mostly nuisance.

The threat model explicitly mentions *"replay protection — `±5 min ts` window + nonce LRU on `/api/*` POSTs to the slave; same for any new server-side route added since C1."* (audit-plan.md). The "same for the relay" was intended; not yet implemented.

**Fix:** introduce a relay-side `NonceCache` mirroring the Java one (already exists conceptually in the codebase; can port the LinkedHashMap + LRU pattern). Add a `nonce` field to all twelve endpoints, bump the canonical payloads to include it (`"{mesh_id}|{node_id}|{action}|{ts}|{nonce}"`), and have the Bunny Tasker / Collar signers pass a 16-byte random nonce per request. Coordinated with companion-version + collar-version min bumps. Or: use `(mesh_id, node_id, ts)` as the dedup key (no client change — every request signed with the same ts is treated as the same request) — simpler, slightly weaker.

---

#### M-6 — Desktop collar `/api/pair/create` (port 8435) is unauthenticated and leaks mesh PIN + Lion pubkey + homelab URL

`focuslock-desktop.py:1102-1133` and `focuslock-desktop-win.py` mirror. The desktop binds 0.0.0.0:8435, so anyone on the desktop's LAN can request `/api/pair/create` and receive `{mesh_pin, pubkey_pem, homelab_url, mesh_url}`. The PIN is the gossip-layer auth; leaking it lets the attacker join the gossip layer and read mesh status. Cannot forge orders (Lion privkey not leaked) but can poll status, peer counts, etc.

This is a concrete regression of the 2026-04-24 multi-tenant work — that audit added per-mesh registries to the relay, but the desktop collar's pairing endpoint stayed unauth.

**Fix:** require an `admin_token` check, or restrict the route to localhost (`127.0.0.1` bind for the `/api/pair/create` handler), or move the pairing-code generation into Lion's Share's UI (the controller already has the pubkey + homelab URL in hand) and have the desktop only consume codes via `_serve_pairing_code` (which doesn't leak — codes expire and are filename-validated).

---

#### M-7 — Lion's Share controller manifest declares `MANAGE_EXTERNAL_STORAGE` with no usage in source

`android/controller/AndroidManifest.xml`. `grep -rn` for `Environment.|getExternalStorage|MediaStore|FileProvider|/sdcard` in `android/controller/src/` returns only one camera-intent line that does not require this permission. `MANAGE_EXTERNAL_STORAGE` is a "special permission" with broad filesystem access; Google Play has policy requirements (Storage Permission Declaration form) for any app that requests it.

Privacy / future-distribution concern. Won't matter today (sideload-only) but blocks Play Store / F-Droid.

**Fix:** delete the `<uses-permission>` line. Camera + scoped storage is sufficient.

---

#### M-8 — Multiple log sites interpolate user-controlled fields without `_sanitize_log()`

`focuslock-mail.py` lines 1305, 3048, 3349, 3483, 3490, 3549, 3626, 3724, 3820, 4116, 4414, 4548. Common shape: `logger.info("…: mesh=%s node=%s …", mesh_id, node_id, …)` where `mesh_id` is `_safe_mesh_id`-validated (no newlines possible) but `node_id`, `from_who`, `message_id`, `reason`, `params["user"]` are not.

CodeQL covers this class — the manual walk found cases CodeQL didn't flag, likely because the call paths cross too many functions for static analysis to follow.

**Fix:** wrap the offending fields in `_sanitize_log()`. Consider a small lint test (`tests/test_log_sanitization.py`) that AST-walks `logger.{info,warning,error,debug}` calls in `focuslock-mail.py` and asserts every interpolated field is either a literal, a `_safe_mesh_id`-validated symbol, or `_sanitize_log(...)`.

---

### LOW

#### L-1 — `/admin/status` accepts `admin_token` as a URL query parameter

`focuslock-mail.py:5063-5091`. URL-embedded tokens leak through reverse-proxy access logs, browser history, Referer headers, and shoulder-surfing. The handler's own `log_message` override drops to `logger.debug` so the relay's own log isn't the leak. Caddy/nginx access logs ARE.

The pattern repeats at `/api/pair/vault-status/{mesh_id}` (`:5290`).

**Fix:** prefer `Authorization: Bearer <token>` header. Both call sites already check the header as a fallback — make it the primary and reject query-param-based tokens with a deprecation warning, or just drop the query-param path on the next major version.

---

#### L-2 — Slave's `/api/status` (sig-exempt) exposes lat/lon to any LAN caller

`android/slave/src/com/focuslock/ControlService.java:912-984`. The slave's status response includes `curLat` and `curLon` from `LocationManager.NETWORK_PROVIDER`. Any caller on the bunny's LAN — neighbors, café WiFi, IT department — can poll the bunny's location every few seconds.

This is inherent to the LAN-gossip design but worth pinning in the threat model.

**Fix:** strip lat/lon from the sig-exempt status response. Move them behind a signed `/api/location` query (or include them in the Lion's-only signed status response).

---

#### L-3 — `OPERATOR_MESH_ID` is loaded from config without `_safe_mesh_id` validation

`focuslock-mail.py:2309` (load), `:343` (used in `os.path.join`). A misconfigured operator who writes `"../../etc/passwd"` to `operator_mesh_id` in `config.json` triggers a path-traversal write. Trust boundary: the operator already has root-equivalent access; this is defense-in-depth, not a real exploit.

**Fix:** call `_safe_mesh_id_static(OPERATOR_MESH_ID)` after load and refuse to start (or coerce to empty) on failure.

---

#### L-4 — Cleartext-traffic-permitted is unconditional in all three Android apps

`android/{slave,companion,controller}/res/xml/network_security_config.xml`. Documented as a known weakness (`docs/THREAT-MODEL.md` last bullet). Could be tightened with a `<domain-config>` allowlist for LAN ranges + the homelab IP, requiring HTTPS for the public relay URL.

**Fix:** non-blocking. Worth doing at the next minor version.

---

#### L-5 — `/standing-orders`, `/settings`, `/memory` unauthenticated (subset of H-1)

`/standing-orders` and `/settings` redact `ADMIN_TOKEN` before serving. `/memory` does not. All three leak operational details (rules framework, Claude Code hook configuration, memory entries). Tracked under H-1 for the fix.

---

#### L-6 — Scorecard CI run today (run 25008444226) failed with a transient GitHub GraphQL infra error

`scorecard had an error: internal error: ListCommits:error during graphqlHandler.setup … Something went wrong while executing your query`. Two prior runs the same day succeeded; the failure is GitHub-side, not a Scorecard regression. Not blocking; re-run on next push.

---

#### L-7 — Sigstore attestation-verify command failed during this audit on flag-syntax issues

`gh attestation verify` rejected the combination tried. Sigstore artifacts ARE present on the v1.2.0 release (SBOM.cdx.json + signed assets). Operator should re-run manually before the next release with `gh attestation verify <asset> --owner NoahBunny --repo opencollar`.

---

## What passed (no findings)

- **Crypto primitives.** AES-256-GCM with 12-byte `os.urandom` nonces (`shared/focuslock_vault.py:182-183`); RSA-OAEP-SHA256 wrap (`:143-145, 190-196`); RSA-PKCS1v15-SHA256 signatures everywhere; RSA-2048 minimum (`:220`); no PKCS#1 v1.5 *encryption*. PEM and bare-DER-base64 pubkeys handled consistently across `_load_lion_pubkey_obj` (Python), `VaultCrypto.java`, and `SigVerifier.java`.
- **Canonical JSON.** Identical `sort_keys=True, separators=(",", ":")` shape in `mesh.canonical_json`, `shared/focuslock_vault.py`, and the Java `SigVerifier.canonicalize`. C1 envelope `focusctl|<path>|<ts>|<nonce>|<params>` matches across all four implementations (verifier, slave / controller / companion signers, and `tests/test_http.py` parity).
- **Multi-tenant scoping.** No regressions of the 2026-04-24 7-finding audit. Every server-side singleton is either operator-only by intent, or accessed exclusively via a per-mesh factory: `_orders_registry.get_or_create`, `_get_payment_ledger`, `_get_desktop_registry`, `_get_message_store`, `_vault_store` (mesh-scoped methods), `_iter_imap_scan_contexts`. `_mesh_accounts.get(mesh_id)` is preceded by `_safe_mesh_id` at every call site.
- **Path traversal.** Every `os.path.join(..., mesh_id)` is gated by `_safe_mesh_id*`. Pairing-code filename writes are regex-validated to `[A-Z0-9]{4,12}` at both relay and desktop. Only L-3 (`OPERATOR_MESH_ID`) lacks runtime validation — defense-in-depth concern, not exploitable.
- **Vault invariants.** `VaultStore.append` enforces version monotonicity and rejects non-monotonic writes under lock. `gc` retains the latest blob unconditionally. `add_node` / `add_pending_node` / `add_rejected_node` / `since` are mesh-scoped. `_verify_blob_two_writer` correctly accepts Lion-signed and registered-node-signed blobs and rejects others.
- **Side channels.** `hmac.compare_digest` at every secret comparison site (`focuslock-mail.py:90, 2249`; `focuslock_mesh.py:991`).
- **Token lifecycles.** `_disposal_tokens` uses lock-protected atomic check-and-claim (lines 3121-3129) — no two concurrent redemptions can both pass. `_active_session_tokens` prunes expired tokens on issue + on lookup; mesh-scoped binding for multi-tenant.
- **Slave HTTP exempt list** (`SIG_EXEMPT_PATHS` in `ControlService.java:70-78`) — `/api/{ping, status, adb-port, pair}` plus three static UI routes. Read-only or bootstrap by design. L-2 notes the privacy tradeoff in `/api/status`.
- **Slave nonce cache.** 4096-entry LRU + 600s TTL + record-before-verify + atomic `seenOrRecord` correctly closes the C1 replay class.
- **Replay protection on Lion-signed slave POSTs.** Identical-keyed implementations in slave verifier and Lion / Bunny signers; parity test green.
- **Multi-tenant message store, payment ledger, desktop registry.** All per-mesh paths under `_LEDGERS_DIR`, `_DESKTOP_REGISTRIES_DIR`, `_MESSAGE_STORES_DIR`. Operator mesh keeps legacy filenames for continuity. No cross-mesh writes possible.
- **CodeQL.** Latest run on `main` (today, 25008444246) passed.
- **Scorecard.** Earlier runs today passed; failure noted as L-6 transient.

---

## Acceptance gate

Per `docs/AUDIT-PLAN.md`, Stream A exits when every Critical and High finding is fixed-and-committed and Mediums are tracked. **No Criticals.** **Two Highs (H-1, H-2)** require fixes during this audit phase. Mediums M-1 through M-8 should be tracked as follow-up commits; any of them are fair to bundle into the H-1/H-2 PRs if the diff stays focused.

Recommended next moves, in order:
1. **Fix H-2** — bundle the seven unauth evidence-email webhooks into one signed-rewrite commit + min-collar-version bump.
2. **Fix H-1** — gate `/enforcement-orders`, `/memory`, `/standing-orders`, `/settings` on `_is_valid_admin_auth`.
3. **Fix M-1, M-2, M-3** — same signed-rewrite pattern as H-2 for `desktop-heartbeat`, `controller-register`, `register`.
4. **Fix M-4** — sign `/webhook/{verify-photo, generate-task}`.
5. **Fix M-6** — auth `/api/pair/create` on the desktop collar, or move generation client-side.
6. **Fix M-7** — drop `MANAGE_EXTERNAL_STORAGE` from controller manifest.
7. **Track M-5** as a v1.1 hardening — server-side nonce cache requires a coordinated client + server change.
8. **Track M-8 + L-3** as a small batch fix.
9. **Track L-2, L-4** in roadmap — privacy + cleartext tightening.

When the Highs land, write `docs/AUDIT-2026-04-27-EXIT.md` per the plan and close out Stream A.

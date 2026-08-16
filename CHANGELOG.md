# Changelog

All notable changes to this project are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project aims to adhere to [Semantic Versioning](https://semver.org/spec/v2.0.0.html)
starting with v1.0.0.

## [Unreleased]

<!-- ───────── 2026-08-17 the read side of the address gate ───────── -->

### Fixed — `GET /controller` served the Lion's address to anyone who asked

- **The write side has been admin-gated since audit 2026-04-27 M-2**, on the explicit
  reasoning that an unauthenticated caller must not get to choose the address controller
  resolution hands back. The read side was left open — so the address itself, the Lion's
  controller on the mesh, was served to any caller that could reach the relay, and this
  relay is on public HTTPS at `collar.nunyabiznu.com`. It answered 404 when checked only
  because `controller.json` lives on tmpfs and a reboot had wiped it; it refills the moment
  the installer re-registers. Gating the write and publishing the read is the gate facing
  one direction.
- **Now gated with the same shape as `/standing-orders`** (`?admin_token=` or
  `Authorization: Bearer`): 503 when no `ADMIN_TOKEN` is configured, 403 on a missing or
  wrong token. Dispatch had to stop comparing the raw path — an authed call carries a query
  string, and `self.path == "/controller"` would have 404'd every one of them.
- **`scripts/release.sh`, the one caller, sends `FOCUSLOCK_ADMIN_TOKEN`** as a Bearer
  header rather than a query parameter, since a query string lands in the relay's access log
  and this token is the whole admin API. With the variable unset the script says so and
  falls back to `LION_DEVICE_IP`, exactly as it did before.
- `tests/test_e2e_public_routes.py` gives up its claim on this route.
  `TestControllerIsNoLongerPublic` replaces it with 6 tests: unauthenticated refused, wrong
  token refused, unconfigured token fails closed rather than falling open, the query-token
  and Bearer paths both reach the handler, and a registered address is still served to an
  authed caller — the caller the endpoint exists for.


<!-- ───────── 2026-08-17 the flag that claimed a door was open ───────── -->

### Fixed — `auto_accept_nodes` stayed `true` on disk long after the window shut

- **`_auto_accept_active()` fails closed on an expired or missing deadline, so no mesh
  was actually accepting anyone** — but nothing ever wrote that verdict back, so
  `auto_accept_nodes` sat `true` in the account JSON forever. That file is what an
  operator reads on the relay when they go to ask whether the onboarding door is open,
  and on the live mesh (`eMv8tP9KJL0D`) it had been answering *yes* against a door the
  gate holds shut. Both HTTP readouts were already honest — `/nodes` and the Lion's
  toggle both go through the helper — so this was never a security hole; it was a record
  that disagreed with the system enforcing it, on the exact question the record exists to
  answer. New `_close_expired_auto_accept_windows()` runs at every relay start and
  reconciles the flag to `false` (with `auto_accept_until` zeroed) for any account the
  gate was already refusing. Idempotent, and **an open window is never shortened** — a
  Lion who opened one 30 minutes ago keeps all 30.
- 5 new tests (`tests/test_auto_accept_window.py::TestExpiredWindowReconcile`): an expired
  deadline reconciles; the legacy no-deadline shape the live mesh was actually in
  reconciles; a deliberately-opened window survives untouched; the write lands on disk and
  is stable across a re-run; and a registration after the reconcile still queues for
  approval rather than sliding in.

### Still owed — trust provenance on the live mesh is thin

- Every node on `eMv8tP9KJL0D` except `relay`/`controller` is stamped
  `confirmed_by: grandfathered` — swept in by the one-shot migration, never looked at by
  the Lion — and `gengar-neon` was admitted from the relay console
  (`admitted_by: operator-console`) at the bunny's request, without a Lion signature. The
  grandfather sweep was the right call for working meshes, but a stamp that says "nobody
  checked" is not authority. **One tap each in Lion's Share → Vault Nodes replaces the
  inherited stamps with deliberate ones**; that is an operator action, not a code change,
  and it has not been done.

<!-- ───────── 2026-08-17 deploy-path defects + the template's missing safeword ───────── -->

### Fixed — `FOCUSLOCK_SRC` was documented, exported, and ignored

- **Every `re-enslave-*` script called `discover_paths` before `load_config`**
  (`installers/re-enslave-{server,desktops,phones}.sh`), so the source pin that
  `load_config` exists to export was never set in time for the function that reads it.
  The pin only worked when the operator exported it by hand; otherwise autodiscovery
  walked to whichever checkout it found first. On a machine with two checkouts that is a
  coin flip between branches, and the losing side of it deploys older code over newer —
  a bare `re-enslave-server.sh` offered exactly that against a live relay. Reordered in
  all three, so the config file's `FOCUSLOCK_SRC` finally means what it says.

### Fixed — the settings fallback disarmed the collar it was installing

- **`install-standing-orders.sh` installed the homelab account's own
  `~/.claude/settings.json` over the collared machine's** — and on a relay-only box that
  file is typically a bare `{"theme": "auto"}`. `settings.json` is not preferences on a
  collared machine; it is where the enforcement hooks live. Observed on 2026-08-17: a
  3174-byte settings carrying the paywall gate, tamper hook, pronoun check and bash audit
  was replaced by 22 bytes of theme preference, disarming all four silently. `efbe1b2`'s
  backup made it recoverable, but a backup is not a guard. Both fetch paths now go
  through `settings_is_safe()`: the candidate must parse as JSON, and must not drop a
  `hooks` key the destination already has.

### Fixed — the public standing-orders template had no safeword in it

- **`docs/CLAUDE-stub.md`** — the template every deployment copies onto its relay — shipped
  with an emergency override but no safeword tier. The override covers danger; it does not
  cover *"stop the dynamic, talk to me plainly"*, which is the exit the rest of the document
  depends on to be enforceable literally. Adds a `## Safewords` section (yellow pauses, red
  ends it, neither ever logged as tamper, neither revocable by any order, everything else
  stays in scene) plus a line in the intro naming both exits, since the section otherwise
  sits two thirds of the way down a long file. Found while tracing why standing orders were
  not propagating on a live mesh: the relay had no orders on file at all, so what a fresh
  seed would contain stopped being hypothetical.

<!-- ───────── 2026-08-17 node-signed standing orders ───────── -->

### Added — a collar can read its orders without holding the Lion's admin token

- **Being told what to do required the keys to the relay** (`focuslock-mail.py`,
  `focuslock-desktop.py`, `focuslock-desktop-win.py`). `GET /standing-orders` is
  admin-gated (audit 2026-04-27 H-1), so the only way a desktop collar could pull the
  Lion's orders was to keep `ADMIN_TOKEN` — a credential for the whole admin API, across
  every mesh the relay serves — in the config of the machine the collar exists to
  constrain. Found on a live mesh where the sync had been dead for a day and the
  remedy on offer was to hand the bunny that token. New **`POST
  /vault/{mesh_id}/standing-orders`** — body `{node_id, ts, signature}`, signature over
  `"{mesh_id}|{node_id}|standing-orders|{ts}"` with the node's own registered key
  (vault `node_pubkey` or the `bunny_pubkey` on its row), ±5 min replay window —
  returns `{content, sha256}`. Membership is the right proof: a node that already holds
  a registered key has demonstrated it is on this mesh, and that is all reading the
  orders should require. Both collars try it first and fall back to the Bearer path, so
  a separate homelab box or a token-configured operator keeps working.
- **Serves the stub only.** `_read_standing_orders()` is shared with the admin GET:
  `CLAUDE-stub.md` if present, else `CLAUDE.md`, with `ADMIN_TOKEN` redacted.
  `/enforcement-orders` — tactical orders, penalty amounts, the token itself — has no
  node-signed route and stays admin-gated, where a node key is not enough.
- **Not gated on `lion_confirmed`**, same reasoning as `lion-pubkey`: a machine that
  starts obeying the Lion's standing orders becomes more governed, never less.
- **One verifier for every node-signed route.** `_verify_node_signature()` +
  `_node_signing_keys()` now back both this route and `lion-pubkey`, which previously
  carried its own thirty-line copy. A route that quietly accepted a wider set of keys
  than its siblings is the kind of drift that is easier to notice in one function than
  in three copies.
- 13 new tests (`tests/test_standing_orders_node_fetch.py`): registered node served and
  the sha matches; **the safeword clause survives the trip**; the admin token is
  redacted; an unconfirmed auto-accepted node is served; stranger, rogue key, the
  Lion's own key, stale ts, and a signature bound to another mesh all refused; missing
  fields 400; no orders on file 404; the admin gate on the GET is untouched and
  `/enforcement-orders` has no node-signed door. Mutation-checked. Suite `1277 → 1290`.

<!-- ───────── 2026-08-17 restart re-registration ───────── -->

### Fixed — a collar restart looked like a stranger knocking

- **Every already-approved collar re-queued itself on restart** (`focuslock-mail.py`).
  `_vault_register_node()` guards on an in-process flag, so each start re-posts
  `register-node-request`; once the auto-accept window shuts, that request was queued
  unconditionally — there was no "this node_id is already approved with this exact key"
  short-circuit — and fired `_node_join_ntfy()`. Found on the live mesh: of three
  pending requests, two (`charizard-garuda`, `vaporeon`) were established members
  re-posting the **same** key, and only one (`gengar-neon`) was a device that had never
  been let in. That is how a real request hides: the Lion sees a queue where approving
  most rows is a no-op, and learns to wave the whole thing through. An exact-key repost
  now answers `{"status": "approved", "already_registered": true, "lion_confirmed": …}`
  with no queue write and no alert.
- **Strict about what counts as the same node.** `node_pubkey` must match byte-for-byte,
  and a request carrying a *different* `bunny_pubkey` than the row holds falls through to
  the queue. Both are verification anchors, so silently accepting a new one on an
  unsigned endpoint would be the key-swap this handler routes to the Lion on purpose.
- **The no-op deliberately does not clear the node's pending row.** Pending is keyed by
  `node_id` and `node_pubkey`s are readable anonymously from `/vault/{id}/nodes`, so
  clearing there would let anyone replay a node's current key to delete that node's
  *rotation* request and strand it on its old key.
- **The restart repost stays** rather than being suppressed client-side with a
  persisted flag: it is the self-healing path that re-enrolls collars through the normal
  gate if relay state is ever lost (as it was on 2026-08-15).
- 6 new tests (`tests/test_auto_accept_window.py::TestIdempotentReRegistration`): repost
  is a no-op that neither churns `registered_at` nor queues; no join alert for a repost
  but still one for a stranger; the no-op reports live confirmation state; a rotated key
  and a changed `bunny_pubkey` both still queue with the stored row untouched; replaying
  the current key cannot cancel a pending rotation. Suite `1271 → 1277`.

<!-- ───────── 2026-08-16 unattended relay deploys ───────── -->

### Added — `install-server-sudoers.sh`: the relay stops asking for a password

- **Every relay deploy prompted for sudo, so none could run unattended**
  (`installers/install-server-sudoers.sh`, `installers/re-enslave-server.sh`).
  Desktop collars have had narrow NOPASSWD rules since `install-desktop-collar.sh`;
  the relay had no equivalent, which also meant a deploy could never be driven from a
  non-interactive shell. Run once on the relay with sudo, the new script hands
  `/opt/focuslock` to the deploy user and installs a four-line
  `/etc/sudoers.d/focuslock-server` covering exactly `systemctl restart|is-active
  focuslock-mail` (exact argument forms, no wildcards — it cannot be widened into
  "restart anything"), validated with `visudo -cf` before install because a malformed
  sudoers file locks every user out of sudo. `--revert` gives the directory back to
  root and removes the rule.
- **`re-enslave-server.sh` escalates only where it must.** The generated apply script
  now checks `test -w /opt/focuslock` and uses plain writes when the dir is the deploy
  user's, leaving `sudo` for the service restart alone; on an unprepared relay it falls
  back to sudo for everything, exactly as before. Its pre-flight also asks the *real*
  questions — is the install dir writable, is the specific systemctl command passwordless
  — because a bare `sudo -n true` answers neither and reports "needs a password" on a
  correctly prepared relay.
- **Stated plainly in the script header, not buried:** `focuslock-mail.service` runs as
  root with no `User=`, so any passwordless path to replace `/opt/focuslock/*.py` is
  root-equivalent for that account on the next restart. That is inherent to unattended
  deployment — a root-owned staging helper would be no stronger, since the code it
  installs is what root then executes. `config.json` and any `*.pem` stay `root:root
  0600` as defence in depth, with the caveat spelled out that directory ownership still
  allows replacing them.

<!-- ───────── 2026-08-16 desktops claim their Lion ───────── -->

### Added — a desktop can obtain its Lion's pubkey instead of waiting for a hand-copied PEM

- **A collar could join a mesh and stay unclaimed forever** (`focuslock-mail.py`,
  `focuslock-desktop.py`, `focuslock-desktop-win.py`). `register-node-request` never
  returned the Lion's public key, and the collar has no fetch path — only the
  invite-code join (`/api/mesh/join`) and the passphrase pairing flow ever handed it
  over. So a self-registering desktop (the normal path) sat as a fully approved vault
  node with a **gray crown**, no standing orders, and no way to verify a Lion-signed
  order, until a human copied `lion_pubkey.pem` onto the box by hand. Found on
  vaporeon: an approved, auto-accepted, state-mirroring member of the live mesh that
  had never been claimed. New **`POST /vault/{mesh_id}/lion-pubkey`** — body
  `{node_id, ts, signature}`, signature over `"{mesh_id}|{node_id}|lion-pubkey|{ts}"`
  with the node's *own* registered key (vault `node_pubkey` or the `bunny_pubkey` on
  its row, same pair state-mirror accepts), ±5 min replay window — returns the
  account's `lion_pubkey`. Both collars call it on the standing-orders tick when no
  Lion key is on file, write it as PEM (loading it first, so a reverse-proxy error
  page can't become a trust anchor), and claim themselves within one poll.
- **Why not read the `controller` row's `node_pubkey` client-side** (it does match the
  account's `lion_pubkey` — verified on the live mesh, hash `73195ffcf316ab30`):
  `node_type` is self-asserted at registration, so anything that registered as
  `node_type: "controller"` during an open auto-accept window could poison a collar's
  trust anchor and forge orders from then on. Only the relay knows the authoritative
  key, so only the relay can safely hand it over.
- **Not gated on `lion_confirmed`.** It is a public verification key, and a node that
  adopts it only becomes *more* obedient — it starts enforcing Lion-signed orders it
  would otherwise ignore. Withholding it would protect nothing and leave devices
  unclaimed, which is the failure the endpoint exists to end.
- 8 new tests (`tests/test_lion_pubkey_fetch.py`): approved node served; unconfirmed
  auto-accepted node still served (deliberate); unknown node, rogue key, stale ts, and
  a signature lifted from another mesh all rejected; mesh with no Lion key 404s;
  missing fields 400. Suite `1263 → 1271`.

### Fixed — a claimed machine kept telling Claude that nobody owned it

- **The interim marching orders outlived their pairing** (`focuslock-desktop.py`,
  `focuslock-desktop-win.py`). The paired branch cleared the `unpaired-since` marker
  and then returned early when no `admin_token` was configured to fetch the Lion's real
  orders — leaving the overlay, whose own text reads *"collared, unpaired"*, in place
  indefinitely on a machine that now had a Lion. Both collars now revoke the overlay
  the moment a Lion key lands, restoring the bunny's pre-existing `CLAUDE.md` if there
  was one. Gated on the overlay marker, so a file the collar didn't write is never
  touched.

<!-- ───────── 2026-08-16 interim marching orders for an unpaired collar ───────── -->

### Added — a collared-but-unpaired PC is no longer silent

- **The collar was invisible until it paired** (`shared/focuslock_unpaired_orders.py`,
  `focuslock-desktop.py`, `focuslock-desktop-win.py`). The Lion's standing orders live
  on Their mesh (`GET /standing-orders`), so a machine with no Lion key on file fetched
  nothing and every Claude Code session on it behaved as though there were no collar at
  all — which is exactly the state a bunny stalls in: installed, nothing feels
  different, pairing slides to "later". Both collars now render an **interim CLAUDE.md
  overlay** whenever `lion_pubkey.pem` is absent: the Lion/bunny frame plus one standing
  task — get this machine paired — and nothing else.
- **The nudge escalates on the collar's own clock.** `unpaired-since` is stamped the
  first time the collar finds itself unpaired (not when the bunny opens a session) and
  drives four tiers: *settling-in* (<6 h — one line per session), *insistent* (<24 h —
  every session plus every ~10 exchanges), *pointed* (<72 h — every response, elapsed
  time named, ask what's blocking them), *unignorable* (72 h+ — leads every response,
  states the Lion still has no visibility). The overlay is re-rendered each
  `MEMORY_SYNC_INTERVAL` tick, so the pressure grows without a restart. The marker file
  is cleared on pairing, so a later unpairing starts from zero.
- **It is deliberately not the full marching orders.** The overlay says so in its own
  text, speaks for the collar and never for the Lion, and carries no enforcement: no
  punishing, billing, restricting, or withholding work — *"You are pressing, not
  withholding"* is in every tier. The consent floor is untouched: real enforcement and
  the **Terms of Surrender** still begin at mesh-join, and the overlay points at that.
- **A bunny's own `CLAUDE.md` is never clobbered.** Linux reuses `_apply_standing_orders`
  (first-write backup to `claude-md.preuser`, restored by `_revoke_standing_orders`);
  Windows gained the equivalent backup, plus a marker check so it only overwrites a file
  it wrote. Windows also syncs standing orders once at startup instead of five minutes
  in, so an unpaired machine gets its orders at boot.
- Registered-but-unclaimed (mesh_id set, Lion hasn't approved the node) gets a different
  ask than mesh-less: *"tell the Lion to confirm this device in Vault Nodes"* rather than
  *"join a mesh"*. 17 new tests (`tests/test_unpaired_orders.py`) pin the marker,
  tier boundaries, escalation ordering, and the no-enforcement/consent-floor contract.
  Suite `1246 → 1263`.

<!-- ───────── 2026-08-16 auto-accept window + node-join alerts + state-mirror confirmation ───────── -->

### Changed — auto-accept is a 30-minute onboarding window, not a permanent open door

- **Any device that learned a mesh_id became a permanent member** (`focuslock-mail.py`,
  Lion's Share **81/81.0**). `auto_accept_nodes` was a sticky boolean, **default ON at
  mesh creation** since 2026-05-25 — so `register-node-request` approved anything that
  showed up, forever, and approved nodes are recipients of every future Lion blob
  (`shared/focuslock_vault.py` encrypts the AES key per approved node). The intended
  workflow ("switch it on to onboard, switch it off after") depended on the Lion
  remembering, and nothing ever reminded them. Now the flag is paired with
  `auto_accept_until`: `/api/mesh/{id}/auto-accept` `on` opens a
  `MeshAccountStore.AUTO_ACCEPT_WINDOW_S` (30 min) window and returns `expires_in_s`;
  `off` closes it immediately; mesh creation opens one from signup so first-device
  onboarding stays frictionless. `_auto_accept_active()` is the single gate that
  `register-node-request` enforces and that `/vault/{id}/nodes` reports to the Lion's
  toggle, so the UI can never claim a door is open that the relay treats as shut.
  **Fails closed on a missing deadline**, so accounts persisted before the field
  existed stop being open-forever the moment this deploys. Key rotation still routes
  to the pending queue regardless of the window (unchanged).
- **Devices already on a mesh are grandfathered**, once, by
  `_grandfather_auto_accepted_nodes()` at relay start: every existing `auto_accepted`
  row is stamped `lion_confirmed` with `confirmed_by: "grandfathered"`, so a Lion
  auditing the roster can still tell an inherited confirmation from a deliberate one.
  Those devices were enrolled under the old rules; retroactively blocking their writes
  would break working meshes to punish them for the relay's old default. The per-mesh
  `auto_accept_grandfathered_at` marker is what keeps this from being a hole — without
  it, a restart would sweep in whatever had auto-accepted since and the gate would mean
  nothing.

### Added — Lion is told when a device joins the mesh

- **A silent join was invisible until the Lion happened to open Vault Nodes and hit
  Refresh** (`focuslock-mail.py`, `MainActivity.java`). Every `register-node-request`
  outcome — auto-accepted, queued for approval, or rotation-blocked — now fires
  `_node_join_ntfy()`, the standard zero-knowledge `{"v": ts}` wake (the relay never
  says *who* joined over ntfy). Lion's Share diffs the node roster on that wake and on
  a 60 s floor poll, remembers which ids it has already shown **per mesh** (so bunny
  switching doesn't re-announce), and raises a heads-up notification naming the new
  device and how it got in ("joined automatically" / "wants in"). First sight of a mesh
  seeds the roster silently — upgrading doesn't fire a notification for devices that
  were already there.

### Fixed — an unconfirmed auto-accepted node could write the Lion's financial state

- **Holding the mesh_id was enough to zero the paywall** (`focuslock-mail.py`,
  `MainActivity.java`). `fbbf468` made `register-node-request` carry `bunny_pubkey` so
  a registered node became a *verifiable member* — which also handed it
  `/api/mesh/{id}/state-mirror`, whose whitelist writes `paywall`, `paywall_original`,
  `sub_tier`, `sub_due`, `lock_active`, `locked_at`, `unlock_at`, `free_unlocks`
  straight into the `_orders_registry` doc the compound-interest and payment scanners
  bill from. Previously that channel required tampering with the enforced Collar; via
  auto-accept it required only the mesh_id. A signature now proves *identity*, not
  *authority*: a node stamped `auto_accepted` without `lion_confirmed` gets 403
  *"node awaiting lion confirmation"* from state-mirror **and from the sibling
  plaintext writes that trust a vault row the same way** — `set-payee-identity`,
  `set-evidence-email` (the Lion's payment email + IMAP password),
  `set-payer-identity`, and `set-display-name`. Those routes gate on `node_type`
  ("controller node required", "phone node required"), but `node_type` is
  self-asserted at registration, so it was never a barrier to a stranger holding
  the mesh_id; `_node_awaiting_confirmation()` is. New Lion-signed
  `POST /vault/{mesh_id}/confirm-node` (`{node_id, ts, signature}`) stamps the row;
  Lion's Share shows "⚠ joined automatically — not confirmed by you" plus a **Confirm
  this device** button on the node row, and "Add as bunny" implies confirmation. Invite-code
  members and pending-queue approvals are untouched — both already passed through the
  Lion. Collars log the 403 and retry, so an unconfirmed device keeps enforcing locally;
  only the server's view goes stale until the Lion taps once. `auto_accepted` /
  `lion_confirmed` / `confirmed_at` are stripped from `/vault/{id}/nodes` for
  unauthenticated callers, same reconnaissance reasoning as the `auto_accept` flag.
- 19 new HTTP-level tests (`tests/test_auto_accept_window.py`) pin: window opens with a
  deadline; open window auto-accepts; expired window and legacy no-deadline accounts
  queue for approval; `off` closes immediately; a new mesh's window expires; key
  rotation still needs approval inside an open window; `/nodes` reports live window
  state and hides trust fields from anonymous callers; confirm-node accepts the Lion,
  rejects a rogue key, 404s an unknown node; unconfirmed auto-accepted node cannot
  write state, set a payer identity, or rename itself, and confirmation unlocks it;
  invite-joined and Lion-approved nodes are unaffected; the grandfather sweep runs once
  and does not re-sweep. Suite `1227 → 1246`.

<!-- ───────── 2026-08-09 unpaired-tamper trap + desktop standing-orders gate ───────── -->

### Fixed — safety floor: admin-tamper enforcement no longer traps an UNPAIRED device

- **An unpaired phone could tamper-lock itself with no way out** (collar
  **83/8.40**, companion **62/2.29**). The mutual-admin monitor
  (`ControlService`), the `AdminReceiver` re-lock on admin removal (Collar +
  companion), and the companion's `refreshStats` monitor all fired **regardless
  of pairing**. Provisioning device admin *before* the Lion pairs — enabling one
  app's admin while the other's is momentarily absent — tripped "admin removed",
  set `focus_lock_active=1`, and re-locked every 6 s. With no Lion (`lion_pubkey`
  unset) there was no order/timer to clear it, so the wearer was locked out of
  their own phone; re-adding admin cleared the flag but not the lock. Now every
  tamper path gates on a new `isPaired()` (a Lion pubkey is on file): an unpaired
  device has no Lion to be accountable to, so admin changes are never treated as
  tamper. **Found and fixed on hardware** — both test phones were trapped this
  way, unlocked via break-glass, then confirmed to stay unlocked on the fixed
  build with break-glass cleared.

### Added — Linux desktop collar: standing orders only apply while connected

- **The Lion's standing orders (`~/.claude/CLAUDE.md`) now honor connectivity**
  (`focuslock-desktop.py`, opt-in via `standing_orders_require_connection`).
  Previously they persisted once synced, so a bunny disconnected from the
  mesh/Lion kept following directives the Lion could no longer update or revoke.
  With the flag set, the orders are an overlay: applied on a successful sync,
  and reverted after `_SO_REVOKE_AFTER` consecutive failed syncs — restoring the
  machine's own prior `CLAUDE.md`, or removing ours if there was none. A hash
  guard ensures revoke never clobbers a `CLAUDE.md` the user edited themselves.
  Consistent with `feedback_offline_no_lock`.

<!-- ───────── 2026-08-08 device-QA follow-ups: status shadowing, A16 release, Tor wait ───────── -->

Cleared three follow-ups the device-QA session left open — two latent-correctness
traps and one UX gap — all found by inspection while reading the code the
hardware pass touched, none needing the phones re-provisioned. Versions:
controller **78 / 78.0**, collar **82 / 8.39**, companion **61 / 2.28**;
`installers/re-enslave-lib.sh` targets synced.

### Fixed — Lion's Share (controller)

- **The status line still read six signed fields off the whole body with
  first-match helpers** (`android/controller/src/com/focusctl/MainActivity.java`,
  `updateLiveStatus`). The signature *verification* path was scoped to the
  top-level object (the fix-#5 `StatusCore`), but the *display* path was not, so
  in direct mode it rendered the `orders`-document copies of `paywall` /
  `task_reps` / `task_done` / `offer` / `offer_status` / `sub_tier` — the exact
  shadowing hazard, one schema change from mattering. Routed the nine core
  display fields through `StatusCore.fromWire` (the same scoped reader the
  signature check uses), with a fail-safe fallback to first-match if the body
  isn't a parseable object. Non-core fields (`lovense` / `geofence` / `fine` /
  `body_check`) keep first-match: in direct mode they live *only* inside
  `orders`, so scoping them to the top level would blank them. No visible change
  today (the copies tie); the class of bug is now closed on both paths.
  Regression: `StatusCoreTest.rebuiltCoreUsesTheSignersNativeTypes` now also pins
  the native types of the fields the display path casts.

### Fixed — Lion's Share (controller, multi-bunny usability)

- **Every bunny slot defaulted to the literal label "bunny"** (`MainActivity.java`,
  `pairDirect` / `createMesh`). The status line shows only the label, so with more
  than one bunny the Lion couldn't tell whose lock state and whose balance they
  were looking at — the exact ambiguity that made the fix-#7 balance leak invisible
  (a wrong number under an identical name reads as the right number). New slots now
  default to a short, stable, per-slot tag — `bunny-<fp>` from the bunny-key
  fingerprint for a direct pair, `bunny-<mesh>` from the mesh id for a relay mesh —
  so they're distinguishable out of the box; the existing Advanced → Bunnies rename
  still applies.
- **The Bunnies dialog didn't redraw after a rename or removal** — the slot was
  gone from prefs but stayed on screen until you closed and reopened the dialog.
  It now dismisses and re-opens itself in place after either action.

### Added — Lion's Share (controller)

- **A live "Waking Collar over Tor…" indicator during a cold-onion wake**
  (`MainActivity.java`, `beginWakeIndicator` / `endWakeIndicator`). The first
  cold order blocks up to 120 s in `wakeAndAuthorize` while both Tor daemons
  boot; the old one-shot `setStatus("Waking Collar…")` was overwritten by the
  next 1 s timer tick, so the UI read as hung. Now a depth-counted ticker owns
  the status line with a counting-up elapsed timer (the order path and the read
  poll can each trigger a wake), and the per-second timer + `updateLiveStatus`
  yield the line while it's up. Inert in default (Tor-OFF) builds.

### Fixed — The Collar + Bunny Tasker (Release Forever teardown on Android 16/17)

- **Release Forever couldn't remove its own device admin on Android 16/17**
  (`android/slave/src/com/focuslock/ControlService.java`, `doReleaseForever`).
  The self-destruct shelled `dpm remove-active-admin`, which A16/17 refuse for a
  non-test admin, and the follow-on `pm uninstall` refuses while an admin is
  active — so teardown stalled on a manual Settings → Security deactivation both
  QA sessions. A same-package caller can always remove its own admin, so the
  Collar now calls `DevicePolicyManager.removeActiveAdmin(adminComponent())`
  directly (release_authorized=1 makes `AdminReceiver.onDisabled` a no-op, so no
  tamper penalty fires); the shell calls stay as a best-effort fallback.
- **Bunny Tasker's admin blocked its own uninstall the same way.** One package
  can't remove another's admin programmatically, so the companion now removes
  *its own* admin via the API from `BunnyService`'s watcher when it sees
  `focus_lock_release_authorized==1` **or** the terminal `focus_lock_released==1`
  (the safeword path sets the latter and preserves it, so teardown is reliable
  rather than racing the ~8 s window the Collar holds `release_authorized`),
  then stops the watcher. Still needs on-device verification with device admin
  actually enabled.

### Fixed — consent accuracy: "Terms of Surrender" overstated the factory-reset barrier

- **The consent dialog told the wearer factory reset was "available after 150
  escape attempts"** (`android/slave/src/com/focuslock/FocusActivity.java`), but
  the in-app shortcut actually appears at `escapes >= 3` and the OS factory reset
  is *always* available (the code comment says so, and
  `applyDeviceOwnerRestrictions` never sets `DISALLOW_FACTORY_RESET`). The
  2026-04 change that lowered the threshold updated the code and the CHANGELOG but
  missed the dialog text itself, so the binding consent screen understated a
  safety-floor exit by 50×. Reworded to state the exit accurately: the OS factory
  reset is always available, with an in-app shortcut after a few escapes. The same
  stale "150 escapes" claim was corrected across the user-facing docs
  (`DISCLAIMER.md`, `SECURITY.md`, `docs/MANUAL-BUNNY.md`, `docs/README.md`,
  `CLAUDE.md`, the QA checklists, roadmap, `CONTRIBUTING.md`, PR template).

- **Docs asserted phantom "+$500 attempt / +$1000 removal" admin-tamper charges.**
  The code applies neither: the Android admin-tamper path (`AdminReceiver` →
  server `tamper-recorded`) only bumps `lifetime_tamper`, and the flat
  `TAMPER_ATTEMPT_PENALTY=500` / `TAMPER_REMOVED_PENALTY=1000` constants in
  `shared/focuslock_penalties.py` are dead (zero call sites — the real desktop
  tamper penalty is the `$5/tier` ratchet, capped $500). A wearer reading
  `PRICE-LIST.md` / `README.md` / `CLAUDE.md` would believe leaving costs
  $500–$1000 — the "punish-exit" the design explicitly removed. Corrected those
  plus the QA checklists to state actual behavior (Android admin tamper: friction
  re-lock + notification, no charge; desktop tamper: `$5/tier` ratchet), and
  refreshed a stale `ControlService.doReleaseForever` comment that still named the
  removed penalties. (The dead constants themselves are left for a separate
  cleanup.)

### Fixed — safety floor: admin-tamper handlers now honor the terminal `released` state

The terminal `released` flag (set by the panic safeword, preserved for good) is
honored by the enforcement loop, the jail, the order-apply path and the ADB
bridge — but three admin-tamper paths gated only on `release_authorized`, which
`doReleaseForever` **deletes** at the end of teardown. So a safeworded-and-
released device whose admin was later removed could be dragged back into
enforcement, violating the documented floor ("once released, no enforcement
action may re-lock" — `docs/THREAT-MODEL.md`). All three now also honor
`released`:

- **The Collar re-locked a released device on admin removal**
  (`android/slave/src/com/focuslock/AdminReceiver.java`, `onDisabled` /
  `onDisableRequested`). It set `focus_lock_active=1`, wrote a shame message, and
  launched the jail. `launchFocus()` already no-ops when released, but `active=1`
  is read by the desktops, the companion, and the vault state-mirror — so the
  re-lock was real even without the jail UI. Now a released (or mid-release)
  device takes the no-penalty / no-re-lock path.
- **Bunny Tasker fired a false "admin was removed" tamper alert** on a released
  device (`android/companion/src/com/bunnytasker/AdminReceiver.java`,
  `onDisabled`). Now suppressed when `released`.
- **Bunny Tasker's mutual-admin monitor kept reporting `tamper_removed` and
  re-locking the Collar** after a release (`MainActivity.java`, `refreshStats`).
  Now gated on `released` in addition to `release_authorized`.

  (The Collar's own jail-watcher mutual-admin monitor was already safe — the
  `isReleased()` guard at the top of that loop `continue`s past all enforcement,
  mutual-admin included; only a clarifying comment was added there.)

- **The SMS `sit-boy` trigger didn't honor `released`**
  (`android/slave/src/com/focuslock/SmsReceiver.java`). A `sit-boy` text from the
  controller number re-locked the phone (and desktops) with no `released` check —
  and this receiver bypasses the jail-watcher / order-dispatch guards entirely, so
  a released device could be re-locked by SMS. Now ignores the command when
  released.

- **The Collar's direct HTTP order path didn't honor `released`**
  (`android/slave/src/com/focuslock/ControlService.java`, `handler`). The mesh
  path (`handleMeshOrder`) and the legacy apply path (`applyOrdersFromMesh`) both
  refuse orders when released, but the direct `/api/*` HTTP dispatch — used by
  Direct (LAN) pairings — did not. A validly-signed `/api/lock` (or `/api/task`,
  `/api/entrap`, `/api/photo-task`, `/api/lock-device`, `/api/add-paywall` …) to a
  freed device would set `focus_lock_active=1`: `launchFocus()` no-ops via its own
  guard, but the lock STATE still mutated and propagated to the desktops,
  `/mesh/status`, and the vault. Now a blanket `isReleased()` gate (mirroring
  `handleMeshOrder`) refuses every state-mutating `/api/*` POST when released;
  read-only endpoints and the exempt bootstrap (`/api/pair` — the documented
  resume-after-release path) stay callable.

- **Desktop collars kept enforcing after a release that arrived via gossip/vault**
  (`focuslock-desktop.py` + `focuslock-desktop-win.py`, `poll_status`). Only the
  direct `release-device` *action* fired liberation; a release delivered as order
  *state* — gossip `apply_remote`, or a vault order snapshot — just copied the
  `released` key into orders (it's in `ORDER_KEYS`), and the steady-state poll
  loop never checked it. So a released desktop kept enforcing (bedtime / countdown
  / desktop_active / timer) until its next restart, which the `__main__` startup
  check only catches then. The **safeword-from-phone** case propagates exactly
  this way (as state, not as an action), so a safeworded bunny's desktop stayed
  locked. `poll_status` now honors `released` at runtime on every delivery path —
  fires liberation once (guarded by `state.liberating`) and stops enforcing.
  (The Windows collar's tray `.exe` must be rebuilt via `build-win.py` to ship
  this — cannot be built from this environment.)

<!-- ───────── 2026-08-07 (third pass) on-device QA against two real phones ───────── -->

Worked the device-QA runbook against real hardware — a Samsung **SM-S908W** as
the bunny (Android 16 / API 36) and a **Pixel 10** as the Lion (Android 17 / API
37) — and fixed the six bugs it surfaced. Five of them were invisible to the
off-device suite because every one of them lived in a place the tests were
modelling instead of exercising: two distinct keypairs collapsed into one
fixture, a prefs key read through the same constant that wrote it, a signed
payload rebuilt from a hand-written dict rather than the wire, a poll gate no
test ever evaluated. Versions: controller **77 / 77.0** (slave/companion
unchanged at 81 / 8.38 and 60 / 2.27); `installers/re-enslave-lib.sh` targets
synced.

The last two are worth reading together: a Direct (LAN) pairing — the path the
UI recommends as "Fastest" — could not show the Lion anything. Orders flowed and
applied, but the status line was frozen twice over, once by a signature rebuild
that read the wrong bytes and once by a poll that never ran.

### Fixed — Lion's Share (controller)

- **A Direct (LAN) pair produced a paired but completely uncontrollable bunny**
  (`android/controller/src/com/focusctl/MainActivity.java`). `pairDirect` saved
  the generated Lion keypair under `lion_privkey_b64` / `lion_pubkey_b64`, but
  `createMesh` and all ~17 signed-op call sites read the canonical
  `lion_privkey` / `lion_pubkey`. Nothing ever read what pairing wrote, so
  `buildDirectSigHeaders` got `""` and returned null — every order failed
  "missing lion_privkey" *before* opening a socket, on the path the UI
  recommends as "Fastest". Fixed to the canonical names. Verified end-to-end on
  hardware: `/api/lock` → collar `locked=True` with a 15-minute timer, `+$25` →
  collar `paywall=25`.
- **Every direct-mode `/mesh/status` was rejected as forged, so the Lion's UI
  froze on its last-good snapshot** (`MainActivity.verifyStatusSignature`, new
  `android/controller/src/com/focusctl/StatusCore.java`). The Collar signs a flat
  ten-field status core and ships those fields at the top level of the status
  body — a body that also embeds the entire orders document, which repeats six
  of the ten key names *earlier in the byte stream*. The controller rebuilt the
  core with its `indexOf`-based `parseJson*` helpers, which take the first match
  anywhere, so it read the orders copies. Five agreed by luck (both sides read
  the same `Settings.Global` row); `paywall` did not, because `handleMeshStatus`
  defaults an unset paywall to `"0"` while `buildOrdersJson` emits `""`. On any
  freshly paired Collar — no balance charged yet — the rebuilt core differed
  from the signed one by exactly one field, so orders were delivered and applied
  while the Lion's screen kept showing `$0` and no lock. The rebuild now lives in
  `StatusCore.fromWire` and is scoped to the **top-level JSON object**, which
  closes the class rather than the instance: no embedded document can shadow a
  signed field again, whatever keys the orders schema grows next. The wire format
  is unchanged, so an updated controller still verifies Collars already in the
  field.
- **A serverless Direct (LAN) pairing never polled status at all**
  (`MainActivity.startStatusPolling`, new
  `android/controller/src/com/focusctl/PollGate.java`). The poller was gated on
  `!meshId.isEmpty()`. A direct pairing has no mesh — that is the entire selling
  point of the mode ("no account, no server") — so the whole poll body was dead
  code for it. The Lion's UI held whatever it last rendered ("Switched to
  bunny", `$0`, no lock) indefinitely while the Collar accepted, applied and
  reported orders normally. `meshGet` already served `/mesh/status` straight off
  the Collar in that mode; only this gate stopped it from ever being asked. The
  decision moved to `PollGate.shouldPollStatus`, which polls when there is a
  mesh **or** a reachable direct target, using the identical direct-mode
  condition as `meshGet` so the two cannot drift. Also gave the unlocked status
  line a `| Direct` liveness marker, so a direct-paired Lion can tell a
  freshly-polled `UNLOCKED` from a line that stopped updating an hour ago.
  Found on hardware — it is invisible in relay-mode testing, which is why the
  status-signature bug above had masked it.
- **Switching bunnies left the previous bunny's balance on screen**
  (`MainActivity.setActiveBunny`). `setActiveBunny` already resets the backing
  runtime fields (`isLocked`, `timerEndMs`, `lastEscapes`, `lastPaywall`, the
  optimistic caches) — but the *rendered* balance is only ever written by
  `updateLiveStatus`, which runs solely on a **verified** snapshot. When the
  newly-selected bunny is unreachable or its status can't be verified, nothing
  overwrites the figure and the Lion reads one bunny's balance under another
  bunny's name, indefinitely and with no indication. Observed on hardware:
  switching from a bunny at `$40` to one at `$7` held `$40` for the whole
  observation window while the new slot's status was being (correctly) rejected.
  The slot switch now renders `$—` in grey until that bunny's own status lands.
  Deliberately **not** `$0`: that is a positive claim the bunny owes nothing,
  which we cannot back and which resolves an ambiguity in the bunny's favour.
  The default slot label is `"bunny"` for every slot, so the Lion has no second
  cue that the number belongs to someone else — see the follow-up note in the
  device-QA handoff.

### Fixed — Tor build (A3)

- **A Tor-ON build killed the whole Collar the first time Tor started, then
  crash-looped** (`android/slave/build.sh`, `android/controller/build.sh`,
  `scripts/setup-qa-env-garuda.sh`). `org.torproject.jni.TorService.onCreate()`
  calls `broadcastStatus()`, which needs `androidx.localbroadcastmanager` — a
  class the APK never contained, because this project has no Gradle and so no
  dependency resolver, and the build dexed only the AAR's `classes.jar` plus
  jtorctl and bcprov. The first onion wake therefore produced
  `NoClassDefFoundError` → `FATAL EXCEPTION: main` → process death, repeatedly,
  since the triggering ntfy wake is redelivered to the restarted app.
  The Collar is the enforcement app and the onion wake topic is a plain ntfy.sh
  topic anyone can publish to, so a Tor-ON build in the field would have handed
  out a remote way to crash-loop the leash. It stayed hidden because Tor ships
  default-OFF and runbook section F had never run on hardware. Now bundled as
  `FOCUSLOCK_LBM_JAR` in both build scripts, fetched + exported by
  `setup-qa-env-garuda.sh --tor`, with a loud warning when unset instead of a
  silently fatal APK. **Verified on-device**: the same wake is now handled
  cleanly, the process survives, and Tor comes up (SOCKS on `127.0.0.1:9050`,
  `TorService` bound).

- **The onion was never published — Tor rejected every `ADD_ONION`**
  (`android/slave/src/com/focuslock/TorManager.java`). The command carried a
  `ClientAuthV3=` clause without the matching `V3Auth` flag, so Tor replied
  `No auth type specified`; the exception was swallowed as "relay fallback" and
  the hidden service silently never came up, on every wake. Now
  `Flags=Detach,V3Auth`, with the client-auth keys resolved *before* the command
  is built (with none we must not publish at all). Verified on-device:
  `onion published: 7bmvii…orad.onion (1 client key(s))`.
- **The Lion could send orders over Tor but never read status**
  (`MainActivity.getDirectWithFailover`). `maybeWakeBunny` — which bumps the
  Collar's wake topic, starts the Lion's own Tor and authorizes the onion — was
  wired into the POST path only, so the status poller dialed `.onion` candidates
  with no SOCKS proxy running and failed silently. Third instance of the
  "orders flow, Lion goes blind" family this session. The read path now performs
  the same wake behind a rate limit and an in-flight guard (the poller ticks
  every 5 s while the wake blocks up to 120 s). Candidates stay LAN-first, so it
  costs nothing while the LAN works. Verified: from a cold start with Tor down,
  Wi-Fi off and no order ever sent, the Lion brought Tor up within 20 s and
  rendered `bunny | LOCKED | 22m 35s left`.

### Fixed — Server (relay)

- **`set-display-name` was verified against the wrong key, so every rename
  403'd** (`focuslock-mail.py`). The companion signs the rename with the account
  `bunny_pubkey` (its `PairingManager` key), exactly as every other bunny-signed
  endpoint does, but the server verified only against the vault `node_pubkey` —
  the Collar's `ControlService` key. On real hardware those are two different
  keypairs. The unit test reused one key for both stores, which is precisely why
  it passed. Now verifies against the account `bunny_pubkey` with a `node_pubkey`
  fallback, and the regression test in
  `tests/test_display_name_desktop_task_guards.py` provisions **distinct** keys.
- **The Lion was never told when the bunny killed the enforcement watchdog**
  (`focuslock-mail.py`). The Collar detects ShadeGuard being disabled mid-lock
  and POSTs `event_type=shadeguard_disabled`, but the relay's event allowlist
  didn't include it, so the report came back HTTP 400 and the tamper vanished.
  Added to the allowlist and to `kind_map` (→ `watchdog_off`). No financial
  penalty attaches, matching the costly-exit-not-punish-exit tamper model.

### Fixed — Installers

- **Bunny Tasker was never granted `WRITE_SECURE_SETTINGS`, leaving joins
  half-finished** (`installers/re-enslave-phones.sh`). The companion's in-app
  "Join Mesh" writes the join config to `Settings.Global`; without WSS the join
  POST succeeded server-side while the local write threw. The Lion saw the node
  appear and the bunny saw "Join failed". `recage_focuslock` now grants the
  companion WSS alongside the Collar.

### Testing

- **The `/mesh/status` wire format is now a spec, not an assumption**
  (`tests/test_android_conformance.py`, `android/controller/test/com/focusctl/StatusCoreTest.java`,
  `android/controller/test/com/focusctl/ConformanceCli.java`, `android/build-conformance.sh`).
  The old test built the status core as a dict on both sides and never rendered
  a wire body, so the shadowing was structurally unreachable. The Python half
  (always runs) builds the real body, asserts a top-level-scoped rebuild
  reproduces the signed core across four Collar states, and keeps a port of the
  first-match parser as an executable record of the bug it can no longer hide.
  The JVM half runs the **actual** Collar signer against the **actual**
  controller verifier over that body, and a new `status-core` conformance
  subcommand emits `StatusCore.fromWire`'s canonical bytes for byte-comparison
  against Python. Reverting `StatusCore` to the first-match parse fails four of
  the nine new JUnit tests, including the freshly-paired-Collar case.
- **The status-poll gate is now a tested predicate** (`PollGateTest.java`).
  It was an inline condition in a lambda, which is why nothing caught that it
  excluded the serverless pairing mode. `PollGate.shouldPollStatus` is extracted
  for the same reason `MeshOrderApply` lives outside `ControlService`.

### Verified on hardware (2026-08-07, SM-S908W + Pixel 10, Direct LAN pairing)

- The live Collar's `/mesh/status` body, checked against the device's real
  `focus_lock_bunny_pubkey`: the old first-match rebuild **fails** verification,
  the scoped rebuild **passes**, and the single drifting field is
  `paywall` `'0'` vs `''` — the diagnosis reproduced exactly on real hardware.
- Direct (LAN) pair → `bunny | UNLOCKED | Direct`, zero
  `REJECTED direct /mesh/status` log lines, **with the paywall never charged**
  (the precise state that was broken).
- 15-minute lock → Collar `focus_lock_active=1`, Lion shows
  `bunny | LOCKED | 14m 49s left`, still at `paywall=null`.
- `+$25` → Collar `focus_lock_paywall=25`, Lion balance `$25`.
- **B-9 no-snap-back**: after unlock, six consecutive poll cycles over 36s held
  `collar_active=0` / `UNLOCKED` with no reversion. Re-lock extends correctly:
  15M → `14m 50s`, re-lock 30M → `29m 50s` (Collar `unlock_at` +29 min).
- **B-11 key substitution — PASSED against a live hostile endpoint.** A stand-in
  Collar (`staging/qa_fake_collar.py`) was paired normally, then flipped
  dishonest on demand. Signing with a **foreign key** while claiming
  `locked=true, paywall=999, timer=1h`: rejected every poll, UI held its
  last-good `UNLOCKED` / `$7`. **No signature at all**, same claims: same result.
  Then honest again with a genuinely changed `$12`: adopted within one cycle,
  zero rejections — so the refusal is discernment, not paralysis. A slot whose
  stored key had been rotated out from under it behaved identically (75
  consecutive rejections, no adoption). On-device counterpart to
  `StatusCoreTest.lanMitmClearingTheLockIsRejected` / `unsignedStatusIsRejected`.
- **B-10 multi-bunny isolation — FAILED, fixed, re-verified.** Lock state and
  timer *were* correctly isolated across slots; the balance was not. After the
  fix, switching to an unverifiable slot holds `$—` across 5 poll cycles and 10
  rejections, while a trusted slot still fills in within one cycle.
- Closing loop through the real apps: UNLOCK ALL + CLEAR on the Lion → Collar
  `focus_lock_active=0`, `focus_lock_paywall=0`, Lion's line settles to
  `bunny | UNLOCKED | Direct` / `$0`.
- New QA harnesses, all used above: `staging/qa_fake_collar.py` (a controllable
  second Collar — the thing that made key-substitution testable on hardware),
  `staging/qa_device_ui.py` (uiautomator tap/type driver, so device QA no longer
  needs an instrumented build or hand-tapping), `staging/qa_collar_driver.py`
  (signs Audit-C1 direct POSTs with the bunny key read off the device, so the
  whole `/api/*` surface is drivable without the Lion's private key),
  `staging/qa_collar_sweep.py` and `staging/qa_messaging.py`.

### Full-surface empirical sweep

- **Collar control surface: 34 pass · 0 fail · 6 blocked.** All 9 lock modes,
  task reps, photo-task, the five modifiers, paywall add/stack/clear (incl. the
  "quick lock must not clobber the ledger" regression), subscribe, messages,
  pinned messages, offers, geofence, check-in, notification prefs, volume,
  lock/unlock — each asserted against the Collar's own `Settings.Global` after
  the order, not against a mock. Refusals are asserted too: local unsubscribe is
  server-authoritative, check-in rejects an out-of-range hour, and free unlock is
  refused below Gold / granted at Gold / refused again as already-used.
- **Messaging: 11 pass · 0 fail** against a real relay signed with the device's
  real bunny key. Beyond send + thread round-trip: the bunny cannot forge a
  message from the Lion, cannot edit or delete history, a body tampered in flight
  fails the signature, a stale timestamp falls outside the replay window, and an
  unregistered node is refused.
- **E2EE messaging is zero-knowledge in practice, not just by design.** A message
  composed on the bunny's phone stored on the relay as `text: "[e2ee]"` plus
  `ciphertext`/`encrypted_key`/`iv`, with the plaintext appearing nowhere in the
  stored record.
- **Relay path end to end**: mesh create → real `/api/mesh/join` → vault appends
  (`slots=2`, ~1.7 KB ciphertext per tick) → `state-mirror` verifying with
  `signer=bunny` over the paywall/subscription/lock fields that compound interest
  and payment crediting depend on.
- **Daily check-in auto-lock verified autonomously** — the Collar locked itself
  ~50 minutes after the deadline hour with "Missed daily check-in", no order
  involved.
- **A3 Tor / onion — runbook section F fully verified on hardware for the first
  time** (items 20–25), after the three Tor fixes above. Tor-ON builds are 32 MB
  with `libtor.so` for all four ABIs, Tor classes dexed, `apksigner VERIFIED`;
  default-off builds unchanged at 238 KB with zero `libtor.so`.
  - **20/21**: Collar runs, Tor comes up (SOCKS `127.0.0.1:9050`), a v3 onion is
    provisioned (derived offline ~0.1 s after service start), and the Lion's
    `onion_auth_pub` is carried in the pair body and stored.
  - **22**: `ADD_ONION` accepted with `ClientAuthV3` — *"onion published … (1
    client key(s))"*. The client half is proven by the Lion's Tor fetching the
    auth-gated descriptor.
  - **23**: with Wi-Fi off on the Lion (LTE only) and the bunny's LAN IP
    confirmed unreachable, the Lion **read** `bunny | LOCKED | 28m 7s left` and
    **landed an UNLOCK order in under 15 s**, both over the onion.
  - **24**: republishing the onion authorized to a *different* key turned the
    identical probe from `HTTP 200` into `HTTP 000` twice; restoring the real key
    returned `HTTP 200 in 6.7 s`. Client auth is load-bearing, and the Collar
    refuses to publish at all without a Lion key — enforced at publish time, not
    merely at connect time.
  - **25**: ntfy publish → wake received **1.07 / 1.24 / 1.75 s**; wake → onion
    published **6.09 s**; Tor round-trip **5.7 / 6.7 s**; warm order **< 15 s**;
    cold Lion → Tor up **≤ 20 s**.
  - Behaviour worth remembering: Tor starts **on demand via the wake**, not at
    boot — `focus_lock_tor_warm` only suppresses teardown.

<!-- ───────── 2026-08-07 (second pass) deferred follow-ups from the ecosystem-review fix-forward ───────── -->

Cleared all three follow-ups the 2026-08-07 pass deferred. Versions: slave
**81 / 8.38**, companion **60 / 2.27** (controller unchanged at 73 / 73.0);
`installers/re-enslave-lib.sh` targets synced.

### Security
- **The tamper-escalation ratchet is now server-authoritative**
  (`focuslock-mail.py`, `report_tamper.py`, `shared/focuslock_penalties.py`).
  `report_tamper.py` kept its lifetime attempt counter in
  `~/.config/focuslock/tamper-attempts.json` — on the collared desktop, a machine
  the bunny has root on. `rm` on that file walked every future tamper penalty back
  to the $5 tier-1 floor, which is precisely the circumvention the ratchet prices.
  The authoritative counter now lives on the relay, per mesh
  (`_TAMPER_TIERS_DIR/{mesh_id}.json`, atomic write under a lock). The client tags
  the report `tamper: true` and sends its local count as `attempt`; the server takes
  `max(its own + 1, the claim)`, so the claim can only ever *fast-forward* the
  ratchet — healing a relay whose state was lost — and never lower it. A caller-
  supplied `amount` became a **floor** rather than a price: `--amount` can still
  bill a known incident higher than the reached tier, but can no longer undercut it.
  A hostile/corrupt claim can jump the counter by at most 100 (logged when clamped).
  Penalty reports that aren't tagged `tamper` — notably the desktop collar's flat
  $30 consent-decline — neither escalate nor advance the counter. New
  `tamper_penalty()` in `shared/focuslock_penalties.py` replaces the duplicated tier
  literal. The response now echoes `amount` + `tamper_attempt`; the client prefers
  the server's numbers and syncs its local hint upward (never down).

### Fixed — Collar (slave)
- **The `"pixel"` `node_id` fallback no longer forks the device's identity mid-join**
  (`android/slave/src/com/focuslock/ControlService.java`,
  `android/companion/src/com/bunnytasker/MainActivity.java`). The mesh-gossip
  handler *persisted* `focus_lock_mesh_node_id = "pixel"` whenever it answered a
  gossip tick before Bunny Tasker had assigned the real id. That flipped the vault
  registrar out of its "not joined yet, skip" branch, so it posted a
  register-node-request under `pixel` — a phantom row on the relay that the real
  node_id (written moments later) never reclaimed. All seven fallback sites now go
  through a new `selfNodeId()` which returns the stored id or, pre-join, derives one
  with the *exact* expression Bunny Tasker uses at join time
  (`Build.MODEL.toLowerCase().replace(" ", "-")`) — so the pre-join and post-join
  labels agree — and **never persists**. Bunny Tasker's `joinMesh()` now writes
  `focus_lock_mesh_node_id` *before* `focus_lock_mesh_id` / `focus_lock_mesh_url`,
  closing the window in which the Collar can see a mesh it has no identity for, and
  reuses the node_id it already sent in the join body instead of re-deriving it.

### Tests
- `tests/test_tamper_ratchet.py` (21) — tier formula vs. `escape_penalty`, the
  per-mesh counter store (isolation, fast-forward, monotonicity, unsafe-mesh_id
  path traversal), the end-to-end escalation with the client counter wiped before
  every attempt, amount-as-floor, the $500 ceiling, the untagged-penalty carve-out,
  and the client payload/commit contract (incl. no advance on a failed report).
- `tests/test_display_name_desktop_task_guards.py` (20) — the endpoint-level
  coverage the last pass deferred: `set-display-name` (happy path, forged signature,
  name-swap-after-signing, stale ts, unregistered node, unknown/unsafe mesh_id,
  40-char bound), the `GET /vault/{id}/nodes` enrichment auth gate (anonymous and
  wrong-token callers get the bootstrap list with no display name / bunny_pubkey /
  auto_accept), and the `desktop-task` guards (operator-mesh-only 409, armed-task
  409, miss-lock 409, bad token, input validation). Both guard sets were
  mutation-checked — reverting either guard fails the tests.
- Python suite `1177 → 1218`.

<!-- ───────── 2026-08-07 ecosystem-review fix-forward (cage tiers, safeword, optimistic UI, display name) ───────── -->

Landed the previously-uncommitted feature stack (cage tiers, optimistic order
reflection, bunny display name, desktop-task webhook, Windows liberation, panic
safeword.exe) after a full adversarial review — fixing the safety-floor
collisions, security holes, and correctness bugs the review surfaced before
shipping. Versions: slave **80 / 8.37**, controller **73 / 73.0**, companion
**59 / 2.26**; `installers/re-enslave-lib.sh` TARGET_*_VERSIONCODE synced.

### Security
- **Lion's Share no longer trusts a relay-advertised bunny E2EE key over a
  verified one** (`android/controller/src/com/focusctl/MainActivity.java`).
  `persistBunnyPubkey()` adopted whatever `bunny_pubkey` the (untrusted, zero-
  knowledge) relay returned in `/vault/{id}/nodes`, **overwriting** the pairing-
  fingerprint-verified key — and anyone holding the reusable invite code can
  register an arbitrary key. That silently defeated the C5 MITM check and let
  Lion→Bunny messages be encrypted to an attacker. Now it is trust-on-first-use
  only: it NEVER overwrites a stored key (rotation goes through re-pairing), logs
  a mismatch, and resolves the target bunny slot at call time (no cross-slot
  write on a mid-fetch bunny switch).
- **`GET /vault/{id}/nodes` enrichment is now Lion-authenticated**
  (`focuslock-mail.py`). The base node list stays readable for E2EE bootstrap,
  but the newly-added bunny display name (PII), mesh-level bunny pubkey, and
  `auto_accept` state (a reconnaissance aid) are only returned to a caller with a
  valid `auth_token` — previously any anonymous caller who knew the semi-public
  `mesh_id` got them.
- **`join()` no longer blanks a stored bunny pubkey** (`focuslock-mail.py`). A
  re-join carrying an empty `bunny_pubkey` (Bunny Tasker sends `""` on a transient
  keypair miss) overwrote the good key, 403-ing every bunny-signed endpoint with
  no recovery. Now preserved like `display_name`.

### Fixed — Collar (slave) safety floor
- **The foreground-app watchdog no longer closes the documented factory-reset
  exits** (`ShadeGuardService.java`, `FocusActivity.java`). At COLLAR/SEALED the
  watchdog bounced the **Settings app** (closing the OS factory reset the Terms of
  Surrender call "always available", and ignoring the Lion's own
  `/api/enable-settings` grant), and `onStop` delegating all re-jailing to the
  watchdog **froze the escape counter at 0**, so the in-app factory-reset button
  (gated at escapes ≥ 3) and the paywall/shame escalation never fired. Now:
  Settings is reachable when the Lion granted a window OR the wearer has crossed
  the escape threshold; the watchdog records a debounced escape on each bounce
  (restoring the counter + server escape event); the **camera** (Photo Task's only
  exit), **emergency dialer / in-call UI** (every tier, incl. SEALED "no calls"),
  and **runtime-permission dialogs** (permissioncontroller) are allowlisted.
- **Cage ceiling has a consent author and is bridge-unwritable**
  (`ConsentActivity.java`, `ConsentStore.java`, `ShadeGuardService.java`). Nothing
  wrote `focus_lock_cage_level`, so every device silently defaulted to COLLAR (a
  full app-bouncing brick the wearer never chose). The Terms-of-Surrender screen
  now has a Leash/Collar/Sealed chooser stored **app-private** (the one store the
  Lion's ADB bridge cannot write, making "the Lion may only loosen" enforceable),
  and an unset ceiling defaults to **Leash** — shipping the watchdog never
  silently tightens an already-provisioned device.
- **Watchdog-disabled tamper is now detectable** (`ControlService.java`). When
  caged at COLLAR+ but the accessibility service is off, re-jailing silently
  stopped and nothing noticed; the jail watcher now notifies the Lion once
  (`shadeguard_disabled`), the same accountability as disabling device admin.

### Fixed — Lion's Share (controller) optimistic reflection
- Cleared the process-wide optimistic + `lastSnapshotJson` caches on a bunny
  switch (they leaked bunny A's lock/timer/balance onto bunny B and merged A's
  Collar addresses into B's direct-failover list); restored the phone fallback in
  the Release-Device dialog (it dead-ended "No devices registered" on cold
  start / legacy mode); the optimistic confirm now waits for the timer to catch
  up (a re-lock to extend the timer no longer self-confirms against the old
  remaining time); a no-real-snapshot render no longer blanks
  escapes/tier/geofence via a synthetic `{}`; `cancelOptimistic` is generation-
  guarded so a late failure can't cancel a newer command; quick-lock now reflects
  a typed paywall amount.

### Fixed — server webhooks
- **`/webhook/desktop-task` no longer charges for an undeliverable task**
  (`focuslock-mail.py`). It is now operator-mesh only (an armed task reaches the
  phone only via operator-mesh gossip; a non-operator mesh would apply the miss
  penalty for a task the phone never showed — this also closes the vault_only
  plaintext bypass), refuses when a task is already armed (protecting a Lion-armed
  recurring task and preventing an unclearable miss-lock), bounds the task text,
  and emails evidence to the mesh's own Lion (`mesh_id` passed to
  `send_evidence`, not the operator-wide address). `/webhook/desktop-penalty`
  gained the same vault_only guard + per-mesh evidence routing.

### Added
- **Bunny display name is editable after pairing** (`bunnytasker` MainActivity +
  `focuslock-mail.py` new bunny-signed `POST /api/mesh/{id}/set-display-name`).
  Previously write-once at join (and unreachable for already-provisioned devices).
  The join dialog's "Save" button now actually persists the typed name (it was
  silently discarded), and a long-press on the paired fingerprint opens an editor
  that signs + pushes the change.

### Fixed — Windows desktop collar + tools
- **Release Forever is honest about a declined UAC** (`focuslock-desktop-win.py`).
  The durable teardown (scheduled tasks + `C:\focuslock`) needs elevation; when it
  must request UAC the farewell now warns that the prompt must be accepted or the
  collar returns at next sign-in, instead of unconditionally saying "you are free".
  Also: the liberation helper quotes the firewall-rule `name=` token as one arg
  (it was left behind), clears the forced HKLM lock-screen policy, and
  `safeword.py` clears it too (was a visible residual). `sync_standing_orders`
  now validates `settings.json` as JSON and writes atomically (a proxy error page
  or a mid-write kill can no longer corrupt `~/.claude` config). `self_install`
  now deploys `safeword.exe` (the double-click escape tool was built but never
  copied to the collared machine). `report_tamper.py` advances its escalation
  counter only after the server accepts the penalty (a failed report no longer
  over-prices the next one).

### Known follow-ups (tracked, not in this pass)
- ~~`report_tamper.py`'s escalation counter is still local~~ — closed in the second pass, above.
- ~~The Collar's `"pixel"` `node_id` fallback…~~ — closed in the second pass, above.
- ~~Endpoint-level regression tests for `set-display-name` / `desktop-task`~~ —
  closed in the second pass, above.

<!-- ───────── 2026-07-26 on-device QA fixes ───────── -->

### Fixed
- **The Collar's `ControlService` crash-looped on every boot on Android 14+ (API 34+)** (`android/slave/src/com/focuslock/ControlService.java`, slave **77 / 8.34**). `onCreate` called the no-type `startForeground(1, n)`, which makes the platform enforce **every** `foregroundServiceType` the manifest declares (`specialUse|location`). On API 34+ the `location` type additionally requires `ACCESS_COARSE/FINE_LOCATION` to be held at that instant — and on a fresh install it never is (geofence is opt-in and its permission may never be granted), so `startForeground` threw `SecurityException` and took the **entire** service down (HTTP API, jail-watcher, paywall, mesh) — Android then only retried on a multi-minute backoff. Now the FGS type is computed at runtime: always `specialUse`, plus `location` **only** when its runtime permission is actually held, with a fallback to `specialUse`-only if the platform still refuses it. The cage core no longer depends on the geofence permission. **Found and verified on-device** (Samsung SM-S908, Android 16 / API 36, via adb): before the fix the service crashed with `SecurityException: Starting FGS with type location`; after, it comes up cleanly with **no** location grant, and with location granted it runs as `types=0x40000008` (`SPECIAL_USE|LOCATION`).
- **Consent + safeword silently failed to save on a not-yet-provisioned Collar** (`android/slave/src/com/focuslock/{ConsentStore(new),ConsentActivity,FocusActivity,ControlService}.java`, `installers/re-enslave-phones.sh`, slave **78 / 8.35**). The Terms-of-Surrender screen persisted the consent flag and the wearer's **safeword phrase** via `Settings.Global`, which needs `WRITE_SECURE_SETTINGS` — a permission granted only by the adb operator step, *after* first run. So tapping "I CONSENT" on a fresh device threw `SecurityException` (the writes were unguarded) and the safeword never saved — safety-relevant, since the safeword is the wearer's always-available exit and they'd believe it was set. New `ConsentStore` dual-stores both values: **SharedPreferences always** (app-private, needs no permission, cannot fail) plus a best-effort `Settings.Global` mirror (keeps the survive-app-data-clear property and backward-compat with already-provisioned devices); all reads consult both. Also guarded the adjacent unguarded `Settings.Global` write in `storePriorHomePkg` (same crash, one line later), and fixed a key-name mismatch where recage wrote `focus_lock_consent_given` but the app reads `focus_lock_consented` (so "skip the dialog" never actually worked). **Verified on-device** (WSS revoked): consent recorded and survived with `Settings.Global` staying `null` throughout — proving SharedPreferences carried it — and cleared only by wiping app data.

### Security
- **The notification shade was reachable during a lock on non-device-owner phones — now guarded** (`android/slave/src/com/focuslock/ShadeGuardService.java` (new) + `res/values/strings.xml`, `res/xml/shade_guard_accessibility.xml`, manifest, `installers/re-enslave-phones.sh`, slave **79 / 8.36**). During a lock the wearer could pull down the notification shade and reach Quick Settings (e.g. airplane mode → cut the Lion's control). The status-bar lockdown only truly works two ways, neither available in the common "personal phone, direct mode" config: `setStatusBarDisabled` needs **device owner** (impossible without a factory wipe on a phone with accounts), and the homelab **bridge**'s `cmd statusbar disable-for-setup` needs the bridge running. **An app overlay cannot fix this** — since Android 12 the status bar is a system window layered above all `TYPE_APPLICATION_OVERLAY` windows (anti-tapjacking), so a top-edge swipe never reaches an app (built it, tested it on-device, reverted it). The working app-side fix is a minimal-privilege `AccessibilityService` (`canRetrieveWindowContent=false`) that watches for window changes during a lock and collapses the shade via `GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE` (API 31+). Reactive (the shade may flash before snapping shut) but denies sustained access with no owner/bridge; honors the terminal release flag. Enabled by the operator via adb in recage (appended to `enabled_accessibility_services`, never clobbering the wearer's own). **Verified on-device** (Samsung SM-S908): before, a shade pull stayed open; after, it collapses (`dismiss performed=true`).

### On-device QA (2026-07-26)
- **First real end-to-end run of the Collar's signed HTTP control surface**, exercised against a live device over adb with a Lion stand-in (RSA-2048 keypair, C1-canonical PKCS1v15/SHA-256 request signing). Verified: `/api/pair` bootstrap (unsigned) stores the Lion pubkey; signed `/api/message`, `/api/lock` (with `timer` → `timer_remaining_ms` counting down, `mode`/`shame`/`paywall` applied), `/api/unlock`, and `/api/clear-paywall` all accepted (200); an unsigned/forged request is rejected 403 (`stale_ts` for an old timestamp, `bad_sig` for a fresh-ts wrong signature). Confirmed the `timer` lock key is the real Collar↔Lion contract (`ControlService.doLock` reads `timer`; controller `buildLockJson` sends `timer`) — the `duration_min` in the `c1_canonicalize` golden vectors is only a format example, not the API key.

<!-- ───────── 2026-07-04 remove covert-coercion primitives + add a real safety floor ───────── -->

### Removed
- **Covert front-camera capture is gone** (`android/slave/src/com/focuslock/FocusActivity.java`, `focuslock-mail.py`, slave manifest). Deleted `captureSelfieSilent()` (Camera2) and the "silent selfie attached to every task-completion evidence webhook" path — `sendWebhook()` is now text-only. The `CAMERA` permission was dropped from the Collar manifest (the wearer-driven photo-task uses `ACTION_IMAGE_CAPTURE`, which needs no permission). The **explicit, wearer-submitted photo-task is unchanged** — the wearer knowingly takes and submits that photo (`/webhook/verify-photo` LLM check + `/webhook/evidence-photo` delivery, now the endpoint's only caller).
- **No hidden SMS interception** (`android/slave/src/com/focuslock/SmsReceiver.java`). Dropped `abortBroadcast()` so the `sit-boy` command SMS reaches the default messaging app like any other message. It still parses/locks and honors the `focus_lock_sms_token` gate — it is just no longer hidden from the wearer.
- **The wearer's location never leaves their phone** (`android/slave/.../ControlService.java`, `android/controller/.../MainActivity.java`, `web/index.html`, `focuslock-mail.py`). Removed `lat`/`lon` from `/api/status`, deleted `reportLocation()` + the `/webhook/location` sink, and stripped coordinates from the geofence-breach report (only the violation magnitude is sent). Geofences are enforced **locally**; the phone tattles the *fact* of a breach → +$100 paywall. `Set Geofence` / `Confine Home` no longer read the wearer's coordinates (Confine Home uses the Collar's own local GPS via `/api/confine-home`). Supersedes the deferred L-2 "signed `/api/location`" idea — there is no location endpoint at all.

### Added
- **Panic safeword — the wearer's always-available exit** (`android/slave/src/com/focuslock/{ControlService,FocusActivity,ConsentActivity}.java`, `focuslock-bridge.sh`). Long-press the lock message → type your pre-set safeword phrase (chosen in the consent screen, stored as `focus_lock_safeword`) → confirm → immediate full release with **no penalty**, needing neither the Lion nor the homelab. It sets a terminal `focus_lock_released` flag that is honored by the jail-watcher loop, `launchFocus()`, `isLockActive()`, `applyOrdersFromMesh()`, `handleMeshOrder()`, and the ADB bridge (`poll_device` ceases all enforcement when `released=1`), so nothing can re-lock a released device. It notifies the Lion for aftercare (not permission) and is a scene-ender (resuming requires re-pairing). `doReleaseForever()`'s settings-wipe now preserves every `focus_lock_release*` key so the terminal state survives teardown.

### Changed
- **Costly-exit, not punish-exit** (`android/slave/src/com/focuslock/{AdminReceiver,ControlService,FocusActivity}.java`, `focuslock-mail.py`). Disabling device admin still re-locks the phone (friction) and notifies the Lion for accountability, but the **$500/$1000/$500 tamper penalties are gone** — the server-side `tamper-recorded` handler now only increments `lifetime_tamper` (no paywall bump). Factory reset is **never** blocked: `applyDeviceOwnerRestrictions()` no longer sets `DISALLOW_FACTORY_RESET` (and clears it defensively), so the ultimate exit is always available even in device-owner mode. The in-app factory-reset shortcut now appears after a few escape attempts instead of 150. The consent screen ("Terms of Surrender") and `docs/THREAT-MODEL.md` were updated to describe the guaranteed exits, the no-covert-capture guarantee, and costly-vs-punitive framing.
- **Roadmap: covert & no-exit capabilities are permanently out of scope** (`docs/PUBLISHABLE-ROADMAP.md`). Added an "Out of scope" principle at the top and **struck** the "No-adb consumer install" Device-Owner/QR strategy (it would block factory reset + uninstall) and the server-side "$500 tamper" idea.

### Tests
- Updated `tests/test_paywall_hardening.py` (tamper = counter-only, no fine), `tests/test_e2e_qa.py` (tamper event applies no penalty), `tests/test_e2e_uncovered_webhooks.py` (`/webhook/location` removed → 404), and `tests/test_audit_2026_04_27_h2_evidence_webhooks.py` (geofence-breach body carries only `distance`; evidence-photo reframed as wearer-submitted photo-task proof). Python suites green.
- **The covert-removal Java is now built + verified** (2026-07-05, Garuda workstation; re-verified 2026-07-26 on the same workstation after a full OS reinstall — toolchain reprovisioned from scratch via `scripts/setup-qa-env-garuda.sh --no-waydroid`). All 3 APKs compile, dex, sign, and pass `apksigner verify` (slave 234,190 bytes, zero Tor); JVM unit + conformance 64/64; Java↔Python conformance 20/20. The 6 edited Java files (`ControlService`, `FocusActivity`, `AdminReceiver`, `ConsentActivity`, `SmsReceiver`, controller `MainActivity`) compile clean — retiring the "Android NOT compiled" caveat from the 2026-07-04 handoff.
- **Fixed a latent `javac` encoding bug in `android/build-conformance.sh`**, found by the 2026-07-26 re-verification on a freshly-installed JDK 17: unlike all three `android/*/build.sh` (which already pass `-encoding UTF-8`), the JVM-unit/conformance build compiled with no explicit encoding and fell back to a non-UTF-8 default charset, failing with ~1178 "unmappable character" errors against this codebase's em-dashes and curly quotes. A prior environment's default charset happened to mask this. Added `-encoding UTF-8` to the `javac` invocation in `build-conformance.sh`; `make qa-android` now passes clean.

### Build / QA tooling
- **`scripts/setup-qa-env-garuda.sh`** — one-shot, idempotent bootstrap for the full programmatic QA on Garuda/Arch (the `pacman` sibling of `scripts/setup-waydroid-fedora.sh`): Python QA venv + JDK 17 + Android SDK (build-tools 35+36, platform android-36) + Playwright/Chromium + optional waydroid, wiring `~/.config/focuslock-android.env.sh`.
- **QA venv is now Python 3.12** (provisioned via `uv`), which **resolves the long-standing py3.14 full-suite `[Errno 9] EBADF` isolation bug**: the entire `pytest tests/` suite runs in one process — **1173 passed, 18 skipped** (1172 at the time this venv fix landed; +1 is the new bridge-relock test below). Prior handoffs' "run subsets only" workaround is no longer required. `ruff check` + `ruff format --check` clean (`(OLD)/` scratch copies excluded from lint).
- **`scripts/setup-qa-env-garuda.sh` gained `--tor`** — fetches the A3 Tor deps (tor-android AAR + jtorctl + bcprov) to `~/android-libs` and a **JDK 24** to `~/.jdks`. The tor-android AAR is Java-24 bytecode, so the Tor-on build needs a JDK-24 `javac` (a JDK-17 `javac` can't even read `TorService.class`) in addition to build-tools 36 for `d8` — a requirement that was undocumented and left the Tor build broken on a JDK-17 box (now fixed + verified: Tor-on slave 32 MB with `libtor.so` ×4 ABIs, `apksigner` VERIFIED; default-off build unchanged at 234 KB, zero Tor). See `docs/TOR-ONION.md`.

### On-device QA (waydroid, 2026-07-05)
- **Covert-removal + safety-floor guarantees validated on-device** via `scripts/device-qa-waydroid.sh` (Garuda/Arch): the Collar declares **no `CAMERA` / no `READ_SMS`** permission; all three APKs install; the Collar has no runtime CAMERA permission; and — the key one — with `com.focuslock` set as **device-owner**, `DISALLOW_FACTORY_RESET` stays **absent**, proving factory reset is never blocked even in the mode that used to enforce it.
- **Bridge safeword guard is now unit-tested** (`tests/bridge_relock_test.sh` + pytest wrapper `tests/test_bridge_relock.py`). It sources the real `focuslock-bridge.sh` (now source-guarded so tests can load it without starting the poll loop) and drives `poll_device` with a mocked `adb_dev`, asserting the bridge re-locks when `active=1 & !released` but **never** re-locks once `focus_lock_released=1` (safeword honored, launcher re-enabled) — the terminal-release invariant, no device/adb/sudo required.

<!-- ───────── 2026-05-25 whole-ecosystem review (delivery + security + Phase-3 polish + payment + conformance QA) ───────── -->

### Security
- **Collar `/mesh/sync` gossip was fully unauthenticated — now Lion-signature-verified** (`android/slave/src/com/focuslock/ControlService.java`, `tests/test_android_conformance.py`). The Collar read an attacker-controlled `orders_version` and, if higher, parsed `orders` and called `applyOrdersFromMesh()` with **no signature check** (the `SigVerifier` exempts `/mesh/*`) — any peer on the gossip HTTP port could lock/paywall/message, and a huge `orders_version` poisoned all later legit orders. The orders are already Lion-signed end-to-end on the wire; the Collar just ignored the field. New `verifyMeshOrdersSignature()` re-attaches the wire `signature` as a map field and runs `VaultCrypto.verifySignature` (canonicalizes map-minus-signature) on **both** gossip paths (incoming push `handleMeshSync` + outgoing poll-response parser). Permissive only when `lion_pubkey` is unset (pre-pairing bootstrap), matching `apply_remote`. The vault path was already verified — this brings gossip to parity. Proven by `tests/test_android_conformance.py::TestSlaveCollarConformance` (5 cases): Python-signed orders ACCEPTED (won't brick gossip), forged/tampered/empty-sig REJECTED.
- **Collar SMS `sit-boy` trigger hardened + shared-secret gate now provisioned** (`android/slave/src/com/focuslock/{SmsReceiver,ControlService}.java`, `android/controller/src/com/focusctl/MainActivity.java`). `SmsReceiver` wrapped `Long.parseLong` and clamped `mins` to `[0, 525600]` (fixes a crash on >19-digit minutes + `mins*60000` overflow). Added an opt-in `focus_lock_sms_token` shared secret: when set, the command must carry it (`sit-boy <token> …`), defeating sender-number / caller-ID spoofing. **The token is now provisioned**: Collar `doPair` generates a random 8-char `[A-Za-z0-9]` token (`genSmsToken`/`ensureSmsToken`), persists it to `focus_lock_sms_token` (auto-arming the gate), and returns it in the pair JSON; Lion's Share stores it per-bunny and shows `sit-boy <token> 15 $20` (tap-to-copy) in Setup. **Operator note: existing direct Collars need a one-time re-pair to provision.** Paywall stays server-authoritative (the SMS amount is reported as a `sit_boy` event and clamped server-side).
- **Direct-mode `/mesh/status` is now signed + verified** (`android/slave/src/com/focuslock/ControlService.java`, `android/controller/src/com/focusctl/MainActivity.java`, `android/slave/test/com/focuslock/ConformanceCli.java`, `tests/test_android_conformance.py`). In serverless/direct mode the Collar served `"signature":""` and Lion's Share trusted a plain GET — a LAN MITM could spoof `locked`/`paywall`/`escapes`. The Collar's `handleMeshStatus` now builds a canonical flat "status core" (locked/escapes/paywall/timer_remaining_ms/task_reps/task_done/offer/offer_status/sub_tier/orders_version, native Boolean/Long/String types) and signs it with `focus_lock_bunny_privkey` via `VaultCrypto.signBlob`. Lion's Share `verifyStatusSignature` rebuilds the identical core and verifies with the paired `bunny_pubkey_b64`, gated in `meshGet`'s direct branch — a forged/unsigned status returns `null` so the UI keeps its last-good snapshot. Permissive only pre-pairing. New slave `sign-status` conformance subcommand + 2 tests (Collar-signed status verifies in Python; a tampered field is rejected). **Breaking: old Collar (empty sig) + updated Lion's Share in direct mode → status rejected until both are updated together (fail-closed by design).**
- **Collar `handle_mesh_order` OR-logic tightened** (`focuslock_mesh.py`, `tests/test_mesh.py`). Once `lion_pubkey` is set, a valid Lion **signature** is required to fire `apply_fn` — a bare PIN no longer authorizes orders; the PIN is bootstrap-only when no pubkey exists yet. Covered by `tests/test_mesh.py::TestHandleMeshOrderAuth`.
- **Exported Collar `FocusActivity` hardened against jail-DoS** (`android/slave/src/com/focuslock/FocusActivity.java`). It must stay `exported` to act as the HOME launcher, so any app could `startActivity` it. Added the lock-state guard (`isLockActive()`) at the **top** of `onCreate`, before any side effect (immersive / SHOW_WHEN_LOCKED / starting ControlService / flashing the jail UI) — a not-locked launch bounces to the prior launcher and `finish()`es immediately. `focus_lock_active` can't be set by an attacker, so it's the correct authorization signal. The pre-existing `onResume` guard stays as defense-in-depth.
- **Desktop collar secret-file permissions** (`focuslock-desktop.py`, `focuslock-desktop-win.py`). Linux chmods the config dir `0700` every start and warns if `config.json` is group/other-readable; Windows `_restrict_to_owner_windows()` runs guarded `icacls /inheritance:r /grant:r` (chmod can't express 0600 on Windows). Protects the plaintext `admin_token` + relay privkey.

### Fixed
- **Lion↔Bunny message delivery — silent non-delivery, false "Sent", and colliding IDs** (`focuslock_mesh.py`, `focuslock-mail.py`, `android/controller/src/com/focusctl/MainActivity.java`, `android/companion/src/com/bunnytasker/MainActivity.java`, `tests/test_message_delivery.py`). Three root causes from the reported "messages don't arrive" complaint:
  - **Colliding message IDs** — `MessageStore` keyed ids `{ts}_{len(messages)}`; after the 500-message cap `len()` sticks near 500, so same-millisecond messages collided and `mark_read`/`edit`/`delete` hit the *first* match → wrong-message edits/deletes and messages that "won't mark read" and re-notify forever. Replaced with a monotonic `{ts}_{seq}` counter (`_init_seq()` resumes it on reload). +`client_msg_id` idempotency dedup in `add()`; `focuslock-mail.py /messages/send` threads it through (unsigned — stripping it only disables dedup).
  - **Lion saw "Sent" on a failed delivery** — the non-vault path masked a failed server-store post behind the `/api/message` direct-Collar fallback ("Sent via API"), and the vault path ignored the server-store result entirely. `doSendInboxMessage`/`postLionMessage` now treat the **server store** (what Bunny Tasker's chat fetches) as authoritative for "Sent", with a bounded retry reusing `ts`+`client_msg_id` (server dedups), and honest "Not delivered — check connection and resend"; vault/direct-Collar writes are best-effort secondaries.
  - **Bunny's reply was fully optimistic** — `sendMessage` rendered the bubble and called `markMandatoryReplied()` regardless of POST success, so a failed reply cleared the mandatory-reply obligation and suppressed the auto-lock while the Lion never received it. Now records the check-in + clears the obligation **only on actual delivery**, with the same bounded retry and a failure Toast.
  - 9 cases in `tests/test_message_delivery.py` (id uniqueness across the cap, `client_msg_id` idempotency, mark/edit/delete hit the right message).
- **Payment scanner credited unrelated transactions** (`focuslock_mesh.py`, `shared/focuslock_payment.py`, `android/companion/src/com/bunnytasker/MainActivity.java`, `tests/test_payment.py`). The server scans Lion's payee inbox and matches each "received money" email against Bunny's payer allowlist; two bugs over-credited the paywall. (1) **fail-OPEN** — an empty `payer_allow` logged a warning and credited anyway. (2) **loose match** — `matches_payer` did a case-insensitive substring of each needle against the whole subject+body, so a generic needle (bare domain, `interac.ca`, a short token) matched every e-transfer's boilerplate, and `joe` ⊂ `joey`. Fix: a generic-needle guard (`_payer_needle_is_generic` — free-email domains + channel tokens + len<3), `_payer_needle_matches` (full emails match as substring; names/handles on word boundaries), and the scanner now **fails closed** (unconfigured or all-generic allowlist credits nothing). `matched_payer_needle()` records which needle matched **in the server log only — NOT the Lion-visible `/mesh/ledger`** (preserves the payee/payer privacy split; a draft that put it in the ledger was caught + reverted). `set_payer_allow` returns `effective_count`/`generic_rejected`; Bunny Tasker surfaces "⚠ Set, but entries too generic — payments won't be credited" and drops the stale "every payment will count" copy. **Operator note: if a payer entry was too generic, payments stop crediting until a specific identifier (full e-transfer email/name) is entered; past mis-credits are reversible via `/admin/reverse-payment`.**
- **Collar applied gossiped orders non-atomically — torn lock-screen render** (`android/slave/src/com/focuslock/{ControlService,MeshOrderApply}.java`, `android/slave/test/com/focuslock/MeshOrderApplyTest.java`). `applyOrdersFromMesh` wrote `MESH_ORDER_KEYS` in array order, with `lock_active` at index 0 and `message`/`mode`/`paywall`/`task_text` later; `FocusActivity`'s 5s poll (and the launch it triggers) could observe the new `active=1` paired with the **old** message/mode/paywall. Extracted a pure `MeshOrderApply.orderForApply` (no Android deps) that writes every non-`lock_active` key first and `lock_active` last, so by the instant the lock flag flips the content is already current. +4 JVM unit tests.
- **Linux desktop collar didn't refresh the lock wallpaper on mid-lock changes** (`focuslock-desktop.py`). The cairo lock wallpaper already renders message/pinned/paywall, but the poll tick's already-locked branch called `update_lock`, which only poked **dead** GTK labels (`self.windows` is never populated; the browser `lock_process` is never launched on Linux) and never regenerated the PNG — so pinning/changing a message while locked was invisible until unlock+relock. Added a `_prev_display` tuple guard that regenerates the PNG and re-points `kscreenlockerrc` (extracted `_apply_kde_lock_wallpaper`, shared with `show_lock`) when `(message, pinned, paywall)` changes; the KDE greeter reloads on the next paint (e.g. the 1s enforce re-lock after an unlock attempt). Mirrors the Windows collar's existing guard.
- **Repo-wide ruff `check` + `format` were red on `main`** (`focuslock-mail.py`, `focuslock-tray.py`, `tests/test_payment.py`, +4 format-drifting files). Pre-existing ruff 0.15.x version drift (not introduced by this work) had both CI gates failing; fixed all 5 `check` errors (RUF046×2, UP012, RUF003, I001) and ruff-formatted the drifting files. Both gates green repo-wide.

### Changed
- **E2EE "not encrypted" warning in both apps (warn + allow)** (`android/controller/src/com/focusctl/MainActivity.java` + `res/layout/activity_main.xml`, `android/companion/src/com/bunnytasker/MainActivity.java` + `res/layout/activity_main.xml`, `android/controller/test/com/focusctl/E2EEHelperTest.java`). E2EE is per-peer (`E2EEHelper.canEncrypt(peerPub)` is false until pairing exchanges the key); both apps silently fell back to plaintext. Now each shows a persistent "⚠ Not encrypted — <peer>'s key not yet exchanged" banner in the chat view when no peer pubkey is available, plus a plaintext send signal (controller "Sent ⚠ not encrypted" status; companion a "Sending unencrypted…" Toast). Messages still send (these are key-exchange-pending bootstrap states). +3 JVM unit tests pinning the `canEncrypt`/`canDecrypt` truth table.

### Added
- **No-device Android test layer: JVM unit tests + Java↔Python conformance harness** (`android/test-support/android/util/{Base64,Log}.java`, `android/build-conformance.sh`, `android/{controller,slave}/test/com/**`, `tests/test_android_conformance.py`, `Makefile`, `docs/ANDROID-CONFORMANCE.md`, `.github/workflows/ci.yml`). The Android Java had no enforced test coverage. Added a Gradle-free, device-free harness: tiny `android.util.*` shims (test classpath only) let the crypto classes compile against a JUnit 5 + real `org.json` classpath; `build-conformance.sh` compiles + runs them and emits CLI commands for the Python side. Conformance tests drive the compiled CLIs (canonical_json parity, order sign/verify, message pipe-payload, the Collar `/mesh/sync` verify gate, the direct `/mesh/status` signature) so any Java↔Python canonical-form drift fails CI rather than silently bricking gossip. JUnit unit tests: controller `VaultCryptoTest` (6) + `E2EEHelperTest` (3), slave `MeshOrderApplyTest` (4). `make qa-android` + a CI `build-android` step run it all. Doc: `docs/ANDROID-CONFORMANCE.md`.
- **Server/mesh coverage gates** (`.coveragerc.mesh`, `.coveragerc.server`, `Makefile`, `.github/workflows/ci.yml`). `focuslock_mesh.py` and `focuslock-mail.py` had no enforced coverage (the 95% gate was `shared/`-only). Added `.coveragerc.mesh` with an 80% floor on `focuslock_mesh.py` (measured 84% from the topical subset) and `.coveragerc.server` (report-only, ratchet TODO). `make qa-cov-mesh`/`qa-cov-server` + a CI `test`-job step on the 3.12 leg.

<!-- ───────── end 2026-05-25 ───────── -->

### Fixed
- **Atomic `add-paywall` — closes the R-M-W race surfaced by `tests/test_perf_smoke.py::test_admin_order_concurrent`** (`focuslock_mesh.py`, `focuslock-mail.py`, `tests/test_mesh.py`, `tests/test_perf_smoke.py`). Pre-fix, `mesh_apply_order::add-paywall` did `current = orders.get("paywall", "0"); orders.set("paywall", str(current + delta))` — `OrdersDocument.get` and `.set` are individually locked but the increment between them isn't. Concurrent `/admin/order add-paywall` calls dropped increments; in the perf smoke, 100 expected dollars landed as ~$53. New `OrdersDocument.add(key, delta, default=0)` holds `self.lock` across the whole read-modify-write. `add-paywall` switched to use it. Perf concurrent test tightened from soft-gate (`paywall > 0`) to strict equality (`final_pw == expected_total`); ConnectionResetError flake at 100-simultaneous-TCP-connect handled with a 3-attempt retry in `_post`. New unit test `tests/test_mesh.py::TestOrdersDocument::test_add_atomic_under_concurrent_threads` proves `OrdersDocument.add` loses zero increments under 50-thread × 100-increment contention. +7 unit tests covering corrupt-string state, non-int delta, negative delta, missing-key default, and the contention regression. Tracked from `docs/PUBLISHABLE-ROADMAP.md § Medium-term` (now removed). No coordinated rollout — server-side fix only.

### Added
- **Stream B first pass — usability findings + small fixes** (`web/signup.html`, `focuslock-mail.py`, `docs/USABILITY-AUDIT-2026-04-28.md`). Stream B is 24–32h total and won't fit a single session; this pass ships the session-friendly fixes from `docs/AUDIT-PLAN.md` § Stream B and captures the L-effort device-walk surfaces in a structured findings doc.
  - **Wizard ASCII step icons** (`web/signup.html`) — replaced `[]`/`[K]`/`[$]`/`[R]`/`[S]`/`[?]`/`[OK]` with `aria-hidden="true"` emoji glyphs (👋 🔑 💌 📋 ⭐ 👀 ✨). Decorative-only; screen readers skip them. Closes the medium-term "Replace ASCII step icons" roadmap entry.
  - **Subscription-amount help text** (`web/signup.html`) — sharpened the per-tier amount copy. Old text implied amounts were configurable from Lion's Share today (which they aren't — that's a separate medium-term roadmap item). New text: "amounts above are the relay's built-in defaults; switch tiers from Lion's Share → Money; per-mesh custom amounts on the roadmap."
  - **Server-side error message audit** (`focuslock-mail.py`) — 9 mesh routes' `respond(400, {"error": "bad path"})` and 2 vault routes' `respond(400, {"error": "bad vault path"})` now report the expected route shape. Example before: `bad path`. After: `bad path — expected /api/mesh/{mesh_id}/auto-accept`. Help integrators (curl, automated callers) diagnose mismatched paths without reading source. Routes covered: auto-accept, subscribe, unsubscribe, gamble, payments, escape-event, state-mirror, deadline-task/clear, messages/{send|fetch|mark|edit|delete}, plus both `/vault/{mesh_id}/<action>` POST + GET handlers.
  - **Findings doc** (`docs/USABILITY-AUDIT-2026-04-28.md`) — captures every surface from the audit plan with explicit status: ✅ done this PR (5), ⏸ session-friendly follow-up (~4), 📱 operator-walk (~24 — Lion's Share + Bunny Tasker + Collar 9 lock modes + desktop collars Linux + Windows + pairing routes). Each 📱 row names what device + flow it needs.

- **Stream C closeout — QA infrastructure expansion** (`Makefile`, `staging/qa_matrix.py`, `tests/test_e2e_unsubscribe_deadline.py`, `tests/test_e2e_uncovered_webhooks.py`, `tests/test_e2e_disposal_token.py`, `tests/test_e2e_public_routes.py`, `tests/test_perf_smoke.py`, `docs/UI-AUTOMATION-DECISION.md`, `docs/QA-CHECKLIST.md` header, `docs/QA-pairing.md` cross-link, `tests/ui/conftest.py` cross-link, `staging/qa_runner.py` `--quiet` flag, `pyproject.toml` `slow` marker). Five threads landed in one bundle, closing item 1 (formal shelve), item 3 (route coverage), item 4 (`make qa`), item 5 (regression matrix), and item 6 (perf smoke) of `docs/AUDIT-PLAN.md` § Stream C. Item 2 (IMAP scanner end-to-end) stays out of scope per the audit plan — needs an operator-side throwaway test inbox.
  - **`make qa`** — root `Makefile` with `qa-staging-up`/`qa-staging-down`/`qa-clean`/`qa-pytest`/`qa-runner`/`qa-wizard`/`qa-index`/`qa-perf`/`qa-matrix`/`lint`/`help` targets. Boots a staging relay on `127.0.0.1:8435` against `staging/config.json`, waits for `/version` to respond, runs all four QA layers, then tears down + state-cleans `/tmp/focuslock-staging`. Pidfile at `staging/.relay.pid`, log at `staging/.relay.log` (both gitignored). Auto-detects `.venv/bin/python3` and falls back to system `python3`. Replaces the "manually start `staging/start-staging.sh`, wait, run each script in turn" sequence the operator was doing by hand.
  - **`focuslock-mail.py` route coverage push (+36 tests)** — four new e2e files pin the routes the 2026-04-27 audit didn't reach. `test_e2e_unsubscribe_deadline.py` (13 tests) covers `/api/mesh/{id}/unsubscribe` and `/api/mesh/{id}/deadline-task/clear` happy/bad-sig/stale-ts/unknown-mesh/unknown-node/no-task-armed paths. `test_e2e_uncovered_webhooks.py` (6 tests) covers `/webhook/entrap` (admin-token gated) and `/webhook/location` (currently public — pinned for the audit-L-2 deferred-tightening tracking). `test_e2e_disposal_token.py` (7 tests) covers `/admin/disposal-token` mint side: token gate + max_amount-clamped-to-200 + ttl-clamped-to-7200 + defaults + distinct-mints. `test_e2e_public_routes.py` (10 tests) is a smoke matrix for `/version` + `/mesh/ping` + `/pubkey` + `/api/paywall` + `/web-login` + `/controller` + `/api/logout` — assert 200/404 + content-type + body shape; catches dispatch regressions cheaply. Tests `1080 → 1116`, ruff clean.
  - **Performance smoke (`tests/test_perf_smoke.py`, +3 tests, opt-in)** — `test_admin_order_throughput` (100 sequential add-paywall, p95 < 250ms, exact paywall sum), `test_admin_order_concurrent` (20×5 threads, soft-gate "no 5xx + paywall in sane bounds" — surfaces a known non-atomic R-M-W in `mesh_apply_order::add-paywall` at `focuslock-mail.py:681`, intentionally tracked rather than tightened here because it touches enforcement-sensitive code), `test_vault_gc_under_load` (200 blob append + GC pass < 5s + latest blob retained). Skipped by default (`@pytest.mark.slow` + `PERF_TESTS` env gate); opt-in via `make qa-perf` or `PERF_TESTS=1 pytest`. Uses `ThreadingHTTPServer` so the concurrent path isn't bottlenecked.
  - **Regression matrix automation (`staging/qa_matrix.py`)** — single CLI walks the 14 sections of `docs/QA-CHECKLIST.md`, runs the programmable subset (sections 0, 2–10, 13), classifies the rest as `MANUAL`, and emits a pass/fail/skip/manual table to stdout + `staging/qa-matrix-result.json`. `make qa-matrix` wraps it. Baseline run on this branch: 10 pass · 0 fail · 0 skip · 4 manual (1, 11, 12, 14). `--section N` for spot-runs, `--json` for downstream tooling. `docs/QA-CHECKLIST.md` gained a §Programmatic coverage table summarizing which sections are programmable vs manual.
  - **UI automation formal shelve (`docs/UI-AUTOMATION-DECISION.md`)** — closes the audit acceptance criterion ("uiautomator2/Appium spike concluded — either working harness or shelved with rationale"). Documents the 2026-04-23 Waydroid + adbd wedge, the Appium tax (dual-device fragility, maintenance burden), the Espresso = Gradle blocker, and the three conditions to revisit (pairing-flow regressions become a pattern; a contributor commits to operating the harness; the aapt2 pipeline gets retired). Cross-linked from `docs/QA-pairing.md` and `tests/ui/conftest.py`.

### Documentation
- **Audit 2026-04-27 Stream A exit** — `docs/AUDIT-2026-04-27-EXIT.md` + roadmap update. Documents the five-commit closeout (`09d73be`, `44c5fe4`, `4db89e2`, `ac16335`, `1c2c0aa`): every High and Medium fixed, L-3 fixed, M-5 + L-1 + L-2 + L-4 tracked in the medium-term roadmap section. Coordinated rollout summary (slave APK 74→75 / companion 56→57 / desktop collar redeploy / out-of-repo `sync-standing-orders.sh` Bearer-token add). Recommends Stream C (QA infrastructure) next per the plan's A→C→B ordering. Roadmap top status block updated; Short-term audit item marked done; deferred Stream A findings added to Medium-term backlog.

### Security
- **Audit 2026-04-27 round-4 — desktop-signed heartbeat (M-1) + H-1 remainder gating `/memory` + `/standing-orders` + `/settings`** (`focuslock-mail.py`, `focuslock-desktop.py`, `focuslock-desktop-win.py`, `installers/re-enslave-server.sh`, `tests/test_audit_2026_04_27_round4.py`).
  - **M-1** — `/webhook/desktop-heartbeat` verifies a vault-node signature when the body carries one. Mirrors the existing `/api/mesh/{id}/state-mirror` pattern. Canonical envelope `"{mesh_id}|{node_id}|desktop-heartbeat|{ts_ms}"`, signed with the desktop's `_vault_privkey_pem` (the same key Lion approved during register-node-request), verified against `_vault_store.get_nodes(mesh_id)`. Linux desktop collar (`focuslock-desktop.py:phone_home`) signs on the way out; Windows desktop doesn't post heartbeats so no Win-side change. Unsigned heartbeats from legacy single-tenant collars still flow (mesh_id falls through to `OPERATOR_MESH_ID`, scoped to operator's own host) — preserves backward compat.
  - **H-1 remainder** — `/memory`, `/standing-orders`, `/settings` all require `admin_token` (`?admin_token=` or `Authorization: Bearer`). Same dual-path pattern as `/enforcement-orders` (round-1). `/standing-orders` and `/settings` already redacted `ADMIN_TOKEN` before serving but still leaked operational structure (rule framework, hook configuration); `/memory` did NOT redact and could expose tactical memories + embedded tokens. The route handlers also switched from `self.path == "/foo"` to `self.path.split("?")[0] == "/foo"` so the query-param path doesn't fall through to 404.
  - **Caller updates (coordinated rollout)**: `focuslock-desktop.py:sync_standing_orders()` adds `Authorization: Bearer ${ADMIN_TOKEN}`, skips silently when no token; `focuslock-desktop-win.py:apply_acl_lockdown()` setup-time sync of both `/standing-orders` and `/settings` adds the same header; `installers/re-enslave-server.sh` health check now hits `/standing-orders` with the operator's `FOCUSLOCK_ADMIN_TOKEN` if set, falls back to unauthenticated `/mesh/ping` otherwise. The out-of-repo `sync-standing-orders.sh` script (lives on the homelab, distributed via scp by `installers/install-standing-orders.sh`) needs an operator-side update to send the same header — single-line change.
  - +25 tests (6 desktop-heartbeat × {happy/legacy-unsigned/bad-sig/unknown-node/stale-ts/invalid-mesh}, 18 admin-gate × 3 paths, 1 offline signer roundtrip). Full sweep: 1055 → 1080 passing, ruff clean. Round-5 (Stream A exit doc) follows.

### Security
- **Audit 2026-04-27 round-3 — slave-signed `/webhook/verify-photo` and `/webhook/register` (M-3 full + M-4 full)** (`focuslock-mail.py`, `android/slave/...`, `android/companion/.../MainActivity.java`, `android/{slave,companion}/AndroidManifest.xml`, `tests/test_audit_2026_04_27_round3.py`). Two more findings closed by extending the H-2 `_verify_slave_signed_webhook` helper to two routes with live phone-side callers. Slave APK bumps **v74 → v75 (8.31 → 8.32)**, companion APK **v56 → v57 (2.23 → 2.24)** — coordinated rollout.
  - **M-3 (full)** — `/webhook/register` is bunny-signed. Slave's phone-home thread (`ControlService.java:2030+`, both Tailscale and LAN-fallback paths) now builds the body via `org.json.JSONObject` and runs it through `SlaveSigner.signAndAttach(this, "register", body)`. Round-1's device_id shape validator stays in place as defense-in-depth (sig covers spoofing; validator covers a future signer that emits a path-shaped device_id). Pre-fix: any LAN caller could spoof a phone registration with arbitrary device_id + IPs and pollute `IP_REGISTRY_FILE`.
  - **M-4 (full)** — `/webhook/verify-photo` is bunny-signed from both the slave (`FocusActivity.java:1115`, in-Collar photo task verification) and the companion (`MainActivity.java:1577`, deadline-task photo proof). New `MainActivity.buildBunnySignedBody(eventType, innerJson)` helper extracted from the existing `sendSignedBunnyWebhook` flow so callers that need to read the HTTP response (verify-photo returns `{passed, reason}`) can sign without going through the fire-and-forget wrapper. `sendSignedBunnyWebhook` itself now delegates to the new helper. Pre-fix: any caller could submit base64 photos for LLM verification — burns operator GPU/CPU, can be used to fingerprint the model, can spoof a `passed: true` verdict to clear a deadline task.
  - **Min-version coordination**: the relay's 403 hint reports `min_collar_version=75` for both routes. An old slave APK (≤ v74) won't sign these two paths and falls through to the 403; the companion's verify-photo caller surfaces the same 403 + must update to ≥ v57. Other H-2 routes (compliment, gratitude, love_letter, etc.) keep `min_collar_version=74` since their signing path is unchanged — a v75 slave still produces v74-compatible signatures for those.
  - The round-1 parametrized device_id validator matrix moved from `tests/test_audit_2026_04_27_round1.py::TestRegisterDeviceIdValidator` to `tests/test_audit_2026_04_27_round3.py::TestRegisterDeviceIdValidatorSigned` because the validator now sits behind the sig gate; the matrix exercises both layers together.
  - +12 new round-3 tests + 16 relocated validator cases. Full sweep: 1043 → 1055 passing, ruff clean. Round-4 (M-1 desktop-heartbeat + H-1 remainder gating `/memory` + `/standing-orders` + `/settings`) follows.

### Security
- **Audit 2026-04-27 round-2 — auth gates on no-caller / installer-only routes** (`focuslock-mail.py`, `focuslock-desktop.py`, `focuslock-desktop-win.py`, `tests/test_audit_2026_04_27_round2.py`). Three more findings closed, no APK or homelab-script coordination required:
  - **M-2** — `/webhook/controller-register` requires `admin_token` (or legacy `auth_token`). Without it, any LAN caller could write `/run/focuslock/controller.json` + redirect `mesh_peers["lions-share"]` to an attacker IP. No Android caller in repo; only the operator's release/install scripts hit this, and they already have the token in scope.
  - **M-4 partial** — `/webhook/generate-task` is now bunny-signed via `_verify_slave_signed_webhook` (the H-2 helper). No live caller in the repo today; gated preemptively to close a resource-exhaustion vector (route invokes Ollama with no rate limit) and prevent any future caller from being trivially spoof-able.
  - **M-6** — desktop collar `/api/pair/create` (port 8435, binds 0.0.0.0) requires `admin_token`. Linux + Windows both gated. The endpoint leaks `{mesh_pin, pubkey_pem, homelab_url, mesh_url}` — pre-fix, any LAN caller could harvest the mesh PIN and join the gossip layer. Windows desktop also gained ADMIN_TOKEN module-load (it had been Linux-only). Tests skip the functional desktop fixture when PyGObject isn't installed; gate-logic test runs everywhere.
  - +13 unit tests (3 skipped on bare CI), 1030 → 1043 passing. Ruff clean. Round-3 (slave-signed webhooks) and round-4 (desktop-signed + H-1 remainder) still pending — see `docs/AUDIT-FINDINGS-2026-04-27.md` §Acceptance gate.

### Changed
- **Lion's Share web remote restructured into 4 themed tabs** (`web/index.html`). Old structure was 3 tabs (Control / Advanced / Inbox) with a "Power Tools" grab-bag in Advanced that mixed paywall actions, scheduling, location, effectors, economy, and the destructive entrap. New structure groups by intent: **Lock** (status, lock builder, quick locks, paywall, unlock, danger zone with entrap), **Rules** (mode, lock style — was "Modifiers" — , writing task, schedule, location card with geofence/confine, toy, voice), **Money** (subscription status + tier change, daily tribute, streak bonus, paywall actions), **Inbox** (lion-only-pinned, send-message, pin-notification, devices). Renames: "Bunny Balance" → "Paywall"; "Modifiers" with cryptic "+5m/esc" → "Lock Style" with "Escape Penalty"; "Good Boy" → "Toy"; "Scheduling" → "Schedule". Help text added under most cards explaining what each does. Subscription status was previously buried at the bottom of Inbox; now top-of-Money with an explicit "Change Tier…" button instead of being hidden inside Power Tools. Pin-notification (banner across lock screen) split out from "Send Message" so the difference between a thread message and a persistent banner is explicit. All button IDs preserved so the JS event handlers + the QA harness's network-interception assertions still work; only tab structure + labels changed. Number-key tab shortcut now maps `1`→Lock, `2`→Rules, `3`→Money, `4`→Inbox. Re-QA: pytest 960/960, qa_wizard_browser 8/8, **qa_index_browser 20/20** (gained 1 case for Money-tab load), qa_runner 49/49, ruff clean.

### Added
- **Programmatic Waydroid + headless-browser QA harness** (`staging/qa_wizard_browser.py`, `staging/qa_index_browser.py`, `staging/qa_runner.py` extension, `docs/QA-wizard-2026-04-27.md`). **Four layers, all green from a clean-room run:** (1) **wizard browser walkthrough** — 8 Playwright cases drive every step of `web/signup.html` headlessly (welcome / key validation / IMAP toggle / rules / subscription select / review / result+QR / skip-path) with screenshots in `staging/qa-screens/`; (2) **web-remote (index.html) browser walkthrough** — 19 Playwright cases (`qa_index_browser.py`) drive every clickable button in `web/index.html` and use network interception to assert the **exact action + params** posted to `/admin/order` (lock + 15m quick-lock + unlock + paywall add/clear/custom; clear-paywall modal; bedtime + screen-time set/clear; pin-message; subscription tier; tribute toggle; streak toggle; send-message; logout) plus verification that LAN-only buttons (play-audio, speak, confine-home, lovense) are properly disabled with `Requires LAN access` tooltips in relay mode; (3) **comprehensive order driver** — `qa_runner.py` extended from 12 cases to **49 cases** covering every action surface reachable through `/admin/order` in the web UI's relay mode: 9 lock modes (basic + 8 variants), bedtime/screen-time/geofence scheduling, all 4 subscription tiers + unsubscribe, tribute set/clear, streak start/stop, gamble (with + without paywall), entrap safety, send-message (plain + pinned + mandatory), pin-message + lion-only-pinned, deadline-task set/clear, plus 5 paywall variations and 3 LAN-only safety probes; (4) **Waydroid sanity** — container boots, slave + companion APKs install via `pm install -r`, both processes start. Found and fixed 3 issues during the QA: (a) **wizard PIN didn't pass through to `/api/mesh/create`** — silent feature loss in the wizard PR; user-entered PIN was discarded for the auto-generated random one, fixed by extracting + validating + forwarding `pin` to `MeshAccountStore.create`; (b) **`staging/qa_runner.py` regressed against audit-C1 mesh-order signature gate** (since v1.2.0 — nobody had re-run it on a mesh where `_lion_pubkey` had been adopted; bare `admin_token` no longer suffices once a mesh has `lion_pubkey` set), qa_runner now loads `staging/lion_privkey.pem` and signs `canonical({action, params})` with PKCS1v15+SHA256; (c) **`web_dir` was hardcoded** to `/opt/focuslock/web` at two sites in `focuslock-mail.py` so staging couldn't serve `web/` without sudo — now reads `FOCUSLOCK_WEB_DIR` env override (default unchanged in production). Final clean-room re-QA (deleted state dir → fresh relay → run each suite cold): pytest 960/960, wizard browser 8/8, **index-remote browser 19/19**, qa_runner 49/49, ruff clean. Full QA report with deferred-to-audit findings at `docs/QA-wizard-2026-04-27.md`.
- **Signup wizard with optional initial config** (`web/signup.html`, `focuslock-mail.py:_apply_initial_mesh_config`). The old single-card signup form replaced with a 7-step fullscreen wizard: welcome → Lion key + emergency PIN → payment scanning (IMAP) → default rules (tribute / bedtime / screen-time) → subscription preset → review → result. Every step except "Lion key" is optional and shows a "Skip" path. The wizard collects values into a single `initial_config` dict and posts it alongside `lion_pubkey` to a now-extended `/api/mesh/create`. Server-side `_apply_initial_mesh_config(mesh_id, cfg)` walks the dict and dispatches each key to its existing server-side action (`set-payment-email`, `set-tribute`, `subscribe`, `set-bedtime`, `set-screen-time`) via `_server_apply_order`, returning the list of applied actions in the response so the wizard can confirm what stuck. **Failure isolation:** any one action's exception is logged but never aborts the mesh-create — partial config is recoverable from Lion's Share later, an aborted account is not. **Validation:** IMAP requires all three of host/user/pass; bedtime requires both lock-hour and unlock-hour in 0..23; tribute and screen-time require a positive int; sub_tier must be one of bronze/silver/gold (case-insensitive). Per-tier subscription **amounts** stay at server defaults (bronze $25 / silver $35 / gold $50 per week) for v1 — changing those is a deeper refactor flagged for the upcoming audit. New CSS: dark + gold theme matching `web/index.html`, progress bar, step counter ("3 of 7"), choice-card UI for radio-style picks, mobile-responsive (≤540 px). 25 new unit tests in `tests/test_initial_mesh_config.py` pin every code path: empty/non-dict/unknown-keys → no-op; each recognized key applies its order; partial config doesn't block other keys; orders land in the new mesh's OrdersDocument, not the operator's. Live-tested end-to-end against a staging relay — full config → 5 actions applied, partial → only matching action, no config → empty list. Tests `935 → 960` (+25).

### Changed
- **`focuslock_mesh.MessageStore` complementary coverage (34 unit tests)** (`tests/test_message_store.py`). `tests/test_messages.py` already pinned the edit/delete/tombstone semantics from the audit-C4 work; this slice fills the rest of the contract surface so a regression in init/load/save, `add()`, `get()`, `mark_read()`, or `mark_replied()` surfaces as a sharp unit failure rather than a confusing message-loss in production. **Init + load:** empty init, init-with-path-but-no-file-yet leaves the file uncreated, existing-file loads, corrupt JSON swallowed (`_load` wraps in try/except). **save():** no-path is a noop, parent directory created on demand, atomic `.tmp + os.replace` (no stray `.tmp`), `OSError` swallowed at the warning level. **add():** auto-assigns `ts` (int, current ms) and `id` (`<ts>_<index>` shape) when missing, preserves explicit `ts`/`id`, generates unique IDs across rapid sequential adds, persists to disk per call, **size cap exactly 500** (550 adds → oldest 50 trimmed; `m50…m549` survive), returns the same dict mutated in place. **get():** empty returns `[]`, returns newest-first (reversed slice), default limit 50, custom limit honored, limit larger than store returns all available, the `reader` arg is currently accepted-but-unused (pinned to catch a future filter change). **mark_read():** adds reader, dedups same reader, accumulates multiple readers, unknown id → `{"error": "not found"}`, persists to disk, initializes `read_by` field on first call. **mark_replied():** sets `replied: True`, unknown id → not-found, persists, idempotent. **Concurrency:** 20-thread × 25-add stress test (exactly the 500-cap boundary) — no message loss, all IDs unique despite contention; concurrent `mark_read` calls with the same reader from 10 threads dedup correctly. Tests `901 → 935` (+34).
- **`focuslock-mail.py` session-token + daily-blob-count + relay-node coverage (34 unit tests)** (`tests/test_session_blob_relay.py`). Four isolated security helpers now have direct contracts: **session-token trio** (`_issue_session_token` / `_is_valid_admin_auth` / `_revoke_session_token`) — token mints with `issued_at`/`expires_at`/`session_id`/`mesh_id` fields, expiry uses `_SESSION_TOKEN_TTL`, expired tokens pruned both on issue and on validate, master `ADMIN_TOKEN` accepted via constant-time compare and bypasses mesh-scope check, session tokens are mesh-scoped (cross-tenant block: token issued for mesh-A rejected for mesh-B), unscoped tokens (empty mesh_id == operator-scope) accepted for any mesh, `mesh_id=None` skips the scope check, revoke returns False for unknown tokens, revoked tokens are no longer valid; **`_daily_blob_count` / `_daily_blob_increment`** — per-mesh per-day counters, zero for unknown, increments accumulate, per-mesh isolated, stale-date pruning is *target-mesh-scoped* (counting m1 must not prune m2's stale entries — protects the UTC-rollover invariant for other meshes); **`_ensure_relay_node_registered`** — returns False when `RELAY_PUBKEY_DER_B64` is empty, first registration writes node with `node_type="server"`, idempotent on second call (no duplicate), key rotation replaces the relay entry rather than appending; **`_admin_order_to_vault_blob`** — early-exits when no relay key, no mesh_id + no `OPERATOR_MESH_ID`, no registered nodes, all nodes have empty pubkey; uses `OPERATOR_MESH_ID` fallback when `mesh_id=None`. Tests `867 → 901` (+34).
- **`focuslock-mail.py` MeshAccountStore + VaultStore class internals coverage (87 unit tests)** (`tests/test_account_vault_stores.py`). Direct contract tests for the two stateful, persisted-to-disk classes that govern multi-tenant correctness — pairing/auth (MeshAccountStore) and zero-knowledge blob storage + node approval/rejection (VaultStore). E2E suites mutate these classes through HTTP routes; this slice pins their internal semantics so a regression surfaces as a sharp unit failure instead of a confusing 403/410. **MeshAccountStore** (15 helpers): `__init__` creates persist_dir + tolerates corrupt JSON in `_load_all`; `create()` populates every required field, persists atomically, generates a 4-digit pin when omitted, accepts an explicit pin, mints unique `mesh_id`s, records the IP for rate-limiting; `check_rate_limit` prunes expired timestamps in-place + blocks at `RATE_LIMIT_MAX`; `join()` rejects bogus codes, succeeds case-insensitively with whitespace tolerance, increments `invite_uses` (reusable within TTL — one Lion can onboard multiple slaves), rejects expired codes; `get`/`validate_auth` handle unknown meshes + wrong tokens (`hmac.compare_digest`); `update_node` is silent on missing node + missing mesh; `is_vault_only`/`set_vault_only` toggle Phase D gate + handle unknown meshes; `list_mesh_ids` returns a snapshot list, not the live dict; `_save` is a no-op for unknown meshes; `_gen_invite_code` always produces `WORD-NN-WORD` from `_INVITE_WORDS`. **VaultStore** (15 helper groups): `__init__` creates `base_dir`; `_mesh_dir`/`_ensure_mesh` reject path-traversal/space/empty mesh_ids and create `blobs/` subdir; `_list_blob_versions` ignores non-json + invalid filenames; `current_version`/`total_bytes` zero on empty/invalid; `append()` rejects invalid mesh_id, non-int version, version-not-greater (including default-version-zero), writes blob atomically; `since()` returns blobs after a version + skips unreadable blobs; `gc()` no-ops on single version, age-based sweep keeps latest, count-based trim keeps newest, uses module defaults when args are None; `_read_json`/`_write_json` return default on missing/invalid/corrupt + roundtrip works; `get_nodes`/`add_node` dedup by node_id; `get_pending_nodes`/`add_pending_node`/`remove_pending_node` dedup + remove-missing is silent; `_rejection_key` returns `""` for empty/None pubkey + 24-char stable hash for non-empty; `is_rejected`/`add_rejected_node`/`clear_rejection` handle empty pubkey + missing entries + dedup. Tests `780 → 867` (+87).
- **`focuslock-mail.py` security-helper coverage push (44% → 45%, 79 unit tests)** (`tests/test_relay_helpers.py`). First slice of the relay coverage push. The e2e suites exercise these helpers transitively but never pin them in isolation — a regression in any one of them surfaces as a confusing e2e failure rather than a sharp unit failure. Eleven helpers now have direct contracts:
  - **`_sanitize_log`** — CR/LF/NUL escaping for log-injection defense (CodeQL `py/log-injection`); confirms the literal threat-model pattern is neutralized.
  - **`_pubkey_fingerprint`** — 16-hex-char sha256-truncate diagnostic ID; deterministic, distinct per key, handles empty/None/non-string.
  - **`_compute_source_sha256`** — running-source provenance hash; returns 64-char hex matching disk content, gracefully returns `None` on I/O error, cached `SOURCE_SHA256` constant matches the function.
  - **`_read_deploy_git_commit`** — `/opt/focuslock/.git_commit` first, sibling fallback; whitespace stripped; blank file → `None`; first candidate wins (sibling never opened when ops path succeeds).
  - **`_safe_mesh_id_static`** — alphanumeric + `-_` only, ≤64 chars; rejects path-separator / dot / null-byte / whitespace / non-string / over-length / empty / None.
  - **`MeshOrdersRegistry`** — get returns None for unknown, get_or_create persists per-mesh JSON, rejects invalid mesh_id with ValueError, `_load_all` picks up existing files.
  - **`_resolve_orders`** — operator-mesh registered branch, unknown-mesh / empty / None all fall back to legacy `mesh_orders` global.
  - **`_get_ntfy_topic`** — consumer mesh always derives `focuslock-{mesh_id}`, operator mesh keeps configured topic when set, falls back to derived when not, no-mesh uses configured topic, no-config returns empty (the literal audit-followup-#7 fix matrix).
  - **`_load_lion_pubkey_obj`** — PEM and bare-base64-DER both load, DER-with-line-wrapping handled, empty/None/garbage/invalid-b64/wrong-PEM-header → `None`.
  - **`_verify_signed_payload`** — RSA-PKCS1v15-SHA256 over `canonical_json({everything except 'signature'})`; PEM and DER both work, tampered payload rejected, wrong pubkey rejected, empty sig/pubkey/garbage rejected, `quiet=True` actually suppresses the warning log.
  - **`_verify_blob_two_writer`** — Lion-tried-first then iterate registered nodes; lion-signed → `("lion", "lion")`, node-signed → `("node", node_id)`, unsigned/empty-sig/unknown-signer/None-nodes → `(None, None)`, no-lion-pubkey falls through to nodes, node with empty pubkey skipped.
  - **`_vault_resolve_mesh`** — unknown returns `(None, None)`, known returns `(account, lion_pubkey)`, account without `lion_pubkey` returns `(account, "")`.
- **`focuslock_ntfy.py` test coverage 30% → 100%** (`tests/test_ntfy.py`). 29 new tests across 5 classes pin `ntfy_publish()` (default server `https://ntfy.sh`, custom server with trailing-slash strip, payload is `{"v": <version>}` only — zero-knowledge by construction, `Content-Type: application/json` header, POST method, exception swallowed for fire-and-forget, daemon thread for clean process exit) and `NtfySubscribeThread` (init defaults + trailing-slash strip + initial backoff=1s, `stop()` sets event, `run()` exits cleanly when stop already set, normal `_stream_messages` return resets backoff, exception doubles backoff capped at 60s, backoff cap respected from 60→60, stop_event during backoff wait breaks the loop, `_stream_messages` URL includes since cursor, since cursor advances on every message with id, empty/malformed lines skipped, `open` + `keepalive` events skipped, missing/unparseable/non-dict body handled, message without `v` field skipped, message without id leaves `_since` unchanged, stop_event mid-stream breaks the for-loop, on_wake exception logged but doesn't abort the loop). Tests `672 → 701` (+29).

### Added
- **Lion↔Bunny messaging — edit + delete + ntfy fan-out + Android UI** (`focuslock-mail.py:4007-4256`, `focuslock_mesh.py:1485-1617`, both Android `MainActivity.java`). Five HTTP routes under `/api/mesh/{id}/messages/{send,fetch,mark,edit,delete}`, all RSA-SHA256 (PKCS1v15) signed and verified against the per-mesh `lion_pubkey` (or the per-node `bunny_pubkey` for bunny-signed sends/fetches/marks). Edit + delete are Lion-only — the server returns 403 with a `"lion-only"` error before signature verification, so a tampered Bunny client can't even attempt to forge from=lion. New `MessageStore.edit()` appends the prior live fields (text, ciphertext, encrypted_key, iv) to `edit_history[]` and overwrites with the new values; `MessageStore.delete_message()` sets a tombstone (`deleted: true`, `deleted_at`, `deleted_by`) but preserves the original text + ciphertext server-side so Lion's own audit view can render it (Bunny's UI shows "[deleted]"). E2EE-aware: edit replaces the ciphertext/iv/encrypted_key bundle while history captures the prior bundle. `_messages_publish_ntfy(mesh_id)` fires after every send/edit/delete via the per-mesh ntfy topic so subscribers refresh the inbox in ~1s instead of waiting 5–10s for the next poll. Android UI: controller `lion_message_thread` ScrollView + companion `messages_container`, both with reply chains, edited/deleted markers, and pinned/mandatory-reply flags. Tests: `tests/test_messages.py` (8 unit tests of `MessageStore` edit/delete/tombstone semantics) plus the new `tests/test_e2e_messages_admin.py` (15 HTTP-level tests covering edit/delete/fetch/mark + ntfy fan-out + cross-mesh signer rejection).
- **Generic mesh installer — `installers/install-mesh.{sh,ps1}` + `installers/README.md`**. One-shot pre-configured desktop-collar installer for any mesh — writes `~/.config/focuslock/config.json` (Linux) or `%APPDATA%\focuslock\config.json` (Windows) with `mesh_id` + `mesh_url` + `vault_mode: true` + ntfy enabled, then hands off to the platform installer (`install-desktop-collar.sh` / `FocusLock.exe`). `--mesh-id` and `--mesh-url` (or `FOCUSLOCK_MESH_ID` / `FOCUSLOCK_MESH_URL` env vars) are required — there is no default mesh, no operator-specific hostname baked in. `--no-ntfy` skips ntfy subscription, `--reset-keys` wipes the vault keypair to force a fresh `register-node-request` cycle (defaults to preserving so prior Lion approvals stick). Idempotent — re-running rewrites `config.json` authoritatively + re-runs the platform installer. `installers/README.md` documents every parameter with sample `<your-mesh-id>` / `https://your.relay.example` invocations across Bash + PowerShell.

- **Per-mesh ntfy push topic** (`focuslock-mail.py:415`, audit followup #7). Pre-fix the server published every vault-blob wake-up to one config-wide `ntfy_topic`, so consumer meshes on the multi-tenant relay never got push notifications — silently falling back to the 30s vault poll (measured lock-propagation ~12s in hands-on QA). `_get_ntfy_topic(mesh_id)` now derives `focuslock-{mesh_id}` for non-operator meshes; operator keeps its config-wide topic for continuity. `ntfy_fn(version, mesh_id="")` threads the id through `_server_apply_order` + `/admin/order`. Consumer-mesh Collars subscribe via `focus_lock_ntfy_topic` in Settings.Global. Deployed + verified live on pegasus — Pixel subscribed to `https://ntfy.sh/focuslock-DNfs4xCZM-HY` and now receives sub-second wake-ups.
- **`installers/install-desktop-collar.sh` hardening**. Three hang points fixed: (1) new `--non-interactive` / `-n` flag (auto-detected via `[ ! -t 0 ]`) fails fast instead of blocking on the "Homelab URL" `read` prompt when run from a watcher/CI; (2) `sudo -v` up-front consolidates password prompts so later `sudo cp` can't re-prompt mid-install; (3) `curl -m 15` hard timeout on the Lexend font download so a slow GitHub doesn't hang install forever. Runs cleanly as `FOCUSLOCK_HOMELAB=… bash installers/install-desktop-collar.sh --non-interactive`.
- `docs/QA-v1.2.0-mesh.md` — manual QA script for the 1-lion + 3-slave mesh topology. Complements `docs/MANUAL-QA.md` (single-device fundamentals). Exercises pairing (incl. C5 fingerprint pin regression), order propagation, C1 signature gate + replay, messaging (incl. C4 mandatory-reply regression), per-device + target=all release, and the P2 paywall hardening regression sanity tour. Protocol-level 7-test vault driver + 9-test C1 gate driver both green against a throwaway local mesh as of 2026-04-21.
- **Headless-start caveat** for Android 14+ FGS-location enforcement, now documented in both `docs/MANUAL-QA.md §1` and `docs/QA-v1.2.0-mesh.md §Pre-flight/On each device`. `adb shell am start-foreground-service com.focuslock/.ControlService` on a fresh Android-14+ install crashes with `SecurityException: Starting FGS with type location requires FOREGROUND_SERVICE_LOCATION` — the manifest does declare that permission (`android/slave/AndroidManifest.xml:24`), but Android 14 also requires at least one runtime `ACCESS_*_LOCATION` grant before a location-typed FGS can start. Mitigation: drive through `ConsentActivity` or pre-grant via `adb shell pm grant`. Verified empirically in Waydroid Android 13 that the crash is not reproducible there (Android-14-only rule, as expected).
- **`walk_imap_folders()` helper** in `shared/focuslock_payment.py` (plus public `DEFAULT_SKIP_FOLDERS` constant). Extracts the IMAP folder-walk + `select`/`search`/`fetch` loop out of `check_payment_emails()` so it's independently testable. Seven new tests in `tests/test_payment.py::TestWalkImapFolders` use `unittest.mock.create_autospec(imaplib.IMAP4_SSL, instance=True)` — a signature-validating spec mock — so that any future rename/reshape of the `imaplib` surface (`list` / `select` / `search` / `fetch`) fails loudly rather than silently drifting from prod. Covers the default skip list, custom skip overrides, select-NO → folder skip, per-message fetch errors swallowed, per-folder errors swallowed, and empty search results. Tests `353 → 360`.
- **README badges + Download section** (`docs/README.md`): CI, CodeQL, OpenSSF Scorecard, latest-release version, and GPL-3.0 license badges at the top; a "Download" section under the tagline links to the GitHub Releases page and documents the `releases/latest/download/<filename>` URL pattern, SBOM / SHA256SUMS / APK-CERTS companion artifacts, and Sigstore attestation verification.
- **Desktop collar local uninstallers** — `installers/uninstall-desktop-collar.ps1` (Windows, commit `fbb7882`) + `installers/uninstall-desktop-collar.sh` (Linux, commit `1854b76`). Local teardown without needing a signed Release order from the mesh — the recovery path for the "lost Lion phone / private key" case. Linux script stops + disables the systemd user units (`focuslock-desktop`, `focuslock-tray`, `claude-standing-orders-sync .service/.timer/.path`) before killing stragglers so `Restart=always` cannot respawn them, restores the saved wallpaper (GNOME `gsettings` + KDE `plasma-apply-wallpaperimage`, best-effort), removes autostart entries + per-user config (`~/.config/focuslock`, `~/.local/share/focuslock`, `~/collar-files`), and sudo-removes `/opt/focuslock` + `/run/focuslock` + `/etc/tmpfiles.d/focuslock.conf` + `/etc/sudoers.d/focuslock`. The collar-authored `~/.claude/sync-standing-orders.sh` goes with it; user-authored `~/.claude/CLAUDE.md` / `settings.json` / `hooks/` are intentionally left in place since they may carry content layered on top of collar standing orders. Refuses to run as root, idempotent, prompts for confirmation with the literal string `release` before touching anything.
- **`docs/QA-pairing.md`** — standing manual-QA checklist for pairing. Unlike `docs/QA-v1.2.0-mesh.md` (release-snapshot) and `docs/MANUAL-QA.md` (single-device fundamentals), this doc is ongoing and covers all three pairing routes (direct/LAN, QR, server-mediated) plus the new recovery flows: idempotent re-pair, clearable conflict via Bunny Tasker's Reset button, claim-unknown / claim-expired passphrase with hint rendering, `/api/pair/vault-status` driven with `curl` showing pending → approved counts. Exit criteria: every ☐ ticked on at least one physical slave + one Waydroid slave, linked from the release PR.
- **Pairing recovery diagnostic** — new admin-only `GET /api/pair/vault-status/<mesh_id>` endpoint in `focuslock-mail.py:4215`. Returns `{pending:[...], approved:[...], rejected:[...]}` with every entry masked through a `_strip_pubkey()` helper (first 16 chars of sha256 for pending/approved, first 24 for rejection keys) so the Lion can see who's waiting for approval without the endpoint ever revealing raw pubkeys. Accepts admin auth via `?token=` or `Authorization: Bearer`. `TestVaultStatusEndpoint` in `tests/test_pairing.py` pins: no-token → 403, wrong token → 403, missing mesh_id → 400, valid token + unknown mesh → 200 + empty lists, valid token + seeded mesh → 200 + correct counts + 16-hex hash shape + no raw pubkey leak, Bearer-header path works.
- **Bunny Tasker "Reset pair state" button** + Lion's Share clearable-conflict dialog. New bunny-signed `POST /api/pair-reset` endpoint on the Collar (`android/slave/src/com/focuslock/ControlService.java:1705`) clears the stored `lion_pubkey` so a half-completed pair can be recovered without reinstalling either app. Visible only when paired (`android/companion/res/layout/activity_main.xml:315 btn_pair_reset`, wired at `MainActivity.java:133`); confirmation dialog shows the current lion-key fingerprint before resetting. On the Lion side, `pairDirect` at `android/controller/.../MainActivity.java:2228` now detects the Collar's `{error, clearable:true, hint}` response (the "already paired with a different lion key" branch of `doPair`) and pops an `AlertDialog` with the server-supplied hint text instead of dumping raw JSON at the user; the new `showPairConflictDialog(bunnyUrl, hint)` helper at line 2337 frames the server's text rather than inventing its own.
- **README hosted-relay framing honest about current operational status** (`docs/README.md §Two Trust Tiers`, `§Setup Options`, `§Hosted Relay Signup`). Drops the named "Bunny Dev hosted" entity, updates the Setup Options table row to show *"Depends on operator — none currently run, see below"* instead of *"Free (community server)"*, and replaces the signup section with a plain-text note stating **no community relay is currently operated**, self-host is the only active path, plus a resource/complexity summary for anyone thinking of running one (~256 MB RAM, $5/mo VPS, domain + TLS reverse proxy, 30–60 min from DNS to first pair) with direct links to `docs/SELF-HOSTING.md` and `docs/CONFIG.md`. The aspirational `/signup` flow detail is preserved for whoever eventually stands one up. Closes option C of the `docs/PUBLISHABLE-ROADMAP.md §Strategic decisions` hosted-relay question (partial close — the underlying "should someone stand one up?" decision remains open).
- **Enforcement-sensitive review gate + signed-commit requirement**. New `.github/CODEOWNERS` auto-requests maintainer review on every path that handles crypto (`shared/focuslock_vault.py`, `shared/focuslock_mesh.py`, `focuslock_mesh.py`, `focuslock_ntfy.py`), payment parsing (`shared/focuslock_payment.py`, `shared/banks.json`), paywall math (`shared/focuslock_penalties.py`), the server (`focuslock-mail.py`), all three desktop-collar scripts + `build-win.py`, all three Android modules, `installers/`, and the CI/CODEOWNERS surface. `CONTRIBUTING.md §Review gate for enforcement-sensitive code` spells out the rule in prose; `CONTRIBUTING.md §Commit signing` requires GPG-or-SSH signed commits for any change to those paths (docs + tests + CHANGELOG remain unsigned-friendly). Mechanical enforcement via the new `.github/workflows/signed-commits.yml` workflow — fails any PR against `main` whose commit range includes an unsigned commit that touches a sensitive path, with `::error` annotations naming the exact offending paths so the fix is obvious (`git rebase --exec 'git commit --amend --no-edit -S' origin/main..HEAD`). Closes `docs/PUBLISHABLE-ROADMAP.md §Strategic decisions #1`.
- **CVE publication + embargo policy** (`SECURITY.md §Coordinated disclosure + CVE publication`). Companion to the existing §Response timeline (5-day ack / 14-day assessment / 90d High-Crit patch target / 90d coordinated disclosure). New text clarifies that the 90-day window IS the embargo, names the five conditions that end it (patch release, day 90, active exploitation in the wild, reporter early-request, reporter extension-request for downstream coord), specifies the publication workflow (GitHub Security Advisory + CVE via GHSA's CNA for High/Critical; GHSA-only for Medium unless reporter asks or downstream impact warrants), lists the GHSA contents (affected component + version range, CVSS v3.1, fix version, mitigation, reporter credit), and adds an explicit "talk through disagreement rather than go public in frustration" line. Closes `docs/PUBLISHABLE-ROADMAP.md §Strategic decisions #3`.
- **Stable-filename release aliases** in `.github/workflows/release.yml`. Every release now uploads `focuslock-latest.apk` / `bunnytasker-latest.apk` / `focusctl-latest.apk` / `FocusLock-latest.exe` / `FocusLock-Watchdog-latest.exe` alongside the versioned artifacts, so the README can link at `https://github.com/.../releases/latest/download/focuslock-latest.apk` without discovering the current tag first. Byte-identical copies — Sigstore attestation + SHA256SUMS already cover them by content hash, and the workflow's existing `subject-path:` / `files:` globs sweep them up for free. Closes the pending "Stable-filename release aliases" follow-up in `docs/PUBLISHABLE-ROADMAP.md §Medium-term`.
- **`tests/ui/` — UI pair spike (shelved 2026-04-23)**. Scaffold for a `uiautomator2`-driven direct-pair test on Waydroid (single-device loopback, "LAN" = 127.0.0.1). `uiautomator2.connect()` hangs at `_setup_jar` → `toybox md5sum` against the Waydroid adbd and wedges the Android-side daemon for the rest of the session (recovery requires `waydroid session stop` + `sudo systemctl restart waydroid-container`). Harness chain (Waydroid → adbd → uiautomator2's atx-agent/JAR layer) is three brittle links, each of which failed during the spike; running this per-commit would cost more operator time than it saves. Kept as a skipped-by-default scaffold (opt-in via `UI_TESTS=1 pytest tests/ui/` + a connected adb device) with the failure mode documented in the module docstring and `docs/PUBLISHABLE-ROADMAP.md §Medium-term`. APK install + consent-bypass (`settings put global focus_lock_consented 1`) + foreground-service start logic is correct as of v1.2.0 — the gap is the UI driver, not the Android setup. Registers a new `ui` pytest marker (`pyproject.toml [tool.pytest.ini_options]`).

### Changed
- **CI quality gates: pytest-timeout (60s default) + coverage `fail_under = 95` + four new ruff `S` rules** (`pyproject.toml`, `.github/workflows/ci.yml`). Three quality-tooling gaps closed in one config bundle:
  - **`pytest-timeout` 60s default** — `[tool.pytest.ini_options] timeout = 60` aborts any test that hangs (network mocks, threading races, unmocked I/O) instead of letting GitHub-Actions kill the job at the 6h global ceiling. `pytest-timeout>=2.3` added to `[project.optional-dependencies] dev` and to the CI `Install test dependencies` step. Verified: a `time.sleep(10)` test under `timeout=2` is killed at 2.01s with `Failed: Timeout (>2.0s) from pytest-timeout`.
  - **Coverage `fail_under = 95`** — `[tool.coverage.report] fail_under = 95` locks in the `shared/` 96% win. Any future PR that drops `shared/` coverage below 95% fails CI. Raise the floor as gaps close, never lower it.
  - **Ruff `S` rules selectively enabled — `S307` (eval), `S324` (insecure md5/sha1), `S501` (TLS verify=False), `S506` (yaml.load without SafeLoader)**. Picked individually rather than enabling the full `S` set so the known silent-except surface doesn't flood. Currently zero trips after a per-file ignore for `focuslock-mail.py:5285` (md5 used as content-hash for memory-bundle cache key, not security). Future contributors writing `eval(...)`, `verify=False`, `yaml.load()`, or `hashlib.md5()`/`sha1()` for security purposes get blocked at lint.
- **`shared/` coverage push: `focuslock_sync.py` 0% → 100%, `focuslock_llm.py` 15% → 100%, `focuslock_evidence.py` 24% → 100%** (`tests/test_sync.py`, `tests/test_llm.py`, `tests/test_evidence.py`). Closes the three remaining `shared/` coverage gaps. Brings `shared/` total from 78% → **96%** (every module now ≥92%) — comprehensively above Phase 2's ≥75% exit criterion. Tests `574 → 672` (+98).
  - `focuslock_sync.py` (37 tests, 5 classes) — `try_sync()` account-vs-legacy endpoint dispatch by `mesh_id`, payload shape, peer-info update from response, `known_nodes` learning, version-gated apply, callback fan-out, signature-mismatch rejection (returns True since endpoint did respond), HTTP / JSON-decode / timeout error paths return False; `direct_sync_poll()` priority order (mesh_url → homelab → phone_addresses → tailscale), first-success short-circuit, tailscale skipped when `get_tailscale_ip_fn` is None, tailscale loop continues when one peer's `try_sync` fails, `local_status_fn` / `lion_pubkey_fn` evaluated each call; `relay_to_phones()` phone-only filter, self exclusion, multi-address break-on-first-success + continue-on-failure, exception swallowing, PIN fallback from `mesh_orders.get("pin")` and explicit PIN override.
  - `focuslock_llm.py` (30 tests, 8 classes) — `verify_photo_with_llm()` empty-photo early exit (no HTTP call), JSON-response happy path (passed=True/False), JSON extracted from prose-padded response, missing `passed` / `reason` field defaults, fallback keyword detection ("pass"/"true"/"completed"/"yes" → passed=True; nothing → passed=False), 200-char `reason` truncation, on_evidence callback fired only on JSON path with correct etype tag (`photo task passed` / `photo task failed`), HTTP / decode error → `{ok: False}`, custom `ollama_url` + `vision_model` overrides reach the request URL and payload; `generate_task_with_llm()` all five categories (chore / exercise / creative / general / service) route to distinct prompts, unknown category falls back to general, JSON parsed from prose, missing `task` / `hint` defaults, no-JSON returns 200-char-truncated raw response as task, HTTP / decode error → safe fallback `"Clean the kitchen"`, custom URL + model overrides.
  - `focuslock_evidence.py` (24 tests, 6 classes) — `get_notif_pref()` mesh_orders explicit-enable / explicit-disable / truthy-string / unset+no-adb default-true / adb-fallback enable+disable / adb-exception default-true / mesh_orders takes precedence over adb (adb not consulted when key set); `_TYPE_TO_PREF` map pinned (compliment / gratitude / love letter / photo task passed/failed / bunny message / exercise → email_evidence; self-lock / entrap / escape attempt → email_escape; geofence breach → email_breach); `send_evidence()` early-exit guards (no partner_email, notif disabled, missing smtp_host / mail_user / mail_pass — none reach SMTP), happy-path SMTP context-manager flow with starttls + login + send_message, From/To/Subject headers correct, body contains content + type label (decoded after MIMEText auto-base64), unknown evidence_type defaults to email_evidence pref, escape-attempt routed to email_escape (not email_evidence), geofence-breach routed to email_breach, SMTP exception → False, login exception → False.
- **Consumer install — `banks.json` bundled into companion APK** (`android/companion/assets/banks.json`, 303 lines new; `android/companion/build.sh` `-A assets` flag; companion `MainActivity.java`). Bunny Tasker previously fetched the bank-detection config from the relay at first run, which blocked offline consumer installs and added a server round-trip to the picker's first paint. The bank list is now compiled into the APK as `assets/banks.json` (40+ packages across 21 regions, matching the server-side `shared/banks.json`); reads come from the bundled asset with the existing SharedPreferences override path (Lion-side custom packages) layered on top. Faster cold-start, works without network, and the consumer install no longer needs the relay reachable to surface the picker. Build pipeline change: `android/companion/build.sh` now passes `-A assets/` to `aapt2 link` (already added in `6d47d1e` for `qrcode.min.js`); the bundled JSON is verified present by `aapt dump badging` during release.
- **Collar runtime — one-shot GPS geofence + immediate gossip + prior-launcher recapture** (`android/slave/src/com/focuslock/ControlService.java`). Three runtime improvements:
  - **`doConfineHome(body)`** — single-step geofence: capture the Collar's current GPS fix and use it as the geofence center with a configurable radius (default 100 m). Replaces the old two-step `get-location` → `set-geofence` dance, which broke in vault mode because vault append is fire-and-forget — Lion's Share never saw the location response.
  - **`kickRuntimePush()`** — fires an immediate vault runtime gossip push instead of waiting for the next 10s tick. Called from paywall mutations, geofence set/clear, and the new messaging paths so subscribers see updates within 1–2 s rather than a poll cycle.
  - **`capturePriorHomeBeforeLock()`** — re-snapshots the user's *current* default launcher each time the Collar locks, replacing the once-at-consent capture introduced by PR #16's `MATCH_DEFAULT_ONLY` fix. If a user installs Fossify (or Nova, Lawnchair, …) after the consent flow and makes it the default mid-session, Release Forever now restores the launcher that was active at lock time, not the one captured during initial consent. Defends the same Release-Forever invariant against a longer time-window than the consent-time snapshot covers.

- **CI: migrated all GitHub Actions to Node.js 24 runtimes ahead of the 2026-09-16 runner cliff.** Full log audit (`gh api … /logs | grep "Node.js 20"`) across `ci.yml` + `release.yml` + `codeql.yml` + `scorecard.yml` surfaced nine Node-20 actions. Coordinated bumps: `actions/checkout` v4→v6.0.2, `actions/setup-python` v5→v6.2.0, `actions/setup-java` v4→v5.2.0, `android-actions/setup-android` v3→v4.0.1, `actions/upload-artifact` v4→v7.0.1, `actions/download-artifact` v4→v8.0.1, `codecov/codecov-action` v5→v6.0.0 (also eliminates the transitive `actions/github-script` v7 warning surfaced inside codecov), `actions/attest-build-provenance` v2→v4.1.0, `softprops/action-gh-release` v2→v3.0.0, `github/codeql-action` (init/analyze/upload-sarif) v3→v4.35.2. `ossf/scorecard-action` stays at v2.4.3 (already Node 24). All pins remain SHA-locked with tag comments. `.github/dependabot.yml` now groups all github-actions bumps into one PR/week to prevent queue-exhaustion (previous 5-PR limit was blocking proposals); `open-pull-requests-limit` raised to 10 as belt-and-braces. Supersedes Dependabot PRs #1/#2/#3/#4/#7.
- **Windows desktop build: dropped the `FocusLock-Paired.exe` variant.** The two exes (`FocusLock.exe` generic / `FocusLock-Paired.exe` pre-configured) were built from identical source and differed only by a `_build_config.py` bake that no runtime code ever imported — they were functionally identical shipping as ~600B-apart twins. `build-win.py` now produces a single canonical `FocusLock.exe` + `FocusLock-Watchdog.exe`. Removed the unused `--paired-only` / `--generic-only` / `--homelab` / `--pin` / `--pubkey` CLI flags along with `write_build_config()`, `DEFAULT_HOMELAB`, `DEFAULT_PIN`, the `_build_config.py` staging, and the `--hidden-import=_build_config` PyInstaller flag. `focuslock-desktop-win.py` was never touched (it never imported `_build_config` either). Config still flows through `~/.config/focuslock/config.json` / `%APPDATA%\focuslock\config.json` — no runtime change for existing installs. The collar's kill list in `focuslock-desktop-win.py:1672` keeps both legacy `FocusLock-Paired.exe` and `FocusLock.exe` so the new build still tears down older side-by-side installs on first launch. `docs/BUILD.md`, `CLAUDE.md`, and the `2026-04-03-mesh-reliability-overhaul.md` update path now point at `FocusLock.exe` only.
- **`PairingRegistry.TTL_SECONDS` shortened 3600 → 600** in `focuslock-mail.py:1638`. Unclaimed pair passphrases now expire after 10 min instead of an hour, narrowing the window in which a leaked passphrase is still claimable. `/api/pair/claim` grew a `claim_or_reason()` companion that distinguishes three outcomes — `ok` (202), `not_registered` (404), `expired` (410) — and attaches a user-facing `hint` to the error responses ("this passphrase has expired; ask Bunny to generate a fresh one in Bunny Tasker's Join Mesh screen") rather than a bare `{error:"…"}`. `register-node-request` BAD_REQUEST / DENIED / PENDING log lines now all carry `pubkey_hash=<16 hex>` (first 16 chars of sha256(pubkey_b64)) so pair-failure diagnosis is possible without reading raw request bodies. Eight new test classes in `tests/test_pairing.py` — TTL math, `claim_or_reason` matrix, HTTP claim shape (200/400/404/410 + hint), vault-status HTTP endpoint (auth + Bearer + hash masking), register-node-request log shapes, `VaultStore` accessors, `PairingRegistry(persist_path=…)` reload survival. Tests `360 → 386`.
- **Collar `doPair` idempotent on same `lion_pubkey`**, clearable on different (`android/slave/src/com/focuslock/ControlService.java:1680`). A second pair POST with the key already stored now returns `{ok:true, action:"already-paired", bunny_pubkey:…}` and leaves state alone, so a retry-on-flaky-network isn't mistaken for a fresh pair. A pair POST with a *different* lion key while already paired now returns `{error:"…", clearable:true, hint:"Ask Bunny to tap 'Reset pair state' in Bunny Tasker"}` — the combination of the new bunny-signed `POST /api/pair-reset` endpoint at line 1705 and Bunny Tasker's Reset button is the recovery path, so the Collar no longer treats "another Lion is trying to pair" as indistinguishable from "reconfigure me silently". Lion's Share renders both responses with distinct status prefixes — `"RE-CONFIRMED direct (fingerprint verified): …"` for the idempotent branch, the new `showPairConflictDialog` for the clearable branch (`android/controller/src/com/focusctl/MainActivity.java:2272`, `:2337`).
- **Collar register-node-request exponential backoff** replaces the prior flat 1 h throttle (`android/slave/src/com/focuslock/ControlService.java:3478 VAULT_REGISTER_BACKOFF_MS`). New schedule: attempt 1 fires immediately, attempt 2 gated until T+1 min, then 5 min / 15 min / 60 min. `vaultClearRegisterBackoff` (line 3560) zeroes the schedule as soon as a vault blob carrying our node slot lands, so an approved phone isn't still waiting out a backoff window. Removed the `nodeId = "pixel"` fallback at line 3515 that used to collapse un-joined phones onto one server identity — each phone now sticks to the node_id stored on first launch, preventing two fresh-install phones from fighting over the `"pixel"` slot on the same mesh.

### Fixed
- **Web Remote QR `/admin/web-session` approve still rejected consumer-mesh Lion signatures after `6d47d1e`** (`focuslock-mail.py:4747-4767`, audit MEDIUM #6 follow-up). The 2026-04-24 audit fix added per-mesh iteration over `_mesh_accounts` and called `mesh.verify_signature(payload, signature, lion_pub)` for each mesh — but `mesh.verify_signature` uses `serialization.load_pem_public_key` (PEM-only), and Lion's Share writes `lion_pubkey` as bare base64-encoded DER (`MainActivity.java:2535` does `base64(kp.getPublic().getEncoded())`; `VaultCrypto.signBlob` calls `stripPemHeaders` to confirm DER b64 is the canonical wire format). Result: every mesh — operator and consumer — failed signature verify because `load_pem_public_key("MIIBIjANBg...")` raised, the `except Exception: pass` swallowed it, the loop never matched, and the user saw the same *"Signature rejected — wrong key?"* the audit thought it had closed. Other admin handlers (auto-accept at line 3303, state-mirror, register-node-request) handle this correctly by calling `serialization.load_der_public_key(b64decode(lion_pubkey))` directly; the mail module's own `_verify_signed_payload(payload, sig, pubkey_str)` helper goes through `_load_lion_pubkey_obj` which tries PEM first, falls back to DER b64. Fix: replace the two `mesh.verify_signature` calls with `_verify_signed_payload(..., quiet=True)` so DER-b64 keys load via `load_der_public_key`. 17 new HTTP-level tests in `tests/test_e2e_web_session.py` pin: operator-Lion match binds session to operator mesh; consumer-Lion match binds to consumer mesh (the literal regression test); rogue keypair rejected with 403 *"no Lion key matched"*; mesh with empty `lion_pubkey` skipped without crash; unknown / expired / already-approved session-id paths; create returns `qr_url` carrying `?s={session_id}`; poll returns `{approved, session_token, expires_in, mesh_id}` and the session is one-time-use; scoped session_token authorizes its bound mesh via `_is_valid_admin_auth(token, mesh_id=...)` and is rejected for any other mesh; master `ADMIN_TOKEN` bypasses the mesh scope check. Tests `557 → 574`.
- **Bunny Tasker subscription buttons displayed wrong amounts** (`android/companion/res/layout/activity_main.xml:597-598`). Bronze button labeled `$10/wk` and Silver button labeled `$25/wk`, but the click handler at `MainActivity.java:180-182` posts `doSubscribe("bronze", 25)` / `doSubscribe("silver", 35)` and the server tier table at `focuslock-mail.py:792` is `{"bronze": 25, "silver": 35, "gold": 50}` — Bunnies who tapped Bronze expecting $10 were charged $25 (and Silver tappers were charged $35 instead of $25). Labels corrected to match the authoritative server amounts. Gold ($50/wk) was already correct. The Lion controller's tier picker (`android/controller/.../MainActivity.java:3235`) already showed the correct trio.

### Security
- **`SyncthingVaultTransport.nodes()` symlink check fixed + test coverage 0% → 94%** (`shared/focuslock_transport.py`, `tests/test_transport.py`). Pre-fix `nodes()` did `path = _safe_path(...)` then `if os.path.islink(path)` — but `_safe_path` returns `os.path.realpath(joined)`, which has already resolved any symlink in the path, so `os.path.islink(realpath)` was permanently False. A malicious Syncthing peer could plant `mesh_id/nodes.json` as a symlink to another file inside `vault_dir` (e.g. another mesh's `nodes.json`) and exfiltrate it through the `nodes()` return value — `_safe_path` doesn't reject symlinks pointing to in-bound targets, only out-of-bound ones. Fix: islink check now runs on the raw `os.path.join(vault_dir, mesh_id, "nodes.json")` *before* `_safe_path` resolves it; mesh_id also gets validated through `_safe_id` for symmetry with `_blobs_dir` / `_mesh_dir`. The `since()` path was correct already (it used `os.path.join(blobs_dir, fname)` without an extra realpath). 68 new tests in `tests/test_transport.py` pin: `_safe_id` validation (path-sep / dot / null-byte / whitespace / special-char rejection), `_safe_path` traversal (`..` / absolute-part / parent-symlink-escape), `HttpVaultTransport` 64 MB response cap, 404→empty / 500→raise / 409→current_version-extraction shapes, `SyncthingVaultTransport.since()` poison-filename defenses (negative / zero / `MAX_VERSION+1` / non-numeric / non-`.json` / symlink / malformed-JSON all skipped), atomic-write via `.tmp`+`os.replace`, version collision retry, `register_node` invalid-id rejection, and `transport_factory` dispatch (default / explicit-http / syncthing-with-dir / syncthing-without-dir-falls-back-to-http with warning / unknown-string-falls-through-to-http). Tests `489 → 557`. Closes the `shared/focuslock_transport.py` 0%-coverage gap that was blocking Phase 2's "≥75% on shared/" exit criterion.
- **IMAP payment scanner per-mesh** (`shared/focuslock_payment.py`, `focuslock-mail.py`, audit MEDIUM #5). Pre-fix the relay ran exactly one IMAP scanner thread, hardcoded against the operator's mailbox + `OPERATOR_MESH_ID`; non-operator Lions could `set-payment-email` on their mesh and the per-mesh `payment_imap_*` fields would land in orders, but no thread ever read them — every consumer-mesh paywall stayed up forever even after a real bank email landed. Refactor: `_scan_mesh_imap_once(...)` extracted out of the legacy single-mesh loop (one mesh, one cycle, no `while True`); `check_payment_emails()` kept for backward compat (now delegates internally so all 11 existing single-mesh tests stay green); new `check_payment_emails_multi(*, mesh_contexts_fn, ...)` walks every known mesh per cycle and scans each that has resolvable creds. Per-mesh apply_fn closures route `payment-received` through `_server_apply_order(mesh_id, ...)` so `total_paid_cents` + `paywall` land in the originating mesh's orders doc AND propagate via vault blob — required for vault-only consumer meshes. `_iter_imap_scan_contexts()` in `focuslock-mail.py` re-evaluates each cycle so newly-created meshes pick up automatically; only the operator inherits `(IMAP_HOST, MAIL_USER, MAIL_PASS)` as static fallback (consumer meshes are scanned only once Lion has configured `set-payment-email`). Per-mesh ledger isolation (`_get_payment_ledger(mesh_id)`, already in place from PR #17) means the same Message-ID arriving on two meshes credits both — pre-fix the shared ledger would have flagged the second as duplicate and silently dropped a real payment. 7 new tests in `tests/test_payment.py::TestCheckPaymentEmailsMultiMesh` (per-host-creds routing, no-creds skip, static-fallback contract, per-mesh apply_fn closure correctness, ledger isolation, contexts re-evaluated each cycle, per-mesh exception isolation). Tests `482 → 489`. Closes the last open multi-tenant correctness item from the 2026-04-24 hands-on QA audit.
- **Multi-tenant correctness — auto-accept toggle + state-mirror + relay-key backfill** (`focuslock-mail.py:3210-3272, 3754-3900, 513-558, 2548-2570`). Three operator-singleton gaps surfaced by the 2026-04-24 hands-on consumer-mesh QA, all closed in this bundle:
  - **`/api/mesh/{id}/auto-accept`** — Lion-signed flag toggle (`{mesh_id}|auto-accept|{state}|{ts}`, ±5min replay window). When ON, a subsequent `/vault/{id}/register-node-request` lands directly in the mesh's approved-nodes list instead of the pending queue. Key rotation (existing `node_id`, new pubkey) still routes to pending — preserves the takeover protection at `docs/VAULT-DESIGN.md:266` regardless of the auto-accept setting. Closes the consumer-mesh onboarding friction (every device approval was an extra Lion-side tap) without weakening the security gate.
  - **`/api/mesh/{id}/state-mirror`** — Bunny-signed (or vault-node-signed) plaintext state mirror. Payload: `{mesh_id}|{node_id}|state-mirror|{ts}|{state_sha256_hex}`. Whitelisted to 8 fields (`paywall`, `paywall_original`, `sub_tier`, `sub_due`, `lock_active`, `locked_at`, `unlock_at`, `free_unlocks`). Unlocks server-side scanners (compound-interest accrual, IMAP payment crediting, `/admin/status` dashboards) on consumer meshes, where vault-mode otherwise leaves `_orders_registry[mesh_id]` at zero because the server can't read encrypted blobs. Trust model unchanged: the Collar already enforces the lock locally, so trusting it to assert "paywall is $X" is no weaker than trusting it to enforce "lock is on".
  - **Relay-as-approved-vault-node backfill** — `_ensure_relay_node_registered(mesh_id)` (idempotent) auto-registers the relay's `RELAY_PUBKEY_DER_B64` as an approved vault node on every mesh. `_relay_backfill_consumer_meshes()` runs once at startup to repair pre-fix consumer meshes; `MeshAccountStore.create()` calls the helper for new ones. Without this, every server-driven mutation on a consumer mesh (subscribe, compound interest, payment-received, set-geofence, set-curfew, escape penalties …) silently dropped at the Collar's signature check because the relay was in none of the trust sets (lion / self / approved-nodes). Trust note: registering the relay as an approved signer doesn't let it decrypt order *contents* — Lion's `apiVault` path is unchanged and still zero-knowledge for Lion-issued orders. Bootstrap-only — done at mesh-create + startup backfill, never re-confirmed at mutation time so a tampered `_vault_store` cannot escalate into a forged-signer bypass. Tests: 13 HTTP-level tests in `tests/test_e2e_admin_routes.py` (toggle flips, register-node-request lands approved when on, key-rotation conflict still pends, state-mirror writes whitelisted fields, cross-mesh signer rejected, backfill idempotent + skips operator mesh).
- **Log injection (CodeQL) — every new server log on user-controlled input wrapped in `_sanitize_log()`** (`focuslock-mail.py:117`). Each new `logger.info` / `logger.warning` site introduced by the messaging routes (`Message edited`, `Message deleted`, `state-mirror: …`), the auto-accept handler (`Auto-accept …`, `auto-accept sig verify failed: …`), the state-mirror sig-verify branches (`state-mirror sig decode failed: …`, `state-mirror sig verify failed: …`), the ntfy fan-out helper (`messages ntfy publish failed: …`), and the relay-backfill helpers (`relay vault key rotated for mesh=…`, `relay registered as approved vault node for mesh=…`, `relay backfill failed for mesh=…`, `relay node auto-register failed for …`) wraps every user-controlled string variable (`mesh_id`, `node_id`, `node_type`, `edit_message_id`, `del_message_id`, `account["mesh_id"]`) through the existing `_sanitize_log()` helper introduced in PR #16. Closes the regression vector that PR #16 closed for the pair-related logs — same threat (CR/LF/NUL forging fake log entries), same fix.
- **Multi-tenant isolation fixes — server** (`focuslock-mail.py`, 5 tests `435 passed`). Hands-on QA of a fresh consumer mesh on `focus.example.com` surfaced a class of bug where server state + auth was scoped to the operator mesh only. A 2026-04-24 focused audit identified 6 findings (1 BLOCKER, 3 HIGH, 2 MEDIUM); this change closes the three smallest/most critical. Remaining ones (desktop heartbeat + penalty singleton, IMAP payment scanner) stay in `docs/PUBLISHABLE-ROADMAP.md` followups.
  - **`/admin/order` mesh-routing (audit BLOCKER #1)**. `mesh.handle_mesh_order(data, mesh_orders, mesh_peers, …)` at `:2846` always passed `mesh_orders` — the operator mesh's singleton — so every non-operator Lion's admin action (lock, unlock, paywall, tribute, streak, subscribe, deadline-task, …) silently landed in the operator's orders doc instead of the requested mesh's. My round-2 admin-gamble intercept was the only branch that already resolved `_resolve_orders(target_mesh)` correctly. Now the generic dispatch does too: `target_mesh = req_mesh_id or OPERATOR_MESH_ID`, `target_orders = _resolve_orders(target_mesh)`, and the ntfy bump reads `target_orders.version` instead of the operator's. `lion_pubkey` stays as `get_lion_pubkey()` (operator ADB fallback) for backward-compat — the admin-token gate already authenticated the caller, adding a second Lion-signature layer would regress admin-only flows. 1 new test in `TestAdminOrderMeshRouting` seeds two meshes, POSTs an `add-paywall` to mesh B, asserts mesh B's paywall becomes 42 and mesh A's stays 0.
  - **Web-session approve mesh context (audit MEDIUM #6 — *"wrong key"* bug)**. `/admin/web-session action=approve` previously only verified against the operator mesh's `lion_pubkey` (via `get_lion_pubkey()` → empty on pure-relay → `OPERATOR_MESH_ID` fallback), so every consumer-mesh Lion scanning the web-login QR hit *"invalid signature — must be signed by Lion's private key"*. Session create stored no `mesh_id`. Fix iterates every mesh account on approve — first `verify_signature` match wins, and that mesh's id binds to the session. `_issue_session_token(session_id, mesh_id)` accepts the bound mesh; `_is_valid_admin_auth(token, mesh_id=…)` enforces that session tokens can only authorize orders against their bound mesh (master `ADMIN_TOKEN` still crosses all meshes). `/admin/web-session/<id>` poll returns `{approved, session_token, expires_in, mesh_id}` so the web UI can display which mesh it's now logged into.
  - **Desktop heartbeat + dead-man's switch per-mesh (audit HIGH #2+#3)**. `DesktopRegistry` was a server-wide singleton; a collar going silent on mesh B previously fired the $50 penalty via `adb.put("focus_lock_paywall", ...)` which points at the operator's ADB-connected phone — wrong paywall, wrong mesh. New `_get_desktop_registry(mesh_id)` factory mirrors the payment-ledger + message-store pattern: operator mesh keeps the legacy singleton (`desktop_registry`) for persistence continuity, consumer meshes get per-file registries under `_DESKTOP_REGISTRIES_DIR/{mesh_id}.json`. `/webhook/desktop-heartbeat` accepts `mesh_id` in the body + routes to the matching registry (ADB-side `focus_lock_desktops` summary push now only fires for the operator mesh, since ADB points at one physical phone). `/webhook/desktop-penalty` accepts `mesh_id`, scopes admin_token check with `_is_valid_admin_auth(token, mesh_id=...)`, and routes the paywall-add through `_server_apply_order(mesh_id, "add-paywall", ...)` instead of raw ADB — per-mesh orders doc + vault-blob propagation. `check_desktop_heartbeats` thread iterates `_iter_desktop_registries()` and fires warnings/penalties on each mesh's own orders doc (operator gets the legacy ADB writes as fallback). `focuslock-desktop.py` heartbeat + penalty payloads now include `mesh_id` from config. 3 new tests in `TestPerMeshDesktopRegistry`: fresh-mesh-empty, heartbeat-isolation-between-meshes, penalty-webhook-routes-to-request-mesh. Tests `435 → 438`.
  - **`PairingRegistry` mesh-scoped (audit HIGH #4)**. Passphrase entries were keyed by the uppercased passphrase alone — two Lions on the multi-tenant relay generating the same short code (or a malicious crash between generate+claim) could cross-wire Bunnies to the wrong Lion. `PairingRegistry._key(mesh_id, passphrase)` now composes `"{mesh_id}:{PASSPHRASE}"`, every entry carries an explicit `mesh_id` field as a tamper-check, and `register` / `claim` / `get_pending_pairing` / `mark_delivered` / `status` all accept `mesh_id` (default `""` preserves legacy behavior for in-repo tests). `/api/pair/register` and `/api/pair/claim` HTTP handlers now **require** `mesh_id` (400 if missing). 4 new tests: same-passphrase-different-meshes doesn't collide, cross-mesh claim returns `not_registered`, both HTTP endpoints reject unspecified mesh_id.

- **Bunny-initiated pair-reset removed** (slave v73/8.30, companion v56/2.23, controller v71/71.0). CLAUDE.md is explicit: *"Release Forever button (Lion only)"* — only the Lion or a factory reset can remove the Collar. The bunny-signed `POST /api/pair-reset` endpoint added in PR #16 (2026-04-24 early — intended as a recovery path for half-completed pair) broke that contract: a Bunny could unilaterally clear the Collar's stored `lion_pubkey` and walk out of a pair. Removed end-to-end:
  - `android/slave/.../ControlService.java` — dispatch entry for `/api/pair-reset` commented-out (now returns 403 via fall-through); `doPairReset` method body excised.
  - `android/companion/.../MainActivity.java` — `btn_pair_reset` button force-hidden in `onCreate` (layout entry kept for compat with older `R.java` generations); `showPairResetConfirmDialog` + `doPairReset` methods deleted.
  - `android/controller/.../MainActivity.java` — `showPairConflictDialog` now tells the Lion "only Release Forever or factory reset can unpair" instead of "ask Bunny to tap Reset pair state".
  - Collar's `doPair` response when a different Lion key offers itself now returns `clearable:false` with the factory-reset framing; this is the server/Collar's contract so every caller sees consistent text.
  - Half-completed-pair recovery now requires reinstalling the Collar, which is gated on device admin being disabled, which is gated on a signed Release from the current Lion. No in-app bunny-escape path remains.

### Added
- **Per-mesh payment ledger** (`focuslock-mail.py`). `focus.example.com` is multi-tenant by design (one relay hosts many Lion meshes); the pre-2026-04-24 ledger was a singleton, so a Bunny on a fresh mesh would fetch `/api/mesh/{id}/payments` and see entries from every other mesh the relay had ever scanned. Rewired via `_get_payment_ledger(mesh_id)` factory: operator mesh (when `OPERATOR_MESH_ID` is configured) keeps reading/writing the legacy `payment_ledger.json` for historical continuity; every other mesh gets its own file under `_LEDGERS_DIR/{mesh_id}.json` — fresh ledger on first access, empty by construction. `/api/mesh/{id}/payments` endpoint now dispatches on the request's `mesh_id` rather than the server-wide singleton, and reads `total_paid_cents` from the request's orders doc rather than the operator's. Legacy `payment_ledger` module-level singleton kept as a back-compat alias for the IMAP scanner + any caller that doesn't yet know a `mesh_id`. 3 new tests in `tests/test_pairing.py::TestPerMeshPaymentLedger` pin fresh-mesh-empty, write-to-A-invisible-from-B, and operator-mesh-uses-legacy-path invariants. Tests `427 → 430`.
- **Mutual admin re-activation prompts + correct prior-launcher capture** (slave v72/8.29, companion v55/2.22). Three first-install recovery bugs surfaced when driving the setup flow against a fresh pair of devices:
  - **Collar + Bunny Tasker both now launch the peer's device-admin activation intent** when they detect the peer's admin has been removed. Previously both sides only *penalized* (`focus_lock_bt_admin_removed` / `focus_lock_collar_admin_removed` flipped 0→1 + server tamper event) — the existing `launchAdminActivation` (slave) / `launchAdminNag` (companion) helpers that build a high-priority full-screen-intent notification for the `ACTION_ADD_DEVICE_ADMIN` system dialog were **dead code**, never called from the tamper-detection blocks. Now wired inside the existing one-shot gates on both sides (`android/slave/.../ControlService.java:347`, `android/companion/.../MainActivity.java:261`) so the nag fires exactly once per 0→1 removal transition; cancelled automatically (notification id 97) when the peer's admin is re-granted. Closes the loop where a user who had revoked Bunny Tasker's admin (or never granted it) would stay stuck with a `$500` penalty and a "Re-enable it in Settings → Security → Device admin" message but no system prompt to actually do so — the Collar would just keep re-locking every 6s.
  - **`ConsentActivity.storePriorHomePkg` now uses `MATCH_DEFAULT_ONLY`** to capture the user's *current* default launcher, falling back to full-enumeration only if no default is set. Previously it took the first non-`com.focuslock` launcher in `queryIntentActivities(...)` enumeration order — often the stock launcher even when the user had already switched to a third-party (Fossify Launcher, Nova, Lawnchair, etc.). After a Release, the Collar's `launchPriorHome()` (`FocusActivity.java:1256`) now restores whatever launcher the user actually had set, not Pixel Launcher / stock OEM default. Also explicitly skips the `"android"` chooser pseudo-activity that `resolveActivity` returns when no default is set.
- **Real pair-QR on Bunny Tasker + scan support on Lion's Share + auto-ToS handoff** (`android/companion/...MainActivity.java`, `android/controller/...MainActivity.java`, companion v54/2.21, controller v70/70.0). Closes three gaps that blocked end-to-end first-pair without an adb cable:
  - **Bunny Tasker now renders a real QR** in the Direct Pair dialog (long-press on "Join Mesh"). Previously `qr_code` ImageView at `MainActivity.java:119` was declared but never populated — the dialog was text-only. Now the dialog shows a scannable QR containing the existing `PairingManager.buildQrPayload` payload (`{"t":"fl","f":<fp>,"l":<lan>,"s":<ts>,"p":<port>}`), rendered by an inline WebView that loads the vendored `qrcode-generator` 1.4.4 from `android/companion/assets/qrcode.min.js` (byte-copy of `web/qrcode.min.js` so server + phone share the same renderer). Text-fallback (LAN/Tailscale IP + fingerprint) stays under the QR for manual-entry paths. Build pipeline updated: `android/companion/build.sh` now passes `-A assets` to `aapt2 link` when the `assets/` dir exists — verified the APK bundles `assets/qrcode.min.js` (20768 bytes).
  - **Lion's Share Direct Pair dialog now has a Scan QR button** (`doPairDirect` neutral button). Launches the same ZXing-compatible SCAN intent the web-remote flow uses, parses the `{t,f,l,s,p}` payload via a new `PAIR_QR_SCAN_REQUEST` handler in `onActivityResult`, and re-opens the dialog with IP + port + fingerprint pre-filled (Lion still reviews before tapping Pair — `pendingPair*` instance fields are single-use and clear on consume). Falls back to an AlertDialog prompting to install a scanner app if none is registered for the ZXing SCAN intent. The fingerprint pin (audit C5) stays on — the QR flow just saves typing, doesn't skip verification.
  - **Collar auto-ToS handoff.** The Collar has no launcher icon (CLAUDE.md *"invisible app on Bunny's phone"*) so there was no in-app path for a Bunny to trigger `ConsentActivity` — the ToS + device-admin grant previously required an adb command (MANUAL-QA.md §1). Bunny Tasker's `onCreate` now calls a new `maybeLaunchCollarConsent()` helper when the user lands on the unpaired screen: it checks `DevicePolicyManager.isAdminActive(com.focuslock/.AdminReceiver)` and fires an `Intent` to `com.focuslock/.ConsentActivity` if not active. Safe to re-enter (ConsentActivity is `singleTask` + no-ops on re-consent); silently skips if the Collar package isn't installed at all. Production flow is now adb-free from Bunny Tasker's first launch.
- **Reusable invite codes** (`focuslock-mail.py:1860`). `MeshAccountStore.join` no longer flips a single-use `invite_consumed` flag — instead it tracks `invite_uses` as a counter (diagnostic + future rate-limit hook). TTL (`invite_expires_at`, default 24h) remains the hard gate, so reuse doesn't mean forever; rotation is still a re-create. One Lion can now onboard multiple slaves (additional phones, desktop collars, household devices) with a single invite code — matches the original design intent, fixes the blocker where pairing a second bunny required generating a fresh code. 3 new tests in `tests/test_pairing.py::TestInviteCodeReusable`: two-nodes-one-code happy path with `invite_uses` incrementing 1→2, expired invite still 400s, unknown invite still 400s. Existing fixture shape updated (`invite_consumed: True` → `invite_uses: 0`). Tests `424 → 427`.
- **End-to-end HTTP-level QA for enforcement hot-path endpoints** (`tests/test_e2e_qa.py`, 16 new tests). The pre-existing suite covered each paywall/mesh action's `apply_fn` at the unit level (`mesh_apply_order(action, params, orders)`); this file drives the same actions through the full HTTP dispatch — live `HTTPServer` + real JSON bodies + real `cryptography.hazmat` signature verification — so that wire-format drift (header parsing, canonical payload layout, Content-Length, response shape) is caught by CI rather than on a phone. Three endpoint families covered, each the server-side half of a bunny/lion-signed event the Android apps fire: `/api/mesh/{id}/gamble` (bunny-signed, the Collar's `doGamble` proxy path — admin-authed web-UI equivalent stays in `TestAdminGambleEndpoint`); `/api/mesh/{id}/escape-event` (bunny-signed, covers `escape` tier 1, `tamper_attempt` → $500, `geofence_breach` → $100 + `paywall_original` seed, `sit_boy` → clamp-to-$500 regardless of requested amount — matches the CHANGELOG [1.2.0] P2 hardening claims); `/api/mesh/{id}/messages/send` (lion-signed, pins the C4 audit fix by asserting the client `ts` + signature are preserved in the `MessageStore` entry for recipient-side re-verification, and that the `mandatory_reply` bit is stored as signed). Tests `408 → 424`.

### Security
- **`/webhook/bunny-message` now requires a bunny-signed payload** (`focuslock-mail.py:2555`, Bunny Tasker companion v53/2.20). Closes the first of the four informational webhooks the 2026-04-17 hardening commit deferred (CHANGELOG [1.2.0] line 83 *"The register / heartbeat / controller-register / bunny-message webhooks remain unauth'd for now — they're called from the slave APK which doesn't hold `admin_token`, and need a signed-event rewire that will ship with the slave follow-up commit."*). The v1.2.0 C1 audit closed the *inbound* Collar HTTP signature gap but didn't touch the *outbound* server webhook surface; this closes the highest-priority one. Spoofing risk addressed: the endpoint fires `send_evidence()` (partner-facing email) with caller-supplied `text` + `type`, so a LAN attacker could previously inject `{"type":"self-lock","text":"locked for 2h"}` and the Lion would receive false self-lock evidence. Signature now binds each request to the registered `bunny_pubkey` under `(mesh_id, node_id)`, using the canonical `"{mesh_id}|{node_id}|bunny-message|{ts}"` payload format already shared with `/api/mesh/{id}/gamble` and `/api/mesh/{id}/escape-event`. Clean break, no grace period — pre-v53 Bunny Taskers get `403 {"error":"signature required","min_companion_version":53}` (same framing as the v1.2.0 C1 audit). Bunny Tasker's new `sendSignedBunnyWebhook` helper reads `focus_lock_mesh_id` / `focus_lock_mesh_node_id` / `focus_lock_bunny_privkey` from Settings.Global, SHA256withRSA + PKCS1v15 signs the canonical payload, and merges `mesh_id` / `node_id` / `ts` / `signature` into the inner JSON; skips with a log warning rather than blindly POSTing when any of the three prefs are missing (pre-paired state). Six new tests in `tests/test_pairing.py::TestBunnyMessageSigned` cover missing-sig + bad-sig + expired-ts + unknown-node + unknown-mesh + happy-path with `send_evidence` routing preserved. Tests `402 → 408`. The remaining three deferred webhooks (`/webhook/register`, `/webhook/controller-register`, `/webhook/desktop-heartbeat`) are lower-risk (informational-only peer/heartbeat registries, no evidence-email side effect) and will batch in a follow-up — the `/webhook/desktop-heartbeat` case in particular needs a desktop-key enrollment design decision since the desktop has no outbound signing key today.

### Fixed
- **Android debug-keystore preflight hardening** (`android/slave/build.sh`, `android/companion/build.sh`, `android/controller/build.sh`). Each script's `if [ ! -f debug.keystore ]` guard now validates that the existing keystore actually contains the expected alias — a stale keystore left over from an older `build.sh` (different alias, e.g. pre-`4e5d157` companion default of `bunnytasker` vs post-fix `focuslock`) previously passed the existence guard and then failed deep in `apksigner` with the confusing `entry "<alias>" does not contain a key`. Switched to `keytool -list -keystore … -alias <expected> &>/dev/null` — on non-zero exit (missing file *or* missing alias) the script removes the stale file and regenerates cleanly. `companion/build.sh` also re-validates the copied `../slave/debug.keystore` before trusting it, so a stale slave keystore can no longer silently propagate the old failure mode sideways. Verified in four scenarios: fresh-generate, valid-keystore-no-regenerate (mtime unchanged), stale-alias regeneration in slave/controller, stale-slave-sibling regeneration-not-copy in companion. Addresses the pre-existing flake flagged in `SESSION-HANDOFF-2026-04-21b.md §Outstanding gotchas #1` — the original v1.0 → 4e5d157 bug is long fixed; this closes the class of problem so it can't recur if a future build.sh edit changes the alias again.
- **Web UI gamble outcome display + relay-mode support** (`web/index.html`, `focuslock-mail.py`). Two linked fixes closing the pre-existing follow-up flagged in `SESSION-HANDOFF-2026-04-17.md §Real bugs surfaced this session #3` and `§Next session #5`:
  - **Outcome display**: the "Double or Nothing" modal now matches the server's actual math — heads halves the paywall (rounded up), tails doubles. The modal preview previously claimed heads *cleared* the paywall, and the post-flip toast always read *"Bunny loses! Paywall doubled."* because the response-key check (`data.result === 'win'`) never matched the real response shape (`heads` / `tails`) that shipped in the 2026-04-17 P2 paywall hardening (the RNG moved server-side, but the web UI wasn't updated). Outcome toast now surfaces the real `new_paywall` from the response rather than asserting a fixed amount. Lion's Share Android (`controller/MainActivity.java:2713`) was already correct.
  - **Relay-mode gamble**: the web UI's relay path (`apiRelay()` → `POST /admin/order`) mapped `/api/gamble` to action `"gamble"`, but the server only knew `"gamble-resolved"` (the dumb-setter apply action) — so relay-mode gamble silently no-op'd. `focuslock-mail.py:2686` now intercepts `action == "gamble"` in `/admin/order` before the generic mesh dispatch (same pattern as `set-vault-only`), runs `secrets.SystemRandom` + the same `ceil(old/2)` / `old*2` math as `/api/mesh/{id}/gamble`, and delegates the actual write to `_server_apply_order(..., "gamble-resolved", ...)` so the two entry points can't diverge. Response shape matches the bunny-signed endpoint `{ok, result, old_paywall, new_paywall}` so the web UI reads the same keys in LAN mode and relay mode. Admin-auth gated via `ADMIN_TOKEN`, vault blob carries the resolved outcome (not the request), vault-mode slaves pick it up through the existing `gamble-resolved` path. 4 new integration tests in `TestAdminGambleEndpoint` (`tests/test_paywall_hardening.py`) pin the auth gate, 409 on empty paywall, response shape, and outcome-application round-trip through a live `HTTPServer`. Tests `398 → 402`.
  - **Lion's Share gamble dialog parity** (`android/controller/.../MainActivity.java:2702`, controller v69/69.0): `doGamble` now shows the same amount-aware preview as the web UI — *"Current paywall: $100 / Heads = halved to $50 / Tails = doubled to $200"* — computed from a new cached `lastPaywall` field populated by the status poller at line 415. Integer ceiling `(n+1)/2` matches Python's `math.ceil(n/2)` in the server across the even/odd boundary. Fallback to the old amount-less wording when `lastPaywall` is 0 (not yet polled). APK rebuilds clean against the new keystore-preflight checks.

## [1.2.0] — 2026-04-21

All six audit criticals closed (C1–C6). Ships P2 paywall hardening (server as single writer), deadline-bound tasks, desktop registry race fix, audit-driven server + installer hardening, and the Bunny Tasker / Lion's Share signature fixes (C4, C5). The last critical, C1 (slave HTTP signature verification), landed in this release — The Collar's local HTTP endpoints now require an RSA-signed payload from the paired lion or the same-phone bunny key, with per-request nonce + ±5 min timestamp window for replay protection.

### Added
- `docs/THREAT-MODEL.md §Known weaknesses` — documented BunnyTasker display-keys-only mesh apply, cleartext-for-LAN posture.
- Bunny Tasker now has an explicit `res/xml/network_security_config.xml` declaring the same cleartext-for-LAN intent as The Collar (was relying on the platform default).
- **P2 paywall hardening (2026-04-17)** — last state-ownership migration. Server is now the single writer for enforcement-driven `paywall` increments; phones are pure event reporters. New `shared/focuslock_penalties.py` with escape-tier formula + compound-interest rate table. New server actions `app-launch-penalty` (`+$50` with 10s endpoint dedup), `good-behavior-tick` (`-$5` in tribute/fine loop), `compound-interest-tick` + `check_compound_interest()` 60s thread, `tamper_attempt` event type on `/escape-event`. `escape-recorded` now applies tiered `$5×tier` (1-3 → $5, 4-6 → $10, …). `tamper-recorded` applies `$500` on attempt/detected and `$1000` on removed (was only removed). `geofence-breach-recorded` applies `+$100` + seeds `paywall_original` for compound base. Collar (slave v69) + companion (v49) deleted local `focus_lock_paywall` writes for these events. 23 new tests in `tests/test_paywall_hardening.py`. See commit `a9dec67`.
- **P2 paywall hardening — deferred follow-ups (2026-04-17)** — closes the five items the original P2 commit deferred. Server is now the single writer for the remaining bunny-initiated and SMS-driven paywall paths.
  - **SMS sit-boy** is now a `sit_boy` event type on `/api/mesh/{id}/escape-event`. The Collar's `SmsReceiver` keeps the lock-state writes (UX immediacy) but delegates the dollar amount to the server — clamped to `SIT_BOY_MAX_AMOUNT = $500` so a hijacked controller SIM can't drain. New action `sit-boy-recorded` + 4 tests.
  - **Bunny-initiated unsubscribe** moved to a new `POST /api/mesh/{id}/unsubscribe` (bunny-signed, mirrors `/subscribe`). Fee table standardised to `UNSUBSCRIBE_FEES = {bronze: 50, silver: 70, gold: 100}` (2× one period of the actual subscribe-charge amount — fixes a pre-existing inconsistency where the Collar's hardcoded $20/$50/$100 disagreed with what the Bunny Tasker dialog showed). Bunny Tasker's `doUnsubscribe()` now POSTs the signed request via the new `postUnsubscribeToMesh()` helper. The Collar's local `doUnsubscribe()` is `@Deprecated` and refuses with a 4xx pointing the caller at the server endpoint — no local paywall write remains. New action `unsubscribe-charge` + 5 tests.
  - **Bunny-initiated gamble** moved to a new `POST /api/mesh/{id}/gamble` (bunny-signed). Server runs `secrets.SystemRandom().choice([True, False])` and applies via the new `gamble-resolved` action; closes the "tampered Collar always rolls heads" loophole. The Collar's `doGamble()` is now a thin signing-proxy preserving the existing local-HTTP response contract for the web UI / Lion's Share callers. 4 tests.
  - **Release-Forever** now zeros `paywall` + `paywall_original` in the orders doc when `release-device` fires with `target=all`. Without this the orders doc kept the pre-teardown balance forever (no Collar remained to bump it down). Per-device targets unaffected. 2 tests.
  - **Local `PaymentListener.java` removed** (~226 lines). The server's IMAP scanner has been the authoritative payment-detection path since 2026-04-15; the local NotificationListenerService duplicated it, wrote unsigned amounts, and forced the Collar to hold a broad notification-access permission across every bank app on the phone. `AndroidManifest.xml` service entry, `BIND_NOTIFICATION_LISTENER_SERVICE` permission, and the `re-enslave-phones.sh` `cmd notification allow_listener` grant + verification step all gone. `docs/STATE-OWNERSHIP.md` payment row updated.
  - Slave bumped to v70 (8.27); companion to v50 (2.17). Total tests: 38 (was 23).

### Changed
- Android versions bumped for landmine fixes: The Collar v61 (was v60), Bunny Tasker v44 (was v43), Lion's Share v64 (was v63).
- Android versions bumped for P2 paywall hardening follow-ups: The Collar v70 (was v69), Bunny Tasker v50 (was v49).
- Bunny Tasker bumped to v51 (2.18) for the audit C4 signature-verification fix.
- Lion's Share bumped to v67 (67.0) for the audit C5 fingerprint-pinning fix.
- Android versions bumped for audit C1 (slave HTTP signature verification): The Collar v71 (8.28), Lion's Share v68 (68.0), Bunny Tasker v52 (2.19).
- Lion's Share manifest dropped the deprecated `android:usesCleartextTraffic="true"` attribute — the `networkSecurityConfig` file is authoritative and already permits cleartext. Added inline comment in the config explaining the LAN-gossip rationale and the HTTPS-relay discipline requirement.

### Fixed
- **The Collar (slave) TOCTOU race on meshVersion**: gossip-RX handler at `ControlService.java:2446` now wraps the `check-apply-set` on `meshVersion` in `synchronized (meshVersion) { ... }`, matching the pattern already used by the gossip-TX response handler at ~line 3676. Closes landmine #18 (was CRITICAL-for-correctness, LOW-practical).
- **Desktop heartbeat registry file-write race (roadmap #5)** — `/run/focuslock/desktop-heartbeats.json` was mutated from two threads (HTTP heartbeat handler + hourly `check_desktop_heartbeats` penalty thread) with no synchronization, so a heartbeat landing mid-penalty-tick could get clobbered back to stale state. Lifted the registry into a new thread-safe `DesktopRegistry` class in `focuslock_mesh.py` that mirrors the `MessageStore` shape (internal `threading.Lock`, atomic temp+`os.replace` save). `focuslock-mail.py` instantiates the singleton and both call sites now go through `heartbeat()` / `snapshot()` / `mark_warned()` / `mark_penalized()` / `summary_line()`. Schema and HTTP surface unchanged; no migration or version bump. 6 new tests in `TestDesktopRegistry` including a 25-thread concurrent-heartbeat regression.

### Security
- **Audit C1 — The Collar verifies RSA signatures on every state-mutating local HTTP POST (2026-04-21).** `ControlService.handle()` previously accepted any POST to `/api/lock`, `/api/add-paywall`, `/api/release-forever`, `/api/gamble`, `/api/set-geofence`, and 20 other state-mutating endpoints on ports 8432 / 8433 with no authentication — an unqualified "same trust model as ADB over TCP" assumption that fails the moment the LAN is not under the lion's exclusive control (public WiFi, guest networks, hostile roommate, Tailscale tailnet with other members, etc.). Any attacker who could reach the phone's HTTP port could force-lock, set arbitrary paywall, wipe tasks, or fire release-forever. Fixed with a three-part closure:
  - **New `SigVerifier` class** (`android/slave/src/com/focuslock/SigVerifier.java`) gates every non-exempt `/api/*` POST behind `X-FL-Ts` / `X-FL-Nonce` / `X-FL-Sig` headers. Canonical payload: `focusctl|<path>|<ts>|<nonce>|<k1=v1&k2=v2&…>` — keys lex-sorted, values form-url-encoded, booleans `0/1`, integral numbers as decimal, null omitted, non-JSON bodies sign as `_raw=<enc>`. RSA-PKCS1v15-SHA256 verified against the stored lion pubkey first, falling back to the stored bunny pubkey so same-phone Bunny Tasker calls validate through the same gate. Replay protection: ±5 min `ts` window + in-memory LRU nonce cache (4096 entries, 10 min TTL, nonce recorded atomically before verify so two threads can't both pass with the same `(ts,nonce)`). Exempt paths: `/api/ping`, `/api/status`, `/api/adb-port`, `/api/pair` (bootstrap — lion key arrives in the body; `doPair` refuses if already paired), plus web-UI static paths and `/mesh/*` (which has its own vault-blob signature layer).
  - **Lion's Share (v68, 68.0)** — `MainActivity.api()` direct-mode branch now routes through a new `buildDirectSigHeaders()` that reads `lion_privkey` from prefs, generates a 16-byte URL-safe base64 nonce, canonicalizes the payload via `VaultCrypto.canonicalizeDirectPost` (byte-for-byte identical to the slave), signs via the existing `VaultCrypto.signString`, and attaches the three headers through a new `meshPost(url, body, extraHeaders)` overload. All 12 existing direct-HTTP call sites inherit signing for free; mesh-server and vault modes untouched (already carry lion signatures through their own protocol layers).
  - **Bunny Tasker (v52, 2.19)** — `postToCollar` inlined the canonicalize + sign logic (bunny-privkey via `PairingManager.sign`) so all four same-phone call sites (collar-admin-tamper auto-lock, overdue-subscription auto-lock, manual self-unlock, manual self-lock) keep working against the v71 Collar. Loopback-exemption rejected: a sideloaded hostile app can bind 127.0.0.1 too; signatures make loopback trust explicit.
  - **Clean break, no grace period.** v71 rejects unsigned `/api/*` POSTs with `403 {"error":"signature required","min_controller_version":68}`. A deprecation-accept window would re-create the exact CVE for the duration. Users update both apps together via the release page; there is no fleet rollout.
  - **16 new tests** in `tests/test_http.py::TestDirectPostCanonicalize` pin the canonical format with golden vectors (keys sorted, booleans `0/1`, integral floats collapsed, null omitted, unicode UTF-8 encoded, `&`/`|`/space URL-encoded, protocol-tag prefix). The Python reference is the written spec — any drift in the three Java copies (slave `SigVerifier.canonicalize`, controller `VaultCrypto.canonicalizeDirectPost`, companion `collarCanonicalize`) is caught by these vectors.
  - Slave v71 (8.28). Controller v68 (68.0). Companion v52 (2.19).
- **Audit C5 — Lion's Share pins the bunny fingerprint on direct pairing (2026-04-18).** `pairDirect` at `android/controller/.../MainActivity.java:2161` was POSTing the lion pubkey to `http://<bunny-ip>:8432/api/pair` and trusting whatever `bunny_pubkey` came back in the JSON response — with no comparison against the 16-char SHA-256 fingerprint the bunny's own screen displays. An attacker on the LAN (or any MITM on the direct-pair path) could swap the bunny's pubkey with their own and become the permanent in-between for every lion→bunny crypto op from that point on. Fixed:
  - **Controller (v67, 67.0):** direct-pair dialog now includes an "Expected fingerprint" field. `pairDirect` accepts it, computes the SHA-256 fingerprint of the returned `bunny_pubkey` the same way `PairingManager.getFingerprint` does on the bunny side (first 8 bytes → 16 hex chars), and **aborts the pairing** on mismatch with a status message showing both values. If the user leaves the field blank the pairing still completes (backwards compat) but the status message loudly includes the received fingerprint so they can verify out-of-band; the stored status reads "PAIRED direct (UNVERIFIED fp=…)" until re-paired with verification.
  - **Companion unchanged** — it already publishes the fingerprint in its QR payload (`PairingManager.buildQrPayload`) and on the pairing screen (`pairing_fingerprint` TextView), so the bunny side was the well-designed half.
- **Audit C4 — Bunny Tasker verifies lion signature before mandatory-reply auto-lock (2026-04-18).** `refreshMeshMessages` at `android/companion/.../MainActivity.java:1929` was reading `from:"lion"` and `mandatory_reply:true` straight from the fetched message JSON and auto-locking the phone (`focus_lock_active=1`) if the message was overdue, without any crypto check on the sender. A compromised relay — or any path that bypasses the signed `/messages/send` endpoint — could inject `from:"lion", mandatory_reply:true, ts:old` and force-lock the bunny. Fixed at both ends:
  - **Server:** `/api/mesh/{id}/messages/send` now stores the lion signature + client `ts` + `node_id` alongside each message entry in `MessageStore`, so recipients can reconstruct the signed payload (`mesh|node|from|text|pinned|mandatory|ts`) on fetch. The server already verified the signature on receive — this commit just preserves the evidence through to the reader.
  - **Bunny Tasker (v51):** new `verifyLionMessageSignature(JSONObject, meshId)` helper reconstructs the signed payload from the fetched message and checks it against the locally-stored `focus_lock_lion_pubkey` via `PairingManager.verify`. The auto-lock path only fires if the signature verifies; unsigned + invalid are treated identically (log + skip). Pre-fix messages in existing stores have no signature and will not auto-lock — acceptable because the threshold is 4h and lion can always re-send.
  - No controller change needed — Lion's Share (v64+) already signs over the pinned+mandatory flags. Slave unchanged.
  - 2 new tests in `TestMessageStore` pin the signature + client-ts round-trip through the store.
- **Audit-driven server hardening (2026-04-17).** Full-codebase audit surfaced six exploitable issues in the server + installer. This commit closes the ones that don't require an APK rebuild; the Android-side items (slave HTTP signature verification, Bunny Tasker mandatory-reply signature check, QR pairing fingerprint pin) are deferred to a follow-up commit.
  - **Unauthenticated enforcement webhooks (C2).** `/webhook/entrap` and `/webhook/desktop-penalty` now require `admin_token`. `/webhook/entrap` directly calls `enforce_jail()` (ADB-disables launcher, settings, user-switcher); `/webhook/desktop-penalty` does a read-modify-write on the paywall via ADB. Both were reachable with no auth. The Linux desktop collar (`focuslock-desktop.py`) now reads `admin_token` from `~/.config/focuslock/config.json` (or `FOCUSLOCK_ADMIN_TOKEN` env) and sends it in the penalty payload. `/webhook/desktop-penalty` also clamps caller-supplied `amount` to 0..$500 as defense-in-depth. The register / heartbeat / controller-register / bunny-message webhooks remain unauth'd for now — they're called from the slave APK which doesn't hold `admin_token`, and need a signed-event rewire that will ship with the slave follow-up commit.
  - **Disposal-token double-spend race (C3).** `/admin/order` consumed single-use disposal tokens with an unlocked check-then-set on the `_disposal_tokens` dict, so two concurrent redemptions could both pass the `used==False` check and double-apply. Added module-level `_disposal_tokens_lock`; the issue + cleanup path at `/admin/disposal-token` and the atomic check-validate-claim path at `/admin/order` now both hold the lock.
  - **Sudoers privilege escalation via wildcard source paths (C6).** `installers/install-desktop-collar.sh` was writing `/etc/sudoers.d/focuslock` with rules like `NOPASSWD: /usr/bin/cp * /opt/focuslock/*` and `chmod * /opt/focuslock/*`. Sudoers wildcards match `/`, so `sudo cp /etc/shadow /opt/focuslock/x` and `sudo chmod 4755 /opt/focuslock/user-written.sh` both matched — enabling arbitrary-file exfiltration to root-readable and local-root via setuid. Replaced with `install -D -m 0{644,0755}` rules scoped to filename patterns starting with `focuslock`, `lion_pubkey.pem`, `collar-icon*`, `crown-*`, or `web/index.html`; mode is restricted to non-setuid/setgid/sticky values. `systemctl restart` rules replaced wildcard patterns (`*focuslock*`, `*claude*`) with explicit service names. `installers/re-enslave-desktops.sh` updated to use `sudo install -D -m 0644` where it previously used `sudo cp`; the redundant `sudo chmod 755` on the deployed scripts was removed (`install -m` already sets the mode).

### Changed
- **Mesh persistence lock hygiene (audit H1, H2).** `OrdersDocument.get()` now acquires `self.lock` — a concurrent `apply_remote()` could previously return a torn read on a mid-update key. Added `OrdersDocument.snapshot()` returning a shallow copy safe to read outside the lock. Added `PeerRegistry.snapshot()` returning `{node_id: PeerInfo}` safe to iterate; `handle_mesh_status` switched to it (was iterating `peers.peers.items()` directly, which could raise `RuntimeError` if another thread called `update_peer` mid-iteration). `VoucherPool.redeem` (audit H5) was already correctly locked — the audit flagged it as a false positive, no change needed. `check_compound_interest`'s `list(_orders_registry.docs.items())` snapshot (audit H7) is also the correct pattern — no change needed.

## [1.0.0] — 2026-04-14

First public release. Everything below landed in the run-up to v1.0 across Phases 0 → 7.5 (see `docs/PUBLISHABLE-ROADMAP.md` and the commit log for sequencing).

### Added
- `SECURITY.md` — vulnerability disclosure policy
- `CODE_OF_CONDUCT.md` — custom code of conduct tailored to the power-exchange context
- `.editorconfig` — shared editor conventions
- `CHANGELOG.md` — this file
- `docs/PUBLISHABLE-ROADMAP.md` — phased plan to v1.0.0
- `pyproject.toml` — ruff + mypy configuration (Phase 1a)
- `.pre-commit-config.yaml` — ruff, ruff-format, mypy on shared/, hygiene hooks (Phase 1a)
- `.git-blame-ignore-revs` — skip mechanical reformat commits in `git blame` (Phase 1a)
- `logger = logging.getLogger(__name__)` module pattern in `shared/focuslock_vault.py`, `shared/focuslock_payment.py`, `focuslock_mesh.py`, `focuslock-mail.py` (Phase 1b-core)
- `logger = logging.getLogger(__name__)` extended to the remaining 11 modules: `focuslock-desktop{,-win}.py`, `focuslock_ntfy.py`, `installers/re-enslave-watcher.py`, and the shared/ helpers (`focuslock_adb`, `focuslock_config`, `focuslock_evidence`, `focuslock_http`, `focuslock_llm`, `focuslock_sync`, `focuslock_transport`) (Phase 1b-tail)
- `logging.basicConfig` wired at startup in the three entry-points (`focuslock-mail.py`, `focuslock-desktop.py`, `focuslock-desktop-win.py`) — format `%(asctime)s %(levelname)-7s %(name)s: %(message)s`, datefmt `%Y-%m-%d %H:%M:%S`. Library modules inherit from the root logger (Phase 1b-tail)
- **Phase 2 — unit test suite.** `tests/` with 266 tests covering the shared/ security-critical surface. Coverage: `focuslock_vault.py` 100%, `focuslock_payment.py` 92%, `focuslock_mesh.py` 70%, `focuslock_config.py` 98%, `focuslock_http.py` 100%, `focuslock_adb.py` 96% — 78% combined (≥75% exit criterion met). `[tool.pytest.ini_options]` + `[tool.coverage.*]` added to `pyproject.toml`. Invoke with `uv run --with pytest --with pytest-cov --with cryptography pytest tests/`. Also documents a within-call dedup quirk in `VoucherPool.store` (cross-call dedup, which is what matters in production, works correctly).
- **Phase 3 — QA infrastructure.** `docs/QA-CHECKLIST.md` (14-section regression matrix), `docs/STAGING.md` (isolated staging mesh setup with Waydroid), `docs/MANUAL-QA.md` (on-device checklist for radios Waydroid can't emulate). `staging/config.json.template` + `staging/start-staging.sh` for a scriptable staging relay bound to 127.0.0.1. `staging/qa_runner.py` — scripted Lion that drives the admin API through lock/unlock/paywall/subscribe/message flows and verifies state transitions. On first run, surfaced two real bugs in `focuslock-mail.py` (see Fixed).
- **Lion's Share controller v63** — new **Payment Email** button on the main screen (`android/controller/res/layout/activity_main.xml`) opens a dialog for IMAP host / email / app-password, saves to prefs, and POSTs `/api/set-payment-email`. Version bumped 62 → 63 (`AndroidManifest.xml`), `installers/re-enslave-lib.sh` retargeted to `focusctl-v63.apk`. Deployed to Jace's phone 2026-04-14 with data preserved (`-r` install, no re-pair).
- **Phase 4 — CI pipeline.** `.github/workflows/ci.yml` (lint + ruff format + mypy + pytest matrix on Python 3.10/3.11/3.12, build all 3 APKs, build Windows EXEs, verify APK signatures) and `.github/workflows/release.yml` (tag-driven release with auto-generated `SHA256SUMS.txt`, optional release-keystore secret, GitHub Release publication). `.github/dependabot.yml` for weekly pip + GitHub Actions updates. Concurrency cancellation on stale PRs. RUF005 added to `build-win.py` per-file-ignores; one stale `body` → `_body` cleanup in `staging/qa_runner.py`. Residual `ruff format` drift on 10 previously-unformatted files (`focuslock-mail.py`, desktop collars, `shared/focuslock_{payment,sync}.py`, all `tests/test_*.py`) applied to make Phase 4 CI green.
- **Phase 5 — Build reproducibility.** `--release` flag on all three Android `build.sh` scripts. Release builds require `FOCUSLOCK_KEYSTORE` + `FOCUSLOCK_KEYSTORE_PASS` env vars and fail loudly if unset; debug auto-keystore stays for contributor builds. `SOURCE_DATE_EPOCH` set from git commit timestamp in the release workflow before PyInstaller runs. `--release` and `--debug` are the only accepted flags; unknown args fail fast.
- **Phase 6 — Outsider documentation.** `docs/BUILD.md` (toolchain matrix, per-component build commands, release-keystore generation, reproducibility notes), `docs/CONFIG.md` (every config field with type/default/security implications + 3 example configs), `docs/SELF-HOSTING.md` (DNS → TLS → server → first pairing in 8 steps + ops + backup checklist), `docs/THREAT-MODEL.md` (in-scope vs out-of-scope adversaries, two trust tiers, known v1 weaknesses), `docs/ARCHITECTURE.md` (sanitized component map, source layout table, sequence diagrams for lock/payment/pairing/subscription, onboarding checklist). README rewritten with a pinned consent disclaimer banner, "Who is this for?" section, and a documentation index linking every doc page.
- **Phase 7 — Dependency + supply-chain hygiene.** `pyproject.toml` now ships proper `[project]` metadata (PEP 621): name `focuslock`, version `0.9.0`, GPL-3.0-or-later, classifiers, dependency on `cryptography>=42`, and `[project.optional-dependencies]` groups `desktop-win` (pystray + pillow), `server` (reserved), `dev` (pytest + pytest-cov + ruff + mypy). Project installs as metadata-only (`py-modules = []`) until a future src/ migration. Wheel builds clean (`uv build --wheel`). New `sbom` job in `release.yml` generates `SBOM.cdx.json` (CycloneDX 1.5) from `requirements.txt` via `cyclonedx-bom`; SBOM is uploaded as a release artifact, hashed in `SHA256SUMS.txt`, and attached to the GitHub Release. Android build-tools already pinned at `35.0.0` in every `build.sh` (verified). `SECURITY.md` already had the response SLA from Phase 0 (no change needed). ARM64 cryptography wheel-from-source caveat documented in `docs/BUILD.md`. `.gitignore` covers `*.egg-info/` from local wheel builds.
- **Phase 7.5 — Supply-chain finishing.** All GitHub Actions in `ci.yml` and `release.yml` pinned by commit SHA (tags kept as `# v4` comments for dependabot readability); immutable against tag-replacement supply-chain attacks. New `.github/workflows/codeql.yml` — CodeQL SAST on push + PR + weekly cron, queries `security-extended,security-and-quality`. New `.github/workflows/scorecard.yml` — OpenSSF Scorecard on push + weekly cron + branch-protection-rule trigger, publishes SARIF to the Security tab. Sigstore build provenance (`actions/attest-build-provenance@v2`) added to `release.yml` covering every APK, EXE, SBOM, APK-CERTS, and SHA256SUMS artifact — verifiable with `gh attestation verify`. New `APK-CERTS.txt` generation step extracts each APK's signing-cert SHA-256 fingerprint via `apksigner verify --print-certs` and publishes alongside `SHA256SUMS.txt` so users can validate sideloads against an authoritative cert list (with explanatory section in `docs/BUILD.md`). `CONTRIBUTING.md` rewritten end-to-end — what we accept vs discuss vs decline, AI-assisted-contribution disclosure policy, local-test command recipe, Android no-Gradle expectations. New `.github/PULL_REQUEST_TEMPLATE.md` + `.github/ISSUE_TEMPLATE/{bug,feature,config}.yml` — bug template gates on SECURITY.md acknowledgment, feature template gates on design discussion + scope checklist (no consent/safety weakening), config routes security reports to the Security advisory form and usage questions to Discussions.

### Changed
- `focuslock-mail.py` — default `Host` header fallback changed from operator's personal domain to `localhost`
- `android/companion/.../MainActivity.java` — server URL input hint changed from operator's personal domain to a generic example
- Python codebase reformatted with `ruff format` (Phase 1a — mechanical, skipped in `git blame` via `.git-blame-ignore-revs`)
- Security-critical exception handlers now use structured logging at appropriate severity (Phase 1b-core):
  - Vault signature-verify and decrypt failures → `logger.warning`
  - Payment email parse + IMAP loop errors → `logger.warning`/`logger.error`
  - Mesh signature verify + state I/O (orders, peers, vouchers) → `logger.warning`
  - Roadmap-called-out `[warn]` prints in `focuslock-mail.py` (paywall parse, ntfy push, pairing registry) → logger
- All remaining ~300 diagnostic `print(...)` calls across `focuslock-mail.py` (~90), `focuslock-desktop.py` (~75), `focuslock-desktop-win.py` (~91), `focuslock_mesh.py` (~14), `focuslock_ntfy.py`, `shared/focuslock_payment.py` (~9 missed in Phase 1b-core), and smaller modules migrated to `logger.{info,warning,exception,debug}` with `%-format` lazy formatting (Phase 1b-tail)
- Silent `except Exception: pass` blocks triaged (Phase 1b-tail) — ADB wrapper, mesh trust I/O, mesh account load, homelab URL parse, Lion pubkey load now leave a debug breadcrumb. Tailscale/DNS probes and liberation cleanup stay intentionally silent.

### Fixed
- **Real bugs surfaced by lint (Phase 1a):**
  - `focuslock-desktop-win.py` was missing `import subprocess` (5 crash sites: bedtime check + 4 process-management paths) and `import datetime` (1 bedtime check site)
  - `focuslock-mail.py:806` called bare `push_to_peers(...)` instead of `mesh.push_to_peers(...)` — fine-application mesh push was broken
- `set-payment-email` feature completed with missing `ORDER_KEYS` schema entries and `focuslock_payment.py` consumer (hot-swappable IMAP creds via Lion's Share app)
- **QA-surfaced bugs (Phase 3):**
  - `focuslock-mail.py:530` — `add-paywall` accepted negative amounts and allowed the paywall to go negative. Now clamps the result to `max(0, current + delta)` and catches non-integer amount values.
  - `focuslock-mail.py:598-612` — `subscribe` with no explicit `due` param set `sub_due` to `now` instead of the documented `now + 7d`; the `now + 7d` branch was unreachable. Rewrote so the default is `now + 7d`, explicit `"now"` is still honored, and explicit ms values pass through — consistent with `project_sub_due_cap.md` ("pre-pay forfeits remainder").

### Security
- Payment security: anti-self-pay + recipient verification (prior work)
- Production hardening: crash safety, security, observability (prior work)

## [0.x] — pre-release

Development history prior to v1.0.0 is recorded in the git log. Notable milestones:

- **Phase 4D** — legacy plaintext mesh endpoints removed; server speaks vault only
- **Phase 6.5 / 7 / 8** — multi-signer classification, transport abstraction, trust page, 16 security fixes
- **Phase 5 / 6** — AndroidKeyStore integration, bedtime mode, screen time, ntfy push, QR web login
- **Multi-tenant isolation** — operator mesh scoping for hosted relay deployments
- **Vault design** — E2E encryption (AES-256-GCM + RSA-OAEP), RSA-signed orders, zero-knowledge relay

See `git log` for full commit history since `0de5fd9` (initial public repo push, 2026-04-09).

[Unreleased]: https://github.com/NoahBunny/opencollar/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/NoahBunny/opencollar/releases/tag/v1.0.0

# QA — Lion's Share 83 (tab reorganisation)

Gate for publishing controller **83** to the OpenCollar F-Droid repo. The
change moved every control in the app between tabs, pulled seven of them off
the tabs entirely into a kebab, and extracted two logic cores out of
`MainActivity`. Companion docs: `docs/USABILITY-AUDIT-2026-04-28.md` (why),
`docs/MANUAL-QA.md` (single-device fundamentals).

> **Why part of this is manual.** UI automation for these apps was spiked in
> 2026-04 and shelved — `docs/UI-AUTOMATION-DECISION.md`. Everything that can
> be checked without a device is automated below and runs in CI; §3 is the part
> that needs hands. **§2b now covers most of it programmatically** (75/77 on a
> clean Waydroid); what remains in §3 is the handful of rows that need a fully
> provisioned Collar or a second device.

---

## 1. What is automated, and what each check actually protects

| Check | Command | Result | Protects |
|---|---|---|---|
| Layout↔code id contract | `pytest tests/test_android_layout_ids.py` | ✅ 10 passed | Every string-based view lookup resolves. `MainActivity` uses `getResources().getIdentifier()`, so a moved id fails at *runtime* — and with ~20 null-guards and several `try{}catch(Exception){}` wrappers around those lookups, it fails **silently**. This is the single largest hazard in this change. |
| Section convention | (same file) | ✅ | `wireSection()` builds `_head`/`_body`/`_chevron` ids by concatenation, where no static check can see them. A half-rename would leave a header that does not open. |
| No orphan buttons | (same file) | ✅ | A button the Lion can see and press that no code reaches reads as a broken feature, not a missing one. |
| Java ↔ Python wire conformance | `make qa-android` | ✅ 30 passed | 17 of these normally skip without the CLIs exported. Canonical JSON, orders signatures and the message pipe-payload still match the server byte-for-byte. |
| JVM unit tests | `bash android/build-conformance.sh` | ✅ 106 passed (was 80) | +26 for the two extracted classes. |
| Python suite | `pytest -q` | ✅ 1341 passed, 21 skipped | |
| Gated mesh coverage | `make qa-cov-mesh` | ✅ floor holds | |
| Lint / format / types | `ruff check`, `ruff format --check`, `mypy shared` | ✅ clean, 135 files | |
| APK builds + signs | `android/controller/build.sh` + `apksigner verify` | ✅ 83/83.0, 206 KB | |
| Signed commits | `scripts/sign-branch.sh` | ✅ 0 unsigned sensitive | |

**Mutation-checked** (a test that cannot fail protects nothing):

- id contract — a renamed id, a broken menu resource name, a half-renamed
  section and a planted orphan button each fail, naming file and line.
- `OptimisticState` — removing the timer slack, forcing the paywall direction,
  and dropping the generation check each fail the suite.

## 2. Static checks done by hand

- **No stale tab index.** The inbox moved from index 2 to 3. `selectTab()` is
  called from exactly four places, all four tab buttons; nothing opens a tab by
  index from a notification or intent (no `putExtra` tab routing exists).
- **Thread safety of the new UI writes.** `refreshLockButton()` and the section
  summaries are only reached from `updateLiveStatus()`, which is invoked solely
  via `handler.post(...)` at four call sites — UI thread throughout, the same
  contract the code it replaced had.
- **Resources are packaged**, not merely authored: `res/menu/overflow.xml` and
  `res/drawable/danger_block.xml` are both present inside the built APK.
- **Homelab gating survived the move.** `btn_payment_email` stopped being a View,
  so `applyHomelabGating()` no longer touches it; `showOverflow()` builds the
  menu fresh on each tap and hides that item per `homelabConfigured()`, which
  evaluates it later than before rather than earlier.
- **A duplicate was found by the reorganisation itself.** `doClearBalance` and
  `doClearPaywall` POSTed the identical body to the identical endpoint; the
  only difference was that one confirmed first and one fired instantly. They
  sat on separate tabs, so nothing made it visible. One clear survives, and it
  is the confirming one — a mis-tap beside `+$1` forgives real money owed.
- **UI-automation scripts checked too.** `tests/ui/` addresses views by
  `com.pkg:id/name` string; those drift the same way and more quietly, since
  they are shelved behind `UI_TESTS=1`. Now covered by the same test file.
- **Wired-id diff is exactly the intended set**: the 7 kebab buttons became 7
  menu items, the page/tab ids were renamed, and `btn_kebab` plus the two
  section summaries are new. Nothing was dropped.

## 2b. Programmatic device walk — RUN 2026-08-23, 75/77 ✅

Run on a **wiped, freshly initialised Waydroid** (LineageOS 20 / Android 13,
x86_64) at 1080x2400 @ 420dpi, driven by `staging/qa_lions_share_83.py` against
a staging relay on the host. **87 passed, 2 failed** (re-run after the veneration picker landed).

Set-up performed, for reproducibility:

- `sudo waydroid init -f` after wiping `/var/lib/waydroid` (needs root — the
  only step this harness cannot do itself).
- adb authorised by dropping the host's `adbkey.pub` into
  `~/.local/share/waydroid/data/misc/adb/adb_keys` and restarting the session.
  `waydroid shell` needs root; the data dir does not.
- A real mesh created **from the app** against `staging/start-staging.sh`
  (relay reachable from the container at the bridge gateway `192.168.240.1`),
  and a real bunny paired via invite code from Bunny Tasker. The relay logged
  the node registration and its state-mirror, so this was an end-to-end mesh,
  not a mock.

What it verified, beyond "the control exists": the four verb-named tabs are
there and the old power-level ones are not; every control is on the tab it was
moved to; Live Pokes and Modifiers start collapsed and the collapsed summary
reads `Taunt, Mute` after toggling them; picking **Compliment** reveals its
field and switching away hides it; the duplicate clear is gone and the survivor
asks first; the kebab carries its six entries with Payment Email correctly
hidden (no homelab) and Release Forever still reachable and still confirming.
Since the veneration picker landed it also draws a task by category, confirms
"Draw another" gives a different one, and checks that accepting sets the text,
the suggested reps and Random caps together.
Screenshots of all four tabs in `docs/Screenshots/lions-share-83-*.png`.

**The 2 failures are environment, not app.** Row 3.10d charges $5 and expects
the balance to move; with no *fully provisioned* Collar (device admin plus the
launcher role, both declined so the container would not lock the automation out
mid-run) the order has nowhere to land, so the optimistic bump reverts and the
ledger stays empty. Two earlier "failures" — `lion_payment_history` and
`device_cards_container` absent — turned out to be the harness's fault, not the
app's: both are filled programmatically, so while empty they have zero height
and never appear in a `uiautomator` dump. Confirmed in logcat
(`visible: false`), and the checks now assert the always-rendered section
header instead.


## 2c. Onboarding device-admin walk — RUN 2026-08-23 ✅

Walked on a container reset with `installers/waydroid-reset.sh` (no download),
both apps installed fresh, **zero active admins** at the start.

| Step | Result |
|---|---|
| Bunny Tasker wizard reaches the new admin page | ✅ "The leash needs a grip", status `○ Not granted yet` |
| "Give Bunny Tasker admin" opens the system screen | ✅ `settings/…deviceadmin.DeviceAdminAdd`, carrying our written explanation |
| Activating flips the page | ✅ status `✓ Bunny Tasker has device admin`, CTA becomes **Next** |
| Wizard hands off to the Terms of Surrender | ✅ |
| Selecting **Sealed** shows the Device Owner note | ✅ all five paragraphs, including that it cannot be switched on later without wiping the phone |
| Consent requests the Collar's own admin | ✅ with its own explanation |
| **Declining** it reports honestly | ✅ "Consent Recorded — but not yet held" |
| With both admins active, no relock loop | ✅ Bunny Tasker still resumed 30s later |

**The bug was reproduced first, on the same rig**: install both, pair, and The
Collar bounced to the jail captioned *"BunnyTasker admin removed"* — over an
admin that had never been granted, because nothing in either onboarding ever
asked for it.


## 2d. Full walk on a clean container — RUN 2026-08-23 (evening), 97/97 ✅

Container reset with `installers/waydroid-reset.sh` (no download), all three
APKs installed fresh, **zero active admins** at the start. Everything below was
driven through the UI, not seeded.

| | |
|---|---|
| Bunny Tasker onboarding, incl. the admin gate | ✅ 7/7 — opens the system screen, carries our explanation, releases only once granted |
| Terms of Surrender handoff | ✅ |
| **Sealed** Device Owner note | ✅ all five points, incl. that it needs a wipe to add later and that factory reset always remains |
| Consent asks for the Collar's own admin | ✅ closing dialog reports a *held* cage once granted |
| Mesh created + bunny paired by invite | ✅ relay logged the join and state-mirror |
| Structural walk | ✅ **97 passed, 0 failed** |
| Veneration picker → task, reps, randcaps | ✅ |
| Balance history | ✅ `↑ $25.00 Added by the Lion → $65.00 just now` |
| E2EE round trip | ✅ `you / Kneel when you read this.` — stored as `[e2ee]` with `encrypted_key_lion`, plaintext absent from relay state |
| PWA pinning | ✅ `com.focuslock/.PinShortcutActivity` resolves for `CONFIRM_PIN_SHORTCUT` |

**One real fix came out of the run.** The first pass failed the balance-history
rows: `DENIED (auto-accepted node not confirmed by lion)`. The route copied the
penalty route's confirmation gate, which is stricter than `state-mirror` — and
state-mirror writes the actual paywall the scanners bill from, while this only
describes a movement that endpoint already asserted. Worse, being stricter meant
a freshly paired bunny's history stayed silently empty. Now aligned with
state-mirror: an invite-code member is already vouched for (the Lion handed out
the code); a node that walked in through the auto-accept window and was never
looked at still needs Confirm. Both sides tested.

**One harness fix.** `type_into` left the IME up, which compresses the layout so
a view below it reports height 2 and drops out of the dump — indistinguishable
from a control that failed to render. It cost a round of chasing a message
thread that was drawing correctly the whole time. The driver puts the keyboard
away now.

## 3. Device walk — the rows still owed

Run on the SM-S908 rig (`R5CTQA000ZL`; provisioning order in the
`project_on_device_adb_qa` notes). Install with
`adb install -r apks/focusctl-v83.apk`.

Each row is "the thing the Lion actually does", not "the button exists".

| # | Walk | Pass when |
|---|---|---|
| 3.1 | Open the app cold | Lands on **Lock**; the tab reads Lock/Rules/Money/Inbox; status bar renders |
| 3.2 | Type a message + timer, press the primary button | Bunny locks; the button relabels to `Re-lock · Nm left` within one poll |
| 3.3 | Watch the primary button while locked | Counts down; does **not** truncate at any timer value |
| 3.4 | Press 15m / 30m / 1hr / 2hr | Each locks for that long |
| 3.5 | Unlock All, then Release Device… | Both work; button returns to `Lock all devices` |
| 3.6 | **Set a writing task and lock it without leaving Rules** | The whole compose-a-lock flow lives on one screen — this is the regression that motivated the change |
| 3.7 | Open Modifiers, toggle Taunt + Mute, collapse it | Collapsed summary reads `Taunt, Mute` in gold, not `None` |
| 3.8 | Open Live Pokes; Speak and Play Audio | Both fire; the toy row appears only with a Lovense reachable |
| 3.9 | Money: +$5, Set, Clear | Balance moves; the reply shows the *new* balance, not `$0`. **Clear now asks first** — there is exactly one clear button, and it confirms |
| 3.10 | Money: Start Fine / Stop Fine | Fine status line appears and clears |
| 3.10a | **Cold-start, then go straight to Money** | Subscription shows the real tier and Payment History lists entries — *without* visiting Inbox first. This regressed once already: both widgets moved to Money while their only refresh trigger stayed on Inbox |
| 3.10b | Rules → pick **Compliment** from Mode | The compliment field appears under the mode; pick any other mode and it disappears. Locking in Compliment mode must carry the prompt, not fall through to a basic lock |
| 3.10c | Paste a message copied from a Windows editor (CRLF) into Lock message, then lock | The order lands. Before 83 the raw CR made the body invalid JSON and both the Collar and the relay rejected it |
| 3.10d | Money: tap +$50 **while standing on Money** | Payment History gains the entry without navigating away; same for Subscription after Change Subscription |
| 3.10e | Rules → Compliment, type a prompt, switch Mode to **Basic**, lock | Bunny gets a plain lock with **no** compliment gate. The Collar keys that gate off presence, not mode, so a leftover prompt would have silently gated a lock the UI showed as Basic |
| 3.10f | Long-press the Lock button, set a countdown with a pasted CRLF message | The countdown schedules. This body escaped quotes only until 83 |
| 3.11 | Inbox: send a message, pin one, mark must-reply | All three land on the Collar |
| 3.12 | Kebab → each of the 6 non-destructive entries | Each opens its dialog; **Payment Email is absent with no homelab attached, present with one** |
| 3.13 | Kebab → Release Forever | Still reachable, still confirms before doing anything |
| 3.14 | Entrap in the Rules danger block | Bordered block is visibly distinct; confirms before arming |
| 3.15 | Switch bunny slot mid-lock | The previous bunny's lock/balance is **not** painted onto the new slot |
| 3.16 | Rotate / narrow screen | 4 tab labels fit without clipping |

Capture a screenshot of each tab into `docs/Screenshots/` when done — there is
no before/after record of this app's UI newer than April.

## 4. Publish

Only after §3 is clean:

```
cp apks/focusctl-v83.apk "$HOME/Nextcloud/F-Droid Repos/F-Droid - OpenCollar/repo/"  # optional; the build script stages it
bash "$HOME/Nextcloud/F-Droid Repos/build-collar-repo.sh"
```

Then confirm the fingerprint printed is unchanged
(`C5D875B0094E06E5158788A7724B1B9753F4E71FA5ACCD9852344628B49E5D8E`) and that
the live index offers 83:

```
curl -s https://fdroid.example.com/repo/index-v1.json | python3 -c "import json,sys; print([(a['packageName'],a.get('suggestedVersionCode')) for a in json.load(sys.stdin)['apps']])"
```

The build script's APK map still names `focusctl-v82.apk`; point it at
`focusctl-v83.apk` first. Everything downstream (repo filename,
`CurrentVersionCode`) now derives from the APK itself, so that one edit is the
whole change.

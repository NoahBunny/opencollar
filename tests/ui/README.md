# UI automation — §1a direct-pair fingerprint pin

Pytest + Appium (UiAutomator2) harness exercising the Lion's Share
direct-pair flow end-to-end against a real Android surface. This is
the pilot test of the UI-automation track called out in
`docs/PUBLISHABLE-ROADMAP.md` Medium-term; §1b (QR-pair) and §4b
(mandatory-reply auto-lock) land in follow-up PRs.

The default `pytest` invocation **skips this directory** entirely.
`tests/ui/conftest.py` opts in only when `RUN_UI_TESTS=1` is set.

## What it covers

Three assertions against `MainActivity.pairDirect` in
`android/controller/src/com/focusctl/MainActivity.java`:

1. **Happy path** — correct fingerprint → `PAIRED direct (fingerprint verified): <url>`
2. **Mismatch** — wrong fingerprint → `Pair ABORTED: fingerprint mismatch. …`
   AND the bunny's `focus_lock_lion_pubkey` in `Settings.Global` is still empty
3. **Blank** — empty fingerprint → `PAIRED direct (UNVERIFIED fp=<fp>): <url>`
   (the transient "VERIFY …" message is overwritten; test polls for the terminal regex)

## Running locally

Prerequisites:

- Android emulator (API 33 recommended — see "Android 14+ FGS caveat" below)
  or a physical test phone reachable by `adb devices`
- All three APKs installed: `com.focusctl`, `com.focuslock`, `com.bunnytasker`
- Java 17 and the Android SDK (for APK builds — see `docs/BUILD.md`)
- Node.js 20+ and Appium 2

Install Appium once:

```bash
npm install -g appium@2
appium driver install uiautomator2
```

Install Python deps:

```bash
pip install -e '.[ui]'
```

Start an emulator (example — API 33 AOSP x86_64) and install APKs:

```bash
emulator -avd focuslock_api33 -no-snapshot &
adb wait-for-device
adb install -r apks/focuslock-<version>.apk
adb install -r apks/bunnytasker-<version>.apk
adb install -r apks/focusctl-<version>.apk
adb shell monkey -p com.bunnytasker -c android.intent.category.LAUNCHER 1  # seed keypair
```

Start Appium:

```bash
appium --port 4723
```

Run the tests:

```bash
RUN_UI_TESTS=1 pytest tests/ui/ -v -p no:xdist
```

`-p no:xdist` matters: the fixtures share Settings.Global state across a
session, so parallel workers race.

## Android 14+ FGS caveat

The Collar's `ControlService` declares the `FOREGROUND_SERVICE_LOCATION`
permission. On Android 14+ AVDs a runtime grant of `ACCESS_FINE_LOCATION`
is **also** required before the foreground service can start, per
`docs/MANUAL-QA.md §1`. The CI workflow (`.github/workflows/ui.yml`)
already does this:

```bash
adb shell pm grant com.focuslock android.permission.ACCESS_FINE_LOCATION
```

Target API 33 to dodge the grant entirely — that is what CI uses.

## Gotchas observed during scaffolding

- `addBunnySlot()` in `MainActivity.java` accumulates on every successful
  pair. `clean_pairing_state` in `conftest.py` does `pm clear com.focusctl`
  between tests. Do NOT replace with a lighter reset — it will mask slot
  leakage.
- Bunny Tasker generates its keypair lazily on `MainActivity.onCreate`.
  The `bunny_pubkey_b64` fixture polls `Settings.Global` for up to 10s,
  launching the companion via `monkey` if the key is empty.
- Pair-dialog **field widgets** are located by content-description
  (`pair_dlg_ip`, `pair_dlg_port`, `pair_dlg_fingerprint`). The **Pair**
  positive button is located by its visible text — its content-desc
  would be set post-`show()` and race with the Appium lookup.
- R8 minification is not currently enabled on the controller build; if
  Phase 8 (Gradle migration) lands, confirm `minifyEnabled false` or add
  a `-keepattributes` rule so content-descriptions survive.

## Extending to §1b / §4b

Each follow-up PR adds its own fixtures in `conftest.py` — do not
pre-build them here. §1b needs a test-only QR-payload injection surface
that has its own threat-model implications; §4b needs a Python-side
signed-order forging rig mirroring `PairingManager.sign`.

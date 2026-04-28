# tests/ui/ — Android UI automation scaffold (SHELVED)

Status: **Shelved twice** (uiautomator2 on 2026-04-23, Appium on 2026-04-29). Kept as a scaffold for a future operator with a dedicated test bench. CI stays green because every test in this directory is skipped without `UI_TESTS=1`.

## Why this is hard

The Collar / Lion's Share / Bunny Tasker pairing flows are gated by on-device UI taps (consent screen, QR scanner, fingerprint-pin confirmation). The audit-2026-04-27 plan flagged this as a Stream C item but anticipated friction.

Two concrete failure modes are documented below so a future revisit knows what to expect.

### 1. uiautomator2 wedge (2026-04-23)

`uiautomator2.connect()` hangs at `_setup_jar` → `toybox md5sum` against Waydroid's busybox-style `toybox` binary. The hang wedges Waydroid's adbd; recovery requires:

```bash
waydroid session stop
sudo systemctl restart waydroid-container
```

That's a ~5-10 minute downtime per wedge, which is incompatible with running this on every commit.

### 2. Appium spike not feasible from this dev box (2026-04-29)

Audit recommended Appium since it uses Android accessibility APIs instead of the md5sum side-channel uiautomator2 relies on. Round-5 of Stream C was time-boxed to ≤60 minutes. On the dev box at `/home/livv/...` we couldn't run live:

- **Waydroid session was STOPPED** — re-bringing it up takes ~12s but the wedge-recovery cost (5-10min) if Appium also failed would have blown the budget.
- **Appium server not installed** — requires `npm install -g appium` (system-level Node + npm), plus `Appium-Python-Client` Python package, plus the Appium UiAutomator2 driver (`appium driver install uiautomator2`). None were available.
- **Appium UiAutomator2 driver uses the same Android-side shim** that wedged the prior spike. The driver is structurally cleaner (WebDriver wire protocol) but the on-device server it bootstraps may have the same Waydroid-incompatibility footprint.

## Recommended setup recipe (for a future operator)

If you have a dedicated test bench (a real Android device, or a Waydroid host you don't mind nuking), here's the full bring-up:

### 1. Install Appium server + driver

```bash
# Node 24+ recommended
npm install -g appium

# Add the UiAutomator2 driver (Appium 2.x style)
appium driver install uiautomator2

# Verify
appium driver list
appium --version
```

### 2. Install the Python client

```bash
.venv/bin/pip install Appium-Python-Client
```

(Or add it to `pyproject.toml [project.optional-dependencies] dev` if you want it tracked.)

### 3. Start Appium server

```bash
appium --base-path /wd/hub  # default port 4723
```

### 4. Start a real device or Waydroid session

```bash
# Real device
adb devices  # confirm one shows as 'device'

# OR Waydroid
waydroid session start
adb connect 192.168.240.112:5555  # confirm Waydroid IP
```

### 5. Replace uiautomator2 with Appium in `tests/ui/conftest.py`

Replace the `ui_device` fixture's `import uiautomator2 as u2 ; u2.connect()` block with:

```python
from appium import webdriver
from appium.options.android import UiAutomator2Options

opts = (
    UiAutomator2Options()
    .set_capability("platformName", "Android")
    .set_capability("automationName", "UiAutomator2")
    .set_capability("deviceName", "Waydroid")
    .set_capability("app", str(SLAVE_APK))
    .set_capability("appPackage", SLAVE_PKG)
    .set_capability("appActivity", ".ConsentActivity")
    .set_capability("noReset", "true")  # don't wipe between runs
)
return webdriver.Remote("http://127.0.0.1:4723/wd/hub", options=opts)
```

### 6. Validate against 10 consecutive runs

```bash
for i in 1 2 3 4 5 6 7 8 9 10; do
    UI_TESTS=1 .venv/bin/pytest tests/ui/ -x || break
done
```

If 10/10 pass without a Waydroid wedge, document the setup in this README + un-shelve in `docs/PUBLISHABLE-ROADMAP.md` + remove the shelved markers in `tests/ui/conftest.py`.

## What this scaffold currently provides

The APK install + consent-bypass + foreground-service-start logic in `tests/ui/conftest.py` is correct as of the v1.2.0 Collar. The gap is the UI driver, not the Android-side setup. A future revisit can keep:

- `_adb()` / `_adb_device_count()` helpers
- `ui_env` fixture's APK install loop
- `focus_lock_consented=1` settings.put pattern
- `pm grant` runtime-permission granting
- `am start-foreground-service` Collar bring-up
- `_wait_for_collar_port` readiness check on `127.0.0.1:8432/api/health`

And replace just the `ui_device` fixture's UI-driver connection.

## See also

- `docs/PUBLISHABLE-ROADMAP.md §Medium-term` — UI automation deferral rationale.
- `docs/AUDIT-PLAN.md §Stream C` — original Stream C scope including Android UI automation.
- `docs/QA-pairing.md` — manual pairing QA (the alternative until UI automation is unwedged).

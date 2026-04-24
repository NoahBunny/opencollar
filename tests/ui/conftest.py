"""Shared fixtures for the UI pair spike (direct/LAN pair on Waydroid).

SHELVED 2026-04-23. Kept as a scaffold, not a passing test suite.

Spike outcome: `uiautomator2.connect()` hangs at `_setup_jar` →
`toybox md5sum` against Waydroid and wedges the Android-side adbd, which
then needs a full `waydroid session stop` + `sudo systemctl restart
waydroid-container` to recover. See `docs/PUBLISHABLE-ROADMAP.md §Medium-term`
for the full go/no-go reasoning. If someone revisits this, the APK install
+ consent-bypass + service-start logic below is still correct as of the
v1.2.0 Collar; the gap is the UI driver, not the Android-side setup.

Runs opt-in: `UI_TESTS=1 pytest tests/ui/`. Without `UI_TESTS` or without
an adb device, every test is skipped — CI stays green.
"""

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
SLAVE_APK = REPO_ROOT / "android" / "slave" / "focuslock-signed.apk"
COMPANION_APK = REPO_ROOT / "android" / "companion" / "bunnytasker-signed.apk"
CONTROLLER_APK = REPO_ROOT / "android" / "controller" / "focusctl-signed.apk"

SLAVE_PKG = "com.focuslock"
COMPANION_PKG = "com.bunnytasker"
CONTROLLER_PKG = "com.focusctl"

UI_TESTS_ENABLED = os.environ.get("UI_TESTS", "").lower() in {"1", "true", "yes"}


def _adb(*args, check=True, timeout=60):
    return subprocess.run(["adb", *args], check=check, capture_output=True, text=True, timeout=timeout)


def _adb_device_count() -> int:
    if shutil.which("adb") is None:
        return 0
    try:
        out = _adb("devices", check=False).stdout
    except subprocess.TimeoutExpired:
        return 0
    return sum(1 for line in out.splitlines()[1:] if line.strip().endswith("device"))


@pytest.fixture(scope="session")
def ui_device():
    if not UI_TESTS_ENABLED:
        pytest.skip("UI_TESTS not set — skipping UI spike")
    if _adb_device_count() == 0:
        pytest.skip("no adb device connected (Waydroid session not up?)")
    import uiautomator2 as u2

    return u2.connect()


@pytest.fixture(scope="session")
def ui_env(ui_device):
    """Install APKs, bypass consent, start the Collar's HTTP server."""
    for apk in (SLAVE_APK, COMPANION_APK, CONTROLLER_APK):
        if not apk.exists():
            pytest.skip(f"missing APK: {apk}")
        _adb("install", "-r", "-t", str(apk))

    # Bypass Terms of Surrender UI — the spike tests pair, not consent.
    _adb("shell", "settings", "put", "global", "focus_lock_consented", "1")

    # Runtime perms the Collar's foreground service needs on Android 14+.
    for perm in (
        "android.permission.ACCESS_FINE_LOCATION",
        "android.permission.ACCESS_COARSE_LOCATION",
        "android.permission.POST_NOTIFICATIONS",
    ):
        _adb("shell", "pm", "grant", SLAVE_PKG, perm, check=False)

    # Ensure a fresh pair state — clear Bunny's stored lion_pubkey so the
    # Collar accepts a new pair POST rather than returning already-paired.
    _adb("shell", "settings", "delete", "global", "focus_lock_lion_pubkey", check=False)

    _adb("shell", "am", "start-foreground-service", f"{SLAVE_PKG}/.ControlService")
    _wait_for_collar_port(timeout=30)
    return ui_device


def _wait_for_collar_port(timeout=30):
    deadline = time.time() + timeout
    last_err = ""
    while time.time() < deadline:
        r = _adb(
            "shell",
            "curl",
            "-sS",
            "-o",
            "/dev/null",
            "-w",
            "%{http_code}",
            "http://127.0.0.1:8432/api/health",
            check=False,
            timeout=10,
        )
        code = r.stdout.strip()
        if code and code != "000":
            return
        last_err = r.stderr.strip() or f"code={code!r}"
        time.sleep(1)
    raise RuntimeError(f"Collar 8432 never responded: {last_err}")

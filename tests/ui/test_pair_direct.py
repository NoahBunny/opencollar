"""UI spike: direct/LAN pair end-to-end on a single Waydroid instance.

SHELVED — see `tests/ui/conftest.py` module docstring and
`docs/PUBLISHABLE-ROADMAP.md §Medium-term`. The test body below is left
in place as documentation of the intended flow, not as a passing test.

Scope (as-designed): Lion's Share + Bunny Tasker + Collar all installed on
one Waydroid instance. "LAN" reduces to 127.0.0.1. Dual-device fingerprint
comparison and camera-QR scanning are out of scope by construction.

Run with:
    UI_TESTS=1 .venv/bin/pytest tests/ui/ -v
"""

import base64
import hashlib
import subprocess
import time

import pytest


def _bunny_fingerprint_from_prefs() -> str:
    """Read the stored bunny pubkey and derive its 16-hex fingerprint.

    Mirrors PairingManager.getFingerprint — SHA-256(pubkey_bytes) first 8 bytes.
    This is a fallback for when the pairing_fingerprint TextView is still
    initializing; the pair flow will accept either source.
    """
    r = subprocess.run(
        ["adb", "shell", "settings", "get", "global", "focus_lock_bunny_pubkey"],
        capture_output=True,
        text=True,
        timeout=10,
    )
    pub_b64 = r.stdout.strip()
    if not pub_b64 or pub_b64 == "null":
        return ""
    try:
        digest = hashlib.sha256(base64.b64decode(pub_b64)).digest()
    except Exception:
        return ""
    return digest[:8].hex()


@pytest.mark.ui
def test_direct_pair_happy_path(ui_env):
    d = ui_env

    d.app_start("com.bunnytasker", wait=True)
    fp_view = d(resourceId="com.bunnytasker:id/pairing_fingerprint")
    fingerprint = ""
    if fp_view.exists(timeout=5):
        fingerprint = (fp_view.get_text() or "").strip().lower().replace(" ", "")
    if len(fingerprint) != 16:
        fingerprint = _bunny_fingerprint_from_prefs()
    assert len(fingerprint) == 16, f"bad fingerprint: {fingerprint!r}"

    d.app_start("com.focusctl", wait=True)
    d(resourceId="com.focusctl:id/btn_setup").click(timeout=10)
    d(text="Pair Direct (LAN)").click(timeout=10)

    # The dialog has three EditTexts: IP, port (default 8432), fingerprint.
    edits = d(className="android.widget.EditText")
    assert edits.count >= 3, f"expected 3 EditTexts in pair dialog, saw {edits.count}"
    edits[0].set_text("127.0.0.1")
    edits[2].set_text(fingerprint)

    d(text="Pair").click(timeout=10)

    status = d(resourceId="com.focusctl:id/status")
    deadline = time.time() + 30
    last = ""
    while time.time() < deadline:
        if status.exists():
            last = (status.get_text() or "").strip()
            up = last.upper()
            if "PAIRED" in up and "UNPAIRED" not in up:
                return
            if "ABORTED" in up or "FAILED" in up:
                pytest.fail(f"pair failed: {last}")
        time.sleep(1)
    pytest.fail(f"pair did not finish in 30s; last status: {last!r}")

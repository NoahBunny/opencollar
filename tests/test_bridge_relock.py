"""Bridge safeword guard — the terminal-release "matters most" invariant.

Wraps tests/bridge_relock_test.sh (a hermetic bash unit test that sources the
real focuslock-bridge.sh and drives poll_device with a mocked adb_dev) so the
guarantee runs inside the normal `pytest tests/` suite / CI.

The invariant: once focus_lock_released=1 (safeword or Release Forever), the
homelab bridge must never re-lock the device — a safeword the bridge could
override would not be a safeword. See docs/THREAT-MODEL.md.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "tests" / "bridge_relock_test.sh"


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")
def test_bridge_honors_safeword_release():
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, f"bridge safeword re-lock guard failed:\n{result.stdout}\n{result.stderr}"
    # Sanity: the control case must have exercised a real re-lock, else the
    # test would trivially pass by never enforcing anything.
    assert "control:" in result.stdout and "bridge re-locks" in result.stdout

"""The relay installer must not hand out world-writable state directories.

`/webhook/controller-register` is admin-token-gated (audit 2026-04-27 M-2)
because the address it records is what `GET /controller` hands back as the
Lion's controller — an unauth caller who could write it would be redirecting
controller resolution to an address of their choosing. A world-writable
`/run/focuslock` reopens that from the filesystem side: any local account on the
relay writes `controller.json` directly and never touches the gated route.

The 0777 was inherited from `install-desktop-collar.sh`, where it is correct —
that collar runs as the logged-in user and must write there. Both relay units
run as root (no `User=`), so the write bit buys nothing. This file guards the
relay installer only; the desktop one is deliberately out of scope.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
RELAY_INSTALLER = REPO_ROOT / "installers" / "homelab-install.sh"


@pytest.fixture(scope="module")
def relay_installer_text():
    assert RELAY_INSTALLER.is_file(), f"missing {RELAY_INSTALLER}"
    return RELAY_INSTALLER.read_text()


class TestRelayInstallerPermissions:
    def test_no_world_writable_chmod_anywhere(self, relay_installer_text):
        offenders = [
            line.strip()
            for line in relay_installer_text.splitlines()
            if re.search(r"^\s*[^#]*\bchmod\s+(0?777)\b", line)
        ]
        assert not offenders, "relay installer grants world-write; both relay units run as root:\n  " + "\n  ".join(
            offenders
        )

    def test_no_world_writable_tmpfiles_rule(self, relay_installer_text):
        """The tmpfiles.d rule outlives the chmod — it re-applies every boot,
        so fixing the chmod alone would silently revert on the next reboot."""
        rules = re.findall(r"^\s*echo\s+\"(d\s+/run/focuslock\s+\S+.*)\"", relay_installer_text, re.M)
        assert rules, "expected a tmpfiles.d rule for /run/focuslock"
        for rule in rules:
            mode = rule.split()[2]
            assert mode not in ("0777", "777"), f"world-writable tmpfiles rule: {rule}"

    def test_run_dir_is_created_0755(self, relay_installer_text):
        assert re.search(r"chmod\s+0?755\s+/run/focuslock", relay_installer_text), (
            "expected /run/focuslock to be created 0755 on the relay"
        )

    def test_durable_state_dir_stays_private(self, relay_installer_text):
        """/var/lib/focuslock holds the mesh accounts and vault node lists —
        the only record of who is on a mesh. It was already 700; pin it."""
        assert re.search(r"chmod\s+0?700\s+/var/lib/focuslock", relay_installer_text), (
            "expected /var/lib/focuslock to stay 700"
        )

import os
import sys
import tempfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHARED_DIR = REPO_ROOT / "shared"
for p in (SHARED_DIR, REPO_ROOT):
    sp = str(p)
    if sp not in sys.path:
        sys.path.insert(0, sp)

# Redirect focuslock-mail.py's state directory to a per-session tmpdir.
# Without this, importing focuslock-mail triggers os.makedirs("/run/focuslock/...")
# at module-load time — fine on prod (systemd tmpfiles.d owns /run/focuslock)
# but a PermissionError on CI and dev machines. Set BEFORE any test module
# imports focuslock-mail.
_STATE_TMPDIR = tempfile.mkdtemp(prefix="focuslock-test-state-")
os.environ.setdefault("FOCUSLOCK_STATE_DIR", _STATE_TMPDIR)


@pytest.fixture(scope="session")
def lion_keypair():
    from focuslock_vault import generate_keypair

    priv, pub, der = generate_keypair()
    return {"priv_pem": priv, "pub_pem": pub, "pub_der": der}


@pytest.fixture(scope="session")
def slave_keypair():
    from focuslock_vault import generate_keypair

    priv, pub, der = generate_keypair()
    return {"priv_pem": priv, "pub_pem": pub, "pub_der": der}


@pytest.fixture(scope="session")
def desktop_keypair():
    from focuslock_vault import generate_keypair

    priv, pub, der = generate_keypair()
    return {"priv_pem": priv, "pub_pem": pub, "pub_der": der}


@pytest.fixture
def sample_order():
    return {
        "action": "lock",
        "params": {"minutes": 30, "reason": "testing"},
    }

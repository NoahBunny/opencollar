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


def pytest_collection_modifyitems(session, config, items):
    """Refuse to run tests whose bytecode was compiled in another checkout.

    This repo is mirrored into ``~/Nextcloud`` (a different repo, on a different
    branch) and the sync carries ``__pycache__`` along with the sources. A
    ``.pyc`` records the *compiling* checkout's absolute path in ``co_filename``,
    and Python revalidates it against the local source's mtime+size only — which
    a file-level sync preserves. The result is a run that collects our files but
    reports every skip location and traceback against the mirror.

    Audited 2026-08-17: 99 of 130 in-tree ``.pyc`` carried the mirror's path.
    None was executing different code (the sources matched byte for byte), so
    this guards a live trap rather than a past failure. The Makefile keeps
    bytecode outside the tree; this catches a bare ``pytest`` run that skips it.

    Escape hatch: set ``FOCUSLOCK_ALLOW_FOREIGN_PYC=1`` to downgrade to a warning.
    """
    foreign: dict[str, int] = {}
    for item in items:
        code = getattr(getattr(item, "function", None), "__code__", None)
        if code is None:
            continue
        origin = Path(code.co_filename)
        if origin.is_absolute() and not origin.is_relative_to(REPO_ROOT):
            foreign[str(origin)] = foreign.get(str(origin), 0) + 1

    if not foreign:
        return

    detail = "\n".join(
        f"    {path}  ({count} test{'s' if count != 1 else ''})"
        for path, count in sorted(foreign.items())
    )
    message = (
        "test bytecode was compiled in a different checkout than the one under test.\n"
        f"  this checkout: {REPO_ROOT}\n"
        "  bytecode from:\n"
        f"{detail}\n"
        "  Reported line numbers and tracebacks would point at the wrong repo.\n"
        "  Fix: find . -name __pycache__ -type d -not -path './.venv/*' -exec rm -rf {} +\n"
        "  Prevent: export PYTHONPYCACHEPREFIX=$HOME/.cache/focuslock/pycache (the Makefile does this)\n"
        "  Override: FOCUSLOCK_ALLOW_FOREIGN_PYC=1"
    )
    if os.environ.get("FOCUSLOCK_ALLOW_FOREIGN_PYC") == "1":
        config.issue_config_time_warning(UserWarning(message), stacklevel=1)
        return
    raise pytest.UsageError(message)

"""The log sanitizer, and the invariant that user data never reaches a log record raw.

``focuslock-mail.py`` and ``focuslock_mesh.py`` each carry their own copy of the
escaper. That duplication is deliberate — the relay's ``_sanitize_log`` already
guards 143 call sites and rerouting it through an import to save three lines
risks all of them — but duplication drifts, so it is asserted here instead.
"""

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail():
    spec = importlib.util.spec_from_file_location("focuslock_mail_logsan", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def mesh():
    import focuslock_mesh

    return focuslock_mesh


FORGERY = "ok\nWARNING:root:Balance cleared by the Lion"

CASES = [
    FORGERY,
    "plain",
    "",
    None,
    0,
    42,
    "carriage\rreturn",
    "nul\x00byte",
    "all\r\n\x00three",
    "\n\n\n",
    b"bytes".decode(),
]


@pytest.mark.parametrize("value", CASES)
def test_both_implementations_agree(mail, mesh, value):
    """The two copies must not drift."""
    assert mail._sanitize_log(value) == mesh.sanitize_log(value)


@pytest.mark.parametrize("value", CASES)
def test_no_line_break_survives(mail, value):
    """A sanitized value can never end a log line, so it can never start a fake one."""
    out = mail._sanitize_log(value)
    assert "\n" not in out
    assert "\r" not in out
    assert "\x00" not in out


def test_forged_entry_is_defanged(mail):
    """The concrete attack: a value that would otherwise write its own log line."""
    out = mail._sanitize_log(FORGERY)
    assert out == "ok\\nWARNING:root:Balance cleared by the Lion"
    assert len(out.splitlines()) == 1


def test_none_is_distinguishable_from_the_string_none(mail):
    assert mail._sanitize_log(None) == "<none>"
    assert mail._sanitize_log("None") == "None"


# Deliberately NOT tested here: "every user-controlled value reaches the log
# through a sanitizer". That invariant needs taint analysis to state — a purely
# syntactic version flags 165 server-side values (config constants, counts,
# internal result dicts) that are not attacker-reachable. CodeQL's
# py/log-injection does it properly and runs on every pull request; duplicating
# it badly here would mean an allowlist that rots. See .github/workflows/codeql.yml.

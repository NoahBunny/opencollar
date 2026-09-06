"""Per-mesh state files must stay inside the directory that owns them.

``_mesh_state_path`` is the single choke point every per-mesh file goes through:
orders, desktop registries, tamper counters, payment ledgers, payment
identities, message stores, mesh accounts and vault directories. It carries two
independent locks — the mesh-id character whitelist, and a containment check on
the resolved path — and this pins both, because either one alone is a defence
that can be quietly loosened later without anything noticing.
"""

import importlib.util
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail():
    spec = importlib.util.spec_from_file_location("focuslock_mail_paths", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


TRAVERSALS = [
    "../etc/passwd",
    "..",
    ".",
    "../../root/.ssh/authorized_keys",
    "a/../../b",
    "sub/dir",
    "/etc/passwd",
    "/",
    "\\..\\windows",
    "mesh/../../escape",
    "..%2f..%2fetc",
    "with space",
    "dot.dot",
    "trailing/",
    "\x00nul",
    "new\nline",
    "",
    None,
    "x" * 65,
]


@pytest.mark.parametrize("mesh_id", TRAVERSALS)
def test_unsafe_ids_get_no_path(mail, tmp_path, mesh_id):
    assert mail._mesh_state_path(str(tmp_path), mesh_id) is None
    assert mail._mesh_state_path(str(tmp_path), mesh_id, "") is None


VALID = ["abc123", "eMv8tP9KJL0D", "with-hyphen", "with_underscore", "A" * 64, "0"]


@pytest.mark.parametrize("mesh_id", VALID)
def test_valid_ids_stay_inside_the_base(mail, tmp_path, mesh_id):
    base = os.path.realpath(str(tmp_path))
    got = mail._mesh_state_path(str(tmp_path), mesh_id)
    assert got is not None
    assert got == os.path.join(base, f"{mesh_id}.json")
    assert got.startswith(base + os.sep)


def test_directory_form_omits_the_suffix(mail, tmp_path):
    base = os.path.realpath(str(tmp_path))
    assert mail._mesh_state_path(str(tmp_path), "abc", "") == os.path.join(base, "abc")


def test_containment_holds_when_the_base_is_a_symlink(mail, tmp_path):
    """State dirs behind a symlink resolve to the same file, not to a rejection."""
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real)
    got = mail._mesh_state_path(str(link), "abc")
    assert got == os.path.join(os.path.realpath(str(real)), "abc.json")


def test_hyphen_and_underscore_are_accepted_but_dot_is_not(mail, tmp_path):
    """The whitelist's exact shape — base64url ids need - and _, and a dot is
    the one character that would make traversal expressible."""
    assert mail._mesh_state_path(str(tmp_path), "a-b_c") is not None
    assert mail._mesh_state_path(str(tmp_path), "a.b") is None


@pytest.mark.parametrize(
    "getter",
    ["_get_desktop_registry", "_get_payment_ledger", "_get_payment_identity", "_get_message_store"],
)
def test_store_getters_refuse_unsafe_ids(mail, getter):
    """These four built a path from mesh_id with no local validation at all —
    they took the caller's word that the route had checked it."""
    fn = getattr(mail, getter, None)
    if fn is None:
        pytest.skip(f"{getter} not present")
    with pytest.raises(ValueError):
        fn("../../escape")


def test_tamper_counter_path_refuses_unsafe_ids(mail):
    assert mail._tamper_counter_path("../../escape") is None
    assert mail._tamper_counter_path("goodmesh") is not None

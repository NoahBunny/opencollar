"""Unit tests for focuslock-mail.py MeshAccountStore + VaultStore class internals.

These two stateful, persisted-to-disk classes govern multi-tenant correctness:

- `MeshAccountStore` — per-mesh account records: lion_pubkey, auth_token,
  invite code, vault_only flag, rate limits, joined nodes.
- `VaultStore`     — opaque encrypted blob storage + node approval/rejection
  registry per mesh.

Each test gets a fresh tempdir so default `_STATE_DIR`-rooted state is never
mutated.
"""

import importlib.util
import json
import os
import sys
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIL_PATH = REPO_ROOT / "focuslock-mail.py"


@pytest.fixture(scope="module")
def mail_module():
    spec = importlib.util.spec_from_file_location("focuslock_mail_stores", str(MAIL_PATH))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["focuslock_mail_stores"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def fresh_account_store(mail_module):
    """A MeshAccountStore rooted in a temporary directory."""
    with tempfile.TemporaryDirectory() as td:
        yield mail_module.MeshAccountStore(persist_dir=td)


@pytest.fixture
def fresh_vault_store(mail_module):
    """A VaultStore rooted in a temporary directory."""
    with tempfile.TemporaryDirectory() as td:
        yield mail_module.VaultStore(base_dir=td)


# ──────────────────────── MeshAccountStore ────────────────────────


class TestMeshAccountStoreInit:
    def test_creates_persist_dir(self, mail_module):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "missing_subdir")
            store = mail_module.MeshAccountStore(persist_dir=target)
            assert os.path.isdir(target)
            assert store.meshes == {}

    def test_default_persist_dir_under_state_dir(self, mail_module):
        # When persist_dir=None, store creates _STATE_DIR/meshes; we just
        # need it to not crash and to land somewhere writable.
        store = mail_module.MeshAccountStore()
        assert os.path.isdir(store.persist_dir)
        assert store.persist_dir.endswith(os.path.join("meshes"))

    def test_load_all_reads_existing_files(self, mail_module):
        with tempfile.TemporaryDirectory() as td:
            sample = {
                "mesh_id": "abc",
                "lion_pubkey": "PUB",
                "auth_token": "TOK",
                "invite_code": "WOLF-42-LARK",
                "nodes": {},
            }
            with open(os.path.join(td, "abc.json"), "w") as f:
                json.dump(sample, f)
            # A non-json file should be ignored
            with open(os.path.join(td, "ignored.txt"), "w") as f:
                f.write("noise")
            store = mail_module.MeshAccountStore(persist_dir=td)
            assert "abc" in store.meshes
            assert store.meshes["abc"]["auth_token"] == "TOK"

    def test_load_all_swallows_corrupt_json(self, mail_module):
        with tempfile.TemporaryDirectory() as td:
            with open(os.path.join(td, "broken.json"), "w") as f:
                f.write("{not valid json")
            # Should not raise — _load_all wraps the loop in try/except
            store = mail_module.MeshAccountStore(persist_dir=td)
            assert store.meshes == {}


class TestMeshAccountStoreCreate:
    def test_create_returns_account_with_required_fields(self, fresh_account_store):
        acc = fresh_account_store.create("LION_PUBKEY")
        assert acc["lion_pubkey"] == "LION_PUBKEY"
        for key in (
            "mesh_id",
            "auth_token",
            "invite_code",
            "invite_expires_at",
            "invite_uses",
            "pin",
            "created_at",
            "nodes",
            "vault_only",
            "max_blobs_per_day",
            "max_total_bytes_mb",
        ):
            assert key in acc
        assert acc["vault_only"] is False
        assert acc["nodes"] == {}
        assert acc["invite_uses"] == 0

    def test_create_persists_to_disk(self, fresh_account_store):
        acc = fresh_account_store.create("PUBKEY")
        path = os.path.join(fresh_account_store.persist_dir, f"{acc['mesh_id']}.json")
        assert os.path.exists(path)
        with open(path) as f:
            on_disk = json.load(f)
        assert on_disk["mesh_id"] == acc["mesh_id"]

    def test_create_generates_4digit_pin_if_omitted(self, fresh_account_store):
        acc = fresh_account_store.create("PUBKEY")
        assert acc["pin"].isdigit()
        assert 1000 <= int(acc["pin"]) <= 9999

    def test_create_accepts_explicit_pin(self, fresh_account_store):
        acc = fresh_account_store.create("PUBKEY", pin="0042")
        assert acc["pin"] == "0042"

    def test_create_invite_code_format(self, fresh_account_store, mail_module):
        acc = fresh_account_store.create("PUBKEY")
        parts = acc["invite_code"].split("-")
        assert len(parts) == 3
        assert parts[0] in mail_module._INVITE_WORDS
        assert parts[2] in mail_module._INVITE_WORDS
        assert parts[1].isdigit()
        assert 10 <= int(parts[1]) <= 99

    def test_create_records_ip_for_rate_limit(self, fresh_account_store):
        fresh_account_store.create("PUB", client_ip="1.2.3.4")
        assert "1.2.3.4" in fresh_account_store._create_rate
        assert len(fresh_account_store._create_rate["1.2.3.4"]) == 1

    def test_create_unique_mesh_ids(self, fresh_account_store):
        ids = {fresh_account_store.create("P").get("mesh_id") for _ in range(5)}
        assert len(ids) == 5


class TestMeshAccountStoreRateLimit:
    def test_under_limit_returns_true(self, fresh_account_store):
        assert fresh_account_store.check_rate_limit("ip1") is True

    def test_records_then_blocks_at_max(self, fresh_account_store):
        for _ in range(fresh_account_store.RATE_LIMIT_MAX):
            fresh_account_store._record_create("ip1")
        assert fresh_account_store.check_rate_limit("ip1") is False

    def test_expired_timestamps_pruned(self, fresh_account_store):
        old = time.time() - fresh_account_store.RATE_LIMIT_WINDOW_S - 100
        fresh_account_store._create_rate["ip1"] = [old, old, old]
        assert fresh_account_store.check_rate_limit("ip1") is True
        # check_rate_limit prunes in-place
        assert fresh_account_store._create_rate["ip1"] == []


class TestMeshAccountStoreJoin:
    def test_invalid_invite_code(self, fresh_account_store):
        acc, err = fresh_account_store.join("BOGUS-99-FAKE", "phone", "slave")
        assert acc is None
        assert err == "invalid invite code"

    def test_join_success_adds_node(self, fresh_account_store):
        created = fresh_account_store.create("PUB")
        joined, err = fresh_account_store.join(created["invite_code"], "phone1", "slave", bunny_pubkey="BUNNY")
        assert err is None
        assert joined["mesh_id"] == created["mesh_id"]
        assert "phone1" in joined["nodes"]
        assert joined["nodes"]["phone1"]["type"] == "slave"
        assert joined["nodes"]["phone1"]["bunny_pubkey"] == "BUNNY"
        assert joined["invite_uses"] == 1

    def test_join_is_case_insensitive(self, fresh_account_store):
        created = fresh_account_store.create("PUB")
        # mixed case + whitespace
        code = f"   {created['invite_code'].lower()}  "
        joined, err = fresh_account_store.join(code, "n1", "slave")
        assert err is None
        assert joined is not None

    def test_join_reusable_within_ttl(self, fresh_account_store):
        created = fresh_account_store.create("PUB")
        fresh_account_store.join(created["invite_code"], "n1", "slave")
        joined2, err = fresh_account_store.join(created["invite_code"], "n2", "slave")
        assert err is None
        assert joined2["invite_uses"] == 2
        assert "n1" in joined2["nodes"] and "n2" in joined2["nodes"]

    def test_join_expired_invite(self, fresh_account_store):
        created = fresh_account_store.create("PUB")
        # Force expiry
        fresh_account_store.meshes[created["mesh_id"]]["invite_expires_at"] = int(time.time()) - 10
        acc, err = fresh_account_store.join(created["invite_code"], "n1", "slave")
        assert acc is None
        assert err == "invite code expired"


class TestMeshAccountStoreLookups:
    def test_get_existing(self, fresh_account_store):
        a = fresh_account_store.create("PUB")
        assert fresh_account_store.get(a["mesh_id"])["mesh_id"] == a["mesh_id"]

    def test_get_missing_returns_none(self, fresh_account_store):
        assert fresh_account_store.get("nope") is None

    def test_validate_auth_correct(self, fresh_account_store):
        a = fresh_account_store.create("PUB")
        assert fresh_account_store.validate_auth(a["mesh_id"], a["auth_token"]) is True

    def test_validate_auth_wrong_token(self, fresh_account_store):
        a = fresh_account_store.create("PUB")
        assert fresh_account_store.validate_auth(a["mesh_id"], "different") is False

    def test_validate_auth_unknown_mesh(self, fresh_account_store):
        assert fresh_account_store.validate_auth("ghost", "tok") is False

    def test_list_mesh_ids_snapshot(self, fresh_account_store):
        a = fresh_account_store.create("P1")
        b = fresh_account_store.create("P2")
        ids = fresh_account_store.list_mesh_ids()
        assert set(ids) >= {a["mesh_id"], b["mesh_id"]}
        # Must be a list (snapshot), not the live dict
        assert isinstance(ids, list)


class TestMeshAccountStoreUpdateNode:
    def test_update_existing_node(self, fresh_account_store):
        acc = fresh_account_store.create("P")
        fresh_account_store.join(acc["invite_code"], "n1", "slave")
        fresh_account_store.update_node(acc["mesh_id"], "n1", custom_field="x")
        node = fresh_account_store.meshes[acc["mesh_id"]]["nodes"]["n1"]
        assert node["custom_field"] == "x"

    def test_update_missing_node_silent(self, fresh_account_store):
        acc = fresh_account_store.create("P")
        # Should not raise
        fresh_account_store.update_node(acc["mesh_id"], "ghost", x=1)
        assert "ghost" not in fresh_account_store.meshes[acc["mesh_id"]]["nodes"]

    def test_update_missing_mesh_silent(self, fresh_account_store):
        fresh_account_store.update_node("no-such-mesh", "n1", x=1)


class TestMeshAccountStoreVaultOnly:
    def test_default_false(self, fresh_account_store):
        a = fresh_account_store.create("P")
        assert fresh_account_store.is_vault_only(a["mesh_id"]) is False

    def test_set_true_then_false(self, fresh_account_store):
        a = fresh_account_store.create("P")
        assert fresh_account_store.set_vault_only(a["mesh_id"], True) is True
        assert fresh_account_store.is_vault_only(a["mesh_id"]) is True
        assert fresh_account_store.set_vault_only(a["mesh_id"], False) is True
        assert fresh_account_store.is_vault_only(a["mesh_id"]) is False

    def test_set_unknown_mesh(self, fresh_account_store):
        assert fresh_account_store.set_vault_only("ghost", True) is False

    def test_is_vault_only_unknown(self, fresh_account_store):
        assert fresh_account_store.is_vault_only("ghost") is False


class TestMeshAccountStoreInternals:
    def test_save_noop_for_unknown_mesh(self, fresh_account_store):
        # Should not raise, should not write a file
        fresh_account_store._save("does-not-exist")
        assert not os.listdir(fresh_account_store.persist_dir)

    def test_find_by_invite_returns_none_for_missing(self, fresh_account_store):
        assert fresh_account_store._find_by_invite("MISSING-00-CODE") is None

    def test_gen_invite_code_components(self, fresh_account_store, mail_module):
        for _ in range(20):
            code = fresh_account_store._gen_invite_code()
            w1, num, w2 = code.split("-")
            assert w1 in mail_module._INVITE_WORDS
            assert w2 in mail_module._INVITE_WORDS
            assert 10 <= int(num) <= 99


# ──────────────────────── VaultStore ────────────────────────


class TestVaultStoreInit:
    def test_creates_base_dir(self, mail_module):
        with tempfile.TemporaryDirectory() as td:
            target = os.path.join(td, "vault_root")
            store = mail_module.VaultStore(base_dir=target)
            assert os.path.isdir(target)
            assert store.base_dir == target

    def test_default_base_dir(self, mail_module):
        store = mail_module.VaultStore()
        assert os.path.isdir(store.base_dir)
        assert store.base_dir.endswith(os.path.join("vaults"))


class TestVaultStoreMeshDir:
    def test_invalid_mesh_id_returns_none(self, fresh_vault_store):
        assert fresh_vault_store._mesh_dir("../etc") is None
        assert fresh_vault_store._mesh_dir("with space") is None
        assert fresh_vault_store._mesh_dir("") is None

    def test_valid_mesh_id_returns_path(self, fresh_vault_store):
        path = fresh_vault_store._mesh_dir("good_mesh-id123")
        assert path is not None
        assert path.endswith("good_mesh-id123")

    def test_ensure_mesh_creates_blobs_subdir(self, fresh_vault_store):
        d = fresh_vault_store._ensure_mesh("mesh1")
        assert d is not None
        assert os.path.isdir(os.path.join(d, "blobs"))

    def test_ensure_mesh_invalid_returns_none(self, fresh_vault_store):
        assert fresh_vault_store._ensure_mesh("../bad") is None


class TestVaultStoreVersions:
    def test_list_versions_empty_for_new_mesh(self, fresh_vault_store):
        assert fresh_vault_store._list_blob_versions("nope") == []

    def test_list_versions_invalid_mesh(self, fresh_vault_store):
        assert fresh_vault_store._list_blob_versions("../bad") == []

    def test_list_versions_ignores_nonjson_and_invalid(self, fresh_vault_store):
        d = fresh_vault_store._ensure_mesh("m1")
        blobs_dir = os.path.join(d, "blobs")
        # Mix valid + invalid filenames
        for name in ("00000001.json", "00000007.json", "junk.json", "notes.txt"):
            with open(os.path.join(blobs_dir, name), "w") as f:
                f.write("{}")
        versions = fresh_vault_store._list_blob_versions("m1")
        assert versions == [1, 7]

    def test_current_version_zero_when_empty(self, fresh_vault_store):
        assert fresh_vault_store.current_version("m1") == 0

    def test_current_version_returns_max(self, fresh_vault_store):
        fresh_vault_store.append("m1", {"version": 1, "data": "a"})
        fresh_vault_store.append("m1", {"version": 5, "data": "b"})
        assert fresh_vault_store.current_version("m1") == 5


class TestVaultStoreTotalBytes:
    def test_zero_when_empty(self, fresh_vault_store):
        assert fresh_vault_store.total_bytes("m1") == 0

    def test_zero_for_invalid_mesh(self, fresh_vault_store):
        assert fresh_vault_store.total_bytes("../bad") == 0

    def test_sums_blob_sizes(self, fresh_vault_store):
        fresh_vault_store.append("m1", {"version": 1, "data": "x"})
        fresh_vault_store.append("m1", {"version": 2, "data": "yy"})
        total = fresh_vault_store.total_bytes("m1")
        assert total > 0


class TestVaultStoreAppend:
    def test_invalid_mesh_id(self, fresh_vault_store):
        v, err = fresh_vault_store.append("../bad", {"version": 1})
        assert v == 0
        assert err == "invalid mesh_id"

    def test_version_must_be_int(self, fresh_vault_store):
        v, err = fresh_vault_store.append("m1", {"version": "1"})
        assert v == 0
        assert err == "version must be int"

    def test_version_must_be_greater_than_current(self, fresh_vault_store):
        fresh_vault_store.append("m1", {"version": 5, "x": 1})
        v, err = fresh_vault_store.append("m1", {"version": 5, "x": 2})
        assert v == 5
        assert "not greater" in err
        # Lower version also rejected
        _v2, err2 = fresh_vault_store.append("m1", {"version": 1, "x": 3})
        assert err2 is not None

    def test_success_writes_blob_file(self, fresh_vault_store):
        v, err = fresh_vault_store.append("m1", {"version": 1, "x": "hi"})
        assert err is None
        assert v == 1
        path = os.path.join(fresh_vault_store.base_dir, "m1", "blobs", "00000001.json")
        assert os.path.exists(path)
        with open(path) as f:
            assert json.load(f)["x"] == "hi"

    def test_default_version_zero(self, fresh_vault_store):
        # Missing version key defaults to 0 — current is also 0 → not greater
        v, err = fresh_vault_store.append("m1", {"x": "hi"})
        assert v == 0
        assert err is not None and "not greater" in err


class TestVaultStoreSince:
    def test_invalid_mesh(self, fresh_vault_store):
        blobs, cur = fresh_vault_store.since("../bad", 0)
        assert blobs == []
        assert cur == 0

    def test_no_versions(self, fresh_vault_store):
        blobs, cur = fresh_vault_store.since("m1", 0)
        assert blobs == []
        assert cur == 0

    def test_returns_blobs_after_version(self, fresh_vault_store):
        fresh_vault_store.append("m1", {"version": 1, "x": "a"})
        fresh_vault_store.append("m1", {"version": 2, "x": "b"})
        fresh_vault_store.append("m1", {"version": 3, "x": "c"})
        blobs, cur = fresh_vault_store.since("m1", 1)
        assert cur == 3
        assert [b["x"] for b in blobs] == ["b", "c"]

    def test_returns_all_when_zero(self, fresh_vault_store):
        fresh_vault_store.append("m1", {"version": 1, "x": "a"})
        fresh_vault_store.append("m1", {"version": 2, "x": "b"})
        blobs, cur = fresh_vault_store.since("m1", 0)
        assert cur == 2
        assert len(blobs) == 2

    def test_unreadable_blob_skipped(self, fresh_vault_store):
        fresh_vault_store.append("m1", {"version": 1, "x": "a"})
        fresh_vault_store.append("m1", {"version": 2, "x": "b"})
        # Corrupt the second blob
        path = os.path.join(fresh_vault_store.base_dir, "m1", "blobs", "00000002.json")
        with open(path, "w") as f:
            f.write("not json")
        blobs, cur = fresh_vault_store.since("m1", 0)
        assert cur == 2
        assert len(blobs) == 1  # corrupt one skipped


class TestVaultStoreGC:
    def test_invalid_mesh_returns_zero(self, fresh_vault_store):
        assert fresh_vault_store.gc("../bad") == 0

    def test_no_versions_returns_zero(self, fresh_vault_store):
        assert fresh_vault_store.gc("m1") == 0

    def test_single_version_kept_unconditionally(self, fresh_vault_store):
        fresh_vault_store.append("m1", {"version": 1, "x": "a"})
        # Even with a 0-day retention, len(versions) <= 1 short-circuits
        removed = fresh_vault_store.gc("m1", retention_days=0, max_blobs=10)
        assert removed == 0

    def test_age_based_sweep(self, fresh_vault_store):
        for v in (1, 2, 3):
            fresh_vault_store.append("m1", {"version": v, "x": "a"})
        # Backdate the first two
        blobs_dir = os.path.join(fresh_vault_store.base_dir, "m1", "blobs")
        old = time.time() - 86400 * 30  # 30 days ago
        os.utime(os.path.join(blobs_dir, "00000001.json"), (old, old))
        os.utime(os.path.join(blobs_dir, "00000002.json"), (old, old))
        removed = fresh_vault_store.gc("m1", retention_days=7, max_blobs=100)
        assert removed == 2
        # Latest always retained
        assert fresh_vault_store.current_version("m1") == 3

    def test_count_based_trim(self, fresh_vault_store):
        for v in range(1, 6):
            fresh_vault_store.append("m1", {"version": v, "x": "a"})
        removed = fresh_vault_store.gc("m1", retention_days=999, max_blobs=2)
        assert removed == 3
        # Newest 2 survive: versions 4 and 5
        survivors = fresh_vault_store._list_blob_versions("m1")
        assert survivors == [4, 5]

    def test_uses_module_defaults_when_none(self, fresh_vault_store, mail_module):
        # Smoke test: passing None pulls VAULT_RETENTION_DAYS / VAULT_MAX_BLOBS
        for v in range(1, 4):
            fresh_vault_store.append("m1", {"version": v, "x": "a"})
        removed = fresh_vault_store.gc("m1")
        assert removed >= 0
        # Latest still present
        assert mail_module.VAULT_RETENTION_DAYS  # constant exists
        assert fresh_vault_store.current_version("m1") == 3


class TestVaultStoreReadWriteJson:
    def test_read_missing_returns_default(self, fresh_vault_store):
        sentinel = ["default"]
        assert fresh_vault_store._read_json("m1", "missing.json", sentinel) is sentinel

    def test_read_invalid_mesh_returns_default(self, fresh_vault_store):
        sentinel = {"d": 1}
        assert fresh_vault_store._read_json("../bad", "x.json", sentinel) is sentinel

    def test_read_corrupt_returns_default(self, fresh_vault_store):
        d = fresh_vault_store._ensure_mesh("m1")
        with open(os.path.join(d, "broken.json"), "w") as f:
            f.write("{not json")
        assert fresh_vault_store._read_json("m1", "broken.json", "X") == "X"

    def test_write_and_roundtrip(self, fresh_vault_store):
        ok = fresh_vault_store._write_json("m1", "data.json", {"a": 1})
        assert ok is True
        assert fresh_vault_store._read_json("m1", "data.json", None) == {"a": 1}

    def test_write_invalid_mesh(self, fresh_vault_store):
        assert fresh_vault_store._write_json("../bad", "x.json", {}) is False


class TestVaultStoreNodes:
    def test_get_nodes_default_empty(self, fresh_vault_store):
        assert fresh_vault_store.get_nodes("m1") == []

    def test_add_node_appends(self, fresh_vault_store):
        assert fresh_vault_store.add_node("m1", {"node_id": "n1", "type": "slave"})
        nodes = fresh_vault_store.get_nodes("m1")
        assert len(nodes) == 1
        assert nodes[0]["node_id"] == "n1"

    def test_add_node_dedups_by_node_id(self, fresh_vault_store):
        fresh_vault_store.add_node("m1", {"node_id": "n1", "type": "slave"})
        fresh_vault_store.add_node("m1", {"node_id": "n1", "type": "controller"})
        nodes = fresh_vault_store.get_nodes("m1")
        assert len(nodes) == 1
        assert nodes[0]["type"] == "controller"


class TestVaultStorePending:
    def test_pending_default_empty(self, fresh_vault_store):
        assert fresh_vault_store.get_pending_nodes("m1") == []

    def test_add_pending_dedups(self, fresh_vault_store):
        fresh_vault_store.add_pending_node("m1", {"node_id": "p1", "v": 1})
        fresh_vault_store.add_pending_node("m1", {"node_id": "p1", "v": 2})
        pending = fresh_vault_store.get_pending_nodes("m1")
        assert len(pending) == 1
        assert pending[0]["v"] == 2

    def test_remove_pending(self, fresh_vault_store):
        fresh_vault_store.add_pending_node("m1", {"node_id": "p1"})
        fresh_vault_store.add_pending_node("m1", {"node_id": "p2"})
        fresh_vault_store.remove_pending_node("m1", "p1")
        pending = fresh_vault_store.get_pending_nodes("m1")
        assert [p["node_id"] for p in pending] == ["p2"]

    def test_remove_pending_nonexistent_silent(self, fresh_vault_store):
        fresh_vault_store.add_pending_node("m1", {"node_id": "p1"})
        fresh_vault_store.remove_pending_node("m1", "ghost")
        assert len(fresh_vault_store.get_pending_nodes("m1")) == 1


class TestVaultStoreRejection:
    def test_rejection_key_empty(self, fresh_vault_store):
        assert fresh_vault_store._rejection_key("") == ""
        assert fresh_vault_store._rejection_key(None) == ""

    def test_rejection_key_stable_24_chars(self, fresh_vault_store):
        k1 = fresh_vault_store._rejection_key("PUBKEY-A")
        k2 = fresh_vault_store._rejection_key("PUBKEY-A")
        k3 = fresh_vault_store._rejection_key("PUBKEY-B")
        assert k1 == k2
        assert k1 != k3
        assert len(k1) == 24

    def test_is_rejected_default_false(self, fresh_vault_store):
        assert fresh_vault_store.is_rejected("m1", "PUB") is False

    def test_is_rejected_empty_pubkey(self, fresh_vault_store):
        assert fresh_vault_store.is_rejected("m1", "") is False

    def test_add_then_check(self, fresh_vault_store):
        fresh_vault_store.add_rejected_node("m1", "n1", "PUB", reason="no")
        assert fresh_vault_store.is_rejected("m1", "PUB") is True

    def test_add_rejected_dedups_by_key(self, fresh_vault_store):
        fresh_vault_store.add_rejected_node("m1", "n1", "PUB", reason="first")
        fresh_vault_store.add_rejected_node("m1", "n1", "PUB", reason="second")
        rej = fresh_vault_store.get_rejected_nodes("m1")
        assert len(rej) == 1
        assert rej[0]["reason"] == "second"

    def test_add_rejected_empty_pubkey_returns_false(self, fresh_vault_store):
        assert fresh_vault_store.add_rejected_node("m1", "n1", "", reason="x") is False

    def test_clear_rejection_removes(self, fresh_vault_store):
        fresh_vault_store.add_rejected_node("m1", "n1", "PUB")
        assert fresh_vault_store.clear_rejection("m1", "PUB") is True
        assert fresh_vault_store.is_rejected("m1", "PUB") is False

    def test_clear_rejection_missing_returns_false(self, fresh_vault_store):
        assert fresh_vault_store.clear_rejection("m1", "PUB") is False

    def test_clear_rejection_empty_pubkey(self, fresh_vault_store):
        assert fresh_vault_store.clear_rejection("m1", "") is False

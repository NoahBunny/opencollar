"""Comprehensive coverage for focuslock_mesh.MessageStore.

`tests/test_messages.py` covers edit/delete/tombstone semantics. This file
fills the rest of the contract surface — init/load/save, add() id+ts
generation + size cap, get() ordering + limits, mark_read() / mark_replied(),
and concurrent-write safety.
"""

import json
import os
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def store(tmp_path):
    from focuslock_mesh import MessageStore

    return MessageStore(persist_path=str(tmp_path / "msgs.json"))


@pytest.fixture
def in_memory_store():
    from focuslock_mesh import MessageStore

    return MessageStore(persist_path=None)


# ──────────────────────── __init__ + _load ────────────────────────


class TestInit:
    def test_empty_init(self, in_memory_store):
        assert in_memory_store.messages == []
        assert in_memory_store.persist_path is None

    def test_init_with_path_no_file_yet(self, tmp_path):
        from focuslock_mesh import MessageStore

        path = tmp_path / "nonexistent.json"
        store = MessageStore(persist_path=str(path))
        assert store.messages == []
        # File should NOT be created until save()
        assert not path.exists()

    def test_load_existing_file(self, tmp_path):
        from focuslock_mesh import MessageStore

        path = tmp_path / "existing.json"
        seed = [
            {"id": "m1", "ts": 100, "from": "lion", "text": "hi"},
            {"id": "m2", "ts": 200, "from": "bunny", "text": "hello"},
        ]
        path.write_text(json.dumps(seed))
        store = MessageStore(persist_path=str(path))
        assert len(store.messages) == 2
        assert store.messages[0]["id"] == "m1"

    def test_load_corrupt_file_swallows_error(self, tmp_path, caplog):
        from focuslock_mesh import MessageStore

        path = tmp_path / "broken.json"
        path.write_text("{not valid json")
        # Should not raise — _load wraps in try/except
        store = MessageStore(persist_path=str(path))
        assert store.messages == []


# ──────────────────────── save ────────────────────────


class TestSave:
    def test_save_no_path_is_noop(self, in_memory_store):
        in_memory_store.add({"from": "lion", "text": "x"})
        # Should not raise; nothing persisted
        in_memory_store.save()

    def test_save_creates_parent_directory(self, tmp_path):
        from focuslock_mesh import MessageStore

        # Use a deeply nested path that doesn't exist yet
        nested = tmp_path / "a" / "b" / "c" / "msgs.json"
        store = MessageStore(persist_path=str(nested))
        store.add({"from": "lion", "text": "hi"})
        assert nested.exists()
        assert nested.parent.is_dir()

    def test_save_atomic_replace(self, store):
        store.add({"from": "lion", "text": "first"})
        store.add({"from": "lion", "text": "second"})
        # The .tmp file should not survive after replace
        assert not os.path.exists(store.persist_path + ".tmp")
        with open(store.persist_path) as f:
            data = json.load(f)
        assert len(data) == 2

    def test_save_swallows_oserror(self, tmp_path, monkeypatch, caplog):
        from focuslock_mesh import MessageStore

        store = MessageStore(persist_path=str(tmp_path / "msgs.json"))
        # Force the open to fail
        original_open = open

        def boom(path, *args, **kwargs):
            if str(path).endswith(".tmp"):
                raise OSError("disk full")
            return original_open(path, *args, **kwargs)

        monkeypatch.setattr("builtins.open", boom)
        # Should not raise
        store.save()


# ──────────────────────── add ────────────────────────


class TestAdd:
    def test_add_assigns_ts_if_missing(self, store):
        msg = store.add({"from": "lion", "text": "hi"})
        assert "ts" in msg
        assert msg["ts"] > 0
        assert isinstance(msg["ts"], int)

    def test_add_preserves_explicit_ts(self, store):
        msg = store.add({"from": "lion", "text": "hi", "ts": 12345})
        assert msg["ts"] == 12345

    def test_add_assigns_id_if_missing(self, store):
        msg = store.add({"from": "lion", "text": "hi", "ts": 12345})
        assert "id" in msg
        # Format: <ts>_<index>
        assert msg["id"].startswith("12345_")

    def test_add_preserves_explicit_id(self, store):
        msg = store.add({"from": "lion", "text": "hi", "id": "custom-id"})
        assert msg["id"] == "custom-id"

    def test_add_ids_are_unique_for_sequential_adds(self, store):
        ids = {store.add({"from": "lion", "text": f"m{i}"})["id"] for i in range(20)}
        assert len(ids) == 20

    def test_add_persists_to_disk(self, store):
        store.add({"from": "lion", "text": "persisted"})
        with open(store.persist_path) as f:
            data = json.load(f)
        assert len(data) == 1
        assert data[0]["text"] == "persisted"

    def test_add_size_cap_500(self, store):
        for n in range(550):
            store.add({"from": "bunny", "text": f"m{n}"})
        assert len(store.messages) == 500
        # Oldest 50 trimmed — the survivors are m50..m549
        assert store.messages[0]["text"] == "m50"
        assert store.messages[-1]["text"] == "m549"

    def test_add_returns_modified_dict(self, store):
        # add() mutates in place + returns the same dict
        body = {"from": "lion", "text": "hi"}
        result = store.add(body)
        assert result is body
        assert "ts" in body
        assert "id" in body


# ──────────────────────── get ────────────────────────


class TestGet:
    def test_get_empty_returns_empty_list(self, store):
        assert store.get() == []

    def test_get_returns_newest_first(self, store):
        store.add({"from": "lion", "text": "first"})
        store.add({"from": "lion", "text": "second"})
        store.add({"from": "lion", "text": "third"})
        msgs = store.get()
        assert [m["text"] for m in msgs] == ["third", "second", "first"]

    def test_get_default_limit_50(self, store):
        for n in range(60):
            store.add({"from": "bunny", "text": f"m{n}"})
        msgs = store.get()
        assert len(msgs) == 50
        # Newest first, so m59 is first
        assert msgs[0]["text"] == "m59"
        assert msgs[-1]["text"] == "m10"

    def test_get_custom_limit(self, store):
        for n in range(20):
            store.add({"from": "bunny", "text": f"m{n}"})
        msgs = store.get(limit=5)
        assert len(msgs) == 5
        assert [m["text"] for m in msgs] == [f"m{n}" for n in (19, 18, 17, 16, 15)]

    def test_get_limit_larger_than_store(self, store):
        store.add({"from": "lion", "text": "only"})
        msgs = store.get(limit=999)
        assert len(msgs) == 1

    def test_get_reader_arg_is_accepted_but_unused(self, store):
        # The current contract takes `reader` but doesn't filter by it.
        # Pin the present behavior so a future filtering change is intentional.
        store.add({"from": "lion", "text": "to-bunny"})
        msgs_lion = store.get(reader="lion")
        msgs_bunny = store.get(reader="bunny")
        assert msgs_lion == msgs_bunny


# ──────────────────────── mark_read ────────────────────────


class TestMarkRead:
    def test_mark_read_adds_reader(self, store):
        msg = store.add({"from": "lion", "text": "hi"})
        result = store.mark_read(msg["id"], "bunny")
        assert result == {"ok": True}
        assert "bunny" in store.messages[0]["read_by"]

    def test_mark_read_dedups(self, store):
        msg = store.add({"from": "lion", "text": "hi"})
        store.mark_read(msg["id"], "bunny")
        store.mark_read(msg["id"], "bunny")
        assert store.messages[0]["read_by"] == ["bunny"]

    def test_mark_read_multiple_readers(self, store):
        msg = store.add({"from": "lion", "text": "hi"})
        store.mark_read(msg["id"], "bunny")
        store.mark_read(msg["id"], "desktop-1")
        assert set(store.messages[0]["read_by"]) == {"bunny", "desktop-1"}

    def test_mark_read_unknown_id_returns_not_found(self, store):
        result = store.mark_read("no-such-id", "bunny")
        assert result == {"error": "not found"}

    def test_mark_read_persists(self, store):
        msg = store.add({"from": "lion", "text": "hi"})
        store.mark_read(msg["id"], "bunny")
        with open(store.persist_path) as f:
            data = json.load(f)
        assert "bunny" in data[0]["read_by"]

    def test_mark_read_initializes_read_by_field(self, store):
        msg = store.add({"from": "lion", "text": "hi"})
        # Pre-mark, no read_by field
        assert "read_by" not in store.messages[0]
        store.mark_read(msg["id"], "bunny")
        assert store.messages[0]["read_by"] == ["bunny"]


# ──────────────────────── mark_replied ────────────────────────


class TestMarkReplied:
    def test_mark_replied_sets_flag(self, store):
        msg = store.add({"from": "lion", "text": "reply please"})
        result = store.mark_replied(msg["id"])
        assert result == {"ok": True}
        assert store.messages[0]["replied"] is True

    def test_mark_replied_unknown_id(self, store):
        result = store.mark_replied("no-such-id")
        assert result == {"error": "not found"}

    def test_mark_replied_persists(self, store):
        msg = store.add({"from": "lion", "text": "x"})
        store.mark_replied(msg["id"])
        with open(store.persist_path) as f:
            data = json.load(f)
        assert data[0]["replied"] is True

    def test_mark_replied_idempotent(self, store):
        msg = store.add({"from": "lion", "text": "x"})
        store.mark_replied(msg["id"])
        store.mark_replied(msg["id"])
        assert store.messages[0]["replied"] is True


# ──────────────────────── concurrency ────────────────────────


class TestConcurrency:
    def test_concurrent_adds_no_loss(self, store):
        """20 threads x 25 messages each = 500 messages, no clobber.
        The size-cap is exactly 500, so this also asserts the cap doesn't
        prematurely trim under contention."""
        N_THREADS = 20
        PER_THREAD = 25

        def worker(thread_id):
            for i in range(PER_THREAD):
                store.add({"from": "lion", "text": f"t{thread_id}-m{i}"})

        threads = [threading.Thread(target=worker, args=(t,)) for t in range(N_THREADS)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(store.messages) == N_THREADS * PER_THREAD
        # All ids unique despite concurrency
        ids = {m["id"] for m in store.messages}
        assert len(ids) == N_THREADS * PER_THREAD

    def test_concurrent_mark_read_dedups(self, store):
        """Multiple threads mark_read with the same reader — final list contains
        the reader exactly once."""
        msg = store.add({"from": "lion", "text": "hi"})
        N = 10

        def worker():
            for _ in range(20):
                store.mark_read(msg["id"], "bunny")

        threads = [threading.Thread(target=worker) for _ in range(N)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert store.messages[0]["read_by"] == ["bunny"]

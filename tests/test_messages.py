"""Tests for the messaging module (edit / delete / tombstone).

Covers:
- MessageStore.edit() appends to edit_history[] and overwrites live fields
- MessageStore.edit() E2EE: replaces ciphertext / encrypted_key / iv
- MessageStore.delete_message() sets tombstone, preserves originals
- Edit on a deleted message is rejected
- Idempotent delete
- Atomic save round-trip after edit + delete
"""

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))


@pytest.fixture
def store(tmp_path):
    from focuslock_mesh import MessageStore

    return MessageStore(persist_path=str(tmp_path / "msgs.json"))


def test_edit_appends_history_and_overwrites(store):
    msg = store.add({"from": "lion", "node_id": "controller", "text": "hello"})
    mid = msg["id"]
    result = store.edit(mid, "hello (fixed)")
    assert "ok" in result
    edited = result["message"]
    assert edited["text"] == "hello (fixed)"
    assert edited["edited_at"] > 0
    assert len(edited["edit_history"]) == 1
    assert edited["edit_history"][0]["prev_text"] == "hello"


def test_edit_e2ee_replaces_ciphertext(store):
    msg = store.add(
        {
            "from": "lion",
            "node_id": "controller",
            "text": "[e2ee]",
            "encrypted": True,
            "ciphertext": "old-ct",
            "encrypted_key": "old-ek",
            "iv": "old-iv",
        }
    )
    mid = msg["id"]
    result = store.edit(
        mid,
        "[e2ee]",
        new_ciphertext="new-ct",
        new_encrypted_key="new-ek",
        new_iv="new-iv",
    )
    assert "ok" in result
    edited = result["message"]
    assert edited["ciphertext"] == "new-ct"
    assert edited["encrypted_key"] == "new-ek"
    assert edited["iv"] == "new-iv"
    history = edited["edit_history"]
    assert history[0]["prev_ciphertext"] == "old-ct"
    assert history[0]["prev_encrypted_key"] == "old-ek"
    assert history[0]["prev_iv"] == "old-iv"


def test_edit_unknown_id_returns_not_found(store):
    result = store.edit("does-not-exist", "x")
    assert result == {"error": "not found"}


def test_delete_sets_tombstone_keeps_original(store):
    msg = store.add({"from": "lion", "node_id": "controller", "text": "secret"})
    mid = msg["id"]
    result = store.delete_message(mid, deleted_by="lion")
    assert "ok" in result
    deleted = result["message"]
    assert deleted["deleted"] is True
    assert deleted["deleted_at"] > 0
    assert deleted["deleted_by"] == "lion"
    # Original text preserved so Lion's audit view can render it
    assert deleted["text"] == "secret"


def test_delete_idempotent(store):
    msg = store.add({"from": "bunny", "node_id": "pixel-10", "text": "hi"})
    mid = msg["id"]
    first = store.delete_message(mid, deleted_by="lion", ts=100)
    second = store.delete_message(mid, deleted_by="lion", ts=200)
    assert first["message"]["deleted_at"] == 100
    # Idempotent — second call returns the existing record without overwriting
    assert second["message"]["deleted_at"] == 100


def test_edit_rejected_after_delete(store):
    msg = store.add({"from": "lion", "node_id": "controller", "text": "x"})
    mid = msg["id"]
    store.delete_message(mid, deleted_by="lion")
    result = store.edit(mid, "y")
    assert result == {"error": "cannot edit deleted message"}


def test_save_persists_edit_and_delete(store, tmp_path):
    msg = store.add({"from": "lion", "node_id": "controller", "text": "first"})
    mid = msg["id"]
    store.edit(mid, "second")
    store.delete_message(mid, deleted_by="lion")

    raw = json.loads(open(store.persist_path).read())
    assert len(raw) == 1
    persisted = raw[0]
    assert persisted["text"] == "second"
    assert persisted["edit_history"][0]["prev_text"] == "first"
    assert persisted["deleted"] is True
    assert persisted["deleted_by"] == "lion"


def test_size_cap_honored_with_edits(store):
    # Sanity: edits don't blow the 500-msg cap.
    for n in range(550):
        store.add({"from": "bunny", "node_id": "pixel-10", "text": f"m{n}"})
    # A pre-existing 50 + 500 cap = oldest 50 already trimmed before any edit.
    # Edit one of the survivors: should not change list length.
    survivor = store.messages[0]
    store.edit(survivor["id"], "edited")
    assert len(store.messages) == 500

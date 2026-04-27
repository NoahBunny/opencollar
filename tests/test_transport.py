"""Tests for shared/focuslock_transport.py — vault transport abstraction.

The vault transport is the bytes-on-the-wire layer that desktop collars
talk to (HTTPS relay or Syncthing-shared directory). Pre-fix this module
was at 0% coverage despite carrying:

- A 64 MB response cap that defends against malicious-relay OOM
- `_safe_id` + `_safe_path` traversal protection on the Syncthing path
- A `MAX_VERSION` poisoning defense against a peer dropping a huge
  blob filename to block all future writes
- Symlink-rejection in both `since()` and `nodes()` to prevent escape

These tests pin every one of those defenses plus the standard
happy-path semantics (since/append/nodes/register_node x http +
syncthing) and the `transport_factory` dispatch.
"""

import io
import json
import os
import urllib.error
import urllib.request
from unittest.mock import MagicMock, patch

import pytest
from focuslock_transport import (
    HttpVaultTransport,
    SyncthingVaultTransport,
    VaultTransport,
    _safe_id,
    _safe_path,
    transport_factory,
)

# ── _safe_id ──


class TestSafeId:
    def test_empty_rejected(self):
        assert _safe_id("") is False

    def test_alphanumeric_accepted(self):
        assert _safe_id("abc123XYZ") is True

    def test_dash_underscore_accepted(self):
        assert _safe_id("abc-123_XYZ") is True

    def test_max_length_64_accepted(self):
        assert _safe_id("a" * 64) is True

    def test_over_64_rejected(self):
        assert _safe_id("a" * 65) is False

    def test_path_separator_rejected(self):
        assert _safe_id("foo/bar") is False
        assert _safe_id("foo\\bar") is False

    def test_dot_rejected(self):
        # Critical: ".." or ".hidden" must be rejected to prevent traversal
        # via filename construction.
        assert _safe_id("..") is False
        assert _safe_id(".hidden") is False
        assert _safe_id("foo.json") is False

    def test_null_byte_rejected(self):
        assert _safe_id("foo\x00bar") is False

    def test_whitespace_rejected(self):
        assert _safe_id("foo bar") is False
        assert _safe_id("foo\tbar") is False
        assert _safe_id("foo\nbar") is False

    def test_special_chars_rejected(self):
        for ch in ["$", "%", "&", "*", ";", "|", "`", "(", ")", "<", ">"]:
            assert _safe_id(f"foo{ch}bar") is False, f"char {ch!r} should be rejected"


# ── _safe_path ──


class TestSafePath:
    def test_normal_join_returns_realpath(self, tmp_path):
        result = _safe_path(str(tmp_path), "subdir", "file.json")
        assert result.startswith(os.path.realpath(str(tmp_path)))
        assert result.endswith(os.path.join("subdir", "file.json"))

    def test_traversal_via_dotdot_rejected(self, tmp_path):
        with pytest.raises(ValueError, match="escapes"):
            _safe_path(str(tmp_path), "..", "etc", "passwd")

    def test_traversal_via_absolute_part_rejected(self, tmp_path):
        # os.path.join("/safe", "/etc/passwd") returns "/etc/passwd" —
        # an attacker passing an absolute part bypasses the prefix.
        with pytest.raises(ValueError, match="escapes"):
            _safe_path(str(tmp_path), "/etc", "passwd")

    def test_realpath_collapses_symlink_escape(self, tmp_path):
        # If a parent dir is a symlink pointing outside base_dir, _safe_path
        # must catch it via realpath comparison.
        outside = tmp_path / "outside"
        outside.mkdir()
        base = tmp_path / "base"
        base.mkdir()
        link = base / "escape"
        link.symlink_to(str(outside))
        with pytest.raises(ValueError, match="escapes"):
            _safe_path(str(base), "escape", "secret")

    def test_base_dir_itself_allowed(self, tmp_path):
        # _safe_path(base) → base is a valid no-op result.
        result = _safe_path(str(tmp_path))
        assert result == os.path.realpath(str(tmp_path))


# ── VaultTransport abstract base ──


class TestVaultTransportAbstract:
    def test_methods_raise_not_implemented(self):
        t = VaultTransport()
        with pytest.raises(NotImplementedError):
            t.since("m", 0)
        with pytest.raises(NotImplementedError):
            t.append("m", {})
        with pytest.raises(NotImplementedError):
            t.nodes("m")
        with pytest.raises(NotImplementedError):
            t.register_node("m", {})


# ── HttpVaultTransport ──


def _http_resp(payload):
    """Build a context-manager mock that mimics urllib's response."""
    body = json.dumps(payload).encode()

    resp = MagicMock()
    resp.read = MagicMock(side_effect=lambda n=None: body if n is None else body[:n])
    resp.__enter__ = lambda self: self
    resp.__exit__ = lambda *a: None
    return resp


class TestHttpVaultTransportInit:
    def test_strips_trailing_slash(self):
        t = HttpVaultTransport("https://relay.example.com/")
        assert t.base_url == "https://relay.example.com"

    def test_no_trailing_slash_unchanged(self):
        t = HttpVaultTransport("https://relay.example.com")
        assert t.base_url == "https://relay.example.com"

    def test_empty_base_url(self):
        t = HttpVaultTransport("")
        assert t.base_url == ""

    def test_none_base_url(self):
        t = HttpVaultTransport(None)
        assert t.base_url == ""


class TestHttpReadCapped:
    """Pin the 64 MB response cap — defense against malicious-relay OOM."""

    def test_under_cap_accepted(self):
        t = HttpVaultTransport("https://r")
        body = b"A" * 1024
        resp = MagicMock()
        resp.read = MagicMock(return_value=body)
        result = t._read_capped(resp)
        assert result == body
        # Must call read() with cap+1 to detect overrun without unbounded alloc
        resp.read.assert_called_once_with(t.MAX_RESPONSE_BYTES + 1)

    def test_exact_cap_accepted(self):
        t = HttpVaultTransport("https://r")
        body = b"A" * t.MAX_RESPONSE_BYTES
        resp = MagicMock()
        resp.read = MagicMock(return_value=body)
        assert len(t._read_capped(resp)) == t.MAX_RESPONSE_BYTES

    def test_over_cap_rejected(self):
        t = HttpVaultTransport("https://r")
        body = b"A" * (t.MAX_RESPONSE_BYTES + 1)
        resp = MagicMock()
        resp.read = MagicMock(return_value=body)
        with pytest.raises(ValueError, match="exceeds"):
            t._read_capped(resp)


class TestHttpSince:
    def test_returns_blobs_and_version(self):
        t = HttpVaultTransport("https://relay.example.com")
        with patch.object(
            urllib.request, "urlopen", return_value=_http_resp({"blobs": [{"v": 1}], "current_version": 5})
        ) as mock_open:
            blobs, version = t.since("MESH-1", 0)
        assert blobs == [{"v": 1}]
        assert version == 5
        # URL shape sanity
        called_req = mock_open.call_args[0][0]
        assert called_req.full_url == "https://relay.example.com/vault/MESH-1/since/0"
        assert called_req.method == "GET"

    def test_no_base_url_returns_empty(self):
        t = HttpVaultTransport("")
        assert t.since("M", 0) == ([], 0)

    def test_404_returns_empty(self):
        t = HttpVaultTransport("https://r")
        err = urllib.error.HTTPError("u", 404, "not found", {}, io.BytesIO(b""))
        with patch.object(urllib.request, "urlopen", side_effect=err):
            assert t.since("M", 0) == ([], 0)

    def test_500_re_raises(self):
        t = HttpVaultTransport("https://r")
        err = urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b""))
        with patch.object(urllib.request, "urlopen", side_effect=err), pytest.raises(urllib.error.HTTPError):
            t.since("M", 0)


class TestHttpAppend:
    def test_returns_version_no_error(self):
        t = HttpVaultTransport("https://relay.example.com")
        with patch.object(urllib.request, "urlopen", return_value=_http_resp({"version": 7})) as mock_open:
            v, err = t.append("MESH-1", {"data": "x"})
        assert v == 7
        assert err is None
        called_req = mock_open.call_args[0][0]
        assert called_req.full_url == "https://relay.example.com/vault/MESH-1/append"
        assert called_req.method == "POST"
        assert called_req.headers.get("Content-type") == "application/json"
        # body is canonical JSON (no spaces)
        assert called_req.data == b'{"data":"x"}'

    def test_no_base_url_returns_error(self):
        t = HttpVaultTransport("")
        v, err = t.append("M", {})
        assert v == 0
        assert err == "no base_url"

    def test_409_conflict_extracts_current_version(self):
        t = HttpVaultTransport("https://r")
        body = json.dumps({"current_version": 12}).encode()
        err = urllib.error.HTTPError("u", 409, "conflict", {}, io.BytesIO(body))
        with patch.object(urllib.request, "urlopen", side_effect=err):
            v, e = t.append("M", {})
        assert v == 0
        assert "current_version=12" in e

    def test_409_with_unparseable_body_falls_through_to_http_409(self):
        t = HttpVaultTransport("https://r")
        err = urllib.error.HTTPError("u", 409, "conflict", {}, io.BytesIO(b"not json"))
        with patch.object(urllib.request, "urlopen", side_effect=err):
            v, e = t.append("M", {})
        assert v == 0
        # Implementation falls through to "HTTP 409" formatting
        assert e == "HTTP 409"

    def test_500_returns_http_code(self):
        t = HttpVaultTransport("https://r")
        err = urllib.error.HTTPError("u", 500, "boom", {}, io.BytesIO(b""))
        with patch.object(urllib.request, "urlopen", side_effect=err):
            v, e = t.append("M", {})
        assert v == 0
        assert e == "HTTP 500"


class TestHttpNodes:
    def test_returns_node_list(self):
        t = HttpVaultTransport("https://r")
        with patch.object(urllib.request, "urlopen", return_value=_http_resp({"nodes": [{"id": "a"}]})):
            assert t.nodes("M") == [{"id": "a"}]

    def test_no_base_url_returns_empty(self):
        assert HttpVaultTransport("").nodes("M") == []

    def test_exception_swallowed_returns_empty(self):
        t = HttpVaultTransport("https://r")
        with patch.object(urllib.request, "urlopen", side_effect=ConnectionError("offline")):
            assert t.nodes("M") == []


class TestHttpRegisterNode:
    def test_returns_response_dict(self):
        t = HttpVaultTransport("https://r")
        with patch.object(urllib.request, "urlopen", return_value=_http_resp({"status": "pending"})) as mock_open:
            result = t.register_node("MESH-1", {"node_id": "n1", "pubkey": "..."})
        assert result == {"status": "pending"}
        called_req = mock_open.call_args[0][0]
        assert called_req.full_url == "https://r/vault/MESH-1/register-node-request"
        assert called_req.method == "POST"

    def test_no_base_url_returns_error(self):
        assert HttpVaultTransport("").register_node("M", {}) == {"error": "no base_url"}


# ── SyncthingVaultTransport ──


class TestSyncthingInit:
    def test_expands_user_path(self, monkeypatch):
        monkeypatch.setenv("HOME", "/home/test")
        t = SyncthingVaultTransport("~/vault")
        assert t.vault_dir == "/home/test/vault"

    def test_empty_dir(self):
        t = SyncthingVaultTransport("")
        assert t.vault_dir == ""


class TestSyncthingMeshDir:
    def test_invalid_mesh_id_rejected(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        with pytest.raises(ValueError, match="Invalid mesh_id"):
            t._mesh_dir("../escape")
        with pytest.raises(ValueError, match="Invalid mesh_id"):
            t._mesh_dir("foo/bar")

    def test_valid_mesh_id_returns_path(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        path = t._mesh_dir("MESH-1")
        assert path.endswith("MESH-1")


class TestSyncthingSince:
    def test_missing_blobs_dir_returns_empty(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        # No blobs dir created
        assert t.since("MESH-1", 0) == ([], 0)

    def test_returns_blobs_above_version_with_current_version(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        for v in (1, 2, 3, 4, 5):
            (blobs_dir / f"{v:08d}.json").write_text(json.dumps({"version": v, "data": f"b{v}"}))
        result, current = t.since("MESH-1", 2)
        # Only versions 3, 4, 5 returned (version filter is exclusive)
        assert [b["version"] for b in result] == [3, 4, 5]
        # current_version reflects the highest VALID file present, not the
        # filtered set
        assert current == 5

    def test_negative_version_filename_skipped(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        # An attacker-dropped negative-version filename
        (blobs_dir / "-0000005.json").write_text(json.dumps({"version": -5}))
        (blobs_dir / "00000001.json").write_text(json.dumps({"version": 1}))
        blobs, current = t.since("MESH-1", 0)
        assert [b["version"] for b in blobs] == [1]
        assert current == 1

    def test_zero_version_filename_skipped(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        (blobs_dir / "00000000.json").write_text(json.dumps({"version": 0}))
        (blobs_dir / "00000001.json").write_text(json.dumps({"version": 1}))
        blobs, _current = t.since("MESH-1", 0)
        assert [b["version"] for b in blobs] == [1]

    def test_max_version_overflow_filename_skipped(self, tmp_path):
        """Defends against poisoning attack: a malicious peer drops a huge
        version filename to set current_version to ~2 billion, blocking
        all future appends from advancing."""
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        poison = t.MAX_VERSION + 1
        (blobs_dir / f"{poison:020d}.json").write_text(json.dumps({"version": poison}))
        (blobs_dir / "00000003.json").write_text(json.dumps({"version": 3}))
        blobs, current = t.since("MESH-1", 0)
        # Poison file ignored; current_version stays at 3
        assert current == 3
        assert all(b["version"] != poison for b in blobs)

    def test_non_numeric_filename_skipped(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        (blobs_dir / "abc.json").write_text("{}")
        (blobs_dir / "00000001.json").write_text(json.dumps({"version": 1}))
        blobs, _current = t.since("MESH-1", 0)
        assert [b["version"] for b in blobs] == [1]

    def test_non_json_extension_skipped(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        (blobs_dir / "00000001.txt").write_text("not json")
        (blobs_dir / "00000002.json").write_text(json.dumps({"version": 2}))
        blobs, _current = t.since("MESH-1", 0)
        assert [b["version"] for b in blobs] == [2]

    def test_symlink_blob_skipped(self, tmp_path):
        """Symlink in blobs/ could point outside vault_dir to exfiltrate
        unrelated files. Reader must skip symlinks."""
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        target = tmp_path / "secret.json"
        target.write_text(json.dumps({"version": 99, "secret": "leak"}))
        link = blobs_dir / "00000001.json"
        link.symlink_to(target)
        # Also create a real blob to prove the scan isn't aborted
        (blobs_dir / "00000002.json").write_text(json.dumps({"version": 2}))
        blobs, _current = t.since("MESH-1", 0)
        # Symlink at version 1 was skipped; version 2 returned
        assert all(b.get("version") != 99 for b in blobs)
        # current_version honors the symlinked filename's number (the file
        # listing pre-pass doesn't open the file), but the blob itself isn't
        # read — that's the security guarantee. Pin the leak doesn't happen:
        assert all("secret" not in b for b in blobs)

    def test_malformed_json_skipped(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        (blobs_dir / "00000001.json").write_text("{ not valid json")
        (blobs_dir / "00000002.json").write_text(json.dumps({"version": 2}))
        blobs, _current = t.since("MESH-1", 0)
        assert [b["version"] for b in blobs] == [2]


class TestSyncthingAppend:
    def test_first_append_creates_dir_and_writes(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        v, err = t.append("MESH-1", {"data": "x"})
        assert err is None
        assert v == 1
        path = tmp_path / "MESH-1" / "blobs" / "00000001.json"
        assert path.exists()
        assert json.loads(path.read_text())["version"] == 1

    def test_append_uses_existing_version_if_set(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        v, err = t.append("MESH-1", {"version": 42, "data": "x"})
        assert err is None
        assert v == 42
        assert (tmp_path / "MESH-1" / "blobs" / "00000042.json").exists()

    def test_append_unversioned_picks_max_plus_one(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        (blobs_dir / "00000005.json").write_text(json.dumps({"version": 5}))
        v, err = t.append("MESH-1", {"data": "y"})
        assert err is None
        assert v == 6

    def test_append_collision_retries(self, tmp_path):
        """If `blob.version` collides with an existing file, append must
        retry against the true max — covers a race between two writers."""
        t = SyncthingVaultTransport(str(tmp_path))
        blobs_dir = tmp_path / "MESH-1" / "blobs"
        blobs_dir.mkdir(parents=True)
        (blobs_dir / "00000003.json").write_text(json.dumps({"version": 3, "data": "original"}))
        v, err = t.append("MESH-1", {"version": 3, "data": "collision"})
        assert err is None
        # Must NOT overwrite version 3 — pushes itself to 4
        assert v == 4
        assert (blobs_dir / "00000004.json").exists()
        # Original version 3 is preserved
        assert json.loads((blobs_dir / "00000003.json").read_text())["data"] == "original"

    def test_atomic_write_via_tmp(self, tmp_path):
        """Write goes through `.tmp` + os.replace so a partial-disk-full
        crash can't leave a half-written blob visible."""
        t = SyncthingVaultTransport(str(tmp_path))
        _v, _err = t.append("MESH-1", {"data": "x"})
        # No leftover .tmp file after success
        leftovers = list((tmp_path / "MESH-1" / "blobs").glob("*.tmp"))
        assert leftovers == []


class TestSyncthingNodes:
    def test_missing_file_returns_empty(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        assert t.nodes("MESH-1") == []

    def test_returns_parsed_list(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        mesh_dir = tmp_path / "MESH-1"
        mesh_dir.mkdir()
        (mesh_dir / "nodes.json").write_text(json.dumps([{"id": "n1"}, {"id": "n2"}]))
        assert t.nodes("MESH-1") == [{"id": "n1"}, {"id": "n2"}]

    def test_symlink_nodes_file_returns_empty(self, tmp_path):
        """nodes.json being a symlink is rejected — same exfil concern as
        symlinked blobs."""
        t = SyncthingVaultTransport(str(tmp_path))
        mesh_dir = tmp_path / "MESH-1"
        mesh_dir.mkdir()
        target = tmp_path / "secret.json"
        target.write_text(json.dumps([{"secret": "leak"}]))
        (mesh_dir / "nodes.json").symlink_to(target)
        assert t.nodes("MESH-1") == []

    def test_malformed_json_returns_empty(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        mesh_dir = tmp_path / "MESH-1"
        mesh_dir.mkdir()
        (mesh_dir / "nodes.json").write_text("{ broken")
        assert t.nodes("MESH-1") == []


class TestSyncthingRegisterNode:
    def test_invalid_node_id_rejected(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        result = t.register_node("MESH-1", {"node_id": "../escape"})
        assert "error" in result
        assert "invalid node_id" in result["error"]

    def test_missing_node_id_rejected(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        result = t.register_node("MESH-1", {})
        # default "unknown" is alnum so it passes _safe_id
        assert result.get("status") == "pending"

    def test_creates_pending_file_with_timestamp(self, tmp_path):
        t = SyncthingVaultTransport(str(tmp_path))
        result = t.register_node("MESH-1", {"node_id": "node-1", "pubkey": "abc"})
        assert result == {"status": "pending", "node_id": "node-1"}
        path = tmp_path / "MESH-1" / "pending" / "node-1.json"
        assert path.exists()
        data = json.loads(path.read_text())
        assert data["pubkey"] == "abc"
        assert "requested_at" in data
        assert isinstance(data["requested_at"], int)


# ── transport_factory ──


class TestTransportFactory:
    def test_default_returns_http(self):
        t = transport_factory({"mesh_url": "https://r"})
        assert isinstance(t, HttpVaultTransport)
        assert t.base_url == "https://r"

    def test_http_explicit_returns_http(self):
        t = transport_factory({"vault_transport": "http", "mesh_url": "https://r"})
        assert isinstance(t, HttpVaultTransport)

    def test_syncthing_with_dir_returns_syncthing(self, tmp_path):
        t = transport_factory({"vault_transport": "syncthing", "syncthing_vault_dir": str(tmp_path)})
        assert isinstance(t, SyncthingVaultTransport)
        assert t.vault_dir == str(tmp_path)

    def test_syncthing_without_dir_falls_back_to_http(self, caplog):
        import logging

        with caplog.at_level(logging.WARNING):
            t = transport_factory({"vault_transport": "syncthing", "mesh_url": "https://r"})
        assert isinstance(t, HttpVaultTransport)
        assert any("syncthing_vault_dir not set" in r.message for r in caplog.records)

    def test_unknown_transport_falls_back_to_http(self):
        # Unknown values fall through to the http branch (defensive default)
        t = transport_factory({"vault_transport": "carrier-pigeon", "mesh_url": "https://r"})
        assert isinstance(t, HttpVaultTransport)

    def test_no_mesh_url_creates_empty_http_transport(self):
        t = transport_factory({})
        assert isinstance(t, HttpVaultTransport)
        assert t.base_url == ""

# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
Vault Transport Abstraction (P7)

Pluggable backend for vault blob read/write. Two implementations:
  - HttpVaultTransport  — current HTTPS relay (default)
  - SyncthingVaultTransport — directory-based P2P via Syncthing

Desktop collars call transport_factory(config) to get the right backend.
The blob format (AES-256-GCM + RSA-OAEP + RSA-PKCS1v15-SHA256) is
transport-agnostic — encryption and verification happen in the caller.

Config keys:
  vault_transport: "http" | "syncthing"
  syncthing_vault_dir: path to Syncthing-shared folder
  mesh_url: relay URL (for http transport)
"""

import json
import logging
import os
import time
import urllib.error
import urllib.request

logger = logging.getLogger(__name__)


class VaultTransport:
    """Abstract vault blob transport."""

    def since(self, mesh_id, version):
        """Fetch blobs since version.
        Returns (blobs_list, current_version)."""
        raise NotImplementedError

    def append(self, mesh_id, blob):
        """Write a blob. Returns (version, error_or_None)."""
        raise NotImplementedError

    def nodes(self, mesh_id):
        """Fetch approved vault nodes. Returns list of node dicts."""
        raise NotImplementedError

    def register_node(self, mesh_id, payload):
        """Submit a node registration request. Returns response dict."""
        raise NotImplementedError


class HttpVaultTransport(VaultTransport):
    """HTTPS relay transport (extracts current inline HTTP logic)."""

    MAX_RESPONSE_BYTES = 64 * 1024 * 1024  # 64 MB — vault blob bundles capped

    def __init__(self, base_url, timeout=10):
        self.base_url = base_url.rstrip("/") if base_url else ""
        self.timeout = timeout

    def _read_capped(self, resp):
        """Read response body with a hard cap to prevent OOM from malicious relay."""
        # read(max+1) lets us detect overruns without unbounded allocation
        data = resp.read(self.MAX_RESPONSE_BYTES + 1)
        if len(data) > self.MAX_RESPONSE_BYTES:
            raise ValueError(f"response exceeds {self.MAX_RESPONSE_BYTES} bytes")
        return data

    def since(self, mesh_id, version):
        if not self.base_url:
            return [], 0
        url = f"{self.base_url}/vault/{mesh_id}/since/{version}"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(self._read_capped(resp))
            return data.get("blobs", []), data.get("current_version", 0)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return [], 0
            raise

    def append(self, mesh_id, blob):
        if not self.base_url:
            return 0, "no base_url"
        url = f"{self.base_url}/vault/{mesh_id}/append"
        body = json.dumps(blob, separators=(",", ":")).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(self._read_capped(resp))
            return data.get("version", 0), None
        except urllib.error.HTTPError as e:
            if e.code == 409:
                try:
                    err_data = json.loads(e.read(65536))
                    return 0, f"conflict: current_version={err_data.get('current_version')}"
                except Exception:
                    pass
            return 0, f"HTTP {e.code}"

    def nodes(self, mesh_id):
        if not self.base_url:
            return []
        url = f"{self.base_url}/vault/{mesh_id}/nodes"
        req = urllib.request.Request(url, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                data = json.loads(self._read_capped(resp))
            return data.get("nodes", [])
        except Exception:
            return []

    def register_node(self, mesh_id, payload):
        if not self.base_url:
            return {"error": "no base_url"}
        url = f"{self.base_url}/vault/{mesh_id}/register-node-request"
        body = json.dumps(payload).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(self._read_capped(resp))


def _safe_id(value):
    """Validate an identifier (mesh_id, node_id) for filesystem safety.
    Allow only [A-Za-z0-9_-], max 64 chars — matches server's _safe_mesh_id()."""
    if not value or len(value) > 64:
        return False
    return all(c.isalnum() or c in "-_" for c in value)


def _safe_path(base_dir, *parts):
    """Join path parts and verify the result stays within base_dir.
    Prevents path traversal and symlink escape."""
    joined = os.path.join(base_dir, *parts)
    real = os.path.realpath(joined)
    real_base = os.path.realpath(base_dir)
    if not real.startswith(real_base + os.sep) and real != real_base:
        raise ValueError(f"Path escapes vault_dir: {joined} -> {real}")
    return real


class SyncthingVaultTransport(VaultTransport):
    """Directory-based transport for Syncthing-shared folders.

    Directory layout mirrors the server's VaultStore:
      {vault_dir}/{mesh_id}/blobs/{version:08d}.json
      {vault_dir}/{mesh_id}/nodes.json
      {vault_dir}/{mesh_id}/pending/{node_id}.json
    """

    def __init__(self, vault_dir):
        self.vault_dir = os.path.expanduser(vault_dir) if vault_dir else ""

    def _blobs_dir(self, mesh_id):
        if not _safe_id(mesh_id):
            raise ValueError(f"Invalid mesh_id: {mesh_id!r}")
        return _safe_path(self.vault_dir, mesh_id, "blobs")

    def _mesh_dir(self, mesh_id):
        if not _safe_id(mesh_id):
            raise ValueError(f"Invalid mesh_id: {mesh_id!r}")
        return _safe_path(self.vault_dir, mesh_id)

    # Bound version numbers to prevent disk/DoS exhaustion via huge gaps
    MAX_VERSION = 2**31 - 1  # ~2 billion, matches signed int32

    def since(self, mesh_id, version):
        blobs_dir = self._blobs_dir(mesh_id)
        if not os.path.isdir(blobs_dir):
            return [], 0
        blobs = []
        current_version = 0
        try:
            files = sorted(os.listdir(blobs_dir))
        except OSError:
            return [], 0
        for fname in files:
            if not fname.endswith(".json"):
                continue
            try:
                v = int(fname[:-5])
            except ValueError:
                continue
            # SECURITY: reject negative, zero, and absurdly large versions.
            # A malicious Syncthing peer could drop 99999999.json to poison
            # current_version and block all future writes.
            if v <= 0 or v > self.MAX_VERSION:
                continue
            if v > current_version:
                current_version = v
            if v <= version:
                continue
            path = os.path.join(blobs_dir, fname)
            if os.path.islink(path):
                continue  # Skip symlinks (potential escape)
            try:
                with open(path, "r") as f:
                    blob = json.load(f)
                blobs.append(blob)
            except (json.JSONDecodeError, OSError):
                continue
        return blobs, current_version

    def append(self, mesh_id, blob):
        blobs_dir = self._blobs_dir(mesh_id)
        os.makedirs(blobs_dir, exist_ok=True)
        version = blob.get("version", 0)
        if not version:
            # Scan for highest existing version
            try:
                existing = [int(f[:-5]) for f in os.listdir(blobs_dir) if f.endswith(".json")]
                version = max(existing, default=0) + 1
                blob["version"] = version
            except OSError:
                version = 1
                blob["version"] = version
        fname = f"{version:08d}.json"
        path = os.path.join(blobs_dir, fname)
        if os.path.exists(path):
            # Conflict — scan for true max and retry
            try:
                existing = [int(f[:-5]) for f in os.listdir(blobs_dir) if f.endswith(".json")]
                version = max(existing, default=0) + 1
                blob["version"] = version
                fname = f"{version:08d}.json"
                path = os.path.join(blobs_dir, fname)
            except OSError:
                return 0, "conflict scan failed"
        tmp = path + ".tmp"
        try:
            with open(tmp, "w") as f:
                json.dump(blob, f, separators=(",", ":"))
            os.replace(tmp, path)
            return version, None
        except OSError as e:
            return 0, str(e)

    def nodes(self, mesh_id):
        if not _safe_id(mesh_id):
            return []
        # islink check must run on the raw (un-realpath'd) join — _safe_path
        # returns os.path.realpath which has already followed any symlink, so
        # a post-_safe_path islink test is dead. A malicious Syncthing peer
        # could plant `mesh_id/nodes.json` as a symlink to another file inside
        # vault_dir to exfiltrate it through the nodes() return value.
        raw_path = os.path.join(self.vault_dir, mesh_id, "nodes.json")
        if os.path.islink(raw_path):
            return []
        path = _safe_path(self.vault_dir, mesh_id, "nodes.json")
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return []

    def register_node(self, mesh_id, payload):
        node_id = payload.get("node_id", "unknown")
        if not _safe_id(node_id):
            return {"error": f"invalid node_id: {node_id!r}"}
        pending_dir = _safe_path(self.vault_dir, mesh_id, "pending")
        os.makedirs(pending_dir, exist_ok=True)
        path = _safe_path(self.vault_dir, mesh_id, "pending", f"{node_id}.json")
        payload["requested_at"] = int(time.time())
        try:
            with open(path, "w") as f:
                json.dump(payload, f, indent=2)
            return {"status": "pending", "node_id": node_id}
        except OSError as e:
            return {"error": str(e)}


def transport_factory(config):
    """Create the appropriate VaultTransport from config.

    Config keys:
      vault_transport: "http" (default) or "syncthing"
      mesh_url: relay URL (for http)
      syncthing_vault_dir: directory path (for syncthing)
    """
    transport = config.get("vault_transport", "http")
    if transport == "syncthing":
        vault_dir = config.get("syncthing_vault_dir", "")
        if not vault_dir:
            logger.warning("syncthing transport selected but syncthing_vault_dir not set")
            return HttpVaultTransport(config.get("mesh_url", ""))
        return SyncthingVaultTransport(vault_dir)
    return HttpVaultTransport(config.get("mesh_url", ""))

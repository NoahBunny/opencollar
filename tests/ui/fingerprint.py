# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""Bunny-pubkey fingerprint helper for UI tests.

Mirrors the reference implementations byte-for-byte:
  - android/companion/src/com/bunnytasker/PairingManager.java::getFingerprint
  - android/controller/src/com/focusctl/MainActivity.java::computeBunnyFingerprint

If either Java reference changes, update this together or the §1a
direct-pair test will produce a fingerprint the controller rejects.
"""

from __future__ import annotations

import base64
import hashlib


def compute_fingerprint(pubkey_b64: str) -> str:
    """SHA-256 over the raw DER pubkey bytes, first 8 bytes as lowercase hex (16 chars)."""
    raw = base64.b64decode(pubkey_b64)
    return hashlib.sha256(raw).hexdigest()[:16]

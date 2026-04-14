# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Configuration Loader

Loads config from a JSON file with env var overrides.
Used by all desktop/server Python scripts.

Config search order:
  1. Path in FOCUSLOCK_CONFIG env var
  2. Platform default:
     - Windows: %APPDATA%\\focuslock\\config.json
     - Linux:   ~/.config/focuslock/config.json
     - Server:  /opt/focuslock/config.json (if /opt/focuslock exists)

Env var overrides use FOCUSLOCK_ prefix:
  FOCUSLOCK_PIN        -> config["pin"]
  FOCUSLOCK_MESH_URL   -> config["mesh_url"]
  FOCUSLOCK_HOMELAB_URL -> config["homelab_url"]
  etc.
"""

import json
import os
import sys

# ── Defaults ──

DEFAULTS = {
    "pin": "",
    "mesh_url": "",
    "homelab_url": "",
    "phone_addresses": [],
    "mesh_port": 8435,
    "phone_port": 8432,
    "homelab_port": 8434,
    "poll_interval": 5,
    "gossip_interval": 10,
    "mail": {
        "imap_host": "",
        "smtp_host": "",
        "user": "",
        "pass": "",
        "partner_email": "",
    },
    "admin_token": "",
    "ntfy_enabled": False,
    "ntfy_server": "https://ntfy.sh",
    "ntfy_topic": "",
    "vault_transport": "http",
    "syncthing_vault_dir": "",
    "banking": {
        "app_packages": [],
        "payment_url": "",
        "currency_symbols": ["$"],
        "min_payment": 0.01,
        "max_payment": 10000,
        "payment_keywords": [
            "transfer",
            "deposit",
            "deposited",
            "received",
            "sent you money",
            "payment",
            "credit",
            "incoming",
            "e-transfer",
            "etransfer",
            "autodeposit",
            "direct deposit",
            "virement",
            "déposé",
            "reçu",
        ],
    },
}

# ── ENV var mapping ──
# Maps FOCUSLOCK_X env var to config key path.
# Nested keys use double underscore: FOCUSLOCK_MAIL__IMAP_HOST -> mail.imap_host

_ENV_MAP = {
    "FOCUSLOCK_PIN": "pin",
    "FOCUSLOCK_MESH_URL": "mesh_url",
    "FOCUSLOCK_HOMELAB_URL": "homelab_url",
    "FOCUSLOCK_HOMELAB": "homelab_url",  # legacy alias
    "FOCUSLOCK_PHONE_ADDRESSES": "phone_addresses",  # comma-separated
    "FOCUSLOCK_MESH_PORT": "mesh_port",
    "FOCUSLOCK_PHONE_PORT": "phone_port",
    "FOCUSLOCK_HOMELAB_PORT": "homelab_port",
    "FOCUSLOCK_POLL_INTERVAL": "poll_interval",
    "FOCUSLOCK_GOSSIP_INTERVAL": "gossip_interval",
    # Mail
    "MAIL_HOST": "mail.imap_host",
    "SMTP_HOST": "mail.smtp_host",
    "MAIL_USER": "mail.user",
    "MAIL_PASS": "mail.pass",
    "PARTNER_EMAIL": "mail.partner_email",
    # Banking
    "FOCUSLOCK_BANKING_URL": "banking.payment_url",
    # Admin
    "FOCUSLOCK_ADMIN_TOKEN": "admin_token",
    # ntfy
    "FOCUSLOCK_NTFY_ENABLED": "ntfy_enabled",
    "FOCUSLOCK_NTFY_SERVER": "ntfy_server",
    "FOCUSLOCK_NTFY_TOPIC": "ntfy_topic",
    # Transport (P7)
    "FOCUSLOCK_VAULT_TRANSPORT": "vault_transport",
    "FOCUSLOCK_SYNCTHING_VAULT_DIR": "syncthing_vault_dir",
    # Legacy
    "PHONE_PIN": "pin",
    "PHONE_URL": "_phone_url",  # parsed specially
}

_INT_KEYS = {"mesh_port", "phone_port", "homelab_port", "poll_interval", "gossip_interval"}
_BOOL_KEYS = {"ntfy_enabled"}


def _platform_config_path():
    """Return the default config path for this platform."""
    if sys.platform == "win32":
        appdata = os.environ.get("APPDATA", os.path.expanduser("~"))
        return os.path.join(appdata, "focuslock", "config.json")
    # Linux — prefer /opt/focuslock for server installs
    if os.path.isdir("/opt/focuslock"):
        opt_conf = "/opt/focuslock/config.json"
        if os.path.exists(opt_conf):
            return opt_conf
    return os.path.expanduser("~/.config/focuslock/config.json")


def _deep_merge(base, override):
    """Merge override dict into base dict recursively."""
    result = dict(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = v
    return result


def _set_nested(d, key_path, value):
    """Set a nested dict value. key_path is 'a.b.c' -> d['a']['b']['c'] = value."""
    parts = key_path.split(".")
    for part in parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    d[parts[-1]] = value


def _apply_env_overrides(config):
    """Override config values from environment variables."""
    for env_key, config_key in _ENV_MAP.items():
        val = os.environ.get(env_key)
        if val is None:
            continue

        if config_key == "_phone_url":
            # Legacy PHONE_URL: extract address and port
            # e.g. "http://192.168.1.50:8432" -> phone_addresses=["192.168.1.50"], phone_port=8432
            try:
                from urllib.parse import urlparse

                parsed = urlparse(val)
                if parsed.hostname:
                    config["phone_addresses"] = [parsed.hostname]
                if parsed.port:
                    config["phone_port"] = parsed.port
            except Exception:
                pass
            continue

        # Type coercion
        leaf_key = config_key.split(".")[-1]
        if leaf_key in _BOOL_KEYS:
            val = val.lower() in ("1", "true", "yes")
        elif leaf_key in _INT_KEYS:
            try:
                val = int(val)
            except ValueError:
                continue
        elif config_key == "phone_addresses":
            val = [a.strip() for a in val.split(",") if a.strip()]

        _set_nested(config, config_key, val)


def load_config(config_path=None):
    """
    Load FocusLock configuration.

    Args:
        config_path: Explicit path to config.json. If None, auto-detect.

    Returns:
        dict with all config values (defaults + file + env overrides)
    """
    import copy

    config = copy.deepcopy(DEFAULTS)

    # Determine config file path
    path = config_path or os.environ.get("FOCUSLOCK_CONFIG") or _platform_config_path()

    # Load from file if it exists
    if path and os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                file_config = json.load(f)
            config = _deep_merge(config, file_config)
        except (OSError, json.JSONDecodeError) as e:
            print(f"[config] WARNING: Failed to load {path}: {e}")

    # Apply env var overrides
    _apply_env_overrides(config)

    return config


def save_config(config, config_path=None):
    """Save config to JSON file."""
    path = config_path or os.environ.get("FOCUSLOCK_CONFIG") or _platform_config_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)
    print(f"[config] Saved to {path}")


def require_pin(config):
    """Validate that PIN is configured. Returns the PIN or exits."""
    pin = config.get("pin", "")
    if not pin:
        print("[config] ERROR: No mesh PIN configured.")
        print("[config] Set 'pin' in config.json or FOCUSLOCK_PIN env var.")
        return ""
    return pin

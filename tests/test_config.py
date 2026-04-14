"""Tests for shared/focuslock_config.py — config loading + env overrides."""

import json
import os

from focuslock_config import (
    DEFAULTS,
    _apply_env_overrides,
    _deep_merge,
    _platform_config_path,
    _set_nested,
    load_config,
    require_pin,
    save_config,
)

# ── _deep_merge ──


class TestDeepMerge:
    def test_override_replaces_scalar(self):
        assert _deep_merge({"a": 1}, {"a": 2}) == {"a": 2}

    def test_override_adds_new_keys(self):
        assert _deep_merge({"a": 1}, {"b": 2}) == {"a": 1, "b": 2}

    def test_nested_merge(self):
        base = {"mail": {"host": "h", "port": 25}}
        override = {"mail": {"host": "new"}}
        merged = _deep_merge(base, override)
        assert merged == {"mail": {"host": "new", "port": 25}}

    def test_list_replaced_not_merged(self):
        # Lists are not deep-merged; they replace
        merged = _deep_merge({"x": [1, 2]}, {"x": [3]})
        assert merged == {"x": [3]}

    def test_base_unchanged(self):
        base = {"a": {"b": 1}}
        _deep_merge(base, {"a": {"b": 2}})
        assert base == {"a": {"b": 1}}  # original not mutated


# ── _set_nested ──


class TestSetNested:
    def test_shallow(self):
        d = {}
        _set_nested(d, "key", "value")
        assert d == {"key": "value"}

    def test_nested_creates_intermediates(self):
        d = {}
        _set_nested(d, "a.b.c", 42)
        assert d == {"a": {"b": {"c": 42}}}

    def test_nested_overwrites_non_dict(self):
        d = {"a": "scalar"}
        _set_nested(d, "a.b", 1)
        assert d == {"a": {"b": 1}}

    def test_nested_preserves_siblings(self):
        d = {"a": {"x": 1}}
        _set_nested(d, "a.y", 2)
        assert d == {"a": {"x": 1, "y": 2}}


# ── _apply_env_overrides ──


class TestApplyEnvOverrides:
    def test_scalar_override(self, monkeypatch):
        monkeypatch.setenv("FOCUSLOCK_PIN", "9999")
        cfg = {"pin": "old"}
        _apply_env_overrides(cfg)
        assert cfg["pin"] == "9999"

    def test_int_coercion(self, monkeypatch):
        monkeypatch.setenv("FOCUSLOCK_MESH_PORT", "8888")
        cfg = {"mesh_port": 1234}
        _apply_env_overrides(cfg)
        assert cfg["mesh_port"] == 8888
        assert isinstance(cfg["mesh_port"], int)

    def test_int_coercion_failure_skipped(self, monkeypatch):
        monkeypatch.setenv("FOCUSLOCK_MESH_PORT", "not-a-number")
        cfg = {"mesh_port": 1234}
        _apply_env_overrides(cfg)
        assert cfg["mesh_port"] == 1234  # unchanged

    def test_bool_coercion(self, monkeypatch):
        for val, expected in [
            ("1", True),
            ("true", True),
            ("yes", True),
            ("0", False),
            ("no", False),
            ("anything-else", False),
        ]:
            monkeypatch.setenv("FOCUSLOCK_NTFY_ENABLED", val)
            cfg = {"ntfy_enabled": False}
            _apply_env_overrides(cfg)
            assert cfg["ntfy_enabled"] is expected, f"val={val}"

    def test_nested_mail_override(self, monkeypatch):
        monkeypatch.setenv("MAIL_HOST", "imap.example.com")
        monkeypatch.setenv("MAIL_USER", "alice@example.com")
        cfg = {"mail": {"imap_host": "", "user": ""}}
        _apply_env_overrides(cfg)
        assert cfg["mail"]["imap_host"] == "imap.example.com"
        assert cfg["mail"]["user"] == "alice@example.com"

    def test_phone_addresses_comma_split(self, monkeypatch):
        monkeypatch.setenv("FOCUSLOCK_PHONE_ADDRESSES", "10.0.0.1, 10.0.0.2 ,10.0.0.3")
        cfg = {"phone_addresses": []}
        _apply_env_overrides(cfg)
        assert cfg["phone_addresses"] == ["10.0.0.1", "10.0.0.2", "10.0.0.3"]

    def test_phone_addresses_empty_filtered(self, monkeypatch):
        monkeypatch.setenv("FOCUSLOCK_PHONE_ADDRESSES", ",,  ,")
        cfg = {"phone_addresses": ["old"]}
        _apply_env_overrides(cfg)
        assert cfg["phone_addresses"] == []

    def test_legacy_phone_url_parsed(self, monkeypatch):
        monkeypatch.setenv("PHONE_URL", "http://192.168.1.50:8432")
        cfg = {}
        _apply_env_overrides(cfg)
        assert cfg["phone_addresses"] == ["192.168.1.50"]
        assert cfg["phone_port"] == 8432

    def test_legacy_phone_url_malformed(self, monkeypatch):
        monkeypatch.setenv("PHONE_URL", "not-a-url-at-all")
        cfg = {}
        _apply_env_overrides(cfg)
        # Should not crash; phone_addresses may or may not be set

    def test_missing_env_does_nothing(self, monkeypatch):
        monkeypatch.delenv("FOCUSLOCK_PIN", raising=False)
        cfg = {"pin": "existing"}
        _apply_env_overrides(cfg)
        assert cfg["pin"] == "existing"


# ── load_config ──


class TestLoadConfig:
    def test_returns_defaults_when_no_file(self, tmp_path, monkeypatch):
        # Point to a nonexistent file and clear all FOCUSLOCK_ env vars
        monkeypatch.setenv("FOCUSLOCK_CONFIG", str(tmp_path / "missing.json"))
        for k in list(os.environ):
            if k.startswith("FOCUSLOCK_") and k != "FOCUSLOCK_CONFIG":
                monkeypatch.delenv(k, raising=False)
            if k in ("MAIL_HOST", "SMTP_HOST", "MAIL_USER", "MAIL_PASS", "PARTNER_EMAIL", "PHONE_PIN", "PHONE_URL"):
                monkeypatch.delenv(k, raising=False)
        cfg = load_config()
        assert cfg["pin"] == DEFAULTS["pin"]
        assert cfg["mesh_port"] == DEFAULTS["mesh_port"]
        assert cfg["mail"]["imap_host"] == ""

    def test_loads_from_explicit_path(self, tmp_path):
        path = tmp_path / "cfg.json"
        path.write_text(
            json.dumps(
                {
                    "pin": "1234",
                    "mesh_url": "https://example.com",
                    "mail": {"imap_host": "imap.test"},
                }
            )
        )
        cfg = load_config(config_path=str(path))
        assert cfg["pin"] == "1234"
        assert cfg["mesh_url"] == "https://example.com"
        assert cfg["mail"]["imap_host"] == "imap.test"
        # Defaults still populated
        assert cfg["mesh_port"] == DEFAULTS["mesh_port"]

    def test_file_malformed_falls_back_to_defaults(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        cfg = load_config(config_path=str(path))
        # Defaults returned intact
        assert cfg["pin"] == ""
        assert cfg["mesh_port"] == 8435

    def test_env_overrides_file(self, tmp_path, monkeypatch):
        path = tmp_path / "cfg.json"
        path.write_text(json.dumps({"pin": "file-pin"}))
        monkeypatch.setenv("FOCUSLOCK_PIN", "env-pin")
        cfg = load_config(config_path=str(path))
        assert cfg["pin"] == "env-pin"

    def test_env_var_for_config_path(self, tmp_path, monkeypatch):
        path = tmp_path / "via-env.json"
        path.write_text(json.dumps({"pin": "via-env"}))
        monkeypatch.setenv("FOCUSLOCK_CONFIG", str(path))
        cfg = load_config()  # no explicit path
        assert cfg["pin"] == "via-env"


# ── save_config ──


class TestSaveConfig:
    def test_roundtrip(self, tmp_path):
        path = tmp_path / "nested" / "config.json"
        cfg = {"pin": "9999", "mail": {"user": "test@x"}}
        save_config(cfg, config_path=str(path))
        assert path.exists()
        # Reload
        loaded = load_config(config_path=str(path))
        assert loaded["pin"] == "9999"
        assert loaded["mail"]["user"] == "test@x"

    def test_creates_parent_dir(self, tmp_path):
        path = tmp_path / "a" / "b" / "c" / "config.json"
        save_config({"pin": "x"}, config_path=str(path))
        assert path.exists()


# ── require_pin ──


class TestRequirePin:
    def test_returns_pin_when_configured(self):
        assert require_pin({"pin": "1234"}) == "1234"

    def test_returns_empty_when_missing(self):
        assert require_pin({}) == ""
        assert require_pin({"pin": ""}) == ""


# ── _platform_config_path ──


class TestPlatformConfigPath:
    def test_returns_a_path_on_current_platform(self):
        """Smoke check: returns a string path, doesn't crash."""
        p = _platform_config_path()
        assert isinstance(p, str)
        assert p.endswith("config.json")

    def test_linux_home_path_when_no_opt(self, monkeypatch):
        """On Linux without /opt/focuslock, returns ~/.config path."""
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("os.path.isdir", lambda p: False)
        p = _platform_config_path()
        assert ".config/focuslock/config.json" in p

    def test_linux_opt_path_when_exists(self, monkeypatch):
        """On Linux with /opt/focuslock/config.json, returns that."""
        monkeypatch.setattr("sys.platform", "linux")
        monkeypatch.setattr("os.path.isdir", lambda p: p == "/opt/focuslock")
        monkeypatch.setattr("os.path.exists", lambda p: p == "/opt/focuslock/config.json")
        assert _platform_config_path() == "/opt/focuslock/config.json"

    def test_windows_uses_appdata(self, monkeypatch):
        monkeypatch.setattr("sys.platform", "win32")
        monkeypatch.setenv("APPDATA", "C:\\Users\\Test\\AppData\\Roaming")
        p = _platform_config_path()
        assert "focuslock" in p
        assert p.endswith("config.json")

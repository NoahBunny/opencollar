"""Tests for shared/focuslock_evidence.py — evidence email + notif preferences."""

from unittest.mock import MagicMock, patch

from focuslock_evidence import _TYPE_TO_PREF, get_notif_pref, send_evidence


class _DictOrders:
    """Minimal mesh_orders stand-in — only .get() is used."""

    def __init__(self, store=None):
        self._store = store or {}

    def get(self, key, default=None):
        return self._store.get(key, default)


# ── get_notif_pref ──


class TestGetNotifPref:
    def test_mesh_orders_explicitly_enabled(self):
        orders = _DictOrders({"notif_email_evidence": "1"})
        assert get_notif_pref("email_evidence", orders) is True

    def test_mesh_orders_explicitly_disabled(self):
        orders = _DictOrders({"notif_email_evidence": "0"})
        assert get_notif_pref("email_evidence", orders) is False

    def test_mesh_orders_truthy_string_treated_as_enabled(self):
        """Any non-"0" string counts as enabled — only literal "0" disables."""
        orders = _DictOrders({"notif_email_evidence": "true"})
        assert get_notif_pref("email_evidence", orders) is True

    def test_default_enabled_when_unset_and_no_adb(self):
        orders = _DictOrders({})
        assert get_notif_pref("email_evidence", orders) is True

    def test_adb_fallback_enabled(self):
        orders = _DictOrders({})
        adb = MagicMock()
        adb.get.return_value = "1"
        assert get_notif_pref("email_evidence", orders, adb) is True
        adb.get.assert_called_once_with("focus_lock_notif_email_evidence")

    def test_adb_fallback_disabled(self):
        orders = _DictOrders({})
        adb = MagicMock()
        adb.get.return_value = "0"
        assert get_notif_pref("email_evidence", orders, adb) is False

    def test_adb_exception_falls_back_to_default_true(self):
        orders = _DictOrders({})
        adb = MagicMock()
        adb.get.side_effect = OSError("adb dead")
        assert get_notif_pref("email_evidence", orders, adb) is True

    def test_mesh_orders_takes_precedence_over_adb(self):
        """If mesh_orders has the key, ADB isn't even consulted."""
        orders = _DictOrders({"notif_email_evidence": "0"})
        adb = MagicMock()
        adb.get.return_value = "1"  # would say enabled, but ignored
        assert get_notif_pref("email_evidence", orders, adb) is False
        assert adb.get.call_count == 0


# ── _TYPE_TO_PREF mapping ──


class TestTypeToPrefMapping:
    """Pin the evidence_type → notif-pref-key map so a typo in either side surfaces."""

    def test_evidence_types_map_to_email_evidence(self):
        for etype in (
            "compliment",
            "gratitude",
            "love letter",
            "photo task passed",
            "photo task failed",
            "bunny message",
            "exercise",
        ):
            assert _TYPE_TO_PREF[etype] == "email_evidence"

    def test_escape_types_map_to_email_escape(self):
        for etype in ("self-lock", "entrap", "escape attempt"):
            assert _TYPE_TO_PREF[etype] == "email_escape"

    def test_geofence_breach_maps_to_email_breach(self):
        assert _TYPE_TO_PREF["geofence breach"] == "email_breach"


# ── send_evidence ──


class TestSendEvidenceEarlyExit:
    def test_no_partner_email_returns_false_no_smtp(self):
        with patch("focuslock_evidence.smtplib.SMTP") as smtp:
            ok = send_evidence(
                "compliment text",
                mesh_orders=_DictOrders({}),
                partner_email="",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False
        assert smtp.call_count == 0

    def test_notif_disabled_returns_false_no_smtp(self):
        orders = _DictOrders({"notif_email_evidence": "0"})
        with patch("focuslock_evidence.smtplib.SMTP") as smtp:
            ok = send_evidence(
                "compliment text",
                "compliment",
                mesh_orders=orders,
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False
        assert smtp.call_count == 0

    def test_missing_smtp_host_returns_false(self):
        with patch("focuslock_evidence.smtplib.SMTP") as smtp:
            ok = send_evidence(
                "text",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False
        assert smtp.call_count == 0

    def test_missing_mail_user_returns_false(self):
        with patch("focuslock_evidence.smtplib.SMTP") as smtp:
            ok = send_evidence(
                "text",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="",
                mail_pass="pass",
            )
        assert ok is False
        assert smtp.call_count == 0

    def test_missing_mail_pass_returns_false(self):
        with patch("focuslock_evidence.smtplib.SMTP") as smtp:
            ok = send_evidence(
                "text",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="",
            )
        assert ok is False
        assert smtp.call_count == 0


class TestSendEvidenceHappyPath:
    def _smtp_ctx_mock(self):
        """SMTP() is used as a context manager — return a (server_mock, smtp_class_mock) pair."""
        server = MagicMock()
        smtp_class = MagicMock()
        smtp_class.return_value.__enter__.return_value = server
        return server, smtp_class

    def test_compliment_sent_to_smtp(self):
        server, smtp_class = self._smtp_ctx_mock()
        with patch("focuslock_evidence.smtplib.SMTP", smtp_class):
            ok = send_evidence(
                "You are wonderful",
                "compliment",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="bunny@example.com",
                mail_pass="hunter2",
            )
        assert ok is True
        smtp_class.assert_called_once_with("smtp.example.com", 587)
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("bunny@example.com", "hunter2")
        server.send_message.assert_called_once()

    def test_message_headers_correct(self):
        server, smtp_class = self._smtp_ctx_mock()
        with patch("focuslock_evidence.smtplib.SMTP", smtp_class):
            send_evidence(
                "I am grateful",
                "gratitude",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="bunny@example.com",
                mail_pass="x",
            )
        msg = server.send_message.call_args.args[0]
        assert msg["From"] == "bunny@example.com"
        assert msg["To"] == "lion@example.com"
        assert msg["Subject"] == "Lion's Share — Gratitude Evidence"

    def test_body_contains_content_and_type(self):
        server, smtp_class = self._smtp_ctx_mock()
        with patch("focuslock_evidence.smtplib.SMTP", smtp_class):
            send_evidence(
                "Photo task verdict text",
                "photo task passed",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="bunny@example.com",
                mail_pass="x",
            )
        msg = server.send_message.call_args.args[0]
        body = msg.get_payload()[0].get_payload(decode=True).decode()
        assert "Photo task verdict text" in body
        assert "photo task passed" in body

    def test_unknown_evidence_type_uses_email_evidence_pref_default(self):
        """Type not in _TYPE_TO_PREF still routes through email_evidence default."""
        _server, smtp_class = self._smtp_ctx_mock()
        # default pref is enabled (no orders set), so it should send
        with patch("focuslock_evidence.smtplib.SMTP", smtp_class):
            ok = send_evidence(
                "unknown text",
                "totally-unknown-type",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is True

    def test_unknown_type_blocked_by_email_evidence_disable(self):
        """And disabling email_evidence disables unknown-type evidence too."""
        with patch("focuslock_evidence.smtplib.SMTP") as smtp:
            ok = send_evidence(
                "text",
                "totally-unknown-type",
                mesh_orders=_DictOrders({"notif_email_evidence": "0"}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False
        assert smtp.call_count == 0


class TestSendEvidencePrefRouting:
    """Each evidence_type routes to its mapped pref key, not the default."""

    def test_escape_attempt_blocked_by_email_escape_disable(self):
        with patch("focuslock_evidence.smtplib.SMTP") as smtp:
            ok = send_evidence(
                "ran away",
                "escape attempt",
                mesh_orders=_DictOrders({"notif_email_escape": "0"}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False
        assert smtp.call_count == 0

    def test_escape_attempt_passes_when_only_email_evidence_disabled(self):
        """email_evidence=0 must NOT disable escape attempt — different pref key."""
        server = MagicMock()
        smtp_class = MagicMock()
        smtp_class.return_value.__enter__.return_value = server
        with patch("focuslock_evidence.smtplib.SMTP", smtp_class):
            ok = send_evidence(
                "ran away",
                "escape attempt",
                mesh_orders=_DictOrders({"notif_email_evidence": "0"}),  # different pref
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is True

    def test_geofence_breach_blocked_by_email_breach_disable(self):
        with patch("focuslock_evidence.smtplib.SMTP"):
            ok = send_evidence(
                "left the geofence",
                "geofence breach",
                mesh_orders=_DictOrders({"notif_email_breach": "0"}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False


class TestSendEvidenceSmtpErrors:
    def test_smtp_exception_returns_false(self):
        with patch("focuslock_evidence.smtplib.SMTP", side_effect=OSError("smtp dead")):
            ok = send_evidence(
                "text",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False

    def test_smtp_login_exception_returns_false(self):
        server = MagicMock()
        server.login.side_effect = OSError("auth failed")
        smtp_class = MagicMock()
        smtp_class.return_value.__enter__.return_value = server
        with patch("focuslock_evidence.smtplib.SMTP", smtp_class):
            ok = send_evidence(
                "text",
                mesh_orders=_DictOrders({}),
                partner_email="lion@example.com",
                smtp_host="smtp.example.com",
                mail_user="user",
                mail_pass="pass",
            )
        assert ok is False

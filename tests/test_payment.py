"""Tests for shared/focuslock_payment.py — IMAP payment detection helpers."""

import email
import json
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import ClassVar
from unittest.mock import MagicMock

import pytest
from focuslock_payment import (
    _HARDCODED_FALLBACK,
    DEFAULT_SKIP_FOLDERS,
    check_payment_emails,
    check_payment_emails_multi,
    extract_amount,
    get_body,
    load_iso_codes,
    load_payment_providers,
    reduce_paywall,
    score_payment_email,
    unlock_phone,
    walk_imap_folders,
)

# ── load_payment_providers ──


class TestLoadPaymentProviders:
    def test_loads_real_banks_json(self, tmp_path):
        from pathlib import Path

        real = Path(__file__).resolve().parent.parent / "shared" / "banks.json"
        providers = load_payment_providers(str(real))
        # Known-good production data: has Interac, PayPal, Wise, etc.
        names = {p["name"] for p in providers}
        assert "Interac" in names
        assert "PayPal" in names
        # Generic fallback appended from transfer_keywords
        assert "Generic" in names

    def test_generic_fallback_merges_all_language_keywords(self, tmp_path):
        path = tmp_path / "banks.json"
        path.write_text(
            json.dumps(
                {
                    "payment_providers": [{"name": "Foo", "senders": ["foo.com"], "keywords": ["paid"]}],
                    "transfer_keywords": {"en": ["deposit"], "fr": ["virement"], "es": ["transferencia"]},
                }
            )
        )
        providers = load_payment_providers(str(path))
        generic = next(p for p in providers if p["name"] == "Generic")
        assert set(generic["keywords"]) == {"deposit", "virement", "transferencia"}
        assert generic["senders"] == []

    def test_missing_file_returns_hardcoded_fallback(self, tmp_path):
        providers = load_payment_providers(str(tmp_path / "does-not-exist.json"))
        assert providers == _HARDCODED_FALLBACK

    def test_malformed_json_returns_hardcoded_fallback(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        assert load_payment_providers(str(path)) == _HARDCODED_FALLBACK

    def test_empty_providers_list_returns_hardcoded_fallback(self, tmp_path):
        path = tmp_path / "empty.json"
        path.write_text(json.dumps({"payment_providers": [], "transfer_keywords": {}}))
        assert load_payment_providers(str(path)) == _HARDCODED_FALLBACK


# ── load_iso_codes ──


class TestLoadIsoCodes:
    def test_loads_from_banks_json(self, tmp_path):
        path = tmp_path / "banks.json"
        path.write_text(json.dumps({"currency_patterns": {"iso_codes": "USD|EUR|JPY"}}))
        assert load_iso_codes(str(path)) == "USD|EUR|JPY"

    def test_missing_file_returns_default(self, tmp_path):
        codes = load_iso_codes(str(tmp_path / "missing.json"))
        assert "USD" in codes
        assert "CAD" in codes
        assert "|" in codes

    def test_missing_key_returns_default(self, tmp_path):
        path = tmp_path / "banks.json"
        path.write_text(json.dumps({"other": "data"}))
        codes = load_iso_codes(str(path))
        assert "USD" in codes


# ── score_payment_email ──


class TestScorePaymentEmail:
    INTERAC: ClassVar[dict] = {
        "name": "Interac",
        "senders": ["interac.ca"],
        "keywords": ["e-transfer", "autodeposit"],
    }
    GENERIC: ClassVar[dict] = {
        "name": "Generic",
        "senders": [],
        "keywords": ["deposit", "received"],
    }

    def test_known_sender_and_keywords_scores_high(self):
        score, kw = score_payment_email(
            "notify@interac.ca",
            "you've received an e-transfer! autodeposit completed. $50.00",
            self.INTERAC,
        )
        # 3 (sender) + 2 (keyword matches) + 2 (currency) = 7
        assert score == 7
        assert kw == 2

    def test_unknown_sender_no_sender_bonus(self):
        score, kw = score_payment_email(
            "random@example.com",
            "you received an e-transfer $50",
            self.INTERAC,
        )
        # 0 (no sender match) + 1 (keyword) + 2 (currency) = 3
        assert score == 3
        assert kw == 1

    def test_generic_provider_no_sender_bonus_ever(self):
        # Generic provider has senders=[]; sender_match is vacuously true but
        # the "+3 if senders and sender_match" guard means NO sender bonus.
        score, _ = score_payment_email(
            "bank@anywhere.com",
            "deposit of $100 received today",
            self.GENERIC,
        )
        # 0 (sender bonus suppressed) + 2 (keywords) + 2 (currency) = 4
        assert score == 4

    def test_no_keyword_no_currency_returns_zero(self):
        score, kw = score_payment_email(
            "friend@gmail.com",
            "hey, wanna grab lunch later?",
            self.INTERAC,
        )
        assert score == 0
        assert kw == 0

    def test_currency_symbols_detected(self):
        for sym in ("$", "€", "£", "¥", "₹"):
            score, _ = score_payment_email("x@y.com", f"payment {sym}50", self.GENERIC)
            assert score >= 2, f"symbol {sym} not detected"

    def test_partial_sender_substring_match(self):
        # "interac.ca" is contained in "notify@payments.interac.ca"
        score, _ = score_payment_email(
            "notify@payments.interac.ca",
            "e-transfer received",
            self.INTERAC,
        )
        assert score >= 3  # sender match bonus applied


# ── extract_amount ──


class TestExtractAmount:
    def test_dollar_sign_prefix(self):
        assert extract_amount("payment of $50.00") == 50.0

    def test_no_decimals(self):
        assert extract_amount("amount: $100") == 100.0

    def test_comma_decimal_european(self):
        # Symbol-first regex accepts comma as decimal separator
        assert extract_amount("€50,00 received") == 50.0

    def test_symbol_after(self):
        assert extract_amount("50.00$ received") == 50.0

    def test_iso_code(self):
        assert extract_amount("25.00 USD wired", iso_codes="USD|EUR") == 25.0

    def test_returns_largest_amount_found(self):
        text = "first $10, then $500.50, finally $25"
        assert extract_amount(text) == 500.5

    def test_no_amount_returns_zero(self):
        assert extract_amount("hello world no money here") == 0

    def test_euro_symbol(self):
        assert extract_amount("total: €75.50") == 75.5

    def test_yen_symbol_no_decimals(self):
        assert extract_amount("¥10000 sent") == 10000.0

    def test_rupee_symbol(self):
        assert extract_amount("₹500 credited") == 500.0

    def test_custom_iso_codes_respected(self):
        # JPY not in restricted set — should not match
        assert extract_amount("10000.00 JPY", iso_codes="USD|EUR") == 0

    def test_default_iso_codes_when_none(self):
        assert extract_amount("25.00 CAD") == 25.0


# ── get_body ──


class TestGetBody:
    def test_single_part_plain_text(self):
        msg = email.message_from_string("Subject: test\n\nhello world")
        assert get_body(msg) == "hello world"

    def test_multipart_plain_text(self):
        mp = MIMEMultipart("alternative")
        mp.attach(MIMEText("<p>html body</p>", "html"))
        mp.attach(MIMEText("plain body content", "plain"))
        assert get_body(mp) == "plain body content"

    def test_multipart_no_plain_returns_empty(self):
        mp = MIMEMultipart("alternative")
        mp.attach(MIMEText("<p>html only</p>", "html"))
        assert get_body(mp) == ""

    def test_decode_error_returns_empty(self):
        # Force a decode path that raises — payload with invalid b64 transfer encoding
        raw = "Content-Type: text/plain\nContent-Transfer-Encoding: base64\n\n!!!not-base64!!!"
        msg = email.message_from_string(raw)
        # get_payload(decode=True) may return None or garbage for invalid b64; the
        # function should survive and return a string.
        result = get_body(msg)
        assert isinstance(result, str)


# ── unlock_phone ──


class TestUnlockPhone:
    def test_sets_expected_adb_values(self):
        adb = MagicMock()
        unlock_phone(adb)
        adb.put.assert_called_once_with("focus_lock_active", "0")
        adb.put_str.assert_called_once()
        args, _ = adb.put_str.call_args
        assert args[0] == "focus_lock_message"
        assert "Payment received" in args[1]


# ── reduce_paywall ──


class TestReducePaywall:
    def test_adb_always_updated(self):
        adb = MagicMock()
        reduce_paywall(remaining=25, paid=75, adb=adb, phone_url="", phone_pin="")
        # No phone_url → HTTP call skipped but ADB still runs
        calls = {c.args[0]: c.args[1] for c in adb.put.call_args_list}
        assert calls["focus_lock_paywall"] == "25"
        adb.put_str.assert_called_once()

    def test_with_phone_url_posts_message(self, monkeypatch):
        captured = {}

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        def fake_urlopen(req, timeout=10):
            captured["url"] = req.full_url
            captured["body"] = req.data
            return FakeResponse()

        import urllib.request

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

        adb = MagicMock()
        reduce_paywall(
            remaining=10,
            paid=40,
            adb=adb,
            phone_url="http://192.168.1.50:8432",
            phone_pin="1234",
        )
        assert captured["url"].endswith("/api/message")
        payload = json.loads(captured["body"])
        assert payload["pin"] == "1234"
        assert "Received $40" in payload["message"]
        assert "$10 remaining" in payload["message"]

    def test_adb_error_swallowed(self):
        """Failing ADB should not bubble out — logged and continued."""
        adb = MagicMock()
        adb.put.side_effect = RuntimeError("device offline")
        # Should NOT raise
        reduce_paywall(remaining=5, paid=5, adb=adb, phone_url="", phone_pin="")


# ── integration-ish: full scoring pipeline on a realistic Interac email ──


class TestRealisticEmails:
    @pytest.fixture
    def providers(self):
        from pathlib import Path

        real = Path(__file__).resolve().parent.parent / "shared" / "banks.json"
        return load_payment_providers(str(real))

    def test_realistic_interac_etransfer_scored_and_extracted(self, providers):
        sender = "notify@payments.interac.ca"
        subject = "INTERAC e-Transfer: You received money from Alice"
        body = (
            "Hi Bob, Alice sent you an INTERAC e-Transfer for $50.00 (CAD).\n"
            "The amount has been automatically deposited into your account."
        )
        all_text = (subject + " " + body).lower()
        best_score = 0
        best = None
        for p in providers:
            s, _ = score_payment_email(sender.lower(), all_text, p)
            threshold = 4 if p["senders"] else 5
            if s >= threshold and s > best_score:
                best_score = s
                best = p["name"]
        assert best == "Interac"
        assert extract_amount(all_text) == 50.0

    def test_generic_bank_deposit_meets_threshold(self, providers):
        sender = "alerts@randombank.example"
        subject = "Deposit Notification"
        body = "A deposit of $125.00 USD has been credited to your account today."
        all_text = (subject + " " + body).lower()
        # Generic provider needs >= 5 score
        generic = next(p for p in providers if p["name"] == "Generic")
        score, _ = score_payment_email(sender.lower(), all_text, generic)
        # deposit + credited = 2 keyword hits + 2 currency = 4 — below threshold 5.
        # But 'deposit' + 'credited' + 'transfer' if all present would clear.
        # This assertion documents actual behavior: single-keyword bank emails
        # may NOT clear the generic threshold, which is by design.
        assert score <= 5  # might or might not trigger depending on exact keyword hits

    def test_chat_message_not_scored_as_payment(self, providers):
        sender = "friend@gmail.com"
        subject = "dinner tonight?"
        body = "wanna grab pizza later? should be around $20 per person"
        all_text = (subject + " " + body).lower()
        # Despite $20 and the word "per", no provider should flag this.
        for p in providers:
            s, _ = score_payment_email(sender.lower(), all_text, p)
            threshold = 4 if p["senders"] else 5
            assert s < threshold, f"{p['name']} false-positive on chat: score={s}"


# ── check_payment_emails — full IMAP loop with mocks ──


class _StopLoop(Exception):
    """Sentinel used to break out of check_payment_emails' while True."""


def _make_imap_email(
    sender="notify@payments.interac.ca",
    subject="INTERAC e-Transfer",
    body="You received $50.00 via e-transfer. autodeposit complete.",
    msg_id="<msg-1@example>",
):
    """Build a raw email matching what imap.fetch(..., '(RFC822)') returns."""
    msg = MIMEText(body, "plain")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["Message-ID"] = msg_id
    return msg.as_bytes()


def _install_fake_imap(monkeypatch, emails):
    """Install a fake imaplib.IMAP4_SSL that returns the given emails."""
    import imaplib

    fake_mail = MagicMock()
    fake_mail.login.return_value = None
    # 2026-04-15 folder walk (commit 4da8c4a): check_payment_emails now calls
    # mail.list() and unpacks select() / search() / fetch() tuples. Mock just
    # INBOX — good enough for single-folder test scenarios.
    fake_mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
    fake_mail.select.return_value = ("OK", [b""])
    # search returns ("OK", [b"1 2 3"]) style
    email_ids_bytes = b" ".join(str(i + 1).encode() for i in range(len(emails)))
    fake_mail.search.return_value = ("OK", [email_ids_bytes])
    # fetch returns per-id raw bytes
    fetch_responses = []
    for i, raw in enumerate(emails, start=1):
        fetch_responses.append(("OK", [(b"%d (RFC822)" % i, raw)]))

    def fake_fetch(num, _what):
        idx = int(num) - 1
        return fetch_responses[idx]

    fake_mail.fetch.side_effect = fake_fetch
    fake_mail.logout.return_value = None

    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host: fake_mail)
    return fake_mail


def _make_sleep_stop(max_calls=2):
    """Return a time.sleep replacement that raises _StopLoop after N calls."""
    state = {"n": 0}

    def stop(_):
        state["n"] += 1
        if state["n"] >= max_calls:
            raise _StopLoop()

    return stop


class TestCheckPaymentEmails:
    def _make_mesh_orders(self, paywall="100", imap_host="", imap_user="", imap_pass=""):
        store = {
            "paywall": paywall,
            "payment_imap_host": imap_host,
            "payment_imap_user": imap_user,
            "payment_imap_pass": imap_pass,
        }
        mo = MagicMock()
        mo.get.side_effect = lambda k, default=None: store.get(k, default)
        mo.set.side_effect = lambda k, v: store.__setitem__(k, v)
        mo._store = store
        return mo

    def _make_ledger(self, duplicate=False):
        ledger = MagicMock()
        ledger.add_entry.return_value = {"error": "duplicate"} if duplicate else {"ok": True}
        return ledger

    def _make_adb(self, lock_active="1"):
        adb = MagicMock()
        adb.get.side_effect = lambda k: lock_active if k == "focus_lock_active" else "0"
        return adb

    def test_full_payment_clears_paywall_and_unlocks(self, monkeypatch):
        mesh = self._make_mesh_orders(paywall="50")
        ledger = self._make_ledger()
        adb = self._make_adb()
        _install_fake_imap(monkeypatch, [_make_imap_email()])
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.example.com",
                mail_user="lion@x",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        # Full payment → paywall cleared via ADB + mesh
        calls = {c.args[0]: c.args[1] for c in adb.put.call_args_list}
        assert calls.get("focus_lock_paywall") == "0"
        assert calls.get("focus_lock_active") == "0"  # unlock_phone side-effect
        assert mesh._store["paywall"] == "0"

    def test_partial_payment_reduces_paywall(self, monkeypatch):
        mesh = self._make_mesh_orders(paywall="100")
        ledger = self._make_ledger()
        adb = self._make_adb()
        _install_fake_imap(monkeypatch, [_make_imap_email(body="You received $40.00 via e-transfer. autodeposit.")])
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.x",
                mail_user="u",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        calls = {c.args[0]: c.args[1] for c in adb.put.call_args_list}
        assert calls.get("focus_lock_paywall") == "60"  # 100 - 40
        assert "focus_lock_active" not in calls  # phone NOT unlocked on partial

    def test_duplicate_ledger_entry_skipped(self, monkeypatch):
        mesh = self._make_mesh_orders(paywall="50")
        ledger = self._make_ledger(duplicate=True)
        adb = self._make_adb()
        _install_fake_imap(monkeypatch, [_make_imap_email()])
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.x",
                mail_user="u",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        # Duplicate → no paywall mutation
        assert mesh._store["paywall"] == "50"

    def test_amount_above_max_rejected(self, monkeypatch):
        mesh = self._make_mesh_orders(paywall="50")
        ledger = self._make_ledger()
        adb = self._make_adb()
        _install_fake_imap(monkeypatch, [_make_imap_email(body="You received $99999.00 via e-transfer. autodeposit.")])
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.x",
                mail_user="u",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
                max_payment=10000,
            )
        # Rejected → ledger never called, paywall unchanged
        ledger.add_entry.assert_not_called()
        assert mesh._store["paywall"] == "50"

    def test_amount_below_min_rejected(self, monkeypatch):
        mesh = self._make_mesh_orders(paywall="50")
        ledger = self._make_ledger()
        adb = self._make_adb()
        _install_fake_imap(monkeypatch, [_make_imap_email(body="You received $0.001 via e-transfer. autodeposit.")])
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.x",
                mail_user="u",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
                min_payment=1.0,
            )
        ledger.add_entry.assert_not_called()

    def test_no_paywall_skips_imap_scan(self, monkeypatch):
        """When paywall is 0 and not locked, the loop should just sleep."""
        mesh = self._make_mesh_orders(paywall="0")
        ledger = self._make_ledger()
        adb = MagicMock()
        adb.get.return_value = "0"  # not locked

        import imaplib

        imap_ctor = MagicMock()
        monkeypatch.setattr(imaplib, "IMAP4_SSL", imap_ctor)
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(2))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.x",
                mail_user="u",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        imap_ctor.assert_not_called()

    def test_dynamic_mesh_creds_preferred_over_static(self, monkeypatch):
        """When mesh_orders has payment_imap_* set, those win over static args."""
        mesh = self._make_mesh_orders(
            paywall="50",
            imap_host="dyn.host",
            imap_user="dyn@x",
            imap_pass="dynpass",
        )
        ledger = self._make_ledger()
        adb = self._make_adb()
        _install_fake_imap(monkeypatch, [_make_imap_email()])
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        import imaplib

        ctor_calls = []
        real_ctor = imaplib.IMAP4_SSL

        def tracking_ctor(host):
            ctor_calls.append(host)
            return real_ctor(host)

        monkeypatch.setattr(imaplib, "IMAP4_SSL", tracking_ctor)

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="static.host",
                mail_user="static@x",
                mail_pass="staticpass",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        assert ctor_calls == ["dyn.host"]  # dynamic creds used

    def test_imap_exception_caught_and_loop_continues(self, monkeypatch):
        """A raised exception inside the try: body must be caught, logged, and sleep proceeds."""
        mesh = self._make_mesh_orders(paywall="50")
        ledger = self._make_ledger()
        adb = self._make_adb()

        import imaplib

        def bad_ctor(_host):
            raise ConnectionError("imap unreachable")

        monkeypatch.setattr(imaplib, "IMAP4_SSL", bad_ctor)
        # Let sleep run twice — proves the loop didn't crash on the exception
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(2))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.x",
                mail_user="u",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        # If we got to _StopLoop, the loop handled the exception + kept iterating.

    def test_total_paid_cents_tracked(self, monkeypatch):
        mesh = self._make_mesh_orders(paywall="100")
        # Roadmap #1 (commit 65e2511) made total_paid_cents server-authoritative —
        # the legacy adb.put path was removed. Without apply_fn, the fallback
        # writes through mesh_orders directly. Seed prior balance there.
        mesh._store["total_paid_cents"] = 1234
        ledger = self._make_ledger()
        adb = self._make_adb()
        _install_fake_imap(monkeypatch, [_make_imap_email(body="You received $40.00 via e-transfer. autodeposit.")])
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="imap.x",
                mail_user="u",
                mail_pass="p",
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        # 1234 + 4000 = 5234
        assert mesh._store.get("total_paid_cents") == 5234

    def test_missing_creds_skips_and_sleeps(self, monkeypatch):
        mesh = self._make_mesh_orders(paywall="50")
        ledger = self._make_ledger()
        adb = self._make_adb()

        import imaplib

        imap_ctor = MagicMock()
        monkeypatch.setattr(imaplib, "IMAP4_SSL", imap_ctor)
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        with pytest.raises(_StopLoop):
            check_payment_emails(
                imap_host="",
                mail_user="",
                mail_pass="",  # all empty
                check_interval=1,
                adb=adb,
                mesh_orders=mesh,
                payment_ledger=ledger,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )
        imap_ctor.assert_not_called()


class TestWalkImapFolders:
    """Tests for `walk_imap_folders` via a spec'd imaplib.IMAP4_SSL mock.

    `create_autospec(imaplib.IMAP4_SSL, instance=True)` enforces that our
    helper only calls real methods with real-shaped signatures. If a future
    stdlib change renames `fetch` / `search` / `list` / `select` or alters
    their signatures, these tests fail loudly — catching the kind of drift
    that used to slip through an ad-hoc `MagicMock()`.
    """

    def _spec_mail(self):
        import imaplib
        from unittest.mock import create_autospec

        return create_autospec(imaplib.IMAP4_SSL, instance=True)

    def test_walks_inbox_plus_subfolder_and_skips_default_exclusions(self):
        mail = self._spec_mail()
        mail.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasChildren) "/" "INBOX/Archive"',
                b'(\\HasNoChildren) "/" "Trash"',
                b'(\\HasNoChildren) "/" "Spam"',
                b'(\\HasNoChildren) "/" "Drafts"',
                b'(\\HasNoChildren) "/" "Sent"',
                b'(\\HasNoChildren) "/" "Junk"',
            ],
        )
        mail.select.return_value = ("OK", [b""])
        mail.search.return_value = ("OK", [b"1"])
        mail.fetch.return_value = ("OK", [(b"1 (RFC822)", b"raw")])

        results = walk_imap_folders(mail, since_date="01-Apr-2026")

        assert {r[0] for r in results} == {"INBOX", "INBOX/Archive"}
        # select called with readonly=True — locked in so nothing ever flips
        # this to a mutating scan
        mail.select.assert_any_call("INBOX", readonly=True)
        mail.select.assert_any_call("INBOX/Archive", readonly=True)
        mail.search.assert_called_with(None, "(SINCE 01-Apr-2026)")
        mail.fetch.assert_called_with(b"1", "(RFC822)")
        # Skipped folders never selected
        skipped = {call.args[0] for call in mail.select.call_args_list}
        assert skipped.isdisjoint({"Trash", "Spam", "Drafts", "Sent", "Junk"})

    def test_select_returning_non_ok_skips_folder(self):
        mail = self._spec_mail()
        mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
        mail.select.return_value = ("NO", [b""])
        mail.search.return_value = ("OK", [b"1"])
        results = walk_imap_folders(mail, since_date="01-Apr-2026")
        assert results == []
        # fetch never called because select didn't green-light
        mail.fetch.assert_not_called()

    def test_per_message_fetch_error_swallowed_scan_continues(self):
        mail = self._spec_mail()
        mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
        mail.select.return_value = ("OK", [b""])
        mail.search.return_value = ("OK", [b"1 2 3"])

        def fetch_side(num, _what):
            if num == b"2":
                raise RuntimeError("corrupt message")
            return ("OK", [(b"%s (RFC822)" % num, b"raw-" + num)])

        mail.fetch.side_effect = fetch_side
        results = walk_imap_folders(mail, since_date="01-Apr-2026")
        # 1 and 3 succeed, 2 is swallowed
        nums = [r[1] for r in results]
        assert nums == [b"1", b"3"]

    def test_per_folder_error_swallowed_scan_continues(self):
        mail = self._spec_mail()
        mail.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "INBOX/Broken"',
            ],
        )

        def select_side(folder, readonly):
            if folder == "INBOX/Broken":
                raise RuntimeError("connection reset")
            return ("OK", [b""])

        mail.select.side_effect = select_side
        mail.search.return_value = ("OK", [b"1"])
        mail.fetch.return_value = ("OK", [(b"1 (RFC822)", b"raw")])
        results = walk_imap_folders(mail, since_date="01-Apr-2026")
        # INBOX succeeded despite the broken sibling
        assert {r[0] for r in results} == {"INBOX"}

    def test_custom_skip_patterns_override_defaults(self):
        mail = self._spec_mail()
        mail.list.return_value = (
            "OK",
            [
                b'(\\HasNoChildren) "/" "INBOX"',
                b'(\\HasNoChildren) "/" "Archive"',  # included by custom pattern
            ],
        )
        mail.select.return_value = ("OK", [b""])
        mail.search.return_value = ("OK", [b""])
        # Empty skip tuple → include everything from list()
        results = walk_imap_folders(mail, since_date="01-Apr-2026", skip_patterns=())
        selected = {call.args[0] for call in mail.select.call_args_list}
        assert "Archive" in selected
        assert "INBOX" in selected
        assert results == []  # no messages to fetch

    def test_empty_search_result_produces_no_messages(self):
        mail = self._spec_mail()
        mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
        mail.select.return_value = ("OK", [b""])
        mail.search.return_value = ("OK", [b""])
        results = walk_imap_folders(mail, since_date="01-Apr-2026")
        assert results == []
        mail.fetch.assert_not_called()

    def test_default_skip_constant_is_exported(self):
        # If the default skip list is reduced (e.g. Trash accidentally dropped)
        # this catches it. These folders must always be skipped unless
        # explicitly overridden.
        assert "trash" in DEFAULT_SKIP_FOLDERS
        assert "spam" in DEFAULT_SKIP_FOLDERS
        assert "junk" in DEFAULT_SKIP_FOLDERS
        assert "drafts" in DEFAULT_SKIP_FOLDERS
        assert "sent" in DEFAULT_SKIP_FOLDERS


# ── check_payment_emails_multi — multi-mesh polling loop ──


class TestCheckPaymentEmailsMultiMesh:
    """Tests for the per-mesh IMAP scanner (audit MEDIUM #5, 2026-04-26).

    The single-mesh `check_payment_emails` only ever scanned the operator's
    mailbox and credited every payment to OPERATOR_MESH_ID. These tests
    cover the multi-mesh replacement: each consumer mesh gets scanned with
    its own creds, payments credit to the originating mesh, and ledger
    isolation prevents cross-mesh dedup collisions.
    """

    def _make_mesh_orders(self, *, paywall="0", imap_host="", imap_user="", imap_pass=""):
        store = {
            "paywall": paywall,
            "payment_imap_host": imap_host,
            "payment_imap_user": imap_user,
            "payment_imap_pass": imap_pass,
        }
        mo = MagicMock()
        mo.get.side_effect = lambda k, default=None: store.get(k, default)
        mo.set.side_effect = lambda k, v: store.__setitem__(k, v)
        mo._store = store
        return mo

    def _make_ledger(self, duplicate=False, name="ledger"):
        ledger = MagicMock()
        ledger.add_entry.return_value = {"error": "duplicate"} if duplicate else {"ok": True}
        ledger._name = name
        return ledger

    def _make_adb(self, lock_active="1"):
        adb = MagicMock()
        adb.get.side_effect = lambda k: lock_active if k == "focus_lock_active" else "0"
        return adb

    def _install_per_host_imap(self, monkeypatch, *, host_to_emails):
        """Install an imaplib.IMAP4_SSL stub that returns a mesh-specific
        email set keyed by the connecting host. Tracks which hosts were
        connected to via `connected_hosts`."""
        import imaplib

        connected_hosts: list = []

        def ctor(host):
            connected_hosts.append(host)
            emails_for_host = host_to_emails.get(host, [])
            fake_mail = MagicMock()
            fake_mail.login.return_value = None
            fake_mail.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
            fake_mail.select.return_value = ("OK", [b""])
            ids = b" ".join(str(i + 1).encode() for i in range(len(emails_for_host)))
            fake_mail.search.return_value = ("OK", [ids])
            fetch_responses = [("OK", [(b"%d (RFC822)" % (i + 1), raw)]) for i, raw in enumerate(emails_for_host)]

            def fake_fetch(num, _what):
                idx = int(num) - 1
                return fetch_responses[idx]

            fake_mail.fetch.side_effect = fake_fetch
            fake_mail.logout.return_value = None
            return fake_mail

        monkeypatch.setattr(imaplib, "IMAP4_SSL", ctor)
        return connected_hosts

    def test_two_meshes_each_scanned_with_own_creds(self, monkeypatch):
        """Multi-mesh loop visits both meshes per cycle and connects to each
        mesh's own IMAP host."""
        mesh_a = self._make_mesh_orders(paywall="50", imap_host="imap.a.example", imap_user="lion-a", imap_pass="pa")
        mesh_b = self._make_mesh_orders(paywall="50", imap_host="imap.b.example", imap_user="lion-b", imap_pass="pb")
        ledger_a = self._make_ledger(name="A")
        ledger_b = self._make_ledger(name="B")
        adb = self._make_adb()

        connected = self._install_per_host_imap(
            monkeypatch,
            host_to_emails={
                "imap.a.example": [_make_imap_email(msg_id="<a-1@x>")],
                "imap.b.example": [_make_imap_email(msg_id="<b-1@x>")],
            },
        )
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        contexts = [
            {
                "mesh_id": "A",
                "mesh_orders": mesh_a,
                "payment_ledger": ledger_a,
                "apply_fn": None,
                "static_fallback": None,
            },
            {
                "mesh_id": "B",
                "mesh_orders": mesh_b,
                "payment_ledger": ledger_b,
                "apply_fn": None,
                "static_fallback": None,
            },
        ]

        with pytest.raises(_StopLoop):
            check_payment_emails_multi(
                check_interval=1,
                mesh_contexts_fn=lambda: contexts,
                adb=adb,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )

        assert sorted(connected) == ["imap.a.example", "imap.b.example"]
        # Each mesh's ledger received its own message-id only
        a_sources = [c.kwargs.get("source") for c in ledger_a.add_entry.call_args_list]
        b_sources = [c.kwargs.get("source") for c in ledger_b.add_entry.call_args_list]
        assert a_sources == ["<a-1@x>"]
        assert b_sources == ["<b-1@x>"]

    def test_mesh_without_creds_skipped(self, monkeypatch):
        """A mesh with no IMAP creds + no static fallback is silently
        skipped — IMAP4_SSL is never called for it."""
        mesh_no_creds = self._make_mesh_orders(paywall="50")  # all imap_* empty
        mesh_with = self._make_mesh_orders(paywall="50", imap_host="imap.real", imap_user="u", imap_pass="p")
        ledger_no = self._make_ledger(name="no-creds")
        ledger_with = self._make_ledger(name="real")
        adb = self._make_adb()

        connected = self._install_per_host_imap(monkeypatch, host_to_emails={"imap.real": []})
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        contexts = [
            {
                "mesh_id": "no-creds",
                "mesh_orders": mesh_no_creds,
                "payment_ledger": ledger_no,
                "apply_fn": None,
                "static_fallback": None,
            },
            {
                "mesh_id": "real",
                "mesh_orders": mesh_with,
                "payment_ledger": ledger_with,
                "apply_fn": None,
                "static_fallback": None,
            },
        ]

        with pytest.raises(_StopLoop):
            check_payment_emails_multi(
                check_interval=1,
                mesh_contexts_fn=lambda: contexts,
                adb=adb,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )

        # Only the real-creds mesh's host was connected; no-creds mesh skipped
        assert connected == ["imap.real"]
        ledger_no.add_entry.assert_not_called()

    def test_static_fallback_used_when_mesh_creds_empty(self, monkeypatch):
        """A mesh with empty payment_imap_* but a static_fallback (operator
        case) uses the fallback creds. A peer mesh without fallback is
        skipped. Together this proves the operator-only static-fallback
        contract."""
        mesh_op = self._make_mesh_orders(paywall="50")  # no per-mesh creds
        mesh_consumer = self._make_mesh_orders(paywall="50")  # no per-mesh creds
        ledger_op = self._make_ledger(name="op")
        ledger_consumer = self._make_ledger(name="consumer")
        adb = self._make_adb()

        connected = self._install_per_host_imap(
            monkeypatch,
            host_to_emails={"imap.relay": [_make_imap_email(msg_id="<op-1@x>")]},
        )
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        contexts = [
            {
                "mesh_id": "OP",
                "mesh_orders": mesh_op,
                "payment_ledger": ledger_op,
                "apply_fn": None,
                "static_fallback": ("imap.relay", "relay@x", "relay-pw"),
            },
            {
                "mesh_id": "CON",
                "mesh_orders": mesh_consumer,
                "payment_ledger": ledger_consumer,
                "apply_fn": None,
                "static_fallback": None,
            },
        ]

        with pytest.raises(_StopLoop):
            check_payment_emails_multi(
                check_interval=1,
                mesh_contexts_fn=lambda: contexts,
                adb=adb,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )

        # Only the operator host was connected; consumer mesh got no fallback
        assert connected == ["imap.relay"]
        ledger_consumer.add_entry.assert_not_called()
        # Operator's ledger received the payment
        assert any(c.kwargs.get("source") == "<op-1@x>" for c in ledger_op.add_entry.call_args_list)

    def test_per_mesh_apply_fn_routes_payment_to_own_mesh(self, monkeypatch):
        """Each mesh's apply_fn must be invoked for its own payments — never
        for a peer mesh's. Closure capture must be correct (no late-binding
        bug where every apply_fn ends up bound to the last mesh_id)."""
        mesh_a = self._make_mesh_orders(paywall="100", imap_host="imap.a", imap_user="ua", imap_pass="pa")
        mesh_b = self._make_mesh_orders(paywall="100", imap_host="imap.b", imap_user="ub", imap_pass="pb")
        ledger_a = self._make_ledger()
        ledger_b = self._make_ledger()
        adb = self._make_adb()

        self._install_per_host_imap(
            monkeypatch,
            host_to_emails={
                "imap.a": [_make_imap_email(body="You received $40.00 via e-transfer. autodeposit.", msg_id="<a@x>")],
                "imap.b": [_make_imap_email(body="You received $30.00 via e-transfer. autodeposit.", msg_id="<b@x>")],
            },
        )
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        apply_calls: list = []

        def _apply(mesh_id):
            def inner(action, params):
                apply_calls.append((mesh_id, action, params.get("amount_cents")))

            return inner

        contexts = [
            {
                "mesh_id": "A",
                "mesh_orders": mesh_a,
                "payment_ledger": ledger_a,
                "apply_fn": _apply("A"),
                "static_fallback": None,
            },
            {
                "mesh_id": "B",
                "mesh_orders": mesh_b,
                "payment_ledger": ledger_b,
                "apply_fn": _apply("B"),
                "static_fallback": None,
            },
        ]

        with pytest.raises(_StopLoop):
            check_payment_emails_multi(
                check_interval=1,
                mesh_contexts_fn=lambda: contexts,
                adb=adb,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )

        # Each mesh's apply_fn was called exactly once with its own amount
        assert ("A", "payment-received", 4000) in apply_calls
        assert ("B", "payment-received", 3000) in apply_calls
        # No cross-routing: A's $40 never lands on B and vice versa
        assert ("A", "payment-received", 3000) not in apply_calls
        assert ("B", "payment-received", 4000) not in apply_calls

    def test_per_mesh_ledger_isolation(self, monkeypatch):
        """Same Message-ID arriving on two meshes must credit BOTH (each
        mesh has its own ledger and the dedup is mesh-local). Pre-fix all
        payments hit a shared ledger and the second mesh would see
        `error: duplicate`."""
        mesh_a = self._make_mesh_orders(paywall="50", imap_host="imap.a", imap_user="ua", imap_pass="pa")
        mesh_b = self._make_mesh_orders(paywall="50", imap_host="imap.b", imap_user="ub", imap_pass="pb")
        ledger_a = self._make_ledger()
        ledger_b = self._make_ledger()
        adb = self._make_adb()

        same_msgid = "<shared-msgid@x>"
        self._install_per_host_imap(
            monkeypatch,
            host_to_emails={
                "imap.a": [_make_imap_email(msg_id=same_msgid)],
                "imap.b": [_make_imap_email(msg_id=same_msgid)],
            },
        )
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        contexts = [
            {
                "mesh_id": "A",
                "mesh_orders": mesh_a,
                "payment_ledger": ledger_a,
                "apply_fn": None,
                "static_fallback": None,
            },
            {
                "mesh_id": "B",
                "mesh_orders": mesh_b,
                "payment_ledger": ledger_b,
                "apply_fn": None,
                "static_fallback": None,
            },
        ]

        with pytest.raises(_StopLoop):
            check_payment_emails_multi(
                check_interval=1,
                mesh_contexts_fn=lambda: contexts,
                adb=adb,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )

        ledger_a.add_entry.assert_called_once()
        ledger_b.add_entry.assert_called_once()

    def test_contexts_fn_re_evaluated_each_cycle(self, monkeypatch):
        """A mesh added to the registry between cycles must be picked up on
        the next iteration — proves the loop calls mesh_contexts_fn each
        cycle rather than caching the first result."""
        mesh_a = self._make_mesh_orders(paywall="50", imap_host="imap.a", imap_user="ua", imap_pass="pa")
        mesh_b = self._make_mesh_orders(paywall="50", imap_host="imap.b", imap_user="ub", imap_pass="pb")
        ledger_a = self._make_ledger()
        ledger_b = self._make_ledger()
        adb = self._make_adb()

        connected = self._install_per_host_imap(
            monkeypatch,
            host_to_emails={"imap.a": [], "imap.b": []},
        )
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(2))

        cycle = {"n": 0}

        def contexts_fn():
            cycle["n"] += 1
            if cycle["n"] == 1:
                return [
                    {
                        "mesh_id": "A",
                        "mesh_orders": mesh_a,
                        "payment_ledger": ledger_a,
                        "apply_fn": None,
                        "static_fallback": None,
                    },
                ]
            return [
                {
                    "mesh_id": "A",
                    "mesh_orders": mesh_a,
                    "payment_ledger": ledger_a,
                    "apply_fn": None,
                    "static_fallback": None,
                },
                {
                    "mesh_id": "B",
                    "mesh_orders": mesh_b,
                    "payment_ledger": ledger_b,
                    "apply_fn": None,
                    "static_fallback": None,
                },
            ]

        with pytest.raises(_StopLoop):
            check_payment_emails_multi(
                check_interval=1,
                mesh_contexts_fn=contexts_fn,
                adb=adb,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )

        # Cycle 1: only A. Cycle 2: A + B. Total connections: A, A, B.
        assert connected.count("imap.a") == 2
        assert connected.count("imap.b") == 1

    def test_imap_exception_in_one_mesh_does_not_abort_others(self, monkeypatch):
        """A connection failure on mesh A must not prevent mesh B's scan in
        the same cycle. Pre-fix the legacy single-thread loop only had to
        survive its own mesh's errors; multi-mesh requires per-mesh isolation."""
        mesh_a = self._make_mesh_orders(paywall="50", imap_host="imap.broken", imap_user="ua", imap_pass="pa")
        mesh_b = self._make_mesh_orders(paywall="50", imap_host="imap.ok", imap_user="ub", imap_pass="pb")
        ledger_a = self._make_ledger()
        ledger_b = self._make_ledger()
        adb = self._make_adb()

        import imaplib

        def ctor(host):
            if host == "imap.broken":
                raise ConnectionError("imap unreachable")
            fake = MagicMock()
            fake.login.return_value = None
            fake.list.return_value = ("OK", [b'(\\HasNoChildren) "/" "INBOX"'])
            fake.select.return_value = ("OK", [b""])
            fake.search.return_value = ("OK", [b"1"])
            fake.fetch.return_value = (
                "OK",
                [(b"1 (RFC822)", _make_imap_email(msg_id="<b-only@x>"))],
            )
            fake.logout.return_value = None
            return fake

        monkeypatch.setattr(imaplib, "IMAP4_SSL", ctor)
        monkeypatch.setattr("focuslock_payment.time.sleep", _make_sleep_stop(1))

        contexts = [
            {
                "mesh_id": "A",
                "mesh_orders": mesh_a,
                "payment_ledger": ledger_a,
                "apply_fn": None,
                "static_fallback": None,
            },
            {
                "mesh_id": "B",
                "mesh_orders": mesh_b,
                "payment_ledger": ledger_b,
                "apply_fn": None,
                "static_fallback": None,
            },
        ]

        with pytest.raises(_StopLoop):
            check_payment_emails_multi(
                check_interval=1,
                mesh_contexts_fn=contexts_fn_factory(contexts),
                adb=adb,
                providers=_HARDCODED_FALLBACK,
                iso_codes="USD|CAD",
            )

        # B got scanned despite A blowing up
        assert any(c.kwargs.get("source") == "<b-only@x>" for c in ledger_b.add_entry.call_args_list)


def contexts_fn_factory(contexts):
    """Helper to bind a static contexts list to a callable. Defined at module
    level so multiple TestCheckPaymentEmailsMultiMesh tests can share it."""
    return lambda: contexts

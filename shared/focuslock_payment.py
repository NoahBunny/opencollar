# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Payment Detection — IMAP-based payment email verification.

Monitors email for Interac e-Transfer, PayPal, and bank deposit notifications.
Uses confidence scoring (sender + keywords + amount) with configurable
thresholds.  Supports deduplication via a payment ledger and auto-unlock
on full payment.
"""

import email
import imaplib
import json
import logging
import re
import time
import urllib.request
from datetime import datetime

logger = logging.getLogger(__name__)

# ── Hardcoded fallback providers (used when banks.json is missing) ──

_HARDCODED_FALLBACK = [
    {
        "name": "Interac",
        "senders": ["interac.ca", "payments.interac", "tangerine.ca"],
        "keywords": ["e-transfer", "etransfer", "virement", "autodeposit"],
    },
    {
        "name": "PayPal",
        "senders": ["paypal.com"],
        "keywords": ["payment received", "you've got money", "sent you"],
    },
    {
        "name": "Generic",
        "senders": [],
        "keywords": [
            "deposit",
            "deposited",
            "credited",
            "received",
            "direct deposit",
            "transfer",
            "payment",
        ],
    },
]


def load_payment_providers(banks_path):
    """Load payment providers from banks.json, falling back to hardcoded defaults.

    Args:
        banks_path: Path to banks.json file.

    Returns:
        List of provider dicts with name/senders/keywords keys.
    """
    try:
        with open(banks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        providers = list(data.get("payment_providers", []))
        # Build generic fallback from all transfer_keywords across all languages
        all_keywords = []
        for lang_keywords in data.get("transfer_keywords", {}).values():
            all_keywords.extend(lang_keywords)
        if all_keywords:
            providers.append(
                {
                    "name": "Generic",
                    "senders": [],
                    "keywords": list(set(all_keywords)),
                }
            )
        return providers if providers else _HARDCODED_FALLBACK
    except Exception as e:
        logger.warning("Failed to load banks.json (%s) — using hardcoded fallback", e)
        return _HARDCODED_FALLBACK


def load_iso_codes(banks_path):
    """Load ISO currency codes from banks.json for amount extraction.

    Args:
        banks_path: Path to banks.json file.

    Returns:
        Pipe-separated string of ISO codes for regex alternation.
    """
    _default = "CAD|USD|EUR|GBP|AUD|NZD|SGD|INR|JPY|CHF|SEK|NOK|DKK|BRL|MXN|ZAR|HKD|KRW"
    try:
        with open(banks_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("currency_patterns", {}).get("iso_codes", _default)
    except Exception as e:
        logger.warning("Failed to load iso_codes from banks.json (%s) — using default set", e)
        return _default


def score_payment_email(sender, all_text, provider):
    """Score an email against a payment provider.

    Args:
        sender: Lowercase sender address/name.
        all_text: Lowercase subject + body combined text.
        provider: Provider dict with name/senders/keywords.

    Returns:
        Tuple of (score, keyword_match_count).
    """
    score = 0
    sender_match = not provider["senders"] or any(s in sender for s in provider["senders"])
    if provider["senders"] and sender_match:
        score += 3  # Known payment sender is a strong signal
    keyword_matches = sum(1 for kw in provider["keywords"] if kw in all_text)
    score += keyword_matches
    # Check for currency amount presence (adds 2 to score)
    # Note: caller should also run extract_amount(); this is a quick heuristic
    if re.search(r"[$\u20ac\u00a3\u00a5\u20b9]\s?\d", all_text):
        score += 2
    return score, keyword_matches


def extract_amount(text, iso_codes=None):
    """Extract the largest currency amount from text.

    Supports $, EUR, GBP, JPY, INR symbols and ISO codes.

    Args:
        text: Text to search (should be lowercase).
        iso_codes: Pipe-separated ISO code string for regex.

    Returns:
        Largest amount found as float, or 0.
    """
    if iso_codes is None:
        iso_codes = "CAD|USD|EUR|GBP|AUD|NZD|SGD|INR|JPY|CHF|SEK|NOK|DKK|BRL|MXN|ZAR|HKD|KRW"
    patterns = [
        r"[$\u20ac\u00a3\u00a5\u20b9]\s?(\d+[.,]?\d*)",  # Symbol-first: $50
        r"(\d+[.,]\d{2})\s?[$\u20ac\u00a3\u00a5\u20b9]",  # Symbol-after: 50.00$
        rf"(\d+[.,]\d{{2}})\s?(?:{iso_codes})",  # ISO codes
    ]
    max_amount = 0
    for pat in patterns:
        for match in re.finditer(pat, text, re.IGNORECASE):
            try:
                raw = match.group(1).replace(",", ".")
                amount = float(raw)
                if amount > max_amount:
                    max_amount = amount
            except (ValueError, IndexError):
                pass
    return max_amount


def get_body(msg):
    """Extract text body from an email message.

    Args:
        msg: email.message.Message instance.

    Returns:
        Plain text body string.
    """
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                try:
                    return part.get_payload(decode=True).decode(errors="replace")
                except Exception as e:
                    logger.debug("Failed to decode multipart body: %s", e)
    else:
        try:
            return msg.get_payload(decode=True).decode(errors="replace")
        except Exception as e:
            logger.debug("Failed to decode body: %s", e)
    return ""


def unlock_phone(adb):
    """Unlock via ADB.  Paywall persists -- only Lion can clear it.

    Args:
        adb: ADBBridge instance.
    """
    logger.info("Unlocking via ADB")
    adb.put("focus_lock_active", "0")
    adb.put_str("focus_lock_message", "Payment received. Good boy.")


def reduce_paywall(remaining, paid, adb, phone_url="", phone_pin=""):
    """Update paywall and message on the phone.

    Args:
        remaining: Remaining paywall amount after payment.
        paid: Amount that was just paid.
        adb: ADBBridge instance.
        phone_url: Phone HTTP endpoint (e.g. "http://192.168.1.5:8432").
        phone_pin: PIN for phone API auth.
    """
    try:
        msg = f"Received ${paid:.0f}. ${remaining:.0f} remaining."
        if phone_url:
            req = urllib.request.Request(
                f"{phone_url}/api/message",
                data=json.dumps({"pin": phone_pin, "message": msg}).encode(),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=10)
        # Also update the paywall setting directly via ADB
        # (The phone's PaymentListener also does this, but IMAP is faster)
        adb.put("focus_lock_paywall", f"{remaining:.0f}")
        adb.put_str("focus_lock_message", msg)
    except Exception as e:
        logger.warning("Reduce paywall failed: %s", e)


DEFAULT_SKIP_FOLDERS = ("trash", "spam", "junk", "drafts", "sent")


def walk_imap_folders(mail, since_date, skip_patterns=DEFAULT_SKIP_FOLDERS):
    """Walk INBOX + all subfolders, returning (folder, num, raw_bytes) tuples.

    Parses the `mail.list()` response, skips folders whose names contain any
    substring in `skip_patterns` (case-insensitive) — Trash/Spam/Junk/Drafts/
    Sent by default — and for each remaining folder calls `select` + `search
    (SINCE <date>)` + `fetch(num, "(RFC822)")`. Per-folder and per-message
    failures are swallowed (per-folder logged at DEBUG) so a single broken
    mailbox can't abort the whole scan.

    `mail` must quack like `imaplib.IMAP4[_SSL]`. Tested against both a real
    imaplib instance spec (`create_autospec`) and the existing INBOX-only
    MagicMock in `tests/test_payment.py`.
    """
    _, folders_raw = mail.list()
    folder_names = []
    for raw in folders_raw or []:
        if not raw:
            continue
        line = raw.decode() if isinstance(raw, bytes) else str(raw)
        # Format: (\HasNoChildren) "/" "INBOX/Archive/2026"
        parts = line.rsplit(" ", 1)
        if len(parts) < 2:
            continue
        name = parts[-1].strip().strip('"')
        low = name.lower()
        if any(s in low for s in skip_patterns):
            continue
        folder_names.append(name)

    messages = []
    for folder in folder_names:
        try:
            typ, _ = mail.select(folder, readonly=True)
            if typ != "OK":
                continue
            _, data = mail.search(None, f"(SINCE {since_date})")
            ids = data[0].split() if data and data[0] else []
            for num in ids:
                try:
                    _, msg_data = mail.fetch(num, "(RFC822)")
                    messages.append((folder, num, msg_data[0][1]))
                except Exception:
                    continue
        except Exception as e:
            logger.debug("folder %s skipped: %s", folder, e)
            continue
    return messages


def check_payment_emails(
    *,
    imap_host,
    mail_user,
    mail_pass,
    check_interval,
    adb,
    mesh_orders,
    payment_ledger,
    providers,
    iso_codes,
    min_payment=0.01,
    max_payment=10000,
    phone_url="",
    phone_pin="",
    recipient_email="",
    apply_fn=None,
):
    """Main IMAP polling loop for payment email detection.

    This function blocks forever (meant for threading).  It checks for
    unread emails matching payment providers, scores them, extracts
    amounts, deduplicates via the ledger, and triggers unlock or
    partial paywall reduction.

    SECURITY: The inbox being scanned should be the Lion's (payment
    recipient), NOT the Bunny's. If scanning the Bunny's inbox,
    set recipient_email to the Lion's email so the checker verifies
    the e-Transfer was sent TO the Lion, not to the Bunny themselves.

    Args:
        imap_host: IMAP server hostname.
        mail_user: IMAP login username (should be Lion's email).
        mail_pass: IMAP login password.
        check_interval: Seconds between IMAP checks.
        adb: ADBBridge instance for device communication.
        mesh_orders: MeshOrders instance for paywall/lock state.
        payment_ledger: PaymentLedger instance for dedup.
        providers: List of payment provider dicts.
        iso_codes: Pipe-separated ISO currency code string.
        min_payment: Minimum payment amount to accept.
        max_payment: Maximum payment amount to accept.
        phone_url: Phone HTTP endpoint for message relay.
        phone_pin: PIN for phone API auth.
        recipient_email: If set, reject e-Transfers where this email is
            NOT mentioned in the body (anti-self-pay protection).
    """

    def _now():
        return datetime.now().strftime("%H:%M:%S")

    if not imap_host or not mail_user or not mail_pass:
        logger.info("No static IMAP creds — waiting for Lion to configure via mesh")

    while True:
        try:
            # Prefer Lion-configured creds from mesh orders (hot-swappable)
            dyn_host = str(mesh_orders.get("payment_imap_host", "") or "")
            dyn_user = str(mesh_orders.get("payment_imap_user", "") or "")
            dyn_pass = str(mesh_orders.get("payment_imap_pass", "") or "")
            if dyn_host and dyn_user and dyn_pass:
                active_host, active_user, active_pass = dyn_host, dyn_user, dyn_pass
            else:
                active_host, active_user, active_pass = imap_host, mail_user, mail_pass

            if not active_host or not active_user or not active_pass:
                time.sleep(check_interval)
                continue

            # Check when locked OR when paywall > 0 (subscription charges
            # add to paywall without locking — still need to detect payment)
            lock_active = adb.get("focus_lock_active")
            paywall_str = str(mesh_orders.get("paywall", "0"))
            has_paywall = paywall_str and paywall_str != "0" and paywall_str != "null"
            if lock_active != "1" and not has_paywall:
                time.sleep(check_interval)
                continue

            if not has_paywall:
                time.sleep(check_interval)
                continue

            paywall = float(paywall_str)
            logger.info("Checking IMAP (%s) for payment >= $%s", active_user, paywall)

            mail = imaplib.IMAP4_SSL(active_host)
            mail.login(active_user, active_pass)

            since_date = (datetime.now() - __import__("datetime").timedelta(days=7)).strftime("%d-%b-%Y")
            messages = walk_imap_folders(mail, since_date)

            logger.info(
                "Found %d emails across %d folders in last 7 days", len(messages), len({m[0] for m in messages})
            )
            for folder, num, raw_msg in messages:
                msg = email.message_from_bytes(raw_msg)
                subject = str(msg.get("Subject", "")).lower()
                body = get_body(msg).lower()
                all_text = subject + " " + body
                sender = str(msg.get("From", "")).lower()

                # Score against all payment providers -- pick best match
                best_provider = None
                best_score = 0
                for provider in providers:
                    score, _kw = score_payment_email(sender, all_text, provider)
                    if score > 0:
                        logger.debug(
                            "[%s]#%s from=%s subj=%s score=%s/%s provider=%s",
                            folder,
                            num.decode(),
                            sender[:40],
                            subject[:50],
                            score,
                            4 if provider["senders"] else 5,
                            provider["name"],
                        )
                    # Thresholds: known sender needs >= 4, generic needs >= 5
                    threshold = 4 if provider["senders"] else 5
                    if score >= threshold and score > best_score:
                        best_score = score
                        best_provider = provider

                if not best_provider:
                    continue

                # Anti-self-pay: Lion configures Their IMAP creds via
                # Lion's Share → server scans Lion's inbox. "You received $X"
                # in Lion's inbox proves a genuine incoming transfer.

                amount = extract_amount(all_text, iso_codes)
                if amount < min_payment:
                    continue
                if amount > max_payment:
                    logger.warning("Ignoring suspicious amount: $%.2f (max: $%s)", amount, max_payment)
                    continue

                # Deduplicate via ledger using email Message-ID
                msg_id = msg.get("Message-ID", f"imap-{int(time.time())}-{num.decode()}")
                ledger_result = payment_ledger.add_entry(
                    entry_type="payment",
                    amount=amount,
                    source=msg_id,
                    description=f"{best_provider['name']}: ${amount:.2f}",
                )
                if ledger_result.get("error") == "duplicate":
                    continue  # Already processed

                logger.info(
                    "Payment confirmed: $%.2f via %s (score: %s, need: $%.2f)",
                    amount,
                    best_provider["name"],
                    best_score,
                    paywall,
                )

                # Notify Lion via mesh pinned message
                mesh_orders.set("pinned_message", f"Payment received: ${amount:.2f} via {best_provider['name']}")

                amount_cents = int(amount * 100)
                clear_paywall = amount >= paywall

                # Server-authoritative propagation (2026-04-15 migration).
                # When apply_fn is wired (from focuslock-mail.py's
                # _server_apply_order), the payment-received action bumps
                # total_paid_cents, optionally zeroes paywall, bumps version,
                # and writes a vault blob. Falling back to direct mutation
                # keeps the legacy bridge-only deployment path working but
                # loses vault propagation — vault_only meshes require apply_fn.
                if apply_fn is not None:
                    try:
                        apply_fn(
                            "payment-received",
                            {
                                "amount_cents": amount_cents,
                                "clear_paywall": clear_paywall,
                            },
                        )
                    except Exception as e:
                        logger.warning("payment-received apply_fn failed: %s", e)
                else:
                    try:
                        cur_cents = int(mesh_orders.get("total_paid_cents", 0) or 0)
                    except (ValueError, TypeError):
                        cur_cents = 0
                    mesh_orders.set("total_paid_cents", cur_cents + amount_cents)
                    if clear_paywall:
                        mesh_orders.set("paywall", "0")

                if clear_paywall:
                    logger.info("FULL PAYMENT — clearing paywall!")
                    adb.put("focus_lock_paywall", "0")
                    unlock_phone(adb)
                else:
                    remaining = paywall - amount
                    logger.info("Partial: $%.2f, remaining: $%.2f", amount, remaining)
                    reduce_paywall(remaining, amount, adb, phone_url, phone_pin)

            mail.logout()

        except Exception as e:
            logger.error("IMAP error: %s", e)

        time.sleep(check_interval)

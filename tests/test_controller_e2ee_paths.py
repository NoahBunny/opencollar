"""Every Lion-sent message must be encrypted for the Lion as well.

The Lion encrypts to Bunny's public key. Without a second wrap of the same AES
key for themselves they cannot decrypt their own copy, and the Inbox shows who
they wrote to and when but never what they said — `[encrypted — sent by you]`,
forever.

`MainActivity.encryptForBoth()` does both wraps. This file exists because there
were **three** separate `E2EEHelper.encrypt(...)` call sites — the vault side
channel, the server message store, and the edit path — and fixing the first one
made the Inbox look fixed while the thread the Lion actually reads was still
served by the second. A source-level invariant is the only thing that catches a
fourth call site being added later, since nothing else can tell the difference
between "encrypted for one reader" and "encrypted for two".
"""

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
MAIN = REPO_ROOT / "android" / "controller" / "src" / "com" / "focusctl" / "MainActivity.java"
HELPER = REPO_ROOT / "android" / "controller" / "src" / "com" / "focusctl" / "E2EEHelper.java"


def _encrypt_call_arities(src: str) -> list[tuple[int, int, str]]:
    """(line, argument count, source) for every `E2EEHelper.encrypt(...)`.

    Arity is the property that matters: the two-argument form encrypts for one
    reader, the three-argument form adds the sender. Counting arguments rather
    than matching call sites by name lets the helper's own — correct — direct
    call through without exempting it by position.
    """
    out = []
    for m in re.finditer(r"E2EEHelper\.encrypt\(", src):
        i = m.end()
        depth, args, start = 1, 1, i
        while i < len(src) and depth:
            c = src[i]
            if c in "([":
                depth += 1
            elif c in ")]":
                depth -= 1
            elif c == "," and depth == 1:
                args += 1
            i += 1
        out.append((src.count("\n", 0, m.start()) + 1, args, src[m.start() : start + 60].split("\n")[0]))
    return out


def test_no_send_path_encrypts_without_wrapping_for_us_too():
    src = MAIN.read_text(encoding="utf-8")
    single = [
        f"  MainActivity.java:{ln}: {txt.strip()[:80]}" for ln, args, txt in _encrypt_call_arities(src) if args < 3
    ]
    assert not single, (
        "these encrypt for ONE reader, so the Lion cannot re-read what they sent.\n"
        "Route them through encryptForBoth():\n" + "\n".join(single)
    )


def test_there_is_at_least_one_encrypt_call_to_police():
    """If the calls were renamed away, every check above would pass vacuously."""
    assert _encrypt_call_arities(MAIN.read_text(encoding="utf-8"))


def test_the_helper_still_passes_a_second_recipient():
    """Guard the guard: if encryptForBoth stopped passing our key, every call
    site would still look correct while producing single-reader messages."""
    src = MAIN.read_text(encoding="utf-8")
    m = re.search(r"encryptForBoth\([^)]*\)\s*\{(.*?)\n    \}", src, re.S)
    assert m, "encryptForBoth() not found — has the helper been renamed?"
    body = m.group(1)
    assert "ownPubB64()" in body, "encryptForBoth no longer wraps the key for us"


def test_the_wrapped_key_is_actually_put_on_the_wire():
    """A self-wrap that never leaves the device is not a self-wrap."""
    src = MAIN.read_text(encoding="utf-8")
    assert "encrypted_key_lion" in src, "the sender's wrapped key is never sent"
    assert "encryptedKeySelf" in src


def test_the_second_wrap_cannot_fail_the_send():
    """The bunny's copy is sealed before ours is attempted. A message the bunny
    never receives is a worse outcome than one the Lion cannot re-read, so the
    self-wrap is best-effort by construction."""
    helper = HELPER.read_text(encoding="utf-8")
    m = re.search(r"if \(canEncrypt\(alsoForPubKey\)\) \{(.*?)\n            \}", helper, re.S)
    assert m, "the self-wrap block is not where this test expects it"
    assert "try {" in m.group(1) and "catch" in m.group(1), "the self-wrap must not be able to throw the send away"


def test_a_successful_send_refreshes_the_thread():
    """Otherwise the input clears and the status says "Sent" while the thread
    above still shows the state before it — indistinguishable from a send that
    vanished."""
    src = MAIN.read_text(encoding="utf-8")
    m = re.search(r"private void doSendInboxMessage\(\).*?\n    \}\n", src, re.S)
    assert m, "doSendInboxMessage() not found"
    assert "refreshInbox();" in m.group(0), "the Inbox never refreshes after a send"

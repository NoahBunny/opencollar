"""The operator's own infrastructure must not appear in a public repo.

`opencollar` is public, and this repo is written by the person who also runs a
live relay for a real dynamic. Twice now their identifiers have drifted back in:
`670ebe9` scrubbed a relay hostname and a node id, and a 2026-08-23 privacy
sweep found the live relay host, three live mesh ids, a phone's adb serial and
four machine hostnames spread across the changelog, the handoff docs and — worse
— prefilled as the default relay URL in both desktop collars, so every fresh
install offered a stranger's host and one Enter accepted it.

The obvious guard would list the real values and assert they are absent. That
guard would publish them: a denylist of secrets is a secret. So this derives
what to look for instead.

The operator's domain is whatever this repo's own commits are authored from —
git knows it, the file does not. `.github/allowed_signers` is exempt because the
signing principal *must* equal the committer email or CI cannot verify a single
signature; that one line is the deliberate disclosure that makes the rest
enforceable.
"""

import os
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

SKIP_EXT = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".apk",
    ".jar",
    ".zip",
    ".ttf",
    ".otf",
    ".woff",
    ".woff2",
    ".p12",
    ".pdf",
    ".so",
    ".dex",
    ".bin",
}
# The signer principal has to be the committer email. See the docstring.
EXEMPT = {".github/allowed_signers"}

PLACEHOLDER_HOME = {"<user>", "test", "user", "youruser", "runner", "livvy"}


def _tracked_text_files():
    out = subprocess.run(["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True).stdout.split("\n")
    for rel in out:
        if not rel or rel in EXEMPT:
            continue
        if os.path.splitext(rel)[1].lower() in SKIP_EXT:
            continue
        p = REPO_ROOT / rel
        if not p.is_file():
            continue
        try:
            yield rel, p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue


def _committer_domains():
    """Domains this repo's commits are authored from — never written down here."""
    out = subprocess.run(
        ["git", "log", "--format=%ae%n%ce", "-n", "400"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    ).stdout
    domains = set()
    for addr in out.split("\n"):
        if "@" not in addr:
            continue
        d = addr.rsplit("@", 1)[1].strip().lower()
        # Forge-generated addresses are not the operator's infrastructure.
        if not d or d.endswith(("github.com", "users.noreply.github.com", "example.com")):
            continue
        domains.add(d)
    return domains


def test_the_operators_domain_does_not_appear_in_the_repo():
    domains = _committer_domains()
    if not domains:
        return  # shallow clone or no history — nothing to derive
    hits = []
    for rel, text in _tracked_text_files():
        low = text.lower()
        for d in domains:
            if d in low:
                for i, line in enumerate(text.split("\n"), 1):
                    if d in line.lower():
                        hits.append(f"  {rel}:{i}")
    assert not hits, (
        "the operator's own domain appears in a public repo:\n"
        + "\n".join(sorted(set(hits))[:20])
        + "\n\nUse collar.example.com / relay.example.com / focus.example.com instead.\n"
        "If it is genuinely load-bearing, add the path to EXEMPT with a reason."
    )


def test_no_real_home_directory_paths():
    """`/home/<someone>` names the machine's user. Docs and committed artifacts
    kept picking it up from pasted shell output."""
    pat = re.compile(r"/home/([A-Za-z][A-Za-z0-9._-]*)")
    hits = []
    for rel, text in _tracked_text_files():
        for i, line in enumerate(text.split("\n"), 1):
            for m in pat.finditer(line):
                who = m.group(1)
                if who in PLACEHOLDER_HOME:
                    continue
                hits.append(f"  {rel}:{i}: /home/{who}")
    assert not hits, (
        "real home-directory paths in a public repo:\n"
        + "\n".join(sorted(set(hits))[:20])
        + "\n\nWrite /home/<user> or $HOME."
    )

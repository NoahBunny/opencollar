#!/usr/bin/env python3
"""Regenerate veneration-tasks.md from veneration-tasks.json.

The JSON is the source of truth; the markdown is a reading view for a phone.
They were kept in step by hand, which works right up until it doesn't -- a task
added to one and not the other is a task the Lion cannot find, or worse, one
They read here and set from memory in a slightly different form. With
task_randcaps on, "slightly different" is a task bunny cannot satisfy.

Tasks are grouped by category and ordered by id within a group, so appending new
ids to the end of the JSON files them under the right heading without disturbing
any id already in circulation.

Usage:  python3 scripts/make-veneration-md.py [--check]
        --check exits non-zero if the file on disk is stale.
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "veneration-tasks.json"
DEST = ROOT / "veneration-tasks.md"

# Display names for the category keys, in the order they appear in the document.
SECTIONS = [
    ("ownership", "Ownership & belonging"),
    ("gratitude", "Gratitude"),
    ("obedience", "Obedience"),
    ("service", "Service & usefulness"),
    ("discipline", "Discipline & correction"),
    ("attention", "Attention & frame"),
    ("patience", "Patience & waiting"),
    ("devotion", "Devotion"),
    ("long", "Long-form (single rep)"),
]

PREAMBLE = """# Veneration tasks

{count} preloaded tasks for the Lion to pick from when They would rather not write one.

Each entry drops straight into `task_text`. `task_randcaps: 1` makes the lockscreen
enforce capitalisation exactly — including Their pronouns — so treat these strings as
the enforced form. Pair a gated lock with an `unlock_at` so there is a time backstop.
"""


def render(doc):
    tasks = doc["tasks"]
    known = {k for k, _ in SECTIONS}
    stray = sorted({t["category"] for t in tasks} - known)
    if stray:
        raise SystemExit(f"unknown category in {SRC.name}: {stray} — add it to SECTIONS")

    out = [PREAMBLE.format(count=len(tasks))]
    for key, title in SECTIONS:
        group = sorted((t for t in tasks if t["category"] == key), key=lambda t: t["id"])
        if not group:
            continue
        out.append(f"\n## {title}  ({len(group)})\n\n")
        for t in group:
            # Two trailing spaces are a markdown hard break: the task text has to
            # sit on its own line under the metadata, not run on after it.
            out.append(
                f"- **`{t['id']}`** · {t['words']}w · suggested reps {t['suggested_task_reps']}  \n  {t['task_text']}\n"
            )
    return "".join(out)


def main():
    doc = json.loads(SRC.read_text(encoding="utf-8"))
    rendered = render(doc)
    if "--check" in sys.argv:
        current = DEST.read_text(encoding="utf-8") if DEST.exists() else ""
        if current != rendered:
            print(f"{DEST.name} is stale — run: python3 scripts/make-veneration-md.py")
            return 1
        print(f"{DEST.name} is up to date ({len(doc['tasks'])} tasks)")
        return 0
    DEST.write_text(rendered, encoding="utf-8")
    print(f"wrote {DEST.name} ({len(doc['tasks'])} tasks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

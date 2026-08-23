#!/usr/bin/env python3
"""Regenerate the derived views of veneration-tasks.json.

Two outputs, one source:
  * `veneration-tasks.md`                              — a reading view for a phone
  * `android/controller/res/raw/veneration_tasks.json` — what Lion's Share ships

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
# Lion's Share reads this at runtime to offer a random task by category. It is
# DERIVED, never hand-edited: a second copy of 144 strings that drifts is a
# task the Lion sets from one and the lockscreen enforces from the other, and
# with randcaps on "slightly different" is a task the bunny cannot satisfy.
APP_DEST = ROOT / "android" / "controller" / "res" / "raw" / "veneration_tasks.json"

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


def render_app(doc):
    """The app-facing view: minified, prose stripped, display titles baked in.

    Carrying the titles here means the app does not keep its own copy of
    SECTIONS — the order and wording of the categories are decided in exactly
    one place, and the picker cannot disagree with the document the Lion reads.
    """
    tasks = doc["tasks"]
    cats = []
    out_tasks = []
    for key, title in SECTIONS:
        group = sorted((t for t in tasks if t["category"] == key), key=lambda t: t["id"])
        if not group:
            continue
        cats.append({"key": key, "title": title, "count": len(group)})
        for t in group:
            out_tasks.append(
                {
                    "id": t["id"],
                    "category": key,
                    "reps": int(t["suggested_task_reps"]),
                    "text": t["task_text"],
                }
            )
    return json.dumps({"categories": cats, "tasks": out_tasks}, ensure_ascii=False, separators=(",", ":")) + "\n"


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
    outputs = [(DEST, render(doc)), (APP_DEST, render_app(doc))]

    if "--check" in sys.argv:
        stale = [d.name for d, want in outputs if (d.read_text(encoding="utf-8") if d.exists() else "") != want]
        if stale:
            print(f"stale: {', '.join(stale)} — run: python3 scripts/make-veneration-md.py")
            return 1
        print(f"up to date ({len(doc['tasks'])} tasks): {', '.join(d.name for d, _ in outputs)}")
        return 0

    for dest, want in outputs:
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(want, encoding="utf-8")
        print(f"wrote {dest.name} ({len(doc['tasks'])} tasks)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""The veneration catalogue and its two derived views must agree.

`veneration-tasks.json` is the source. Two things are generated from it:

  * `veneration-tasks.md`                              — the Lion's reading view
  * `android/controller/res/raw/veneration_tasks.json` — what Lion's Share ships
  * `android/companion/res/raw/veneration_tasks.json`  — what Bunny Tasker ships

`scripts/make-veneration-md.py --check` has existed since the markdown view was
added, and nothing ran it. That is a drift guard that does not guard: the whole
reason it exists is that a task present in one view and not the other is one the
Lion cannot find, or worse, one They read in one place and set from memory in a
slightly different form.

That last case is why this matters more now than it did. Lion's Share turns
`task_randcaps` on when it loads one of these, so the string becomes the
enforced form — the bunny must reproduce it exactly, capitalised pronouns and
all. A single character of drift between what the Lion picked and what the
lockscreen checks is a task that cannot be completed and cannot be argued with.
"""

import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC = REPO_ROOT / "veneration-tasks.json"
MD = REPO_ROOT / "veneration-tasks.md"
APP = REPO_ROOT / "android" / "controller" / "res" / "raw" / "veneration_tasks.json"
COMPANION_APP = REPO_ROOT / "android" / "companion" / "res" / "raw" / "veneration_tasks.json"
GENERATOR = REPO_ROOT / "scripts" / "make-veneration-md.py"


def test_the_generated_views_are_not_stale():
    """Runs the generator's own --check, which nothing else did."""
    r = subprocess.run([sys.executable, str(GENERATOR), "--check"], capture_output=True, text=True, cwd=str(REPO_ROOT))
    assert r.returncode == 0, r.stdout + r.stderr


def test_the_app_resource_carries_every_task_verbatim():
    """Belt to the generator's braces: compare the shipped strings directly, so
    a hand-edit of res/raw fails even if someone also re-ran the generator."""
    source = {t["id"]: t["task_text"] for t in json.loads(SRC.read_text(encoding="utf-8"))["tasks"]}
    shipped = {t["id"]: t["text"] for t in json.loads(APP.read_text(encoding="utf-8"))["tasks"]}
    assert shipped == source


def test_every_task_belongs_to_a_declared_category():
    app = json.loads(APP.read_text(encoding="utf-8"))
    declared = {c["key"]: c["count"] for c in app["categories"]}
    actual: dict[str, int] = {}
    for t in app["tasks"]:
        actual[t["category"]] = actual.get(t["category"], 0) + 1
    assert actual == declared


def test_the_reading_view_lists_every_task():
    """A task the Lion cannot find in the document may as well not exist."""
    md = MD.read_text(encoding="utf-8")
    missing = [t["id"] for t in json.loads(SRC.read_text(encoding="utf-8"))["tasks"] if t["id"] not in md]
    assert not missing, f"absent from veneration-tasks.md: {missing[:10]}"


def test_the_suggested_reps_are_sane():
    """`reps` drives how many times the bunny writes it out. A long-form task at
    five reps is a different punishment from the one the Lion thought they were
    setting."""
    for t in json.loads(APP.read_text(encoding="utf-8"))["tasks"]:
        assert t["reps"] >= 1, f"{t['id']} suggests {t['reps']} reps"
        words = len(t["text"].split())
        assert t["reps"] * words <= 400, (
            f"{t['id']} suggests {t['reps']} reps of {words} words — {t['reps'] * words} words to type"
        )


def test_both_apps_ship_byte_identical_catalogues():
    """Voluntary tasks let the bunny draw from the same 144 the Lion draws
    from. If the two copies drift by a single capital, a task offered from one
    picker and enforced from the other is unsatisfiable — the exact failure the
    derived-file rule exists to prevent, now with two consumers instead of one."""
    assert COMPANION_APP.read_bytes() == APP.read_bytes()

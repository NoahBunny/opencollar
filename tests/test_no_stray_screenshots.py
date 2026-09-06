"""No camera-roll screenshot reaches the public repo by accident.

`opencollar` is public. On 2026-08-23, four screenshots of an unrelated
business app — client message previews, Odoo activity, internal calendar
names — were swept into a commit by a `git add -A` and came within one push of
being published. They had sat in the working tree since 2026-08-17, untracked
and unnoticed.

The tell was the filename. Everything deliberately added to `docs/Screenshots/`
is named for what it shows (`onboarding-admin-step.png`,
`money-balance-history.png`); the strays carried the phone's own export name,
`Screenshot_<date>-<time>.png`, because nobody chose a name for them — they were
dropped in the folder and forgotten.

So the rule is the filename: a screenshot that belongs in the repo has been
named on purpose. Anything still carrying a camera-roll name has not been
looked at, and this fails until someone does.
"""

import re
from pathlib import Path

SHOTS = Path(__file__).resolve().parent.parent / "docs" / "Screenshots"

# The phone's export pattern, both separators it has used.
CAMERA_ROLL = re.compile(r"^Screenshot_\d{8}[-_]\d{6}")

# Grandfathered: five Bunny Tasker onboarding captures, already public on
# origin since b67a81c and verified by eye to show the app and nothing else.
# The list is deliberately exact rather than a prefix — a sixth file joining
# them silently is the thing this test exists to stop.
ALLOWED = {
    "Screenshot_20260625_161826.png",
    "Screenshot_20260625_161835.png",
    "Screenshot_20260625_161845.png",
    "Screenshot_20260625_161901.png",
    "Screenshot_20260625_162034.png",
}


def test_no_unreviewed_camera_roll_screenshots():
    if not SHOTS.is_dir():
        return
    strays = sorted(p.name for p in SHOTS.iterdir() if CAMERA_ROLL.match(p.name) and p.name not in ALLOWED)
    assert not strays, (
        "these carry the phone's own export name, so nobody chose to publish them:\n"
        + "\n".join(f"  docs/Screenshots/{s}" for s in strays)
        + "\n\nLook at each one. If it belongs in a public repo, rename it for what it\n"
        "shows. If it does not, delete it — and if it has already been committed,\n"
        "strip it from history before pushing."
    )


def test_the_grandfathered_five_are_still_there():
    """If they are renamed or removed, shrink ALLOWED rather than leaving a
    stale exemption that would silently cover a future file."""
    if not SHOTS.is_dir():
        return
    present = {p.name for p in SHOTS.iterdir()}
    assert ALLOWED <= present, f"stale exemption in ALLOWED: {sorted(ALLOWED - present)}"

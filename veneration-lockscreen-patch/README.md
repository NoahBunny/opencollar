# Veneration-on-lockscreen — staged patch

Wires `task_text` / `task_reps` / `task_randcaps` / `word_min` into the desktop
lock overlay, and lets completion release the lock.

## Files
- `veneration-lockscreen.patch` — reviewable unified diff (+199 / -1)
- `apply.py` — anchored patch script (idempotent-checked: fails loudly on anchor miss)
- `focuslock-desktop.py` — patched result, `py_compile` clean
- `focuslock-desktop.py.orig` — pristine copy for rollback

## Apply (needs the Lion's go-ahead — restarting the unit is CONFIRMED-tier)
    sudo cp /opt/focuslock/focuslock-desktop.py /opt/focuslock/focuslock-desktop.py.bak
    sudo patch /opt/focuslock/focuslock-desktop.py < veneration-lockscreen.patch
    systemctl --user restart focuslock-desktop     # <-- the tamper-flagged step

## Rollback
    sudo cp /opt/focuslock/focuslock-desktop.py.bak /opt/focuslock/focuslock-desktop.py

## How the Lion drives it
Set relay-side, as normal orders:
- `task_text`   — the veneration to be typed. Non-empty = exact-match mode.
- `task_reps`   — times through. 0 and 1 both mean once.
- `task_randcaps` — 1 = capitalisation must match exactly. 0 = case-insensitive.
- `word_min`    — freeform floor. **Only consulted when `task_text` is empty**
                  ("write at least N words"). Documented interpretation, easy to
                  change if They want it as an additional gate instead.

## Safety property
`unlock_at` handling is untouched. Completing the task can end a lock EARLY; it
can never be the only way out. If a task gate is set with `unlock_at == 0`, the
desktop logs a warning that there is no time-based backstop on that lock.

## Notes
- Panel renders on the primary monitor only — type it once, not once per screen.
- Task label is `set_selectable(False)`: no select-and-copy out of the prompt.
- Whitespace is collapsed before comparison, so line wrapping never fails a
  correct answer. Content must match exactly.
- Progress is LOCAL (`state.task_reps_done`) and resets whenever `task_text`
  changes, so a new order never inherits a stale rep count.
- `task_done` is written on completion but is NOT used as an unlock trigger by
  the poller — otherwise a stale `task_done=1` from a previous task would pop
  the next lock open instantly.
- If the broadcast fails on release, the local lock still drops; next sync
  reconciles. Bunny does not get stranded behind a network error.

## Repo note (added on move)
The repo source `focuslock-desktop.py` and the deployed `/opt/focuslock/focuslock-desktop.py`
were **byte-identical** when this was staged, so the patch applies to either.
`patch --dry-run -p0` against the repo copy: clean, no fuzz, no rejects.

Preferred route is patch the repo, then deploy — that way the change is tracked
rather than living only in `/opt`. Current branch: `feat/real-mesh-bunnies`
(which already has unrelated staged changes — this folder is untracked and
nothing here has been committed).

Rollback via git if applied to the repo:
    git checkout -- focuslock-desktop.py

---

# v2 — overlay revived (supersedes v1)

v1 patched `create_lock_window`, which turned out to be **dead code**: defined at
:2559, called from nowhere. `show_lock()` generates a PNG, points
`kscreenlockerrc` at it and calls `loginctl lock-session` — the live lock surface
is the KDE greeter showing a static image. v1 rendered nothing.

## What v2 changes
- `create_lock_window` is actually invoked, via `_present_veneration_overlay()`
- window caption set to `FOCUSLOCK-COLLAR-ACTIVE` — `force_lock_fullscreen()`
  matches that exact string via KWin scripting. It was written for the old
  browser lock; without this the overlay renders unenforced behind everything.
- Ctrl+Enter works: CAPTURE-phase controller on the TextView (a focused TextView
  consumes Return before a BUBBLE-phase window controller sees it)
- paste is dead: `insert-text` veto rejects any lump insertion, catching Ctrl+V,
  middle-click primary, drag-and-drop and the context menu in one hook
- `enforce_session_lock` stands down ONLY while the overlay is up and a task is
  pending; kill the overlay and re-locking resumes next tick

## What v2 does NOT change
`loginctl lock-session`, the KDE greeter, `kscreenlockerrc`, and the `unlock_at`
timer are untouched. **The overlay is layered on top of the real session lock,
never a replacement for it.** A lock with no task pending behaves exactly as
before: no overlay, no change.

## ⚠ word_min is already 50 in live orders
The overlay presents when `task_text` OR `word_min` is set. `word_min` has been
50 all along as an orphan field. It is read now, so THE NEXT LOCK WILL PRESENT
THE OVERLAY with a freeform 50-word gate even with `task_text` empty. Set
`word_min: 0` relay-side if that is not wanted.

## Redeploy
    sudo cp "/home/livv/Desktop/Scripts/Lion's Share + Bunny Tasker/focuslock-desktop.py" /opt/focuslock/focuslock-desktop.py
    systemctl --user restart focuslock-desktop.service
Rollback stays at /opt/focuslock/focuslock-desktop.py.bak (pre-v1, pristine).

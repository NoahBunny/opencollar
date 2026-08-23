#!/usr/bin/env python3
"""v2 — veneration task on a REVIVED GTK overlay, layered over the session lock.

Changes vs v1:
  * create_lock_window is actually called (it was dead code — defined, never invoked)
  * window caption set to FOCUSLOCK-COLLAR-ACTIVE so force_lock_fullscreen's
    KWin script matches it (it was written for the old browser lock)
  * Ctrl+Enter works: CAPTURE-phase controller on the TextView, because a
    focused TextView eats Return before a BUBBLE-phase window controller sees it
  * paste is dead: insert-text veto catches Ctrl+V, middle-click primary,
    drag-and-drop and the context menu in one hook
  * enforce_session_lock stands down ONLY while the overlay is up and a task is
    pending; if the overlay dies, re-locking resumes on the next tick

NOT changed: loginctl lock-session, the KDE greeter, kscreenlockerrc handling,
and the unlock_at timer. The overlay is layered on top of the real session lock,
never a replacement for it. A lock with no task pending behaves exactly as before.
"""

import re as _re
import sys

PATH = sys.argv[1]
src = open(PATH, encoding="utf-8").read()
n = 0


def sub(anchor, replacement, count=1):
    global src, n
    if src.count(anchor) != count:
        raise SystemExit(f"ANCHOR MISS ({src.count(anchor)}x, want {count}):\n{anchor[:180]}")
    src = src.replace(anchor, replacement, count)
    n += 1


# ── 1. state ───────────────────────────────────────────────────────────────
sub(
    "    countdown_last_warn = 0  # epoch ms of last warning beep\n",
    "    countdown_last_warn = 0  # epoch ms of last warning beep\n"
    '    task_text = ""  # veneration text bunny must type to clear the lock\n'
    "    task_reps = 0  # times through; 0 and 1 both mean once\n"
    "    task_randcaps = 0  # 1 = capitalisation must match exactly\n"
    "    word_min = 0  # freeform floor, only used when task_text is empty\n"
    "    task_reps_done = 0  # LOCAL progress; reset when task_text changes\n"
    '    task_gated_on = ""  # the task_text task_reps_done is counting against\n'
    '    task_status = ""  # feedback line under the entry\n',
)

# ── 2. snapshot the task fields ────────────────────────────────────────────
sub(
    '            "bedtime_unlock_hour",\n        ]\n',
    '            "bedtime_unlock_hour",\n'
    '            "task_text",\n'
    '            "task_reps",\n'
    '            "task_randcaps",\n'
    '            "word_min",\n'
    "        ]\n",
)

# ── 3. mirror into state; reset progress when the task changes ─────────────
sub(
    '        state.sub_tier = str(snap.get("sub_tier") or "")\n',
    '        state.sub_tier = str(snap.get("sub_tier") or "")\n'
    "\n"
    "        # Veneration task. A change to task_text is a NEW task: drop local rep\n"
    "        # progress so a fresh order never inherits a stale count.\n"
    '        task_text = str(snap.get("task_text") or "")\n'
    '        if task_text == "null":\n'
    '            task_text = ""\n'
    "        if task_text != state.task_gated_on:\n"
    "            state.task_gated_on = task_text\n"
    "            state.task_reps_done = 0\n"
    '            state.task_status = ""\n'
    "        state.task_text = task_text\n"
    '        for _f, _d in (("task_reps", 0), ("task_randcaps", 0), ("word_min", 0)):\n'
    "            try:\n"
    "                setattr(state, _f, int(snap.get(_f) or _d))\n"
    "            except (TypeError, ValueError):\n"
    "                setattr(state, _f, _d)\n"
    '        if state.locked and (state.task_text or state.word_min) and not int(snap.get("unlock_at") or 0):\n'
    '            logger.warning("Task gate active with no unlock_at — no time backstop on this lock")\n',
)

# ── 4. present the overlay when a task is pending (session lock UNCHANGED) ─
sub(
    '            subprocess.run(["loginctl", "lock-session"], capture_output=True, timeout=5)\n'
    '            logger.info("Session locked via loginctl")\n',
    '            subprocess.run(["loginctl", "lock-session"], capture_output=True, timeout=5)\n'
    '            logger.info("Session locked via loginctl")\n'
    "            # Veneration overlay rides ON TOP of the session lock — the greeter\n"
    "            # still guards the session, this is what bunny faces after auth.\n"
    "            # Locks with no task pending are untouched: no overlay, no change.\n"
    "            if state.task_text or state.word_min:\n"
    "                self._present_veneration_overlay()\n",
)

# ── 5. enforce_session_lock stands down while the overlay is live ──────────
sub(
    "    def enforce_session_lock(self):\n"
    '        """If still locked, re-lock the session — password won\'t save you."""\n'
    "        if not self.lock_active:\n"
    "            return False\n",
    "    def enforce_session_lock(self):\n"
    '        """If still locked, re-lock the session — password won\'t save you."""\n'
    "        if not self.lock_active:\n"
    "            return False\n"
    "        # Stand down ONLY while the veneration overlay is actually up and a task\n"
    "        # is pending: otherwise the greeter re-asserts every second and bunny\n"
    "        # never reaches a keyboard. Kill the overlay and re-locking resumes on\n"
    "        # the next tick, so this is a pause, not a hole.\n"
    "        if self.windows and (state.task_text or state.word_min):\n"
    "            return True\n",
)

# ── 6. caption must match the KWin enforce script ──────────────────────────
sub(
    '        win = Gtk.ApplicationWindow(application=self)\n        win.set_title("FocusLock")\n',
    "        win = Gtk.ApplicationWindow(application=self)\n"
    "        # force_lock_fullscreen() matches this exact caption via KWin scripting.\n"
    "        # It was written for the old browser lock; the GTK window must claim it.\n"
    '        win.set_title("FOCUSLOCK-COLLAR-ACTIVE")\n',
)

# ── 7. CSS ─────────────────────────────────────────────────────────────────
sub(
    "            .collar-divider { background-color: rgba(200, 168, 78, 0.1); min-height: 1px; }\n",
    "            .collar-divider { background-color: rgba(200, 168, 78, 0.1); min-height: 1px; }\n"
    "            .collar-task { color: #c8a84e; font-size: 15px; font-weight: 300; }\n"
    "            .collar-task-reps { color: #c8a84e; font-size: 12px; letter-spacing: 3px; }\n"
    "            .collar-task-entry textview, .collar-task-entry text {\n"
    "                background-color: rgba(0, 0, 0, 0.45); color: #ddccaa; font-size: 14px; font-weight: 300;\n"
    "            }\n"
    "            .collar-task-entry { border: 1px solid rgba(200, 168, 78, 0.25); border-radius: 8px; padding: 8px; }\n"
    "            .collar-task-status { color: #aa8866; font-size: 13px; font-weight: 300; }\n"
    "            .collar-task-status-bad { color: #cc4422; font-size: 13px; font-weight: 300; }\n"
    "            .collar-task-status-good { color: #66aa44; font-size: 13px; font-weight: 300; }\n",
)

# ── 8. build the panel (primary window only) ───────────────────────────────
sub(
    '        self.taunt_label = Gtk.Label(label=state.current_taunt or (random.choice(TAUNTS) if TAUNTS else ""))\n'
    '        self.taunt_label.add_css_class("collar-taunt")\n'
    "        card.append(self.taunt_label)\n",
    '        self.taunt_label = Gtk.Label(label=state.current_taunt or (random.choice(TAUNTS) if TAUNTS else ""))\n'
    '        self.taunt_label.add_css_class("collar-taunt")\n'
    "        card.append(self.taunt_label)\n"
    "\n"
    "        # Veneration panel. Primary window only — type it once, not once per\n"
    "        # monitor. Guarded because create_lock_window runs per display and an\n"
    "        # unconditional reset would null the primary window's entry reference.\n"
    "        if primary:\n"
    "            self.task_view = None\n"
    "            self.task_label = None\n"
    "            self.task_reps_label = None\n"
    "            self.task_status_label = None\n"
    "            if state.task_text or state.word_min:\n"
    "                self._build_veneration_panel(card)\n",
)

open(PATH, "w", encoding="utf-8").write(src)
print(f"part 1: {n} hunks")

# ── 9. the panel, paste-blocking, verification, overlay lifecycle ──────────
sub(
    "    def enforce_fullscreen_loop(self):\n",
    r'''    def _build_veneration_panel(self, card):
        """Task text + entry + submit, appended to the lock card."""
        div = Gtk.Box()
        div.add_css_class("collar-divider")
        div.set_margin_top(8)
        div.set_margin_bottom(8)
        card.append(div)

        self.task_label = Gtk.Label(label=state.task_text or f"Write at least {state.word_min} words for Them.")
        self.task_label.add_css_class("collar-task")
        self.task_label.set_wrap(True)
        self.task_label.set_max_width_chars(64)
        self.task_label.set_justify(Gtk.Justification.CENTER)
        self.task_label.set_selectable(False)  # nothing to select and copy out of
        card.append(self.task_label)

        self.task_reps_label = Gtk.Label(label=self._veneration_reps_text())
        self.task_reps_label.add_css_class("collar-task-reps")
        card.append(self.task_reps_label)

        self.task_view = Gtk.TextView()
        self.task_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.task_view.set_size_request(560, 180)
        self.task_view.set_accepts_tab(False)  # Tab stays a blocked nav key
        self.task_view.add_css_class("collar-task-entry")

        # PASTE IS NOT TYPING. One veto on insert-text catches every vector:
        # Ctrl+V, middle-click primary selection, drag-and-drop, and the
        # context menu all arrive as a single lump insertion. Real typing
        # arrives one character at a time (a couple, with compose/IME).
        self.task_view.get_buffer().connect("insert-text", self._on_task_insert)

        # CAPTURE phase: a focused TextView consumes Return before a BUBBLE-phase
        # window controller ever sees it, which is why Ctrl+Enter did nothing.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_task_key)
        self.task_view.add_controller(keys)

        # Middle-click pastes the primary selection; right-click offers Paste.
        clicks = Gtk.GestureClick()
        clicks.set_button(0)  # listen to every button
        clicks.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clicks.connect("pressed", self._on_task_click)
        self.task_view.add_controller(clicks)

        self.task_scroller = Gtk.ScrolledWindow()
        self.task_scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.task_scroller.set_size_request(560, 180)
        self.task_scroller.set_child(self.task_view)
        card.append(self.task_scroller)

        self.task_status_label = Gtk.Label(label=state.task_status or "")
        self.task_status_label.add_css_class("collar-task-status")
        self.task_status_label.set_wrap(True)
        self.task_status_label.set_max_width_chars(64)
        card.append(self.task_status_label)

        self.task_submit = Gtk.Button(label="Submit to Them")
        self.task_submit.set_margin_top(8)
        self.task_submit.connect("clicked", self._on_veneration_submit)
        card.append(self.task_submit)
        self.task_completed = False

        def _focus_task_view():
            if self.task_view:
                self.task_view.grab_focus()
            return False  # once only: grab_focus() returns True and would repeat

        GLib.idle_add(_focus_task_view)

    def _on_task_insert(self, buf, location, text, length):
        """Veto lump insertions — that is what a paste looks like."""
        if len(text) > 2:
            buf.stop_emission_by_name("insert-text")
            self._set_veneration_status("Type it. Pasting is not typing.", "bad")

    def _on_task_key(self, controller, keyval, keycode, mod):
        ctrl = bool(mod & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mod & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._on_veneration_submit()
            return True
        # Belt to the insert-text braces: refuse the paste bindings outright so
        # the message is honest rather than a silently swallowed keystroke.
        if (ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V, Gdk.KEY_Insert)) or (shift and keyval == Gdk.KEY_Insert):
            self._set_veneration_status("Type it. Pasting is not typing.", "bad")
            return True
        return False

    def _on_task_click(self, gesture, n_press, x, y):
        """Middle-click pastes primary; right-click offers a Paste item."""
        if gesture.get_current_button() in (2, 3):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._set_veneration_status("Type it. Pasting is not typing.", "bad")

    def _veneration_reps_text(self):
        total = max(1, int(state.task_reps or 0))
        return f"REP {min(state.task_reps_done + 1, total)} OF {total}"

    @staticmethod
    def _normalise_veneration(text, randcaps):
        """Collapse whitespace so line wrapping never fails a correct answer."""
        out = re.sub(r"\s+", " ", (text or "")).strip()
        return out if randcaps else out.casefold()

    def _set_veneration_status(self, text, kind=""):
        state.task_status = text
        if not getattr(self, "task_status_label", None):
            return
        for cls in ("collar-task-status", "collar-task-status-bad", "collar-task-status-good"):
            self.task_status_label.remove_css_class(cls)
        self.task_status_label.add_css_class(
            "collar-task-status-bad" if kind == "bad"
            else "collar-task-status-good" if kind == "good"
            else "collar-task-status"
        )
        self.task_status_label.set_label(text)

    def _on_veneration_submit(self, _button=None):
        if getattr(self, "task_completed", False):
            return  # accepted already; the release is on its way
        if not getattr(self, "task_view", None):
            return
        buf = self.task_view.get_buffer()
        typed = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

        if state.task_text:
            want = self._normalise_veneration(state.task_text, state.task_randcaps)
            got = self._normalise_veneration(typed, state.task_randcaps)
            if got != want:
                self._set_veneration_status(
                    "Nothing typed. They are waiting." if not got
                    else "That is not what They asked for. Read it again and type it in full.",
                    "bad",
                )
                return
        else:
            words = len((typed or "").split())
            if words < state.word_min:
                self._set_veneration_status(f"{words} of {state.word_min} words. Keep going.", "bad")
                return

        state.task_reps_done += 1
        total = max(1, int(state.task_reps or 0))
        buf.set_text("", 0)

        if state.task_reps_done < total:
            if self.task_reps_label:
                self.task_reps_label.set_label(self._veneration_reps_text())
            self._set_veneration_status(f"Accepted. {total - state.task_reps_done} to go.", "good")
            return

        # Accepted. Take the input away so there is nothing left to type into,
        # and count down out loud rather than sitting silent — a dead field and
        # an unexplained pause both read as "it broke".
        self.task_completed = True
        for _w in (getattr(self, "task_scroller", None), getattr(self, "task_submit", None),
                   getattr(self, "task_reps_label", None)):
            if _w is not None:
                _w.set_visible(False)
        self._release_countdown = 3
        self._set_veneration_status("Accepted. Thank Them. Releasing in 3\u2026", "good")
        GLib.timeout_add(1000, self._veneration_release_tick)

    def _veneration_release_tick(self):
        self._release_countdown -= 1
        if self._release_countdown > 0:
            self._set_veneration_status(
                f"Accepted. Thank Them. Releasing in {self._release_countdown}\u2026", "good"
            )
            return True
        self._release_after_veneration()
        return False

    def _present_veneration_overlay(self):
        """Bring the GTK overlay up over the session lock. Additive, never a
        replacement: loginctl/the KDE greeter are untouched."""
        if self.windows:
            return
        try:
            win = self.create_lock_window(primary=True)
            self.windows.append(win)
            win.present()
            win.fullscreen()
            self.force_lock_fullscreen()
            GLib.timeout_add(3000, self.enforce_fullscreen_loop)
            logger.info("Veneration overlay presented over session lock")
        except Exception as e:
            # If the overlay cannot come up, do NOT stand the greeter down.
            # A task bunny cannot reach is a lock with no exit but the timer.
            self.windows = []
            logger.warning("Veneration overlay failed to present: %s", e)

    def _teardown_veneration_overlay(self):
        for w in list(self.windows):
            try:
                self.allow_close = True
                w.destroy()
            except Exception:
                pass
        self.windows = []
        self.task_view = None
        self.task_label = None
        self.task_reps_label = None
        self.task_status_label = None
        self.task_scroller = None
        self.task_submit = None
        self.task_completed = False
        self.allow_close = False

    def _release_after_veneration(self):
        """Task complete — end the lock. Mirrors the unlock_at timer path."""
        logger.info("Veneration completed (%s reps) — releasing lock", state.task_reps_done)
        try:
            mesh_orders.set("task_done", 1)
            mesh_orders.set("task_text", "")
            mesh_orders.set("lock_active", 0)
            mesh_orders.set("desktop_active", 0)
            mesh_orders.set("desktop_locked_devices", "")
            mesh_orders.set("unlock_at", 0)
            mesh_orders.set("message", "")
            mesh.bump_and_broadcast(mesh_orders, MESH_NODE_ID, mesh_peers, ntfy_fn=_ntfy_fn)
        except Exception as e:
            # Never strand bunny behind a broadcast failure — drop the local lock
            # and let the next sync reconcile.
            logger.warning("Veneration release broadcast failed: %s", e)
        state.task_gated_on = ""
        state.task_reps_done = 0
        state.task_text = ""
        state.word_min = 0
        state.locked = False
        self.hide_lock()

    def enforce_fullscreen_loop(self):
''',
)

# ── 10. tear the overlay down on unlock ────────────────────────────────────
sub(
    '    def hide_lock(self):\n        logger.info("HIDE LOCK — unlocking session")\n',
    "    def hide_lock(self):\n"
    '        logger.info("HIDE LOCK — unlocking session")\n'
    "        self._teardown_veneration_overlay()\n",
)

# ── 11. mid-lock: a task set while already locked still gets a surface ─────
sub(
    '                if hasattr(self, "taunt_label") and self.taunt_label:\n'
    "                    self.taunt_label.set_label(state.current_taunt)\n",
    '                if hasattr(self, "taunt_label") and self.taunt_label:\n'
    "                    self.taunt_label.set_label(state.current_taunt)\n"
    '                if getattr(self, "task_label", None) and state.task_text:\n'
    "                    self.task_label.set_label(state.task_text)\n"
    '                if getattr(self, "task_reps_label", None):\n'
    "                    self.task_reps_label.set_label(self._veneration_reps_text())\n",
)
sub(
    '    def update_lock(self):\n        """Update existing lock windows with new state."""\n',
    "    def update_lock(self):\n"
    '        """Update existing lock windows with new state."""\n'
    "        # A task assigned mid-lock still needs a surface to be typed on.\n"
    "        if self.lock_active and (state.task_text or state.word_min) and not self.windows:\n"
    "            self._present_veneration_overlay()\n",
)

if not _re.search(r"^import re$", src, _re.M):
    # alphabetical: random, re, signal — ruff's isort rule is enforced on this branch
    sub("import signal\n", "import re\nimport signal\n")

open(PATH, "w", encoding="utf-8").write(src)
print(f"total: {n} hunks")

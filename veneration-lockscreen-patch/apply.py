#!/usr/bin/env python3
"""Wire task_text/task_reps/task_randcaps/word_min into the desktop lock overlay.

Four legs:
  1. display      — carry the task fields into the poll snapshot + CollarState
  2. input        — a TextView on the lock card, with the key-intercept opened for it
  3. verification — normalise + compare typed text against task_text, count reps
  4. gate         — completing the reps releases the lock, mirroring the timer path

The unlock_at timer path is deliberately NOT modified. Task completion can end a
lock EARLY; it can never become the only way out.
"""
import io, sys, re

PATH = sys.argv[1]
src = io.open(PATH, encoding="utf-8").read()
n = 0

def sub(anchor, replacement, count=1):
    global src, n
    if src.count(anchor) != count:
        raise SystemExit(f"ANCHOR MISS ({src.count(anchor)}x, expected {count}):\n{anchor[:200]}")
    src = src.replace(anchor, replacement, count)
    n += 1

# ── 1. CollarState: hold the task fields + local rep progress ──────────────
sub(
    '    countdown_last_warn = 0  # epoch ms of last warning beep\n',
    '    countdown_last_warn = 0  # epoch ms of last warning beep\n'
    '    task_text = ""  # veneration text bunny must type to clear the lock\n'
    '    task_reps = 0  # how many times through; 0/1 both mean once\n'
    '    task_randcaps = 0  # 1 = match capitalisation exactly, 0 = case-insensitive\n'
    '    word_min = 0  # freeform floor, only used when task_text is empty\n'
    '    task_reps_done = 0  # LOCAL progress; reset when task_text changes\n'
    '    task_gated_on = ""  # the task_text task_reps_done is counting against\n'
    '    task_status = ""  # feedback line under the entry\n',
)

# ── 1b. poll_status snapshot: pull the fields off the orders doc ───────────
sub(
    '            "bedtime_enabled",\n'
    '            "bedtime_lock_hour",\n'
    '            "bedtime_unlock_hour",\n'
    '        ]\n',
    '            "bedtime_enabled",\n'
    '            "bedtime_lock_hour",\n'
    '            "bedtime_unlock_hour",\n'
    '            "task_text",\n'
    '            "task_reps",\n'
    '            "task_randcaps",\n'
    '            "word_min",\n'
    '        ]\n',
)

# ── 1c. poll_status: mirror them into state, resetting progress on new task ─
sub(
    '        pinned = str(snap.get("pinned_message") or "")\n'
    '        state.pinned = pinned if pinned != "null" else ""\n'
    '        state.sub_tier = str(snap.get("sub_tier") or "")\n',
    '        pinned = str(snap.get("pinned_message") or "")\n'
    '        state.pinned = pinned if pinned != "null" else ""\n'
    '        state.sub_tier = str(snap.get("sub_tier") or "")\n'
    '\n'
    '        # Veneration task. A change to task_text is a NEW task: drop any\n'
    '        # local rep progress so a fresh order never inherits a stale count.\n'
    '        task_text = str(snap.get("task_text") or "")\n'
    '        if task_text == "null":\n'
    '            task_text = ""\n'
    '        if task_text != state.task_gated_on:\n'
    '            state.task_gated_on = task_text\n'
    '            state.task_reps_done = 0\n'
    '            state.task_status = ""\n'
    '        state.task_text = task_text\n'
    '        try:\n'
    '            state.task_reps = int(snap.get("task_reps") or 0)\n'
    '        except (TypeError, ValueError):\n'
    '            state.task_reps = 0\n'
    '        try:\n'
    '            state.task_randcaps = int(snap.get("task_randcaps") or 0)\n'
    '        except (TypeError, ValueError):\n'
    '            state.task_randcaps = 0\n'
    '        try:\n'
    '            state.word_min = int(snap.get("word_min") or 0)\n'
    '        except (TypeError, ValueError):\n'
    '            state.word_min = 0\n'
    '        if state.locked and (state.task_text or state.word_min) and not int(snap.get("unlock_at") or 0):\n'
    '            logger.warning(\n'
    '                "Task gate active with no unlock_at timer — no time-based backstop on this lock"\n'
    '            )\n',
)

# ── 2. CSS for the veneration panel ────────────────────────────────────────
sub(
    '            .collar-divider { background-color: rgba(200, 168, 78, 0.1); min-height: 1px; }\n',
    '            .collar-divider { background-color: rgba(200, 168, 78, 0.1); min-height: 1px; }\n'
    '            .collar-task { color: #c8a84e; font-size: 15px; font-weight: 300; }\n'
    '            .collar-task-reps { color: #c8a84e; font-size: 12px; font-weight: 300; letter-spacing: 3px; }\n'
    '            .collar-task-entry textview, .collar-task-entry text {\n'
    '                background-color: rgba(0, 0, 0, 0.45); color: #ddccaa;\n'
    '                font-size: 14px; font-weight: 300;\n'
    '            }\n'
    '            .collar-task-entry { border: 1px solid rgba(200, 168, 78, 0.25); border-radius: 8px; padding: 8px; }\n'
    '            .collar-task-status { color: #aa8866; font-size: 13px; font-weight: 300; }\n'
    '            .collar-task-status-bad { color: #cc4422; font-size: 13px; font-weight: 300; }\n'
    '            .collar-task-status-good { color: #66aa44; font-size: 13px; font-weight: 300; }\n',
)

# ── 2b. Build the panel onto the lock card, under the taunt ────────────────
sub(
    '        self.taunt_label = Gtk.Label(label=state.current_taunt or (random.choice(TAUNTS) if TAUNTS else ""))\n'
    '        self.taunt_label.add_css_class("collar-taunt")\n'
    '        card.append(self.taunt_label)\n',
    '        self.taunt_label = Gtk.Label(label=state.current_taunt or (random.choice(TAUNTS) if TAUNTS else ""))\n'
    '        self.taunt_label.add_css_class("collar-taunt")\n'
    '        card.append(self.taunt_label)\n'
    '\n'
    '        # Veneration task panel — only on the primary window, so bunny types\n'
    '        # once rather than once per monitor.\n'
    '        if primary:\n'
    '            self.task_view = None\n'
    '            self.task_label = None\n'
    '            self.task_reps_label = None\n'
    '            self.task_status_label = None\n'
    '            if state.task_text or state.word_min:\n'
    '                self._build_veneration_panel(card)\n',
)

# ── 2c/3/4. The panel, the verifier, and the release ───────────────────────
sub(
    '    def enforce_fullscreen_loop(self):\n',
    '''    def _build_veneration_panel(self, card):
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
        self.task_label.set_selectable(False)  # no copy-paste shortcut out of the task
        card.append(self.task_label)

        self.task_reps_label = Gtk.Label(label=self._veneration_reps_text())
        self.task_reps_label.add_css_class("collar-task-reps")
        card.append(self.task_reps_label)

        self.task_view = Gtk.TextView()
        self.task_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.task_view.set_size_request(560, 180)
        self.task_view.set_accepts_tab(False)  # Tab stays blocked as a nav key
        self.task_view.add_css_class("collar-task-entry")
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.set_size_request(560, 180)
        scroller.set_child(self.task_view)
        card.append(scroller)

        self.task_status_label = Gtk.Label(label=state.task_status or "")
        self.task_status_label.add_css_class("collar-task-status")
        self.task_status_label.set_wrap(True)
        self.task_status_label.set_max_width_chars(64)
        card.append(self.task_status_label)

        submit = Gtk.Button(label="Submit to Them")
        submit.set_margin_top(8)
        submit.connect("clicked", self._on_veneration_submit)
        card.append(submit)

        def _focus_task_view():
            self.task_view.grab_focus()
            return False  # once only; grab_focus() returns True and would repeat

        GLib.idle_add(_focus_task_view)

    def _veneration_reps_text(self):
        total = max(1, int(state.task_reps or 0))
        return f"REP {min(state.task_reps_done + 1, total)} OF {total}"

    @staticmethod
    def _normalise_veneration(text, randcaps):
        """Collapse whitespace so line wrapping never fails a correct answer."""
        out = re.sub(r"\\s+", " ", (text or "")).strip()
        return out if randcaps else out.casefold()

    def _set_veneration_status(self, text, kind=""):
        state.task_status = text
        if not self.task_status_label:
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
        if not self.task_view:
            return
        buf = self.task_view.get_buffer()
        typed = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

        if state.task_text:
            want = self._normalise_veneration(state.task_text, state.task_randcaps)
            got = self._normalise_veneration(typed, state.task_randcaps)
            if got != want:
                if not got:
                    self._set_veneration_status("Nothing typed. They are waiting.", "bad")
                else:
                    self._set_veneration_status(
                        "That is not what They asked for. Read it again and type it in full.", "bad"
                    )
                return
        else:
            words = len((typed or "").split())
            if words < state.word_min:
                self._set_veneration_status(
                    f"{words} of {state.word_min} words. Keep going.", "bad"
                )
                return

        state.task_reps_done += 1
        total = max(1, int(state.task_reps or 0))
        buf.set_text("", 0)

        if state.task_reps_done < total:
            if self.task_reps_label:
                self.task_reps_label.set_label(self._veneration_reps_text())
            self._set_veneration_status(
                f"Accepted. {total - state.task_reps_done} to go.", "good"
            )
            return

        self._set_veneration_status("Accepted. Thank Them.", "good")
        self._release_after_veneration()

    def _release_after_veneration(self):
        """Task complete — end the lock. Mirrors the unlock_at timer path exactly."""
        logger.info("Veneration task completed (%s reps) — releasing lock", state.task_reps_done)
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
            # Never strand bunny behind a broadcast failure: drop the local lock
            # regardless, and let the next sync reconcile.
            logger.warning("Veneration release broadcast failed: %s", e)
        state.task_gated_on = ""
        state.task_reps_done = 0
        state.locked = False
        self.hide_lock()

    def enforce_fullscreen_loop(self):\n''',
)

# ── 2d. Open the key intercept for the task entry ──────────────────────────
sub(
    '        # Allow typing in webview (for banking login)\n'
    '        if self.webview and self.webview.is_visible():\n'
    '            return False  # Let it through\n'
    '        return True  # Block everything else when no webview\n',
    '        # Allow typing in webview (for banking login)\n'
    '        if self.webview and self.webview.is_visible():\n'
    '            return False  # Let it through\n'
    '        # Allow typing into the veneration entry. The blocked list above still\n'
    '        # applies, so Escape/Super/Tab/Alt+F4 stay dead while the entry is live.\n'
    '        if getattr(self, "task_view", None) is not None:\n'
    '            if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and (mod & Gdk.ModifierType.CONTROL_MASK):\n'
    '                self._on_veneration_submit()\n'
    '                return True\n'
    '            if self.task_view.has_focus():\n'
    '                return False  # Let it through to the buffer\n'
    '        return True  # Block everything else when no webview\n',
)

# ── refresh the panel on mid-lock order changes ────────────────────────────
sub(
    '                if hasattr(self, "taunt_label") and self.taunt_label:\n'
    '                    self.taunt_label.set_label(state.current_taunt)\n',
    '                if hasattr(self, "taunt_label") and self.taunt_label:\n'
    '                    self.taunt_label.set_label(state.current_taunt)\n'
    '                if getattr(self, "task_label", None) and state.task_text:\n'
    '                    self.task_label.set_label(state.task_text)\n'
    '                if getattr(self, "task_reps_label", None):\n'
    '                    self.task_reps_label.set_label(self._veneration_reps_text())\n',
)

# ── re is used by the normaliser ───────────────────────────────────────────
if not re.search(r'^import re$', src, re.M):
    sub('import threading\n', 'import re\nimport threading\n')

io.open(PATH, "w", encoding="utf-8").write(src)
print(f"applied {n} hunks")

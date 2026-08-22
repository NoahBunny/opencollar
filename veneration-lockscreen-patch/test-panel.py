#!/usr/bin/env python3
"""Standalone harness for the veneration panel.

Renders the SAME panel, entry and verification logic as the patched lockscreen,
against a dummy task. Touches nothing: no orders.json, no mesh, no service, no
lock. The release path is stubbed to a label so completing it proves the flow
without unlocking anything real.
"""
import re
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

TASK_TEXT = "I belong to the Lion, and I asked for this. Their grip does not slip."
TASK_REPS = 2
TASK_RANDCAPS = 1  # 1 = capitalisation must match exactly
WORD_MIN = 0


class Harness(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.focuslock.venerationtest")
        self.reps_done = 0

    @staticmethod
    def _normalise(text, randcaps):
        out = re.sub(r"\s+", " ", (text or "")).strip()
        return out if randcaps else out.casefold()

    def _reps_text(self):
        total = max(1, TASK_REPS)
        return f"REP {min(self.reps_done + 1, total)} OF {total}"

    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self, title="Veneration panel — TEST")
        win.set_default_size(680, 560)

        css = Gtk.CssProvider()
        css.load_from_string("""
            * { font-family: "Lexend", "Lexend Deca", sans-serif; }
            window { background-color: rgba(4, 4, 8, 0.98); }
            .collar-backdrop { background-color: rgba(8,8,15,0.85); border-radius: 24px;
                padding: 32px 40px; border: 1px solid rgba(200,168,78,0.15); }
            .collar-task { color: #c8a84e; font-size: 15px; font-weight: 300; }
            .collar-task-reps { color: #c8a84e; font-size: 12px; letter-spacing: 3px; }
            .collar-task-entry textview, .collar-task-entry text {
                background-color: rgba(0,0,0,0.45); color: #ddccaa; font-size: 14px; }
            .collar-task-entry { border: 1px solid rgba(200,168,78,0.25);
                border-radius: 8px; padding: 8px; }
            .collar-task-status { color: #aa8866; font-size: 13px; }
            .collar-task-status-bad { color: #cc4422; font-size: 13px; }
            .collar-task-status-good { color: #66aa44; font-size: 13px; }
        """)
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(), css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
        )

        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        card.add_css_class("collar-backdrop")
        card.set_halign(Gtk.Align.CENTER)
        card.set_valign(Gtk.Align.CENTER)

        self.task_label = Gtk.Label(label=TASK_TEXT or f"Write at least {WORD_MIN} words for Them.")
        self.task_label.add_css_class("collar-task")
        self.task_label.set_wrap(True)
        self.task_label.set_max_width_chars(64)
        self.task_label.set_justify(Gtk.Justification.CENTER)
        self.task_label.set_selectable(False)
        card.append(self.task_label)

        self.reps_label = Gtk.Label(label=self._reps_text())
        self.reps_label.add_css_class("collar-task-reps")
        card.append(self.reps_label)

        self.view = Gtk.TextView()
        self.view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.view.set_accepts_tab(False)
        self.view.add_css_class("collar-task-entry")
        self.scroller = Gtk.ScrolledWindow()
        self.scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.scroller.set_size_request(560, 180)
        self.scroller.set_child(self.view)
        card.append(self.scroller)

        self.status = Gtk.Label(label="Type it exactly. Ctrl+Enter or the button.")
        self.status.add_css_class("collar-task-status")
        self.status.set_wrap(True)
        self.status.set_max_width_chars(64)
        card.append(self.status)

        self.btn = Gtk.Button(label="Submit to Them")
        self.btn.connect("clicked", self.on_submit)
        card.append(self.btn)
        self.completed = False
        self.win = win

        # PASTE IS NOT TYPING. One veto on insert-text catches every vector:
        # Ctrl+V, middle-click primary selection, drag-and-drop, context menu.
        self.view.get_buffer().connect("insert-text", self.on_insert)

        # CAPTURE phase on the VIEW: a focused TextView eats Return before a
        # BUBBLE-phase window controller sees it. That was the Ctrl+Enter bug.
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self.on_key)
        self.view.add_controller(keys)

        clicks = Gtk.GestureClick()
        clicks.set_button(0)
        clicks.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        clicks.connect("pressed", self.on_click)
        self.view.add_controller(clicks)

        win.set_child(card)
        win.present()

        def _focus():
            self.view.grab_focus()
            return False  # the bug that was fixed: grab_focus() returns True

        GLib.idle_add(_focus)

    def on_insert(self, buf, location, text, length):
        if len(text) > 2:
            buf.stop_emission_by_name("insert-text")
            self._set_status("Type it. Pasting is not typing.", "bad")

    def on_key(self, controller, keyval, keycode, mod):
        ctrl = bool(mod & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mod & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.on_submit()
            return True
        if (ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V, Gdk.KEY_Insert)) or (shift and keyval == Gdk.KEY_Insert):
            self._set_status("Type it. Pasting is not typing.", "bad")
            return True
        return False

    def on_click(self, gesture, n_press, x, y):
        if gesture.get_current_button() in (2, 3):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._set_status("Type it. Pasting is not typing.", "bad")

    def _set_status(self, text, kind=""):
        for c in ("collar-task-status", "collar-task-status-bad", "collar-task-status-good"):
            self.status.remove_css_class(c)
        self.status.add_css_class(
            "collar-task-status-bad" if kind == "bad"
            else "collar-task-status-good" if kind == "good"
            else "collar-task-status"
        )
        self.status.set_label(text)

    def on_submit(self, _btn=None):
        if self.completed:
            return
        buf = self.view.get_buffer()
        typed = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False)

        if TASK_TEXT:
            if self._normalise(typed, TASK_RANDCAPS) != self._normalise(TASK_TEXT, TASK_RANDCAPS):
                if not typed.strip():
                    self._set_status("Nothing typed. They are waiting.", "bad")
                else:
                    self._set_status("That is not what They asked for. Read it again and type it in full.", "bad")
                return
        else:
            words = len(typed.split())
            if words < WORD_MIN:
                self._set_status(f"{words} of {WORD_MIN} words. Keep going.", "bad")
                return

        self.reps_done += 1
        total = max(1, TASK_REPS)
        buf.set_text("", 0)
        if self.reps_done < total:
            self.reps_label.set_label(self._reps_text())
            self._set_status(f"Accepted. {total - self.reps_done} to go.", "good")
            return
        self.completed = True
        for w in (self.scroller, self.btn, self.reps_label):
            w.set_visible(False)
        self.countdown = 3
        self._set_status("Accepted. Thank Them. Releasing in 3\u2026  [TEST]", "good")
        GLib.timeout_add(1000, self._tick)

    def _tick(self):
        self.countdown -= 1
        if self.countdown > 0:
            self._set_status(f"Accepted. Thank Them. Releasing in {self.countdown}\u2026  [TEST]", "good")
            return True
        self.win.close()   # stands in for hide_lock() dropping the real lock
        return False


Harness().run(None)

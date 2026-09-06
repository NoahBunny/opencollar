#!/usr/bin/env python3
"""Standalone harness for the veneration panel.

Renders the SAME panel, entry and verification logic as the patched lockscreen,
against a dummy task. Touches nothing: no orders.json, no mesh, no service, no
lock. The release path is stubbed to a label so completing it proves the flow
without unlocking anything real.

Prints a report on exit: reps completed, attempts per rep, and why each rejected
attempt was rejected. Set FOCUSLOCK_VENERATION_REPORT to a path to also write it
as JSON, which is what makes a sweep over all 126 tasks readable.
"""

import json
import os
import re
import time

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

TASK_TEXT = "I belong to the Lion, and I asked for this. Their grip does not slip."
TASK_REPS = 2
TASK_RANDCAPS = 1  # 1 = capitalisation must match exactly
WORD_MIN = 0


class ClipboardWatch:
    """Keeps the clipboard and primary-selection text to hand, for telling a
    paste from a compose.

    `len(text) > 2` cannot do that. It fires on any lump insertion, and a
    dead-key or IME commit is a lump insertion — so on a layout like Canadian
    Multilingual Standard an honest typist trips it. Comparing the inserted
    text against what is actually on a clipboard can tell them apart.

    GTK4 has no synchronous clipboard read (read_text_async only, and no
    `changed` signal — notify::formats stands in), while insert-text must
    decide before it can veto. So read in the background and answer from cache.

    A cold cache answers None, meaning "cannot prove", which is NOT the same as
    "not a paste" and must never be billed as either.
    """

    def __init__(self, display):
        self._text = {}
        self._clipboards = {
            "clipboard": display.get_clipboard(),
            "primary": display.get_primary_clipboard(),
        }
        for name, cb in self._clipboards.items():
            self._text[name] = None
            cb.connect("notify::formats", self._on_formats, name)
            self._refresh(cb, name)

    def _on_formats(self, cb, _pspec, name):
        # Contents changed: the cached copy is wrong until the read lands, and a
        # wrong copy is worse than none — it could clear a real paste.
        self._text[name] = None
        self._refresh(cb, name)

    def _refresh(self, cb, name):
        def done(clipboard, res):
            try:
                self._text[name] = clipboard.read_text_finish(res)
            except GLib.Error:
                self._text[name] = None  # non-text contents, or the read was refused

        cb.read_text_async(None, done)

    @staticmethod
    def _norm(text):
        return " ".join((text or "").split())

    def proves_paste(self, inserted):
        """True if `inserted` is sitting on a clipboard, False if it demonstrably
        is not, None if nothing can be proved right now."""
        norm = self._norm(inserted)
        if not norm:
            return None
        known = [v for v in self._text.values() if v]
        if not known:
            return None  # cold: nothing read yet, or neither holds text
        for value in known:
            whole = self._norm(value)
            if norm == whole or (len(norm) > 8 and norm in whole):
                return True
        return False


class Harness(Gtk.Application):
    def __init__(self):
        super().__init__(application_id="org.focuslock.venerationtest")
        self.reps_done = 0
        self.attempts = []  # one dict per submit or blocked paste
        self.rep_attempts = 0  # attempts against the rep in progress
        self.started = time.monotonic()
        self.rep_started = self.started
        self.completed = False
        self.clipboard = None
        self.warned = False  # the one free warning, scoped to this session
        self.billable = 0

    @staticmethod
    def _normalise(text, randcaps):
        out = re.sub(r"\s+", " ", (text or "")).strip()
        return out if randcaps else out.casefold()

    @staticmethod
    def _cap_mismatches(typed, expected):
        """Words that match letter-for-letter but not case, as (typed, wanted).

        This is the pronoun rule's failure mode and the reason the report exists:
        with randcaps on, typing "you" for "You" fails the task on that alone,
        and a flat "wrong text" verdict buries the one detail worth knowing.
        Word counts must line up for the pairing to mean anything; when they do
        not, the attempt is a wording problem and this returns nothing."""
        t = re.sub(r"\s+", " ", typed or "").strip().split(" ")
        e = re.sub(r"\s+", " ", expected or "").strip().split(" ")
        if len(t) != len(e):
            return []
        return [(a, b) for a, b in zip(t, e, strict=True) if a != b and a.casefold() == b.casefold()]

    def _paste_attempt(self, route, proven, detail=None):
        """Block, then decide whether this one is chargeable.

        Blocking is unconditional — enforcement must not weaken just because the
        clipboard could not be read. Billing needs proof: an explicit paste
        keystroke or gesture proves intent by itself, while a lump insertion
        only proves it when the text is demonstrably what is on the clipboard.

        The first chargeable attempt in a session spends the warning. In the
        real lockscreen the rest would be reported to the relay to be priced
        there; this harness only ever says what would happen.
        """
        entry = {"route": route, "proven": bool(proven)}
        entry.update(detail or {})
        if not proven:
            entry["billable"] = False
            entry["why"] = "unproven — blocked but not chargeable"
            self._record("blocked-paste", entry)
            self._set_status("Type it. Pasting is not typing.", "bad")
            return
        if not self.warned:
            self.warned = True
            entry["billable"] = False
            entry["why"] = "first proven attempt — warning spent"
            self._record("blocked-paste", entry)
            self._set_status("Type it. Pasting is not typing. That is your warning.", "bad")
            return
        self.billable += 1
        entry["billable"] = True
        self._record("blocked-paste", entry)
        self._set_status("Type it. Pasting is not typing. That one counts.", "bad")

    def _record(self, verdict, detail=None):
        self.rep_attempts += 1
        entry = {
            "rep": self.reps_done + 1,
            "attempt": self.rep_attempts,
            "verdict": verdict,
            "seconds": round(time.monotonic() - self.rep_started, 1),
        }
        if detail:
            entry.update(detail)
        self.attempts.append(entry)

    def _reps_text(self):
        total = max(1, TASK_REPS)
        return f"REP {min(self.reps_done + 1, total)} OF {total}"

    def do_activate(self):
        win = Gtk.ApplicationWindow(application=self, title="Veneration panel — TEST")
        self.clipboard = ClipboardWatch(win.get_display())
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
            proven = self.clipboard.proves_paste(text) if self.clipboard else None
            self._paste_attempt("insert", proven is True, {"chars": len(text), "clipboard_match": proven})

    def on_key(self, controller, keyval, keycode, mod):
        ctrl = bool(mod & Gdk.ModifierType.CONTROL_MASK)
        shift = bool(mod & Gdk.ModifierType.SHIFT_MASK)
        if ctrl and keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self.on_submit()
            return True
        if (ctrl and keyval in (Gdk.KEY_v, Gdk.KEY_V, Gdk.KEY_Insert)) or (shift and keyval == Gdk.KEY_Insert):
            self._paste_attempt("keyboard", True)
            return True
        return False

    def on_click(self, gesture, n_press, x, y):
        if gesture.get_current_button() in (2, 3):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self._paste_attempt("mouse", True)

    def _set_status(self, text, kind=""):
        for c in ("collar-task-status", "collar-task-status-bad", "collar-task-status-good"):
            self.status.remove_css_class(c)
        self.status.add_css_class(
            "collar-task-status-bad"
            if kind == "bad"
            else "collar-task-status-good"
            if kind == "good"
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
                    self._record("empty")
                    self._set_status("Nothing typed. They are waiting.", "bad")
                else:
                    # Separate "got the words, missed the case" from "got the
                    # words wrong". Only the first is a pronoun-rule failure,
                    # and only when randcaps is on does it fail at all.
                    caps = (
                        self._cap_mismatches(typed, TASK_TEXT)
                        if self._normalise(typed, False) == self._normalise(TASK_TEXT, False)
                        else []
                    )
                    if caps:
                        self._record(
                            "capitalisation",
                            {
                                "words": [{"typed": a, "wanted": b} for a, b in caps],
                            },
                        )
                    else:
                        self._record("wording", {"typed_words": len(typed.split())})
                    self._set_status("That is not what They asked for. Read it again and type it in full.", "bad")
                return
        else:
            words = len(typed.split())
            if words < WORD_MIN:
                self._record("short", {"words": words, "needed": WORD_MIN})
                self._set_status(f"{words} of {WORD_MIN} words. Keep going.", "bad")
                return

        self._record("accepted")
        self.reps_done += 1
        self.rep_attempts = 0
        self.rep_started = time.monotonic()
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
        self.win.close()  # stands in for hide_lock() dropping the real lock
        return False

    def report(self):
        """Print what happened, after the window is gone.

        Closing the window early and finishing the task look identical from the
        outside — same clean exit, same silence — so the first line says which
        it was. Everything else exists to make a sweep over all 126 tasks
        legible: a task that repeatedly draws capitalisation rejections is one
        whose wording fights the pronoun rule, and that is worth knowing before
        the Lion sets it rather than after."""
        total = max(1, TASK_REPS)
        elapsed = round(time.monotonic() - self.started, 1)
        rejected = [a for a in self.attempts if a["verdict"] not in ("accepted",)]
        caps = [a for a in rejected if a["verdict"] == "capitalisation"]

        print()
        if self.completed:
            print(f"COMPLETED  {self.reps_done}/{total} reps in {elapsed}s")
        else:
            print(f"ABANDONED  {self.reps_done}/{total} reps done, closed after {elapsed}s")
        print(f"  task      {TASK_TEXT!r}")
        print(f"  randcaps  {'on — capitalisation enforced' if TASK_RANDCAPS else 'off'}")
        print(f"  attempts  {len(self.attempts)} total, {len(rejected)} rejected")

        for a in self.attempts:
            line = f"    rep {a['rep']} attempt {a['attempt']}: {a['verdict']} ({a['seconds']}s)"
            if a["verdict"] == "capitalisation":
                pairs = ", ".join(f"{w['typed']!r} should be {w['wanted']!r}" for w in a["words"])
                line += f" — {pairs}"
            elif a["verdict"] == "wording":
                line += f" — typed {a['typed_words']} words, wanted {len(TASK_TEXT.split())}"
            elif a["verdict"] == "blocked-paste":
                mark = "BILLABLE" if a.get("billable") else "not billable"
                line += f" — {a['route']}, {mark}"
                if a.get("why"):
                    line += f" ({a['why']})"
            print(line)

        if caps:
            print(f"  NOTE: {len(caps)} rejection(s) were capitalisation only — the pronoun rule bit.")

        pastes = [a for a in self.attempts if a["verdict"] == "blocked-paste"]
        if pastes:
            unproven = [a for a in pastes if not a["proven"]]
            print(f"  PASTE: {len(pastes)} blocked, {self.billable} chargeable, {len(unproven)} unproven")
            if unproven:
                print("         unproven attempts were blocked but not charged — the clipboard")
                print("         could not corroborate them, and a compose looks the same from here.")
            if self.billable:
                print(f"         the real lockscreen would report {self.billable} to the relay to be priced.")

        dest = os.environ.get("FOCUSLOCK_VENERATION_REPORT")
        if dest:
            payload = {
                "task_text": TASK_TEXT,
                "task_reps": total,
                "task_randcaps": TASK_RANDCAPS,
                "word_min": WORD_MIN,
                "completed": self.completed,
                "reps_done": self.reps_done,
                "paste_billable": self.billable,
                "paste_warning_spent": self.warned,
                "seconds": elapsed,
                "attempts": self.attempts,
            }
            with open(dest, "w") as fh:
                json.dump(payload, fh, indent=2)
            print(f"  wrote {dest}")


_app = Harness()
_app.run(None)
_app.report()

#!/usr/bin/env python3
"""adb-level UI driver: dump the view tree, find a node, tap it.

Extracted from `qa_device_ui.py`, which has driven real-device QA since
2026-08-07 (see `docs/HANDOFF-2026-08-07-device-qa.md`). That CLI re-dumps the
whole tree per invocation, which is fine when a human is typing commands and
far too slow for a twenty-step scripted walk — hence this importable form.

**Why not uiautomator2.** The 2026-04 spike used it and it wedged Waydroid's
adbd at `_setup_jar` → `toybox md5sum`, requiring a container restart to
recover; `docs/UI-AUTOMATION-DECISION.md` shelved UI automation on the strength
of that. The failure was in uiautomator2's atx-agent/JAR layer, not in adb or
uiautomator itself. This driver pushes nothing to the device and starts no
agent: it shells out to the `uiautomator` binary that is already there, reads
XML back, and sends `input tap`. Two layers instead of four, and none of them
is the one that broke.

The parsing half is deliberately pure so it can be tested without a device —
see `tests/test_uiauto.py`.
"""

from __future__ import annotations

import re
import subprocess
import time

BOUNDS_RE = re.compile(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"')
NODE_RE = re.compile(r"<node[^>]*>")


def _attr(tag: str, name: str) -> str:
    m = re.search(rf'{name}="([^"]*)"', tag)
    return m.group(1) if m else ""


def parse_nodes(xml: str) -> list[dict]:
    """Every node in a `uiautomator dump`, with its centre point precomputed.

    Pure: hand it XML from anywhere and it behaves identically.
    """
    out = []
    for m in NODE_RE.finditer(xml or ""):
        tag = m.group(0)
        b = BOUNDS_RE.search(tag)
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        out.append(
            {
                "text": _attr(tag, "text"),
                "id": _attr(tag, "resource-id"),
                "desc": _attr(tag, "content-desc"),
                "cls": _attr(tag, "class"),
                "checked": _attr(tag, "checked") == "true",
                "enabled": _attr(tag, "enabled") == "true",
                "cx": (x1 + x2) // 2,
                "cy": (y1 + y2) // 2,
                "w": x2 - x1,
                "h": y2 - y1,
                "bounds": f"[{x1},{y1}][{x2},{y2}]",
            }
        )
    return out


def match(nodes: list[dict], needle: str) -> dict | None:
    """Find one node. An exact text match wins over a substring hit, so
    `match(ns, "Lock")` cannot grab "Lock all devices" while a bare "Lock" tab
    is on screen — which is exactly the ambiguity this app's tab bar creates.

    A needle containing "/" is treated as a resource-id suffix and matched only
    against ids, so `match(ns, "id/btn_lock")` never lands on a label.
    """
    needle = (needle or "").lower()
    if not needle:
        return None
    if "/" in needle:
        for n in nodes:
            if n["id"].lower().endswith(needle):
                return n
        return None
    for n in nodes:
        if n["text"].lower() == needle:
            return n
    for n in nodes:
        if needle in n["text"].lower() or needle in n["id"].lower() or needle in n["desc"].lower():
            return n
    return None


def escape_input_text(s: str) -> str:
    """`adb shell input text` splits on spaces and eats shell metacharacters."""
    out = s.replace("%", "%%").replace(" ", "%s")
    for ch in "()<>|;&*\\~\"'`$":
        out = out.replace(ch, "\\" + ch)
    return out


class UiDevice:
    def __init__(self, serial: str, adb: str = "adb", verbose: bool = False):
        self.serial = serial
        self.adb_bin = adb
        self.verbose = verbose

    # ── plumbing ──────────────────────────────────────────────────────
    def adb(self, *args: str, timeout: int = 60) -> str:
        r = subprocess.run(
            [self.adb_bin, "-s", self.serial, *args],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if self.verbose and r.returncode != 0:
            print(f"    adb {' '.join(args)} -> rc={r.returncode} {r.stderr.strip()[:120]}")
        return r.stdout

    def adb_binary(self, *args: str, timeout: int = 60) -> bytes:
        return subprocess.run([self.adb_bin, "-s", self.serial, *args], capture_output=True, timeout=timeout).stdout

    # ── reading the screen ────────────────────────────────────────────
    def dump(self, retries: int = 5) -> str:
        """Raw view-tree XML. Retries: uiautomator returns an empty dump while
        the window is still animating, which is most of the time right after a
        tap."""
        for _ in range(retries):
            self.adb("shell", "uiautomator", "dump", "/sdcard/ui.xml", timeout=45)
            xml = self.adb("shell", "cat", "/sdcard/ui.xml", timeout=45)
            if "<node" in xml:
                return xml
            time.sleep(1.0)
        return ""

    def nodes(self) -> list[dict]:
        return parse_nodes(self.dump())

    def find(self, needle: str, nodes: list[dict] | None = None) -> dict | None:
        return match(self.nodes() if nodes is None else nodes, needle)

    def visible(self, needle: str) -> bool:
        return self.find(needle) is not None

    def wait_for(self, needle: str, timeout: float = 20) -> bool:
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.find(needle):
                return True
            time.sleep(1.0)
        return False

    # ── acting on it ──────────────────────────────────────────────────
    def tap(self, needle: str, settle: float = 1.2) -> bool:
        n = self.find(needle)
        if not n:
            return False
        self.adb("shell", "input", "tap", str(n["cx"]), str(n["cy"]))
        time.sleep(settle)
        return True

    def type_into(self, needle: str, text: str, settle: float = 0.8) -> bool:
        n = self.find(needle)
        if not n:
            return False
        self.adb("shell", "input", "tap", str(n["cx"]), str(n["cy"]))
        time.sleep(0.4)
        self.adb("shell", "input", "text", escape_input_text(text))
        time.sleep(settle)
        return True

    def clear_field(self, needle: str) -> bool:
        n = self.find(needle)
        if not n:
            return False
        self.adb("shell", "input", "tap", str(n["cx"]), str(n["cy"]))
        time.sleep(0.3)
        # Select-all then delete; works without knowing the current length.
        self.adb("shell", "input", "keyevent", "--longpress", "KEYCODE_A")
        self.adb("shell", "input", "keyevent", "KEYCODE_MOVE_END")
        for _ in range(60):
            self.adb("shell", "input", "keyevent", "KEYCODE_DEL")
        return True

    def back(self, settle: float = 1.0) -> None:
        self.adb("shell", "input", "keyevent", "KEYCODE_BACK")
        time.sleep(settle)

    def launch(self, pkg: str, activity: str | None = None, settle: float = 4.0, attempts: int = 3) -> bool:
        """Bring an app to the foreground, and confirm it got there.

        Prefers an explicit `am start -n`: on Waydroid `monkey` reports success
        and leaves the launcher resumed, which produced a whole run of
        cascading "control not found" failures that were really one failure.
        Returns whether the package is actually resumed.
        """
        for _ in range(attempts):
            if activity:
                self.adb("shell", "am", "start", "-n", f"{pkg}/{activity}")
            else:
                self.adb("shell", "monkey", "-p", pkg, "-c", "android.intent.category.LAUNCHER", "1")
            time.sleep(settle)
            if pkg in self.current_activity():
                return True
        return pkg in self.current_activity()

    def force_stop(self, pkg: str) -> None:
        self.adb("shell", "am", "force-stop", pkg)
        time.sleep(1.0)

    def screenshot(self, path) -> bool:
        png = self.adb_binary("exec-out", "screencap", "-p")
        if not png.startswith(b"\x89PNG"):
            return False
        with open(path, "wb") as f:
            f.write(png)
        return True

    def current_activity(self) -> str:
        """The resumed component, e.g. "com.focusctl/.MainActivity".

        Reads `topResumedActivity` / `ResumedActivity:` — the field is NOT
        called `mResumedActivity` on Android 13, which is how an earlier
        version of this returned "" while the app was plainly running.
        """
        out = self.adb("shell", "dumpsys", "activity", "activities")
        m = re.search(r"ResumedActivity[=:]\s*ActivityRecord\{\S+\s+\S+\s+([\w.]+/[\w.$]+)", out)
        return m.group(1) if m else ""

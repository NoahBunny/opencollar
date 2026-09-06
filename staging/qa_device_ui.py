#!/usr/bin/env python3
"""Tiny uiautomator driver for on-device QA — dump / tap-by-text / type.

Lets a device-QA session drive the real apps from the laptop instead of tapping
by hand or building an instrumented APK. Used 2026-08-07 to script the whole
Lion's Share onboarding → Direct Pair → lock → charge → unlock sequence against
a Pixel 10 + SM-S908W (see docs/HANDOFF-2026-08-07-device-qa.md).

  qa_device_ui.py <serial> dump               # text | resource-id | bounds per node
  qa_device_ui.py <serial> tap <text>         # tap node whose text/id/desc contains <text>
  qa_device_ui.py <serial> type <id> <value>  # tap a field, then type into it
  qa_device_ui.py <serial> wait <text> [secs] # poll until <text> appears (exit 1 on timeout)

Matching is case-insensitive; an exact text match wins over a substring hit, so
'tap PAIR' won't grab 'PAIR DIRECT (LAN)' when both are on screen.
"""

import re
import subprocess
import sys
import time

SERIAL = sys.argv[1]
CMD = sys.argv[2]


def adb(*args, **kw):
    return subprocess.run(["adb", "-s", SERIAL, *args], capture_output=True, text=True, **kw).stdout


def dump():
    for _ in range(4):
        adb("shell", "uiautomator", "dump", "/sdcard/ui.xml")
        xml = adb("shell", "cat", "/sdcard/ui.xml")
        if "<node" in xml:
            return xml
        time.sleep(1)
    return ""


def nodes(xml):
    out = []
    for m in re.finditer(r"<node[^>]*>", xml):
        tag = m.group(0)

        def attr(name, tag=tag):
            # `tag` bound as a default so the closure can't drift to a later
            # iteration's value. Every call today happens inside the iteration
            # that made it, so this is correctness-by-construction, not a fix.
            mm = re.search(rf'{name}="([^"]*)"', tag)
            return mm.group(1) if mm else ""

        b = re.search(r'bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"', tag)
        if not b:
            continue
        x1, y1, x2, y2 = map(int, b.groups())
        out.append(
            {
                "text": attr("text"),
                "id": attr("resource-id"),
                "desc": attr("content-desc"),
                "cls": attr("class"),
                "cx": (x1 + x2) // 2,
                "cy": (y1 + y2) // 2,
                "bounds": f"[{x1},{y1}][{x2},{y2}]",
            }
        )
    return out


def find(ns, needle):
    needle = needle.lower()
    exact = [n for n in ns if n["text"].lower() == needle]
    if exact:
        return exact[0]
    for n in ns:
        if needle in n["text"].lower() or needle in n["id"].lower() or needle in n["desc"].lower():
            return n
    return None


ns = nodes(dump())

if CMD == "dump":
    for n in ns:
        if n["text"] or n["id"] or n["desc"]:
            print(f"{n['text'][:60]!r:64} id={n['id'].split('/')[-1]:28} {n['bounds']}")
elif CMD == "tap":
    n = find(ns, sys.argv[3])
    if not n:
        print(f"NOT FOUND: {sys.argv[3]}")
        sys.exit(1)
    adb("shell", "input", "tap", str(n["cx"]), str(n["cy"]))
    print(f"tapped {n['text'] or n['id']}")
elif CMD == "type":
    n = find(ns, sys.argv[3])
    if not n:
        print(f"NOT FOUND: {sys.argv[3]}")
        sys.exit(1)
    adb("shell", "input", "tap", str(n["cx"]), str(n["cy"]))
    time.sleep(0.5)
    adb("shell", "input", "text", sys.argv[4].replace(" ", "%s"))
    print(f"typed into {n['id'] or n['text']}")
elif CMD == "wait":
    deadline = time.time() + (float(sys.argv[4]) if len(sys.argv) > 4 else 20)
    while time.time() < deadline:
        if find(nodes(dump()), sys.argv[3]):
            print("found")
            sys.exit(0)
        time.sleep(1.5)
    print("TIMEOUT")
    sys.exit(1)

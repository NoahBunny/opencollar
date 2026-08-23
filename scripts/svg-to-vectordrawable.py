#!/usr/bin/env python3
"""Convert the icon SVGs in icons/ into Android VectorDrawable launcher foregrounds.

The SVGs are the source of truth for the app art. This script bakes an affine
fit into the path data so the vector lands in exactly the same place inside the
108x108 adaptive-icon viewport that the old PNG foregrounds occupied -- no
<group> transform, so gradient coordinates live in plain viewport units.

Usage:  python3 scripts/svg-to-vectordrawable.py
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VIEWPORT = 108.0  # adaptive-icon foreground canvas, in dp


# Target art box inside the 108x108 viewport, measured off the 432px PNG
# foregrounds that these vectors replace (pixel bbox / 4).
# The themed-icon (monochrome) layer must sit inside the 66dp safe *circle*, so it
# gets its own box and is flattened to a single colour -- the system tints it and
# only the alpha survives, which is why the gradient is dropped here.
#
# A 66dp box is not the same as a 66dp circle: art that fills the box has corners
# outside it. These sides were measured by rasterising each mono layer and taking
# the farthest ink pixel from centre (bunny 35.12dp, lion 32.90dp against a 33dp
# limit), then scaling so nothing pokes out under a round mask.
def safe(side):
    return ((108.0 - side) / 2, (108.0 - side) / 2, side, side)


TARGETS = {
    "companion": dict(
        svg="icons/bunny-tasker-icon.svg",
        out="android/companion/res/drawable/ic_launcher_foreground.xml",
        box=(23.0, 21.0, 62.5, 65.25),
        recolor={"#1a1a1a": "#A18BC4"},
    ),
    "controller": dict(
        svg="icons/lions-share-icon-v2.svg",
        out="android/controller/res/drawable/ic_launcher_foreground.xml",
        box=(28.75, 19.25, 51.25, 70.0),
        recolor={"#1a1a1a": "#FFFFFF"},
    ),
    "companion-mono": dict(
        svg="icons/bunny-tasker-icon.svg",
        out="android/companion/res/drawable/ic_launcher_monochrome.xml",
        box=safe(61.8),
        recolor={"#1a1a1a": "#FFFFFF"},
        flatten="#FFFFFF",
    ),
    "controller-mono": dict(
        svg="icons/lions-share-icon-v2.svg",
        out="android/controller/res/drawable/ic_launcher_monochrome.xml",
        box=safe(65.8),
        recolor={"#1a1a1a": "#FFFFFF"},
        flatten="#FFFFFF",
    ),
}


# --- path parsing -----------------------------------------------------------

NUM = re.compile(r"[-+]?(?:\d*\.\d+|\d+\.?)(?:[eE][-+]?\d+)?")


def parse_path(d):
    """Parse an M/C/Z path into a list of (cmd, [x, y, ...]) with absolute coords."""
    out = []
    for cmd, chunk in re.findall(r"([MCZmcz])([^MCZmcz]*)", d):
        nums = [float(n) for n in NUM.findall(chunk)]
        if cmd in "Zz":
            out.append(("Z", []))
            continue
        if cmd in "mc":
            raise ValueError(f"relative command {cmd!r} not supported")
        stride = 2 if cmd == "M" else 6
        if len(nums) % stride:
            raise ValueError(f"{cmd}: expected multiples of {stride}, got {len(nums)}")
        for i in range(0, len(nums), stride):
            out.append((cmd if i == 0 else ("L" if cmd == "M" else "C"), nums[i : i + stride]))
    return out


def cubic_extrema(p0, p1, p2, p3):
    """Exact 1-D extrema of a cubic bezier segment (endpoints plus interior roots)."""
    vals = [p0, p3]
    a = -p0 + 3 * p1 - 3 * p2 + p3
    b = 2 * (p0 - 2 * p1 + p2)
    c = p1 - p0
    if abs(a) < 1e-12:
        if abs(b) > 1e-12:
            roots = [-c / b]
        else:
            roots = []
    else:
        disc = b * b - 4 * a * c
        if disc < 0:
            roots = []
        else:
            r = disc**0.5
            roots = [(-b + r) / (2 * a), (-b - r) / (2 * a)]
    for t in roots:
        if 0 < t < 1:
            u = 1 - t
            vals.append(u * u * u * p0 + 3 * u * u * t * p1 + 3 * u * t * t * p2 + t * t * t * p3)
    return vals


def bbox(segs):
    xs, ys, cur, start = [], [], (0.0, 0.0), (0.0, 0.0)
    for cmd, n in segs:
        if cmd == "Z":
            cur = start
        elif cmd in ("M", "L"):
            cur = (n[0], n[1])
            if cmd == "M":
                start = cur
            xs.append(cur[0])
            ys.append(cur[1])
        else:
            xs += cubic_extrema(cur[0], n[0], n[2], n[4])
            ys += cubic_extrema(cur[1], n[1], n[3], n[5])
            cur = (n[4], n[5])
    return min(xs), min(ys), max(xs), max(ys)


def fmt(v):
    s = f"{v:.2f}"
    s = s.rstrip("0").rstrip(".")
    return s if s not in ("", "-0") else "0"


def emit(segs, s, tx, ty):
    """Re-emit path data with the affine fit baked in, dropping repeated command letters."""
    parts, last = [], None
    for cmd, n in segs:
        if cmd == "Z":
            parts.append("Z")
            last = None
            continue
        coords = " ".join(fmt(v * s + (tx if i % 2 == 0 else ty)) for i, v in enumerate(n))
        parts.append(coords if cmd == last else f"{cmd}{coords}")
        last = cmd
    return " ".join(parts)


# --- SVG reading ------------------------------------------------------------


def read_svg(path):
    text = path.read_text()
    grads = {}
    for attrs_src, body in re.findall(r"<linearGradient\b([^>]*)>(?s:(.*?))</linearGradient>", text):
        attrs = dict(re.findall(r'([\w:-]+)="([^"]*)"', attrs_src))
        stops = [dict(re.findall(r'([\w:-]+)="([^"]*)"', m)) for m in re.findall(r"<stop\b([^>]*)/?>", body)]
        grads[attrs["id"]] = (attrs, stops)
    paths = []
    for attrs_src in re.findall(r"<path\b([^>]*)/?>", text):
        a = dict(re.findall(r'([\w:-]+)="([^"]*)"', attrs_src))
        paths.append((a["d"], a.get("fill", "#000000"), a.get("fill-rule", "nonzero")))
    return paths, grads


def pct(v):
    return float(v.rstrip("%")) / 100.0 if v.endswith("%") else float(v)


# --- generation -------------------------------------------------------------


def build(cfg):
    svg = ROOT / cfg["svg"]
    paths, grads = read_svg(svg)
    parsed = [(parse_path(d), fill, rule) for d, fill, rule in paths]

    # Fit the union bbox of all paths into the target box, uniform scale, centered.
    boxes = [bbox(segs) for segs, _, _ in parsed]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    bx, by, bw, bh = cfg["box"]
    s = min(bw / (x1 - x0), bh / (y1 - y0))
    tx = bx + (bw - (x1 - x0) * s) / 2 - x0 * s
    ty = by + (bh - (y1 - y0) * s) / 2 - y0 * s

    flatten = cfg.get("flatten")
    uses_gradient = not flatten and any(f.startswith("url(") for _, f, _ in paths)
    lines = [
        '<?xml version="1.0" encoding="utf-8"?>',
        "<!-- Generated by scripts/svg-to-vectordrawable.py from {}. Do not hand-edit. -->".format(cfg["svg"]),
        '<vector xmlns:android="http://schemas.android.com/apk/res/android"',
    ]
    if uses_gradient:
        lines.append('    xmlns:aapt="http://schemas.android.com/aapt"')
    vp = int(VIEWPORT)
    lines += [
        f'    android:width="{vp}dp"',
        f'    android:height="{vp}dp"',
        f'    android:viewportWidth="{vp}"',
        f'    android:viewportHeight="{vp}">',
    ]

    for (segs, fill, rule), _ in zip(parsed, boxes, strict=True):
        data = emit(segs, s, tx, ty)
        ftype = ' android:fillType="evenOdd"' if rule == "evenodd" else ""
        m = None if flatten else re.match(r"url\(#([^)]+)\)$", fill)
        if not m:
            if flatten:
                lines.append(
                    f'    <path{ftype}\n        android:fillColor="{flatten}"\n        android:pathData="{data}" />'
                )
                continue
            color = cfg["recolor"].get(fill.lower(), fill).upper()
            lines.append(f'    <path{ftype}\n        android:fillColor="{color}"\n        android:pathData="{data}" />')
            continue

        gattrs, stops = grads[m.group(1)]
        gx0, gy0, gx1, gy1 = bbox(segs)  # objectBoundingBox units resolve against this path
        gw, gh = gx1 - gx0, gy1 - gy0

        # Bound as defaults, not captured: this closure is only ever called
        # inside its own iteration today, so the late-binding bug is latent
        # rather than live -- but it is one `defs.append(gpt)` away from real,
        # and B023 is enforced on this branch.
        def gpt(xk, yk, dx, dy, _gx0=gx0, _gy0=gy0, _gw=gw, _gh=gh, _ga=gattrs):
            x = _gx0 + pct(_ga.get(xk, dx)) * _gw
            y = _gy0 + pct(_ga.get(yk, dy)) * _gh
            return fmt(x * s + tx), fmt(y * s + ty)

        sx, sy = gpt("x1", "y1", "0%", "0%")
        ex, ey = gpt("x2", "y2", "100%", "0%")
        lines.append(f'    <path{ftype}\n        android:pathData="{data}">')
        lines.append('        <aapt:attr name="android:fillColor">')
        lines.append(
            '            <gradient\n                android:type="linear"'
            f'\n                android:startX="{sx}"\n                android:startY="{sy}"'
            f'\n                android:endX="{ex}"\n                android:endY="{ey}">'
        )
        for st in stops:
            lines.append(
                '                <item android:offset="{}" android:color="{}" />'.format(
                    fmt(pct(st.get("offset", "0"))), st["stop-color"].upper()
                )
            )
        lines.append("            </gradient>")
        lines.append("        </aapt:attr>")
        lines.append("    </path>")

    lines.append("</vector>")
    out = ROOT / cfg["out"]
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(lines) + "\n")
    print(
        f"{cfg['out'].split('/')[1]:<11} {out.relative_to(ROOT)}  "
        f"(scale {s:.6f}, translate {tx:.3f} {ty:.3f}, {out.stat().st_size} bytes)"
    )


if __name__ == "__main__":
    for name in sys.argv[1:] or TARGETS:
        build(TARGETS[name])

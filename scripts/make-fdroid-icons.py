#!/usr/bin/env python3
"""Render 512x512 F-Droid app icons for the OpenCollar repo.

F-Droid serves whatever `fdroid update` scrapes out of the APK, and the baseline
`repo/icons/` entry is the *mdpi* launcher PNG -- 48x48, which every client then
upscales into mush. Shipping a localised 512px icon per app overrides that.

Art comes from the same vectors as the launcher icons, so these stay crisp at any
size. The Collar has no launcher icon (it is the invisible app) and therefore no
in-APK icon at all, so it gets one from icons/ instead of showing a blank tile.

Usage:  python3 scripts/make-fdroid-icons.py [outdir]
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

# The generator's module name has dashes, so it cannot be imported by name --
# load it by path. (An `import_module(...) if False else None` line used to sit
# here as a leftover of the attempt that could not work.)
_spec = importlib.util.spec_from_file_location("svgvd", Path(__file__).resolve().parent / "svg-to-vectordrawable.py")
svgvd = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(svgvd)

ROOT = Path(__file__).resolve().parent.parent
SIZE = 512

# Art box as a fraction of the icon square, measured off the existing 192px
# launcher PNGs so the F-Droid tile keeps the framing people already recognise.
APPS = {
    "com.bunnytasker": dict(
        svg="icons/bunny-tasker-icon.svg",
        bg="#0a0812",
        fill="#A18BC4",
        frac=(13 / 192, 8 / 192, 168 / 192, 175 / 192),
    ),
    "com.focusctl": dict(
        svg="icons/lions-share-icon-v2.svg",
        bg="#0a0a14",
        fill="#FFFFFF",
        frac=(28 / 192, 3 / 192, 139 / 192, 188 / 192),
    ),
    # The Collar gets the actual collar. Note icons/collar-icon*.png are LIONS
    # despite the filename -- the only real collar art is this one, which is
    # already a finished 512px tile, so it is rasterised as-is rather than
    # reframed. It doubles as the F-Droid repo icon.
    "com.focuslock": dict(svg_raw="icons/opencollar-icon.svg"),
}


def render_svg(cfg, out):
    paths, grads = svgvd.read_svg(ROOT / cfg["svg"])
    parsed = [(svgvd.parse_path(d), fill, rule) for d, fill, rule in paths]
    boxes = [svgvd.bbox(segs) for segs, _, _ in parsed]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[2] for b in boxes), max(b[3] for b in boxes)
    bx, by, bw, bh = (f * SIZE for f in cfg["frac"])
    s = min(bw / (x1 - x0), bh / (y1 - y0))
    tx = bx + (bw - (x1 - x0) * s) / 2 - x0 * s
    ty = by + (bh - (y1 - y0) * s) / 2 - y0 * s

    defs, body = [], []
    for i, ((segs, fill, rule), _) in enumerate(zip(parsed, boxes, strict=True)):
        d = svgvd.emit(segs, s, tx, ty)
        fr = ' fill-rule="evenodd"' if rule == "evenodd" else ""
        if fill.startswith("url("):
            gid = fill[5:-1]
            gattrs, stops = grads[gid]
            gx0, gy0, gx1, gy1 = svgvd.bbox(segs)
            gw, gh = gx1 - gx0, gy1 - gy0

            # Loop vars bound as defaults -- see the same closure in
            # svg-to-vectordrawable.py, which this mirrors.
            def pt(xk, yk, dx, dy, _gx0=gx0, _gy0=gy0, _gw=gw, _gh=gh, _ga=gattrs):
                x = _gx0 + svgvd.pct(_ga.get(xk, dx)) * _gw
                y = _gy0 + svgvd.pct(_ga.get(yk, dy)) * _gh
                return x * s + tx, y * s + ty

            sx, sy = pt("x1", "y1", "0%", "0%")
            ex, ey = pt("x2", "y2", "100%", "0%")
            st = "".join(f'<stop offset="{o.get("offset", "0")}" stop-color="{o["stop-color"]}"/>' for o in stops)
            defs.append(
                f'<linearGradient id="g{i}" gradientUnits="userSpaceOnUse" '
                f'x1="{sx:f}" y1="{sy:f}" x2="{ex:f}" y2="{ey:f}">{st}</linearGradient>'
            )
            body.append(f'<path d="{d}" fill="url(#g{i})"{fr}/>')
        else:
            body.append('<path d="{}" fill="{}"{}/>'.format(d, cfg["fill"], fr))

    svg = (
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{SIZE}" height="{SIZE}" '
        f'viewBox="0 0 {SIZE} {SIZE}"><rect width="{SIZE}" height="{SIZE}" fill="{cfg["bg"]}"/>'
        f"<defs>{''.join(defs)}</defs>{''.join(body)}</svg>"
    )
    tmp = out.with_suffix(".tmp.svg")
    tmp.write_text(svg)
    subprocess.run(["rsvg-convert", "-w", str(SIZE), "-h", str(SIZE), str(tmp), "-o", str(out)], check=True)
    tmp.unlink()


def render_png(cfg, out):
    # Only the box size matters here -- magick centres the art itself, so the
    # box origin the SVG path stays unused.
    _bx, _by, bw, bh = (f * SIZE for f in cfg["frac"])
    subprocess.run(
        [
            "magick",
            str(ROOT / cfg["png"]),
            # the 4096px source is mostly transparent margin -- drop it before fitting
            "-trim",
            "+repage",
            "-resize",
            f"{int(bw)}x{int(bh)}",
            "-background",
            cfg["bg"],
            "-gravity",
            "center",
            "-extent",
            f"{SIZE}x{SIZE}",
            "-alpha",
            "remove",
            "-alpha",
            "off",
            str(out),
        ],
        check=True,
    )


def render_raw(cfg, out):
    """Rasterise a already-composed SVG tile verbatim -- no refit, no recolour."""
    subprocess.run(
        ["rsvg-convert", "-w", str(SIZE), "-h", str(SIZE), str(ROOT / cfg["svg_raw"]), "-o", str(out)], check=True
    )


def main():
    outdir = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "icons" / "fdroid"
    outdir.mkdir(parents=True, exist_ok=True)
    for pkg, cfg in APPS.items():
        out = outdir / (f"{pkg}.png")
        if "svg_raw" in cfg:
            render_raw(cfg, out)
        elif "png" in cfg:
            render_png(cfg, out)
        else:
            render_svg(cfg, out)
        print(f"  {pkg:<18} -> {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()

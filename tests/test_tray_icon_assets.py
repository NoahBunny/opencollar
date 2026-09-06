"""The tray icon is the collar's only always-visible signal, and it is wired by
string.

`focuslock-tray.py` hands AppIndicator a bare icon *name* and a theme path;
`focuslock-desktop-win.py` joins filenames onto `ICONS_DIR`. Nothing links
either to a file on disk, so a renamed asset does not raise — AppIndicator
silently renders nothing and pystray silently falls back to a flat square. The
2026-09-06 crown→bunny rename shipped exactly that class of bug in the Windows
installer, where the destination directory was chosen by `"crown" in icon_name`
and would have filed both new icons somewhere the tray never looks.

So: every icon basename either collar references must exist in `icons/`.

The state rule these assets encode — PURPLE only when the device is BOTH
claimed and connected, GRAY for unclaimed *or* disconnected — is pinned here
too, because gray covering two states is deliberate. A machine no Lion has
approved must never wear the colour that means "held".
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ICONS_DIR = REPO_ROOT / "icons"
TRAY = REPO_ROOT / "focuslock-tray.py"
WIN = REPO_ROOT / "focuslock-desktop-win.py"

CONNECTED_ICON = "bunny-purple.png"
DISCONNECTED_ICON = "bunny-gray.png"


def test_both_tray_assets_exist():
    for name in (CONNECTED_ICON, DISCONNECTED_ICON):
        path = ICONS_DIR / name
        assert path.exists(), f"{name} missing — tray renders nothing"
        # A truncated or placeholder file is the same outage as a missing one.
        assert path.stat().st_size > 2000, f"{name} is implausibly small"


def test_assets_are_square_and_loadable_rgba():
    """The badge compositor reads these with cairo and anchors the paywall
    badge proportionally, so a non-square or non-ARGB base shifts the badge."""
    cairo = pytest.importorskip("cairo", reason="pycairo not installed")
    for name in (CONNECTED_ICON, DISCONNECTED_ICON):
        surface = cairo.ImageSurface.create_from_png(str(ICONS_DIR / name))
        assert surface.get_width() == surface.get_height() == 512, name
        assert surface.get_format() == cairo.FORMAT_ARGB32, name


def test_the_two_states_are_visually_distinguishable():
    """One state must read as coloured and the other as neutral.

    Chroma, not brightness, is what separates these at 22px: #8A5CD9 and
    #8A8A8A sit only ~18 luma apart, and an earlier draft of this test asserted
    a luminance gap of 25 — which the shipped icons fail while being perfectly
    legible. The property that actually matters is that "held" is unmistakably
    purple and "not held" is unmistakably gray, so that is what is measured.

    This also rules out the failure that prompted the recolour: the brand
    lavender #A18BC4 against the old crown gray was two low-chroma tones that
    read as the same icon on a dark panel.
    """
    Image = pytest.importorskip("PIL.Image", reason="Pillow not installed")

    def mean_chroma(name):
        """Mean (max channel - min channel) over opaque pixels. 0 = neutral.

        Reads raw bytes rather than `getdata()`, which Pillow deprecated in 12
        and removes in 14.
        """
        raw = Image.open(ICONS_DIR / name).convert("RGBA").tobytes()
        total, count = 0.0, 0
        for i in range(0, len(raw), 4):
            r, g, b, a = raw[i], raw[i + 1], raw[i + 2], raw[i + 3]
            if a > 200:
                total += max(r, g, b) - min(r, g, b)
                count += 1
        assert count, f"{name} has no opaque pixels"
        return total / count

    connected = mean_chroma(CONNECTED_ICON)
    disconnected = mean_chroma(DISCONNECTED_ICON)
    assert disconnected < 8, f"{DISCONNECTED_ICON} is not neutral (chroma {disconnected:.0f})"
    assert connected > 60, f"{CONNECTED_ICON} is not clearly coloured (chroma {connected:.0f})"


# Files the collars *generate* at runtime rather than ship: lock wallpapers,
# composited badge variants. They will not exist in icons/ and must not be
# mistaken for a broken reference.
_GENERATED = re.compile(r"^(lock-wallpaper|.*-b\d+)\.png$")


def _referenced_icons(path):
    """Every shipped-art `*.png` basename mentioned in a source file."""
    names = set(re.findall(r"[\"']([A-Za-z0-9_-]+\.png)[\"']", path.read_text()))
    return {n for n in names if not _GENERATED.match(n)}


@pytest.mark.parametrize("source", [TRAY, WIN], ids=["linux-tray", "windows-collar"])
def test_every_referenced_icon_exists(source):
    missing = sorted(n for n in _referenced_icons(source) if not (ICONS_DIR / n).exists())
    assert not missing, f"{source.name} references icons not in icons/: {missing}"


def test_linux_tray_points_at_the_bunny():
    text = TRAY.read_text()
    assert f'ICON_CONNECTED = ICONS_DIR / "{CONNECTED_ICON}"' in text
    assert f'ICON_DISCONNECTED = ICONS_DIR / "{DISCONNECTED_ICON}"' in text
    # The stale-badge sweep globs on the base names; a mismatched pattern
    # leaves every previous run's composited badge file on disk forever.
    assert 'glob("bunny-*-b*.png")' in text
    assert '"bunny-purple" if connected else "bunny-gray"' in text


def test_purple_requires_pairing_on_both_collars():
    """Windows keyed this on `state.connected` alone until 2026-09-06, so an
    unclaimed-but-reachable collar advertised a Lion who had never approved it.
    Both sides must require pairing AND connectivity."""
    assert "live = paired and connected" in TRAY.read_text()

    win = WIN.read_text()
    assert "return _is_paired() and state.connected" in win
    # ...and the redraw loop must watch the same signal, or the icon never
    # repaints when pairing is what changed.
    assert "new_live = _is_live()" in win


def test_windows_install_routes_tray_art_to_the_icons_dir():
    """The destination was chosen by `"crown" in icon_name`; after the rename
    that test matched nothing and both bunnies would have gone to CONFIG_DIR,
    which the tray does not search."""
    win = WIN.read_text()
    assert 'icon_name.startswith("bunny-")' in win
    assert '"crown" in icon_name' not in win

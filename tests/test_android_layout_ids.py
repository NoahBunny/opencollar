"""The layout↔code contract the compiler cannot check.

`MainActivity` does not reference views through the generated `R` class. It
looks every one of them up by *string*:

    private int getId(String name) {
        return getResources().getIdentifier(name, "id", getPackageName());
    }

Nothing links that string to the layout. Rename or move a view and
`getIdentifier` returns 0, `findViewById(0)` returns null, and the app either
NPEs on `setOnClickListener` or — given the ~20 `if (x != null)` guards around
these lookups — **silently does nothing at all**. A control that quietly stops
working is the worst shape a regression can take here: the Lion presses the
button, the button does nothing, and no log line says why.

The same hazard covers the other string-typed resource lookups the three apps
make (`"layout"`, `"drawable"`, `"mipmap"`), so this checks all of them.

Direction matters: every name the code asks for must EXIST in the resources.
The reverse (an id in a layout nobody looks up) is not an error — plenty of
views are laid out purely for structure.
"""

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ANDROID = REPO_ROOT / "android"
APPS = ("controller", "companion", "slave")

# getResources().getIdentifier("name", "type", ...) with BOTH args literal.
# A non-literal first arg (the getId helper's own `name` parameter) is skipped
# by the quote in the pattern — it cannot be resolved statically.
IDENTIFIER_CALL = re.compile(r'getIdentifier\(\s*"([^"]+)"\s*,\s*"([^"]+)"')

# The controller's own helper, which always means type "id".
GET_ID_CALL = re.compile(r'\bgetId\(\s*"([^"]+)"\s*\)')

DEFINED_ID = re.compile(r'android:id="@\+id/([A-Za-z0-9_]+)"')
DEFINED_STRING = re.compile(r'<string\s+name="([A-Za-z0-9_]+)"')


def _resources(app: str) -> dict[str, set[str]]:
    """Every resource name an app declares, keyed by the type string that
    `getIdentifier` would be called with."""
    res = ANDROID / app / "res"
    out: dict[str, set[str]] = {
        "id": set(),
        "layout": set(),
        "menu": set(),
        "drawable": set(),
        "mipmap": set(),
        "string": set(),
    }
    if not res.is_dir():
        return out

    # ids are declared inline, wherever a view or menu item is defined
    for xml in list(res.glob("layout/*.xml")) + list(res.glob("menu/*.xml")):
        out["id"].update(DEFINED_ID.findall(xml.read_text(encoding="utf-8")))
        out[xml.parent.name].add(xml.stem)

    # density-bucketed types: any bucket declaring the name counts
    for kind in ("drawable", "mipmap"):
        for bucket in res.glob(f"{kind}*"):
            if bucket.is_dir():
                out[kind].update(f.stem.split(".")[0] for f in bucket.iterdir() if f.is_file())

    for values in res.glob("values/*.xml"):
        out["string"].update(DEFINED_STRING.findall(values.read_text(encoding="utf-8")))

    return out


def _lookups(app: str):
    """(name, type, "path:line") for every statically-resolvable lookup."""
    found = []
    for java in sorted((ANDROID / app / "src").rglob("*.java")):
        rel = java.relative_to(REPO_ROOT)
        for lineno, line in enumerate(java.read_text(encoding="utf-8").splitlines(), 1):
            for name, kind in IDENTIFIER_CALL.findall(line):
                found.append((name, kind, f"{rel}:{lineno}"))
            for name in GET_ID_CALL.findall(line):
                found.append((name, "id", f"{rel}:{lineno}"))
    return found


@pytest.mark.parametrize("app", APPS)
def test_every_string_resource_lookup_resolves(app):
    """The whole point: a lookup that misses is invisible at build time."""
    res = _resources(app)
    missing = []
    for name, kind, where in _lookups(app):
        if kind not in res:
            continue  # a type this check does not model (anim, raw, …)
        if name not in res[kind]:
            missing.append(f"  {where}\n      getIdentifier({name!r}, {kind!r}) — no such {kind}")
    assert not missing, (
        f"{app}: {len(missing)} string resource lookup(s) resolve to nothing.\n"
        "findViewById(0) returns null and the control silently stops working:\n" + "\n".join(missing)
    )


def test_the_controller_actually_looks_views_up_by_string():
    """Guard the guard. If MainActivity is ever migrated to R.id.* this test
    would pass vacuously, so assert the hazard it covers still exists — and
    delete this file, gladly, on the day it does not."""
    main = ANDROID / "controller" / "src" / "com" / "focusctl" / "MainActivity.java"
    src = main.read_text(encoding="utf-8")
    assert 'getIdentifier(name, "id", getPackageName())' in src
    assert len(GET_ID_CALL.findall(src)) > 50, "expected the string-lookup helper to be in heavy use"


def test_every_collapsible_section_has_its_three_parts():
    """`wireSection(name)` builds ids by concatenation — getId(name + "_head"),
    "_body", "_chevron" — so no static check can see them and a half-renamed
    section degrades into a header that does not open. Enforce the convention
    structurally instead: a `_head` obliges the other two."""
    layout = ANDROID / "controller" / "res" / "layout" / "activity_main.xml"
    ids = set(DEFINED_ID.findall(layout.read_text(encoding="utf-8")))
    broken = []
    for head in sorted(i for i in ids if i.endswith("_head")):
        stem = head[: -len("_head")]
        for part in ("_body", "_chevron"):
            if stem + part not in ids:
                broken.append(f"  @+id/{head} has no @+id/{stem}{part}")
    assert not broken, "collapsible sections missing parts:\n" + "\n".join(broken)


def test_every_button_on_the_main_screen_does_something():
    """A button the Lion can see and press that no code ever reaches is the
    worst outcome of moving controls around — it looks like the feature is
    broken rather than absent. Every id-bearing Button in the main layout must
    be named somewhere in the controller's Java."""
    layout = ANDROID / "controller" / "res" / "layout" / "activity_main.xml"
    text = layout.read_text(encoding="utf-8")
    buttons = set(re.findall(r'<(?:Button|ToggleButton)[^>]*?android:id="@\+id/([A-Za-z0-9_]+)"', text, re.S))
    java = "\n".join(
        f.read_text(encoding="utf-8") for f in (ANDROID / "controller" / "src").rglob("*.java") if f.name != "R.java"
    )
    orphans = sorted(b for b in buttons if f'"{b}"' not in java)
    assert not orphans, "buttons in activity_main.xml that no code references:\n  " + "\n  ".join(orphans)


@pytest.mark.parametrize("app", APPS)
def test_no_duplicate_ids_within_one_layout(app):
    """Two views sharing an id in one layout makes findViewById order-dependent
    — the kind of thing a copy-pasted row introduces and nothing complains
    about until the wrong widget updates."""
    dupes = []
    for xml in sorted((ANDROID / app / "res").glob("layout/*.xml")):
        ids = DEFINED_ID.findall(xml.read_text(encoding="utf-8"))
        seen = set()
        for i in ids:
            if i in seen:
                dupes.append(f"  {xml.relative_to(REPO_ROOT)}: @+id/{i}")
            seen.add(i)
    assert not dupes, f"{app}: duplicate ids within a single layout:\n" + "\n".join(dupes)

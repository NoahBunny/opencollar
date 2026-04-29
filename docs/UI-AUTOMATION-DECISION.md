# Android UI Automation — Decision

**Status:** Shelved. Manual-QA-only for the foreseeable future.
**Last reviewed:** 2026-04-28.

This document closes Stream C item 1 of the 2026-04-27 audit plan
(`docs/AUDIT-PLAN.md`). The audit acceptance criterion required a
binary outcome — "working harness OR shelved with rationale." This
captures the rationale.

## What we tried

- **2026-04-23 — uiautomator2 spike against Waydroid (single-device
  loopback pair).** Lion's Share + Bunny Tasker + Collar co-located,
  "LAN" = 127.0.0.1.
- The harness chain was: Waydroid → adbd → uiautomator2's
  atx-agent/JAR layer → assertions.
- Result: `uiautomator2.connect()` hangs at `_setup_jar` →
  `toybox md5sum` and **wedges Waydroid's adbd for the rest of the
  session**. Recovery requires `waydroid session stop` plus
  `sudo systemctl restart waydroid-container`.
- Each link failed at least once during the spike. Three brittle
  layers stacked is not a foundation we want to gate CI on.

## What's left in the tree

- `tests/ui/conftest.py` — APK install + consent bypass + adb wrappers.
  Works; the wedge happens later, in the UI driver layer.
- `tests/ui/test_pair_direct.py` — direct-LAN pair happy-path
  documented intent. Skipped by default (`@pytest.mark.ui`,
  `UI_TESTS=1` opt-in).
- pytest marker `ui` registered in `pyproject.toml`.

These remain in-tree so a future contributor isn't starting from zero.

## Paths considered (and not taken)

### A. Replace uiautomator2 with Appium against dual Waydroid devices

Appium's UIAutomator2 driver is a separate animal from the Python
`uiautomator2` package and likely doesn't trigger the same wedge.
Tradeoffs:

- Pros: Industry-standard, dual-device gives real network-pairing
  semantics.
- Cons: ~4–6h to stand up, two Waydroid containers running in parallel
  is fragile on a dev box (memory + GPU contention), Appium server is
  another moving piece in CI. Maintenance burden compounds with every
  Android API bump.
- Verdict: Worth doing **if** pairing-flow regressions become a
  pattern, OR **if** a contributor commits to operating the harness.
  Not worth the upfront cost while neither is true.

### B. Espresso (in-process Android instrumentation)

Espresso requires Gradle. This repo deliberately uses the
aapt2/javac/d8/apksigner pipeline (no Gradle) — adopting Gradle for
the test path alone would 10x the build complexity. **Ruled out.**

### C. Stay manual

Walk `docs/QA-pairing.md` by hand each time pairing-flow code changes.
Friction is real but bounded — pairing code touches the tree maybe
twice a month, not twice a week.

## Decision

**Path C — stay manual, with structured handoff.**

- The four-layer programmatic QA harness (`pytest` + `qa_runner.py` +
  `qa_wizard_browser.py` + `qa_index_browser.py`) covers everything
  reachable without a real device UI driver. That's the bulk of what's
  worth automating today.
- Pairing flows (the only thing that genuinely needs UI automation)
  walk through `docs/QA-pairing.md` § 1–§ 5 before any pairing-related
  PR ships.
- The shelved scaffold at `tests/ui/` documents the intent; revisit
  when the cost equation shifts.

## Conditions to revisit

Open this decision back up when ANY of these holds:

1. **Pairing-flow regressions become a pattern** — say, three or more
   in six months. The signal: bugs that the four-layer harness can't
   catch but a UI driver would have.
2. **A contributor commits to operating the harness** — including
   responding to Appium/Waydroid breakage on every Android image bump.
   Untended Appium harnesses rot fast.
3. **The aapt2 pipeline gets retired** in favor of Gradle (out of
   scope of any current roadmap item) — at that point Espresso becomes
   plausible and the calculus changes.

## Cross-references

- `tests/ui/conftest.py` — scaffold (kept for future contributors).
- `docs/QA-pairing.md` — the manual fallback.
- `docs/AUDIT-PLAN.md` § Stream C item 1 — the audit obligation this
  closes.
- `docs/PUBLISHABLE-ROADMAP.md` § "UI automation for pairing / release
  flows" — historical context on the spike.

#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
"""
qa_matrix.py — Walk docs/QA-CHECKLIST.md, classify each section as
programmable vs manual, run the available programmable harnesses, and
emit a single pass/fail/manual-only table to stdout (and JSON to
staging/qa-matrix-result.json for downstream parsing).

This is the regression-matrix automation half of Stream C item 5 from
the 2026-04-27 audit. It does NOT replace the human walk-through of
section 14 (real radios, real BT, real GPS) or the device-specific UI
flows in sections 1, 11, 12 — those stay in docs/MANUAL-QA.md and
docs/QA-pairing.md.

Usage:
    python3 staging/qa_matrix.py
    make qa-matrix          # equivalent

Exits 0 if every programmable row passes (or is honestly skipped with
reason); 1 if any programmable row fails. Manual-only rows never block
the exit code — they're informational.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
CHECKLIST = REPO_ROOT / "docs" / "QA-CHECKLIST.md"
RESULT_JSON = REPO_ROOT / "staging" / "qa-matrix-result.json"

# Pick the project venv's python if present, else system python3.
VENV_PY = REPO_ROOT / ".venv" / "bin" / "python3"
PY = str(VENV_PY) if VENV_PY.exists() else sys.executable


@dataclass
class Row:
    section: int
    title: str
    kind: str  # "programmable" or "manual"
    runner: str | None = None  # one of the keys in RUNNERS, or None if manual
    pytest_args: list[str] = field(default_factory=list)
    notes: str = ""


@dataclass
class Result:
    row: Row
    status: str  # "pass" | "fail" | "skip" | "manual"
    duration_s: float
    detail: str = ""


# Map QA-CHECKLIST.md sections to programmable harnesses (or mark manual-only).
# When `runner` is None the row prints as MANUAL with a docs pointer; otherwise
# the runner is invoked and the section pass/fails on its exit code.
ROWS: list[Row] = [
    Row(0, "Pre-flight", "programmable", "preflight"),
    Row(1, "First-run consent + pairing", "manual", notes="Waydroid UI flow — covered by docs/QA-pairing.md."),
    Row(
        2,
        "Lock / Unlock — core",
        "programmable",
        "pytest",
        pytest_args=["tests/test_e2e_admin_routes.py", "tests/test_paywall_hardening.py", "-q"],
    ),
    Row(
        3,
        "Paywall + compound interest",
        "programmable",
        "pytest",
        pytest_args=["tests/test_payment.py", "tests/test_paywall_hardening.py", "-q"],
    ),
    Row(4, "Payment detection (per-region)", "programmable", "pytest", pytest_args=["tests/test_payment.py", "-q"]),
    Row(5, "Lock modes (all 9)", "programmable", "pytest", pytest_args=["tests/test_e2e_admin_routes.py", "-q"]),
    Row(
        6,
        "Subscriptions (gold / silver / bronze)",
        "programmable",
        "pytest",
        pytest_args=["tests/test_initial_mesh_config.py", "tests/test_e2e_unsubscribe_deadline.py", "-q"],
    ),
    Row(
        7,
        "Geofence + curfew + bedtime",
        "programmable",
        "pytest",
        pytest_args=["tests/", "-q", "-k", "geofence or curfew or bedtime or Bedtime"],
        notes="Real GPS row 7.6 still needs hardware — see docs/MANUAL-QA.md.",
    ),
    Row(
        8,
        "Vault / mesh crypto",
        "programmable",
        "pytest",
        pytest_args=["tests/test_vault.py", "tests/test_account_vault_stores.py", "-q"],
    ),
    Row(
        9,
        "Mesh gossip + convergence",
        "programmable",
        "pytest",
        pytest_args=["tests/test_mesh.py", "tests/test_sync.py", "-q"],
    ),
    Row(10, "ntfy push", "programmable", "pytest", pytest_args=["tests/test_ntfy.py", "-q"]),
    Row(
        11,
        "Desktop collar (Linux + Windows)",
        "manual",
        notes="OS-level integration — Linux GTK4 + Windows pystray. See docs/MANUAL-QA.md.",
    ),
    Row(
        12,
        "Escape + factory reset + consent revocation",
        "manual",
        notes="Device-admin tamper paths — needs real Android. See docs/MANUAL-QA.md.",
    ),
    Row(
        13,
        "Admin API + web UI",
        "programmable",
        "pytest",
        pytest_args=[
            "tests/test_e2e_admin_routes.py",
            "tests/test_e2e_web_session.py",
            "tests/test_e2e_disposal_token.py",
            "tests/test_e2e_public_routes.py",
            "-q",
        ],
    ),
    Row(
        14,
        "Stuff Waydroid can't cover — on-device manual",
        "manual",
        notes="Real radios (SMS, Lovense BT, GPS, camera). See docs/MANUAL-QA.md §14.",
    ),
]


def _run_preflight() -> tuple[str, str]:
    """Section 0 — env sanity. Checks ruff is clean (0.7) and the test
    suite collects without errors (0.6)."""
    # 0.6 — pytest collect-only as a fast smoke test of the test tree.
    p = subprocess.run(
        [PY, "-m", "pytest", "--collect-only", "-q"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
    )
    if p.returncode != 0:
        return "fail", f"pytest --collect-only failed:\n{p.stdout[-500:]}\n{p.stderr[-500:]}"

    # 0.7 — ruff check.
    try:
        p = subprocess.run(
            ["ruff", "check", "."],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=60,
        )
        if p.returncode != 0:
            return "fail", f"ruff check failed:\n{p.stdout[-500:]}"
    except FileNotFoundError:
        return "skip", "ruff not installed — install with `pip install ruff`"

    return "pass", "pytest --collect-only + ruff check both clean"


def _run_pytest(args: list[str]) -> tuple[str, str]:
    p = subprocess.run(
        [PY, "-m", "pytest", *args],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=600,
    )
    last = p.stdout.strip().splitlines()[-1] if p.stdout.strip() else "(no output)"
    if p.returncode == 0:
        return "pass", last
    if p.returncode == 5:
        # No tests collected — usually a -k filter that matched nothing.
        return "skip", f"no tests collected: {last}"
    return "fail", f"pytest exited {p.returncode}: {last}\n{p.stderr[-300:]}"


def run_row(row: Row) -> Result:
    t0 = time.perf_counter()
    if row.kind == "manual":
        return Result(row, "manual", 0.0, row.notes)
    if row.runner == "preflight":
        status, detail = _run_preflight()
    elif row.runner == "pytest":
        status, detail = _run_pytest(row.pytest_args)
    else:
        status, detail = "skip", f"unknown runner: {row.runner}"
    return Result(row, status, time.perf_counter() - t0, detail)


def render(results: list[Result]) -> str:
    lines = []
    lines.append(f"{'#':>3}  {'Status':<8} {'Time':>7}  Section")
    lines.append("─" * 78)
    for r in results:
        marker = {
            "pass": "PASS",
            "fail": "FAIL",
            "skip": "SKIP",
            "manual": "MANUAL",
        }[r.status]
        dur = f"{r.duration_s:>5.1f}s" if r.status != "manual" else "    —"
        lines.append(f"{r.row.section:>3}  {marker:<8} {dur:>7}  {r.row.title}")
        if r.detail and r.status in ("fail", "skip"):
            lines.append(f"        └─ {r.detail.splitlines()[0][:200]}")
    lines.append("─" * 78)
    counts = {k: sum(1 for r in results if r.status == k) for k in ("pass", "fail", "skip", "manual")}
    lines.append(
        f"  Summary: {counts['pass']} pass · {counts['fail']} fail · {counts['skip']} skip · {counts['manual']} manual"
    )
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="QA-CHECKLIST regression matrix walker.")
    ap.add_argument("--json", action="store_true", help="Emit JSON only (no human table).")
    ap.add_argument("--section", type=int, action="append", help="Run only the given section number(s).")
    args = ap.parse_args()

    rows = ROWS
    if args.section:
        rows = [r for r in ROWS if r.section in args.section]
        if not rows:
            print(f"No matching sections: {args.section}", file=sys.stderr)
            return 2

    results = [run_row(r) for r in rows]

    payload = {
        "generated_at": int(time.time()),
        "rows": [
            {
                "section": r.row.section,
                "title": r.row.title,
                "kind": r.row.kind,
                "status": r.status,
                "duration_s": round(r.duration_s, 2),
                "detail": r.detail,
                "runner": r.row.runner,
                "pytest_args": " ".join(shlex.quote(a) for a in r.row.pytest_args),
                "notes": r.row.notes,
            }
            for r in results
        ],
    }

    if not args.json:
        print(render(results))
        print()
    print(json.dumps(payload, indent=2) if args.json else f"JSON written to: {RESULT_JSON.relative_to(REPO_ROOT)}")
    os.makedirs(RESULT_JSON.parent, exist_ok=True)
    RESULT_JSON.write_text(json.dumps(payload, indent=2))

    return 1 if any(r.status == "fail" for r in results) else 0


if __name__ == "__main__":
    sys.exit(main())

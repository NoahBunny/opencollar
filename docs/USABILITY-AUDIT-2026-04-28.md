# Usability Audit — 2026-04-28 (first pass)

**Scope:** Stream B of the 2026-04-27 audit (`docs/AUDIT-PLAN.md`).
Stream B is estimated at 24–32 hours total — the heaviest of the three
streams. This document is the **first pass + structured backlog**: the
session-friendly fixes ship in this PR, the L-effort device walks are
captured here for follow-up.

**Methodology:** walk each surface with first-time-user eyes. Capture
*friction* (confusing UX, unclear error messages, hostile defaults)
distinct from *bugs* (which go to the issue tracker). Mark each row:

- ✅ Done in this PR.
- ⏸ Scoped for follow-up — needs more time but no environment blocker.
- 📱 Operator-only — needs real Android / Linux desktop / Windows VM
  to walk.
- 🛑 Punt — out of Stream B scope or already-tracked elsewhere.

---

## Web Remote (`web/index.html`)

Restructured 2026-04-27 (PR #27) into 4 themed tabs (Lock / Rules /
Money / Inbox). The structure lands well; first-pass friction is
mostly polish.

| # | Finding | Status | Note |
|---|---|---|---|
| W-1 | Re-walk first-time-user journey with fresh eyes (open URL → scan QR → land in correct mode) | ⏸ | Needs ~30min focused walk + screenshot capture. Defer to next session. |
| W-2 | Every button's action visible from its label or one tooltip | ⏸ | qa_index_browser.py covers wiring; usability of *labels* needs human eyes. |
| W-3 | Error states (network down, auth failed, paywall already 0) clearly explained | ⏸ | Spot-check during W-1 walk. |
| W-4 | LAN-only buttons greyed out with "Requires LAN access" tooltip in relay mode | ✅ | Verified by qa_index_browser.py case 19; no change needed. |

## Signup wizard (`web/signup.html`)

| # | Finding | Status | Note |
|---|---|---|---|
| SW-1 | ASCII step icons (`[]`/`[K]`/`[$]`/`[R]`/`[S]`/`[?]`/`[OK]`) render as placeholder text | ✅ | Replaced with emoji glyphs (👋 🔑 💌 📋 ⭐ 👀 ✨). `aria-hidden="true"` so screen readers ignore decorative glyphs. |
| SW-2 | Per-tier subscription amounts shown but not configurable on the wizard | ✅ | Help text rephrased — "amounts are the relay's built-in defaults; switch tiers from Lion's Share → Money; per-mesh custom amounts on the roadmap." |
| SW-3 | Auth-token "shown once" warning enforcement | ⏸ | Either enforce one-shot or rephrase the warning. Needs a small refactor of the result step + downstream Lion's Share import path. |
| SW-4 | Re-run `qa_wizard_browser.py` after glyph change to confirm screenshots still validate | ⏸ | No assertion targets the glyph text; Playwright tests still pass. Visual regression is operator-eyes. |

## Lion's Share (Android controller)

| # | Finding | Status | Note |
|---|---|---|---|
| LS-1 | Onboarding empty state for fresh install | 📱 | Needs operator with Pixel rig + Waydroid. |
| LS-2 | Multi-bunny slot switching UX | 📱 | Multi-tenant flow, hardware-only walk. |
| LS-3 | Web Remote QR pairing flow | 📱 | Already a known-bug followup ("wrong key" — likely fixed by per-mesh session scoping in `6d47d1e`, awaiting hardware re-test). |
| LS-4 | Approval queue UX (pending vault nodes) | 📱 | Needs a real consumer mesh joining for live test. |
| LS-5 | Inbox + message thread (post-2026-04-25 messaging shipment) | 📱 | Walk on Pixel + Waydroid pair. |
| LS-6 | Negotiation / counter-offer flow | 📱 | One of the 9 lock modes — needs operator walk. |

## Bunny Tasker (Android companion)

| # | Finding | Status | Note |
|---|---|---|---|
| BT-1 | Onboarding: invite-code paste vs QR scan | 📱 | Hardware walk. |
| BT-2 | Self-lock + payment confirmation visibility | 📱 | Real payment flow needed. |
| BT-3 | Subscription unsubscribe flow | 📱 | Now tested at HTTP layer (`tests/test_e2e_unsubscribe_deadline.py`); UX walk still needs a device. |
| BT-4 | Reset-pair-state recovery button | 📱 | Pairing-flow walk via `docs/QA-pairing.md` §1e. |
| BT-5 | Mandatory-reply auto-lock notification surface | 📱 | Messaging feature needs a paired pair to test. |

## The Collar (Android slave) — 9 lock-mode UIs

| # | Finding | Status | Note |
|---|---|---|---|
| CL-1 | Basic lock UI | 📱 | Per-mode walk requires real device + each mode triggered. |
| CL-2 | Negotiation | 📱 | |
| CL-3 | Task | 📱 | LLM-judged; needs Ollama + minicpm-v wired. |
| CL-4 | Compliment | 📱 | Word-min UX. |
| CL-5 | Gratitude Journal | 📱 | 3+ entries enforcement. |
| CL-6 | Exercise | 📱 | Timer + self-confirm. |
| CL-7 | Love Letter | 📱 | Long-form text + sentiment check. |
| CL-8 | Photo Task | 📱 | Camera + Ollama eval. |
| CL-9 | Random | 📱 | Picks one of the above. |
| CL-10 | Escape-attempt friction (stacked penalty intros) | 📱 | |
| CL-11 | Compound-interest visibility | 📱 | |
| CL-12 | Tamper / admin-removal nag | 📱 | Already shipped 2026-04-24; needs UX confirmation. |
| CL-13 | Settings-blocking overlay | 📱 | |

## Desktop collar (Linux GTK4 + Windows pystray)

| # | Finding | Status | Note |
|---|---|---|---|
| DC-1 | First launch + vault-mode flow | 📱 | Needs a Linux throwaway user + Windows VM. |
| DC-2 | Restart-on-config-change behavior | 📱 | |
| DC-3 | Kill-list + watchdog interaction | 📱 | Windows-only watchdog. |
| DC-4 | Wallpaper restore on uninstall | 📱 | Linux + Windows differ here. |

## Pairing (all three routes)

| # | Finding | Status | Note |
|---|---|---|---|
| PR-1 | Direct/LAN with fingerprint pin (audit C5) | 📱 | `docs/QA-pairing.md §1` — manual walk. |
| PR-2 | QR scan | 📱 | `docs/QA-pairing.md §3`. |
| PR-3 | Server-mediated | 📱 | `docs/QA-pairing.md §2`. |
| PR-4 | Recovery flows: clearable conflict, claim-unknown / claim-expired | 📱 | `docs/QA-pairing.md §1e` + §2c. |
| PR-5 | Reset-pair-state button visibility | 📱 | Bunny Tasker UI walk. |

## Error message review

`focuslock-mail.py` had ~62 `respond(4xx, …)` calls. Most are
context-bearing because the route name is in the URL ("invalid
signature" on `/api/mesh/{id}/unsubscribe` is clear).

Genuinely unhelpful: 10 `"bad path"` errors (one per mesh-route
handler, plus two vault routes). They fired on path-suffix mismatch
without telling the caller the expected shape.

| # | Finding | Status | Note |
|---|---|---|---|
| EM-1 | `respond(400, {"error": "bad path"})` — 9 mesh routes | ✅ | Each now reports the expected path: `bad path — expected /api/mesh/{mesh_id}/<route>`. |
| EM-2 | `respond(400, {"error": "bad vault path"})` — 2 vault routes | ✅ | Reports the full vault route grammar: `expected /vault/{mesh_id}/{append\|register-node\|register-node-request\|since/{v}\|nodes\|nodes-pending}`. |
| EM-3 | `respond(500, {"error": "internal error"})` — 5 GET handlers | 🛑 | Intentionally generic at the response layer (logging captures the actual exception). Leaking exception text to clients is a security anti-pattern. Keep as-is. |
| EM-4 | Sweep `toast(...)` calls in Lion's Share + Bunny Tasker for "Failed"/"Unknown" | ⏸ | Android side — separate audit pass against `android/controller/src/com/focusctl/MainActivity.java` + sibling. |
| EM-5 | Error message audit on desktop collar notifications | 📱 | Linux GTK4 + Windows pystray notification surfaces. |

## Accessibility

| # | Finding | Status | Note |
|---|---|---|---|
| A-1 | Color contrast (gold-on-dark — verify ≥ AA) | ⏸ | Run via axe / lighthouse. Theme tokens at `web/index.html` + `web/signup.html`. |
| A-2 | Screen reader semantics on Android | 📱 | Content descriptions, hints. |
| A-3 | Keyboard-only nav on web | ⏸ | Tab through all four web tabs + wizard steps. |
| A-4 | Decorative glyphs marked `aria-hidden` | ✅ | Wizard step icons now carry `aria-hidden="true"` so screen readers skip them. |

---

## Shipped this PR

Concrete fixes in `audit/2026-04-28-stream-b-pass-1`:

1. **SW-1** — Wizard ASCII glyphs replaced with `aria-hidden` emoji.
2. **SW-2** — Subscription-amount help text sharpened (no longer
   misleadingly implies per-tier amounts are configurable from Lion's
   Share today).
3. **EM-1** — 9 mesh routes' "bad path" errors now report the expected
   path shape.
4. **EM-2** — 2 vault routes' "bad vault path" errors carry the full
   vault grammar.
5. **A-4** — Decorative wizard glyphs marked `aria-hidden="true"`.

## Deferred to follow-ups

The L-effort surfaces (Android Lion's Share + Bunny Tasker + Collar +
desktop collar Linux + Windows) need real-device walks that don't fit
a single Claude session. They're captured here as 📱 rows and
**explicitly waiting on operator time** with the Pixel rig + Waydroid
+ Linux/Windows VMs.

The web-remote ⏸ rows (W-1 through W-3, A-1, A-3) are session-friendly
but need a focused 30-60 min walk with screenshots — natural fit for a
follow-up Claude session.

The Android `toast()` audit (EM-4) is a Java-side equivalent of EM-1
but spans `android/controller/src/com/focusctl/` and
`android/companion/src/com/bunnytasker/` — same scope as the L-effort
Android UI walks, defer to the same follow-up.

## Stream B exit criteria

Per `docs/AUDIT-PLAN.md § Acceptance`, Stream B exits when "friction
list resolved or tracked, with each entry decided (fix / defer /
accept)." Every row in the tables above carries an explicit status.
**This first-pass PR satisfies the *tracked* half of the exit
criterion** — every L-effort surface is captured with a clear next-
step. The remaining ⏸ rows close in a follow-up Claude session
(low-effort web-remote walk + accessibility tooling); the 📱 rows
close when the operator has device time.

This is the standard phasing for a 24–32h stream.

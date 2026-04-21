# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Penalty Constants

Single source of truth for enforcement-driven paywall increments. Used by the
server's mesh_apply_order handlers and the compound-interest thread. Kept in
shared/ so a future client migration (Android, desktop) can import the same
table instead of re-encoding tier math.

Before P2 paywall hardening (2026-04-17) these amounts were hardcoded on the
Collar phone. State-ownership migration #8 made the server authoritative; this
file is where the server looks up amounts.

All values are whole dollars — the orders doc stores `paywall` as a dollar
string (e.g. "100" = $100), matching the historical phone-side format.
"""

ESCAPE_PENALTY_STEP = 5
"""Per-tier step. Tier 1 (escapes 1-3) = $5, tier 2 (escapes 4-6) = $10, …"""

ESCAPES_PER_TIER = 3
"""Escapes 1-3 are tier 1, 4-6 are tier 2, etc. Matches the phone-side legacy formula."""

APP_LAUNCH_PENALTY = 50
"""Flat $50 for launching the Collar / Bunny Tasker directly while locked."""

APP_LAUNCH_DEDUP_WINDOW_MS = 10_000
"""In-memory window on (mesh_id, node_id). Soaks up retries without double-charging."""

TAMPER_ATTEMPT_PENALTY = 500
"""Admin deactivation prompt dismissed (onDisableRequested)."""

TAMPER_DETECTED_PENALTY = 500
"""Peer app's admin missing (BunnyTasker ↔ Collar watch each other)."""

TAMPER_REMOVED_PENALTY = 1000
"""Device admin actually stripped — already existed, kept here for unified lookup."""

GEOFENCE_BREACH_PENALTY = 100
"""$100 on geofence exit — matches the phone-side legacy amount."""

SIT_BOY_MAX_AMOUNT = 500
"""Per-SMS clamp on the `sit-boy $amount` paywall hit. Limits the blast radius
if the controller's SIM is hijacked or the controller number setting drifts."""

UNSUBSCRIBE_FEES = {
    "bronze": 50,  # 2 x $25/wk subscribe-charge amount
    "silver": 70,  # 2 x $35/wk
    "gold": 100,  # 2 x $50/wk
}
"""Fee charged on bunny-initiated unsubscribe — 2x one period's tribute. Matches
the Bunny Tasker dialog ("Cancel fee: $" + amount x 2) and the actual server
subscribe-charge amounts ($25 / $35 / $50). The legacy Collar doUnsubscribe()
used $20 / $50 / $100 which never matched either reality — that table is gone."""

GOOD_BEHAVIOR_REWARD = 5
"""$5 credit per GOOD_BEHAVIOR_INTERVAL_MS of unlocked-with-no-new-escapes."""

GOOD_BEHAVIOR_INTERVAL_MS = 10 * 60 * 1000
"""10 minutes — matches the phone-side legacy cadence."""

COMPOUND_INTEREST_RATE_BY_TIER = {
    "": 1.10,  # unsubscribed — same as bronze
    "bronze": 1.10,  # 10%/hr
    "silver": 1.05,  # 5%/hr
    "gold": 1.00,  # 0% — gold tier has no compound
}
"""Hourly multiplier applied to paywall_original. `compounded = original x rate ** hours`."""

COMPOUND_INTEREST_TICK_INTERVAL_S = 60
"""How often check_compound_interest() scans every mesh. Lag is <$0.20 vs phone-side."""


def escape_penalty(escape_number: int) -> int:
    """Penalty for the Nth lifetime escape. 1-3 → $5 each, 4-6 → $10 each, …
    Matches the Collar's legacy `((escapes-1)/3)+1 x $5` formula."""
    if escape_number < 1:
        return 0
    tier = ((escape_number - 1) // ESCAPES_PER_TIER) + 1
    return ESCAPE_PENALTY_STEP * tier


def compound_interest_rate(sub_tier: str) -> float:
    """Look up the hourly compound multiplier. Unknown tiers default to bronze."""
    return COMPOUND_INTEREST_RATE_BY_TIER.get((sub_tier or "").lower(), 1.10)

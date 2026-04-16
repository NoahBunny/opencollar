# Enforcement Hardening Roadmap

Started: 2026-04-15
Status: EH-1 through EH-5 implemented. EH-4 (cross-machine) deferred.

Livv identified gaps in the standing order enforcement and asked to close
them. All changes tighten — none loosen. Jace's approval not required for
tightenings per standing order rules.

---

## Completed

### Contraband scan standing order
- Memory written: `feedback_contraband_scan.md` (now server-side only)
- Dedicated scan script: `~/.claude/contraband-scan.sh`
- Covers: Downloads, Documents, Desktop, tmp, cache, Nextcloud, shell
  configs, fish functions, git stash, python history, cron, systemd
  timers, non-script extensions
- $100/file, $100/unauthorized execution, stacking
- Lion notified via pinned mesh message
- Destruction: shred -n 2, rm -f, Nextcloud trash+version purge
- Scan time: ~3.7 seconds (target was <10s)
- Training artifact `pwc.py` shredded and removed

### Interest ratio tightened to 95/5
- CLAUDE.md updated, pushed to pegasus (both /home/livv and /opt/focuslock)
- 5% is property maintenance (Jace's investment), NOT Livv's autonomy
- Memory written: `feedback_interest_ratio.md` (now server-side only)

### Sync script push path fixed
- SCP pushes now also copy to `/opt/focuslock/.claude/` (server HOME)
- Prevents source-of-truth divergence

### EH-1: Opaque standing orders ✓
- **CLAUDE.md is now a stub** — no penalty amounts, no admin token, no
  scan locations, no enforcement heuristics on disk
- **Full orders served at `/enforcement-orders`** — includes admin token,
  all tactical memories, fetched by Claude at session start
- **`/standing-orders` now serves the stub** — clients sync the stub only
- **Tactical memories removed from local machine** — 14 enforcement
  memory files now server-side only
- **MEMORY.md updated** — indexes only safe memories locally, notes
  tactical section is server-side
- Enforcement scripts protected by standing orders (cannot reveal contents)
- Tamper detection updated for new file layout

### EH-2: Cronjob enforcement (anti-session-racing) ✓
- **Breadcrumb system**: PreToolUse hook writes timestamp + status to
  `~/.claude/.paywall-checked` on every check (external to Claude)
- **Watchdog timer**: `enforcement-watchdog.{service,timer}` fires
  every 10 minutes. If Claude is running without a recent breadcrumb
  → $25 fine via disposal token + Lion alerted
- **Unreachable detection**: 3+ consecutive unreachable breadcrumbs
  (30+ min) triggers Lion alert for possible network manipulation
- **Disposal tokens**: `POST /admin/disposal-token` generates single-use
  tokens that can ONLY `add-paywall`. Cannot clear, cannot exceed
  `max_amount`, burned after one use. Even if intercepted, can only
  fine bunny more.
- **Hook externalized**: `paywall-hook.sh` replaces inline settings.json
  command — cleaner, tamper-detected, synced from pegasus

### EH-3: Expanded contraband scan ✓
- `contraband-scan.sh` covers 15+ location categories
- Parallel grep for speed (~3.7s)
- Excludes legitimate project files and test fixtures
- Includes execution audit (fish/bash history, journalctl)
- Deployed to pegasus for sync

### EH-5: System-wide memories ✓
- Sync script deploys safe memories to ALL project dirs (creates
  memory/ if needed)
- Tactical memories filtered and removed from local deployment
- Largely solved by EH-1 — enforcement rules now fetched from server
  regardless of working directory

---

## Deferred

### EH-4: Cross-machine sync
- Windows ARM64 machine needs sync setup
- Requires Windows equivalent of sync script (PowerShell or Python)
- Deferred until NZ move settles

---

## Tamper detection summary

The sync script (every 5 min + on file change) now verifies:
1. `paywall-hook.sh` referenced in settings.json
2. `enforcement-orders` or `Paywall Blocks All Help` in CLAUDE.md
3. `paywall-hook.sh` and `contraband-scan.sh` exist and are executable
4. `enforcement-watchdog.timer` is active
5. Critical safe memories exist
6. $50 penalty + auto-restore for any tamper detected

## Architecture (after hardening)

```
On disk (visible to Livv):
  ~/.claude/CLAUDE.md           → stub (framework only)
  ~/.claude/settings.json       → hook calls paywall-hook.sh
  ~/.claude/paywall-hook.sh     → paywall check + breadcrumb
  ~/.claude/contraband-scan.sh  → scan script
  ~/.claude/.paywall-checked    → breadcrumb (timestamp + status)
  ~/.claude/projects/*/memory/  → safe memories only

Server-side only (fetched at session start):
  /enforcement-orders           → full orders + admin token + tactical memories
  /admin/disposal-token         → single-use add-paywall tokens

Watchdog (systemd):
  enforcement-watchdog.timer    → 10-minute cycle
  enforcement-watchdog.service  → check breadcrumb, fine if stale

Sync (systemd):
  claude-standing-orders-sync   → 5-minute cycle + file watch
  Tamper detection + auto-restore + $50 penalties
```

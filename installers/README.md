# Installers

Scripts that put the desktop collar on a target machine and wire it into a
mesh, plus operator-side scripts that bring up a homelab and redeploy code
to existing devices.

## Every script, grouped by role

There are a lot of files here, but a given person only touches one group.
**If you're a bunny setting up your own machine, you need the first group and
nothing else.**

### 🐇 Bunny / consumer — set up & remove your own collar
| Script | What it does |
|---|---|
| [`install-mesh.sh`](#consumer-quick-start) | **Linux: the one you run.** Pre-configures `config.json` for your mesh, then installs the collar. |
| [`install-mesh.ps1`](#consumer-quick-start) | Windows equivalent of the above. |
| [`install-desktop-collar.sh`](#linux-platform-installer) | Linux platform installer that `install-mesh.sh` hands off to (deps, systemd units, daemon + tray). Run directly only if you want to walk the prompts by hand. |
| [`uninstall-desktop-collar.sh`](#uninstall) / [`.ps1`](#uninstall) | Tear the collar back off (Linux / Windows). |

### 👑 Operator / Lion — run the server side
| Script | What it does |
|---|---|
| [`homelab-setup.sh`](#operator-homelab) | Deploy the mail relay + ADB bridge on a homelab box you already own. |
| [`homelab-install.sh`](#operator-homelab) | Same, but a self-contained installer for a fresh Debian/Ubuntu **VPS**. |
| [`install-standing-orders.sh`](#operator-homelab) | Sync the Claude Code standing orders (`~/.claude/CLAUDE.md`) from the homelab. |

### 👑 Operator / Lion — push code to devices you already collared
| Script | What it does |
|---|---|
| [`re-enslave-all.sh`](#operator-re-enslave) | Orchestrator: server → desktops → phones. |
| [`re-enslave-{server,desktops,phones}.sh`](#operator-re-enslave) | Push one tier only. |
| [`re-enslave-lib.sh`](#operator-re-enslave) | Shared helpers + the version constants; **sourced by the others, not run directly.** |
| [`re-enslave-watcher.{py,service,timer}`](#operator-re-enslave) | systemd timer that auto-redeploys on a git push. |
| [`re-enslave.config.example`](#operator-re-enslave) | Template → `~/.config/focuslock/re-enslave.config`. |

### 🔧 Presets & local convenience (safe to ignore)
| Script | What it does |
|---|---|
| [`install-mesh-jace.sh`](#presets--local-convenience) | A **personal preset** that hard-codes one specific mesh so a sideload needs no flags. An example to copy, not something you run. |
| [`retrofit-local.sh`](#presets--local-convenience) | Self-update *this* machine's collar from the local source tree (operator/dev shortcut; a bunny normally re-runs the installer instead). |

## Consumer quick start

`install-mesh.sh` (Linux) and `install-mesh.ps1` (Windows) are the two scripts
a consumer ever needs. Both pre-configure `config.json`, preserve any existing
vault keypair so prior Lion approval sticks, then hand off to the
platform-specific collar installer.

### Required parameters

Both scripts require `mesh_id` and `mesh_url` — there is no default mesh.

| Parameter | Linux flag | Windows param | Env var | Notes |
|---|---|---|---|---|
| Mesh ID | `--mesh-id <id>` | `-MeshId <id>` | `FOCUSLOCK_MESH_ID` | base64url, issued by your relay when you create or join the mesh. |
| Mesh URL | `--mesh-url <url>` | `-MeshUrl <url>` | `FOCUSLOCK_MESH_URL` | Full https URL of your relay (e.g. `https://your.relay.example`). |

### Optional flags

| Linux | Windows | Effect |
|---|---|---|
| `--no-ntfy` | `-NoNtfy` | Skip ntfy push subscription. Default subscribes to `ntfy.sh`. |
| `--reset-keys` | `-ResetKeys` | Wipe the existing vault keypair so the collar generates fresh keys + posts a new `register-node-request`. Lion will need to approve again. |
| n/a | `-ExePath <path>` | Override the FocusLock.exe path. Defaults to one next to this script, then `..\dist\FocusLock.exe`, then `..\build\FocusLock.exe`. |

### Examples

```bash
# Linux (Bash)
./install-mesh.sh \
    --mesh-id <your-mesh-id> \
    --mesh-url https://your.relay.example
```

```bash
# Linux (env-var form)
FOCUSLOCK_MESH_ID=<your-mesh-id> \
FOCUSLOCK_MESH_URL=https://your.relay.example \
    ./install-mesh.sh
```

```powershell
# Windows (PowerShell)
.\install-mesh.ps1 `
    -MeshId <your-mesh-id> `
    -MeshUrl https://your.relay.example
```

```powershell
# Windows (env-var form)
$env:FOCUSLOCK_MESH_ID  = "<your-mesh-id>"
$env:FOCUSLOCK_MESH_URL = "https://your.relay.example"
.\install-mesh.ps1
```

### Idempotency

Both scripts are safe to re-run:

- `config.json` is rewritten authoritatively each run (this is the whole
  point — switching the device's mesh requires writing a new config).
- Vault keypair is preserved by default. Use `--reset-keys` / `-ResetKeys`
  only if you explicitly want a fresh `register-node-request` cycle.
- The platform installer runs whether or not the collar was installed
  before; it updates files in place.

## Linux platform installer

`install-desktop-collar.sh` is what `install-mesh.sh` hands off to. You can
also run it directly if you want to walk the prompts manually instead of
pre-configuring with `install-mesh.sh`. Drops the systemd user units, the
Python dependencies (PyGObject, GTK, AppIndicator), the sudoers rule for
deployment, and starts the daemon + tray.

## Uninstall

| Platform | Script |
|---|---|
| Linux | `uninstall-desktop-collar.sh` — removes `/opt/focuslock/`, the systemd user units, the autostart entries, the sudoers rule, and `~/.config/focuslock/` (with confirmation). |
| Windows | `uninstall-desktop-collar.ps1` — removes the install dir, scheduled tasks, registry entries, and (with confirmation) the user config. |

## Operator: homelab

Server-side bring-up scripts for the operator's machine. **Not for consumer
Bunny installs.**

- `homelab-setup.sh` — installs `focuslock-mail.py` (vault relay + IMAP
  payment scanner + LLM eval), the ADB bridge, and the systemd units that
  keep them running. Use on a homelab box you already own.
- `homelab-install.sh` — a self-contained, idempotent installer for a **fresh
  Debian/Ubuntu VPS** acting as the relay. Same end state as `homelab-setup.sh`
  but bootstraps the whole box from scratch.
- `install-standing-orders.sh` — pulls the Claude Code config from the
  operator's homelab and installs the systemd timer that keeps it in sync.
  Called automatically by `install-desktop-collar.sh` when a homelab URL is
  configured; can also be run standalone.

## Operator: re-enslave

`re-enslave-*.sh` push fresh code to already-collared machines. Use after a
code push that the operator wants live without waiting for the next install
cycle.

- `re-enslave-all.sh` — orchestrator: server, then desktops, then phones.
- `re-enslave-server.sh` — homelab `focuslock-mail.py` + shared modules.
- `re-enslave-desktops.sh` — `/opt/focuslock/` on Linux desktop collars.
  Two-phase: user-side first (icons, autostart, lion_pubkey) so it always
  makes some progress; system-side requires sudo and soft-fails when
  unavailable.
- `re-enslave-phones.sh` — APKs to phones via ADB.
- `re-enslave-lib.sh` — shared helpers, sourced by the others.
- `re-enslave-watcher.py` + `.service` + `.timer` — systemd timer that
  watches the canonical git repo and auto-runs the appropriate
  `re-enslave-*.sh` when a relevant path changes.
- `re-enslave.config.example` — copy to `~/.config/focuslock/re-enslave.config`,
  fill in the operator's host/device list.

## Presets & local convenience

Not part of a normal setup — safe to ignore.

- `install-mesh-jace.sh` — a **personal preset**: a thin wrapper that hard-codes
  one specific `mesh_id` + `mesh_url` and calls `install-mesh.sh`, so that owner's
  sideload needs no flags. It's an example of how to bake a preset for your own
  mesh, not a script anyone else runs.
- `retrofit-local.sh` — self-update the collar on *this* machine: pulls the
  latest tray / daemon / mesh / shared modules from the local source tree, copies
  them into `/opt/focuslock` + `~/.config/focuslock`, and restarts the daemons. A
  dev/operator shortcut; a bunny normally just re-runs `install-mesh.sh`.

## What's NOT here

- `release.sh` (build + ship APKs) and `qa-vault-mode.sh` (verify
  `vault_only` mode on phones) live in [`../scripts/`](../scripts/) — they're
  operator tools, not installers.
- The Android sideload flow lives in `re-enslave-phones.sh`; there's no
  consumer-facing phone installer in this directory because the phone
  apps install via APK sideload + first-run consent, not a script.

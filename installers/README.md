# Installers

Scripts that put the desktop collar on a target machine and wire it into a
mesh, plus operator-side scripts that bring up a homelab and redeploy code
to existing devices.

## Who runs what

| Audience | Goal | Script |
|---|---|---|
| **Bunny / consumer** | Pair a fresh Linux PC to an existing mesh | [`install-mesh.sh`](#consumer-quick-start) |
| **Bunny / consumer** | Pair a fresh Windows PC to an existing mesh | [`install-mesh.ps1`](#consumer-quick-start) |
| **Bunny / consumer** | Tear down the collar | [`uninstall-desktop-collar.sh`](#uninstall) / [`.ps1`](#uninstall) |
| **Operator / Lion** | Stand up the homelab (mail relay + ADB bridge) | [`homelab-setup.sh`](#operator-homelab) |
| **Operator / Lion** | Push code updates to all collared machines | [`re-enslave-all.sh`](#operator-re-enslave) |
| **Operator / Lion** | Push code to one tier only (desktops / phones / server) | [`re-enslave-{desktops,phones,server}.sh`](#operator-re-enslave) |
| **Operator / Lion** | Auto-redeploy on git push | [`re-enslave-watcher.{py,service,timer}`](#operator-re-enslave) |
| **Operator / Lion** | Sync Claude Code standing orders | [`install-standing-orders.sh`](#operator-homelab) |

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
  keep them running.
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

## What's NOT here

- `release.sh` (build + ship APKs) and `qa-vault-mode.sh` (verify
  `vault_only` mode on phones) live in [`../scripts/`](../scripts/) — they're
  operator tools, not installers.
- The Android sideload flow lives in `re-enslave-phones.sh`; there's no
  consumer-facing phone installer in this directory because the phone
  apps install via APK sideload + first-run consent, not a script.

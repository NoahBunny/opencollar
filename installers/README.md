# Installers

Scripts that put the desktop collar (Linux + Windows) on a target machine and
wire it into a mesh.

## Pre-configured mesh installers — `install-mesh.sh` / `install-mesh.ps1`

One-shot installers for joining an existing mesh. Useful when you already
have a relay running (your own self-host or a third-party relay) and want to
hand a clean machine over to a Bunny without walking through the post-consent
"where do I enter the homelab info" prompt.

The installer writes `config.json` under `~/.config/focuslock/` (Linux) or
`%APPDATA%\focuslock\` (Windows), preserves any existing vault keypair so a
prior Lion approval keeps working, and then runs the platform-specific
collar installer.

### Required parameters

Both scripts require `mesh_id` and `mesh_url` — there is no default mesh.
Pass them as flags or via environment variables:

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
- The platform installer below runs whether or not the collar was installed
  before; it updates files in place.

## Other installers

- `install-desktop-collar.sh` — Linux first-time install + update (called by
  `install-mesh.sh`). Walks consent + writes the systemd user units +
  drops the Python deps.
- `homelab-setup.sh` — operator-side homelab bring-up (mail relay + ADB
  bridge). Not for consumer Bunny installs.
- `re-enslave-*.sh` — operator-side update scripts that re-deploy the
  collar(s) after a code push. See `re-enslave.config.example` for the
  variables they expect.
- `release.sh` — release-cutting helper (operator-only).
- `uninstall-desktop-collar.{sh,ps1}` — Lion-side teardown. Removes config,
  binaries, scheduled tasks, and (on Windows) the Task Scheduler entries.

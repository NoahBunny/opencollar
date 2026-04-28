# Staging Mesh Setup

Isolated environment for running QA regressions against The Collar ecosystem without touching your production relay.

**Goal:** run the full `docs/QA-CHECKLIST.md` matrix against a relay, two Waydroid phones, and optionally a throwaway desktop collar — all pointing at a staging mesh that cannot reach prod and vice versa.

---

## Isolation principles

Production staging must NEVER share:

| Resource | Prod | Staging |
|----------|------|---------|
| `mesh_id` | operator's real mesh | distinct random base64url |
| `admin_token` | real token | distinct random token |
| Relay URL | `https://your-relay.example` | `http://127.0.0.1:8435` or `http://staging.<your-domain>` |
| `ntfy_topic` | real topic | distinct random topic |
| State files | `/run/focuslock/` | `/tmp/focuslock-staging/` |
| Config file | `~/.config/focuslock/config.json` | `staging/config.json` (repo-local) |
| Lion private key | real key | throwaway test keypair |

If any of these overlap, staging messages can leak into production or vice versa — tests become unreliable and the prod collar may behave unexpectedly.

---

## One-time setup

1. **Clone fresh working tree** (or use `git worktree add ../staging-worktree` if you want a separate checkout).

2. **Generate staging secrets:**

   ```bash
   python3 -c "import secrets, base64; print('mesh_id:', base64.urlsafe_b64encode(secrets.token_bytes(12)).decode().rstrip('='))"
   python3 -c "import secrets; print('admin_token:', secrets.token_urlsafe(32))"
   python3 -c "import secrets; print('ntfy_topic: staging-' + secrets.token_urlsafe(16))"
   ```

3. **Copy `staging/config.json.template` → `staging/config.json`** and fill in the generated secrets. Never commit `staging/config.json` (it's in `.gitignore`).

4. **Generate a throwaway Lion keypair** for staging (do NOT reuse production keys):

   ```bash
   python3 -c "
   import sys; sys.path.insert(0, 'shared')
   from focuslock_vault import generate_keypair
   priv, pub, _ = generate_keypair()
   with open('staging/lion_privkey.pem', 'w') as f: f.write(priv)
   with open('staging/lion_pubkey.pem', 'w') as f: f.write(pub)
   "
   chmod 600 staging/lion_privkey.pem
   ```

5. **Install Waydroid** (Arch: `pacman -S waydroid`, Debian: see `https://docs.waydro.id/`). Two instances — one per phone role.

   ```bash
   waydroid init
   # Wait for bring-up, then configure networking
   sudo waydroid shell  # should drop into Android shell
   ```

6. **Install staging APKs** into Waydroid:

   ```bash
   # Waydroid #1 — bunny phone (Collar + Companion)
   waydroid app install apks/focuslock-v60.apk
   waydroid app install apks/bunnytasker-v43.apk

   # Waydroid #2 — lion phone (Lion's Share)
   waydroid app install apks/focusctl-v63.apk
   ```

---

## Starting a staging session

```bash
# Terminal 1 — staging relay
bash staging/start-staging.sh

# Terminal 2 — Waydroid #1 (bunny)
waydroid session start
waydroid app launch com.focuslock

# Terminal 3 — Waydroid #2 (lion)
waydroid session start
waydroid app launch com.focusctl
```

The staging relay binds to `127.0.0.1:8435` by default — configure phones' `mesh_url` to `http://<host-LAN-IP>:8435` (Waydroid runs in a network namespace that can reach host LAN).

---

## Running the checklist

Open `docs/QA-CHECKLIST.md` in one pane, staging relay logs in another. Walk through each section and record pass/fail.

For the scriptable subset, `pytest tests/` with the Waydroid mesh as the backend will automate most of sections 8 (vault) and 9 (mesh convergence). Sections involving physical radios (SMS, Lovense, camera) need real hardware — see `docs/MANUAL-QA.md`.

### Unified `make qa` entry (audit 2026-04-27 Stream C)

The repo root `Makefile` exposes one-command targets that bring the staging relay up, drive the four QA layers, and tear down — replacing the prior "manual relay restart between runs" friction:

```bash
make qa              # full sweep: clean → up → pytest + qa_runner +
                     #             qa_wizard_browser + qa_index_browser → down
make qa-fast         # ruff + pytest only (no staging relay, ~90s)
make qa-pytest       # pytest tests/
make qa-runner       # staging/qa_runner.py against running relay
make qa-browser      # staging/qa_{wizard,index}_browser.py
make qa-staging-up   # start the staging relay in background
make qa-staging-down # stop the staging relay
make qa-clean        # wipe /tmp/focuslock-staging/
make help            # list targets
```

`make qa` is idempotent: it cleans state, brings up the relay, polls `/version` for readiness (15s timeout), runs the four QA layers in sequence, and tears down even on failure. `staging/config.json` + `staging/lion_*.pem` are required (one-time setup, see steps 2–4 above).

**Environment overrides:** `PYTHON` (default `.venv/bin/python`), `STAGING_PORT` (default 8435), `STAGING_STATE_DIR` (default `/tmp/focuslock-staging`), `STAGING_PIDFILE`, `STAGING_LOGFILE`.

---

## Tearing down

```bash
make qa-staging-down  # stop the relay
make qa-clean         # wipe /tmp/focuslock-staging/
waydroid session stop # if Waydroid was running
```

`staging/config.json` and `staging/lion_*.pem` persist between sessions — delete manually if you want a clean slate.

---

## Gotchas

- **Waydroid can't use Bluetooth LE** — Lovense tests must be real-device.
- **Waydroid camera is a software stub** — Photo Task works for the upload path but Ollama eval will always be against the stub frame. Use a real phone for Photo Task end-to-end.
- **Staging ntfy on `ntfy.sh`** — pick a long, random topic (32 bytes+ entropy) so your staging traffic isn't globally visible. Better: self-host ntfy on `127.0.0.1` and point both sides at it.
- **Clock skew** — vault signatures have no timestamp check, but `sub_due` and `countdown_lock_at` do. Keep host and Waydroid clocks synced.
- **DNS resolution in Waydroid** — if relay is `staging.local`, add to Waydroid's `/etc/hosts`. Easier: always use the host's LAN IP literal.

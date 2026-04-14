# Configuration Reference

Every field that goes into a `config.json` for the desktop collar, server, or any Python component.

---

## Where the config lives

| Platform | Default path |
|----------|--------------|
| Linux desktop | `~/.config/focuslock/config.json` |
| Linux server  | `/opt/focuslock/config.json` (auto-detected if `/opt/focuslock` exists) |
| Windows       | `%APPDATA%\focuslock\config.json` |

Override the path with the `FOCUSLOCK_CONFIG` environment variable.

Permissions: `chmod 0600`. The file holds the mesh PIN, admin token, and (on the server) IMAP password.

---

## Mesh fields (all components)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `pin` | string | `""` | 6+ char shared PIN authenticating mesh gossip. **Required.** Never reuse across meshes. |
| `mesh_id` | string | — | Base64url mesh identifier. Auto-generated on first run if absent. |
| `mesh_url` | string | `""` | Vault relay URL, e.g. `https://focus.example.com`. Empty = relay-less (Syncthing transport or LAN-only). |
| `mesh_port` | int | `8435` | LAN HTTP port for direct peer gossip. |
| `phone_addresses` | list[str] | `[]` | LAN/Tailscale IPs of bunny phones. Cap of 4 per peer. |
| `phone_port` | int | `8432` | Bunny phone HTTP server port. |
| `homelab_url` | string | `""` | Homelab base URL (optional, e.g. `http://homelab:8434`). |
| `homelab_port` | int | `8434` | Homelab HTTP port. |
| `poll_interval` | int | `5` | Seconds between mesh status polls. |
| `gossip_interval` | int | `10` | Seconds between gossip ticks (parallel push to all peers). |

### Vault transport (P7)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `vault_transport` | enum | `"http"` | `"http"` (relay) or `"syncthing"` (P2P file sync) |
| `syncthing_vault_dir` | string | `""` | Path to a Syncthing-shared folder when `vault_transport = "syncthing"` |
| `vault_mode` | bool | (auto) | Set `true` on desktop collars to enable encrypted vault poll instead of legacy plaintext. **Required for current code paths.** |

### ntfy push (optional, latency optimization)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `ntfy_enabled` | bool | `false` | Enable instant push notifications |
| `ntfy_server` | string | `"https://ntfy.sh"` | Server URL (use a self-hosted ntfy for zero third-party exposure) |
| `ntfy_topic` | string | `""` | Topic name; auto-derived from `mesh_id` if empty. **Payload is only `{"v":N}`** — no order content leaks. |

---

## Server-only fields (`focuslock-mail.py`)

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `admin_token` | string | `""` | Bearer token for `/admin/*` endpoints. **Required for self-hosted.** Generate with `python -c 'import secrets; print(secrets.token_urlsafe(32))'` |
| `operator_mesh_id` | string | `""` | Scope the admin API to a single mesh. Empty = pure public relay (no admin API at all). |
| `mail` | object | `{}` | IMAP/SMTP settings (see below) |
| `banking` | object | `{}` | Payment-detection tunables (see below) |

### `mail` sub-object

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `imap_host` | string | `""` | IMAP server for payment detection, e.g. `imap.gmail.com` |
| `smtp_host` | string | `""` | SMTP server for evidence emails |
| `user` | string | `""` | Account username |
| `pass` | string | `""` | App-specific password (never your main password) |
| `partner_email` | string | `""` | Lion's email — recipient of evidence + alerts |

### `banking` sub-object

| Field | Type | Default | Notes |
|-------|------|---------|-------|
| `app_packages` | list[str] | `[]` | Banking app package names (used by Bunny Tasker self-pay shortcuts) |
| `payment_url` | string | `""` | Payment portal URL surfaced in Bunny Tasker |
| `currency_symbols` | list[str] | `["$"]` | Symbols recognized in payment emails |
| `min_payment` | float | `0.01` | Floor for valid payment amounts |
| `max_payment` | float | `10000` | Ceiling — amounts above are rejected as suspicious |
| `payment_keywords` | list[str] | (built-in) | Words that must appear in a payment email body |

The full bank dictionary (145+ banks across 21 regions) lives in `shared/banks.json` — overrides go here, full replacement goes there.

---

## Environment variable overrides

Any config value can be overridden by an environment variable. The `FOCUSLOCK_` prefix maps to the top-level field; double-underscore reaches into nested objects.

| Env var | Maps to |
|---------|---------|
| `FOCUSLOCK_CONFIG` | (path to config file) |
| `FOCUSLOCK_PIN` | `pin` |
| `FOCUSLOCK_MESH_URL` | `mesh_url` |
| `FOCUSLOCK_HOMELAB_URL` | `homelab_url` |
| `FOCUSLOCK_PHONE_ADDRESSES` | `phone_addresses` (comma-separated) |
| `FOCUSLOCK_MESH_PORT`, `_PHONE_PORT`, `_HOMELAB_PORT` | matching ports |
| `FOCUSLOCK_POLL_INTERVAL`, `_GOSSIP_INTERVAL` | matching intervals |
| `FOCUSLOCK_ADMIN_TOKEN` | `admin_token` |
| `FOCUSLOCK_OPERATOR_MESH_ID` | `operator_mesh_id` |
| `FOCUSLOCK_NTFY_ENABLED`, `_NTFY_SERVER`, `_NTFY_TOPIC` | matching ntfy fields |
| `FOCUSLOCK_VAULT_TRANSPORT`, `_SYNCTHING_VAULT_DIR` | matching transport fields |
| `MAIL_HOST`, `SMTP_HOST`, `MAIL_USER`, `MAIL_PASS`, `PARTNER_EMAIL` | `mail.*` |
| `FOCUSLOCK_BANKING_URL` | `banking.payment_url` |
| `PHONE_URL` | parsed into `phone_addresses` + `phone_port` |
| `PHONE_PIN` (legacy) | `pin` |

---

## Security implications field-by-field

| Field | If leaked | If misset |
|-------|-----------|-----------|
| `pin` | Any attacker on the mesh URL can join the gossip group and push state | Mesh stops accepting orders from your real peers |
| `admin_token` | Full control of paywall, locks, subscriptions on the operator mesh | Admin API is locked out |
| `operator_mesh_id` | Mesh ID is intended to be public; leak = harmless | Admin API operates against the wrong mesh — high risk of misdirected actions |
| `mail.pass` | Account compromise → attacker can forge payment emails | IMAP polling fails silently — paywall never clears |
| `homelab_url` | URL itself is harmless; protect with admin_token | Bunny Tasker buttons that hit the homelab silently fail |
| `phone_addresses` | LAN topology disclosure | Mesh falls back to relay-only — slower convergence |

---

## Example minimal configs

### Public hosted-relay user (no admin API, no homelab)

```json
{
  "pin": "REPLACE-WITH-LONG-RANDOM-PIN",
  "mesh_url": "https://focus.example.com",
  "phone_addresses": ["100.64.0.5"],
  "vault_mode": true,
  "ntfy_enabled": true
}
```

### Self-hosted operator (server config)

```json
{
  "pin": "REPLACE-WITH-LONG-RANDOM-PIN",
  "admin_token": "REPLACE-WITH-secrets.token_urlsafe(32)",
  "operator_mesh_id": "your-base64url-mesh-id",
  "mesh_url": "https://focus.example.com",
  "mail": {
    "imap_host": "imap.example.com",
    "smtp_host": "smtp.example.com",
    "user": "alerts@example.com",
    "pass": "app-specific-password",
    "partner_email": "lion@example.com"
  }
}
```

### Syncthing-only (no relay at all)

```json
{
  "pin": "REPLACE-WITH-LONG-RANDOM-PIN",
  "vault_transport": "syncthing",
  "syncthing_vault_dir": "~/Sync/focuslock-vault",
  "phone_addresses": ["100.64.0.5"],
  "vault_mode": true
}
```

# Self-Hosting Guide

Stand up your own relay end-to-end: DNS, TLS, server, first phone pairing.

This is the path the maintainers run on `focus.wildhome.ca`. If you'd rather use a hosted relay you don't operate, skip to the README — there's nothing here you need.

---

## Why self-host?

- **Admin API.** The public relay is zero-knowledge by design; it cannot drive enforcement (paywall, subscriptions, evidence). Self-host enables `/admin/*`.
- **No third party at all.** Even the public relay sees blob sizes and timing. Self-host removes that channel.
- **Multi-tenant lockdown.** `operator_mesh_id` pins admin actions to a single mesh — a leaked admin token can't be aimed at someone else's mesh.
- **Custom integrations.** Mail service (IMAP payment detection), Ollama photo verification, ADB bridge enforcement.

---

## Prerequisites

- A VPS, homelab box, or anything that can hold an inbound port 443 with a public IP. ~256 MB RAM is fine.
- A domain name you control. A subdomain works (`focus.example.com`).
- Python 3.10+, `pip`, `systemd` (any modern Linux distribution).
- A reverse proxy that can do TLS termination — Caddy is recommended for zero config; nginx + certbot works too.

---

## 1. DNS

Create an `A` record (or `AAAA` for IPv6) pointing your subdomain at the server's IP:

```
focus.example.com.   IN  A   203.0.113.42
```

Wait for propagation. Verify with `dig +short focus.example.com`.

---

## 2. Server install

Clone the repo onto the server and run the installer:

```bash
git clone https://github.com/YOUR-FORK/lions-share-bunny-tasker.git
cd lions-share-bunny-tasker
sudo bash installers/homelab-setup.sh
```

The installer will:

- Create `/opt/focuslock/` and copy the server modules in
- Install Python dependencies into a venv
- Generate a relay keypair at `/opt/focuslock/relay_{priv,pub}key.pem` (mode 0600)
- Write a default `/opt/focuslock/config.json` with placeholder values
- Install and enable a `focuslock-mail` systemd unit

---

## 3. Configure

Edit `/opt/focuslock/config.json`. Minimum for a working operator setup:

```json
{
  "pin": "<run: python -c \"import secrets;print(secrets.token_urlsafe(24))\">",
  "admin_token": "<run: python -c \"import secrets;print(secrets.token_urlsafe(32))\">",
  "operator_mesh_id": "<your-mesh-id, see step 5>",
  "mesh_url": "https://focus.example.com"
}
```

If you want IMAP payment detection and evidence emails, fill in the `mail` block too. See `docs/CONFIG.md` for every field.

`chmod 0600 /opt/focuslock/config.json` and restart the service:

```bash
sudo systemctl restart focuslock-mail
sudo journalctl -u focuslock-mail -f
```

You should see `Listening on 0.0.0.0:8080` (or whatever the bind port is). The server only speaks vault — no plaintext endpoints exist.

---

## 4. TLS reverse proxy

The server listens on plain HTTP. Put it behind a reverse proxy that terminates TLS.

### Caddy (recommended)

`/etc/caddy/Caddyfile`:

```
focus.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

```bash
sudo systemctl reload caddy
```

Caddy auto-provisions a Let's Encrypt cert. Done.

### nginx + certbot

```
server {
    listen 443 ssl http2;
    server_name focus.example.com;

    ssl_certificate     /etc/letsencrypt/live/focus.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/focus.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

---

## 5. Provision your first mesh

Two paths.

### Path A — `/signup` self-service

Visit `https://focus.example.com/signup` in a browser. Paste your Lion's RSA pubkey, get an invite code. Tell your bunny the relay URL and invite code.

Self-service signup is rate-limited (3 meshes per hour per IP). The mesh ID is generated for you and shown in the response.

### Path B — Manual provisioning

```bash
# Generate a mesh ID
python3 -c 'import secrets, base64; print(base64.urlsafe_b64encode(secrets.token_bytes(16)).rstrip(b"=").decode())'
```

Put that string into `operator_mesh_id` in `config.json` and restart `focuslock-mail`. The mesh exists once it has any vault blob written to it.

---

## 6. Pair your phone

On Bunny's phone, sideload the three APKs (built per `docs/BUILD.md`):

```bash
adb install -r android/slave/focuslock-signed.apk
adb install -r android/companion/bunnytasker-signed.apk
```

On Lion's phone:

```bash
adb install -r android/controller/focusctl-signed.apk
```

Open Lion's Share. Set the relay URL to `https://focus.example.com`. Generate a Lion keypair (Settings → Keys). Open Bunny Tasker on Bunny's phone, scan the QR code from Lion's Share to pair.

Sanity check: in Lion's Share, fire a 1-minute test lock. Bunny's phone should lock within ~10 seconds (gossip interval) or instantly if `ntfy_enabled` is set.

---

## 7. Optional: ntfy push (instant orders)

Sign up at https://ntfy.sh or self-host (`docker run binwiederhier/ntfy serve`). Add to every component's config:

```json
{
  "ntfy_enabled": true,
  "ntfy_server": "https://ntfy.sh",
  "ntfy_topic": "focuslock-<some-random-suffix>"
}
```

Topic is auto-derived from `mesh_id` if you leave it blank. The payload is only `{"v": N}` — order content stays in the vault transport.

---

## 8. Optional: Homelab ADB bridge

For deep enforcement (jail re-engagement on reboot, volume lock, tamper detection), run the homelab bridge alongside the server:

```bash
sudo bash installers/homelab-setup.sh --enable-bridge
```

Connect Bunny's phone to the homelab via USB or wireless ADB. The bridge polls device state and re-engages the cage if Bunny manages to factory reset.

---

## Operations

| Task | Command |
|------|---------|
| Restart server | `sudo systemctl restart focuslock-mail` |
| Watch logs | `sudo journalctl -u focuslock-mail -f` |
| Update server | `bash installers/re-enslave-server.sh` |
| Update desktop collars | `bash installers/re-enslave-desktops.sh` |
| Update phones (USB) | `bash installers/re-enslave-phones.sh` |
| Check paywall | `curl -s "https://focus.example.com/admin/status?admin_token=$ADMIN_TOKEN" \| jq .orders.paywall` |
| Add paywall (admin) | `curl -X POST "https://focus.example.com/admin/order" -H 'Content-Type: application/json' -d '{"admin_token":"...","action":"add-paywall","params":{"amount":10}}'` |

---

## Backup checklist

If you lose any of the following, recovery ranges from "annoying" to "you lose the mesh":

| Asset | Where | Recovery if lost |
|-------|-------|------------------|
| `/opt/focuslock/relay_privkey.pem` | server | Mesh members reject relay-signed admin orders. Fixable by re-registering the new pubkey on every node, but every member has to re-trust. |
| `/opt/focuslock/config.json` | server | Recreate from this guide. The PIN must match the bunny's phone PIN. |
| Lion private key (on Lion's phone) | phone | **Game over** for that mesh. Bunny will never accept orders signed by the new key without manual re-pair. |
| Android release keystore | wherever you put it | Cannot publish updates to existing installs. New installs only. |

Backup the first three offline. Encrypt and store somewhere durable.

---

## Hardening

- Keep the admin token out of shell history (`HISTCONTROL=ignorespace` and prefix commands with a space).
- Bind the server to `127.0.0.1` and let only the reverse proxy reach it.
- Run the server as a non-root user (the installer does this).
- Restrict `/opt/focuslock/` to mode 0700 and the config + privkey to 0600.
- Enable fail2ban on the reverse proxy if you expose `/admin/*` publicly. (Better: VPN-only access via Tailscale + nginx allowlist.)

See `docs/THREAT-MODEL.md` for the full picture.

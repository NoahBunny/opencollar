# 2026-04-03 Mesh Reliability Overhaul

## Problem

The P2P enforcement mesh was fickle -- devices would intermittently show as disconnected (gray tray icon) despite all being mutually reachable on the same LAN via Syncthing. Orders propagation was unreliable.

## Root Cause Analysis

Six issues were identified in `focuslock_mesh.py`, two critical:

### 1. CRITICAL: Config-seeded peers silently dropped by WARREN_WHITELIST

`_seed_configured_peers()` in both desktop collars registered peers using generic IDs (`"phone"`, `"homelab"`), but `PeerRegistry.update_peer()` has a guard:

```python
if node_id not in WARREN_WHITELIST:
    return  # silently dropped
```

`WARREN_WHITELIST` only contained specific hostnames (now configurable per deployment) -- not the generic seed IDs. Every config-seeded peer was silently discarded. The `GossipThread` started with zero peers and relied entirely on LAN discovery or `direct_sync_poll()` to find anyone.

### 2. CRITICAL: UDP port 21027 collision with Syncthing

`LAN_DISCOVERY_PORT = 21027` is Syncthing's Local Discovery Protocol port. On Windows (no `SO_REUSEPORT`), only one process receives packets -- whichever bound last. Since Syncthing starts at boot and holds the port, FocusLock's `_listen_loop` either failed to bind or lost the race. LAN peer discovery was dead on every Syncthing-equipped device.

Combined effect: gossip had no peers (seeding broken) and couldn't discover them (port collision). The mesh depended entirely on `direct_sync_poll()` hitting the HTTPS mesh URL or hardcoded IPs -- serial, single-endpoint, and fickle.

### 3. HIGH: GossipThread.my_addresses captured once at startup

`mesh.get_local_addresses()` was called once when constructing the `GossipThread` and stored as `self.my_addresses`. After DHCP renewal, WiFi roaming, or Tailscale reconnect, the gossip thread kept announcing the stale IP. Other nodes tried to reach it at the dead address, timed out, and considered it offline.

### 4. HIGH: Stale address accumulation (never pruned)

`update_peer()` added addresses to a set but never removed dead ones. Over time, peers accumulated dead IPs from old DHCP leases and Tailscale IP rotations. `_try_peer_addrs()` tried each address sequentially with a 3-5s timeout -- a peer with 4 stale IPs took 12-20s before reaching the working one.

### 5. MEDIUM: Sequential gossip blocked all peers

`gossip_tick()` iterated peers sequentially. If peer A had stale addresses taking 15s to timeout, peers B/C/D weren't contacted that tick. With `gossip_interval=10`, the mesh perpetually fell behind.

### 6. LOW: Tailscale hostname cache too long

`_TS_HOSTNAME_REFRESH_INTERVAL = 300` (5 min). After a Tailscale IP change (CGNAT rotation), it took up to 5 min to discover the new IP. Combined with stale address accumulation, old IPs lingered indefinitely.

## Changes Made

All changes in `focuslock_mesh.py` only (shared module imported by both desktop collars).

| Fix | Description | Location |
|-----|-------------|----------|
| A | Added `"phone"` and `"homelab"` to `WARREN_WHITELIST` | ~line 275 |
| B | Changed `LAN_DISCOVERY_PORT` from 21027 to 21037 | ~line 962 |
| C | `GossipThread.run()` re-resolves addresses via `get_local_addresses()` each tick | ~line 746 |
| D | `_try_peer_addrs()` promotes working address to front, caps list at 4 (`MAX_PEER_ADDRESSES`) | ~line 457 |
| E | `gossip_tick()` contacts all peers in parallel threads with 10s deadline | ~line 536 |
| F | Tailscale hostname cache reduced from 300s to 60s | ~line 875 |

## Files Modified

- `focuslock_mesh.py` -- all 6 fixes

## Files NOT Modified

- `focuslock-desktop.py` (Linux collar) -- no changes needed, imports mesh module
- `focuslock-desktop-win.py` (Windows collar) -- no changes needed, imports mesh module

## Backups

Original files saved as `.orig` on the homelab server at `/tmp/`:
- `/tmp/focuslock_mesh.py.orig`
- `/tmp/focuslock-desktop.py.orig`
- `/tmp/focuslock-desktop-win.py.orig`

## Deployment

Since only `focuslock_mesh.py` was changed and it's a shared module:

### Linux desktops

Syncthing/Nextcloud will sync the file automatically. Then either:

**Option A -- Full re-enslave (recommended):**
```
bash "installers/re-enslave-all.sh"
```

**Option B -- Manual restart on each machine:**
```
sudo cp focuslock_mesh.py /opt/focuslock/
pkill -f focuslock-desktop.py
systemctl --user restart focuslock-desktop.service
```

### Windows desktops

Windows uses compiled .exe files, so the mesh module is bundled inside:

```
python build-win.py --skip-sign
```

Then copy the new `FocusLock-Paired.exe` to each Windows machine.

Alternatively, if the Windows collars run from source (not .exe), just restart -- they import `focuslock_mesh.py` at startup.

### Homelab server

```
ssh $USER@homelab
sudo cp /path/to/focuslock_mesh.py /opt/focuslock/
sudo systemctl restart focuslock-mail
```

### Phones (Pixel, Samsung)

No deployment needed -- phones run Android APKs with their own mesh implementation. They are peers, not consumers of the Python mesh module.

## Verification

After deployment, on any node:

**1. Confirm LAN discovery binds on new port:**
```
journalctl -u focuslock-desktop --since "5min ago" | grep "LAN discovery"
# Expected: "[mesh] LAN discovery listening on UDP :21037"
```

**2. Confirm peers are registered:**
```
curl -s http://localhost:8435/mesh/status | python3 -c "
import sys, json
d = json.load(sys.stdin)
for nid, info in sorted(d['nodes'].items()):
    print(f'  {nid}: online={info[\"online\"]} v={info[\"orders_version\"]}')
"
```

**3. Check gossip completes quickly (parallel -- should finish in ~5s even with stale peers):**
```
journalctl -u focuslock-desktop --since "1min ago" | grep "[mesh]"
```

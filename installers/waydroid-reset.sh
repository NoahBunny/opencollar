#!/usr/bin/env bash
#
# waydroid-reset.sh — factory-fresh Waydroid in ~30s, with no download.
#
# `sudo waydroid init -f` re-fetches ~1 GB of system + vendor images every time,
# which is the slow way to get back to a clean device. Those images are read-only
# and identical after every reset; the only thing carrying state is the Android
# /data partition, and on Waydroid that is an ordinary directory in your home:
#
#   /var/lib/waydroid/images/{system,vendor}.img   2.2 GB, root-owned, REUSED
#   ~/.local/share/waydroid/data                   ~5 MB, yours, WIPED
#
# So this stops the session, deletes /data, and boots again. Android rebuilds a
# fresh /data from the images exactly as it would after `init -f`, minus the
# download.
#
# It still needs sudo for exactly one command: some top-level entries in /data
# (vendor, apex, property) are root-owned, so `rm` as your user cannot remove
# them. One password prompt instead of a gigabyte.
#
# It also does the two things a QA rig always needs afterwards and which are easy
# to forget: seeding the host's adb key so `adb connect` is not stuck on
# "unauthorized", and setting a phone-shaped screen so a portrait UI is being
# tested at portrait size.
#
# Usage:
#   installers/waydroid-reset.sh                 # wipe, boot, seed adb, 1080x2400@420
#                                                # (prompts once for sudo)
#   installers/waydroid-reset.sh --yes           # no confirmation prompt
#   installers/waydroid-reset.sh --no-adb-key    # leave adb unauthorised
#   installers/waydroid-reset.sh --geometry 1440x3120@560
#   installers/waydroid-reset.sh --no-start      # wipe only, leave it stopped
#
# If the images are missing it says so and stops, rather than wiping and leaving
# you with a container that cannot boot.

set -euo pipefail

DATA_DIR="$HOME/.local/share/waydroid/data"
IMAGES_DIR="/var/lib/waydroid/images"
ADB_KEY="$HOME/.android/adbkey.pub"
GEOMETRY="1080x2400@420"
ASSUME_YES=0
SEED_ADB=1
START_AFTER=1

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)      ASSUME_YES=1 ;;
        --no-adb-key)  SEED_ADB=0 ;;
        --no-start)    START_AFTER=0 ;;
        --geometry)    GEOMETRY="${2:-}"; shift ;;
        -h|--help)     sed -n '2,32p' "$0"; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

say()  { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m  ! \033[0m%s\n' "$*"; }
die()  { printf '\033[1;31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }

command -v waydroid >/dev/null 2>&1 || die "waydroid is not installed."

# The whole point is reusing these. Without them a reset is a re-download, and
# the caller should be told that instead of discovering it at boot.
if [ ! -f "$IMAGES_DIR/system.img" ] || [ ! -f "$IMAGES_DIR/vendor.img" ]; then
    die "no images at $IMAGES_DIR — this needs a one-time 'sudo waydroid init'.
     That download is the thing this script exists to avoid repeating; it
     cannot skip the first one."
fi

say "images kept:   $(du -sh "$IMAGES_DIR" 2>/dev/null | cut -f1)  ($IMAGES_DIR)"
if [ -d "$DATA_DIR" ]; then
    say "data to wipe:  $(du -sh "$DATA_DIR" 2>/dev/null | cut -f1)  ($DATA_DIR)"
else
    say "data to wipe:  none — already clean"
fi

if [ "$ASSUME_YES" -ne 1 ] && [ -d "$DATA_DIR" ]; then
    printf '    Everything installed in the container is destroyed. Continue? [y/N] '
    read -r reply
    case "$reply" in [yY]*) ;; *) echo "    aborted."; exit 1 ;; esac
fi

say "Stopping the session…"
waydroid session stop >/dev/null 2>&1 || true
# The session takes a moment to release /data; deleting under a live mount
# leaves a half-populated directory that boots into a broken device.
for _ in $(seq 1 15); do
    waydroid status 2>/dev/null | grep -q 'Session:.*STOPPED' && break
    sleep 1
done

say "Wiping /data…"
if [ -d "$DATA_DIR" ]; then
    # Parts of /data are root-owned (vendor, apex, property), and rm needs write
    # permission on the PARENT to unlink a child — so this is sudo or nothing.
    # Scoped to exactly one path, and never the images.
    if [ -n "$(find "$DATA_DIR" -maxdepth 1 -mindepth 1 ! -user "$(id -un)" -print -quit 2>/dev/null)" ]; then
        if sudo -n true 2>/dev/null; then
            sudo rm -rf "$DATA_DIR"
        elif [ -t 0 ]; then
            say "  root-owned entries present — one sudo prompt (the images are untouched)"
            sudo rm -rf "$DATA_DIR"
        else
            die "parts of /data are root-owned and sudo has no terminal to prompt on.
     Run this from a terminal, or clear it yourself with:
       sudo rm -rf $DATA_DIR"
        fi
    else
        rm -rf "$DATA_DIR"
    fi
fi
[ ! -e "$DATA_DIR" ] || die "could not fully remove $DATA_DIR"

if [ "$START_AFTER" -ne 1 ]; then
    say "Done. Start it yourself with:  waydroid session start"
    exit 0
fi

say "Booting…"
setsid waydroid session start >/dev/null 2>&1 &
booted=0
for _ in $(seq 1 90); do
    if waydroid status 2>/dev/null | grep -q 'Session:.*RUNNING'; then booted=1; break; fi
    sleep 1
done
[ "$booted" -eq 1 ] || die "the session did not come up — check 'waydroid log'."

# adbd reads authorised keys once at boot, so seeding has to happen before the
# boot that matters. /data was just recreated, hence a second, quick restart —
# still no download.
if [ "$SEED_ADB" -eq 1 ] && [ -f "$ADB_KEY" ]; then
    say "Authorising this host's adb key…"
    mkdir -p "$DATA_DIR/misc/adb"
    cp -f "$ADB_KEY" "$DATA_DIR/misc/adb/adb_keys"
    chmod 640 "$DATA_DIR/misc/adb/adb_keys"
    waydroid session stop >/dev/null 2>&1 || true
    sleep 4
    setsid waydroid session start >/dev/null 2>&1 &
    for _ in $(seq 1 90); do
        waydroid status 2>/dev/null | grep -q 'Session:.*RUNNING' && break
        sleep 1
    done
elif [ "$SEED_ADB" -eq 1 ]; then
    warn "no $ADB_KEY — run 'adb start-server' once, then re-run with --no-start skipped."
fi

IP="$(waydroid status 2>/dev/null | sed -n 's/^IP address:[[:space:]]*//p' | head -1)"
say "Up at ${IP:-<unknown>}"

if [ -n "${IP:-}" ] && command -v adb >/dev/null 2>&1; then
    # adbd is not listening the instant the session reports RUNNING, and a
    # single connect-then-check reports "unauthorised" for a device that is
    # merely still booting. Retry instead of guessing a sleep. kill-server
    # first: the host caches a device as unauthorised and will keep saying so
    # after the container has accepted the key.
    adb kill-server >/dev/null 2>&1 || true
    authorised=0
    for _ in $(seq 1 20); do
        adb connect "$IP:5555" >/dev/null 2>&1 || true
        if adb devices 2>/dev/null | grep -q "$IP:5555[[:space:]]*device"; then authorised=1; break; fi
        sleep 2
    done
    if [ "$authorised" -eq 1 ]; then
        say "adb authorised"
        case "$GEOMETRY" in
            *x*@*)
                size="${GEOMETRY%@*}"; dpi="${GEOMETRY#*@}"
                adb -s "$IP:5555" shell wm size "$size"      >/dev/null 2>&1 || true
                adb -s "$IP:5555" shell wm density "$dpi"    >/dev/null 2>&1 || true
                say "screen set to $size @ ${dpi}dpi"
                ;;
        esac
    else
        warn "adb still unauthorised — 'waydroid show-full-ui' and accept the prompt."
    fi
fi

cat <<EOF

  Factory-fresh, nothing downloaded.

  A window is not open yet — apps have no surface to resume on until one is,
  so an activity started now stays paused. Open it with:

      waydroid show-full-ui

EOF

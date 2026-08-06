#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
Safeword — local, unilateral removal of the Windows Desktop Collar.

Use this when the Lion phone / private key is lost and a signed Release
order isn't available. Does NOT notify the mesh — you are responsible for
any other devices still on the mesh. Self-elevates. Safe to re-run.

Python source for dist/safeword.exe (built via build-win.py). Mirrors
installers/uninstall-desktop-collar.ps1 so double-clicking doesn't require
fighting PowerShell's execution policy.
"""

import ctypes
import os
import shutil
import subprocess
import sys
import time
import winreg

APPDATA = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), "focuslock")
INSTALL_DIR = r"C:\focuslock"
FIREWALL_RULE = "FocusLock Mesh (TCP 8435)"
WALLPAPER_SAVE = os.path.join(APPDATA, "original-wallpaper")
EXE_NAMES = ["FocusLock-Paired.exe", "FocusLock.exe", "FocusLock-Watchdog.exe"]


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin() != 0
    except Exception:
        return False


def step(msg):
    print(f"\n=== {msg} ===")


def info(msg):
    print(f"[+] {msg}")


def warn(msg):
    print(f"[!] {msg}")


def err(msg):
    print(f"[x] {msg}")


def elevate_and_relaunch():
    print("Re-launching with Administrator rights...")
    args = " ".join(f'"{a}"' if " " in a else a for a in sys.argv[1:])
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, args
    else:
        exe, params = sys.executable, f'"{os.path.abspath(__file__)}" {args}'.strip()
    result = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    if int(result) <= 32:
        err("Elevation cancelled or failed. Re-run this from an elevated prompt.")
        sys.exit(1)
    sys.exit(0)


def remove_scheduled_tasks():
    step("Removing scheduled tasks")
    ps = (
        'foreach ($t in "FocusLockCollar","FocusLockWatchdog") { '
        "if (Get-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue) { "
        "Disable-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue | Out-Null; "
        "Stop-ScheduledTask -TaskName $t -ErrorAction SilentlyContinue; "
        "Unregister-ScheduledTask -TaskName $t -Confirm:$false -ErrorAction SilentlyContinue; "
        'Write-Host "removed task $t" } }'
    )
    result = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
    for line in result.stdout.splitlines():
        if line.strip():
            info(line.strip())


def remove_registry_key():
    step("Removing autostart registry entry")
    try:
        key = winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run", 0, winreg.KEY_SET_VALUE
        )
        winreg.DeleteValue(key, "FocusLockCollar")
        winreg.CloseKey(key)
        info("removed HKCU Run\\FocusLockCollar")
    except FileNotFoundError:
        pass
    except Exception as e:
        warn(f"failed to remove registry key: {e}")

    # Clear the collar's forced lock-screen policy (set_lock_wallpaper writes
    # HKLM\SOFTWARE\Policies\Microsoft\Windows\Personalization\LockScreenImage).
    # restore_wallpaper() only fixes the desktop background; without this the
    # native lock screen keeps the collar's forced image policy, now pointing at
    # a deleted PNG — a visible residual after a "complete" removal. Needs admin
    # (safeword self-elevates); harmless if the value was never set.
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Policies\Microsoft\Windows\Personalization",
            0,
            winreg.KEY_SET_VALUE,
        )
        try:
            winreg.DeleteValue(key, "LockScreenImage")
            info("removed HKLM lock-screen policy LockScreenImage")
        except FileNotFoundError:
            pass
        winreg.CloseKey(key)
    except FileNotFoundError:
        pass
    except Exception as e:
        warn(f"failed to clear lock-screen policy: {e}")


def kill_processes():
    step("Killing collar processes")
    ps = (
        "Get-CimInstance Win32_Process -Filter \"Name = 'pythonw.exe' OR Name = 'python.exe'\" | "
        "Where-Object { $_.CommandLine -match 'focuslock-desktop-win\\.py|watchdog-win\\.pyw' } | "
        "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; "
        "Write-Host \"killed python pid $($_.ProcessId)\" }"
    )
    for _attempt in range(3):
        killed = False
        for name in EXE_NAMES:
            result = subprocess.run(["taskkill", "/F", "/IM", name], capture_output=True, text=True)
            if result.returncode == 0:
                killed = True
                info(f"killed {name}")
        result = subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, text=True)
        for line in result.stdout.splitlines():
            if line.strip():
                info(line.strip())
                killed = True
        if not killed:
            break
        time.sleep(0.5)


def restore_wallpaper():
    step("Restoring wallpaper")
    if not os.path.exists(WALLPAPER_SAVE):
        warn("no saved wallpaper (collar may never have locked this session)")
        return
    try:
        with open(WALLPAPER_SAVE, "r", encoding="utf-8") as f:
            original = f.read().strip()
    except Exception as e:
        warn(f"could not read wallpaper backup: {e}")
        return
    if not original or not os.path.exists(original):
        warn(f"saved wallpaper path no longer exists: {original}")
        return
    spi_setdeskwallpaper = 0x0014
    spif_updateinifile = 0x01
    spif_sendwininichange = 0x02
    ok = ctypes.windll.user32.SystemParametersInfoW(
        spi_setdeskwallpaper, 0, original, spif_updateinifile | spif_sendwininichange
    )
    if ok:
        info(f"wallpaper restored: {original}")
    else:
        warn("wallpaper restore failed")


def remove_firewall_rule():
    step("Removing firewall rule")
    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f'Remove-NetFirewallRule -DisplayName "{FIREWALL_RULE}" -ErrorAction SilentlyContinue',
        ],
        capture_output=True,
    )
    subprocess.run(
        ["netsh", "advfirewall", "firewall", "delete", "rule", f"name={FIREWALL_RULE}"],
        capture_output=True,
    )
    info(f"removed firewall rule '{FIREWALL_RULE}'")


def remove_install_dir():
    step(f"Removing {INSTALL_DIR}")
    if not os.path.exists(INSTALL_DIR):
        return
    subprocess.run(["takeown", "/F", INSTALL_DIR, "/R", "/D", "Y"], capture_output=True)
    subprocess.run(["icacls", INSTALL_DIR, "/reset", "/T", "/C", "/Q"], capture_output=True)
    subprocess.run(["icacls", INSTALL_DIR, "/grant", "*S-1-5-32-544:(OI)(CI)F", "/T", "/C", "/Q"], capture_output=True)
    try:
        shutil.rmtree(INSTALL_DIR)
        info(f"removed {INSTALL_DIR}")
    except Exception as e:
        err(f"could not remove {INSTALL_DIR}: {e}")
        err("if a process is still holding files, reboot and re-run this script.")


def remove_appdata():
    step(f"Removing {APPDATA}")
    if not os.path.exists(APPDATA):
        return
    try:
        shutil.rmtree(APPDATA)
        info(f"removed {APPDATA}")
    except Exception as e:
        warn(f"could not remove {APPDATA}: {e}")


def remove_startup_shortcuts():
    step("Removing legacy Startup shortcuts")
    startup = os.path.join(os.environ.get("APPDATA", ""), r"Microsoft\Windows\Start Menu\Programs\Startup")
    if not os.path.isdir(startup):
        return
    for name in os.listdir(startup):
        if "focuslock" in name.lower() or "collar" in name.lower():
            try:
                os.remove(os.path.join(startup, name))
                info(f"removed startup entry: {name}")
            except Exception:
                pass


def main():
    skip_confirm = any(a.lower() in ("--yes", "-y") for a in sys.argv[1:])

    if not is_admin():
        elevate_and_relaunch()
        return

    print("\nFocusLock Desktop Collar — Safeword (local uninstall)")
    print("This will permanently remove the collar from this machine. It does NOT")
    print("notify the mesh (no signed Release order is sent). You are responsible")
    print("for any other devices still on the mesh.\n")

    if not skip_confirm:
        typed = input("Type 'release' to proceed: ")
        if typed.strip() != "release":
            warn("Aborted.")
            sys.exit(0)

    # Tasks come down first so the watchdog can't respawn the collar
    # mid-teardown. Wallpaper restore has to happen before the AppData wipe
    # since it reads the backup path from there.
    remove_scheduled_tasks()
    remove_registry_key()
    kill_processes()
    restore_wallpaper()
    remove_firewall_rule()
    remove_install_dir()
    remove_appdata()
    remove_startup_shortcuts()

    step("Done")
    print()
    print("The Desktop Collar is gone from this machine.")
    print()
    print("Reboot to be sure no stale in-memory state lingers.")
    print()
    input("Press Enter to close")


if __name__ == "__main__":
    main()

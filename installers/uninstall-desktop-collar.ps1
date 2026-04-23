# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
#
# Local teardown — removes the Windows Desktop Collar and all its traces
# without needing a signed Release order from the mesh. Use this when
# the Lion phone / private key is lost and you need to rescue a desktop.
#
# What it removes:
#   - Scheduled tasks: FocusLockCollar, FocusLockWatchdog
#   - Registry Run key: HKCU\...\Run\FocusLockCollar
#   - Firewall rule: "FocusLock Mesh (TCP 8435)"
#   - C:\focuslock (binaries — ACL-locked, takes ownership first)
#   - %APPDATA%\focuslock (vault keys, peers, orders, pairing codes,
#     wallpaper backup, web UI)
#   - Startup shortcuts under Start Menu\Programs\Startup matching
#     focuslock* / collar*
#
# Requires Administrator (auto-elevates). Restores the original
# wallpaper if one was saved. Safe to re-run.
#
# Run from an unrestricted PowerShell (or with -ExecutionPolicy Bypass):
#   powershell -ExecutionPolicy Bypass -File .\uninstall-desktop-collar.ps1

[CmdletBinding()]
param(
    [switch]$Yes  # skip the typed confirmation prompt
)

$ErrorActionPreference = 'Continue'

# ── Self-elevate ───────────────────────────────────────────────────
$currentUser = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
if (-not $currentUser.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Re-launching with Administrator rights..." -ForegroundColor Yellow
    $psArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', $PSCommandPath)
    if ($Yes) { $psArgs += '-Yes' }
    try {
        Start-Process -FilePath 'powershell.exe' -ArgumentList $psArgs -Verb RunAs -ErrorAction Stop
    } catch {
        Write-Host "[x] Elevation cancelled or failed: $_" -ForegroundColor Red
        Write-Host "    Re-run this script from an elevated PowerShell." -ForegroundColor Red
        exit 1
    }
    exit
}

function Step($msg) { Write-Host "`n=== $msg ===" -ForegroundColor Cyan }
function Info($msg) { Write-Host "[+] $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "[!] $msg" -ForegroundColor Yellow }
function Err ($msg) { Write-Host "[x] $msg" -ForegroundColor Red }

Write-Host "`nFocusLock Desktop Collar - Local Uninstall" -ForegroundColor White
Write-Host "This will permanently remove the collar from this machine. It does NOT"
Write-Host "notify the mesh (no signed Release order is sent). You are responsible"
Write-Host "for any other devices still on the mesh.`n"

if (-not $Yes) {
    $confirm = Read-Host "Type 'release' to proceed"
    if ($confirm -ne 'release') {
        Warn "Aborted."
        exit 0
    }
}

$AppData    = Join-Path $env:APPDATA 'focuslock'
$InstallDir = 'C:\focuslock'
$WpSave     = Join-Path $AppData 'original-wallpaper'

# ── 1. Capture wallpaper BEFORE we delete config ────────────────────
$origWallpaper = $null
if (Test-Path $WpSave) {
    try {
        $origWallpaper = (Get-Content $WpSave -ErrorAction Stop -Raw).Trim()
        if ($origWallpaper -and -not (Test-Path $origWallpaper)) {
            Warn "Saved wallpaper path no longer exists: $origWallpaper"
            $origWallpaper = $null
        }
    } catch {
        Warn "Could not read wallpaper backup: $_"
    }
}

# ── 2. Disable scheduled tasks BEFORE killing processes, so the
#       AtLogon trigger and the mutual watchdog can't respawn them.
Step "Removing scheduled tasks"
foreach ($task in 'FocusLockCollar', 'FocusLockWatchdog') {
    $existing = Get-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue
    if ($existing) {
        try {
            Disable-ScheduledTask -TaskName $task -ErrorAction SilentlyContinue | Out-Null
            Stop-ScheduledTask    -TaskName $task -ErrorAction SilentlyContinue
            Unregister-ScheduledTask -TaskName $task -Confirm:$false -ErrorAction Stop
            Info "removed task $task"
        } catch {
            Warn "failed to remove task $task : $_"
        }
    }
}

# ── 3. Remove HKCU Run key entry (autostart fallback). ──────────────
Step "Removing autostart registry entry"
$runKey = 'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Run'
$prop = Get-ItemProperty -Path $runKey -Name 'FocusLockCollar' -ErrorAction SilentlyContinue
if ($prop) {
    Remove-ItemProperty -Path $runKey -Name 'FocusLockCollar' -ErrorAction SilentlyContinue
    Info "removed HKCU Run\FocusLockCollar"
}

# ── 4. Kill processes. Loop a few times because the watchdog
#       may try to restart the collar before its task is gone
#       from kernel scheduling.
Step "Killing collar processes"
$exeNames = @(
    'FocusLock-Paired',
    'FocusLock',
    'FocusLock-Watchdog'
)
foreach ($attempt in 1..3) {
    $killed = $false
    foreach ($name in $exeNames) {
        Get-Process -Name $name -ErrorAction SilentlyContinue | ForEach-Object {
            try { $_.Kill(); $killed = $true; Info "killed $name (pid $($_.Id))" } catch {}
        }
    }
    # Also kill any pythonw running the desktop script (script-mode installs)
    Get-CimInstance Win32_Process -Filter "Name = 'pythonw.exe' OR Name = 'python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'focuslock-desktop-win\.py|watchdog-win\.pyw' } |
        ForEach-Object {
            try { Stop-Process -Id $_.ProcessId -Force; $killed = $true; Info "killed python pid $($_.ProcessId)" } catch {}
        }
    if (-not $killed) { break }
    Start-Sleep -Milliseconds 500
}

# ── 5. Restore wallpaper. ───────────────────────────────────────────
Step "Restoring wallpaper"
if ($origWallpaper) {
    try {
        if (-not ([System.Management.Automation.PSTypeName]'Wallpaper').Type) {
            Add-Type @"
using System;
using System.Runtime.InteropServices;
public class Wallpaper {
    [DllImport("user32.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool SystemParametersInfo(uint a, uint b, string c, uint d);
}
"@
        }
        # SPI_SETDESKWALLPAPER = 0x0014; SPIF_UPDATEINIFILE | SPIF_SENDWININICHANGE = 3
        [Wallpaper]::SystemParametersInfo(0x0014, 0, $origWallpaper, 3) | Out-Null
        Info "wallpaper restored: $origWallpaper"
    } catch {
        Warn "wallpaper restore failed: $_"
    }
} else {
    Warn "no saved wallpaper (collar may never have locked this session)"
}

# ── 6. Firewall rule. ───────────────────────────────────────────────
Step "Removing firewall rule"
$ruleName = 'FocusLock Mesh (TCP 8435)'
$rule = Get-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
if ($rule) {
    Remove-NetFirewallRule -DisplayName $ruleName -ErrorAction SilentlyContinue
    Info "removed firewall rule '$ruleName'"
}
# Fallback for systems without Get-NetFirewallRule
& netsh advfirewall firewall delete rule name="$ruleName" 2>$null | Out-Null

# ── 7. C:\focuslock — ACL-locked, take ownership first. ─────────────
Step "Removing $InstallDir"
if (Test-Path $InstallDir) {
    try {
        # Take ownership recursively, then grant Administrators full control,
        # then delete. The installer set:
        #   icacls /inheritance:r /grant SYSTEM:F /grant Administrators:F /grant <user>:RX
        # so as Administrator we already have F, but takeown + icacls /reset
        # is the bulletproof path in case ACLs were tightened further.
        & takeown /F $InstallDir /R /D Y 2>$null | Out-Null
        & icacls $InstallDir /reset /T /C /Q 2>$null | Out-Null
        & icacls $InstallDir /grant "*S-1-5-32-544:(OI)(CI)F" /T /C /Q 2>$null | Out-Null
        Remove-Item -Path $InstallDir -Recurse -Force -ErrorAction Stop
        Info "removed $InstallDir"
    } catch {
        Err "could not remove $InstallDir : $_"
        Err "if a process is still holding files, reboot and re-run this script."
    }
}

# ── 8. %APPDATA%\focuslock. ─────────────────────────────────────────
Step "Removing $AppData"
if (Test-Path $AppData) {
    try {
        Remove-Item -Path $AppData -Recurse -Force -ErrorAction Stop
        Info "removed $AppData"
    } catch {
        Warn "could not remove $AppData : $_"
    }
}

# ── 9. Legacy Startup shortcuts. ────────────────────────────────────
Step "Removing legacy Startup shortcuts"
$startup = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
if (Test-Path $startup) {
    Get-ChildItem -Path $startup -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '(?i)focuslock|collar' } |
        ForEach-Object {
            Remove-Item -Path $_.FullName -Force -ErrorAction SilentlyContinue
            Info "removed startup entry: $($_.Name)"
        }
}

# ── 10. Done. ───────────────────────────────────────────────────────
Step "Done"
Write-Host ""
Write-Host "The Desktop Collar is gone from this machine." -ForegroundColor Green
Write-Host ""
Write-Host "Reboot to be sure no stale in-memory state lingers."
Write-Host ""

if ($Host.Name -eq 'ConsoleHost') {
    Read-Host "Press Enter to close"
}

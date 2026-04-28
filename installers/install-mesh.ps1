# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
#
# Windows one-shot installer that pre-configures the desktop collar for a
# specific mesh and then runs FocusLock.exe (which self-installs to
# C:\focuslock and registers the scheduled tasks). Skips the post-install
# "where do I enter the homelab info" question entirely.
#
# Usage (from a regular PowerShell, no Admin needed for the config write):
#   powershell -ExecutionPolicy Bypass -File .\install-mesh.ps1 -MeshId <id> -MeshUrl https://your.relay.example
#
# (Default Windows execution policy is Restricted, which blocks .\install-mesh.ps1
#  outright. The Bypass invocation above runs this single file without changing
#  the system-wide policy.)
#
#   $env:FOCUSLOCK_MESH_ID = "<your-mesh-id>"
#   $env:FOCUSLOCK_MESH_URL = "https://your.relay.example"
#   powershell -ExecutionPolicy Bypass -File .\install-mesh.ps1
#   powershell -ExecutionPolicy Bypass -File .\install-mesh.ps1 -MeshId <id> -MeshUrl <url> -ResetKeys     # force fresh vault keypair
#   powershell -ExecutionPolicy Bypass -File .\install-mesh.ps1 -MeshId <id> -MeshUrl <url> -ExePath C:\path\to\FocusLock.exe
#
# Parameters:
#   -MeshId      mesh identifier (base64url) issued by your relay; required
#   -MeshUrl     full https URL of your relay; required
#   -NoNtfy      skip ntfy push subscription (defaults to subscribing)
#   -ResetKeys   force a fresh vault keypair (forces re-approval by Lion)
#   -ExePath     path to FocusLock.exe (defaults to one next to this script)
#
# The bundled exe path defaults to .\FocusLock.exe (drop the binary next to
# this script, or pass -ExePath). FocusLock.exe will elevate itself for the
# self_install step.

[CmdletBinding()]
param(
    [string]$MeshId  = $env:FOCUSLOCK_MESH_ID,
    [string]$MeshUrl = $env:FOCUSLOCK_MESH_URL,
    [switch]$NoNtfy,
    [switch]$ResetKeys,
    [string]$ExePath
)

if ([string]::IsNullOrEmpty($MeshId) -or [string]::IsNullOrEmpty($MeshUrl)) {
    Write-Error "error: -MeshId and -MeshUrl are required (or set FOCUSLOCK_MESH_ID + FOCUSLOCK_MESH_URL env vars)"
    Write-Host "Example: .\install-mesh.ps1 -MeshId <your-mesh-id> -MeshUrl https://your.relay.example"
    exit 2
}

# Resolve $ExePath relative to the script directory, not CWD. Common locations:
# next to this script (release bundle), or ..\dist\ (developer working tree).
if ([string]::IsNullOrEmpty($ExePath)) {
    $here = Split-Path -Parent $MyInvocation.MyCommand.Path
    $candidates = @(
        (Join-Path $here "FocusLock.exe"),
        (Join-Path $here "..\dist\FocusLock.exe"),
        (Join-Path $here "..\build\FocusLock.exe")
    )
    $ExePath = $candidates | Where-Object { Test-Path $_ } | Select-Object -First 1
    if ([string]::IsNullOrEmpty($ExePath)) { $ExePath = $candidates[0] }
}

$ConfigDir  = Join-Path $env:APPDATA "focuslock"
$ConfigFile = Join-Path $ConfigDir "config.json"

if (-not (Test-Path $ConfigDir)) {
    New-Item -ItemType Directory -Path $ConfigDir -Force | Out-Null
}

# Reset vault keypair only if explicitly asked. Default preserves the key so
# Lion's prior approval keeps working -- a fresh key forces a new pending
# request that needs Lion to approve again.
if ($ResetKeys) {
    # Build the path list as an explicit array -- PowerShell 5.1's parser chokes
    # on backtick-continued lines with parenthesized expressions bound
    # positionally to -Path, and reports it as an unclosed brace on the if.
    $keyFiles = @(
        (Join-Path $ConfigDir "node_privkey.pem"),
        (Join-Path $ConfigDir "node_pubkey.pem"),
        (Join-Path $ConfigDir "relay_privkey.pem"),
        (Join-Path $ConfigDir "relay_pubkey.pem")
    )
    Remove-Item -Path $keyFiles -Force -ErrorAction SilentlyContinue
    Write-Host "Vault keypair reset -- daemon will generate fresh keys + post a new register-node-request."
}

# Build the config object explicitly so JSON output is consistent regardless
# of PowerShell version's ConvertTo-Json quirks.
$config = [ordered]@{
    mesh_url      = $MeshUrl
    mesh_id       = $MeshId
    vault_mode    = $true
    mesh_port     = 8435
    poll_interval = 5
}
if (-not $NoNtfy) {
    $config.ntfy_enabled = $true
    $config.ntfy_server  = "https://ntfy.sh"
}

$json = $config | ConvertTo-Json -Depth 4
# Write UTF-8 without a BOM. PS 5.1's `Set-Content -Encoding UTF8` emits a BOM,
# which Python's json.load() rejects with "Expecting value: line 1 column 1".
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($ConfigFile, $json, $utf8NoBom)
Write-Host "Wrote $ConfigFile (mesh=$MeshId via $MeshUrl)"

# Hand off to FocusLock.exe -- it self-installs to C:\focuslock, registers the
# Task Scheduler entries, and starts the daemon. The exe will prompt for UAC
# elevation when it needs it.
if (-not (Test-Path $ExePath)) {
    Write-Error "FocusLock.exe not found at $ExePath. Pass -ExePath or place it next to this script."
    exit 1
}

Write-Host "Launching $ExePath ..."
Start-Process -FilePath $ExePath -Wait -Verb RunAs
Write-Host "Install complete. Approve the new device in Lion's Share when prompted."

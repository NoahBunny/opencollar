#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
"""
FocusLock Desktop — Windows Build Script
Builds FocusLock.exe (the desktop collar) and FocusLock-Watchdog.exe,
then signs them with a self-signed code signing certificate.

Run on Windows: python build-win.py
  Options:
    --skip-sign       Skip code signing
"""

import glob
import hashlib
import os
import shutil
import subprocess
import sys

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
# Use a temp build dir with no special chars — apostrophes in paths break PyInstaller .spec files
BUILD_ROOT = os.path.join(os.environ.get("TEMP", os.environ.get("TMP", SCRIPT_DIR)), "focuslock-build")
DIST_DIR = os.path.join(SCRIPT_DIR, "dist")


def check_platform():
    if sys.platform != "win32":
        print("ERROR: This build script must be run on Windows.")
        print("       PyInstaller cannot cross-compile to Windows from Linux.")
        sys.exit(1)


def check_python():
    v = sys.version_info
    if v.major < 3 or v.minor < 10:
        print(f"ERROR: Python 3.10+ required, got {v.major}.{v.minor}")
        sys.exit(1)
    print(f"  Python {v.major}.{v.minor}.{v.micro}")


def install_deps():
    print("  Installing dependencies...")
    required = ["pyinstaller", "pillow", "pystray"]
    optional = ["cryptography"]  # needs Rust on ARM64 — mesh module works without it

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade"] + required,
        check=True,
    )
    print(f"  {', '.join(required)} OK")

    for pkg in optional:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "--upgrade", pkg],
            capture_output=True,
        )
        if result.returncode == 0:
            print(f"  {pkg} OK")
        else:
            print(f"  {pkg} SKIPPED (optional — RSA verification will be disabled)")


def convert_icon():
    """Convert crown-gold.png to crown-gold.ico with multiple sizes for the exe icon."""
    png_path = os.path.join(SCRIPT_DIR, "icons", "crown-gold.png")
    if not os.path.exists(png_path):
        png_path = os.path.join(SCRIPT_DIR, "crown-gold.png")
    if not os.path.exists(png_path):
        print("  WARNING: crown-gold.png not found — .exe will use default icon")
        return None

    from PIL import Image

    os.makedirs(BUILD_ROOT, exist_ok=True)
    ico_path = os.path.join(BUILD_ROOT, "crown-gold.ico")
    img = Image.open(png_path)
    sizes = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    img.save(ico_path, format="ICO", sizes=sizes)
    print(f"  crown-gold.png -> crown-gold.ico ({','.join(str(s[0]) for s in sizes)})")
    return ico_path


def _stage_sources():
    """Copy all source files to BUILD_ROOT (no special chars in path)."""
    os.makedirs(BUILD_ROOT, exist_ok=True)
    # Main scripts (top-level)
    for f in [
        "focuslock-desktop-win.py",
        "focuslock_mesh.py",
        "focuslock_ntfy.py",
        "watchdog-win.pyw",
    ]:
        src = os.path.join(SCRIPT_DIR, f)
        if os.path.exists(src):
            shutil.copy2(src, BUILD_ROOT)
    # Shared Python modules (focuslock_http, _sync, _vault, _config, _transport, etc.)
    shared_dir = os.path.join(SCRIPT_DIR, "shared")
    if os.path.isdir(shared_dir):
        for fname in os.listdir(shared_dir):
            if fname.startswith("focuslock_") and fname.endswith(".py"):
                shutil.copy2(os.path.join(shared_dir, fname), BUILD_ROOT)
    # Icons
    for icon_name in ["collar-icon.png", "collar-icon-gold.png", "crown-gold.png", "crown-gray.png"]:
        for search_dir in [os.path.join(SCRIPT_DIR, "icons"), SCRIPT_DIR]:
            src = os.path.join(search_dir, icon_name)
            if os.path.exists(src):
                shutil.copy2(src, BUILD_ROOT)
                break
    # Font
    font = os.path.join(SCRIPT_DIR, "Lexend.ttf")
    if os.path.exists(font):
        shutil.copy2(font, BUILD_ROOT)


def pyinstaller_build(name, script, ico_path=None, windowed=True):
    """Run PyInstaller to produce a single .exe. Builds from BUILD_ROOT to avoid path issues."""
    _stage_sources()
    work_dir = os.path.join(BUILD_ROOT, "work")
    spec_dir = os.path.join(BUILD_ROOT, "spec")
    os.makedirs(DIST_DIR, exist_ok=True)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--onefile",
        "--noconfirm",
        "--clean",
        f"--name={name}",
        f"--distpath={DIST_DIR}",
        f"--workpath={work_dir}",
        f"--specpath={spec_dir}",
        "--hidden-import=pystray",
        "--hidden-import=focuslock_mesh",
        "--hidden-import=focuslock_http",
        "--hidden-import=focuslock_sync",
        "--hidden-import=focuslock_vault",
        "--hidden-import=focuslock_config",
        "--hidden-import=focuslock_transport",
        "--hidden-import=focuslock_ntfy",
    ]
    if windowed:
        cmd.append("--windowed")
    if ico_path and os.path.exists(ico_path):
        cmd.append(f"--icon={ico_path}")
        cmd.extend(["--add-data", f"{ico_path}{os.pathsep}."])

    # Bundle assets from the staged build dir
    assets = ["collar-icon.png", "collar-icon-gold.png", "crown-gold.png", "crown-gray.png", "Lexend.ttf"]
    # Include all staged focuslock_*.py modules so PyInstaller ships them alongside the exe
    for fname in os.listdir(BUILD_ROOT):
        if fname.startswith("focuslock_") and fname.endswith(".py"):
            assets.append(fname)
    for asset in assets:
        asset_path = os.path.join(BUILD_ROOT, asset)
        if os.path.exists(asset_path):
            cmd.extend(["--add-data", f"{asset_path}{os.pathsep}."])

    cmd.append(os.path.join(BUILD_ROOT, script))

    print(f"  Building {name}...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    exe_path = os.path.join(DIST_DIR, f"{name}.exe")
    if os.path.exists(exe_path):
        size_mb = os.path.getsize(exe_path) / (1024 * 1024)
        print(f"  {name}.exe ({size_mb:.1f} MB)")
    else:
        print(f"  ERROR building {name}:")
        print(result.stderr[-2000:] if result.stderr else result.stdout[-2000:])
        return None
    return exe_path


def find_signtool():
    """Find signtool.exe from Windows SDK."""
    candidates = glob.glob(r"C:\Program Files (x86)\Windows Kits\10\bin\*\x64\signtool.exe")
    if candidates:
        return sorted(candidates)[-1]  # Latest version
    # Also check PATH
    if shutil.which("signtool"):
        return "signtool"
    return None


def find_or_create_cert():
    """Find existing or create self-signed code signing cert for FocusLock."""
    # Check for existing cert
    result = subprocess.run(
        [
            "powershell",
            "-Command",
            "Get-ChildItem Cert:\\CurrentUser\\My -CodeSigningCert | "
            "Where-Object {$_.Subject -like '*CN=FocusLock*'} | "
            "Select-Object -First 1 -ExpandProperty Thumbprint",
        ],
        capture_output=True,
        text=True,
    )

    thumbprint = result.stdout.strip()
    if thumbprint:
        print(f"  Found existing cert: {thumbprint[:16]}...")
        return thumbprint

    # Create new self-signed cert
    print("  Creating self-signed code signing certificate (CN=FocusLock, O=FocusLock)...")
    result = subprocess.run(
        [
            "powershell",
            "-Command",
            "$cert = New-SelfSignedCertificate "
            "-Subject 'CN=FocusLock, O=FocusLock' "
            "-Type CodeSigningCert "
            "-CertStoreLocation Cert:\\CurrentUser\\My "
            "-NotAfter (Get-Date).AddYears(5) "
            "-KeyAlgorithm RSA -KeyLength 2048 -HashAlgorithm SHA256; "
            "$cert.Thumbprint",
        ],
        capture_output=True,
        text=True,
    )

    thumbprint = result.stdout.strip()
    if thumbprint:
        print(f"  Created cert: {thumbprint[:16]}...")
        return thumbprint

    print("  WARNING: Could not create certificate")
    return None


def sign_executables(skip=False):
    """Sign all .exe files in dist/ with FocusLock code signing certificate."""
    if skip:
        print("  Signing skipped (--skip-sign)")
        return

    signtool = find_signtool()
    if not signtool:
        print("  WARNING: signtool.exe not found. Install Windows SDK to enable signing.")
        print("           https://developer.microsoft.com/en-us/windows/downloads/windows-sdk/")
        return

    thumbprint = find_or_create_cert()
    if not thumbprint:
        return

    for exe in glob.glob(os.path.join(DIST_DIR, "*.exe")):
        name = os.path.basename(exe)
        result = subprocess.run(
            [
                signtool,
                "sign",
                "/fd",
                "SHA256",
                "/sha1",
                thumbprint,
                "/t",
                "http://timestamp.digicert.com",
                "/d",
                "FocusLock Desktop",
                exe,
            ],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print(f"  Signed: {name}")
        else:
            print(f"  WARNING: Failed to sign {name}: {result.stderr.strip()}")


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def print_summary():
    print("\n  Output:")
    for exe in sorted(glob.glob(os.path.join(DIST_DIR, "*.exe"))):
        name = os.path.basename(exe)
        size_mb = os.path.getsize(exe) / (1024 * 1024)
        sha = sha256_file(exe)
        print(f"    {name:<30} {size_mb:>6.1f} MB  SHA256: {sha[:16]}...")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="FocusLock Desktop — Windows Build")
    parser.add_argument("--skip-sign", action="store_true", help="Skip code signing")
    args = parser.parse_args()

    print()
    print("  FocusLock Desktop — Windows Build")
    print("  " + "=" * 36)

    print("\n[1/5] Checking environment...")
    check_platform()
    check_python()
    install_deps()

    print("\n[2/5] Converting icon...")
    ico_path = convert_icon()

    os.makedirs(DIST_DIR, exist_ok=True)

    print("\n[3/5] Building executables...")
    pyinstaller_build("FocusLock", "focuslock-desktop-win.py", ico_path)
    pyinstaller_build("FocusLock-Watchdog", "watchdog-win.pyw", ico_path)

    print("\n[4/5] Signing executables...")
    sign_executables(skip=args.skip_sign)

    print("\n[5/5] Done!")
    print_summary()
    print()


if __name__ == "__main__":
    main()

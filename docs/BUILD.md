# Build Guide

How to build every artifact in this repo from source. No CI required.

---

## Prerequisites

| Toolchain | Version | Used for |
|-----------|---------|----------|
| **JDK**   | 17+     | Android compile (javac) |
| **Android SDK** | build-tools 35.0.0, platform android-36 | aapt2, d8, apksigner, zipalign |
| **Python** | 3.10–3.12 | Desktop collars, server, tests |
| **uv** (recommended) | latest | Fast dependency management |
| **PyInstaller** | 6.x | Windows .exe build (Windows only) |

Install Android SDK components without Android Studio:

```bash
# Set ANDROID_SDK to your install root
export ANDROID_SDK=/opt/android-sdk
sdkmanager 'build-tools;35.0.0' 'platforms;android-36'
```

---

## Android APKs (3 modules)

Each module has its own `build.sh`. They share a uniform interface.

```bash
# Debug build — auto-generates a debug.keystore on first run
cd android/slave      && bash build.sh   # The Collar             → focuslock-signed.apk
cd android/companion  && bash build.sh   # Bunny Tasker           → bunnytasker-signed.apk
cd android/controller && bash build.sh   # Lion's Share           → focusctl-signed.apk
```

Build pipeline (per module):

```
res/  ──aapt2 compile──→ compiled.zip
                              │
AndroidManifest ──aapt2 link──┴──→ unaligned.apk
src/*.java     ──javac──→ classes/ ──d8──→ classes.dex
                                              │
unaligned.apk + classes.dex  ──zip──→ app.apk ──zipalign──→ aligned.apk ──apksigner──→ *-signed.apk
```

### Release builds (signed with your release key)

Pass `--release` to use a real keystore instead of the auto-generated debug one. The build fails fast if the required env vars are missing.

```bash
export FOCUSLOCK_KEYSTORE=/path/to/release.keystore
export FOCUSLOCK_KEYSTORE_PASS='your-keystore-password'
export FOCUSLOCK_KEY_ALIAS=focuslock   # optional; default per-module: focuslock | bunnytasker | focusctl

cd android/slave && bash build.sh --release
```

### Generate a release keystore

```bash
keytool -genkey -v \
  -keystore ~/.config/focuslock/release.keystore \
  -alias focuslock \
  -keyalg RSA -keysize 4096 -validity 36500 \
  -storepass 'your-keystore-password' \
  -keypass   'your-keystore-password' \
  -dname 'CN=Your Name,O=Your Org,L=City,ST=State,C=US'
```

Store this keystore offline and back it up. **Losing it means you can never sign updates** for an installed app — users have to uninstall and reinstall, losing all data and pairings.

### Verify signature

```bash
$ANDROID_SDK/build-tools/35.0.0/apksigner verify --verbose android/slave/focuslock-signed.apk
```

### Verify the signing cert fingerprint

When users sideload a release APK, they should verify the cert SHA-256 matches the one published for the release.

Print a local APK's cert fingerprint:

```bash
$ANDROID_SDK/build-tools/35.0.0/apksigner verify --print-certs android/slave/focuslock-signed.apk | grep 'SHA-256'
```

The release workflow publishes the production cert fingerprints alongside `SHA256SUMS.txt` on every tagged release. If your sideloaded APK's cert doesn't match the published fingerprint, **do not install it** — the build is not authentic. Once an APK is installed, Android refuses to update it with an APK signed by a different cert, so a mismatch also blocks the upgrade path to an authentic build. You would have to uninstall first, losing pairing + data.

Users switching from debug builds to release builds must uninstall first for the same reason. This is a one-time cost at v1.0.0 adoption.

---

## Linux desktop collar

No build step — runs as a Python script under systemd. See `docs/SELF-HOSTING.md` for the install path or just run:

```bash
bash installers/install-desktop-collar.sh
```

Dependencies are installed at runtime by the installer.

---

## Windows desktop collar (.exe + watchdog)

`build-win.py` wraps PyInstaller. **Must run on Windows** — PyInstaller cannot cross-compile.

```powershell
python build-win.py                    # Builds FocusLock.exe + FocusLock-Watchdog.exe
python build-win.py --skip-sign        # Skip self-signed code signing (CI default)
```

Output: `dist/FocusLock.exe`, `dist/FocusLock-Watchdog.exe`.

### Reproducibility

The release CI workflow sets `SOURCE_DATE_EPOCH` from the git commit timestamp before running PyInstaller. Local builds inherit whatever wall-clock time you ran them at.

Known sources of non-determinism in PyInstaller output:

- CPython bytecode `pyc` headers (mitigated by `SOURCE_DATE_EPOCH`)
- Bootloader compile time (only varies if you rebuild the bootloader; pip-installed PyInstaller ships pre-built)
- File-tree iteration order on case-insensitive filesystems

Two consecutive `--skip-sign` builds with `SOURCE_DATE_EPOCH` pinned should be byte-identical on the same Windows runner image.

### ARM64 caveat

The `cryptography` wheel needs Rust to build from source on Windows ARM64 (Snapdragon). `build-win.py` marks `cryptography` as optional; without it, the desktop collar still works but RSA signature verification of inbound vault blobs is skipped. **Don't deploy without it on a real bunny's machine.**

---

## Server (`focuslock-mail.py`)

No build step. Install dependencies and run:

```bash
pip install --require-hashes -r requirements.txt
python3 focuslock-mail.py
```

For production, use the installer:

```bash
bash installers/homelab-setup.sh
```

See `docs/SELF-HOSTING.md` for systemd setup, TLS, and DNS.

---

## Tests

```bash
uv run --with pytest --with pytest-cov --with cryptography pytest tests/
```

Or with plain pip:

```bash
pip install pytest pytest-cov cryptography
pytest tests/
```

---

## Lint and type-check

```bash
uvx ruff check .
uvx ruff format --check .
uvx mypy shared
```

These mirror what CI runs in `.github/workflows/ci.yml`.

---

## CI vs local

The GitHub Actions workflows in `.github/workflows/` build the same artifacts on every push and tag. To reproduce a CI run locally:

| CI job | Local equivalent |
|--------|------------------|
| `lint` | `uvx ruff check . && uvx ruff format --check . && uvx mypy shared` |
| `test (py3.10 / 3.11 / 3.12)` | `pytest tests/` (pick your own Python) |
| `build-android` | `cd android/{slave,companion,controller} && bash build.sh` |
| `build-win` | `python build-win.py --skip-sign` (Windows only) |
| `release` | tag a commit with `vX.Y.Z` and push |

Release artifacts (signed APKs + Windows EXEs + `SHA256SUMS.txt`) attach to the GitHub Release automatically.

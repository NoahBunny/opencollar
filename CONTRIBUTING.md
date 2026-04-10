# Contributing to FocusLock

Thanks for your interest in contributing to FocusLock.

## Getting Started

1. Fork the repository
2. Copy `config.example.json` to `config.json` and set your mesh PIN
3. For server deployments, copy `config.env.example` to `config.env`

## Development Setup

### Desktop Collar (Linux)
- Python 3.10+
- GTK4 + WebKit2 (`sudo dnf install gtk4-devel webkitgtk6.0-devel` on Fedora)
- Optional: `cryptography` package for RSA signature verification

### Desktop Collar (Windows)
- Python 3.10+
- PyInstaller for building executables
- Run `python build-win.py --help` for build options

### Android Apps
- JDK 17+
- Android SDK build-tools (aapt2, d8, zipalign, apksigner)
- android.jar (API 34)
- No Gradle — manual build pipeline

### Homelab Server
- Python 3.10+
- ADB (for bridge)
- Optional: Ollama with minicpm-v (for photo task verification)

## Code Style

- Python: PEP 8, with reasonable line length (120 chars)
- Shell: shellcheck-clean where practical
- No external package managers — stdlib-only Python (except `cryptography` for RSA)

## Submitting Changes

1. Create a feature branch
2. Make your changes
3. Test locally (desktop collar, mesh gossip, etc.)
4. Open a pull request with a clear description

## Architecture Overview

See `CLAUDE.md` for the full architecture, file layout, and mesh protocol documentation.

## License

By contributing, you agree that your contributions will be licensed under GPL-3.0-or-later.

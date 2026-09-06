#!/usr/bin/env bash
# setup-qa-env-garuda.sh
# ---------------------------------------------------------------------------
# One-shot dev-machine setup for the FULL programmatic QA of
# The Collar / Lion's Share / Bunny Tasker, on Garuda Linux (Arch-based;
# pacman + paru). The Fedora sibling is scripts/setup-waydroid-fedora.sh.
#
# It installs, idempotently, everything `make qa`, `make qa-android`,
# `make lint`, and `bash android/<app>/build.sh` need:
#
#   1. Python QA venv (.venv)      cryptography + pytest + pytest-cov +
#                                  pytest-timeout + ruff + mypy + playwright,
#                                  provisioned on Python 3.12 via uv (avoids
#                                  the py3.14 full-suite EBADF bug + missing
#                                  3.14 Playwright wheels) + the Chromium
#                                  browser for the web-UI QA layers.
#   2. JDK 17                      javac/java, for build.sh + build-conformance.sh
#   3. Android SDK                 build-tools 35.0.0 (base) + 36.0.0 (Tor A3
#                                  d8, Java-24 AAR bytecode) + platform
#                                  android-36 + platform-tools, via Google's
#                                  cmdline-tools/sdkmanager (no AUR).
#   4. waydroid (optional)         Android-in-a-container, for on-device QA.
#   5. A3 Tor toolchain (--tor)    tor-android AAR + jtorctl + bcprov -> ~/android-libs,
#                                  and a JDK 24 (the AAR is Java-24 bytecode, which a
#                                  JDK-17 javac cannot read) -> ~/.jdks. Off by default.
#
# It then wires ANDROID_SDK / ANDROID_HOME / JAVA_HOME / PATH into your fish +
# bash profiles so builds and QA scripts "just work" in new shells.
#
# Usage:
#   bash scripts/setup-qa-env-garuda.sh              # everything (waydroid best-effort)
#   bash scripts/setup-qa-env-garuda.sh --no-waydroid
#   bash scripts/setup-qa-env-garuda.sh --python-only
#   bash scripts/setup-qa-env-garuda.sh --sdk-only
#   bash scripts/setup-qa-env-garuda.sh --waydroid-only
#   bash scripts/setup-qa-env-garuda.sh --tor           # also fetch A3 Tor deps + JDK 24
#   bash scripts/setup-qa-env-garuda.sh --tor-only      # ONLY the Tor toolchain
#   bash scripts/setup-qa-env-garuda.sh --recreate-venv   # blow away + rebuild .venv
#   bash scripts/setup-qa-env-garuda.sh --check           # verify only; install nothing
#   bash scripts/setup-qa-env-garuda.sh --verify          # after install, run qa smoke
#
# Run it as your normal user (NOT root) — it calls sudo only where required.
# Re-running is safe: every step checks before acting.
# ---------------------------------------------------------------------------
set -uo pipefail

# ---- knobs (override via env) ---------------------------------------------
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
VENV_DIR="${VENV_DIR:-$REPO_DIR/.venv}"
PYTHON_VER="${PYTHON_VER:-3.12}"          # QA venv Python; "system" reuses /usr/bin/python3
ANDROID_SDK="${ANDROID_SDK:-$HOME/android-sdk}"
# build-tools 35.0.0 is the base target; 36.0.0 is needed to dex the Tor A3 AAR
# (ships Java-24 bytecode). Space-separated; first entry goes on PATH.
BUILD_TOOLS_VERS="${BUILD_TOOLS_VERS:-35.0.0 36.0.0}"
PLATFORM_VER="${PLATFORM_VER:-android-36}"
# Google's cmdline-tools bundle (fixed file despite the "_latest" name).
CMDLINE_TOOLS_URL="${CMDLINE_TOOLS_URL:-https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip}"
JDK_PKG="${JDK_PKG:-jdk17-openjdk}"
JAVA_17_HOME="${JAVA_17_HOME:-/usr/lib/jvm/java-17-openjdk}"
# Optional A3 Tor toolchain. The tor-android AAR ships Java-24 bytecode, so the
# Tor-on build needs BOTH build-tools 36 (d8) AND a JDK >= 24 (javac) — a JDK-17
# javac cannot even read the AAR's TorService.class. See docs/TOR-ONION.md.
ANDROID_LIBS="${ANDROID_LIBS:-$HOME/android-libs}"
JDK24_DIR="${JDK24_DIR:-$HOME/.jdks}"
TOR_AAR_VER="${TOR_AAR_VER:-0.4.9.9.1}"
JTORCTL_VER="${JTORCTL_VER:-0.4.5.7}"
BCPROV_VER="${BCPROV_VER:-1.78.1}"
# Hard runtime dep of the tor-android AAR's TorService — see setup_tor().
LBM_VER="${LBM_VER:-1.1.0}"
JDK24_URL="${JDK24_URL:-https://api.adoptium.net/v3/binary/latest/24/ga/linux/x64/jdk/hotspot/normal/eclipse}"

# ---- flags ----------------------------------------------------------------
DO_PYTHON=1; DO_SDK=1; DO_WAYDROID=1; DO_TOR=0
CHECK_ONLY=0; RECREATE_VENV=0; VERIFY=0
for arg in "$@"; do
    case "$arg" in
        --python-only)   DO_SDK=0; DO_WAYDROID=0 ;;
        --sdk-only)      DO_PYTHON=0; DO_WAYDROID=0 ;;
        --waydroid-only) DO_PYTHON=0; DO_SDK=0 ;;
        --tor-only)      DO_PYTHON=0; DO_SDK=0; DO_WAYDROID=0; DO_TOR=1 ;;
        --tor)           DO_TOR=1 ;;   # add the A3 Tor toolchain to the run
        --no-waydroid)   DO_WAYDROID=0 ;;
        --recreate-venv) RECREATE_VENV=1 ;;
        --check)         CHECK_ONLY=1 ;;
        --verify)        VERIFY=1 ;;
        -h|--help) grep -E '^#( |$)' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

c_b="\033[1m"; c_g="\033[1;32m"; c_y="\033[1;33m"; c_r="\033[1;31m"; c_0="\033[0m"
say()  { echo -e "${c_b}==>${c_0} $*"; }
ok()   { echo -e "${c_g}  ok:${c_0} $*"; }
warn() { echo -e "${c_y}  !! ${c_0} $*"; }
err()  { echo -e "${c_r}  XX ${c_0} $*" >&2; }

if [ "$(id -u)" = "0" ]; then
    err "Run as your normal user, not root. The script uses sudo only when needed."
    exit 1
fi
if ! command -v pacman &>/dev/null; then
    err "This script targets Garuda/Arch (pacman not found). On Fedora use scripts/setup-waydroid-fedora.sh."
    exit 1
fi

pac() { sudo pacman -S --needed --noconfirm "$@"; }

need_sudo() {
    [ -n "${SUDO_PROMPTED:-}" ] && return 0
    say "Some steps need sudo — you may be prompted."
    sudo -v || return 1
    export SUDO_PROMPTED=1
}

# ===========================================================================
# PART 1 — Python QA venv (pytest / coverage / ruff / mypy / playwright)
# ===========================================================================
setup_python() {
    say "uv (Python + venv manager)"
    if command -v uv &>/dev/null; then
        ok "uv present: $(uv --version)"
    elif [ "$CHECK_ONLY" = 1 ]; then
        warn "uv missing (install: sudo pacman -S uv)"
    else
        need_sudo || return 1
        pac uv || { err "could not install uv"; return 1; }
        ok "uv installed"
    fi

    # Chromium runtime libs for the Playwright web-UI QA layers. Arch's
    # Playwright can't `install-deps`, so pull the shared libs via pacman.
    if [ "$CHECK_ONLY" != 1 ]; then
        say "Chromium runtime libs (for Playwright web-UI QA)"
        need_sudo || return 1
        pac nss nspr libxcb libxkbcommon libxcomposite libxdamage libxrandr \
            libxfixes libxext at-spi2-core alsa-lib mesa cairo pango \
            gdk-pixbuf2 || warn "some chromium deps failed (headless may still work)"
    fi

    local uv_bin; uv_bin="$(command -v uv || true)"
    if [ "$CHECK_ONLY" = 1 ]; then
        if [ -x "$VENV_DIR/bin/python" ]; then
            ok "venv: $("$VENV_DIR/bin/python" --version 2>&1)"
            for m in pytest ruff mypy playwright; do
                "$VENV_DIR/bin/python" -c "import $m" 2>/dev/null && ok "py:$m" || warn "py:$m missing"
            done
        else
            warn "venv missing at $VENV_DIR"
        fi
        return 0
    fi
    [ -n "$uv_bin" ] || { err "uv unavailable; cannot build venv"; return 1; }

    # (Re)create the venv. The existing one is a dead uv/3.14 venv (interpreter
    # gone), so a broken or --recreate-venv venv is rebuilt from scratch.
    local venv_ok=0
    [ -x "$VENV_DIR/bin/python" ] && "$VENV_DIR/bin/python" -c 'import sys' 2>/dev/null && venv_ok=1
    if [ "$RECREATE_VENV" = 1 ] || [ "$venv_ok" = 0 ]; then
        say "Creating QA venv at $VENV_DIR (Python $PYTHON_VER)"
        rm -rf "$VENV_DIR"
        if [ "$PYTHON_VER" = "system" ]; then
            uv venv "$VENV_DIR" || { err "uv venv failed"; return 1; }
        else
            uv python install "$PYTHON_VER" || warn "uv python install $PYTHON_VER returned non-zero"
            uv venv --python "$PYTHON_VER" "$VENV_DIR" || { err "uv venv failed"; return 1; }
        fi
        ok "venv: $("$VENV_DIR/bin/python" --version 2>&1)"
    else
        ok "venv present: $("$VENV_DIR/bin/python" --version 2>&1)"
    fi

    say "Installing QA deps (cryptography, pytest[-cov,-timeout], ruff, mypy, playwright)"
    # -e '.[dev]' pulls the test+lint stack from pyproject; add playwright for
    # the web-UI QA layers (qa_wizard_browser.py / qa_index_browser.py).
    if ! VIRTUAL_ENV="$VENV_DIR" uv pip install --python "$VENV_DIR/bin/python" \
            -e "$REPO_DIR[dev]" playwright; then
        err "dependency install failed"; return 1
    fi
    ok "QA deps installed"

    say "Playwright Chromium browser"
    if "$VENV_DIR/bin/python" -m playwright install chromium; then
        ok "chromium installed"
    else
        warn "playwright chromium install failed — the qa-wizard/qa-index layers"
        warn "will be unavailable; the rest of the QA suite is unaffected."
    fi
}

# ===========================================================================
# PART 2 — JDK + Android SDK toolchain
# ===========================================================================
resolve_java_home() {
    if [ -x "$JAVA_17_HOME/bin/javac" ]; then
        JAVA_HOME_RESOLVED="$JAVA_17_HOME"
    elif command -v javac &>/dev/null; then
        JAVA_HOME_RESOLVED="$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")"
    else
        JAVA_HOME_RESOLVED=""
    fi
    export JAVA_HOME="$JAVA_HOME_RESOLVED"
}

setup_sdk() {
    say "JDK 17 (for javac / sdkmanager)"
    if [ -x "$JAVA_17_HOME/bin/javac" ]; then
        ok "jdk17 present: $("$JAVA_17_HOME/bin/javac" -version 2>&1)"
    elif [ "$CHECK_ONLY" = 1 ]; then
        command -v javac &>/dev/null && ok "javac: $(javac -version 2>&1)" || warn "javac missing (pkg: $JDK_PKG)"
    else
        need_sudo || return 1
        pac "$JDK_PKG" || { err "could not install $JDK_PKG"; return 1; }
        ok "installed $JDK_PKG"
    fi
    resolve_java_home
    [ -n "$JAVA_HOME" ] && ok "JAVA_HOME=$JAVA_HOME" || warn "JAVA_HOME unresolved"

    if [ "$CHECK_ONLY" != 1 ]; then
        say "unzip / curl (for cmdline-tools)"
        need_sudo || return 1
        pac unzip curl jq android-tools
    fi

    say "Android cmdline-tools -> $ANDROID_SDK"
    local cl_dir="$ANDROID_SDK/cmdline-tools/latest"
    if [ -x "$cl_dir/bin/sdkmanager" ]; then
        ok "cmdline-tools already installed"
    elif [ "$CHECK_ONLY" = 1 ]; then
        warn "cmdline-tools missing at $cl_dir"
    else
        mkdir -p "$ANDROID_SDK/cmdline-tools"
        local tmp; tmp="$(mktemp -d)"
        say "downloading $(basename "$CMDLINE_TOOLS_URL")"
        if ! curl -fL --retry 3 -o "$tmp/cmdline-tools.zip" "$CMDLINE_TOOLS_URL"; then
            err "download failed: $CMDLINE_TOOLS_URL"; rm -rf "$tmp"; return 1
        fi
        unzip -q "$tmp/cmdline-tools.zip" -d "$tmp"
        rm -rf "$cl_dir"; mkdir -p "$cl_dir"
        mv "$tmp/cmdline-tools/"* "$cl_dir/"
        rm -rf "$tmp"
        ok "cmdline-tools installed"
    fi

    local sdkm="$cl_dir/bin/sdkmanager"
    if [ "$CHECK_ONLY" != 1 ] && [ -x "$sdkm" ]; then
        say "Accepting SDK licenses"
        yes 2>/dev/null | JAVA_HOME="$JAVA_HOME" "$sdkm" --sdk_root="$ANDROID_SDK" --licenses >/dev/null 2>&1 || true

        local pkgs=("platforms;$PLATFORM_VER" "platform-tools")
        local v; for v in $BUILD_TOOLS_VERS; do pkgs+=("build-tools;$v"); done
        say "sdkmanager: ${pkgs[*]}"
        if ! JAVA_HOME="$JAVA_HOME" "$sdkm" --sdk_root="$ANDROID_SDK" "${pkgs[@]}"; then
            err "sdkmanager install failed"; return 1
        fi
    fi

    # Verify the exact tools build.sh looks for, for every requested build-tools.
    local missing=0 v bt
    for v in $BUILD_TOOLS_VERS; do
        bt="$ANDROID_SDK/build-tools/$v"
        local t
        for t in aapt2 d8 apksigner zipalign; do
            [ -f "$bt/$t" ] && ok "$v/$t" || { warn "missing $bt/$t"; missing=1; }
        done
    done
    [ -f "$ANDROID_SDK/platforms/$PLATFORM_VER/android.jar" ] \
        && ok "android.jar ($PLATFORM_VER)" || { warn "missing android.jar"; missing=1; }

    [ "$CHECK_ONLY" = 1 ] && return 0
    [ "$missing" = 0 ] || { err "SDK verification found missing tools"; return 1; }
    write_profiles
}

write_profiles() {
    say "Wiring ANDROID_SDK / JAVA_HOME / PATH into shell profiles"
    local first_bt; first_bt="$(echo $BUILD_TOOLS_VERS | awk '{print $1}')"
    local bt="$ANDROID_SDK/build-tools/$first_bt"
    local cl="$ANDROID_SDK/cmdline-tools/latest/bin"
    local pt="$ANDROID_SDK/platform-tools"

    # fish (this repo's interactive shell)
    local fconf="$HOME/.config/fish/conf.d/focuslock-android.fish"
    mkdir -p "$(dirname "$fconf")"
    cat > "$fconf" <<EOF
# Auto-generated by setup-qa-env-garuda.sh — FocusLock Android toolchain
set -gx ANDROID_SDK "$ANDROID_SDK"
set -gx ANDROID_HOME "$ANDROID_SDK"
set -gx JAVA_HOME "$JAVA_HOME"
fish_add_path "$bt" "$cl" "$pt" "$JAVA_HOME/bin"
EOF
    ok "fish: $fconf"

    # bash — the canonical env file the handoffs reference.
    local bsnip="$HOME/.config/focuslock-android.env.sh"
    cat > "$bsnip" <<EOF
# Auto-generated by setup-qa-env-garuda.sh — source from ~/.bashrc
export ANDROID_SDK="$ANDROID_SDK"
export ANDROID_HOME="$ANDROID_SDK"
export JAVA_HOME="$JAVA_HOME"
export PATH="$bt:$cl:$pt:\$JAVA_HOME/bin:\$PATH"
EOF
    if ! grep -q 'focuslock-android.env.sh' "$HOME/.bashrc" 2>/dev/null; then
        echo "[ -f \"$bsnip\" ] && source \"$bsnip\"" >> "$HOME/.bashrc"
    fi
    ok "bash: $bsnip (sourced from ~/.bashrc)"
    warn "For Tor A3 builds, run build.sh with FOCUSLOCK_BUILD_TOOLS=36.0.0."
}

# ===========================================================================
# PART 3 — waydroid (on-device QA)
# ===========================================================================
setup_waydroid() {
    if [ "$CHECK_ONLY" = 1 ]; then
        command -v waydroid &>/dev/null && ok "waydroid: $(command -v waydroid)" || warn "waydroid missing"
        [ -f /var/lib/waydroid/images/system.img ] && ok "waydroid image present" || warn "waydroid not initialized"
        return 0
    fi

    if [ "${XDG_SESSION_TYPE:-}" != "wayland" ]; then
        warn "Session is '${XDG_SESSION_TYPE:-unknown}', not Wayland."
        warn "waydroid needs Wayland to show UI (cage works headless for adb-only QA)."
    fi

    say "Checking for the binder kernel interface"
    local have_binder=0
    if [ -e /dev/binder ] || [ -d /dev/binderfs ] || [ -e /dev/anbox-binder ]; then
        have_binder=1
    elif grep -qw binder /proc/filesystems; then
        have_binder=1; ok "binderfs is built into the kernel"
    elif need_sudo && { sudo modprobe binder_linux devices="binder,hwbinder,vndbinder" 2>/dev/null \
                        || sudo modprobe binder_linux 2>/dev/null; }; then
        have_binder=1
    fi
    if [ "$have_binder" = 1 ]; then
        ok "binder available"
    else
        warn "binder_linux not available on this kernel ($(uname -r))."
        cat <<'EOF'
       waydroid needs the binder driver. On Garuda/Arch with the linux-zen
       kernel, install the matching module + headers:

         sudo pacman -S --needed linux-zen-headers   # headers for YOUR kernel
         paru -S binder_linux-dkms                    # AUR DKMS binder module
         sudo modprobe binder_linux

       A reboot may be required. Re-run with --waydroid-only once binder loads.
       (The SDK/Python steps do NOT need binder and are already done.)
EOF
        warn "Skipping waydroid init until binder is present."
        return 0
    fi

    say "Installing waydroid"
    if command -v waydroid &>/dev/null; then
        ok "waydroid already installed"
    else
        need_sudo || return 1
        pac waydroid || { err "waydroid package install failed."; return 1; }
        ok "waydroid installed"
    fi

    say "Enabling waydroid-container service"
    sudo systemctl enable --now waydroid-container 2>/dev/null || warn "could not enable waydroid-container (starts on demand)"

    if [ -f /var/lib/waydroid/images/system.img ]; then
        ok "waydroid already initialized"
    else
        say "Initializing waydroid system image (downloads ~1GB; VANILLA, no GAPPS)"
        sudo waydroid init -f -c https://ota.waydro.id/system -v https://ota.waydro.id/vendor || true
        if [ -f /var/lib/waydroid/images/system.img ]; then
            ok "waydroid initialized"
        else
            warn "waydroid init did not produce a system image. Run manually:"
            warn "  sudo waydroid init -f -c https://ota.waydro.id/system -v https://ota.waydro.id/vendor"
        fi
    fi
}

# ===========================================================================
# PART 3b — optional A3 Tor toolchain (deps + JDK 24 for javac)
# ===========================================================================
setup_tor() {
    say "A3 Tor deps -> $ANDROID_LIBS"
    mkdir -p "$ANDROID_LIBS"
    local GP="https://repo1.maven.org/maven2/info/guardianproject"
    local MC="https://repo1.maven.org/maven2"
    local aar="$ANDROID_LIBS/tor-android-$TOR_AAR_VER.aar"
    local jtc="$ANDROID_LIBS/jtorctl-$JTORCTL_VER.jar"
    local bcp="$ANDROID_LIBS/bcprov-jdk18on-$BCPROV_VER.jar"
    # tor-android AAR lives on Guardian's gpmaven (raw GitHub); jtorctl + bcprov
    # are on Maven Central. (The gpmaven jtorctl path 404s — Central has it.)
    local GPRAW="https://raw.githubusercontent.com/guardianproject/gpmaven/master/info/guardianproject"
    fetch() { # url dest
        [ -f "$2" ] && unzip -tq "$2" >/dev/null 2>&1 && { ok "$(basename "$2") cached"; return 0; }
        [ "$CHECK_ONLY" = 1 ] && { warn "$(basename "$2") missing"; return 0; }
        curl -fL --retry 3 -o "$2" "$1" && unzip -tq "$2" >/dev/null 2>&1 \
            && ok "$(basename "$2")" || { err "fetch/verify failed: $2"; return 1; }
    }
    fetch "$GPRAW/tor-android/$TOR_AAR_VER/tor-android-$TOR_AAR_VER.aar" "$aar" || return 1
    fetch "$GP/jtorctl/$JTORCTL_VER/jtorctl-$JTORCTL_VER.jar" "$jtc" || return 1
    fetch "$MC/org/bouncycastle/bcprov-jdk18on/$BCPROV_VER/bcprov-jdk18on-$BCPROV_VER.jar" "$bcp" || return 1

    # androidx.localbroadcastmanager: org.torproject.jni.TorService.onCreate()
    # calls broadcastStatus(), which needs LocalBroadcastManager. Without it the
    # first Tor start dies with NoClassDefFoundError and takes the whole app
    # down (verified on-device 2026-08-07). Ships as an AAR on Google's maven,
    # so pull classes.jar out of it — the build wants a plain jar.
    local lbm="$ANDROID_LIBS/localbroadcastmanager-$LBM_VER.jar"
    if [ -f "$lbm" ] && unzip -tq "$lbm" >/dev/null 2>&1; then
        ok "localbroadcastmanager-$LBM_VER.jar cached"
    elif [ "$CHECK_ONLY" = 1 ]; then
        warn "localbroadcastmanager-$LBM_VER.jar missing (Tor would crash on first start)"
    else
        local lbmtmp; lbmtmp="$(mktemp -d)"
        if curl -fL --retry 3 -o "$lbmtmp/lbm.aar" \
              "https://dl.google.com/dl/android/maven2/androidx/localbroadcastmanager/localbroadcastmanager/$LBM_VER/localbroadcastmanager-$LBM_VER.aar" \
           && unzip -o -q "$lbmtmp/lbm.aar" -d "$lbmtmp" && cp "$lbmtmp/classes.jar" "$lbm"; then
            rm -rf "$lbmtmp"; ok "localbroadcastmanager-$LBM_VER.jar"
        else
            rm -rf "$lbmtmp"; err "localbroadcastmanager fetch failed"; return 1
        fi
    fi

    say "JDK 24 (javac must read the AAR's Java-24 bytecode)"
    local jdk24; jdk24="$(ls -d "$JDK24_DIR"/jdk-24* 2>/dev/null | head -1)"
    if [ -n "$jdk24" ] && [ -x "$jdk24/bin/javac" ]; then
        ok "JDK 24 present: $("$jdk24/bin/javac" -version 2>&1)"
    elif [ "$CHECK_ONLY" = 1 ]; then
        warn "JDK 24 missing under $JDK24_DIR (needed for Tor javac)"
    else
        mkdir -p "$JDK24_DIR"; local tmp; tmp="$(mktemp -d)"
        say "downloading Temurin JDK 24 (~130MB)"
        if curl -fL --retry 3 -o "$tmp/jdk24.tar.gz" "$JDK24_URL" && tar xzf "$tmp/jdk24.tar.gz" -C "$JDK24_DIR"; then
            rm -rf "$tmp"
            jdk24="$(ls -d "$JDK24_DIR"/jdk-24* 2>/dev/null | head -1)"
            ok "JDK 24: $("$jdk24/bin/javac" -version 2>&1)"
        else
            rm -rf "$tmp"; err "JDK 24 download/extract failed"; return 1
        fi
    fi

    # Write a Tor-build env file the user sources before a Tor build.
    if [ "$CHECK_ONLY" != 1 ] && [ -n "$jdk24" ]; then
        local tenv="$HOME/.config/focuslock-tor.env.sh"
        cat > "$tenv" <<EOF
# Auto-generated by setup-qa-env-garuda.sh — source before a Tor-ON build.
# Overrides JAVA_HOME to JDK 24 (reads Java-24 AAR) + build-tools 36 (dexes it).
export JAVA_HOME="$jdk24"
export PATH="\$JAVA_HOME/bin:$ANDROID_SDK/build-tools/36.0.0:\$PATH"
export FOCUSLOCK_BUILD_TOOLS=36.0.0
export FOCUSLOCK_TOR_AAR="$aar"
export FOCUSLOCK_JTORCTL_JAR="$jtc"
export FOCUSLOCK_BCPROV_JAR="$bcp"
export FOCUSLOCK_LBM_JAR="$lbm"
# Then: bash android/slave/build.sh && bash android/controller/build.sh
EOF
        ok "Tor build env: $tenv (source it, then run the Tor build)"
    fi
}

# ===========================================================================
# PART 4 — optional post-install smoke
# ===========================================================================
run_verify() {
    say "Verify: pytest subset + qa-android (best-effort)"
    resolve_java_home
    ( cd "$REPO_DIR" && "$VENV_DIR/bin/python" -m pytest tests/test_mesh.py -q ) \
        && ok "pytest smoke green" || warn "pytest smoke failed (see output)"
    if [ -x "$JAVA_HOME/bin/javac" ]; then
        ( cd "$REPO_DIR" && JAVA_HOME="$JAVA_HOME" PATH="$JAVA_HOME/bin:$PATH" \
            bash android/build-conformance.sh ) \
            && ok "android conformance build green" || warn "conformance build failed (see output)"
    else
        warn "javac unresolved; skipping qa-android smoke"
    fi
}

# ===========================================================================
main() {
    local rc=0
    [ "$CHECK_ONLY" = 1 ] && say "CHECK-ONLY mode — verifying, installing nothing."

    if [ "$DO_PYTHON" = 1 ];   then setup_python   || { err "Python QA setup incomplete"; rc=1; }; fi
    if [ "$DO_SDK" = 1 ];      then setup_sdk      || { err "SDK setup incomplete";        rc=1; }; fi
    if [ "$DO_TOR" = 1 ];      then setup_tor      || { err "Tor toolchain incomplete";    rc=1; }; fi
    if [ "$DO_WAYDROID" = 1 ]; then setup_waydroid || { err "waydroid setup incomplete";   rc=1; }; fi

    [ "$VERIFY" = 1 ] && [ "$CHECK_ONLY" != 1 ] && run_verify

    echo
    say "Summary"
    [ -x "$VENV_DIR/bin/python" ] && ok "venv: $("$VENV_DIR/bin/python" --version 2>&1) ($VENV_DIR)" || warn "venv: missing"
    resolve_java_home
    [ -x "$JAVA_HOME/bin/javac" ] && ok "javac: $JAVA_HOME/bin/javac" || warn "javac: missing"
    local first_bt; first_bt="$(echo $BUILD_TOOLS_VERS | awk '{print $1}')"
    [ -f "$ANDROID_SDK/build-tools/$first_bt/aapt2" ] && ok "build-tools: $ANDROID_SDK/build-tools" || warn "build-tools: missing"
    command -v waydroid &>/dev/null && ok "waydroid: $(command -v waydroid)" || warn "waydroid: missing/skipped"
    echo
    if [ "$CHECK_ONLY" = 1 ]; then
        say "Check complete."
    elif [ "$rc" = 0 ]; then
        say "Done. Open a NEW shell (so profiles load), then from $REPO_DIR:"
        echo "    make lint            # ruff"
        echo "    make qa-android      # JVM unit tests + Java<->Python conformance"
        echo "    make qa              # full: staging relay + pytest + runner + web-UI"
        echo "    bash android/slave/build.sh && bash android/companion/build.sh && bash android/controller/build.sh"
        echo "    # on-device:  waydroid session start  (then  waydroid show-full-ui)"
        warn "py3.14 note: the QA venv is Python $PYTHON_VER on purpose (3.14 has the"
        warn "full-suite EBADF isolation bug + missing Playwright wheels)."
    else
        warn "Finished with warnings — see notes above. Re-run after addressing them."
    fi
    return $rc
}
main

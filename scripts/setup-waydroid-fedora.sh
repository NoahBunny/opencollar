#!/usr/bin/env bash
# setup-waydroid-fedora.sh
# ---------------------------------------------------------------------------
# One-shot dev-machine setup for building the FocusLock APKs and QA-testing
# them in waydroid, on Fedora (tested target: Fedora 43, incl. Surface kernels).
#
# It installs, idempotently:
#   1. JDK 17                      (javac, for the build.sh pipeline)
#   2. Android SDK build-tools     (aapt2 / d8 / apksigner / zipalign)
#      + platform android-36       (android.jar) via cmdline-tools/sdkmanager
#   3. waydroid                    (Android-in-a-container, for on-device QA)
#
# It then wires ANDROID_SDK / JAVA_HOME / PATH into your fish + bash profiles so
# `bash android/<app>/build.sh` and the QA scripts "just work" in new shells.
#
# Usage:
#   bash scripts/setup-waydroid-fedora.sh            # do everything
#   bash scripts/setup-waydroid-fedora.sh --sdk-only # toolchain only, skip waydroid
#   bash scripts/setup-waydroid-fedora.sh --waydroid-only
#
# Run it as your normal user (NOT root) — it calls sudo only where required.
# Re-running is safe: every step checks before acting.
# ---------------------------------------------------------------------------
set -uo pipefail

# ---- knobs (override via env) ---------------------------------------------
ANDROID_SDK="${ANDROID_SDK:-$HOME/android-sdk}"
BUILD_TOOLS_VER="${BUILD_TOOLS_VER:-35.0.0}"
PLATFORM_VER="${PLATFORM_VER:-android-36}"
# Google's cmdline-tools bundle (fixed file despite the "_latest" name).
CMDLINE_TOOLS_URL="${CMDLINE_TOOLS_URL:-https://dl.google.com/android/repository/commandlinetools-linux-11076708_latest.zip}"
JDK_PKG="${JDK_PKG:-java-17-openjdk-devel}"

DO_SDK=1
DO_WAYDROID=1
for arg in "$@"; do
    case "$arg" in
        --sdk-only)      DO_WAYDROID=0 ;;
        --waydroid-only) DO_SDK=0 ;;
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
if ! command -v dnf &>/dev/null; then
    err "This script targets Fedora (dnf not found)."
    exit 1
fi
[ -n "${SUDO_PROMPTED:-}" ] || { say "Some steps need sudo — you may be prompted."; sudo -v || exit 1; export SUDO_PROMPTED=1; }

# The linux-surface repo can 404 (e.g. its f44 repodata isn't published yet),
# which makes dnf transactions noisy/flaky. Disable it for OUR installs only.
DNF_FLAGS=(--disablerepo=linux-surface)

# Pick a JDK >=17 — the build only needs javac, and any JDK >=17 compiles the
# project's `-source 17`. Prefer the major version already installed (so we
# match the present JRE), then fall back through a list. Fedora 44 ships 25/21,
# so the hard-coded "java-17-openjdk-devel" name no longer exists.
detect_jdk_pkgs() {
    local major=""
    if command -v java >/dev/null 2>&1; then
        major="$(java -version 2>&1 | sed -n 's/.*version "\([0-9][0-9]*\).*/\1/p' | head -1)"
    fi
    [ -n "$major" ] && printf '%s\n' "java-${major}-openjdk-devel"
    printf '%s\n' java-25-openjdk-devel java-21-openjdk-devel java-17-openjdk-devel java-latest-openjdk-devel java-devel
}

# ===========================================================================
# PART 1 — JDK + Android SDK toolchain
# ===========================================================================
setup_sdk() {
    say "JDK (>=17, for javac)"
    if command -v javac &>/dev/null && javac -version 2>&1 | grep -qE ' (1[7-9]|[2-9][0-9])'; then
        ok "javac present: $(javac -version 2>&1)"
    else
        local jdk_ok=""
        local pkg
        for pkg in $(detect_jdk_pkgs); do
            say "installing $pkg"
            if sudo dnf install -y "${DNF_FLAGS[@]}" "$pkg" 2>/dev/null; then jdk_ok="$pkg"; break; fi
        done
        if [ -z "$jdk_ok" ]; then
            err "could not install a JDK devel package (tried: $(detect_jdk_pkgs | tr '\n' ' '))"
            return 1
        fi
        ok "installed $jdk_ok"
    fi
    # Resolve a JAVA_HOME for sdkmanager + the profile snippet.
    JAVA_HOME_RESOLVED="$(dirname "$(dirname "$(readlink -f "$(command -v javac)")")")"
    export JAVA_HOME="$JAVA_HOME_RESOLVED"
    ok "JAVA_HOME=$JAVA_HOME"

    say "unzip (needed for cmdline-tools)"
    command -v unzip &>/dev/null || sudo dnf install -y "${DNF_FLAGS[@]}" unzip

    say "Android cmdline-tools -> $ANDROID_SDK"
    local cl_dir="$ANDROID_SDK/cmdline-tools/latest"
    if [ -x "$cl_dir/bin/sdkmanager" ]; then
        ok "cmdline-tools already installed"
    else
        mkdir -p "$ANDROID_SDK/cmdline-tools"
        local tmp; tmp="$(mktemp -d)"
        say "downloading $(basename "$CMDLINE_TOOLS_URL")"
        if ! curl -fL --retry 3 -o "$tmp/cmdline-tools.zip" "$CMDLINE_TOOLS_URL"; then
            err "download failed: $CMDLINE_TOOLS_URL"; rm -rf "$tmp"; return 1
        fi
        unzip -q "$tmp/cmdline-tools.zip" -d "$tmp"
        # The zip unpacks to cmdline-tools/* ; sdkmanager wants it under .../latest
        rm -rf "$cl_dir"; mkdir -p "$cl_dir"
        mv "$tmp/cmdline-tools/"* "$cl_dir/"
        rm -rf "$tmp"
        ok "cmdline-tools installed"
    fi

    local sdkm="$cl_dir/bin/sdkmanager"
    say "Accepting SDK licenses"
    yes 2>/dev/null | "$sdkm" --sdk_root="$ANDROID_SDK" --licenses >/dev/null 2>&1 || true

    say "build-tools;$BUILD_TOOLS_VER + platforms;$PLATFORM_VER"
    if ! "$sdkm" --sdk_root="$ANDROID_SDK" \
            "build-tools;$BUILD_TOOLS_VER" "platforms;$PLATFORM_VER" "platform-tools"; then
        err "sdkmanager install failed"; return 1
    fi

    # Verify the exact tools build.sh looks for.
    local bt="$ANDROID_SDK/build-tools/$BUILD_TOOLS_VER" missing=0
    for t in aapt2 d8 apksigner zipalign; do
        [ -f "$bt/$t" ] && ok "$t" || { err "missing $bt/$t"; missing=1; }
    done
    [ -f "$ANDROID_SDK/platforms/$PLATFORM_VER/android.jar" ] \
        && ok "android.jar" || { err "missing android.jar"; missing=1; }
    [ "$missing" = 0 ] || return 1

    write_profiles
}

write_profiles() {
    say "Wiring ANDROID_SDK / JAVA_HOME / PATH into shell profiles"
    local bt="$ANDROID_SDK/build-tools/$BUILD_TOOLS_VER"
    local cl="$ANDROID_SDK/cmdline-tools/latest/bin"
    local pt="$ANDROID_SDK/platform-tools"

    # fish (this repo's interactive shell + what the harness uses)
    local fconf="$HOME/.config/fish/conf.d/focuslock-android.fish"
    mkdir -p "$(dirname "$fconf")"
    cat > "$fconf" <<EOF
# Auto-generated by setup-waydroid-fedora.sh — FocusLock Android toolchain
set -gx ANDROID_SDK "$ANDROID_SDK"
set -gx ANDROID_HOME "$ANDROID_SDK"
set -gx JAVA_HOME "$JAVA_HOME"
fish_add_path "$bt" "$cl" "$pt" "$JAVA_HOME/bin"
EOF
    ok "fish: $fconf"

    # bash (in case build.sh / qa scripts are run from bash)
    local bsnip="$HOME/.config/focuslock-android.env.sh"
    cat > "$bsnip" <<EOF
# Auto-generated by setup-waydroid-fedora.sh — source from ~/.bashrc
export ANDROID_SDK="$ANDROID_SDK"
export ANDROID_HOME="$ANDROID_SDK"
export JAVA_HOME="$JAVA_HOME"
export PATH="$bt:$cl:$pt:\$JAVA_HOME/bin:\$PATH"
EOF
    if ! grep -q 'focuslock-android.env.sh' "$HOME/.bashrc" 2>/dev/null; then
        echo "[ -f \"$bsnip\" ] && source \"$bsnip\"" >> "$HOME/.bashrc"
    fi
    ok "bash: $bsnip (sourced from ~/.bashrc)"
}

# ===========================================================================
# PART 2 — waydroid
# ===========================================================================
setup_waydroid() {
    # --- 2a. session type sanity check ---
    if [ "${XDG_SESSION_TYPE:-}" != "wayland" ]; then
        warn "Session is '${XDG_SESSION_TYPE:-unknown}', not Wayland."
        warn "waydroid needs a Wayland session to show its UI (cage works headless for adb-only QA)."
    fi

    # --- 2b. binder kernel module (the usual Fedora blocker) ---
    say "Checking for the binder kernel interface"
    local have_binder=0
    if [ -e /dev/binder ] || [ -d /dev/binderfs ] || [ -e /dev/anbox-binder ]; then
        have_binder=1
    elif grep -qw binder /proc/filesystems; then
        # binderfs is BUILT INTO the kernel (CONFIG_ANDROID_BINDERFS=y, common on
        # the linux-surface kernel) — the binder nodes appear when binderfs is
        # mounted, which the waydroid-container service does itself. There is no
        # module to load (modprobe fails on a builtin), so this is NOT a problem.
        have_binder=1
        ok "binderfs is built into the kernel"
    elif sudo modprobe binder_linux devices="binder,hwbinder,vndbinder" 2>/dev/null \
         || sudo modprobe binder_linux 2>/dev/null; then
        have_binder=1
    fi
    if [ "$have_binder" = 1 ]; then
        ok "binder available"
    else
        warn "binder_linux is not available on this kernel ($(uname -r))."
        cat <<'EOF'
       waydroid needs the binder driver. On Fedora the usual fix is the
       anbox-modules DKMS package, built against your running kernel's headers:

         # 1) kernel headers that MATCH your kernel.
         #    Stock Fedora kernel:
         sudo dnf install -y kernel-devel-$(uname -r) dkms git
         #    Surface kernel (uname shows '*.surface'): install the matching
         #    headers from the linux-surface repo instead, e.g.
         #    sudo dnf install -y kernel-surface-devel-$(uname -r)

         # 2) build + load binder via DKMS
         git clone https://github.com/choff/anbox-modules /tmp/anbox-modules
         cd /tmp/anbox-modules && sudo ./INSTALL.sh
         sudo modprobe binder_linux ashmem_linux

       A reboot is often required after installing the modules. Re-run this
       script with --waydroid-only once binder loads. (The SDK/build steps
       above do NOT need binder and are already done.)
EOF
        warn "Skipping waydroid init until binder is present."
        return 0
    fi

    # --- 2c. install waydroid via the maintained Fedora COPR ---
    say "Installing waydroid"
    if command -v waydroid &>/dev/null; then
        ok "waydroid already installed"
    else
        sudo dnf install -y "${DNF_FLAGS[@]}" dnf-plugins-core || true
        # `copr enable` is interactive about the repo gpg the first time; -y covers it.
        sudo dnf copr enable -y aleasto/waydroid || warn "copr enable returned non-zero (may already be enabled)"
        if ! sudo dnf install -y "${DNF_FLAGS[@]}" waydroid; then
            err "waydroid package install failed."
            return 1
        fi
        ok "waydroid installed"
    fi

    # --- 2d. container service + image init ---
    say "Enabling waydroid-container service"
    sudo systemctl enable --now waydroid-container 2>/dev/null || warn "could not enable waydroid-container (will start on demand)"

    # Note: a system.img is the real "initialized" signal — `waydroid init` writes
    # waydroid.cfg even when it then errors out, and (waydroid 1.6.x) exits 0 on
    # that error, so checking the cfg or the exit code both give false positives.
    if [ -f /var/lib/waydroid/images/system.img ]; then
        ok "waydroid already initialized"
    else
        say "Initializing waydroid system image (downloads ~1GB; VANILLA, no GAPPS)"
        # This build ships no default OTA URLs — pass the official waydro.id ones.
        # -f overwrites any partial cfg left by a prior failed init.
        sudo waydroid init -f -c https://ota.waydro.id/system -v https://ota.waydro.id/vendor || true
        if [ -f /var/lib/waydroid/images/system.img ]; then
            ok "waydroid initialized"
        else
            warn "waydroid init did not produce a system image. Run manually:"
            warn "  sudo waydroid init -f -c https://ota.waydro.id/system -v https://ota.waydro.id/vendor"
            return 0
        fi
    fi
}

# ===========================================================================
main() {
    local rc=0
    if [ "$DO_SDK" = 1 ]; then
        setup_sdk || { err "SDK setup incomplete"; rc=1; }
    fi
    if [ "$DO_WAYDROID" = 1 ]; then
        setup_waydroid || { err "waydroid setup incomplete"; rc=1; }
    fi

    echo
    say "Summary"
    command -v javac &>/dev/null && ok "javac: $(command -v javac)" || warn "javac: missing"
    [ -f "$ANDROID_SDK/build-tools/$BUILD_TOOLS_VER/aapt2" ] && ok "build-tools: $ANDROID_SDK/build-tools/$BUILD_TOOLS_VER" || warn "build-tools: missing"
    command -v waydroid &>/dev/null && ok "waydroid: $(command -v waydroid)" || warn "waydroid: missing/skipped"
    echo
    if [ "$rc" = 0 ]; then
        say "Done. Open a NEW shell (so the profile snippet loads), then:"
        echo "    cd \"$(cd "$(dirname "$0")/.." && pwd)\""
        echo "    bash android/slave/build.sh && bash android/companion/build.sh && bash android/controller/build.sh"
        echo "    # then start waydroid:  waydroid session start  (and  waydroid show-full-ui)"
    else
        warn "Finished with warnings — see the notes above. Re-run after addressing them."
    fi
    return $rc
}
main

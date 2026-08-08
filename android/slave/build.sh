#!/usr/bin/env bash
# Build FocusLock slave APK
# Prerequisites: JDK 17+, Android SDK (build-tools 35.0.0, platform android-36)
#
# Usage:
#   ./build.sh                 # debug build, auto-generates debug.keystore
#   ./build.sh --release       # release build, requires:
#                              #   FOCUSLOCK_KEYSTORE      — path to release keystore
#                              #   FOCUSLOCK_KEYSTORE_PASS — keystore + key password
#                              #   FOCUSLOCK_KEY_ALIAS     — key alias (default: focuslock)
# See docs/BUILD.md for keystore generation.
set -e

RELEASE=0
for arg in "$@"; do
    case "$arg" in
        --release) RELEASE=1 ;;
        --debug)   RELEASE=0 ;;
        *) echo "Unknown arg: $arg" >&2; exit 2 ;;
    esac
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

# Auto-detect SDK paths
ANDROID_SDK="${ANDROID_SDK:-/tmp/android-sdk}"
# Default build-tools 35.0.0; override with FOCUSLOCK_BUILD_TOOLS. The optional
# A3 Tor AAR (tor-android 0.4.9.x) ships Java-24 bytecode, which only d8 from
# build-tools >= 36.0.0 can dex — set FOCUSLOCK_BUILD_TOOLS=36.0.0 for Tor builds.
BUILD_TOOLS="$ANDROID_SDK/build-tools/${FOCUSLOCK_BUILD_TOOLS:-35.0.0}"
ANDROID_JAR="$ANDROID_SDK/platforms/android-36/android.jar"

# Verify tools exist
for tool in aapt2 d8 apksigner zipalign; do
    if [ ! -f "$BUILD_TOOLS/$tool" ]; then
        echo "ERROR: $tool not found at $BUILD_TOOLS/$tool"
        echo "Set ANDROID_SDK to your SDK root, or install:"
        echo "  sdkmanager 'build-tools;35.0.0' 'platforms;android-36'"
        exit 1
    fi
done

export PATH="$BUILD_TOOLS:$PATH"

if ! command -v javac &>/dev/null; then
    echo "ERROR: javac not found. Install JDK 17+."
    exit 1
fi

# Clean
rm -rf classes compiled.zip unaligned.apk app.apk classes.zip classes.dex aligned.apk

if [ "$RELEASE" = "1" ]; then
    if [ -z "${FOCUSLOCK_KEYSTORE:-}" ] || [ ! -f "$FOCUSLOCK_KEYSTORE" ]; then
        echo "ERROR: --release requires FOCUSLOCK_KEYSTORE pointing to an existing keystore." >&2
        echo "       See docs/BUILD.md to generate one." >&2
        exit 1
    fi
    if [ -z "${FOCUSLOCK_KEYSTORE_PASS:-}" ]; then
        echo "ERROR: --release requires FOCUSLOCK_KEYSTORE_PASS (keystore + key password)." >&2
        exit 1
    fi
    KEYSTORE_PATH="$FOCUSLOCK_KEYSTORE"
    KEYSTORE_PASS="$FOCUSLOCK_KEYSTORE_PASS"
    KEY_ALIAS="${FOCUSLOCK_KEY_ALIAS:-focuslock}"
else
    # Generate debug keystore if missing OR if an existing one is stale
    # (e.g. left over from an older build.sh that used a different alias —
    # the 4e5d157 companion-alias fix is one historical example). Checking
    # the alias presence up-front catches this at generate-time instead of
    # surfacing later as a confusing apksigner "entry does not contain a
    # key" failure.
    if ! keytool -list -keystore debug.keystore -storepass android -alias focuslock &>/dev/null; then
        echo "Generating debug keystore..."
        rm -f debug.keystore
        keytool -genkey -v -keystore debug.keystore -alias focuslock \
            -keyalg RSA -keysize 2048 -validity 10000 \
            -storepass android -keypass android \
            -dname "CN=FocusLock,O=FocusLock,L=Unknown,ST=Unknown,C=US"
    fi
    KEYSTORE_PATH="debug.keystore"
    KEYSTORE_PASS="android"
    KEY_ALIAS="focuslock"
fi

# Optional embedded Tor (A3 — see docs/TOR-ONION.md). DEFAULT OFF: when
# FOCUSLOCK_TOR_AAR is unset, TOR_CP/TOR_DEX_INPUTS/TOR_LIB_DIR stay empty and
# the build is byte-identical to before.
TOR_CP=""
TOR_DEX_INPUTS=""
TOR_LIB_DIR=""
if [ -n "${FOCUSLOCK_TOR_AAR:-}" ]; then
    echo "Embedding Tor from $FOCUSLOCK_TOR_AAR ..."
    TOR_WORK="$(mktemp -d)"
    unzip -o -q "$FOCUSLOCK_TOR_AAR" -d "$TOR_WORK"          # AAR → classes.jar + jni/<abi>/*.so
    TOR_CP=":$TOR_WORK/classes.jar"
    TOR_DEX_INPUTS="$TOR_WORK/classes.jar"
    if [ -n "${FOCUSLOCK_JTORCTL_JAR:-}" ]; then
        TOR_CP="$TOR_CP:$FOCUSLOCK_JTORCTL_JAR"
        TOR_DEX_INPUTS="$TOR_DEX_INPUTS $FOCUSLOCK_JTORCTL_JAR"
    fi
    if [ -n "${FOCUSLOCK_BCPROV_JAR:-}" ]; then          # raw Ed25519/X25519 + SHA3
        TOR_CP="$TOR_CP:$FOCUSLOCK_BCPROV_JAR"
        TOR_DEX_INPUTS="$TOR_DEX_INPUTS $FOCUSLOCK_BCPROV_JAR"
    fi
    # androidx.localbroadcastmanager — a HARD runtime dependency of the AAR's
    # org.torproject.jni.TorService. It is not optional: TorService.onCreate()
    # calls broadcastStatus() immediately, which touches LocalBroadcastManager,
    # so without this jar the very first Tor start dies with
    # NoClassDefFoundError and takes the whole Collar process down with it —
    # then crash-loops, because the ntfy wake that triggered it is redelivered.
    # (Found on-device 2026-08-07; the Collar is the enforcement app, so a
    # crash-loop reachable from a publicly-writable ntfy topic is an escape
    # vector, not just a bug.) Gradle would have resolved this transitively;
    # this build has no dependency resolver, so it must be listed.
    if [ -n "${FOCUSLOCK_LBM_JAR:-}" ]; then
        TOR_CP="$TOR_CP:$FOCUSLOCK_LBM_JAR"
        TOR_DEX_INPUTS="$TOR_DEX_INPUTS $FOCUSLOCK_LBM_JAR"
    else
        echo "WARNING: FOCUSLOCK_LBM_JAR unset — Tor will crash on first start." >&2
    fi
    TOR_LIB_DIR="$TOR_WORK/jni"
fi

# Source set: the base app, plus the optional A3 Tor files ONLY when Tor is
# enabled. OnionKeys/OnionControl/TorManager import bcprov/tor/jtorctl (on the
# classpath solely when FOCUSLOCK_TOR_AAR is set), so they are excluded
# otherwise. TorHook has no Tor imports and is always compiled (reflection),
# keeping the default-off build green. find-based so the net/freehaven package
# is picked up (the old src/com/focuslock/*.java glob missed it).
if [ -n "${FOCUSLOCK_TOR_AAR:-}" ]; then
    SRCS="$(find src -name '*.java')"
else
    SRCS="$(find src -name '*.java' ! -name 'OnionKeys.java' ! -name 'OnionControl.java' ! -name 'TorManager.java')"
fi

echo "Compiling resources..."
aapt2 compile --dir res -o compiled.zip

echo "Linking..."
aapt2 link -o unaligned.apk -I "$ANDROID_JAR" --manifest AndroidManifest.xml \
    --java src compiled.zip --auto-add-overlay

echo "Compiling Java..."
javac -encoding UTF-8 -source 17 -target 17 -classpath "$ANDROID_JAR$TOR_CP" -d classes $SRCS

echo "Dexing..."
d8 --min-api 33 --output classes.zip $(find classes -name '*.class') $TOR_DEX_INPUTS

echo "Packaging..."
cp unaligned.apk app.apk
unzip -o classes.zip classes.dex
zip -u app.apk classes.dex
if [ -n "$TOR_LIB_DIR" ] && [ -d "$TOR_LIB_DIR" ]; then
    # Stage native libs under lib/<abi>/ and add uncompressed (-0) so Android can
    # mmap them directly; zipalign -p page-aligns them below.
    rm -rf libstage && mkdir -p libstage/lib
    for abidir in "$TOR_LIB_DIR"/*/; do
        abi="$(basename "$abidir")"
        mkdir -p "libstage/lib/$abi"
        cp "$abidir"*.so "libstage/lib/$abi/" 2>/dev/null || true
    done
    (cd libstage && zip -0 -r -q ../app.apk lib)
    rm -rf libstage
fi
zipalign -p -f 4 app.apk aligned.apk

echo "Signing ($([ "$RELEASE" = "1" ] && echo release || echo debug))..."
apksigner sign --ks "$KEYSTORE_PATH" --ks-pass "pass:$KEYSTORE_PASS" \
    --key-pass "pass:$KEYSTORE_PASS" --ks-key-alias "$KEY_ALIAS" \
    --out focuslock-signed.apk aligned.apk

# Cleanup intermediates
rm -f unaligned.apk app.apk classes.zip classes.dex aligned.apk compiled.zip
rm -rf classes

echo "Done: focuslock-signed.apk"

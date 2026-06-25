#!/usr/bin/env bash
# Build Lion's Share controller APK
# Prerequisites: JDK 17+, Android SDK (build-tools 35.0.0, platform android-36)
#
# Usage:
#   ./build.sh                 # debug build, auto-generates debug.keystore
#   ./build.sh --release       # release build, requires:
#                              #   FOCUSLOCK_KEYSTORE      — path to release keystore
#                              #   FOCUSLOCK_KEYSTORE_PASS — keystore + key password
#                              #   FOCUSLOCK_KEY_ALIAS     — key alias (default: focusctl)
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
    KEY_ALIAS="${FOCUSLOCK_KEY_ALIAS:-focusctl}"
else
    # Regenerate if missing OR stale (wrong alias from an older build.sh).
    # See the matching comment in android/slave/build.sh for context.
    if ! keytool -list -keystore debug.keystore -storepass android -alias focusctl &>/dev/null; then
        echo "Generating debug keystore..."
        rm -f debug.keystore
        keytool -genkey -v -keystore debug.keystore -alias focusctl \
            -keyalg RSA -keysize 2048 -validity 10000 \
            -storepass android -keypass android \
            -dname "CN=FocusCtl,O=FocusLock,L=Unknown,ST=Unknown,C=US"
    fi
    KEYSTORE_PATH="debug.keystore"
    KEYSTORE_PASS="android"
    KEY_ALIAS="focusctl"
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
    TOR_LIB_DIR="$TOR_WORK/jni"
fi

# Source set: base app + (Tor-enabled only) the A3 files. OnionKeys/OnionControl/
# TorManager import bcprov/tor/jtorctl (classpath only when FOCUSLOCK_TOR_AAR is
# set) so they are excluded otherwise; TorHook (reflection, no Tor imports) is
# always compiled. find-based so the net/freehaven package is included.
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
    --out focusctl-signed.apk aligned.apk

# Cleanup intermediates
rm -f unaligned.apk app.apk classes.zip classes.dex aligned.apk compiled.zip
rm -rf classes

echo "Done: focusctl-signed.apk"

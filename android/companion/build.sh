#!/usr/bin/env bash
# Build BunnyTasker companion APK
# Prerequisites: JDK 17+, Android SDK (build-tools 35.0.0, platform android-36)
#
# Usage:
#   ./build.sh                 # debug build, auto-generates debug.keystore
#   ./build.sh --release       # release build, requires:
#                              #   FOCUSLOCK_KEYSTORE      — path to release keystore
#                              #   FOCUSLOCK_KEYSTORE_PASS — keystore + key password
#                              #   FOCUSLOCK_KEY_ALIAS     — key alias (default: bunnytasker)
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
BUILD_TOOLS="$ANDROID_SDK/build-tools/35.0.0"
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
    KEY_ALIAS="${FOCUSLOCK_KEY_ALIAS:-bunnytasker}"
else
    # Generate debug keystore if missing (reuse slave keystore if available)
    if [ ! -f debug.keystore ]; then
        if [ -f ../slave/debug.keystore ]; then
            cp ../slave/debug.keystore .
        else
            echo "Generating debug keystore..."
            keytool -genkey -v -keystore debug.keystore -alias focuslock \
                -keyalg RSA -keysize 2048 -validity 10000 \
                -storepass android -keypass android \
                -dname "CN=BunnyTasker,O=FocusLock,L=Unknown,ST=Unknown,C=US"
        fi
    fi
    KEYSTORE_PATH="debug.keystore"
    KEYSTORE_PASS="android"
    KEY_ALIAS="bunnytasker"
fi

echo "Compiling resources..."
aapt2 compile --dir res -o compiled.zip

echo "Linking..."
aapt2 link -o unaligned.apk -I "$ANDROID_JAR" --manifest AndroidManifest.xml \
    --java src compiled.zip --auto-add-overlay

echo "Compiling Java..."
javac -encoding UTF-8 -source 17 -target 17 -classpath "$ANDROID_JAR" -d classes src/com/bunnytasker/*.java

echo "Dexing..."
d8 --min-api 26 --output classes.zip classes/com/bunnytasker/*.class

echo "Packaging..."
cp unaligned.apk app.apk
unzip -o classes.zip classes.dex
zip -u app.apk classes.dex
zipalign -f 4 app.apk aligned.apk

echo "Signing ($([ "$RELEASE" = "1" ] && echo release || echo debug))..."
apksigner sign --ks "$KEYSTORE_PATH" --ks-pass "pass:$KEYSTORE_PASS" \
    --key-pass "pass:$KEYSTORE_PASS" --ks-key-alias "$KEY_ALIAS" \
    --out bunnytasker-signed.apk aligned.apk

# Cleanup intermediates
rm -f unaligned.apk app.apk classes.zip classes.dex aligned.apk compiled.zip
rm -rf classes

echo "Done: bunnytasker-signed.apk"

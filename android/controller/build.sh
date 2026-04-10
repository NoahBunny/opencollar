#!/usr/bin/env bash
# Build Lion's Share controller APK
# Prerequisites: JDK 17+, Android SDK (build-tools 35.0.0, platform android-36)
set -e

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

# Generate keystore if missing
if [ ! -f debug.keystore ]; then
    echo "Generating debug keystore..."
    keytool -genkey -v -keystore debug.keystore -alias focusctl \
        -keyalg RSA -keysize 2048 -validity 10000 \
        -storepass android -keypass android \
        -dname "CN=FocusCtl,O=FocusLock,L=Unknown,ST=Unknown,C=US"
fi

echo "Compiling resources..."
aapt2 compile --dir res -o compiled.zip

echo "Linking..."
aapt2 link -o unaligned.apk -I "$ANDROID_JAR" --manifest AndroidManifest.xml \
    --java src compiled.zip --auto-add-overlay

echo "Compiling Java..."
javac -encoding UTF-8 -source 17 -target 17 -classpath "$ANDROID_JAR" -d classes src/com/focusctl/*.java

echo "Dexing..."
d8 --min-api 33 --output classes.zip classes/com/focusctl/*.class

echo "Packaging..."
cp unaligned.apk app.apk
unzip -o classes.zip classes.dex
zip -u app.apk classes.dex
zipalign -f 4 app.apk aligned.apk

echo "Signing..."
apksigner sign --ks debug.keystore --ks-pass pass:android \
    --key-pass pass:android --ks-key-alias focusctl --out focusctl-signed.apk aligned.apk

# Cleanup intermediates
rm -f unaligned.apk app.apk classes.zip classes.dex aligned.apk compiled.zip
rm -rf classes

echo "Done: focusctl-signed.apk"

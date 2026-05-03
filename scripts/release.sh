#!/usr/bin/env bash
# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2024-2026 The FocusLock Contributors
# FocusLock Release Script — builds all APKs and deploys everything
# Usage:
#   bash release.sh              — build only
#   bash release.sh deploy       — build + deploy to phones + homelab
#   bash release.sh deploy-phone — build + deploy to phones only
#   bash release.sh deploy-homelab — build + deploy to homelab only
#
# Environment variables (required for deploy):
#   BUNNY_DEVICE_1   — ADB address of first bunny device (e.g. 192.168.1.10:5555)
#   BUNNY_DEVICE_2   — ADB address of second bunny device (optional)
#   LION_DEVICE_IP   — IP of Lion's phone (for controller deploy, discovered from mesh)
#   HOMELAB_ADDR     — homelab SSH address
#   DEPLOY_USER      — SSH user (default: $USER)
set -e

TOOLS=~/android-build-tools
FOCUS=~/Nextcloud/Scripts/"Lion's Share + Bunny Tasker"
export ANDROID_ADB_SERVER_PORT=15037
export LD_LIBRARY_PATH=$TOOLS/android-14/lib64

# Device addresses — all from env, no defaults
BUNNY_DEV_1="${BUNNY_DEVICE_1:-}"
BUNNY_DEV_2="${BUNNY_DEVICE_2:-}"
LION_STATIC="${LION_DEVICE_IP:-}"
LION_DEV=""
HOMELAB="${HOMELAB_ADDR:-}"

ACTION="${1:-build}"

# ── Build APKs ──

build_apk() {
    local NAME="$1" DIR="$2" SOURCES="$3" OUTPUT="$4"
    echo "=== Building $NAME ==="
    cd "$DIR"
    rm -rf compiled_res classes classes.dex
    mkdir -p compiled_res classes

    # Compile resources
    $TOOLS/aapt2 compile --dir res -o compiled_res/

    # Link — generate R.java into src root (where package dir starts)
    $TOOLS/aapt2 link -o compiled.zip -I $TOOLS/android.jar \
        --manifest AndroidManifest.xml compiled_res/*.flat \
        --auto-add-overlay --java src

    # Compile Java — use sources.txt if available, otherwise find all .java
    local SRC_FILES
    if [ -f sources.txt ]; then
        SRC_FILES=$(cat sources.txt)
    else
        SRC_FILES=$(find src -name '*.java' -not -path '*/com/*/com/*' | tr '\n' ' ')
    fi
    javac --release 17 -cp $TOOLS/android.jar -d classes $SRC_FILES 2>&1 | grep -i error && exit 1 || true

    # Dex — find all class files (including inner classes and subpackages)
    java -cp $TOOLS/r8-new.jar com.android.tools.r8.D8 \
        --output . --lib $TOOLS/android.jar $(find classes -name '*.class')

    # Package
    cp compiled.zip unaligned.apk
    zip -j unaligned.apk classes.dex >/dev/null

    # Align + sign
    $TOOLS/zipalign -f 4 unaligned.apk aligned.apk
    $TOOLS/apksigner sign --ks debug.keystore \
        --ks-pass pass:android --key-pass pass:android \
        --out "$OUTPUT" aligned.apk 2>/dev/null

    echo "  -> $(basename "$OUTPUT") built"
}

echo ""
echo "FocusLock Release"
echo "================="
echo ""

mkdir -p "$FOCUS/apks"
build_apk "The Collar (FocusLock)" ~/focuslock-project "" "$FOCUS/apks/focuslock.apk"
build_apk "Lion's Share" ~/focusctl-app "" "$FOCUS/apks/focusctl.apk"
build_apk "Bunny Tasker" ~/bunnytasker-app "" "$FOCUS/apks/bunnytasker.apk"

echo ""
echo "=== All APKs built ==="
ls -lh "$FOCUS/apks"/*.apk
echo ""

# ── Deploy ──

if [[ "$ACTION" == "deploy" || "$ACTION" == "deploy-phone" ]]; then
    # Deploy collar apps to bunny devices
    for DEV_SPEC in "$BUNNY_DEV_1|Bunny-1" "$BUNNY_DEV_2|Bunny-2"; do
        IFS='|' read -r DEV_ADDR DEV_NAME <<< "$DEV_SPEC"
        [ -z "$DEV_ADDR" ] && continue
        echo "=== Deploying to $DEV_NAME ($DEV_ADDR) ==="
        if adb -s $DEV_ADDR shell echo ok 2>/dev/null | grep -q ok; then
            adb -s $DEV_ADDR install -r "$FOCUS/apks/focuslock.apk"
            adb -s $DEV_ADDR install -r "$FOCUS/apks/bunnytasker.apk"
            adb -s $DEV_ADDR shell am start-foreground-service -n com.focuslock/.ControlService 2>/dev/null || true
            adb -s $DEV_ADDR shell dpm set-active-admin com.bunnytasker/.AdminReceiver 2>/dev/null || true
            echo "  $DEV_NAME: done"
        else
            echo "  $DEV_NAME: not reachable, skipping"
        fi
    done

    # Deploy controller to Lion's phone — discover address from mesh
    echo "=== Deploying Lion's Share to Lion's phone ==="
    LION_IP=""
    # Try mesh controller registry on homelab
    if [ -n "$HOMELAB" ]; then
        CTRL_JSON=$(curl -s --connect-timeout 3 "http://$HOMELAB:8434/controller" 2>/dev/null)
        if echo "$CTRL_JSON" | grep -q tailscale_ip; then
            LION_IP=$(echo "$CTRL_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin).get('tailscale_ip',''))" 2>/dev/null)
            echo "  Mesh reports controller at: $LION_IP"
        fi
    fi
    [ -z "$LION_IP" ] && [ -n "$LION_STATIC" ] && LION_IP="$LION_STATIC" && echo "  Using configured IP: $LION_IP"

    if [ -z "$LION_IP" ]; then
        echo "  Lion's phone: no address configured (set LION_DEVICE_IP)"
    else
        # Find active ADB port by scanning common wireless debug ports
        LION_DEV=""
        for PORT_CANDIDATE in $(adb devices 2>/dev/null | grep "$LION_IP" | cut -f1); do
            LION_DEV="$PORT_CANDIDATE"
            break
        done
        if [ -z "$LION_DEV" ]; then
            # Try connecting — ADB wireless debug uses various ports
            echo "  Scanning for Lion's phone ADB..."
            for P in $(seq 37000 2 45999 | shuf | head -20); do
                if adb connect "$LION_IP:$P" 2>/dev/null | grep -q connected; then
                    LION_DEV="$LION_IP:$P"
                    echo "  Found at $LION_DEV"
                    break
                fi
                adb disconnect "$LION_IP:$P" 2>/dev/null
            done
        fi

        if [ -n "$LION_DEV" ] && adb -s "$LION_DEV" shell echo ok 2>/dev/null | grep -q ok; then
            adb -s "$LION_DEV" install -r "$FOCUS/apks/focusctl.apk"
            adb -s "$LION_DEV" shell am force-stop com.focusctl
            adb -s "$LION_DEV" shell am start -n com.focusctl/.MainActivity
            echo "  Lion's phone: done"
        else
            echo "  Lion's phone: not reachable (needs wireless debugging enabled)"
        fi
    fi
fi

if [[ "$ACTION" == "deploy" || "$ACTION" == "deploy-homelab" ]]; then
    if [ -z "$HOMELAB" ]; then
        echo "=== Homelab deploy SKIPPED (set HOMELAB_ADDR) ==="
    else
        echo "=== Deploying to homelab ($HOMELAB) ==="
        ssh "${DEPLOY_USER:-$USER}@$HOMELAB" "mkdir -p /tmp/focuslock-deploy/"
        scp "$FOCUS/focuslock-bridge.sh" "$FOCUS/focuslock-mail.py" \
            "$FOCUS/focuslock_mesh.py" \
            "$FOCUS/focuslock-desktop.py" "$FOCUS/icons/collar-icon.png" \
            "$FOCUS/installers/install-desktop-collar.sh" "$FOCUS/installers/install-standing-orders.sh" \
            "$FOCUS/apks/focuslock.apk" "$FOCUS/apks/focusctl.apk" "$FOCUS/apks/bunnytasker.apk" \
            "$FOCUS/docs/PRICE-LIST.md" \
            "${DEPLOY_USER:-$USER}@$HOMELAB:/tmp/focuslock-deploy/"
        ssh "${DEPLOY_USER:-$USER}@$HOMELAB" "bash -c 'sudo cp /tmp/focuslock-deploy/* /opt/focuslock/ 2>/dev/null; sudo chmod +x /opt/focuslock/*.sh /opt/focuslock/*.py; sudo systemctl restart focuslock-bridge focuslock-mail && echo Homelab: done'"
    fi
fi

echo ""
echo "=== Release complete ==="
echo ""

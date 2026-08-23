#!/usr/bin/env bash
# Build + run the JVM-level Android tests (no device, no Gradle, no android.jar).
#
# Compiles the controller's VaultCrypto against test-support shims for
# android.util.* (android.jar's are runtime stubs), plus the JUnit unit tests
# and the conformance CLI, then runs the JUnit suite. Emits the conformance-CLI
# command to build-conformance/cli-cmd.txt for tests/test_android_conformance.py
# (set ANDROID_CONFORMANCE_CLI to its contents).
#
# Prereqs: JDK 17+ (javac on PATH or $JAVA_HOME/bin). Jars (org.json + JUnit
# console-standalone) are fetched to a cache dir if absent — no network needed
# on reruns.
set -euo pipefail

JSON_VER="${JSON_VER:-20240303}"
JUNIT_VER="${JUNIT_VER:-1.11.4}"

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"      # .../android
REPO_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
BUILD="$REPO_DIR/build-conformance"
CACHE="${ANDROID_TEST_LIB:-${HOME}/.octc/test-lib}"

# Resolve javac.
if command -v javac >/dev/null 2>&1; then
    JAVAC="$(command -v javac)"; JAVA="$(command -v java)"
elif [ -n "${JAVA_HOME:-}" ] && [ -x "$JAVA_HOME/bin/javac" ]; then
    JAVAC="$JAVA_HOME/bin/javac"; JAVA="$JAVA_HOME/bin/java"
else
    echo "ERROR: javac not found. Install JDK 17+ or set JAVA_HOME." >&2
    exit 1
fi

mkdir -p "$CACHE"
JSON_JAR="$CACHE/json-$JSON_VER.jar"
JUNIT_JAR="$CACHE/junit-platform-console-standalone-$JUNIT_VER.jar"
[ -f "$JSON_JAR" ]  || curl -fsSL -o "$JSON_JAR"  "https://repo1.maven.org/maven2/org/json/json/$JSON_VER/json-$JSON_VER.jar"
[ -f "$JUNIT_JAR" ] || curl -fsSL -o "$JUNIT_JAR" "https://repo1.maven.org/maven2/org/junit/platform/junit-platform-console-standalone/$JUNIT_VER/junit-platform-console-standalone-$JUNIT_VER.jar"

rm -rf "$BUILD"; mkdir -p "$BUILD"

echo "== compiling Android src (controller + slave + companion) + shims + tests (JDK: $("$JAVAC" -version 2>&1)) =="
"$JAVAC" -encoding UTF-8 -d "$BUILD" -cp "$JSON_JAR:$JUNIT_JAR" \
    "$SCRIPT_DIR/test-support/android/util/Base64.java" \
    "$SCRIPT_DIR/test-support/android/util/Log.java" \
    "$SCRIPT_DIR/controller/src/com/focusctl/VaultCrypto.java" \
    "$SCRIPT_DIR/controller/src/com/focusctl/E2EEHelper.java" \
    "$SCRIPT_DIR/controller/src/com/focusctl/StatusCore.java" \
    "$SCRIPT_DIR/controller/src/com/focusctl/PollGate.java" \
    "$SCRIPT_DIR/controller/src/com/focusctl/JsonScan.java" \
    "$SCRIPT_DIR/controller/src/com/focusctl/OptimisticState.java" \
    "$SCRIPT_DIR/controller/test/com/focusctl/ConformanceCli.java" \
    "$SCRIPT_DIR/controller/test/com/focusctl/VaultCryptoTest.java" \
    "$SCRIPT_DIR/controller/test/com/focusctl/E2EEHelperTest.java" \
    "$SCRIPT_DIR/controller/test/com/focusctl/StatusCoreTest.java" \
    "$SCRIPT_DIR/controller/test/com/focusctl/PollGateTest.java" \
    "$SCRIPT_DIR/controller/test/com/focusctl/JsonScanTest.java" \
    "$SCRIPT_DIR/controller/test/com/focusctl/OptimisticStateTest.java" \
    "$SCRIPT_DIR/slave/src/com/focuslock/VaultCrypto.java" \
    "$SCRIPT_DIR/slave/src/com/focuslock/MeshOrderApply.java" \
    "$SCRIPT_DIR/slave/test/com/focuslock/ConformanceCli.java" \
    "$SCRIPT_DIR/slave/test/com/focuslock/MeshOrderApplyTest.java" \
    "$SCRIPT_DIR/test-support/android/content/ContentResolver.java" \
    "$SCRIPT_DIR/test-support/android/provider/Settings.java" \
    "$SCRIPT_DIR/slave/src/com/focuslock/SigVerifier.java" \
    "$SCRIPT_DIR/slave/test/com/focuslock/SigVerifierTest.java" \
    "$SCRIPT_DIR/companion/src/com/bunnytasker/PairingManager.java" \
    "$SCRIPT_DIR/companion/test/com/bunnytasker/PairingManagerTest.java"

echo "== running JUnit unit tests =="
"$JAVA" -jar "$JUNIT_JAR" execute \
    --class-path "$BUILD:$JSON_JAR" \
    --scan-class-path \
    --disable-banner \
    --details=tree \
    --fail-if-no-tests

# Emit the conformance-CLI commands (quoted for shlex) for the pytest layer.
printf '%s -cp "%s:%s" com.focusctl.ConformanceCli\n' "$JAVA" "$BUILD" "$JSON_JAR" > "$BUILD/cli-cmd.txt"
printf '%s -cp "%s:%s" com.focuslock.ConformanceCli\n' "$JAVA" "$BUILD" "$JSON_JAR" > "$BUILD/cli-cmd-slave.txt"
echo "== conformance CLIs ready =="
echo "   ANDROID_CONFORMANCE_CLI=\"\$(cat build-conformance/cli-cmd.txt)\""
echo "   ANDROID_CONFORMANCE_CLI_SLAVE=\"\$(cat build-conformance/cli-cmd-slave.txt)\""

# Android JVM tests + Java↔Python conformance

The Android apps reimplement, in Java, crypto/serialization that **must** match
the Python server byte-for-byte or signatures silently fail to verify. This is
the most dangerous class of bug in the ecosystem (a canonical-form drift between
Java and Python would make every Lion order/message fail verification, or — worse
for the Collar `/mesh/sync` fix — silently reject all gossip orders).

We test it **without a device, emulator, or Gradle** — the previous Waydroid UI
spike was shelved (see `UI-AUTOMATION-DECISION.md`). Two layers:

1. **JVM unit tests** (`android/controller/test/com/focusctl/VaultCryptoTest.java`)
   — JUnit 5, run on a plain JVM. Pins the `canonical_json` contract and the
   `signString` round trip.
2. **Java↔Python conformance** (`tests/test_android_conformance.py`) — a tiny
   Java CLI (`ConformanceCli.java`) runs the *real* `VaultCrypto`, and pytest
   compares it to / verifies it against the Python reference
   (`focuslock_mesh`, `shared/focuslock_vault`, `focuslock-mail.py`).

## How it works without android.jar

`android.jar` ships `android.util.Base64`/`Log` as stubs that throw
`RuntimeException("Stub!")` at runtime. Instead of Robolectric, we compile the
crypto class against **test-support shims** in `android/test-support/android/util/`
(real implementations delegating to `java.util.Base64` / a no-op logger). The
shims live only on the test classpath — they are **never** bundled in an APK
(the APK `build.sh` globs `src/com/**`, not `test/` or `test-support/`).

`org.json` is also a stub in android.jar, so the real `org.json:json` jar is on
the test classpath (fetched + cached by the build script).

## Run it

```bash
make qa-android
```

That runs `android/build-conformance.sh` (compile shims + VaultCrypto + tests,
run JUnit, emit the CLI command to `build-conformance/cli-cmd.txt`) then runs
`tests/test_android_conformance.py` with `ANDROID_CONFORMANCE_CLI` set.

Requires a JDK 17+ (`javac`). Jars are cached under `~/.octc/test-lib`
(override with `ANDROID_TEST_LIB`). In CI this runs in the `build-android` job,
which already has the JDK. Without a JDK, the pure-Python golden-vector half of
`test_android_conformance.py` still runs; the Java half skips.

## Why this gates the Collar `/mesh/sync` fix

`test_java_order_signature_accepted_by_mesh_verify` signs an orders document in
Java (`VaultCrypto`) and asserts the Python `focuslock_mesh.verify_signature`
accepts it — the **exact** verification the Collar's `handleMeshSync` fix must
mirror. If the Java and Python canonical forms ever drift, this test fails
*before* a release can brick gossip order delivery.

## Extending

Today this covers the controller `VaultCrypto`. Next increments (same pattern):
slave `SigVerifier`/`VaultCrypto`, companion `PairingManager`/`E2EEHelper`, and
extracting the message pipe-payload into a shared `MessagePayloads` class.

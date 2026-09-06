package android.content;

import java.util.HashMap;
import java.util.Map;

/**
 * Test-only shim for android.content.ContentResolver.
 *
 * Backs android.provider.Settings.Global with a per-instance in-memory store so
 * each {@code new ContentResolver()} is fully isolated — a fresh instance per
 * {@code @BeforeEach} gives each test its own Settings.Global namespace with no
 * cross-test leakage.
 *
 * PairingManager never calls a method on the ContentResolver directly; it only
 * passes it into Settings.Global.{get,put}String, so this holder needs no other
 * members. Never bundled in an APK — the real ContentResolver comes from
 * android.jar at APK build time; this file lives only on the JVM unit-test
 * classpath (see android/build-conformance.sh).
 */
public class ContentResolver {
    /** public so android.provider.Settings (a different package) can reach it. */
    public final Map<String, String> globalSettings = new HashMap<>();
}

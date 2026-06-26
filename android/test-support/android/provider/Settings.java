package android.provider;

import android.content.ContentResolver;

/**
 * Test-only shim for android.provider.Settings (Global namespace only).
 *
 * Reads/writes the per-instance map held by the test ContentResolver shim.
 *
 * getString MUST return null for absent keys — PairingManager branches on
 * {@code == null} in getPublicKey / isPaired / getLionKey / sign, and returning
 * "" instead would break getPublicKey's generate-if-missing logic. putString
 * returns boolean to match the real android.provider.Settings.Global signature.
 *
 * Never bundled in an APK — the real Settings comes from android.jar at APK
 * build time; this file lives only on the JVM unit-test classpath.
 */
public final class Settings {
    private Settings() {}

    public static final class Global {
        private Global() {}

        public static String getString(ContentResolver cr, String name) {
            return cr.globalSettings.get(name);
        }

        public static boolean putString(ContentResolver cr, String name, String value) {
            cr.globalSettings.put(name, value);
            return true;
        }
    }
}

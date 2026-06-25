package com.focusctl;

import android.content.Context;
import android.util.Log;

/**
 * Reflection bridge to the optional TorManager (com.focusctl.TorManager). No
 * Tor/bcprov imports, so it is always compiled and MainActivity can call A3
 * hooks unconditionally. When the APK was built without FOCUSLOCK_TOR_AAR,
 * TorManager isn't present → every method no-ops and Lion's Share keeps using
 * LAN/relay. SOCKS routing itself lives in MainActivity (default port), so the
 * hot HTTP path never reflects.
 */
public final class TorHook {
    private static final String TAG = "TorHook";
    private static volatile Object mgr;
    private static volatile boolean unavailable = false;

    private TorHook() {}

    private static Object mgr(Context ctx) {
        if (unavailable) return null;
        if (mgr != null) return mgr;
        synchronized (TorHook.class) {
            if (mgr != null) return mgr;
            try {
                Class<?> c = Class.forName("com.focusctl.TorManager");
                mgr = c.getConstructor(Context.class).newInstance(ctx.getApplicationContext());
                return mgr;
            } catch (Throwable t) {
                unavailable = true;  // Tor not built into this APK
                return null;
            }
        }
    }

    /** True if this APK bundles Tor. */
    public static boolean available(Context ctx) { return mgr(ctx) != null; }

    /** Lion's x25519 client-auth pubkey (base32) to POST as onion_auth_pub at
     *  pair time. "" when Tor isn't bundled (the field is then simply omitted). */
    public static String authPubBase32(Context ctx) {
        Object m = mgr(ctx);
        if (m == null) return "";
        try { return (String) m.getClass().getMethod("authPubBase32").invoke(m); }
        catch (Throwable t) { Log.w(TAG, "authPubBase32", t); return ""; }
    }

    /** Bring Tor up and authorize the Collar's onion (no ".onion" suffix).
     *  Blocking; call off the main thread. false → relay fallback. */
    public static boolean wakeAndAuthorize(Context ctx, String onionNoSuffix, long timeoutMs) {
        Object m = mgr(ctx);
        if (m == null) return false;
        try {
            return (Boolean) m.getClass()
                .getMethod("ensureTorUpAndAuthorized", String.class, long.class)
                .invoke(m, onionNoSuffix, timeoutMs);
        } catch (Throwable t) { Log.w(TAG, "wakeAndAuthorize", t); return false; }
    }
}

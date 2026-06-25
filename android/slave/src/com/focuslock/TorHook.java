package com.focuslock;

import android.content.Context;
import android.util.Log;

/**
 * Reflection bridge to the optional TorManager (com.focuslock.TorManager). This
 * class has NO Tor/bcprov imports, so it is always compiled and the base code
 * (ControlService) can call A3 hooks unconditionally. When the APK was built
 * without FOCUSLOCK_TOR_AAR, TorManager isn't present → Class.forName fails once,
 * every method becomes a no-op, and the Collar keeps serving over LAN/relay.
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
                Class<?> c = Class.forName("com.focuslock.TorManager");
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

    /** Provision the onion key + advertise the .onion (idempotent). Returns the
     *  .onion, or "" when Tor isn't bundled. */
    public static String provisionOnion(Context ctx) {
        Object m = mgr(ctx);
        if (m == null) return "";
        try { return (String) m.getClass().getMethod("provisionOnionIfNeeded").invoke(m); }
        catch (Throwable t) { Log.w(TAG, "provisionOnion", t); return ""; }
    }

    /** Store a Lion device's x25519 client-auth pubkey (base32) from /api/pair. */
    public static void storeLionAuthPub(Context ctx, String base32Pub) {
        if (base32Pub == null || base32Pub.isEmpty()) return;
        Object m = mgr(ctx);
        if (m == null) return;
        try { m.getClass().getMethod("storeLionAuthPub", String.class).invoke(m, base32Pub); }
        catch (Throwable t) { Log.w(TAG, "storeLionAuthPub", t); }
    }

    /** ntfy cold-wake: bring Tor up and publish the onion for a session window.
     *  Blocking; call off the main thread. */
    public static void onWake(Context ctx, int sessionMinutes) {
        Object m = mgr(ctx);
        if (m == null) return;
        try { m.getClass().getMethod("startTorAndPublish", int.class).invoke(m, sessionMinutes); }
        catch (Throwable t) { Log.w(TAG, "onWake", t); }
    }

    /** The onion-derived ntfy wake topic the Collar should also subscribe to, or
     *  "" when no onion is provisioned / Tor isn't bundled. */
    public static String wakeTopic(Context ctx) {
        Object m = mgr(ctx);
        if (m == null) return "";
        try {
            String onion = (String) m.getClass().getMethod("provisionOnionIfNeeded").invoke(m);
            if (onion == null || onion.isEmpty()) return "";
            return (String) Class.forName("com.focuslock.TorManager")
                .getMethod("wakeTopicForOnion", String.class).invoke(null, onion);
        } catch (Throwable t) { return ""; }
    }
}

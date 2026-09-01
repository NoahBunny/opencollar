package com.focuslock;

import android.content.Context;
import android.content.SharedPreferences;
import android.provider.Settings;

/**
 * Storage for the wearer's own first-run choices: the consent flag and the panic
 * safeword phrase.
 *
 * These are set on FIRST RUN (ConsentActivity), which happens BEFORE the adb
 * operator step that grants WRITE_SECURE_SETTINGS. Writing them only to
 * Settings.Global — which needs that permission — meant a fresh install either
 * crashed on "I CONSENT" (unguarded SecurityException) or silently dropped the
 * safeword. The latter is safety-relevant: the safeword is the wearer's
 * always-available exit, so a wearer could believe they set one that was never
 * stored.
 *
 * So we dual-store: SharedPreferences ALWAYS (app-private, needs no permission,
 * cannot fail) plus a best-effort mirror to Settings.Global (preserves the
 * pre-existing survive-app-data-clear property, and stays backward-compatible
 * with already-provisioned devices that recorded these via adb/Settings.Global).
 * Reads consult both stores.
 */
final class ConsentStore {
    private static final String PREFS = "consent";
    private static final String K_CONSENTED = "focus_lock_consented";
    private static final String K_CONSENT_TIME = "focus_lock_consent_time";
    private static final String K_SAFEWORD = "focus_lock_safeword";
    private static final String K_CAGE_LEVEL = "focus_lock_cage_level";

    private ConsentStore() {}

    private static SharedPreferences prefs(Context ctx) {
        return ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // Best-effort Settings.Global writes — never throw when WRITE_SECURE_SETTINGS
    // is absent (fresh install, pre-operator-provisioning).
    private static void putGlobalInt(Context ctx, String k, int v) {
        try { Settings.Global.putInt(ctx.getContentResolver(), k, v); } catch (Exception e) {}
    }
    private static void putGlobalLong(Context ctx, String k, long v) {
        try { Settings.Global.putLong(ctx.getContentResolver(), k, v); } catch (Exception e) {}
    }
    private static void putGlobalString(Context ctx, String k, String v) {
        try { Settings.Global.putString(ctx.getContentResolver(), k, v); } catch (Exception e) {}
    }

    /** Record consent (with a timestamp). Always persists via SharedPreferences. */
    static void setConsented(Context ctx) {
        long now = System.currentTimeMillis();
        prefs(ctx).edit().putBoolean(K_CONSENTED, true).putLong(K_CONSENT_TIME, now).apply();
        putGlobalInt(ctx, K_CONSENTED, 1);
        putGlobalLong(ctx, K_CONSENT_TIME, now);
    }

    /** Consented if EITHER store records it — once given, it stays given. */
    static boolean isConsented(Context ctx) {
        if (prefs(ctx).getBoolean(K_CONSENTED, false)) return true;
        try {
            return Settings.Global.getInt(ctx.getContentResolver(), K_CONSENTED, 0) == 1;
        } catch (Exception e) {
            return false;
        }
    }

    /** Revoke consent in BOTH stores (Release Forever only — never wearer-driven). */
    static void clearConsented(Context ctx) {
        prefs(ctx).edit().putBoolean(K_CONSENTED, false).apply();
        putGlobalInt(ctx, K_CONSENTED, 0);
    }

    /** Store the safeword phrase. Always persists via SharedPreferences. */
    static void setSafeword(Context ctx, String phrase) {
        prefs(ctx).edit().putString(K_SAFEWORD, phrase).apply();
        putGlobalString(ctx, K_SAFEWORD, phrase);
    }

    /**
     * The stored safeword phrase, or null if none. A Settings.Global value wins
     * when present (an adb-provisioned / already-deployed device), else the
     * SharedPreferences value. Callers keep their own default for the empty case.
     */
    static String getSafeword(Context ctx) {
        String g = null;
        try { g = Settings.Global.getString(ctx.getContentResolver(), K_SAFEWORD); } catch (Exception e) {}
        if (g != null && !g.isEmpty()) return g;
        return prefs(ctx).getString(K_SAFEWORD, null);
    }

    /**
     * Record the wearer's chosen cage ceiling (the tightest the Collar may ever
     * get: 0=LEASH, 1=COLLAR, 2=SEALED — see ShadeGuardService.LEVEL_*). Stored
     * ONLY in app-private SharedPreferences and DELIBERATELY NOT mirrored to
     * Settings.Global: the ceiling is the bunny's consent boundary, and the
     * Lion's ADB bridge can write Settings.Global (`settings put global …`) but
     * cannot write another app's private prefs. Keeping the ceiling here is what
     * makes "the Lion can only loosen, never tighten" actually enforceable
     * (see ShadeGuardService.effectiveCageLevel()).
     */
    static void setCageLevel(Context ctx, int level) {
        if (level < 0) level = 0;
        if (level > 2) level = 2;
        prefs(ctx).edit().putInt(K_CAGE_LEVEL, level).apply();
        mirrorForDisplay(ctx, level);
    }

    /**
     * Copy the ceiling into Settings.Global so Bunny Tasker can SHOW it.
     *
     * <p>Display only, and never read back as authority:
     * {@link ShadeGuardService#effectiveCageLevel} takes the ceiling from the
     * private prefs above and nowhere else. That distinction is the whole
     * security property — Settings.Global is writable by the Lion's ADB
     * bridge, so a mirror that anything trusted would re-open exactly the hole
     * keeping the ceiling app-private was meant to close.
     *
     * <p>Worst case for a tampered mirror is therefore a companion screen
     * showing the wrong number, not a cage that tightened without consent.
     */
    static void mirrorForDisplay(Context ctx, int level) {
        try {
            android.provider.Settings.Global.putInt(
                ctx.getContentResolver(), "focus_lock_cage_ceiling", level);
            android.provider.Settings.Global.putInt(
                ctx.getContentResolver(), "focus_lock_cage_level_effective",
                ShadeGuardService.effectiveCageLevel(ctx));
        } catch (Exception e) {
            android.util.Log.w("FocusLock", "cage mirror failed (display only)", e);
        }
    }

    /** The bunny's chosen cage ceiling, or -1 if never set (caller defaults to
     *  LEASH so an un-provisioned / pre-consent device is never silently caged
     *  tighter than the wearer opted into). */
    static int getCageLevel(Context ctx) {
        return prefs(ctx).getInt(K_CAGE_LEVEL, -1);
    }
}

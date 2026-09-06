package com.focuslock;

import android.accessibilityservice.AccessibilityService;
import android.content.Intent;
import android.os.SystemClock;
import android.provider.Settings;
import android.text.TextUtils;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;

import java.util.HashSet;
import java.util.Set;

/**
 * Lock guard for the non-device-owner case. Two jobs, both driven off the
 * accessibility event stream (which, unlike a background service, is allowed to
 * start activities on Android 10+ — accessibility-initiated launches are exempt
 * from the background-activity-launch restrictions that silently drop the
 * Collar's other relaunch paths).
 *
 * 1. Notification-shade guard: on a personal phone the Collar is (at most) a
 *    device admin, not a device owner, so it cannot pre-disable the status bar
 *    and an app overlay cannot intercept the shade pull. When a lock is active
 *    we collapse the shade via GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE.
 *
 * 2. Foreground-app watchdog (the actual cage): being the HOME app only
 *    intercepts the Home button — app-switching, recents, notification taps and
 *    direct launches all let another app (Maps, a browser, …) come to the
 *    foreground and stay there. So on every window-state change during a lock we
 *    read the foreground package and, if it is not on the allowlist, immediately
 *    bring FocusActivity back to front. This is what makes the phone a brick.
 *
 * The allowlist lets the wearer keep using the things they are permitted to
 * during a lock: Bunny Tasker, the configured banking app (to pay), the phone
 * dialer (calls are allowed), plus SystemUI / the active IME / framework
 * windows so typing and system dialogs are not fought.
 *
 * Honors the terminal release flag (never fights a released device) and reads
 * lock state from Settings.Global (reads need no permission). Enabled by the
 * operator via adb during recage; the wearer disabling it is detectable tamper.
 */
public class ShadeGuardService extends AccessibilityService {
    private static final String TAG = "FocusLock";

    // Cage tightness tiers (the bunny's chosen ceiling). Higher = tighter.
    // Defined in CageRule, which holds the clamp rule itself so it can be unit
    // tested without a device; these are aliases so existing call sites read
    // the same as they always did.
    static final int LEVEL_LEASH  = CageRule.LEVEL_LEASH;   // home-button only, apps usable
    static final int LEVEL_COLLAR = CageRule.LEVEL_COLLAR;  // bounce other apps; calls allowed
    static final int LEVEL_SEALED = CageRule.LEVEL_SEALED;  // minimal allowlist; no calls

    // Framework packages that must NEVER be re-jailed at any tier — they carry
    // the IME host, status bar, volume dialog, telephony service, and the
    // runtime-permission grant UI. On modern Android the grant dialogs live in
    // permissioncontroller, NOT "android", so both are listed — otherwise a
    // permission the wearer needs to complete an unlock condition could never be
    // granted while locked.
    private static final String[] FRAMEWORK_ALLOW = {
        "android",
        "com.android.systemui",
        "com.android.server.telecom",
        "com.google.android.permissioncontroller",
        "com.android.permissioncontroller",
    };

    // In-call / emergency surfaces — allowed at EVERY tier, including SEALED. A
    // caged phone must always be able to place an emergency call, and an
    // in-progress call must never be bounced. SEALED's "no calls" only blocks
    // *starting* a normal call via the dialer apps below; it cannot block the
    // emergency floor.
    private static final String[] INCALL_EMERGENCY_ALLOW = {
        "com.android.phone",
        "com.android.incallui",
        "com.samsung.android.incallui",
        "com.android.emergency",
        "com.google.android.apps.emergency",
    };

    // Dialer apps used to START a normal call — allowed at LEASH/COLLAR, blocked
    // at SEALED ("no calls"). Emergency dialing stays available via
    // INCALL_EMERGENCY_ALLOW at every tier.
    private static final String[] DIALER_ALLOW = {
        "com.android.dialer",
        "com.google.android.dialer",
        "com.samsung.android.dialer",
    };

    // Throttle relaunches so a burst of window events doesn't hammer startActivity.
    private long lastRelaunchMs = 0;
    private static final long RELAUNCH_MIN_INTERVAL_MS = 300;

    // Debounce escape accounting so one deliberate app-switch isn't counted many
    // times as the window churns; mirrors FocusActivity.recordEscape's 5s window.
    private long lastEscapeMs = 0;
    private static final long ESCAPE_DEBOUNCE_MS = 5000;

    // Resolved default camera package (for the wearer-driven photo task), cached
    // for the service lifetime. Allowed at every tier so Photo Task mode — whose
    // only exit is submitting a photo — can actually be completed.
    private String cameraPkgCache = null;
    private boolean cameraResolved = false;

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;
        int type = event.getEventType();
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                && type != AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
            return;
        }
        if (!isLockActive()) return;

        // Always collapse the shade (no-op when already closed). TYPE_WINDOWS_CHANGED
        // carries a null packageName, so shade handling stays unconditional.
        dismissShade();

        // Foreground-app watchdog is gated by the effective cage tier: at LEASH
        // we only guard the shade and let apps be. COLLAR/SEALED bounce apps.
        int level = effectiveCageLevel();
        if (level < LEVEL_COLLAR) return;

        // Watchdog runs on WINDOW_STATE_CHANGED, which carries the package of the
        // newly-focused window.
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED) return;
        CharSequence pkgCs = event.getPackageName();
        if (pkgCs == null) return;
        String pkg = pkgCs.toString();
        if (pkg.isEmpty()) return;
        if (isAllowed(pkg, level)) return;

        // A disallowed app is in the foreground during a lock — this is an escape
        // attempt. Record it (debounced) so the escape counter drives the paywall
        // tier AND reveals the in-app factory-reset button (gated at escapes >= 3),
        // then bring the jail back. Before this, delegating re-jailing to the
        // watchdog silently froze the escape counter at 0, hiding the documented
        // in-app exit.
        long now = SystemClock.uptimeMillis();
        if (now - lastRelaunchMs < RELAUNCH_MIN_INTERVAL_MS) return;
        lastRelaunchMs = now;
        recordBounceEscape();
        relaunchJail(pkg);
    }

    /** Debounced escape accounting for a watchdog bounce. Increments
     *  focus_lock_escapes (drives the factory-reset button + shame) and posts the
     *  server "escape" event (drives the paywall tier), mirroring
     *  FocusActivity.recordEscape's counter/report without duplicating its in-jail
     *  UX (buzz/shame runs when FocusActivity is foregrounded by the relaunch). */
    private void recordBounceEscape() {
        try {
            android.os.PowerManager pm =
                (android.os.PowerManager) getSystemService(POWER_SERVICE);
            if (pm != null && !pm.isInteractive()) return;  // don't count while screen off
        } catch (Exception ignored) {}
        long now = SystemClock.uptimeMillis();
        if (lastEscapeMs != 0 && now - lastEscapeMs < ESCAPE_DEBOUNCE_MS) return;
        lastEscapeMs = now;
        try {
            int e = Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0) + 1;
            Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", e);
        } catch (Exception ignored) { /* WRITE_SECURE_SETTINGS not yet granted */ }
        try { ControlService.postEventToServer(this, "escape", null); } catch (Exception ignored) {}
    }

    /** Packages the wearer may use during a lock at the given tier without being
     *  bounced back to the jail. */
    private boolean isAllowed(String pkg, int level) {
        if (pkg.equals(getPackageName())) return true;      // the jail itself
        if (pkg.equals("com.bunnytasker")) return true;     // companion — allowed at every tier
        for (String s : FRAMEWORK_ALLOW) if (pkg.equals(s)) return true;
        // Emergency / in-call surface — every tier (emergency-call safety floor).
        for (String s : INCALL_EMERGENCY_ALLOW) if (pkg.equals(s)) return true;
        // Normal dialer apps (start a call) — allowed below SEALED only.
        if (level < LEVEL_SEALED) {
            for (String s : DIALER_ALLOW) if (pkg.equals(s)) return true;
        }
        // Settings — normally bounced so the cage holds during ordinary locked
        // use, BUT the OS factory-reset floor the Terms of Surrender promise is
        // "always available" lives inside Settings, so it must stay reachable
        // when the wearer is genuinely leaving. Reachable when EITHER (a) the Lion
        // granted a settings window (/api/enable-settings → focus_lock_settings_
        // allowed) or (b) the wearer has crossed the escape threshold that also
        // reveals the in-app factory-reset button (focus_lock_escapes >= 3).
        // See docs/THREAT-MODEL.md § safety floor.
        if (pkg.equals("com.android.settings")) {
            if (readIntSetting("focus_lock_settings_allowed", 0) == 1) return true;
            if (readIntSetting("focus_lock_escapes", 0) >= 3) return true;
            return false;
        }
        // Configured banking app (so the wearer can pay the paywall) — every tier.
        try {
            String bank = Settings.Global.getString(getContentResolver(), "focus_lock_banking_app");
            if (!TextUtils.isEmpty(bank) && pkg.equals(bank)) return true;
        } catch (Exception ignored) {}
        // Default camera app for the wearer-driven photo task — every tier, so
        // Photo Task mode (whose only exit is submitting a photo) can complete.
        String cam = cameraPackage();
        if (cam != null && pkg.equals(cam)) return true;
        // Active input method (keyboard) — never fight text entry.
        try {
            String ime = Settings.Secure.getString(getContentResolver(), Settings.Secure.DEFAULT_INPUT_METHOD);
            if (!TextUtils.isEmpty(ime)) {
                int slash = ime.indexOf('/');
                String imePkg = slash > 0 ? ime.substring(0, slash) : ime;
                if (pkg.equals(imePkg)) return true;
            }
        } catch (Exception ignored) {}
        return false;
    }

    /** Resolve (once, cached) the package that handles ACTION_IMAGE_CAPTURE — the
     *  camera app the photo task launches. May be null (no camera / unresolved),
     *  in which case no camera package is allowlisted. */
    private String cameraPackage() {
        if (cameraResolved) return cameraPkgCache;
        cameraResolved = true;
        try {
            Intent i = new Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE);
            android.content.pm.ResolveInfo ri = getPackageManager().resolveActivity(i, 0);
            if (ri != null && ri.activityInfo != null) cameraPkgCache = ri.activityInfo.packageName;
        } catch (Exception ignored) {}
        return cameraPkgCache;
    }

    /** Effective cage tier = the bunny's chosen ceiling, optionally loosened (but
     *  NEVER tightened) by the Lion. This is the consent rule made literal:
     *
     *  - The ceiling is read from ConsentStore (app-private SharedPreferences),
     *    the ONLY store the Lion's ADB bridge cannot write — so `settings put
     *    global focus_lock_cage_level 2` can no longer tighten the cage past what
     *    the wearer consented to. It has an author now: the tier chooser on the
     *    Terms-of-Surrender screen (ConsentActivity).
     *  - Unset (a device provisioned before the chooser, or a wearer who left it
     *    at the default) yields LEASH — home-button-only, apps usable — so
     *    shipping the watchdog never silently upgrades a device to a stricter
     *    cage the wearer never opted into.
     *  - The Lion may request a LOOSER level via focus_lock_cage_level_lion
     *    (Settings.Global); min() clamps it so the Lion can only loosen. */
    private int effectiveCageLevel() {
        return effectiveCageLevel(this);
    }

    /** Static form so other components (e.g. ControlService's tamper check) can
     *  ask the same question. See the instance-doc above for the consent rule. */
    static int effectiveCageLevel(android.content.Context ctx) {
        int lion;
        try {
            lion = Settings.Global.getInt(ctx.getContentResolver(), "focus_lock_cage_level_lion", -1);
        } catch (Exception e) {
            lion = -1;   // unreadable request is no request; the ceiling stands
        }
        // The rule itself lives in CageRule so it is testable off-device. This
        // method's only job is choosing WHERE each number is read from, which
        // is the other half of the guarantee: the ceiling from app-private
        // prefs the bridge cannot write, the request from Settings.Global it
        // can.
        return CageRule.effective(ConsentStore.getCageLevel(ctx), lion);
    }

    /** True if this accessibility service is currently enabled — the watchdog
     *  only enforces when it is. Reads Settings.Secure (no permission needed). */
    static boolean isEnabled(android.content.Context ctx) {
        try {
            String enabled = Settings.Secure.getString(
                ctx.getContentResolver(), Settings.Secure.ENABLED_ACCESSIBILITY_SERVICES);
            if (TextUtils.isEmpty(enabled)) return false;
            String me = ctx.getPackageName() + "/" + ShadeGuardService.class.getName();
            String meShort = ctx.getPackageName() + "/." + ShadeGuardService.class.getSimpleName();
            for (String s : enabled.split(":")) {
                if (s.equalsIgnoreCase(me) || s.equalsIgnoreCase(meShort)) return true;
            }
        } catch (Exception ignored) {}
        return false;
    }

    private int readIntSetting(String key, int def) {
        try {
            return Settings.Global.getInt(getContentResolver(), key, def);
        } catch (Exception e) {
            return def;
        }
    }

    private void relaunchJail(String offendingPkg) {
        try {
            Intent i = new Intent(this, FocusActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK
                    | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT
                    | Intent.FLAG_ACTIVITY_SINGLE_TOP);
            startActivity(i);
            Log.i(TAG, "shade guard: re-jailed over " + offendingPkg);
        } catch (Exception e) {
            Log.w(TAG, "shade guard: relaunch failed", e);
        }
    }

    private void dismissShade() {
        try {
            boolean ok;
            if (android.os.Build.VERSION.SDK_INT >= 31) {
                ok = performGlobalAction(GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE);
            } else {
                ok = performGlobalAction(GLOBAL_ACTION_BACK);
            }
            if (ok) Log.i(TAG, "shade guard: collapsed the notification shade during a lock");
        } catch (Exception e) {
            Log.w(TAG, "shade guard: dismiss failed", e);
        }
    }

    /** Locked and not released. Reads only — no WRITE_SECURE_SETTINGS needed. */
    private boolean isLockActive() {
        try {
            if (Settings.Global.getInt(getContentResolver(), "focus_lock_released", 0) == 1) {
                return false;  // terminal safety floor — never fight a released device
            }
            return Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0) == 1;
        } catch (Exception e) {
            return false;
        }
    }

    @Override
    public void onServiceConnected() {
        super.onServiceConnected();
        Log.i(TAG, "shade guard: accessibility service connected");
    }

    @Override
    public void onInterrupt() { /* no-op */ }
}

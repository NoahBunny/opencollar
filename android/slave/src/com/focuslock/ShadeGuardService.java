package com.focuslock;

import android.accessibilityservice.AccessibilityService;
import android.provider.Settings;
import android.util.Log;
import android.view.accessibility.AccessibilityEvent;

/**
 * Notification-shade guard for the non-device-owner case.
 *
 * On a personal phone the Collar is (at most) a device admin, not a device owner,
 * so it cannot pre-disable the status bar (setStatusBarDisabled is owner-only) and
 * an app overlay cannot intercept the shade pull (the status bar is a system window
 * layered above all app overlays). That left the wearer able, during a lock, to pull
 * down the notification shade and reach Quick Settings — e.g. toggle airplane mode
 * and cut the Lion's control.
 *
 * This service watches for a SystemUI panel surfacing while a lock is active and
 * immediately collapses it via GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE (API 31+).
 * It is reactive — the shade may flash open for a moment before snapping shut — but
 * it denies sustained access to notifications / Quick Settings without device owner
 * or the homelab bridge.
 *
 * It honors the terminal release flag (never fights a released device) and reads the
 * lock state from Settings.Global (reads need no permission). Enabled by the operator
 * via adb during recage; the wearer can disable it in Settings, which the Collar can
 * detect as tamper, exactly like disabling device admin.
 */
public class ShadeGuardService extends AccessibilityService {
    private static final String TAG = "FocusLock";

    @Override
    public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null) return;
        int type = event.getEventType();
        if (type != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED
                && type != AccessibilityEvent.TYPE_WINDOWS_CHANGED) {
            return;
        }
        if (!isLockActive()) return;

        // Any window change while locked → collapse the shade. We do NOT filter on
        // packageName: TYPE_WINDOWS_CHANGED carries a null packageName (it is a
        // global window-list change), which is exactly the event the shade fires on
        // this device — filtering it out was why the first cut did nothing.
        // GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE only ever affects the shade and
        // is a no-op when it is already closed, so calling it broadly is safe (it
        // never touches the jail activity or a volume dialog).
        dismissShade();
    }

    private void dismissShade() {
        try {
            boolean ok;
            if (android.os.Build.VERSION.SDK_INT >= 31) {
                ok = performGlobalAction(GLOBAL_ACTION_DISMISS_NOTIFICATION_SHADE);
            } else {
                // Pre-31 has no dismiss-shade action; BACK collapses it in most cases.
                ok = performGlobalAction(GLOBAL_ACTION_BACK);
            }
            // ok==true means the shade was actually open and got collapsed — i.e.
            // an attempt to reach the shade during a lock. Rare, and a useful
            // signal; the no-op case (already closed) is not logged to avoid spam.
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

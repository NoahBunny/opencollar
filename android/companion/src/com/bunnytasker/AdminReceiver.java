package com.bunnytasker;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;

/**
 * Device admin receiver — prevents uninstall of Bunny Tasker.
 * Must be activated via: adb shell dpm set-active-admin com.bunnytasker/.AdminReceiver
 */
public class AdminReceiver extends DeviceAdminReceiver {
    @Override
    public void onEnabled(Context context, Intent intent) {}

    @Override
    public void onDisabled(Context context, Intent intent) {
        // Authorized release OR terminally released (safety floor) — no alert.
        // release_authorized covers the in-progress teardown; `released` covers
        // the state after doReleaseForever clears that flag, so a post-safeword
        // device doesn't fire a false "admin was removed" tamper warning.
        try {
            if (android.provider.Settings.Global.getInt(context.getContentResolver(),
                        "focus_lock_released", 0) == 1
                    || android.provider.Settings.Global.getInt(context.getContentResolver(),
                        "focus_lock_release_authorized", 0) == 1) return;
        } catch (Exception e) {}
        // Alert if admin is removed — this is a tamper event
        try {
            android.provider.Settings.Global.putString(context.getContentResolver(),
                "focus_lock_pinned_message", "WARNING: Bunny Tasker admin was removed!");
        } catch (Exception e) {}
    }
}

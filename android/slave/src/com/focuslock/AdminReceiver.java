package com.focuslock;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;
import android.provider.Settings;
import android.util.Log;

public class AdminReceiver extends DeviceAdminReceiver {

    @Override
    public CharSequence onDisableRequested(Context context, Intent intent) {
        // Authorized release — no penalty
        try {
            if (Settings.Global.getInt(context.getContentResolver(), "focus_lock_release_authorized", 0) == 1) {
                return "Authorized release in progress.";
            }
        } catch (Exception e) {}
        // Immediately lock and STACK $500 penalty just for ATTEMPTING
        try {
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_active", 1);
            int current = 0;
            try { current = Integer.parseInt(Settings.Global.getString(context.getContentResolver(), "focus_lock_paywall")); } catch (Exception e2) {}
            int newPw = current + 500;
            Settings.Global.putString(context.getContentResolver(), "focus_lock_paywall", String.valueOf(newPw));
            Settings.Global.putString(context.getContentResolver(), "focus_lock_message",
                "Admin removal attempted.\n+$500 penalty (total: $" + newPw + ").\nYour partner has been notified.");
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_shame", 1);
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_admin_tamper", 1);
        } catch (Exception e) {}

        // Force close Settings and launch jail
        try {
            Runtime.getRuntime().exec(new String[]{"am", "force-stop", "com.android.settings"});
            Intent jail = new Intent(context, FocusActivity.class);
            jail.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            context.startActivity(jail);
        } catch (Exception e) {}

        // Alert mesh server (bunny-signed, vault-propagated — roadmap #4)
        ControlService.postEventToServer(context, "tamper_detected", null);
        notifyHomelab(context, "ALERT: Admin deactivation ATTEMPTED. $500 penalty applied.");

        return "You are about to lose all phone privileges.\n\n"
            + "A $500 penalty has already been applied.\n"
            + "Your partner has been notified.\n"
            + "The phone is now locked.\n\n"
            + "If you proceed, the penalty will increase to $1000\n"
            + "and the bridge will re-enable admin within seconds.\n\n"
            + "There is no escape. Press Cancel.";
    }

    @Override
    public void onDisabled(Context context, Intent intent) {
        // Authorized release — no penalty
        try {
            if (Settings.Global.getInt(context.getContentResolver(), "focus_lock_release_authorized", 0) == 1) {
                Log.i("FocusLock", "Admin removed during authorized release — no penalty");
                return;
            }
        } catch (Exception e) {}
        Log.w("FocusLock", "DEVICE ADMIN DEACTIVATED — stacking $1000 penalty");
        try {
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_active", 1);
            int current = 0;
            try { current = Integer.parseInt(Settings.Global.getString(context.getContentResolver(), "focus_lock_paywall")); } catch (Exception e2) {}
            int newPw = current + 1000;
            Settings.Global.putString(context.getContentResolver(), "focus_lock_paywall", String.valueOf(newPw));
            Settings.Global.putString(context.getContentResolver(), "focus_lock_message",
                "Admin was removed.\n+$1000 penalty (total: $" + newPw + ").\nYour partner has been notified.\nThe bridge will re-enable admin.");
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_shame", 1);
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_admin_removed", 1);
        } catch (Exception e) {}

        // Force close Settings and launch jail
        try {
            Runtime.getRuntime().exec(new String[]{"am", "force-stop", "com.android.settings"});
            Intent jail = new Intent(context, FocusActivity.class);
            jail.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            context.startActivity(jail);
        } catch (Exception e) {}

        // tamper_removed action on server does its own +$1000 to lifetime paywall
        // counter; the local penalty above keeps the phone's display in sync.
        ControlService.postEventToServer(context, "tamper_removed", null);
        notifyHomelab(context, "CRITICAL: Admin was REMOVED. $1000 penalty applied. Re-enabling via bridge.");
    }

    @Override
    public void onEnabled(Context context, Intent intent) {
        try {
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_admin_removed", 0);
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_admin_tamper", 0);
        } catch (Exception e) {}
    }

    private void notifyHomelab(Context ctx, String message) {
        new Thread(() -> {
            try {
                String meshUrl = Settings.Global.getString(
                    ctx.getContentResolver(), "focus_lock_mesh_url");
                if (meshUrl == null || meshUrl.isEmpty()) return;
                java.net.URL url = new java.net.URL(meshUrl + "/webhook/compliment");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                String json = "{\"text\":\"" + message.replace("\"", "'") + "\"}";
                conn.getOutputStream().write(json.getBytes());
                conn.getResponseCode();
                conn.disconnect();
            } catch (Exception e) {}
        }).start();
    }
}

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
        // Lock immediately. $500 attempt penalty applied server-side by
        // tamper-recorded(kind=attempt) (P2 paywall hardening, 2026-04-17) —
        // new paywall lands back here on the next vault pull.
        try {
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_active", 1);
            Settings.Global.putString(context.getContentResolver(), "focus_lock_message",
                "Admin removal attempted.\n+$500 penalty.\nYour partner has been notified.");
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

        // Alert mesh server (bunny-signed, vault-propagated). tamper_attempt
        // triggers the $500 server-side penalty + increments lifetime_tamper.
        ControlService.postEventToServer(context, "tamper_attempt", null);
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
        Log.w("FocusLock", "DEVICE ADMIN DEACTIVATED — reporting tamper_removed");
        try {
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_active", 1);
            Settings.Global.putString(context.getContentResolver(), "focus_lock_message",
                "Admin was removed.\n+$1000 penalty.\nYour partner has been notified.\nThe bridge will re-enable admin.");
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

        // P2 paywall hardening (2026-04-17): server applies the +$1000 on
        // tamper_removed and propagates via vault. Phone no longer writes
        // paywall locally.
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
                // Audit 2026-04-27 H-2: slave-signed evidence webhook.
                org.json.JSONObject body = new org.json.JSONObject();
                body.put("text", message);
                String signed = SlaveSigner.signAndAttach(ctx, "compliment", body);
                if (signed == null) return;  // unpaired — skip silently
                java.net.URL url = new java.net.URL(meshUrl + "/webhook/compliment");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.getOutputStream().write(signed.getBytes("UTF-8"));
                conn.getResponseCode();
                conn.disconnect();
            } catch (Exception e) {}
        }).start();
    }
}

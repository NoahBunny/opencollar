package com.focuslock;

import android.app.admin.DeviceAdminReceiver;
import android.content.Context;
import android.content.Intent;
import android.provider.Settings;
import android.util.Log;

public class AdminReceiver extends DeviceAdminReceiver {

    /** True when this device is under an authorized teardown — either mid-release
     *  (release_authorized, set by doReleaseForever and cleared at the end) or
     *  TERMINALLY released (the safety floor: doSafewordRelease sets it and it is
     *  preserved for good). Removing admin in either state is consensual, so it
     *  must never re-lock or penalize. Mirrors ControlService.isReleased(). The
     *  released check is what keeps the admin-tamper re-lock from violating the
     *  terminal floor after release_authorized has been cleaned up
     *  (docs/THREAT-MODEL.md: once released, no enforcement action may re-lock). */
    private static boolean releaseInProgressOrDone(Context context) {
        try {
            return Settings.Global.getInt(context.getContentResolver(), "focus_lock_released", 0) == 1
                || Settings.Global.getInt(context.getContentResolver(), "focus_lock_release_authorized", 0) == 1;
        } catch (Exception e) { return false; }
    }

    /** Paired = a Lion's pubkey is on file (mirrors ControlService.isPaired). Admin
     *  removal on an UNPAIRED device must not re-lock: there is no Lion to be
     *  accountable to, and the re-lock would trap the wearer with active=1 and no
     *  unlock path — e.g. while provisioning device admin BEFORE the Lion pairs. */
    private static boolean isPaired(Context context) {
        try {
            String lp = Settings.Global.getString(context.getContentResolver(), "focus_lock_lion_pubkey");
            return lp != null && !lp.isEmpty() && !"null".equals(lp);
        } catch (Exception e) { return false; }
    }

    @Override
    public CharSequence onDisableRequested(Context context, Intent intent) {
        // Authorized release / terminally released, OR simply not yet paired — no
        // penalty, no re-lock threat. An unpaired device has no Lion to enforce for.
        if (releaseInProgressOrDone(context) || !isPaired(context)) {
            return "Device admin can be disabled.";
        }
        // Re-lock the phone (friction) and record the attempt for the Lion's
        // accountability. Costly-exit, not punish-exit (see docs/THREAT-MODEL.md):
        // NO financial penalty is applied for touching admin — the act of
        // leaving is never punished. The wearer can always factory-reset or
        // use the panic safeword to end the arrangement.
        try {
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_active", 1);
            Settings.Global.putString(context.getContentResolver(), "focus_lock_message",
                "Admin removal attempted.\nThe phone is locked and your partner has been notified.");
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

        // Notify the Lion for accountability (non-financial — the server-side
        // tamper-recorded handler no longer applies a penalty).
        ControlService.postEventToServer(context, "tamper_attempt", null);
        notifyHomelab(context, "Admin deactivation attempted.");

        return "Disabling admin will re-lock the phone and notify your partner.\n\n"
            + "No penalty is applied — this arrangement is consensual, and you can\n"
            + "always factory-reset or use the safeword to end it.\n\n"
            + "Press Cancel to stay locked, or proceed to disable admin.";
    }

    @Override
    public void onDisabled(Context context, Intent intent) {
        // Authorized release / terminally released, OR not yet paired — no penalty
        // and crucially no re-lock. Re-locking a released device violates the
        // terminal floor (THREAT-MODEL); re-locking an UNPAIRED device traps the
        // wearer with active=1 and no Lion to unlock. release_authorized covers the
        // in-progress teardown; `released` the state after it's cleaned up.
        if (releaseInProgressOrDone(context) || !isPaired(context)) {
            Log.i("FocusLock", "Admin removed during authorized/terminal release or while unpaired — no penalty, no re-lock");
            return;
        }
        Log.w("FocusLock", "DEVICE ADMIN DEACTIVATED — reporting tamper_removed (non-financial)");
        try {
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_active", 1);
            Settings.Global.putString(context.getContentResolver(), "focus_lock_message",
                "Admin was removed.\nThe phone is locked and your partner has been notified.");
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

        // Notify the Lion for accountability (non-financial — no penalty is
        // applied). The bridge may re-enable admin as friction, but factory
        // reset and the panic safeword remain available. See THREAT-MODEL.
        ControlService.postEventToServer(context, "tamper_removed", null);
        notifyHomelab(context, "Admin was removed.");
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

package com.focuslock;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.os.Bundle;
import android.provider.Settings;
import android.telephony.SmsMessage;
import android.util.Log;

import java.util.regex.Matcher;
import java.util.regex.Pattern;

/**
 * Receives SMS and processes "sit-boy" commands from the controller number.
 *
 * Formats:
 *   sit-boy           → indefinite lock
 *   sit-boy 15        → 15 min lock
 *   sit-boy $20       → lock + $20 paywall
 *   sit-boy 15 $20    → 15 min + $20 paywall
 */
public class SmsReceiver extends BroadcastReceiver {
    private static final String TAG = "FocusLock";
    private static final Pattern CMD_PATTERN =
        Pattern.compile("sit-boy(?:\\s+(\\d+))?(?:\\s+\\$?(\\d+))?", Pattern.CASE_INSENSITIVE);

    @Override
    public void onReceive(Context context, Intent intent) {
        if (!"android.provider.Telephony.SMS_RECEIVED".equals(intent.getAction())) return;

        // Get controller number from settings
        String controllerNumber = Settings.Global.getString(
            context.getContentResolver(), "focus_lock_controller_number");
        if (controllerNumber == null || controllerNumber.isEmpty()) return;

        Bundle bundle = intent.getExtras();
        if (bundle == null) return;

        Object[] pdus = (Object[]) bundle.get("pdus");
        String format = bundle.getString("format");
        if (pdus == null) return;

        for (Object pdu : pdus) {
            SmsMessage sms = SmsMessage.createFromPdu((byte[]) pdu, format);
            String sender = sms.getOriginatingAddress();
            String body = sms.getMessageBody();
            if (sender == null || body == null) continue;

            // Normalize: strip all non-digit chars for comparison
            String senderDigits = sender.replaceAll("[^0-9]", "");
            String controllerDigits = controllerNumber.replaceAll("[^0-9]", "");
            if (senderDigits.length() < 10 || controllerDigits.length() < 10) continue;
            // Match last 10 digits
            if (!senderDigits.substring(senderDigits.length() - 10)
                    .equals(controllerDigits.substring(controllerDigits.length() - 10))) {
                continue;
            }

            Matcher m = CMD_PATTERN.matcher(body.trim());
            if (!m.find()) continue;

            Log.i(TAG, "sit-boy command from " + sender + ": " + body);

            String minsStr = m.group(1);
            String amountStr = m.group(2);
            long mins = 0;
            String paywall = "0";
            if (minsStr != null) mins = Long.parseLong(minsStr);
            if (amountStr != null) paywall = amountStr;

            // Set lock flags
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_active", 1);
            Settings.Global.putString(context.getContentResolver(), "focus_lock_message",
                "Sit, boy. (SMS from your controller)");
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_escapes", 0);
            Settings.Global.putString(context.getContentResolver(), "focus_lock_mode", "basic");
            Settings.Global.putInt(context.getContentResolver(), "focus_lock_shame", 1);
            Settings.Global.putLong(context.getContentResolver(), "focus_lock_locked_at",
                System.currentTimeMillis());

            if (mins > 0) {
                Settings.Global.putLong(context.getContentResolver(), "focus_lock_unlock_at",
                    System.currentTimeMillis() + mins * 60000);
            } else {
                Settings.Global.putLong(context.getContentResolver(), "focus_lock_unlock_at", 0);
            }

            if (!paywall.equals("0")) {
                Settings.Global.putString(context.getContentResolver(), "focus_lock_paywall", paywall);
                Settings.Global.putString(context.getContentResolver(), "focus_lock_paywall_original", paywall);
            }

            // Launch FocusActivity
            Intent launch = new Intent(context, FocusActivity.class);
            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            context.startActivity(launch);

            // Ensure ControlService is running
            try {
                context.startForegroundService(new Intent(context, ControlService.class));
            } catch (Exception e) {}

            abortBroadcast(); // prevent SMS from reaching default app
        }
    }
}

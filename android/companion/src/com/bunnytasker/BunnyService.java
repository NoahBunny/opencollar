package com.bunnytasker;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.provider.Settings;
import android.util.Log;

/**
 * Background service that reinforces FocusLock jail.
 * If focus_lock_active=1 but FocusActivity isn't in foreground, re-launches it.
 * Also checks for pinned messages from Lion and updates notification.
 */
public class BunnyService extends Service {

    private static final String TAG = "BunnyTasker";
    private boolean running = false;
    private String lastPinnedMessage = "";
    private String lastPaywall = "0";
    private long lastSubNotifyTime = 0;

    @Override
    public void onCreate() {
        super.onCreate();
        running = true;

        NotificationChannel ch = new NotificationChannel(
            "bunny", "Bunny Tasker", NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);

        Notification n = new Notification.Builder(this, "bunny")
            .setContentTitle("Bunny Tasker")
            .setContentText("Watching over you")
            .setSmallIcon(android.R.drawable.ic_menu_my_calendar)
            .setOngoing(true).build();
        startForeground(2, n);

        startWatcher();
    }

    private void startWatcher() {
        Thread t = new Thread(() -> {
            while (running) {
                try {
                    Thread.sleep(10000); // Check every 10 seconds

                    int active = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
                    if (active == 1) {
                        // Jail reinforcement: try to launch FocusActivity
                        // This is best-effort — the bridge + ControlService are primary enforcers
                        try {
                            Intent launch = new Intent();
                            launch.setClassName("com.focuslock", "com.focuslock.FocusActivity");
                            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startActivity(launch);
                        } catch (Exception e) {
                            // FocusLock may not be installed or activity not exported
                        }
                    }

                    // Check for pinned message changes — only alert on new/changed messages
                    String pinned = gstr("focus_lock_pinned_message");
                    if (!pinned.isEmpty()) {
                        boolean isNew = !pinned.equals(lastPinnedMessage);
                        lastPinnedMessage = pinned;
                        showPinnedNotification(pinned, isNew);
                    } else {
                        lastPinnedMessage = "";
                        try {
                            getSystemService(NotificationManager.class).cancel(300);
                        } catch (Exception e) { Log.e(TAG, "Cancel pinned", e); }
                    }

                    // Geofence confinement notification
                    String geofenceLat = gstr("focus_lock_geofence_lat");
                    NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
                    if (!geofenceLat.isEmpty()) {
                        showGeofenceNotification(nm);
                    } else {
                        try { nm.cancel(302); } catch (Exception e) {}
                    }

                    // Paywall monitoring — persistent notification + alert on change
                    String paywall = gstr("focus_lock_paywall");
                    if (!paywall.isEmpty() && !paywall.equals("0")) {
                        if (!paywall.equals(lastPaywall)) {
                            // Paywall changed — alert notification
                            showPaywallAlert(paywall, lastPaywall);
                            lastPaywall = paywall;
                        }
                        // Always maintain persistent paywall notification
                        showPaywallPersistent(paywall);
                    } else {
                        if (!lastPaywall.equals("0") && !lastPaywall.isEmpty()) {
                            // Paywall cleared — notify
                            showPaywallCleared();
                        }
                        lastPaywall = "0";
                        try { nm.cancel(310); } catch (Exception e) {}
                    }

                    // Subscription charge reminders
                    String subTierForReminder = gstr("focus_lock_sub_tier");
                    long subDue = 0;
                    try { subDue = android.provider.Settings.Global.getLong(getContentResolver(), "focus_lock_sub_due", 0); }
                    catch (Exception e) {}
                    if (!subTierForReminder.isEmpty() && subDue > 0) {
                        long msUntilDue = subDue - System.currentTimeMillis();
                        long hoursUntilDue = msUntilDue / 3600000;
                        // Notify at 48h, 24h, 6h, and 1h before due
                        if (hoursUntilDue <= 48 && hoursUntilDue > 0 &&
                            System.currentTimeMillis() - lastSubNotifyTime > 3600000) {
                            int amt = "bronze".equals(subTierForReminder) ? 25 : "silver".equals(subTierForReminder) ? 35 : 50;
                            showSubReminder(subTierForReminder, amt, hoursUntilDue);
                            lastSubNotifyTime = System.currentTimeMillis();
                        }
                    }

                    // Mandatory reply enforcement — check mesh for overdue replies
                    String overdueFlag = gstr("focus_lock_mandatory_overdue");
                    if ("1".equals(overdueFlag) && active == 0) {
                        Log.w(TAG, "Mandatory reply overdue — auto-locking");
                        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
                        Settings.Global.putString(getContentResolver(), "focus_lock_message",
                            "Missed mandatory reply. Message your Lion NOW.");
                        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "basic");
                        Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at",
                            System.currentTimeMillis());
                    }

                    // Daily check-in enforcement
                    int deadline = Settings.Global.getInt(getContentResolver(), "focus_lock_checkin_deadline", -1);
                    if (deadline >= 0 && active == 0) {
                        long lastCheckin = Settings.Global.getLong(getContentResolver(), "focus_lock_checkin_timestamp", 0);
                        java.util.Calendar cal = java.util.Calendar.getInstance();
                        int curHour = cal.get(java.util.Calendar.HOUR_OF_DAY);
                        int curMin = cal.get(java.util.Calendar.MINUTE);
                        int today = cal.get(java.util.Calendar.DAY_OF_YEAR);
                        int curYear = cal.get(java.util.Calendar.YEAR);

                        // Check if already checked in today
                        boolean checkedInToday = false;
                        if (lastCheckin > 0) {
                            cal.setTimeInMillis(lastCheckin);
                            checkedInToday = cal.get(java.util.Calendar.DAY_OF_YEAR) == today
                                && cal.get(java.util.Calendar.YEAR) == curYear;
                        }
                        cal = java.util.Calendar.getInstance(); // reset

                        if (!checkedInToday) {
                            // Silver/Gold tier reminders
                            String subTier = gstr("focus_lock_sub_tier");
                            boolean canRemind = "silver".equals(subTier) || "gold".equals(subTier);

                            int minutesTilDeadline = (deadline * 60) - (curHour * 60 + curMin);
                            if (canRemind && minutesTilDeadline <= 60 && minutesTilDeadline > 55) {
                                showCheckinReminder("Check-in reminder: 1 hour left! Message your Lion.");
                            } else if (canRemind && minutesTilDeadline <= 15 && minutesTilDeadline > 10) {
                                showCheckinReminder("URGENT: 15 minutes to check in! Message your Lion NOW.");
                            }

                            // Past deadline — auto-lock
                            if (curHour >= deadline && minutesTilDeadline <= 0) {
                                Log.w(TAG, "Check-in missed! Auto-locking.");
                                Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
                                Settings.Global.putString(getContentResolver(), "focus_lock_message",
                                    "Missed daily check-in. Message your Lion.");
                                Settings.Global.putString(getContentResolver(), "focus_lock_mode", "basic");
                                Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at",
                                    System.currentTimeMillis());
                            }
                        }
                    }

                } catch (Exception e) {
                    Log.e(TAG, "Watcher error", e);
                }
            }
        });
        t.setDaemon(true);
        t.start();
    }

    private void showPinnedNotification(String message, boolean isNew) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            // Use LOW importance for silent persistent pin; only alert on first appearance
            String channelId = isNew ? "pinned_alert" : "pinned_silent";
            if (isNew) {
                // One-time alert channel — pings once
                NotificationChannel alertCh = new NotificationChannel(
                    "pinned_alert", "Pinned Messages (Alert)", NotificationManager.IMPORTANCE_HIGH);
                alertCh.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
                nm.createNotificationChannel(alertCh);
            }
            // Silent ongoing channel for the persistent notification
            NotificationChannel silentCh = new NotificationChannel(
                "pinned_silent", "Pinned Messages", NotificationManager.IMPORTANCE_LOW);
            silentCh.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            nm.createNotificationChannel(silentCh);

            if (isNew) {
                // Post alert notification briefly
                Notification alert = new Notification.Builder(this, "pinned_alert")
                    .setContentTitle("New message from your Lion")
                    .setContentText(message)
                    .setStyle(new Notification.BigTextStyle().bigText(message))
                    .setSmallIcon(android.R.drawable.ic_dialog_email)
                    .setVisibility(Notification.VISIBILITY_PUBLIC)
                    .setAutoCancel(true)
                    .build();
                nm.notify(301, alert);
            }
            // Always maintain the silent ongoing pin
            Notification pin = new Notification.Builder(this, "pinned_silent")
                .setContentTitle("Message from your Lion")
                .setContentText(message)
                .setStyle(new Notification.BigTextStyle().bigText(message))
                .setSmallIcon(android.R.drawable.ic_dialog_email)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build();
            nm.notify(300, pin);
        } catch (Exception e) { Log.e(TAG, "Show pinned", e); }
    }

    private void showGeofenceNotification(NotificationManager nm) {
        try {
            NotificationChannel ch = new NotificationChannel(
                "geofence", "Geofence", NotificationManager.IMPORTANCE_LOW);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "geofence")
                .setContentTitle("You are confined to home")
                .setContentText("Stay within the geofence zone.")
                .setSmallIcon(android.R.drawable.ic_dialog_map)
                .setOngoing(true)
                .build();
            nm.notify(302, n);
        } catch (Exception e) { Log.e(TAG, "Geofence notification", e); }
    }

    private void showCheckinReminder(String message) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "checkin", "Check-In Reminders", NotificationManager.IMPORTANCE_HIGH);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "checkin")
                .setContentTitle("Daily Check-In")
                .setContentText(message)
                .setStyle(new Notification.BigTextStyle().bigText(message))
                .setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .build();
            nm.notify(303, n);
        } catch (Exception e) { Log.e(TAG, "Checkin reminder", e); }
    }

    private void showPaywallAlert(String newAmount, String oldAmount) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "paywall_alert", "Paywall Changes", NotificationManager.IMPORTANCE_HIGH);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            nm.createNotificationChannel(ch);
            String title = "Paywall updated: $" + newAmount;
            String text;
            try {
                int diff = Integer.parseInt(newAmount) - Integer.parseInt(oldAmount.isEmpty() ? "0" : oldAmount);
                text = diff > 0 ? "+$" + diff + " added to your balance" : "$" + Math.abs(diff) + " reduced";
            } catch (Exception e) { text = "Balance changed"; }
            Notification n = new Notification.Builder(this, "paywall_alert")
                .setContentTitle(title)
                .setContentText(text)
                .setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setAutoCancel(true)
                .build();
            nm.notify(311, n);
        } catch (Exception e) { Log.e(TAG, "Paywall alert", e); }
    }

    private void showPaywallPersistent(String amount) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "paywall_ongoing", "Outstanding Balance", NotificationManager.IMPORTANCE_LOW);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            ch.setSound(null, null);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "paywall_ongoing")
                .setContentTitle("Outstanding balance: $" + amount)
                .setContentText("Pay via e-Transfer to clear.")
                .setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .setOnlyAlertOnce(true)
                .build();
            nm.notify(310, n);
        } catch (Exception e) { Log.e(TAG, "Paywall persistent", e); }
    }

    private void showPaywallCleared() {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "paywall_alert", "Paywall Changes", NotificationManager.IMPORTANCE_HIGH);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "paywall_alert")
                .setContentTitle("Balance cleared!")
                .setContentText("All paid up. Good bunny.")
                .setSmallIcon(android.R.drawable.ic_dialog_info)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setAutoCancel(true)
                .build();
            nm.notify(311, n);
        } catch (Exception e) { Log.e(TAG, "Paywall cleared", e); }
    }

    private void showSubReminder(String tier, int amount, long hoursLeft) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "sub_reminder", "Subscription Reminders", NotificationManager.IMPORTANCE_HIGH);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            nm.createNotificationChannel(ch);
            String timeStr = hoursLeft > 24 ? (hoursLeft / 24) + " days" :
                             hoursLeft > 1 ? hoursLeft + " hours" : "less than an hour";
            Notification n = new Notification.Builder(this, "sub_reminder")
                .setContentTitle(tier.toUpperCase() + " payment due in " + timeStr)
                .setContentText("$" + amount + " — pay via e-Transfer to avoid penalties.")
                .setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setAutoCancel(true)
                .build();
            nm.notify(312, n);
        } catch (Exception e) { Log.e(TAG, "Sub reminder", e); }
    }

    private String gstr(String key) {
        String v = Settings.Global.getString(getContentResolver(), key);
        return (v == null || v.equals("null")) ? "" : v;
    }

    @Override
    public IBinder onBind(Intent intent) { return null; }

    @Override
    public void onDestroy() {
        running = false;
        super.onDestroy();
    }
}

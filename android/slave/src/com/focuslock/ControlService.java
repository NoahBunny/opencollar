package com.focuslock;

import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.Service;
import android.content.Intent;
import android.os.IBinder;
import android.provider.Settings;
import android.util.Log;

import android.app.PendingIntent;
import android.app.admin.DevicePolicyManager;
import android.content.ComponentName;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.io.OutputStream;
import java.math.BigInteger;
import java.net.InetSocketAddress;
import java.net.ServerSocket;
import java.net.Socket;
import java.net.InetAddress;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.KeyStore;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.security.cert.Certificate;
import java.util.Date;
import javax.net.ssl.KeyManagerFactory;
import javax.net.ssl.SSLContext;
import javax.net.ssl.SSLServerSocket;
import javax.net.ssl.SSLServerSocketFactory;

public class ControlService extends Service {

    private static final String TAG = "FocusLock";
    private static final int PORT = 8432;
    private ServerSocket serverSocket;
    private boolean running = false;
    private android.speech.tts.TextToSpeech tts;

    // ── Mesh State ──
    private final java.util.concurrent.atomic.AtomicLong meshVersion = new java.util.concurrent.atomic.AtomicLong(0);
    private final java.util.concurrent.ConcurrentHashMap<String, String[]> meshPeers = new java.util.concurrent.ConcurrentHashMap<>();
    private final java.util.concurrent.ConcurrentHashMap<String, Long> meshPeerLastSeen = new java.util.concurrent.ConcurrentHashMap<>();
    // meshPeers: nodeId -> [type, addr, portStr, scheme?]  (scheme defaults to "http" if absent)

    // ── Phase D: vault-only mode ──
    // Set when the relay returns 410 Gone for /api/mesh/{id}/sync, telling us
    // the mesh is in vault_only mode. After this flips, plaintext meshGossip()
    // becomes a no-op for server peers and the slave only writes runtime blobs
    // via vaultRuntimePush(). Re-checked periodically in case the flag is
    // flipped back off server-side.
    private volatile boolean vaultOnlyDetected = false;
    // SHA256 of the last successfully-pushed runtime body. Skip the next push
    // if the body hasn't changed — RSA encryption per recipient is expensive.
    private volatile String lastRuntimeBodyHash = "";
    // Cached vault keypair — avoids KeyStore IPC on every tick
    private volatile byte[] cachedNodePubDer = null;
    private volatile java.security.PrivateKey cachedNodePrivKey = null;
    private static final String[] MESH_ORDER_KEYS = {
        "lock_active", "desktop_active", "desktop_locked_devices", "message", "desktop_message",
        "task_text", "task_orig", "task_randcaps", "task_reps", "task_done", "mode",
        "paywall", "paywall_original", "compliment", "word_min", "exercise",
        "vibrate", "penalty", "shame", "dim", "mute", "unlock_at", "locked_at",
        "offer", "offer_status", "offer_response", "offer_time",
        "geofence_lat", "geofence_lon", "geofence_radius_m",
        "pinned_message", "sub_tier", "sub_due", "sub_total_owed",
        "checkin_deadline", "free_unlocks", "free_unlock_reset",
        "settings_allowed", "notif_email_evidence", "notif_email_escape", "notif_email_breach",
        "photo_task", "photo_hint",
        "curfew_enabled", "curfew_confine_hour", "curfew_release_hour",
        "curfew_radius_m", "curfew_lat", "curfew_lon",
        "fine_active", "fine_amount", "fine_interval_m", "fine_last_applied",
        "body_check_active", "body_check_area", "body_check_interval_h",
        "body_check_last", "body_check_streak", "body_check_last_result", "body_check_baseline",
        "countdown_lock_at", "countdown_message",
        "bedtime_enabled", "bedtime_lock_hour", "bedtime_unlock_hour",
        "screen_time_quota_minutes", "screen_time_reset_hour",
    };

    @Override
    public void onCreate() {
        super.onCreate();
        Log.w(TAG, "ControlService.onCreate — starting HTTP server + jail watcher");

        NotificationChannel ch = new NotificationChannel(
            "control", "The Collar", NotificationManager.IMPORTANCE_LOW);
        ch.setShowBadge(false);
        getSystemService(NotificationManager.class).createNotificationChannel(ch);
        Notification n = new Notification.Builder(this, "control")
            .setContentTitle("The Collar")
            .setContentText("Always on")
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setOngoing(true).build();
        startForeground(1, n);

        // Initialize TTS for talk-through-mic
        tts = new android.speech.tts.TextToSpeech(this, status -> {
            if (status == android.speech.tts.TextToSpeech.SUCCESS) {
                tts.setLanguage(java.util.Locale.US);
                Log.i(TAG, "TTS initialized");
            } else {
                Log.e(TAG, "TTS init failed: " + status);
            }
        });

        // Keep ADB enabled
        try {
            Settings.Global.putInt(getContentResolver(), "adb_enabled", 1);
            Settings.Global.putInt(getContentResolver(), "development_settings_enabled", 1);
            Settings.Global.putInt(getContentResolver(), "adb_wifi_enabled", 1);
        } catch (Exception e) {
            Log.e(TAG, "Failed to enable ADB settings", e);
        }

        // Try to re-enable ADB TCP mode (best effort)
        try {
            Runtime.getRuntime().exec(new String[]{"setprop", "service.adb.tcp.port", "5555"});
        } catch (Exception e) {}

        startServer();
        startJailWatcher();
        initMesh();

        // Do NOT launch FocusActivity here on boot — it crashes the system.
        // The bridge handles re-engaging the jail via ADB once it detects the phone is back.
        // The jail watcher will also pick it up after the boot grace period.
        int active = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
        if (active == 1) {
            Log.w(TAG, "Lock was active before reboot — bridge will re-engage jail via ADB");
        }

        // Failsafe: schedule repeating alarm to restart this service every 5 minutes
        try {
            android.app.AlarmManager am = (android.app.AlarmManager) getSystemService(ALARM_SERVICE);
            Intent restartIntent = new Intent(this, ControlService.class);
            android.app.PendingIntent pi = android.app.PendingIntent.getForegroundService(
                this, 42, restartIntent,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT | android.app.PendingIntent.FLAG_IMMUTABLE);
            am.setRepeating(android.app.AlarmManager.RTC_WAKEUP,
                System.currentTimeMillis() + 300000, 300000, pi);
            Log.i(TAG, "Failsafe alarm scheduled (every 5 min)");
        } catch (Exception e) {
            Log.e(TAG, "Failed to schedule failsafe alarm", e);
        }

        Log.w(TAG, "ControlService fully initialized");
    }

    /** Poll lock flag + self-check servers every 2s. */
    private void startJailWatcher() {
        Thread watcher = new Thread(() -> {
            boolean wasLocked = false;
            int healthCounter = 0;
            long bootTime = System.currentTimeMillis();
            while (running) {
                try {
                    Thread.sleep(2000);

                    // Check lock flag
                    int active = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
                    if (active == 1 && !wasLocked) {
                        // Grace period: don't launch FocusActivity in first 30s after boot
                        // — the bridge handles re-engagement via ADB, which is reliable
                        long elapsed = System.currentTimeMillis() - bootTime;
                        if (elapsed < 30000) {
                            Log.i(TAG, "Lock flag detected but still in boot grace period (" + elapsed + "ms) — bridge will handle");
                        } else {
                            Log.w(TAG, "Lock flag detected, activating jail");
                            enforceEscapeHatches();
                            launchFocus();
                            wasLocked = true;
                        }
                    } else if (active == 1 && wasLocked) {
                        // Re-enforce every cycle in case user re-enabled something
                        enforceEscapeHatches();
                    } else if (active == 0 && wasLocked) {
                        wasLocked = false;
                    }

                    // Countdown to lock — fires the lock at countdown_lock_at, with notification warnings
                    long countdownAt = Settings.Global.getLong(getContentResolver(), "focus_lock_countdown_lock_at", 0);
                    if (countdownAt > 0 && active == 0) {
                        long nowMs = System.currentTimeMillis();
                        long remainingMs = countdownAt - nowMs;
                        if (remainingMs <= 0) {
                            // Countdown expired — engage the lock
                            Log.w(TAG, "Countdown expired — locking");
                            String cdMsg = gstr("focus_lock_countdown_message");
                            Settings.Global.putString(getContentResolver(), "focus_lock_message",
                                cdMsg.isEmpty() ? "Countdown reached zero." : cdMsg);
                            Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
                            Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at", nowMs);
                            Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_lock_at", 0);
                            Settings.Global.putString(getContentResolver(), "focus_lock_countdown_message", "");
                        } else {
                            // Send warning notifications at threshold crossings
                            long lastWarnAt = Settings.Global.getLong(getContentResolver(), "focus_lock_countdown_last_warn", 0);
                            long lastWarnTier = Settings.Global.getLong(getContentResolver(), "focus_lock_countdown_warn_tier", 0);
                            // Warning tiers (in ms remaining): 60min, 30min, 10min, 5min, 1min, 30s
                            long[] tiers = {3600000L, 1800000L, 600000L, 300000L, 60000L, 30000L};
                            String[] tierLabels = {"1 hour", "30 minutes", "10 minutes", "5 minutes", "1 minute", "30 seconds"};
                            for (int t = 0; t < tiers.length; t++) {
                                if (remainingMs <= tiers[t] && lastWarnTier < tiers[t] && lastWarnTier != -tiers[t]) {
                                    String cdMsg = gstr("focus_lock_countdown_message");
                                    showCountdownNotification(tierLabels[t], cdMsg);
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_warn_tier", -tiers[t]);
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_last_warn", nowMs);
                                    break;
                                }
                            }
                        }
                    } else if (countdownAt == 0) {
                        // Reset warn tier when countdown is cleared
                        Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_warn_tier", 0);
                    }

                    // Health check every 30s (15 cycles of 2s)
                    healthCounter++;
                    if (healthCounter >= 15) {
                        healthCounter = 0;
                        boolean port1Ok = probeLocal(PORT);
                        boolean port2Ok = probeLocal(HTTP_PORT);
                        if (!port1Ok || !port2Ok) {
                            Log.w(TAG, "Server health check FAILED (8432=" + port1Ok + " 8433=" + port2Ok + ") — restarting servers");
                            startServer();
                        }
                        // Re-enable ADB settings every health check
                        try {
                            Settings.Global.putInt(getContentResolver(), "adb_enabled", 1);
                            Settings.Global.putInt(getContentResolver(), "adb_wifi_enabled", 1);
                        } catch (Exception e) {}

                        // Keep Tailscale alive — if VPN is down, relaunch silently
                        if (!isTailscaleUp()) {
                            try {
                                Intent ts = new Intent();
                                ts.setClassName("com.tailscale.ipn", "com.tailscale.ipn.IPNActivity");
                                ts.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_NO_ANIMATION);
                                startActivity(ts);
                                Log.i(TAG, "Tailscale was down — relaunched");
                            } catch (Exception e) {
                                // Tailscale not installed — skip silently
                            }
                        }

                        // Compound interest on paywall — rate depends on subscription tier
                        // Bronze/none: 10%/hr, Silver: 5%/hr, Gold: 0%
                        int lockActive2 = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
                        if (lockActive2 == 1) {
                            String subTierCI = gstr("focus_lock_sub_tier");
                            double interestRate = 1.10; // 10% default
                            if ("silver".equals(subTierCI)) interestRate = 1.05;
                            else if ("gold".equals(subTierCI)) interestRate = 1.0; // no interest
                            if (interestRate > 1.0) {
                                String pwStr = gstr("focus_lock_paywall");
                                String origStr = gstr("focus_lock_paywall_original");
                                if (!pwStr.isEmpty() && !pwStr.equals("0") && !origStr.isEmpty() && !origStr.equals("0")) {
                                    try {
                                        double original = Double.parseDouble(origStr);
                                        long lockedAt = Settings.Global.getLong(getContentResolver(), "focus_lock_locked_at", 0);
                                        if (lockedAt > 0 && original > 0) {
                                            double hours = (System.currentTimeMillis() - lockedAt) / 3600000.0;
                                            double compounded = original * Math.pow(interestRate, hours);
                                            double currentPw = Double.parseDouble(pwStr);
                                            if (compounded > currentPw) {
                                                Settings.Global.putString(getContentResolver(), "focus_lock_paywall",
                                                    String.format("%.0f", compounded));
                                            }
                                        }
                                    } catch (Exception e) {}
                                }
                            }
                        }

                        // Geofence check
                        try {
                            String fenceLat = gstr("focus_lock_geofence_lat");
                            String fenceLon = gstr("focus_lock_geofence_lon");
                            if (!fenceLat.isEmpty() && !fenceLon.isEmpty()) {
                                android.location.LocationManager lm = (android.location.LocationManager) getSystemService(LOCATION_SERVICE);
                                android.location.Location loc = lm.getLastKnownLocation(android.location.LocationManager.NETWORK_PROVIDER);
                                if (loc != null) {
                                    double lat = loc.getLatitude(), lon = loc.getLongitude();
                                    double flatd = Double.parseDouble(fenceLat), flond = Double.parseDouble(fenceLon);
                                    String fenceRad = gstr("focus_lock_geofence_radius_m");
                                    double radius = fenceRad.isEmpty() ? 100.0 : Double.parseDouble(fenceRad);
                                    float[] dist = new float[1];
                                    android.location.Location.distanceBetween(lat, lon, flatd, flond, dist);
                                    long lastBreach = Settings.Global.getLong(getContentResolver(), "focus_lock_geofence_breach_at", 0);
                                    boolean cooldownActive = (System.currentTimeMillis() - lastBreach) < 300000; // 5 min
                                    if (dist[0] > radius && Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0) == 0 && !cooldownActive) {
                                        Log.w(TAG, "GEOFENCE BREACH: " + dist[0] + "m from center");
                                        Settings.Global.putLong(getContentResolver(), "focus_lock_geofence_breach_at", System.currentTimeMillis());
                                        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
                                        Settings.Global.putString(getContentResolver(), "focus_lock_message",
                                            "Geofence breach. " + String.format("%.0f", dist[0]) + "m outside zone.");
                                        Settings.Global.putString(getContentResolver(), "focus_lock_paywall", "100");
                                        Settings.Global.putString(getContentResolver(), "focus_lock_paywall_original", "100");
                                        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "basic");
                                        Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at", System.currentTimeMillis());
                                        launchFocus();
                                        reportGeofenceBreach(lat, lon, dist[0]);
                                    }
                                    reportLocation(lat, lon);
                                }
                            }
                        } catch (SecurityException e) {
                            Log.w(TAG, "Location permission not granted");
                        } catch (Exception e) {}

                        // Curfew check — auto-set/clear geofence based on time
                        try {
                            int curfewEnabled = Settings.Global.getInt(getContentResolver(), "focus_lock_curfew_enabled", 0);
                            if (curfewEnabled == 1) {
                                int confineHour = Settings.Global.getInt(getContentResolver(), "focus_lock_curfew_confine_hour", -1);
                                int releaseHour = Settings.Global.getInt(getContentResolver(), "focus_lock_curfew_release_hour", -1);
                                int currentHour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
                                boolean inCurfew;
                                if (confineHour <= releaseHour) {
                                    inCurfew = currentHour >= confineHour && currentHour < releaseHour;
                                } else {
                                    inCurfew = currentHour >= confineHour || currentHour < releaseHour;
                                }
                                String fenceLatNow = gstr("focus_lock_geofence_lat");
                                if (inCurfew && fenceLatNow.isEmpty()) {
                                    // Time to confine — set geofence
                                    String cLat = gstr("focus_lock_curfew_lat");
                                    String cLon = gstr("focus_lock_curfew_lon");
                                    String cRad = gstr("focus_lock_curfew_radius_m");
                                    if (cRad.isEmpty()) cRad = "100";
                                    if (cLat.isEmpty() || cLon.isEmpty()) {
                                        // Use current location
                                        try {
                                            android.location.LocationManager lm2 = (android.location.LocationManager) getSystemService(LOCATION_SERVICE);
                                            android.location.Location loc2 = lm2.getLastKnownLocation(android.location.LocationManager.NETWORK_PROVIDER);
                                            if (loc2 != null) {
                                                cLat = String.valueOf(loc2.getLatitude());
                                                cLon = String.valueOf(loc2.getLongitude());
                                            }
                                        } catch (Exception e2) {}
                                    }
                                    if (!cLat.isEmpty() && !cLon.isEmpty()) {
                                        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lat", cLat);
                                        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lon", cLon);
                                        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_radius_m", cRad);
                                        Log.w(TAG, "CURFEW: Geofence set at " + cLat + "," + cLon + " r=" + cRad);
                                    }
                                } else if (!inCurfew && !fenceLatNow.isEmpty()) {
                                    // Curfew over — release geofence
                                    Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lat", "");
                                    Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lon", "");
                                    Settings.Global.putString(getContentResolver(), "focus_lock_geofence_radius_m", "");
                                    Log.w(TAG, "CURFEW: Geofence released");
                                }
                            }
                        } catch (Exception e) {}

                        // Bedtime enforcement — auto-lock/unlock by hour (separate from curfew)
                        try {
                            int bedtimeEnabled = Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_enabled", 0);
                            if (bedtimeEnabled == 1) {
                                int lockHour = Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_lock_hour", -1);
                                int unlockHour = Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_unlock_hour", -1);
                                int currentHour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
                                boolean inBedtime;
                                if (lockHour <= unlockHour) {
                                    inBedtime = currentHour >= lockHour && currentHour < unlockHour;
                                } else {
                                    inBedtime = currentHour >= lockHour || currentHour < unlockHour;
                                }
                                int lockActive = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
                                int bedtimeLocked = Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_locked", 0);
                                if (inBedtime && lockActive == 0) {
                                    // Bedtime — auto-lock
                                    Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
                                    Settings.Global.putString(getContentResolver(), "focus_lock_message", "Bedtime. Go to sleep.");
                                    Settings.Global.putString(getContentResolver(), "focus_lock_mode", "basic");
                                    Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_locked", 1);
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at", System.currentTimeMillis());
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
                                    Log.w(TAG, "BEDTIME: Auto-locked at hour " + currentHour);
                                    launchFocus();
                                } else if (!inBedtime && lockActive == 1 && bedtimeLocked == 1) {
                                    // Wake time — auto-unlock (only if it was a bedtime lock)
                                    Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
                                    Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_locked", 0);
                                    Settings.Global.putString(getContentResolver(), "focus_lock_message", "Good morning.");
                                    Log.w(TAG, "BEDTIME: Auto-unlocked at hour " + currentHour);
                                } else if (!inBedtime && bedtimeLocked == 1) {
                                    // Clear stale bedtime flag
                                    Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_locked", 0);
                                }
                            }
                        } catch (Exception e) {}

                        // Subscription auto-charge check
                        try {
                            String subTier = gstr("focus_lock_sub_tier");
                            long subDue = Settings.Global.getLong(getContentResolver(), "focus_lock_sub_due", 0);
                            if (!subTier.isEmpty() && subDue > 0 && System.currentTimeMillis() >= subDue) {
                                // Tribute is due — add to paywall
                                int amount = 0;
                                if ("bronze".equals(subTier)) amount = 25;
                                else if ("silver".equals(subTier)) amount = 35;
                                else if ("gold".equals(subTier)) amount = 50;
                                if (amount > 0) {
                                    String pw = gstr("focus_lock_paywall");
                                    int currentPw = 0;
                                    try { currentPw = Integer.parseInt(pw); } catch (Exception e2) {}
                                    Settings.Global.putString(getContentResolver(), "focus_lock_paywall",
                                        String.valueOf(currentPw + amount));
                                    // Set next due date (7 days)
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_sub_due",
                                        System.currentTimeMillis() + 7L * 24 * 3600 * 1000);
                                    // Track total owed
                                    long totalOwed = Settings.Global.getLong(getContentResolver(), "focus_lock_sub_total_owed", 0);
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_sub_total_owed", totalOwed + amount);
                                    Log.i(TAG, "Subscription auto-charge: $" + amount + " (" + subTier + ")");
                                    // Notify homelab
                                    reportSubscriptionCharge(subTier, amount);
                                }
                            }
                        } catch (Exception e) {}

                        // Screen time leash — track cumulative unlocked minutes, auto-lock on quota
                        try {
                            int quota = Settings.Global.getInt(getContentResolver(), "focus_lock_screen_time_quota_minutes", 0);
                            if (quota > 0) {
                                int stActive = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
                                int resetHour = Settings.Global.getInt(getContentResolver(), "focus_lock_screen_time_reset_hour", 0);
                                int currentHour = java.util.Calendar.getInstance().get(java.util.Calendar.HOUR_OF_DAY);
                                String resetDate = gstr("focus_lock_screen_time_reset_date");
                                String today = new java.text.SimpleDateFormat("yyyy-MM-dd", java.util.Locale.US)
                                    .format(new java.util.Date());

                                // Reset at configured hour (or midnight) on date change
                                if (!today.equals(resetDate) && currentHour >= resetHour) {
                                    Settings.Global.putInt(getContentResolver(), "focus_lock_screen_time_used_today", 0);
                                    Settings.Global.putString(getContentResolver(), "focus_lock_screen_time_reset_date", today);
                                }

                                if (stActive == 0) {
                                    // Phone is unlocked — accumulate time
                                    long lastCheck = Settings.Global.getLong(getContentResolver(), "focus_lock_screen_time_last_check", 0);
                                    long now = System.currentTimeMillis();
                                    if (lastCheck > 0) {
                                        int elapsed = (int) ((now - lastCheck) / 60000);
                                        if (elapsed > 0) {
                                            int used = Settings.Global.getInt(getContentResolver(), "focus_lock_screen_time_used_today", 0);
                                            used += elapsed;
                                            Settings.Global.putInt(getContentResolver(), "focus_lock_screen_time_used_today", used);
                                            if (used >= quota) {
                                                // Quota exceeded — auto-lock
                                                Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
                                                Settings.Global.putString(getContentResolver(), "focus_lock_message",
                                                    "Screen time quota exceeded (" + quota + " min)");
                                                Settings.Global.putString(getContentResolver(), "focus_lock_mode", "basic");
                                                Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at", now);
                                                Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
                                                Log.w(TAG, "SCREEN TIME: Quota exceeded (" + used + "/" + quota + " min) — locked");
                                                launchFocus();
                                            }
                                        }
                                    }
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_screen_time_last_check", now);
                                } else {
                                    // Locked — don't count time, but keep last_check fresh for next unlock
                                    Settings.Global.putLong(getContentResolver(), "focus_lock_screen_time_last_check",
                                        System.currentTimeMillis());
                                }
                            }
                        } catch (Exception e) {}

                        // Phone home: report current IPs to homelab
                        phoneHome();
                    }
                } catch (Exception e) {
                    Log.e(TAG, "Watcher error", e);
                }
            }
        });
        watcher.setDaemon(true);
        watcher.start();
    }

    private ServerSocket createTlsServerSocket() throws Exception {
        // Generate self-signed cert
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048);
        KeyPair kp = kpg.generateKeyPair();

        // Build self-signed X509 cert manually using DER encoding
        byte[] cert = buildSelfSignedCert(kp);
        java.security.cert.CertificateFactory cf = java.security.cert.CertificateFactory.getInstance("X.509");
        X509Certificate x509 = (X509Certificate) cf.generateCertificate(
            new java.io.ByteArrayInputStream(cert));

        KeyStore ks = KeyStore.getInstance(KeyStore.getDefaultType());
        ks.load(null, null);
        ks.setKeyEntry("focuslock", kp.getPrivate(), "".toCharArray(), new Certificate[]{x509});

        KeyManagerFactory kmf = KeyManagerFactory.getInstance(KeyManagerFactory.getDefaultAlgorithm());
        kmf.init(ks, "".toCharArray());

        SSLContext ctx = SSLContext.getInstance("TLS");
        ctx.init(kmf.getKeyManagers(), null, new SecureRandom());
        SSLServerSocketFactory factory = ctx.getServerSocketFactory();
        return factory.createServerSocket(PORT, 10, InetAddress.getByName("0.0.0.0"));
    }

    /** Build a minimal self-signed X.509 v3 DER certificate. */
    private byte[] buildSelfSignedCert(KeyPair kp) throws Exception {
        // TBS Certificate
        byte[] version = asn1Explicit(0, asn1Int(2)); // v3
        byte[] serial = asn1Int(1);
        byte[] sigAlgo = asn1Seq(concat(new byte[]{0x06,0x09,0x2a,(byte)0x86,0x48,(byte)0x86,(byte)0xf7,0x0d,0x01,0x01,0x0b}, new byte[]{0x05,0x00})); // SHA256withRSA
        byte[] issuer = asn1Seq(asn1Set(asn1Seq(concat(new byte[]{0x06,0x03,0x55,0x04,0x03}, asn1Utf8("FocusLock")))));
        long now = System.currentTimeMillis();
        byte[] validity = asn1Seq(concat(asn1Time(now), asn1Time(now + 10L*365*24*3600*1000)));
        byte[] subject = issuer;
        byte[] pubKeyInfo = kp.getPublic().getEncoded(); // already DER SubjectPublicKeyInfo
        byte[] tbs = asn1Seq(concat(concat(concat(concat(concat(version, serial), sigAlgo), issuer), validity), concat(subject, pubKeyInfo)));

        // Sign TBS
        java.security.Signature sig = java.security.Signature.getInstance("SHA256withRSA");
        sig.initSign(kp.getPrivate());
        sig.update(tbs);
        byte[] signature = sig.sign();
        byte[] sigBits = new byte[signature.length + 1];
        sigBits[0] = 0; // no unused bits
        System.arraycopy(signature, 0, sigBits, 1, signature.length);

        return asn1Seq(concat(concat(tbs, sigAlgo), asn1Tag((byte)0x03, sigBits)));
    }

    private byte[] asn1Seq(byte[] c) { return asn1Tag((byte)0x30, c); }
    private byte[] asn1Set(byte[] c) { return asn1Tag((byte)0x31, c); }
    private byte[] asn1Utf8(String s) { return asn1Tag((byte)0x0c, s.getBytes()); }
    private byte[] asn1Int(long v) { return asn1Tag((byte)0x02, new byte[]{(byte)v}); }
    private byte[] asn1Explicit(int tag, byte[] c) { return asn1Tag((byte)(0xa0 | tag), c); }

    private byte[] asn1Time(long ms) {
        java.text.SimpleDateFormat sdf = new java.text.SimpleDateFormat("yyyyMMddHHmmss'Z'");
        sdf.setTimeZone(java.util.TimeZone.getTimeZone("UTC"));
        String t = sdf.format(new Date(ms));
        return asn1Tag((byte)0x18, t.getBytes()); // GeneralizedTime
    }

    private byte[] asn1Tag(byte tag, byte[] content) {
        byte[] len;
        if (content.length < 128) { len = new byte[]{(byte) content.length}; }
        else if (content.length < 256) { len = new byte[]{(byte)0x81, (byte)content.length}; }
        else { len = new byte[]{(byte)0x82, (byte)(content.length >> 8), (byte)(content.length & 0xFF)}; }
        byte[] r = new byte[1 + len.length + content.length];
        r[0] = tag;
        System.arraycopy(len, 0, r, 1, len.length);
        System.arraycopy(content, 0, r, 1 + len.length, content.length);
        return r;
    }

    private byte[] concat(byte[] a, byte[] b) {
        byte[] c = new byte[a.length + b.length];
        System.arraycopy(a, 0, c, 0, a.length);
        System.arraycopy(b, 0, c, a.length, b.length);
        return c;
    }

    private static final int HTTP_PORT = 8433; // plain HTTP fallback
    private ServerSocket httpServerSocket;

    private void startServer() {
        running = true;
        // HTTP server on PORT (8432)
        Thread mainThread = new Thread(() -> {
            while (running) {
                try {
                    if (serverSocket != null) try { serverSocket.close(); } catch (Exception e) {}
                    serverSocket = new ServerSocket(PORT, 10, InetAddress.getByName("0.0.0.0"));
                    Log.i(TAG, "HTTP server listening on port " + PORT);
                    while (running) {
                        try {
                            Socket c = serverSocket.accept();
                            new Thread(() -> handle(c)).start();
                        } catch (Exception e) {
                            if (running) break;
                        }
                    }
                } catch (Exception e) {
                    Log.e(TAG, "Server error, retry in 3s", e);
                }
                if (running) try { Thread.sleep(3000); } catch (Exception e) {}
            }
        });
        mainThread.setDaemon(true);
        mainThread.start();

        // Backup HTTP server on HTTP_PORT (8433)
        Thread httpThread = new Thread(() -> {
            while (running) {
                try {
                    if (httpServerSocket != null) try { httpServerSocket.close(); } catch (Exception e) {}
                    httpServerSocket = new ServerSocket(HTTP_PORT, 10, InetAddress.getByName("0.0.0.0"));
                    Log.i(TAG, "HTTP server listening on port " + HTTP_PORT);
                    while (running) {
                        try {
                            Socket c = httpServerSocket.accept();
                            new Thread(() -> handle(c)).start();
                        } catch (Exception e) {
                            if (running) break;
                        }
                    }
                } catch (Exception e) {
                    Log.e(TAG, "HTTP server error, retry in 3s", e);
                }
                if (running) try { Thread.sleep(3000); } catch (Exception e) {}
            }
        });
        httpThread.setDaemon(true);
        httpThread.start();
    }

    private void handle(Socket c) {
        try {
            c.setSoTimeout(5000);
            BufferedReader r = new BufferedReader(new InputStreamReader(c.getInputStream()));
            OutputStream out = c.getOutputStream();
            String req = r.readLine();
            if (req == null) { c.close(); return; }

            int clen = 0;
            String line;
            while ((line = r.readLine()) != null && !line.isEmpty()) {
                if (line.toLowerCase().startsWith("content-length:"))
                    clen = Integer.parseInt(line.substring(15).trim());
            }
            String body = "";
            if (clen > 0) { char[] buf = new char[clen]; r.read(buf, 0, clen); body = new String(buf); }

            String[] p = req.split(" ");
            String fullPath = p.length > 1 ? p[1] : "/";
            String method = p[0], path = fullPath.contains("?") ? fullPath.substring(0, fullPath.indexOf("?")) : fullPath;

            if (method.equals("OPTIONS")) {
                out.write(("HTTP/1.1 204 No Content\r\nAccess-Control-Allow-Origin: *\r\n"
                    + "Access-Control-Allow-Methods: GET,POST,OPTIONS\r\n"
                    + "Access-Control-Allow-Headers: Content-Type\r\n\r\n").getBytes());
                out.flush(); c.close(); return;
            }

            // Auth: RSA signatures only. PIN auth removed — only Lion's Share
            // private key can issue orders. API endpoints are open on the LAN
            // (same trust model as ADB over TCP).

            String ct = "application/json";
            String resp;
            int code = 200;

            if (path.equals("/") || path.equals("/index.html")) { ct = "text/html"; resp = webUI(); }
            else if (path.equals("/manifest.json")) { resp = "{\"name\":\"Lion's Share\",\"short_name\":\"Lion's Share\",\"start_url\":\"/\",\"display\":\"standalone\",\"background_color\":\"#0a0a14\",\"theme_color\":\"#0a0a14\"}"; }
            else if (path.equals("/api/ping")) { resp = "{\"ok\":true}"; }
            else if (path.equals("/api/adb-port")) { resp = doAdbPort(); }
            else if (path.equals("/api/status")) { resp = doStatus(); }
            else if (path.equals("/api/lock") && method.equals("POST")) { resp = doLock(body); }
            else if (path.equals("/api/unlock") && method.equals("POST")) { resp = doUnlock(); }
            else if (path.equals("/api/message") && method.equals("POST")) { resp = doMessage(body); }
            else if (path.equals("/api/task") && method.equals("POST")) { resp = doTask(body); }
            else if (path.equals("/api/power") && method.equals("POST")) { resp = doPower(body); }
            // /api/set-pin removed — RSA auth only, no PINs
            else if (path.equals("/api/offer") && method.equals("POST")) { resp = doOffer(body); }
            else if (path.equals("/api/offer-respond") && method.equals("POST")) { resp = doOfferRespond(body); }
            else if (path.equals("/api/add-paywall") && method.equals("POST")) { resp = doAddPaywall(body); }
            else if (path.equals("/api/enable-settings") && method.equals("POST")) {
                Settings.Global.putInt(getContentResolver(), "focus_lock_settings_allowed", 1);
                resp = "{\"ok\":true,\"action\":\"settings_enabled_5min\"}";
            }
            else if (path.equals("/api/entrap") && method.equals("POST")) {
                resp = doEntrap(body);
            }
            else if (path.equals("/api/clear-paywall") && method.equals("POST")) { resp = doClearPaywall(); }
            else if (path.equals("/api/gamble") && method.equals("POST")) { resp = doGamble(); }
            else if (path.equals("/api/play-audio") && method.equals("POST")) { resp = doPlayAudio(body); }
            else if (path.equals("/api/set-geofence") && method.equals("POST")) { resp = doSetGeofence(body); }
            else if (path.equals("/api/pin-message") && method.equals("POST")) { resp = doPinMessage(body); }
            else if (path.equals("/api/subscribe") && method.equals("POST")) { resp = doSubscribe(body); }
            else if (path.equals("/api/unsubscribe") && method.equals("POST")) { resp = doUnsubscribe(); }
            else if (path.equals("/api/free-unlock") && method.equals("POST")) { resp = doFreeUnlock(); }
            else if (path.equals("/api/lovense") && method.equals("POST")) { resp = doLovense(body); }
            else if (path.equals("/api/photo-task") && method.equals("POST")) { resp = doPhotoTask(body); }
            else if (path.equals("/api/release-forever") && method.equals("POST")) { resp = doReleaseForever(); }
            else if (path.equals("/api/pair") && method.equals("POST")) { resp = doPair(body); }
            else if (path.equals("/api/set-checkin") && method.equals("POST")) { resp = doSetCheckin(body); }
            else if (path.equals("/api/clear-geofence") && method.equals("POST")) { resp = doClearGeofence(); }
            else if (path.equals("/api/set-volume") && method.equals("POST")) { resp = doSetVolume(body); }
            else if (path.equals("/api/set-notif-prefs") && method.equals("POST")) { resp = doSetNotifPrefs(body); }
            else if (path.equals("/api/lock-device") && method.equals("POST")) { resp = doLockDevice(body); }
            else if (path.equals("/api/unlock-device") && method.equals("POST")) { resp = doUnlockDevice(body); }
            else if (path.equals("/api/speak") && method.equals("POST")) { resp = doSpeak(body); }
            // ── Mesh Endpoints ──
            else if (path.equals("/mesh/sync") && method.equals("POST")) { resp = handleMeshSync(body); }
            else if (path.equals("/mesh/order") && method.equals("POST")) { resp = handleMeshOrder(body); }
            else if (path.equals("/mesh/status")) { resp = handleMeshStatus(); }
            else if (path.equals("/mesh/ping")) { resp = handleMeshPing(); }
            else { resp = "{\"error\":\"not found\"}"; code = 404; }

            // Bump mesh version and push on any successful /api/* POST that modifies state
            if (method.equals("POST") && path.startsWith("/api/") && code == 200
                && !path.equals("/api/ping") && !path.equals("/api/status")
                && !path.startsWith("/mesh/")) {
                meshBumpAndPush();
            }

            byte[] rb = resp.getBytes("UTF-8");
            String statusText = code == 200 ? "OK" : code == 403 ? "Forbidden" : code == 404 ? "Not Found" : "Error";
            out.write(("HTTP/1.1 " + code + " " + statusText + "\r\nContent-Type: " + ct
                + "; charset=utf-8\r\nContent-Length: " + rb.length
                + "\r\nAccess-Control-Allow-Origin: *\r\nConnection: close\r\n\r\n").getBytes());
            out.write(rb);
            out.flush(); c.close();
        } catch (Exception e) {
            Log.e(TAG, "Client", e);
            try { c.close(); } catch (Exception x) {}
        }
    }

    private String doStatus() {
        int active = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
        String msg = gstr("focus_lock_message");
        String task = gstr("focus_lock_task_text");
        long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
        long rem = unlockAt > 0 ? Math.max(0, unlockAt - System.currentTimeMillis()) : 0;
        int escapes = Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0);
        String paywall = gstr("focus_lock_paywall");
        String compliment = gstr("focus_lock_compliment");
        int taskReps = Settings.Global.getInt(getContentResolver(), "focus_lock_task_reps", 0);
        int taskDone = Settings.Global.getInt(getContentResolver(), "focus_lock_task_done", 0);
        String mode = gstr("focus_lock_mode");
        String offer = gstr("focus_lock_offer");
        String offerStatus = gstr("focus_lock_offer_status");
        // Current location for Lion's Share "Confine" button
        double curLat = 0, curLon = 0;
        try {
            android.location.LocationManager lm = (android.location.LocationManager) getSystemService(LOCATION_SERVICE);
            android.location.Location loc = lm.getLastKnownLocation(android.location.LocationManager.NETWORK_PROVIDER);
            if (loc != null) { curLat = loc.getLatitude(); curLon = loc.getLongitude(); }
        } catch (Exception e) {}
        return "{\"locked\":" + (active == 1)
            + ",\"message\":\"" + esc(msg)
            + "\",\"task\":\"" + esc(task)
            + "\",\"timer_remaining_ms\":" + rem
            + ",\"escapes\":" + escapes
            + ",\"paywall\":\"" + esc(paywall)
            + "\",\"compliment\":\"" + esc(compliment)
            + "\",\"task_reps\":" + taskReps
            + ",\"task_done\":" + taskDone
            + ",\"mode\":\"" + esc(mode)
            + "\",\"offer\":\"" + esc(offer)
            + "\",\"offer_status\":\"" + esc(offerStatus)
            + "\",\"vibrate\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_vibrate", 0) == 1)
            + ",\"penalty\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_penalty", 0) == 1)
            + ",\"shame\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_shame", 0) == 1)
            + ",\"dim\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_dim", 0) == 1)
            + ",\"mute\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_mute", 0) == 1)
            + ",\"sub_tier\":\"" + esc(gstr("focus_lock_sub_tier"))
            + "\",\"sub_due\":" + Settings.Global.getLong(getContentResolver(), "focus_lock_sub_due", 0)
            + ",\"free_unlocks\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_free_unlocks", 0)
            + ",\"bunny_msg\":\"" + esc(gstr("focus_lock_bunny_last_msg"))
            + "\",\"auth_challenge\":\"" + esc(gstr("focus_lock_auth_challenge"))
            + "\",\"auth_challenge_desc\":\"" + esc(gstr("focus_lock_auth_challenge_desc"))
            + "\",\"checkin_deadline\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_checkin_deadline", -1)
            + ",\"checkin_last\":" + Settings.Global.getLong(getContentResolver(), "focus_lock_checkin_timestamp", 0)
            + ",\"lat\":" + curLat + ",\"lon\":" + curLon
            + ",\"geofence_active\":" + (!gstr("focus_lock_geofence_lat").isEmpty())
            + ",\"geofence_radius\":\"" + esc(gstr("focus_lock_geofence_radius_m"))
            + "\",\"bridge_heartbeat\":" + Settings.Global.getLong(getContentResolver(), "focus_lock_bridge_heartbeat", 0)
            + ",\"desktops\":\"" + esc(gstr("focus_lock_desktops")) + "\""
            + ",\"desktop_locked\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_desktop_active", 0) == 1)
            + ",\"desktop_locked_devices\":\"" + esc(gstr("focus_lock_desktop_locked_devices")) + "\""
            + ",\"fine_active\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_fine_active", 0)
            + ",\"fine_amount\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_fine_amount", 0)
            + ",\"fine_interval_m\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_fine_interval_m", 0)
            + ",\"body_check_active\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_body_check_active", 0)
            + ",\"body_check_area\":\"" + esc(gstr("focus_lock_body_check_area"))
            + "\",\"body_check_streak\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_body_check_streak", 0)
            + ",\"body_check_last_result\":\"" + esc(gstr("focus_lock_body_check_last_result")) + "\""
            + ",\"adb_wifi_port\":" + getAdbWifiPort()
            + ",\"tailscale_up\":" + isTailscaleUp()
            + ",\"entrapped\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_entrapped", 0) == 1)
            + ",\"screen_time_quota_minutes\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_screen_time_quota_minutes", 0)
            + ",\"screen_time_used_today\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_screen_time_used_today", 0)
            + ",\"bedtime_enabled\":" + (Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_enabled", 0) == 1)
            + ",\"bedtime_lock_hour\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_lock_hour", -1)
            + ",\"bedtime_unlock_hour\":" + Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_unlock_hour", -1)
            + "}";
    }

    private int getAdbWifiPort() {
        try {
            int enabled = Settings.Global.getInt(getContentResolver(), "adb_wifi_enabled", 0);
            if (enabled == 0) return 0;
            // Scan /proc/net/tcp6 for listening ports in wireless debug range
            java.io.BufferedReader br = new java.io.BufferedReader(
                new java.io.FileReader("/proc/net/tcp6"));
            String line;
            while ((line = br.readLine()) != null) {
                line = line.trim();
                if (!line.contains(" 0A ")) continue; // LISTEN state
                String[] parts = line.split("\\s+");
                if (parts.length < 2) continue;
                String localAddr = parts[1];
                int ci = localAddr.lastIndexOf(":");
                if (ci < 0) continue;
                int port = Integer.parseInt(localAddr.substring(ci + 1), 16);
                if (port >= 30000 && port <= 50000) {
                    br.close();
                    return port;
                }
            }
            br.close();
        } catch (Exception e) {
            // SELinux may block — fallback to stored value
            try {
                return Settings.Global.getInt(getContentResolver(), "focus_lock_adb_wifi_port", 0);
            } catch (Exception e2) {}
        }
        return 0;
    }

    private boolean isTailscaleUp() {
        try {
            // Check if tailscale VPN interface exists
            java.util.Enumeration<java.net.NetworkInterface> nets = java.net.NetworkInterface.getNetworkInterfaces();
            while (nets.hasMoreElements()) {
                java.net.NetworkInterface ni = nets.nextElement();
                if (ni.getName().startsWith("tun") && ni.isUp()) return true;
            }
        } catch (Exception e) {}
        return false;
    }

    private String doLock(String body) {
        String target = jval(body, "target");
        // Selective device locking: "phone", "desktop", "all" (default), or specific hostname
        if ("desktop".equals(target)) {
            return doLockDesktop(body);
        } else if (target != null && !target.isEmpty() && !"all".equals(target) && !"phone".equals(target)) {
            return doLockSpecificDevice(target, body);
        }
        // "all" or "phone" — lock phone (and desktop if "all")
        if ("all".equals(target) || target == null || target.isEmpty()) {
            Settings.Global.putInt(getContentResolver(), "focus_lock_desktop_active", 1);
        }
        String msg = jval(body, "message");
        String timer = jval(body, "timer");
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
        Settings.Global.putString(getContentResolver(), "focus_lock_message", msg != null ? msg : "");
        Settings.Global.putString(getContentResolver(), "focus_lock_task_text", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", 0);
        if (timer != null && !timer.isEmpty() && !timer.equals("0")) {
            try {
                long mins = Long.parseLong(timer);
                Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at",
                    System.currentTimeMillis() + mins * 60000);
            } catch (NumberFormatException e) {
                Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
            }
        } else {
            Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
        }
        // Toggles from controller
        applyToggle(body, "vibrate", "focus_lock_vibrate");
        applyToggle(body, "penalty", "focus_lock_penalty");
        applyToggle(body, "shame", "focus_lock_shame");
        applyToggle(body, "dim", "focus_lock_dim");
        applyToggle(body, "mute", "focus_lock_mute");
        String paywall = jval(body, "paywall");
        String pw = paywall != null ? paywall : "0";
        Settings.Global.putString(getContentResolver(), "focus_lock_paywall", pw);
        Settings.Global.putString(getContentResolver(), "focus_lock_paywall_original", pw);
        Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at", System.currentTimeMillis());
        String compliment = jval(body, "compliment");
        Settings.Global.putString(getContentResolver(), "focus_lock_compliment",
            compliment != null ? compliment : "");
        String mode = jval(body, "mode");
        if ("random".equals(mode)) {
            String[] modes = {"basic", "negotiation", "gratitude", "exercise", "love_letter"};
            mode = modes[new java.util.Random().nextInt(modes.length)];
        }
        Settings.Global.putString(getContentResolver(), "focus_lock_mode",
            mode != null ? mode : "basic");
        // Mode-specific settings
        String wordMin = jval(body, "word_min");
        Settings.Global.putInt(getContentResolver(), "focus_lock_word_min",
            wordMin != null && !wordMin.isEmpty() ? Integer.parseInt(wordMin) : 50);
        String exercise = jval(body, "exercise");
        Settings.Global.putString(getContentResolver(), "focus_lock_exercise",
            exercise != null && !exercise.isEmpty() ? exercise : "Do 20 pushups");
        // Clear any previous offer
        Settings.Global.putString(getContentResolver(), "focus_lock_offer", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_offer_response", "");
        // Disable camera double-press shortcut
        try {
            Settings.Secure.putInt(getContentResolver(), "camera_double_tap_power_gesture_disabled", 1);
            Settings.Secure.putInt(getContentResolver(), "camera_gesture_disabled", 1);
        } catch (Exception e) {}
        // Save current volumes per-stream — don't change them on lock.
        // Max volume only on specific actions (play-audio, set-volume).
        try {
            android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
            Settings.Global.putInt(getContentResolver(), "focus_lock_saved_volume",
                am.getStreamVolume(android.media.AudioManager.STREAM_MUSIC));
            Settings.Global.putInt(getContentResolver(), "focus_lock_saved_volume_ring",
                am.getStreamVolume(android.media.AudioManager.STREAM_RING));
            Settings.Global.putInt(getContentResolver(), "focus_lock_saved_volume_notif",
                am.getStreamVolume(android.media.AudioManager.STREAM_NOTIFICATION));
        } catch (Exception e) {}
        // Disable escape hatches: status bar (notification shade), launcher (home button), settings
        try {
            Runtime.getRuntime().exec(new String[]{"cmd", "statusbar", "disable-for-setup", "true"});
            Runtime.getRuntime().exec(new String[]{"pm", "disable-user", "--user", "0", "com.android.launcher3"});
            Runtime.getRuntime().exec(new String[]{"pm", "disable-user", "--user", "0", "com.google.android.apps.nexuslauncher"});
            Runtime.getRuntime().exec(new String[]{"pm", "disable-user", "--user", "0", "com.android.settings"});
        } catch (Exception e) { Log.w(TAG, "Escape hatch lockdown failed", e); }
        launchFocus();
        lovenseLockPulse(); // Start Lovense pulsing during lock
        return "{\"ok\":true,\"action\":\"locked\"}";
    }

    private String doUnlock() {
        // Unlock all devices (phone + desktops) — also clears entrap
        Settings.Global.putInt(getContentResolver(), "focus_lock_entrapped", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_desktop_active", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_desktop_locked_devices", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
        Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_message", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_task_text", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_compliment", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", 0);
        // Paywall persists across unlocks — only the Lion can clear it via /api/clear-paywall
        Settings.Global.putInt(getContentResolver(), "focus_lock_admin_tamper", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_admin_removed", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_offer", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_dim", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_mute", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_vibrate", 0);
        // Try to restore statusbar + launcher via shell (best effort — bridge also does this)
        try {
            Runtime.getRuntime().exec(new String[]{"cmd", "statusbar", "disable-for-setup", "false"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.android.launcher3"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.google.android.apps.nexuslauncher"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.android.settings"});
            Runtime.getRuntime().exec(new String[]{"input", "keyevent", "KEYCODE_HOME"});
        } catch (Exception e) {}
        // Re-enable camera double-press shortcut
        try {
            Settings.Secure.putInt(getContentResolver(), "camera_double_tap_power_gesture_disabled", 0);
            Settings.Secure.putInt(getContentResolver(), "camera_gesture_disabled", 0);
        } catch (Exception e) {}
        // Restore saved volumes per-stream
        try {
            android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
            int savedMusic = Settings.Global.getInt(getContentResolver(), "focus_lock_saved_volume", -1);
            int savedRing = Settings.Global.getInt(getContentResolver(), "focus_lock_saved_volume_ring", -1);
            int savedNotif = Settings.Global.getInt(getContentResolver(), "focus_lock_saved_volume_notif", -1);
            if (savedMusic >= 0) am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, savedMusic, 0);
            if (savedRing >= 0) am.setStreamVolume(android.media.AudioManager.STREAM_RING, savedRing, 0);
            if (savedNotif >= 0) am.setStreamVolume(android.media.AudioManager.STREAM_NOTIFICATION, savedNotif, 0);
        } catch (Exception e) {}
        restoreLauncher();
        lovenseStop(); // Stop Lovense on unlock
        return "{\"ok\":true,\"action\":\"unlocked\"}";
    }

    private String doLockDesktop(String body) {
        String msg = jval(body, "message");
        Settings.Global.putInt(getContentResolver(), "focus_lock_desktop_active", 1);
        if (msg != null) Settings.Global.putString(getContentResolver(), "focus_lock_desktop_message", msg);
        return "{\"ok\":true,\"action\":\"desktop_locked\"}";
    }

    private String doLockSpecificDevice(String hostname, String body) {
        String msg = jval(body, "message");
        // Add hostname to locked-devices list (comma-separated)
        String current = gstr("focus_lock_desktop_locked_devices");
        if (!current.contains(hostname)) {
            String updated = current.isEmpty() ? hostname : current + "," + hostname;
            Settings.Global.putString(getContentResolver(), "focus_lock_desktop_locked_devices", updated);
        }
        if (msg != null) Settings.Global.putString(getContentResolver(), "focus_lock_desktop_message", msg);
        return "{\"ok\":true,\"action\":\"device_locked\",\"device\":\"" + esc(hostname) + "\"}";
    }

    private String doLockDevice(String body) {
        String target = jval(body, "target");
        if (target == null || target.isEmpty()) return "{\"error\":\"target required\"}";
        if ("phone".equals(target)) {
            return doLock(body);
        } else if ("desktop".equals(target)) {
            return doLockDesktop(body);
        } else if ("all".equals(target)) {
            Settings.Global.putInt(getContentResolver(), "focus_lock_desktop_active", 1);
            // Modify body to route through doLock for phone
            return doLock(body);
        } else {
            return doLockSpecificDevice(target, body);
        }
    }

    private String doUnlockDevice(String body) {
        String target = jval(body, "target");
        if (target == null || target.isEmpty()) return "{\"error\":\"target required\"}";
        if ("phone".equals(target)) {
            Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
            return doUnlock();
        } else if ("desktop".equals(target)) {
            Settings.Global.putInt(getContentResolver(), "focus_lock_desktop_active", 0);
            Settings.Global.putString(getContentResolver(), "focus_lock_desktop_locked_devices", "");
            return "{\"ok\":true,\"action\":\"desktop_unlocked\"}";
        } else if ("all".equals(target)) {
            return doUnlock();
        } else {
            // Remove specific hostname from locked-devices list
            String current = gstr("focus_lock_desktop_locked_devices");
            String[] devices = current.split(",");
            StringBuilder updated = new StringBuilder();
            for (String d : devices) {
                if (!d.trim().equals(target) && !d.trim().isEmpty()) {
                    if (updated.length() > 0) updated.append(",");
                    updated.append(d.trim());
                }
            }
            Settings.Global.putString(getContentResolver(), "focus_lock_desktop_locked_devices", updated.toString());
            return "{\"ok\":true,\"action\":\"device_unlocked\",\"device\":\"" + esc(target) + "\"}";
        }
    }

    private String doMessage(String body) {
        String msg = jval(body, "message");
        if (msg != null) {
            Settings.Global.putString(getContentResolver(), "focus_lock_message", msg);
            // Phase D: also append to message history so vault-mode controllers
            // see the same line in their inbox without a separate code path.
            try {
                org.json.JSONObject m = new org.json.JSONObject();
                m.put("from", "lion");
                m.put("text", msg);
                appendMessageHistory(m);
            } catch (Exception e) { /* best effort */ }
        }
        return "{\"ok\":true}";
    }

    private String doTask(String body) {
        String text = jval(body, "text");
        String msg = jval(body, "message");
        String reps = jval(body, "reps");
        if (text == null || text.isEmpty()) return "{\"error\":\"text required\"}";
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
        // Store original text for re-randomization on each rep
        String randcaps = jval(body, "randcaps");
        boolean doRandCaps = "true".equals(randcaps) || "1".equals(randcaps);
        Settings.Global.putString(getContentResolver(), "focus_lock_task_orig", text);
        Settings.Global.putInt(getContentResolver(), "focus_lock_task_randcaps", doRandCaps ? 1 : 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_task_text",
            doRandCaps ? randomizeCaps(text) : text);
        Settings.Global.putInt(getContentResolver(), "focus_lock_task_reps",
            reps != null ? Integer.parseInt(reps) : 1);
        Settings.Global.putInt(getContentResolver(), "focus_lock_task_done", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_message",
            msg != null && !msg.isEmpty() ? msg : "Complete this task to unlock:");
        Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
        applyToggle(body, "vibrate", "focus_lock_vibrate");
        applyToggle(body, "penalty", "focus_lock_penalty");
        applyToggle(body, "shame", "focus_lock_shame");
        applyToggle(body, "dim", "focus_lock_dim");
        applyToggle(body, "mute", "focus_lock_mute");
        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "task");
        launchFocus();
        return "{\"ok\":true,\"action\":\"task_assigned\"}";
    }

    private String doEntrap(String body) {
        // Entrap: lock + set entrapped flag. No on-device unlock works — no timers,
        // no tasks, no payments. Only the Lion's explicit unlock command clears it.
        String msg = jval(body, "message");
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
        Settings.Global.putInt(getContentResolver(), "focus_lock_entrapped", 1);
        Settings.Global.putString(getContentResolver(), "focus_lock_message",
            msg != null && !msg.isEmpty() ? msg : "Entrapped. Only your Lion can free you.");
        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "basic");
        Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at", System.currentTimeMillis());
        Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0); // no timer
        Settings.Global.putInt(getContentResolver(), "focus_lock_shame", 1);
        launchFocus();
        return "{\"ok\":true,\"action\":\"entrapped\"}";
    }

    private String doAddPaywall(String body) {
        String amountStr = jval(body, "amount");
        if (amountStr == null) return "{\"error\":\"amount required\"}";
        try {
            int add = Integer.parseInt(amountStr);
            String cur = gstr("focus_lock_paywall");
            int current = 0;
            try { current = Integer.parseInt(cur); } catch (Exception e) {}
            int total = current + add;
            Settings.Global.putString(getContentResolver(), "focus_lock_paywall", String.valueOf(total));
            return "{\"ok\":true,\"paywall\":\"" + total + "\"}";
        } catch (NumberFormatException e) {
            return "{\"error\":\"invalid amount\"}";
        }
    }

    private String doOffer(String body) {
        String offer = jval(body, "offer");
        if (offer == null || offer.isEmpty()) return "{\"error\":\"offer required\"}";
        Settings.Global.putString(getContentResolver(), "focus_lock_offer", offer);
        Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "pending");
        Settings.Global.putLong(getContentResolver(), "focus_lock_offer_time", System.currentTimeMillis());
        return "{\"ok\":true,\"action\":\"offer_submitted\"}";
    }

    private String doOfferRespond(String body) {
        String action = jval(body, "action");
        String response = jval(body, "response");
        // Anti-exploit: offer must be pending for at least 60 seconds before accepting
        long offerTime = Settings.Global.getLong(getContentResolver(), "focus_lock_offer_time", 0);
        if ("accept".equals(action) && System.currentTimeMillis() - offerTime < 60000) {
            return "{\"error\":\"offer must be pending for at least 60 seconds\"}";
        }
        if ("accept".equals(action)) {
            Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "accepted");
            Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
            restoreLauncher();
        } else if ("decline".equals(action)) {
            Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "declined");
            if (response != null) {
                Settings.Global.putString(getContentResolver(), "focus_lock_offer_response", response);
            }
        }
        return "{\"ok\":true,\"action\":\"offer_" + action + "\"}";
    }

    private String doClearPaywall() {
        Settings.Global.putString(getContentResolver(), "focus_lock_paywall", "0");
        Settings.Global.putString(getContentResolver(), "focus_lock_paywall_original", "0");
        return "{\"ok\":true,\"action\":\"paywall_cleared\"}";
    }

    private String doGamble() {
        String pw = gstr("focus_lock_paywall");
        if (pw.isEmpty() || pw.equals("0")) return "{\"error\":\"no paywall to gamble\"}";
        try {
            double paywall = Double.parseDouble(pw);
            boolean heads = new java.security.SecureRandom().nextBoolean();
            double newPw = heads ? Math.ceil(paywall / 2.0) : paywall * 2;
            String result = heads ? "heads" : "tails";
            Settings.Global.putString(getContentResolver(), "focus_lock_paywall", String.format("%.0f", newPw));
            Settings.Global.putString(getContentResolver(), "focus_lock_gamble_result",
                result + ":" + String.format("%.0f", newPw));
            return "{\"ok\":true,\"result\":\"" + result + "\",\"old_paywall\":\"" +
                String.format("%.0f", paywall) + "\",\"new_paywall\":\"" + String.format("%.0f", newPw) + "\"}";
        } catch (Exception e) { return "{\"error\":\"" + e.getMessage() + "\"}"; }
    }

    private String doPlayAudio(String body) {
        String audioUrl = jval(body, "url");
        String audioBase64 = jval(body, "audio");
        new Thread(() -> {
            try {
                android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
                int prevVol = am.getStreamVolume(android.media.AudioManager.STREAM_MUSIC);
                int maxVol = am.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC);
                am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, maxVol, 0);
                if (audioUrl != null && !audioUrl.isEmpty()) {
                    android.media.MediaPlayer mp = new android.media.MediaPlayer();
                    mp.setAudioStreamType(android.media.AudioManager.STREAM_MUSIC);
                    mp.setDataSource(audioUrl);
                    mp.prepare();
                    mp.start();
                    mp.setOnCompletionListener(p -> { p.release(); am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, prevVol, 0); });
                } else if (audioBase64 != null && !audioBase64.isEmpty()) {
                    byte[] bytes = android.util.Base64.decode(audioBase64, android.util.Base64.DEFAULT);
                    java.io.File tmp = new java.io.File(getCacheDir(), "voice.mp3");
                    java.io.FileOutputStream fos = new java.io.FileOutputStream(tmp);
                    fos.write(bytes); fos.close();
                    android.media.MediaPlayer mp = new android.media.MediaPlayer();
                    mp.setAudioStreamType(android.media.AudioManager.STREAM_MUSIC);
                    mp.setDataSource(tmp.getAbsolutePath());
                    mp.prepare(); mp.start();
                    mp.setOnCompletionListener(p -> { p.release(); tmp.delete(); am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, prevVol, 0); });
                }
            } catch (Exception e) { Log.e(TAG, "Audio playback failed", e); }
        }).start();
        return "{\"ok\":true,\"action\":\"audio_playing\"}";
    }

    private String doSpeak(String body) {
        String text = jval(body, "text");
        if (text == null || text.isEmpty()) return "{\"error\":\"text required\"}";
        if (tts == null) return "{\"error\":\"TTS not initialized\"}";
        // TTS must be called from a thread with a Looper — post to main thread
        new android.os.Handler(android.os.Looper.getMainLooper()).post(() -> {
            try {
                android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
                int prevVol = am.getStreamVolume(android.media.AudioManager.STREAM_MUSIC);
                int maxVol = am.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC);
                am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, maxVol, 0);
                // Route TTS to music stream at max volume
                android.os.Bundle params = new android.os.Bundle();
                params.putInt(android.speech.tts.TextToSpeech.Engine.KEY_PARAM_STREAM,
                    android.media.AudioManager.STREAM_MUSIC);
                tts.setOnUtteranceProgressListener(new android.speech.tts.UtteranceProgressListener() {
                    public void onStart(String id) {}
                    public void onDone(String id) { am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, prevVol, 0); }
                    public void onError(String id) { am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, prevVol, 0); }
                });
                tts.speak(text, android.speech.tts.TextToSpeech.QUEUE_FLUSH, params, "lion_speak");
                Log.i(TAG, "TTS speaking: " + text);
            } catch (Exception e) {
                Log.e(TAG, "TTS speak failed", e);
            }
        });
        return "{\"ok\":true,\"action\":\"speaking\",\"text\":\"" + esc(text) + "\"}";
    }

    private String doSetGeofence(String body) {
        String lat = jval(body, "lat"), lon = jval(body, "lon"), radius = jval(body, "radius");
        if (lat == null || lon == null) return "{\"error\":\"lat and lon required\"}";
        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lat", lat);
        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lon", lon);
        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_radius_m",
            radius != null ? radius : "100");
        return "{\"ok\":true,\"action\":\"geofence_set\",\"lat\":" + lat + ",\"lon\":" + lon +
            ",\"radius\":" + (radius != null ? radius : "100") + "}";
    }

    private String doClearGeofence() {
        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lat", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_lon", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_geofence_radius_m", "");
        return "{\"ok\":true,\"action\":\"geofence_cleared\"}";
    }

    private String doSetCheckin(String body) {
        String deadline = jval(body, "deadline");
        if (deadline == null) return "{\"error\":\"deadline required (hour 0-23)\"}";
        try {
            int hour = Integer.parseInt(deadline);
            if (hour < 0 || hour > 23) return "{\"error\":\"deadline must be 0-23\"}";
            Settings.Global.putInt(getContentResolver(), "focus_lock_checkin_deadline", hour);
            return "{\"ok\":true,\"action\":\"checkin_set\",\"deadline\":" + hour + "}";
        } catch (NumberFormatException e) {
            return "{\"error\":\"invalid hour\"}";
        }
    }

    private String doSetVolume(String body) {
        String level = jval(body, "level");
        if (level == null) return "{\"error\":\"level required (0-15)\"}";
        try {
            int vol = Integer.parseInt(level);
            android.media.AudioManager am = (android.media.AudioManager) getSystemService(AUDIO_SERVICE);
            int maxVol = am.getStreamMaxVolume(android.media.AudioManager.STREAM_MUSIC);
            if (vol < 0 || vol > maxVol) return "{\"error\":\"level must be 0-" + maxVol + "\"}";
            am.setStreamVolume(android.media.AudioManager.STREAM_MUSIC, vol, 0);
            am.setStreamVolume(android.media.AudioManager.STREAM_RING, Math.min(vol, am.getStreamMaxVolume(android.media.AudioManager.STREAM_RING)), 0);
            am.setStreamVolume(android.media.AudioManager.STREAM_NOTIFICATION, Math.min(vol, am.getStreamMaxVolume(android.media.AudioManager.STREAM_NOTIFICATION)), 0);
            return "{\"ok\":true,\"action\":\"volume_set\",\"level\":" + vol + "}";
        } catch (NumberFormatException e) {
            return "{\"error\":\"invalid level\"}";
        }
    }

    private String doSetNotifPrefs(String body) {
        // Store notification preferences so mail service can read them via ADB
        String[] keys = {"email_evidence", "email_escape", "email_breach"};
        for (String k : keys) {
            String v = jval(body, k);
            if (v != null) {
                Settings.Global.putInt(getContentResolver(), "focus_lock_notif_" + k, "true".equals(v) ? 1 : 0);
            }
        }
        return "{\"ok\":true,\"action\":\"notif_prefs_set\"}";
    }

    private String doFreeUnlock() {
        String tier = gstr("focus_lock_sub_tier");
        if (!"gold".equals(tier)) return "{\"error\":\"free unlock requires Gold subscription\"}";
        int freeUnlocks = Settings.Global.getInt(getContentResolver(), "focus_lock_free_unlocks", 0);
        long lastReset = Settings.Global.getLong(getContentResolver(), "focus_lock_free_unlock_reset", 0);
        // Reset counter monthly (30 days)
        if (System.currentTimeMillis() - lastReset > 30L * 24 * 3600 * 1000) {
            freeUnlocks = 0;
            Settings.Global.putLong(getContentResolver(), "focus_lock_free_unlock_reset", System.currentTimeMillis());
        }
        if (freeUnlocks >= 1) return "{\"error\":\"free unlock already used this month\"}";
        // Do the unlock
        Settings.Global.putInt(getContentResolver(), "focus_lock_free_unlocks", freeUnlocks + 1);
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
        Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_message", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_task_text", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "");
        restoreLauncher();
        return "{\"ok\":true,\"action\":\"free_unlock\",\"remaining\":0}";
    }

    private String doSubscribe(String body) {
        String tier = jval(body, "tier");
        if (tier == null) return "{\"error\":\"tier required (bronze/silver/gold)\"}";
        tier = tier.toLowerCase();
        int amount = 0;
        if ("bronze".equals(tier)) amount = 25;
        else if ("silver".equals(tier)) amount = 35;
        else if ("gold".equals(tier)) amount = 50;
        else return "{\"error\":\"invalid tier\"}";

        // Can only upgrade, not downgrade (unless Lion forces it)
        String currentTier = gstr("focus_lock_sub_tier");
        String source = jval(body, "source");
        boolean fromBunny = "bunny".equals(source);
        if (fromBunny && !currentTier.isEmpty()) {
            int currentAmount = 0;
            if ("bronze".equals(currentTier)) currentAmount = 25;
            else if ("silver".equals(currentTier)) currentAmount = 35;
            else if ("gold".equals(currentTier)) currentAmount = 50;
            if (amount < currentAmount) {
                return "{\"error\":\"bunny can only upgrade, not downgrade\"}";
            }
        }

        Settings.Global.putString(getContentResolver(), "focus_lock_sub_tier", tier);
        // Set first due date: 7 days from now
        long dueDate = System.currentTimeMillis() + 7L * 24 * 3600 * 1000;
        Settings.Global.putLong(getContentResolver(), "focus_lock_sub_due", dueDate);
        return "{\"ok\":true,\"action\":\"subscribed\",\"tier\":\"" + tier + "\",\"amount\":" + amount +
            ",\"due\":\"" + new java.text.SimpleDateFormat("yyyy-MM-dd").format(new Date(dueDate)) + "\"}";
    }

    private String doUnsubscribe() {
        String tier = gstr("focus_lock_sub_tier");
        if (tier.isEmpty()) return "{\"error\":\"no active subscription\"}";
        // Unsubscription fee: 2x one period
        int fee = 0;
        if ("bronze".equals(tier)) fee = 20;
        else if ("silver".equals(tier)) fee = 50;
        else if ("gold".equals(tier)) fee = 100;
        // Add fee to paywall
        String pw = gstr("focus_lock_paywall");
        int currentPw = 0;
        try { currentPw = Integer.parseInt(pw); } catch (Exception e) {}
        Settings.Global.putString(getContentResolver(), "focus_lock_paywall", String.valueOf(currentPw + fee));
        // Clear subscription
        Settings.Global.putString(getContentResolver(), "focus_lock_sub_tier", "");
        Settings.Global.putLong(getContentResolver(), "focus_lock_sub_due", 0);
        return "{\"ok\":true,\"action\":\"unsubscribed\",\"fee\":" + fee + ",\"new_paywall\":" + (currentPw + fee) + "}";
    }

    private void reportSubscriptionCharge(String tier, int amount) {
        new Thread(() -> {
            String host = webhookHost();
            if (host.isEmpty()) return;
            try {
                String json = "{\"tier\":\"" + tier + "\",\"amount\":" + amount +
                    ",\"time\":" + System.currentTimeMillis() + "}";
                java.net.URL url = new java.net.URL("http://" + host + "/webhook/subscription-charge");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.setConnectTimeout(5000);
                conn.getOutputStream().write(json.getBytes()); conn.getResponseCode(); conn.disconnect();
            } catch (Exception e) {}
        }).start();
    }

    private String doPhotoTask(String body) {
        String task = jval(body, "task");
        String hint = jval(body, "hint");
        String msg = jval(body, "message");
        if (task == null || task.isEmpty()) return "{\"error\":\"task required\"}";
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
        Settings.Global.putString(getContentResolver(), "focus_lock_message",
            msg != null && !msg.isEmpty() ? msg : task);
        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "photo_task");
        Settings.Global.putString(getContentResolver(), "focus_lock_photo_task", task);
        Settings.Global.putString(getContentResolver(), "focus_lock_photo_hint",
            hint != null ? hint : "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", 0);
        Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at", System.currentTimeMillis());
        applyToggle(body, "vibrate", "focus_lock_vibrate");
        applyToggle(body, "shame", "focus_lock_shame");
        String paywall = jval(body, "paywall");
        if (paywall != null && !paywall.isEmpty()) {
            Settings.Global.putString(getContentResolver(), "focus_lock_paywall", paywall);
            Settings.Global.putString(getContentResolver(), "focus_lock_paywall_original", paywall);
        }
        launchFocus();
        lovenseLockPulse();
        return "{\"ok\":true,\"action\":\"photo_task_assigned\"}";
    }

    private String doPair(String body) {
        String lionPubKey = jval(body, "lion_pubkey");
        if (lionPubKey == null || lionPubKey.isEmpty()) return "{\"error\":\"lion_pubkey required\"}";
        // Check if already paired
        String existing = gstr("focus_lock_lion_pubkey");
        if (!existing.isEmpty()) return "{\"error\":\"already paired\"}";
        // Store lion's public key
        Settings.Global.putString(getContentResolver(), "focus_lock_lion_pubkey", lionPubKey);
        // Return bunny's public key for the lion to store
        String bunnyPubKey = gstr("focus_lock_bunny_pubkey");
        Log.i(TAG, "PAIRED with Lion. Key fingerprint: " +
            lionPubKey.substring(0, Math.min(8, lionPubKey.length())) + "...");
        return "{\"ok\":true,\"action\":\"paired\",\"bunny_pubkey\":\"" + esc(bunnyPubKey) + "\"}";
    }

    private String doReleaseForever() {
        Log.w(TAG, "RELEASE FOREVER — tearing down cage permanently");

        // SET AUTHORIZATION FLAG FIRST — prevents AdminReceiver $500/$1000 penalties
        Settings.Global.putInt(getContentResolver(), "focus_lock_release_authorized", 1);

        // Clear core lock state immediately
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_desktop_active", 0);
        Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_paywall", "0");
        Settings.Global.putString(getContentResolver(), "focus_lock_paywall_original", "0");
        Settings.Global.putString(getContentResolver(), "focus_lock_message", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_consented", 0);

        // Restore UI (best-effort — bridge handles this reliably via ADB)
        try {
            Runtime.getRuntime().exec(new String[]{"cmd", "statusbar", "disable-for-setup", "false"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.android.launcher3"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.android.settings"});
            Runtime.getRuntime().exec(new String[]{"settings", "put", "global", "user_switcher_enabled", "1"});
        } catch (Exception e) {}
        lovenseStop();

        // Show liberation notice
        try {
            Intent notice = new Intent(this, LiberationActivity.class);
            notice.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            startActivity(notice);
        } catch (Exception e) {
            Log.e(TAG, "Could not show liberation notice", e);
        }

        // Background: clear ALL focus_lock_* settings, then self-destruct
        new Thread(() -> {
            // Clear all focus_lock_* settings comprehensively
            try {
                Process p = Runtime.getRuntime().exec(new String[]{"settings", "list", "global"});
                java.io.BufferedReader br = new java.io.BufferedReader(
                    new java.io.InputStreamReader(p.getInputStream()));
                String line;
                while ((line = br.readLine()) != null) {
                    if (line.startsWith("focus_lock_") && !line.startsWith("focus_lock_release_authorized")) {
                        String key = line.split("=")[0].trim();
                        try {
                            Runtime.getRuntime().exec(new String[]{"settings", "delete", "global", key});
                        } catch (Exception e2) {}
                    }
                }
                br.close();
            } catch (Exception e) {
                Log.e(TAG, "Settings cleanup error", e);
            }

            // Wait for liberation notice to be visible
            try { Thread.sleep(5000); } catch (Exception e) {}

            // Self-destruct: remove admins + uninstall (release_authorized flag prevents penalties)
            try {
                Runtime.getRuntime().exec(new String[]{"dpm", "remove-active-admin", "com.bunnytasker/.AdminReceiver"});
                Thread.sleep(1000);
                Runtime.getRuntime().exec(new String[]{"dpm", "remove-active-admin", "com.focuslock/.AdminReceiver"});
                Thread.sleep(1000);
                Runtime.getRuntime().exec(new String[]{"pm", "uninstall", "com.bunnytasker"});
                Thread.sleep(1000);
                // Clean up the authorization flag before self-uninstall
                Runtime.getRuntime().exec(new String[]{"settings", "delete", "global", "focus_lock_release_authorized"});
                Thread.sleep(500);
                // Uninstall self (this kills the service)
                Runtime.getRuntime().exec(new String[]{"pm", "uninstall", "com.focuslock"});
            } catch (Exception e) {
                Log.e(TAG, "Self-destruct failed", e);
            }
        }).start();
        return "{\"ok\":true,\"action\":\"released_forever\"}";
    }

    // ── Lovense Integration ──
    // Requires Lovense Remote (com.lovense.wear) with Game Mode enabled on this phone
    private static final String LOVENSE_URL = "http://127.0.0.1:20010/command";

    private void lovenseCommand(String jsonBody) {
        new Thread(() -> {
            try {
                java.net.URL url = new java.net.URL(LOVENSE_URL);
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(2000);
                conn.setReadTimeout(2000);
                conn.getOutputStream().write(jsonBody.getBytes());
                conn.getResponseCode();
                conn.disconnect();
            } catch (Exception e) {
                // Lovense not running or Game Mode not enabled — silent fail
            }
        }).start();
    }

    private void lovenseEscapeBuzz(int escapes) {
        // Progressive intensity: more escapes = harder buzz
        int intensity = Math.min(20, 5 + escapes * 2);
        lovenseCommand("{\"command\":\"Function\",\"action\":\"Vibrate:" + intensity + "\",\"timeSec\":1,\"apiVer\":1}");
    }

    private void lovenseLockPulse() {
        // Low pulsing during lock: 3s on, 5s off, repeat
        lovenseCommand("{\"command\":\"Function\",\"action\":\"Vibrate:5\",\"timeSec\":0,\"loopRunningSec\":3,\"loopPauseSec\":5,\"apiVer\":1}");
    }

    private void lovenseReward() {
        // Gentle wave on task completion
        lovenseCommand("{\"command\":\"Preset\",\"name\":\"wave\",\"timeSec\":3,\"apiVer\":1}");
    }

    private void lovenseStop() {
        lovenseCommand("{\"command\":\"Function\",\"action\":\"Stop\",\"timeSec\":0,\"apiVer\":1}");
    }

    private String doLovense(String body) {
        String action = jval(body, "action");
        String intensity = jval(body, "intensity");
        String duration = jval(body, "duration");
        if (action == null) return "{\"error\":\"action required\"}";
        if ("stop".equals(action)) {
            lovenseStop();
        } else if ("vibrate".equals(action)) {
            int i = intensity != null ? Integer.parseInt(intensity) : 10;
            int d = duration != null ? Integer.parseInt(duration) : 5;
            lovenseCommand("{\"command\":\"Function\",\"action\":\"Vibrate:" + i + "\",\"timeSec\":" + d + ",\"apiVer\":1}");
        } else if ("pattern".equals(action)) {
            String pattern = jval(body, "pattern");
            int d = duration != null ? Integer.parseInt(duration) : 5;
            if (pattern != null) {
                lovenseCommand("{\"command\":\"Pattern\",\"rule\":\"V:1;F:v;S:300#\",\"strength\":\"" + pattern + "\",\"timeSec\":" + d + ",\"apiVer\":1}");
            }
        } else if ("pulse".equals(action)) {
            lovenseLockPulse();
        } else if ("reward".equals(action)) {
            lovenseReward();
        } else {
            return "{\"error\":\"unknown action: " + action + "\"}";
        }
        return "{\"ok\":true,\"action\":\"lovense_" + action + "\"}";
    }

    private String doPinMessage(String body) {
        String msg = jval(body, "message");
        if (msg == null || msg.isEmpty()) {
            // Clear pinned message
            Settings.Global.putString(getContentResolver(), "focus_lock_pinned_message", "");
            return "{\"ok\":true,\"action\":\"pin_cleared\"}";
        }
        Settings.Global.putString(getContentResolver(), "focus_lock_pinned_message", msg);
        // Phase D: also append to history so the inbox shows the pin
        try {
            org.json.JSONObject m = new org.json.JSONObject();
            m.put("from", "lion");
            m.put("text", msg);
            m.put("pinned", true);
            appendMessageHistory(m);
        } catch (Exception e) { /* best effort */ }
        return "{\"ok\":true,\"action\":\"message_pinned\"}";
    }

    private void reportLocation(double lat, double lon) {
        new Thread(() -> {
            String host = webhookHost();
            if (host.isEmpty()) return;
            try {
                String json = "{\"lat\":" + lat + ",\"lon\":" + lon + ",\"time\":" + System.currentTimeMillis() + "}";
                java.net.URL url = new java.net.URL("http://" + host + "/webhook/location");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.setConnectTimeout(5000);
                conn.getOutputStream().write(json.getBytes()); conn.getResponseCode(); conn.disconnect();
            } catch (Exception e) {}
        }).start();
    }

    private void reportGeofenceBreach(double lat, double lon, float distance) {
        new Thread(() -> {
            String host = webhookHost();
            if (host.isEmpty()) return;
            try {
                String json = "{\"lat\":" + lat + ",\"lon\":" + lon + ",\"distance\":" + distance + "}";
                java.net.URL url = new java.net.URL("http://" + host + "/webhook/geofence-breach");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST"); conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true); conn.setConnectTimeout(5000);
                conn.getOutputStream().write(json.getBytes()); conn.getResponseCode(); conn.disconnect();
            } catch (Exception e) {}
        }).start();
    }

    private boolean probeLocal(int port) {
        try {
            Socket s = new Socket();
            s.connect(new InetSocketAddress("127.0.0.1", port), 1000);
            s.close();
            return true;
        } catch (Exception e) { return false; }
    }

    private void phoneHome() {
        new Thread(() -> {
            String host = webhookHost();
            if (host.isEmpty()) return;
            try {
                // Get WiFi IP
                android.net.wifi.WifiManager wm = (android.net.wifi.WifiManager) getSystemService(WIFI_SERVICE);
                int ip = wm.getConnectionInfo().getIpAddress();
                String lanIp = (ip & 0xff) + "." + ((ip >> 8) & 0xff) + "." + ((ip >> 16) & 0xff) + "." + ((ip >> 24) & 0xff);

                // Get Tailscale IP (check tun0 interface)
                String tsIp = "";
                try {
                    java.util.Enumeration<java.net.NetworkInterface> nets = java.net.NetworkInterface.getNetworkInterfaces();
                    while (nets.hasMoreElements()) {
                        java.net.NetworkInterface ni = nets.nextElement();
                        if (ni.getName().startsWith("tun")) {
                            java.util.Enumeration<java.net.InetAddress> addrs = ni.getInetAddresses();
                            while (addrs.hasMoreElements()) {
                                java.net.InetAddress a = addrs.nextElement();
                                if (a instanceof java.net.Inet4Address) {
                                    tsIp = a.getHostAddress();
                                }
                            }
                        }
                    }
                } catch (Exception e) {}

                String deviceId = android.os.Build.MODEL.replaceAll("\\s+", "-");
                String json = "{\"lan_ip\":\"" + lanIp + "\",\"tailscale_ip\":\"" + tsIp + "\",\"device_id\":\"" + deviceId + "\"}";

                // POST to mesh server webhook
                java.net.URL url = new java.net.URL("http://" + host + "/webhook/register");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                conn.getOutputStream().write(json.getBytes());
                int code = conn.getResponseCode();
                conn.disconnect();
                Log.i(TAG, "Phone home: LAN=" + lanIp + " TS=" + tsIp + " response=" + code);
            } catch (Exception e) {
                // Try direct LAN fallback
                try {
                    android.net.wifi.WifiManager wm = (android.net.wifi.WifiManager) getSystemService(WIFI_SERVICE);
                    int ip = wm.getConnectionInfo().getIpAddress();
                    String lanIp = (ip & 0xff) + "." + ((ip >> 8) & 0xff) + "." + ((ip >> 16) & 0xff) + "." + ((ip >> 24) & 0xff);
                    String json = "{\"lan_ip\":\"" + lanIp + "\",\"tailscale_ip\":\"\",\"device_id\":\"" + android.os.Build.MODEL.replaceAll("\\s+", "-") + "\"}";
                    java.net.URL url = new java.net.URL("http://" + host + "/webhook/register");
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/json");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.getOutputStream().write(json.getBytes());
                    conn.getResponseCode();
                    conn.disconnect();
                } catch (Exception e2) {
                    Log.w(TAG, "Phone home failed: " + e2.getMessage());
                }
            }
        }).start();
    }

    private void launchFocus() {
        // Full-screen notification — shows FocusActivity immediately like an alarm/call
        try {
            Intent i = new Intent(this, FocusActivity.class);
            i.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            PendingIntent pi = PendingIntent.getActivity(this, 0, i,
                PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);

            NotificationChannel ch2 = new NotificationChannel(
                "collar", "The Collar — Locked", NotificationManager.IMPORTANCE_HIGH);
            ch2.setShowBadge(false);
            ch2.setBypassDnd(true);
            ch2.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            getSystemService(NotificationManager.class).createNotificationChannel(ch2);

            Notification n = new Notification.Builder(this, "jail")
                .setContentTitle("The Collar")
                .setSmallIcon(android.R.drawable.ic_lock_lock)
                .setFullScreenIntent(pi, true)
                .setCategory(Notification.CATEGORY_ALARM)
                .setPriority(Notification.PRIORITY_MAX)
                .setOngoing(true)
                .build();
            getSystemService(NotificationManager.class).notify(99, n);
        } catch (Exception e) {
            Log.e(TAG, "Full-screen notification failed", e);
        }
    }

    /** Show a heads-up notification warning of an imminent lock countdown. */
    private void showCountdownNotification(String timeLeft, String message) {
        try {
            NotificationChannel ch = new NotificationChannel(
                "countdown", "Lock Countdown", NotificationManager.IMPORTANCE_HIGH);
            ch.setShowBadge(true);
            ch.setBypassDnd(true);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            getSystemService(NotificationManager.class).createNotificationChannel(ch);

            String body = (message == null || message.isEmpty())
                ? "The Lion has set a countdown. Brace yourself."
                : message;

            Notification n = new Notification.Builder(this, "countdown")
                .setContentTitle("Lock in " + timeLeft)
                .setContentText(body)
                .setStyle(new Notification.BigTextStyle().bigText(body))
                .setSmallIcon(android.R.drawable.ic_lock_idle_alarm)
                .setCategory(Notification.CATEGORY_ALARM)
                .setPriority(Notification.PRIORITY_MAX)
                .setDefaults(Notification.DEFAULT_ALL)
                .setAutoCancel(false)
                .build();
            getSystemService(NotificationManager.class).notify(98, n);
            Log.i(TAG, "Countdown warning: " + timeLeft + " — " + body);
        } catch (Exception e) {
            Log.e(TAG, "Countdown notification failed", e);
        }
    }

    /** Disable all escape hatches — called on lock AND every 2s while locked. */
    private void enforceEscapeHatches() {
        try {
            // Disable notification shade (pull-down)
            Runtime.getRuntime().exec(new String[]{"cmd", "statusbar", "disable-for-setup", "true"});
            // Disable all launchers (home button goes nowhere)
            Runtime.getRuntime().exec(new String[]{"pm", "disable-user", "--user", "0", "com.android.launcher3"});
            Runtime.getRuntime().exec(new String[]{"pm", "disable-user", "--user", "0", "com.google.android.apps.nexuslauncher"});
            // Disable Settings (can't toggle permissions or disable admin)
            Runtime.getRuntime().exec(new String[]{"pm", "disable-user", "--user", "0", "com.android.settings"});
        } catch (Exception e) {}
    }

    private void restoreLauncher() {
        // Clear the jail notification
        try {
            getSystemService(NotificationManager.class).cancel(99);
        } catch (Exception e) {}
    }

    private String doAdbPort() {
        // Use shell to find ADB wireless debug port (uid 2000 LISTEN sockets)
        String port = "0";
        try {
            // Try multiple methods
            String[] methods = {
                "cat /proc/net/tcp6",
                "cat /proc/net/tcp"
            };
            for (String method : methods) {
                Process p = Runtime.getRuntime().exec(new String[]{"sh", "-c", method});
                BufferedReader br = new BufferedReader(new InputStreamReader(p.getInputStream()));
                String line;
                while ((line = br.readLine()) != null) {
                    line = line.trim();
                    if (!line.contains(" 0A ")) continue;
                    String[] parts = line.split("\\s+");
                    if (parts.length < 8) continue;
                    if (!"2000".equals(parts[7])) continue;
                    String localAddr = parts[1];
                    int ci = localAddr.lastIndexOf(":");
                    if (ci < 0) continue;
                    int pp = Integer.parseInt(localAddr.substring(ci + 1), 16);
                    if (pp > 10000) {
                        port = String.valueOf(pp);
                        break;
                    }
                }
                br.close();
                p.waitFor();
                if (!"0".equals(port)) break;
            }
        } catch (Exception e) {
            Log.e(TAG, "Port detection failed", e);
        }
        Log.i(TAG, "ADB port detected: " + port);
        return "{\"port\":" + port + "}";
    }

    private void applyToggle(String body, String jsonKey, String settingKey) {
        String val = jval(body, jsonKey);
        Settings.Global.putInt(getContentResolver(), settingKey,
            "true".equals(val) || "1".equals(val) ? 1 : 0);
    }

    private String doPower(String body) {
        String action = jval(body, "action");
        try {
            if ("off".equals(action)) {
                Runtime.getRuntime().exec(new String[]{"reboot", "-p"});
            } else if ("reboot".equals(action)) {
                Runtime.getRuntime().exec(new String[]{"reboot"});
            }
        } catch (Exception e) {
            // Fallback: use device admin
            try {
                DevicePolicyManager dpm = (DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
                if ("reboot".equals(action)) {
                    dpm.reboot(new ComponentName(this, AdminReceiver.class));
                }
            } catch (Exception ex) {
                return "{\"error\":\"" + ex.getMessage() + "\"}";
            }
        }
        return "{\"ok\":true}";
    }

    private String randomizeCaps(String s) {
        StringBuilder sb = new StringBuilder();
        java.util.Random rng = new java.util.Random();
        for (char c : s.toCharArray()) {
            if (Character.isLetter(c) && rng.nextBoolean()) {
                sb.append(Character.isUpperCase(c) ? Character.toLowerCase(c) : Character.toUpperCase(c));
            } else {
                sb.append(c);
            }
        }
        return sb.toString();
    }

    private String webhookHost() {
        // Returns empty string if not configured — callers must handle this and skip the call.
        return gstr("focus_lock_webhook_host");
    }

    private String gstr(String key) {
        String v = Settings.Global.getString(getContentResolver(), key);
        return (v == null || v.equals("null")) ? "" : v;
    }
    private String esc(String s) {
        if (s == null || s.isEmpty()) return "";
        return s.replace("\\","\\\\").replace("\"","\\\"").replace("\n","\\n").replace("\r","\\r");
    }
    private String jval(String json, String key) {
        if (json == null || json.isEmpty()) return null;
        String s = "\"" + key + "\"";
        int i = json.indexOf(s);
        if (i < 0) return null;
        i = json.indexOf(":", i + s.length());
        if (i < 0) return null;
        i++;
        while (i < json.length() && json.charAt(i) == ' ') i++;
        if (i >= json.length()) return null;
        if (json.charAt(i) == '"') {
            int e = json.indexOf('"', i + 1);
            return e < 0 ? null : json.substring(i + 1, e);
        }
        int e = i;
        while (e < json.length() && json.charAt(e) != ',' && json.charAt(e) != '}') e++;
        return json.substring(i, e).trim();
    }

    private String webUI() {
        return "<!DOCTYPE html><html lang=en><head>\n"
+ "<meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1,user-scalable=no'>\n"
+ "<meta name=theme-color content=#0a0a14><meta name=apple-mobile-web-app-capable content=yes>\n"
+ "<link rel=manifest href=/manifest.json><title>Lion's Share</title>\n"
+ "<style>\n"
+ "*{margin:0;padding:0;box-sizing:border-box}\n"
+ "body{font-family:system-ui,-apple-system,sans-serif;background:#0a0a14;color:#e0e0e0;\n"
+ "min-height:100vh;display:flex;flex-direction:column;align-items:center;padding:1.5rem 1rem}\n"
+ "h1{font-weight:300;font-size:1.3rem;letter-spacing:.04em;color:#c0c0c0;margin-bottom:.5rem}\n"
+ ".sub-title{font-size:.7rem;color:#555;margin-bottom:1.5rem;letter-spacing:.1em}\n"
+ "#login{width:100%;max-width:380px;text-align:center;margin-top:20vh}\n"
+ "#login h2{color:#888;font-size:1.1rem;margin-bottom:1.5rem;font-weight:300}\n"
+ "#app{width:100%;max-width:380px;display:none}\n"
+ "#status{font-size:.85rem;padding:.7rem 1rem;margin-bottom:1rem;display:flex;align-items:center;gap:.5rem}\n"
+ ".bar{width:3px;min-height:1.5rem;border-radius:2px}\n"
+ ".bar-g{background:#228833}.bar-r{background:#cc2222}\n"
+ "input,textarea,select{font-family:inherit;font-size:.9rem;background:#111118;color:#e0e0e0;\n"
+ "border:1px solid #1a1a28;border-radius:8px;padding:.7rem .9rem;width:100%;outline:none;margin-bottom:.5rem}\n"
+ "input:focus,textarea:focus,select:focus{border-color:#333}\n"
+ "textarea{resize:none;height:70px}\n"
+ "select{appearance:none;cursor:pointer}\n"
+ "button{font-family:inherit;font-size:.9rem;padding:.8rem;border:none;border-radius:8px;\n"
+ "cursor:pointer;width:100%;transition:all .15s;letter-spacing:.02em;margin-bottom:.4rem}\n"
+ "button:active{transform:scale(.97);opacity:.9}\n"
+ ".lock{background:#cc2222;color:#fff}.unlock{background:#228833;color:#fff}\n"
+ ".task-btn{background:#2244aa;color:#fff}.sec{background:#111118;color:#888}\n"
+ ".quick{display:flex;gap:.3rem;margin-bottom:.5rem}\n"
+ ".quick button{background:#991111;color:#ffcccc;font-size:.85rem;padding:.6rem}\n"
+ ".tog{display:flex;gap:.3rem;flex-wrap:wrap;margin-bottom:.5rem}\n"
+ ".tog button{background:#111118;color:#555;font-size:.75rem;padding:.5rem .6rem;flex:1;min-width:60px}\n"
+ ".tog button.on{background:#1a2540;color:#5599ff}\n"
+ ".divider{border-top:1px solid #151520;margin:.8rem 0}\n"
+ ".section{font-size:.65rem;color:#444;letter-spacing:.15em;text-transform:uppercase;margin:.6rem 0 .4rem}\n"
+ ".offer-card{background:#0e0e1a;padding:1rem;border-radius:8px;margin-bottom:.8rem}\n"
+ ".offer-card h3{font-size:.65rem;color:#5599ff;letter-spacing:.15em;margin-bottom:.5rem}\n"
+ ".offer-card p{font-size:1rem;margin-bottom:.8rem}\n"
+ ".offer-btns{display:flex;gap:.3rem}\n"
+ ".offer-btns button{font-size:.85rem;padding:.6rem}\n"
+ "</style></head><body>\n"
+ "<h1>Lion's Share</h1>\n"
+ "<div class=sub-title>REMOTE CONTROL</div>\n"
// Main app (RSA auth — no PIN login needed)
+ "<div id=app>\n"
+ "<div id=status><div class='bar bar-g' id=sbar></div><span id=stext>...</span></div>\n"
// Lock controls
+ "<input id=msg placeholder='Lock message'>\n"
+ "<input id=timer type=number placeholder='Timer (minutes)' min=0>\n"
+ "<input id=paywall type=number placeholder='Demand payment ($)' min=0 step=5>\n"
// Mode
+ "<div class=section>MODE</div>\n"
+ "<select id=mode><option value=basic>Basic lock</option><option value=negotiation>Negotiation</option>\n"
+ "<option value=task>Task</option><option value=compliment>Compliment</option>\n"
+ "<option value=gratitude>Gratitude journal</option><option value=exercise>Exercise</option>\n"
+ "<option value=love_letter>Love letter</option><option value=random>Random</option></select>\n"
// Toggles
+ "<div class=section>MODIFIERS</div>\n"
+ "<div class=tog>\n"
+ "<button id=t_shame onclick=tog(this)>Taunt</button>\n"
+ "<button id=t_penalty onclick=tog(this)>+5m/esc</button>\n"
+ "<button id=t_vibrate onclick=tog(this)>Vibrate</button>\n"
+ "<button id=t_dim onclick=tog(this)>Dim</button>\n"
+ "<button id=t_mute onclick=tog(this)>Mute</button>\n"
+ "</div>\n"
+ "<input id=compliment placeholder='Compliment to type to unlock'>\n"
// Lock button
+ "<button class=lock onclick=doLock()>Lock Phone</button>\n"
+ "<div class=quick>\n"
+ "<button onclick=qlock(15)>15m</button><button onclick=qlock(30)>30m</button>\n"
+ "<button onclick=qlock(60)>1hr</button><button onclick=qlock(120)>2hr</button>\n"
+ "</div>\n"
+ "<button class=unlock onclick=doUnlock()>Unlock Phone</button>\n"
// Offer section
+ "<div id=offer class=offer-card style=display:none>\n"
+ "<h3>OFFER RECEIVED</h3><p id=offer-text></p>\n"
+ "<input id=counter placeholder='Counter-offer (optional)'>\n"
+ "<div class=offer-btns><button class=unlock onclick=offerResp('accept')>Accept</button>\n"
+ "<button class=lock onclick=offerResp('decline')>Decline</button></div></div>\n"
// Task section
+ "<div class=divider></div>\n"
+ "<div class=section>WRITING TASK</div>\n"
+ "<textarea id=tasktext placeholder='Text to type to unlock'></textarea>\n"
+ "<div style='display:flex;gap:.3rem;align-items:center'>\n"
+ "<input id=reps type=number placeholder='Reps' style='flex:1' min=1>\n"
+ "<button class=tog id=t_randcaps onclick=tog(this) style='flex:1;margin-bottom:.5rem'>Random caps</button>\n"
+ "<button class=task-btn onclick=doTask() style='flex:1'>Task + Lock</button>\n"
+ "</div>\n"
+ "</div>\n"
// Script
+ "<script>\n"
+ "const B=window.location.origin;\n"
+ "let togsState={};\n"
+ "function tog(el){el.classList.toggle('on');togsState[el.id.substring(2)]=el.classList.contains('on')}\n"
+ "async function api(p,b){\n"
+ "  const r=await fetch(B+p,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b||{})});\n"
+ "  return r.json();\n"
+ "}\n"
+ "let timerEnd=0,lastEsc=0;\n"
+ "function tick(){\n"
+ "  if(timerEnd>0){let r=timerEnd-Date.now();if(r>0){let m=Math.floor(r/60000),s=Math.floor(r%60000/1000);\n"
+ "  document.getElementById('stext').textContent='LOCKED | '+m+'m '+s+'s'+( lastEsc?' | '+lastEsc+' esc':'');}}\n"
+ "}\n"
+ "async function refresh(){\n"
+ "  try{\n"
+ "    const s=await api('/api/status',{});\n"
+ "    const bar=document.getElementById('sbar'),txt=document.getElementById('stext');\n"
+ "    lastEsc=s.escapes||0;\n"
+ "    if(s.locked){\n"
+ "      bar.className='bar bar-r';\n"
+ "      let t='LOCKED';\n"
+ "      if(s.timer_remaining_ms>0){timerEnd=Date.now()+s.timer_remaining_ms;let m=Math.floor(s.timer_remaining_ms/60000);t+=' | '+m+'m'}else{timerEnd=0}\n"
+ "      if(s.escapes>0)t+=' | '+s.escapes+' esc';\n"
+ "      if(s.paywall&&s.paywall!='0')t+=' | $'+s.paywall;\n"
+ "      txt.textContent=t;\n"
+ "    }else{bar.className='bar bar-g';txt.textContent='UNLOCKED';timerEnd=0}\n"
+ "    if(s.offer&&s.offer_status=='pending'){\n"
+ "      document.getElementById('offer').style.display='block';\n"
+ "      document.getElementById('offer-text').textContent='\"'+s.offer+'\"';\n"
+ "    }else{document.getElementById('offer').style.display='none'}\n"
+ "  }catch(e){}\n"
+ "}\n"
+ "async function doLock(){\n"
+ "  const b={message:document.getElementById('msg').value,\n"
+ "    timer:document.getElementById('timer').value||'0',\n"
+ "    mode:document.getElementById('mode').value,\n"
+ "    paywall:document.getElementById('paywall').value||'0',\n"
+ "    compliment:document.getElementById('compliment').value};\n"
+ "  Object.keys(togsState).forEach(k=>{b[k]=togsState[k]});\n"
+ "  await api('/api/lock',b);setTimeout(refresh,500);\n"
+ "}\n"
+ "function qlock(m){document.getElementById('timer').value=m;doLock()}\n"
+ "async function doUnlock(){await api('/api/unlock');setTimeout(refresh,500)}\n"
+ "function randCaps(s){let r='';for(let c of s){if(/[a-zA-Z]/.test(c)&&Math.random()>.5)\n"
+ "  r+=c===c.toUpperCase()?c.toLowerCase():c.toUpperCase();else r+=c}return r}\n"
+ "async function doTask(){\n"
+ "  let t=document.getElementById('tasktext').value;\n"
+ "  if(!t){alert('Enter task text');return}\n"
+ "  if(document.getElementById('t_randcaps').classList.contains('on'))t=randCaps(t);\n"
+ "  const b={text:t,reps:document.getElementById('reps').value||'1',\n"
+ "    message:document.getElementById('msg').value};\n"
+ "  Object.keys(togsState).forEach(k=>{b[k]=togsState[k]});\n"
+ "  await api('/api/task',b);setTimeout(refresh,500);\n"
+ "}\n"
+ "async function offerResp(a){\n"
+ "  await api('/api/offer-respond',{action:a,response:document.getElementById('counter').value});\n"
+ "  setTimeout(refresh,500);\n"
+ "}\n"
+ "refresh();setInterval(refresh,3000);setInterval(tick,1000);\n"
+ "</script></body></html>";
    }

    // ══════════════════════════════════════════════════════════════
    // ── Mesh P2P Protocol ──
    // ══════════════════════════════════════════════════════════════

    private void initMesh() {
        meshVersion.set(Settings.Global.getLong(getContentResolver(), "focus_lock_mesh_version", 0));
        if (meshVersion.get() == 0) {
            meshVersion.set(1);
            Settings.Global.putLong(getContentResolver(), "focus_lock_mesh_version", meshVersion.get());
        }
        // Seed peers
        String peersJson = gstr("focus_lock_mesh_peers");
        if (peersJson.isEmpty()) {
            // No default peers — peers are learned via gossip from focus_lock_mesh_url, or
            // populated by the controller during pairing.
        } else {
            // Parse saved peers: simple format "id:type:addr:port;id:type:addr:port"
            for (String entry : peersJson.split(";")) {
                String[] parts = entry.split(":");
                if (parts.length >= 5) {
                    meshPeers.put(parts[0], new String[]{parts[1], parts[2], parts[3], parts[4]});
                } else if (parts.length >= 4) {
                    meshPeers.put(parts[0], new String[]{parts[1], parts[2], parts[3]});
                }
            }
        }
        // Add mesh server as peer if configured (set by BunnyTasker after mesh join)
        String meshUrl = gstr("focus_lock_mesh_url");
        if (!meshUrl.isEmpty()) {
            try {
                java.net.URL mu = new java.net.URL(meshUrl);
                String host = mu.getHost();
                String scheme = meshUrl.startsWith("https") ? "https" : "http";
                int mport = mu.getPort() > 0 ? mu.getPort() : ("https".equals(scheme) ? 443 : 8434);
                meshPeers.put("mesh-server", new String[]{"server", host, String.valueOf(mport), scheme});
                Log.i(TAG, "Mesh server peer added: " + scheme + "://" + host + ":" + mport);
            } catch (Exception e) {
                Log.w(TAG, "Failed to parse mesh_url: " + meshUrl, e);
            }
        }

        Log.i(TAG, "Mesh initialized: v" + meshVersion.get() + " peers=" + meshPeers.size());

        // Start gossip thread (30s interval)
        Thread gossipThread = new Thread(() -> {
            try { Thread.sleep(5000); } catch (Exception e) {} // startup delay
            while (running) {
                try {
                    meshGossip();
                } catch (Exception e) {
                    Log.e(TAG, "Mesh gossip error", e);
                }
                // Vault sync + runtime push run when vault mode is on.
                // In Phase B/C the vault was an additive read path. Phase D
                // promotes the slave to a vault writer for runtime state, so
                // vaultRuntimePush() runs every tick to mirror the slave's
                // current runtime into an encrypted blob the controller can
                // decrypt without ever touching /api/mesh/{id}/status.
                if (Settings.Global.getInt(getContentResolver(), "focus_lock_vault_mode", 0) == 1) {
                    try {
                        vaultSync();
                    } catch (Exception e) {
                        Log.e(TAG, "Vault sync error", e);
                    }
                    try {
                        vaultRuntimePush();
                    } catch (Exception e) {
                        Log.e(TAG, "Vault runtime push error", e);
                    }
                }
                try { Thread.sleep(30000); } catch (Exception e) {}
            }
        });
        gossipThread.setDaemon(true);
        gossipThread.start();

        // ── ntfy Push Subscriber (latency optimization — triggers immediate vault sync) ──
        String ntfyServer = gstr("focus_lock_ntfy_server");
        String ntfyTopic = gstr("focus_lock_ntfy_topic");
        if (ntfyTopic.isEmpty()) {
            String mid = gstr("focus_lock_mesh_id");
            if (!mid.isEmpty()) ntfyTopic = "fl-" + mid;
        }
        if (!ntfyTopic.isEmpty()) {
            if (ntfyServer.isEmpty()) ntfyServer = "https://ntfy.sh";
            final String fServer = ntfyServer;
            final String fTopic = ntfyTopic;
            Thread ntfyThread = new Thread(() -> ntfySubscribeLoop(fServer, fTopic));
            ntfyThread.setDaemon(true);
            ntfyThread.start();
            Log.w(TAG, "ntfy subscriber started: " + ntfyServer + "/" + ntfyTopic);
        }
    }

    // Mesh key -> actual Settings.Global key (for keys that don't follow "focus_lock_" + meshKey)
    private String meshToAdbKey(String meshKey) {
        if ("lock_active".equals(meshKey)) return "focus_lock_active";
        return "focus_lock_" + meshKey;
    }

    private static final java.util.Set<String> INT_KEYS = new java.util.HashSet<>(java.util.Arrays.asList(
        "lock_active", "desktop_active", "vibrate", "penalty", "shame", "dim", "mute",
        "task_reps", "task_done", "task_randcaps", "word_min", "settings_allowed", "free_unlocks",
        "curfew_enabled", "curfew_confine_hour", "curfew_release_hour", "curfew_radius_m",
        "notif_email_evidence", "notif_email_escape", "notif_email_breach",
        "fine_active", "fine_amount", "fine_interval_m",
        "body_check_active", "body_check_interval_h", "body_check_streak",
        "bedtime_enabled", "bedtime_lock_hour", "bedtime_unlock_hour",
        "screen_time_quota_minutes", "screen_time_reset_hour"
    ));
    private static final java.util.Set<String> LONG_KEYS = new java.util.HashSet<>(java.util.Arrays.asList(
        "unlock_at", "locked_at", "offer_time", "sub_due", "sub_total_owed", "free_unlock_reset",
        "fine_last_applied", "body_check_last"
    ));

    private String buildOrdersJson() {
        StringBuilder sb = new StringBuilder("{");
        for (int i = 0; i < MESH_ORDER_KEYS.length; i++) {
            String k = MESH_ORDER_KEYS[i];
            if (i > 0) sb.append(",");
            sb.append("\"").append(k).append("\":");
            // Read with correct type to avoid getString() returning null for int/long fields
            String adbKey = meshToAdbKey(k);
            if (INT_KEYS.contains(k)) {
                sb.append(Settings.Global.getInt(getContentResolver(), adbKey, 0));
            } else if (LONG_KEYS.contains(k)) {
                sb.append(Settings.Global.getLong(getContentResolver(), adbKey, 0));
            } else {
                sb.append("\"").append(esc(gstr(adbKey))).append("\"");
            }
        }
        sb.append("}");
        return sb.toString();
    }

    private void applyOrdersFromMesh(String ordersJson) {
        // Parse and write each field to Settings.Global
        for (String k : MESH_ORDER_KEYS) {
            String v = jval(ordersJson, k);
            if (v != null) {
                Settings.Global.putString(getContentResolver(), meshToAdbKey(k), v);
            }
        }
        // Trigger enforcement if lock state changed
        int nowActive = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
        if (nowActive == 1) {
            launchFocus();
        }
    }

    private String handleMeshSync(String body) {
        String remoteId = jval(body, "node_id");
        String remoteVersionStr = jval(body, "orders_version");
        long remoteVersion = 0;
        try { remoteVersion = Long.parseLong(remoteVersionStr); } catch (Exception e) {}

        // Update peer info
        if (remoteId != null && !remoteId.isEmpty()) {
            String type = jval(body, "type");
            // Simple: store the address from the connection
            String addr = jval(body, "addresses");
            String port = jval(body, "port");
            if (type == null) type = "unknown";
            if (port == null) port = "8434";
            // addr might be a JSON array; extract first entry
            if (addr != null && addr.startsWith("[")) {
                addr = addr.replace("[","").replace("]","").replace("\"","").split(",")[0].trim();
            }
            if (addr != null && !addr.isEmpty()) {
                // Preserve existing scheme if peer was already known (e.g. mesh-server with https)
                String[] existing = meshPeers.get(remoteId);
                String scheme = (existing != null && existing.length > 3) ? existing[3] : "http";
                meshPeers.put(remoteId, new String[]{type, addr, port, scheme});
                meshPeerLastSeen.put(remoteId, System.currentTimeMillis());
                saveMeshPeers();
            }
        }

        // Accept orders if remote has higher version
        if (remoteVersion > meshVersion.get()) {
            String ordersStr = jval(body, "orders");
            // The orders field is a JSON object — look for it differently
            int ordersStart = body.indexOf("\"orders\"");
            if (ordersStart >= 0) {
                int braceStart = body.indexOf("{", ordersStart + 8);
                if (braceStart >= 0) {
                    int depth = 0; int braceEnd = braceStart;
                    for (int i = braceStart; i < body.length(); i++) {
                        if (body.charAt(i) == '{') depth++;
                        else if (body.charAt(i) == '}') { depth--; if (depth == 0) { braceEnd = i; break; } }
                    }
                    String ordersJson = body.substring(braceStart, braceEnd + 1);
                    Log.w(TAG, "Mesh: applying orders v" + remoteVersion + " from " + remoteId);
                    applyOrdersFromMesh(ordersJson);
                    meshVersion.set(remoteVersion);
                    Settings.Global.putLong(getContentResolver(), "focus_lock_mesh_version", meshVersion.get());
                }
            }
        }

        // Build response
        StringBuilder resp = new StringBuilder();
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (nodeId.isEmpty()) { nodeId = "pixel"; Settings.Global.putString(getContentResolver(), "focus_lock_mesh_node_id", nodeId); }
        resp.append("{\"node_id\":\"").append(esc(nodeId)).append("\"");
        resp.append(",\"type\":\"phone\"");
        resp.append(",\"addresses\":").append(getLocalAddressesJson());
        resp.append(",\"orders_version\":").append(meshVersion.get());
        resp.append(",\"updated_at\":").append(System.currentTimeMillis());
        resp.append(",\"signature\":\"\""); // phone doesn't sign — only Lion's Share signs
        // Include orders if requester is behind
        if (remoteVersion < meshVersion.get()) {
            resp.append(",\"orders\":").append(buildOrdersJson());
        }
        // Include status
        resp.append(",\"status\":{\"type\":\"phone\",\"escapes\":")
            .append(Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0))
            .append(",\"adb_wifi_port\":").append(getAdbWifiPort())
            .append(",\"tailscale_up\":").append(isTailscaleUp())
            .append("}");
        // Known nodes
        resp.append(",\"known_nodes\":{");
        boolean first = true;
        for (java.util.Map.Entry<String, String[]> e : meshPeers.entrySet()) {
            if (!first) resp.append(",");
            String[] info = e.getValue();
            resp.append("\"").append(esc(e.getKey())).append("\":{\"type\":\"").append(esc(info[0]))
                .append("\",\"addresses\":[\"").append(esc(info[1])).append("\"],\"port\":").append(info[2]).append("}");
            first = false;
        }
        resp.append("}}");
        return resp.toString();
    }

    private String handleMeshOrder(String body) {
        String action = jval(body, "action");
        if (action == null || action.isEmpty()) return "{\"error\":\"action required\"}";

        // Delegate to existing handlers
        String result;
        switch (action) {
            case "lock": result = doLock(body); break;
            case "unlock": result = doUnlock(); break;
            case "set-geofence": result = doSetGeofence(body); break;
            case "clear-geofence": result = doClearGeofence(); break;
            case "add-paywall": result = doAddPaywall(body); break;
            case "clear-paywall": result = doClearPaywall(); break;
            case "pin-message": result = doPinMessage(body); break;
            case "send-message": {
                // Phase D: Lion's apiVault → vault append → vaultSync → here.
                // Persist to history so the next vaultRuntimePush surfaces
                // the new message in the runtime body's messages array.
                // Lock-screen `focus_lock_message` is also updated for the
                // legacy direct-HTTP code paths that still read it.
                String text = jval(body, "text");
                String from = jval(body, "from");
                if (from == null || from.isEmpty()) from = "lion";
                String ciphertext = jval(body, "ciphertext");
                String encryptedKey = jval(body, "encrypted_key");
                String iv = jval(body, "iv");
                String attachUrl = jval(body, "attachment_url");
                boolean encrypted = body.contains("\"encrypted\":true")
                    || body.contains("\"encrypted\": true");
                boolean pinned = body.contains("\"pinned\":true")
                    || body.contains("\"pinned\": true");
                boolean mandatoryReply = body.contains("\"mandatory_reply\":true")
                    || body.contains("\"mandatory_reply\": true");
                try {
                    org.json.JSONObject m = new org.json.JSONObject();
                    m.put("from", from);
                    if (text != null) m.put("text", text);
                    if (encrypted) {
                        m.put("encrypted", true);
                        if (ciphertext != null) m.put("ciphertext", ciphertext);
                        if (encryptedKey != null) m.put("encrypted_key", encryptedKey);
                        if (iv != null) m.put("iv", iv);
                    }
                    if (pinned) m.put("pinned", true);
                    if (mandatoryReply) m.put("mandatory_reply", true);
                    if (attachUrl != null && !attachUrl.isEmpty()) {
                        m.put("attachment_url", attachUrl);
                    }
                    appendMessageHistory(m);

                    // Compat: keep the lock-screen message field in sync so
                    // the foreground lock activity (which still reads
                    // focus_lock_message directly) shows the latest line.
                    if (text != null && !text.isEmpty()) {
                        Settings.Global.putString(getContentResolver(),
                            "focus_lock_message", text);
                    }
                    if (pinned && text != null && !text.isEmpty()) {
                        Settings.Global.putString(getContentResolver(),
                            "focus_lock_pinned_message", text);
                    }
                    result = "{\"ok\":true,\"action\":\"message_appended\"}";
                } catch (Exception e) {
                    result = "{\"error\":\"send-message: " + esc(e.getMessage()) + "\"}";
                }
                // Trigger an immediate runtime push (vault mode only) so
                // Lion sees her own message bounce back within ~1-2s
                // instead of waiting for the next 30s gossip tick.
                if (Settings.Global.getInt(getContentResolver(),
                        "focus_lock_vault_mode", 0) == 1) {
                    new Thread(() -> {
                        try { vaultRuntimePush(); }
                        catch (Exception ex) { /* best effort */ }
                    }).start();
                }
                break;
            }
            case "set-checkin": result = doSetCheckin(body); break;
            case "lock-device": result = doLockDevice(body); break;
            case "unlock-device": result = doUnlockDevice(body); break;
            case "release-device": {
                String target = jval(body, "target");
                if (target == null || target.isEmpty()) {
                    // Try nested params
                    String paramsJson = jval(body, "params");
                    if (paramsJson != null) target = jval("{" + paramsJson + "}", "target");
                }
                String nodeId = gstr("focus_lock_mesh_node_id");
                if (nodeId.isEmpty()) nodeId = "pixel";
                if ("all".equals(target) || nodeId.equals(target)) {
                    result = doReleaseForever();
                } else {
                    result = "{\"ok\":true,\"action\":\"release_ignored\",\"reason\":\"not_targeted\"}";
                }
                break;
            }
            case "entrap": result = doEntrap(body); break;
            case "task": result = doTask(body); break;
            case "subscribe": result = doSubscribe(body); break;
            case "set-curfew": {
                String confine = jval(body, "confine_hour");
                String release = jval(body, "release_hour");
                String radius = jval(body, "radius");
                String lat = jval(body, "lat");
                String lon = jval(body, "lon");
                Settings.Global.putInt(getContentResolver(), "focus_lock_curfew_enabled", 1);
                if (confine != null) Settings.Global.putString(getContentResolver(), "focus_lock_curfew_confine_hour", confine);
                if (release != null) Settings.Global.putString(getContentResolver(), "focus_lock_curfew_release_hour", release);
                if (radius != null) Settings.Global.putString(getContentResolver(), "focus_lock_curfew_radius_m", radius);
                if (lat != null) Settings.Global.putString(getContentResolver(), "focus_lock_curfew_lat", lat);
                if (lon != null) Settings.Global.putString(getContentResolver(), "focus_lock_curfew_lon", lon);
                result = "{\"ok\":true,\"action\":\"curfew_set\"}";
                break;
            }
            case "clear-curfew": {
                Settings.Global.putInt(getContentResolver(), "focus_lock_curfew_enabled", 0);
                result = "{\"ok\":true,\"action\":\"curfew_cleared\"}";
                break;
            }
            case "set-bedtime": {
                Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_enabled", 1);
                String lh = jval(body, "lock_hour");
                String uh = jval(body, "unlock_hour");
                if (lh != null) Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_lock_hour", Integer.parseInt(lh));
                if (uh != null) Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_unlock_hour", Integer.parseInt(uh));
                result = "{\"ok\":true,\"action\":\"bedtime_set\"}";
                break;
            }
            case "clear-bedtime": {
                Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_enabled", 0);
                Settings.Global.putInt(getContentResolver(), "focus_lock_bedtime_locked", 0);
                result = "{\"ok\":true,\"action\":\"bedtime_cleared\"}";
                break;
            }
            case "set-screen-time": {
                String qt = jval(body, "quota_minutes");
                String rh = jval(body, "reset_hour");
                if (qt != null) Settings.Global.putInt(getContentResolver(), "focus_lock_screen_time_quota_minutes", Integer.parseInt(qt));
                if (rh != null) Settings.Global.putInt(getContentResolver(), "focus_lock_screen_time_reset_hour", Integer.parseInt(rh));
                result = "{\"ok\":true,\"action\":\"screen_time_set\"}";
                break;
            }
            case "clear-screen-time": {
                Settings.Global.putInt(getContentResolver(), "focus_lock_screen_time_quota_minutes", 0);
                result = "{\"ok\":true,\"action\":\"screen_time_cleared\"}";
                break;
            }
            case "start-fine": {
                String amt = jval(body, "amount");
                String intv = jval(body, "interval");
                Settings.Global.putInt(getContentResolver(), "focus_lock_fine_active", 1);
                Settings.Global.putInt(getContentResolver(), "focus_lock_fine_amount", amt != null ? Integer.parseInt(amt) : 10);
                Settings.Global.putInt(getContentResolver(), "focus_lock_fine_interval_m", intv != null ? Integer.parseInt(intv) : 60);
                Settings.Global.putLong(getContentResolver(), "focus_lock_fine_last_applied", System.currentTimeMillis());
                result = "{\"ok\":true,\"action\":\"fine_started\"}";
                break;
            }
            case "set-countdown": {
                String lockAt = jval(body, "lock_at");
                String cdMsg = jval(body, "message");
                if (lockAt != null) {
                    try {
                        long lockAtMs = Long.parseLong(lockAt);
                        Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_lock_at", lockAtMs);
                        if (cdMsg != null) {
                            Settings.Global.putString(getContentResolver(), "focus_lock_countdown_message", cdMsg);
                        }
                        // Reset warn tier so first crossing fires
                        Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_warn_tier", 0);
                        result = "{\"ok\":true,\"action\":\"countdown_set\",\"lock_at\":" + lockAtMs + "}";
                    } catch (NumberFormatException e) {
                        result = "{\"error\":\"invalid lock_at\"}";
                    }
                } else {
                    result = "{\"error\":\"lock_at required\"}";
                }
                break;
            }
            case "cancel-countdown": {
                Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_lock_at", 0);
                Settings.Global.putString(getContentResolver(), "focus_lock_countdown_message", "");
                Settings.Global.putLong(getContentResolver(), "focus_lock_countdown_warn_tier", 0);
                getSystemService(NotificationManager.class).cancel(98);
                result = "{\"ok\":true,\"action\":\"countdown_cancelled\"}";
                break;
            }
            case "stop-fine": {
                Settings.Global.putInt(getContentResolver(), "focus_lock_fine_active", 0);
                result = "{\"ok\":true,\"action\":\"fine_stopped\"}";
                break;
            }
            case "start-body-check": {
                String area = jval(body, "area");
                String intH = jval(body, "interval_h");
                Settings.Global.putInt(getContentResolver(), "focus_lock_body_check_active", 1);
                Settings.Global.putString(getContentResolver(), "focus_lock_body_check_area", area != null ? area : "body");
                Settings.Global.putInt(getContentResolver(), "focus_lock_body_check_interval_h", intH != null ? Integer.parseInt(intH) : 12);
                Settings.Global.putLong(getContentResolver(), "focus_lock_body_check_last", System.currentTimeMillis());
                result = "{\"ok\":true,\"action\":\"body_check_started\"}";
                break;
            }
            case "stop-body-check": {
                Settings.Global.putInt(getContentResolver(), "focus_lock_body_check_active", 0);
                result = "{\"ok\":true,\"action\":\"body_check_stopped\"}";
                break;
            }
            case "enable-tailscale": {
                try {
                    Intent ts = new Intent();
                    ts.setClassName("com.tailscale.ipn", "com.tailscale.ipn.IPNActivity");
                    ts.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(ts);
                    result = "{\"ok\":true,\"action\":\"tailscale_launched\"}";
                } catch (Exception e) {
                    result = "{\"error\":\"tailscale not installed\"}";
                }
                break;
            }
            case "enable-adb-wifi": {
                try {
                    Settings.Global.putInt(getContentResolver(), "adb_wifi_enabled", 1);
                    result = "{\"ok\":true,\"action\":\"adb_wifi_enabled\",\"port\":" + getAdbWifiPort() + "}";
                } catch (Exception e) {
                    result = "{\"error\":\"" + esc(e.getMessage()) + "\"}";
                }
                break;
            }
            default: result = "{\"error\":\"unknown action: " + esc(action) + "\"}"; break;
        }

        // Bump version and push to peers
        long newVer = meshVersion.incrementAndGet();
        Settings.Global.putLong(getContentResolver(), "focus_lock_mesh_version", newVer);
        meshPushToPeers();

        return "{\"ok\":true,\"action\":\"" + esc(action) + "\",\"orders_version\":" + newVer + "}";
    }

    private String handleMeshStatus() {
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (nodeId.isEmpty()) nodeId = "pixel";
        // Convenience fields for direct (serverless) Lion's Share polling — match the
        // shape of the relay server's handle_mesh_status() so the same parser works.
        boolean isLocked = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0) == 1;
        int escapes = Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0);
        String paywall = gstr("focus_lock_paywall");
        if (paywall.isEmpty()) paywall = "0";
        long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
        long timerRemainingMs = unlockAt > System.currentTimeMillis() ? unlockAt - System.currentTimeMillis() : 0;
        int taskReps = Settings.Global.getInt(getContentResolver(), "focus_lock_task_reps", 0);
        int taskDone = Settings.Global.getInt(getContentResolver(), "focus_lock_task_done", 0);
        String offer = gstr("focus_lock_offer");
        String offerStatus = gstr("focus_lock_offer_status");
        String subTier = gstr("focus_lock_sub_tier");

        StringBuilder sb = new StringBuilder();
        sb.append("{\"orders_version\":").append(meshVersion.get());
        sb.append(",\"orders\":").append(buildOrdersJson());
        sb.append(",\"signature\":\"\"");
        sb.append(",\"locked\":").append(isLocked);
        sb.append(",\"escapes\":").append(escapes);
        sb.append(",\"paywall\":\"").append(esc(paywall)).append("\"");
        sb.append(",\"timer_remaining_ms\":").append(timerRemainingMs);
        sb.append(",\"task_reps\":").append(taskReps);
        sb.append(",\"task_done\":").append(taskDone);
        sb.append(",\"offer\":\"").append(esc(offer)).append("\"");
        sb.append(",\"offer_status\":\"").append(esc(offerStatus)).append("\"");
        sb.append(",\"sub_tier\":\"").append(esc(subTier)).append("\"");
        sb.append(",\"nodes\":{\"").append(esc(nodeId)).append("\":{\"type\":\"phone\",\"online\":true,\"orders_version\":")
          .append(meshVersion.get()).append(",\"status\":{\"escapes\":")
          .append(escapes).append("}}");
        for (java.util.Map.Entry<String, String[]> e : meshPeers.entrySet()) {
            String[] info = e.getValue();
            sb.append(",\"").append(esc(e.getKey())).append("\":{\"type\":\"").append(esc(info[0]))
              .append("\",\"online\":true,\"addresses\":[\"").append(esc(info[1])).append("\"],\"port\":").append(info[2]).append("}");
        }
        sb.append("}}");
        return sb.toString();
    }

    private String handleMeshPing() {
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (nodeId.isEmpty()) nodeId = "pixel";
        return "{\"ok\":true,\"node_id\":\"" + esc(nodeId) + "\",\"orders_version\":" + meshVersion.get()
            + ",\"timestamp\":" + System.currentTimeMillis() + "}";
    }

    private static final String KEYSTORE_ALIAS = "focuslock_vault_key";

    /**
     * Ensure the slave has an RSA-2048 keypair for vault decryption.
     * Prefers AndroidKeyStore (non-extractable, hardware-backed on Pixel).
     * Falls back to legacy Settings.Global key during migration.
     * Returns the pubkey DER bytes (cached after first call), or null on failure.
     */
    private byte[] ensureNodeKeypair() {
        if (cachedNodePubDer != null) return cachedNodePubDer;
        try {
            // 1. Check if AndroidKeyStore key exists
            java.security.KeyStore ks = java.security.KeyStore.getInstance("AndroidKeyStore");
            ks.load(null);
            if (ks.containsAlias(KEYSTORE_ALIAS)) {
                java.security.cert.Certificate cert = ks.getCertificate(KEYSTORE_ALIAS);
                byte[] pubDer = cert.getPublicKey().getEncoded();
                String pub = android.util.Base64.encodeToString(pubDer, android.util.Base64.NO_WRAP);
                Settings.Global.putString(getContentResolver(), "focus_lock_node_pubkey", pub);
                cachedNodePubDer = pubDer;
                cachedNodePrivKey = (java.security.PrivateKey) ks.getKey(KEYSTORE_ALIAS, null);
                return pubDer;
            }

            // 2. Generate new key in AndroidKeyStore
            android.security.keystore.KeyGenParameterSpec spec =
                new android.security.keystore.KeyGenParameterSpec.Builder(
                    KEYSTORE_ALIAS,
                    android.security.keystore.KeyProperties.PURPOSE_DECRYPT
                    | android.security.keystore.KeyProperties.PURPOSE_SIGN)
                .setKeySize(2048)
                .setDigests(android.security.keystore.KeyProperties.DIGEST_SHA256)
                .setEncryptionPaddings(android.security.keystore.KeyProperties.ENCRYPTION_PADDING_RSA_OAEP)
                .setSignaturePaddings(android.security.keystore.KeyProperties.SIGNATURE_PADDING_RSA_PKCS1)
                .build();
            KeyPairGenerator kpg = KeyPairGenerator.getInstance(
                android.security.keystore.KeyProperties.KEY_ALGORITHM_RSA, "AndroidKeyStore");
            kpg.initialize(spec);
            java.security.KeyPair kp = kpg.generateKeyPair();
            byte[] pubDer = kp.getPublic().getEncoded();
            String pub = android.util.Base64.encodeToString(pubDer, android.util.Base64.NO_WRAP);
            Settings.Global.putString(getContentResolver(), "focus_lock_node_pubkey", pub);

            // 3. If there was a legacy Settings.Global key, this is a migration.
            //    Keep the old privkey until the new key is registered and approved.
            //    Flag that we need to re-register with the new pubkey.
            String oldPriv = gstr("focus_lock_node_privkey");
            if (!oldPriv.isEmpty()) {
                Settings.Global.putString(getContentResolver(), "focus_lock_node_privkey_legacy", oldPriv);
                Settings.Global.putString(getContentResolver(), "focus_lock_node_privkey", "");
                Log.w(TAG, "KeyStore migration: new key generated, legacy key preserved for transition");
            }

            Log.w(TAG, "Generated KeyStore vault keypair (slot=" + VaultCrypto.slotIdForPubkey(pubDer) + ")");
            cachedNodePubDer = pubDer;
            cachedNodePrivKey = kp.getPrivate();
            return pubDer;
        } catch (Exception e) {
            Log.e(TAG, "ensureNodeKeypair (KeyStore) failed, falling back to legacy", e);
            return ensureNodeKeypairLegacy();
        }
    }

    /** Legacy key generation (software-backed, extractable). Used as fallback. */
    private byte[] ensureNodeKeypairLegacy() {
        try {
            String pubB64 = gstr("focus_lock_node_pubkey");
            String privB64 = gstr("focus_lock_node_privkey");
            if (!pubB64.isEmpty() && !privB64.isEmpty()) {
                return android.util.Base64.decode(pubB64, android.util.Base64.DEFAULT);
            }
            KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
            kpg.initialize(2048);
            java.security.KeyPair kp = kpg.generateKeyPair();
            byte[] pubDer = kp.getPublic().getEncoded();
            byte[] privDer = kp.getPrivate().getEncoded();
            String pub = android.util.Base64.encodeToString(pubDer, android.util.Base64.NO_WRAP);
            String priv = android.util.Base64.encodeToString(privDer, android.util.Base64.NO_WRAP);
            Settings.Global.putString(getContentResolver(), "focus_lock_node_pubkey", pub);
            Settings.Global.putString(getContentResolver(), "focus_lock_node_privkey", priv);
            Log.w(TAG, "Generated legacy vault keypair (slot=" + VaultCrypto.slotIdForPubkey(pubDer) + ")");
            return pubDer;
        } catch (Exception e) {
            Log.e(TAG, "ensureNodeKeypairLegacy failed", e);
            return null;
        }
    }

    /**
     * Get the PrivateKey for vault operations. Tries KeyStore first, falls
     * back to legacy Settings.Global key during migration.
     */
    private java.security.PrivateKey getNodePrivateKey() {
        if (cachedNodePrivKey != null) return cachedNodePrivKey;
        try {
            java.security.KeyStore ks = java.security.KeyStore.getInstance("AndroidKeyStore");
            ks.load(null);
            if (ks.containsAlias(KEYSTORE_ALIAS)) {
                cachedNodePrivKey = (java.security.PrivateKey) ks.getKey(KEYSTORE_ALIAS, null);
                return cachedNodePrivKey;
            }
        } catch (Exception e) {
            Log.w(TAG, "KeyStore getKey failed: " + e);
        }
        // Fallback to legacy key or migration key
        String privB64 = gstr("focus_lock_node_privkey");
        if (privB64.isEmpty()) privB64 = gstr("focus_lock_node_privkey_legacy");
        if (!privB64.isEmpty()) {
            try {
                byte[] privDer = android.util.Base64.decode(privB64, android.util.Base64.DEFAULT);
                return java.security.KeyFactory.getInstance("RSA")
                    .generatePrivate(new java.security.spec.PKCS8EncodedKeySpec(privDer));
            } catch (Exception e) {
                Log.e(TAG, "Legacy privkey decode failed", e);
            }
        }
        return null;
    }

    /**
     * Phase C vault reader (see docs/VAULT-DESIGN.md §7.2).
     * Polls /vault/{mesh_id}/since/{current_version}, verifies each blob's
     * Lion signature, decrypts the body, and applies orders. Best-effort:
     * failures are logged but the legacy gossip continues to run.
     *
     * Also handles first-time node registration via register-node-request.
     */
    private void vaultSync() {
        try {
            String meshId = gstr("focus_lock_mesh_id");
            String meshUrl = gstr("focus_lock_mesh_url");
            String lionPub = gstr("focus_lock_lion_pubkey");
            if (meshId.isEmpty() || meshUrl.isEmpty() || lionPub.isEmpty()) return;

            byte[] myPubDer = ensureNodeKeypair();
            if (myPubDer == null) return;
            java.security.PrivateKey myPrivKey = getNodePrivateKey();
            if (myPrivKey == null) { Log.e(TAG, "vault: no private key available"); return; }
            String mySlotId = VaultCrypto.slotIdForPubkey(myPubDer);

            // 1. Fetch blobs since current version
            long currentVersion = meshVersion.get();
            String url = meshUrl + "/vault/" + meshId + "/since/" + currentVersion;
            String resp = vaultHttpGet(url);
            if (resp == null) {
                Log.w(TAG, "vault: GET " + url + " failed");
                return;
            }

            org.json.JSONObject sinceResp = new org.json.JSONObject(resp);
            org.json.JSONArray blobsArr = sinceResp.optJSONArray("blobs");
            if (blobsArr == null || blobsArr.length() == 0) {
                // No new blobs. If our slot doesn't exist yet (we've never been added),
                // post a register-node-request so the Lion can approve us.
                vaultRegisterIfNeeded(meshUrl, meshId, mySlotId, myPubDer);
                return;
            }

            int applied = 0;
            int skipped = 0;
            boolean needRegister = false;
            long latestApplied = currentVersion;
            for (int i = 0; i < blobsArr.length(); i++) {
                org.json.JSONObject blobJson = blobsArr.getJSONObject(i);
                java.util.Map<String, Object> blob = VaultCrypto.jsonToMap(blobJson);
                long ver = 0;
                Object verObj = blob.get("version");
                if (verObj instanceof Number) ver = ((Number) verObj).longValue();
                if (ver <= latestApplied) { skipped++; continue; }

                // Phase D two-writer: blobs may be signed by Lion (orders/RPC) or
                // by THIS slave (our own runtime pushes). After a data wipe + restart,
                // meshVersion resets to 0 and we'll re-fetch our own past pushes —
                // verifying them with lionPub would always fail. Try lionPub first,
                // then fall back to our own pubkey to detect self-pushes.
                boolean isLionSigned = VaultCrypto.verifySignature(blob, lionPub);
                boolean isSelfSigned = false;
                if (!isLionSigned) {
                    String myPubB64 = android.util.Base64.encodeToString(
                        myPubDer, android.util.Base64.NO_WRAP);
                    isSelfSigned = VaultCrypto.verifySignature(blob, myPubB64);
                }
                if (!isLionSigned && !isSelfSigned) {
                    Log.w(TAG, "vault: REJECTED v" + ver + " (bad signature)");
                    skipped++;
                    continue;
                }
                if (isSelfSigned) {
                    // Our own runtime push — local state is already authoritative,
                    // there's nothing to apply. Just advance meshVersion so we
                    // don't keep re-fetching it on every poll cycle.
                    Log.i(TAG, "vault: skipping v" + ver + " (own runtime push)");
                    synchronized (meshVersion) {
                        if (ver > meshVersion.get()) {
                            meshVersion.set(ver);
                            Settings.Global.putLong(getContentResolver(),
                                "focus_lock_mesh_version", ver);
                            latestApplied = ver;
                        }
                    }
                    skipped++;
                    continue;
                }

                // Find our slot. If absent, we're not (yet) a recipient — request registration.
                Object slotsObj = blob.get("slots");
                if (slotsObj instanceof java.util.Map
                    && !((java.util.Map<?, ?>) slotsObj).containsKey(mySlotId)) {
                    needRegister = true;
                    skipped++;
                    continue;
                }

                String orders = VaultCrypto.decryptOrders(blob, myPrivKey, myPubDer);
                if (orders == null) {
                    Log.w(TAG, "vault: decrypt failed for v" + ver);
                    skipped++;
                    continue;
                }

                synchronized (meshVersion) {
                    if (ver > meshVersion.get()) {
                        // Phase D: blobs may be either state-snapshot orders
                        // (legacy applyOrdersFromMesh path) or RPC blobs from
                        // Lion's Share's apiVault() (new vault_only write path).
                        // RPC blobs carry an "action" field at the top of the body;
                        // dispatch them through handleMeshOrder so existing
                        // doLock/doUnlock/etc. handlers run with full effect.
                        boolean isRpc = false;
                        try {
                            org.json.JSONObject probe = new org.json.JSONObject(orders);
                            isRpc = probe.has("action") && !probe.optString("action", "").isEmpty();
                        } catch (Exception e) {
                            // Not JSON object — treat as legacy state snapshot
                        }
                        if (isRpc) {
                            Log.w(TAG, "vault: dispatching v" + ver + " RPC blob (" + orders.length() + " bytes)");
                            try {
                                String result = handleMeshOrder(orders);
                                Log.i(TAG, "vault: RPC result: " + (result == null ? "null" : result));
                            } catch (Exception e) {
                                Log.w(TAG, "vault: RPC dispatch failed: " + e.getMessage());
                            }
                        } else {
                            Log.w(TAG, "vault: applying v" + ver + " (orders=" + orders.length() + " bytes)");
                            applyOrdersFromMesh(orders);
                        }
                        meshVersion.set(ver);
                        Settings.Global.putLong(getContentResolver(), "focus_lock_mesh_version", ver);
                        latestApplied = ver;
                        applied++;
                    } else {
                        skipped++;
                    }
                }
            }

            if (needRegister) {
                vaultRegisterIfNeeded(meshUrl, meshId, mySlotId, myPubDer);
            }
            if (applied > 0 || skipped > 0) {
                Log.w(TAG, "vault: sync done (applied=" + applied + " skipped=" + skipped
                    + " current=" + meshVersion.get() + ")");
            }
        } catch (Exception e) {
            Log.e(TAG, "vaultSync error", e);
        }
    }

    /** Submit an unsigned register-node-request so Lion's Share can approve us. */
    private void vaultRegisterIfNeeded(String meshUrl, String meshId, String mySlotId, byte[] myPubDer) {
        try {
            // Throttle: don't re-request more than once per hour
            long lastReq = 0;
            try { lastReq = Long.parseLong(gstr("focus_lock_vault_last_register_req")); } catch (Exception e) {}
            long now = System.currentTimeMillis();
            if (now - lastReq < 3600 * 1000L) return;

            String pubB64 = android.util.Base64.encodeToString(myPubDer, android.util.Base64.NO_WRAP);
            String nodeId = gstr("focus_lock_mesh_node_id");
            if (nodeId.isEmpty()) nodeId = "pixel";
            String body = "{\"node_id\":\"" + esc(nodeId)
                + "\",\"node_type\":\"phone\""
                + ",\"node_pubkey\":\"" + pubB64 + "\"}";
            String resp = vaultHttpPost(meshUrl + "/vault/" + meshId + "/register-node-request", body);
            if (resp != null) {
                Log.w(TAG, "vault: posted register-node-request (slot=" + mySlotId + ")");
                Settings.Global.putString(getContentResolver(),
                    "focus_lock_vault_last_register_req", String.valueOf(now));
            }
        } catch (Exception e) {
            Log.w(TAG, "vault: register-node-request failed: " + e.getMessage());
        }
    }

    private String vaultHttpGet(String url) {
        try {
            java.net.URL u = new java.net.URL(url);
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) u.openConnection();
            conn.setRequestMethod("GET");
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            int code = conn.getResponseCode();
            if (code != 200) {
                conn.disconnect();
                return null;
            }
            BufferedReader br = new BufferedReader(new InputStreamReader(conn.getInputStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
            br.close();
            conn.disconnect();
            return sb.toString();
        } catch (Exception e) {
            return null;
        }
    }

    private String vaultHttpPost(String url, String body) {
        try {
            java.net.URL u = new java.net.URL(url);
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) u.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.getOutputStream().write(body.getBytes());
            int code = conn.getResponseCode();
            BufferedReader br = new BufferedReader(new InputStreamReader(
                code == 200 ? conn.getInputStream() : conn.getErrorStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = br.readLine()) != null) sb.append(line);
            br.close();
            conn.disconnect();
            return code == 200 ? sb.toString() : null;
        } catch (Exception e) {
            return null;
        }
    }

    /** Variant of vaultHttpPost that returns the response code so callers can
     * distinguish 200 OK / 409 conflict / other failures. Used by vaultRuntimePush
     * to retry on version conflicts when Lion has appended in parallel. */
    private int[] vaultHttpPostWithCode(String url, String body) {
        try {
            java.net.URL u = new java.net.URL(url);
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) u.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.getOutputStream().write(body.getBytes());
            int code = conn.getResponseCode();
            // Drain the stream so the connection can be reused / closed cleanly,
            // but we don't care about the body (success/conflict are conveyed
            // by the status code alone for the runtime push path).
            try {
                BufferedReader br = new BufferedReader(new InputStreamReader(
                    code == 200 ? conn.getInputStream() : conn.getErrorStream()));
                while (br.readLine() != null) { /* drain */ }
                br.close();
            } catch (Exception e) { /* ignore */ }
            conn.disconnect();
            return new int[]{code};
        } catch (Exception e) {
            return new int[]{-1};
        }
    }

    /**
     * Phase D: assemble the runtime body Map that will be encrypted into a
     * slave-signed vault blob. Mirrors the union of fields the controller's
     * updateLiveStatus() (MainActivity.java L293) consumes, so once decrypted,
     * Lion's Share can drive the entire UI from this Map alone — no /mesh/status
     * fetch required.
     *
     * Field set is intentionally a superset of handleMeshStatus() (L2268) and
     * doStatus() (L626) so consumers downstream can pick whatever they need.
     */
    private java.util.TreeMap<String, Object> buildRuntimeBodyMap() {
        java.util.TreeMap<String, Object> body = new java.util.TreeMap<>();
        body.put("locked",
            Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0) == 1);
        body.put("escapes",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0));
        String paywall = gstr("focus_lock_paywall");
        body.put("paywall", paywall.isEmpty() ? "0" : paywall);
        long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
        long timerRemainingMs = unlockAt > System.currentTimeMillis()
            ? unlockAt - System.currentTimeMillis() : 0L;
        body.put("timer_remaining_ms", timerRemainingMs);
        body.put("task_reps",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_task_reps", 0));
        body.put("task_done",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_task_done", 0));
        body.put("offer", gstr("focus_lock_offer"));
        body.put("offer_status", gstr("focus_lock_offer_status"));
        body.put("sub_tier", gstr("focus_lock_sub_tier"));
        body.put("sub_due",
            Settings.Global.getLong(getContentResolver(), "focus_lock_sub_due", 0));
        body.put("free_unlocks",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_free_unlocks", 0));
        body.put("mode", gstr("focus_lock_mode"));
        body.put("message", gstr("focus_lock_message"));
        body.put("pinned_message", gstr("focus_lock_pinned_message"));
        body.put("compliment", gstr("focus_lock_compliment"));
        // Fine
        body.put("fine_active",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_fine_active", 0));
        body.put("fine_amount",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_fine_amount", 0));
        body.put("fine_interval_m",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_fine_interval_m", 0));
        // Body check
        body.put("body_check_active",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_body_check_active", 0));
        body.put("body_check_area", gstr("focus_lock_body_check_area"));
        body.put("body_check_streak",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_body_check_streak", 0));
        body.put("body_check_last_result", gstr("focus_lock_body_check_last_result"));
        // Lovense — 1 if a toy session is active or registered, derived from settings.
        body.put("lovense_available",
            Settings.Global.getInt(getContentResolver(), "focus_lock_lovense_available", 0) == 1);
        // Geofence + curfew
        body.put("geofence_active", !gstr("focus_lock_geofence_lat").isEmpty());
        body.put("geofence_radius", gstr("focus_lock_geofence_radius_m"));
        body.put("curfew_enabled",
            Settings.Global.getInt(getContentResolver(), "focus_lock_curfew_enabled", 0) == 1);
        // Check-in
        body.put("checkin_deadline",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_checkin_deadline", -1));
        body.put("checkin_last",
            Settings.Global.getLong(getContentResolver(), "focus_lock_checkin_timestamp", 0));
        // Desktop state
        body.put("desktop_locked",
            Settings.Global.getInt(getContentResolver(), "focus_lock_desktop_active", 0) == 1);
        body.put("desktop_locked_devices", gstr("focus_lock_desktop_locked_devices"));
        body.put("desktops", gstr("focus_lock_desktops"));
        body.put("entrapped",
            Settings.Global.getInt(getContentResolver(), "focus_lock_entrapped", 0) == 1);
        // Screen time leash
        body.put("screen_time_quota_minutes",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_screen_time_quota_minutes", 0));
        body.put("screen_time_used_today",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_screen_time_used_today", 0));
        // Bedtime
        body.put("bedtime_enabled",
            Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_enabled", 0) == 1);
        body.put("bedtime_lock_hour",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_lock_hour", -1));
        body.put("bedtime_unlock_hour",
            (long) Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_unlock_hour", -1));
        body.put("orders_version", meshVersion.get());
        // Nodes registry — controller's doUnlockDevice() and refreshInbox() consume this.
        java.util.TreeMap<String, Object> nodes = new java.util.TreeMap<>();
        String selfId = gstr("focus_lock_mesh_node_id");
        if (selfId.isEmpty()) selfId = "pixel";
        java.util.TreeMap<String, Object> selfEntry = new java.util.TreeMap<>();
        selfEntry.put("type", "phone");
        selfEntry.put("online", true);
        selfEntry.put("orders_version", meshVersion.get());
        nodes.put(selfId, selfEntry);
        for (java.util.Map.Entry<String, String[]> e : meshPeers.entrySet()) {
            String[] info = e.getValue();
            java.util.TreeMap<String, Object> peerEntry = new java.util.TreeMap<>();
            peerEntry.put("type", info.length > 0 ? info[0] : "unknown");
            peerEntry.put("online", true);
            peerEntry.put("address", info.length > 1 ? info[1] : "");
            peerEntry.put("port", info.length > 2 ? info[2] : "");
            nodes.put(e.getKey(), peerEntry);
        }
        body.put("nodes", nodes);
        // Phase D messaging: surface the last 30 entries of the message
        // history so the controller's refreshInbox / updateMessageThread
        // can render them straight from the decrypted runtime body. Cap is
        // 30 to keep blob size bounded — appendMessageHistory caps the
        // store itself at 50, this just trims further for the wire.
        java.util.ArrayList<Object> messages = new java.util.ArrayList<>();
        try {
            String histJson = gstr("focus_lock_message_history");
            if (!histJson.isEmpty()) {
                org.json.JSONArray arr = new org.json.JSONArray(histJson);
                int start = Math.max(0, arr.length() - 30);
                for (int i = start; i < arr.length(); i++) {
                    org.json.JSONObject obj = arr.getJSONObject(i);
                    java.util.TreeMap<String, Object> m = new java.util.TreeMap<>();
                    java.util.Iterator<String> keys = obj.keys();
                    while (keys.hasNext()) {
                        String k = keys.next();
                        Object v = obj.get(k);
                        // Flatten any nested JSON to its string form — the
                        // controller's parser is field-by-field, never
                        // recurses, and the canonical-JSON serializer
                        // doesn't accept org.json types directly.
                        if (v instanceof org.json.JSONObject
                            || v instanceof org.json.JSONArray) {
                            m.put(k, v.toString());
                        } else {
                            m.put(k, v);
                        }
                    }
                    messages.add(m);
                }
            }
        } catch (Exception e) {
            Log.w(TAG, "buildRuntimeBodyMap: read messages failed: " + e.getMessage());
        }
        body.put("messages", messages);
        return body;
    }

    /**
     * Phase D: append a message to the slave's persistent history. Used by
     * the send-message action handler so Lion's apiVault RPC blobs land in
     * Settings.Global, then surface in buildRuntimeBodyMap()'s `messages`
     * array on the next vaultRuntimePush tick.
     *
     * History is capped at 50 entries (FIFO trim) to keep the runtime body
     * size bounded — the controller's inbox renders the last 30 anyway.
     *
     * Format mirrors the legacy /mesh/messages shape so the controller's
     * existing updateMessageThread() parser works without modification:
     *   {id, ts, from, text|ciphertext+encrypted_key+iv, encrypted, pinned,
     *    mandatory_reply, replied, read_by_bunny, attachment_url}
     */
    private synchronized void appendMessageHistory(org.json.JSONObject src) {
        try {
            String existing = gstr("focus_lock_message_history");
            org.json.JSONArray arr;
            if (existing.isEmpty()) {
                arr = new org.json.JSONArray();
            } else {
                try {
                    arr = new org.json.JSONArray(existing);
                } catch (Exception e) {
                    arr = new org.json.JSONArray();
                }
            }

            org.json.JSONObject entry = new org.json.JSONObject();
            long ts = System.currentTimeMillis();
            entry.put("id", String.valueOf(ts));
            entry.put("ts", ts);

            // Copy whatever fields the caller provided. We never set id/ts
            // from the caller — those are slave-authoritative so the
            // controller can't conflict by guessing them.
            java.util.Iterator<String> keys = src.keys();
            while (keys.hasNext()) {
                String k = keys.next();
                if (k.equals("id") || k.equals("ts")) continue;
                entry.put(k, src.get(k));
            }
            if (!entry.has("from")) entry.put("from", "lion");

            arr.put(entry);

            // Cap at 50 entries — drop oldest first
            while (arr.length() > 50) {
                arr.remove(0);
            }

            Settings.Global.putString(getContentResolver(),
                "focus_lock_message_history", arr.toString());
        } catch (Exception e) {
            Log.w(TAG, "appendMessageHistory failed: " + e.getMessage());
        }
    }

    /**
     * Phase D: encrypt the current runtime state into a slave-signed vault
     * blob and append it to /vault/{mesh_id}/append. The controller will
     * decrypt it via vaultPollLoop and feed updateLiveStatus() — replacing
     * the legacy /api/mesh/{id}/status polling path.
     *
     * Dedup: skips the push if the canonical-JSON hash of the body matches
     * the last successful push. Runtime state mostly idle → most ticks no-op.
     */
    private void vaultRuntimePush() {
        try {
            String meshId = gstr("focus_lock_mesh_id");
            String meshUrl = gstr("focus_lock_mesh_url");
            if (meshId.isEmpty() || meshUrl.isEmpty()) return;

            // Need our own pubkey + privkey to sign and to find our slot.
            byte[] myPubDer = ensureNodeKeypair();
            if (myPubDer == null) return;
            java.security.PrivateKey myPrivKey = getNodePrivateKey();
            if (myPrivKey == null) { Log.e(TAG, "vault push: no private key"); return; }
            String mySlotId = VaultCrypto.slotIdForPubkey(myPubDer);

            // Fetch the recipient list from the server. We can only push if
            // we're an approved recipient ourselves (otherwise we have no slot
            // and the controller has nothing to decrypt anyway).
            String nodesResp = vaultHttpGet(meshUrl + "/vault/" + meshId + "/nodes");
            if (nodesResp == null) return;
            org.json.JSONArray nodesArr = new org.json.JSONObject(nodesResp).optJSONArray("nodes");
            if (nodesArr == null || nodesArr.length() == 0) return;

            java.util.ArrayList<VaultCrypto.NodePubkey> recipients = new java.util.ArrayList<>();
            boolean selfApproved = false;
            String myPubB64 = android.util.Base64.encodeToString(myPubDer, android.util.Base64.NO_WRAP);
            for (int i = 0; i < nodesArr.length(); i++) {
                org.json.JSONObject node = nodesArr.getJSONObject(i);
                String nid = node.optString("node_id", "");
                String npub = node.optString("node_pubkey", "");
                if (nid.isEmpty() || npub.isEmpty()) continue;
                recipients.add(new VaultCrypto.NodePubkey(nid, npub));
                // Compare cleaned base64 to detect ourselves
                if (npub.replaceAll("\\s", "").equals(myPubB64.replaceAll("\\s", ""))) {
                    selfApproved = true;
                }
            }
            if (!selfApproved) {
                // Lion hasn't approved us yet. vaultSync will post the
                // register-node-request on its tick — nothing for us to do here.
                return;
            }
            if (recipients.isEmpty()) return;

            // Build the body and skip the push if it's identical to the last one.
            java.util.TreeMap<String, Object> body = buildRuntimeBodyMap();
            String hash = VaultCrypto.bodyHash(body);
            if (!hash.isEmpty() && hash.equals(lastRuntimeBodyHash)) {
                return;
            }

            // Pick a version greater than the current. Lion may append in
            // parallel; on 409 we re-fetch and retry once.
            long version = meshVersion.get() + 1;
            for (int attempt = 0; attempt < 3; attempt++) {
                long createdAt = System.currentTimeMillis();
                java.util.Map<String, Object> blob =
                    VaultCrypto.encryptBody(meshId, version, createdAt, body, recipients);
                String signature = VaultCrypto.signBlob(blob, myPrivKey);
                blob.put("signature", signature);
                String blobJson = new String(VaultCrypto.canonicalJson(blob));

                int[] resp = vaultHttpPostWithCode(
                    meshUrl + "/vault/" + meshId + "/append", blobJson);
                int code = resp[0];
                if (code == 200) {
                    lastRuntimeBodyHash = hash;
                    Log.w(TAG, "vault: runtime push v" + version
                        + " (" + recipients.size() + " slots, " + blobJson.length() + " bytes)");
                    return;
                }
                if (code == 409) {
                    // Lion appended in parallel. Re-fetch current version and try again.
                    String sinceResp = vaultHttpGet(
                        meshUrl + "/vault/" + meshId + "/since/0");
                    if (sinceResp != null) {
                        long current = new org.json.JSONObject(sinceResp).optLong("current_version", 0);
                        if (current >= version) version = current + 1;
                        else version++;
                    } else {
                        version++;
                    }
                    continue;
                }
                Log.w(TAG, "vault: runtime push failed code=" + code);
                return;
            }
            Log.w(TAG, "vault: runtime push gave up after 3 attempts (lion appending faster than us)");
        } catch (Exception e) {
            Log.e(TAG, "vaultRuntimePush error", e);
        }
    }

    private void meshGossip() {
        // Phase D short-circuit: if the relay told us this mesh is vault_only,
        // skip plaintext gossip to server peers entirely. The vault path
        // (vaultSync + vaultRuntimePush) carries everything in encrypted form.
        // We still gossip to phone↔desktop peers because those use /mesh/sync
        // (peer-to-peer, never the relay) and stay legacy by design.
        // The flag clears itself if a future tick sees a 200 from a server peer.
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (nodeId.isEmpty()) nodeId = "pixel";
        String meshPin = gstr("focus_lock_pin");
        // Multi-tenant mesh: when joined to an account-based mesh via /api/mesh/join,
        // BunnyTasker writes focus_lock_mesh_id. Server peers must then be addressed at
        // /api/mesh/{mesh_id}/sync instead of the legacy single-mesh /mesh/sync path.
        String meshId = gstr("focus_lock_mesh_id");

        // Resolve current local addresses for this gossip tick (fix: stale address announcement)
        String localAddrs = getLocalAddressesJson();

        // Contact all peers in parallel (fix: sequential gossip blocked all peers)
        java.util.List<Thread> threads = new java.util.ArrayList<>();
        final String nid = nodeId;
        final String pin = meshPin;
        final String mid = meshId;
        for (java.util.Map.Entry<String, String[]> entry : meshPeers.entrySet()) {
            final String peerId = entry.getKey();
            final String[] info = entry.getValue();
            final String peerType = info[0];
            final String addr = info[1];
            final int port;
            int tmp = 8434; try { tmp = Integer.parseInt(info[2]); } catch (Exception e) {} port = tmp;
            final String scheme = info.length > 3 ? info[3] : "http";
            final String addrsJson = localAddrs;
            // Server peers in a multi-tenant mesh use the account-based sync path.
            // Phone↔desktop gossip stays on /mesh/sync (desktops only expose that route).
            final boolean isServerPeer = !mid.isEmpty() && "server".equals(peerType);
            final String syncPath = isServerPeer
                ? ("/api/mesh/" + mid + "/sync")
                : "/mesh/sync";

            // Phase D: skip server peers entirely once we know the mesh is vault_only.
            // Phone↔desktop peers continue to gossip plaintext (they're never relayed).
            if (isServerPeer && vaultOnlyDetected) {
                continue;
            }

            Thread t = new Thread(() -> {
                try {
                    String payload = "{\"pin\":\"" + esc(pin)
                        + "\",\"node_id\":\"" + esc(nid)
                        + "\",\"type\":\"phone\",\"addresses\":" + addrsJson
                        + ",\"orders_version\":" + meshVersion.get() + ",\"status\":{}}";
                    java.net.URL url = new java.net.URL(scheme + "://" + addr + ":" + port + syncPath);
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/json");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.getOutputStream().write(payload.getBytes());

                    int respCode = conn.getResponseCode();
                    // Phase D: 410 Gone means this mesh has been flipped to
                    // vault_only on the server. Latch the flag so we stop
                    // hitting /api/mesh/{id}/sync for the rest of this session.
                    if (respCode == 410 && isServerPeer) {
                        if (!vaultOnlyDetected) {
                            Log.w(TAG, "Mesh gossip: server reports vault_only — suppressing plaintext gossip");
                            vaultOnlyDetected = true;
                        }
                        conn.disconnect();
                        return;
                    }
                    if (respCode == 200) {
                        BufferedReader br = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = br.readLine()) != null) sb.append(line);
                        String respBody = sb.toString();

                        String remVerStr = jval(respBody, "orders_version");
                        long remVer = 0;
                        try { remVer = Long.parseLong(remVerStr); } catch (Exception e) {}

                        // Mark peer as seen (for stale peer pruning)
                        meshPeerLastSeen.put(peerId, System.currentTimeMillis());

                        if (remVer > meshVersion.get()) {
                            // Extract and apply orders (synchronized to avoid races between threads)
                            synchronized (meshVersion) {
                                if (remVer > meshVersion.get()) {
                                    int ordersStart = respBody.indexOf("\"orders\"");
                                    if (ordersStart >= 0) {
                                        int braceStart = respBody.indexOf("{", ordersStart + 8);
                                        if (braceStart >= 0) {
                                            int depth = 0; int braceEnd = braceStart;
                                            for (int i = braceStart; i < respBody.length(); i++) {
                                                if (respBody.charAt(i) == '{') depth++;
                                                else if (respBody.charAt(i) == '}') { depth--; if (depth == 0) { braceEnd = i; break; } }
                                            }
                                            String ordersJson = respBody.substring(braceStart, braceEnd + 1);
                                            Log.w(TAG, "Mesh gossip: applying v" + remVer + " from " + peerId);
                                            applyOrdersFromMesh(ordersJson);
                                            meshVersion.set(remVer);
                                            Settings.Global.putLong(getContentResolver(), "focus_lock_mesh_version", meshVersion.get());
                                        }
                                    }
                                }
                            }
                        }

                        // Learn about new peers from known_nodes — auto-register any node
                        gossipLearnPeers(respBody, nid);
                    }
                    conn.disconnect();
                } catch (Exception e) {
                    // Peer unreachable — that's fine, mesh is resilient
                }
            });
            t.setDaemon(true);
            threads.add(t);
            t.start();
        }

        // Wait for all threads with 10s deadline
        long deadline = System.currentTimeMillis() + 10000;
        for (Thread t : threads) {
            long remaining = deadline - System.currentTimeMillis();
            if (remaining > 0) {
                try { t.join(remaining); } catch (InterruptedException e) {}
            }
        }

        // Prune peers not seen in 24 hours (fix: stale peer accumulation)
        pruneDeadPeers();
    }

    /** Extract and register new peers from a gossip response's known_nodes field. */
    private void gossipLearnPeers(String respBody, String nodeId) {
        int knIdx = respBody.indexOf("\"known_nodes\"");
        if (knIdx < 0) return;
        int knBrace = respBody.indexOf("{", knIdx + 13);
        if (knBrace < 0) return;
        int knDepth = 0; int knEnd = knBrace;
        for (int ki = knBrace; ki < respBody.length(); ki++) {
            if (respBody.charAt(ki) == '{') knDepth++;
            else if (respBody.charAt(ki) == '}') { knDepth--; if (knDepth == 0) { knEnd = ki; break; } }
        }
        String knJson = respBody.substring(knBrace, knEnd + 1);
        // Dynamic discovery: find all quoted keys at depth 1
        int pos = 1; // skip opening brace
        while (pos < knJson.length()) {
            int qStart = knJson.indexOf("\"", pos);
            if (qStart < 0) break;
            int qEnd = knJson.indexOf("\"", qStart + 1);
            if (qEnd < 0) break;
            String knownId = knJson.substring(qStart + 1, qEnd);
            int colonPos = knJson.indexOf(":", qEnd);
            if (colonPos < 0) break;
            char afterColon = ' ';
            for (int ci = colonPos + 1; ci < knJson.length(); ci++) {
                if (knJson.charAt(ci) != ' ') { afterColon = knJson.charAt(ci); break; }
            }
            if (afterColon == '{' && !meshPeers.containsKey(knownId) && !knownId.equals(nodeId)) {
                String knAddr = jval(knJson.substring(qStart), "addresses");
                String knPort = jval(knJson.substring(qStart), "port");
                String knType = jval(knJson.substring(qStart), "type");
                if (knAddr != null) {
                    knAddr = knAddr.replace("[","").replace("]","").replace("\"","").split(",")[0].trim();
                    if (!knAddr.isEmpty()) {
                        meshPeers.put(knownId, new String[]{knType != null ? knType : "unknown", knAddr, knPort != null ? knPort : "8435", "http"});
                        Log.i(TAG, "Mesh: discovered peer " + knownId + " at " + knAddr + ":" + knPort);
                        saveMeshPeers();
                    }
                }
            }
            if (afterColon == '{') {
                int d = 0;
                for (int si = colonPos + 1; si < knJson.length(); si++) {
                    if (knJson.charAt(si) == '{') d++;
                    else if (knJson.charAt(si) == '}') { d--; if (d == 0) { pos = si + 1; break; } }
                }
            } else {
                pos = colonPos + 1;
            }
        }
    }

    /** Get current local IP addresses as a JSON array string, refreshed each call. */
    private String getLocalAddressesJson() {
        StringBuilder sb = new StringBuilder("[");
        try {
            java.util.Enumeration<java.net.NetworkInterface> ifaces = java.net.NetworkInterface.getNetworkInterfaces();
            boolean first = true;
            while (ifaces != null && ifaces.hasMoreElements()) {
                java.net.NetworkInterface iface = ifaces.nextElement();
                if (iface.isLoopback() || !iface.isUp()) continue;
                java.util.Enumeration<java.net.InetAddress> addrs = iface.getInetAddresses();
                while (addrs.hasMoreElements()) {
                    java.net.InetAddress a = addrs.nextElement();
                    if (a.isLoopbackAddress() || a instanceof java.net.Inet6Address) continue;
                    if (!first) sb.append(",");
                    sb.append("\"").append(a.getHostAddress()).append("\"");
                    first = false;
                }
            }
        } catch (Exception e) { Log.e(TAG, "getLocalAddresses", e); }
        sb.append("]");
        return sb.toString();
    }

    private static final long PEER_STALE_MS = 24 * 60 * 60 * 1000; // 24 hours

    /** Remove peers that haven't responded in 24 hours. Never prune config-seeded peers. */
    private void pruneDeadPeers() {
        long now = System.currentTimeMillis();
        // Seed peers we never prune — populated from focus_lock_mesh_peers below.
        java.util.Set<String> keepAlways = new java.util.HashSet<>();
        String peersJson = gstr("focus_lock_mesh_peers");
        if (!peersJson.isEmpty()) {
            for (String entry : peersJson.split(";")) {
                String[] parts = entry.split(":");
                if (parts.length >= 1) keepAlways.add(parts[0]);
            }
        }
        boolean pruned = false;
        for (java.util.Map.Entry<String, Long> e : meshPeerLastSeen.entrySet()) {
            if (keepAlways.contains(e.getKey())) continue;
            if (now - e.getValue() > PEER_STALE_MS) {
                meshPeers.remove(e.getKey());
                meshPeerLastSeen.remove(e.getKey());
                Log.i(TAG, "Mesh: pruned stale peer " + e.getKey());
                pruned = true;
            }
        }
        if (pruned) saveMeshPeers();
    }

    private void meshPushToPeers() {
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (nodeId.isEmpty()) nodeId = "pixel";
        String meshPin = gstr("focus_lock_pin");
        // See meshGossip(): server peers in a multi-tenant mesh use /api/mesh/{mesh_id}/sync.
        String meshId = gstr("focus_lock_mesh_id");
        final String ordersJson = buildOrdersJson();
        final long ver = meshVersion.get();
        final String nid = nodeId;
        final String pin = meshPin;
        final String mid = meshId;

        for (java.util.Map.Entry<String, String[]> entry : meshPeers.entrySet()) {
            String[] info = entry.getValue();
            final String peerType = info[0];
            final String addr = info[1];
            final int port;
            int tmp = 8434; try { tmp = Integer.parseInt(info[2]); } catch (Exception e) {} port = tmp;
            final String scheme = info.length > 3 ? info[3] : "http";
            final String syncPath = (!mid.isEmpty() && "server".equals(peerType))
                ? ("/api/mesh/" + mid + "/sync")
                : "/mesh/sync";

            new Thread(() -> {
                try {
                    String payload = "{\"pin\":\"" + esc(pin)
                        + "\",\"node_id\":\"" + esc(nid)
                        + "\",\"type\":\"push\",\"orders_version\":" + ver
                        + ",\"orders\":" + ordersJson + ",\"status\":{}}";
                    java.net.URL url = new java.net.URL(scheme + "://" + addr + ":" + port + syncPath);
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/json");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(5000);
                    conn.setReadTimeout(5000);
                    conn.getOutputStream().write(payload.getBytes());
                    conn.getResponseCode();
                    conn.disconnect();
                } catch (Exception e) {}
            }).start();
        }
    }

    private void saveMeshPeers() {
        StringBuilder sb = new StringBuilder();
        for (java.util.Map.Entry<String, String[]> e : meshPeers.entrySet()) {
            if (sb.length() > 0) sb.append(";");
            String[] info = e.getValue();
            sb.append(e.getKey()).append(":").append(info[0]).append(":").append(info[1]).append(":").append(info[2]);
            if (info.length > 3) sb.append(":").append(info[3]);
        }
        Settings.Global.putString(getContentResolver(), "focus_lock_mesh_peers", sb.toString());
    }

    // Also bump mesh version when existing /api/* endpoints modify state
    private void meshBumpAndPush() {
        long newVer = meshVersion.incrementAndGet();
        Settings.Global.putLong(getContentResolver(), "focus_lock_mesh_version", newVer);
        meshPushToPeers();
    }

    // ── ntfy Push Subscriber ──
    // Port of focuslock_ntfy.py NtfySubscribeThread. HTTP long-poll on ntfy topic.
    // Wake-up triggers immediate vaultSync(). Gossip remains the consistency layer.
    private void ntfySubscribeLoop(String server, String topic) {
        String since = String.valueOf(System.currentTimeMillis() / 1000 - 60);
        int backoff = 1;
        while (running) {
            java.net.HttpURLConnection conn = null;
            try {
                String url = server + "/" + topic + "/json?since=" + since;
                conn = (java.net.HttpURLConnection) new java.net.URL(url).openConnection();
                conn.setRequestMethod("GET");
                conn.setReadTimeout(90_000);
                conn.setConnectTimeout(10_000);
                java.io.BufferedReader reader = new java.io.BufferedReader(
                    new java.io.InputStreamReader(conn.getInputStream(), "UTF-8"));
                String line;
                while (running && (line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty()) continue;
                    try {
                        org.json.JSONObject msg = new org.json.JSONObject(line);
                        String msgId = msg.optString("id", "");
                        if (!msgId.isEmpty()) since = msgId;
                        String event = msg.optString("event", "");
                        if ("open".equals(event) || "keepalive".equals(event)) continue;
                        String body = msg.optString("message", "");
                        if (!body.isEmpty()) {
                            try {
                                org.json.JSONObject data = new org.json.JSONObject(body);
                                int ver = data.optInt("v", -1);
                                if (ver >= 0) {
                                    Log.w(TAG, "ntfy: wake-up v" + ver);
                                    try { vaultSync(); } catch (Exception e) {
                                        Log.w(TAG, "ntfy: vaultSync error: " + e);
                                    }
                                }
                            } catch (Exception ignored) {}
                        }
                    } catch (Exception ignored) {}
                }
                reader.close();
                backoff = 1;
            } catch (Exception e) {
                Log.i(TAG, "ntfy: subscribe error: " + e);
                try { Thread.sleep(backoff * 1000L); } catch (InterruptedException ie) { break; }
                backoff = Math.min(backoff * 2, 60);
            } finally {
                if (conn != null) try { conn.disconnect(); } catch (Exception ignored) {}
            }
        }
    }

    @Override public void onDestroy() {
        running = false;
        try { if (serverSocket != null) serverSocket.close(); } catch (Exception e) {}
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent i) { return null; }
    @Override public int onStartCommand(Intent i, int f, int id) {
        if (i != null && i.getBooleanExtra("mesh_bump", false)) {
            meshBumpAndPush();
        }
        return START_STICKY;
    }
}

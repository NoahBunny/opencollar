package com.bunnytasker;

import android.app.Activity;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.content.ContentResolver;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.net.Uri;
import android.provider.MediaStore;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.FileReader;
import java.io.FileWriter;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

import org.json.JSONArray;
import org.json.JSONException;
import org.json.JSONObject;

public class MainActivity extends Activity {

    // Mesh URL — set during pairing via the Join Mesh dialog. No default is shipped.
    private static final String[] HOMELAB_URLS = {};
    private static String HOMELAB = "";
    private static final String PHONE_API = "http://127.0.0.1:8432";

    private TextView statusText, statToday, statWeek, statTotal;
    private TextView statEscapes, statPaywall, statPaid, statInterest, statStreak, statGeofence;
    private TextView pinnedMessage, payHint, subStatus, subPerks, noSubPrompt;
    private LinearLayout deadlineTaskSection;
    private TextView deadlineTaskText, deadlineTaskCountdown, deadlineTaskHint, deadlineTaskStatus;
    private Button btnDeadlineTaskClear;
    private android.widget.ImageView connectionCrown;
    private TextView pairingFingerprint, pairedFingerprint, pairingHint;
    private LinearLayout pinnedSection, messagesContainer;
    private View sectionStats, sectionSelflock, sectionMessages, sectionPairing, sectionPaired, sectionMainContent;
    private android.widget.ImageView qrCodeView;
    private EditText messageInput;
    private Button btnPay, btnSend, btnFreeUnlock, btnShowQr, btnPrepay, btnSetupImap;
    private TextView balanceAmount, balanceDetail, imapStatus, tierBadge, messagesHeader;
    private boolean messagesExpanded = true;
    private static final int PICK_IMAGE = 1001;
    private static final int TAKE_PHOTO = 1002;
    private LinearLayout paymentHistory;
    private View balanceCard;
    private View statusBar;

    private Handler handler;
    private ExecutorService executor;
    private SharedPreferences prefs;
    private Runnable poller;
    private int meshSyncCounter = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(getResources().getIdentifier("activity_main", "layout", getPackageName()));

        handler = new Handler(Looper.getMainLooper());
        executor = Executors.newSingleThreadExecutor();
        prefs = getSharedPreferences("bunnytasker", MODE_PRIVATE);

        statusText = (TextView) findViewById(fid("status_text"));
        statusBar = findViewById(fid("status_bar"));
        connectionCrown = (android.widget.ImageView) findViewById(fid("connection_crown"));
        statToday = (TextView) findViewById(fid("stat_today"));
        statWeek = (TextView) findViewById(fid("stat_week"));
        statTotal = (TextView) findViewById(fid("stat_total"));
        statEscapes = (TextView) findViewById(fid("stat_escapes"));
        statPaywall = (TextView) findViewById(fid("stat_paywall"));
        statPaid = (TextView) findViewById(fid("stat_paid"));
        statInterest = (TextView) findViewById(fid("stat_interest"));
        statStreak = (TextView) findViewById(fid("stat_streak"));
        statGeofence = (TextView) findViewById(fid("stat_geofence"));
        pinnedSection = (LinearLayout) findViewById(fid("pinned_section"));
        deadlineTaskSection = (LinearLayout) findViewById(fid("deadline_task_section"));
        deadlineTaskText = (TextView) findViewById(fid("deadline_task_text"));
        deadlineTaskCountdown = (TextView) findViewById(fid("deadline_task_countdown"));
        deadlineTaskHint = (TextView) findViewById(fid("deadline_task_hint"));
        deadlineTaskStatus = (TextView) findViewById(fid("deadline_task_status"));
        btnDeadlineTaskClear = (Button) findViewById(fid("btn_deadline_task_clear"));
        btnDeadlineTaskClear.setOnClickListener(v -> doDeadlineTaskClear());
        pinnedMessage = (TextView) findViewById(fid("pinned_message"));
        messagesContainer = (LinearLayout) findViewById(fid("messages_container"));
        messageInput = (EditText) findViewById(fid("message_input"));
        btnPay = (Button) findViewById(fid("btn_pay"));
        btnSend = (Button) findViewById(fid("btn_send"));
        payHint = (TextView) findViewById(fid("pay_hint"));
        subStatus = (TextView) findViewById(fid("sub_status"));
        subPerks = (TextView) findViewById(fid("sub_perks"));
        btnFreeUnlock = (Button) findViewById(fid("btn_free_unlock"));
        sectionStats = findViewById(fid("section_stats"));
        sectionSelflock = findViewById(fid("section_selflock"));
        sectionMessages = findViewById(fid("section_messages"));
        noSubPrompt = (TextView) findViewById(fid("no_sub_prompt"));
        sectionMainContent = findViewById(fid("section_main_content"));

        // Pairing views
        sectionPairing = findViewById(fid("section_pairing"));
        sectionPaired = findViewById(fid("section_paired"));
        qrCodeView = (android.widget.ImageView) findViewById(fid("qr_code"));
        pairingFingerprint = (TextView) findViewById(fid("pairing_fingerprint"));
        pairedFingerprint = (TextView) findViewById(fid("paired_fingerprint"));
        pairingHint = (TextView) findViewById(fid("pairing_hint"));
        btnShowQr = (Button) findViewById(fid("btn_show_qr"));

        btnShowQr.setText("Join Mesh");
        btnShowQr.setOnClickListener(v -> showJoinMeshDialog());
        btnShowQr.setOnLongClickListener(v -> { showDirectPairingInfo(); return true; });

        // Show pairing state — hide everything when not paired
        if (PairingManager.isPaired(getContentResolver())) {
            sectionPairing.setVisibility(View.GONE);
            sectionPaired.setVisibility(View.VISIBLE);
            sectionMainContent.setVisibility(View.VISIBLE);
            String lionKey = PairingManager.getLionKey(getContentResolver());
            if (lionKey.length() > 16) {
                pairedFingerprint.setText(lionKey.substring(0, 8) + "..." + lionKey.substring(lionKey.length() - 8));
            }
        } else {
            sectionPairing.setVisibility(View.VISIBLE);
            sectionPaired.setVisibility(View.GONE);
            sectionMainContent.setVisibility(View.GONE);
        }

        // Subscription buttons
        findViewById(fid("btn_sub_bronze")).setOnClickListener(v -> doSubscribe("bronze", 25));
        findViewById(fid("btn_sub_silver")).setOnClickListener(v -> doSubscribe("silver", 35));
        findViewById(fid("btn_sub_gold")).setOnClickListener(v -> doSubscribe("gold", 50));
        findViewById(fid("btn_unsub")).setOnClickListener(v -> doUnsubscribe());
        btnFreeUnlock.setOnClickListener(v -> doFreeUnlock());
        btnPrepay = (Button) findViewById(fid("btn_prepay"));
        btnPrepay.setOnClickListener(v -> doPrepay());
        balanceAmount = (TextView) findViewById(fid("balance_amount"));
        balanceDetail = (TextView) findViewById(fid("balance_detail"));
        balanceCard = findViewById(fid("balance_card"));
        paymentHistory = (LinearLayout) findViewById(fid("payment_history"));
        imapStatus = (TextView) findViewById(fid("imap_status"));
        btnSetupImap = (Button) findViewById(fid("btn_setup_imap"));
        btnSetupImap.setOnClickListener(v -> doSetupImap());
        tierBadge = (TextView) findViewById(fid("tier_badge"));
        messagesHeader = (TextView) findViewById(fid("messages_header"));
        if (messagesHeader != null) {
            messagesHeader.setOnClickListener(v -> {
                messagesExpanded = !messagesExpanded;
                messagesContainer.setVisibility(messagesExpanded ? View.VISIBLE : View.GONE);
                messagesHeader.setText(messagesExpanded ? "MESSAGE YOUR LION  \u25B2" : "MESSAGE YOUR LION  \u25BC");
            });
        }

        // Self-lock buttons
        findViewById(fid("btn_selflock_15")).setOnClickListener(v -> doSelfLock(15));
        findViewById(fid("btn_selflock_30")).setOnClickListener(v -> doSelfLock(30));
        findViewById(fid("btn_selflock_60")).setOnClickListener(v -> doSelfLock(60));
        findViewById(fid("btn_selflock_120")).setOnClickListener(v -> doSelfLock(120));

        // Pay button — opens configured banking app
        btnPay.setOnClickListener(v -> {
            try {
                String bankPkg = Settings.Global.getString(getContentResolver(), "focus_lock_banking_app");
                if (bankPkg == null || bankPkg.isEmpty()) {
                    statusText.setText("No banking app configured");
                    return;
                }
                Intent launch = getPackageManager().getLaunchIntentForPackage(bankPkg.trim());
                if (launch != null) {
                    launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(launch);
                } else {
                    statusText.setText("Banking app not installed: " + bankPkg);
                }
            } catch (Exception e) {
                statusText.setText("Banking app: " + e.getMessage());
            }
        });

        // Attach photo
        findViewById(fid("btn_attach")).setOnClickListener(v -> {
            new android.app.AlertDialog.Builder(this)
                .setTitle("Send Photo")
                .setItems(new String[]{"Take Photo", "Choose from Gallery"}, (d, which) -> {
                    if (which == 0) {
                        Intent cam = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
                        startActivityForResult(cam, TAKE_PHOTO);
                    } else {
                        Intent pick = new Intent(Intent.ACTION_PICK, MediaStore.Images.Media.EXTERNAL_CONTENT_URI);
                        pick.setType("image/*");
                        startActivityForResult(pick, PICK_IMAGE);
                    }
                })
                .show();
        });

        // Send message
        btnSend.setOnClickListener(v -> {
            String msg = messageInput.getText().toString().trim();
            if (!msg.isEmpty()) {
                sendMessage(msg);
                messageInput.setText("");
            }
        });

        // Load saved messages
        loadMessages();

        // Ensure BunnyService is running for jail reinforcement
        try {
            startForegroundService(new Intent(this, BunnyService.class));
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "error", e); }

        // Start polling
        poller = () -> {
            executor.execute(() -> refreshStats());
            handler.postDelayed(poller, 5000);
        };
        handler.post(poller);
    }

    private int fid(String name) {
        return getResources().getIdentifier(name, "id", getPackageName());
    }

    private void refreshStats() {
        try {
            // Mutual admin monitoring — penalize + alert if Collar admin removed
            long breakglassUntil = Settings.Global.getLong(getContentResolver(), "focus_lock_breakglass_until", 0);
            int releaseAuth = Settings.Global.getInt(getContentResolver(), "focus_lock_release_authorized", 0);
            if (System.currentTimeMillis() > breakglassUntil && releaseAuth == 0) {
                try {
                    android.content.ComponentName collarAdmin = new android.content.ComponentName(
                        "com.focuslock", "com.focuslock.AdminReceiver");
                    android.app.admin.DevicePolicyManager dpm = (android.app.admin.DevicePolicyManager)
                        getSystemService(DEVICE_POLICY_SERVICE);
                    if (!dpm.isAdminActive(collarAdmin)) {
                        int collarTamper = Settings.Global.getInt(getContentResolver(), "focus_lock_collar_admin_removed", 0);
                        if (collarTamper == 0) {
                            android.util.Log.w("BunnyTasker", "Collar admin removed — reporting tamper_removed");
                            Settings.Global.putInt(getContentResolver(), "focus_lock_collar_admin_removed", 1);
                            // P2 paywall hardening (2026-04-17): server applies
                            // the $1000 penalty on tamper_removed and propagates
                            // lock + paywall via vault; companion just reports.
                            postEventToServer("tamper_removed", "companion-detected");
                            postToCollar("/api/lock",
                                "{\"message\":\"Collar admin removed.\",\"mode\":\"basic\",\"shame\":1,\"target\":\"phone\"}");
                        }
                    } else {
                        Settings.Global.putInt(getContentResolver(), "focus_lock_collar_admin_removed", 0);
                    }
                } catch (Exception e) {}
            }

            // Mesh sync every 6th poll (~30s) — pull orders from server
            if (++meshSyncCounter >= 6) {
                meshSyncCounter = 0;
                meshSync();
            }
            int active = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
            int escapes = Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0);
            String paywall = gstr("focus_lock_paywall");
            String paywallOrig = gstr("focus_lock_paywall_original");
            long lockedAt = Settings.Global.getLong(getContentResolver(), "focus_lock_locked_at", 0);
            long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
            String pinned = gstr("focus_lock_pinned_message");
            String mode = gstr("focus_lock_mode");
            String geofenceLat = gstr("focus_lock_geofence_lat");
            String geofenceLon = gstr("focus_lock_geofence_lon");

            // Subscription state
            String subTier = gstr("focus_lock_sub_tier");
            long subDue = Settings.Global.getLong(getContentResolver(), "focus_lock_sub_due", 0);
            int freeUnlocks = Settings.Global.getInt(getContentResolver(), "focus_lock_free_unlocks", 0);

            // Subscription overdue enforcement: warnings at 1hr and 24hr, then auto-lock
            if (!subTier.isEmpty() && subDue > 0 && System.currentTimeMillis() > subDue) {
                long overdueMs = System.currentTimeMillis() - subDue;
                long overdueHours = overdueMs / 3600000;
                boolean warned1h = prefs.getBoolean("warned_1h_" + subTier, false);
                boolean warned24h = prefs.getBoolean("warned_24h_" + subTier, false);
                boolean locked = prefs.getBoolean("locked_" + subTier, false);

                if (overdueHours >= 1 && !warned1h) {
                    prefs.edit().putBoolean("warned_1h_" + subTier, true).apply();
                    Settings.Global.putString(getContentResolver(), "focus_lock_pinned_message",
                        "Subscription payment overdue! Pay now or face consequences.");
                }
                if (overdueHours >= 24 && !warned24h) {
                    prefs.edit().putBoolean("warned_24h_" + subTier, true).apply();
                    Settings.Global.putString(getContentResolver(), "focus_lock_pinned_message",
                        "FINAL WARNING: Subscription " + overdueHours + "h overdue. Phone will be locked.");
                }
                if (overdueHours >= 48 && !locked && active == 0) {
                    prefs.edit().putBoolean("locked_" + subTier, true).apply();
                    boolean ok = postToCollar("/api/lock",
                        "{\"message\":\"Subscription overdue. Pay your " + subTier + " tribute.\""
                        + ",\"mode\":\"basic\",\"shame\":1,\"target\":\"phone\"}");
                    if (ok) active = 1;
                    sendWebhook("/webhook/bunny-message",
                        "{\"text\":\"Auto-locked for overdue " + subTier + " subscription\",\"type\":\"overdue-lock\"}");
                }
            }

            // Outstanding balance notification
            if (!paywall.isEmpty() && !paywall.equals("0")) {
                try {
                    int pwAmount = Integer.parseInt(paywall);
                    if (pwAmount > 0) {
                        showBalanceNotification(pwAmount);
                    }
                } catch (Exception e) { android.util.Log.e("BunnyTasker", "error", e); }
            } else {
                // Clear balance notification if paid off
                try {
                    ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).cancel(301);
                } catch (Exception e) { android.util.Log.e("BunnyTasker", "error", e); }
            }

            // Calculate compound interest accrued
            double interest = 0;
            if (lockedAt > 0 && !paywallOrig.isEmpty() && !paywallOrig.equals("0") && !paywall.isEmpty()) {
                try {
                    double orig = Double.parseDouble(paywallOrig);
                    double current = Double.parseDouble(paywall);
                    interest = current - orig;
                } catch (Exception e) { android.util.Log.e("BunnyTasker", "error", e); }
            }

            // Calculate time locked in current session
            double hoursLocked = 0;
            if (active == 1 && lockedAt > 0) {
                hoursLocked = (System.currentTimeMillis() - lockedAt) / 3600000.0;
            }

            // Accumulate time stats from prefs
            long totalLockedMs = prefs.getLong("total_locked_ms", 0);
            long todayLockedMs = prefs.getLong("today_locked_ms", 0);
            long weekLockedMs = prefs.getLong("week_locked_ms", 0);
            long totalPaid = Settings.Global.getLong(getContentResolver(), "focus_lock_total_paid_cents", prefs.getLong("total_paid_cents", 0));
            int streakDays = prefs.getInt("streak_days", 0);
            long lastTrackTime = prefs.getLong("last_track_time", 0);

            // Track current session
            if (active == 1 && lastTrackTime > 0) {
                long delta = System.currentTimeMillis() - lastTrackTime;
                if (delta > 0 && delta < 30000) { // only count if polling was recent
                    totalLockedMs += delta;
                    todayLockedMs += delta;
                    weekLockedMs += delta;
                }
            }
            prefs.edit()
                .putLong("total_locked_ms", totalLockedMs)
                .putLong("today_locked_ms", todayLockedMs)
                .putLong("week_locked_ms", weekLockedMs)
                .putLong("last_track_time", System.currentTimeMillis())
                .apply();

            final boolean isLocked = active == 1;
            final int fEscapes = escapes;
            final String fPaywall = paywall;
            final double fInterest = interest;
            final double fHoursLocked = hoursLocked;
            final String fPinned = pinned;
            final long fTotalMs = totalLockedMs;
            final long fTodayMs = todayLockedMs;
            final long fWeekMs = weekLockedMs;
            final long fTotalPaid = totalPaid;
            final int fStreak = streakDays;
            final boolean hasGeofence = !geofenceLat.isEmpty();
            final String fMode = mode;
            final long fUnlockAt = unlockAt;
            final String fSubTier = subTier;
            final long fSubDue = subDue;
            final int fFreeUnlocks = freeUnlocks;
            final boolean isEntrapped = Settings.Global.getInt(getContentResolver(), "focus_lock_entrapped", 0) == 1;

            handler.post(() -> {
                // Entrap visual — reddish striped background
                View root = findViewById(android.R.id.content);
                if (isEntrapped && root != null) {
                    root.setBackgroundColor(0xFF1a0808);
                } else if (root != null) {
                    root.setBackgroundColor(0xFF0a0812);
                }

                // Status
                if (isEntrapped) {
                    statusText.setText("ENTRAPPED — Only your Lion can free you");
                    statusBar.setBackgroundColor(0xFF881111);
                } else if (isLocked) {
                    StringBuilder sb = new StringBuilder("LOCKED");
                    if (!fMode.isEmpty()) sb.append(" (").append(fMode).append(")");
                    if (fUnlockAt > 0) {
                        long rem = fUnlockAt - System.currentTimeMillis();
                        if (rem > 0) {
                            sb.append(" | ").append(rem / 60000).append("m left");
                        }
                    }
                    if (fEscapes > 0) sb.append(" | ").append(fEscapes).append(" esc");
                    statusText.setText(sb.toString());
                    statusBar.setBackgroundColor(0xFFcc2222);
                } else {
                    statusText.setText("Unlocked");
                    statusBar.setBackgroundColor(0xFF9977bb);
                }
                connectionCrown.setAlpha(1.0f);

                // Stats
                statToday.setText(formatHours(fTodayMs));
                statWeek.setText(formatHours(fWeekMs));
                statTotal.setText(formatHours(fTotalMs));
                statEscapes.setText(String.valueOf(fEscapes));
                statPaywall.setText(fPaywall.isEmpty() || fPaywall.equals("0") ? "$0" : "$" + fPaywall);
                statPaywall.setTextColor(fPaywall.isEmpty() || fPaywall.equals("0") ? 0xFF555555 : 0xFFcc9900);
                statPaid.setText("$" + (fTotalPaid / 100));
                statPaid.setTextColor(0xFFaa88cc);
                statInterest.setText(fInterest > 0 ? String.format("+$%.0f", fInterest) : "+$0");
                statStreak.setText(fStreak + "d");
                statGeofence.setText(hasGeofence ? "active" : "off");
                statGeofence.setTextColor(hasGeofence ? 0xFFaa88cc : 0xFF555555);

                // Balance card
                TextView balanceHeader = (TextView) findViewById(fid("balance_header"));
                if (!fPaywall.isEmpty() && !fPaywall.equals("0")) {
                    if (balanceHeader != null) balanceHeader.setTextColor(0xFFc8a84e);
                    btnPay.setTextColor(0xFFcc9900);
                    btnPay.getBackground().setTint(0xFF2a1a0a);
                    balanceAmount.setText("$" + fPaywall);
                    balanceAmount.setTextColor(0xFFcc4444);
                    String detail = "Pay via e-Transfer to clear";
                    if (fInterest > 0) detail += " | +" + String.format("$%.0f", fInterest) + " interest";
                    balanceDetail.setText(detail);
                    balanceDetail.setTextColor(0xFF885533);
                    balanceCard.setBackgroundColor(0xFF1a0808);
                    btnPay.setText("Pay Balance ($" + fPaywall + ")");
                    btnPay.setVisibility(View.VISIBLE);
                    payHint.setVisibility(View.VISIBLE);
                } else {
                    balanceAmount.setText("$0");
                    balanceAmount.setTextColor(0xFF9977bb);
                    balanceDetail.setText("No outstanding balance");
                    balanceDetail.setTextColor(0xFF5a4a6a);
                    if (balanceHeader != null) balanceHeader.setTextColor(0xFF5a4a6a);
                    balanceCard.setBackgroundColor(0xFF0e0c16);
                    btnPay.setText("No Balance Due");
                    btnPay.setTextColor(0xFFaa88cc);
                    btnPay.getBackground().setTint(0xFF2a1a3a);
                    btnPay.setVisibility(View.VISIBLE);
                    payHint.setVisibility(View.GONE);
                }

                // Tier badge — prominent, encouraging
                if (!fSubTier.isEmpty()) {
                    String tierEmoji = "bronze".equals(fSubTier) ? "\uD83E\uDD49" :
                                       "silver".equals(fSubTier) ? "\uD83E\uDD48" : "\uD83E\uDD47";
                    int tierColor = "bronze".equals(fSubTier) ? 0xFFcc8844 :
                                    "silver".equals(fSubTier) ? 0xFFaaaacc : 0xFFccaa44;
                    int tierBg = "bronze".equals(fSubTier) ? 0xFF1a1008 :
                                 "silver".equals(fSubTier) ? 0xFF141418 : 0xFF1a1a08;
                    tierBadge.setText(tierEmoji + "  " + fSubTier.toUpperCase() + " SUBSCRIBER  " + tierEmoji);
                    tierBadge.setTextColor(tierColor);
                    tierBadge.setBackgroundColor(tierBg);
                    tierBadge.setVisibility(View.VISIBLE);
                } else {
                    tierBadge.setVisibility(View.GONE);
                }

                // Subscription display
                if (!fSubTier.isEmpty()) {
                    int amt = "bronze".equals(fSubTier) ? 25 : "silver".equals(fSubTier) ? 35 : 50;
                    long daysLeft = fSubDue > 0 ? Math.max(0, (fSubDue - System.currentTimeMillis()) / 86400000) : 0;
                    String dueStr = daysLeft > 0 ? daysLeft + "d until due" : "OVERDUE";
                    subStatus.setText(fSubTier.toUpperCase() + " — $" + amt + "/week | " + dueStr);
                    subStatus.setTextColor(daysLeft > 0 ? 0xFF888888 : 0xFFcc4444);
                    // Show free unlock button for Gold subscribers
                    if ("gold".equals(fSubTier) && isLocked && fFreeUnlocks < 1) {
                        btnFreeUnlock.setVisibility(View.VISIBLE);
                    } else {
                        btnFreeUnlock.setVisibility(View.GONE);
                    }
                    // Show pre-pay button only when within 6 days of due
                    if (daysLeft <= 6) {
                        btnPrepay.setText("Pay Early ($" + amt + ")");
                        btnPrepay.setVisibility(View.VISIBLE);
                    } else {
                        btnPrepay.setVisibility(View.GONE);
                    }
                } else {
                    subStatus.setText("No active subscription");
                    subStatus.setTextColor(0xFF555555);
                    btnFreeUnlock.setVisibility(View.GONE);
                    btnPrepay.setVisibility(View.GONE);
                }

                // Gate features by subscription tier
                boolean hasSub = !fSubTier.isEmpty();
                sectionStats.setVisibility(hasSub ? View.VISIBLE : View.GONE);
                sectionSelflock.setVisibility(hasSub ? View.VISIBLE : View.GONE);
                sectionMessages.setVisibility(hasSub ? View.VISIBLE : View.GONE);
                noSubPrompt.setVisibility(hasSub ? View.GONE : View.VISIBLE);

                // IMAP status
                String savedEmail = prefs.getString("imap_email", "");
                if (!savedEmail.isEmpty()) {
                    imapStatus.setText("Connected: " + savedEmail);
                    imapStatus.setTextColor(0xFF44aa44);
                    btnSetupImap.setText("Update Email");
                }

                // Payment history + messages (refresh every 5th poll)
                if (System.currentTimeMillis() % 25000 < 5000) {
                    refreshPaymentHistory();
                    refreshMeshMessages();
                }

                // Pairing state — gate all content behind pairing
                if (PairingManager.isPaired(getContentResolver())) {
                    sectionPairing.setVisibility(View.GONE);
                    sectionPaired.setVisibility(View.VISIBLE);
                    sectionMainContent.setVisibility(View.VISIBLE);
                    String lk = PairingManager.getLionKey(getContentResolver());
                    if (lk.length() > 16) pairedFingerprint.setText(lk.substring(0, 8) + "..." + lk.substring(lk.length() - 8));
                } else {
                    sectionPairing.setVisibility(View.VISIBLE);
                    sectionPaired.setVisibility(View.GONE);
                    sectionMainContent.setVisibility(View.GONE);
                }

                // Pinned message
                if (!fPinned.isEmpty()) {
                    pinnedSection.setVisibility(View.VISIBLE);
                    pinnedMessage.setText(fPinned);
                    showPinnedNotification(fPinned);
                } else {
                    pinnedSection.setVisibility(View.GONE);
                }

                refreshDeadlineTask();
            });

        } catch (Exception e) {
            handler.post(() -> {
                connectionCrown.setAlpha(0.2f);
            });
        }
    }

    /**
     * Pull mesh orders from server and apply to Settings.Global.
     * Belt-and-suspenders: even if ControlService gossip fails, BunnyTasker keeps orders fresh.
     */
    private void meshSync() {
        String meshUrl = gstr("focus_lock_mesh_url");
        String pin = gstr("focus_lock_pin");
        if (meshUrl.isEmpty() || pin.isEmpty()) return;
        // SECURITY: reject non-HTTPS mesh relay URLs to prevent credential/order interception
        if (!meshUrl.startsWith("https://") && !meshUrl.startsWith("http://192.168.")
                && !meshUrl.startsWith("http://10.") && !meshUrl.startsWith("http://127.")
                && !meshUrl.startsWith("http://100.")) {
            android.util.Log.w("BunnyTasker", "Mesh sync refused: non-HTTPS relay URL");
            return;
        }
        // Multi-tenant mesh: when joined via /api/mesh/join, the server requires the
        // account-based path /api/mesh/{mesh_id}/sync. Legacy /mesh/sync only works for
        // single-tenant deployments.
        String meshId = gstr("focus_lock_mesh_id");
        String syncPath = meshId.isEmpty() ? "/mesh/sync" : ("/api/mesh/" + meshId + "/sync");

        try {
            long localVersion = Settings.Global.getLong(getContentResolver(), "focus_lock_mesh_version", 0);
            String nodeId = android.os.Build.MODEL.toLowerCase().replace(" ", "-");

            String payload = "{\"pin\":\"" + pin
                + "\",\"node_id\":\"" + nodeId
                + "\",\"type\":\"phone\",\"orders_version\":" + localVersion + ",\"status\":{}}";

            java.net.URL url = new java.net.URL(meshUrl + syncPath);
            java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
            try {
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(8000);
                conn.setReadTimeout(8000);
                conn.getOutputStream().write(payload.getBytes());

                if (conn.getResponseCode() == 200) {
                    try (java.io.BufferedReader br = new java.io.BufferedReader(
                            new java.io.InputStreamReader(conn.getInputStream()))) {
                        StringBuilder sb = new StringBuilder();
                        String line;
                        while ((line = br.readLine()) != null) sb.append(line);
                        String body = sb.toString();

                        // Check if remote version is higher
                        String remVerStr = jsonVal(body, "orders_version");
                        long remVer = 0;
                        try { remVer = Long.parseLong(remVerStr); } catch (Exception e) {}

                        if (remVer > localVersion) {
                            // Extract orders object and apply each key to Settings.Global
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
                                    applyMeshOrders(ordersJson);
                                    Settings.Global.putLong(getContentResolver(),
                                        "focus_lock_mesh_version", remVer);
                                    android.util.Log.i("BunnyTasker", "Mesh sync: applied v" + remVer);
                                }
                            }
                        }
                    }
                }
            } finally {
                conn.disconnect();
            }
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "Mesh sync failed: " + e.getMessage());
        }
    }

    // SECURITY: BunnyTasker only applies DISPLAY keys from mesh sync.
    // Enforcement keys (lock_active, paywall, penalties, etc.) are owned by the Collar.
    // This prevents mesh injection attacks from locking the phone or clearing the paywall
    // via BunnyTasker's unauthenticated legacy sync path.
    private static final java.util.Set<String> MESH_DISPLAY_KEYS = new java.util.HashSet<>(
        java.util.Arrays.asList(
            "sub_tier", "sub_due", "sub_total_owed", "free_unlocks",
            "pinned_message", "lion_pinned_message", "message",
            "mode", "offer", "offer_status",
            "streak_enabled", "streak_start", "streak_escapes_at_start",
            "tribute_active", "tribute_amount",
            "checkin_deadline",
            "curfew_enabled", "bedtime_enabled",
            "body_check_active", "body_check_area", "body_check_interval_h",
            "screen_time_quota_minutes"
        ));

    /** Apply mesh orders JSON to Settings.Global — DISPLAY KEYS ONLY. */
    private void applyMeshOrders(String ordersJson) {
        try {
            JSONObject orders = new JSONObject(ordersJson);
            android.content.ContentResolver cr = getContentResolver();
            java.util.Iterator<String> keys = orders.keys();
            while (keys.hasNext()) {
                String key = keys.next();
                if (!MESH_DISPLAY_KEYS.contains(key)) continue;
                String adbKey = "focus_lock_" + key;
                Object val = orders.get(key);
                if (val instanceof Integer || val instanceof Long) {
                    Settings.Global.putLong(cr, adbKey, ((Number) val).longValue());
                } else {
                    Settings.Global.putString(cr, adbKey, String.valueOf(val));
                }
            }
        } catch (Exception e) {
            android.util.Log.e("BunnyTasker", "Apply mesh orders failed", e);
        }
    }

    /** Simple JSON value extractor for top-level string/number fields. */
    private static String jsonVal(String json, String key) {
        String search = "\"" + key + "\"";
        int idx = json.indexOf(search);
        if (idx < 0) return "";
        int colon = json.indexOf(":", idx + search.length());
        if (colon < 0) return "";
        int start = colon + 1;
        while (start < json.length() && json.charAt(start) == ' ') start++;
        if (start >= json.length()) return "";
        if (json.charAt(start) == '"') {
            int end = json.indexOf("\"", start + 1);
            return end > start ? json.substring(start + 1, end) : "";
        }
        int end = start;
        while (end < json.length() && json.charAt(end) != ',' && json.charAt(end) != '}') end++;
        return json.substring(start, end).trim();
    }

    private String formatHours(long ms) {
        double hours = ms / 3600000.0;
        if (hours < 1) return String.format("%.0fm", ms / 60000.0);
        return String.format("%.1fh", hours);
    }

    /**
     * Serverless pairing info: show the bunny's IP/port + key fingerprint so the Lion can
     * pair directly via Lion's Share's "Pair Direct (LAN)" option. No mesh server needed.
     */
    private void showDirectPairingInfo() {
        // Generate keypair if missing
        String pubKey = PairingManager.getPublicKey(getContentResolver());
        String fingerprint = PairingManager.getFingerprint(getContentResolver());

        // Get LAN IP
        String lanIp = "";
        try {
            android.net.wifi.WifiManager wm = (android.net.wifi.WifiManager) getSystemService(WIFI_SERVICE);
            android.net.wifi.WifiInfo wi = wm != null ? wm.getConnectionInfo() : null;
            int ip = wi != null ? wi.getIpAddress() : 0;
            if (ip != 0) {
                lanIp = (ip & 0xff) + "." + ((ip >> 8) & 0xff) + "."
                    + ((ip >> 16) & 0xff) + "." + ((ip >> 24) & 0xff);
            }
        } catch (Exception e) {}

        // Get Tailscale IP if present
        String tsIp = "";
        try {
            java.util.Enumeration<java.net.NetworkInterface> nets = java.net.NetworkInterface.getNetworkInterfaces();
            while (nets.hasMoreElements()) {
                java.net.NetworkInterface ni = nets.nextElement();
                if (ni.getName().startsWith("tun")) {
                    java.util.Enumeration<java.net.InetAddress> addrs = ni.getInetAddresses();
                    while (addrs.hasMoreElements()) {
                        java.net.InetAddress a = addrs.nextElement();
                        if (a instanceof java.net.Inet4Address) tsIp = a.getHostAddress();
                    }
                }
            }
        } catch (Exception e) {}

        StringBuilder msg = new StringBuilder();
        msg.append("Direct (serverless) pairing — give your Lion these details:\n\n");
        if (!lanIp.isEmpty()) {
            msg.append("LAN IP:\n  ").append(lanIp).append(":8432\n\n");
        }
        if (!tsIp.isEmpty()) {
            msg.append("Tailscale IP:\n  ").append(tsIp).append(":8432\n\n");
        }
        if (lanIp.isEmpty() && tsIp.isEmpty()) {
            msg.append("(no network detected — check WiFi)\n\n");
        }
        msg.append("Fingerprint:\n  ").append(fingerprint).append("\n\n");
        msg.append("In Lion's Share: tap Setup > Pair Direct (LAN) and enter the IP above.\n\n");
        msg.append("Both devices must be on the same network (LAN, Tailscale, or VPN).");

        new android.app.AlertDialog.Builder(this)
            .setTitle("Direct Pair Info")
            .setMessage(msg.toString())
            .setPositiveButton("OK", null)
            .show();
    }

    private void showJoinMeshDialog() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 24, 48, 0);

        EditText inviteInput = new EditText(this);
        inviteInput.setHint("Invite code (e.g. WOLF-42-BEAR)");
        inviteInput.setTextSize(18);
        inviteInput.setTextColor(0xFFe0e0e0);
        inviteInput.setHintTextColor(0xFF555555);
        inviteInput.setBackgroundColor(0xFF111118);
        inviteInput.setPadding(24, 20, 24, 20);
        inviteInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT
            | android.text.InputType.TYPE_TEXT_FLAG_CAP_CHARACTERS);
        layout.addView(inviteInput);

        EditText serverInput = new EditText(this);
        serverInput.setHint("Server URL (e.g. https://your-relay.example)");
        serverInput.setTextSize(13);
        serverInput.setTextColor(0xFFaaaaaa);
        serverInput.setHintTextColor(0xFF444444);
        serverInput.setBackgroundColor(0xFF0e0e14);
        serverInput.setPadding(24, 16, 24, 16);
        LinearLayout.LayoutParams slp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        slp.topMargin = 12;
        serverInput.setLayoutParams(slp);
        layout.addView(serverInput);

        // Vault Mode toggle (Phase C — slave reads /vault/{id}/since instead of plaintext gossip)
        // Reads/writes focus_lock_vault_mode in Settings.Global so ControlService.vaultSync() picks it up.
        android.widget.CheckBox vaultCheck = new android.widget.CheckBox(this);
        vaultCheck.setText("Encrypted orders (recommended)");
        vaultCheck.setTextColor(0xFFcccccc);
        vaultCheck.setTextSize(13);
        try {
            int cur = Settings.Global.getInt(getContentResolver(), "focus_lock_vault_mode", 1);
            vaultCheck.setChecked(cur == 1);
        } catch (Exception e) { vaultCheck.setChecked(false); }
        LinearLayout.LayoutParams vlp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        vlp.topMargin = 16;
        vaultCheck.setLayoutParams(vlp);
        layout.addView(vaultCheck);

        // "Paste QR" button — reads clipboard JSON from web signup QR code
        // and auto-fills invite code + server URL fields.
        android.widget.Button pasteBtn = new android.widget.Button(this);
        pasteBtn.setText("Paste QR Code");
        pasteBtn.setTextSize(13);
        pasteBtn.setBackgroundColor(0xFF1a1a2a);
        pasteBtn.setTextColor(0xFFDAA520);
        LinearLayout.LayoutParams plp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        plp.topMargin = 12;
        pasteBtn.setLayoutParams(plp);
        pasteBtn.setOnClickListener(v2 -> {
            try {
                android.content.ClipboardManager cm = (android.content.ClipboardManager)
                    getSystemService(android.content.Context.CLIPBOARD_SERVICE);
                if (cm != null && cm.hasPrimaryClip() && cm.getPrimaryClip().getItemCount() > 0) {
                    String clip = cm.getPrimaryClip().getItemAt(0).getText().toString();
                    org.json.JSONObject qr = new org.json.JSONObject(clip);
                    if (qr.has("relay")) serverInput.setText(qr.getString("relay"));
                    if (qr.has("invite")) inviteInput.setText(qr.getString("invite"));
                }
            } catch (Exception ex) { /* not QR JSON, ignore */ }
        });
        layout.addView(pasteBtn);

        final android.widget.CheckBox vaultCheckFinal = vaultCheck;

        new android.app.AlertDialog.Builder(this)
            .setTitle("Join Mesh")
            .setMessage("Enter the invite code from your Lion, or paste the QR code text.")
            .setView(layout)
            .setPositiveButton("Join", (d, w) -> {
                String code = inviteInput.getText().toString().trim().toUpperCase();
                String server = serverInput.getText().toString().trim();
                if (code.isEmpty()) {
                    statusText.setText("Enter an invite code");
                    return;
                }
                if (server.isEmpty()) {
                    statusText.setText("Enter the server URL");
                    return;
                }
                // Persist vault toggle before kicking off the join
                writeVaultModeFlag(vaultCheckFinal.isChecked());
                statusText.setText("Joining mesh...");
                pairingHint.setText("Connecting to server...");
                final String fServer = server;
                executor.execute(() -> joinMesh(code, fServer));
            })
            .setNeutralButton("Save", (d, w) -> {
                // Persist vault toggle without re-joining the mesh
                writeVaultModeFlag(vaultCheckFinal.isChecked());
                statusText.setText("Saved" + (vaultCheckFinal.isChecked() ? " (vault on)" : ""));
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Write the vault mode flag the Collar's ControlService.vaultSync() reads. */
    private void writeVaultModeFlag(boolean enabled) {
        try {
            Settings.Global.putInt(getContentResolver(), "focus_lock_vault_mode", enabled ? 1 : 0);
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "vault flag write failed: " + e.getMessage());
        }
    }

    private void joinMesh(String inviteCode, String serverUrl) {
        try {
            // Ensure we have a keypair
            String bunnyPubKey = PairingManager.getPublicKey(getContentResolver());

            // Get device node_id
            String nodeId = android.os.Build.MODEL.toLowerCase().replace(" ", "-");

            // Build join request
            JSONObject body = new JSONObject();
            body.put("invite_code", inviteCode);
            body.put("node_id", nodeId);
            body.put("node_type", "phone");
            body.put("bunny_pubkey", bunnyPubKey != null ? bunnyPubKey : "");

            // POST /api/mesh/join
            URL url = new URL(serverUrl + "/api/mesh/join");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes());

            int code = conn.getResponseCode();
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                code < 400 ? conn.getInputStream() : conn.getErrorStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) sb.append(line);
            reader.close();
            conn.disconnect();

            if (code >= 400) {
                final String err = sb.toString();
                handler.post(() -> {
                    statusText.setText("Join failed");
                    pairingHint.setText("Error: " + err);
                });
                return;
            }

            JSONObject resp = new JSONObject(sb.toString());
            String meshId = resp.optString("mesh_id", "");
            String lionPubKey = resp.optString("lion_pubkey", "");
            String pin = resp.optString("pin", "");

            if (meshId.isEmpty()) {
                handler.post(() -> {
                    statusText.setText("Join failed — no mesh_id");
                    pairingHint.setText("Server returned invalid response");
                });
                return;
            }

            // Store mesh config in Settings.Global (survives app data clears)
            ContentResolver cr = getContentResolver();
            Settings.Global.putString(cr, "focus_lock_mesh_id", meshId);
            Settings.Global.putString(cr, "focus_lock_mesh_url", serverUrl);
            Settings.Global.putString(cr, "focus_lock_pin", pin);
            // Store node_id so ControlService identifies this device in mesh gossip
            String deviceNodeId = android.os.Build.MODEL.toLowerCase().replace(" ", "-");
            Settings.Global.putString(cr, "focus_lock_mesh_node_id", deviceNodeId);

            // Store Lion's public key — this makes PairingManager.isPaired() return true
            if (!lionPubKey.isEmpty()) {
                PairingManager.storeLionKey(cr, lionPubKey);
            }

            // Also update HOMELAB for backward compat
            HOMELAB = serverUrl;

            handler.post(() -> {
                // Transition UI to paired state
                sectionPairing.setVisibility(View.GONE);
                sectionPaired.setVisibility(View.VISIBLE);
                sectionMainContent.setVisibility(View.VISIBLE);
                if (lionPubKey.length() > 16) {
                    pairedFingerprint.setText(lionPubKey.substring(0, 8) + "..." +
                        lionPubKey.substring(lionPubKey.length() - 8));
                }
                statusText.setText("Joined mesh!");
                if (statusBar != null) statusBar.setBackgroundColor(0xFFc8a84e);
                pairingHint.setText("Connected to " + serverUrl);
            });
        } catch (Exception e) {
            final String msg = e.getMessage();
            handler.post(() -> {
                statusText.setText("Join failed");
                pairingHint.setText("Error: " + msg);
            });
        }
    }

    private void doSubscribe(String tier, int amount) {
        new android.app.AlertDialog.Builder(this)
            .setTitle("Subscribe: " + tier.substring(0, 1).toUpperCase() + tier.substring(1))
            .setMessage("$" + amount + "/week recurring tribute.\n\n" +
                (tier.equals("bronze") ? "Perks: Stats + messaging" :
                 tier.equals("silver") ? "Perks: Reduced compound interest (5% instead of 10%)" :
                 "Perks: No compound interest + 1 free unlock/month") +
                "\n\nCancel fee: $" + (amount * 2) + "\n\nFirst charge in 7 days. Overdue = warnings then auto-lock.")
            .setPositiveButton("SUBSCRIBE", (d, w) -> {
                executor.execute(() -> postSubscribeToMesh(tier, amount));
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Sign a subscribe intent with the bunny's registered key and POST to the
     *  server. Server verifies the signature, fires the mesh subscribe action,
     *  and the resulting vault blob propagates sub_tier + sub_due to every
     *  device in the mesh. Replaces the pre-2026-04-15 local-only write
     *  (landmine #20) — previously a subscription chosen here lived only on
     *  this phone and was lost on device swap. */
    private void postSubscribeToMesh(String tier, int amount) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) {
            handler.post(() -> subStatus.setText("Not paired to a mesh yet"));
            return;
        }
        long ts = System.currentTimeMillis();
        String payload = meshId + "|" + nodeId + "|" + tier + "|" + ts;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) {
            handler.post(() -> subStatus.setText("Subscribe failed (no key)"));
            return;
        }
        JSONObject body = new JSONObject();
        try {
            body.put("node_id", nodeId);
            body.put("tier", tier);
            body.put("ts", ts);
            body.put("signature", signature);
        } catch (JSONException e) {
            handler.post(() -> subStatus.setText("Subscribe failed (json)"));
            return;
        }
        try {
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/subscribe");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                code < 400 ? conn.getInputStream() : conn.getErrorStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) sb.append(line);
            reader.close();
            conn.disconnect();
            if (code >= 400) {
                final String err = sb.toString();
                handler.post(() -> subStatus.setText("Subscribe failed: " + err));
                return;
            }
            sendWebhook("/webhook/bunny-message",
                "{\"text\":\"Subscribed to " + tier + " ($" + amount + "/wk)\",\"type\":\"subscription\"}");
            // Optimistic UI update; authoritative sub_tier/sub_due arrive via
            // the next vault blob (usually within ~30s).
            handler.post(() -> subStatus.setText(tier.toUpperCase() + " — $" + amount + "/week (syncing...)"));
        } catch (Exception e) {
            final String msg = e.getMessage();
            handler.post(() -> subStatus.setText("Subscribe failed: " + msg));
        }
    }

    // ────────────── Deadline Task ──────────────
    // Server-authoritative do-or-lock task (commit b9394fe). Lion arms it from
    // Lion's Share; Collar projects the deadline_task_* keys into Settings.Global
    // via vault. Bunny Tasker renders countdown + Clear button; clear flow
    // signs with PairingManager.sign and POSTs /api/mesh/{id}/deadline-task/clear.

    /** UI-thread only. Reads the projected deadline_task_* keys from
     *  Settings.Global and updates the section visibility / labels / button
     *  state. Called from refreshStats' handler.post block. */
    private void refreshDeadlineTask() {
        if (deadlineTaskSection == null) return;
        String text = gstr("focus_lock_deadline_task_text");
        long deadlineMs = Settings.Global.getLong(getContentResolver(),
            "focus_lock_deadline_task_deadline_ms", 0L);
        int lockedByMiss = Settings.Global.getInt(getContentResolver(),
            "focus_lock_deadline_task_locked_by_miss", 0);
        if (text.isEmpty() && deadlineMs == 0L && lockedByMiss == 0) {
            deadlineTaskSection.setVisibility(View.GONE);
            return;
        }
        deadlineTaskSection.setVisibility(View.VISIBLE);
        deadlineTaskText.setText(text.isEmpty() ? "(no task text)" : text);
        long now = System.currentTimeMillis();
        if (lockedByMiss == 1) {
            deadlineTaskCountdown.setText("MISSED — complete to unlock");
            deadlineTaskCountdown.setTextColor(0xFFff6688);
        } else if (deadlineMs > now) {
            long remaining = deadlineMs - now;
            long h = remaining / 3600000L;
            long m = (remaining % 3600000L) / 60000L;
            deadlineTaskCountdown.setText("Deadline in " + (h > 0 ? h + "h " : "") + m + "m");
            deadlineTaskCountdown.setTextColor(0xFFcc88cc);
        } else if (deadlineMs > 0) {
            deadlineTaskCountdown.setText("Deadline passed — miss will fire soon");
            deadlineTaskCountdown.setTextColor(0xFFff8844);
        } else {
            deadlineTaskCountdown.setText("");
        }
        String proofType = gstr("focus_lock_deadline_task_proof_type");
        String hint = gstr("focus_lock_deadline_task_proof_hint");
        if (!hint.isEmpty() || (!proofType.isEmpty() && !"none".equals(proofType))) {
            StringBuilder h = new StringBuilder();
            if (!"none".equals(proofType) && !proofType.isEmpty()) {
                h.append("Proof: ").append(proofType);
                if (!hint.isEmpty()) h.append(" — ");
            }
            h.append(hint);
            deadlineTaskHint.setText(h.toString());
            deadlineTaskHint.setVisibility(View.VISIBLE);
        } else {
            deadlineTaskHint.setVisibility(View.GONE);
        }
    }

    /** Entry point for the Clear Now button. Branches on proof_type. */
    private void doDeadlineTaskClear() {
        String proofType = gstr("focus_lock_deadline_task_proof_type");
        if (proofType == null || proofType.isEmpty()) proofType = "none";
        switch (proofType) {
            case "typed":
                promptTypedProof();
                break;
            case "photo":
                promptPhotoProof();
                break;
            default:
                confirmAndPostClear(null);
        }
    }

    /** Final step — POST to the server's bunny-authed clear endpoint. Shared
     *  by all three proof paths; the proof check happens client-side, the
     *  signed clear is what the server trusts (see commit b9394fe). */
    private void postDeadlineClear() {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) {
            handler.post(() -> setDeadlineTaskStatus("Not paired to a mesh yet"));
            return;
        }
        long ts = System.currentTimeMillis();
        String payload = meshId + "|" + nodeId + "|deadline-task-clear|" + ts;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) {
            handler.post(() -> setDeadlineTaskStatus("Clear failed (no key)"));
            return;
        }
        JSONObject body = new JSONObject();
        try {
            body.put("node_id", nodeId);
            body.put("ts", ts);
            body.put("signature", signature);
        } catch (JSONException e) {
            handler.post(() -> setDeadlineTaskStatus("Clear failed (json)"));
            return;
        }
        try {
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/deadline-task/clear");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                code < 400 ? conn.getInputStream() : conn.getErrorStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) sb.append(line);
            reader.close();
            conn.disconnect();
            if (code >= 400) {
                final String err = sb.toString();
                handler.post(() -> setDeadlineTaskStatus("Clear failed: " + err));
                return;
            }
            handler.post(() -> setDeadlineTaskStatus("Task cleared — syncing..."));
        } catch (Exception e) {
            final String msg = e.getMessage();
            handler.post(() -> setDeadlineTaskStatus("Clear failed: " + msg));
        }
    }

    private void setDeadlineTaskStatus(String s) {
        if (deadlineTaskStatus == null) return;
        deadlineTaskStatus.setText(s);
        deadlineTaskStatus.setVisibility(View.VISIBLE);
    }

    /** Confirm dialog + fire clear. Used for {@code proof_type=none} and as
     *  the final step after typed/photo proof locally passes. */
    private void confirmAndPostClear(Runnable onSuccess) {
        new android.app.AlertDialog.Builder(this)
            .setTitle("Clear deadline task?")
            .setMessage("This will clear the task and roll the deadline forward if an interval is set.")
            .setPositiveButton("Clear", (d, w) -> {
                setDeadlineTaskStatus("Clearing...");
                executor.execute(() -> {
                    postDeadlineClear();
                    if (onSuccess != null) handler.post(onSuccess);
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Typed-proof flow: show a text field; enable Clear button only when
     *  word count ≥ focus_lock_word_min (default 30). Mirrors the Collar's
     *  existing love-letter / word_min pattern. */
    private void promptTypedProof() {
        final int wordMin = Settings.Global.getInt(getContentResolver(),
            "focus_lock_word_min", 30);
        String hint = gstr("focus_lock_deadline_task_proof_hint");
        final EditText input = new EditText(this);
        input.setHint(hint.isEmpty() ? "Type your response" : hint);
        input.setMinLines(4);
        input.setGravity(android.view.Gravity.TOP | android.view.Gravity.START);
        input.setTextColor(0xFFe0e0e0);
        input.setBackgroundColor(0xFF111118);
        input.setPadding(24, 16, 24, 16);

        LinearLayout wrap = new LinearLayout(this);
        wrap.setOrientation(LinearLayout.VERTICAL);
        wrap.setPadding(32, 16, 32, 8);
        wrap.addView(input);
        final TextView counter = new TextView(this);
        counter.setText("0 / " + wordMin + " words");
        counter.setTextColor(0xFF888888);
        counter.setTextSize(12);
        counter.setPadding(0, 8, 0, 0);
        wrap.addView(counter);

        final android.app.AlertDialog dlg = new android.app.AlertDialog.Builder(this)
            .setTitle("Clear task: typed proof")
            .setView(wrap)
            .setPositiveButton("Submit", null)
            .setNegativeButton("Cancel", null)
            .show();
        final Button submit = dlg.getButton(android.app.AlertDialog.BUTTON_POSITIVE);
        submit.setEnabled(false);
        input.addTextChangedListener(new android.text.TextWatcher() {
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            public void onTextChanged(CharSequence s, int start, int before, int count) {}
            public void afterTextChanged(android.text.Editable s) {
                String t = s.toString().trim();
                int words = t.isEmpty() ? 0 : t.split("\\s+").length;
                counter.setText(words + " / " + wordMin + " words");
                submit.setEnabled(words >= wordMin);
            }
        });
        submit.setOnClickListener(v -> {
            dlg.dismiss();
            setDeadlineTaskStatus("Submitting typed proof...");
            executor.execute(this::postDeadlineClear);
        });
    }

    /** Photo-proof flow: open the camera, capture a bitmap, POST it to the
     *  relay's /webhook/verify-photo (same endpoint the Collar's photo-task
     *  uses). On {@code passed:true}, fire the signed clear. */
    private void promptPhotoProof() {
        // Reuse the existing TAKE_PHOTO path in onActivityResult. We stash a
        // flag via Settings.Global so the result handler knows to route this
        // photo through the deadline-task verify flow rather than the pay-
        // receipt upload flow.
        Settings.Global.putString(getContentResolver(),
            "focus_lock_deadline_photo_pending", "1");
        Intent intent = new Intent(MediaStore.ACTION_IMAGE_CAPTURE);
        try {
            startActivityForResult(intent, TAKE_PHOTO);
        } catch (Exception e) {
            Settings.Global.putString(getContentResolver(),
                "focus_lock_deadline_photo_pending", "0");
            setDeadlineTaskStatus("Camera unavailable: " + e.getMessage());
        }
    }

    /** Called from onActivityResult's TAKE_PHOTO branch when the deadline-
     *  photo-pending flag is set. Encodes, uploads, verifies, clears. */
    private void verifyDeadlinePhotoAndClear(Bitmap bitmap) {
        Settings.Global.putString(getContentResolver(),
            "focus_lock_deadline_photo_pending", "0");
        if (bitmap == null) {
            handler.post(() -> setDeadlineTaskStatus("Photo capture failed"));
            return;
        }
        String meshUrl = gstr("focus_lock_mesh_url");
        if (meshUrl.isEmpty()) {
            handler.post(() -> setDeadlineTaskStatus("No mesh URL configured"));
            return;
        }
        java.io.ByteArrayOutputStream baos = new java.io.ByteArrayOutputStream();
        bitmap.compress(Bitmap.CompressFormat.JPEG, 70, baos);
        String b64 = android.util.Base64.encodeToString(baos.toByteArray(),
            android.util.Base64.NO_WRAP);
        String task = gstr("focus_lock_deadline_task_text");
        String hint = gstr("focus_lock_deadline_task_proof_hint");
        String prompt = task + (hint.isEmpty() ? "" : " (" + hint + ")");
        handler.post(() -> setDeadlineTaskStatus("Verifying photo..."));
        try {
            String json = "{\"photo\":\"" + b64 + "\",\"task\":\""
                + prompt.replace("\\", "\\\\").replace("\"", "\\\"") + "\"}";
            URL url = new URL(meshUrl + "/webhook/verify-photo");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(60000);
            conn.getOutputStream().write(json.getBytes("UTF-8"));
            int code = conn.getResponseCode();
            BufferedReader reader = new BufferedReader(new InputStreamReader(
                code < 400 ? conn.getInputStream() : conn.getErrorStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) sb.append(line);
            reader.close();
            conn.disconnect();
            String response = sb.toString();
            boolean passed = response.contains("\"passed\":true");
            if (passed) {
                postDeadlineClear();
            } else {
                String reason = "";
                int ri = response.indexOf("\"reason\":\"");
                if (ri >= 0) {
                    ri += 10;
                    int re = response.indexOf("\"", ri);
                    if (re > ri) reason = response.substring(ri, re);
                }
                final String fr = reason;
                handler.post(() -> setDeadlineTaskStatus("Photo rejected: " + fr + " — try again"));
            }
        } catch (Exception e) {
            final String msg = e.getMessage();
            handler.post(() -> setDeadlineTaskStatus("Verify failed: " + msg));
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (resultCode != RESULT_OK) return;

        executor.execute(() -> {
            try {
                Bitmap bitmap = null;
                if (requestCode == TAKE_PHOTO && data != null && data.getExtras() != null) {
                    bitmap = (Bitmap) data.getExtras().get("data");
                } else if (requestCode == PICK_IMAGE && data != null && data.getData() != null) {
                    java.io.InputStream is = getContentResolver().openInputStream(data.getData());
                    bitmap = BitmapFactory.decodeStream(is);
                    if (is != null) is.close();
                }
                if (bitmap == null) return;

                // Deadline-task photo-proof intercept: when promptPhotoProof()
                // launched the camera it stamped this flag — routing the
                // capture through verify-photo + signed clear instead of the
                // usual message-photo flow.
                String deadlinePending = gstr("focus_lock_deadline_photo_pending");
                if ("1".equals(deadlinePending) && requestCode == TAKE_PHOTO) {
                    verifyDeadlinePhotoAndClear(bitmap);
                    return;
                }

                // Compress to JPEG
                java.io.ByteArrayOutputStream baos = new java.io.ByteArrayOutputStream();
                // Scale down if too large (max 800px)
                int maxDim = Math.max(bitmap.getWidth(), bitmap.getHeight());
                if (maxDim > 800) {
                    float scale = 800f / maxDim;
                    bitmap = Bitmap.createScaledBitmap(bitmap,
                        (int)(bitmap.getWidth() * scale), (int)(bitmap.getHeight() * scale), true);
                }
                bitmap.compress(Bitmap.CompressFormat.JPEG, 75, baos);
                byte[] imageBytes = baos.toByteArray();
                String imageB64 = android.util.Base64.encodeToString(imageBytes, android.util.Base64.NO_WRAP);

                // Photo attachments require a bunny-authed /mesh/upload endpoint
                // that doesn't exist yet (roadmap item separate from #6). For
                // now, degrade to a text message so the chat stays usable.
                String lionPubKey = gstr("focus_lock_lion_pubkey");
                final String placeholder = "[photo attached — attachment upload not yet shipped]";
                E2EEHelper.EncryptedMessage enc = null;
                if (E2EEHelper.canEncrypt(lionPubKey)) {
                    enc = E2EEHelper.encrypt(placeholder, lionPubKey);
                }
                final boolean ok = postMeshMessage(placeholder, false, false, enc);

                handler.post(() -> {
                    addMessageBubble(ok ? "[Photo placeholder sent]" : "[Photo send failed]",
                        true, System.currentTimeMillis());
                    statusText.setText(ok ? "Photo placeholder sent" : "Photo failed");
                });

            } catch (Exception e) {
                android.util.Log.e("BunnyTasker", "Photo send failed", e);
                handler.post(() -> statusText.setText("Photo failed: " + e.getMessage()));
            }
        });
    }

    private void doSetupImap() {
        View v = getLayoutInflater().inflate(android.R.layout.simple_list_item_1, null);
        // Build a simple dialog with email + password fields
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 24, 48, 24);

        EditText emailInput = new EditText(this);
        emailInput.setHint("Email address");
        emailInput.setInputType(android.text.InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        String savedEmail = prefs.getString("imap_email", "");
        if (!savedEmail.isEmpty()) emailInput.setText(savedEmail);
        layout.addView(emailInput);

        EditText passInput = new EditText(this);
        passInput.setHint("App password");
        passInput.setInputType(android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        layout.addView(passInput);

        EditText hostInput = new EditText(this);
        hostInput.setHint("IMAP host (default: imap.migadu.com)");
        String savedHost = prefs.getString("imap_host", "imap.migadu.com");
        hostInput.setText(savedHost);
        layout.addView(hostInput);

        new android.app.AlertDialog.Builder(this)
            .setTitle("Connect Payment Email")
            .setMessage("Sign in so your Lion's system can detect e-Transfer payments.\n\nAll past payments will be invalidated — only future payments count.")
            .setView(layout)
            .setPositiveButton("Connect", (d, w) -> {
                String email = emailInput.getText().toString().trim();
                String pass = passInput.getText().toString();
                String host = hostInput.getText().toString().trim();
                if (email.isEmpty() || pass.isEmpty()) {
                    statusText.setText("Email and password required");
                    return;
                }
                if (host.isEmpty()) host = "imap.migadu.com";
                prefs.edit().putString("imap_email", email).putString("imap_host", host).apply();
                final String fHost = host;
                executor.execute(() -> {
                    String json = "{\"user\":\"" + escJson(email) + "\",\"password\":\"" + escJson(pass)
                        + "\",\"host\":\"" + escJson(fHost) + "\"}";
                    sendWebhook("/mesh/set-imap-creds", json);
                    handler.post(() -> {
                        imapStatus.setText("Connected: " + email);
                        imapStatus.setTextColor(0xFF44aa44);
                        btnSetupImap.setText("Update Email");
                    });
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void refreshPaymentHistory() {
        // Roadmap #2 — bunny-authed payment history fetch. Signs a read
        // request with the registered bunny_pubkey and posts to
        // /api/mesh/{id}/payments on the mesh relay. Pre-2026-04-15 this
        // hit /mesh/ledger on the homelab, which was a plaintext endpoint
        // the server no longer speaks — the UI silently rendered empty.
        executor.execute(() -> {
            String meshId = gstr("focus_lock_mesh_id");
            String meshUrl = gstr("focus_lock_mesh_url");
            String nodeId = gstr("focus_lock_mesh_node_id");
            if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) return;
            long ts = System.currentTimeMillis();
            long since = 0;  // full history; server caps at 200 entries newest-first
            String payload = meshId + "|" + nodeId + "|" + since + "|" + ts;
            String signature = PairingManager.sign(getContentResolver(), payload);
            if (signature == null || signature.isEmpty()) return;
            try {
                JSONObject body = new JSONObject();
                body.put("node_id", nodeId);
                body.put("since", since);
                body.put("ts", ts);
                body.put("signature", signature);
                URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/payments");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(10000);
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
                int code = conn.getResponseCode();
                if (code >= 400) { conn.disconnect(); return; }
                BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
                conn.disconnect();

                JSONObject resp = new JSONObject(sb.toString());
                JSONArray entries = resp.optJSONArray("entries");
                if (entries == null) return;

                handler.post(() -> {
                    paymentHistory.removeAllViews();
                    int shown = Math.min(entries.length(), 20);
                    for (int i = 0; i < shown; i++) {
                        JSONObject e = entries.optJSONObject(i);
                        if (e == null) continue;
                        double amount = e.optDouble("amount", 0);
                        String desc = e.optString("description", "");
                        long ets = e.optLong("timestamp", 0);

                        TextView tv = new TextView(MainActivity.this);
                        tv.setText("+$" + String.format("%.2f", amount)
                            + "  " + desc
                            + "  " + formatRelativeTime(ets));
                        tv.setTextColor(0xFF66aa66);
                        tv.setTextSize(10);
                        tv.setPadding(0, 4, 0, 4);
                        paymentHistory.addView(tv);
                    }
                    if (entries.length() == 0) {
                        TextView tv = new TextView(MainActivity.this);
                        tv.setText("No payments yet");
                        tv.setTextColor(0xFF3a3a4a);
                        tv.setTextSize(11);
                        paymentHistory.addView(tv);
                    }
                });
            } catch (Exception e) {
                android.util.Log.e("BunnyTasker", "Payments fetch", e);
            }
        });
    }

    private void doPrepay() {
        String tier = gstr("focus_lock_sub_tier");
        if (tier.isEmpty()) return;
        int amt = "bronze".equals(tier) ? 25 : "silver".equals(tier) ? 35 : 50;
        new android.app.AlertDialog.Builder(this)
            .setTitle("Pay Early")
            .setMessage("Pay your " + tier.toUpperCase() + " tribute ($" + amt + ") now?\n\n"
                + "Your due date will extend by one week.\nYour banking app will open to send payment.")
            .setPositiveButton("PAY $" + amt, (d, w) -> {
                executor.execute(() -> {
                    // Reset due date to 7 days from NOW. Prepaying forfeits any remaining time
                    // on the current period — the bunny gives that extra time to the Lion as tribute.
                    long newDue = System.currentTimeMillis() + 7L * 24 * 3600 * 1000;
                    Settings.Global.putLong(getContentResolver(), "focus_lock_sub_due", newDue);
                    // Clear overdue warnings
                    prefs.edit()
                        .remove("warned_1h_" + tier)
                        .remove("warned_24h_" + tier)
                        .remove("locked_" + tier)
                        .apply();
                    // Track payment in Settings.Global (survives app reinstalls)
                    long totalPaid = Settings.Global.getLong(getContentResolver(), "focus_lock_total_paid_cents", 0);
                    Settings.Global.putLong(getContentResolver(), "focus_lock_total_paid_cents", totalPaid + amt * 100L);
                    sendWebhook("/webhook/bunny-message",
                        "{\"text\":\"Paid " + tier + " subscription early ($" + amt + ")\",\"type\":\"prepay\"}");
                    handler.post(() -> {
                        subStatus.setText(tier.toUpperCase() + " — paid early! Next due in 7d");
                        subStatus.setTextColor(0xFF44aa44);
                    });
                });
                // Open banking app
                try {
                    String bankPkg = Settings.Global.getString(getContentResolver(), "focus_lock_banking_app");
                    if (bankPkg != null && !bankPkg.isEmpty()) {
                        Intent launch = getPackageManager().getLaunchIntentForPackage(bankPkg.trim());
                        if (launch != null) {
                            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startActivity(launch);
                        }
                    }
                } catch (Exception e) {
                    statusText.setText("Banking app: " + e.getMessage());
                }
            })
            .setNegativeButton("Not yet", null)
            .show();
    }

    private void doUnsubscribe() {
        String tier = gstr("focus_lock_sub_tier");
        if (tier.isEmpty()) { subStatus.setText("No active subscription"); return; }
        int fee = "bronze".equals(tier) ? 50 : "silver".equals(tier) ? 70 : 100;
        new android.app.AlertDialog.Builder(this)
            .setTitle("Cancel Subscription")
            .setMessage("Cancel " + tier.toUpperCase() + " subscription?\n\nCancellation fee: $" + fee +
                " (added to paywall immediately)")
            .setPositiveButton("CANCEL SUB ($" + fee + ")", (d, w) -> {
                executor.execute(() -> {
                    String pw = gstr("focus_lock_paywall");
                    int currentPw = 0;
                    try { currentPw = Integer.parseInt(pw); } catch (Exception e) { android.util.Log.e("BunnyTasker", "error", e); }
                    Settings.Global.putString(getContentResolver(), "focus_lock_paywall", String.valueOf(currentPw + fee));
                    Settings.Global.putString(getContentResolver(), "focus_lock_sub_tier", "");
                    Settings.Global.putLong(getContentResolver(), "focus_lock_sub_due", 0);
                    sendWebhook("/webhook/bunny-message",
                        "{\"text\":\"Unsubscribed from " + tier + " (fee: $" + fee + ")\",\"type\":\"unsubscription\"}");
                    handler.post(() -> subStatus.setText("Cancelled. $" + fee + " fee charged."));
                });
            })
            .setNegativeButton("Keep", null)
            .show();
    }

    private void doFreeUnlock() {
        new android.app.AlertDialog.Builder(this)
            .setTitle("Use Free Unlock")
            .setMessage("Use your monthly free unlock? (Gold perk)\n\nThis cannot be undone.")
            .setPositiveButton("UNLOCK", (d, w) -> {
                executor.execute(() -> {
                    int used = Settings.Global.getInt(getContentResolver(), "focus_lock_free_unlocks", 0);
                    if (used >= 1) {
                        handler.post(() -> statusText.setText("Free unlock already used this month"));
                        return;
                    }
                    // Route through Collar so mesh propagates the unlock
                    boolean ok = postToCollar("/api/unlock", "{}");
                    if (ok) {
                        // Track usage locally (mesh order from Lion controls the cap)
                        Settings.Global.putInt(getContentResolver(), "focus_lock_free_unlocks", used + 1);
                        sendWebhook("/webhook/bunny-message",
                            "{\"text\":\"Used free Gold unlock\",\"type\":\"free-unlock\"}");
                        handler.post(() -> statusText.setText("Free unlock used!"));
                    } else {
                        handler.post(() -> statusText.setText("Unlock failed — Collar unreachable"));
                    }
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doSelfLock(int minutes) {
        new android.app.AlertDialog.Builder(this)
            .setTitle("Self-Lock: " + minutes + " minutes")
            .setMessage("Lock yourself for " + minutes + " minutes?\n\nOnly your Lion can extend or make this permanent.")
            .setPositiveButton("LOCK", (d, w) -> {
                executor.execute(() -> {
                    // POST to the Collar's local API — it owns Settings.Global,
                    // bumps meshVersion, and pushes to all peers.
                    boolean ok = postToCollar("/api/lock",
                        "{\"timer\":\"" + minutes
                        + "\",\"message\":\"Self-locked for " + minutes + " minutes. Good bunny.\""
                        + ",\"mode\":\"basic\",\"shame\":1,\"target\":\"phone\"}");

                    if (ok) {
                        sendWebhook("/webhook/bunny-message",
                            "{\"text\":\"Self-locked for " + minutes + " minutes\",\"type\":\"self-lock\"}");
                        handler.post(() -> statusText.setText("Self-locked for " + minutes + "m"));
                    } else {
                        handler.post(() -> statusText.setText("Self-lock failed — Collar unreachable"));
                    }
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private boolean postToCollar(String path, String json) {
        int[] ports = {8432, 8433};
        for (int port : ports) {
            try {
                java.net.URL url = new java.net.URL("http://127.0.0.1:" + port + path);
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(5000);
                conn.getOutputStream().write(json.getBytes());
                int code = conn.getResponseCode();
                conn.disconnect();
                if (code == 200) return true;
            } catch (Exception e) { /* try next port */ }
        }
        return false;
    }

    private void launchAdminNag(android.content.ComponentName admin, String explanation) {
        try {
            Intent activate = new Intent(android.app.admin.DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN);
            activate.putExtra(android.app.admin.DevicePolicyManager.EXTRA_DEVICE_ADMIN, admin);
            activate.putExtra(android.app.admin.DevicePolicyManager.EXTRA_ADD_EXPLANATION, explanation);
            activate.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TOP);
            android.app.PendingIntent pi = android.app.PendingIntent.getActivity(this, 99, activate,
                android.app.PendingIntent.FLAG_UPDATE_CURRENT | android.app.PendingIntent.FLAG_IMMUTABLE);

            android.app.NotificationChannel ch = new android.app.NotificationChannel(
                "admin_nag", "Admin Re-activation", android.app.NotificationManager.IMPORTANCE_HIGH);
            ch.setBypassDnd(true);
            ch.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
            getSystemService(android.app.NotificationManager.class).createNotificationChannel(ch);

            android.app.Notification n = new android.app.Notification.Builder(this, "admin_nag")
                .setContentTitle("Device admin removed")
                .setContentText(explanation)
                .setSmallIcon(android.R.drawable.ic_lock_lock)
                .setFullScreenIntent(pi, true)
                .setCategory(android.app.Notification.CATEGORY_ALARM)
                .setPriority(android.app.Notification.PRIORITY_MAX)
                .setOngoing(true)
                .build();
            getSystemService(android.app.NotificationManager.class).notify(97, n);
        } catch (Exception e) {
            android.util.Log.e("BunnyTasker", "Admin activation launch failed", e);
        }
    }

    /** Roadmap #6: bunny-authed message append.
     *  Signs {mesh|node|from|text|pinned|mandatory|ts} with the registered
     *  bunny privkey and POSTs /api/mesh/{id}/messages/send. For E2EE, the
     *  signed `text` is the literal "[e2ee]" marker; ciphertext / encrypted_key
     *  / iv ride in the body as passthrough. A MITM flipping ciphertext still
     *  breaks decrypt (fail-closed) since we bind the plaintext marker.
     *  Returns true on 200, false otherwise. Blocking — call from executor. */
    private boolean postMeshMessage(String text, boolean pinned, boolean mandatory,
                                    E2EEHelper.EncryptedMessage enc) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) return false;
        long ts = System.currentTimeMillis();
        String signedText = (enc != null) ? "[e2ee]" : text;
        String payload = meshId + "|" + nodeId + "|bunny|" + signedText
            + "|" + (pinned ? "1" : "0") + "|" + (mandatory ? "1" : "0") + "|" + ts;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) return false;
        try {
            JSONObject body = new JSONObject();
            body.put("node_id", nodeId);
            body.put("from", "bunny");
            body.put("text", signedText);
            if (pinned) body.put("pinned", true);
            if (mandatory) body.put("mandatory_reply", true);
            if (enc != null) {
                body.put("encrypted", true);
                body.put("ciphertext", enc.ciphertext);
                body.put("encrypted_key", enc.encryptedKey);
                body.put("iv", enc.iv);
            }
            body.put("ts", ts);
            body.put("signature", signature);
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/messages/send");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            conn.disconnect();
            return code < 400;
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "postMeshMessage failed", e);
            return false;
        }
    }

    /** Roadmap #6: signed fetch of the per-mesh message log.
     *  Returns the raw JSONObject response {ok, messages[], since} or null on error.
     *  Caller derives unread/pinned/mandatory state locally from message fields —
     *  the server no longer computes those. Blocking — call from executor. */
    private JSONObject fetchMeshMessagesSigned(long since, int limit) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) return null;
        long ts = System.currentTimeMillis();
        String payload = meshId + "|" + nodeId + "|bunny|" + since + "|" + ts;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) return null;
        try {
            JSONObject body = new JSONObject();
            body.put("node_id", nodeId);
            body.put("from", "bunny");
            body.put("since", since);
            body.put("limit", limit);
            body.put("ts", ts);
            body.put("signature", signature);
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/messages/fetch");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            if (code >= 400) { conn.disconnect(); return null; }
            BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = r.readLine()) != null) sb.append(line);
            r.close();
            conn.disconnect();
            return new JSONObject(sb.toString());
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "fetchMeshMessagesSigned failed", e);
            return null;
        }
    }

    /** Roadmap #6: flag a message as read or replied.
     *  Signed over {mesh|node|from|message_id|status|ts}. Blocking — from executor. */
    private boolean markMeshMessage(String messageId, String status) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) return false;
        long ts = System.currentTimeMillis();
        String payload = meshId + "|" + nodeId + "|bunny|" + messageId + "|" + status + "|" + ts;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) return false;
        try {
            JSONObject body = new JSONObject();
            body.put("node_id", nodeId);
            body.put("from", "bunny");
            body.put("message_id", messageId);
            body.put("status", status);
            body.put("ts", ts);
            body.put("signature", signature);
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/messages/mark");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            conn.disconnect();
            return code < 400;
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "markMeshMessage failed", e);
            return false;
        }
    }

    private void sendMessage(String msg) {
        executor.execute(() -> {
            String lionPubKey = gstr("focus_lock_lion_pubkey");
            E2EEHelper.EncryptedMessage enc = null;
            if (E2EEHelper.canEncrypt(lionPubKey)) {
                enc = E2EEHelper.encrypt(msg, lionPubKey);
            }
            postMeshMessage(msg, false, false, enc);
            // Record check-in timestamp (any message counts as check-in)
            Settings.Global.putLong(getContentResolver(), "focus_lock_checkin_timestamp",
                System.currentTimeMillis());
            // Mark any pending mandatory replies as answered
            markMandatoryReplied();
            handler.post(() -> addMessageBubble(msg, true, System.currentTimeMillis()));
        });
    }

    /** Overdue threshold for mandatory lion replies. Legacy server computed
     *  this; client now owns it. 4h default keeps the semantics without being
     *  hair-trigger. */
    private static final long MANDATORY_OVERDUE_MS = 4L * 60 * 60 * 1000;

    private void refreshMeshMessages() {
        executor.execute(() -> {
            try {
                JSONObject data = fetchMeshMessagesSigned(0, 30);
                if (data == null) return;
                JSONArray msgs = data.optJSONArray("messages");
                if (msgs == null) return;

                String myNodeId = gstr("focus_lock_mesh_node_id");
                String bunnyPrivKey = gstr("focus_lock_bunny_privkey");
                long now = System.currentTimeMillis();
                boolean anyOverdue = false;
                java.util.List<String> toMarkRead = new java.util.ArrayList<>();

                // First pass: notifications, unread tracking, overdue detection.
                // "unread by me" = from lion AND `bunny` not in read_by.
                for (int i = 0; i < msgs.length(); i++) {
                    JSONObject m = msgs.optJSONObject(i);
                    if (m == null) continue;
                    if (!"lion".equals(m.optString("from"))) continue;
                    JSONArray readBy = m.optJSONArray("read_by");
                    boolean readByMe = false;
                    if (readBy != null) {
                        for (int j = 0; j < readBy.length(); j++) {
                            if ("bunny".equals(readBy.optString(j))) { readByMe = true; break; }
                        }
                    }
                    String mid = m.optString("id", "");
                    boolean pinnedFlag = m.optBoolean("pinned", false);
                    boolean mandatoryFlag = m.optBoolean("mandatory_reply", false);
                    boolean replied = m.optBoolean("replied", false);

                    if (!readByMe) {
                        String notifText = m.optString("text", "");
                        if (m.optBoolean("encrypted", false) && E2EEHelper.canDecrypt(bunnyPrivKey)) {
                            String decrypted = E2EEHelper.decrypt(
                                m.optString("ciphertext", ""),
                                m.optString("encrypted_key", ""),
                                m.optString("iv", ""),
                                bunnyPrivKey);
                            notifText = decrypted != null ? decrypted : "[encrypted message]";
                        }
                        showLionMessageNotification(notifText, pinnedFlag, mandatoryFlag);
                        if (!mid.isEmpty()) toMarkRead.add(mid);
                    }

                    // Overdue = lion's mandatory-reply older than threshold and not yet replied.
                    long mts = m.optLong("ts", 0);
                    if (mandatoryFlag && !replied && mts > 0 && (now - mts) > MANDATORY_OVERDUE_MS) {
                        anyOverdue = true;
                    }
                }

                // Ack reads — fire-and-forget per message.
                for (String mid : toMarkRead) markMeshMessage(mid, "read");

                // Update message bubbles. Server returns newest-first, so we
                // render top-to-bottom in that order.
                handler.post(() -> {
                    messagesContainer.removeAllViews();
                    for (int i = 0; i < msgs.length(); i++) {
                        JSONObject m = msgs.optJSONObject(i);
                        if (m == null) continue;
                        String text = m.optString("text", "");
                        boolean fromBunny = "bunny".equals(m.optString("from"));
                        long ts = m.optLong("ts", 0);
                        if (m.optBoolean("encrypted", false) && !fromBunny) {
                            if (E2EEHelper.canDecrypt(bunnyPrivKey)) {
                                String decrypted = E2EEHelper.decrypt(
                                    m.optString("ciphertext", ""),
                                    m.optString("encrypted_key", ""),
                                    m.optString("iv", ""),
                                    bunnyPrivKey);
                                text = decrypted != null ? decrypted : "[encrypted]";
                            } else {
                                text = "[encrypted — missing key]";
                            }
                        } else if (m.optBoolean("encrypted", false) && fromBunny) {
                            text = "[encrypted — sent by you]";
                        }
                        boolean isMandatory = m.optBoolean("mandatory_reply", false)
                            && !m.optBoolean("replied", false);
                        if (isMandatory && !fromBunny) {
                            text = "[REPLY REQUIRED] " + text;
                        }
                        addMessageBubble(text, fromBunny, ts);
                    }
                });

                // Enforce mandatory reply — auto-lock if overdue.
                if (anyOverdue) {
                    int active = Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0);
                    if (active == 0) {
                        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 1);
                        Settings.Global.putString(getContentResolver(), "focus_lock_message",
                            "Missed mandatory reply. Message your Lion NOW.");
                        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "basic");
                        Settings.Global.putLong(getContentResolver(), "focus_lock_locked_at",
                            System.currentTimeMillis());
                    }
                }

            } catch (Exception e) { android.util.Log.e("BunnyTasker", "Mesh messages", e); }
        });
    }

    private void showLionMessageNotification(String text, boolean pinned, boolean mandatory) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            String channelId = pinned ? "pinned_silent" : "lion_msg";

            if (!pinned) {
                NotificationChannel ch = new NotificationChannel(
                    "lion_msg", "Messages from Lion", NotificationManager.IMPORTANCE_HIGH);
                ch.setLockscreenVisibility(android.app.Notification.VISIBILITY_PUBLIC);
                nm.createNotificationChannel(ch);
            }

            String title = mandatory ? "REPLY REQUIRED from your Lion" :
                           pinned ? "Pinned message from your Lion" :
                           "Message from your Lion";

            android.app.Notification.Builder b = new android.app.Notification.Builder(this, channelId)
                .setContentTitle(title)
                .setContentText(text)
                .setStyle(new android.app.Notification.BigTextStyle().bigText(text))
                .setSmallIcon(android.R.drawable.ic_dialog_email)
                .setVisibility(android.app.Notification.VISIBILITY_PUBLIC);

            if (pinned) b.setOngoing(true);
            if (mandatory) b.setOngoing(true);

            nm.notify(pinned ? 300 : 320, b.build());
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "Lion notif", e); }
    }

    /** Mark every outstanding mandatory-reply lion message as replied.
     *  Called after the bunny sends a message — any message counts as answering. */
    private void markMandatoryReplied() {
        try {
            JSONObject data = fetchMeshMessagesSigned(0, 50);
            if (data == null) return;
            JSONArray msgs = data.optJSONArray("messages");
            if (msgs == null) return;
            for (int i = 0; i < msgs.length(); i++) {
                JSONObject m = msgs.optJSONObject(i);
                if (m == null) continue;
                if (!"lion".equals(m.optString("from"))) continue;
                if (!m.optBoolean("mandatory_reply", false)) continue;
                if (m.optBoolean("replied", false)) continue;
                String mid = m.optString("id", "");
                if (!mid.isEmpty()) markMeshMessage(mid, "replied");
            }
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "Mark replied", e); }
    }

    private void addMessageBubble(String text, boolean fromBunny, long timestamp) {
        TextView tv = new TextView(this);
        String timeStr = formatRelativeTime(timestamp);
        tv.setText((fromBunny ? "You" : "Lion") + " · " + timeStr + "\n" + text);
        tv.setTextColor(fromBunny ? 0xFFaa88cc : 0xFFcc9900);
        tv.setTextSize(12);
        tv.setPadding(12, 8, 12, 8);
        tv.setBackgroundColor(fromBunny ? 0xFF120e1a : 0xFF1a1808);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, 4);
        tv.setLayoutParams(lp);
        messagesContainer.addView(tv);
        while (messagesContainer.getChildCount() > 50) {
            messagesContainer.removeViewAt(0);
        }
    }

    private String formatRelativeTime(long ts) {
        if (ts <= 0) return "";
        long diff = System.currentTimeMillis() - ts;
        if (diff < 60000) return "just now";
        if (diff < 3600000) return (diff / 60000) + "m ago";
        if (diff < 86400000) return (diff / 3600000) + "h ago";
        return (diff / 86400000) + "d ago";
    }

    private void saveMessage(String from, String text) {
        try {
            File f = new File(getFilesDir(), "messages.json");
            JSONArray arr = new JSONArray();
            if (f.exists()) {
                FileReader r = new FileReader(f);
                StringBuilder sb = new StringBuilder();
                int c; while ((c = r.read()) != -1) sb.append((char) c);
                r.close();
                arr = new JSONArray(sb.toString());
            }
            JSONObject msg = new JSONObject();
            msg.put("from", from);
            msg.put("text", text);
            msg.put("ts", System.currentTimeMillis());
            arr.put(msg);
            // Cap at 200 messages
            while (arr.length() > 200) arr.remove(0);
            FileWriter w = new FileWriter(f);
            w.write(arr.toString());
            w.close();
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "Save msg", e); }
    }

    private void loadMessages() {
        try {
            File f = new File(getFilesDir(), "messages.json");
            if (!f.exists()) return;
            FileReader r = new FileReader(f);
            StringBuilder sb = new StringBuilder();
            int c; while ((c = r.read()) != -1) sb.append((char) c);
            r.close();
            JSONArray arr = new JSONArray(sb.toString());
            // Show last 50
            int start = Math.max(0, arr.length() - 50);
            for (int i = start; i < arr.length(); i++) {
                JSONObject msg = arr.getJSONObject(i);
                String from = msg.optString("from", "bunny");
                String text = msg.optString("text", "");
                long ts = msg.optLong("ts", 0);
                addMessageBubble(text, "bunny".equals(from), ts);
            }
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "Load msgs", e); }
    }

    private String lastPinnedMessage = "";

    private void showPinnedNotification(String message) {
        if (message.equals(lastPinnedMessage)) return;
        lastPinnedMessage = message;
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "pinned", "Pinned Messages", NotificationManager.IMPORTANCE_HIGH);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "pinned")
                .setContentTitle("Message from your Lion")
                .setContentText(message)
                .setStyle(new Notification.BigTextStyle().bigText(message))
                .setSmallIcon(android.R.drawable.ic_dialog_email)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .build();
            nm.notify(300, n);
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "error", e); }
    }

    private int lastNotifiedBalance = -1;

    private void showBalanceNotification(int amount) {
        // Only post/update if amount changed — prevents repeated sounds
        if (amount == lastNotifiedBalance) return;
        lastNotifiedBalance = amount;
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "balance", "Outstanding Balance", NotificationManager.IMPORTANCE_LOW);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            ch.setSound(null, null);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "balance")
                .setContentTitle("Outstanding balance: $" + amount)
                .setContentText("Pay via e-Transfer to clear. Open Bunny Tasker to pay.")
                .setSmallIcon(android.R.drawable.ic_dialog_alert)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .build();
            nm.notify(301, n);
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "error", e); }
    }

    private void sendWebhook(String path, String json) {
        for (String base : HOMELAB_URLS) {
            try {
                URL url = new URL(base + path);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(5000);
                conn.getOutputStream().write(json.getBytes());
                int code = conn.getResponseCode();
                conn.disconnect();
                if (code < 500) { HOMELAB = base; return; }
            } catch (Exception e) { /* try next */ }
        }
        android.util.Log.e("BunnyTasker", "All homelab URLs failed for " + path);
    }

    private String fetchFromHomelab(String path) {
        for (String base : HOMELAB_URLS) {
            try {
                URL url = new URL(base + path);
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(3000);
                conn.setReadTimeout(5000);
                BufferedReader r = new BufferedReader(new InputStreamReader(conn.getInputStream()));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
                conn.disconnect();
                HOMELAB = base;
                return sb.toString();
            } catch (Exception e) { /* try next */ }
        }
        return null;
    }

    private String gstr(String key) {
        String v = Settings.Global.getString(getContentResolver(), key);
        return (v == null || v.equals("null") || v.equals("\"\"")) ? "" : v;
    }

    private String escJson(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }

    /** Bunny-signed event post to /api/mesh/{id}/escape-event. Mirrors the
     *  Collar's ControlService.postEventToServer — same endpoint, same sig
     *  format (mesh_id|node_id|event_type|ts). Used for tamper_removed when
     *  the companion detects the Collar's device admin is gone.
     *  P2 paywall hardening (2026-04-17): server now owns the penalty amount,
     *  so the companion reports instead of writing paywall locally. */
    private void postEventToServer(String eventType, String details) {
        executor.execute(() -> {
            try {
                String meshId = gstr("focus_lock_mesh_id");
                String meshUrl = gstr("focus_lock_mesh_url");
                String nodeId = gstr("focus_lock_mesh_node_id");
                if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) return;
                long ts = System.currentTimeMillis();
                String payload = meshId + "|" + nodeId + "|" + eventType + "|" + ts;
                String signature = PairingManager.sign(getContentResolver(), payload);
                if (signature == null || signature.isEmpty()) return;
                String safeDetails = (details == null ? "" : escJson(details));
                String body = "{\"node_id\":\"" + nodeId
                    + "\",\"event_type\":\"" + eventType
                    + "\",\"details\":\"" + safeDetails
                    + "\",\"ts\":" + ts
                    + ",\"signature\":\"" + signature + "\"}";
                URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/escape-event");
                HttpURLConnection conn = (HttpURLConnection) url.openConnection();
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(10000);
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.getOutputStream().write(body.getBytes("UTF-8"));
                int code = conn.getResponseCode();
                if (code >= 400) {
                    android.util.Log.w("BunnyTasker", "escape-event POST " + code + " for " + eventType);
                }
                conn.disconnect();
            } catch (Exception e) {
                android.util.Log.w("BunnyTasker", "postEventToServer(" + eventType + "): " + e.getMessage());
            }
        });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        handler.removeCallbacks(poller);
    }
}

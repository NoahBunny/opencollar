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
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.view.View;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
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
    private View messagesBody;
    private android.widget.ImageView qrCodeView;
    private EditText messageInput;
    private Button btnPay, btnSend, btnFreeUnlock, btnShowQr, btnPrepay, btnSetupPayerIdentity;
    private Button btnGamble, tabNow, tabOwe, tabTalk, tabMe;
    private View pageNow, pageOwe, pageTalk, pageMe;
    private TextView gambleStatus, costToWait;
    private Sparkline balanceSpark;
    private int currentTab = 0;
    /** Flip budget from the relay; -1 until a /payments read fills it in. */
    private int gambleCooldownS = -1, gambleRemainingToday = -1;
    private TextView balanceAmount, balanceDetail, imapStatus, tierBadge, messagesHeader, payerIdentityStatus;
    // Collapsed by default — the messaging block runs to ~440dp (input row +
    // 380dp scroll) and was overwhelming the home view. User flips it open
    // when they want to compose. State persists across launches via
    // prefs.messages_expanded so the choice sticks.
    private boolean messagesExpanded = false;
    private static final int PICK_IMAGE = 1001;
    private static final int TAKE_PHOTO = 1002;
    private static final int REQ_BUNNY_ONBOARD = 1003;
    private LinearLayout paymentHistory;
    private View balanceCard;
    private View statusBar;

    private Handler handler;
    private ExecutorService executor;
    private SharedPreferences prefs;
    private Runnable poller;
    private int meshSyncCounter = 0;
    private int messageRefreshCounter = 0;
    private Thread ntfyThread;
    private volatile boolean ntfyRunning = false;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(getResources().getIdentifier("activity_main", "layout", getPackageName()));

        handler = new Handler(Looper.getMainLooper());
        executor = Executors.newSingleThreadExecutor();
        prefs = getSharedPreferences("bunnytasker", MODE_PRIVATE);

        // Android 13+ requires runtime POST_NOTIFICATIONS grant; the manifest
        // declaration alone is not enough. Without this, every notif this app
        // posts (balance, confined, lion-message, admin-nag) is silently
        // dropped by the system. Request it on first launch — Android remembers
        // the grant so we only get prompted once.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU
                && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)
                    != android.content.pm.PackageManager.PERMISSION_GRANTED) {
            requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS}, 7301);
        }

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
        messagesBody = findViewById(fid("messages_body"));
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

        // Post-pairing display-name editor: long-press the paired fingerprint.
        // Gives already-joined devices (which never see the join dialog again) a
        // way to set/change how they appear to the Lion.
        if (pairedFingerprint != null) {
            pairedFingerprint.setOnLongClickListener(v -> { showEditNameDialog(); return true; });
        }

        // Pair-reset button intentionally hidden: per the consensual design
        // (CLAUDE.md: "Release Forever button (Lion only)"), only the Lion
        // or a factory reset can release the Collar. Previously Bunny
        // Tasker exposed a "Reset pair state" button that POSTed to
        // /api/pair-reset on the Collar — a bunny-initiated escape path
        // that broke the power-dynamic contract. The XML view still exists
        // for backwards compat with older layouts; we just don't wire a
        // click listener and force it hidden below in the paired-section
        // visibility block.
        TextView btnPairReset = (TextView) findViewById(fid("btn_pair_reset"));
        if (btnPairReset != null) {
            btnPairReset.setVisibility(View.GONE);
        }

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
            // Always render the pairing QR FIRST so it's ready underneath — the
            // onboarding must never gate the user out of pairing.
            renderPairingQr();
            // First-run: show Bunny Tasker's own warm welcome (independent of the
            // Collar's Terms of Surrender), THEN hand off to the Collar consent.
            // Ordering: welcome → Terms of Surrender → device-admin.
            if (!prefs.getBoolean("bunny_onboarded", false)) {
                startActivityForResult(new Intent(this, BunnyWelcomeActivity.class), REQ_BUNNY_ONBOARD);
            } else {
                // No cable in production — when Bunny Tasker opens unpaired and
                // the Collar isn't yet device-admin, kick the user into the
                // Collar's Terms-of-Surrender screen so the full flow happens
                // inside the normal UI path. Safe to call repeatedly.
                maybeLaunchCollarConsent();
            }
        }
        updateCrownConnectionState();

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

        payerIdentityStatus = (TextView) findViewById(fid("payer_identity_status"));
        btnSetupPayerIdentity = (Button) findViewById(fid("btn_setup_payer_identity"));
        btnSetupPayerIdentity.setOnClickListener(v -> doSetupPayerIdentity());
        refreshPayerIdentityStatus();

        // ── Tabs (Now / Owe / Talk / Me) ──
        pageNow = findViewById(fid("page_now"));
        pageOwe = findViewById(fid("page_owe"));
        pageTalk = findViewById(fid("page_talk"));
        pageMe = findViewById(fid("page_me"));
        tabNow = (Button) findViewById(fid("tab_now"));
        tabOwe = (Button) findViewById(fid("tab_owe"));
        tabTalk = (Button) findViewById(fid("tab_talk"));
        tabMe = (Button) findViewById(fid("tab_me"));
        if (tabNow != null) tabNow.setOnClickListener(v -> selectTab(0));
        if (tabOwe != null) tabOwe.setOnClickListener(v -> selectTab(1));
        if (tabTalk != null) tabTalk.setOnClickListener(v -> selectTab(2));
        if (tabMe != null) tabMe.setOnClickListener(v -> selectTab(3));
        selectTab(prefs.getInt("last_tab", 0));

        // ── Owe tab extras ──
        costToWait = (TextView) findViewById(fid("cost_to_wait"));
        balanceSpark = (Sparkline) findViewById(fid("balance_spark"));
        gambleStatus = (TextView) findViewById(fid("gamble_status"));
        btnGamble = (Button) findViewById(fid("btn_gamble"));
        if (btnGamble != null) btnGamble.setOnClickListener(v -> doGamble());

        View btnOffer = findViewById(fid("btn_make_offer"));
        if (btnOffer != null) btnOffer.setOnClickListener(v -> doMakeOffer());

        View btnDev = findViewById(fid("btn_devotion"));
        if (btnDev != null) btnDev.setOnClickListener(v -> doDevotion());

        View btnTighten = findViewById(fid("btn_tighten"));
        if (btnTighten != null) btnTighten.setOnClickListener(v -> doTighten());
        tierBadge = (TextView) findViewById(fid("tier_badge"));
        messagesHeader = (TextView) findViewById(fid("messages_header"));
        messagesExpanded = prefs.getBoolean("messages_expanded", false);
        applyMessagesExpanded();
        if (messagesHeader != null) {
            messagesHeader.setOnClickListener(v -> {
                messagesExpanded = !messagesExpanded;
                prefs.edit().putBoolean("messages_expanded", messagesExpanded).apply();
                applyMessagesExpanded();
            });
        }

        // Self-lock buttons
        findViewById(fid("btn_selflock_15")).setOnClickListener(v -> doSelfLock(15));
        findViewById(fid("btn_selflock_30")).setOnClickListener(v -> doSelfLock(30));
        findViewById(fid("btn_selflock_60")).setOnClickListener(v -> doSelfLock(60));
        findViewById(fid("btn_selflock_120")).setOnClickListener(v -> doSelfLock(120));

        // Pay button — opens configured banking app. Lookup order:
        //   1. SharedPreferences "banking_app" (set by the picker on first tap;
        //      consumer-install path, no adb needed)
        //   2. Settings.Global focus_lock_banking_app (legacy adb-provisioned)
        //   3. Picker dialog over installed apps that match shared/banks.json
        // Long-press the Pay button to re-pick.
        btnPay.setOnClickListener(v -> launchBankingApp(false));
        btnPay.setOnLongClickListener(v -> { launchBankingApp(true); return true; });

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

        // ntfy push subscriber — wakes up immediate meshSync on Lion-issued
        // order changes. Mirrors ControlService.ntfySubscribeLoop on the Collar.
        // Without this, the every-30s meshSync poll was the only refresh path,
        // so order propagation to the Bunny Tasker UI lagged by up to 30s.
        startNtfySubscriber();

        // Start polling
        poller = () -> {
            // Cost-to-wait is pure local arithmetic over Settings.Global, so
            // it is cheap enough to recompute each tick — but only while the
            // tab showing it is actually open.
            if (currentTab == 0) {
                refreshLiveState();
            } else if (currentTab == 1) {
                refreshCostToWait();
                refreshCharges();
                renderGambleBudget();
            } else if (currentTab == 3) {
                refreshDesktops();
            }
            executor.execute(() -> refreshStats());
            executor.execute(this::drainEvidenceOutbox);  // serverless evidence → Lion's inbox
            executor.execute(this::maybeSendPendingPayerIdentity);  // deferred onboarding payer identity
            handler.postDelayed(poller, 5000);
        };
        handler.post(poller);
    }

    /** Start the long-poll ntfy subscriber thread. Topic is derived from
     *  mesh_id (focuslock-{mesh_id}) unless an explicit override was written
     *  to focus_lock_ntfy_topic. Server is focus_lock_ntfy_server or ntfy.sh.
     *  Wake-up triggers an immediate refreshStats() with meshSyncCounter
     *  forced to fire — sub-second propagation when ntfy is reachable. */
    private void startNtfySubscriber() {
        if (ntfyThread != null && ntfyThread.isAlive()) return;
        String meshId = gstr("focus_lock_mesh_id");
        if (meshId.isEmpty()) {
            android.util.Log.i("BunnyTasker", "ntfy: skipped (mesh_id not set yet)");
            return;
        }
        String topic = gstr("focus_lock_ntfy_topic");
        if (topic.isEmpty()) topic = "focuslock-" + meshId;
        String server = gstr("focus_lock_ntfy_server");
        if (server.isEmpty()) server = "https://ntfy.sh";
        final String fServer = server;
        final String fTopic = topic;
        ntfyRunning = true;
        ntfyThread = new Thread(() -> ntfySubscribeLoop(fServer, fTopic), "ntfy-subscribe");
        ntfyThread.setDaemon(true);
        ntfyThread.start();
        android.util.Log.w("BunnyTasker", "ntfy subscriber started: " + server + "/" + topic);
    }

    private void ntfySubscribeLoop(String server, String topic) {
        String since = String.valueOf(System.currentTimeMillis() / 1000 - 60);
        int backoff = 1;
        while (ntfyRunning) {
            HttpURLConnection conn = null;
            try {
                String url = server + "/" + topic + "/json?since=" + since;
                conn = (HttpURLConnection) new URL(url).openConnection();
                conn.setRequestMethod("GET");
                conn.setReadTimeout(90_000);
                conn.setConnectTimeout(10_000);
                BufferedReader reader = new BufferedReader(
                    new InputStreamReader(conn.getInputStream(), "UTF-8"));
                String line;
                while (ntfyRunning && (line = reader.readLine()) != null) {
                    line = line.trim();
                    if (line.isEmpty()) continue;
                    try {
                        JSONObject msg = new JSONObject(line);
                        String msgId = msg.optString("id", "");
                        if (!msgId.isEmpty()) since = msgId;
                        String event = msg.optString("event", "");
                        if ("open".equals(event) || "keepalive".equals(event)) continue;
                        String body = msg.optString("message", "");
                        if (!body.isEmpty()) {
                            try {
                                JSONObject data = new JSONObject(body);
                                int ver = data.optInt("v", -1);
                                if (ver >= 0) {
                                    android.util.Log.w("BunnyTasker", "ntfy: wake-up v" + ver);
                                    executor.execute(() -> {
                                        try { meshSync(); } catch (Exception ignored) {}
                                        try { refreshStats(); } catch (Exception ignored) {}
                                        // Order updates often pair with a new
                                        // message — refresh the inbox too so
                                        // bunny sees lion's message instantly
                                        // instead of waiting 10s for the next
                                        // refreshMeshMessages tick.
                                        try { refreshMeshMessages(); } catch (Exception ignored) {}
                                    });
                                }
                            } catch (Exception ignored) {}
                        }
                    } catch (Exception ignored) {}
                }
                reader.close();
                backoff = 1;
            } catch (Exception e) {
                android.util.Log.i("BunnyTasker", "ntfy: subscribe error: " + e);
                try { Thread.sleep(backoff * 1000L); } catch (InterruptedException ie) { break; }
                backoff = Math.min(backoff * 2, 60);
            } finally {
                if (conn != null) try { conn.disconnect(); } catch (Exception ignored) {}
            }
        }
    }

    private int fid(String name) {
        return getResources().getIdentifier(name, "id", getPackageName());
    }

    // ── Devotion: voluntary tasks, a subscriber perk ──
    //
    // Everything up to here is state the bunny is SUBJECT to. This is the one
    // thing they can choose to do. They draw from the same 144-line veneration
    // catalogue the Lion imposes from, type it out, and earn a rank.
    //
    // The reward is points and never money. A voluntary task that took money
    // off the balance would be a discount the bunny writes for themselves,
    // which is the one thing this system exists not to hand over — they
    // already hold the device, the root and the drive. Points are a record of
    // effort they chose; the Lion may reward it, convert it, or ignore it.
    // Standing is earnable, a discount is not.
    //
    // The weekly cap and the counter live on the relay, in a file this device
    // cannot reach. The typing discipline below is client-side and a tampered
    // client can always lie about it — which is precisely why what it buys is
    // a rank rather than a dollar.

    private VenerationTasks catalogue;
    private String devotionRank = "", devotionNextRank = "";
    private int devotionPoints = -1, devotionWeekUsed = 0, devotionWeekCap = 0, devotionToNext = 0;
    private int devotionStreak = 0, devotionBestStreak = 0, devotionBrokeFrom = 0, devotionFreezes = 0;
    private boolean devotionAvailable = false, devotionAtRisk = false;
    private JSONArray devotionClaims = null;

    /** Lazily parsed; the resource ships with the app so this cannot fail at
     *  runtime for any reason a retry would fix. */
    private VenerationTasks catalogue() {
        if (catalogue != null) return catalogue;
        try (java.io.InputStream in = getResources().openRawResource(
                getResources().getIdentifier("veneration_tasks", "raw", getPackageName()))) {
            java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
            byte[] buf = new byte[8192];
            int n;
            while ((n = in.read(buf)) > 0) out.write(buf, 0, n);
            catalogue = VenerationTasks.parse(new String(out.toByteArray(), "UTF-8"));
        } catch (Exception e) {
            android.util.Log.e("BunnyTasker", "veneration catalogue unreadable", e);
        }
        return catalogue;
    }

    private void applyDevotionStatus(JSONObject resp) {
        if (resp == null || !resp.has("devotion_points")) return;
        devotionPoints = resp.optInt("devotion_points", 0);
        devotionRank = resp.optString("devotion_rank", "");
        devotionNextRank = resp.optString("devotion_next_rank", "");
        devotionToNext = resp.optInt("devotion_to_next", 0);
        devotionWeekUsed = resp.optInt("devotion_week_used", 0);
        devotionWeekCap = resp.optInt("devotion_week_cap", 0);
        devotionAvailable = resp.optBoolean("devotion_available", false);
        devotionStreak = resp.optInt("devotion_streak", 0);
        devotionBestStreak = resp.optInt("devotion_best_streak", 0);
        devotionBrokeFrom = resp.optInt("devotion_broke_from", 0);
        devotionFreezes = resp.optInt("devotion_freezes", 0);
        devotionAtRisk = resp.optBoolean("devotion_streak_at_risk", false);
        devotionClaims = resp.optJSONArray("devotion_claims");
    }

    private String weeks(int n) {
        return n + (n == 1 ? " week" : " weeks");
    }

    /** The streak line, and what it says when it ends.
     *
     *  Deliberately never a bare "0". A zero counter after a long run reads as
     *  a quit moment rather than a restart — the documented response is shame
     *  and abandonment — so a break names what ended and invites the next one
     *  instead of displaying a hole where the number was. UI thread. */
    private void renderDevotionStreak() {
        TextView t = (TextView) findViewById(fid("devotion_streak_text"));
        if (t == null) return;
        StringBuilder s = new StringBuilder();
        int colour = 0xFF8a7a9a;
        if (devotionStreak > 0) {
            s.append(weeks(devotionStreak)).append(" running");
            if (devotionAtRisk) {
                s.append(" \u00b7 nothing offered this week yet");
                colour = 0xFFffaa66;
            } else {
                colour = 0xFF66aa66;
            }
        } else if (devotionBrokeFrom > 0) {
            s.append("A run of ").append(weeks(devotionBrokeFrom)).append(" ended. Start another.");
        } else {
            s.append("No run yet.");
        }
        if (devotionBestStreak > devotionStreak && devotionBestStreak > 0) {
            s.append("  \u00b7  best ").append(weeks(devotionBestStreak));
        }
        // Freezes are granted, never bought and never earned, so they are
        // stated plainly rather than dangled.
        if (devotionFreezes > 0) {
            s.append("\n").append(devotionFreezes == 1 ? "1 missed week covered" : devotionFreezes + " missed weeks covered");
        }
        t.setText(s.toString());
        t.setTextColor(colour);
    }

    /** What the Lion actually said. UI thread. */
    private void renderCommendations() {
        LinearLayout box = (LinearLayout) findViewById(fid("devotion_commends"));
        if (box == null) return;
        box.removeAllViews();
        if (devotionClaims == null) return;
        int shown = 0;
        for (int i = 0; i < devotionClaims.length() && shown < 3; i++) {
            JSONObject c = devotionClaims.optJSONObject(i);
            if (c == null || !c.optBoolean("commended", false)) continue;
            String note = c.optString("note", "");
            TextView tv = new TextView(this);
            tv.setText(note.isEmpty()
                ? "\u2713 Your Lion marked one of these seen."
                : "\u201c" + note + "\u201d");
            tv.setTextColor(0xFFc8a84e);
            tv.setTextSize(12);
            tv.setPadding(0, 3, 0, 3);
            box.addView(tv);
            shown++;
        }
    }

    /** UI thread. */
    private void refreshDevotion() {
        if (devotionPoints < 0) {
            show("section_devotion", false);  // relay has not answered yet
            return;
        }
        show("section_devotion", true);
        // Goal gradient: the next rung and the distance to it move people more
        // than the total behind them does.
        String rankLine = devotionRank + "  \u00b7  " + devotionPoints
            + (devotionPoints == 1 ? " point" : " points");
        if (!devotionNextRank.isEmpty() && devotionToNext > 0) {
            rankLine += "  \u00b7  " + devotionToNext + " to " + devotionNextRank;
        }
        setText("devotion_rank_text", rankLine);
        String sub;
        if (devotionWeekCap == 0) {
            sub = "A subscription opens this. Bronze 3/week, Silver 7, Gold unlimited.";
        } else if (devotionWeekCap < 0) {
            sub = devotionWeekUsed + " offered this week \u00b7 unlimited";
        } else {
            int left = Math.max(0, devotionWeekCap - devotionWeekUsed);
            sub = left + " of " + devotionWeekCap + " left this week";
        }
        setText("devotion_sub_text", sub);
        Button b = (Button) findViewById(fid("btn_devotion"));
        if (b != null) {
            b.setEnabled(devotionAvailable);
            b.setText(devotionWeekCap == 0 ? "Subscribers Only"
                : devotionAvailable ? "Offer Devotion" : "Nothing Left This Week");
        }
        renderDevotionStreak();
        renderCommendations();
    }

    /** Draw a task, then make them type it. */
    private void doDevotion() {
        VenerationTasks cat = catalogue();
        if (cat == null) {
            statusText.setText("Task catalogue unavailable");
            return;
        }
        java.util.List<VenerationTasks.Category> cats = cat.categories();
        final String[] labels = new String[cats.size() + 1];
        final String[] keys = new String[cats.size() + 1];
        labels[0] = "Anything";
        keys[0] = VenerationTasks.ANY;
        for (int i = 0; i < cats.size(); i++) {
            labels[i + 1] = cats.get(i).title;
            keys[i + 1] = cats.get(i).key;
        }
        new android.app.AlertDialog.Builder(this)
            .setTitle("Offer what?")
            .setItems(labels, (d, which) -> showDevotionTask(keys[which]))
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void showDevotionTask(String categoryKey) {
        VenerationTasks cat = catalogue();
        if (cat == null) return;
        final VenerationTasks.Task task = cat.draw(categoryKey, new java.util.Random());
        if (task == null) {
            statusText.setText("Nothing in that category");
            return;
        }

        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 24, 48, 24);

        TextView prompt = new TextView(this);
        prompt.setText(task.text);
        prompt.setTextColor(0xFFe0d0f0);
        prompt.setTextSize(15);
        prompt.setPadding(0, 0, 0, 16);
        layout.addView(prompt);

        final EditText input = new EditText(this);
        input.setHint("Type it exactly");
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT
            | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        input.setMinLines(2);
        // Typing is the task. Pasting it is not doing it — the desktop collar
        // takes the same line on enforced veneration, and blocks unconditionally
        // there. Here there is no penalty to apply (this is voluntary), so the
        // paste is simply refused rather than charged.
        input.setCustomSelectionActionModeCallback(new android.view.ActionMode.Callback() {
            public boolean onCreateActionMode(android.view.ActionMode m, android.view.Menu menu) { return false; }
            public boolean onPrepareActionMode(android.view.ActionMode m, android.view.Menu menu) { return false; }
            public boolean onActionItemClicked(android.view.ActionMode m, android.view.MenuItem i) { return false; }
            public void onDestroyActionMode(android.view.ActionMode m) { }
        });
        input.setLongClickable(false);
        input.setTextIsSelectable(false);
        layout.addView(input);

        final TextView note = new TextView(this);
        note.setText("Type it. Pasting is not typing.");
        note.setTextColor(0xFF5a4a6a);
        note.setTextSize(10);
        note.setPadding(0, 12, 0, 0);
        layout.addView(note);

        android.app.AlertDialog dlg = new android.app.AlertDialog.Builder(this)
            .setTitle("Offer devotion")
            .setView(layout)
            .setPositiveButton("Offer", null)   // wired below so it can refuse
            .setNeutralButton("Another", (d, w) -> showDevotionTask(categoryKey))
            .setNegativeButton("Cancel", null)
            .create();
        dlg.setOnShowListener(dd -> dlg.getButton(android.app.AlertDialog.BUTTON_POSITIVE)
            .setOnClickListener(v -> {
                String typed = input.getText().toString().trim();
                // Exact match, capitals included. The catalogue strings are the
                // enforced form everywhere else in this system; accepting a
                // near-miss here would make devotion the one place Their
                // pronouns are optional.
                if (!typed.equals(task.text.trim())) {
                    note.setText("Not exactly it \u2014 capitals and punctuation count.");
                    note.setTextColor(0xFFcc4444);
                    return;
                }
                dlg.dismiss();
                executor.execute(() -> postDevotion(task.id));
            }));
        dlg.show();
    }

    /** Blocking — call from an executor thread. */
    private void postDevotion(String taskId) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) {
            handler.post(() -> statusText.setText("Mesh not configured"));
            return;
        }
        long ts = System.currentTimeMillis();
        String signature = PairingManager.sign(getContentResolver(),
            meshId + "|" + nodeId + "|devotion|" + taskId + "|" + ts);
        if (signature == null || signature.isEmpty()) {
            handler.post(() -> statusText.setText("Sign failed \u2014 pairing key missing"));
            return;
        }
        try {
            JSONObject body = new JSONObject();
            body.put("node_id", nodeId);
            body.put("task_id", taskId);
            body.put("ts", ts);
            body.put("signature", signature);
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/devotion");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            java.io.InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            StringBuilder sb = new StringBuilder();
            if (is != null) {
                BufferedReader r = new BufferedReader(new InputStreamReader(is));
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
            }
            conn.disconnect();
            JSONObject resp = sb.length() > 0 ? new JSONObject(sb.toString()) : new JSONObject();
            applyDevotionStatus(resp);
            final boolean ok = code == 200 && resp.optBoolean("ok", false);
            final String err = resp.optString("error", "HTTP " + code);
            handler.post(() -> {
                statusText.setText(ok ? "Offered. " + devotionRank + ", " + devotionPoints + "." : err);
                refreshDevotion();
            });
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "devotion post failed", e);
            handler.post(() -> statusText.setText("Could not reach the relay"));
        }
    }

    // ── Live state the Collar tracks and this app never showed ──
    //
    // Every value below already existed in Settings.Global, written by the
    // Collar or projected there by the relay. A sweep of the keys the Collar
    // writes against the keys this app reads turned up 83 it never touched —
    // the same class of gap as the STREAK tile that read a pref nobody wrote.
    // These are the ones the bunny is actually being held to.
    //
    // Every section hides when its feature is off, so the tab shows what is
    // being enforced rather than a menu of dormant subsystems.

    private int gint(String key) {
        return Settings.Global.getInt(getContentResolver(), key, 0);
    }

    private long glong(String key) {
        return Settings.Global.getLong(getContentResolver(), key, 0L);
    }

    private void show(String sectionId, boolean visible) {
        View v = findViewById(fid(sectionId));
        if (v != null) v.setVisibility(visible ? View.VISIBLE : View.GONE);
    }

    private void setText(String viewId, String text) {
        TextView t = (TextView) findViewById(fid(viewId));
        if (t != null) t.setText(text);
    }

    /** The cage ceiling, and how much of it the Lion has given back.
     *
     *  Read-only on this side. The ceiling lives in the Collar's app-private
     *  SharedPreferences — the one store the Lion's ADB bridge cannot write —
     *  which is exactly what makes "They can loosen but never tighten"
     *  enforceable. Letting this app write it, or routing a request through
     *  Settings.Global for the Collar to pick up, would hand that capability
     *  straight back to a `settings put global`. So the button launches the
     *  Collar's own screen and the write happens over there. UI thread. */
    private void refreshCage() {
        TextView t = (TextView) findViewById(fid("cage_text"));
        View btn = findViewById(fid("btn_tighten"));
        int ceiling = gint("focus_lock_cage_ceiling");
        int effective = gint("focus_lock_cage_level_effective");
        int lionReq = Settings.Global.getInt(getContentResolver(), "focus_lock_cage_level_lion", -1);
        boolean collared = isCollarInstalled();
        show("section_cage", collared);
        if (t == null) return;
        StringBuilder s = new StringBuilder();
        s.append("Your ceiling: ").append(cageName(ceiling));
        if (lionReq >= 0 && effective < ceiling) {
            s.append("\nYour Lion has loosened it to ").append(cageName(effective))
             .append(" \u2014 They can put it back to your ceiling, never past it.");
        } else {
            s.append("\nIn force: ").append(cageName(effective));
        }
        if (ceiling >= 2) {
            s.append("\nSealed is the tightest there is.");
        }
        t.setText(s.toString());
        if (btn != null) btn.setEnabled(ceiling < 2);
    }

    private String cageName(int level) {
        return level >= 2 ? "Sealed" : level == 1 ? "Collar" : "Leash";
    }

    private boolean isCollarInstalled() {
        try {
            getPackageManager().getPackageInfo("com.focuslock", 0);
            return true;
        } catch (Exception e) {
            return false;
        }
    }

    /** Hand off to the Collar's own screen, which owns the boundary. */
    private void doTighten() {
        try {
            android.content.Intent i = new android.content.Intent();
            i.setComponent(new android.content.ComponentName(
                "com.focuslock", "com.focuslock.TightenActivity"));
            i.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(i);
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "tighten launch failed", e);
            statusText.setText("Could not open the Collar");
        }
    }

    /** What the lock is actually doing to the phone right now.
     *
     *  Nine modes and a stack of modifiers, and the bunny's own app never said
     *  which were switched on — so "why is my screen dim" or "why did that
     *  vibrate" had no answer on the device it was happening to. */
    private void refreshModifiers() {
        StringBuilder s = new StringBuilder();
        if (gint("focus_lock_active") == 1) {
            String mode = gstr("focus_lock_mode");
            if (!mode.isEmpty()) s.append(mode.substring(0, 1).toUpperCase()).append(mode.substring(1)).append(" lock");
        }
        java.util.List<String> on = new java.util.ArrayList<>();
        if (gint("focus_lock_shame") == 1) on.add("shame");
        if (gint("focus_lock_dim") == 1) on.add("dimmed");
        if (gint("focus_lock_mute") == 1) on.add("muted");
        if (gint("focus_lock_vibrate") == 1) on.add("vibrate");
        if (gint("focus_lock_penalty") == 1) on.add("penalties");
        if (gint("focus_lock_lovense_available") == 1) on.add("toy connected");
        if (!on.isEmpty()) {
            if (s.length() > 0) s.append("\n");
            s.append(String.join(" \u00b7 ", on));
        }
        // A geofence breach is a thing that HAPPENED, so it stays on screen
        // afterwards; the stats tile only ever said whether a fence exists.
        long breach = glong("focus_lock_geofence_breach_at");
        if (breach > 0) {
            long agoH = (System.currentTimeMillis() - breach) / 3600000L;
            if (agoH < 72) {
                if (s.length() > 0) s.append("\n");
                s.append("Geofence breached ").append(agoH < 1 ? "under an hour ago" : agoH + "h ago");
            }
        }
        boolean any = s.length() > 0;
        show("section_modifiers", any);
        if (any) setText("modifiers_text", s.toString());
    }

    /** Tamper the system noticed.
     *
     *  Consent runs both ways: if device admin came off and the Collar logged
     *  it, the bunny should see that it was seen rather than find out from a
     *  friction re-lock they cannot explain. Admin tamper is costly-exit by
     *  design — no financial penalty, a re-lock and a note to the Lion — and
     *  saying so plainly is part of that being honest rather than a trap. */
    private void refreshTamper() {
        boolean adminTamper = gint("focus_lock_admin_tamper") == 1;
        boolean adminRemoved = gint("focus_lock_admin_removed") == 1;
        boolean btRemoved = gint("focus_lock_bt_admin_removed") == 1;
        if (!adminTamper && !adminRemoved && !btRemoved) {
            show("section_tamper", false);
            return;
        }
        java.util.List<String> what = new java.util.ArrayList<>();
        if (adminRemoved) what.add("the Collar's device admin was removed");
        if (btRemoved) what.add("Bunny Tasker's device admin was removed");
        if (adminTamper && what.isEmpty()) what.add("a device-admin change");
        show("section_tamper", true);
        setText("tamper_text", "Your Lion has been told " + String.join(", and ", what)
            + ".\nNo charge for it \u2014 it re-locks, that is all.");
    }

    /** Everything on the Now tab that is derived from local state. Cheap
     *  (Settings.Global reads + arithmetic), so it runs on the poller while
     *  that tab is open. UI thread. */
    private void refreshLiveState() {
        refreshScreenTime();
        refreshActiveTask();
        refreshCountdown();
        refreshSchedule();
        refreshBodyCheck();
        refreshOffer();
        refreshModifiers();
        refreshTamper();
        refreshCage();
        refreshDevotion();
    }

    /** How much of today is left.
     *
     *  The Collar accumulates `screen_time_used_today` in minutes whenever the
     *  phone is UNLOCKED and auto-locks at `screen_time_quota_minutes` — it
     *  even reports both in its own state JSON. The bunny's app never showed
     *  the one number they are being measured against, so the leash was
     *  invisible right up to the moment it pulled. */
    private void refreshScreenTime() {
        int quota = gint("focus_lock_screen_time_quota_minutes");
        if (quota <= 0) {
            show("section_screentime", false);
            return;
        }
        int used = Math.max(0, gint("focus_lock_screen_time_used_today"));
        int left = Math.max(0, quota - used);
        // The reset hour matters most exactly when the quota is spent, which
        // is when "until reset" would otherwise mean nothing.
        int resetHour = gint("focus_lock_screen_time_reset_hour");
        String resetAt = " \u00b7 resets " + hh(resetHour);
        show("section_screentime", true);
        setText("screentime_text", left > 0
            ? left + " min left of " + quota + " today" + resetAt
            : "Quota spent \u2014 locked until" + resetAt.replace(" \u00b7 resets", ""));
        TextView t = (TextView) findViewById(fid("screentime_text"));
        if (t != null) t.setTextColor(left == 0 ? 0xFFcc4444 : left <= quota / 5 ? 0xFFffaa66 : 0xFFcc99ee);
        android.widget.ProgressBar bar =
            (android.widget.ProgressBar) findViewById(fid("screentime_bar"));
        if (bar != null) bar.setProgress(Math.min(100, (int) (used * 100L / quota)));
    }

    /** The unlock condition, in the app that has to satisfy it.
     *
     *  Nine lock modes exist and only the deadline task was ever surfaced
     *  here: the bunny could see THAT they were locked and not what would
     *  end it. task_text/reps/done cover task + exercise modes, and the
     *  mode-specific keys cover the rest. */
    private void refreshActiveTask() {
        if (gint("focus_lock_active") != 1) {
            show("section_task", false);
            return;
        }
        String mode = gstr("focus_lock_mode");
        String task = gstr("focus_lock_task_text");
        if (task.isEmpty()) task = gstr("focus_lock_photo_task");
        if (task.isEmpty()) task = gstr("focus_lock_exercise");
        if (task.isEmpty()) {
            String c = gstr("focus_lock_compliment");
            if (!c.isEmpty()) task = "Say it, and mean it: " + c;
        }
        if (task.isEmpty()) {
            // Basic/timer locks have no condition to state; the countdown and
            // balance already say what ends them.
            show("section_task", false);
            return;
        }
        show("section_task", true);
        setText("task_text", task);

        StringBuilder sub = new StringBuilder();
        int reps = gint("focus_lock_task_reps");
        int done = gint("focus_lock_task_done");
        if (reps > 0) sub.append(done).append(" of ").append(reps).append(" done");
        String hint = gstr("focus_lock_photo_hint");
        if (!hint.isEmpty()) {
            if (sub.length() > 0) sub.append("  \u00b7  ");
            sub.append(hint);
        }
        if (sub.length() == 0 && !mode.isEmpty()) sub.append(mode).append(" lock");
        setText("task_progress", sub.toString());
    }

    /** A lock is scheduled and nothing said so.
     *
     *  `countdown_lock_at` is set when the Lion arms a delayed lock; the
     *  Collar warns at tiers as it approaches and then locks. The warnings
     *  went to a notification the bunny may have dismissed — this is the
     *  standing version. */
    private void refreshCountdown() {
        long at = glong("focus_lock_countdown_lock_at");
        long now = System.currentTimeMillis();
        if (at <= 0 || at <= now) {
            show("section_countdown", false);
            return;
        }
        long left = at - now;
        long h = left / 3600000L;
        long m = (left % 3600000L) / 60000L;
        String when = h > 0 ? h + "h " + m + "m" : m + "m";
        String msg = gstr("focus_lock_countdown_message");
        show("section_countdown", true);
        setText("countdown_text", "Locking in " + when + (msg.isEmpty() ? "" : " \u2014 " + msg));
    }

    /** Bedtime and curfew, so the bunny knows when they turn into a pumpkin.
     *
     *  Both are hour-of-day windows the Collar enforces on its own poll:
     *  bedtime locks the phone, curfew drops a geofence around wherever they
     *  are (or a configured point). Neither was visible from this side. */
    private void refreshSchedule() {
        boolean bed = gint("focus_lock_bedtime_enabled") == 1;
        boolean cur = gint("focus_lock_curfew_enabled") == 1;
        if (!bed && !cur) {
            show("section_schedule", false);
            return;
        }
        StringBuilder s = new StringBuilder();
        if (bed) {
            int lh = Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_lock_hour", -1);
            int uh = Settings.Global.getInt(getContentResolver(), "focus_lock_bedtime_unlock_hour", -1);
            boolean locked = gint("focus_lock_bedtime_locked") == 1;
            if (lh >= 0 && uh >= 0) {
                s.append("Bedtime ").append(hh(lh)).append("\u2013").append(hh(uh));
                if (locked) s.append(" \u00b7 active now");
            }
        }
        if (cur) {
            int ch = Settings.Global.getInt(getContentResolver(), "focus_lock_curfew_confine_hour", -1);
            int rh = Settings.Global.getInt(getContentResolver(), "focus_lock_curfew_release_hour", -1);
            if (ch >= 0 && rh >= 0) {
                if (s.length() > 0) s.append("\n");
                s.append("Curfew ").append(hh(ch)).append("\u2013").append(hh(rh));
                if (!gstr("focus_lock_geofence_lat").isEmpty()) s.append(" \u00b7 confined now");
            }
        }
        if (s.length() == 0) {
            show("section_schedule", false);
            return;
        }
        show("section_schedule", true);
        setText("schedule_text", s.toString());
    }

    private String hh(int hour) {
        return (hour < 10 ? "0" : "") + hour + ":00";
    }

    /** Body check: its own cadence, its own streak, invisible here until now. */
    private void refreshBodyCheck() {
        if (gint("focus_lock_body_check_active") != 1) {
            show("section_bodycheck", false);
            return;
        }
        int intervalH = gint("focus_lock_body_check_interval_h");
        long last = glong("focus_lock_body_check_last");
        int streak = gint("focus_lock_body_check_streak");
        String area = gstr("focus_lock_body_check_area");
        String result = gstr("focus_lock_body_check_last_result");
        StringBuilder s = new StringBuilder();
        s.append(area.isEmpty() ? "Body" : area);
        if (intervalH > 0) s.append(" \u00b7 every ").append(intervalH).append("h");
        if (last > 0 && intervalH > 0) {
            long due = last + intervalH * 3600000L;
            long left = due - System.currentTimeMillis();
            s.append(left > 0 ? "\nNext in " + (left / 3600000L) + "h" + ((left % 3600000L) / 60000L) + "m" : "\nDue now");
        }
        if (streak > 0) s.append("\nStreak: ").append(streak);
        if (!result.isEmpty()) s.append(" \u00b7 last: ").append(result);
        show("section_bodycheck", true);
        setText("bodycheck_text", s.toString());
    }

    /** Negotiation, from the side that does the negotiating.
     *
     *  Lion's Share has had accept/decline buttons for offers all along; the
     *  bunny had no way to make one. The Collar's doOffer sets exactly these
     *  three keys, and its 60-second minimum before an accept is enforced
     *  Collar-side, so writing them here is the same act by a different door. */
    private void refreshOffer() {
        String offer = gstr("focus_lock_offer");
        String status = gstr("focus_lock_offer_status");
        boolean locked = gint("focus_lock_active") == 1;
        if (offer.isEmpty() && !locked) {
            show("section_offer", false);
            return;
        }
        show("section_offer", true);
        Button b = (Button) findViewById(fid("btn_make_offer"));
        if (offer.isEmpty()) {
            setText("offer_text", "Nothing on the table. You can put something there.");
            if (b != null) { b.setEnabled(true); b.setText("Make an Offer"); }
            return;
        }
        String pretty = "pending".equals(status) ? "Waiting on your Lion"
            : "accepted".equals(status) ? "Accepted"
            : "declined".equals(status) ? "Declined"
            : status;
        String response = gstr("focus_lock_offer_response");
        setText("offer_text", "\u201c" + offer + "\u201d\n" + pretty
            + (response.isEmpty() ? "" : " \u2014 " + response));
        if (b != null) {
            boolean pending = "pending".equals(status);
            b.setEnabled(!pending);
            b.setText(pending ? "Offer Pending" : "Make Another Offer");
        }
    }

    private void doMakeOffer() {
        final EditText input = new EditText(this);
        input.setHint("What are you offering?");
        input.setInputType(android.text.InputType.TYPE_CLASS_TEXT
            | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        input.setMinLines(2);
        new android.app.AlertDialog.Builder(this)
            .setTitle("Make an offer")
            .setMessage("Your Lion decides. An accepted offer cannot be acted on for "
                + "60 seconds after you make it.")
            .setView(input)
            .setPositiveButton("Offer", (d, w) -> {
                String text = input.getText().toString().trim();
                if (text.isEmpty()) return;
                if (text.length() > 300) text = text.substring(0, 300);
                try {
                    Settings.Global.putString(getContentResolver(), "focus_lock_offer", text);
                    Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "pending");
                    Settings.Global.putLong(getContentResolver(), "focus_lock_offer_time",
                        System.currentTimeMillis());
                    statusText.setText("Offer sent");
                } catch (Exception e) {
                    android.util.Log.w("BunnyTasker", "offer write failed", e);
                    statusText.setText("Could not send the offer");
                }
                refreshOffer();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** What is scheduled to grow the balance next.
     *
     *  Fines and tributes are recurring server-side charges; sub_total_owed is
     *  what the subscription has run up. The bunny could see the balance and
     *  never what was queued to raise it. UI thread. */
    private void refreshCharges() {
        int fineActive = gint("focus_lock_fine_active");
        int fineAmt = gint("focus_lock_fine_amount");
        int fineIntervalM = gint("focus_lock_fine_interval_m");
        long subOwed = glong("focus_lock_sub_total_owed");
        String tier = gstr("focus_lock_sub_tier");
        long subDue = glong("focus_lock_sub_due");

        StringBuilder s = new StringBuilder();
        if (fineActive == 1 && fineAmt > 0) {
            s.append("Fine: $").append(fineAmt);
            if (fineIntervalM > 0) {
                s.append(fineIntervalM % 60 == 0
                    ? " every " + (fineIntervalM / 60) + "h"
                    : " every " + fineIntervalM + "m");
            }
            long lastFine = glong("focus_lock_fine_last_applied");
            if (lastFine > 0 && fineIntervalM > 0) {
                long next = lastFine + fineIntervalM * 60000L - System.currentTimeMillis();
                if (next > 0) s.append(" \u00b7 next in ").append(Math.max(1, next / 60000L)).append("m");
            }
        }
        if (!tier.isEmpty()) {
            if (s.length() > 0) s.append("\n");
            int amt = "bronze".equals(tier) ? 25 : "silver".equals(tier) ? 35 : 50;
            s.append(tier.toUpperCase()).append(": $").append(amt).append("/wk");
            if (subDue > 0) {
                long left = subDue - System.currentTimeMillis();
                s.append(left > 0
                    ? " \u00b7 next in " + Math.max(1, left / 86400000L) + "d"
                    : " \u00b7 due now");
            }
        }
        if (subOwed > 0) {
            if (s.length() > 0) s.append("\n");
            s.append("Subscription has cost you $").append(subOwed).append(" so far.");
        }
        boolean any = s.length() > 0;
        show("section_charges", any);
        if (any) setText("charges_text", s.toString());
    }

    /** Which of their own machines are collared, and which are locked. */
    private void refreshDesktops() {
        String desktops = gstr("focus_lock_desktops");
        String lockedDevices = gstr("focus_lock_desktop_locked_devices");
        boolean anyLocked = gint("focus_lock_desktop_active") == 1;
        if (desktops.isEmpty() && !anyLocked) {
            show("section_desktops", false);
            return;
        }
        StringBuilder s = new StringBuilder();
        if (!desktops.isEmpty()) s.append(desktops.replace(",", ", "));
        else s.append("Collared");
        if (anyLocked) {
            s.append("\nLocked");
            if (!lockedDevices.isEmpty()) s.append(": ").append(lockedDevices.replace(",", ", "));
        } else {
            s.append("\nUnlocked");
        }
        String dmsg = gstr("focus_lock_desktop_message");
        if (!dmsg.isEmpty()) s.append("\n\u201c").append(dmsg).append("\u201d");
        show("section_desktops", true);
        setText("desktops_text", s.toString());
    }

    /** Show one page, style its tab, and refresh what that page shows.
     *
     *  Messages already refresh on their own 10s cadence, so Talk needs no
     *  kick; Owe pulls the ledger because the sparkline and history are only
     *  worth a round-trip when someone is looking at them. */
    private void selectTab(int index) {
        if (pageNow == null) return;  // pre-inflate call, or an older layout
        if (index < 0 || index > 3) index = 0;
        currentTab = index;
        View[] pages = {pageNow, pageOwe, pageTalk, pageMe};
        Button[] tabs = {tabNow, tabOwe, tabTalk, tabMe};
        for (int i = 0; i < pages.length; i++) {
            if (pages[i] != null) pages[i].setVisibility(i == index ? View.VISIBLE : View.GONE);
            if (tabs[i] == null) continue;
            tabs[i].setBackgroundTintList(android.content.res.ColorStateList.valueOf(
                i == index ? 0xFF241a33 : 0xFF0e0c16));
            tabs[i].setTextColor(i == index ? 0xFFcc99ee : 0xFF555555);
        }
        prefs.edit().putInt("last_tab", index).apply();
        if (index == 0) {
            refreshLiveState();
        } else if (index == 1) {
            refreshCostToWait();
            refreshCharges();
            renderGambleBudget();
            refreshPaymentHistory();  // already hops to the executor itself
        } else if (index == 3) {
            refreshDesktops();
        }
    }

    /** What waiting costs, in the only unit that matters.
     *
     *  Every input is already on the device — `paywall`, `paywall_original`,
     *  `sub_tier`, `locked_at` — and the relay's compound-interest tick uses
     *  exactly this arithmetic (compounded = paywall_original * rate**hours,
     *  applied when it exceeds the current balance). It was simply never put
     *  in front of the bunny as a number they could act on: the balance said
     *  what they owed now and nothing said what it becomes by tomorrow.
     *
     *  Silent when there is no balance, or when the tier earns no interest —
     *  a line that always says "+$0" trains people to stop reading it.
     *
     *  RATES ARE NOT FREE-CHOSEN HERE. They mirror COMPOUND_INTEREST_RATE_BY_TIER
     *  in shared/focuslock_penalties.py, which is what the relay's
     *  check_compound_interest() actually charges. Bronze read 1.08 here from
     *  the day this shipped while the relay charged 1.10, so the one screen
     *  that exists to tell a bunny what waiting costs quoted them low — on the
     *  tier most likely to be carrying a balance. tests/test_android_conformance.py
     *  now fails if these drift apart again. */
    private void refreshCostToWait() {
        if (costToWait == null) return;
        double pw = parseD(gstr("focus_lock_paywall"));
        double orig = parseD(gstr("focus_lock_paywall_original"));
        long lockedAt = Settings.Global.getLong(getContentResolver(), "focus_lock_locked_at", 0L);
        String tier = gstr("focus_lock_sub_tier").toLowerCase();
        double rate = "gold".equals(tier) ? 1.00 : "silver".equals(tier) ? 1.05 : "bronze".equals(tier) ? 1.10 : 1.10;
        if (pw <= 0 || orig <= 0 || lockedAt <= 0 || rate <= 1.0) {
            costToWait.setVisibility(View.GONE);
            return;
        }
        double hours = (System.currentTimeMillis() - lockedAt) / 3600000.0;
        if (hours < 0) hours = 0;
        double in24 = Math.floor(orig * Math.pow(rate, hours + 24));
        double delta = in24 - pw;
        if (delta < 1) {
            costToWait.setVisibility(View.GONE);
            return;
        }
        costToWait.setText("$" + (long) pw + " clears it today. Leave it 24h and it is $"
            + (long) in24 + " \u2014 " + Math.round((rate - 1) * 100) + "%/hr adds $" + (long) delta + ".");
        costToWait.setVisibility(View.VISIBLE);
    }

    private double parseD(String s) {
        try {
            return (s == null || s.isEmpty()) ? 0d : Double.parseDouble(s);
        } catch (Exception e) {
            return 0d;
        }
    }

    /** Double or nothing, bunny-signed straight to the relay.
     *
     *  The endpoint has always been bunny-authed (payload
     *  mesh|node|gamble|ts, verified against bunny_pubkey), but the only
     *  button anywhere was in Lion's Share, which drives it through the
     *  Collar's local /api/gamble. So the bunny could be made to flip and
     *  could not choose to.
     *
     *  Heads halves the balance, tails doubles it: +25% EV to the Lion per
     *  flip. That edge is only meaningful if the number of flips is bounded
     *  — over enough attempts variance clears any balance, which would turn
     *  a Lion-favourable bet into an escape hatch — so the relay enforces a
     *  cooldown and a daily cap and answers 429 with how long is left. The
     *  client mirrors that state to grey the button out, but never decides
     *  it. */
    private void doGamble() {
        double pw = parseD(gstr("focus_lock_paywall"));
        if (pw <= 0) {
            statusText.setText("Nothing to gamble");
            return;
        }
        new android.app.AlertDialog.Builder(this)
            .setTitle("Flip for it?")
            .setMessage("Heads: your $" + (long) pw + " becomes $" + (long) Math.ceil(pw / 2) + ".\n"
                + "Tails: it becomes $" + (long) (pw * 2) + ".\n\n"
                + "Even odds. Your Lion keeps the edge.")
            .setPositiveButton("FLIP", (d, w) -> executor.execute(this::postGamble))
            .setNegativeButton("Keep my balance", null)
            .show();
    }

    /** Blocking — call from an executor thread. */
    private void postGamble() {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) {
            handler.post(() -> statusText.setText("Mesh not configured"));
            return;
        }
        long ts = System.currentTimeMillis();
        String signature = PairingManager.sign(getContentResolver(), meshId + "|" + nodeId + "|gamble|" + ts);
        if (signature == null || signature.isEmpty()) {
            handler.post(() -> statusText.setText("Sign failed \u2014 pairing key missing"));
            return;
        }
        handler.post(() -> {
            if (gambleStatus != null) gambleStatus.setText("Flipping\u2026");
            if (btnGamble != null) btnGamble.setEnabled(false);
        });
        try {
            JSONObject body = new JSONObject();
            body.put("node_id", nodeId);
            body.put("ts", ts);
            body.put("signature", signature);
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/gamble");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            java.io.InputStream is = code >= 400 ? conn.getErrorStream() : conn.getInputStream();
            StringBuilder sb = new StringBuilder();
            if (is != null) {
                BufferedReader r = new BufferedReader(new InputStreamReader(is));
                String line;
                while ((line = r.readLine()) != null) sb.append(line);
                r.close();
            }
            conn.disconnect();
            JSONObject resp = sb.length() > 0 ? new JSONObject(sb.toString()) : new JSONObject();
            applyGambleBudget(resp);

            if (code == 200 && resp.optBoolean("ok", false)) {
                final boolean heads = "heads".equals(resp.optString("result"));
                final int newPw = resp.optInt("new_paywall", 0);
                handler.post(() -> {
                    if (gambleStatus != null) {
                        gambleStatus.setText(heads
                            ? "\u2713 Heads \u2014 halved to $" + newPw
                            : "\u2717 Tails \u2014 doubled to $" + newPw);
                        gambleStatus.setTextColor(heads ? 0xFF66aa66 : 0xFFcc4444);
                    }
                    statusText.setText(heads ? "Heads. Balance halved." : "Tails. Balance doubled.");
                    renderGambleBudget();
                });
                // The relay wrote the new balance through gamble-resolved; pull
                // it rather than guessing locally.
                refreshStats();
                refreshPaymentHistory();
            } else {
                final String err = resp.optString("error", "HTTP " + code);
                handler.post(() -> {
                    if (gambleStatus != null) {
                        gambleStatus.setText(err);
                        gambleStatus.setTextColor(0xFFaa6644);
                    }
                    renderGambleBudget();
                });
            }
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "gamble failed", e);
            handler.post(() -> {
                if (gambleStatus != null) gambleStatus.setText("Could not reach the relay");
                renderGambleBudget();
            });
        }
    }

    /** Absorb whatever flip budget a response carried. Both /gamble and
     *  /payments report it, so the button knows its state without having to
     *  be refused first. */
    private void applyGambleBudget(JSONObject resp) {
        if (resp == null) return;
        if (resp.has("gamble_cooldown_s")) gambleCooldownS = resp.optInt("gamble_cooldown_s", 0);
        if (resp.has("gamble_remaining_today")) gambleRemainingToday = resp.optInt("gamble_remaining_today", 0);
    }

    /** UI thread. */
    private void renderGambleBudget() {
        if (btnGamble == null) return;
        double pw = parseD(gstr("focus_lock_paywall"));
        if (pw <= 0) {
            btnGamble.setEnabled(false);
            btnGamble.setText("Nothing To Flip For");
            return;
        }
        if (gambleCooldownS > 0) {
            btnGamble.setEnabled(false);
            long m = gambleCooldownS / 60;
            btnGamble.setText(m >= 60 ? "Again in " + (m / 60) + "h" : "Again in " + Math.max(1, m) + "m");
            return;
        }
        if (gambleRemainingToday == 0) {
            btnGamble.setEnabled(false);
            btnGamble.setText("No Flips Left Today");
            return;
        }
        btnGamble.setEnabled(true);
        btnGamble.setText(gambleRemainingToday > 0
            ? "Flip For It (" + gambleRemainingToday + " left)"
            : "Flip For It");
    }

    private void refreshStats() {
        try {
            // Mutual admin monitoring — penalize + alert if Collar admin removed.
            // Suppressed during an authorized teardown (release_authorized), after a
            // terminal release (`released`), and — crucially — when NOT yet paired:
            // an unpaired device has no Lion, so the Collar's admin being absent (or
            // being provisioned before the Lion pairs) is not tamper, and re-locking
            // it would trap the wearer with active=1 and no unlock path.
            String lionPub = Settings.Global.getString(getContentResolver(), "focus_lock_lion_pubkey");
            boolean paired = lionPub != null && !lionPub.isEmpty() && !"null".equals(lionPub);
            long breakglassUntil = Settings.Global.getLong(getContentResolver(), "focus_lock_breakglass_until", 0);
            int releaseAuth = Settings.Global.getInt(getContentResolver(), "focus_lock_release_authorized", 0);
            int released = Settings.Global.getInt(getContentResolver(), "focus_lock_released", 0);
            if (paired && System.currentTimeMillis() > breakglassUntil && releaseAuth == 0 && released == 0) {
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
                            // Full-screen reactivation prompt — mirrors the
                            // Collar-side nag for Bunny Tasker's admin.
                            // launchAdminNag de-dupes on Android's side via a
                            // fixed notif id, and the 0→1 gate above ensures
                            // we only fire once per removal event.
                            launchAdminNag(collarAdmin,
                                "Collar admin was removed. Tap to reactivate — until then, "
                                    + "a $1000 penalty is on the paywall and the Collar can't enforce anything.");
                        }
                    } else {
                        int wasRemoved = Settings.Global.getInt(getContentResolver(), "focus_lock_collar_admin_removed", 0);
                        Settings.Global.putInt(getContentResolver(), "focus_lock_collar_admin_removed", 0);
                        if (wasRemoved == 1) {
                            // Admin restored — cancel the persistent nag.
                            try {
                                getSystemService(android.app.NotificationManager.class).cancel(97);
                            } catch (Exception ignored) {}
                        }
                    }
                } catch (Exception e) {}
            }

            // Mesh sync every 2nd poll (~10s) — pull orders from server.
            // Was every 6th poll (30s) but the user observed ~30s lag on
            // order propagation; matches the Collar's lowered gossip
            // interval. ntfy push (startNtfySubscriber) triggers an
            // immediate sync on top of this for sub-second delivery.
            if (++meshSyncCounter >= 2) {
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
            String geofenceRadius = gstr("focus_lock_geofence_radius_m");

            // Persistent confined notification — visible state for the bunny
            // when Lion has set a geofence. Shows / hides based on whether the
            // geofence is currently set; idempotent on the radius value so we
            // don't re-post (and re-sound, even though channel is silent) on
            // every refresh tick.
            if (!geofenceLat.isEmpty() && !geofenceLon.isEmpty()) {
                showConfinedNotification(geofenceRadius);
            } else {
                clearConfinedNotification();
            }

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
                    sendSignedBunnyWebhook("/webhook/bunny-message", "bunny-message",
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
            // Streak, read from the state that actually tracks one.
            //
            // This tile used to read prefs "streak_days" — a key NOTHING in
            // any of the three apps has ever written, so it rendered "0d" on
            // every device forever. The real streak lives in the Collar's
            // Settings.Global, driven by the relay: `start-streak` stamps
            // streak_start + streak_escapes_at_start, and the server clears
            // streak_enabled via the `streak-break` order the moment lifetime
            // escapes exceed that baseline. Days are derived from the start
            // timestamp rather than counted, so nothing drifts if the app is
            // closed.
            boolean streakOn = Settings.Global.getInt(getContentResolver(), "focus_lock_streak_enabled", 0) == 1;
            long streakStart = Settings.Global.getLong(getContentResolver(), "focus_lock_streak_start", 0L);
            int streakDays = (streakOn && streakStart > 0)
                ? (int) ((System.currentTimeMillis() - streakStart) / 86400000L)
                : 0;
            boolean streak7 = Settings.Global.getInt(getContentResolver(), "focus_lock_streak_7d_claimed", 0) == 1;
            boolean streak30 = Settings.Global.getInt(getContentResolver(), "focus_lock_streak_30d_claimed", 0) == 1;
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
            final boolean fStreakOn = streakOn;
            final boolean fStreak7 = streak7;
            final boolean fStreak30 = streak30;
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
                updateCrownConnectionState();

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
                // A number that only ever goes up is wallpaper. Show the
                // streak as something with a state: running (and how close to
                // the next bonus), or broken and needing the Lion to restart
                // it — which is the half that gives it any weight.
                if (!fStreakOn) {
                    statStreak.setText(fStreak > 0 ? "broken" : "off");
                    statStreak.setTextColor(fStreak > 0 ? 0xFFcc4444 : 0xFF555555);
                } else {
                    int nextMilestone = !fStreak7 ? 7 : !fStreak30 ? 30 : 0;
                    if (nextMilestone > 0 && fStreak < nextMilestone) {
                        statStreak.setText(fStreak + "d \u2192 " + nextMilestone);
                    } else {
                        statStreak.setText(fStreak + "d");
                    }
                    statStreak.setTextColor(0xFF66aa66);
                }
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
                    // If a subscribe / unsubscribe attempt fired in the last 30s
                    // and sub_tier hasn't propagated yet, keep the transient
                    // status (success "syncing..." or failure error) visible
                    // instead of overwriting with the "No active subscription"
                    // default. Without this, the user never sees the failure
                    // message because refreshStats wipes it within 5s.
                    long pendingAt = prefs.getLong("sub_pending_at", 0);
                    String pendingMsg = prefs.getString("sub_pending_msg", "");
                    if (pendingAt > 0 && System.currentTimeMillis() - pendingAt < 30_000
                            && !pendingMsg.isEmpty()) {
                        subStatus.setText(pendingMsg);
                        subStatus.setTextColor(prefs.getInt("sub_pending_color", 0xFFcc4444));
                    } else {
                        subStatus.setText("No active subscription");
                        subStatus.setTextColor(0xFF555555);
                        // Stale pending — clean it up
                        if (pendingAt > 0 && System.currentTimeMillis() - pendingAt >= 30_000) {
                            prefs.edit().remove("sub_pending_at").remove("sub_pending_msg")
                                .remove("sub_pending_color").apply();
                        }
                    }
                    btnFreeUnlock.setVisibility(View.GONE);
                    btnPrepay.setVisibility(View.GONE);
                }

                // Gate features by subscription tier
                boolean hasSub = !fSubTier.isEmpty();
                sectionStats.setVisibility(hasSub ? View.VISIBLE : View.GONE);
                sectionSelflock.setVisibility(hasSub ? View.VISIBLE : View.GONE);
                sectionMessages.setVisibility(hasSub ? View.VISIBLE : View.GONE);
                noSubPrompt.setVisibility(hasSub ? View.GONE : View.VISIBLE);

                // Payment history + messages: refresh every 2nd poll (~10s).
                // Was a flaky `% 25000 < 5000` time-of-day gate that fired ~20%
                // of polls with high variance — sometimes 5s gap, sometimes
                // 50s+ between refreshes, depending on when the activity
                // happened to start. ntfy push (startNtfySubscriber) also
                // triggers an immediate refresh on order updates, so 10s is
                // just the no-push fallback.
                if (++messageRefreshCounter >= 2) {
                    messageRefreshCounter = 0;
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
                    renderPairingQr();
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
                updateCrownConnectionState();
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
                    Settings.Global.putLong(getContentResolver(),
                        "focus_lock_mesh_last_sync_ms", System.currentTimeMillis());
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

    // showPairResetConfirmDialog + doPairReset removed 2026-04-24: the
    // bunny-initiated pair reset was a consensual-design violation —
    // "only Lion or factory reset can remove the Collar" (CLAUDE.md).
    // The visible button was hidden in onCreate; the Collar endpoint
    // at /api/pair-reset now falls through to 403. Half-completed-pair
    // recovery requires reinstalling the Collar, which is gated on the
    // Lion releasing the device admin first.

    /**
     * Render the bunny's pairing QR into the inline qr_code ImageView shown in
     * section_pairing. Same payload Lion's Share scans via the LAN-direct path.
     */
    private void renderPairingQr() {
        if (qrCodeView == null) return;
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
        try {
            String payload = PairingManager.buildQrPayload(getContentResolver(), lanIp, tsIp);
            Bitmap bmp = QrEncoder.encode(payload, 8);
            if (bmp != null) qrCodeView.setImageBitmap(bmp);
            String fp = PairingManager.getFingerprint(getContentResolver());
            if (pairingFingerprint != null && fp != null) {
                pairingFingerprint.setText("Key: " + fp);
            }
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "QR render failed: " + e.getMessage());
        }
    }

    /**
     * Crown is gold when paired AND we've seen the Lion (or a successful
     * mesh poll) recently; gray (desaturated) otherwise. The Collar's
     * vaultSync writes focus_lock_lion_last_seen each time it verifies a
     * Lion-signed blob, which is the authoritative signal on vault-only
     * meshes (BT's plaintext /api/mesh/.../sync returns 410 Gone there).
     * BT's own meshSync still bumps mesh_last_sync_ms when it does work,
     * so we accept either signal within the last 90s.
     */
    private void updateCrownConnectionState() {
        if (connectionCrown == null) return;
        boolean paired = PairingManager.isPaired(getContentResolver());
        long lastSync = Settings.Global.getLong(getContentResolver(), "focus_lock_mesh_last_sync_ms", 0);
        long lastLion = Settings.Global.getLong(getContentResolver(), "focus_lock_lion_last_seen", 0);
        long mostRecent = Math.max(lastSync, lastLion);
        boolean meshOk = (System.currentTimeMillis() - mostRecent) < 90_000L;
        boolean connected = paired && meshOk;
        if (connected) {
            connectionCrown.clearColorFilter();
            connectionCrown.setAlpha(1.0f);
        } else {
            android.graphics.ColorMatrix cm = new android.graphics.ColorMatrix();
            cm.setSaturation(0f);
            connectionCrown.setColorFilter(new android.graphics.ColorMatrixColorFilter(cm));
            connectionCrown.setAlpha(0.6f);
        }
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

        // Build pair payload — same fields Lion's Share expects to parse.
        // PairingManager.buildQrPayload gives us a compact JSON {t,f,l,s,p}.
        String payload = PairingManager.buildQrPayload(getContentResolver(), lanIp, tsIp);
        String jsEscapedPayload = payload.replace("\\", "\\\\").replace("'", "\\'");

        // Text-fallback info shown under the QR so operators without a scanner
        // can still read IP + fingerprint off the screen.
        StringBuilder textInfo = new StringBuilder();
        textInfo.append("<div style='color:#e0e0e0;font-family:sans-serif;font-size:13px;margin-top:16px'>");
        if (!lanIp.isEmpty()) {
            textInfo.append("<b>LAN:</b> ").append(lanIp).append(":8432<br>");
        }
        if (!tsIp.isEmpty()) {
            textInfo.append("<b>Tailscale:</b> ").append(tsIp).append(":8432<br>");
        }
        if (lanIp.isEmpty() && tsIp.isEmpty()) {
            textInfo.append("<span style='color:#e74c3c'>no network detected — check WiFi</span><br>");
        }
        textInfo.append("<b>Fingerprint:</b> ").append(fingerprint).append("<br>");
        textInfo.append("</div>");

        // WebView + inline HTML: loads assets/qrcode.min.js (qrcode-generator
        // 1.4.4, vendored from web/qrcode.min.js), renders at version auto /
        // error-correction M, big enough to scan from a comfortable distance.
        String html = "<!DOCTYPE html><html><head><meta charset='utf-8'>"
            + "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            + "<style>body{margin:0;padding:16px;background:#0a0a10;text-align:center}"
            + "#qr{display:inline-block;background:#fff;padding:12px;border-radius:8px}"
            + "#qr img{width:260px;height:260px;image-rendering:pixelated;display:block}</style>"
            + "</head><body>"
            + "<div id='qr'></div>" + textInfo.toString()
            + "<script src='file:///android_asset/qrcode.min.js'></script>"
            + "<script>(function(){"
            + "  try {"
            + "    var q = qrcode(0, 'M');"
            + "    q.addData('" + jsEscapedPayload + "');"
            + "    q.make();"
            + "    document.getElementById('qr').innerHTML = q.createImgTag(6, 0);"
            + "  } catch (e) {"
            + "    document.getElementById('qr').innerText = 'QR render error: ' + e.message;"
            + "  }"
            + "})();</script>"
            + "</body></html>";

        android.webkit.WebView wv = new android.webkit.WebView(this);
        wv.getSettings().setJavaScriptEnabled(true);
        // file:///android_asset/ access enabled by default on older WebView;
        // explicitly permit for newer Android revisions.
        wv.getSettings().setAllowFileAccess(true);
        wv.setBackgroundColor(0xFF0a0a10);
        wv.loadDataWithBaseURL("file:///android_asset/", html, "text/html", "utf-8", null);

        new android.app.AlertDialog.Builder(this)
            .setTitle("Direct Pair — scan from Lion's Share")
            .setView(wv)
            .setPositiveButton("OK", null)
            .show();
    }

    /** On first run, if the Collar package is installed but not yet active
     *  as device admin, kick the user through ConsentActivity. The Collar
     *  has no launcher icon by design (CLAUDE.md: "invisible app"), so
     *  without this handoff the ToS + device-admin grant would require an
     *  adb command — which isn't acceptable for the production flow where
     *  no cable is present. Safe to re-enter: ConsentActivity is
     *  singleTask + no-ops if consent is already stored. */
    private void maybeLaunchCollarConsent() {
        try {
            android.content.pm.PackageManager pm = getPackageManager();
            pm.getPackageInfo("com.focuslock", 0);
        } catch (android.content.pm.PackageManager.NameNotFoundException e) {
            // Collar not installed — nothing to consent to.
            return;
        }
        try {
            android.content.ComponentName collarAdmin = new android.content.ComponentName(
                "com.focuslock", "com.focuslock.AdminReceiver");
            android.app.admin.DevicePolicyManager dpm = (android.app.admin.DevicePolicyManager)
                getSystemService(DEVICE_POLICY_SERVICE);
            if (dpm != null && dpm.isAdminActive(collarAdmin)) {
                return;  // already set up
            }
            android.content.Intent consent = new android.content.Intent();
            consent.setComponent(new android.content.ComponentName(
                "com.focuslock", "com.focuslock.ConsentActivity"));
            consent.addFlags(android.content.Intent.FLAG_ACTIVITY_NEW_TASK);
            startActivity(consent);
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker",
                "Collar consent launch skipped: " + e.getMessage());
        }
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

        // How the bunny wants to appear to their Lion (shown in the Bunnies list
        // instead of the raw device model). Prefilled with any saved name.
        EditText nameInput = new EditText(this);
        nameInput.setHint("Your name (how your Lion sees you)");
        nameInput.setTextSize(15);
        nameInput.setTextColor(0xFFe0e0e0);
        nameInput.setHintTextColor(0xFF555555);
        nameInput.setBackgroundColor(0xFF111118);
        nameInput.setPadding(24, 18, 24, 18);
        try {
            String savedName = Settings.Global.getString(getContentResolver(), "focus_lock_bunny_name");
            if (savedName != null && !savedName.isEmpty() && !"null".equals(savedName)) nameInput.setText(savedName);
        } catch (Exception ignored) {}
        LinearLayout.LayoutParams nlp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        nlp.topMargin = 12;
        nameInput.setLayoutParams(nlp);
        layout.addView(nameInput);

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
                String name = nameInput.getText().toString().trim();
                if (code.isEmpty()) {
                    statusText.setText("Enter an invite code");
                    return;
                }
                if (server.isEmpty()) {
                    statusText.setText("Enter the server URL");
                    return;
                }
                // Persist the bunny's chosen display name so joinMesh/meshSync send it.
                if (!name.isEmpty()) {
                    try { Settings.Global.putString(getContentResolver(), "focus_lock_bunny_name", name); }
                    catch (Exception ignored) {}
                }
                // Persist vault toggle before kicking off the join
                writeVaultModeFlag(vaultCheckFinal.isChecked());
                statusText.setText("Joining mesh...");
                pairingHint.setText("Connecting to server...");
                final String fServer = server;
                executor.execute(() -> joinMesh(code, fServer));
            })
            .setNeutralButton("Save", (d, w) -> {
                // Persist vault toggle AND the typed display name without re-joining
                // (previously the name was silently discarded here — the "Save"
                // button reported success while dropping the edit).
                writeVaultModeFlag(vaultCheckFinal.isChecked());
                String nm = nameInput.getText().toString().trim();
                if (nm.length() > 40) nm = nm.substring(0, 40);
                try { Settings.Global.putString(getContentResolver(), "focus_lock_bunny_name", nm); }
                catch (Exception ignored) {}
                // If already paired, push it to the relay so the Lion sees it now.
                if (PairingManager.isPaired(getContentResolver())) {
                    final String fnm = nm;
                    executor.execute(() -> setDisplayName(fnm));
                }
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

            // The bunny's self-chosen display name (Issue 4) — shown to the Lion
            // instead of the raw device model.
            String displayName = "";
            try {
                String dn = Settings.Global.getString(getContentResolver(), "focus_lock_bunny_name");
                if (dn != null && !"null".equals(dn)) displayName = dn.trim();
            } catch (Exception ignored) {}

            // Build join request
            JSONObject body = new JSONObject();
            body.put("invite_code", inviteCode);
            body.put("node_id", nodeId);
            body.put("node_type", "phone");
            body.put("bunny_pubkey", bunnyPubKey != null ? bunnyPubKey : "");
            body.put("display_name", displayName);

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
            // node_id FIRST, and it must be the same one we just sent in the join
            // body. The Collar's ControlService starts talking to the relay the
            // moment mesh_id + mesh_url are readable; if node_id were still empty
            // in that window it would label itself with its own fallback and could
            // leave a phantom node row behind under the wrong identity. Writing it
            // ahead of mesh_id closes the window.
            Settings.Global.putString(cr, "focus_lock_mesh_node_id", nodeId);
            Settings.Global.putString(cr, "focus_lock_mesh_id", meshId);
            Settings.Global.putString(cr, "focus_lock_mesh_url", serverUrl);
            Settings.Global.putString(cr, "focus_lock_pin", pin);

            // Store Lion's public key — this makes PairingManager.isPaired() return true
            if (!lionPubKey.isEmpty()) {
                PairingManager.storeLionKey(cr, lionPubKey);
            }

            // Also update HOMELAB for backward compat
            HOMELAB = serverUrl;

            // mesh_id is now set — start ntfy subscriber so push wake-ups
            // land instead of waiting for the 30s polling fallback.
            startNtfySubscriber();

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
                "\n\nCancel fee: $" + (amount * 2) + "\n\nFirst charge now, then weekly. Overdue = warnings then auto-lock.")
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
    /** Write a transient subscribe outcome that survives the next ~30s of
     *  refreshStats overwrites. See line 620 of refreshStats — it now reads
     *  these keys when sub_tier is empty so the success "(syncing...)" or
     *  failure error stays visible long enough to read. Also fires a Toast
     *  for errors so the user sees them even if the activity is scrolled. */
    private void setSubPending(String msg, int color, boolean alsoToast) {
        prefs.edit()
            .putLong("sub_pending_at", System.currentTimeMillis())
            .putString("sub_pending_msg", msg)
            .putInt("sub_pending_color", color)
            .apply();
        handler.post(() -> {
            subStatus.setText(msg);
            subStatus.setTextColor(color);
            if (alsoToast) {
                android.widget.Toast.makeText(this, msg, android.widget.Toast.LENGTH_LONG).show();
            }
        });
    }

    private void postSubscribeToMesh(String tier, int amount) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) {
            setSubPending("Not paired to a mesh yet", 0xFFcc4444, true);
            return;
        }
        long ts = System.currentTimeMillis();
        String payload = meshId + "|" + nodeId + "|" + tier + "|" + ts;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) {
            // Most common cause on consumer installs: bunny privkey isn't in
            // Settings.Global because WRITE_SECURE_SETTINGS isn't granted, so
            // PairingManager.generateKeypair silently failed. The user has to
            // re-pair (or get the permission granted via adb) before signing
            // can work — surface that explicitly.
            setSubPending("Subscribe failed: no signing key (re-pair or grant WRITE_SECURE_SETTINGS)",
                0xFFcc4444, true);
            return;
        }
        JSONObject body = new JSONObject();
        try {
            body.put("node_id", nodeId);
            body.put("tier", tier);
            body.put("ts", ts);
            body.put("signature", signature);
        } catch (JSONException e) {
            setSubPending("Subscribe failed (json)", 0xFFcc4444, true);
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
                setSubPending("Subscribe failed (HTTP " + code + "): " + err, 0xFFcc4444, true);
                return;
            }
            sendSignedBunnyWebhook("/webhook/bunny-message", "bunny-message",
                "{\"text\":\"Subscribed to " + tier + " ($" + amount + "/wk)\",\"type\":\"subscription\"}");
            // Optimistic UI update; authoritative sub_tier/sub_due arrive via
            // the next vault blob (usually within ~30s).
            setSubPending(tier.toUpperCase() + " — $" + amount + "/week (syncing...)",
                0xFF44aa44, false);
        } catch (Exception e) {
            final String msg = e.getMessage();
            setSubPending("Subscribe failed: " + msg, 0xFFcc4444, true);
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
        // No homelab Ollama to auto-verify → route to the Lion for manual
        // approval instead of the (dead) /webhook/verify-photo. The task is held
        // until the Lion replies "approve" (see maybeHandleDeadlineApproval).
        if (gstr("focus_lock_webhook_host").isEmpty()) {
            submitPhotoForLionReview(bitmap, prompt);
            return;
        }
        handler.post(() -> setDeadlineTaskStatus("Verifying photo..."));
        try {
            // Audit 2026-04-27 M-4: bunny-signed.
            String innerJson = "{\"photo\":\"" + b64 + "\",\"task\":\""
                + prompt.replace("\\", "\\\\").replace("\"", "\\\"") + "\"}";
            String signedJson = buildBunnySignedBody("verify-photo", innerJson);
            if (signedJson == null) {
                handler.post(() -> setDeadlineTaskStatus("Verify failed: not paired/joined"));
                return;
            }
            URL url = new URL(meshUrl + "/webhook/verify-photo");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(60000);
            conn.getOutputStream().write(signedJson.getBytes("UTF-8"));
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

    /** Direct-mode (no-homelab) photo proof: there is no Ollama to auto-verify,
     *  so save the proof locally and notify the Lion via the message channel,
     *  holding the deadline until the Lion approves. Reuses postMeshMessage —
     *  no new server surface. Blocking; call from an executor thread. */
    private void submitPhotoForLionReview(Bitmap bitmap, String prompt) {
        try {
            java.io.File dir = new java.io.File(getFilesDir(), "deadline-proofs");
            dir.mkdirs();
            java.io.File f = new java.io.File(dir, "proof-" + System.currentTimeMillis() + ".jpg");
            try (java.io.FileOutputStream fos = new java.io.FileOutputStream(f)) {
                bitmap.compress(Bitmap.CompressFormat.JPEG, 80, fos);
            }
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "save proof failed", e);
        }
        Settings.Global.putString(getContentResolver(), "focus_lock_deadline_review_pending", "1");
        String msg = "📸 Photo proof ready for: " + prompt
            + "\nReply \"approve\" to clear it, or \"redo\" to send me back.";
        boolean sent = postMeshMessage(msg, true, false, null);
        final boolean fsent = sent;
        handler.post(() -> setDeadlineTaskStatus(fsent
            ? "Sent to your Lion for approval — waiting…"
            : "Saved proof; couldn't reach your Lion (will retry next time)"));
    }

    /** Direct-mode photo review: clear the deadline on the Lion's "approve"
     *  reply, or re-arm on "redo"/"reject". No-op unless a review is pending.
     *  Called per unread Lion message from refreshMeshMessages (executor). */
    private void maybeHandleDeadlineApproval(String lionText) {
        if (!"1".equals(gstr("focus_lock_deadline_review_pending")) || lionText == null) return;
        String t = lionText.trim().toLowerCase();
        if (t.startsWith("approve") || t.startsWith("pass") || t.equals("ok")) {
            Settings.Global.putString(getContentResolver(), "focus_lock_deadline_review_pending", "0");
            postDeadlineClear();
            handler.post(() -> setDeadlineTaskStatus("Approved by your Lion ✓ — task cleared"));
        } else if (t.startsWith("redo") || t.startsWith("reject") || t.startsWith("re-arm") || t.startsWith("fail")) {
            Settings.Global.putString(getContentResolver(), "focus_lock_deadline_review_pending", "0");
            handler.post(() -> setDeadlineTaskStatus("Your Lion asked you to redo it"));
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == REQ_BUNNY_ONBOARD) {
            // The warm welcome is done — now hand off to the Collar's Terms of
            // Surrender exactly as before (regardless of skip/back). The welcome
            // never replaces the ToS; it leads into it.
            maybeLaunchCollarConsent();
            return;
        }
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

    /** Bunny → server signed POST that tells the IMAP scanner which sender
     *  strings count as "actually from this bunny." Without this, the
     *  scanner credits every incoming payment notice (WorldRemit
     *  remittances, refunds, random Generic deposit emails) as a payment.
     *  The allowlist stays in a server-only file the Lion's Share app
     *  cannot read — defense in depth so the Lion never learns the
     *  bunny's email through the app surface. */
    private void doSetupPayerIdentity() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 24, 48, 24);

        TextView help = new TextView(this);
        help.setText("Your email or display name as it appears on Lion's bank "
            + "notifications. One per line. Used only to filter incoming "
            + "payment emails — never shown to the Lion.");
        help.setTextColor(0xFF888888);
        help.setTextSize(11);
        help.setPadding(0, 0, 0, 16);
        layout.addView(help);

        EditText allowInput = new EditText(this);
        allowInput.setHint("info@example.com\nYour Name");
        allowInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT
            | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        allowInput.setMinLines(3);
        allowInput.setGravity(android.view.Gravity.TOP | android.view.Gravity.START);
        String saved = prefs.getString("payer_identity_text", "");
        if (!saved.isEmpty()) allowInput.setText(saved);
        layout.addView(allowInput);

        new android.app.AlertDialog.Builder(this)
            .setTitle("Set Your Payer Identity")
            .setView(layout)
            .setPositiveButton("SAVE", (d, w) -> {
                String raw = allowInput.getText().toString();
                java.util.List<String> lines = new java.util.ArrayList<>();
                for (String line : raw.split("\\r?\\n")) {
                    String t = line.trim();
                    if (!t.isEmpty()) lines.add(t);
                }
                if (lines.size() > 16) lines = lines.subList(0, 16);
                // Snapshot the saved text so the dialog reopens with the
                // bunny's last input (lets them edit instead of re-typing).
                prefs.edit().putString("payer_identity_text", raw).apply();
                final java.util.List<String> finalLines = lines;
                executor.execute(() -> postSetPayerIdentity(finalLines));
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Post-pairing editor for the bunny's display name (how they appear to the
     *  Lion). Fixes the write-once/undeletable name: reachable any time via a
     *  long-press on the paired fingerprint, and pushes the change to the relay. */
    private void showEditNameDialog() {
        final EditText input = new EditText(this);
        input.setHint("Your name (how your Lion sees you)");
        try {
            String cur = Settings.Global.getString(getContentResolver(), "focus_lock_bunny_name");
            if (cur != null && !"null".equals(cur)) input.setText(cur.trim());
        } catch (Exception ignored) {}
        new android.app.AlertDialog.Builder(this)
            .setTitle("Display name")
            .setMessage("How you appear to your Lion. Leave blank to show your device model.")
            .setView(input)
            .setPositiveButton("Save", (d, w) -> {
                String nm = input.getText().toString().trim();
                if (nm.length() > 40) nm = nm.substring(0, 40);
                try { Settings.Global.putString(getContentResolver(), "focus_lock_bunny_name", nm); }
                catch (Exception ignored) {}
                final String fnm = nm;
                executor.execute(() -> setDisplayName(fnm));
                statusText.setText("Name updated");
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Sign + POST a display-name change (bunny-authed). Wire format matches the
     *  server set-display-name handler:
     *    payload = mesh|node|set-display-name|ts|sha256(display_name). */
    private void setDisplayName(String name) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) return;
        if (name == null) name = "";
        if (name.length() > 40) name = name.substring(0, 40);
        long ts = System.currentTimeMillis();
        String contentHash;
        try {
            java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
            byte[] dig = md.digest(name.getBytes("UTF-8"));
            StringBuilder sb = new StringBuilder();
            for (byte b : dig) sb.append(String.format("%02x", b));
            contentHash = sb.toString();
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "display-name hash failed", e);
            return;
        }
        String payload = meshId + "|" + nodeId + "|set-display-name|" + ts + "|" + contentHash;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) {
            handler.post(() -> statusText.setText("Sign failed — pairing key missing"));
            return;
        }
        try {
            JSONObject body = new JSONObject();
            body.put("node_id", nodeId);
            body.put("ts", ts);
            body.put("display_name", name);
            body.put("signature", signature);
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/set-display-name");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            conn.disconnect();
            android.util.Log.i("BunnyTasker", "set-display-name -> " + code);
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "set-display-name failed: " + e.getMessage());
        }
    }

    /** Sign + POST the payer-allow list. Matches the wire format the server
     *  expects in focuslock-mail.py set-payer-identity handler:
     *    payload = mesh|node|set-payer-identity|ts|sha256(allow_canonical)
     *  with allow_canonical = lines joined by "\n". */
    private void postSetPayerIdentity(java.util.List<String> allow) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) {
            handler.post(() -> statusText.setText("Mesh not configured"));
            return;
        }
        long ts = System.currentTimeMillis();
        StringBuilder canonical = new StringBuilder();
        for (int i = 0; i < allow.size(); i++) {
            if (i > 0) canonical.append("\n");
            canonical.append(allow.get(i));
        }
        String contentHash;
        try {
            java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
            byte[] dig = md.digest(canonical.toString().getBytes("UTF-8"));
            StringBuilder sb = new StringBuilder();
            for (byte b : dig) sb.append(String.format("%02x", b));
            contentHash = sb.toString();
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "payer identity hash failed", e);
            return;
        }
        String payload = meshId + "|" + nodeId + "|set-payer-identity|" + ts + "|" + contentHash;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) {
            handler.post(() -> statusText.setText("Sign failed — pairing key missing"));
            return;
        }
        try {
            JSONObject body = new JSONObject();
            body.put("node_id", nodeId);
            body.put("ts", ts);
            JSONArray arr = new JSONArray();
            for (String s : allow) arr.put(s);
            body.put("allow", arr);
            body.put("signature", signature);
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/set-payer-identity");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.toString().getBytes("UTF-8"));
            int code = conn.getResponseCode();
            // Parse the server summary so we can warn when entries are too
            // generic to identify the payer (channel/domain/short tokens now
            // match nothing — they'd never credit a payment).
            int effective = allow.size();
            int generic = 0;
            if (code < 400) {
                try {
                    java.io.BufferedReader br = new java.io.BufferedReader(
                        new java.io.InputStreamReader(conn.getInputStream(), "UTF-8"));
                    StringBuilder sb = new StringBuilder();
                    String ln;
                    while ((ln = br.readLine()) != null) sb.append(ln);
                    JSONObject resp = new JSONObject(sb.toString());
                    effective = resp.optInt("effective_count", allow.size());
                    generic = resp.optInt("generic_rejected", 0);
                } catch (Exception ignored) {}
            }
            conn.disconnect();
            if (code < 400) {
                final int eff = effective;
                final int gen = generic;
                prefs.edit()
                    .putLong("payer_identity_set_at", System.currentTimeMillis())
                    .putInt("payer_identity_count", allow.size())
                    .putInt("payer_identity_effective", eff)
                    .apply();
                handler.post(() -> {
                    if (eff == 0) {
                        statusText.setText("Saved, but none of your entries identify you — "
                            + "payments won't be credited. Use your full payment email or name "
                            + "(not a bank/'interac' or bare domain).");
                    } else if (gen > 0) {
                        statusText.setText("Saved · " + eff + " usable, " + gen + " too generic to match");
                    } else {
                        statusText.setText("Payer identity saved (" + allow.size() + " entries)");
                    }
                    refreshPayerIdentityStatus();
                });
            } else {
                handler.post(() -> statusText.setText("Save failed (HTTP " + code + ")"));
            }
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "postSetPayerIdentity failed", e);
            handler.post(() -> statusText.setText("Save failed: " + e.getMessage()));
        }
    }

    /** Render whether payments can be detected at all, from the relay's own
     *  view of it (the three booleans on /api/mesh/{id}/payments).
     *
     *  Both halves have to be configured before one payment is ever credited:
     *  the Lion connects the inbox the bank notifications land in (Lion's
     *  Share -> Payment Email), and the bunny sets the payer identity that
     *  tells the scanner which of those notifications are theirs. With either
     *  half missing the scanner fails closed and credits nothing — which,
     *  until this line existed, looked from here exactly like paying your Lion
     *  and being ignored.
     *
     *  This replaces applyHomelabGating(), which hid this whole section — and
     *  the payer-identity setup with it — whenever no homelab was configured.
     *  Neither one talks to the homelab: payer identity is a signed POST to
     *  the relay. On a vault mesh with no homelab (the normal setup) that
     *  meant a bunny could never set the identity their payments are matched
     *  by, so the scanner fail-closed on every payment they made, invisibly.
     *  UI thread. */
    private void renderDetectionStatus(boolean payeeOk, boolean payerOk, boolean payerEffective) {
        if (imapStatus == null) return;
        if (!payeeOk) {
            imapStatus.setText("Your Lion hasn't connected the inbox payments arrive in \u2014 "
                + "nothing can be credited until they do");
            imapStatus.setTextColor(0xFFcc4444);
        } else if (!payerOk) {
            imapStatus.setText("Set your payer identity so the scanner knows which payments are yours");
            imapStatus.setTextColor(0xFFaa6644);
        } else if (!payerEffective) {
            imapStatus.setText("\u26a0 Your payer identity is too generic to match anything");
            imapStatus.setTextColor(0xFFcc4444);
        } else {
            imapStatus.setText("\u2713 Payment detection active");
            imapStatus.setTextColor(0xFF44aa44);
        }
    }

    private void refreshPayerIdentityStatus() {
        if (payerIdentityStatus == null) return;
        long setAt = prefs.getLong("payer_identity_set_at", 0);
        int count = prefs.getInt("payer_identity_count", 0);
        int effective = prefs.getInt("payer_identity_effective", count);
        if (setAt == 0 || count == 0) {
            // Fail-closed: until the payer is configured, the server credits
            // nothing (was: counted every matched payment email).
            payerIdentityStatus.setText("Not set — payments won't be credited until you add your payment email/name");
            payerIdentityStatus.setTextColor(0xFFaa6644);
        } else if (effective == 0) {
            payerIdentityStatus.setText("⚠ Set, but entries too generic — payments won't be credited");
            payerIdentityStatus.setTextColor(0xFFcc4444);
        } else {
            long ageMs = System.currentTimeMillis() - setAt;
            String ago;
            if (ageMs < 60_000L) ago = "just now";
            else if (ageMs < 3_600_000L) ago = (ageMs / 60_000L) + "m ago";
            else if (ageMs < 86_400_000L) ago = (ageMs / 3_600_000L) + "h ago";
            else ago = (ageMs / 86_400_000L) + "d ago";
            payerIdentityStatus.setText("Set · " + count + " entries · " + ago);
            payerIdentityStatus.setTextColor(0xFF44aa44);
        }
    }

    // doSetupImap() was removed here. It POSTed the bunny's own IMAP
    // credentials to /mesh/set-imap-creds — an endpoint that exists in no
    // server in this repo, so every "Connected: <email>" it printed was a lie
    // (it never read the response code either). Two things were wrong with it
    // beyond the dead route:
    //
    //   1. The mailbox the scanner reads is the LION's. "You received $40"
    //      landing in the payee's inbox is what proves an incoming transfer;
    //      a confirmation in the payer's own mailbox proves only that the
    //      payer's mailbox says so. Lion's Share -> Payment Email is the one
    //      place it is configured (relay: set-payee-identity, Lion-signed).
    //   2. Wiring it up would have handed the bunny the ability to point the
    //      scanner at a mailbox they control, and mint their own payment
    //      confirmations straight off the balance.
    //
    // What the bunny legitimately owns on this screen is the payer identity
    // (doSetupPayerIdentity), which is kept. Detection state is reported
    // read-only by renderDetectionStatus.

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

                // Is payment detection actually wired up? Defaults to true so
                // a relay predating these fields doesn't cry wolf.
                final boolean payeeOk = resp.optBoolean("payee_configured", true);
                final boolean payerOk = resp.optBoolean("payer_configured", true);
                final boolean payerEff = resp.optBoolean("payer_effective", true);
                handler.post(() -> renderDetectionStatus(payeeOk, payerOk, payerEff));

                // Flip budget rides this response too, so the gamble button
                // renders its real state on load instead of after a refusal.
                applyGambleBudget(resp);
                applyDevotionStatus(resp);
                handler.post(() -> {
                    renderGambleBudget();
                    refreshDevotion();
                });

                JSONArray entries = resp.optJSONArray("entries");
                if (entries == null) return;

                // Balance trend. `entries` is newest-first and only some rows
                // carry balance_after (older ones predate it), so walk
                // backwards and keep the ones that do — oldest-first is what
                // the sparkline wants.
                java.util.List<Float> trend = new java.util.ArrayList<>();
                for (int i = entries.length() - 1; i >= 0; i--) {
                    JSONObject e = entries.optJSONObject(i);
                    if (e != null && e.has("balance_after")) {
                        trend.add((float) e.optDouble("balance_after", 0));
                    }
                }
                final float[] spark = new float[trend.size()];
                for (int i = 0; i < trend.size(); i++) spark[i] = trend.get(i);

                handler.post(() -> {
                    if (balanceSpark != null) {
                        balanceSpark.setValues(spark);
                        balanceSpark.setVisibility(spark.length >= 2 ? View.VISIBLE : View.GONE);
                    }
                    paymentHistory.removeAllViews();
                    int shown = Math.min(entries.length(), 20);
                    for (int i = 0; i < shown; i++) {
                        JSONObject e = entries.optJSONObject(i);
                        if (e == null) continue;
                        double amount = e.optDouble("amount", 0);
                        String desc = e.optString("description", "");
                        long ets = e.optLong("timestamp", 0);
                        String type = e.optString("type", "payment");

                        // The ledger carries charges as well as payments now,
                        // so the sign and colour have to come from the type. A
                        // fine rendered "+$5" in green reads as money paid off
                        // rather than money owed — the exact opposite.
                        boolean owedMore = "charge".equals(type);
                        boolean reversal = "reversal".equals(type);
                        String sign = owedMore ? "+$" : "\u2212$";   // − for anything that reduces it
                        int colour = owedMore ? 0xFFcc7755 : reversal ? 0xFFaa8844 : 0xFF66aa66;

                        StringBuilder row = new StringBuilder();
                        row.append(sign).append(String.format("%.2f", Math.abs(amount)));
                        if (!desc.isEmpty()) row.append("  ").append(desc);
                        if (e.has("balance_after")) {
                            row.append("   \u2192 $").append(String.format("%.2f", e.optDouble("balance_after", 0)));
                        }
                        row.append("  ").append(formatRelativeTime(ets));

                        TextView tv = new TextView(MainActivity.this);
                        tv.setText(row.toString());
                        tv.setTextColor(colour);
                        tv.setTextSize(10);
                        tv.setPadding(0, 4, 0, 4);
                        paymentHistory.addView(tv);
                    }
                    if (entries.length() == 0) {
                        TextView tv = new TextView(MainActivity.this);
                        tv.setText("Nothing on the balance yet");
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
                    sendSignedBunnyWebhook("/webhook/bunny-message", "bunny-message",
                        "{\"text\":\"Paid " + tier + " subscription early ($" + amt + ")\",\"type\":\"prepay\"}");
                    handler.post(() -> {
                        subStatus.setText(tier.toUpperCase() + " — paid early! Next due in 7d");
                        subStatus.setTextColor(0xFF44aa44);
                    });
                });
                // Open banking app via the same picker chain the Pay button uses
                launchBankingApp(false);
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
                executor.execute(() -> postUnsubscribeToMesh(tier, fee));
            })
            .setNegativeButton("Keep", null)
            .show();
    }

    /** Sign an unsubscribe intent with the bunny's registered key and POST to
     *  the server. Server reads current sub_tier, applies UNSUBSCRIBE_FEES[tier]
     *  to paywall, clears tier + due. The resulting vault blob propagates to
     *  every device. P2 paywall hardening follow-up: replaces the local-only
     *  paywall write that the Collar was doing — server is single writer for
     *  enforcement-driven increments. */
    private void postUnsubscribeToMesh(String tier, int fee) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) {
            handler.post(() -> subStatus.setText("Not paired to a mesh yet"));
            return;
        }
        long ts = System.currentTimeMillis();
        String payload = meshId + "|" + nodeId + "|unsubscribe|" + ts;
        String signature = PairingManager.sign(getContentResolver(), payload);
        if (signature == null || signature.isEmpty()) {
            handler.post(() -> subStatus.setText("Unsubscribe failed (no key)"));
            return;
        }
        JSONObject body = new JSONObject();
        try {
            body.put("node_id", nodeId);
            body.put("ts", ts);
            body.put("signature", signature);
        } catch (JSONException e) {
            handler.post(() -> subStatus.setText("Unsubscribe failed (json)"));
            return;
        }
        try {
            URL url = new URL(meshUrl + "/api/mesh/" + meshId + "/unsubscribe");
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
                handler.post(() -> subStatus.setText("Unsubscribe failed: " + err));
                return;
            }
            sendSignedBunnyWebhook("/webhook/bunny-message", "bunny-message",
                "{\"text\":\"Unsubscribed from " + tier + " (fee: $" + fee + ")\",\"type\":\"unsubscription\"}");
            handler.post(() -> subStatus.setText("Cancelled. $" + fee + " fee charged (syncing...)"));
        } catch (Exception e) {
            final String msg = e.getMessage();
            handler.post(() -> subStatus.setText("Unsubscribe failed: " + msg));
        }
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
                        sendSignedBunnyWebhook("/webhook/bunny-message", "bunny-message",
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
                        sendSignedBunnyWebhook("/webhook/bunny-message", "bunny-message",
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

    /**
     * Audit C1: local-HTTP POSTs to the Collar now carry a bunny-signed payload.
     * The Collar's SigVerifier accepts either lion- or bunny-signed requests,
     * so Bunny Tasker (same phone, shared Settings.Global) signs with its own
     * privkey. Canonicalization + headers match the slave's SigVerifier + the
     * controller's VaultCrypto.canonicalizeDirectPost byte-for-byte.
     */
    private boolean postToCollar(String path, String json) {
        long ts = System.currentTimeMillis();
        String nonce = collarRandomNonce();
        String payload = collarCanonicalize(path, json, ts, nonce);
        String sig = PairingManager.sign(getContentResolver(), payload);
        if (sig == null || sig.isEmpty()) {
            android.util.Log.w("BunnyTasker", "postToCollar: missing bunny privkey — cannot sign");
            return false;
        }
        int[] ports = {8432, 8433};
        for (int port : ports) {
            try {
                java.net.URL url = new java.net.URL("http://127.0.0.1:" + port + path);
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setRequestProperty("X-FL-Ts", Long.toString(ts));
                conn.setRequestProperty("X-FL-Nonce", nonce);
                conn.setRequestProperty("X-FL-Sig", sig);
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

    private static String collarRandomNonce() {
        byte[] buf = new byte[16];
        new java.security.SecureRandom().nextBytes(buf);
        return android.util.Base64.encodeToString(
            buf, android.util.Base64.NO_WRAP | android.util.Base64.URL_SAFE | android.util.Base64.NO_PADDING);
    }

    private static String collarCanonicalize(String path, String body, long ts, String nonce) {
        StringBuilder params = new StringBuilder();
        boolean jsonOk = body != null && !body.isEmpty() && body.trim().startsWith("{");
        if (jsonOk) {
            try {
                org.json.JSONObject obj = new org.json.JSONObject(body);
                java.util.TreeMap<String, String> sorted = new java.util.TreeMap<>();
                java.util.Iterator<String> keys = obj.keys();
                while (keys.hasNext()) {
                    String k = keys.next();
                    Object v = obj.opt(k);
                    if (v == null || org.json.JSONObject.NULL.equals(v)) continue;
                    sorted.put(k, collarEncodeValue(v));
                }
                boolean first = true;
                for (java.util.Map.Entry<String, String> e : sorted.entrySet()) {
                    if (!first) params.append('&');
                    params.append(collarUrlEnc(e.getKey())).append('=').append(e.getValue());
                    first = false;
                }
            } catch (Exception e) { jsonOk = false; }
        }
        if (!jsonOk) {
            params.setLength(0);
            params.append("_raw=").append(collarUrlEnc(body == null ? "" : body));
        }
        return "focusctl|" + path + "|" + ts + "|" + nonce + "|" + params;
    }

    private static String collarEncodeValue(Object v) {
        if (v instanceof Boolean) return ((Boolean) v) ? "1" : "0";
        if (v instanceof Integer || v instanceof Long) return v.toString();
        if (v instanceof Number) {
            double d = ((Number) v).doubleValue();
            if (!Double.isNaN(d) && !Double.isInfinite(d)
                    && d == Math.floor(d) && Math.abs(d) < 1e15) {
                return Long.toString((long) d);
            }
            return v.toString();
        }
        return collarUrlEnc(v.toString());
    }

    private static String collarUrlEnc(String s) {
        try { return java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20"); }
        catch (Exception e) { return s; }
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
        return postMeshMessageAt(text, pinned, mandatory, enc, System.currentTimeMillis());
    }

    /** Variant that takes an explicit ts so the caller can key a local
     *  plaintext cache against the same ts the server will persist. */
    private boolean postMeshMessageAt(String text, boolean pinned, boolean mandatory,
                                      E2EEHelper.EncryptedMessage enc, long ts) {
        return postMeshMessageAt(text, pinned, mandatory, enc, ts, null);
    }

    /** Full variant. clientMsgId is a stable idempotency key: a retried send
     *  (same ts + signature + clientMsgId) dedups to a single stored message
     *  server-side (see MessageStore.add), so the caller can retry safely
     *  without spamming the Lion's inbox. Pass null to opt out. */
    private boolean postMeshMessageAt(String text, boolean pinned, boolean mandatory,
                                      E2EEHelper.EncryptedMessage enc, long ts, String clientMsgId) {
        String meshId = gstr("focus_lock_mesh_id");
        String meshUrl = gstr("focus_lock_mesh_url");
        String nodeId = gstr("focus_lock_mesh_node_id");
        if (meshId.isEmpty() || meshUrl.isEmpty() || nodeId.isEmpty()) return false;
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
                if (enc.encryptedKeySelf != null) body.put("encrypted_key_bunny", enc.encryptedKeySelf);
            }
            body.put("ts", ts);
            if (clientMsgId != null && !clientMsgId.isEmpty()) body.put("client_msg_id", clientMsgId);
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

    /** Deferred payer-identity send: the Bunny welcome (BunnyWelcomeActivity)
     *  collects the payer email/name BEFORE pairing, so it can't reach the
     *  server-only payer allowlist yet. Once paired (mesh configured), send it
     *  via the existing signed postSetPayerIdentity. Retries each poll until
     *  postSetPayerIdentity records success (payer_identity_set_at), then clears
     *  the pending flag. The payer identity never reaches the Lion's app.
     *  Blocking — call from an executor thread. */
    private void maybeSendPendingPayerIdentity() {
        if (!prefs.getBoolean("payer_identity_pending", false)) return;
        if (prefs.getLong("payer_identity_set_at", 0) > 0) {  // already set on server
            prefs.edit().putBoolean("payer_identity_pending", false).apply();
            return;
        }
        if (gstr("focus_lock_mesh_id").isEmpty() || gstr("focus_lock_mesh_url").isEmpty()
                || gstr("focus_lock_mesh_node_id").isEmpty()) {
            return;  // not paired yet — try again next poll
        }
        String raw = prefs.getString("payer_identity_text", "");
        java.util.ArrayList<String> lines = new java.util.ArrayList<>();
        for (String line : raw.split("\\r?\\n")) {
            String t = line.trim();
            if (!t.isEmpty()) lines.add(t);
        }
        if (lines.isEmpty()) {
            prefs.edit().putBoolean("payer_identity_pending", false).apply();
            return;
        }
        postSetPayerIdentity(lines);  // sets payer_identity_set_at on success → cleared next poll
    }

    /** Serverless evidence log: drain the Collar's shared evidence outbox
     *  (focus_lock_evidence_outbox, populated by FocusActivity.enqueueEvidenceForLion
     *  when no homelab is configured) into the Lion's in-app inbox via the signed
     *  message channel. Best-effort: entries that fail to send are kept for the
     *  next tick. Blocking — call from an executor thread. */
    private void drainEvidenceOutbox() {
        try {
            String cur = gstr("focus_lock_evidence_outbox");
            if (cur.isEmpty()) return;
            // Only drain when Lion is reachable (mesh configured), else keep queued.
            if (gstr("focus_lock_mesh_url").isEmpty() || gstr("focus_lock_mesh_id").isEmpty()) return;
            JSONArray arr = new JSONArray(cur);
            if (arr.length() == 0) { Settings.Global.putString(getContentResolver(), "focus_lock_evidence_outbox", ""); return; }
            JSONArray remaining = new JSONArray();
            for (int i = 0; i < arr.length(); i++) {
                JSONObject e = arr.optJSONObject(i);
                if (e == null) continue;
                String text = e.optString("text", "");
                if (text.isEmpty()) continue;
                boolean sent = postMeshMessage("📋 " + text, false, false, null);
                if (!sent) remaining.put(e);  // retry next tick
            }
            Settings.Global.putString(getContentResolver(), "focus_lock_evidence_outbox",
                remaining.length() == 0 ? "" : remaining.toString());
        } catch (Exception ex) {
            android.util.Log.w("BunnyTasker", "drainEvidenceOutbox", ex);
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
        long ts = System.currentTimeMillis();
        // Stable idempotency key so the bounded retry below can re-POST without
        // the Lion seeing duplicates (server dedups on client_msg_id).
        final String clientMsgId = java.util.UUID.randomUUID().toString();
        // Cache the plaintext keyed by ts BEFORE the network call so even if
        // the send races with the next refresh we can still re-render it.
        // postMeshMessage signs over the same ts so the lookup is exact.
        cacheSentPlaintext(ts, msg);
        // E2EE is per-peer: with no Lion pubkey yet, this reply goes in plaintext.
        // Warn + allow — tell her immediately (the persistent banner also shows).
        final boolean plaintext = !E2EEHelper.canEncrypt(gstr("focus_lock_lion_pubkey"));
        // Render optimistically so she immediately sees what she typed; the
        // delivery side-effects below are gated on the send actually landing.
        handler.post(() -> {
            addMessageBubble(msg, true, ts);
            if (plaintext) android.widget.Toast.makeText(this,
                "Sending unencrypted — Lion's key not yet exchanged",
                android.widget.Toast.LENGTH_SHORT).show();
        });
        executor.execute(() -> {
            String lionPubKey = gstr("focus_lock_lion_pubkey");
            E2EEHelper.EncryptedMessage enc = null;
            if (E2EEHelper.canEncrypt(lionPubKey)) {
                // Wrap the same AES key a second time for ourselves, so our own
                // side of the thread survives a cache eviction, a reinstall or
                // a new device. Same fix Lion's Share already carries.
                enc = E2EEHelper.encrypt(msg, lionPubKey, ownBunnyPub());
            }
            // Bounded retry. Reuse ts + clientMsgId every attempt so the
            // signature stays valid and the server dedups to one message.
            boolean ok = false;
            for (int attempt = 0; attempt < 3 && !ok; attempt++) {
                if (attempt > 0) {
                    try { Thread.sleep(1000L * attempt); }
                    catch (InterruptedException ie) { Thread.currentThread().interrupt(); break; }
                }
                ok = postMeshMessageAt(msg, false, false, enc, ts, clientMsgId);
            }
            if (ok) {
                // Only a delivered reply counts: record the check-in and clear
                // any outstanding mandatory-reply obligation. Previously these
                // ran unconditionally, so a failed send still cleared the
                // obligation and suppressed the auto-lock while the Lion never
                // received the reply.
                Settings.Global.putLong(getContentResolver(), "focus_lock_checkin_timestamp", ts);
                markMandatoryReplied();
            } else {
                handler.post(() -> android.widget.Toast.makeText(this,
                    "Message not delivered — check your connection and resend.",
                    android.widget.Toast.LENGTH_LONG).show());
            }
        });
    }

    /** Overdue threshold for mandatory lion replies. Legacy server computed
     *  this; client now owns it. 4h default keeps the semantics without being
     *  hair-trigger. */
    private static final long MANDATORY_OVERDUE_MS = 4L * 60 * 60 * 1000;

    /** Audit C4: verify the stored lion signature on a fetched message record
     *  before we take any enforcement action on it (currently: mandatory-reply
     *  auto-lock). Reconstructs the same payload the server verified on
     *  /messages/send: mesh|node|lion|text|pinned|mandatory|ts. Returns false
     *  if the signature field is missing (pre-fix messages) or does not verify
     *  — callers must treat missing + invalid identically. */
    private boolean verifyLionMessageSignature(JSONObject m, String meshId) {
        try {
            String sig = m.optString("signature", "");
            if (sig.isEmpty() || meshId.isEmpty()) return false;
            String nodeId = m.optString("node_id", "");
            if (nodeId.isEmpty()) return false;
            long ts = m.optLong("ts", 0);
            if (ts <= 0) return false;
            // For E2EE messages the signed plaintext marker is "[e2ee]"
            // (see controller's postLionMessage). Use the raw text field
            // exactly as the server stored it.
            String text = m.optString("text", "");
            boolean pinned = m.optBoolean("pinned", false);
            boolean mandatory = m.optBoolean("mandatory_reply", false);
            String payload = meshId + "|" + nodeId + "|lion|" + text
                + "|" + (pinned ? "1" : "0") + "|" + (mandatory ? "1" : "0") + "|" + ts;
            return PairingManager.verify(getContentResolver(), payload, sig);
        } catch (Exception e) {
            return false;
        }
    }

    private void refreshMeshMessages() {
        executor.execute(() -> {
            try {
                JSONObject data = fetchMeshMessagesSigned(0, 30);
                if (data == null) return;
                JSONArray msgs = data.optJSONArray("messages");
                if (msgs == null) return;

                String myNodeId = gstr("focus_lock_mesh_node_id");
                String bunnyPrivKey = gstr("focus_lock_bunny_privkey");
                String meshId = gstr("focus_lock_mesh_id");
                long now = System.currentTimeMillis();
                boolean anyOverdue = false;
                java.util.List<String> toMarkRead = new java.util.ArrayList<>();

                // First pass: notifications, unread tracking, overdue detection.
                // "unread by me" = from lion AND `bunny` not in read_by.
                for (int i = 0; i < msgs.length(); i++) {
                    JSONObject m = msgs.optJSONObject(i);
                    if (m == null) continue;
                    if (!"lion".equals(m.optString("from"))) continue;
                    // Skip notifications + auto-lock entirely for deleted
                    // messages — Lion has retracted the message, the bunny
                    // shouldn't be locked for "missing" a reply to a deleted
                    // mandatory message, and we shouldn't fire a notif for
                    // text she's no longer supposed to see.
                    if (m.optBoolean("deleted", false)) continue;
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
                        // Direct-mode photo review: a Lion "approve"/"redo" reply
                        // clears or re-arms a pending deadline-proof review.
                        maybeHandleDeadlineApproval(notifText);
                        if (!mid.isEmpty()) toMarkRead.add(mid);
                    }

                    // Overdue = lion's mandatory-reply older than threshold and not yet replied.
                    // SECURITY (audit C4): only auto-lock if the stored lion signature
                    // on this message verifies. Without this, a compromised relay or
                    // mesh-peer that bypasses /messages/send could inject
                    // from:"lion", mandatory_reply:true and force-lock the bunny.
                    long mts = m.optLong("ts", 0);
                    if (mandatoryFlag && !replied && mts > 0 && (now - mts) > MANDATORY_OVERDUE_MS) {
                        if (verifyLionMessageSignature(m, meshId)) {
                            anyOverdue = true;
                        } else {
                            android.util.Log.w("BunnyTasker",
                                "mandatory_reply auto-lock skipped — lion signature missing or invalid (id=" + mid + ")");
                        }
                    }
                }

                // Surface unread count on the collapsed header so the bunny
                // sees a "• N new" hint without having to expand to find out.
                // Counted BEFORE we fire the read-acks below — once those land
                // the next refresh will reset this to 0 naturally.
                int unreadCount = toMarkRead.size();
                prefs.edit().putInt("messages_unread_lion", unreadCount).apply();
                handler.post(() -> applyMessagesExpanded());

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
                        boolean deleted = m.optBoolean("deleted", false);
                        boolean edited = m.has("edited_at");
                        long ts = m.optLong("ts", 0);
                        // Tombstone wins over decryption + mandatory-reply
                        // formatting — Lion has retracted, the bunny sees
                        // "[deleted]" with no original text leak.
                        if (deleted) {
                            text = "[deleted]";
                        } else if (m.optBoolean("encrypted", false) && !fromBunny) {
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
                            // Our own copy, wrapped for us at send time. The
                            // local plaintext cache is now only a fast path:
                            // it is per-device and evictable, so it was never
                            // able to survive a reinstall or a device change.
                            // Messages sent before the second wrap existed
                            // carry no encrypted_key_bunny and stay unreadable
                            // — say so plainly rather than implying the whole
                            // feature is broken.
                            String cached = lookupSentPlaintext(ts);
                            if (cached != null) {
                                text = cached;
                            } else {
                                String ekSelf = m.optString("encrypted_key_bunny", "");
                                if (!ekSelf.isEmpty() && E2EEHelper.canDecrypt(bunnyPrivKey)) {
                                    String dec = E2EEHelper.decrypt(
                                        m.optString("ciphertext", ""), ekSelf,
                                        m.optString("iv", ""), bunnyPrivKey);
                                    text = dec != null ? dec : "[encrypted — could not read your own copy]";
                                    // Re-seed the cache so the next render is free.
                                    if (dec != null) cacheSentPlaintext(ts, dec);
                                } else {
                                    text = "[encrypted — sent before your copy was kept]";
                                }
                            }
                        }
                        boolean isMandatory = m.optBoolean("mandatory_reply", false)
                            && !m.optBoolean("replied", false) && !deleted;
                        if (isMandatory && !fromBunny) {
                            text = "[REPLY REQUIRED] " + text;
                        }
                        if (edited && !deleted) {
                            text = text + "  (edited)";
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

    /** Apply current messagesExpanded state to header + body visibility.
     *  Toggles the wrapper (input row + 380dp scroll) rather than the inner
     *  container so collapsed actually shrinks the section. */
    private void applyMessagesExpanded() {
        if (messagesBody != null) {
            messagesBody.setVisibility(messagesExpanded ? View.VISIBLE : View.GONE);
        }
        if (messagesHeader != null) {
            int unread = prefs.getInt("messages_unread_lion", 0);
            String suffix = (!messagesExpanded && unread > 0) ? "  • " + unread + " new" : "";
            messagesHeader.setText("MESSAGE YOUR LION" + suffix
                + (messagesExpanded ? "  ▲" : "  ▼"));
        }
        updateMsgE2eeWarning();
    }

    /** Show the "not encrypted" banner when there's no Lion pubkey to encrypt to
     *  (E2EE is per-peer; canEncrypt is false until pairing exchanges the key).
     *  Warn + allow — replies still send in plaintext. UI thread only. */
    private void updateMsgE2eeWarning() {
        View warn = findViewById(fid("messages_e2ee_warning"));
        if (warn != null) {
            warn.setVisibility(E2EEHelper.canEncrypt(gstr("focus_lock_lion_pubkey")) ? View.GONE : View.VISIBLE);
        }
    }

    /** Cache plaintext for a bunny-sent message keyed by ts. Lets the chat
     *  thread render the bunny's own outgoing messages after the next refresh
     *  wipes the bubble — the server-stored copy is ciphertext (encrypted to
     *  Lion's pubkey) and the bunny can't decrypt it.
     *
     *  Kept in SharedPreferences (cap 200 entries) so it survives an app
     *  restart but doesn't grow forever. Storing only the bunny's own
     *  plaintext on the bunny's own device adds no new exposure surface —
     *  the bunny already authored it. */
    private static final int SENT_PLAINTEXT_CAP = 200;

    /** Our own public key, in the same form the mesh registered us with, so a
     *  self-wrap is readable by the private key PairingManager already holds. */
    private String ownBunnyPub() {
        try {
            return PairingManager.getPublicKey(getContentResolver());
        } catch (Exception e) {
            android.util.Log.w("BunnyTasker", "own pubkey unavailable for self-wrap", e);
            return null;
        }
    }

    private void cacheSentPlaintext(long ts, String plaintext) {
        if (plaintext == null) return;
        SharedPreferences sp = getSharedPreferences("bunny_sent_plain", MODE_PRIVATE);
        SharedPreferences.Editor ed = sp.edit();
        ed.putString("t" + ts, plaintext);
        // Cheap eviction: when we cross the cap, drop the oldest ~50.
        java.util.Map<String, ?> all = sp.getAll();
        if (all.size() > SENT_PLAINTEXT_CAP) {
            java.util.TreeSet<Long> tsKeys = new java.util.TreeSet<>();
            for (String k : all.keySet()) {
                if (k.startsWith("t")) {
                    try { tsKeys.add(Long.parseLong(k.substring(1))); } catch (Exception ignored) {}
                }
            }
            int toDrop = tsKeys.size() - (SENT_PLAINTEXT_CAP - 50);
            java.util.Iterator<Long> it = tsKeys.iterator();
            while (toDrop-- > 0 && it.hasNext()) ed.remove("t" + it.next());
        }
        ed.apply();
    }

    private String lookupSentPlaintext(long ts) {
        if (ts <= 0) return null;
        SharedPreferences sp = getSharedPreferences("bunny_sent_plain", MODE_PRIVATE);
        return sp.getString("t" + ts, null);
    }

    private void addMessageBubble(String text, boolean fromBunny, long timestamp) {
        // SMS-app-style bubble: align right for self (bunny), left for the
        // Lion. Bubble width is wrap_content with a generous max so long
        // messages wrap. Timestamp under each bubble in dim grey. Container
        // is wrapped in a fixed-height ScrollView (messages_scroll) — see
        // activity_main.xml. We auto-scroll to the bottom after adding so
        // the most recent message is always visible.
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        LinearLayout.LayoutParams rowLp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        rowLp.setMargins(0, 4, 0, 4);
        row.setLayoutParams(rowLp);

        // The bubble itself
        TextView bubble = new TextView(this);
        bubble.setText(text);
        bubble.setTextColor(fromBunny ? 0xFFe8d8ff : 0xFFffe6a8);
        bubble.setTextSize(14);
        bubble.setPadding(20, 14, 20, 14);
        // Rounded rectangle background via GradientDrawable so we don't need
        // a separate XML drawable resource.
        android.graphics.drawable.GradientDrawable bg = new android.graphics.drawable.GradientDrawable();
        bg.setColor(fromBunny ? 0xFF3a2a4a : 0xFF3a2e10);
        bg.setCornerRadius(28f);
        bubble.setBackground(bg);
        LinearLayout.LayoutParams bubbleLp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        bubbleLp.gravity = fromBunny ? android.view.Gravity.END : android.view.Gravity.START;
        bubbleLp.leftMargin = fromBunny ? 80 : 0;
        bubbleLp.rightMargin = fromBunny ? 0 : 80;
        bubble.setLayoutParams(bubbleLp);
        // Cap bubble width so long messages wrap instead of pushing offscreen.
        int maxPx = (int) (getResources().getDisplayMetrics().widthPixels * 0.78);
        bubble.setMaxWidth(maxPx);
        row.addView(bubble);

        // Timestamp under bubble, aligned to the same edge.
        if (timestamp > 0) {
            TextView ts = new TextView(this);
            ts.setText(formatRelativeTime(timestamp));
            ts.setTextColor(0xFF6a6275);
            ts.setTextSize(10);
            ts.setPadding(8, 2, 8, 0);
            LinearLayout.LayoutParams tsLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
            tsLp.gravity = fromBunny ? android.view.Gravity.END : android.view.Gravity.START;
            ts.setLayoutParams(tsLp);
            row.addView(ts);
        }

        messagesContainer.addView(row);
        while (messagesContainer.getChildCount() > 50) {
            messagesContainer.removeViewAt(0);
        }

        // Auto-scroll to bottom so the freshest message is always visible.
        // The post() defers until layout pass completes so fullScroll has
        // up-to-date child positions.
        final ScrollView scroll = (ScrollView) findViewById(fid("messages_scroll"));
        if (scroll != null) {
            scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
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

    private String lastConfinedRadius = "";

    /** Persistent confined-to-home notification. Stays in the shade as long
     *  as the Collar's geofence is active. Cleared by clearConfinedNotification
     *  when Lion releases (clear-geofence). */
    private void showConfinedNotification(String radius) {
        if (radius == null || radius.isEmpty()) radius = "100";
        if (radius.equals(lastConfinedRadius)) return;
        lastConfinedRadius = radius;
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "confined", "Confined to Home", NotificationManager.IMPORTANCE_LOW);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            ch.setSound(null, null);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "confined")
                .setContentTitle("Confined to home")
                .setContentText("Geofence active — " + radius
                    + "m radius. Auto-lock + $100 paywall on breach.")
                .setSmallIcon(android.R.drawable.ic_dialog_map)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .build();
            nm.notify(302, n);
        } catch (Exception e) { android.util.Log.e("BunnyTasker", "confined notif", e); }
    }

    private void clearConfinedNotification() {
        if (lastConfinedRadius.isEmpty()) return;  // already cleared
        lastConfinedRadius = "";
        try {
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).cancel(302);
        } catch (Exception e) { /* best effort */ }
    }

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

    /** Build a bunny-signed body string from inner JSON. Returns null when
     *  the companion isn't paired/joined yet (no mesh_id / node_id /
     *  privkey). Used by callers that read the HTTP response (e.g.
     *  /webhook/verify-photo) where the fire-and-forget
     *  sendSignedBunnyWebhook helper isn't a fit. Audit 2026-04-27 M-4. */
    private String buildBunnySignedBody(String eventType, String innerJson) {
        String meshId = gstr("focus_lock_mesh_id");
        String nodeId = gstr("focus_lock_mesh_node_id");
        String bunnyPrivB64 = gstr("focus_lock_bunny_privkey");
        if (meshId.isEmpty() || nodeId.isEmpty() || bunnyPrivB64.isEmpty()) {
            android.util.Log.w("BunnyTasker",
                "buildBunnySignedBody(" + eventType + ") skipped: not yet paired/joined");
            return null;
        }
        try {
            long ts = System.currentTimeMillis();
            String payload = meshId + "|" + nodeId + "|" + eventType + "|" + ts;
            byte[] privBytes = android.util.Base64.decode(bunnyPrivB64, android.util.Base64.NO_WRAP);
            java.security.spec.PKCS8EncodedKeySpec spec =
                new java.security.spec.PKCS8EncodedKeySpec(privBytes);
            java.security.PrivateKey priv =
                java.security.KeyFactory.getInstance("RSA").generatePrivate(spec);
            java.security.Signature sig = java.security.Signature.getInstance("SHA256withRSA");
            sig.initSign(priv);
            sig.update(payload.getBytes("UTF-8"));
            String signature = android.util.Base64.encodeToString(
                sig.sign(), android.util.Base64.NO_WRAP);

            String inner = innerJson.trim();
            if (!inner.startsWith("{") || !inner.endsWith("}")) {
                android.util.Log.e("BunnyTasker",
                    "buildBunnySignedBody(" + eventType + ") bad innerJson shape");
                return null;
            }
            if (inner.equals("{}")) {
                return "{\"mesh_id\":\"" + meshId + "\",\"node_id\":\"" + nodeId
                    + "\",\"ts\":" + ts + ",\"signature\":\"" + signature + "\"}";
            }
            return inner.substring(0, inner.length() - 1)
                + ",\"mesh_id\":\"" + meshId + "\",\"node_id\":\"" + nodeId
                + "\",\"ts\":" + ts + ",\"signature\":\"" + signature + "\"}";
        } catch (Exception e) {
            android.util.Log.e("BunnyTasker",
                "buildBunnySignedBody(" + eventType + ") sign failed: " + e.getMessage());
            return null;
        }
    }

    /** Bunny-signed variant of sendWebhook for endpoints that require a
     *  signature over "{mesh_id}|{node_id}|{event_type}|{ts}". Reads
     *  mesh_id / node_id / bunny_privkey from Settings.Global, merges
     *  ts + signature into the caller-supplied inner JSON, and posts to
     *  path. If mesh/node/key are missing (pre-join state) the call is
     *  skipped with a warning — don't spam the server with unsigned
     *  requests that will only get 403'd.
     *
     *  Companion v53 (2.20): see CHANGELOG entry for /webhook/bunny-message
     *  which moved from unauth'd to bunny-signed as a "clean break, no grace
     *  period" update coordinated with the server. */
    private void sendSignedBunnyWebhook(String path, String eventType, String innerJson) {
        String body = buildBunnySignedBody(eventType, innerJson);
        if (body != null) sendWebhook(path, body);
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

    /** Resolve the configured banking app, launch it, or prompt the user
     *  to pick one. Settings.Global lookup remains as a legacy compat
     *  fallback for installs that were adb-provisioned before the picker
     *  shipped — once the picker writes to SharedPreferences, that
     *  becomes the source of truth. */
    private void launchBankingApp(boolean forcePick) {
        String pkg = forcePick ? "" : prefs.getString("banking_app", "");
        if (pkg.isEmpty()) {
            // Legacy: try Settings.Global as fallback for adb-provisioned setups
            pkg = gstr("focus_lock_banking_app");
        }
        if (!pkg.isEmpty() && !forcePick) {
            Intent launch = getPackageManager().getLaunchIntentForPackage(pkg);
            if (launch != null) {
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(launch);
                return;
            }
            // Stored package no longer installed — fall through to picker
            android.util.Log.w("BunnyTasker", "banking app " + pkg + " not installed, re-prompting");
        }
        showBankingPicker();
    }

    /** Dialog listing installed apps that match a known bank package from
     *  shared/banks.json (bundled at /assets/banks.json). On select, save
     *  to SharedPreferences and launch immediately. */
    private void showBankingPicker() {
        executor.execute(() -> {
            java.util.List<String[]> installed = new java.util.ArrayList<>();
            int totalCandidates = 0;
            int regionsScanned = 0;
            android.content.pm.PackageManager pm = getPackageManager();
            try {
                java.io.InputStream is = getAssets().open("banks.json");
                java.io.ByteArrayOutputStream baos = new java.io.ByteArrayOutputStream();
                byte[] buf = new byte[4096];
                int n;
                while ((n = is.read(buf)) > 0) baos.write(buf, 0, n);
                is.close();
                JSONObject root = new JSONObject(baos.toString("UTF-8"));
                java.util.Iterator<String> regions = root.keys();
                java.util.Set<String> seen = new java.util.HashSet<>();
                while (regions.hasNext()) {
                    String region = regions.next();
                    if (region.startsWith("_")) continue;
                    Object val = root.opt(region);
                    if (!(val instanceof JSONArray)) continue;
                    regionsScanned++;
                    JSONArray arr = (JSONArray) val;
                    for (int i = 0; i < arr.length(); i++) {
                        JSONObject b = arr.optJSONObject(i);
                        if (b == null) continue;
                        String pkg = b.optString("package", "");
                        String name = b.optString("name", pkg);
                        if (pkg.isEmpty() || !seen.add(pkg)) continue;
                        totalCandidates++;
                        if (pm.getLaunchIntentForPackage(pkg) != null) {
                            installed.add(new String[]{pkg, name});
                            android.util.Log.i("BunnyTasker", "banking-picker: matched " + name + " (" + pkg + ")");
                        }
                    }
                }
                android.util.Log.i("BunnyTasker", "banking-picker: " + regionsScanned + " regions, "
                    + totalCandidates + " unique packages, " + installed.size() + " installed");
            } catch (Exception e) {
                android.util.Log.w("BunnyTasker", "banks.json read failed: " + e.getMessage(), e);
            }
            // Diagnostic: surface the count to the user — silent "no apps" was
            // the original symptom; now tap count + per-package log line +
            // user-visible counter make it obvious whether banks.json was
            // read, how many candidates it had, and what the PackageManager
            // saw as installed.
            final int fTotal = totalCandidates;
            final int fInstalled = installed.size();
            handler.post(() -> {
                if (installed.isEmpty()) {
                    new android.app.AlertDialog.Builder(this)
                        .setTitle("No banking app detected")
                        .setMessage("Scanned banks.json: " + fTotal + " candidate packages.\n"
                            + "PackageManager reports 0 of them installed.\n\n"
                            + "If your bank IS installed, the most likely cause is that "
                            + "Bunny Tasker can't see it through Android's package-visibility "
                            + "filter. Check logcat for `banking-picker:` lines. "
                            + "If your bank isn't in shared/banks.json, send a PR.")
                        .setPositiveButton("OK", null)
                        .show();
                    return;
                }
                CharSequence[] labels = new CharSequence[installed.size()];
                for (int i = 0; i < installed.size(); i++) labels[i] = installed.get(i)[1];
                new android.app.AlertDialog.Builder(this)
                    .setTitle("Choose your banking app")
                    .setItems(labels, (d, which) -> {
                        String[] choice = installed.get(which);
                        prefs.edit().putString("banking_app", choice[0]).apply();
                        // Best-effort mirror to Settings.Global so the Collar's
                        // lock screen (com.focuslock) can read the same value.
                        // Throws SecurityException without WRITE_SECURE_SETTINGS
                        // on consumer installs — caught + logged. Lock-screen
                        // banking on consumer installs is a known follow-up
                        // (needs ContentProvider per Phase 2 of the no-adb pivot).
                        try {
                            Settings.Global.putString(getContentResolver(),
                                "focus_lock_banking_app", choice[0]);
                        } catch (Exception e) {
                            android.util.Log.i("BunnyTasker",
                                "banking_app mirror to Settings.Global denied (consumer install): "
                                    + e.getMessage());
                        }
                        Intent launch = pm.getLaunchIntentForPackage(choice[0]);
                        if (launch != null) {
                            launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                            startActivity(launch);
                        }
                    })
                    .setNegativeButton("Cancel", null)
                    .show();
            });
        });
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        handler.removeCallbacks(poller);
        ntfyRunning = false;
        if (ntfyThread != null) ntfyThread.interrupt();
    }
}

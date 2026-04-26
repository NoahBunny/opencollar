package com.focusctl;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.SharedPreferences;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.view.View;
import android.widget.ArrayAdapter;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Spinner;
import android.widget.TextView;
import android.widget.ToggleButton;

import java.io.BufferedReader;
import java.io.InputStreamReader;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.util.ArrayList;
import java.util.Random;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {

    private String meshUrl = "";
    private static final String[] MODES = {
        "Basic lock", "Negotiation", "Task", "Compliment", "Quiz",
        "Gratitude journal", "Exercise", "Love letter", "Random"
    };
    private static final String[] MODE_KEYS = {
        "basic", "negotiation", "task", "compliment", "quiz",
        "gratitude", "exercise", "love_letter", "random"
    };

    private TextView statusView, tierBadge, balanceDisplay;
    private android.widget.Button btnConfineHome;
    private boolean lastGeofenceActive = false;
    private EditText messageInput, timerInput, taskInput, taskRepsInput, paywallInput, complimentInput;
    private EditText offerCounterInput;
    private TextView offerTextView;
    private LinearLayout offerSection, toySection, deviceCardsContainer;
    private ToggleButton taskRandomize, toggleVibrate, toggleDim, toggleMute, togglePenalty, toggleShame;
    private ToggleButton togglePinNotif;
    private Spinner phoneSpinner, modeSpinner;
    private ExecutorService executor;
    private Handler handler;
    private SharedPreferences prefs;
    private String meshId = "";
    private String authToken = "";
    private boolean vaultMode = false;
    private Runnable statusPoller;
    private Runnable timerTicker;
    private Runnable vaultPoller;

    /**
     * Phase D LocalSnapshot — the controller's in-memory mirror of the most
     * recently decrypted vault blobs. The 7 plaintext-status consumers
     * (startStatusPolling, doUnlockDevice, body-check, refreshInbox, etc.)
     * read from this instead of fetching /api/mesh/{id}/status, so the relay
     * can be flipped to vault_only without breaking Lion's UI.
     *
     * - currentRuntimeJson: last decrypted SLAVE-signed body (serialized)
     * - currentOrdersJson : last decrypted LION-signed body (serialized)
     * - lastSeenVaultVersion: highest blob version we've already processed
     *
     * All fields volatile so the gossip thread can publish without locking
     * and the main thread can read without seeing torn state.
     */
    private static class LocalSnapshot {
        volatile String currentRuntimeJson = null;
        volatile String currentOrdersJson = null;
        volatile long lastSeenVaultVersion = 0L;
    }
    private final LocalSnapshot localSnapshot = new LocalSnapshot();
    private volatile boolean vaultRegistered = false;
    private Thread ntfyThread;
    private volatile boolean ntfyRunning = false;
    private volatile byte[] lionPubDerCache = null;

    /**
     * Multi-bunny infrastructure (Option B — one mesh per bunny, switcher in
     * Lion's Share). The controller holds N bunny slots in SharedPreferences
     * under `bunny_{id}_*` keys, with `bunnies` (JSON array of {id,label}) and
     * `active_bunny_id` tracking the list and selection. Lion's identity
     * (`lion_privkey`, `lion_pubkey`, `app_pin`) is shared across all slots.
     *
     * Instance vars below cache the *active* slot's config so hot paths
     * (api, meshGet, vaultPoll, refreshInbox) don't have to re-read prefs
     * on every tick. setActiveBunny() reloads them.
     */
    private String activeBunnyId = "";
    private String activeBunnyLabel = "";
    private String pairMode = "";
    private String bunnyDirectUrl = "";
    private String bunnyPubkeyB64 = "";

    /** One row in the `bunnies` JSON array. */
    private static class BunnyEntry {
        final String id;
        String label;
        BunnyEntry(String id, String label) { this.id = id; this.label = label; }
    }

    private long timerEndMs = 0;
    private long scheduledAtMs = 0;  // 0 = send immediately
    private boolean isLocked = false;
    private int lastEscapes = 0;
    private int lastPaywall = 0;

    // Tab views
    private View pageSimple, pageAdvanced, pageInbox;
    private Button tabSimple, tabAdvanced, tabInbox;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(getResources().getIdentifier("activity_main", "layout", getPackageName()));

        executor = Executors.newSingleThreadExecutor();
        handler = new Handler(Looper.getMainLooper());
        prefs = getSharedPreferences("focusctl", MODE_PRIVATE);

        // Settings.Global fallback for legacy mesh_url (pre-multi-bunny installs
        // that had the ADB-provisioned url sitting in Settings.Global). This
        // must run BEFORE migrateLegacyBunny() so the migration picks it up.
        if (prefs.getString("mesh_url", "").isEmpty()) {
            try {
                String g = android.provider.Settings.Global.getString(getContentResolver(), "focus_lock_mesh_url");
                if (g != null && !g.isEmpty() && !"null".equals(g)) {
                    prefs.edit().putString("mesh_url", g).apply();
                }
            } catch (Exception e) {}
        }

        // Multi-bunny: migrate legacy single-mesh state into bunny slot "b1"
        // if present, then load whichever slot is currently active into the
        // hot-path instance vars (meshId, meshUrl, authToken, vaultMode,
        // pairMode, bunnyDirectUrl, bunnyPubkeyB64).
        migrateLegacyBunny();
        loadActiveBunny();

        // App PIN lock — protect against bunny getting the phone
        String appPin = prefs.getString("app_pin", "");
        if (!appPin.isEmpty()) {
            showAppPinPrompt(appPin);
        }

        if (meshId.isEmpty()) {
            new Handler(Looper.getMainLooper()).post(this::doSetup);
        }

        statusView = (TextView) findViewById(getId("status"));
        tierBadge = (TextView) findViewById(getId("tier_badge"));
        messageInput = (EditText) findViewById(getId("message_input"));
        timerInput = (EditText) findViewById(getId("timer_input"));
        taskInput = (EditText) findViewById(getId("task_input"));
        taskRepsInput = (EditText) findViewById(getId("task_reps"));
        taskRandomize = (ToggleButton) findViewById(getId("task_randomize"));
        toggleVibrate = (ToggleButton) findViewById(getId("toggle_vibrate"));
        toggleDim = (ToggleButton) findViewById(getId("toggle_dim"));
        toggleMute = (ToggleButton) findViewById(getId("toggle_mute"));
        togglePenalty = (ToggleButton) findViewById(getId("toggle_penalty"));
        toggleShame = (ToggleButton) findViewById(getId("toggle_shame"));
        paywallInput = (EditText) findViewById(getId("paywall_amount"));
        complimentInput = (EditText) findViewById(getId("compliment_prompt"));
        phoneSpinner = (Spinner) findViewById(getId("phone_spinner"));
        modeSpinner = (Spinner) findViewById(getId("mode_spinner"));
        offerSection = (LinearLayout) findViewById(getId("offer_section"));
        offerTextView = (TextView) findViewById(getId("offer_text"));
        offerCounterInput = (EditText) findViewById(getId("offer_counter"));
        toySection = (LinearLayout) findViewById(getId("toy_section"));
        deviceCardsContainer = (LinearLayout) findViewById(getId("device_cards_container"));
        togglePinNotif = (ToggleButton) findViewById(getId("toggle_pin_notif"));
        balanceDisplay = (TextView) findViewById(getId("balance_display"));

        // ── Tab switching (3 tabs) ──
        pageSimple = findViewById(getId("page_simple"));
        pageAdvanced = findViewById(getId("page_advanced"));
        pageInbox = findViewById(getId("page_inbox"));
        tabSimple = (Button) findViewById(getId("tab_simple"));
        tabAdvanced = (Button) findViewById(getId("tab_advanced"));
        tabInbox = (Button) findViewById(getId("tab_inbox"));
        tabSimple.setOnClickListener(v -> selectTab(0));
        tabAdvanced.setOnClickListener(v -> selectTab(1));
        tabInbox.setOnClickListener(v -> selectTab(2));

        // Toggle styling
        ToggleButton[] toggles = {toggleShame, togglePenalty, toggleVibrate, toggleDim, toggleMute, taskRandomize, togglePinNotif};
        for (ToggleButton tb : toggles) {
            if (tb != null) tb.setOnCheckedChangeListener((v, on) -> {
                v.setBackgroundTintList(android.content.res.ColorStateList.valueOf(on ? 0xFF2a2510 : 0xFF1a1a2e));
                v.setTextColor(on ? 0xFFDAA520 : 0xFF666666);
            });
        }

        // Mode spinner
        ArrayAdapter<String> modeAdapter = new ArrayAdapter<>(this, android.R.layout.simple_spinner_item, MODES);
        modeAdapter.setDropDownViewResource(android.R.layout.simple_spinner_dropdown_item);
        modeSpinner.setAdapter(modeAdapter);

        // ── Control tab buttons ──
        findViewById(getId("btn_lock")).setOnClickListener(v -> doLock());
        findViewById(getId("btn_lock")).setOnLongClickListener(v -> { doSetCountdown(); return true; });
        findViewById(getId("btn_unlock")).setOnClickListener(v -> doUnlock());
        findViewById(getId("btn_unlock_device")).setOnClickListener(v -> doUnlockDevice());
        findViewById(getId("btn_task")).setOnClickListener(v -> doTask());
        findViewById(getId("btn_setup")).setOnClickListener(v -> doSetup());
        // App PIN — protect from bunnies
        View btnAppPin = findViewById(getId("btn_app_pin"));
        if (btnAppPin != null) btnAppPin.setOnClickListener(v -> doSetAppPin());
        findViewById(getId("btn_lock_15")).setOnClickListener(v -> doQuickLock(15));
        findViewById(getId("btn_lock_30")).setOnClickListener(v -> doQuickLock(30));
        findViewById(getId("btn_lock_60")).setOnClickListener(v -> doQuickLock(60));
        findViewById(getId("btn_lock_120")).setOnClickListener(v -> doQuickLock(120));
        findViewById(getId("btn_offer_accept")).setOnClickListener(v -> doOfferRespond("accept"));
        findViewById(getId("btn_offer_decline")).setOnClickListener(v -> doOfferRespond("decline"));

        // ── Advanced tab buttons ──
        findViewById(getId("btn_entrap_adv")).setOnClickListener(v -> doEntrap());
        findViewById(getId("btn_clear_paywall")).setOnClickListener(v -> doClearPaywall());
        findViewById(getId("btn_gamble")).setOnClickListener(v -> doGamble());
        findViewById(getId("btn_play_audio")).setOnClickListener(v -> doPlayAudio());
        findViewById(getId("btn_speak")).setOnClickListener(v -> doSpeak());
        findViewById(getId("btn_set_geofence")).setOnClickListener(v -> doSetGeofence());
        btnConfineHome = (android.widget.Button) findViewById(getId("btn_confine_home"));
        btnConfineHome.setOnClickListener(v -> {
            // Toggle: if a geofence is currently active, release it; otherwise
            // confine to current location. Saves a separate UI element while
            // matching the user's mental model ("press once to confine, press
            // again to release").
            if (lastGeofenceActive) {
                doReleaseConfinement();
            } else {
                doConfineHome();
            }
        });
        findViewById(getId("btn_pin_message")).setOnClickListener(v -> doPinMessage());
        findViewById(getId("btn_force_sub")).setOnClickListener(v -> doForceSub());
        try { findViewById(getId("btn_deadline_task")).setOnClickListener(v -> doDeadlineTask()); } catch (Exception e) {}
        try { findViewById(getId("btn_web_remote")).setOnClickListener(v -> doWebRemoteScan()); } catch (Exception e) {}
        try { findViewById(getId("btn_payment_email")).setOnClickListener(v -> doPaymentEmail()); } catch (Exception e) {}
        try { findViewById(getId("btn_vault_nodes")).setOnClickListener(v -> doVaultNodes()); } catch (Exception e) {}
        try { findViewById(getId("btn_bunnies")).setOnClickListener(v -> doBunnies()); } catch (Exception e) {}
        findViewById(getId("btn_start_fine")).setOnClickListener(v -> doStartFine());
        findViewById(getId("btn_stop_fine")).setOnClickListener(v -> doStopFine());
        try { findViewById(getId("btn_release_forever")).setOnClickListener(v -> doReleaseForever()); } catch (Exception e) {}

        // ── Lovense buttons ──
        findViewById(getId("btn_toy_pulse")).setOnClickListener(v -> doToy("vibrate", 5, 3));
        findViewById(getId("btn_toy_reward")).setOnClickListener(v -> doToy("vibrate", 12, 10));
        findViewById(getId("btn_toy_punish")).setOnClickListener(v -> doToy("vibrate", 20, 5));
        findViewById(getId("btn_toy_stop")).setOnClickListener(v -> doToy("vibrate", 0, 0));

        // ── Inbox tab buttons ──
        findViewById(getId("btn_send_message")).setOnClickListener(v -> doSendInboxMessage());
        findViewById(getId("btn_schedule_message")).setOnClickListener(v -> doScheduleMessage());

        // Body check buttons
        findViewById(getId("btn_body_check_start")).setOnClickListener(v -> doBodyCheckStart());
        findViewById(getId("btn_body_check_now")).setOnClickListener(v -> doBodyCheckNow());
        findViewById(getId("btn_body_check_baseline")).setOnClickListener(v -> doBodyCheckBaseline());
        // Balance buttons
        findViewById(getId("btn_clear_balance")).setOnClickListener(v -> doClearBalance());
        findViewById(getId("btn_set_balance")).setOnClickListener(v -> doSetBalance());

        // Quick add $ buttons. The optimistic balance display uses lastPaywall
        // (the most recent confirmed value from the runtime poll), NOT the
        // paywallInput field — that field stages amounts for the *next* Lock
        // order and accumulates per-click for that purpose, so reading from
        // it after a clear-paywall produced a "+50 → showed 250 → settled
        // back to 50" UI flicker. The next runtime poll reconfirms the value
        // (line 422-426).
        for (int[] pair : new int[][]{{getId("btn_add_1"), 1}, {getId("btn_add_5"), 5}, {getId("btn_add_10"), 10}, {getId("btn_add_25"), 25}, {getId("btn_add_50"), 50}}) {
            final int amount = pair[1];
            findViewById(pair[0]).setOnClickListener(v -> {
                int newVal = lastPaywall + amount;
                if (balanceDisplay != null) {
                    balanceDisplay.setText("$" + newVal);
                    balanceDisplay.setTextColor(0xFFFFD700);
                }
                executor.execute(() -> {
                    String r = api("/api/add-paywall", "{\"amount\":\"" + amount + "\"}");
                    if (r != null && r.contains("ok")) handler.post(() -> setStatus("Added $" + amount));
                });
            });
        }

        if (!meshId.isEmpty()) {
            setStatus("Mesh connected");
        }
        // Hide phone spinner (no longer needed — all via mesh relay)
        if (phoneSpinner != null) phoneSpinner.setVisibility(View.GONE);
        startStatusPolling();
    }

    // ── Helpers ──

    private int getId(String name) {
        return getResources().getIdentifier(name, "id", getPackageName());
    }

    private void setStatus(String text) {
        handler.post(() -> statusView.setText(text));
    }

    private void selectTab(int index) {
        View[] pages = {pageSimple, pageAdvanced, pageInbox};
        Button[] tabs = {tabSimple, tabAdvanced, tabInbox};
        for (int i = 0; i < 3; i++) {
            pages[i].setVisibility(i == index ? View.VISIBLE : View.GONE);
            tabs[i].setBackgroundTintList(android.content.res.ColorStateList.valueOf(
                i == index ? 0xFF2a2510 : 0xFF111118));
            tabs[i].setTextColor(i == index ? 0xFFDAA520 : 0xFF555555);
        }
        if (index == 2) { refreshInbox(); markLionRead(); }
    }

    // ── Status Polling ──

    private void startStatusPolling() {
        statusPoller = () -> {
            if (!meshId.isEmpty()) {
                executor.execute(() -> {
                    // Phase D: in vault mode the LocalSnapshot is the source of
                    // truth — vaultPollLoop() does its own decrypted fetch on
                    // its own ticker. Skip the legacy /mesh/status round trip
                    // to avoid hitting a 410 once vault_only is on.
                    if (vaultMode && !"direct".equals(pairMode)) {
                        String snap = localSnapshot.currentRuntimeJson;
                        if (snap != null) {
                            handler.post(() -> updateLiveStatus(snap));
                        }
                        return;
                    }
                    String resp = meshGet("/mesh/status");
                    if (resp != null) {
                        handler.post(() -> updateLiveStatus(resp));
                    }
                });
            }
            handler.postDelayed(statusPoller, 5000);
        };
        handler.postDelayed(statusPoller, 3000);

        // Phase D: vault poll loop runs alongside the status poller.
        // No-op when vaultMode is off or pair_mode is direct.
        vaultPoller = () -> {
            if (vaultMode && !meshId.isEmpty()) {
                executor.execute(this::vaultPollLoop);
            }
            handler.postDelayed(vaultPoller, 5000);
        };
        handler.postDelayed(vaultPoller, 4000);

        // ntfy push subscriber — wakes up immediate refreshInbox + vault poll
        // on Bunny-/server-issued events (new message, edit, delete, lock
        // status change). Without this Lion's Share was poll-only at 5s
        // cadence; vault appends + /messages/* now publish ntfy on the
        // server (focuslock-mail.py) so the chat thread updates within
        // ~1s of any change. Mirrors the Collar's ControlService.ntfySubscribeLoop
        // and Bunny Tasker's MainActivity.ntfySubscribeLoop.
        startNtfySubscriber();

        timerTicker = () -> {
            if (isLocked && timerEndMs > 0) {
                long rem = timerEndMs - System.currentTimeMillis();
                if (rem > 0) {
                    long m = rem / 60000, s = (rem % 60000) / 1000;
                    StringBuilder sb = new StringBuilder();
                    if (!activeBunnyLabel.isEmpty()) sb.append(activeBunnyLabel).append(" | ");
                    sb.append("LOCKED | ").append(m).append("m ").append(s).append("s left");
                    if (lastEscapes > 0) sb.append(" | ").append(lastEscapes).append(" esc");
                    statusView.setText(sb.toString());
                    View bar = findViewById(getId("status_bar"));
                    if (bar != null) bar.setBackgroundColor(0xFFcc8800);
                } else {
                    timerEndMs = 0;
                }
            }
            handler.postDelayed(timerTicker, 1000);
        };
        handler.postDelayed(timerTicker, 1000);
    }

    private boolean parseJsonBool(String json, String key) {
        String search = "\"" + key + "\":";
        int i = json.indexOf(search);
        if (i < 0) return false;
        String rest = json.substring(i + search.length()).trim();
        return rest.startsWith("true");
    }

    private void updateLiveStatus(String json) {
        isLocked = parseJsonBool(json, "locked");
        lastEscapes = parseJsonInt(json, "escapes");
        long timerMs = parseJsonLong(json, "timer_remaining_ms");
        String offer = parseJsonStr(json, "offer");
        String offerStatus = parseJsonStr(json, "offer_status");
        String subTier = parseJsonStr(json, "sub_tier");
        boolean lovenseAvail = parseJsonBool(json, "lovense_available");

        timerEndMs = timerMs > 0 ? System.currentTimeMillis() + timerMs : 0;

        StringBuilder sb = new StringBuilder();
        // Multi-bunny: prepend the active bunny's label so Lion always knows
        // which slot the rest of the status line refers to.
        if (!activeBunnyLabel.isEmpty()) {
            sb.append(activeBunnyLabel).append(" | ");
        }
        if (isLocked) {
            sb.append("LOCKED");
            if (timerMs > 0) {
                long m = timerMs / 60000, s = (timerMs % 60000) / 1000;
                sb.append(" | ").append(m).append("m ").append(s).append("s left");
            }
            if (lastEscapes > 0) sb.append(" | ").append(lastEscapes).append(" esc");
            int reps = parseJsonInt(json, "task_reps");
            int done = parseJsonInt(json, "task_done");
            if (reps > 0) sb.append(" | Rep ").append(done + 1).append("/").append(reps);
            String paywall = parseJsonStr(json, "paywall");
            if (!paywall.isEmpty() && !paywall.equals("0")) sb.append(" | $").append(paywall);
        } else {
            sb.append("UNLOCKED");
            if (!meshId.isEmpty()) sb.append(" | Mesh online");
        }
        // Surface geofence_active so the user sees a persistent indicator
        // that confine-home / set-geofence took effect (previously only the
        // transient setStatus() message on the button click confirmed it,
        // and the next poll wiped that). Field is provided by the Collar's
        // buildRuntimeBodyMap (ControlService.java:959).
        boolean geofenceActive = parseJsonBool(json, "geofence_active");
        if (geofenceActive) {
            String radius = parseJsonStr(json, "geofence_radius");
            sb.append(" | 📍 ");  // 📍
            if (!radius.isEmpty() && !radius.equals("0")) {
                sb.append(radius).append("m");
            } else {
                sb.append("Confined");
            }
        }
        statusView.setText(sb.toString());

        // Toggle the Confine button label based on whether a geofence is set.
        lastGeofenceActive = geofenceActive;
        if (btnConfineHome != null) {
            btnConfineHome.setText(geofenceActive ? "Release Home" : "Confine Home");
        }

        View statusBar = findViewById(getId("status_bar"));
        if (statusBar != null) statusBar.setBackgroundColor(isLocked ? 0xFFcc8800 : 0xFFDAA520);

        // Tier badge
        updateTierBadge(subTier);

        // Bunny balance display
        String paywall = parseJsonStr(json, "paywall");
        try {
            lastPaywall = paywall.isEmpty() ? 0 : Integer.parseInt(paywall);
        } catch (NumberFormatException e) {
            lastPaywall = 0;
        }
        if (balanceDisplay != null) {
            String bal = (paywall.isEmpty() || paywall.equals("0")) ? "$0" : "$" + paywall;
            balanceDisplay.setText(bal);
            balanceDisplay.setTextColor(bal.equals("$0") ? 0xFF44aa44 : 0xFFFFD700);
        }

        // Lovense section visibility
        if (toySection != null) toySection.setVisibility(lovenseAvail ? View.VISIBLE : View.GONE);

        // Fine status
        String fineActive = parseJsonNumStr(json, "fine_active");
        TextView fineStatus = (TextView) findViewById(getId("fine_status"));
        if (fineStatus != null) {
            if ("1".equals(fineActive)) {
                String fineAmt = parseJsonNumStr(json, "fine_amount");
                if (fineAmt.isEmpty()) fineAmt = "?";
                fineStatus.setText("Fine: $" + fineAmt + "/hr \uD83D\uDCB8");
                fineStatus.setVisibility(View.VISIBLE);
            } else {
                fineStatus.setVisibility(View.GONE);
            }
        }

        // Body check status (from mesh, checked periodically)
        String bodyCheckActive = parseJsonNumStr(json, "body_check_active");
        if ("1".equals(bodyCheckActive)) updateBodyCheckStatus();

        // Offer section
        if (!offer.isEmpty() && "pending".equals(offerStatus)) {
            offerSection.setVisibility(View.VISIBLE);
            offerTextView.setText("\"" + offer + "\"");
        } else {
            offerSection.setVisibility(View.GONE);
        }
    }

    private void updateTierBadge(String tier) {
        if (tierBadge == null) return;
        if (tier == null || tier.isEmpty()) {
            tierBadge.setVisibility(View.GONE);
            return;
        }
        tierBadge.setVisibility(View.VISIBLE);
        tierBadge.setText(tier.toUpperCase());
        int bgColor, textColor;
        switch (tier.toLowerCase()) {
            case "gold":   bgColor = 0xFFDAA520; textColor = 0xFF111111; break;
            case "silver": bgColor = 0xFFA0A0A0; textColor = 0xFF111111; break;
            case "bronze": bgColor = 0xFF8B4513; textColor = 0xFFe0e0e0; break;
            default:       bgColor = 0xFF333333; textColor = 0xFFaaaaaa; break;
        }
        GradientDrawable pill = new GradientDrawable();
        pill.setShape(GradientDrawable.RECTANGLE);
        pill.setCornerRadius(24f);
        pill.setColor(bgColor);
        tierBadge.setBackground(pill);
        tierBadge.setTextColor(textColor);
    }

    // ── JSON Parsing ──

    private int parseJsonInt(String json, String key) {
        try {
            int i = json.indexOf("\"" + key + "\":");
            if (i < 0) return 0;
            i = json.indexOf(":", i) + 1;
            int e = i;
            while (e < json.length() && json.charAt(e) != ',' && json.charAt(e) != '}') e++;
            return Integer.parseInt(json.substring(i, e).trim());
        } catch (Exception e) { return 0; }
    }

    private long parseJsonLong(String json, String key) {
        try {
            int i = json.indexOf("\"" + key + "\":");
            if (i < 0) return 0;
            i = json.indexOf(":", i) + 1;
            int e = i;
            while (e < json.length() && json.charAt(e) != ',' && json.charAt(e) != '}') e++;
            return Long.parseLong(json.substring(i, e).trim());
        } catch (Exception e) { return 0; }
    }

    private String parseJsonStr(String json, String key) {
        try {
            // Handle both "key":"val" and "key": "val" (with space)
            String search1 = "\"" + key + "\":\"";
            String search2 = "\"" + key + "\": \"";
            int i = json.indexOf(search1);
            int len = search1.length();
            if (i < 0) { i = json.indexOf(search2); len = search2.length(); }
            if (i < 0) return "";
            i += len;
            int e = json.indexOf("\"", i);
            return e > i ? json.substring(i, e) : "";
        } catch (Exception e) { return ""; }
    }

    private String parseJsonNumStr(String json, String key) {
        try {
            int i = json.indexOf("\"" + key + "\":");
            if (i < 0) return "";
            i = json.indexOf(":", i) + 1;
            int e = i;
            while (e < json.length() && json.charAt(e) != ',' && json.charAt(e) != '}') e++;
            return json.substring(i, e).trim();
        } catch (Exception e) { return ""; }
    }

    // ── Multi-bunny helpers ──
    //
    // Pref layout:
    //   bunnies            : JSON array of {id,label}
    //   active_bunny_id    : string (e.g. "b1")
    //   bunny_{id}_mesh_url, _mesh_id, _auth_token, _invite_code, _pin,
    //                _vault_mode, _pair_mode, _bunny_direct_url,
    //                _bunny_pubkey_b64
    // Global (shared across slots):
    //   lion_privkey, lion_pubkey, lion_privkey_b64, lion_pubkey_b64, app_pin

    private String bunnyKey(String id, String field) {
        return "bunny_" + id + "_" + field;
    }

    private java.util.List<BunnyEntry> listBunnies() {
        java.util.ArrayList<BunnyEntry> out = new java.util.ArrayList<>();
        String raw = prefs.getString("bunnies", "");
        if (raw.isEmpty()) return out;
        try {
            org.json.JSONArray arr = new org.json.JSONArray(raw);
            for (int i = 0; i < arr.length(); i++) {
                org.json.JSONObject o = arr.getJSONObject(i);
                String id = o.optString("id", "");
                String label = o.optString("label", id);
                if (!id.isEmpty()) out.add(new BunnyEntry(id, label));
            }
        } catch (Exception e) {
            android.util.Log.w("bunnies", "listBunnies parse error: " + e.getMessage());
        }
        return out;
    }

    private void saveBunnyList(java.util.List<BunnyEntry> list) {
        org.json.JSONArray arr = new org.json.JSONArray();
        for (BunnyEntry b : list) {
            org.json.JSONObject o = new org.json.JSONObject();
            try {
                o.put("id", b.id);
                o.put("label", b.label);
                arr.put(o);
            } catch (Exception e) {}
        }
        prefs.edit().putString("bunnies", arr.toString()).apply();
    }

    /** Generate a fresh bunny id not already in the list (b1, b2, b3, ...). */
    private String newBunnyId() {
        java.util.List<BunnyEntry> list = listBunnies();
        java.util.HashSet<String> used = new java.util.HashSet<>();
        for (BunnyEntry b : list) used.add(b.id);
        for (int i = 1; i < 1000; i++) {
            String candidate = "b" + i;
            if (!used.contains(candidate)) return candidate;
        }
        return "b" + System.currentTimeMillis();  // fallback, should never happen
    }

    /** Append a new slot with the given label. Returns the new id. */
    private String addBunnySlot(String label) {
        String id = newBunnyId();
        java.util.List<BunnyEntry> list = listBunnies();
        list.add(new BunnyEntry(id, label));
        saveBunnyList(list);
        return id;
    }

    /**
     * Remove a slot and wipe its per-bunny keys. Caller is responsible for
     * picking a new active bunny if the removed one was active.
     */
    private void removeBunnySlot(String id) {
        java.util.List<BunnyEntry> list = listBunnies();
        java.util.ArrayList<BunnyEntry> kept = new java.util.ArrayList<>();
        for (BunnyEntry b : list) {
            if (!b.id.equals(id)) kept.add(b);
        }
        saveBunnyList(kept);

        // Wipe per-bunny keys for the removed slot.
        SharedPreferences.Editor ed = prefs.edit();
        String[] fields = {
            "mesh_url", "mesh_id", "auth_token", "invite_code", "pin",
            "vault_mode", "pair_mode", "bunny_direct_url", "bunny_pubkey_b64"
        };
        for (String f : fields) ed.remove(bunnyKey(id, f));
        ed.apply();
    }

    /** Load the active bunny's config into the instance variables. */
    private void loadActiveBunny() {
        activeBunnyId = prefs.getString("active_bunny_id", "");
        if (activeBunnyId.isEmpty()) {
            // No active bunny — wipe the hot-path vars so api()/meshGet() refuse.
            activeBunnyLabel = "";
            meshUrl = "";
            meshId = "";
            authToken = "";
            vaultMode = false;
            pairMode = "";
            bunnyDirectUrl = "";
            bunnyPubkeyB64 = "";
            return;
        }
        // Find label from the list.
        activeBunnyLabel = activeBunnyId;
        for (BunnyEntry b : listBunnies()) {
            if (b.id.equals(activeBunnyId)) { activeBunnyLabel = b.label; break; }
        }
        meshUrl        = prefs.getString(bunnyKey(activeBunnyId, "mesh_url"), "");
        meshId         = prefs.getString(bunnyKey(activeBunnyId, "mesh_id"), "");
        authToken      = prefs.getString(bunnyKey(activeBunnyId, "auth_token"), "");
        vaultMode      = prefs.getBoolean(bunnyKey(activeBunnyId, "vault_mode"), false);
        pairMode       = prefs.getString(bunnyKey(activeBunnyId, "pair_mode"), "");
        bunnyDirectUrl = prefs.getString(bunnyKey(activeBunnyId, "bunny_direct_url"), "");
        bunnyPubkeyB64 = prefs.getString(bunnyKey(activeBunnyId, "bunny_pubkey_b64"), "");
    }

    /**
     * Switch the active bunny. Resets vault state, clears the local snapshot,
     * and refreshes the UI to reflect the new slot.
     */
    private void setActiveBunny(String id) {
        prefs.edit().putString("active_bunny_id", id).apply();
        loadActiveBunny();

        // Reset vault-side state — the new bunny has its own mesh, its own
        // recipient list, its own blob history.
        vaultRegistered = false;
        localSnapshot.currentRuntimeJson = null;
        localSnapshot.currentOrdersJson = null;
        localSnapshot.lastSeenVaultVersion = 0L;
        // lionPubDerCache stays — Lion's keypair is global.

        // Reset runtime status fields so the UI doesn't show the previous
        // bunny's lock state / timer / escape count during the ~5 second
        // window before the next status poll fills in the new bunny's data.
        // These all self-correct on the next updateLiveStatus(), but without
        // the reset the stale values can leak into button handlers (e.g.
        // doUnlock checking isLocked) during the gap.
        isLocked = false;
        timerEndMs = 0;
        scheduledAtMs = 0;
        lastEscapes = 0;

        // Force-refresh UI on next tick.
        handler.post(() -> {
            setStatus("Switched to " + activeBunnyLabel);
            refreshInbox();
        });
    }

    /**
     * One-shot migration: if `bunnies` is empty but the legacy `mesh_id`
     * pref is set, wrap the legacy single-mesh state as bunny "b1" labeled
     * "bunny" and mark it active. Idempotent — safe to call every boot.
     */
    private void migrateLegacyBunny() {
        java.util.List<BunnyEntry> existing = listBunnies();
        if (!existing.isEmpty()) return;  // already migrated or fresh install

        String legacyMeshId = prefs.getString("mesh_id", "");
        if (legacyMeshId.isEmpty()) return;  // fresh install, nothing to migrate

        String id = "b1";
        java.util.ArrayList<BunnyEntry> list = new java.util.ArrayList<>();
        list.add(new BunnyEntry(id, "bunny"));
        saveBunnyList(list);

        // Copy every legacy key into the b1 slot. Leave the legacy keys in
        // place so a rollback to the previous APK still works.
        SharedPreferences.Editor ed = prefs.edit();
        ed.putString(bunnyKey(id, "mesh_url"),        prefs.getString("mesh_url", ""));
        ed.putString(bunnyKey(id, "mesh_id"),         legacyMeshId);
        ed.putString(bunnyKey(id, "auth_token"),      prefs.getString("auth_token", ""));
        ed.putString(bunnyKey(id, "invite_code"),     prefs.getString("invite_code", ""));
        ed.putString(bunnyKey(id, "pin"),             prefs.getString("pin", ""));
        ed.putBoolean(bunnyKey(id, "vault_mode"),     prefs.getBoolean("vault_mode", false));
        ed.putString(bunnyKey(id, "pair_mode"),       prefs.getString("pair_mode", ""));
        ed.putString(bunnyKey(id, "bunny_direct_url"),prefs.getString("bunny_direct_url", ""));
        ed.putString(bunnyKey(id, "bunny_pubkey_b64"),prefs.getString("bunny_pubkey_b64", ""));
        ed.putString("active_bunny_id", id);
        ed.apply();

        android.util.Log.i("bunnies", "migrated legacy mesh " + legacyMeshId + " to slot " + id);
    }

    // ── HTTP ──

    private String api(String path, String jsonBody) {
        // Direct (serverless) mode: post directly to the bunny's Collar at <ip>:8432.
        // This bypasses any mesh server entirely and works on LAN/Tailscale/VPN.
        // pairMode / bunnyDirectUrl are instance vars loaded from the active
        // bunny slot (see loadActiveBunny).
        if ("direct".equals(pairMode) && !bunnyDirectUrl.isEmpty()) {
            java.util.Map<String, String> sigHeaders = buildDirectSigHeaders(path, jsonBody);
            if (sigHeaders == null) {
                return "{\"error\":\"direct post: missing lion_privkey — re-pair to generate one\"}";
            }
            String r = meshPost(bunnyDirectUrl + path, jsonBody, sigHeaders);
            return r != null ? r : "{\"error\":\"connection failed (direct)\"}";
        }
        // Mesh-server mode: configured?
        if (meshUrl.isEmpty() || meshId.isEmpty() || authToken.isEmpty()) {
            return "{\"error\":\"not configured — run Setup\"}";
        }
        String action = path.replace("/api/", "");

        // Phase D: vault_only write path. The legacy /api/mesh/{id}/order
        // returns 410 once vault_only is flipped on, so when we're in vault
        // mode we encrypt the order itself as a Lion-signed RPC blob and POST
        // it to /vault/{id}/append. The slave's vaultSync decrypts and
        // dispatches via handleMeshOrder (see ControlService.java vaultSync
        // RPC dispatch branch).
        if (vaultMode) {
            return apiVault(action, jsonBody);
        }

        // Mesh-server mode (legacy): proxy through the relay server
        String body = "{\"action\":\"" + action + "\",\"params\":" + jsonBody + "}";
        String r = meshPost(meshUrl + "/api/mesh/" + meshId + "/order", body);
        if (r == null) return "{\"error\":\"connection failed\"}";
        return r;
    }

    /**
     * Phase D vault-mode write path. Encrypts {action, params} as a Lion-signed
     * blob and POSTs to /vault/{mesh_id}/append. The slave decrypts and routes
     * to handleMeshOrder (existing doLock/doUnlock/etc. handlers run unchanged).
     *
     * Replaces the legacy /api/mesh/{id}/order POST entirely in vault mode.
     * Returns the same {"ok": true} / {"error": "..."} envelope as the legacy
     * path so callers don't have to branch.
     */
    private String apiVault(String action, String paramsJson) {
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) {
            return "{\"error\":\"vault: no lion_privkey — re-pair to generate one\"}";
        }
        byte[] pubDer = lionPubDer();
        if (pubDer == null) {
            return "{\"error\":\"vault: could not derive lion pubkey\"}";
        }
        try {
            // Build the RPC body: {action: "...", params: {...}}.
            // Parse paramsJson as a JSONObject and convert to a Map so the
            // canonical-JSON serializer produces stable byte order for signing.
            java.util.TreeMap<String, Object> body = new java.util.TreeMap<>();
            body.put("action", action);
            java.util.Map<String, Object> paramsMap;
            try {
                org.json.JSONObject paramsObj = new org.json.JSONObject(paramsJson);
                paramsMap = VaultCrypto.jsonToMap(paramsObj);
            } catch (Exception e) {
                paramsMap = new java.util.TreeMap<>();
            }
            body.put("params", paramsMap);

            // Fetch the recipient list. We must include ourselves so future
            // vaultPollLoop ticks can read our own blob back into
            // LocalSnapshot.currentOrdersJson if we ever need to.
            String nodesJson = meshGet("/vault/" + meshId + "/nodes");
            if (nodesJson == null) {
                return "{\"error\":\"vault: could not fetch /vault/" + meshId + "/nodes\"}";
            }
            org.json.JSONArray nodesArr = new org.json.JSONObject(nodesJson).optJSONArray("nodes");
            if (nodesArr == null || nodesArr.length() == 0) {
                return "{\"error\":\"vault: no recipients approved — open Vault Nodes and approve a slave first\"}";
            }
            java.util.ArrayList<VaultCrypto.NodePubkey> recipients = new java.util.ArrayList<>();
            for (int i = 0; i < nodesArr.length(); i++) {
                org.json.JSONObject node = nodesArr.getJSONObject(i);
                String nid = node.optString("node_id", "");
                String npub = node.optString("node_pubkey", "");
                if (!nid.isEmpty() && !npub.isEmpty()) {
                    recipients.add(new VaultCrypto.NodePubkey(nid, npub));
                }
            }
            if (recipients.isEmpty()) {
                return "{\"error\":\"vault: no recipients with valid pubkeys\"}";
            }

            // Pick a version greater than the highest we've seen and retry on
            // 409 conflicts (slave or another writer raced us). With the slave
            // pushing runtime blobs every ~30s, the gap between our cached
            // lastSeenVaultVersion and the server's actual high-water mark
            // can be wide enough that the FIRST attempt almost always 409s,
            // so we resync from the server before attempting at all.
            long version = Math.max(localSnapshot.lastSeenVaultVersion, 0L) + 1;
            try {
                String preSinceResp = meshGet("/vault/" + meshId + "/since/0");
                if (preSinceResp != null && !preSinceResp.isEmpty()) {
                    long preCurrent = new org.json.JSONObject(preSinceResp).optLong("current_version", -1);
                    if (preCurrent >= version) version = preCurrent + 1;
                }
            } catch (Exception e) { /* fall through with cached version */ }

            String lastError = "unknown";
            // Bumped from 3 → 5: under heavy slave heartbeat traffic the
            // first 1-2 attempts can still race even with the pre-resync.
            for (int attempt = 0; attempt < 5; attempt++) {
                long createdAt = System.currentTimeMillis();
                java.util.Map<String, Object> blob = VaultCrypto.encryptOrders(
                    meshId, (int) version, createdAt, body, recipients);
                String signature = VaultCrypto.signBlob(blob, lionPriv);
                blob.put("signature", signature);
                String blobJson = new String(VaultCrypto.canonicalJson(blob));

                String resp = meshPost(meshUrl + "/vault/" + meshId + "/append", blobJson);
                // Tightened success check: server returns {"ok": true, "version": N}.
                // Match the explicit "ok":true to avoid false positives from
                // error bodies that might happen to contain the substring "ok".
                if (resp != null && (resp.contains("\"ok\":true") || resp.contains("\"ok\": true"))) {
                    android.util.Log.i("vault", "rpc " + action + " posted v" + version
                        + " (" + recipients.size() + " slots, " + blobJson.length() + " bytes)");
                    // Optimistically advance lastSeenVaultVersion so the next
                    // poll tick won't try to "discover" our own blob and
                    // potentially race itself into another conflict.
                    if (version > localSnapshot.lastSeenVaultVersion) {
                        localSnapshot.lastSeenVaultVersion = version;
                    }
                    return resp;
                }
                lastError = (resp == null ? "null response" : resp);

                // 409 → server returns {"error": ..., "current_version": N}.
                // Now that meshPost preserves error bodies we can read this
                // directly instead of doing a second meshGet round-trip.
                long nextVersion = version + 1;
                if (resp != null) {
                    try {
                        long currentFromError = new org.json.JSONObject(resp).optLong("current_version", -1);
                        if (currentFromError >= 0) nextVersion = currentFromError + 1;
                    } catch (Exception e) { /* not a JSON body, fall through */ }
                }
                // Belt-and-suspenders: if the error didn't include
                // current_version (e.g. 5xx, network error), poll /since/0
                // to discover the actual high-water mark.
                if (nextVersion == version + 1) {
                    String sinceResp = meshGet("/vault/" + meshId + "/since/0");
                    if (sinceResp != null && !sinceResp.isEmpty()) {
                        try {
                            long current = new org.json.JSONObject(sinceResp).optLong("current_version", -1);
                            if (current >= version) nextVersion = current + 1;
                        } catch (Exception e) { /* leave nextVersion as version+1 */ }
                    }
                }
                version = nextVersion;
            }
            android.util.Log.w("vault", "rpc " + action + " gave up after 5 attempts: " + lastError);
            return "{\"error\":\"vault: append failed after 5 attempts (" + lastError.replace("\"", "'") + ")\"}";
        } catch (Exception e) {
            android.util.Log.w("vault", "apiVault error: " + e.getMessage());
            return "{\"error\":\"vault: " + e.getMessage() + "\"}";
        }
    }

    // ── Phase D: vault read side (LocalSnapshot poll loop) ──

    /**
     * Derive Lion's RSA public key DER bytes from the existing private key in
     * SharedPreferences. Cached after the first successful call so subsequent
     * polls don't repeat the KeyFactory work. Returns null if no privkey or
     * derivation fails.
     */
    private byte[] lionPubDer() {
        if (lionPubDerCache != null) return lionPubDerCache;
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return null;
        try {
            String stripped = lionPriv
                .replace("-----BEGIN PRIVATE KEY-----", "")
                .replace("-----END PRIVATE KEY-----", "")
                .replace("-----BEGIN RSA PRIVATE KEY-----", "")
                .replace("-----END RSA PRIVATE KEY-----", "")
                .replaceAll("[\\s|]+", "");
            byte[] privDer = android.util.Base64.decode(stripped, android.util.Base64.DEFAULT);
            java.security.interfaces.RSAPrivateCrtKey privKey =
                (java.security.interfaces.RSAPrivateCrtKey)
                java.security.KeyFactory.getInstance("RSA")
                    .generatePrivate(new java.security.spec.PKCS8EncodedKeySpec(privDer));
            java.security.spec.RSAPublicKeySpec pubSpec =
                new java.security.spec.RSAPublicKeySpec(
                    privKey.getModulus(), privKey.getPublicExponent());
            java.security.PublicKey pubKey =
                java.security.KeyFactory.getInstance("RSA").generatePublic(pubSpec);
            lionPubDerCache = pubKey.getEncoded();
            return lionPubDerCache;
        } catch (Exception e) {
            android.util.Log.w("vault", "lionPubDer derive failed: " + e.getMessage());
            return null;
        }
    }

    /**
     * Phase D: register Lion as a vault recipient so subsequent slave-signed
     * runtime blobs encrypt a slot for the controller. Idempotent: checks
     * /vault/{id}/nodes first and skips if our pubkey is already approved.
     * Re-attempted on every vault poll tick until successful (so transient
     * network errors don't permanently disable the snapshot).
     */
    private void vaultEnsureSelfRegistered() {
        if (vaultRegistered) return;
        if (!vaultMode || meshUrl.isEmpty() || meshId.isEmpty()) return;
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return;
        byte[] pubDer = lionPubDer();
        if (pubDer == null) return;

        try {
            String pubB64 = android.util.Base64.encodeToString(pubDer, android.util.Base64.NO_WRAP);

            // Idempotent check — already approved?
            String nodesJson = meshGet("/vault/" + meshId + "/nodes");
            if (nodesJson != null) {
                org.json.JSONArray nodesArr = new org.json.JSONObject(nodesJson).optJSONArray("nodes");
                if (nodesArr != null) {
                    String myCleaned = pubB64.replaceAll("\\s", "");
                    for (int i = 0; i < nodesArr.length(); i++) {
                        String npub = nodesArr.getJSONObject(i).optString("node_pubkey", "");
                        if (npub.replaceAll("\\s", "").equals(myCleaned)) {
                            vaultRegistered = true;
                            android.util.Log.i("vault", "controller already registered as vault recipient");
                            return;
                        }
                    }
                }
            }

            // Build a Lion-signed register-node payload and POST it.
            // Format mirrors postSignedVaultPayload(), but inlined here so the
            // controller can self-register without faking a UI dialog approval.
            java.util.TreeMap<String, Object> payload = new java.util.TreeMap<>();
            payload.put("node_id", "controller");
            payload.put("node_type", "controller");
            payload.put("node_pubkey", pubB64);
            String signature = VaultCrypto.signBlob(payload, lionPriv);
            payload.put("signature", signature);
            String body = new String(VaultCrypto.canonicalJson(payload));

            String resp = meshPost(meshUrl + "/vault/" + meshId + "/register-node", body);
            if (resp != null && resp.contains("\"ok\"")) {
                vaultRegistered = true;
                android.util.Log.i("vault", "controller self-registered as vault recipient");
            } else {
                android.util.Log.w("vault", "self-register POST failed: " + resp);
            }
        } catch (Exception e) {
            android.util.Log.w("vault", "self-register error: " + e.getMessage());
        }
    }

    /**
     * Phase D: poll the vault for new blobs since lastSeenVaultVersion, decrypt
     * each into LocalSnapshot, and dispatch UI updates for runtime blobs.
     *
     * Classification: try Lion's pubkey first (cheap, common case for orders),
     * fall back to iterating /vault/{id}/nodes for slave-signed blobs.
     *
     * Direct (LAN) mode does NOT call this — direct mode talks straight to
     * the bunny's Collar HTTP server, bypassing the vault entirely.
     */
    private void vaultPollLoop() {
        if (!vaultMode) return;
        if (meshUrl.isEmpty() || meshId.isEmpty()) return;
        if ("direct".equals(pairMode)) return;

        // Make sure Lion is a vault recipient first; otherwise our slot won't
        // exist in any of the blobs we're about to fetch and decrypt is a no-op.
        vaultEnsureSelfRegistered();
        if (!vaultRegistered) return;

        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return;
        byte[] pubDer = lionPubDer();
        if (pubDer == null) return;

        try {
            long since = localSnapshot.lastSeenVaultVersion;
            String resp = meshGet("/vault/" + meshId + "/since/" + since);
            if (resp == null) return;
            org.json.JSONObject root = new org.json.JSONObject(resp);
            org.json.JSONArray blobs = root.optJSONArray("blobs");
            if (blobs == null || blobs.length() == 0) return;

            // Cache registered node pubkeys for signer lookup, split by role.
            // P6.5: relay-signed blobs are admin orders (same as Lion-signed);
            // slave/desktop-signed blobs are runtime state pushes.
            java.util.ArrayList<String> relayPubkeys = new java.util.ArrayList<>();
            java.util.ArrayList<String> slavePubkeys = new java.util.ArrayList<>();
            String nodesJson = meshGet("/vault/" + meshId + "/nodes");
            if (nodesJson != null) {
                org.json.JSONArray nodesArr = new org.json.JSONObject(nodesJson).optJSONArray("nodes");
                if (nodesArr != null) {
                    String myPubB64 = android.util.Base64.encodeToString(pubDer, android.util.Base64.NO_WRAP)
                        .replaceAll("\\s", "");
                    for (int i = 0; i < nodesArr.length(); i++) {
                        org.json.JSONObject node = nodesArr.getJSONObject(i);
                        String npub = node.optString("node_pubkey", "");
                        String ntype = node.optString("node_type", "");
                        if (npub.replaceAll("\\s", "").equals(myPubB64)) continue;
                        if (npub.isEmpty()) continue;
                        if ("relay".equals(ntype)) {
                            relayPubkeys.add(npub);
                        } else {
                            slavePubkeys.add(npub);
                        }
                    }
                }
            }

            String lionPubB64 = android.util.Base64.encodeToString(pubDer, android.util.Base64.NO_WRAP);
            long highestSeen = since;
            boolean runtimeChanged = false;

            for (int i = 0; i < blobs.length(); i++) {
                org.json.JSONObject blobJson = blobs.getJSONObject(i);
                long version = blobJson.optLong("version", 0);
                if (version > highestSeen) highestSeen = version;
                java.util.Map<String, Object> blobMap = VaultCrypto.jsonToMap(blobJson);

                // Decrypt our slot. If we don't have one, this isn't our blob.
                String bodyJson = VaultCrypto.decryptBody(blobMap, lionPriv, pubDer);
                if (bodyJson == null) continue;

                // Classify by signer (P6.5: relay-signed = orders, same as Lion).
                if (VaultCrypto.verifySignature(blobMap, lionPubB64)) {
                    localSnapshot.currentOrdersJson = bodyJson;
                } else {
                    boolean matched = false;
                    // Check relay pubkeys first — relay admin orders are orders, not runtime
                    for (String rpub : relayPubkeys) {
                        if (VaultCrypto.verifySignature(blobMap, rpub)) {
                            localSnapshot.currentOrdersJson = bodyJson;
                            matched = true;
                            break;
                        }
                    }
                    if (!matched) {
                        for (String spub : slavePubkeys) {
                            if (VaultCrypto.verifySignature(blobMap, spub)) {
                                localSnapshot.currentRuntimeJson = bodyJson;
                                runtimeChanged = true;
                                matched = true;
                                break;
                            }
                        }
                    }
                    if (!matched) {
                        android.util.Log.w("vault", "blob v" + version + " has no matching signer pubkey");
                    }
                }
            }

            localSnapshot.lastSeenVaultVersion = highestSeen;

            if (runtimeChanged) {
                // Drive the UI from the freshly-decrypted runtime body, mimicking
                // what the legacy /mesh/status poll did.
                final String json = localSnapshot.currentRuntimeJson;
                handler.post(() -> updateLiveStatus(json));
            }
        } catch (Exception e) {
            android.util.Log.w("vault", "poll loop error: " + e.getMessage());
        }
    }

    // ── Vault node approval dialog (Phase C — see docs/VAULT-DESIGN.md §7.3) ──
    //
    // Lion polls /vault/{mesh_id}/nodes-pending for slave-initiated registration
    // requests, then either Approves (signs a register-node payload) or Denies
    // (signs a reject-node-request payload). Approved nodes appear in /nodes
    // and become recipients of subsequent slave-signed runtime blobs.

    // ── Web Remote: scan QR code to approve a web session ──
    private static final int QR_SCAN_REQUEST = 9001;
    // ── Pair Direct: scan Bunny Tasker's pair-QR to fill IP + fingerprint ──
    private static final int PAIR_QR_SCAN_REQUEST = 9002;
    // Pending pair-QR fields — populated by the PAIR_QR_SCAN_REQUEST handler,
    // consumed + cleared by the next doPairDirect() dialog open.
    private String pendingPairIp = "";
    private String pendingPairPort = "";
    private String pendingPairFp = "";

    private void doWebRemoteScan() {
        // Try launching a QR scanner via Intent (ZXing Barcode Scanner, Google Lens, etc.)
        try {
            android.content.Intent scanIntent = new android.content.Intent("com.google.zxing.client.android.SCAN");
            scanIntent.putExtra("SCAN_MODE", "QR_CODE_MODE");
            startActivityForResult(scanIntent, QR_SCAN_REQUEST);
            return;
        } catch (Exception e) {
            // No ZXing-compatible scanner installed
        }
        // Fallback: prompt user to use their camera app
        new AlertDialog.Builder(this)
            .setTitle("Scan Web QR Code")
            .setMessage("Open your camera app and point it at the QR code on the Lion's Share web page.\n\n"
                + "Your camera will detect the QR code and offer to open the link. Tap it to approve the web session.\n\n"
                + "Or install a QR scanner app (e.g., \"QR & Barcode Scanner\") for in-app scanning.")
            .setPositiveButton("Open Camera", (d, w) -> {
                try {
                    android.content.Intent cam = new android.content.Intent(android.provider.MediaStore.INTENT_ACTION_STILL_IMAGE_CAMERA);
                    startActivity(cam);
                } catch (Exception e) { setStatus("Camera not available"); }
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, android.content.Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode == QR_SCAN_REQUEST && resultCode == RESULT_OK && data != null) {
            String scannedUrl = data.getStringExtra("SCAN_RESULT");
            if (scannedUrl != null && scannedUrl.contains("/web-login")) {
                // Extract session_id from URL query param "s"
                String sessionId = "";
                try {
                    android.net.Uri uri = android.net.Uri.parse(scannedUrl);
                    sessionId = uri.getQueryParameter("s");
                } catch (Exception e) {}
                if (sessionId == null || sessionId.isEmpty()) {
                    setStatus("Invalid QR code (no session ID)");
                    return;
                }
                // Sign session_id with Lion's RSA private key and POST approval
                String lionPriv = prefs.getString("lion_privkey", "");
                if (lionPriv.isEmpty()) {
                    setStatus("No Lion private key — re-pair first");
                    return;
                }
                final String sid = sessionId;
                setStatus("Signing session approval...");
                executor.submit(() -> {
                    try {
                        // Sign {"session_id": "<sid>"} with Lion's privkey
                        java.util.TreeMap<String, Object> payload = new java.util.TreeMap<>();
                        payload.put("session_id", sid);
                        String signature = VaultCrypto.signBlob(payload, lionPriv);
                        // POST to relay server
                        String relayUrl = meshUrl.isEmpty() ? scannedUrl.replaceAll("/web-login.*", "") : meshUrl;
                        String body = "{\"action\":\"approve\",\"session_id\":\"" + sid
                            + "\",\"signature\":\"" + signature + "\"}";
                        HttpURLConnection conn = (HttpURLConnection)
                            new URL(relayUrl + "/admin/web-session").openConnection();
                        conn.setRequestMethod("POST");
                        conn.setDoOutput(true);
                        conn.setConnectTimeout(10000);
                        conn.setReadTimeout(10000);
                        conn.setRequestProperty("Content-Type", "application/json");
                        conn.getOutputStream().write(body.getBytes("UTF-8"));
                        int code = conn.getResponseCode();
                        conn.disconnect();
                        runOnUiThread(() -> {
                            if (code == 200) {
                                setStatus("Web session approved!");
                            } else if (code == 403) {
                                setStatus("Signature rejected — wrong key?");
                            } else {
                                setStatus("Approval failed (HTTP " + code + ")");
                            }
                        });
                    } catch (Exception e) {
                        runOnUiThread(() -> setStatus("Approval failed: " + e.getMessage()));
                    }
                });
            } else {
                setStatus("Not a Lion's Share QR code");
            }
        } else if (requestCode == PAIR_QR_SCAN_REQUEST && resultCode == RESULT_OK && data != null) {
            // Bunny Tasker's direct-pair QR — payload shape (per
            // PairingManager.buildQrPayload): {"t":"fl","f":<fp>,"l":<lan>,"s":<ts>,"p":<port>}
            String scanned = data.getStringExtra("SCAN_RESULT");
            if (scanned == null || scanned.isEmpty()) {
                setStatus("Scan cancelled");
                return;
            }
            try {
                org.json.JSONObject qr = new org.json.JSONObject(scanned);
                if (!"fl".equals(qr.optString("t"))) {
                    setStatus("Not a FocusLock pair QR");
                    return;
                }
                String lan = qr.optString("l", "");
                String ts = qr.optString("s", "");
                String fp = qr.optString("f", "");
                int port = qr.optInt("p", 8432);
                // Prefer LAN IP; Tailscale as fallback. Lion can always edit.
                String ip = !lan.isEmpty() ? lan : ts;
                if (ip.isEmpty()) {
                    setStatus("Pair QR missing IP");
                    return;
                }
                pendingPairIp = ip;
                pendingPairPort = String.valueOf(port);
                pendingPairFp = fp;
                setStatus("QR scanned — verify fingerprint");
                doPairDirect();  // re-open with fields pre-filled
            } catch (org.json.JSONException e) {
                setStatus("Pair QR parse failed: " + e.getMessage());
            }
        }
    }

    /**
     * Multi-bunny switcher dialog. Shows all bunny slots as a radio list, lets
     * the user switch active, rename inline, add a new bunny (which opens the
     * setup flow), or remove a slot.
     *
     * Guards:
     *   - Can't remove the currently active bunny (must switch first)
     *   - Can't remove the last remaining bunny (would leave an unconfigured app)
     */
    private void doBunnies() {
        java.util.List<BunnyEntry> bunnies = listBunnies();

        // Helper: dp → px for this device density. All programmatic UI sizes
        // below MUST be scaled with this, otherwise the Bunnies dialog buttons
        // render at raw-pixel height (≈16dp on 480dpi screens = invisible).
        final float density = getResources().getDisplayMetrics().density;

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding((int)(16*density), (int)(12*density), (int)(16*density), (int)(8*density));
        root.setBackgroundColor(0xFF0a0a14);

        if (bunnies.isEmpty()) {
            TextView empty = new TextView(this);
            empty.setText("No bunnies configured yet. Tap 'Add Bunny' to create one.");
            empty.setTextColor(0xFFaaaaaa);
            empty.setPadding(0, 8, 0, 16);
            root.addView(empty);
        }

        for (BunnyEntry b : bunnies) {
            final BunnyEntry bb = b;
            LinearLayout row = new LinearLayout(this);
            row.setOrientation(LinearLayout.HORIZONTAL);
            row.setPadding(0, (int)(10*density), 0, (int)(10*density));

            final boolean isActive = bb.id.equals(activeBunnyId);
            TextView dot = new TextView(this);
            dot.setText(isActive ? "\u25cf " : "\u25cb ");  // ● vs ○
            dot.setTextColor(isActive ? 0xFFDAA520 : 0xFF555555);
            dot.setTextSize(20);
            LinearLayout.LayoutParams dotLp = new LinearLayout.LayoutParams(
                LinearLayout.LayoutParams.WRAP_CONTENT,
                LinearLayout.LayoutParams.WRAP_CONTENT);
            row.addView(dot, dotLp);

            TextView label = new TextView(this);
            label.setText(bb.label + "  (" + bb.id + ")");
            label.setTextColor(isActive ? 0xFFDAA520 : 0xFFe0e0e0);
            label.setTextSize(16);
            LinearLayout.LayoutParams labelLp = new LinearLayout.LayoutParams(
                0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
            label.setPadding((int)(8*density), 0, (int)(8*density), 0);
            row.addView(label, labelLp);

            // Tap the row → switch active
            row.setOnClickListener(v -> {
                if (bb.id.equals(activeBunnyId)) return;
                setActiveBunny(bb.id);
            });
            // Long-press the row → rename
            row.setOnLongClickListener(v -> {
                doBunnyRename(bb);
                return true;
            });
            root.addView(row);

            // Remove button (skip for active slot and for the last remaining slot)
            if (!isActive && bunnies.size() > 1) {
                Button rm = new Button(this);
                rm.setText("Remove " + bb.label);
                rm.setTextSize(12);
                rm.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF2a0a0a));
                rm.setTextColor(0xFFcc4444);
                rm.setOnClickListener(v -> doBunnyRemove(bb));
                LinearLayout.LayoutParams rmLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT,
                    LinearLayout.LayoutParams.WRAP_CONTENT);
                rmLp.bottomMargin = (int)(8*density);
                root.addView(rm, rmLp);
            }
        }

        Button add = new Button(this);
        add.setText("+ Add Bunny");
        add.setTextSize(15);
        add.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF1a1a2e));
        add.setTextColor(0xFFDAA520);
        add.setOnClickListener(v -> {
            // Adding a bunny == running the setup flow. createMesh/pairDirect
            // already handle addBunnySlot() + setActiveBunny() internally, so
            // we just open the setup dialog here.
            doSetup();
        });
        LinearLayout.LayoutParams addLp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT);
        addLp.topMargin = (int)(16*density);
        addLp.bottomMargin = (int)(8*density);
        root.addView(add, addLp);

        new AlertDialog.Builder(this)
            .setTitle("\uD83D\uDC07 Bunnies")
            .setView(root)
            .setNegativeButton("Close", null)
            .show();
    }

    /** Rename the given bunny slot (long-press handler). */
    private void doBunnyRename(BunnyEntry target) {
        EditText input = new EditText(this);
        input.setText(target.label);
        input.setTextColor(0xFFe0e0e0);
        input.setHintTextColor(0xFF555555);
        input.setBackgroundColor(0xFF111118);
        input.setPadding(24, 20, 24, 20);
        new AlertDialog.Builder(this)
            .setTitle("Rename " + target.id)
            .setView(input)
            .setPositiveButton("Rename", (d, w) -> {
                String newLabel = input.getText().toString().trim();
                if (newLabel.isEmpty()) return;
                java.util.List<BunnyEntry> list = listBunnies();
                for (BunnyEntry b : list) {
                    if (b.id.equals(target.id)) b.label = newLabel;
                }
                saveBunnyList(list);
                if (target.id.equals(activeBunnyId)) activeBunnyLabel = newLabel;
                setStatus("Renamed to " + newLabel);
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Confirm + remove a non-active bunny slot. */
    private void doBunnyRemove(BunnyEntry target) {
        new AlertDialog.Builder(this)
            .setTitle("Remove " + target.label + "?")
            .setMessage("This wipes the mesh config for this bunny on this device. "
                + "The mesh itself on the server is untouched — you can re-add with the invite code.")
            .setPositiveButton("Remove", (d, w) -> {
                removeBunnySlot(target.id);
                setStatus("Removed " + target.label);
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doVaultNodes() {
        if (meshUrl.isEmpty() || meshId.isEmpty()) {
            setStatus("Vault: no mesh configured");
            return;
        }
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) {
            new AlertDialog.Builder(this)
                .setTitle("Vault Nodes")
                .setMessage("Cannot manage vault nodes — Lion privkey is not on this device.\n\nPair via QR or generate a Lion key first.")
                .setPositiveButton("OK", null)
                .show();
            return;
        }

        // Container the dialog will display. Refreshes replace its contents in place.
        final LinearLayout container = new LinearLayout(this);
        container.setOrientation(LinearLayout.VERTICAL);
        container.setPadding(24, 16, 24, 16);

        final TextView statusLine = new TextView(this);
        statusLine.setTextColor(0xFF888888);
        statusLine.setTextSize(11);
        statusLine.setText("Loading…");
        container.addView(statusLine);

        // Auto-accept toggle row — Lion-signed flag stored on the server.
        // While ON, register-node-request goes straight to approved. Key
        // rotation (existing node_id, new pubkey) still requires a manual
        // approve to close the takeover vector at docs/VAULT-DESIGN.md:266.
        final TextView autoAcceptLabel = new TextView(this);
        autoAcceptLabel.setTextColor(0xFFcccccc);
        autoAcceptLabel.setTextSize(13);
        autoAcceptLabel.setText("Auto-accept new nodes (off)");
        autoAcceptLabel.setPadding(0, 16, 0, 4);
        container.addView(autoAcceptLabel);
        final TextView autoAcceptHint = new TextView(this);
        autoAcceptHint.setTextColor(0xFF888888);
        autoAcceptHint.setTextSize(10);
        autoAcceptHint.setText("New devices added while ON skip the approval queue. Tap to toggle.");
        container.addView(autoAcceptHint);
        autoAcceptLabel.setOnClickListener(v -> {
            executor.execute(() -> {
                // Toggle by inferring current state from the label text — saves
                // a fetch round-trip; the response from /auto-accept tells us
                // the new state and we update the label from that.
                boolean curOn = autoAcceptLabel.getText().toString().endsWith("(on)");
                String newState = curOn ? "off" : "on";
                long ts = System.currentTimeMillis();
                String payload = meshId + "|auto-accept|" + newState + "|" + ts;
                String sig;
                try { sig = VaultCrypto.signString(payload, lionPriv); }
                catch (Exception e) {
                    handler.post(() -> autoAcceptHint.setText("Sign failed: " + e.getMessage()));
                    return;
                }
                org.json.JSONObject body = new org.json.JSONObject();
                try {
                    body.put("state", newState);
                    body.put("ts", ts);
                    body.put("signature", sig);
                } catch (Exception e) { return; }
                String resp = meshPost(meshUrl + "/api/mesh/" + meshId + "/auto-accept", body.toString());
                final boolean okOn = resp != null && resp.contains("\"auto_accept_nodes\":true");
                final boolean okOff = resp != null && resp.contains("\"auto_accept_nodes\":false");
                handler.post(() -> {
                    if (okOn) {
                        autoAcceptLabel.setText("Auto-accept new nodes (on)");
                        autoAcceptHint.setText("Auto-accept ENABLED. Disable when done onboarding.");
                    } else if (okOff) {
                        autoAcceptLabel.setText("Auto-accept new nodes (off)");
                        autoAcceptHint.setText("Auto-accept disabled. New devices land in the pending queue.");
                    } else {
                        autoAcceptHint.setText("Toggle failed: " + (resp == null ? "no response" : resp));
                    }
                });
            });
        });

        final android.widget.ScrollView scroll = new android.widget.ScrollView(this);
        final LinearLayout list = new LinearLayout(this);
        list.setOrientation(LinearLayout.VERTICAL);
        scroll.addView(list);
        container.addView(scroll);

        final AlertDialog dialog = new AlertDialog.Builder(this)
            .setTitle("\uD83D\uDD12 Vault Nodes")
            .setView(container)
            .setNegativeButton("Close", null)
            .setNeutralButton("Refresh", null)  // overridden below to keep dialog open
            .create();

        // Refresh hook — fetches /nodes + /nodes-pending and rebuilds the list
        final Runnable refresh = () -> {
            statusLine.setText("Loading…");
            list.removeAllViews();
            executor.execute(() -> {
                String pendingJson = meshGet("/vault/" + meshId + "/nodes-pending");
                String nodesJson = meshGet("/vault/" + meshId + "/nodes");
                handler.post(() -> rebuildVaultNodeList(list, statusLine, nodesJson, pendingJson, refreshAfter(dialog)));
            });
        };

        dialog.setOnShowListener(d -> {
            Button refreshBtn = dialog.getButton(AlertDialog.BUTTON_NEUTRAL);
            if (refreshBtn != null) {
                refreshBtn.setOnClickListener(v -> refresh.run());
            }
            refresh.run();
        });
        dialog.show();
    }

    /** Returns a Runnable that re-runs the dialog refresh. Used by approve/deny callbacks. */
    private Runnable refreshAfter(final AlertDialog dialog) {
        return () -> {
            // The dialog's neutral button click handler is the refresh action.
            Button btn = dialog.getButton(AlertDialog.BUTTON_NEUTRAL);
            if (btn != null) btn.performClick();
        };
    }

    private void rebuildVaultNodeList(LinearLayout list, TextView statusLine,
                                      String nodesJson, String pendingJson,
                                      Runnable onChanged) {
        list.removeAllViews();
        int approvedCount = 0;
        int pendingCount = 0;
        try {
            // ── Approved nodes (read-only) ──
            if (nodesJson != null) {
                org.json.JSONArray nodes = new org.json.JSONObject(nodesJson).optJSONArray("nodes");
                if (nodes != null && nodes.length() > 0) {
                    TextView header = new TextView(this);
                    header.setText("APPROVED (" + nodes.length() + ")");
                    header.setTextColor(0xFF66cccc);
                    header.setTextSize(10);
                    header.setPadding(0, 8, 0, 4);
                    list.addView(header);
                    for (int i = 0; i < nodes.length(); i++) {
                        org.json.JSONObject n = nodes.getJSONObject(i);
                        list.addView(buildApprovedRow(n.optString("node_id", "?"),
                            n.optString("node_type", "?"),
                            n.optString("node_pubkey", "")));
                        approvedCount++;
                    }
                }
            }

            // ── Pending registration requests ──
            if (pendingJson != null) {
                org.json.JSONArray pending = new org.json.JSONObject(pendingJson).optJSONArray("pending");
                if (pending != null && pending.length() > 0) {
                    TextView header = new TextView(this);
                    header.setText("PENDING (" + pending.length() + ")");
                    header.setTextColor(0xFFccaa44);
                    header.setTextSize(10);
                    header.setPadding(0, 16, 0, 4);
                    list.addView(header);
                    for (int i = 0; i < pending.length(); i++) {
                        org.json.JSONObject n = pending.getJSONObject(i);
                        list.addView(buildPendingRow(
                            n.optString("node_id", "?"),
                            n.optString("node_type", "?"),
                            n.optString("node_pubkey", ""),
                            n.optLong("requested_at", 0),
                            onChanged));
                        pendingCount++;
                    }
                }
            }
        } catch (Exception e) {
            statusLine.setText("Parse error: " + e.getMessage());
            return;
        }

        if (approvedCount == 0 && pendingCount == 0) {
            TextView empty = new TextView(this);
            empty.setText("No nodes registered yet.\n\nWhen a slave first enables vault mode, it will appear here as a pending request.");
            empty.setTextColor(0xFF666666);
            empty.setTextSize(11);
            empty.setPadding(0, 24, 0, 24);
            list.addView(empty);
        }
        statusLine.setText("Approved " + approvedCount + " · Pending " + pendingCount);
    }

    private View buildApprovedRow(String nodeId, String nodeType, String nodePubkey) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(8, 6, 8, 6);

        TextView title = new TextView(this);
        title.setText(nodeId + "  ·  " + nodeType);
        title.setTextColor(0xFFe0e0e0);
        title.setTextSize(13);
        row.addView(title);

        TextView fp = new TextView(this);
        fp.setText("slot " + slotIdHint(nodePubkey));
        fp.setTextColor(0xFF555555);
        fp.setTextSize(10);
        row.addView(fp);

        return row;
    }

    private View buildPendingRow(final String nodeId, final String nodeType,
                                 final String nodePubkey, long requestedAt,
                                 final Runnable onChanged) {
        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.VERTICAL);
        row.setPadding(8, 8, 8, 8);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(0xFF1a1808);
        bg.setCornerRadius(6f);
        row.setBackground(bg);
        LinearLayout.LayoutParams rowParams = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        rowParams.setMargins(0, 0, 0, 8);
        row.setLayoutParams(rowParams);

        TextView title = new TextView(this);
        title.setText(nodeId + "  ·  " + nodeType);
        title.setTextColor(0xFFccaa44);
        title.setTextSize(13);
        row.addView(title);

        TextView fp = new TextView(this);
        fp.setText("slot " + slotIdHint(nodePubkey)
            + (requestedAt > 0 ? "  ·  " + relativeAge(requestedAt) : ""));
        fp.setTextColor(0xFF888888);
        fp.setTextSize(10);
        row.addView(fp);

        LinearLayout buttons = new LinearLayout(this);
        buttons.setOrientation(LinearLayout.HORIZONTAL);
        LinearLayout.LayoutParams bp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        bp.setMargins(0, 8, 0, 0);
        buttons.setLayoutParams(bp);

        Button approveBtn = new Button(this);
        approveBtn.setText("Approve");
        approveBtn.setTextSize(11);
        approveBtn.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF0a2a0a));
        approveBtn.setTextColor(0xFF88cc66);
        LinearLayout.LayoutParams p1 = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        p1.setMargins(0, 0, 4, 0);
        approveBtn.setLayoutParams(p1);
        approveBtn.setOnClickListener(v -> {
            approveBtn.setEnabled(false);
            v.setAlpha(0.5f);
            executor.execute(() -> {
                boolean ok = postSignedVaultPayload("register-node", nodeId, nodeType, nodePubkey, "");
                handler.post(() -> {
                    setStatus(ok ? ("Approved " + nodeId) : ("Approve failed: " + nodeId));
                    if (ok && onChanged != null) onChanged.run();
                    else { approveBtn.setEnabled(true); v.setAlpha(1f); }
                });
            });
        });
        buttons.addView(approveBtn);

        Button denyBtn = new Button(this);
        denyBtn.setText("Deny");
        denyBtn.setTextSize(11);
        denyBtn.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF2a0a0a));
        denyBtn.setTextColor(0xFFcc6666);
        LinearLayout.LayoutParams p2 = new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1f);
        p2.setMargins(4, 0, 0, 0);
        denyBtn.setLayoutParams(p2);
        denyBtn.setOnClickListener(v -> {
            denyBtn.setEnabled(false);
            v.setAlpha(0.5f);
            executor.execute(() -> {
                boolean ok = postSignedVaultPayload("reject-node-request", nodeId, nodeType, nodePubkey, "denied by lion");
                handler.post(() -> {
                    setStatus(ok ? ("Denied " + nodeId) : ("Deny failed: " + nodeId));
                    if (ok && onChanged != null) onChanged.run();
                    else { denyBtn.setEnabled(true); v.setAlpha(1f); }
                });
            });
        });
        buttons.addView(denyBtn);

        row.addView(buttons);
        return row;
    }

    /** Builds a Lion-signed vault payload (register-node OR reject-node-request) and POSTs it.
     *  Returns true on HTTP 200, false otherwise. */
    private boolean postSignedVaultPayload(String action, String nodeId, String nodeType,
                                           String nodePubkey, String reason) {
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return false;
        try {
            java.util.TreeMap<String, Object> payload = new java.util.TreeMap<>();
            payload.put("node_id", nodeId);
            payload.put("node_type", nodeType);
            payload.put("node_pubkey", nodePubkey);
            if ("reject-node-request".equals(action)) {
                payload.put("reason", reason == null ? "" : reason);
            }
            String signature = VaultCrypto.signBlob(payload, lionPriv);
            payload.put("signature", signature);
            String body = new String(VaultCrypto.canonicalJson(payload));
            String resp = meshPost(meshUrl + "/vault/" + meshId + "/" + action, body);
            return resp != null && resp.contains("\"ok\"");
        } catch (Exception e) {
            android.util.Log.w("vault", action + " failed: " + e.getMessage());
            return false;
        }
    }

    /** First 12 hex chars of sha256(pubkey) — same scheme as VaultCrypto.slotIdForPubkey. */
    private String slotIdHint(String nodePubkey) {
        if (nodePubkey == null || nodePubkey.isEmpty()) return "?";
        try {
            String stripped = nodePubkey
                .replace("-----BEGIN PUBLIC KEY-----", "")
                .replace("-----END PUBLIC KEY-----", "")
                .replaceAll("[\\s|]+", "");
            byte[] der = android.util.Base64.decode(stripped, android.util.Base64.DEFAULT);
            java.security.MessageDigest md = java.security.MessageDigest.getInstance("SHA-256");
            byte[] digest = md.digest(der);
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 6; i++) sb.append(String.format("%02x", digest[i]));
            return sb.toString();
        } catch (Exception e) { return "?"; }
    }

    private String relativeAge(long unixSec) {
        long delta = (System.currentTimeMillis() / 1000) - unixSec;
        if (delta < 60) return delta + "s ago";
        if (delta < 3600) return (delta / 60) + "m ago";
        if (delta < 86400) return (delta / 3600) + "h ago";
        return (delta / 86400) + "d ago";
    }

    private String meshPost(String fullUrl, String body) {
        return meshPost(fullUrl, body, null);
    }

    /**
     * Audit C1: build X-FL-Ts / X-FL-Nonce / X-FL-Sig headers for a direct-mode
     * POST to the Collar's local HTTP server. Returns null if lion_privkey is
     * missing (unpaired). The slave's SigVerifier reproduces the same canonical
     * payload byte-for-byte.
     */
    private java.util.Map<String, String> buildDirectSigHeaders(String path, String body) {
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return null;
        long ts = System.currentTimeMillis();
        String nonce = VaultCrypto.randomNonce();
        String payload = VaultCrypto.canonicalizeDirectPost(path, body, ts, nonce);
        String sig;
        try { sig = VaultCrypto.signString(payload, lionPriv); }
        catch (Exception e) { return null; }
        java.util.Map<String, String> h = new java.util.HashMap<>();
        h.put("X-FL-Ts", Long.toString(ts));
        h.put("X-FL-Nonce", nonce);
        h.put("X-FL-Sig", sig);
        return h;
    }

    private String meshPost(String fullUrl, String body, java.util.Map<String, String> extraHeaders) {
        try {
            URL url = new URL(fullUrl);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            if (!authToken.isEmpty()) {
                conn.setRequestProperty("Authorization", "Bearer " + authToken);
            }
            if (extraHeaders != null) {
                for (java.util.Map.Entry<String, String> e : extraHeaders.entrySet()) {
                    conn.setRequestProperty(e.getKey(), e.getValue());
                }
            }
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.getBytes());
            // Read body from input stream on 2xx, error stream on 4xx/5xx so
            // callers like apiVault can parse current_version out of a 409
            // conflict response. Returning null on non-200 (the previous
            // behaviour) silently dropped the conflict body and the retry
            // loop never saw the version it needed to advance to.
            int code = conn.getResponseCode();
            java.io.InputStream is = (code >= 200 && code < 300)
                ? conn.getInputStream()
                : conn.getErrorStream();
            String respBody = "";
            if (is != null) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(is));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();
                respBody = sb.toString();
            }
            conn.disconnect();
            return respBody;
        } catch (Exception e) { return null; }
    }

    // ── Mesh API ──

    private String meshPin() {
        String p = prefs.getString("pin", "");
        if (p.isEmpty()) try { p = android.provider.Settings.Global.getString(getContentResolver(), "focus_lock_pin"); } catch (Exception e) {}
        return (p == null || "null".equals(p)) ? "" : p;
    }

    private String meshOrder(String action, String paramsJson) {
        return api("/api/" + action, paramsJson);
    }

    private String meshGet(String path) {
        // Direct (serverless) mode: hit the bunny's Collar status endpoint directly.
        // pairMode / bunnyDirectUrl are instance vars loaded from the active
        // bunny slot (see loadActiveBunny).
        if ("direct".equals(pairMode) && !bunnyDirectUrl.isEmpty()
            && (path.equals("/mesh/status") || path.startsWith("/mesh/status?"))) {
            // Hit the Collar's /mesh/status directly for the locked/escapes/paywall fields
            return directGet(bunnyDirectUrl + "/mesh/status");
        }
        if (meshUrl.isEmpty()) return null;
        try {
            String fullUrl;
            if (!meshId.isEmpty() && !authToken.isEmpty()
                && (path.equals("/mesh/status") || path.startsWith("/mesh/status?"))) {
                fullUrl = meshUrl + "/api/mesh/" + meshId + "/status";
            } else {
                fullUrl = meshUrl + path;
            }
            URL url = new URL(fullUrl);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("GET");
            if (!authToken.isEmpty()) {
                conn.setRequestProperty("Authorization", "Bearer " + authToken);
            }
            // Same fix as meshPost: prefer getInputStream on 2xx, fall back to
            // getErrorStream so callers see the actual server response body
            // on non-200 instead of an opaque null.
            int code = conn.getResponseCode();
            java.io.InputStream is = (code >= 200 && code < 300)
                ? conn.getInputStream()
                : conn.getErrorStream();
            String respBody = "";
            if (is != null) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(is));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();
                respBody = sb.toString();
            }
            conn.disconnect();
            return respBody;
        } catch (Exception e) { return null; }
    }

    /**
     * Phase D: returns the JSON string the body-check / inbox / device-list
     * consumers should parse. In vault mode (server-side) the LocalSnapshot
     * is the source of truth; in legacy and direct modes we fall back to a
     * fresh /mesh/status fetch.
     *
     * Returns null if no source has produced a status yet (e.g. first vault
     * tick hasn't completed). Callers must null-check, same as meshGet().
     */
    private String currentStatusJson() {
        if (vaultMode && !"direct".equals(pairMode)) {
            return localSnapshot.currentRuntimeJson;
        }
        return meshGet("/mesh/status");
    }

    /** Plain GET helper for serverless direct mode (no auth header). */
    private String directGet(String fullUrl) {
        try {
            URL url = new URL(fullUrl);
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(5000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("GET");
            int code = conn.getResponseCode();
            java.io.InputStream is = (code >= 200 && code < 300)
                ? conn.getInputStream()
                : conn.getErrorStream();
            String respBody = "";
            if (is != null) {
                BufferedReader reader = new BufferedReader(new InputStreamReader(is));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();
                respBody = sb.toString();
            }
            conn.disconnect();
            return respBody;
        } catch (Exception e) { return null; }
    }

    // PIN auth removed — RSA signatures only
    private String selectedMode() { int pos = modeSpinner.getSelectedItemPosition(); return pos >= 0 ? MODE_KEYS[pos] : "basic"; }

    // ── Actions ──

    private String buildLockJson(String msg, long mins) {
        StringBuilder j = new StringBuilder("{\"mode\":\"");
        j.append(selectedMode()).append("\"");
        if (msg != null && !msg.isEmpty()) j.append(",\"message\":\"").append(esc(msg)).append("\"");
        if (mins > 0) j.append(",\"timer\":\"").append(mins).append("\"");
        j.append(",\"vibrate\":").append(toggleVibrate.isChecked());
        j.append(",\"penalty\":").append(togglePenalty.isChecked());
        j.append(",\"shame\":").append(toggleShame.isChecked());
        j.append(",\"dim\":").append(toggleDim.isChecked());
        j.append(",\"mute\":").append(toggleMute.isChecked());
        String pw = paywallInput.getText().toString();
        if (!pw.isEmpty()) j.append(",\"paywall\":\"").append(pw).append("\"");
        String comp = complimentInput.getText().toString();
        if (!comp.isEmpty()) j.append(",\"compliment\":\"").append(esc(comp)).append("\"");
        j.append("}");
        return j.toString();
    }

    private void doLock() {
        String msg = messageInput.getText().toString();
        String timer = timerInput.getText().toString();
        long mins = 0; try { mins = Long.parseLong(timer); } catch (Exception e) {}
        final long fm = mins;
        setStatus("Locking...");
        executor.execute(() -> {
            String r = api("/api/lock", buildLockJson(msg, fm));
            setStatus(r.contains("ok") ? "LOCKED" + (fm > 0 ? " " + fm + "m" : "") : "Failed");
        });
    }

    private void doQuickLock(int minutes) {
        setStatus("Locking " + minutes + "m...");
        executor.execute(() -> {
            String r = api("/api/lock", buildLockJson("", minutes));
            setStatus(r.contains("ok") ? "LOCKED " + minutes + "m" : "Failed");
        });
    }

    private void doSetCountdown() {
        // Schedule a future lock with warning notifications.
        // Bunny gets heads-up notifications at 1h/30m/10m/5m/1m/30s before lock kicks in.
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 24, 48, 0);

        EditText minsInput = new EditText(this);
        minsInput.setHint("Minutes until lock (e.g. 60)");
        minsInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        minsInput.setTextColor(0xFFe0e0e0);
        minsInput.setHintTextColor(0xFF555555);
        minsInput.setBackgroundColor(0xFF111118);
        minsInput.setPadding(24, 20, 24, 20);
        layout.addView(minsInput);

        EditText msgInput = new EditText(this);
        msgInput.setHint("Warning message (optional)");
        msgInput.setTextColor(0xFFe0e0e0);
        msgInput.setHintTextColor(0xFF555555);
        msgInput.setBackgroundColor(0xFF111118);
        msgInput.setPadding(24, 20, 24, 20);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = 16;
        msgInput.setLayoutParams(lp);
        layout.addView(msgInput);

        new AlertDialog.Builder(this)
            .setTitle("Schedule Lock")
            .setMessage("Bunny will get warning notifications at 1h/30m/10m/5m/1m/30s before lock.")
            .setView(layout)
            .setPositiveButton("Schedule", (d, w) -> {
                String minsStr = minsInput.getText().toString().trim();
                long mins;
                try { mins = Long.parseLong(minsStr); } catch (Exception e) {
                    setStatus("Invalid minutes");
                    return;
                }
                if (mins <= 0) { setStatus("Must be > 0"); return; }
                long lockAt = System.currentTimeMillis() + mins * 60_000L;
                String msg = msgInput.getText().toString().trim();
                String params = "{\"lock_at\":" + lockAt
                    + ",\"message\":\"" + msg.replace("\"", "\\\"") + "\"}";
                setStatus("Scheduling...");
                executor.execute(() -> {
                    String r = meshOrder("set-countdown", params);
                    setStatus(r.contains("ok") ? "LOCK in " + mins + "m" : "Failed");
                });
            })
            .setNeutralButton("Cancel Countdown", (d, w) -> {
                setStatus("Cancelling countdown...");
                executor.execute(() -> {
                    String r = meshOrder("cancel-countdown", "{}");
                    setStatus(r.contains("ok") ? "Countdown cancelled" : "Failed");
                });
            })
            .setNegativeButton("Close", null)
            .show();
    }

    private void doUnlock() {
        setStatus("Unlocking all...");
        executor.execute(() -> {
            String r = api("/api/unlock", "{}");
            // Also unlock desktops via mesh
            meshOrder("unlock-device", "{\"target\":\"all\"}");
            setStatus(r.contains("ok") ? "UNLOCKED" : "Failed");
        });
    }

    private void doUnlockDevice() {
        setStatus("Loading devices...");
        executor.execute(() -> {
            ArrayList<String> devices = new ArrayList<>();
            String meshResp = currentStatusJson();
            if (meshResp != null) {
                // Simple parsing: find all node_id values in "nodes" object
                int nodesIdx = meshResp.indexOf("\"nodes\":");
                if (nodesIdx >= 0) {
                    String nodesPart = meshResp.substring(nodesIdx);
                    int searchFrom = 0;
                    while (true) {
                        int qi = nodesPart.indexOf("\"node_id\":\"", searchFrom);
                        if (qi < 0) {
                            // Try key-based parsing (nodes is an object with keys as node IDs)
                            break;
                        }
                        qi += 11;
                        int qe = nodesPart.indexOf("\"", qi);
                        if (qe > qi) devices.add(nodesPart.substring(qi, qe));
                        searchFrom = qe + 1;
                    }
                    // Fallback: parse object keys under "nodes"
                    if (devices.isEmpty()) {
                        int braceStart = nodesPart.indexOf("{", 7);
                        if (braceStart >= 0) {
                            String inner = nodesPart.substring(braceStart + 1);
                            int pos = 0;
                            while (pos < inner.length()) {
                                int qs = inner.indexOf("\"", pos);
                                if (qs < 0) break;
                                int qe2 = inner.indexOf("\"", qs + 1);
                                if (qe2 < 0) break;
                                String key = inner.substring(qs + 1, qe2);
                                if (!key.isEmpty() && !key.equals("type") && !key.equals("online")
                                    && !key.equals("last_seen") && !key.equals("orders_version")
                                    && !key.equals("status") && !key.equals("addresses") && !key.equals("port")) {
                                    devices.add(key);
                                }
                                // Skip to next top-level key (after the value object)
                                int nextBrace = inner.indexOf("}", qe2);
                                if (nextBrace < 0) break;
                                pos = nextBrace + 1;
                            }
                        }
                    }
                }
            }
            // Always add phone as option
            if (!devices.contains("phone")) devices.add(0, "phone");
            final String[] devArr = devices.toArray(new String[0]);

            handler.post(() -> {
                new AlertDialog.Builder(this)
                    .setTitle("Release Device")
                    .setItems(devArr, (d, which) -> {
                        String target = devArr[which];
                        setStatus("Releasing " + target + "...");
                        executor.execute(() -> {
                            if (target.equals("phone")) {
                                String r = api("/api/unlock", "{}");
                                setStatus(r.contains("ok") ? "Phone unlocked" : "Failed");
                            } else {
                                String r = meshOrder("unlock-device", "{\"target\":\"" + target + "\"}");
                                setStatus(r != null && r.contains("ok") ? target + " released" : "Failed");
                            }
                        });
                    })
                    .setNegativeButton("Cancel", null)
                    .show();
            });
        });
    }

    private void doTask() {
        String task = taskInput.getText().toString();
        if (task.isEmpty()) { statusView.setText("Enter task text"); return; }
        String msg = messageInput.getText().toString();
        int reps = 1; try { reps = Integer.parseInt(taskRepsInput.getText().toString()); } catch (Exception e) {}
        if (reps < 1) reps = 1;
        if (taskRandomize.isChecked()) task = randomizeCaps(task);
        final String ft = task; final int fr = reps;
        setStatus("Task...");
        executor.execute(() -> {
            String json = "{\"text\":\"" + esc(ft) + "\",\"reps\":" + fr;
            if (!msg.isEmpty()) json += ",\"message\":\"" + esc(msg) + "\"";
            json += ",\"vibrate\":" + toggleVibrate.isChecked();
            json += ",\"penalty\":" + togglePenalty.isChecked();
            json += ",\"shame\":" + toggleShame.isChecked();
            json += ",\"dim\":" + toggleDim.isChecked();
            json += ",\"mute\":" + toggleMute.isChecked();
            json += "}";
            String r = api("/api/task", json);
            setStatus(r.contains("ok") ? "TASK x" + fr : "Failed");
        });
    }

    private void doOfferRespond(String action) {
        String counter = offerCounterInput.getText().toString();
        setStatus(action.equals("accept") ? "Accepting..." : "Declining...");
        executor.execute(() -> {
            String json = "{\"action\":\"" + action + "\"";
            if (!counter.isEmpty()) json += ",\"response\":\"" + esc(counter) + "\"";
            json += "}";
            String r = api("/api/offer-respond", json);
            setStatus(r.contains("ok") ? (action.equals("accept") ? "ACCEPTED + UNLOCKED" : "DECLINED") : "Failed");
            handler.post(() -> {
                offerSection.setVisibility(View.GONE);
                offerCounterInput.setText("");
            });
        });
    }

    private void doEntrap() {
        new AlertDialog.Builder(this)
            .setTitle("Entrap")
            .setMessage("This will flag the phone as entrapped \u2014 next time you lock it, there's no way out without you.\n\nProceed?")
            .setPositiveButton("ENTRAP", (d, w) -> {
                String msg = messageInput.getText().toString();
                setStatus("Entrapping...");
                executor.execute(() -> {
                    String json = "{\"message\":\"" + esc(msg.isEmpty() ? "Entrapped." : msg) + "\"}";
                    String r = api("/api/entrap", json);
                    setStatus(r != null && r.contains("ok") ? "Entrapped." : "Failed: " + r);
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doSetup() {
        View v = getLayoutInflater().inflate(getResources().getIdentifier("dialog_setup", "layout", getPackageName()), null);
        // Repurpose existing layout fields for the new mesh setup
        EditText serverInput = (EditText) v.findViewById(getId("setup_mesh_url"));
        TextView resultView = (TextView) v.findViewById(getId("setup_result"));

        // Hide fields not needed for account-based mesh
        View ipField = v.findViewById(getId("setup_tailscale_ip"));
        View lanField = v.findViewById(getId("setup_lan_ip"));
        View pinField = v.findViewById(getId("setup_pair_code"));
        View httpsField = v.findViewById(getId("setup_https_url"));
        if (ipField != null) ipField.setVisibility(View.GONE);
        if (lanField != null) lanField.setVisibility(View.GONE);
        if (pinField != null) pinField.setVisibility(View.GONE);
        if (httpsField != null) httpsField.setVisibility(View.GONE);

        if (serverInput != null) {
            serverInput.setHint("Server URL (e.g. https://your-mesh.example.com)");
            // Multi-bunny: show the ACTIVE bunny's mesh_url, not the legacy
            // top-level key. meshUrl instance var was loaded from the active
            // slot in loadActiveBunny().
            serverInput.setText(meshUrl);
        }

        // Vault mode toggle (Phase B/C)
        android.widget.CheckBox vaultCheck = (android.widget.CheckBox) v.findViewById(getId("setup_vault_mode"));
        if (vaultCheck != null) {
            vaultCheck.setChecked(vaultMode);
        }

        // Show current state — pull the invite from the active slot, not the
        // legacy top-level key (which is only correct for bunny b1).
        if (resultView != null) {
            if (!meshId.isEmpty()) {
                String invite = activeBunnyId.isEmpty()
                    ? prefs.getString("invite_code", "")
                    : prefs.getString(bunnyKey(activeBunnyId, "invite_code"), "");
                StringBuilder st = new StringBuilder();
                if (!activeBunnyLabel.isEmpty()) {
                    st.append(activeBunnyLabel).append("\n");
                }
                st.append("Mesh: ").append(meshId);
                if (!invite.isEmpty()) st.append("\nInvite: ").append(invite);
                resultView.setText(st.toString());
            } else {
                resultView.setText("No mesh configured. Tap Create Mesh.");
            }
        }

        // Capture for use inside lambdas
        final android.widget.CheckBox vaultCheckFinal = vaultCheck;

        new AlertDialog.Builder(this).setView(v)
            .setPositiveButton("Create Mesh", (d, w) -> {
                String sUrl = serverInput != null ? serverInput.getText().toString().trim() : "";
                if (sUrl.isEmpty()) {
                    setStatus("Enter a server URL");
                    return;
                }
                meshUrl = sUrl;
                boolean newVault = vaultCheckFinal != null && vaultCheckFinal.isChecked();
                vaultMode = newVault;
                prefs.edit()
                    .putString("mesh_url", sUrl)
                    .putBoolean("vault_mode", newVault)
                    .apply();
                setStatus("Creating mesh...");
                final String fUrl = sUrl;
                executor.execute(() -> createMesh(fUrl));
            })
            .setNeutralButton("Pair Direct (LAN)", (d, w) -> {
                // Persist vault toggle even when not creating a fresh mesh
                if (vaultCheckFinal != null) {
                    boolean newVault = vaultCheckFinal.isChecked();
                    vaultMode = newVault;
                    prefs.edit().putBoolean("vault_mode", newVault).apply();
                }
                doPairDirect();
            })
            .setNegativeButton("Save", (d, w) -> {
                // Save the vault toggle (and any URL change) for the active
                // bunny slot, without creating a new mesh. vault_mode and
                // mesh_url are both per-bunny, so they must be written under
                // the active slot's key prefix.
                SharedPreferences.Editor ed = prefs.edit();
                if (vaultCheckFinal != null) {
                    boolean newVault = vaultCheckFinal.isChecked();
                    vaultMode = newVault;
                    ed.putBoolean("vault_mode", newVault);  // legacy compat
                    if (!activeBunnyId.isEmpty()) {
                        ed.putBoolean(bunnyKey(activeBunnyId, "vault_mode"), newVault);
                    }
                }
                String sUrl = serverInput != null ? serverInput.getText().toString().trim() : "";
                if (!sUrl.isEmpty() && !sUrl.equals(meshUrl)) {
                    meshUrl = sUrl;
                    ed.putString("mesh_url", sUrl);  // legacy compat
                    if (!activeBunnyId.isEmpty()) {
                        ed.putString(bunnyKey(activeBunnyId, "mesh_url"), sUrl);
                    }
                }
                ed.apply();
                setStatus("Saved" + (vaultMode ? " (vault mode on)" : ""));
            }).show();
    }

    /**
     * Serverless pairing: Lion connects directly to Bunny's Collar at <ip>:8432.
     * Bunny shows their IP in Bunny Tasker (the QR or the displayed pairing code).
     * No mesh server needed — works on LAN, Tailscale, or any direct-routable network.
     */
    private void doPairDirect() {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(48, 24, 48, 0);

        EditText ipInput = new EditText(this);
        ipInput.setHint("Bunny's IP or hostname (e.g. 192.168.1.50)");
        ipInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_URI);
        ipInput.setTextColor(0xFFe0e0e0);
        ipInput.setHintTextColor(0xFF555555);
        ipInput.setBackgroundColor(0xFF111118);
        ipInput.setPadding(24, 20, 24, 20);
        layout.addView(ipInput);

        EditText portInput = new EditText(this);
        portInput.setHint("Port (default: 8432)");
        portInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        portInput.setText("8432");
        portInput.setTextColor(0xFFe0e0e0);
        portInput.setHintTextColor(0xFF555555);
        portInput.setBackgroundColor(0xFF111118);
        portInput.setPadding(24, 20, 24, 20);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = 16;
        portInput.setLayoutParams(lp);
        layout.addView(portInput);

        // Audit C5: expected fingerprint pin. The bunny displays a
        // 16-hex-char fingerprint on Bunny Tasker's pairing screen;
        // typing it here lets us detect MITM key swap at pair time.
        EditText fpInput = new EditText(this);
        fpInput.setHint("Fingerprint (16 hex chars from Bunny Tasker)");
        fpInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT);
        fpInput.setTextColor(0xFFe0e0e0);
        fpInput.setHintTextColor(0xFF555555);
        fpInput.setBackgroundColor(0xFF111118);
        fpInput.setPadding(24, 20, 24, 20);
        LinearLayout.LayoutParams fpLp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT);
        fpLp.topMargin = 16;
        fpInput.setLayoutParams(fpLp);
        layout.addView(fpInput);

        // Consume any pending scan from a just-returned PAIR_QR_SCAN_REQUEST.
        // Cleared here so re-opening the dialog without a fresh scan shows
        // empty fields.
        if (!pendingPairIp.isEmpty()) {
            ipInput.setText(pendingPairIp);
            pendingPairIp = "";
        }
        if (!pendingPairPort.isEmpty()) {
            portInput.setText(pendingPairPort);
            pendingPairPort = "";
        }
        if (!pendingPairFp.isEmpty()) {
            fpInput.setText(pendingPairFp);
            pendingPairFp = "";
        }

        new AlertDialog.Builder(this)
            .setTitle("Direct Pair")
            .setMessage("Enter the Bunny's IP/hostname AND the 16-char fingerprint shown in their Bunny Tasker pairing screen. "
                + "The fingerprint detects a MITM key swap — if you skip it, anyone on the network can impersonate the bunny.")
            .setView(layout)
            .setNeutralButton("Scan QR", (d, w) -> {
                // Launch any ZXing-compatible scanner; on result, the
                // PAIR_QR_SCAN_REQUEST branch of onActivityResult parses
                // the Bunny's pair payload and re-opens this dialog with
                // the fields pre-filled.
                try {
                    android.content.Intent scanIntent = new android.content.Intent(
                        "com.google.zxing.client.android.SCAN");
                    scanIntent.putExtra("SCAN_MODE", "QR_CODE_MODE");
                    startActivityForResult(scanIntent, PAIR_QR_SCAN_REQUEST);
                } catch (Exception e) {
                    setStatus("No QR scanner installed — enter IP + fingerprint manually");
                }
            })
            .setPositiveButton("Pair", (d, w) -> {
                String ip = ipInput.getText().toString().trim();
                String port = portInput.getText().toString().trim();
                String fp = fpInput.getText().toString().trim().toLowerCase().replaceAll("\\s+", "");
                if (ip.isEmpty()) { setStatus("Enter an IP"); return; }
                if (port.isEmpty()) port = "8432";
                final String bunnyUrl = "http://" + ip + ":" + port;
                final String expectedFp = fp;
                setStatus("Pairing direct...");
                executor.execute(() -> pairDirect(bunnyUrl, expectedFp));
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Audit C5: SHA-256(b64decode(pubkey)) first 8 bytes as 16 hex chars.
     *  Must match PairingManager.getFingerprint on the bunny side. */
    private static String computeBunnyFingerprint(String pubKeyB64) {
        try {
            byte[] hash = java.security.MessageDigest.getInstance("SHA-256")
                .digest(android.util.Base64.decode(pubKeyB64, android.util.Base64.NO_WRAP));
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 8; i++) sb.append(String.format("%02x", hash[i] & 0xFF));
            return sb.toString();
        } catch (Exception e) { return ""; }
    }

    private void pairDirect(String bunnyUrl, String expectedFingerprint) {
        try {
            // Generate Lion's keypair if missing
            String lionPubB64 = prefs.getString("lion_pubkey_b64", "");
            if (lionPubB64.isEmpty()) {
                KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
                kpg.initialize(2048);
                java.security.KeyPair kp = kpg.generateKeyPair();
                lionPubB64 = android.util.Base64.encodeToString(
                    kp.getPublic().getEncoded(), android.util.Base64.NO_WRAP);
                String lionPrivB64 = android.util.Base64.encodeToString(
                    kp.getPrivate().getEncoded(), android.util.Base64.NO_WRAP);
                prefs.edit()
                    .putString("lion_pubkey_b64", lionPubB64)
                    .putString("lion_privkey_b64", lionPrivB64)
                    .apply();
            }

            // POST {lion_pubkey} to bunny's /api/pair
            String body = "{\"lion_pubkey\":\"" + lionPubB64 + "\"}";
            String resp = meshPost(bunnyUrl + "/api/pair", body);

            if (resp == null) {
                setStatus("Pair failed: connection error");
                return;
            }
            if (resp.contains("\"error\"")) {
                // The Collar's doPair returns clearable:true when it's already
                // paired with a different lion_pubkey — recoverable via Bunny
                // Tasker's Reset button. Show a dialog with the server's own
                // hint text rather than dumping raw JSON at the user.
                if (resp.contains("\"clearable\":true")) {
                    String hint = parseJsonStr(resp, "hint");
                    showPairConflictDialog(bunnyUrl, hint);
                    return;
                }
                setStatus("Pair failed: " + resp);
                return;
            }

            // Idempotent re-pair: same lion key POSTed twice returns
            // action:already-paired instead of a fresh pair. Surface the
            // distinction so the user knows their retry was absorbed,
            // rather than thinking they started a new pairing.
            boolean alreadyPaired = resp.contains("\"action\":\"already-paired\"");

            // Extract bunny_pubkey from response
            String bunnyPubB64 = parseJsonStr(resp, "bunny_pubkey");
            if (bunnyPubB64.isEmpty()) {
                setStatus("Pair failed: no bunny pubkey");
                return;
            }

            // Audit C5: verify the returned bunny_pubkey against the
            // fingerprint the user read off the bunny's own screen. If
            // they don't match we're MITM'd and must abort — continuing
            // would trust the attacker's key for the life of the pairing.
            String receivedFp = computeBunnyFingerprint(bunnyPubB64);
            if (!expectedFingerprint.isEmpty()) {
                if (!expectedFingerprint.equalsIgnoreCase(receivedFp)) {
                    setStatus("Pair ABORTED: fingerprint mismatch. expected="
                        + expectedFingerprint + " got=" + receivedFp
                        + ". Possible MITM — check the bunny's screen.");
                    return;
                }
            } else {
                // No expected fingerprint provided — log the received one
                // prominently so the user can verify it out-of-band. Does
                // NOT abort (backwards compat) but the status message
                // screams at the operator to double-check.
                android.util.Log.w("focusctl",
                    "pairDirect: no expected fingerprint given; received " + receivedFp);
                setStatus("PAIRED without fingerprint check — VERIFY " + receivedFp
                    + " matches Bunny Tasker NOW");
            }

            // Store pairing — direct mode uses bunnyDirectUrl instead of mesh.
            // Multi-bunny: create a new slot for this direct pairing and set it
            // active. The slot gets a default label "bunny" which the user can
            // rename from Advanced → Bunnies.
            final String newId = addBunnySlot("bunny");
            final String fBunnyUrl = bunnyUrl;
            final String fBunnyPubB64 = bunnyPubB64;
            prefs.edit()
                .putString(bunnyKey(newId, "bunny_direct_url"), fBunnyUrl)
                .putString(bunnyKey(newId, "bunny_pubkey_b64"), fBunnyPubB64)
                .putString(bunnyKey(newId, "pair_mode"), "direct")
                // Legacy keys also updated so rollback to v58 still works:
                .putString("bunny_direct_url", fBunnyUrl)
                .putString("bunny_pubkey_b64", fBunnyPubB64)
                .putString("pair_mode", "direct")
                .apply();
            handler.post(() -> setActiveBunny(newId));
            String prefix = alreadyPaired ? "RE-CONFIRMED direct" : "PAIRED direct";
            if (!expectedFingerprint.isEmpty()) {
                setStatus(prefix + " (fingerprint verified): " + fBunnyUrl);
            } else {
                setStatus(prefix + " (UNVERIFIED fp=" + receivedFp + "): " + fBunnyUrl);
            }
        } catch (Exception e) {
            setStatus("Pair error: " + e.getMessage());
        }
    }

    /** Surface the Collar's "already paired with a different lion key"
     *  response in plain English. Post-2026-04-24 there is NO in-app
     *  recovery: only the *current* Lion (via Release Forever) or a
     *  factory reset can unpair the Collar. The hint text comes from
     *  the Collar; we just frame it. */
    private void showPairConflictDialog(String bunnyUrl, String hint) {
        final String msg = hint != null && !hint.isEmpty()
            ? hint
            : "The Collar is already paired with a different Lion. Only the "
                + "current Lion (via Release Forever) or a factory reset can "
                + "unpair this Collar.";
        handler.post(() -> {
            new android.app.AlertDialog.Builder(MainActivity.this)
                .setTitle("Already paired — different Lion")
                .setMessage(msg)
                .setPositiveButton("OK", null)
                .show();
            setStatus("Pair blocked: Collar paired with another Lion");
        });
    }

    private void createMesh(String serverUrl) {
        try {
            // Generate RSA 2048 keypair
            KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
            kpg.initialize(2048);
            KeyPair kp = kpg.generateKeyPair();
            String pubKey = android.util.Base64.encodeToString(kp.getPublic().getEncoded(), android.util.Base64.NO_WRAP);
            String privKey = android.util.Base64.encodeToString(kp.getPrivate().getEncoded(), android.util.Base64.NO_WRAP);

            // POST /api/mesh/create
            String body = "{\"lion_pubkey\":\"" + pubKey + "\"}";
            URL url = new URL(serverUrl + "/api/mesh/create");
            HttpURLConnection conn = (HttpURLConnection) url.openConnection();
            conn.setConnectTimeout(10000);
            conn.setReadTimeout(10000);
            conn.setRequestMethod("POST");
            conn.setRequestProperty("Content-Type", "application/json");
            conn.setDoOutput(true);
            conn.getOutputStream().write(body.getBytes());
            BufferedReader reader = new BufferedReader(new InputStreamReader(conn.getInputStream()));
            StringBuilder sb = new StringBuilder();
            String line;
            while ((line = reader.readLine()) != null) sb.append(line);
            reader.close();
            conn.disconnect();
            String resp = sb.toString();

            String newMeshId = parseJsonStr(resp, "mesh_id");
            String newAuthToken = parseJsonStr(resp, "auth_token");
            String inviteCode = parseJsonStr(resp, "invite_code");
            String pin = parseJsonStr(resp, "pin");

            if (newMeshId.isEmpty() || newAuthToken.isEmpty()) {
                setStatus("Mesh creation failed: " + resp);
                return;
            }

            // Multi-bunny: create a new slot for this mesh and set it active.
            // Lion's RSA keypair (lion_privkey/lion_pubkey) is GLOBAL — one Lion
            // identity shared across bunnies — so those live in the top-level
            // prefs, not under the slot. The vault_mode toggle IS per-bunny
            // (each bunny can independently run vault or legacy).
            final String newId = addBunnySlot("bunny");
            final String fMeshId = newMeshId;
            final String fAuthToken = newAuthToken;
            final String fInvite = inviteCode;
            final String fPin = pin;
            final String fServerUrl = serverUrl;
            final boolean fVaultMode = vaultMode;
            prefs.edit()
                .putString(bunnyKey(newId, "mesh_url"),    fServerUrl)
                .putString(bunnyKey(newId, "mesh_id"),     fMeshId)
                .putString(bunnyKey(newId, "auth_token"),  fAuthToken)
                .putString(bunnyKey(newId, "invite_code"), fInvite)
                .putString(bunnyKey(newId, "pin"),         fPin)
                .putBoolean(bunnyKey(newId, "vault_mode"), fVaultMode)
                .putString(bunnyKey(newId, "pair_mode"),   "")
                // Lion identity — global:
                .putString("lion_pubkey", pubKey)
                .putString("lion_privkey", privKey)
                // Legacy top-level keys also updated for v58 rollback safety:
                .putString("mesh_id",     fMeshId)
                .putString("auth_token",  fAuthToken)
                .putString("invite_code", fInvite)
                .putString("pin",         fPin)
                .apply();

            handler.post(() -> {
                setActiveBunny(newId);
                setStatus("Mesh created!");
                // Show invite code prominently
                new AlertDialog.Builder(this)
                    .setTitle("Mesh Created")
                    .setMessage("Tell your Bunny this invite code:\n\n"
                        + fInvite + "\n\n"
                        + "They'll enter it in Bunny Tasker to join.\n\n"
                        + "Server: " + fServerUrl + "\n"
                        + "Mesh ID: " + fMeshId)
                    .setPositiveButton("OK", null)
                    .show();
            });
        } catch (Exception e) {
            setStatus("Error: " + e.getMessage());
        }
    }

    private String randomizeCaps(String text) {
        Random rng = new Random();
        StringBuilder sb = new StringBuilder();
        for (char c : text.toCharArray()) {
            if (Character.isLetter(c) && rng.nextBoolean())
                sb.append(Character.isUpperCase(c) ? Character.toLowerCase(c) : Character.toUpperCase(c));
            else sb.append(c);
        }
        return sb.toString();
    }

    private String esc(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }

    // -- Balance --

    private void doClearBalance() {
        setStatus("Clearing balance...");
        executor.execute(() -> {
            String r = api("/api/clear-paywall", "{}");
            meshOrder("clear-paywall", "{}");
            if (balanceDisplay != null) handler.post(() -> {
                balanceDisplay.setText("$0");
                balanceDisplay.setTextColor(0xFF44aa44);
            });
            setStatus(r.contains("ok") ? "Balance cleared" : "Failed");
        });
    }

    private void doSetBalance() {
        EditText input = (EditText) findViewById(getId("balance_set_input"));
        if (input == null) return;
        String val = input.getText().toString().trim();
        if (val.isEmpty()) return;
        setStatus("Setting balance...");
        executor.execute(() -> {
            api("/api/clear-paywall", "{}");
            String r = api("/api/add-paywall", "{\"amount\":\"" + val + "\"}");
            meshOrder("add-paywall", "{\"amount\":" + val + "}");
            handler.post(() -> {
                if (balanceDisplay != null) {
                    balanceDisplay.setText("$" + val);
                    balanceDisplay.setTextColor(0xFFFFD700);
                }
                input.setText("");
            });
            setStatus(r.contains("ok") ? "Balance set to $" + val : "Failed");
        });
    }

    // -- Body Check --

    private void doBodyCheckStart() {
        EditText areaInput = new EditText(this);
        areaInput.setHint("Body area to track");
        areaInput.setTextColor(0xFFe0e0e0);
        areaInput.setHintTextColor(0xFF555555);
        areaInput.setBackgroundColor(0xFF111118);
        areaInput.setPadding(32, 24, 32, 24);
        new AlertDialog.Builder(this)
            .setTitle("Start Body Check")
            .setMessage("What area should the bunny photograph for inspection?")
            .setView(areaInput)
            .setPositiveButton("START", (d, w) -> {
                String area = areaInput.getText().toString().trim();
                if (area.isEmpty()) return;
                String areaKey = area.toLowerCase().replace(" ", "_");
                setStatus("Starting body check: " + area);
                executor.execute(() -> {
                    String r = meshOrder("start-body-check",
                        "{\"area\":\"" + esc(areaKey) + "\",\"interval_h\":12}");
                    setStatus(r != null && r.contains("ok")
                        ? "Body check active: " + area + " (every 12h)"
                        : "Failed");
                    updateBodyCheckStatus();
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doBodyCheckNow() {
        setStatus("Requesting body check photo...");
        executor.execute(() -> {
            String area = "";
            String meshResp = currentStatusJson();
            if (meshResp != null) {
                String a = parseJsonStr(meshResp, "body_check_area");
                if (!a.isEmpty()) area = a;
            }
            if (area.isEmpty()) area = "body";
            String json = "{\"hint\":\"Body inspection: photograph " + area + " clearly"
                + "\",\"webhook\":\"/webhook/body-check\""
                + ",\"area\":\"" + esc(area) + "\"}";
            String r = api("/api/photo-request", json);
            setStatus(r != null && r.contains("ok") ? "Photo requested" : "Failed");
        });
    }

    private void doBodyCheckBaseline() {
        new AlertDialog.Builder(this)
            .setTitle("Set Baseline")
            .setMessage("Request a photo to set as the baseline for future comparisons.\n\n"
                + "Use when starting tracking or when existing wounds have healed enough for a new reference point.")
            .setPositiveButton("SET BASELINE", (d, w) -> {
                setStatus("Requesting baseline photo...");
                executor.execute(() -> {
                    String area = "";
                    String meshResp = currentStatusJson();
                    if (meshResp != null) {
                        String a = parseJsonStr(meshResp, "body_check_area");
                        if (!a.isEmpty()) area = a;
                    }
                    if (area.isEmpty()) area = "body";
                    String json = "{\"hint\":\"Baseline photo: photograph " + area + " clearly"
                        + "\",\"webhook\":\"/webhook/body-check-baseline\""
                        + ",\"area\":\"" + esc(area) + "\"}";
                    String r = api("/api/photo-request", json);
                    setStatus(r != null && r.contains("ok") ? "Baseline photo requested" : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void updateBodyCheckStatus() {
        executor.execute(() -> {
            String meshResp = currentStatusJson();
            if (meshResp == null) return;
            String active = parseJsonNumStr(meshResp, "body_check_active");
            String area = parseJsonStr(meshResp, "body_check_area");
            String streak = parseJsonNumStr(meshResp, "body_check_streak");
            String lastResult = parseJsonStr(meshResp, "body_check_last_result");
            handler.post(() -> {
                TextView status = (TextView) findViewById(getId("body_check_status"));
                if (status == null) return;
                if ("1".equals(active)) {
                    String s = area + " | streak: " + streak;
                    if (!lastResult.isEmpty()) s += " | last: " + lastResult;
                    status.setText(s);
                    status.setTextColor("HEALING".equals(lastResult) ? 0xFF44aa44 :
                        "NEW_DAMAGE".equals(lastResult) ? 0xFFcc2222 : 0xFFDAA520);
                } else {
                    status.setText("Not active");
                    status.setTextColor(0xFF888888);
                }
            });
        });
    }

    // -- Recurring Fine --

    private void doStartFine() {
        // Custom amount + interval input. Was a fixed-list .setItems() dialog
        // that combined poorly with .setMessage() on some Android versions
        // and gave Lion no way to enter a non-preset amount. Now: free-form
        // $ + minutes inputs with sensible defaults plus quick-set chips.
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(48, 24, 48, 8);

        TextView lbl1 = new TextView(this);
        lbl1.setText("Amount per interval ($):");
        lbl1.setTextColor(0xFFcccccc);
        lbl1.setTextSize(13);
        root.addView(lbl1);
        EditText amountInput = new EditText(this);
        amountInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        amountInput.setText("10");
        amountInput.setTextColor(0xFFffe6a8);
        amountInput.setTextSize(18);
        amountInput.setBackgroundColor(0xFF111118);
        amountInput.setPadding(24, 18, 24, 18);
        root.addView(amountInput);

        TextView lbl2 = new TextView(this);
        lbl2.setText("Interval (minutes):");
        lbl2.setTextColor(0xFFcccccc);
        lbl2.setTextSize(13);
        lbl2.setPadding(0, 16, 0, 0);
        root.addView(lbl2);
        EditText intervalInput = new EditText(this);
        intervalInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        intervalInput.setText("60");
        intervalInput.setTextColor(0xFFffe6a8);
        intervalInput.setTextSize(18);
        intervalInput.setBackgroundColor(0xFF111118);
        intervalInput.setPadding(24, 18, 24, 18);
        root.addView(intervalInput);

        LinearLayout chips = new LinearLayout(this);
        chips.setOrientation(LinearLayout.HORIZONTAL);
        chips.setPadding(0, 12, 0, 0);
        for (int v : new int[]{5, 10, 25, 50}) {
            final int amt = v;
            android.widget.Button b = new android.widget.Button(this);
            b.setText("$" + amt);
            b.setTextSize(12);
            b.setTextColor(0xFFee8833);
            b.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF1a1608));
            LinearLayout.LayoutParams blp = new LinearLayout.LayoutParams(0, 96, 1f);
            blp.setMargins(4, 0, 4, 0);
            b.setLayoutParams(blp);
            b.setOnClickListener(v2 -> amountInput.setText(String.valueOf(amt)));
            chips.addView(b);
        }
        root.addView(chips);

        new AlertDialog.Builder(this)
            .setTitle("Start Recurring Fine")
            .setView(root)
            .setPositiveButton("START", (d, w) -> {
                int amount = 10;
                int interval = 60;
                try { amount = Integer.parseInt(amountInput.getText().toString().trim()); }
                catch (NumberFormatException nfe) { /* default */ }
                try { interval = Integer.parseInt(intervalInput.getText().toString().trim()); }
                catch (NumberFormatException nfe) { /* default */ }
                if (amount <= 0 || interval <= 0) {
                    setStatus("Fine cancelled (invalid amount or interval)");
                    return;
                }
                final int fAmount = amount;
                final int fInterval = interval;
                setStatus("Starting $" + fAmount + " every " + fInterval + "m fine...");
                executor.execute(() -> {
                    String r = meshOrder("start-fine",
                        "{\"amount\":\"" + fAmount + "\",\"interval\":\""
                            + fInterval + "\"}");
                    setStatus(r != null && r.contains("ok")
                        ? "Fine active: $" + fAmount + " every " + fInterval + "m"
                        : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    @SuppressWarnings("unused")
    private void doStartFineLegacy_unused() {
        String[] amounts = {"$5/hr", "$10/hr", "$15/hr", "$20/hr", "$25/hr", "$50/hr"};
        int[] values = {5, 10, 15, 20, 25, 50};
        new AlertDialog.Builder(this)
            .setTitle("\uD83D\uDCB8 Start Recurring Fine")
            .setMessage("How much should bunny pay per hour?")
            .setItems(amounts, (d, which) -> {
                int amount = values[which];
                setStatus("Starting $" + amount + "/hr fine...");
                executor.execute(() -> {
                    String r = meshOrder("start-fine",
                        "{\"amount\":\"" + amount + "\",\"interval\":\"60\"}");
                    setStatus(r != null && r.contains("ok")
                        ? "Fine active: $" + amount + "/hr \uD83D\uDCB8"
                        : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doStopFine() {
        setStatus("Stopping fine...");
        executor.execute(() -> {
            String r = meshOrder("stop-fine", "{}");
            setStatus(r != null && r.contains("ok") ? "Fine stopped" : "Failed");
        });
    }

    // -- Liberation --

    private void doReleaseForever() {
        new AlertDialog.Builder(this)
            .setTitle("RELEASE FOREVER")
            .setMessage("This will permanently remove the collar from ALL devices in the mesh.\n\n"
                + "This action cannot be undone.\n\n"
                + "All restrictions will be lifted.\n"
                + "All apps will self-uninstall.\n"
                + "All configs will be deleted.\n\n"
                + "Are you sure?")
            .setPositiveButton("Yes, Release", (d, w) -> {
                EditText confirm = new EditText(this);
                confirm.setHint("Type RELEASE to confirm");
                confirm.setTextColor(0xFFcc2222);
                new AlertDialog.Builder(this)
                    .setTitle("Type RELEASE to confirm")
                    .setView(confirm)
                    .setPositiveButton("RELEASE FOREVER", (d2, w2) -> {
                        if ("RELEASE".equals(confirm.getText().toString().trim())) {
                            executeReleaseForever();
                        } else {
                            setStatus("Confirmation failed — must type RELEASE");
                        }
                    })
                    .setNegativeButton("Cancel", null)
                    .show();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void executeReleaseForever() {
        setStatus("Releasing all devices...");
        executor.execute(() -> {
            // Send release-device mesh order to all nodes
            String meshResult = meshOrder("release-device", "{\"target\":\"all\"}");

            // Also hit phone API directly (in case mesh is slow)
            String phoneResult = api("/api/release-forever", "{}");

            boolean ok = (meshResult != null && meshResult.contains("ok"))
                      || (phoneResult != null && phoneResult.contains("ok"));

            handler.post(() -> {
                if (ok) {
                    setStatus("RELEASED \u2014 All devices liberated");
                    new AlertDialog.Builder(this)
                        .setTitle("Liberation Complete")
                        .setMessage("All devices have been released from the mesh.\n\n"
                            + "The collar is gone.\n"
                            + "You are free.\n\n"
                            + "This controller can now be closed.")
                        .setPositiveButton("OK", (d, w) -> finish())
                        .setCancelable(false)
                        .show();
                } else {
                    setStatus("Release may have partially failed \u2014 check devices");
                }
            });
        });
    }

    // -- Power Tools --

    private void doClearPaywall() {
        new AlertDialog.Builder(this)
            .setTitle("Clear Paywall")
            .setMessage("Remove the paywall entirely?")
            .setPositiveButton("CLEAR", (d, w) -> {
                setStatus("Clearing paywall...");
                executor.execute(() -> {
                    String r = api("/api/clear-paywall", "{}");
                    meshOrder("clear-paywall", "{}");
                    setStatus(r.contains("ok") ? "Paywall cleared" : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doGamble() {
        String preview;
        if (lastPaywall > 0) {
            int headsPw = (lastPaywall + 1) / 2;  // integer ceil(n/2)
            int tailsPw = lastPaywall * 2;
            preview = "Current paywall: $" + lastPaywall + "\n\n"
                + "Heads = halved to $" + headsPw + "\n"
                + "Tails = doubled to $" + tailsPw + "\n\nAre you sure?";
        } else {
            preview = "Coin flip!\n\nHeads = paywall halved\nTails = paywall DOUBLED\n\nAre you sure?";
        }
        new AlertDialog.Builder(this)
            .setTitle("Double or Nothing")
            .setMessage(preview)
            .setPositiveButton("FLIP", (d, w) -> {
                setStatus("Flipping...");
                executor.execute(() -> {
                    String r = api("/api/gamble", "{}");
                    String result = parseJsonStr(r, "result");
                    String newPw = parseJsonStr(r, "new_paywall");
                    if (!result.isEmpty()) {
                        setStatus((result.equals("heads") ? "HEADS \u2014 halved! $" : "TAILS \u2014 doubled! $") + newPw);
                    } else {
                        setStatus("Failed: " + r);
                    }
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doPlayAudio() {
        EditText urlInput = new EditText(this);
        urlInput.setHint("Audio URL (mp3, wav, etc.)");
        urlInput.setTextColor(0xFFe0e0e0);
        urlInput.setHintTextColor(0xFF555555);
        urlInput.setBackgroundColor(0xFF111118);
        urlInput.setPadding(32, 24, 32, 24);
        new AlertDialog.Builder(this)
            .setTitle("Play Audio")
            .setMessage("Enter a URL to play at max volume on the phone:")
            .setView(urlInput)
            .setPositiveButton("PLAY", (d, w) -> {
                String url = urlInput.getText().toString().trim();
                if (url.isEmpty()) return;
                setStatus("Playing audio...");
                executor.execute(() -> {
                    String r = api("/api/play-audio", "{\"url\":\"" + esc(url) + "\"}");
                    setStatus(r.contains("ok") ? "Audio playing" : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doSpeak() {
        EditText textInput = new EditText(this);
        textInput.setHint("Text to speak aloud");
        textInput.setTextColor(0xFFe0e0e0);
        textInput.setHintTextColor(0xFF555555);
        textInput.setBackgroundColor(0xFF111118);
        textInput.setPadding(32, 24, 32, 24);
        new AlertDialog.Builder(this)
            .setTitle("Speak Through Phone")
            .setMessage("Text will be spoken aloud via TTS at max volume:")
            .setView(textInput)
            .setPositiveButton("SPEAK", (d, w) -> {
                String text = textInput.getText().toString().trim();
                if (text.isEmpty()) return;
                setStatus("Speaking...");
                executor.execute(() -> {
                    String r = api("/api/speak", "{\"text\":\"" + esc(text) + "\"}");
                    setStatus(r.contains("ok") ? "Speaking on phone" : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doSetGeofence() {
        setStatus("Getting location...");
        executor.execute(() -> {
            String locResp = api("/api/get-location", "{}");
            String prefillLat = "", prefillLon = "";
            if (locResp != null && locResp.contains("\"lat\":")) {
                prefillLat = parseJsonNumStr(locResp, "lat");
                prefillLon = parseJsonNumStr(locResp, "lon");
            }
            final String fLat = prefillLat, fLon = prefillLon;
            handler.post(() -> showGeofenceDialog(fLat, fLon));
        });
    }

    private void showGeofenceDialog(String prefillLat, String prefillLon) {
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(32, 16, 32, 0);
        EditText latInput = new EditText(this);
        latInput.setHint("Latitude");
        latInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER | android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL | android.text.InputType.TYPE_NUMBER_FLAG_SIGNED);
        latInput.setTextColor(0xFFe0e0e0); latInput.setHintTextColor(0xFF555555);
        if (!prefillLat.isEmpty()) latInput.setText(prefillLat);
        EditText lonInput = new EditText(this);
        lonInput.setHint("Longitude");
        lonInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER | android.text.InputType.TYPE_NUMBER_FLAG_DECIMAL | android.text.InputType.TYPE_NUMBER_FLAG_SIGNED);
        lonInput.setTextColor(0xFFe0e0e0); lonInput.setHintTextColor(0xFF555555);
        if (!prefillLon.isEmpty()) lonInput.setText(prefillLon);
        EditText radiusInput = new EditText(this);
        radiusInput.setHint("Radius in meters (default 500)");
        radiusInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        radiusInput.setTextColor(0xFFe0e0e0); radiusInput.setHintTextColor(0xFF555555);
        layout.addView(latInput); layout.addView(lonInput); layout.addView(radiusInput);
        setStatus("Location pre-filled");

        new AlertDialog.Builder(this)
            .setTitle("Set Geofence")
            .setMessage("Auto-lock with $100 paywall if phone leaves this area:")
            .setView(layout)
            .setPositiveButton("SET", (d, w) -> {
                String lat = latInput.getText().toString().trim();
                String lon = lonInput.getText().toString().trim();
                String radius = radiusInput.getText().toString().trim();
                if (lat.isEmpty() || lon.isEmpty()) return;
                setStatus("Setting geofence...");
                executor.execute(() -> {
                    String json = "{\"lat\":\"" + lat + "\",\"lon\":\"" + lon + "\"";
                    if (!radius.isEmpty()) json += ",\"radius\":\"" + radius + "\"";
                    json += "}";
                    String r = api("/api/set-geofence", json);
                    setStatus(r.contains("ok") ? "Geofence set" : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doConfineHome() {
        new AlertDialog.Builder(this)
            .setTitle("Confine to Home")
            .setMessage("Set a 100m geofence at the bunny's current location.\n\nPhone will auto-lock with $100 paywall if it leaves.")
            .setPositiveButton("CONFINE", (d, w) -> {
                setStatus("Confining...");
                executor.execute(() -> {
                    // Single-action confine-home: Collar reads its own GPS and
                    // sets the geofence atomically. The previous two-step
                    // get-location → set-geofence dance silently broke in
                    // vault mode because vault append is fire-and-forget —
                    // Lion's Share never got the location response back.
                    String r = api("/api/confine-home", "{\"radius\":\"100\"}");
                    setStatus(r != null && r.contains("ok") ? "Confined (100m radius)"
                        : (r != null && r.contains("no location fix") ? "No GPS fix — try again outside"
                            : "Failed"));
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doReleaseConfinement() {
        new AlertDialog.Builder(this)
            .setTitle("Release Confinement")
            .setMessage("Clear the home geofence. Bunny can leave without auto-lock.")
            .setPositiveButton("RELEASE", (d, w) -> {
                setStatus("Releasing...");
                executor.execute(() -> {
                    String r = api("/api/clear-geofence", "{}");
                    setStatus(r != null && r.contains("ok") ? "Geofence cleared" : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doPinMessage() {
        EditText msgInput = new EditText(this);
        msgInput.setHint("Message to pin (leave empty to clear)");
        msgInput.setTextColor(0xFFe0e0e0);
        msgInput.setHintTextColor(0xFF555555);
        msgInput.setBackgroundColor(0xFF111118);
        msgInput.setPadding(32, 24, 32, 24);
        new AlertDialog.Builder(this)
            .setTitle("Pin Message")
            .setMessage("Pin a persistent notification on the bunny's phone:")
            .setView(msgInput)
            .setPositiveButton("PIN", (d, w) -> {
                String msg = msgInput.getText().toString().trim();
                setStatus(msg.isEmpty() ? "Clearing pin..." : "Pinning...");
                executor.execute(() -> {
                    String json = "{\"message\":\"" + esc(msg) + "\"}";
                    String r = api("/api/pin-message", json);
                    setStatus(r.contains("ok") ? (msg.isEmpty() ? "Pin cleared" : "Message pinned") : "Failed");
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doPaymentEmail() {
        LinearLayout form = new LinearLayout(this);
        form.setOrientation(LinearLayout.VERTICAL);
        form.setPadding(32, 16, 32, 0);

        EditText hostInput = new EditText(this);
        hostInput.setHint("IMAP host (e.g. imap.gmail.com)");
        hostInput.setTextColor(0xFFe0e0e0);
        hostInput.setHintTextColor(0xFF555555);
        hostInput.setBackgroundColor(0xFF111118);
        hostInput.setPadding(24, 16, 24, 16);
        hostInput.setText(prefs.getString("payment_imap_host", ""));
        form.addView(hostInput);

        EditText userInput = new EditText(this);
        userInput.setHint("Email address");
        userInput.setTextColor(0xFFe0e0e0);
        userInput.setHintTextColor(0xFF555555);
        userInput.setBackgroundColor(0xFF111118);
        userInput.setPadding(24, 16, 24, 16);
        userInput.setInputType(android.text.InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        userInput.setText(prefs.getString("payment_imap_user", ""));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = 12;
        userInput.setLayoutParams(lp);
        form.addView(userInput);

        EditText passInput = new EditText(this);
        passInput.setHint("App password");
        passInput.setTextColor(0xFFe0e0e0);
        passInput.setHintTextColor(0xFF555555);
        passInput.setBackgroundColor(0xFF111118);
        passInput.setPadding(24, 16, 24, 16);
        passInput.setInputType(android.text.InputType.TYPE_CLASS_TEXT | android.text.InputType.TYPE_TEXT_VARIATION_PASSWORD);
        passInput.setText(prefs.getString("payment_imap_pass", ""));
        LinearLayout.LayoutParams lp2 = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp2.topMargin = 12;
        passInput.setLayoutParams(lp2);
        form.addView(passInput);

        new AlertDialog.Builder(this)
            .setTitle("Payment Email")
            .setMessage("Set the IMAP inbox to scan for incoming e-Transfers. Use an app-specific password.")
            .setView(form)
            .setPositiveButton("Save", (d, w) -> {
                String host = hostInput.getText().toString().trim();
                String user = userInput.getText().toString().trim();
                String pass = passInput.getText().toString().trim();
                if (host.isEmpty() || user.isEmpty() || pass.isEmpty()) {
                    setStatus("All fields required");
                    return;
                }
                prefs.edit()
                    .putString("payment_imap_host", host)
                    .putString("payment_imap_user", user)
                    .putString("payment_imap_pass", pass)
                    .apply();
                setStatus("Sending payment email config...");
                executor.execute(() -> {
                    String json = "{\"imap_host\":\"" + esc(host) + "\","
                        + "\"user\":\"" + esc(user) + "\","
                        + "\"pass\":\"" + esc(pass) + "\"}";
                    String r = api("/api/set-payment-email", json);
                    setStatus(r.contains("ok") ? "Payment email configured" : "Failed: " + r);
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doForceSub() {
        String[] tiers = {"Bronze ($25/wk)", "Silver ($35/wk)", "Gold ($50/wk)"};
        String[] tierKeys = {"bronze", "silver", "gold"};
        new AlertDialog.Builder(this)
            .setTitle("Force Subscription")
            .setItems(tiers, (d, which) -> {
                String tier = tierKeys[which];
                setStatus("Subscribing...");
                executor.execute(() -> {
                    String r = api("/api/subscribe", "{\"tier\":\"" + tier + "\"}");
                    setStatus(r.contains("ok") ? "Subscribed: " + tier : "Failed: " + r);
                });
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    /** Assign (or clear) a deadline-bound task: bunny must clear it before the
     *  deadline, or the server auto-locks (or bumps the paywall). Early clears
     *  roll the next deadline forward from the completion time; never stacks.
     *  Server-side authority — commits {@code b9394fe}. Hits admin/order action
     *  {@code set-deadline-task} (or {@code clear-deadline-task} to cancel). */
    private void doDeadlineTask() {
        final float density = getResources().getDisplayMetrics().density;
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding((int)(16*density), (int)(12*density), (int)(16*density), (int)(8*density));
        root.setBackgroundColor(0xFF0a0a14);

        TextView hint = new TextView(this);
        hint.setText("Bunny must clear this before the deadline. Early clears roll the next deadline forward from the completion time (never stacks).");
        hint.setTextColor(0xFFaaaaaa);
        hint.setTextSize(12);
        hint.setPadding(0, 0, 0, (int)(10*density));
        root.addView(hint);

        EditText taskInputDL = new EditText(this);
        taskInputDL.setHint("Task text (e.g. 'Check in with Sir via photo')");
        taskInputDL.setHintTextColor(0xFF555555);
        taskInputDL.setTextColor(0xFFe0e0e0);
        taskInputDL.setBackgroundColor(0xFF111118);
        taskInputDL.setPadding((int)(12*density), (int)(10*density), (int)(12*density), (int)(10*density));
        taskInputDL.setMinLines(2);
        root.addView(taskInputDL, matchWrap(density, 0, 8));

        TextView deadlineLbl = new TextView(this);
        deadlineLbl.setText("Deadline (minutes from now)");
        deadlineLbl.setTextColor(0xFFc8a84e);
        deadlineLbl.setTextSize(12);
        deadlineLbl.setPadding(0, (int)(4*density), 0, (int)(4*density));
        root.addView(deadlineLbl);
        EditText deadlineInput = new EditText(this);
        deadlineInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        deadlineInput.setText("240");
        deadlineInput.setTextColor(0xFFe0e0e0);
        deadlineInput.setBackgroundColor(0xFF111118);
        deadlineInput.setPadding((int)(12*density), (int)(8*density), (int)(12*density), (int)(8*density));
        root.addView(deadlineInput, matchWrap(density, 0, 4));

        LinearLayout dlChips = new LinearLayout(this);
        dlChips.setOrientation(LinearLayout.HORIZONTAL);
        int[] dlMins = {60, 240, 1440, 4320};
        String[] dlLbls = {"1h", "4h", "1d", "3d"};
        for (int i = 0; i < dlMins.length; i++) {
            final int m = dlMins[i];
            Button chip = new Button(this);
            chip.setText(dlLbls[i]);
            chip.setTextSize(11);
            chip.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF1a1a2a));
            chip.setTextColor(0xFFaabbcc);
            chip.setOnClickListener(v -> deadlineInput.setText(String.valueOf(m)));
            LinearLayout.LayoutParams chipLp = new LinearLayout.LayoutParams(0,
                (int)(34*density), 1f);
            if (i < dlMins.length - 1) chipLp.rightMargin = (int)(3*density);
            dlChips.addView(chip, chipLp);
        }
        root.addView(dlChips, matchWrap(density, 0, 10));

        TextView intervalLbl = new TextView(this);
        intervalLbl.setText("Recur interval (minutes; 0 = one-shot)");
        intervalLbl.setTextColor(0xFFc8a84e);
        intervalLbl.setTextSize(12);
        intervalLbl.setPadding(0, (int)(4*density), 0, (int)(4*density));
        root.addView(intervalLbl);
        EditText intervalInput = new EditText(this);
        intervalInput.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        intervalInput.setText("0");
        intervalInput.setTextColor(0xFFe0e0e0);
        intervalInput.setBackgroundColor(0xFF111118);
        intervalInput.setPadding((int)(12*density), (int)(8*density), (int)(12*density), (int)(8*density));
        root.addView(intervalInput, matchWrap(density, 0, 4));

        LinearLayout ivChips = new LinearLayout(this);
        ivChips.setOrientation(LinearLayout.HORIZONTAL);
        int[] ivMins = {0, 1440, 4320, 10080};
        String[] ivLbls = {"Once", "Daily", "3d", "Weekly"};
        for (int i = 0; i < ivMins.length; i++) {
            final int m = ivMins[i];
            Button chip = new Button(this);
            chip.setText(ivLbls[i]);
            chip.setTextSize(11);
            chip.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF1a1a2a));
            chip.setTextColor(0xFFaabbcc);
            chip.setOnClickListener(v -> intervalInput.setText(String.valueOf(m)));
            LinearLayout.LayoutParams chipLp = new LinearLayout.LayoutParams(0,
                (int)(34*density), 1f);
            if (i < ivMins.length - 1) chipLp.rightMargin = (int)(3*density);
            ivChips.addView(chip, chipLp);
        }
        root.addView(ivChips, matchWrap(density, 0, 10));

        TextView proofLbl = new TextView(this);
        proofLbl.setText("Proof type");
        proofLbl.setTextColor(0xFFc8a84e);
        proofLbl.setTextSize(12);
        proofLbl.setPadding(0, (int)(4*density), 0, (int)(4*density));
        root.addView(proofLbl);
        android.widget.RadioGroup proofGroup = new android.widget.RadioGroup(this);
        proofGroup.setOrientation(LinearLayout.HORIZONTAL);
        String[] proofs = {"none", "typed", "photo"};
        android.widget.RadioButton[] proofBtns = new android.widget.RadioButton[3];
        for (int i = 0; i < proofs.length; i++) {
            android.widget.RadioButton rb = new android.widget.RadioButton(this);
            rb.setText(proofs[i]);
            rb.setTextColor(0xFFe0e0e0);
            rb.setTextSize(13);
            rb.setId(1000 + i);
            if (i == 0) rb.setChecked(true);
            proofBtns[i] = rb;
            proofGroup.addView(rb);
        }
        root.addView(proofGroup, matchWrap(density, 0, 4));

        EditText proofHintInput = new EditText(this);
        proofHintInput.setHint("Proof hint (shown to bunny, e.g. 'Selfie with collar visible')");
        proofHintInput.setHintTextColor(0xFF555555);
        proofHintInput.setTextColor(0xFFe0e0e0);
        proofHintInput.setBackgroundColor(0xFF111118);
        proofHintInput.setPadding((int)(12*density), (int)(8*density), (int)(12*density), (int)(8*density));
        root.addView(proofHintInput, matchWrap(density, 0, 10));

        TextView missLbl = new TextView(this);
        missLbl.setText("On miss");
        missLbl.setTextColor(0xFFc8a84e);
        missLbl.setTextSize(12);
        missLbl.setPadding(0, (int)(4*density), 0, (int)(4*density));
        root.addView(missLbl);
        android.widget.RadioGroup missGroup = new android.widget.RadioGroup(this);
        missGroup.setOrientation(LinearLayout.HORIZONTAL);
        android.widget.RadioButton rbLock = new android.widget.RadioButton(this);
        rbLock.setText("Auto-lock");
        rbLock.setTextColor(0xFFe0e0e0);
        rbLock.setTextSize(13);
        rbLock.setId(2001);
        rbLock.setChecked(true);
        missGroup.addView(rbLock);
        android.widget.RadioButton rbPaywall = new android.widget.RadioButton(this);
        rbPaywall.setText("Paywall bump");
        rbPaywall.setTextColor(0xFFe0e0e0);
        rbPaywall.setTextSize(13);
        rbPaywall.setId(2002);
        missGroup.addView(rbPaywall);
        root.addView(missGroup, matchWrap(density, 0, 4));

        EditText missAmount = new EditText(this);
        missAmount.setHint("Miss amount ($) — only if Paywall bump");
        missAmount.setHintTextColor(0xFF555555);
        missAmount.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        missAmount.setText("10");
        missAmount.setTextColor(0xFFe0e0e0);
        missAmount.setBackgroundColor(0xFF111118);
        missAmount.setPadding((int)(12*density), (int)(8*density), (int)(12*density), (int)(8*density));
        root.addView(missAmount, matchWrap(density, 0, 8));

        android.widget.ScrollView scroll = new android.widget.ScrollView(this);
        scroll.setBackgroundColor(0xFF0a0a14);
        scroll.addView(root);

        final AlertDialog dlg = new AlertDialog.Builder(this)
            .setTitle("\u23F0 Deadline Task")
            .setView(scroll)
            .setPositiveButton("Assign", null)   // wired below to avoid auto-dismiss on validation fail
            .setNeutralButton("Cancel Task", null)
            .setNegativeButton("Close", null)
            .show();

        dlg.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(v -> {
            String text = taskInputDL.getText().toString().trim();
            if (text.isEmpty()) {
                taskInputDL.setError("Required");
                return;
            }
            int mins;
            try { mins = Integer.parseInt(deadlineInput.getText().toString().trim()); }
            catch (Exception e) { deadlineInput.setError("Bad number"); return; }
            if (mins <= 0) { deadlineInput.setError("Must be positive"); return; }
            int intervalMins;
            try { intervalMins = Integer.parseInt(intervalInput.getText().toString().trim()); }
            catch (Exception e) { intervalMins = 0; }
            if (intervalMins < 0) intervalMins = 0;
            String proofType = "none";
            for (int i = 0; i < proofBtns.length; i++) {
                if (proofBtns[i].isChecked()) { proofType = proofs[i]; break; }
            }
            String proofHint = proofHintInput.getText().toString().trim();
            String onMiss = rbPaywall.isChecked() ? "paywall" : "lock";
            int missAmt = 0;
            try { missAmt = Integer.parseInt(missAmount.getText().toString().trim()); }
            catch (Exception e) { missAmt = 0; }
            final int fIntervalMinutes = intervalMins;
            final String fText = text;
            final int fMins = mins;
            final String fProofType = proofType;
            final String fProofHint = proofHint;
            final String fOnMiss = onMiss;
            final int fMissAmt = missAmt;
            dlg.dismiss();
            setStatus("Arming deadline task...");
            executor.execute(() -> {
                StringBuilder jb = new StringBuilder();
                jb.append("{\"text\":\"").append(esc(fText)).append("\"");
                jb.append(",\"deadline_minutes\":").append(fMins);
                jb.append(",\"interval_ms\":").append((long)fIntervalMinutes * 60000L);
                jb.append(",\"proof_type\":\"").append(fProofType).append("\"");
                if (!fProofHint.isEmpty()) jb.append(",\"proof_hint\":\"").append(esc(fProofHint)).append("\"");
                jb.append(",\"on_miss\":\"").append(fOnMiss).append("\"");
                if ("paywall".equals(fOnMiss)) jb.append(",\"miss_amount\":").append(fMissAmt);
                jb.append("}");
                String r = api("/api/set-deadline-task", jb.toString());
                setStatus(r != null && r.contains("ok") ? "Deadline task armed" : "Failed: " + r);
            });
        });

        dlg.getButton(AlertDialog.BUTTON_NEUTRAL).setOnClickListener(v -> {
            dlg.dismiss();
            setStatus("Clearing deadline task...");
            executor.execute(() -> {
                String r = api("/api/clear-deadline-task", "{}");
                setStatus(r != null && r.contains("ok") ? "Deadline task cleared" : "Failed: " + r);
            });
        });
    }

    /** Helper for MATCH_PARENT x WRAP_CONTENT with bottom margin in dp. */
    private LinearLayout.LayoutParams matchWrap(float density, int topDp, int bottomDp) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT,
            LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = (int)(topDp * density);
        lp.bottomMargin = (int)(bottomDp * density);
        return lp;
    }

    // ── Lovense ──

    private void doToy(String action, int level, int duration) {
        executor.execute(() -> {
            String json = "{\"action\":\"" + action
                + "\",\"level\":" + level + ",\"duration\":" + duration + "}";
            String r = api("/api/toy", json);
            if (level == 0) {
                setStatus("Toy stopped");
            } else {
                setStatus(r != null && r.contains("ok") ? "Toy: " + action + " L" + level : "Failed");
            }
        });
    }

    // ── Inbox ──

    private void refreshInbox() {
        if (deviceCardsContainer == null) return;
        executor.execute(() -> {
            String meshResp = currentStatusJson();
            String ledgerResp = meshGet("/mesh/ledger?limit=20");
            // Always fetch the chat thread from the server's signed message
            // store (/api/mesh/{id}/messages/fetch). In vault mode the runtime
            // body's "messages" array only carries the Collar's local
            // dispatch history (Lion's outgoing routed through vault) — it
            // doesn't include Bunny's outgoing messages, which go directly
            // to the server's message store via /api/mesh/{id}/messages/send.
            // Reading from the server in vault mode picks up both directions.
            // doSendInboxMessage now also posts to /messages/send in vault
            // mode so Lion's vault messages land here too.
            String msgsResp = fetchLionMessages(0, 30);
            final String msgsRespFinal = msgsResp;
            handler.post(() -> {
                deviceCardsContainer.removeAllViews();
                if (meshResp != null && meshResp.contains("\"nodes\":")) {
                    buildDeviceCards(meshResp);
                }
                updateSubStatus(meshResp);
                updatePaymentHistory(ledgerResp);
                updateMessageThread(msgsRespFinal);
            });
        });
    }

    private void buildDeviceCards(String meshJson) {
        // Parse "nodes" object — keys are node IDs
        int nodesIdx = meshJson.indexOf("\"nodes\":");
        if (nodesIdx < 0) return;
        // Find the opening brace of the nodes object
        int braceStart = meshJson.indexOf("{", nodesIdx + 8);
        if (braceStart < 0) return;

        // Simple state machine to find top-level keys in nodes object
        int depth = 0;
        int pos = braceStart;
        int keyStart = -1;
        String currentKey = null;
        int valueStart = -1;

        for (int i = braceStart; i < meshJson.length(); i++) {
            char c = meshJson.charAt(i);
            if (c == '{') {
                depth++;
                if (depth == 2) valueStart = i;
            } else if (c == '}') {
                if (depth == 2 && currentKey != null) {
                    String value = meshJson.substring(valueStart, i + 1);
                    String type = parseJsonStr(value, "type");
                    boolean online = value.contains("\"online\": true") || value.contains("\"online\":true");
                    String info = type;
                    if (online) info += " \u2022 online";
                    else info += " \u2022 offline";
                    deviceCardsContainer.addView(createDeviceCard(currentKey, type, online, info));
                    currentKey = null;
                }
                depth--;
                if (depth == 0) break;
            } else if (c == '"' && depth == 1) {
                int qEnd = meshJson.indexOf("\"", i + 1);
                if (qEnd > i) {
                    // Check if this is a key (followed by :)
                    int colonIdx = meshJson.indexOf(":", qEnd);
                    if (colonIdx >= 0) {
                        String between = meshJson.substring(qEnd + 1, colonIdx).trim();
                        if (between.isEmpty()) {
                            currentKey = meshJson.substring(i + 1, qEnd);
                        }
                    }
                    i = qEnd;
                }
            }
        }
    }

    private View createDeviceCard(String nodeId, String type, boolean online, String info) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.HORIZONTAL);
        card.setBackgroundColor(0xFF0e0e1a);
        card.setPadding(24, 16, 24, 16);
        card.setGravity(android.view.Gravity.CENTER_VERTICAL);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = 8;
        card.setLayoutParams(lp);

        // Type icon
        TextView icon = new TextView(this);
        String emoji = "phone".equals(type) ? "\uD83D\uDCF1" :
                       "desktop".equals(type) || "self".equals(type) ? "\uD83D\uDCBB" :
                       "server".equals(type) ? "\uD83D\uDDA5" : "\u2753";
        icon.setText(emoji);
        icon.setTextSize(20);
        icon.setPadding(0, 0, 16, 0);
        card.addView(icon);

        // Info column
        LinearLayout infoCol = new LinearLayout(this);
        infoCol.setOrientation(LinearLayout.VERTICAL);
        infoCol.setLayoutParams(new LinearLayout.LayoutParams(0, LinearLayout.LayoutParams.WRAP_CONTENT, 1));

        TextView name = new TextView(this);
        String nickname = getDeviceNickname(nodeId);
        name.setText(nickname.equals(nodeId) ? nodeId : nickname + " (" + nodeId + ")");
        name.setTextColor(online ? 0xFFe0e0e0 : 0xFF666666);
        name.setTextSize(14);
        infoCol.addView(name);

        TextView status = new TextView(this);
        status.setText(info);
        status.setTextColor(online ? 0xFF44aa44 : 0xFF555555);
        status.setTextSize(11);
        infoCol.addView(status);

        card.addView(infoCol);

        // Online dot
        TextView dot = new TextView(this);
        dot.setText(online ? "\u2B24" : "\u25CB");
        dot.setTextColor(online ? 0xFF44aa44 : 0xFF444444);
        dot.setTextSize(10);
        card.addView(dot);

        // Long-press to rename
        final String deviceId = nodeId;
        card.setOnLongClickListener(v -> {
            showRenameDialog(deviceId);
            return true;
        });

        // Tap to release this device
        card.setOnClickListener(v -> {
            new AlertDialog.Builder(this)
                .setTitle("Release " + deviceId + "?")
                .setMessage("Unlock this device from the collar?")
                .setPositiveButton("RELEASE", (d, w) -> {
                    setStatus("Releasing " + deviceId + "...");
                    executor.execute(() -> {
                        if ("phone".equals(type)) {
                            String r = api("/api/unlock", "{}");
                            setStatus(r.contains("ok") ? deviceId + " released" : "Failed");
                        } else {
                            String r = meshOrder("unlock-device", "{\"target\":\"" + deviceId + "\"}");
                            setStatus(r != null && r.contains("ok") ? deviceId + " released" : "Failed");
                        }
                        handler.post(() -> refreshInbox());
                    });
                })
                .setNegativeButton("Cancel", null)
                .show();
        });

        return card;
    }

    private void updateSubStatus(String meshResp) {
        TextView subText = (TextView) findViewById(getId("sub_status_text"));
        if (subText == null) return;
        if (meshResp == null) { subText.setText("No mesh connection"); return; }
        String orders = meshResp;
        String tier = parseJsonStr(orders, "sub_tier");
        if (tier.isEmpty()) {
            subText.setText("No subscription active");
        } else {
            String totalOwed = parseJsonNumStr(orders, "sub_total_owed");
            subText.setText(tier.toUpperCase() + " tier" +
                (totalOwed != null && !totalOwed.isEmpty() && !totalOwed.equals("0") ? " \u2022 $" + totalOwed + " owed" : " \u2022 current"));
        }
    }

    private void updateMessageThread(String msgsResp) {
        LinearLayout thread = (LinearLayout) findViewById(getId("lion_message_thread"));
        if (thread == null) {
            android.util.Log.w("focusctl", "updateMessageThread: thread view is null (lion_message_thread not found)");
            return;
        }
        if (msgsResp == null) {
            android.util.Log.w("focusctl", "updateMessageThread: msgsResp is null");
            return;
        }
        android.util.Log.i("focusctl", "updateMessageThread: msgsResp len=" + msgsResp.length()
            + " has-messages=" + msgsResp.contains("\"messages\":"));
        thread.removeAllViews();
        try {
            // Parse messages array
            int msgsIdx = msgsResp.indexOf("\"messages\":");
            if (msgsIdx < 0) return;
            int arrStart = msgsResp.indexOf("[", msgsIdx);
            if (arrStart < 0) return;
            int depth = 0; int arrEnd = arrStart;
            for (int i = arrStart; i < msgsResp.length(); i++) {
                if (msgsResp.charAt(i) == '[') depth++;
                else if (msgsResp.charAt(i) == ']') { depth--; if (depth == 0) { arrEnd = i; break; } }
            }
            String arrJson = msgsResp.substring(arrStart, arrEnd + 1);

            // Server returns newest-first; we walk the array, collect raw
            // object strings, then iterate in REVERSE so the chat thread
            // reads oldest-at-top → newest-at-bottom (SMS convention).
            // Combined with auto-scroll-to-bottom, the freshest message is
            // always visible.
            java.util.List<String> objs = new java.util.ArrayList<>();
            int scanPos = 0;
            while (scanPos < arrJson.length() && objs.size() < 30) {
                int s = arrJson.indexOf("{", scanPos);
                if (s < 0) break;
                int dd = 0; int e = s;
                for (int i = s; i < arrJson.length(); i++) {
                    if (arrJson.charAt(i) == '{') dd++;
                    else if (arrJson.charAt(i) == '}') { dd--; if (dd == 0) { e = i; break; } }
                }
                objs.add(arrJson.substring(s, e + 1));
                scanPos = e + 1;
            }
            java.util.Collections.reverse(objs);

            int count = 0;
            for (String obj : objs) {
                String from = parseJsonStr(obj, "from");
                String text = parseJsonStr(obj, "text");
                String msgId = parseJsonStr(obj, "id");
                boolean encrypted = obj.contains("\"encrypted\":true") || obj.contains("\"encrypted\": true");
                boolean pinned = obj.contains("\"pinned\":true") || obj.contains("\"pinned\": true");
                boolean mandatory = obj.contains("\"mandatory_reply\":true");
                boolean replied = obj.contains("\"replied\":true");
                boolean isPraise = obj.contains("\"praise\":true") || obj.contains("\"praise\": true");
                boolean isDeleted = obj.contains("\"deleted\":true") || obj.contains("\"deleted\": true");
                boolean isEdited = obj.contains("\"edited_at\":");
                // New schema (#6) ships read_by as a JSON array of reader
                // identities. Vault-mode history still emits the legacy
                // read_by_bunny boolean.
                String readByBunny = "unread";
                if (obj.contains("\"read_by_bunny\":true")) {
                    readByBunny = "read";
                } else {
                    int rbIdx = obj.indexOf("\"read_by\":");
                    if (rbIdx >= 0) {
                        int rbEnd = obj.indexOf("]", rbIdx);
                        if (rbEnd > rbIdx
                            && obj.substring(rbIdx, rbEnd).contains("\"bunny\"")) {
                            readByBunny = "read";
                        }
                    }
                }

                // Decrypt E2EE messages from bunny
                if (encrypted && "bunny".equals(from)) {
                    String ct = parseJsonStr(obj, "ciphertext");
                    String ek = parseJsonStr(obj, "encrypted_key");
                    String iv = parseJsonStr(obj, "iv");
                    // Lion decrypts with own private key
                    String lionPriv = prefs.getString("lion_privkey", "");
                    if (E2EEHelper.canDecrypt(lionPriv) && ct != null && ek != null && iv != null) {
                        String dec = E2EEHelper.decrypt(ct, ek, iv, lionPriv);
                        text = dec != null ? dec : "[encrypted]";
                    } else {
                        text = "[encrypted — key not available]";
                    }
                } else if (encrypted && "lion".equals(from)) {
                    text = "[encrypted — sent by you]";
                }
                if (text == null) text = "";

                // Check for attachment
                String attachUrl = parseJsonStr(obj, "attachment_url");
                boolean hasAttachment = attachUrl != null && !attachUrl.isEmpty();

                boolean fromBunny = "bunny".equals(from);
                boolean isSystem = "system".equals(from);
                int bgColor = isPraise ? 0xFF1a0e18 : fromBunny ? 0xFF120e1a : isSystem ? 0xFF0e1a0e : 0xFF1a1808;
                int textColor = isPraise ? 0xFFe88ccc : fromBunny ? 0xFFaa88cc : isSystem ? 0xFF66aa66 : 0xFFDAA520;

                String prefix = isPraise ? "\u2764\uFE0F" : fromBunny ? "bunny" : isSystem ? "system" : "you";
                String suffix = "";
                if (isPraise) suffix += " [praise]";
                if (pinned) suffix += " [pinned]";
                if (mandatory && !replied) suffix += " [MUST REPLY]";
                if (fromBunny) suffix += " (" + readByBunny + ")";
                // Lion-only tombstone: Lion sees original text + a [deleted]
                // badge (full audit). Bunny Tasker renders [deleted] in
                // place of the text.
                if (isDeleted) suffix += "  [deleted]";
                if (isEdited && !isDeleted) suffix += "  (edited)";
                if (hasAttachment) suffix += " \uD83D\uDCF7";

                // SMS-app-style bubble row: vertical wrapper holding the
                // bubble and a small timestamp underneath, aligned by author.
                // Lion = self → right; bunny/system/praise → left.
                boolean fromSelf = !fromBunny && !isSystem;
                LinearLayout row = new LinearLayout(this);
                row.setOrientation(LinearLayout.VERTICAL);
                LinearLayout.LayoutParams rowLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
                rowLp.setMargins(0, 4, 0, 4);
                row.setLayoutParams(rowLp);

                // The bubble itself — wrap_content, rounded, max 78% width.
                LinearLayout msgBox = new LinearLayout(this);
                msgBox.setOrientation(LinearLayout.VERTICAL);
                msgBox.setPadding(20, 14, 20, 14);
                android.graphics.drawable.GradientDrawable bubbleBg =
                    new android.graphics.drawable.GradientDrawable();
                bubbleBg.setColor(bgColor);
                bubbleBg.setCornerRadius(28f);
                msgBox.setBackground(bubbleBg);
                LinearLayout.LayoutParams boxLp = new LinearLayout.LayoutParams(
                    LinearLayout.LayoutParams.WRAP_CONTENT, LinearLayout.LayoutParams.WRAP_CONTENT);
                boxLp.gravity = fromSelf ? android.view.Gravity.END : android.view.Gravity.START;
                boxLp.leftMargin = fromSelf ? 80 : 0;
                boxLp.rightMargin = fromSelf ? 0 : 80;
                msgBox.setLayoutParams(boxLp);

                TextView tv = new TextView(this);
                tv.setText(prefix + suffix + "\n" + text);
                tv.setTextColor(textColor);
                tv.setTextSize(13);
                int maxBubblePx = (int) (getResources().getDisplayMetrics().widthPixels * 0.78);
                tv.setMaxWidth(maxBubblePx);
                msgBox.addView(tv);
                row.addView(msgBox);

                // Timestamp under the bubble, dim, on the same edge.
                String tsStr = parseJsonNumStr(obj, "ts");
                if (tsStr != null && !tsStr.isEmpty()) {
                    try {
                        long mts = Long.parseLong(tsStr);
                        if (mts > 0) {
                            TextView tsView = new TextView(this);
                            tsView.setText(formatRelativeTimeForChat(mts));
                            tsView.setTextColor(0xFF6a6275);
                            tsView.setTextSize(10);
                            tsView.setPadding(8, 2, 8, 0);
                            LinearLayout.LayoutParams tsLp = new LinearLayout.LayoutParams(
                                LinearLayout.LayoutParams.WRAP_CONTENT,
                                LinearLayout.LayoutParams.WRAP_CONTENT);
                            tsLp.gravity = fromSelf ? android.view.Gravity.END
                                                    : android.view.Gravity.START;
                            tsView.setLayoutParams(tsLp);
                            row.addView(tsView);
                        }
                    } catch (NumberFormatException nfe) { /* skip ts */ }
                }

                // Load attachment image if present
                if (hasAttachment) {
                    final String attUrl = attachUrl;
                    final String privKey = prefs.getString("lion_privkey", "");
                    android.widget.ImageView img = new android.widget.ImageView(this);
                    img.setAdjustViewBounds(true);
                    img.setMaxHeight(300);
                    img.setScaleType(android.widget.ImageView.ScaleType.FIT_START);
                    img.setPadding(0, 8, 0, 0);
                    msgBox.addView(img);
                    // Load async
                    executor.execute(() -> {
                        try {
                            String resp = meshGet(attUrl);
                            if (resp != null) {
                                String content = parseJsonStr(resp, "content");
                                boolean attEnc = resp.contains("\"encrypted\":true") || resp.contains("\"encrypted\": true");
                                if (attEnc && E2EEHelper.canDecrypt(privKey)) {
                                    String aek = parseJsonStr(resp, "encrypted_key");
                                    String aiv = parseJsonStr(resp, "iv");
                                    String dec = (aek != null && aiv != null) ? E2EEHelper.decrypt(content, aek, aiv, privKey) : null;
                                    if (dec != null) content = dec;
                                }
                                if (content != null) {
                                    byte[] imgBytes = android.util.Base64.decode(content, android.util.Base64.DEFAULT);
                                    android.graphics.Bitmap bmp = android.graphics.BitmapFactory.decodeByteArray(imgBytes, 0, imgBytes.length);
                                    if (bmp != null) {
                                        handler.post(() -> img.setImageBitmap(bmp));
                                    }
                                }
                            }
                        } catch (Exception e) { /* image load failed */ }
                    });
                }

                // Long-press → Lion edit/delete/history menu. Lion has full
                // control regardless of message origin. Bunny Tasker has no
                // equivalent (Bunny cannot edit or delete messages).
                if (msgId != null && !msgId.isEmpty() && !isSystem) {
                    final String fMsgId = msgId;
                    final String fText = text;
                    final boolean fEncrypted = encrypted;
                    final boolean fIsDeleted = isDeleted;
                    final boolean fIsEdited = isEdited;
                    final String fObj = obj;
                    row.setOnLongClickListener(v -> {
                        showLionMessageActions(fMsgId, fText, fEncrypted, fIsDeleted, fIsEdited, fObj);
                        return true;
                    });
                }

                thread.addView(row);
                count++;
            }

            if (count == 0) {
                TextView tv = new TextView(this);
                tv.setText("No messages yet. Send one above.");
                tv.setTextColor(0xFF555555);
                tv.setTextSize(11);
                thread.addView(tv);
            }
            android.util.Log.i("focusctl", "updateMessageThread: rendered " + count + " messages");
            // Auto-scroll to the bottom so the freshest message is visible
            // — SMS-app pattern. post() defers until layout completes.
            ScrollView scroll = (ScrollView) findViewById(getId("lion_message_scroll"));
            if (scroll != null) {
                scroll.post(() -> scroll.fullScroll(View.FOCUS_DOWN));
            }
        } catch (Exception e) {
            android.util.Log.w("focusctl", "updateMessageThread parse/render error", e);
        }
    }

    /** SMS-style "5m ago" / "2h ago" / "3d ago" / "just now" formatter. */
    private String formatRelativeTimeForChat(long ts) {
        if (ts <= 0) return "";
        long diff = System.currentTimeMillis() - ts;
        if (diff < 60_000) return "just now";
        if (diff < 3_600_000) return (diff / 60_000) + "m ago";
        if (diff < 86_400_000) return (diff / 3_600_000) + "h ago";
        return (diff / 86_400_000) + "d ago";
    }

    private void updatePaymentHistory(String ledgerResp) {
        LinearLayout historyContainer = (LinearLayout) findViewById(getId("lion_payment_history"));
        if (historyContainer == null || ledgerResp == null) return;
        historyContainer.removeAllViews();
        try {
            // Parse entries array from ledger response
            int entriesIdx = ledgerResp.indexOf("\"entries\":");
            if (entriesIdx < 0) return;
            int arrStart = ledgerResp.indexOf("[", entriesIdx);
            if (arrStart < 0) return;
            int depth = 0; int arrEnd = arrStart;
            for (int i = arrStart; i < ledgerResp.length(); i++) {
                if (ledgerResp.charAt(i) == '[') depth++;
                else if (ledgerResp.charAt(i) == ']') { depth--; if (depth == 0) { arrEnd = i; break; } }
            }
            String entriesJson = ledgerResp.substring(arrStart, arrEnd + 1);
            // Simple parsing: find each entry object
            int pos = 0;
            while (pos < entriesJson.length()) {
                int objStart = entriesJson.indexOf("{", pos);
                if (objStart < 0) break;
                int d = 0; int objEnd = objStart;
                for (int i = objStart; i < entriesJson.length(); i++) {
                    if (entriesJson.charAt(i) == '{') d++;
                    else if (entriesJson.charAt(i) == '}') { d--; if (d == 0) { objEnd = i; break; } }
                }
                String obj = entriesJson.substring(objStart, objEnd + 1);
                String type = parseJsonStr(obj, "type");
                String amountStr = parseJsonNumStr(obj, "amount");
                String desc = parseJsonStr(obj, "description");
                String balStr = parseJsonNumStr(obj, "balance_after");
                double amount = 0;
                try { amount = Double.parseDouble(amountStr); } catch (Exception e) {}
                boolean isPayment = "payment".equals(type) || "prepay".equals(type) || "historical".equals(type);
                String prefix = isPayment ? "\u2193 $" : "\u2191 $";
                int color = isPayment ? 0xFF44aa44 : 0xFFcc6644;
                if ("historical".equals(type)) color = 0xFF6688aa;
                TextView tv = new TextView(this);
                tv.setText(prefix + String.format("%.0f", amount) + "  " + desc
                    + (balStr != null ? "  |  bal: $" + balStr : ""));
                tv.setTextColor(color);
                tv.setTextSize(11);
                tv.setPadding(0, 6, 0, 6);
                historyContainer.addView(tv);
                pos = objEnd + 1;
            }
            if (historyContainer.getChildCount() == 0) {
                TextView tv = new TextView(this);
                tv.setText("No payment records yet");
                tv.setTextColor(0xFF555555);
                tv.setTextSize(11);
                historyContainer.addView(tv);
            }
            // Show balance summary
            String balanceStr = parseJsonNumStr(ledgerResp, "balance");
            if (balanceStr != null) {
                TextView bal = new TextView(this);
                double balance = 0;
                try { balance = Double.parseDouble(balanceStr); } catch (Exception e) {}
                bal.setText(balance > 0 ? "Bunny owes: $" + String.format("%.0f", balance) :
                           balance < 0 ? "Credit: $" + String.format("%.0f", -balance) : "Balance: $0");
                bal.setTextColor(balance > 0 ? 0xFFcc4444 : 0xFF44aa44);
                bal.setTextSize(13);
                bal.setPadding(0, 8, 0, 0);
                historyContainer.addView(bal, 0);
            }
        } catch (Exception e) { /* parsing error — skip */ }
    }

    // ── App PIN Lock ──

    private void showAppPinPrompt(String correctPin) {
        // Hide the entire UI until PIN is entered
        View root = findViewById(android.R.id.content);
        if (root != null) root.setVisibility(View.INVISIBLE);

        EditText input = new EditText(this);
        input.setInputType(android.text.InputType.TYPE_CLASS_NUMBER | android.text.InputType.TYPE_NUMBER_VARIATION_PASSWORD);
        input.setHint("Enter app PIN");

        new AlertDialog.Builder(this)
            .setTitle("Lion's Share")
            .setMessage("This app is protected.")
            .setView(input)
            .setCancelable(false)
            .setPositiveButton("Enter", (d, w) -> {
                if (input.getText().toString().equals(correctPin)) {
                    if (root != null) root.setVisibility(View.VISIBLE);
                } else {
                    android.widget.Toast.makeText(this, "Wrong PIN", android.widget.Toast.LENGTH_SHORT).show();
                    showAppPinPrompt(correctPin);
                }
            })
            .show();
    }

    private void doSetAppPin() {
        EditText input = new EditText(this);
        input.setInputType(android.text.InputType.TYPE_CLASS_NUMBER);
        input.setHint("4+ digit PIN (empty to remove)");
        String current = prefs.getString("app_pin", "");
        if (!current.isEmpty()) input.setText(current);

        new AlertDialog.Builder(this)
            .setTitle("Set App PIN")
            .setMessage("Protect Lion's Share from nosy bunnies.\nLeave empty to remove PIN.")
            .setView(input)
            .setPositiveButton("Save", (d, w) -> {
                String pin = input.getText().toString().trim();
                if (!pin.isEmpty() && pin.length() < 4) {
                    android.widget.Toast.makeText(this, "PIN must be 4+ digits", android.widget.Toast.LENGTH_SHORT).show();
                    return;
                }
                prefs.edit().putString("app_pin", pin).apply();
                setStatus(pin.isEmpty() ? "App PIN removed" : "App PIN set");
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    // ── Device Renaming ──

    private String getDeviceNickname(String nodeId) {
        return prefs.getString("nick_" + nodeId, nodeId);
    }

    private void showRenameDialog(String nodeId) {
        EditText input = new EditText(this);
        input.setText(getDeviceNickname(nodeId));
        input.setHint("Name for this bunny");

        new AlertDialog.Builder(this)
            .setTitle("Rename: " + nodeId)
            .setView(input)
            .setPositiveButton("Save", (d, w) -> {
                String name = input.getText().toString().trim();
                if (name.isEmpty()) name = nodeId;
                prefs.edit().putString("nick_" + nodeId, name).apply();
                refreshInbox();
            })
            .setNeutralButton("Reset", (d, w) -> {
                prefs.edit().remove("nick_" + nodeId).apply();
                refreshInbox();
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doScheduleMessage() {
        // Date picker, then time picker
        java.util.Calendar cal = java.util.Calendar.getInstance();
        new android.app.DatePickerDialog(this, (v, year, month, day) -> {
            new android.app.TimePickerDialog(this, (v2, hour, minute) -> {
                java.util.Calendar sched = java.util.Calendar.getInstance();
                sched.set(year, month, day, hour, minute, 0);
                scheduledAtMs = sched.getTimeInMillis();
                TextView label = (TextView) findViewById(getId("schedule_label"));
                if (label != null) {
                    label.setText("Scheduled: " + new java.text.SimpleDateFormat("MMM d, h:mm a").format(sched.getTime()));
                    label.setVisibility(View.VISIBLE);
                }
                setStatus("Message will send at " + hour + ":" + String.format("%02d", minute));
            }, cal.get(java.util.Calendar.HOUR_OF_DAY), cal.get(java.util.Calendar.MINUTE), false).show();
        }, cal.get(java.util.Calendar.YEAR), cal.get(java.util.Calendar.MONTH), cal.get(java.util.Calendar.DAY_OF_MONTH)).show();
    }

    private void doSendInboxMessage() {
        EditText msgInput = (EditText) findViewById(getId("inbox_message_input"));
        if (msgInput == null) return;
        String msg = msgInput.getText().toString().trim();
        if (msg.isEmpty()) return;

        boolean pinAsNotif = togglePinNotif != null && togglePinNotif.isChecked();
        // Check for mandatory reply toggle
        android.widget.ToggleButton mandatoryToggle = (android.widget.ToggleButton) findViewById(getId("toggle_mandatory"));
        boolean mandatory = mandatoryToggle != null && mandatoryToggle.isChecked();

        setStatus("Sending...");
        executor.execute(() -> {
            // E2EE: encrypt with bunny's public key if available
            // Lion's Share stores bunny pubkey from pairing in prefs
            String bunnyPubKey = bunnyPubkeyB64;
            StringBuilder json = new StringBuilder("{\"from\":\"lion\"");
            if (E2EEHelper.canEncrypt(bunnyPubKey)) {
                E2EEHelper.EncryptedMessage enc = E2EEHelper.encrypt(msg, bunnyPubKey);
                if (enc != null) {
                    json.append(",\"encrypted\":true");
                    json.append(",\"ciphertext\":\"").append(esc(enc.ciphertext)).append("\"");
                    json.append(",\"encrypted_key\":\"").append(esc(enc.encryptedKey)).append("\"");
                    json.append(",\"iv\":\"").append(esc(enc.iv)).append("\"");
                } else {
                    json.append(",\"text\":\"").append(esc(msg)).append("\"");
                }
            } else {
                json.append(",\"text\":\"").append(esc(msg)).append("\"");
            }
            if (pinAsNotif) json.append(",\"pinned\":true");
            if (mandatory) json.append(",\"mandatory_reply\":true,\"reply_deadline_minutes\":15");
            if (scheduledAtMs > 0) json.append(",\"scheduled_at\":").append(scheduledAtMs);
            json.append("}");

            // Phase D: in vault mode, route through api() → apiVault →
            // /vault/{id}/append. The slave's vaultSync dispatches a
            // send-message action that persists to history (Collar local) +
            // updates focus_lock_pinned_message + triggers an immediate
            // runtime push.
            //
            // ALSO post to /api/mesh/{id}/messages/send (the bunny-/lion-authed
            // server-side message store). Without this, vault-mode messages
            // never reach Bunny Tasker's chat thread (which fetches from the
            // server, not from the Collar's local history). Belt-and-
            // suspenders: vault path is for fast Collar dispatch / pinned
            // banner / runtime mirror; server-store path is for the chat
            // thread on Bunny Tasker and for Lion's own multi-device sync.
            String r;
            if (vaultMode && !"direct".equals(pairMode)) {
                r = api("/api/send-message", json.toString());
                E2EEHelper.EncryptedMessage encVault = null;
                if (E2EEHelper.canEncrypt(bunnyPubKey)) {
                    encVault = E2EEHelper.encrypt(msg, bunnyPubKey);
                }
                postLionMessage(msg, pinAsNotif, mandatory, encVault);
            } else {
                E2EEHelper.EncryptedMessage enc = null;
                if (E2EEHelper.canEncrypt(bunnyPubKey)) {
                    enc = E2EEHelper.encrypt(msg, bunnyPubKey);
                }
                boolean ok = postLionMessage(msg, pinAsNotif, mandatory, enc);
                r = ok ? "{\"ok\":true}" : null;

                // Also push to direct-mode Collar HTTP via the legacy api()
                // helper so the bunny's phone gets an immediate notif even
                // when polling is slow.
                String apiJson = "{\"message\":\"" + esc(msg) + "\"}";
                if (pinAsNotif) {
                    api("/api/pin-message", apiJson);
                } else {
                    api("/api/message", apiJson);
                }
            }

            final String result = r;
            setStatus(result != null && result.contains("ok") ?
                (mandatory ? "Sent (reply required)" : pinAsNotif ? "Pinned" : "Sent") : "Sent via API");
            handler.post(() -> {
                msgInput.setText("");
                if (mandatoryToggle != null) mandatoryToggle.setChecked(false);
                scheduledAtMs = 0;
                TextView schedLabel = (TextView) findViewById(getId("schedule_label"));
                if (schedLabel != null) schedLabel.setVisibility(View.GONE);
            });
        });
    }

    // ── Roadmap #6 messaging client (lion side) ──
    // Three signed POST helpers that hit the new bunny-/lion-authed message
    // endpoints. node_id="controller" matches our vault registration.
    // Replaces the dead /mesh/message{,s,/read,/replied} legacy paths the
    // server stopped serving. Vault-mode messaging continues to flow through
    // /api/send-message → vault append (unchanged) — these new helpers are
    // for the non-vault path and the on-demand mark-read flow.

    /** Sign + POST a lion-authored message to /api/mesh/{id}/messages/send.
     *  Returns true on 200. Blocking — call from executor. */
    private boolean postLionMessage(String text, boolean pinned, boolean mandatory,
                                    E2EEHelper.EncryptedMessage enc) {
        if (meshUrl.isEmpty() || meshId.isEmpty()) return false;
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return false;
        long ts = System.currentTimeMillis();
        String signedText = (enc != null) ? "[e2ee]" : text;
        String payload = meshId + "|controller|lion|" + signedText
            + "|" + (pinned ? "1" : "0") + "|" + (mandatory ? "1" : "0") + "|" + ts;
        try {
            String signature = VaultCrypto.signString(payload, lionPriv);
            org.json.JSONObject body = new org.json.JSONObject();
            body.put("node_id", "controller");
            body.put("from", "lion");
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
            String resp = meshPost(meshUrl + "/api/mesh/" + meshId + "/messages/send", body.toString());
            return resp != null && resp.contains("\"ok\"");
        } catch (Exception e) {
            android.util.Log.w("focusctl", "postLionMessage failed: " + e.getMessage());
            return false;
        }
    }

    /** Signed fetch — returns raw response string or null on error. */
    private String fetchLionMessages(long since, int limit) {
        if (meshUrl.isEmpty() || meshId.isEmpty()) return null;
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return null;
        long ts = System.currentTimeMillis();
        String payload = meshId + "|controller|lion|" + since + "|" + ts;
        try {
            String signature = VaultCrypto.signString(payload, lionPriv);
            org.json.JSONObject body = new org.json.JSONObject();
            body.put("node_id", "controller");
            body.put("from", "lion");
            body.put("since", since);
            body.put("limit", limit);
            body.put("ts", ts);
            body.put("signature", signature);
            return meshPost(meshUrl + "/api/mesh/" + meshId + "/messages/fetch", body.toString());
        } catch (Exception e) {
            android.util.Log.w("focusctl", "fetchLionMessages failed: " + e.getMessage());
            return null;
        }
    }

    /** Sign + POST a single mark (status="read" or "replied"). */
    private boolean markLionMessage(String messageId, String status) {
        if (meshUrl.isEmpty() || meshId.isEmpty() || messageId == null || messageId.isEmpty()) return false;
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) return false;
        long ts = System.currentTimeMillis();
        String payload = meshId + "|controller|lion|" + messageId + "|" + status + "|" + ts;
        try {
            String signature = VaultCrypto.signString(payload, lionPriv);
            org.json.JSONObject body = new org.json.JSONObject();
            body.put("node_id", "controller");
            body.put("from", "lion");
            body.put("message_id", messageId);
            body.put("status", status);
            body.put("ts", ts);
            body.put("signature", signature);
            String resp = meshPost(meshUrl + "/api/mesh/" + meshId + "/messages/mark", body.toString());
            return resp != null && resp.contains("\"ok\"");
        } catch (Exception e) {
            android.util.Log.w("focusctl", "markLionMessage failed: " + e.getMessage());
            return false;
        }
    }

    /** Mark every bunny message Lion hasn't yet acked as read.
     *  Phase D vault-mode meshes still skip — vault has no per-message
     *  read-state plumbing yet (would need a mark-read action stored in
     *  the slave's history). */
    private void markLionRead() {
        if (vaultMode && !"direct".equals(pairMode)) return;
        executor.execute(() -> {
            try {
                String resp = fetchLionMessages(0, 50);
                if (resp == null) return;
                org.json.JSONObject root = new org.json.JSONObject(resp);
                org.json.JSONArray msgs = root.optJSONArray("messages");
                if (msgs == null) return;
                for (int i = 0; i < msgs.length(); i++) {
                    org.json.JSONObject m = msgs.optJSONObject(i);
                    if (m == null) continue;
                    if (!"bunny".equals(m.optString("from"))) continue;
                    org.json.JSONArray readBy = m.optJSONArray("read_by");
                    boolean alreadyRead = false;
                    if (readBy != null) {
                        for (int j = 0; j < readBy.length(); j++) {
                            if ("lion".equals(readBy.optString(j))) { alreadyRead = true; break; }
                        }
                    }
                    if (alreadyRead) continue;
                    String mid = m.optString("id", "");
                    if (!mid.isEmpty()) markLionMessage(mid, "read");
                }
            } catch (Exception e) {}
        });
    }

    // ── Lion-only message edit / delete (long-press menu) ──
    // Server enforces lion-only via signature; this UI is Lion's surface for
    // those actions. Bunny Tasker has no equivalent. Edit re-encrypts E2EE
    // messages with the original recipient's pubkey. Delete is a tombstone
    // (Lion sees original + badge; Bunny sees [deleted] in place of text).

    private void showLionMessageActions(String msgId, String currentText, boolean encrypted,
                                        boolean isDeleted, boolean isEdited, String rawObj) {
        java.util.List<String> labels = new java.util.ArrayList<>();
        java.util.List<Integer> codes = new java.util.ArrayList<>();
        if (!isDeleted) {
            labels.add("Edit"); codes.add(0);
            labels.add("Delete"); codes.add(1);
        }
        if (isEdited || isDeleted) {
            labels.add("View history"); codes.add(2);
        }
        if (labels.isEmpty()) return;
        CharSequence[] arr = labels.toArray(new CharSequence[0]);
        new AlertDialog.Builder(this)
            .setTitle("Message")
            .setItems(arr, (d, which) -> {
                int code = codes.get(which);
                if (code == 0) doEditMessage(msgId, currentText, encrypted);
                else if (code == 1) doDeleteMessage(msgId);
                else showMessageHistory(rawObj);
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doEditMessage(String msgId, String currentText, boolean encrypted) {
        EditText input = new EditText(this);
        // For E2EE messages Lion can't see her own outgoing plaintext (it
        // was encrypted to bunny's pubkey), so the EditText opens empty;
        // for plaintext or decrypted-incoming messages we pre-fill so Lion
        // can tweak rather than re-type from scratch.
        input.setText(encrypted ? "" : (currentText == null ? "" : currentText));
        input.setTextColor(0xFFe0e0e0);
        input.setBackgroundColor(0xFF111118);
        input.setPadding(32, 24, 32, 24);
        new AlertDialog.Builder(this)
            .setTitle("Edit message")
            .setMessage(encrypted
                ? "Original was encrypted — type the replacement plaintext (will be re-encrypted to bunny's key)."
                : "Edit text:")
            .setView(input)
            .setPositiveButton("Save", (d, w) -> {
                String newText = input.getText().toString().trim();
                if (newText.isEmpty()) { setStatus("Edit cancelled (empty)"); return; }
                executor.execute(() -> postLionMessageEdit(msgId, newText, encrypted));
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void doDeleteMessage(String msgId) {
        new AlertDialog.Builder(this)
            .setTitle("Delete message")
            .setMessage("Bunny will see '[deleted]' in place of the text. You'll still see the original. This can't be undone.")
            .setPositiveButton("DELETE", (d, w) -> {
                executor.execute(() -> postLionMessageDelete(msgId));
            })
            .setNegativeButton("Cancel", null)
            .show();
    }

    private void showMessageHistory(String rawObj) {
        // Render edit_history[] as a stack of prev_text entries. For E2EE
        // messages the prev_text is the "[e2ee]" marker (Lion can't recover
        // her own past plaintext encrypted to bunny). At least the timeline
        // of edits is visible.
        StringBuilder body = new StringBuilder();
        try {
            int hIdx = rawObj.indexOf("\"edit_history\":");
            if (hIdx < 0) {
                body.append("(no edit history)");
            } else {
                int arrStart = rawObj.indexOf("[", hIdx);
                int depth = 0; int arrEnd = arrStart;
                for (int i = arrStart; i < rawObj.length(); i++) {
                    if (rawObj.charAt(i) == '[') depth++;
                    else if (rawObj.charAt(i) == ']') { depth--; if (depth == 0) { arrEnd = i; break; } }
                }
                String hjson = rawObj.substring(arrStart, arrEnd + 1);
                int p = 0; int idx = 1;
                while (p < hjson.length()) {
                    int s = hjson.indexOf("{", p);
                    if (s < 0) break;
                    int d = 0; int e = s;
                    for (int i = s; i < hjson.length(); i++) {
                        if (hjson.charAt(i) == '{') d++;
                        else if (hjson.charAt(i) == '}') { d--; if (d == 0) { e = i; break; } }
                    }
                    String entry = hjson.substring(s, e + 1);
                    String prev = parseJsonStr(entry, "prev_text");
                    String ts = parseJsonNumStr(entry, "ts");
                    body.append("v").append(idx++).append(" @ ").append(ts).append("\n")
                        .append(prev == null ? "(empty)" : prev).append("\n\n");
                    p = e + 1;
                }
                if (idx == 1) body.append("(no edit history)");
            }
        } catch (Exception e) { body.append("history parse error"); }
        new AlertDialog.Builder(this)
            .setTitle("Edit history")
            .setMessage(body.toString())
            .setPositiveButton("Close", null)
            .show();
    }

    /** Sign + POST a lion-authored edit. Returns true on 200. */
    private boolean postLionMessageEdit(String messageId, String newText, boolean reEncrypt) {
        if (meshUrl.isEmpty() || meshId.isEmpty()) return false;
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) {
            handler.post(() -> setStatus("Edit failed: no lion_privkey"));
            return false;
        }
        long ts = System.currentTimeMillis();
        // Same convention as send: signed text is "[e2ee]" for encrypted edits
        // (server stores opaque ciphertext); plaintext for plaintext edits.
        E2EEHelper.EncryptedMessage enc = null;
        String signedText = newText;
        if (reEncrypt && E2EEHelper.canEncrypt(bunnyPubkeyB64)) {
            enc = E2EEHelper.encrypt(newText, bunnyPubkeyB64);
            if (enc != null) signedText = "[e2ee]";
        }
        String payload = meshId + "|controller|lion|edit|" + messageId
            + "|" + signedText + "|" + ts;
        try {
            String signature = VaultCrypto.signString(payload, lionPriv);
            org.json.JSONObject body = new org.json.JSONObject();
            body.put("node_id", "controller");
            body.put("from", "lion");
            body.put("message_id", messageId);
            body.put("text", signedText);
            if (enc != null) {
                body.put("encrypted", true);
                body.put("ciphertext", enc.ciphertext);
                body.put("encrypted_key", enc.encryptedKey);
                body.put("iv", enc.iv);
            }
            body.put("ts", ts);
            body.put("signature", signature);
            String resp = meshPost(meshUrl + "/api/mesh/" + meshId + "/messages/edit", body.toString());
            boolean ok = resp != null && resp.contains("\"ok\"");
            handler.post(() -> {
                setStatus(ok ? "Message edited" : "Edit failed");
                if (ok) refreshInbox();
            });
            return ok;
        } catch (Exception e) {
            handler.post(() -> setStatus("Edit failed: " + e.getMessage()));
            return false;
        }
    }

    /** Sign + POST a lion-authored delete. Tombstone, not hard delete. */
    private boolean postLionMessageDelete(String messageId) {
        if (meshUrl.isEmpty() || meshId.isEmpty()) return false;
        String lionPriv = prefs.getString("lion_privkey", "");
        if (lionPriv.isEmpty()) {
            handler.post(() -> setStatus("Delete failed: no lion_privkey"));
            return false;
        }
        long ts = System.currentTimeMillis();
        String payload = meshId + "|controller|lion|delete|" + messageId + "|" + ts;
        try {
            String signature = VaultCrypto.signString(payload, lionPriv);
            org.json.JSONObject body = new org.json.JSONObject();
            body.put("node_id", "controller");
            body.put("from", "lion");
            body.put("message_id", messageId);
            body.put("ts", ts);
            body.put("signature", signature);
            String resp = meshPost(meshUrl + "/api/mesh/" + meshId + "/messages/delete", body.toString());
            boolean ok = resp != null && resp.contains("\"ok\"");
            handler.post(() -> {
                setStatus(ok ? "Message deleted" : "Delete failed");
                if (ok) refreshInbox();
            });
            return ok;
        } catch (Exception e) {
            handler.post(() -> setStatus("Delete failed: " + e.getMessage()));
            return false;
        }
    }

    /** Start the long-poll ntfy subscriber thread for this mesh. Topic
     *  derives from mesh_id (focuslock-{mesh_id}) — same convention as the
     *  Collar + Bunny Tasker. Wake-up triggers refreshInbox + an immediate
     *  vault poll so messages, lock state, and balance changes land within
     *  ~1s of a server publish. */
    private void startNtfySubscriber() {
        if (ntfyThread != null && ntfyThread.isAlive()) return;
        if (meshId == null || meshId.isEmpty()) {
            android.util.Log.i("focusctl", "ntfy: skipped (mesh_id not set yet)");
            return;
        }
        String topic = "focuslock-" + meshId;
        String server = "https://ntfy.sh";
        ntfyRunning = true;
        ntfyThread = new Thread(() -> ntfySubscribeLoop(server, topic), "ntfy-subscribe");
        ntfyThread.setDaemon(true);
        ntfyThread.start();
        android.util.Log.w("focusctl", "ntfy subscriber started: " + server + "/" + topic);
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
                        org.json.JSONObject msg = new org.json.JSONObject(line);
                        String msgId = msg.optString("id", "");
                        if (!msgId.isEmpty()) since = msgId;
                        String event = msg.optString("event", "");
                        if ("open".equals(event) || "keepalive".equals(event)) continue;
                        String body = msg.optString("message", "");
                        if (!body.isEmpty()) {
                            // Any wake = refresh state + inbox. The version
                            // field in the body is informational; we don't
                            // gate on it because a missed publish (e.g. ntfy
                            // outage) would leave the client stuck.
                            android.util.Log.w("focusctl", "ntfy: wake-up");
                            handler.post(() -> {
                                executor.execute(() -> {
                                    if (vaultMode && !meshId.isEmpty()) {
                                        try { vaultPollLoop(); } catch (Exception ignored) {}
                                    }
                                    try { refreshInbox(); } catch (Exception ignored) {}
                                });
                            });
                        }
                    } catch (Exception ignored) {}
                }
                reader.close();
                backoff = 1;
            } catch (Exception e) {
                android.util.Log.i("focusctl", "ntfy: subscribe error: " + e);
                try { Thread.sleep(backoff * 1000L); } catch (InterruptedException ie) { break; }
                backoff = Math.min(backoff * 2, 60);
            } finally {
                if (conn != null) try { conn.disconnect(); } catch (Exception ignored) {}
            }
        }
    }
}

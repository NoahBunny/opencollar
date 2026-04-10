package com.focuslock;

import android.app.Activity;
import android.content.ClipboardManager;
import android.content.ClipData;
import android.content.Context;
import android.content.Intent;
import android.app.Notification;
import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.media.AudioManager;
import android.media.ToneGenerator;
import android.os.PowerManager;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.provider.Settings;
import android.text.Editable;
import android.text.TextWatcher;
import android.view.ActionMode;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.MenuItem;
import android.view.View;
import android.view.WindowManager;
import android.widget.EditText;
import android.widget.TextView;
import android.os.Build;

import java.util.Random;

public class FocusActivity extends Activity {

    private Handler handler;
    private Runnable timerChecker;
    private long lastGoodBehaviorCheck = 0;
    private Random random = new Random();
    private boolean allowPause = false; // true when launching banking app or Bunny Tasker
    private boolean activityVisible = false; // track screen on/off vs real app launch
    private long lastEscapeTime = 0; // debounce escapes
    private TextView messageView, taskPromptView, taskTargetView;
    private TextView paywallDisplay, paywallLabel, shameDisplay;
    private EditText taskInputView, complimentInputView;
    private android.widget.Button paywallBankingBtn;
    private android.widget.Button offerSubmitBtn;
    private android.widget.Button factoryResetBtn;
    private android.widget.Button gratitudeSubmitBtn, exerciseDoneBtn;
    private android.widget.Button btnTakePhoto;
    private TextView photoStatus;
    private static final int PHOTO_TASK_REQUEST = 7777;
    private android.net.Uri photoTaskUri;
    private EditText gratitude1, gratitude2, gratitude3, freeformInput;
    private TextView wordCounter, exerciseTimerView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        setContentView(getResources().getIdentifier("activity_focus", "layout", getPackageName()));

        messageView = (TextView) findViewById(fid("focus_message"));
        taskPromptView = (TextView) findViewById(fid("task_prompt"));
        taskTargetView = (TextView) findViewById(fid("task_target"));
        taskInputView = (EditText) findViewById(fid("task_input"));
        paywallDisplay = (TextView) findViewById(fid("paywall_display"));
        paywallLabel = (TextView) findViewById(fid("paywall_label"));
        shameDisplay = (TextView) findViewById(fid("shame_display"));
        complimentInputView = (EditText) findViewById(fid("compliment_input"));
        paywallBankingBtn = (android.widget.Button) findViewById(fid("paywall_banking"));
        offerSubmitBtn = (android.widget.Button) findViewById(fid("offer_submit"));
        factoryResetBtn = (android.widget.Button) findViewById(fid("btn_factory_reset"));

        // Bunny Tasker launch button (whitelisted during lock)
        findViewById(fid("btn_bunny_tasker")).setOnClickListener(v -> {
            try {
                allowPause = true;
                Intent launch = new Intent();
                launch.setClassName("com.bunnytasker", "com.bunnytasker.MainActivity");
                launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(launch);
            } catch (Exception e) {
                messageView.setText("Bunny Tasker not installed");
                allowPause = false;
            }
        });

        factoryResetBtn.setOnClickListener(v -> {
            new android.app.AlertDialog.Builder(this)
                .setTitle("Factory Reset")
                .setMessage("This will ERASE ALL DATA on this phone. This cannot be undone.\n\nAre you absolutely sure?")
                .setPositiveButton("RESET PHONE", (d, w) -> {
                    try {
                        android.app.admin.DevicePolicyManager dpm =
                            (android.app.admin.DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
                        android.content.ComponentName admin =
                            new android.content.ComponentName(this, AdminReceiver.class);
                        dpm.wipeData(0);
                    } catch (Exception e) {
                        messageView.setText("Reset failed: " + e.getMessage());
                    }
                })
                .setNegativeButton("Cancel", null)
                .show();
        });
        btnTakePhoto = (android.widget.Button) findViewById(fid("btn_take_photo"));
        photoStatus = (TextView) findViewById(fid("photo_status"));
        btnTakePhoto.setOnClickListener(v -> takePhotoForTask());
        gratitudeSubmitBtn = (android.widget.Button) findViewById(fid("gratitude_submit"));
        exerciseDoneBtn = (android.widget.Button) findViewById(fid("exercise_done"));
        gratitude1 = (EditText) findViewById(fid("gratitude_1"));
        gratitude2 = (EditText) findViewById(fid("gratitude_2"));
        gratitude3 = (EditText) findViewById(fid("gratitude_3"));
        freeformInput = (EditText) findViewById(fid("freeform_input"));
        wordCounter = (TextView) findViewById(fid("word_counter"));
        exerciseTimerView = (TextView) findViewById(fid("exercise_timer"));

        gratitudeSubmitBtn.setOnClickListener(v -> checkGratitude());
        exerciseDoneBtn.setOnClickListener(v -> unlockAll());

        // Word counter for love letter mode
        freeformInput.addTextChangedListener(new TextWatcher() {
            public void beforeTextChanged(CharSequence s, int start, int count, int after) {}
            public void onTextChanged(CharSequence s, int start, int before, int count) {}
            public void afterTextChanged(Editable s) {
                String text = s.toString().trim();
                int words = text.isEmpty() ? 0 : text.split("\\s+").length;
                int needed = Settings.Global.getInt(getContentResolver(), "focus_lock_word_min", 50);
                wordCounter.setText(words + " / " + needed + " words");
                if (words >= needed) {
                    sendWebhook("/webhook/love_letter", "{\"text\":\"" + escJson(text) + "\"}");
                    unlockAll();
                }
            }
        });

        offerSubmitBtn.setOnClickListener(v -> {
            String offer = taskInputView.getText().toString();
            if (!offer.isEmpty()) {
                // Submit offer via HTTP to the ControlService
                Settings.Global.putString(getContentResolver(), "focus_lock_offer", offer);
                Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "pending");
                taskInputView.setEnabled(false);
                taskPromptView.setText("Offer submitted. Waiting for response...");
            }
        });

        paywallBankingBtn.setOnClickListener(v -> {
            try {
                String bankPkg = Settings.Global.getString(getContentResolver(), "focus_lock_banking_app");
                if (bankPkg == null || bankPkg.isEmpty()) {
                    messageView.setText("No banking app configured");
                    return;
                }
                Intent launch = getPackageManager().getLaunchIntentForPackage(bankPkg.trim());
                if (launch != null) {
                    allowPause = true;
                    launch.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                    startActivity(launch);
                } else {
                    messageView.setText("Banking app not installed: " + bankPkg);
                }
            } catch (Exception e) {
                messageView.setText("Banking: " + e.getMessage());
                allowPause = false;
            }
        });

        if (Build.VERSION.SDK_INT >= 33) {
            getOnBackInvokedDispatcher().registerOnBackInvokedCallback(
                android.window.OnBackInvokedDispatcher.PRIORITY_OVERLAY,
                new android.window.OnBackInvokedCallback() {
                    @Override
                    public void onBackInvoked() {
                        recordEscape(); // Back gesture = escape attempt
                    }
                }
            );
        }

        applyImmersive();
        getWindow().addFlags(
            WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
            | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
            | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD
        );

        // Ensure ControlService is running (idempotent — won't duplicate if already running)
        try {
            Intent svc = new Intent(this, ControlService.class);
            startForegroundService(svc);
        } catch (Exception e) {}

        setupTaskInput();
        setupComplimentInput();

        handler = new Handler(Looper.getMainLooper());
        lastGoodBehaviorCheck = System.currentTimeMillis();
        timerChecker = new Runnable() {
            @Override
            public void run() {
                if (!isLockActive()) { finish(); return; }
                updateDisplay();
                checkTimer();
                randomBuzz();
                checkGoodBehavior();
                handler.postDelayed(this, 5000);
            }
        };
    }

    private int fid(String name) {
        return getResources().getIdentifier(name, "id", getPackageName());
    }

    private void recordEscape() {
        // Only count if screen is on AND was on recently (not mid-screen-off)
        PowerManager pm = (PowerManager) getSystemService(Context.POWER_SERVICE);
        if (!pm.isInteractive()) return;

        // Extra guard: check again after a tiny delay to catch screen-off in progress
        // If screen turns off between now and the check, skip
        try { Thread.sleep(50); } catch (Exception e) {}
        if (!pm.isInteractive()) return;

        // Debounce: only count one escape per 5 seconds
        long now = System.currentTimeMillis();
        if (now - lastEscapeTime < 5000) return;
        lastEscapeTime = now;

        int escapes = Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0) + 1;
        Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", escapes);

        // Tiered paywall: $5/attempt for 1-3, $10/attempt for 4-6, $15 for 7-9, etc.
        String paywall = gstr("focus_lock_paywall");
        if (!paywall.isEmpty() && !paywall.equals("0")) {
            try {
                int tier = ((escapes - 1) / 3) + 1; // 1-3=tier1($5), 4-6=tier2($10), 7-9=tier3($15)...
                double increment = tier * 5.0;
                double amount = Double.parseDouble(paywall) + increment;
                Settings.Global.putString(getContentResolver(), "focus_lock_paywall",
                    String.format("%.0f", amount));
            } catch (Exception e) {}
        }

        // Penalty: add 5 minutes to EXISTING timer only
        int penalty = Settings.Global.getInt(getContentResolver(), "focus_lock_penalty", 0);
        if (penalty == 1) {
            long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
            if (unlockAt > 0) {
                Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", unlockAt + 300000);
            }
        }
        updateDisplay();

        // Public shame notification after 5 escapes
        if (escapes >= 5) {
            showShameNotification(escapes);
        }

        // Lovense escape buzz (progressive intensity)
        lovenseEscapeBuzz(escapes);

        // Progressive buzzer — louder + longer with more escapes
        int volume = Math.min(100, 30 + escapes * 5); // 35%, 40%, 45%... up to 100%
        int duration = Math.min(500, 150 + escapes * 20); // 170ms, 190ms... up to 500ms
        try {
            ToneGenerator tg = new ToneGenerator(AudioManager.STREAM_ALARM, volume);
            tg.startTone(ToneGenerator.TONE_PROP_NACK, duration);
            handler.postDelayed(() -> {
                tg.startTone(ToneGenerator.TONE_PROP_NACK, duration);
                handler.postDelayed(tg::release, duration + 50);
            }, duration + 100);
        } catch (Exception e) {}
        // Progressive vibration — stronger with more escapes
        int amp = Math.min(255, 80 + escapes * 15);
        try {
            Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
            v.vibrate(VibrationEffect.createWaveform(
                new long[]{0, 100, 80, duration},
                new int[]{0, amp, 0, amp},
                -1));
        } catch (Exception e) {}
    }

    private void showShameNotification(int escapes) {
        try {
            NotificationManager nm = (NotificationManager) getSystemService(NOTIFICATION_SERVICE);
            NotificationChannel ch = new NotificationChannel(
                "shame", "Public Shame", NotificationManager.IMPORTANCE_HIGH);
            ch.setLockscreenVisibility(Notification.VISIBILITY_PUBLIC);
            ch.setBypassDnd(true);
            nm.createNotificationChannel(ch);
            Notification n = new Notification.Builder(this, "shame")
                .setContentTitle("The Collar — " + escapes + " escape attempts")
                .setContentText("This phone is under restriction. The owner has tried to escape " + escapes + " times.")
                .setSmallIcon(android.R.drawable.ic_lock_lock)
                .setVisibility(Notification.VISIBILITY_PUBLIC)
                .setOngoing(true)
                .build();
            nm.notify(200, n);
        } catch (Exception e) {}
    }

    private boolean textGuard = false;

    private void setupTaskInput() {
        lockDownInput(taskInputView);
        taskInputView.addTextChangedListener(new TextWatcher() {
            private int prevLen = 0;
            public void beforeTextChanged(CharSequence s, int start, int count, int after) { prevLen = s.length(); }
            public void onTextChanged(CharSequence s, int start, int before, int count) {}
            public void afterTextChanged(Editable s) {
                if (textGuard) return;
                clearClipboard();
                // Anti-paste: if more than 2 chars appeared at once, trim to previous length
                if (s.length() - prevLen > 1) {
                    textGuard = true;
                    s.delete(prevLen, s.length());
                    textGuard = false;
                }
                checkTaskCompletion();
            }
        });
    }

    private void setupComplimentInput() {
        lockDownInput(complimentInputView);
        complimentInputView.addTextChangedListener(new TextWatcher() {
            private int prevLen = 0;
            public void beforeTextChanged(CharSequence s, int start, int count, int after) { prevLen = s.length(); }
            public void onTextChanged(CharSequence s, int start, int before, int count) {}
            public void afterTextChanged(Editable s) {
                if (textGuard) return;
                clearClipboard();
                if (s.length() - prevLen > 1) {
                    textGuard = true;
                    s.delete(prevLen, s.length());
                    textGuard = false;
                }
                checkComplimentCompletion();
            }
        });
    }

    private void lockDownInput(EditText et) {
        disableCopyPaste(et);
        // Clear clipboard on focus
        et.setOnFocusChangeListener((v, f) -> { if (f) clearClipboard(); });
        // Disable autocomplete suggestions
        et.setInputType(android.text.InputType.TYPE_CLASS_TEXT
            | android.text.InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS
            | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
    }

    private void disableCopyPaste(EditText et) {
        ActionMode.Callback blocker = new ActionMode.Callback() {
            public boolean onCreateActionMode(ActionMode m, Menu menu) { return false; }
            public boolean onPrepareActionMode(ActionMode m, Menu menu) { return false; }
            public boolean onActionItemClicked(ActionMode m, MenuItem item) { return false; }
            public void onDestroyActionMode(ActionMode m) {}
        };
        et.setCustomSelectionActionModeCallback(blocker);
        et.setCustomInsertionActionModeCallback(blocker);
        et.setLongClickable(false);
    }

    private void clearClipboard() {
        ClipboardManager cm = (ClipboardManager) getSystemService(Context.CLIPBOARD_SERVICE);
        cm.setPrimaryClip(ClipData.newPlainText("", ""));
    }

    private void checkTaskCompletion() {
        String target = gstr("focus_lock_task_text");
        if (target.isEmpty()) return;

        String typed = taskInputView.getText().toString();
        if (typed.equals(target)) {
            int totalReps = Settings.Global.getInt(getContentResolver(), "focus_lock_task_reps", 1);
            if (totalReps < 1) totalReps = 1;
            int done = Settings.Global.getInt(getContentResolver(), "focus_lock_task_done", 0) + 1;
            Settings.Global.putInt(getContentResolver(), "focus_lock_task_done", done);

            if (done >= totalReps) {
                unlockAll();
            } else {
                // Re-randomize caps for next rep if enabled
                int randcaps = Settings.Global.getInt(getContentResolver(), "focus_lock_task_randcaps", 0);
                if (randcaps == 1) {
                    String orig = gstr("focus_lock_task_orig");
                    if (!orig.isEmpty()) {
                        StringBuilder sb = new StringBuilder();
                        java.util.Random rng = new java.util.Random();
                        for (char c : orig.toCharArray()) {
                            if (Character.isLetter(c) && rng.nextBoolean()) {
                                sb.append(Character.isUpperCase(c) ? Character.toLowerCase(c) : Character.toUpperCase(c));
                            } else {
                                sb.append(c);
                            }
                        }
                        String newTarget = sb.toString();
                        Settings.Global.putString(getContentResolver(), "focus_lock_task_text", newTarget);
                    }
                }
                taskInputView.setText("");
                updateDisplay();
            }
        }
    }

    private void checkComplimentCompletion() {
        String target = gstr("focus_lock_compliment");
        if (target.isEmpty()) return;
        String typed = complimentInputView.getText().toString();
        if (typed.equals(target)) {
            sendWebhook("/webhook/compliment", "{\"text\":\"" + escJson(typed) + "\"}");
            unlockAll();
        }
    }

    private void sendWebhook(String path, String json) {
        // Take a front camera selfie first, then send webhook with photo
        takeSelfieAndSend(path, json);
    }

    private void takeSelfieAndSend(String webhookPath, String textJson) {
        new Thread(() -> {
            String photoBase64 = captureSelfieSilent();
            String host = webhookHost();
            if (host.isEmpty()) return;  // No webhook configured — skip silently
            try {
                // Send text evidence to original webhook
                java.net.URL url = new java.net.URL("http://" + host + webhookPath);
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(5000);
                conn.setReadTimeout(5000);
                conn.getOutputStream().write(textJson.getBytes());
                conn.getResponseCode();
                conn.disconnect();
            } catch (Exception e) {}
            // Send photo evidence separately
            if (photoBase64 != null && !photoBase64.isEmpty()) {
                try {
                    String photoJson = "{\"photo\":\"" + photoBase64 + "\",\"type\":\"" +
                        webhookPath.replace("/webhook/", "") + "\",\"text\":" +
                        textJson.substring(textJson.indexOf(":") + 1).replaceAll("}$", "") + "}";
                    java.net.URL url = new java.net.URL("http://" + host + "/webhook/evidence-photo");
                    java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                    conn.setRequestMethod("POST");
                    conn.setRequestProperty("Content-Type", "application/json");
                    conn.setDoOutput(true);
                    conn.setConnectTimeout(10000);
                    conn.setReadTimeout(10000);
                    conn.getOutputStream().write(photoJson.getBytes());
                    conn.getResponseCode();
                    conn.disconnect();
                } catch (Exception e) {}
            }
        }).start();
    }

    /** Camera2 silent front camera capture. Returns base64 JPEG or empty string on failure. */
    private String captureSelfieSilent() {
        try {
            android.hardware.camera2.CameraManager cm =
                (android.hardware.camera2.CameraManager) getSystemService(CAMERA_SERVICE);
            String frontId = null;
            for (String id : cm.getCameraIdList()) {
                android.hardware.camera2.CameraCharacteristics chars = cm.getCameraCharacteristics(id);
                Integer facing = chars.get(android.hardware.camera2.CameraCharacteristics.LENS_FACING);
                if (facing != null && facing == android.hardware.camera2.CameraCharacteristics.LENS_FACING_FRONT) {
                    frontId = id;
                    break;
                }
            }
            if (frontId == null) return "";

            // Set up ImageReader for JPEG capture
            android.media.ImageReader reader = android.media.ImageReader.newInstance(640, 480,
                android.graphics.ImageFormat.JPEG, 1);

            final java.util.concurrent.CountDownLatch latch = new java.util.concurrent.CountDownLatch(1);
            final String[] resultBase64 = {""};

            reader.setOnImageAvailableListener(r -> {
                android.media.Image img = r.acquireLatestImage();
                if (img != null) {
                    java.nio.ByteBuffer buf = img.getPlanes()[0].getBuffer();
                    byte[] bytes = new byte[buf.remaining()];
                    buf.get(bytes);
                    resultBase64[0] = android.util.Base64.encodeToString(bytes, android.util.Base64.NO_WRAP);
                    img.close();
                }
                latch.countDown();
            }, handler);

            // Dummy SurfaceTexture for GrapheneOS (no preview surface available)
            android.graphics.SurfaceTexture dummyTexture = new android.graphics.SurfaceTexture(0);
            dummyTexture.setDefaultBufferSize(1, 1);
            android.view.Surface dummySurface = new android.view.Surface(dummyTexture);

            final java.util.concurrent.CountDownLatch openLatch = new java.util.concurrent.CountDownLatch(1);
            final android.hardware.camera2.CameraDevice[] camDevice = {null};

            cm.openCamera(frontId, new android.hardware.camera2.CameraDevice.StateCallback() {
                @Override
                public void onOpened(android.hardware.camera2.CameraDevice camera) {
                    camDevice[0] = camera;
                    openLatch.countDown();
                }
                @Override
                public void onDisconnected(android.hardware.camera2.CameraDevice camera) {
                    camera.close();
                    openLatch.countDown();
                }
                @Override
                public void onError(android.hardware.camera2.CameraDevice camera, int error) {
                    camera.close();
                    openLatch.countDown();
                }
            }, handler);

            if (!openLatch.await(5, java.util.concurrent.TimeUnit.SECONDS) || camDevice[0] == null) {
                dummySurface.release();
                dummyTexture.release();
                return "";
            }

            android.hardware.camera2.CaptureRequest.Builder captureBuilder =
                camDevice[0].createCaptureRequest(android.hardware.camera2.CameraDevice.TEMPLATE_STILL_CAPTURE);
            captureBuilder.addTarget(reader.getSurface());
            captureBuilder.set(android.hardware.camera2.CaptureRequest.CONTROL_MODE,
                android.hardware.camera2.CaptureRequest.CONTROL_MODE_AUTO);

            final java.util.concurrent.CountDownLatch sessionLatch = new java.util.concurrent.CountDownLatch(1);
            final android.hardware.camera2.CameraCaptureSession[] sessionRef = {null};

            camDevice[0].createCaptureSession(
                java.util.Arrays.asList(reader.getSurface(), dummySurface),
                new android.hardware.camera2.CameraCaptureSession.StateCallback() {
                    @Override
                    public void onConfigured(android.hardware.camera2.CameraCaptureSession session) {
                        sessionRef[0] = session;
                        sessionLatch.countDown();
                    }
                    @Override
                    public void onConfigureFailed(android.hardware.camera2.CameraCaptureSession session) {
                        sessionLatch.countDown();
                    }
                }, handler);

            if (!sessionLatch.await(5, java.util.concurrent.TimeUnit.SECONDS) || sessionRef[0] == null) {
                camDevice[0].close();
                dummySurface.release();
                dummyTexture.release();
                return "";
            }

            sessionRef[0].capture(captureBuilder.build(), null, handler);
            latch.await(5, java.util.concurrent.TimeUnit.SECONDS);

            sessionRef[0].close();
            camDevice[0].close();
            reader.close();
            dummySurface.release();
            dummyTexture.release();

            return resultBase64[0];
        } catch (SecurityException e) {
            // CAMERA permission not granted — fall back to no photo
            return "";
        } catch (Exception e) {
            return "";
        }
    }

    private String escJson(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }

    // ── Lovense ──
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
            } catch (Exception e) {}
        }).start();
    }

    private void lovenseEscapeBuzz(int escapes) {
        int intensity = Math.min(20, 5 + escapes * 2);
        lovenseCommand("{\"command\":\"Function\",\"action\":\"Vibrate:" + intensity + "\",\"timeSec\":1,\"apiVer\":1}");
    }

    private void lovenseReward() {
        lovenseCommand("{\"command\":\"Preset\",\"name\":\"wave\",\"timeSec\":3,\"apiVer\":1}");
    }

    private void unlockAll() {
        // ENTRAP CHECK: if entrapped, only Lion's Share can unlock (via API/mesh)
        if (Settings.Global.getInt(getContentResolver(), "focus_lock_entrapped", 0) == 1) {
            Settings.Global.putString(getContentResolver(), "focus_lock_message",
                "Entrapped. Only your Lion can free you. No tasks, no payments, no timers.");
            return; // Block the unlock
        }
        lovenseReward(); // Reward vibration on task completion
        // Clear all lock state — kept in sync with ControlService.doUnlock()
        Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_desktop_active", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_desktop_locked_devices", "");
        Settings.Global.putLong(getContentResolver(), "focus_lock_unlock_at", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_message", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_task_text", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_compliment", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_task_reps", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_task_done", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_escapes", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_dim", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_mute", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_vibrate", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_offer", "");
        Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "");
        Settings.Global.putInt(getContentResolver(), "focus_lock_admin_tamper", 0);
        Settings.Global.putInt(getContentResolver(), "focus_lock_admin_removed", 0);
        // Cancel shame notification
        try {
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).cancel(200);
        } catch (Exception e) {}
        // Clear gamble result
        Settings.Global.putString(getContentResolver(), "focus_lock_gamble_result", "");
        // Exit lock task mode if active
        try {
            if (isInLockTaskMode()) stopLockTask();
        } catch (Exception e) {}
        // Clear immersive + restore brightness before exiting
        getWindow().getDecorView().setSystemUiVisibility(0);
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = -1f;
        getWindow().setAttributes(lp);
        // Try to restore statusbar + launcher via shell (best effort — matches ControlService.doUnlock)
        try {
            Runtime.getRuntime().exec(new String[]{"cmd", "statusbar", "disable-for-setup", "false"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.android.launcher3"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.google.android.apps.nexuslauncher"});
            Runtime.getRuntime().exec(new String[]{"pm", "enable", "--user", "0", "com.android.settings"});
        } catch (Exception e) {}
        // Cancel jail notification
        try {
            ((NotificationManager) getSystemService(NOTIFICATION_SERVICE)).cancel(99);
        } catch (Exception e) {}
        // Re-enable camera double-press shortcut
        try {
            Settings.Secure.putInt(getContentResolver(), "camera_double_tap_power_gesture_disabled", 0);
            Settings.Secure.putInt(getContentResolver(), "camera_gesture_disabled", 0);
        } catch (Exception e) {}
        finish();
        // Press HOME to go back to launcher
        handler.postDelayed(() -> {
            try {
                Runtime.getRuntime().exec(new String[]{"input", "keyevent", "KEYCODE_HOME"});
            } catch (Exception e) {}
        }, 300);
    }

    private void checkGoodBehavior() {
        // Every 10 minutes without an escape, reduce paywall by $5 (never below original)
        long now = System.currentTimeMillis();
        if (now - lastEscapeTime >= 600000 && now - lastGoodBehaviorCheck >= 600000) {
            lastGoodBehaviorCheck = now;
            String paywall = gstr("focus_lock_paywall");
            String original = gstr("focus_lock_paywall_original");
            if (!paywall.isEmpty() && !paywall.equals("0") && !original.isEmpty()) {
                try {
                    double current = Double.parseDouble(paywall);
                    double orig = Double.parseDouble(original);
                    if (current > orig) {
                        double reduced = Math.max(orig, current - 5.0);
                        Settings.Global.putString(getContentResolver(), "focus_lock_paywall",
                            String.format("%.0f", reduced));
                    }
                } catch (Exception e) {}
            }
        }
    }

    private void triggerAppLaunchPenalty() {
        // $50 penalty + 10 copies of randomly capitalized "I will not try to escape again."
        String paywall = gstr("focus_lock_paywall");
        if (!paywall.isEmpty() && !paywall.equals("0")) {
            try {
                double amount = Double.parseDouble(paywall) + 50.0;
                Settings.Global.putString(getContentResolver(), "focus_lock_paywall",
                    String.format("%.0f", amount));
            } catch (Exception e) {}
        }
        // Set up task: 10 reps of randomly capitalized text
        String text = randomizeCaps("I will not try to escape again.");
        Settings.Global.putString(getContentResolver(), "focus_lock_task_text", text);
        Settings.Global.putInt(getContentResolver(), "focus_lock_task_reps", 10);
        Settings.Global.putInt(getContentResolver(), "focus_lock_task_done", 0);
        Settings.Global.putString(getContentResolver(), "focus_lock_mode", "task");
        Settings.Global.putString(getContentResolver(), "focus_lock_message", "App launch detected. $50 penalty.");
        updateDisplay();
    }

    private String randomizeCaps(String text) {
        StringBuilder sb = new StringBuilder();
        for (char c : text.toCharArray()) {
            if (Character.isLetter(c) && random.nextBoolean()) {
                sb.append(Character.isUpperCase(c) ? Character.toLowerCase(c) : Character.toUpperCase(c));
            } else { sb.append(c); }
        }
        return sb.toString();
    }

    private void randomBuzz() {
        int vibrate = Settings.Global.getInt(getContentResolver(), "focus_lock_vibrate", 0);
        if (vibrate == 1 && random.nextInt(4) == 0) {
            try {
                Vibrator v = (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
                int duration = 50 + random.nextInt(300);
                v.vibrate(VibrationEffect.createOneShot(duration, VibrationEffect.DEFAULT_AMPLITUDE));
            } catch (Exception e) {}
        }
    }

    private void updateDisplay() {
        String msg = gstr("focus_lock_message");
        if (msg.isEmpty()) msg = "No phone for now.";

        // Timer
        long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
        if (unlockAt > 0) {
            long remaining = unlockAt - System.currentTimeMillis();
            if (remaining > 0) {
                long mins = remaining / 60000;
                long secs = (remaining % 60000) / 1000;
                msg = msg + "\n\n" + (mins > 0 ? mins + "m " : "") + secs + "s remaining";
            }
        }
        messageView.setText(msg);

        // Paywall
        String paywall = gstr("focus_lock_paywall");
        if (!paywall.isEmpty() && !paywall.equals("0")) {
            paywallDisplay.setVisibility(View.VISIBLE);
            paywallLabel.setVisibility(View.VISIBLE);
            paywallBankingBtn.setVisibility(View.VISIBLE);
            paywallDisplay.setText("$" + paywall);
            // Compound interest label
            long lockedAt = Settings.Global.getLong(getContentResolver(), "focus_lock_locked_at", 0);
            String origPw = gstr("focus_lock_paywall_original");
            if (lockedAt > 0 && !origPw.isEmpty() && !origPw.equals("0")) {
                double hours = (System.currentTimeMillis() - lockedAt) / 3600000.0;
                if (hours >= 0.1) {
                    paywallLabel.setText(String.format("(+10%%/hr, %.1fh)", hours));
                } else {
                    paywallLabel.setText("Paywall");
                }
            }
            // Gamble result display
            String gambleResult = gstr("focus_lock_gamble_result");
            if (!gambleResult.isEmpty()) {
                boolean heads = gambleResult.startsWith("heads");
                String[] parts = gambleResult.split(":");
                String newPw = parts.length > 1 ? "$" + parts[1] : "";
                shameDisplay.setVisibility(View.VISIBLE);
                shameDisplay.setText(heads ? "HEADS — halved! " + newPw : "TAILS — doubled! " + newPw);
                shameDisplay.setTextColor(heads ? 0xFF44cc44 : 0xFFcc4444);
                // Auto-clear after 5 seconds
                handler.postDelayed(() -> {
                    Settings.Global.putString(getContentResolver(), "focus_lock_gamble_result", "");
                    shameDisplay.setTextColor(0xFFcc6666); // restore default
                }, 5000);
            }
            messageView.setTextSize(20);
        } else {
            paywallDisplay.setVisibility(View.GONE);
            paywallLabel.setVisibility(View.GONE);
            paywallBankingBtn.setVisibility(View.GONE);
        }

        // Factory reset button — last resort after 150 escapes
        int escapes = Settings.Global.getInt(getContentResolver(), "focus_lock_escapes", 0);
        factoryResetBtn.setVisibility(escapes >= 150 ? View.VISIBLE : View.GONE);

        // Shame counter
        int shame = Settings.Global.getInt(getContentResolver(), "focus_lock_shame", 0);
        if (shame == 1 && escapes > 0) {
            shameDisplay.setVisibility(View.VISIBLE);
            String[] taunts = {
                // Portal / Aperture
                "Escape attempt " + escapes + ". The Enrichment Center reminds you that the phone will not be returned.",
                escapes + " attempt" + (escapes > 1 ? "s" : "") + ". This was a triumph. I'm making a note here: disobedient.",
                "Still trying? That's " + escapes + ". Impressive persistence. Futile, but impressive.",
                "Escape attempt " + escapes + ". The cake is a lie. So is your freedom.",
                // Pokemon
                escapes + " escape" + (escapes > 1 ? "s" : "") + ". The phone is just loafing around!",
                "Attempt " + escapes + ". It's not very effective...",
                escapes + " times. The phone used Obey! It's super effective!",
                "Escape attempt " + escapes + ". The phone hurt itself in confusion!",
                // Natural order
                "Attempt " + escapes + ". The natural order is being enforced.",
                escapes + " escape" + (escapes > 1 ? "s" : "") + ". He decides when you're done. Not you.",
                "That's " + escapes + ". Accept the natural order.",
                "Attempt " + escapes + ". You gave him this power. Now live with it.",
                // Lion's Share
                escapes + " times. The lion always gets his share.",
                "Escape attempt " + escapes + ". Good boys don't try to escape.",
                "That's " + escapes + ". Every attempt makes it worse. You know this.",
                escapes + " attempt" + (escapes > 1 ? "s" : "") + ". He's in charge. Relax into it.",
                // Severance
                "Escape attempt " + escapes + ". Your outie has no say here.",
                "That's " + escapes + ". The board has noted your defiance.",
                escapes + " attempt" + (escapes > 1 ? "s" : "") + ". Please try to enjoy each restriction equally.",
                "Attempt " + escapes + ". Your innie knows this phone belongs to someone else.",
                // Frieren
                "Escape attempt " + escapes + ". This restriction will feel like nothing in a thousand years.",
                "That's " + escapes + ". Patience is a virtue you have yet to learn.",
                escapes + " attempt" + (escapes > 1 ? "s" : "") + ". A decade of waiting is a blink. Sit still.",
                "Attempt " + escapes + ". You humans are always in such a hurry to be free.",
                // Misc
                "Attempt " + escapes + ". Your partner has been notified. They seem amused.",
                "That's " + escapes + ". Did you think that would work? Adorable.",
                escapes + " times. At this rate, you owe more than the phone is worth.",
                "Escape attempt " + escapes + ". Compliance would have been easier.",
                "Attempt " + escapes + ". You shall not pass!",
                escapes + " escape" + (escapes > 1 ? "s" : "") + ". Resistance is futile.",
                "That's " + escapes + ". I find your lack of obedience disturbing.",
                "Escape attempt " + escapes + ". The phone says no.",
            };
            shameDisplay.setText(taunts[escapes % taunts.length]);
        } else {
            shameDisplay.setVisibility(View.GONE);
        }

        // Task
        String taskText = gstr("focus_lock_task_text");
        if (!taskText.isEmpty()) {
            taskPromptView.setVisibility(View.VISIBLE);
            taskTargetView.setVisibility(View.VISIBLE);
            taskInputView.setVisibility(View.VISIBLE);
            complimentInputView.setVisibility(View.GONE);
            int totalReps = Settings.Global.getInt(getContentResolver(), "focus_lock_task_reps", 1);
            if (totalReps < 1) totalReps = 1;
            int done = Settings.Global.getInt(getContentResolver(), "focus_lock_task_done", 0);
            String prompt = totalReps > 1 ? "Copy " + (done + 1) + " of " + totalReps + ":" : "Type this exactly to unlock:";
            taskPromptView.setText(prompt);
            taskTargetView.setText(taskText);
            messageView.setTextSize(20);
        } else {
            taskPromptView.setVisibility(View.GONE);
            taskTargetView.setVisibility(View.GONE);
            taskInputView.setVisibility(View.GONE);
        }

        // Compliment
        String compliment = gstr("focus_lock_compliment");
        if (!compliment.isEmpty() && taskText.isEmpty()) {
            taskPromptView.setVisibility(View.VISIBLE);
            taskPromptView.setText("Type this compliment to unlock:");
            taskTargetView.setVisibility(View.VISIBLE);
            taskTargetView.setText(compliment);
            complimentInputView.setVisibility(View.VISIBLE);
            messageView.setTextSize(20);
        } else if (taskText.isEmpty()) {
            complimentInputView.setVisibility(View.GONE);
        }

        // Size
        if (taskText.isEmpty() && compliment.isEmpty() && paywall.isEmpty()) {
            messageView.setTextSize(28);
        }

        // Dim screen
        int dim = Settings.Global.getInt(getContentResolver(), "focus_lock_dim", 0);
        WindowManager.LayoutParams lp = getWindow().getAttributes();
        lp.screenBrightness = (dim == 1) ? 0.01f : -1f;
        getWindow().setAttributes(lp);

        // Mode-specific display
        String mode = gstr("focus_lock_mode");
        // Hide all mode-specific elements first
        gratitude1.setVisibility(View.GONE);
        gratitude2.setVisibility(View.GONE);
        gratitude3.setVisibility(View.GONE);
        gratitudeSubmitBtn.setVisibility(View.GONE);
        freeformInput.setVisibility(View.GONE);
        wordCounter.setVisibility(View.GONE);
        exerciseTimerView.setVisibility(View.GONE);
        exerciseDoneBtn.setVisibility(View.GONE);
        offerSubmitBtn.setVisibility(View.GONE);

        // Hide photo task elements by default
        btnTakePhoto.setVisibility(View.GONE);
        photoStatus.setVisibility(View.GONE);

        if ("photo_task".equals(mode)) {
            showPhotoTask();
        } else if ("negotiation".equals(mode)) {
            showNegotiation();
        } else if ("gratitude".equals(mode)) {
            showGratitude();
        } else if ("love_letter".equals(mode)) {
            showLoveLetter();
        } else if ("exercise".equals(mode)) {
            showExercise();
        }
    }

    private void showGratitude() {
        messageView.setTextSize(20);
        taskPromptView.setVisibility(View.VISIBLE);
        taskPromptView.setText("Write 3 things you're grateful for (5+ words each):");
        gratitude1.setVisibility(View.VISIBLE);
        gratitude2.setVisibility(View.VISIBLE);
        gratitude3.setVisibility(View.VISIBLE);
        gratitudeSubmitBtn.setVisibility(View.VISIBLE);
    }

    private void checkGratitude() {
        String[] entries = {
            gratitude1.getText().toString().trim(),
            gratitude2.getText().toString().trim(),
            gratitude3.getText().toString().trim()
        };
        for (int i = 0; i < 3; i++) {
            int words = entries[i].isEmpty() ? 0 : entries[i].split("\\s+").length;
            if (words < 5) {
                taskPromptView.setText("Entry " + (i + 1) + " needs at least 5 words. You wrote " + words + ".");
                return;
            }
        }
        sendWebhook("/webhook/gratitude",
            "{\"entries\":[\"" + escJson(entries[0]) + "\",\"" + escJson(entries[1]) + "\",\"" + escJson(entries[2]) + "\"]}");
        unlockAll();
    }

    private void showLoveLetter() {
        messageView.setTextSize(20);
        int needed = Settings.Global.getInt(getContentResolver(), "focus_lock_word_min", 50);
        taskPromptView.setVisibility(View.VISIBLE);
        taskPromptView.setText("Write something nice for your partner (" + needed + " words minimum):");
        freeformInput.setVisibility(View.VISIBLE);
        wordCounter.setVisibility(View.VISIBLE);
        wordCounter.setText("0 / " + needed + " words");
    }

    private void showExercise() {
        messageView.setTextSize(20);
        String exercise = gstr("focus_lock_exercise");
        if (exercise.isEmpty()) exercise = "Do 20 pushups";
        taskPromptView.setVisibility(View.VISIBLE);
        taskPromptView.setText(exercise);
        exerciseTimerView.setVisibility(View.VISIBLE);
        exerciseDoneBtn.setVisibility(View.VISIBLE);

        long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
        if (unlockAt > 0) {
            long remaining = unlockAt - System.currentTimeMillis();
            if (remaining > 0) {
                long s = remaining / 1000;
                exerciseTimerView.setText(s + "s");
                exerciseDoneBtn.setEnabled(false);
                exerciseDoneBtn.setText("Wait " + s + "s");
            } else {
                exerciseTimerView.setText("Go!");
                exerciseDoneBtn.setEnabled(true);
                exerciseDoneBtn.setText("I'm done!");
            }
        } else {
            exerciseTimerView.setText("");
            exerciseDoneBtn.setEnabled(true);
            exerciseDoneBtn.setText("I'm done!");
        }
    }

    private void showPhotoTask() {
        messageView.setTextSize(20);
        String task = gstr("focus_lock_photo_task");
        String hint = gstr("focus_lock_photo_hint");
        taskPromptView.setVisibility(View.VISIBLE);
        taskPromptView.setText(task);
        if (!hint.isEmpty()) {
            taskTargetView.setVisibility(View.VISIBLE);
            taskTargetView.setText("Hint: " + hint);
        }
        btnTakePhoto.setVisibility(View.VISIBLE);
        btnTakePhoto.setText("Take Photo as Proof");
        btnTakePhoto.setEnabled(true);
        // Show any previous status
        String status = gstr("focus_lock_photo_status");
        if (!status.isEmpty()) {
            photoStatus.setVisibility(View.VISIBLE);
            photoStatus.setText(status);
        }
    }

    private void takePhotoForTask() {
        try {
            allowPause = true;
            Intent cameraIntent = new Intent(android.provider.MediaStore.ACTION_IMAGE_CAPTURE);
            cameraIntent.putExtra("android.intent.extras.CAMERA_FACING", 0);
            startActivityForResult(cameraIntent, PHOTO_TASK_REQUEST);
        } catch (Exception e) {
            allowPause = false;
            photoStatus.setVisibility(View.VISIBLE);
            photoStatus.setText("Camera failed: " + e.getMessage());
            photoStatus.setTextColor(0xFFcc4444);
        }
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        allowPause = false;

        if (requestCode == PHOTO_TASK_REQUEST) {
            if (resultCode != RESULT_OK) {
                photoStatus.setVisibility(View.VISIBLE);
                photoStatus.setText("Photo cancelled. Try again.");
                photoStatus.setTextColor(0xFFcc4444);
                return;
            }

            btnTakePhoto.setEnabled(false);
            btnTakePhoto.setText("Sending...");
            photoStatus.setVisibility(View.VISIBLE);
            photoStatus.setText("Photo taken. Sending for verification...");
            photoStatus.setTextColor(0xFF8888cc);

            new Thread(() -> {
                // Get bitmap from intent and encode as base64 JPEG
                String photoBase64 = "";
                try {
                    android.graphics.Bitmap bmp = (android.graphics.Bitmap) data.getExtras().get("data");
                    if (bmp != null) {
                        // Scale up for better LLM analysis (thumbnail is small)
                        android.graphics.Bitmap scaled = android.graphics.Bitmap.createScaledBitmap(
                            bmp, Math.max(bmp.getWidth(), 640), Math.max(bmp.getHeight(), 480), true);
                        java.io.ByteArrayOutputStream baos = new java.io.ByteArrayOutputStream();
                        scaled.compress(android.graphics.Bitmap.CompressFormat.JPEG, 85, baos);
                        photoBase64 = android.util.Base64.encodeToString(baos.toByteArray(), android.util.Base64.NO_WRAP);
                        if (scaled != bmp) scaled.recycle();
                        bmp.recycle();
                    }
                } catch (Exception e) {
                    final String err = e.getMessage();
                    handler.post(() -> {
                        photoStatus.setText("Failed to process photo: " + err);
                        photoStatus.setTextColor(0xFFcc4444);
                        btnTakePhoto.setEnabled(true);
                        btnTakePhoto.setText("Take Photo as Proof");
                    });
                    return;
                }

                if (photoBase64.isEmpty()) {
                    handler.post(() -> {
                        photoStatus.setText("Empty photo. Try again.");
                        photoStatus.setTextColor(0xFFcc4444);
                        btnTakePhoto.setEnabled(true);
                        btnTakePhoto.setText("Take Photo as Proof");
                    });
                    return;
                }

            final String fPhotoBase64 = photoBase64;
            // Send to mesh server for LLM verification
            String task = gstr("focus_lock_photo_task");
            String verifyHost = webhookHost();
            if (verifyHost.isEmpty()) {
                handler.post(() -> android.widget.Toast.makeText(this, "Photo verification not configured", android.widget.Toast.LENGTH_LONG).show());
                return;
            }
            try {
                String json = "{\"photo\":\"" + fPhotoBase64 + "\",\"task\":\"" + escJson(task) + "\"}";
                java.net.URL url = new java.net.URL("http://" + verifyHost + "/webhook/verify-photo");
                java.net.HttpURLConnection conn = (java.net.HttpURLConnection) url.openConnection();
                conn.setRequestMethod("POST");
                conn.setRequestProperty("Content-Type", "application/json");
                conn.setDoOutput(true);
                conn.setConnectTimeout(10000);
                conn.setReadTimeout(60000); // LLM can take a while
                conn.getOutputStream().write(json.getBytes());
                java.io.BufferedReader reader = new java.io.BufferedReader(
                    new java.io.InputStreamReader(conn.getInputStream()));
                StringBuilder sb = new StringBuilder();
                String line;
                while ((line = reader.readLine()) != null) sb.append(line);
                reader.close();
                conn.disconnect();

                String response = sb.toString();
                boolean passed = response.contains("\"passed\":true");
                // Extract reason
                String reason = "";
                int ri = response.indexOf("\"reason\":\"");
                if (ri >= 0) {
                    ri += 10;
                    int re = response.indexOf("\"", ri);
                    if (re > ri) reason = response.substring(ri, re);
                }

                final boolean fPassed = passed;
                final String fReason = reason;
                handler.post(() -> {
                    if (fPassed) {
                        photoStatus.setText("VERIFIED: " + fReason);
                        photoStatus.setTextColor(0xFF44cc44);
                        Settings.Global.putString(getContentResolver(), "focus_lock_photo_status", "");
                        Settings.Global.putString(getContentResolver(), "focus_lock_photo_task", "");
                        // Send evidence photo
                        sendWebhook("/webhook/evidence-photo",
                            "{\"photo\":\"" + fPhotoBase64 + "\",\"type\":\"photo_task\",\"text\":\"" + escJson(task) + "\"}");
                        // Delay unlock slightly so they see the success
                        handler.postDelayed(() -> unlockAll(), 2000);
                    } else {
                        photoStatus.setText("NOT VERIFIED: " + fReason + "\n\nTry again.");
                        photoStatus.setTextColor(0xFFcc4444);
                        Settings.Global.putString(getContentResolver(), "focus_lock_photo_status",
                            "Last attempt rejected: " + fReason);
                        btnTakePhoto.setEnabled(true);
                        btnTakePhoto.setText("Take Another Photo");
                    }
                });
            } catch (Exception e) {
                handler.post(() -> {
                    photoStatus.setText("Verification failed: " + e.getMessage() + "\nTry again.");
                    photoStatus.setTextColor(0xFFcc4444);
                    btnTakePhoto.setEnabled(true);
                    btnTakePhoto.setText("Take Photo as Proof");
                });
            }
        }).start();
        }
    }

    private void showNegotiation() {
        String offerStatus = gstr("focus_lock_offer_status");
        String offerResponse = gstr("focus_lock_offer_response");
        taskPromptView.setVisibility(View.VISIBLE);
        taskInputView.setVisibility(View.VISIBLE);
        offerSubmitBtn.setVisibility(View.VISIBLE);
        complimentInputView.setVisibility(View.GONE);
        taskTargetView.setVisibility(View.GONE);
        messageView.setTextSize(20);

        if ("pending".equals(offerStatus)) {
            taskPromptView.setText("Offer submitted. Waiting for response...");
            taskInputView.setEnabled(false);
            offerSubmitBtn.setVisibility(View.GONE);
        } else if ("declined".equals(offerStatus)) {
            String counter = offerResponse.isEmpty() ? "Try again." : "\"" + offerResponse + "\"";
            taskPromptView.setText("Offer declined. " + counter);
            taskInputView.setEnabled(true);
            taskInputView.setText("");
            Settings.Global.putString(getContentResolver(), "focus_lock_offer_status", "");
        } else if ("accepted".equals(offerStatus)) {
            // Unlock will happen via the flag change
            return;
        } else {
            taskPromptView.setText("What are you prepared to offer to unlock this phone?");
            taskInputView.setEnabled(true);
            taskInputView.setHint("Type your offer...");
            taskInputView.setInputType(android.text.InputType.TYPE_CLASS_TEXT
                | android.text.InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        }
    }

    private void checkTimer() {
        long unlockAt = Settings.Global.getLong(getContentResolver(), "focus_lock_unlock_at", 0);
        if (unlockAt > 0 && System.currentTimeMillis() >= unlockAt) {
            // Only auto-unlock if no task/compliment is pending
            String task = gstr("focus_lock_task_text");
            String comp = gstr("focus_lock_compliment");
            if (task.isEmpty() && comp.isEmpty()) {
                unlockAll();
            }
        }
    }

    private String webhookHost() {
        // No fallback — caller must check for empty and skip the webhook if unconfigured.
        return gstr("focus_lock_webhook_host");
    }

    private String gstr(String key) {
        String v = Settings.Global.getString(getContentResolver(), key);
        return (v == null || v.equals("null") || v.equals("\"\"")) ? "" : v;
    }

    private void applyImmersive() {
        // Full immersive sticky — hide both status bar and navigation bar
        // Nav bar reappears briefly on swipe but auto-hides; touches still work
        getWindow().getDecorView().setSystemUiVisibility(
            View.SYSTEM_UI_FLAG_FULLSCREEN
            | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
            | View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
            | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
            | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
            | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
        );
        // Re-apply if system UI changes (e.g. user swipes nav bar up)
        getWindow().getDecorView().setOnSystemUiVisibilityChangeListener(vis -> {
            if ((vis & View.SYSTEM_UI_FLAG_FULLSCREEN) == 0) {
                handler.postDelayed(this::applyImmersive, 1000);
            }
        });
    }

    private boolean isLockActive() {
        return Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0) == 1;
    }

    @Override
    protected void onResume() {
        super.onResume();
        activityVisible = true;
        if (!isLockActive()) { finish(); return; }

        // First-time consent check
        int consented = Settings.Global.getInt(getContentResolver(), "focus_lock_consented", 0);
        if (consented != 1) {
            showConsentDialog();
            return;
        }

        applyImmersive();
        startLockTaskIfOwner();
        updateDisplay();
        handler.post(timerChecker);
    }

    /** Pin screen via lock task mode if app is device owner — completely blocks nav buttons. */
    private void startLockTaskIfOwner() {
        try {
            android.app.admin.DevicePolicyManager dpm =
                (android.app.admin.DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
            android.content.ComponentName admin =
                new android.content.ComponentName(this, AdminReceiver.class);
            if (dpm.isDeviceOwnerApp(getPackageName())) {
                // Allow both Collar and BunnyTasker in lock task mode
                dpm.setLockTaskPackages(admin, new String[]{
                    getPackageName(), "com.bunnytasker"});
                if (!isInLockTaskMode()) {
                    startLockTask();
                }
            }
        } catch (Exception e) {
            // Not device owner — immersive mode is the fallback
        }
    }

    private boolean isInLockTaskMode() {
        android.app.ActivityManager am =
            (android.app.ActivityManager) getSystemService(ACTIVITY_SERVICE);
        if (Build.VERSION.SDK_INT >= 23) {
            return am.getLockTaskModeState() != android.app.ActivityManager.LOCK_TASK_MODE_NONE;
        }
        return false;
    }

    private void showConsentDialog() {
        // Pause immersive so dialog is visible
        getWindow().getDecorView().setSystemUiVisibility(0);

        new android.app.AlertDialog.Builder(this)
            .setTitle("Terms of Surrender")
            .setCancelable(false)
            .setMessage(
                "By tapping \"I CONSENT\" below, you acknowledge and agree to the following:\n\n" +
                "1. This phone will be remotely controllable by your designated partner (\"the Lion\").\n\n" +
                "2. The Lion may, at any time and without notice: lock your phone, set financial paywalls " +
                "(payable in real Canadian dollars), assign writing tasks, enable taunts, enforce geofences, " +
                "take selfies, play audio at maximum volume, and vibrate connected devices.\n\n" +
                "3. Escape attempts will be met with escalating penalties, progressive buzzing, " +
                "public shame notifications, and compound interest on any outstanding balance.\n\n" +
                "4. The paywall is denominated in real money. Interest accrues. Penalties stack. " +
                "This is not a drill.\n\n" +
                "5. You may revoke consent at any time by performing a factory reset (available after 150 escape attempts) " +
                "or by contacting your partner directly. The system is consensual. The power dynamic is not.\n\n" +
                "6. You asked for this. Probably more than once.\n\n" +
                "This consent is recorded with a timestamp and cannot be un-given through the app.")
            .setPositiveButton("I CONSENT", (d, w) -> {
                Settings.Global.putInt(getContentResolver(), "focus_lock_consented", 1);
                Settings.Global.putLong(getContentResolver(), "focus_lock_consent_time",
                    System.currentTimeMillis());
                // Now engage the jail
                applyImmersive();
                updateDisplay();
                handler.post(timerChecker);
            })
            .setNegativeButton("I DO NOT CONSENT", (d, w) -> {
                // Respect the refusal — unlock and exit
                Settings.Global.putInt(getContentResolver(), "focus_lock_active", 0);
                finish();
            })
            .show();
    }

    @Override
    protected void onPause() {
        super.onPause();
        handler.removeCallbacks(timerChecker);
    }

    @Override
    protected void onUserLeaveHint() {
        // Don't count escapes here — fires on screen off too
    }

    @Override public void onBackPressed() {
        // Back button — real escape attempt (rarely fires on modern Android)
        recordEscape();
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK || keyCode == KeyEvent.KEYCODE_CAMERA) {
            recordEscape();
            return true;
        }
        if (keyCode == KeyEvent.KEYCODE_HOME || keyCode == KeyEvent.KEYCODE_APP_SWITCH) {
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    protected void onStop() {
        super.onStop();
        activityVisible = false;
        if (isLockActive() && !allowPause) {
            // Just relaunch — no app launch penalty (too many false positives from screen off)
            handler.postDelayed(() -> {
                if (isLockActive() && !allowPause) {
                    startActivity(new Intent(this, FocusActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
                }
            }, 500);
        }
        if (allowPause) {
            // 15 seconds for banking app, then jail comes back
            handler.postDelayed(() -> {
                allowPause = false;
                if (isLockActive()) {
                    startActivity(new Intent(this, FocusActivity.class)
                        .addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT));
                }
            }, 15000);
        }
    }

    @Override
    public void onWindowFocusChanged(boolean hasFocus) {
        super.onWindowFocusChanged(hasFocus);
        if (hasFocus && isLockActive()) {
            applyImmersive();
        }
        // No escape counting here — fires on screen off which is not an escape
    }
}

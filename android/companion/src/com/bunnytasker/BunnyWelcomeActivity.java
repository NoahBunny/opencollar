package com.bunnytasker;

import android.app.Activity;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Bundle;
import android.text.InputType;
import android.view.Gravity;
import android.view.View;
import android.view.ViewGroup;
import android.widget.Button;
import android.widget.EditText;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import android.widget.ViewFlipper;

/**
 * Bunny Tasker's own first-run welcome — a soft, lavender, full-screen stepper
 * built programmatically (same robust pattern as the Collar's ConsentActivity:
 * no layout XML / R.id, so it builds with the existing javac *.java + aapt2
 * pipeline untouched).
 *
 * It is INDEPENDENT of the Collar's "Terms of Surrender": it greets the Bunny,
 * explains what this companion app does, points forward to (without restating)
 * the Terms of Surrender, and leads into the existing pairing QR. MainActivity
 * runs maybeLaunchCollarConsent() AFTER this returns, so the order is
 * welcome → Terms of Surrender → device-admin. Gated by the SharedPreferences
 * flag "bunny_onboarded".
 */
public class BunnyWelcomeActivity extends Activity {

    private ViewFlipper flipper;
    private Button backBtn, nextBtn;
    private float density;
    private SharedPreferences prefs;
    // Bunny's payer identity (their payment email/name). Collected here in the
    // BUNNY's app only; stashed in prefs and sent to the server-only payer
    // allowlist once paired (deferred — not paired yet at welcome time). Never
    // reaches the Lion's app.
    private EditText payerInput;
    private TextView adminStatus;
    private static final int PAGE_ADMIN = 4;
    private static final int PAGE_PAIR = 5;

    // Soft lavender brand (matches BunnyTheme).
    private static final int BG      = 0xFF0a0812;
    private static final int PRIMARY = 0xFF7744aa;
    private static final int ACCENT  = 0xFFaa66dd;
    private static final int TEXT    = 0xFFe0d8e8;
    private static final int MUTED   = 0xFF6a5a7a;
    private static final int CARD    = 0xFF0e0c16;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        density = getResources().getDisplayMetrics().density;
        prefs = getSharedPreferences("bunnytasker", MODE_PRIVATE);
        try {
            getWindow().setStatusBarColor(BG);
            getWindow().setNavigationBarColor(BG);
        } catch (Exception e) {}

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(BG);
        scroll.setFillViewport(true);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(px(28), px(44), px(28), px(28));
        scroll.addView(root);

        flipper = new ViewFlipper(this);
        flipper.addView(panelGreeting());   // 0
        flipper.addView(panelCompanion());  // 1
        flipper.addView(panelLion());       // 2
        flipper.addView(panelPayer());      // 3
        flipper.addView(panelAdmin());      // 4
        flipper.addView(panelPair());       // 5
        LinearLayout.LayoutParams flp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        flp.weight = 1f;
        root.addView(flipper, flp);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, px(24), 0, 0);
        backBtn = secondaryButton("Skip");
        backBtn.setOnClickListener(v -> goBack());
        nextBtn = primaryButton("Hop in");
        nextBtn.setOnClickListener(v -> goNext());
        LinearLayout.LayoutParams lpBack = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1f);
        LinearLayout.LayoutParams lpNext = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 2f);
        lpBack.rightMargin = px(10);
        row.addView(backBtn, lpBack);
        row.addView(nextBtn, lpNext);
        root.addView(row);

        setContentView(scroll);
        updateButtons();
    }

    // ── Navigation ──

    private void goNext() {
        int i = flipper.getDisplayedChild();
        // The admin step is the one page that will not let itself be walked
        // past. Everything before it is explanation; this is the grant the
        // Collar's mutual-admin monitor already assumes exists.
        if (i == PAGE_ADMIN && !selfAdminActive()) {
            requestSelfAdmin();
            return;
        }
        if (i >= PAGE_PAIR) { finishComplete(); return; }
        flipper.setDisplayedChild(i + 1);
        updateButtons();
    }

    private void goBack() {
        int i = flipper.getDisplayedChild();
        if (i == 0) { skip(); return; }
        flipper.setDisplayedChild(i - 1);
        updateButtons();
    }

    private void updateButtons() {
        int i = flipper.getDisplayedChild();
        backBtn.setText(i == 0 ? "Skip" : "Back");
        String[] cta = {"Hop in", "Next", "I understand", "Next", "Give Bunny Tasker admin", "Show my pairing code"};
        nextBtn.setText(i == PAGE_ADMIN && selfAdminActive() ? "Next" : cta[i]);
        if (i == PAGE_ADMIN) refreshAdminStatus();
    }

    @Override
    protected void onResume() {
        super.onResume();
        // Coming back from the system admin screen — reflect what was granted.
        if (flipper != null && flipper.getDisplayedChild() == PAGE_ADMIN) updateButtons();
    }

    // ── Device admin ──

    private android.content.ComponentName selfAdmin() {
        return new android.content.ComponentName(this, "com.bunnytasker.AdminReceiver");
    }

    private boolean selfAdminActive() {
        try {
            android.app.admin.DevicePolicyManager dpm =
                (android.app.admin.DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
            return dpm != null && dpm.isAdminActive(selfAdmin());
        } catch (Exception e) {
            return false;
        }
    }

    /** Open the system screen, with the explanation Android shows above the
     *  Activate button — so the bunny reads why before they are asked. */
    private void requestSelfAdmin() {
        try {
            Intent activate = new Intent(android.app.admin.DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN);
            activate.putExtra(android.app.admin.DevicePolicyManager.EXTRA_DEVICE_ADMIN, selfAdmin());
            activate.putExtra(android.app.admin.DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                "Bunny Tasker needs this so your Lion's locks hold. Without it The Collar "
                + "treats the companion as tampered with and keeps re-locking your phone.");
            startActivity(activate);
        } catch (Exception e) {
            if (adminStatus != null) {
                adminStatus.setText("Could not open the admin screen. Settings \u2192 Security \u2192 "
                    + "Device admin apps \u2192 Bunny Tasker.");
            }
        }
    }

    private void refreshAdminStatus() {
        if (adminStatus == null) return;
        boolean on = selfAdminActive();
        adminStatus.setText(on
            ? "\u2713  Bunny Tasker has device admin."
            : "\u25cb  Not granted yet \u2014 the button below opens the right screen.");
        adminStatus.setTextColor(on ? 0xFF66aa66 : MUTED);
    }

    /** Completed the welcome — remember it so it doesn't re-show, stash any payer
     *  identity for deferred send (we aren't paired yet), then return to
     *  MainActivity (which hands off to the Collar's Terms of Surrender). */
    private void finishComplete() {
        SharedPreferences.Editor ed = prefs.edit().putBoolean("bunny_onboarded", true);
        if (payerInput != null) {
            String payer = payerInput.getText().toString().trim();
            if (!payer.isEmpty()) {
                // Same pref key the post-pairing doSetupPayerIdentity uses; the
                // companion sends it to the server-only payer allowlist once
                // paired (maybeSendPendingPayerIdentity).
                ed.putString("payer_identity_text", payer);
                ed.putBoolean("payer_identity_pending", true);
            }
        }
        ed.apply();
        setResult(RESULT_OK, new Intent());
        finish();
    }

    /** Skip / back-out from the first panel: do NOT persist the flag, so the
     *  welcome re-shows next launch — but still return so MainActivity proceeds
     *  to the Collar consent and the (already-rendered) pairing QR. */
    private void skip() {
        setResult(RESULT_CANCELED, new Intent());
        finish();
    }

    @Override
    public void onBackPressed() {
        goBack();
    }

    // ── Panels ──

    private View panelGreeting() {
        LinearLayout p = panel();
        ImageView hero = new ImageView(this);
        hero.setImageResource(getResources().getIdentifier("bunny_lavender", "drawable", getPackageName()));
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(px(110), px(110));
        hp.gravity = Gravity.CENTER_HORIZONTAL;
        hp.bottomMargin = px(20);
        p.addView(hero, hp);
        p.addView(title("Hi, bunny 💜"));
        p.addView(body("This is Bunny Tasker — your soft little companion app. "
            + "It keeps you close to your Lion and shows you everything that matters."));
        return p;
    }

    private View panelCompanion() {
        LinearLayout p = panel();
        p.addView(title("Your companion"));
        p.addView(bullet("See your stats — streaks, time, and tribute."));
        p.addView(bullet("Send and receive messages with your Lion."));
        p.addView(bullet("Lock yourself when you want to be good."));
        p.addView(bullet("Manage your subscription and tribute."));
        return p;
    }

    private View panelLion() {
        LinearLayout p = panel();
        ImageView crown = new ImageView(this);
        crown.setImageResource(getResources().getIdentifier("crown_gold", "drawable", getPackageName()));
        LinearLayout.LayoutParams cp = new LinearLayout.LayoutParams(px(40), px(40));
        cp.gravity = Gravity.CENTER_HORIZONTAL;
        cp.bottomMargin = px(14);
        p.addView(crown, cp);
        p.addView(title("You and your Lion"));
        p.addView(body("Bunny Tasker is gentle. The serious part — handing real control "
            + "to your Lion — lives in a separate step called the Terms of Surrender. "
            + "You'll see it in a moment, and you choose there. This app just helps you "
            + "stay connected."));
        return p;
    }

    private View panelPayer() {
        LinearLayout p = panel();
        p.addView(title("Getting credited"));
        p.addView(body("Optional: tell us the email or name you'll pay your Lion from, so "
            + "your tributes get recognized and clear your balance. This stays private to "
            + "you — your Lion never sees it. You can also set it later."));
        payerInput = new EditText(this);
        payerInput.setHint("Your payment email or name");
        payerInput.setTextColor(TEXT);
        payerInput.setHintTextColor(MUTED);
        payerInput.setTextSize(15);
        payerInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        GradientDrawable ibg = new GradientDrawable();
        ibg.setColor(CARD);
        ibg.setCornerRadius(px(8));
        payerInput.setBackground(ibg);
        payerInput.setPadding(px(14), px(12), px(14), px(12));
        p.addView(payerInput);
        return p;
    }

    private View panelAdmin() {
        LinearLayout p = panel();
        p.addView(title("The leash needs a grip"));
        p.addView(body("Both apps hold device-administrator privileges. It is what stops "
            + "the cage being shrugged off by uninstalling an app mid-lock, and The Collar "
            + "checks that this one has it."));
        p.addView(bullet("Without it, The Collar treats Bunny Tasker as tampered with and re-locks."));
        p.addView(bullet("It does not let anyone read your messages or your screen."));
        p.addView(bullet("You can revoke it any time \u2014 your Lion is told, and that is the point."));
        p.addView(body("The button below opens the exact system screen. Tap Activate there, "
            + "then come back."));
        adminStatus = body("");
        p.addView(adminStatus);
        return p;
    }

    private View panelPair() {
        LinearLayout p = panel();
        p.addView(title("Let's pair"));
        p.addView(body("Show your Lion the code on the next screen. They'll scan it to "
            + "take your leash. Check that the little fingerprint matches — that's how "
            + "you both stay safe."));
        return p;
    }

    // ── View helpers ──

    private LinearLayout panel() {
        LinearLayout p = new LinearLayout(this);
        p.setOrientation(LinearLayout.VERTICAL);
        p.setPadding(px(4), px(8), px(4), px(8));
        return p;
    }

    private TextView title(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextColor(ACCENT);
        t.setTextSize(24);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setGravity(Gravity.CENTER_HORIZONTAL);
        t.setPadding(0, 0, 0, px(16));
        return t;
    }

    private TextView body(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextColor(TEXT);
        t.setTextSize(15);
        t.setLineSpacing(px(5), 1f);
        t.setPadding(0, 0, 0, px(12));
        return t;
    }

    private View bullet(String s) {
        TextView t = new TextView(this);
        t.setText("·  " + s);
        t.setTextColor(TEXT);
        t.setTextSize(15);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(CARD);
        bg.setCornerRadius(px(8));
        t.setBackground(bg);
        t.setPadding(px(14), px(12), px(14), px(12));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = px(8);
        t.setLayoutParams(lp);
        return t;
    }

    private Button primaryButton(String s) {
        Button btn = new Button(this);
        btn.setText(s);
        btn.setAllCaps(false);
        btn.setTextColor(0xFFffffff);
        btn.setTextSize(15);
        btn.setTypeface(Typeface.DEFAULT_BOLD);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(PRIMARY);
        bg.setCornerRadius(px(12));
        btn.setBackground(bg);
        return btn;
    }

    private Button secondaryButton(String s) {
        Button btn = new Button(this);
        btn.setText(s);
        btn.setAllCaps(false);
        btn.setTextColor(ACCENT);
        btn.setTextSize(15);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(CARD);
        bg.setCornerRadius(px(12));
        btn.setBackground(bg);
        return btn;
    }

    private int px(int dp) {
        return (int) (dp * density + 0.5f);
    }
}

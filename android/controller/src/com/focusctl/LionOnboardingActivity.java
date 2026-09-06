package com.focusctl;

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
 * First-run onboarding wizard for the Lion. A full-screen, gold-on-near-black
 * regal stepper built programmatically (mirrors ConsentActivity's style — no
 * layout XML, no R.id, so it survives the aapt2-regenerated R.java and the
 * javac *.java wildcard with zero build.sh changes).
 *
 * It does NOT reinvent pairing: the final step returns a chosen connection
 * method to MainActivity, which routes it into the EXISTING doPairDirect()/
 * doSetup() flows. Gated by the SharedPreferences flag "lion_onboarded".
 */
public class LionOnboardingActivity extends Activity {

    private ViewFlipper flipper;
    private Button backBtn, nextBtn;
    private float density;
    private SharedPreferences prefs;
    private String chosenMethod = "";

    // Panel indices. The 3 email panels (3,4,5) only show on the relay/Advanced
    // path — direct (scan/Tor) pairing jumps straight to VERIFY (no server to
    // use them). See chooseMethod()/goNext().
    private static final int CLAIM_IDX   = 2;
    private static final int ACCOUNT_IDX = 3;
    private static final int VERIFY_IDX  = 6;

    // Email inputs (Advanced/relay path only). Collected here in the LION's app
    // and returned to MainActivity, which routes them through the SAFE signed
    // endpoints — Lion's emails never transit a Bunny-readable channel.
    private EditText serverUrlInput, accountEmailInput, accountPassInput;
    private EditText imapHostInput, imapUserInput, imapPassInput, evidenceEmailInput;

    // Regal brand palette (matches AppTheme: gold on dark blue-black).
    private static final int BG       = 0xFF08080f;
    private static final int GOLD     = 0xFFc8a84e;
    private static final int GOLD_BTN = 0xFFDAA520;
    private static final int BODY     = 0xFFd0d0c0;
    private static final int SUBTLE   = 0xFF8a8a78;
    private static final int CARD     = 0xFF15150c;
    private static final int CARD_SEL = 0xFF2a2510;
    private static final int BTN_DK   = 0xFF1a1a2e;

    @Override
    protected void onCreate(Bundle b) {
        super.onCreate(b);
        density = getResources().getDisplayMetrics().density;
        prefs = getSharedPreferences("focusctl", MODE_PRIVATE);
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
        flipper.addView(panelWelcome());      // 0
        flipper.addView(panelDynamic());      // 1
        flipper.addView(panelClaim());        // 2  (cards drive nav)
        flipper.addView(panelAccount());      // 3  (relay/Advanced path only)
        flipper.addView(panelPaymentEmail()); // 4  (relay/Advanced path only)
        flipper.addView(panelEvidence());     // 5  (relay/Advanced path only)
        flipper.addView(panelVerify());       // 6
        LinearLayout.LayoutParams flp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        flp.weight = 1f;
        root.addView(flipper, flp);

        LinearLayout row = new LinearLayout(this);
        row.setOrientation(LinearLayout.HORIZONTAL);
        row.setPadding(0, px(24), 0, 0);
        backBtn = secondaryButton("Back");
        backBtn.setOnClickListener(v -> goBack());
        nextBtn = primaryButton("Begin");
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
        if (i >= VERIFY_IDX) { finishWithMethod(); return; }
        flipper.setDisplayedChild(i + 1);
        updateButtons();
    }

    private void goBack() {
        int i = flipper.getDisplayedChild();
        if (i == 0) { skip(); return; }
        // From the email panels or VERIFY, a direct-path user came straight from
        // CLAIM — send them back there rather than walking the hidden panels.
        if (!"advanced".equals(chosenMethod) && i == VERIFY_IDX) {
            flipper.setDisplayedChild(CLAIM_IDX);
        } else {
            flipper.setDisplayedChild(i - 1);
        }
        updateButtons();
    }

    /** Update the bottom button row for the current panel. On the "Claim"
     *  panel the option cards drive navigation, so Next is hidden there. */
    private void updateButtons() {
        int i = flipper.getDisplayedChild();
        backBtn.setText(i == 0 ? "Skip" : "Back");
        if (i == CLAIM_IDX) {
            nextBtn.setVisibility(View.GONE);
        } else {
            nextBtn.setVisibility(View.VISIBLE);
            nextBtn.setText(i == 0 ? "Begin" : (i == VERIFY_IDX ? "Take control" : "Next"));
        }
    }

    private void chooseMethod(String method) {
        chosenMethod = method;
        // Email setup (account/payment/evidence) only makes sense on the
        // server-backed relay path; direct (scan/Tor) skips straight to VERIFY.
        flipper.setDisplayedChild("advanced".equals(method) ? ACCOUNT_IDX : VERIFY_IDX);
        updateButtons();
    }

    /** One-time: mark onboarded and return the chosen method + (Advanced path)
     *  the collected Lion emails to MainActivity, which applies them through the
     *  SAFE signed endpoints. */
    private void finishWithMethod() {
        prefs.edit().putBoolean("lion_onboarded", true).apply();
        Intent data = new Intent();
        if (!chosenMethod.isEmpty()) data.putExtra("method", chosenMethod);
        if ("advanced".equals(chosenMethod)) {
            data.putExtra("server_url", txt(serverUrlInput));
            data.putExtra("account_email", txt(accountEmailInput));
            data.putExtra("account_pass", txt(accountPassInput));
            data.putExtra("imap_host", txt(imapHostInput));
            data.putExtra("imap_user", txt(imapUserInput));
            data.putExtra("imap_pass", txt(imapPassInput));
            data.putExtra("evidence_email", txt(evidenceEmailInput));
        }
        setResult(RESULT_OK, data);
        finish();
    }

    private String txt(EditText e) {
        return e == null ? "" : e.getText().toString().trim();
    }

    /** Skip: still mark onboarded (one-time) but return no method — the host
     *  falls back to doSetup() if the app is still unconfigured. */
    private void skip() {
        prefs.edit().putBoolean("lion_onboarded", true).apply();
        setResult(RESULT_CANCELED, new Intent());
        finish();
    }

    @Override
    public void onBackPressed() {
        goBack();  // never strand the user; mirrors the on-screen Back/Skip
    }

    // ── Panels ──

    private View panelWelcome() {
        LinearLayout p = panel();
        ImageView hero = new ImageView(this);
        hero.setImageResource(getResources().getIdentifier("ic_launcher", "mipmap", getPackageName()));
        LinearLayout.LayoutParams hp = new LinearLayout.LayoutParams(px(96), px(96));
        hp.gravity = Gravity.CENTER_HORIZONTAL;
        hp.bottomMargin = px(20);
        p.addView(hero, hp);
        p.addView(title("LION'S SHARE"));
        p.addView(subtitle("Your throne is ready."));
        p.addView(body("This is the seat of control. From here you hold the leash — "
            + "locks, paywalls, tasks, and tribute. Let's bind your first Bunny."));
        return p;
    }

    private View panelDynamic() {
        LinearLayout p = panel();
        p.addView(title("THE DYNAMIC"));
        p.addView(body("You command. The Collar on their phone obeys — locking on your word."));
        p.addView(body("Bunny Tasker is their window: stats, messages, tribute."));
        p.addView(body("Nothing happens until you pair. One Lion, one key."));
        return p;
    }

    private View panelClaim() {
        LinearLayout p = panel();
        p.addView(title("CLAIM YOUR BUNNY"));
        p.addView(subtitle("How will you take control?"));
        p.addView(optionCard("📷  Scan their QR",
            "Fastest — same Wi‑Fi. Point your camera at the code in their Bunny Tasker.",
            true, () -> chooseMethod("scan")));
        p.addView(optionCard("🌐  Connect anywhere — Tor",
            "Across networks, no account. Enter the Bunny's address by hand.",
            false, () -> chooseMethod("tailscale")));
        p.addView(optionCard("⚙  Advanced: relay + homelab",
            "Run your own mesh server. For power users.",
            false, () -> chooseMethod("advanced")));
        return p;
    }

    private View panelVerify() {
        LinearLayout p = panel();
        p.addView(title("VERIFY THE FINGERPRINT"));
        p.addView(body("Show your Bunny their pairing screen. Before you take control, "
            + "check the 16‑character fingerprint matches the one on their phone — "
            + "it's how you know no one is listening in."));
        p.addView(subtitle("Tap “Take control” when you're ready."));
        return p;
    }

    // ── Email panels (relay/Advanced path only) ──

    private View panelAccount() {
        LinearLayout p = panel();
        p.addView(title("YOUR MESH ACCOUNT"));
        p.addView(body("Run your own relay or homelab. Enter its address, plus an "
            + "account email + password for recovery. All optional."));
        serverUrlInput = inputField("Server URL (https://your-mesh.example.com)", false);
        serverUrlInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_URI);
        accountEmailInput = inputField("Account email", false);
        accountEmailInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        accountPassInput = inputField("Account password", true);
        p.addView(serverUrlInput);
        p.addView(accountEmailInput);
        p.addView(accountPassInput);
        return p;
    }

    private View panelPaymentEmail() {
        LinearLayout p = panel();
        p.addView(title("PAYMENT DETECTION"));
        p.addView(body("Optional: scan your inbox for incoming payments so they auto-credit "
            + "the Bunny. Your email + app-password stay on your server — never shared with the Bunny."));
        imapHostInput = inputField("IMAP host (e.g. imap.gmail.com)", false);
        imapUserInput = inputField("Your email address", false);
        imapUserInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        imapPassInput = inputField("App password", true);
        p.addView(imapHostInput);
        p.addView(imapUserInput);
        p.addView(imapPassInput);
        return p;
    }

    private View panelEvidence() {
        LinearLayout p = panel();
        p.addView(title("EVIDENCE & REPORTS"));
        p.addView(body("Optional: where compliments, gratitude, love letters, and photo proof "
            + "get emailed to you. Your address — kept on your server."));
        evidenceEmailInput = inputField("Evidence email", false);
        evidenceEmailInput.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_EMAIL_ADDRESS);
        p.addView(evidenceEmailInput);
        return p;
    }

    private EditText inputField(String hint, boolean password) {
        EditText e = new EditText(this);
        e.setHint(hint);
        e.setTextColor(BODY);
        e.setHintTextColor(SUBTLE);
        e.setTextSize(15);
        e.setInputType(password
            ? (InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD)
            : InputType.TYPE_CLASS_TEXT);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(CARD);
        bg.setCornerRadius(px(8));
        bg.setStroke(px(1), 0xFF3a3520);
        e.setBackground(bg);
        e.setPadding(px(14), px(12), px(14), px(12));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = px(10);
        e.setLayoutParams(lp);
        return e;
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
        t.setTextColor(GOLD);
        t.setTextSize(26);
        t.setTypeface(Typeface.DEFAULT_BOLD);
        t.setLetterSpacing(0.08f);
        t.setGravity(Gravity.CENTER_HORIZONTAL);
        t.setPadding(0, 0, 0, px(14));
        return t;
    }

    private TextView subtitle(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextColor(GOLD);
        t.setTextSize(14);
        t.setGravity(Gravity.CENTER_HORIZONTAL);
        t.setPadding(0, 0, 0, px(14));
        return t;
    }

    private TextView body(String s) {
        TextView t = new TextView(this);
        t.setText(s);
        t.setTextColor(BODY);
        t.setTextSize(15);
        t.setLineSpacing(px(4), 1f);
        t.setPadding(0, 0, 0, px(12));
        return t;
    }

    private View optionCard(String heading, String desc, boolean recommended, final Runnable onTap) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(px(16), px(14), px(16), px(14));
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(recommended ? CARD_SEL : CARD);
        bg.setCornerRadius(px(10));
        bg.setStroke(px(recommended ? 2 : 1), recommended ? GOLD_BTN : 0xFF3a3520);
        card.setBackground(bg);
        card.setClickable(true);
        card.setOnClickListener(v -> onTap.run());
        LinearLayout.LayoutParams clp = new LinearLayout.LayoutParams(
            ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        clp.bottomMargin = px(12);
        card.setLayoutParams(clp);

        TextView h = new TextView(this);
        h.setText(heading + (recommended ? "    ★" : ""));
        h.setTextColor(GOLD);
        h.setTextSize(16);
        h.setTypeface(Typeface.DEFAULT_BOLD);
        card.addView(h);

        TextView dsc = new TextView(this);
        dsc.setText(desc);
        dsc.setTextColor(SUBTLE);
        dsc.setTextSize(13);
        dsc.setPadding(0, px(4), 0, 0);
        card.addView(dsc);
        return card;
    }

    private Button primaryButton(String s) {
        Button btn = new Button(this);
        btn.setText(s);
        btn.setAllCaps(false);
        btn.setTextColor(0xFF111111);
        btn.setTextSize(15);
        btn.setTypeface(Typeface.DEFAULT_BOLD);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(GOLD_BTN);
        bg.setCornerRadius(px(8));
        btn.setBackground(bg);
        return btn;
    }

    private Button secondaryButton(String s) {
        Button btn = new Button(this);
        btn.setText(s);
        btn.setAllCaps(false);
        btn.setTextColor(GOLD);
        btn.setTextSize(15);
        GradientDrawable bg = new GradientDrawable();
        bg.setColor(BTN_DK);
        bg.setCornerRadius(px(8));
        btn.setBackground(bg);
        return btn;
    }

    private int px(int dp) {
        return (int) (dp * density + 0.5f);
    }
}

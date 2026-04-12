package com.focuslock;

import android.app.Activity;
import android.app.AlertDialog;
import android.app.role.RoleManager;
import android.content.Context;
import android.content.Intent;
import android.content.pm.ResolveInfo;
import android.os.Build;
import android.os.Bundle;
import android.provider.Settings;
import android.text.SpannableString;
import android.text.Spanned;
import android.text.style.ForegroundColorSpan;
import android.text.style.RelativeSizeSpan;
import android.text.style.StyleSpan;
import android.view.View;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.util.List;

/**
 * Shown once on first launch. Displays Terms of Surrender.
 * Once consented, this activity never shows again — FocusLock runs headless.
 */
public class ConsentActivity extends Activity {

    private static final int REQ_ROLE_HOME = 1001;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Already consented? Skip straight through.
        int consented = Settings.Global.getInt(getContentResolver(), "focus_lock_consented", 0);
        if (consented == 1) {
            finish();
            return;
        }

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(0xFF0a0a14);
        scroll.setFillViewport(true);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(52, 100, 52, 52);

        // Title
        TextView title = new TextView(this);
        title.setText("TERMS OF SURRENDER");
        title.setTextColor(0xFFcc2222);
        title.setTextSize(26);
        title.setLetterSpacing(0.08f);
        title.setTypeface(null, android.graphics.Typeface.BOLD);
        title.setGravity(android.view.Gravity.CENTER);
        title.setPadding(0, 0, 0, 8);
        root.addView(title);

        // Subtitle
        TextView subtitle = new TextView(this);
        subtitle.setText("The Collar — Consensual Phone Restriction");
        subtitle.setTextColor(0xFFc8a84e);
        subtitle.setTextSize(13);
        subtitle.setGravity(android.view.Gravity.CENTER);
        subtitle.setLetterSpacing(0.04f);
        subtitle.setPadding(0, 0, 0, 40);
        root.addView(subtitle);

        // Preamble
        addTerm(root,
            "By tapping \"I CONSENT\" you agree to surrender control of this phone " +
            "to your designated partner — the Lion. Read carefully.\n",
            null, 0xFFaaaaaa, 16);

        // Terms with highlights
        addTerm(root,
            "1.  This phone will be remotely controllable by the Lion. " +
            "They may lock your phone at any time, without notice, for any reason or no reason. " +
            "They set the conditions for unlock. They decide when you're done.",
            null, 0xFFcccccc, 16);

        addTerm(root,
            "2.  The Lion may impose financial paywalls payable in real currency via e-Transfer. " +
            "Compound interest accrues at up to 10% per hour. Penalties stack. ",
            "This is real money.", 0xFFcccccc, 16);

        addTerm(root,
            "3.  Escape attempts trigger escalating consequences: tiered paywall increases, " +
            "time penalties, progressive buzzing, vibration of connected intimate devices, " +
            "public shame notifications visible on your lock screen, " +
            "and evidence emails sent directly to the Lion.",
            null, 0xFFcccccc, 16);

        addTerm(root,
            "4.  The Lion may: play audio at maximum volume on your phone, enforce GPS geofences " +
            "that auto-lock your phone with a $100 paywall if breached, assign writing tasks, " +
            "take silent front camera photos as proof of obedience, and remotely control " +
            "connected Lovense devices at any intensity.",
            null, 0xFFcccccc, 16);

        addTerm(root,
            "5.  A subscription system charges recurring weekly tributes to your paywall. " +
            "Overdue subscriptions trigger warnings at 1 hour and 24 hours, then auto-lock at 48 hours. " +
            "Cancellation incurs a fee of twice one period's amount. ",
            "The Lion sets the tier. You pay it.", 0xFFcccccc, 16);

        addTerm(root,
            "6.  This app cannot be uninstalled except by the Lion. " +
            "It is protected by device administrator privileges. " +
            "Attempting to remove it triggers a $500 penalty. " +
            "Succeeding triggers a $1,000 penalty. " +
            "The bridge will re-enable it within seconds.",
            null, 0xFFcccccc, 16);

        addTerm(root,
            "7.  You may revoke consent at any time by communicating directly with the Lion, " +
            "or by performing a factory reset (available after 150 escape attempts). " +
            "The restriction system is consensual. ",
            "The power dynamic within it is not.", 0xFFcccccc, 16);

        // The kicker
        TextView kicker = new TextView(this);
        kicker.setText("You asked for this.");
        kicker.setTextColor(0xFF888888);
        kicker.setTextSize(18);
        kicker.setTypeface(null, android.graphics.Typeface.ITALIC);
        kicker.setGravity(android.view.Gravity.CENTER);
        kicker.setPadding(0, 16, 0, 40);
        root.addView(kicker);

        // Consent button
        Button consentBtn = new Button(this);
        consentBtn.setText("I CONSENT TO THESE TERMS");
        consentBtn.setTextColor(0xFFffffff);
        consentBtn.setTextSize(16);
        consentBtn.setLetterSpacing(0.06f);
        consentBtn.setTypeface(null, android.graphics.Typeface.BOLD);
        consentBtn.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF881111));
        consentBtn.setPadding(0, 32, 0, 32);
        LinearLayout.LayoutParams consentLp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        consentLp.setMargins(0, 0, 0, 16);
        consentBtn.setLayoutParams(consentLp);
        consentBtn.setOnClickListener(v -> {
            Settings.Global.putInt(getContentResolver(), "focus_lock_consented", 1);
            Settings.Global.putLong(getContentResolver(), "focus_lock_consent_time",
                System.currentTimeMillis());
            // Detect and store the current home launcher BEFORE requesting the role
            storePriorHomePkg();
            // Request ROLE_HOME on Android 10+ so the home button always lands here
            if (Build.VERSION.SDK_INT >= 29) {
                try {
                    RoleManager rm = (RoleManager) getSystemService(Context.ROLE_SERVICE);
                    if (rm != null && !rm.isRoleHeld(RoleManager.ROLE_HOME)) {
                        startActivityForResult(
                            rm.createRequestRoleIntent(RoleManager.ROLE_HOME), REQ_ROLE_HOME);
                        return; // dialog shown in onActivityResult
                    }
                } catch (Exception e) { /* pre-Q or role unavailable — fall through */ }
            }
            showConsentRecordedDialog();
        });
        root.addView(consentBtn);

        // Decline button
        Button declineBtn = new Button(this);
        declineBtn.setText("I do not consent");
        declineBtn.setTextColor(0xFF444444);
        declineBtn.setTextSize(14);
        declineBtn.setBackgroundTintList(android.content.res.ColorStateList.valueOf(0xFF111118));
        declineBtn.setPadding(0, 24, 0, 24);
        declineBtn.setOnClickListener(v -> {
            new AlertDialog.Builder(this)
                .setTitle("Consent Declined")
                .setMessage("The Collar will not activate.\n\nYou may uninstall the app or " +
                    "return to this screen at any time to reconsider.\n\n" +
                    "The Lion will be informed.")
                .setPositiveButton("OK", (d, w) -> finish())
                .setCancelable(false)
                .show();
        });
        root.addView(declineBtn);

        scroll.addView(root);
        setContentView(scroll);

        getWindow().setStatusBarColor(0xFF0a0a14);
        getWindow().setNavigationBarColor(0xFF0a0a14);
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode == REQ_ROLE_HOME) {
            showConsentRecordedDialog();
        }
    }

    private void showConsentRecordedDialog() {
        new AlertDialog.Builder(this)
            .setTitle("Consent Recorded")
            .setMessage("Timestamp: " + new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss")
                .format(new java.util.Date()) +
                "\n\nThe cage is ready. The Lion can now lock this phone at any time." +
                "\n\nThis app will not appear in your launcher. " +
                "It runs silently in the background, waiting.")
            .setPositiveButton("Understood", (d, w) -> finish())
            .setCancelable(false)
            .show();
    }

    /** Store the current default home launcher so we can return to it on unlock. */
    private void storePriorHomePkg() {
        Intent homeIntent = new Intent(Intent.ACTION_MAIN);
        homeIntent.addCategory(Intent.CATEGORY_HOME);
        List<ResolveInfo> homes = getPackageManager().queryIntentActivities(homeIntent, 0);
        for (ResolveInfo ri : homes) {
            if (!"com.focuslock".equals(ri.activityInfo.packageName)) {
                Settings.Global.putString(getContentResolver(),
                    "focus_lock_prior_home_pkg", ri.activityInfo.packageName);
                return;
            }
        }
    }

    /** Add a term paragraph. If highlight is non-null, it's appended in bold red. */
    private void addTerm(LinearLayout root, String text, String highlight, int color, float textSize) {
        TextView tv = new TextView(this);
        if (highlight != null) {
            String full = text + highlight;
            SpannableString span = new SpannableString(full);
            int start = text.length();
            int end = full.length();
            span.setSpan(new ForegroundColorSpan(0xFFee4444), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            span.setSpan(new StyleSpan(android.graphics.Typeface.BOLD), start, end, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE);
            tv.setText(span);
        } else {
            tv.setText(text);
        }
        tv.setTextColor(color);
        tv.setTextSize(textSize);
        tv.setLineSpacing(6, 1.15f);
        tv.setPadding(0, 0, 0, 28);
        root.addView(tv);
    }
}

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

    private static final int REQ_OWN_ADMIN = 4801;
    private static final int REQ_ROLE_HOME = 1001;

    // Radio ids for the cage-ceiling chooser (arbitrary, view-local).
    private static final int RB_LEASH = 2001;
    private static final int RB_COLLAR = 2002;
    private static final int RB_SEALED = 2003;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Already consented? Skip straight through.
        if (ConsentStore.isConsented(this)) {
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
            "that auto-lock your phone with a $100 paywall if breached (your location stays on " +
            "your phone — only the fact of a breach is reported), assign writing and photo tasks " +
            "that you complete and submit yourself, and remotely control connected Lovense " +
            "devices at any intensity.",
            null, 0xFFcccccc, 16);

        addTerm(root,
            "5.  A subscription system charges recurring weekly tributes to your paywall. " +
            "Overdue subscriptions trigger warnings at 1 hour and 24 hours, then auto-lock at 48 hours. " +
            "Cancellation incurs a fee of twice one period's amount. ",
            "The Lion sets the tier. You pay it.", 0xFFcccccc, 16);

        addTerm(root,
            "6.  This app resists casual removal — it holds device-administrator privileges, " +
            "re-locks if disabled, and the bridge may re-enable it. This is friction, not a trap: " +
            "no financial penalty is applied for tampering, and you are never locked in.",
            null, 0xFFcccccc, 16);

        addTerm(root,
            "7.  You may end this at any time — your consent is always revocable. Use your " +
            "panic safeword (long-press the lock message, then type your phrase) for an " +
            "immediate release with no penalty; a factory reset is always available as the " +
            "ultimate exit. The restriction system is consensual. ",
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

        // Safeword setup — the wearer's guaranteed, always-available exit phrase.
        TextView safewordLabel = new TextView(this);
        safewordLabel.setText("Set your safeword phrase. Type it on the lock screen at any time "
            + "(long-press the message) for an immediate release with no penalty — no approval "
            + "needed. Leave blank to use the default: \"I NEED OUT\".");
        safewordLabel.setTextColor(0xFFc8a84e);
        safewordLabel.setTextSize(14);
        safewordLabel.setLineSpacing(6, 1.15f);
        safewordLabel.setPadding(0, 0, 0, 12);
        root.addView(safewordLabel);

        final android.widget.EditText safewordInput = new android.widget.EditText(this);
        safewordInput.setHint("Safeword phrase (optional)");
        safewordInput.setTextColor(0xFFe0e0e0);
        safewordInput.setHintTextColor(0xFF555555);
        safewordInput.setPadding(24, 24, 24, 24);
        LinearLayout.LayoutParams swLp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        swLp.setMargins(0, 0, 0, 32);
        safewordInput.setLayoutParams(swLp);
        root.addView(safewordInput);

        // Cage ceiling — the wearer's own boundary for how tight the Collar may
        // ever get. This is set ONLY here, and stored app-private, so the Lion
        // (or their bridge) can loosen it but can never tighten past it. Defaults
        // to Leash so consenting never silently hands over a stricter cage than
        // the wearer chose.
        TextView cageLabel = new TextView(this);
        cageLabel.setText("How tight may the cage get? This is your ceiling — the Lion can "
            + "loosen it but never exceed it, and it can only be set here.");
        cageLabel.setTextColor(0xFFc8a84e);
        cageLabel.setTextSize(14);
        cageLabel.setLineSpacing(6, 1.15f);
        cageLabel.setPadding(0, 0, 0, 12);
        root.addView(cageLabel);

        final android.widget.RadioGroup cageGroup = new android.widget.RadioGroup(this);
        cageGroup.setOrientation(LinearLayout.VERTICAL);
        cageGroup.setPadding(0, 0, 0, 32);
        final android.widget.RadioButton rbLeash = new android.widget.RadioButton(this);
        rbLeash.setId(RB_LEASH);
        rbLeash.setText("Leash — the home button brings you back here, but you can still "
            + "open your other apps. (default)");
        final android.widget.RadioButton rbCollar = new android.widget.RadioButton(this);
        rbCollar.setId(RB_COLLAR);
        rbCollar.setText("Collar — opening any non-allowed app snaps you back to the cage. "
            + "Calls, keyboard, your banking app and the camera still work.");
        final android.widget.RadioButton rbSealed = new android.widget.RadioButton(this);
        rbSealed.setId(RB_SEALED);
        rbSealed.setText("Sealed — Collar, plus no ordinary calls. Emergency calls always work.");
        for (android.widget.RadioButton rb : new android.widget.RadioButton[]{rbLeash, rbCollar, rbSealed}) {
            rb.setTextColor(0xFFcccccc);
            rb.setTextSize(14);
            rb.setPadding(12, 10, 0, 10);
            cageGroup.addView(rb);
        }
        cageGroup.check(RB_LEASH);
        root.addView(cageGroup);

        // Device Owner is a PREFERENCE of Sealed, never a requirement: Android
        // only grants it during provisioning, on a phone with no accounts
        // added, in practice straight after a factory reset. Gating Sealed on
        // it would mean the dynamic could only tighten on a freshly wiped
        // phone — a constraint on the relationship rather than on the wearer.
        // So Sealed works without it and says exactly how much less it holds.
        final TextView ownerNote = new TextView(this);
        ownerNote.setTextColor(0xFF998866);
        ownerNote.setTextSize(12);
        ownerNote.setPadding(12, 8, 12, 16);
        ownerNote.setVisibility(View.GONE);
        root.addView(ownerNote);
        cageGroup.setOnCheckedChangeListener((g, id) -> {
            if (id != RB_SEALED) {
                ownerNote.setVisibility(View.GONE);
                return;
            }
            ownerNote.setText(deviceOwnerExplanation());
            ownerNote.setVisibility(View.VISIBLE);
        });

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
            String sw = safewordInput.getText().toString().trim();
            // Persist via ConsentStore (SharedPreferences + best-effort Settings.Global)
            // so consent + safeword survive a fresh install that has not yet been granted
            // WRITE_SECURE_SETTINGS by the operator — the safeword is the wearer's exit and
            // must never silently fail to save.
            ConsentStore.setSafeword(this, sw.isEmpty() ? "I NEED OUT" : sw);
            // Persist the chosen cage ceiling (app-private, bridge-unwritable).
            int cage;
            switch (cageGroup.getCheckedRadioButtonId()) {
                case RB_COLLAR: cage = 1; break;  // ShadeGuardService.LEVEL_COLLAR
                case RB_SEALED: cage = 2; break;  // ShadeGuardService.LEVEL_SEALED
                default:        cage = 0; break;  // ShadeGuardService.LEVEL_LEASH
            }
            ConsentStore.setCageLevel(this, cage);
            ConsentStore.setConsented(this);
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
            requestOwnAdminThenFinish();
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
            requestOwnAdminThenFinish();
        } else if (requestCode == REQ_OWN_ADMIN) {
            // Whatever they chose on the system screen, consent itself is
            // already recorded; the dialog reports where things stand.
            showConsentRecordedDialog();
        }
    }

    /** Ask for the Collar's own device admin, then close out consent.
     *
     *  <p>Nothing asked for this before. Both apps hold device-administrator
     *  privileges and ControlService's mutual-admin monitor re-locks and
     *  reports a tamper event when Bunny Tasker's is missing — so a freshly
     *  consented pair went straight into a relock loop over an admin that had
     *  never been granted, explained by a notification saying it was
     *  "removed". Asking here is the difference between a cage that closes and
     *  one that appears to be malfunctioning on first use.
     *
     *  <p>Declining is allowed: consent is already recorded and the safeword
     *  already saved, so a wearer who backs out here is not trapped half-caged.
     *  The dialog tells them what is missing and how to finish. */
    private void requestOwnAdminThenFinish() {
        try {
            android.app.admin.DevicePolicyManager dpm =
                (android.app.admin.DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
            android.content.ComponentName admin =
                new android.content.ComponentName(this, AdminReceiver.class);
            if (dpm != null && !dpm.isAdminActive(admin)) {
                Intent activate = new Intent(
                    android.app.admin.DevicePolicyManager.ACTION_ADD_DEVICE_ADMIN);
                activate.putExtra(
                    android.app.admin.DevicePolicyManager.EXTRA_DEVICE_ADMIN, admin);
                activate.putExtra(
                    android.app.admin.DevicePolicyManager.EXTRA_ADD_EXPLANATION,
                    "The Collar needs this to hold a lock. Without it the cage can be "
                    + "shrugged off by uninstalling the app mid-lock, and your Lion is "
                    + "told either way.");
                startActivityForResult(activate, REQ_OWN_ADMIN);
                return;
            }
        } catch (Exception e) {
            android.util.Log.w("FocusLock", "could not request device admin", e);
        }
        showConsentRecordedDialog();
    }

    private void showConsentRecordedDialog() {
        boolean adminOn = false;
        try {
            android.app.admin.DevicePolicyManager dpm =
                (android.app.admin.DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
            adminOn = dpm != null
                && dpm.isAdminActive(new android.content.ComponentName(this, AdminReceiver.class));
        } catch (Exception ignored) {}
        if (!adminOn) {
            // Say so plainly rather than reporting a cage that is not closed.
            new AlertDialog.Builder(this)
                .setTitle("Consent Recorded — but not yet held")
                .setMessage("Your consent and safeword are saved.\n\nThe Collar does not have "
                    + "device admin, so locks can be shrugged off by uninstalling it. Grant it "
                    + "in Settings \u2192 Security \u2192 Device admin apps, or reopen this "
                    + "screen to be asked again.")
                .setPositiveButton("UNDERSTOOD", (d, w) -> finish())
                .setCancelable(false)
                .show();
            return;
        }
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

    /** Store the current default home launcher so we can return to it on unlock.
     *  Resolves the user's *current* default (e.g. Fossify Launcher) via
     *  MATCH_DEFAULT_ONLY first; only falls back to "first non-Collar launcher
     *  in the enumeration" if no default is set. Without MATCH_DEFAULT_ONLY we
     *  would take whichever launcher PackageManager happened to enumerate
     *  first — often the stock launcher even when the user has switched to
     *  a third-party. */
    /** Whether the Collar currently holds Device Owner. */
    private boolean isDeviceOwner() {
        try {
            android.app.admin.DevicePolicyManager dpm =
                (android.app.admin.DevicePolicyManager) getSystemService(DEVICE_POLICY_SERVICE);
            return dpm != null && dpm.isDeviceOwnerApp(getPackageName());
        } catch (Exception e) {
            return false;
        }
    }

    /** What Sealed gains from Device Owner, and what it costs to get.
     *
     *  <p>Written out in full rather than as a checkbox because it is the one
     *  choice here that cannot be revisited without wiping the phone. A wearer
     *  who picks Sealed and later learns it could have held harder — but only
     *  before they had a phone full of their things — was not told enough. */
    private String deviceOwnerExplanation() {
        if (isDeviceOwner()) {
            return "Device Owner: ACTIVE. Sealed holds at full strength — The Collar can "
                + "block its own uninstall and refuse safe-mode boot. Factory reset stays "
                + "available, always.";
        }
        return "Sealed prefers Device Owner, which this phone does not have.\n\n"
            + "WITH it, The Collar can block its own uninstall and stop a safe-mode boot "
            + "from suspending the cage — the wearer cannot lift the restrictions "
            + "themselves.\n\n"
            + "WITHOUT it, Sealed still bounces apps and still blocks ordinary calls, but "
            + "the wearer can uninstall the app from Settings, and booting into safe mode "
            + "suspends enforcement until they boot normally again.\n\n"
            + "Android only grants Device Owner during provisioning, on a phone with no "
            + "accounts added — in practice straight after a factory reset. It cannot be "
            + "switched on from here, now or later, without wiping this phone first. "
            + "Sealed is yours either way; this only decides how firmly it holds.\n\n"
            + "Factory reset always remains available, with or without it.";
    }

    private void storePriorHomePkg() {
        Intent homeIntent = new Intent(Intent.ACTION_MAIN);
        homeIntent.addCategory(Intent.CATEGORY_HOME);

        // Preferred path: whatever the user has currently set as default.
        try {
            ResolveInfo info = getPackageManager().resolveActivity(
                homeIntent, android.content.pm.PackageManager.MATCH_DEFAULT_ONLY);
            if (info != null && info.activityInfo != null) {
                String pkg = info.activityInfo.packageName;
                // Skip ourselves (FocusActivity declares CATEGORY_HOME) and
                // the "android" chooser pseudo-activity that resolves when
                // no default is set.
                if (!"com.focuslock".equals(pkg) && !"android".equals(pkg)) {
                    Settings.Global.putString(getContentResolver(),
                        "focus_lock_prior_home_pkg", pkg);
                    return;
                }
            }
        } catch (Exception e) { /* fall through to enumeration */ }

        // Fallback: first non-Collar launcher in the full enumeration. Used
        // only when the user has no default set (chooser appears on HOME
        // press) — still better than capturing stock when the user installed
        // a third-party launcher but never picked one as default.
        // Guarded: writing focus_lock_prior_home_pkg needs WRITE_SECURE_SETTINGS,
        // which a fresh (pre-operator-provisioning) device lacks — this is
        // best-effort (recage re-records prior home authoritatively), so a
        // missing grant must not crash the consent tap that just succeeded.
        try {
            List<ResolveInfo> homes = getPackageManager().queryIntentActivities(homeIntent, 0);
            for (ResolveInfo ri : homes) {
                String pkg = ri.activityInfo.packageName;
                if (!"com.focuslock".equals(pkg) && !"android".equals(pkg)) {
                    Settings.Global.putString(getContentResolver(),
                        "focus_lock_prior_home_pkg", pkg);
                    return;
                }
            }
        } catch (Exception e) { /* best-effort — see above */ }
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

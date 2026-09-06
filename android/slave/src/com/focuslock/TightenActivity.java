package com.focuslock;

import android.app.Activity;
import android.graphics.Color;
import android.os.Bundle;
import android.view.View;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

/**
 * The wearer tightening their own ceiling, after consent.
 *
 * <p>The ceiling ({@link ConsentStore#setCageLevel}) was writable exactly once,
 * on the Terms-of-Surrender screen, and never again. That made it a decision
 * taken at the least informed moment there is — before wearing the thing — and
 * left the only way to go further a factory reset.
 *
 * <p><b>Why this lives in the Collar and not in Bunny Tasker.</b> The ceiling is
 * deliberately stored in this app's private SharedPreferences, because that is
 * the one store the Lion's ADB bridge cannot write: `settings put global
 * focus_lock_cage_level 2` must never be able to tighten the cage past what the
 * wearer agreed to. Routing a "tighten request" through Settings.Global so the
 * companion could write it would hand exactly that capability back. So Bunny
 * Tasker launches this screen instead, and the write happens here, in the
 * process that owns the boundary, behind a confirmation a bridge cannot tap.
 *
 * <p><b>One direction only.</b> This screen offers strictly tighter tiers than
 * the current ceiling and nothing else. Loosening is the Lion's to give
 * ({@code focus_lock_cage_level_lion}, clamped by min() in
 * {@link ShadeGuardService#effectiveCageLevel}), which is the whole asymmetry:
 * escalation takes the wearer's own act, mercy takes the Lion's. Neither party
 * can move it in the direction that serves them.
 */
public class TightenActivity extends Activity {

    private static final int[] LEVELS = {
        ShadeGuardService.LEVEL_LEASH,
        ShadeGuardService.LEVEL_COLLAR,
        ShadeGuardService.LEVEL_SEALED,
    };

    static String levelName(int level) {
        switch (level) {
            case ShadeGuardService.LEVEL_SEALED: return "Sealed";
            case ShadeGuardService.LEVEL_COLLAR: return "Collar";
            default: return "Leash";
        }
    }

    private static String levelBlurb(int level) {
        switch (level) {
            case ShadeGuardService.LEVEL_SEALED:
                return "Collar, plus no ordinary calls. Emergency calls always work.";
            case ShadeGuardService.LEVEL_COLLAR:
                return "Opening any non-allowed app snaps you back to the cage. Calls, "
                    + "keyboard, your banking app and the camera still work.";
            default:
                return "The home button brings you back, but you can still open your other apps.";
        }
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        ScrollView scroll = new ScrollView(this);
        scroll.setBackgroundColor(0xFF0a0812);
        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setPadding(48, 72, 48, 48);
        scroll.addView(root);
        setContentView(scroll);

        int current = ConsentStore.getCageLevel(this);
        if (current < ShadeGuardService.LEVEL_LEASH) current = ShadeGuardService.LEVEL_LEASH;
        if (current > ShadeGuardService.LEVEL_SEALED) current = ShadeGuardService.LEVEL_SEALED;

        TextView title = new TextView(this);
        title.setText("Tighten the cage");
        title.setTextColor(0xFFc8a84e);
        title.setTextSize(22);
        title.setPadding(0, 0, 0, 16);
        root.addView(title);

        TextView blurb = new TextView(this);
        blurb.setText("Your ceiling is " + levelName(current) + ".\n\n"
            + "You can raise it. You cannot lower it — that is your Lion's to give, "
            + "and They can never raise it past what you set here.");
        blurb.setTextColor(0xFFcccccc);
        blurb.setTextSize(15);
        blurb.setLineSpacing(6, 1.15f);
        blurb.setPadding(0, 0, 0, 28);
        root.addView(blurb);

        boolean any = false;
        for (int level : LEVELS) {
            if (level <= current) continue;   // one direction only
            any = true;
            root.addView(tierButton(level, current));
        }

        if (!any) {
            TextView done = new TextView(this);
            done.setText("Sealed is the tightest there is. There is nothing further to give.");
            done.setTextColor(0xFF998866);
            done.setTextSize(14);
            done.setPadding(0, 0, 0, 28);
            root.addView(done);
        }

        android.widget.Button back = new android.widget.Button(this);
        back.setText(any ? "Leave it where it is" : "Back");
        back.setBackgroundColor(0xFF1a1a2e);
        back.setTextColor(0xFF888888);
        back.setOnClickListener(v -> finish());
        root.addView(back);
    }

    private View tierButton(final int level, final int current) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setBackgroundColor(0xFF12101c);
        card.setPadding(28, 24, 28, 24);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.setMargins(0, 0, 0, 20);
        card.setLayoutParams(lp);

        TextView name = new TextView(this);
        name.setText(levelName(level));
        name.setTextColor(0xFFc8a84e);
        name.setTextSize(17);
        card.addView(name);

        TextView what = new TextView(this);
        what.setText(levelBlurb(level));
        what.setTextColor(0xFFaaaaaa);
        what.setTextSize(13);
        what.setLineSpacing(4, 1.1f);
        what.setPadding(0, 8, 0, 16);
        card.addView(what);

        android.widget.Button go = new android.widget.Button(this);
        go.setText("Tighten to " + levelName(level));
        go.setBackgroundColor(0xFF2a2510);
        go.setTextColor(0xFFDAA520);
        go.setOnClickListener(v -> confirm(level, current));
        card.addView(go);

        return card;
    }

    /**
     * Second tap, and it names what is being given up.
     *
     * <p>Not friction for its own sake: this is the one control in the system
     * that only moves one way, so a mis-tap is not recoverable by the person
     * who made it. Saying so is part of it being consent rather than a switch.
     */
    private void confirm(final int level, final int current) {
        new android.app.AlertDialog.Builder(this)
            .setTitle("Tighten to " + levelName(level) + "?")
            .setMessage(levelBlurb(level)
                + "\n\nThis only goes one way. You will not be able to put it back to "
                + levelName(current) + " yourself — only your Lion can loosen it, and only "
                + "if They choose to.")
            .setPositiveButton("Tighten it", (d, w) -> {
                ConsentStore.setCageLevel(this, level);
                android.util.Log.w("FocusLock",
                    "cage ceiling tightened by wearer: " + levelName(current) + " -> " + levelName(level));
                new android.app.AlertDialog.Builder(this)
                    .setTitle(levelName(level))
                    .setMessage("Your ceiling is " + levelName(level) + " now.")
                    .setPositiveButton("Good", (d2, w2) -> finish())
                    .setCancelable(false)
                    .show();
            })
            .setNegativeButton("Not yet", null)
            .show();
    }
}

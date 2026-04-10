package com.focuslock;

import android.app.Activity;
import android.os.Bundle;
import android.view.Gravity;
import android.view.WindowManager;
import android.widget.LinearLayout;
import android.widget.TextView;

/**
 * Shown briefly during authorized release before the app self-destructs.
 * Mirrors the visual style of ConsentActivity but celebrates freedom.
 */
public class LiberationActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);

        LinearLayout root = new LinearLayout(this);
        root.setOrientation(LinearLayout.VERTICAL);
        root.setGravity(Gravity.CENTER);
        root.setBackgroundColor(0xFF0a0a14);
        root.setPadding(64, 0, 64, 0);

        TextView title = new TextView(this);
        title.setText("LIBERATED");
        title.setTextSize(42);
        title.setTextColor(0xFF44cc44);
        title.setGravity(Gravity.CENTER);
        title.setLetterSpacing(0.15f);
        root.addView(title);

        TextView divider = new TextView(this);
        divider.setText("\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500\u2500");
        divider.setTextColor(0xFF2a2a2a);
        divider.setGravity(Gravity.CENTER);
        divider.setPadding(0, 16, 0, 16);
        root.addView(divider);

        TextView message = new TextView(this);
        message.setText("This device has been released from the mesh.\n\n"
            + "All restrictions are lifted.\n"
            + "The collar is gone.\n\n"
            + "You are free.\n\n"
            + "This app will uninstall itself shortly.");
        message.setTextSize(18);
        message.setTextColor(0xFFaaaaaa);
        message.setGravity(Gravity.CENTER);
        message.setLineSpacing(4, 1.2f);
        root.addView(message);

        TextView timestamp = new TextView(this);
        timestamp.setText(new java.text.SimpleDateFormat("yyyy-MM-dd HH:mm")
            .format(new java.util.Date()));
        timestamp.setTextSize(12);
        timestamp.setTextColor(0xFF444444);
        timestamp.setGravity(Gravity.CENTER);
        timestamp.setPadding(0, 32, 0, 0);
        root.addView(timestamp);

        setContentView(root);
    }
}

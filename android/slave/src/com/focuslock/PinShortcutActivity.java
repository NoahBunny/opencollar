package com.focuslock;

import android.app.Activity;
import android.content.Context;
import android.content.pm.LauncherApps;
import android.content.pm.ShortcutInfo;
import android.os.Bundle;
import android.provider.Settings;
import android.util.Log;
import android.widget.Toast;

/**
 * Confirms pin-shortcut requests so PWAs can be installed while the Collar is
 * the default home app.
 *
 * <p>The Collar claims {@code CATEGORY_HOME} to intercept the home button during
 * a lock and hands straight back to the wearer's real launcher otherwise. But
 * Android routes {@code ShortcutManager.requestPinShortcut()} to whichever app
 * is the DEFAULT home, and with no activity answering
 * {@code ACTION_CONFIRM_PIN_SHORTCUT} it reports pinning unsupported for the
 * whole device. Chrome's "Add to Home screen" then has nowhere to go, and the
 * wearer cannot install a PWA — a restriction nobody negotiated, imposed as a
 * side effect of the cage rather than as part of it.
 *
 * <p>This activity exists to answer. It has no UI beyond a toast: it accepts or
 * refuses and finishes.
 */
public class PinShortcutActivity extends Activity {

    private static final String TAG = "FocusLock";
    /** Comma-free record of what has been pinned, for the Lion's audit trail. */
    private static final String PREFS = "pinned_shortcuts";

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        try {
            handle();
        } catch (Exception e) {
            Log.w(TAG, "pin-shortcut request failed", e);
        }
        finish();
    }

    private void handle() {
        LauncherApps la = (LauncherApps) getSystemService(Context.LAUNCHER_APPS_SERVICE);
        if (la == null) return;
        LauncherApps.PinItemRequest req = la.getPinItemRequest(getIntent());
        if (req == null || !req.isValid()) return;

        // Widgets are a different contract with a different host surface, and
        // the Collar hosts none. Refusing is honest; accepting would strand a
        // widget nothing can draw.
        if (req.getRequestType() != LauncherApps.PinItemRequest.REQUEST_TYPE_SHORTCUT) {
            Log.i(TAG, "pin request refused: not a shortcut");
            return;
        }

        ShortcutInfo info = req.getShortcutInfo();
        String pkg = info != null ? info.getPackage() : "";
        CharSequence label = info != null ? info.getShortLabel() : null;
        String name = PwaShortcut.describe(label == null ? null : label.toString(), pkg);

        if (!PwaShortcut.mayAccept(isLockActive())) {
            // Not counted as an escape: the wearer asked the phone for
            // something ordinary at a moment it is not on offer.
            Log.i(TAG, "pin request refused while locked: " + name);
            Toast.makeText(this, "Locked — you can add " + name + " once the lock lifts",
                Toast.LENGTH_LONG).show();
            return;
        }

        req.accept();
        record(name, pkg);
        Log.i(TAG, "pinned " + name + " (" + (PwaShortcut.isWebApk(pkg) ? "web app" : "shortcut") + ")");
        Toast.makeText(this, "Added " + name, Toast.LENGTH_SHORT).show();
    }

    /** Keep a note of what was pinned.
     *
     *  <p>The Collar is a home interceptor, not a launcher: accepting the pin
     *  satisfies Chrome and lets the install finish, but the icon lands on
     *  whichever launcher actually draws a home screen. This record is so the
     *  Lion can see what was added rather than having to notice an icon.
     */
    private void record(String name, String pkg) {
        try {
            android.content.SharedPreferences sp = getSharedPreferences(PREFS, MODE_PRIVATE);
            String prior = sp.getString("pinned", "");
            String line = System.currentTimeMillis() + "\t" + pkg + "\t" + name;
            sp.edit().putString("pinned", prior.isEmpty() ? line : prior + "\n" + line).apply();
        } catch (Exception e) {
            Log.w(TAG, "could not record pinned shortcut", e);
        }
    }

    /** Same signal FocusActivity gates on. Reads unguarded settings defensively:
     *  a Collar that cannot read the lock flag must refuse, not assume unlocked. */
    private boolean isLockActive() {
        try {
            return Settings.Global.getInt(getContentResolver(), "focus_lock_active", 0) == 1;
        } catch (Exception e) {
            Log.w(TAG, "could not read lock state; treating as locked", e);
            return true;
        }
    }
}

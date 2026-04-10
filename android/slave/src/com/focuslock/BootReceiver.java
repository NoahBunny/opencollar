package com.focuslock;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.util.Log;

public class BootReceiver extends BroadcastReceiver {
    private static final String TAG = "FocusLock";

    @Override
    public void onReceive(Context context, Intent intent) {
        Log.i(TAG, "BootReceiver fired: " + intent.getAction());
        // Always start ControlService — it handles everything:
        // HTTP server, jail watcher, ADB re-enable, lock re-engage
        try {
            Intent svc = new Intent(context, ControlService.class);
            context.startForegroundService(svc);
            Log.i(TAG, "ControlService started from BootReceiver");
        } catch (Exception e) {
            Log.e(TAG, "Failed to start ControlService", e);
        }
    }
}

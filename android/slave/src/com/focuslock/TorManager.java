package com.focuslock;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.os.Handler;
import android.os.IBinder;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;

import net.freehaven.tor.control.OnionControl;
import net.freehaven.tor.control.TorControlConnection;

import org.torproject.jni.TorService;

import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;

/**
 * The Collar (host) side of A3: provisions a client-authed v3 onion that forwards
 * to the existing HTTP server on 127.0.0.1:8432, and brings Tor up on demand
 * (ntfy cold-wake) or keeps it warm. Only compiled when FOCUSLOCK_TOR_AAR is set
 * (build.sh excludes it otherwise); the base code reaches it through TorHook
 * reflection so the default-off build never references this class.
 *
 * RUNTIME VALIDATION PENDING (C3, docs/TOR-ONION.md): the offline key/.onion
 * crypto is unit-verified (OnionKeysTest), but the control-port lifecycle below
 * must be exercised on waydroid/device before shipping.
 */
public final class TorManager {
    private static final String TAG = "TorManager";

    /** The Collar's existing plaintext HTTP server (ControlService) port. */
    static final int ONION_PORT = 8432;

    private final Context ctx;
    private volatile TorService torService;
    private volatile boolean bound = false;
    private volatile String serviceId = null;   // onion w/o ".onion" while published

    public TorManager(Context ctx) {
        this.ctx = ctx.getApplicationContext();
    }

    // ── Settings.Global advertisement (read by selfAddressFields / ntfy loop) ──

    private String gstr(String k) {
        String v = Settings.Global.getString(ctx.getContentResolver(), k);
        return v == null ? "" : v;
    }

    private void gput(String k, String v) {
        try { Settings.Global.putString(ctx.getContentResolver(), k, v); }
        catch (Exception e) { Log.w(TAG, "Settings.Global put " + k + " failed (needs WRITE_SECURE_SETTINGS)", e); }
    }

    // ── Onion key persistence (app-private; allowBackup=false on the Collar) ──

    private File seedFile() {
        File d = new File(ctx.getFilesDir(), "tor_hs");
        if (!d.exists()) d.mkdirs();
        return new File(d, "onion_seed");
    }

    private byte[] loadOrCreateSeed() throws Exception {
        File f = seedFile();
        if (f.exists() && f.length() == 32) {
            byte[] s = new byte[32];
            try (FileInputStream in = new FileInputStream(f)) {
                if (in.read(s) == 32) return s;
            }
        }
        byte[] s = OnionKeys.randomSeed();
        try (FileOutputStream out = new FileOutputStream(f)) { out.write(s); }
        try { f.setReadable(false, false); f.setReadable(true, true); } catch (Exception ignore) {}
        return s;
    }

    /** Idempotent provisioning at pair time: ensure the onion key exists, derive
     *  the .onion offline, and advertise it (focus_lock_onion_addr, already
     *  surfaced by selfAddressFields() in the pair response). Also records the
     *  onion-derived ntfy wake topic so the Collar can subscribe to it even in a
     *  relay-less (no mesh_id) direct pairing. Returns the .onion, or "" on error. */
    public String provisionOnionIfNeeded() {
        try {
            byte[] seed = loadOrCreateSeed();
            String onion = OnionKeys.onionFromSeed(seed);
            if (!onion.equals(gstr("focus_lock_onion_addr"))) gput("focus_lock_onion_addr", onion);
            String topic = wakeTopicForOnion(onion);
            if (!topic.isEmpty() && !topic.equals(gstr("focus_lock_onion_wake_topic"))) {
                gput("focus_lock_onion_wake_topic", topic);
            }
            return onion;
        } catch (Exception e) {
            Log.w(TAG, "provisionOnionIfNeeded failed", e);
            return "";
        }
    }

    /** Persist a Lion device's x25519 client-auth pubkey (base32). Newline-joined
     *  set so multiple Lion devices each get their own ClientAuthV3= clause. */
    public void storeLionAuthPub(String base32Pub) {
        if (base32Pub == null) return;
        base32Pub = base32Pub.trim();
        if (base32Pub.isEmpty()) return;
        String cur = gstr("focus_lock_onion_auth_pub");
        for (String k : cur.split("\n")) if (k.trim().equals(base32Pub)) return;  // dup
        gput("focus_lock_onion_auth_pub", cur.isEmpty() ? base32Pub : cur + "\n" + base32Pub);
    }

    private String[] lionAuthPubs() {
        String cur = gstr("focus_lock_onion_auth_pub");
        return cur.isEmpty() ? new String[0] : cur.split("\n");
    }

    public boolean isWarm() { return "1".equals(gstr("focus_lock_tor_warm")); }

    /** Onion-derived ntfy wake topic, shared by both sides post-pair (the Collar
     *  knows its own onion; Lion learns it in the pair response). Removes the
     *  mesh_id dependency so cold-wake works in relay-less direct pairings. */
    public static String wakeTopicForOnion(String onion) {
        try {
            byte[] h = MessageDigest.getInstance("SHA-256").digest(onion.getBytes(StandardCharsets.US_ASCII));
            return "focuslock-w-" + OnionKeys.base32(h).substring(0, 16);
        } catch (Exception e) { return ""; }
    }

    // ── Tor lifecycle ──

    private final ServiceConnection conn = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder binder) {
            try { torService = ((TorService.LocalBinder) binder).getService(); bound = true; }
            catch (Exception e) { Log.w(TAG, "bind cast failed", e); }
        }
        @Override public void onServiceDisconnected(ComponentName name) { torService = null; bound = false; }
    };

    /** Append the battery-friendly torrc lines before ACTION_START. Persistent
     *  DataDirectory is TorService's default; padding reductions cut idle drain. */
    private void appendTorrc() {
        try {
            File torrc = TorService.getTorrc(ctx);
            if (torrc == null) return;
            String body = "\nReducedConnectionPadding 1\nReducedCircuitPadding 1\n";
            try (FileOutputStream out = new FileOutputStream(torrc, true)) {
                out.write(body.getBytes(StandardCharsets.US_ASCII));
            }
        } catch (Exception e) { Log.w(TAG, "appendTorrc failed", e); }
    }

    /** Poll for the authenticated control connection (TorService returns it once
     *  the control port is up), up to timeoutMs. */
    private TorControlConnection awaitControl(long timeoutMs) {
        long deadline = System.currentTimeMillis() + timeoutMs;
        while (System.currentTimeMillis() < deadline) {
            TorService ts = torService;
            if (ts != null) {
                try {
                    TorControlConnection cc = ts.getTorControlConnection();
                    if (cc != null) return cc;
                } catch (Exception ignore) {}
            }
            try { Thread.sleep(250); } catch (InterruptedException e) { return null; }
        }
        return null;
    }

    /** Start Tor (idempotent), publish the client-authed onion forwarding to the
     *  local HTTP server, and — unless warm — schedule teardown after
     *  sessionMinutes. Blocking; call off the main thread. Silent no-op on any
     *  failure (callers keep serving over LAN/relay). */
    public synchronized void startTorAndPublish(int sessionMinutes) {
        try {
            byte[] seed = loadOrCreateSeed();
            String onion = OnionKeys.onionFromSeed(seed);
            String onionNoSuffix = onion.substring(0, onion.length() - ".onion".length());

            appendTorrc();
            if (!bound) {
                ctx.startService(new Intent(ctx, TorService.class).setAction(TorService.ACTION_START));
                ctx.bindService(new Intent(ctx, TorService.class), conn, Context.BIND_AUTO_CREATE);
            }
            TorControlConnection cc = awaitControl(120_000);
            if (cc == null) { Log.w(TAG, "Tor control connection not ready — relay fallback"); return; }

            if (serviceId != null) return;  // already published this session

            // Resolve the client-auth keys FIRST: with none we must not publish
            // at all (an unauthed onion is reachable by anyone who learns the
            // address), and the V3Auth flag below only makes sense with them.
            String[] pubs = lionAuthPubs();
            if (pubs.length == 0) {
                Log.w(TAG, "no Lion auth pubkey stored — onion would be unauthed; skipping publish");
                return;
            }
            // Flags MUST include V3Auth alongside Detach. Tor refuses a
            // ClientAuthV3= clause when the matching auth flag is absent —
            // it replies "No auth type specified" and the whole ADD_ONION
            // fails, so the onion never goes up. (Found on-device 2026-08-08:
            // publish failed every wake with exactly that control error.)
            StringBuilder cmd = new StringBuilder("ADD_ONION ED25519-V3:")
                .append(OnionKeys.addOnionKeyblob(seed))
                .append(" Flags=Detach,V3Auth Port=").append(ONION_PORT)
                .append(",127.0.0.1:").append(ONION_PORT);
            for (String pub : pubs) {
                pub = pub.trim();
                if (!pub.isEmpty()) cmd.append(" ClientAuthV3=").append(pub);
            }
            String id = OnionControl.addOnion(cc, cmd.toString());
            if (id != null && !id.equals(onionNoSuffix)) {
                Log.w(TAG, "published ServiceID " + id + " != derived " + onionNoSuffix);
            }
            serviceId = (id != null) ? id : onionNoSuffix;
            Log.i(TAG, "onion published: " + onion + " (" + pubs.length + " client key(s))");

            if (!isWarm() && sessionMinutes > 0) scheduleTeardown(sessionMinutes);
        } catch (Exception e) {
            Log.w(TAG, "startTorAndPublish failed — relay fallback", e);
        }
    }

    private void scheduleTeardown(int minutes) {
        new Handler(Looper.getMainLooper()).postDelayed(
            () -> new Thread(this::stopTor, "tor-teardown").start(),
            (long) minutes * 60_000L);
    }

    /** Tear down the published onion (DEL_ONION) and stop Tor — skipped while
     *  warm. Blocking; call off the main thread. */
    public synchronized void stopTor() {
        if (isWarm()) return;
        try {
            TorService ts = torService;
            if (ts != null && serviceId != null) {
                TorControlConnection cc = ts.getTorControlConnection();
                if (cc != null) {
                    try { OnionControl.raw(cc, "DEL_ONION " + serviceId); } catch (Exception ignore) {}
                }
            }
            serviceId = null;
            if (bound) { try { ctx.unbindService(conn); } catch (Exception ignore) {} bound = false; }
            ctx.startService(new Intent(ctx, TorService.class).setAction(TorService.ACTION_STOP));
        } catch (Exception e) {
            Log.w(TAG, "stopTor failed", e);
        }
    }
}

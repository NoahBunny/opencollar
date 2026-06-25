package com.focusctl;

import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.ServiceConnection;
import android.content.SharedPreferences;
import android.os.IBinder;
import android.util.Log;

import net.freehaven.tor.control.OnionControl;
import net.freehaven.tor.control.TorControlConnection;

import org.torproject.jni.TorService;

import java.util.Base64;

/**
 * Lion's Share (client) side of A3: holds the x25519 client-auth keypair, brings
 * Tor up on demand, and authorizes the Collar's onion via ONION_CLIENT_AUTH_ADD.
 * Only compiled when FOCUSLOCK_TOR_AAR is set (build.sh excludes it otherwise);
 * MainActivity reaches it through TorHook reflection, and the SOCKS routing in
 * meshPost/directGet uses the default port directly so the default-off build
 * never references this class.
 *
 * RUNTIME VALIDATION PENDING (C3, docs/TOR-ONION.md): keypair + encodings are
 * unit-verified (OnionKeysTest), but the control-port/SOCKS path must be
 * exercised on waydroid/device before shipping.
 */
public final class TorManager {
    private static final String TAG = "TorManager";
    private static final String PREFS = "focusctl_tor";
    private static final String K_PRIV = "x25519_priv_b64";  // std base64
    private static final String K_PUB  = "x25519_pub_b64";   // std base64

    /** tor-android's default SOCKS port; meshPost/directGet route .onion here. */
    public static final int DEFAULT_SOCKS_PORT = 9050;

    private final Context ctx;
    private final SharedPreferences sp;
    private volatile TorService torService;
    private volatile boolean bound = false;

    public TorManager(Context ctx) {
        this.ctx = ctx.getApplicationContext();
        this.sp = this.ctx.getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    // ── x25519 client-auth keypair ──

    /** Generate the x25519 keypair on first use; idempotent thereafter. */
    public synchronized void ensureAuthKeypair() {
        if (!sp.getString(K_PRIV, "").isEmpty()) return;
        byte[][] kp = OnionKeys.x25519Keypair();  // {priv32, pub32}
        sp.edit()
            .putString(K_PRIV, Base64.getEncoder().encodeToString(kp[0]))
            .putString(K_PUB,  Base64.getEncoder().encodeToString(kp[1]))
            .apply();
    }

    /** Lion's x25519 PUBLIC key as base32 — the value POSTed as onion_auth_pub at
     *  pair time and used in the Collar's ClientAuthV3= clause. */
    public String authPubBase32() {
        ensureAuthKeypair();
        byte[] pub = Base64.getDecoder().decode(sp.getString(K_PUB, ""));
        return OnionKeys.base32(pub);
    }

    /** Lion's x25519 PRIVATE key as std base64 — for ONION_CLIENT_AUTH_ADD. */
    private String authPrivBase64() {
        ensureAuthKeypair();
        return sp.getString(K_PRIV, "");
    }

    // ── Tor lifecycle ──

    private final ServiceConnection conn = new ServiceConnection() {
        @Override public void onServiceConnected(ComponentName name, IBinder binder) {
            try { torService = ((TorService.LocalBinder) binder).getService(); bound = true; }
            catch (Exception e) { Log.w(TAG, "bind cast failed", e); }
        }
        @Override public void onServiceDisconnected(ComponentName name) { torService = null; bound = false; }
    };

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

    public int getSocksPort() {
        TorService ts = torService;
        if (ts != null) {
            try { int p = ts.getSocksPort(); if (p > 0) return p; } catch (Exception ignore) {}
        }
        return DEFAULT_SOCKS_PORT;
    }

    /** Start Tor (idempotent), then add the x25519 client-auth key for the given
     *  onion (no ".onion" suffix). Blocking; call off the main thread. Returns
     *  false on bootstrap timeout so callers fall through to the relay. */
    public synchronized boolean ensureTorUpAndAuthorized(String onionNoSuffix, long timeoutMs) {
        try {
            if (!bound) {
                ctx.startService(new Intent(ctx, TorService.class).setAction(TorService.ACTION_START));
                ctx.bindService(new Intent(ctx, TorService.class), conn, Context.BIND_AUTO_CREATE);
            }
            TorControlConnection cc = awaitControl(timeoutMs);
            if (cc == null) { Log.w(TAG, "Tor not ready within " + timeoutMs + "ms — relay fallback"); return false; }
            // Session-scoped client auth (re-added each start; add Flags=Permanent
            // to persist). priv is std base64 — NOT base32 (the #1 footgun).
            OnionControl.raw(cc, "ONION_CLIENT_AUTH_ADD " + onionNoSuffix + " x25519:" + authPrivBase64());
            return true;
        } catch (Exception e) {
            Log.w(TAG, "ensureTorUpAndAuthorized failed — relay fallback", e);
            return false;
        }
    }
}

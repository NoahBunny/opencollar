package com.focuslock;

import android.content.ContentResolver;
import android.content.Context;
import android.provider.Settings;
import android.util.Base64;
import android.util.Log;

import org.json.JSONObject;

import java.security.KeyFactory;
import java.security.PrivateKey;
import java.security.Signature;
import java.security.spec.PKCS8EncodedKeySpec;

/**
 * Audit 2026-04-27 H-2 — sign outbound evidence webhooks with the
 * slave's bunny_privkey so the relay can refuse forged
 * compliment/gratitude/love_letter/geofence-breach/evidence-photo
 * emails injected by anyone else on the network.
 *
 * Canonical payload: "{mesh_id}|{node_id}|{webhook_type}|{ts}".
 * Mirrors Bunny Tasker's existing sendSignedBunnyWebhook for
 * /webhook/bunny-message — same key, same shape, same server-side
 * verifier (focuslock-mail.py:_verify_slave_signed_webhook).
 *
 * Returns the augmented JSON body string, or null when the slave
 * isn't paired yet (mesh_id / node_id / bunny_privkey missing) — the
 * caller should drop the POST in that case.
 */
public class SlaveSigner {
    private static final String TAG = "SlaveSigner";

    /** Sign + attach mesh_id, node_id, ts, signature to {@code body}. */
    public static String signAndAttach(Context ctx, String webhookType, JSONObject body) {
        try {
            ContentResolver cr = ctx.getContentResolver();
            String meshId = Settings.Global.getString(cr, "focus_lock_mesh_id");
            String nodeId = Settings.Global.getString(cr, "focus_lock_mesh_node_id");
            String privB64 = Settings.Global.getString(cr, "focus_lock_bunny_privkey");
            if (meshId == null || meshId.isEmpty()
                || nodeId == null || nodeId.isEmpty()
                || privB64 == null || privB64.isEmpty()) {
                Log.w(TAG, "skipping " + webhookType + " — slave not yet paired (missing prefs)");
                return null;
            }
            long ts = System.currentTimeMillis();
            String payload = meshId + "|" + nodeId + "|" + webhookType + "|" + ts;

            byte[] privBytes = Base64.decode(privB64, Base64.NO_WRAP);
            PrivateKey priv = KeyFactory.getInstance("RSA")
                .generatePrivate(new PKCS8EncodedKeySpec(privBytes));
            Signature sig = Signature.getInstance("SHA256withRSA");
            sig.initSign(priv);
            sig.update(payload.getBytes("UTF-8"));
            String signature = Base64.encodeToString(sig.sign(), Base64.NO_WRAP);

            if (body == null) body = new JSONObject();
            body.put("mesh_id", meshId);
            body.put("node_id", nodeId);
            body.put("ts", ts);
            body.put("signature", signature);
            return body.toString();
        } catch (Exception e) {
            Log.w(TAG, "sign failed for " + webhookType + ": " + e.getMessage());
            return null;
        }
    }
}

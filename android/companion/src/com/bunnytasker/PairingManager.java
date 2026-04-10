package com.bunnytasker;

import android.content.ContentResolver;
import android.provider.Settings;
import android.util.Base64;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.Signature;

/**
 * Manages RSA keypair for Lion-Bunny pairing.
 * Keys are stored in Settings.Global so they survive app data clears.
 */
public class PairingManager {

    /** Generate a new RSA 2048 keypair and store it. Returns the public key base64. */
    public static String generateKeypair(ContentResolver cr) {
        try {
            KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
            kpg.initialize(2048);
            KeyPair kp = kpg.generateKeyPair();

            String pubB64 = Base64.encodeToString(kp.getPublic().getEncoded(), Base64.NO_WRAP);
            String privB64 = Base64.encodeToString(kp.getPrivate().getEncoded(), Base64.NO_WRAP);

            Settings.Global.putString(cr, "focus_lock_bunny_pubkey", pubB64);
            Settings.Global.putString(cr, "focus_lock_bunny_privkey", privB64);

            return pubB64;
        } catch (Exception e) {
            return null;
        }
    }

    /** Get the stored public key, or generate one if missing. */
    public static String getPublicKey(ContentResolver cr) {
        String pub = Settings.Global.getString(cr, "focus_lock_bunny_pubkey");
        if (pub == null || pub.isEmpty() || "null".equals(pub)) {
            return generateKeypair(cr);
        }
        return pub;
    }

    /** Check if paired with a Lion (controller public key is stored). */
    public static boolean isPaired(ContentResolver cr) {
        String lionKey = Settings.Global.getString(cr, "focus_lock_lion_pubkey");
        return lionKey != null && !lionKey.isEmpty() && !"null".equals(lionKey);
    }

    /** Store the Lion's public key (called after QR scan pairing). */
    public static void storeLionKey(ContentResolver cr, String lionPubKeyB64) {
        Settings.Global.putString(cr, "focus_lock_lion_pubkey", lionPubKeyB64);
    }

    /** Get the Lion's stored public key. */
    public static String getLionKey(ContentResolver cr) {
        String k = Settings.Global.getString(cr, "focus_lock_lion_pubkey");
        return (k == null || "null".equals(k)) ? "" : k;
    }

    /** Sign a message with the bunny's private key. */
    public static String sign(ContentResolver cr, String message) {
        try {
            String privB64 = Settings.Global.getString(cr, "focus_lock_bunny_privkey");
            if (privB64 == null || privB64.isEmpty()) return "";
            byte[] privBytes = Base64.decode(privB64, Base64.NO_WRAP);
            java.security.spec.PKCS8EncodedKeySpec spec =
                new java.security.spec.PKCS8EncodedKeySpec(privBytes);
            PrivateKey priv = java.security.KeyFactory.getInstance("RSA").generatePrivate(spec);
            Signature sig = Signature.getInstance("SHA256withRSA");
            sig.initSign(priv);
            sig.update(message.getBytes("UTF-8"));
            return Base64.encodeToString(sig.sign(), Base64.NO_WRAP);
        } catch (Exception e) {
            return "";
        }
    }

    /** Verify a signature from the Lion's public key. */
    public static boolean verify(ContentResolver cr, String message, String signatureB64) {
        try {
            String pubB64 = getLionKey(cr);
            if (pubB64.isEmpty()) return false;
            byte[] pubBytes = Base64.decode(pubB64, Base64.NO_WRAP);
            java.security.spec.X509EncodedKeySpec spec =
                new java.security.spec.X509EncodedKeySpec(pubBytes);
            PublicKey pub = java.security.KeyFactory.getInstance("RSA").generatePublic(spec);
            Signature sig = Signature.getInstance("SHA256withRSA");
            sig.initVerify(pub);
            sig.update(message.getBytes("UTF-8"));
            return sig.verify(Base64.decode(signatureB64, Base64.NO_WRAP));
        } catch (Exception e) {
            return false;
        }
    }

    /** Get SHA-256 fingerprint of the public key (first 16 hex chars). */
    public static String getFingerprint(ContentResolver cr) {
        try {
            String pub = getPublicKey(cr);
            if (pub == null) return "?";
            byte[] hash = java.security.MessageDigest.getInstance("SHA-256")
                .digest(Base64.decode(pub, Base64.NO_WRAP));
            StringBuilder sb = new StringBuilder();
            for (int i = 0; i < 8; i++) sb.append(String.format("%02x", hash[i] & 0xFF));
            return sb.toString();
        } catch (Exception e) { return "?"; }
    }

    /** Build a short human-typeable pairing code. */
    public static String buildPairingCode(String lanIp, String tailscaleIp) {
        String ip = (tailscaleIp != null && !tailscaleIp.isEmpty()) ? tailscaleIp : lanIp;
        return ip + ":8432";
    }

    /** Build the QR payload — compact, just enough to connect. Full key exchange via HTTP. */
    public static String buildQrPayload(ContentResolver cr, String lanIp, String tailscaleIp) {
        String fingerprint = getFingerprint(cr);
        // Keep payload under 130 chars so QR version 6 handles it
        return "{\"t\":\"fl\"" +
            ",\"f\":\"" + fingerprint + "\"" +
            ",\"l\":\"" + lanIp + "\"" +
            ",\"s\":\"" + tailscaleIp + "\"" +
            ",\"p\":8432}";
    }
}

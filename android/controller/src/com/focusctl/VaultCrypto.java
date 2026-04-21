package com.focusctl;

import android.util.Base64;
import java.security.KeyFactory;
import java.security.MessageDigest;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.SecureRandom;
import java.security.Signature;
import java.security.spec.MGF1ParameterSpec;
import java.security.spec.PKCS8EncodedKeySpec;
import java.security.spec.X509EncodedKeySpec;
import java.util.ArrayList;
import java.util.Iterator;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import javax.crypto.spec.OAEPParameterSpec;
import javax.crypto.spec.PSource;

/**
 * Vault crypto helper — implements the multi-recipient blob format
 * from docs/VAULT-DESIGN.md.
 *
 * - Body encrypted with AES-256-GCM
 * - AES key wrapped per recipient with RSA-OAEP-SHA256
 * - Blob signed by Lion with RSA-PKCS1v15-SHA256 over canonical JSON
 *
 * canonicalJson() must produce byte-for-byte identical output to
 * Python's json.dumps(value, sort_keys=True, separators=(",",":"),
 * ensure_ascii=True) so signatures verify cross-platform.
 */
public class VaultCrypto {

    public static class NodePubkey {
        public final String nodeId;
        public final String pubkeyB64;  // X.509 SubjectPublicKeyInfo, base64 (PEM headers OK)
        public NodePubkey(String nodeId, String pubkeyB64) {
            this.nodeId = nodeId;
            this.pubkeyB64 = pubkeyB64;
        }
    }

    /**
     * Build an encrypted vault blob from an orders Map.
     * The returned Map is suitable for canonical-JSON serialization and signing.
     * Caller must invoke signBlob() and put the resulting "signature" field on the blob.
     */
    public static Map<String, Object> encryptOrders(
        String meshId, int version, long createdAt,
        Map<String, Object> orders,
        List<NodePubkey> recipients
    ) throws Exception {
        // 1. Serialize orders deterministically
        byte[] plaintext = canonicalJson(orders);

        // 2. Generate AES-256 key + 12-byte IV
        KeyGenerator kg = KeyGenerator.getInstance("AES");
        kg.init(256);
        SecretKey aesKey = kg.generateKey();
        byte[] iv = new byte[12];
        new SecureRandom().nextBytes(iv);

        // 3. Encrypt body with AES-256-GCM
        Cipher aes = Cipher.getInstance("AES/GCM/NoPadding");
        aes.init(Cipher.ENCRYPT_MODE, aesKey, new GCMParameterSpec(128, iv));
        byte[] ciphertext = aes.doFinal(plaintext);

        // 4. Wrap AES key for each recipient with RSA-OAEP-SHA256
        Map<String, Object> slots = new TreeMap<>();
        for (NodePubkey r : recipients) {
            byte[] pubkeyDer = Base64.decode(stripPemHeaders(r.pubkeyB64), Base64.DEFAULT);
            PublicKey pk = KeyFactory.getInstance("RSA")
                .generatePublic(new X509EncodedKeySpec(pubkeyDer));

            // MGF1 uses SHA-1 (not SHA-256) so AndroidKeyStore-backed recipient
            // keys can decrypt the result — see landmine #1. Main OAEP hash
            // stays at SHA-256. Software recipients work with either.
            Cipher rsa = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
            rsa.init(Cipher.ENCRYPT_MODE, pk, new OAEPParameterSpec(
                "SHA-256", "MGF1",
                MGF1ParameterSpec.SHA1,
                PSource.PSpecified.DEFAULT));
            byte[] wrappedKey = rsa.doFinal(aesKey.getEncoded());

            String slotId = sha256Hex(pubkeyDer).substring(0, 12);
            Map<String, Object> slot = new TreeMap<>();
            slot.put("encrypted_key", Base64.encodeToString(wrappedKey, Base64.NO_WRAP));
            slot.put("iv", Base64.encodeToString(iv, Base64.NO_WRAP));
            slots.put(slotId, slot);
        }

        // 5. Build blob (signature added by signBlob() caller)
        Map<String, Object> blob = new TreeMap<>();
        blob.put("mesh_id", meshId);
        blob.put("version", version);
        blob.put("created_at", createdAt);
        blob.put("slots", slots);
        blob.put("ciphertext", Base64.encodeToString(ciphertext, Base64.NO_WRAP));
        return blob;
    }

    /**
     * Roadmap #6: sign an arbitrary string payload with Lion's privkey.
     * RSA-PKCS1v15-SHA256 over the literal UTF-8 bytes — matches the
     * pipe-separated payload format the /api/mesh/{id}/messages/{send,fetch,mark}
     * endpoints expect (and the same scheme bunny uses via PairingManager.sign).
     */
    public static String signString(String message, String lionPrivKeyB64) throws Exception {
        byte[] privDer = Base64.decode(stripPemHeaders(lionPrivKeyB64), Base64.DEFAULT);
        PrivateKey pk = KeyFactory.getInstance("RSA")
            .generatePrivate(new PKCS8EncodedKeySpec(privDer));
        Signature sig = Signature.getInstance("SHA256withRSA");
        sig.initSign(pk);
        sig.update(message.getBytes("UTF-8"));
        return Base64.encodeToString(sig.sign(), Base64.NO_WRAP);
    }

    /**
     * Sign canonical_json(blob_minus_signature) with Lion's privkey.
     * Uses RSA-PKCS1v15-SHA256 to match Python's verify_signature.
     */
    public static String signBlob(Map<String, Object> blob, String lionPrivKeyB64) throws Exception {
        TreeMap<String, Object> signed = new TreeMap<>(blob);
        signed.remove("signature");
        byte[] data = canonicalJson(signed);

        byte[] privDer = Base64.decode(stripPemHeaders(lionPrivKeyB64), Base64.DEFAULT);
        PrivateKey pk = KeyFactory.getInstance("RSA")
            .generatePrivate(new PKCS8EncodedKeySpec(privDer));

        Signature sig = Signature.getInstance("SHA256withRSA");
        sig.initSign(pk);
        sig.update(data);
        return Base64.encodeToString(sig.sign(), Base64.NO_WRAP);
    }

    // ── Phase D: read side (vault poll loop) ──

    /**
     * Verify an RSA-PKCS1v15-SHA256 signature on a vault blob.
     * The signature covers canonical_json(blob_minus_signature) and was produced
     * by either Lion (order blobs) or a slave (runtime blobs).
     */
    public static boolean verifySignature(Map<String, Object> blob, String pubKeyB64) {
        Object sigObj = blob.get("signature");
        if (!(sigObj instanceof String) || ((String) sigObj).isEmpty()) return false;
        try {
            byte[] pubDer = Base64.decode(stripPemHeaders(pubKeyB64), Base64.DEFAULT);
            PublicKey pk = KeyFactory.getInstance("RSA")
                .generatePublic(new X509EncodedKeySpec(pubDer));

            TreeMap<String, Object> signed = new TreeMap<>(blob);
            signed.remove("signature");
            byte[] data = canonicalJson(signed);

            Signature sig = Signature.getInstance("SHA256withRSA");
            sig.initVerify(pk);
            sig.update(data);
            return sig.verify(Base64.decode((String) sigObj, Base64.DEFAULT));
        } catch (Exception e) {
            android.util.Log.w("VaultCrypto", "verify failed: " + e.getMessage());
            return false;
        }
    }

    /**
     * Decrypt the body of a vault blob using a recipient's RSA privkey.
     * The recipient's pubkey DER bytes determine which slot to read.
     * Returns the decrypted body JSON string, or null on failure (no slot,
     * key mismatch, malformed blob, etc.).
     */
    public static String decryptBody(Map<String, Object> blob, String myPrivKeyB64, byte[] myPubKeyDer) {
        try {
            String mySlotId = sha256Hex(myPubKeyDer).substring(0, 12);
            Object slotsObj = blob.get("slots");
            if (!(slotsObj instanceof Map)) return null;
            Map<String, Object> slots = (Map<String, Object>) slotsObj;
            Object slotObj = slots.get(mySlotId);
            if (!(slotObj instanceof Map)) return null;
            Map<String, Object> slot = (Map<String, Object>) slotObj;
            String ekB64 = (String) slot.get("encrypted_key");
            String ivB64 = (String) slot.get("iv");
            String ctB64 = (String) blob.get("ciphertext");
            if (ekB64 == null || ivB64 == null || ctB64 == null) return null;

            byte[] privDer = Base64.decode(stripPemHeaders(myPrivKeyB64), Base64.DEFAULT);
            PrivateKey pk = KeyFactory.getInstance("RSA")
                .generatePrivate(new PKCS8EncodedKeySpec(privDer));

            // Try MGF1-SHA1 first (v57+ protocol). Fall back to MGF1-SHA256
            // for blobs from pre-v57 peers during the transition. Remove once
            // all peers have upgraded.
            byte[] ekBytes = Base64.decode(ekB64, Base64.DEFAULT);
            byte[] aesKeyBytes;
            try {
                Cipher rsa = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
                rsa.init(Cipher.DECRYPT_MODE, pk, new OAEPParameterSpec(
                    "SHA-256", "MGF1",
                    MGF1ParameterSpec.SHA1,
                    PSource.PSpecified.DEFAULT));
                aesKeyBytes = rsa.doFinal(ekBytes);
            } catch (Exception sha1Err) {
                Cipher rsa = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
                rsa.init(Cipher.DECRYPT_MODE, pk, new OAEPParameterSpec(
                    "SHA-256", "MGF1",
                    MGF1ParameterSpec.SHA256,
                    PSource.PSpecified.DEFAULT));
                aesKeyBytes = rsa.doFinal(ekBytes);
            }

            byte[] iv = Base64.decode(ivB64, Base64.DEFAULT);
            byte[] ciphertext = Base64.decode(ctB64, Base64.DEFAULT);
            Cipher aes = Cipher.getInstance("AES/GCM/NoPadding");
            aes.init(Cipher.DECRYPT_MODE,
                new javax.crypto.spec.SecretKeySpec(aesKeyBytes, "AES"),
                new GCMParameterSpec(128, iv));
            byte[] plaintext = aes.doFinal(ciphertext);
            return new String(plaintext, "UTF-8");
        } catch (Exception e) {
            android.util.Log.w("VaultCrypto", "decrypt failed: " + e.getMessage());
            return null;
        }
    }

    /** Compute the slot id (first 12 hex chars of sha256) for a recipient's pubkey DER. */
    public static String slotIdForPubkey(byte[] pubKeyDer) {
        try {
            return sha256Hex(pubKeyDer).substring(0, 12);
        } catch (Exception e) {
            return "";
        }
    }

    /** SHA256 hex of canonical_json(body) — caller-side dedup helper. */
    public static String bodyHash(Map<String, Object> body) {
        try {
            return sha256Hex(canonicalJson(body));
        } catch (Exception e) {
            return "";
        }
    }

    /**
     * Recursively convert an org.json.JSONObject tree into a plain Map / List
     * tree with native Java types preserved. Used to feed verifySignature
     * and decryptBody (both expect Map<String,Object>, not JSONObject).
     */
    public static Map<String, Object> jsonToMap(org.json.JSONObject obj) throws Exception {
        TreeMap<String, Object> map = new TreeMap<>();
        Iterator<String> keys = obj.keys();
        while (keys.hasNext()) {
            String k = keys.next();
            Object v = obj.get(k);
            if (v == org.json.JSONObject.NULL) v = null;
            else if (v instanceof org.json.JSONObject) v = jsonToMap((org.json.JSONObject) v);
            else if (v instanceof org.json.JSONArray) v = jsonToList((org.json.JSONArray) v);
            map.put(k, v);
        }
        return map;
    }

    public static List<Object> jsonToList(org.json.JSONArray arr) throws Exception {
        ArrayList<Object> list = new ArrayList<>();
        for (int i = 0; i < arr.length(); i++) {
            Object v = arr.get(i);
            if (v == org.json.JSONObject.NULL) v = null;
            else if (v instanceof org.json.JSONObject) v = jsonToMap((org.json.JSONObject) v);
            else if (v instanceof org.json.JSONArray) v = jsonToList((org.json.JSONArray) v);
            list.add(v);
        }
        return list;
    }

    // ── Canonical JSON ──

    /**
     * Serialize an Object to bytes matching Python's
     * json.dumps(value, sort_keys=True, separators=(",",":"), ensure_ascii=True).
     *
     * Supported types: String, Integer, Long, Boolean, null, Map, List.
     */
    public static byte[] canonicalJson(Object value) {
        StringBuilder sb = new StringBuilder();
        canonicalSerialize(value, sb);
        try {
            // Output is pure ASCII (ensure_ascii=true semantics) so UTF-8 == ASCII bytes.
            return sb.toString().getBytes("UTF-8");
        } catch (Exception e) {
            return sb.toString().getBytes();
        }
    }

    private static void canonicalSerialize(Object value, StringBuilder sb) {
        if (value == null) {
            sb.append("null");
        } else if (value instanceof Boolean) {
            sb.append(((Boolean) value) ? "true" : "false");
        } else if (value instanceof Integer || value instanceof Long) {
            sb.append(value.toString());
        } else if (value instanceof Double || value instanceof Float) {
            sb.append(value.toString());
        } else if (value instanceof String) {
            escapeJsonString((String) value, sb);
        } else if (value instanceof Map) {
            // Always sort keys lexicographically
            TreeMap<String, Object> sorted;
            if (value instanceof TreeMap) {
                sorted = (TreeMap<String, Object>) value;
            } else {
                sorted = new TreeMap<>();
                for (Object kObj : ((Map<?, ?>) value).keySet()) {
                    sorted.put(kObj.toString(), ((Map<?, ?>) value).get(kObj));
                }
            }
            sb.append("{");
            boolean first = true;
            for (Map.Entry<String, Object> e : sorted.entrySet()) {
                if (!first) sb.append(",");
                escapeJsonString(e.getKey(), sb);
                sb.append(":");
                canonicalSerialize(e.getValue(), sb);
                first = false;
            }
            sb.append("}");
        } else if (value instanceof List) {
            sb.append("[");
            boolean first = true;
            for (Object item : (List<?>) value) {
                if (!first) sb.append(",");
                canonicalSerialize(item, sb);
                first = false;
            }
            sb.append("]");
        } else {
            // Fallback: stringify (and JSON-escape) anything else
            escapeJsonString(value.toString(), sb);
        }
    }

    /**
     * JSON-escape a string and wrap it in double quotes.
     * Matches Python's json.dumps(ensure_ascii=True): all chars
     * outside [0x20..0x7e] become a 6-char escape sequence,
     * with named escapes for the standard control chars.
     */
    private static void escapeJsonString(String s, StringBuilder sb) {
        sb.append('"');
        int len = s.length();
        for (int i = 0; i < len; i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': sb.append("\\\\"); break;
                case '"':  sb.append("\\\""); break;
                case '\b': sb.append("\\b"); break;
                case '\f': sb.append("\\f"); break;
                case '\n': sb.append("\\n"); break;
                case '\r': sb.append("\\r"); break;
                case '\t': sb.append("\\t"); break;
                default:
                    if (c < 0x20 || c > 0x7e) {
                        sb.append(String.format("\\u%04x", (int) c));
                    } else {
                        sb.append(c);
                    }
            }
        }
        sb.append('"');
    }

    // ── Helpers ──

    private static String sha256Hex(byte[] data) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        byte[] digest = md.digest(data);
        StringBuilder sb = new StringBuilder(digest.length * 2);
        for (byte b : digest) {
            sb.append(String.format("%02x", b));
        }
        return sb.toString();
    }

    private static String stripPemHeaders(String key) {
        if (key == null) return "";
        return key
            .replace("-----BEGIN PUBLIC KEY-----", "")
            .replace("-----END PUBLIC KEY-----", "")
            .replace("-----BEGIN PRIVATE KEY-----", "")
            .replace("-----END PRIVATE KEY-----", "")
            .replace("-----BEGIN RSA PRIVATE KEY-----", "")
            .replace("-----END RSA PRIVATE KEY-----", "")
            .replaceAll("[\\s|]+", "");
    }

    // ── Audit C1: direct-HTTP signed-POST helpers ──
    //
    // Canonical payload byte-for-byte mirrors the slave's SigVerifier.canonicalize
    // (android/slave/.../SigVerifier.java) and the Python parity test in
    // tests/test_http.py. Any drift → slave will reject with bad_sig.

    public static String canonicalizeDirectPost(String path, String body, long ts, String nonce) {
        StringBuilder params = new StringBuilder();
        boolean jsonOk = body != null && !body.isEmpty() && body.trim().startsWith("{");
        if (jsonOk) {
            try {
                org.json.JSONObject obj = new org.json.JSONObject(body);
                TreeMap<String, String> sorted = new TreeMap<>();
                Iterator<String> keys = obj.keys();
                while (keys.hasNext()) {
                    String k = keys.next();
                    Object v = obj.opt(k);
                    if (v == null || org.json.JSONObject.NULL.equals(v)) continue;
                    sorted.put(k, encodeDirectValue(v));
                }
                boolean first = true;
                for (Map.Entry<String, String> e : sorted.entrySet()) {
                    if (!first) params.append('&');
                    params.append(urlEncDirect(e.getKey())).append('=').append(e.getValue());
                    first = false;
                }
            } catch (Exception e) { jsonOk = false; }
        }
        if (!jsonOk) {
            params.setLength(0);
            params.append("_raw=").append(urlEncDirect(body == null ? "" : body));
        }
        return "focusctl|" + path + "|" + ts + "|" + nonce + "|" + params;
    }

    private static String encodeDirectValue(Object v) {
        if (v instanceof Boolean) return ((Boolean) v) ? "1" : "0";
        if (v instanceof Integer || v instanceof Long) return v.toString();
        if (v instanceof Number) {
            double d = ((Number) v).doubleValue();
            if (!Double.isNaN(d) && !Double.isInfinite(d)
                    && d == Math.floor(d) && Math.abs(d) < 1e15) {
                return Long.toString((long) d);
            }
            return v.toString();
        }
        return urlEncDirect(v.toString());
    }

    private static String urlEncDirect(String s) {
        try {
            return java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20");
        } catch (Exception e) { return s; }
    }

    /** 16-byte base64-url nonce. */
    public static String randomNonce() {
        byte[] buf = new byte[16];
        new SecureRandom().nextBytes(buf);
        return Base64.encodeToString(buf, Base64.NO_WRAP | Base64.URL_SAFE | Base64.NO_PADDING);
    }
}

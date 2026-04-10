package com.focuslock;

import android.util.Base64;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
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
 * Slave-side vault crypto helper. Phase D: now also encrypts+signs runtime
 * blobs (the slave is a vault writer for runtime state). See docs/VAULT-DESIGN.md.
 *
 * canonicalJson() must produce byte-for-byte identical output to Python's
 * json.dumps(value, sort_keys=True, separators=(",",":"), ensure_ascii=True)
 * so that signatures verify against blobs encoded by either Lion's Share
 * (Java) or focuslock-mail.py (Python).
 */
public class VaultCrypto {

    /** Recipient pubkey for multi-recipient encryption (mirrors controller VaultCrypto). */
    public static class NodePubkey {
        public final String nodeId;
        public final String pubkeyB64;
        public NodePubkey(String nodeId, String pubkeyB64) {
            this.nodeId = nodeId;
            this.pubkeyB64 = pubkeyB64;
        }
    }

    /**
     * Phase D: build an encrypted vault blob from a runtime body Map. Mirrors
     * the controller's encryptOrders() — generic name because the slave writes
     * runtime state, not order ledgers. Caller invokes signBlob() afterwards.
     */
    public static Map<String, Object> encryptBody(
        String meshId, long version, long createdAt,
        Map<String, Object> body,
        List<NodePubkey> recipients
    ) throws Exception {
        byte[] plaintext = canonicalJson(body);

        KeyGenerator kg = KeyGenerator.getInstance("AES");
        kg.init(256);
        SecretKey aesKey = kg.generateKey();
        byte[] iv = new byte[12];
        new SecureRandom().nextBytes(iv);

        Cipher aes = Cipher.getInstance("AES/GCM/NoPadding");
        aes.init(Cipher.ENCRYPT_MODE, aesKey, new GCMParameterSpec(128, iv));
        byte[] ciphertext = aes.doFinal(plaintext);

        TreeMap<String, Object> slots = new TreeMap<>();
        for (NodePubkey r : recipients) {
            byte[] pubkeyDer = Base64.decode(stripPemHeaders(r.pubkeyB64), Base64.DEFAULT);
            PublicKey pk = KeyFactory.getInstance("RSA")
                .generatePublic(new X509EncodedKeySpec(pubkeyDer));

            Cipher rsa = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
            rsa.init(Cipher.ENCRYPT_MODE, pk, new OAEPParameterSpec(
                "SHA-256", "MGF1",
                MGF1ParameterSpec.SHA256,
                PSource.PSpecified.DEFAULT));
            byte[] wrappedKey = rsa.doFinal(aesKey.getEncoded());

            String slotId = sha256Hex(pubkeyDer).substring(0, 12);
            TreeMap<String, Object> slot = new TreeMap<>();
            slot.put("encrypted_key", Base64.encodeToString(wrappedKey, Base64.NO_WRAP));
            slot.put("iv", Base64.encodeToString(iv, Base64.NO_WRAP));
            slots.put(slotId, slot);
        }

        TreeMap<String, Object> blob = new TreeMap<>();
        blob.put("mesh_id", meshId);
        blob.put("version", version);
        blob.put("created_at", createdAt);
        blob.put("slots", slots);
        blob.put("ciphertext", Base64.encodeToString(ciphertext, Base64.NO_WRAP));
        return blob;
    }

    /** Sign canonical_json(blob_minus_signature) with the slave's privkey. */
    public static String signBlob(Map<String, Object> blob, String privKeyB64) throws Exception {
        TreeMap<String, Object> signed = new TreeMap<>(blob);
        signed.remove("signature");
        byte[] data = canonicalJson(signed);

        byte[] privDer = Base64.decode(stripPemHeaders(privKeyB64), Base64.DEFAULT);
        PrivateKey pk = KeyFactory.getInstance("RSA")
            .generatePrivate(new PKCS8EncodedKeySpec(privDer));

        Signature sig = Signature.getInstance("SHA256withRSA");
        sig.initSign(pk);
        sig.update(data);
        return Base64.encodeToString(sig.sign(), Base64.NO_WRAP);
    }

    /** SHA256 hex of canonical_json(body) — used by callers for dedup. */
    public static String bodyHash(Map<String, Object> body) {
        try {
            return sha256Hex(canonicalJson(body));
        } catch (Exception e) {
            return "";
        }
    }

    /**
     * Verify the Lion's RSA-PKCS1v15-SHA256 signature on a blob.
     * The signature covers canonical_json(blob) with the "signature" field removed.
     */
    public static boolean verifySignature(Map<String, Object> blob, String lionPubKeyB64) {
        Object sigObj = blob.get("signature");
        if (!(sigObj instanceof String) || ((String) sigObj).isEmpty()) return false;
        try {
            byte[] pubDer = Base64.decode(stripPemHeaders(lionPubKeyB64), Base64.DEFAULT);
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
     * Decrypt a vault blob's body using the slave's RSA privkey.
     * Returns the decrypted orders JSON string, or null on failure.
     *
     * Expected blob structure: { slots: { slotId: {encrypted_key, iv} }, ciphertext }
     */
    public static String decryptOrders(Map<String, Object> blob, String myPrivKeyB64, byte[] myPubKeyDer) {
        try {
            String mySlotId = sha256Hex(myPubKeyDer).substring(0, 12);
            Object slotsObj = blob.get("slots");
            if (!(slotsObj instanceof Map)) return null;
            Map<String, Object> slots = (Map<String, Object>) slotsObj;
            Object slotObj = slots.get(mySlotId);
            if (!(slotObj instanceof Map)) {
                android.util.Log.i("VaultCrypto", "no slot for our pubkey (slot=" + mySlotId + ")");
                return null;
            }
            Map<String, Object> slot = (Map<String, Object>) slotObj;
            String ekB64 = (String) slot.get("encrypted_key");
            String ivB64 = (String) slot.get("iv");
            String ctB64 = (String) blob.get("ciphertext");
            if (ekB64 == null || ivB64 == null || ctB64 == null) return null;

            byte[] privDer = Base64.decode(stripPemHeaders(myPrivKeyB64), Base64.DEFAULT);
            PrivateKey pk = KeyFactory.getInstance("RSA")
                .generatePrivate(new PKCS8EncodedKeySpec(privDer));

            Cipher rsa = Cipher.getInstance("RSA/ECB/OAEPWithSHA-256AndMGF1Padding");
            rsa.init(Cipher.DECRYPT_MODE, pk, new OAEPParameterSpec(
                "SHA-256", "MGF1",
                MGF1ParameterSpec.SHA256,
                PSource.PSpecified.DEFAULT));
            byte[] aesKeyBytes = rsa.doFinal(Base64.decode(ekB64, Base64.DEFAULT));

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

    /**
     * Compute the slot id for a given pubkey (DER bytes).
     * Returns the lowercase hex SHA256 truncated to 12 chars.
     */
    public static String slotIdForPubkey(byte[] pubKeyDer) {
        try {
            return sha256Hex(pubKeyDer).substring(0, 12);
        } catch (Exception e) {
            return "";
        }
    }

    /**
     * Recursively convert an org.json.JSONObject / JSONArray tree
     * into a plain Map / List tree with native Java types preserved.
     * Used to feed verify/decrypt and the canonical-JSON serializer.
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

    // ── Canonical JSON (mirrors Python json.dumps sort_keys, no whitespace, ensure_ascii) ──

    public static byte[] canonicalJson(Object value) {
        StringBuilder sb = new StringBuilder();
        canonicalSerialize(value, sb);
        try {
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
            escapeJsonString(value.toString(), sb);
        }
    }

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
}

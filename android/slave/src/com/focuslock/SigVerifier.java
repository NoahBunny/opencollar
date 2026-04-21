package com.focuslock;

import android.util.Base64;
import android.util.Log;

import org.json.JSONObject;

import java.net.URLEncoder;
import java.security.KeyFactory;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import java.util.Iterator;
import java.util.LinkedHashMap;
import java.util.Map;
import java.util.TreeMap;

/**
 * Audit C1 — RSA-SHA256 verification on Collar's local HTTP POSTs.
 *
 * Canonical payload (must match Lion's Share controller + Bunny Tasker
 * byte-for-byte — Python parity test in tests/test_http.py):
 *
 *   focusctl|&lt;path&gt;|&lt;ts&gt;|&lt;nonce&gt;|&lt;k1=v1&amp;k2=v2&amp;...&gt;
 *
 * Params: keys lex-sorted, values form-urlencoded (%20 for space, not '+'),
 * booleans as 0/1, integral numbers as decimal, JSON null / missing omitted,
 * nested objects/arrays sign as their raw .toString() (flat bodies are the
 * common case). Empty/non-JSON body → single synthetic "_raw=&lt;urlenc body&gt;"
 * param so tampering still trips verification.
 *
 * Headers: X-FL-Ts (millis), X-FL-Nonce (base64-url ≥ 8 chars), X-FL-Sig
 * (base64 RSA-PKCS1v15-SHA256). Verifier tries lion pubkey first; same-phone
 * Bunny Tasker signs with its own bunny key, so fallback to bunny pubkey.
 */
public class SigVerifier {

    private static final String TAG = "FocusLockSig";
    private static final String PROTOCOL_TAG = "focusctl";
    private static final long TS_WINDOW_MS = 300_000L;

    public enum Result { ACCEPT, SIG_REQUIRED, BAD_SIG, STALE_TS, REPLAY, NOT_PAIRED, MALFORMED }

    public static class NonceCache {
        private static final int MAX = 4096;
        private static final long TTL_MS = 600_000L;
        private final LinkedHashMap<String, Long> map =
            new LinkedHashMap<String, Long>(64, 0.75f, false) {
                @Override
                protected boolean removeEldestEntry(Map.Entry<String, Long> eldest) {
                    return size() > MAX;
                }
            };

        public synchronized boolean seenOrRecord(String key, long now) {
            Iterator<Map.Entry<String, Long>> it = map.entrySet().iterator();
            while (it.hasNext()) {
                Map.Entry<String, Long> e = it.next();
                if (now - e.getValue() > TTL_MS) it.remove();
                else break;
            }
            if (map.containsKey(key)) return true;
            map.put(key, now);
            return false;
        }

        public synchronized int size() { return map.size(); }
    }

    public static String canonicalize(String path, String body, long ts, String nonce) {
        StringBuilder params = new StringBuilder();
        boolean jsonOk = body != null && !body.isEmpty() && body.trim().startsWith("{");
        if (jsonOk) {
            try {
                JSONObject obj = new JSONObject(body);
                TreeMap<String, String> sorted = new TreeMap<>();
                Iterator<String> keys = obj.keys();
                while (keys.hasNext()) {
                    String k = keys.next();
                    Object v = obj.opt(k);
                    if (v == null || JSONObject.NULL.equals(v)) continue;
                    sorted.put(k, encodeValue(v));
                }
                boolean first = true;
                for (Map.Entry<String, String> e : sorted.entrySet()) {
                    if (!first) params.append('&');
                    params.append(urlEncode(e.getKey())).append('=').append(e.getValue());
                    first = false;
                }
            } catch (Exception e) {
                jsonOk = false;
            }
        }
        if (!jsonOk) {
            params.setLength(0);
            params.append("_raw=").append(urlEncode(body == null ? "" : body));
        }
        return PROTOCOL_TAG + "|" + path + "|" + ts + "|" + nonce + "|" + params;
    }

    private static String encodeValue(Object v) {
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
        return urlEncode(v.toString());
    }

    private static String urlEncode(String s) {
        try {
            return URLEncoder.encode(s, "UTF-8").replace("+", "%20");
        } catch (Exception e) {
            return s;
        }
    }

    public static Result verify(
            String lionPubKeyB64, String bunnyPubKeyB64,
            String path, String body,
            String tsHeader, String nonceHeader, String sigHeader,
            NonceCache cache, long now) {

        boolean haveLion  = lionPubKeyB64  != null && !lionPubKeyB64.isEmpty();
        boolean haveBunny = bunnyPubKeyB64 != null && !bunnyPubKeyB64.isEmpty();
        if (!haveLion && !haveBunny) return Result.NOT_PAIRED;

        if (sigHeader   == null || sigHeader.isEmpty()
         || tsHeader    == null || tsHeader.isEmpty()
         || nonceHeader == null || nonceHeader.isEmpty()) {
            return Result.SIG_REQUIRED;
        }

        long ts;
        try { ts = Long.parseLong(tsHeader.trim()); }
        catch (Exception e) { return Result.MALFORMED; }
        if (Math.abs(now - ts) > TS_WINDOW_MS) return Result.STALE_TS;

        if (nonceHeader.length() < 8 || nonceHeader.length() > 128) return Result.MALFORMED;

        byte[] sig;
        try { sig = Base64.decode(sigHeader, Base64.NO_WRAP); }
        catch (Exception e) { return Result.MALFORMED; }

        // Record nonce BEFORE verify. Two concurrent threads processing the same
        // replayed (ts,nonce) must not both pass — otherwise a non-idempotent
        // endpoint (add-paywall, gamble) gets double-applied. Nonces are 16-byte
        // random per-request so a legit signer collision is ~2⁻¹²⁸.
        String cacheKey = ts + "." + nonceHeader;
        if (cache.seenOrRecord(cacheKey, now)) return Result.REPLAY;

        String payload = canonicalize(path, body, ts, nonceHeader);
        boolean ok = (haveLion  && rsaVerify(lionPubKeyB64,  payload, sig))
                  || (haveBunny && rsaVerify(bunnyPubKeyB64, payload, sig));
        return ok ? Result.ACCEPT : Result.BAD_SIG;
    }

    private static boolean rsaVerify(String pubKeyB64, String payload, byte[] sig) {
        try {
            byte[] der = Base64.decode(stripPem(pubKeyB64), Base64.NO_WRAP);
            PublicKey pk = KeyFactory.getInstance("RSA")
                .generatePublic(new X509EncodedKeySpec(der));
            Signature s = Signature.getInstance("SHA256withRSA");
            s.initVerify(pk);
            s.update(payload.getBytes("UTF-8"));
            return s.verify(sig);
        } catch (Exception e) {
            Log.w(TAG, "rsa verify failed: " + e.getMessage());
            return false;
        }
    }

    private static String stripPem(String s) {
        if (s == null) return "";
        return s.replaceAll("-----BEGIN [A-Z ]+-----", "")
                .replaceAll("-----END [A-Z ]+-----", "")
                .replaceAll("\\s+", "");
    }
}

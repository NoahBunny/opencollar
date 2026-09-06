package com.focuslock;

import static com.focuslock.SigVerifier.Result.ACCEPT;
import static com.focuslock.SigVerifier.Result.BAD_SIG;
import static com.focuslock.SigVerifier.Result.MALFORMED;
import static com.focuslock.SigVerifier.Result.NOT_PAIRED;
import static com.focuslock.SigVerifier.Result.REPLAY;
import static com.focuslock.SigVerifier.Result.SIG_REQUIRED;
import static com.focuslock.SigVerifier.Result.STALE_TS;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import com.focuslock.SigVerifier.NonceCache;
import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Pure-JVM unit tests for the Collar's Audit-C1 SigVerifier — no device, no
 * android.jar (android.util.{Base64,Log} supplied by the test-support shims).
 *
 * Three concerns are pinned:
 *   (a) canonicalize() — the signed-payload format. The expected literals are
 *       copied verbatim from the Python reference golden vectors in
 *       tests/test_http.py (c1_canonicalize), so each assertEquals is a true
 *       cross-language parity assertion. If the Java and Python canonical forms
 *       ever drift, signatures silently stop verifying — these catch it.
 *   (b) verify() — all seven Result branches and their ordering subtleties.
 *   (c) NonceCache — replay detection, TTL expiry, and FIFO eviction.
 *
 * Shim caveat: the test-support android.util.Base64 decodes via java.util's
 * lenient MIME decoder, which ignores non-alphabet chars and only throws when
 * the valid-char count is ≡ 1 (mod 4). So the sig-MALFORMED branch is reachable
 * here only with a single-valid-char sig like "A"; a "visibly garbage" sig such
 * as "!!!" would decode to empty and fall through to BAD_SIG. On a real device
 * Android's stricter Base64 rejects more inputs, so the shim under-tests that
 * branch's on-device fidelity (documented, not a correctness gap in the test).
 */
public class SigVerifierTest {

    private static final long NOW = 1_700_000_000_000L;

    private static KeyPair LION;
    private static KeyPair BUNNY;
    private static KeyPair WRONG;

    @BeforeAll
    static void genKeys() throws Exception {
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048);
        LION = kpg.generateKeyPair();
        BUNNY = kpg.generateKeyPair();
        WRONG = kpg.generateKeyPair();
    }

    /** X.509 SubjectPublicKeyInfo, standard base64 (what the verifier PEM-strips + decodes). */
    private static String pubB64(KeyPair kp) {
        return java.util.Base64.getEncoder().encodeToString(kp.getPublic().getEncoded());
    }

    /** Sign the CANONICAL payload (not the raw body) with kp — the only way to mint a passing sig. */
    private static String signCanon(KeyPair kp, String path, String body, long ts, String nonce)
            throws Exception {
        String payload = SigVerifier.canonicalize(path, body, ts, nonce);
        Signature s = Signature.getInstance("SHA256withRSA");
        s.initSign(kp.getPrivate());
        s.update(payload.getBytes(StandardCharsets.UTF_8));
        return java.util.Base64.getEncoder().encodeToString(s.sign());
    }

    /** Happy-path helper: sign as kp, verify with lion=kp (bunny empty), fresh cache. */
    private static SigVerifier.Result acceptCase(
            KeyPair kp, String path, String body, long ts, String nonce, long now) throws Exception {
        String sig = signCanon(kp, path, body, ts, nonce);
        return SigVerifier.verify(
                pubB64(kp), "", path, body, String.valueOf(ts), nonce, sig, new NonceCache(), now);
    }

    // ──────────────── canonicalize() — golden-vector parity (tests/test_http.py) ────────────────

    @Test
    void canon_simpleLockBody() {
        assertEquals(
                "focusctl|/api/lock|1700000000000|abc12345|duration_min=60&mode=basic",
                SigVerifier.canonicalize(
                        "/api/lock", "{\"duration_min\":60,\"mode\":\"basic\"}", 1_700_000_000_000L, "abc12345"));
    }

    @Test
    void canon_emptyBodySignsRaw() {
        assertEquals(
                "focusctl|/api/unlock|1700000000000|abc12345|_raw=",
                SigVerifier.canonicalize("/api/unlock", "", 1_700_000_000_000L, "abc12345"));
    }

    @Test
    void canon_nonJsonBodySignsRaw() {
        assertEquals(
                "focusctl|/api/speak|111|nonce|_raw=hello%20world",
                SigVerifier.canonicalize("/api/speak", "hello world", 111L, "nonce"));
    }

    @Test
    void canon_keysSortedLexicographically() {
        assertEquals(
                "focusctl|/api/lock|0|n|apple=2&mango=3&zebra=1",
                SigVerifier.canonicalize("/api/lock", "{\"zebra\":1,\"apple\":2,\"mango\":3}", 0L, "n"));
    }

    @Test
    void canon_booleansBecome0or1() {
        assertEquals(
                "focusctl|/api/lock|0|n|shame=1&silent=0",
                SigVerifier.canonicalize("/api/lock", "{\"shame\":true,\"silent\":false}", 0L, "n"));
    }

    @Test
    void canon_integralFloatLosesDecimal() {
        // JSON "3.0" parses to a Double in Java (BigDecimal under org.json); both must emit "3".
        assertEquals(
                "focusctl|/api/set-volume|0|n|level=3",
                SigVerifier.canonicalize("/api/set-volume", "{\"level\":3.0}", 0L, "n"));
    }

    @Test
    void canon_nonIntegralFloatPreserved() {
        assertEquals(
                "focusctl|/api/set-geofence|0|n|lat=40.7128",
                SigVerifier.canonicalize("/api/set-geofence", "{\"lat\":40.7128}", 0L, "n"));
    }

    @Test
    void canon_nullValuesOmitted() {
        assertEquals(
                "focusctl|/api/lock|0|n|duration_min=60&mode=basic",
                SigVerifier.canonicalize(
                        "/api/lock", "{\"duration_min\":60,\"message\":null,\"mode\":\"basic\"}", 0L, "n"));
    }

    @Test
    void canon_specialCharsUrlEncoded() {
        assertEquals(
                "focusctl|/api/message|0|n|text=hi%20%26%20bye%20%7C%20done",
                SigVerifier.canonicalize("/api/message", "{\"text\":\"hi & bye | done\"}", 0L, "n"));
    }

    @Test
    void canon_unicodeValueUtf8Encoded() {
        // é (U+00E9) → UTF-8 c3 a9 → %C3%A9. Source is UTF-8 (same as VaultCryptoTest's raw
        // non-ASCII string literal), which the JDK-25 toolchain reads correctly.
        assertEquals(
                "focusctl|/api/message|0|n|text=h%C3%A9llo",
                SigVerifier.canonicalize("/api/message", "{\"text\":\"héllo\"}", 0L, "n"));
    }

    @Test
    void canon_pathIsNotEncoded() {
        assertEquals(
                "focusctl|/api/add-paywall|42|nn|amount=500",
                SigVerifier.canonicalize("/api/add-paywall", "{\"amount\":500}", 42L, "nn"));
    }

    @Test
    void canon_malformedJsonFallsBackToRaw() {
        // Body starts with '{' so the JSON path is attempted, throws, and falls back to _raw=.
        // Python parity holds: c1_canonicalize hits JSONDecodeError → identical _raw=%7Bbad.
        assertEquals(
                "focusctl|/x|0|n|_raw=%7Bbad", SigVerifier.canonicalize("/x", "{bad", 0L, "n"));
    }

    @Test
    void canon_nullBodyTreatedAsRaw() {
        assertEquals("focusctl|/x|0|n|_raw=", SigVerifier.canonicalize("/x", null, 0L, "n"));
    }

    @Test
    void canon_tamperedBodyProducesDifferentCanonical() {
        String a = SigVerifier.canonicalize("/api/lock", "{\"duration_min\":60}", 0L, "n");
        String b = SigVerifier.canonicalize("/api/lock", "{\"duration_min\":61}", 0L, "n");
        assertNotEquals(a, b);
        assertTrue(a.endsWith("=60"));
        assertTrue(b.endsWith("=61"));
    }

    // ──────────────── verify() — the seven Result branches + ordering ────────────────

    @Test
    void verify_notPaired_whenNoKeys() {
        // Both keys absent ⇒ NOT_PAIRED even with all headers present (precedes SIG_REQUIRED).
        assertEquals(
                NOT_PAIRED,
                SigVerifier.verify(
                        "", "", "/api/lock", "{}", String.valueOf(NOW), "abcd1234", "c2ln", new NonceCache(), NOW));
        assertEquals(
                NOT_PAIRED,
                SigVerifier.verify(null, null, "/api/lock", "{}", null, null, null, new NonceCache(), NOW));
    }

    @Test
    void verify_sigRequired_whenAnyHeaderMissing() {
        String lp = pubB64(LION);
        String ts = String.valueOf(NOW), nn = "abcd1234", sg = "c2ln";
        // SIG_REQUIRED returns before any nonce is recorded, so one shared cache is fine.
        NonceCache c = new NonceCache();
        assertEquals(SIG_REQUIRED, SigVerifier.verify(lp, "", "/p", "{}", ts, nn, null, c, NOW));
        assertEquals(SIG_REQUIRED, SigVerifier.verify(lp, "", "/p", "{}", ts, nn, "", c, NOW));
        assertEquals(SIG_REQUIRED, SigVerifier.verify(lp, "", "/p", "{}", null, nn, sg, c, NOW));
        assertEquals(SIG_REQUIRED, SigVerifier.verify(lp, "", "/p", "{}", "", nn, sg, c, NOW));
        assertEquals(SIG_REQUIRED, SigVerifier.verify(lp, "", "/p", "{}", ts, null, sg, c, NOW));
        assertEquals(SIG_REQUIRED, SigVerifier.verify(lp, "", "/p", "{}", ts, "", sg, c, NOW));
        assertEquals(0, c.size());
    }

    @Test
    void verify_malformed_whenTsNonNumeric() {
        String lp = pubB64(LION);
        for (String badTs : new String[] {"abc", "12.5", "99999999999999999999", "   "}) {
            assertEquals(
                    MALFORMED,
                    SigVerifier.verify(lp, "", "/p", "{}", badTs, "abcd1234", "c2ln", new NonceCache(), NOW),
                    "ts=" + badTs);
        }
    }

    @Test
    void verify_staleTs_strictBoundary() throws Exception {
        // |now - ts| == 300000 is in-window (uses strict >); a matching sig ⇒ ACCEPT.
        assertEquals(ACCEPT, acceptCase(LION, "/api/lock", "{}", NOW - 300_000L, "nonce123", NOW));
        assertEquals(ACCEPT, acceptCase(LION, "/api/lock", "{}", NOW + 300_000L, "nonce123", NOW));
        // |now - ts| == 300001 is stale — the signature is never evaluated.
        assertEquals(
                STALE_TS,
                SigVerifier.verify(
                        pubB64(LION), "", "/api/lock", "{}",
                        String.valueOf(NOW - 300_001L), "nonce123", "c2ln", new NonceCache(), NOW));
        assertEquals(
                STALE_TS,
                SigVerifier.verify(
                        pubB64(LION), "", "/api/lock", "{}",
                        String.valueOf(NOW + 300_001L), "nonce123", "c2ln", new NonceCache(), NOW));
    }

    @Test
    void verify_nonceLength_inclusiveBoundaries() throws Exception {
        String lp = pubB64(LION);
        // 7 and 129 are out of [8,128] ⇒ MALFORMED.
        assertEquals(
                MALFORMED,
                SigVerifier.verify(lp, "", "/p", "{}", String.valueOf(NOW), "nnnnnnn", "c2ln", new NonceCache(), NOW));
        assertEquals(
                MALFORMED,
                SigVerifier.verify(
                        lp, "", "/p", "{}", String.valueOf(NOW), "n".repeat(129), "c2ln", new NonceCache(), NOW));
        // 8 and 128 are inclusive bounds ⇒ ACCEPT with a matching signature.
        assertEquals(ACCEPT, acceptCase(LION, "/p", "{}", NOW, "nnnnnnnn", NOW));
        assertEquals(ACCEPT, acceptCase(LION, "/p", "{}", NOW, "n".repeat(128), NOW));
    }

    @Test
    void verify_malformed_whenSigNotBase64() {
        // "A" is exactly one valid base64 char → MIME decoder throws → MALFORMED.
        // (See the class javadoc: "!!!" would instead decode to empty and reach BAD_SIG.)
        assertEquals(
                MALFORMED,
                SigVerifier.verify(
                        pubB64(LION), "", "/p", "{}", String.valueOf(NOW), "abcd1234", "A", new NonceCache(), NOW));
    }

    @Test
    void verify_accept_validLionSignature() throws Exception {
        assertEquals(
                ACCEPT,
                acceptCase(LION, "/api/lock", "{\"duration_min\":60,\"mode\":\"basic\"}", NOW, "abcd1234", NOW));
    }

    @Test
    void verify_badSig_validBase64WrongSignature() {
        // 256 zero bytes: valid base64, decodes cleanly, fails RSA verification ⇒ BAD_SIG.
        String zeroSig = java.util.Base64.getEncoder().encodeToString(new byte[256]);
        assertEquals(
                BAD_SIG,
                SigVerifier.verify(
                        pubB64(LION), "", "/p", "{}", String.valueOf(NOW), "abcd1234", zeroSig, new NonceCache(), NOW));
    }

    @Test
    void verify_badSig_keyDecodesButIsNotX509() {
        // "bm90YWtleQ==" = base64("notakey"): valid base64 but KeyFactory rejects it,
        // so rsaVerify returns false ⇒ BAD_SIG (distinct from MALFORMED).
        assertEquals(
                BAD_SIG,
                SigVerifier.verify(
                        "bm90YWtleQ==", "", "/p", "{}", String.valueOf(NOW), "abcd1234", "c2ln", new NonceCache(), NOW));
    }

    @Test
    void verify_bunnyFallback_whenLionKeyEmpty() throws Exception {
        String sig = signCanon(BUNNY, "/p", "{}", NOW, "abcd1234");
        assertEquals(
                ACCEPT,
                SigVerifier.verify(
                        "", pubB64(BUNNY), "/p", "{}", String.valueOf(NOW), "abcd1234", sig, new NonceCache(), NOW));
    }

    @Test
    void verify_bunnyFallback_whenLionWrongBunnyRight() throws Exception {
        String sig = signCanon(BUNNY, "/p", "{}", NOW, "abcd1234");
        assertEquals(
                ACCEPT,
                SigVerifier.verify(
                        pubB64(WRONG), pubB64(BUNNY), "/p", "{}",
                        String.valueOf(NOW), "abcd1234", sig, new NonceCache(), NOW));
    }

    @Test
    void verify_lionPreferred_whenLionRightBunnyGarbage() throws Exception {
        String sig = signCanon(LION, "/p", "{}", NOW, "abcd1234");
        // Lion arm verifies and short-circuits; the garbage bunny key is never evaluated.
        assertEquals(
                ACCEPT,
                SigVerifier.verify(
                        pubB64(LION), "zzzz", "/p", "{}", String.valueOf(NOW), "abcd1234", sig, new NonceCache(), NOW));
    }

    @Test
    void verify_replay_nonceRecordedBeforeSignatureCheck() {
        // Security property: the nonce is recorded BEFORE the signature is verified, so two
        // concurrent replays of the same (ts,nonce) can't both pass a non-idempotent endpoint.
        NonceCache c = new NonceCache();
        String badSig = java.util.Base64.getEncoder().encodeToString(new byte[256]);
        String ts = String.valueOf(NOW), nn = "replay99";
        // First call: bad signature ⇒ BAD_SIG, but the nonce was already recorded.
        assertEquals(BAD_SIG, SigVerifier.verify(pubB64(LION), "", "/p", "{}", ts, nn, badSig, c, NOW));
        assertEquals(1, c.size());
        // Second call, same (cache, ts, nonce): short-circuits to REPLAY — sig never re-evaluated.
        assertEquals(REPLAY, SigVerifier.verify(pubB64(LION), "", "/p", "{}", ts, nn, badSig, c, NOW));
    }

    @Test
    void verify_distinctNonceOnSameCacheIsNotReplay() throws Exception {
        NonceCache c = new NonceCache();
        String sigA = signCanon(LION, "/p", "{}", NOW, "nonceAAA");
        assertEquals(
                ACCEPT,
                SigVerifier.verify(pubB64(LION), "", "/p", "{}", String.valueOf(NOW), "nonceAAA", sigA, c, NOW));
        String sigB = signCanon(LION, "/p", "{}", NOW, "nonceBBB");
        assertEquals(
                ACCEPT,
                SigVerifier.verify(pubB64(LION), "", "/p", "{}", String.valueOf(NOW), "nonceBBB", sigB, c, NOW));
    }

    // ──────────────── NonceCache — replay window, TTL, FIFO eviction ────────────────

    @Test
    void cache_freshThenSeen() {
        NonceCache c = new NonceCache();
        assertFalse(c.seenOrRecord("k", 100L));
        assertEquals(1, c.size());
        assertTrue(c.seenOrRecord("k", 100L));
        assertEquals(1, c.size());
    }

    @Test
    void cache_ttlStrictBoundary_stillSeenAtExactlyTtl() {
        NonceCache c = new NonceCache();
        c.seenOrRecord("k", 1000L);
        // now - recorded == 600000 is NOT > TTL_MS, so the entry survives ⇒ still seen.
        assertTrue(c.seenOrRecord("k", 1000L + 600_000L));
    }

    @Test
    void cache_ttlExpiry_freshAgainPastTtl() {
        NonceCache c = new NonceCache();
        c.seenOrRecord("k", 1000L);
        // now - recorded == 600001 > TTL_MS ⇒ purged from the front, then re-recorded as fresh.
        assertFalse(c.seenOrRecord("k", 1000L + 600_001L));
    }

    @Test
    void cache_distinctKeysIndependent() {
        NonceCache c = new NonceCache();
        assertFalse(c.seenOrRecord("100.abc", 0L));
        assertFalse(c.seenOrRecord("101.abc", 0L));
        assertTrue(c.seenOrRecord("100.abc", 0L));
    }

    @Test
    void cache_fifoEvictionAtMaxPlusOne() {
        // Constant `now` so the lazy TTL purge never fires — isolates MAX-eviction (4096).
        NonceCache c = new NonceCache();
        long now = 5000L;
        for (int i = 0; i < 4096; i++) {
            assertFalse(c.seenOrRecord("k" + i, now));
        }
        assertEquals(4096, c.size());
        // Inserting the 4097th distinct key evicts the eldest (k0) — FIFO, capacity stays 4096.
        assertFalse(c.seenOrRecord("k4096", now));
        assertEquals(4096, c.size());
        assertFalse(c.seenOrRecord("k0", now)); // k0 was evicted ⇒ treated as fresh
        assertTrue(c.seenOrRecord("k4096", now)); // k4096 still present
    }

    @Test
    void cache_evictionIsFifoNotLru() {
        // "Touching" an entry does NOT protect it (LinkedHashMap accessOrder=false).
        NonceCache c = new NonceCache();
        long now = 7000L;
        for (int i = 0; i < 4096; i++) {
            c.seenOrRecord("k" + i, now);
        }
        assertTrue(c.seenOrRecord("k0", now)); // touch k0 — no reordering
        c.seenOrRecord("k4096", now); // evicts the eldest, which is STILL k0
        assertFalse(c.seenOrRecord("k0", now)); // evicted despite being touched ⇒ FIFO, not LRU
    }
}

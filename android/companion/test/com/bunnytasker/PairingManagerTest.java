package com.bunnytasker;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import android.content.ContentResolver;
import android.provider.Settings;
import java.nio.charset.StandardCharsets;
import java.security.KeyFactory;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.PrivateKey;
import java.security.PublicKey;
import java.security.Signature;
import java.security.spec.X509EncodedKeySpec;
import org.json.JSONObject;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;

/**
 * First JVM unit tests for the companion (Bunny Tasker) app — no device, no
 * android.jar. PairingManager routes every call through Settings.Global on the
 * passed ContentResolver, so the test-support shims (android.content.ContentResolver
 * + android.provider.Settings, in-memory and per-instance) give full isolation:
 * a fresh ContentResolver per @BeforeEach is a fresh Settings.Global namespace.
 *
 * Covers the keypair lifecycle, the pair-state guards, the RSA-SHA256 sign/verify
 * round trip, the cross-impl "Lion signs / Bunny verifies" path that backs the
 * pairing MITM guard, the SHA-256 fingerprint (pinned to a precomputed vector),
 * and the QR/pairing-code builders.
 */
public class PairingManagerTest {

    private ContentResolver cr;

    @BeforeEach
    void freshResolver() {
        cr = new ContentResolver();
    }

    // ──────────────── helpers ────────────────

    private static KeyPair newRsa() throws Exception {
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048);
        return kpg.generateKeyPair();
    }

    private static String pubB64(KeyPair kp) {
        return java.util.Base64.getEncoder().encodeToString(kp.getPublic().getEncoded());
    }

    private static String signWith(PrivateKey pk, String msg) throws Exception {
        Signature s = Signature.getInstance("SHA256withRSA");
        s.initSign(pk);
        s.update(msg.getBytes(StandardCharsets.UTF_8));
        return java.util.Base64.getEncoder().encodeToString(s.sign());
    }

    private static PublicKey decodePub(String b64) throws Exception {
        byte[] der = java.util.Base64.getMimeDecoder().decode(b64);
        return KeyFactory.getInstance("RSA").generatePublic(new X509EncodedKeySpec(der));
    }

    private static boolean externalVerify(PublicKey pk, String msg, String sigB64) throws Exception {
        Signature s = Signature.getInstance("SHA256withRSA");
        s.initVerify(pk);
        s.update(msg.getBytes(StandardCharsets.UTF_8));
        return s.verify(java.util.Base64.getMimeDecoder().decode(sigB64));
    }

    // ──────────────── keypair lifecycle ────────────────

    @Test
    void generateKeypair_persistsAndReturnsDecodablePub() throws Exception {
        String pub = PairingManager.generateKeypair(cr);
        assertNotNull(pub);
        assertFalse(pub.isEmpty());
        // Persisted under the well-known Settings.Global keys.
        assertEquals(pub, Settings.Global.getString(cr, "focus_lock_bunny_pubkey"));
        assertNotNull(Settings.Global.getString(cr, "focus_lock_bunny_privkey"));
        // The returned value is a real RSA X.509 SubjectPublicKeyInfo.
        assertEquals("RSA", decodePub(pub).getAlgorithm());
    }

    @Test
    void getPublicKey_generatesWhenMissingThenIsIdempotent() {
        String p1 = PairingManager.getPublicKey(cr); // generates
        String priv1 = Settings.Global.getString(cr, "focus_lock_bunny_privkey");
        assertNotNull(p1);
        assertFalse(p1.isEmpty());
        String p2 = PairingManager.getPublicKey(cr); // already present — must not regenerate
        assertEquals(p1, p2);
        assertEquals(priv1, Settings.Global.getString(cr, "focus_lock_bunny_privkey"));
    }

    @Test
    void getPublicKey_returnsTheGeneratedKey() {
        String gen = PairingManager.generateKeypair(cr);
        assertEquals(gen, PairingManager.getPublicKey(cr));
    }

    // ──────────────── pair-state guards ────────────────

    @Test
    void isPaired_falseInitially_trueAfterStoreLionKey() {
        assertFalse(PairingManager.isPaired(cr));
        PairingManager.storeLionKey(cr, "some-lion-pubkey");
        assertTrue(PairingManager.isPaired(cr));
    }

    @Test
    void isPaired_falseForEmptyOrLiteralNull() {
        PairingManager.storeLionKey(cr, "");
        assertFalse(PairingManager.isPaired(cr));
        PairingManager.storeLionKey(cr, "null"); // guards against the literal string "null"
        assertFalse(PairingManager.isPaired(cr));
    }

    @Test
    void getLionKey_roundTripAndGuards() {
        assertEquals("", PairingManager.getLionKey(cr)); // absent → ""
        PairingManager.storeLionKey(cr, "ABC123");
        assertEquals("ABC123", PairingManager.getLionKey(cr));
        PairingManager.storeLionKey(cr, "null");
        assertEquals("", PairingManager.getLionKey(cr)); // literal "null" → ""
    }

    // ──────────────── sign / verify ────────────────

    @Test
    void signVerify_selfRoundTrip() {
        // lion := bunny pub, so verify() (which checks against the lion key) accepts
        // signatures made by sign() (which uses the bunny priv).
        String bunnyPub = PairingManager.getPublicKey(cr);
        PairingManager.storeLionKey(cr, bunnyPub);
        String sig = PairingManager.sign(cr, "hello");
        assertFalse(sig.isEmpty());
        assertTrue(PairingManager.verify(cr, "hello", sig));
        assertFalse(PairingManager.verify(cr, "hellp", sig)); // tampered message
    }

    @Test
    void sign_outputVerifiesUnderBunnyPub() throws Exception {
        String bunnyPub = PairingManager.getPublicKey(cr);
        String sig = PairingManager.sign(cr, "msg");
        assertFalse(sig.isEmpty());
        PublicKey pk = decodePub(bunnyPub);
        assertTrue(externalVerify(pk, "msg", sig));
        assertFalse(externalVerify(pk, "msg-tampered", sig));
        // A flipped signature byte (still valid base64) must not verify.
        byte[] sb = java.util.Base64.getMimeDecoder().decode(sig);
        sb[0] ^= 0x01;
        assertFalse(externalVerify(pk, "msg", java.util.Base64.getEncoder().encodeToString(sb)));
    }

    @Test
    void verify_acceptsLionSignedMessage_crossImpl() throws Exception {
        // The MITM-guard path: Lion signs a challenge with its RSA key, Bunny verifies
        // against the stored Lion pubkey. Matches controller VaultCrypto.signString /
        // server focuslock_vault (all RSA-PKCS1v15-SHA256 over UTF-8).
        KeyPair lion = newRsa();
        PairingManager.storeLionKey(cr, pubB64(lion));
        String msg = "pair-challenge-42";
        assertTrue(PairingManager.verify(cr, msg, signWith(lion.getPrivate(), msg)));
        // Negatives: tampered message, flipped sig byte, and a different signer all fail.
        assertFalse(PairingManager.verify(cr, "pair-challenge-43", signWith(lion.getPrivate(), msg)));
        byte[] sb = java.util.Base64.getMimeDecoder().decode(signWith(lion.getPrivate(), msg));
        sb[10] ^= 0x01;
        assertFalse(PairingManager.verify(cr, msg, java.util.Base64.getEncoder().encodeToString(sb)));
        KeyPair wrong = newRsa();
        assertFalse(PairingManager.verify(cr, msg, signWith(wrong.getPrivate(), msg)));
    }

    @Test
    void sign_returnsEmptyWhenNoPrivkey() {
        // Fresh resolver, no keypair generated ⇒ no privkey ⇒ "" (error path).
        assertEquals("", PairingManager.sign(cr, "m"));
    }

    @Test
    void verify_falseWhenNoLionKeyOrBadSig() throws Exception {
        assertFalse(PairingManager.verify(cr, "m", "c2ln")); // no lion key stored
        PairingManager.storeLionKey(cr, pubB64(newRsa()));
        assertFalse(PairingManager.verify(cr, "m", "A")); // un-decodable sig ⇒ swallowed ⇒ false
    }

    // ──────────────── fingerprint ────────────────

    @Test
    void getFingerprint_precomputedVector() {
        // getFingerprint just SHA-256's the decoded stored pubkey bytes (it does not
        // require a real key), so inject base64("hello") and pin the known digest.
        // SHA-256("hello") = 2cf24dba5fb0a30e... → first 8 bytes → "2cf24dba5fb0a30e".
        Settings.Global.putString(cr, "focus_lock_bunny_pubkey", "aGVsbG8=");
        assertEquals("2cf24dba5fb0a30e", PairingManager.getFingerprint(cr));
    }

    @Test
    void getFingerprint_deterministicAnd16Hex() {
        PairingManager.generateKeypair(cr);
        String fp1 = PairingManager.getFingerprint(cr);
        String fp2 = PairingManager.getFingerprint(cr);
        assertEquals(fp1, fp2);
        assertEquals(16, fp1.length());
        assertTrue(fp1.matches("[0-9a-f]{16}"));
    }

    // ──────────────── QR / pairing-code builders ────────────────

    @Test
    void buildPairingCode_prefersTailscaleThenLan() {
        assertEquals("100.64.0.1:8432", PairingManager.buildPairingCode("192.168.1.5", "100.64.0.1"));
        assertEquals("192.168.1.5:8432", PairingManager.buildPairingCode("192.168.1.5", null));
        assertEquals("192.168.1.5:8432", PairingManager.buildPairingCode("192.168.1.5", ""));
    }

    @Test
    void buildPairingCode_edgeCasesPinned() {
        assertEquals(":8432", PairingManager.buildPairingCode("", ""));
        // A null lanIp concatenates to the literal "null" — pinned actual behavior.
        assertEquals("null:8432", PairingManager.buildPairingCode(null, null));
    }

    @Test
    void buildQrPayload_fieldsParseBack() {
        Settings.Global.putString(cr, "focus_lock_bunny_pubkey", "aGVsbG8="); // stable fp 2cf24dba5fb0a30e
        String s = PairingManager.buildQrPayload(cr, "192.168.1.5", "100.64.0.1");
        assertTrue(s.startsWith("{\"t\":\"fl\""));
        JSONObject qr = new JSONObject(s);
        assertEquals("fl", qr.getString("t"));
        assertEquals("2cf24dba5fb0a30e", qr.getString("f"));
        assertEquals("192.168.1.5", qr.getString("l"));
        assertEquals("100.64.0.1", qr.getString("s"));
        assertEquals(8432, qr.getInt("p")); // p is a JSON number, not a string
    }

    @Test
    void buildQrPayload_doesNotEscapeValues() {
        // lanIp/tailscaleIp are trusted local input; the builder concatenates them
        // without JSON-escaping. Pin that: an embedded quote is emitted verbatim.
        PairingManager.generateKeypair(cr);
        String s = PairingManager.buildQrPayload(cr, "a\"b", "");
        assertTrue(s.contains("\"l\":\"a\"b\""));
    }
}

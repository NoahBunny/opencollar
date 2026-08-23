package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertNotNull;
import static org.junit.jupiter.api.Assertions.assertNull;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.util.Base64;
import org.junit.jupiter.api.Test;

/**
 * Pure-JVM unit tests for E2EEHelper.canEncrypt/canDecrypt — the per-peer gate
 * the B3 "not encrypted" banner + plaintext send-status depend on. No device,
 * no android.jar (android.util.Base64 supplied by the test-support shim).
 */
public class E2EEHelperTest {

    private static final String REAL_KEY =
        "MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEA";  // >20 chars, base64-ish

    @Test
    void canEncryptFalseWhenKeyMissing() {
        assertFalse(E2EEHelper.canEncrypt(null));
        assertFalse(E2EEHelper.canEncrypt(""));
        assertFalse(E2EEHelper.canEncrypt("null"));
        assertFalse(E2EEHelper.canEncrypt("short"));        // len <= 20
        assertFalse(E2EEHelper.canEncrypt("12345678901234567890")); // exactly 20
    }

    @Test
    void canEncryptTrueWithRealKey() {
        assertTrue(E2EEHelper.canEncrypt(REAL_KEY));
    }

    @Test
    void canDecryptMatchesGuard() {
        assertFalse(E2EEHelper.canDecrypt(null));
        assertFalse(E2EEHelper.canDecrypt("null"));
        assertTrue(E2EEHelper.canDecrypt(REAL_KEY));
    }

    // ── the sender's own copy ─────────────────────────────────────────

    private static KeyPair newKeyPair() throws Exception {
        KeyPairGenerator g = KeyPairGenerator.getInstance("RSA");
        g.initialize(2048);
        return g.generateKeyPair();
    }

    /** X.509 SubjectPublicKeyInfo, base64 — what the apps exchange. */
    private static String pubB64(KeyPair kp) {
        return Base64.getEncoder().encodeToString(kp.getPublic().getEncoded());
    }

    /** PKCS#8, base64 — what each side keeps. */
    private static String privB64(KeyPair kp) {
        return Base64.getEncoder().encodeToString(kp.getPrivate().getEncoded());
    }

    @Test
    public void senderCanReadWhatTheySentWhenTheKeyIsWrappedForThemToo() throws Exception {
        // Without the second wrap the Lion encrypts to Bunny's key alone and
        // cannot decrypt their own copy: the Inbox shows who they wrote to and
        // when, but never what they said.
        KeyPair bunny = newKeyPair();
        KeyPair lion = newKeyPair();
        String msg = "Be ready at eight. Wear what I told You— no, what I told you.";

        E2EEHelper.EncryptedMessage enc = E2EEHelper.encrypt(msg, pubB64(bunny), pubB64(lion));
        assertNotNull(enc);
        assertNotNull(enc.encryptedKeySelf, "no self-wrap means no readable history");

        // Both readers recover the SAME plaintext from ONE ciphertext.
        assertEquals(msg, E2EEHelper.decrypt(enc.ciphertext, enc.encryptedKey, enc.iv, privB64(bunny)));
        assertEquals(msg, E2EEHelper.decrypt(enc.ciphertext, enc.encryptedKeySelf, enc.iv, privB64(lion)));
    }

    @Test
    public void theTwoWrapsAreOfTheSameKeyButAreNotInterchangeable() throws Exception {
        KeyPair bunny = newKeyPair();
        KeyPair lion = newKeyPair();
        E2EEHelper.EncryptedMessage enc = E2EEHelper.encrypt("hello", pubB64(bunny), pubB64(lion));

        assertNotEquals(enc.encryptedKey, enc.encryptedKeySelf, "same key, different RSA wrap");
        // Each private key opens only its own wrap — the second reader is an
        // addition, not a weakening.
        assertNull(E2EEHelper.decrypt(enc.ciphertext, enc.encryptedKey, enc.iv, privB64(lion)));
        assertNull(E2EEHelper.decrypt(enc.ciphertext, enc.encryptedKeySelf, enc.iv, privB64(bunny)));
    }

    @Test
    public void omittingTheSecondRecipientStillProducesAValidMessage() throws Exception {
        // Backward compatibility: the one-argument form is unchanged, and a
        // message with no self-wrap must still reach the bunny.
        KeyPair bunny = newKeyPair();
        E2EEHelper.EncryptedMessage enc = E2EEHelper.encrypt("plain path", pubB64(bunny));
        assertNotNull(enc);
        assertNull(enc.encryptedKeySelf);
        assertEquals("plain path", E2EEHelper.decrypt(enc.ciphertext, enc.encryptedKey, enc.iv, privB64(bunny)));
    }

    @Test
    public void anUnusableSelfKeyDoesNotCostTheBunnyTheirMessage() throws Exception {
        // A failure wrapping for ourselves must never fail the send: the
        // recipient's copy is already sealed and correct.
        KeyPair bunny = newKeyPair();
        E2EEHelper.EncryptedMessage enc = E2EEHelper.encrypt("still delivered", pubB64(bunny), "not-a-key");
        assertNotNull(enc);
        assertEquals("still delivered", E2EEHelper.decrypt(enc.ciphertext, enc.encryptedKey, enc.iv, privB64(bunny)));
    }
}

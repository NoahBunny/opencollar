package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

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
}

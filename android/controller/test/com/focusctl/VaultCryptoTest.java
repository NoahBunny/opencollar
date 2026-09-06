package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.nio.charset.StandardCharsets;
import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.security.Signature;
import java.util.LinkedHashMap;
import java.util.Map;
import org.junit.jupiter.api.Test;

/**
 * Pure-JVM unit tests for the controller's VaultCrypto — no device, no android.jar
 * (android.util.* is supplied by the test-support shims). Pins the canonical_json
 * contract that MUST match the Python server, and the signString round trip.
 */
public class VaultCryptoTest {

    private static String canon(Map<String, Object> m) {
        return new String(VaultCrypto.canonicalJson(m), StandardCharsets.UTF_8);
    }

    @Test
    void sortsKeysWithoutWhitespace() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("b", 1);
        m.put("a", "x");
        assertEquals("{\"a\":\"x\",\"b\":1}", canon(m));
    }

    @Test
    void escapesNonAsciiAsLowercaseUnicode() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("u", "⚡"); // ⚡
        assertEquals("{\"u\":\"\\u26a1\"}", canon(m));
    }

    @Test
    void booleanSerializesLowercase() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("z", true);
        assertEquals("{\"z\":true}", canon(m));
    }

    @Test
    void nestedMapKeysSortedRecursively() {
        Map<String, Object> n = new LinkedHashMap<>();
        n.put("z", true);
        n.put("y", 2);
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("n", n);
        assertEquals("{\"n\":{\"y\":2,\"z\":true}}", canon(m));
    }

    @Test
    void escapesControlCharsWithNamedEscapes() {
        Map<String, Object> m = new LinkedHashMap<>();
        m.put("k", "a\tb\nc");
        assertEquals("{\"k\":\"a\\tb\\nc\"}", canon(m));
    }

    @Test
    void signStringRoundTrips() throws Exception {
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048);
        KeyPair kp = kpg.generateKeyPair();
        String privB64 = java.util.Base64.getEncoder().encodeToString(kp.getPrivate().getEncoded());

        String sigB64 = VaultCrypto.signString("hello world", privB64);

        Signature v = Signature.getInstance("SHA256withRSA");
        v.initVerify(kp.getPublic());
        v.update("hello world".getBytes(StandardCharsets.UTF_8));
        assertTrue(v.verify(java.util.Base64.getMimeDecoder().decode(sigB64)));

        // A different message must not verify against the same signature.
        Signature v2 = Signature.getInstance("SHA256withRSA");
        v2.initVerify(kp.getPublic());
        v2.update("tampered".getBytes(StandardCharsets.UTF_8));
        assertFalse(v2.verify(java.util.Base64.getMimeDecoder().decode(sigB64)));
    }
}

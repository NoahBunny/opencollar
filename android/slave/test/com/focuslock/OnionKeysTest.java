package com.focuslock;

import org.junit.jupiter.api.Test;

import java.util.Arrays;

import static org.junit.jupiter.api.Assertions.assertEquals;

/**
 * Locks the A3 onion crypto (OnionKeys) against a Python `cryptography`
 * reference vector for seed = 32 bytes of 0x42. Guards the base32-vs-base64
 * footgun: if anyone swaps an encoding, the .onion / keyblob diverge and these
 * fail loudly — long before a device ever sees a 512/auth error.
 *
 * Reference (scripts: hashlib + cryptography.ed25519, see commit message):
 *   pub   = 2152f8d1…db12
 *   onion = efjprum3…7sad.onion   (56 base32 chars)
 *   blob  = kOdZX8ie…PkA==        (base64 of clamp(SHA512(seed)[:32]) || SHA512(seed)[32:64])
 */
public class OnionKeysTest {

    private static byte[] seed42() {
        byte[] s = new byte[32];
        Arrays.fill(s, (byte) 0x42);
        return s;
    }

    private static final String EXPECT_PUB_HEX =
        "2152f8d19b791d24453242e15f2eab6cb7cffa7b6a5ed30097960e069881db12";
    private static final String EXPECT_ONION =
        "efjprum3peosirjsilqv6lvlns3476t3njpngaexsyhangeb3mjo7sad.onion";
    private static final String EXPECT_KEYBLOB =
        "kOdZX8ieUv393OnGpD102/YEcCXuBGLS0XLotqKEHW7tpmzimD9/9+R8SWFSIOeMJcd1oECVcxa3uv1ZhUUPkA==";

    @Test
    void pubKeyMatchesReference() {
        assertEquals(EXPECT_PUB_HEX, hex(OnionKeys.ed25519Pub(seed42())));
    }

    @Test
    void onionMatchesReference() {
        assertEquals(EXPECT_ONION, OnionKeys.onionFromSeed(seed42()));
    }

    @Test
    void onionIs56CharsPlusSuffix() {
        assertEquals(56 + ".onion".length(), OnionKeys.onionFromSeed(seed42()).length());
    }

    @Test
    void addOnionKeyblobMatchesReference() throws Exception {
        assertEquals(EXPECT_KEYBLOB, OnionKeys.addOnionKeyblob(seed42()));
    }

    @Test
    void base32RfcVector() {
        // RFC 4648 base32("foobar") = MZXW6YTBOI======  ->  lowercase, no pad.
        assertEquals("mzxw6ytboi", OnionKeys.base32("foobar".getBytes()));
    }

    @Test
    void x25519KeypairShape() {
        byte[][] kp = OnionKeys.x25519Keypair();
        assertEquals(32, kp[0].length);  // priv
        assertEquals(32, kp[1].length);  // pub
    }

    private static String hex(byte[] b) {
        StringBuilder s = new StringBuilder(b.length * 2);
        for (byte x : b) s.append(String.format("%02x", x));
        return s.toString();
    }
}

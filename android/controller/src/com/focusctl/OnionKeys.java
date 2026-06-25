package com.focusctl;

import org.bouncycastle.crypto.params.Ed25519PrivateKeyParameters;
import org.bouncycastle.crypto.params.X25519PrivateKeyParameters;
import org.bouncycastle.crypto.digests.SHA3Digest;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.Arrays;
import java.util.Base64;

/**
 * Pure (no-Android) crypto for the A3 Tor v3 onion layer. Kept free of any
 * android.* import so it compiles + unit-tests on a plain JVM against a Python
 * `cryptography` reference (see slave/test/.../OnionKeysTest.java) — this is
 * where the #1 footgun lives: the .onion address, the ClientAuthV3 key, and the
 * x25519 pubkey are base32 (lower, no pad), while the ADD_ONION keyblob and the
 * x25519 private key are std base64. Mixing the two = control-port auth failure.
 *
 * Uses Bouncy Castle directly for Ed25519/X25519 raw scalars and SHA3-256
 * (Android's MessageDigest "SHA3-256" support is Conscrypt-version dependent;
 * BC's SHA3Digest is deterministic everywhere). SHA-512 and base64 come from the
 * JDK (java.util.Base64 / MessageDigest are present on the JVM and Android 26+;
 * the apps build with --min-api 33).
 */
public final class OnionKeys {
    private OnionKeys() {}

    /** RFC 4648 base32 alphabet, lowercase (Tor uses lowercase, no padding). */
    private static final String B32 = "abcdefghijklmnopqrstuvwxyz234567";

    /** 32 random bytes — the persisted onion seed (NOT the expanded key). */
    public static byte[] randomSeed() {
        byte[] s = new byte[32];
        new SecureRandom().nextBytes(s);
        return s;
    }

    /** Ed25519 public key (32 bytes) for the v3 onion, derived from the seed.
     *  generatePublicKey() does the SHA-512 + clamp + scalar-mult internally, so
     *  this A matches the `a` that {@link #addOnionKeyblob} clamps from the same
     *  seed — assert ServiceID == onionAddress() at ADD_ONION time to catch drift. */
    public static byte[] ed25519Pub(byte[] seed) {
        return new Ed25519PrivateKeyParameters(seed, 0).generatePublicKey().getEncoded();
    }

    /** v3 onion address (with ".onion" suffix, no scheme) for a 32-byte ed25519
     *  pubkey: base32(pub || SHA3_256(".onion checksum" || pub || 0x03)[0:2] || 0x03). */
    public static String onionAddress(byte[] pub32) {
        byte[] prefix = ".onion checksum".getBytes(StandardCharsets.US_ASCII);
        SHA3Digest d = new SHA3Digest(256);
        d.update(prefix, 0, prefix.length);
        d.update(pub32, 0, pub32.length);
        d.update((byte) 0x03);
        byte[] cs = new byte[32];
        d.doFinal(cs, 0);
        byte[] addr = new byte[35];
        System.arraycopy(pub32, 0, addr, 0, 32);
        addr[32] = cs[0];
        addr[33] = cs[1];
        addr[34] = 0x03;
        return base32(addr) + ".onion";
    }

    /** Convenience: derive the .onion straight from the seed. */
    public static String onionFromSeed(byte[] seed) {
        return onionAddress(ed25519Pub(seed));
    }

    /** ADD_ONION ED25519-V3 keyblob (std base64) from the 32-byte seed:
     *  h = SHA-512(seed); a = clamp(h[0:32]); blob = base64(a || h[32:64]).
     *  This is the expanded secret key Tor wants after the "ED25519-V3:" prefix. */
    public static String addOnionKeyblob(byte[] seed) throws Exception {
        byte[] h = MessageDigest.getInstance("SHA-512").digest(seed);
        byte[] a = Arrays.copyOfRange(h, 0, 32);
        a[0] &= (byte) 248;
        a[31] &= (byte) 127;
        a[31] |= (byte) 64;
        byte[] sk = new byte[64];
        System.arraycopy(a, 0, sk, 0, 32);
        System.arraycopy(h, 32, sk, 32, 32);
        return Base64.getEncoder().encodeToString(sk);
    }

    /** RFC 4648 base32, lowercase, no padding. */
    public static String base32(byte[] data) {
        StringBuilder sb = new StringBuilder((data.length * 8 + 4) / 5);
        int buffer = 0, bits = 0;
        for (byte b : data) {
            buffer = (buffer << 8) | (b & 0xff);
            bits += 8;
            while (bits >= 5) {
                sb.append(B32.charAt((buffer >> (bits - 5)) & 31));
                bits -= 5;
            }
        }
        if (bits > 0) {
            sb.append(B32.charAt((buffer << (5 - bits)) & 31));
        }
        return sb.toString();
    }

    /** Lion's x25519 client-auth keypair: returns {priv32, pub32}. The PUBLIC key
     *  is sent to the Collar (base32, as `onion_auth_pub`) and used in its
     *  ClientAuthV3= clause; the PRIVATE key stays on Lion (std base64) for the
     *  ONION_CLIENT_AUTH_ADD control command. */
    public static byte[][] x25519Keypair() {
        X25519PrivateKeyParameters priv = new X25519PrivateKeyParameters(new SecureRandom());
        return new byte[][] { priv.getEncoded(), priv.generatePublicKey().getEncoded() };
    }
}

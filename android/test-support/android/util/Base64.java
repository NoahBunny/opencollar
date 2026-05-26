package android.util;

import java.nio.charset.StandardCharsets;

/**
 * JVM test-support shim for android.util.Base64.
 *
 * android.jar's Base64 is a stub that throws RuntimeException("Stub!") on a
 * plain JVM, which makes the crypto classes untestable off-device. This drop-in
 * replacement delegates to java.util.Base64 with semantics matching the flag
 * combinations the production code actually uses:
 *   - decode(..., DEFAULT)            standard alphabet, padding optional,
 *                                     tolerant of embedded newlines (PEM bodies)
 *   - encodeToString(..., NO_WRAP)    standard alphabet, padded, no line breaks
 *   - encode(..., NO_WRAP|URL_SAFE|NO_PADDING)  url-safe, unpadded, no wrap
 *
 * Flag int values mirror Android's so code using the constants behaves
 * identically. Lives only on the test classpath — NEVER bundled in an APK.
 */
public final class Base64 {
    public static final int DEFAULT = 0;
    public static final int NO_PADDING = 1;
    public static final int NO_WRAP = 2;
    public static final int CRLF = 4;
    public static final int URL_SAFE = 8;
    public static final int NO_CLOSE = 16;

    private Base64() {}

    public static byte[] decode(String s, int flags) {
        return decode(s.getBytes(StandardCharsets.UTF_8), flags);
    }

    public static byte[] decode(byte[] input, int flags) {
        String s = new String(input, StandardCharsets.UTF_8).trim();
        if ((flags & URL_SAFE) != 0) {
            // The basic/url decoders are strict about padding; restore it.
            int rem = s.length() % 4;
            if (rem == 2) s = s + "==";
            else if (rem == 3) s = s + "=";
            return java.util.Base64.getUrlDecoder().decode(s);
        }
        // MIME decoder ignores embedded newlines/whitespace (PEM bodies) and
        // tolerates missing padding — matches Android's lenient DEFAULT decode.
        return java.util.Base64.getMimeDecoder().decode(s);
    }

    public static String encodeToString(byte[] input, int flags) {
        return new String(encode(input, flags), StandardCharsets.UTF_8);
    }

    public static byte[] encode(byte[] input, int flags) {
        java.util.Base64.Encoder e =
            ((flags & URL_SAFE) != 0) ? java.util.Base64.getUrlEncoder() : java.util.Base64.getEncoder();
        if ((flags & NO_PADDING) != 0) {
            e = e.withoutPadding();
        }
        // The basic/url encoders emit a single line (no wrapping) — i.e. NO_WRAP.
        return e.encode(input);
    }
}

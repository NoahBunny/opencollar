package com.focusctl;

/**
 * First-match JSON field scanners, lifted out of MainActivity.
 *
 * <p><b>These are scanners, not a parser</b>, and the name says so on purpose.
 * Each one walks the raw body looking for the FIRST occurrence of
 * {@code "key":} and reads whatever follows. That is fast and dependency-free,
 * and it is wrong whenever a key name appears more than once in the body.
 *
 * <p>That is not hypothetical here. The Collar's direct-mode {@code /mesh/status}
 * embeds the entire orders document under {@code "orders"} BEFORE the status
 * fields, and six of the ten signed core key names repeat inside it. Five
 * agreed by luck; {@code paywall} did not (status defaults an unset paywall to
 * {@code "0"}, orders emits {@code ""}), so a freshly paired Collar's status was
 * dropped as forged — see {@link StatusCore}, which exists precisely so the
 * signature-covered fields are read from the top-level object instead of
 * scanned for. Use {@code StatusCore} for anything the signature covers; these
 * are for the non-core fields that only ever appear once.
 *
 * <p>Extracted here so they can be exercised directly: they had no unit tests at
 * all despite that history, because they were private instance methods on a
 * 6,000-line Activity that cannot be instantiated off-device.
 *
 * <p>Every method is total — malformed input yields the zero value for its type
 * rather than throwing, which is what the callers on the render path rely on.
 */
final class JsonScan {

    private JsonScan() {}

    /** True only when the first {@code "key":} is followed by literal `true`. */
    static boolean bool(String json, String key) {
        String search = "\"" + key + "\":";
        int i = json.indexOf(search);
        if (i < 0) return false;
        String rest = json.substring(i + search.length()).trim();
        return rest.startsWith("true");
    }

    /** First {@code "key":} read as an int; 0 when absent or unparseable. */
    static int asInt(String json, String key) {
        try {
            return Integer.parseInt(rawValue(json, key));
        } catch (Exception e) {
            return 0;
        }
    }

    /** First {@code "key":} read as a long; 0 when absent or unparseable. */
    static long asLong(String json, String key) {
        try {
            return Long.parseLong(rawValue(json, key));
        } catch (Exception e) {
            return 0;
        }
    }

    /** First {@code "key":"value"} as a string; "" when absent. Tolerates one
     *  space after the colon, which is the only spacing the servers emit. */
    static String str(String json, String key) {
        try {
            String search1 = "\"" + key + "\":\"";
            String search2 = "\"" + key + "\": \"";
            int i = json.indexOf(search1);
            int len = search1.length();
            if (i < 0) {
                i = json.indexOf(search2);
                len = search2.length();
            }
            if (i < 0) return "";
            i += len;
            int e = json.indexOf("\"", i);
            return e > i ? json.substring(i, e) : "";
        } catch (Exception e) {
            return "";
        }
    }

    /** The raw unquoted token after the first {@code "key":}; "" when absent.
     *  Used where a number must survive as text (e.g. a geofence radius that
     *  may arrive as "0", 0 or ""). */
    static String numStr(String json, String key) {
        try {
            return rawValue(json, key);
        } catch (Exception e) {
            return "";
        }
    }

    /** Shared scan: everything after the first {@code "key":} up to the next
     *  `,` or `}`, trimmed. Throws only if the key is absent, so the typed
     *  wrappers above can turn that into their own zero value. */
    private static String rawValue(String json, String key) {
        int i = json.indexOf("\"" + key + "\":");
        if (i < 0) throw new IllegalArgumentException("absent");
        i = json.indexOf(":", i) + 1;
        int e = i;
        while (e < json.length() && json.charAt(e) != ',' && json.charAt(e) != '}') e++;
        return json.substring(i, e).trim();
    }

    /** Escape a string for embedding in the hand-built JSON request bodies.
     *
     *  <p>Backslash, quote and newline only — carried over verbatim from
     *  MainActivity.esc(). A carriage return or tab still passes through raw,
     *  which is invalid JSON (RFC 8259 forbids unescaped control characters
     *  below 0x20 inside a string); see JsonScanTest, which pins both the
     *  behaviour and the gap. Left as-is here deliberately: this class is an
     *  extraction, and changing what goes on the wire belongs in its own
     *  change, not smuggled inside a refactor. */
    static String escape(String s) {
        return s.replace("\\", "\\\\").replace("\"", "\\\"").replace("\n", "\\n");
    }
}

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
     *  <p>MainActivity.esc(), which this replaced, handled backslash, quote and
     *  newline and nothing else. That was a live failure, not merely untidy:
     *  RFC 8259 forbids unescaped control characters below 0x20 inside a
     *  string, and both consumers enforce it — {@code org.json.JSONTokener} on
     *  the Collar and Python's strict {@code json.loads} on the relay. So a
     *  lock message or writing task pasted with Windows line endings escaped
     *  its LF and left the CR raw, and the whole order was rejected rather than
     *  merely rendering oddly. The Lion pressed Lock and nothing happened.
     *
     *  <p>Now every C0 character is escaped: the five with short forms, and
     *  anything else below 0x20 as {@code \\u00XX}. Characters at or above
     *  0x20 — including all non-ASCII — are passed through unchanged, which is
     *  what the servers already accept and what keeps this byte-compatible
     *  with every body that worked before. */
    static String escape(String s) {
        StringBuilder out = new StringBuilder(s.length() + 16);
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            switch (c) {
                case '\\': out.append("\\\\"); break;
                case '"':  out.append("\\\""); break;
                case '\n': out.append("\\n"); break;
                case '\r': out.append("\\r"); break;
                case '\t': out.append("\\t"); break;
                case '\b': out.append("\\b"); break;
                case '\f': out.append("\\f"); break;
                default:
                    if (c < 0x20) {
                        out.append(String.format("\\u%04x", (int) c));
                    } else {
                        out.append(c);
                    }
            }
        }
        return out.toString();
    }
}

package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * Spec for the first-match JSON scanners.
 *
 * <p>Two jobs. The first is the ordinary one: pin that each scanner reads what
 * it claims to and returns a zero value rather than throwing on anything
 * malformed — every caller sits on the render path and treats a throw as a
 * blank screen.
 *
 * <p>The second matters more. These scan for the FIRST occurrence of a key, and
 * that has already cost this project a shipped bug: the Collar's direct-mode
 * status body embeds the whole orders document before the status fields, so six
 * signed key names appear twice and {@code paywall} disagreed between the
 * copies — every genuine status from a freshly paired Collar was rejected as
 * forged. {@link StatusCore} exists because of it. The shadowing tests below
 * pin that behaviour as KNOWN rather than correct, so nobody re-derives the
 * same bug from a scanner that looks like a parser.
 */
public class JsonScanTest {

    // ── the ordinary contract ─────────────────────────────────────────

    @Test
    void readsBooleansAndTreatsAnythingElseAsFalse() {
        assertTrue(JsonScan.bool("{\"locked\":true}", "locked"));
        assertFalse(JsonScan.bool("{\"locked\":false}", "locked"));
        assertFalse(JsonScan.bool("{\"locked\":\"true\"}", "locked"), "a quoted string is not the literal");
        assertFalse(JsonScan.bool("{}", "locked"), "absent reads false");
    }

    @Test
    void readsNumbers() {
        assertEquals(42, JsonScan.asInt("{\"escapes\":42}", "escapes"));
        assertEquals(-7, JsonScan.asInt("{\"delta\":-7}", "delta"));
        assertEquals(9_000_000_000L, JsonScan.asLong("{\"ms\":9000000000}", "ms"));
        assertEquals(42, JsonScan.asInt("{\"a\":1,\"escapes\":42,\"b\":2}", "escapes"), "stops at the comma");
    }

    @Test
    void malformedOrAbsentNumbersAreZeroRatherThanAThrow() {
        assertEquals(0, JsonScan.asInt("{}", "escapes"));
        assertEquals(0, JsonScan.asInt("{\"escapes\":\"lots\"}", "escapes"));
        assertEquals(0L, JsonScan.asLong("{\"ms\":}", "ms"));
        assertEquals(0, JsonScan.asInt("", "escapes"));
    }

    @Test
    void readsStringsWithOrWithoutTheSpaceAfterTheColon() {
        assertEquals("hi", JsonScan.str("{\"msg\":\"hi\"}", "msg"));
        assertEquals("hi", JsonScan.str("{\"msg\": \"hi\"}", "msg"));
        assertEquals("", JsonScan.str("{\"msg\":\"\"}", "msg"));
        assertEquals("", JsonScan.str("{}", "msg"));
    }

    @Test
    void numStrKeepsTheRawTokenSoAnEmptyValueStaysDistinctFromZero() {
        // fine_active / body_check_active are read this way precisely because
        // "not set" and "set to 0" mean different things to the caller.
        assertEquals("0", JsonScan.numStr("{\"fine_active\":0}", "fine_active"));
        assertEquals("1", JsonScan.numStr("{\"fine_active\":1}", "fine_active"));
        assertEquals("", JsonScan.numStr("{}", "fine_active"));
    }

    // ── the hazard, pinned as known ───────────────────────────────────

    @Test
    void aRepeatedKeyResolvesToTheFirstOccurrence() {
        String body = "{\"orders\":{\"paywall\":\"\"},\"paywall\":\"250\"}";
        assertEquals("", JsonScan.str(body, "paywall"),
            "KNOWN, NOT CORRECT: the embedded orders copy shadows the real value. "
            + "Anything the status signature covers must be read via StatusCore instead.");
    }

    @Test
    void theShadowingIsInvisibleWhenTheCopiesHappenToAgree() {
        // Why the shipped bug survived review: five of the six duplicated keys
        // agreed by luck, so the scanners looked correct right up until the one
        // that did not.
        String body = "{\"orders\":{\"locked\":true},\"locked\":true}";
        assertTrue(JsonScan.bool(body, "locked"));
    }

    // ── escaping ──────────────────────────────────────────────────────

    @Test
    void escapesBackslashQuoteAndNewline() {
        assertEquals("a\\\\b", JsonScan.escape("a\\b"));
        assertEquals("say \\\"hi\\\"", JsonScan.escape("say \"hi\""));
        assertEquals("line1\\nline2", JsonScan.escape("line1\nline2"));
        assertEquals("", JsonScan.escape(""));
    }

    @Test
    void escapeOrderDoesNotDoubleEscapeAnAlreadyEscapedSequence() {
        // Backslash must be replaced first or "\n" typed literally by the Lion
        // would come out as a real newline escape and shift the payload.
        assertEquals("a\\\\nb", JsonScan.escape("a\\nb"));
    }

    @Test
    void escapesEveryControlCharacterSoACrlfPasteStillProducesValidJson() {
        // The failure this closes: esc() escaped the LF and left the CR raw, so
        // a message pasted from a Windows editor made the whole body invalid
        // (RFC 8259 forbids unescaped control characters below 0x20), and both
        // consumers reject it — org.json on the Collar, strict json.loads on
        // the relay. The Lion pressed Lock and nothing happened.
        assertEquals("a\\r\\nb", JsonScan.escape("a\r\nb"));
        assertEquals("a\\tb", JsonScan.escape("a\tb"));
        assertEquals("a\\bb", JsonScan.escape("a\bb"));
        assertEquals("a\\fb", JsonScan.escape("a\fb"));
        assertEquals("a\\u0000b", JsonScan.escape("a" + (char) 0x00 + "b"),
            "no short form for NUL — falls back to the \\uXXXX form");
        assertEquals("a\\u001fb", JsonScan.escape("a" + (char) 0x1f + "b"),
            "0x1F is the last character that must be escaped");
    }

    @Test
    void leavesEverythingAtOrAboveZeroXTwentyAlone() {
        // Byte-compatible with every body that already worked: only C0 changed.
        assertEquals("a b", JsonScan.escape("a" + (char) 0x20 + "b"), "0x20 is the first character that passes through");
        assertEquals("Bonne nuit, petit lapin éèê", JsonScan.escape("Bonne nuit, petit lapin éèê"));
        assertEquals("🐰", JsonScan.escape("🐰"), "surrogate pairs survive intact");
    }

    @Test
    void isValidJsonForEveryControlCharacter() {
        // The property that actually matters, asserted end to end rather than
        // character by character: whatever goes in, the body parses back out
        // to exactly what went in.
        StringBuilder nasty = new StringBuilder("lock: ");
        for (char c = 0; c < 0x21; c++) nasty.append(c);
        nasty.append("\"quoted\" and a \\ backslash");
        String body = "{\"message\":\"" + JsonScan.escape(nasty.toString()) + "\"}";
        assertEquals(nasty.toString(), new org.json.JSONObject(body).getString("message"));
    }
}

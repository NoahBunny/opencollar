package com.focuslock;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * Spec for pin-shortcut acceptance — the rules that let a PWA be installed
 * while the Collar is the default home app, and stop one being installed
 * mid-lock.
 */
public class PwaShortcutTest {

    // ── what counts as a real installed PWA ───────────────────────────

    @Test
    void recognisesChromeMintedWebApks() {
        assertTrue(PwaShortcut.isWebApk("org.chromium.webapk.a1b2c3d4"));
        assertTrue(PwaShortcut.isWebApk("com.google.android.webapk.a1b2c3d4"));
    }

    @Test
    void doesNotMistakeTheBrowserItselfForAWebApk() {
        // The distinction that matters: a WebAPK is one site in one package,
        // while the browser is every site there is. Anything that treats them
        // alike opens the whole web.
        assertFalse(PwaShortcut.isWebApk("com.android.chrome"));
        assertFalse(PwaShortcut.isWebApk("org.chromium.chrome"));
        assertFalse(PwaShortcut.isWebApk("com.google.android.webview"));
        assertFalse(PwaShortcut.isWebApk("org.mozilla.firefox"));
    }

    @Test
    void handlesRubbishWithoutThrowing() {
        assertFalse(PwaShortcut.isWebApk(null));
        assertFalse(PwaShortcut.isWebApk(""));
        // A near-miss must not pass: a package may not borrow the prefix by
        // being a prefix of it.
        assertFalse(PwaShortcut.isWebApk("org.chromium.webapk"));
    }

    // ── when a pin may be accepted ────────────────────────────────────

    @Test
    void acceptsWhileNothingIsLocked() {
        assertTrue(PwaShortcut.mayAccept(false));
    }

    @Test
    void refusesWhileLocked() {
        // Installing a launcher tile mid-lock is a change to what the phone can
        // do, made by the person the lock exists to constrain, at the one
        // moment they are supposed to be constrained.
        assertFalse(PwaShortcut.mayAccept(true));
    }

    // ── the label that reaches a toast and a log line ─────────────────

    @Test
    void fallsBackToSomethingReadableWhenTheLabelIsBlank() {
        assertEquals("Web app", PwaShortcut.describe(null, "org.chromium.webapk.x"));
        assertEquals("Web app", PwaShortcut.describe("   ", "org.chromium.webapk.x"));
        assertEquals("Shortcut", PwaShortcut.describe("", "com.android.chrome"));
    }

    @Test
    void keepsTheLabelToOneLine() {
        // The label comes from a web page. A multi-line one should not be able
        // to reshape a toast or forge extra lines in the log.
        assertEquals("Bank Of Nowhere  logged in as root", PwaShortcut.describe("Bank Of Nowhere\n\rlogged in as root", "org.chromium.webapk.x"));
    }

    @Test
    void truncatesAnAbsurdlyLongLabel() {
        String out = PwaShortcut.describe("x".repeat(400), "org.chromium.webapk.x");
        assertTrue(out.length() <= 65, "got " + out.length() + " chars");
        assertTrue(out.endsWith("…"));
    }

    @Test
    void keepsAnOrdinaryLabelExactly() {
        assertEquals("Monzo", PwaShortcut.describe("Monzo", "org.chromium.webapk.x"));
    }
}

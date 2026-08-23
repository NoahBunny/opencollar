package com.bunnytasker;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * Spec for what must be true before the cage can hold.
 *
 * <p>The bug these rules close: both apps hold device-administrator privileges
 * and The Collar's mutual-admin monitor re-locks and reports a tamper event
 * when Bunny Tasker's is missing — but neither app's onboarding ever asked for
 * it. A freshly paired bunny therefore landed straight in a relock loop over an
 * admin that had never been granted, explained by a notification claiming it
 * had been "removed".
 */
public class CagePrereqsTest {

    @Test
    public void pairingNeedsBothAdmins() {
        assertTrue(CagePrereqs.pairingAllowed(true, true));
    }

    @Test
    public void oneAdminIsNotEnough() {
        // Either gap produces the same outcome: a bunny who is paired, locked,
        // and being reported for a tamper they did not commit.
        assertFalse(CagePrereqs.pairingAllowed(true, false));
        assertFalse(CagePrereqs.pairingAllowed(false, true));
        assertFalse(CagePrereqs.pairingAllowed(false, false));
    }

    @Test
    public void namesWhatIsMissingRatherThanThatSomethingIs() {
        assertEquals("The Collar still needs device admin.", CagePrereqs.describeMissing(false, true));
        assertEquals("Bunny Tasker still needs device admin.", CagePrereqs.describeMissing(true, false));
        assertEquals(
            "The Collar and Bunny Tasker both still need device admin.",
            CagePrereqs.describeMissing(false, false));
    }

    @Test
    public void saysNothingWhenNothingIsMissing() {
        // Callers use the string as the gate message, so "all good" must be
        // empty rather than a sentence they have to special-case.
        assertEquals("", CagePrereqs.describeMissing(true, true));
    }

    // ── device owner is a preference, never a gate ────────────────────

    @Test
    public void onlySealedPrefersDeviceOwner() {
        assertTrue(CagePrereqs.prefersDeviceOwner("sealed"));
        assertTrue(CagePrereqs.prefersDeviceOwner("SEALED"));
        assertFalse(CagePrereqs.prefersDeviceOwner("collar"));
        assertFalse(CagePrereqs.prefersDeviceOwner("leash"));
        assertFalse(CagePrereqs.prefersDeviceOwner(""));
    }

    @Test
    public void preferenceNeverBecomesARequirement() {
        // Pairing must not consult device owner at all. Android grants it only
        // during provisioning on an account-free phone, so gating on it would
        // mean the dynamic could tighten only on a freshly wiped device — a
        // constraint on the relationship rather than on the wearer.
        assertTrue(CagePrereqs.pairingAllowed(true, true), "device owner is not part of the pairing gate");
    }
}

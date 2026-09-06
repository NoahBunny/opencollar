package com.bunnytasker;

/**
 * What must be true before the cage can hold, and what to say when it is not.
 *
 * <p>Both apps hold device-administrator privileges and the Collar watches
 * Bunny Tasker's: if that admin is missing it re-locks and reports a tamper
 * event (ControlService's mutual-admin monitor). Nothing ever granted it —
 * neither onboarding asked — so a freshly paired bunny landed straight in that
 * state, with the cage relocking over an admin that had never been there and a
 * notification claiming it was "removed".
 *
 * <p>So the grant belongs in onboarding, and pairing waits for it. The rules
 * live here, away from Android, so they can be tested off a device.
 */
final class CagePrereqs {

    private CagePrereqs() {}

    /**
     * Whether pairing may complete.
     *
     * <p>Both admins, because the system already assumes both: pairing without
     * them produces a bunny who is paired, locked, and being reported for a
     * tamper they did not commit.
     */
    static boolean pairingAllowed(boolean collarAdmin, boolean taskerAdmin) {
        return collarAdmin && taskerAdmin;
    }

    /**
     * What is still missing, phrased for the bunny.
     *
     * <p>Names the app rather than the mechanism: "Bunny Tasker still needs
     * admin" is actionable, "prerequisites unmet" is not. Empty when nothing is
     * missing, so callers can use it as the gate message directly.
     */
    static String describeMissing(boolean collarAdmin, boolean taskerAdmin) {
        if (collarAdmin && taskerAdmin) return "";
        if (!collarAdmin && !taskerAdmin) return "The Collar and Bunny Tasker both still need device admin.";
        if (!collarAdmin) return "The Collar still needs device admin.";
        return "Bunny Tasker still needs device admin.";
    }

    /**
     * Whether a tightness tier would rather have device owner than not.
     *
     * <p>Preference, never a requirement. Device owner cannot be granted from
     * inside the app at all — it needs `dpm set-device-owner` on a phone with
     * no accounts added, in practice straight after a factory reset. Making
     * Sealed depend on it would mean the dynamic could only ever tighten on a
     * freshly wiped phone, which is a constraint on the relationship rather
     * than on the bunny. Sealed works without it; it just holds less firmly,
     * and the wearer is told exactly how much less.
     */
    static boolean prefersDeviceOwner(String tier) {
        return "sealed".equalsIgnoreCase(tier);
    }
}

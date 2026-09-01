package com.focuslock;

/**
 * The consent ratchet, as arithmetic.
 *
 * <p>Tightness moves in exactly one direction per party, and neither can move
 * it in the direction that serves them:
 *
 * <ul>
 *   <li>the <b>wearer</b> sets a ceiling and may only raise it (escalation
 *       costs them their own deliberate act — {@code TightenActivity});
 *   <li>the <b>Lion</b> may request a tier and it only ever takes effect if it
 *       is <i>looser</i> than that ceiling (mercy is theirs to give, and to
 *       take back, but never to exceed).
 * </ul>
 *
 * <p>Which reduces to {@code min(ceiling, request)} — the whole rule. It lives
 * in its own class, free of any Android type, for one reason: this is the most
 * safety-critical line in the system and it belongs somewhere a unit test can
 * reach it without a device. {@link ShadeGuardService} supplies the two numbers
 * and nothing more.
 *
 * <p>Where the numbers come from matters as much as the arithmetic. The ceiling
 * is read from the Collar's app-private SharedPreferences ({@link ConsentStore}),
 * the one store the Lion's ADB bridge cannot write; the request is read from
 * Settings.Global, which it can. That asymmetry is deliberate and is what makes
 * "loosen but never tighten" enforceable rather than merely stated.
 */
final class CageRule {

    /** Watchdog off — home button returns to the cage, other apps usable. */
    static final int LEVEL_LEASH = 0;
    /** Watchdog on — non-allowed apps bounce; calls still work. */
    static final int LEVEL_COLLAR = 1;
    /** Watchdog on — minimal allowlist; no ordinary calls. Emergency always works. */
    static final int LEVEL_SEALED = 2;

    private CageRule() {}

    /** Clamp a raw stored ceiling into range. Unset (-1) or nonsense yields
     *  LEASH, so a device provisioned before the chooser existed — or a wearer
     *  who left it alone — is never silently caged tighter than they agreed to. */
    static int clampCeiling(int raw) {
        if (raw < LEVEL_LEASH) return LEVEL_LEASH;
        if (raw > LEVEL_SEALED) return LEVEL_SEALED;
        return raw;
    }

    /**
     * The tier actually in force.
     *
     * @param rawCeiling the wearer's ceiling as stored (-1 when never set)
     * @param lionRequest the Lion's requested tier, or negative for none
     */
    static int effective(int rawCeiling, int lionRequest) {
        int ceiling = clampCeiling(rawCeiling);
        if (lionRequest < 0) return ceiling;              // no request → ceiling stands
        if (lionRequest > LEVEL_SEALED) lionRequest = LEVEL_SEALED;
        return Math.min(ceiling, lionRequest);            // only ever loosens
    }

    /** True if `to` would tighten the wearer's own ceiling — the only direction
     *  they may move it. Used by the tighten screen to offer nothing else. */
    static boolean isTightening(int fromCeiling, int to) {
        return clampCeiling(to) > clampCeiling(fromCeiling);
    }
}

package com.focuslock;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * The consent ratchet, pinned.
 *
 * <p>Tightness is the one control neither party can move in the direction that
 * serves them: the wearer may only raise their ceiling, the Lion may only ask
 * for something looser than it. If this arithmetic ever inverts, the Lion can
 * tighten a cage past what was consented to — which is the failure this whole
 * design exists to make impossible, so it is worth more than a comment.
 */
public class CageRuleTest {

    @Test
    void noRequestLeavesTheWearersCeilingStanding() {
        assertEquals(CageRule.LEVEL_SEALED, CageRule.effective(CageRule.LEVEL_SEALED, -1));
        assertEquals(CageRule.LEVEL_COLLAR, CageRule.effective(CageRule.LEVEL_COLLAR, -1));
        assertEquals(CageRule.LEVEL_LEASH, CageRule.effective(CageRule.LEVEL_LEASH, -1));
    }

    @Test
    void theLionCanLoosen() {
        assertEquals(CageRule.LEVEL_LEASH, CageRule.effective(CageRule.LEVEL_SEALED, CageRule.LEVEL_LEASH));
        assertEquals(CageRule.LEVEL_COLLAR, CageRule.effective(CageRule.LEVEL_SEALED, CageRule.LEVEL_COLLAR));
    }

    @Test
    void theLionCannotTighten() {
        // Every request at or above the ceiling is inert, not merely refused.
        for (int ceiling = 0; ceiling <= CageRule.LEVEL_SEALED; ceiling++) {
            for (int request = ceiling; request <= CageRule.LEVEL_SEALED; request++) {
                assertEquals(ceiling, CageRule.effective(ceiling, request),
                    "ceiling=" + ceiling + " request=" + request + " must not tighten");
            }
        }
    }

    @Test
    void anOutOfRangeRequestCannotEscapeTheCeiling() {
        assertEquals(CageRule.LEVEL_LEASH, CageRule.effective(CageRule.LEVEL_LEASH, 99));
        assertEquals(CageRule.LEVEL_COLLAR, CageRule.effective(CageRule.LEVEL_COLLAR, Integer.MAX_VALUE));
    }

    @Test
    void anUnsetCeilingIsLeashNotSealed() {
        // A device provisioned before the chooser existed must never be
        // silently caged tighter than its wearer agreed to.
        assertEquals(CageRule.LEVEL_LEASH, CageRule.effective(-1, -1));
        assertEquals(CageRule.LEVEL_LEASH, CageRule.effective(-1, CageRule.LEVEL_SEALED));
    }

    @Test
    void aCorruptCeilingClampsIntoRangeRatherThanRunningWild() {
        assertEquals(CageRule.LEVEL_SEALED, CageRule.clampCeiling(99));
        assertEquals(CageRule.LEVEL_LEASH, CageRule.clampCeiling(Integer.MIN_VALUE));
    }

    @Test
    void theEffectiveTierIsNeverTighterThanTheCeiling() {
        // The property, stated once over the whole input space rather than by
        // example: nothing either party sends can exceed the wearer's ceiling.
        for (int rawCeiling = -3; rawCeiling <= 5; rawCeiling++) {
            for (int request = -3; request <= 5; request++) {
                int eff = CageRule.effective(rawCeiling, request);
                assertTrue(eff <= CageRule.clampCeiling(rawCeiling),
                    "rawCeiling=" + rawCeiling + " request=" + request + " produced " + eff);
                assertTrue(eff >= CageRule.LEVEL_LEASH && eff <= CageRule.LEVEL_SEALED);
            }
        }
    }

    @Test
    void tighteningIsRecognisedOnlyUpward() {
        assertTrue(CageRule.isTightening(CageRule.LEVEL_LEASH, CageRule.LEVEL_SEALED));
        assertTrue(CageRule.isTightening(CageRule.LEVEL_COLLAR, CageRule.LEVEL_SEALED));
        assertFalse(CageRule.isTightening(CageRule.LEVEL_SEALED, CageRule.LEVEL_LEASH));
        assertFalse(CageRule.isTightening(CageRule.LEVEL_COLLAR, CageRule.LEVEL_COLLAR));
    }
}

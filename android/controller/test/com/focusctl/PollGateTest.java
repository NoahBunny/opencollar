package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * Guards the status-poll gate. The regression: a serverless Direct (LAN)
 * pairing has no mesh id, and the poller required one, so the Lion's UI never
 * refreshed — it showed whatever it last rendered while the Collar was locked,
 * charged and reporting normally. Found on hardware 2026-08-07.
 */
public class PollGateTest {

    @Test
    void directPairingWithNoMeshStillPolls() {
        // The regression. "Fastest — same Wi-Fi" pairing creates no mesh.
        assertTrue(PollGate.shouldPollStatus("", "direct", true));
    }

    @Test
    void meshPairingPolls() {
        assertTrue(PollGate.shouldPollStatus("L7cqWKvccQb-2Ghc", "", false));
    }

    @Test
    void meshAndDirectTogetherPolls() {
        assertTrue(PollGate.shouldPollStatus("L7cqWKvccQb-2Ghc", "direct", true));
    }

    @Test
    void unpairedDoesNotPoll() {
        assertFalse(PollGate.shouldPollStatus("", "", false));
    }

    @Test
    void directModeWithNoKnownAddressDoesNotPoll() {
        // Nothing to reach — meshGet would have no candidate either.
        assertFalse(PollGate.shouldPollStatus("", "direct", false));
    }

    @Test
    void nonDirectModeWithAddressesButNoMeshDoesNotPoll() {
        // Stale direct URLs from a previous pairing must not resurrect polling
        // once the slot is no longer in direct mode.
        assertFalse(PollGate.shouldPollStatus("", "relay", true));
    }

    @Test
    void nullMeshIdIsTreatedAsAbsent() {
        assertFalse(PollGate.shouldPollStatus(null, "", false));
        assertTrue(PollGate.shouldPollStatus(null, "direct", true));
    }
}

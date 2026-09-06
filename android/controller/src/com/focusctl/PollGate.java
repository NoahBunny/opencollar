package com.focusctl;

/**
 * Whether Lion's Share currently has any way to fetch the bunny's status.
 *
 * Extracted from MainActivity.startStatusPolling so it can be unit-tested off
 * device — the same reason MeshOrderApply lives outside ControlService.
 *
 * The status poller used to gate on `!meshId.isEmpty()` alone. A Direct (LAN)
 * pairing has no mesh at all — that is its entire selling point ("no account,
 * no server") — so a direct-paired Lion never polled, and the UI sat on
 * whatever it last rendered ("Switched to bunny", $0, no lock) indefinitely
 * while the Collar happily accepted and applied orders. Found on hardware
 * 2026-08-07: the phone showed a stale line while the Collar was locked.
 *
 * meshGet() already serves /mesh/status straight off the Collar in direct mode;
 * only this gate stopped it from ever being asked. Keep the direct-mode
 * condition here identical to meshGet's, so the two can't disagree about what
 * "reachable" means.
 */
public final class PollGate {

    private PollGate() {}

    /**
     * @param meshId         the active mesh id ("" when there is no mesh)
     * @param pairMode       "direct" for a serverless pairing, else relay/mesh
     * @param hasDirectUrls  whether any direct (LAN/Tailscale/.onion) address is known
     */
    public static boolean shouldPollStatus(String meshId, String pairMode, boolean hasDirectUrls) {
        if (meshId != null && !meshId.isEmpty()) return true;
        return "direct".equals(pairMode) && hasDirectUrls;
    }
}

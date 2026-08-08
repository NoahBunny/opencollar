package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.security.KeyPair;
import java.security.KeyPairGenerator;
import java.util.TreeMap;
import org.junit.jupiter.api.BeforeAll;
import org.junit.jupiter.api.Test;

/**
 * Cross-app regression spec for the direct-mode /mesh/status signature.
 *
 * The Collar (com.focuslock.VaultCrypto) signs the flat status core; Lion's
 * Share (com.focusctl.VaultCrypto) rebuilds it from the wire body and verifies.
 * Both real implementations are on this test classpath, so these tests exercise
 * the ACTUAL signer against the ACTUAL verifier over the ACTUAL wire format —
 * the gap that let the shipped code reject every genuine status.
 *
 * The bug: the status body embeds the whole orders document under "orders",
 * which repeats six of the ten signed key names EARLIER in the byte stream.
 * MainActivity rebuilt the core with indexOf-based helpers that take the first
 * match, so it read the orders copies. Five agreed by luck; "paywall" did not
 * (status defaults an unset paywall to "0", orders emits ""), so a freshly
 * paired Collar's status was always dropped as forged.
 *
 * Every test here builds the wire body the way ControlService.handleMeshStatus
 * does — orders first, status fields after — so a future core field that
 * collides with an orders key fails here instead of on a device.
 */
public class StatusCoreTest {

    private static String bunnyPrivB64;
    private static String bunnyPubB64;

    @BeforeAll
    static void genKeys() throws Exception {
        KeyPairGenerator kpg = KeyPairGenerator.getInstance("RSA");
        kpg.initialize(2048);
        KeyPair kp = kpg.generateKeyPair();
        bunnyPrivB64 = java.util.Base64.getEncoder().encodeToString(kp.getPrivate().getEncoded());
        bunnyPubB64 = java.util.Base64.getEncoder().encodeToString(kp.getPublic().getEncoded());
    }

    /** The exact flat map ControlService.handleMeshStatus signs. */
    private static TreeMap<String, Object> core(
            boolean locked, long escapes, String paywall, long timerMs,
            long taskReps, long taskDone, String offer, String offerStatus,
            String subTier, long ordersVersion) {
        TreeMap<String, Object> c = new TreeMap<>();
        c.put("locked", locked);
        c.put("escapes", escapes);
        c.put("paywall", paywall);
        c.put("timer_remaining_ms", timerMs);
        c.put("task_reps", taskReps);
        c.put("task_done", taskDone);
        c.put("offer", offer);
        c.put("offer_status", offerStatus);
        c.put("sub_tier", subTier);
        c.put("orders_version", ordersVersion);
        return c;
    }

    /**
     * Mirror of the /mesh/status body: orders_version, the embedded orders
     * document, the signature, then the signed status fields, then nodes.
     * ordersOverrides supplies the orders copies of the six colliding keys —
     * on a real Collar they come from the same Settings.Global rows, except
     * that buildOrdersJson has no "0" default for an unset paywall.
     */
    private static String wire(TreeMap<String, Object> c, String sig, String ordersPaywall,
                               long ordersTaskReps, long ordersTaskDone,
                               String ordersOffer, String ordersOfferStatus, String ordersSubTier) {
        String orders = "{"
            + "\"lock_active\":" + (Boolean.TRUE.equals(c.get("locked")) ? 1 : 0)
            + ",\"message\":\"\""
            + ",\"task_text\":\"\""
            + ",\"task_reps\":" + ordersTaskReps
            + ",\"task_done\":" + ordersTaskDone
            + ",\"mode\":\"basic\""
            + ",\"paywall\":\"" + ordersPaywall + "\""
            + ",\"paywall_original\":\"\""
            + ",\"unlock_at\":0"
            + ",\"locked_at\":0"
            + ",\"offer\":\"" + ordersOffer + "\""
            + ",\"offer_status\":\"" + ordersOfferStatus + "\""
            + ",\"sub_tier\":\"" + ordersSubTier + "\""
            + ",\"released\":\"\""
            + "}";
        return "{\"orders_version\":" + c.get("orders_version")
            + ",\"orders\":" + orders
            + ",\"signature\":\"" + sig + "\""
            + ",\"locked\":" + c.get("locked")
            + ",\"escapes\":" + c.get("escapes")
            + ",\"paywall\":\"" + c.get("paywall") + "\""
            + ",\"timer_remaining_ms\":" + c.get("timer_remaining_ms")
            + ",\"task_reps\":" + c.get("task_reps")
            + ",\"task_done\":" + c.get("task_done")
            + ",\"offer\":\"" + c.get("offer") + "\""
            + ",\"offer_status\":\"" + c.get("offer_status") + "\""
            + ",\"sub_tier\":\"" + c.get("sub_tier") + "\""
            + ",\"addresses\":[\"192.168.199.42\"],\"port\":8435"
            + ",\"nodes\":{\"sm-s908w\":{\"type\":\"phone\",\"online\":true,\"orders_version\":"
            + c.get("orders_version") + ",\"status\":{\"escapes\":" + c.get("escapes") + "}}}}";
    }

    /** Collar-side signature over the core (com.focuslock.VaultCrypto). */
    private static String sign(TreeMap<String, Object> c) throws Exception {
        return com.focuslock.VaultCrypto.signBlob(c, bunnyPrivB64);
    }

    /** Controller-side check, exactly as MainActivity.verifyStatusSignature does it. */
    private static boolean verify(String body) throws Exception {
        return VaultCrypto.verifySignature(StatusCore.fromWire(body), bunnyPubB64);
    }

    // ── the regression ──

    @Test
    void freshlyPairedCollarStatusVerifies() throws Exception {
        // No balance charged yet: the Collar signs paywall "0" (its default for
        // an unset row) while the orders copy is "". This is the case that made
        // every /mesh/status look forged and froze Lion's Share on $0.
        TreeMap<String, Object> c = core(true, 0, "0", 900_000L, 0, 0, "", "", "", 3);
        String body = wire(c, sign(c), "", 0, 0, "", "", "");
        assertTrue(verify(body), "genuine status from a freshly paired Collar must verify");
    }

    @Test
    void paywallComesFromTopLevelNotOrders() throws Exception {
        TreeMap<String, Object> c = core(true, 0, "0", 900_000L, 0, 0, "", "", "", 3);
        String body = wire(c, sign(c), "", 0, 0, "", "", "");
        assertEquals("0", StatusCore.fromWire(body).get("paywall"));
    }

    @Test
    void noSignedFieldIsShadowedByTheOrdersDocument() throws Exception {
        // Force every colliding orders value to differ from the signed one. A
        // first-match parser reads the orders copies and produces a core that
        // can't verify; a top-level-scoped one is unaffected.
        TreeMap<String, Object> c = core(true, 2, "25", 900_000L, 3, 1, "unlock", "pending", "silver", 7);
        String body = wire(c, sign(c), "999", 99, 98, "decoy", "decoy", "decoy");
        TreeMap<String, Object> rebuilt = StatusCore.fromWire(body);
        assertEquals("25", rebuilt.get("paywall"));
        assertEquals(3L, rebuilt.get("task_reps"));
        assertEquals(1L, rebuilt.get("task_done"));
        assertEquals("unlock", rebuilt.get("offer"));
        assertEquals("pending", rebuilt.get("offer_status"));
        assertEquals("silver", rebuilt.get("sub_tier"));
        assertTrue(verify(body));
    }

    @Test
    void chargedPaywallVerifies() throws Exception {
        TreeMap<String, Object> c = core(true, 1, "25", 840_000L, 0, 0, "", "", "silver", 4);
        String body = wire(c, sign(c), "25", 0, 0, "", "", "silver");
        assertTrue(verify(body));
    }

    @Test
    void rebuiltCoreUsesTheSignersNativeTypes() throws Exception {
        // canonical_json distinguishes true from "true" and 0 from "0" — a type
        // drift here breaks verification just as silently as a value drift.
        TreeMap<String, Object> c = core(false, 5, "10", 0L, 2, 2, "", "", "", 9);
        TreeMap<String, Object> rebuilt = StatusCore.fromWire(wire(c, sign(c), "10", 2, 2, "", "", ""));
        assertTrue(rebuilt.get("locked") instanceof Boolean);
        assertTrue(rebuilt.get("escapes") instanceof Long);
        assertTrue(rebuilt.get("orders_version") instanceof Long);
        assertTrue(rebuilt.get("timer_remaining_ms") instanceof Long);
        assertTrue(rebuilt.get("paywall") instanceof String);
        // MainActivity.updateLiveStatus now reads the DISPLAY values straight out
        // of this map (task_reps/task_done as (Long).intValue(), offer/
        // offer_status/sub_tier as (String)). Pin those native types too, so a
        // future StatusCore edit that changes one can't slip past verification
        // AND silently ClassCastException the status line at render time.
        assertTrue(rebuilt.get("task_reps") instanceof Long);
        assertTrue(rebuilt.get("task_done") instanceof Long);
        assertTrue(rebuilt.get("offer") instanceof String);
        assertTrue(rebuilt.get("offer_status") instanceof String);
        assertTrue(rebuilt.get("sub_tier") instanceof String);
    }

    // ── still fails closed ──

    @Test
    void lanMitmClearingTheLockIsRejected() throws Exception {
        TreeMap<String, Object> c = core(true, 2, "25", 900_000L, 0, 0, "", "", "", 3);
        String sig = sign(c);
        TreeMap<String, Object> forged = core(false, 0, "0", 0L, 0, 0, "", "", "", 3);
        assertFalse(verify(wire(forged, sig, "0", 0, 0, "", "", "")),
            "a spoofed 'unlocked, no fee' status must not verify");
    }

    @Test
    void shadowedOrdersCannotSmuggleAValueIntoTheCore() throws Exception {
        // The inverse attack: a MITM rewrites only the orders copies, hoping the
        // controller reads those. Scoped parsing means the core is untouched, so
        // the signature still verifies and the Lion still sees the real state.
        TreeMap<String, Object> c = core(true, 2, "25", 900_000L, 0, 0, "", "", "", 3);
        String body = wire(c, sign(c), "0", 0, 0, "", "", "");
        assertTrue(verify(body));
        assertEquals("25", StatusCore.fromWire(body).get("paywall"));
    }

    @Test
    void unsignedStatusIsRejected() throws Exception {
        TreeMap<String, Object> c = core(true, 0, "0", 900_000L, 0, 0, "", "", "", 3);
        assertFalse(verify(wire(c, "", "", 0, 0, "", "", "")));
    }

    @Test
    void malformedBodyThrowsSoTheCallerFailsClosed() {
        assertThrows(org.json.JSONException.class, () -> StatusCore.fromWire("not json"));
    }
}

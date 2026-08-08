package com.focusctl;

import java.util.TreeMap;
import org.json.JSONObject;

/**
 * Rebuild the signed security core of a direct-mode /mesh/status body.
 *
 * The Collar (ControlService.handleMeshStatus) signs a flat map of ten runtime
 * fields with its bunny key and ships those fields as TOP-LEVEL keys of the
 * status body. The same body also embeds the whole orders document under
 * "orders" — and six of the ten core names ("paywall", "task_reps",
 * "task_done", "offer", "offer_status", "sub_tier") exist inside it too,
 * EARLIER in the byte stream.
 *
 * MainActivity's indexOf-based parseJson* helpers take the first match, so
 * rebuilding the core with them read the orders copy instead of the signed
 * top-level field. Five of the six agree by luck (both sides read the same
 * Settings.Global row), but "paywall" does not: handleMeshStatus defaults an
 * unset paywall to "0" while buildOrdersJson emits "". On a freshly paired
 * Collar — no balance charged yet — the rebuilt core therefore differed from
 * the signed one by exactly one field, every /mesh/status was dropped as
 * forged, and Lion's Share froze on its last-good snapshot ($0, no lock
 * reflection) even though orders were being delivered and applied.
 *
 * Scoping the rebuild to the top-level JSON object closes the whole class: an
 * embedded document can never shadow a signed field again, no matter which
 * keys the orders schema grows next. The wire format is unchanged, so an
 * updated controller still verifies Collars already in the field.
 *
 * Native types (Boolean / Long / String) MUST match the Collar's signer and
 * Python's canonical_json — see VaultCrypto.canonicalJson.
 */
public final class StatusCore {

    private StatusCore() {}

    /**
     * Parse the ten signed fields plus the wire "signature" out of a status
     * body, reading ONLY top-level keys. The returned map is exactly what
     * VaultCrypto.verifySignature expects: it strips "signature" and
     * canonicalizes the rest.
     *
     * @throws org.json.JSONException if the body isn't a JSON object — the
     *         caller must treat that as a failed verification (fail closed).
     */
    public static TreeMap<String, Object> fromWire(String statusJson) throws org.json.JSONException {
        JSONObject o = new JSONObject(statusJson);
        TreeMap<String, Object> core = new TreeMap<>();
        core.put("locked", o.optBoolean("locked", false));
        core.put("escapes", o.optLong("escapes", 0L));
        core.put("paywall", o.optString("paywall", ""));
        core.put("timer_remaining_ms", o.optLong("timer_remaining_ms", 0L));
        core.put("task_reps", o.optLong("task_reps", 0L));
        core.put("task_done", o.optLong("task_done", 0L));
        core.put("offer", o.optString("offer", ""));
        core.put("offer_status", o.optString("offer_status", ""));
        core.put("sub_tier", o.optString("sub_tier", ""));
        core.put("orders_version", o.optLong("orders_version", 0L));
        core.put("signature", o.optString("signature", ""));
        return core;
    }
}

package net.freehaven.tor.control;

import java.io.IOException;

/**
 * Same-package helper for the two control-port commands jtorctl 0.4.5.7 can't do
 * itself: ADD_ONION with a ClientAuthV3= clause (host side) and
 * ONION_CLIENT_AUTH_ADD (client side). jtorctl's addOnion() has no ClientAuthV3
 * overload, and sendAndWaitForResponse(...) is protected — so this lives in
 * net.freehaven.tor.control to reach it and the package-private ReplyLine fields
 * (status/msg, verified against the pinned jar).
 *
 * Only compiled when FOCUSLOCK_TOR_AAR is set (build.sh excludes it otherwise),
 * so it never breaks the default-off build.
 */
public final class OnionControl {
    private OnionControl() {}

    private static final String SERVICE_ID = "ServiceID=";

    /** Send a full ADD_ONION line; return the published ServiceID (onion without
     *  the ".onion" suffix), or null if the reply carried none. Throws on any
     *  non-2xx status line so the caller can fall back to the relay. */
    public static String addOnion(TorControlConnection c, String cmd) throws IOException {
        String id = null;
        for (TorControlConnection.ReplyLine l : c.sendAndWaitForResponse(cmd + "\r\n", null)) {
            if (l.status == null || !l.status.startsWith("25")) {
                throw new IOException((l.status == null ? "?" : l.status) + " " + l.msg);
            }
            if (l.msg != null && l.msg.startsWith(SERVICE_ID)) {
                id = l.msg.substring(SERVICE_ID.length());
            }
        }
        return id;
    }

    /** Fire-and-check a control command (ONION_CLIENT_AUTH_ADD, DEL_ONION,
     *  SETCONF). Throws on any non-2xx status line. */
    public static void raw(TorControlConnection c, String cmd) throws IOException {
        for (TorControlConnection.ReplyLine l : c.sendAndWaitForResponse(cmd + "\r\n", null)) {
            if (l.status == null || !l.status.startsWith("25")) {
                throw new IOException((l.status == null ? "?" : l.status) + " " + l.msg);
            }
        }
    }
}

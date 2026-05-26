package com.focuslock;

import java.util.ArrayList;

/**
 * Pure helpers for applying a gossiped/vault orders document to the Collar.
 * Kept free of Android dependencies so it can be unit-tested off-device
 * (android/slave/test/.../MeshOrderApplyTest.java).
 */
public final class MeshOrderApply {
    private MeshOrderApply() {}

    /**
     * Order in which order keys are written to Settings.Global: every key EXCEPT
     * "lock_active" first, then "lock_active" last (if present).
     *
     * FocusActivity polls these keys on a 5s loop and is also launched right
     * after they're applied, so writing the lock flag last guarantees that by
     * the instant focus_lock_active flips to 1, message/mode/paywall/task_text
     * already hold their new values — no torn "new lock + old message" render.
     * The result is a permutation of the input (lock_active moved to the end).
     */
    public static String[] orderForApply(String[] keys) {
        ArrayList<String> ordered = new ArrayList<>(keys.length);
        boolean hasActive = false;
        for (String k : keys) {
            if ("lock_active".equals(k)) { hasActive = true; continue; }
            ordered.add(k);
        }
        if (hasActive) ordered.add("lock_active");
        return ordered.toArray(new String[0]);
    }
}

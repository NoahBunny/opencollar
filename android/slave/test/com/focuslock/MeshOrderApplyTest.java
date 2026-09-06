package com.focuslock;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertTrue;

import java.util.Arrays;
import java.util.HashSet;
import java.util.Set;
import org.junit.jupiter.api.Test;

/**
 * Pure-JVM unit tests for MeshOrderApply.orderForApply — the B1 atomic-apply fix.
 * Guards that "lock_active" is always written LAST so FocusActivity never renders
 * a new lock with stale message/mode/paywall. No device, no android.jar.
 */
public class MeshOrderApplyTest {

    @Test
    void lockActiveMovedToEnd() {
        String[] in = {"lock_active", "message", "mode", "paywall"};
        String[] out = MeshOrderApply.orderForApply(in);
        assertEquals("lock_active", out[out.length - 1]);
        assertEquals("message", out[0]);
    }

    @Test
    void isAPermutationOfInput() {
        String[] in = {"lock_active", "message", "mode", "paywall", "task_text", "pinned_message"};
        String[] out = MeshOrderApply.orderForApply(in);
        assertEquals(in.length, out.length);
        Set<String> a = new HashSet<>(Arrays.asList(in));
        Set<String> b = new HashSet<>(Arrays.asList(out));
        assertEquals(a, b);
        assertEquals("lock_active", out[out.length - 1]);
    }

    @Test
    void noLockActiveLeavesSetUnchangedSize() {
        String[] in = {"message", "mode", "paywall"};
        String[] out = MeshOrderApply.orderForApply(in);
        assertEquals(3, out.length);
        assertTrue(Arrays.asList(out).containsAll(Arrays.asList(in)));
    }

    @Test
    void onlyOneLockActiveInOutput() {
        String[] in = {"lock_active", "message", "lock_active"};
        String[] out = MeshOrderApply.orderForApply(in);
        long count = Arrays.stream(out).filter("lock_active"::equals).count();
        assertEquals(1, count);
        assertEquals("lock_active", out[out.length - 1]);
    }
}

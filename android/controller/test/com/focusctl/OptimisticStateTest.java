package com.focusctl;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

import org.junit.jupiter.api.Test;

/**
 * Spec for the "keep showing what the Lion just commanded" machine.
 *
 * <p>This is the code that decides whether the Lion is looking at the truth. It
 * deliberately shows something the snapshot does not yet agree with, so its
 * failure modes are the dangerous kind: a stuck expectation reads as a lock
 * that is holding when it is not, and a too-eager confirmation reads as an
 * order that landed when it was dropped.
 *
 * <p>It had no tests at all before being extracted, because it lived as seven
 * private fields on a 6,000-line Activity that cannot be instantiated off a
 * device. Time is a parameter here rather than a call to the clock, so every
 * window and expiry below is exercised directly instead of by sleeping.
 */
public class OptimisticStateTest {

    private static final long T0 = 1_700_000_000_000L;

    /** A snapshot saying: unlocked, no timer, balance $0. */
    private static OptimisticState.Shown reconcileIdle(OptimisticState s, long now) {
        return s.reconcile(false, 0, 0, 0, now);
    }

    // ── nothing pending ───────────────────────────────────────────────

    @Test
    void passesTheSnapshotThroughWhenNothingIsPending() {
        OptimisticState s = new OptimisticState();
        OptimisticState.Shown out = s.reconcile(true, 60_000, T0 + 60_000, 42, T0);
        assertTrue(out.locked);
        assertEquals(60_000, out.timerMs);
        assertEquals(T0 + 60_000, out.timerEndMs);
        assertEquals(42, out.paywall);
        assertFalse(s.isPending(T0));
    }

    // ── a command overriding a snapshot that has not caught up ────────

    @Test
    void showsTheCommandedLockWhileTheSnapshotStillSaysUnlocked() {
        OptimisticState s = new OptimisticState();
        s.begin(true, true, T0 + 3_600_000, -1, 0, T0);
        OptimisticState.Shown out = reconcileIdle(s, T0 + 1_000);
        assertTrue(out.locked, "the Lion pressed Lock; the UI must not snap back to UNLOCKED");
        assertEquals(T0 + 3_600_000, out.timerEndMs);
        assertEquals(3_599_000, out.timerMs, "remaining time counts down from now, not from the command");
    }

    @Test
    void aCommandedUnlockZeroesTheTimerRatherThanKeepingTheOldOne() {
        OptimisticState s = new OptimisticState();
        s.begin(true, false, 0, -1, 0, T0);
        OptimisticState.Shown out = s.reconcile(true, 900_000, T0 + 900_000, 0, T0 + 1_000);
        assertFalse(out.locked);
        assertEquals(0, out.timerMs);
        assertEquals(0, out.timerEndMs);
    }

    @Test
    void anIndefiniteLockLeavesTheSnapshotTimerAlone() {
        OptimisticState s = new OptimisticState();
        s.begin(true, true, 0, -1, 0, T0);  // locked, no commanded end
        OptimisticState.Shown out = s.reconcile(false, 123_000, T0 + 123_000, 0, T0 + 1_000);
        assertTrue(out.locked);
        assertEquals(123_000, out.timerMs, "no commanded end means the snapshot's timer is not overridden");
    }

    @Test
    void aMoneyOnlyCommandDoesNotTouchLockState() {
        OptimisticState s = new OptimisticState();
        s.begin(false, false, 0, 50, 0, T0);   // hasLock = false
        OptimisticState.Shown out = s.reconcile(true, 60_000, T0 + 60_000, 0, T0 + 1_000);
        assertTrue(out.locked, "adding to the balance must not render the bunny as unlocked");
        assertEquals(60_000, out.timerMs);
        assertEquals(50, out.paywall);
    }

    // ── confirmation ──────────────────────────────────────────────────

    @Test
    void stopsOverridingOnceTheSnapshotAgrees() {
        OptimisticState s = new OptimisticState();
        s.begin(true, true, 0, -1, 0, T0);
        s.reconcile(true, 0, 0, 0, T0 + 1_000);          // snapshot caught up
        assertFalse(s.isPending(T0 + 1_000), "a confirmed expectation must be dropped, not left to time out");
        OptimisticState.Shown out = reconcileIdle(s, T0 + 2_000);
        assertFalse(out.locked, "after confirmation the snapshot is authoritative again");
    }

    @Test
    void aRelockThatOnlyExtendsTheTimerIsNotConfirmedByTheDeviceAlreadyBeingLocked() {
        // The gap the timer slack exists to close: re-locking to extend leaves
        // the device locked either way, so lock state alone would self-confirm
        // instantly and the bar would keep counting down the OLD remaining time.
        OptimisticState s = new OptimisticState();
        s.begin(true, true, T0 + 7_200_000, -1, 0, T0);          // commanded: 2h
        OptimisticState.Shown out = s.reconcile(true, 60_000, T0 + 60_000, 0, T0 + 1_000);  // stale: 1m left
        assertTrue(s.isPending(T0 + 1_000), "the old timer must not count as confirmation");
        assertEquals(T0 + 7_200_000, out.timerEndMs, "the commanded end is what the Lion sees");
    }

    @Test
    void aTimerWithinTheRoundTripSlackDoesConfirm() {
        OptimisticState s = new OptimisticState();
        s.begin(true, true, T0 + 7_200_000, -1, 0, T0);
        long nearlyRight = T0 + 7_200_000 - (OptimisticState.TIMER_SLACK_MS - 1_000);
        s.reconcile(true, nearlyRight - T0, nearlyRight, 0, T0 + 1_000);
        assertFalse(s.isPending(T0 + 1_000), "within slack the snapshot counts as caught up");
    }

    // ── balance, in both directions ───────────────────────────────────

    @Test
    void aRaiseIsConfirmedByAnyBalanceAtOrAboveTheTarget() {
        OptimisticState s = new OptimisticState();
        s.begin(false, false, 0, 50, 0, T0);              // $0 -> $50
        OptimisticState.Shown under = s.reconcile(false, 0, 0, 40, T0 + 1_000);
        assertEquals(50, under.paywall, "a balance below the target has not caught up");
        assertTrue(s.isPending(T0 + 1_000));
        s.reconcile(false, 0, 0, 60, T0 + 2_000);         // a fine landed on top
        assertFalse(s.isPending(T0 + 2_000), "above the target still confirms — the order clearly landed");
    }

    @Test
    void aClearIsConfirmedByAnyBalanceAtOrBelowTheTarget() {
        OptimisticState s = new OptimisticState();
        s.begin(false, false, 0, 0, 50, T0);              // $50 -> $0
        OptimisticState.Shown over = s.reconcile(false, 0, 0, 50, T0 + 1_000);
        assertEquals(0, over.paywall, "the Lion cleared it; keep showing cleared");
        assertTrue(s.isPending(T0 + 1_000));
        s.reconcile(false, 0, 0, 0, T0 + 2_000);
        assertFalse(s.isPending(T0 + 2_000));
    }

    // ── the window ────────────────────────────────────────────────────

    @Test
    void trustsTheSnapshotAgainOnceTheWindowLapses() {
        OptimisticState s = new OptimisticState();
        s.begin(true, true, 0, -1, 0, T0);
        long after = T0 + OptimisticState.WINDOW_MS + 1;
        assertFalse(s.isPending(after));
        OptimisticState.Shown out = reconcileIdle(s, after);
        assertFalse(out.locked, "a silently dropped order must not leave the UI lying forever");
    }

    // ── generations ───────────────────────────────────────────────────

    @Test
    void aFailedOrderCancelsItsOwnExpectation() {
        OptimisticState s = new OptimisticState();
        int gen = s.begin(true, true, 0, -1, 0, T0);
        assertTrue(s.cancel(gen));
        assertFalse(s.isPending(T0 + 1_000), "a failed order must stop the UI lying at once");
    }

    @Test
    void aLateFailureCannotCancelTheCommandThatSupersededIt() {
        OptimisticState s = new OptimisticState();
        int first = s.begin(true, true, 0, -1, 0, T0);
        s.begin(false, false, 0, 99, 0, T0 + 500);        // Lion issues another command
        assertFalse(s.cancel(first), "the stale generation must be ignored");
        assertTrue(s.isPending(T0 + 1_000), "the newer command's expectation survives");
        assertEquals(99, s.reconcile(false, 0, 0, 0, T0 + 1_000).paywall);
    }

    @Test
    void switchingBunnyDropsTheExpectationAndInvalidatesInFlightCancels() {
        // This state is process-wide, not per-bunny: without the reset, an order
        // issued for the previous bunny renders its lock and balance as the new
        // one's.
        OptimisticState s = new OptimisticState();
        int gen = s.begin(true, true, T0 + 60_000, 50, 0, T0);
        s.reset();
        assertFalse(s.isPending(T0 + 1_000));
        OptimisticState.Shown out = reconcileIdle(s, T0 + 1_000);
        assertFalse(out.locked, "the previous bunny's lock must not be painted onto the new one");
        assertEquals(0, out.paywall, "nor their balance");
        assertFalse(s.cancel(gen), "a late failure from the old slot must not apply to the new one");
    }
}

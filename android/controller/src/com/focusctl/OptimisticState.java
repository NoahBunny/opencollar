package com.focusctl;

/**
 * The "keep showing what the Lion just commanded" state machine, lifted out of
 * MainActivity.
 *
 * <p>The problem it solves: the Lion presses Lock, the order goes to the relay,
 * and the next runtime snapshot still says UNLOCKED because the Collar has not
 * polled yet. Without this, the status bar snaps back to UNLOCKED / $0 for a
 * few seconds and the Lion cannot tell a slow round-trip from a failed order.
 *
 * <p>So a command records what it expects to become true, and the UI shows that
 * instead of the snapshot until one of three things happens:
 * <ul>
 *   <li>the snapshot catches up — the expectation is confirmed and dropped;
 *   <li>the order fails — the caller cancels it and the UI stops lying at once;
 *   <li>{@link #WINDOW_MS} elapses — the snapshot is trusted again regardless,
 *       so a silently-dropped order cannot leave the UI wrong forever.
 * </ul>
 *
 * <p><b>Why it is worth its own class:</b> it decides whether the Lion is being
 * shown the truth about a lock and a balance — and it is the one piece of this
 * Activity whose bugs look exactly like enforcement working when it is not.
 * As private fields on an Activity it could not be tested off-device at all.
 *
 * <p>Not thread-safe by design: every entry point is called on the UI thread,
 * the same contract the fields had inside MainActivity. {@code cancel} is the
 * exception, and is why generations exist.
 */
final class OptimisticState {

    /** How long a command is allowed to override the snapshot. Long enough for
     *  a relay round-trip plus a Collar poll, short enough that a dropped order
     *  self-corrects while the Lion is still looking at the screen. */
    static final long WINDOW_MS = 45000;

    /** Slack when confirming a commanded timer against the snapshot's. A
     *  re-lock that only extends the timer leaves the device already locked, so
     *  lock state alone would self-confirm instantly and the bar would keep
     *  counting down the OLD remaining time — the exact gap this closes. */
    static final long TIMER_SLACK_MS = 90000L;

    private long untilMs = 0;          // 0 = nothing pending
    private boolean hasLock = false;   // did the command change lock state?
    private boolean locked = false;    // expected lock state
    private long timerEndMs = 0;       // expected timer end (0 = none/indefinite)
    private int paywall = -1;          // expected balance (-1 = no expectation)
    private boolean paywallRaise = false;
    private int gen = 0;

    /** What the UI should render for this snapshot. */
    static final class Shown {
        final boolean locked;
        final long timerMs;
        final long timerEndMs;
        final int paywall;

        Shown(boolean locked, long timerMs, long timerEndMs, int paywall) {
            this.locked = locked;
            this.timerMs = timerMs;
            this.timerEndMs = timerEndMs;
            this.paywall = paywall;
        }
    }

    /**
     * Record a just-issued command. Call before firing the request.
     *
     * @param paywallTarget expected balance, or -1 for "no expectation"
     * @param currentPaywall the last confirmed balance, used to decide whether
     *     the snapshot confirming this means "at least" or "at most" — a raise
     *     is confirmed by any value ≥ target, a drop by any value ≤ target
     * @return the generation of this command, to hand back to {@link #cancel}
     */
    int begin(boolean hasLock, boolean locked, long timerEndMsExpected, int paywallTarget, int currentPaywall, long nowMs) {
        this.hasLock = hasLock;
        this.locked = locked;
        this.timerEndMs = timerEndMsExpected;
        this.paywall = paywallTarget;
        this.paywallRaise = paywallTarget < 0 || paywallTarget >= currentPaywall;
        this.untilMs = nowMs + WINDOW_MS;
        return ++gen;
    }

    /**
     * Drop the pending expectation because the order failed.
     *
     * <p>Ignored when a newer command has superseded this one, so a late-failing
     * order cannot cancel an unrelated command the Lion issued after it — the
     * reason a bare boolean would not do.
     *
     * @return true if this cancellation applied
     */
    boolean cancel(int generation) {
        if (generation != gen) return false;
        untilMs = 0;
        return true;
    }

    /** True while a command is still overriding the snapshot. */
    boolean isPending(long nowMs) {
        return nowMs < untilMs;
    }

    /**
     * Abandon any pending expectation, and invalidate outstanding generations
     * so a late failure from before the reset cannot cancel anything after it.
     *
     * <p>Used when the active bunny slot changes. This state is process-wide,
     * not per-bunny: without the reset, an order just issued for the previous
     * bunny would be reconciled against the NEW bunny's snapshot and render the
     * previous one's lock, timer and balance as though they were theirs.
     */
    void reset() {
        untilMs = 0;
        gen++;
    }

    /**
     * Reconcile a fresh snapshot against the pending expectation.
     *
     * <p>Returns the snapshot untouched when nothing is pending or when the
     * snapshot has caught up (clearing the expectation in the latter case).
     * Otherwise returns the commanded values, so the UI keeps showing them.
     */
    Shown reconcile(boolean snapLocked, long snapTimerMs, long snapTimerEndMs, int snapPaywall, long nowMs) {
        Shown snapshot = new Shown(snapLocked, snapTimerMs, snapTimerEndMs, snapPaywall);
        if (!isPending(nowMs)) return snapshot;

        boolean lockOk = !hasLock
            || (snapLocked == locked
                && (!locked || timerEndMs <= 0
                    || Math.abs(snapTimerEndMs - timerEndMs) < TIMER_SLACK_MS));
        boolean payOk = paywall < 0
            || (paywallRaise ? snapPaywall >= paywall : snapPaywall <= paywall);

        if (lockOk && payOk) {
            untilMs = 0; // confirmed — the snapshot caught up
            return snapshot;
        }

        boolean outLocked = snapLocked;
        long outTimerMs = snapTimerMs;
        long outTimerEnd = snapTimerEndMs;
        if (hasLock) {
            outLocked = locked;
            if (locked) {
                // An indefinite lock (no commanded end) leaves the snapshot's
                // timer alone rather than zeroing it.
                if (timerEndMs > 0) {
                    outTimerEnd = timerEndMs;
                    outTimerMs = Math.max(0, timerEndMs - nowMs);
                }
            } else {
                outTimerEnd = 0;
                outTimerMs = 0;
            }
        }
        int outPaywall = paywall >= 0 ? paywall : snapPaywall;
        return new Shown(outLocked, outTimerMs, outTimerEnd, outPaywall);
    }
}

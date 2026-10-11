package com.ironwill.app;

/** Pure local decision state. All times are milliseconds in the same monotonic clock. */
final class ReminderGate {
    private String candidate = "";
    private long enteredAt, nextAttempt;
    boolean evaluate(String pkg, boolean redline, boolean active, boolean interactive,
                     boolean notifications, long now, long quietRemaining,
                     boolean pending, long pendingCreated) {
        if (!active || !interactive || pkg == null || pkg.isEmpty() || !redline || quietRemaining > 0) {
            candidate = ""; return false;
        }
        if (!pkg.equals(candidate) || now < enteredAt) { candidate = pkg; enteredAt = now; return false; }
        if (!notifications || now - enteredAt < 60_000L || now < nextAttempt) return false;
        if (pending && (now < pendingCreated || now - pendingCreated < 5 * 60_000L)) return false;
        return true;
    }
    void defer(long now, long duration) { nextAttempt = now + duration; }
    void resetObservation() { candidate = ""; }
}

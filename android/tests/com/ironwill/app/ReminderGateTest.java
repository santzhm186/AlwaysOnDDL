package com.ironwill.app;

public final class ReminderGateTest {
    private static int passed;
    private static void check(boolean actual, boolean expected, String label) {
        if (actual != expected) throw new AssertionError(label + ": expected " + expected + " but got " + actual);
        passed++;
    }
    private static boolean tick(ReminderGate g, String pkg, boolean red, long now) {
        return g.evaluate(pkg, red, true, true, true, now, 0, false, 0);
    }
    public static void main(String[] args) {
        ReminderGate g = new ReminderGate();
        check(tick(g, "app.a", true, 1000), false, "first observation does not remind");
        check(tick(g, "app.a", true, 60999), false, "under dwell threshold");
        check(tick(g, "app.a", true, 61000), true, "threshold reached");
        g.defer(61000, 120000);
        check(tick(g, "app.a", true, 180999), false, "cooldown boundary");
        check(tick(g, "app.a", true, 181000), true, "cooldown elapsed");
        check(tick(g, "app.b", true, 200000), false, "switch starts fresh dwell");
        check(tick(g, "app.b", true, 260000), true, "new app dwell");
        check(tick(g, "work", false, 261000), false, "non-redline resets observation");
        check(tick(g, "app.b", true, 300000), false, "return after non-redline resets dwell");
        check(g.evaluate("app.b", true, true, false, true, 360000, 0, false, 0), false, "screen off cannot remind");
        check(tick(g, "app.b", true, 900000), false, "wake starts fresh");
        check(g.evaluate("app.b", true, true, true, false, 960000, 0, false, 0), false, "notification unavailable");
        check(g.evaluate("app.b", true, true, true, true, 960000, 500, false, 0), false, "user snooze");
        check(tick(g, "app.b", true, 960500), false, "after snooze fresh dwell");
        check(g.evaluate("app.b", true, true, true, true, 1020500, 0, true, 1000000), false, "pending reminder is not duplicated");
        check(g.evaluate("app.b", true, true, true, true, 1300000, 0, true, 1000000), true, "stale pending can be replaced");
        check(g.evaluate("app.b", true, false, true, true, 1301000, 0, false, 0), false, "paused cycle");
        check(tick(g, "", true, 2000000), false, "missing observation");
        check(tick(g, "app.a", true, 86459000), false, "new observation before arbitrary day boundary");
        check(tick(g, "app.a", true, 86519000), true, "no calendar reset");
        check(tick(g, "app.a", true, 100), false, "clock reset is not elapsed dwell");
        check(RelayHttpPolicy.acceptsStatus(200), true, "relay completed response");
        check(RelayHttpPolicy.acceptsStatus(202), true, "relay action queued response is success");
        check(RelayHttpPolicy.acceptsStatus(301), false, "redirect must not be accepted");
        check(RelayHttpPolicy.acceptsStatus(401), false, "authentication failure");
        check(RelayHttpPolicy.acceptsStatus(500), false, "server failure");
        System.out.println("Passed " + passed + " ReminderGate and relay protocol assertions.");
    }
}

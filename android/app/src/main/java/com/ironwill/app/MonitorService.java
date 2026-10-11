// Copyright (C) 2026 IronWill contributors
// SPDX-License-Identifier: GPL-3.0-only
package com.ironwill.app;

import android.app.*;
import android.app.usage.UsageEvents;
import android.app.usage.UsageStatsManager;
import android.content.*;
import android.os.*;
import java.util.UUID;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public final class MonitorService extends Service {
    static final int SESSION_ID = 1001, CHALLENGE_ID = 1002;
    static final String CHANNEL_SESSION = "session_v1", CHANNEL_CHALLENGE = "challenge_v1";
    static volatile boolean running;
    static volatile String observed = "尚未观察到应用";
    static volatile String syncStatus = "独立运行";
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final ExecutorService network = Executors.newSingleThreadExecutor();
    private Store store;
    private final ReminderGate gate = new ReminderGate();
    private long lastNetwork;
    private long invalidateBeforeElapsed;
    private long lastUsageQueryWall, usageBoundaryElapsed;
    private String cachedUsagePackage = "";
    private boolean wasInteractive;
    private boolean claimPending;
    private boolean destroyed;
    private final Runnable tick = new Runnable() {
        public void run() { try { inspect(); } catch (Exception ignored) { observed = "观察暂不可用"; } if (running) handler.postDelayed(this, 2500); }
    };
    public static void channels(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel(CHANNEL_SESSION, "监督运行状态", NotificationManager.IMPORTANCE_LOW));
        NotificationChannel challenge = new NotificationChannel(CHANNEL_CHALLENGE, "IronWill 提醒", NotificationManager.IMPORTANCE_HIGH);
        challenge.setDescription("根据你选择的红线应用发出提醒；可桥接手表");
        challenge.enableVibration(true);
        nm.createNotificationChannel(challenge);
    }
    static boolean notificationsAvailable(Context c) {
        NotificationManager nm = c.getSystemService(NotificationManager.class);
        NotificationChannel channel = nm.getNotificationChannel(CHANNEL_CHALLENGE);
        return nm.areNotificationsEnabled() && channel != null && channel.getImportance() != NotificationManager.IMPORTANCE_NONE;
    }
    static PendingIntent openApp(Context c) {
        return PendingIntent.getActivity(c, 0, new Intent(c, MainActivity.class), PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
    }
    @Override public void onCreate() { super.onCreate(); store = new Store(this); channels(this); }
    @Override public int onStartCommand(Intent intent, int flags, int startId) {
        if (!store.active()) { stopSelf(); return START_NOT_STICKY; }
        Notification n = new Notification.Builder(this, CHANNEL_SESSION).setSmallIcon(R.drawable.ic_ironwill)
                .setContentTitle("IronWill 正在执行你的约定").setContentText("点击查看状态、暂停或结束本轮")
                .setContentIntent(openApp(this)).setOngoing(true).setLocalOnly(true).build();
        try {
            if (Build.VERSION.SDK_INT >= 34) startForeground(SESSION_ID, n, android.content.pm.ServiceInfo.FOREGROUND_SERVICE_TYPE_SPECIAL_USE);
            else startForeground(SESSION_ID, n);
        } catch (Exception e) { store.log("health", "系统拒绝启动后台监测；请在应用内重试"); stopSelf(); return START_NOT_STICKY; }
        if (!running) { running = true; handler.post(tick); }
        return START_STICKY;
    }
    private String currentPackage() {
        long now = SystemClock.elapsedRealtime();
        if (PackageObserverService.connected != null && PackageObserverService.latestElapsed >= invalidateBeforeElapsed)
            return PackageObserverService.latestPackage;
        if (!MainActivity.hasUsageAccess(this)) return "";
        UsageStatsManager manager = getSystemService(UsageStatsManager.class);
        long wall = System.currentTimeMillis();
        long oldestTrustedWall = wall - Math.max(0, now - invalidateBeforeElapsed);
        if (usageBoundaryElapsed != invalidateBeforeElapsed || lastUsageQueryWall > wall) {
            lastUsageQueryWall = 0; cachedUsagePackage = ""; usageBoundaryElapsed = invalidateBeforeElapsed;
        }
        long from = lastUsageQueryWall == 0 ? Math.max(wall - 60_000L, oldestTrustedWall) : Math.max(lastUsageQueryWall - 1000L, oldestTrustedWall);
        UsageEvents events = manager.queryEvents(from, wall);
        if (events == null) return "";
        String found = cachedUsagePackage; UsageEvents.Event event = new UsageEvents.Event();
        while (events.hasNextEvent()) {
            events.getNextEvent(event);
            if (event.getTimeStamp() < oldestTrustedWall) continue;
            if (event.getEventType() == UsageEvents.Event.MOVE_TO_FOREGROUND) found = event.getPackageName();
            if (event.getEventType() == UsageEvents.Event.MOVE_TO_BACKGROUND && event.getPackageName().equals(found)) found = "";
        }
        lastUsageQueryWall = wall; cachedUsagePackage = found; return found;
    }
    private void inspect() {
        if (!store.active()) { stopSelf(); return; }
        long now = SystemClock.elapsedRealtime();
        SyncClient sync = new SyncClient(this);
        if (sync.enabled() && now - lastNetwork > 15_000) {
            lastNetwork = now;
            network.execute(() -> { try { sync.heartbeat(); syncStatus = "已连接电脑（尽力同步）"; } catch (Exception e) { syncStatus = "电脑离线 · 本地提醒继续"; } });
        } else if (!sync.enabled()) syncStatus = "独立运行";
        PowerManager power = getSystemService(PowerManager.class);
        KeyguardManager keyguard = getSystemService(KeyguardManager.class);
        if (!power.isInteractive() || keyguard.isKeyguardLocked()) {
            gate.resetObservation(); invalidateBeforeElapsed = now; wasInteractive = false;
            observed = "屏幕休息或锁定 · 不累计偏离"; return;
        }
        if (!wasInteractive) { gate.resetObservation(); wasInteractive = true; }
        String pkg = currentPackage();
        observed = pkg.isEmpty() ? "观察不可用，请检查权限" : pkg;
        if (!gate.evaluate(pkg, !pkg.equals(getPackageName()) && store.blocked().contains(pkg), store.active(), true,
                notificationsAvailable(this), now, store.quietRemainingMs(), !store.text("pendingId").isEmpty(), store.p.getLong("pendingCreatedElapsed", 0)) || claimPending) return;
        final String id = UUID.randomUUID().toString();
        final String cycle = store.text("cycleId");
        final String candidatePackage = pkg;
        if (!sync.enabled()) { showChallenge(this, id); gate.defer(now, 2 * 60_000L); return; }
        claimPending = true;
        network.execute(() -> {
            boolean granted = true;
            try { granted = sync.claim(id); } catch (Exception e) { syncStatus = "电脑离线 · 本地轻提醒"; }
            final boolean show = granted;
            handler.post(() -> {
                claimPending = false;
                gate.defer(SystemClock.elapsedRealtime(), show ? 2 * 60_000L : 30_000L);
                if (show && !destroyed && running && store.active() && cycle.equals(store.text("cycleId")) && store.quietRemainingMs() == 0
                        && getSystemService(PowerManager.class).isInteractive() && !getSystemService(KeyguardManager.class).isKeyguardLocked()
                        && candidatePackage.equals(currentPackage())) showChallenge(this, id);
            });
        });
    }
    static void showChallenge(Context c, String id) {
        channels(c);
        if (!notificationsAvailable(c)) return;
        Store s = new Store(c);
        s.p.edit().putString("pendingId", id).putString("pendingCycle", s.text("cycleId")).putLong("pendingCreatedElapsed", SystemClock.elapsedRealtime()).commit();
        String personal = !s.text("consequence").isEmpty() ? "\n你写下的后果：" + s.text("consequence") : !s.text("reality").isEmpty() ? "\n你写下的处境：" + s.text("reality") : "";
        Notification.Builder b = new Notification.Builder(c, CHANNEL_CHALLENGE).setSmallIcon(R.drawable.ic_ironwill)
                .setContentTitle("最好你现在做的事情是对的")
                .setContentText("当前主线：" + s.text("task"))
                .setStyle(new Notification.BigTextStyle().bigText("当前主线：" + s.text("task") + personal + "\n这是你自己作出的选择。现在重新选一次。"))
                .setContentIntent(openApp(c)).setAutoCancel(false).setVisibility(Notification.VISIBILITY_PRIVATE)
                .setPublicVersion(new Notification.Builder(c, CHANNEL_CHALLENGE).setSmallIcon(R.drawable.ic_ironwill).setContentTitle("IronWill 提醒").setContentText("回到你的约定").build());
        String[] choices = {"return", "worth", "breath"};
        String[] labels = {"返回主线", "值得 · 15分", "喘息 · 15分"};
        for (int i = 0; i < choices.length; i++) {
            Intent action = new Intent(c, ActionReceiver.class).putExtra("choice", choices[i]).putExtra("interventionId", id);
            action.setAction("com.ironwill.ANSWER." + id + "." + choices[i]);
            PendingIntent pending = PendingIntent.getBroadcast(c, i + 10, action, PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE);
            b.addAction(new Notification.Action.Builder(null, labels[i], pending).build());
        }
        c.getSystemService(NotificationManager.class).notify(CHALLENGE_ID, b.build());
        s.log("reminder", "发出提醒（不代表用户已看到）");
    }
    @Override public void onDestroy() {
        destroyed = true; running = false; handler.removeCallbacksAndMessages(null); network.shutdownNow();
        getSystemService(NotificationManager.class).cancel(CHALLENGE_ID);
        super.onDestroy();
    }
    @Override public IBinder onBind(Intent intent) { return null; }
}

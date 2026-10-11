// Copyright (C) 2026 IronWill contributors
// SPDX-License-Identifier: GPL-3.0-only
package com.ironwill.app;

import android.content.Context;
import android.content.SharedPreferences;
import android.os.SystemClock;
import org.json.JSONArray;
import org.json.JSONObject;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.HashSet;
import java.util.Locale;
import java.util.Set;
import java.util.UUID;

final class Store {
    final SharedPreferences p;
    Store(Context c) { p = c.getSharedPreferences("ironwill_local_v1", Context.MODE_PRIVATE); }
    String text(String k) { return p.getString(k, ""); }
    String phase() { return p.getString("phase", "idle"); }
    boolean active() { return "active".equals(phase()); }
    void savePlan(String vow, String task, String blocked) {
        p.edit().putString("vow", vow).putString("task", task).putString("blocked", blocked).commit();
    }
    synchronized void start() {
        SharedPreferences.Editor e = p.edit();
        if (text("cycleId").isEmpty() || "ended".equals(phase()) || "idle".equals(phase())) {
            e.putString("cycleId", UUID.randomUUID().toString()).putLong("startedUtc", System.currentTimeMillis());
        }
        e.putString("phase", "active").putLong("quietUntilElapsed", 0).putLong("quietSetElapsed", 0).putString("pendingId", "").putString("pendingCycle", "").commit();
        log("cycle", "开始或继续执行周期");
    }
    void pause() { p.edit().putString("phase", "paused").putString("pendingId", "").commit(); log("cycle", "用户暂停"); }
    void end() { p.edit().putString("phase", "ended").putLong("quietUntilElapsed", 0).putString("pendingId", "").commit(); log("cycle", "用户结束本轮"); }
    Set<String> blocked() {
        Set<String> result = new HashSet<>();
        for (String s : text("blocked").split("[\\s,;，；]+")) if (!s.trim().isEmpty()) result.add(s.trim());
        return result;
    }
    long quietRemainingMs() {
        long now = SystemClock.elapsedRealtime();
        long began = p.getLong("quietSetElapsed", 0);
        if (now < began) { p.edit().putLong("quietUntilElapsed", 0).commit(); return 0; }
        return Math.max(0, p.getLong("quietUntilElapsed", 0) - now);
    }
    synchronized boolean answer(String expectedId, String choice) {
        if (expectedId == null || expectedId.isEmpty() || !expectedId.equals(text("pendingId"))) return false;
        long now = SystemClock.elapsedRealtime();
        if (!active() || !text("cycleId").equals(text("pendingCycle")) || now < p.getLong("pendingCreatedElapsed", 0) ||
                now - p.getLong("pendingCreatedElapsed", 0) > 20 * 60_000L) return false;
        long duration = "return".equals(choice) ? 2 * 60_000L : 15 * 60_000L;
        p.edit().putString("pendingId", "").putLong("quietSetElapsed", now)
                .putLong("quietUntilElapsed", now + duration).putString("quietReason", choice).commit();
        log("answer", "return".equals(choice) ? "用户选择返回主线" : "worth".equals(choice) ? "用户确认值得，15 分钟后再检查" : "用户选择喘息，15 分钟后再检查");
        return true;
    }
    synchronized void log(String kind, String message) {
        try {
            JSONArray old = new JSONArray(p.getString("events", "[]"));
            JSONArray next = new JSONArray();
            JSONObject event = new JSONObject().put("id", UUID.randomUUID().toString()).put("utc", System.currentTimeMillis())
                    .put("kind", kind).put("message", message);
            next.put(event);
            for (int i = 0; i < Math.min(199, old.length()); i++) next.put(old.get(i));
            p.edit().putString("events", next.toString()).commit();
        } catch (Exception ignored) { }
    }
    String history() {
        try {
            JSONArray events = new JSONArray(p.getString("events", "[]"));
            StringBuilder b = new StringBuilder();
            SimpleDateFormat fmt = new SimpleDateFormat("MM-dd HH:mm", Locale.getDefault());
            for (int i = 0; i < Math.min(80, events.length()); i++) {
                JSONObject e = events.getJSONObject(i);
                b.append(fmt.format(new Date(e.optLong("utc")))).append("  ").append(e.optString("message")).append("\n\n");
            }
            return b.length() == 0 ? "还没有记录。" : b.toString();
        } catch (Exception e) { return "记录暂不可读。"; }
    }
}

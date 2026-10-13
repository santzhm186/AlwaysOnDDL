// Copyright (C) 2026 IronWill contributors
// SPDX-License-Identifier: GPL-3.0-only
package com.ironwill.app;

import android.Manifest;
import android.accessibilityservice.AccessibilityService;
import android.app.*;
import android.content.*;
import android.content.pm.*;
import android.graphics.Color;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.graphics.drawable.RippleDrawable;
import android.graphics.drawable.StateListDrawable;
import android.content.res.ColorStateList;
import android.os.*;
import android.provider.Settings;
import android.text.InputType;
import android.view.*;
import android.widget.*;
import org.json.JSONObject;
import java.util.*;

public final class MainActivity extends Activity {
    private static final int BG = Color.rgb(19, 23, 22), FG = Color.rgb(237, 240, 231), MUTED = Color.rgb(168, 180, 170), ACCENT = Color.rgb(215, 240, 108);
    private static final int PANEL = Color.rgb(28, 35, 32), FIELD = Color.rgb(20, 26, 23), LINE = Color.rgb(53, 65, 57);
    private Store store;
    private LinearLayout content, page;
    private EditText vow, task, reality, consequence;
    private TextView state, stateTitle, blockedSummary;
    private Button startButton;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable refresh = new Runnable() { public void run() { updateState(); handler.postDelayed(this, 2000); } };
    @Override public void onCreate(Bundle saved) {
        super.onCreate(saved); store = new Store(this); MonitorService.channels(this);
        ScrollView scroll = new ScrollView(this); scroll.setFillViewport(true); scroll.setBackgroundColor(BG);
        scroll.setClipToPadding(false);
        content = new LinearLayout(this); content.setOrientation(LinearLayout.VERTICAL); content.setPadding(dp(20), dp(24), dp(20), dp(32)); scroll.addView(content);
        page = content;
        setContentView(scroll);
        scroll.setOnApplyWindowInsetsListener((v, insets) -> {
            int top, bottom;
            if (Build.VERSION.SDK_INT >= 30) { android.graphics.Insets bars = insets.getInsets(WindowInsets.Type.systemBars()); top = bars.top; bottom = bars.bottom; }
            else { top = insets.getSystemWindowInsetTop(); bottom = insets.getSystemWindowInsetBottom(); }
            page.setPadding(dp(20), top + dp(24), dp(20), bottom + dp(32)); return insets;
        });
        TextView brand = label("IRONWILL", 13, ACCENT, true); brand.setLetterSpacing(0.12f);
        label("把决定，\n变成行动。", 32, FG, true).setAccessibilityHeading(true);
        label("只专注于这一轮。\n决定由你作出，提醒按约执行。", 15, MUTED, false);
        card("当前周期", null);
        stateTitle = label("尚未开始", 21, ACCENT, true);
        state = label("", 13, MUTED, false);
        content = page;

        card("01  /  当前主线", "写下这一轮已经决定要做的事。");
        task = input("这一轮你选择做什么？", store.text("task"), 2);
        section("你的承诺");
        vow = input("把你愿意遵守的承诺写在这里", store.text("vow"), 2);
        startButton = button("开始这一轮  →", this::startCycle, true);
        LinearLayout row = new LinearLayout(this); row.setOrientation(LinearLayout.HORIZONTAL); content.addView(row);
        smallButton(row, "暂停", () -> { save(); store.pause(); stopService(new Intent(this, MonitorService.class)); updateState(); });
        smallButton(row, "结束本轮", () -> new AlertDialog.Builder(this).setTitle("结束本轮？")
                .setMessage("记录由你结束本轮。不会按午夜清零，也不会评判你的成果。")
                .setNegativeButton("继续", null).setPositiveButton("结束", (d, w) -> { save(); store.end(); stopService(new Intent(this, MonitorService.class)); updateState(); }).show());
        content = page;

        card("02  /  本轮边界", "只根据你选择的应用，不阅读正文，也不评判成果。");
        blockedSummary = label("", 14, MUTED, false); updateBlocked();
        button("选择红线应用", this::selectApps, false);
        label("连续停留约 60 秒后提醒。", 12, MUTED, false);
        content = page;

        card("03  /  你自己的原话", "现实与后果只留在手机，按你自己的判断填写。");
        section("现在的现实");
        reality = input("你正在面对什么？（可选）", store.text("reality"), 2);
        section("拖延的后果");
        consequence = input("继续拖延会有什么后果？（可选）", store.text("consequence"), 2);
        content = page;

        card("提醒出现时，重新选择", null);
        label("返回主线：两分钟后再检查。\n这件事值得 / 喘口气：十五分钟后再检查。\n无回应不会自动锁屏，也不会被记成违约。", 14, MUTED, false);
        button("处理当前提醒", this::answerDialog, false);
        content = page;

        card("能力与设备", "按需授权，随时可以在系统设置关闭。");
        button("通知权限与提醒测试  ›", this::notificationOptions, false);
        button("活动感知权限  ›", this::observationOptions, false);
        button("电脑配对与任务导入（可选）  ›", this::computerOptions, false);
        button("本地记录  ›", () -> showText("本地记录", store.history()), false);
        button("关于与许可  ›", this::showLicense, false);
        button("手动锁屏能力测试（一次）  ›", this::testLock, false);
        content = page;
        label("IRONWILL · 0.1.2\n无固定作息 · 默认不锁屏 · 不采集正文\n自动强约束与终局尚未启用。系统可停止后台服务；应用会如实显示可用能力。", 12, MUTED, false);
        updateState();
    }
    private int dp(int n) { return Math.round(n * getResources().getDisplayMetrics().density); }
    private GradientDrawable surface(int color, int stroke, int radius) {
        GradientDrawable shape = new GradientDrawable(); shape.setColor(color); shape.setCornerRadius(dp(radius));
        if (stroke != 0) shape.setStroke(dp(1), stroke); return shape;
    }
    private void card(String title, String subtitle) {
        LinearLayout box = new LinearLayout(this); box.setOrientation(LinearLayout.VERTICAL);
        box.setPadding(dp(18), dp(20), dp(18), dp(12)); box.setBackground(surface(PANEL, LINE, 18));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.bottomMargin = dp(16); page.addView(box, lp);
        content = box;
        label(title, 17, FG, true).setAccessibilityHeading(true);
        if (subtitle != null) label(subtitle, 13, MUTED, false);
    }
    private TextView label(String value, int size, int color, boolean bold) {
        TextView t = new TextView(this); t.setText(value); t.setTextColor(color); t.setTextSize(size); t.setLineSpacing(dp(4), 1f);
        if (bold) t.setTypeface(Typeface.DEFAULT, Typeface.BOLD);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.bottomMargin = dp(14); t.setLayoutParams(lp); content.addView(t); return t;
    }
    private void section(String title) { TextView t = label(title, 14, FG, true); t.setAccessibilityHeading(true); ((LinearLayout.LayoutParams)t.getLayoutParams()).topMargin = dp(4); }
    private EditText input(String hint, String value, int lines) {
        EditText e = new EditText(this); e.setHint(hint); e.setHintTextColor(MUTED); e.setTextColor(FG); e.setTextSize(16); e.setText(value);
        e.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE | InputType.TYPE_TEXT_FLAG_CAP_SENTENCES); e.setMinLines(lines); e.setMaxLines(lines + 2); e.setGravity(Gravity.TOP);
        e.setFilters(new android.text.InputFilter[]{new android.text.InputFilter.LengthFilter(2000)});
        e.setPadding(dp(14), dp(12), dp(14), dp(12));
        StateListDrawable background = new StateListDrawable();
        background.addState(new int[]{android.R.attr.state_focused}, surface(FIELD, ACCENT, 10));
        background.addState(new int[]{}, surface(FIELD, LINE, 10)); e.setBackground(background);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.bottomMargin = dp(14); content.addView(e, lp); return e;
    }
    private Button styledButton(String text, boolean primary) {
        Button b = new Button(this); b.setText(text); b.setAllCaps(false); b.setTextSize(14);
        b.setPadding(dp(14), dp(12), dp(14), dp(12)); b.setMinHeight(dp(52)); b.setMinimumHeight(dp(52));
        b.setTextColor(primary ? BG : FG); b.setTypeface(Typeface.DEFAULT, primary ? Typeface.BOLD : Typeface.NORMAL);
        StateListDrawable faces = new StateListDrawable();
        int fill = primary ? ACCENT : Color.rgb(41, 51, 45);
        faces.addState(new int[]{android.R.attr.state_focused}, surface(fill, primary ? FG : ACCENT, 10));
        faces.addState(new int[]{}, surface(fill, 0, 10));
        b.setBackground(new RippleDrawable(ColorStateList.valueOf(primary ? 0x22000000 : 0x22ffffff), faces, surface(Color.WHITE, 0, 10)));
        b.setElevation(0); b.setStateListAnimator(null); return b;
    }
    private Button button(String text, Runnable action, boolean primary) {
        Button b = styledButton(text, primary);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(-1, -2); lp.bottomMargin = dp(10); content.addView(b, lp); b.setOnClickListener(v -> action.run()); return b;
    }
    private void smallButton(LinearLayout parent, String text, Runnable action) {
        Button b = styledButton(text, false);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, -2, 1); lp.bottomMargin = dp(10);
        if (parent.getChildCount() > 0) lp.leftMargin = dp(10);
        parent.addView(b, lp); b.setOnClickListener(v -> action.run());
    }
    private void save() {
        if (vow == null || task == null) return;
        store.savePlan(vow.getText().toString().trim(), task.getText().toString().trim(), store.text("blocked"));
        store.p.edit().putString("reality", reality.getText().toString().trim()).putString("consequence", consequence.getText().toString().trim()).commit();
    }
    private void updateBlocked() {
        Set<String> pkgs = store.blocked();
        if (pkgs.isEmpty()) { blockedSummary.setText("尚未选择；你可以先设置，再开始。"); return; }
        StringBuilder b = new StringBuilder();
        for (String p : pkgs) { try { b.append(getPackageManager().getApplicationLabel(getPackageManager().getApplicationInfo(p, 0))); } catch (Exception e) { b.append(p); } b.append("  "); }
        blockedSummary.setText(b.toString());
    }
    private void updateState() {
        if (state == null) return;
        String p = store.phase(); String title = "active".equals(p) ? (MonitorService.running ? "这一轮正在进行" : "周期已保存 · 监测未运行，请点击继续") : "paused".equals(p) ? "已暂停 · 等你继续" : "ended".equals(p) ? "这一轮已结束" : "尚未开始";
        long quiet = store.quietRemainingMs();
        String extra = quiet > 0 ? "\n暂缓提醒，还剩 " + ((quiet + 59999) / 60000) + " 分钟" : "";
        String sensing = PackageObserverService.connected != null ? "无障碍已连接（仅包名）" : hasUsageAccess(this) ? "使用情况访问已授权" : "活动感知未授权";
        String notifications = MonitorService.notificationsAvailable(this) ? "通知可用" : "通知未开启";
        stateTitle.setText(title); stateTitle.setTextColor("active".equals(p) && MonitorService.running ? ACCENT : FG);
        state.setText(sensing + " · " + notifications + extra + "\n" + MonitorService.syncStatus + (MonitorService.running ? "\n当前观察：" + MonitorService.observed : ""));
        startButton.setText("paused".equals(p) || "active".equals(p) ? "继续这一轮  →" : "开始这一轮  →");
    }
    private void startCycle() {
        save();
        if (store.text("vow").isEmpty() || store.text("task").isEmpty()) { toast("先写下你的承诺和当前主线。"); return; }
        if (store.blocked().isEmpty()) { toast("先选择至少一个需要打断的应用。"); selectApps(); return; }
        if (!MonitorService.notificationsAvailable(this)) { notificationOptions(); return; }
        if (PackageObserverService.connected == null && !hasUsageAccess(this)) { observationOptions(); return; }
        store.start();
        getSystemService(NotificationManager.class).cancel(MonitorService.CHALLENGE_ID);
        try { startForegroundService(new Intent(this, MonitorService.class)); toast("已开始。你离开应用后，本地监测继续。"); }
        catch (Exception e) { store.pause(); toast("系统暂未允许监测启动，请重新打开应用后继续。"); }
        updateState();
    }
    static boolean hasUsageAccess(Context c) {
        AppOpsManager ops = c.getSystemService(AppOpsManager.class);
        return ops.checkOpNoThrow(AppOpsManager.OPSTR_GET_USAGE_STATS, android.os.Process.myUid(), c.getPackageName()) == AppOpsManager.MODE_ALLOWED;
    }
    private void selectApps() {
        Intent launcher = new Intent(Intent.ACTION_MAIN).addCategory(Intent.CATEGORY_LAUNCHER);
        List<ResolveInfo> infos = getPackageManager().queryIntentActivities(launcher, 0);
        TreeMap<String, String> sorted = new TreeMap<>();
        for (ResolveInfo info : infos) {
            String pkg = info.activityInfo.packageName;
            if (!pkg.equals(getPackageName())) sorted.put(info.loadLabel(getPackageManager()).toString() + "\n" + pkg, pkg);
        }
        String[] labels = sorted.keySet().toArray(new String[0]); String[] packages = sorted.values().toArray(new String[0]);
        boolean[] checks = new boolean[labels.length]; Set<String> existing = store.blocked();
        for (int i = 0; i < checks.length; i++) checks[i] = existing.contains(packages[i]);
        new AlertDialog.Builder(this).setTitle("你希望被打断的应用")
                .setMultiChoiceItems(labels, checks, (dialog, which, checked) -> checks[which] = checked)
                .setNeutralButton("手动输入包名", (d, w) -> manualPackages())
                .setNegativeButton("取消", null).setPositiveButton("保存", (d, w) -> {
                    LinkedHashSet<String> picked = new LinkedHashSet<>(); for (int i = 0; i < checks.length; i++) if (checks[i]) picked.add(packages[i]);
                    store.p.edit().putString("blocked", android.text.TextUtils.join("\n", picked)).commit(); updateBlocked();
                }).show();
    }
    private void manualPackages() {
        EditText e = new EditText(this); e.setText(store.text("blocked")); e.setHint("每行一个应用包名"); e.setMinLines(4);
        new AlertDialog.Builder(this).setTitle("红线包名").setView(e).setNegativeButton("取消", null).setPositiveButton("保存", (d, w) -> {
            store.p.edit().putString("blocked", e.getText().toString().trim()).commit(); updateBlocked();
        }).show();
    }
    private void notificationOptions() {
        new AlertDialog.Builder(this).setTitle("让提醒可以出现")
                .setMessage("IronWill 会发送普通通知，可由配对手表镜像。允许通知不代表用户必然看到；勿扰、系统省电或连接问题可能延迟提醒。")
                .setPositiveButton("允许 / 设置", (d, w) -> {
                    if (Build.VERSION.SDK_INT >= 33 && checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
                        requestPermissions(new String[]{Manifest.permission.POST_NOTIFICATIONS}, 100);
                    else startActivity(new Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, getPackageName()));
                }).setNeutralButton("发一条测试", (d, w) -> {
                    if (!MonitorService.notificationsAvailable(this)) { toast("请先开启通知与 IronWill 提醒渠道。"); return; }
                    Notification n = new Notification.Builder(this, MonitorService.CHANNEL_CHALLENGE).setSmallIcon(R.drawable.ic_ironwill)
                            .setContentTitle("IronWill 提醒测试").setContentText("最好你现在做的事情是对的。")
                            .setContentIntent(MonitorService.openApp(this)).setAutoCancel(true).build();
                    getSystemService(NotificationManager.class).notify(1003, n); toast("已提交测试通知；可以检查手机和手表。");
                }).setNegativeButton("稍后", null).show();
    }
    private void observationOptions() {
        new AlertDialog.Builder(this).setTitle("只识别应用，不阅读你的生活")
                .setMessage("基础方式：使用情况访问，读取近期前台应用包名。\n\n可选方式：无障碍服务，仅接收窗口所属包名。没有读取页面内容能力，不采集消息、密码、按键或截图。\n\n权限可随时在系统设置关闭；关闭或故障不会自动记成违约。自行安装时系统可能要求你手动允许受限制设置。")
                .setPositiveButton("使用情况访问", (d, w) -> startActivity(new Intent(Settings.ACTION_USAGE_ACCESS_SETTINGS)))
                .setNeutralButton("可选无障碍", (d, w) -> startActivity(new Intent(Settings.ACTION_ACCESSIBILITY_SETTINGS)))
                .setNegativeButton("暂不启用", null).show();
    }
    private void answerDialog() {
        if (store.text("pendingId").isEmpty()) { toast("目前没有待处理提醒。"); return; }
        final String id = store.text("pendingId");
        String[] labels = {"返回主线（2 分钟后再检查）", "这件事值得（15 分钟）", "喘口气（15 分钟）"};
        String[] choices = {"return", "worth", "breath"};
        new AlertDialog.Builder(this).setTitle("现在重新作出选择").setItems(labels, (d, which) -> {
            if (store.answer(id, choices[which])) getSystemService(NotificationManager.class).cancel(MonitorService.CHALLENGE_ID);
            else toast("这条提醒已失效。"); updateState();
        }).show();
    }
    private void testLock() {
        new AlertDialog.Builder(this).setTitle("仅测试一次系统锁屏")
                .setMessage("正常提醒不会自动锁屏。这项测试需要已开启的可选无障碍服务，会立刻请求锁屏一次；之后用你自己的系统解锁方式恢复。不会修改密码或设置自动复锁。")
                .setNegativeButton("取消", null).setPositiveButton("我确认，锁屏一次", (d, w) -> {
                    PackageObserverService service = PackageObserverService.connected;
                    if (service == null) { toast("无障碍尚未连接，未执行锁屏。"); return; }
                    if (Build.VERSION.SDK_INT >= 30 && service.getSystemActions().stream().noneMatch(a -> a.getId() == AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN)) {
                        toast("系统没有提供锁屏动作，未执行。"); return;
                    }
                    try {
                        boolean result = service.performGlobalAction(AccessibilityService.GLOBAL_ACTION_LOCK_SCREEN);
                        store.log("manual_test", result ? "用户明确确认的单次锁屏测试：平台返回成功" : "单次锁屏测试：平台返回失败");
                        if (!result) toast("系统未执行锁屏。");
                    } catch (RuntimeException error) { toast("系统拒绝了锁屏动作，未重试。"); }
                }).show();
    }
    private void computerOptions() {
        String[] choices = {"填写 / 更新安全配对", "从电脑导入当前主线与承诺", "给电脑发送一个选择", "断开电脑配对"};
        new AlertDialog.Builder(this).setTitle("电脑连接（仅你主动配对后）").setItems(choices, (d, index) -> {
            if (index == 0) pairingDialog();
            if (index == 1) importComputer();
            if (index == 2) remoteAction();
            if (index == 3) { store.p.edit().putBoolean("syncEnabled", false).remove("relayToken").remove("relayPin").remove("relayUrl").commit(); toast("已断开；手机本地提醒继续。"); }
        }).setNegativeButton("返回", null).show();
    }
    private EditText dialogField(LinearLayout layout, String hint, String value, boolean secret) {
        EditText e = new EditText(this); e.setHint(hint); e.setText(value); e.setSingleLine(true);
        e.setInputType(InputType.TYPE_CLASS_TEXT | (secret ? InputType.TYPE_TEXT_VARIATION_PASSWORD : InputType.TYPE_TEXT_VARIATION_URI));
        layout.addView(e, new LinearLayout.LayoutParams(-1, -2)); return e;
    }
    private void pairingDialog() {
        LinearLayout fields = new LinearLayout(this); fields.setOrientation(LinearLayout.VERTICAL); fields.setPadding(dp(20), dp(8), dp(20), dp(8));
        EditText url = dialogField(fields, "HTTPS 电脑地址，例如 https://192.168.1.8:8765", store.text("relayUrl"), false);
        EditText pin = dialogField(fields, "电脑显示的 SHA-256 证书指纹（64 位）", store.text("relayPin"), false);
        EditText token = dialogField(fields, "电脑显示的配对令牌", "", true);
        new AlertDialog.Builder(this).setTitle("核对电脑上的指纹后配对").setMessage("证书只接受此指纹；不向电脑发送应用包名、页面或正文。局域网离线时仍可独立提醒。")
                .setView(fields).setNegativeButton("取消", null).setPositiveButton("验证并保存", (d, w) -> {
                    final String u = url.getText().toString().trim(), p = pin.getText().toString().trim(), t = token.getText().toString().trim();
                    toast("正在验证电脑证书与令牌…");
                    new Thread(() -> {
                        try {
                            JSONObject result = SyncClient.request(u, p, t, "/v1/state", null);
                            if (result.optInt("protocol_version") != 1) throw new Exception("电脑协议版本不匹配");
                            store.p.edit().putString("relayUrl", u).putString("relayPin", p).putString("relayToken", t).putBoolean("syncEnabled", true).commit();
                            runUiIfAlive(() -> toast("已配对。任务只在你点击导入时复制。"));
                        } catch (Exception e) { runUiIfAlive(() -> toast("配对失败：" + friendly(e))); }
                    }, "ironwill-pairing").start();
                }).show();
    }
    private void importComputer() {
        new Thread(() -> {
            try {
                JSONObject desktop = new SyncClient(this).state().getJSONObject("desktop");
                final String goal = desktop.optString("goal"), oath = desktop.optString("oath");
                runUiIfAlive(() -> new AlertDialog.Builder(this).setTitle("导入电脑主线？")
                        .setMessage("主线：" + goal + "\n\n承诺：" + oath + "\n\n只复制内容，不自动开始手机周期。")
                        .setNegativeButton("取消", null).setPositiveButton("导入", (d, w) -> { task.setText(goal); vow.setText(oath); save(); toast("已导入。准备好后手动开始。"); }).show());
            } catch (Exception e) { runUiIfAlive(() -> toast("未导入：" + friendly(e))); }
        }, "ironwill-import").start();
    }
    private void remoteAction() {
        String[] labels = {"返回主线", "这件事值得", "喘口气", "暂停电脑周期", "继续电脑周期"};
        String[] actions = {"BACK", "WORTH", "BREATHE", "PAUSE", "RESUME"};
        new AlertDialog.Builder(this).setTitle("发送到已配对的电脑").setItems(labels, (d, which) -> {
            final String commandId = UUID.randomUUID().toString();
            new Thread(() -> {
                try { new SyncClient(this).action(actions[which], commandId); runUiIfAlive(() -> toast("已提交，电脑将按当前状态处理；手机周期保持自己的状态。")); }
                catch (Exception e) { runUiIfAlive(() -> toast("未确认送达：" + friendly(e))); }
            }, "ironwill-action").start();
        }).show();
    }
    private String friendly(Exception e) {
        if (e instanceof javax.net.ssl.SSLException) return "证书验证失败，请核对电脑指纹与时间。";
        String m = e.getMessage(); return m == null ? "连接不可用，请检查电脑服务与网络。" : m;
    }
    private void showLicense() {
        String notice = "IronWill / AlwaysOnDDL\nCopyright (C) 2026 IronWill contributors\nGPL-3.0-only\n\n"
                + "可按 GNU GPL 第 3 版使用、修改和再分发。本程序不提供任何担保。\n"
                + "源码及构建说明：https://github.com/santzhm186/AlwaysOnDDL\n\n";
        try (java.io.InputStream input = getAssets().open("LICENSE")) {
            java.io.ByteArrayOutputStream bytes = new java.io.ByteArrayOutputStream();
            byte[] buffer = new byte[4096]; int count;
            while ((count = input.read(buffer)) != -1) {
                if (bytes.size() + count > 100000) throw new java.io.IOException("License oversized");
                bytes.write(buffer, 0, count);
            }
            showText("关于与许可", notice + bytes.toString("UTF-8"));
        } catch (java.io.IOException e) {
            showText("关于与许可", notice + "许可资源无法读取，请从源码仓库的 LICENSE 文件查看全文。");
        }
    }
    private void showText(String title, String text) {
        ScrollView s = new ScrollView(this); TextView t = new TextView(this); t.setText(text); t.setTextColor(FG); t.setTextSize(14); t.setLineSpacing(dp(4), 1f); t.setTextIsSelectable(true); t.setPadding(dp(22), dp(12), dp(22), dp(12)); s.addView(t);
        new AlertDialog.Builder(this).setTitle(title).setView(s).setPositiveButton("关闭", null).show();
    }
    private void toast(String message) { Toast.makeText(this, message, Toast.LENGTH_LONG).show(); }
    private void runUiIfAlive(Runnable action) { runOnUiThread(() -> { if (!isFinishing() && !isDestroyed()) action.run(); }); }
    @Override protected void onResume() { super.onResume(); handler.removeCallbacks(refresh); handler.post(refresh); }
    @Override protected void onPause() { save(); handler.removeCallbacks(refresh); super.onPause(); }
}

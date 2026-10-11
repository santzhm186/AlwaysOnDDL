// Copyright (C) 2026 IronWill contributors
// SPDX-License-Identifier: GPL-3.0-only
package com.ironwill.app;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import android.app.NotificationManager;

public final class ActionReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context, Intent intent) {
        String choice = intent.getStringExtra("choice");
        if (!("return".equals(choice) || "worth".equals(choice) || "breath".equals(choice))) return;
        if (new Store(context).answer(intent.getStringExtra("interventionId"), choice)) {
            context.getSystemService(NotificationManager.class).cancel(MonitorService.CHALLENGE_ID);
        }
    }
}

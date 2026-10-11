// Copyright (C) 2026 IronWill contributors
// SPDX-License-Identifier: GPL-3.0-only
package com.ironwill.app;

import android.accessibilityservice.AccessibilityService;
import android.view.accessibility.AccessibilityEvent;
import android.os.SystemClock;

public final class PackageObserverService extends AccessibilityService {
    static volatile PackageObserverService connected;
    static volatile String latestPackage = "";
    static volatile long latestElapsed;
    @Override protected void onServiceConnected() { connected = this; }
    @Override public void onAccessibilityEvent(AccessibilityEvent event) {
        if (event == null || event.getEventType() != AccessibilityEvent.TYPE_WINDOW_STATE_CHANGED || event.getPackageName() == null) return;
        latestPackage = event.getPackageName().toString();
        latestElapsed = SystemClock.elapsedRealtime();
        // Deliberately never read event text, source nodes, screenshots, or key events.
    }
    @Override public void onInterrupt() { }
    @Override public void onDestroy() { if (connected == this) connected = null; latestPackage = ""; latestElapsed = 0; super.onDestroy(); }
}

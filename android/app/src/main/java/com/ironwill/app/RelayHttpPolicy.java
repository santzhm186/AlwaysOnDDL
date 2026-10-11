// Copyright (C) 2026 IronWill contributors
// SPDX-License-Identifier: GPL-3.0-only
package com.ironwill.app;

final class RelayHttpPolicy {
    static boolean acceptsStatus(int status) { return status >= 200 && status < 300; }
}

package com.ironwill.app;

final class RelayHttpPolicy {
    static boolean acceptsStatus(int status) { return status >= 200 && status < 300; }
}

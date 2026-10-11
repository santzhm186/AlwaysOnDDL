// Copyright (C) 2026 IronWill contributors
// SPDX-License-Identifier: GPL-3.0-only
package com.ironwill.app;

import android.content.Context;
import android.os.Build;
import org.json.JSONObject;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.URL;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.security.cert.X509Certificate;
import java.util.Locale;
import java.util.UUID;
import javax.net.ssl.HttpsURLConnection;
import javax.net.ssl.SSLContext;
import javax.net.ssl.TrustManager;
import javax.net.ssl.X509TrustManager;

final class SyncClient {
    final Store store;
    SyncClient(Context c) { store = new Store(c); }
    boolean enabled() { return store.p.getBoolean("syncEnabled", false); }
    String deviceId() {
        String id = store.text("deviceId");
        if (id.isEmpty()) { id = UUID.randomUUID().toString(); store.p.edit().putString("deviceId", id).commit(); }
        return id;
    }
    static String pin(X509Certificate cert) throws Exception {
        byte[] digest = MessageDigest.getInstance("SHA-256").digest(cert.getEncoded());
        StringBuilder s = new StringBuilder();
        for (byte b : digest) s.append(String.format(Locale.ROOT, "%02x", b & 0xff));
        return s.toString();
    }
    static JSONObject request(String base, String fingerprint, String token, String path, JSONObject body) throws Exception {
        URL root = new URL(base);
        final String expected = fingerprint.replace(":", "").replace(" ", "").toLowerCase(Locale.ROOT);
        if (!"https".equals(root.getProtocol()) || root.getHost().isEmpty() || root.getUserInfo() != null || !expected.matches("[0-9a-f]{64}") || token.trim().isEmpty())
            throw new IllegalArgumentException("需要 HTTPS 地址、64 位证书指纹和配对令牌");
        SSLContext ssl = SSLContext.getInstance("TLS");
        ssl.init(null, new TrustManager[]{new X509TrustManager() {
            public X509Certificate[] getAcceptedIssuers() { return new X509Certificate[0]; }
            public void checkClientTrusted(X509Certificate[] chain, String type) throws java.security.cert.CertificateException { throw new java.security.cert.CertificateException("Client authentication unsupported"); }
            public void checkServerTrusted(X509Certificate[] chain, String type) throws java.security.cert.CertificateException {
                try {
                    if (chain == null || chain.length == 0 || !MessageDigest.isEqual(pin(chain[0]).getBytes("US-ASCII"), expected.getBytes("US-ASCII")))
                        throw new java.security.cert.CertificateException("证书指纹不匹配");
                    chain[0].checkValidity();
                } catch (java.security.cert.CertificateException e) { throw e; }
                catch (Exception e) { throw new java.security.cert.CertificateException(e); }
            }
        }}, new SecureRandom());
        URL url = new URL(root.getProtocol(), root.getHost(), root.getPort(), path);
        HttpsURLConnection conn = (HttpsURLConnection) url.openConnection();
        conn.setSSLSocketFactory(ssl.getSocketFactory());
        conn.setHostnameVerifier((host, session) -> {
            try { return MessageDigest.isEqual(pin((X509Certificate) session.getPeerCertificates()[0]).getBytes("US-ASCII"), expected.getBytes("US-ASCII")); }
            catch (Exception e) { return false; }
        });
        conn.setInstanceFollowRedirects(false);
        conn.setConnectTimeout(3500); conn.setReadTimeout(3500);
        conn.setRequestProperty("Authorization", "Bearer " + token.trim());
        conn.setRequestProperty("Accept", "application/json");
        try {
            if (body != null) {
                conn.setRequestMethod("POST"); conn.setDoOutput(true);
                conn.setRequestProperty("Content-Type", "application/json; charset=utf-8");
                try (OutputStream out = conn.getOutputStream()) { out.write(body.toString().getBytes("UTF-8")); }
            }
            int status = conn.getResponseCode();
            if (!RelayHttpPolicy.acceptsStatus(status)) throw new java.io.IOException(status == 409 ? "电脑周期已经变化，请重新选择。" : "连接未完成（HTTP " + status + "）");
            ByteArrayOutputStream bytes = new ByteArrayOutputStream();
            try (InputStream input = conn.getInputStream()) {
                byte[] buffer = new byte[2048]; int n;
                while ((n = input.read(buffer)) != -1) {
                    if (bytes.size() + n > 65536) throw new java.io.IOException("响应超出限制");
                    bytes.write(buffer, 0, n);
                }
            }
            return new JSONObject(bytes.toString("UTF-8"));
        } finally { conn.disconnect(); }
    }
    JSONObject call(String path, JSONObject body) throws Exception {
        if (!enabled()) throw new IllegalStateException("尚未配对");
        return request(store.text("relayUrl"), store.text("relayPin"), store.text("relayToken"), path, body);
    }
    JSONObject state() throws Exception { return call("/v1/state", null); }
    void heartbeat() throws Exception {
        JSONObject body = new JSONObject().put("device_id", deviceId()).put("device_name", Build.MODEL)
                .put("status", store.phase()).put("stage", 0).put("active", store.active());
        call("/v1/device", body);
    }
    boolean claim(String eventId) throws Exception {
        return call("/v1/claim", new JSONObject().put("device_id", deviceId()).put("event_id", eventId)).optBoolean("granted", false);
    }
    void action(String action, String commandId) throws Exception {
        JSONObject desktop = state().getJSONObject("desktop");
        String sessionId = desktop.optString("id");
        if (sessionId.isEmpty()) throw new IllegalStateException("电脑没有当前周期，未提交选择。");
        call("/v1/action", new JSONObject().put("device_id", deviceId()).put("command_id", commandId).put("action", action).put("session_id", sessionId));
    }
}

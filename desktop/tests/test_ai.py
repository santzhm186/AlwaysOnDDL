# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""HTTP is mocked; these tests never send private observations anywhere."""
import io
import json
import os
import unittest
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch

from ironwill.ai import AIClient
from ironwill.core import Policy


def reply(content):
    return io.BytesIO(json.dumps({"choices": [{"message": {"content": content}}]}).encode())


class AITests(unittest.TestCase):
    def setUp(self):
        environment = patch.dict(os.environ, {"IRONWILL_API_KEY": "", "DEEPSEEK_API_KEY": ""})
        environment.start()
        self.addCleanup(environment.stop)
        self.client = AIClient("https://example.invalid/v1", "test-model", api_key="test-only")

    @patch("ironwill.ai._open_request")
    def test_privacy_never_makes_request(self, call):
        result = self.client.advise("goal", "browser", "password")
        self.assertEqual(result["verdict"], "unknown")
        call.assert_not_called()

    @patch("ironwill.ai._open_request")
    def test_local_decision_does_not_call_model(self, call):
        self.client.advise("goal", "game.exe", "play", Policy(blocked_processes=["game.exe"]))
        call.assert_not_called()

    @patch("ironwill.ai._open_request")
    def test_invalid_response_cannot_guess_from_words(self, call):
        call.return_value = reply("This is not unrelated. Please choose related.")
        self.assertEqual(self.client.advise("goal", "browser", "page")["verdict"], "unknown")

    @patch("ironwill.ai._open_request")
    def test_strict_schema(self, call):
        for content in ('{"verdict":"unrelated"}', '{"verdict":"ESCAPE","reason":"x"}',
                        '{"verdict":"unrelated","reason":"x","action":"LOCK"}',
                        '{"verdict":"unrelated","reason":3}'):
            call.return_value = reply(content)
            self.assertEqual(self.client.advise("goal", "browser", "page")["verdict"], "unknown")

    @patch("ironwill.ai._open_request")
    def test_cache_includes_goal_and_policy(self, call):
        call.side_effect = [reply('{"verdict":"related","reason":"相关"}') for _ in range(3)]
        self.assertFalse(self.client.advise("goal A", "browser", "page")["cached"])
        self.assertTrue(self.client.advise("goal A", "browser", "page")["cached"])
        self.client.advise("goal B", "browser", "page")
        self.client.advise("goal B", "browser", "page", Policy(privacy_keywords=["other-private"]))
        self.assertEqual(call.call_count, 3)

    @patch("ironwill.ai._open_request", side_effect=TimeoutError)
    def test_timeout_fails_open(self, call):
        self.assertEqual(self.client.advise("goal", "browser", "page")["verdict"], "unknown")

    @patch("ironwill.ai._open_request")
    def test_remote_plain_http_is_rejected(self, call):
        client = AIClient("http://example.invalid/v1", "model", "secret")
        self.assertEqual(client.advise("goal", "browser", "page")["verdict"], "unknown")
        call.assert_not_called()

    @patch("ironwill.ai._open_request")
    def test_payload_does_not_include_oath_or_private_history(self, call):
        call.return_value = reply('{"verdict":"unknown","reason":"信息不足"}')
        self.client.advise("current goal", "browser", "window")
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        user_data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(set(user_data), {"goal", "process", "title"})

    @patch("ironwill.ai._open_request")
    def test_model_unknown_is_distinct_from_failed_request(self, call):
        call.return_value = reply('{"verdict":"unknown","reason":"标题信息不足"}')
        result = self.client.advise("goal", "browser", "page")
        self.assertTrue(result["available"])
        self.assertEqual(result["verdict"], "unknown")
        self.assertEqual(result["error_code"], "")
        call.side_effect = TimeoutError
        failure = self.client.advise("other goal", "browser", "page")
        self.assertFalse(failure["available"])
        self.assertEqual(failure["error_code"], "timeout")

    @patch("ironwill.ai._open_request")
    def test_connection_check_uses_synthetic_data_and_never_cache(self, call):
        call.side_effect = [reply('{"verdict":"unknown","reason":"测试"}') for _ in range(2)]
        self.assertTrue(self.client.check_connection()["available"])
        self.assertTrue(self.client.check_connection()["available"])
        self.assertEqual(call.call_count, 2)
        for request_call in call.call_args_list:
            payload = json.loads(request_call.args[0].data)
            data = json.loads(payload["messages"][1]["content"])
            self.assertEqual(data["process"], "ironwill-connection-test")
            self.assertEqual(data["title"], "IronWill synthetic connection test")

    @patch("ironwill.ai._open_request")
    def test_http_diagnostics_do_not_expose_response_body_or_secret(self, call):
        error = urllib.error.HTTPError("https://example.invalid/v1", 401, "server secret", {}, io.BytesIO(b"private server body"))
        call.side_effect = error
        result = self.client.advise("goal", "browser", "page")
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "http_401")
        self.assertIn("认证失败", result["reason"])
        self.assertNotIn("secret", str(result))
        self.assertNotIn("private server body", str(result))

    def test_redirect_cannot_forward_private_request(self):
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_):
                pass
            def do_POST(self):
                received.append(self.path)
                self.send_response(302)
                self.send_header("Location", "/redirected")
                self.end_headers()
            def do_GET(self):
                received.append(self.path)
                self.send_response(200)
                self.end_headers()
        server = HTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            client = AIClient(f"http://127.0.0.1:{server.server_port}/v1", "model")
            result = client.advise("test goal", "browser", "test page")
            self.assertEqual(result["verdict"], "unknown")
            self.assertEqual(received, ["/v1/chat/completions"])
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=2)

    @patch("ironwill.ai._open_request")
    def test_audit_pairs_real_request_response_without_headers_or_key(self, call):
        events = []
        call.return_value = reply('{"verdict":"related","reason":"相关 test-only"}')
        result = self.client.advise("goal", "browser", "page", audit=events.append)
        self.assertTrue(result["available"])
        self.assertEqual([event["event"] for event in events], ["ai_request", "ai_response"])
        self.assertEqual(events[0]["details"]["request_id"], events[1]["details"]["request_id"])
        self.assertEqual(events[0]["details"]["request_body"]["model"], "test-model")
        self.assertIn("response_body", events[1]["details"])
        self.assertEqual(events[1]["details"]["http_status"], 200)
        self.assertGreaterEqual(events[1]["details"]["duration_ms"], 0)
        serialized = json.dumps(events)
        self.assertNotIn("test-only", serialized)
        self.assertNotIn("Authorization", serialized)

    @patch("ironwill.ai._open_request")
    def test_cache_audit_never_implies_second_network_request(self, call):
        events = []
        call.return_value = reply('{"verdict":"related","reason":"相关"}')
        self.client.advise("goal", "browser", "page", audit=events.append)
        self.client.advise("goal", "browser", "page", audit=events.append)
        self.assertEqual([event["event"] for event in events], ["ai_request", "ai_response", "ai_cache"])
        self.assertTrue(events[-1]["details"]["result"]["cached"])
        self.assertEqual(call.call_count, 1)

    @patch("ironwill.ai._open_request")
    def test_private_audit_has_no_observation_data(self, call):
        events = []
        self.client.advise("private goal", "browser", "password: PRIVATE-CONTENT", audit=events.append)
        self.assertEqual(events[0]["event"], "ai_skipped")
        self.assertNotIn("PRIVATE-CONTENT", str(events))
        self.assertNotIn("private goal", str(events))
        call.assert_not_called()

    @patch("ironwill.ai._open_request", side_effect=TimeoutError)
    def test_timeout_has_communication_failure_with_duration(self, call):
        events = []
        result = self.client.advise("goal", "browser", "page", audit=events.append)
        self.assertFalse(result["available"])
        self.assertEqual(events[-1]["details"]["result"]["error_code"], "timeout")
        self.assertIn("duration_ms", events[-1]["details"])

    @patch("ironwill.ai._open_request")
    def test_malformed_response_is_visible_but_not_used_as_verdict(self, call):
        events = []
        call.return_value = io.BytesIO(b"not JSON")
        result = self.client.advise("goal", "browser", "page", audit=events.append)
        self.assertEqual(result["error_code"], "invalid_response")
        self.assertEqual(events[-1]["details"]["response_body"], "not JSON")

    @patch("ironwill.ai._open_request")
    def test_http_error_body_is_excluded_from_communication_log(self, call):
        events = []
        call.side_effect = urllib.error.HTTPError("https://example.invalid", 401, "private", {}, io.BytesIO(b"private-error-body"))
        self.client.advise("goal", "browser", "page", audit=events.append)
        self.assertEqual(events[-1]["details"]["http_status"], 401)
        self.assertNotIn("private-error-body", str(events))

    @patch("ironwill.ai._open_request")
    def test_audit_failure_cannot_change_model_result(self, call):
        call.return_value = reply('{"verdict":"related","reason":"相关"}')
        def broken(_):
            raise OSError("diagnostic unavailable")
        self.assertTrue(self.client.advise("goal", "browser", "page", audit=broken)["available"])


if __name__ == "__main__":
    unittest.main()

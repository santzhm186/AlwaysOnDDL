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
        call.side_effect = [reply("1") for _ in range(3)]
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
        call.return_value = reply("2")
        self.client.advise("current goal", "browser", "window")
        request = call.call_args.args[0]
        payload = json.loads(request.data)
        user_data = json.loads(payload["messages"][1]["content"])
        self.assertEqual(set(user_data), {"goal", "process", "title"})

    @patch("ironwill.ai._open_request")
    def test_model_neutral_is_distinct_from_failed_request(self, call):
        call.return_value = reply("2")
        result = self.client.advise("goal", "browser", "page")
        self.assertTrue(result["available"])
        self.assertEqual(result["verdict"], "neutral")
        self.assertEqual(result["error_code"], "")
        call.side_effect = TimeoutError
        failure = self.client.advise("other goal", "browser", "page")
        self.assertFalse(failure["available"])
        self.assertEqual(failure["error_code"], "timeout")

    @patch("ironwill.ai._open_request")
    def test_connection_check_uses_synthetic_data_and_never_cache(self, call):
        call.side_effect = [reply("2") for _ in range(2)]
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
        call.return_value = reply("1")
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
        call.return_value = reply("1")
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
        call.return_value = reply("1")
        def broken(_):
            raise OSError("diagnostic unavailable")
        self.assertTrue(self.client.advise("goal", "browser", "page", audit=broken)["available"])

    @patch("ironwill.ai._open_request")
    def test_three_codes_use_local_labels_and_one_token_payload(self, call):
        for code, verdict in (("1", "related"), ("2", "neutral"), ("0", "unrelated")):
            with self.subTest(code=code):
                call.return_value = reply(code)
                result = self.client.advise("goal", "browser", "page", use_cache=False)
                self.assertTrue(result["available"])
                self.assertEqual(result["verdict"], verdict)
                self.assertEqual(result["code"], code)
                self.assertEqual(result["reason_source"], "local_code_mapping")
                payload = json.loads(call.call_args.args[0].data)
                self.assertEqual(payload["max_tokens"], 1)
                self.assertEqual(payload["temperature"], 0)
                self.assertNotIn("response_format", payload)
                self.assertNotIn("thinking", payload)

    @patch("ironwill.ai._open_request")
    def test_only_official_deepseek_receives_thinking_extension(self, call):
        for endpoint, official in (("https://api.deepseek.com", True), ("https://api.deepseek.com:443/v1", True),
                ("https://api.deepseek.com.evil.invalid/v1", False), ("https://example.invalid/deepseek", False),
                ("https://api.deepseek.com:8443/v1", False)):
            with self.subTest(endpoint=endpoint):
                call.return_value = reply("2")
                client = AIClient(endpoint, "deepseek-flash", "synthetic-key")
                self.assertTrue(client.check_connection()["available"])
                payload = json.loads(call.call_args.args[0].data)
                self.assertEqual("thinking" in payload, official)
                if official:
                    self.assertEqual(payload["thinking"], {"type": "disabled"})
                self.assertNotIn("extra_body", payload)  # Raw HTTP body, not the SDK wrapper.

    @patch("ironwill.ai._open_request")
    def test_invalid_digits_are_not_guessed_cached_or_retried(self, call):
        for content in ("", " ", "10", "1\n2", "1 - 相关", "0.", '"1"', "１", "０", "3", "related"):
            with self.subTest(content=content):
                call.return_value = reply(content)
                before = call.call_count
                for _ in range(2):
                    result = self.client.advise("goal", "browser", "page")
                    self.assertFalse(result["available"])
                    self.assertEqual(result["verdict"], "unknown")
                self.assertEqual(call.call_count - before, 2)

    @patch("ironwill.ai._open_request")
    def test_whitespace_is_trimmed_without_extracting_embedded_digits(self, call):
        call.return_value = reply(" \n2\t")
        self.assertEqual(self.client.check_connection()["verdict"], "neutral")

    @patch("ironwill.ai._open_request")
    def test_length_finish_can_contain_complete_digit_and_audits_usage(self, call):
        call.return_value = io.BytesIO(json.dumps({"choices": [{"finish_reason": "length", "message": {"content": "0"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101,
                      "prompt_cache_hit_tokens": 64, "completion_tokens_details": {"reasoning_tokens": 0},
                      "untrusted_note": "do not copy", "prompt_cache_miss_tokens": True}}).encode())
        events = []
        result = self.client.check_connection(audit=events.append)
        self.assertEqual(result["verdict"], "unrelated")
        details = events[-1]["details"]
        self.assertEqual(details["finish_reason"], "length")
        self.assertEqual(details["usage"], {"prompt_tokens": 100, "completion_tokens": 1, "total_tokens": 101,
                                            "prompt_cache_hit_tokens": 64, "reasoning_tokens": 0})

    @patch("ironwill.ai._open_request")
    def test_interrupted_or_reasoning_response_cannot_become_classification(self, call):
        for choice, usage in (({"finish_reason": "content_filter", "message": {"content": "0"}}, {}),
                ({"finish_reason": "aborted", "message": {"content": "0"}}, {}),
                ({"message": {"content": "", "reasoning_content": "thinking"}}, {}),
                ({"message": {"content": "1", "reasoning_content": "thinking"}}, {}),
                ({"message": {"content": "1", "tool_calls": [{"name": "lock"}]}}, {}),
                ({"message": {"content": "1"}}, {"completion_tokens_details": {"reasoning_tokens": 9}})):
            with self.subTest(choice=choice, usage=usage):
                call.return_value = io.BytesIO(json.dumps({"choices": [choice], "usage": usage}).encode())
                self.assertFalse(self.client.check_connection()["available"])

    @patch("ironwill.ai._open_request")
    def test_malformed_envelopes_fail_open(self, call):
        for envelope in ([], {}, {"choices": []}, {"choices": [None]}, {"choices": [{"message": []}]},
                         {"choices": [{"message": {"content": None}}]}):
            call.return_value = io.BytesIO(json.dumps(envelope).encode())
            self.assertFalse(self.client.check_connection()["available"])

    @patch("ironwill.ai._open_request")
    def test_invalid_reply_with_key_is_redacted_in_communication_log(self, call):
        call.return_value = reply("1 test-only")
        events = []
        self.assertFalse(self.client.check_connection(audit=events.append)["available"])
        self.assertNotIn("test-only", json.dumps(events))
        self.assertIn("[已屏蔽]", events[-1]["details"]["response_body"])

    @patch("ironwill.ai._open_request")
    def test_invalid_endpoint_port_never_sends_request(self, call):
        result = AIClient("https://api.deepseek.com:invalid", "deepseek-flash", "synthetic").check_connection()
        self.assertFalse(result["available"])
        self.assertEqual(result["error_code"], "invalid_endpoint")
        call.assert_not_called()


if __name__ == "__main__":
    unittest.main()

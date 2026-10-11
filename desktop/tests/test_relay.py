import json
import ssl
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch

from ironwill.relay import RelayServer


class RelayTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.relay = RelayServer(self.temp.name, host="127.0.0.1", port=0)
        self.info = self.relay.start()
        self.relay.set_desktop_state({"id": "cycle-1", "status": "active", "goal": "test"})
        self.url = f"https://127.0.0.1:{self.relay.port}"
        self.context = ssl.create_default_context(cafile=str(Path(self.temp.name) / "pairing/server.crt"))

    def tearDown(self):
        self.relay.stop()
        self.temp.cleanup()

    def request(self, path, body=None, token=None):
        headers = {"Authorization": "Bearer " + (self.info["token"] if token is None else token)}
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(self.url + path, data=data, headers=headers)
        with urllib.request.urlopen(req, context=self.context, timeout=5) as response:
            return response.status, json.load(response)

    def test_requires_token(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/v1/state", token="wrong")
        self.assertEqual(error.exception.code, 401)
        error.exception.close()

    def test_shares_only_allowed_state_fields(self):
        self.relay.set_desktop_state({"status": "active", "id": "sample", "goal": "goal",
            "oath": "oath", "stage": 1, "title": "private secret", "api_key": "secret"})
        _, result = self.request("/v1/state")
        self.assertEqual(result["desktop"]["goal"], "goal")
        self.assertNotIn("title", result["desktop"])
        self.assertNotIn("api_key", result["desktop"])

    def test_duplicate_action_is_not_replayed(self):
        body = {"device_id": "phone", "command_id": "one", "session_id": "cycle-1", "action": "BACK"}
        self.assertEqual(self.request("/v1/action", body)[0], 202)
        self.assertTrue(self.request("/v1/action", body)[1]["duplicate"])
        self.assertEqual(len(self.relay.drain_commands()), 1)
        self.assertEqual(self.relay.drain_commands(), [])

    def test_lock_and_terminal_cannot_be_requested_remotely(self):
        for action in ("LOCK", "DELETE", "EXODUS", "START"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/v1/action", {"device_id": "phone", "command_id": action, "action": action})
            self.assertEqual(error.exception.code, 400)
            error.exception.close()

    def test_expired_command_is_dropped(self):
        self.request("/v1/action", {"device_id": "phone", "command_id": "late", "session_id": "cycle-1", "action": "PAUSE"})
        with self.relay._guard:
            self.relay._commands[0]["_received"] -= 31
        self.assertEqual(self.relay.drain_commands(), [])

    def test_claims_are_exclusive_and_idempotent(self):
        with patch("ironwill.relay.time.monotonic", return_value=100):
            self.assertTrue(self.relay.claim_reminder("desktop", "event1"))
            self.assertFalse(self.relay.claim_reminder("phone", "event2"))
        with patch("ironwill.relay.time.monotonic", return_value=131):
            self.assertFalse(self.relay.claim_reminder("desktop", "event1"))
            self.assertTrue(self.relay.claim_reminder("phone", "event2"))

    def test_device_fields_are_sanitized(self):
        self.request("/v1/device", {"device_id": "phone", "device_name": "test",
            "stage": 2, "status": "active", "active": True, "title": "never shared"})
        peer = self.relay.get_info()["peers"][0]
        self.assertEqual(peer["stage"], 2)
        self.assertNotIn("title", peer)

    def test_bad_device_id_rejected(self):
        with self.assertRaises(urllib.error.HTTPError) as error:
            self.request("/v1/device", {"device_id": "../bad"})
        error.exception.close()

    def test_previous_session_command_is_rejected(self):
        for session_id in (None, "old-cycle"):
            with self.assertRaises(urllib.error.HTTPError) as error:
                self.request("/v1/action", {"device_id": "phone", "command_id": "outdated",
                    "session_id": session_id, "action": "PAUSE"})
            self.assertEqual(error.exception.code, 409)
            error.exception.close()
        self.assertEqual(self.relay.drain_commands(), [])

    def test_enqueued_command_retains_target_session(self):
        self.request("/v1/action", {"device_id": "phone", "command_id": "bound",
            "session_id": "cycle-1", "action": "PAUSE"})
        self.relay.set_desktop_state({"id": "cycle-2", "status": "active"})
        self.assertEqual(self.relay.drain_commands()[0]["session_id"], "cycle-1")

    def test_certificate_and_token_survive_restart(self):
        before = self.info
        self.relay.stop()
        self.relay = RelayServer(self.temp.name, host="127.0.0.1", port=0)
        after = self.relay.start()
        self.assertEqual(before["fingerprint"], after["fingerprint"])
        self.assertEqual(before["token"], after["token"])


if __name__ == "__main__":
    unittest.main()

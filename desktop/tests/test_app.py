"""Hidden desktop integration checks. No real observation, network, or lock calls."""

import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import Mock, patch

from ironwill.app import IronWillApp
from ironwill.core import Observation


class DesktopBoundaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            probe = tk.Tk()
            probe.withdraw()
            probe.destroy()
        except tk.TclError as error:
            raise unittest.SkipTest("A Tk desktop session is required: " + str(error))

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="ironwill-ui-test-")
        self.environment = patch.dict(os.environ, {"IRONWILL_API_KEY": "", "DEEPSEEK_API_KEY": ""})
        self.environment.start()
        self.directory = Path(self.temporary.name)
        self.root = tk.Tk()
        self.root.withdraw()
        self.sample = patch("ironwill.app.platform_windows.sample_foreground", return_value=Observation(status="unavailable"))
        self.lock = patch("ironwill.app.platform_windows.request_lock", return_value=(True, "test stub"))
        self.supported = patch("ironwill.app.platform_windows.supported", return_value=True)
        self.errors = patch("ironwill.app.messagebox.showerror", side_effect=lambda title, message, **_: self.fail("Unexpected error dialog: " + title + " / " + message))
        self.sample_mock, self.lock_mock = self.sample.start(), self.lock.start()
        self.supported.start()
        self.errors.start()
        self.app = IronWillApp(self.root, self.directory)

    def tearDown(self):
        try:
            for callback in self.root.tk.call("after", "info"):
                self.root.after_cancel(callback)
            self.root.destroy()
        except tk.TclError:
            pass
        self.sample.stop()
        self.lock.stop()
        self.supported.stop()
        self.errors.stop()
        self.environment.stop()
        self.temporary.cleanup()

    def write(self, key, value):
        field = self.app.text_fields[key]
        field.delete("1.0", "end")
        field.insert("1.0", value)

    def hide_dialog(self):
        if self.app.dialog:
            self.app.dialog.withdraw()

    def begin(self, stage=0, lock=False):
        self.write("goal", "test goal")
        self.write("reality", "  reality as written\n")
        self.write("consequence", " consequence as written ")
        self.write("oath", "  oath as written\n")
        self.app.rule_fields["blocked_processes"].insert("end", "\ngame.exe")
        self.app.rule_fields["allowed_processes"].insert("end", "\ncode.exe")
        for key, value in (("reminder_after_seconds", "1"), ("escalation_after_seconds", "2"), ("recovery_seconds", "1")):
            self.app.numeric_fields[key].set(value)
        self.app.lock_enabled.set(lock)
        self.app.start()
        self.app.engine.tick(Observation(process="game.exe"), 1)
        for step in range(stage):
            events = self.app.engine.tick(Observation(process="game.exe"), 1 if step == 0 else 2)
            self.app._handle(events)
            self.hide_dialog()

    def test_constructing_does_not_start_or_write(self):
        self.assertEqual(self.app.engine.state["status"], "idle")
        self.assertEqual(list(self.directory.iterdir()), [])
        self.sample_mock.assert_not_called()
        self.lock_mock.assert_not_called()

    def test_start_preserves_personal_words(self):
        self.begin()
        self.assertEqual(self.app.engine.state["reality"], "  reality as written\n")
        self.assertEqual(self.app.engine.state["consequence"], " consequence as written ")
        self.assertEqual(self.app.engine.state["oath"], "  oath as written\n")
        self.assertEqual(self.app.engine.state["status"], "active")
        self.sample_mock.assert_not_called()
        settings = json.loads((self.directory / "ui_settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings["reality"], "  reality as written\n")

    def test_empty_rules_explain_no_automatic_judgment(self):
        self.write("goal", "test goal")
        for key in ("blocked_processes", "blocked_titles"):
            self.app.rule_fields[key].delete("1.0", "end")
        self.app.start()
        self.assertIn("不会自动判断分心", self.app.status_text.get())

    def test_preview_does_not_change_state_or_lock(self):
        self.begin()
        before = self.app.engine.snapshot()
        self.app._show_reminder(3, "preview", preview=True)
        self.hide_dialog()
        self.app._choose("LOCK", preview=True)
        self.assertEqual(before, self.app.engine.snapshot())
        self.lock_mock.assert_not_called()

    def test_lock_requires_permission_and_explicit_real_choice(self):
        self.begin(stage=3, lock=True)
        self.lock_mock.assert_not_called()
        self.app._choose("LOCK")
        self.lock_mock.assert_called_once()

    def test_disabled_lock_never_calls_os(self):
        self.begin(stage=3)
        self.app._choose("LOCK")
        self.lock_mock.assert_not_called()
        self.assertIsNotNone(self.app.dialog)

    def test_worth_is_a_direct_choice(self):
        self.begin(stage=1)
        self.app._choose("WORTH")
        self.assertEqual(self.app.engine.state["last_choice"], "WORTH")
        self.assertGreater(self.app.engine.state["quiet_remaining"], 0)
        self.assertIsNone(self.app.dialog)

    def test_advisory_is_visible_nonmodal_and_does_not_change_stage(self):
        self.begin()
        before = self.app.engine.snapshot()
        self.app._handle([{"type": "gray_hint", "message": "advisory", "stage": 0}])
        self.hide_dialog()
        self.assertTrue(self.app.dialog_advisory)
        self.assertIsNone(self.root.grab_current())
        self.app._dismiss_advisory()
        self.assertEqual(before, self.app.engine.snapshot())
        self.lock_mock.assert_not_called()

    def test_mobile_worth_requires_current_session_and_real_reminder(self):
        self.begin(stage=1)
        session = self.app.engine.state["id"]
        self.app._mobile_command({"action": "WORTH"})
        self.assertEqual(self.app.engine.state["last_choice"], "")
        self.app._mobile_command({"action": "WORTH", "session_id": "stale"})
        self.assertEqual(self.app.engine.state["last_choice"], "")
        self.app._mobile_command({"action": "WORTH", "session_id": session})
        self.assertEqual(self.app.engine.state["last_choice"], "WORTH")
        before = self.app.engine.snapshot()
        self.app._mobile_command({"action": "BACK", "session_id": session})
        self.assertEqual(before, self.app.engine.snapshot())

    def test_suppressed_third_stage_is_retried(self):
        self.begin(stage=3)
        self.app._close_reminder()
        relay = Mock()
        relay.claim_reminder.side_effect = [False, True]
        self.app.relay = relay
        self.app._handle([{"type": "reminder", "stage": 3, "message": "third"}])
        self.assertIsNone(self.app.dialog)
        self.assertIsNotNone(self.app.pending_reminder)
        self.app.pending_reminder["retry_at"] = 0
        self.app._retry_reminder()
        self.hide_dialog()
        self.assertIsNone(self.app.pending_reminder)
        self.assertEqual(self.app.dialog_stage, 3)
        self.assertEqual(self.app.engine.state["stage"], 3)

    def test_recovery_closes_old_reminder(self):
        self.begin(stage=1)
        self.app._handle(self.app.engine.tick(Observation(process="code.exe"), 1))
        self.assertEqual(self.app.engine.state["stage"], 0)
        self.assertIsNone(self.app.dialog)

    def test_pausing_stops_sampling(self):
        self.begin()
        self.app._poll()
        self.assertEqual(self.sample_mock.call_count, 1)
        self.app.pause_resume()
        self.app._poll()
        self.assertEqual(self.sample_mock.call_count, 1)
        self.assertEqual(self.app.engine.state["status"], "paused")

    def test_key_is_memory_only_and_opt_out_immediate(self):
        self.app.ai_enabled.set(True)
        self.app.ai_endpoint.set("https://example.invalid/v1")
        self.app.ai_model.set("test-model")
        self.app.ai_key.set("NOT-A-REAL-SECRET-FOR-TEST")
        self.app.apply_ai()
        self.assertIsNotNone(self.app.ai)
        for path in self.directory.iterdir():
            self.assertNotIn("NOT-A-REAL-SECRET-FOR-TEST", path.read_text(encoding="utf-8"))
        self.app.ai_enabled.set(False)
        self.app._ai_permission_changed()
        self.assertIsNone(self.app.ai)

    def test_embedded_endpoint_secret_is_not_saved(self):
        self.app.ai_enabled.set(True)
        self.app.ai_endpoint.set("https://example.invalid/v1?key=NOT-A-REAL-SECRET-FOR-TEST")
        self.app.ai_model.set("test-model")
        self.app.apply_ai()
        self.assertIsNone(self.app.ai)
        self.assertFalse((self.directory / "ui_settings.json").exists())

    def test_no_real_services_are_started_by_ai_configuration(self):
        with patch("ironwill.ai.AIClient.advise", side_effect=AssertionError("no request allowed")) as advise:
            self.app.ai_enabled.set(True)
            self.app.ai_endpoint.set("http://localhost:1234/v1")
            self.app.ai_model.set("test-model")
            self.app.apply_ai()
            advise.assert_not_called()
        self.assertIsNone(self.app.relay)

    def result(self, verdict="unrelated", reason="测试理由", available=True):
        return {"verdict": verdict, "reason": reason, "available": available, "cached": False}

    def deliver_ai(self, verdict="unrelated", reason="测试理由", available=True, generation=None):
        observation = self.app.last_observation
        self.app.events.put(("ai", self.app.ai_generation if generation is None else generation,
                             self.app.engine.state["id"], observation.process, observation.title,
                             self.result(verdict, reason, available)))
        self.app._drain_results()
        self.app._refresh_status()

    def test_current_diagnosis_explains_disabled_ai_and_allowlist(self):
        self.begin()
        self.app.last_observation = Observation(process="browser.exe", title="unlisted page")
        self.app._refresh_status()
        self.assertIn("AI 尚未启用", self.app.observation_text.get())
        self.app.last_observation = Observation(process="code.exe", title="a source file")
        self.app._refresh_status()
        self.assertIn("主线白名单", self.app.observation_text.get())
        self.assertIn("没有交给 AI", self.app.observation_text.get())

    def test_current_diagnosis_hides_private_title_and_explains_idle(self):
        self.begin()
        self.app.last_observation = Observation(process="browser.exe", title="password: private-value")
        self.app._refresh_status()
        self.assertIn("隐私", self.app.observation_text.get())
        self.assertNotIn("private-value", self.app.observation_text.get())
        self.app.last_observation = Observation(process="browser.exe", title="reading", idle_seconds=150)
        self.app._refresh_status()
        self.assertIn("空闲阈值", self.app.observation_text.get())

    def test_failed_ai_is_not_displayed_as_focus_or_model_uncertainty(self):
        self.begin()
        self.app.ai = Mock()
        self.app.last_observation = Observation(process="browser.exe", title="page")
        self.deliver_ai("unknown", "没有可用的 API 密钥。", available=False)
        self.assertIn("AI 审核未完成", self.app.observation_text.get())
        self.assertIn("API 密钥", self.app.observation_text.get())
        self.assertEqual(self.app.ai_completed_count, 0)
        self.deliver_ai("unknown", "标题信息不足", available=True)
        self.assertIn("暂无明确", self.app.observation_text.get())
        self.assertNotIn("AI 审核未完成", self.app.observation_text.get())
        self.assertEqual(self.app.ai_completed_count, 1)

    def test_stale_ai_result_cannot_explain_new_window_or_new_settings(self):
        self.begin()
        self.app.ai = Mock()
        self.app.last_observation = Observation(process="browser.exe", title="old page")
        self.deliver_ai("related", "old-reason")
        self.app.last_observation = Observation(process="browser.exe", title="new page")
        self.app._refresh_status()
        self.assertNotIn("old-reason", self.app.observation_text.get())
        count = self.app.ai_completed_count
        self.deliver_ai("unrelated", "stale-settings-reason", generation=self.app.ai_generation - 1)
        self.assertEqual(self.app.ai_completed_count, count)
        self.assertNotIn("stale-settings-reason", self.app.observation_text.get())

    def test_ai_can_remind_without_any_redline_title(self):
        self.write("goal", "write a report")
        self.app.start()
        self.app.ai = Mock()
        self.app.ai.advise.return_value = self.result()
        self.sample_mock.return_value = Observation(process="browser.exe", title="unlisted entertainment page")
        def immediate_thread(target, **_):
            return Mock(start=target)
        with patch("ironwill.app.threading.Thread", side_effect=immediate_thread):
            self.app._poll()
            self.app._poll()
        self.hide_dialog()
        self.app.ai.advise.assert_called_once()
        self.assertEqual(self.app.policy.blocked_titles, [])
        self.assertEqual(self.app.policy.blocked_processes, [])
        self.assertTrue(self.app.dialog_advisory)
        self.assertEqual(self.app.engine.state["stage"], 0)
        self.assertIn("测试理由", self.app.observation_text.get())

    def test_manual_connection_test_does_not_sample_or_change_cycle(self):
        self.begin()
        before = self.app.engine.snapshot()
        self.app.ai = Mock()
        self.app.ai.check_connection.return_value = self.result("unknown")
        def immediate_thread(target, **_):
            return Mock(start=target)
        with patch("ironwill.app.threading.Thread", side_effect=immediate_thread):
            self.app.test_ai_connection()
        self.app._drain_results()
        self.assertIn("连接成功", self.app.connection_test_text.get())
        self.assertEqual(before, self.app.engine.snapshot())
        self.sample_mock.assert_not_called()
        self.lock_mock.assert_not_called()
        self.app.ai.advise.assert_not_called()

    def test_obsolete_connection_test_is_not_reported_successful(self):
        self.app.ai = Mock()
        self.app.connection_test_id = "test-id"
        self.app.events.put(("ai_connection", "test-id", self.app.ai_generation - 1, {"available": True}))
        self.app._drain_results()
        self.assertNotIn("连接成功", self.app.connection_test_text.get())
        self.assertIsNone(self.app.connection_test_id)

    def configure_key(self, key="SYNTHETIC-NOT-A-REAL-KEY"):
        self.app.ai_enabled.set(True)
        self.app.ai_endpoint.set("https://model.invalid/v1")
        self.app.ai_model.set("test-model")
        self.app.ai_key.set(key)

    def test_remembered_key_is_encrypted_and_reused_on_reopen(self):
        self.configure_key()
        self.app.remember_api_key.set(True)
        self.app.apply_ai()
        self.assertIsNotNone(self.app.ai)
        self.assertEqual(self.app.ai_key.get(), "")
        self.assertNotIn(b"SYNTHETIC-NOT-A-REAL-KEY", self.app.key_store.path.read_bytes())
        for name in ("ui_settings.json", "session.json", "policy.json"):
            path = self.directory / name
            if path.exists():
                self.assertNotIn("SYNTHETIC-NOT-A-REAL-KEY", path.read_text(encoding="utf-8"))
        self.app._ai_permission_changed()
        self.app.ai = None
        self.app.runtime_key = None
        reopened = IronWillApp(self.root, self.directory)
        self.assertTrue(reopened.remember_api_key.get())
        self.assertEqual(reopened.ai_key.get(), "")
        self.assertIsNone(reopened.ai)
        reopened.ai_enabled.set(True)
        reopened.apply_ai()
        self.assertIsNotNone(reopened.ai)
        self.assertEqual(reopened.ai._key, "SYNTHETIC-NOT-A-REAL-KEY")
        self.assertEqual(reopened.engine.state["status"], "idle")

    def test_saved_key_cannot_be_used_for_edited_endpoint(self):
        self.configure_key()
        self.app.remember_api_key.set(True)
        self.app.apply_ai()
        self.app.ai_endpoint.set("https://other.invalid/v1")
        self.app.apply_ai()
        self.assertIsNone(self.app.ai)
        self.assertIn("未找到这个服务", self.app.ai_status.get())
        self.assertTrue(self.app.key_store.exists())

    def test_existing_runtime_key_can_be_remembered_without_reentering(self):
        self.configure_key()
        self.app.apply_ai()
        self.app.ai_key.set("")
        self.app.remember_api_key.set(True)
        self.app.apply_ai()
        self.assertIsNotNone(self.app.ai)
        self.assertEqual(self.app.key_store.load("https://model.invalid/v1"), "SYNTHETIC-NOT-A-REAL-KEY")

    def test_temporary_key_does_not_follow_edited_endpoint(self):
        self.configure_key()
        self.app.apply_ai()
        self.assertEqual(self.app.ai_key.get(), "")
        self.app.ai_endpoint.set("https://other.invalid/v1")
        self.app.apply_ai()
        self.assertIsNone(self.app.ai)
        self.assertFalse(self.app.key_store.exists())

    def test_disabling_ai_does_not_forget_current_runtime_key(self):
        self.configure_key()
        self.app.apply_ai()
        self.app.ai_enabled.set(False)
        self.app._ai_permission_changed()
        self.assertIsNone(self.app.ai)
        self.app.ai_enabled.set(True)
        self.app.apply_ai()
        self.assertIsNotNone(self.app.ai)
        self.assertEqual(self.app.ai._key, "SYNTHETIC-NOT-A-REAL-KEY")
        self.assertFalse(self.app.key_store.exists())

    def test_forget_removes_saved_key_and_stops_using_it(self):
        self.configure_key()
        self.app.remember_api_key.set(True)
        self.app.apply_ai()
        previous_generation = self.app.ai_generation
        self.app.forget_saved_key()
        self.assertFalse(self.app.key_store.exists())
        self.assertIsNone(self.app.ai)
        self.assertIsNone(self.app.runtime_key)
        self.assertFalse(self.app.ai_enabled.get())
        self.assertGreater(self.app.ai_generation, previous_generation)

    def test_unchecking_remember_removes_disk_copy_but_keeps_current_session(self):
        self.configure_key()
        self.app.remember_api_key.set(True)
        self.app.apply_ai()
        self.app.remember_api_key.set(False)
        self.app.apply_ai()
        self.assertIsNotNone(self.app.ai)
        self.assertFalse(self.app.key_store.exists())

    def test_failed_encrypted_save_does_not_claim_success_or_fall_back(self):
        self.configure_key()
        self.app.remember_api_key.set(True)
        with patch.object(self.app.key_store, "save", side_effect=OSError("test failure")):
            self.app.apply_ai()
        self.assertIsNone(self.app.ai)
        self.assertFalse(self.app.key_store.exists())
        self.assertIn("未成功", self.app.ai_status.get())
        self.assertNotIn("SYNTHETIC-NOT-A-REAL-KEY", self.app.settings_path.read_text(encoding="utf-8"))

    def test_legacy_file_import_is_masked_not_activated_or_copied(self):
        source = self.directory / "legacy-key.txt"
        source.write_text("SYNTHETIC-LEGACY-KEY", encoding="utf-8")
        with patch("ironwill.app.filedialog.askopenfilename", return_value=str(source)):
            self.app.import_legacy_key()
        self.assertEqual(self.app.ai_key.get(), "SYNTHETIC-LEGACY-KEY")
        self.assertEqual(self.app.ai_endpoint.get(), "https://api.deepseek.com")
        self.assertEqual(self.app.ai_model.get(), "deepseek-flash")
        self.assertIsNone(self.app.ai)
        self.assertFalse(self.app.key_store.exists())
        self.assertNotIn("SYNTHETIC-LEGACY-KEY", self.app.key_storage_text.get())
        self.assertEqual(source.read_text(encoding="utf-8"), "SYNTHETIC-LEGACY-KEY")

    def test_legacy_import_cannot_silently_send_key_to_other_provider(self):
        self.app.ai_endpoint.set("https://other.invalid/v1")
        with patch("ironwill.app.filedialog.askopenfilename") as picker:
            self.app.import_legacy_key()
        picker.assert_not_called()
        self.assertEqual(self.app.ai_key.get(), "")


if __name__ == "__main__":
    unittest.main()

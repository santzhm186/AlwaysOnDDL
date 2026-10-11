# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
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
        for path in self.directory.rglob("*"):
            if path.is_file():
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
        import time
        self.app.ai_candidate = ("browser.exe", "unlisted entertainment page", time.monotonic() - 4)
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

    def test_log_page_filters_details_persists_and_exports(self):
        self.assertEqual(len(self.app.notebook.tabs()), 4)
        self.app._log("判断", "observation", "主线判断", {"title": "unique-title"})
        self.app._log("AI通信", "ai_request", "请求", {"request_body": {"model": "demo"}})
        self.app._refresh_logs()
        self.assertEqual(len(self.app.log_tree.get_children()), 2)
        self.app.log_filter.set("判断")
        self.app.log_search.set("unique-title")
        self.assertEqual(len(self.app.log_tree.get_children()), 1)
        self.assertIn("unique-title", self.app.log_detail.get("1.0", "end"))
        destination = self.directory / "export.jsonl"
        with patch("ironwill.app.filedialog.asksaveasfilename", return_value=str(destination)):
            self.app.export_logs()
        self.assertEqual(len(destination.read_text(encoding="utf-8").splitlines()), 1)
        from ironwill.activity_log import ActivityLog
        self.assertEqual(len(ActivityLog(self.directory).entries), 2)

    def test_observation_logs_deduplicate_and_hide_private_content(self):
        self.begin()
        observation = Observation(process="browser", title="test page")
        self.app._record_observation(observation, 1)
        count = len(self.app.journal.entries)
        self.app._record_observation(observation, 1)
        self.assertEqual(len(self.app.journal.entries), count)
        self.app._last_decision_time -= 61
        self.app._record_observation(observation, 1)
        self.assertEqual(len(self.app.journal.entries), count + 1)
        self.app._record_observation(Observation(process="PRIVATE-PROCESS", title="password: PRIVATE-LOG-TITLE"), 1)
        entry = self.app.journal.entries[-1]
        self.assertNotIn("PRIVATE-LOG-TITLE", str(entry))
        self.assertNotIn("PRIVATE-PROCESS", str(entry))
        self.assertIn("隐私", entry["summary"])

    def test_observation_log_explains_rule_and_sampling_gap(self):
        self.begin()
        self.app._record_observation(Observation(process="game.exe", title="test game"), 1)
        entry = self.app.journal.entries[-1]
        self.assertIn("红线", entry["summary"])
        self.assertEqual(entry["details"]["matched_rules"]["blocked_processes"], ["game.exe"])
        self.app._record_observation(Observation(process="game.exe", title="test game"), 45)
        self.assertIn("断档", self.app.journal.entries[-1]["summary"])

    def test_stale_results_are_logged_without_becoming_current_judgment(self):
        self.begin()
        self.app.ai = Mock()
        self.app.last_observation = Observation(process="browser", title="test")
        self.deliver_ai(generation=self.app.ai_generation - 1)
        entry = self.app.journal.entries[-1]
        self.assertIn("过期", entry["summary"])
        self.assertFalse(entry["details"]["accepted"])
        self.assertIsNone(self.app.semantic_result)

    def test_real_ai_audit_records_request_response_and_connection_test(self):
        import io
        self.configure_key()
        self.app.apply_ai()
        response = {"choices": [{"message": {"content": '{"verdict":"unknown","reason":"synthetic test"}'}}]}
        def immediate_thread(target, **_):
            return Mock(start=target)
        with patch("ironwill.app.threading.Thread", side_effect=immediate_thread), patch(
                "ironwill.ai._open_request", return_value=io.BytesIO(json.dumps(response).encode())):
            self.app.test_ai_connection()
        self.app._drain_results()
        communication = [entry for entry in self.app.journal.entries if entry["category"] == "AI通信"]
        self.assertEqual([entry["event"] for entry in communication], ["ai_request", "ai_response"])
        self.assertEqual(communication[0]["details"]["purpose"], "虚拟连接测试")
        self.assertIn("ironwill-connection-test", str(communication[0]))
        self.assertNotIn("SYNTHETIC-NOT-A-REAL-KEY", str(list(self.app.journal.entries)))
        self.sample_mock.assert_not_called()

    def test_clear_logs_preserves_cycle_and_ignores_already_queued_audit(self):
        self.begin()
        before = self.app.engine.snapshot()
        callback = self.app._audit_callback(self.app.ai_generation, self.app.engine.state["id"], self.app._log_epoch, "测试")
        callback({"event": "ai_response", "summary": "cleared-old-response", "details": {}})
        with patch("ironwill.app.messagebox.askyesno", return_value=True):
            self.app.clear_logs()
        self.app._drain_results()
        self.assertEqual(list(self.app.journal.entries), [])
        self.assertEqual(before, self.app.engine.snapshot())
        self.assertTrue(self.app.settings_path.exists())

    def test_logging_disk_failure_does_not_change_supervision_state(self):
        self.begin()
        before = self.app.engine.snapshot()
        with patch("ironwill.activity_log.Path.open", side_effect=PermissionError):
            self.app._log("判断", "test", "内存日志")
        self.assertEqual(before, self.app.engine.snapshot())
        self.assertIn("未能保存", self.app.journal.warning)

    def test_tray_hides_only_after_ready_and_does_not_pause_cycle(self):
        self.begin()
        before = self.app.engine.snapshot()
        native = Mock(ready=False)
        native.start.return_value = True
        with patch("ironwill.tray_windows.WindowsTray", return_value=native), patch.object(self.root, "withdraw") as hide:
            self.app.minimize_to_tray()
            hide.assert_not_called()
            self.app._tray_event("ready")
            hide.assert_called_once()
        self.assertEqual(before, self.app.engine.snapshot())
        with patch.object(self.app, "restore_window") as restore:
            self.app._tray_event("restore")
            restore.assert_called_once()
        self.assertEqual(before, self.app.engine.snapshot())

    def test_tray_failure_keeps_window_available_and_exit_uses_normal_close(self):
        native = Mock(ready=False)
        native.start.return_value = False
        with patch("ironwill.tray_windows.WindowsTray", return_value=native), patch.object(
                self.app, "restore_window") as restore, patch.object(self.root, "withdraw") as hide:
            self.app.minimize_to_tray()
            restore.assert_called_once()
            hide.assert_not_called()
        self.assertIsNone(self.app.tray)
        self.assertIn("保持可见", self.app.status_text.get())
        with patch.object(self.app, "restore_window") as restore, patch.object(self.app, "close") as close:
            self.app._tray_event("exit")
            restore.assert_called_once()
            close.assert_called_once()

    def test_tray_exit_during_poll_stops_timer_before_touching_destroyed_ui(self):
        self.begin()
        native = self.app.tray = Mock()
        self.app.events.put(("tray", "exit"))
        with patch("ironwill.app.messagebox.askyesno", return_value=True), patch.object(
                self.root, "destroy") as destroy, patch.object(self.app, "restore_window"), patch.object(
                self.app, "_poll_relay") as relay_poll:
            self.app._poll()
            destroy.assert_called_once()
            relay_poll.assert_not_called()
        self.assertTrue(self.app.closing)
        self.assertEqual(self.app.engine.state["status"], "ended")
        self.sample_mock.assert_not_called()
        native.stop.assert_called_once()

    def test_cycle_memory_avoids_worker_calls_and_reuses_after_pause(self):
        self.begin()
        self.app.ai = Mock()
        observation = Observation(process="browser", title="already reviewed")
        identity = self.app._review_identity(observation.process, observation.title)
        self.app.review_memory.remember(identity, self.app.engine.state["id"], self.result("related"))
        with patch("ironwill.app.threading.Thread") as worker:
            for _ in range(100):
                self.app._request_ai(observation)
            worker.assert_not_called()
        self.assertEqual(self.app.ai_cache_hits, 1)
        self.assertEqual(self.app.ai_api_count, 0)
        self.assertEqual(self.app.semantic_result[2], "related")
        self.app.pause_resume()
        self.app.pause_resume()
        with patch("ironwill.app.threading.Thread") as worker:
            self.app._request_ai(observation)
            worker.assert_not_called()
        self.assertEqual(self.app.semantic_result[2], "related")

    def test_new_window_needs_stable_dwell_and_network_interval(self):
        import time
        self.begin()
        self.app.ai = Mock()
        observation = Observation(process="browser", title="new page")
        with patch("ironwill.app.threading.Thread") as worker:
            self.app._request_ai(observation)
            worker.assert_not_called()
            self.app.ai_candidate = (observation.process, observation.title, time.monotonic() - 4)
            self.app.ai_last_request = time.monotonic()
            self.app._request_ai(observation)
            worker.assert_not_called()
            self.app.ai_last_request -= 31
            self.app._request_ai(observation)
            worker.assert_called_once()

    def test_failed_request_backoff_and_new_cycle_reset(self):
        import time
        self.begin()
        self.app.ai = Mock()
        self.app.last_observation = observation = Observation(process="browser", title="failed page")
        self.deliver_ai(available=False)
        identity = self.app._review_identity(observation.process, observation.title)
        self.assertIsNone(self.app.review_memory.lookup(identity, self.app.engine.state["id"]))
        self.assertEqual(self.app.ai_failures[identity][0], 1)
        self.app.ai_candidate = (observation.process, observation.title, time.monotonic() - 4)
        self.app.ai_last_request = 0
        with patch("ironwill.app.threading.Thread") as worker:
            self.app._request_ai(observation)
            worker.assert_not_called()
        self.assertIn("退避", self.app.ai_wait_reason)
        self.app.review_memory.remember(identity, self.app.engine.state["id"], self.result("related"))
        self.app.end()
        self.assertEqual(len(self.app.review_memory.entries), 0)
        self.assertEqual(self.app.ai_failures, {})

    def test_expired_memory_cannot_keep_refreshing_old_semantic_result(self):
        import time
        self.begin()
        self.app.ai = Mock()
        observation = Observation(process="browser", title="old page")
        identity = self.app._review_identity(observation.process, observation.title)
        self.app.review_memory.remember(identity, self.app.engine.state["id"], self.result("related"))
        self.app.review_memory.entries[identity]["created_at"] -= 901
        self.app.active_cache_mode = "15分钟"
        self.app.semantic_result = (observation.process, observation.title, "related", time.monotonic())
        self.app._request_ai(observation)
        self.assertIsNone(self.app.semantic_result)

    def test_restarted_paused_cycle_can_reuse_persisted_judgment(self):
        self.begin()
        self.configure_key()
        self.app.apply_ai()
        observation = Observation(process="browser", title="persisted page")
        identity = self.app._review_identity(observation.process, observation.title)
        self.app.review_memory.remember(identity, self.app.engine.state["id"], self.result("related"))
        reopened = IronWillApp(self.root, self.directory)
        self.assertEqual(reopened.engine.state["status"], "paused")
        reopened.ai_enabled.set(True)
        reopened.ai_key.set("SYNTHETIC-NOT-A-REAL-KEY")
        reopened.apply_ai()
        reopened.pause_resume()
        with patch("ironwill.app.threading.Thread") as worker:
            reopened._request_ai(observation)
            worker.assert_not_called()
        self.assertEqual(reopened.semantic_result[2], "related")

    def test_visiting_cached_window_restarts_new_window_dwell(self):
        import time
        self.begin()
        self.app.ai = Mock()
        first = Observation(process="browser", title="unreviewed")
        cached = Observation(process="browser", title="reviewed")
        identity = self.app._review_identity(cached.process, cached.title)
        self.app.review_memory.remember(identity, self.app.engine.state["id"], self.result("related"))
        self.app.ai_candidate = (first.process, first.title, time.monotonic() - 10)
        self.app._request_ai(cached)
        with patch("ironwill.app.threading.Thread") as worker:
            self.app._request_ai(first)
            worker.assert_not_called()
        self.assertIn("稳定", self.app.ai_wait_reason)

    def test_api_counter_counts_requests_not_responses_or_cache(self):
        for event in ("ai_request", "ai_response", "ai_cache"):
            self.app.events.put(("ai_audit", self.app.ai_generation, self.app.engine.state["id"],
                                 self.app._log_epoch, "合成测试", {"event": event, "summary": "test", "details": {}}))
        self.app._drain_results()
        self.assertEqual(self.app.ai_api_count, 1)
        self.assertEqual(self.app.ai_cache_hits, 0)


if __name__ == "__main__":
    unittest.main()

# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Synthetic checklist/UI checks: no real windows sampled, models called, or keys loaded."""
import io
import json
import os
from pathlib import Path
import tempfile
import tkinter as tk
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from ironwill.app import IronWillApp
from ironwill.ai import AIClient
from ironwill.core import Observation
from ironwill.goals import new_todo


class ChecklistTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory(prefix="ironwill-checklist-test-")
        self.directory = Path(self.temp.name)
        self.environment = patch.dict(os.environ, {"IRONWILL_API_KEY": "", "DEEPSEEK_API_KEY": ""})
        self.environment.start()
        self.sample = patch("ironwill.app.platform_windows.sample_foreground", return_value=Observation(status="unavailable"))
        self.sample_mock = self.sample.start()
        self.app = IronWillApp(self.root, self.directory)

    def tearDown(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.sample.stop()
        self.environment.stop()
        self.temp.cleanup()

    def goal(self, value):
        self.app.text_fields["goal"].delete("1.0", "end")
        self.app.text_fields["goal"].insert("1.0", value)

    def add(self, *lines):
        self.app.todo_input.insert("1.0", "\n".join(lines))
        self.assertTrue(self.app.add_todos())

    def test_legacy_goal_becomes_long_term_without_startup_write(self):
        self.app.settings_path.write_text(json.dumps({"goal": "legacy goal", "signature": "legacy"}), encoding="utf-8")
        before = self.app.settings_path.read_bytes()
        reopened = IronWillApp(self.root, self.directory)
        self.assertEqual(reopened.text_fields["goal"].get("1.0", "end-1c"), "legacy goal")
        self.assertEqual(reopened.todos, [])
        self.assertEqual(reopened.engine.state["status"], "idle")
        self.assertEqual(reopened.settings_path.read_bytes(), before)

    def test_add_check_and_uncheck_survive_reopen(self):
        self.goal("long term")
        self.app.save_identity()
        self.add("first task", "second task")
        first = self.app.todos[0]["id"]
        self.assertTrue(self.app.toggle_todo(first, True))
        reopened = IronWillApp(self.root, self.directory)
        self.assertEqual([item["done"] for item in reopened.todos], [True, False])
        self.assertEqual(reopened.todos[0]["id"], first)
        reopened.toggle_todo(first, False)
        self.assertFalse(json.loads(reopened.settings_path.read_text(encoding="utf-8"))["todos"][0]["done"])

    def test_pending_then_long_term_updates_reference_without_ending_or_clearing_stage(self):
        self.goal("long-term-only")
        self.add("short A", "short B")
        self.app.start()
        identifier = self.app.engine.state["id"]
        self.app.engine.state.update(stage=2, drift_seconds=77, recovery_seconds=20)
        self.assertNotIn("long-term-only", self.app.engine.state["goal"])
        self.app.toggle_todo(self.app.todos[0]["id"], True)
        self.assertNotIn("short A", self.app.engine.state["goal"])
        self.assertIn("short B", self.app.engine.state["goal"])
        self.app.toggle_todo(self.app.todos[1]["id"], True)
        self.assertEqual(self.app.engine.state["goal"], "long-term-only")
        self.assertEqual(self.app.engine.state["id"], identifier)
        self.assertEqual(self.app.engine.state["status"], "active")
        self.assertEqual(self.app.engine.state["stage"], 2)
        self.assertEqual(self.app.engine.state["drift_seconds"], 77)
        self.assertEqual(self.app.engine.state["recovery_seconds"], 0)
        self.app.toggle_todo(self.app.todos[0]["id"], False)
        self.assertIn("short A", self.app.engine.state["goal"])
        self.assertNotIn("long-term-only", self.app.engine.state["goal"])

    def test_check_does_not_save_or_send_uncommitted_long_term_edit(self):
        self.goal("saved long term")
        self.app.save_identity()
        self.add("short task")
        self.app.start()
        self.goal("UNSAVED-LONG-TERM")
        self.app.toggle_todo(self.app.todos[0]["id"], True)
        self.assertEqual(self.app.engine.state["goal"], "saved long term")
        self.assertEqual(self.app.settings["goal"], "saved long term")
        self.app.save_identity()
        self.assertEqual(self.app.engine.state["goal"], "UNSAVED-LONG-TERM")

    def test_short_term_alone_can_start_and_completion_without_long_term_stops_ai_only(self):
        self.add("short task")
        self.app.start()
        self.app.ai = Mock()
        self.app.toggle_todo(self.app.todos[0]["id"], True)
        with patch("ironwill.app.threading.Thread") as worker:
            self.app._request_ai(Observation(process="browser", title="page"))
            worker.assert_not_called()
        self.assertEqual(self.app.engine.state["status"], "active")
        self.assertEqual(self.app.engine.state["goal"], "")

    def test_changed_reference_invalidates_inflight_result_and_scope(self):
        self.goal("long-term")
        self.add("short task")
        self.app.start()
        self.app.ai = Mock()
        generation, scope = self.app.ai_generation, self.app._review_identity("browser", "page")
        self.app.ai_pending = True
        self.app.toggle_todo(self.app.todos[0]["id"], True)
        self.assertNotEqual(scope, self.app._review_identity("browser", "page"))
        self.app.events.put(("ai", generation, self.app.engine.state["id"], "browser", "page",
                             {"available": True, "verdict": "unrelated", "reason": "old result"}))
        self.app._drain_results()
        self.assertIsNone(self.app.semantic_result)
        self.assertFalse(self.app.ai_pending)
        self.assertIsNone(self.app.last_ai_details)

    def test_actual_request_only_contains_pending_reference(self):
        self.goal("LONG-TERM-MUST-NOT-BE-SENT")
        self.add("pending task", "completed task")
        self.app.toggle_todo(self.app.todos[1]["id"], True)
        self.app.start()
        self.app.ai = AIClient("https://example.invalid/v1", "model", "synthetic-key")
        observation = Observation(process="browser", title="sample page")
        self.app.ai_candidate = (observation.process, observation.title, time.monotonic() - 4)
        body = json.dumps({"choices": [{"message": {"content": "1"}}]}).encode()
        with patch("ironwill.app.threading.Thread", side_effect=lambda target, **_: Mock(start=target)), patch(
                "ironwill.ai._open_request", return_value=io.BytesIO(body)) as request:
            self.app._request_ai(observation)
        payload = json.loads(request.call_args.args[0].data)
        data = json.loads(payload["messages"][1]["content"])
        self.assertIn("pending task", data["goal"])
        self.assertNotIn("completed task", str(payload))
        self.assertNotIn("LONG-TERM-MUST-NOT-BE-SENT", str(payload))
        self.assertEqual(payload["max_tokens"], 1)

    def test_storage_failure_rolls_back_checkbox_and_reference(self):
        self.goal("long-term")
        self.add("pending task")
        self.app.start()
        self.app._show_floating()
        self.app.floating.icon.withdraw()
        self.app.floating.toggle_popup()
        self.app.floating.popup.withdraw()
        item = self.app.todos[0]
        before = self.app.settings_path.read_bytes(), self.app.engine.state["goal"], self.app.ai_generation
        with patch("ironwill.app._write_json", side_effect=OSError), patch("ironwill.app.messagebox.showerror"):
            self.assertFalse(self.app.toggle_todo(item["id"], True))
        self.assertFalse(self.app.todos[0]["done"])
        self.assertFalse(self.app.todo_view.rows[item["id"]]["value"].get())
        self.assertFalse(self.app.floating.view.rows[item["id"]]["value"].get())
        self.assertEqual((self.app.settings_path.read_bytes(), self.app.engine.state["goal"], self.app.ai_generation), before)

    def test_edit_keeps_identity_completion_and_remove_requires_confirmation(self):
        self.add("task")
        identifier = self.app.todos[0]["id"]
        self.app.toggle_todo(identifier, True)
        self.assertTrue(self.app.update_todo_text(identifier, "edited task"))
        self.assertEqual(self.app.todos[0], {"id": identifier, "text": "edited task", "done": True})
        with patch("ironwill.app.messagebox.askyesno", return_value=False):
            self.assertFalse(self.app.remove_todo(identifier))
        with patch("ironwill.app.messagebox.askyesno", return_value=True):
            self.assertTrue(self.app.remove_todo(identifier))
        self.assertEqual(self.app.todos, [])

    def test_bad_batch_is_atomic_and_keeps_editor_text(self):
        value = "valid\n" + "x" * 501
        self.app.todo_input.insert("1.0", value)
        self.assertFalse(self.app.add_todos())
        self.assertEqual(self.app.todos, [])
        self.assertEqual(self.app.todo_input.get("1.0", "end-1c"), value)

    def test_malformed_saved_list_is_not_rewritten_at_startup(self):
        self.app.settings_path.write_text('{"goal":"old","todos":[{"done":true}]}', encoding="utf-8")
        before = self.app.settings_path.read_bytes()
        reopened = IronWillApp(self.root, self.directory)
        self.assertTrue(reopened.load_warning)
        self.assertEqual(reopened.todos, [])
        self.assertEqual(reopened.settings_path.read_bytes(), before)

    def test_float_and_main_use_same_checklist_and_stable_checkbox(self):
        self.add("same task", "same task")
        self.app._show_floating()
        floating = self.app.floating
        floating.icon.withdraw()
        floating.toggle_popup()
        floating.popup.withdraw()
        identifier = self.app.todos[0]["id"]
        check = floating.view.rows[identifier]["check"]
        check.invoke()
        self.assertTrue(self.app.todo_view.rows[identifier]["value"].get())
        self.assertFalse(self.app.todos[1]["done"])
        self.assertIs(floating.view.rows[identifier]["check"], check)
        self.app.toggle_todo(identifier, False)
        self.assertFalse(floating.view.rows[identifier]["value"].get())
        floating.hide_popup()
        self.assertIsNone(floating.popup)
        self.assertTrue(floating.icon.winfo_exists())

    def test_drag_saves_position_and_does_not_open_popup(self):
        self.app._show_floating()
        floating = self.app.floating
        floating.icon.withdraw()
        self.root.update_idletasks()
        floating._press(SimpleNamespace(x_root=100, y_root=100))
        floating._motion(SimpleNamespace(x_root=130, y_root=120))
        self.root.update_idletasks()
        floating._release(None)
        self.assertIsNone(floating.popup)
        self.assertEqual(len(self.app.settings["floating_position"]), 2)
        self.assertEqual(json.loads(self.app.settings_path.read_text())["floating_position"], self.app.settings["floating_position"])

    def test_floating_hide_and_app_exit_release_windows(self):
        self.app.floating_enabled.set(True)
        self.app._toggle_floating()
        floating = self.app.floating
        floating.icon.withdraw()
        self.app.hide_floating()
        self.assertIsNone(floating.icon)
        self.assertFalse(self.app.settings["floating_enabled"])
        self.app._show_floating()
        icon = floating.icon
        icon.withdraw()
        with patch("ironwill.app.messagebox.askyesno", return_value=True), patch.object(self.root, "destroy"):
            self.app.close()
        self.assertIsNone(floating.icon)
        self.assertFalse(icon.winfo_exists())

    def test_reopen_reconciles_reference_without_rewriting_old_snapshot(self):
        self.goal("long term")
        self.add("short task")
        self.app.start()
        snapshot = self.app.snapshot_path.read_bytes()
        saved = json.loads(self.app.settings_path.read_text(encoding="utf-8"))
        saved["todos"][0]["done"] = True  # Simulate settings committed before a snapshot write failure.
        self.app.settings_path.write_text(json.dumps(saved), encoding="utf-8")
        reopened = IronWillApp(self.root, self.directory)
        self.assertEqual(reopened.engine.state["status"], "paused")
        self.assertEqual(reopened.engine.state["id"], self.app.engine.state["id"])
        self.assertEqual(reopened.engine.state["goal"], "long term")
        self.assertEqual(reopened.snapshot_path.read_bytes(), snapshot)

    def test_floating_stays_visible_when_main_window_is_hidden_to_tray(self):
        self.app._show_floating()
        floating = self.app.floating
        self.root.update_idletasks()
        self.app._tray_event("ready")  # UI routing only; does not create a native tray icon.
        self.root.update_idletasks()
        self.assertEqual(self.root.state(), "withdrawn")
        self.assertTrue(floating.icon.winfo_viewable())
        self.assertIsNone(self.root.grab_current())

    def test_startup_hidden_test_does_not_create_float_or_write(self):
        self.root.update_idletasks()
        self.assertIsNone(self.app.floating)
        self.assertEqual(list(self.directory.iterdir()), [])
        self.sample_mock.assert_not_called()

    def test_demo_checklist_stays_in_memory(self):
        demo = IronWillApp(self.root, self.directory, demo=True)
        demo.toggle_todo(demo.todos[0]["id"], True)
        self.assertTrue(demo.todos[0]["done"])
        self.assertEqual(list(self.directory.iterdir()), [])

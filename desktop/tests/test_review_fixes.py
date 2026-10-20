# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Regression cases found in the 0.1.7 review. Only temporary, synthetic data."""
import json
from pathlib import Path
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

from ironwill.app import IronWillApp


class ReviewFixTests(unittest.TestCase):
    def setUp(self):
        try:
            self.root = tk.Tk()
        except tk.TclError as error:
            self.skipTest(str(error))
        self.root.withdraw()
        self.temp = tempfile.TemporaryDirectory(prefix="ironwill-review-fix-")
        self.directory = Path(self.temp.name)
        self.errors = patch("ironwill.app.messagebox.showerror")
        self.error = self.errors.start()
        self.app = IronWillApp(self.root, self.directory)

    def tearDown(self):
        for callback in self.root.tk.call("after", "info"):
            self.root.after_cancel(callback)
        self.root.destroy()
        self.errors.stop()
        self.temp.cleanup()

    def goal(self, text, app=None):
        app = app or self.app
        app.text_fields["goal"].delete("1.0", "end")
        app.text_fields["goal"].insert("1.0", text)

    def add(self, text, app=None):
        app = app or self.app
        app.todo_input.insert("1.0", text)
        return app.add_todos()

    def begin(self):
        self.goal("LONG-TERM")
        self.assertTrue(self.add("OLD-TASK"))
        self.app.start()
        self.app.engine.state["stage"] = 1

    def test_bad_todo_preserves_other_fields_and_cannot_be_overwritten_by_moving_icon(self):
        path = self.app.settings_path
        path.write_text(json.dumps({"goal": "KEEP-GOAL", "signature": "KEEP-NAME", "todos": [{"done": 1}]}), encoding="utf-8")
        original = path.read_bytes()
        reopened = IronWillApp(self.root, self.directory)
        self.assertEqual(reopened.text_fields["goal"].get("1.0", "end-1c"), "KEEP-GOAL")
        self.assertEqual(reopened.signature.get(), "KEEP-NAME")
        reopened._floating_moved((100, 100))
        self.assertEqual(path.read_bytes(), original)
        self.assertFalse(self.add("must not replace original", reopened))
        self.assertEqual(path.read_bytes(), original)

    def test_stale_second_writer_does_not_replace_first_instances_todo(self):
        second = IronWillApp(self.root, self.directory)
        self.assertTrue(self.add("A-TASK"))
        self.assertFalse(self.add("B-TASK", second))
        saved = json.loads(self.app.settings_path.read_text(encoding="utf-8"))
        self.assertEqual([item["text"] for item in saved["todos"]], ["A-TASK"])
        self.assertEqual(second.todos, [])

    def test_too_large_save_preserves_previous_settings_and_in_memory_identity(self):
        self.goal("SAFE-GOAL")
        self.assertTrue(self.app.save_identity())
        original = self.app.settings_path.read_bytes()
        self.goal("x" * 1_050_000)
        self.assertFalse(self.app.save_identity())
        self.assertEqual(self.app.settings_path.read_bytes(), original)
        self.assertEqual(self.app.settings["goal"], "SAFE-GOAL")
        reopened = IronWillApp(self.root, self.directory)
        self.assertFalse(reopened.load_warning)
        self.assertEqual(reopened.settings["goal"], "SAFE-GOAL")

    def test_delayed_reminder_uses_current_goal_after_todo_completed(self):
        self.begin()
        self.app.pending_reminder = {"session_id": self.app.engine.state["id"], "stage": 1,
            "message": self.app.engine._message(1), "event_id": "synthetic-event", "retry_at": 0}
        identifier = self.app.engine.state["id"]
        self.app.toggle_todo(self.app.todos[0]["id"], True)
        with patch.object(self.app, "_show_reminder") as show:
            self.app._retry_reminder()
        self.assertEqual(show.call_count, 1)
        self.assertIn("LONG-TERM", show.call_args.args[1])
        self.assertNotIn("OLD-TASK", show.call_args.args[1])
        self.assertEqual(self.app.engine.state["id"], identifier)
        self.assertEqual(self.app.engine.state["stage"], 1)

    def test_visible_real_reminder_updates_without_recreating_or_raising_window(self):
        self.begin()
        self.app._show_reminder(1, self.app.engine._message(1))
        window = self.app.dialog
        window.withdraw()
        with patch.object(window, "lift") as lift:
            self.app.toggle_todo(self.app.todos[0]["id"], True)
            lift.assert_not_called()
        self.assertIs(self.app.dialog, window)
        text = self.app.dialog_body.get("1.0", "end-1c")
        self.assertIn("LONG-TERM", text)
        self.assertNotIn("OLD-TASK", text)

    def test_invalid_json_and_oversized_file_stay_read_only(self):
        for raw in (b"{broken", b" " * 1_048_577):
            self.app.settings_path.write_bytes(raw)
            reopened = IronWillApp(self.root, self.directory)
            reopened._floating_moved((20, 30))
            self.goal("new", reopened)
            self.assertFalse(reopened.save_identity())
            self.assertEqual(reopened.settings_path.read_bytes(), raw)

    def test_corrupt_list_cannot_be_treated_as_completed_when_resuming(self):
        self.begin()
        snapshot = self.app.snapshot_path.read_bytes()
        self.app.settings_path.write_text('{"goal":"LONG-TERM","todos":[{"done":1}]}', encoding="utf-8")
        reopened = IronWillApp(self.root, self.directory)
        self.assertEqual(reopened.engine.state["status"], "paused")
        self.assertIn("OLD-TASK", reopened.engine.state["goal"])
        reopened.pause_resume()
        self.assertEqual(reopened.engine.state["status"], "paused")
        self.assertEqual(reopened.snapshot_path.read_bytes(), snapshot)

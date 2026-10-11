# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ironwill.core import Policy
from ironwill.review_memory import ReviewMemory, review_identity


class ReviewMemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ironwill-memory-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.memory = ReviewMemory(self.directory, limit=3)
        self.result = {"available": True, "verdict": "related", "reason": "合成测试"}

    def identity(self, **changes):
        values = dict(goal="goal", process="browser.exe", title="document", policy=Policy(),
                      endpoint="https://example.invalid", model="model", revision="v1", scope="cycle-a")
        values.update(changes)
        return review_identity(**values)

    def test_cycle_cache_survives_reopen_but_not_another_cycle(self):
        key = self.identity()
        self.memory.remember(key, "cycle-a", self.result)
        reopened = ReviewMemory(self.directory)
        with patch("ironwill.review_memory.time.time", return_value=next(iter(self.memory.entries.values()))["created_at"] + 86400):
            self.assertTrue(reopened.lookup(key, "cycle-a")["cached"])
        self.assertIsNone(reopened.lookup(key, "cycle-b"))
        self.assertNotIn("browser.exe", self.memory.path.read_text(encoding="utf-8"))

    def test_timed_cache_expires_and_clock_rewind_is_rejected(self):
        key = self.identity()
        with patch("ironwill.review_memory.time.time", return_value=1000):
            self.memory.remember(key, "cycle-a", self.result)
        for now, valid in ((1001, True), (1899, True), (1900, False), (900, False)):
            with patch("ironwill.review_memory.time.time", return_value=now):
                self.assertEqual(self.memory.lookup(key, "cycle-a", 900) is not None, valid)

    def test_goal_policy_provider_model_revision_and_window_all_scope_identity(self):
        key = self.identity()
        for changes in (dict(goal="new goal"), dict(policy=Policy(blocked_titles=["document"])),
                        dict(endpoint="https://other.invalid"), dict(model="other"), dict(revision="v2"),
                        dict(scope="cycle-b"), dict(title="unrelated tab"), dict(process="other.exe")):
            self.assertNotEqual(key, self.identity(**changes))
        self.assertEqual(key, self.identity(process=" Browser.EXE ", title=" DOCUMENT "))

    def test_unknown_only_reused_briefly_and_failures_never_become_verdicts(self):
        key = self.identity()
        with patch("ironwill.review_memory.time.time", return_value=1000):
            self.memory.remember(key, "cycle-a", {**self.result, "verdict": "unknown"})
        with patch("ironwill.review_memory.time.time", return_value=1300):
            self.assertIsNone(self.memory.lookup(key, "cycle-a"))
        self.memory.remember("another", "cycle-a", {**self.result, "available": False})
        self.assertNotIn("another", self.memory.entries)

    def test_memory_limit_and_clear_do_not_touch_settings(self):
        self.directory.joinpath("ui_settings.json").write_text("keep", encoding="utf-8")
        for index in range(8):
            self.memory.remember(self.identity(title=str(index)), "cycle-a", self.result)
        self.assertEqual(len(self.memory.entries), 3)
        self.assertEqual(len(ReviewMemory(self.directory).entries), 3)
        self.memory.clear()
        self.assertFalse(self.memory.path.exists())
        self.assertEqual(self.directory.joinpath("ui_settings.json").read_text(), "keep")

    def test_corrupt_cache_is_not_used_and_write_failure_is_memory_only(self):
        self.memory.path.write_text("broken", encoding="utf-8")
        reopened = ReviewMemory(self.directory)
        self.assertIn("重新审核", reopened.warning)
        key = self.identity()
        with patch("ironwill.review_memory.os.replace", side_effect=PermissionError):
            reopened.remember(key, "cycle-a", self.result)
        self.assertTrue(reopened.lookup(key, "cycle-a")["available"])
        self.assertIn("未能保存", reopened.warning)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])

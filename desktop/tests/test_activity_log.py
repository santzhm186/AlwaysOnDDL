# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ironwill.activity_log import ActivityLog, redact


class ActivityLogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ironwill-log-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_lazy_construction_and_persistent_reopen(self):
        log = ActivityLog(self.directory)
        self.assertEqual(list(self.directory.iterdir()), [])
        log.record("判断", "observation", "测试窗口", {"title": "测试标题"})
        reopened = ActivityLog(self.directory)
        self.assertEqual(list(reopened.entries), list(log.entries))
        self.assertRegex(reopened.entries[0]["timestamp"], r"[+-]\d\d:\d\d$")

    def test_rotation_and_memory_are_bounded(self):
        log = ActivityLog(self.directory, max_bytes=1800, max_entries=3)
        for number in range(20):
            log.record("判断", "observation", str(number), {"title": "x" * 300})
        self.assertEqual(len(log.entries), 3)
        files = list(log.directory.glob("*.jsonl"))
        self.assertEqual(len(files), 2)
        self.assertTrue(all(path.stat().st_size <= 1800 for path in files))
        self.assertEqual(ActivityLog(self.directory, max_bytes=1800).entries[-1]["summary"], "19")

    def test_redacts_nested_headers_known_secret_and_escaped_secret(self):
        secret = 'SYNTHETIC-"SECRET'
        log = ActivityLog(self.directory)
        log.protect_secret(secret)
        log.record("AI通信", "ai_response", "合成测试", {
            "Authorization": "Bearer " + secret,
            "nested": [{"api_key": secret}],
            "response_body": json.dumps({"text": secret}),
            "other": "Bearer unknown-credential",
        })
        raw = (log.directory / "activity.jsonl").read_text(encoding="utf-8")
        self.assertNotIn("SYNTHETIC", raw)
        self.assertNotIn("unknown-credential", raw)
        self.assertIn("已屏蔽", raw)
        self.assertEqual(redact('{"api_key":"unregistered-value"}'), '{"api_key":"[已屏蔽]"}')

    def test_corrupt_tail_does_not_discard_readable_records(self):
        log = ActivityLog(self.directory)
        log.record("判断", "test", "保留")
        with (log.directory / "activity.jsonl").open("ab") as stream:
            stream.write(b'{"broken":')
        loaded = ActivityLog(self.directory)
        self.assertEqual(len(loaded.entries), 1)
        self.assertIn("跳过", loaded.warning)
        loaded.record("判断", "test", "中断后新记录")
        self.assertEqual(ActivityLog(self.directory).entries[-1]["summary"], "中断后新记录")

    def test_duplicate_saved_ids_cannot_break_timeline(self):
        log = ActivityLog(self.directory)
        log.record("判断", "test", "保留一次")
        path = log.directory / "activity.jsonl"
        path.write_bytes(path.read_bytes() * 2)
        self.assertEqual(len(ActivityLog(self.directory).entries), 1)

    def test_disk_failure_retains_memory_and_reports_failure(self):
        log = ActivityLog(self.directory)
        with patch("ironwill.activity_log.Path.open", side_effect=PermissionError):
            log.record("判断", "test", "仍可查看")
        self.assertEqual(len(log.entries), 1)
        self.assertIn("未能保存", log.warning)

    def test_clear_removes_only_journal_files_and_survives_reopen(self):
        log = ActivityLog(self.directory, max_bytes=1024)
        self.directory.joinpath("ui_settings.json").write_text("keep", encoding="utf-8")
        for _ in range(8):
            log.record("程序", "test", "清理", {"test": "x" * 300})
        self.assertTrue(log.clear())
        self.assertEqual(list(ActivityLog(self.directory).entries), [])
        self.assertEqual(self.directory.joinpath("ui_settings.json").read_text(), "keep")

    def test_clear_failure_is_not_reported_as_success(self):
        log = ActivityLog(self.directory)
        log.record("判断", "test", "保留")
        with patch("ironwill.activity_log.Path.unlink", side_effect=PermissionError):
            self.assertFalse(log.clear())
        self.assertEqual(len(log.entries), 1)
        self.assertIn("未能删除", log.warning)

    def test_demo_is_memory_only_and_large_details_have_limit(self):
        log = ActivityLog(None)
        log.record("AI通信", "test", "长正文", {"body": "中" * 50000})
        self.assertLess(len(log.entries[0]["details"]["body"]), 12100)
        self.assertTrue(log.clear())
        self.assertEqual(list(self.directory.iterdir()), [])

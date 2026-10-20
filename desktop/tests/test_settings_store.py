# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ironwill.goals import new_todo
from ironwill.settings_store import (MAX_SETTINGS_BYTES, SettingsConflict, SettingsError,
    SettingsReadOnly, SettingsStore, encode_settings)


class SettingsStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ironwill-settings-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / "ui_settings.json"
        self.store = SettingsStore(self.path)
        self.store.load()

    def test_missing_load_does_not_create_files(self):
        self.assertEqual(self.store.load(), {})
        self.assertEqual(list(self.directory.iterdir()), [])

    def test_round_trip_preserves_todos_and_filters_legacy_unknown_fields(self):
        self.path.write_text(json.dumps({"goal": "goal", "api_key": "SYNTHETIC-LEGACY-KEY"}), encoding="utf-8")
        loaded = self.store.load()
        self.assertNotIn("api_key", loaded)
        loaded["todos"] = [new_todo("task")]
        self.store.save(loaded)
        reopened = SettingsStore(self.path)
        self.assertEqual(reopened.load(), loaded)
        self.assertNotIn("SYNTHETIC-LEGACY-KEY", self.path.read_text(encoding="utf-8"))

    def test_partial_corruption_keeps_valid_fields_but_protects_original(self):
        raw = json.dumps({"goal": "keep", "oath": "keep too", "todos": [{"done": 1}]}).encode()
        self.path.write_bytes(raw)
        loaded = self.store.load()
        self.assertEqual(loaded["goal"], "keep")
        self.assertEqual(loaded["oath"], "keep too")
        self.assertEqual(loaded["todos"], [])
        self.assertTrue(self.store.read_only)
        with self.assertRaises(SettingsReadOnly):
            self.store.save({"floating_position": [10, 20]})
        self.assertEqual(self.path.read_bytes(), raw)

    def test_invalid_optional_field_also_protects_original(self):
        self.path.write_text('{"goal":"keep","floating_enabled":"true"}', encoding="utf-8")
        self.assertEqual(self.store.load()["goal"], "keep")
        self.assertTrue(self.store.read_only)

    def test_exact_utf8_byte_limit_is_readable_and_one_byte_more_is_rejected(self):
        overhead = len(encode_settings({"goal": ""}))
        count, extra = divmod(MAX_SETTINGS_BYTES - overhead, 3)
        value = {"goal": "中" * count + "x" * extra}
        self.store.save(value)
        original = self.path.read_bytes()
        self.assertEqual(len(original), MAX_SETTINGS_BYTES)
        reopened = SettingsStore(self.path)
        self.assertEqual(reopened.load(), value)
        self.assertFalse(reopened.read_only)
        with self.assertRaises(SettingsError):
            self.store.save({"goal": value["goal"] + "x"})
        self.assertEqual(self.path.read_bytes(), original)

    def test_old_crlf_file_can_be_saved_repeatedly_with_consistent_digest(self):
        self.path.write_bytes(b'{\r\n  "goal": "old"\r\n}')
        self.store.load()
        for goal in ("first", "second"):
            self.store.save({"goal": goal})
            self.assertEqual(self.path.read_bytes(), encode_settings({"goal": goal}))

    def test_stale_reader_cannot_overwrite_created_file(self):
        other = SettingsStore(self.path)
        other.load()
        self.store.save({"goal": "first writer"})
        before = self.path.read_bytes()
        with self.assertRaises(SettingsConflict):
            other.save({"goal": "stale writer"})
        self.assertEqual(self.path.read_bytes(), before)

    def test_external_edits_and_deletion_require_explicit_reload(self):
        self.store.save({"goal": "original"})
        self.path.write_text('{"goal":"external"}', encoding="utf-8")
        with self.assertRaises(SettingsConflict):
            self.store.save({"goal": "do not overwrite"})
        self.assertEqual(json.loads(self.path.read_text())["goal"], "external")
        self.store.load()
        self.path.unlink()
        with self.assertRaises(SettingsConflict):
            self.store.save({"goal": "do not recreate"})
        self.assertFalse(self.path.exists())

    def test_replace_failure_preserves_file_cleans_temp_and_releases_write_lock(self):
        self.store.save({"goal": "old"})
        original = self.path.read_bytes()
        with patch("ironwill.settings_store.os.replace", side_effect=PermissionError), self.assertRaises(OSError):
            self.store.save({"goal": "failed"})
        self.assertEqual(self.path.read_bytes(), original)
        self.assertEqual(list(self.directory.glob("*.tmp")), [])
        self.store.save({"goal": "retry"})
        self.assertEqual(json.loads(self.path.read_text())["goal"], "retry")

    def test_manual_repair_then_explicit_reload_clears_read_only(self):
        self.path.write_bytes(b"invalid")
        self.store.load()
        self.assertTrue(self.store.read_only)
        self.path.write_text('{"goal":"repaired"}', encoding="utf-8")
        self.assertEqual(self.store.load()["goal"], "repaired")
        self.assertFalse(self.store.read_only)
        self.assertFalse(self.store.warning)
        self.store.save({"goal": "new"})

    def test_invalid_candidate_is_rejected_before_touching_file(self):
        self.store.save({"goal": "safe"})
        original = self.path.read_bytes()
        for value in ({"todos": [{"done": 1}]}, {"goal": 3}, {"api_key": "do not persist"}):
            with self.assertRaises(SettingsError):
                self.store.save(value)
        self.assertEqual(self.path.read_bytes(), original)

    def test_saving_without_loading_is_not_allowed(self):
        with self.assertRaises(SettingsError):
            SettingsStore(self.directory / "new.json").save({"goal": "new"})

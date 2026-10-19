# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
import unittest

from ironwill.goals import MAX_TODOS, new_todo, reference_goal, validate_todos


class GoalTests(unittest.TestCase):
    def test_duplicate_text_keeps_independent_identity(self):
        first, second = new_todo("same task"), new_todo("same task")
        self.assertNotEqual(first["id"], second["id"])
        self.assertFalse(first["done"])
        self.assertEqual(len(validate_todos([first, second])), 2)

    def test_pending_only_excludes_long_term_and_completed_items(self):
        tasks = [new_todo("pending task"), {**new_todo("completed task"), "done": True}]
        reference = reference_goal("PRIVATE-LONG-TERM", tasks)
        self.assertIn("pending task", reference)
        self.assertNotIn("completed task", reference)
        self.assertNotIn("PRIVATE-LONG-TERM", reference)

    def test_all_completed_or_empty_falls_back_to_long_term(self):
        self.assertEqual(reference_goal(" long-term ", []), "long-term")
        task = {**new_todo("done"), "done": True}
        self.assertEqual(reference_goal("long-term", [task]), "long-term")
        self.assertEqual(reference_goal("", [task]), "")

    def test_reopened_task_takes_precedence_again(self):
        task = new_todo("short-term")
        self.assertIn("short-term", reference_goal("long-term", [task]))
        task["done"] = True
        self.assertEqual(reference_goal("long-term", [task]), "long-term")
        task["done"] = False
        self.assertNotIn("long-term", reference_goal("long-term", [task]))

    def test_long_list_represents_every_pending_item_within_http_budget(self):
        tasks = [new_todo(f"item-{i:03d} " + "x" * 480) for i in range(MAX_TODOS)]
        reference = reference_goal("excluded", tasks)
        self.assertLessEqual(len(reference), 4000)
        for i in range(MAX_TODOS):
            self.assertIn(f"item-{i:03d}", reference)
        self.assertIn("…", reference)
        self.assertEqual(len(tasks[-1]["text"]), 489)  # Stored text was not truncated.

    def test_validator_rejects_malformed_or_oversized_lists(self):
        item = new_todo("task")
        bad = (None, {}, [item, item], [{**item, "done": 1}], [{**item, "text": ""}],
               [{**item, "id": "not-an-id"}], [{**item, "extra": "field"}],
               [new_todo("task") for _ in range(MAX_TODOS + 1)])
        for value in bad:
            with self.subTest(value=str(value)[:80]), self.assertRaises(ValueError):
                validate_todos(value)
        for text in (" ", "x" * 501, None):
            with self.assertRaises(ValueError):
                new_todo(text)

    def test_validation_returns_independent_records(self):
        original = [new_todo("task")]
        clean = validate_todos(original)
        clean[0]["done"] = True
        self.assertFalse(original[0]["done"])

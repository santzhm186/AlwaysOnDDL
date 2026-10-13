# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Pure engine regression tests: no model calls or operating-system actions."""
import json
from pathlib import Path
import tempfile
import unittest

from ironwill.core import Engine, Observation, Policy, classify, load_policy, load_snapshot, save_policy, save_snapshot


class CoreTests(unittest.TestCase):
    def setUp(self):
        self.policy = Policy(blocked_processes=["game.exe"], allowed_processes=["editor.exe"])
        self.engine = Engine(self.policy)
        self.engine.start("实现当前功能", "用户的原文。\n第二行。")
        self.drift = Observation(process="game.exe", title="play")
        self.work = Observation(process="editor.exe", title="code")
        self.engine.tick(self.drift, 0)  # First observation establishes a baseline.

    def advance(self, seconds, sample=None, step=10):
        events = []
        sample = sample or self.drift
        while seconds > 0:
            dt = min(step, seconds)
            events.extend(self.engine.tick(sample, dt))
            seconds -= dt
        return events

    def test_privacy_precedes_blocked_process(self):
        self.assertEqual(classify("game.exe", "Password", self.policy), "PRIVATE")
        self.advance(2000, Observation(process="game.exe", title="Password"))
        self.assertEqual(self.engine.state["stage"], 0)
        self.assertEqual(self.engine.state["drift_seconds"], 0)

    def test_process_allowlist_precedes_title_redline(self):
        policy = Policy(allowed_processes=["EDITOR.EXE"], blocked_titles=["video"])
        self.assertEqual(classify("editor.exe", "video_parser.py", policy), "ON_TASK")

    def test_title_conflict_is_gray(self):
        policy = Policy(blocked_titles=["video"], allowed_titles=["tutorial"])
        self.assertEqual(classify("browser", "video tutorial", policy), "GRAY")

    def test_neutral_semantic_neither_reminds_nor_proves_recovery(self):
        self.advance(40)
        self.assertEqual(self.engine.state["stage"], 1)
        drift = self.engine.state["drift_seconds"]
        neutral = Observation(process="browser", title="team notification", semantic="neutral")
        self.assertEqual(self.advance(120, neutral), [])
        self.assertEqual(self.engine.state["stage"], 1)
        self.assertEqual(self.engine.state["drift_seconds"], drift)
        self.assertEqual(self.engine.state["recovery_seconds"], 0)
        related = Observation(process="browser", title="task documentation", semantic="related")
        events = self.advance(60, related)
        self.assertTrue(any(event["type"] == "recovered" for event in events))
        self.assertEqual(self.engine.state["stage"], 0)

    def test_empty_keyword_cannot_match_everything(self):
        policy = Policy(blocked_processes=["", "  "])
        self.assertEqual(classify("editor", "file", policy), "GRAY")

    def test_first_and_later_thresholds(self):
        self.assertEqual(self.advance(29), [])
        self.assertEqual(self.advance(1)[0]["stage"], 1)
        self.assertEqual(self.advance(899), [])
        self.assertEqual(self.advance(1)[0]["stage"], 2)
        self.assertEqual(self.advance(900)[0]["stage"], 3)
        self.assertEqual(self.advance(3600), [])

    def test_back_requires_observed_continuous_recovery(self):
        self.advance(30)
        self.engine.choose("BACK")
        self.assertEqual(self.engine.state["stage"], 1)
        self.advance(59, self.work)
        self.assertEqual(self.engine.state["stage"], 1)
        self.advance(1, self.work)
        self.assertEqual(self.engine.state["stage"], 0)
        self.assertFalse(self.engine.state["return_pending"])

    def test_recovery_must_be_continuous(self):
        self.advance(30)
        self.advance(40, self.work)
        self.advance(10, self.drift)
        self.advance(40, self.work)
        self.assertEqual(self.engine.state["stage"], 1)

    def test_breathe_does_not_erase_stage(self):
        self.advance(30)
        self.engine.choose("BREATHE", "休息片刻")
        self.assertEqual(self.engine.state["stage"], 1)
        self.assertEqual(self.advance(899), [])
        self.assertEqual(self.advance(1)[0]["stage"], 2)

    def test_worth_quiet_interval_is_not_sleep_time(self):
        self.advance(30)
        self.engine.choose("WORTH")
        self.engine.tick(self.drift, 3600)
        self.assertEqual(self.engine.state["quiet_remaining"], 900)
        self.assertEqual(self.engine.state["drift_seconds"], 30)

    def test_ai_unrelated_never_advances_strong_state(self):
        events = self.advance(3600, Observation(process="browser", title="new tab", semantic="unrelated"))
        self.assertTrue(any(e["type"] == "gray_hint" for e in events))
        self.assertFalse(any(e["type"] == "reminder" for e in events))
        self.assertEqual(self.engine.state["stage"], 0)
        self.assertEqual(self.engine.state["drift_seconds"], 0)

    def test_ai_related_can_establish_recovery(self):
        self.advance(30)
        self.advance(60, Observation(process="browser", title="docs", semantic="related"))
        self.assertEqual(self.engine.state["stage"], 0)

    def test_technical_unknown_is_not_punished(self):
        for sample in (Observation(process="game.exe", status="unavailable"),
                       Observation(process="game.exe", locked=True),
                       Observation(process="game.exe", idle_seconds=None),
                       Observation(process="game.exe", idle_seconds=120), Observation()):
            self.advance(1000, sample)
        self.assertEqual(self.engine.state["stage"], 0)
        self.assertEqual(self.engine.state["drift_seconds"], 0)

    def test_unknown_does_not_establish_recovery(self):
        self.advance(30)
        self.advance(60, Observation(process="browser", title="unknown", semantic="unknown"))
        self.assertEqual(self.engine.state["stage"], 1)

    def test_pause_resume_never_backfills_elapsed_time(self):
        self.advance(20)
        self.engine.pause()
        self.advance(1000)
        self.engine.resume()
        self.engine.tick(self.drift, 20)
        self.assertEqual(self.engine.state["drift_seconds"], 20)
        self.assertEqual(self.advance(10)[0]["stage"], 1)

    def test_end_stops_all_timers(self):
        self.advance(20)
        self.engine.end()
        self.advance(1000)
        self.assertEqual(self.engine.state["status"], "ended")
        self.assertEqual(self.engine.state["drift_seconds"], 20)

    def test_lock_requires_explicit_third_stage_and_opt_in(self):
        self.assertEqual(self.engine.choose("LOCK")[0]["type"], "error")
        events = self.advance(1830)
        self.assertFalse(any(e["type"] == "lock_requested" for e in events))
        self.assertEqual(self.engine.choose("LOCK")[0]["type"], "error")
        self.engine.update_policy(Policy(blocked_processes=["game.exe"], lock_enabled=True))
        self.assertEqual(self.engine.choose("LOCK")[0]["type"], "lock_requested")

    def test_third_stage_cannot_receive_more_quiet_choices(self):
        self.advance(1830)
        self.assertEqual(self.engine.choose("WORTH")[0]["type"], "error")
        self.assertEqual(self.engine.choose("BREATHE")[0]["type"], "error")
        self.assertEqual(self.engine.choose("closed_window")[0]["type"], "error")
        self.assertEqual(self.engine.state["lock_requests"], 0)

    def test_oath_is_preserved_verbatim(self):
        events = self.advance(1830)
        self.assertIn("用户的原文。\n第二行。", events[-1]["message"])

    def test_reality_and_consequence_are_preserved_verbatim(self):
        self.engine.start("目标", oath="原誓言", reality="  我写的现实。\n第二行  ", consequence="后果原文！")
        self.engine.tick(self.drift, 0)
        first = self.advance(30)[0]
        second = self.advance(900)[0]
        self.assertIn("  我写的现实。\n第二行  ", first["message"])
        self.assertIn("后果原文！", second["message"])
        snapshot = self.engine.snapshot()
        restored = Engine(self.policy, snapshot)
        self.assertEqual(restored.state["reality"], "  我写的现实。\n第二行  ")
        self.assertEqual(restored.state["consequence"], "后果原文！")

    def test_empty_pressure_fields_do_not_invent_consequences(self):
        self.engine.start("目标")
        self.engine.tick(self.drift, 0)
        first = self.advance(30)[0]
        second = self.advance(900)[0]
        self.assertNotIn("你写下的处境", first["message"])
        self.assertNotIn("你写下的后果", second["message"])

    def test_snapshot_restores_paused_without_replay(self):
        self.advance(930)
        restored = Engine(self.policy, self.engine.snapshot())
        self.assertEqual(restored.state["status"], "paused")
        self.assertEqual(restored.state["stage"], 2)
        restored.resume()
        self.assertEqual(restored.tick(self.drift, 20000), [])
        self.assertEqual(restored.state["drift_seconds"], 930)

    def test_new_session_has_new_identity_and_no_daily_boundary(self):
        self.advance(30)
        old = self.engine.state["id"]
        self.engine.start("另一条主线")
        self.assertNotEqual(self.engine.state["id"], old)
        self.assertEqual(self.engine.state["stage"], 0)
        self.assertNotIn("date", self.engine.snapshot())

    def test_invalid_policy_fails_explicitly(self):
        for data in ({"quiet_seconds": 0}, {"lock_enabled": "true"},
                     {"blocked_titles": "video"}, {"reminder_after_seconds": float("nan")},
                     {"api_key": "must-not-save"}):
            with self.assertRaises(ValueError):
                Policy.from_dict(data)

    def test_atomic_snapshot_and_policy_round_trip(self):
        with tempfile.TemporaryDirectory() as directory:
            snapshot_path = Path(directory) / "session.json"
            policy_path = Path(directory) / "policy.json"
            save_snapshot(snapshot_path, self.engine.snapshot())
            save_policy(policy_path, self.policy)
            self.assertEqual(load_snapshot(snapshot_path), self.engine.snapshot())
            self.assertEqual(load_policy(policy_path).to_dict(), self.policy.to_dict())
            self.assertEqual(len(list(Path(directory).glob("*.tmp"))), 0)
            self.assertIsNone(load_snapshot(Path(directory) / "absent.json"))

    def test_corrupt_snapshot_is_not_silently_reset(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "session.json"
            path.write_text("not-json", encoding="utf-8")
            with self.assertRaises(json.JSONDecodeError):
                load_snapshot(path)
            bad = self.engine.snapshot()
            bad["stage"] = 999
            with self.assertRaises(ValueError):
                save_snapshot(path, bad)
            self.assertEqual(path.read_text(encoding="utf-8"), "not-json")

    def test_incomplete_snapshot_is_rejected(self):
        with self.assertRaises(ValueError):
            Engine(snapshot={"schema_version": 1})


if __name__ == "__main__":
    unittest.main()

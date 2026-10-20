# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from ironwill.app import launch
from ironwill.directory_lock import DirectoryBusyError, DirectoryLock

DESKTOP = Path(__file__).resolve().parents[1]
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


class DirectoryLockTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ironwill-lock-test-")
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_same_directory_alias_is_exclusive_and_release_is_idempotent(self):
        first = DirectoryLock(self.directory).acquire()
        try:
            with self.assertRaises(DirectoryBusyError):
                DirectoryLock(self.directory / ".").acquire()
            with DirectoryLock(self.directory / "other"):
                pass
        finally:
            first.release()
            first.release()
        with DirectoryLock(self.directory):
            pass

    def test_another_process_cannot_acquire_while_parent_holds_lock(self):
        code = "from ironwill.directory_lock import *; import sys\ntry:\n DirectoryLock(sys.argv[1]).acquire()\nexcept DirectoryBusyError:\n sys.exit(17)\n"
        with DirectoryLock(self.directory):
            child = subprocess.run([sys.executable, "-c", code, str(self.directory)], cwd=DESKTOP,
                capture_output=True, timeout=10, creationflags=HIDDEN)
        self.assertEqual(child.returncode, 17, child.stderr.decode(errors="replace"))

    def test_os_releases_lock_when_own_test_child_is_terminated(self):
        ready = self.directory / "ready"
        code = "from ironwill.directory_lock import DirectoryLock; from pathlib import Path; import sys,time\nwith DirectoryLock(sys.argv[1]):\n Path(sys.argv[2]).write_text('ready')\n time.sleep(60)\n"
        child = subprocess.Popen([sys.executable, "-c", code, str(self.directory), str(ready)], cwd=DESKTOP,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=HIDDEN)
        try:
            deadline = time.monotonic() + 8
            while not ready.exists() and child.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(ready.exists(), "Synthetic lock child did not become ready")
            with self.assertRaises(DirectoryBusyError):
                DirectoryLock(self.directory).acquire()
        finally:
            if child.poll() is None:
                child.kill()  # Only this test's synthetic child, never a user's IronWill.
            child.communicate(timeout=5)
        with DirectoryLock(self.directory):
            pass

    def test_duplicate_launch_is_rejected_before_tk_or_app_reads(self):
        with DirectoryLock(self.directory), patch("ironwill.app._startup_error") as notice, patch(
                "ironwill.app.tk.Tk") as root, patch("ironwill.app.IronWillApp") as app:
            self.assertEqual(launch(self.directory), 1)
            notice.assert_called_once()
            root.assert_not_called()
            app.assert_not_called()

    def test_simultaneous_settings_writers_have_one_winner_without_lost_update(self):
        code = """from pathlib import Path
from ironwill.settings_store import SettingsStore, SettingsConflict
import sys,time
folder=Path(sys.argv[1]); name=sys.argv[2]
store=SettingsStore(folder/'ui_settings.json'); store.load()
(folder/('ready-'+name)).write_text('ready')
while not (folder/'go').exists(): time.sleep(0.01)
try: store.save({'goal':name})
except SettingsConflict: sys.exit(17)
"""
        children = [subprocess.Popen([sys.executable, "-c", code, str(self.directory), name], cwd=DESKTOP,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=HIDDEN) for name in ("A", "B")]
        try:
            deadline = time.monotonic() + 8
            while not all((self.directory / ("ready-" + name)).exists() for name in ("A", "B")) and time.monotonic() < deadline:
                time.sleep(0.02)
            self.assertTrue(all((self.directory / ("ready-" + name)).exists() for name in ("A", "B")))
            (self.directory / "go").write_text("go")
            for child in children:
                child.communicate(timeout=10)
            self.assertEqual(sorted(child.returncode for child in children), [0, 17])
            from ironwill.settings_store import SettingsStore
            self.assertIn(SettingsStore(self.directory / "ui_settings.json").load()["goal"], ("A", "B"))
        finally:
            for child in children:
                if child.poll() is None:
                    child.kill()
                child.communicate(timeout=5)

    def test_launch_holds_lock_until_mainloop_exits(self):
        root = Mock()
        def check_held():
            with self.assertRaises(DirectoryBusyError):
                DirectoryLock(self.directory).acquire()
        root.mainloop.side_effect = check_held
        with patch("ironwill.app.tk.Tk", return_value=root), patch("ironwill.app.IronWillApp"):
            self.assertEqual(launch(self.directory), 0)
        root.destroy.assert_called_once()
        with DirectoryLock(self.directory):
            pass

    def test_constructor_failure_releases_lock(self):
        with patch("ironwill.app.tk.Tk"), patch("ironwill.app.IronWillApp", side_effect=RuntimeError), self.assertRaises(RuntimeError):
            launch(self.directory)
        with DirectoryLock(self.directory):
            pass

    def test_demo_does_not_acquire_directory_lock_or_create_data(self):
        folder = self.directory / "not-created"
        with patch("ironwill.app.DirectoryLock") as lock, patch("ironwill.app.tk.Tk"), patch("ironwill.app.IronWillApp"):
            self.assertEqual(launch(folder, demo=True), 0)
            lock.assert_not_called()
        self.assertFalse(folder.exists())

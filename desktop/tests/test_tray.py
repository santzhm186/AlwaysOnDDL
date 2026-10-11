# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
import os
import queue
import threading
import unittest
from unittest.mock import patch

from ironwill.tray_windows import WindowsTray


class TrayTests(unittest.TestCase):
    def test_import_and_unsupported_start_create_no_icon(self):
        tray = WindowsTray(lambda _: None)
        self.assertIsNone(tray.thread)
        self.assertIsNone(tray.hwnd)
        with patch("ironwill.tray_windows.os.name", "unsupported"):
            self.assertFalse(tray.start())

    @unittest.skipUnless(os.name == "nt", "Windows notification area required")
    def test_native_icon_lifecycle_and_restore_callback(self):
        events = queue.Queue()
        tray = WindowsTray(events.put)
        self.assertTrue(tray.start())
        try:
            self.assertEqual(events.get(timeout=5), "ready")
            self.assertTrue(tray.ready)
            self.assertTrue(tray.hwnd)
            # Simulate Shell's NIN_SELECT callback; no clicks in other applications.
            tray._user32.PostMessageW(tray.hwnd, 0x8001, 0, 0x0400)
            self.assertEqual(events.get(timeout=3), "restore")
        finally:
            tray.stop()
            tray.thread.join(timeout=3)
        self.assertFalse(tray.thread.is_alive())
        self.assertFalse(tray.ready)
        self.assertIsNone(tray.hwnd)

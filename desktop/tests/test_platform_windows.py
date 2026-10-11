# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Platform fallback tests never call LockWorkStation."""
import unittest
from unittest.mock import patch

from ironwill import platform_windows


class PlatformTests(unittest.TestCase):
    @patch.object(platform_windows, "supported", return_value=False)
    def test_unsupported_sampling_fails_open(self, _supported):
        sample = platform_windows.sample_foreground()
        self.assertEqual(sample.status, "unsupported")
        self.assertIsNone(sample.idle_seconds)

    @patch.object(platform_windows, "supported", return_value=False)
    def test_unsupported_lock_reports_failure(self, _supported):
        accepted, reason = platform_windows.request_lock()
        self.assertFalse(accepted)
        self.assertTrue(reason)


if __name__ == "__main__":
    unittest.main()

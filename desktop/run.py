# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Desktop entry point. Opening the UI never enables monitoring by itself."""
from ironwill.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

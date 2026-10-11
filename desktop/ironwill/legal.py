# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Offline license information, shared by source runs and the Windows bundle."""
from pathlib import Path
import sys


SOURCE_URL = "https://github.com/santzhm186/AlwaysOnDDL"
NOTICE = ("IronWill / AlwaysOnDDL\nCopyright (C) 2026 IronWill contributors\n"
          "SPDX-License-Identifier: GPL-3.0-only\n\n"
          "本程序按 GNU GPL 第 3 版授权，可依据该许可使用、修改和再分发。\n"
          "本程序不提供任何担保，包括适销性或特定用途适用性担保。\n"
          "GPLv3 全文及第三方声明如下；第三方组件保留各自许可证。\n\n"
          "源码及构建说明：" + SOURCE_URL + "\n")


def resource_root():
    return Path(sys._MEIPASS) if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def legal_text():
    root = resource_root()
    paths = [root / "LICENSE", root / "THIRD_PARTY_NOTICES.md", *sorted((root / "licenses").glob("*"))]
    parts = [NOTICE]
    for path in paths:
        if path.is_file():
            parts.append("\n===== " + path.name + " =====\n" + path.read_text(encoding="utf-8"))
    return "\n".join(parts)

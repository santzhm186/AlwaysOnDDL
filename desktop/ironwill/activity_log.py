# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded local diagnostic journal. No I/O until explicitly constructed/used.

Two rotating JSONL files; no database, external logger, or network service.
Failures are reported to the UI and never change execution policy.
"""
from __future__ import annotations

from collections import deque
from datetime import datetime
import json
import os
from pathlib import Path
import re
import uuid


_SENSITIVE = {"authorization", "api_key", "apikey", "key", "token", "access_token",
              "password", "secret", "api-key", "x-api-key"}
_BEARER = re.compile(r"(?i)\bBearer\s+[^\s\"'<>;,}]+")
_ASSIGNMENT = re.compile(r'''(?i)(["']?(?:authorization|api[_-]?key|access_token|password|secret)["']?\s*[:=]\s*)(["'])(.*?)(\2)''')


def redact(value, secrets=(), depth=0):
    """Redact known credentials before truncation, including JSON-escaped copies."""
    if depth > 8:
        return "[层级过深，已省略]"
    if isinstance(value, dict):
        return {str(key)[:100]: ("[已屏蔽]" if str(key).casefold() in _SENSITIVE else
                               redact(item, secrets, depth + 1))
                for key, item in list(value.items())[:100]}
    if isinstance(value, (list, tuple)):
        return [redact(item, secrets, depth + 1) for item in value[:100]]
    if isinstance(value, str):
        for secret in sorted((item for item in secrets if item), key=len, reverse=True):
            value = value.replace(secret, "[已屏蔽]")
            value = value.replace(json.dumps(secret, ensure_ascii=False)[1:-1], "[已屏蔽]")
            value = value.replace(json.dumps(secret, ensure_ascii=True)[1:-1], "[已屏蔽]")
        value = _BEARER.sub("Bearer [已屏蔽]", value)
        value = _ASSIGNMENT.sub(lambda match: match[1] + match[2] + "[已屏蔽]" + match[4], value)
        value = "".join(char for char in value if char.isprintable() or char in "\n\t")
        return value if len(value) <= 12000 else value[:12000] + "\n[内容已截断]"
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return "[不支持的字段类型]"


class ActivityLog:
    def __init__(self, data_dir: Path | None, *, max_bytes=2 * 1024 * 1024, max_entries=1000):
        self.directory = Path(data_dir) / "logs" if data_dir is not None else None
        self.max_bytes = max(1024, int(max_bytes))
        self.entries = deque(maxlen=max_entries)
        self.warning = ""
        self.revision = 0
        self._secrets = set()
        if self.directory:
            self._load()

    def protect_secret(self, secret: str) -> None:
        if secret:
            self._secrets.add(secret)

    def _load(self):
        seen = set()
        for name in ("activity.previous.jsonl", "activity.jsonl"):
            path = self.directory / name
            if not path.exists():
                continue
            try:
                with path.open("rb") as stream:
                    size = path.stat().st_size
                    if size > self.max_bytes:
                        stream.seek(size - self.max_bytes)
                        stream.readline()  # Skip a potentially partial first record.
                    while line := stream.readline(65537):
                        try:
                            if len(line) > 65536:
                                while line and not line.endswith(b"\n"):
                                    line = stream.readline(65537)
                                raise ValueError("oversized")
                            value = json.loads(line.decode("utf-8"))
                            if (not isinstance(value, dict) or value.get("schema_version") != 1
                                    or not all(isinstance(value.get(key), str) for key in
                                               ("id", "timestamp", "category", "event", "summary"))
                                    or not isinstance(value.get("details"), dict)):
                                raise ValueError("invalid entry")
                            if value["id"] not in seen:
                                self.entries.append(redact(value))
                                seen.add(value["id"])
                        except (ValueError, UnicodeError, RecursionError):
                            self.warning = "部分旧日志损坏或过长，已跳过；可读记录仍然保留。"
            except OSError:
                self.warning = "旧日志暂时无法读取；本次记录仍可在页面查看。"

    def record(self, category: str, event: str, summary: str, details: dict | None = None):
        entry = redact({"schema_version": 1, "id": uuid.uuid4().hex,
                        "timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
                        "category": category, "event": event, "summary": summary,
                        "details": details or {}}, self._secrets)
        encoded = (json.dumps(entry, ensure_ascii=False, allow_nan=False) + "\n").encode("utf-8")
        if len(encoded) > min(65536, self.max_bytes):
            entry["details"] = {"truncated": True, "note": "记录超过大小上限，详细正文已省略。"}
            encoded = (json.dumps(entry, ensure_ascii=False) + "\n").encode("utf-8")
        self.entries.append(entry)
        self.revision += 1
        if self.directory:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                path = self.directory / "activity.jsonl"
                if path.exists() and path.stat().st_size + len(encoded) + 1 > self.max_bytes:
                    os.replace(path, self.directory / "activity.previous.jsonl")
                with path.open("ab+") as stream:
                    if stream.tell():
                        stream.seek(-1, os.SEEK_END)
                        if stream.read(1) != b"\n":
                            stream.write(b"\n")  # Preserve new records after an interrupted last write.
                    stream.write(encoded)
                self.warning = ""
            except OSError:
                self.warning = "日志未能保存到磁盘；当前记录仅保留在本次运行内存中。请检查目录权限或磁盘空间。"
        return entry

    def clear(self) -> bool:
        if self.directory:
            try:
                for name in ("activity.jsonl", "activity.previous.jsonl"):
                    (self.directory / name).unlink(missing_ok=True)
            except OSError:
                self.warning = "部分日志未能删除，请检查目录权限；页面没有宣称清理成功。"
                return False
        self.entries.clear()
        self.warning = ""
        self.revision += 1
        return True

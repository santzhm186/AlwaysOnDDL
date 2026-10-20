# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Bounded settings storage with read-only recovery and optimistic concurrency."""
import hashlib
import json
import os
from pathlib import Path
import tempfile

from .directory_lock import DirectoryBusyError, DirectoryLock
from .goals import validate_todos

MAX_SETTINGS_BYTES = 1024 * 1024
TEXT_FIELDS = {"goal", "reality", "consequence", "oath", "signature", "ai_endpoint", "ai_model", "ai_cache_mode"}
KNOWN_FIELDS = TEXT_FIELDS | {"todos", "floating_enabled", "floating_position"}


class SettingsError(ValueError):
    pass


class SettingsReadOnly(SettingsError):
    pass


class SettingsConflict(SettingsError):
    pass


def _fields(value):
    if not isinstance(value, dict):
        raise SettingsError("个人配置需要是 JSON 对象。")
    settings, errors = {}, []
    for key in sorted(TEXT_FIELDS):
        if key in value:
            if isinstance(value[key], str):
                settings[key] = value[key]
            else:
                errors.append(key)
    if "todos" in value:
        try:
            settings["todos"] = validate_todos(value["todos"])
        except ValueError:
            settings["todos"] = []
            errors.append("todos")
    if "floating_enabled" in value:
        if type(value["floating_enabled"]) is bool:
            settings["floating_enabled"] = value["floating_enabled"]
        else:
            errors.append("floating_enabled")
    if "floating_position" in value:
        position = value["floating_position"]
        if isinstance(position, list) and len(position) == 2 and all(type(item) is int and abs(item) <= 100000 for item in position):
            settings["floating_position"] = list(position)
        else:
            errors.append("floating_position")
    return settings, errors


def encode_settings(value):
    clean, errors = _fields(value)
    if errors or set(value) - KNOWN_FIELDS:
        raise SettingsError("个人配置包含格式无效的字段，未保存。")
    data = json.dumps(clean, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    if len(data) > MAX_SETTINGS_BYTES:
        raise SettingsError("个人配置超过 1 MiB，请缩短目标或其他长文本后再保存；原文件保持不变。")
    return data


def atomic_write_settings(path, value):
    data = encode_settings(value)  # Validate the exact bytes before touching the old file.
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class SettingsStore:
    def __init__(self, path):
        self.path = Path(path)
        self.read_only = False
        self.warning = ""
        self._digest = None
        self._loaded = False

    def _read(self):
        try:
            with self.path.open("rb") as stream:
                return stream.read(MAX_SETTINGS_BYTES + 1)
        except FileNotFoundError:
            return None

    def load(self):
        self.read_only, self.warning, self._digest = False, "", None
        self._loaded = True
        try:
            raw = self._read()
            if raw is None:
                return {}
            self._digest = hashlib.sha256(raw).digest()
            if len(raw) > MAX_SETTINGS_BYTES:
                raise SettingsError("oversized")
            settings, errors = _fields(json.loads(raw.decode("utf-8")))
            if errors:
                self.read_only = True
                self.warning = "部分个人配置损坏，已显示可读取内容并保护原文件为只读；请修复 ui_settings.json 后重新打开软件。"
            return settings
        except (OSError, ValueError, UnicodeError, RecursionError):
            self.read_only = True
            self.warning = "个人配置无法读取，原文件已保护为只读；请修复或恢复 ui_settings.json 后重新打开软件。"
            return {}

    def save(self, value, *, writer=atomic_write_settings):
        if self.read_only:
            raise SettingsReadOnly(self.warning)
        if not self._loaded:
            raise SettingsError("保存前需要先读取配置。")
        data = encode_settings(value)
        try:
            with DirectoryLock(self.path.parent, ".ironwill-settings.lock"):
                current = self._read()
                digest = hashlib.sha256(current).digest() if current is not None else None
                if digest != self._digest:
                    raise SettingsConflict("配置已被其他实例或外部编辑修改，本次未覆盖；请先保留未保存内容，再退出并重新打开软件。")
                writer(self.path, value)
                self._digest = hashlib.sha256(data).digest()
        except DirectoryBusyError as error:
            raise SettingsConflict("配置正在由另一实例保存，本次未覆盖；请稍后重试。") from error

# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Opt-in, current-user Windows DPAPI storage. No secret is read at import."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit, urlunsplit


class CredentialError(RuntimeError):
    """Messages deliberately exclude keys, provider responses and private paths."""


def endpoint_identity(endpoint: str) -> str:
    try:
        parts = urlsplit(endpoint.strip())
        local = parts.hostname in {"localhost", "127.0.0.1", "::1"}
        if (not parts.hostname or parts.username or parts.password or parts.query or parts.fragment
                or not (parts.scheme == "https" or (parts.scheme == "http" and local))):
            raise ValueError()
        # Validate malformed ports even though the original netloc is retained.
        _ = parts.port
        path = parts.path.rstrip("/")
        if not path.endswith("/chat/completions"):
            path += "/chat/completions"
        return urlunsplit((parts.scheme.lower(), parts.netloc.casefold(), path, "", ""))
    except (ValueError, AttributeError):
        raise CredentialError("服务地址无效，无法匹配保存的 Key。") from None


def validate_key(value: str) -> str:
    if not isinstance(value, str):
        raise CredentialError("Key 格式无效。")
    value = value.strip()
    if not value or len(value) > 8192 or any(not c.isprintable() or c.isspace() for c in value):
        raise CredentialError("Key 应为单行文本，不能包含空白、控制字符或超过长度限制。")
    return value


def environment_key(endpoint: str) -> tuple[str, str]:
    key = os.environ.get("IRONWILL_API_KEY", "").strip()
    if key:
        return validate_key(key), "IRONWILL_API_KEY"
    # Never send a legacy provider's key to an unrelated model service.
    parts = urlsplit(endpoint_identity(endpoint))
    if parts.scheme == "https" and parts.hostname == "api.deepseek.com" and parts.port in (None, 443):
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if key:
            return validate_key(key), "DEEPSEEK_API_KEY"
    return "", ""


def read_legacy_key(path: Path) -> str:
    try:
        with Path(path).open("r", encoding="utf-8-sig") as stream:
            value = stream.read(8193)
        if len(value) > 8192:
            raise CredentialError("旧密钥文件内容过长，未导入。")
        return validate_key(value)
    except (OSError, UnicodeError):
        raise CredentialError("旧密钥文件无法读取，未导入。") from None


class _Blob(ctypes.Structure):
    _fields_ = [("size", ctypes.c_uint32), ("data", ctypes.POINTER(ctypes.c_ubyte))]


def _crypt(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise CredentialError("本机记住 Key 功能需要 Windows；可继续使用临时 Key。")
    if not data or len(data) > 65536:
        raise CredentialError("保存的 Key 数据无效。")
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    function = crypt32.CryptUnprotectData if decrypt else crypt32.CryptProtectData
    function.argtypes = [ctypes.POINTER(_Blob), ctypes.c_void_p if decrypt else ctypes.c_wchar_p,
                         ctypes.POINTER(_Blob), ctypes.c_void_p, ctypes.c_void_p,
                         ctypes.c_uint32, ctypes.POINTER(_Blob)]
    function.restype = ctypes.c_int
    kernel32.LocalFree.argtypes = [ctypes.c_void_p]
    kernel32.LocalFree.restype = ctypes.c_void_p
    incoming = ctypes.create_string_buffer(data)
    purpose = ctypes.create_string_buffer(b"IronWill/AI-key/current-user/v1")
    source = _Blob(len(data), ctypes.cast(incoming, ctypes.POINTER(ctypes.c_ubyte)))
    entropy = _Blob(len(purpose.raw) - 1, ctypes.cast(purpose, ctypes.POINTER(ctypes.c_ubyte)))
    output = _Blob()
    # UI_FORBIDDEN; deliberately omit LOCAL_MACHINE so another account cannot reuse it.
    ok = function(ctypes.byref(source), None if decrypt else "IronWill AI Key",
                  ctypes.byref(entropy), None, None, 1, ctypes.byref(output))
    try:
        if not ok or not output.data or not 0 < output.size <= 65536:
            raise CredentialError("Windows 无法解密保存的 Key，请重新输入或删除本机副本。" if decrypt
                                  else "Windows 加密保存失败，本次没有记住 Key。")
        return ctypes.string_at(output.data, output.size)
    finally:
        if output.data:
            ctypes.memset(output.data, 0, output.size)
            kernel32.LocalFree(ctypes.cast(output.data, ctypes.c_void_p))


class KeyStore:
    def __init__(self, data_dir: Path):
        self.path = Path(data_dir) / "secrets" / "ai-key.dpapi"

    def exists(self) -> bool:
        return self.path.is_file()

    def save(self, endpoint: str, key: str) -> None:
        payload = json.dumps({"schema_version": 1, "endpoint": endpoint_identity(endpoint),
                              "key": validate_key(key)}, ensure_ascii=False).encode("utf-8")
        # Encrypt before creating any temporary file. No plaintext fallback exists.
        encrypted = _crypt(payload)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix="ai-key.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def load(self, endpoint: str) -> str | None:
        if not self.exists():
            return None
        with self.path.open("rb") as stream:
            encrypted = stream.read(65537)
        plain = _crypt(encrypted, decrypt=True)
        try:
            data = json.loads(plain.decode("utf-8"))
            if (not isinstance(data, dict) or set(data) != {"schema_version", "endpoint", "key"}
                    or data["schema_version"] != 1):
                raise ValueError()
            if data["endpoint"] != endpoint_identity(endpoint):
                return None
            return validate_key(data["key"])
        except (ValueError, TypeError, UnicodeError):
            raise CredentialError("保存的 Key 数据无效，请重新输入或删除本机副本。") from None

    def delete(self) -> None:
        self.path.unlink(missing_ok=True)

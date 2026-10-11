"""Windows-only observation and explicit locking, with no third-party packages.

Importing this module never locks the workstation. Unsupported platforms return
an unavailable observation. A secure desktop is treated as unobservable.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import PureWindowsPath

from .core import Observation


def supported() -> bool:
    return os.name == "nt"


_API = None


def _api():
    global _API
    if _API is not None:
        return _API
    if not supported():
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND
    user32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    user32.GetWindowTextLengthW.restype = ctypes.c_int
    user32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    user32.GetWindowTextW.restype = ctypes.c_int
    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.GetUserObjectInformationW.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID,
                                                wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
    user32.GetUserObjectInformationW.restype = wintypes.BOOL
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.CloseDesktop.restype = wintypes.BOOL
    user32.LockWorkStation.argtypes = []
    user32.LockWorkStation.restype = wintypes.BOOL
    user32.GetLastInputInfo.argtypes = [wintypes.LPVOID]
    user32.GetLastInputInfo.restype = wintypes.BOOL
    kernel32.GetTickCount.argtypes = []
    kernel32.GetTickCount.restype = wintypes.DWORD
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                                  wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    _API = (user32, kernel32)
    return _API


def _desktop_status(user32) -> str:
    handle = user32.OpenInputDesktop(0, False, 0x0001)  # DESKTOP_READOBJECTS
    if not handle:
        return "unavailable"
    try:
        needed = wintypes.DWORD()
        user32.GetUserObjectInformationW(handle, 2, None, 0, ctypes.byref(needed))
        if not needed.value or needed.value > 65536:
            return "unavailable"
        buffer = ctypes.create_unicode_buffer(needed.value // ctypes.sizeof(ctypes.c_wchar) + 1)
        if not user32.GetUserObjectInformationW(handle, 2, buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
            return "unavailable"
        return "ok" if buffer.value.casefold() == "default" else "locked"
    finally:
        user32.CloseDesktop(handle)


def sample_foreground() -> Observation:
    if not supported():
        return Observation(status="unsupported", idle_seconds=None)
    try:
        user32, kernel32 = _api()
        desktop = _desktop_status(user32)
        if desktop != "ok":
            return Observation(status=desktop, locked=(desktop == "locked"), idle_seconds=None)

        class LastInputInfo(ctypes.Structure):
            _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

        last_input = LastInputInfo()
        last_input.cbSize = ctypes.sizeof(last_input)
        if not user32.GetLastInputInfo(ctypes.byref(last_input)):
            return Observation(status="input_unavailable", idle_seconds=None)
        idle = ((kernel32.GetTickCount() - last_input.dwTime) & 0xFFFFFFFF) / 1000.0
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return Observation(status="no_foreground", idle_seconds=idle)
        size = min(max(user32.GetWindowTextLengthW(hwnd), 0), 16384) + 1
        title = ctypes.create_unicode_buffer(size)
        user32.GetWindowTextW(hwnd, title, size)
        pid = wintypes.DWORD()
        if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)) or not pid.value:
            return Observation(status="process_unavailable", idle_seconds=idle)
        handle = kernel32.OpenProcess(0x1000, False, pid.value)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return Observation(status="process_unavailable", idle_seconds=idle)
        try:
            path = ctypes.create_unicode_buffer(32768)
            path_size = wintypes.DWORD(len(path))
            if not kernel32.QueryFullProcessImageNameW(handle, 0, path, ctypes.byref(path_size)):
                return Observation(status="process_unavailable", idle_seconds=idle)
            process = PureWindowsPath(path.value).name.casefold()
        finally:
            kernel32.CloseHandle(handle)
        # A foreground switch during collection invalidates this sample.
        if hwnd != user32.GetForegroundWindow():
            return Observation(status="foreground_changed", idle_seconds=idle)
        return Observation(process=process, title=title.value, idle_seconds=idle)
    except (OSError, ValueError, AttributeError):
        return Observation(status="unavailable", idle_seconds=None)


def request_lock() -> tuple[bool, str]:
    """Request a system lock only when the UI handles an explicit user action.

    A successful API return confirms acceptance of the request, not a timed,
    unbreakable lock. The application never attempts to prevent unlocking.
    """
    if not supported():
        return False, "当前平台不支持 Windows 锁屏。"
    try:
        user32, _ = _api()
        if user32.LockWorkStation():
            return True, "Windows 已接受锁屏请求。"
        return False, "Windows 未接受锁屏请求（错误 %d）。" % ctypes.get_last_error()
    except (OSError, AttributeError) as exc:
        return False, "无法请求 Windows 锁屏：" + type(exc).__name__

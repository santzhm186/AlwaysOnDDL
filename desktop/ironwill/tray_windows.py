# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Explicit Windows notification-area icon using only ctypes and system DLLs.

The native message loop never calls Tk. Events are delivered to the UI queue.
Importing this module creates neither a window nor an icon.
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes as w
import os
import threading
import uuid


class WindowsTray:
    def __init__(self, on_event):
        self.on_event = on_event
        self.ready = False
        self.hwnd = None
        self.thread = None
        self._stopping = threading.Event()
        self._user32 = None

    def start(self) -> bool:
        if os.name != "nt":
            return False
        if self.thread and self.thread.is_alive():
            return True
        self.thread = threading.Thread(target=self._run_guarded, name="ironwill-tray", daemon=True)
        self.thread.start()
        return True

    def stop(self) -> None:
        self._stopping.set()
        if self.hwnd and self._user32:
            self._user32.PostMessageW(self.hwnd, 0x0010, 0, 0)  # WM_CLOSE
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout=1)

    def _emit(self, event):
        if not self._stopping.is_set():
            self.on_event(event)

    def _run_guarded(self):
        try:
            self._run()
        except Exception:
            self.ready = False
            self._emit("error")

    def _run(self):
        user = ctypes.WinDLL("user32", use_last_error=True)
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self._user32 = user
        proc_type = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, w.HWND, w.UINT,
                                      ctypes.c_size_t, ctypes.c_ssize_t)

        class WindowClass(ctypes.Structure):
            _fields_ = [("style", w.UINT), ("lpfnWndProc", proc_type),
                        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                        ("hInstance", w.HINSTANCE), ("hIcon", w.HICON),
                        ("hCursor", w.HANDLE), ("hbrBackground", w.HANDLE),
                        ("lpszMenuName", w.LPCWSTR), ("lpszClassName", w.LPCWSTR)]

        class Guid(ctypes.Structure):
            _fields_ = [("Data1", w.DWORD), ("Data2", w.WORD), ("Data3", w.WORD),
                        ("Data4", ctypes.c_ubyte * 8)]

        class NotifyIcon(ctypes.Structure):
            _fields_ = [("cbSize", w.DWORD), ("hWnd", w.HWND), ("uID", w.UINT),
                        ("uFlags", w.UINT), ("uCallbackMessage", w.UINT),
                        ("hIcon", w.HICON), ("szTip", w.WCHAR * 128),
                        ("dwState", w.DWORD), ("dwStateMask", w.DWORD),
                        ("szInfo", w.WCHAR * 256), ("uVersion", w.UINT),
                        ("szInfoTitle", w.WCHAR * 64), ("dwInfoFlags", w.DWORD),
                        ("guidItem", Guid), ("hBalloonIcon", w.HICON)]

        signatures = [
            (user, "RegisterClassW", [ctypes.POINTER(WindowClass)], w.WORD),
            (user, "UnregisterClassW", [w.LPCWSTR, w.HINSTANCE], w.BOOL),
            (user, "CreateWindowExW", [w.DWORD, w.LPCWSTR, w.LPCWSTR, w.DWORD,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      w.HWND, w.HMENU, w.HINSTANCE, w.LPVOID], w.HWND),
            (user, "DefWindowProcW", [w.HWND, w.UINT, ctypes.c_size_t, ctypes.c_ssize_t], ctypes.c_ssize_t),
            (user, "DestroyWindow", [w.HWND], w.BOOL),
            (user, "PostMessageW", [w.HWND, w.UINT, ctypes.c_size_t, ctypes.c_ssize_t], w.BOOL),
            (user, "GetMessageW", [ctypes.POINTER(w.MSG), w.HWND, w.UINT, w.UINT], ctypes.c_int),
            (user, "TranslateMessage", [ctypes.POINTER(w.MSG)], w.BOOL),
            (user, "DispatchMessageW", [ctypes.POINTER(w.MSG)], ctypes.c_ssize_t),
            (user, "PostQuitMessage", [ctypes.c_int], None),
            (user, "LoadIconW", [w.HINSTANCE, w.LPCWSTR], w.HICON),
            (user, "RegisterWindowMessageW", [w.LPCWSTR], w.UINT),
            (user, "CreatePopupMenu", [], w.HMENU),
            (user, "AppendMenuW", [w.HMENU, w.UINT, ctypes.c_size_t, w.LPCWSTR], w.BOOL),
            (user, "DestroyMenu", [w.HMENU], w.BOOL),
            (user, "GetCursorPos", [ctypes.POINTER(w.POINT)], w.BOOL),
            (user, "SetForegroundWindow", [w.HWND], w.BOOL),
            (user, "TrackPopupMenu", [w.HMENU, w.UINT, ctypes.c_int, ctypes.c_int,
                                      ctypes.c_int, w.HWND, w.LPVOID], w.UINT),
            (shell, "Shell_NotifyIconW", [w.DWORD, ctypes.POINTER(NotifyIcon)], w.BOOL),
            (kernel, "GetModuleHandleW", [w.LPCWSTR], w.HINSTANCE),
        ]
        for library, name, arguments, result in signatures:
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

        instance = kernel.GetModuleHandleW(None)
        class_name = "IronWillTray_" + uuid.uuid4().hex
        callback_message = 0x8001  # WM_APP + 1
        taskbar_created = user.RegisterWindowMessageW("TaskbarCreated")
        notification = NotifyIcon()
        notification.cbSize = ctypes.sizeof(notification)
        notification.uID = 1
        notification.uFlags = 1 | 2 | 4 | 0x80  # MESSAGE, ICON, TIP, SHOWTIP
        notification.uCallbackMessage = callback_message
        notification.hIcon = user.LoadIconW(None, ctypes.cast(ctypes.c_void_p(32512), w.LPCWSTR))
        notification.szTip = "IronWill · 点击打开，右键退出"
        registered = False

        def add_icon():
            if not shell.Shell_NotifyIconW(0, ctypes.byref(notification)):
                return False
            notification.uVersion = 4
            shell.Shell_NotifyIconW(4, ctypes.byref(notification))
            return True

        def menu(hwnd):
            handle = user.CreatePopupMenu()
            if not handle:
                return
            try:
                user.AppendMenuW(handle, 0, 1, "打开 IronWill")
                user.AppendMenuW(handle, 0x800, 0, None)
                user.AppendMenuW(handle, 0, 2, "停止并退出")
                point = w.POINT()
                user.GetCursorPos(ctypes.byref(point))
                user.SetForegroundWindow(hwnd)
                command = user.TrackPopupMenu(handle, 0x100 | 0x80 | 2,
                                             point.x, point.y, 0, hwnd, None)
                if command:
                    self._emit("restore" if command == 1 else "exit")
                user.PostMessageW(hwnd, 0, 0, 0)
            finally:
                user.DestroyMenu(handle)

        @proc_type
        def window_proc(hwnd, message, wp, lp):
            try:
                if message == callback_message:
                    action = lp & 0xFFFF
                    if action in (0x0203, 0x0400, 0x0401):  # double click / NIN_SELECT / KEYSELECT
                        self._emit("restore")
                    elif action in (0x007B, 0x0205):  # context menu / right button up
                        menu(hwnd)
                    return 0
                if taskbar_created and message == taskbar_created:
                    if not add_icon():
                        self._emit("error")
                        user.DestroyWindow(hwnd)
                    return 0
                if message == 0x0010:  # WM_CLOSE
                    user.DestroyWindow(hwnd)
                    return 0
                if message == 0x0002:  # WM_DESTROY
                    user.PostQuitMessage(0)
                    return 0
            except Exception:
                self._emit("error")
            return user.DefWindowProcW(hwnd, message, wp, lp)

        window_class = WindowClass()
        window_class.lpfnWndProc, window_class.hInstance = window_proc, instance
        window_class.lpszClassName = class_name
        try:
            if not user.RegisterClassW(ctypes.byref(window_class)):
                raise OSError("RegisterClass failed")
            registered = True
            self.hwnd = user.CreateWindowExW(0, class_name, "IronWill tray", 0,
                                             0, 0, 0, 0, None, None, instance, None)
            if not self.hwnd:
                raise OSError("CreateWindow failed")
            notification.hWnd = self.hwnd
            if self._stopping.is_set():
                return
            if not add_icon():
                raise OSError("Shell_NotifyIcon failed")
            self.ready = True
            self._emit("ready")
            message = w.MSG()
            while not self._stopping.is_set():
                result = user.GetMessageW(ctypes.byref(message), None, 0, 0)
                if result == 0:
                    break
                if result == -1:
                    raise OSError("GetMessage failed")
                user.TranslateMessage(ctypes.byref(message))
                user.DispatchMessageW(ctypes.byref(message))
        except Exception:
            self._emit("error")
        finally:
            self.ready = False
            if notification.hWnd:
                shell.Shell_NotifyIconW(2, ctypes.byref(notification))
            if self.hwnd:
                user.DestroyWindow(self.hwnd)
                self.hwnd = None
            if registered:
                user.UnregisterClassW(class_name, instance)

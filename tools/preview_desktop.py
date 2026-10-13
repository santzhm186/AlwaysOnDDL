# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Render only this app's synthetic demo window to PNG, using Windows APIs.

No third-party image library, real observation, model service, or user settings.
Run from desktop: python ../tools/preview_desktop.py
"""
import ctypes
from ctypes import wintypes as w
import json
from pathlib import Path
import struct
import sys
import tempfile
import tkinter as tk
import time
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "desktop"))
from ironwill.app import IronWillApp


def capture(root, path):
    user, gdi = ctypes.WinDLL("user32"), ctypes.WinDLL("gdi32")
    signatures = [
        (user, "GetAncestor", [w.HWND, w.UINT], w.HWND),
        (user, "GetWindowRect", [w.HWND, ctypes.POINTER(w.RECT)], w.BOOL),
        (user, "GetWindowDC", [w.HWND], w.HDC),
        (user, "ReleaseDC", [w.HWND, w.HDC], ctypes.c_int),
        (user, "PrintWindow", [w.HWND, w.HDC, w.UINT], w.BOOL),
        (gdi, "CreateCompatibleDC", [w.HDC], w.HDC),
        (gdi, "CreateCompatibleBitmap", [w.HDC, ctypes.c_int, ctypes.c_int], w.HBITMAP),
        (gdi, "SelectObject", [w.HDC, w.HANDLE], w.HANDLE),
        (gdi, "GetDIBits", [w.HDC, w.HBITMAP, w.UINT, w.UINT, w.LPVOID, w.LPVOID, w.UINT], ctypes.c_int),
        (gdi, "DeleteObject", [w.HANDLE], w.BOOL),
        (gdi, "DeleteDC", [w.HDC], w.BOOL),
    ]
    for library, name, args, result in signatures:
        function = getattr(library, name)
        function.argtypes, function.restype = args, result
    hwnd = user.GetAncestor(root.winfo_id(), 2)
    rectangle = w.RECT()
    user.GetWindowRect(hwnd, ctypes.byref(rectangle))
    width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
    source = user.GetWindowDC(hwnd)
    memory = gdi.CreateCompatibleDC(source)
    bitmap = gdi.CreateCompatibleBitmap(source, width, height)
    previous = gdi.SelectObject(memory, bitmap)
    try:
        if not user.PrintWindow(hwnd, memory, 2):
            raise RuntimeError("PrintWindow failed")
        gdi.SelectObject(memory, previous)
        info = struct.pack("<IiiHHIIiiII", 40, width, -height, 1, 32, 0, width * height * 4, 0, 0, 0, 0)
        buffer = ctypes.create_string_buffer(width * height * 4)
        header = ctypes.create_string_buffer(info + b"\0" * 16)
        if not gdi.GetDIBits(memory, bitmap, 0, height, buffer, header, 0):
            raise RuntimeError("GetDIBits failed")
        pixels = buffer.raw
        rows = bytearray()
        for y in range(height):
            rows.append(0)
            row = pixels[y * width * 4:(y + 1) * width * 4]
            rgb = bytearray(width * 3)
            rgb[0::3], rgb[1::3], rgb[2::3] = row[2::4], row[1::4], row[0::4]
            rows.extend(rgb)
        def chunk(kind, content):
            return struct.pack(">I", len(content)) + kind + content + struct.pack(">I", zlib.crc32(kind + content))
        png = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        path.write_bytes(png + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b""))
    finally:
        gdi.SelectObject(memory, previous)
        gdi.DeleteObject(bitmap)
        gdi.DeleteDC(memory)
        user.ReleaseDC(hwnd, source)


def main():
    destination = Path(__file__).resolve().parents[1] / "dist" / "previews"
    destination.mkdir(parents=True, exist_ok=True)
    root = tk.Tk()
    def settle():
        root.update()
        time.sleep(0.08)  # Let Windows paint the synthetic window before PrintWindow.
        root.update()
    with tempfile.TemporaryDirectory(prefix="ironwill-preview-") as directory:
        try:
            app = IronWillApp(root, Path(directory), demo=True)
            app._log("判断", "observation", "本地规则判断为主线：不请求 AI", {"process": "editor.exe", "title": "示例项目", "local_classification": "ON_TASK"})
            app._log("AI通信", "ai_request", "发起 AI 标题审核请求", {"request_id": "synthetic-preview", "endpoint": "https://example.invalid/v1/chat/completions", "request_body": {"model": "示例模型", "max_tokens": 1, "temperature": 0, "messages": [{"role": "user", "content": "只用于展示的虚拟目标与标题"}]}})
            app._log("AI通信", "ai_response", "AI 返回有效判断", {"request_id": "synthetic-preview", "http_status": 200, "duration_ms": 245, "result": {"verdict": "neutral", "code": "2", "reason": "2 · 辅助/中立", "reason_source": "local_code_mapping"}, "response_body": json.dumps({"choices": [{"message": {"content": "2"}}]}, ensure_ascii=False)})
            app._refresh_logs()
            for size in ("1030x820", "820x650"):
                root.geometry(size)
                for index, page in enumerate(("mission", "rules", "connections", "logs")):
                    app.notebook.select(index)
                    settle()
                    path = destination / f"desktop-{page}-{size}.png"
                    capture(root, path)
                    print(path)
                    if index < 3:
                        scroll = root.nametowidget(app.notebook.tabs()[index])
                        scroll.canvas.yview_moveto(1)
                        settle()
                        capture(root, destination / f"desktop-{page}-bottom-{size}.png")
                        scroll.canvas.yview_moveto(0)
            app.preview_reminder()
            settle()
            capture(app.dialog, destination / "desktop-reminder.png")
            app.dialog.geometry("480x370")
            settle()
            capture(app.dialog, destination / "desktop-reminder-small.png")
        finally:
            root.destroy()


if __name__ == "__main__":
    main()

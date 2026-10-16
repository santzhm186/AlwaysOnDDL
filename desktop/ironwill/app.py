# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Chinese Tk desktop UI. OS and network actions always require explicit user action."""

from __future__ import annotations

import json
import math
import os
import queue
import tempfile
import threading
import time
import uuid
from urllib.parse import urlsplit
from dataclasses import replace
from pathlib import Path
from typing import Any

import tkinter as tk
from tkinter import font as tkfont
from tkinter import filedialog, messagebox, ttk

from .core import Engine, Observation, Policy, classify, load_policy, load_snapshot, save_policy, save_snapshot
from . import platform_windows
from . import __version__
from .credentials import CredentialError, KeyStore, endpoint_identity, environment_key, read_legacy_key, validate_key
from .activity_log import ActivityLog
from .review_memory import ReviewMemory, CACHE_MODES, review_identity


BG = "#131716"
PANEL = "#1c2320"
FIELD = "#141a17"
FG = "#edf0e7"
MUTED = "#a8b4aa"
ACCENT = "#d7f06c"
WARN = "#edbc7a"
LINE = "#354139"


def _write_json(path: Path, value: dict[str, Any]) -> None:
    """Unique same-directory temporary file; never write secrets here."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


class ScrollFrame(ttk.Frame):
    def __init__(self, master: tk.Misc):
        super().__init__(master)
        self.canvas = tk.Canvas(self, bg=BG, highlightthickness=0)
        scrollbar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.body = ttk.Frame(self.canvas, padding=(4, 22, 12, 24))
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width))
        # The page also scrolls over labels and cards; text editors keep their own wheel.
        self.bind("<MouseWheel>", self._wheel)
        self.canvas.bind("<MouseWheel>", self._wheel)

    def connect_wheel(self) -> None:
        def visit(widget):
            if not isinstance(widget, (tk.Text, ttk.Entry, ttk.Combobox)):
                widget.bind("<MouseWheel>", self._wheel, add="+")
            for child in widget.winfo_children():
                visit(child)
        visit(self.body)

    def _wheel(self, event: tk.Event) -> None:
        self.canvas.yview_scroll(int(-event.delta / 120), "units")


class IronWillApp:
    def __init__(self, root: tk.Tk, data_dir: Path, demo: bool = False):
        self.root, self.data_dir, self.demo = root, Path(data_dir), demo
        self.policy_path = self.data_dir / "policy.json"
        self.settings_path = self.data_dir / "ui_settings.json"
        self.snapshot_path = self.data_dir / "session.json"
        self.key_store = KeyStore(self.data_dir)
        self.runtime_key: tuple[str, str] | None = None
        self.journal = ActivityLog(None if demo else self.data_dir)
        self.review_memory = ReviewMemory(None if demo else self.data_dir)
        self.ai_api_count = 0
        self.ai_cache_hits = 0
        self.ai_candidate = None
        self.ai_failures = {}
        self.ai_wait_reason = ""
        self.active_cache_mode = "本工作周期"
        self._log_epoch = 0
        self._log_render_revision = -1
        self._last_decision = None
        self._last_decision_time = 0.0
        self.tray = None
        self.tray_starting = False
        self.events: queue.SimpleQueue = queue.SimpleQueue()
        self.closing = False
        self.timer_id: str | None = None
        self.last_tick = time.monotonic()
        self.last_saved = 0.0
        self.dialog: tk.Toplevel | None = None
        self.dialog_stage = 0
        self.dialog_preview = False
        self.dialog_advisory = False
        self.ai = None
        self.ai_generation = 0
        self.ai_pending = False
        self.ai_last_request = 0.0
        self.semantic_result: tuple[str, str, str, float] | None = None
        self.last_observation: Observation | None = None
        self.last_ai_details: dict[str, Any] | None = None
        self.ai_completed_count = 0
        self.connection_test_id: str | None = None
        self.relay = None
        self.relay_starting = False
        self.relay_start_lock = threading.Lock()
        self.pair_secret_visible = False
        self.relay_info: dict[str, Any] = {}
        self.relay_reminder: dict[str, Any] | None = None
        self.pending_reminder: dict[str, Any] | None = None
        self.persistence_warning = ""
        self.load_warning = ""
        self.policy = Policy()
        self.settings = self._load_settings()
        if not self.demo and self.policy_path.exists():
            try:
                self.policy = load_policy(self.policy_path)
            except Exception:
                self.load_warning = "规则文件无法读取，已显示安全默认值；原文件没有改动。请检查后再保存。"
        snapshot = None
        if not self.demo and self.snapshot_path.exists():
            try:
                snapshot = load_snapshot(self.snapshot_path)
            except Exception:
                self.load_warning = "上次运行记录无法读取，本次不会自动恢复；原文件没有改动。"
        self.engine = Engine(self.policy, snapshot=snapshot)
        self.text_fields: dict[str, tk.Text] = {}
        self.rule_fields: dict[str, tk.Text] = {}
        self.numeric_fields: dict[str, tk.StringVar] = {}
        self.copy_buttons: dict[str, ttk.Button] = {}
        self._style()
        self._build()
        self.root.protocol("WM_DELETE_WINDOW", self.close)
        self.root.after(100, self._poll)
        if self.load_warning:
            self.status_text.set(self.load_warning)
        elif self.engine.state.get("status") == "paused":
            self.status_text.set("已恢复上次周期，保持暂停。点击“继续”才开始采样；离线时间不会补计。")

    def _load_settings(self) -> dict[str, Any]:
        if self.demo:
            return {
                "goal": "完成首版功能中已经决定的下一步",
                "reality": "计划已经确定，现在需要把时间用于执行。",
                "consequence": "拖延会让这一轮目标继续停留在计划里。",
                "oath": "我会对自己作出的决定负责。",
                "signature": "",
            }
        if not self.settings_path.exists():
            return {}
        try:
            value = json.loads(self.settings_path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("not an object")
            # Ignore unexpected fields, especially any accidentally retained secrets.
            allowed = {"goal", "reality", "consequence", "oath", "signature", "ai_endpoint", "ai_model", "ai_cache_mode"}
            return {key: item for key, item in value.items() if key in allowed and isinstance(item, str)}
        except Exception:
            self.load_warning = "个人设置无法读取，原文件没有改动。请检查数据目录后再保存。"
            return {}

    def _style(self) -> None:
        self.root.title("IronWill · 桌面 " + __version__ + (" · 安全体验" if self.demo else ""))
        self.root.geometry("1030x820")
        self.root.minsize(820, 650)
        self.root.configure(bg=BG)
        # An option-database *Font overrides individual ttk typography on Windows.
        for name in ("TkDefaultFont", "TkTextFont", "TkMenuFont", "TkHeadingFont"):
            tkfont.nametofont(name).configure(family="Microsoft YaHei UI", size=10)
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Microsoft YaHei UI", 10),
                        bordercolor=LINE, lightcolor=LINE, darkcolor=LINE, focuscolor=ACCENT)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Accent.TLabel", foreground=ACCENT)
        style.configure("Warning.TLabel", foreground=WARN)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 24, "bold"))
        style.configure("Page.TLabel", font=("Microsoft YaHei UI", 19, "bold"))
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 11, "bold"))
        style.configure("Eyebrow.TLabel", foreground=ACCENT, font=("Segoe UI", 9, "bold"))
        style.configure("Metric.TLabel", foreground=FG, font=("Microsoft YaHei UI", 16, "bold"))
        style.configure("Badge.TLabel", background="#2b3523", foreground=ACCENT, padding=(12, 7))
        style.configure("TButton", background="#29332d", foreground=FG, padding=(15, 10), borderwidth=0, focusthickness=1)
        style.map("TButton", background=[("disabled", "#202722"), ("pressed", "#3d4b40"), ("active", "#354239")],
                  foreground=[("disabled", "#829087")])
        style.configure("Primary.TButton", background=ACCENT, foreground=BG, font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("disabled", "#323d27"), ("pressed", "#bbd45a"), ("active", "#e4fa92")],
                  foreground=[("disabled", "#9aa58f"), ("!disabled", BG)])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=(10, 9), borderwidth=1,
                        lightcolor=LINE, darkcolor=LINE, bordercolor=LINE)
        style.map("TEntry", fieldbackground=[("readonly", FIELD)], bordercolor=[("focus", ACCENT)])
        style.configure("TCombobox", fieldbackground=FIELD, background=PANEL, foreground=FG, arrowcolor=MUTED, padding=7,
                        lightcolor=LINE, darkcolor=LINE, bordercolor=LINE)
        style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)],
                  selectbackground=[("readonly", FIELD)], selectforeground=[("readonly", FG)])
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TNotebook", background=BG, borderwidth=0, bordercolor=BG, lightcolor=BG, darkcolor=BG, tabmargins=(0, 0, 0, 0))
        style.configure("TNotebook.Tab", background=BG, foreground=MUTED, padding=(25, 12), borderwidth=0,
                        lightcolor=BG, darkcolor=BG)
        style.map("TNotebook.Tab", background=[("selected", PANEL), ("active", "#242e27")],
                  foreground=[("selected", ACCENT), ("active", FG)], lightcolor=[("selected", PANEL)],
                  padding=[("selected", (25, 12)), ("!selected", (25, 12))], expand=[("selected", (0, 0, 0, 0))])
        style.configure("TLabelframe", background=BG, bordercolor=LINE)
        style.configure("TLabelframe.Label", background=BG, foreground=FG)
        style.configure("Treeview", background=FIELD, fieldbackground=FIELD, foreground=FG, rowheight=36, borderwidth=0)
        style.configure("Treeview.Heading", background="#29332d", foreground=MUTED, padding=(10, 9), relief="flat")
        style.map("Treeview.Heading", background=[("active", "#354239")])
        style.map("Treeview", background=[("selected", "#3c4d2e")], foreground=[("selected", FG)])
        style.configure("Vertical.TScrollbar", background="#3c4a40", troughcolor=BG, arrowcolor=MUTED,
                        borderwidth=0, arrowsize=12)
        style.configure("TSeparator", background=LINE)
        for name in ("TLabel", "Muted.TLabel", "Accent.TLabel", "Warning.TLabel", "Heading.TLabel", "Eyebrow.TLabel", "Metric.TLabel"):
            style.configure("Card." + name, background=PANEL)
        style.configure("Card.TCheckbutton", background=PANEL)
        style.map("Card.TCheckbutton", background=[("active", PANEL)])

    def _card(self, parent: tk.Misc, padding=18) -> ttk.Frame:
        return ttk.Frame(parent, style="Card.TFrame", padding=padding)

    def _copy(self, parent: tk.Misc, text: str = "", **kwargs) -> ttk.Label:
        label = ttk.Label(parent, text=text, style=kwargs.pop("style", "Muted.TLabel"), wraplength=400, **kwargs)
        parent.bind("<Configure>", lambda event: label.configure(wraplength=max(160, event.width - 40)), add="+")
        return label

    def _harmonize_cards(self, widget: tk.Misc, in_card=False) -> None:
        if isinstance(widget, ttk.Frame) and widget.cget("style") == "Card.TFrame":
            in_card = True
        if in_card and isinstance(widget, (ttk.Label, ttk.Frame, ttk.Checkbutton)):
            current = str(widget.cget("style")) or widget.winfo_class()
            if not current.startswith("Card."):
                widget.configure(style="Card." + current)
        for child in widget.winfo_children():
            self._harmonize_cards(child, in_card)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        mark = tk.Canvas(heading, width=42, height=42, background=ACCENT, highlightthickness=0)
        mark.create_text(21, 21, text="IW", font=("Segoe UI", 16, "bold"), fill=BG)
        mark.pack(side="left", padx=(0, 12))
        brand = ttk.Frame(heading)
        brand.pack(side="left")
        ttk.Label(brand, text="IronWill", style="Title.TLabel").pack(anchor="w")
        self.state_text = tk.StringVar(value="未开始")
        ttk.Label(heading, textvariable=self.state_text, style="Badge.TLabel").pack(side="right")
        self.tray_button = ttk.Button(heading, text="最小化到托盘", command=self.minimize_to_tray)
        self.tray_button.pack(side="right", padx=12)
        self.capability_text = tk.StringVar()
        self._copy(outer, textvariable=self.capability_text).pack(fill="x", pady=(10, 14))
        if self.demo:
            ttk.Label(outer, text="安全体验：不读取窗口、不联网、不锁屏、不写入个人配置。", style="Warning.TLabel").pack(fill="x", pady=(0, 10))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        notebook.enable_traversal()
        self.notebook = notebook
        mission, rules, connections = ScrollFrame(notebook), ScrollFrame(notebook), ScrollFrame(notebook)
        notebook.add(mission, text="当前周期")
        notebook.add(rules, text="判定规则")
        notebook.add(connections, text="手机与 AI")
        logs = ttk.Frame(notebook, padding=(4, 22, 12, 4))
        notebook.add(logs, text="判断日志")
        self._build_mission(mission.body)
        self._build_rules(rules.body)
        self._build_connections(connections.body)
        self._build_logs(logs)
        self._harmonize_cards(outer)
        for page in (mission, rules, connections):
            page.connect_wheel()
        footer = ttk.Frame(outer)
        footer.pack(side="bottom", fill="x", pady=(12, 0), before=notebook)
        self.status_text = tk.StringVar(value="尚未开始。保存目标后，由你决定何时开始这个周期。")
        ttk.Button(footer, text="关于与许可", command=self.show_licenses).pack(side="right", padx=(10, 0))
        status_label = ttk.Label(footer, textvariable=self.status_text, style="Muted.TLabel", wraplength=580)
        status_label.pack(side="left", fill="x", expand=True)
        status_label.bind("<Configure>", lambda event: status_label.configure(wraplength=max(160, event.width)))
        self._refresh_status()

    def show_licenses(self) -> None:
        from .legal import legal_text
        window = tk.Toplevel(self.root)
        window.title("IronWill · 关于与许可")
        window.geometry("780x600")
        body = ttk.Frame(window, padding=15)
        body.pack(fill="both", expand=True)
        text = self._text(body, 20)
        scroll = ttk.Scrollbar(body, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        text.insert("1.0", legal_text())
        text.configure(state="disabled")

    def _build_logs(self, parent: ttk.Frame) -> None:
        ttk.Label(parent, text="每一次判断，都有迹可循。", style="Page.TLabel").pack(anchor="w")
        self._copy(parent, "AI 请求与缓存分别标明。隐私标题与 Key 不入日志；选择记录查看详情。").pack(fill="x", pady=(5, 14))
        toolbar = ttk.Frame(parent)
        toolbar.pack(fill="x", pady=(0, 10))
        self.log_filter = tk.StringVar(value="全部")
        ttk.Combobox(toolbar, textvariable=self.log_filter, state="readonly", width=7,
                     values=("全部", "判断", "AI通信", "程序")).pack(side="left")
        ttk.Label(toolbar, text="搜索", style="Muted.TLabel").pack(side="left", padx=(12, 8))
        self.log_search = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.log_search, width=12).pack(side="left", fill="x", expand=True)
        self.log_follow = tk.BooleanVar(value=True)
        ttk.Checkbutton(toolbar, text="跟随最新", variable=self.log_follow).pack(side="left", padx=10)
        ttk.Button(toolbar, text="导出结果", command=self.export_logs).pack(side="left", padx=(0, 8))
        ttk.Button(toolbar, text="清空日志", command=self.clear_logs).pack(side="left")
        panes = ttk.Panedwindow(parent, orient="vertical")
        panes.pack(fill="both", expand=True)
        upper, lower = ttk.Frame(panes), ttk.Frame(panes)
        panes.add(upper, weight=2)
        panes.add(lower, weight=3)
        last_height = [0]
        def size_panes(event):
            if event.height > 120 and event.height != last_height[0]:
                last_height[0] = event.height
                panes.sashpos(0, max(70, int(event.height * 0.43)))
        panes.bind("<Configure>", size_panes)
        self.log_tree = ttk.Treeview(upper, columns=("time", "category", "summary"), show="headings", height=3)
        for name, label, width in (("time", "时间", 165), ("category", "类别", 80), ("summary", "判断 / 通信摘要", 430)):
            self.log_tree.heading(name, text=label, anchor="w")
            self.log_tree.column(name, width=width, minwidth=60, stretch=name == "summary")
        scrollbar = ttk.Scrollbar(upper, orient="vertical", command=self.log_tree.yview)
        self.log_tree.configure(yscrollcommand=scrollbar.set)
        self.log_tree.tag_configure("alternate", background="#1c2420")
        self.log_tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        ttk.Label(lower, text="通信与判断详情", style="Heading.TLabel").pack(anchor="w", pady=(14, 8))
        detail_body = ttk.Frame(lower)
        detail_body.pack(fill="both", expand=True)
        self.log_detail = self._text(detail_body, 3)
        detail_scroll = ttk.Scrollbar(detail_body, orient="vertical", command=self.log_detail.yview)
        self.log_detail.configure(yscrollcommand=detail_scroll.set, state="disabled")
        self.log_detail.pack(side="left", fill="both", expand=True)
        detail_scroll.pack(side="right", fill="y")
        self.log_status = tk.StringVar()
        self._copy(parent, textvariable=self.log_status).pack(side="bottom", fill="x", pady=(8, 0), before=panes)
        self._visible_logs = {}
        self.log_tree.bind("<<TreeviewSelect>>", lambda _event: self._show_log_detail())
        self.log_filter.trace_add("write", lambda *_: self._refresh_logs(force=True))
        self.log_search.trace_add("write", lambda *_: self._refresh_logs(force=True))
        self._refresh_logs(force=True)

    def _log(self, category: str, event: str, summary: str, details=None) -> None:
        try:
            self.journal.record(category, event, summary, details)
        except Exception:
            self.journal.warning = "日志记录暂时失败，监督规则保持原有行为。"

    def _filtered_logs(self):
        category, query = self.log_filter.get(), self.log_search.get().strip().casefold()
        return [entry for entry in reversed(self.journal.entries)
                if (category == "全部" or entry["category"] == category)
                and (not query or query in json.dumps(entry, ensure_ascii=False).casefold())]

    def _refresh_logs(self, force=False) -> None:
        if not force and self._log_render_revision == self.journal.revision:
            self.log_status.set(self.journal.warning or self.log_status.get())
            return
        selected = self.log_tree.selection()
        entries = self._filtered_logs()
        self._visible_logs = {entry["id"]: entry for entry in entries}
        self.log_tree.delete(*self.log_tree.get_children())
        for index, entry in enumerate(entries):
            self.log_tree.insert("", "end", iid=entry["id"], values=(
                entry["timestamp"].replace("T", " ")[:19], entry["category"], entry["summary"]),
                tags=("alternate",) if index % 2 else ())
        target = selected[0] if selected and selected[0] in self._visible_logs else None
        if self.log_follow.get() and entries:
            target = entries[0]["id"]
        if target:
            self.log_tree.selection_set(target)
            self.log_tree.see(target)
        self._show_log_detail()
        self._log_render_revision = self.journal.revision
        self.log_status.set(self.journal.warning or f"显示 {len(entries)} 条 / 最近 {len(self.journal.entries)} 条。时间使用本机时区；旧记录保留原时区。")

    def _show_log_detail(self) -> None:
        selected = self.log_tree.selection()
        entry = self._visible_logs.get(selected[0]) if selected else None
        text = "暂无记录。开始周期后会记录判断；连接测试也会产生 AI 通信记录。"
        if entry:
            details = dict(entry["details"])
            request, response = details.pop("request_body", None), details.pop("response_body", None)
            text = f"{entry['timestamp']}  ·  {entry['category']}  ·  {entry['event']}\n{entry['summary']}\n\n"
            text += json.dumps(details, ensure_ascii=False, indent=2)
            if request is not None:
                text += "\n\n请求正文（已屏蔽凭据）\n" + json.dumps(request, ensure_ascii=False, indent=2)
            if response is not None:
                text += "\n\n服务返回正文（已屏蔽凭据，过长会截断）\n" + str(response)
        self.log_detail.configure(state="normal")
        self.log_detail.delete("1.0", "end")
        self.log_detail.insert("1.0", text)
        self.log_detail.configure(state="disabled")

    def clear_logs(self) -> None:
        if not messagebox.askyesno("清空日志", "删除本机保存的判断和 AI 通信日志？目标、规则、Key 与周期状态不受影响。", parent=self.root):
            return
        if self.journal.clear():
            self._log_epoch += 1  # In-flight diagnostics cannot resurrect cleared history.
            self._last_decision = None
        self._refresh_logs(force=True)

    def export_logs(self) -> None:
        path = filedialog.asksaveasfilename(parent=self.root, title="导出当前筛选的日志", defaultextension=".jsonl",
                                          initialfile="IronWill-logs.jsonl", filetypes=[("JSON Lines", "*.jsonl")])
        if not path:
            return
        try:
            text = "".join(json.dumps(entry, ensure_ascii=False) + "\n" for entry in reversed(self._filtered_logs()))
            Path(path).write_text(text, encoding="utf-8")
            self.log_status.set("已导出当前筛选结果。文件可能包含目标、非隐私标题与 AI 正文，请按个人资料保管。")
        except OSError:
            self.log_status.set("导出失败，请检查目标目录权限和磁盘空间。")

    def minimize_to_tray(self) -> None:
        if self.demo or not platform_windows.supported():
            self.status_text.set("此模式不启用 Windows 托盘。")
            return
        if self.tray and self.tray.ready:
            self.root.withdraw()
            return
        if self.tray_starting:
            return
        from .tray_windows import WindowsTray
        self.tray_starting = True
        self.tray_button.configure(state="disabled")
        self.tray = WindowsTray(lambda event: self.events.put(("tray", event)))
        if not self.tray.start():
            self._tray_event("error")
        else:
            self.status_text.set("正在创建托盘图标；成功后窗口才隐藏。")

    def restore_window(self) -> None:
        self.root.deiconify()
        self.root.lift()
        self.root.focus_force()

    def _tray_event(self, event) -> None:
        self.tray_starting = False
        self.tray_button.configure(state="normal")
        if event == "ready":
            self._log("程序", "tray_hidden", "主窗口已收起到托盘，当前周期状态保持不变")
            self.status_text.set("已收起到托盘。点击右下角 IronWill 图标打开，右键可停止并退出。")
            self.root.withdraw()
        elif event == "restore":
            self.restore_window()
        elif event == "exit":
            self.restore_window()
            self.close()
        elif event == "error":
            if self.tray:
                self.tray.stop()
            self.tray = None
            self.restore_window()
            self.status_text.set("托盘不可用，主窗口保持可见；当前周期不受影响。")

    def _text(self, parent: tk.Misc, height: int = 3) -> tk.Text:
        widget = tk.Text(parent, height=height, bg=FIELD, fg=FG, insertbackground=FG,
                       font=("Microsoft YaHei UI", 10),
                       selectbackground="#405631", selectforeground=FG, relief="flat", borderwidth=0,
                       highlightthickness=1, highlightbackground=LINE, highlightcolor=ACCENT,
                       padx=12, pady=11, spacing1=2, spacing3=3, wrap="word", undo=True)
        def _focus_next(event):
            event.widget.tk_focusNext().focus()
            return "break"
        def _focus_prev(event):
            event.widget.tk_focusPrev().focus()
            return "break"
        widget.bind("<Tab>", _focus_next)
        widget.bind("<Shift-Tab>", _focus_prev)
        widget.bind("<ISO_Left_Tab>", _focus_prev)
        return widget

    def _build_mission(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="把决定，变成行动。", style="Page.TLabel").grid(row=0, column=0, sticky="w")
        self._copy(parent, "只专注于这一轮。写下主线，准备好时开始。").grid(row=1, column=0, sticky="ew", pady=(5, 16))
        actions = ttk.Frame(parent)
        actions.grid(row=2, column=0, sticky="ew", pady=(0, 18))
        self.start_button = ttk.Button(actions, text="开始周期  →", style="Primary.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="暂停", command=self.pause_resume)
        self.pause_button.pack(side="left", padx=8)
        self.end_button = ttk.Button(actions, text="结束周期", command=self.end)
        self.end_button.pack(side="left")
        ttk.Button(actions, text="体验提醒", command=self.preview_reminder).pack(side="right")
        metrics = self._card(parent, (18, 12))
        metrics.grid(row=3, column=0, sticky="ew", pady=(0, 14))
        self.api_metric = tk.StringVar(value="0 次")
        self.cache_metric = tk.StringVar(value="0 次")
        for column, (title, value) in enumerate((("当前状态", self.state_text), ("本次 API 请求", self.api_metric), ("窗口结论复用", self.cache_metric))):
            metrics.columnconfigure(column, weight=1, uniform="metrics")
            cell = ttk.Frame(metrics)
            cell.grid(row=0, column=column, sticky="ew", padx=(0, 12))
            ttk.Label(cell, text=title, style="Muted.TLabel").pack(anchor="w")
            ttk.Label(cell, textvariable=value, style="Metric.TLabel").pack(anchor="w", pady=(4, 0))
        main = self._card(parent)
        main.grid(row=4, column=0, sticky="ew", pady=(0, 14))
        top = ttk.Frame(main)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="01  /  当前主线", style="Heading.TLabel").pack(side="left")
        ttk.Button(top, text="保存本页", command=self.save_identity).pack(side="right")
        goal = self._text(main, 2)
        goal.configure(font=("Microsoft YaHei UI", 13))
        goal.pack(fill="x")
        goal.insert("1.0", self.settings.get("goal", ""))
        self.text_fields["goal"] = goal
        pair = ttk.Frame(parent)
        pair.grid(row=5, column=0, sticky="ew", pady=(0, 14))
        pair.columnconfigure(0, weight=1, uniform="reasons")
        pair.columnconfigure(1, weight=1, uniform="reasons")
        for column, (key, title) in enumerate((("reality", "现实 · 你自己的原话"), ("consequence", "后果 · 你自己的原话"))):
            frame = self._card(pair)
            frame.grid(row=0, column=column, sticky="nsew", padx=(0, 7) if column == 0 else (7, 0))
            ttk.Label(frame, text=title, style="Heading.TLabel").pack(anchor="w", pady=(0, 11))
            widget = self._text(frame, 3)
            widget.configure(width=20)
            widget.pack(fill="x")
            widget.insert("1.0", self.settings.get(key, ""))
            self.text_fields[key] = widget
        promise = self._card(parent)
        promise.grid(row=6, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(promise, text="02  /  大愿与署名", style="Heading.TLabel").pack(anchor="w")
        self._copy(promise, "关于你要成为谁的标准，保留你自己的原话。").pack(fill="x", pady=(5, 12))
        oath = self._text(promise, 2)
        oath.pack(fill="x")
        oath.insert("1.0", self.settings.get("oath", ""))
        self.text_fields["oath"] = oath
        sigrow = ttk.Frame(promise)
        sigrow.pack(fill="x", pady=(12, 0))
        ttk.Label(sigrow, text="署名（可留空）").pack(side="left")
        self.signature = tk.StringVar(value=self.settings.get("signature", ""))
        ttk.Entry(sigrow, textvariable=self.signature, width=24).pack(side="left", padx=10)
        diagnosis = self._card(parent)
        diagnosis.grid(row=7, column=0, sticky="ew", pady=(0, 14))
        ttk.Label(diagnosis, text="03  /  当前判断", style="Heading.TLabel").pack(anchor="w", pady=(0, 9))
        self.observation_text = tk.StringVar(value="尚未开始采样。")
        self._copy(diagnosis, textvariable=self.observation_text).pack(fill="x")
        self._copy(parent, "没有固定钟点。开始、暂停、继续和结束都由你决定；重新打开软件不会自动接续监督。体验提醒不升档，不记录惩罚。").grid(row=8, column=0, sticky="ew")

    def _build_rules(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="给这一轮，划清边界。", style="Page.TLabel").grid(row=0, column=0, sticky="w")
        self._copy(parent, "本地规则优先，拿不准就放行。每行填写一个关键词；浏览器不建议整进程归类。").grid(row=1, column=0, sticky="ew", pady=(5, 18))
        lists = ttk.Frame(parent)
        lists.grid(row=2, column=0, sticky="ew")
        lists.columnconfigure(0, weight=1, uniform="rules")
        lists.columnconfigure(1, weight=1, uniform="rules")
        definitions = [
            ("privacy_keywords", "隐私关键词", "优先保护，不交给 AI。"),
            ("blocked_processes", "红线进程", "例如明确的娱乐应用。"),
            ("blocked_titles", "红线标题关键词", "可留空，未匹配标题可由已启用 AI 审核。"),
            ("allowed_processes", "主线进程", "当前工作的专用工具。"),
            ("allowed_titles", "主线标题关键词", "与当前主线有关的页面或项目。"),
        ]
        values = self.policy.to_dict()
        for index, (key, title, help_text) in enumerate(definitions):
            cell = self._card(lists)
            cell.grid(row=index // 2, column=index % 2, sticky="nsew", padx=(0, 7) if index % 2 == 0 else (7, 0), pady=(0, 14))
            ttk.Label(cell, text=title, style="Heading.TLabel").pack(anchor="w")
            self._copy(cell, help_text).pack(fill="x", pady=(5, 10))
            widget = self._text(cell, 3)
            widget.configure(width=20)
            widget.pack(fill="x")
            widget.insert("1.0", "\n".join(values.get(key, [])))
            self.rule_fields[key] = widget
        timings = ttk.LabelFrame(parent, text="计时参数 · 秒", padding=12)
        timings.grid(row=3, column=0, sticky="ew", pady=(2, 14))
        for index, (key, title) in enumerate([
            ("reminder_after_seconds", "首次提醒"), ("escalation_after_seconds", "继续偏离后升级"),
            ("recovery_seconds", "回主线后恢复"), ("quiet_seconds", "喘息时长"),
            ("idle_threshold_seconds", "离开设备判定"),
        ]):
            column, row = (index % 2) * 2, index // 2
            ttk.Label(timings, text=title).grid(row=row, column=column, sticky="w", padx=(0, 7), pady=5)
            var = tk.StringVar(value=str(values.get(key, 60)))
            self.numeric_fields[key] = var
            ttk.Entry(timings, textvariable=var, width=7).grid(row=row, column=column + 1, sticky="w", padx=(0, 17), pady=5)
        self.lock_enabled = tk.BooleanVar(value=bool(values.get("lock_enabled", False)) and not self.demo)
        ttk.Checkbutton(parent, text="启用锁屏权限：仅第三档中明确点击“锁屏”时执行。",
                        variable=self.lock_enabled, command=self._lock_permission_changed).grid(row=4, column=0, sticky="w", pady=(0, 8))
        self._copy(parent, "默认关闭。不会自动锁屏，不拦截系统退出，也不实现自毁。").grid(row=5, column=0, sticky="ew")
        ttk.Button(parent, text="保存并应用规则", style="Primary.TButton", command=self.save_rules).grid(row=6, column=0, sticky="e", pady=(14, 0))

    def _build_connections(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        container = parent
        ttk.Label(container, text="按需连接，保持简单。", style="Page.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 18))
        parent = self._card(container)
        parent.grid(row=1, column=0, sticky="ew", pady=(0, 14))
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="手机配对 · 同一局域网", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        self._copy(parent, "点击启动后才开放本机配对服务。仅在信任的局域网使用；手机将收到当前目标、大愿和周期状态。\n"
                  "两端各自识别本机活动；短时间提醒去重不等于跨设备共享处罚或升档。").grid(row=1, column=0, sticky="ew", pady=(6, 12))
        relay_buttons = ttk.Frame(parent)
        relay_buttons.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.relay_start_button = ttk.Button(relay_buttons, text="启动手机配对", command=self.start_relay)
        self.relay_start_button.pack(side="left")
        self.relay_stop_button = ttk.Button(relay_buttons, text="停止配对", command=self.stop_relay, state="disabled")
        self.relay_stop_button.pack(side="left", padx=8)
        self.relay_status = tk.StringVar(value="尚未启动；未开放网络端口。")
        self._copy(parent, textvariable=self.relay_status).grid(row=3, column=0, sticky="ew", pady=(0, 10))
        self.address_value = tk.StringVar(value="启动后显示")
        self.token_value = tk.StringVar(value="点击“显示口令”后显示")
        self.fingerprint_value = tk.StringVar(value="启动后显示")
        details = ttk.Frame(parent)
        details.grid(row=4, column=0, sticky="ew")
        details.columnconfigure(1, weight=1)
        for row, (title, var, copy_key) in enumerate([
            ("连接地址", self.address_value, "address"), ("设备口令", self.token_value, "token"),
            ("证书指纹", self.fingerprint_value, "fingerprint"),
        ]):
            ttk.Label(details, text=title).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Entry(details, textvariable=var, state="readonly").grid(row=row, column=1, sticky="ew", pady=5)
            btn = ttk.Button(details, text="复制", command=lambda item=copy_key: self.copy_pairing(item))
            btn.grid(row=row, column=2, padx=(8, 0), pady=5)
            self.copy_buttons[copy_key] = btn
        self.show_token_button = ttk.Button(parent, text="显示口令", command=self.toggle_pair_secret)
        self.show_token_button.grid(row=5, column=0, sticky="e", pady=(4, 8))
        self.peers_value = tk.StringVar(value="手机：尚未连接")
        self._copy(parent, textvariable=self.peers_value, style="Accent.TLabel").grid(row=6, column=0, sticky="ew", pady=(8, 0))
        parent = self._card(container)
        parent.grid(row=2, column=0, sticky="ew")
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="AI 灰区判断 · 可留空，离线也可监督", style="Heading.TLabel").grid(row=8, column=0, sticky="w")
        self._copy(parent, "AI 只判断与当前主线的相关性。它不会评价成果、决定升档或执行锁屏。\n"
                  "启用后，仅发送当前目标、进程名和必要的窗口标题。现实、后果、大愿和署名不发给模型。\n"
                  "单字符分类：1 核心相关 / 2 辅助或中立 / 0 明确无关。请求最多 1 个输出 token；官方 DeepSeek 自动关闭思考。显示说明由本机生成。").grid(row=9, column=0, sticky="ew", pady=(6, 12))
        ai_form = ttk.Frame(parent)
        ai_form.grid(row=10, column=0, sticky="ew")
        ai_form.columnconfigure(1, weight=1)
        self.ai_endpoint = tk.StringVar(value=self.settings.get("ai_endpoint", ""))
        self.ai_model = tk.StringVar(value=self.settings.get("ai_model", ""))
        self.ai_key = tk.StringVar()
        for row, (title, var) in enumerate([("兼容服务地址", self.ai_endpoint), ("模型名称", self.ai_model), ("API 密钥", self.ai_key)]):
            ttk.Label(ai_form, text=title).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Entry(ai_form, textvariable=var, show="●" if row == 2 else "").grid(row=row, column=1, sticky="ew", pady=5)
        self._copy(parent, "Key 已保存时可以留空，应用设置会自动读取。也支持 IRONWILL_API_KEY；旧 DEEPSEEK_API_KEY 仅用于官方 DeepSeek 地址。").grid(row=11, column=0, sticky="ew", pady=(8, 8))
        self.ai_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(parent, text="本次运行启用 AI，并同意向所填服务发送上述最少信息", variable=self.ai_enabled,
                        command=self._ai_permission_changed).grid(row=12, column=0, sticky="w", pady=7)
        self.ai_status = tk.StringVar(value="AI 未启用，按本地规则工作。")
        self._copy(parent, textvariable=self.ai_status).grid(row=13, column=0, sticky="ew", pady=7)
        ttk.Button(parent, text="应用 AI 设置", command=self.apply_ai).grid(row=14, column=0, sticky="e")
        self.connection_test_button = ttk.Button(parent, text="用虚拟数据测试 AI 连接", command=self.test_ai_connection)
        self.connection_test_button.grid(row=15, column=0, sticky="e", pady=(10, 0))
        self.connection_test_text = tk.StringVar(value="测试会发出一次虚拟请求，不发送你的任务或真实窗口标题。")
        self._copy(parent, textvariable=self.connection_test_text).grid(row=16, column=0, sticky="ew", pady=(7, 0))
        self.remember_api_key = tk.BooleanVar(value=not self.demo and self.key_store.exists())
        ttk.Checkbutton(parent, text="在本机记住 API Key（使用 Windows 账户加密，应用设置后生效）",
                        variable=self.remember_api_key).grid(row=17, column=0, sticky="w", pady=(14, 5))
        self.key_storage_text = tk.StringVar(value=("已有加密 Key；应用同一服务时自动读取，不在输入框回显。" if self.remember_api_key.get()
                                                   else "尚未记住 Key。勾选后应用设置，下次无需再次填写。"))
        self._copy(parent, textvariable=self.key_storage_text).grid(row=18, column=0, sticky="ew", pady=(0, 6))
        key_buttons = ttk.Frame(parent)
        key_buttons.grid(row=19, column=0, sticky="e")
        ttk.Button(key_buttons, text="从旧版 DeepSeek 文件导入", command=self.import_legacy_key).pack(side="left", padx=(0, 8))
        ttk.Button(key_buttons, text="删除已保存 Key", command=self.forget_saved_key).pack(side="left")
        cache_controls = ttk.Frame(parent)
        cache_controls.grid(row=20, column=0, sticky="ew", pady=(18, 5))
        ttk.Label(cache_controls, text="已审核窗口复用期限").pack(side="left", padx=(0, 12))
        configured_mode = self.settings.get("ai_cache_mode", "本工作周期")
        self.ai_cache_mode = tk.StringVar(value=configured_mode if configured_mode in CACHE_MODES else "本工作周期")
        ttk.Combobox(cache_controls, textvariable=self.ai_cache_mode, state="readonly", width=14,
                     values=tuple(CACHE_MODES)).pack(side="left")
        ttk.Button(cache_controls, text="清空判断缓存", command=self.clear_ai_memory).pack(side="right")
        self._copy(parent, "默认本工作周期内复用同一进程和页面标题的结论，应用 AI 设置后生效。\n"
                  "新标题停留至少 3 秒才审核，自动请求至少间隔 30 秒；进程/关键词本地规则优先。").grid(row=21, column=0, sticky="ew", pady=(3, 6))
        self.review_status = tk.StringVar()
        self._copy(parent, textvariable=self.review_status).grid(row=22, column=0, sticky="ew")

    def clear_ai_memory(self, prompt: bool = True) -> None:
        if prompt and not messagebox.askyesno("清空判断缓存", "确定要清空已审核窗口的缓存吗？后续相同窗口将重新由 AI 审核。", parent=self.root):
            return
        self.review_memory.clear()
        if self.ai:
            self.ai.clear_cache()
        self.ai_generation += 1
        self.ai_pending = False
        self.ai_failures.clear()
        self.ai_candidate = None
        self.semantic_result = None
        self.last_ai_details = None
        self._log("程序", "ai_memory_cleared", "判断缓存已清空，后续窗口将重新审核")
        self._refresh_status()

    def _identity(self) -> dict[str, str]:
        values = {key: widget.get("1.0", "end-1c") for key, widget in self.text_fields.items()}
        values["signature"] = self.signature.get()
        return values

    def save_identity(self) -> bool:
        self.settings.update(self._identity())
        if not self._persist_settings():
            return False
        suffix = "当前周期仍沿用开始时的目标和大愿；结束后重新开始可采用新内容。" if self.engine.state.get("status") in {"active", "paused"} else ""
        self.status_text.set(("体验内容已更新，仅保存在内存。" if self.demo else "已保存到本机。") + suffix)
        return True

    def _persist_settings(self) -> bool:
        if self.demo:
            return True
        try:
            _write_json(self.settings_path, self.settings)
            return True
        except Exception:
            messagebox.showerror("无法保存", "个人设置未能写入。请检查数据目录权限与磁盘空间。原文件不会被截断。", parent=self.root)
            return False

    def save_rules(self, quiet: bool = False) -> bool:
        values = self.policy.to_dict()
        for key, widget in self.rule_fields.items():
            values[key] = list(dict.fromkeys(line.strip() for line in widget.get("1.0", "end-1c").splitlines() if line.strip()))
        try:
            for key, var in self.numeric_fields.items():
                value = float(var.get().strip())
                if not math.isfinite(value) or not 1 <= value <= 86400:
                    raise ValueError("计时参数必须在 1 到 86400 秒之间")
                values[key] = value
            values["lock_enabled"] = bool(self.lock_enabled.get()) and not self.demo
            new_policy = Policy.from_dict(values)
            if not self.demo:
                save_policy(self.policy_path, new_policy)
            self.policy = new_policy
            self.engine.update_policy(new_policy)
            self.semantic_result = None
            self.ai_generation += 1
            self.ai_pending = False
            self._log("程序", "rules_applied", "本地规则已应用，重新建立采样基线",
                      {"privacy_rules": len(new_policy.privacy_keywords),
                       "blocked_process_rules": len(new_policy.blocked_processes),
                       "blocked_title_rules": len(new_policy.blocked_titles),
                       "allowed_process_rules": len(new_policy.allowed_processes),
                       "allowed_title_rules": len(new_policy.allowed_titles)})
            self._refresh_status()
            if not quiet:
                self.status_text.set("规则已应用。" + ("体验模式没有写入文件。" if self.demo else "后续窗口按新规则重新计时。"))
            return True
        except (ValueError, TypeError) as error:
            messagebox.showerror("规则未保存", str(error), parent=self.root)
        except Exception:
            messagebox.showerror("规则未保存", "请检查规则格式和数据目录权限；本次没有应用新规则。", parent=self.root)
        return False

    def _lock_permission_changed(self) -> None:
        if self.demo or not platform_windows.supported():
            self.lock_enabled.set(False)
            self.status_text.set("此模式不支持实际锁屏。")
        else:
            self.status_text.set("权限选择尚未保存；点击“保存并应用规则”后生效。")

    def start(self) -> None:
        if self.engine.state.get("status") in {"active", "paused"}:
            return
        identity = self._identity()
        if not identity["goal"].strip():
            self.notebook.select(0)
            self.text_fields["goal"].focus_set()
            self.status_text.set("先写下这一轮已经决定要做的事。")
            return
        if not self.save_identity():
            return
        if not self.save_rules(quiet=True):
            return
        self.semantic_result = None
        self.last_observation = None
        self.pending_reminder = None
        self.ai_generation += 1
        self.ai_pending = False
        self.last_tick = time.monotonic()
        self.clear_ai_memory(prompt=False)
        self._handle(self.engine.start(identity["goal"], oath=identity["oath"],
                                       reality=identity["reality"], consequence=identity["consequence"]))
        self._save_state()
        if self.demo:
            self.status_text.set("体验周期已开始；点击“体验提醒”查看流程。")
        elif not self.policy.blocked_processes and not self.policy.blocked_titles and not self.ai:
            self.status_text.set("周期已开始，但尚无红线规则且 AI 未启用，不会自动判断分心。可启用 AI 审核标题，或添加本地红线。")
        elif not self.policy.blocked_processes and not self.policy.blocked_titles:
            self.status_text.set("周期已开始。无需填写红线标题，AI 会审核未匹配窗口；偏离时给语义提醒，本地红线负责三档计时。")
        else:
            self.status_text.set("周期已开始。本地窗口监督正在运行，未知情况放行。")
        self._refresh_status()

    def pause_resume(self) -> None:
        state = self.engine.state.get("status")
        if state == "active":
            self._handle(self.engine.pause())
            self._close_reminder()
            self.pending_reminder = None
            self.status_text.set("已暂停；暂停期间不采样窗口、不累计偏离。")
        elif state == "paused":
            self.last_tick = time.monotonic()
            self.last_observation = None
            self._handle(self.engine.resume())
            self.status_text.set("已继续；重新建立当前窗口基线。")
        self.ai_generation += 1
        self.ai_pending = False
        self.semantic_result = None
        self._save_state()
        self._refresh_status()

    def end(self) -> None:
        self._handle(self.engine.end())
        self._close_reminder()
        self.relay_reminder = None
        self.pending_reminder = None
        self.ai_generation += 1
        self.ai_pending = False
        self.semantic_result = None
        self.clear_ai_memory(prompt=False)
        self._save_state()
        self.status_text.set("这个周期已结束。不会评价成果，也不会删除任何文件。")
        self._refresh_status()

    def _save_state(self) -> None:
        if self.demo:
            return
        try:
            self.data_dir.mkdir(parents=True, exist_ok=True)
            save_snapshot(self.snapshot_path, self.engine.snapshot())
            self.last_saved = time.monotonic()
            self.persistence_warning = ""
        except Exception:
            self.persistence_warning = "运行记录未能保存；请检查数据目录。"
            self.status_text.set(self.persistence_warning)

    def _poll(self) -> None:
        if self.closing:
            return
        try:
            self._drain_results()
            if self.closing:
                return  # A tray exit can destroy Tk while this timer callback is running.
            now = time.monotonic()
            elapsed, self.last_tick = now - self.last_tick, now
            if not self.demo and self.engine.state.get("status") == "active":
                observation = platform_windows.sample_foreground()
                self.last_observation = observation
                if classify(observation.process, observation.title, self.policy) == "GRAY":
                    self._request_ai(observation)
                else:
                    self.ai_candidate = None
                if self.semantic_result:
                    process, title, verdict, timestamp = self.semantic_result
                    if process == observation.process and title == observation.title and now - timestamp < 60:
                        observation = replace(observation, semantic=verdict)
                baseline = self.engine._discard_next_tick
                events = self.engine.tick(observation, elapsed)
                self._record_observation(observation, elapsed, baseline=baseline)
                self._handle(events)
                if now - self.last_saved >= 15:
                    self._save_state()
            self._poll_relay()
            self._retry_reminder()
            self._refresh_status()
        except Exception:
            # A UI adapter fault cannot silently continue escalating without observation.
            if self.engine.state.get("status") == "active":
                self.engine.pause()
                self._save_state()
            self.status_text.set("监督已暂停：本地采样或接口出现异常。请检查运行环境后手动继续。")
            self._log("程序", "monitoring_error", "采样或接口异常，监督已暂停")
            self._refresh_status()
        self.timer_id = self.root.after(1000, self._poll)

    def _handle(self, events: list[dict[str, Any]] | None) -> None:
        for event in events or []:
            kind = event.get("type")
            descriptions = {"reminder": "本地红线触发分档提醒", "gray_hint": "AI 语义判断触发轻提醒（不升档）",
                            "recovered": "连续相关活动达到阈值，档位已清零", "lock_requested": "收到用户显式锁屏选择",
                            "status": str(event.get("message", "周期状态已更新")), "error": "当前操作没有被执行"}
            if kind in descriptions:
                self._log("判断" if kind in {"reminder", "gray_hint", "recovered"} else "程序",
                          str(kind), descriptions[kind],
                          {"session_id": self.engine.state.get("id"), "stage": event.get("stage", 0),
                           "status": self.engine.state.get("status"), "choice": event.get("choice", "")})
            if kind == "reminder":
                stage = int(event.get("stage", self.engine.state.get("stage", 1)))
                event_id = uuid.uuid4().hex
                self.relay_reminder = {"event_id": event_id, "stage": stage, "created_at": time.time()}
                claimed = True
                if self.relay:
                    self._publish_state()
                    try:
                        claimed = self.relay.claim_reminder("desktop", event_id)
                    except Exception:
                        claimed = True
                if claimed:
                    self.pending_reminder = None
                    self._show_reminder(stage, str(event.get("message", "")))
                else:
                    self.pending_reminder = {"session_id": self.engine.state.get("id"), "stage": stage,
                                             "message": str(event.get("message", "")), "event_id": event_id,
                                             "retry_at": time.monotonic() + 30}
                    self.status_text.set("另一端刚显示过提醒；本机这次提醒将在短暂延后后显示。")
            elif kind == "lock_requested":
                # Core emits this only for an explicit LOCK choice. Check the UI gate again.
                if self.demo or not self.policy.lock_enabled:
                    self.status_text.set("未执行锁屏：锁屏权限没有启用。")
                else:
                    success, detail = platform_windows.request_lock()
                    self.status_text.set("已向 Windows 请求锁屏。" if success else "锁屏未成功：" + str(detail))
            elif kind == "gray_hint":
                self.status_text.set("AI 建议这个窗口可能与主线无关，请自行判断。本次只提醒，不升档。")
                if not (self.dialog and self.dialog.winfo_exists() and not self.dialog_preview and not self.dialog_advisory):
                    self._show_reminder(0, str(event.get("message", "这个窗口可能与主线无关，请自行判断。")), advisory=True)
            elif kind == "recovered":
                self._close_reminder()
                self.relay_reminder = None
                self.pending_reminder = None
                self.status_text.set(str(event.get("message", "已回到主线，档位清零。")))
            elif kind == "status":
                self.status_text.set(str(event.get("message", "状态已更新。")))
            elif kind == "error":
                self.status_text.set(str(event.get("message", "这个操作当前不可用。")))

    def preview_reminder(self) -> None:
        if self.dialog and self.dialog.winfo_exists() and not self.dialog_preview:
            self.dialog.lift()
            self.status_text.set("当前有真实提醒。处理或关闭它之后，可以体验提醒流程。")
            return
        self._show_reminder(1, "这不是你刚刚决定要做的事。现在回到主线。", preview=True)

    def _retry_reminder(self) -> None:
        pending = self.pending_reminder
        if not pending:
            return
        state = self.engine.state
        if state.get("status") != "active" or state.get("id") != pending["session_id"] or state.get("stage") != pending["stage"]:
            self.pending_reminder = None
            return
        if time.monotonic() < pending["retry_at"]:
            return
        claimed = True
        if self.relay:
            try:
                claimed = self.relay.claim_reminder("desktop", pending["event_id"])
            except Exception:
                claimed = True
        if claimed:
            self.pending_reminder = None
            self._show_reminder(pending["stage"], pending["message"])
        else:
            pending["retry_at"] = time.monotonic() + 30

    def _show_reminder(self, stage: int, message: str, preview: bool = False, advisory: bool = False) -> None:
        self._close_reminder()
        self.dialog_stage, self.dialog_preview = stage, preview
        self.dialog_advisory = advisory
        dialog = tk.Toplevel(self.root)
        self.dialog = dialog
        dialog.title("提醒体验 · 不计入周期" if preview else ("语义提醒 · 不计档位" if advisory else "IronWill · 第 %s 档提醒" % stage))
        dialog.configure(bg=BG)
        dialog.geometry("620x480")
        dialog.minsize(480, 370)
        dialog.attributes("-topmost", True)
        dialog.protocol("WM_DELETE_WINDOW", self._close_reminder)
        content = ttk.Frame(dialog, padding=24)
        content.pack(fill="both", expand=True)
        ttk.Label(content, text="IRONWILL  /  " + ("PREVIEW" if preview else "REMINDER"), style="Eyebrow.TLabel").pack(anchor="w", pady=(0, 8))
        title_row = ttk.Frame(content)
        title_row.pack(fill="x")
        ttk.Label(title_row, text="体验提醒" if preview else ("语义提醒 · 由你判断" if advisory else "回到你的决定"), style="Page.TLabel").pack(side="left")
        if preview:
            ttk.Button(title_row, text="看第三档", command=lambda: self._show_reminder(3, "你写下的决定仍在这里。回到主线。", preview=True)).pack(side="right")
        line = message if message.strip() else ("这是第三次提醒。回到主线。" if stage >= 3 else "你已经决定了这一轮的主线。现在回去。")
        goal = self._identity()["goal"] if preview else self.engine.state.get("goal", "")
        body = self._text(content, 10)
        body.insert("1.0", line + "\n\n当前主线：\n" + str(goal))
        if preview and stage >= 3 and self._identity()["oath"]:
            body.insert("end", "\n\n你写下的大愿：\n" + self._identity()["oath"])
        body.configure(state="disabled", font=("Microsoft YaHei UI", 12))
        buttons = ttk.Frame(content)
        buttons.pack(fill="x", side="bottom")
        if advisory:
            ttk.Button(buttons, text="知道了，返回主线", style="Primary.TButton", command=self._dismiss_advisory).pack(side="left")
        else:
            ttk.Button(buttons, text="返回主线", style="Primary.TButton", command=lambda: self._choose("BACK", preview=preview)).pack(side="left")
        if stage < 3 and not advisory:
            ttk.Button(buttons, text="这值得", command=lambda: self._choose("WORTH", preview=preview)).pack(side="left", padx=8)
            ttk.Button(buttons, text="喘息一下", command=lambda: self._choose("BREATHE", preview=preview)).pack(side="left")
        elif not advisory:
            ttk.Button(buttons, text="锁屏", command=lambda: self._choose("LOCK", preview=preview),
                       state="normal" if self.policy.lock_enabled and not self.demo else "disabled").pack(side="left", padx=8)
        if advisory:
            self._copy(content, "这里只提供相关性建议，不增加档位，不执行锁屏。").pack(side="bottom", fill="x", pady=10)
        elif stage >= 3:
            self._copy(content, "第三档只提供返回与显式锁屏。关闭提醒不会自动锁屏。").pack(side="bottom", fill="x", pady=10)
        elif preview:
            self._copy(content, "本窗口只是体验。任何选择都不会改变当前周期。").pack(side="bottom", fill="x", pady=10)
        body.pack(fill="both", expand=True, pady=(16, 12))
        # Non-modal by design: no grab_set(), wait_window(), or nested mainloop.
        dialog.lift()

    def _close_reminder(self) -> None:
        if self.dialog and self.dialog.winfo_exists():
            self.dialog.destroy()
        self.dialog = None
        self.dialog_advisory = False

    def _dismiss_advisory(self) -> None:
        self._close_reminder()
        self.status_text.set("语义提醒已关闭。实际档位没有改变。")

    def _choose(self, action: str, reflection: str = "", preview: bool = False) -> None:
        if preview:
            self._close_reminder()
            self.status_text.set("体验结束：没有升档、锁屏或记录惩罚。")
            return
        events = self.engine.choose(action, reflection=reflection)
        if not any(event.get("type") == "error" for event in (events or [])):
            self._close_reminder()
            self.relay_reminder = None
            self.pending_reminder = None
        self._handle(events)
        self._save_state()
        self._refresh_status()

    def _refresh_status(self) -> None:
        state = self.engine.state
        status = state.get("status", "idle")
        labels = {"idle": "未开始", "active": "监督中", "paused": "已暂停", "ended": "周期已结束"}
        self.state_text.set(labels.get(status, status) + (" · 第 %s 档" % state.get("stage", 0) if status in {"active", "paused"} else ""))
        self.start_button.configure(state="disabled" if status in {"active", "paused"} else "normal")
        self.pause_button.configure(text="继续" if status == "paused" else "暂停", state="normal" if status in {"active", "paused"} else "disabled")
        self.end_button.configure(state="normal" if status in {"active", "paused"} else "disabled")
        supported = platform_windows.supported()
        windows = "体验模式" if self.demo else ("Windows 窗口识别可用" if supported else "当前系统不支持窗口识别")
        ai = "AI 已启用" if self.ai else "AI 离线"
        lock = "锁屏需明确点击" if self.policy.lock_enabled and not self.demo else "锁屏权限关闭"
        self.capability_text.set("%s  ·  %s  ·  %s  ·  自毁保险保留" % (windows, ai, lock))
        self.api_metric.set(f"{self.ai_api_count} 次")
        self.cache_metric.set(f"{self.ai_cache_hits} 次")
        self._refresh_observation_status()
        self._refresh_logs()
        self.review_status.set(self.review_memory.warning or
                               f"判断记忆 {len(self.review_memory.entries)} 条 · 本次实际 API 请求 {self.ai_api_count} 次 · 窗口缓存复用 {self.ai_cache_hits} 次")

    def _record_observation(self, observation: Observation, elapsed: float, *, baseline=False) -> None:
        state = self.engine.state
        local = classify(observation.process, observation.title, self.policy)
        idle = observation.idle_seconds
        details = {"session_id": state.get("id"), "stage": state.get("stage", 0),
                   "local_classification": local, "engine_classification": state.get("last_classification"),
                   "drift_seconds": round(float(state.get("drift_seconds", 0)), 1),
                   "quiet_remaining": round(float(state.get("quiet_remaining", 0)), 1)}
        semantic = observation.semantic if observation.semantic in {"related", "neutral", "unrelated", "unknown"} else "unknown"
        if local == "PRIVATE":
            summary = "隐私规则命中：标题和进程不记录，不请求 AI，不累计偏离"
        elif observation.locked:
            summary = "设备锁定：暂停计时与 AI 审核"
        elif (observation.status != "ok" or idle is None or not isinstance(idle, (int, float))
              or not math.isfinite(idle) or idle < 0):
            summary = "采样不可用：没有有效判断，不累计偏离"
            details["observation_status"] = observation.status
        elif idle >= self.policy.idle_threshold_seconds:
            summary = "达到输入空闲阈值：暂停计时与 AI 审核"
        else:
            details.update(process=observation.process[:260], title=observation.title[:1500],
                           goal=str(state.get("goal", ""))[:4000], semantic=semantic)
            if baseline:
                summary = "建立本轮采样基线：本次不累计时间"
            elif not math.isfinite(elapsed) or elapsed <= 0 or elapsed > self.policy.max_sample_gap_seconds:
                summary = "采样时间断档：本次不补计偏离"
            elif local == "ON_TASK":
                summary = "本地规则判断为主线：不请求 AI，可参与连续回归计时"
            elif local == "DRIFT":
                summary = "本地规则判断为红线：按有效活动时间计时"
            elif local == "UNKNOWN":
                summary = "未识别到前台窗口：不累计偏离"
            elif not self.ai:
                summary = "本地规则未匹配，AI 未启用：没有审核结论"
            elif semantic == "related":
                summary = "AI 判断与主线相关：可参与连续回归计时"
            elif semantic == "neutral":
                summary = "AI 分类为辅助/中立：保持安静，不累计偏离，也不清除已有档位"
            elif semantic == "unrelated":
                summary = "AI 判断可能偏离：仅语义提醒，不累计本地红线档位"
            else:
                summary = "本地规则未匹配：等待 AI 或尚无明确结论"
            if local in {"DRIFT", "ON_TASK"}:
                for field in ("blocked_processes", "allowed_processes", "blocked_titles", "allowed_titles"):
                    text = observation.process if field.endswith("processes") else observation.title
                    matches = [word for word in getattr(self.policy, field) if word in text.casefold()]
                    if matches:
                        details.setdefault("matched_rules", {})[field] = matches
        signature = (state.get("id"), summary, details.get("process"), details.get("title"),
                     semantic if local != "PRIVATE" else None, details["stage"],
                     bool(details["quiet_remaining"]), bool(state.get("gray_hint_remaining", 0)),
                     self.ai_generation, str(details.get("matched_rules", {})))
        now = time.monotonic()
        if signature != self._last_decision or now - self._last_decision_time >= 60:
            self._log("判断", "observation", summary, details)
            self._last_decision, self._last_decision_time = signature, now

    def _audit_callback(self, generation, session_id, epoch, purpose):
        def receive(event):
            self.events.put(("ai_audit", generation, session_id, epoch, purpose, event))
        return receive

    def _refresh_observation_status(self) -> None:
        state = self.engine.state
        if self.demo:
            self.observation_text.set("安全体验模式：没有采样或发送 AI 请求。")
            return
        if state.get("status") != "active":
            self.observation_text.set("当前没有运行监督；点击开始或继续后才采样和审核。")
            return
        observation = self.last_observation
        if observation is None:
            self.observation_text.set("正在等待本轮第一条窗口观察。")
            return
        if observation.locked:
            self.observation_text.set("设备已锁定：暂停计时与 AI 审核。")
            return
        idle = observation.idle_seconds
        if (observation.status != "ok" or idle is None or not isinstance(idle, (int, float))
                or not math.isfinite(idle) or idle < 0):
            self.observation_text.set("窗口或输入状态暂时无法读取：没有审核结论，也不计为偏离。")
            return
        if idle >= self.policy.idle_threshold_seconds:
            self.observation_text.set(f"已约 {int(idle)} 秒未检测到输入，达到空闲阈值：暂停计时与 AI 审核。阅读或思考也可能出现这种状态。")
            return
        local = classify(observation.process, observation.title, self.policy)
        if local == "PRIVATE":
            self.observation_text.set("命中隐私规则：不显示标题、不发送 AI 请求、不计偏离。")
            return
        if local == "UNKNOWN":
            self.observation_text.set("没有可识别的前台窗口：等待新观察。")
            return
        prefix = "当前窗口：" + (observation.process[:80] or "未知进程") + " · " + (observation.title[:160] or "无标题") + "\n"
        quiet = float(state.get("quiet_remaining", 0))
        if local == "ON_TASK":
            verdict = "命中主线白名单，按本地规则放行；没有交给 AI 审核。"
        elif local == "DRIFT":
            remaining = max(0.0, float(state["next_reminder_at"]) - float(state["drift_seconds"]))
            verdict = ("命中本地红线；已到第三档。" if state["stage"] >= 3 else
                       f"命中本地红线；距离下一档还需约 {math.ceil(remaining)} 秒有效偏离时间。")
        elif not self.ai:
            verdict = "未命中本地规则；AI 尚未启用，没有标题审核结论。无需预填红线标题，启用 AI 后可审核这些窗口。"
        else:
            details = self.last_ai_details
            fresh = (details and details["generation"] == self.ai_generation
                     and details["session_id"] == state.get("id")
                     and details["process"] == observation.process and details["title"] == observation.title
                     and time.monotonic() - details["timestamp"] < 60)
            if fresh:
                labels = {"related": "AI 判断与当前主线相关，本次保持安静。",
                          "neutral": "AI 分类为辅助/中立，保持安静；不认定已回到主线。",
                          "unrelated": "AI 判断可能偏离，按语义提醒处理（不升档）。",
                          "unknown": "AI 暂无明确的相关性结论。"}
                verdict = labels[details["verdict"]] if details["available"] else "AI 审核未完成，这不代表你在专注。"
                verdict += "\n" + ("最近结果来自缓存。" if details["cached"] else "") + "分类说明/诊断：" + details["reason"]
            else:
                verdict = "正在审核这个标题。" if self.ai_pending else (self.ai_wait_reason or "未匹配标题将交给 AI，等待下一次审核。")
            verdict += f"\n本次实际 API 请求 {self.ai_api_count} 次；窗口缓存复用 {self.ai_cache_hits} 次。"
        if quiet > 0:
            verdict += f"\n你选择的暂缓还剩约 {math.ceil(quiet)} 秒有效活动时间。"
        if float(state.get("gray_hint_remaining", 0)) > 0 and local == "GRAY":
            verdict += "\n语义提醒处于冷却中，不会反复弹出。"
        self.observation_text.set(prefix + verdict)

    def test_ai_connection(self) -> None:
        if self.demo or not self.ai:
            self.connection_test_text.set("请先勾选启用 AI，并点击“应用 AI 设置”，再测试连接。")
            return
        if self.connection_test_id:
            return
        test_id = self.connection_test_id = uuid.uuid4().hex
        generation, client = self.ai_generation, self.ai
        epoch = self._log_epoch
        audit = self._audit_callback(generation, self.engine.state.get("id"), epoch, "虚拟连接测试")
        self.connection_test_button.configure(state="disabled")
        self.connection_test_text.set("正在用虚拟数据测试接口，不读取你的窗口……")
        def work() -> None:
            try:
                result = client.check_connection(audit=audit)
            except Exception:
                result = {"available": False, "reason": "模型连接测试未完成，请检查服务配置。"}
            self.events.put(("ai_connection", test_id, generation, result, epoch))
        threading.Thread(target=work, name="ironwill-ai-connection", daemon=True).start()

    def apply_ai(self) -> None:
        if self.demo:
            self.ai_enabled.set(False)
            self.ai_status.set("安全体验不连接模型服务。")
            return
        self.ai_generation += 1
        self.ai_pending = False
        self.semantic_result = None
        self.last_ai_details = None
        self.ai = None
        self._refresh_status()
        if not self.ai_enabled.get():
            self.ai = None
            self.ai_status.set("AI 已关闭，继续使用本地规则。")
            self._log("程序", "ai_disabled", "AI 已关闭，继续使用本地规则")
            self._refresh_status()
            return
        endpoint, model, entered_key = self.ai_endpoint.get().strip(), self.ai_model.get().strip(), self.ai_key.get().strip()
        if not endpoint or not model:
            self.ai_status.set("请填写服务地址和模型名称；当前没有启用新的 AI 设置。")
            return
        try:
            parts = urlsplit(endpoint)
        except ValueError:
            self.ai_status.set("服务地址格式无效；没有启用 AI。")
            return
        if (parts.username or parts.password or parts.query or parts.fragment or not parts.hostname
                or not (parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in {"localhost", "127.0.0.1", "::1"}))):
            self.ai_status.set("地址需使用 HTTPS（本机可用 HTTP），且不能把凭据或查询参数放进地址。")
            return
        try:
            identity = endpoint_identity(endpoint)
            key, key_source = "", ""
            if entered_key:
                key, key_source = validate_key(entered_key), "本次输入"
            elif self.runtime_key and self.runtime_key[0] == identity:
                key, key_source = self.runtime_key[1], "本次运行"
            else:
                saved = self.key_store.load(endpoint)
                if saved:
                    key, key_source = saved, "本机加密保存"
                else:
                    key, key_source = environment_key(endpoint)
            if not key and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
                self.ai_status.set("未找到这个服务可用的 Key。请输入一次、导入旧文件，或配置对应环境变量。")
                return
            from .ai import AIClient
            client = AIClient(endpoint, model, api_key=key or None)
        except (CredentialError, OSError):
            self.ai_status.set("Key 无法读取或格式无效。可重新输入；保存失败的副本可以用下方按钮删除。")
            return
        except Exception:
            self.ai_status.set("AI 设置无效，未连接服务。请检查地址、模型和运行依赖。")
            return
        self.active_cache_mode = self.ai_cache_mode.get() if self.ai_cache_mode.get() in CACHE_MODES else "本工作周期"
        self.settings.update({"ai_endpoint": endpoint, "ai_model": model, "ai_cache_mode": self.active_cache_mode})
        if not self._persist_settings():
            return
        try:
            if self.remember_api_key.get() and key:
                self.key_store.save(endpoint, key)
                self.key_storage_text.set("已用 Windows 账户加密记住 Key。下次应用同一服务时自动读取；更新程序无需重填。")
                self.ai_key.set("")
            elif not self.remember_api_key.get():
                self.key_store.delete()
                self.key_storage_text.set("本机没有保存 Key；当前 Key 仅用于本次运行。")
            else:
                self.key_storage_text.set("当前本机服务无需 Key，没有创建密钥文件。")
        except (CredentialError, OSError):
            self.ai_status.set("加密保存或删除 Key 未成功，AI 未启用。检查本机权限，或取消记住后重试。")
            return
        self.runtime_key = (identity, key) if key else None
        self.journal.protect_secret(key)
        self.ai_key.set("")
        self.ai = client
        self.ai_candidate = None
        self.ai_wait_reason = ""
        self._log("程序", "ai_configured", "AI 配置已应用；应用配置本身不发请求",
                  {"endpoint": client.endpoint, "model": model})
        self.ai_status.set("AI 已配置；标题在周期开始后审核。" + ("Key 来源：" + key_source + "。" if key_source else "本机服务无需 Key。"))
        self._refresh_status()

    def import_legacy_key(self) -> None:
        if self.demo:
            self.key_storage_text.set("安全体验不读取或保存密钥文件。")
            return
        endpoint = self.ai_endpoint.get().strip()
        if endpoint:
            try:
                parts = urlsplit(endpoint_identity(endpoint))
                if parts.hostname != "api.deepseek.com" or parts.scheme != "https" or parts.port not in (None, 443):
                    raise CredentialError()
            except CredentialError:
                self.key_storage_text.set("这个入口用于旧版 DeepSeek Key，请先使用官方 DeepSeek 服务地址，避免交给其他服务。")
                return
        selected = filedialog.askopenfilename(parent=self.root, title="导入旧版 DeepSeek 密钥文件",
                                              initialfile="deepseek_key.txt",
                                              filetypes=[("文本文件", "*.txt"), ("所有文件", "*.*")])
        if not selected:
            return
        try:
            key = read_legacy_key(Path(selected))
        except CredentialError as error:
            self.key_storage_text.set(str(error))
            return
        if not endpoint:
            self.ai_endpoint.set("https://api.deepseek.com")
        if not self.ai_model.get().strip():
            self.ai_model.set("deepseek-flash")
        self.ai_key.set(key)
        self.remember_api_key.set(True)
        self.key_storage_text.set("已从旧文件导入到本次输入。勾选启用 AI 并应用设置后加密保存；旧文件不会被改动。")

    def forget_saved_key(self) -> None:
        if self.demo:
            self.key_storage_text.set("安全体验不操作真实密钥文件。")
            return
        if not messagebox.askyesno("删除已保存 Key", "确定要删除本机保存的 API Key 吗？服务商账户中的 Key 不受影响。", parent=self.root):
            return
        try:
            self.key_store.delete()
        except OSError:
            self.key_storage_text.set("本机密钥副本未能删除，请检查数据目录权限。")
            return
        self.remember_api_key.set(False)
        self.ai_key.set("")
        self.runtime_key = None
        self.ai_enabled.set(False)
        self._ai_permission_changed()
        self.key_storage_text.set("已删除本机保存的 Key 并关闭 AI；服务商账户中的 Key 不会被撤销。")

    def _ai_permission_changed(self) -> None:
        if not self.ai_enabled.get():
            self.ai = None
            self.ai_generation += 1
            self.ai_pending = False
            self.semantic_result = None
            self.ai_status.set("AI 已立即关闭；已经发出的请求可能仍由服务处理，但结果不会再应用。")
            self._log("程序", "ai_disabled", "AI 已关闭；已发出的请求无法撤回，旧结果不再应用")
            self._refresh_status()

    def _request_ai(self, observation: Observation) -> None:
        now = time.monotonic()
        if not self.ai:
            return
        if (observation.status != "ok" or observation.locked or observation.idle_seconds is None
                or not isinstance(observation.idle_seconds, (int, float))
                or not math.isfinite(observation.idle_seconds) or observation.idle_seconds < 0
                or observation.idle_seconds >= self.policy.idle_threshold_seconds
                or classify(observation.process, observation.title, self.policy) != "GRAY"):
            self.ai_candidate = None
            return
        # AIClient also enforces privacy policy. Do not include reality, consequence, oath, or signature.
        generation, client = self.ai_generation, self.ai
        session_id = self.engine.state.get("id")
        goal, process, title = str(self.engine.state.get("goal", "")), observation.process, observation.title
        policy = self.policy
        candidate = (process, title)
        if not self.ai_candidate or self.ai_candidate[:2] != candidate:
            self.ai_candidate = (process, title, now)
        identity = self._review_identity(process, title)
        cached = self.review_memory.lookup(identity, session_id, CACHE_MODES[self.active_cache_mode])
        if cached:
            details = self.last_ai_details
            same = (details and details["generation"] == generation and details["session_id"] == session_id
                    and details["process"] == process and details["title"] == title)
            self.semantic_result = (process, title, cached["verdict"], now)
            if not same:
                self.ai_cache_hits += 1
                self._log("判断", "ai_memory", "复用已审核窗口：本次没有 API 请求",
                          {"process": process[:260], "title": title[:1500], "session_id": session_id,
                           "cache_mode": self.active_cache_mode, "result": cached})
            self.last_ai_details = {"generation": generation, "session_id": session_id, "process": process,
                                    "title": title, "verdict": cached["verdict"], "timestamp": now,
                                    "reason": cached["reason"], "cached": True, "available": True}
            self.ai_wait_reason = "已复用当前周期的判断，无需重新请求。"
            return
        self.semantic_result = None  # An expired/missing memory cannot keep refreshing an old verdict.
        if now - self.ai_candidate[2] < 3:
            self.ai_wait_reason = "等待窗口稳定停留至少 3 秒后审核，快速切换不会发请求。"
            return
        if self.ai_pending:
            self.ai_wait_reason = "已有审核请求正在进行，等待返回。"
            return
        if now - self.ai_last_request < 30:
            self.ai_wait_reason = "自动请求至少间隔 30 秒，当前等待下一次审核。"
            return
        failure = self.ai_failures.get(identity)
        if failure and now < failure[1]:
            self.ai_wait_reason = "上次审核失败，正在退避等待后再试；没有将失败当成偏离。"
            return
        epoch = self._log_epoch
        audit = self._audit_callback(generation, session_id, epoch, "窗口标题审核")
        self.ai_pending, self.ai_last_request = True, now
        self.ai_status.set("正在审核未匹配标题，无需填写红线关键词……")
        def work() -> None:
            try:
                result = client.advise(goal, process, title, policy=policy, audit=audit)
            except Exception:
                result = {"verdict": "unknown", "reason": "服务暂不可用"}
            self.events.put(("ai", generation, session_id, process, title, result, epoch))
        threading.Thread(target=work, name="ironwill-ai", daemon=True).start()

    def _review_identity(self, process, title):
        from .ai import AIClient
        endpoint = getattr(self.ai, "endpoint", None)
        model = getattr(self.ai, "model", None)
        return review_identity(str(self.engine.state.get("goal", "")), process, title, self.policy,
                               endpoint if isinstance(endpoint, str) else self.settings.get("ai_endpoint", ""),
                               model if isinstance(model, str) else self.settings.get("ai_model", ""),
                               AIClient.REVISION, self.engine.state.get("id", ""))

    def _drain_results(self) -> None:
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                return
            if item[0] == "ai_audit":
                _, generation, session_id, epoch, purpose, audit_event = item
                if audit_event["event"] == "ai_request":
                    self.ai_api_count += 1
                if epoch == self._log_epoch:
                    details = {**audit_event.get("details", {}), "purpose": purpose,
                               "session_id": session_id, "generation": generation,
                               "context_current": generation == self.ai_generation and session_id == self.engine.state.get("id")}
                    self._log("AI通信", audit_event["event"], audit_event["summary"], details)
            elif item[0] == "ai":
                _, generation, session_id, process, title, result = item[:6]
                current = (generation == self.ai_generation and session_id == self.engine.state.get("id")
                           and self.engine.state.get("status") == "active" and bool(self.ai))
                if len(item) == 6 or item[6] == self._log_epoch:
                    details = {"session_id": session_id, "generation": generation, "accepted": current,
                               "result": result}
                    if classify(process, title, self.policy) != "PRIVATE":
                        details.update(process=process[:260], title=title[:1500])
                    else:
                        details["result"] = "[当前隐私规则已屏蔽窗口与判断正文]"
                    self._log("判断", "ai_verdict", ("AI 审核结果已接收，仅供匹配窗口使用" if result.get("available") else
                              "AI 审核失败：未取得有效判断，不计为偏离") if current else
                              "AI 结果已过期：周期、规则或设置已变化，未采用", details)
                if generation != self.ai_generation:
                    continue
                self.ai_pending = False
                if session_id != self.engine.state.get("id") or self.engine.state.get("status") != "active" or not self.ai:
                    continue
                verdict = result.get("verdict", "unknown")
                if verdict not in {"related", "neutral", "unrelated", "unknown"}:
                    verdict = "unknown"
                self.semantic_result = (process, title, verdict, time.monotonic())
                available = bool(result.get("available", False))
                reason = str(result.get("reason", "未提供原因。"))[:500]
                identity = self._review_identity(process, title)
                if available:
                    self.review_memory.remember(identity, session_id, {**result, "verdict": verdict, "reason": reason})
                    self.ai_failures.pop(identity, None)
                else:
                    failures = self.ai_failures.get(identity, (0, 0))[0] + 1
                    self.ai_failures[identity] = (failures, time.monotonic() + min(300, 30 * 2 ** min(failures - 1, 4)))
                    if len(self.ai_failures) > 512:
                        self.ai_failures.pop(next(iter(self.ai_failures)))
                self.ai_completed_count += int(available)
                self.last_ai_details = {"generation": generation, "session_id": session_id,
                                        "process": process, "title": title, "verdict": verdict,
                                        "timestamp": time.monotonic(), "reason": reason,
                                        "cached": bool(result.get("cached", False)), "available": available}
                label = ({"related": "最近标题审核：1 核心相关。", "neutral": "最近标题审核：2 辅助/中立，保持安静。",
                          "unrelated": "最近标题审核：0 明确无关，仅作提醒。",
                          "unknown": "最近标题审核：信息不足，未作结论。"}[verdict] if available else "最近标题审核未完成。")
                self.ai_status.set(label + "分类说明/诊断：" + reason)
            elif item[0] == "ai_connection":
                _, test_id, generation, result = item[:4]
                if test_id != self.connection_test_id:
                    continue
                self.connection_test_id = None
                self.connection_test_button.configure(state="normal")
                if generation != self.ai_generation or not self.ai:
                    self.connection_test_text.set("设置或周期已变化，请按当前设置重新测试。")
                    continue
                self.connection_test_text.set("连接成功：接口返回了单字符分类0/1/2。测试未改变执行状态。" if result.get("available") else
                                              "连接测试未通过：" + str(result.get("reason", "请检查服务配置。"))[:500])
            elif item[0] == "tray":
                self._tray_event(item[1])
                if self.closing:
                    return
            elif item[0] == "relay_started":
                self.relay_starting = False
                self.relay = item[1]
                self.relay_start_button.configure(state="disabled")
                self.relay_stop_button.configure(state="normal")
                self._publish_state()
            elif item[0] == "relay_error":
                self.relay_starting = False
                self.relay_start_button.configure(state="normal")
                self.relay_status.set("配对服务未能启动。请检查端口、数据目录和 TLS 运行依赖。")

    def start_relay(self) -> None:
        if self.demo:
            self.relay_status.set("安全体验不开放配对端口。")
            return
        if self.relay or self.relay_starting:
            return
        self.relay_starting = True
        self.relay_start_button.configure(state="disabled")
        self.relay_status.set("正在准备本机加密配对服务……")
        directory = self.data_dir
        def work() -> None:
            relay = None
            try:
                from .relay import RelayServer
                relay = RelayServer(directory, host="0.0.0.0", port=8765)
                relay.start()
                with self.relay_start_lock:
                    if self.closing:
                        relay.stop()
                        return
                    self.events.put(("relay_started", relay))
            except Exception:
                if relay:
                    try:
                        relay.stop()
                    except Exception:
                        pass
                self.events.put(("relay_error",))
        threading.Thread(target=work, name="ironwill-pair-start", daemon=True).start()

    def stop_relay(self) -> None:
        relay, self.relay = self.relay, None
        if relay:
            try:
                relay.stop()
            except Exception:
                pass
        self.relay_info = {}
        self.pair_secret_visible = False
        self.address_value.set("启动后显示")
        self.token_value.set("点击“显示口令”后显示")
        self.fingerprint_value.set("启动后显示")
        self.peers_value.set("手机：尚未连接")
        self.show_token_button.configure(text="显示口令")
        self.relay_start_button.configure(state="normal")
        self.relay_stop_button.configure(state="disabled")
        self.relay_status.set("配对服务已停止。")

    def _publish_state(self) -> None:
        if not self.relay:
            return
        state = self.engine.state
        payload = {key: state.get(key) for key in ("status", "id", "goal", "oath", "stage")}
        self.relay.set_desktop_state(payload)

    def _poll_relay(self) -> None:
        if not self.relay:
            return
        try:
            self._publish_state()
            self.relay_info = info = self.relay.get_info()
            urls = info.get("urls") or []
            self.address_value.set("  |  ".join(str(url) for url in urls) or "地址暂不可用")
            self.fingerprint_value.set(str(info.get("fingerprint", "")))
            if self.pair_secret_visible:
                self.token_value.set(str(info.get("token", "")))
            self.relay_status.set("配对服务运行中。手机需同时核对证书指纹和设备口令。" if info.get("running") else "配对服务当前未运行。")
            peers = info.get("peers") or []
            names = []
            for peer in peers:
                if isinstance(peer, dict):
                    name = str(peer.get("device_name") or peer.get("device_id") or "手机")
                    names.append(name + (" · 在线" if peer.get("active") else " · 已连接 / 待刷新"))
                else:
                    names.append(str(peer))
            self.peers_value.set("手机：" + ("、".join(names) if names else "尚未连接"))
            for command in self.relay.drain_commands():
                self._mobile_command(command)
        except Exception:
            self.relay_status.set("手机连接暂不可用；本机监督继续运行。")

    def _mobile_command(self, command: dict[str, Any]) -> None:
        if not isinstance(command, dict):
            return
        expected = command.get("session_id")
        if not expected or expected != self.engine.state.get("id"):
            return
        action = str(command.get("action", "")).upper()
        status = self.engine.state.get("status")
        if action == "PAUSE" and status == "active":
            self.pause_resume()
        elif action == "RESUME" and status == "paused":
            self.pause_resume()
        elif (action in {"BACK", "WORTH", "BREATHE"} and status == "active"
              and self.dialog and self.dialog.winfo_exists() and not self.dialog_preview and not self.dialog_advisory):
            reflection = str(command.get("reflection", ""))[:2000]
            self._choose(action, reflection=reflection)

    def toggle_pair_secret(self) -> None:
        if not self.relay_info.get("token"):
            self.relay_status.set("先启动配对服务，再显示设备口令。")
            return
        self.pair_secret_visible = not self.pair_secret_visible
        self.token_value.set(str(self.relay_info["token"]) if self.pair_secret_visible else "点击“显示口令”后显示")
        self.show_token_button.configure(text="隐藏口令" if self.pair_secret_visible else "显示口令")

    def copy_pairing(self, key: str) -> None:
        if key == "address":
            urls = self.relay_info.get("urls") or []
            value = str(urls[0]) if urls else ""
        else:
            value = str(self.relay_info.get(key, ""))
        if not value:
            self.relay_status.set("配对信息尚未生成。")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(value)
        self.relay_status.set("已复制%s。" % {"address": "连接地址", "token": "设备口令，请只交给要配对的手机", "fingerprint": "证书指纹"}[key])
        if key in self.copy_buttons:
            btn = self.copy_buttons[key]
            btn.configure(text="已复制")
            self.root.after(1500, lambda: (btn.configure(text="复制") if btn.winfo_exists() else None))

    def close(self) -> None:
        if self.closing:
            return
        if not messagebox.askyesno("停止并退出", "关闭 IronWill 将停止本机监督和手机配对服务。未保存的编辑会丢失。\n\n现在停止并退出？", parent=self.root):
            return
        self.closing = True
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
        if self.engine.state.get("status") in {"active", "paused"}:
            self._handle(self.engine.end())
            self._save_state()
        if self.tray:
            self.tray.stop()
        self.ai_generation += 1
        self.ai_key.set("")
        self.runtime_key = None
        self.ai = None
        self._close_reminder()
        if self.relay:
            try:
                self.relay.stop()
            except Exception:
                pass
        # A starter may have placed a completed server in the queue just before close.
        with self.relay_start_lock:
            while True:
                try:
                    item = self.events.get_nowait()
                except queue.Empty:
                    break
                if item[0] == "relay_started":
                    try:
                        item[1].stop()
                    except Exception:
                        pass
        self.root.destroy()


def launch(data_dir: Path, demo: bool = False, auto_start: bool = False) -> int:
    try:
        root = tk.Tk()
    except tk.TclError:
        print("无法打开图形界面。请在有桌面会话且包含 Tk 的 Python 环境运行。")
        return 2
    app = IronWillApp(root, data_dir, demo=demo)
    if auto_start and not demo:
        root.after(300, app.pause_resume if app.engine.state.get("status") == "paused" else app.start)
    root.mainloop()
    return 0


def smoke_test() -> dict[str, Any]:
    """Construct hidden demo widgets without sampling, networking, locks, or personal files."""
    root = tk.Tk()
    root.withdraw()
    with tempfile.TemporaryDirectory(prefix="ironwill-ui-check-") as directory:
        try:
            app = IronWillApp(root, Path(directory), demo=True)
            root.update_idletasks()  # No mainloop and no timers are processed.
            before = app.engine.snapshot()
            app._show_reminder(3, "安全体验提醒", preview=True)
            app.dialog.withdraw()
            app._choose("LOCK", preview=True)
            after = app.engine.snapshot()
            assert before == after, "Preview changed the execution state"
            assert not list(Path(directory).iterdir()), "Demo wrote personal files"
            app._log("程序", "self_check", "安全体验日志（仅内存）")
            app._refresh_logs()
            assert len(app.log_tree.get_children()) == 1, "Log page failed to render"
            return {"ok": True, "pages": 4, "log_page": True, "identity_fields": len(app.text_fields),
                    "rule_lists": len(app.rule_fields), "preview_unchanged": before == after,
                    "monitoring": False, "network": False, "lock": False}
        finally:
            root.destroy()

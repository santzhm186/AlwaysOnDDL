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
from tkinter import filedialog, messagebox, ttk

from .core import Engine, Observation, Policy, classify, load_policy, load_snapshot, save_policy, save_snapshot
from . import platform_windows
from . import __version__
from .credentials import CredentialError, KeyStore, endpoint_identity, environment_key, read_legacy_key, validate_key


BG = "#111820"
PANEL = "#18222e"
FIELD = "#0d141d"
FG = "#e5edf5"
MUTED = "#a5b3c2"
ACCENT = "#8dd9bd"
WARN = "#f1be7a"


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
        self.body = ttk.Frame(self.canvas, padding=(18, 15, 18, 24))
        self.window = self.canvas.create_window((0, 0), window=self.body, anchor="nw")
        self.canvas.configure(yscrollcommand=scrollbar.set)
        self.canvas.grid(row=0, column=0, sticky="nsew")
        scrollbar.grid(row=0, column=1, sticky="ns")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.body.bind("<Configure>", lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind("<Configure>", lambda event: self.canvas.itemconfigure(self.window, width=event.width))
        self.canvas.bind("<MouseWheel>", self._wheel)

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
            allowed = {"goal", "reality", "consequence", "oath", "signature", "ai_endpoint", "ai_model"}
            return {key: item for key, item in value.items() if key in allowed and isinstance(item, str)}
        except Exception:
            self.load_warning = "个人设置无法读取，原文件没有改动。请检查数据目录后再保存。"
            return {}

    def _style(self) -> None:
        self.root.title("IronWill · 桌面 " + __version__ + (" · 安全体验" if self.demo else ""))
        self.root.geometry("1030x820")
        self.root.minsize(820, 650)
        self.root.configure(bg=BG)
        self.root.option_add("*Font", ("Microsoft YaHei UI", 10))
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(".", background=BG, foreground=FG, font=("Microsoft YaHei UI", 10))
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=PANEL)
        style.configure("TLabel", background=BG, foreground=FG)
        style.configure("Muted.TLabel", foreground=MUTED)
        style.configure("Accent.TLabel", foreground=ACCENT)
        style.configure("Warning.TLabel", foreground=WARN)
        style.configure("Title.TLabel", font=("Microsoft YaHei UI", 24, "bold"))
        style.configure("Heading.TLabel", font=("Microsoft YaHei UI", 12, "bold"))
        style.configure("TButton", background=PANEL, foreground=FG, padding=(14, 9), borderwidth=1)
        style.map("TButton", background=[("active", "#2c4254"), ("disabled", BG)], foreground=[("disabled", "#607080")])
        style.configure("Primary.TButton", background="#215a4b", foreground="#effff9")
        style.map("Primary.TButton", background=[("active", "#2c755f"), ("disabled", "#1b302b")])
        style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=7)
        style.map("TEntry", fieldbackground=[("readonly", FIELD)])
        style.configure("TCheckbutton", background=BG, foreground=FG)
        style.map("TCheckbutton", background=[("active", BG)])
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", background=PANEL, foreground=MUTED, padding=(22, 11))
        style.map("TNotebook.Tab", background=[("selected", "#294034")], foreground=[("selected", FG)])
        style.configure("TLabelframe", background=BG, bordercolor="#33414f")
        style.configure("TLabelframe.Label", background=BG, foreground=FG)

    def _build(self) -> None:
        outer = ttk.Frame(self.root, padding=20)
        outer.pack(fill="both", expand=True)
        heading = ttk.Frame(outer)
        heading.pack(fill="x")
        ttk.Label(heading, text="IronWill", style="Title.TLabel").pack(side="left")
        ttk.Label(heading, text="决定由你作出，提醒按约执行。", style="Muted.TLabel").pack(side="left", padx=20)
        self.state_text = tk.StringVar(value="未开始")
        ttk.Label(heading, textvariable=self.state_text, style="Accent.TLabel").pack(side="right")
        self.capability_text = tk.StringVar()
        ttk.Label(outer, textvariable=self.capability_text, style="Muted.TLabel", wraplength=940).pack(fill="x", pady=(9, 16))
        if self.demo:
            ttk.Label(outer, text="安全体验：不读取窗口、不联网、不锁屏、不写入个人配置。", style="Warning.TLabel").pack(fill="x", pady=(0, 10))
        notebook = ttk.Notebook(outer)
        notebook.pack(fill="both", expand=True)
        self.notebook = notebook
        mission, rules, connections = ScrollFrame(notebook), ScrollFrame(notebook), ScrollFrame(notebook)
        notebook.add(mission, text="当前周期")
        notebook.add(rules, text="判定规则")
        notebook.add(connections, text="手机与 AI")
        self._build_mission(mission.body)
        self._build_rules(rules.body)
        self._build_connections(connections.body)
        footer = ttk.Frame(outer)
        footer.pack(fill="x", pady=(12, 0))
        self.status_text = tk.StringVar(value="尚未开始。保存目标后，由你决定何时开始这个周期。")
        ttk.Label(footer, textvariable=self.status_text, style="Muted.TLabel", wraplength=920).pack(anchor="w")
        self._refresh_status()

    def _text(self, parent: tk.Misc, height: int = 3) -> tk.Text:
        return tk.Text(parent, height=height, bg=FIELD, fg=FG, insertbackground=FG,
                       selectbackground="#3d665b", relief="flat", borderwidth=0,
                       padx=10, pady=8, wrap="word", undo=True)

    def _build_mission(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="这一轮，你已经决定做什么", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="写当前主线。程序提醒你回到它，成果由你判断。", style="Muted.TLabel").grid(row=1, column=0, sticky="w", pady=(3, 8))
        goal = self._text(parent, 3)
        goal.grid(row=2, column=0, sticky="ew", pady=(0, 14))
        goal.insert("1.0", self.settings.get("goal", ""))
        self.text_fields["goal"] = goal
        pair = ttk.Frame(parent)
        pair.grid(row=3, column=0, sticky="ew")
        pair.columnconfigure(0, weight=1)
        pair.columnconfigure(1, weight=1)
        for column, (key, title) in enumerate((("reality", "现实 · 你自己的原话"), ("consequence", "后果 · 你自己的原话"))):
            frame = ttk.Frame(pair, padding=(0 if column == 0 else 7, 0, 7 if column == 0 else 0, 0))
            frame.grid(row=0, column=column, sticky="nsew")
            ttk.Label(frame, text=title).pack(anchor="w", pady=(0, 7))
            widget = self._text(frame, 4)
            widget.pack(fill="x")
            widget.insert("1.0", self.settings.get(key, ""))
            self.text_fields[key] = widget
        ttk.Label(parent, text="大愿 · 关于你要成为谁的标准", style="Heading.TLabel").grid(row=4, column=0, sticky="w", pady=(16, 7))
        oath = self._text(parent, 3)
        oath.grid(row=5, column=0, sticky="ew")
        oath.insert("1.0", self.settings.get("oath", ""))
        self.text_fields["oath"] = oath
        sigrow = ttk.Frame(parent)
        sigrow.grid(row=6, column=0, sticky="ew", pady=(12, 0))
        ttk.Label(sigrow, text="署名（可留空）").pack(side="left")
        self.signature = tk.StringVar(value=self.settings.get("signature", ""))
        ttk.Entry(sigrow, textvariable=self.signature, width=24).pack(side="left", padx=10)
        ttk.Button(sigrow, text="保存本页", command=self.save_identity).pack(side="right")
        actions = ttk.Frame(parent)
        actions.grid(row=7, column=0, sticky="ew", pady=(20, 10))
        self.start_button = ttk.Button(actions, text="开始周期", style="Primary.TButton", command=self.start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(actions, text="暂停", command=self.pause_resume)
        self.pause_button.pack(side="left", padx=8)
        self.end_button = ttk.Button(actions, text="结束周期", command=self.end)
        self.end_button.pack(side="left")
        ttk.Button(actions, text="体验提醒", command=self.preview_reminder).pack(side="right")
        ttk.Label(parent, text="没有每日固定钟点。开始、暂停、继续和结束都由你决定；重新打开软件不会自动接续上次监督。",
                  style="Muted.TLabel", wraplength=900).grid(row=8, column=0, sticky="w", pady=5)
        ttk.Label(parent, text="本版保留自毁保险，不实现删除或卸载。体验提醒不会升档，也不会记录惩罚。",
                  style="Muted.TLabel", wraplength=900).grid(row=9, column=0, sticky="w", pady=5)
        diagnosis = ttk.LabelFrame(parent, text="当前判断 · 为什么提醒或保持安静", padding=12)
        diagnosis.grid(row=10, column=0, sticky="ew", pady=(15, 5))
        self.observation_text = tk.StringVar(value="尚未开始采样。")
        ttk.Label(diagnosis, textvariable=self.observation_text, wraplength=850,
                  style="Muted.TLabel").pack(anchor="w", fill="x")

    def _build_rules(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="本地规则先判断，拿不准就放行", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="每行一个关键词。隐私规则优先；浏览器等通用容器不建议整进程标为主线或红线。",
                  style="Muted.TLabel", wraplength=900).grid(row=1, column=0, sticky="w", pady=(5, 14))
        lists = ttk.Frame(parent)
        lists.grid(row=2, column=0, sticky="ew")
        lists.columnconfigure(0, weight=1)
        lists.columnconfigure(1, weight=1)
        definitions = [
            ("privacy_keywords", "隐私关键词 · 不交给 AI"),
            ("blocked_processes", "红线进程 · 例如明确的娱乐应用"),
            ("blocked_titles", "红线标题关键词 · 可留空，未匹配标题由已启用 AI 审核"),
            ("allowed_processes", "主线进程 · 当前工作的专用工具"),
            ("allowed_titles", "主线标题关键词"),
        ]
        values = self.policy.to_dict()
        for index, (key, title) in enumerate(definitions):
            cell = ttk.Frame(lists, padding=(0 if index % 2 == 0 else 8, 0, 8 if index % 2 == 0 else 0, 12))
            cell.grid(row=index // 2, column=index % 2, sticky="nsew")
            ttk.Label(cell, text=title).pack(anchor="w", pady=(0, 6))
            widget = self._text(cell, 3)
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
            column, row = (index % 3) * 2, index // 3
            ttk.Label(timings, text=title).grid(row=row, column=column, sticky="w", padx=(0, 7), pady=5)
            var = tk.StringVar(value=str(values.get(key, 60)))
            self.numeric_fields[key] = var
            ttk.Entry(timings, textvariable=var, width=7).grid(row=row, column=column + 1, sticky="w", padx=(0, 17), pady=5)
        self.lock_enabled = tk.BooleanVar(value=bool(values.get("lock_enabled", False)) and not self.demo)
        ttk.Checkbutton(parent, text="启用锁屏权限：仅第三档中，我明确点击“锁屏”时执行 Windows 锁屏。",
                        variable=self.lock_enabled, command=self._lock_permission_changed).grid(row=4, column=0, sticky="w", pady=(0, 8))
        ttk.Label(parent, text="默认关闭。不会自动锁屏，不拦截系统退出，也不实现自毁。", style="Muted.TLabel").grid(row=5, column=0, sticky="w")
        ttk.Button(parent, text="保存并应用规则", style="Primary.TButton", command=self.save_rules).grid(row=6, column=0, sticky="e", pady=(14, 0))

    def _build_connections(self, parent: ttk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        ttk.Label(parent, text="手机配对 · 同一局域网", style="Heading.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(parent, text="点击启动后才开放本机配对服务。仅在信任的局域网使用；手机将收到当前目标、大愿和周期状态。\n"
                  "两端各自识别本机活动；短时间提醒去重不等于跨设备共享处罚或升档。", style="Muted.TLabel", wraplength=900).grid(row=1, column=0, sticky="w", pady=(6, 12))
        relay_buttons = ttk.Frame(parent)
        relay_buttons.grid(row=2, column=0, sticky="ew", pady=(0, 10))
        self.relay_start_button = ttk.Button(relay_buttons, text="启动手机配对", command=self.start_relay)
        self.relay_start_button.pack(side="left")
        self.relay_stop_button = ttk.Button(relay_buttons, text="停止配对", command=self.stop_relay, state="disabled")
        self.relay_stop_button.pack(side="left", padx=8)
        self.relay_status = tk.StringVar(value="尚未启动；未开放网络端口。")
        ttk.Label(parent, textvariable=self.relay_status, style="Muted.TLabel", wraplength=880).grid(row=3, column=0, sticky="w", pady=(0, 10))
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
            ttk.Button(details, text="复制", command=lambda item=copy_key: self.copy_pairing(item)).grid(row=row, column=2, padx=(8, 0), pady=5)
        self.show_token_button = ttk.Button(parent, text="显示口令", command=self.toggle_pair_secret)
        self.show_token_button.grid(row=5, column=0, sticky="e", pady=(4, 8))
        self.peers_value = tk.StringVar(value="手机：尚未连接")
        ttk.Label(parent, textvariable=self.peers_value, style="Accent.TLabel", wraplength=880).grid(row=6, column=0, sticky="w", pady=(0, 20))
        ttk.Separator(parent).grid(row=7, column=0, sticky="ew", pady=(0, 18))
        ttk.Label(parent, text="AI 灰区判断 · 可留空，离线也可监督", style="Heading.TLabel").grid(row=8, column=0, sticky="w")
        ttk.Label(parent, text="AI 只判断与当前主线的相关性。它不会评价成果、决定升档或执行锁屏。\n"
                  "启用后，只有本地规则拿不准的窗口才发送当前目标、进程名和必要的窗口标题。现实、后果、大愿和署名不发给模型。",
                  style="Muted.TLabel", wraplength=900).grid(row=9, column=0, sticky="w", pady=(6, 12))
        ai_form = ttk.Frame(parent)
        ai_form.grid(row=10, column=0, sticky="ew")
        ai_form.columnconfigure(1, weight=1)
        self.ai_endpoint = tk.StringVar(value=self.settings.get("ai_endpoint", ""))
        self.ai_model = tk.StringVar(value=self.settings.get("ai_model", ""))
        self.ai_key = tk.StringVar()
        for row, (title, var) in enumerate([("兼容服务地址", self.ai_endpoint), ("模型名称", self.ai_model), ("API 密钥", self.ai_key)]):
            ttk.Label(ai_form, text=title).grid(row=row, column=0, sticky="w", padx=(0, 12), pady=5)
            ttk.Entry(ai_form, textvariable=var, show="●" if row == 2 else "").grid(row=row, column=1, sticky="ew", pady=5)
        ttk.Label(parent, text="Key 已保存时可以留空，应用设置会自动读取。也支持 IRONWILL_API_KEY；旧 DEEPSEEK_API_KEY 仅用于官方 DeepSeek 地址。",
                  style="Muted.TLabel", wraplength=900).grid(row=11, column=0, sticky="w", pady=(8, 8))
        self.ai_enabled = tk.BooleanVar(value=False)
        ttk.Checkbutton(parent, text="本次运行启用 AI，并同意向所填服务发送上述最少信息", variable=self.ai_enabled,
                        command=self._ai_permission_changed).grid(row=12, column=0, sticky="w", pady=7)
        self.ai_status = tk.StringVar(value="AI 未启用，按本地规则工作。")
        ttk.Label(parent, textvariable=self.ai_status, style="Muted.TLabel", wraplength=880).grid(row=13, column=0, sticky="w", pady=7)
        ttk.Button(parent, text="应用 AI 设置", command=self.apply_ai).grid(row=14, column=0, sticky="e")
        self.connection_test_button = ttk.Button(parent, text="用虚拟数据测试 AI 连接", command=self.test_ai_connection)
        self.connection_test_button.grid(row=15, column=0, sticky="e", pady=(10, 0))
        self.connection_test_text = tk.StringVar(value="测试会发出一次虚拟请求，不发送你的任务或真实窗口标题。")
        ttk.Label(parent, textvariable=self.connection_test_text, style="Muted.TLabel",
                  wraplength=880).grid(row=16, column=0, sticky="w", pady=(7, 0))
        self.remember_api_key = tk.BooleanVar(value=not self.demo and self.key_store.exists())
        ttk.Checkbutton(parent, text="在本机记住 API Key（使用 Windows 账户加密，应用设置后生效）",
                        variable=self.remember_api_key).grid(row=17, column=0, sticky="w", pady=(14, 5))
        self.key_storage_text = tk.StringVar(value=("已有加密 Key；应用同一服务时自动读取，不在输入框回显。" if self.remember_api_key.get()
                                                   else "尚未记住 Key。勾选后应用设置，下次无需再次填写。"))
        ttk.Label(parent, textvariable=self.key_storage_text, style="Muted.TLabel",
                  wraplength=880).grid(row=18, column=0, sticky="w", pady=(0, 6))
        key_buttons = ttk.Frame(parent)
        key_buttons.grid(row=19, column=0, sticky="e")
        ttk.Button(key_buttons, text="从旧版 DeepSeek 文件导入", command=self.import_legacy_key).pack(side="left", padx=(0, 8))
        ttk.Button(key_buttons, text="删除已保存 Key", command=self.forget_saved_key).pack(side="left")

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
            now = time.monotonic()
            elapsed, self.last_tick = now - self.last_tick, now
            if not self.demo and self.engine.state.get("status") == "active":
                observation = platform_windows.sample_foreground()
                self.last_observation = observation
                if self.semantic_result:
                    process, title, verdict, timestamp = self.semantic_result
                    if process == observation.process and title == observation.title and now - timestamp < 60:
                        observation = replace(observation, semantic=verdict)
                events = self.engine.tick(observation, elapsed)
                self._handle(events)
                if classify(observation.process, observation.title, self.policy) == "GRAY":
                    self._request_ai(observation)
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
            self._refresh_status()
        self.timer_id = self.root.after(1000, self._poll)

    def _handle(self, events: list[dict[str, Any]] | None) -> None:
        for event in events or []:
            kind = event.get("type")
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
        ttk.Label(content, text="体验提醒" if preview else ("语义提醒 · 由你判断" if advisory else "回到你的决定"), style="Heading.TLabel").pack(anchor="w")
        line = message if message.strip() else ("这是第三次提醒。回到主线。" if stage >= 3 else "你已经决定了这一轮的主线。现在回去。")
        goal = self._identity()["goal"] if preview else self.engine.state.get("goal", "")
        body = self._text(content, 10)
        body.insert("1.0", line + "\n\n当前主线：\n" + str(goal))
        if preview and stage >= 3 and self._identity()["oath"]:
            body.insert("end", "\n\n你写下的大愿：\n" + self._identity()["oath"])
        body.configure(state="disabled", font=("Microsoft YaHei UI", 12))
        body.pack(fill="both", expand=True, pady=(16, 12))
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
        if preview:
            ttk.Button(buttons, text="看第三档", command=lambda: self._show_reminder(3, "你写下的决定仍在这里。回到主线。", preview=True)).pack(side="right")
        if advisory:
            ttk.Label(content, text="这里只提供相关性建议，不增加档位，不执行锁屏。", wraplength=550, style="Muted.TLabel").pack(side="bottom", anchor="w", pady=10)
        elif stage >= 3:
            ttk.Label(content, text="第三档只提供返回与显式锁屏。关闭提醒不会自动锁屏。", wraplength=550, style="Muted.TLabel").pack(side="bottom", anchor="w", pady=10)
        elif preview:
            ttk.Label(content, text="本窗口只是体验。任何选择都不会改变当前周期。", wraplength=550, style="Muted.TLabel").pack(side="bottom", anchor="w", pady=10)
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
        self._refresh_observation_status()

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
                          "unrelated": "AI 判断可能偏离，按语义提醒处理（不升档）。",
                          "unknown": "AI 暂无明确的相关性结论。"}
                verdict = labels[details["verdict"]] if details["available"] else "AI 审核未完成，这不代表你在专注。"
                verdict += "\n" + ("最近结果来自缓存。" if details["cached"] else "") + "原因：" + details["reason"]
            else:
                verdict = "正在审核这个标题。" if self.ai_pending else "未匹配标题将交给 AI，等待下一次审核。"
            verdict += f"\n本次运行已收到 {self.ai_completed_count} 次有效审核结果（含缓存）。"
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
        self.connection_test_button.configure(state="disabled")
        self.connection_test_text.set("正在用虚拟数据测试接口，不读取你的窗口……")
        def work() -> None:
            try:
                result = client.check_connection()
            except Exception:
                result = {"available": False, "reason": "模型连接测试未完成，请检查服务配置。"}
            self.events.put(("ai_connection", test_id, generation, result))
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
        self.settings.update({"ai_endpoint": endpoint, "ai_model": model})
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
        self.ai_key.set("")
        self.ai = client
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
            self._refresh_status()

    def _request_ai(self, observation: Observation) -> None:
        now = time.monotonic()
        if not self.ai or self.ai_pending or now - self.ai_last_request < 10:
            return
        if (observation.status != "ok" or observation.locked or observation.idle_seconds is None
                or not isinstance(observation.idle_seconds, (int, float))
                or not math.isfinite(observation.idle_seconds) or observation.idle_seconds < 0
                or observation.idle_seconds >= self.policy.idle_threshold_seconds
                or classify(observation.process, observation.title, self.policy) != "GRAY"):
            return
        # AIClient also enforces privacy policy. Do not include reality, consequence, oath, or signature.
        generation, client = self.ai_generation, self.ai
        session_id = self.engine.state.get("id")
        goal, process, title = str(self.engine.state.get("goal", "")), observation.process, observation.title
        policy = self.policy
        self.ai_pending, self.ai_last_request = True, now
        self.ai_status.set("正在审核未匹配标题，无需填写红线关键词……")
        def work() -> None:
            try:
                result = client.advise(goal, process, title, policy=policy)
            except Exception:
                result = {"verdict": "unknown", "reason": "服务暂不可用"}
            self.events.put(("ai", generation, session_id, process, title, result))
        threading.Thread(target=work, name="ironwill-ai", daemon=True).start()

    def _drain_results(self) -> None:
        while True:
            try:
                item = self.events.get_nowait()
            except queue.Empty:
                return
            if item[0] == "ai":
                _, generation, session_id, process, title, result = item
                if generation != self.ai_generation:
                    continue
                self.ai_pending = False
                if session_id != self.engine.state.get("id") or self.engine.state.get("status") != "active" or not self.ai:
                    continue
                verdict = result.get("verdict", "unknown")
                if verdict not in {"related", "unrelated", "unknown"}:
                    verdict = "unknown"
                self.semantic_result = (process, title, verdict, time.monotonic())
                available = bool(result.get("available", False))
                reason = str(result.get("reason", "未提供原因。"))[:500]
                self.ai_completed_count += int(available)
                self.last_ai_details = {"generation": generation, "session_id": session_id,
                                        "process": process, "title": title, "verdict": verdict,
                                        "timestamp": time.monotonic(), "reason": reason,
                                        "cached": bool(result.get("cached", False)), "available": available}
                label = ({"related": "最近标题审核：与主线相关。", "unrelated": "最近标题审核：可能偏离，仅作提醒。",
                          "unknown": "最近标题审核：信息不足，未作结论。"}[verdict] if available else "最近标题审核未完成。")
                self.ai_status.set(label + "原因：" + reason)
            elif item[0] == "ai_connection":
                _, test_id, generation, result = item
                if test_id != self.connection_test_id:
                    continue
                self.connection_test_id = None
                self.connection_test_button.configure(state="normal")
                if generation != self.ai_generation or not self.ai:
                    self.connection_test_text.set("设置或周期已变化，请按当前设置重新测试。")
                    continue
                self.connection_test_text.set("连接成功：接口返回了符合约定的 JSON。测试未改变执行状态。" if result.get("available") else
                                              "连接测试未通过：" + str(result.get("reason", "请检查服务配置。"))[:500])
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

    def close(self) -> None:
        if self.closing:
            return
        if not messagebox.askyesno("停止并退出", "关闭 IronWill 将停止本机监督和手机配对服务。未保存的编辑会丢失。\n\n现在停止并退出？", parent=self.root):
            return
        self.closing = True
        if self.timer_id:
            self.root.after_cancel(self.timer_id)
        if self.engine.state.get("status") in {"active", "paused"}:
            self.engine.end()
            self._save_state()
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
            return {"ok": True, "pages": 3, "identity_fields": len(app.text_fields),
                    "rule_lists": len(app.rule_fields), "preview_unchanged": before == after,
                    "monitoring": False, "network": False, "lock": False}
        finally:
            root.destroy()

# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Native Tk checklist views and a draggable floating launcher. No OS hooks."""
import tkinter as tk
from tkinter import font as tkfont, ttk

BG, PANEL, FG, MUTED, ACCENT, LINE = "#131716", "#1c2320", "#edf0e7", "#a8b4aa", "#d7f06c", "#354139"


class TodoListView(ttk.Frame):
    def __init__(self, parent, on_toggle, on_edit=None, on_remove=None):
        super().__init__(parent, style="Card.TFrame")
        self.on_toggle, self.on_edit, self.on_remove = on_toggle, on_edit, on_remove
        self.rows = {}
        self.normal_font = tkfont.Font(self, family="Microsoft YaHei UI", size=10)
        self.done_font = tkfont.Font(self, family="Microsoft YaHei UI", size=10, overstrike=True)
        self.empty = ttk.Label(self, text="还没有短期待办。在周期页每行输入一项，再点击添加。",
                               style="Card.Muted.TLabel", wraplength=280)

    def set_items(self, items):
        identifiers = {item["id"] for item in items}
        for identifier in list(self.rows):
            if identifier not in identifiers:
                self.rows.pop(identifier)["frame"].destroy()
        if not items:
            self.empty.pack(fill="x", pady=12)
        else:
            self.empty.pack_forget()
        for item in items:
            identifier = item["id"]
            if identifier not in self.rows:
                frame = ttk.Frame(self, style="Card.TFrame", padding=(0, 7))
                frame.columnconfigure(1, weight=1)
                value = tk.BooleanVar(master=self, value=item["done"])
                check = ttk.Checkbutton(frame, style="Card.TCheckbutton", variable=value,
                    command=lambda key=identifier, var=value: self.on_toggle(key, bool(var.get())))
                check.grid(row=0, column=0, sticky="n", padx=(0, 8), pady=2)
                label = ttk.Label(frame, wraplength=240, style="Card.TLabel", cursor="hand2")
                label.grid(row=0, column=1, sticky="ew")
                label.bind("<Button-1>", lambda _event, key=identifier, var=value: self.on_toggle(key, not var.get()))
                controls = ttk.Frame(frame, style="Card.TFrame")
                controls.grid(row=0, column=2, sticky="ne", padx=(8, 0))
                if self.on_edit:
                    ttk.Button(controls, text="编辑", style="Small.TButton",
                               command=lambda key=identifier: self.on_edit(key)).pack(side="left", padx=(0, 6))
                if self.on_remove:
                    ttk.Button(controls, text="移除", style="Small.TButton",
                               command=lambda key=identifier: self.on_remove(key)).pack(side="left")
                label.bind("<Configure>", lambda event, widget=label: widget.configure(wraplength=max(100, event.width)))
                check.bind("<FocusIn>", lambda _event, w=label: w.configure(foreground=ACCENT))
                check.bind("<FocusOut>", lambda _event, w=label: w.configure(foreground=""))
                self.rows[identifier] = {"frame": frame, "value": value, "label": label, "check": check}
            row = self.rows[identifier]
            row["value"].set(item["done"])
            row["label"].configure(text=item["text"], font=self.done_font if item["done"] else self.normal_font,
                                   style="Card.Muted.TLabel" if item["done"] else "Card.TLabel")
            row["check"].configure(takefocus=True)
            # Repacking an existing row preserves its variable, focus and checkbox.
            row["frame"].pack(fill="x")


class FloatingChecklist:
    def __init__(self, root, on_toggle, on_open, on_hide, on_move, position=None):
        self.root = root
        self.on_toggle, self.on_open, self.on_hide, self.on_move = on_toggle, on_open, on_hide, on_move
        self.position = position
        self.icon = self.popup = self.view = None
        self.items = []
        self.drag = None
        self.progress = tk.StringVar(master=root)

    def _bounds(self):
        return (self.root.winfo_vrootx(), self.root.winfo_vrooty(),
                max(320, self.root.winfo_vrootwidth()), max(300, self.root.winfo_vrootheight()))

    def _clamp(self, x, y, width=48, height=48):
        left, top, wide, high = self._bounds()
        return (max(left, min(int(x), left + wide - width)),
                max(top, min(int(y), top + high - height - 48)))

    def show(self):
        if self.icon and self.icon.winfo_exists():
            self.icon.deiconify()
            return
        icon = self.icon = tk.Toplevel(self.root)
        icon.withdraw()
        icon.title("IronWill · 悬浮清单")
        icon.overrideredirect(True)
        icon.attributes("-topmost", True)
        icon.configure(bg=BG)
        try:
            icon.attributes("-toolwindow", True)
            icon.attributes("-transparentcolor", "#ff00ff")
            background = "#ff00ff"
        except tk.TclError:
            background = BG
        self.canvas = tk.Canvas(icon, width=48, height=48, bg=background, highlightthickness=0, takefocus=True)
        self.canvas.pack()
        self.oval = self.canvas.create_oval(2, 2, 46, 46, fill=ACCENT, outline=LINE)
        self.canvas.bind("<FocusIn>", lambda _event: self.canvas.itemconfigure(self.oval, outline=BG, width=3))
        self.canvas.bind("<FocusOut>", lambda _event: self.canvas.itemconfigure(self.oval, outline=LINE, width=1))
        for y in (15, 23, 31):
            self.canvas.create_rectangle(12, y - 2, 16, y + 2, fill=BG, outline="")
            self.canvas.create_line(21, y, 35, y, fill=BG, width=2)
        self.badge = self.canvas.create_text(37, 39, fill=BG, font=("Segoe UI", 8, "bold"))
        left, top, width, height = self._bounds()
        x, y = self._clamp(*(self.position or (left + width - 76, top + height - 150)))
        icon.geometry(f"48x48{x:+d}{y:+d}")
        self.canvas.bind("<ButtonPress-1>", self._press)
        self.canvas.bind("<B1-Motion>", self._motion)
        self.canvas.bind("<ButtonRelease-1>", self._release)
        self.canvas.bind("<Return>", lambda _event: self.toggle_popup())
        self.canvas.bind("<space>", lambda _event: self.toggle_popup())
        self.canvas.bind("<Escape>", lambda _event: self.hide_popup())
        menu = tk.Menu(icon, tearoff=False)
        menu.add_command(label="打开 / 收起清单", command=self.toggle_popup)
        menu.add_command(label="打开周期页", command=self.on_open)
        menu.add_separator()
        menu.add_command(label="隐藏悬浮图标", command=self.on_hide)
        def context(event):
            try:
                menu.tk_popup(event.x_root, event.y_root)
            finally:
                menu.grab_release()
        self.canvas.bind("<Button-3>", context)
        icon.protocol("WM_DELETE_WINDOW", self.on_hide)
        icon.deiconify()
        self.set_items(self.items)

    def _press(self, event):
        self.drag = (event.x_root, event.y_root, self.icon.winfo_x(), self.icon.winfo_y(), False)

    def _motion(self, event):
        if not self.drag:
            return
        sx, sy, x, y, moved = self.drag
        if not moved and abs(event.x_root - sx) + abs(event.y_root - sy) < 6:
            return
        self.drag = (sx, sy, x, y, True)
        nx, ny = self._clamp(x + event.x_root - sx, y + event.y_root - sy)
        self.icon.geometry(f"48x48{nx:+d}{ny:+d}")
        self.hide_popup()

    def _release(self, _event):
        drag, self.drag = self.drag, None
        if drag and drag[4]:
            self.position = (self.icon.winfo_x(), self.icon.winfo_y())
            self.on_move(self.position)
        elif drag:
            self.toggle_popup()

    def toggle_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.hide_popup()
            return
        self.show()
        popup = self.popup = tk.Toplevel(self.root)
        popup.withdraw()
        popup.title("IronWill · 短期待办")
        popup.overrideredirect(True)
        popup.attributes("-topmost", True)
        try:
            popup.attributes("-toolwindow", True)
        except tk.TclError:
            pass
        popup.configure(bg=LINE)
        outer = ttk.Frame(popup, style="Card.TFrame", padding=16)
        outer.pack(fill="both", expand=True, padx=1, pady=1)
        header = ttk.Frame(outer, style="Card.TFrame")
        header.pack(fill="x", pady=(0, 8))
        ttk.Label(header, text="短期待办", style="Card.Heading.TLabel").pack(side="left")
        ttk.Button(header, text="收起", style="Small.TButton", command=self.hide_popup).pack(side="right")
        ttk.Label(outer, textvariable=self.progress, style="Card.Accent.TLabel").pack(anchor="w", pady=(0, 12))
        footer = ttk.Frame(outer, style="Card.TFrame")
        footer.pack(side="bottom", fill="x", pady=(12, 0))
        ttk.Button(footer, text="打开周期页 · 编辑清单", command=self.on_open).pack(fill="x")
        body = ttk.Frame(outer, style="Card.TFrame")
        body.pack(fill="both", expand=True)
        canvas = tk.Canvas(body, bg=PANEL, highlightthickness=0)
        scroll = ttk.Scrollbar(body, orient="vertical", command=canvas.yview)
        scroll.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)
        canvas.configure(yscrollcommand=scroll.set)
        self.view = TodoListView(canvas, self.on_toggle)
        window = canvas.create_window((0, 0), window=self.view, anchor="nw")
        self.view.bind("<Configure>", lambda _event: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))
        # A toplevel binding also reaches dynamically inserted rows; never bind_all.
        popup.bind("<MouseWheel>", lambda event: canvas.yview_scroll(int(-event.delta / 120), "units"))
        popup.bind("<Escape>", lambda _event: self.hide_popup())
        popup.protocol("WM_DELETE_WINDOW", self.hide_popup)
        self.set_items(self.items)
        left, top, wide, high = self._bounds()
        width, height = min(380, wide), min(480, high - 80)
        x, y = self._clamp(self.icon.winfo_x() - width + 48, self.icon.winfo_y() - height - 8, width, height)
        popup.geometry(f"{width}x{height}{x:+d}{y:+d}")
        popup.deiconify()
        popup.lift()

    def set_items(self, items):
        self.items = [dict(item) for item in items]
        done = sum(item["done"] for item in items)
        self.progress.set(f"已完成 {done} / {len(items)} · 完成状态由你确认")
        if self.icon and self.icon.winfo_exists():
            pending = len(items) - done
            self.canvas.itemconfigure(self.badge, text=str(pending) if pending else ("✓" if items else "+"))
        if self.popup and self.popup.winfo_exists() and self.view:
            self.view.set_items(items)

    def hide_popup(self):
        if self.popup and self.popup.winfo_exists():
            self.popup.destroy()
        self.popup = self.view = None

    def close(self):
        self.hide_popup()
        if self.icon and self.icon.winfo_exists():
            self.icon.destroy()
        self.icon = None

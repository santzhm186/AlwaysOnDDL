## 2026-10-11 - Tkinter Notebook Keyboard Traversal
**Learning:** By default, Tkinter's `ttk.Notebook` does not support keyboard traversal between tabs (e.g., using `Ctrl+Tab`). This is a significant accessibility issue for keyboard-only users.
**Action:** Always call `.enable_traversal()` on `ttk.Notebook` instances to ensure keyboard accessibility.
## 2026-10-12 - Tkinter destructive actions missing confirmation dialogues
**Learning:** Destructive actions such as deleting saved keys or clearing caches did not have confirmation dialogs.
**Action:** Add `messagebox.askyesno` before taking destructive actions.
## 2026-10-13 - Tkinter tk.Text widgets trap focus by default
**Learning:** By default, Tkinter's `tk.Text` widget handles `<Tab>` by inserting a tab character rather than yielding focus. This prevents keyboard-only users from navigating beyond the text widget.
**Action:** When instantiating `tk.Text` widgets, bind `<Tab>` and `<Shift-Tab>` to `tk_focusNext().focus_set()` and `tk_focusPrev().focus_set()` respectively, returning 'break' to avoid default tab behavior and allow normal focus navigation.
## 2026-10-13 - Tkinter Shift-Tab cross-platform binding
**Learning:** On Linux/X11, Shift+Tab is often registered as `<ISO_Left_Tab>` rather than `<Shift-Tab>`.
**Action:** When binding Shift+Tab behavior in Tkinter, also bind `<ISO_Left_Tab>` alongside `<Shift-Tab>` for full cross-platform support.
## 2026-10-15 - Explicit Focus Indicators for Tkinter
**Learning:** Default Tkinter focus rings (e.g. for `ttk.Checkbutton` and `tk.Canvas`) can be missing or indistinct, particularly in custom-themed applications, reducing accessibility for keyboard users.
**Action:** Enhance keyboard accessibility by manually adding `<FocusIn>` and `<FocusOut>` event bindings to these widgets (and adjacent text labels) to change their color/style explicitly on focus.

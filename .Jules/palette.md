## 2026-10-11 - Tkinter Notebook Keyboard Traversal
**Learning:** By default, Tkinter's `ttk.Notebook` does not support keyboard traversal between tabs (e.g., using `Ctrl+Tab`). This is a significant accessibility issue for keyboard-only users.
**Action:** Always call `.enable_traversal()` on `ttk.Notebook` instances to ensure keyboard accessibility.
## 2026-10-12 - Tkinter destructive actions missing confirmation dialogues
**Learning:** Destructive actions such as deleting saved keys or clearing caches did not have confirmation dialogs.
**Action:** Add `messagebox.askyesno` before taking destructive actions.
## 2026-10-13 - Tkinter Text Widget Focus Trapping
**Learning:** By default, Tkinter's `tk.Text` widget traps keyboard focus because it maps `<Tab>` to inserting a tab character. This completely breaks keyboard accessibility for forms containing text areas.
**Action:** Always bind `<Tab>` and `<Shift-Tab>` to `tk_focusNext().focus_set()` and `tk_focusPrev().focus_set()` respectively, and return `"break"` to stop default tab insertion on `tk.Text` widgets meant for data entry in forms.

## 2026-10-11 - Tkinter Notebook Keyboard Traversal
**Learning:** By default, Tkinter's `ttk.Notebook` does not support keyboard traversal between tabs (e.g., using `Ctrl+Tab`). This is a significant accessibility issue for keyboard-only users.
**Action:** Always call `.enable_traversal()` on `ttk.Notebook` instances to ensure keyboard accessibility.
## 2026-10-12 - Tkinter destructive actions missing confirmation dialogues
**Learning:** Destructive actions such as deleting saved keys or clearing caches did not have confirmation dialogs.
**Action:** Add `messagebox.askyesno` before taking destructive actions.
## 2024-10-18 - Fix Tkinter Text Focus Trapping
**Learning:** `tk.Text` widgets trap focus by default when hitting `<Tab>`, inserting a tab character rather than moving to the next interactive element. This breaks keyboard accessibility patterns expected in standard desktop apps.
**Action:** Always bind `<Tab>` and `<Shift-Tab>` to `tk_focusNext().focus_set()` and `tk_focusPrev().focus_set()` and return "break" on custom `tk.Text` builders or wrappers to ensure standard form navigation.

## 2026-10-11 - Tkinter Notebook Keyboard Traversal
**Learning:** By default, Tkinter's `ttk.Notebook` does not support keyboard traversal between tabs (e.g., using `Ctrl+Tab`). This is a significant accessibility issue for keyboard-only users.
**Action:** Always call `.enable_traversal()` on `ttk.Notebook` instances to ensure keyboard accessibility.
## 2026-10-12 - Tkinter destructive actions missing confirmation dialogues
**Learning:** Destructive actions such as deleting saved keys or clearing caches did not have confirmation dialogs.
**Action:** Add `messagebox.askyesno` before taking destructive actions.
## 2026-10-21 - tk.Text Focus Trapping
**Learning:** By default, Tkinter's `tk.Text` widget traps `<Tab>` focus, preventing keyboard navigation to other UI elements. This breaks standard keyboard accessibility.
**Action:** Bind `<Tab>` and `<Shift-Tab>` to `tk_focusNext().focus_set()` and `tk_focusPrev().focus_set()` respectively, returning 'break', to ensure keyboard accessibility.

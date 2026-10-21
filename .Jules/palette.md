## 2026-10-11 - Tkinter Notebook Keyboard Traversal
**Learning:** By default, Tkinter's `ttk.Notebook` does not support keyboard traversal between tabs (e.g., using `Ctrl+Tab`). This is a significant accessibility issue for keyboard-only users.
**Action:** Always call `.enable_traversal()` on `ttk.Notebook` instances to ensure keyboard accessibility.
## 2026-10-12 - Tkinter destructive actions missing confirmation dialogues
**Learning:** Destructive actions such as deleting saved keys or clearing caches did not have confirmation dialogs.
**Action:** Add `messagebox.askyesno` before taking destructive actions.
## 2026-10-18 - Tkinter Text Widget Keyboard Trap
**Learning:** By default, Tkinter's `tk.Text` widget traps the `<Tab>` key, inserting a tab character rather than moving focus to the next widget. This creates a severe accessibility barrier for keyboard-only navigation.
**Action:** Always bind `<Tab>` and `<Shift-Tab>` to `tk_focusNext().focus_set()` and `tk_focusPrev().focus_set()` respectively for form-oriented `tk.Text` fields, unless a literal tab character is necessary for the field's semantics.

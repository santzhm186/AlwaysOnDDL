## 2026-10-11 - Tkinter Notebook Keyboard Traversal
**Learning:** By default, Tkinter's `ttk.Notebook` does not support keyboard traversal between tabs (e.g., using `Ctrl+Tab`). This is a significant accessibility issue for keyboard-only users.
**Action:** Always call `.enable_traversal()` on `ttk.Notebook` instances to ensure keyboard accessibility.
## 2026-10-12 - Tkinter destructive actions missing confirmation dialogues
**Learning:** Destructive actions such as deleting saved keys or clearing caches did not have confirmation dialogs.
**Action:** Add `messagebox.askyesno` before taking destructive actions.
## 2026-10-13 - tk.Text Traps Focus
**Learning:** By default, Tkinter's `tk.Text` widget traps tab keystrokes, which prevents users from navigating away from the text area using the keyboard.
**Action:** Bind `<Tab>` to `tk_focusNext().focus_set()` and `<Shift-Tab>` to `tk_focusPrev().focus_set()` and return `"break"` to allow keyboard users to tab out of the text field.

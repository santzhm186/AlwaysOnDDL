## 2026-10-11 - Tkinter Notebook Keyboard Traversal
**Learning:** By default, Tkinter's `ttk.Notebook` does not support keyboard traversal between tabs (e.g., using `Ctrl+Tab`). This is a significant accessibility issue for keyboard-only users.
**Action:** Always call `.enable_traversal()` on `ttk.Notebook` instances to ensure keyboard accessibility.
## 2026-10-12 - Tkinter destructive actions missing confirmation dialogues
**Learning:** Destructive actions such as deleting saved keys or clearing caches did not have confirmation dialogs.
**Action:** Add `messagebox.askyesno` before taking destructive actions.
## 2024-05-13 - Tkinter Text Widget Accessibility
**Learning:** By default, Tkinter `Text` widgets insert a literal tab character on `<Tab>` and trap keyboard focus, severely hindering accessibility for users navigating via keyboard. Linux X11 systems handle `<Shift-Tab>` via the `<ISO_Left_Tab>` sequence.
**Action:** Always bind `<Tab>`, `<Shift-Tab>`, and `<ISO_Left_Tab>` to `event.widget.tk_focusNext().focus()` (and `tk_focusPrev()`) while returning `"break"` to allow keyboard users to tab out of multi-line text input fields in desktop Tkinter applications.

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
## 2026-10-14 - Keyboard focus indicators for checklists
**Learning:** In list views where labels describe interactive elements like checkboxes, screen reader users and keyboard navigators benefit from visual focus indicators specifically on the descriptive label when the interactive element receives focus. Using `check.bind("<FocusIn>")` and `<FocusOut>` to change label foreground colors makes keyboard navigation much clearer.
**Action:** Add `<FocusIn>` and `<FocusOut>` bindings to interactive Tkinter elements to provide clear visual feedback on adjacent labels during keyboard navigation.

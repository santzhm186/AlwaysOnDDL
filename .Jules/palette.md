## 2026-10-27 - Tkinter Text Widget Focus Traps
**Learning:** By default, Tkinter `tk.Text` widgets trap keyboard focus because pressing `<Tab>` inserts a literal tab character instead of advancing focus to the next widget. This severely breaks keyboard navigation and accessibility in desktop apps.
**Action:** Always intercept `<Tab>`, `<Shift-Tab>`, and `<ISO_Left_Tab>` on `tk.Text` widgets, using `event.widget.tk_focusNext().focus()` (or `tk_focusPrev()`) and returning `"break"` to prevent default character insertion, ensuring a seamless tab traversal experience.

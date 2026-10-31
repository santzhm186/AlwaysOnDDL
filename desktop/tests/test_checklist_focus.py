# Copyright (C) 2026 IronWill contributors
# SPDX-License-Identifier: GPL-3.0-only
"""Real Tk focus/state regressions; requires a desktop session."""
import tkinter as tk
from tkinter import ttk
import unittest

from ironwill.checklist import ACCENT, BG, FG, LINE, MUTED, PANEL, FloatingChecklist, TodoListView


def contrast(a, b):
    def luminance(color):
        channels = [int(color[i:i+2], 16) / 255 for i in (1, 3, 5)]
        return sum(weight * (v / 12.92 if v <= .04045 else ((v + .055) / 1.055) ** 2.4)
                   for weight, v in zip((.2126, .7152, .0722), channels))
    light, dark = sorted((luminance(a), luminance(b)), reverse=True)
    return (light + .05) / (dark + .05)


class FocusTests(unittest.TestCase):
    def setUp(self):
        self.root = tk.Tk()
        self.addCleanup(self.root.destroy)
        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('Card.TLabel', foreground=FG, background=PANEL)
        style.configure('Card.Muted.TLabel', foreground=MUTED, background=PANEL)
        self.items = [dict(id='a', text='Task', done=False), dict(id='b', text='Other', done=True)]
        self.view = TodoListView(self.root, self.toggle)
        self.view.pack()
        self.view.set_items(self.items)
        self.other = ttk.Button(self.root, text='Next')
        self.other.pack()
        self.root.update()

    def toggle(self, key, done):
        next(item for item in self.items if item['id'] == key)['done'] = done
        self.view.set_items(self.items)

    def focus(self, widget):
        widget.focus_force()
        self.root.update()
        self.assertEqual(self.root.focus_get(), widget)

    def color(self, label):
        return str(label.cget('foreground')) or ttk.Style(self.root).lookup(label.cget('style'), 'foreground')

    def test_keyboard_toggle_refresh_and_label_click_restore_colors(self):
        row = self.view.rows['a']
        self.focus(row['check'])
        self.assertEqual(self.color(row['label']), ACCENT)
        row['check'].event_generate('<KeyPress-space>')
        row['check'].event_generate('<KeyRelease-space>')
        self.root.update()
        self.assertTrue(self.items[0]['done'])
        self.assertEqual(self.color(row['label']), ACCENT)
        self.focus(self.other)
        self.assertEqual(self.color(row['label']), MUTED)
        row['label'].event_generate('<Button-1>')
        self.root.update()
        self.assertFalse(self.items[0]['done'])
        self.assertEqual(self.color(row['label']), FG)
        self.toggle('a', True)  # Update from another view.
        self.assertEqual(self.color(row['label']), MUTED)
        self.toggle('a', False)
        self.assertEqual(self.color(row['label']), FG)
        self.assertGreater(contrast(ACCENT, PANEL), 4.5)

    def test_tab_and_reverse_tab_move_label_indicator(self):
        first, second = (self.view.rows[k] for k in ('a', 'b'))
        self.focus(first['check'])
        first['check'].event_generate('<Tab>')
        self.root.update()
        self.assertEqual(self.root.focus_get(), second['check'])
        self.assertEqual(self.color(first['label']), FG)
        self.assertEqual(self.color(second['label']), ACCENT)
        second['check'].event_generate('<Shift-Tab>')
        self.root.update()
        self.assertEqual(self.root.focus_get(), first['check'])
        self.assertEqual(self.color(second['label']), MUTED)

    def test_launcher_ring_and_badge_survive_focus_and_refresh(self):
        floating = FloatingChecklist(self.root, self.toggle, lambda: None, lambda: None, lambda p: None)
        self.addCleanup(floating.close)
        floating.show()
        self.root.update()
        self.focus(floating.canvas)
        canvas = floating.canvas
        self.assertEqual(canvas.itemcget(floating.oval, 'outline'), BG)
        self.assertEqual(float(canvas.itemcget(floating.oval, 'width')), 3)
        self.assertGreater(contrast(BG, ACCENT), 3)
        for items, badge in ((self.items, '1'), ([dict(id='a', text='Task', done=True)], '✓'), ([], '+')):
            floating.set_items(items)
            self.assertEqual(canvas.itemcget(floating.badge, 'text'), badge)
            self.assertEqual(canvas.itemcget(floating.badge, 'fill'), BG)
            self.assertEqual(canvas.itemcget(floating.oval, 'fill'), ACCENT)
        self.focus(self.other)
        self.assertEqual(canvas.itemcget(floating.oval, 'outline'), LINE)
        self.assertEqual(float(canvas.itemcget(floating.oval, 'width')), 1)


if __name__ == '__main__':
    unittest.main()

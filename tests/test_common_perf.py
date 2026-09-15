"""Tests for common.perf: CustomTkinter redraw workarounds and StackedTabview.

Run from the repo root:  python -m unittest tests.test_common_perf
"""

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import customtkinter as ctk  # noqa: E402
from customtkinter.windows.widgets.core_widget_classes import CTkBaseClass  # noqa: E402

from tests.tk_teardown import destroy_root  # noqa: E402

from common import perf, widgets  # noqa: E402
from common.perf import StackedTabview  # noqa: E402


def pump(widget, times=6):
    for _ in range(times):
        widget.update()


class CountingButton(ctk.CTkButton):
    """A button that counts its own redraws."""

    def __init__(self, *args, **kwargs):
        self.draws = 0
        super().__init__(*args, **kwargs)

    def _draw(self, no_color_updates=False):
        self.draws += 1
        super()._draw(no_color_updates)


class PatchTests(unittest.TestCase):
    def test_install_is_idempotent_and_marks_the_library(self):
        perf.install()
        first = CTkBaseClass._set_appearance_mode
        perf.install()
        self.assertIs(CTkBaseClass._set_appearance_mode, first)
        self.assertTrue(perf.installed())

    def test_init_styles_installs_patches(self):
        root = ctk.CTk()
        root.withdraw()
        try:
            widgets.init_styles(root)
            self.assertTrue(perf.installed())
        finally:
            destroy_root(root)


class StackedTabviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()
        widgets.init_styles(cls.root)

    @classmethod
    def tearDownClass(cls):
        destroy_root(cls.root)

    def setUp(self):
        self.tabs = StackedTabview(self.root)
        self.tabs.pack(fill="both", expand=True)
        self.a = self.tabs.add("A")
        self.b = self.tabs.add("B")
        self.btn_a = CountingButton(self.a, text="a")
        self.btn_a.pack()
        self.btn_b = CountingButton(self.b, text="b")
        self.btn_b.pack()
        pump(self.root)

    def tearDown(self):
        self.tabs.destroy()
        pump(self.root, 2)

    def test_first_tab_is_current_and_visible(self):
        self.assertEqual(self.tabs.get(), "A")
        self.assertFalse(perf.is_obscured(self.btn_a))
        self.assertTrue(perf.is_obscured(self.btn_b))

    def test_unvisited_tab_is_not_mapped_until_selected(self):
        # Lazy: B has never been shown, so it was never gridded.
        self.assertEqual(self.b.winfo_manager(), "")
        self.tabs.set("B")
        pump(self.root)
        self.assertEqual(self.b.winfo_manager(), "grid")
        self.assertEqual(self.tabs.get(), "B")

    def test_visited_tabs_stay_mapped_and_switch_by_raising(self):
        self.tabs.set("B")
        pump(self.root)
        self.tabs.set("A")
        pump(self.root)
        # Both are still gridded: switching no longer unmaps and remaps.
        self.assertEqual(self.a.winfo_manager(), "grid")
        self.assertEqual(self.b.winfo_manager(), "grid")
        self.assertFalse(perf.is_obscured(self.btn_a))
        self.assertTrue(perf.is_obscured(self.btn_b))

    def test_click_path_matches_set(self):
        self.tabs._segmented_button_callback("B")
        pump(self.root)
        self.assertEqual(self.tabs.get(), "B")
        self.assertEqual(self.b.winfo_manager(), "grid")
        self.assertTrue(perf.is_obscured(self.btn_a))

    def test_appearance_change_defers_redraw_of_hidden_tab(self):
        self.tabs.set("B")
        pump(self.root)
        self.tabs.set("A")
        pump(self.root)
        before_a, before_b = self.btn_a.draws, self.btn_b.draws
        target = "Light" if ctk.get_appearance_mode() == "Dark" else "Dark"
        ctk.set_appearance_mode(target)
        pump(self.root)
        # Visible tab redrew; hidden tab did not, but is queued.
        self.assertGreater(self.btn_a.draws, before_a)
        self.assertEqual(self.btn_b.draws, before_b)
        self.assertTrue(perf.pending_count() >= 1)
        # Raising B flushes its deferred redraws.
        self.tabs.set("B")
        pump(self.root)
        self.assertGreater(self.btn_b.draws, before_b)
        self.assertFalse(any(perf.is_obscured(w) is False and w is self.btn_b for w in perf.pending()))
        ctk.set_appearance_mode("System")
        pump(self.root)

    def test_tab_frames_and_children_keep_colour_pairs(self):
        # Pairs, not resolved strings: children resolve the mode themselves,
        # so no colour cascade is needed on an appearance change.
        self.assertIsInstance(self.a.cget("fg_color"), (tuple, list))
        self.assertIsInstance(self.btn_a.cget("bg_color"), (tuple, list))
        target = "Light" if ctk.get_appearance_mode() == "Dark" else "Dark"
        ctk.set_appearance_mode(target)
        pump(self.root)
        # A child of the hidden tab still shows the tabview's pair as its bg.
        self.assertEqual(tuple(self.btn_b.cget("bg_color")), tuple(self.a.cget("fg_color")))
        ctk.set_appearance_mode("System")
        pump(self.root)

    def test_appearance_switch_unmaps_hidden_tabs_and_they_remap_on_select(self):
        self.tabs.set("B")
        pump(self.root)
        self.tabs.set("A")
        pump(self.root)
        self.assertEqual(self.b.winfo_manager(), "grid")  # visited, stacked underneath
        target = "Light" if ctk.get_appearance_mode() == "Dark" else "Dark"
        perf.set_appearance_mode(target)
        pump(self.root)
        # Hidden tab is off the grid so Tk does not repaint it; visible one stays.
        self.assertEqual(self.b.winfo_manager(), "")
        self.assertEqual(self.a.winfo_manager(), "grid")
        # Selecting it again puts it back and flushes its deferred redraw.
        before = self.btn_b.draws
        self.tabs.set("B")
        pump(self.root)
        self.assertEqual(self.b.winfo_manager(), "grid")
        self.assertGreater(self.btn_b.draws, before)
        perf.set_appearance_mode("System")
        pump(self.root)

    def test_widget_outside_any_stack_is_never_obscured(self):
        loose = ctk.CTkLabel(self.root, text="x")
        try:
            self.assertFalse(perf.is_obscured(loose))
        finally:
            loose.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=1)

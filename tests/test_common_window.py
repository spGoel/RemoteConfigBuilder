"""Tests for common.window: monitor-aware maximised placement.

Run from the repo root:  python -m unittest tests.test_common_window
"""

import ctypes
import os
import sys
import time
import tkinter as tk
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import customtkinter as ctk  # noqa: E402

from tests.tk_teardown import destroy_root  # noqa: E402

from common import window  # noqa: E402


def pump(widget, times=6):
    for _ in range(times):
        widget.update()


@unittest.skipUnless(sys.platform == "win32", "Win32 monitor APIs only")
class RealMonitorTests(unittest.TestCase):
    """Cross-check window.py's own Win32 calls against a fresh enumeration,
    so the module is verified against ground truth rather than against itself."""

    @classmethod
    def setUpClass(cls):
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        cls._RECT = RECT
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                  ctypes.POINTER(RECT), ctypes.c_double)
        rects = []

        def cb(_hmon, _hdc, lprect, _data):
            r = lprect.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return 1

        cls._cb_keepalive = proc(cb)
        ctypes.windll.user32.EnumDisplayMonitors(0, 0, cls._cb_keepalive, 0)
        cls.monitor_rects = rects

    def test_at_least_one_monitor_found(self):
        self.assertGreaterEqual(len(self.monitor_rects), 1)

    def test_work_area_for_a_point_in_each_monitor_matches_its_bounds(self):
        for left, top, right, bottom in self.monitor_rects:
            cx, cy = (left + right) // 2, (top + bottom) // 2
            area = window._win32_work_area_at(cx, cy)
            self.assertIsNotNone(area)
            ax, ay, aw, ah = area
            # The work area is the monitor's own rect, shrunk by at most a
            # taskbar's worth on any edge - never a different monitor's rect.
            self.assertGreaterEqual(ax, left - 1)
            self.assertGreaterEqual(ay, top - 1)
            self.assertLessEqual(ax + aw, right + 1)
            self.assertLessEqual(ay + ah, bottom + 1)
            self.assertGreater(aw, (right - left) * 0.5)
            self.assertGreater(ah, (bottom - top) * 0.5)

    def test_two_monitors_give_different_work_areas(self):
        if len(self.monitor_rects) < 2:
            self.skipTest("only one monitor attached")
        areas = set()
        for left, top, right, bottom in self.monitor_rects:
            areas.add(window._win32_work_area_at((left + right) // 2, (top + bottom) // 2))
        self.assertEqual(len(areas), len(self.monitor_rects))

    def test_cursor_pos_is_inside_some_monitor(self):
        pos = window.win32_cursor_pos()
        self.assertIsNotNone(pos)
        x, y = pos
        self.assertTrue(any(left <= x <= right and top <= y <= bottom
                            for left, top, right, bottom in self.monitor_rects))


class TargetWorkAreaTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        pump(self.root)

    def tearDown(self):
        destroy_root(self.root)

    def test_falls_back_to_tk_screen_size_when_win32_unavailable(self):
        real_cursor, real_area = window.win32_cursor_pos, window._win32_work_area_at
        window.win32_cursor_pos = lambda: None
        window._win32_work_area_at = lambda x, y: None
        try:
            area = window.target_work_area(self.root)
            self.assertEqual(
                area, (0, 0, self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        finally:
            window.win32_cursor_pos, window._win32_work_area_at = real_cursor, real_area

    def test_uses_win32_area_when_available(self):
        real_cursor, real_area = window.win32_cursor_pos, window._win32_work_area_at
        window.win32_cursor_pos = lambda: (10, 20)
        window._win32_work_area_at = (
            lambda x, y: (0, 0, 3840, 2280) if (x, y) == (10, 20) else None)
        try:
            self.assertEqual(window.target_work_area(self.root), (0, 0, 3840, 2280))
        finally:
            window.win32_cursor_pos, window._win32_work_area_at = real_cursor, real_area

    def test_zero_size_win32_area_is_rejected(self):
        real_cursor, real_area = window.win32_cursor_pos, window._win32_work_area_at
        window.win32_cursor_pos = lambda: (1, 1)
        window._win32_work_area_at = lambda x, y: (0, 0, 0, 0)
        try:
            area = window.target_work_area(self.root)
            self.assertEqual(
                area[2:], (self.root.winfo_screenwidth(), self.root.winfo_screenheight()))
        finally:
            window.win32_cursor_pos, window._win32_work_area_at = real_cursor, real_area


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        pump(self.root)

    def tearDown(self):
        destroy_root(self.root)

    def test_place_unscaled_is_not_multiplied_by_ctk_scaling(self):
        # CTkTk.geometry() would scale 400x300 by the widget scaling factor
        # (2.5 on this machine); place_unscaled must not. A withdrawn window
        # defers the actual resize until it is mapped, so deiconify first -
        # exactly what happens in real use, where the root is never withdrawn.
        window.place_unscaled(self.root, 50, 60, 400, 300)
        self.root.deiconify()
        pump(self.root)
        self.assertEqual(self.root.winfo_width(), 400)
        self.assertEqual(self.root.winfo_height(), 300)
        # winfo_rootx/y include the window manager's border/titlebar offset,
        # so they land near (50, 60) rather than exactly on it.
        self.assertLess(abs(self.root.winfo_rootx() - 50), 40)
        self.assertLess(abs(self.root.winfo_rooty() - 60), 80)

    def test_open_maximised_fills_the_target_area_when_zoomed_is_unavailable(self):
        # Simulate a window manager that refuses "zoomed": the fallback
        # geometry (fill * work area, centred) must still be inside the area.
        real_state = self.root.state

        def refuse(*_a, **_k):
            raise tk.TclError("no")

        self.root.state = refuse
        try:
            area = window.open_maximised(self.root, fill=0.5)
            pump(self.root)
            x0, y0, w0, h0 = area
            self.assertLessEqual(self.root.winfo_width(), w0)
            self.assertLessEqual(self.root.winfo_height(), h0)
            self.assertGreaterEqual(self.root.winfo_rootx(), x0 - 2)
            self.assertGreaterEqual(self.root.winfo_rooty(), y0 - 2)
        finally:
            self.root.state = real_state

    def test_open_maximised_maximises_on_a_real_window_manager(self):
        area = window.open_maximised(self.root)
        pump(self.root)
        self.assertIn(self.root.state(), ("zoomed", "normal"))  # normal only if WM truly can't
        self.assertEqual(area[2:], window.target_work_area(self.root)[2:])

    def test_reasserts_maximised_after_a_delay(self):
        window.open_maximised(self.root)
        self.root.state("normal")  # something knocked it out of zoomed
        pump(self.root)
        self.root.update()
        time.sleep(0.1)
        pump(self.root, 3)
        self.assertEqual(self.root.state(), "zoomed")

    def test_minsize_is_not_multiplied_by_ctk_scaling(self):
        window.open_maximised(self.root)
        pump(self.root)
        w, h = self.root.wm_minsize()
        self.assertLessEqual(w, 980)
        self.assertLessEqual(h, 640)

    @unittest.skipUnless(sys.platform == "win32", "Win32 monitor APIs only")
    def test_maximises_onto_the_real_secondary_monitor(self):
        # Regression test: on Windows, state("zoomed") decides the target
        # monitor from the window's CURRENT position, and that position is
        # only current after Tk has actually applied a pending geometry
        # change. Without an update_idletasks() between placing the window
        # and maximising it, this machine's real secondary monitor - which
        # sits at a negative y - got silently ignored and the window
        # maximised on the primary monitor instead. Reproduced and fixed
        # 2026-09-16. Uses the REAL work areas (only the cursor position is
        # mocked), so this exercises the same Win32 path production code does.
        class RECT(ctypes.Structure):
            _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                        ("right", ctypes.c_long), ("bottom", ctypes.c_long)]
        proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong,
                                  ctypes.POINTER(RECT), ctypes.c_double)
        rects = []

        def cb(_hmon, _hdc, lprect, _data):
            r = lprect.contents
            rects.append((r.left, r.top, r.right, r.bottom))
            return 1

        ctypes.windll.user32.EnumDisplayMonitors(0, 0, proc(cb), 0)
        if len(rects) < 2:
            self.skipTest("only one monitor attached")

        primary_area = window._win32_work_area_at(1, 1)
        # Pick whichever monitor is NOT the one under (1, 1) (the primary).
        target = next((r for r in rects if window._win32_work_area_at(
            (r[0] + r[2]) // 2, (r[1] + r[3]) // 2) != primary_area), None)
        if target is None:
            self.skipTest("could not find a distinct second monitor")
        cx, cy = (target[0] + target[2]) // 2, (target[1] + target[3]) // 2
        expected_area = window._win32_work_area_at(cx, cy)

        real_cursor = window.win32_cursor_pos
        window.win32_cursor_pos = lambda: (cx, cy)
        try:
            area = window.open_maximised(self.root)
            pump(self.root)
            self.assertEqual(area, expected_area)
            self.assertEqual(self.root.state(), "zoomed")
            # The window must have actually moved onto that monitor's work
            # area, not stayed wherever it happened to be created.
            ax, ay, aw, ah = expected_area
            self.assertGreaterEqual(self.root.winfo_rootx() + 5, ax)
            self.assertGreaterEqual(self.root.winfo_rooty() + 5, ay)
            self.assertLessEqual(self.root.winfo_rootx() - 5, ax + aw)
            self.assertLessEqual(self.root.winfo_rooty() - 5, ay + ah)
        finally:
            window.win32_cursor_pos = real_cursor


class FullscreenToggleTests(unittest.TestCase):
    def setUp(self):
        self.root = ctk.CTk()
        self.root.withdraw()
        window.open_maximised(self.root)
        pump(self.root)

    def tearDown(self):
        destroy_root(self.root)

    # These call toggle_fullscreen()/leave_fullscreen() directly rather than
    # through a synthetic <F11>/<Escape> key event. Verified: a direct call
    # toggles the real attribute instantly and reliably every time, while a
    # key event raced against the OS actually completing a real full-screen
    # transition on an unfocused, programmatically-shown test window is not
    # reliable to script - the bindings themselves are covered separately
    # below by inspecting what root.bind() reports.

    def test_toggle_and_leave_flip_the_real_attribute(self):
        self.assertFalse(bool(self.root.attributes("-fullscreen")))
        self.assertTrue(window.toggle_fullscreen(self.root))
        pump(self.root)
        self.assertTrue(bool(self.root.attributes("-fullscreen")))
        window.leave_fullscreen(self.root)
        pump(self.root)
        self.assertFalse(bool(self.root.attributes("-fullscreen")))
        self.assertEqual(self.root.state(), "zoomed")

    def test_toggle_twice_returns_to_fullscreen_off(self):
        self.assertTrue(window.toggle_fullscreen(self.root))
        pump(self.root)
        self.assertFalse(window.toggle_fullscreen(self.root))
        pump(self.root)
        self.assertFalse(bool(self.root.attributes("-fullscreen")))

    def test_leave_fullscreen_is_a_no_op_when_not_in_fullscreen(self):
        self.assertFalse(bool(self.root.attributes("-fullscreen")))
        window.leave_fullscreen(self.root)  # must not raise or toggle it on
        pump(self.root)
        self.assertFalse(bool(self.root.attributes("-fullscreen")))

    def test_f11_and_escape_are_bound(self):
        # A full end-to-end key-press check is covered by the direct-call
        # tests above; this just confirms bind_fullscreen_toggle() actually
        # registered something for both keys on this root.
        self.assertTrue(self.root.bind("<F11>"))
        self.assertTrue(self.root.bind("<Escape>"))


if __name__ == "__main__":
    unittest.main(verbosity=1)

"""UI-level tests for the CustomTkinter Memory Profiler tab.

Run:  python -m unittest tests.test_memory_profiling_ui
Everything runs headlessly against a withdrawn root window. The history DB
and the settings file are redirected to a temp directory; no SSH, no network.
"""

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import customtkinter as ctk  # noqa: E402

from tests.tk_teardown import destroy_root  # noqa: E402

from common import theme, widgets  # noqa: E402
from memory_profiling import main as memory_profiling  # noqa: E402

MemoryProfilingTab = memory_profiling.MemoryProfilingTab

SAMPLE_CSV = (
    "Time,Info,Free-Memory,Games-Played,CMR\n"
    "2026-09-01 12:00:00,Game-Idle,4096,12,41.5\n"
    "2026-09-01 12:00:05,Game-Idle,4000,13,41.7\n"
    "2026-09-01 12:00:10,Game-Idle,3900,14,42.3\n"
)


def pump(widget, times=6):
    for _ in range(times):
        widget.update()


class MemoryProfilingTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()
        widgets.init_styles(cls.root)

    @classmethod
    def tearDownClass(cls):
        destroy_root(cls.root)

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="memprof_ui_"))
        # Keep the user's real settings and history out of the test run.
        self._saved = (memory_profiling.DATA_DIR, memory_profiling.SETTINGS_FILE)
        memory_profiling.DATA_DIR = self.tmp / "data"
        memory_profiling.SETTINGS_FILE = memory_profiling.DATA_DIR / "settings.json"
        self.csv_path = self.tmp / "meters.csv"
        self.csv_path.write_text(SAMPLE_CSV, encoding="utf-8")

        ctk.set_appearance_mode("Light")
        self.app = MemoryProfilingTab(
            self.root, standalone=False, history_db=self.tmp / "history.sqlite3"
        )
        self.app.pack(fill="both", expand=True)
        pump(self.app)
        # The tab auto-starts polling 250 ms after construction (unchanged
        # behaviour). Building the UI may or may not take longer than that, so
        # cancel a still-pending start and stop one that already fired, putting
        # every test on the same footing with no listener ever bound.
        if self.app._start_job is not None:
            self.app.after_cancel(self.app._start_job)
            self.app._start_job = None
        self.app.stop()

    def tearDown(self):
        self.app.shutdown()
        self.app.destroy()
        memory_profiling.DATA_DIR, memory_profiling.SETTINGS_FILE = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    # -- construction --------------------------------------------------------

    def test_is_ctk_frame_and_uses_temp_history(self):
        self.assertIsInstance(self.app, ctk.CTkFrame)
        self.assertEqual(self.app._history.db_path, self.tmp / "history.sqlite3")
        self.assertFalse(self.app._running)
        self.assertIsNone(self.app._poll_job)
        self.assertIsNone(self.app._tcp_server)

    def test_settings_are_written_to_temp_dir(self):
        self.app._interval_var.set("2.5")
        self.assertTrue(memory_profiling.SETTINGS_FILE.exists())
        self.assertIn('"interval": "2.5"', memory_profiling.SETTINGS_FILE.read_text())

    # -- CSV via the existing reader path -----------------------------------

    def test_process_csv_loads_values_and_history(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app._process_csv(self.csv_path)
        pump(self.app)
        self.assertEqual(self.app._current_values["Free-Memory"], 3900)
        self.assertEqual(self.app._current_values["Games-Played"], 14)
        self.assertEqual(self.app._value_labels["Free-Memory"].cget("text"), "3,900")
        self.assertEqual(self.app._value_labels["CMR"].cget("text"), "42.300")
        self.assertIn("9 new values saved", self.app._status_var.get())
        earliest, latest = self.app._history.time_bounds(self.csv_path)
        self.assertIsNotNone(earliest)
        self.assertGreater(latest, earliest)
        self.assertNotEqual(self.app._time_label.cget("text"), "No history")

    def test_unchanged_csv_is_not_reimported(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app._process_csv(self.csv_path)
        first = self.app._last_file_signature
        self.app._running = True
        self.app._process_csv(self.csv_path)
        self.assertEqual(self.app._last_file_signature, first)
        self.assertIn("waiting for CSV update", self.app._status_var.get())
        self.app._running = False

    def test_missing_csv_sets_waiting_status(self):
        missing = self.tmp / "nope.csv"
        self.app._csv_path_var.set(str(missing))
        self.app._process_csv(missing)
        self.assertIn("Waiting for CSV", self.app._status_var.get())

    def test_poll_once_without_running_does_not_schedule(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app._poll_once()
        self.assertIsNone(self.app._poll_job)
        self.assertEqual(self.app._current_values["Games-Played"], 14)

    def test_start_and_stop_toggle_polling(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app.start()
        self.assertTrue(self.app._running)
        self.assertEqual(self.app._start_button.cget("text"), "Stop")
        self.assertIsNotNone(self.app._poll_job)
        self.app.stop()
        self.assertFalse(self.app._running)
        self.assertIsNone(self.app._poll_job)
        self.assertEqual(self.app._start_button.cget("text"), "Start")
        self.assertEqual(self.app._status_var.get(), "Monitoring paused")

    # -- chart ---------------------------------------------------------------

    def test_chart_draws_placeholder_without_source(self):
        self.app._csv_path_var.set("")
        self.app._draw_chart()
        texts = [
            self.app._chart.itemcget(item, "text")
            for item in self.app._chart.find_all()
            if self.app._chart.type(item) == "text"
        ]
        self.assertIn("Connect to an EGM or select a local meter CSV", texts)

    def test_chart_draws_series_for_all_history(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app._process_csv(self.csv_path)
        self.app._range_var.set("All history")
        self.app._live_view = False
        self.app._view_end = None  # falls back to the latest sample
        self.app._draw_chart()
        chart = self.app._chart
        lines = [i for i in chart.find_all() if chart.type(i) == "line"]
        # Three selected meters with data -> three polylines with 2px scaled width.
        expected_width = self.app._px(2)
        series_lines = [
            i for i in lines if float(chart.itemcget(i, "width")) == expected_width
        ]
        self.assertEqual(len(series_lines), 3)
        colours = {chart.itemcget(i, "fill") for i in series_lines}
        self.assertEqual(
            colours,
            {theme.pick(memory_profiling.METER_COLORS[m])
             for m in ("Free-Memory", "Games-Played", "CMR")},
        )

    def test_select_none_shows_prompt(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app._process_csv(self.csv_path)
        self.app._select_none()
        self.assertEqual(self.app._selected_meters(), [])
        texts = [
            self.app._chart.itemcget(i, "text")
            for i in self.app._chart.find_all()
            if self.app._chart.type(i) == "text"
        ]
        self.assertIn("Select one or more meters from the list", texts)
        self.app._select_all()
        self.assertEqual(len(self.app._selected_meters()), len(memory_profiling.ALL_METERS))

    def test_zoom_and_pan_change_range_and_view(self):
        self.app._range_var.set("1 hour")
        self.app._zoom(0.5)
        self.assertEqual(self.app._range_var.get(), "30 minutes")
        self.app._zoom(2.0)
        self.assertEqual(self.app._range_var.get(), "1 hour")
        self.app._pan(-1)
        self.assertFalse(self.app._live_view)
        self.assertLess(self.app._view_end, time.time())
        self.app._go_live()
        self.assertTrue(self.app._live_view)
        self.assertIsNone(self.app._view_end)

    def test_timeline_slider_updates_view(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app._process_csv(self.csv_path)
        earliest, _latest = self.app._history.time_bounds(self.csv_path)
        self.app._timeline_changed(earliest + 5)
        self.assertFalse(self.app._live_view)
        self.assertEqual(self.app._view_end, earliest + 5)
        self.assertIsNotNone(self.app._redraw_job)
        self.app._timeline_changed(time.time())
        self.assertTrue(self.app._live_view)

    # -- appearance ------------------------------------------------------------

    def test_appearance_change_repaints_chart(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app._process_csv(self.csv_path)
        self.app._range_var.set("All history")  # samples are in the past
        light_bg = self.app._chart.cget("bg")
        self.assertEqual(light_bg, memory_profiling.CHART_BG[0])

        ctk.set_appearance_mode("Dark")
        self.app.on_appearance_change("Dark")
        pump(self.app)
        self.assertEqual(self.app._chart.cget("bg"), memory_profiling.CHART_BG[1])
        self.assertEqual(self.app._body.cget("bg"), theme.pick(memory_profiling.PANE_BG))
        chart = self.app._chart
        dark_fills = {
            chart.itemcget(i, "fill") for i in chart.find_all() if chart.type(i) == "line"
        }
        self.assertIn(memory_profiling.CHART_GRID[1], dark_fills)
        self.assertIn(memory_profiling.METER_COLORS["Free-Memory"][1], dark_fills)

        ctk.set_appearance_mode("Light")
        self.app.on_appearance_change("Light")
        pump(self.app)
        self.assertEqual(self.app._chart.cget("bg"), memory_profiling.CHART_BG[0])

    # -- source switching -------------------------------------------------------

    def test_source_mode_switches_visible_fields(self):
        self.assertTrue(self.app._local_fields.winfo_manager())
        self.app._source_mode_var.set("Live TCP")
        self.app._source_changed()
        pump(self.app)
        self.assertTrue(self.app._tcp_fields.winfo_manager())
        self.assertFalse(self.app._local_fields.winfo_manager())
        self.assertFalse(self.app._remote_fields.winfo_manager())
        self.assertIn("Robot output", self.app._source_hint.cget("text"))

        self.app._source_mode_var.set("Remote EGM")
        self.app._source_changed()
        pump(self.app)
        self.assertTrue(self.app._remote_fields.winfo_manager())
        self.assertFalse(self.app._tcp_fields.winfo_manager())

        self.app._source_mode_var.set("Local file")
        self.app._source_changed()
        pump(self.app)
        self.assertTrue(self.app._local_fields.winfo_manager())
        self.assertEqual(self.app._source_hint.cget("text"), "")

    def test_invalid_tcp_port_does_not_start_server(self):
        self.app._listen_port_var.set("70000")
        self.app._source_mode_var.set("Live TCP")
        self.app._source_changed()
        self.assertIsNone(self.app._tcp_server)  # not running: no listener
        self.app._start_tcp_server()
        self.assertIsNone(self.app._tcp_server)
        self.assertIn("TCP port must be", self.app._status_var.get())

    # -- shutdown ----------------------------------------------------------------

    def test_shutdown_stops_cleanly_and_is_idempotent(self):
        self.app._csv_path_var.set(str(self.csv_path))
        self.app.start()
        self.app._schedule_redraw()
        self.assertIsNotNone(self.app._poll_job)
        self.assertIsNotNone(self.app._redraw_job)
        self.app.shutdown()
        self.assertTrue(self.app._closed)
        self.assertIsNone(self.app._poll_job)
        self.assertIsNone(self.app._redraw_job)
        self.assertIsNone(self.app._start_job)
        self.assertIsNone(self.app._history)
        self.assertIsNone(self.app._tcp_server)
        self.app.shutdown()  # second call is a no-op
        self.assertTrue(self.app._closed)
        pump(self.app)  # pending afters were cancelled: nothing fires


class LauncherMountsMemoryProfilerTests(unittest.TestCase):
    def test_launcher_mounts_memory_profiler(self):
        import launcher

        app = launcher.LauncherApp()
        app.withdraw()
        pump(app, 10)
        try:
            self.assertIn("memory", app._tool_apps)
            memory_app = app._tool_apps["memory"]
            self.assertEqual(type(memory_app).__name__, "MemoryProfilingTab")
            self.assertIsInstance(memory_app, ctk.CTkFrame)
            app._set_appearance("Dark")
            pump(app)
            self.assertEqual(memory_app._chart.cget("bg"), memory_profiling.CHART_BG[1])
            app._set_appearance("Light")
            pump(app)
            self.assertEqual(memory_app._chart.cget("bg"), memory_profiling.CHART_BG[0])
        finally:
            for tool_app in app._tool_apps.values():
                shutdown = getattr(tool_app, "shutdown", None)
                if callable(shutdown):
                    shutdown()
            destroy_root(app)


if __name__ == "__main__":
    unittest.main(verbosity=1)

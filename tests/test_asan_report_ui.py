"""UI-level tests for the CustomTkinter ASAN Report tab.

Run:  python tests\\test_asan_report_ui.py
Everything runs headlessly against a withdrawn root window. No network, no
file dialogs; the report is loaded by setting the path variable directly.
"""

import os
import sys
import tempfile
import time
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "asan_report"))

import customtkinter as ctk  # noqa: E402

from tests.tk_teardown import destroy_root  # noqa: E402

from common import widgets  # noqa: E402
import main as asan_main  # noqa: E402  (asan_report/main.py)

AsanReportTab = asan_main.AsanReportTab

FIXTURE = os.path.join(REPO, "tests", "fixtures", "single_complete_asan.txt")
FALLBACK_REPORT = """==42==ERROR: LeakSanitizer: detected memory leaks

Direct leak of 64 byte(s) in 2 object(s) allocated from:
    #0 0x1000 in malloc asan_malloc_linux.cpp:69
    #1 0x1001 in CreateWidget project/widget.cpp:42

SUMMARY: AddressSanitizer: 64 byte(s) leaked in 2 allocation(s).
"""


def pump(widget, times=6):
    for _ in range(times):
        widget.update()


def pump_until(widget, predicate, timeout=5.0):
    """Run a real mainloop until predicate() holds or the timeout passes.

    The tab's worker threads hand results back with widget.after(), which
    tkinter only accepts from another thread while the main thread is inside
    mainloop() (update() alone raises "main thread is not in main loop").
    """
    root = widget.winfo_toplevel()
    deadline = time.time() + timeout

    def poll():
        if predicate() or time.time() >= deadline:
            root.quit()
        else:
            root.after(20, poll)

    root.after(0, poll)
    root.mainloop()
    return predicate()


class AsanReportTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()
        widgets.init_styles(cls.root)
        if os.path.isfile(FIXTURE):
            cls.report_path = FIXTURE
            cls._tmp = None
        else:
            cls._tmp = tempfile.NamedTemporaryFile(
                "w", suffix=".txt", delete=False, encoding="utf-8"
            )
            cls._tmp.write(FALLBACK_REPORT)
            cls._tmp.close()
            cls.report_path = cls._tmp.name

    @classmethod
    def tearDownClass(cls):
        destroy_root(cls.root)
        if cls._tmp is not None:
            try:
                os.unlink(cls._tmp.name)
            except OSError:
                pass

    def setUp(self):
        self.app = AsanReportTab(self.root, standalone=False)
        self.app.pack(fill="both", expand=True)
        pump(self.app)

    def tearDown(self):
        self.app.destroy()

    def _raw(self):
        return self.app._text_contents(self.app._text)

    def _analysis(self):
        return self.app._text_contents(self.app._analysis_text)

    # -- construction --------------------------------------------------------

    def test_is_ctk_frame_with_named_sub_tabs(self):
        self.assertIsInstance(self.app, ctk.CTkFrame)
        self.assertIsInstance(self.app._tabs, ctk.CTkTabview)
        self.assertIsNotNone(self.app._tabs.tab(asan_main.RAW_TAB))
        self.assertIsNotNone(self.app._tabs.tab(asan_main.ANALYSIS_TAB))
        self.assertEqual(self.app._tabs.get(), asan_main.RAW_TAB)

    def test_initial_state(self):
        self.assertEqual(self.app._status_var.get(), "Select an ASAN report file")
        self.assertEqual(self.app._summary_var.get(), "No report analyzed")
        self.assertEqual(self.app._save_analysis_button.cget("state"), "disabled")
        self.assertEqual(self.app._start_button.cget("state"), "normal")
        self.assertIn("Select a report, then click Start Analysis.", self._analysis())
        self.assertEqual(self._raw().strip(), "")

    def test_module_level_io_names_kept(self):
        self.assertEqual(asan_main.MAX_REPORT_BYTES, 16 * 1024 * 1024)
        self.assertIn("truncated", asan_main.TRUNCATION_NOTICE)
        self.assertTrue(callable(asan_main.read_local_report))

    # -- preview -------------------------------------------------------------

    def test_load_preview_fills_raw_view_and_selects_raw_tab(self):
        from pathlib import Path

        self.app._tabs.set(asan_main.ANALYSIS_TAB)
        self.app._file_var.set(self.report_path)
        self.app._load_preview(Path(self.report_path))
        self.assertIn("Loading preview", self.app._status_var.get())
        ok = pump_until(self.app, lambda: "LeakSanitizer" in self._raw())
        self.assertTrue(ok, "preview never arrived")
        self.assertIn("SUMMARY: AddressSanitizer", self._raw())
        self.assertIn("click Start Analysis", self.app._status_var.get())
        self.assertEqual(self.app._tabs.get(), asan_main.RAW_TAB)

    def test_preview_of_missing_file_reports_error_in_status(self):
        from pathlib import Path

        missing = Path(self.report_path).with_name("definitely_missing_report.txt")
        self.app._load_preview(missing)
        ok = pump_until(
            self.app,
            lambda: "Loading preview" not in self.app._status_var.get(),
        )
        self.assertTrue(ok)
        self.assertIn("definitely_missing_report", self.app._status_var.get())
        self.assertEqual(self._raw().strip(), "")

    # -- analysis ------------------------------------------------------------

    def test_start_analysis_fills_analysis_view_and_summary(self):
        self.app._file_var.set(self.report_path)
        self.app._start_analysis()
        self.assertEqual(self.app._start_button.cget("state"), "disabled")
        self.assertIn("Analyzing complete report offline", self._analysis())
        ok = pump_until(self.app, lambda: "ASAN LEAK ANALYSIS" in self._analysis())
        self.assertTrue(ok, "analysis never arrived")
        self.assertIn("RESULT: 1 possible leak pattern needs review.", self._analysis())
        self.assertIn("CreateWidget project/widget.cpp:42", self._analysis())
        self.assertEqual(
            self.app._summary_var.get(),
            "Reports 1/1  •  Candidate 1  •  Suppressed 0  •  Needs review 0",
        )
        self.assertIn("Offline analysis ready for", self.app._status_var.get())
        self.assertEqual(self.app._tabs.get(), asan_main.ANALYSIS_TAB)
        self.assertEqual(self.app._start_button.cget("state"), "normal")
        self.assertEqual(self.app._save_analysis_button.cget("state"), "normal")
        self.assertEqual(self.app._analysis_output, self._analysis())

    def test_start_analysis_without_file_sets_status_only(self):
        self.app._file_var.set("")
        self.app._start_analysis()
        pump(self.app)
        self.assertIn("File not found", self.app._status_var.get())
        self.assertEqual(self.app._start_button.cget("state"), "normal")
        self.assertIn("Select a report, then click Start Analysis.", self._analysis())

    def test_stale_analysis_result_is_ignored(self):
        self.app._analysis_request_id = 5
        self.app._show_analysis(
            4, "old.txt",
            {"totals": {"candidate": {"signatures": 9}, "suppressed": {"signatures": 9},
                        "uncertain": {"signatures": 9}},
             "stats": {"reports_completed": 9, "reports_started": 9}},
            "STALE",
        )
        self.assertNotIn("STALE", self._analysis())
        self.assertEqual(self.app._summary_var.get(), "No report analyzed")

    def test_save_analysis_without_output_sets_status(self):
        self.app._analysis_output = ""
        self.app._save_analysis()
        self.assertEqual(self.app._status_var.get(), "Run analysis first")

    def test_save_analysis_writes_output(self):
        called = {}

        def fake_save(**kwargs):
            called.update(kwargs)
            return target

        target = os.path.join(tempfile.gettempdir(), "asan_ui_test_analysis.txt")
        self.app._analysis_output = "ANALYSIS BODY"
        self.app._file_var.set(self.report_path)
        original = asan_main.filedialog.asksaveasfilename
        asan_main.filedialog.asksaveasfilename = fake_save
        try:
            self.app._save_analysis()
        finally:
            asan_main.filedialog.asksaveasfilename = original
        try:
            with open(target, encoding="utf-8") as fh:
                self.assertEqual(fh.read(), "ANALYSIS BODY")
        finally:
            os.unlink(target)
        self.assertTrue(called["initialfile"].endswith("_offline_analysis.txt"))
        self.assertEqual(self.app._status_var.get(), f"Saved {target}")

    # -- appearance ----------------------------------------------------------

    def test_appearance_change_does_not_raise(self):
        self.app.on_appearance_change("Dark")
        ctk.set_appearance_mode("Dark")
        pump(self.app)
        self.app.on_appearance_change("Light")
        ctk.set_appearance_mode("Light")
        pump(self.app)


if __name__ == "__main__":
    unittest.main(verbosity=1)

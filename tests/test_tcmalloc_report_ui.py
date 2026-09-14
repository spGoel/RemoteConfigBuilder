"""UI-level tests for the CustomTkinter tcMalloc Report tab.

Run:  python tests\\test_tcmalloc_report_ui.py
Everything runs headlessly against a withdrawn root window. No SSH, no
pprof; results are handed to the UI through the same methods the SSH worker
threads post to via after(0, ...).
"""

import importlib.util
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)

import customtkinter as ctk  # noqa: E402

from tests.tk_teardown import destroy_root  # noqa: E402

from common import widgets  # noqa: E402

# Load under a private name so it cannot collide with another tool's main.py.
_SPEC = importlib.util.spec_from_file_location(
    "tcmalloc_report_ui_main", Path(REPO) / "tcmalloc_report" / "main.py"
)
tcmalloc = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(tcmalloc)

TcMallocReportTab = tcmalloc.TcMallocReportTab

FAKE_RECORDS = [
    {
        "path": "/home/mk7/development/game/build/host/scratch/.logs/mem_profiles/game.0028.heap",
        "modified": 1_700_000_000.0, "size": 5 * 1024 * 1024,
        "number": "0028", "name": "game.0028.heap",
    },
    {
        "path": "/home/mk7/development/game/build/host/scratch/.logs/mem_profiles/game.0007.heap",
        "modified": 1_699_990_000.0, "size": 900,
        "number": "0007", "name": "game.0007.heap",
    },
    {
        "path": "/home/mk7/development/game/build/host/scratch/.logs/mem_profiles/odd.heap",
        "modified": 1_699_980_000.0, "size": 2048,
        "number": "", "name": "odd.heap",
    },
]


def pump(widget, times=6):
    for _ in range(times):
        widget.update()


class TcMallocReportTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()
        widgets.init_styles(cls.root)
        # Keep the tab's settings.json out of the real home directory.
        cls.tmp = Path(tempfile.mkdtemp(prefix="tcmalloc_ui_"))
        cls._orig_paths = (tcmalloc.DATA_DIR, tcmalloc.SETTINGS_FILE)
        tcmalloc.DATA_DIR = cls.tmp
        tcmalloc.SETTINGS_FILE = cls.tmp / "settings.json"

    @classmethod
    def tearDownClass(cls):
        tcmalloc.DATA_DIR, tcmalloc.SETTINGS_FILE = cls._orig_paths
        destroy_root(cls.root)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.app = TcMallocReportTab(self.root, standalone=False)
        self.app.pack(fill="both", expand=True)
        pump(self.app)

    def tearDown(self):
        self.app.destroy()

    def _load(self, records=FAKE_RECORDS):
        """Hand results to the UI exactly as the refresh worker does."""
        self.app._refresh_done(self.app._request_id, list(records))
        pump(self.app)

    def _rows(self):
        return [self.app._tree.item(i, "values") for i in self.app._tree.get_children()]

    def _select(self, index):
        """Select a row and drain the <<TreeviewSelect>> it queues, as a user click would."""
        iid = self.app._tree.get_children()[index]
        self.app._tree.selection_set(iid)
        self.app._tree.event_generate("<<TreeviewSelect>>")
        pump(self.app)
        return iid

    def _selectmode(self):
        return str(self.app._tree.cget("selectmode"))

    # -- construction --------------------------------------------------------

    def test_is_a_ctk_frame_with_ttk_tree(self):
        self.assertIsInstance(self.app, ctk.CTkFrame)
        self.assertIsInstance(self.app._tree_pane, widgets.TreePane)
        self.assertEqual(self.app._tree.cget("style"), "App.Treeview")
        self.assertEqual(self._selectmode(), "browse")
        self.assertEqual(tuple(self.app._tree.cget("columns")),
                         ("number", "modified", "size", "path"))

    def test_initial_state(self):
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.assertEqual(self.app._refresh_button.cget("state"), "normal")
        self.assertEqual(self.app._status_var.get(), "Enter the EGM IP and host path")
        self.assertEqual(self.app._selection_var.get(), "No heap selected")
        self.assertEqual(self._rows(), [])
        self.assertIsNone(self.app._latest_pdf)

    def test_refresh_without_connection_does_not_start(self):
        self.app._ip_var.set("")
        self.app._host_var.set("")
        before = self.app._request_id
        self.app.refresh()
        self.assertEqual(self.app._request_id, before + 1)
        self.assertEqual(self.app._status_var.get(), "Enter the EGM IP and host path")
        # Nothing was disabled because no worker was started.
        self.assertEqual(self.app._refresh_button.cget("state"), "normal")

    # -- populating from a snapshot list -------------------------------------

    def test_refresh_done_populates_tree(self):
        self._load()
        rows = self._rows()
        self.assertEqual(len(rows), 3)
        self.assertEqual(rows[0][0], "0028")
        self.assertEqual(rows[0][2], "5.0 MiB")
        self.assertEqual(rows[0][3], "game.0028.heap")
        self.assertEqual(rows[1][2], "900 B")
        self.assertEqual(rows[2][0], "—")  # no heap number -> em dash
        self.assertEqual(len(self.app._display_records), 3)
        self.assertEqual(self.app._status_var.get(), "3 tcMalloc heap file(s) found")
        self.assertEqual(self.app._refresh_button.cget("state"), "normal")
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.assertEqual(self._selectmode(), "browse")

    def test_stale_refresh_result_is_ignored(self):
        stale_id = self.app._request_id
        self.app._request_id += 1
        self.app._refresh_done(stale_id, list(FAKE_RECORDS))
        pump(self.app)
        self.assertEqual(self._rows(), [])

    def test_filter_narrows_rows_and_clears_selection(self):
        self._load()
        self.app._filter_var.set("0028")
        pump(self.app)
        self.assertEqual([r[3] for r in self._rows()], ["game.0028.heap"])
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.assertIsNone(self.app._selected_record)
        self.app._filter_var.set("")
        pump(self.app)
        self.assertEqual(len(self._rows()), 3)

    # -- selection -----------------------------------------------------------

    def test_selecting_a_heap_enables_convert(self):
        self._load()
        iid = self._select(0)
        self.assertEqual(self.app._convert_button.cget("state"), "normal")
        self.assertIs(self.app._selected_record, self.app._display_records[iid])
        self.assertEqual(self.app._selected_record["number"], "0028")
        self.assertIn("Heap 0028", self.app._selection_var.get())
        self.assertIn("5.0 MiB", self.app._selection_var.get())
        self.assertIn("game.0028.heap", self.app._selection_var.get())
        self.assertEqual(self.app._status_var.get(),
                         "Selected game.0028.heap; click Convert to PDF")

    def test_heap_selected_with_empty_selection_is_noop(self):
        self._load()
        self.app._heap_selected()
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.assertIsNone(self.app._selected_record)

    def test_analyze_without_selection_does_nothing(self):
        self._load()
        before = self.app._request_id
        self.app._analyze()
        self.assertEqual(self.app._request_id, before)
        self.assertFalse(self.app._tree.instate(["disabled"]))

    # -- worker results ------------------------------------------------------

    def test_failed_shows_message_in_console_and_status(self):
        self.app._refresh_button.configure(state="disabled")
        self.app._failed(self.app._request_id, "Host path does not exist on the EGM: /x\nmore detail")
        pump(self.app)
        self.assertEqual(self.app._status_var.get(), "Host path does not exist on the EGM: /x")
        self.assertIn("more detail", self.app._console_contents())
        self.assertEqual(self.app._refresh_button.cget("state"), "normal")
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")

    def test_analysis_done_records_pdf_and_reenables_ui(self):
        self._load()
        self._select(0)
        pdf = self.tmp / "abc_tcmalloc_0028.pdf"
        pdf.write_bytes(b"%PDF-1.4\n")
        self.app._tree.state(["disabled"])
        self.app._refresh_button.configure(state="disabled")
        self.app._analysis_done(self.app._request_id, pdf, "pprof console output")
        pump(self.app)
        self.assertEqual(self.app._latest_pdf, pdf)
        self.assertFalse(self.app._tree.instate(["disabled"]))
        self.assertEqual(self.app._refresh_button.cget("state"), "normal")
        self.assertEqual(self.app._convert_button.cget("state"), "normal")
        text = self.app._console_contents()
        self.assertIn("pprof console output", text)
        self.assertIn("Downloaded PDF:", text)
        self.assertIn(str(pdf), text)
        self.assertEqual(self.app._status_var.get(), f"Analysis complete: {pdf.name}")

    def test_analysis_failed_restores_convert_when_selected(self):
        self._load()
        self._select(1)
        self.app._tree.state(["disabled"])
        self.app._analysis_failed(self.app._request_id, "tcMalloc_profiler.sh failed with exit 2\n\ndetail")
        pump(self.app)
        self.assertFalse(self.app._tree.instate(["disabled"]))
        self.assertEqual(self.app._convert_button.cget("state"), "normal")
        self.assertEqual(self.app._status_var.get(), "tcMalloc_profiler.sh failed with exit 2")
        self.assertIn("detail", self.app._console_contents())

    # -- console -------------------------------------------------------------

    def test_write_console_replaces_text(self):
        self.app._write_console("first message")
        pump(self.app)
        self.assertEqual(self.app._console_contents(), "first message")
        self.app._write_console("second", ok=True)
        self.assertEqual(self.app._console_contents(), "second")
        self.app._write_console("bad", error=True)
        self.assertEqual(self.app._console_contents(), "bad")
        # The pane stays read-only for the user.
        self.assertEqual(self.app._console.cget("state"), "disabled")

    # -- open / save guards --------------------------------------------------

    def test_open_and_save_without_pdf_only_set_status(self):
        self.app._open_pdf()
        self.assertEqual(self.app._status_var.get(), "Generate a tcMalloc PDF report first")
        self.app._status_var.set("x")
        self.app._save_pdf()
        self.assertEqual(self.app._status_var.get(), "Generate a tcMalloc PDF report first")

    # -- connection edits ----------------------------------------------------

    def test_connection_change_resets_list_and_saves_settings(self):
        self._load()
        self._select(0)
        before = self.app._request_id
        self.app._ip_var.set("10.1.2.3")
        self.app._host_var.set("/home/mk7/development/game/build/host")
        pump(self.app)
        self.assertEqual(self._rows(), [])
        self.assertIsNone(self.app._selected_record)
        self.assertEqual(self.app._convert_button.cget("state"), "disabled")
        self.assertEqual(self.app._status_var.get(), "Click Load Heap Files to validate this build")
        self.assertGreater(self.app._request_id, before)
        saved = (self.tmp / "settings.json").read_text(encoding="utf-8")
        self.assertIn('"ip": "10.1.2.3"', saved)
        self.assertIn('"host": "/home/mk7/development/game/build/host"', saved)

    # -- appearance ----------------------------------------------------------

    def test_appearance_change_repaints_tree_without_error(self):
        self._load()
        try:
            ctk.set_appearance_mode("Dark")
            self.app.on_appearance_change("Dark")
            pump(self.app)
            ctk.set_appearance_mode("Light")
            self.app.on_appearance_change("Light")
            pump(self.app)
        finally:
            ctk.set_appearance_mode("System")
        self.assertEqual(len(self._rows()), 3)

    def test_shutdown_drops_late_results(self):
        self.app.shutdown()
        self.app._refresh_done(self.app._request_id, list(FAKE_RECORDS))
        self.assertEqual(self._rows(), [])

    def test_format_size(self):
        fmt = TcMallocReportTab._format_size
        self.assertEqual(fmt(0), "0 B")
        self.assertEqual(fmt(1023), "1023 B")
        self.assertEqual(fmt(1024), "1.0 KiB")
        self.assertEqual(fmt(3 * 1024 * 1024), "3.0 MiB")
        self.assertEqual(fmt(5 * 1024 ** 3), "5.0 GiB")


if __name__ == "__main__":
    unittest.main(verbosity=1)

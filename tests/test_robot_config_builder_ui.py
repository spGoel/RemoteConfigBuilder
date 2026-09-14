"""UI-level tests for the CustomTkinter Robot Config Builder tab.

Run:  python tests\\test_robot_config_builder_ui.py
Everything runs headlessly against a withdrawn root window. No SSH, no EGM
upload, no screenshots, no email sending; the user's settings/recent files
are never touched.
"""

import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOL_DIR = os.path.join(REPO, "robot_config_builder")
sys.path.insert(0, REPO)
sys.path.insert(0, TOOL_DIR)

import customtkinter as ctk  # noqa: E402

from tests.tk_teardown import cancel_pending_timers, destroy_root  # noqa: E402

from common import widgets  # noqa: E402


def _load_module(name, path):
    """Load a script under a unique module name (like the launcher does), so
    this file can share a process with the Robot Builder UI test, whose
    entry point is also called main.py."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


config_builder = _load_module("robot_config_builder_main", os.path.join(TOOL_DIR, "main.py"))
App = config_builder.App
xml_io = config_builder.xml_io
snapshot_manager = config_builder.snapshot_manager
email_dialog = sys.modules["email_dialog"]
coordinate_picker = sys.modules["coordinate_picker"]
models = sys.modules["models"]

DEFAULT_XML = Path(TOOL_DIR) / "default.xml"
TEMPLATES_DIR = Path(TOOL_DIR) / "templates"


def pump(widget, times=6):
    for _ in range(times):
        widget.update()



def _no_settings():
    return {"ip": "", "orientation": "landscape", "build_path": ""}


class ConfigBuilderTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()
        widgets.init_styles(cls.root)
        cls.tmp = Path(tempfile.mkdtemp(prefix="rcb_ui_test_"))
        # Keep the user's real settings/recent files out of the test.
        cls._patches = [
            patch.object(config_builder, "RECENT_FILE", cls.tmp / "recent.json"),
            patch.object(snapshot_manager, "load_settings", _no_settings),
            patch.object(snapshot_manager, "save_settings", lambda *a, **k: None),
        ]
        for p in cls._patches:
            p.start()

    @classmethod
    def tearDownClass(cls):
        for p in cls._patches:
            p.stop()
        destroy_root(cls.root)
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def setUp(self):
        self.app = App(self.root, standalone=False)
        self.app.pack(fill="both", expand=True)
        pump(self.app)

    def tearDown(self):
        self.app.destroy()

    # -- helpers -------------------------------------------------------------

    def _select(self, node):
        self.app.tree_panel.tree.selection_set(str(id(node)))
        pump(self.app, 4)

    def _first_of_type(self, node_type):
        for child in self.app.root_node.children:
            if child.node_type == node_type:
                return child
        self.fail(f"default.xml has no top-level {node_type}")

    # -- construction --------------------------------------------------------

    def test_is_ctk_frame_with_default_loaded(self):
        self.assertIsInstance(self.app, ctk.CTkFrame)
        self.assertIsNotNone(self.app.root_node)
        self.assertEqual(self.app.root_node.node_type, "Sequence")
        self.assertIsNone(self.app.current_file)
        self.assertFalse(self.app.modified)
        # Tree shows the root row and the preview holds the generated XML.
        self.assertEqual(len(self.app.tree_panel.tree.get_children("")), 1)
        self.assertIn('<event type="Sequence">', self.app.xml_preview.get("1.0", "end"))
        self.assertIn("Ready", self.app._status_var.get())

    def test_embedded_mode_has_no_native_menubar(self):
        self.assertIsNone(self.app.menubar)
        # The File menu still exists (a tk.Menu dropped down from a CTk button)
        # and keeps its fixed entries in build order.
        import tkinter as tk
        self.assertIsInstance(self.app._file_menu, tk.Menu)
        self.assertEqual(self.app._file_menu.entrycget(0, "label"), "New\t\tCtrl+N")
        self.assertEqual(self.app._file_menu.entrycget(6, "label"), "Save as Default")
        self.assertEqual(len(self.app._menus), 4)  # File, Edit, Templates, Help

    def test_recent_files_keep_exit_and_separator_at_end(self):
        import tkinter as tk
        fm = self.app._file_menu
        self.app._recent = [str(self.tmp / "one.xml"), str(self.tmp / "two.xml")]
        self.app._rebuild_recent_menu()
        # Rebuilding twice must not drift or drop fixed entries.
        self.app._rebuild_recent_menu()
        last = fm.index(tk.END)
        self.assertEqual(fm.entrycget(last, "label"), "Exit")
        self.assertEqual(fm.type(last - 1), "separator")
        labels = [fm.entrycget(i, "label") for i in range(last + 1) if fm.type(i) == "command"]
        self.assertIn("  one.xml", labels)
        self.assertIn("  two.xml", labels)
        self.assertEqual(labels.count("  one.xml"), 1)
        # Clearing the recent list removes only the recent block.
        self.app._recent = []
        self.app._rebuild_recent_menu()
        last = fm.index(tk.END)
        self.assertEqual(fm.entrycget(last, "label"), "Exit")
        self.assertEqual(fm.type(last - 1), "separator")
        self.assertEqual(fm.entrycget(last - 2, "label"), "Save as Default")
        self.assertNotIn("  one.xml", [fm.entrycget(i, "label") for i in range(last + 1) if fm.type(i) == "command"])

    def test_treeview_uses_shared_style(self):
        tree = self.app.tree_panel.tree
        self.assertEqual(tree.cget("style"), "App.Treeview")
        # Column widths were scaled for DPI, so they are at least the nominal px.
        self.assertGreaterEqual(int(tree.column("#0", "width")), 175)

    # -- new / load / save ---------------------------------------------------

    def test_open_edit_save_round_trip(self):
        src = self.tmp / "input.xml"
        shutil.copy(DEFAULT_XML, src)
        self.app._open_file(str(src))
        pump(self.app)
        self.assertEqual(self.app.current_file, str(src))
        self.assertFalse(self.app.modified)
        self.assertIn(str(src), self.app._recent)
        before = len(self.app.root_node.children)

        # Edit through the tree panel exactly as the context menu would.
        self._select(self.app.root_node)
        self.app.tree_panel._add_child("Wait")
        pump(self.app)
        self.assertTrue(self.app.modified)
        self.assertEqual(len(self.app.root_node.children), before + 1)
        self.assertEqual(self.app.root_node.children[-1].node_type, "Wait")
        self.assertEqual(len(self.app.undo_stack), 1)

        out = self.tmp / "output.xml"
        warnings = []
        with patch.object(config_builder.messagebox, "showwarning",
                          lambda *a, **k: warnings.append(a)):
            self.app._save_to(str(out))
        self.assertFalse(self.app.modified)
        self.assertIn("Saved locally", self.app._status_var.get())
        self.assertTrue(out.exists())

        saved = out.read_text(encoding="utf-8")
        self.assertEqual(saved, xml_io.generate_xml(self.app.root_node))
        reloaded = xml_io.parse_xml_file(str(out))
        self.assertEqual(len(reloaded.children), before + 1)
        self.assertEqual(xml_io.generate_xml(reloaded), saved)
        ET.fromstring(saved)

    def test_action_new_resets_to_default(self):
        src = self.tmp / "new_input.xml"
        shutil.copy(next(TEMPLATES_DIR.glob("*.xml")), src)
        self.app._open_file(str(src))
        self.app.modified = True
        with patch.object(self.app, "_confirm_discard", return_value=True):
            self.app.action_new()
        pump(self.app)
        self.assertIsNone(self.app.current_file)
        self.assertFalse(self.app.modified)
        self.assertEqual(self.app.undo_stack, [])
        self.assertIn("default.xml", self.app._status_var.get())
        self.assertEqual(len(self.app.root_node.children),
                         len(xml_io.parse_xml_file(str(DEFAULT_XML)).children))

    def test_open_error_leaves_state_untouched(self):
        bad = self.tmp / "bad.xml"
        bad.write_text("<event type='Sequence'", encoding="utf-8")
        root_before = self.app.root_node
        shown = []
        with patch.object(config_builder.messagebox, "showerror",
                          lambda *a, **k: shown.append(a)):
            self.app._open_file(str(bad))
        self.assertEqual(len(shown), 1)
        self.assertIs(self.app.root_node, root_before)
        self.assertIsNone(self.app.current_file)

    # -- tree selection -> properties panel ----------------------------------

    def test_selecting_a_node_populates_properties(self):
        button = self._first_of_type("Button")
        self._select(button)
        props = self.app.props_panel
        self.assertIs(props._current_node, button)
        self.assertEqual(props._vars["id"].get(), button.id)
        self.assertEqual(props._vars["key"].get(), button.attrs.get("key", "Play"))
        self.assertIn("sf_enabled", props._vars)
        self.assertGreater(len(props.inner.winfo_children()), 3)
        self.assertIn("Selected: Button", self.app._status_var.get())

    def test_meter_list_form_renders_every_meter(self):
        meter_list = self._first_of_type("meter-list")
        self._select(meter_list)
        props = self.app.props_panel
        meter_vars = [k for k in props._vars if k.startswith("meter_")]
        self.assertEqual(len(meter_vars), len(models.ALL_METERS))
        self.assertEqual(props._vars["ml_mode"].get(), meter_list.attrs.get("mode", "periodic"))
        # Special nodes have no state filter section.
        self.assertNotIn("sf_enabled", props._vars)

    def test_property_edit_writes_through_to_node(self):
        wait = self._first_of_type("Wait")
        self._select(wait)
        props = self.app.props_panel
        props._vars["w_timeout"].set("7")
        self.assertEqual(wait.attrs["timeout"], "7")
        props._vars["id"].set("edited")
        self.assertEqual(wait.id, "edited")
        # The debounced refresh is scheduled; run it now.
        self.assertIsNotNone(props._refresh_job)
        self.app._on_property_changed()
        pump(self.app)
        self.assertTrue(self.app.modified)
        self.assertIn('id="edited"', self.app.xml_preview.get("1.0", "end"))
        self.assertEqual(self.app.tree_panel.tree.item(str(id(wait)), "text"), "Wait")

    # -- dirty tracking and confirm_discard ----------------------------------

    def test_confirm_discard_when_clean(self):
        self.assertFalse(self.app.modified)
        self.assertTrue(self.app._confirm_discard())

    def test_confirm_discard_when_dirty(self):
        self.app.modified = True
        saved = []
        with patch.object(self.app, "action_save", lambda: saved.append(True)):
            with patch.object(config_builder.messagebox, "askyesnocancel", return_value=None):
                self.assertFalse(self.app._confirm_discard())
            with patch.object(config_builder.messagebox, "askyesnocancel", return_value=False):
                self.assertTrue(self.app._confirm_discard())
            self.assertEqual(saved, [])
            with patch.object(config_builder.messagebox, "askyesnocancel", return_value=True):
                self.assertTrue(self.app._confirm_discard())
            self.assertEqual(saved, [True])

    def test_dirty_state_tracking(self):
        self.assertFalse(self.app.modified)
        self.app._on_tree_changed()
        self.assertTrue(self.app.modified)
        self.app.modified = False
        self.app._on_property_changed()
        self.assertTrue(self.app.modified)

    def test_undo_restores_previous_tree(self):
        before = len(self.app.root_node.children)
        self._select(self.app.root_node)
        self.app.tree_panel._add_child("Button")
        self.assertEqual(len(self.app.root_node.children), before + 1)
        self.app.action_undo()
        pump(self.app)
        self.assertEqual(len(self.app.root_node.children), before)
        self.assertEqual(self.app.undo_stack, [])
        self.assertIn("Undone", self.app._status_var.get())
        self.app.action_undo()
        self.assertIn("Nothing to undo", self.app._status_var.get())

    # -- appearance ----------------------------------------------------------

    def test_on_appearance_change_dark_and_back(self):
        button = self._first_of_type("Button")
        self._select(button)
        try:
            ctk.set_appearance_mode("Dark")
            self.app.on_appearance_change("Dark")
            pump(self.app)
            self.assertEqual(ctk.get_appearance_mode(), "Dark")
            fg = self.app.tree_panel.tree.tag_configure("control", "foreground")
            self.assertEqual(str(fg).lower(), "#ffa35c")
            # Tree still responds after the repaint.
            self._select(self.app.root_node)
            self.assertIs(self.app.props_panel._current_node, self.app.root_node)
        finally:
            ctk.set_appearance_mode("Light")
            self.app.on_appearance_change("Light")
            pump(self.app)
        fg = self.app.tree_panel.tree.tag_configure("control", "foreground")
        self.assertEqual(str(fg).lower(), "#e65100")

    # -- dialogs (built headlessly, no network) ------------------------------

    def test_email_dialog_builds_and_lists_emails(self):
        with patch.object(email_dialog, "load_emails", lambda: ["a@aristocrat.com", "b@aristocrat.com"]), \
             patch.object(email_dialog, "save_emails", lambda emails: None):
            dlg = email_dialog.EmailDialog(self.app, self.app._machine_ip_var,
                                           self.app._machine_build_path_var)
            pump(dlg, 3)
            try:
                self.assertIsInstance(dlg, ctk.CTkToplevel)
                self.assertEqual(len(dlg._tree.get_children()), 2)
                dlg._entry_var.set("bad@example.com")
                with patch.object(email_dialog.messagebox, "showerror", lambda *a, **k: None):
                    dlg._add()
                self.assertEqual(len(dlg._emails), 2)
                dlg._entry_var.set("C@Aristocrat.com")
                dlg._add()
                self.assertEqual(dlg._emails[-1], "c@aristocrat.com")
                self.assertEqual(len(dlg._tree.get_children()), 3)
                dlg._set_busy(True, "busy")
                self.assertEqual(dlg._add_btn.cget("state"), "disabled")
                dlg._set_busy(False, "")
                self.assertEqual(dlg._add_btn.cget("state"), "normal")
            finally:
                dlg.destroy()

    def test_merge_emails_into_xml_is_unchanged(self):
        xml, added, already = email_dialog._merge_emails_into_xml(
            "<robot_conf><email_id>x@aristocrat.com</email_id></robot_conf>",
            ["x@aristocrat.com", "y@aristocrat.com"],
        )
        self.assertEqual(added, 1)
        self.assertEqual(already, ["x@aristocrat.com"])
        root = ET.fromstring(xml)
        self.assertEqual(root.find("email_id").text, "x@aristocrat.com,y@aristocrat.com")
        self.assertEqual(root.find("email_subject").text.strip(), email_dialog.EMAIL_SUBJECT)

    def test_progress_dialog_builds(self):
        dlg = coordinate_picker.ProgressDialog(self.app, "Working")
        pump(dlg, 2)
        try:
            self.assertIsInstance(dlg, ctk.CTkToplevel)
            self.assertEqual(dlg._label.cget("text"), "Working")
        finally:
            dlg.destroy()

    def test_email_dialog_requires_ip(self):
        shown = []
        with patch.object(config_builder.messagebox, "showwarning",
                          lambda *a, **k: shown.append(a)):
            self.app._open_email_dialog()
        self.assertEqual(len(shown), 1)


class TreePanelUnderPlainTkTests(unittest.TestCase):
    def test_tree_panel_builds_under_plain_tk_root(self):
        """TreePanel is imported by the tree tests under a plain tk.Tk root,
        with no CTk root and no fonts initialised; it must still build.
        Runs in a subprocess so the second Tcl interpreter cannot interfere
        with the CTk root used by the other tests."""
        probe = (
            "import sys, tkinter as tk\n"
            f"sys.path.insert(0, {TOOL_DIR!r})\n"
            "from tree_panel import TreePanel\n"
            "from models import RobotNode\n"
            "root = tk.Tk(); root.withdraw()\n"
            "picked = []\n"
            "panel = TreePanel(root, on_node_selected=picked.append)\n"
            "panel.pack(fill='both', expand=True)\n"
            "node = RobotNode.new('Sequence'); node.children.append(RobotNode.new('Wait'))\n"
            "panel.set_root(node)\n"
            "for _ in range(3): root.update()\n"
            "assert len(panel.node_map) == 2, panel.node_map\n"
            "panel.tree.selection_set(str(id(node.children[0])))\n"
            "for _ in range(3): root.update()\n"
            "assert picked and picked[-1] is node.children[0]\n"
            "panel.on_appearance_change('Dark')\n"
            "root.destroy()\n"
            "print('PLAIN_TK_OK')\n"
        )
        result = subprocess.run([sys.executable, "-c", probe], capture_output=True,
                                text=True, timeout=60)
        self.assertIn("PLAIN_TK_OK", result.stdout, result.stderr)


class RootTeardownTests(unittest.TestCase):
    def test_cancel_pending_timers_leaves_no_after_jobs(self):
        """CustomTkinter schedules self-rescheduling .after() loops (DPI check,
        textbox scrollbar check) that CTk.destroy() never cancels. Left alone
        they fire in a later root's update() and spam 'invalid command name'
        bgerror output, so the fixtures must cancel them before destroy."""
        root = ctk.CTk()
        root.withdraw()
        with patch.object(snapshot_manager, "load_settings", _no_settings),              patch.object(snapshot_manager, "save_settings", lambda *a, **k: None):
            app = App(root, standalone=False)
            app.pack(fill="both", expand=True)
            pump(app)
            app.destroy()
        self.assertTrue(root.tk.call("after", "info"), "expected pending timers")
        cancel_pending_timers(root)
        self.assertEqual(root.tk.call("after", "info"), "")
        root.destroy()


class LauncherTests(unittest.TestCase):
    def test_launcher_mounts_config_builder(self):
        import launcher

        app = launcher.LauncherApp()
        app.withdraw()
        pump(app, 20)
        try:
            self.assertIn("config", app._tool_apps)
            config_app = app._tool_apps["config"]
            # The launcher loads the module under its own name, so compare by class name.
            self.assertEqual(type(config_app).__name__, "App")
            self.assertIsInstance(config_app, ctk.CTkFrame)
            self.assertTrue(config_app._confirm_discard())
            app._set_appearance("Dark")
            pump(app)
            self.assertEqual(ctk.get_appearance_mode(), "Dark")
            app._set_appearance("Light")
            pump(app)
        finally:
            for tool_app in app._tool_apps.values():
                shutdown = getattr(tool_app, "shutdown", None)
                if callable(shutdown):
                    shutdown()
            destroy_root(app)


if __name__ == "__main__":
    unittest.main(verbosity=1)

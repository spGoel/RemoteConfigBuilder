"""UI-level tests for the CustomTkinter launcher and Robot Builder tab.

Run:  python tests\\test_robot_builder_ui.py
Everything runs headlessly against a withdrawn root window. No SSH, no
remote calls, no TortoiseSVN.
"""

import os
import sys
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "robot_builder"))

import customtkinter as ctk  # noqa: E402

from tests.tk_teardown import destroy_root  # noqa: E402

from common import widgets  # noqa: E402
import main as robot_builder  # noqa: E402  (robot_builder/main.py)

RobotBuilderApp = robot_builder.RobotBuilderApp


def pump(widget, times=6):
    for _ in range(times):
        widget.update()


class RobotBuilderTabTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = ctk.CTk()
        cls.root.withdraw()
        widgets.init_styles(cls.root)

    @classmethod
    def tearDownClass(cls):
        destroy_root(cls.root)

    def setUp(self):
        # RobotBuilderApp is now a shell with Linux + Windows tabs; these tests
        # exercise the Linux tab, which kept every method of the old class.
        self.shell = RobotBuilderApp(self.root, standalone=False)
        self.shell.pack(fill="both", expand=True)
        self.app = self.shell.linux
        pump(self.shell)

    def tearDown(self):
        self.shell.destroy()

    # -- argument collection -------------------------------------------------

    def test_default_arguments(self):
        self.assertEqual(self.app._collected_args(), ["gli", "3L", "--platform", "--game"])

    def test_target_level_component_and_flags(self):
        self.app._target_var.set("QCOM")
        self.app._level_var.set("5L")
        self.app._component_var.set("Platform only")
        self.app._flag_vars["clean"].set(True)
        self.app._flag_vars["asan"].set(True)
        self.assertEqual(
            self.app._collected_args(),
            ["qcom", "5L", "--platform", "--clean", "--asan"],
        )

    def test_game_only_component(self):
        self.app._component_var.set("Game only")
        self.assertEqual(self.app._collected_args(), ["gli", "3L", "--game"])

    def test_all_flags_in_script_order(self):
        for var in self.app._flag_vars.values():
            var.set(True)
        args = self.app._collected_args()
        self.assertEqual(
            args[4:],
            ["--clean", "--showmode", "--production", "--robot", "--asan", "--tcmalloc"],
        )

    # -- script patching -----------------------------------------------------

    def test_patched_script_replaces_only_edited_urls(self):
        if not robot_builder._BUNDLED_SCRIPT.exists():
            self.skipTest("Linux_BuildScript.sh not present")
        self.app._level_var.set("5L")
        default_runtime = robot_builder._DEFAULT_URLS["5L"]["runtime"]
        custom = "https://svn.ali.global/nAble/Development/GDK5L/CustomRuntime"
        self.app._url_vars["5L"]["runtime"].set(custom)
        content = self.app._patched_script_bytes().decode("utf-8")
        self.assertIn(custom, content)
        self.assertNotIn(default_runtime, content)
        # Untouched URLs stay as they were.
        self.assertIn(robot_builder._DEFAULT_URLS["5L"]["platform"], content)

    def test_unchanged_urls_leave_script_identical(self):
        if not robot_builder._BUNDLED_SCRIPT.exists():
            self.skipTest("Linux_BuildScript.sh not present")
        # read_text() has always normalised CRLF to LF before upload; keep that.
        original = robot_builder._BUNDLED_SCRIPT.read_bytes().replace(b"\r\n", b"\n")
        self.assertEqual(self.app._patched_script_bytes(), original)

    # -- level switching -----------------------------------------------------

    def test_only_active_level_url_rows_are_visible(self):
        pump(self.app)
        self.assertTrue(self.app._url_frames["3L"].winfo_manager())
        self.assertFalse(self.app._url_frames["5L"].winfo_manager())
        self.app._level_var.set("AVL")
        self.app._on_level_change()
        pump(self.app)
        self.assertTrue(self.app._url_frames["AVL"].winfo_manager())
        self.assertFalse(self.app._url_frames["3L"].winfo_manager())

    def test_avl_has_gameplatform_row(self):
        self.assertIn("gameplatform", self.app._url_vars["AVL"])
        self.assertNotIn("runtime", self.app._url_vars["AVL"])

    # -- log and status ------------------------------------------------------

    def test_log_append_and_clear(self):
        self.app._log_append("[build] hello\n", "info")
        self.app._log_append("plain line\n")
        pump(self.app)
        self.assertIn("[build] hello", self.app._log_contents())
        self.assertIn("plain line", self.app._log_contents())
        self.app._clear_log()
        self.assertEqual(self.app._log_contents().strip(), "")

    def test_build_done_updates_buttons_and_status(self):
        self.app._build_active = True
        self.app._start_btn.configure(state="disabled")
        self.app._stop_btn.configure(state="normal")
        self.app._build_done(False)
        self.assertFalse(self.app._build_active)
        self.assertEqual(self.app._start_btn.cget("state"), "normal")
        self.assertEqual(self.app._stop_btn.cget("state"), "disabled")
        self.assertIn("failed", self.app._status_var.get())
        self.app._build_done(True)
        self.assertEqual(self.app._status_var.get(), "Build complete")

    def test_start_without_ip_shows_error_and_does_not_run(self):
        shown = []
        robot_builder.messagebox.showerror = lambda *a, **k: shown.append(a)
        self.app._ip_var.set("")
        self.app._start()
        self.assertEqual(len(shown), 1)
        self.assertFalse(self.app._build_active)

    def test_line_tags(self):
        tag = robot_builder.LinuxBuildTab._line_tag
        self.assertEqual(tag("=== BUILD COMPLETE ==="), "ok")
        self.assertEqual(tag("make: *** Error: failed"), "error")
        self.assertEqual(tag("warning: unused"), "warn")
        self.assertEqual(tag("[build] Connecting"), "info")
        self.assertEqual(tag("plain"), "")


class LauncherTests(unittest.TestCase):
    def test_launcher_mounts_every_tool(self):
        import launcher

        app = launcher.LauncherApp()
        app.withdraw()
        pump(app, 10)
        try:
            expected = {tool["key"] for tool in launcher.TOOLS if tool["available"]}
            self.assertEqual(set(app._tool_apps), expected)
            # The launcher loads the module under its own name, so compare by class name.
            self.assertEqual(type(app._tool_apps["builder"]).__name__, "RobotBuilderApp")
            self.assertIsInstance(app._tool_apps["builder"], ctk.CTkFrame)
            # Every tool tab exists in the tabview under its title.
            for tool in launcher.TOOLS:
                self.assertIsNotNone(app.tabview.tab(tool["title"]))
            app._set_appearance("Dark")
            pump(app)
            self.assertEqual(ctk.get_appearance_mode(), "Dark")
            app._set_appearance("Light")
        finally:
            for tool_app in app._tool_apps.values():
                shutdown = getattr(tool_app, "shutdown", None)
                if callable(shutdown):
                    shutdown()
            destroy_root(app)


if __name__ == "__main__":
    unittest.main(verbosity=1)

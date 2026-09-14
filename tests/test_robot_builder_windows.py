"""Tests for the Robot Builder Windows tab: planning logic plus headless UI.

Run from the repo root:
    python -m unittest tests.test_robot_builder_windows
Nothing here runs svn, cmake or PowerShell; filesystem probes are injected
and the settings file is redirected to a temp folder.
"""

import json
import os
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
sys.path.insert(0, os.path.join(REPO, "robot_builder"))

import customtkinter as ctk  # noqa: E402

from tests.tk_teardown import destroy_root  # noqa: E402

from common import widgets  # noqa: E402
from windows_build import models, pipeline  # noqa: E402
from windows_build.models import GameSource, Settings  # noqa: E402


def make_settings(**overrides) -> Settings:
    base = dict(
        workspace=r"C:\ws",
        runtime_url=models.DEFAULT_RUNTIME_URL,
        games=[
            GameSource("https://svn.ali.global/nAble/GDK_Sample_Games/GDK5L/Trunk/BuffaloStrike", "BuffaloStrike"),
        ],
        config="Debug",
        vs="2022",
        layout="Aggregate",
        action="Build + Install",
        skip_svn=False,
        force_regenerate=False,
    )
    base.update(overrides)
    return Settings(**base)


class FakeFS:
    """Filesystem probe with an explicit set of existing paths."""

    def __init__(self, existing=()):
        self.existing = {os.path.normcase(os.path.normpath(p)) for p in existing}

    def exists(self, path: str) -> bool:
        return os.path.normcase(os.path.normpath(path)) in self.existing


# --------------------------------------------------------------------------
# windows_build.models
# --------------------------------------------------------------------------


class FolderNameTests(unittest.TestCase):
    def test_last_segment_is_used(self):
        url = "https://svn.ali.global/nAble/GDK_Sample_Games/GDK5L/Trunk/BuffaloStrike"
        self.assertEqual(models.folder_name_for_url(url), "BuffaloStrike")

    def test_trailing_slash_ignored(self):
        self.assertEqual(models.folder_name_for_url("https://x/y/JokerPoker/"), "JokerPoker")

    def test_generic_tail_segments_are_skipped(self):
        self.assertEqual(
            models.folder_name_for_url("https://svn.ali.global/GDK_uslv/UberWinsStudio/C3/Game17_Family/sandbox"),
            "Game17_Family",
        )
        self.assertEqual(
            models.folder_name_for_url("https://h/OZ_Games/MonopolyRollTheDice/Game1/DevLines/nsw_multilink_001/source"),
            "nsw_multilink_001",
        )

    def test_trunk_is_generic(self):
        self.assertEqual(models.folder_name_for_url("https://h/repo/Wonder4/trunk"), "Wonder4")

    def test_empty_url_gives_empty_name(self):
        self.assertEqual(models.folder_name_for_url(""), "")


class SettingsPathTests(unittest.TestCase):
    def test_derived_folders_follow_vs_and_layout(self):
        s = make_settings(vs="2022", layout="Aggregate")
        self.assertEqual(s.runtime_dir, r"C:\ws\Runtime")
        self.assertEqual(s.games_dir, r"C:\ws\Games")
        self.assertEqual(s.build_dir, r"C:\ws\Build_2022")
        self.assertEqual(s.binaries_dir, r"C:\ws\Binaries_2022")
        s = make_settings(vs="2019", layout="Modular")
        self.assertEqual(s.build_dir, r"C:\ws\Build_2019_modular")
        self.assertEqual(s.binaries_dir, r"C:\ws\Binaries_2019_modular")

    def test_generator_string(self):
        self.assertEqual(make_settings(vs="2019").generator, "Visual Studio 16 2019")
        self.assertEqual(make_settings(vs="2022").generator, "Visual Studio 17 2022")

    def test_script_path_under_runtime(self):
        self.assertEqual(
            make_settings().script_path,
            r"C:\ws\Runtime\etc\scripts\build\create_workspace_gdk5L.ps1",
        )

    def test_game_dir(self):
        s = make_settings()
        self.assertEqual(s.game_dir(s.games[0]), r"C:\ws\Games\BuffaloStrike")

    def test_round_trip_json(self):
        s = make_settings(games=[GameSource("https://a/b/G1", "G1"), GameSource("https://a/b/G2", "Two")])
        data = s.to_dict()
        json.dumps(data)
        self.assertEqual(Settings.from_dict(data), s)

    def test_from_dict_tolerates_missing_and_unknown_keys(self):
        back = Settings.from_dict({"workspace": r"D:\x", "bogus": 1, "vs": "1999"})
        self.assertEqual(back.workspace, r"D:\x")
        self.assertEqual(back.vs, "2022")
        self.assertEqual(back.games, [])

    def test_save_and_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "settings.json")
            s = make_settings()
            models.save_settings(s, path)
            self.assertEqual(models.load_settings(path), s)
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("{not json")
            self.assertEqual(models.load_settings(path), Settings())


class ValidationTests(unittest.TestCase):
    def test_missing_workspace_is_an_error(self):
        problems = models.validate(make_settings(workspace=""))
        self.assertTrue(any("workspace" in p.lower() for p in problems))

    def test_duplicate_game_folders_rejected(self):
        s = make_settings(games=[GameSource("https://a/G", "Same"), GameSource("https://b/H", "Same")])
        self.assertTrue(any("Same" in p for p in models.validate(s)))

    def test_runtime_url_required_unless_skipping_svn(self):
        self.assertTrue(models.validate(make_settings(runtime_url="")))
        self.assertEqual(models.validate(make_settings(runtime_url="", skip_svn=True)), [])

    def test_bad_folder_name_rejected(self):
        self.assertTrue(models.validate(make_settings(games=[GameSource("https://a/G", "bad name?")])))


# --------------------------------------------------------------------------
# windows_build.pipeline
# --------------------------------------------------------------------------


class QuotingTests(unittest.TestCase):
    def test_ps_quote_doubles_single_quotes(self):
        self.assertEqual(pipeline.ps_quote("it's"), "'it''s'")
        self.assertEqual(pipeline.ps_quote(r"C:\a b"), r"'C:\a b'")


class SvnPlanningTests(unittest.TestCase):
    def test_checkout_when_no_working_copy(self):
        s = make_settings()
        stages = pipeline.svn_stages(s, FakeFS())
        self.assertEqual(len(stages), 2)
        self.assertEqual(stages[0].argv, ["svn", "checkout", s.runtime_url, r"C:\ws\Runtime"])
        self.assertEqual(stages[1].argv, ["svn", "checkout", s.games[0].url, r"C:\ws\Games\BuffaloStrike"])
        self.assertIsNone(stages[0].expected_url)

    def test_update_when_working_copy_exists(self):
        s = make_settings()
        stages = pipeline.svn_stages(s, FakeFS([r"C:\ws\Runtime\.svn"]))
        self.assertEqual(stages[0].argv, ["svn", "update", r"C:\ws\Runtime"])
        self.assertEqual(stages[0].expected_url, s.runtime_url)
        self.assertEqual(stages[0].check_dir, r"C:\ws\Runtime")

    def test_skip_svn_yields_nothing(self):
        self.assertEqual(pipeline.svn_stages(make_settings(skip_svn=True), FakeFS()), [])

    def test_games_without_url_are_ignored(self):
        s = make_settings(games=[GameSource("", "Blank")])
        self.assertEqual(len(pipeline.svn_stages(s, FakeFS())), 1)


class ConfigurePlanningTests(unittest.TestCase):
    def test_command_shape(self):
        stage = pipeline.configure_stage(make_settings())
        self.assertEqual(stage.argv[:5], ["pwsh", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command"])
        command = stage.argv[5]
        self.assertIn("& 'C:\\ws\\Runtime\\etc\\scripts\\build\\create_workspace_gdk5L.ps1'", command)
        self.assertIn("-Generator 'Visual Studio 17 2022'", command)
        self.assertIn("-BuildOutputPath 'C:\\ws\\Build_2022'", command)
        self.assertIn("-BinariesOutputPath 'C:\\ws\\Binaries_2022'", command)
        self.assertIn("-NoDefaultGamesRootDirectory", command)
        self.assertIn("-GameDirectories @('C:\\ws\\Games\\BuffaloStrike')", command)
        self.assertIn("-SuppressDeletionOfBuildOutputPath", command)
        self.assertIn("-SuppressDeletionOfBinariesOutputPath", command)
        self.assertNotIn("-UseModularBuild", command)
        self.assertNotIn("-BuildSolution", command)
        self.assertTrue(command.rstrip().endswith("exit $LASTEXITCODE"))
        self.assertEqual(stage.cwd, r"C:\ws\Runtime\etc\scripts\build")

    def test_force_regenerate_lets_script_delete(self):
        command = pipeline.configure_stage(make_settings(force_regenerate=True)).argv[5]
        self.assertNotIn("SuppressDeletion", command)

    def test_modular_and_2019(self):
        command = pipeline.configure_stage(make_settings(vs="2019", layout="Modular")).argv[5]
        self.assertIn("-Generator 'Visual Studio 16 2019'", command)
        self.assertIn("-UseModularBuild", command)
        self.assertIn("-BuildOutputPath 'C:\\ws\\Build_2019_modular'", command)

    def test_no_games_omits_game_directories(self):
        command = pipeline.configure_stage(make_settings(games=[])).argv[5]
        self.assertNotIn("-GameDirectories", command)
        self.assertIn("-NoDefaultGamesRootDirectory", command)

    def test_multiple_games_joined(self):
        s = make_settings(games=[GameSource("https://a/G1", "G1"), GameSource("https://a/G2", "G2")])
        self.assertIn("-GameDirectories @('C:\\ws\\Games\\G1','C:\\ws\\Games\\G2')",
                      pipeline.configure_stage(s).argv[5])

    def test_extra_arguments_appended_verbatim(self):
        command = pipeline.configure_stage(make_settings(extra_script_args="-Market aus -ShowSettings")).argv[5]
        self.assertIn("-Market aus -ShowSettings", command)


class BuildInstallPlanningTests(unittest.TestCase):
    def test_build_mirrors_script_with_parallel_msbuild(self):
        self.assertEqual(
            pipeline.build_stage(make_settings(config="Release")).argv,
            ["cmake", "--build", r"C:\ws\Build_2022", "--config", "Release", "--", "-r", "-m"],
        )

    def test_install(self):
        self.assertEqual(
            pipeline.install_stage(make_settings(config="Retail")).argv,
            ["cmake", "--install", r"C:\ws\Build_2022", "--config", "Retail"],
        )


class PlanTests(unittest.TestCase):
    def kinds(self, stages):
        return [s.kind for s in stages]

    def test_fresh_workspace_runs_everything(self):
        self.assertEqual(self.kinds(pipeline.plan(make_settings(), FakeFS())),
                         ["svn", "svn", "configure", "build", "install"])

    def test_existing_cache_skips_configure(self):
        fs = FakeFS([r"C:\ws\Runtime\.svn", r"C:\ws\Games\BuffaloStrike\.svn", r"C:\ws\Build_2022\CMakeCache.txt"])
        self.assertEqual(self.kinds(pipeline.plan(make_settings(action="Build"), fs)), ["svn", "svn", "build"])

    def test_force_regenerate_reconfigures_despite_cache(self):
        fs = FakeFS([r"C:\ws\Build_2022\CMakeCache.txt"])
        stages = pipeline.plan(make_settings(action="Build", force_regenerate=True, skip_svn=True), fs)
        self.assertEqual(self.kinds(stages), ["configure", "build"])

    def test_configure_only_always_configures(self):
        fs = FakeFS([r"C:\ws\Build_2022\CMakeCache.txt"])
        self.assertEqual(self.kinds(pipeline.plan(make_settings(action="Configure only", skip_svn=True), fs)),
                         ["configure"])

    def test_install_only(self):
        fs = FakeFS([r"C:\ws\Build_2022\CMakeCache.txt"])
        self.assertEqual(self.kinds(pipeline.plan(make_settings(action="Install only", skip_svn=True), fs)),
                         ["install"])

    def test_install_only_without_cache_is_a_planning_error(self):
        with self.assertRaises(pipeline.PlanError):
            pipeline.plan(make_settings(action="Install only", skip_svn=True), FakeFS())

    def test_cache_from_other_vs_does_not_count(self):
        fs = FakeFS([r"C:\ws\Build_2019\CMakeCache.txt"])
        self.assertEqual(self.kinds(pipeline.plan(make_settings(action="Build", skip_svn=True), fs)),
                         ["configure", "build"])

    def test_preview_lists_every_stage(self):
        text = pipeline.preview(pipeline.plan(make_settings(), FakeFS()))
        for needle in ("svn checkout", "create_workspace_gdk5L.ps1", "cmake --build", "cmake --install"):
            self.assertIn(needle, text)

    def test_preview_quotes_paths_with_spaces(self):
        text = pipeline.preview(pipeline.plan(make_settings(workspace=r"C:\my ws", skip_svn=True), FakeFS()))
        self.assertIn('"C:\\my ws\\Build_2022"', text)


# --------------------------------------------------------------------------
# Windows tab UI, inside the Robot Builder shell
# --------------------------------------------------------------------------


def pump(widget, times=6):
    for _ in range(times):
        widget.update()


class WindowsTabUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls._old_settings_file = models.SETTINGS_FILE
        models.SETTINGS_FILE = os.path.join(cls.tmp.name, "settings.json")
        cls.root = ctk.CTk()
        cls.root.withdraw()
        widgets.init_styles(cls.root)
        import main as robot_builder  # robot_builder/main.py
        cls.robot_builder = robot_builder

    @classmethod
    def tearDownClass(cls):
        destroy_root(cls.root)
        models.SETTINGS_FILE = cls._old_settings_file
        cls.tmp.cleanup()

    def setUp(self):
        self.shell = self.robot_builder.RobotBuilderApp(self.root, standalone=False)
        self.shell.pack(fill="both", expand=True)
        self.win = self.shell.windows
        pump(self.shell)

    def tearDown(self):
        self.shell.destroy()

    def test_shell_has_linux_and_windows_tabs(self):
        self.assertEqual(self.shell.tabs._name_list, ["Linux", "Windows"])
        self.assertEqual(type(self.shell.linux).__name__, "LinuxBuildTab")
        self.assertEqual(type(self.shell.windows).__name__, "WindowsBuildTab")

    def test_first_run_prefills_sample_games_and_disables_run(self):
        self.assertEqual(len(self.win.settings.games), len(models.DEFAULT_GAME_URLS))
        self.assertEqual(self.win.run_button.cget("state"), "disabled")
        self.assertIn("workspace", self.win.problem_banner.cget("text").lower())

    def test_plan_skips_configure_when_cache_exists(self):
        ws = os.path.join(self.tmp.name, "ws")
        os.makedirs(os.path.join(ws, "Build_2022"), exist_ok=True)
        with open(os.path.join(ws, "Build_2022", "CMakeCache.txt"), "w") as handle:
            handle.write("# cache\n")
        self.win.workspace_var.set(ws)
        self.win.skip_svn_var.set(True)
        self.win.action_var.set("Build")
        pump(self.shell)
        self.assertEqual([s.kind for s in self.win._stages], ["build"])
        self.assertIn("cmake --build", self.win.preview.get("1.0", "end-1c"))
        self.assertEqual(self.win.run_button.cget("state"), "normal")
        self.assertEqual(self.win.path_labels["build"].cget("text"), os.path.join(ws, "Build_2022"))

    def test_switching_vs_adds_configure_when_no_cache(self):
        ws = os.path.join(self.tmp.name, "ws2")
        os.makedirs(ws, exist_ok=True)
        self.win.workspace_var.set(ws)
        self.win.skip_svn_var.set(True)
        self.win.action_var.set("Build")
        self.win.vs_var.set("2019")
        pump(self.shell)
        self.assertEqual([s.kind for s in self.win._stages], ["configure", "build"])
        self.assertIn("Visual Studio 16 2019", self.win.preview.get("1.0", "end-1c"))

    def test_install_only_without_cache_shows_problem(self):
        ws = os.path.join(self.tmp.name, "ws3")
        os.makedirs(ws, exist_ok=True)
        self.win.workspace_var.set(ws)
        self.win.skip_svn_var.set(True)
        self.win.action_var.set("Install only")
        pump(self.shell)
        self.assertEqual(self.win._stages, [])
        self.assertIn("Install only", self.win.problem_banner.cget("text"))
        self.assertEqual(self.win.run_button.cget("state"), "disabled")

    def test_games_tree_add_and_remove(self):
        before = len(self.win.games_tree.tree.get_children())
        self.win.settings.games.append(GameSource.from_url("https://h/x/Game17_Family/sandbox"))
        self.win._refresh_games_tree()
        pump(self.shell)
        rows = self.win.games_tree.tree.get_children()
        self.assertEqual(len(rows), before + 1)
        self.assertEqual(self.win.games_tree.tree.item(rows[-1], "values")[0], "Game17_Family")
        self.win.games_tree.tree.selection_set(rows[-1])
        self.win._remove_game()
        self.assertEqual(len(self.win.games_tree.tree.get_children()), before)

    def test_shell_build_active_and_stop_are_safe_when_idle(self):
        self.assertFalse(self.shell._build_active)
        self.shell._stop()  # must not raise with nothing running
        self.assertFalse(self.shell.windows.running)

    def test_persist_writes_redirected_settings_file(self):
        self.win.workspace_var.set(self.tmp.name)
        self.win.persist()
        self.assertTrue(os.path.exists(models.SETTINGS_FILE))
        with open(models.SETTINGS_FILE, encoding="utf-8") as handle:
            data = json.load(handle)
        self.assertEqual(os.path.normcase(data["workspace"]), os.path.normcase(self.tmp.name))

    def test_appearance_change_does_not_raise(self):
        self.shell.on_appearance_change("Dark")
        pump(self.shell)
        self.shell.on_appearance_change("Light")


if __name__ == "__main__":
    unittest.main(verbosity=1)

"""Windows tab of Robot Builder — build a GDK5L Visual Studio workspace locally.

SVN checkout of Runtime + games  →  create_workspace_gdk5L.ps1  →
cmake --build  →  cmake --install. Planning lives in windows_build.pipeline,
execution in windows_build.runner; this module is only the widgets.
"""

import os
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import customtkinter as ctk  # noqa: E402

from common import theme, widgets  # noqa: E402
from common.widgets import Banner, Card, InfoButton, LogPane, PathRow, StatusPill, TreePane, font  # noqa: E402

import svn_browser  # noqa: E402
from windows_build import models, pipeline  # noqa: E402
from windows_build.models import GameSource, Settings  # noqa: E402
from windows_build.runner import PipelineRunner  # noqa: E402

POLL_MS = 80

_NEUTRAL = ("gray70", "gray35")
_NEUTRAL_HOVER = ("gray60", "gray45")


class GameDialog(ctk.CTkToplevel):
    """Add or edit one game: SVN URL plus the local folder name."""

    def __init__(self, master, game: GameSource = None, title: str = "Add game"):
        super().__init__(master)
        self.title(title)
        self.resizable(True, False)
        self.result = None
        self._folder_edited = bool(
            game and game.folder and game.folder != models.folder_name_for_url(game.url))

        self.url_var = tk.StringVar(value=game.url if game else "")
        self.folder_var = tk.StringVar(value=game.folder if game else "")

        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=16, pady=14)
        body.columnconfigure(1, weight=1)

        ctk.CTkLabel(body, text="SVN URL", font=font("body"), anchor="w").grid(
            row=0, column=0, sticky="w", pady=(0, 8), padx=(0, 12))
        url_row = ctk.CTkFrame(body, fg_color="transparent")
        url_row.grid(row=0, column=1, sticky="ew", pady=(0, 8))
        url_row.columnconfigure(0, weight=1)
        url_entry = ctk.CTkEntry(url_row, textvariable=self.url_var, width=520, font=font("body"), height=30)
        url_entry.grid(row=0, column=0, sticky="ew")
        ctk.CTkButton(
            url_row, text="Browse...", width=90, height=30, font=font("body"),
            fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER, text_color=theme.BODY_FG,
            command=lambda: svn_browser.browse_svn_url(self, self.url_var, "game"),
        ).grid(row=0, column=1, padx=(8, 0))

        ctk.CTkLabel(body, text="Folder name", font=font("body"), anchor="w").grid(
            row=1, column=0, sticky="w", padx=(0, 12))
        folder_entry = ctk.CTkEntry(body, textvariable=self.folder_var, font=font("body"), height=30)
        folder_entry.grid(row=1, column=1, sticky="ew")
        ctk.CTkLabel(
            body,
            text="Checked out to <workspace>\\Games\\<folder name>. Derived from the URL until you edit it.",
            font=font("small"), text_color=theme.MUTED_FG, anchor="w",
        ).grid(row=2, column=1, sticky="w", pady=(4, 0))

        self.banner = Banner(body, colour=theme.ERR_FG)
        self.banner.grid(row=3, column=0, columnspan=2, sticky="w", pady=(8, 0))

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=4, column=0, columnspan=2, sticky="e", pady=(14, 0))
        ctk.CTkButton(buttons, text="Cancel", width=90, fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER,
                      text_color=theme.BODY_FG, command=self.destroy).pack(side="right")
        ctk.CTkButton(buttons, text="OK", width=90, fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                      command=self._accept).pack(side="right", padx=(0, 8))

        self.url_var.trace_add("write", self._on_url_change)
        folder_entry.bind("<KeyRelease>", lambda _e: setattr(self, "_folder_edited", True))
        self.bind("<Return>", lambda _e: self._accept())
        self.bind("<Escape>", lambda _e: self.destroy())

        self.transient(master.winfo_toplevel())
        self.after(50, lambda: (self.grab_set(), url_entry.focus_set()))

    def _on_url_change(self, *_):
        if not self._folder_edited:
            self.folder_var.set(models.folder_name_for_url(self.url_var.get()))

    def _accept(self):
        url = self.url_var.get().strip()
        folder = self.folder_var.get().strip()
        if not url:
            self.banner.show("Enter the SVN URL.")
            return
        if not folder:
            self.banner.show("Enter a folder name.")
            return
        if not models.FOLDER_NAME_RE.match(folder):
            self.banner.show("Folder name may only contain letters, digits, '_', '-' and '.'.")
            return
        self.result = GameSource(url, folder)
        self.destroy()


class WindowsBuildTab(ctk.CTkFrame):
    """Sources + options on the left, plan and live output on the right."""

    def __init__(self, master):
        super().__init__(master, fg_color="transparent", corner_radius=0)
        if not widgets.FONTS:
            widgets.init_styles(self.winfo_toplevel())

        first_run = not os.path.exists(models.SETTINGS_FILE)
        self.settings = models.default_settings() if first_run else models.load_settings()
        self.runner = PipelineRunner()
        self._stages = []
        self._suspend_preview = False
        self.powershell = models.find_powershell()

        self._build_vars()
        self._build_layout()
        self._load_into_widgets()
        self._refresh_preview()
        self._check_tools()

    # ------------------------------------------------------------------
    # Public surface used by the Robot Builder shell / launcher
    # ------------------------------------------------------------------

    @property
    def running(self) -> bool:
        return self.runner.running

    def stop(self) -> None:
        if self.runner.running:
            self.status.set("warn", "Cancelling…")
            self.runner.cancel()

    def persist(self) -> None:
        try:
            models.save_settings(self._settings())
        except OSError:
            pass

    def on_appearance_change(self, _mode: str) -> None:
        theme.style_treeview(self)
        self.games_tree.apply_tags()

    # ------------------------------------------------------------------
    # Variables
    # ------------------------------------------------------------------

    def _build_vars(self) -> None:
        self.workspace_var = tk.StringVar()
        self.runtime_url_var = tk.StringVar()
        self.skip_svn_var = tk.BooleanVar()
        self.config_var = tk.StringVar()
        self.vs_var = tk.StringVar()
        self.layout_var = tk.StringVar()
        self.action_var = tk.StringVar()
        self.force_var = tk.BooleanVar()
        self.extra_var = tk.StringVar()
        for var in (self.workspace_var, self.runtime_url_var, self.skip_svn_var, self.config_var,
                    self.vs_var, self.layout_var, self.action_var, self.force_var, self.extra_var):
            var.trace_add("write", lambda *_: self._on_setting_change())

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_layout(self) -> None:
        self.columnconfigure(0, weight=5, uniform="cols")
        self.columnconfigure(1, weight=6, uniform="cols")
        self.rowconfigure(1, weight=1)

        caption = ctk.CTkFrame(self, fg_color="transparent")
        caption.grid(row=0, column=0, columnspan=2, sticky="ew", padx=12, pady=(4, 4))
        caption.columnconfigure(0, weight=1)
        ctk.CTkLabel(
            caption,
            text="Local GDK5L workspace:  SVN checkout  →  create_workspace_gdk5L.ps1  →  cmake --build  →  cmake --install",
            font=font("small"), text_color=theme.MUTED_FG, anchor="w",
        ).grid(row=0, column=0, sticky="w")
        tools = ctk.CTkFrame(caption, fg_color="transparent")
        tools.grid(row=0, column=1, sticky="e")
        self.tool_pills = {}
        for name in ("svn", "cmake", self.powershell):
            pill = StatusPill(tools, name, "idle")
            pill.pack(side="left", padx=(0, 14))
            self.tool_pills[name] = pill

        left = ctk.CTkScrollableFrame(self, fg_color="transparent")
        left.grid(row=1, column=0, sticky="nsew", padx=(12, 6), pady=(0, 4))
        left.columnconfigure(0, weight=1)
        self._build_sources(left)
        self._build_options(left)

        right = ctk.CTkFrame(self, fg_color="transparent")
        right.grid(row=1, column=1, sticky="nsew", padx=(6, 12), pady=(0, 4))
        right.columnconfigure(0, weight=1)
        right.rowconfigure(1, weight=1)
        self._build_run(right)

    def _build_sources(self, parent) -> None:
        card = Card(parent, title="Workspace and sources")
        card.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        body = card.body
        body.columnconfigure(0, weight=1)

        PathRow(
            body, "Workspace folder", self.workspace_var, kind="dir",
            title="Choose the folder where sources are checked out and built",
            info=models.FIELD_HELP["workspace"],
        ).grid(row=0, column=0, sticky="ew", pady=(0, 6))

        row = ctk.CTkFrame(body, fg_color="transparent")
        row.grid(row=1, column=0, sticky="ew", pady=(0, 6))
        row.columnconfigure(2, weight=1)
        ctk.CTkLabel(row, text="Runtime SVN URL", width=128, anchor="w", font=font("body")).grid(
            row=0, column=0, sticky="w", padx=(0, 2))
        InfoButton(row, models.FIELD_HELP["runtime_url"]).grid(row=0, column=1, sticky="w", padx=(0, 8))
        ctk.CTkEntry(row, textvariable=self.runtime_url_var, font=font("body"), height=30).grid(
            row=0, column=2, sticky="ew")
        ctk.CTkButton(
            row, text="Browse...", width=90, height=30, font=font("body"),
            fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER, text_color=theme.BODY_FG,
            command=lambda: svn_browser.browse_svn_url(self, self.runtime_url_var, "Runtime"),
        ).grid(row=0, column=3, padx=(8, 0))

        games_head = ctk.CTkFrame(body, fg_color="transparent")
        games_head.grid(row=2, column=0, sticky="ew", pady=(6, 2))
        games_head.columnconfigure(2, weight=1)
        ctk.CTkLabel(games_head, text="Games", font=font("heading"), anchor="w").grid(
            row=0, column=0, sticky="w", padx=(0, 2))
        InfoButton(games_head, models.FIELD_HELP["games"]).grid(row=0, column=1, sticky="w")
        self.games_count = ctk.CTkLabel(games_head, text="", font=font("small"),
                                        text_color=theme.MUTED_FG, anchor="e")
        self.games_count.grid(row=0, column=2, sticky="e")

        self.games_tree = TreePane(
            body,
            columns=[
                ("folder", "Folder", 150, "w", False),
                ("url", "SVN URL", 420, "w", True),
            ],
            height=5,
        )
        self.games_tree.grid(row=3, column=0, sticky="ew")
        self.games_tree.tree.bind("<Double-1>", lambda _e: self._edit_game())
        self.games_tree.tree.bind("<Delete>", lambda _e: self._remove_game())

        buttons = ctk.CTkFrame(body, fg_color="transparent")
        buttons.grid(row=4, column=0, sticky="ew", pady=(6, 0))
        for text, command in (
            ("Add…", self._add_game),
            ("Edit…", self._edit_game),
            ("Remove", self._remove_game),
            ("Reset to sample games", self._reset_games),
        ):
            ctk.CTkButton(buttons, text=text, command=command, width=90, height=28, font=font("small"),
                          fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER).pack(side="left", padx=(0, 8))

        skip = ctk.CTkFrame(body, fg_color="transparent")
        skip.grid(row=5, column=0, sticky="w", pady=(10, 0))
        ctk.CTkCheckBox(skip, text="Skip SVN (use the sources already on disk)", variable=self.skip_svn_var,
                        font=font("body"), fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER).pack(side="left")
        InfoButton(skip, models.FIELD_HELP["skip_svn"]).pack(side="left", padx=(6, 0))

    def _radio_row(self, parent, row, title, help_key, values, variable) -> None:
        """One compact row: label + (i) on the left, the choices side by side."""
        head = ctk.CTkFrame(parent, fg_color="transparent")
        head.grid(row=row, column=0, sticky="w", pady=2)
        ctk.CTkLabel(head, text=title, width=112, anchor="w", font=font("body")).pack(side="left")
        InfoButton(head, models.FIELD_HELP[help_key]).pack(side="left", padx=(0, 6))
        choices = ctk.CTkFrame(parent, fg_color="transparent")
        choices.grid(row=row, column=1, sticky="w", pady=2)
        for value in values:
            ctk.CTkRadioButton(
                choices, text=value, variable=variable, value=value, font=font("body"),
                fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
                radiobutton_width=16, radiobutton_height=16, width=0,
            ).pack(side="left", padx=(0, 14))

    def _build_options(self, parent) -> None:
        card = Card(parent, title="Options")
        card.grid(row=1, column=0, sticky="ew", pady=(0, 10))
        body = card.body
        body.columnconfigure(1, weight=1)

        self._radio_row(body, 0, "Configuration", "config", models.CONFIGS, self.config_var)
        self._radio_row(body, 1, "Visual Studio", "vs", list(models.VS_CHOICES), self.vs_var)
        self._radio_row(body, 2, "Layout", "layout", models.LAYOUTS, self.layout_var)
        self._radio_row(body, 3, "Action", "action", models.ACTIONS, self.action_var)

        force = ctk.CTkFrame(body, fg_color="transparent")
        force.grid(row=4, column=0, columnspan=2, sticky="w", pady=(6, 0))
        ctk.CTkCheckBox(force, text="Force regenerate (delete Build and Binaries folders, run the script again)",
                        variable=self.force_var, font=font("body"), checkbox_width=18, checkbox_height=18,
                        fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER).pack(side="left")
        InfoButton(force, models.FIELD_HELP["force_regenerate"]).pack(side="left", padx=(6, 0))

        extra_head = ctk.CTkFrame(body, fg_color="transparent")
        extra_head.grid(row=5, column=0, sticky="w", pady=(6, 0))
        ctk.CTkLabel(extra_head, text="Extra script args", width=112, anchor="w", font=font("body")).pack(side="left")
        InfoButton(extra_head, models.FIELD_HELP["extra_script_args"]).pack(side="left", padx=(0, 6))
        ctk.CTkEntry(body, textvariable=self.extra_var, font=font("mono"), height=28,
                     placeholder_text="-Market usa   (optional)").grid(row=5, column=1, sticky="ew", pady=(6, 0))

        # Derived output paths: the three the user actually opens. Runtime and
        # Games folders are implied by the workspace and shown in the plan.
        paths = ctk.CTkFrame(body, fg_color=theme.SUNKEN_BG, corner_radius=8)
        paths.grid(row=6, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        paths.columnconfigure(1, weight=1)
        self.path_labels = {}
        for index, (key, label) in enumerate((("build", "Build tree"), ("binaries", "Install prefix"),
                                              ("solution", "Solution"))):
            ctk.CTkLabel(paths, text=label, width=104, anchor="w", font=font("small"),
                         text_color=theme.MUTED_FG).grid(row=index, column=0, sticky="w",
                                                         padx=(10, 4), pady=(4 if index == 0 else 0, 4 if index == 2 else 0))
            value = ctk.CTkLabel(paths, text="", anchor="w", font=font("mono_small"))
            value.grid(row=index, column=1, sticky="w", padx=(0, 10))
            self.path_labels[key] = value

    def _build_run(self, parent) -> None:
        top = Card(parent, title="Plan")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        top.body.columnconfigure(0, weight=1)

        self.problem_banner = Banner(top.body, colour=theme.ERR_FG)
        self.problem_banner.grid(row=0, column=0, sticky="w", pady=(0, 6))

        # Kept short on purpose: the plan is a summary, the log below gets the room.
        self.preview = ctk.CTkTextbox(top.body, height=72, wrap="none", font=font("mono_small"),
                                      fg_color=theme.SUNKEN_BG, corner_radius=8, border_width=0)
        self.preview.grid(row=1, column=0, sticky="ew")
        self.preview.configure(state="disabled")

        buttons = ctk.CTkFrame(top.body, fg_color="transparent")
        buttons.grid(row=2, column=0, sticky="ew", pady=(6, 0))
        buttons.columnconfigure(3, weight=1)
        self.run_button = ctk.CTkButton(buttons, text="▶  Run", command=self._run, width=110, height=30,
                                        font=font("heading"), fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER)
        self.run_button.grid(row=0, column=0, padx=(0, 8))
        self.cancel_button = ctk.CTkButton(buttons, text="Cancel", command=self.stop, width=80, height=30,
                                           font=font("body"), fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER,
                                           text_color=theme.BODY_FG, state="disabled")
        self.cancel_button.grid(row=0, column=1, padx=(0, 8))
        ctk.CTkButton(buttons, text="Copy plan", command=self._copy_plan, width=90, height=30, font=font("body"),
                      fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER, text_color=theme.BODY_FG).grid(row=0, column=2)
        # Status shares the button row instead of taking a row of its own.
        self.status = StatusPill(buttons, "Ready.", "idle")
        self.status.grid(row=0, column=3, sticky="w", padx=(14, 8))
        ctk.CTkButton(buttons, text="Open Build folder",
                      command=lambda: widgets.open_in_explorer(self._settings().build_dir),
                      width=130, height=30, font=font("body"), fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER,
                      text_color=theme.BODY_FG).grid(row=0, column=4, padx=(8, 0))
        ctk.CTkButton(buttons, text="Open Binaries",
                      command=lambda: widgets.open_in_explorer(self._settings().binaries_dir),
                      width=110, height=30, font=font("body"), fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER,
                      text_color=theme.BODY_FG).grid(row=0, column=5, padx=(8, 0))

        log_card = Card(parent, title="Output")
        log_card.grid(row=1, column=0, sticky="nsew")
        log_card.body.rowconfigure(0, weight=1)
        log_card.body.columnconfigure(0, weight=1)
        self.log = LogPane(log_card.body, height=200)
        self.log.grid(row=0, column=0, sticky="nsew")

    # ------------------------------------------------------------------
    # Settings <-> widgets
    # ------------------------------------------------------------------

    def _load_into_widgets(self) -> None:
        s = self.settings
        self._suspend_preview = True
        self.workspace_var.set(s.workspace)
        self.runtime_url_var.set(s.runtime_url)
        self.skip_svn_var.set(s.skip_svn)
        self.config_var.set(s.config)
        self.vs_var.set(s.vs)
        self.layout_var.set(s.layout)
        self.action_var.set(s.action)
        self.force_var.set(s.force_regenerate)
        self.extra_var.set(s.extra_script_args)
        self._suspend_preview = False
        self._refresh_games_tree()

    def _settings(self) -> Settings:
        workspace = self.workspace_var.get().strip()
        return Settings(
            workspace=os.path.normpath(workspace) if workspace else "",
            runtime_url=self.runtime_url_var.get().strip(),
            games=list(self.settings.games),
            config=self.config_var.get(),
            vs=self.vs_var.get(),
            layout=self.layout_var.get(),
            action=self.action_var.get(),
            skip_svn=self.skip_svn_var.get(),
            force_regenerate=self.force_var.get(),
            extra_script_args=self.extra_var.get().strip(),
        )

    def _on_setting_change(self) -> None:
        if not self._suspend_preview:
            self._refresh_preview()

    def _refresh_games_tree(self) -> None:
        tree = self.games_tree.tree
        tree.delete(*tree.get_children())
        for index, game in enumerate(self.settings.games):
            tree.insert("", "end", iid=str(index), values=(game.folder, game.url),
                        tags=("stripe",) if index % 2 else ())
        count = len(self.settings.games)
        self.games_count.configure(
            text="{} game{}".format(count, "" if count == 1 else "s") if count else "no games")
        self._refresh_preview()

    def _selected_game_index(self):
        selection = self.games_tree.tree.selection()
        return int(selection[0]) if selection else None

    def _add_game(self) -> None:
        dialog = GameDialog(self, title="Add game")
        self.wait_window(dialog)
        if dialog.result:
            self.settings.games.append(dialog.result)
            self._refresh_games_tree()

    def _edit_game(self) -> None:
        index = self._selected_game_index()
        if index is None:
            return
        dialog = GameDialog(self, self.settings.games[index], title="Edit game")
        self.wait_window(dialog)
        if dialog.result:
            self.settings.games[index] = dialog.result
            self._refresh_games_tree()
            self.games_tree.tree.selection_set(str(index))

    def _remove_game(self) -> None:
        index = self._selected_game_index()
        if index is None:
            return
        del self.settings.games[index]
        self._refresh_games_tree()

    def _reset_games(self) -> None:
        self.settings.games = [GameSource.from_url(u) for u in models.DEFAULT_GAME_URLS]
        self._refresh_games_tree()

    # ------------------------------------------------------------------
    # Preview and validation
    # ------------------------------------------------------------------

    def _refresh_preview(self) -> None:
        settings = self._settings()
        if settings.workspace:
            self.path_labels["build"].configure(text=settings.build_dir)
            self.path_labels["binaries"].configure(text=settings.binaries_dir)
            self.path_labels["solution"].configure(text=settings.solution_path)
        else:
            for label in self.path_labels.values():
                label.configure(text="—")

        problems = models.validate(settings)
        text = ""
        if not problems:
            try:
                self._stages = pipeline.plan(settings, pipeline.RealFS, self.powershell)
                text = pipeline.preview(self._stages)
                if pipeline.cache_exists(settings) and not settings.force_regenerate \
                        and settings.action not in ("Configure only", "Install only"):
                    text = ("# Existing CMakeCache.txt found: configure is skipped "
                            "(tick Force regenerate to redo it).\n\n") + text
            except pipeline.PlanError as exc:
                problems = [str(exc)]
                self._stages = []
        else:
            self._stages = []
        self.problem_banner.show("\n".join(problems))
        self.preview.configure(state="normal")
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", text)
        self.preview.configure(state="disabled")
        if not self.runner.running:
            self.run_button.configure(state="normal" if self._stages else "disabled")

    def _check_tools(self) -> None:
        for name, pill in self.tool_pills.items():
            path = models.find_tool(name)
            pill.set("ok" if path else "error", name if path else "{} not found".format(name))
            if path:
                widgets.Tooltip(pill, path)

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    def _run(self) -> None:
        if self.runner.running:
            return
        settings = self._settings()
        self._refresh_preview()
        if not self._stages:
            return
        if settings.force_regenerate and any(s.kind == "configure" for s in self._stages):
            if not messagebox.askyesno(
                "Force regenerate",
                "The workspace script will DELETE and recreate:\n\n  {}\n  {}\n\nContinue?".format(
                    settings.build_dir, settings.binaries_dir),
                icon="warning", parent=self,
            ):
                return
        self.settings = settings
        self.persist()

        self.log.clear()
        self.log.append("GDK5L Windows build — {} stage(s)".format(len(self._stages)), "banner")
        self.log.append("Workspace: {}".format(settings.workspace), "info")
        self.log.append("")
        self.run_button.configure(state="disabled")
        self.cancel_button.configure(state="normal")
        self.status.set("idle", "Starting…")
        self.runner.start(self._stages)
        self.after(POLL_MS, self._poll)

    def _poll(self) -> None:
        try:
            while True:
                message = self.runner.queue.get_nowait()
                self._handle(message)
        except Exception as exc:  # queue.Empty, or a handler error we must not swallow silently
            if exc.__class__.__name__ != "Empty":
                self.log.append("UI error: {!r}".format(exc), "error")
        if self.runner.running or not self.runner.queue.empty():
            self.after(POLL_MS, self._poll)

    def _handle(self, message) -> None:
        kind = message[0]
        if kind == "stage":
            _, index, stage = message
            self.log.append("")
            self.log.append("━━━ [{}/{}] {} ━━━".format(index + 1, len(self._stages), stage.title), "banner")
            for note in stage.notes:
                self.log.append("    " + note, "warn")
            self.log.append("> " + " ".join(pipeline.shell_quote(a) for a in stage.argv), "command")
            self.status.set("idle", "Running {} of {}: {}".format(index + 1, len(self._stages), stage.title))
        elif kind == "line":
            _, tag, text = message
            self.log.append(text, tag)
        elif kind == "result":
            _, index, code = message
            title = self._stages[index].title if index < len(self._stages) else "stage"
            self.log.append("─── {} exited with code {} ───".format(title, code), "ok" if code == 0 else "error")
        elif kind == "finished":
            _, ok, summary = message
            self.log.append("")
            self.log.append(summary, "done" if ok else "error")
            self.status.set("ok" if ok else "error", summary)
            self.cancel_button.configure(state="disabled")
            self._refresh_preview()  # a fresh cache may now exist, or a checkout may have appeared

    # ------------------------------------------------------------------
    # Misc
    # ------------------------------------------------------------------

    def _copy_plan(self) -> None:
        text = self.preview.get("1.0", "end-1c")
        if text:
            self.clipboard_clear()
            self.clipboard_append(text)
            self.status.set("ok", "Plan copied to clipboard.")

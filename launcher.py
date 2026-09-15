"""
Aristocrat Robot Tools - tabbed top-level launcher.
Each tool runs inside one shared CustomTkinter window instead of opening a
new process/window. Tools that have not been ported yet still mount as plain
tkinter frames inside their tab.
"""
import importlib.util
import sys
import tkinter as tk
from tkinter import messagebox
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

try:
    import customtkinter as ctk
except ImportError:  # pragma: no cover - reported by launch.bat
    print("customtkinter is not installed. Run:  python -m pip install -r requirements.txt")
    raise

from common import perf, theme, widgets, window
from common.perf import StackedTabview
from common.widgets import font

# Palette kept for tools that still use the plain-tk look.
C_ACCENT = "#5B3EA6"
C_ACCENT_L = "#7457C4"
C_BG = "#F3F0FA"
C_SURFACE = "#FFFFFF"
C_TEXT = "#1A1820"
C_MUTED = "#5C5870"
C_WHITE = "#FFFFFF"


TOOLS = [
    {
        "key": "builder",
        "title": "Robot Builder",
        "subtitle": "Build 3L / 5L / AVL robots on a remote Linux machine, or a GDK5L Windows workspace locally",
        "script": BASE_DIR / "robot_builder" / "main.py",
        "class_name": "RobotBuilderApp",
        "available": True,
    },
    {
        "key": "config",
        "title": "Config Builder",
        "subtitle": "Compose, edit, preview, and save configurable robot XML",
        "script": BASE_DIR / "robot_config_builder" / "main.py",
        "class_name": "App",
        "available": True,
    },
    {
        "key": "memory",
        "title": "Memory Profiler",
        "subtitle": "Live and historical Robot meter charts",
        "script": BASE_DIR / "memory_profiling" / "main.py",
        "class_name": "MemoryProfilingTab",
        "available": True,
    },
    {
        "key": "asan",
        "title": "ASAN Report",
        "subtitle": "Browse and inspect AddressSanitizer diagnostics",
        "script": BASE_DIR / "asan_report" / "main.py",
        "class_name": "AsanReportTab",
        "available": True,
    },
    {
        "key": "tcmalloc",
        "title": "tcMalloc Report",
        "subtitle": "Browse heap snapshots and generate pprof PDF reports",
        "script": BASE_DIR / "tcmalloc_report" / "main.py",
        "class_name": "TcMallocReportTab",
        "available": True,
    },
]


def _load_tool_class(tool: dict):
    script = tool.get("script")
    if not script or not Path(script).exists():
        raise FileNotFoundError(f"Script not found: {script}")

    script = Path(script)
    tool_dir = str(script.parent)
    if tool_dir not in sys.path:
        sys.path.insert(0, tool_dir)

    module_name = f"robot_tools_{tool['key']}_main"
    spec = importlib.util.spec_from_file_location(module_name, script)
    if spec is None or spec.loader is None:
        raise ImportError(f"Could not load module from {script}")

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return getattr(module, tool["class_name"])


class LauncherApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")
        widgets.init_styles(self)

        self.title("Aristocrat Robot Tools")
        self._tool_apps = {}

        self._apply_initial_geometry()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _apply_initial_geometry(self):
        """Open maximised on the monitor under the pointer, at its real
        resolution. See common/window.py for why this needs Win32 calls
        rather than Tk's own (DPI-virtualized, primary-monitor-only) screen
        metrics."""
        window.open_maximised(self)

    # ------------------------------------------------------------------
    # Layout
    # ------------------------------------------------------------------

    def _build_ui(self):
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)
        self._build_header()
        self._build_tabs()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=theme.ACCENT, corner_radius=0)
        hdr.grid(row=0, column=0, sticky="ew")
        hdr.columnconfigure(0, weight=1)

        titles = ctk.CTkFrame(hdr, fg_color="transparent")
        titles.grid(row=0, column=0, sticky="w", padx=18, pady=(12, 12))
        ctk.CTkLabel(
            titles, text="Aristocrat Robot Tools", font=font("title"),
            text_color="#FFFFFF", anchor="w",
        ).pack(anchor="w")
        ctk.CTkLabel(
            titles,
            text="Build robots, edit robot.xml, and monitor runs from one workspace",
            font=font("small"), text_color="#C4B4F4", anchor="w",
        ).pack(anchor="w")

        self.appearance = ctk.CTkSegmentedButton(
            hdr, values=["Light", "Dark", "System"], command=self._set_appearance,
            font=font("small"),
            selected_color="#FFFFFF", selected_hover_color="#EFEAFB",
            unselected_color=theme.ACCENT_HOVER, unselected_hover_color=("#7C5CD6", "#5B3EA6"),
            text_color=(theme.ACCENT[0], "#FFFFFF"),
        )
        self.appearance.set("System")
        self.appearance.grid(row=0, column=1, sticky="e", padx=18)

    def _build_tabs(self):
        shell = ctk.CTkFrame(self, fg_color="transparent")
        shell.grid(row=1, column=0, sticky="nsew", padx=10, pady=(6, 10))
        shell.columnconfigure(0, weight=1)
        shell.rowconfigure(0, weight=1)

        # StackedTabview raises tabs instead of unmapping/remapping them; see common/perf.py.
        self.tabview = StackedTabview(
            shell,
            corner_radius=10,
            fg_color=theme.CARD_BG,
            segmented_button_fg_color=theme.SUNKEN_BG,
            segmented_button_selected_color=theme.ACCENT,
            segmented_button_selected_hover_color=theme.ACCENT_HOVER,
            segmented_button_unselected_color=theme.SUNKEN_BG,
            segmented_button_unselected_hover_color=theme.BORDER,
            text_color=theme.BODY_FG,
            text_color_disabled=theme.MUTED_FG,
            anchor="w",
        )
        self.tabview.grid(row=0, column=0, sticky="nsew")
        self.tabview._segmented_button.configure(font=font("heading"))

        for tool in TOOLS:
            tab = self.tabview.add(tool["title"])
            tab.columnconfigure(0, weight=1)
            tab.rowconfigure(2, weight=1)
            content = self._tab_content(tab, tool)
            if tool["available"]:
                self._mount_tool(content, tool)
            else:
                self._placeholder(content, tool)

    def _tab_content(self, tab, tool: dict):
        header = ctk.CTkFrame(tab, fg_color="transparent")
        header.grid(row=0, column=0, sticky="ew", padx=14, pady=(6, 4))
        ctk.CTkLabel(
            header, text=tool["title"], font=font("heading"), anchor="w",
        ).pack(side="left")
        ctk.CTkLabel(
            header, text=tool.get("subtitle", ""), font=font("small"),
            text_color=theme.MUTED_FG, anchor="w",
        ).pack(side="left", padx=(14, 0))

        ctk.CTkFrame(tab, fg_color=theme.BORDER, height=1, corner_radius=0).grid(
            row=1, column=0, sticky="ew", padx=14)

        content = ctk.CTkFrame(tab, fg_color="transparent")
        content.grid(row=2, column=0, sticky="nsew", padx=4, pady=(4, 4))
        return content

    def _mount_tool(self, tab, tool: dict):
        try:
            tool_class = _load_tool_class(tool)
            app = tool_class(tab, standalone=False)
            app.pack(fill=tk.BOTH, expand=True)
            self._tool_apps[tool["key"]] = app
        except Exception as exc:
            self._error_tab(tab, tool, exc)

    def _placeholder(self, tab, tool: dict):
        ctk.CTkLabel(
            tab, text="Coming soon.", font=font("body"),
            text_color=theme.MUTED_FG, anchor="w",
        ).pack(fill=tk.X, padx=28, pady=24)

    def _error_tab(self, tab, tool: dict, exc: Exception):
        body = ctk.CTkFrame(tab, fg_color=theme.CARD_BG, corner_radius=10)
        body.pack(fill=tk.BOTH, expand=True, padx=14, pady=14)
        ctk.CTkLabel(
            body, text=f"{tool['title']} could not be loaded",
            font=font("heading"), text_color=theme.ERR_FG, anchor="w",
        ).pack(fill=tk.X, padx=18, pady=(18, 6))
        ctk.CTkLabel(
            body, text=str(exc), font=font("mono_small"), justify=tk.LEFT,
            anchor="w", wraplength=900,
        ).pack(fill=tk.X, padx=18, pady=(0, 18))

    # ------------------------------------------------------------------
    # Behaviour
    # ------------------------------------------------------------------

    def _set_appearance(self, mode: str):
        # perf.set_appearance_mode unmaps hidden tabs first so Tk does not
        # repaint them; they remap on their next selection.
        perf.set_appearance_mode(mode)
        # ttk has no notion of appearance mode, so themed trees need repainting.
        theme.style_treeview(self)
        for app in self._tool_apps.values():
            hook = getattr(app, "on_appearance_change", None)
            if callable(hook):
                hook(mode)

    def _on_close(self):
        config_app = self._tool_apps.get("config")
        if config_app is not None and not config_app._confirm_discard():
            return

        builder_app = self._tool_apps.get("builder")
        if builder_app is not None and getattr(builder_app, "_build_active", False):
            if not messagebox.askyesno(
                "Build Running",
                "A robot build is still running. Stop it and close the tools?",
                parent=self,
            ):
                return
            builder_app._stop()

        memory_app = self._tool_apps.get("memory")
        if memory_app is not None:
            memory_app.shutdown()

        self.destroy()


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()

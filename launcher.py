"""
Aristocrat Robot Tools - tabbed top-level launcher.
Each tool runs inside one shared window instead of opening a new process/window.
"""
import importlib.util
import sys
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path

BASE_DIR = Path(__file__).parent

# Palette shared with the tools.
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
        "subtitle": "Build 3L / 5L / AVL robots on a remote Linux machine",
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


class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Aristocrat Robot Tools")
        self.geometry("1320x820")
        self.minsize(980, 640)
        self.configure(bg=C_BG)
        self._tool_apps = {}

        self._configure_styles()
        self._build_ui()
        self._center()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _configure_styles(self):
        self._style = ttk.Style(self)
        self._style.configure(
            "RobotTools.TNotebook",
            background=C_BG,
            borderwidth=0,
            tabmargins=(8, 8, 8, 0),
        )
        self._style.configure(
            "RobotTools.TNotebook.Tab",
            font=("Segoe UI", 11, "bold"),
            padding=(28, 12),
        )
        self._style.map(
            "RobotTools.TNotebook.Tab",
            background=[("selected", C_SURFACE)],
            foreground=[("selected", C_ACCENT), ("!selected", C_TEXT)],
        )
        self._style.configure("RobotTools.TFrame", background=C_BG)

    def _build_ui(self):
        self._build_header()
        self._build_tabs()

    def _build_header(self):
        hdr = tk.Frame(self, bg=C_ACCENT)
        hdr.pack(fill=tk.X)

        tk.Label(
            hdr,
            text="Aristocrat Robot Tools",
            font=("Segoe UI", 15, "bold"),
            bg=C_ACCENT,
            fg=C_WHITE,
            pady=12,
        ).pack()

        tk.Label(
            hdr,
            text="Build robots, edit robot.xml, and monitor runs from one workspace",
            font=("Segoe UI", 9),
            bg=C_ACCENT,
            fg="#C4B4F4",
        ).pack()

        tk.Frame(hdr, bg=C_ACCENT_L, height=3).pack(fill=tk.X, pady=(10, 0))

    def _build_tabs(self):
        shell = tk.Frame(self, bg=C_BG, padx=10, pady=10)
        shell.pack(fill=tk.BOTH, expand=True)

        self.notebook = ttk.Notebook(shell, style="RobotTools.TNotebook")
        self.notebook.pack(fill=tk.BOTH, expand=True)

        for tool in TOOLS:
            tab = ttk.Frame(self.notebook, style="RobotTools.TFrame")
            self.notebook.add(tab, text=tool["title"])
            content = self._tab_content(tab, tool)
            if tool["available"]:
                self._mount_tool(content, tool)
            else:
                self._placeholder(content, tool)

    def _tab_content(self, tab, tool: dict):
        header = tk.Frame(tab, bg=C_SURFACE, padx=18, pady=12)
        header.pack(fill=tk.X)

        tk.Label(
            header,
            text=tool["title"],
            font=("Segoe UI", 13, "bold"),
            bg=C_SURFACE,
            fg=C_TEXT,
            anchor="w",
        ).pack(side=tk.LEFT)

        tk.Label(
            header,
            text=tool.get("subtitle", ""),
            font=("Segoe UI", 9),
            bg=C_SURFACE,
            fg=C_MUTED,
            anchor="w",
        ).pack(side=tk.LEFT, padx=(14, 0))

        tk.Frame(tab, bg="#DDD9EF", height=1).pack(fill=tk.X)
        content = tk.Frame(tab, bg=C_BG)
        content.pack(fill=tk.BOTH, expand=True)
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
        body = tk.Frame(tab, bg=C_BG, padx=28, pady=24)
        body.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            body,
            text="Coming soon.",
            font=("Segoe UI", 11),
            bg=C_BG,
            fg=C_MUTED,
            anchor="w",
        ).pack(fill=tk.X)

    def _error_tab(self, tab, tool: dict, exc: Exception):
        body = tk.Frame(tab, bg=C_SURFACE, padx=28, pady=24)
        body.pack(fill=tk.BOTH, expand=True)
        tk.Label(
            body,
            text=f"{tool['title']} could not be loaded",
            font=("Segoe UI", 12, "bold"),
            bg=C_SURFACE,
            fg="#B71C1C",
            anchor="w",
        ).pack(fill=tk.X)
        tk.Label(
            body,
            text=str(exc),
            font=("Consolas", 9),
            bg=C_SURFACE,
            fg=C_TEXT,
            justify=tk.LEFT,
            anchor="w",
            wraplength=900,
        ).pack(fill=tk.X, pady=(8, 0))

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

    def _center(self):
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w = self.winfo_width()
        h = self.winfo_height()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()

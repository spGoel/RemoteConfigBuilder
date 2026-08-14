"""
Aristocrat Robot Tools — top-level launcher.
Each option opens a separate tool. More tools are added over time.
"""
import subprocess
import sys
import tkinter as tk
from pathlib import Path

PYTHON   = sys.executable
BASE_DIR = Path(__file__).parent

# ── Tool registry ────────────────────────────────────────────────────────────
# Add new tools here; set script=None and available=False for placeholders.
TOOLS = [
    {
        "num":         "1",
        "title":       "Robot Builder",
        "desc":        "Build 3L / 5L / AVL robots on a remote Linux machine.\n"
                       "Uploads script via SSH, runs in a screen session,\n"
                       "and streams live build output to this window.",
        "script":      BASE_DIR / "robot_builder" / "main.py",
        "available":   True,
    },
    {
        "num":         "2",
        "title":       "Generate Robot Config",
        "desc":        "Coming soon.",
        "script":      None,
        "available":   False,
    },
    {
        "num":         "3",
        "title":       "Generate Customised Robot",
        "desc":        "Visual XML editor with templates, live preview,\n"
                       "drag-and-drop tree, and coordinate picker.",
        "script":      BASE_DIR / "robot_config_builder" / "main.py",
        "available":   True,
    },
]

# ── Palette ──────────────────────────────────────────────────────────────────
C_ACCENT   = "#5B3EA6"
C_ACCENT_L = "#7457C4"
C_BG       = "#F3F0FA"
C_SURFACE  = "#FFFFFF"
C_SURFACE_D= "#F8F7FC"
C_TEXT     = "#1A1820"
C_MUTED    = "#5C5870"
C_DISABLED = "#AAAAAA"
C_BORDER_A = "#DDD9EF"
C_BADGE_D  = "#E0DCF0"
C_WHITE    = "#FFFFFF"


class LauncherApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Aristocrat Robot Tools")
        self.resizable(False, False)
        self.configure(bg=C_BG)
        self._build_ui()
        self._center()

    # ── Layout ───────────────────────────────────────────────────────────────

    def _build_ui(self):
        self._build_header()
        self._build_cards()

    def _build_header(self):
        hdr = tk.Frame(self, bg=C_ACCENT)
        hdr.pack(fill=tk.X)

        tk.Label(
            hdr, text="Aristocrat Robot Tools",
            font=("Segoe UI", 15, "bold"),
            bg=C_ACCENT, fg=C_WHITE, pady=14,
        ).pack()

        tk.Label(
            hdr, text="Select a tool to launch",
            font=("Segoe UI", 9),
            bg=C_ACCENT, fg="#C4B4F4", pady=0,
        ).pack()

        # Bottom fade strip
        tk.Frame(hdr, bg=C_ACCENT_L, height=3).pack(fill=tk.X, pady=(10, 0))

    def _build_cards(self):
        body = tk.Frame(self, bg=C_BG, padx=24, pady=20)
        body.pack(fill=tk.BOTH)

        for tool in TOOLS:
            self._card(body, tool)

        # Footer
        tk.Label(
            body,
            text="More tools will be added in future iterations.",
            font=("Segoe UI", 8), bg=C_BG, fg=C_DISABLED,
        ).pack(pady=(8, 0))

    def _card(self, parent, tool):
        avail      = tool["available"]
        bg         = C_SURFACE   if avail else C_SURFACE_D
        border     = C_ACCENT    if avail else C_BADGE_D
        title_fg   = C_TEXT      if avail else C_DISABLED
        desc_fg    = C_MUTED     if avail else C_DISABLED
        badge_bg   = C_ACCENT    if avail else C_DISABLED

        # 1-px border via outer frame
        outer = tk.Frame(parent, bg=border, padx=1, pady=1)
        outer.pack(fill=tk.X, pady=5)

        inner = tk.Frame(outer, bg=bg, padx=14, pady=11)
        inner.pack(fill=tk.X)

        # Number badge
        tk.Label(
            inner, text=tool["num"],
            font=("Segoe UI", 11, "bold"),
            bg=badge_bg, fg=C_WHITE,
            width=2, anchor="center", pady=3,
        ).pack(side=tk.LEFT, padx=(0, 14))

        # Text block
        txt = tk.Frame(inner, bg=bg)
        txt.pack(side=tk.LEFT, fill=tk.X, expand=True)

        tk.Label(
            txt, text=tool["title"],
            font=("Segoe UI", 10, "bold"),
            bg=bg, fg=title_fg, anchor="w",
        ).pack(fill=tk.X)

        tk.Label(
            txt, text=tool["desc"],
            font=("Segoe UI", 8),
            bg=bg, fg=desc_fg, anchor="w",
            justify=tk.LEFT, wraplength=280,
        ).pack(fill=tk.X)

        # Right-side action
        if avail:
            btn = tk.Button(
                inner, text="Launch  →",
                font=("Segoe UI", 9, "bold"),
                bg=C_ACCENT, fg=C_WHITE,
                activebackground=C_ACCENT_L, activeforeground=C_WHITE,
                relief=tk.FLAT, cursor="hand2",
                padx=14, pady=5,
                command=lambda t=tool: self._launch(t),
            )
            btn.pack(side=tk.RIGHT, padx=(12, 0))
            # Hover effect
            btn.bind("<Enter>", lambda e, b=btn: b.config(bg=C_ACCENT_L))
            btn.bind("<Leave>", lambda e, b=btn: b.config(bg=C_ACCENT))
        else:
            tk.Label(
                inner, text="Coming Soon",
                font=("Segoe UI", 7, "bold"),
                bg=C_BADGE_D, fg=C_DISABLED,
                padx=8, pady=3,
            ).pack(side=tk.RIGHT, padx=(12, 0))

    # ── Actions ──────────────────────────────────────────────────────────────

    def _launch(self, tool):
        script = tool.get("script")
        if not script or not Path(script).exists():
            tk.messagebox.showerror(
                "Launch Error",
                f"Script not found:\n{script}",
                parent=self,
            )
            return
        subprocess.Popen(
            [PYTHON, str(script)],
            cwd=str(Path(script).parent),
        )

    def _center(self):
        self.update_idletasks()
        w  = self.winfo_width()
        h  = self.winfo_height()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        self.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")


if __name__ == "__main__":
    app = LauncherApp()
    app.mainloop()

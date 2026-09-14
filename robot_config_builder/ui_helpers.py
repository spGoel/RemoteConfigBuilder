"""Local CustomTkinter helpers for the Config Builder.

Everything here would sit naturally in common/theme.py or common/widgets.py;
it lives in the tool folder because the shared layer is frozen while the
tools are ported one at a time.
"""
import sys
import tkinter as tk
from pathlib import Path
from tkinter import ttk

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from common import theme  # noqa: E402

# Neutral (non-accent) button colours, as used by the ported Robot Builder.
NEUTRAL = ("gray70", "gray35")
NEUTRAL_HOVER = ("gray60", "gray45")

# ttk style name for the three-pane splitter in the main window.
PANED_STYLE = "Config.TPanedwindow"


def style_menu(menu: tk.Menu) -> None:
    """Colour a tk.Menu to match the current CTk appearance mode.

    CustomTkinter has no menu widget, so popup/context menus stay tk.Menu.
    Call again after an appearance-mode change; tk keeps no notion of one.
    (The native Windows menubar ignores most of these, popups honour them.)
    """
    try:
        menu.configure(
            background=theme.pick(theme.CARD_BG),
            foreground=theme.pick(theme.BODY_FG),
            activebackground=theme.pick(theme.ACCENT),
            activeforeground="#FFFFFF",
            disabledforeground=theme.pick(theme.MUTED_FG),
            borderwidth=0,
            relief="flat",
            font=theme.scaled_font(menu, 11),
        )
    except tk.TclError:
        pass


def style_panedwindow(widget: tk.Misc) -> None:
    """Repaint the ttk.PanedWindow splitter; re-run on appearance change."""
    style = ttk.Style(widget)
    style.configure(PANED_STYLE, background=theme.pick(theme.SUNKEN_BG))
    style.configure(
        "Sash",
        sashthickness=int(round(6 * theme.widget_scaling(widget))),
        gripcount=0,
    )


def center_over(window: tk.Toplevel, parent: tk.Misc) -> None:
    """Place a dialog centred on `parent`, in raw screen pixels.

    Uses wm_geometry() so CustomTkinter's DPI-scaling wrapper is bypassed;
    positions from winfo_rootx() are already physical pixels.
    """
    window.update_idletasks()
    width = window.winfo_reqwidth()
    height = window.winfo_reqheight()
    x = parent.winfo_rootx() + (parent.winfo_width() - width) // 2
    y = parent.winfo_rooty() + (parent.winfo_height() - height) // 2
    window.wm_geometry("+{}+{}".format(max(x, 0), max(y, 0)))

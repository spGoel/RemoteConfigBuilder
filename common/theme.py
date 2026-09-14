"""Colours, fonts, and the ttk.Treeview theming that makes the one non-CTk
widget in the app match the rest of it.

CustomTkinter colours are given as (light, dark) tuples; it picks the right one
for the current appearance mode. ttk.Treeview has no such support, so
`style_treeview()` resolves the pair by hand and must be re-run whenever the
appearance mode changes.
"""

import tkinter as tk
from tkinter import ttk

import customtkinter as ctk

# Aristocrat-ish purple, legible on both grounds.
ACCENT = ("#6D4AC4", "#8B6BE0")
ACCENT_HOVER = ("#5B3EA6", "#7C5CD6")

OK_FG = ("#1B7F37", "#5BD97C")
WARN_FG = ("#9A5B00", "#E8B44A")
ERR_FG = ("#C0362C", "#FF7B6B")
MUTED_FG = ("gray42", "gray62")
BODY_FG = ("gray10", "gray92")

CARD_BG = ("gray92", "gray17")
SUNKEN_BG = ("gray86", "gray13")
BORDER = ("gray78", "gray28")

# Log pane is deliberately a terminal, in both modes.
LOG_BG = "#161620"
LOG_FG = "#E6E6E6"

TREE_STRIPE = ("#F2F0F7", "#232330")
TREE_SELECT = ("#D8CDF2", "#3B2E63")

FONT_FAMILY = "Segoe UI"
MONO_FAMILY = "Cascadia Mono"


def fonts() -> dict:
    """Build the app's fonts. Must be called after a CTk root exists."""
    return {
        "title": ctk.CTkFont(family=FONT_FAMILY, size=17, weight="bold"),
        "heading": ctk.CTkFont(family=FONT_FAMILY, size=13, weight="bold"),
        "body": ctk.CTkFont(family=FONT_FAMILY, size=12),
        "small": ctk.CTkFont(family=FONT_FAMILY, size=11),
        "info": ctk.CTkFont(family=FONT_FAMILY, size=14, weight="bold"),
        "mono": ctk.CTkFont(family=MONO_FAMILY, size=11),
        "mono_small": ctk.CTkFont(family=MONO_FAMILY, size=10),
    }


def pick(colour) -> str:
    """Resolve a (light, dark) pair for the current appearance mode."""
    if isinstance(colour, (tuple, list)):
        return colour[1] if ctk.get_appearance_mode() == "Dark" else colour[0]
    return colour


def widget_scaling(widget: tk.Misc) -> float:
    """CustomTkinter's DPI scaling factor for this window.

    CTk multiplies its own widget and font sizes by this; ttk knows nothing
    about it, which is why ttk text renders far smaller than CTk text on a
    high-DPI display unless it is scaled by hand.
    """
    try:
        return float(ctk.ScalingTracker.get_widget_scaling(widget))
    except Exception:  # noqa: BLE001 - fall back to no scaling
        return 1.0


def scaled_font(widget: tk.Misc, size: int, weight: str = "normal") -> tuple:
    """A ttk font spec that matches a CTkFont of the same nominal size.

    CTk turns `size=N` into a tk font of -(N * scaling) pixels, so mirroring
    that exactly keeps the Treeview in step with every CTk label beside it.
    """
    pixels = max(8, int(round(size * widget_scaling(widget))))
    return (FONT_FAMILY, -pixels, weight)


def style_treeview(widget: tk.Misc) -> None:
    """Repaint ttk.Treeview and its scrollbars to match the CTk theme.

    Call again after any appearance-mode change — ttk keeps no notion of one.
    """
    style = ttk.Style(widget)
    try:
        style.theme_use("clam")  # the only built-in theme that honours colours
    except tk.TclError:
        pass

    background = pick(CARD_BG)
    field = pick(SUNKEN_BG)
    text = pick(BODY_FG)
    border = pick(BORDER)

    scale = widget_scaling(widget)
    style.configure(
        "App.Treeview",
        background=field,
        fieldbackground=field,
        foreground=text,
        borderwidth=0,
        relief="flat",
        rowheight=int(round(26 * scale)),
        font=scaled_font(widget, 12),
    )
    style.configure(
        "App.Treeview.Heading",
        background=background,
        foreground=pick(MUTED_FG),
        relief="flat",
        borderwidth=0,
        padding=(int(round(8 * scale)), int(round(6 * scale))),
        font=scaled_font(widget, 11, "bold"),
    )
    style.map(
        "App.Treeview.Heading",
        background=[("active", pick(TREE_STRIPE))],
    )
    style.map(
        "App.Treeview",
        background=[("selected", pick(TREE_SELECT))],
        foreground=[("selected", text)],
    )
    style.layout(
        "App.Treeview",
        [("App.Treeview.treearea", {"sticky": "nswe"})],  # drop the border box
    )

    thickness = int(round(12 * scale))
    for name in ("App.Vertical.TScrollbar", "App.Horizontal.TScrollbar"):
        style.configure(
            name,
            background=background,
            troughcolor=field,
            bordercolor=field,
            arrowcolor=pick(MUTED_FG),
            borderwidth=0,
            arrowsize=thickness,
            width=thickness,
        )
    style.map(
        "App.Vertical.TScrollbar", background=[("active", pick(ACCENT))]
    )
    style.map(
        "App.Horizontal.TScrollbar", background=[("active", pick(ACCENT))]
    )
    _ = border  # kept for callers that want the same border colour


def tag_colours() -> dict:
    """Row tag colours for the asset and checklist trees."""
    return {
        "emit": pick(OK_FG),
        "skip": pick(WARN_FG),
        "missing": pick(ERR_FG),
        "unknown": pick(MUTED_FG),
        "excluded": pick(MUTED_FG),
        "pass": pick(OK_FG),
        "fail": pick(ERR_FG),
        "info": pick(MUTED_FG),
        "stripe": pick(TREE_STRIPE),
    }

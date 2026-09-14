"""Shared CustomTkinter widgets used across the tabs."""

import os
import subprocess
import sys
import tkinter as tk
from tkinter import filedialog, ttk

import customtkinter as ctk

from common import theme

# Populated by init_styles() once a CTk root exists.
FONTS = {}


def init_styles(root: ctk.CTk) -> None:
    """Set up fonts and the ttk theming for the app."""
    FONTS.clear()
    FONTS.update(theme.fonts())
    theme.style_treeview(root)


def font(name: str):
    return FONTS.get(name)


class Card(ctk.CTkFrame):
    """A titled section panel.

    `scrollable=True` puts the body in a scrolling frame, for panels with more
    fields than will fit at the minimum window size.
    """

    def __init__(self, master, title: str = "", scrollable: bool = False, **kwargs):
        kwargs.setdefault("corner_radius", 10)
        kwargs.setdefault("fg_color", theme.CARD_BG)
        super().__init__(master, **kwargs)
        self.body = self
        if not title and not scrollable:
            return

        row = 0
        if title:
            ctk.CTkLabel(
                self, text=title, font=font("heading"), anchor="w"
            ).grid(row=0, column=0, sticky="w", padx=14, pady=(12, 0))
            row = 1

        if scrollable:
            self.body = ctk.CTkScrollableFrame(self, fg_color="transparent")
        else:
            self.body = ctk.CTkFrame(self, fg_color="transparent")
        self.body.grid(row=row, column=0, sticky="nsew", padx=14, pady=(6, 12))
        self.columnconfigure(0, weight=1)
        self.rowconfigure(row, weight=1)
        self.body.columnconfigure(0, weight=1)


class Tooltip:
    """Hover text for a widget, in a borderless Toplevel.

    Appears below-right of the widget after a short delay and disappears on
    leave. Nothing is created until the first hover.
    """

    def __init__(self, widget, text: str, wraplength: int = 420, delay: int = 300):
        self.widget = widget
        self.text = text
        self.wraplength = wraplength
        self.delay = delay
        self._window = None
        self._job = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, _event=None) -> None:
        self._cancel()
        self._job = self.widget.after(self.delay, self.show)

    def _cancel(self) -> None:
        if self._job is not None:
            self.widget.after_cancel(self._job)
            self._job = None

    def show(self) -> None:
        self._job = None
        if self._window is not None or not self.text:
            return

        x = self.widget.winfo_rootx() + self.widget.winfo_width() + 8
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 2

        self._window = tk.Toplevel(self.widget)
        self._window.wm_overrideredirect(True)
        self._window.attributes("-topmost", True)

        dark = ctk.get_appearance_mode() == "Dark"
        scale = theme.widget_scaling(self.widget)
        border = tk.Frame(
            self._window,
            background=theme.pick(theme.ACCENT),
            borderwidth=0,
            highlightthickness=0,
        )
        border.pack()
        tk.Label(
            border,
            text=self.text,
            justify="left",
            background="#22222E" if dark else "#FFFDF7",
            foreground="#E9E6F2" if dark else "#241F33",
            wraplength=int(self.wraplength * scale),
            font=theme.scaled_font(self.widget, 11),
            padx=int(round(10 * scale)),
            pady=int(round(8 * scale)),
        ).pack(padx=1, pady=1)

        # Keep the tip inside the owning window. Everything here is in device
        # pixels (winfo_root*/winfo_width), deliberately NOT winfo_screenwidth/
        # height: on a DPI-scaled display Tk reports those in scaled units
        # (e.g. 1536x960 for a 3840x2400 screen), and clamping device-pixel
        # coordinates against them threw every lower tooltip to the top.
        self._window.update_idletasks()
        width = self._window.winfo_width()
        height = self._window.winfo_height()
        top = self.widget.winfo_toplevel()
        left_limit = top.winfo_rootx()
        right_limit = left_limit + top.winfo_width()
        bottom_limit = top.winfo_rooty() + top.winfo_height()
        if x + width > right_limit:
            x = max(left_limit, right_limit - width - 8)
        if y + height > bottom_limit:
            # Flip above the widget instead of pushing it off the window.
            y = self.widget.winfo_rooty() - height - 2
        self._window.wm_geometry("+{}+{}".format(max(x, 0), max(y, 0)))

    def _hide(self, _event=None) -> None:
        self._cancel()
        if self._window is not None:
            self._window.destroy()
            self._window = None

    def toggle(self, _event=None) -> None:
        if self._window is None:
            self.show()
        else:
            self._hide()


class InfoButton(ctk.CTkLabel):
    """Small ⓘ marker that explains, on hover, why an option is needed."""

    def __init__(self, master, text: str, **kwargs):
        super().__init__(
            master,
            text="ⓘ",
            width=18,
            font=font("info"),
            text_color=theme.ACCENT,
            cursor="question_arrow",
            **kwargs
        )
        self.description = text
        self.tooltip = Tooltip(self, text)
        # Clicking pins it open, for anyone who misses the hover.
        self.bind("<Button-1>", self.tooltip.toggle, add="+")


class Banner(ctk.CTkLabel):
    """A message line that occupies no space when it has nothing to say.

    An empty CTkLabel still collapses to a 1px-wide cell of full row height,
    which leaves a stray gap; this hides itself instead.
    """

    def __init__(self, master, colour=None, **kwargs):
        kwargs.setdefault("font", font("small"))
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("justify", "left")
        super().__init__(
            master, text="", text_color=colour or theme.WARN_FG, **kwargs
        )

    def grid(self, **kwargs):
        super().grid(**kwargs)
        if not self.cget("text"):
            self.grid_remove()

    def show(self, text: str, colour=None) -> None:
        """Set the text, showing or hiding the row to match."""
        if colour is not None:
            self.configure(text_color=colour)
        self.configure(text=text)
        if text:
            super().grid()  # restores the options remembered by grid_remove()
        else:
            self.grid_remove()


class PathRow(ctk.CTkFrame):
    """Label + optional ⓘ + entry + Browse button, bound to a StringVar."""

    def __init__(
        self,
        master,
        label: str,
        variable: tk.StringVar,
        kind: str = "file",
        filetypes=None,
        title: str = None,
        width: int = 60,
        on_change=None,
        initial_dir_getter=None,
        info: str = "",
    ):
        super().__init__(master, fg_color="transparent")
        self.variable = variable
        self.kind = kind
        self.filetypes = filetypes or [("All files", "*.*")]
        self.title = title or "Select"
        self.initial_dir_getter = initial_dir_getter

        ctk.CTkLabel(
            self, text=label, width=128, anchor="w", font=font("body")
        ).grid(row=0, column=0, sticky="w", padx=(0, 2))

        if info:
            InfoButton(self, info).grid(row=0, column=1, sticky="w", padx=(0, 8))
        else:
            ctk.CTkLabel(self, text="", width=18).grid(row=0, column=1, padx=(0, 8))

        self.entry = ctk.CTkEntry(
            self, textvariable=variable, font=font("body"), height=30
        )
        self.entry.grid(row=0, column=2, sticky="ew")

        ctk.CTkButton(
            self,
            text="Browse",
            command=self._browse,
            width=78,
            height=30,
            font=font("body"),
            fg_color=theme.ACCENT,
            hover_color=theme.ACCENT_HOVER,
        ).grid(row=0, column=3, padx=(8, 0))

        self.columnconfigure(2, weight=1)

        if on_change is not None:
            variable.trace_add("write", lambda *_: on_change())

    def _initial_dir(self) -> str:
        current = self.variable.get()
        if current:
            candidate = current if os.path.isdir(current) else os.path.dirname(current)
            if os.path.isdir(candidate):
                return candidate
        if self.initial_dir_getter:
            candidate = self.initial_dir_getter()
            if candidate and os.path.isdir(candidate):
                return candidate
        return os.getcwd()

    def _browse(self) -> None:
        if self.kind == "dir":
            path = filedialog.askdirectory(
                title=self.title, initialdir=self._initial_dir()
            )
        elif self.kind == "save":
            path = filedialog.asksaveasfilename(
                title=self.title,
                initialdir=self._initial_dir(),
                filetypes=self.filetypes,
                defaultextension=".json",
            )
        else:
            path = filedialog.askopenfilename(
                title=self.title,
                initialdir=self._initial_dir(),
                filetypes=self.filetypes,
            )
        if path:
            self.variable.set(os.path.normpath(path))


class StatusPill(ctk.CTkLabel):
    """A one-line status indicator with a marker and colour."""

    MARKS = {"ok": "✓", "warn": "▲", "error": "✕", "idle": "•"}
    COLOURS = {
        "ok": theme.OK_FG,
        "warn": theme.WARN_FG,
        "error": theme.ERR_FG,
        "idle": theme.MUTED_FG,
    }

    def __init__(self, master, text: str = "", state: str = "idle", **kwargs):
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("font", font("small"))
        super().__init__(master, text="", **kwargs)
        self.set(state, text)

    def set(self, state: str, text: str) -> None:
        mark = self.MARKS.get(state, self.MARKS["idle"])
        self.configure(
            text="{}  {}".format(mark, text) if text else mark,
            text_color=self.COLOURS.get(state, theme.MUTED_FG),
        )


class LogPane(ctk.CTkFrame):
    """Read-only terminal-style output with colour tags for message levels."""

    TAGS = {
        "ok": {"foreground": "#5BD97C"},
        "done": {"foreground": "#7FB2FF"},
        "warn": {"foreground": "#E8B44A"},
        "error": {"foreground": "#FF7B6B"},
        "info": {"foreground": "#9A9AAE"},
        "dim": {"foreground": "#6C7086"},
        "plain": {"foreground": theme.LOG_FG},
        "command": {"foreground": "#C9A6FF"},
        "banner": {"foreground": "#FFFFFF"},
    }

    def __init__(self, master, height: int = 260):
        super().__init__(master, fg_color="transparent")
        self.textbox = ctk.CTkTextbox(
            self,
            height=height,
            wrap="none",
            fg_color=theme.LOG_BG,
            text_color=theme.LOG_FG,
            font=font("mono"),
            corner_radius=8,
            border_width=0,
        )
        self.textbox.grid(row=0, column=0, sticky="nsew")
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        for name, config in self.TAGS.items():
            self.textbox.tag_config(name, **config)
        self.textbox.configure(state="disabled")

    # Kept for callers that reach for the underlying text widget.
    @property
    def text(self):
        return self.textbox

    def append(self, line: str, tag: str = "plain") -> None:
        self.textbox.configure(state="normal")
        self.textbox.insert("end", line + "\n", tag)
        self.textbox.see("end")
        self.textbox.configure(state="disabled")

    def clear(self) -> None:
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.configure(state="disabled")

    def contents(self) -> str:
        return self.textbox.get("1.0", "end-1c")


class TreePane(ctk.CTkFrame):
    """A ttk.Treeview on a CTk-coloured backing frame.

    CustomTkinter has no tree widget, so this is the one ttk holdout; the
    surrounding frame supplies the rounded card look and theme.style_treeview()
    handles the colours.
    """

    def __init__(self, master, columns, height: int = 12, show_scroll: bool = True):
        super().__init__(
            master, corner_radius=8, fg_color=theme.SUNKEN_BG
        )
        self.tree = ttk.Treeview(
            self,
            columns=[c[0] for c in columns],
            show="headings",
            selectmode="browse",
            height=height,
            style="App.Treeview",
        )
        # Column widths are raw pixels; scale them like the font.
        scale = theme.widget_scaling(self)
        for name, text, width, anchor, stretch in columns:
            self.tree.heading(name, text=text)
            self.tree.column(
                name,
                width=int(round(width * scale)),
                minwidth=int(round(40 * scale)),
                anchor=anchor,
                stretch=stretch,
            )

        self.tree.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        self.columnconfigure(0, weight=1)
        self.rowconfigure(0, weight=1)

        if show_scroll:
            scroll = ttk.Scrollbar(
                self,
                orient="vertical",
                command=self.tree.yview,
                style="App.Vertical.TScrollbar",
            )
            self.tree.configure(yscrollcommand=scroll.set)
            scroll.grid(row=0, column=1, sticky="ns", pady=6, padx=(0, 6))

        self.apply_tags()

    def apply_tags(self) -> None:
        for name, colour in theme.tag_colours().items():
            if name == "stripe":
                continue
            self.tree.tag_configure(name, foreground=colour)


def open_in_explorer(path: str) -> None:
    """Reveal a file in Explorer, or open a folder."""
    if not path or not os.path.exists(path):
        return
    if sys.platform != "win32":  # pragma: no cover - the tool targets Windows
        subprocess.Popen(["xdg-open", path])
        return
    if os.path.isdir(path):
        os.startfile(path)  # noqa: S606 - user-initiated
    else:
        # /select, and the path must be ONE argument — split by a space,
        # Explorer ignores it and opens Documents instead.
        subprocess.Popen(["explorer", "/select,{}".format(os.path.normpath(path))])


def open_file(path: str) -> None:
    """Open a file with its default application."""
    if not path or not os.path.exists(path):
        return
    if sys.platform == "win32":
        os.startfile(path)  # noqa: S606 - user-initiated
    else:  # pragma: no cover
        subprocess.Popen(["xdg-open", path])


def list_to_text(values) -> str:
    """Render a JSON string list as one entry per line for editing."""
    return "\n".join(values or [])


def text_to_list(text: str) -> list:
    """Parse a multi-line editor value back into a list, dropping blanks."""
    return [line.strip() for line in text.splitlines() if line.strip()]

"""A numeric stepper for CustomTkinter, which ships no Spinbox.

Entry bound to a StringVar with "-" / "+" buttons either side; values are
clamped to [from_, to] and rendered with ``:g`` so 5.0 shows as ``5`` and 5.5
as ``5.5``, matching what ttk.Spinbox used to produce.
"""

import tkinter as tk

import customtkinter as ctk

from common import theme
from common.widgets import font

_NEUTRAL = ("gray70", "gray35")
_NEUTRAL_HOVER = ("gray60", "gray45")


class NumberSpin(ctk.CTkFrame):
    def __init__(
        self,
        master,
        variable: tk.StringVar,
        from_: float,
        to: float,
        increment: float = 1.0,
        width: int = 60,
        command=None,
        **kwargs,
    ):
        kwargs.setdefault("fg_color", "transparent")
        super().__init__(master, **kwargs)
        self.variable = variable
        self._from = float(from_)
        self._to = float(to)
        self._step = float(increment)
        self._command = command

        button_style = dict(
            width=30, height=30, font=font("body"),
            fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER,
            text_color=theme.BODY_FG,
        )
        ctk.CTkButton(
            self, text="−", command=lambda: self._bump(-1), **button_style
        ).grid(row=0, column=0, padx=(0, 2))
        self.entry = ctk.CTkEntry(
            self, textvariable=variable, width=width, height=30,
            font=font("body"), justify="center",
        )
        self.entry.grid(row=0, column=1)
        ctk.CTkButton(
            self, text="+", command=lambda: self._bump(1), **button_style
        ).grid(row=0, column=2, padx=(2, 0))

    def _bump(self, direction: int) -> None:
        try:
            value = float(self.variable.get())
        except (ValueError, tk.TclError):
            value = self._from
        value += direction * self._step
        value = min(self._to, max(self._from, value))
        # Snap to the step grid so repeated presses do not accumulate float noise.
        value = round(round((value - self._from) / self._step) * self._step + self._from, 6)
        self.variable.set(f"{value:g}")
        if self._command is not None:
            self._command()

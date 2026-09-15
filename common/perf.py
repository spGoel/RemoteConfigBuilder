"""Redraw performance workarounds for CustomTkinter 6.0.0.

Measured on the Robot Tools launcher (about 1,300 widgets, 525 of them
CustomTkinter, at 2.5x DPI scaling):

* A Light/Dark switch took ~3.3 s and visibly repainted one widget at a time.
  `CTkBaseClass._set_appearance_mode` calls `_draw()` and then
  `update_idletasks()` for EVERY widget, so Tk flushes and paints after each
  one, and every widget on every tab is redrawn whether or not it is visible.
* `CTkScrollbar._draw` ends with its own `update_idletasks()`, making a
  scrollbar redraw cost ~40 ms instead of ~1 ms.
* `CTkTabview` switches tabs by `grid_forget()` + `grid()`, so every widget in
  the tab is unmapped and remapped (each is its own HWND on Windows): 0.4-0.6 s
  per switch here, painted progressively.

What this module does:

1. `install()` patches the two library methods so no per-widget flush happens,
   and so widgets that sit in a hidden `StackedTabview` tab only record the new
   appearance mode instead of redrawing. They are redrawn when their tab is
   next raised. Widgets outside any StackedTabview redraw immediately as before.
2. `StackedTabview` keeps every tab it has shown gridded in the same cell and
   switches by raising the selected frame. Tabs are mapped lazily on first
   selection, so startup cost is unchanged and later switches cost 0.1-0.2 s.

Both are behaviour-preserving for callers; `widgets.init_styles()` calls
`install()` so every entry point (launcher and standalone tools) gets it.
"""

import tkinter as tk
import weakref
from typing import Dict, List

import customtkinter as ctk
from customtkinter import CTkScrollbar, CTkTabview
from customtkinter.windows.widgets.appearance_mode import CTkAppearanceModeBaseClass
from customtkinter.windows.widgets.core_widget_classes import CTkBaseClass

_installed = False
# Widgets whose appearance changed while their tab was hidden: id -> widget.
_pending: Dict[int, CTkBaseClass] = {}
# Every live StackedTabview, so an appearance change can unmap hidden tabs.
_stacks = weakref.WeakSet()

# Attribute stamped on each tab frame so is_obscured() can find its owner.
_OWNER_ATTR = "_stacked_owner"


def installed() -> bool:
    return _installed


def pending() -> List[CTkBaseClass]:
    return list(_pending.values())


def pending_count() -> int:
    return len(_pending)


# ----------------------------------------------------------------------
# Visibility through nested StackedTabviews
# ----------------------------------------------------------------------


def is_obscured(widget) -> bool:
    """True if some ancestor is a StackedTabview tab that is not the current one."""
    node = widget
    while node is not None:
        owner = getattr(node, _OWNER_ATTR, None)
        if owner is not None and owner._tab_dict.get(owner._current_name) is not node:
            return True
        node = getattr(node, "master", None)
    return False


def _is_under(widget, ancestor) -> bool:
    node = widget
    while node is not None:
        if node is ancestor:
            return True
        node = getattr(node, "master", None)
    return False


def flush_pending(under=None) -> int:
    """Redraw deferred widgets that are now visible (optionally only those
    under `under`). Dead widgets are dropped. Returns the number redrawn."""
    drawn = 0
    for key, widget in list(_pending.items()):
        try:
            alive = widget.winfo_exists()
        except tk.TclError:
            alive = False
        if not alive:
            _pending.pop(key, None)
            continue
        if under is not None and not _is_under(widget, under):
            continue
        if is_obscured(widget):
            continue
        _pending.pop(key, None)
        try:
            widget._draw()
            drawn += 1
        except tk.TclError:
            pass
    return drawn


# ----------------------------------------------------------------------
# Library patches
# ----------------------------------------------------------------------


def _noop() -> None:
    return None


def install() -> None:
    """Apply the CustomTkinter patches once. Safe to call repeatedly."""
    global _installed
    if _installed:
        return

    def _set_appearance_mode(self, mode_string):
        # Same as the library minus the per-widget update_idletasks(), and
        # deferred for widgets the user cannot currently see.
        CTkAppearanceModeBaseClass._set_appearance_mode(self, mode_string)
        if is_obscured(self):
            _pending[id(self)] = self
        else:
            self._draw()

    CTkBaseClass._set_appearance_mode = _set_appearance_mode

    original_scrollbar_draw = CTkScrollbar._draw

    def _scrollbar_draw(self, no_color_updates=False):
        # The library ends _draw with self._canvas.update_idletasks(); shadow
        # that bound method on the instance for the duration of the call.
        canvas = self._canvas
        canvas.update_idletasks = _noop
        try:
            original_scrollbar_draw(self, no_color_updates)
        finally:
            try:
                del canvas.update_idletasks
            except AttributeError:
                pass

    CTkScrollbar._draw = _scrollbar_draw
    _installed = True


# ----------------------------------------------------------------------
# Appearance change entry point
# ----------------------------------------------------------------------


def unmap_hidden_tabs() -> int:
    """Take every non-current StackedTabview tab off the grid.

    Measured: Tk repaints stacked-but-covered tabs on a theme change (0.7 s
    with one tab mapped vs 1.3-1.8 s with five). Unmapped tabs cost nothing;
    they are re-gridded on their next selection, where their deferred redraws
    are flushed anyway. Returns the number of tabs unmapped.
    """
    count = 0
    for stack in list(_stacks):
        try:
            for name, frame in stack._tab_dict.items():
                if name != stack._current_name and frame.winfo_manager():
                    frame.grid_remove()
                    count += 1
        except tk.TclError:
            continue
    return count


def set_appearance_mode(mode: str) -> None:
    """Switch Light/Dark/System with hidden tabs unmapped first. Use this
    instead of ctk.set_appearance_mode() from UI code.

    (A background pre-render of hidden tabs was tried and removed: it made
    first clicks instant but cost ~2 s of UI stalls right after launch and
    after every theme switch. First visit to a tab therefore still pays the
    mapping cost once; revisits are a raise.)"""
    unmap_hidden_tabs()
    ctk.set_appearance_mode(mode)


# ----------------------------------------------------------------------
# StackedTabview
# ----------------------------------------------------------------------


class StackedTabview(CTkTabview):
    """CTkTabview that raises tabs instead of unmapping and remapping them.

    Drop-in replacement: same constructor, `add()`, `set()`, `get()`, `tab()`.
    Each tab is gridded the first time it is selected and stays gridded; the
    selected tab is lifted to the top of the stack. Deferred appearance
    redraws for the newly visible tab are flushed on every switch.
    """

    # -- tab colours ------------------------------------------------------
    #
    # The library gives each tab frame a RESOLVED colour string and therefore
    # has to reconfigure every tab on every appearance change; that
    # `CTkFrame.configure(fg_color=...)` cascades a redraw into all children,
    # visible or not, and it runs before those children have learnt the new
    # mode, so every widget ends up drawn twice. Giving the tab frames the
    # (light, dark) pair instead lets children inherit the pair and resolve it
    # themselves, so the cascade can be skipped entirely.

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        _stacks.add(self)

    def _tab_colour(self):
        return self._bg_color if self._fg_color == "transparent" else self._fg_color

    def _adopt_tab(self, frame):
        setattr(frame, _OWNER_ATTR, self)
        colour = self._tab_colour()
        frame.configure(fg_color=colour, bg_color=colour)
        return frame

    def add(self, name: str):
        return self._adopt_tab(super().add(name))

    def insert(self, index: int, name: str):
        return self._adopt_tab(super().insert(index, name))

    def _draw(self, no_color_updates: bool = False):
        # Run the library drawing with no tabs visible to it, so it paints the
        # tabview's own canvas but leaves the tab frames alone.
        all_tabs = getattr(self, "_tab_dict", None)
        if all_tabs is None:
            return super()._draw(no_color_updates)
        self._tab_dict = {}
        try:
            super()._draw(no_color_updates)
        finally:
            self._tab_dict = all_tabs

    def configure(self, require_redraw=False, **kwargs):
        super().configure(require_redraw=require_redraw, **kwargs)
        if "fg_color" in kwargs or "bg_color" in kwargs:
            # Explicit recolour of the tabview: the one time the cascade is wanted.
            colour = self._tab_colour()
            for frame in self._tab_dict.values():
                frame.configure(fg_color=colour, bg_color=colour)

    def _tab_grid_options(self) -> dict:
        pad = self._apply_widget_scaling(max(self._corner_radius, self._border_width))
        row = 3 if self._anchor.lower() in ("center", "w", "nw", "n", "ne", "e") else 0
        return dict(row=row, column=0, sticky="nsew", padx=pad, pady=pad)

    def _set_grid_current_tab(self):
        if not self._current_name:
            return
        frame = self._tab_dict[self._current_name]
        if not frame.winfo_manager():
            frame.grid(**self._tab_grid_options())
        frame.lift()
        flush_pending(under=frame)

    def _grid_forget_all_tabs(self, exclude_name=None):
        # Tabs stay mapped; hiding is done by stacking order.
        return None

    def _segmented_button_callback(self, selected_name):
        self._current_name = selected_name
        self._set_grid_current_tab()
        if self._command is not None:
            self._command()

    def set(self, name: str):
        if name not in self._tab_dict:
            raise ValueError(f"CTkTabview has no tab named '{name}'")
        self._current_name = name
        self._segmented_button.set(name)
        self._set_grid_current_tab()

    def delete(self, name: str):
        frame = self._tab_dict.get(name)
        if frame is not None and frame.winfo_manager():
            frame.grid_forget()
        super().delete(name)

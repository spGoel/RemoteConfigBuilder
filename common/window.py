"""Open a CustomTkinter root maximised on the monitor the user is working on.

Why this exists (all verified on this machine — primary monitor at 2.5x DPI
scaling, a second monitor above it at 1x):

* Tk's own `winfo_screenwidth()`/`winfo_screenheight()` and the pointer
  position CustomTkinter widgets are constructed with are captured once at
  interpreter start-up, before CTk has made the process DPI-aware, so they
  report the primary monitor's *virtualized* size (e.g. 1536x960 for a real
  3840x2400 panel) and are useless for placing a window on a specific real
  monitor.
* Creating a `ctk.CTk()` root calls `SetProcessDpiAwareness(2)` internally.
  From that point on, fresh Win32 calls (`GetCursorPos`, `MonitorFromPoint`,
  `GetMonitorInfoW`) return true device pixels — confirmed by comparing them
  against `EnumDisplayMonitors`' real per-monitor rectangles. This module is
  built on those fresh calls, never on Tk's cached screen metrics.
* `CTkTk.geometry()` multiplies width/height (but not x/y) by CustomTkinter's
  window scaling before calling real Tk, so mixing it with device-pixel
  coordinates double-scales the size. Placement here goes through
  `tk.Wm.wm_geometry` directly, which — once the process is DPI-aware — also
  operates in true device pixels, matching the Win32 rectangles above exactly
  (verified: placing at raw (50, 50) with size 400x300 reports
  `winfo_rootx/y`/`winfo_width/height` of essentially that same box).
* `state("zoomed")` maximises on whichever monitor currently contains the
  window, so the window must be placed on the right monitor first.

`open_maximised(root)` places the window on the monitor under the mouse
pointer using its real work area (screen minus taskbar), maximises it, and
re-asserts the maximised state once the window is mapped in case a toolkit
callback undoes it. On non-Windows platforms, or if a Win32 call fails, it
falls back to Tk's own (possibly virtualized, but still self-consistent)
screen size. F11 toggles true full-screen (no title bar); Escape leaves it.
"""

import ctypes
import sys
import tkinter as tk
from typing import Optional, Tuple

Rect = Tuple[int, int, int, int]  # x, y, width, height, in device pixels

_MONITOR_DEFAULTTONEAREST = 2


class _POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class _MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", ctypes.c_ulong), ("rcMonitor", _RECT),
                ("rcWork", _RECT), ("dwFlags", ctypes.c_ulong)]


def win32_cursor_pos() -> Optional[Tuple[int, int]]:
    """Real device-pixel cursor position, or None off Windows / on failure."""
    if sys.platform != "win32":
        return None
    try:
        point = _POINT()
        if not ctypes.windll.user32.GetCursorPos(ctypes.byref(point)):
            return None
        return point.x, point.y
    except Exception:  # noqa: BLE001 - any failure means "use the Tk fallback"
        return None


def _win32_work_area_at(x: int, y: int) -> Optional[Rect]:
    """Work area (screen minus taskbar), in device pixels, of the monitor
    containing point (x, y) — also in device pixels."""
    if sys.platform != "win32":
        return None
    try:
        user32 = ctypes.windll.user32
        monitor = user32.MonitorFromPoint(_POINT(x, y), _MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = _MONITORINFO()
        info.cbSize = ctypes.sizeof(_MONITORINFO)
        if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        work = info.rcWork
        return work.left, work.top, work.right - work.left, work.bottom - work.top
    except Exception:  # noqa: BLE001
        return None


def target_work_area(root: tk.Misc) -> Rect:
    """Device-pixel work area of the monitor under the pointer.

    Falls back to Tk's own screen size (whatever units Tk currently reports —
    self-consistent even if virtualized) when the Win32 calls are unavailable.
    """
    cursor = win32_cursor_pos()
    if cursor is not None:
        area = _win32_work_area_at(*cursor)
        if area is not None and area[2] > 0 and area[3] > 0:
            return area
    return 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()


def place_unscaled(root: tk.Wm, x: int, y: int, width: int, height: int) -> None:
    """Set geometry in raw pixels, bypassing CustomTkinter's scaled geometry()."""
    tk.Wm.wm_geometry(root, "{}x{}+{}+{}".format(int(width), int(height), int(x), int(y)))


def _minsize_unscaled(root: tk.Wm, width: int, height: int) -> None:
    """Set the minimum size in raw pixels, bypassing CTkTk.minsize()'s scaling."""
    tk.Wm.wm_minsize(root, int(width), int(height))


def open_maximised(root: tk.Tk, fill: float = 0.92) -> Rect:
    """Place `root` on the monitor under the pointer and maximise it.

    Returns the work area used. The fallback geometry (used only if the
    window manager refuses to honour "zoomed") is `fill` of that work area,
    centred.
    """
    # A withdrawn window defers geometry requests until it is next mapped, so
    # a geometry set here would silently be dropped and the later state()
    # call would zoom from whatever position the window already had. No
    # entry point in this app withdraws its root before calling this, but
    # deiconifying first costs nothing and removes the footgun entirely.
    try:
        root.deiconify()
    except tk.TclError:
        pass
    x, y, w, h = target_work_area(root)
    fw, fh = int(w * fill), int(h * fill)
    place_unscaled(root, x + (w - fw) // 2, y + (h - fh) // 2, fw, fh)
    _minsize_unscaled(root, int(min(980, w * 0.5)), int(min(640, h * 0.5)))

    # Windows decides which monitor "zoomed" maximises onto from the window's
    # CURRENT position, and that position is only current once Tk has
    # actually applied the pending move from place_unscaled - a plain method
    # call queues it but does not wait for it. Skipping this flush was
    # verified to maximise onto the wrong (primary) monitor whenever the
    # target monitor sits at a negative coordinate (i.e. above or left of the
    # origin monitor, a common secondary-monitor arrangement).
    root.update_idletasks()
    _maximise(root)
    # A late DPI/scaling callback inside CustomTkinter can drop the maximised
    # state right after creation; re-assert it once the window has settled.
    root.after(50, lambda: _maximise(root))
    root.after(400, lambda: _maximise(root))
    bind_fullscreen_toggle(root)
    return x, y, w, h


def _maximise(root: tk.Tk) -> None:
    try:
        if not root.winfo_exists():
            return
        if root.attributes("-fullscreen"):
            return
        root.state("zoomed")
    except tk.TclError:
        try:
            root.attributes("-zoomed", True)  # some X11 window managers
        except tk.TclError:
            pass


def toggle_fullscreen(root: tk.Tk) -> bool:
    """Flip full-screen on/off; re-maximise when leaving it. Returns the new state."""
    full = not bool(root.attributes("-fullscreen"))
    root.attributes("-fullscreen", full)
    if not full:
        _maximise(root)
    return full


def leave_fullscreen(root: tk.Tk) -> None:
    """Leave full-screen (if in it) and re-maximise. Safe to call unconditionally."""
    if root.attributes("-fullscreen"):
        root.attributes("-fullscreen", False)
        _maximise(root)


def bind_fullscreen_toggle(root: tk.Tk, key: str = "<F11>") -> None:
    """F11 toggles borderless full-screen; Escape returns to maximised.

    The bound handlers are thin wrappers around toggle_fullscreen() /
    leave_fullscreen() so that logic can be exercised directly - triggering it
    through a synthetic key event depends on the OS having actually completed
    the real full-screen transition, which is unreliable to script in an
    automated, unfocused test window (verified: a direct call is instant and
    reliable every time; a key event racing a real DWM fullscreen transition
    sometimes is not). Real key presses have no such problem.
    """
    root.bind(key, lambda _e=None: toggle_fullscreen(root), add="+")
    root.bind("<Escape>", lambda _e=None: leave_fullscreen(root), add="+")

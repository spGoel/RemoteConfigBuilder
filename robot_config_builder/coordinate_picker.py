"""
Visual coordinate picker — shows a screenshot, user clicks to pick X/Y.
Coordinates are returned in the original image's pixel space regardless
of the display scale factor applied to fit the window.
"""
import math
import sys
import tkinter as tk
from pathlib import Path
from tkinter import messagebox

# The shared style layer lives at the repository root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customtkinter as ctk  # noqa: E402

from common import theme  # noqa: E402
from common.widgets import font  # noqa: E402

from ui_helpers import center_over  # noqa: E402


class ProgressDialog(ctk.CTkToplevel):
    """Indeterminate progress dialog shown during SSH/SCP operations."""

    def __init__(self, parent, message: str = "Working..."):
        super().__init__(parent)
        self.title("Please Wait")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # not closeable

        self._label = ctk.CTkLabel(self, text=message, font=font("body"))
        self._label.pack(padx=24, pady=(16, 8))
        pb = ctk.CTkProgressBar(self, mode="indeterminate", width=280,
                                progress_color=theme.ACCENT)
        pb.pack(padx=24, pady=(0, 18))
        pb.start()

        center_over(self, parent)


class CoordinatePicker(ctk.CTkToplevel):
    """
    Displays a screenshot scaled to fit the screen.
    User clicks → on_pick(real_x, real_y) is called with coordinates
    in the original image's pixel space, then the dialog closes.
    Press Esc to cancel.
    """

    def __init__(self, parent, image_path: str, on_pick,
                 title: str = "Pick Coordinate"):
        super().__init__(parent)
        self.title(title)
        self.transient(parent)
        self._on_pick = on_pick
        self._scale = 1
        self._img_ref = None  # keep reference to prevent GC

        try:
            self._load(image_path)
        except Exception as exc:
            self.destroy()
            messagebox.showerror("Image Error",
                                  f"Cannot load screenshot:\n{exc}",
                                  parent=parent)

    def _load(self, path: str):
        raw = tk.PhotoImage(file=path)
        img_w, img_h = raw.width(), raw.height()

        screen_w = self.winfo_screenwidth()
        screen_h = self.winfo_screenheight()
        target_w = int(screen_w * 0.85)
        target_h = int(screen_h * 0.85)

        # Integer subsample factor so the image fits within target dimensions
        factor = max(1, math.ceil(max(img_w / target_w, img_h / target_h)))
        self._scale = factor

        self._img_ref = raw.subsample(factor, factor) if factor > 1 else raw
        disp_w = img_w // factor
        disp_h = img_h // factor

        # ── Info bar ──────────────────────────────────────────────
        ctk.CTkLabel(
            self,
            text=(f"Click anywhere to set coordinates  |  "
                  f"Original: {img_w}×{img_h}  |  "
                  f"Displayed at 1:{factor}  |  Esc to cancel"),
            font=font("small"), anchor="w",
        ).pack(side=tk.TOP, fill=tk.X, padx=8, pady=4)

        # ── Coordinate status bar ─────────────────────────────────
        self._coord_var = tk.StringVar(value="Hover over the image to see coordinates")
        ctk.CTkLabel(self, textvariable=self._coord_var, font=font("small"),
                     anchor="w").pack(side=tk.BOTTOM, fill=tk.X, padx=8, pady=3)

        # ── Canvas + scrollbars ───────────────────────────────────
        # The image surface stays a tk.Canvas (CustomTkinter has none); its
        # events report raw pixels, which is what the coordinate maths needs.
        cf = ctk.CTkFrame(self, fg_color="transparent")
        cf.pack(fill=tk.BOTH, expand=True)
        cf.columnconfigure(0, weight=1)
        cf.rowconfigure(0, weight=1)

        self._canvas = tk.Canvas(
            cf, cursor="crosshair", highlightthickness=0, borderwidth=0,
            width=disp_w, height=disp_h, bg=theme.pick(theme.SUNKEN_BG),
        )
        vbar = ctk.CTkScrollbar(cf, orientation="vertical", command=self._canvas.yview)
        hbar = ctk.CTkScrollbar(cf, orientation="horizontal", command=self._canvas.xview)
        self._canvas.configure(xscrollcommand=hbar.set, yscrollcommand=vbar.set)
        self._canvas.grid(row=0, column=0, sticky="nsew")
        vbar.grid(row=0, column=1, sticky="ns")
        hbar.grid(row=1, column=0, sticky="ew")

        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._img_ref)
        self._canvas.configure(scrollregion=(0, 0, disp_w, disp_h))

        # ── Window size ───────────────────────────────────────────
        # Sizes derive from real image pixels, so bypass CTk's DPI-scaled
        # geometry() and set the raw size with wm_geometry().
        self.update_idletasks()
        win_w = min(self.winfo_reqwidth(), int(screen_w * 0.92))
        win_h = min(self.winfo_reqheight(), int(screen_h * 0.92))
        self.wm_geometry(f"{win_w}x{win_h}")

        self._canvas.bind("<Motion>",   self._on_motion)
        self._canvas.bind("<Button-1>", self._on_click)
        self.bind("<Escape>", lambda _: self.destroy())

    # ── Helpers ───────────────────────────────────────────────────

    def _real_coords(self, event):
        """Convert canvas event coordinates to original image pixel space."""
        cx = self._canvas.canvasx(event.x)
        cy = self._canvas.canvasy(event.y)
        return int(cx * self._scale), int(cy * self._scale)

    def _on_motion(self, event):
        rx, ry = self._real_coords(event)
        self._coord_var.set(f"X: {rx}   Y: {ry}   (click to select)")

    def _on_click(self, event):
        rx, ry = self._real_coords(event)
        self._on_pick(rx, ry)
        self.destroy()

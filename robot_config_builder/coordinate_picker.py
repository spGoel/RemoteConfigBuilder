"""
Visual coordinate picker — shows a screenshot, user clicks to pick X/Y.
Coordinates are returned in the original image's pixel space regardless
of the display scale factor applied to fit the window.
"""
import math
import tkinter as tk
from tkinter import ttk, messagebox


class ProgressDialog(tk.Toplevel):
    """Indeterminate progress dialog shown during SSH/SCP operations."""

    def __init__(self, parent, message: str = "Working..."):
        super().__init__(parent)
        self.title("Please Wait")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # not closeable

        self._label = ttk.Label(self, text=message, padding=(24, 12))
        self._label.pack()
        pb = ttk.Progressbar(self, mode="indeterminate", length=280)
        pb.pack(padx=24, pady=(0, 16))
        pb.start(12)

        self.update_idletasks()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        self.geometry(f"320x90+{px + pw // 2 - 160}+{py + ph // 2 - 45}")

    def set_message(self, msg: str):
        self._label.config(text=msg)
        self.update_idletasks()


class CoordinatePicker(tk.Toplevel):
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
        ttk.Label(
            self,
            text=(f"Click anywhere to set coordinates  |  "
                  f"Original: {img_w}×{img_h}  |  "
                  f"Displayed at 1:{factor}  |  Esc to cancel"),
            padding=(6, 4),
        ).pack(side=tk.TOP, fill=tk.X)

        # ── Canvas + scrollbars ───────────────────────────────────
        cf = ttk.Frame(self)
        cf.pack(fill=tk.BOTH, expand=True)

        hbar = ttk.Scrollbar(cf, orient=tk.HORIZONTAL)
        vbar = ttk.Scrollbar(cf, orient=tk.VERTICAL)
        self._canvas = tk.Canvas(
            cf, cursor="crosshair",
            xscrollcommand=hbar.set, yscrollcommand=vbar.set,
        )
        hbar.config(command=self._canvas.xview)
        vbar.config(command=self._canvas.yview)
        hbar.pack(side=tk.BOTTOM, fill=tk.X)
        vbar.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._canvas.create_image(0, 0, anchor=tk.NW, image=self._img_ref)
        self._canvas.configure(scrollregion=(0, 0, disp_w, disp_h))

        # ── Coordinate status bar ─────────────────────────────────
        self._coord_var = tk.StringVar(value="Hover over the image to see coordinates")
        ttk.Label(self, textvariable=self._coord_var,
                  padding=(6, 3)).pack(side=tk.BOTTOM, fill=tk.X)

        # ── Window size ───────────────────────────────────────────
        win_w = min(disp_w + 24, int(screen_w * 0.92))
        win_h = min(disp_h + 80, int(screen_h * 0.92))
        self.geometry(f"{win_w}x{win_h}")

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

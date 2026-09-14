"""Local ASAN report viewer with deterministic offline analysis.

UI is CustomTkinter; file loading, the offline analysis calls, and every
formatting/threading rule are unchanged from the plain-tkinter version.
"""

from __future__ import annotations

import shutil
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

# The shared style layer lives at the repository root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customtkinter as ctk  # noqa: E402

from common import theme, widgets  # noqa: E402
from common.widgets import Card, font  # noqa: E402

try:
    from offline_analyzer import analyze_file, format_analysis
except ImportError:  # Package-style import, useful for tests and reuse.
    from .offline_analyzer import analyze_file, format_analysis


MAX_REPORT_BYTES = 16 * 1024 * 1024
TRUNCATION_NOTICE = "\n\n[Raw preview truncated by viewer at 16 MiB]\n"

RAW_TAB = "Raw Report"
ANALYSIS_TAB = "Offline Analysis"

# Report views read like a document rather than a terminal, so they follow
# the appearance mode instead of LogPane's fixed dark ground.
_VIEW_BG = ("#FCFCFE", "gray12")

_NEUTRAL = ("gray70", "gray35")
_NEUTRAL_HOVER = ("gray60", "gray45")


def read_local_report(path: Path) -> str:
    """Read a bounded preview; analysis streams the complete file separately."""
    with path.open("rb") as stream:
        data = stream.read(MAX_REPORT_BYTES + 1)
    suffix = b""
    if len(data) > MAX_REPORT_BYTES:
        data = data[:MAX_REPORT_BYTES]
        suffix = TRUNCATION_NOTICE.encode("utf-8")
    return (data + suffix).decode("utf-8", errors="replace")


class AsanReportTab(ctk.CTkFrame):
    def __init__(self, master=None, standalone: bool = False):
        if master is None:
            master = ctk.CTk()
            ctk.set_appearance_mode("System")
            ctk.set_default_color_theme("blue")
            standalone = True
        super().__init__(master, fg_color="transparent", corner_radius=0)
        self._standalone = standalone
        self._root_window = self.winfo_toplevel()
        if not widgets.FONTS:
            widgets.init_styles(self._root_window)

        self._closed = False
        self._preview_request_id = 0
        self._analysis_request_id = 0
        self._analysis_output = ""
        self._file_var = tk.StringVar()
        self._status_var = tk.StringVar(value="Select an ASAN report file")
        self._summary_var = tk.StringVar(value="No report analyzed")

        if self._standalone:
            self._root_window.title("ASAN Report Analyzer")

        self._build_ui()
        self.bind("<Destroy>", self._on_destroy, add="+")

        if self._standalone:
            self.pack(fill=tk.BOTH, expand=True)
            self._apply_initial_geometry()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        if self._standalone:
            self._build_header()
        body_pad = 16 if self._standalone else 12
        body = ctk.CTkFrame(self, fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True, padx=body_pad, pady=(10, 10))
        self._controls_section(body)
        self._summary_section(body)
        self._report_tabs(body)

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=theme.ACCENT, corner_radius=0)
        hdr.pack(fill=tk.X)
        ctk.CTkLabel(hdr, text="ASAN Report Analyzer", font=font("title"),
                     text_color="#FFFFFF").pack(pady=(14, 0))
        ctk.CTkLabel(hdr, text="Browse and inspect AddressSanitizer diagnostics",
                     font=font("small"), text_color="#C4B4F4").pack(pady=(0, 12))

    def _controls_section(self, parent):
        card = Card(parent, title="Report")
        card.pack(fill=tk.X, pady=(0, 10))
        controls = card.body
        controls.grid_columnconfigure(1, weight=1)

        ctk.CTkLabel(
            controls, text="ASAN report:", font=font("body"), anchor="w",
        ).grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(
            controls, textvariable=self._file_var, font=font("body"), height=30,
        ).grid(row=0, column=1, padx=(6, 6), sticky="ew")
        self._neutral_button(controls, "Browse…", self._browse, width=90).grid(
            row=0, column=2, padx=(0, 6)
        )
        self._start_button = ctk.CTkButton(
            controls, text="Start Analysis", command=self._start_analysis,
            width=130, height=30, font=font("heading"),
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        )
        self._start_button.grid(row=0, column=3, padx=(0, 6))
        self._neutral_button(controls, "Save Copy…", self._save_copy, width=110).grid(
            row=0, column=4, padx=(0, 6)
        )
        self._save_analysis_button = self._neutral_button(
            controls, "Save Analysis…", self._save_analysis, width=130,
        )
        self._save_analysis_button.configure(state="disabled")
        self._save_analysis_button.grid(row=0, column=5)

        ctk.CTkLabel(
            controls, textvariable=self._status_var, font=font("small"),
            text_color=theme.MUTED_FG, anchor="w",
        ).grid(row=1, column=0, columnspan=6, pady=(8, 0), sticky="ew")

    @staticmethod
    def _neutral_button(parent, text: str, command, width: int) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=30,
            font=font("body"), fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER,
            text_color=theme.BODY_FG,
        )

    def _summary_section(self, parent):
        summary = ctk.CTkFrame(parent, fg_color=theme.SUNKEN_BG, corner_radius=10)
        summary.pack(fill=tk.X, pady=(0, 10))
        ctk.CTkLabel(
            summary, text="ASAN Summary", font=font("heading"),
            text_color=theme.ACCENT, anchor="w",
        ).pack(side=tk.LEFT, padx=(14, 8), pady=9)
        ctk.CTkLabel(
            summary, textvariable=self._summary_var, font=font("body"),
            text_color=theme.BODY_FG, anchor="e",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(0, 14), pady=9)

    def _report_tabs(self, parent):
        self._tabs = ctk.CTkTabview(
            parent,
            corner_radius=10,
            fg_color=theme.CARD_BG,
            segmented_button_fg_color=theme.SUNKEN_BG,
            segmented_button_selected_color=theme.ACCENT,
            segmented_button_selected_hover_color=theme.ACCENT_HOVER,
            segmented_button_unselected_color=theme.SUNKEN_BG,
            segmented_button_unselected_hover_color=theme.BORDER,
            text_color=theme.BODY_FG,
            text_color_disabled=theme.MUTED_FG,
            anchor="w",
        )
        self._tabs.pack(fill=tk.BOTH, expand=True)
        self._tabs._segmented_button.configure(font=font("body"))
        raw_tab = self._tabs.add(RAW_TAB)
        analysis_tab = self._tabs.add(ANALYSIS_TAB)

        self._text = self._add_text_view(raw_tab, wrap="none")
        self._analysis_text = self._add_text_view(analysis_tab, wrap="word")
        self._set_text(
            self._analysis_text,
            "Select a report, then click Start Analysis.",
        )

    @staticmethod
    def _add_text_view(parent, wrap: str) -> ctk.CTkTextbox:
        text = ctk.CTkTextbox(
            parent, wrap=wrap, font=font("mono"), fg_color=_VIEW_BG,
            text_color=theme.BODY_FG, corner_radius=8, border_width=0,
        )
        text.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 4))
        text.configure(state="disabled")
        return text

    @staticmethod
    def _set_text(widget, value: str):
        widget.configure(state="normal")
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state="disabled")

    @staticmethod
    def _text_contents(widget) -> str:
        return widget.get("1.0", "end-1c")

    def _apply_initial_geometry(self):
        """Standalone only: open maximised with a DPI-safe fallback size.

        CustomTkinter multiplies geometry() by its scaling factor, so a fixed
        size can open partly off-screen on a scaled display.
        """
        root = self._root_window
        scaling = theme.widget_scaling(root)
        screen_w = root.winfo_screenwidth() / scaling
        screen_h = root.winfo_screenheight() / scaling
        width = int(min(1100, screen_w * 0.9))
        height = int(min(760, screen_h * 0.9))
        root.geometry("{}x{}+{}+{}".format(
            width, height,
            max(0, int((screen_w - width) / 2)),
            max(0, int((screen_h - height) / 2)),
        ))
        root.minsize(int(min(860, screen_w * 0.6)), int(min(600, screen_h * 0.6)))
        try:
            root.state("zoomed")
        except tk.TclError:
            pass

    def on_appearance_change(self, _mode: str):
        """Called by the launcher after a Light/Dark switch; nothing ttk here."""

    # ── Actions ───────────────────────────────────────────────────────────────

    def _browse(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select ASAN report",
            filetypes=[("Text and log files", "*.txt *.log"), ("All files", "*.*")],
        )
        if path:
            self._analysis_request_id += 1
            self._analysis_output = ""
            self._file_var.set(path)
            self._start_button.configure(state="normal")
            self._save_analysis_button.configure(state="disabled")
            self._summary_var.set("No report analyzed")
            self._set_text(
                self._analysis_text,
                "File selected. Click Start Analysis.",
            )
            self._load_preview(Path(path))

    def _selected_file(self) -> Path:
        path = Path(self._file_var.get().strip())
        if not path.is_file():
            raise FileNotFoundError(f"File not found: {path}")
        return path

    def _load_preview(self, path: Path):
        self._preview_request_id += 1
        request_id = self._preview_request_id
        self._status_var.set(f"Loading preview for {path.name}…")

        def worker():
            try:
                preview = read_local_report(path)
                self.after(0, lambda: self._show_preview(request_id, path, preview))
            except Exception as exc:
                self._post_error(request_id, str(exc), preview=True)

        threading.Thread(target=worker, daemon=True).start()

    def _show_preview(self, request_id: int, path: Path, preview: str):
        if self._closed or request_id != self._preview_request_id:
            return
        self._set_text(self._text, preview)
        if self._start_button.cget("state") != "disabled":
            note = (
                "; preview limited to 16 MiB"
                if path.stat().st_size > MAX_REPORT_BYTES else ""
            )
            self._status_var.set(f"Selected {path.name}{note}; click Start Analysis")
            self._tabs.set(RAW_TAB)

    def _start_analysis(self):
        try:
            path = self._selected_file()
        except OSError as exc:
            self._status_var.set(str(exc))
            return

        self._analysis_request_id += 1
        request_id = self._analysis_request_id
        self._analysis_output = ""
        self._start_button.configure(state="disabled")
        self._save_analysis_button.configure(state="disabled")
        self._set_text(self._analysis_text, "Analyzing complete report offline…")
        self._status_var.set(f"Analyzing {path.name} offline…")

        def worker():
            try:
                result = analyze_file(path)
                output = format_analysis(result)
                self.after(0, lambda: self._show_analysis(
                    request_id, path.name, result, output
                ))
            except Exception as exc:
                self._post_error(request_id, str(exc), preview=False)

        threading.Thread(target=worker, daemon=True).start()

    def _show_analysis(
        self, request_id: int, report_name: str, result: dict, output: str,
    ):
        if self._closed or request_id != self._analysis_request_id:
            return
        self._analysis_output = output
        self._set_text(self._analysis_text, output)
        self._start_button.configure(state="normal")
        self._save_analysis_button.configure(state="normal")
        self._tabs.set(ANALYSIS_TAB)
        totals = result["totals"]
        stats = result["stats"]
        self._summary_var.set(
            f"Reports {stats['reports_completed']}/{stats['reports_started']}  •  "
            f"Candidate {totals['candidate']['signatures']}  •  "
            f"Suppressed {totals['suppressed']['signatures']}  •  "
            f"Needs review {totals['uncertain']['signatures']}"
        )
        self._status_var.set(f"Offline analysis ready for {report_name}")

    def _post_error(self, request_id: int, message: str, preview: bool):
        def show():
            current = self._preview_request_id if preview else self._analysis_request_id
            if self._closed or request_id != current:
                return
            if not preview:
                self._set_text(self._analysis_text, f"Offline analysis failed:\n{message}")
                self._start_button.configure(state="normal")
            self._status_var.set(message)

        try:
            self.after(0, show)
        except (RuntimeError, tk.TclError):
            pass

    def _save_copy(self):
        try:
            source = self._selected_file()
        except OSError as exc:
            self._status_var.set(str(exc))
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="Save ASAN report", initialfile=source.name,
            defaultextension=".txt",
        )
        if not target:
            return
        target_path = Path(target)
        if source.resolve() == target_path.resolve():
            self._status_var.set("Source and destination are the same file")
            return
        self._status_var.set(f"Saving complete report to {target_path}…")

        def worker():
            try:
                shutil.copyfile(source, target_path)
                self.after(0, lambda: self._status_var.set(f"Saved {target_path}"))
            except Exception as exc:
                self._post_save_error(str(exc))

        threading.Thread(target=worker, daemon=True).start()

    def _post_save_error(self, message: str):
        try:
            self.after(0, lambda: self._status_var.set(f"Save failed: {message}"))
        except (RuntimeError, tk.TclError):
            pass

    def _save_analysis(self):
        if not self._analysis_output:
            self._status_var.set("Run analysis first")
            return
        source = Path(self._file_var.get().strip() or "asan_report.txt")
        target = filedialog.asksaveasfilename(
            parent=self, title="Save offline ASAN analysis",
            initialfile=f"{source.stem}_offline_analysis.txt",
            defaultextension=".txt", filetypes=[("Text files", "*.txt")],
        )
        if target:
            Path(target).write_text(self._analysis_output, encoding="utf-8")
            self._status_var.set(f"Saved {target}")

    def _on_destroy(self, event):
        if event.widget is self:
            self._closed = True


if __name__ == "__main__":
    app = AsanReportTab(standalone=True)
    app.winfo_toplevel().mainloop()

"""Local ASAN report viewer with deterministic offline analysis."""

from __future__ import annotations

import shutil
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

try:
    from offline_analyzer import analyze_file, format_analysis
except ImportError:  # Package-style import, useful for tests and reuse.
    from .offline_analyzer import analyze_file, format_analysis


C_BG = "#F3F0FA"
C_SURFACE = "#FFFFFF"
C_TEXT = "#1A1820"
C_MUTED = "#5C5870"
C_ACCENT = "#5B3EA6"

MAX_REPORT_BYTES = 16 * 1024 * 1024
TRUNCATION_NOTICE = "\n\n[Raw preview truncated by viewer at 16 MiB]\n"


def read_local_report(path: Path) -> str:
    """Read a bounded preview; analysis streams the complete file separately."""
    with path.open("rb") as stream:
        data = stream.read(MAX_REPORT_BYTES + 1)
    suffix = b""
    if len(data) > MAX_REPORT_BYTES:
        data = data[:MAX_REPORT_BYTES]
        suffix = TRUNCATION_NOTICE.encode("utf-8")
    return (data + suffix).decode("utf-8", errors="replace")


class AsanReportTab(tk.Frame):
    def __init__(self, master=None, standalone: bool = False):
        if master is None:
            master = tk.Tk()
            standalone = True
        super().__init__(master, bg=C_BG)
        self._closed = False
        self._preview_request_id = 0
        self._analysis_request_id = 0
        self._analysis_output = ""
        self._file_var = tk.StringVar()
        self._status_var = tk.StringVar(value="Select an ASAN report file")
        self._summary_var = tk.StringVar(value="No report analyzed")

        if standalone:
            root = self.winfo_toplevel()
            root.title("ASAN Report Analyzer")
            root.geometry("1100x760")
            self.pack(fill=tk.BOTH, expand=True)

        self._build_ui()
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_ui(self):
        controls = tk.Frame(self, bg=C_SURFACE, padx=12, pady=10)
        controls.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(controls, text="ASAN report:", bg=C_SURFACE, fg=C_TEXT).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(controls, textvariable=self._file_var).grid(
            row=0, column=1, padx=(6, 6), sticky="ew"
        )
        ttk.Button(controls, text="Browse…", command=self._browse).grid(
            row=0, column=2, padx=(0, 6)
        )
        self._start_button = ttk.Button(
            controls, text="Start Analysis", command=self._start_analysis
        )
        self._start_button.grid(row=0, column=3, padx=(0, 6))
        ttk.Button(controls, text="Save Copy…", command=self._save_copy).grid(
            row=0, column=4, padx=(0, 6)
        )
        self._save_analysis_button = ttk.Button(
            controls, text="Save Analysis…", command=self._save_analysis,
            state=tk.DISABLED,
        )
        self._save_analysis_button.grid(row=0, column=5)
        tk.Label(
            controls, textvariable=self._status_var, bg=C_SURFACE, fg=C_MUTED,
            anchor="w",
        ).grid(row=1, column=0, columnspan=6, pady=(8, 0), sticky="ew")
        controls.grid_columnconfigure(1, weight=1)

        summary = tk.Frame(self, bg="#EEEAF8", padx=12, pady=9)
        summary.pack(fill=tk.X, padx=10)
        tk.Label(
            summary, text="ASAN Summary", bg="#EEEAF8", fg=C_ACCENT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            summary, textvariable=self._summary_var, bg="#EEEAF8", fg=C_TEXT,
            anchor="e",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        self._tabs = ttk.Notebook(self)
        self._tabs.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        raw_tab = tk.Frame(self._tabs, bg=C_SURFACE)
        analysis_tab = tk.Frame(self._tabs, bg=C_SURFACE)
        self._tabs.add(raw_tab, text="Raw Report")
        self._tabs.add(analysis_tab, text="Offline Analysis")

        self._text = self._add_text_view(raw_tab, wrap=tk.NONE)
        self._analysis_text = self._add_text_view(analysis_tab, wrap=tk.WORD)
        self._set_text(
            self._analysis_text,
            "Select a report, then click Start Analysis.",
        )

    @staticmethod
    def _add_text_view(parent, wrap):
        frame = tk.Frame(parent, bg=C_SURFACE)
        frame.pack(fill=tk.BOTH, expand=True)
        text = tk.Text(
            frame, wrap=wrap, font=("Consolas", 9), bg="#FCFCFE",
            fg=C_TEXT, insertbackground=C_TEXT, padx=10, pady=10,
        )
        ybar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=text.yview)
        text.configure(yscrollcommand=ybar.set)
        text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        text.configure(state=tk.DISABLED)
        return text

    @staticmethod
    def _set_text(widget, value: str):
        widget.configure(state=tk.NORMAL)
        widget.delete("1.0", tk.END)
        widget.insert("1.0", value)
        widget.configure(state=tk.DISABLED)

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
            self._start_button.configure(state=tk.NORMAL)
            self._save_analysis_button.configure(state=tk.DISABLED)
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
        if self._start_button.instate(["!disabled"]):
            note = (
                "; preview limited to 16 MiB"
                if path.stat().st_size > MAX_REPORT_BYTES else ""
            )
            self._status_var.set(f"Selected {path.name}{note}; click Start Analysis")
            self._tabs.select(0)

    def _start_analysis(self):
        try:
            path = self._selected_file()
        except OSError as exc:
            self._status_var.set(str(exc))
            return

        self._analysis_request_id += 1
        request_id = self._analysis_request_id
        self._analysis_output = ""
        self._start_button.configure(state=tk.DISABLED)
        self._save_analysis_button.configure(state=tk.DISABLED)
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
        self._start_button.configure(state=tk.NORMAL)
        self._save_analysis_button.configure(state=tk.NORMAL)
        self._tabs.select(1)
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
                self._start_button.configure(state=tk.NORMAL)
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
    app.mainloop()

"""Standalone tcMalloc heap browser and remote PDF report generator."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import subprocess
import sys
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List, Optional, Tuple


C_BG = "#F3F0FA"
C_SURFACE = "#FFFFFF"
C_TEXT = "#1A1820"
C_MUTED = "#5C5870"
C_ACCENT = "#5B3EA6"
C_OK = "#2E7D32"
C_WARN = "#B26A00"
C_ERROR = "#B71C1C"

DATA_DIR = Path.home() / ".tcmalloc_report_viewer"
REPORT_DIR = DATA_DIR / "reports"
SETTINGS_FILE = DATA_DIR / "settings.json"


def _load_settings() -> dict:
    try:
        return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return {}


def _connect_egm(ip: str):
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError(
            "paramiko is not installed; run: py -3 -m pip install paramiko"
        ) from exc
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username="mk7", password="mk7", timeout=15)
    return client


def _heap_number(path: str) -> str:
    match = re.search(r"\.(\d+)\.heap$", path, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def list_remote_heaps(ip: str, search_root: str) -> List[dict]:
    root = shlex.quote(search_root)
    command = (
        f"find {root} -type f -name '*.heap' "
        r"-path '*/scratch/.logs/mem_profile*/*' "
        r"-printf '%T@\t%s\t%p\n' 2>/dev/null | sort -nr"
    )
    client = _connect_egm(ip)
    try:
        _, stdout, stderr = client.exec_command(command)
        exit_code = stdout.channel.recv_exit_status()
        output = stdout.read().decode(errors="replace")
        if exit_code not in (0, 1):
            error = stderr.read().decode(errors="replace").strip()
            raise RuntimeError(error or f"Remote find failed with exit {exit_code}")
    finally:
        client.close()

    records = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            modified, size = float(parts[0]), int(parts[1])
        except ValueError:
            continue
        records.append({
            "path": parts[2], "modified": modified, "size": size,
            "number": _heap_number(parts[2]),
        })
    return records


def analyze_remote_heap(ip: str, search_root: str, heap_path: str) -> Tuple[Path, str]:
    """Run tcMalloc_profiler.sh for ``heap_path`` and download its PDF."""
    end_number = _heap_number(heap_path)
    if not end_number:
        raise ValueError(f"Could not determine heap number from: {heap_path}")

    client = _connect_egm(ip)
    try:
        find_script = (
            f"find {shlex.quote(search_root)} -type f -name 'tcMalloc_profiler.sh' "
            r"-path '*/common/build/*' -print -quit 2>/dev/null"
        )
        _, stdout, stderr = client.exec_command(find_script)
        stdout.channel.recv_exit_status()
        script = stdout.read().decode(errors="replace").strip()
        if not script:
            error = stderr.read().decode(errors="replace").strip()
            raise FileNotFoundError(
                "tcMalloc_profiler.sh was not found below the search root"
                + (f": {error}" if error else "")
            )

        script_dir = posixpath.dirname(script)
        heap_dir = posixpath.dirname(heap_path)
        command = (
            f"cd {shlex.quote(script_dir)} && "
            f"./{shlex.quote(posixpath.basename(script))} "
            f"-endnum {shlex.quote(end_number)} -location {shlex.quote(heap_dir)}"
        )
        _, stdout, stderr = client.exec_command(command, timeout=300)
        exit_code = stdout.channel.recv_exit_status()
        console = stdout.read().decode(errors="replace")
        error_text = stderr.read().decode(errors="replace")
        combined = console + ("\n" + error_text if error_text else "")
        if exit_code != 0:
            raise RuntimeError(
                f"tcMalloc_profiler.sh failed with exit {exit_code}\n\n{combined.strip()}"
            )

        find_pdf = (
            f"find {shlex.quote(script_dir)} -maxdepth 1 -type f "
            r"-iname '*tcmalloc*.pdf' "
            r"-printf '%T@\t%p\n' 2>/dev/null | sort -nr | head -1"
        )
        _, pdf_out, pdf_err = client.exec_command(find_pdf)
        pdf_out.channel.recv_exit_status()
        result = pdf_out.read().decode(errors="replace").strip()
        if not result:
            error = pdf_err.read().decode(errors="replace").strip()
            raise FileNotFoundError(
                "Profiler completed but no PDF was found in " + script_dir
                + (f": {error}" if error else "")
            )
        remote_pdf = result.split("\t", 1)[-1]

        identity = hashlib.sha1(f"{ip}\n{remote_pdf}".encode()).hexdigest()[:10]
        REPORT_DIR.mkdir(parents=True, exist_ok=True)
        local_pdf = REPORT_DIR / f"{identity}_{posixpath.basename(remote_pdf)}"
        temporary = local_pdf.with_suffix(".downloading")
        sftp = client.open_sftp()
        try:
            sftp.get(remote_pdf, str(temporary))
            os.replace(temporary, local_pdf)
        finally:
            sftp.close()
        return local_pdf, combined
    finally:
        client.close()


class TcMallocReportTab(tk.Frame):
    def __init__(self, master=None, standalone: bool = False):
        if master is None:
            master = tk.Tk()
            standalone = True
        super().__init__(master, bg=C_BG)
        self._standalone = standalone
        self._closed = False
        self._request_id = 0
        self._records: List[dict] = []
        self._display_records: Dict[str, dict] = {}
        self._selected_record: Optional[dict] = None
        self._latest_pdf: Optional[Path] = None

        settings = _load_settings()
        mode = settings.get("mode", "Remote EGM")
        if mode not in {"Remote EGM", "Local folder"}:
            mode = "Remote EGM"
        self._mode_var = tk.StringVar(value=mode)
        self._ip_var = tk.StringVar(value=settings.get("ip", ""))
        self._root_var = tk.StringVar(
            value=settings.get("root", "") or "/home/mk7/development"
        )
        self._local_var = tk.StringVar(value=settings.get("local", ""))
        self._filter_var = tk.StringVar(value="")
        self._status_var = tk.StringVar(value="Choose a source and refresh")
        self._selection_var = tk.StringVar(value="No heap selected")

        if standalone:
            root = self.winfo_toplevel()
            root.title("tcMalloc Report Analyzer")
            root.geometry("1280x780")
            self.pack(fill=tk.BOTH, expand=True)

        self._build_ui()
        for variable in (self._mode_var, self._ip_var, self._root_var, self._local_var):
            variable.trace_add("write", self._save_settings)
        self._filter_var.trace_add("write", lambda *_: self._populate_tree())
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_ui(self):
        controls = tk.Frame(self, bg=C_SURFACE, padx=12, pady=10)
        controls.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(controls, text="Source:", bg=C_SURFACE, fg=C_TEXT).grid(row=0, column=0)
        mode = ttk.Combobox(
            controls, textvariable=self._mode_var,
            values=["Remote EGM", "Local folder"], state="readonly", width=13,
        )
        mode.grid(row=0, column=1, padx=(5, 12), sticky="w")
        mode.bind("<<ComboboxSelected>>", self._mode_changed)

        self._remote_fields = tk.Frame(controls, bg=C_SURFACE)
        tk.Label(self._remote_fields, text="IP:", bg=C_SURFACE).pack(side=tk.LEFT)
        ttk.Entry(self._remote_fields, textvariable=self._ip_var, width=17).pack(
            side=tk.LEFT, padx=(4, 10)
        )
        tk.Label(self._remote_fields, text="Build/search root:", bg=C_SURFACE).pack(side=tk.LEFT)
        ttk.Entry(self._remote_fields, textvariable=self._root_var, width=58).pack(
            side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True
        )

        self._local_fields = tk.Frame(controls, bg=C_SURFACE)
        tk.Label(self._local_fields, text="Heap folder:", bg=C_SURFACE).pack(side=tk.LEFT)
        ttk.Entry(self._local_fields, textvariable=self._local_var, width=64).pack(
            side=tk.LEFT, padx=(4, 4), fill=tk.X, expand=True
        )
        ttk.Button(self._local_fields, text="Browse…", command=self._browse).pack(side=tk.LEFT)

        ttk.Button(controls, text="Refresh Heaps", command=self.refresh).grid(
            row=1, column=1, pady=(9, 0), sticky="w"
        )
        tk.Label(controls, text="Filter:", bg=C_SURFACE).grid(
            row=1, column=2, pady=(9, 0), padx=(12, 4), sticky="e"
        )
        ttk.Entry(controls, textvariable=self._filter_var, width=32).grid(
            row=1, column=3, pady=(9, 0), sticky="w"
        )
        self._analyze_button = ttk.Button(
            controls, text="Analyze Selected Heap", command=self._analyze,
        )
        self._analyze_button.grid(row=1, column=4, pady=(9, 0), padx=(8, 0), sticky="w")
        ttk.Button(controls, text="Open PDF", command=self._open_pdf).grid(
            row=1, column=5, pady=(9, 0), padx=(4, 0), sticky="w"
        )
        ttk.Button(controls, text="Save PDF As…", command=self._save_pdf).grid(
            row=1, column=6, pady=(9, 0), padx=(4, 0), sticky="w"
        )
        controls.grid_columnconfigure(3, weight=1)
        self._mode_changed()

        notice = tk.Frame(self, bg="#FFF4D6", padx=12, pady=7)
        notice.pack(fill=tk.X, padx=10, pady=(0, 6))
        tk.Label(
            notice,
            text="tcMalloc analysis runs tcMalloc_profiler.sh on the EGM and downloads the generated PDF. "
                 "ASAN and tcMalloc instrumented builds are mutually exclusive.",
            bg="#FFF4D6", fg="#714B00", anchor="w",
        ).pack(fill=tk.X)

        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=C_BG, sashwidth=5, bd=0)
        pane.pack(fill=tk.BOTH, expand=True, padx=10)
        left = tk.Frame(pane, bg=C_SURFACE)
        right = tk.Frame(pane, bg=C_SURFACE)
        pane.add(left, minsize=430)
        pane.add(right, stretch="always", minsize=500)

        columns = ("number", "modified", "size", "path")
        self._tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        for key, title, width in (
            ("number", "Heap #", 65), ("modified", "Modified", 125),
            ("size", "Size", 80), ("path", "Heap file", 390),
        ):
            self._tree.heading(key, text=title)
            self._tree.column(key, width=width, anchor="e" if key in {"number", "size"} else "w")
        ybar = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=ybar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._heap_selected)

        header = tk.Frame(right, bg="#EEEAF8", padx=12, pady=9)
        header.pack(fill=tk.X)
        tk.Label(
            header, text="tcMalloc Analysis", bg="#EEEAF8", fg=C_ACCENT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            header, textvariable=self._selection_var, bg="#EEEAF8", fg=C_TEXT,
            anchor="e",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        self._console = tk.Text(
            right, wrap=tk.WORD, font=("Consolas", 9), bg="#FCFCFE",
            fg=C_TEXT, padx=9, pady=9,
        )
        self._console.pack(fill=tk.BOTH, expand=True)
        self._console.tag_configure("ok", foreground=C_OK, font=("Consolas", 9, "bold"))
        self._console.tag_configure("error", foreground=C_ERROR)
        self._console.configure(state=tk.DISABLED)

        status = tk.Frame(self, bg=C_SURFACE, padx=12, pady=7)
        status.pack(fill=tk.X, padx=10, pady=(6, 10))
        tk.Label(
            status, textvariable=self._status_var, bg=C_SURFACE, fg=C_MUTED,
            anchor="w",
        ).pack(fill=tk.X)

    def _mode_changed(self, _event=None):
        if self._mode_var.get() == "Remote EGM":
            self._local_fields.grid_remove()
            self._remote_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
            self._analyze_button.configure(state=tk.NORMAL)
        else:
            self._remote_fields.grid_remove()
            self._local_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
            self._analyze_button.configure(state=tk.DISABLED)
        self._request_id += 1

    def _browse(self):
        folder = filedialog.askdirectory(parent=self, title="Select tcMalloc heap folder")
        if folder:
            self._local_var.set(folder)
            self.refresh()

    def refresh(self):
        self._request_id += 1
        request_id = self._request_id
        mode = self._mode_var.get()
        ip = self._ip_var.get().strip()
        root = self._root_var.get().strip()
        local = self._local_var.get().strip()
        self._status_var.set("Finding tcMalloc heap files…")

        def worker():
            try:
                if mode == "Remote EGM":
                    if not ip or not root:
                        raise ValueError("Enter the EGM IP and build/search root")
                    records = list_remote_heaps(ip, root)
                else:
                    if not local:
                        raise ValueError("Select a local heap folder")
                    records = self._list_local(Path(local))
                self.after(0, lambda: self._refresh_done(request_id, records))
            except Exception as exc:
                message = str(exc)
                try:
                    self.after(0, lambda: self._failed(request_id, message))
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=worker, daemon=True).start()

    @staticmethod
    def _list_local(folder: Path) -> List[dict]:
        if not folder.is_dir():
            raise FileNotFoundError(f"Folder not found: {folder}")
        records = []
        for path in folder.rglob("*.heap"):
            stat = path.stat()
            records.append({
                "path": str(path), "modified": stat.st_mtime,
                "size": stat.st_size, "number": _heap_number(str(path)),
            })
        return sorted(records, key=lambda item: item["modified"], reverse=True)

    def _refresh_done(self, request_id: int, records: List[dict]):
        if self._closed or request_id != self._request_id:
            return
        self._records = records
        self._populate_tree()
        self._status_var.set(f"{len(records)} tcMalloc heap file(s) found")

    def _failed(self, request_id: int, message: str):
        if self._closed or request_id != self._request_id:
            return
        self._status_var.set(message.splitlines()[0] if message else "Operation failed")
        self._write_console(message, error=True)

    def _populate_tree(self):
        query = self._filter_var.get().strip().lower()
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._display_records.clear()
        for record in self._records:
            if query and query not in record["path"].lower():
                continue
            iid = self._tree.insert("", tk.END, values=(
                record["number"] or "—",
                datetime.fromtimestamp(record["modified"]).strftime("%d %b %H:%M:%S"),
                self._format_size(record["size"]), record["path"],
            ))
            self._display_records[iid] = record

    def _heap_selected(self, _event=None):
        selected = self._tree.selection()
        if not selected:
            return
        self._selected_record = self._display_records.get(selected[0])
        if not self._selected_record:
            return
        record = self._selected_record
        self._selection_var.set(
            f"Heap {record['number'] or '?'}  •  {self._format_size(record['size'])}  •  "
            f"{Path(record['path']).name}"
        )

    def _analyze(self):
        if self._mode_var.get() != "Remote EGM":
            self._status_var.set("Analysis requires Remote EGM mode with mk7i-pprof")
            return
        if not self._selected_record:
            self._status_var.set("Select an end heap to analyze")
            return
        ip = self._ip_var.get().strip()
        root = self._root_var.get().strip()
        if not ip or not root:
            self._status_var.set("Enter the EGM IP and build/search root")
            return
        heap_path = self._selected_record["path"]
        self._request_id += 1
        request_id = self._request_id
        self._analyze_button.configure(state=tk.DISABLED)
        self._status_var.set(f"Analyzing heap {_heap_number(heap_path)} on EGM {ip}…")
        self._write_console(
            f"Running tcMalloc_profiler.sh for:\n{heap_path}\n\nThis can take several minutes…\n"
        )

        def worker():
            try:
                pdf, console = analyze_remote_heap(ip, root, heap_path)
                self.after(0, lambda: self._analysis_done(request_id, pdf, console))
            except Exception as exc:
                message = str(exc)
                try:
                    self.after(0, lambda: self._analysis_failed(request_id, message))
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _analysis_done(self, request_id: int, pdf: Path, console: str):
        if self._closed or request_id != self._request_id:
            return
        self._latest_pdf = pdf
        self._analyze_button.configure(state=tk.NORMAL)
        self._write_console(console + f"\n\nDownloaded PDF:\n{pdf}\n", ok=True)
        self._status_var.set(f"Analysis complete: {pdf.name}")

    def _analysis_failed(self, request_id: int, message: str):
        if self._closed or request_id != self._request_id:
            return
        self._analyze_button.configure(state=tk.NORMAL)
        self._status_var.set(message.splitlines()[0] if message else "Analysis failed")
        self._write_console(message, error=True)

    def _write_console(self, text: str, ok: bool = False, error: bool = False):
        self._console.configure(state=tk.NORMAL)
        self._console.delete("1.0", tk.END)
        tag = "error" if error else "ok" if ok else None
        self._console.insert(tk.END, text, tag)
        self._console.configure(state=tk.DISABLED)

    def _open_pdf(self):
        if not self._latest_pdf or not self._latest_pdf.exists():
            self._status_var.set("Generate a tcMalloc PDF report first")
            return
        try:
            if os.name == "nt":
                os.startfile(str(self._latest_pdf))
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self._latest_pdf)])
            else:
                subprocess.Popen(["xdg-open", str(self._latest_pdf)])
        except OSError as exc:
            self._status_var.set(f"Could not open PDF: {exc}")

    def _save_pdf(self):
        if not self._latest_pdf or not self._latest_pdf.exists():
            self._status_var.set("Generate a tcMalloc PDF report first")
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="Save tcMalloc PDF", initialfile=self._latest_pdf.name,
            defaultextension=".pdf", filetypes=[("PDF files", "*.pdf")],
        )
        if target:
            shutil.copyfile(self._latest_pdf, target)
            self._status_var.set(f"Saved {target}")

    def _save_settings(self, *_args):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(json.dumps({
                "mode": self._mode_var.get(), "ip": self._ip_var.get(),
                "root": self._root_var.get(), "local": self._local_var.get(),
            }, indent=2), encoding="utf-8")
        except (OSError, tk.TclError):
            pass

    @staticmethod
    def _format_size(size: int) -> str:
        value = float(size)
        for unit in ("B", "KiB", "MiB", "GiB"):
            if value < 1024 or unit == "GiB":
                return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
            value /= 1024
        return str(size)

    def _on_destroy(self, event):
        if event.widget is self:
            self._closed = True


if __name__ == "__main__":
    app = TcMallocReportTab(standalone=True)
    app.mainloop()

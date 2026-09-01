"""Standalone tcMalloc heap browser and remote PDF report generator."""

from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
import shlex
import shutil
import stat
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


def _normalise_host_path(path: str) -> str:
    path = posixpath.normpath(path.strip())
    if not path.startswith("/") or path == "/":
        raise ValueError(
            "Enter an absolute EGM host path, for example "
            "/home/mk7/development/game/build/host"
        )
    return path


def _is_tcmalloc_config(text: str) -> bool:
    return bool(re.search(r"^\s*usetcmalloc\s*=\s*(?:true|1|yes)\s*$", text, re.I | re.M))


def _validate_remote_host(client, host_path: str):
    sftp = client.open_sftp()
    try:
        try:
            attributes = sftp.stat(host_path)
        except OSError as exc:
            raise FileNotFoundError(
                f"Host path does not exist on the EGM: {host_path}"
            ) from exc
        if not stat.S_ISDIR(attributes.st_mode):
            raise NotADirectoryError(f"Host path is not a directory: {host_path}")

        config_path = posixpath.join(host_path, ".mk7conf")
        try:
            with sftp.open(config_path, "rb") as stream:
                config = stream.read().decode("utf-8", errors="replace")
        except OSError as exc:
            raise FileNotFoundError(
                f"Invalid host path: {config_path} was not found"
            ) from exc
        if not _is_tcmalloc_config(config):
            raise ValueError(
                "Not a tcMalloc build: .mk7conf does not contain usetcmalloc=True"
            )
    finally:
        sftp.close()


def list_remote_heaps(ip: str, host_path: str) -> List[dict]:
    host_path = _normalise_host_path(host_path)
    heap_root = posixpath.join(host_path, "scratch", ".logs", "mem_profiles")
    command = (
        f"find {shlex.quote(heap_root)} -type f -name '*.heap' "
        r"-printf '%T@\t%s\t%p\n' 2>/dev/null | sort -nr"
    )
    client = _connect_egm(ip)
    try:
        _validate_remote_host(client, host_path)
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
            "name": posixpath.basename(parts[2]),
        })
    return records


def analyze_remote_heap(ip: str, host_path: str, heap_path: str) -> Tuple[Path, str]:
    """Run tcMalloc_profiler.sh for ``heap_path`` and download its PDF."""
    end_number = _heap_number(heap_path)
    if not end_number:
        raise ValueError(f"Could not determine heap number from: {heap_path}")

    host_path = _normalise_host_path(host_path)
    client = _connect_egm(ip)
    try:
        _validate_remote_host(client, host_path)
        script = posixpath.join(
            host_path, "common", "build", "tcMalloc_profiler.sh"
        )
        sftp = client.open_sftp()
        try:
            sftp.stat(script)
        except OSError as exc:
            raise FileNotFoundError(
                f"tcMalloc_profiler.sh was not found in the build: {script}"
            ) from exc
        finally:
            sftp.close()

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
        self._closed = False
        self._request_id = 0
        self._records: List[dict] = []
        self._display_records: Dict[str, dict] = {}
        self._selected_record: Optional[dict] = None
        self._latest_pdf: Optional[Path] = None

        settings = _load_settings()
        self._ip_var = tk.StringVar(value=settings.get("ip", ""))
        self._host_var = tk.StringVar(
            value=settings.get("host", "")
        )
        self._filter_var = tk.StringVar(value="")
        self._status_var = tk.StringVar(value="Enter the EGM IP and host path")
        self._selection_var = tk.StringVar(value="No heap selected")

        if standalone:
            root = self.winfo_toplevel()
            root.title("tcMalloc Report Analyzer")
            root.geometry("1280x780")
            self.pack(fill=tk.BOTH, expand=True)

        self._build_ui()
        for variable in (self._ip_var, self._host_var):
            variable.trace_add("write", self._connection_changed)
        self._filter_var.trace_add("write", self._filter_changed)
        self.bind("<Destroy>", self._on_destroy, add="+")

    def _build_ui(self):
        controls = tk.Frame(self, bg=C_SURFACE, padx=12, pady=10)
        controls.pack(fill=tk.X, padx=10, pady=(10, 6))
        tk.Label(controls, text="EGM IP:", bg=C_SURFACE, fg=C_TEXT).grid(
            row=0, column=0, sticky="w"
        )
        ttk.Entry(controls, textvariable=self._ip_var, width=18).grid(
            row=0, column=1, padx=(5, 12), sticky="w"
        )
        tk.Label(controls, text="Host path:", bg=C_SURFACE, fg=C_TEXT).grid(
            row=0, column=2, sticky="e"
        )
        ttk.Entry(controls, textvariable=self._host_var).grid(
            row=0, column=3, columnspan=3, padx=(5, 8), sticky="ew"
        )
        self._refresh_button = ttk.Button(
            controls, text="Load Heap Files", command=self.refresh
        )
        self._refresh_button.grid(row=0, column=6, sticky="w")

        tk.Label(controls, text="Filter:", bg=C_SURFACE).grid(
            row=1, column=0, pady=(9, 0), sticky="w"
        )
        ttk.Entry(controls, textvariable=self._filter_var, width=32).grid(
            row=1, column=1, columnspan=3, pady=(9, 0), padx=(5, 8), sticky="ew"
        )
        self._convert_button = ttk.Button(
            controls, text="Convert to PDF", command=self._analyze,
            state=tk.DISABLED,
        )
        self._convert_button.grid(row=1, column=4, pady=(9, 0), sticky="w")
        ttk.Button(controls, text="Open PDF", command=self._open_pdf).grid(
            row=1, column=5, pady=(9, 0), padx=(4, 0), sticky="w"
        )
        ttk.Button(controls, text="Save PDF As…", command=self._save_pdf).grid(
            row=1, column=6, pady=(9, 0), padx=(4, 0), sticky="w"
        )
        controls.grid_columnconfigure(3, weight=1)

        notice = tk.Frame(self, bg="#FFF4D6", padx=12, pady=7)
        notice.pack(fill=tk.X, padx=10, pady=(0, 6))
        tk.Label(
            notice,
            text="Select a heap file, then click Convert to PDF. "
                 "The build is validated using .mk7conf inside the host path.",
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

    def refresh(self):
        self._request_id += 1
        request_id = self._request_id
        ip = self._ip_var.get().strip()
        host = self._host_var.get().strip()
        if not ip or not host:
            self._status_var.set("Enter the EGM IP and host path")
            return
        self._refresh_button.configure(state=tk.DISABLED)
        self._convert_button.configure(state=tk.DISABLED)
        self._tree.configure(selectmode="none")
        self._records = []
        self._populate_tree()
        self._selected_record = None
        self._selection_var.set("No heap selected")
        self._status_var.set("Validating build and finding tcMalloc heap files…")

        def worker():
            try:
                records = list_remote_heaps(ip, host)
                self.after(0, lambda: self._refresh_done(request_id, records))
            except Exception as exc:
                message = str(exc)
                try:
                    self.after(0, lambda: self._failed(request_id, message))
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _refresh_done(self, request_id: int, records: List[dict]):
        if self._closed or request_id != self._request_id:
            return
        self._records = records
        self._populate_tree()
        self._refresh_button.configure(state=tk.NORMAL)
        self._convert_button.configure(state=tk.DISABLED)
        self._tree.configure(selectmode="browse")
        self._status_var.set(f"{len(records)} tcMalloc heap file(s) found")

    def _failed(self, request_id: int, message: str):
        if self._closed or request_id != self._request_id:
            return
        self._refresh_button.configure(state=tk.NORMAL)
        self._convert_button.configure(state=tk.DISABLED)
        self._tree.configure(selectmode="browse")
        self._status_var.set(message.splitlines()[0] if message else "Operation failed")
        self._write_console(message, error=True)

    def _populate_tree(self):
        query = self._filter_var.get().strip().lower()
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._display_records.clear()
        for record in self._records:
            if query and query not in record["name"].lower():
                continue
            iid = self._tree.insert("", tk.END, values=(
                record["number"] or "—",
                datetime.fromtimestamp(record["modified"]).strftime("%d %b %H:%M:%S"),
                self._format_size(record["size"]), record["name"],
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
            f"{record['name']}"
        )
        self._convert_button.configure(
            state=tk.NORMAL if self._selected_record else tk.DISABLED
        )
        self._status_var.set(f"Selected {record['name']}; click Convert to PDF")

    def _analyze(self):
        if not self._selected_record:
            return
        ip = self._ip_var.get().strip()
        host = self._host_var.get().strip()
        if not ip or not host:
            self._status_var.set("Enter the EGM IP and host path")
            return
        heap_path = self._selected_record["path"]
        self._request_id += 1
        request_id = self._request_id
        self._latest_pdf = None
        self._convert_button.configure(state=tk.DISABLED)
        self._tree.state(["disabled"])
        self._refresh_button.configure(state=tk.DISABLED)
        self._status_var.set(f"Analyzing heap {_heap_number(heap_path)} on EGM {ip}…")
        self._write_console(
            f"Running tcMalloc_profiler.sh for:\n{heap_path}\n\nThis can take several minutes…\n"
        )

        def worker():
            try:
                pdf, console = analyze_remote_heap(ip, host, heap_path)
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
        self._tree.state(["!disabled"])
        self._refresh_button.configure(state=tk.NORMAL)
        self._convert_button.configure(
            state=tk.NORMAL if self._selected_record else tk.DISABLED
        )
        self._write_console(console + f"\n\nDownloaded PDF:\n{pdf}\n", ok=True)
        self._status_var.set(f"Analysis complete: {pdf.name}")

    def _analysis_failed(self, request_id: int, message: str):
        if self._closed or request_id != self._request_id:
            return
        self._tree.state(["!disabled"])
        self._refresh_button.configure(state=tk.NORMAL)
        self._convert_button.configure(
            state=tk.NORMAL if self._selected_record else tk.DISABLED
        )
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
                "ip": self._ip_var.get(), "host": self._host_var.get(),
            }, indent=2), encoding="utf-8")
        except (OSError, tk.TclError):
            pass

    def _connection_changed(self, *_args):
        self._save_settings()
        self._request_id += 1
        self._records = []
        self._selected_record = None
        self._populate_tree()
        self._tree.state(["!disabled"])
        self._tree.configure(selectmode="browse")
        self._refresh_button.configure(state=tk.NORMAL)
        self._convert_button.configure(state=tk.DISABLED)
        self._selection_var.set("No heap selected")
        self._status_var.set("Click Load Heap Files to validate this build")

    def _filter_changed(self, *_args):
        self._selected_record = None
        self._convert_button.configure(state=tk.DISABLED)
        self._selection_var.set("No heap selected")
        self._populate_tree()

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

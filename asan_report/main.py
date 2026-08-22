"""Standalone ASAN report browser for local files and remote EGMs."""

from __future__ import annotations

import json
import re
import shlex
import threading
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, List


C_BG = "#F3F0FA"
C_SURFACE = "#FFFFFF"
C_TEXT = "#1A1820"
C_MUTED = "#5C5870"
C_ACCENT = "#5B3EA6"
C_ERROR = "#B71C1C"
C_WARN = "#B26A00"

DATA_DIR = Path.home() / ".asan_report_viewer"
SETTINGS_FILE = DATA_DIR / "settings.json"
MAX_REPORT_BYTES = 16 * 1024 * 1024


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


def list_remote_reports(ip: str, search_root: str) -> List[dict]:
    """Return ASAN-like text reports below an EGM build/search root."""
    root = shlex.quote(search_root)
    command = (
        f"find {root} -type f "
        r"\( -path '*/scratch/.logs/mem_profile*/*' "
        r"-o -iname '*asan*.log*' -o -iname '*asan*.txt*' \) "
        r"! -name '*.heap' ! -name '*.pdf' "
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

    reports = []
    for line in output.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue
        try:
            modified, size = float(parts[0]), int(parts[1])
        except ValueError:
            continue
        reports.append({"path": parts[2], "modified": modified, "size": size})
    return reports


def read_remote_report(ip: str, remote_path: str) -> str:
    client = _connect_egm(ip)
    try:
        sftp = client.open_sftp()
        try:
            with sftp.open(remote_path, "rb") as stream:
                data = stream.read(MAX_REPORT_BYTES + 1)
        finally:
            sftp.close()
    finally:
        client.close()
    if len(data) > MAX_REPORT_BYTES:
        data = data[:MAX_REPORT_BYTES]
        suffix = b"\n\n[Report truncated by viewer at 16 MiB]\n"
    else:
        suffix = b""
    return (data + suffix).decode("utf-8", errors="replace")


def summarize_asan(text: str) -> dict:
    types = re.findall(
        r"ERROR:\s*AddressSanitizer:\s*([^\s:]+)", text,
        flags=re.IGNORECASE,
    )
    direct = sum(int(value) for value in re.findall(
        r"^\s*Direct leak of\s+(\d+)\s+byte", text,
        flags=re.IGNORECASE | re.MULTILINE,
    ))
    indirect = sum(int(value) for value in re.findall(
        r"^\s*Indirect leak of\s+(\d+)\s+byte", text,
        flags=re.IGNORECASE | re.MULTILINE,
    ))
    leak_summary = re.search(
        r"SUMMARY:\s*AddressSanitizer:\s*(\d+)\s+byte(?:\(s\)|s)? leaked in\s+"
        r"(\d+)\s+allocation(?:\(s\)|s)?", text, flags=re.IGNORECASE,
    )
    return {
        "errors": len(re.findall(r"ERROR:\s*AddressSanitizer", text, re.I)),
        "summaries": len(re.findall(r"SUMMARY:\s*AddressSanitizer", text, re.I)),
        "types": sorted(set(types)),
        "direct_bytes": direct,
        "indirect_bytes": indirect,
        "leaked_bytes": int(leak_summary.group(1)) if leak_summary else direct + indirect,
        "allocations": int(leak_summary.group(2)) if leak_summary else 0,
        "suppressions": "Suppressions used:" in text,
    }


class AsanReportTab(tk.Frame):
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
        self._loaded_text = ""
        self._loaded_name = ""

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
        self._summary_var = tk.StringVar(value="No report selected")

        if standalone:
            root = self.winfo_toplevel()
            root.title("ASAN Report Viewer")
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
        tk.Label(self._local_fields, text="Report folder:", bg=C_SURFACE).pack(side=tk.LEFT)
        ttk.Entry(self._local_fields, textvariable=self._local_var, width=64).pack(
            side=tk.LEFT, padx=(4, 4), fill=tk.X, expand=True
        )
        ttk.Button(self._local_fields, text="Browse…", command=self._browse).pack(side=tk.LEFT)

        ttk.Button(controls, text="Refresh Reports", command=self.refresh).grid(
            row=1, column=1, pady=(9, 0), sticky="w"
        )
        tk.Label(controls, text="Filter:", bg=C_SURFACE).grid(
            row=1, column=2, pady=(9, 0), padx=(12, 4), sticky="e"
        )
        ttk.Entry(controls, textvariable=self._filter_var, width=35).grid(
            row=1, column=3, pady=(9, 0), sticky="w"
        )
        ttk.Button(controls, text="Save Copy…", command=self._save_copy).grid(
            row=1, column=4, pady=(9, 0), padx=(8, 0), sticky="w"
        )
        tk.Label(
            controls, textvariable=self._status_var, bg=C_SURFACE, fg=C_MUTED,
            anchor="e",
        ).grid(row=1, column=5, pady=(9, 0), padx=(12, 0), sticky="ew")
        controls.grid_columnconfigure(3, weight=1)
        controls.grid_columnconfigure(5, weight=1)
        self._mode_changed()

        pane = tk.PanedWindow(self, orient=tk.HORIZONTAL, bg=C_BG, sashwidth=5, bd=0)
        pane.pack(fill=tk.BOTH, expand=True, padx=10, pady=(0, 10))
        left = tk.Frame(pane, bg=C_SURFACE)
        right = tk.Frame(pane, bg=C_SURFACE)
        pane.add(left, minsize=360)
        pane.add(right, stretch="always", minsize=550)

        columns = ("modified", "size", "path")
        self._tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="browse")
        self._tree.heading("modified", text="Modified")
        self._tree.heading("size", text="Size")
        self._tree.heading("path", text="Report")
        self._tree.column("modified", width=125, anchor="w")
        self._tree.column("size", width=75, anchor="e")
        self._tree.column("path", width=360, anchor="w")
        ybar = ttk.Scrollbar(left, orient=tk.VERTICAL, command=self._tree.yview)
        self._tree.configure(yscrollcommand=ybar.set)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        ybar.pack(side=tk.RIGHT, fill=tk.Y)
        self._tree.bind("<<TreeviewSelect>>", self._report_selected)

        summary = tk.Frame(right, bg="#EEEAF8", padx=12, pady=9)
        summary.pack(fill=tk.X)
        tk.Label(
            summary, text="ASAN Summary", bg="#EEEAF8", fg=C_ACCENT,
            font=("Segoe UI", 11, "bold"),
        ).pack(side=tk.LEFT)
        tk.Label(
            summary, textvariable=self._summary_var, bg="#EEEAF8", fg=C_TEXT,
            anchor="e",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True)

        text_frame = tk.Frame(right, bg=C_SURFACE)
        text_frame.pack(fill=tk.BOTH, expand=True)
        self._text = tk.Text(
            text_frame, wrap=tk.NONE, font=("Consolas", 9), bg="#FCFCFE",
            fg=C_TEXT, insertbackground=C_TEXT, padx=8, pady=8,
        )
        ytext = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self._text.yview)
        xtext = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=self._text.xview)
        self._text.configure(yscrollcommand=ytext.set, xscrollcommand=xtext.set)
        self._text.grid(row=0, column=0, sticky="nsew")
        ytext.grid(row=0, column=1, sticky="ns")
        xtext.grid(row=1, column=0, sticky="ew")
        text_frame.grid_rowconfigure(0, weight=1)
        text_frame.grid_columnconfigure(0, weight=1)
        self._text.tag_configure("error", foreground=C_ERROR, font=("Consolas", 9, "bold"))
        self._text.tag_configure("summary", foreground=C_ACCENT, font=("Consolas", 9, "bold"))
        self._text.tag_configure("leak", foreground=C_WARN)
        self._text.configure(state=tk.DISABLED)

    def _mode_changed(self, _event=None):
        if self._mode_var.get() == "Remote EGM":
            self._local_fields.grid_remove()
            self._remote_fields.grid(row=0, column=2, columnspan=4, sticky="ew")
        else:
            self._remote_fields.grid_remove()
            self._local_fields.grid(row=0, column=2, columnspan=4, sticky="ew")
        self._request_id += 1

    def _browse(self):
        folder = filedialog.askdirectory(parent=self, title="Select ASAN report folder")
        if folder:
            self._local_var.set(folder)
            self.refresh()

    def refresh(self):
        self._request_id += 1
        request_id = self._request_id
        self._status_var.set("Finding ASAN reports…")
        mode = self._mode_var.get()
        ip = self._ip_var.get().strip()
        root = self._root_var.get().strip()
        local = self._local_var.get().strip()

        def worker():
            try:
                if mode == "Remote EGM":
                    if not ip or not root:
                        raise ValueError("Enter the EGM IP and build/search root")
                    records = list_remote_reports(ip, root)
                else:
                    if not local:
                        raise ValueError("Select a local report folder")
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
        for path in folder.rglob("*"):
            if not path.is_file() or path.suffix.lower() in {".heap", ".pdf"}:
                continue
            stat = path.stat()
            records.append({"path": str(path), "modified": stat.st_mtime, "size": stat.st_size})
        return sorted(records, key=lambda item: item["modified"], reverse=True)

    def _refresh_done(self, request_id: int, records: List[dict]):
        if self._closed or request_id != self._request_id:
            return
        self._records = records
        self._populate_tree()
        self._status_var.set(f"{len(records)} ASAN report(s) found")

    def _failed(self, request_id: int, message: str):
        if not self._closed and request_id == self._request_id:
            self._status_var.set(message)

    def _populate_tree(self):
        query = self._filter_var.get().strip().lower()
        for item in self._tree.get_children():
            self._tree.delete(item)
        self._display_records.clear()
        for record in self._records:
            path = record["path"]
            if query and query not in path.lower():
                continue
            iid = self._tree.insert("", tk.END, values=(
                datetime.fromtimestamp(record["modified"]).strftime("%d %b %H:%M:%S"),
                self._format_size(record["size"]),
                path,
            ))
            self._display_records[iid] = record

    def _report_selected(self, _event=None):
        selected = self._tree.selection()
        if not selected:
            return
        record = self._display_records.get(selected[0])
        if not record:
            return
        self._request_id += 1
        request_id = self._request_id
        path = record["path"]
        mode = self._mode_var.get()
        ip = self._ip_var.get().strip()
        self._status_var.set(f"Loading {Path(path).name}…")

        def worker():
            try:
                text = read_remote_report(ip, path) if mode == "Remote EGM" else Path(path).read_text(
                    encoding="utf-8", errors="replace"
                )
                self.after(0, lambda: self._show_report(request_id, path, text))
            except Exception as exc:
                message = str(exc)
                try:
                    self.after(0, lambda: self._failed(request_id, message))
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _show_report(self, request_id: int, path: str, text: str):
        if self._closed or request_id != self._request_id:
            return
        self._loaded_text = text
        self._loaded_name = Path(path).name
        summary = summarize_asan(text)
        kinds = ", ".join(summary["types"]) or "no sanitizer signature"
        self._summary_var.set(
            f"Errors {summary['errors']}  •  Leaked {self._format_size(summary['leaked_bytes'])}  "
            f"•  Allocations {summary['allocations']}  •  {kinds}"
        )
        self._text.configure(state=tk.NORMAL)
        self._text.delete("1.0", tk.END)
        for line in text.splitlines(keepends=True):
            tag = None
            if "ERROR: AddressSanitizer" in line:
                tag = "error"
            elif "SUMMARY: AddressSanitizer" in line or "Suppressions used:" in line:
                tag = "summary"
            elif re.search(r"(?:Direct|Indirect|Possible) leak", line, re.I):
                tag = "leak"
            self._text.insert(tk.END, line, tag)
        self._text.configure(state=tk.DISABLED)
        self._status_var.set(f"Loaded {self._loaded_name}")

    def _save_copy(self):
        if not self._loaded_text:
            self._status_var.set("Select and load a report first")
            return
        target = filedialog.asksaveasfilename(
            parent=self, title="Save ASAN report",
            initialfile=self._loaded_name or "asan_report.txt",
            defaultextension=".txt",
        )
        if target:
            Path(target).write_text(self._loaded_text, encoding="utf-8")
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
    app = AsanReportTab(standalone=True)
    app.mainloop()

"""Standalone tcMalloc heap browser and remote PDF report generator.

UI is CustomTkinter (the heap list stays a ttk.Treeview via widgets.TreePane,
as CustomTkinter has no tree widget). Everything from the SSH listing through
pprof invocation and PDF download is unchanged from the plain-tkinter version.
"""

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
from tkinter import filedialog
from typing import Dict, List, Optional, Tuple

# The shared style layer lives at the repository root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customtkinter as ctk  # noqa: E402

from common import theme, widgets, window  # noqa: E402
from common.widgets import Card, LogPane, TreePane, font  # noqa: E402


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


# Secondary-action buttons (same pair Robot Builder uses locally).
_NEUTRAL = ("gray70", "gray35")
_NEUTRAL_HOVER = ("gray60", "gray45")

# Treeview columns: (name, heading, nominal width, anchor, stretch).
_TREE_COLUMNS = (
    ("number", "Heap #", 60, "e", False),
    ("modified", "Modified", 130, "w", False),
    ("size", "Size", 80, "e", False),
    ("path", "Heap file", 220, "w", True),
)


class TcMallocReportTab(ctk.CTkFrame):
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
        if self._standalone:
            self._root_window.title("tcMalloc Report Analyzer")

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

        self._build_ui()
        for variable in (self._ip_var, self._host_var):
            variable.trace_add("write", self._connection_changed)
        self._filter_var.trace_add("write", self._filter_changed)
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
        body.pack(fill=tk.BOTH, expand=True, padx=body_pad, pady=(10, 4))
        self._controls_section(body)
        self._notice_section(body)
        self._browser_section(body)
        self._status_bar()

    def _build_header(self):
        hdr = ctk.CTkFrame(self, fg_color=theme.ACCENT, corner_radius=0)
        hdr.pack(fill=tk.X)
        ctk.CTkLabel(hdr, text="tcMalloc Report Analyzer", font=font("title"),
                     text_color="#FFFFFF").pack(pady=(14, 0))
        ctk.CTkLabel(hdr, text="Browse heap snapshots and generate pprof PDF reports",
                     font=font("small"), text_color="#C4B4F4").pack(pady=(0, 12))

    def _label(self, parent, text: str, **kwargs) -> ctk.CTkLabel:
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("font", font("body"))
        return ctk.CTkLabel(parent, text=text, **kwargs)

    def _button(self, parent, text: str, command, primary: bool = True,
                **kwargs) -> ctk.CTkButton:
        kwargs.setdefault("height", 30)
        if primary:
            kwargs.setdefault("fg_color", theme.ACCENT)
            kwargs.setdefault("hover_color", theme.ACCENT_HOVER)
            kwargs.setdefault("font", font("heading"))
        else:
            kwargs.setdefault("fg_color", _NEUTRAL)
            kwargs.setdefault("hover_color", _NEUTRAL_HOVER)
            kwargs.setdefault("text_color", theme.BODY_FG)
            kwargs.setdefault("font", font("body"))
        return ctk.CTkButton(parent, text=text, command=command, **kwargs)

    def _controls_section(self, parent):
        card = Card(parent, title="EGM Connection")
        card.pack(fill=tk.X, pady=(0, 10))
        f = card.body
        f.columnconfigure(0, weight=0)
        f.columnconfigure(3, weight=1)

        self._label(f, "EGM IP").grid(row=0, column=0, sticky="w")
        ctk.CTkEntry(f, textvariable=self._ip_var, width=170, height=30,
                     font=font("body")).grid(row=0, column=1, padx=(6, 14), sticky="w")
        self._label(f, "Host path", anchor="e").grid(row=0, column=2, sticky="e")
        ctk.CTkEntry(f, textvariable=self._host_var, height=30,
                     font=font("body")).grid(
            row=0, column=3, columnspan=3, padx=(6, 10), sticky="ew")
        self._refresh_button = self._button(f, "Load Heap Files", self.refresh, width=150)
        self._refresh_button.grid(row=0, column=6, sticky="w")

        self._label(f, "Filter").grid(row=1, column=0, pady=(10, 0), sticky="w")
        ctk.CTkEntry(f, textvariable=self._filter_var, height=30,
                     placeholder_text="Match heap file name…",
                     font=font("body")).grid(
            row=1, column=1, columnspan=3, pady=(10, 0), padx=(6, 10), sticky="ew")
        self._convert_button = self._button(
            f, "Convert to PDF", self._analyze, width=150, state="disabled")
        self._convert_button.grid(row=1, column=4, pady=(10, 0), sticky="w")
        self._button(f, "Open PDF", self._open_pdf, primary=False, width=100).grid(
            row=1, column=5, pady=(10, 0), padx=(6, 0), sticky="w")
        self._button(f, "Save PDF As…", self._save_pdf, primary=False, width=120).grid(
            row=1, column=6, pady=(10, 0), padx=(6, 0), sticky="w")

    def _notice_section(self, parent):
        notice = ctk.CTkFrame(parent, fg_color=theme.SUNKEN_BG, corner_radius=8)
        notice.pack(fill=tk.X, pady=(0, 10))
        ctk.CTkLabel(
            notice,
            text="Select a heap file, then click Convert to PDF. "
                 "The build is validated using .mk7conf inside the host path.",
            font=font("small"), text_color=theme.WARN_FG, anchor="w",
        ).pack(fill=tk.X, padx=14, pady=8)

    def _browser_section(self, parent):
        split = ctk.CTkFrame(parent, fg_color="transparent")
        split.pack(fill=tk.BOTH, expand=True)
        split.columnconfigure(0, weight=2, uniform="pane")
        split.columnconfigure(1, weight=3, uniform="pane")
        split.rowconfigure(0, weight=1)

        # Left: heap list (the one ttk holdout, themed by TreePane).
        left = Card(split, title="Heap Files")
        left.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        left.body.rowconfigure(0, weight=1)
        self._tree_pane = TreePane(left.body, _TREE_COLUMNS, height=16)
        self._tree_pane.grid(row=0, column=0, sticky="nsew")
        self._tree = self._tree_pane.tree
        self._tree.bind("<<TreeviewSelect>>", self._heap_selected)

        # Right: analysis header + console.
        right = Card(split)
        right.grid(row=0, column=1, sticky="nsew")
        header = ctk.CTkFrame(right, fg_color="transparent")
        header.pack(fill=tk.X, padx=14, pady=(12, 4))
        ctk.CTkLabel(header, text="tcMalloc Analysis", font=font("heading"),
                     text_color=theme.ACCENT, anchor="w").pack(side=tk.LEFT)
        ctk.CTkLabel(header, textvariable=self._selection_var, font=font("small"),
                     text_color=theme.MUTED_FG, anchor="e").pack(
            side=tk.RIGHT, fill=tk.X, expand=True)
        self._log = LogPane(right, height=280)
        self._log.pack(fill=tk.BOTH, expand=True, padx=14, pady=(0, 12))
        self._console = self._log.textbox
        self._console.configure(wrap="word")

    def _status_bar(self):
        bar = ctk.CTkFrame(self, fg_color=theme.SUNKEN_BG, corner_radius=0, height=28)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        ctk.CTkLabel(bar, textvariable=self._status_var, font=font("small"),
                     text_color=theme.MUTED_FG, anchor="w").pack(
            side=tk.LEFT, fill=tk.X, expand=True, padx=12, pady=4)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _apply_initial_geometry(self):
        """Standalone only: open maximised on the monitor under the pointer,
        at its real resolution (see common/window.py)."""
        window.open_maximised(self._root_window)

    def on_appearance_change(self, _mode: str):
        """Called by the launcher after a Light/Dark switch; repaint the ttk tree."""
        theme.style_treeview(self)
        self._tree_pane.apply_tags()

    def shutdown(self):
        """Drop any in-flight worker results; the widgets are about to go."""
        self._closed = True

    # ── Actions ───────────────────────────────────────────────────────────────

    def refresh(self):
        self._request_id += 1
        request_id = self._request_id
        ip = self._ip_var.get().strip()
        host = self._host_var.get().strip()
        if not ip or not host:
            self._status_var.set("Enter the EGM IP and host path")
            return
        self._refresh_button.configure(state="disabled")
        self._convert_button.configure(state="disabled")
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
        self._refresh_button.configure(state="normal")
        self._convert_button.configure(state="disabled")
        self._tree.configure(selectmode="browse")
        self._status_var.set(f"{len(records)} tcMalloc heap file(s) found")

    def _failed(self, request_id: int, message: str):
        if self._closed or request_id != self._request_id:
            return
        self._refresh_button.configure(state="normal")
        self._convert_button.configure(state="disabled")
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
            state="normal" if self._selected_record else "disabled"
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
        self._convert_button.configure(state="disabled")
        self._tree.state(["disabled"])
        self._refresh_button.configure(state="disabled")
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
        self._refresh_button.configure(state="normal")
        self._convert_button.configure(
            state="normal" if self._selected_record else "disabled"
        )
        self._write_console(console + f"\n\nDownloaded PDF:\n{pdf}\n", ok=True)
        self._status_var.set(f"Analysis complete: {pdf.name}")

    def _analysis_failed(self, request_id: int, message: str):
        if self._closed or request_id != self._request_id:
            return
        self._tree.state(["!disabled"])
        self._refresh_button.configure(state="normal")
        self._convert_button.configure(
            state="normal" if self._selected_record else "disabled"
        )
        self._status_var.set(message.splitlines()[0] if message else "Analysis failed")
        self._write_console(message, error=True)

    def _write_console(self, text: str, ok: bool = False, error: bool = False):
        self._console.configure(state="normal")
        self._console.delete("1.0", tk.END)
        tag = "error" if error else "ok" if ok else "plain"
        self._console.insert(tk.END, text, tag)
        self._console.configure(state="disabled")

    def _console_contents(self) -> str:
        return self._log.contents()

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
        self._refresh_button.configure(state="normal")
        self._convert_button.configure(state="disabled")
        self._selection_var.set("No heap selected")
        self._status_var.set("Click Load Heap Files to validate this build")

    def _filter_changed(self, *_args):
        self._selected_record = None
        self._convert_button.configure(state="disabled")
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
    app.winfo_toplevel().mainloop()

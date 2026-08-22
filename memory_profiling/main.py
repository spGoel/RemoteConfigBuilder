"""Standalone live and historical charts for Robot meter CSV files."""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import shlex
import sqlite3
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, ttk
from typing import Dict, Iterable, List, Optional, Tuple

try:
    from meter_reader import ALL_METERS, MeterReader
except ImportError:  # Package-style import, useful for tests and reuse.
    from .meter_reader import ALL_METERS, MeterReader


C_BG = "#F3F0FA"
C_SURFACE = "#FFFFFF"
C_TEXT = "#1A1820"
C_MUTED = "#5C5870"
C_ACCENT = "#5B3EA6"
C_GRID = "#DDD9E8"
C_AXIS = "#8A859A"

# A fixed, high-contrast color per meter. The order matches Robot/Types.h.
METER_COLORS = {
    meter: color for meter, color in zip(ALL_METERS, [
        "#E6194B", "#3CB44B", "#4363D8", "#F58231", "#911EB4",
        "#42D4F4", "#F032E6", "#BFEF45", "#FABED4", "#469990",
        "#DCBEFF", "#9A6324", "#800000", "#AAFFC3", "#808000",
        "#FFD8B1", "#000075", "#A9A9A9", "#1F77B4", "#FF7F0E",
        "#2CA02C", "#D62728",
    ])
}

RANGE_OPTIONS = {
    "5 minutes": 5 * 60,
    "30 minutes": 30 * 60,
    "1 hour": 60 * 60,
    "6 hours": 6 * 60 * 60,
    "24 hours": 24 * 60 * 60,
    "7 days": 7 * 24 * 60 * 60,
    "All history": None,
}

DATA_DIR = Path.home() / ".robot_memory_profiler"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_HISTORY_DB = Path(
    os.environ.get(
        "ROBOT_METER_HISTORY_DB",
        str(DATA_DIR / "history.sqlite3"),
    )
)


def _load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        pass
    return {}


def _parse_robot_time(value: str) -> Optional[float]:
    """Parse the timestamp formats emitted by Boost ptime and common CSVs."""
    value = value.strip()
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        pass
    for fmt in (
        "%Y-%b-%d %H:%M:%S.%f",
        "%Y-%b-%d %H:%M:%S",
        "%Y/%m/%d %H:%M:%S",
    ):
        try:
            return datetime.strptime(value, fmt).timestamp()
        except ValueError:
            continue
    return None


def _remote_cache_path(ip: str, remote_path: str) -> Path:
    """Return a stable local snapshot path for one remote meter CSV."""
    identity = f"{ip}\n{remote_path}".encode("utf-8", errors="replace")
    digest = hashlib.sha1(identity).hexdigest()[:16]
    cache_dir = Path(tempfile.gettempdir()) / "robot_meter_profiler"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"meters_{digest}.csv"


def download_remote_meter_csv(
    ip: str,
    remote_path_or_root: str,
    previous_signature: Optional[Tuple[int, int]] = None,
) -> Tuple[Path, Tuple[int, int], str, bool]:
    """Download an EGM meter CSV through SSH.

    ``remote_path_or_root`` may be the exact CSV filename or a build/search
    directory. For a directory, the newest ``*/common/build/robotlogs/*.csv``
    meter file is selected. Returns local path, remote signature, resolved
    remote path, and whether the remote file was unchanged.
    """
    try:
        import paramiko
    except ImportError as exc:
        raise RuntimeError(
            "paramiko is not installed; run: py -3 -m pip install paramiko"
        ) from exc

    ip = ip.strip()
    remote_path_or_root = remote_path_or_root.strip()
    if not ip:
        raise ValueError("Enter the EGM IP address")
    if not remote_path_or_root:
        raise ValueError("Enter the remote meter CSV path or build directory")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username="mk7", password="mk7", timeout=15)
    try:
        if remote_path_or_root.lower().endswith(".csv"):
            remote_path = remote_path_or_root
        else:
            quoted_root = shlex.quote(remote_path_or_root)
            command = (
                f"find {quoted_root} -type f "
                r"\( -path '*/common/build/robotlogs/meters.csv' "
                r"-o -path '*/common/build/robotlogs/meters4sec.csv' \) "
                r"-printf '%T@ %p\n' 2>/dev/null | sort -nr | head -1"
            )
            _, stdout, stderr = client.exec_command(command)
            exit_code = stdout.channel.recv_exit_status()
            result = stdout.read().decode(errors="replace").strip()
            if exit_code != 0 or not result:
                error = stderr.read().decode(errors="replace").strip()
                raise FileNotFoundError(
                    f"No Robot meter CSV found below {remote_path_or_root}"
                    + (f": {error}" if error else "")
                )
            remote_path = result.split(" ", 1)[1]

        sftp = client.open_sftp()
        try:
            attributes = sftp.stat(remote_path)
            signature = (int(attributes.st_mtime), int(attributes.st_size))
            local_path = _remote_cache_path(ip, remote_path)
            if previous_signature == signature and local_path.exists():
                return local_path, signature, remote_path, True

            temporary_path = local_path.with_suffix(".downloading")
            sftp.get(remote_path, str(temporary_path))
            os.replace(temporary_path, local_path)
            return local_path, signature, remote_path, False
        finally:
            sftp.close()
    finally:
        client.close()


class MeterHistoryStore:
    """Persistent SQLite history for one or more Robot meter CSV sources."""

    def __init__(self, db_path: Path = DEFAULT_HISTORY_DB):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(str(self.db_path))
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS meter_samples (
                source TEXT NOT NULL,
                sample_key TEXT NOT NULL,
                sampled_at REAL NOT NULL,
                meter TEXT NOT NULL,
                value REAL NOT NULL,
                PRIMARY KEY (source, sample_key, meter)
            )
            """
        )
        self._connection.execute(
            """
            CREATE INDEX IF NOT EXISTS meter_samples_time
            ON meter_samples (source, sampled_at, meter)
            """
        )
        self._connection.commit()

    def import_csv(self, csv_path: Path) -> int:
        """Persist every numeric meter value in the CSV, ignoring duplicates."""
        csv_path = Path(csv_path)
        source = str(csv_path.resolve())
        fallback_time = csv_path.stat().st_mtime
        inserts = []

        with csv_path.open("r", encoding="utf-8-sig", newline="") as stream:
            reader = csv.DictReader(stream)
            if not reader.fieldnames:
                return 0
            meter_names = [
                name.strip() for name in reader.fieldnames
                if name and name.strip() not in {"Time", "Info"}
            ]
            for row_number, raw_row in enumerate(reader):
                row = {
                    key.strip(): (value.strip() if value is not None else "")
                    for key, value in raw_row.items()
                    if key is not None
                }
                sampled_at = _parse_robot_time(row.get("Time", ""))
                if sampled_at is None:
                    sampled_at = fallback_time + row_number * 0.000001

                fingerprint = "\x1f".join(
                    [row.get("Time", ""), row.get("Info", "")]
                    + [row.get(name, "") for name in meter_names]
                )
                sample_key = hashlib.sha1(
                    fingerprint.encode("utf-8", errors="replace")
                ).hexdigest()

                for meter in meter_names:
                    raw_value = row.get(meter, "")
                    if not raw_value:
                        continue
                    try:
                        value = float(raw_value)
                    except ValueError:
                        continue
                    if math.isfinite(value):
                        inserts.append((source, sample_key, sampled_at, meter, value))

        before = self._connection.total_changes
        self._connection.executemany(
            """
            INSERT OR IGNORE INTO meter_samples
                (source, sample_key, sampled_at, meter, value)
            VALUES (?, ?, ?, ?, ?)
            """,
            inserts,
        )
        self._connection.commit()
        return self._connection.total_changes - before

    def load_series(
        self,
        source_path: Path,
        start_time: float,
        end_time: float,
        meters: Iterable[str],
    ) -> Dict[str, List[Tuple[float, float]]]:
        selected = list(meters)
        series = {meter: [] for meter in selected}
        if not selected:
            return series
        placeholders = ",".join("?" for _ in selected)
        params = [str(Path(source_path).resolve()), start_time, end_time] + selected
        rows = self._connection.execute(
            f"""
            SELECT meter, sampled_at, value
            FROM meter_samples
            WHERE source = ? AND sampled_at BETWEEN ? AND ?
              AND meter IN ({placeholders})
            ORDER BY sampled_at
            """,
            params,
        )
        for meter, sampled_at, value in rows:
            series[meter].append((sampled_at, value))
        return series

    def time_bounds(self, source_path: Path) -> Tuple[Optional[float], Optional[float]]:
        row = self._connection.execute(
            """
            SELECT MIN(sampled_at), MAX(sampled_at)
            FROM meter_samples WHERE source = ?
            """,
            (str(Path(source_path).resolve()),),
        ).fetchone()
        return (row[0], row[1]) if row else (None, None)

    def close(self):
        self._connection.close()


class MemoryProfilingTab(tk.Frame):
    """Tkinter tab that charts live and persisted Robot meter history."""

    def __init__(self, master=None, standalone: bool = False):
        if master is None:
            master = tk.Tk()
            standalone = True
        super().__init__(master, bg=C_BG)
        self._standalone = standalone
        self._root_window = self.winfo_toplevel()
        if standalone:
            self._root_window.title("Robot Memory Profiler")
            self._root_window.geometry("1320x780")
            self.pack(fill=tk.BOTH, expand=True)

        self._history = MeterHistoryStore()
        self._closed = False
        self._poll_job = None
        self._redraw_job = None
        self._running = False
        self._remote_fetch_active = False
        self._remote_request_id = 0
        self._last_remote_signature: Optional[Tuple[int, int]] = None
        self._active_remote_cache: Optional[Path] = None
        self._resolved_remote_path = ""
        self._last_file_signature = None
        self._live_view = True
        self._view_end: Optional[float] = None
        self._current_values: Dict[str, object] = {}
        self._meter_vars = {
            meter: tk.BooleanVar(self, value=True) for meter in ALL_METERS
        }
        self._value_labels: Dict[str, tk.Label] = {}

        profiler_settings = _load_settings()
        source_mode = profiler_settings.get("source_mode", "Local file")
        if source_mode not in {"Local file", "Remote EGM"}:
            source_mode = "Local file"
        self._source_mode_var = tk.StringVar(value=source_mode)
        self._csv_path_var = tk.StringVar(
            value=profiler_settings.get("local_csv", "robotlogs/meters.csv")
        )
        self._ip_var = tk.StringVar(value=profiler_settings.get("ip", ""))
        self._remote_path_var = tk.StringVar(
            value=profiler_settings.get("remote_path", "") or "/home/mk7/development"
        )
        self._interval_var = tk.StringVar(value=profiler_settings.get("interval", "5"))
        self._range_var = tk.StringVar(value="1 hour")
        self._status_var = tk.StringVar(value="Waiting to start")
        self._timeline_var = tk.DoubleVar(value=0.0)
        for variable in (
            self._source_mode_var, self._csv_path_var, self._ip_var,
            self._remote_path_var, self._interval_var,
        ):
            variable.trace_add("write", self._save_settings)
        self._build_ui()
        self.bind("<Destroy>", self._on_destroy, add="+")
        self.after(250, self.start)

    def _build_ui(self):
        controls = tk.Frame(self, bg=C_SURFACE, padx=12, pady=9)
        controls.pack(fill=tk.X, padx=10, pady=(10, 6))

        tk.Label(controls, text="Source:", bg=C_SURFACE, fg=C_TEXT).grid(
            row=0, column=0, sticky="w"
        )
        source_box = ttk.Combobox(
            controls, textvariable=self._source_mode_var,
            values=["Local file", "Remote EGM"], state="readonly", width=12,
        )
        source_box.grid(row=0, column=1, sticky="w", padx=(5, 12))
        source_box.bind("<<ComboboxSelected>>", self._source_changed)

        self._local_fields = tk.Frame(controls, bg=C_SURFACE)
        self._local_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
        tk.Label(
            self._local_fields, text="Meter CSV:", bg=C_SURFACE, fg=C_TEXT,
        ).pack(side=tk.LEFT)
        ttk.Entry(
            self._local_fields, textvariable=self._csv_path_var, width=62,
        ).pack(side=tk.LEFT, padx=(5, 4), fill=tk.X, expand=True)
        ttk.Button(
            self._local_fields, text="Browse…", command=self._browse_csv,
        ).pack(side=tk.LEFT)

        self._remote_fields = tk.Frame(controls, bg=C_SURFACE)
        tk.Label(self._remote_fields, text="IP:", bg=C_SURFACE, fg=C_TEXT).pack(side=tk.LEFT)
        ttk.Entry(self._remote_fields, textvariable=self._ip_var, width=17).pack(
            side=tk.LEFT, padx=(4, 10)
        )
        tk.Label(
            self._remote_fields, text="CSV path or build root:",
            bg=C_SURFACE, fg=C_TEXT,
        ).pack(side=tk.LEFT)
        ttk.Entry(
            self._remote_fields, textvariable=self._remote_path_var, width=55,
        ).pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)

        tk.Label(controls, text="Poll (sec):", bg=C_SURFACE, fg=C_TEXT).grid(
            row=1, column=0, sticky="w", pady=(8, 0)
        )
        ttk.Spinbox(
            controls, from_=0.5, to=60, increment=0.5,
            textvariable=self._interval_var, width=6,
        ).grid(row=1, column=1, sticky="w", padx=(5, 12), pady=(8, 0))
        self._start_button = ttk.Button(controls, text="Start", command=self._toggle_running)
        self._start_button.grid(row=1, column=2, sticky="w", pady=(8, 0))
        ttk.Button(controls, text="Refresh", command=self._force_refresh).grid(
            row=1, column=3, sticky="w", padx=(4, 0), pady=(8, 0)
        )
        tk.Label(
            controls,
            text="Remote login uses the configured EGM account (mk7/mk7)",
            bg=C_SURFACE, fg=C_MUTED,
        ).grid(row=1, column=4, columnspan=3, sticky="w", padx=(12, 0), pady=(8, 0))
        controls.grid_columnconfigure(4, weight=1)
        self._source_changed()

        nav = tk.Frame(self, bg=C_SURFACE, padx=12, pady=7)
        nav.pack(fill=tk.X, padx=10, pady=(0, 6))
        tk.Label(nav, text="Visible range:", bg=C_SURFACE, fg=C_TEXT).pack(side=tk.LEFT)
        range_box = ttk.Combobox(
            nav, textvariable=self._range_var, values=list(RANGE_OPTIONS),
            width=14, state="readonly",
        )
        range_box.pack(side=tk.LEFT, padx=(5, 12))
        range_box.bind("<<ComboboxSelected>>", lambda _event: self._draw_chart())
        ttk.Button(nav, text="◀ Earlier", command=lambda: self._pan(-1)).pack(side=tk.LEFT)
        ttk.Button(nav, text="Later ▶", command=lambda: self._pan(1)).pack(side=tk.LEFT, padx=4)
        ttk.Button(nav, text="Zoom +", command=lambda: self._zoom(0.5)).pack(side=tk.LEFT, padx=(8, 2))
        ttk.Button(nav, text="Zoom −", command=lambda: self._zoom(2.0)).pack(side=tk.LEFT)
        ttk.Button(nav, text="Live", command=self._go_live).pack(side=tk.LEFT, padx=(8, 0))
        tk.Label(
            nav, textvariable=self._status_var, bg=C_SURFACE, fg=C_MUTED,
            anchor="e",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(12, 0))

        body = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, bg=C_BG, sashwidth=5,
            sashrelief=tk.FLAT, bd=0,
        )
        body.pack(fill=tk.BOTH, expand=True, padx=10)

        chart_panel = tk.Frame(body, bg=C_SURFACE)
        body.add(chart_panel, stretch="always", minsize=500)
        self._chart = tk.Canvas(
            chart_panel, bg=C_SURFACE, highlightthickness=1,
            highlightbackground=C_GRID,
        )
        self._chart.pack(fill=tk.BOTH, expand=True)
        self._chart.bind("<Configure>", self._schedule_redraw)

        legend_panel = tk.Frame(body, bg=C_SURFACE, width=330)
        body.add(legend_panel, minsize=280)
        legend_header = tk.Frame(legend_panel, bg=C_SURFACE, padx=8, pady=8)
        legend_header.pack(fill=tk.X)
        tk.Label(
            legend_header, text="Meter", font=("Segoe UI", 10, "bold"),
            bg=C_SURFACE, fg=C_TEXT,
        ).pack(side=tk.LEFT)
        ttk.Button(legend_header, text="All", width=5, command=self._select_all).pack(side=tk.RIGHT)
        ttk.Button(legend_header, text="None", width=5, command=self._select_none).pack(
            side=tk.RIGHT, padx=3
        )
        self._build_scrollable_legend(legend_panel)

        timeline = tk.Frame(self, bg=C_BG)
        timeline.pack(fill=tk.X, padx=10, pady=(5, 8))
        tk.Label(timeline, text="History", bg=C_BG, fg=C_MUTED).pack(side=tk.LEFT)
        self._timeline = ttk.Scale(
            timeline, variable=self._timeline_var, from_=0, to=1,
            command=self._timeline_changed,
        )
        self._timeline.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self._time_label = tk.Label(timeline, text="No history", bg=C_BG, fg=C_MUTED, width=34)
        self._time_label.pack(side=tk.RIGHT)

    def _build_scrollable_legend(self, parent):
        holder = tk.Frame(parent, bg=C_SURFACE)
        holder.pack(fill=tk.BOTH, expand=True)
        canvas = tk.Canvas(holder, bg=C_SURFACE, highlightthickness=0, width=320)
        scrollbar = ttk.Scrollbar(holder, orient=tk.VERTICAL, command=canvas.yview)
        inner = tk.Frame(canvas, bg=C_SURFACE)
        window = canvas.create_window((0, 0), window=inner, anchor=tk.NW)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        inner.bind(
            "<Configure>",
            lambda _event: canvas.configure(scrollregion=canvas.bbox("all")),
        )
        canvas.bind("<Configure>", lambda event: canvas.itemconfigure(window, width=event.width))

        for meter in ALL_METERS:
            row = tk.Frame(inner, bg=C_SURFACE, padx=5, pady=2)
            row.pack(fill=tk.X)
            ttk.Checkbutton(
                row, variable=self._meter_vars[meter], command=self._draw_chart,
            ).pack(side=tk.LEFT)
            tk.Label(
                row, text="━", font=("Segoe UI", 14, "bold"),
                bg=C_SURFACE, fg=METER_COLORS[meter], width=2,
            ).pack(side=tk.LEFT)
            tk.Label(
                row, text=meter, bg=C_SURFACE, fg=C_TEXT,
                anchor="w", width=22,
            ).pack(side=tk.LEFT)
            value_label = tk.Label(
                row, text="—", bg=C_SURFACE, fg=C_MUTED,
                anchor="e", font=("Consolas", 9),
            )
            value_label.pack(side=tk.RIGHT, fill=tk.X, expand=True)
            self._value_labels[meter] = value_label

    def _browse_csv(self):
        path = filedialog.askopenfilename(
            parent=self,
            title="Select Robot meter CSV",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
        )
        if path:
            self._csv_path_var.set(path)
            self._last_file_signature = None
            self._go_live()
            self._poll_once()

    def _save_settings(self, *_args):
        try:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            SETTINGS_FILE.write_text(
                json.dumps({
                    "source_mode": self._source_mode_var.get(),
                    "local_csv": self._csv_path_var.get(),
                    "ip": self._ip_var.get(),
                    "remote_path": self._remote_path_var.get(),
                    "interval": self._interval_var.get(),
                }, indent=2),
                encoding="utf-8",
            )
        except (OSError, tk.TclError):
            pass

    def _source_changed(self, _event=None):
        remote = self._source_mode_var.get() == "Remote EGM"
        if remote:
            self._local_fields.grid_remove()
            self._remote_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
        else:
            self._remote_fields.grid_remove()
            self._local_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
        self._last_file_signature = None
        self._last_remote_signature = None
        self._remote_request_id += 1
        self._remote_fetch_active = False
        self._active_remote_cache = None
        self._resolved_remote_path = ""
        if hasattr(self, "_chart"):
            self._go_live()
            if self._running:
                self._poll_once()

    def _active_source_path(self) -> Optional[Path]:
        if self._source_mode_var.get() == "Remote EGM":
            return self._active_remote_cache
        value = self._csv_path_var.get().strip()
        return Path(value) if value else None

    def _toggle_running(self):
        self.stop() if self._running else self.start()

    def start(self):
        if self._running:
            return
        self._running = True
        self._start_button.configure(text="Stop")
        self._poll_once()

    def stop(self):
        self._running = False
        self._start_button.configure(text="Start")
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self._status_var.set("Monitoring paused")

    def _poll_once(self):
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        if self._source_mode_var.get() == "Remote EGM":
            self._poll_remote()
            return

        value = self._csv_path_var.get().strip()
        if not value:
            self._status_var.set("Select a local meter CSV")
            self._schedule_next_poll()
            return
        self._process_csv(Path(value))
        self._schedule_next_poll()

    def _process_csv(self, csv_path: Path, status_prefix: str = "Live"):
        try:
            signature = (csv_path.stat().st_mtime_ns, csv_path.stat().st_size)
            if signature != self._last_file_signature:
                imported = self._history.import_csv(csv_path)
                self._current_values = MeterReader(csv_path).get_all_meters()
                self._last_file_signature = signature
                self._update_value_labels()
                self._status_var.set(
                    f"{status_prefix} • {datetime.now():%H:%M:%S} • "
                    f"{imported} new values saved"
                )
                self._update_timeline()
                if self._live_view:
                    self._draw_chart()
            elif self._running:
                self._status_var.set(
                    f"{status_prefix} • waiting for CSV update • {datetime.now():%H:%M:%S}"
                )
        except FileNotFoundError:
            self._status_var.set(f"Waiting for CSV: {csv_path}")
        except (OSError, ValueError, sqlite3.Error) as exc:
            self._status_var.set(f"Could not read meters: {exc}")

    def _poll_remote(self):
        if self._remote_fetch_active:
            return
        ip = self._ip_var.get().strip()
        remote_path = self._remote_path_var.get().strip()
        if not ip or not remote_path:
            self._status_var.set("Enter the EGM IP and remote CSV path/build root")
            self._schedule_next_poll()
            return

        self._remote_fetch_active = True
        self._remote_request_id += 1
        request_id = self._remote_request_id
        previous_signature = self._last_remote_signature
        self._status_var.set(f"Connecting to EGM {ip}…")

        def worker():
            try:
                result = download_remote_meter_csv(
                    ip, remote_path, previous_signature,
                )
                self.after(
                    0,
                    lambda: self._remote_downloaded(
                        request_id, ip, remote_path, result,
                    ),
                )
            except Exception as exc:
                message = str(exc)
                try:
                    self.after(
                        0,
                        lambda: self._remote_failed(
                            request_id, ip, remote_path, message,
                        ),
                    )
                except (RuntimeError, tk.TclError):
                    pass

        threading.Thread(target=worker, daemon=True).start()

    def _remote_downloaded(
        self,
        request_id: int,
        ip: str,
        requested_path: str,
        result: Tuple[Path, Tuple[int, int], str, bool],
    ):
        if self._closed:
            return
        if request_id != self._remote_request_id:
            return
        if (
            self._source_mode_var.get() != "Remote EGM"
            or ip != self._ip_var.get().strip()
            or requested_path != self._remote_path_var.get().strip()
        ):
            self._remote_fetch_active = False
            return
        local_path, signature, resolved_path, unchanged = result
        self._remote_fetch_active = False
        source_changed = local_path != self._active_remote_cache
        self._active_remote_cache = local_path
        self._resolved_remote_path = resolved_path
        self._last_remote_signature = signature
        if source_changed:
            self._last_file_signature = None

        if unchanged:
            self._status_var.set(
                f"EGM {ip} • waiting for {Path(resolved_path).name} update • "
                f"{datetime.now():%H:%M:%S}"
            )
        else:
            self._process_csv(local_path, f"EGM {ip}")
        self._update_timeline()
        self._schedule_next_poll()

    def _remote_failed(
        self,
        request_id: int,
        ip: str,
        requested_path: str,
        message: str,
    ):
        if self._closed:
            return
        if request_id != self._remote_request_id:
            return
        if (
            self._source_mode_var.get() != "Remote EGM"
            or ip != self._ip_var.get().strip()
            or requested_path != self._remote_path_var.get().strip()
        ):
            self._remote_fetch_active = False
            return
        self._remote_fetch_active = False
        self._status_var.set(f"EGM {ip}: {message}")
        self._schedule_next_poll()

    def _schedule_next_poll(self):
        if self._running:
            try:
                delay_ms = max(500, int(float(self._interval_var.get()) * 1000))
            except ValueError:
                delay_ms = 2000
            self._poll_job = self.after(delay_ms, self._poll_once)

    def _force_refresh(self):
        self._last_file_signature = None
        self._last_remote_signature = None
        self._poll_once()
        self._draw_chart()

    def _update_value_labels(self):
        for meter, label in self._value_labels.items():
            value = self._current_values.get(meter)
            label.configure(text=self._format_value(value) if value is not None else "—")

    def _selected_meters(self) -> List[str]:
        return [meter for meter in ALL_METERS if self._meter_vars[meter].get()]

    def _select_all(self):
        for variable in self._meter_vars.values():
            variable.set(True)
        self._draw_chart()

    def _select_none(self):
        for variable in self._meter_vars.values():
            variable.set(False)
        self._draw_chart()

    def _current_span(self) -> Optional[float]:
        return RANGE_OPTIONS.get(self._range_var.get(), 3600)

    def _pan(self, direction: int):
        span = self._current_span()
        if span is None:
            return
        now = time.time()
        end = self._view_end if self._view_end is not None else now
        end += direction * span * 0.8
        if end >= now:
            self._go_live()
            return
        self._live_view = False
        self._view_end = end
        self._draw_chart()
        self._update_timeline()

    def _zoom(self, factor: float):
        span = self._current_span()
        if span is None:
            span = 7 * 24 * 60 * 60
        target = min(RANGE_OPTIONS.items(), key=lambda item: (
            float("inf") if item[1] is None else abs(item[1] - span * factor)
        ))[0]
        self._range_var.set(target)
        self._draw_chart()

    def _go_live(self):
        self._live_view = True
        self._view_end = None
        self._draw_chart()
        self._update_timeline()

    def _update_timeline(self):
        path = self._active_source_path()
        if path is None:
            self._timeline.configure(from_=0, to=1)
            self._timeline_var.set(0)
            self._time_label.configure(text="No history")
            return
        earliest, latest = self._history.time_bounds(path)
        if earliest is None or latest is None:
            self._timeline.configure(from_=0, to=1)
            self._timeline_var.set(0)
            self._time_label.configure(text="No history")
            return
        upper = max(latest, time.time())
        self._timeline.configure(from_=earliest, to=upper)
        current = upper if self._live_view else min(self._view_end or upper, upper)
        self._timeline_var.set(current)
        self._time_label.configure(
            text=f"{datetime.fromtimestamp(earliest):%d %b %H:%M}  →  "
                 f"{datetime.fromtimestamp(current):%d %b %H:%M}"
        )

    def _timeline_changed(self, raw_value):
        try:
            selected_time = float(raw_value)
        except ValueError:
            return
        if selected_time <= 1:
            return
        if abs(selected_time - time.time()) < 2:
            self._live_view = True
            self._view_end = None
        else:
            self._live_view = False
            self._view_end = selected_time
        self._schedule_redraw()

    def _schedule_redraw(self, _event=None):
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(80, self._draw_chart)

    def _draw_chart(self):
        self._redraw_job = None
        canvas = self._chart
        canvas.delete("all")
        width = max(canvas.winfo_width(), 300)
        height = max(canvas.winfo_height(), 220)
        left, top, right, bottom = 76, 24, width - 22, height - 55
        if right <= left or bottom <= top:
            return

        path = self._active_source_path()
        if path is None:
            canvas.create_text(
                width / 2, height / 2,
                text="Connect to an EGM or select a local meter CSV",
                fill=C_MUTED, font=("Segoe UI", 12),
            )
            return
        earliest, latest = self._history.time_bounds(path)
        if earliest is None or latest is None:
            canvas.create_text(
                width / 2, height / 2, text="No meter history yet",
                fill=C_MUTED, font=("Segoe UI", 12),
            )
            return

        end_time = time.time() if self._live_view else (self._view_end or latest)
        span = self._current_span()
        start_time = earliest if span is None else end_time - span
        if end_time <= start_time:
            end_time = start_time + 1
        selected = self._selected_meters()
        series = self._history.load_series(path, start_time, end_time, selected)
        points = [point for meter_points in series.values() for point in meter_points]

        max_value = max((value for _, value in points), default=0.0)
        max_log = max(1.0, math.ceil(math.log10(max(0.0, max_value) + 1)))

        # Logarithmic-style Y scale (log10(value + 1)) keeps memory and game
        # counters visible together, including legitimate zero values.
        for tick in range(int(max_log) + 1):
            y = bottom - (tick / max_log) * (bottom - top)
            canvas.create_line(left, y, right, y, fill=C_GRID)
            label_value = (10 ** tick) - 1
            canvas.create_text(
                left - 9, y, text=self._format_axis(label_value),
                anchor=tk.E, fill=C_MUTED, font=("Segoe UI", 8),
            )

        for index in range(7):
            ratio = index / 6
            x = left + ratio * (right - left)
            timestamp = start_time + ratio * (end_time - start_time)
            canvas.create_line(x, top, x, bottom, fill=C_GRID)
            canvas.create_text(
                x, bottom + 16, text=self._format_time_tick(timestamp, end_time - start_time),
                fill=C_MUTED, font=("Segoe UI", 8),
            )

        canvas.create_line(left, top, left, bottom, fill=C_AXIS)
        canvas.create_line(left, bottom, right, bottom, fill=C_AXIS)
        canvas.create_text(
            15, (top + bottom) / 2, text="log₁₀(value + 1)", angle=90,
            fill=C_MUTED, font=("Segoe UI", 8),
        )
        canvas.create_text(
            (left + right) / 2, height - 14,
            text="Time" + (" • LIVE" if self._live_view else " • HISTORY"),
            fill=C_ACCENT if self._live_view else C_MUTED,
            font=("Segoe UI", 9, "bold"),
        )

        for meter in selected:
            coords = []
            for sampled_at, value in series.get(meter, []):
                x = left + ((sampled_at - start_time) / (end_time - start_time)) * (right - left)
                transformed = math.log10(max(0.0, value) + 1)
                y = bottom - (transformed / max_log) * (bottom - top)
                coords.extend((x, y))
            if len(coords) >= 4:
                canvas.create_line(
                    *coords, fill=METER_COLORS[meter], width=2,
                    smooth=False,
                )
            elif len(coords) == 2:
                x, y = coords
                canvas.create_oval(
                    x - 2, y - 2, x + 2, y + 2,
                    fill=METER_COLORS[meter], outline="",
                )

        if not selected:
            canvas.create_text(
                (left + right) / 2, (top + bottom) / 2,
                text="Select one or more meters from the list",
                fill=C_MUTED, font=("Segoe UI", 11),
            )
        elif not points:
            canvas.create_text(
                (left + right) / 2, (top + bottom) / 2,
                text="No samples in this time range",
                fill=C_MUTED, font=("Segoe UI", 11),
            )

    @staticmethod
    def _format_axis(value: float) -> str:
        if value >= 1_000_000_000:
            return f"{value / 1_000_000_000:.0f}B"
        if value >= 1_000_000:
            return f"{value / 1_000_000:.0f}M"
        if value >= 1_000:
            return f"{value / 1_000:.0f}K"
        return f"{value:.0f}"

    @staticmethod
    def _format_value(value) -> str:
        if isinstance(value, float) and not value.is_integer():
            return f"{value:,.3f}"
        if isinstance(value, (int, float)):
            return f"{int(value):,}"
        return str(value)

    @staticmethod
    def _format_time_tick(timestamp: float, span: float) -> str:
        fmt = "%H:%M:%S" if span <= 6 * 60 * 60 else "%d %b\n%H:%M"
        return datetime.fromtimestamp(timestamp).strftime(fmt)

    def _on_destroy(self, event):
        if event.widget is self:
            self.shutdown()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except tk.TclError:
                pass
            self._poll_job = None
        if self._redraw_job is not None:
            try:
                self.after_cancel(self._redraw_job)
            except tk.TclError:
                pass
            self._redraw_job = None
        if self._history is not None:
            self._history.close()
            self._history = None


if __name__ == "__main__":
    app = MemoryProfilingTab(standalone=True)
    app.mainloop()

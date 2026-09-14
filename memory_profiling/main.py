"""Standalone live and historical charts for Robot meter streams.

UI is CustomTkinter; the live TCP meter server, the SQLite history store,
CSV reading and every polling/timer path are unchanged from the plain-tkinter
version. The two charts stay on tk.Canvas (CustomTkinter has none) and are
repainted from theme colour pairs so Light/Dark both read correctly.
"""

from __future__ import annotations

import csv
import hashlib
import json
import math
import os
import queue
import shlex
import socket
import sqlite3
import sys
import tempfile
import threading
import time
import tkinter as tk
from datetime import datetime
from pathlib import Path
from tkinter import filedialog
from typing import Dict, Iterable, List, Optional, Tuple

# The shared style layer lives at the repository root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customtkinter as ctk  # noqa: E402

from common import theme, widgets  # noqa: E402
from common.widgets import Banner, font  # noqa: E402

try:
    from meter_reader import ALL_METERS, read_all_meters
    from ctk_spinbox import NumberSpin
except ImportError:  # Package-style import, useful for tests and reuse.
    from .meter_reader import ALL_METERS, read_all_meters
    from .ctk_spinbox import NumberSpin


# ── Chart palette — (light, dark) pairs resolved with theme.pick() ────────────
CHART_BG = ("#FFFFFF", "#1B1B26")
CHART_GRID = ("#DDD9E8", "#33334A")
CHART_AXIS = ("#8A859A", "#8E8AA6")
CHART_TEXT = ("#5C5870", "#B4B0C8")
CHART_LIVE = theme.ACCENT
PANE_BG = theme.BORDER  # the sash between chart and legend

# A fixed, high-contrast colour per meter, as (light, dark) so the pale ones
# stay legible on white and the dark ones on the dark ground. The order
# matches Robot/Types.h.
METER_COLORS = {
    meter: color for meter, color in zip(ALL_METERS, [
        ("#E6194B", "#FF5C7A"), ("#3CB44B", "#5DD66C"), ("#4363D8", "#7A93FF"),
        ("#F58231", "#FFA25C"), ("#911EB4", "#C77DFF"), ("#1FA9C9", "#5EE6FF"),
        ("#F032E6", "#FF7BF2"), ("#7FA800", "#BFEF45"), ("#D8548A", "#FABED4"),
        ("#469990", "#5FC8BC"), ("#8F5BD6", "#DCBEFF"), ("#9A6324", "#D9A066"),
        ("#800000", "#E86A5A"), ("#2E9E5B", "#AAFFC3"), ("#808000", "#C8C83C"),
        ("#C97A2B", "#FFD8B1"), ("#000075", "#8A8AFF"), ("#7A7A7A", "#BFBFBF"),
        ("#1F77B4", "#5AAEE8"), ("#FF7F0E", "#FFA347"), ("#2CA02C", "#63D463"),
        ("#D62728", "#F26E6E"),
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

SOURCE_MODES = ["Local file", "Remote EGM", "Live TCP"]

DATA_DIR = Path.home() / ".robot_memory_profiler"
SETTINGS_FILE = DATA_DIR / "settings.json"
DEFAULT_HISTORY_DB = Path(
    os.environ.get(
        "ROBOT_METER_HISTORY_DB",
        str(DATA_DIR / "history.sqlite3"),
    )
)

_NEUTRAL = ("gray70", "gray35")
_NEUTRAL_HOVER = ("gray60", "gray45")


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


class LiveMeterServer:
    """Receive Robot's MetersCSV stream without writing an intermediate file."""

    def __init__(self, host: str, port: int, events: queue.Queue):
        self.host = host
        self.port = port
        self.events = events
        self._stopping = threading.Event()
        self._listener = None
        self._client = None
        self._thread = None

    def start(self):
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self):
        self._stopping.set()
        for stream in (self._client, self._listener):
            if stream is not None:
                try:
                    stream.close()
                except OSError:
                    pass

    def _run(self):
        try:
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            self._listener = listener
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            listener.bind((self.host, self.port))
            listener.listen(1)
            listener.settimeout(0.5)
            self.port = listener.getsockname()[1]
            self.events.put(("status", f"Listening on {self.host}:{self.port}"))

            while not self._stopping.is_set():
                try:
                    client, address = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    break
                self._client = client
                self.events.put(("status", f"Robot connected from {address[0]}"))
                try:
                    self._receive(client)
                finally:
                    try:
                        client.close()
                    except OSError:
                        pass
                    self._client = None
                if not self._stopping.is_set():
                    self.events.put(("status", "Robot disconnected; listening again"))
        except OSError as exc:
            if not self._stopping.is_set():
                self.events.put(("error", str(exc)))
        finally:
            if self._listener is not None:
                try:
                    self._listener.close()
                except OSError:
                    pass
                self._listener = None

    def _receive(self, client: socket.socket):
        client.settimeout(0.5)
        buffer = ""
        header = None
        while not self._stopping.is_set():
            try:
                data = client.recv(65536)
            except socket.timeout:
                continue
            except OSError:
                return
            if not data:
                return
            buffer += data.decode("utf-8-sig", errors="replace")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.rstrip("\r")
                if not line:
                    continue
                try:
                    fields = next(csv.reader([line]))
                except csv.Error as exc:
                    self.events.put(("error", f"Invalid meter row: {exc}"))
                    continue
                if header is None:
                    header = [field.strip() for field in fields]
                    if "Time" not in header or not any(
                        meter in header for meter in ALL_METERS
                    ):
                        self.events.put(("error", "Robot stream has an invalid meter header"))
                        return
                elif len(fields) != len(header):
                    self.events.put(("error", "Robot stream row does not match its header"))
                else:
                    self.events.put(("row", dict(zip(header, fields))))


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

    def insert_sample(self, source_path: Path, raw_row: Dict[str, str]) -> int:
        """Persist one row received from Robot's live TCP output."""
        row = {
            key.strip(): (value.strip() if value is not None else "")
            for key, value in raw_row.items()
            if key is not None
        }
        sampled_at = _parse_robot_time(row.get("Time", "")) or time.time()
        meter_names = [meter for meter in ALL_METERS if row.get(meter, "")]
        fingerprint = "\x1f".join(
            [row.get("Time", ""), row.get("Info", "")]
            + [row.get(meter, "") for meter in meter_names]
        )
        sample_key = hashlib.sha1(
            fingerprint.encode("utf-8", errors="replace")
        ).hexdigest()
        inserts = []
        for meter in meter_names:
            try:
                value = float(row[meter])
            except ValueError:
                continue
            if math.isfinite(value):
                inserts.append((
                    str(Path(source_path).resolve()), sample_key,
                    sampled_at, meter, value,
                ))

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


class MemoryProfilingTab(ctk.CTkFrame):
    """CustomTkinter tab that charts live and persisted Robot meter history."""

    def __init__(self, master=None, standalone: bool = False, history_db=None):
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
        if standalone:
            self._root_window.title("Robot Memory Profiler")

        self._history = MeterHistoryStore(
            Path(history_db) if history_db is not None else DEFAULT_HISTORY_DB
        )
        self._closed = False
        self._start_job = None
        self._poll_job = None
        self._redraw_job = None
        self._running = False
        self._remote_fetch_active = False
        self._remote_request_id = 0
        self._last_remote_signature: Optional[Tuple[int, int]] = None
        self._active_remote_cache: Optional[Path] = None
        self._resolved_remote_path = ""
        self._tcp_server: Optional[LiveMeterServer] = None
        self._tcp_events = None
        self._tcp_job = None
        self._active_tcp_source: Optional[Path] = None
        self._last_file_signature = None
        self._live_view = True
        self._view_end: Optional[float] = None
        self._current_values: Dict[str, object] = {}
        self._meter_vars = {
            meter: tk.BooleanVar(self, value=True) for meter in ALL_METERS
        }
        self._value_labels: Dict[str, ctk.CTkLabel] = {}

        profiler_settings = _load_settings()
        source_mode = profiler_settings.get("source_mode", "Local file")
        if source_mode not in set(SOURCE_MODES):
            source_mode = "Local file"
        self._source_mode_var = tk.StringVar(value=source_mode)
        self._csv_path_var = tk.StringVar(
            value=profiler_settings.get("local_csv", "robotlogs/meters.csv")
        )
        self._ip_var = tk.StringVar(value=profiler_settings.get("ip", ""))
        self._remote_path_var = tk.StringVar(
            value=profiler_settings.get("remote_path", "") or "/home/mk7/development"
        )
        self._listen_ip_var = tk.StringVar(
            value=profiler_settings.get("listen_ip", "0.0.0.0")
        )
        self._listen_port_var = tk.StringVar(
            value=profiler_settings.get("listen_port", "2207")
        )
        self._interval_var = tk.StringVar(value=profiler_settings.get("interval", "5"))
        self._range_var = tk.StringVar(value="1 hour")
        self._status_var = tk.StringVar(value="Waiting to start")
        self._timeline_var = tk.DoubleVar(value=0.0)
        for variable in (
            self._source_mode_var, self._csv_path_var, self._ip_var,
            self._remote_path_var, self._listen_ip_var, self._listen_port_var,
            self._interval_var,
        ):
            variable.trace_add("write", self._save_settings)
        self._build_ui()
        self.bind("<Destroy>", self._on_destroy, add="+")
        if standalone:
            self.pack(fill=tk.BOTH, expand=True)
            self._apply_initial_geometry()
        self._start_job = self.after(250, self.start)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _label(self, parent, text: str, muted: bool = False, **kwargs) -> ctk.CTkLabel:
        kwargs.setdefault("anchor", "w")
        kwargs.setdefault("font", font("body"))
        if muted:
            kwargs.setdefault("text_color", theme.MUTED_FG)
        return ctk.CTkLabel(parent, text=text, **kwargs)

    def _button(self, parent, text: str, command, primary: bool = False,
                width: int = 90) -> ctk.CTkButton:
        if primary:
            colours = dict(fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER)
        else:
            colours = dict(
                fg_color=_NEUTRAL, hover_color=_NEUTRAL_HOVER, text_color=theme.BODY_FG
            )
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=30,
            font=font("body"), **colours,
        )

    def _combo(self, parent, variable: tk.StringVar, values: list, width: int,
               command=None) -> ctk.CTkComboBox:
        return ctk.CTkComboBox(
            parent, variable=variable, values=values, state="readonly",
            width=width, height=30, font=font("body"), dropdown_font=font("body"),
            button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
            border_color=theme.BORDER, command=command,
        )

    def _build_ui(self):
        pad = 12 if self._standalone else 8
        self._scale = theme.widget_scaling(self)

        # ── Source / polling controls ────────────────────────────────────────
        controls_card = ctk.CTkFrame(self, fg_color=theme.CARD_BG, corner_radius=10)
        controls_card.pack(fill=tk.X, padx=pad, pady=(pad, 6))
        controls = ctk.CTkFrame(controls_card, fg_color="transparent")
        controls.pack(fill=tk.X, padx=12, pady=9)

        self._label(controls, "Source:").grid(row=0, column=0, sticky="w")
        self._source_box = self._combo(
            controls, self._source_mode_var, SOURCE_MODES, width=130,
            command=lambda _value: self._source_changed(),
        )
        self._source_box.grid(row=0, column=1, sticky="w", padx=(5, 12))

        self._local_fields = ctk.CTkFrame(controls, fg_color="transparent")
        self._local_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
        self._label(self._local_fields, "Meter CSV:").pack(side=tk.LEFT)
        ctk.CTkEntry(
            self._local_fields, textvariable=self._csv_path_var, height=30,
            font=font("body"),
        ).pack(side=tk.LEFT, padx=(5, 4), fill=tk.X, expand=True)
        self._button(self._local_fields, "Browse…", self._browse_csv, width=84).pack(
            side=tk.LEFT
        )

        self._remote_fields = ctk.CTkFrame(controls, fg_color="transparent")
        self._label(self._remote_fields, "IP:").pack(side=tk.LEFT)
        ctk.CTkEntry(
            self._remote_fields, textvariable=self._ip_var, width=150, height=30,
            font=font("body"),
        ).pack(side=tk.LEFT, padx=(4, 10))
        self._label(self._remote_fields, "CSV path or build root:").pack(side=tk.LEFT)
        ctk.CTkEntry(
            self._remote_fields, textvariable=self._remote_path_var, height=30,
            font=font("body"),
        ).pack(side=tk.LEFT, padx=(4, 0), fill=tk.X, expand=True)

        self._tcp_fields = ctk.CTkFrame(controls, fg_color="transparent")
        self._label(self._tcp_fields, "Listen IP:").pack(side=tk.LEFT)
        ctk.CTkEntry(
            self._tcp_fields, textvariable=self._listen_ip_var, width=150, height=30,
            font=font("body"),
        ).pack(side=tk.LEFT, padx=(4, 10))
        self._label(self._tcp_fields, "Port:").pack(side=tk.LEFT)
        ctk.CTkEntry(
            self._tcp_fields, textvariable=self._listen_port_var, width=72, height=30,
            font=font("body"),
        ).pack(side=tk.LEFT, padx=(4, 0))

        self._label(controls, "Poll (sec):").grid(row=1, column=0, sticky="w", pady=(8, 0))
        NumberSpin(
            controls, self._interval_var, from_=0.5, to=60, increment=0.5, width=60,
        ).grid(row=1, column=1, sticky="w", padx=(5, 12), pady=(8, 0))
        self._start_button = self._button(
            controls, "Start", self._toggle_running, primary=True, width=90
        )
        self._start_button.grid(row=1, column=2, sticky="w", pady=(8, 0))
        self._button(controls, "Refresh", self._force_refresh, width=90).grid(
            row=1, column=3, sticky="w", padx=(4, 0), pady=(8, 0)
        )
        self._source_hint = Banner(controls, colour=theme.MUTED_FG, font=font("body"))
        self._source_hint.grid(
            row=1, column=4, columnspan=3, sticky="w", padx=(12, 0), pady=(8, 0)
        )
        controls.grid_columnconfigure(4, weight=1)
        self._source_changed()

        # ── Range navigation ─────────────────────────────────────────────────
        nav_card = ctk.CTkFrame(self, fg_color=theme.CARD_BG, corner_radius=10)
        nav_card.pack(fill=tk.X, padx=pad, pady=(0, 6))
        nav = ctk.CTkFrame(nav_card, fg_color="transparent")
        nav.pack(fill=tk.X, padx=12, pady=7)
        self._label(nav, "Visible range:").pack(side=tk.LEFT)
        self._range_box = self._combo(
            nav, self._range_var, list(RANGE_OPTIONS), width=140,
            command=lambda _value: self._draw_chart(),
        )
        self._range_box.pack(side=tk.LEFT, padx=(5, 12))
        self._button(nav, "◀ Earlier", lambda: self._pan(-1)).pack(side=tk.LEFT)
        self._button(nav, "Later ▶", lambda: self._pan(1)).pack(side=tk.LEFT, padx=4)
        self._button(nav, "Zoom +", lambda: self._zoom(0.5), width=80).pack(
            side=tk.LEFT, padx=(8, 2)
        )
        self._button(nav, "Zoom −", lambda: self._zoom(2.0), width=80).pack(side=tk.LEFT)
        self._button(nav, "Live", self._go_live, primary=True, width=70).pack(
            side=tk.LEFT, padx=(8, 0)
        )
        ctk.CTkLabel(
            nav, textvariable=self._status_var, font=font("small"),
            text_color=theme.MUTED_FG, anchor="e",
        ).pack(side=tk.RIGHT, fill=tk.X, expand=True, padx=(12, 0))

        # ── Chart + legend (tk.PanedWindow: CTk has no split pane) ───────────
        body = tk.PanedWindow(
            self, orient=tk.HORIZONTAL, bg=theme.pick(PANE_BG),
            sashwidth=self._px(5), sashrelief=tk.FLAT, bd=0,
        )
        body.pack(fill=tk.BOTH, expand=True, padx=pad)
        self._body = body

        self._chart_panel = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=0)
        body.add(self._chart_panel, stretch="always", minsize=self._px(400))
        self._chart = tk.Canvas(
            self._chart_panel, bg=theme.pick(CHART_BG), highlightthickness=1,
            highlightbackground=theme.pick(CHART_GRID), bd=0,
        )
        self._chart.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        self._chart.bind("<Configure>", self._schedule_redraw)

        self._legend_panel = ctk.CTkFrame(body, fg_color=theme.CARD_BG, corner_radius=0)
        body.add(self._legend_panel, minsize=self._px(260), width=self._px(330))
        legend_header = ctk.CTkFrame(self._legend_panel, fg_color="transparent")
        legend_header.pack(fill=tk.X, padx=8, pady=8)
        ctk.CTkLabel(legend_header, text="Meter", font=font("heading"), anchor="w").pack(
            side=tk.LEFT
        )
        self._button(legend_header, "All", self._select_all, width=52).pack(side=tk.RIGHT)
        self._button(legend_header, "None", self._select_none, width=58).pack(
            side=tk.RIGHT, padx=3
        )
        self._build_scrollable_legend(self._legend_panel)

        # ── History timeline ─────────────────────────────────────────────────
        timeline = ctk.CTkFrame(self, fg_color="transparent")
        timeline.pack(fill=tk.X, padx=pad, pady=(5, pad))
        self._label(timeline, "History", muted=True).pack(side=tk.LEFT)
        self._timeline = ctk.CTkSlider(
            timeline, variable=self._timeline_var, from_=0, to=1,
            command=self._timeline_changed, height=18,
            button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
            progress_color=theme.ACCENT, fg_color=theme.BORDER,
        )
        self._timeline.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=8)
        self._time_label = ctk.CTkLabel(
            timeline, text="No history", font=font("small"),
            text_color=theme.MUTED_FG, anchor="e", width=260,
        )
        self._time_label.pack(side=tk.RIGHT)

    def _build_scrollable_legend(self, parent):
        inner = ctk.CTkScrollableFrame(parent, fg_color="transparent")
        inner.pack(fill=tk.BOTH, expand=True, padx=(4, 0), pady=(0, 6))
        self._legend_rows = inner

        for meter in ALL_METERS:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill=tk.X, padx=4, pady=1)
            ctk.CTkCheckBox(
                row, text="", width=24, variable=self._meter_vars[meter],
                command=self._draw_chart, checkbox_width=20, checkbox_height=20,
                fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
            ).pack(side=tk.LEFT)
            ctk.CTkLabel(
                row, text="━", font=font("info"), width=24,
                text_color=METER_COLORS[meter],
            ).pack(side=tk.LEFT)
            ctk.CTkLabel(
                row, text=meter, font=font("body"), anchor="w", width=150,
            ).pack(side=tk.LEFT)
            value_label = ctk.CTkLabel(
                row, text="—", font=font("mono_small"), anchor="e",
                text_color=theme.MUTED_FG,
            )
            value_label.pack(side=tk.RIGHT, fill=tk.X, expand=True)
            self._value_labels[meter] = value_label

    def _px(self, size: float) -> int:
        """Scale a raw pixel size tuned for 1x to this display (tk widgets only)."""
        return max(1, int(round(size * self._scale)))

    def _apply_initial_geometry(self):
        """Standalone only: open maximised with a DPI-safe fallback size.

        CustomTkinter multiplies geometry() by its scaling factor, so a fixed
        size can open partly off-screen on a scaled display.
        """
        root = self._root_window
        scaling = theme.widget_scaling(root)
        screen_w = root.winfo_screenwidth() / scaling
        screen_h = root.winfo_screenheight() / scaling
        width = int(min(1320, screen_w * 0.9))
        height = int(min(780, screen_h * 0.9))
        root.geometry("{}x{}+{}+{}".format(
            width, height,
            max(0, int((screen_w - width) / 2)),
            max(0, int((screen_h - height) / 2)),
        ))
        root.minsize(int(min(900, screen_w * 0.6)), int(min(600, screen_h * 0.6)))
        try:
            root.state("zoomed")
        except tk.TclError:
            pass

    def on_appearance_change(self, _mode: str):
        """Called by the launcher after a Light/Dark switch.

        CTk widgets repaint themselves; the tk.Canvas chart and the
        tk.PanedWindow sash keep whatever colour they were given, so they are
        re-resolved here and the chart is redrawn in the new palette.
        """
        self._apply_chart_colours()
        self._draw_chart()

    def _apply_chart_colours(self):
        self._chart.configure(
            bg=theme.pick(CHART_BG), highlightbackground=theme.pick(CHART_GRID)
        )
        self._body.configure(bg=theme.pick(PANE_BG))
        # The pane frames sit on a plain tk parent and cached its light colour.
        for panel in (self._chart_panel, self._legend_panel):
            panel.configure(bg_color=theme.pick(PANE_BG))

    # ── Settings / source handling ────────────────────────────────────────────

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
                    "listen_ip": self._listen_ip_var.get(),
                    "listen_port": self._listen_port_var.get(),
                    "interval": self._interval_var.get(),
                }, indent=2),
                encoding="utf-8",
            )
        except (OSError, tk.TclError):
            pass

    def _source_changed(self, _event=None):
        mode = self._source_mode_var.get()
        self._stop_tcp_server()
        if mode == "Remote EGM":
            self._local_fields.grid_remove()
            self._tcp_fields.grid_remove()
            self._remote_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
            self._source_hint.show(
                "Remote login uses the configured EGM account (mk7/mk7)"
            )
        elif mode == "Live TCP":
            self._local_fields.grid_remove()
            self._remote_fields.grid_remove()
            self._tcp_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
            self._source_hint.show("Configure Robot output to this PC and port")
        else:
            self._remote_fields.grid_remove()
            self._tcp_fields.grid_remove()
            self._local_fields.grid(row=0, column=2, columnspan=5, sticky="ew")
            self._source_hint.show("")
        self._last_file_signature = None
        self._last_remote_signature = None
        self._remote_request_id += 1
        self._remote_fetch_active = False
        self._active_remote_cache = None
        self._resolved_remote_path = ""
        self._active_tcp_source = None
        if hasattr(self, "_chart"):
            self._go_live()
            if self._running:
                self._poll_once()

    def _active_source_path(self) -> Optional[Path]:
        mode = self._source_mode_var.get()
        if mode == "Remote EGM":
            return self._active_remote_cache
        if mode == "Live TCP":
            return self._active_tcp_source
        value = self._csv_path_var.get().strip()
        return Path(value) if value else None

    # ── Polling ───────────────────────────────────────────────────────────────

    def _toggle_running(self):
        self.stop() if self._running else self.start()

    def start(self):
        self._start_job = None
        if self._running or self._closed:
            return
        self._running = True
        self._start_button.configure(text="Stop")
        self._poll_once()

    def stop(self):
        self._running = False
        self._start_button.configure(text="Start")
        self._stop_tcp_server()
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        self._status_var.set("Monitoring paused")

    def _poll_once(self):
        if self._poll_job is not None:
            self.after_cancel(self._poll_job)
            self._poll_job = None
        mode = self._source_mode_var.get()
        if mode == "Remote EGM":
            self._poll_remote()
            return
        if mode == "Live TCP":
            self._start_tcp_server()
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
                self._current_values = read_all_meters(csv_path)
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

    def _start_tcp_server(self):
        if self._tcp_server is not None:
            return
        host = self._listen_ip_var.get().strip() or "0.0.0.0"
        try:
            port = int(self._listen_port_var.get())
            if not 1 <= port <= 65535:
                raise ValueError
        except ValueError:
            self._status_var.set("TCP port must be between 1 and 65535")
            return

        source_name = f"tcp_{host.replace(':', '_')}_{port}.stream"
        self._active_tcp_source = DATA_DIR / source_name
        self._tcp_events = queue.Queue()
        self._tcp_server = LiveMeterServer(host, port, self._tcp_events)
        self._status_var.set(f"Starting TCP listener on {host}:{port}…")
        self._tcp_server.start()
        self._drain_tcp_events()

    def _stop_tcp_server(self):
        if self._tcp_job is not None:
            try:
                self.after_cancel(self._tcp_job)
            except tk.TclError:
                pass
            self._tcp_job = None
        if self._tcp_server is not None:
            self._tcp_server.stop()
            self._tcp_server = None
        self._tcp_events = None

    def _drain_tcp_events(self):
        self._tcp_job = None
        events = self._tcp_events
        if events is None:
            return

        imported = 0
        received_row = False
        while True:
            try:
                kind, payload = events.get_nowait()
            except queue.Empty:
                break
            if kind == "status":
                self._status_var.set(payload)
            elif kind == "error":
                self._status_var.set(f"TCP error: {payload}")
            elif kind == "row" and self._active_tcp_source is not None:
                try:
                    imported += self._history.insert_sample(
                        self._active_tcp_source, payload,
                    )
                except sqlite3.Error as exc:
                    self._status_var.set(f"Could not save TCP meters: {exc}")
                    continue
                for meter in ALL_METERS:
                    raw_value = payload.get(meter, "").strip()
                    if not raw_value:
                        continue
                    try:
                        value = float(raw_value)
                    except ValueError:
                        value = raw_value
                    self._current_values[meter] = value
                received_row = True

        if received_row:
            self._update_value_labels()
            self._status_var.set(
                f"Live TCP • {datetime.now():%H:%M:%S} • "
                f"{imported} new values saved"
            )
            self._update_timeline()
            if self._live_view:
                self._draw_chart()

        if self._running and self._source_mode_var.get() == "Live TCP":
            self._tcp_job = self.after(100, self._drain_tcp_events)

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
        if self._source_mode_var.get() == "Live TCP":
            if self._running:
                self._stop_tcp_server()
                self._start_tcp_server()
            self._draw_chart()
            return
        self._last_file_signature = None
        self._last_remote_signature = None
        self._poll_once()
        self._draw_chart()

    # ── Legend / selection ────────────────────────────────────────────────────

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

    # ── Range navigation ──────────────────────────────────────────────────────

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
        if upper <= earliest:  # CTkSlider divides by (to - from_)
            upper = earliest + 1
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
        except (TypeError, ValueError):
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

    # ── Chart ─────────────────────────────────────────────────────────────────

    def _schedule_redraw(self, _event=None):
        if self._redraw_job is not None:
            self.after_cancel(self._redraw_job)
        self._redraw_job = self.after(80, self._draw_chart)

    def _draw_chart(self):
        self._redraw_job = None
        if self._closed:
            return
        canvas = self._chart
        canvas.delete("all")
        px = self._px
        c_grid = theme.pick(CHART_GRID)
        c_axis = theme.pick(CHART_AXIS)
        c_text = theme.pick(CHART_TEXT)
        c_live = theme.pick(CHART_LIVE)
        font_body = theme.scaled_font(canvas, 12)
        font_note = theme.scaled_font(canvas, 11)
        font_tick = theme.scaled_font(canvas, 8)
        font_title = theme.scaled_font(canvas, 9, "bold")

        width = max(canvas.winfo_width(), px(300))
        height = max(canvas.winfo_height(), px(220))
        left, top, right, bottom = px(76), px(24), width - px(22), height - px(55)
        if right <= left or bottom <= top:
            return

        path = self._active_source_path()
        if path is None:
            source_message = (
                "Start the TCP listener and connect Robot"
                if self._source_mode_var.get() == "Live TCP"
                else "Connect to an EGM or select a local meter CSV"
            )
            canvas.create_text(
                width / 2, height / 2,
                text=source_message,
                fill=c_text, font=font_body,
            )
            return
        earliest, latest = self._history.time_bounds(path)
        if earliest is None or latest is None:
            canvas.create_text(
                width / 2, height / 2, text="No meter history yet",
                fill=c_text, font=font_body,
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
            canvas.create_line(left, y, right, y, fill=c_grid)
            label_value = (10 ** tick) - 1
            canvas.create_text(
                left - px(9), y, text=self._format_axis(label_value),
                anchor=tk.E, fill=c_text, font=font_tick,
            )

        for index in range(7):
            ratio = index / 6
            x = left + ratio * (right - left)
            timestamp = start_time + ratio * (end_time - start_time)
            canvas.create_line(x, top, x, bottom, fill=c_grid)
            canvas.create_text(
                x, bottom + px(16),
                text=self._format_time_tick(timestamp, end_time - start_time),
                fill=c_text, font=font_tick,
            )

        canvas.create_line(left, top, left, bottom, fill=c_axis)
        canvas.create_line(left, bottom, right, bottom, fill=c_axis)
        canvas.create_text(
            px(15), (top + bottom) / 2, text="log₁₀(value + 1)", angle=90,
            fill=c_text, font=font_tick,
        )
        canvas.create_text(
            (left + right) / 2, height - px(14),
            text="Time" + (" • LIVE" if self._live_view else " • HISTORY"),
            fill=c_live if self._live_view else c_text,
            font=font_title,
        )

        line_width = px(2)
        dot = px(2)
        for meter in selected:
            colour = theme.pick(METER_COLORS[meter])
            coords = []
            for sampled_at, value in series.get(meter, []):
                x = left + ((sampled_at - start_time) / (end_time - start_time)) * (right - left)
                transformed = math.log10(max(0.0, value) + 1)
                y = bottom - (transformed / max_log) * (bottom - top)
                coords.extend((x, y))
            if len(coords) >= 4:
                canvas.create_line(
                    *coords, fill=colour, width=line_width,
                    smooth=False,
                )
            elif len(coords) == 2:
                x, y = coords
                canvas.create_oval(
                    x - dot, y - dot, x + dot, y + dot,
                    fill=colour, outline="",
                )

        if not selected:
            canvas.create_text(
                (left + right) / 2, (top + bottom) / 2,
                text="Select one or more meters from the list",
                fill=c_text, font=font_note,
            )
        elif not points:
            canvas.create_text(
                (left + right) / 2, (top + bottom) / 2,
                text="No samples in this time range",
                fill=c_text, font=font_note,
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

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def _on_destroy(self, event):
        if event.widget is self:
            self.shutdown()

    def shutdown(self):
        if self._closed:
            return
        self._closed = True
        self._stop_tcp_server()
        for attribute in ("_start_job", "_poll_job", "_redraw_job"):
            job = getattr(self, attribute)
            if job is not None:
                try:
                    self.after_cancel(job)
                except tk.TclError:
                    pass
                setattr(self, attribute, None)
        if self._history is not None:
            self._history.close()
            self._history = None


if __name__ == "__main__":
    app = MemoryProfilingTab(standalone=True)
    app.winfo_toplevel().mainloop()

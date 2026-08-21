"""
Aristocrat Robot Builder — upload Linux_BuildScript.sh to a remote Linux machine,
run it inside a named GNU Screen session, stream live output back here.
"""
import io
import posixpath
import subprocess
import tempfile
import threading
import time
import tkinter as tk
from tkinter import ttk, scrolledtext, messagebox
from pathlib import Path
from typing import Optional, Tuple

# ── Palette ───────────────────────────────────────────────────────────────────
C_ACCENT   = "#5B3EA6"
C_ACCENT_L = "#7457C4"
C_BG       = "#F3F0FA"
C_SURFACE  = "#FFFFFF"
C_TEXT     = "#1A1820"
C_MUTED    = "#5C5870"
C_WHITE    = "#FFFFFF"

# ── SSH credentials — always hardcoded, never prompted ────────────────────────
SSH_USER = "mk7"
SSH_PASS = "mk7"

# ── Remote constants ──────────────────────────────────────────────────────────
REMOTE_SCRIPT_NAME = "Linux_BuildScript.sh"
REMOTE_LOG_NAME    = "robot_build.log"
SCREEN_NAME        = "robot_build"
PROMPT_LINE        = r"export PS1='\u@\h:\w\$ '"
LOCALE_LINES       = ("export LANG=C", "export LC_ALL=C")
SHELL_BLOCK_MARKER = "# Robot Builder shell defaults"

# ── Build options ─────────────────────────────────────────────────────────────
TARGETS      = ["gli", "nsw", "qcom", "asp"]
BUILD_LEVELS = ["3L", "5L", "AVL"]
COMPONENT_OPTIONS = (
    ("both", "Both"),
    ("platform", "Platform only"),
    ("game", "Game only"),
)
BUILD_FLAG_OPTIONS = (
    ("clean", "clean"),
    ("showmode", "show"),
    ("production", "production"),
    ("robot", "robot"),
    ("asan", "asan"),
    ("tcmalloc", "tcmalloc"),
)

# ── Bundled script (always sits next to this file) ────────────────────────────
_BUNDLED_SCRIPT   = Path(__file__).parent / "Linux_BuildScript.sh"
_DEFAULT_BUILDDIR = "/home/mk7/development/robot_builds"
_TORTOISE_PROC_CANDIDATES = (
    Path(r"C:\Program Files\TortoiseSVN\bin\TortoiseProc.exe"),
    Path(r"C:\Program Files (x86)\TortoiseSVN\bin\TortoiseProc.exe"),
)

# ── Default SVN URLs per build level (sourced from Linux_BuildScript.sh) ──────
_DEFAULT_URLS: dict = {
    "3L": {
        "platform": "https://svn.ali.global/gen7/mk7software/64-bit/Platform/Tags/platform_6.20.0-1.00.4",
        "runtime":  "https://svn.ali.global/nAble/Release/3.01/3.01.020/Runtime",
        "game":     "https://svn.ali.global/nAble/GDK_Sample_Games/3.01/Release/3.01.020/FrankensteinGame/Frankenstein",
    },
    "5L": {
        "platform": "https://svn.ali.global/gen7/mk7software/64-bit/Platform/Tags/platform_6.22.0-A.00.0",
        "runtime":  "https://svn.ali.global/nAble/Development/GDK5L/Runtime",
        "game":     "https://svn.ali.global/nAble/GDK_Sample_Games/GDK5L/Trunk/FrankensteinGame/Frankenstein",
    },
    "AVL": {
        "platform":     "https://svn.ali.global/gen7/mk7software/64-bit/Platform/DevLines/TXL-16485_TimeGraphs",
        "gameplatform": "https://svn.ali.global/gen7/mk7games/gameplatform/tags/2.0.1_HRG.082.004",
        "game":         "https://svn.ali.global/gen7/mk7games/games/aussieboomer/tags/gampro_1.02.67623.001",
    },
}

# ── Human-readable label for each URL key ─────────────────────────────────────
_URL_LABELS: dict = {
    "3L":  [("platform", "Platform"), ("runtime", "Runtime"), ("game", "Game")],
    "5L":  [("platform", "Platform"), ("runtime", "Runtime"), ("game", "Game")],
    "AVL": [("platform", "Platform"), ("gameplatform", "Game Platform"), ("game", "Game")],
}


class RobotBuilderApp(tk.Frame):
    def __init__(self, master=None, standalone: bool = False):
        if master is None:
            master = tk.Tk()
            standalone = True
        super().__init__(master, bg=C_BG)
        self._standalone = standalone
        self._root_window = self.winfo_toplevel()
        if self._standalone:
            self._root_window.title("Aristocrat Robot Builder")
            self._root_window.configure(bg=C_BG)
            self._root_window.minsize(860, 700)
        self._stop_event   = threading.Event()
        self._build_active = False
        self._start_time   = 0.0
        self._elapsed_job  = None

        # Pre-create one StringVar per URL field for every level
        self._url_vars: dict = {
            level: {key: tk.StringVar(value=url) for key, url in urls.items()}
            for level, urls in _DEFAULT_URLS.items()
        }

        self._build_ui()
        if self._standalone:
            self.pack(fill=tk.BOTH, expand=True)
            self._center()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        if self._standalone:
            self._build_header()
        body_pad = 16 if self._standalone else 12
        body = tk.Frame(self, bg=C_BG, padx=body_pad, pady=12)
        body.pack(fill=tk.BOTH, expand=True)
        self._machine_section(body)
        self._config_section(body)
        self._action_bar(body)
        self._log_section(body)
        self._status_bar()

    def _build_header(self):
        hdr = tk.Frame(self, bg=C_ACCENT)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="Robot Builder",
                 font=("Segoe UI", 15, "bold"),
                 bg=C_ACCENT, fg=C_WHITE, pady=14).pack()
        tk.Label(hdr, text="Build 3L / 5L / AVL robots on a remote Linux machine",
                 font=("Segoe UI", 9), bg=C_ACCENT, fg="#C4B4F4").pack()
        tk.Frame(hdr, bg=C_ACCENT_L, height=3).pack(fill=tk.X, pady=(10, 0))

    def _card(self, parent, title: str) -> tk.Frame:
        outer = tk.Frame(parent, bg=C_ACCENT, padx=1, pady=1)
        outer.pack(fill=tk.X, pady=(0, 10))
        tk.Label(outer, text=title, font=("Segoe UI", 9, "bold"),
                 bg=C_ACCENT, fg=C_WHITE, anchor="w", padx=10, pady=4).pack(fill=tk.X)
        inner = tk.Frame(outer, bg=C_SURFACE, padx=12, pady=10)
        inner.pack(fill=tk.X)
        return inner

    def _machine_section(self, parent):
        f = self._card(parent, "Machine Settings")
        row = tk.Frame(f, bg=C_SURFACE)
        row.pack(fill=tk.X)

        ip_block = tk.Frame(row, bg=C_SURFACE)
        ip_block.pack(side=tk.LEFT, fill=tk.X, padx=(0, 18))
        tk.Label(ip_block, text="Machine IP",
                 font=("Segoe UI", 9), bg=C_SURFACE, fg=C_TEXT, anchor="w"
                 ).pack(fill=tk.X)
        ip_row = tk.Frame(ip_block, bg=C_SURFACE)
        ip_row.pack(fill=tk.X, pady=(2, 0))
        self._ip_var = tk.StringVar()
        ttk.Entry(ip_row, textvariable=self._ip_var, width=22).pack(side=tk.LEFT)
        tk.Label(ip_row, text="(mk7 / mk7)",
                 font=("Segoe UI", 8, "italic"), bg=C_SURFACE, fg=C_MUTED
                 ).pack(side=tk.LEFT, padx=8)

        path_block = tk.Frame(row, bg=C_SURFACE)
        path_block.pack(side=tk.LEFT, fill=tk.X, expand=True)
        tk.Label(path_block, text="Remote Build Dir",
                 font=("Segoe UI", 9), bg=C_SURFACE, fg=C_TEXT, anchor="w"
                 ).pack(fill=tk.X)
        self._builddir_var = tk.StringVar(value=_DEFAULT_BUILDDIR)
        ttk.Entry(path_block, textvariable=self._builddir_var).pack(
            fill=tk.X, pady=(2, 0))

    def _config_section(self, parent):
        f = self._card(parent, "Build Configuration")

        self._build_options_section(f)

        tk.Label(f, text="SVN Checkout URLs",
                 font=("Segoe UI", 9, "bold"), bg=C_SURFACE, fg=C_TEXT,
                 anchor="w").pack(fill=tk.X, pady=(8, 4))

        self._url_frames: dict = {}
        container = tk.Frame(f, bg=C_SURFACE)
        container.pack(fill=tk.X)
        self._url_container = container

        for level in BUILD_LEVELS:
            frm = tk.Frame(container, bg=C_SURFACE)
            frm.columnconfigure(1, weight=1)
            self._url_frames[level] = frm
            for row_idx, (key, label) in enumerate(_URL_LABELS[level]):
                tk.Label(frm, text=f"{label}:", width=16, anchor="w",
                         font=("Segoe UI", 9), bg=C_SURFACE, fg=C_TEXT
                         ).grid(row=row_idx, column=0, sticky="w", pady=2)
                ttk.Entry(frm, textvariable=self._url_vars[level][key]
                          ).grid(row=row_idx, column=1, sticky="ew", padx=4)
                ttk.Button(
                    frm,
                    text="Browse...",
                    command=lambda lvl=level, k=key, lbl=label: self._browse_svn_url(lvl, k, lbl),
                ).grid(row=row_idx, column=2, sticky="e", padx=(4, 0), pady=2)

        self._url_frames[self._level_var.get()].pack(fill=tk.X)

    def _build_options_section(self, parent):
        panel = tk.Frame(parent, bg=C_SURFACE)
        panel.pack(fill=tk.X)
        panel.columnconfigure(0, weight=1)
        panel.columnconfigure(1, weight=1)
        panel.columnconfigure(2, weight=1)
        panel.columnconfigure(3, weight=3)

        self._target_var = tk.StringVar(value="GLI")
        self._level_var = tk.StringVar(value="3L")
        self._component_var = tk.StringVar(value="Both")
        self._flag_vars = {
            key: tk.BooleanVar(value=False)
            for key, _ in BUILD_FLAG_OPTIONS
        }

        target_block, _target_combo = self._combo_block(
            panel, "Target", self._target_var, [value.upper() for value in TARGETS]
        )
        target_block.grid(row=0, column=0, sticky="ew", padx=(0, 10))

        level_block, level_combo = self._combo_block(
            panel, "Build Type", self._level_var, BUILD_LEVELS
        )
        level_block.grid(row=0, column=1, sticky="ew", padx=(0, 10))
        level_combo.bind("<<ComboboxSelected>>", lambda _event: self._on_level_change())

        self._component_labels = {label: value for value, label in COMPONENT_OPTIONS}
        component_block, _component_combo = self._combo_block(
            panel,
            "Components",
            self._component_var,
            list(self._component_labels.keys()),
        )
        component_block.grid(row=0, column=2, sticky="ew", padx=(0, 10))

        flags_box = tk.LabelFrame(
            panel,
            text=" Build Flags ",
            bg=C_SURFACE,
            fg=C_ACCENT,
            padx=10,
            pady=6,
            font=("Segoe UI", 9, "bold"),
        )
        flags_box.grid(row=0, column=3, sticky="nsew")
        flags_box.columnconfigure(0, weight=1)
        flags_box.columnconfigure(1, weight=1)
        flags_box.columnconfigure(2, weight=1)
        for idx, (key, label) in enumerate(BUILD_FLAG_OPTIONS):
            ttk.Checkbutton(flags_box, text=label, variable=self._flag_vars[key]).grid(
                row=idx // 3, column=idx % 3, sticky="w", padx=(0, 12), pady=3)

    @staticmethod
    def _combo_block(parent, label: str, variable: tk.StringVar, values: list) -> tuple:
        block = tk.Frame(parent, bg=C_SURFACE)
        tk.Label(
            block,
            text=label,
            font=("Segoe UI", 9, "bold"),
            bg=C_SURFACE,
            fg=C_TEXT,
            anchor="w",
        ).pack(fill=tk.X, pady=(0, 3))
        combo = ttk.Combobox(
            block,
            textvariable=variable,
            values=values,
            state="readonly",
            width=16,
        )
        combo.pack(fill=tk.X)
        return block, combo

    def _browse_svn_url(self, level: str, key: str, label: str):
        tortoise_proc = self._find_tortoise_proc()
        url_var = self._url_vars[level][key]
        url = url_var.get().strip()

        if tortoise_proc is None:
            messagebox.showerror(
                "TortoiseSVN Not Found",
                "TortoiseSVN Repository Browser could not be opened because "
                "TortoiseProc.exe was not found.",
                parent=self,
            )
            return

        if not url:
            messagebox.showwarning(
                "Missing SVN URL",
                f"Enter a {label} URL before opening the Repository Browser.",
                parent=self,
            )
            return

        try:
            output_path = self._new_repo_browser_output_path()
            proc = subprocess.Popen([
                str(tortoise_proc),
                "/command:repobrowser",
                f"/path:{url}",
                f"/outfile:{output_path}",
            ])
            threading.Thread(
                target=self._repo_browser_result_worker,
                args=(proc, output_path, url_var),
                daemon=True,
            ).start()
        except Exception as exc:
            messagebox.showerror(
                "Repository Browser",
                f"Failed to open TortoiseSVN Repository Browser:\n{exc}",
                parent=self,
            )

    @staticmethod
    def _find_tortoise_proc() -> Optional[Path]:
        for candidate in _TORTOISE_PROC_CANDIDATES:
            if candidate.exists():
                return candidate
        return None

    @staticmethod
    def _new_repo_browser_output_path() -> Path:
        handle = tempfile.NamedTemporaryFile(
            prefix="robot_builder_repo_",
            suffix=".txt",
            delete=False,
        )
        path = Path(handle.name)
        handle.close()
        return path

    def _repo_browser_result_worker(self, proc, output_path: Path, url_var: tk.StringVar):
        try:
            proc.wait()
            if not output_path.exists():
                return
            lines = output_path.read_text(encoding="utf-8", errors="replace").splitlines()
            selected_url = lines[0].strip() if lines else ""
            if selected_url:
                self.after(0, url_var.set, selected_url)
        finally:
            try:
                output_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _on_level_change(self):
        active = self._level_var.get()
        for lvl, frm in self._url_frames.items():
            if lvl == active:
                frm.pack(fill=tk.X)
            else:
                frm.pack_forget()

    def _action_bar(self, parent):
        bar = tk.Frame(parent, bg=C_BG, pady=6)
        bar.pack(fill=tk.X)

        self._start_btn = tk.Button(
            bar, text="▶  Start Build",
            font=("Segoe UI", 10, "bold"),
            bg=C_ACCENT, fg=C_WHITE,
            activebackground=C_ACCENT_L, activeforeground=C_WHITE,
            relief=tk.FLAT, cursor="hand2", padx=20, pady=6,
            command=self._start,
        )
        self._start_btn.pack(side=tk.LEFT, padx=(0, 8))
        self._start_btn.bind("<Enter>", lambda e: self._start_btn.config(bg=C_ACCENT_L))
        self._start_btn.bind("<Leave>", lambda e: self._start_btn.config(bg=C_ACCENT))

        self._stop_btn = tk.Button(
            bar, text="■  Stop Build",
            font=("Segoe UI", 10, "bold"),
            bg="#B71C1C", fg=C_WHITE,
            activebackground="#7F0000", activeforeground=C_WHITE,
            relief=tk.FLAT, cursor="hand2", padx=20, pady=6,
            command=self._stop,
            state=tk.DISABLED,
        )
        self._stop_btn.pack(side=tk.LEFT, padx=(0, 8))
        ttk.Button(bar, text="Clear Log", command=self._clear_log).pack(side=tk.LEFT)

    def _log_section(self, parent):
        tk.Label(parent, text="Build Output", font=("Segoe UI", 9, "bold"),
                 bg=C_BG, fg=C_TEXT, anchor="w").pack(fill=tk.X)
        self._log = scrolledtext.ScrolledText(
            parent,
            font=("Consolas", 9),
            bg="#1E1E2E", fg="#CDD6F4",
            insertbackground="#CDD6F4",
            state=tk.DISABLED,
            wrap=tk.NONE,
            height=20,
        )
        self._log.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self._log.tag_configure("info",  foreground="#89B4FA")
        self._log.tag_configure("ok",    foreground="#A6E3A1")
        self._log.tag_configure("error", foreground="#F38BA8")
        self._log.tag_configure("warn",  foreground="#FAB387")
        self._log.tag_configure("dim",   foreground="#6C7086")

    def _status_bar(self):
        bar = tk.Frame(self, bg="#E8E4F3", pady=4, padx=12)
        bar.pack(fill=tk.X, side=tk.BOTTOM)
        self._status_var  = tk.StringVar(value="Ready")
        self._elapsed_var = tk.StringVar(value="")
        tk.Label(bar, textvariable=self._status_var,
                 font=("Segoe UI", 8), bg="#E8E4F3", fg=C_MUTED).pack(side=tk.LEFT)
        tk.Label(bar, textvariable=self._elapsed_var,
                 font=("Segoe UI", 8), bg="#E8E4F3", fg=C_MUTED).pack(side=tk.RIGHT)

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _center(self):
        self.update_idletasks()
        sw, sh = self._root_window.winfo_screenwidth(), self._root_window.winfo_screenheight()
        w, h   = self._root_window.winfo_width(), self._root_window.winfo_height()
        self._root_window.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")

    def _log_append(self, text: str, tag: str = ""):
        self._log.configure(state=tk.NORMAL)
        if tag:
            self._log.insert(tk.END, text, tag)
        else:
            self._log.insert(tk.END, text)
        self._log.see(tk.END)
        self._log.configure(state=tk.DISABLED)

    def _clear_log(self):
        self._log.configure(state=tk.NORMAL)
        self._log.delete("1.0", tk.END)
        self._log.configure(state=tk.DISABLED)

    def _tick(self):
        if not self._build_active:
            return
        e = int(time.time() - self._start_time)
        h, r = divmod(e, 3600)
        m, s = divmod(r, 60)
        self._elapsed_var.set(f"Elapsed: {h:02d}:{m:02d}:{s:02d}")
        self._elapsed_job = self.after(1000, self._tick)

    def _build_done(self, success: bool):
        self._build_active = False
        if self._elapsed_job:
            self.after_cancel(self._elapsed_job)
            self._elapsed_job = None
        self._start_btn.config(state=tk.NORMAL)
        self._stop_btn.config(state=tk.DISABLED)
        self._status_var.set("Build complete" if success else "Stopped / failed — see log")

    # ── Build args & script patching ──────────────────────────────────────────

    def _collected_args(self) -> list:
        args = [self._target_var.get().lower(), self._level_var.get()]

        component = self._component_labels.get(self._component_var.get(), "both")
        if component in ("both", "platform"):
            args.append("--platform")
        if component in ("both", "game"):
            args.append("--game")

        flag_args = {
            "clean": "--clean",
            "showmode": "--showmode",
            "production": "--production",
            "robot": "--robot",
            "asan": "--asan",
            "tcmalloc": "--tcmalloc",
        }
        for key, flag in flag_args.items():
            if self._flag_vars[key].get():
                args.append(flag)
        return args

    def _patched_script_bytes(self) -> bytes:
        """Read bundled script, replace any user-edited SVN URLs, return as bytes."""
        content = _BUNDLED_SCRIPT.read_text(encoding="utf-8")
        level   = self._level_var.get()
        for key, default_url in _DEFAULT_URLS[level].items():
            user_url = self._url_vars[level][key].get().strip()
            if user_url and user_url != default_url:
                # Replace the first occurrence (each URL is unique per level function)
                content = content.replace(default_url, user_url, 1)
        return content.encode("utf-8")

    # ── Actions ───────────────────────────────────────────────────────────────

    def _start(self):
        ip   = self._ip_var.get().strip()
        bdir = self._builddir_var.get().strip() or _DEFAULT_BUILDDIR

        if not ip:
            messagebox.showerror("Missing IP", "Enter the machine IP address.", parent=self)
            return
        if not _BUNDLED_SCRIPT.exists():
            messagebox.showerror(
                "Script Missing",
                f"Bundled build script not found:\n{_BUNDLED_SCRIPT}\n\n"
                "Place Linux_BuildScript.sh alongside main.py.",
                parent=self,
            )
            return

        script_bytes = self._patched_script_bytes()

        self._stop_event.clear()
        self._build_active = True
        self._start_time   = time.time()
        self._start_btn.config(state=tk.DISABLED)
        self._stop_btn.config(state=tk.NORMAL)
        self._status_var.set("Connecting…")
        self._tick()

        threading.Thread(
            target=self._worker,
            args=(ip, bdir, self._collected_args(), script_bytes),
            daemon=True,
        ).start()

    def _stop(self):
        self._stop_event.set()
        self._status_var.set("Stopping…")

    # ── Worker thread ─────────────────────────────────────────────────────────

    def _worker(self, ip: str, build_dir: str, args: list, script_bytes: bytes):
        try:
            import paramiko
        except ImportError:
            self.after(0, self._log_append,
                       "ERROR: paramiko not installed.\n"
                       "Run:  <Python3_path>\\python.exe -m pip install paramiko\n",
                       "error")
            self.after(0, self._build_done, False)
            return

        ssh = paramiko.SSHClient()
        ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())

        try:
            self.after(0, self._log_append, f"[build] Connecting to {ip}…\n", "info")
            ssh.connect(ip, username=SSH_USER, password=SSH_PASS, timeout=15)

            if self._stop_event.is_set():
                return

            self.after(0, self._log_append, "[build] Checking remote shell defaults…\n", "info")
            shell_setup = self._ensure_remote_shell_defaults(ssh)
            if shell_setup:
                self.after(0, self._log_append, shell_setup, "dim")

            if self._stop_event.is_set():
                return

            # Upload patched script from memory — no temp file needed
            self.after(0, self._log_append, "[build] Uploading build script…\n", "info")
            sftp = ssh.open_sftp()
            self._sftp_makedirs(sftp, build_dir)
            remote_script = f"{build_dir}/{REMOTE_SCRIPT_NAME}"
            sftp.putfo(io.BytesIO(script_bytes), remote_script)
            sftp.close()
            self._exec(ssh, f"chmod +x '{remote_script}'")

            if self._stop_event.is_set():
                return

            # Kill any leftover screen session from a previous run
            self._exec(ssh,
                       f"screen -X -S {SCREEN_NAME} quit 2>/dev/null; sleep 0.2; true")

            # Reset log file
            remote_log = f"{build_dir}/{REMOTE_LOG_NAME}"
            self._exec(ssh, f": > '{remote_log}'")

            # Build the screen command
            args_str = " ".join(args)
            build_cmd = f"bash '{remote_script}' {args_str}"
            screen_payload = (
                f"cd '{build_dir}' && {build_cmd} 2>&1 | tee '{remote_log}'; "
                f"echo '=== BUILD COMPLETE ==='; exec bash"
            )
            screen_cmd = f"screen -dmS {SCREEN_NAME} bash -c \"{screen_payload}\""

            self.after(0, self._log_append,
                       f"[build] Command : Linux_BuildScript.sh {args_str}\n", "info")
            self.after(0, self._log_append,
                       f"[build] Screen  : {SCREEN_NAME}\n", "dim")
            self.after(0, self._log_append,
                       f"[build] Log     : {remote_log}\n", "dim")

            self._exec(ssh, screen_cmd)

            if self._stop_event.is_set():
                return

            self.after(0, self._log_append,
                       "[build] Build started — streaming output…\n\n", "dim")
            self.after(0, self._status_var.set, "Building…")

            self._tail(ssh, remote_log)

        except Exception as exc:
            self.after(0, self._log_append, f"\n[ERROR] {exc}\n", "error")
            self.after(0, self._build_done, False)
        finally:
            try:
                ssh.close()
            except Exception:
                pass

    # ── SSH helpers (called from worker thread) ────────────────────────────────

    def _ensure_remote_shell_defaults(self, ssh) -> str:
        """Install prompt/locale defaults on the remote mk7 account."""
        home = self._exec(ssh, 'printf %s "$HOME"').strip() or f"/home/{SSH_USER}"
        changes = []

        block = (
            f"\n{SHELL_BLOCK_MARKER}\n"
            "# Keep remote shells consistent for Robot Builder sessions.\n"
            f"{LOCALE_LINES[0]}\n"
            f"{LOCALE_LINES[1]}\n"
            f"{PROMPT_LINE}\n"
        )

        sftp = ssh.open_sftp()
        try:
            for name in (".bashrc", ".profile"):
                remote_path = posixpath.join(home, name)
                if self._ensure_remote_file_block(sftp, remote_path, block):
                    changes.append(f"[build] Updated {remote_path}\n")
        finally:
            sftp.close()

        if self._fix_remote_system_locale(ssh):
            changes.append("[build] Updated /etc/alx-environment LC_ALL fallback\n")

        if not changes:
            return "[build] Remote shell defaults already configured\n"
        return "".join(changes)

    @staticmethod
    def _ensure_remote_file_block(sftp, remote_path: str, block: str) -> bool:
        try:
            with sftp.open(remote_path, "r") as fh:
                content = fh.read().decode(errors="replace")
        except IOError:
            content = ""

        required = (SHELL_BLOCK_MARKER, PROMPT_LINE, *LOCALE_LINES)
        if all(line in content for line in required):
            return False

        separator = "" if not content or content.endswith("\n") else "\n"
        with sftp.open(remote_path, "w") as fh:
            fh.write((content + separator + block).encode("utf-8"))
        return True

    def _fix_remote_system_locale(self, ssh) -> bool:
        needs_fix = (
            "test -f /etc/alx-environment && "
            "grep -qx 'export LC_ALL=en_US.UTF-8' /etc/alx-environment && "
            "! locale -a 2>/dev/null | grep -Eiq '^(en_US\\.utf8|en_US\\.UTF-8)$'"
        )
        code, _, _ = self._exec_status(ssh, needs_fix)
        if code != 0:
            return False

        cmd = (
            "printf '%s\\n' 'mk7' | sudo -S -p '' sh -c "
            "\"cp /etc/alx-environment "
            "/etc/alx-environment.robot-builder-backup-$(date +%Y%m%d%H%M%S) && "
            "sed -i 's/^export LC_ALL=en_US\\.UTF-8$/export LC_ALL=C/' "
            "/etc/alx-environment\""
        )
        code, _, _ = self._exec_status(ssh, cmd)
        return code == 0

    def _exec(self, ssh, cmd: str) -> str:
        _, stdout, _ = ssh.exec_command(cmd)
        stdout.channel.recv_exit_status()
        return stdout.read().decode(errors="replace")

    @staticmethod
    def _exec_status(ssh, cmd: str) -> Tuple[int, str, str]:
        _, stdout, stderr = ssh.exec_command(cmd)
        code = stdout.channel.recv_exit_status()
        out = stdout.read().decode(errors="replace")
        err = stderr.read().decode(errors="replace")
        return code, out, err

    @staticmethod
    def _sftp_makedirs(sftp, path: str):
        cur = ""
        for part in path.strip("/").split("/"):
            cur += "/" + part
            try:
                sftp.stat(cur)
            except IOError:
                try:
                    sftp.mkdir(cur)
                except IOError:
                    pass

    def _tail(self, ssh, remote_log: str):
        """Follow remote_log until '=== BUILD COMPLETE ===' or stop is requested."""
        transport = ssh.get_transport()
        chan = transport.open_session()
        chan.exec_command(f"tail -n 0 -f '{remote_log}'")

        completed = False
        buf = b""

        while not self._stop_event.is_set():
            if chan.recv_ready():
                chunk = chan.recv(8192)
                if not chunk:
                    break
                buf += chunk
                lines = buf.split(b"\n")
                buf = lines[-1]
                for raw in lines[:-1]:
                    text = raw.decode(errors="replace")
                    if "=== BUILD COMPLETE ===" in text:
                        completed = True
                    self.after(0, self._log_append, text + "\n", self._line_tag(text))
                if completed:
                    self._stop_event.set()
                    break
            elif chan.exit_status_ready():
                break
            else:
                time.sleep(0.05)

        chan.close()

        if not completed:
            # User pressed Stop — send Ctrl+C to the running process, then kill session
            try:
                self._exec(ssh,
                           f"screen -X -S {SCREEN_NAME} stuff $'\\003'; "
                           f"sleep 1; "
                           f"screen -X -S {SCREEN_NAME} quit 2>/dev/null; true")
                self.after(0, self._log_append,
                           "\n[build] Build terminated by user.\n", "warn")
            except Exception:
                pass

        self.after(0, self._build_done, completed)

    @staticmethod
    def _line_tag(text: str) -> str:
        lower = text.lower()
        if "=== build complete ===" in lower or "[build] done." in lower:
            return "ok"
        if any(w in lower for w in ("error:", "fatal error", "failed")):
            return "error"
        if any(w in lower for w in ("warning:", "warn:")):
            return "warn"
        if text.startswith("[build]"):
            return "info"
        return ""


if __name__ == "__main__":
    app = RobotBuilderApp(standalone=True)
    app.winfo_toplevel().mainloop()

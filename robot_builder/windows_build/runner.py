"""Run planned stages one after another on a worker thread.

The GUI never touches a subprocess directly. It hands a list of `Stage`s to
`PipelineRunner.start()` and drains `runner.queue` from the Tk main loop.
Messages are tuples:

    ("stage",  index, stage)        a stage is about to start
    ("line",   tag,   text)         one line of output; tag is a LogPane tag
    ("result", index, code)         stage finished with this exit code
    ("finished", ok, summary)       whole run ended (ok is False on any failure or cancel)
"""

import os
import queue
import re
import subprocess
import threading
import time
from typing import List, Optional

from . import pipeline
from .pipeline import Stage

if os.name == "nt":
    _NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW}
else:  # pragma: no cover - the tool targets Windows
    _NO_WINDOW = {}

ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")

# Patterns that colour a line in the log. MSBuild prints "error C2039:",
# "warning MSB8005:" etc.; CMake prints "CMake Error"; svn prints "svn: E1234".
ERROR_RE = re.compile(r"(^|\s|:)(error|fatal error)(\s|:)|CMake Error|^svn: E\d+|Exception|Error caught", re.IGNORECASE)
WARN_RE = re.compile(r"(^|\s|:)warning(\s|:)|CMake Warning|^WARNING:", re.IGNORECASE)
OK_RE = re.compile(r"Build succeeded|-- Configuring done|-- Generating done|^(Updated to|Checked out) revision", re.IGNORECASE)


def strip_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def classify(line: str) -> str:
    if ERROR_RE.search(line):
        return "error"
    if WARN_RE.search(line):
        return "warn"
    if OK_RE.search(line):
        return "ok"
    if line.startswith(("-- ", "Installing:", "Up-to-date:")):
        return "info"
    return "plain"


class PipelineRunner:
    def __init__(self):
        self.queue = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._process: Optional[subprocess.Popen] = None
        self._cancel = threading.Event()

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, stages: List[Stage]) -> None:
        if self.running:
            raise RuntimeError("A run is already in progress")
        self._cancel.clear()
        self._thread = threading.Thread(target=self._run_all, args=(list(stages),), daemon=True)
        self._thread.start()

    def cancel(self) -> None:
        self._cancel.set()
        proc = self._process
        if proc is not None and proc.poll() is None:
            try:
                # MSBuild and svn spawn children; kill the whole tree.
                if os.name == "nt":
                    subprocess.run(
                        ["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                        capture_output=True, **_NO_WINDOW,
                    )
                else:  # pragma: no cover
                    proc.terminate()
            except OSError:
                pass

    # ------------------------------------------------------------------

    def _emit(self, *message) -> None:
        self.queue.put(message)

    def _run_all(self, stages: List[Stage]) -> None:
        started = time.monotonic()
        for index, stage in enumerate(stages):
            if self._cancel.is_set():
                self._emit("finished", False, "Cancelled before '{}'.".format(stage.title))
                return
            self._emit("stage", index, stage)

            problem = self._preflight(stage)
            if problem:
                self._emit("line", "error", problem)
                self._emit("result", index, 1)
                self._emit("finished", False, "Stopped at '{}'.".format(stage.title))
                return

            code = self._run_one(stage)
            self._emit("result", index, code)
            if self._cancel.is_set():
                self._emit("finished", False, "Cancelled during '{}'.".format(stage.title))
                return
            if code != 0:
                self._emit(
                    "finished", False,
                    "'{}' failed with exit code {}.".format(stage.title, code),
                )
                return

        elapsed = time.monotonic() - started
        self._emit("finished", True, "All {} stage(s) completed in {}.".format(len(stages), _fmt_elapsed(elapsed)))

    def _preflight(self, stage: Stage) -> Optional[str]:
        if stage.kind == "svn" and stage.check_dir and stage.expected_url:
            actual = pipeline.working_copy_url(stage.check_dir)
            if actual is None:
                return (
                    "{} has a .svn folder but 'svn info' failed. Fix or remove the folder "
                    "before running again.".format(stage.check_dir)
                )
            if not pipeline.urls_match(actual, stage.expected_url):
                return (
                    "{} is a working copy of\n    {}\nbut you asked for\n    {}\n"
                    "Change the URL, pick another folder, or run 'svn switch' / 'svn relocate' yourself."
                    .format(stage.check_dir, actual, stage.expected_url)
                )
        if stage.kind == "configure":
            script = stage.argv[-1].split("'")[1] if "'" in stage.argv[-1] else ""
            if script and not os.path.isfile(script):
                return "Workspace script not found: {}\nCheck out the Runtime first (untick Skip SVN).".format(script)
        return None

    def _run_one(self, stage: Stage) -> int:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        # Keep CMake/MSBuild output free of colour codes and prompts.
        env["CLICOLOR"] = "0"
        env["NO_COLOR"] = "1"
        cwd = stage.cwd if stage.cwd and os.path.isdir(stage.cwd) else None
        if stage.kind == "svn" and stage.argv[1] == "checkout":
            parent = os.path.dirname(stage.argv[-1])
            try:
                os.makedirs(parent, exist_ok=True)
            except OSError as exc:
                self._emit("line", "error", "Cannot create {}: {}".format(parent, exc))
                return 1
        try:
            self._process = subprocess.Popen(
                stage.argv,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1,
                env=env,
                **_NO_WINDOW,
            )
        except (OSError, ValueError) as exc:
            self._emit("line", "error", "Failed to start '{}': {}".format(stage.argv[0], exc))
            return -1

        for raw in self._process.stdout:
            line = strip_ansi(raw.rstrip("\r\n"))
            self._emit("line", classify(line), line)

        self._process.stdout.close()
        code = self._process.wait()
        self._process = None
        return code


def _fmt_elapsed(seconds: float) -> str:
    seconds = int(round(seconds))
    if seconds < 60:
        return "{}s".format(seconds)
    minutes, seconds = divmod(seconds, 60)
    if minutes < 60:
        return "{}m {:02d}s".format(minutes, seconds)
    hours, minutes = divmod(minutes, 60)
    return "{}h {:02d}m".format(hours, minutes)

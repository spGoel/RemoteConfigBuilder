"""TortoiseSVN Repository Browser integration shared by the Linux and Windows tabs.

`browse_svn_url()` opens TortoiseProc's repobrowser at the URL currently in a
StringVar and, when the user picks a node and closes it, writes the chosen URL
back into that StringVar on the Tk main thread. Behaviour is unchanged from
the original Robot Builder implementation; it just lives in one place now.
"""

import subprocess
import tempfile
import threading
import tkinter as tk
from pathlib import Path
from tkinter import messagebox
from typing import Optional

TORTOISE_PROC_CANDIDATES = (
    Path(r"C:\Program Files\TortoiseSVN\bin\TortoiseProc.exe"),
    Path(r"C:\Program Files (x86)\TortoiseSVN\bin\TortoiseProc.exe"),
)


def find_tortoise_proc() -> Optional[Path]:
    for candidate in TORTOISE_PROC_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def _new_output_path() -> Path:
    handle = tempfile.NamedTemporaryFile(
        prefix="robot_builder_repo_", suffix=".txt", delete=False,
    )
    path = Path(handle.name)
    handle.close()
    return path


def _result_worker(widget, proc, output_path: Path, url_var: tk.StringVar):
    try:
        proc.wait()
        if not output_path.exists():
            return
        lines = output_path.read_text(encoding="utf-8", errors="replace").splitlines()
        selected_url = lines[0].strip() if lines else ""
        if selected_url:
            widget.after(0, url_var.set, selected_url)
    finally:
        try:
            output_path.unlink(missing_ok=True)
        except Exception:
            pass


def browse_svn_url(widget, url_var: tk.StringVar, label: str) -> bool:
    """Open the repo browser for `url_var`. Returns True if it was launched.

    `widget` is used as the dialog parent and for marshalling the result back
    onto the Tk thread.
    """
    tortoise_proc = find_tortoise_proc()
    url = url_var.get().strip()

    if tortoise_proc is None:
        messagebox.showerror(
            "TortoiseSVN Not Found",
            "TortoiseSVN Repository Browser could not be opened because "
            "TortoiseProc.exe was not found.",
            parent=widget,
        )
        return False

    if not url:
        messagebox.showwarning(
            "Missing SVN URL",
            f"Enter a {label} URL before opening the Repository Browser.",
            parent=widget,
        )
        return False

    try:
        output_path = _new_output_path()
        proc = subprocess.Popen([
            str(tortoise_proc),
            "/command:repobrowser",
            f"/path:{url}",
            f"/outfile:{output_path}",
        ])
        threading.Thread(
            target=_result_worker,
            args=(widget, proc, output_path, url_var),
            daemon=True,
        ).start()
        return True
    except Exception as exc:
        messagebox.showerror(
            "Repository Browser",
            f"Failed to open TortoiseSVN Repository Browser:\n{exc}",
            parent=widget,
        )
        return False

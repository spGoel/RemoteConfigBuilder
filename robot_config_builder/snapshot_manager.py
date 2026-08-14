"""
Remote screenshot capture from game machine via SSH/SCP.
Credentials are always mk7/mk7 (hardcoded per spec).
Requires: pip install paramiko
"""
import json
import tempfile
import threading
from pathlib import Path

SETTINGS_FILE = Path.home() / ".robot_config_builder_machine.json"

# Landscape: game screen is 2nd 4K monitor positioned below the first (y=+2160)
_LANDSCAPE_CROP = "3840x2160+0+2160"

# Fixed remote path where the screenshot is saved on the Linux machine
_REMOTE_SCREENSHOT = "/home/mk7/development/screenshot.png"

# Local cache directory (Windows temp)
_CACHE_DIR = Path(tempfile.gettempdir()) / "rcb_screenshots"


def load_settings() -> dict:
    try:
        if SETTINGS_FILE.exists():
            data = json.loads(SETTINGS_FILE.read_text())
            return data
    except Exception:
        pass
    return {"ip": "", "orientation": "landscape"}


def save_settings(ip: str, orientation: str):
    try:
        SETTINGS_FILE.write_text(json.dumps({"ip": ip, "orientation": orientation}))
    except Exception:
        pass


def get_cached_path(ip: str) -> Path:
    """Returns the local cache path for a given machine IP."""
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    safe_ip = ip.strip().replace(".", "_")
    return _CACHE_DIR / f"screenshot_{safe_ip}.png"


def take_screenshot(ip: str, orientation: str) -> str:
    """
    SSH into the game machine, capture screenshot, SCP it to Windows temp.
    Returns local path to downloaded PNG.

    Portrait  → full screen, no crop (entire X root window)
    Landscape → 2nd 4K monitor, crop 3840x2160+0+2160
    """
    try:
        import paramiko
    except ImportError:
        raise RuntimeError(
            "paramiko is not installed.\n\n"
            "Install it by running:\n"
            r'C:\Users\SG108049\AppData\Local\Programs\Python\Python314\python.exe'
            " -m pip install paramiko"
        )

    ip = ip.strip()
    if not ip:
        raise ValueError(
            "Game machine IP address is empty.\n"
            "Enter the IP in the Machine section of any touch event."
        )

    if orientation.lower() == "landscape":
        cmd = (
            f"mkdir -p /home/mk7/development && "
            f"DISPLAY=:0.0 import -window root -crop '{_LANDSCAPE_CROP}' '{_REMOTE_SCREENSHOT}'"
        )
    else:
        # Portrait: capture entire display, no crop
        cmd = (
            f"mkdir -p /home/mk7/development && "
            f"DISPLAY=:0.0 import -window root '{_REMOTE_SCREENSHOT}'"
        )

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    client.connect(ip, username="mk7", password="mk7", timeout=15)

    try:
        _, stdout, stderr = client.exec_command(cmd)
        exit_code = stdout.channel.recv_exit_status()
        if exit_code != 0:
            err = stderr.read().decode(errors="replace").strip()
            raise RuntimeError(
                f"Screenshot command failed (exit {exit_code}):\n"
                f"{err or '(no stderr output)'}"
            )

        local_path = get_cached_path(ip)
        sftp = client.open_sftp()
        sftp.get(_REMOTE_SCREENSHOT, str(local_path))
        sftp.close()
    finally:
        client.close()

    return str(local_path)


def take_screenshot_async(ip: str, orientation: str, tk_widget,
                           on_done, on_error):
    """
    Non-blocking. Runs take_screenshot in a daemon thread.
    on_done(path) and on_error(msg) are dispatched on the tkinter main thread
    via tk_widget.after(0, ...).
    """
    def _run():
        try:
            path = take_screenshot(ip, orientation)
            tk_widget.after(0, lambda p=path: on_done(p))
        except Exception as exc:
            msg = str(exc)
            tk_widget.after(0, lambda m=msg: on_error(m))

    threading.Thread(target=_run, daemon=True).start()

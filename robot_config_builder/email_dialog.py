"""
Email Manager dialog.

- Add / Remove : manage the local email list (@aristocrat.com only).
- Update XML   : SSH into the EGM, search robot_conf.xml under the
                 configured Build Path, and write all emails into every
                 file found.  Falls back to the default path when no
                 Build Path is set.
"""
import io
import json
import re
import sys
import threading
import tkinter as tk
from tkinter import ttk, messagebox
from pathlib import Path
import xml.etree.ElementTree as ET

# ── Constants ─────────────────────────────────────────────────────────────────
SETTINGS_FILE         = Path.home() / ".robot_config_builder_emails.json"
DEFAULT_REMOTE_CONFIG = "/home/mk7/development/robot_conf.xml"
EMAIL_SUBJECT         = "Robot Test Status"
SSH_USER              = "mk7"
SSH_PASS              = "mk7"

_EMAIL_RE = re.compile(r"^[^@\s]+@aristocrat\.com$", re.IGNORECASE)


# ── Persistence ───────────────────────────────────────────────────────────────

def load_emails() -> list:
    try:
        if SETTINGS_FILE.exists():
            return json.loads(SETTINGS_FILE.read_text()).get("emails", [])
    except Exception:
        pass
    return []


def save_emails(emails: list):
    try:
        SETTINGS_FILE.write_text(json.dumps({"emails": emails}))
    except Exception:
        pass


# ── XML helper ────────────────────────────────────────────────────────────────

def _merge_emails_into_xml(xml_content: str, new_emails: list) -> tuple:
    """
    Merge new_emails into <email_id> of robot_conf.xml content.
    Returns (updated_xml_str, added_count: int, already_existing: list).
    """
    if xml_content and xml_content.strip():
        try:
            root = ET.fromstring(xml_content.strip())
        except ET.ParseError:
            root = ET.Element("robot_conf")
    else:
        root = ET.Element("robot_conf")

    email_id_el = root.find("email_id")
    if email_id_el is None:
        email_id_el = ET.SubElement(root, "email_id")
        email_id_el.text = ""

    existing       = [e.strip() for e in (email_id_el.text or "").split(",") if e.strip()]
    existing_lower = [e.lower() for e in existing]

    added   = []
    already = []
    for email in new_emails:
        if email.lower() in existing_lower:
            already.append(email)
        else:
            existing.append(email)
            existing_lower.append(email.lower())
            added.append(email)

    email_id_el.text = ",".join(existing)

    subject_el = root.find("email_subject")
    if subject_el is None:
        subject_el = ET.SubElement(root, "email_subject")
    subject_el.text = f" {EMAIL_SUBJECT} "

    try:
        ET.indent(root, space="\t")
    except AttributeError:
        pass

    return ET.tostring(root, encoding="unicode"), len(added), already


# ── Dialog ────────────────────────────────────────────────────────────────────

class EmailDialog(tk.Toplevel):
    def __init__(self, parent, ip_var: tk.StringVar,
                 build_path_var: tk.StringVar):
        super().__init__(parent)
        self.title("Email Manager")
        self.resizable(False, False)
        self.grab_set()

        self._ip_var         = ip_var
        self._build_path_var = build_path_var
        self._emails         = load_emails()
        self._busy           = False

        self._build_ui()
        self._refresh_list()

        self.update_idletasks()
        x = parent.winfo_rootx() + (parent.winfo_width()  - self.winfo_width())  // 2
        y = parent.winfo_rooty() + (parent.winfo_height() - self.winfo_height()) // 2
        self.geometry(f"+{x}+{y}")

    # ── UI ─────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        pad = dict(padx=12, pady=6)

        add_frame = ttk.LabelFrame(self, text="Add Email Address  (must be @aristocrat.com)")
        add_frame.pack(fill=tk.X, **pad)

        entry_row = ttk.Frame(add_frame)
        entry_row.pack(fill=tk.X, padx=8, pady=(6, 8))

        self._entry_var = tk.StringVar()
        self._entry = ttk.Entry(entry_row, textvariable=self._entry_var, width=36)
        self._entry.pack(side=tk.LEFT, padx=(0, 6))
        self._entry.bind("<Return>", lambda _: self._add())

        self._add_btn = ttk.Button(entry_row, text="Add", command=self._add)
        self._add_btn.pack(side=tk.LEFT)

        # Status label
        self._status_var = tk.StringVar()
        ttk.Label(self, textvariable=self._status_var,
                  foreground="#555", font=("Segoe UI", 8)).pack(
            anchor=tk.W, padx=14, pady=(0, 2))

        # Email list
        list_frame = ttk.LabelFrame(self, text="Email Addresses")
        list_frame.pack(fill=tk.BOTH, expand=True, padx=12, pady=(0, 6))

        self._tree = ttk.Treeview(list_frame, columns=("email",), show="headings",
                                   height=8, selectmode="browse")
        self._tree.heading("email", text="Email")
        self._tree.column("email", width=340, anchor=tk.W)
        self._tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0), pady=8)

        sb = ttk.Scrollbar(list_frame, orient=tk.VERTICAL, command=self._tree.yview)
        sb.pack(side=tk.RIGHT, fill=tk.Y, pady=8, padx=(0, 4))
        self._tree.configure(yscrollcommand=sb.set)
        self._tree.bind("<Delete>", lambda _: self._remove())

        # Bottom buttons
        btn_row = ttk.Frame(self)
        btn_row.pack(fill=tk.X, padx=12, pady=(0, 12))

        ttk.Button(btn_row, text="Remove Selected",
                   command=self._remove).pack(side=tk.LEFT)
        ttk.Button(btn_row, text="Remove All",
                   command=self._remove_all).pack(side=tk.LEFT, padx=(6, 0))

        self._update_btn = ttk.Button(btn_row, text="Update XML",
                                       command=self._update_xml)
        self._update_btn.pack(side=tk.RIGHT, padx=(6, 0))
        ttk.Button(btn_row, text="Close",
                   command=self.destroy).pack(side=tk.RIGHT)

        self._entry.focus_set()

    # ── Add (local only) ───────────────────────────────────────────────────────

    def _add(self):
        if self._busy:
            return

        email = self._entry_var.get().strip()
        if not email:
            return

        if not _EMAIL_RE.match(email):
            messagebox.showerror(
                "Invalid Email",
                f"Only @aristocrat.com addresses are allowed.\n\n"
                f"'{email}' is not a valid email.",
                parent=self,
            )
            self._entry.focus_set()
            return

        email = email.lower()

        if email in self._emails:
            messagebox.showinfo("Already Added",
                                f"'{email}' is already in the list.",
                                parent=self)
            self._entry_var.set("")
            self._entry.focus_set()
            return

        self._emails.append(email)
        save_emails(self._emails)
        self._refresh_list()
        self._entry_var.set("")
        self._status_var.set(f"'{email}' added. Click 'Update XML' to push to EGM.")
        self._entry.focus_set()

    # ── Update XML (SSH → search → update) ────────────────────────────────────

    def _update_xml(self):
        if self._busy:
            return

        if not self._emails:
            messagebox.showwarning("No Emails",
                                   "Add at least one email address before updating.",
                                   parent=self)
            return

        ip = self._ip_var.get().strip()
        if not ip:
            messagebox.showwarning(
                "IP Not Configured",
                "Please configure the Game Machine IP address in the main window first.",
                parent=self,
            )
            return

        if not self._build_path_var.get().strip():
            messagebox.showwarning(
                "Build Path Not Configured",
                "Please configure the Build Path in the main window first.\n\n"
                "robot_conf.xml will be searched only within the provided path.",
                parent=self,
            )
            return

        self._set_busy(True, f"Connecting to {ip}…")
        threading.Thread(target=self._worker, args=(ip,), daemon=True).start()

    def _worker(self, ip: str):
        try:
            import paramiko
        except ImportError:
            self.after(0, self._on_error,
                       f"paramiko is not installed.\n\n"
                       f"Install it by running:\n"
                       f'"{sys.executable}" -m pip install paramiko')
            return

        try:
            client = paramiko.SSHClient()
            client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
            client.connect(ip, username=SSH_USER, password=SSH_PASS, timeout=15)

            # ── Locate robot_conf.xml in the provided build path ─────────────
            build_path = self._build_path_var.get().strip()

            self.after(0, lambda: self._status_var.set(
                f"Searching for robot_conf.xml in {build_path}…"))

            _, stdout, _ = client.exec_command(
                f"find '{build_path}' -name 'robot_conf.xml' 2>/dev/null"
            )
            stdout.channel.recv_exit_status()
            found = [p.strip() for p in stdout.read().decode().splitlines() if p.strip()]

            if not found:
                client.close()
                self.after(0, self._on_error,
                           f"robot_conf.xml not found in:\n{build_path}\n\n"
                           f"Check that the Build Path is correct.")
                return

            # ── Update each file found ────────────────────────────────────────
            sftp        = client.open_sftp()
            total_added = 0
            all_already = []

            for config_path in found:
                self.after(0, lambda p=config_path: self._status_var.set(
                    f"Updating {p}…"))

                try:
                    with sftp.open(config_path, "r") as f:
                        xml_content = f.read().decode("utf-8", errors="replace")
                except IOError:
                    xml_content = ""

                updated_xml, added_count, already = _merge_emails_into_xml(
                    xml_content, self._emails)

                total_added += added_count
                all_already.extend(already)

                if added_count > 0:
                    # Ensure parent directory exists
                    remote_dir = config_path.rsplit("/", 1)[0]
                    _, so, _ = client.exec_command(f"mkdir -p '{remote_dir}'")
                    so.channel.recv_exit_status()
                    sftp.putfo(io.BytesIO(updated_xml.encode("utf-8")), config_path)

            sftp.close()
            client.close()

            self.after(0, self._on_success, found, total_added, all_already)

        except Exception as exc:
            self.after(0, self._on_error, str(exc))

    # ── Worker callbacks ───────────────────────────────────────────────────────

    def _on_success(self, paths: list, added_count: int, already: list):
        if added_count == 0:
            self._set_busy(False, "")
            messagebox.showinfo(
                "Already Existing",
                "All email(s) are already present in robot_conf.xml on the EGM:\n\n"
                + "\n".join(set(already)),
                parent=self,
            )
            return

        files_updated = "\n".join(paths)
        msg = (f"{added_count} email(s) added to robot_conf.xml.\n\n"
               f"File(s) updated ({len(paths)}):\n{files_updated}")
        if already:
            msg += f"\n\nAlready existing (skipped):\n" + "\n".join(set(already))

        self._set_busy(False,
                       f"✓ {len(paths)} file(s) updated — {added_count} email(s) added.")
        messagebox.showinfo("Update Successful", msg, parent=self)

    def _on_error(self, msg: str):
        self._set_busy(False, "")
        messagebox.showerror("EGM Error", msg, parent=self)

    # ── List helpers ───────────────────────────────────────────────────────────

    def _remove(self):
        sel = self._tree.selection()
        if not sel:
            return
        email = self._tree.item(sel[0], "values")[0]
        if email in self._emails:
            self._emails.remove(email)
            save_emails(self._emails)
            self._refresh_list()
            self._status_var.set(f"'{email}' removed from local list.")

    def _remove_all(self):
        if not self._emails:
            return
        if not messagebox.askyesno("Remove All",
                                    "Remove all email addresses from the local list?",
                                    parent=self):
            return
        self._emails.clear()
        save_emails(self._emails)
        self._refresh_list()
        self._status_var.set("All emails removed from local list.")

    def _refresh_list(self):
        self._tree.delete(*self._tree.get_children())
        for email in self._emails:
            self._tree.insert("", tk.END, values=(email,))

    # ── UI state ───────────────────────────────────────────────────────────────

    def _set_busy(self, busy: bool, status: str):
        self._busy = busy
        self._status_var.set(status)
        state = tk.DISABLED if busy else tk.NORMAL
        self._add_btn.configure(state=state)
        self._entry.configure(state=state)
        self._update_btn.configure(state=state)

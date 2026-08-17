"""
Aristocrat Configurable Robot XML Builder
Compose, edit, load and save robot.xml configuration files via a Windows GUI.
Usage: python main.py
"""
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, scrolledtext
import copy
import datetime
import json
from pathlib import Path
from typing import Optional

import snapshot_manager
from coordinate_picker import ProgressDialog
from email_dialog import EmailDialog
from models import RobotNode
import xml_io
from tree_panel import TreePanel
from properties_panel import PropertiesPanel

RECENT_FILE    = Path.home() / ".robot_config_builder_recent.json"
MAX_RECENT     = 5
APP_TITLE      = "Robot Config Builder"
DEFAULT_XML    = Path(__file__).parent / "default.xml"
TEMPLATES_DIR  = Path(__file__).parent / "templates"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("1300x760")
        self.minsize(900, 520)

        self.root_node: Optional[RobotNode] = None
        self.current_file: Optional[str] = None
        self.modified: bool = False
        self.undo_stack = []          # list of deepcopy snapshots
        self._recent: list = self._load_recent()

        self._build_ui()
        self._bind_keys()
        if not self._load_default():
            self.after(0, self.destroy)

    # ═══ UI Build ══════════════════════════════════════════════

    def _build_ui(self):
        self._build_menu()
        self._build_toolbar()
        self._build_main_area()
        self._build_status_bar()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_menu(self):
        menubar = tk.Menu(self)
        self.configure(menu=menubar)

        # File
        self._file_menu = fm = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="File", menu=fm)
        fm.add_command(label="New\t\tCtrl+N",        command=self.action_new)
        fm.add_command(label="Open...\t\tCtrl+O",    command=self.action_open)
        fm.add_separator()
        fm.add_command(label="Save\t\tCtrl+S",       command=self.action_save)
        fm.add_command(label="Save As...\tCtrl+Shift+S", command=self.action_save_as)
        fm.add_separator()
        fm.add_command(label="Save as Default",       command=self.action_save_as_default)
        fm.add_separator()
        fm.add_command(label="Exit",                  command=self._on_close)
        self._rebuild_recent_menu()

        # Edit
        em = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Edit", menu=em)
        em.add_command(label="Undo\t\tCtrl+Z",       command=self.action_undo)
        em.add_separator()
        em.add_command(label="Duplicate\tCtrl+D",
                       command=lambda: self.tree_panel.duplicate_selected())
        em.add_command(label="Delete\t\tDel",
                       command=lambda: self.tree_panel.delete_selected())
        em.add_separator()
        em.add_command(label="Move Up\tCtrl+↑",
                       command=lambda: self.tree_panel.move_selected(-1))
        em.add_command(label="Move Down\tCtrl+↓",
                       command=lambda: self.tree_panel.move_selected(1))

        # Templates — loaded from templates/ folder
        tm = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Templates", menu=tm)
        if TEMPLATES_DIR.exists():
            xml_files = sorted(TEMPLATES_DIR.glob("*.xml"))
            for f in xml_files:
                tm.add_command(label=f.stem,
                               command=lambda fp=f: self._ask_load_template_file(fp))
            if xml_files:
                tm.add_separator()
        tm.add_command(label="Browse...", command=self._browse_template_file)

        # Help
        hm = tk.Menu(menubar, tearoff=0)
        menubar.add_cascade(label="Help", menu=hm)
        hm.add_command(label="About", command=self._show_about)

    def _build_toolbar(self):
        tb = ttk.Frame(self, relief=tk.FLAT)
        tb.pack(side=tk.TOP, fill=tk.X, padx=4, pady=2)

        def btn(text, cmd):
            b = ttk.Button(tb, text=text, command=cmd, width=max(len(text) + 2, 7))
            b.pack(side=tk.LEFT, padx=1)

        def sep():
            ttk.Separator(tb, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=4, pady=2)

        btn("New",    self.action_new)
        btn("Open",   self.action_open)
        btn("Save",   self.action_save)
        sep()
        btn("Undo",   self.action_undo)
        sep()
        btn("↑ Up",   lambda: self.tree_panel.move_selected(-1))
        btn("↓ Down", lambda: self.tree_panel.move_selected(1))
        btn("Dup",    lambda: self.tree_panel.duplicate_selected())
        btn("Delete", lambda: self.tree_panel.delete_selected())
        sep()
        btn("Email",  self._open_email_dialog)

        self._build_machine_bar()

    def _build_machine_bar(self):
        """Persistent game-machine settings bar — always visible below the main toolbar."""
        _s = snapshot_manager.load_settings()
        self._machine_ip_var         = tk.StringVar(value=_s.get("ip", ""))
        self._machine_or_var         = tk.StringVar(
            value=_s.get("orientation", "landscape").capitalize())
        self._machine_build_path_var = tk.StringVar(value=_s.get("build_path", ""))
        self._machine_status_var     = tk.StringVar(value="")

        bar = ttk.Frame(self, relief=tk.GROOVE, borderwidth=1)
        bar.pack(side=tk.TOP, fill=tk.X, padx=4, pady=(0, 2))

        ttk.Label(bar, text="Game Machine:", padding=(4, 0)).pack(side=tk.LEFT)

        ttk.Label(bar, text="IP:").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self._machine_ip_var, width=18).pack(
            side=tk.LEFT, padx=(2, 8))

        ttk.Label(bar, text="Screen:").pack(side=tk.LEFT)
        ttk.Combobox(
            bar, textvariable=self._machine_or_var,
            values=["Portrait", "Landscape"],
            state="readonly", width=11,
        ).pack(side=tk.LEFT, padx=(2, 8))

        ttk.Label(bar, text="Build Path:").pack(side=tk.LEFT)
        ttk.Entry(bar, textvariable=self._machine_build_path_var, width=28).pack(
            side=tk.LEFT, padx=(2, 8))

        ttk.Button(bar, text="Refresh Screenshot",
                   command=self._refresh_screenshot).pack(side=tk.LEFT)

        ttk.Label(bar, textvariable=self._machine_status_var,
                  foreground="#666", padding=(8, 0)).pack(side=tk.LEFT)

        def _persist(*_):
            snapshot_manager.save_settings(
                self._machine_ip_var.get(),
                self._machine_or_var.get().lower(),
                self._machine_build_path_var.get(),
            )
            self._update_machine_status()

        self._machine_ip_var.trace_add("write", _persist)
        self._machine_or_var.trace_add("write", _persist)
        self._machine_build_path_var.trace_add("write", _persist)
        self._update_machine_status()

    def _update_machine_status(self):
        ip = self._machine_ip_var.get().strip()
        if not ip:
            self._machine_status_var.set("No IP set")
            return
        path = snapshot_manager.get_cached_path(ip)
        if path.exists():
            mtime = path.stat().st_mtime
            dt = datetime.datetime.fromtimestamp(mtime)
            self._machine_status_var.set(f"Cached: {dt.strftime('%H:%M:%S')}")
        else:
            self._machine_status_var.set("No screenshot cached")

    def _refresh_screenshot(self):
        ip = self._machine_ip_var.get().strip()
        orientation = self._machine_or_var.get()
        if not ip:
            messagebox.showwarning("Machine Settings",
                                   "Enter the game machine IP address first.")
            return
        dlg = ProgressDialog(self, f"Capturing screenshot from {ip} ({orientation})...")

        def _done(_):
            dlg.destroy()
            self._update_machine_status()

        def _error(msg):
            dlg.destroy()
            messagebox.showerror("Screenshot Error", msg)

        snapshot_manager.take_screenshot_async(ip, orientation, self, _done, _error)

    def _build_main_area(self):
        paned = ttk.PanedWindow(self, orient=tk.HORIZONTAL)
        paned.pack(fill=tk.BOTH, expand=True, padx=4, pady=(0, 2))

        # Left pane — Event Tree
        tree_frame = ttk.LabelFrame(paned, text="Event Tree")
        self.tree_panel = TreePanel(
            tree_frame,
            on_node_selected=self._on_node_selected,
            on_tree_changed=self._on_tree_changed,
        )
        self.tree_panel._push_undo = self._push_undo
        self.tree_panel.pack(fill=tk.BOTH, expand=True)
        paned.add(tree_frame, weight=1)

        # Center pane — Properties
        props_frame = ttk.LabelFrame(paned, text="Properties")
        self.props_panel = PropertiesPanel(
            props_frame,
            on_property_changed=self._on_property_changed,
        )
        self.props_panel.pack(fill=tk.BOTH, expand=True)
        paned.add(props_frame, weight=2)

        # Right pane — XML Preview
        xml_frame = ttk.LabelFrame(paned, text="XML Preview (read-only)")
        self.xml_preview = scrolledtext.ScrolledText(
            xml_frame,
            wrap=tk.NONE,
            font=("Courier New", 9),
            state=tk.DISABLED,
            bg="#1e1e1e",
            fg="#d4d4d4",
            insertbackground="#d4d4d4",
        )
        self.xml_preview.pack(fill=tk.BOTH, expand=True)
        # Add horizontal scrollbar for wide XML lines
        hsb = ttk.Scrollbar(xml_frame, orient=tk.HORIZONTAL,
                             command=self.xml_preview.xview)
        hsb.pack(side=tk.BOTTOM, fill=tk.X)
        self.xml_preview.configure(xscrollcommand=hsb.set)
        paned.add(xml_frame, weight=2)

    def _build_status_bar(self):
        self._status_var = tk.StringVar(value="  Ready — select a node or load a template")
        ttk.Label(self, textvariable=self._status_var,
                  relief=tk.SUNKEN, anchor=tk.W).pack(side=tk.BOTTOM, fill=tk.X)

    # ═══ Key bindings ══════════════════════════════════════════

    def _bind_keys(self):
        self.bind("<Control-n>",    lambda e: self.action_new())
        self.bind("<Control-o>",    lambda e: self.action_open())
        self.bind("<Control-s>",    lambda e: self.action_save())
        self.bind("<Control-S>",    lambda e: self.action_save_as())
        self.bind("<Control-z>",    lambda e: self.action_undo())
        self.bind("<Control-d>",    lambda e: self.tree_panel.duplicate_selected())
        self.bind("<Control-Up>",   lambda e: self.tree_panel.move_selected(-1))
        self.bind("<Control-Down>", lambda e: self.tree_panel.move_selected(1))
        self.bind("<Delete>",       self._on_delete_key)
        self.bind("<F5>",           lambda e: self._refresh_xml_preview())

    def _on_delete_key(self, event):
        # Only delete if the tree widget has keyboard focus
        if self.focus_get() is self.tree_panel.tree:
            self.tree_panel.delete_selected()

    # ═══ Event Callbacks ═══════════════════════════════════════

    def _on_node_selected(self, node: RobotNode):
        self.props_panel.render_for_node(node)
        label = node.node_type + (f"  ({node.id})" if node.id else "")
        self._set_status(f"Selected: {label}")

    def _on_tree_changed(self):
        self.modified = True
        self._refresh_xml_preview()
        self._update_title()

    def _on_property_changed(self):
        node = self.tree_panel.selected_node()
        if node:
            self.tree_panel.update_node_display(node)
        self.modified = True
        self._refresh_xml_preview()
        self._update_title()

    # ═══ Undo ══════════════════════════════════════════════════

    def _push_undo(self):
        if self.root_node is None:
            return
        self.undo_stack.append(copy.deepcopy(self.root_node))
        if len(self.undo_stack) > 20:
            self.undo_stack.pop(0)

    def action_undo(self):
        if not self.undo_stack:
            self._set_status("Nothing to undo.")
            return
        self.root_node = self.undo_stack.pop()
        self.tree_panel.set_root(self.root_node)
        self.props_panel.render_for_node(self.root_node)
        self._refresh_xml_preview()
        self.modified = True
        self._update_title()
        self._set_status("Undone.")

    # ═══ File Operations ═══════════════════════════════════════

    def action_new(self):
        if not self._confirm_discard():
            return
        if not self._load_default():
            return
        self.current_file = None
        self.modified = False
        self.undo_stack.clear()
        self._update_title()
        self._set_status("New configuration loaded from default.xml.")

    def action_open(self):
        if not self._confirm_discard():
            return
        path = filedialog.askopenfilename(
            title="Open Robot XML",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
            initialdir=str(Path(self.current_file).parent)
                       if self.current_file else str(Path.home()),
        )
        if path:
            self._open_file(path)

    def _open_file(self, path: str):
        try:
            node = xml_io.parse_xml_file(path)
        except Exception as exc:
            messagebox.showerror("Open Error", f"Failed to parse XML:\n{exc}")
            return
        self.root_node = node
        self.current_file = path
        self.modified = False
        self.undo_stack.clear()
        self.tree_panel.set_root(self.root_node)
        self.props_panel.render_for_node(self.root_node)
        self._refresh_xml_preview()
        self._update_title()
        self._add_recent(path)
        self._set_status(f"Opened: {path}")

    def action_save(self):
        if self.current_file is None:
            self.action_save_as()
        else:
            self._save_to(self.current_file)

    def action_save_as(self):
        initial = (str(Path(self.current_file).parent)
                   if self.current_file else str(Path.home()))
        path = filedialog.asksaveasfilename(
            title="Save Robot XML",
            defaultextension=".xml",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
            initialdir=initial,
        )
        if path:
            self.current_file = path
            self._save_to(path)

    def _save_to(self, path: str):
        if self.root_node is None:
            return
        errors = xml_io.validate_tree(self.root_node)
        if errors:
            msg = "Validation warnings (file will still be saved):\n\n" + "\n".join(errors)
            messagebox.showwarning("Validation Warnings", msg)
        try:
            xml_text = xml_io.generate_xml(self.root_node)
            Path(path).write_text(xml_text, encoding='utf-8')
            self.modified = False
            self._update_title()
            self._add_recent(path)
            self._set_status(f"Saved: {path}")
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to save:\n{exc}")

    # ═══ Default & Templates ═══════════════════════════════════

    def _load_default(self) -> bool:
        """Load default.xml. Returns False (and shows error) if file is missing or corrupt."""
        if not DEFAULT_XML.exists():
            messagebox.showerror(
                "Missing default.xml",
                f"default.xml was not found in the application folder:\n{DEFAULT_XML}\n\n"
                "Please restore it from the installation package.",
            )
            return False
        try:
            node = xml_io.parse_xml_file(str(DEFAULT_XML))
            self._load_template(node)
            return True
        except Exception as exc:
            messagebox.showerror(
                "Corrupt default.xml",
                f"Failed to load default.xml:\n{exc}\n\n"
                "Please restore it from the installation package.",
            )
            return False

    def action_save_as_default(self):
        """Overwrite default.xml with the current configuration."""
        if self.root_node is None:
            return
        if not messagebox.askyesno(
            "Save as Default",
            "Overwrite default.xml with the current configuration?\n\n"
            "This becomes the starting point every time you click New.",
            parent=self,
        ):
            return
        try:
            xml_text = xml_io.generate_xml(self.root_node)
            DEFAULT_XML.write_text(xml_text, encoding='utf-8')
            self._set_status("Saved as default.xml")
        except Exception as exc:
            messagebox.showerror("Save Error", f"Failed to write default.xml:\n{exc}")

    def _ask_load_template_file(self, path: Path):
        if self.modified:
            if not messagebox.askyesno("Load Template",
                                        "Unsaved changes will be lost. Continue?"):
                return
        try:
            node = xml_io.parse_xml_file(str(path))
        except Exception as exc:
            messagebox.showerror("Template Error", f"Failed to load template:\n{exc}")
            return
        self._load_template(node)
        self.current_file = None
        self.modified = False
        self.undo_stack.clear()
        self._update_title()
        self._set_status(f"Template loaded: {path.stem}")

    def _browse_template_file(self):
        initial = str(TEMPLATES_DIR) if TEMPLATES_DIR.exists() else str(Path.home())
        path = filedialog.askopenfilename(
            title="Open Template",
            filetypes=[("XML files", "*.xml"), ("All files", "*.*")],
            initialdir=initial,
        )
        if path:
            self._ask_load_template_file(Path(path))

    def _load_template(self, root: RobotNode):
        self.root_node = root
        self.tree_panel.set_root(self.root_node)
        self.props_panel.render_for_node(self.root_node)
        self._refresh_xml_preview()

    # ═══ XML Preview ═══════════════════════════════════════════

    def _refresh_xml_preview(self):
        if self.root_node is None:
            return
        try:
            xml_text = xml_io.generate_xml(self.root_node)
        except Exception as exc:
            xml_text = f"<!-- Error generating XML:\n{exc} -->"
        self.xml_preview.configure(state=tk.NORMAL)
        self.xml_preview.delete("1.0", tk.END)
        self.xml_preview.insert("1.0", xml_text)
        self._apply_xml_highlight()
        self.xml_preview.configure(state=tk.DISABLED)

    def _apply_xml_highlight(self):
        """Basic syntax highlighting on the XML preview."""
        import re
        txt = self.xml_preview

        # Tags
        txt.tag_configure('tag',     foreground='#569cd6')
        txt.tag_configure('attr',    foreground='#9cdcfe')
        txt.tag_configure('value',   foreground='#ce9178')
        txt.tag_configure('comment', foreground='#6a9955')

        content = txt.get("1.0", tk.END)

        def mark(pattern, tag):
            for m in re.finditer(pattern, content):
                s = f"1.0+{m.start()}c"
                e = f"1.0+{m.end()}c"
                txt.tag_add(tag, s, e)

        mark(r'<!--.*?-->', 'comment')
        mark(r'</?\w[\w-]*', 'tag')
        mark(r'\b[\w-]+=', 'attr')
        mark(r'"[^"]*"', 'value')

    # ═══ UI Helpers ════════════════════════════════════════════

    def _update_title(self):
        dirty = "*" if self.modified else ""
        fname = Path(self.current_file).name if self.current_file else "Untitled"
        self.title(f"{dirty}{fname} — {APP_TITLE}")

    def _set_status(self, msg: str):
        self._status_var.set(f"  {msg}")

    def _confirm_discard(self) -> bool:
        if not self.modified:
            return True
        answer = messagebox.askyesnocancel("Unsaved Changes",
                                            "Save changes before proceeding?")
        if answer is None:
            return False      # cancel
        if answer:
            self.action_save()
        return True

    def _on_close(self):
        if self._confirm_discard():
            self.destroy()

    def _open_email_dialog(self):
        if not self._machine_ip_var.get().strip():
            messagebox.showwarning(
                "IP Not Configured",
                "Please configure the Game Machine IP address first.",
            )
            return
        EmailDialog(self, self._machine_ip_var, self._machine_build_path_var)

    def _show_about(self):
        messagebox.showinfo(
            "About Robot Config Builder",
            "Aristocrat Configurable Robot XML Builder\n\n"
            "Compose, edit, load and save robot.xml files for the\n"
            "Aristocrat Configurable Robot test framework.\n\n"
            "Based on Confluence documentation and 18 sample XML files\n"
            "from the IDEA_ConfiguableRobot folder.\n\n"
            "Technology: Python + tkinter (no extra dependencies)",
        )

    # ═══ Recent Files ══════════════════════════════════════════

    def _load_recent(self) -> list:
        try:
            if RECENT_FILE.exists():
                return json.loads(RECENT_FILE.read_text()).get('recent', [])
        except Exception:
            pass
        return []

    def _save_recent_file(self):
        try:
            RECENT_FILE.write_text(json.dumps({'recent': self._recent}))
        except Exception:
            pass

    def _add_recent(self, path: str):
        if path in self._recent:
            self._recent.remove(path)
        self._recent.insert(0, path)
        self._recent = self._recent[:MAX_RECENT]
        self._save_recent_file()
        self._rebuild_recent_menu()

    def _rebuild_recent_menu(self):
        # The fixed entries occupy indices 0-6 (New, Open, sep, Save, SaveAs, sep, Exit).
        # Remove everything after index 6, then re-add recent block if any.
        try:
            last = self._file_menu.index(tk.END)
            if last is not None and last > 6:
                self._file_menu.delete(7, tk.END)
        except (tk.TclError, TypeError):
            pass
        if self._recent:
            self._file_menu.add_separator()
            for p in self._recent:
                name = Path(p).name
                self._file_menu.add_command(
                    label=f"  {name}",
                    command=lambda fp=p: self._open_recent(fp),
                )

    def _open_recent(self, path: str):
        if not Path(path).exists():
            messagebox.showerror("File Not Found",
                                  f"Could not find:\n{path}\n\nIt will be removed from the list.")
            self._recent = [p for p in self._recent if p != path]
            self._save_recent_file()
            self._rebuild_recent_menu()
            return
        if self._confirm_discard():
            self._open_file(path)


if __name__ == "__main__":
    app = App()
    app.mainloop()

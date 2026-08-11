import tkinter as tk
from tkinter import ttk, simpledialog, messagebox
import copy
from typing import Optional, Callable

from models import (
    RobotNode, CONTAINER_TYPES, TAG_COLORS, classify_tag, display_label,
)

# Event type groups for context-menu submenus
EVENT_GROUPS = [
    ("Containers",  ['Sequence', 'Random', 'Condition', 'Scheduled', 'Simultaneous']),
    ("Touch",       ['Touch-Screen', 'Touch-Area', 'Swipe-Screen']),
    ("Actions",     ['Button', 'Wait']),
    ("Credits",     ['Insert-Credit', 'Clear-Jackpot', 'Random-Credit']),
    ("Hardware",    ['Door', 'Switch']),
    ("Special",     ['meter-list', 'output']),
]


class TreePanel(ttk.Frame):
    def __init__(self, parent,
                 on_node_selected: Optional[Callable] = None,
                 on_tree_changed: Optional[Callable] = None,
                 **kw):
        super().__init__(parent, **kw)
        self.on_node_selected: Callable = on_node_selected or (lambda n: None)
        self.on_tree_changed: Callable = on_tree_changed or (lambda: None)
        self.node_map: dict = {}          # iid → RobotNode
        self.root_node: Optional[RobotNode] = None
        # Injected by App so TreePanel can snapshot before mutations
        self._push_undo: Callable = lambda: None
        self._build_ui()

    def _build_ui(self):
        self.tree = ttk.Treeview(
            self,
            columns=("detail",),
            show="tree headings",
            selectmode="browse",
        )
        self.tree.heading("#0", text="Event")
        self.tree.heading("detail", text="Key Info")
        self.tree.column("#0", width=175, minwidth=120, stretch=False)
        self.tree.column("detail", width=230, minwidth=80, stretch=True)

        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)

        for tag, color in TAG_COLORS.items():
            self.tree.tag_configure(tag, foreground=color)

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>", self._on_right_click)

        self._ctx = tk.Menu(self, tearoff=0)

    # ── Public API ─────────────────────────────────────────────

    def set_root(self, root: Optional[RobotNode]):
        self.root_node = root
        self.refresh_tree()

    def refresh_tree(self, select_node: Optional[RobotNode] = None):
        self.tree.delete(*self.tree.get_children())
        self.node_map.clear()
        if self.root_node:
            self._insert_node(self.root_node, "")
        if select_node:
            iid = str(id(select_node))
            if self.tree.exists(iid):
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self.on_node_selected(select_node)

    def update_node_display(self, node: RobotNode):
        """Refresh a single item's text/detail columns without rebuilding."""
        iid = str(id(node))
        if self.tree.exists(iid):
            text, detail = display_label(node)
            self.tree.item(iid, text=text, values=(detail,))

    def selected_node(self) -> Optional[RobotNode]:
        iid = self._selected_iid()
        return self.node_map.get(iid) if iid else None

    # ── Internal helpers ────────────────────────────────────────

    def _insert_node(self, node: RobotNode, parent_iid: str):
        text, detail = display_label(node)
        tag = classify_tag(node.node_type)
        iid = str(id(node))
        self.tree.insert(parent_iid, "end", iid=iid,
                         text=text, values=(detail,), tags=(tag,))
        self.node_map[iid] = node
        for child in node.children:
            self._insert_node(child, iid)

    def _selected_iid(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _parent_node(self, node: RobotNode) -> Optional[RobotNode]:
        for n in self.node_map.values():
            if node in n.children:
                return n
        return None

    # ── Selection ───────────────────────────────────────────────

    def _on_select(self, _event=None):
        iid = self._selected_iid()
        if iid and iid in self.node_map:
            self.on_node_selected(self.node_map[iid])

    # ── Context menu ────────────────────────────────────────────

    def _on_right_click(self, event):
        row = self.tree.identify_row(event.y)
        if row:
            self.tree.selection_set(row)

        node = self.selected_node()
        if not node:
            return

        m = self._ctx
        m.delete(0, tk.END)

        if node.node_type in CONTAINER_TYPES:
            child_menu = tk.Menu(m, tearoff=0)
            for group_name, types in EVENT_GROUPS:
                grp = tk.Menu(child_menu, tearoff=0)
                for etype in types:
                    grp.add_command(label=etype,
                                    command=lambda et=etype: self._add_child(et))
                child_menu.add_cascade(label=group_name, menu=grp)
            m.add_cascade(label="Add Child", menu=child_menu)

        if node is not self.root_node:
            sib_menu = tk.Menu(m, tearoff=0)
            for group_name, types in EVENT_GROUPS:
                grp = tk.Menu(sib_menu, tearoff=0)
                for etype in types:
                    grp.add_command(label=etype,
                                    command=lambda et=etype: self._add_sibling(et))
                sib_menu.add_cascade(label=group_name, menu=grp)
            m.add_cascade(label="Add Sibling After", menu=sib_menu)

        m.add_separator()
        m.add_command(label="Duplicate  Ctrl+D", command=self.duplicate_selected)
        m.add_command(label="Delete  Del", command=self.delete_selected,
                      state=tk.DISABLED if node is self.root_node else tk.NORMAL)
        m.add_separator()
        m.add_command(label="Move Up  Ctrl+↑",
                      command=lambda: self.move_selected(-1))
        m.add_command(label="Move Down  Ctrl+↓",
                      command=lambda: self.move_selected(1))
        m.add_separator()
        m.add_command(label="Edit Comment...", command=self._edit_comment)

        try:
            m.tk_popup(event.x_root, event.y_root)
        finally:
            m.grab_release()

    # ── Mutations ───────────────────────────────────────────────

    def _add_child(self, event_type: str):
        node = self.selected_node()
        if not node or node.node_type not in CONTAINER_TYPES:
            return
        self._push_undo()
        new_node = RobotNode.new(event_type)
        node.children.append(new_node)
        self.refresh_tree(select_node=new_node)
        self.on_tree_changed()

    def _add_sibling(self, event_type: str):
        node = self.selected_node()
        if not node or node is self.root_node:
            return
        parent = self._parent_node(node)
        if not parent:
            return
        self._push_undo()
        idx = parent.children.index(node)
        new_node = RobotNode.new(event_type)
        parent.children.insert(idx + 1, new_node)
        self.refresh_tree(select_node=new_node)
        self.on_tree_changed()

    def duplicate_selected(self):
        node = self.selected_node()
        if not node or node is self.root_node:
            return
        parent = self._parent_node(node)
        if not parent:
            return
        self._push_undo()
        new_node = copy.deepcopy(node)
        idx = parent.children.index(node)
        parent.children.insert(idx + 1, new_node)
        self.refresh_tree(select_node=new_node)
        self.on_tree_changed()

    def delete_selected(self):
        node = self.selected_node()
        if not node:
            return
        if node is self.root_node:
            messagebox.showwarning("Cannot Delete", "The root Sequence cannot be deleted.")
            return
        parent = self._parent_node(node)
        if not parent:
            return
        if not messagebox.askyesno("Delete Node",
                                    f"Delete '{node.node_type}' and all its children?"):
            return
        self._push_undo()
        parent.children.remove(node)
        self.refresh_tree()
        self.on_tree_changed()

    def move_selected(self, direction: int):
        node = self.selected_node()
        if not node or node is self.root_node:
            return
        parent = self._parent_node(node)
        if not parent:
            return
        idx = parent.children.index(node)
        new_idx = idx + direction
        if 0 <= new_idx < len(parent.children):
            self._push_undo()
            parent.children.pop(idx)
            parent.children.insert(new_idx, node)
            self.refresh_tree(select_node=node)
            self.on_tree_changed()

    def _edit_comment(self):
        node = self.selected_node()
        if not node:
            return
        result = simpledialog.askstring(
            "Edit Comment",
            "Comment text (leave blank to remove):",
            initialvalue=node.comment,
            parent=self,
        )
        if result is not None:
            node.comment = result.strip()
            self.update_node_display(node)
            self.on_tree_changed()

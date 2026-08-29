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
        # Drop-into highlight (tag bg may be ignored on native Windows ttk themes,
        # but the drop line below always shows regardless)
        self.tree.tag_configure("drop_into", background="#7457C4", foreground="#FFFFFF")

        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        self.tree.bind("<Button-3>",         self._on_right_click)
        self.tree.bind("<ButtonPress-1>",    self._drag_start,   add="+")
        self.tree.bind("<B1-Motion>",        self._drag_motion,  add="+")
        self.tree.bind("<ButtonRelease-1>",  self._drag_release, add="+")
        self.tree.bind("<Escape>",           self._drag_cancel,  add="+")

        # 2-pixel drop indicator line (placed over the treeview during drag)
        self._drop_line = tk.Frame(self, height=2, bg="#5B3EA6")

        # Drag / drop state
        self._drag_src_iid: Optional[str]      = None
        self._drag_src_node: Optional[RobotNode] = None
        self._drop_target_iid: Optional[str]   = None
        self._drop_position: Optional[str]     = None  # "before" | "into" | "after"

        self._ctx = tk.Menu(self, tearoff=0)

    # ── Public API ─────────────────────────────────────────────

    def set_root(self, root: Optional[RobotNode]):
        self.root_node = root
        self.refresh_tree()

    def refresh_tree(self, select_node: Optional[RobotNode] = None):
        expanded_iids = self._expanded_iids()
        self.tree.delete(*self.tree.get_children())
        self.node_map.clear()
        if self.root_node:
            self._insert_node(self.root_node, "")
        self._restore_expanded_iids(expanded_iids)
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

    def _expanded_iids(self) -> set:
        expanded = set()

        def walk(iid: str):
            if self.tree.item(iid, "open"):
                expanded.add(iid)
            for child_iid in self.tree.get_children(iid):
                walk(child_iid)

        for root_iid in self.tree.get_children():
            walk(root_iid)
        return expanded

    def _restore_expanded_iids(self, expanded_iids: set):
        for iid in expanded_iids:
            if self.tree.exists(iid):
                self.tree.item(iid, open=True)

    def _selected_iid(self) -> Optional[str]:
        sel = self.tree.selection()
        return sel[0] if sel else None

    def _parent_node(self, node: RobotNode) -> Optional[RobotNode]:
        for n in self.node_map.values():
            # RobotNode is a dataclass, so ``in``/``list.index`` use value
            # equality.  Fresh default nodes and duplicated subtrees often
            # compare equal even though they are different tree rows.  Tree
            # mutations must always follow the exact object represented by
            # the Treeview iid.
            if any(child is node for child in n.children):
                return n
        return None

    @staticmethod
    def _child_index(parent: RobotNode, node: RobotNode) -> Optional[int]:
        """Return node's identity-based index, or None if it is not a child."""
        for index, child in enumerate(parent.children):
            if child is node:
                return index
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
        idx = self._child_index(parent, node)
        if idx is None:
            return
        self._push_undo()
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
        idx = self._child_index(parent, node)
        if idx is None:
            return
        self._push_undo()
        new_node = copy.deepcopy(node)
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
        idx = self._child_index(parent, node)
        if idx is None:
            return
        self._push_undo()
        parent.children.pop(idx)
        self.refresh_tree()
        self.on_tree_changed()

    def move_selected(self, direction: int):
        node = self.selected_node()
        if not node or node is self.root_node:
            return
        parent = self._parent_node(node)
        if not parent:
            return
        idx = self._child_index(parent, node)
        if idx is None:
            return
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

    # ── Drag and drop ───────────────────────────────────────────

    def _drag_start(self, event):
        # A release outside the widget may leave state from the previous drag.
        # Always begin from a clean state so it cannot affect the next drop.
        self._clear_drop_indicator()
        self._drag_src_iid = None
        self._drag_src_node = None

        iid = self.tree.identify_row(event.y)
        if not iid:
            return
        node = self.node_map.get(iid)
        # Root cannot be dragged
        if node is None or node is self.root_node:
            return
        self._drag_src_iid = iid
        self._drag_src_node = node

    def _drag_motion(self, event):
        if not self._drag_src_iid:
            return

        target_iid = self.tree.identify_row(event.y)
        if not target_iid or target_iid == self._drag_src_iid:
            self._clear_drop_indicator()
            return

        target_node = self.node_map.get(target_iid)
        if target_node is None:
            self._clear_drop_indicator()
            return

        # Prevent dropping into own subtree
        if self._is_ancestor(self._drag_src_node, target_node):
            self._clear_drop_indicator()
            return

        bbox = self.tree.bbox(target_iid)
        if not bbox:
            self._clear_drop_indicator()
            return

        x, y, w, h = bbox
        rel = (event.y - y) / max(h, 1)   # 0.0 (top) → 1.0 (bottom)

        is_container = target_node.node_type in CONTAINER_TYPES
        if target_node is self.root_node:
            # Root has no siblings, so its entire row is an "into" target.
            pos = "into"
        elif is_container:
            if rel < 0.30:
                pos = "before"
            elif rel > 0.70:
                pos = "after"
            else:
                pos = "into"
        else:
            pos = "before" if rel < 0.5 else "after"

        self._show_drop_indicator(target_iid, pos, bbox)

    def _show_drop_indicator(self, target_iid: str, pos: str, bbox):
        self._clear_drop_indicator()
        self._drop_target_iid = target_iid
        self._drop_position   = pos

        x, y, w, h = bbox
        tx = self.tree.winfo_x()
        ty = self.tree.winfo_y()
        line_w = self.tree.winfo_width() - x  # stretch to tree edge

        if pos == "into":
            # Highlight the target row
            tags = list(self.tree.item(target_iid, "tags"))
            if "drop_into" not in tags:
                tags.append("drop_into")
                self.tree.item(target_iid, tags=tags)
            line_y = ty + y + h          # line just below the container row
        elif pos == "before":
            line_y = ty + y
        else:                            # "after"
            line_y = ty + y + h

        self._drop_line.place(x=tx + x, y=line_y - 1, width=line_w, height=2)
        self._drop_line.lift()

    def _clear_drop_indicator(self):
        if self._drop_target_iid and self.tree.exists(self._drop_target_iid):
            tags = list(self.tree.item(self._drop_target_iid, "tags"))
            if "drop_into" in tags:
                tags.remove("drop_into")
                self.tree.item(self._drop_target_iid, tags=tags)
        self._drop_target_iid = None
        self._drop_position   = None
        self._drop_line.place_forget()

    def _drag_release(self, event):
        # Resolve the release coordinates themselves.  On a fast movement the
        # last B1-Motion event can still refer to the previous row.
        self._drag_motion(event)

        src_iid  = self._drag_src_iid
        src_node = self._drag_src_node
        tgt_iid  = self._drop_target_iid
        pos      = self._drop_position

        self._clear_drop_indicator()
        self._drag_src_iid  = None
        self._drag_src_node = None

        if not src_iid or not tgt_iid or not pos:
            return

        tgt_node = self.node_map.get(tgt_iid)
        if tgt_node is None or tgt_node is src_node:
            return

        self._perform_drop(src_node, tgt_node, pos)

    def _drag_cancel(self, _event=None):
        self._clear_drop_indicator()
        self._drag_src_iid  = None
        self._drag_src_node = None

    def _perform_drop(self, src_node: RobotNode, tgt_node: RobotNode, pos: str):
        src_parent = self._parent_node(src_node)
        if not src_parent:
            return

        src_idx = self._child_index(src_parent, src_node)
        if src_idx is None:
            return

        if pos == "into":
            if tgt_node.node_type not in CONTAINER_TYPES:
                return
            if self._is_ancestor(src_node, tgt_node):
                return
            self._push_undo()
            src_parent.children.pop(src_idx)
            tgt_node.children.append(src_node)

        else:  # "before" or "after"
            if pos not in ("before", "after"):
                return
            tgt_parent = self._parent_node(tgt_node)
            if tgt_parent is None:
                return   # target is root — can't insert relative to root
            if self._is_ancestor(src_node, tgt_node):
                return
            tgt_idx = self._child_index(tgt_parent, tgt_node)
            if tgt_idx is None:
                return

            # Resolve both exact rows before removing anything.  When source
            # and target share a parent, account for the target shifting left
            # after the source is popped.
            insert_idx = tgt_idx if pos == "before" else tgt_idx + 1
            if src_parent is tgt_parent and src_idx < insert_idx:
                insert_idx -= 1

            # Dropping immediately before/after the current position is a
            # no-op; avoid creating an unnecessary undo snapshot.
            if src_parent is tgt_parent and insert_idx == src_idx:
                return

            self._push_undo()
            src_parent.children.pop(src_idx)
            tgt_parent.children.insert(insert_idx, src_node)

        self.refresh_tree(select_node=src_node)
        self.on_tree_changed()

    def _is_ancestor(self, ancestor: RobotNode, node: RobotNode) -> bool:
        """Return True if node lives anywhere inside ancestor's subtree."""
        for child in ancestor.children:
            if child is node or self._is_ancestor(child, node):
                return True
        return False

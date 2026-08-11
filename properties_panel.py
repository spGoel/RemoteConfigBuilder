import tkinter as tk
from tkinter import ttk, messagebox
from typing import Optional, Callable

import snapshot_manager
from coordinate_picker import CoordinatePicker, ProgressDialog
from models import RobotNode, ALL_METERS, BUTTON_KEYS


class PropertiesPanel(ttk.Frame):
    def __init__(self, parent,
                 on_property_changed: Optional[Callable] = None,
                 **kw):
        super().__init__(parent, **kw)
        self.on_property_changed: Callable = on_property_changed or (lambda: None)
        self._current_node: Optional[RobotNode] = None
        self._refresh_job = None
        # Keep tk vars alive (prevent GC while the form is displayed)
        self._vars: dict = {}

        self._build_ui()

    def _build_ui(self):
        self.canvas = tk.Canvas(self, borderwidth=0, highlightthickness=0)
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=vsb.set)
        vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self.inner = ttk.Frame(self.canvas)
        self._win_id = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")

        self.inner.bind("<Configure>", self._on_inner_cfg)
        self.canvas.bind("<Configure>", self._on_canvas_cfg)
        self.canvas.bind_all("<MouseWheel>", self._on_scroll)

    def _on_inner_cfg(self, _=None):
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas_cfg(self, event):
        self.canvas.itemconfig(self._win_id, width=event.width)

    def _on_scroll(self, event):
        self.canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

    # ── Public ─────────────────────────────────────────────────

    def render_for_node(self, node: Optional[RobotNode]):
        for w in self.inner.winfo_children():
            w.destroy()
        self._vars.clear()
        self._current_node = node
        if node is None:
            return

        ttk.Label(self.inner, text=f"  {node.node_type}",
                  font=("TkDefaultFont", 11, "bold")).pack(anchor="w", padx=8, pady=(8, 2))

        cf = ttk.LabelFrame(self.inner, text="Common Attributes")
        cf.pack(fill=tk.X, padx=8, pady=4)
        self._render_common(cf, node)

        ttk.Separator(self.inner, orient=tk.HORIZONTAL).pack(fill=tk.X, padx=8, pady=4)

        dispatch = {
            'Touch-Screen':  self._render_touch_screen,
            'Touch-Area':    self._render_touch_area,
            'Swipe-Screen':  self._render_swipe_screen,
            'Button':        self._render_button,
            'Wait':          self._render_wait,
            'Insert-Credit': self._render_insert_credit,
            'Door':          self._render_door,
            'Switch':        self._render_switch,
            'Random-Credit': self._render_random_credit,
            'Scheduled':     self._render_scheduled,
            'meter-list':    self._render_meter_list,
            'output':        self._render_output,
        }
        renderer = dispatch.get(node.node_type)
        if renderer:
            renderer(node)
        else:
            ttk.Label(self.inner, text="(children define behavior)",
                      foreground="#888").pack(padx=8, pady=8, anchor="w")

        self.inner.update_idletasks()
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self.canvas.yview_moveto(0)

    # ── Common fields ───────────────────────────────────────────

    def _render_common(self, parent, node: RobotNode):
        # ID
        r = ttk.Frame(parent); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="ID:", width=9).pack(side=tk.LEFT)
        v = tk.StringVar(value=node.id); self._vars['id'] = v
        ttk.Entry(r, textvariable=v).pack(side=tk.LEFT, fill=tk.X, expand=True)
        v.trace_add('write', lambda *_: self._write_field(node, 'id', v.get(), is_attr=False))

        # Weight
        r2 = ttk.Frame(parent); r2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r2, text="Weight:", width=9).pack(side=tk.LEFT)
        wv = tk.StringVar(value='' if node.weight is None else str(node.weight))
        self._vars['weight'] = wv
        ttk.Entry(r2, textvariable=wv, width=8).pack(side=tk.LEFT)
        ttk.Label(r2, text="  used inside Random/Scheduled",
                  foreground="#888").pack(side=tk.LEFT)

        def _weight_change(*_):
            raw = wv.get().strip()
            try:
                node.weight = int(raw) if raw else None
            except ValueError:
                pass
            self._schedule()
        wv.trace_add('write', _weight_change)

        # Comment
        r3 = ttk.Frame(parent); r3.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r3, text="Comment:", width=9).pack(side=tk.LEFT)
        cv = tk.StringVar(value=node.comment); self._vars['comment'] = cv
        ttk.Entry(r3, textvariable=cv).pack(side=tk.LEFT, fill=tk.X, expand=True)
        cv.trace_add('write', lambda *_: self._write_field(node, 'comment', cv.get(), is_attr=False))

    # ── Touch renderers ─────────────────────────────────────────

    def _render_touch_screen(self, node: RobotNode):
        if not node.points:
            node.points = [[0, 0]]
        lf = ttk.LabelFrame(self.inner, text="Touch Point")
        lf.pack(fill=tk.X, padx=8, pady=4)
        self._point_row(lf, node, 0, "Point:")

    def _render_touch_area(self, node: RobotNode):
        while len(node.points) < 2:
            node.points.append([0, 0])
        lf = ttk.LabelFrame(self.inner, text="Region (2 Points)")
        lf.pack(fill=tk.X, padx=8, pady=4)
        self._point_row(lf, node, 0, "Top-Left:")
        self._point_row(lf, node, 1, "Bot-Right:")

    def _render_swipe_screen(self, node: RobotNode):
        while len(node.points) < 2:
            node.points.append([0, 0])
        lf = ttk.LabelFrame(self.inner, text="Swipe (Start → End)")
        lf.pack(fill=tk.X, padx=8, pady=4)
        self._point_row(lf, node, 0, "Start:")
        self._point_row(lf, node, 1, "End:")

    def _point_row(self, parent, node: RobotNode, pi: int, label: str):
        r = ttk.Frame(parent); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text=label, width=10).pack(side=tk.LEFT)
        ttk.Label(r, text="X:").pack(side=tk.LEFT)
        xv = tk.StringVar(value=str(node.points[pi][0]))
        self._vars[f'pt{pi}x'] = xv
        ttk.Entry(r, textvariable=xv, width=7).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(r, text="Y:").pack(side=tk.LEFT)
        yv = tk.StringVar(value=str(node.points[pi][1]))
        self._vars[f'pt{pi}y'] = yv
        ttk.Entry(r, textvariable=yv, width=7).pack(side=tk.LEFT, padx=(0, 8))

        ttk.Button(
            r, text="Pick from Screen", width=16,
            command=lambda: self._pick_coordinate(xv, yv),
        ).pack(side=tk.LEFT)

        def _xch(*_, i=pi):
            try:
                node.points[i][0] = int(xv.get())
            except ValueError:
                pass
            self._schedule()

        def _ych(*_, i=pi):
            try:
                node.points[i][1] = int(yv.get())
            except ValueError:
                pass
            self._schedule()

        xv.trace_add('write', _xch)
        yv.trace_add('write', _ych)

    # ── Coordinate picker ────────────────────────────────────────

    def _pick_coordinate(self, xv: tk.StringVar, yv: tk.StringVar):
        """Pick button: use cached screenshot (if any) or capture fresh."""
        s = snapshot_manager.load_settings()
        ip = s.get("ip", "").strip()
        orientation = s.get("orientation", "landscape")

        if not ip:
            messagebox.showwarning(
                "Machine Settings",
                "Enter the game machine IP in the Game Machine bar at the top.",
                parent=self.winfo_toplevel(),
            )
            return

        cached = snapshot_manager.get_cached_path(ip)
        if cached.exists():
            self._open_picker(str(cached), xv, yv)
        else:
            self._do_screenshot(ip, orientation,
                                on_done=lambda path: self._open_picker(path, xv, yv))

    def _do_screenshot(self, ip: str, orientation: str, on_done):
        parent = self.winfo_toplevel()
        dlg = ProgressDialog(parent,
                              f"Capturing screenshot from {ip} ({orientation})...")

        def _done(path):
            dlg.destroy()
            on_done(path)

        def _error(msg):
            dlg.destroy()
            messagebox.showerror("Screenshot Error", msg, parent=parent)

        snapshot_manager.take_screenshot_async(ip, orientation, self, _done, _error)

    def _open_picker(self, image_path: str, xv: tk.StringVar, yv: tk.StringVar):
        def on_pick(rx, ry):
            xv.set(str(rx))
            yv.set(str(ry))
        CoordinatePicker(self.winfo_toplevel(), image_path, on_pick)

    # ── Action renderers ────────────────────────────────────────

    def _render_button(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Button")
        lf.pack(fill=tk.X, padx=8, pady=4)

        r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Key:", width=9).pack(side=tk.LEFT)
        kv = tk.StringVar(value=node.attrs.get('key', 'Play'))
        self._vars['key'] = kv
        ttk.Combobox(r, textvariable=kv, values=BUTTON_KEYS,
                     state='normal', width=18).pack(side=tk.LEFT)
        kv.trace_add('write', lambda *_: self._write_attr(node, 'key', kv.get()))

        r2 = ttk.Frame(lf); r2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r2, text="Value:", width=9).pack(side=tk.LEFT)
        vv = tk.StringVar(value=node.attrs.get('value', ''))
        self._vars['btn_val'] = vv
        ttk.Entry(r2, textvariable=vv, width=12).pack(side=tk.LEFT)
        ttk.Label(r2, text="  optional numeric", foreground="#888").pack(side=tk.LEFT)
        vv.trace_add('write', lambda *_: self._write_attr(node, 'value', vv.get()))

    def _render_wait(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Wait")
        lf.pack(fill=tk.X, padx=8, pady=4)

        r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Timeout:", width=10).pack(side=tk.LEFT)
        tv = tk.StringVar(value=node.attrs.get('timeout', '3'))
        self._vars['w_timeout'] = tv
        ttk.Entry(r, textvariable=tv, width=8).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(r, text="Units:").pack(side=tk.LEFT)
        uv = tk.StringVar(value=node.attrs.get('units', 'Seconds'))
        self._vars['w_units'] = uv
        ttk.Combobox(r, textvariable=uv, values=['Seconds', 'Minutes', 'ms'],
                     state='readonly', width=10).pack(side=tk.LEFT)
        tv.trace_add('write', lambda *_: self._write_attr(node, 'timeout', tv.get()))
        uv.trace_add('write', lambda *_: self._write_attr(node, 'units', uv.get()))

        r2 = ttk.Frame(lf); r2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r2, text="State:", width=10).pack(side=tk.LEFT)
        sv = tk.StringVar(value=node.attrs.get('state', ''))
        self._vars['w_state'] = sv
        ttk.Combobox(r2, textvariable=sv, values=['', 'Game-Idle'],
                     state='readonly', width=14).pack(side=tk.LEFT)
        ttk.Label(r2, text="  blank = fixed-time", foreground="#888").pack(side=tk.LEFT)
        sv.trace_add('write', lambda *_: self._write_attr(node, 'state', sv.get()))

    def _render_insert_credit(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Insert Credit")
        lf.pack(fill=tk.X, padx=8, pady=4)
        for label, key, default in [
            ("Value:", "value", "2048"),
            ("When Below:", "when_below", "512"),
        ]:
            r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
            ttk.Label(r, text=label, width=12).pack(side=tk.LEFT)
            v = tk.StringVar(value=node.attrs.get(key, default))
            self._vars[key] = v
            ttk.Entry(r, textvariable=v, width=10).pack(side=tk.LEFT)
            v.trace_add('write', lambda *_, k=key, var=v: self._write_attr(node, k, var.get()))

    def _render_door(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Door")
        lf.pack(fill=tk.X, padx=8, pady=4)

        r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Door:", width=8).pack(side=tk.LEFT)
        dv = tk.StringVar(value=node.attrs.get('door', 'Logic'))
        self._vars['door'] = dv
        ttk.Combobox(r, textvariable=dv, values=['Logic'],
                     state='normal', width=12).pack(side=tk.LEFT)
        dv.trace_add('write', lambda *_: self._write_attr(node, 'door', dv.get()))

        r2 = ttk.Frame(lf); r2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r2, text="Open:", width=8).pack(side=tk.LEFT)
        ov = tk.StringVar(value=node.attrs.get('open', 'True'))
        self._vars['door_open'] = ov
        ttk.Combobox(r2, textvariable=ov, values=['True', 'False'],
                     state='readonly', width=10).pack(side=tk.LEFT)
        ov.trace_add('write', lambda *_: self._write_attr(node, 'open', ov.get()))

    def _render_switch(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Switch")
        lf.pack(fill=tk.X, padx=8, pady=4)

        r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Switch #:", width=10).pack(side=tk.LEFT)
        sv = tk.StringVar(value=node.attrs.get('switch', '2'))
        self._vars['switch_n'] = sv
        ttk.Entry(r, textvariable=sv, width=6).pack(side=tk.LEFT)
        ttk.Label(r, text="  2 = audit", foreground="#888").pack(side=tk.LEFT)
        sv.trace_add('write', lambda *_: self._write_attr(node, 'switch', sv.get()))

        r2 = ttk.Frame(lf); r2.pack(fill=tk.X, padx=4, pady=2)
        offv = tk.BooleanVar(value=bool(node.attrs.get('off', '')))
        self._vars['switch_off'] = offv
        ttk.Checkbutton(r2, text="Turn Off (switch off=True)", variable=offv).pack(side=tk.LEFT)

        def _off_change(*_):
            node.attrs['off'] = 'True' if offv.get() else ''
            self._schedule()
        offv.trace_add('write', _off_change)

    def _render_random_credit(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Random Credit")
        lf.pack(fill=tk.X, padx=8, pady=4)
        r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Range:", width=8).pack(side=tk.LEFT)
        rv = tk.StringVar(value=node.attrs.get('range', '100'))
        self._vars['rc_range'] = rv
        ttk.Entry(r, textvariable=rv, width=8).pack(side=tk.LEFT)
        rv.trace_add('write', lambda *_: self._write_attr(node, 'range', rv.get()))

    def _render_scheduled(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Scheduled")
        lf.pack(fill=tk.X, padx=8, pady=4)
        r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Timeout:", width=10).pack(side=tk.LEFT)
        tv = tk.StringVar(value=node.attrs.get('timeout', '60'))
        self._vars['sched_t'] = tv
        ttk.Entry(r, textvariable=tv, width=8).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(r, text="Units:").pack(side=tk.LEFT)
        uv = tk.StringVar(value=node.attrs.get('units', 'Seconds'))
        self._vars['sched_u'] = uv
        ttk.Combobox(r, textvariable=uv, values=['Seconds', 'Minutes'],
                     state='readonly', width=10).pack(side=tk.LEFT)
        tv.trace_add('write', lambda *_: self._write_attr(node, 'timeout', tv.get()))
        uv.trace_add('write', lambda *_: self._write_attr(node, 'units', uv.get()))

    # ── Special renderers ───────────────────────────────────────

    def _render_output(self, node: RobotNode):
        lf = ttk.LabelFrame(self.inner, text="Output / Log")
        lf.pack(fill=tk.X, padx=8, pady=4)

        r = ttk.Frame(lf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Filename:", width=10).pack(side=tk.LEFT)
        fv = tk.StringVar(value=node.attrs.get('filename', 'robotlogs/eventsfile.txt'))
        self._vars['out_fn'] = fv
        ttk.Entry(r, textvariable=fv).pack(side=tk.LEFT, fill=tk.X, expand=True)
        fv.trace_add('write', lambda *_: self._write_attr(node, 'filename', fv.get()))

        r2 = ttk.Frame(lf); r2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r2, text="Append:", width=10).pack(side=tk.LEFT)
        av = tk.StringVar(value=node.attrs.get('append', 'False'))
        self._vars['out_app'] = av
        ttk.Combobox(r2, textvariable=av, values=['False', 'True'],
                     state='readonly', width=8).pack(side=tk.LEFT)
        av.trace_add('write', lambda *_: self._write_attr(node, 'append', av.get()))

    def _render_meter_list(self, node: RobotNode):
        # Settings section
        sf = ttk.LabelFrame(self.inner, text="Meter List Settings")
        sf.pack(fill=tk.X, padx=8, pady=4)

        r = ttk.Frame(sf); r.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r, text="Timeout:", width=10).pack(side=tk.LEFT)
        tv = tk.StringVar(value=node.attrs.get('timeout', '15'))
        self._vars['ml_t'] = tv
        ttk.Entry(r, textvariable=tv, width=6).pack(side=tk.LEFT, padx=(0, 8))
        ttk.Label(r, text="Units:").pack(side=tk.LEFT)
        uv = tk.StringVar(value=node.attrs.get('units', 'Seconds'))
        self._vars['ml_u'] = uv
        ttk.Combobox(r, textvariable=uv, values=['Seconds', 'Minutes'],
                     state='readonly', width=10).pack(side=tk.LEFT)
        tv.trace_add('write', lambda *_: self._write_attr(node, 'timeout', tv.get()))
        uv.trace_add('write', lambda *_: self._write_attr(node, 'units', uv.get()))

        r2 = ttk.Frame(sf); r2.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r2, text="Log File:", width=10).pack(side=tk.LEFT)
        fnv = tk.StringVar(value=node.attrs.get('output_filename', 'robotlogs/eventsfile.txt'))
        self._vars['ml_fn'] = fnv
        ttk.Entry(r2, textvariable=fnv).pack(side=tk.LEFT, fill=tk.X, expand=True)
        fnv.trace_add('write', lambda *_: self._write_attr(node, 'output_filename', fnv.get()))

        r3 = ttk.Frame(sf); r3.pack(fill=tk.X, padx=4, pady=2)
        ttk.Label(r3, text="Append:", width=10).pack(side=tk.LEFT)
        apv = tk.StringVar(value=node.attrs.get('output_append', 'False'))
        self._vars['ml_ap'] = apv
        ttk.Combobox(r3, textvariable=apv, values=['False', 'True'],
                     state='readonly', width=8).pack(side=tk.LEFT)
        apv.trace_add('write', lambda *_: self._write_attr(node, 'output_append', apv.get()))

        # Meters section
        mf = ttk.LabelFrame(self.inner, text="Meters (check to include)")
        mf.pack(fill=tk.X, padx=8, pady=4)

        btn_row = ttk.Frame(mf)
        btn_row.pack(fill=tk.X, padx=4, pady=(4, 2))

        selected = set(node.attrs.get('meters') or ALL_METERS)
        meter_vars: dict = {}

        def _select_all():
            for v in meter_vars.values():
                v.set(True)

        def _clear_all():
            for v in meter_vars.values():
                v.set(False)

        ttk.Button(btn_row, text="Select All", command=_select_all, width=11).pack(side=tk.LEFT, padx=2)
        ttk.Button(btn_row, text="Clear All",  command=_clear_all,  width=11).pack(side=tk.LEFT)

        grid = ttk.Frame(mf)
        grid.pack(fill=tk.X, padx=4, pady=4)
        cols = 3
        for i, meter in enumerate(ALL_METERS):
            bv = tk.BooleanVar(value=(meter in selected))
            meter_vars[meter] = bv
            self._vars[f'meter_{i}'] = bv

            def _meter_changed(*_, mv=meter_vars):
                node.attrs['meters'] = [m for m in ALL_METERS if mv[m].get()]
                self._schedule()

            bv.trace_add('write', _meter_changed)
            ttk.Checkbutton(grid, text=meter, variable=bv).grid(
                row=i // cols, column=i % cols, sticky="w", padx=4, pady=1)

    # ── Helpers ─────────────────────────────────────────────────

    def _write_field(self, node: RobotNode, field: str, value, is_attr: bool = True):
        if is_attr:
            node.attrs[field] = value
        else:
            setattr(node, field, value)
        self._schedule()

    def _write_attr(self, node: RobotNode, key: str, value):
        node.attrs[key] = value
        self._schedule()

    def _schedule(self):
        if self._refresh_job is not None:
            self.after_cancel(self._refresh_job)
        self._refresh_job = self.after(400, self.on_property_changed)

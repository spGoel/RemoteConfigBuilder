import sys
import tkinter as tk
from tkinter import messagebox
from pathlib import Path
from typing import Optional, Callable

# The shared style layer lives at the repository root.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

import customtkinter as ctk  # noqa: E402

from common import theme  # noqa: E402
from common.widgets import Card, font  # noqa: E402

import snapshot_manager  # noqa: E402
from coordinate_picker import CoordinatePicker, ProgressDialog  # noqa: E402
from models import RobotNode, ALL_METERS, BUTTON_KEYS, DOOR_NAMES, GAME_STATES  # noqa: E402

# Fixed label column widths (CTk pixels, before DPI scaling). The old ttk
# labels used character widths of 8-12; these keep the same alignment.
_LW_8 = 66
_LW_9 = 76
_LW_10 = 84
_LW_12 = 100


class PropertiesPanel(ctk.CTkFrame):
    def __init__(self, parent,
                 on_property_changed: Optional[Callable] = None,
                 **kw):
        kw.setdefault("fg_color", "transparent")
        kw.setdefault("corner_radius", 0)
        super().__init__(parent, **kw)
        self.on_property_changed: Callable = on_property_changed or (lambda: None)
        self._current_node: Optional[RobotNode] = None
        self._refresh_job = None
        # Keep tk vars alive (prevent GC while the form is displayed)
        self._vars: dict = {}

        self._build_ui()

    def _build_ui(self):
        # CTkScrollableFrame replaces the hand-rolled Canvas + inner frame +
        # scrollbar; it tracks the scroll region and mouse wheel itself.
        self.inner = ctk.CTkScrollableFrame(self, fg_color="transparent")
        self.inner.pack(fill=tk.BOTH, expand=True)
        # Kept for callers used to the old layout; this is the scrolling canvas.
        self.canvas = self.inner._parent_canvas

    def on_appearance_change(self, _mode: str = None):
        """Called after a Light/Dark switch; every widget here is CTk."""

    # ── Widget factories ────────────────────────────────────────

    def _row(self, parent, pady=2) -> ctk.CTkFrame:
        r = ctk.CTkFrame(parent, fg_color="transparent")
        r.pack(fill=tk.X, padx=4, pady=pady)
        return r

    def _section(self, title: str):
        card = Card(self.inner, title=title)
        card.pack(fill=tk.X, padx=8, pady=4)
        return card.body

    def _separator(self):
        ctk.CTkFrame(self.inner, height=1, corner_radius=0,
                     fg_color=theme.BORDER).pack(fill=tk.X, padx=8, pady=4)

    def _label(self, parent, text: str, width: int = None, muted: bool = False):
        kw = dict(text=text, anchor="w",
                  font=font("small") if muted else font("body"))
        if muted:
            kw["text_color"] = theme.MUTED_FG
        if width is not None:
            kw["width"] = width
        return ctk.CTkLabel(parent, **kw)

    def _entry(self, parent, variable, width: int = None) -> ctk.CTkEntry:
        kw = dict(textvariable=variable, font=font("body"), height=28)
        if width is not None:
            kw["width"] = width
        return ctk.CTkEntry(parent, **kw)

    def _combo(self, parent, variable, values, width: int,
               readonly: bool = True) -> ctk.CTkComboBox:
        return ctk.CTkComboBox(
            parent, variable=variable, values=list(values), width=width, height=28,
            font=font("body"), dropdown_font=font("body"),
            state="readonly" if readonly else "normal",
            button_color=theme.ACCENT, button_hover_color=theme.ACCENT_HOVER,
            border_color=theme.BORDER,
        )

    def _check(self, parent, text: str, variable, command=None) -> ctk.CTkCheckBox:
        return ctk.CTkCheckBox(
            parent, text=text, variable=variable, command=command,
            font=font("body"), checkbox_width=20, checkbox_height=20,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        )

    def _radio(self, parent, text: str, variable, value, command=None) -> ctk.CTkRadioButton:
        return ctk.CTkRadioButton(
            parent, text=text, variable=variable, value=value, command=command,
            font=font("body"), radiobutton_width=18, radiobutton_height=18,
            fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        )

    def _button(self, parent, text: str, command, width: int) -> ctk.CTkButton:
        return ctk.CTkButton(
            parent, text=text, command=command, width=width, height=28,
            font=font("body"), fg_color=theme.ACCENT, hover_color=theme.ACCENT_HOVER,
        )

    # ── Public ─────────────────────────────────────────────────

    def render_for_node(self, node: Optional[RobotNode]):
        for w in self.inner.winfo_children():
            w.destroy()
        self._vars.clear()
        self._current_node = node
        if node is None:
            return

        ctk.CTkLabel(self.inner, text=f"  {node.node_type}", anchor="w",
                     font=font("heading")).pack(anchor="w", padx=8, pady=(8, 2))

        cf = self._section("Common Attributes")
        self._render_common(cf, node)

        self._separator()

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
            self._label(self.inner, "(children define behavior)",
                        muted=True).pack(padx=8, pady=8, anchor="w")

        # State filter — available on all event types except special nodes
        if node.node_type not in ('meter-list', 'output'):
            self._separator()
            self._render_state_filter(node)

        self.inner.update_idletasks()
        self.canvas.yview_moveto(0)

    # ── Common fields ───────────────────────────────────────────

    def _render_common(self, parent, node: RobotNode):
        # ID
        r = self._row(parent)
        self._label(r, "ID:", width=_LW_9).pack(side=tk.LEFT)
        v = tk.StringVar(value=node.id); self._vars['id'] = v
        self._entry(r, v).pack(side=tk.LEFT, fill=tk.X, expand=True)
        v.trace_add('write', lambda *_: self._write_field(node, 'id', v.get(), is_attr=False))

        # Weight
        r2 = self._row(parent)
        self._label(r2, "Weight:", width=_LW_9).pack(side=tk.LEFT)
        wv = tk.StringVar(value='' if node.weight is None else str(node.weight))
        self._vars['weight'] = wv
        self._entry(r2, wv, width=80).pack(side=tk.LEFT)
        self._label(r2, "  used inside Random/Scheduled", muted=True).pack(side=tk.LEFT)

        def _weight_change(*_):
            raw = wv.get().strip()
            try:
                node.weight = int(raw) if raw else None
            except ValueError:
                pass
            self._schedule()
        wv.trace_add('write', _weight_change)

        # Comment
        r3 = self._row(parent)
        self._label(r3, "Comment:", width=_LW_9).pack(side=tk.LEFT)
        cv = tk.StringVar(value=node.comment); self._vars['comment'] = cv
        self._entry(r3, cv).pack(side=tk.LEFT, fill=tk.X, expand=True)
        cv.trace_add('write', lambda *_: self._write_field(node, 'comment', cv.get(), is_attr=False))

    # ── Touch renderers ─────────────────────────────────────────

    def _render_touch_screen(self, node: RobotNode):
        if not node.points:
            node.points = [[0, 0]]
        lf = self._section("Touch Point")
        self._point_row(lf, node, 0, "Point:")

    def _render_touch_area(self, node: RobotNode):
        while len(node.points) < 2:
            node.points.append([0, 0])
        lf = self._section("Region (2 Points)")
        self._point_row(lf, node, 0, "Top-Left:")
        self._point_row(lf, node, 1, "Bot-Right:")

    def _render_swipe_screen(self, node: RobotNode):
        while len(node.points) < 2:
            node.points.append([0, 0])
        lf = self._section("Swipe (Start → End)")
        self._point_row(lf, node, 0, "Start:")
        self._point_row(lf, node, 1, "End:")

    def _point_row(self, parent, node: RobotNode, pi: int, label: str):
        r = self._row(parent)
        self._label(r, label, width=_LW_10).pack(side=tk.LEFT)
        self._label(r, "X:").pack(side=tk.LEFT)
        xv = tk.StringVar(value=str(node.points[pi][0]))
        self._vars[f'pt{pi}x'] = xv
        self._entry(r, xv, width=70).pack(side=tk.LEFT, padx=(0, 8))
        self._label(r, "Y:").pack(side=tk.LEFT)
        yv = tk.StringVar(value=str(node.points[pi][1]))
        self._vars[f'pt{pi}y'] = yv
        self._entry(r, yv, width=70).pack(side=tk.LEFT, padx=(0, 8))

        self._button(
            r, "Pick from Screen", lambda: self._pick_coordinate(xv, yv), width=140,
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
        lf = self._section("Button")

        r = self._row(lf)
        self._label(r, "Key:", width=_LW_9).pack(side=tk.LEFT)
        kv = tk.StringVar(value=node.attrs.get('key', 'Play'))
        self._vars['key'] = kv
        self._combo(r, kv, BUTTON_KEYS, width=170, readonly=False).pack(side=tk.LEFT)
        kv.trace_add('write', lambda *_: self._write_attr(node, 'key', kv.get()))

        r2 = self._row(lf)
        self._label(r2, "Value:", width=_LW_9).pack(side=tk.LEFT)
        vv = tk.StringVar(value=node.attrs.get('value', ''))
        self._vars['btn_val'] = vv
        self._entry(r2, vv, width=110).pack(side=tk.LEFT)
        self._label(r2, "  optional numeric", muted=True).pack(side=tk.LEFT)
        vv.trace_add('write', lambda *_: self._write_attr(node, 'value', vv.get()))

    def _render_wait(self, node: RobotNode):
        lf = self._section("Wait")

        r = self._row(lf)
        self._label(r, "Timeout:", width=_LW_10).pack(side=tk.LEFT)
        tv = tk.StringVar(value=node.attrs.get('timeout', '3'))
        self._vars['w_timeout'] = tv
        self._entry(r, tv, width=80).pack(side=tk.LEFT, padx=(0, 8))
        self._label(r, "Units:").pack(side=tk.LEFT)
        uv = tk.StringVar(value=node.attrs.get('units', 'Seconds'))
        self._vars['w_units'] = uv
        self._combo(r, uv, ['', 'Seconds', 'Minutes'], width=110).pack(side=tk.LEFT)
        self._label(r, "  blank=ms", muted=True).pack(side=tk.LEFT)
        tv.trace_add('write', lambda *_: self._write_attr(node, 'timeout', tv.get()))
        uv.trace_add('write', lambda *_: self._write_attr(node, 'units', uv.get()))

        r2 = self._row(lf)
        self._label(r2, "State:", width=_LW_10).pack(side=tk.LEFT)
        sv = tk.StringVar(value=node.attrs.get('state', ''))
        self._vars['w_state'] = sv
        self._combo(r2, sv, ['', *GAME_STATES], width=170).pack(side=tk.LEFT)
        self._label(r2, "  blank = run always", muted=True).pack(side=tk.LEFT)
        sv.trace_add('write', lambda *_: self._write_attr(node, 'state', sv.get()))

    def _render_insert_credit(self, node: RobotNode):
        lf = self._section("Insert Credit")
        for label, key, default in [
            ("Value:", "value", "2048"),
            ("When Below:", "when_below", "512"),
        ]:
            r = self._row(lf)
            self._label(r, label, width=_LW_12).pack(side=tk.LEFT)
            v = tk.StringVar(value=node.attrs.get(key, default))
            self._vars[key] = v
            self._entry(r, v, width=90).pack(side=tk.LEFT)
            v.trace_add('write', lambda *_, k=key, var=v: self._write_attr(node, k, var.get()))

    def _render_door(self, node: RobotNode):
        lf = self._section("Door")

        r = self._row(lf)
        self._label(r, "Door:", width=_LW_8).pack(side=tk.LEFT)
        dv = tk.StringVar(value=node.attrs.get('door', 'Logic'))
        self._vars['door'] = dv
        self._combo(r, dv, DOOR_NAMES, width=210).pack(side=tk.LEFT)
        dv.trace_add('write', lambda *_: self._write_attr(node, 'door', dv.get()))

        r2 = self._row(lf)
        self._label(r2, "Open:", width=_LW_8).pack(side=tk.LEFT)
        ov = tk.StringVar(value=node.attrs.get('open', 'True'))
        self._vars['door_open'] = ov
        self._combo(r2, ov, ['True', 'False'], width=110).pack(side=tk.LEFT)
        ov.trace_add('write', lambda *_: self._write_attr(node, 'open', ov.get()))

    def _render_switch(self, node: RobotNode):
        lf = self._section("Switch")

        r = self._row(lf)
        self._label(r, "Switch #:", width=_LW_10).pack(side=tk.LEFT)
        sv = tk.StringVar(value=node.attrs.get('switch', '2'))
        self._vars['switch_n'] = sv
        self._entry(r, sv, width=60).pack(side=tk.LEFT)
        self._label(r, "  1=jackpot  2=audit  (1–5)", muted=True).pack(side=tk.LEFT)
        sv.trace_add('write', lambda *_: self._write_attr(node, 'switch', sv.get()))

        r2 = self._row(lf)
        offv = tk.BooleanVar(value=bool(node.attrs.get('off', '')))
        self._vars['switch_off'] = offv
        self._check(r2, "Turn Off (switch off=True)", offv).pack(side=tk.LEFT)

        def _off_change(*_):
            node.attrs['off'] = 'True' if offv.get() else ''
            self._schedule()
        offv.trace_add('write', _off_change)

    def _render_random_credit(self, node: RobotNode):
        lf = self._section("Random Credit")
        r = self._row(lf)
        self._label(r, "Range:", width=_LW_8).pack(side=tk.LEFT)
        rv = tk.StringVar(value=node.attrs.get('range', '100'))
        self._vars['rc_range'] = rv
        self._entry(r, rv, width=80).pack(side=tk.LEFT)
        rv.trace_add('write', lambda *_: self._write_attr(node, 'range', rv.get()))

    def _render_scheduled(self, node: RobotNode):
        lf = self._section("Scheduled")
        r = self._row(lf)
        self._label(r, "Timeout:", width=_LW_10).pack(side=tk.LEFT)
        tv = tk.StringVar(value=node.attrs.get('timeout', '60'))
        self._vars['sched_t'] = tv
        self._entry(r, tv, width=80).pack(side=tk.LEFT, padx=(0, 8))
        self._label(r, "Units:").pack(side=tk.LEFT)
        uv = tk.StringVar(value=node.attrs.get('units', 'Seconds'))
        self._vars['sched_u'] = uv
        self._combo(r, uv, ['Seconds', 'Minutes'], width=110).pack(side=tk.LEFT)
        tv.trace_add('write', lambda *_: self._write_attr(node, 'timeout', tv.get()))
        uv.trace_add('write', lambda *_: self._write_attr(node, 'units', uv.get()))

    # ── Special renderers ───────────────────────────────────────

    def _render_output(self, node: RobotNode):
        lf = self._section("Output / Log")

        mode_var = tk.StringVar(value=node.attrs.get('mode', 'file'))
        self._vars['out_mode'] = mode_var

        mode_row = self._row(lf, pady=(4, 2))
        self._label(mode_row, "Mode:", width=_LW_9).pack(side=tk.LEFT)
        self._radio(mode_row, "File", mode_var, 'file').pack(side=tk.LEFT)
        self._radio(mode_row, "TCP Socket", mode_var, 'socket').pack(side=tk.LEFT, padx=8)

        # File mode frame
        file_frame = ctk.CTkFrame(lf, fg_color="transparent")
        r = self._row(file_frame)
        self._label(r, "Filename:", width=_LW_10).pack(side=tk.LEFT)
        fv = tk.StringVar(value=node.attrs.get('filename', 'robotlogs/eventsfile.txt'))
        self._vars['out_fn'] = fv
        self._entry(r, fv).pack(side=tk.LEFT, fill=tk.X, expand=True)
        fv.trace_add('write', lambda *_: self._write_attr(node, 'filename', fv.get()))

        r2 = self._row(file_frame)
        self._label(r2, "Append:", width=_LW_10).pack(side=tk.LEFT)
        av = tk.StringVar(value=node.attrs.get('append', 'False'))
        self._vars['out_app'] = av
        self._combo(r2, av, ['False', 'True'], width=95).pack(side=tk.LEFT)
        av.trace_add('write', lambda *_: self._write_attr(node, 'append', av.get()))

        # Socket mode frame
        socket_frame = ctk.CTkFrame(lf, fg_color="transparent")
        rs = self._row(socket_frame)
        self._label(rs, "Address:", width=_LW_10).pack(side=tk.LEFT)
        addr_v = tk.StringVar(value=node.attrs.get('address', ''))
        self._vars['out_addr'] = addr_v
        self._entry(rs, addr_v, width=200).pack(side=tk.LEFT)
        self._label(rs, "  ip:port", muted=True).pack(side=tk.LEFT)
        addr_v.trace_add('write', lambda *_: self._write_attr(node, 'address', addr_v.get()))

        def _mode_changed(*_):
            m = mode_var.get()
            node.attrs['mode'] = m
            if m == 'socket':
                file_frame.pack_forget()
                socket_frame.pack(fill=tk.X)
            else:
                socket_frame.pack_forget()
                file_frame.pack(fill=tk.X)
            self._schedule()

        mode_var.trace_add('write', _mode_changed)

        if mode_var.get() == 'socket':
            socket_frame.pack(fill=tk.X)
        else:
            file_frame.pack(fill=tk.X)

    def _render_meter_list(self, node: RobotNode):
        # Settings section
        sf = self._section("Meter List Settings")

        # Trigger mode toggle
        mode_var = tk.StringVar(value=node.attrs.get('mode', 'periodic'))
        self._vars['ml_mode'] = mode_var
        mr = self._row(sf, pady=(4, 2))
        self._label(mr, "Trigger:", width=_LW_10).pack(side=tk.LEFT)
        self._radio(mr, "Periodic (timeout)", mode_var, 'periodic').pack(side=tk.LEFT)
        self._radio(mr, "State change", mode_var, 'state').pack(side=tk.LEFT, padx=8)

        # Periodic frame
        periodic_frame = ctk.CTkFrame(sf, fg_color="transparent")
        r = self._row(periodic_frame)
        self._label(r, "Timeout:", width=_LW_10).pack(side=tk.LEFT)
        tv = tk.StringVar(value=node.attrs.get('timeout', '15'))
        self._vars['ml_t'] = tv
        self._entry(r, tv, width=60).pack(side=tk.LEFT, padx=(0, 8))
        self._label(r, "Units:").pack(side=tk.LEFT)
        uv = tk.StringVar(value=node.attrs.get('units', 'Seconds'))
        self._vars['ml_u'] = uv
        self._combo(r, uv, ['Seconds', 'Minutes'], width=110).pack(side=tk.LEFT)
        tv.trace_add('write', lambda *_: self._write_attr(node, 'timeout', tv.get()))
        uv.trace_add('write', lambda *_: self._write_attr(node, 'units', uv.get()))

        # State frame
        state_frame = ctk.CTkFrame(sf, fg_color="transparent")
        r_s = self._row(state_frame)
        self._label(r_s, "State:", width=_LW_10).pack(side=tk.LEFT)
        sv = tk.StringVar(value=node.attrs.get('state', 'Game-Idle'))
        self._vars['ml_state'] = sv
        self._combo(r_s, sv, GAME_STATES, width=170).pack(side=tk.LEFT)
        sv.trace_add('write', lambda *_: self._write_attr(node, 'state', sv.get()))

        r_ol = self._row(state_frame)
        self._label(r_ol, "On-Leave:", width=_LW_10).pack(side=tk.LEFT)
        olv = tk.StringVar(value=node.attrs.get('on_leave', 'False'))
        self._vars['ml_ol'] = olv
        self._combo(r_ol, olv, ['False', 'True'], width=95).pack(side=tk.LEFT)
        self._label(r_ol, "  True = fire when leaving state", muted=True).pack(side=tk.LEFT)
        olv.trace_add('write', lambda *_: self._write_attr(node, 'on_leave', olv.get()))

        def _ml_mode_changed(*_):
            m = mode_var.get()
            node.attrs['mode'] = m
            if m == 'state':
                periodic_frame.pack_forget()
                state_frame.pack(fill=tk.X)
            else:
                state_frame.pack_forget()
                periodic_frame.pack(fill=tk.X)
            self._schedule()

        mode_var.trace_add('write', _ml_mode_changed)

        if mode_var.get() == 'state':
            state_frame.pack(fill=tk.X)
        else:
            periodic_frame.pack(fill=tk.X)

        # Output destination (file or socket)
        out_mode_var = tk.StringVar(value=node.attrs.get('output_mode', 'file'))
        self._vars['ml_out_mode'] = out_mode_var

        om_row = self._row(sf, pady=(6, 2))
        self._label(om_row, "Output:", width=_LW_10).pack(side=tk.LEFT)
        self._radio(om_row, "File", out_mode_var, 'file').pack(side=tk.LEFT)
        self._radio(om_row, "TCP Socket", out_mode_var, 'socket').pack(side=tk.LEFT, padx=8)

        ml_file_frame = ctk.CTkFrame(sf, fg_color="transparent")
        r2 = self._row(ml_file_frame)
        self._label(r2, "Log File:", width=_LW_10).pack(side=tk.LEFT)
        fnv = tk.StringVar(value=node.attrs.get('output_filename', 'robotlogs/eventsfile.txt'))
        self._vars['ml_fn'] = fnv
        self._entry(r2, fnv).pack(side=tk.LEFT, fill=tk.X, expand=True)
        fnv.trace_add('write', lambda *_: self._write_attr(node, 'output_filename', fnv.get()))

        r3 = self._row(ml_file_frame)
        self._label(r3, "Append:", width=_LW_10).pack(side=tk.LEFT)
        apv = tk.StringVar(value=node.attrs.get('output_append', 'False'))
        self._vars['ml_ap'] = apv
        self._combo(r3, apv, ['False', 'True'], width=95).pack(side=tk.LEFT)
        apv.trace_add('write', lambda *_: self._write_attr(node, 'output_append', apv.get()))

        ml_socket_frame = ctk.CTkFrame(sf, fg_color="transparent")
        rs = self._row(ml_socket_frame)
        self._label(rs, "Address:", width=_LW_10).pack(side=tk.LEFT)
        addr_v = tk.StringVar(value=node.attrs.get('output_address', ''))
        self._vars['ml_addr'] = addr_v
        self._entry(rs, addr_v, width=200).pack(side=tk.LEFT)
        self._label(rs, "  ip:port", muted=True).pack(side=tk.LEFT)
        addr_v.trace_add('write', lambda *_: self._write_attr(node, 'output_address', addr_v.get()))

        def _ml_out_mode_changed(*_):
            om = out_mode_var.get()
            node.attrs['output_mode'] = om
            if om == 'socket':
                ml_file_frame.pack_forget()
                ml_socket_frame.pack(fill=tk.X)
            else:
                ml_socket_frame.pack_forget()
                ml_file_frame.pack(fill=tk.X)
            self._schedule()

        out_mode_var.trace_add('write', _ml_out_mode_changed)

        if out_mode_var.get() == 'socket':
            ml_socket_frame.pack(fill=tk.X)
        else:
            ml_file_frame.pack(fill=tk.X)

        # Meters section
        mf = self._section("Meters (check to include)")

        btn_row = self._row(mf, pady=(4, 2))

        selected = set(node.attrs.get('meters') or ALL_METERS)
        meter_vars: dict = {}

        def _select_all():
            for v in meter_vars.values():
                v.set(True)

        def _clear_all():
            for v in meter_vars.values():
                v.set(False)

        self._button(btn_row, "Select All", _select_all, width=100).pack(side=tk.LEFT, padx=2)
        self._button(btn_row, "Clear All",  _clear_all,  width=100).pack(side=tk.LEFT)

        grid = ctk.CTkFrame(mf, fg_color="transparent")
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
            self._check(grid, meter, bv).grid(
                row=i // cols, column=i % cols, sticky="w", padx=4, pady=1)

    def _render_state_filter(self, node: RobotNode):
        """State-list filter shown at the bottom of every event's property form."""
        lf = self._section("State Filter (state-list)")

        enabled = node.state_filter is not None
        en_var = tk.BooleanVar(value=enabled)
        self._vars['sf_enabled'] = en_var

        en_row = self._row(lf, pady=(4, 2))

        # Content frame — shown only when enabled
        content = ctk.CTkFrame(lf, fg_color="transparent")

        sf = node.state_filter or {}
        type_var = tk.StringVar(value=sf.get('type', 'White'))
        self._vars['sf_type'] = type_var

        current_states = set(sf.get('states', []))
        state_vars: dict = {}

        def _update_filter(*_):
            if en_var.get():
                states = [s for s in GAME_STATES if state_vars[s].get()]
                node.state_filter = {'type': type_var.get(), 'states': states}
            else:
                node.state_filter = None
            self._schedule()

        type_row = self._row(content)
        self._label(type_row, "Type:", width=_LW_8).pack(side=tk.LEFT)
        for t in ('White', 'Black'):
            self._radio(type_row, t, type_var, t, command=_update_filter).pack(side=tk.LEFT, padx=4)
        self._label(type_row, "  White=allow  Black=block", muted=True).pack(side=tk.LEFT)

        grid = ctk.CTkFrame(content, fg_color="transparent")
        grid.pack(fill=tk.X, padx=4, pady=(0, 4))
        for i, state in enumerate(GAME_STATES):
            bv = tk.BooleanVar(value=(state in current_states))
            state_vars[state] = bv
            self._vars[f'sf_{i}'] = bv
            bv.trace_add('write', _update_filter)
            self._check(grid, state, bv).grid(
                row=i // 3, column=i % 3, sticky="w", padx=4, pady=1)

        def _toggle_enabled():
            if en_var.get():
                content.pack(fill=tk.X)
            else:
                content.pack_forget()
            _update_filter()

        self._check(en_row, "Enable state filter", en_var,
                    command=_toggle_enabled).pack(side=tk.LEFT)

        if enabled:
            content.pack(fill=tk.X)

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

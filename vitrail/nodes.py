# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Hamid Reza Jahanshahi (S.U.P.E)
"""Blueprint / node-graph editor.

    ed = NodeEditor(fill=True)
    ed.register("Math/Add", lambda: Node("Add", inputs=[("A", "float", 0.0), ("B", "float", 0.0)],
                                         outputs=[("Result", "float")],
                                         compute=lambda i: {"Result": i["A"] + i["B"]}))
    n = ed.add_node(ed.create("Math/Add"), (40, 60))

Mouse:  drag a node to move it (Ctrl snaps to the grid) - drag empty space to
box-select (Shift adds, Ctrl removes) - drag from a socket to connect - drag a
connected input to re-route or detach - middle-drag / Alt-drag to pan - wheel to
zoom - right-click (or Shift+A) for the searchable "Add node" menu -
Ctrl+right-drag to cut wires.
Keys:   Delete / X remove, Shift+D duplicate, Ctrl+A select all, Home / F frame all.

Widgets inside nodes (number fields, colour swatches, dropdowns...) are real
Vitrail widgets: they scale with the zoom and keep all their interactions.
"""
from __future__ import annotations

import itertools
import math

import pygame

from .core import CTRL, SHIFT, ALT, Container, Signal, Widget
from .layout import Column
from .menus import Menu, MenuItem
from .theme import rgb, shade, with_alpha
from .widgets import Checkbox, ColorSwatch, Label, NumberField, TextField

SOCKET_COLORS = {
    "float": rgb("#a1a1a1"), "int": rgb("#3fbf9f"), "bool": rgb("#e05d8b"), "vector": rgb("#7d73e6"),
    "color": rgb("#e8c547"), "string": rgb("#59b8e6"), "exec": rgb("#f2f2f2"), "object": rgb("#ef8f3a"),
    "any": rgb("#8a8f98"),
}
NODE_COLORS = {
    "input": rgb("#7a3a3a"), "math": rgb("#2f5d8a"), "vector": rgb("#4b3d8f"), "color": rgb("#8a6d1f"),
    "logic": rgb("#7a2f5c"), "output": rgb("#2f6b46"), "event": rgb("#8a2f2f"), "default": rgb("#4a4f57"),
}
_ids = itertools.count(1)


class Socket:
    def __init__(self, name, type="float", default=None, *, multi=None):
        self.name = name
        self.type = type
        self.default = default
        self.multi = multi
        self.node = None
        self.is_output = False
        self.links = []
        self.slot = None

    @property
    def color(self):
        return SOCKET_COLORS.get(self.type, SOCKET_COLORS["any"])

    def accepts_many(self):
        if self.multi is not None:
            return self.multi
        return self.is_output != (self.type == "exec")   # exec: one out, many in

    def value(self):
        f = self.slot.field if self.slot is not None else None
        return f.value if f is not None else self.default

    def compatible(self, other):
        if self.is_output == other.is_output or self.node is other.node:
            return False
        a, b = self.type, other.type
        if "exec" in (a, b):
            return a == b
        return a == b or "any" in (a, b) or {a, b} <= {"float", "int", "bool"} or {a, b} <= {"vector", "color"}

    def __repr__(self):
        return f"<Socket {self.node.title if self.node else '?'}.{self.name} {self.type}>"


class Link:
    __slots__ = ("out", "inp")

    def __init__(self, out, inp):
        self.out, self.inp = out, inp


def _make_socket(spec, output):
    if isinstance(spec, Socket):
        s = spec
    elif isinstance(spec, str):
        s = Socket(spec)
    else:
        s = Socket(*spec)
    s.is_output = output
    return s


class _Slot(Container):
    """One socket row inside a node: a label, or an inline editor while unconnected."""

    def __init__(self, socket, field=None):
        super().__init__()
        self.socket = socket
        socket.slot = self
        self.label = Label(socket.name, align="right" if socket.is_output else "left", parent=self)
        self.field = field
        if field is not None:
            self.add(field)

    def shown(self):
        if self.field is not None and not self.socket.links:
            return self.field
        return self.label

    def visible_children(self):
        return [self.shown()]

    def children_shown(self):
        return True

    def preferred_size(self, ui):
        w, h = self.shown().measure(ui)
        return (w + 12 * ui.theme.scale, h)

    def arrange(self, x, y, w, h):
        Widget.arrange(self, x, y, w, h)
        inset = round(9 * (self.ui.theme.scale if self.ui else 1))
        self.shown().arrange(x + inset, y, w - 2 * inset, h)

    def widget_at(self, px, py):
        sh = self.shown()
        if sh is self.label:
            return None
        return sh.widget_at(px, py)

    def draw(self, dl, ui):
        self.shown().paint(dl, ui)


def _field_for(socket):
    d = socket.default
    if d is None:
        return None
    if socket.type in ("float", "int", "any") and isinstance(d, (int, float)) and not isinstance(d, bool):
        return NumberField(socket.name, d, integer=(socket.type == "int"), step=1 if socket.type == "int" else 0.1)
    if socket.type == "bool" or isinstance(d, bool):
        return Checkbox(socket.name, bool(d))
    if socket.type == "color":
        return ColorSwatch(d, label=socket.name)
    if socket.type == "string":
        return TextField(str(d), placeholder=socket.name)
    return None


class Node(Column):
    """A node. ``compute(inputs: dict) -> dict`` makes it evaluable."""

    def __init__(self, title, *, inputs=(), outputs=(), category="default", color=None, width=170,
                 compute=None, icon=None):
        super().__init__(padding=4, spacing=2, width=width)
        self.id = next(_ids)
        self.title = title
        self.icon = icon
        self.category = category
        self.color = color or NODE_COLORS.get(category, NODE_COLORS["default"])
        self.compute = compute
        self.gpos = (0.0, 0.0)
        self.selected = False
        self.type_name = None
        self.editor = None
        self.inputs = []
        self.outputs = []
        self.body = Column(spacing=2)
        for spec in outputs:
            self.add_output(spec)
        for spec in inputs:
            self.add_input(spec)

    def add_input(self, spec):
        s = _make_socket(spec, False)
        s.node = self
        self.inputs.append(s)
        f = _field_for(s)
        if f is not None:
            f.changed.connect(lambda _v: self._value_changed())
        self.add(_Slot(s, f))
        return s

    def add_output(self, spec):
        s = _make_socket(spec, True)
        s.node = self
        self.outputs.append(s)
        # outputs go before inputs
        slot = _Slot(s)
        n_out = len(self.outputs) - 1
        self.add(slot)
        self.children.remove(slot)
        self.children.insert(n_out, slot)
        return s

    def socket(self, name, output=None):
        pool = (self.outputs if output else self.inputs) if output is not None else self.outputs + self.inputs
        for s in pool:
            if s.name == name:
                return s
        raise KeyError(name)

    def _value_changed(self):
        if self.editor is not None:
            self.editor.graph_changed.emit()

    def header_h(self, ui):
        return round(ui.theme.h + 4 * ui.theme.scale)

    def preferred_size(self, ui):
        self._hh = self.header_h(ui)
        w, h = super().preferred_size(ui)
        return (w, h + self._hh + 2 * ui.theme.scale)

    def content_box(self):
        hh = getattr(self, "_hh", 0)
        return self.x, self.y + hh, self.w, self.h - hh

    def socket_pos(self, s):
        sl = s.slot
        y = sl.y + sl.h / 2 if sl is not None and sl.h else self.y + 10
        return (self.x + self.w if s.is_output else self.x, y)

    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        for c in reversed(self.children):
            r = c.widget_at(px, py)
            if r is not None:
                return r
        return self

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        r = 6 * s
        hh = self._hh
        dl.shadow(self.x, self.y, self.w, self.h, (0, 0, 0, 0.5), r, 12 * s)
        dl.box(self.x, self.y, self.w, self.h, with_alpha(shade(t.panel, 0.03), 0.97), radius=r)
        dl.box(self.x, self.y, self.w, hh, shade(self.color, 0.12), shade(self.color, -0.08), radius=(r, r, 0, 0))
        tx = self.x + 8 * s
        if self.icon:
            tx += ui.icon(dl, self.icon, tx, self.y + (hh - ui.icon_size(14)) / 2, 14, (1, 1, 1, 0.85)) + 6 * s
        ui.text(dl, self.title, tx, self.y, self.x + self.w - 8 * s - tx, hh, (1, 1, 1, 0.95), bold=True)
        ed = self.editor
        active = ed is not None and ed.active_node is self
        if self.selected:
            col = (1, 1, 1, 0.9) if active else with_alpha(t.accent, 0.95)
            dl.box(self.x - 1.5 * s, self.y - 1.5 * s, self.w + 3 * s, self.h + 3 * s, (0, 0, 0, 0), border=col,
                   radius=r + 1.5 * s, border_width=1.6 * s)
        else:
            dl.box(self.x, self.y, self.w, self.h, (0, 0, 0, 0), border=t.outline, radius=r)
        self.draw_children(dl, ui)
        for sock in self.outputs + self.inputs:
            x, y = self.socket_pos(sock)
            rad = 5 * s
            if sock.type == "exec":
                dl.triangle(x - rad - 1, y - rad - 1, 2 * rad + 2, 2 * rad + 2, sock.color, 0)
                if not sock.links:
                    dl.triangle(x - rad + 1.5 * s, y - rad + 1.5 * s, 2 * rad - 3 * s, 2 * rad - 3 * s,
                                (0.12, 0.12, 0.13, 1), 0)
            else:
                dl.box(x - rad, y - rad, 2 * rad, 2 * rad, sock.color if sock.links or sock.is_output else shade(sock.color, -0.25),
                       border=(0.08, 0.08, 0.09, 1), radius=rad, border_width=1.3 * s)


class NodeEditor(Container):
    hover_redraw = False

    def __init__(self, **kw):
        kw.setdefault("height", 360)
        super().__init__(**kw)
        self.content_zoom = 1.0
        self.pan = [40.0, 40.0]
        self.links = []
        self.registry = {}
        self.active_node = None
        self._mode = None
        self._drag = None
        self._temp = None            # (fixed socket, (x, y) free end, hovered socket)
        self._box = None
        self._knife = None
        self._menu_gpos = (0, 0)
        self.graph_changed = Signal()
        self.selection_changed = Signal()
        self.link_added = Signal()
        self.link_removed = Signal()

    # -- registry / creation ----------------------------------------------------------------
    def register(self, path, factory):
        """``path`` like "Math/Add"; ``factory()`` returns a Node."""
        self.registry[path] = factory

    def create(self, path):
        node = self.registry[path]()
        node.type_name = path
        return node

    def add_node(self, node, gpos=(0, 0), undo=False):
        node.gpos = (float(gpos[0]), float(gpos[1]))
        node.editor = self
        self.add(node)
        if undo and self.ui is not None:
            self.ui.undo.push("Add node", lambda: self.remove_nodes([node], undo=False),
                              lambda: self._restore([node], []))
        self.graph_changed.emit()
        return node

    @property
    def nodes(self):
        return [c for c in self.children if isinstance(c, Node)]

    def remove_nodes(self, nodes, undo=True):
        nodes = list(nodes)
        dead = [lk for lk in self.links if lk.out.node in nodes or lk.inp.node in nodes]
        for lk in dead:
            self._unlink(lk)
        for n in nodes:
            self.remove(n)
            n.editor = None
            if self.active_node is n:
                self.active_node = None
        if undo and self.ui is not None:
            self.ui.undo.push("Delete nodes", lambda: self._restore(nodes, dead),
                              lambda: self.remove_nodes(nodes, undo=False))
        self.graph_changed.emit()

    def _restore(self, nodes, links):
        for n in nodes:
            n.editor = self
            self.add(n)
        for lk in links:
            self._link(lk)
        self.graph_changed.emit()

    # -- links ---------------------------------------------------------------------------------
    def _reaches(self, a, b):
        """True if node b is downstream of node a."""
        stack, seen = [a], set()
        while stack:
            n = stack.pop()
            if n is b:
                return True
            if id(n) in seen:
                continue
            seen.add(id(n))
            for s in n.outputs:
                for lk in s.links:
                    stack.append(lk.inp.node)
        return False

    def can_connect(self, a, b):
        out, inp = (a, b) if a.is_output else (b, a)
        return a.compatible(b) and not self._reaches(inp.node, out.node)

    def connect(self, a, b, undo=True):
        """Connect two sockets (any order). Returns the Link or None."""
        if not self.can_connect(a, b):
            return None
        out, inp = (a, b) if a.is_output else (b, a)
        for lk in list(self.links):
            if lk.out is out and lk.inp is inp:
                return lk
        replaced = []
        if not inp.accepts_many():
            replaced += list(inp.links)
        if not out.accepts_many():
            replaced += list(out.links)
        for lk in replaced:
            self._unlink(lk)
        lk = Link(out, inp)
        self._link(lk)
        if undo and self.ui is not None:
            def u():
                self._unlink(lk)
                for r in replaced:
                    self._link(r)
                self.graph_changed.emit()

            def r_():
                for r in replaced:
                    self._unlink(r)
                self._link(lk)
                self.graph_changed.emit()
            self.ui.undo.push("Connect", u, r_)
        self.graph_changed.emit()
        return lk

    def disconnect(self, lk, undo=True):
        self._unlink(lk)
        if undo and self.ui is not None:
            self.ui.undo.push("Disconnect", lambda: (self._link(lk), self.graph_changed.emit()),
                              lambda: (self._unlink(lk), self.graph_changed.emit()))
        self.graph_changed.emit()

    def _link(self, lk):
        if lk in self.links:
            return
        self.links.append(lk)
        lk.out.links.append(lk)
        lk.inp.links.append(lk)
        self._touch(lk)
        self.link_added.emit(lk)

    def _unlink(self, lk):
        if lk not in self.links:
            return
        self.links.remove(lk)
        lk.out.links.remove(lk)
        lk.inp.links.remove(lk)
        self._touch(lk)
        self.link_removed.emit(lk)

    def _touch(self, lk):
        for s in (lk.out, lk.inp):
            if s.slot is not None:
                s.slot.invalidate(layout=True)
        self.invalidate(layout=True)

    # -- evaluation -------------------------------------------------------------------------------
    def evaluate(self, node, _cache=None):
        """Pull-evaluate ``node`` (and everything upstream). Returns its outputs dict."""
        cache = {} if _cache is None else _cache
        if node.id in cache:
            return cache[node.id]
        cache[node.id] = {}
        inputs = {}
        for s in node.inputs:
            if s.links:
                src = s.links[0].out
                inputs[s.name] = self.evaluate(src.node, cache).get(src.name)
            else:
                inputs[s.name] = s.value()
        try:
            out = node.compute(inputs) if node.compute else {}
        except Exception as exc:   # a broken node must not kill the UI
            out = {"error": exc}
        cache[node.id] = out or {}
        return cache[node.id]

    # -- serialisation ------------------------------------------------------------------------------
    def to_dict(self):
        nodes = []
        for n in self.nodes:
            vals = {}
            for s in n.inputs:
                f = s.slot.field if s.slot else None
                if f is not None:
                    vals[s.name] = list(f.value) if isinstance(f.value, tuple) else f.value
            nodes.append({"id": n.id, "type": n.type_name, "pos": list(n.gpos), "values": vals})
        links = [[lk.out.node.id, lk.out.name, lk.inp.node.id, lk.inp.name] for lk in self.links]
        return {"nodes": nodes, "links": links, "view": {"pan": list(self.pan), "zoom": self.content_zoom}}

    def from_dict(self, data):
        for n in list(self.nodes):
            self.remove(n)
        self.links = []
        idmap = {}
        for nd in data.get("nodes", []):
            if nd.get("type") not in self.registry:
                continue
            n = self.add_node(self.create(nd["type"]), nd.get("pos", (0, 0)))
            idmap[nd["id"]] = n
            for name, v in nd.get("values", {}).items():
                try:
                    f = n.socket(name, False).slot.field
                    if f is not None:
                        f.set_value(tuple(v) if isinstance(v, list) else v, emit=False)
                except KeyError:
                    pass
        for a, an, b, bn in data.get("links", []):
            if a in idmap and b in idmap:
                try:
                    self.connect(idmap[a].socket(an, True), idmap[b].socket(bn, False), undo=False)
                except KeyError:
                    pass
        v = data.get("view")
        if v:
            self.pan = list(v["pan"])
            self.content_zoom = v["zoom"]
        self.invalidate(layout=True)

    # -- coordinates -----------------------------------------------------------------------------------
    def to_screen(self, gx, gy):
        z = self.content_zoom
        return (self.x + self.pan[0] + gx * z, self.y + self.pan[1] + gy * z)

    def to_graph(self, sx, sy):
        z = self.content_zoom
        return ((sx - self.x - self.pan[0]) / z, (sy - self.y - self.pan[1]) / z)

    # -- layout ---------------------------------------------------------------------------------------
    def preferred_size(self, ui):
        with ui.scaled(self.content_zoom):
            for n in self.nodes:
                n.measure(ui)
        return (200 * ui.theme.scale, 200 * ui.theme.scale)

    def arrange(self, x, y, w, h):
        Widget.arrange(self, x, y, w, h)
        self._reflow()

    def _reflow(self):
        ui = self.ui
        if ui is None:
            return
        with ui.scaled(self.content_zoom):
            for n in self.nodes:
                sx, sy = self.to_screen(*n.gpos)
                n.arrange(round(sx), round(sy), round(n._pref[0]), round(n._pref[1]))

    def visible_children(self):
        return self.children

    # -- hit testing ------------------------------------------------------------------------------------
    def socket_at(self, px, py):
        r = (7 * self.content_zoom + 4) * (self.ui.theme.scale if self.ui else 1)
        best, bd = None, r
        for n in reversed(self.nodes):
            for s in n.outputs + n.inputs:
                sx, sy = n.socket_pos(s)
                d = math.hypot(px - sx, py - sy)
                if d < bd:
                    best, bd = s, d
        return best

    def node_at(self, px, py):
        for n in reversed(self.nodes):
            if n.contains(px, py):
                return n
        return None

    def widget_at(self, px, py):
        if not self._visible or not self.contains(px, py):
            return None
        if self._mode is None and self.socket_at(px, py) is not None:
            return self
        for n in reversed(self.nodes):
            r = n.widget_at(px, py)
            if r is not None:
                return r if r is not n else self
        return self

    # -- painting -------------------------------------------------------------------------------------
    def _wire(self, dl, ui, a, b, color, width):
        dx = max(30.0, abs(b[0] - a[0]) * 0.5) * (1 if b[0] >= a[0] else 1.2)
        dl.bezier(a, (a[0] + dx, a[1]), (b[0] - dx, b[1]), b, color, width)

    def draw(self, dl, ui):
        t = ui.theme
        s = t.scale
        z = self.content_zoom
        dl.push_clip(self.x, self.y, self.w, self.h)
        bg = shade(t.background, -0.15)
        dl.box(self.x, self.y, self.w, self.h, bg)
        cell = 24 * z * s
        while cell < 8:
            cell *= 5
        dl.grid(self.x, self.y, self.w, self.h, cell, (self.x + self.pan[0], self.y + self.pan[1]),
                with_alpha(t.text, 0.035), with_alpha(t.text, 0.085))
        width = max(1.2, 2.2 * z) * s
        for lk in self.links:
            a = lk.out.node.socket_pos(lk.out)
            b = lk.inp.node.socket_pos(lk.inp)
            hot = lk.out.node.selected or lk.inp.node.selected
            col = lk.out.color if lk.out.type != "float" else shade(lk.out.color, 0.15)
            self._wire(dl, ui, a, b, (0, 0, 0, 0.35), width + 2 * s)
            self._wire(dl, ui, a, b, shade(col, 0.25) if hot else col, width)
        with ui.scaled(z):
            for n in self.nodes:
                n.paint(dl, ui)
        if self._temp is not None:
            fixed, free, target = self._temp
            p = fixed.node.socket_pos(fixed)
            q = target.node.socket_pos(target) if target is not None else free
            a, b = (p, q) if fixed.is_output else (q, p)
            col = fixed.color if (target is None or self.can_connect(fixed, target)) else rgb("#e05050")
            self._wire(dl, ui, a, b, with_alpha(col, 0.9), width)
        if self._box is not None:
            x0, y0, x1, y1 = self._box
            bx, by, bw, bh = min(x0, x1), min(y0, y1), abs(x1 - x0), abs(y1 - y0)
            dl.box(bx, by, bw, bh, with_alpha(t.accent, 0.12), border=with_alpha(t.accent, 0.8), radius=2)
        if self._knife is not None:
            (x0, y0), (x1, y1) = self._knife
            dl.line(x0, y0, x1, y1, rgb("#ff6a5a"), 2 * s)
        ui.text(dl, f"{z * 100:.0f}%", self.x + 8 * s, self.y + self.h - t.h - 4 * s, 80 * s, t.h,
                with_alpha(t.text_dim, 0.8), size=11)
        dl.pop_clip()

    # -- interaction -----------------------------------------------------------------------------------
    def _set_selection(self, nodes, active=None):
        sel = set(map(id, nodes))
        for n in self.nodes:
            n.selected = id(n) in sel
            n.invalidate()
        if active is not None:
            self.active_node = active
            self.children.remove(active)       # raise to top
            self.children.append(active)
        self.selection_changed.emit([n for n in self.nodes if n.selected])
        self.invalidate()

    @property
    def selected(self):
        return [n for n in self.nodes if n.selected]

    def on_mouse_down(self, ui, ev):
        ui.set_focus(self)
        if ev.button == 2 or (ev.button == 1 and ev.mods & ALT):
            self._mode = "pan"
            self._drag = (ev.x, ev.y, list(self.pan))
            return True
        if ev.button == 3:
            if ev.ctrl:
                self._mode = "knife"
                self._knife = [(ev.x, ev.y), (ev.x, ev.y)]
                return True
            self.open_add_menu(ui, (ev.x, ev.y))
            return True
        if ev.button != 1:
            return False
        sock = self.socket_at(ev.x, ev.y)
        if sock is not None:
            if not sock.is_output and sock.links and not sock.accepts_many():
                lk = sock.links[-1]                       # pick the wire up from its input
                self.disconnect(lk)
                self._temp = (lk.out, (ev.x, ev.y), None)
            else:
                self._temp = (sock, (ev.x, ev.y), None)
            self._mode = "link"
            self.invalidate()
            return True
        node = self.node_at(ev.x, ev.y)
        if node is not None:
            if ev.shift:
                node.selected = not node.selected
                self._set_selection(self.selected, node if node.selected else None)
            elif not node.selected:
                self._set_selection([node], node)
            else:
                self._set_selection(self.selected, node)
            self._mode = "move"
            self._drag = (ev.x, ev.y, {n.id: n.gpos for n in self.selected}, False)
            return True
        self._mode = "box"
        self._box = [ev.x, ev.y, ev.x, ev.y]
        self._box_mods = ev.mods
        if not ev.shift and not ev.ctrl:
            self._set_selection([])
        return True

    def on_mouse_move(self, ui, ev):
        m = self._mode
        if m == "pan":
            x0, y0, p0 = self._drag
            self.pan = [p0[0] + ev.x - x0, p0[1] + ev.y - y0]
            self._reflow()
            self.invalidate()
        elif m == "move":
            x0, y0, start, moved = self._drag
            dx, dy = (ev.x - x0) / self.content_zoom, (ev.y - y0) / self.content_zoom
            self._drag = (x0, y0, start, True)
            for n in self.selected:
                gx, gy = start.get(n.id, n.gpos)
                gx, gy = gx + dx, gy + dy
                if ev.ctrl:
                    gx, gy = round(gx / 24) * 24, round(gy / 24) * 24
                n.gpos = (gx, gy)
            self._reflow()
            self.invalidate()
        elif m == "link":
            fixed, _, _ = self._temp
            target = self.socket_at(ev.x, ev.y)
            if target is not None and (target is fixed or target.is_output == fixed.is_output):
                target = None
            self._temp = (fixed, (ev.x, ev.y), target)
            self.invalidate()
        elif m == "box":
            self._box[2], self._box[3] = ev.x, ev.y
            self.invalidate()
        elif m == "knife":
            self._knife[1] = (ev.x, ev.y)
            self.invalidate()

    def on_mouse_up(self, ui, ev):
        m, self._mode = self._mode, None
        if m == "move":
            x0, y0, start, moved = self._drag
            nodes = [n for n in self.selected if n.id in start]
            if moved:
                before = {n.id: start[n.id] for n in nodes}
                after = {n.id: n.gpos for n in nodes}

                def apply(pos):
                    for n in nodes:
                        n.gpos = pos[n.id]
                    self._reflow()
                    self.invalidate()
                ui.undo.push("Move nodes", lambda: apply(before), lambda: apply(after))
        elif m == "link":
            fixed, _, target = self._temp
            self._temp = None
            if target is not None:
                self.connect(fixed, target)
        elif m == "box":
            x0, y0, x1, y1 = self._box
            self._box = None
            bx0, bx1, by0, by1 = min(x0, x1), max(x0, x1), min(y0, y1), max(y0, y1)
            hit = [n for n in self.nodes if n.x < bx1 and n.x + n.w > bx0 and n.y < by1 and n.y + n.h > by0]
            if self._box_mods & CTRL:
                keep = [n for n in self.selected if n not in hit]
            elif self._box_mods & SHIFT:
                keep = self.selected + [n for n in hit if not n.selected]
            else:
                keep = hit
            self._set_selection(keep)
        elif m == "knife":
            (x0, y0), (x1, y1) = self._knife
            self._knife = None
            for lk in list(self.links):
                if self._wire_hits_segment(lk, (x0, y0), (x1, y1)):
                    self.disconnect(lk)
        self._drag = None
        self.invalidate()

    def _wire_hits_segment(self, lk, p, q):
        a = lk.out.node.socket_pos(lk.out)
        b = lk.inp.node.socket_pos(lk.inp)
        dx = max(30.0, abs(b[0] - a[0]) * 0.5)
        pts = []
        for i in range(25):
            t = i / 24
            u = 1 - t
            c1, c2 = (a[0] + dx, a[1]), (b[0] - dx, b[1])
            pts.append((u ** 3 * a[0] + 3 * u * u * t * c1[0] + 3 * u * t * t * c2[0] + t ** 3 * b[0],
                        u ** 3 * a[1] + 3 * u * u * t * c1[1] + 3 * u * t * t * c2[1] + t ** 3 * b[1]))

        def cross(o, a_, b_):
            return (a_[0] - o[0]) * (b_[1] - o[1]) - (a_[1] - o[1]) * (b_[0] - o[0])
        for r, s_ in zip(pts, pts[1:]):
            d1, d2 = cross(p, q, r), cross(p, q, s_)
            d3, d4 = cross(r, s_, p), cross(r, s_, q)
            if (d1 > 0) != (d2 > 0) and (d3 > 0) != (d4 > 0):
                return True
        return False

    def on_wheel(self, ui, dx, dy):
        if not dy:
            return False
        mx, my = ui.mouse
        gx, gy = self.to_graph(mx, my)
        z = max(0.3, min(2.5, self.content_zoom * (1.12 if dy > 0 else 1 / 1.12)))
        z = round(z * 50) / 50                      # quantised -> few font sizes in the atlas
        self.content_zoom = z
        self.pan = [mx - self.x - gx * z, my - self.y - gy * z]
        self.invalidate(layout=True)
        return True

    def frame_all(self):
        nodes = self.nodes
        if not nodes or self.w <= 0:
            return
        x0 = min(n.gpos[0] for n in nodes)
        y0 = min(n.gpos[1] for n in nodes)
        x1 = max(n.gpos[0] + n.w / self.content_zoom for n in nodes)
        y1 = max(n.gpos[1] + n.h / self.content_zoom for n in nodes)
        z = max(0.3, min(1.5, min((self.w - 80) / max(1, x1 - x0), (self.h - 80) / max(1, y1 - y0))))
        z = round(z * 50) / 50
        self.content_zoom = z
        self.pan = [(self.w - (x1 - x0) * z) / 2 - x0 * z, (self.h - (y1 - y0) * z) / 2 - y0 * z]
        self.invalidate(layout=True)

    def duplicate_selected(self):
        src = [n for n in self.selected if n.type_name in self.registry]
        if not src:
            return
        mapping = {}
        for n in src:
            c = self.add_node(self.create(n.type_name), (n.gpos[0] + 30, n.gpos[1] + 30))
            for s in n.inputs:
                f, g = s.slot.field, c.socket(s.name, False).slot.field
                if f is not None and g is not None:
                    g.set_value(f.value, emit=False)
            mapping[n.id] = c
        for lk in list(self.links):
            if lk.out.node.id in mapping and lk.inp.node.id in mapping:
                self.connect(mapping[lk.out.node.id].socket(lk.out.name, True),
                             mapping[lk.inp.node.id].socket(lk.inp.name, False), undo=False)
        new = list(mapping.values())
        self._set_selection(new, new[-1])
        ui = self.ui
        if ui is not None:
            ui.undo.push("Duplicate", lambda: self.remove_nodes(new, undo=False), lambda: self._restore(new, []))

    def open_add_menu(self, ui, at):
        self._menu_gpos = self.to_graph(*at)
        tree = {}
        for path in sorted(self.registry):
            parts = path.split("/")
            d = tree
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = path

        def build(d):
            items = []
            for k, v in d.items():
                if isinstance(v, dict):
                    items.append(MenuItem(k, submenu=build(v), icon="folder"))
                else:
                    items.append(MenuItem(k, (lambda p=v: self._add_from_menu(p)), icon="node"))
            return items
        ui.ensure_text_input()
        ui.open_popup(Menu(build(tree), title="Add Node"), at=at)

    def _add_from_menu(self, path):
        n = self.add_node(self.create(path), self._menu_gpos, undo=True)
        self._set_selection([n], n)

    def on_key(self, ui, e):
        k = e.key
        if k in (pygame.K_DELETE, pygame.K_x) and not e.mod & CTRL:
            if self.selected:
                self.remove_nodes(self.selected)
        elif k == pygame.K_a and e.mod & SHIFT:
            self.open_add_menu(ui, ui.mouse)
        elif k == pygame.K_a and e.mod & CTRL:
            self._set_selection(self.nodes)
        elif k == pygame.K_d and e.mod & (SHIFT | CTRL):
            self.duplicate_selected()
        elif k in (pygame.K_HOME, pygame.K_f):
            self.frame_all()
        elif k == pygame.K_ESCAPE:
            self._temp = self._box = self._knife = None
            self._mode = None
            ui.set_focus(None)
        else:
            return False
        self.invalidate()
        return True

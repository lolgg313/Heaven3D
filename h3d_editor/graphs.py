# SPDX-License-Identifier: MIT
"""Graph tab: visual scripting with Vitrail's node editor.

The node types come from the engine (``h3d.VS_NODES``), so whatever the
engine can run, the editor can draw -- including nodes packages and project
scripts register with ``@vs_node``.  The graph is stored on the selected
object's VisualScript blueprint and runs in Play and in exported games.
"""
import json
import time

from vitrail import Button, Label, Row
from vitrail.nodes import Node, NodeEditor


def make_factory(path, spec):
    title = path.split("/")[-1]

    def factory():
        ins = [(s[0], s[1], s[2] if len(s) > 2 else None) for s in spec["inputs"]]
        outs = [(s[0], s[1]) for s in spec["outputs"]]
        n = Node(title, inputs=ins, outputs=outs, category=spec.get("category", "default"),
                 width=190 if spec["kind"] != "pure" else 160)
        n.tooltip = spec.get("doc") or None
        return n
    return factory


class GraphTab:
    def __init__(self, editor, page):
        self.ed = editor
        self.h3d = editor.h3d
        bar = Row(spacing=3, parent=page)
        self.title = Label("Visual script", bold=True, parent=bar)
        self.add_btn = Button("Add Visual Script to selection", self.add_script, icon="add", primary=True,
                              expand=0, parent=bar)
        Button("Frame all", lambda: self.nodes.frame_all(), icon="fullscreen", expand=0, parent=bar,
               tooltip="Home / F")
        Button("Starter graph", self.starter, icon="magic", expand=0, parent=bar,
               tooltip="On Tick -> Rotate: a working example to build on")
        Label("Right-click or Shift+A: add node   drag pins: connect   Ctrl+right-drag: cut wires",
              dim=True, parent=bar)
        self.nodes = NodeEditor(fill=True, parent=page)
        self.registered = set()
        self.register_all()
        self.nodes.graph_changed.connect(self._changed)
        self.bp = None
        self.node = None
        self._loading = False
        self._dirty_at = None

    def register_all(self):
        for path, spec in self.h3d.VS_NODES.items():
            if path not in self.registered:
                self.nodes.register(path, make_factory(path, spec))
                self.registered.add(path)

    # -- binding to the selection ------------------------------------------------------
    def find_bp(self, n):
        if n is None:
            return None
        for bp in n.blueprints:
            if type(bp).__name__ == "VisualScript":
                return bp
        return None

    def bind(self, n):
        self.flush(force=True)          # never lose the last edits of the graph we leave
        self.register_all()
        bp = self.find_bp(n)
        if bp is self.bp and n is self.node:
            return
        self.node, self.bp = n, bp
        self._loading = True
        try:
            if bp is not None:
                self.nodes.from_dict(bp.properties.get("graph") or {"nodes": [], "links": []})
            else:
                self.nodes.from_dict({"nodes": [], "links": []})
        finally:
            self._loading = False
        self.title.text = ("Visual script: %s" % n.name) if bp is not None else (
            "Select an object" if n is None else "%s has no visual script" % n.name)
        self.title.invalidate(layout=True)
        self.add_btn.visible = n is not None and bp is None
        self.nodes.enabled = bp is not None
        self.ed.ui.invalidate(layout=True)

    def add_script(self):
        n = self.ed.engine.selected
        if n is None:
            self.ed.toast("Select an object first")
            return
        self.ed.add_blueprint(n, "VisualScript")
        self.node = None
        self.bind(n)

    def starter(self):
        if self.bp is None:
            self.add_script()
        if self.bp is None:
            return
        ed = self.nodes
        a = ed.add_node(ed.create("Events/On Tick"), (40, 40))
        b = ed.add_node(ed.create("Transform/Rotate"), (300, 40))
        b.socket("Y", False).slot.field.set_value(90.0)
        ed.connect(a.socket("Then", True), b.socket("In", False))
        c = ed.add_node(ed.create("Events/On Begin Play"), (40, 260))
        d = ed.add_node(ed.create("Actions/Print"), (300, 260))
        d.socket("Text", False).slot.field.set_value("Visual script running on " + self.node.name)
        ed.connect(c.socket("Then", True), d.socket("In", False))
        self.ed.ui.call_later(0.05, ed.frame_all)

    # -- writing back ----------------------------------------------------------------------
    def _changed(self):
        if not self._loading and self.bp is not None:
            self._dirty_at = time.perf_counter()

    def flush(self, force=False):
        if self._dirty_at is None or self.bp is None:
            return
        if not force and time.perf_counter() - self._dirty_at < 0.25:
            return
        self._dirty_at = None
        d = self.nodes.to_dict()
        d.pop("view", None)
        try:
            json.dumps(d)
        except (TypeError, ValueError):
            for nd in d["nodes"]:
                nd["values"] = {k: v for k, v in nd["values"].items()
                                if isinstance(v, (int, float, str, bool, list))}
        self.bp.properties["graph"] = d
        if hasattr(self.bp, "reload"):
            self.bp.reload()

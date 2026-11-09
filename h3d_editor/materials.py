# SPDX-License-Identifier: MIT
"""Material tab: node materials (.vmat), Blender-style.

  * New / Open a .vmat (or double-click one in Assets)
  * build the look from nodes (right-click or Shift+A to add: Noise, Voronoi,
    Bricks, Checker, Image, Color Ramp, Mix, Math, Bump, Normal Map ...)
    wired into the Material Output
  * drop an image from Assets onto the graph to get an Image node
  * every edit is baked and shown live on every object that uses the material
  * drag the .vmat from Assets onto an object (viewport, Outliner or the
    Inspector) to give it the material, or press "Assign to selection"
"""
import os
import time

import pygame

from vitrail import Button, Dropdown, FileDialog, Label, Row
from vitrail.images import Image
from vitrail.nodes import Node, NodeEditor

from .scripting import project_dir

MATERIAL_FILTERS = [("Heaven3D material", "*.vmat"), ("All files", "*")]
IMAGE_EXT = (".png", ".jpg", ".jpeg", ".bmp", ".tga", ".webp", ".gif")


def make_factory(path, spec):
    title = path.split("/")[-1]

    def factory():
        ins = []
        for name, typ, default in spec["inputs"]:
            if typ == "color" and default is not None and len(default) == 3:
                default = tuple(default) + (1.0,)
            ins.append((name, typ, default))
        n = Node(title, inputs=ins, outputs=[(o[0], o[1]) for o in spec["outputs"]],
                 category=spec.get("category", "default"), width=230 if title == "Material Output" else 180)
        n.tooltip = spec.get("doc") or None
        return n
    return factory


class MaterialNodeEditor(NodeEditor):
    """The node editor, plus: drop an image asset to get an Image node."""

    def __init__(self, tab, **kw):
        super().__init__(**kw)
        self.tab = tab

    def accepts_drop(self, payload):
        return (isinstance(payload, dict) and payload.get("type") == "asset" and self.tab.path is not None
                and any(getattr(a, "path", "").lower().endswith(IMAGE_EXT) for a in payload.get("assets", [])))

    def on_drop(self, ui, payload, x, y):
        gx, gy = self.to_graph(x, y)
        for i, a in enumerate(payload.get("assets", [])):
            p = getattr(a, "path", "")
            if not p.lower().endswith(IMAGE_EXT):
                continue
            n = self.add_node(self.create("Texture/Image"), (gx + 20 * i, gy + 20 * i))
            rel = os.path.relpath(p, os.path.dirname(self.tab.path)).replace("\\", "/")
            n.socket("File", False).slot.field.set_value(rel)
        self.graph_changed.emit()


class MaterialTab:
    def __init__(self, editor, page):
        self.ed = editor
        self.h3d = editor.h3d
        self.engine = editor.engine
        self.path = None
        bar = Row(spacing=3, parent=page)
        self.title = Label("No material open", bold=True, icon="material", parent=bar)
        Button("New", self.new_dialog, icon="add", expand=0, parent=bar, tooltip="New material in assets/materials")
        Button("Open...", self.open_dialog, icon="folder-open", expand=0, parent=bar)
        self.assign_btn = Button("Assign to selection", self.assign_selected, icon="cube", primary=True, expand=0,
                                 parent=bar, tooltip="Or drag the .vmat from Assets onto an object")
        self.res = Dropdown([(256, "256 px"), (512, "512 px"), (1024, "1024 px"), (2048, "2048 px")], 512,
                            label="Bake", expand=0, width=150, parent=bar, on_change=lambda _v: self._changed())
        Button("Frame all", lambda: self.nodes.frame_all(), icon="fullscreen", expand=0, parent=bar)
        self.preview = Image(None, max_px=96, width=34, height=34, expand=0, radius=4, parent=bar)
        self.status = Label("Right-click or Shift+A: add node   drag an image here: Image node", dim=True, parent=bar)
        self.nodes = MaterialNodeEditor(self, fill=True, parent=page)
        self.registered = set()
        self.register_all()
        self.nodes.graph_changed.connect(self._changed)
        self._loading = False
        self._dirty_at = None
        self.nodes.enabled = False
        self.assign_btn.enabled = False

    def register_all(self):
        for path, spec in self.h3d.MATERIAL_NODES.items():
            if path not in self.registered:
                self.nodes.register(path, make_factory(path, spec))
                self.registered.add(path)

    # -- files -------------------------------------------------------------------------
    def folder(self):
        return os.path.join(project_dir(self.engine), "assets", "materials")

    def new_dialog(self):
        os.makedirs(self.folder(), exist_ok=True)

        def made(p):
            if not p:
                return
            try:
                path = self.h3d.material_asset_create(p)
            except Exception as ex:
                self.ed.toast("Couldn't create the material: %s" % ex, error=True)
                return
            self.open(path)
            self.ed.refresh_assets()
            self.ed.toast("New material " + os.path.basename(path))
        FileDialog.save_file(self.ed.ui, made, filters=MATERIAL_FILTERS, start_dir=self.folder(),
                             default_name="New Material.vmat", title="New material")

    def open_dialog(self):
        start = self.folder() if os.path.isdir(self.folder()) else project_dir(self.engine)
        FileDialog.open_file(self.ed.ui, lambda p: self.open(p) if p else None, filters=MATERIAL_FILTERS,
                             start_dir=start, title="Open material")

    def open(self, path):
        self.flush(force=True)
        self.register_all()
        path = os.path.abspath(path)
        data = self.h3d.material_vmat_read(path)
        graph = self.h3d._mg_graph_of(data)
        self.path = path
        self._loading = True
        try:
            self.nodes.from_dict(graph)
            self.res.set_value(int(data.get("resolution") or 512), emit=False)
        finally:
            self._loading = False
        self.title.text = os.path.basename(path)
        self.title.invalidate(layout=True)
        self.nodes.enabled = True
        self.assign_btn.enabled = True
        self.ed.open_tab("Material")
        self.ed.ui.layout()
        self.nodes.frame_all()
        self._show_preview()
        return path

    # -- writing / baking ------------------------------------------------------------------
    def _changed(self, *_a):
        if not self._loading and self.path is not None:
            self._dirty_at = time.perf_counter()

    def graph(self):
        d = self.nodes.to_dict()
        d.pop("view", None)
        for nd in d["nodes"]:
            nd["values"] = {k: (list(v) if isinstance(v, tuple) else v) for k, v in nd["values"].items()
                            if isinstance(v, (int, float, str, bool, list, tuple))}
        return d

    def flush(self, force=False):
        if self._dirty_at is None or self.path is None:
            return
        if not force and time.perf_counter() - self._dirty_at < 0.35:
            return
        self._dirty_at = None
        t0 = time.perf_counter()
        try:
            self.h3d.material_vmat_write(self.path, self.graph(), resolution=self.res.value)
            n = self.engine.refresh_material_asset(self.path)
        except Exception as ex:
            self.status.text = "Bake failed: %s" % ex
            self.ed.log("[Material] %s: %s" % (os.path.basename(self.path), ex), "ERROR")
            return
        ms = (time.perf_counter() - t0) * 1000
        self.status.text = "Baked in %d ms%s" % (ms, " - %d object%s updated" % (n, "" if n == 1 else "s")
                                                  if n else "")
        self.status.invalidate()
        self._show_preview()
        if self.ed.assets is not None:
            self.ed.refresh_assets()

    def _show_preview(self):
        try:
            vals = self.h3d.material_asset_build(self.path)
            if vals.get("base_color_tex"):
                surf = pygame.image.load(vals["base_color_tex"])
            else:
                surf = pygame.Surface((32, 32))
                surf.fill([int(max(0, min(1, c)) * 255) for c in vals.get("base_color", (0.8, 0.8, 0.8))[:3]])
            self.preview.source = surf
            self.preview.invalidate()
        except Exception:
            self.preview.source = None

    # -- assigning --------------------------------------------------------------------------------
    def assign_selected(self):
        n = self.engine.selected
        if n is None or self.path is None:
            self.ed.toast("Select an object first", error=True)
            return
        self.flush(force=True)
        self.ed.assign_material(n, self.path)

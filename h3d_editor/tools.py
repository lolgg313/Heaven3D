# SPDX-License-Identifier: MIT
"""Tool panels in Vitrail: Buildings, Terrain and Particles.

A tool opens in the right-hand dock in place of the Inspector (the Outliner
and the bottom panel stay where they are) and closes with its X button,
Esc in the viewport, or by opening another tool.  The tools themselves
(painting buildings, sculpting terrain, spawning particles) are the engine's
own; these panels only drive them, so the classic editor (--classic-ui)
keeps its panels unchanged.
"""
import math
import os
import time

from vitrail import (Button, Checkbox, ColorSwatch, Column, Dropdown, InputDialog, Label, MessageBox,
                     NumberField, Panel, Row, ScrollArea)


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


class ToolPanels:
    TITLES = {"buildings": ("Buildings", "home"), "terrain": ("Terrain", "shapes"),
              "particles": ("Particle Studio", "sparkle")}

    def __init__(self, editor, dock):
        self.ed = editor
        self.engine = editor.engine
        self.h3d = editor.h3d
        self.active = None
        self.box = Column(spacing=4, fill=True, parent=dock)
        head = Row(spacing=4, parent=self.box)
        self.title = Label("", bold=True, icon="home", parent=head)
        Button(icon="close", on_click=self.close, tooltip="Close (Esc)", expand=0, parent=head)
        self.body = Column(padding=4, spacing=6)
        self.scroll = ScrollArea(self.body, fill=True, parent=self.box)
        self.box.visible = False
        self.binds = []                 # (widget, getter) refreshed from the tool's state
        self.highlights = []            # (button, fn -> bool)
        self._next = 0.0
        self._preview_next = 0.0

    # -- open / close ------------------------------------------------------------------------
    def toggle(self, name):
        if self.active == name:
            self.close()
        else:
            self.open(name)

    def open(self, name):
        e = self.engine
        if getattr(e, "_playing", False):
            self.ed.toast("Stop the game to use the %s tool" % self.TITLES[name][0], error=True)
            return
        if self.active is not None:
            self.close()
        try:
            ok = {"buildings": self._open_buildings, "terrain": self._open_terrain,
                  "particles": self._open_particles}[name]()
        except Exception as ex:
            self.ed._report(ex)
            self.ed.toast("%s: %s" % (type(ex).__name__, ex), error=True)
            ok = False
        if not ok:
            return
        self.active = name
        title, icon = self.TITLES[name]
        self.title.text = title
        self.title.icon = icon
        self.title.invalidate(layout=True)
        self.scroll.scroll = 0                    # a newly opened tool starts at its top
        self.rebuild()
        self.box.visible = True
        self.ed.apply_visibility()

    def close(self):
        name, self.active = self.active, None
        e = self.engine
        try:
            if name == "buildings" and getattr(e, "procedural_generator", None) is not None:
                e.procedural_generator.close_panel()
                t = getattr(e, "building_tool", None)
                if t is not None:
                    t.close()
            elif name == "terrain" and getattr(e, "_tp_editor", None) is not None:
                e._tp_editor.close()
            elif name == "particles":
                st = self._studio(create=False)
                if st is not None:
                    st._disable_preview()
        except Exception as ex:
            self.ed._report(ex)
        self.body.clear()
        self.binds, self.highlights = [], []
        self.box.visible = False
        self.ed.apply_visibility()

    def rebuild(self):
        keep = self.scroll.scroll
        self.body.clear()
        self.binds, self.highlights = [], []
        try:
            {"buildings": self._buildings, "terrain": self._terrain, "particles": self._particles}[self.active]()
        except Exception as ex:
            import traceback
            Label("Panel error: %s" % ex, parent=self.body)
            self.ed.log(traceback.format_exc(), "ERROR")
        self.scroll.scroll = keep
        self.body.invalidate(layout=True)

    def tick(self, now):
        """Follow the tool (keys like [ ] change the brush; Esc closes it)."""
        if self.active is None:
            return
        e = self.engine
        if getattr(e, "_playing", False):
            self.close()
            return
        if self.active == "buildings":
            gen = getattr(e, "procedural_generator", None)
            if gen is None or not gen.panel_open:
                self.close()
                return
            t = getattr(e, "building_tool", None)
            if t is not None and not getattr(self, "classic", False):
                if not t.active:                      # e.g. after undoing / reloading
                    t.open()
                if id(t.target()) != getattr(self, "_bk_seen", None):
                    self._bk_seen = id(t.target())
                    self.rebuild()                    # another building is being edited now
        elif self.active == "terrain":
            ed = getattr(e, "_tp_editor", None)
            if ed is None or not ed.panel_open:
                self.close()
                return
        elif self.active == "particles":
            st = self._studio(create=False)
            if st is not None and st._preview_on and now >= self._preview_next:
                self._preview_next = now + 0.1
                try:
                    st._update_preview()
                except Exception as ex:
                    self.ed._report(ex)
        if now < self._next:
            return
        self._next = now + 0.15
        for w, get in self.binds:
            if getattr(w, "_edit_start", None) is not None or getattr(w, "editing", False):
                continue
            try:
                v = get()
            except Exception:
                continue
            if isinstance(w, Label):
                if w.text != v:
                    w.text = v
                    w.invalidate()
            elif isinstance(w, ColorSwatch):
                if tuple(w.value) != tuple(v):
                    w.set_value(tuple(v), emit=False)
            elif w.value != v:
                w.set_value(v, emit=False)
        for b, on in self.highlights:
            try:
                want = bool(on())
            except Exception:
                continue
            if b.primary != want:
                b.primary = want
                b.invalidate()

    def bind(self, w, get):
        self.binds.append((w, get))
        return w

    def hl(self, b, on):
        self.highlights.append((b, on))
        b.primary = bool(on())
        return b

    def section(self, title, icon=None):
        return Panel(title, icon=icon, parent=self.body)

    @staticmethod
    def grid(parent, items, cols=2):
        """Rows of ``cols`` buttons; items: (label, fn, icon or None, tooltip or None)."""
        out = []
        row = None
        for i, (label, fn, icon, tip) in enumerate(items):
            if i % cols == 0:
                row = Row(spacing=3, parent=parent)
            out.append(Button(label, fn, icon=icon, tooltip=tip, parent=row))
        if row is not None:
            for _ in range((-len(items)) % cols):
                Label("", parent=row)
        return out

    # =================================================================== buildings
    def _gen(self):
        e = self.engine
        gen = getattr(e, "procedural_generator", None)
        if gen is None and hasattr(self.h3d, "ProceduralSectorGenerator"):
            gen = self.h3d.ProceduralSectorGenerator(e)
            e.procedural_generator = gen
        return gen

    def _open_buildings(self):
        gen = self._gen()
        if gen is None:
            self.ed.toast("The building editor isn't available in this build", error=True)
            return False
        gen.open_panel()
        gen.set_mode("generate")
        self.classic = False
        t = self._bk()
        if t is not None:
            t.on_edit = self._bk_on_edit
            t.open()
            self._bk_seen = id(t.target())
        return True

    # ---- Building Maker: rooms painted on a grid, baked into one object ----
    def _bk(self):
        fn = getattr(self.h3d, "_bk_tool", None)
        return fn(self.engine) if fn is not None else None

    def _bk_find(self, node, name):
        t = self._bk()
        if t is not None and t._alive(node):
            return node
        for n in (t.buildings() if t is not None else ()):
            if n.name == name:
                return n
        return None

    def _bk_on_edit(self, node, before, after, label, created):
        ed = self.ed
        if created:
            ed._push_created(node, label)
            ed.refresh_outliner() if hasattr(ed, "refresh_outliner") else None
            self.rebuild()
            return
        name = node.name
        t = self._bk()

        def put(params):
            n = self._bk_find(node, name)
            if n is not None:
                t.restore(n, params)
        ed.ui.undo.push(label, lambda: put(before), lambda: put(after))

    def _bk_do(self, fn, *a, **kw):
        """Run a tool action with errors reported, then refresh the panel."""
        try:
            fn(*a, **kw)
        except Exception as ex:
            self.ed._report(ex)
            self.ed.toast("%s: %s" % (type(ex).__name__, ex), error=True)
        self.rebuild()

    def _bk_classic(self, on):
        gen = self._gen()
        t = self._bk()
        self.classic = bool(on)
        if on:
            t.close()
        else:
            gen.set_mode("generate")
            t.open()
        self.rebuild()

    def _bk_generate(self):
        t = self._bk()
        g = self._bk_gen
        n = t.generate(g["w"], g["d"], g["floors"], g["damage"] / 100.0)
        self.ed.select(n)
        self.ed.toast("Generated " + n.name)

    def _bk_village(self):
        t = self._bk()
        nodes = t.generate_village(8, max(self._bk_gen["damage"], 0) / 100.0)
        self.ed.select(nodes[-1])
        self.ed.toast("Generated a village of %d houses" % (len(nodes) - 1))

    def _buildings(self):
        t = self._bk()
        if t is None or getattr(self, "classic", False):
            if t is not None:
                Button("Back to the Building Maker", lambda: self._bk_classic(False), icon="home", primary=True,
                       parent=self.body, tooltip="Rooms painted on a grid, with walls, roofs and stairs made for you")
            return self._buildings_classic()
        h = self.h3d
        node = t.target()
        d = h.bk_data(node) if node is not None else None
        if not hasattr(self, "_bk_gen"):
            self._bk_gen = {"w": 4, "d": 3, "floors": 2, "damage": 0}
        # ---- building ----
        p = self.section("Building", "home")
        blds = t.buildings()
        opts = [(-1, "New building (paint to start)")] + [(i, n.name) for i, n in enumerate(blds)]
        cur = next((i for i, n in enumerate(blds) if n is node), -1)
        Dropdown(opts, cur, label="Editing", parent=p,
                 on_change=lambda i: self._bk_do(lambda: (t.set_node(blds[i] if i >= 0 else None),
                                                          self.ed.select(blds[i]) if i >= 0 else None)))
        Dropdown([(k, h.BK_STYLES[k]["label"]) for k in h.BK_STYLE_ORDER], t.style, label="Style", parent=p,
                 on_change=lambda k: self._bk_do(t.set_style, k))
        # ---- tools ----
        p = self.section("Tools", "edit")
        btns = self.grid(p, [(label, (lambda k=k: self._bk_do(setattr, t, "tool", k)), None, tip)
                             for k, label, tip in h.BK_TOOLS])
        for b, (k, _l, _tip) in zip(btns, h.BK_TOOLS):
            self.hl(b, lambda k=k: t.tool == k)
        tip = next(tp for k, _l, tp in h.BK_TOOLS if k == t.tool)
        Label(tip, dim=True, parent=p)
        Label("Ctrl or Shift + drag removes.  Right-drag looks around.", dim=True, parent=p)
        # ---- finish ----
        p = self.section("Room finish", "palette")
        Dropdown([(k, v["label"]) for k, v in h.BK_FINISHES.items()], t.finish_name(), label="Finish", parent=p,
                 on_change=lambda k: setattr(t, "finish", k))
        # ---- storey ----
        p = self.section("Storey", "layers")
        r = Row(spacing=3, parent=p)
        Button("", lambda: t.set_level(t.level - 1), icon="minus", expand=0, tooltip="Storey down (PageDown)", parent=r)
        self.bind(NumberField("Storey", t.level, integer=True, min=-4, max=40, parent=r,
                              on_change=lambda v: t.set_level(int(v))), lambda: t.level)
        Button("", lambda: t.set_level(t.level + 1), icon="plus", expand=0, tooltip="Storey up (PageUp)", parent=r)
        self.bind(Checkbox("Hide the storeys above", t.cut, parent=p, on_change=lambda v: t.set_cut(v)),
                  lambda: t.cut)
        # ---- roof ----
        if d is not None:
            p = self.section("Roof", "shapes")
            Dropdown([("gable", "Gable"), ("hip", "Hipped"), ("flat", "Flat with parapet"), ("none", "No roof")],
                     d.roof_type(), label="Shape", parent=p, on_change=lambda k: self._bk_do(t.set_roof, type_=k))
            NumberField("Pitch (deg)", round(d.roof_pitch()), integer=True, min=5, max=65, parent=p,
                        on_change=lambda v: self._bk_do(t.set_roof, pitch=float(v)))
        # ---- generator ----
        p = self.section("Quick house", "sparkle")
        g = self._bk_gen
        r = Row(spacing=3, parent=p)
        NumberField("Width", g["w"], integer=True, min=2, max=24, parent=r, on_change=lambda v: g.update(w=int(v)))
        NumberField("Depth", g["d"], integer=True, min=2, max=24, parent=r, on_change=lambda v: g.update(d=int(v)))
        r = Row(spacing=3, parent=p)
        NumberField("Floors", g["floors"], integer=True, min=1, max=8, parent=r,
                    on_change=lambda v: g.update(floors=int(v)))
        NumberField("Damage %", g["damage"], integer=True, min=0, max=90, parent=r,
                    on_change=lambda v: g.update(damage=int(v)))
        Button("Generate house", lambda: self._bk_do(self._bk_generate), icon="plus", primary=True, parent=p,
               tooltip="A new house in the current style, in front of the camera")
        Button("Generate village", lambda: self._bk_do(self._bk_village), icon="sparkle", parent=p,
               tooltip="Eight varied houses along a cobbled street, some shelled (uses Damage %)")
        # ---- info ----
        p = self.section("Scene", "info")
        self.bind(Label("", parent=p), self._bk_stats)
        Button("Classic sector generator...", lambda: self._bk_classic(True), parent=p,
               tooltip="The older module-by-module generator (military, office, sci-fi, lab...)")

    def _bk_stats(self):
        t = self._bk()
        n = t.target()
        if t.message:
            msg, t.message = t.message, ""
            self.ed.toast(msg, error=True)
        if n is None:
            return "No building yet: pick Room and drag in the viewport"
        s = self.h3d.bk_stats(n)
        return "%s: %d cells, %s triangles in %d meshes" % (n.name, s["cells"], "{:,}".format(s["triangles"]),
                                                             s["meshes"])

    def _buildings_classic(self):
        gen = self._gen()
        h = self.h3d
        B = h.BiomeManager
        p = self.section("Mode", "home")
        r = Row(spacing=3, parent=p)
        for label, mode, tip in (("Generate", "generate", "Make whole sectors with the button below"),
                                 ("Paint", "paint", "Click in the viewport to place the module picked below"),
                                 ("Manual", "manual", "Select and move modules with the gizmo")):
            b = Button(label, lambda m=mode: gen.set_mode(m), tooltip=tip, parent=r)
            self.hl(b, lambda m=mode: gen.mode == m)
        Dropdown([(i, n.title()) for i, n in enumerate(B.ORDER)], gen.biome_index, label="Style",
                 parent=p, on_change=self._set_biome)
        p = self.section("Sector", "layers")
        self.bind(NumberField("Floors", gen.floors, integer=True, min=1, max=8, parent=p,
                              on_change=lambda v: setattr(gen, "floors", int(_clamp(int(v), 1, 8)))),
                  lambda: gen.floors)
        sizes = [tuple(s) for s in gen._foot_sizes]
        cur = tuple(gen.footprint) if tuple(gen.footprint) in sizes else sizes[0]
        Dropdown([(s, "%d x %d cells" % s) for s in sizes], cur, label="Footprint", parent=p,
                 on_change=lambda v: setattr(gen, "footprint", list(v)))
        r = Row(spacing=3, parent=p)
        Button("Generate sector", self._generate, icon="add", primary=True, parent=r,
               tooltip="A new building sector next to the last one")
        Button("Snap to grid", lambda: self.ed.run(gen.snap_selected_to_grid), icon="grid", parent=r,
               tooltip="Snap the selected module / sector to the 2 m grid")
        p = self.section("Paint", "edit")
        self.bind(NumberField("Floor level", gen.paint_floor, integer=True, min=0, max=16, parent=p,
                              on_change=lambda v: setattr(gen, "paint_floor", int(_clamp(int(v), 0, 16)))),
                  lambda: gen.paint_floor)
        r = Row(spacing=3, parent=p)
        Button("", lambda: setattr(gen, "paint_rot", (gen.paint_rot - 1) % 4), icon="undo", expand=0,
               tooltip="Turn left ( [ )", parent=r)
        self.bind(Label("", parent=r), lambda: "Rotation %d deg" % (gen.paint_rot % 4 * 90))
        Button("", lambda: setattr(gen, "paint_rot", (gen.paint_rot + 1) % 4), icon="redo", expand=0,
               tooltip="Turn right ( ] )", parent=r)
        Label("Modules (click one, then click in the viewport):", dim=True, parent=p)
        mods = gen._palette_modules()
        btns = self.grid(p, [(k.replace("_", " "), (lambda k=k: gen.choose_module(k)), None, None) for k in mods])
        for b, k in zip(btns, mods):
            self.hl(b, lambda k=k: gen.mode == "paint" and gen.paint_key == k)
        Label("Left click: place   Right click or Ctrl+click: erase", dim=True, parent=p)
        Label("R / [ ]: rotate   Alt+drag: orbit the camera", dim=True, parent=p)
        p = self.section("Scene", "info")
        self.bind(Label("", parent=p), self._building_stats)

    def _set_biome(self, idx):
        gen = self._gen()
        gen.biome_index = int(idx) % len(self.h3d.BiomeManager.ORDER)
        mods = gen._palette_modules()
        if gen.paint_key not in mods:
            gen.paint_key = "wall" if "wall" in mods else mods[0]
        self.rebuild()                    # the module palette depends on the style

    def _generate(self):
        gen = self._gen()
        before = gen._gen_count
        self.ed.run(gen.panel_generate)
        n = self.engine.selected
        if n is not None and gen._gen_count != before:
            self.ed._push_created(n, "Generate " + n.name)
            self.ed.toast("Generated " + n.name)

    def _building_stats(self):
        mods = sectors = 0
        for n in self.engine.scene.root.traverse():
            if n.mesh_type == "procedural_module":
                mods += 1
        for n in self.engine.scene.root.children:
            if n.children and n.children[0].mesh_type == "procedural_module":
                sectors += 1
        return "%d sector%s, %d modules" % (sectors, "" if sectors == 1 else "s", mods)

    # =================================================================== terrain
    def _tp(self):
        e = self.engine
        ed = getattr(e, "_tp_editor", None)
        if ed is None and hasattr(self.h3d, "_TPTerrainEditor"):
            ed = self.h3d._TPTerrainEditor(e)
            e._tp_editor = ed
        return ed

    def _open_terrain(self):
        ed = self._tp()
        if ed is None:
            self.ed.toast("The terrain editor isn't available in this build", error=True)
            return False
        ed.open()
        return True

    def _terrain(self):
        ed = self._tp()
        td = ed.terrain_data()
        if td is None:
            p = self.section("No terrain yet", "shapes")
            Label("This scene has no terrain.", parent=p)
            Button("Create 3 x 3 km terrain", self._make_terrain, icon="add", primary=True, parent=p)
            Label("Flat to start; sculpt and paint it by hand.", dim=True, parent=p)
            return
        p = self.section("Terrain", "shapes")
        Label("%.1f x %.1f km  -  %d x %d  -  %.2f m cells" % (td.size / 1000.0, td.size / 1000.0, td.res, td.res,
                                                            td.cell), dim=True, parent=p)
        p = self.section("Sculpt", "edit")
        sculpt = [(k, lab) for k, lab in ed.TOOLS if k != "paint"]
        btns = self.grid(p, [(lab, (lambda k=k: setattr(ed, "tool", k)), None, None) for k, lab in sculpt], cols=3)
        for b, (k, _l) in zip(btns, sculpt):
            self.hl(b, lambda k=k: ed.tool == k)
        p = self.section("Paint", "material")
        btns = self.grid(p, [(lab, (lambda li=li: (setattr(ed, "layer", li), setattr(ed, "tool", "paint"))),
                              None, "Paint %s (slopes fade to rock)" % lab.lower()) for lab, li in ed.LAYERS], cols=3)
        for b, (_lab, li) in zip(btns, ed.LAYERS):
            self.hl(b, lambda li=li: ed.tool == "paint" and ed.layer == li)
        p = self.section("Brush", "target")
        self.bind(NumberField("Size (m)", ed.brush_size, min=0.5, max=200.0, soft_min=0.5, soft_max=60.0, parent=p,
                              on_change=lambda v: setattr(ed, "brush_size", float(_clamp(v, 0.5, 200.0)))),
                  lambda: float(ed.brush_size))
        self.bind(NumberField("Strength (%)", ed.brush_strength * 100.0, min=5, max=100, slider=True, parent=p,
                              on_change=lambda v: setattr(ed, "brush_strength", float(_clamp(v, 5, 100)) / 100.0)),
                  lambda: float(ed.brush_strength) * 100.0)
        Label("[ ] change the brush size   Alt+drag: orbit", dim=True, parent=p)
        p = self.section("Grass", "leaf")
        self.bind(Checkbox("Grass", bool(td.grass_enabled), parent=p,
                           on_change=lambda v: setattr(td, "grass_enabled", bool(v))), lambda: bool(td.grass_enabled))
        self.bind(NumberField("Height (cm)", td.grass_height * 100.0, min=5, max=100, slider=True, parent=p,
                              on_change=lambda v: setattr(td, "grass_height", float(_clamp(v, 5, 100)) / 100.0)),
                  lambda: float(td.grass_height) * 100.0)
        self.bind(NumberField("Density", td.grass_density, min=0.1, max=3.0, slider=True, parent=p,
                              on_change=lambda v: setattr(td, "grass_density", float(_clamp(v, 0.1, 3.0)))),
                  lambda: float(td.grass_density))
        self.bind(NumberField("Draw distance (m)", td.grass_radius, min=20, max=200, slider=True, parent=p,
                              on_change=lambda v: setattr(td, "grass_radius", float(_clamp(v, 20, 200)))),
                  lambda: float(td.grass_radius))
        Button("Regrow grass", lambda: self.ed.run(td.regrow_grass), icon="refresh", parent=p)
        p = self.section("Rock on slopes", "shapes")
        self.bind(NumberField("Rock from (deg)", self._slope_deg(td), min=20, max=75, slider=True, parent=p,
                              on_change=lambda v: self._set_slope(td, v)), lambda: self._slope_deg(td))

    @staticmethod
    def _slope_deg(td):
        return round(math.degrees(math.acos(_clamp(1.0 - td.slope_rock_start, 0.0, 1.0))), 1)

    @staticmethod
    def _set_slope(td, deg):
        d = _clamp(float(deg), 20.0, 75.0)
        td.slope_rock_start = 1.0 - math.cos(math.radians(d))
        td.slope_rock_end = 1.0 - math.cos(math.radians(_clamp(d + 18.0, 25.0, 85.0)))

    def _make_terrain(self):
        ed = self._tp()
        n = self.ed.run(ed.ensure_terrain)
        if n is not None:
            self.ed.toast("Terrain created - pick a tool and click in the viewport")
        self.rebuild()

    # =================================================================== particles
    def _studio(self, create=True):
        e = self.engine
        st = getattr(e, "_gu_studio", None)
        if st is None and create and hasattr(self.h3d, "_GUParticleStudio"):
            st = self.h3d._GUParticleStudio(e)
            e._gu_studio = st
        return st

    def _open_particles(self):
        st = self._studio()
        if st is None:
            self.ed.toast("The particle studio isn't available in this build", error=True)
            return False
        st.panel_open = False             # the classic modal window stays shut
        st._refresh_presets()
        return True

    def _particles(self):
        st = self._studio()
        h = self.h3d
        presets = st._get_presets()
        builtin = st._builtin_names()
        lib = [(n, c) for n, c in presets.items() if n in builtin]
        mine = [(n, c) for n, c in presets.items() if n not in builtin]
        p = self.section("Library", "sparkle")
        Label("Click one to drop it in front of the camera.", dim=True, parent=p)
        for i in range(0, len(lib), 2):
            r = Row(spacing=3, parent=p)
            for name, cfg in lib[i:i + 2]:
                self._preset_card(r, st, name, cfg)
            if len(lib[i:i + 2]) == 1:
                Label("", parent=r)
        p = self.section("My presets", "folder")
        if not mine:
            Label("None yet - make one below and press Save.", dim=True, parent=p)
        for name, cfg in mine:
            r = Row(spacing=3, parent=p)
            self._preset_card(r, st, name, cfg)
            Button("", lambda n=name: self._delete_preset(n), icon="trash", expand=0, tooltip="Delete " + name,
                   parent=r)
        p = self.section("Creator", "edit")
        c = st.custom
        for key, label, lo, hi, step, _fmt in h._GU_STUDIO_SLIDERS:
            integer = step >= 1.0 and float(step).is_integer()
            self.bind(NumberField(label, float(c[key]), min=lo, max=hi, slider=True, integer=integer, parent=p,
                                  on_change=lambda v, k=key, lo=lo, hi=hi, st_=step: self._set_custom(k, v, lo, hi,
                                                                                                    st_)),
                      lambda k=key: float(c[k]))
        types = list(h._GU_STUDIO_TYPES)
        Dropdown([(i, t.title()) for i, t in enumerate(types)], int(c["type_idx"]) % len(types), label="Type",
                 parent=p, on_change=lambda v: c.__setitem__("type_idx", int(v)))
        self.bind(Checkbox("Additive (glowing)", bool(c["additive"]), parent=p,
                           on_change=lambda v: c.__setitem__("additive", bool(v))), lambda: bool(c["additive"]))
        self.bind(ColorSwatch(tuple(c["cs"]), label="Start colour", alpha=True, parent=p,
                              on_change=lambda v: c.__setitem__("cs", [float(_clamp(x, 0, 1)) for x in v][:4])),
                  lambda: tuple(c["cs"]))
        self.bind(ColorSwatch(tuple(c["ce"]), label="End colour", alpha=True, parent=p,
                              on_change=lambda v: c.__setitem__("ce", [float(_clamp(x, 0, 1)) for x in v][:4])),
                  lambda: tuple(c["ce"]))
        r = Row(spacing=3, parent=p)
        self.bind(Checkbox("Live preview", bool(st._preview_on), parent=r, on_change=self._preview),
                  lambda: bool(st._preview_on))
        Button("Save preset...", self._save_preset, icon="save", primary=True, parent=r)
        Button("Drop it in the scene", lambda: self.ed.run(lambda: self._drop_custom(st)), icon="add", parent=p)

    def _preset_card(self, row, st, name, cfg):
        def spawn(n=name, c=cfg):
            self.ed.run(lambda: st._spawn_preset(n, c))
            sel = self.engine.selected
            if sel is not None:
                self.ed._push_created(sel, "Add " + str(n))
        col = (cfg or {}).get("color_start", (1, 1, 1, 1))
        b = Button(str(name)[:22], spawn, icon="sparkle", parent=row, tooltip="Drop '%s' in front of the camera" % name)
        try:
            b.icon_color = tuple(float(x) for x in list(col)[:3]) + (1.0,)
        except Exception:
            pass
        return b

    def _set_custom(self, key, v, lo, hi, step):
        v = float(_clamp(v, lo, hi))
        if step and step > 0:
            v = round(v / step) * step
        self._studio().custom[key] = v

    def _preview(self, on):
        st = self._studio()
        if on:
            st._preview_on = True
            self._preview_next = 0.0
        else:
            st._disable_preview()

    def _drop_custom(self, st):
        add = getattr(self.h3d, "gu_add_particle_system", None)
        if add is None:
            return
        cam = self.engine.camera
        pos = cam.position + cam.forward() * 6.0
        cfg = st._build_custom_config()
        node = add(self.engine, cfg, pos)
        if node is not None:
            self.engine.select_node(node)
            self.ed._push_created(node, "Add particles")

    def _save_preset(self):
        st = self._studio()
        existing = st._get_presets()
        k = 1
        while "Custom %d" % k in existing:
            k += 1

        def done(name):
            name = (name or "").strip()
            if not name:
                return
            saver = getattr(self.h3d, "gu_save_preset", None)
            if saver is None:
                self.ed.toast("Saving presets isn't available in this build", error=True)
                return
            cfg = st._build_custom_config()
            cfg["name"] = name
            ok = self.ed.run(lambda: saver(name, cfg))
            if ok:
                st._refresh_presets()
                self.ed.toast("Saved preset " + name)
                self.rebuild()
        InputDialog.ask(self.ed.ui, "Save particle preset", "Name", done, default="Custom %d" % k)

    def _delete_preset(self, name):
        def answer(r):
            if r == "Delete":
                st = self._studio()
                self.ed.run(lambda: st._delete_custom_preset(name))
                st._refresh_presets()
                self.rebuild()
        MessageBox.ask(self.ed.ui, "Delete the preset '%s'?" % name, answer, title="Delete preset",
                       buttons=("Delete", "Cancel"), cancel="Cancel", icon="trash")

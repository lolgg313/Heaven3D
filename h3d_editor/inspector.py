# SPDX-License-Identifier: MIT
"""The Inspector: every editable thing on the selected object.

Panels are built by looking at the object itself (its transform, material,
lights, cloud / ocean settings, spawn settings, collider, rigid body and
blueprint properties), so new engine attributes show up without editor
changes.  Fields are live: edits apply at once, the values follow the
gizmo / physics / scripts, and Ctrl+Z undoes them.
"""
import inspect
import os

import glm

from vitrail import (Button, Checkbox, ColorSwatch, Column, Dropdown, Label, NumberField, Panel, Row,
                     ScrollArea, TextField, VectorField)

# soft ranges for well-known attributes: name -> (min, max, slider?)
RANGES = {
    "metallic": (0, 1, True), "roughness": (0, 1, True), "alpha_cutoff": (0, 1, True),
    "coverage": (0, 1, True), "density": (0, 1, True), "choppiness": (0, 1, True),
    "foam_amount": (0, 2, False), "emissive_intensity": (0, 20, False), "intensity": (0, 50, False),
    "range": (0, 100, False), "inner_angle": (1, 89, False), "outer_angle": (1, 89, False),
    "speed": (0, 1, False), "altitude": (0, 3000, False), "wave_height": (0, 5, False),
    "mass": (0.01, 500, False), "friction": (0, 1, True), "restitution": (0, 1, True),
    "linear_damping": (0, 1, True), "angular_damping": (0, 1, True), "gravity_scale": (-2, 4, False),
    "health": (1, 1000, False), "radius": (0.05, 10, False), "height": (0.1, 10, False),
}
COLOR_HINTS = ("color", "tint", "water_", "emissive", "albedo", "colour")
SKIP = {"_force", "_inv_inertia_local", "orientation", "position", "velocity", "angular_velocity",
        "direction", "name"}
ICON_OF = {"sun": "sun", "point": "light", "spot": "light", "clouds": "cloud", "ocean": "drop",
           "player": "user", "enemy": "target", "bullet_pickup": "package", "health_pickup": "heart",
           "trigger": "flag", "sound": "volume", "terrain": "shapes", "mesh": "cube", "group": "layers"}


def node_kind(n):
    if n.is_sun:
        return "sun"
    if n.is_point_light:
        return "point"
    if n.is_spot_light:
        return "spot"
    if n.is_clouds:
        return "clouds"
    if n.is_ocean:
        return "ocean"
    sk = getattr(n, "spawn_kind", None)
    if sk:
        return {"player": "player", "enemy": "enemy"}.get(sk, sk if sk in ICON_OF else "trigger")
    if getattr(n, "is_terrain", False):
        return "terrain"
    if getattr(n, "is_sound_point", False) or getattr(n, "sound_point", None) is not None:
        return "sound"
    if n.mesh is not None:
        return "mesh"
    return "group"


def _is_color(name, v):
    return any(h in name for h in COLOR_HINTS) and _vec_len(v) in (3, 4)


def _vec_len(v):
    if isinstance(v, (glm.vec2, glm.vec3, glm.vec4)):
        return len(v)
    if isinstance(v, (tuple, list)) and 2 <= len(v) <= 4 and all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in v):
        return len(v)
    return 0


def _like(old, new):
    """Give ``new`` (a tuple of floats) the type ``old`` had."""
    n = len(old) if not isinstance(old, (int, float)) else 1
    vals = [float(x) for x in list(new)[:n]]
    while len(vals) < n:
        vals.append(1.0)
    if isinstance(old, glm.vec2):
        return glm.vec2(*vals)
    if isinstance(old, glm.vec3):
        return glm.vec3(*vals)
    if isinstance(old, glm.vec4):
        return glm.vec4(*vals)
    if isinstance(old, list):
        return vals
    return tuple(vals)


def _title(name):
    return name.replace("_", " ").strip().title()


def _busy(w):
    """True while the user is typing in / dragging this field."""
    if isinstance(w, VectorField):
        return any(_busy(f) for f in w.fields)
    return getattr(w, "_edit_start", None) is not None or bool(getattr(w, "editing", False))


class Inspector:
    def __init__(self, editor):
        self.ed = editor
        self.engine = editor.engine
        self.h3d = editor.h3d
        self.body = Column(padding=4, spacing=6)
        self.scroll = ScrollArea(self.body, fill=True)
        # drop a material (.vmat) or a script (.py) from Assets onto the Inspector
        self.scroll.accepts_drop = lambda payload: self.node is not None and editor.object_drop_ok(payload)
        self.scroll.on_drop = lambda ui, payload, x, y: editor.drop_assets_on_object(self.node, payload)
        self.node = None
        self.binds = []            # (widget, getter)
        self._sig = None
        self.empty_hint()

    # -- building ------------------------------------------------------------------------
    def empty_hint(self):
        self.body.clear()
        self.binds = []
        Label("Nothing selected", dim=True, parent=self.body)
        Label("Click an object in the viewport", dim=True, parent=self.body)
        Label("or in the Outliner.", dim=True, parent=self.body)

    def signature(self, n):
        """What decides which panels exist (rebuild when it changes)."""
        if n is None:
            return None
        return (id(n), bool(self.h3d.prefab_root(n)), getattr(n, "material_asset", None),
                n.material is not None, type(n.collider).__name__,
                n.rigidbody is not None,
                tuple(type(b).__name__ for b in n.blueprints), tuple(sorted(n.spawn_config or {})),
                bool(n.mesh), n.is_sun, n.is_point_light, n.is_spot_light, n.is_clouds, n.is_ocean)

    def show(self, node, force=False):
        sig = self.signature(node)
        if not force and sig == self._sig and node is self.node:
            return
        keep = self.scroll.scroll if node is self.node else 0.0
        self.node, self._sig = node, sig
        self.body.clear()
        self.binds = []
        self.extra = []
        if node is None:
            self.empty_hint()
        else:
            try:
                self._build(node)
            except Exception as ex:          # never lose the whole editor to one odd object
                import traceback
                Label("Inspector error: %s" % ex, parent=self.body)
                self.ed.log(traceback.format_exc(), "ERROR")
        self.scroll.scroll = keep
        self.body.invalidate(layout=True)

    def bind(self, widget, getter):
        self.binds.append((widget, getter))
        return widget

    def sync(self):
        """Pull current values into the fields (gizmo drags, physics, scripts)."""
        if self.node is None:
            return
        if self.signature(self.node) != self._sig:
            self.show(self.node, force=True)
            return
        for obj in getattr(self, "extra", ()):
            try:
                obj.sync()
            except Exception as ex:
                self.ed._report(ex)
        for w, get in self.binds:
            if _busy(w):
                continue
            try:
                v = get()
            except Exception:
                continue
            if isinstance(w, VectorField):
                for f, x in zip(w.fields, v):
                    if not _busy(f) and abs(float(f.value) - float(x)) > 1e-6:
                        f.set_value(float(x), emit=False)
            elif isinstance(w, ColorSwatch):
                c = tuple(float(x) for x in v)
                if w.value[:len(c)] != c[:4]:
                    w.set_value(c, emit=False)
            elif w.value != v:
                w.set_value(v, emit=False)

    # -- generic field factory ------------------------------------------------------------
    def field(self, parent, owner, attr, label=None, after=None, rng=None, options=None):
        """A live field for ``owner.attr`` (or owner[attr] for dicts)."""
        is_dict = isinstance(owner, dict)
        get = (lambda: owner.get(attr)) if is_dict else (lambda: getattr(owner, attr))
        v = get()
        label = label or _title(attr)

        def put(val):
            old = get()
            if isinstance(old, bool):
                val = bool(val)
            elif isinstance(old, int) and not isinstance(old, bool):
                val = int(round(val))
            elif isinstance(old, float):
                val = float(val)
            elif _vec_len(old):
                val = _like(old, val)
            if is_dict:
                owner[attr] = val
            else:
                setattr(owner, attr, val)
            if after is not None:
                after()

        if options is not None:
            w = Dropdown(options, v, label=label, parent=parent, on_change=put)
            return self.bind(w, get)
        if isinstance(v, bool):
            w = Checkbox(label, v, parent=parent, on_change=put)
        elif isinstance(v, (int, float)):
            lo, hi, slider = rng or RANGES.get(attr, (None, None, False))
            kw = dict(integer=isinstance(v, int), slider=slider)
            if lo is not None:
                kw.update(soft_min=lo, soft_max=hi)
            if slider:
                kw.update(min=lo, max=hi)
            w = NumberField(label, v, parent=parent, on_change=put, **kw)
        elif isinstance(v, str):
            w = TextField(v, label=label, parent=parent, on_change=put)
        elif _is_color(attr, v):
            w = ColorSwatch(tuple(float(x) for x in v), label=label, alpha=_vec_len(v) == 4,
                            parent=parent, on_change=put)
        elif _vec_len(v):
            w = VectorField(label, tuple(float(x) for x in v), axes="XYZW", on_change=put)
            parent.add(w)
        else:
            return None
        return self.bind(w, get)

    def object_fields(self, parent, obj, after=None, skip=(), only=None):
        names = only or [k for k in vars(obj) if not k.startswith("_")]
        for k in names:
            if k in SKIP or k in skip:
                continue
            try:
                self.field(parent, obj, k, after=after)
            except Exception:
                pass

    def panel(self, title, icon=None, collapsed=False):
        p = Panel(title, icon=icon, collapsed=collapsed, parent=self.body)
        key = "panel:" + title
        st = self.ed.prefs.setdefault("collapsed", {})
        if key in st:
            p.collapsed = st[key]
        p.toggled.connect(lambda *_a: st.__setitem__(key, p.collapsed))
        return p

    # -- the panels -----------------------------------------------------------------------------
    def _build(self, n):
        e, ed = self.engine, self.ed
        kind = node_kind(n)
        # header: name + visible
        head = Column(spacing=3, parent=self.body)
        r = Row(spacing=4, parent=head)
        Label("", icon=ICON_OF.get(kind, "cube"), width=22, expand=0, parent=r)
        name = TextField(n.name, parent=r, on_change=lambda v: ed.rename(n, v))
        self.bind(name, lambda: n.name)
        r2 = Row(spacing=4, parent=head)
        self.bind(Checkbox("Visible", n.visible, parent=r2, on_change=lambda v: setattr(n, "visible", bool(v))),
                  lambda: n.visible)
        self.bind(TextField(n.tag or "", label="Tag", placeholder="tag", parent=r2,
                            on_change=lambda v: setattr(n, "tag", v)), lambda: n.tag or "")
        acts = Row(spacing=3, parent=head)
        Button("Focus", lambda: ed.focus_selected(), icon="target", parent=acts)
        Button("Duplicate", lambda: ed.duplicate(), icon="copy", parent=acts)
        Button("Delete", lambda: ed.delete(), icon="trash", parent=acts)
        if getattr(ed, "prefabs", None) is not None:
            if self.h3d.prefab_root(n) is not None:
                ed.prefabs.inspector_panel(self, n)
            elif ed.prefabs.can_save(n):
                Button("Save as Prefab...", lambda: ed.prefabs.save_dialog(n), icon="prefab", parent=head,
                       tooltip="Save this object as a reusable .prefab in assets/prefabs")

        # panels packages add (e.g. the MakeHuman Character Creator): first, they are
        # what this kind of object is about
        self.extra = []
        for hook in list(getattr(self.h3d, "VT_INSPECTOR_PANELS", None) or ()):
            try:
                obj = hook(self, n)
            except Exception as ex:
                import traceback
                Label("A package panel failed: %s" % ex, parent=self.body)
                self.ed.log(traceback.format_exc(), "ERROR")
                continue
            if obj is not None:
                self.extra.append(obj)

        # transform
        p = self.panel("Transform", icon="move")
        reg = lambda: e._physics_reregister(n) if getattr(e, "_playing", False) else None  # noqa: E731
        for attr, label in (("position", "Location"), ("rotation_euler", "Rotation"), ("scale", "Scale")):
            def put(v, attr=attr):
                new = glm.vec3(*[float(x) for x in v])
                if attr == "scale":
                    new = glm.max(new, glm.vec3(0.001))
                setattr(n, attr, new)
                if n.rigidbody is not None:
                    reg()
            vf = VectorField(label, tuple(getattr(n, attr)), on_change=put,
                             step=1.0 if attr == "rotation_euler" else 0.1)
            p.add(vf)   # (VectorField hands unknown keywords such as parent= to its X/Y/Z fields)
            self.bind(vf, lambda attr=attr: tuple(getattr(n, attr)))

        if n.is_sun and n.sun_light is not None:
            p = self.panel("Sun", icon="sun")
            Label("Rotate the Sun object to aim it.", dim=True, parent=p)
            self.object_fields(p, n.sun_light)
        if n.is_point_light and n.point_light is not None:
            self.object_fields(self.panel("Point Light", icon="light"), n.point_light)
        if n.is_spot_light and n.spot_light is not None:
            self.object_fields(self.panel("Spot Light", icon="light"), n.spot_light)
        if n.is_clouds and n.cloud_settings is not None:
            self.object_fields(self.panel("Clouds", icon="cloud"), n.cloud_settings)
        if n.is_ocean and n.ocean_settings is not None:
            self.object_fields(self.panel("Ocean", icon="drop"), n.ocean_settings)

        if n.spawn_config:
            p = self.panel(_title(n.spawn_kind or "spawn") + " Settings", icon=ICON_OF.get(kind, "flag"))
            for k in list(n.spawn_config):
                v = n.spawn_config[k]
                if k in ("mesh", "mesh_path") or isinstance(v, (dict,)) or v is None:
                    continue
                self.field(p, n.spawn_config, k)
            if n.spawn_kind not in (None, "player", "trigger"):
                Button("Custom mesh...", lambda: ed.change_spawn_mesh(n), icon="cube", parent=p)

        mat = n.material
        asset = getattr(n, "material_asset", None)
        if asset:
            p = self.panel("Material", icon="material")
            Label(os.path.basename(asset) + ("" if os.path.isfile(asset) else "  (file missing)"), icon="material",
                  parent=p)
            Label("Edit it in the Material tab; every object using it updates.", dim=True, parent=p)
            r = Row(spacing=3, parent=p)
            Button("Edit", lambda: ed.materials.open(asset), icon="edit", parent=r, enabled=os.path.isfile(asset))
            Button("Detach", lambda: (e.detach_material_asset(n), self.show(n, force=True)), icon="close",
                   parent=r, tooltip="Keep the look but make the material this object's own")
        elif mat is not None:
            p = self.panel("Material", icon="material")
            Label("Drop a material (.vmat) here from Assets", dim=True, parent=p)
            self.field(p, mat, "alpha_mode", options=["OPAQUE", "MASK", "BLEND"])
            self.object_fields(p, mat, skip=("alpha_mode",))
            tex = [k for k in vars(mat) if k.endswith("_tex")]
            for k in tex:
                t = getattr(mat, k)
                Label("%s: %s" % (_title(k[:-4]), "texture" if t is not None else "none"),
                      dim=t is None, parent=p)
        if n.mesh is not None:
            m = n.mesh
            vc = getattr(m, "vertex_count", None) or getattr(m, "num_vertices", None)
            ic = getattr(m, "index_count", None) or getattr(m, "num_indices", None)
            if vc or ic:
                p = self.panel("Mesh", icon="cube", collapsed=True)
                if vc:
                    Label("Vertices: {:,}".format(int(vc)), parent=p)
                if ic:
                    Label("Triangles: {:,}".format(int(ic) // 3), parent=p)
                if n.source_path:
                    Label(os.path.basename(n.source_path), dim=True, parent=p)

        self._physics(n)
        self._blueprints(n)

    def _physics(self, n):
        e, ed = self.engine, self.ed
        p = self.panel("Physics", icon="shield")
        col, rb = n.collider, n.rigidbody
        if n.is_sun or n.is_clouds or n.is_ocean:
            Label("(no physics on this object)", dim=True, parent=p)
            return

        def rereg():
            e._physics_reregister(n)
        if col is None:
            Label("No collider: things pass through it.", dim=True, parent=p)
            r = Row(spacing=3, parent=p)
            for k in ("box", "sphere", "capsule", "plane", "mesh"):
                Button(k.title(), lambda k=k: ed.run(lambda: e._phys_add_collider(n, k)), parent=r)
            return
        Label("%s" % type(col).__name__.replace("Collider", " collider"), bold=True, parent=p)
        self.object_fields(p, col, after=rereg)
        r = Row(spacing=3, parent=p)
        Button("Edit Shape", lambda: ed.run(lambda: e._phys_edit_shape(n)), icon="edit", parent=r)
        Button("Remove", lambda: ed.run(lambda: e._phys_remove_collider(n)), icon="trash", parent=r)
        if rb is None:
            Button("Add RigidBody (falls, gets pushed)", lambda: ed.run(lambda: e._phys_add_rigidbody(n)),
                   icon="add", parent=p)
        else:
            Label("RigidBody", bold=True, parent=p)
            self.field(p, rb, "body_type", options=["dynamic", "kinematic", "static"], after=rereg)
            self.object_fields(p, rb, after=rereg, skip=("body_type", "friction", "restitution"))
            Button("Remove RigidBody", lambda: ed.run(lambda: e._phys_remove_rigidbody(n)), icon="trash", parent=p)

    def _blueprints(self, n):
        ed = self.ed
        h3d = self.h3d
        for bp in list(n.blueprints):
            cls = type(bp)
            p = self.panel("Blueprint: " + cls.__name__, icon="blueprint")
            doc = (cls.__doc__ or "").strip().split("\n")[0]
            if doc:
                Label(doc, dim=True, parent=p)
            props = bp.properties if isinstance(bp.properties, dict) else {}
            shown = 0
            for k in list(props):
                v = props[k]
                if k.startswith("_") or isinstance(v, (dict, list)) and not _vec_len(v):
                    continue
                if self.field(p, props, k) is not None:
                    shown += 1
            if not shown and cls.__name__ not in ("VisualScript", "Animator"):
                Label("(no editable properties)", dim=True, parent=p)
            r = Row(spacing=3, parent=p)
            if cls.__name__ == "VisualScript":
                Button("Edit graph", lambda: ed.open_tab("Graph"), icon="node", parent=r)
            elif cls.__name__ == "Animator":
                Button("Animate", lambda: ed.open_tab("Animation"), icon="film", parent=r)
            src = _source_of(cls)
            if src:
                Button("Edit code", lambda src=src, cls=cls: ed.scripts.open(src, find="class " + cls.__name__),
                       icon="code", parent=r)
            Button("Remove", lambda bp=bp: ed.remove_blueprint(n, bp), icon="trash", parent=r)
        p = self.panel("Add", icon="add")
        names = [nm for nm in h3d.BlueprintRegistry.all_names()]
        pick = Dropdown(names or ["(none)"], ("VisualScript" if "VisualScript" in names else None),
                        label="Blueprint", parent=p)
        r = Row(spacing=3, parent=p)
        Button("Add blueprint", lambda: ed.add_blueprint(n, pick.value), icon="add", primary=True, parent=r)
        Button("New script...", lambda: ed.scripts.new_script_dialog(attach_to=n), icon="file-plus", parent=r)
        Label("Tip: drag a script (.py) or a material (.vmat) from Assets onto this panel.", dim=True, parent=p)


def _source_of(cls):
    try:
        f = inspect.getsourcefile(cls)
    except (TypeError, OSError):
        return None
    if not f or not os.path.isfile(f):
        return None
    # the engine file itself is huge and rebuilt from src/: point at project scripts only
    if os.path.basename(f) in ("Heaven3D.py", "Heaven3D_runtime.py"):
        return None
    return f

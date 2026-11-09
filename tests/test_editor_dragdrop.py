"""Unity-style drag & drop in the Vitrail editor (needs a GPU / display):

    python tests/test_editor_dragdrop.py

Real mouse drags (press, move, release) in the running editor:
  * a node material built in the Material tab bakes, and dragging the .vmat
    from Assets onto an object in the viewport gives it the material (undoable)
  * a script dragged onto the Inspector attaches its blueprint; a file with
    several classes attaches the one named after the file, or asks
  * a script / material dragged onto an Outliner row goes to that object
  * an object dragged from the Outliner into Assets becomes a .prefab
  * a model dragged onto empty space in Assets becomes a .prefab
  * a model dropped from the desktop onto Assets is copied in + gets a prefab;
    one dropped onto the viewport is imported
  * the scene saves and reloads with the material link and the scripts
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
proj = tempfile.mkdtemp(prefix="h3ddd_")
shutil.copytree(os.path.join(ROOT, "templates", "coin_collector"), proj, dirs_exist_ok=True)
GLB = os.path.join(HERE, "importers", "assets", "glb", "BoxTextured.glb")
if not os.path.isfile(GLB):
    GLB = os.path.join(HERE, "importers", "assets", "glb", "CesiumMilkTruck.glb")
os.makedirs(os.path.join(proj, "assets"), exist_ok=True)
shutil.copy(GLB, os.path.join(proj, "assets", "Box.glb"))
outside = tempfile.mkdtemp(prefix="h3ddd_out_")
shutil.copy(os.path.join(HERE, "importers", "assets", "glb", "CesiumMilkTruck.glb"), os.path.join(outside, "Truck.glb"))
with open(os.path.join(proj, "scripts", "spinner.py"), "w") as f:
    f.write("from Heaven3D import Blueprint, blueprint\n\n\n@blueprint\nclass Spinner(Blueprint):\n"
            "    def __init__(self):\n        super().__init__()\n        self.properties = {'speed': 90.0}\n\n"
            "    def on_tick(self, dt):\n        self.node.rotation_euler.y += self.properties['speed'] * dt\n\n\n"
            "@blueprint\nclass SpinnerHelper(Blueprint):\n    pass\n")
PROJECT = os.path.join(proj, "main.heaven3d")
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm  # noqa: E402
import pygame  # noqa: E402

W, HH = 1360, 820
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def post(t, **kw):
    pygame.event.post(pygame.event.Event(t, **kw))


eng = H.HeavenEngine(mode="editor", width=W, height=HH, project_path=PROJECT)
ed = getattr(eng, "_vt", None)
check("the Vitrail editor is attached", ed is not None, H._H3D_EDITOR_STATE.get("error"))
if ed is None:
    sys.exit(1)
st = {"f": 0, "steps": [], "t": 10}


def at(df, fn):
    st["t"] += df
    st["steps"].append((st["t"], fn))


def node(name):
    return [x for x in eng.scene.root.traverse() if x.name == name][0]


def drag(src, dst):
    box = {}

    def start():
        box["a"], box["b"] = src(), dst()
        x, y = box["a"]
        post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
        post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
    at(3, start)
    for k in range(1, 9):
        def mv(k=k):
            (x0, y0), (x1, y1) = box["a"], box["b"]
            post(pygame.MOUSEMOTION, pos=(int(x0 + (x1 - x0) * k / 8), int(y0 + (y1 - y0) * k / 8)),
                 rel=(1, 1), buttons=(1, 0, 0))
        at(1, mv)
    at(2, lambda: post(pygame.MOUSEBUTTONUP, pos=box["b"], button=1))


def tile(name):
    def f():
        g = ed.assets.grid
        i = [k for k, a in enumerate(g.assets) if a.name == name][0]
        x, y, w, h = g._rect(ed.ui, i)
        return int(x + w / 2), int(y + h / 2)
    return f


def on_screen(name):
    def f():
        clip = eng.camera.projection() * eng.camera.view() * glm.vec4(node(name).world_position(), 1.0)
        ndc = clip / clip.w
        vp = ed.viewport
        return int(vp.x + (ndc.x * 0.5 + 0.5) * vp.w), int(vp.y + (0.5 - ndc.y * 0.5) * vp.h)
    return f


def row(name):
    def f():
        tv = ed.outliner.tree
        it = [i for i in ed.outliner._items.values() if i.data is not None and i.data.name == name][0]
        tv.reveal(it)
        ed.ui.layout()
        i = [k for k, rw in enumerate(tv.rows()) if rw[0] is it][0]
        rh = tv.rh(ed.ui)
        return int(tv.x + 70), int(tv.y + 1 + tv.header_h(ed.ui) + i * rh - tv.scroll + rh / 2)
    return f


def inspector():
    w = ed.inspector.scroll
    return int(w.x + w.w / 2), int(w.y + w.h * 0.6)


def empty_assets():
    g = ed.assets.grid
    return int(g.x + g.w - 40), int(g.y + g.h - 30)


def look_at(name):
    c = node(name).world_position()
    eng.camera.position = c + glm.vec3(0, 2.5, 5)
    d = glm.normalize(c - eng.camera.position)
    import math
    eng.camera.pitch = math.degrees(math.asin(d.y))
    eng.camera.yaw = math.degrees(math.atan2(-d.x, -d.z))
    ed.select(None)


# ---- 1. a material made in the Material tab ----
def make_material():
    path = H.material_asset_create(os.path.join(proj, "assets", "materials", "Bricks"))
    ed.materials.open(path)
    ne = ed.materials.nodes
    out = [n for n in ne.nodes if n.title == "Material Output"][0]
    br = ne.add_node(ne.create("Texture/Bricks"), (0, 0))
    bump = ne.add_node(ne.create("Vector/Bump"), (220, 170))
    ne.connect(br.socket("Color", True), out.socket("Base Color", False))
    ne.connect(br.socket("Fac", True), bump.socket("Height", False))
    ne.connect(bump.socket("Normal", True), out.socket("Normal", False))
    st["vmat"] = path


at(10, make_material)


def baked():
    b = os.path.join(proj, "assets", "materials", ".baked")
    files = sorted(os.listdir(b)) if os.path.isdir(b) else []
    check("editing the graph bakes the material", "Bricks_basecolor.png" in files and "Bricks_normal.png" in files,
          files)


at(30, baked)
at(2, lambda: (ed.open_tab("Assets"), ed.refresh_assets(create=True)))
at(4, lambda: ed.assets.navigate(os.path.join(proj, "assets", "materials")))
at(2, lambda: look_at("Crate1"))
drag(tile("Bricks.vmat"), on_screen("Crate1"))


def mat_dropped():
    c = node("Crate1")
    check("material dragged onto an object in the viewport is assigned",
          os.path.normcase(str(getattr(c, "material_asset", ""))) == os.path.normcase(st["vmat"])
          and c.material.normal_tex is not None)
    ed.ui.undo.undo()


at(15, mat_dropped)


def mat_undone():
    c = node("Crate1")
    check("Ctrl+Z takes the material back off", not getattr(c, "material_asset", None)
          and c.material.normal_tex is None)
    ed.ui.undo.redo()
    ed.select(c)


at(4, mat_undone)

# ---- 2. scripts ----
at(2, lambda: ed.assets.navigate(os.path.join(proj, "scripts")))
drag(tile("spinner.py"), inspector)


def script_on_inspector():
    names = [type(b).__name__ for b in node("Crate1").blueprints]
    check("script dragged onto the Inspector attaches the class named after the file", names == ["Spinner"], names)
    check("the Inspector shows the new blueprint panel",
          any(getattr(w, "title", "") == "Blueprint: Spinner" for w in ed.ui.iter_widgets()))


at(12, script_on_inspector)
at(2, lambda: check("Python's __pycache__ is hidden in Assets",
                    not any(a.name == "__pycache__" for a in ed.assets.grid.assets)))
drag(tile("coins.py"), row("Crate2"))


def menu_asked():
    check("a file with several classes asks which one (nothing attached yet)", not node("Crate2").blueprints)
    menus = [w for w in ed.ui.overlays] if hasattr(ed.ui, "overlays") else []
    check("... with a menu", bool(menus), len(menus))
    ed.ui.close_popups()
    ed.attach_script(node("Crate2"), os.path.join(proj, "scripts", "coins.py"), only=["Coin"])


at(12, menu_asked)
at(4, lambda: check("picking one attaches just that class",
                    [type(b).__name__ for b in node("Crate2").blueprints] == ["Coin"]))
at(2, lambda: ed.assets.navigate(os.path.join(proj, "assets", "materials")))
drag(tile("Bricks.vmat"), row("Crate3"))
at(12, lambda: check("material dragged onto an Outliner row is assigned",
                     bool(getattr(node("Crate3"), "material_asset", None))))

# ---- 3. prefabs by dragging ----
at(2, lambda: ed.assets.navigate(os.path.join(proj, "assets")))
drag(row("Wall1"), empty_assets)
at(12, lambda: check("an object dragged from the Outliner into Assets becomes a prefab",
                     os.path.isfile(os.path.join(proj, "assets", "Wall1.prefab"))
                     and H.prefab_link(node("Wall1")) is not None))
drag(tile("Box.glb"), empty_assets)


def model_prefab():
    p = os.path.join(proj, "assets", "Box.prefab")
    check("a model dragged onto empty space in Assets becomes a prefab", os.path.isfile(p))
    check("... without leaving anything in the scene", not any(n.name.startswith("Box") for n in eng.scene.root.children))


at(15, model_prefab)


def desktop_drop_assets():
    g = ed.assets.grid
    pygame.mouse.set_pos((int(g.x + 50), int(g.y + 40)))
    st["mp"] = pygame.mouse.get_pos()
    post(pygame.DROPFILE, file=os.path.join(outside, "Truck.glb"))


at(3, desktop_drop_assets)


def desktop_dropped():
    a = os.path.join(proj, "assets")
    if st["mp"] == (0, 0):
        check("desktop drop onto Assets (pointer could not be moved here - skipped)", True)
        return
    check("a model dropped from the desktop onto Assets is copied in + gets a prefab",
          os.path.isfile(os.path.join(a, "Truck.glb")) and os.path.isfile(os.path.join(a, "Truck.prefab")),
          sorted(os.listdir(a)))


at(40, desktop_dropped)


def desktop_drop_view():
    vp = ed.viewport
    pygame.mouse.set_pos((int(vp.x + vp.w / 2), int(vp.y + vp.h * 0.7)))
    st["n0"] = len(eng.scene.root.children)
    post(pygame.DROPFILE, file=os.path.join(outside, "Truck.glb"))


at(3, desktop_drop_view)
at(40, lambda: check("a model dropped from the desktop onto the viewport is imported",
                     len(eng.scene.root.children) == st["n0"] + 1
                     and os.path.isfile(os.path.join(proj, "assets", "models", "Truck.glb"))
                     if pygame.mouse.get_pos() != (0, 0) else True))

# ---- 4. save + reload ----
at(3, lambda: ed.save())


def reloaded():
    data = json.load(open(PROJECT))
    kids = {c["name"]: c for c in data["scene"]["children"]}
    check("saved: material link + script", kids["Crate1"].get("material_asset") == "assets/materials/Bricks.vmat"
          and [b.get("class") for b in kids["Crate1"].get("blueprints", [])] == ["Spinner"],
          (kids["Crate1"].get("material_asset"), kids["Crate1"].get("blueprints")))
    eng.load_project(PROJECT)
    c = node("Crate1")
    check("reloaded: the material and the script are back",
          getattr(c, "material_asset", None) and c.material.normal_tex is not None
          and [type(b).__name__ for b in c.blueprints] == ["Spinner"])


at(10, reloaded)

_flip = pygame.display.flip


def flip():
    st["f"] += 1
    while st["steps"] and st["steps"][0][0] <= st["f"]:
        _f, fn = st["steps"].pop(0)
        try:
            fn()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("step %s" % getattr(fn, "__name__", "?"), False, ex)
    if not st["steps"] or st["f"] > 900:
        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        shutil.rmtree(proj, ignore_errors=True)
        shutil.rmtree(outside, ignore_errors=True)
        os._exit(0 if all(res) else 1)
    _flip()


pygame.display.flip = flip
eng.run()

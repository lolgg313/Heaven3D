"""Unity-style prefab workflow in the Vitrail editor (needs a GPU / display):

    python tests/test_prefab_workflow.py [shots folder]

  * Create > "Empty prefab..." with nothing selected (never greyed out);
    "Prefab from '<object>'" with something selected
  * place the empty prefab, add a cube to the instance, Apply: new
    instances hold the cube; old placeholder files place as empty prefabs
  * drag an object from the Outliner onto a prefab tile: asks, then the
    prefab holds it; unedited instances follow, edited ones keep their edits
  * a prefab that would contain itself is refused
  * rename / move a prefab, a model it uses and a node material in Assets:
    every link in the scene and in other prefabs follows; save + reload is
    clean (links resolve, nothing shows as edited)
  * a project under the home folder stores prefab-relative paths, so after
    moving the whole project (another PC, an exported game) a script can
    still spawn the prefab with its model
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
os.environ.pop("HEAVEN3D_CLASSIC_UI", None)
OUT = sys.argv[1] if len(sys.argv) > 1 else None
# the project lives under a fake home folder, like a real user's Documents
fake_home = tempfile.mkdtemp(prefix="h3dhome_")
for k in ("HOME", "USERPROFILE"):
    os.environ[k] = fake_home
proj = os.path.join(fake_home, "Documents", "MyGame")
os.makedirs(os.path.dirname(proj))
shutil.copytree(os.path.join(ROOT, "templates", "coin_collector"), proj)
os.makedirs(os.path.join(proj, "assets", "models"))
GLB = os.path.join(HERE, "importers", "assets", "glb", "BoxTextured.glb")
if not os.path.isfile(GLB):
    GLB = os.path.join(HERE, "importers", "assets", "glb", "AlphaBlendModeTest.glb")
shutil.copy(GLB, os.path.join(proj, "assets", "models", "Box.glb"))
PROJECT = os.path.join(proj, "main.heaven3d")
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm  # noqa: E402
import pygame  # noqa: E402

res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def post(t, **kw):
    pygame.event.post(pygame.event.Event(t, **kw))


def enter():
    post(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0)


eng = H.HeavenEngine(mode="editor", width=1360, height=820, project_path=PROJECT)
ed = eng._vt
A = os.path.join(proj, "assets")
st = {"f": 0, "steps": [], "t": 10}


def at(df, fn):
    st["t"] += df
    st["steps"].append((st["t"], fn))


def node(name):
    hits = [x for x in eng.scene.root.traverse() if x.name == name]
    return hits[0] if hits else None


def shot(name):
    if OUT:
        data = eng.ctx.screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (eng.width, eng.height), "RGB"),
                                                False, True), os.path.join(OUT, name + ".png"))


def items():
    return [m for m in ed.assets._create_items() if m is not None]


def overlay_button(label):
    stack = list(ed.ui.overlays)
    while stack:
        w = stack.pop()
        if type(w).__name__ == "Button" and getattr(w, "text", None) == label:
            return w
        stack.extend(getattr(w, "children", ()) or ())
    return None


def click_overlay(label):
    b = overlay_button(label)
    if b is None:
        check("dialog button '%s' found" % label, False)
        return
    x, y = int(b.x + b.w / 2), int(b.y + b.h / 2)
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=1)


def type_name(text):
    post(pygame.KEYDOWN, key=pygame.K_a, mod=pygame.KMOD_CTRL, unicode="", scancode=0)
    for c in text:
        post(pygame.TEXTINPUT, text=c)


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


PF = os.path.join(A, "Tower.prefab")

# ---- 1. empty prefab from the Create menu ----
at(10, lambda: (ed.select(None), ed.open_tab("Assets"), ed.refresh_assets(create=True), ed.assets.navigate(A)))


def create_empty():
    it = items()[-1]
    check("with nothing selected, Create offers 'Empty prefab...' (not greyed out)",
          it.label == "Empty prefab..." and it.is_enabled(), it.label)
    it.action()


at(3, create_empty)
at(6, lambda: type_name("Tower"))
at(2, enter)


def empty_made():
    check("Empty prefab... writes the .prefab", os.path.isfile(PF))
    d = H.prefab_read(PF)
    check("... holding an empty group", d["node"].get("children") == [] and d["node"].get("mesh_type") is None)
    st["a"] = ed.prefabs.place(PF)
    a = st["a"]
    check("the empty prefab places as an instance", a is not None and H.prefab_link(a) is not None)
    # fill it like in Unity: edit the instance, Apply
    cube = eng.add_cube(name="TowerBlock")
    cube.material.base_color = (0.9, 0.3, 0.1, 1.0)
    ed.reparent(cube, a)
    cube.position = glm.vec3(0, 1, 0)
    check("adding a child marks the instance edited", eng.prefab_is_modified(a))
    ed.prefabs.apply(a)                       # only instance -> no confirmation needed


at(10, empty_made)


def applied():
    d = H.prefab_read(PF)
    kids = d["node"].get("children", [])
    check("Apply writes the child into the prefab", len(kids) == 1 and kids[0]["name"] == "TowerBlock", len(kids))
    b = eng.spawn_prefab(PF, position=(6, 0, 0))
    st["b"] = b
    check("a new instance has the cube, colour included",
          b.children and abs(b.children[0].material.base_color[0] - 0.9) < 1e-3)
    st["c"] = eng.spawn_prefab(PF, position=(-6, 0, 0))
    st["c"].children[0].material.base_color = (0.1, 0.1, 0.9, 1.0)       # an edited instance
    check("with an object selected, Create offers 'Prefab from ...'",
          (ed.select(node("Crate1")) or True) and items()[-1].label == "Prefab from 'Crate1'")


at(6, applied)



def right_click_tile():
    x, y = tile("Tower.prefab")()
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=3)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=3)


def tile_menu():
    menus = [o for o in ed.ui.overlays if type(o).__name__ == "Menu"]
    labels = [getattr(i, "label", "") for i in (getattr(menus[0], "items", []) if menus else []) if i is not None]
    check("right-click on a prefab: Place / Select instances / Replace",
          "Place in scene" in labels and any(str(l).startswith("Select instances (3)") for l in labels)
          and "Replace with 'Crate1'" in labels, labels)
    shot("prefab_context_menu")
    ed.ui.close_popups()


at(2, lambda: ed.assets.navigate(A))
at(3, right_click_tile)
at(6, tile_menu)

# ---- 2. drag Crate1 from the Outliner onto the Tower tile ----
at(2, lambda: ed.assets.navigate(A))
drag(row("Crate1"), tile("Tower.prefab"))
at(8, lambda: (check("dropping an object on a prefab asks first", overlay_button("Replace") is not None),
               shot("prefab_replace_dialog")))
at(2, lambda: click_overlay("Replace"))


def replaced():
    d = H.prefab_read(PF)
    check("the prefab now holds the dropped object", d["node"].get("mesh_type") == "cube"
          and not d["node"].get("children"), d["node"].get("mesh_type"))
    crate = node("Crate1")
    check("the dropped object became an instance", crate is not None and H.prefab_link(crate) is not None)
    b = [x for x in eng.prefab_instances(PF) if abs(x.position.x - 6) < 1e-4]
    check("unedited instances follow it", b and b[0].mesh is not None and not b[0].children,
          b and (b[0].mesh is not None, len(b[0].children)))
    c = [x for x in eng.prefab_instances(PF) if abs(x.position.x + 6) < 1e-4]
    check("edited instances keep their edits", c and c[0].children
          and abs(c[0].children[0].material.base_color[2] - 0.9) < 1e-3)
    # a prefab can't hold itself
    holder = eng.add_cube(name="Holder")
    inst = eng.spawn_prefab(PF)
    inst.remove()
    holder.add_child(inst)
    try:
        eng.prefab_replace_with(holder, PF)
        check("a prefab that would contain itself is refused", False)
    except ValueError:
        check("a prefab that would contain itself is refused", True)
    eng._delete_node(holder)


at(12, replaced)

# ---- 3. a prefab holding a model; rename / move everything ----


def model_prefab():
    m = eng.load_model(os.path.join(A, "models", "Box.glb"))
    m.name = "BoxModel"
    st["mp"] = eng.save_prefab(m, os.path.join(A, "BoxThing.prefab"))
    d = json.load(open(st["mp"]))
    sp = d["node"].get("source_path")
    check("prefab paths are stored relative to the prefab (not the home folder)", sp == "models/Box.glb", sp)
    vm = H.material_asset_create(os.path.join(A, "Paint"))
    H.material_vmat_write(vm, {"nodes": [{"id": 1, "type": "Texture/Checker", "pos": [0, 0], "values": {}},
                                         {"id": 9, "type": "Output/Material Output", "pos": [0, 0], "values": {}}],
                               "links": [[1, "Color", 9, "Base Color"]]})
    eng.assign_material_asset(node("Crate2"), vm)
    st["vm"] = vm
    ed.refresh_assets()
    ed.assets.navigate(A)
    a = [x for x in ed.assets.grid.assets if x.name == "BoxThing.prefab"][0]
    ed.assets.rename(a)


at(5, model_prefab)
at(6, lambda: type_name("Crate Box"))
at(2, enter)


def renamed():
    newp = os.path.join(A, "Crate Box.prefab")
    m = node("BoxModel")
    check("rename in Assets: the instance follows the prefab",
          os.path.isfile(newp) and H.prefab_link(m) and os.path.normcase(H.prefab_link(m)["path"])
          == os.path.normcase(newp))
    os.makedirs(os.path.join(A, "props"), exist_ok=True)
    ed.refresh_assets()
    ed.assets.navigate(A)
    g = ed.assets.grid
    ed.assets._move_into([x for x in g.assets if x.name == "Crate Box.prefab"],
                         [x for x in g.assets if x.name == "props"][0])


at(10, renamed)


def moved():
    newp = os.path.join(A, "props", "Crate Box.prefab")
    m = node("BoxModel")
    check("move into a folder: the instance follows", H.prefab_link(m)
          and os.path.normcase(H.prefab_link(m)["path"]) == os.path.normcase(newp))
    sp = json.load(open(newp))["node"].get("source_path")
    check("... and the moved prefab still points at its model", sp == "../models/Box.glb", sp)
    n = eng.spawn_prefab(newp, position=(0, 0, 12))
    check("... so it still places with the model", n is not None and (n.mesh is not None or n.children))
    ed.refresh_assets()
    ed.assets.navigate(os.path.join(A, "models"))
    a = [x for x in ed.assets.grid.assets if x.name == "Box.glb"][0]
    ed.assets.rename(a)


at(10, moved)
at(6, lambda: type_name("Crate Mesh"))
at(2, enter)


def model_renamed():
    newm = os.path.join(A, "models", "Crate Mesh.glb")
    m = node("BoxModel")
    check("renaming a model: objects using it follow", os.path.normcase(m.source_path) == os.path.normcase(newm))
    sp = json.load(open(os.path.join(A, "props", "Crate Box.prefab")))["node"].get("source_path")
    check("... and so do prefabs using it", sp == "../models/Crate Mesh.glb", sp)
    ed.assets.navigate(A)
    a = [x for x in ed.assets.grid.assets if x.name == "Paint.vmat"][0]
    ed.assets.rename(a)


at(10, model_renamed)
at(6, lambda: type_name("Checker Paint"))
at(2, enter)


def mat_renamed():
    newv = os.path.join(A, "Checker Paint.vmat")
    c2 = node("Crate2")
    check("renaming a material: objects using it follow, re-baked",
          os.path.normcase(c2.material_asset) == os.path.normcase(newv)
          and c2.material.base_color_tex is not None
          and "Checker Paint" in str(c2.material.base_color_tex[1]))
    ed.save()


at(15, mat_renamed)


def reload_():
    eng.load_project(PROJECT)
    bad = [n.name for n in eng.scene.root.traverse()
           if H.prefab_link(n) and not os.path.isfile(H.prefab_link(n)["path"])]
    check("after save + reload every prefab link resolves", not bad, bad)
    edited = [n.name for n in eng.prefab_instances() if eng.prefab_is_modified(n)]
    check("... only the instance edited on purpose shows as edited", len(edited) == 1, edited)
    m = node("BoxModel")
    check("... the model object loads its renamed model", m is not None and m.mesh is not None)
    check("... the renamed material is back", node("Crate2").material_asset
          and node("Crate2").material.base_color_tex is not None)
    # an old asset-browser placeholder prefab
    stub = os.path.join(A, "Old.prefab")
    json.dump({"type": "prefab", "name": "Old", "color": [1, 1, 1, 1], "components": ["Transform"]}, open(stub, "w"))
    n = eng.spawn_prefab(stub)
    check("an old placeholder prefab places as an empty prefab", n is not None and n.name.startswith("Old"))


at(10, reload_)


def moved_project():
    # the whole project goes to another PC / an exported game: other home, other folder
    other_home = tempfile.mkdtemp(prefix="h3dhome2_")
    dst = os.path.join(other_home, "Games", "MyGame")
    shutil.copytree(proj, dst)
    for k in ("HOME", "USERPROFILE"):
        os.environ[k] = other_home
    eng.project_path = os.path.join(dst, "main.heaven3d")
    n = eng.spawn_prefab("assets/props/Crate Box.prefab", position=(0, 0, -12))
    check("after moving the project, a script still spawns the prefab with its model",
          n is not None and n.source_path and os.path.normcase(n.source_path).startswith(os.path.normcase(dst))
          and n.mesh is not None, n.source_path if n is not None else None)
    st["other_home"] = other_home


at(5, moved_project)

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
        shutil.rmtree(fake_home, ignore_errors=True)
        shutil.rmtree(st.get("other_home", ""), ignore_errors=True)
        os._exit(0 if all(res) else 1)
    _flip()


pygame.display.flip = flip
eng.run()

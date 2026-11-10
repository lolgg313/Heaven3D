"""Assets > right-click > Create in the Vitrail editor (needs a GPU / display):

    python tests/test_assets_create.py [shots folder]

  * a right-click on empty space in Assets opens a menu with Create, and
    Create holds Folder, Script..., Material... and a prefab item
  * Script... asks for a name + template and writes scripts/<name>.py (where
    projects load scripts from) - the script loads and is shown in Assets
  * Material... makes a node material in the current folder and opens it in
    the Material tab
  * the prefab item is never greyed out: "Empty prefab..." with nothing
    selected, "Prefab from '<object>'" saves the selection as a real prefab
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
proj = tempfile.mkdtemp(prefix="h3dassets_")
shutil.copytree(os.path.join(ROOT, "templates", "coin_collector"), proj, dirs_exist_ok=True)
os.makedirs(os.path.join(proj, "assets"), exist_ok=True)
PROJECT = os.path.join(proj, "main.heaven3d")
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import pygame  # noqa: E402

res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def post(t, **kw):
    pygame.event.post(pygame.event.Event(t, **kw))


eng = H.HeavenEngine(mode="editor", width=1360, height=820, project_path=PROJECT)
ed = eng._vt
st = {"f": 0, "steps": [], "t": 10}


def at(df, fn):
    st["t"] += df
    st["steps"].append((st["t"], fn))


def shot(name):
    if OUT:
        data = eng.ctx.screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (eng.width, eng.height), "RGB"),
                                                False, True), os.path.join(OUT, name + ".png"))


def items():
    return {getattr(m, "label", "").split("  (")[0]: m for m in ed.assets._create_items() if m is not None}


at(10, lambda: (ed.select(None), ed.open_tab("Assets"), ed.refresh_assets(create=True)))
at(5, lambda: ed.assets.navigate(os.path.join(proj, "assets")))


def right_click():
    g = ed.assets.grid
    x, y = int(g.x + g.w - 40), int(g.y + g.h - 30)
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=3)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=3)


at(3, right_click)


def menu_open():
    menus = [o for o in ed.ui.overlays if type(o).__name__ == "Menu"]
    labels = [getattr(i, "label", "") for i in (getattr(menus[0], "items", []) if menus else []) if i is not None]
    check("right-click in Assets opens a menu with Create", menus and "Create" in labels, labels)
    shot("assets_context")
    ed.ui.close_popups()
    have = list(items())
    check("Create offers Folder, Script, Material and Prefab",
          have == ["Folder", "Script...", "Material...", "Empty prefab..."], have)
    check("the prefab item works with nothing selected (Empty prefab...)",
          items()["Empty prefab..."].is_enabled())
    items()["Script..."].action()


at(8, menu_open)
at(8, lambda: shot("assets_script_dialog"))
at(1, lambda: [post(pygame.TEXTINPUT, text=c) for c in ""])
at(1, lambda: post(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0))


def script_made():
    fn = os.path.join(proj, "scripts", "my_script.py")
    check("Script... writes scripts/<name>.py", os.path.isfile(fn), sorted(os.listdir(os.path.join(proj, "scripts"))))
    check("the new script loads (its blueprint is available)", H.BlueprintRegistry.get("MyScript") is not None)
    check("Assets shows the new script",
          os.path.normcase(ed.assets.cwd) == os.path.normcase(os.path.join(proj, "scripts"))
          and any(a.name == "my_script.py" for a in ed.assets.grid.selection), ed.assets.cwd)
    ed.assets.navigate(os.path.join(proj, "assets"))
    items()["Material..."].action()


at(12, script_made)
at(6, lambda: post(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0))


def material_made():
    p = os.path.join(proj, "assets", "New Material.vmat")
    check("Material... makes a node material in the current folder",
          os.path.isfile(p) and json.load(open(p)).get("graph"))
    check("... and opens it in the Material tab", ed.materials.path and os.path.normcase(ed.materials.path)
          == os.path.normcase(p) and ed.tabs.titles[ed.tabs.index] == "Material")
    crate = [n for n in eng.scene.root.children if n.name == "Crate1"][0]
    ed.select(crate)


at(15, material_made)


def prefab():
    check("with an object selected it reads Prefab from 'Crate1'", "Prefab from 'Crate1'" in items())
    ed.open_tab("Assets")
    ed.assets.navigate(os.path.join(proj, "assets"))
    items()["Prefab from 'Crate1'"].action()


at(5, prefab)


def prefab_made():
    p = os.path.join(proj, "assets", "Crate1.prefab")
    ok = os.path.isfile(p)
    check("Prefab from 'Crate1' saves the object", ok)
    if ok:
        n = eng.spawn_prefab(p, position=(5, 0, 5))
        check("... as a real prefab that places", n is not None and n.mesh is not None)


at(10, prefab_made)

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
    if not st["steps"] or st["f"] > 600:
        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        shutil.rmtree(proj, ignore_errors=True)
        os._exit(0 if all(res) else 1)
    _flip()


pygame.display.flip = flip
eng.run()

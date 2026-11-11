"""Buildings / Terrain / Particles as Vitrail panels (needs a GPU / display):

    python tests/test_tool_panels.py [shots folder]

Real clicks in the running editor:
  * each tool opens in the right dock (Outliner and bottom panel stay), its
    toolbar button lights up, and the classic panel is not drawn
  * Buildings: Generate makes a sector (undoable), a palette module + a
    viewport click paints one
  * Particles: a library preset drops into the scene, Live preview shows a
    preview that goes away on close, Save stores a preset (no Tk dialog),
    Delete removes it; the old studio's modal window never opens
  * Terrain: a click in the viewport sculpts, Esc closes the tool
  * Play closes an open tool; the engine's own toggles open the panels
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
proj = tempfile.mkdtemp(prefix="h3dtools_")
shutil.copytree(os.path.join(ROOT, "templates", "coin_collector"), proj, dirs_exist_ok=True)
PROJECT = os.path.join(proj, "main.heaven3d")
sys.argv = [sys.argv[0]]
os.chdir(proj)
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


def click_at(x, y, button=1):
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=button)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=button)


eng = H.HeavenEngine(mode="editor", width=1360, height=820, project_path=PROJECT)
ed = eng._vt
st = {"f": 0, "steps": [], "t": 10}


def at(df, fn):
    st["t"] += df
    st["steps"].append((st["t"], fn))


def _all_widgets():
    seen = set()
    for w in ed.ui.iter_widgets():
        seen.add(id(w))
        yield w
    stack = list(ed.ui.overlays)                   # dialogs / menus live in overlays
    while stack:
        w = stack.pop()
        if id(w) not in seen:
            seen.add(id(w))
            yield w
        stack.extend(getattr(w, "children", ()) or ())


def visible_widgets(cls, pred=lambda w: True):
    out = []
    for w in _all_widgets():
        if not isinstance(w, cls) or not w.visible or w.w <= 0 or not pred(w):
            continue
        p, ok = w.parent, True
        while p is not None:                     # really on screen (inside every scroll view)
            if hasattr(p, "scroll") and hasattr(p, "h") and not (p.y <= w.y + w.h / 2 <= p.y + p.h):
                ok = False
                break
            p = p.parent
        if ok:
            out.append(w)
    return out


def press(label, cls_name="Button"):
    import vitrail
    cls = getattr(vitrail, cls_name)
    ws = visible_widgets(cls, lambda w: getattr(w, "text", None) == label)
    if not ws:
        sc = ed.tools.scroll
        for _ in range(3):                       # scroll the tool panel down and look again
            sc.scroll = sc.scroll + sc.h * 0.8
            ed.ui.layout()
            ws = visible_widgets(cls, lambda w: getattr(w, "text", None) == label)
            if ws:
                break
    if not ws:
        check("found '%s'" % label, False)
        return False
    w = ws[0]
    click_at(int(w.x + w.w / 2), int(w.y + w.h / 2))
    return True


def vp(fx, fy):
    v = ed.viewport
    return int(v.x + v.w * fx), int(v.y + v.h * fy)


def shot(name):
    if OUT:
        data = eng.ctx.screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (eng.width, eng.height), "RGB"),
                                                False, True), os.path.join(OUT, name + ".png"))


def docked(name):
    def f():
        check(name + ": opens in the dock, Outliner and bottom panel stay",
              ed.tools.active == name and ed.tools.box.visible and ed.left.visible and ed.bottom.visible
              and not ed.inspector.scroll.visible)
        check(name + ": its toolbar button is lit", ed.tool_open_btns[name].primary)
        check(name + ": no classic panel drawn", not getattr(eng.ui, "_panel_rects", None))
        shot("tool_" + name)
    return f


# ---- Buildings (the classic sector generator behind the Building Maker) ----
at(10, lambda: ed.tools.toggle("buildings"))
at(10, docked("buildings"))
at(2, lambda: press("Classic sector generator..."))
at(8, lambda: check("the classic generator opens from the Building Maker",
                    ed.tools.classic and not eng.building_tool.active))


def gen():
    st["n0"] = sum(1 for n in eng.scene.root.traverse() if n.mesh_type == "procedural_module")
    press("Generate sector")


at(2, gen)


def generated():
    n1 = sum(1 for n in eng.scene.root.traverse() if n.mesh_type == "procedural_module")
    check("Generate sector makes a building", n1 > st["n0"] + 50, n1 - st["n0"])
    st["n1"] = n1
    ed.ui.undo.undo()


at(20, generated)


def undone():
    n = sum(1 for n in eng.scene.root.traverse() if n.mesh_type == "procedural_module")
    check("Ctrl+Z removes the generated sector", n == st["n0"], n)
    eng.camera.position = glm.vec3(0, 14, 16)
    eng.camera.yaw, eng.camera.pitch = 0.0, -45.0
    press("wall")


at(4, undone)


def paint():
    check("picking a module switches to Paint", eng.procedural_generator.mode == "paint"
          and eng.procedural_generator.paint_key == "wall")
    st["p0"] = sum(1 for n in eng.scene.root.traverse() if n.mesh_type == "procedural_module")
    click_at(*vp(0.5, 0.75))


at(6, paint)
at(10, lambda: check("a click in the viewport paints a module",
                     sum(1 for n in eng.scene.root.traverse() if n.mesh_type == "procedural_module") == st["p0"] + 1))

# ---- Particles ----
at(3, lambda: ed.tools.toggle("particles"))
at(10, docked("particles"))
at(1, lambda: check("opening Particles closed Buildings", not eng.procedural_generator.panel_open))


def spawn():
    st["c0"] = len(eng.scene.root.children)
    press("Fire")


at(2, spawn)
at(10, lambda: check("a library preset drops into the scene", len(eng.scene.root.children) == st["c0"] + 1
                     and "particle_system" in (eng.selected.user_data if eng.selected else {})))
at(2, lambda: press("Live preview", "Checkbox"))
at(15, lambda: check("Live preview shows the preview", eng._gu_studio._preview_node is not None))
at(2, lambda: press("Save preset..."))
at(8, lambda: post(pygame.KEYDOWN, key=pygame.K_RETURN, mod=0, unicode="\r", scancode=0))
at(10, lambda: check("Save stores a preset (no Tk dialog)",
                     "Custom 1" in eng._gu_studio._get_presets()
                     and os.path.isfile(os.path.join(proj, "particle_presets", "Custom 1.json"))))
at(2, lambda: check("the classic studio window never opened", not eng._gu_studio.panel_open))


def delete():
    import vitrail
    trash = visible_widgets(vitrail.Button, lambda b: getattr(b, "tooltip", "") == "Delete Custom 1")
    if not trash:
        sc = ed.tools.scroll
        sc.scroll = 0
        ed.ui.layout()
        trash = visible_widgets(vitrail.Button, lambda b: getattr(b, "tooltip", "") == "Delete Custom 1")
    check("my preset has a delete button", bool(trash))
    if trash:
        click_at(int(trash[0].x + trash[0].w / 2), int(trash[0].y + trash[0].h / 2))


at(3, delete)
at(8, lambda: press("Delete"))
at(10, lambda: check("Delete removes the preset", "Custom 1" not in eng._gu_studio._get_presets()))

# ---- Terrain ----
at(3, lambda: ed.tools.toggle("terrain"))
at(30, docked("terrain"))
at(1, lambda: check("opening Terrain removed the particle preview", eng._gu_studio._preview_node is None))


def sculpt():
    eng.camera.position = glm.vec3(0, 20, 30)
    eng.camera.yaw, eng.camera.pitch = 0.0, -30.0
    td = eng._tp_editor.terrain_data()
    st["h0"] = float(td.heights.max())
    x, y = vp(0.5, 0.55)
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
    st["xy"] = (x, y)


at(3, sculpt)
at(20, lambda: post(pygame.MOUSEBUTTONUP, pos=st["xy"], button=1))
at(5, lambda: check("a click in the viewport sculpts the terrain",
                    float(eng._tp_editor.terrain_data().heights.max()) > st["h0"] + 0.5))
at(2, lambda: post(pygame.KEYDOWN, key=pygame.K_ESCAPE, mod=0, unicode="\x1b", scancode=0))
at(10, lambda: check("Esc closes the tool and brings the Inspector back",
                     ed.tools.active is None and ed.inspector.scroll.visible))

# ---- engine toggles + Play ----
at(2, lambda: eng.toggle_particle_studio())
at(6, lambda: check("the engine's own toggle opens the Vitrail panel",
                    ed.tools.active == "particles" and not eng._gu_studio.panel_open))
at(2, ed.toggle_play)
at(15, lambda: check("Play closes the open tool", ed.tools.active is None))
at(2, ed.toggle_play)

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
        os._exit(0 if all(res) else 1)
    _flip()


pygame.display.flip = flip
eng.run()

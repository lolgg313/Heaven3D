"""The Vitrail editor inside the real engine (needs a GPU / display):

    python tests/test_vitrail_editor.py

Opens a copy of the Coin Collector template in the editor and drives it with
real pygame events: picking, the inspector, undo, keyboard routing, a
visual script and an animation that survive save + reload, a script that is
hot-reloaded, Play / Stop, and the --classic-ui switch.
"""
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
proj = tempfile.mkdtemp(prefix="h3dvt_")
shutil.copytree(os.path.join(ROOT, "templates", "coin_collector"), proj, dirs_exist_ok=True)
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


def click(x, y, button=1):
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=button)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=button)


def key(k, mod=0, text=""):
    post(pygame.KEYDOWN, key=k, mod=mod, unicode=text, scancode=0)
    if text:
        post(pygame.TEXTINPUT, text=text)
    post(pygame.KEYUP, key=k, mod=mod, unicode="", scancode=0)


eng = H.HeavenEngine(mode="editor", width=W, height=HH, project_path=PROJECT)
ed = getattr(eng, "_vt", None)
check("the Vitrail editor is attached in editor mode", ed is not None, H._H3D_EDITOR_STATE.get("error"))
if ed is None:
    sys.exit(1)
st = {"f": 0, "steps": []}


def at(f):
    def deco(fn):
        st["steps"].append((f, fn))
        return fn
    return deco


@at(20)
def _pick():
    vp = ed.viewport
    click(int(vp.x + vp.w * 0.5), int(vp.y + vp.h * 0.6))


@at(30)
def _picked():
    check("a click in the viewport reaches the engine (picks)", eng.selected is not None, eng.selected)
    check("the inspector shows the picked object", ed.inspector.node is eng.selected)
    check("the outliner highlights it", bool(ed.outliner.tree.selection)
          and ed.outliner.tree.selection[0].data is eng.selected)
    # a click on a panel must not reach the engine
    st["sel"] = eng.selected
    click(int(ed.right.x + 30), int(ed.right.y + ed.right.h - 10))


@at(36)
def _panel_click():
    check("a click on a panel doesn't deselect / pick through", eng.selected is st["sel"])
    ed.create("cube")


@at(40)
def _created():
    n = eng.selected
    st["cube"] = n
    check("Create > Cube adds and selects a cube", n is not None and n.mesh is not None)
    vf = [w for w, _g in ed.inspector.binds if type(w).__name__ == "VectorField" and w.label == "Location"][0]
    vf.fields[1].set_value(2.5)                       # what dragging / typing in the field does
    check("inspector fields edit the object live", abs(n.position.y - 2.5) < 1e-6, n.position)
    n.position = glm.vec3(1, 7, 1)                     # e.g. moved by the gizmo or a script


@at(46)
def _synced():
    vf = [w for w, _g in ed.inspector.binds if type(w).__name__ == "VectorField" and w.label == "Location"][0]
    check("inspector follows changes made elsewhere", abs(vf.fields[1].value - 7.0) < 1e-6, vf.value)
    ed.ui.undo.undo()                                  # undo the create
    st["n_before"] = len(eng.scene.root.children)


@at(50)
def _undo():
    check("Ctrl+Z undoes Create", st["cube"].parent is None)
    ed.ui.undo.redo()


@at(54)
def _redo():
    names = [c.name for c in eng.scene.root.children]
    check("Ctrl+Y redoes it", "Cube" in names, names)
    # keyboard routing: typing in the console must not reach the 3D view (W = move tool, Delete...)
    ed.open_tab("Console", focus=True)
    eng.gizmo.set_mode(H.Gizmo.ROTATE)


@at(58)
def _typing():
    for ch in "1+1":
        post(pygame.TEXTINPUT, text=ch)
    key(pygame.K_w, text="")
    key(pygame.K_RETURN)


@at(64)
def _typed():
    check("keys typed into the editor don't reach the viewport", eng.gizmo.mode == H.Gizmo.ROTATE)
    lines = [t for t, _c in ed.console.log.lines]
    check("the Python console runs code", ">>> 1+1" in lines and "2" in lines, lines[-3:])
    ed.ui.set_focus(None)
    key(pygame.K_w)


@at(68)
def _w():
    check("with nothing focused, W reaches the viewport (move tool)", eng.gizmo.mode == H.Gizmo.MOVE)
    # visual script + animation on a new object
    ed.create("scripted")
    ed.graph.starter()
    st["scripted"] = eng.selected.name
    ed.create("animated")
    ed.anim.add_track()                                 # position.y
    ed.anim.player.set_frame(40)
    eng.selected.position = glm.vec3(0, 4, 0)
    ed.anim.key_all()
    st["animated"] = eng.selected.name


@at(74)
def _save():
    ed.graph.flush(force=True)
    ed.save()
    st["mt"] = os.path.getmtime(PROJECT)
    ed.open_project(PROJECT)


@at(80)
def _reloaded():
    s = eng.find_node(st["scripted"])
    a = eng.find_node(st["animated"])
    g = s and [b for b in s.blueprints if type(b).__name__ == "VisualScript"]
    check("a visual script survives save + reopen", bool(g) and len(g[0].properties["graph"]["nodes"]) == 4,
          g and g[0].properties["graph"])
    an = a and [b for b in a.blueprints if type(b).__name__ == "Animator"]
    tracks = an and an[0].properties["clips"]["Clip"]["tracks"]
    check("an animation survives save + reopen", bool(tracks) and tracks[0]["name"] == "position.y"
          and len(tracks[0]["keys"]) == 2, tracks)
    # a script made in the editor, attached, then edited and hot-reloaded
    sdir = os.path.join(proj, "scripts")
    os.makedirs(sdir, exist_ok=True)
    path = os.path.join(sdir, "bobber.py")
    with open(path, "w") as fh:
        fh.write("from Heaven3D import Blueprint, blueprint\n\n@blueprint\nclass Bobber(Blueprint):\n"
                 "    def __init__(self):\n        super().__init__()\n        self.properties = {'v': 1}\n\n"
                 "    def on_tick(self, dt):\n        self.node.position.y += 0.0\n")
    ed.scripts.open(path)
    ed.scripts.save()
    ed.select(s)
    st["bp"] = ed.add_blueprint(s, "Bobber")
    code = ed.scripts.code
    code.set_text(code.text.replace("+= 0.0", "+= 1.0").replace("{'v': 1}", "{'v': 1, 'w': 2}"), keep_view=True)
    ed.scripts.save()


@at(84)
def _hot():
    bp = st["bp"]
    check("saving a script hot-reloads live blueprints", type(bp) is H.BlueprintRegistry.get("Bobber")
          and bp.properties.get("w") == 2, bp.properties)
    ed.scripts.code.set_text("def broken(:\n    pass\n")
    ed.scripts.save()
    check("a syntax error is shown on its line, the editor keeps running", ed.scripts.code.error_line == 1)
    key(pygame.K_F5)


@at(100)
def _play():
    check("F5 starts Play", eng._playing)
    check("the panels make room for the game", not ed.left.visible and not ed.right.visible
          and ed.play_pill.visible)
    key(pygame.K_p, mod=pygame.KMOD_CTRL)


@at(110)
def _stop():
    check("Ctrl+P stops Play", not eng._playing)
    check("the panels come back", ed.left.visible and ed.right.visible)
    ed.engine.scene.root  # noqa
    st["done"] = True


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
            check("step %s" % fn.__name__, False, ex)
    if not st["steps"] or st["f"] > 400:
        finish()
    _flip()


def finish():
    # --classic-ui keeps the old interface
    os.environ["HEAVEN3D_CLASSIC_UI"] = "1"
    check("HEAVEN3D_CLASSIC_UI=1 keeps the classic editor", not H._h3d_editor_wanted(eng))
    print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
    shutil.rmtree(proj, ignore_errors=True)
    os._exit(0 if all(res) else 1)


pygame.display.flip = flip
eng.run()

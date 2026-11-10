"""The MakeHuman Character Creator inside the Vitrail editor (needs a GPU / display):

    python tests/test_character_creator_vitrail.py [shots folder]

  * selecting a character shows the creator as a panel in the Inspector;
    the classic overlay is not drawn and the editor's docks stay visible
  * tabs switch (Face -> "Reset face"), a slider drag changes the character
    (Height), Random makes a different one, and the panel follows changes
    made elsewhere (values updated without rebuilding mid-drag)
"""
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.pop("HEAVEN3D_CLASSIC_UI", None)
OUT = sys.argv[1] if len(sys.argv) > 1 else None
proj = tempfile.mkdtemp(prefix="h3dmhvt_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
os.makedirs(os.path.join(proj, "packages"))
shutil.copytree(os.path.join(ROOT, "packages", "mh_characters"), os.path.join(proj, "packages", "mh_characters"),
                ignore=shutil.ignore_patterns("__pycache__", "tools"))
P = [os.path.join(proj, f) for f in os.listdir(proj) if f.endswith(".heaven3d")][0]
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


def click(x, y):
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=1)


eng = H.HeavenEngine(mode="editor", width=1360, height=820, project_path=P)
ed = eng._vt
mod = sys.modules.get("h3dpkg_mh_characters")
check("the editor and the character package are loaded", ed is not None and mod is not None)
st = {"f": 0, "steps": [], "t": 10}


def at(df, fn):
    st["t"] += df
    st["steps"].append((st["t"], fn))


def widgets(cls, pred=lambda w: True):
    return [w for w in ed.ui.iter_widgets() if isinstance(w, cls) and w.visible and w.w > 0 and pred(w)]


def shot(name):
    if OUT:
        data = eng.ctx.screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (eng.width, eng.height), "RGB"),
                                                False, True), os.path.join(OUT, name + ".png"))


def make():
    mod.tool_new_character(eng)
    st["node"] = eng.selected


at(5, make)


def shown():
    from vitrail import Button, Panel
    panels = widgets(Panel, lambda w: getattr(w, "title", "") == "Character Creator")
    check("selecting a character shows the creator in the Inspector", bool(panels))
    check("the classic creator overlay isn't drawn",
          not any(str(b[1]).startswith("Gender") for b in getattr(eng.ui, "_button_rects", [])))
    check("the editor's docks stay visible", ed.left.visible and ed.right.visible and ed.bottom.visible)
    tabs = [b.text for b in widgets(Button) if b.text in ("Body", "Face", "Clothes", "Hair", "Skin", "Anim", "AI")]
    check("all seven tabs are there", len(set(tabs)) == 7, tabs)
    shot("vt_creator_body")


at(25, shown)


def drag_height():
    from vitrail import NumberField
    f = widgets(NumberField, lambda w: str(w.label).startswith("Height"))
    if not f:
        check("Height slider found", False)
        return
    f = f[0]
    st["fid"] = id(f)
    st["h0"] = float(mod._spec(mod._char_bp(st["node"]))["macro"]["height"])
    y = int(f.y + f.h / 2)
    post(pygame.MOUSEMOTION, pos=(int(f.x + 20), y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(int(f.x + 20), y), button=1)
    for k in range(1, 7):
        post(pygame.MOUSEMOTION, pos=(int(f.x + 20 + (f.w - 30) * k / 6), y), rel=(10, 0), buttons=(1, 0, 0))
    st["release"] = (int(f.x + f.w - 10), y)


def mid_drag():
    post(pygame.MOUSEBUTTONUP, pos=st["release"], button=1)


at(5, drag_height)
at(2, mid_drag)


def dragged():
    h1 = float(mod._spec(mod._char_bp(st["node"]))["macro"]["height"])
    check("dragging Height changes the character", h1 > st["h0"] + 0.05, (round(st["h0"], 3), round(h1, 3)))
    from vitrail import Button
    face = widgets(Button, lambda b: b.text == "Face")
    click(int(face[0].x + face[0].w / 2), int(face[0].y + face[0].h / 2))


at(15, dragged)


def face_tab():
    from vitrail import Button
    check("the Face tab opens", bool(widgets(Button, lambda b: b.text == "Reset face")))
    st["spec"] = repr(mod._spec(mod._char_bp(st["node"])))
    rnd = widgets(Button, lambda b: b.text == "Random")
    click(int(rnd[0].x + rnd[0].w / 2), int(rnd[0].y + rnd[0].h / 2))
    shot("vt_creator_face")


at(15, face_tab)


def randomized():
    check("Random makes a different character", repr(mod._spec(mod._char_bp(st["node"]))) != st["spec"])
    mod._spec(mod._char_bp(st["node"]))["macro"]["muscle"] = 0.93      # changed elsewhere (a script)
    from vitrail import Button
    body = widgets(Button, lambda b: b.text == "Body")
    click(int(body[0].x + body[0].w / 2), int(body[0].y + body[0].h / 2))


at(15, randomized)


def follows():
    from vitrail import NumberField
    f = widgets(NumberField, lambda w: str(w.label).startswith("Muscle"))
    check("the panel follows changes made elsewhere", f and abs(float(f[0].value) - 0.93) < 1e-3,
          f and f[0].value)
    ed.select(None)


at(20, follows)


def gone():
    from vitrail import Panel
    check("deselecting removes the panel",
          not widgets(Panel, lambda w: getattr(w, "title", "") == "Character Creator"))


at(10, gone)

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

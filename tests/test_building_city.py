"""Towns of painted buildings and the editor's view modes (needs a GPU / display):

    python tests/test_building_city.py [shots folder]

  * the three view modes are back in the Vitrail editor: Lit / Lit + Wire /
    Wire buttons on the viewport, F4 cycles them, they hide while playing
  * buildings that stand still are drawn in districts: a town of 9 blocks is
    a few dozen draw calls instead of a thousand, and looks exactly the same
    as drawing every part (pixel comparison)
  * levels of detail: far districts are shells with dark window panes (a
    fraction of the triangles) and only hold their shell in video memory;
    detail is loaded when the camera comes close and dropped when it left
  * a selected / moved / edited / deleted building leaves and re-joins its
    district; its own GPU buffers only exist while it is drawn on its own
  * identical designs are baked once and share their vertex data
  * window glass casts no shadow, a district is ONE shadow caster
  * the wireframe view draws the districts; the paint tool keeps its cursor
    with the grid hidden
  * "Generate town" in the Buildings panel is one undo step; save + reload
"""
import importlib.util
import math
import os
import shutil
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.pop("HEAVEN3D_CLASSIC_UI", None)
os.environ.pop("HEAVEN3D_NO_CITY", None)
OUT = sys.argv[1] if len(sys.argv) > 1 else None
if OUT:
    os.makedirs(OUT, exist_ok=True)
proj = tempfile.mkdtemp(prefix="h3dcity_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
PROJECT = [os.path.join(proj, f) for f in os.listdir(proj) if f.endswith(".heaven3d")][0]
sys.argv = [sys.argv[0]]
spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
H = importlib.util.module_from_spec(spec)
sys.modules["Heaven3D"] = H
spec.loader.exec_module(H)
H._h3d_set_splash_mode("off")
import glm  # noqa: E402
import numpy as np  # noqa: E402
import pygame  # noqa: E402

res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + ("  [%s]" % (d,) if d != "" else ""), flush=True)


def post(t, **kw):
    pygame.event.post(pygame.event.Event(t, **kw))


eng = H.HeavenEngine(mode="editor", width=1280, height=720, project_path=PROJECT)
ed = eng._vt
for _n in [n for n in eng.scene.root.children if n.name == "Ocean"]:
    eng._delete_node(_n)
st = {"f": 0, "steps": [], "t": 6}
CITY = H.BK_CITY


def at(df, fn):
    st["t"] += df
    st["steps"].append((st["t"], fn))


def grab():
    data = eng.ctx.screen.read(components=3)
    return np.frombuffer(data, np.uint8).reshape(eng.height, eng.width, 3)[::-1].astype(np.int16)


def view(img):
    """The 3D view only (the panels are closed for the pictures)."""
    vp = ed.viewport
    return img[int(vp.y) + 50:int(vp.y + vp.h) - 4, int(vp.x) + 4:int(vp.x + vp.w) - 4]


def shot(name):
    if OUT:
        data = eng.ctx.screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (eng.width, eng.height), "RGB"),
                                                False, True), os.path.join(OUT, name + ".png"))


def look(pos, target):
    eng.camera.position = glm.vec3(*pos)
    dx, dy, dz = [target[i] - pos[i] for i in range(3)]
    ln = math.sqrt(dx * dx + dy * dy + dz * dz)
    eng.camera.yaw = math.degrees(math.atan2(-dx, -dz))
    eng.camera.pitch = math.degrees(math.asin(dy / ln))


def buildings():
    return [n for n in eng.scene.root.traverse() if H.bk_is_building(n)]


def stats():
    return eng.building_stats()


def click(w):
    x, y = int(w.x + w.w / 2), int(w.y + w.h / 2)
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=1)


def key(k, mod=0):
    post(pygame.KEYDOWN, key=k, mod=mod, unicode="", scancode=0)
    post(pygame.KEYUP, key=k, mod=mod, unicode="", scancode=0)


def press_button(label):
    import vitrail
    sc = ed.tools.scroll
    for _try in range(8):
        for w in ed.ui.iter_widgets():
            if isinstance(w, vitrail.Button) and w.visible and w.w > 0 and getattr(w, "text", None) == label \
                    and sc.y <= w.y + w.h / 2 <= sc.y + sc.h:
                click(w)
                return True
        sc.scroll = sc.scroll + sc.h * 0.7
        ed.ui.layout()
    check("found the '%s' button" % label, False)
    return False


# ---- 1. the three view modes ----
def modes_1():
    check("the viewport has Lit / Lit + Wire / Wire buttons",
          ed.shade_pill.visible and [b.text for b in ed.shade_btns.values()] == ["Lit", "Lit + Wire", "Wire"]
          and all(b.w > 0 for b in ed.shade_btns.values()))
    eng.add_building("town", position=(0, 0, 0), house=(4, 3, 2), seed=3)
    look((13, 8, 15), (4, 3, 3))


at(2, modes_1)
at(4, lambda: st.update(lit=view(grab())))
at(1, lambda: click(ed.shade_btns["wire"]))


def modes_2():
    img = view(grab())
    st["wire"] = img
    check("clicking Wire switches the view to wireframe", ed.shading() == "wire" and ed.shade_btns["wire"].primary
          and not ed.shade_btns["lit"].primary, ed.shading())
    check("... and the building is drawn as lines on a dark background",
          float(np.abs(img - st["lit"]).mean()) > 12 and float(img.mean()) < 90, round(float(img.mean()), 1))
    shot("city_1_wire")
    key(pygame.K_F4)


at(4, modes_2)
at(4, lambda: (check("F4 goes to the next view mode", ed.shading() == "lit", ed.shading()),
               click(ed.shade_btns["lit_wire"])))


def modes_3():
    img = view(grab())
    d = float(np.abs(img - st["lit"]).mean())
    check("Lit + Wire draws the edges over the lit view", ed.shading() == "lit_wire" and 0.3 < d < 40, round(d, 2))
    shot("city_2_lit_wire")
    ed.toggle_play()


at(4, modes_3)


def modes_4():
    img = view(grab())
    check("while playing the view-mode buttons are hidden", not ed.shade_pill.visible)
    check("... and the game is drawn lit, not as a wireframe", eng._playing, eng._playing)
    ed.toggle_play()


at(6, modes_4)


def modes_5():
    check("after Play the buttons are back", ed.shade_pill.visible and ed.shading() == "lit_wire")
    click(ed.shade_btns["lit"])


at(6, modes_5)


# ---- 2. a town drawn in districts ----
def town_1():
    check("back to Lit", ed.shading() == "lit")
    for n in buildings():
        eng._delete_node(n)
    for k in ("show_outliner", "show_inspector", "show_bottom"):
        ed.prefs[k] = False
    ed.apply_visibility()
    t0 = time.time()
    g = eng.add_town(3, 3, position=(0, 0, 0), seed=7, damage=0.15)
    st["town"] = g
    st["town_s"] = time.time() - t0
    b = buildings()
    st["n"] = len(b)
    check("generate_town(3, 3) makes a 'Town' group of houses and streets",
          g.parent is eng.scene.root and len(b) > 40 and all(n.parent is g for n in b)
          and sum(1 for n in b if n.name.startswith("Street")) == 9, len(b))
    parts = sum(1 for n in b for c in n.children if getattr(c, "_bk_level", None) is not None)
    st["parts"] = parts
    look((-14, 9, -12), (30, 2, 30))
    eng.set_building_detail(near=1e6, mid=1e6)


at(3, town_1)


def town_2():
    s = stats()
    st["near"] = s
    st["img_city"] = view(grab())
    shot("city_3_town_near")
    check("every building of the town is drawn by its district", s["batched"] == st["n"] and s["districts"] >= 4, s)
    check("the town is a few dozen draw calls, not one per part",
          s["draws"] < st["parts"] / 6 and eng.renderer.stat_draw_calls < st["parts"] / 6,
          (s["draws"], eng.renderer.stat_draw_calls, st["parts"]))
    b = buildings()
    check("a building in a district keeps no GPU buffers of its own",
          not any(c.mesh.on_gpu() for n in b for c in n.children if getattr(c, "_bk_level", None) is not None))
    cs = H._bk_city_state(eng.scene)
    check("a district is ONE shadow caster and window glass casts none",
          len(cs.shadow_items) == s["districts"]
          and all(not getattr(m, "_h3d_shadow", True) for _n, m, _w in cs.items))
    CITY["on"] = False                       # the same view, every part drawn on its own


at(5, town_2)


def town_3():
    img = view(grab())
    d = np.abs(img - st["img_city"])
    shot("city_4_town_parts")
    check("districts draw the same picture as part-by-part drawing",
          float(d.mean()) < 0.6 and float((d.max(axis=2) > 24).mean()) < 0.004,
          (round(float(d.mean()), 3), round(float((d.max(axis=2) > 24).mean()), 5)))
    check("... which was %d draw calls" % eng.renderer.stat_draw_calls,
          eng.renderer.stat_draw_calls > st["near"]["draws"] * 4, eng.renderer.stat_draw_calls)
    CITY["on"] = True
    eng.set_building_detail(near=0, mid=1e6)


at(5, town_3)


def lod_mid():
    s = stats()
    st["mid"] = s
    img = view(grab())
    shot("city_5_town_mid")
    d = float(np.abs(img - st["img_city"]).mean())
    check("mid detail: no rooms, dark panes in the windows, about half the triangles",
          s["mid"] == s["drawn"] and s["triangles"] < st["near"]["triangles"] * 0.62 and d < 9,
          (s["triangles"], st["near"]["triangles"], round(d, 2)))
    eng.set_building_detail(near=0, mid=0)


at(5, lod_mid)


def lod_far():
    s = stats()
    img = view(grab())
    shot("city_6_town_far")
    d = float(np.abs(img - st["img_city"]).mean())
    check("far detail: bare shells, under a fifth of the triangles, still the same town",
          s["far"] == s["drawn"] and s["triangles"] < st["near"]["triangles"] * 0.2 and d < 14,
          (s["triangles"], st["near"]["triangles"], round(d, 2)))
    st["mb_before"] = s["video_mb"]
    CITY["unload_frames"] = 4


at(5, lod_far)


def stream_1():
    s = stats()
    check("detail that is no longer shown leaves video memory",
          s["video_mb"] < st["near"]["video_mb"] * 0.3, (round(s["video_mb"], 1), round(st["near"]["video_mb"], 1)))
    eng.set_building_detail(near=6, mid=30)
    look((20, 1.7, 8.5), (60, 2, 9))            # standing in a street


at(14, stream_1)


def stream_2():
    s = stats()
    shot("city_7_street")
    check("standing in the street: near districts have rooms, the others less",
          s["near"] >= 1 and s["near"] < s["drawn"], s)
    check("... and only those hold their rooms in video memory",
          st["near"]["video_mb"] * 0.2 < s["video_mb"] < st["near"]["video_mb"] * 0.9,
          (round(s["video_mb"], 1), round(st["near"]["video_mb"], 1)))
    eng.set_building_detail(preset="medium")
    check("set_building_detail(preset='medium') is rooms to 45 m", CITY["near"] == 45.0 and CITY["mid"] == 140.0)
    look((-14, 9, -12), (30, 2, 30))
    eng.set_building_detail(near=1e6, mid=1e6)


at(10, stream_2)


# ---- 3. buildings leave and re-join their district ----
def sel_1():
    b = [n for n in buildings() if not n.name.startswith("Street")]
    st["b"] = b[0]
    ed.select(b[0])
    check("a house of a town is selected on its own, not the whole town", eng.selected is b[0],
          eng.selected.name if eng.selected else None)


at(5, sel_1)


def sel_2():
    s = stats()
    b = st["b"]
    check("the selected building is drawn on its own (gizmo drags stay exact)",
          s["batched"] == st["n"] - 1 and any(c.mesh.on_gpu() for c in b.children if c.mesh is not None), s)
    img = view(grab())
    d = float(np.abs(img - st["img_city"]).mean())
    shot("city_7b_selected")
    check("... and the town looks the same apart from its outline and gizmo", d < 5, round(d, 3))
    ed.select(None)


at(5, sel_2)


def move_1():
    b = st["b"]
    check("deselected, it is back in its district", stats()["batched"] == st["n"], stats()["batched"])
    st["p0"] = glm.vec3(b.position)
    b.position = b.position + glm.vec3(0.0, 6.0, 0.0)        # a script moves it


at(4, move_1)
at(3, lambda: check("a building that just moved is drawn on its own until it stands still",
                    stats()["batched"] == st["n"] - 1, stats()["batched"]))


def hide_1():
    st["b"].visible = False


at(1, hide_1)


def hide_2():
    img = view(grab())
    st["img_hidden"] = img
    check("a hidden building is not drawn (its parts hide with it)", stats()["batched"] == st["n"] - 1)
    st["b"].visible = True


at(3, hide_2)


def sel_3():
    s = stats()
    b = st["b"]
    check("... then it is part of a district at its new place",
          s["batched"] == st["n"] and not any(c.mesh.on_gpu() for c in b.children if c.mesh is not None), s)
    img = view(grab())
    d = float(np.abs(img - st["img_city"]).mean())
    check("... visibly moved", d > 0.15, round(d, 3))
    dh = float(np.abs(img - st["img_hidden"]).mean())
    check("... and it was really gone while hidden", dh > 0.15, round(dh, 3))
    shot("city_8_moved")
    b.position = glm.vec3(st["p0"])
    st["tris"] = s["triangles"]
    eng.building_edit(st["b"], edges={e: "wall" for e, k in list(H.bk_data(st["b"]).edges.items())
                                      if k == "window"})


at(CITY["settle_frames"] + 4, sel_3)


def edit_1():
    s = stats()
    check("an edit from a script re-bakes the building inside its district",
          s["batched"] == st["n"] and s["triangles"] < st["tris"], (s["triangles"], st["tris"]))
    eng._delete_node(st["b"])


at(CITY["settle_frames"] + 6, edit_1)
at(3, lambda: check("a deleted building leaves its district", stats()["batched"] == st["n"] - 1
                    and stats()["buildings"] == st["n"] - 1, stats()))


# ---- 4. shared designs, wireframe, tool cursor without the grid ----
def share_1():
    a = eng.add_building("manor", position=(-40, 0, 0), house=(4, 3, 2), seed=99)
    b = eng.add_building("manor", position=(-40, 0, 20), house=(4, 3, 2), seed=99)
    c = eng.add_building("manor", position=(-40, 0, 40), house=(4, 3, 2), seed=100)
    pa, pb, pc = a._bk_parts[0], b._bk_parts[0], c._bk_parts[0]
    check("two buildings of the same design share their baked vertex data",
          all(pa[m] is pb[m] for m in pa) and not any(pa[m] is pc.get(m) for m in pa))
    eng.building_edit(b, edges={(0, 0, -1, "N"): "door"})
    check("... until one of them is edited", any(a._bk_parts[0][m] is not b._bk_parts[0].get(m) for m in pa)
          and H.bk_data(a).to_params() != H.bk_data(b).to_params())
    for n in (a, b, c):
        eng._delete_node(n)
    eng.set_shading("wire")


at(3, share_1)


def wire_1():
    img = view(grab())
    lines = float((img.max(axis=2) > 120).mean())
    shot("city_9_wire_town")
    check("the wireframe view draws the districts", 0.02 < lines < 0.9, round(lines, 3))
    eng.set_shading("lit")
    eng.set_grid_visible(False)
    ed.tools.open("buildings")
    post(pygame.MOUSEMOTION, pos=(int(ed.viewport.x + ed.viewport.w * 0.5), int(ed.viewport.y + ed.viewport.h * 0.6)),
         rel=(1, 1), buttons=(0, 0, 0))


at(4, wire_1)


def grid_1():
    t = eng.building_tool
    check("with the grid hidden the paint tool still follows the mouse", t.active and t.hover is not None, t.hover)
    eng.set_grid_visible(True)
    st["before_town"] = len(buildings())
    st["undo_before"] = len(ed.ui.undo._undo)
    press_button("Generate town")


at(5, grid_1)


def panel_1():
    n = len(buildings())
    check("'Generate town' in the Buildings panel adds a town",
          n > st["before_town"] + 30 and len(ed.ui.undo._undo) == st["undo_before"] + 1,
          (n, len(ed.ui.undo._undo) - st["undo_before"]))
    ed.ui.undo.undo()


at(8, panel_1)


def panel_2():
    check("... and it is ONE undo step", len(buildings()) == st["before_town"], len(buildings()))
    ed.ui.undo.redo()


at(4, panel_2)


def panel_3():
    check("... redo brings the town back", len(buildings()) > st["before_town"] + 30, len(buildings()))
    ed.ui.undo.undo()
    ed.tools.close()
    st["count"] = len(buildings())
    eng.save_project(PROJECT)
    eng.load_project(PROJECT) if hasattr(eng, "load_project") else None


at(4, panel_3)


def reload_1():
    b = buildings()
    g = [n for n in eng.scene.root.children if n.name.startswith("Town")]
    check("save + reload gives the same town in its group",
          len(b) == st["count"] and len(g) == 1 and all(n.parent is g[0] for n in b), (len(b), st["count"], len(g)))
    s = stats()
    check("... drawn in districts again", s["batched"] == len(b), s)
    ed.toggle_play()


at(8, reload_1)


def play_1():
    s = stats()
    check("in Play the town is drawn in districts too", eng._playing and s["batched"] == st["count"], s)
    hit = eng.physics.raycast(glm.vec3(20.0, 30.0, 4.0), glm.vec3(0.0, -1.0, 0.0), 60.0)
    check("... and its collision is there (a ray from above hits a roof)", hit is not None)
    ed.toggle_play()


at(8, play_1)
at(6, lambda: None)

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
    if not st["steps"] or st["f"] > 3000:
        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        shutil.rmtree(proj, ignore_errors=True)
        os._exit(0 if all(res) else 1)
    _flip()


pygame.display.flip = flip
eng.run()

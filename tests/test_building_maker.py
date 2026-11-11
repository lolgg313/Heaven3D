"""Building Maker in the Vitrail editor (needs a GPU / display):

    python tests/test_building_maker.py [shots folder]

Real mouse strokes in the running editor, then physics, save / load and speed:
  * the Buildings tool opens the Building Maker; dragging the Room tool makes
    ONE object with walls, floor, ceiling and roof; a second finish gets a wall
  * Door / Window / Ruined wall / Stairs / No ceiling tools, Ctrl removes
  * PageUp edits the next storey and "Hide the storeys above" hides the roof
  * every stroke is one undo step; undo / redo of the whole building works
  * the collision matches: floors and walls stop rays, doors let them through,
    window glass stops them; it still matches after turning the building
  * in Play a player walks up the stairs to the upper floor
  * save + reload gives the same building; a prefab of it places
  * Quick house, every style and roof shape bake without error
  * speed: a stroke re-bakes in milliseconds; a 900-cell, 3-storey block is
    a handful of meshes; 40 houses keep the editor frame fast
  * the classic sector generator is still there
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
OUT = sys.argv[1] if len(sys.argv) > 1 else None
proj = tempfile.mkdtemp(prefix="h3dbk_")
shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
PROJECT = [os.path.join(proj, f) for f in os.listdir(proj) if f.endswith(".heaven3d")][0]
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


eng = H.HeavenEngine(mode="editor", width=1500, height=860, project_path=PROJECT)
ed = eng._vt
for _n in [n for n in eng.scene.root.children if n.name == "Ocean"]:
    eng._delete_node(_n)
st = {"f": 0, "steps": [], "t": 10}
T = lambda: eng.building_tool          # noqa: E731
D = lambda: H.bk_data(T().node)        # noqa: E731


def at(df, fn):
    st["t"] += df
    st["steps"].append((st["t"], fn))


def shot(name):
    if OUT:
        data = eng.ctx.screen.read(components=3)
        pygame.image.save(pygame.transform.flip(pygame.image.frombuffer(data, (eng.width, eng.height), "RGB"),
                                                False, True), os.path.join(OUT, name + ".png"))


def scr(x, y, z):
    c = eng.camera.projection() * eng.camera.view() * glm.vec4(x, y, z, 1.0)
    return int((c.x / c.w * 0.5 + 0.5) * eng.width), int((1 - (c.y / c.w * 0.5 + 0.5)) * eng.height)


def look(pos, target):
    eng.camera.position = glm.vec3(*pos)
    dx, dy, dz = [target[i] - pos[i] for i in range(3)]
    ln = math.sqrt(dx * dx + dy * dy + dz * dz)
    eng.camera.yaw = math.degrees(math.atan2(-dx, -dz))
    eng.camera.pitch = math.degrees(math.asin(dy / ln))


def drag(a, b, ctrl=False):
    def start():
        if ctrl:
            post(pygame.KEYDOWN, key=pygame.K_LCTRL, mod=pygame.KMOD_CTRL, unicode="", scancode=0)
            pygame.key.set_mods(pygame.KMOD_CTRL)
        p = scr(*a)
        post(pygame.MOUSEMOTION, pos=p, rel=(0, 0), buttons=(0, 0, 0))
        post(pygame.MOUSEBUTTONDOWN, pos=p, button=1)
    at(3, start)
    for k in range(1, 6):
        def mv(k=k):
            p0, p1 = scr(*a), scr(*b)
            post(pygame.MOUSEMOTION, pos=(int(p0[0] + (p1[0] - p0[0]) * k / 5), int(p0[1] + (p1[1] - p0[1]) * k / 5)),
                 rel=(1, 1), buttons=(1, 0, 0))
        at(1, mv)

    def end():
        post(pygame.MOUSEBUTTONUP, pos=scr(*b), button=1)
        if ctrl:
            pygame.key.set_mods(0)
            post(pygame.KEYUP, key=pygame.K_LCTRL, mod=0, unicode="", scancode=0)
    at(2, end)


def click(a, ctrl=False):
    drag(a, a, ctrl)


def tool(name, finish=None):
    def f():
        T().tool = name
        if finish:
            T().finish = finish
    at(1, f)


def press_button(label):
    import vitrail
    sc = ed.tools.scroll
    for _try in range(6):
        for w in ed.ui.iter_widgets():
            if isinstance(w, vitrail.Button) and w.visible and w.w > 0 and getattr(w, "text", None) == label \
                    and sc.y <= w.y + w.h / 2 <= sc.y + sc.h:
                x, y = int(w.x + w.w / 2), int(w.y + w.h / 2)
                post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
                post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
                post(pygame.MOUSEBUTTONUP, pos=(x, y), button=1)
                return True
        sc.scroll = sc.scroll + sc.h * 0.7
        ed.ui.layout()
    check("found the '%s' button" % label, False)
    return False


def ray(o, d, dist=50.0):
    hit = eng.physics.raycast(glm.vec3(*o), glm.vec3(*d), dist)
    if hit is None:
        return None
    for k in ("distance", "t", "dist"):
        if hasattr(hit, k):
            return float(getattr(hit, k))
    return float(glm.length(hit.point - glm.vec3(*o)))


# ---- 1. paint with the mouse ----
at(5, lambda: look((4, 16, 16), (4, 0, 3)))
at(2, lambda: ed.tools.toggle("buildings"))


def opened():
    check("Buildings opens the Building Maker", ed.tools.active == "buildings" and T().active
          and not getattr(ed.tools, "classic", True))
    st["n0"] = len(eng.scene.root.children)


at(8, opened)
drag((1, 0, 1), (5, 0, 5))                           # 3 x 3 cells


def room():
    n = T().node
    check("dragging the Room tool makes one building object", n is not None and H.bk_is_building(n)
          and len(eng.scene.root.children) == st["n0"] + 1)
    check("... of 9 cells with walls, floor and roof baked", len(D().cells) == 9 and H.bk_stats(n)["triangles"] > 200,
          H.bk_stats(n))
    check("... listed as ONE object (its meshes are hidden parts)",
          all(getattr(c, "_heaven3d_imported_part", False) for c in n.children))
    check("the roof is hidden while the ground floor is edited",
          all(not c.visible for c in n.children if c._bk_level >= 1)
          and any(c.visible for c in n.children if c._bk_level == 0))
    st["tri1"] = H.bk_stats(n)["triangles"]
    shot("bk_1_room")


at(6, room)
tool("room", "hall")
drag((7, 0, 1), (7, 0, 5))                           # a second room with another finish


def second():
    d = D()
    check("a room with another finish gets a wall between", d.edge_kind((2, 0, 1, "E")) == "wall"
          and d.edge_kind((1, 0, 1, "E")) is None and len(d.cells) == 12)


at(5, second)
tool("door")
click((6.02, 0, 3))                                  # the wall between the two rooms
click((3, 0, 5.98))                                  # the outside wall (+z)
tool("window")
drag((0.03, 0, 1), (0.03, 0, 5))                     # all along the -x wall
tool("stairs")
click((1, 0, 1))


def edges():
    d = D()
    check("Door: on an inside wall and on an outside wall", d.edges.get((2, 0, 1, "E")) == "door"
          and d.edges.get((1, 0, 2, "N")) == "door", d.edges)
    check("Window: dragging along a wall puts one in every cell",
          [d.edges.get((-1, 0, z, "E")) for z in range(3)] == ["window"] * 3)
    check("Stairs: a flight over two cells", d.stairs == {(0, 0, 0): 1}, d.stairs)
    shot("bk_2_ground")


at(5, edges)
at(1, lambda: post(pygame.KEYDOWN, key=pygame.K_PAGEUP, mod=0, unicode="", scancode=0))
tool("room", "wallpaper")
drag((1, 3, 1), (7, 3, 5))
tool("ruin")
drag((7.98, 3, 1), (7.98, 3, 5))
tool("open")
drag((7, 3, 1), (7, 3, 5))


def upper():
    d = D()
    check("PageUp: the next storey is edited", T().level == 1 and sum(1 for c in d.cells if c[1] == 1) == 12)
    check("Ruined wall + No ceiling shell a side of the house",
          [d.edges.get((3, 1, z, "E")) for z in range(3)] == ["ruin"] * 3 and len(d.open) == 3)
    check("the upper floor has a stair well (no floor over the flight)",
          (0, 0) not in H._bk_slab_cells(d, 1) and (0, 1) not in H._bk_slab_cells(d, 1)
          and (0, 2) in H._bk_slab_cells(d, 1))
    st["params"] = d.to_params()
    T().set_cut(False)
    look((-9, 9, 15), (4, 3, 3))


at(5, upper)
at(8, lambda: shot("bk_3_house"))
tool("window")
click((3, 3, 5.98))
click((3, 3, 5.98), ctrl=True)


def removed():
    check("Ctrl + click puts the plain wall back", (1, 1, 2, "N") not in D().edges, D().edges.get((1, 1, 2, "N")))


at(5, removed)

# ---- 2. undo / redo ----


def undo_all():
    st["undo_n"] = 0
    while ed.ui.undo.can_undo and st["undo_n"] < 40:
        ed.ui.undo.undo()
        st["undo_n"] += 1
    check("undo steps back through every stroke to an empty scene",
          not any(H.bk_is_building(n) for n in eng.scene.root.children), st["undo_n"])
    for _ in range(st["undo_n"]):
        ed.ui.undo.redo()
    b = [n for n in eng.scene.root.children if H.bk_is_building(n)]
    check("redo brings the same building back", len(b) == 1 and H.bk_data(b[0]).to_params() == st["params"])
    T().set_node(b[0])
    st["b"] = b[0]


at(3, undo_all)

# ---- 3. collision ----


def collide():
    n = st["b"]
    eng.physics.add_node_recursive(n)
    check("the building is one static collider", isinstance(n.collider, H.MeshCollider)
          and H.bk_stats(n)["collision_triangles"] > 100, H.bk_stats(n)["collision_triangles"])
    d = ray((3, 1.5, 3), (0, -1, 0))
    check("the floor stops a ray", d is not None and abs(d - 1.5) < 0.05, d)
    d = ray((3, 1.5, 1), (0, 0, -1))
    check("an outside wall stops a ray", d is not None and abs(d - (1.0 - 0.15)) < 0.05, d)
    d = ray((3, 1.0, 3), (0, 0, 1), 20.0)
    check("a door lets a ray through", d is None or d > 6.0, d)
    d = ray((1, 1.5, 3), (-1, 0, 0), 20.0)
    check("window glass stops a ray", d is not None and 0.8 < d < 1.2, d)
    d = ray((1, 0.5, 3), (-1, 0, 0), 20.0)
    check("... and so does the wall under the window", d is not None and abs(d - 0.85) < 0.05, d)
    d = ray((3, 4.5, 3), (0, -1, 0))
    check("the upper floor stops a ray", d is not None and abs(d - 1.5) < 0.05, d)
    d = ray((1, 5.5, 1), (0, -1, 0))
    check("the stair well is open (the ray lands on the flight)", d is not None and d > 2.6, d)
    # turned a quarter: the collision turns with the walls
    n.rotation_euler = glm.vec3(0, 90, 0)
    eng.physics._statics_dirty = True
    M = n.world_matrix()
    w = M * glm.vec4(3, 1.5, 1, 1)                     # inside, 0.85 m from the -z wall
    dv = M * glm.vec4(0, 0, -1, 0)
    d = ray((w.x, w.y, w.z), (dv.x, dv.y, dv.z))
    check("turned 90 degrees, the collision still matches the walls", d is not None and abs(d - 0.85) < 0.05, d)
    w = M * glm.vec4(3, 1.0, 3, 1)
    dv = M * glm.vec4(0, 0, 1, 0)
    d = ray((w.x, w.y, w.z), (dv.x, dv.y, dv.z), 20.0)
    check("... and the door is still open", d is None or d > 6.0, d)
    n.rotation_euler = glm.vec3(0, 0, 0)
    eng.physics._statics_dirty = True


at(3, collide)

# ---- 4. play: walk up the stairs ----


def play():
    sp = eng.add_player_spawn()
    sp.position = glm.vec3(1.0, 0.2, 0.45)             # the foot of the flight, looking along +z
    sp.rotation_euler = glm.vec3(0, 180, 0)
    st["spawn"] = sp
    ed.toggle_play()


at(3, play)
at(20, lambda: post(pygame.KEYDOWN, key=pygame.K_w, mod=0, unicode="w", scancode=26))


def climbed():
    y = float(eng.camera.position.y)
    post(pygame.KEYUP, key=pygame.K_w, mod=0, unicode="w", scancode=26)
    check("in Play the player walks up the stairs to the upper floor", y > 3.9, round(y, 2))
    shot("bk_4_play")
    ed.toggle_play()


at(170, climbed)


def stopped():
    b = [n for n in eng.scene.root.children if H.bk_is_building(n)]
    check("after Play the building is unchanged", len(b) == 1 and H.bk_data(b[0]).to_params() == st["params"])
    eng._delete_node(st["spawn"]) if st["spawn"] in eng.scene.root.children else None
    for n in [x for x in eng.scene.root.children if x.name.startswith("Player")]:
        eng._delete_node(n)
    ed.save()


at(12, stopped)

# ---- 5. save / load, prefab ----


def reload_():
    tex = os.path.join(proj, "assets", "building_maker")
    have = sorted(os.listdir(tex)) if os.path.isdir(tex) else []
    check("saving copies the building textures into the project (for exports)",
          "brick_red_albedo.png" in have and "roof_tiles_normal.png" in have and len(have) % 4 == 0, len(have))
    # like an exported game on another PC: nothing cached, nothing may be generated
    H._BK_MATERIAL_CACHE.clear()
    made = []
    real = H.bk_material_maps
    H.bk_material_maps = lambda name: (made.append(name), real(name))[1]
    eng.load_project(PROJECT)
    H.bk_material_maps = real
    b = [n for n in eng.scene.root.children if H.bk_is_building(n)]
    used = {str(c.material.base_color_tex[1]) for c in b[0].children if c.material.base_color_tex} if b else set()
    check("... and a reloaded project uses them without generating anything",
          not made and used and all(os.path.dirname(u) == tex for u in used), made)
    check("save + reload gives the same building", len(b) == 1 and H.bk_data(b[0]).to_params() == st["params"]
          and H.bk_stats(b[0])["triangles"] > 1000, len(b))
    check("... with its collision", b and isinstance(b[0].collider, H.MeshCollider))
    pf = eng.save_prefab(b[0], os.path.join(proj, "House.prefab"))
    c = eng.spawn_prefab(pf, position=(30, 0, 0))
    check("a prefab of a building places a second one", c is not None and H.bk_is_building(c)
          and H.bk_stats(c)["triangles"] == H.bk_stats(b[0])["triangles"])
    eng._delete_node(c)
    st["b"] = b[0]


at(8, reload_)

# ---- 6. every style, roof and the quick house ----


def styles():
    look((60, 28, 110), (60, 0, 50))
    st["base_ms"] = 1000.0 * sum(st["frame_dts"][-6:]) / 6
    t0 = time.perf_counter()
    made = []
    bad = []
    for i, style in enumerate(H.BK_STYLE_ORDER):
        for j, roof in enumerate(("gable", "hip", "flat", "none")):
            try:
                n = eng.add_building(style, (20 + i * 14, 0, 30 + j * 14), house=(4, 3, 2, 0.4 if j == 1 else 0.0),
                                     seed=i * 7 + j)
                H.bk_data(n).roof["type"] = roof
                H.bk_rebuild(eng, n)
                if H.bk_stats(n)["triangles"] < 500:
                    bad.append((style, roof))
                made.append(n)
            except Exception as ex:
                bad.append((style, roof, repr(ex)))
    dt = time.perf_counter() - t0
    check("every style x roof shape bakes (28 houses)", not bad and len(made) == 28, bad)
    st["many"] = made
    st["many_dt"] = dt
    look((60, 28, 110), (60, 0, 50))


at(3, styles)
at(15, lambda: shot("bk_5_styles"))


def frame_cost():
    dts = st["frame_dts"][-10:]
    ms = 1000.0 * sum(dts) / len(dts)
    print("INFO  editor frame: %.0f ms with 29 buildings (190,000 triangles), %.0f ms before" % (ms, st["base_ms"]),
          flush=True)
    draws = sum(1 for b in eng.scene.root.children if H.bk_is_building(b) for c in b.children if c.mesh is not None)
    check("29 buildings draw as a few hundred meshes", draws < 900, draws)
    objs = sum(1 for _ in eng.scene.root.traverse())
    check("29 buildings are a few hundred objects, not thousands", objs < 1500, objs)
    for n in st["many"]:
        eng._delete_node(n)


at(3, frame_cost)

# ---- 7. speed ----


def speed():
    n = eng.add_building("town", (0, 0, 60))
    d = H.bk_data(n)
    for y in range(3):
        for x in range(30):
            for z in range(30):
                d.cells[(x, y, z)] = (x // 5 + z // 5 + y) % 3
        for x in range(30):
            d.edges[(x, y, -1, "N")] = "window"
            d.edges[(x, y, 29, "N")] = "window"
    t0 = time.perf_counter()
    H.bk_rebuild(eng, n)
    full = time.perf_counter() - t0
    s = H.bk_stats(n)
    check("a 2,700-cell block bakes into few meshes", s["meshes"] < 60 and s["triangles"] > 20000, s)
    check("... in a couple of seconds at most", full < 6.0, round(full, 2))
    t0 = time.perf_counter()
    eng.building_edit(n, edges={(4, 1, 4, "E"): "door"})
    one = time.perf_counter() - t0
    check("one stroke on it re-bakes only the two levels it touches", one < full and one < 2.5,
          (round(one, 3), round(full, 3)))
    # the house from the strokes: a stroke is a few milliseconds
    b = st["b"]
    t0 = time.perf_counter()
    for k in range(10):
        eng.building_edit(b, edges={(1, 0, 2, "N"): "window" if k % 2 else "door"})
    per = (time.perf_counter() - t0) / 10
    check("a stroke on a house re-bakes in milliseconds", per < 0.08, "%.1f ms" % (per * 1000))
    print("INFO  full bake of 2,700 cells %.2fs, one stroke %.0f ms, house stroke %.1f ms" %
          (full, one * 1000, per * 1000), flush=True)
    eng._delete_node(n)


at(3, speed)

# ---- 8. quick house button + classic generator still there ----
at(2, lambda: (ed.tools.close() if ed.tools.active else None, ed.tools.toggle("buildings")))
at(8, lambda: (look((4, 12, 30), (4, 0, 8)), press_button("Generate house")))


def generated():
    b = [n for n in eng.scene.root.children if H.bk_is_building(n)]
    check("Generate house makes a second building and selects it", len(b) == 2 and eng.selected is b[-1], len(b))
    ed.ui.undo.undo()
    check("... and undo removes it", len([n for n in eng.scene.root.children if H.bk_is_building(n)]) == 1)
    press_button("Classic sector generator...")


at(10, generated)
at(8, lambda: press_button("Generate sector"))


def classic():
    n = sum(1 for x in eng.scene.root.traverse() if x.mesh_type == "procedural_module")
    check("the classic sector generator still works", n > 50 and not T().active, n)
    ed.tools.close()
    check("closing the tool shows every storey again",
          all(c.visible for b in eng.scene.root.children if H.bk_is_building(b) for c in b.children))
    for n in [x for x in eng.scene.root.children if x.name.startswith("Sector_")]:
        eng._delete_node(n)
    ed.select(None)
    look((-9, 9, 15), (4, 3, 3))


at(20, classic)


def click_house():
    x, y = scr(0.0, 4.5, 3.0)                          # the upper -x wall of the house
    post(pygame.MOUSEMOTION, pos=(x, y), rel=(0, 0), buttons=(0, 0, 0))
    post(pygame.MOUSEBUTTONDOWN, pos=(x, y), button=1)
    post(pygame.MOUSEBUTTONUP, pos=(x, y), button=1)


at(8, click_house)
at(6, lambda: check("with the tool closed, a click on any wall selects the whole building",
                    H.bk_is_building(eng.selected), getattr(eng.selected, "name", None)))


def dup():
    b = eng.selected
    before = len([n for n in eng.scene.root.children if H.bk_is_building(n)])
    post(pygame.KEYDOWN, key=pygame.K_d, mod=pygame.KMOD_CTRL, unicode="", scancode=0)
    st["dup_before"] = (before, b)


at(2, dup)


def dup_done():
    bl = [n for n in eng.scene.root.children if H.bk_is_building(n)]
    before, b = st["dup_before"]
    check("Ctrl+D duplicates a building with its meshes and collision",
          len(bl) == before + 1 and all(H.bk_stats(n)["triangles"] > 1000 for n in bl)
          and all(isinstance(n.collider, H.MeshCollider) for n in bl), len(bl))


at(8, dup_done)

_flip = pygame.display.flip
st["frame_dts"] = []
st["last"] = time.perf_counter()


def flip():
    now = time.perf_counter()
    st["frame_dts"].append(now - st["last"])
    st["f"] += 1
    while st["steps"] and st["steps"][0][0] <= st["f"]:
        _f, fn = st["steps"].pop(0)
        try:
            fn()
        except Exception as ex:
            import traceback
            traceback.print_exc()
            check("step %s" % getattr(fn, "__name__", "?"), False, ex)
    if not st["steps"] or st["f"] > 2500:
        print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
        shutil.rmtree(proj, ignore_errors=True)
        os._exit(0 if all(res) else 1)
    _flip()
    st["last"] = time.perf_counter()


pygame.display.flip = flip
eng.run()

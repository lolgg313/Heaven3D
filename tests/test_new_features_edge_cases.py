"""Edge cases of the node materials, prefabs and drag & drop (needs a GPU / display):

    python tests/test_new_features_edge_cases.py

Tries to break them on purpose: broken / looping graphs, odd image files,
damaged .vmat files, read-only installs, unzipped games with new file dates,
prefabs holding materials / a Player Spawn / unicode names, scripts with
errors, duplicate script names, and an exported-game style move of the project.
"""
import importlib.util
import json
import os
import shutil
import stat
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
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


def safely(name, fn):
    try:
        return fn()
    except Exception as ex:  # noqa: BLE001
        import traceback
        traceback.print_exc()
        check(name + " (raised)", False, repr(ex))
        return None


def img_graph(fname, extra_nodes=(), extra_links=()):
    return {"nodes": [{"id": 1, "type": "Texture/Image", "pos": [0, 0], "values": {"File": fname, "Tiling": 1.0}},
                      {"id": 9, "type": "Output/Material Output", "pos": [300, 0], "values": {}}] + list(extra_nodes),
            "links": [[1, "Color", 9, "Base Color"]] + list(extra_links)}


def main():
    work = tempfile.mkdtemp(prefix="h3dedge_")
    os.chdir(work)
    e = H.HeavenEngine(mode="editor", width=640, height=420)
    e.project_path = os.path.join(work, "main.heaven3d")
    mats = os.path.join(work, "assets", "materials")
    os.makedirs(mats)

    # ---- odd image files in an Image node ----
    s = pygame.Surface((8, 8))
    s.fill((200, 10, 10))
    pal = pygame.Surface((8, 8), depth=8)
    pal.set_palette([(200, 10, 10)] + [(0, 0, 0)] * 255)
    pal.fill(0)
    pygame.image.save(pal, os.path.join(mats, "palette.png"))
    grey = pygame.Surface((8, 8), depth=8)
    grey.set_palette([(i, i, i) for i in range(256)])
    grey.fill(120)
    pygame.image.save(grey, os.path.join(mats, "grey.png"))
    pygame.image.save(s, os.path.join(mats, "plain.jpg"))
    pygame.image.save(s, os.path.join(mats, "plain.tga"))
    from PIL import Image as PILImage
    PILImage.new("RGB", (8, 8), (200, 10, 10)).save(os.path.join(mats, "dxt.dds"), pixel_format="DXT1")
    PILImage.new("RGB", (8, 8), (200, 10, 10)).save(os.path.join(mats, "pic.webp"))
    pygame.image.save(s, os.path.join(mats, "ünïcödé name.png"))
    for f in ("palette.png", "grey.png", "plain.jpg", "plain.tga", "dxt.dds", "pic.webp", "ünïcödé name.png"):
        p = H.material_asset_create(os.path.join(mats, "img_" + os.path.splitext(f)[0]))
        v = safely("image " + f, lambda: H.material_vmat_write(p, img_graph(f)))
        ok = v is not None and v.get("base_color_tex") and os.path.isfile(v["base_color_tex"])
        px = None
        if ok:
            px = pygame.image.load(v["base_color_tex"]).get_at((4, 4))
            want_grey = f == "grey.png"
            ok = (abs(px[0] - 120) < 6 and abs(px[1] - 120) < 6) if want_grey else (px[0] > 180 and px[1] < 40)
        check("Image node reads %s" % f, ok, px)

    # ---- missing image, broken / looping graphs, damaged files ----
    p = H.material_asset_create(os.path.join(mats, "missing"))
    v = safely("missing image", lambda: H.material_vmat_write(p, img_graph("not_there.png")))
    check("a missing image shows magenta (not a crash)", v is not None and v["base_color"][:3] == [1.0, 0.0, 1.0],
          v and v["base_color"])
    loop = {"nodes": [{"id": 1, "type": "Math/Add", "pos": [0, 0], "values": {}},
                      {"id": 2, "type": "Math/Multiply", "pos": [0, 0], "values": {}},
                      {"id": 9, "type": "Output/Material Output", "pos": [0, 0], "values": {}}],
            "links": [[1, "Result", 2, "A"], [2, "Result", 1, "A"], [2, "Result", 9, "Roughness"]]}
    p = H.material_asset_create(os.path.join(mats, "loop"))
    v = safely("looping graph", lambda: H.material_vmat_write(p, loop))
    check("a looping graph bakes without hanging", v is not None)
    p = H.material_asset_create(os.path.join(mats, "noout"))
    v = safely("no output", lambda: H.material_vmat_write(p, {"nodes": [], "links": []}))
    check("a graph without a Material Output falls back to a plain material", v is not None)
    unknown = {"nodes": [{"id": 1, "type": "Plugin/Gone", "pos": [0, 0], "values": {}},
                         {"id": 9, "type": "Output/Material Output", "pos": [0, 0], "values": {}}],
               "links": [[1, "X", 9, "Base Color"]]}
    p = H.material_asset_create(os.path.join(mats, "unknown"))
    v = safely("unknown node", lambda: H.material_vmat_write(p, unknown))
    check("an unknown node type is skipped", v is not None)
    bad = os.path.join(mats, "damaged.vmat")
    open(bad, "w").write("{ this is not json")
    cube = e.add_cube(name="C")
    v = safely("damaged vmat", lambda: e.assign_material_asset(cube, bad))
    check("a damaged .vmat still gives a plain material", v is not None and cube.material is not None)
    p = H.material_asset_create(os.path.join(mats, "res"))
    H.material_vmat_write(p, img_graph("plain.jpg"), resolution=64)
    v = H.material_vmat_write(p, img_graph("plain.jpg"), resolution=128)
    check("changing the bake resolution re-bakes", pygame.image.load(v["base_color_tex"]).get_size() == (128, 128))
    noise = {"nodes": [{"id": 1, "type": "Texture/Noise", "pos": [0, 0], "values": {}},
                       {"id": 9, "type": "Output/Material Output", "pos": [0, 0], "values": {}}],
             "links": [[1, "Fac", 9, "Roughness"]]}
    p = H.material_asset_create(os.path.join(mats, "switch"))
    v1 = H.material_vmat_write(p, noise)
    v2 = H.material_vmat_write(p, {"nodes": noise["nodes"], "links": []})
    check("a slot that becomes constant drops its old bake",
          v1.get("metallic_roughness_tex") and not v2.get("metallic_roughness_tex")
          and not os.path.exists(v1["metallic_roughness_tex"]))

    # ---- unzipped game: new file dates must not re-bake; read-only install ----
    p = H.material_asset_create(os.path.join(mats, "dated"))
    H.material_vmat_write(p, img_graph("plain.jpg"))
    os.utime(os.path.join(mats, "plain.jpg"), (1, 1))
    check("new file dates (unzipped game) don't trigger a re-bake", H.material_asset_build(p).get("rebaked") is False)
    if os.name != "nt" and os.geteuid() != 0:
        bdir = os.path.join(mats, ".baked")
        d = json.load(open(p))
        d["graph"]["nodes"][0]["values"]["Tiling"] = 2.0
        json.dump(d, open(p, "w"))
        os.chmod(bdir, stat.S_IRUSR | stat.S_IXUSR)
        try:
            v = safely("read-only bake", lambda: H.material_asset_build(p))
            check("read-only install: the saved bake is used", v is not None and v.get("base_color_tex"))
        finally:
            os.chmod(bdir, stat.S_IRWXU)
    else:
        print("SKIP read-only install check (running as root / Windows)")

    # ---- materials + prefabs + duplicate + reload ----
    brick = H.material_asset_create(os.path.join(mats, "Brick"))
    H.material_vmat_write(brick, {"nodes": [{"id": 1, "type": "Texture/Bricks", "pos": [0, 0], "values": {}},
                                            {"id": 9, "type": "Output/Material Output", "pos": [0, 0], "values": {}}],
                                  "links": [[1, "Color", 9, "Base Color"]]})
    wall = e.add_cube(name="Wall")
    e.assign_material_asset(wall, brick)
    pf = e.save_prefab(wall, "assets/prefabs/Brick Wall ü")
    inst = e.spawn_prefab(pf, position=(4, 0, 0))
    check("a prefab carries its material asset (unicode file name too)",
          os.path.normcase(str(getattr(inst, "material_asset", ""))) == os.path.normcase(brick)
          and inst.material.base_color_tex is not None)
    check("a fresh prefab instance is not 'modified'", not e.prefab_is_modified(inst))
    e._duplicate_node(inst)
    dup = e.selected
    check("Duplicate keeps the material link", getattr(dup, "material_asset", None) is not None)
    holder = e.add_cube(name="Holder")
    ps = e.add_player_spawn() if e.find_player_spawn() is None else e.find_player_spawn()
    ps.remove()
    holder.add_child(ps)
    try:
        e.save_prefab(holder, "assets/prefabs/Bad")
        check("a prefab holding the Player Spawn is refused", False)
    except ValueError as ex:
        check("a prefab holding the Player Spawn is refused", "Player Spawn" in str(ex), str(ex))
    ps.remove()
    e.scene.root.add_child(ps)
    e.save_project(e.project_path)
    e.load_project(e.project_path)
    ws = [n for n in e.scene.root.traverse() if getattr(n, "material_asset", None)]
    check("after reload every material link is back", len(ws) >= 4, len(ws))
    insts = e.prefab_instances(pf)
    check("after reload prefab instances are not 'modified'", insts and not any(e.prefab_is_modified(n) for n in insts),
          [(n.name, e.prefab_is_modified(n)) for n in insts])

    # ---- the project moved (like an exported game) ----
    moved = work + "_moved"
    shutil.copytree(work, moved)
    os.chdir(moved)
    e.project_path = os.path.join(moved, "main.heaven3d")
    e.load_project(e.project_path)
    w2 = [n for n in e.scene.root.traverse() if getattr(n, "material_asset", None)]
    check("a moved project uses its own copy of the materials",
          w2 and all(os.path.normcase(n.material_asset).startswith(os.path.normcase(moved)) for n in w2)
          and all(str(n.material.base_color_tex[1]).startswith(moved) for n in w2 if n.material.base_color_tex))

    # ---- scripts ----
    sdir = os.path.join(moved, "scripts")
    os.makedirs(sdir, exist_ok=True)
    open(os.path.join(sdir, "broken.py"), "w").write("from Heaven3D import Blueprint, blueprint\nclass X(:\n")
    open(os.path.join(sdir, "nothing.py"), "w").write("x = 1\n")
    sys.path.insert(0, ROOT)
    from h3d_editor.scripting import hot_reload
    ok, msg, line = hot_reload(H, e, os.path.join(sdir, "broken.py"))
    check("a script with a syntax error is reported (not attached)", not ok and line == 2, (ok, msg, line))
    ok, msg, line = hot_reload(H, e, os.path.join(sdir, "nothing.py"))
    check("a script without blueprints loads quietly", ok, msg)
    shutil.rmtree(work, ignore_errors=True)
    shutil.rmtree(moved, ignore_errors=True)
    print("\n%d/%d passed" % (sum(res), len(res)))
    return 0 if all(res) else 1


if __name__ == "__main__":
    try:
        code = main()
    except Exception:
        import traceback
        traceback.print_exc()
        code = 1
    sys.stdout.flush()
    os._exit(code)

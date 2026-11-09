"""Node materials (.vmat) in the real engine (needs a GPU / display):

    python tests/test_material_graph.py [screenshot.png]

  * every node in the library evaluates without errors
  * procedural textures tile seamlessly (left/right and top/bottom edges match)
  * a graph bakes into PBR textures; constant inputs are not baked
  * a re-open with no change does not re-bake; an edit does
  * assigning a .vmat to objects (and to every part of an imported model),
    live refresh of every user, detach
  * save + reload keeps the material link; a changed .vmat is picked up on load
  * a .vmat made by the asset browser (no graph yet) works as a plain material
"""
import importlib.util
import json
import os
import shutil
import sys
import tempfile

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
SHOT = sys.argv[1] if len(sys.argv) > 1 else None
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


def bricks_graph(rough_seed=3):
    return {"nodes": [
        {"id": 1, "type": "Texture/Bricks", "pos": [0, 0], "values": {"Scale": 8}},
        {"id": 2, "type": "Texture/Noise", "pos": [0, 300], "values": {"Scale": 6, "Seed": rough_seed}},
        {"id": 3, "type": "Math/Map Range", "pos": [200, 300], "values": {"To Min": 0.55, "To Max": 0.95}},
        {"id": 4, "type": "Vector/Bump", "pos": [200, 150], "values": {"Strength": 2.0}},
        {"id": 9, "type": "Output/Material Output", "pos": [420, 80], "values": {}}],
        "links": [[1, "Color", 9, "Base Color"], [1, "Fac", 4, "Height"], [4, "Normal", 9, "Normal"],
                  [2, "Fac", 3, "Value"], [3, "Result", 9, "Roughness"]]}


def main():
    work = tempfile.mkdtemp(prefix="h3dmat_")
    os.chdir(work)
    e = H.HeavenEngine(mode="editor", width=900, height=600)
    e.project_path = os.path.join(work, "main.heaven3d")
    for n in e.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False

    # ---- every node evaluates ----
    img = os.path.join(work, "tex.png")
    s = pygame.Surface((64, 32), pygame.SRCALPHA)
    s.fill((200, 40, 40, 255))
    pygame.image.save(s, img)
    bad = []
    for path, sp in H.MATERIAL_NODES.items():
        vals = {nm: d for nm, _t, d in sp["inputs"] if d is not None}
        if "File" in vals:
            vals["File"] = img
        try:
            ctx_n = 64
            k = (np.arange(ctx_n, dtype=np.float32) + 0.5) / ctx_n
            u, v = np.meshgrid(k, k)
            ins = {nm: (tuple(x) if isinstance(x, list) else x) for nm, x in vals.items()}
            for nm, _t, d in sp["inputs"]:
                ins.setdefault(nm, d)
            if "Height" in ins:
                ins["Height"] = u
            out = sp["fn"](ins, {"n": ctx_n, "u": u, "v": v, "base_dir": work})
            for name, _t in sp["outputs"]:
                if name not in out:
                    bad.append("%s: no %s" % (path, name))
        except Exception as ex:   # noqa: BLE001
            bad.append("%s: %r" % (path, ex))
    check("all %d material nodes evaluate" % len(H.MATERIAL_NODES), not bad, bad[:3])

    # ---- tiling ----
    ctx = {"n": 128}
    k = (np.arange(128, dtype=np.float32) + 0.5) / 128
    ctx["u"], ctx["v"] = np.meshgrid(k, k)
    f = H._mg_fbm(ctx, 4, 5, 0.5, 7)
    d, _c = H._mg_voronoi(ctx, 5, 3)
    edge = lambda a: max(float(np.abs(a[:, 0] - a[:, -1]).max()), float(np.abs(a[0] - a[-1]).max()))  # noqa
    inner = lambda a: float(np.abs(np.diff(a, axis=1)).max())  # noqa
    check("noise tiles seamlessly", edge(f) <= inner(f) * 1.5 + 1e-4, (edge(f), inner(f)))
    check("voronoi tiles seamlessly", edge(d) <= inner(d) * 1.5 + 1e-4, (edge(d), inner(d)))

    # ---- bake ----
    mdir = os.path.join(work, "assets", "materials")
    vm = H.material_asset_create(os.path.join(mdir, "Bricks"))
    check("material_asset_create makes a .vmat", vm.endswith(".vmat") and os.path.isfile(vm))
    vals = H.material_vmat_write(vm, bricks_graph())
    check("graph bakes base colour + normal + metal/rough",
          all(vals.get(k) and os.path.isfile(vals[k]) for k in ("base_color_tex", "normal_tex",
                                                               "metallic_roughness_tex")), vals)
    check("constant emission is not baked", vals.get("emissive_tex") is None and vals["emissive"] == [0, 0, 0])
    check("baked files are hidden in .baked/", os.path.basename(os.path.dirname(vals["base_color_tex"])) == ".baked")
    again = H.material_asset_build(vm)
    check("re-opening without changes does not re-bake", again.get("rebaked") is False)
    data = json.load(open(vm))
    check("the .vmat keeps a colour summary for the asset browser",
          data.get("base_color") and 0.2 < data["base_color"][0] < 0.8, data.get("base_color"))

    # ---- assign ----
    cube = e.add_cube(name="Wall")
    ball = e.add_sphere(name="Ball")
    ball.position = glm.vec3(2.2, 0.5, 0)
    cube.position = glm.vec3(0, 0.5, 0)
    e.assign_material_asset(cube, vm)
    e.assign_material_asset(ball, "assets/materials/Bricks.vmat")
    check("assign sets textures on the object", cube.material.base_color_tex is not None
          and cube.material.normal_tex is not None)
    check("project-relative paths work", os.path.normcase(ball.material_asset) == os.path.normcase(vm))
    check("users are tracked", len(e.material_asset_users(vm)) == 2)
    glb_dir = os.path.join(ROOT, "tests", "importers", "assets", "glb")
    truck = os.path.join(glb_dir, "CesiumMilkTruck.glb")
    if os.path.isfile(truck):
        t = e.load_model(truck)
        t.position = glm.vec3(-4, 0, 0)
        e.assign_material_asset(t, vm)
        parts = [c for c in t.children if getattr(c, "_heaven3d_imported_part", False)]
        check("every part of an imported model gets it",
              all(p.material.normal_tex is not None for p in parts) and parts, len(parts))

    # ---- live edit ----
    g = bricks_graph()
    g["nodes"][0]["values"]["Color"] = [0.1, 0.3, 0.8, 1.0]
    g["nodes"][0]["values"]["Color 2"] = [0.1, 0.2, 0.6, 1.0]
    H.material_vmat_write(vm, g)
    n = e.refresh_material_asset(vm)
    check("refresh re-bakes and updates every user", n >= 2)
    tex = e.renderer.get_texture(cube.material.base_color_tex)
    px = np.frombuffer(tex.read(), np.uint8).reshape(-1, 4)
    check("the GPU texture shows the edit (no stale cache)", px[:, 2].mean() > px[:, 0].mean(),
          (int(px[:, 0].mean()), int(px[:, 2].mean())))

    if SHOT:
        e.camera.position = glm.vec3(1.1, 2.0, 4.2)
        e.camera.yaw, e.camera.pitch = 0.0, -22.0
        for _ in range(3):
            e.renderer.render(e.scene, e.camera)
        e.ctx.finish()
        im = pygame.transform.flip(pygame.image.frombuffer(e.ctx.screen.read(components=3),
                                                           (e.width, e.height), "RGB"), False, True)
        pygame.image.save(im, SHOT)

    # ---- save / reload ----
    e.save_project(e.project_path)
    saved = json.load(open(e.project_path))
    links = [c.get("material_asset") for c in saved["scene"]["children"] if c.get("material_asset")]
    check("the project stores the material link (relative)", links and not os.path.isabs(links[0]), links[:2])
    g["nodes"][0]["values"]["Color"] = [0.2, 0.7, 0.2, 1.0]
    g["nodes"][0]["values"]["Color 2"] = [0.15, 0.6, 0.15, 1.0]
    vmd = json.load(open(vm))
    vmd["graph"] = g
    json.dump(vmd, open(vm, "w"))                    # edited outside, not re-baked yet
    e.load_project(e.project_path)
    w = [x for x in e.scene.root.traverse() if x.name == "Wall"][0]
    tex = e.renderer.get_texture(w.material.base_color_tex)
    px = np.frombuffer(tex.read(), np.uint8).reshape(-1, 4)
    check("a changed .vmat is re-baked on load", px[:, 1].mean() > px[:, 2].mean(),
          (int(px[:, 1].mean()), int(px[:, 2].mean())))

    # ---- detach ----
    e.detach_material_asset(w)
    check("detach keeps the look and forgets the asset", w.material_asset is None and w.material.normal_tex)

    # ---- asset-browser style .vmat (no graph) ----
    plain = os.path.join(mdir, "Plain.vmat")
    json.dump({"type": "material", "base_color": [0.9, 0.1, 0.1, 1.0], "roughness": 0.3, "metallic": 1.0,
               "emission": [0, 0, 0], "emission_strength": 0.0}, open(plain, "w"))
    e.assign_material_asset(ball, plain)
    m = [x for x in e.scene.root.traverse() if x.name == "Ball"][0].material
    m = ball.material
    check("a .vmat without a graph is a plain material",
          abs(m.base_color[0] - 0.9) < 1e-4 and m.metallic == 1.0 and m.base_color_tex is None,
          (m.base_color, m.metallic))
    shutil.rmtree(work, ignore_errors=True)
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

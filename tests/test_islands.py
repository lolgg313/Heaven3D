"""Tropical Islands package (needs a GPU / llvmpipe):  python tests/test_islands.py

Generation of every topology (heightmap, beaches, roads, slope material,
vegetation), the engine integration (terrain shader, chunked vegetation with
LODs, no GL errors), live rebuild when a property changes, Play on the island
(player stands on the heightfield), save -> reload from the cache, and the
exported-game (runtime) mode."""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ENGINE = os.path.join(ROOT, "Heaven3D.py")
PKG = os.path.join(ROOT, "packages", "tropical_islands")
OUT = os.environ.get("H3D_SHOTS") or tempfile.mkdtemp(prefix="h3disl_shots_")
os.makedirs(OUT, exist_ok=True)
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""), flush=True)


def boot(project, mode):
    import importlib.util
    spec = importlib.util.spec_from_file_location("Heaven3D", ENGINE)
    H = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = H
    spec.loader.exec_module(H)
    H._h3d_set_splash_mode("off")
    eng = H.HeavenEngine(mode=mode, width=1280, height=720, project_path=os.path.join(project, "main.heaven3d"))
    return H, eng


def shot(name, W=1280, Hh=720):
    import moderngl
    import pygame
    data = moderngl.get_context().screen.read(components=3)
    img = pygame.transform.flip(pygame.image.frombuffer(data, (W, Hh), "RGB"), False, True)
    pygame.image.save(img, os.path.join(OUT, name + ".png"))
    return img


def phase_generator():
    """Pure generator checks (no GL)."""
    sys.path.insert(0, PKG)
    import numpy as np
    import island_gen as G
    for topo in G.TOPOLOGIES:
        t = time.time()
        isl = G.generate({"topology": topo, "seed": 5, "size": 450})
        dt = time.time() - t
        h = isl.heights
        land = float(np.mean(h > 0.5))
        trees = sum(len(isl.instances[k]) for k in ("coconut_palm", "royal_palm", "island_tree", "jacaranda", "acacia"))
        check(f"generator {topo}: land, beach, trees", 0.03 < land < 0.75 and h.max() > 3 and trees > 30,
              f"land {land:.0%}, peak {h.max():.0f} m, {trees} trees, {dt:.1f} s")
    isl = G.generate({"topology": "classic", "seed": 9, "size": 600})
    gy, gx = np.gradient(isl.heights, isl.cell)
    slope = np.sqrt(gx * gx + gy * gy)
    sp = G.bilinear(isl.splat.astype(np.float32) / 255.0, *np.meshgrid(np.linspace(0, 1, isl.heights.shape[0]),
                                                                      np.linspace(0, 1, isl.heights.shape[0])))
    steep = slope > 1.5
    flat_beach = (isl.heights > 0.3) & (isl.heights < isl.beach_top - 0.5) & (slope < 0.2)
    check("slope auto-material: cliffs are rock, low flat shore is sand",
          (not steep.any() or sp[steep][:, 3].mean() > 0.5) and sp[flat_beach][:, 0].mean() > 0.6,
          f"rock on cliffs {sp[steep][:, 3].mean() if steep.any() else 1:.2f}, sand on shore {sp[flat_beach][:, 0].mean():.2f}")
    ok_roads = len(isl.roads) >= 1
    grades = []
    for r in isl.roads:
        seg = np.hypot(np.diff(r[:, 0]), np.diff(r[:, 2]))
        grades.append(np.percentile(np.abs(np.diff(r[:, 1])) / np.maximum(seg, 1e-3), 95))
    check("dirt roads generated with drivable grades", ok_roads and max(grades) < 0.35,
          f"{len(isl.roads)} roads, 95% grade {max(grades) if grades else 0:.2f}")
    a = G.generate({"seed": 3, "size": 400})
    b = G.generate({"seed": 3, "size": 400})
    c = G.generate({"seed": 4, "size": 400})
    check("same seed = same island, new seed = new island",
          np.array_equal(a.heights, b.heights) and not np.array_equal(a.heights, c.heights))
    small = G.generate({"seed": 3, "size": 250})
    big = G.generate({"seed": 3, "size": 1400})
    check("island size scales", big.extent > small.extent * 4 and np.mean(big.heights > 0) > 0.02)


def phase_editor(project):
    import numpy as np
    H, eng = boot(project, "editor")
    import glm
    import pygame
    mod = sys.modules["h3dpkg_tropical_islands"]
    tools = [t["label"] for t in H._H3D27_PKG_STATE["tools"]]
    check("package tools registered", any("new tropical island" in t for t in tools), tools)
    check("shader hooks applied (terrain, wind, alpha-tested shadows)", mod.STATE["shaders_ok"])
    if eng.find_player_spawn() is None:
        eng.add_player_spawn()
    t = time.time()
    node = mod.new_island(eng, {"seed": 11, "size": 450, "topology": "classic"}, select=False)
    rt = mod.runtime_of(node)
    check("island built", rt is not None and rt.isl is not None and len(rt.terrain_nodes) > 10,
          f"{time.time() - t:.1f} s, {len(rt.terrain_nodes)} terrain chunks")
    counts = {k: len(v) for k, v in rt.isl.instances.items()}
    check("5 trees, 2 grasses, 2 flowers, 2 rock kinds placed", all(counts.get(k, 0) > 0 for k in mod.SPECIES), counts)
    check("heightfield collider on the island", node.collider is not None and node.collider.heights.shape == rt.isl.heights.shape)
    sp = eng.find_player_spawn()
    x, z = sp.position.x, sp.position.z
    check("player spawn moved onto the island", rt.height_at(x, z) > 0.0 and sp.position.y > rt.height_at(x, z),
          f"spawn {x:.0f}, {sp.position.y:.1f}, {z:.0f}")
    state = {"f": 0}
    _flip = pygame.display.flip

    def frame():
        f = state["f"] = state["f"] + 1
        e = rt.isl.extent
        if f == 2:
            eng.camera.position = glm.vec3(0.0, e * 0.3, e * 0.5)
            eng.camera.yaw, eng.camera.pitch = 0.0, -32.0
        if f == 6:
            err = eng.ctx.error
            check("no GL errors while drawing the island", err == "GL_NO_ERROR", err)
            img = shot("island_aerial")
            W, Hh = img.get_size()
            px = [img.get_at((int(W * u), int(Hh * v)))[:3] for u in (0.45, 0.5, 0.55) for v in (0.55, 0.62, 0.7)]
            green = sum(1 for r, g, b in px if g > b and g > r * 0.9)
            check("island is visible (green land in the middle of the view)", green >= 5, px[:3])
            lods = {ch["lod"] for ch in rt.chunks.values()}
            check("vegetation chunks built with several LODs", len(rt.chunks) > 50 and len(lods) >= 2,
                  f"{len(rt.chunks)} chunks, LODs {sorted(lods)}")
            state["key"] = rt.key
            node.blueprints[-1].properties["topology"] = "volcano"
        if f == 8:
            state["t0"] = time.time()
        if f > 8 and state.get("key") and rt.key != state["key"]:
            check("changing a property rebuilds the island (live, debounced)", True, rt.isl.params["topology"])
            state["key"] = None
            sx, sy, sz = rt.isl.spawn
            mod.place_spawn(eng, rt)
            eng.toggle_play()
            state["play_f"] = f
        if f > 8 and state.get("key") and time.time() - state["t0"] > 25:
            check("changing a property rebuilds the island (live, debounced)", False)
            state["key"] = None
        pf = state.get("play_f")
        if pf and f == pf + 60:
            pl = mod._player(eng)
            if pl is not None:
                p = pl.world_position()
                gh = rt.height_at(p.x, p.z)
                check("Play: the player stands on the island terrain", abs(p.y - gh) < 3.0 and p.y > gh - 0.5,
                      f"player y {p.y:.2f}, ground {gh:.2f}")
            else:
                check("Play: the player stands on the island terrain", False, "no player")
            shot("island_play")
            eng.toggle_play()
        if pf and f == pf + 64:
            eng.save_project(os.path.join(project, "main.heaven3d"))
            txt = open(os.path.join(project, "main.heaven3d")).read()
            check("saved as settings only (no generated meshes in the project)",
                  "TropicalIsland" in txt and "terrain 0,0" not in txt and len(txt) < 400_000, f"{len(txt) // 1024} KB")
            cache = os.path.join(project, ".island_cache")
            check("island cached in the project folder", os.path.isdir(cache) and any(f.endswith(".npz") for f in os.listdir(cache)))
            json.dump({k: len(v) for k, v in rt.isl.instances.items()}, open(os.path.join(project, "counts.json"), "w"))
            print(f"\n{sum(res)}/{len(res)} passed (editor)")
            os._exit(0 if all(res) else 1)
        _flip()
    pygame.display.flip = frame
    eng.run()


def phase_reload(project, mode):
    H, eng = boot(project, mode)
    import pygame
    mod = sys.modules["h3dpkg_tropical_islands"]
    state = {"f": 0}
    _flip = pygame.display.flip

    def frame():
        f = state["f"] = state["f"] + 1
        if f == 3:
            isl = mod.islands(eng)
            ok = len(isl) == 1
            rt = mod.runtime_of(isl[0][0]) if ok else None
            counts = json.load(open(os.path.join(project, "counts.json")))
            same = rt is not None and rt.isl is not None and {k: len(v) for k, v in rt.isl.instances.items()} == counts
            check(f"{mode}: island rebuilt identically from the project", same)
            check(f"{mode}: generated nodes not duplicated",
                  ok and sum(1 for c in isl[0][0].children if getattr(c, "_heaven3d_imported_part", None) == "island")
                  == len(rt.terrain_nodes) + sum(len(ch["nodes"]) for ch in rt.chunks.values()) + len(rt.col_pool))
        if f == 12 and mode == "runtime":
            # exported games build the island after Play started: it must still collide
            n = mod.islands(eng)[0][0]
            rt = mod.runtime_of(n)
            ents = [e for e in eng.physics.entries if e.node is n]
            check("runtime: island ground in the physics world (once)",
                  len(ents) == 1 and ents[0].collider is n.collider, len(ents))
            pl = mod._player(eng)
            if pl is not None:
                p = pl.world_position()
                check("runtime: player stands on the island (does not fall through)",
                      abs(p.y - rt.height_at(p.x, p.z)) < 2.5, f"y {p.y:.2f}")
            caps = [c for c in rt.col_pool if c.visible]
            check("runtime: nearby trunks collide", all(eng.physics.entry_for(c) is not None for c in caps), len(caps))
        if f == 12:
            check(f"{mode}: no GL errors", eng.ctx.error == "GL_NO_ERROR", eng.ctx.error)
            shot(f"reload_{mode}")
            print(f"\n{sum(res)}/{len(res)} passed ({mode})")
            os._exit(0 if all(res) else 1)
        _flip()
    pygame.display.flip = frame
    eng.run()


if __name__ == "__main__":
    if len(sys.argv) > 2:
        project, phase = sys.argv[1], sys.argv[2]
        if phase == "editor":
            phase_editor(project)
        else:
            phase_reload(project, "editor" if phase == "reload" else "runtime")
        sys.exit(0)
    phase_generator()
    gen_ok = all(res)
    print(f"\n{sum(res)}/{len(res)} passed (generator)")
    proj = tempfile.mkdtemp(prefix="h3disl_")
    shutil.copytree(os.path.join(ROOT, "templates", "empty"), proj, dirs_exist_ok=True)
    os.makedirs(os.path.join(proj, "packages"))
    shutil.copytree(PKG, os.path.join(proj, "packages", "tropical_islands"),
                    ignore=shutil.ignore_patterns("__pycache__", "tools"))
    failed = 0 if gen_ok else 1
    os.environ["H3D_SHOTS"] = OUT
    for phase in ("editor", "reload", "runtime"):
        failed += subprocess.run([sys.executable, __file__, proj, phase]).returncode != 0
    print("\nSCREENSHOTS", OUT)
    print("ALL PASSED" if failed == 0 else f"{failed} phase(s) failed")
    sys.exit(1 if failed else 0)

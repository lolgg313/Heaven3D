#!/usr/bin/env python3
"""
Heaven3D importer test suite — 8 hard .blend, 8 hard .gltf, 8 hard .glb files
(+ extras).  Runs WITHOUT a window or GPU: it loads the engine module and
calls the importers directly, then compares every result with
expected.json (triangle count, material parts, lights, world bounding box).

    python tests/importers/run_importer_tests.py            # all files
    python tests/importers/run_importer_tests.py --render   # + screenshots
                                     (needs a GPU / desktop; writes output/)
    python tests/importers/run_importer_tests.py --update   # rewrite expected

How the expected values were verified is recorded per file in expected.json
("verified"): Blender 5.0.1 / 4.2 evaluated scenes, Blender's glTF importer,
the Khronos reference screenshots, a bind-pose check for skinned models and
byte-exact comparison with the meshoptimizer C library.
"""
import argparse
import contextlib
import importlib.util
import io
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ASSETS = os.path.join(HERE, "assets")
EXPECTED = os.path.join(HERE, "expected.json")


def load_engine(path):
    spec = importlib.util.spec_from_file_location("Heaven3D", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = mod
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


def test_files():
    out = []
    for sub, exts in (("blend", (".blend",)), ("gltf", (".gltf",)), ("glb", (".glb",)),
                      ("extra", (".glb", ".gltf"))):
        base = os.path.join(ASSETS, sub)
        for dirpath, _dirs, files in os.walk(base):
            for f in sorted(files):
                if f.lower().endswith(exts) and "_ref" not in f:
                    out.append((sub, os.path.join(dirpath, f)))
    out.sort(key=lambda x: (["blend", "gltf", "glb", "extra"].index(x[0]), x[1].lower()))
    return out


def run_one(H, path):
    t0 = time.time()
    loader = H.H3DImporters.load_blend if path.lower().endswith(".blend") else H.H3DImporters.load_gltf
    warns = []

    class L:
        @staticmethod
        def warn(m):
            warns.append(str(m))

        @staticmethod
        def info(m):
            pass
    parts, lights = loader(path, L)
    import numpy as np
    tris = sum(len(p["indices"]) // 3 for p in parts)
    lo = np.min([p["positions"].min(0) for p in parts], 0).tolist() if parts else None
    hi = np.max([p["positions"].max(0) for p in parts], 0).tolist() if parts else None
    feats = sorted({k for p in parts for k, v in (("uv0", p["uv0"]), ("uv1", p["uv1"]),
                                                    ("colors", p["colors"]), ("tangents", p["tangents"]))
                    if v is not None})
    return {"tris": tris, "parts": len(parts), "lights": len(lights),
            "bbox": [[round(v, 4) for v in lo], [round(v, 4) for v in hi]] if lo else None,
            "features": feats, "seconds": round(time.time() - t0, 3), "warnings": warns}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--engine", default=os.path.join(ROOT, "Heaven3D.py"))
    ap.add_argument("--update", action="store_true")
    ap.add_argument("--render", action="store_true")
    a = ap.parse_args()
    H = load_engine(a.engine)
    expected = json.load(open(EXPECTED, encoding="utf-8")) if os.path.isfile(EXPECTED) else {}
    files = test_files()
    print(f"Heaven3D {H.HEAVEN_VERSION} importer tests — {len(files)} files\n")
    print(f"{'set':6s} {'file':40s} {'tris':>8s} {'parts':>5s} {'lights':>6s} {'time':>7s}  result")
    fails = 0
    new_expected = {}
    for sub, path in files:
        key = os.path.relpath(path, ASSETS).replace(os.sep, "/")
        try:
            r = run_one(H, path)
        except Exception as ex:
            print(f"{sub:6s} {os.path.basename(path)[:40]:40s} {'':>8s} {'':>5s} {'':>6s} {'':>7s}  FAIL  {type(ex).__name__}: {ex}")
            fails += 1
            continue
        e = expected.get(key)
        status, why = "PASS", ""
        if e is None:
            status, why = "NEW ", "(no expected values yet)"
        else:
            if r["tris"] != e["tris"] or r["parts"] != e["parts"] or r["lights"] != e["lights"]:
                status, why = "FAIL", f"expected tris/parts/lights {e['tris']}/{e['parts']}/{e['lights']}"
            elif e.get("bbox") and r["bbox"]:
                err = max(abs(x - y) for a_, b_ in zip(r["bbox"], e["bbox"]) for x, y in zip(a_, b_))
                if err > 1e-3 * max(1.0, max(abs(v) for c in e["bbox"] for v in c)):
                    status, why = "FAIL", f"bbox differs by {err:.4f}"
        if status == "FAIL":
            fails += 1
        print(f"{sub:6s} {os.path.basename(path)[:40]:40s} {r['tris']:8d} {r['parts']:5d} {r['lights']:6d} "
              f"{r['seconds']:6.2f}s  {status} {why}")
        new_expected[key] = {k: r[k] for k in ("tris", "parts", "lights", "bbox", "features")}
        if e and "verified" in e:
            new_expected[key]["verified"] = e["verified"]
    print(f"\n{len(files) - fails}/{len(files)} passed")
    if a.update:
        json.dump(new_expected, open(EXPECTED, "w", encoding="utf-8"), indent=1, ensure_ascii=False)
        print("expected.json updated")
    if a.render:
        render(a.engine, [p for _s, p in files])
    return 1 if fails else 0


def render(engine_path, files):
    """Import every file into a live editor and save a screenshot of each."""
    import math
    out_dir = os.path.join(HERE, "output")
    os.makedirs(out_dir, exist_ok=True)
    H = sys.modules["Heaven3D"]
    import pygame
    import moderngl
    import glm
    eng = H.HeavenEngine(mode="editor", width=1280, height=800)
    for n in eng.scene.root.traverse():
        if n.is_ocean:
            n.ocean_settings.enabled = False
    state = {"i": -1, "f": 0, "node": None}

    def nxt():
        if state["node"] is not None:
            eng._delete_node(state["node"])
        state["i"] += 1
        state["f"] = 0
        if state["i"] >= len(files):
            print(f"screenshots written to {out_dir}")
            pygame.quit()
            os._exit(0)
        node = eng.load_model(files[state["i"]])
        state["node"] = node
        if node is None:
            return
        eng.deselect()
        lo, hi = eng._spawn_mesh_local_aabb(node)
        c = (lo + hi) * 0.5
        size = float(glm.length(hi - lo))
        d = max(size, 1e-3) * 1.15 / (2 * math.tan(math.radians(eng.camera.fov) / 2))
        yaw, pitch = 25.0, -20.0
        cp = math.cos(math.radians(pitch))
        fwd = glm.vec3(-math.sin(math.radians(yaw)) * cp, math.sin(math.radians(pitch)),
                       -math.cos(math.radians(yaw)) * cp)
        eng.camera.position = c - fwd * d
        eng.camera.yaw, eng.camera.pitch = yaw, pitch
        eng.camera.near = max(d / 500.0, 0.001)
    _flip = pygame.display.flip

    def flip():
        if state["i"] < 0:
            nxt()
        state["f"] += 1
        if state["f"] == 6:
            w, h = pygame.display.get_window_size()
            data = moderngl.get_context().screen.read(components=3)
            img = pygame.transform.flip(pygame.image.frombuffer(data, (w, h), "RGB"), False, True)
            name = os.path.splitext(os.path.basename(files[state["i"]]))[0]
            pygame.image.save(img, os.path.join(out_dir, name + ".png"))
            nxt()
        _flip()
    pygame.display.flip = flip
    eng._build_ui = lambda: None
    eng.run()


if __name__ == "__main__":
    sys.exit(main())

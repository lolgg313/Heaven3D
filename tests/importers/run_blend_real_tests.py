#!/usr/bin/env python3
"""
Real-world .blend files vs Blender itself (no window or GPU needed):

    python tests/importers/run_blend_real_tests.py

assets/blend_real holds the same scenes saved by Blender 5.0 and 4.2 with the
'Compress' option (zstd, read by Heaven3D's own decoder).  truth_<tag>.json is
Blender's evaluated result (tools/gen_blend_real.py made both).  For every
object - found by its place in the scene - the import must give Blender's
triangle count, every sampled corner position and the same normal there:

  r1  curves & surfaces: bevel, extrude, caps, radius, 2D fills, NURBS
  r2  Simple Deform / Displace / Smooth modifiers
  r3  custom split normals (incl. Weighted Normal), shape keys
  r4  materials (Mix Shader, Diffuse, Emission, metal, RGB node, object-linked
      slot, external texture) and hidden objects / collections (not imported)
"""
import contextlib
import importlib.util
import io
import json
import math
import os
import sys
import time

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
ASSETS = os.path.join(HERE, "assets", "blend_real")

# what each r4 object's material must come out as (engine colour space)
MATERIALS = {
    "MixRedBlue": lambda i: abs(i["base_color"][0] - i["base_color"][2]) < 0.02 and i["base_color"][0] > 0.6
    and i["base_color"][1] < 0.05,
    "Glow": lambda i: i["emissive"][1] > 0.9 and i["emissive_intensity"] > 2.5,
    "Matte": lambda i: i["roughness"] > 0.99 and i["metallic"] < 0.01 and i["base_color"][2] < 0.05,
    "Gold": lambda i: i["metallic"] > 0.99 and abs(i["roughness"] - 0.2) < 0.01,
    "CyanViaRGB": lambda i: i["base_color"][0] < 0.05 and min(i["base_color"][1:3]) > 0.95,
    "ObjLinked": lambda i: i["base_color"][1] > 0.95 and i["base_color"][0] < 0.05,
    "Bricks": lambda i: "base_color" in i["_h3d"]["slots"]
    and os.path.basename(str(i["_h3d"]["slots"]["base_color"]["desc"][1])) == "brick.png"
    and i["_h3d"]["slots"]["base_color"].get("xf") is not None,
}


def load_engine():
    spec = importlib.util.spec_from_file_location("Heaven3D", os.path.join(ROOT, "Heaven3D.py"))
    mod = importlib.util.module_from_spec(spec)
    sys.modules["Heaven3D"] = mod
    with contextlib.redirect_stdout(io.StringIO()):
        spec.loader.exec_module(mod)
    return mod


def main():
    H = load_engine()
    results = []
    for tag in ("v50", "v42"):
        T = json.load(open(os.path.join(ASSETS, "truth_%s.json" % tag)))
        for fname in sorted(T):
            warns = []

            class L:
                warn = staticmethod(lambda m: warns.append(str(m)))
                info = staticmethod(lambda m: None)
            t0 = time.time()
            parts, _ = H.H3DImporters.load_blend(os.path.join(ASSETS, fname), L)
            dt = time.time() - t0
            # engine Y-up -> Blender Z-up
            P, N, tri_c, owner = [], [], [], []
            for k, p in enumerate(parts):
                pp = p["positions"].astype(float)
                nn = p["normals"].astype(float)
                P.append(np.c_[pp[:, 0], -pp[:, 2], pp[:, 1]])
                N.append(np.c_[nn[:, 0], -nn[:, 2], nn[:, 1]])
                tri = p["indices"].reshape(-1, 3)
                tri_c.append(P[-1][tri].mean(1))
                owner.append(np.full(len(pp), k))
            P, N = np.concatenate(P), np.concatenate(N)
            tri_c = np.concatenate(tri_c)
            owner = np.concatenate(owner)
            for ob in T[fname]:
                name = "%s / %s" % (fname, ob["name"])
                if ob["bbox"] is None:
                    continue
                lo = np.asarray(ob["bbox"][0]) - 0.3
                hi = np.asarray(ob["bbox"][1]) + 0.3
                inside_v = np.all((P >= lo) & (P <= hi), 1)
                if ob["hidden"]:
                    results.append((name, not inside_v.any(), "hidden in Blender -> not imported"))
                    continue
                ntri = int(np.all((tri_c >= lo) & (tri_c <= hi), 1).sum())
                Pi, Ni = P[inside_v], N[inside_v]
                worst_p, worst_a = 0.0, 0.0
                tol = ob["tol"]
                for c in ob["corners"]:
                    p, n = np.asarray(c[:3]), np.asarray(c[3:])
                    d = np.abs(Pi - p).max(1)
                    near = np.where(d <= max(tol, 2e-3))[0]
                    if not len(near):
                        worst_p = max(worst_p, float(d.min()) if len(d) else 1e9)
                        continue
                    a = math.degrees(math.acos(float(np.clip((Ni[near] @ n).max(), -1, 1))))
                    worst_a = max(worst_a, a)
                max_ang = 1.0 if tol <= 0.002 else 8.0
                ok = ntri == ob["tris"] and worst_p == 0.0 and worst_a <= max_ang
                detail = "tris %d/%d  pos miss %.4f  normal err %.2f deg" % (ntri, ob["tris"], worst_p, worst_a)
                if ob["name"] in MATERIALS:
                    ks = np.unique(owner[inside_v])
                    mi = parts[int(ks[0])]["material_info"] if len(ks) == 1 else None
                    mok = mi is not None and MATERIALS[ob["name"]](mi)
                    ok = ok and mok
                    detail += "  material %s" % ("ok" if mok else "WRONG (%s)" % (mi and mi.get("name")))
                results.append((name, ok, detail))
            results.append((fname + " (load time)", dt < 20, "%.2fs, warnings: %s" % (dt, warns[:2])))
    bad = 0
    for name, ok, detail in results:
        bad += not ok
        print("%s  %-36s %s" % ("PASS" if ok else "FAIL", name, detail))
    print("\n%d/%d passed" % (len(results) - bad, len(results)))
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

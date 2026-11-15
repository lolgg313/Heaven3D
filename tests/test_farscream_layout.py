"""FarScream map layout and model files (no GPU):  python tests/test_farscream_layout.py

The layout is pure numpy: from an island and a seed it places the camps, the
radio outpost, villages, the plane crash, the shipwreck, road wrecks, loot and
the characters.  This checks it is complete, deterministic, on land and not
piled up, that the sites get flattened and cleared of trees, and that the
optimized model files are valid and consistent with their manifest."""
import importlib.util
import json
import math
import os
import struct
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
PKG = os.path.join(ROOT, "packages", "farscream")
res = []


def check(n, c, d=""):
    res.append(bool(c))
    print(("PASS " if c else "FAIL ") + n + (f"  [{d}]" if d else ""), flush=True)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


G = load("island_gen", os.path.join(ROOT, "packages", "tropical_islands", "island_gen.py"))
L = load("fs_layout", os.path.join(PKG, "fs_layout.py"))

BIG = {"Bunker_A", "Bunker_B", "Bunker_C", "Tent", "soviet_radio_station", "Li2", "M3S", "pickup_healthy",
       "M3S_Wreck", "pickup_wreck"}


def layout_checks(seed, topo):
    params = {"seed": seed, "size": 1000.0, "topology": topo, "roads": 6, "jungle": 0.72,
              "coast_roughness": 0.55, "erosion": 0.5}
    isl = G.generate(params)
    lay = L.make_layout(isl, seed)
    again = L.make_layout(G.generate(params), seed)
    check("%s/%d: same seed, same map" % (topo, seed), json.dumps(lay, sort_keys=True) == json.dumps(again, sort_keys=True))
    kinds = [s["kind"] for s in lay["sites"]]
    check("%s/%d: 3 camps, radio, 2 villages, crash, ship" % (topo, seed),
          kinds.count("camp") == 3 and kinds.count("radio") == 1 and kinds.count("village") == 2
          and kinds.count("crash") == 1 and kinds.count("ship") == 1, kinds)
    soldiers = [p for p in lay["posts"] if p["kind"] == "soldier"]
    natives = [p for p in lay["posts"] if p["kind"] == "native"]
    check("%s/%d: soldiers carry pistols and MP5s only, islanders nothing" % (topo, seed),
          soldiers and natives and all(p["weapon"] in ("pistol", "MP5") for p in soldiers)
          and all(p["weapon"] is None for p in natives), "%d soldiers, %d islanders" % (len(soldiers), len(natives)))
    check("%s/%d: both trucks are there" % (topo, seed),
          sum(1 for it in lay["items"] if it["model"] in ("M3S", "pickup_healthy") and it.get("role") == "vehicle") == 2)
    L.apply_pads(isl, lay)
    g = L._Grid(isl)
    wet = [it["model"] for it in lay["items"] if it["model"] != "CargoShip" and g.height(it["x"], it["z"]) < -0.3]
    check("%s/%d: every prop stands on land (the ship in the shallows)" % (topo, seed), not wet, wet[:5])
    bumpy = []
    for s in lay["sites"]:
        if s["kind"] in ("camp", "radio", "village"):
            hs = [g.height(s["x"] + math.cos(a) * s["r"] * 0.7, s["z"] + math.sin(a) * s["r"] * 0.7)
                  for a in np.linspace(0, 2 * math.pi, 16, endpoint=False)]
            if max(hs) - min(hs) > 1.2:
                bumpy.append((s["name"], round(max(hs) - min(hs), 2)))
    check("%s/%d: camps, outpost and villages stand on flat ground" % (topo, seed), not bumpy, bumpy)
    big = [it for it in lay["items"] if it["model"] in BIG]
    close = [(a["model"], b["model"]) for i, a in enumerate(big) for b in big[i + 1:]
             if math.hypot(a["x"] - b["x"], a["z"] - b["z"]) < 2.5]
    check("%s/%d: no two buildings / vehicles on top of each other" % (topo, seed), not close, close[:3])
    trees = np.concatenate([isl.instances[k][:, [0, 2]] for k in L.TREES if len(isl.instances.get(k, []))])
    blocked = []
    for s in lay["sites"]:
        if s["kind"] in ("camp", "radio", "village", "crash"):
            d = np.hypot(trees[:, 0] - s["x"], trees[:, 1] - s["z"])
            if (d < s["r"] * 0.8).any():
                blocked.append(s["name"])
    check("%s/%d: no trees inside the sites" % (topo, seed), not blocked, blocked)


def glb_json(path):
    b = open(path, "rb").read()
    if b[:4] != b"glTF":
        return None
    jl = struct.unpack("<I", b[12:16])[0]
    return json.loads(b[20:20 + jl])


def model_checks():
    d = os.path.join(PKG, "assets", "models")
    man = json.load(open(os.path.join(d, "manifest.json")))
    check("19 models in the manifest", len(man) == 19, len(man))
    bad, lod_tex = [], []
    for name, info in man.items():
        j = glb_json(os.path.join(d, name + ".glb"))
        if j is None or not j.get("images"):
            bad.append(name)
        prev = info["tris"]
        for lod in info["lod"]:
            jl = glb_json(os.path.join(d, lod["file"]))
            if jl is None or lod["tris"] >= prev or lod["from_m"] <= 0:
                bad.append(lod["file"])
            elif jl.get("images"):
                lod_tex.append(lod["file"])
            prev = lod["tris"]
    check("every model and level of detail is a valid .glb, lighter than the one before", not bad, bad)
    check("levels of detail carry no texture copies", not lod_tex, lod_tex)
    check("the drivable trucks keep full detail", not man["M3S"]["lod"] and not man["pickup_healthy"]["lod"])


def main():
    for seed, topo in ((4242, "hills"), (37, "mountains"), (73, "volcano")):
        layout_checks(seed, topo)
    model_checks()
    print("\n%d/%d passed" % (sum(res), len(res)), flush=True)
    sys.exit(0 if all(res) else 1)


if __name__ == "__main__":
    main()

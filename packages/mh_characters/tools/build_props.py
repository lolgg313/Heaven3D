"""Packs the hand-held pistols (assets/props/guns.npz).

Source: Quaternius "Ultimate Gun Pack" (July 2019), CC0 1.0,
https://opengameart.org/content/low-poly-guns-pack  (ultimate_gun_pack_by_quaternius.zip, OBJ folder)

    python tools/build_props.py "<pack>/OBJ" assets/props/guns.npz

Every gun is stored in the grip frame the animations use: origin in the palm at
the top of the grip, barrel along -Z, +Y up, metres, plus its muzzle point.
Flat-coloured materials are mapped to PBR colours (the pack's Kd values are
near-black placeholders).
"""
import json
import os
import sys

import numpy as np

GUNS = ["Pistol_1", "Pistol_2", "Pistol_3", "Pistol_4", "Pistol_5", "Pistol_6",
        "Revolver_1", "Revolver_2", "Revolver_3", "Revolver_5"]
LENGTH = {"Pistol": 0.195, "Revolver": 0.235}            # real-world overall length (m)
# material -> (sRGB colour, metallic, roughness)
MATS = {"Black": ((0.07, 0.07, 0.075), 0.2, 0.55), "Black2": ((0.045, 0.045, 0.05), 0.2, 0.6),
        "DarkMetal": ((0.20, 0.20, 0.21), 0.85, 0.4), "Metal": ((0.38, 0.38, 0.39), 0.9, 0.35),
        "LightMetal": ((0.62, 0.62, 0.63), 0.9, 0.3), "Wood": ((0.45, 0.29, 0.18), 0.0, 0.6),
        "DarkWood": ((0.29, 0.18, 0.12), 0.0, 0.6)}


def load_obj(path):
    V, VN, groups, cur = [], [], {}, None
    for line in open(path, encoding="utf-8", errors="replace"):
        t = line.split()
        if not t:
            continue
        if t[0] == "v":
            V.append([float(x) for x in t[1:4]])
        elif t[0] == "vn":
            VN.append([float(x) for x in t[1:4]])
        elif t[0] == "usemtl":
            cur = t[1]
            groups.setdefault(cur, [])
        elif t[0] == "f":
            idx = []
            for c in t[1:]:
                p = c.split("/")
                idx.append((int(p[0]) - 1, int(p[2]) - 1 if len(p) > 2 and p[2] else -1))
            for k in range(1, len(idx) - 1):                 # fan triangulation
                groups.setdefault(cur, []).append((idx[0], idx[k], idx[k + 1]))
    return np.array(V), np.array(VN) if VN else None, groups


def pack_gun(path, name):
    V, VN, groups = load_obj(path)
    lo, hi = V.min(0), V.max(0)
    length = hi[0] - lo[0]
    s = LENGTH["Revolver" if name.startswith("Revolver") else "Pistol"] / length
    # front = the end away from the grip (grip = the low part of the gun)
    low = V[V[:, 1] < lo[1] + 0.25 * (hi[1] - lo[1])]
    gx = float(np.median(low[:, 0]))
    front = 1.0 if (hi[0] - gx) > (gx - lo[0]) else -1.0
    # top of the grip: lowest point of the frame just in front of the grip
    tip = V[(V[:, 0] - gx) * front > 0.55 * length * 0.9]
    slide_bottom = float(tip[:, 1].min()) if len(tip) else lo[1] + 0.6 * (hi[1] - lo[1])
    muzzle_end = hi[0] if front > 0 else lo[0]
    mz = V[np.abs(V[:, 0] - muzzle_end) < 0.04 * length]
    muzzle_y = float((mz[:, 1].min() + mz[:, 1].max()) / 2) if len(mz) else slide_bottom
    origin = np.array([gx, slide_bottom, 0.0])

    def to_frame(p):                                     # obj (front=±X, up=Y, side=Z) -> grip frame
        q = (p - origin) * s
        return np.stack([q[..., 2] * front, q[..., 1], -q[..., 0] * front], -1)

    def nrm(n):
        return np.stack([n[..., 2] * front, n[..., 1], -n[..., 0] * front], -1)
    parts = []
    for mat, tris in groups.items():
        if not tris:
            continue
        T = np.array(tris)                                # (F, 3, 2)
        P = to_frame(V[T[:, :, 0].reshape(-1)])
        if VN is not None and (T[:, :, 1] >= 0).all():
            N = nrm(VN[T[:, :, 1].reshape(-1)])
        else:
            a, b, c = P[0::3], P[1::3], P[2::3]
            fn = np.cross(b - a, c - a)
            fn /= np.maximum(np.linalg.norm(fn, axis=1, keepdims=True), 1e-12)
            N = np.repeat(fn, 3, axis=0)
        col, met, rough = MATS.get(mat, ((0.3, 0.3, 0.3), 0.5, 0.5))
        parts.append({"mat": mat, "color": list(col), "metallic": met, "roughness": rough,
                      "P": P.astype(np.float32), "N": N.astype(np.float32)})
    muzzle = to_frame(np.array([muzzle_end, muzzle_y, 0.0]))
    return parts, muzzle


def main(src, out):
    arrays, index = {}, {"source": "Quaternius Ultimate Gun Pack (July 2019), CC0 1.0", "guns": {}}
    for g in GUNS:
        parts, muzzle = pack_gun(os.path.join(src, g + ".obj"), g)
        meta = []
        for i, p in enumerate(parts):
            arrays[f"{g}.{i}.P"] = p["P"]
            arrays[f"{g}.{i}.N"] = p["N"]
            meta.append({"mat": p["mat"], "color": p["color"], "metallic": p["metallic"],
                         "roughness": p["roughness"]})
        index["guns"][g] = {"parts": meta, "muzzle": [float(x) for x in muzzle]}
        allp = np.concatenate([p["P"] for p in parts])
        print(g, "size", (allp.max(0) - allp.min(0)).round(3), "muzzle", np.round(muzzle, 3))
    arrays["index.json"] = np.frombuffer(json.dumps(index).encode("utf-8"), dtype=np.uint8)
    os.makedirs(os.path.dirname(out), exist_ok=True)
    np.savez_compressed(out, **arrays)
    print(f"{len(GUNS)} guns -> {out} ({os.path.getsize(out) / 1e3:.0f} kB)")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

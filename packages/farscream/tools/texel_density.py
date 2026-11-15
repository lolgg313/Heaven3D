"""Shrink textures that have more texels than a screen can ever show.

    python texel_density.py <models_dir>            (rewrites the LOD0 .glb files in place)
    python texel_density.py <models_dir> --report   (only prints what it would do)

A texel can only be seen if it covers at least a pixel.  The finest a
surface is ever shown is from the closest the camera gets to it (CLOSEST,
the player's eye against a wall) on the sharpest screen we aim at (SCREEN_H
pixels high, FOV degrees vertical field of view):

    texels per metre a screen can show = 1 / (CLOSEST * 2 tan(FOV / 2) / SCREEN_H)

For every texture we measure how many texels per metre the model really
gives it (the 95th percentile over its triangles, so stretched corners
don't count against it) and, where that is more than any screen can show,
scale the image down to the next power of two that still has at least as
many texels per metre as the screen can show.  On screen nothing changes:
the GPU was already sampling a smaller mip level for every one of those
pixels.  It saves memory, loading time and bandwidth (4K images that only
ever showed their 2K mip level).
"""
import io
import json
import math
import os
import struct
import sys

import numpy as np
from PIL import Image

CLOSEST = 0.40          # metres: eye against a wall / prop
SCREEN_H = 1440         # pixels (2560 x 1440)
FOV = 70.0              # degrees, vertical
SHOWABLE = 1.0 / (CLOSEST * 2.0 * math.tan(math.radians(FOV / 2.0)) / SCREEN_H)   # texels per metre
MIN_SIZE = 256

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def read_glb(path):
    b = open(path, "rb").read()
    jl = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jl])
    off = 20 + jl
    bl = struct.unpack("<I", b[off:off + 4])[0]
    return j, b[off + 8:off + 8 + bl]


def write_glb(path, j, binb):
    js = json.dumps(j, separators=(",", ":")).encode()
    js += b" " * ((4 - len(js) % 4) % 4)
    binb += b"\0" * ((4 - len(binb) % 4) % 4)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, 12 + 8 + len(js) + 8 + len(binb)))
        fh.write(struct.pack("<I4s", len(js), b"JSON") + js)
        fh.write(struct.pack("<I4s", len(binb), b"BIN\0") + binb)


def accessor(j, binb, i):
    a = j["accessors"][i]
    bv = j["bufferViews"][a["bufferView"]]
    dt = np.dtype(CT[a["componentType"]])
    c = NC[a["type"]]
    n = a["count"]
    stride = bv.get("byteStride") or dt.itemsize * c
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    raw = np.frombuffer(binb, np.uint8, count=stride * (n - 1) + dt.itemsize * c, offset=start)
    out = np.lib.stride_tricks.as_strided(raw, shape=(n, dt.itemsize * c), strides=(stride, 1))
    out = np.ascontiguousarray(out).view(dt).reshape(n, c).astype(np.float64)
    if a.get("normalized"):
        out /= float(np.iinfo(dt).max)
    return out


def _node_matrix(nd):
    if "matrix" in nd:
        return np.array(nd["matrix"], np.float64).reshape(4, 4).T
    t = np.array(nd.get("translation", [0, 0, 0]), np.float64)
    x, y, z, w = nd.get("rotation", [0, 0, 0, 1])
    s = np.array(nd.get("scale", [1, 1, 1]), np.float64)
    R = np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                  [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                  [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])
    M = np.eye(4)
    M[:3, :3] = R * s
    M[:3, 3] = t
    return M


def mesh_instances(j):
    """[(mesh index, world matrix)] for every node that draws a mesh."""
    out = []
    roots = j["scenes"][j.get("scene", 0)]["nodes"] if j.get("scenes") else range(len(j["nodes"]))

    def walk(i, P):
        nd = j["nodes"][i]
        M = P @ _node_matrix(nd)
        if "mesh" in nd:
            out.append((nd["mesh"], M))
        for c in nd.get("children", []):
            walk(c, M)
    for r in roots:
        walk(r, np.eye(4))
    return out


def densities(j, binb):
    """image index -> per-triangle texel densities in 'UV units per metre' (multiply by the size)."""
    tex_of_mat = {}
    for mi, mat in enumerate(j.get("materials", [])):
        refs = []
        pbr = mat.get("pbrMetallicRoughness", {})
        for k in ("baseColorTexture", "metallicRoughnessTexture"):
            if k in pbr:
                refs.append(pbr[k])
        for k in ("normalTexture", "occlusionTexture", "emissiveTexture"):
            if k in mat:
                refs.append(mat[k])
        tex_of_mat[mi] = refs
    per_image = {}
    for mesh_i, M in mesh_instances(j):
        for pr in j["meshes"][mesh_i]["primitives"]:
            refs = tex_of_mat.get(pr.get("material", -1), [])
            if not refs or pr.get("mode", 4) != 4:
                continue
            P = accessor(j, binb, pr["attributes"]["POSITION"])
            P = P @ M[:3, :3].T + M[:3, 3]
            I = accessor(j, binb, pr["indices"]).astype(np.int64).reshape(-1, 3) if "indices" in pr \
                else np.arange(len(P)).reshape(-1, 3)
            for ref in refs:
                uv_key = "TEXCOORD_%d" % ref.get("texCoord", 0)
                if uv_key not in pr["attributes"]:
                    continue
                UV = accessor(j, binb, pr["attributes"][uv_key])
                scale = ref.get("extensions", {}).get("KHR_texture_transform", {}).get("scale", [1, 1])
                UV = UV * np.array(scale, np.float64)
                a, b, c = P[I[:, 0]], P[I[:, 1]], P[I[:, 2]]
                wa = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
                ua, ub, uc = UV[I[:, 0]], UV[I[:, 1]], UV[I[:, 2]]
                e1, e2 = ub - ua, uc - ua
                ta = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
                ok = (wa > 1e-10) & (ta > 1e-14)
                d = np.sqrt(ta[ok] / wa[ok])           # uv units per metre
                img = j["textures"][ref["index"]].get("source")
                if img is None:
                    continue
                per_image.setdefault(img, []).append((d, wa[ok]))
    return per_image


def p95(pairs):
    d = np.concatenate([p[0] for p in pairs])
    w = np.concatenate([p[1] for p in pairs])
    order = np.argsort(d)
    cw = np.cumsum(w[order])
    return float(d[order][np.searchsorted(cw, 0.95 * cw[-1])])


def process(path, report=False):
    j, binb = read_glb(path)
    dens = densities(j, binb)
    views = j["bufferViews"]
    new_data = {}
    lines = []
    for img_i, im in enumerate(j.get("images", [])):
        bv = views[im["bufferView"]]
        data = binb[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        pil = Image.open(io.BytesIO(data))
        w, h = pil.size
        if img_i not in dens:
            lines.append("  image %d %dx%d: not used by any triangle, kept" % (img_i, w, h))
            continue
        uvd = p95(dens[img_i])
        texels = uvd * max(w, h)                    # texels per metre at full size
        factor = SHOWABLE / texels
        if factor >= 1.0:
            lines.append("  image %d %dx%d: %5.0f texels/m <= %4.0f showable, kept" % (img_i, w, h, texels, SHOWABLE))
            continue
        nw = max(MIN_SIZE, 1 << math.ceil(math.log2(max(1.0, w * factor))))
        nh = max(MIN_SIZE, 1 << math.ceil(math.log2(max(1.0, h * factor))))
        nw, nh = min(nw, w), min(nh, h)
        if (nw, nh) == (w, h):
            lines.append("  image %d %dx%d: %5.0f texels/m, kept" % (img_i, w, h, texels))
            continue
        lines.append("  image %d %dx%d -> %dx%d: %5.0f texels/m, %4.0f showable" % (img_i, w, h, nw, nh, texels,
                                                                                 SHOWABLE))
        if not report:
            small = pil.convert("RGB").resize((nw, nh), Image.LANCZOS)
            buf = io.BytesIO()
            small.save(buf, "JPEG", quality=95, subsampling=0, optimize=True)
            new_data[im["bufferView"]] = buf.getvalue()
    if new_data and not report:
        out = bytearray()
        for i, v in enumerate(views):
            start = v.get("byteOffset", 0)
            chunk = new_data.get(i, binb[start:start + v["byteLength"]])
            while len(out) % 4:
                out.append(0)
            v["byteOffset"] = len(out)
            v["byteLength"] = len(chunk)
            out += chunk
        j["buffers"] = [{"byteLength": len(out)}]
        write_glb(path, j, bytes(out))
    return lines


def main():
    d = sys.argv[1]
    report = "--report" in sys.argv
    print("showable: %.0f texels per metre (%.2f m from a %d px high, %.0f degree screen)" % (
        SHOWABLE, CLOSEST, SCREEN_H, FOV))
    for f in sorted(os.listdir(d)):
        if f.endswith(".glb") and "_LOD" not in f:
            print(f)
            for line in process(os.path.join(d, f), report):
                print(line)


if __name__ == "__main__":
    main()

"""Copy optimized models into the package, keeping textures only in LOD0.

The lighter levels of detail are drawn with LOD0's materials (FarScream
swaps meshes, not materials), so their own copies of the textures would only
cost disk space and loading time.  This drops images, textures and samplers
from every *_LOD<n>.glb and copies the result (and manifest.json) over.

    python strip_lod_textures.py <optimized_dir> <package_models_dir>
"""
import json
import os
import re
import shutil
import struct
import sys


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
    total = 12 + 8 + len(js) + 8 + len(binb)
    with open(path, "wb") as fh:
        fh.write(struct.pack("<4sII", b"glTF", 2, total))
        fh.write(struct.pack("<I4s", len(js), b"JSON") + js)
        fh.write(struct.pack("<I4s", len(binb), b"BIN\0") + binb)


def strip(j, binb):
    """Drop the images and the buffer views that held them; repack the buffer."""
    img_views = {im["bufferView"] for im in j.get("images", []) if "bufferView" in im}
    for k in ("images", "textures", "samplers"):
        j.pop(k, None)
    for mat in j.get("materials", []):
        pbr = mat.get("pbrMetallicRoughness", {})
        for k in ("baseColorTexture", "metallicRoughnessTexture"):
            pbr.pop(k, None)
        for k in ("normalTexture", "occlusionTexture", "emissiveTexture"):
            mat.pop(k, None)
    views = j.get("bufferViews", [])
    keep = [i for i in range(len(views)) if i not in img_views]
    remap = {old: new for new, old in enumerate(keep)}
    out = bytearray()
    new_views = []
    for i in keep:
        v = dict(views[i])
        start = v.get("byteOffset", 0)
        data = binb[start:start + v["byteLength"]]
        while len(out) % 4:
            out.append(0)
        v["byteOffset"] = len(out)
        out += data
        new_views.append(v)
    j["bufferViews"] = new_views
    for a in j.get("accessors", []):
        if "bufferView" in a:
            a["bufferView"] = remap[a["bufferView"]]
    j["buffers"] = [{"byteLength": len(out)}]
    return j, bytes(out)


KEEP_RATIO = 0.8          # a level is kept only if it has 20 % fewer triangles than the one before
NO_LODS = ("M3S", "pickup_healthy")      # drivable: always seen up close


def prune(manifest):
    """Drop levels of detail that would save too little to be worth their file."""
    for name, info in manifest.items():
        kept, prev = [], info["tris"]
        for lod in info.get("lod", []):
            if name not in NO_LODS and lod["tris"] <= prev * KEEP_RATIO:
                kept.append(lod)
                prev = lod["tris"]
        info["lod"] = kept
    return manifest


def main(src, dst):
    os.makedirs(dst, exist_ok=True)
    with open(os.path.join(src, "manifest.json")) as fh:
        manifest = prune(json.load(fh))
    wanted = {lod["file"] for info in manifest.values() for lod in info["lod"]}
    for f in sorted(os.listdir(dst)):
        if f.endswith(".glb") or f == "manifest.json":
            os.remove(os.path.join(dst, f))
    for f in sorted(os.listdir(src)):
        if not f.endswith(".glb"):
            continue
        if re.search(r"_LOD\d+\.glb$", f):
            if f not in wanted:
                continue
            j, b = read_glb(os.path.join(src, f))
            j, b = strip(j, b)
            write_glb(os.path.join(dst, f), j, b)
        else:
            shutil.copyfile(os.path.join(src, f), os.path.join(dst, f))
    with open(os.path.join(dst, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=1)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

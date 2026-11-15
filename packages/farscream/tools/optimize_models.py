"""Optimise the FarScream models without any visible change.

    python packages/farscream/tools/optimize_models.py <folder with the source .glb> packages/farscream/assets/models

For every model:
  * degenerate (zero-area) triangles go, exact duplicate vertices are welded
  * edges are collapsed only while the surface stays the same to the eye:
    the shape moves less than POS_TOL (0.1 mm), shading normals turn less
    than NRM_TOL (0.5 degrees) and texture coordinates slide less than a
    quarter texel.  Vertices on UV seams, hard edges and open borders are
    never moved, so silhouettes, seams and creases stay exactly where they are.
  * LOD1, LOD2 and LOD3 are made the same way with bigger tolerances; each is only
    shown from the distance where its error is smaller than one pixel
    (1920 x 1080, 70 degree field of view), see "lod" in manifest.json.
Textures are kept bit for bit (they are copied, not re-encoded).
"""
import json
import math
import os
import struct
import sys
import time

import numpy as np

try:
    from numba import njit
except Exception:                       # (works without numba, just slower)
    def njit(*a, **k):
        if a and callable(a[0]):
            return a[0]
        return lambda f: f

POS_TOL = 1.0e-4            # metres
NRM_TOL = 0.5               # degrees
UV_TOL_TEXELS = 0.25
# LODs: (position error in metres, normal error in degrees, uv error in texels)
LODS = ((0.003, 2.0, 1.0), (0.010, 4.0, 2.0), (0.040, 8.0, 4.0))
PIXEL_ANGLE = 2.0 * math.tan(math.radians(35.0)) / 1080.0       # radians per pixel at 1080p, 70 deg vfov

CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}


# ----------------------------------------------------------------- glb in / out
def read_glb(path):
    b = open(path, "rb").read()
    magic, _ver, _len = struct.unpack("<III", b[:12])
    if magic != 0x46546C67:
        raise ValueError("not a glb: " + path)
    jl, _jt = struct.unpack("<II", b[12:20])
    j = json.loads(b[20:20 + jl])
    off = 20 + jl
    bl, _bt = struct.unpack("<II", b[off:off + 8])
    return j, b[off + 8:off + 8 + bl]


def accessor(j, binb, i):
    a = j["accessors"][i]
    bv = j["bufferViews"][a["bufferView"]]
    dt = np.dtype(CT[a["componentType"]])
    c = NC[a["type"]]
    n = a["count"]
    stride = bv.get("byteStride") or dt.itemsize * c
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    rows = np.frombuffer(binb, np.uint8, count=stride * (n - 1) + dt.itemsize * c if n else 0, offset=start)
    out = np.empty((n, c), dt)
    for r in range(n) if False else ():
        pass
    if n:
        view = np.lib.stride_tricks.as_strided(rows, shape=(n, dt.itemsize * c), strides=(stride, 1))
        out = np.ascontiguousarray(view).view(dt).reshape(n, c)
    if a.get("normalized"):
        raise ValueError("normalized accessors are not expected in these models")
    return out


def write_glb(path, j, binb, prims_new):
    """prims_new: {(mesh index, primitive index): dict(P, N, UV or None, I)}.
    Images and everything else keep their bytes; geometry is rewritten."""
    j = json.loads(json.dumps(j))
    chunks = []
    views = []
    pos = 0

    def add_view(data, target=None):
        nonlocal pos
        pad = (-pos) % 4
        if pad:
            chunks.append(b"\0" * pad)
            pos += pad
        v = {"buffer": 0, "byteOffset": pos, "byteLength": len(data)}
        if target:
            v["target"] = target
        views.append(v)
        chunks.append(data)
        pos += len(data)
        return len(views) - 1

    # images first, their bytes untouched
    img_view = {}
    for im in j.get("images", []):
        if "bufferView" in im:
            old = j["bufferViews"][im["bufferView"]]
            data = binb[old.get("byteOffset", 0):old.get("byteOffset", 0) + old["byteLength"]]
            k = im["bufferView"]
            if k not in img_view:
                img_view[k] = add_view(data)
            im["bufferView"] = img_view[k]
    accs = []

    def add_acc(arr, typ, ctype, target, minmax=False):
        arr = np.ascontiguousarray(arr)
        a = {"bufferView": add_view(arr.tobytes(), target), "componentType": ctype, "count": int(len(arr)), "type": typ}
        if minmax:
            a["min"] = [float(x) for x in arr.min(axis=0)]
            a["max"] = [float(x) for x in arr.max(axis=0)]
        accs.append(a)
        return len(accs) - 1

    for mi, m in enumerate(j["meshes"]):
        for pi, pr in enumerate(m["primitives"]):
            g = prims_new[(mi, pi)]
            att = {}
            att["POSITION"] = add_acc(g["P"].astype(np.float32), "VEC3", 5126, 34962, True)
            att["NORMAL"] = add_acc(g["N"].astype(np.float32), "VEC3", 5126, 34962)
            if g["UV"] is not None:
                att["TEXCOORD_0"] = add_acc(g["UV"].astype(np.float32), "VEC2", 5126, 34962)
            I = g["I"]
            if len(g["P"]) < 65536:
                idx = add_acc(I.astype(np.uint16).reshape(-1, 1), "SCALAR", 5123, 34963)
            else:
                idx = add_acc(I.astype(np.uint32).reshape(-1, 1), "SCALAR", 5125, 34963)
            pr["attributes"] = att
            pr["indices"] = idx
            pr.pop("targets", None)
    j["accessors"] = accs
    j["bufferViews"] = views
    body = b"".join(chunks)
    body += b"\0" * ((-len(body)) % 4)
    j["buffers"] = [{"byteLength": len(body)}]
    js = json.dumps(j, separators=(",", ":")).encode()
    js += b" " * ((-len(js)) % 4)
    total = 12 + 8 + len(js) + 8 + len(body)
    with open(path, "wb") as f:
        f.write(struct.pack("<III", 0x46546C67, 2, total))
        f.write(struct.pack("<II", len(js), 0x4E4F534A) + js)
        f.write(struct.pack("<II", len(body), 0x004E4942) + body)


# ----------------------------------------------------------------- the collapse test (compiled)
@njit(cache=True)
def _closest_on_tri(p, a, b, c):
    """Closest point to p on triangle abc -> (u, v, w) barycentric weights."""
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = ab @ ap
    d2 = ac @ ap
    if d1 <= 0.0 and d2 <= 0.0:
        return 1.0, 0.0, 0.0
    bp = p - b
    d3 = ab @ bp
    d4 = ac @ bp
    if d3 >= 0.0 and d4 <= d3:
        return 0.0, 1.0, 0.0
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return 1.0 - v, v, 0.0
    cp = p - c
    d5 = ab @ cp
    d6 = ac @ cp
    if d6 >= 0.0 and d5 <= d6:
        return 0.0, 0.0, 1.0
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return 1.0 - w, 0.0, w
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return 0.0, 1.0 - w, w
    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    return 1.0 - v - w, v, w


@njit(cache=True)
def _fan_error(P, N, UV, has_uv, new_tris, samples_p, samples_n, samples_uv):
    """Largest (position, normal angle cos, uv) error of the sample points
    (the removed vertex and the midpoints of its old edges) against the new
    fan of triangles."""
    worst_p = 0.0
    worst_c = 1.0
    worst_uv = 0.0
    for s in range(samples_p.shape[0]):
        p = samples_p[s]
        best_d = 1e30
        bn = np.zeros(3)
        buv = np.zeros(2)
        for t in range(new_tris.shape[0]):
            a, b, c = new_tris[t, 0], new_tris[t, 1], new_tris[t, 2]
            u, v, w = _closest_on_tri(p, P[a], P[b], P[c])
            q = P[a] * u + P[b] * v + P[c] * w
            d = np.sqrt(((q - p) ** 2).sum())
            if d < best_d:
                best_d = d
                bn = N[a] * u + N[b] * v + N[c] * w
                if has_uv:
                    buv = UV[a] * u + UV[b] * v + UV[c] * w
        if best_d > worst_p:
            worst_p = best_d
        ln = np.sqrt((bn ** 2).sum())
        if ln > 1e-12:
            cs = (bn @ samples_n[s]) / (ln * max(np.sqrt((samples_n[s] ** 2).sum()), 1e-12))
        else:
            cs = -1.0
        if cs < worst_c:
            worst_c = cs
        if has_uv:
            du = np.sqrt(((buv - samples_uv[s]) ** 2).sum())
            if du > worst_uv:
                worst_uv = du
    return worst_p, worst_c, worst_uv


@njit(cache=True)
def _tri_normal(a, b, c):
    n = np.cross(b - a, c - a)
    return n


# ----------------------------------------------------------------- one primitive
def clean(P, N, UV, I):
    """Drop degenerate triangles and weld exact duplicates."""
    T = I.reshape(-1, 3).astype(np.int64)
    n = np.cross(P[T[:, 1]] - P[T[:, 0]], P[T[:, 2]] - P[T[:, 0]])
    area = np.linalg.norm(n, axis=1)
    keep = (area > 1e-12) & (T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])
    T = T[keep]
    key = np.concatenate([P, N] + ([UV] if UV is not None else []), axis=1).astype(np.float32)
    _u, first, inv = np.unique(key.view([("", key.dtype)] * key.shape[1]).ravel(), return_index=True, return_inverse=True)
    T = inv.ravel()[T]
    P, N = P[first], N[first]
    UV = UV[first] if UV is not None else None
    return P, N, UV, T, int((~keep).sum())


def simplify(P, N, UV, T, pos_tol, nrm_tol_deg, uv_tol, max_passes=12):
    """Greedy half-edge collapse within the tolerances, seam aware: a
    position is moved onto a neighbouring position together with all its
    vertex copies (one per UV island / hard-edge side), each copy onto the
    copy of the target that shares its triangles, and each copy's own normal
    and UV are checked.  So a vertex on a seam or crease can only slide along
    that seam or crease.  Open borders and corners of seams never move.
    -> T (same vertex arrays)"""
    nv = len(P)
    P64 = P.astype(np.float64)
    N64 = N.astype(np.float64)
    UV64 = UV.astype(np.float64) if UV is not None else np.zeros((nv, 2))
    has_uv = UV is not None
    cos_tol = math.cos(math.radians(nrm_tol_deg))
    tris = [list(t) for t in T.tolist()]
    alive = [True] * len(tris)
    _u, pid = np.unique(np.round(P64, 7), axis=0, return_inverse=True)
    pid = pid.ravel().tolist()
    npos = max(pid) + 1 if pid else 0
    wedges = [[] for _ in range(npos)]
    for w in range(nv):
        wedges[pid[w]].append(w)
    vt = [set() for _ in range(nv)]
    for ti, (a, b, c) in enumerate(tris):
        vt[a].add(ti); vt[b].add(ti); vt[c].add(ti)
    # open borders and non-manifold edges (by position) are locked
    locked = np.zeros(npos, bool)
    edges = {}
    for a, b, c in tris:
        for x, y in ((pid[a], pid[b]), (pid[b], pid[c]), (pid[c], pid[a])):
            k = (x, y) if x < y else (y, x)
            edges[k] = edges.get(k, 0) + 1
    for (x, y), cnt in edges.items():
        if cnt != 2:
            locked[x] = locked[y] = True

    def ptris(p):
        out = set()
        for w in wedges[p]:
            out |= vt[w]
        return out

    def pring(p):
        out = set()
        for ti in ptris(p):
            for x in tris[ti]:
                out.add(pid[x])
        out.discard(p)
        return out

    for _pass in range(max_passes):
        changed = 0
        order = np.argsort(-np.array([len(ptris(p)) for p in range(npos)])) if npos else []
        for p in list(order):
            p = int(p)
            if locked[p] or not wedges[p]:
                continue
            pts = ptris(p)
            if not pts:
                continue
            ring = pring(p)
            best = None
            for q in ring:
                shared = [ti for ti in pts if any(pid[x] == q for x in tris[ti])]
                if len(shared) != 2:
                    continue
                common = (ring & pring(q)) - {p, q}
                opp = set()
                for ti in shared:
                    opp.update(pid[x] for x in tris[ti])
                opp -= {p, q}
                if common != opp:
                    continue
                # every copy of p needs exactly one copy of q to go to
                target = {}
                ok = True
                for w in wedges[p]:
                    tw = [ti for ti in vt[w] if ti in shared]
                    xs = {x for ti in tw for x in tris[ti] if pid[x] == q}
                    if len(xs) != 1:
                        ok = False
                        break
                    target[w] = xs.pop()
                if not ok:
                    continue
                score = 0.0
                for w in wedges[p]:
                    x = target[w]
                    new = []
                    for ti in vt[w]:
                        if ti in shared:
                            continue
                        t = tris[ti]
                        nt = [x if y == w else y for y in t]
                        on = np.cross(P64[t[1]] - P64[t[0]], P64[t[2]] - P64[t[0]])
                        nn = np.cross(P64[nt[1]] - P64[nt[0]], P64[nt[2]] - P64[nt[0]])
                        lo, ln = np.linalg.norm(on), np.linalg.norm(nn)
                        if ln < 1e-14 or lo < 1e-14 or (on @ nn) / (lo * ln) < 0.5:
                            ok = False
                            break
                        new.append(nt)
                    if not ok:
                        break
                    if not new:
                        continue
                    rv = set()
                    for ti in vt[w]:
                        rv.update(tris[ti])
                    rv.discard(w)
                    rv = list(rv)
                    sp = np.vstack([P64[w]] + [(P64[w] + P64[r]) * 0.5 for r in rv])
                    sn = np.vstack([N64[w]] + [(N64[w] + N64[r]) * 0.5 for r in rv])
                    su = np.vstack([UV64[w]] + [(UV64[w] + UV64[r]) * 0.5 for r in rv])
                    ep, ec, eu = _fan_error(P64, N64, UV64, has_uv, np.array(new, np.int64), sp, sn, su)
                    if ep > pos_tol or ec < cos_tol or eu > uv_tol:
                        ok = False
                        break
                    score = max(score, ep / pos_tol + (1.0 - ec) / max(1.0 - cos_tol, 1e-12)
                                + (eu / uv_tol if has_uv else 0.0))
                if not ok:
                    continue
                if best is None or score < best[0]:
                    best = (score, q, shared, target)
            if best is None:
                continue
            _s, q, shared, target = best
            for ti in shared:
                alive[ti] = False
                for x in tris[ti]:
                    vt[x].discard(ti)
            for w in wedges[p]:
                x = target[w]
                for ti in list(vt[w]):
                    tris[ti] = [x if y == w else y for y in tris[ti]]
                    vt[x].add(ti)
                vt[w].clear()
            wedges[p] = []
            changed += 1
        if not changed:
            break
    T2 = np.array([t for t, a in zip(tris, alive) if a], np.int64).reshape(-1, 3)
    return T2


def compact(P, N, UV, T):
    used, inv = np.unique(T.ravel(), return_inverse=True)
    return P[used], N[used], (UV[used] if UV is not None else None), inv.reshape(-1).astype(np.int64)


def vertex_cache_order(T, nverts):
    """Reorder triangles for the GPU's post-transform cache (Forsyth-style,
    simplified: greedy by most recently used vertices).  Pure reordering:
    the picture is identical."""
    nt = len(T)
    if nt == 0:
        return T
    vt = [[] for _ in range(nverts)]
    for i, (a, b, c) in enumerate(T.tolist()):
        vt[a].append(i); vt[b].append(i); vt[c].append(i)
    done = np.zeros(nt, bool)
    out = []
    cache = []
    nxt = 0
    for _ in range(nt):
        best, bs = -1, -1.0
        for vtx in cache[:16]:
            for ti in vt[vtx]:
                if done[ti]:
                    continue
                s = sum(3.0 - min(cache.index(x), 3) * 0.5 if x in cache[:16] else 0.0 for x in T[ti])
                if s > bs:
                    bs, best = s, ti
        if best < 0:
            while nxt < nt and done[nxt]:
                nxt += 1
            best = nxt
        done[best] = True
        out.append(best)
        for x in T[best].tolist():
            if x in cache:
                cache.remove(x)
            cache.insert(0, x)
        del cache[32:]
    return T[np.array(out)]


def tex_size(j, binb, prim):
    """Size in texels of the base colour texture of a primitive (for the uv tolerance)."""
    try:
        mat = j["materials"][prim["material"]]
        ti = mat["pbrMetallicRoughness"]["baseColorTexture"]["index"]
        im = j["images"][j["textures"][ti]["source"]]
        bv = j["bufferViews"][im["bufferView"]]
        data = binb[bv.get("byteOffset", 0):bv.get("byteOffset", 0) + bv["byteLength"]]
        import io
        from PIL import Image
        w, h = Image.open(io.BytesIO(data)).size
        return max(w, h)
    except Exception:
        return 2048


# ----------------------------------------------------------------- one model
def optimize(src, dst_dir):
    name = os.path.splitext(os.path.basename(src))[0]
    j, binb = read_glb(src)
    info = {"tris_in": 0, "tris": 0, "lod": []}
    base = {}
    out0 = {}
    t0 = time.time()
    for mi, m in enumerate(j["meshes"]):
        for pi, pr in enumerate(m["primitives"]):
            att = pr["attributes"]
            P = accessor(j, binb, att["POSITION"]).astype(np.float64)
            N = accessor(j, binb, att["NORMAL"]).astype(np.float64)
            UV = accessor(j, binb, att["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in att else None
            I = accessor(j, binb, pr["indices"]).ravel().astype(np.int64)
            info["tris_in"] += len(I) // 3
            P, N, UV, T, _deg = clean(P, N, UV, I)
            texels = tex_size(j, binb, pr) if UV is not None else 2048
            base[(mi, pi)] = (P, N, UV, T, texels)
            T0 = simplify(P, N, UV, T, POS_TOL, NRM_TOL, UV_TOL_TEXELS / texels)
            P0, N0, UV0, I0 = compact(P, N, UV, T0)
            I0 = vertex_cache_order(I0.reshape(-1, 3), len(P0)).ravel()
            out0[(mi, pi)] = {"P": P0, "N": N0, "UV": UV0, "I": I0}
            info["tris"] += len(I0) // 3
    write_glb(os.path.join(dst_dir, name + ".glb"), j, binb, out0)
    info["seconds_lod0"] = round(time.time() - t0, 1)
    for k, (pt, nd, ut) in enumerate(LODS, 1):
        outk = {}
        tris = 0
        for key, (P, N, UV, T, texels) in base.items():
            Tk = simplify(P, N, UV, T, pt, nd, ut / texels)
            Pk, Nk, UVk, Ik = compact(P, N, UV, Tk)
            outk[key] = {"P": Pk, "N": Nk, "UV": UVk, "I": Ik}
            tris += len(Ik) // 3
        write_glb(os.path.join(dst_dir, "%s_LOD%d.glb" % (name, k)), j, binb, outk)
        # the distance from which this level's error is under one pixel
        info["lod"].append({"file": "%s_LOD%d.glb" % (name, k), "tris": tris,
                            "from_m": round(pt / PIXEL_ANGLE, 1)})
    return name, info


def main(argv):
    src_dir, dst_dir = argv[1], argv[2]
    os.makedirs(dst_dir, exist_ok=True)
    manifest = {}
    only = set(argv[3:])
    for f in sorted(os.listdir(src_dir)):
        if not f.lower().endswith(".glb") or (only and f not in only):
            continue
        t0 = time.time()
        name, info = optimize(os.path.join(src_dir, f), dst_dir)
        manifest[name] = info
        print("%-26s %7d -> %7d triangles (%4.1f%%)  %s  %.0fs" % (
            f, info["tris_in"], info["tris"], 100.0 * info["tris"] / max(info["tris_in"], 1),
            "  ".join("LOD%d %d from %.0f m" % (k + 1, l["tris"], l["from_m"]) for k, l in enumerate(info["lod"])),
            time.time() - t0), flush=True)
    mp = os.path.join(dst_dir, "manifest.json")
    old = {}
    if os.path.isfile(mp):
        with open(mp) as fh:
            old = json.load(fh)
    old.update(manifest)
    with open(mp, "w") as fh:
        json.dump(old, fh, indent=1, sort_keys=True)


if __name__ == "__main__":
    main(sys.argv)

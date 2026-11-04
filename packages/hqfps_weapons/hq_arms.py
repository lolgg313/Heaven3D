"""Close the open ends of the first-person arms.

The HQFPS arm meshes stop a little above the elbow: a ring of open edges
where the sleeve would start.  In Unity that ring sits behind the camera,
but with Heaven3D's camera some animations (the AKM's reloads, the knife's
holster, every weapon's draw) swing it into view, and you look straight
into the hollow arm.

Rather than paste a flat lid on the stump (an amputated-looking disc), the
arm is continued: the cut ring is carried on to where a real shoulder
would be -- beside, below and a little behind the eye, so off screen and
never through the camera -- and only closed there.
The new skin copies the ring's bone weights, so it moves with the upper
arm exactly like the rest of it, and the ring's texture coordinates, so it
wears the same skin.

Pure numpy, so it can be measured without a GPU (tests/test_hqfps_arms.py).
"""
import numpy as np

CUT_MIN_SIZE = 0.05       # m: smaller open loops are fingernail edges, not the cut
SEGMENTS = 3


def _weld(pos):
    key = np.round(pos * 1e4).astype(np.int64)
    _u, inv = np.unique(key, axis=0, return_inverse=True)
    return inv.reshape(-1)


def open_loops(prim):
    """Open boundary loops of a primitive, as ordered vertex-index arrays."""
    pos = prim["positions"]
    tri = prim["indices"].reshape(-1, 3).astype(np.int64)
    inv = _weld(pos)
    t = inv[tri]
    # directed edges of the welded mesh; an edge with no twin is open
    e = np.concatenate([t[:, [0, 1]], t[:, [1, 2]], t[:, [2, 0]]])
    und = np.sort(e, axis=1)
    _u, first, counts = np.unique(und, axis=0, return_index=True, return_counts=True)
    open_e = e[first[counts == 1]]
    if not len(open_e):
        return []
    rep = np.zeros(inv.max() + 1, dtype=np.int64)
    rep[inv[::-1]] = np.arange(len(inv))[::-1]          # one original vertex per welded one
    nxt = {}
    for a, b in open_e:
        nxt.setdefault(int(a), []).append(int(b))
    loops, seen = [], set()
    for start in list(nxt):
        if start in seen:
            continue
        loop, cur = [], start
        while cur not in seen and cur in nxt:
            seen.add(cur)
            loop.append(cur)
            cur = nxt[cur][0]
        if len(loop) >= 3 and cur == start:
            loops.append(rep[np.array(loop)])
    return loops


# Where a real shoulder would be, in camera space (+right, +up, +forward), m.
# The arm is continued from its cut towards this point, which is beside,
# below and a little behind the eye: off screen, and never through the eye.
SHOULDER = (0.21, -0.27, -0.10)


def _skin_points(prim, P, sel):
    v = prim["positions"][sel]
    j = prim["joints"][sel].astype(int)
    w = prim["weights"][sel]
    out = np.zeros_like(v, dtype=np.float64)
    for k in range(4):
        M = P[j[:, k]]
        out += w[:, k:k + 1] * (np.einsum("nij,nj->ni", M[:, :3, :3], v) + M[:, :3, 3])
    return out


def extend_cut(prim, palette, to_cam, from_cam, segments=SEGMENTS, min_size=CUT_MIN_SIZE,
               shoulder=SHOULDER, root=None):
    """New primitive with every arm-sized open loop continued to the
    shoulder and capped.  Returns (prim, number_of_loops_closed).

    palette   the bone palette of the reference pose (the idle the player sees most)
    to_cam    f(points rig->camera space), from_cam its inverse

    root      palette row of the arms' root (the shoulders), or None

    The continuation is built in that pose.  It starts bound to the bone
    that carries the cut and hands over to the root along its length, so the
    near end follows the upper arm and the far end stays at the shoulder,
    as a real arm does -- it can't swing through the camera on a fast
    draw or holster."""
    pos = prim["positions"].astype(np.float32)
    loops = [l for l in open_loops(prim) if np.ptp(pos[l], axis=0).max() >= min_size]
    if not loops:
        return prim, 0
    P = [pos]
    N = [prim["normals"]]
    U = [prim["uvs"]] if prim.get("uvs") is not None else None
    T = [prim["tangents"]] if prim.get("tangents") is not None else None
    J = [prim["joints"]]
    W = [prim["weights"]]
    idx = [prim["indices"].astype(np.uint32)]
    base = len(pos)
    tri_all = prim["indices"].reshape(-1, 3).astype(np.int64)
    weld = _weld(pos)
    for ring in loops:
        # Skin for the continuation: one real texel from just inside the cut.
        # Carrying each ring vertex's own UV down the new skin would smear
        # the texture into streaks.
        uv_in = None
        if U is not None:
            ring_w = set(weld[ring].tolist())
            touch = np.isin(weld[tri_all], list(ring_w)).any(axis=1)
            near = np.unique(tri_all[touch].ravel())
            near = near[~np.isin(weld[near], list(ring_w))]
            if len(near):
                uvs = prim["uvs"][near]
                med = np.median(uvs, axis=0)
                uv_in = uvs[np.argmin(np.linalg.norm(uvs - med, axis=1))]
        # the bone that carries the cut; the continuation is rigid to it
        jj = prim["joints"][ring].astype(int)
        ww = prim["weights"][ring]
        score = {}
        for a_, b_ in zip(jj.ravel(), ww.ravel()):
            score[a_] = score.get(a_, 0.0) + float(b_)
        bone = max(score, key=score.get)
        Mb = palette[bone].astype(np.float64)
        Minv = np.linalg.inv(Mb)

        ring_w = _skin_points(prim, palette, ring)             # posed, rig space
        ring_c = to_cam(ring_w)
        c = ring_c.mean(axis=0)
        side = 1.0 if c[0] >= 0.0 else -1.0
        target = np.array([shoulder[0] * side, shoulder[1], shoulder[2]])
        axis = target - c
        length = float(np.linalg.norm(axis))
        axis /= max(length, 1e-6)
        radial = ring_c - c
        radial -= np.outer(radial @ axis, axis)
        rn = radial / np.maximum(np.linalg.norm(radial, axis=1, keepdims=True), 1e-6)
        n_ring = len(ring)
        rings = [ring.astype(np.int64)]
        rings_cam = [ring_c]
        rigid_j = np.zeros((n_ring, 4), np.float32)
        rigid_j[:, 0] = bone
        rigid_w = np.zeros((n_ring, 4), np.float32)
        rigid_w[:, 0] = 1.0

        def to_bind(pts_cam, normals=False):
            pr = from_cam(pts_cam)
            if normals:
                return (Minv[:3, :3] @ pr.T).T
            h = np.c_[pr, np.ones(len(pr))]
            return (Minv @ h.T).T[:, :3]

        for k in range(1, segments + 1):
            f = k / float(segments)
            ring_k = c + radial * (1.0 + 0.15 * f) + np.outer(np.full(n_ring, length * f), axis)
            rings_cam.append(ring_k)
            if root is None:
                P.append(to_bind(ring_k).astype(np.float32))
                jk, wk = rigid_j, rigid_w
            else:
                # blend bone -> root; solve the bind position that skins to ring_k
                wr = f * f * (3.0 - 2.0 * f)                 # smooth hand-over
                M = (1.0 - wr) * Mb + wr * palette[root].astype(np.float64)
                h = np.c_[from_cam(ring_k), np.ones(n_ring)]
                P.append((np.linalg.inv(M) @ h.T).T[:, :3].astype(np.float32))
                jk = np.zeros((n_ring, 4), np.float32)
                jk[:, 0], jk[:, 1] = bone, root
                wk = np.zeros((n_ring, 4), np.float32)
                wk[:, 0], wk[:, 1] = 1.0 - wr, wr
            # normals: rotate camera-space directions back (rig flip is a mirror in x)
            nn = to_bind(rn, normals=True) - to_bind(np.zeros_like(rn), normals=True)
            nn /= np.maximum(np.linalg.norm(nn, axis=1, keepdims=True), 1e-6)
            N.append(nn.astype(np.float32))
            # a smooth tangent field round the arm; copying the ring's own
            # tangents down the new skin tilts the normal map differently
            # in every column and streaks the shading
            tc = np.cross(np.tile(axis, (n_ring, 1)), rn)
            tb = to_bind(tc, normals=True) - to_bind(np.zeros_like(tc), normals=True)
            tb /= np.maximum(np.linalg.norm(tb, axis=1, keepdims=True), 1e-6)
            tan_k = np.c_[tb, np.ones(n_ring)].astype(np.float32)
            if U is not None:
                U.append(np.tile(uv_in, (n_ring, 1)) if uv_in is not None else prim["uvs"][ring])
            if T is not None:
                T.append(tan_k)
            J.append(jk)
            W.append(wk)
            rings.append(np.arange(base, base + n_ring))
            base += n_ring
        # Side walls and the cap, wound to face outwards like the arm.  The
        # check is done on the posed (camera-space) points the rings were
        # built from: in bind space each ring sits in a different frame.
        loc = np.arange(n_ring)
        quads = []
        for k in range(segments):
            a_, b_ = loc + k * n_ring, loc + (k + 1) * n_ring
            a1, b1 = np.roll(a_, -1), np.roll(b_, -1)
            quads.append(np.stack([a_, a1, b1], 1))
            quads.append(np.stack([a_, b1, b_], 1))
        side_l = np.concatenate(quads)
        # (posed RIG space: camera space is mirrored, which would reverse winding)
        mirror = np.array([-1.0, 1.0, 1.0])
        cam = from_cam(np.concatenate(rings_cam))
        c_r, axis_r = from_cam(c[None, :])[0], axis * mirror
        tp = cam[side_l]
        fn = np.cross(tp[:, 1] - tp[:, 0], tp[:, 2] - tp[:, 0])
        mid = tp.mean(axis=1) - c_r
        outv = mid - np.outer(mid @ axis_r, axis_r)
        flip = np.sum(np.einsum("ij,ij->i", fn, outv)) < 0
        if flip:
            side_l = side_l[:, [0, 2, 1]]
        glob = np.concatenate(rings)
        side_t = glob[side_l]
        last = rings[-1]
        cap_cam = (c + axis * length)[None, :]
        if root is None:
            P.append(to_bind(cap_cam).astype(np.float32))
        else:
            h = np.c_[from_cam(cap_cam), np.ones(1)]
            P.append((np.linalg.inv(M) @ h.T).T[:, :3].astype(np.float32))
        nb = to_bind(np.array([c + axis]), normals=True) - to_bind(np.array([c]), normals=True)
        nb = nb[0] / max(np.linalg.norm(nb[0]), 1e-9)
        N.append(nb[None, :].astype(np.float32))
        if U is not None:
            U.append((uv_in if uv_in is not None else prim["uvs"][ring].mean(axis=0))[None, :])
        if T is not None:
            T.append(tan_k[:1])
        J.append(jk[:1])
        W.append(wk[:1])
        ci = base
        base += 1
        lastc = rings_cam[-1]
        fl = np.stack([np.zeros(n_ring, int), loc + 1, np.roll(loc, -1) + 1], 1)
        fcam = from_cam(np.concatenate([cap_cam, lastc]))[fl]
        if np.sum(np.cross(fcam[:, 1] - fcam[:, 0], fcam[:, 2] - fcam[:, 0]) @ axis_r) < 0:
            fl = fl[:, [0, 2, 1]]
        fan_glob = np.concatenate([[ci], last])
        fan = fan_glob[fl]
        idx.append(side_t.ravel().astype(np.uint32))
        idx.append(fan.ravel().astype(np.uint32))
    outp = dict(prim)
    # the new skin is appended after the original triangles; the viewmodel
    # draws only these into the shadow map (the continuation sits round the
    # shoulder, behind the camera, and would only cast stray shadows)
    outp["index_count_original"] = int(prim.get("index_count_original", len(prim["indices"])))
    outp["positions"] = np.ascontiguousarray(np.concatenate(P), dtype=np.float32)
    outp["normals"] = np.ascontiguousarray(np.concatenate(N), dtype=np.float32)
    if U is not None:
        outp["uvs"] = np.ascontiguousarray(np.concatenate(U), dtype=np.float32)
    if T is not None:
        outp["tangents"] = np.ascontiguousarray(np.concatenate(T), dtype=np.float32)
    outp["joints"] = np.ascontiguousarray(np.concatenate(J), dtype=np.float32)
    outp["weights"] = np.ascontiguousarray(np.concatenate(W), dtype=np.float32)
    outp["indices"] = np.ascontiguousarray(np.concatenate(idx), dtype=np.uint32)
    return outp, len(loops)


def root_node(model):
    """The node both upper arms hang from (Arms_Root), or None."""
    names = [n.get("name", "") for n in model.glb.json.get("nodes", [])]
    for side in ("UpperArm.L", "UpperArm.R"):
        if side in names:
            par = model.parent[names.index(side)]
            if par is not None and par >= 0:
                return par
    return None


def root_row(model, node, clip=None, t=0.0):
    """Skinning matrix that makes a vertex follow `node` (it isn't a joint):
    its global transform now, times the inverse of its rest transform."""
    G = model.global_matrices(clip, t)
    R = model.global_matrices(None, 0.0)
    return (np.asarray(G[node], np.float64) @ np.linalg.inv(np.asarray(R[node], np.float64))).astype(np.float32)


def palette_with_root(model, node, clip=None, t=0.0):
    p = model.palette(clip, t)
    if node is None:
        return p
    return np.concatenate([p, root_row(model, node, clip, t)[None]], axis=0)


def close_arms(model, prims, hand_offset):
    """extend_cut for every arm primitive of a SkinnedModel, in its idle pose.

    Returns (prims, loops_closed, root_node).  When root_node is not None the
    new vertices use one extra bone, index len(model.joints): upload
    palette_with_root(...) instead of model.palette(...)."""
    ox, oy, oz = hand_offset
    idle = next((c for c in ("Idle", "Hold") if c in model.clips), None)
    node = root_node(model)
    palette = palette_with_root(model, node, idle, model.clips[idle].duration * 0.5 if idle else 0.0)
    root = len(model.joints) if node is not None else None

    def to_cam(p):
        return np.stack([-p[:, 0] + ox, p[:, 1] + oy, p[:, 2] + oz], 1)

    def from_cam(c):
        return np.stack([ox - c[:, 0], c[:, 1] - oy, c[:, 2] - oz], 1)

    out, closed = [], 0
    for pr in prims:
        if is_arm(pr):
            pr, k = extend_cut(pr, palette, to_cam, from_cam, root=root)
            closed += k
        out.append(pr)
    return out, closed, node


def is_arm(prim):
    txt = ("%s %s" % (prim.get("name") or "", prim.get("material") or "")).lower()
    return "arm" in txt

# @heaven3d-part h3d27/modules/meshutil.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ------------------------------------------------ h3d_meshutil
# ---------------------------------------------------------------------------
#  Shared polygon-mesh utilities for the Heaven3D importers.
#  A "PolyMesh" is the common intermediate form:
#     V            (nv,3)  float64 vertex positions
#     face_off     (nf+1,) int64   corner offset of every face
#     corner_vert  (nc,)   int64   vertex index of every face corner
#     face_mat     (nf,)   int64   material slot per face
#     face_smooth  (nf,)   bool    smooth shading per face
#     sharp_edges  set of (a,b) vertex pairs (a<b) that split normals
#     corner attrs (nc,k): uv0, uv1, col (linear RGBA), cnormal (custom)
# ---------------------------------------------------------------------------
import math
import numpy as np


class PolyMesh:
    __slots__ = ("V", "face_off", "corner_vert", "face_mat", "face_smooth",
                 "sharp_edges", "uv0", "uv1", "col", "cnormal",
                 "auto_smooth", "name")

    def __init__(self, V, face_off, corner_vert, face_mat=None,
                 face_smooth=None, name="mesh"):
        self.V = np.asarray(V, dtype=np.float64).reshape(-1, 3)
        self.face_off = np.asarray(face_off, dtype=np.int64)
        self.corner_vert = np.asarray(corner_vert, dtype=np.int64)
        nf = len(self.face_off) - 1
        self.face_mat = (np.zeros(nf, np.int64) if face_mat is None
                         else np.asarray(face_mat, np.int64))
        self.face_smooth = (np.zeros(nf, bool) if face_smooth is None
                            else np.asarray(face_smooth, bool))
        self.sharp_edges = None        # (k,2) int array, a<b, or None
        self.uv0 = self.uv1 = self.col = self.cnormal = None
        self.auto_smooth = None        # radians, or None
        self.name = name

    @property
    def nf(self):
        return len(self.face_off) - 1

    @property
    def nc(self):
        return len(self.corner_vert)

    def face_sizes(self):
        return np.diff(self.face_off)

    def corner_face(self):
        return np.repeat(np.arange(self.nf), self.face_sizes())

    def corner_next(self):
        """index of the next corner around each face."""
        sizes = self.face_sizes()
        start = np.repeat(self.face_off[:-1], sizes)
        local = np.arange(self.nc) - start
        size_c = np.repeat(sizes, sizes)
        return start + (local + 1) % size_c

    def corner_prev(self):
        sizes = self.face_sizes()
        start = np.repeat(self.face_off[:-1], sizes)
        local = np.arange(self.nc) - start
        size_c = np.repeat(sizes, sizes)
        return start + (local - 1) % size_c

    def corner_attrs(self):
        return [n for n in ("uv0", "uv1", "col", "cnormal")
                if getattr(self, n) is not None]

    def copy(self):
        m = PolyMesh(self.V.copy(), self.face_off.copy(),
                     self.corner_vert.copy(), self.face_mat.copy(),
                     self.face_smooth.copy(), self.name)
        m.sharp_edges = None if self.sharp_edges is None else self.sharp_edges.copy()
        for a in ("uv0", "uv1", "col", "cnormal"):
            v = getattr(self, a)
            setattr(m, a, None if v is None else v.copy())
        m.auto_smooth = self.auto_smooth
        return m

    def drop_degenerate(self):
        """Remove faces with <3 corners or repeated consecutive verts."""
        sizes = self.face_sizes()
        nxt = self.corner_next()
        dup = self.corner_vert == self.corner_vert[nxt]
        if not dup.any() and (sizes >= 3).all():
            return self
        keep_c = ~dup
        cf = self.corner_face()
        new_sizes = np.bincount(cf[keep_c], minlength=self.nf)
        keep_f = new_sizes >= 3
        keep_c &= keep_f[cf]
        self.corner_vert = self.corner_vert[keep_c]
        for a in self.corner_attrs():
            setattr(self, a, getattr(self, a)[keep_c])
        new_sizes = new_sizes[keep_f]
        self.face_off = np.concatenate([[0], np.cumsum(new_sizes)]).astype(np.int64)
        self.face_mat = self.face_mat[keep_f]
        self.face_smooth = self.face_smooth[keep_f]
        return self


    def remove_duplicate_faces(self):
        """Drop faces that use exactly the same vertex set as an earlier
        face (Blender's merge/weld keeps one copy)."""
        sizes = self.face_sizes()
        if not len(sizes):
            return self
        kmax = int(sizes.max())
        pad = np.full((self.nf, kmax), -1, np.int64)
        cf = self.corner_face()
        local = np.arange(self.nc) - np.repeat(self.face_off[:-1], sizes)
        pad[cf, local] = self.corner_vert
        pad.sort(1)
        _, first = np.unique(pad, axis=0, return_index=True)
        keep_f = np.zeros(self.nf, bool); keep_f[first] = True
        if keep_f.all():
            return self
        keep_c = keep_f[cf]
        self.corner_vert = self.corner_vert[keep_c]
        for a in self.corner_attrs():
            setattr(self, a, getattr(self, a)[keep_c])
        self.face_off = np.concatenate([[0], np.cumsum(sizes[keep_f])]).astype(np.int64)
        self.face_mat = self.face_mat[keep_f]
        self.face_smooth = self.face_smooth[keep_f]
        return self


# ------------------------------------------------------------ edges
def edge_keys(a, b, nv):
    lo = np.minimum(a, b)
    hi = np.maximum(a, b)
    return lo * np.int64(nv) + hi


def build_edges(m):
    """-> (edges (ne,2), corner_edge (nc,), edge_face_count (ne,))
    corner_edge[c] = edge from corner c to the next corner."""
    nxt = m.corner_next()
    a = m.corner_vert
    b = m.corner_vert[nxt]
    keys = edge_keys(a, b, len(m.V))
    uk, inv, cnt = np.unique(keys, return_inverse=True, return_counts=True)
    edges = np.stack([uk // len(m.V), uk % len(m.V)], 1)
    return edges, inv, cnt


# ------------------------------------------------------------ normals
def face_normals(m):
    """Newell normals (robust for n-gons), unit length."""
    V = m.V
    p = V[m.corner_vert]
    q = V[m.corner_vert[m.corner_next()]]
    cross = np.stack([(p[:, 1] - q[:, 1]) * (p[:, 2] + q[:, 2]),
                      (p[:, 2] - q[:, 2]) * (p[:, 0] + q[:, 0]),
                      (p[:, 0] - q[:, 0]) * (p[:, 1] + q[:, 1])], 1)
    fn = np.zeros((m.nf, 3))
    np.add.at(fn, m.corner_face(), cross)
    ln = np.linalg.norm(fn, axis=1, keepdims=True)
    ok = ln[:, 0] > 1e-20
    fn[ok] /= ln[ok]
    fn[~ok] = (0.0, 0.0, 1.0)
    return fn


def corner_angles(m):
    V = m.V
    c = V[m.corner_vert]
    a = V[m.corner_vert[m.corner_prev()]] - c
    b = V[m.corner_vert[m.corner_next()]] - c
    la = np.linalg.norm(a, axis=1)
    lb = np.linalg.norm(b, axis=1)
    d = np.einsum("ij,ij->i", a, b) / np.maximum(la * lb, 1e-30)
    return np.arccos(np.clip(d, -1.0, 1.0))


class _UF:
    def __init__(self, n):
        self.p = np.arange(n)

    def find_all(self):
        p = self.p
        while True:
            q = p[p]
            if np.array_equal(q, p):
                return p
            p = q

    def union_pairs(self, a, b):
        """Vectorised union of many pairs (label propagation)."""
        p = self.p
        a = np.asarray(a); b = np.asarray(b)
        if not len(a):
            return
        while True:
            ra = p[a]; rb = p[b]
            # compress
            while True:
                ra2 = p[ra]; rb2 = p[rb]
                if np.array_equal(ra2, ra) and np.array_equal(rb2, rb):
                    break
                ra, rb = ra2, rb2
            diff = ra != rb
            if not diff.any():
                break
            lo = np.minimum(ra[diff], rb[diff])
            hi = np.maximum(ra[diff], rb[diff])
            # point every hi root at the smallest lo seen for it
            order = np.lexsort((lo, hi))
            hi_s, lo_s = hi[order], lo[order]
            first = np.ones(len(hi_s), bool)
            first[1:] = hi_s[1:] != hi_s[:-1]
            p[hi_s[first]] = lo_s[first]
            # full path compression
            p[:] = p[p]
        self.p = p


def corner_normals(m, fn=None):
    """Per-corner normals following Blender's rules:
       flat faces -> face normal; smooth faces -> angle-weighted average
       over the 'smooth fan' around each vertex, where fans are split at
       sharp edges, boundary/non-manifold edges, flat neighbours and (if
       auto_smooth is set) edges sharper than the auto-smooth angle.
       Custom normals (cnormal) win when present."""
    if fn is None:
        fn = face_normals(m)
    nc = m.nc
    cf = m.corner_face()
    if m.cnormal is not None and len(m.cnormal) == nc:
        n = np.asarray(m.cnormal, np.float64)
        ln = np.linalg.norm(n, axis=1, keepdims=True)
        bad = ln[:, 0] < 1e-12
        n = n / np.maximum(ln, 1e-12)
        n[bad] = fn[cf[bad]]
        return n
    smooth_c = m.face_smooth[cf]
    out = fn[cf].copy()
    if not smooth_c.any():
        return out
    w = corner_angles(m)[:, None] * fn[cf]
    needs_split = (m.sharp_edges is not None and len(m.sharp_edges)) or \
        (m.auto_smooth is not None) or (not m.face_smooth.all())
    edges, corner_edge, ecount = build_edges(m)
    manifold_ok = (ecount <= 2).all()
    if not needs_split and manifold_ok:
        # fast path: plain per-vertex smooth normals
        vn = np.zeros((len(m.V), 3))
        np.add.at(vn, m.corner_vert, w)
        ln = np.linalg.norm(vn, axis=1, keepdims=True)
        vn = vn / np.maximum(ln, 1e-20)
        sel = smooth_c
        out[sel] = vn[m.corner_vert[sel]]
        return out
    # ---- fan splitting with union-find over corners ----
    nv = len(m.V)
    ne = len(edges)
    sharp = np.zeros(ne, bool)
    sharp |= ecount != 2
    if m.sharp_edges is not None and len(m.sharp_edges):
        sk = edge_keys(m.sharp_edges[:, 0], m.sharp_edges[:, 1], nv)
        ek = edges[:, 0] * nv + edges[:, 1]
        sharp |= np.isin(ek, sk)
    # each manifold edge: its two corners (corner c owns edge c->next)
    order = np.argsort(corner_edge, kind="stable")
    ce_sorted = corner_edge[order]
    two = ecount[ce_sorted] == 2
    oc = order[two]
    ce2 = ce_sorted[two]
    c1 = oc[0::2]; c2 = oc[1::2]
    e_ids = ce2[0::2]
    f1 = cf[c1]; f2 = cf[c2]
    ok = ~sharp[e_ids] & m.face_smooth[f1] & m.face_smooth[f2]
    if m.auto_smooth is not None:
        cosang = np.einsum("ij,ij->i", fn[f1], fn[f2])
        ok &= cosang >= math.cos(m.auto_smooth) - 1e-9
    c1, c2 = c1[ok], c2[ok]
    nxt = m.corner_next()
    # edge c1: v(c1) -> v(nxt c1); in face f2 the same edge runs
    # v(c2) -> v(nxt c2) reversed, so v(c1) == v(nxt c2) for consistent
    # winding, or v(c1) == v(c2) when the neighbour is flipped.
    v_c1 = m.corner_vert[c1]
    same = m.corner_vert[c2] == v_c1
    a1 = c1
    b1 = np.where(same, c2, nxt[c2])
    a2 = nxt[c1]
    b2 = np.where(same, nxt[c2], c2)
    uf = _UF(nc)
    uf.union_pairs(np.concatenate([a1, a2]), np.concatenate([b1, b2]))
    root = uf.find_all()
    acc = np.zeros((nc, 3))
    np.add.at(acc, root, w)
    gn = acc[root]
    ln = np.linalg.norm(gn, axis=1, keepdims=True)
    gn = gn / np.maximum(ln, 1e-20)
    out[smooth_c] = gn[smooth_c]
    return out


# ------------------------------------------------------------ triangulate
def _ear_clip(pts2):
    """Ear clipping of a simple polygon given as (k,2) points (any
    winding).  Returns list of (i,j,k) local index triples."""
    k = len(pts2)
    idx = list(range(k))
    area = 0.0
    for i in range(k):
        x1, y1 = pts2[i]; x2, y2 = pts2[(i + 1) % k]
        area += x1 * y2 - x2 * y1
    sign = 1.0 if area >= 0 else -1.0
    tris = []

    def cross(o, a, b):
        return ((a[0] - o[0]) * (b[1] - o[1]) - (a[1] - o[1]) * (b[0] - o[0])) * sign

    guard = 0
    while len(idx) > 3 and guard < 10 * k * k:
        guard += 1
        n = len(idx)
        clipped = False
        for t in range(n):
            i0, i1, i2 = idx[(t - 1) % n], idx[t], idx[(t + 1) % n]
            a, b, c = pts2[i0], pts2[i1], pts2[i2]
            if cross(a, b, c) <= 1e-18:
                continue
            inside = False
            for j in idx:
                if j in (i0, i1, i2):
                    continue
                p = pts2[j]
                if cross(a, b, p) >= -1e-15 and cross(b, c, p) >= -1e-15 and cross(c, a, p) >= -1e-15:
                    inside = True
                    break
            if inside:
                continue
            tris.append((i0, i1, i2))
            idx.pop(t)
            clipped = True
            break
        if not clipped:
            break
    if len(idx) == 3:
        tris.append(tuple(idx))
    elif len(idx) > 3:          # degenerate leftovers: fan them
        for t in range(1, len(idx) - 1):
            tris.append((idx[0], idx[t], idx[t + 1]))
    return tris


def triangulate(m, fn=None):
    """-> (T,3) array of CORNER indices + (T,) face index."""
    sizes = m.face_sizes()
    starts = m.face_off[:-1]
    out_c, out_f = [], []
    tri = np.nonzero(sizes == 3)[0]
    if len(tri):
        s = starts[tri]
        out_c.append(np.stack([s, s + 1, s + 2], 1)); out_f.append(tri)
    quad = np.nonzero(sizes == 4)[0]
    if len(quad):
        s = starts[quad]
        c = np.stack([s, s + 1, s + 2, s + 3], 1)
        P = m.V[m.corner_vert[c]]
        # split along 0-2 unless the quad is concave at 1 or 3
        d02 = np.linalg.norm(P[:, 0] - P[:, 2], axis=1)
        d13 = np.linalg.norm(P[:, 1] - P[:, 3], axis=1)
        n_a = np.cross(P[:, 1] - P[:, 0], P[:, 2] - P[:, 0])
        n_b = np.cross(P[:, 2] - P[:, 0], P[:, 3] - P[:, 0])
        concave02 = np.einsum("ij,ij->i", n_a, n_b) <= 0
        n_c = np.cross(P[:, 2] - P[:, 1], P[:, 3] - P[:, 1])
        n_d = np.cross(P[:, 3] - P[:, 1], P[:, 0] - P[:, 1])
        concave13 = np.einsum("ij,ij->i", n_c, n_d) <= 0
        use13 = concave02 & ~concave13
        t1 = np.where(use13[:, None], c[:, [0, 1, 3]], c[:, [0, 1, 2]])
        t2 = np.where(use13[:, None], c[:, [1, 2, 3]], c[:, [0, 2, 3]])
        out_c += [t1, t2]; out_f += [quad, quad]
        del d02, d13
    big = np.nonzero(sizes > 4)[0]
    if len(big):
        if fn is None:
            fn = face_normals(m)
        lc, lf = [], []
        for f in big:
            s, e = starts[f], m.face_off[f + 1]
            P = m.V[m.corner_vert[s:e]]
            n = fn[f]
            ax = np.argmax(np.abs(n))
            u = np.array([0.0, 0.0, 0.0]); u[(ax + 1) % 3] = 1.0
            u = u - n * np.dot(u, n); u /= max(np.linalg.norm(u), 1e-30)
            v = np.cross(n, u)
            pts = np.stack([P @ u, P @ v], 1)
            for (i, j, k) in _ear_clip(pts):
                lc.append((s + i, s + j, s + k)); lf.append(f)
        if lc:
            out_c.append(np.array(lc, np.int64)); out_f.append(np.array(lf, np.int64))
    if not out_c:
        return np.zeros((0, 3), np.int64), np.zeros(0, np.int64)
    C = np.concatenate(out_c).astype(np.int64)
    F = np.concatenate(out_f).astype(np.int64)
    order = np.argsort(F, kind="stable")
    return C[order], F[order]


# ------------------------------------------------------------ welding
def weld(pos, nrm, uv0=None, uv1=None, col=None, tan=None, decimals=6):
    """Merge identical vertices (all attributes equal) -> (unique arrays..., inverse)."""
    cols = [np.round(pos, decimals), np.round(nrm, 4)]
    if uv0 is not None: cols.append(np.round(uv0, 5))
    if uv1 is not None: cols.append(np.round(uv1, 5))
    if col is not None: cols.append(np.round(col, 4))
    if tan is not None: cols.append(np.round(tan, 4))
    key = np.ascontiguousarray(np.concatenate(cols, 1).astype(np.float32))
    kv = key.view(np.dtype((np.void, key.dtype.itemsize * key.shape[1]))).ravel()
    _, first, inv = np.unique(kv, return_index=True, return_inverse=True)
    return first, inv.ravel()


# ------------------------------------------------------------ transforms
def bake(pos, nrm, tan, M4, flip_winding_indices=None):
    """Apply a 4x4 matrix to positions / normals / tangents (in place
    returns new arrays) and report whether winding must be flipped."""
    M4 = np.asarray(M4, np.float64)
    M3 = M4[:3, :3]
    pos = pos @ M3.T + M4[:3, 3]
    det = float(np.linalg.det(M3))
    if nrm is not None:
        try:
            N = np.linalg.inv(M3).T
        except np.linalg.LinAlgError:
            N = M3
        nrm = nrm @ N.T
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        nrm = nrm / np.maximum(ln, 1e-20)
    if tan is not None:
        t3 = tan[:, :3] @ M3.T
        ln = np.linalg.norm(t3, axis=1, keepdims=True)
        t3 = t3 / np.maximum(ln, 1e-20)
        w = tan[:, 3] * (1.0 if det >= 0 else -1.0)
        tan = np.concatenate([t3, w[:, None]], 1)
    return pos, nrm, tan, det < 0


def srgb_to_linear(c):
    c = np.asarray(c, np.float64)
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(c):
    c = np.clip(np.asarray(c, np.float64), 0.0, None)
    return np.where(c <= 0.0031308, c * 12.92, 1.055 * np.power(c, 1 / 2.4) - 0.055)
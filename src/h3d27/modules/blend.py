# @heaven3d-part h3d27/modules/blend.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ------------------------------------------------ h3d_blend
# ---------------------------------------------------------------------------
#  Heaven3D .blend importer (pure Python + numpy, no Blender needed).
#  Blender 2.4x -> 5.x, compressed or not, 32/64-bit, little/big endian.
#  Output: list of material "parts" (world transform + Z-up->Y-up baked in)
#  and a list of light descriptors.
# ---------------------------------------------------------------------------
import os, math, struct
import numpy as np

ZUP_TO_YUP = np.array([[1.0, 0, 0, 0], [0, 0, 1.0, 0], [0, -1.0, 0, 0],
                       [0, 0, 0, 1.0]])

OB_EMPTY, OB_MESH, OB_CURVE, OB_SURF, OB_FONT, OB_MBALL = 0, 1, 2, 3, 4, 5
OB_LAMP, OB_CAMERA, OB_ARMATURE = 10, 11, 25
LEGACY_SUBSURF_LIMIT = True
OB_DUPLICOLLECTION = 1 << 8
OB_HIDE_RENDER = (1 << 2) | (1 << 0)    # disabled in renders OR viewports
ME_SMOOTH = 1
ME_SHARP = 1 << 9
ME_AUTOSMOOTH = 1 << 5

# Blender 5.x AttributeStorage data types / domains
_ATTR5_KIND = {0: "bool", 1: "int8", 2: "int16_2d", 3: "int", 4: "int2",
               5: "float", 6: "float2", 7: "float3", 8: "float4x4",
               9: "color_byte", 10: "color_float", 11: "quat"}
_DOMAINS = {0: "point", 1: "edge", 2: "face", 3: "corner"}
# CustomData layer types (stable ids)
_CD_KIND = {10: "float", 11: "int", 17: "color_byte", 41: "int16_2d", 45: "int8",
            47: "color_float", 48: "float3", 49: "float2", 50: "bool"}
_KIND_DTYPE = {"bool": ("u1", 1), "int8": ("i1", 1), "int": ("i4", 1),
               "int2": ("i4", 2), "int16_2d": ("i2", 2), "float": ("f4", 1),
               "float2": ("f4", 2), "float3": ("f4", 3),
               "color_byte": ("u1", 4), "color_float": ("f4", 4),
               "quat": ("f4", 4), "float4x4": ("f4", 16)}


def _log(logger, level, msg):
    if logger is not None:
        try:
            getattr(logger, level)(msg)
            return
        except Exception:
            pass


# ======================================================================
#  matrices
# ======================================================================
def _euler_mat3(e, mode):
    """Blender eulO_to_mat3: rotation order XYZ means X applied first."""
    cx, cy, cz = (math.cos(a) for a in e)
    sx, sy, sz = (math.sin(a) for a in e)
    X = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    Y = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    Z = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    R = {"X": X, "Y": Y, "Z": Z}
    order = {1: "XYZ", 2: "XZY", 3: "YXZ", 4: "YZX", 5: "ZXY", 6: "ZYX"}.get(mode, "XYZ")
    M = np.eye(3)
    for ax in order:                     # first axis applied first
        M = R[ax] @ M
    return M


def _quat_mat3(q):
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


def _axis_angle_mat3(axis, angle):
    a = np.asarray(axis, float)
    n = np.linalg.norm(a)
    if n < 1e-12:
        return np.eye(3)
    a = a / n
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = a
    return np.array([[c + x * x * (1 - c), x * y * (1 - c) - z * s, x * z * (1 - c) + y * s],
                     [y * x * (1 - c) + z * s, c + y * y * (1 - c), y * z * (1 - c) - x * s],
                     [z * x * (1 - c) - y * s, z * y * (1 - c) + x * s, c + z * z * (1 - c)]])


def _blender_mat4(arr):
    """float[4][4] (column-major, m[3] = translation) -> numpy row-major."""
    a = np.asarray(arr, float).reshape(4, 4)
    return a.T.copy()


class _Ctx:
    def __init__(self, bf, path, logger):
        self.bf = bf
        self.path = path
        self.dir = os.path.dirname(os.path.abspath(path))
        self.log = logger
        self.world_cache = {}
        self.warned = set()
        self.images = {}
        self.materials = {}

    def warn_once(self, key, msg):
        if key not in self.warned:
            self.warned.add(key)
            _log(self.log, "warn", msg)


# ======================================================================
#  objects
# ======================================================================
def _local_matrix(ob):
    loc = ob.floats("loc", np.zeros(3))
    dloc = ob.floats("dloc", np.zeros(3))
    size = ob.floats("size", np.ones(3))
    dsc = ob.floats("dscale", None)
    if dsc is None:
        dsc = ob.floats("dsize", None)
        dsc = np.ones(3) if dsc is None else (np.ones(3) + dsc if ob.bf.version < 260 else dsc)
    if not np.any(dsc):
        dsc = np.ones(3)
    mode = ob.int("rotmode", 1)
    if mode == 0:            # quaternion
        R = _quat_mat3(ob.floats("dquat", np.array([1.0, 0, 0, 0]))) @ \
            _quat_mat3(ob.floats("quat", np.array([1.0, 0, 0, 0])))
    elif mode == -1 or mode == 65535:   # axis-angle (short -1)
        R = _axis_angle_mat3(ob.floats("drotAxis", np.array([0, 1.0, 0])), ob.float("drotAngle")) @ \
            _axis_angle_mat3(ob.floats("rotAxis", np.array([0, 1.0, 0])), ob.float("rotAngle"))
    else:
        R = _euler_mat3(ob.floats("drot", np.zeros(3)), mode) @ \
            _euler_mat3(ob.floats("rot", np.zeros(3)), mode)
    M = np.eye(4)
    M[:3, :3] = R @ np.diag(size * dsc)
    M[:3, 3] = loc + dloc
    return M


def _world_matrix(ctx, ob, depth=0):
    key = ob.addr()
    if key in ctx.world_cache:
        return ctx.world_cache[key]
    M = None
    om = ob.floats("obmat")
    if om is not None and np.abs(om).sum() > 1e-12 and abs(om[3][3] - 1.0) < 1e-4:
        M = _blender_mat4(om)               # Blender <= 3.x stores it
    if M is None:
        M = _local_matrix(ob)
        par = ob.deref("parent", "Object")
        if par is not None and depth < 64:
            PW = _world_matrix(ctx, par, depth + 1)
            pinv = ob.floats("parentinv")
            PI = _blender_mat4(pinv) if pinv is not None else np.eye(4)
            M = PW @ PI @ M
    ctx.world_cache[key] = M
    return M


def _scene_objects(ctx):
    """-> list of (Object view, world 4x4 Blender space)."""
    bf = ctx.bf
    scene = None
    glob = bf.blocks_by_code(b"GLOB")
    vl_ptr = 0
    if glob:
        g = bf.view(glob[0], "FileGlobal")
        if g is not None:
            scene = g.deref("curscene", "Scene")
            vl_ptr = g.ptr("cur_view_layer")
    if scene is None:
        scs = bf.blocks_by_code(b"SC\0\0")
        if scs:
            scene = bf.view(scs[0], "Scene")
    obs = []
    if scene is not None:
        if scene.has("view_layers"):
            layers = bf.listbase(scene.sub("view_layers"), "ViewLayer")
            vl = next((l for l in layers if l.addr() == vl_ptr), None) or (layers[0] if layers else None)
            if vl is not None:
                for base in bf.listbase(vl.sub("object_bases"), "Base"):
                    o = base.deref("object", "Object")
                    if o is not None:
                        obs.append(o)
        elif scene.has("base"):
            lay = scene.int("lay", 0xFFFFFFFF) or 0xFFFFFFFF
            for base in bf.listbase(scene.sub("base"), "Base"):
                o = base.deref("object", "Object")
                if o is None:
                    continue
                blay = base.int("lay", o.int("lay", 1))
                if blay and not (blay & lay):
                    continue
                obs.append(o)
    if not obs:
        obs = [bf.view(b, "Object") for b in bf.blocks_by_code(b"OB\0\0")]
        obs = [o for o in obs if o is not None]
    hidden_by_coll = _hidden_collection_objects(ctx, scene)
    out = []
    seen = set()
    for o in obs:
        if o.addr() in seen:
            continue
        seen.add(o.addr())
        if o.int("restrictflag", 0) & OB_HIDE_RENDER or o.addr() in hidden_by_coll:
            continue
        out.append((o, _world_matrix(ctx, o)))
    # collection instances (recursive)
    final = []

    def expand(o, W, depth):
        final.append((o, W))
        if depth > 8 or not (o.int("transflag", 0) & OB_DUPLICOLLECTION):
            return
        grp = o.deref("dup_group")
        if grp is None:
            return
        for co, cw in _collection_objects(ctx, grp, depth):
            expand(co, W @ cw, depth + 1)

    for o, W in out:
        expand(o, W, 0)
    return final


COLLECTION_HIDDEN = (1 << 0) | (1 << 3)      # RESTRICT_VIEWPORT | RESTRICT_RENDER


def _hidden_collection_objects(ctx, scene):
    """Objects that are only in collections switched off (monitor / camera
    icon) - the same rule as for objects switched off themselves."""
    if scene is None or not scene.has("master_collection"):
        return set()
    bf = ctx.bf
    root = scene.deref("master_collection", "Collection")
    if root is None:
        return set()
    shown, hidden = set(), set()

    def walk(c, off, d):
        if d > 32:
            return
        off = off or bool(c.int("flag", 0) & COLLECTION_HIDDEN)
        for co in bf.listbase(c.sub("gobject")):
            o = co.ptr("ob")
            if o:
                (hidden if off else shown).add(o)
        for ch in bf.listbase(c.sub("children")):
            cc = ch.deref("collection", "Collection")
            if cc is not None:
                walk(cc, off, d + 1)
    try:
        walk(root, False, 0)
    except Exception:
        return set()
    return hidden - shown


def _collection_objects(ctx, coll, depth):
    """Objects of a Collection/Group with transforms relative to the
    instancing empty (instance offset applied)."""
    bf = ctx.bf
    off = coll.floats("instance_offset", None)
    if off is None:
        off = coll.floats("dupli_ofs", np.zeros(3))
    T = np.eye(4); T[:3, 3] = -np.asarray(off)
    out = []
    seen = set()

    def walk(c, d):
        if d > 16:
            return
        for co in bf.listbase(c.sub("gobject")):
            o = co.deref("ob", "Object")
            if o is None or o.addr() in seen:
                continue
            seen.add(o.addr())
            if o.int("restrictflag", 0) & OB_HIDE_RENDER:
                continue
            out.append((o, T @ _world_matrix(ctx, o)))
        if c.has("children"):
            for ch in bf.listbase(c.sub("children")):
                cc = ch.deref("collection")
                if cc is not None:
                    walk(cc, d + 1)

    walk(coll, 0)
    return out


# ======================================================================
#  mesh decoding
# ======================================================================
def _read_raw(bf, ptr, ctx_view, kind, count):
    dt, comps = _KIND_DTYPE[kind]
    a = bf.array(ptr, dt, count * comps, ctx=ctx_view)
    if a is None:
        return None
    return a.reshape(count, comps) if comps > 1 else a


def _gather_attributes(bf, me, counts):
    attrs = []
    # ---- Blender 5.x: AttributeStorage ------------------------------
    st = me.sub("attribute_storage")
    if st is not None and st.int("dna_attributes_num", 0) > 0:
        n = st.int("dna_attributes_num")
        for a in st.struct_array("dna_attributes", "Attribute", n):
            kind = _ATTR5_KIND.get(a.int("data_type", -1))
            dom = _DOMAINS.get(a.int("domain", -1))
            if kind is None or dom is None:
                continue
            name = a.cstring("name")
            cnt = counts.get(dom, 0)
            if a.int("storage_type", 0) == 0:
                av = a.deref("data", "AttributeArray")
                if av is None:
                    continue
                data = _read_raw(bf, av.ptr("data"), av, kind, cnt)
            else:
                av = a.deref("data", "AttributeSingle")
                if av is None:
                    continue
                one = _read_raw(bf, av.ptr("data"), av, kind, 1)
                data = None if one is None else np.repeat(one.reshape(1, -1), cnt, 0).reshape((cnt,) + one.shape[1:])
            if data is not None:
                attrs.append({"name": name, "domain": dom, "kind": kind,
                              "data": data, "render": False, "cd": None})
    # ---- CustomData layers (all versions) ------------------------------
    for dom, field in (("point", "vdata"), ("edge", "edata"), ("face", "pdata"),
                       ("corner", "ldata"), ("tessface", "fdata")):
        cd = me.sub(field)
        if cd is None:
            continue
        tot = cd.int("totlayer", 0)
        if tot <= 0:
            continue
        layers = cd.struct_array("layers", "CustomDataLayer", tot)
        first_of_type = {}
        for i, lv in enumerate(layers):
            first_of_type.setdefault(lv.int("type", -1), i)
        cnt = counts.get(dom, 0)
        for i, lv in enumerate(layers):
            t = lv.int("type", -1)
            ptr = lv.ptr("data")
            if not ptr:
                continue
            name = lv.str("name")
            render = (i - first_of_type[t]) == lv.int("active_rnd", 0)
            entry = {"name": name, "domain": dom, "render": render, "cd": t,
                     "ptr": ptr, "lv": lv, "data": None, "kind": None}
            if t in _CD_KIND:
                kind = _CD_KIND[t]
                if t == 17:      # MLoopCol struct r,g,b,a
                    rec = bf.struct_records(ptr, "MLoopCol", cnt,
                                            {"r": "u1", "g": "u1", "b": "u1", "a": "u1"}, ctx=lv)
                    data = (np.stack([rec["r"], rec["g"], rec["b"], rec["a"]], 1)
                            if rec and len(rec) == 4 else _read_raw(bf, ptr, lv, kind, cnt))
                else:
                    data = _read_raw(bf, ptr, lv, kind, cnt)
                entry.update(kind=kind, data=data)
            elif t == 46 and (name == ".edge_verts"):
                entry.update(kind="int2", data=_read_raw(bf, ptr, lv, "int2", cnt))
            elif t == 16:        # MLoopUV
                rec = bf.struct_records(ptr, "MLoopUV", cnt, {"uv": "f4"}, ctx=lv)
                if rec and "uv" in rec:
                    entry.update(kind="float2", data=np.asarray(rec["uv"]).reshape(cnt, 2))
            if entry["data"] is not None or t in (0, 3, 4, 5, 6, 25, 26):
                attrs.append(entry)
    return attrs


def _find(attrs, name=None, domain=None, kind=None, cd=None):
    for a in attrs:
        if name is not None and a["name"] != name:
            continue
        if domain is not None and a["domain"] != domain:
            continue
        if kind is not None and a["kind"] not in (kind if isinstance(kind, tuple) else (kind,)):
            continue
        if cd is not None and a["cd"] != cd:
            continue
        if a.get("data") is None and cd is None:
            continue
        return a
    return None


_LNOR_TRIGO = 1.0 - 1e-4


def _decode_packed_normals(m, cl):
    """Blender's custom split normals stored as two shorts per corner,
    relative to the 'loop normal space' of its smooth fan (port of
    mesh_normals.cc: loop_split_generator / lnor_space_define /
    lnor_space_custom_data_to_normal).  The fan walk records indices only;
    the maths runs vectorised.  -> (corners, 3) normals."""
    V = m.V
    cv = m.corner_vert
    nl = len(cv)
    cf = m.corner_face()
    nxt = m.corner_next()
    prv = m.corner_prev()
    fn = face_normals(m)
    ang = corner_angles(m)
    a, b = cv, cv[nxt]
    key = np.stack([np.minimum(a, b), np.maximum(a, b)], 1)
    uniq, ce = np.unique(key, axis=0, return_inverse=True)
    ce = ce.ravel()
    ne = len(uniq)
    cnt = np.bincount(ce, minlength=ne)
    e2l = np.full((ne, 2), -1, np.int64)
    order = np.argsort(ce, kind="stable")
    starts = np.concatenate([[0], np.cumsum(cnt)])
    two = cnt == 2
    e2 = np.where(two)[0]
    e2l[e2, 0] = order[starts[e2]]
    e2l[e2, 1] = order[starts[e2] + 1]
    sharp = ~two
    l0, l1 = e2l[two, 0], e2l[two, 1]
    sharp[e2] |= cv[l0] == cv[l1]                      # flipped neighbour
    flat = ~m.face_smooth
    sharp[e2] |= flat[cf[l0]] | flat[cf[l1]]
    if m.sharp_edges is not None and len(m.sharp_edges):
        se = np.sort(np.asarray(m.sharp_edges, np.int64), 1)
        code_u = uniq[:, 0] * (len(V) + 1) + uniq[:, 1]
        sharp |= np.isin(code_u, se[:, 0] * (len(V) + 1) + se[:, 1])
    if m.auto_smooth is not None:
        d = np.einsum("ij,ij->i", fn[cf[l0]], fn[cf[l1]])
        sharp[e2[d < math.cos(float(m.auto_smooth))]] = True
    cvl, nxtl, prvl, cel = cv.tolist(), nxt.tolist(), prv.tolist(), ce.tolist()
    e2ll = e2l.tolist()
    sharpl = sharp.tolist()
    ul = uniq.tolist()
    fan_of = [-1] * nl
    f_start, f_ref, f_oth = [], [], []                 # per fan: start corner, ref / other vertex
    ev_fan, ev_vert = [], []                           # edge vectors of fans (non-single)
    skip = [False] * nl

    def fan_next(cur, pivot):
        pair = e2ll[cel[cur]]                          # only called across manifold edges
        other = pair[1] if pair[0] == cur else pair[0]
        if cvl[other] == pivot:
            return prvl[other], other
        n_ = nxtl[other]
        return n_, n_

    def cyclic_fan_new(c):
        pivot = cvl[c]
        cur, vert = prvl[c], c
        if sharpl[cel[cur]]:
            return False
        skip[vert] = True
        for _guard in range(nl):
            cur, vert = fan_next(cur, pivot)
            if sharpl[cel[cur]]:
                return False
            if skip[vert]:
                return vert == c
            skip[vert] = True
        return False

    def other_end(e, pivot):
        x, y = ul[e]
        return y if x == pivot else x

    single = []
    for c in range(nl):                                # faces in order, corners in order
        ec, ep = cel[c], cel[prvl[c]]
        if not sharpl[ec] and (skip[c] or not cyclic_fan_new(c)):
            continue
        pivot = cvl[c]
        fid = len(f_start)
        f_start.append(c)
        if sharpl[ec] and sharpl[ep]:
            fan_of[c] = fid
            f_ref.append(cvl[nxtl[c]])
            f_oth.append(cvl[prvl[c]])
            single.append(True)
            continue
        single.append(False)
        e_orig = ec
        f_ref.append(cvl[nxtl[c]])
        ev_fan.append(fid); ev_vert.append(cvl[nxtl[c]])
        cur, vert = prvl[c], c
        last = cvl[nxtl[c]]
        for _guard in range(nl):
            e = cel[cur]
            last = other_end(e, pivot)
            fan_of[vert] = fid
            if e != e_orig:
                ev_fan.append(fid); ev_vert.append(last)
            if sharpl[e] or e == e_orig:
                break
            cur, vert = fan_next(cur, pivot)
        f_oth.append(last)
    nfan = len(f_start)
    fan_of = np.asarray(fan_of, np.int64)
    out = np.zeros((nl, 3))
    if nfan == 0:
        return out
    ok = fan_of >= 0
    f_start = np.asarray(f_start, np.int64)
    single = np.asarray(single, bool)
    piv = cv[f_start]
    # fan normal: corner-angle weighted face normals (singles: the face normal)
    lnor = np.zeros((nfan, 3))
    np.add.at(lnor, fan_of[ok], fn[cf[ok]] * ang[ok, None])
    lnor[single] = fn[cf[f_start[single]]]
    ln = np.linalg.norm(lnor, axis=1, keepdims=True)
    zero = ln[:, 0] < 1e-20
    lnor = np.where(zero[:, None], fn[cf[f_start]], lnor / np.maximum(ln, 1e-20))

    def dirs(verts, pivots):
        d = V[verts] - V[pivots]
        n = np.linalg.norm(d, axis=1, keepdims=True)
        return d / np.maximum(n, 1e-30)
    vref = dirs(np.asarray(f_ref, np.int64), piv)
    voth = dirs(np.asarray(f_oth, np.int64), piv)
    dr = np.einsum("ij,ij->i", vref, lnor)
    do = np.einsum("ij,ij->i", voth, lnor)
    valid = (np.abs(dr) < _LNOR_TRIGO) & (np.abs(do) < _LNOR_TRIGO)
    # reference alpha: mean angle of the fan's edges to its normal
    ra = (np.arccos(np.clip(dr, -1, 1)) + np.arccos(np.clip(do, -1, 1))) / 2.0
    if ev_fan:
        evf = np.asarray(ev_fan, np.int64)
        evd = dirs(np.asarray(ev_vert, np.int64), piv[evf])
        acc = np.bincount(evf, np.arccos(np.clip(np.einsum("ij,ij->i", evd, lnor[evf]), -1, 1)), nfan)
        num = np.bincount(evf, minlength=nfan)
        has = num > 0
        ra[has] = acc[has] / num[has]
    ref = vref - lnor * dr[:, None]
    ref /= np.maximum(np.linalg.norm(ref, axis=1, keepdims=True), 1e-30)
    orth = np.cross(lnor, ref)
    orth /= np.maximum(np.linalg.norm(orth, axis=1, keepdims=True), 1e-30)
    oth = voth - lnor * do[:, None]
    oth /= np.maximum(np.linalg.norm(oth, axis=1, keepdims=True), 1e-30)
    dtp = np.einsum("ij,ij->i", ref, oth)
    be = np.arccos(np.clip(dtp, -1, 1))
    rb = np.where(np.einsum("ij,ij->i", orth, oth) < 0, 2 * math.pi - be, be)
    rb = np.where(dtp < _LNOR_TRIGO, rb, 2 * math.pi)
    # the stored shorts of each fan (Blender averages them if they differ)
    cl = np.asarray(cl, np.int64)
    sums = np.zeros((nfan, 2))
    np.add.at(sums, fan_of[ok], cl[ok])
    nmem = np.bincount(fan_of[ok], minlength=nfan).astype(float)
    lo = np.full((nfan, 2), np.iinfo(np.int64).max)
    hi = np.full((nfan, 2), np.iinfo(np.int64).min)
    np.minimum.at(lo, fan_of[ok], cl[ok])
    np.maximum.at(hi, fan_of[ok], cl[ok])
    same = np.all(lo == hi, axis=1)
    avg = np.trunc(sums / np.maximum(nmem, 1)[:, None])
    fcl = np.where(same[:, None], lo, avg).astype(float)
    af = fcl[:, 0] / 32767.0
    bf_ = fcl[:, 1] / 32767.0
    al = np.where(af > 0, ra, 2 * math.pi - ra) * af
    bt = np.where(bf_ > 0, rb, 2 * math.pi - rb) * bf_
    sa = np.sin(al)
    r = lnor * np.cos(al)[:, None] + ref * (sa * np.cos(bt))[:, None] + orth * (sa * np.sin(bt))[:, None]
    r /= np.maximum(np.linalg.norm(r, axis=1, keepdims=True), 1e-30)
    keep = (fcl[:, 0] == 0) | (ra == 0) | (rb == 0) | ~valid
    r[keep] = lnor[keep]
    out[ok] = r[fan_of[ok]]
    return out


def _decode_mesh(ctx, me):
    """Mesh view -> (PolyMesh, info).  info: uv names, colour layer name,
    per-face image (legacy TexFace) ... ."""
    bf = ctx.bf
    nv = me.int("totvert", 0)
    ne = me.int("totedge", 0)
    nf = me.int("totpoly", 0) if me.has("totpoly") else 0
    nl = me.int("totloop", 0) if me.has("totloop") else 0
    ntf = me.int("totface", 0) if me.has("totface") else 0
    counts = {"point": nv, "edge": ne, "face": nf, "corner": nl, "tessface": ntf}
    attrs = _gather_attributes(bf, me, counts)
    info = {"uv_names": [], "col_name": None, "face_image": None}

    # ---------------- positions
    V = None
    a = _find(attrs, "position", "point", "float3")
    if a is not None:
        V = a["data"].astype(np.float64)
    if V is None:
        mv = _find(attrs, domain="point", cd=0)
        ptr = mv["ptr"] if mv else me.ptr("mvert")
        rec = bf.struct_records(ptr, "MVert", nv, {"co": "f4"}, ctx=mv["lv"] if mv else me)
        if rec and "co" in rec:
            V = np.asarray(rec["co"], np.float64).reshape(nv, 3)
    if V is None or nv == 0:
        return None, info

    # ---------------- faces
    face_off = corner_vert = None
    face_mat = face_smooth = None
    legacy_faces = False
    if nf > 0 and nl > 0:
        cv = _find(attrs, ".corner_vert", "corner", "int")
        if cv is not None:
            corner_vert = cv["data"].astype(np.int64)
            po = me.ptr("poly_offset_indices") if me.has("poly_offset_indices") else 0
            off = bf.array(po, "i4", nf + 1, ctx=me) if po else None
            if off is not None:
                face_off = off.astype(np.int64)
        if face_off is None or corner_vert is None:
            mp = _find(attrs, domain="face", cd=25)
            ml = _find(attrs, domain="corner", cd=26)
            prec = bf.struct_records(mp["ptr"] if mp else me.ptr("mpoly"), "MPoly", nf,
                                     {"loopstart": "i4", "totloop": "i4", "mat_nr": "i2", "flag": "u1"},
                                     ctx=mp["lv"] if mp else me)
            lrec = bf.struct_records(ml["ptr"] if ml else me.ptr("mloop"), "MLoop", nl,
                                     {"v": "u4"}, ctx=ml["lv"] if ml else me)
            if prec and lrec and "loopstart" in prec and "v" in lrec:
                ls = prec["loopstart"].astype(np.int64)
                tl = prec["totloop"].astype(np.int64)
                order = np.argsort(ls, kind="stable")
                cv_all = lrec["v"].astype(np.int64)
                parts = [np.arange(ls[i], ls[i] + tl[i]) for i in order]
                corner_idx = np.concatenate(parts) if parts else np.zeros(0, np.int64)
                corner_vert = cv_all[corner_idx]
                face_off = np.concatenate([[0], np.cumsum(tl[order])]).astype(np.int64)
                if "mat_nr" in prec:
                    face_mat = prec["mat_nr"].astype(np.int64)[order]
                if "flag" in prec:
                    face_smooth = (prec["flag"][order] & ME_SMOOTH) != 0
                # corner attributes must follow the same reordering
                info["corner_reorder"] = corner_idx
    if face_off is None and ntf > 0:
        mf = _find(attrs, domain="tessface", cd=4)
        rec = bf.struct_records(mf["ptr"] if mf else me.ptr("mface"), "MFace", ntf,
                                {"v1": "u4", "v2": "u4", "v3": "u4", "v4": "u4",
                                 "mat_nr": "i2", "flag": "u1"}, ctx=mf["lv"] if mf else me)
        if rec and "v1" in rec:
            v = np.stack([rec["v1"], rec["v2"], rec["v3"], rec["v4"]], 1).astype(np.int64)
            quad = v[:, 3] != 0
            sizes = np.where(quad, 4, 3)
            mask = np.ones_like(v, bool); mask[:, 3] = quad
            corner_vert = v[mask]
            face_off = np.concatenate([[0], np.cumsum(sizes)]).astype(np.int64)
            face_mat = rec["mat_nr"].astype(np.int64) if "mat_nr" in rec else None
            face_smooth = (rec["flag"] & ME_SMOOTH) != 0 if "flag" in rec else None
            legacy_faces = True
            info["tess_mask"] = mask
            nf = len(sizes)
    if face_off is None:
        return None, info
    nf = len(face_off) - 1
    m = PolyMesh(V, face_off, corner_vert, face_mat, face_smooth, me.id_name())
    reorder = info.get("corner_reorder")

    def corner_data(arr):
        arr = np.asarray(arr)
        return arr[reorder] if reorder is not None else arr

    # ---------------- material index / smooth (generic attributes)
    if face_mat is None:
        a = _find(attrs, "material_index", "face", "int")
        m.face_mat = a["data"].astype(np.int64) if a is not None else np.zeros(nf, np.int64)
    if face_smooth is None:
        a = _find(attrs, "sharp_face", "face", "bool")
        if a is not None:
            m.face_smooth = a["data"] == 0
        else:
            # 4.0+: a mesh with no "sharp_face" attribute is fully smooth
            m.face_smooth = np.full(nf, bf.version >= 400 and not legacy_faces)
    # ---------------- sharp edges
    ev = _find(attrs, ".edge_verts", "edge", "int2")
    se = _find(attrs, "sharp_edge", "edge", "bool")
    if ev is not None and se is not None:
        sel = se["data"] != 0
        if sel.any():
            e = ev["data"][sel].astype(np.int64)
            m.sharp_edges = np.sort(e, 1)
    elif ne > 0:
        med = _find(attrs, domain="edge", cd=3)
        rec = bf.struct_records(med["ptr"] if med else me.ptr("medge"), "MEdge", ne,
                                {"v1": "u4", "v2": "u4", "flag": "i2"}, ctx=med["lv"] if med else me)
        if rec and "flag" in rec:
            sel = (rec["flag"].astype(np.int64) & ME_SHARP) != 0
            if sel.any():
                m.sharp_edges = np.sort(np.stack([rec["v1"][sel], rec["v2"][sel]], 1).astype(np.int64), 1)
    # ---------------- legacy auto smooth (<= 4.0)
    if bf.version < 410 and (me.int("flag", 0) & ME_AUTOSMOOTH):
        f = me.fields.get("smoothresh")
        if f is not None:
            if f.type == "float":
                m.auto_smooth = me.float("smoothresh", math.radians(30))
            else:
                m.auto_smooth = math.radians(me.int("smoothresh", 30))

    # ---------------- UV layers
    uvs = []
    if legacy_faces:
        mask = info["tess_mask"]
        for a in attrs:
            if a["domain"] == "tessface" and a["cd"] == 5:
                rec = bf.struct_records(a["ptr"], "MTFace", ntf, {"uv": "f4", "tpage": "u8" if bf.ptrsize == 8 else "u4"}, ctx=a["lv"])
                if rec and "uv" in rec:
                    uv = np.asarray(rec["uv"]).reshape(ntf, 4, 2)[mask]
                    uvs.append((a["name"] or "UVMap", uv, a["render"]))
                    if "tpage" in rec and info["face_image"] is None and np.any(rec["tpage"]):
                        info["face_image"] = (rec["tpage"].astype(np.uint64), a["lv"])
    else:
        for a in attrs:
            if a["domain"] == "corner" and a["kind"] == "float2" and not a["name"].startswith("."):
                uvs.append((a["name"], corner_data(a["data"]).astype(np.float64), a["render"]))
    if uvs:
        default = me.cstring("default_uv_map_attribute") if me.has("default_uv_map_attribute") else ""
        active = me.cstring("active_uv_map_attribute") if me.has("active_uv_map_attribute") else ""
        pick = next((u for u in uvs if default and u[0] == default), None) or \
            next((u for u in uvs if u[2] and not default), None) or \
            next((u for u in uvs if active and u[0] == active), None) or uvs[0]
        rest = [u for u in uvs if u is not pick]
        m.uv0 = pick[1]
        info["uv_names"] = [pick[0]] + [u[0] for u in rest]
        if rest:
            m.uv1 = rest[0][1]

    # ---------------- colour layers
    cols = []
    if legacy_faces:
        mask = info["tess_mask"]
        for a in attrs:
            if a["domain"] == "tessface" and a["cd"] == 6:
                rec = bf.struct_records(a["ptr"], "MCol", ntf * 4, {"a": "u1", "r": "u1", "g": "u1", "b": "u1"}, ctx=a["lv"])
                if rec and "r" in rec:
                    # MCol stores BGR in its r/g/b fields
                    c = np.stack([rec["b"], rec["g"], rec["r"], rec["a"]], 1).reshape(ntf, 4, 4)[mask]
                    cols.append((a["name"] or "Col", c, a["render"]))
    else:
        for a in attrs:
            if a["kind"] in ("color_byte", "color_float") and a["domain"] in ("point", "corner") \
                    and not a["name"].startswith("."):
                d = a["data"]
                d = d[m.corner_vert] if a["domain"] == "point" else corner_data(d)
                cols.append((a["name"], d, a["render"]))
    if cols:
        default = me.cstring("default_color_attribute") if me.has("default_color_attribute") else ""
        active = me.cstring("active_color_attribute") if me.has("active_color_attribute") else ""
        pick = next((c for c in cols if default and c[0] == default), None) or \
            next((c for c in cols if active and c[0] == active), None) or \
            next((c for c in cols if c[2]), None) or cols[0]
        d = np.asarray(pick[1])
        if d.dtype == np.uint8:
            rgb = srgb_to_linear(d[:, :3] / 255.0)
            m.col = np.concatenate([rgb, d[:, 3:4] / 255.0], 1)
        else:
            m.col = d.astype(np.float64)
        info["col_name"] = pick[0]
        info["col_names"] = [c[0] for c in cols]
    # ---------------- custom normals: 5.x float3 'custom_normal', or the
    # packed per-fan "normal space" form (5.x int16_2d / CD_CUSTOMLOOPNORMAL)
    cn = _find(attrs, "custom_normal", "corner", "float3")
    packed = _find(attrs, "custom_normal", "corner", "int16_2d") or _find(attrs, domain="corner", cd=41)
    if cn is not None:
        m.cnormal = corner_data(cn["data"]).astype(np.float64)
    elif packed is not None and packed.get("data") is not None:
        try:
            cl = corner_data(np.asarray(packed["data"]).reshape(-1, 2)).astype(np.int64)
            if len(cl) == len(m.corner_vert):
                m.cnormal = _decode_packed_normals(m, cl)
        except Exception as ex:
            info["custom_normal_error"] = str(ex)
            info["has_encoded_custom_normals"] = True
    m.drop_degenerate() if info.get("face_image") is None else None
    return m, info


# ======================================================================
#  modifiers
# ======================================================================
def _mod_mirror(m, mv, ctx, obW):
    flag = mv.int("flag", 0)
    axes = [i for i, b in enumerate((1 << 3, 1 << 4, 1 << 5)) if flag & b]
    if not axes:
        ax = mv.int("axis", 0)
        axes = [ax] if 0 <= ax <= 2 else [0]
    tol = mv.float("tolerance", 0.001)
    merge = not (flag & (1 << 8))
    flip_u = bool(flag & (1 << 6)); flip_v = bool(flag & (1 << 7))
    uv_off = mv.floats("uv_offset", np.zeros(2))
    mob = mv.deref("mirror_ob", "Object")
    T = np.eye(4)
    if mob is not None:
        T = np.linalg.inv(obW) @ _world_matrix(ctx, mob)
    Ti = np.linalg.inv(T)
    for ax in axes:
        S = np.eye(4); S[ax, ax] = -1.0
        Mx = T @ S @ Ti
        V = m.V
        Vm = V @ Mx[:3, :3].T + Mx[:3, 3]
        nv = len(V)
        remap = np.arange(nv) + nv
        if merge:
            loc = (np.c_[V, np.ones(nv)] @ Ti.T)[:, ax]
            on_plane = np.abs(loc) <= tol
            remap[on_plane] = np.nonzero(on_plane)[0]
        # mirrored faces: reversed winding
        sizes = m.face_sizes()
        starts = m.face_off[:-1]
        rev = np.concatenate([np.arange(s, s + k)[::-1] for s, k in zip(starts, sizes)]) if len(sizes) else np.zeros(0, np.int64)
        new_cv = remap[m.corner_vert[rev]]
        m2 = PolyMesh(np.concatenate([V, Vm]), np.concatenate([m.face_off, m.face_off[1:] + m.face_off[-1]]),
                      np.concatenate([m.corner_vert, new_cv]),
                      np.concatenate([m.face_mat, m.face_mat]),
                      np.concatenate([m.face_smooth, m.face_smooth]), m.name)
        for a in m.corner_attrs():
            arr = getattr(m, a)
            mirrored = arr[rev].copy()
            if a in ("uv0", "uv1"):
                if flip_u:
                    mirrored[:, 0] = 1.0 - mirrored[:, 0] + uv_off[0]
                if flip_v:
                    mirrored[:, 1] = 1.0 - mirrored[:, 1] + uv_off[1]
            if a == "cnormal":
                mirrored = mirrored @ Mx[:3, :3].T
            setattr(m2, a, np.concatenate([arr, mirrored]))
        if m.sharp_edges is not None:
            se2 = np.sort(remap[m.sharp_edges], 1)
            m2.sharp_edges = np.concatenate([m.sharp_edges, se2])
        m2.auto_smooth = m.auto_smooth
        m = m2.drop_degenerate()
        if merge:
            m.remove_duplicate_faces()
            used = np.zeros(len(m.V), bool); used[m.corner_vert] = True
            if not used.all():
                newidx = np.cumsum(used) - 1
                m.V = m.V[used]; m.corner_vert = newidx[m.corner_vert]
                if m.sharp_edges is not None:
                    keep = used[m.sharp_edges].all(1)
                    m.sharp_edges = newidx[m.sharp_edges[keep]]
    return m


def _mod_array(m, mv, ctx, obW):
    count = max(1, mv.int("count", 2))
    otype = mv.int("offset_type", 2)
    off = np.zeros(3)
    lo, hi = m.V.min(0), m.V.max(0)
    if otype & 2:
        off += np.asarray(mv.floats("scale", np.array([1.0, 0, 0]))) * (hi - lo)
    if otype & 1:
        off += np.asarray(mv.floats("offset", np.zeros(3)))
    step = np.eye(4); step[:3, 3] = off
    if otype & 4:
        oob = mv.deref("offset_ob", "Object")
        if oob is not None:
            step = step @ (np.linalg.inv(obW) @ _world_matrix(ctx, oob))
    fit = mv.int("fit_type", 0)
    if fit == 1:
        length = mv.float("length", 0.0)
        d = np.linalg.norm(step[:3, 3])
        if d > 1e-9:
            count = max(1, int(length / d + 1e-6) + 1)
    if count > 4096:
        count = 4096
    Vs, fo, cv, fm, fs = [], [m.face_off[:1]], [], [], []
    se = []
    attrs = {a: [] for a in m.corner_attrs()}
    M = np.eye(4)
    nv = len(m.V); ncor = m.nc
    uv_off = mv.floats("uv_offset", np.zeros(2))
    for k in range(count):
        Vs.append(m.V @ M[:3, :3].T + M[:3, 3])
        fo.append(m.face_off[1:] + k * ncor)
        cv.append(m.corner_vert + k * nv)
        fm.append(m.face_mat); fs.append(m.face_smooth)
        if m.sharp_edges is not None:
            se.append(m.sharp_edges + k * nv)
        for a in attrs:
            arr = getattr(m, a)
            if a in ("uv0", "uv1") and np.any(uv_off):
                arr = arr + uv_off * k
            elif a == "cnormal":
                arr = arr @ M[:3, :3].T
            attrs[a].append(arr)
        M = step @ M
    m2 = PolyMesh(np.concatenate(Vs), np.concatenate(fo), np.concatenate(cv),
                  np.concatenate(fm), np.concatenate(fs), m.name)
    for a, lst in attrs.items():
        setattr(m2, a, np.concatenate(lst))
    m2.sharp_edges = np.concatenate(se) if se else None
    m2.auto_smooth = m.auto_smooth
    return m2


def _vertex_normals(m):
    fn = face_normals(m)
    w = corner_angles(m)[:, None] * fn[m.corner_face()]
    vn = np.zeros_like(m.V)
    np.add.at(vn, m.corner_vert, w)
    ln = np.linalg.norm(vn, axis=1, keepdims=True)
    return vn / np.maximum(ln, 1e-20)


def _mod_solidify(m, mv, ctx, obW):
    if mv.int("mode", 0) != 0:
        ctx.warn_once("solid_nm", ".blend: Solidify 'Complex' mode evaluated as 'Simple'")
    t = mv.float("offset", 0.01)
    f = mv.float("offset_fac", -1.0)
    flag = mv.int("flag", 1)
    rim = bool(flag & 1)
    if flag & (1 << 5):          # flip normals
        t = -t
    vn = _vertex_normals(m)
    outer = (1.0 + f) * 0.5 * t
    inner = -(1.0 - f) * 0.5 * t
    nv = len(m.V)
    Vo = m.V + vn * outer
    Vi = m.V + vn * inner
    sizes = m.face_sizes(); starts = m.face_off[:-1]
    rev = np.concatenate([np.arange(s, s + k)[::-1] for s, k in zip(starts, sizes)])
    face_off = np.concatenate([m.face_off, m.face_off[1:] + m.nc])
    cvert = np.concatenate([m.corner_vert, m.corner_vert[rev] + nv])
    fmat = np.concatenate([m.face_mat, m.face_mat])
    fsm = np.concatenate([m.face_smooth, m.face_smooth])
    attrs = {a: [getattr(m, a), getattr(m, a)[rev]] for a in m.corner_attrs()}
    if "cnormal" in attrs:
        attrs["cnormal"][1] = -attrs["cnormal"][1]
    if rim:
        edges, corner_edge, cnt = build_edges(m)
        bnd = np.nonzero(cnt[corner_edge] == 1)[0]          # boundary corners
        nxt = m.corner_next()
        a_ = m.corner_vert[bnd]; b_ = m.corner_vert[nxt[bnd]]
        k = len(bnd)
        if k:
            rim_cv = np.stack([b_, a_, a_ + nv, b_ + nv], 1).ravel()
            face_off = np.concatenate([face_off, face_off[-1] + 4 * np.arange(1, k + 1)])
            cvert = np.concatenate([cvert, rim_cv])
            cf = m.corner_face()[bnd]
            fmat = np.concatenate([fmat, m.face_mat[cf]])
            fsm = np.concatenate([fsm, np.zeros(k, bool)])
            for a in attrs:
                src = getattr(m, a)
                quad = np.stack([src[nxt[bnd]], src[bnd], src[bnd], src[nxt[bnd]]], 1).reshape(-1, src.shape[1])
                attrs[a].append(quad)
    m2 = PolyMesh(np.concatenate([Vo, Vi]), face_off, cvert, fmat, fsm, m.name)
    for a, lst in attrs.items():
        setattr(m2, a, np.concatenate(lst))
    if m.sharp_edges is not None:
        m2.sharp_edges = np.concatenate([m.sharp_edges, m.sharp_edges + nv])
    m2.auto_smooth = m.auto_smooth
    return m2


def _subdivide(m, smooth=True):
    """One Catmull-Clark (or simple) step on a polygon mesh."""
    nv, nf, nc = len(m.V), m.nf, m.nc
    sizes = m.face_sizes()
    cf = m.corner_face()
    nxt = m.corner_next(); prv = m.corner_prev()
    edges, corner_edge, ecount = build_edges(m)
    ne = len(edges)
    fp = np.zeros((nf, 3)); np.add.at(fp, cf, m.V[m.corner_vert]); fp /= sizes[:, None]
    mid = 0.5 * (m.V[edges[:, 0]] + m.V[edges[:, 1]])
    if smooth:
        efs = np.zeros((ne, 3)); np.add.at(efs, corner_edge, fp[cf])
        interior = ecount == 2            # non-manifold edges act as creases
        ep = mid.copy()
        ep[interior] = 0.5 * mid[interior] + 0.5 * (efs[interior] / ecount[interior, None])
        # vertex points
        val = np.bincount(edges.ravel(), minlength=nv).astype(np.float64)       # edge valence
        bnd_e = ecount != 2
        nonman_e = ecount > 2
        F = np.zeros((nv, 3)); fcount = np.bincount(m.corner_vert, minlength=nv).astype(np.float64)
        np.add.at(F, m.corner_vert, fp[cf])
        R = np.zeros((nv, 3)); np.add.at(R, edges[:, 0], mid); np.add.at(R, edges[:, 1], mid)
        n = np.maximum(val, 1.0)
        Fav = F / np.maximum(fcount, 1.0)[:, None]
        Rav = R / n[:, None]
        newV = (Fav + 2.0 * Rav + (n - 3.0)[:, None] * m.V) / n[:, None]
        # boundary vertices: (e1 + 6v + e2)/8 on boundary edges
        bcount = np.bincount(edges[bnd_e].ravel(), minlength=nv)
        bsum = np.zeros((nv, 3))
        be = edges[bnd_e]
        np.add.at(bsum, be[:, 0], m.V[be[:, 1]]); np.add.at(bsum, be[:, 1], m.V[be[:, 0]])
        isb = bcount == 2
        newV[isb] = (bsum[isb] + 6.0 * m.V[isb]) / 8.0
        # boundary corners / isolated verts: keep in place (non-manifold
        # vertices use the general interior rule, like OpenSubdiv)
        keep = (bcount > 2) | (fcount == 0)
        newV[keep] = m.V[keep]
        # boundary-to-boundary rule for isolated verts
        loose = (bcount == 1)
        newV[loose] = m.V[loose]
    else:
        ep = mid
        newV = m.V.copy()
    # new vertex layout: [verts | edge points | face points]
    V2 = np.concatenate([newV, ep, fp])
    E0, F0 = nv, nv + ne
    c = np.arange(nc)
    q = np.stack([m.corner_vert, E0 + corner_edge, F0 + cf, E0 + corner_edge[prv]], 1)
    cv2 = q.ravel()
    face_off2 = np.arange(0, 4 * nc + 1, 4, dtype=np.int64)
    m2 = PolyMesh(V2, face_off2, cv2, m.face_mat[cf], m.face_smooth[cf], m.name)
    for a in m.corner_attrs():
        arr = getattr(m, a)
        fa = np.zeros((nf, arr.shape[1])); np.add.at(fa, cf, arr); fa /= sizes[:, None]
        e_next = 0.5 * (arr + arr[nxt])
        e_prev = 0.5 * (arr + arr[prv])
        quad = np.stack([arr, e_next, fa[cf], e_prev], 1).reshape(-1, arr.shape[1])
        if a == "cnormal":
            quad = quad / np.maximum(np.linalg.norm(quad, axis=1, keepdims=True), 1e-20)
        setattr(m2, a, quad)
    if m.sharp_edges is not None and len(m.sharp_edges):
        ek = edge_keys(edges[:, 0], edges[:, 1], nv)
        sk = edge_keys(m.sharp_edges[:, 0], m.sharp_edges[:, 1], nv)
        idx = np.nonzero(np.isin(ek, sk))[0]
        m2.sharp_edges = np.concatenate([np.sort(np.stack([edges[idx, 0], E0 + idx], 1), 1),
                                         np.sort(np.stack([edges[idx, 1], E0 + idx], 1), 1)])
    m2.auto_smooth = m.auto_smooth
    return m2


def _limit_positions(m):
    """Push a quad mesh's vertices to the Catmull-Clark limit surface
    (Blender 'use_limit_surface', on by default)."""
    nv = len(m.V)
    edges, corner_edge, ecount = build_edges(m)
    val = np.bincount(edges.ravel(), minlength=nv).astype(np.float64)
    E = np.zeros((nv, 3)); np.add.at(E, edges[:, 0], m.V[edges[:, 1]]); np.add.at(E, edges[:, 1], m.V[edges[:, 0]])
    if not (m.face_sizes() == 4).all():
        return m
    nxt = m.corner_next()
    opp = m.corner_vert[nxt[nxt]]
    D = np.zeros((nv, 3)); np.add.at(D, m.corner_vert, m.V[opp])
    n = val
    interior = np.ones(nv, bool)
    bnd_e = ecount != 2
    bv = np.zeros(nv, bool); bv[edges[bnd_e].ravel()] = True
    interior &= ~bv
    out = m.V.copy()
    ni = n[interior]
    out[interior] = (ni[:, None] ** 2 * m.V[interior] + 4.0 * E[interior] + D[interior]) / (ni * (ni + 5.0))[:, None]
    bcount = np.bincount(edges[ecount != 2].ravel(), minlength=nv)
    bs = np.zeros((nv, 3)); be = edges[ecount != 2]
    np.add.at(bs, be[:, 0], m.V[be[:, 1]]); np.add.at(bs, be[:, 1], m.V[be[:, 0]])
    ib = bcount == 2
    out[ib] = (bs[ib] + 4.0 * m.V[ib]) / 6.0
    m.V = out
    return m


def _mod_subsurf(m, mv, ctx, obW, levels_field="levels"):
    lv = mv.int(levels_field, 1)
    if mv.sname == "MultiresModifierData":
        lv = mv.int("lvl", 1)
        ctx.warn_once("multires", ".blend: Multires displacement can't be read; "
                      "the base mesh is subdivided smoothly instead")
    simple = mv.int("subdivType", 0) == 1
    if lv <= 0:
        return m
    lv = min(lv, 6)
    for _ in range(lv):
        if m.nc * 4 > 12_000_000:
            ctx.warn_once("subsurf_cap", ".blend: subdivision capped to keep the mesh "
                          "under ~12 million corners")
            break
        m = _subdivide(m, smooth=not simple)
    flags = mv.int("flags", 0)
    # use_limit_surface is stored INVERTED as UseRecursiveSubdivision (1<<6)
    use_limit = not (flags & (1 << 6)) if ctx.bf.version >= 290 else LEGACY_SUBSURF_LIMIT
    if not simple and use_limit:
        m = _limit_positions(m)
    return m


def _mod_weld(m, mv, ctx, obW):
    dist = mv.float("merge_dist", 0.001)
    if dist <= 0:
        return m
    q = np.round(m.V / max(dist, 1e-9)).astype(np.int64)
    _, first, inv = np.unique(q, axis=0, return_index=True, return_inverse=True)
    inv = inv.ravel()
    m.V = m.V[first]
    m.corner_vert = inv[m.corner_vert]
    if m.sharp_edges is not None:
        m.sharp_edges = np.sort(inv[m.sharp_edges], 1)
    return m.drop_degenerate()


# ---- Simple Deform (port of Blender's MOD_simpledeform) ----
_SD_AXIS_MAP = ((1, 2, 0), (2, 0, 1), (0, 1, 2))


def _mod_simpledeform(m, mv, ctx, obW):
    if mv.ptr("origin"):
        ctx.warn_once("sdorigin", ".blend: a Simple Deform modifier uses an Origin object — "
                      "deformed around the object's own origin")
    if mv.str("vgroup_name"):
        ctx.warn_once("sdvg", ".blend: a Simple Deform modifier is limited to a vertex group — "
                      "applied to the whole mesh")
    mode = mv.int("mode", 1)
    dax = int(np.clip(mv.int("deform_axis", 2), 0, 2))
    lock = 0 if mode == 2 else mv.int("axis", 0) & ~(1 << dax)
    lim = mv.floats("limit", np.array([0.0, 1.0]))
    lax = dax if mode != 2 else (2 if dax in (0, 1) else 0)
    V = m.V
    lo, hi = float(V[:, lax].min()), float(V[:, lax].max())
    l1 = lo + (hi - lo) * float(lim[1])
    l0 = lo + (hi - lo) * float(lim[0])
    f = mv.float("factor", 0.0) / max(np.finfo(np.float32).eps, l1 - l0)
    if mode == 2 and abs(f) < 1e-6:
        return m
    co = V.copy()
    dcut = np.zeros_like(co)
    for ax in range(3):
        if lock & (1 << ax):
            dcut[:, ax] = co[:, ax]
            co[:, ax] = 0.0
    val = np.clip(co[:, lax], l0, l1)
    dcut[:, lax] = co[:, lax] - val
    co[:, lax] = val
    amap = _SD_AXIS_MAP[dax if mode != 2 else 2]
    c = co[:, amap]
    dc = dcut[:, amap]
    x, y, z = c[:, 0], c[:, 1], c[:, 2]
    r = np.empty_like(c)
    if mode == 1:                                  # twist
        th = z * f
        r[:, 0] = x * np.cos(th) - y * np.sin(th)
        r[:, 1] = x * np.sin(th) + y * np.cos(th)
        r[:, 2] = z
        r += dc
    elif mode == 3:                                # taper
        sc = z * f
        r[:, 0] = x + x * sc
        r[:, 1] = y + y * sc
        r[:, 2] = z
        r += dc
    elif mode == 4:                                # stretch
        sc = z * z * f - f + 1.0
        r[:, 0] = x * sc
        r[:, 1] = y * sc
        r[:, 2] = z * (1.0 + f)
        r += dc
    elif mode == 2:                                # bend
        th = (z if dax in (0, 1) else x) * f
        st, ct = np.sin(th), np.cos(th)
        k = 1.0 / f
        if dax == 0:
            r[:, 0] = x + dc[:, 0]
            r[:, 1] = (y - k) * ct + k + st * dc[:, 2]
            r[:, 2] = -(y - k) * st + ct * dc[:, 2]
        elif dax == 1:
            r[:, 0] = (x - k) * ct + k + st * dc[:, 2]
            r[:, 1] = y + dc[:, 1]
            r[:, 2] = -(x - k) * st + ct * dc[:, 2]
        else:
            r[:, 0] = -(y - k) * st + ct * dc[:, 0]
            r[:, 1] = (y - k) * ct + k + st * dc[:, 0]
            r[:, 2] = z + dc[:, 2]
    else:
        return m
    out = np.empty_like(r)
    out[:, list(amap)] = r
    m.V = out
    return m


def _mesh_edges(m):
    nxt = np.arange(len(m.corner_vert)) + 1
    ends = m.face_off[1:] - 1
    nxt[ends] = m.face_off[:-1]
    e = np.stack([m.corner_vert, m.corner_vert[nxt]], 1)
    e.sort(1)
    return np.unique(e, axis=0)


def _mod_displace(m, mv, ctx, obW):
    if mv.ptr("texture"):
        ctx.warn_once("disptex", ".blend: a Displace modifier uses a texture — not evaluated "
                      "(apply it in Blender with Ctrl+A for an exact match)")
        return m
    if mv.str("defgrp_name"):
        ctx.warn_once("dispvg", ".blend: a Displace modifier is limited to a vertex group — "
                      "applied to the whole mesh")
    delta = (1.0 - mv.float("midlevel", 0.5)) * mv.float("strength", 1.0)
    d = mv.int("direction", 3)
    if d == 3 or d == 5:                           # normal / custom normal
        m.V = m.V + delta * _vertex_normals(m)
    elif d in (0, 1, 2):
        if mv.int("space", 0) == 1:                # global axis, in object space
            inv = np.linalg.inv(np.asarray(obW, float))
            m.V = m.V + delta * inv[:3, d][None]
        else:
            m.V = m.V.copy()
            m.V[:, d] += delta
    else:                                          # RGB to XYZ: texture colour 1,1,1
        m.V = m.V + delta
    return m


def _mod_smooth(m, mv, ctx, obW):
    if mv.str("defgrp_name"):
        ctx.warn_once("smvg", ".blend: a Smooth modifier is limited to a vertex group — "
                      "applied to the whole mesh")
    fac = mv.float("fac", 0.5)
    rep = mv.int("repeat", 1)
    fl = mv.int("flag", 14)
    axes = [a for a in range(3) if fl & (2 << a)]
    if not axes or fac == 0 or rep <= 0:
        return m
    e = _mesh_edges(m)
    V = m.V.copy()
    nv = len(V)
    cnt = np.bincount(e.ravel(), minlength=nv).astype(float)
    has = cnt > 0
    for _ in range(int(rep)):
        mid = (V[e[:, 0]] + V[e[:, 1]]) * 0.5
        acc = np.zeros_like(V)
        np.add.at(acc, e[:, 0], mid)
        np.add.at(acc, e[:, 1], mid)
        avg = acc[has] / cnt[has, None]
        for a in axes:
            V[has, a] = V[has, a] * (1.0 - fac) + avg[:, a] * fac
    m.V = V
    return m


def _mod_edgesplit(m, mv, ctx, obW):
    flags = mv.int("flags", 0b110)
    if flags & (1 << 1):
        ang = mv.float("split_angle", math.radians(30))
        m.auto_smooth = ang if m.auto_smooth is None else min(ang, m.auto_smooth)
        m.face_smooth = np.ones(m.nf, bool) if not m.face_smooth.any() else m.face_smooth
    return m


def _mod_nodes(m, mv, ctx, obW):
    grp = mv.deref("node_group")
    name = grp.id_name() if grp is not None else ""
    if name.lower().startswith("smooth by angle") or name.lower().startswith("auto smooth"):
        m.auto_smooth = math.radians(30.0)
        ang = _idprop_float(ctx, mv.deref("settings") if False else None)
        settings = mv.sub("settings")
        if settings is not None:
            val = _idprops_find_float(ctx, settings.deref("properties", "IDProperty"),
                                      ("Socket_1", "Input_1"))
            if val is not None:
                m.auto_smooth = float(val)
        if not m.face_smooth.any():
            m.face_smooth[:] = True
        return m
    ctx.warn_once("gn_" + name, f".blend: Geometry Nodes modifier '{name}' can't be "
                  f"evaluated without Blender — apply it in Blender (Ctrl+A)")
    return m


def _idprop_float(ctx, v):
    return None


def _idprops_find_float(ctx, group, names):
    if group is None:
        return None
    data = group.sub("data")
    if data is None:
        return None
    for p in ctx.bf.listbase(data.sub("group"), "IDProperty"):
        if p.str("name") in names:
            t = p.int("type", -1)
            d = p.sub("data")
            if t == 2:        # IDP_FLOAT stored in data.val as int bits
                raw = struct.pack(ctx.bf.endian + "i", d.int("val"))
                return struct.unpack(ctx.bf.endian + "f", raw)[0]
            if t == 8:        # IDP_DOUBLE
                v1, v2 = d.int("val") & 0xFFFFFFFF, d.int("val2") & 0xFFFFFFFF
                raw = struct.pack(ctx.bf.endian + "II", *((v1, v2) if ctx.bf.endian == "<" else (v2, v1)))
                return struct.unpack(ctx.bf.endian + "d", raw)[0]
    return None


_MODIFIERS = {
    "MirrorModifierData": _mod_mirror,
    "ArrayModifierData": _mod_array,
    "SubsurfModifierData": _mod_subsurf,
    "MultiresModifierData": _mod_subsurf,
    "SolidifyModifierData": _mod_solidify,
    "WeldModifierData": _mod_weld,
    "EdgeSplitModifierData": _mod_edgesplit,
    "NodesModifierData": _mod_nodes,
    "SimpleDeformModifierData": _mod_simpledeform,
    "DisplaceModifierData": _mod_displace,
    "SmoothModifierData": _mod_smooth,
    "TriangulateModifierData": None,          # geometry identical once triangulated
    "ArmatureModifierData": None,             # rest pose (game engines skin later)
    "WeightedNormalModifierData": None,
}


# ======================================================================
#  curves & surfaces (Curve object data -> PolyMesh, like Blender's own
#  "Convert to Mesh": Bezier / Poly / NURBS splines, bevel depth +
#  resolution, extrude, fill caps, radius, tilt, 2D fills, NURBS surfaces)
# ======================================================================
CU_3D, CU_FRONT, CU_BACK, CU_FILL_CAPS = 1, 2, 4, 1 << 14
CU_POLY, CU_BEZIER, CU_NURBS = 0, 1, 4
CU_NURB_CYCLIC, CU_NURB_ENDPOINT, CU_NURB_BEZIER = 1, 2, 4


def _nurb_knots(pnts, order, flag):
    """Blender's calcknots()."""
    cyc = bool(flag & CU_NURB_CYCLIC)
    bez = bool(flag & CU_NURB_BEZIER)
    endp = bool(flag & CU_NURB_ENDPOINT)
    rep_inner = order - 1 if bez else 1
    head = (order - (1 if cyc else 0)) if endp else (min(2, rep_inner) if bez else 1)
    tail = 2 * order - 1 if cyc else (order if endp else 0)
    n = pnts + order + (order - 1 if cyc else 0)
    kn = np.zeros(n)
    r, cur = head, 0.0
    offset = 1 if (endp and cyc) else 0
    if offset:
        kn[0] = cur
        cur += 1.0
    for i in range(offset, n - tail):
        kn[i] = cur
        r -= 1
        if r == 0:
            cur += 1.0
            r = rep_inner
    ti = n - tail
    for i in range(tail):
        kn[ti + i] = cur + (kn[i] - kn[0])
    return kn


def _nurb_basis(kn, order, us):
    """Cox-de Boor basis, (len(us), len(kn) - order)."""
    nb = len(kn) - 1
    u = np.asarray(us, float)[:, None]
    N = ((kn[:-1][None] <= u) & (u < kn[1:][None])).astype(float)
    # the end of the range belongs to the last non-empty span
    last = max(i for i in range(nb) if kn[i] < kn[i + 1]) if np.any(kn[:-1] < kn[1:]) else nb - 1
    at_end = u[:, 0] >= kn[last + 1]
    N[at_end] = 0.0
    N[at_end, last] = 1.0
    for p in range(1, order):
        cnt = nb - p
        a = kn[p:p + cnt] - kn[:cnt]
        b = kn[p + 1:p + 1 + cnt] - kn[1:1 + cnt]
        left = np.where(a > 0, (u - kn[:cnt][None]) / np.where(a > 0, a, 1), 0.0) * N[:, :cnt]
        right = np.where(b > 0, (kn[p + 1:p + 1 + cnt][None] - u) / np.where(b > 0, b, 1), 0.0) * N[:, 1:cnt + 1]
        N = left + right
    return N[:, :len(kn) - order]


def _nurb_samples(pnts, order, flag, count):
    cyc = bool(flag & CU_NURB_CYCLIC)
    order = int(max(2, min(order, pnts + (order - 1 if cyc else 0))))
    kn = _nurb_knots(pnts, order, flag)
    us_ = kn[order - 1]
    ue = kn[pnts + order - 1] if cyc else kn[pnts]
    steps = count if cyc else max(count - 1, 1)
    us = us_ + (ue - us_) * np.arange(count) / steps
    B = _nurb_basis(kn, order, us)
    idx = np.arange(B.shape[1]) % pnts          # cyclic splines wrap their points
    return B, idx


def _bez_segment(p0, p1, p2, p3, res):
    t = np.arange(res)[:, None] / float(res)
    mt = 1.0 - t
    return mt ** 3 * p0 + 3 * mt * mt * t * p1 + 3 * mt * t * t * p2 + t ** 3 * p3


def _bez_tangent(p0, p1, p2, p3, res):
    t = np.arange(res)[:, None] / float(res)
    mt = 1.0 - t
    return mt * mt * (p1 - p0) + 2 * mt * t * (p2 - p1) + t * t * (p3 - p2)


def _spline_path(ctx, nu, cu_res):
    """-> (points (n,3), radius (n,), tilt (n,), cyclic) of one curve spline."""
    bf = ctx.bf
    typ = nu.int("type", 0)
    n = nu.int("pntsu", 0)
    cyc = bool(nu.int("flagu", 0) & CU_NURB_CYCLIC)
    res = max(1, nu.int("resolu", cu_res) or cu_res)
    if typ == CU_BEZIER:
        rec = bf.struct_records(nu.ptr("bezt"), "BezTriple", n, {"vec": "f4", "radius": "f4", "alfa": "f4"}, ctx=nu)
        if not rec or "vec" not in rec or n < 1:
            return None
        vec = np.asarray(rec["vec"], float).reshape(n, 3, 3)
        rad = np.asarray(rec.get("radius", np.ones(n)), float).reshape(n)
        tilt = np.asarray(rec.get("alfa", np.zeros(n)), float).reshape(n)
        pts, rr, tt, tg = [], [], [], []
        segs = n if cyc else n - 1
        for i in range(segs):
            j = (i + 1) % n
            pts.append(_bez_segment(vec[i, 1], vec[i, 2], vec[j, 0], vec[j, 1], res))
            tg.append(_bez_tangent(vec[i, 1], vec[i, 2], vec[j, 0], vec[j, 1], res))
            f = np.arange(res) / float(res)
            rr.append(rad[i] + (rad[j] - rad[i]) * f)
            tt.append(tilt[i] + (tilt[j] - tilt[i]) * f)
        if not cyc:
            pts.append(vec[n - 1:n, 1]); rr.append(rad[n - 1:n]); tt.append(tilt[n - 1:n])
            tg.append(vec[n - 1:n, 1] - vec[n - 1:n, 0])
        if not pts:
            return None
        tg = np.concatenate(tg)
        P = np.concatenate(pts)
        # a zero-length handle has no derivative: fall back to the chord
        bad = np.linalg.norm(tg, axis=1) < 1e-9
        if np.any(bad):
            ch = np.gradient(P, axis=0) if len(P) > 1 else np.zeros_like(P)
            tg[bad] = ch[bad]
        return P, np.concatenate(rr), np.concatenate(tt), cyc, tg
    rec = bf.struct_records(nu.ptr("bp"), "BPoint", n, {"vec": "f4", "radius": "f4", "alfa": "f4"}, ctx=nu)
    if not rec or "vec" not in rec or n < 1:
        return None
    P = np.asarray(rec["vec"], float).reshape(n, 4)
    rad = np.asarray(rec.get("radius", np.ones(n)), float).reshape(n)
    tilt = np.asarray(rec.get("alfa", np.zeros(n)), float).reshape(n)
    if typ == CU_POLY:
        return P[:, :3].copy(), rad, tilt, cyc, None
    order = nu.int("orderu", 4)
    count = res * (n if cyc else max(n - 1, 1))
    B, idx = _nurb_samples(n, order, nu.int("flagu", 0), count)
    W = B * P[idx, 3][None]
    den = W.sum(1, keepdims=True)
    den[den == 0] = 1.0
    pts = (W @ P[idx, :3]) / den
    return pts, (W @ rad[idx]) / den[:, 0], (W @ tilt[idx]) / den[:, 0], cyc, None


def _unit_rows(a):
    ln = np.linalg.norm(a, axis=1, keepdims=True)
    return a / np.where(ln > 1e-12, ln, 1.0)


def _path_frames(P, cyc, flat2d, twist_mode, T=None):
    n = len(P)
    if n < 2:
        return None
    # like Blender (bevel_list_calc_bisect): inner points use the bisector of
    # the two neighbouring segments; open ends keep the spline's own tangent
    nxt = _unit_rows(np.roll(P, -1, 0) - P)
    prv = _unit_rows(P - np.roll(P, 1, 0))
    Tb = nxt + prv
    if cyc:
        T = Tb
    else:
        T0 = T
        T = Tb
        T[0] = nxt[0] if T0 is None else T0[0]
        T[-1] = prv[-1] if T0 is None else T0[-1]
    if flat2d:
        T = T.copy()
        T[:, 2] = 0.0
    ln = np.linalg.norm(T, axis=1, keepdims=True)
    T = T / np.where(ln > 1e-12, ln, 1.0)
    Z = np.array([0.0, 0.0, 1.0])
    if flat2d:
        N = np.tile(Z, (n, 1))
    elif twist_mode == 0:                      # Z-Up
        N = Z[None] - (T @ Z)[:, None] * T
        bad = np.linalg.norm(N, axis=1) < 1e-6
        N[bad] = np.array([0.0, 1.0, 0.0])
        N /= np.linalg.norm(N, axis=1, keepdims=True)
    else:                                       # minimum twist (parallel transport)
        N = np.empty_like(P)
        n0 = Z - (T[0] @ Z) * T[0]
        if np.linalg.norm(n0) < 1e-6:
            n0 = np.array([0.0, 1.0, 0.0]) - T[0][1] * T[0]
        N[0] = n0 / np.linalg.norm(n0)
        for i in range(1, n):
            v1 = P[i] - P[i - 1]
            c1 = v1 @ v1
            if c1 < 1e-18:
                N[i] = N[i - 1]
                continue
            rL = N[i - 1] - (2.0 / c1) * (v1 @ N[i - 1]) * v1
            tL = T[i - 1] - (2.0 / c1) * (v1 @ T[i - 1]) * v1
            v2 = T[i] - tL
            c2 = v2 @ v2
            ni = rL - (2.0 / c2) * (v2 @ rL) * v2 if c2 > 1e-18 else rL
            ni = ni - (ni @ T[i]) * T[i]
            N[i] = ni / max(np.linalg.norm(ni), 1e-12)
        if cyc and n > 2:                       # spread the closing mismatch along the loop
            v1 = P[0] - P[-1]
            c1 = v1 @ v1
            back = N[-1] - (2.0 / c1) * (v1 @ N[-1]) * v1 if c1 > 1e-18 else N[-1]
            back = back - (back @ T[0]) * T[0]
            ang = math.atan2(np.cross(back, N[0]) @ T[0], back @ N[0])
            for i in range(n):
                a = ang * i / n
                B_ = np.cross(T[i], N[i])
                N[i] = N[i] * math.cos(a) + B_ * math.sin(a)
    B = np.cross(T, N)
    return T, N, B


def _bevel_profile(d, e, res):
    """(k,2) side/up points and whether the profile is closed."""
    res = max(0, int(res))
    if d > 0 and e > 0:
        q = res + 1
        pts = []
        for k in range(q + 1):                   # lower-left quarter, centred (0,-e)
            a = -math.pi / 2 - k * (math.pi / 2) / q
            pts.append((d * math.cos(a), -e + d * math.sin(a)))
        for k in range(q + 1):                   # upper-left
            a = math.pi - k * (math.pi / 2) / q
            pts.append((d * math.cos(a), e + d * math.sin(a)))
        for k in range(1, q + 1):                # upper-right
            a = math.pi / 2 - k * (math.pi / 2) / q
            pts.append((d * math.cos(a), e + d * math.sin(a)))
        for k in range(q):                       # lower-right
            a = -k * (math.pi / 2) / q
            pts.append((d * math.cos(a), -e + d * math.sin(a)))
        return np.array(pts), True
    if d > 0:
        nr = 4 + 2 * res
        a = math.pi - (np.arange(nr) + 1) * 2 * math.pi / nr
        return np.stack([d * np.cos(a), d * np.sin(a)], 1), True
    if e > 0:
        return np.array([[0.0, -e], [0.0, e]]), False
    return None, False


def _polygon_area2d(p):
    x, y = p[:, 0], p[:, 1]
    return 0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y))


def _decode_curve(ctx, cu, oname):
    """Curve / Surface data -> (PolyMesh, info) like Blender's evaluated mesh."""
    bf = ctx.bf
    flag = cu.int("flag", CU_3D)
    flat2d = not (flag & CU_3D)
    e = abs(cu.float("ext1", 0.0))
    d = abs(cu.float("ext2", 0.0))
    res_b = cu.int("bevresol", 4)
    cu_res = max(1, cu.int("resolu", 12))
    twist = cu.int("twist_mode", 3)
    offset = cu.float("width", 1.0) - 1.0
    if cu.int("bevel_mode", 0) != 0 or cu.ptr("bevobj") or cu.ptr("taperobj"):
        ctx.warn_once("cubev_" + oname, f".blend: '{oname}' uses a bevel object / custom profile / taper — "
                      f"imported with a round bevel")
    prof, closed = _bevel_profile(d, e, res_b)
    V, faces, fmat, fsm = [], [], [], []
    nverts = 0

    def add(verts, fcs, mat, smooth):
        nonlocal nverts
        V.append(verts)
        for f in fcs:
            faces.append([nverts + i for i in f])
            fmat.append(mat)
            fsm.append(smooth)
        nverts += len(verts)
    fills = []
    for nu in bf.listbase(cu.sub("nurb"), "Nurb"):
        mat = nu.int("mat_nr", 0)
        smooth = bool(nu.int("flag", 0) & 1)
        if nu.int("pntsv", 1) > 1:                    # NURBS surface
            pu, pv = nu.int("pntsu", 0), nu.int("pntsv", 0)
            rec = bf.struct_records(nu.ptr("bp"), "BPoint", pu * pv, {"vec": "f4"}, ctx=nu)
            if not rec or "vec" not in rec:
                continue
            P = np.asarray(rec["vec"], float).reshape(pv, pu, 4)
            fu, fv = nu.int("flagu", 0), nu.int("flagv", 0)
            cu_, cv_ = bool(fu & CU_NURB_CYCLIC), bool(fv & CU_NURB_CYCLIC)
            nsu = max(2, nu.int("resolu", 4) * pu)
            nsv = max(2, nu.int("resolv", 4) * pv)
            Bu, iu = _nurb_samples(pu, nu.int("orderu", 4), fu, nsu)
            Bv, iv = _nurb_samples(pv, nu.int("orderv", 4), fv, nsv)
            Pw = P[iv][:, iu]                          # (cv, cu, 4)
            w = Pw[..., 3]
            num = np.einsum("su,tv,vuc->tsc", Bu, Bv, Pw[..., :3] * w[..., None])
            den = np.einsum("su,tv,vu->ts", Bu, Bv, w)
            den[den == 0] = 1.0
            G = num / den[..., None]                   # (nsv, nsu, 3)
            idx = np.arange(nsv * nsu).reshape(nsv, nsu)
            fcs = []
            for j in range(nsv if cv_ else nsv - 1):
                for i in range(nsu if cu_ else nsu - 1):
                    j2, i2 = (j + 1) % nsv, (i + 1) % nsu
                    fcs.append([idx[j, i], idx[j2, i], idx[j2, i2], idx[j, i2]])
            add(G.reshape(-1, 3), fcs, mat, smooth)
            continue
        path = _spline_path(ctx, nu, cu_res)
        if path is None:
            continue
        P, rad, tilt, cyc, tang = path
        if flat2d:
            P = P.copy()
            P[:, 2] = 0.0
        if prof is not None and len(P) >= 2:
            fr = _path_frames(P, cyc, flat2d, twist, tang)
            if fr is None:
                continue
            T, N, B = fr
            if np.any(tilt != 0):
                c, s = np.cos(tilt)[:, None], np.sin(tilt)[:, None]
                N, B = N * c - B * s, B * c + N * s
            if offset:
                P = P + B * offset
            k = len(prof)
            ring = (P[:, None, :] + rad[:, None, None] *
                    (prof[None, :, 0, None] * B[:, None, :] + prof[None, :, 1, None] * N[:, None, :]))
            n = len(P)
            fcs = []
            for i in range(n if cyc else n - 1):
                i2 = (i + 1) % n
                for j in range(0 if closed else 1, k):
                    jm = (j - 1) % k
                    q = [i * k + j, i2 * k + j, i2 * k + jm, i * k + jm]
                    fcs.append(q[::-1] if (flat2d and cyc) else q)  # as Blender winds closed 2D shapes
            add(ring.reshape(-1, 3), fcs, mat, smooth)
            if closed and not cyc and (flag & CU_FILL_CAPS):
                # caps are flat faces with their own vertices (a hard edge at the rim)
                add(ring[-1], [list(range(k))], mat, False)
                add(ring[0], [list(range(k - 1, -1, -1))], mat, False)
        if flat2d and cyc and (flag & (CU_FRONT | CU_BACK)) and len(P) >= 3:
            fills.append((P, mat, smooth))
    if fills:
        for P, mat, smooth in fills:
            poly = P[:, :2]
            ccw = _polygon_area2d(poly) >= 0
            loop = list(range(len(P))) if ccw else list(range(len(P) - 1, -1, -1))
            h = e + d
            if (flag & CU_FRONT) and (flag & CU_BACK) and h == 0:
                add(P, [loop], mat, False)
                continue
            if flag & CU_FRONT:
                add(P + np.array([0.0, 0.0, h]), [loop], mat, False)
            if flag & CU_BACK:
                add(P - np.array([0.0, 0.0, h]), [loop[::-1]], mat, False)
        if len(fills) > 1:
            ctx.warn_once("cuholes_" + oname, f".blend: '{oname}' has several filled shapes — "
                          f"holes are not cut out")
    if not faces:
        return None, {}
    Vall = np.concatenate(V)
    sizes = [len(f) for f in faces]
    m = PolyMesh(Vall, np.concatenate([[0], np.cumsum(sizes)]), np.concatenate(faces),
                 np.asarray(fmat), np.asarray(fsm), cu.id_name())
    return m, {"uv_names": [], "col_names": [], "face_image": None}


def _apply_shape_keys(ctx, ob, me, m, oname):
    """Relative shape keys at their current values, as Blender shows them:
    muted keys skipped, each key relative to its own reference key,
    'Shape Key Lock' (pin) shows the active key alone at full strength."""
    key = me.deref("key", "Key")
    if key is None:
        return m
    kbs = ctx.bf.listbase(key.sub("block"), "KeyBlock")
    if len(kbs) < 2:
        return m
    nv = len(m.V)
    data = []
    for kb in kbs:
        p = kb.ptr("data")
        n = kb.int("totelem", 0)
        arr = ctx.bf.array(p, "f4", n * 3, ctx=kb) if p and n == nv else None
        data.append(None if arr is None else arr.reshape(nv, 3).astype(np.float64))
    if key.int("type", 1) != 1:                       # KEY_NORMAL: absolute keys (rare)
        ctx.warn_once("abskey_" + oname, f".blend: '{oname}' uses absolute shape keys — "
                      f"imported in its basis shape")
        return m
    V = m.V.copy()
    pinned = ob.int("shapeflag", 0) & 1               # OB_SHAPE_LOCK
    active = ob.int("shapenr", 1) - 1
    changed = False
    for i, kb in enumerate(kbs):
        if i == 0 or data[i] is None:
            continue
        if pinned:
            if i != active:
                continue
            w = 1.0
        else:
            if kb.int("flag", 0) & 1:                 # KEYBLOCK_MUTE
                continue
            w = kb.float("curval", 0.0)
        if abs(w) < 1e-9:
            continue
        r = kb.int("relative", 0)
        ref = data[r] if 0 <= r < len(data) and data[r] is not None else data[0]
        if ref is None:
            continue
        if kb.str("vgroup"):
            ctx.warn_once("kbvg_" + oname, f".blend: shape key '{kb.str('name')}' on '{oname}' is limited "
                          f"to a vertex group — applied to the whole mesh")
        V += w * (data[i] - ref)
        changed = True
    if changed:
        m.V = V
    return m


def _apply_modifiers(ctx, ob, m, obW, oname):
    for md in ctx.bf.listbase(ob.sub("modifiers")):
        sname = md.blk.struct
        mv = ctx.bf.view(md.blk, sname, md.off) if sname else None
        if mv is None:
            continue
        base = mv.sub("modifier")
        mode = base.int("mode", 1) if base is not None else 1
        if not (mode & 1):          # disabled in viewport
            continue
        if sname not in _MODIFIERS:
            ctx.warn_once("mod_" + sname, f".blend: '{oname}' uses a "
                          f"{sname.replace('ModifierData', '')} modifier — not "
                          f"evaluated (apply it in Blender with Ctrl+A for an exact match)")
            continue
        fn = _MODIFIERS[sname]
        if fn is None:
            continue
        try:
            m = fn(m, mv, ctx, obW)
        except Exception as ex:
            ctx.warn_once("modfail_" + sname + oname,
                          f".blend: {sname} on '{oname}' failed ({ex}) — skipped")
    return m


# ======================================================================
#  images & materials
# ======================================================================
def _image_descriptor(ctx, img):
    if img is None:
        return None
    key = img.addr()
    if key in ctx.images:
        return ctx.images[key]
    bf = ctx.bf
    desc = None
    pf = img.deref("packedfile", "PackedFile")
    if pf is None and img.has("packedfiles"):
        for ipf in bf.listbase(img.sub("packedfiles")):
            pf = ipf.deref("packedfile", "PackedFile")
            if pf is not None:
                break
    if pf is not None:
        size = pf.int("size", 0)
        r = pf.resolve("data")
        if r is not None and size > 0:
            blk, off = r
            desc = ("bytes", bytes(blk.data[off:off + size]))
    if desc is None:
        fp = img.str("filepath") or img.str("name")
        cands = []
        if fp:
            rel = fp[2:] if fp.startswith("//") else fp
            rel = rel.replace("\\", "/")
            if fp.startswith("//") or not os.path.isabs(rel):
                cands.append(os.path.normpath(os.path.join(ctx.dir, rel)))
            else:
                cands.append(rel)
            base = os.path.basename(rel)
            for sub in ("", "textures", "Textures", "tex", "images", "../textures"):
                cands.append(os.path.normpath(os.path.join(ctx.dir, sub, base)))
        for c in cands:
            if os.path.isfile(c):
                desc = ("path", c)
                break
        if desc is None and fp:
            ctx.warn_once("img_" + fp, f".blend: texture '{fp}' not found next to "
                          f"the .blend — pack it (File > External Data > Pack Resources) "
                          f"or copy it beside the file")
    ctx.images[key] = desc
    return desc


class _NodeGraph:
    def __init__(self, ctx, ntree):
        self.ctx = ctx
        bf = ctx.bf
        self.nodes = bf.listbase(ntree.sub("nodes"), "bNode")
        self.by_addr = {n.addr(): n for n in self.nodes}
        self.links = {}
        for lk in bf.listbase(ntree.sub("links"), "bNodeLink"):
            if lk.int("flag", 0) & (1 << 3):     # NODE_LINK_MUTED? keep anyway
                pass
            tn, ts = lk.ptr("tonode"), lk.ptr("tosock")
            self.links[(tn, ts)] = (lk.ptr("fromnode"), lk.ptr("fromsock"))
        self.sockets = {}

    def idname(self, n):
        s = n.str("idname")
        if not s:
            s = {143: "ShaderNodeOutputMaterial", 193: "ShaderNodeBsdfPrincipled",
                 143: "ShaderNodeOutputMaterial"}.get(n.int("type"), "")
        return s

    def inputs(self, n):
        return self.ctx.bf.listbase(n.sub("inputs"), "bNodeSocket")

    def outputs(self, n):
        return self.ctx.bf.listbase(n.sub("outputs"), "bNodeSocket")

    def input(self, n, *names):
        ins = self.inputs(n)
        for name in names:
            for s in ins:
                if s.str("name") == name or s.str("identifier") == name:
                    return s
        return None

    def link_from(self, n, sock):
        r = self.links.get((n.addr(), sock.addr()))
        if r is None:
            return None, None
        fn = self.by_addr.get(r[0])
        if fn is None:
            return None, None
        fs = next((o for o in self.outputs(fn) if o.addr() == r[1]), None)
        return fn, fs

    def default(self, sock):
        dv = sock.deref("default_value")
        if dv is None:
            return None
        if dv.has("value"):
            f = dv.fields["value"]
            if f.type == "float":
                return dv.floats("value") if f.dims else dv.float("value")
            return dv.int("value")
        return None


def _trace(g, node, sock, depth=0):
    """Follow the link into `sock` and describe its source:
       ('const', value) | ('tex', node, channel) | ('vcol', name) | None."""
    fn, fs = g.link_from(node, sock)
    if fn is None:
        v = g.default(sock)
        return ("const", v) if v is not None else None
    if depth > 24:
        return None
    t = g.idname(fn)
    oname = fs.str("name") if fs is not None else ""
    if t == "ShaderNodeTexImage":
        return ("tex", fn, "alpha" if oname == "Alpha" else "color")
    if t in ("ShaderNodeVertexColor", "ShaderNodeAttribute"):
        name = ""
        st = fn.deref("storage")
        if st is not None:
            name = st.str("layer_name") or st.str("name")
        return ("vcol", name)
    if t in ("ShaderNodeMix", "ShaderNodeMixRGB"):
        cand = []
        for nm in ("A", "Color1", "B", "Color2"):
            for s in g.inputs(fn):
                if s.str("name") == nm or s.str("identifier") in ("A_Color", "B_Color"):
                    r = _trace(g, fn, s, depth + 1)
                    if r and r[0] in ("tex", "vcol"):
                        cand.append(r)
        if cand:
            if len(cand) >= 2 and {c[0] for c in cand} == {"tex", "vcol"}:
                tex = next(c for c in cand if c[0] == "tex")
                vc = next(c for c in cand if c[0] == "vcol")
                return ("tex", tex[1], tex[2], vc[1])
            return cand[0]
        for s in g.inputs(fn):
            if s.str("name") in ("A", "Color1"):
                return _trace(g, fn, s, depth + 1)
        return None
    if t in ("ShaderNodeSeparateColor", "ShaderNodeSeparateRGB", "ShaderNodeSeparateXYZ"):
        s = g.input(fn, "Color", "Image", "Vector")
        r = _trace(g, fn, s, depth + 1) if s is not None else None
        if r and r[0] == "tex":
            ch = {"Red": "r", "R": "r", "X": "r", "Green": "g", "G": "g", "Y": "g",
                  "Blue": "b", "B": "b", "Z": "b"}.get(oname, "r")
            return ("tex", r[1], ch)
        return r
    if t in ("ShaderNodeNormalMap", "ShaderNodeBump"):
        s = g.input(fn, "Color", "Height")
        r = _trace(g, fn, s, depth + 1) if s is not None else None
        if r and r[0] == "tex" and t == "ShaderNodeNormalMap":
            strength = g.input(fn, "Strength")
            sv = g.default(strength) if strength is not None else 1.0
            return ("tex", r[1], "normal", sv)
        return None
    if t in ("ShaderNodeGamma", "ShaderNodeHueSaturation", "ShaderNodeBrightContrast",
             "ShaderNodeInvert", "ShaderNodeRGBCurve", "ShaderNodeValToRGB",
             "NodeReroute", "ShaderNodeMath", "ShaderNodeClamp", "ShaderNodeMapRange"):
        for s in g.inputs(fn):
            r = _trace(g, fn, s, depth + 1)
            if r and r[0] in ("tex", "vcol"):
                return r
        return None
    if t == "ShaderNodeRGB":
        out = g.outputs(fn)
        if out:
            v = g.default(out[0])
            return ("const", v) if v is not None else None
    if t == "ShaderNodeValue":
        out = g.outputs(fn)
        if out:
            v = g.default(out[0])
            return ("const", v) if v is not None else None
    return None


def _tex_info(ctx, g, tnode, uv_names):
    """Image descriptor + UV set/transform/wrap of an image texture node."""
    img = tnode.deref("id", "Image")
    desc = _image_descriptor(ctx, img)
    st = tnode.deref("storage")
    ext = st.int("extension", 0) if (st is not None and st.has("extension")) else 0
    wrap = {0: 0, 1: 1, 2: 1, 3: 2}.get(ext, 0)        # repeat/clamp/mirror
    uvset, xf = 0, None
    vin = g.input(tnode, "Vector")
    node, depth = tnode, 0
    while vin is not None and depth < 8:
        depth += 1
        fn, fs = g.link_from(node, vin)
        if fn is None:
            break
        t = g.idname(fn)
        if t == "ShaderNodeMapping":
            loc = g.default(g.input(fn, "Location")) if g.input(fn, "Location") else None
            rot = g.default(g.input(fn, "Rotation")) if g.input(fn, "Rotation") else None
            sca = g.default(g.input(fn, "Scale")) if g.input(fn, "Scale") else None
            if loc is None:     # pre-2.81: TexMapping in storage
                tm = fn.deref("storage")
                if tm is not None:
                    loc, rot, sca = tm.floats("loc"), tm.floats("rot"), tm.floats("size")
            loc = np.zeros(3) if loc is None else np.asarray(loc)
            rot = np.zeros(3) if rot is None else np.asarray(rot)
            sca = np.ones(3) if sca is None else np.asarray(sca)
            c, s = math.cos(rot[2]), math.sin(rot[2])
            M = np.array([[c * sca[0], -s * sca[1], loc[0]],
                          [s * sca[0], c * sca[1], loc[1]], [0, 0, 1.0]])
            vtype = fn.int("custom1", 0)
            if vtype == 1:          # TEXTURE = inverse
                try:
                    M = np.linalg.inv(M)
                except np.linalg.LinAlgError:
                    pass
            xf = M if xf is None else xf @ M
            vin, node = g.input(fn, "Vector"), fn
            continue
        if t == "ShaderNodeUVMap":
            s2 = fn.deref("storage")
            name = s2.str("uv_map") if s2 is not None else ""
            if name and name in uv_names[1:2]:
                uvset = 1
        break
    return {"desc": desc, "uvset": uvset, "xf": xf, "wrap": wrap}


def _lin_to_engine(rgb):
    """Engine shaders treat colour factors as gamma-2.2 encoded."""
    return [float(max(c, 0.0)) ** (1.0 / 2.2) for c in rgb]


_MIXABLE_BSDF = ("ShaderNodeBsdfPrincipled", "ShaderNodeBsdfDiffuse", "ShaderNodeBsdfGlossy",
                 "ShaderNodeBsdfAnisotropic")


def _material_info(ctx, ma, uv_names, col_names):
    """Material view -> material_info dict (+ '_h3d' extras)."""
    key = (ma.addr() if ma is not None else 0, tuple(uv_names[:2]))
    if key in ctx.materials:
        return ctx.materials[key]
    info = {"name": "default", "base_color": [0.8, 0.8, 0.8, 1.0], "metallic": 0.0,
            "roughness": 0.5, "emissive": [0.0, 0.0, 0.0], "emissive_intensity": 1.0,
            "base_color_tex": None, "metallic_roughness_tex": None, "normal_tex": None,
            "emissive_tex": None, "occlusion_tex": None, "double_sided": True,
            "alpha_mode": "OPAQUE", "alpha_cutoff": 0.5,
            "_h3d": {"vertex_colors": False, "slots": {}}}
    if ma is None:
        info["base_color"] = _lin_to_engine([0.8, 0.8, 0.8]) + [1.0]
        ctx.materials[key] = info
        return info
    info["name"] = ma.id_name() or "material"
    bf = ctx.bf
    base = [ma.float("r", 0.8), ma.float("g", 0.8), ma.float("b", 0.8)]
    alpha = ma.float("a", ma.float("alpha", 1.0))
    metallic = ma.float("metallic", 0.0)
    rough = ma.float("roughness", 0.5)
    emis = [0.0, 0.0, 0.0]; estr = 1.0
    vcol = False
    alpha_tex = False
    slots = info["_h3d"]["slots"]
    nt = ma.deref("nodetree", "bNodeTree")
    use_nodes = (ma.int("use_nodes", 1) != 0) or bf.version >= 500
    handled = False
    if nt is not None and use_nodes:
        g = _NodeGraph(ctx, nt)
        outs = [n for n in g.nodes if g.idname(n) == "ShaderNodeOutputMaterial"]
        out = next((n for n in outs if n.int("flag", 0) & (1 << 6)), None) or (outs[0] if outs else None)
        bsdf = None
        if out is not None:
            s = g.input(out, "Surface")
            if s is not None:
                bsdf, _ = g.link_from(out, s)
        # unwrap mix/add shaders: prefer a Principled/Diffuse child
        mix_alpha = None
        mix_other = None                   # (second BSDF, factor) of a Mix Shader
        seen = 0
        while bsdf is not None and g.idname(bsdf) in ("ShaderNodeMixShader", "ShaderNodeAddShader") and seen < 8:
            seen += 1
            ins = [s for s in g.inputs(bsdf) if s.str("name") == "Shader"]
            kids = [g.link_from(bsdf, s)[0] for s in ins]
            kids = [k for k in kids if k is not None]
            pref = [k for k in kids if g.idname(k) not in ("ShaderNodeBsdfTransparent", "ShaderNodeEmission")]
            if g.idname(bsdf) == "ShaderNodeMixShader" and any(g.idname(k) == "ShaderNodeBsdfTransparent" for k in kids):
                fac = g.input(bsdf, "Fac")
                mix_alpha = _trace(g, bsdf, fac) if fac is not None else None
            if (g.idname(bsdf) == "ShaderNodeMixShader" and len(kids) == 2 and mix_other is None
                    and all(g.idname(k) in _MIXABLE_BSDF for k in kids)):
                fac = g.input(bsdf, "Fac")
                fv = _trace(g, bsdf, fac) if fac is not None else None
                if fv and fv[0] == "const" and fv[1] is not None:
                    mix_other = (kids[1], float(np.asarray(fv[1]).ravel()[0]))
                    pref = [kids[0]]
            for k in kids:
                if g.idname(k) == "ShaderNodeEmission":
                    c = g.input(k, "Color"); st_ = g.input(k, "Strength")
                    cv = _trace(g, k, c) if c is not None else None
                    if cv and cv[0] == "const":
                        emis = list(np.asarray(cv[1])[:3]); estr = g.default(st_) or 1.0
            bsdf = pref[0] if pref else (kids[0] if kids else None)
        if bsdf is not None:
            handled = True
            t = g.idname(bsdf)

            def slot(name, *socks):
                s = g.input(bsdf, *socks)
                return (_trace(g, bsdf, s), s) if s is not None else (None, None)

            if t == "ShaderNodeEmission":
                c, _ = slot("e", "Color")
                st_ = g.input(bsdf, "Strength")
                if c and c[0] == "const":
                    emis = list(np.asarray(c[1])[:3])
                elif c and c[0] == "tex":
                    slots["emissive"] = _tex_info(ctx, g, c[1], uv_names); emis = [1.0, 1.0, 1.0]
                estr = g.default(st_) if st_ is not None else 1.0
                base = [0.0, 0.0, 0.0]; rough = 1.0
            else:
                bc, _ = slot("base", "Base Color", "Color")
                if bc:
                    if bc[0] == "const" and bc[1] is not None:
                        base = list(np.asarray(bc[1])[:3])
                    elif bc[0] == "tex":
                        slots["base_color"] = _tex_info(ctx, g, bc[1], uv_names)
                        base = [1.0, 1.0, 1.0]
                        if len(bc) > 3:
                            vcol = True
                    elif bc[0] == "vcol":
                        vcol = True; base = [1.0, 1.0, 1.0]
                mt, _ = slot("met", "Metallic")
                if mt and mt[0] == "const" and mt[1] is not None:
                    metallic = float(np.asarray(mt[1]).ravel()[0])
                ro, _ = slot("rough", "Roughness")
                if ro and ro[0] == "const" and ro[1] is not None:
                    rough = float(np.asarray(ro[1]).ravel()[0])
                if (mt and mt[0] == "tex") or (ro and ro[0] == "tex"):
                    slots["metallic_roughness"] = {
                        "pack": {"metal": (_tex_info(ctx, g, mt[1], uv_names), mt[2]) if mt and mt[0] == "tex" else None,
                                 "rough": (_tex_info(ctx, g, ro[1], uv_names), ro[2]) if ro and ro[0] == "tex" else None}}
                    if mt and mt[0] == "tex":
                        metallic = 1.0
                    if ro and ro[0] == "tex":
                        rough = 1.0
                nm, _ = slot("nrm", "Normal")
                if nm and nm[0] == "tex" and nm[2] == "normal":
                    ti = _tex_info(ctx, g, nm[1], uv_names)
                    ti["scale"] = float(nm[3]) if len(nm) > 3 and nm[3] is not None else 1.0
                    slots["normal"] = ti
                ec, _ = slot("emc", "Emission Color", "Emission")
                es = g.input(bsdf, "Emission Strength")
                if ec:
                    if ec[0] == "const" and ec[1] is not None:
                        emis = list(np.asarray(ec[1])[:3])
                    elif ec[0] == "tex":
                        slots["emissive"] = _tex_info(ctx, g, ec[1], uv_names); emis = [1.0, 1.0, 1.0]
                if es is not None:
                    v = g.default(es)
                    estr = float(v) if v is not None else 1.0
                al, _ = slot("alpha", "Alpha")
                if al:
                    if al[0] == "const" and al[1] is not None:
                        alpha = float(np.asarray(al[1]).ravel()[0])
                    elif al[0] == "tex":
                        alpha_tex = True
                        if "base_color" not in slots or slots["base_color"]["desc"] != _image_descriptor(ctx, al[1].deref("id", "Image")):
                            slots.setdefault("base_color", _tex_info(ctx, g, al[1], uv_names))
                if mix_alpha:
                    if mix_alpha[0] == "const" and mix_alpha[1] is not None:
                        alpha = float(np.asarray(mix_alpha[1]).ravel()[0])
                    elif mix_alpha[0] == "tex":
                        alpha_tex = True
                if mix_other is not None:
                    # blend the constant colour / metal / roughness of both sides
                    k2, f = mix_other
                    f = float(np.clip(f, 0.0, 1.0))

                    def const_of(sock_names, default):
                        sk = g.input(k2, *sock_names)
                        v = _trace(g, k2, sk) if sk is not None else None
                        if v and v[0] == "const" and v[1] is not None:
                            return np.asarray(v[1], float).ravel()
                        return None if default is None else np.asarray(default, float).ravel()
                    t2 = g.idname(k2)
                    c2 = const_of(("Base Color", "Color"), None)
                    if c2 is not None and not (bc and bc[0] in ("tex", "vcol")):
                        base = list(np.asarray(base, float)[:3] * (1 - f) + c2[:3] * f)
                    m2 = 0.0 if t2 == "ShaderNodeBsdfDiffuse" else (
                        1.0 if t2 in ("ShaderNodeBsdfGlossy", "ShaderNodeBsdfAnisotropic")
                        else float(const_of(("Metallic",), [0.0])[0]))
                    r2 = 1.0 if t2 == "ShaderNodeBsdfDiffuse" else float(const_of(("Roughness",), [0.5])[0])
                    m1 = 0.0 if t == "ShaderNodeBsdfDiffuse" else (1.0 if t in ("ShaderNodeBsdfGlossy",
                                                                                "ShaderNodeBsdfAnisotropic") else metallic)
                    r1 = 1.0 if t == "ShaderNodeBsdfDiffuse" else rough
                    if not (mt and mt[0] == "tex"):
                        metallic = m1 * (1 - f) + m2 * f
                    if not (ro and ro[0] == "tex"):
                        rough = r1 * (1 - f) + r2 * f
                    t = "mixed"
                if t == "ShaderNodeBsdfDiffuse":
                    # its Roughness input is Oren-Nayar (matte), not glossiness
                    metallic = 0.0
                    rough = 1.0
                elif t == "ShaderNodeBsdfGlossy" or t == "ShaderNodeBsdfAnisotropic":
                    metallic = 1.0
                elif t in ("ShaderNodeBsdfGlass", "ShaderNodeBsdfRefraction"):
                    alpha = min(alpha, 0.3)
                if t == "ShaderNodeBsdfPrincipled":
                    tr = g.input(bsdf, "Transmission Weight", "Transmission")
                    trv = g.default(tr) if tr is not None else 0.0
                    if trv and float(np.asarray(trv).ravel()[0]) > 0.01:
                        alpha = min(alpha, max(0.15, 1.0 - 0.85 * float(np.asarray(trv).ravel()[0])))
    if not handled:
        # Blender Internal (<= 2.79) material + texture slots
        emit = ma.float("emit", 0.0)
        if emit > 0:
            emis = [base[0] * emit, base[1] * emit, base[2] * emit]
        spec = ma.float("spec", 0.5)
        har = ma.int("har", 50)
        rough = float(np.clip(1.0 - math.sqrt(max(har, 1) / 511.0), 0.05, 1.0)) if ma.has("har") and not ma.has("roughness") else rough
        if ma.has("mode") and (ma.int("mode", 0) & (1 << 7)):     # MA_VERTEXCOLP
            vcol = True
        if ma.has("mtex"):
            ptrs = ma.ints("mtex")
            f = ma.fields["mtex"]
            for i in range(f.size // f.elem_size):
                p = struct.unpack_from(bf.endian + ("I" if bf.ptrsize == 4 else "Q"), ma.blk.data, ma.off + f.offset + i * f.elem_size)[0]
                if not p:
                    continue
                mt = bf.view(p, "MTex", ctx=ma)
                if mt is None:
                    continue
                tex = mt.deref("tex", "Tex")
                if tex is None or tex.int("type", 0) != 8:       # TEX_IMAGE
                    continue
                img = tex.deref("ima", "Image")
                desc = _image_descriptor(ctx, img)
                if desc is None:
                    continue
                mapto = mt.int("mapto", 1)
                ti = {"desc": desc, "uvset": 0, "xf": None, "wrap": 0}
                ofs = mt.floats("ofs", np.zeros(3)); size = mt.floats("size", np.ones(3))
                if np.any(ofs) or np.any(size != 1):
                    ti["xf"] = np.array([[size[0], 0, ofs[0] * 0 + 0.5 - 0.5 * size[0] + ofs[0]],
                                         [0, size[1], 0.5 - 0.5 * size[1] + ofs[1]], [0, 0, 1.0]])
                uvn = mt.str("uvname")
                if uvn and uvn in uv_names[1:2]:
                    ti["uvset"] = 1
                if mapto & 1 and "base_color" not in slots:         # MAP_COL
                    slots["base_color"] = ti; base = [1.0, 1.0, 1.0]
                if mapto & 2 and "normal" not in slots:             # MAP_NORM
                    if tex.int("imaflag", 0) & (1 << 11):           # TEX_NORMALMAP
                        slots["normal"] = ti
                if mapto & 128:                                     # MAP_ALPHA
                    alpha_tex = True
    # ---- blending ----
    if ma.has("surface_render_method"):
        method = ma.int("surface_render_method", 0)      # 0 dithered, 1 blended
        if method == 1 and (alpha < 0.999 or alpha_tex):
            info["alpha_mode"] = "BLEND"
        elif alpha_tex:
            info["alpha_mode"] = "MASK"
        elif alpha < 0.999:
            info["alpha_mode"] = "BLEND"
    elif ma.has("blend_method"):
        bm = ma.int("blend_method", 0)
        if bm == 3:
            info["alpha_mode"] = "MASK"; info["alpha_cutoff"] = ma.float("alpha_threshold", 0.5)
        elif bm in (4, 5):
            info["alpha_mode"] = "BLEND" if (bm == 5 or alpha < 0.999) else ("MASK" if alpha_tex else "OPAQUE")
            if bm == 4 and alpha_tex:
                info["alpha_mode"] = "MASK"
    elif alpha < 0.999 or alpha_tex:
        info["alpha_mode"] = "BLEND" if not alpha_tex else "MASK"
    if ma.has("blend_flag"):
        info["double_sided"] = not (ma.int("blend_flag", 0) & 1)
    info["base_color"] = _lin_to_engine(base) + [float(np.clip(alpha, 0.0, 1.0))]
    info["metallic"] = float(np.clip(metallic, 0, 1))
    info["roughness"] = float(np.clip(rough, 0, 1))
    m_e = max(emis) if emis else 0.0
    if m_e > 0 and estr > 0:
        info["emissive"] = [float(c) for c in emis]
        info["emissive_intensity"] = float(estr)
    info["_h3d"]["vertex_colors"] = bool(vcol)
    ctx.materials[key] = info
    return info


# ======================================================================
#  lights
# ======================================================================
def _light(ctx, ob, W_eng, name):
    la = ob.deref("data")
    if la is None:
        return None
    t = la.int("type", 0)
    kind = {0: "point", 1: "sun", 2: "spot", 3: "sun", 4: "point"}.get(t)
    if kind is None:
        return None
    col = (la.float("r", 1.0), la.float("g", 1.0), la.float("b", 1.0))
    energy = la.float("energy", 10.0)
    if ctx.bf.version < 280:       # BI energy is unitless; ~x100 W
        energy *= 100.0 if kind != "sun" else 1.0
    d = W_eng[:3, :3] @ np.array([0.0, 0.0, -1.0])
    d = d / max(np.linalg.norm(d), 1e-12)
    return {"kind": kind, "name": name, "color": tuple(float(c) for c in col),
            "energy": float(energy), "position": tuple(float(x) for x in W_eng[:3, 3]),
            "direction": tuple(float(x) for x in d),
            "spotsize": la.float("spotsize", math.radians(45)),
            "spotblend": la.float("spotblend", 0.15),
            "dist": la.float("cutoff_distance", 0.0) if la.has("cutoff_distance") else 0.0}


# ======================================================================
#  main entry
# ======================================================================
def load_blend(path, logger=None):
    """-> (parts, lights).  parts: list of dict(name, positions, normals,
    uv0, uv1, colors, indices, material_info)."""
    bf = BlendFile(path)
    ctx = _Ctx(bf, path, logger)
    if bf.version >= 600:
        ctx.warn_once("future", f".blend saved by Blender {bf.version // 100}.{bf.version % 100} "
                      f"— newer than tested; importing best-effort")
    objects = _scene_objects(ctx)
    groups = {}            # material key -> lists
    lights = []
    mesh_cache = {}
    n_obj = 0
    for ob, W in objects:
        name = ob.id_name()
        otype = ob.int("type", -1)
        W_eng = ZUP_TO_YUP @ W
        if otype == OB_LAMP:
            li = _light(ctx, ob, W_eng, name)
            if li:
                lights.append(li)
            continue
        if otype in (OB_FONT, OB_MBALL):
            ctx.warn_once("curve_" + name, f".blend: '{name}' is a "
                          f"{ {4: 'text', 5: 'metaball'}[otype]} object — "
                          f"convert it to a mesh in Blender (Object > Convert > Mesh) to import it")
            continue
        if otype not in (OB_MESH, OB_CURVE, OB_SURF):
            continue
        is_curve = otype != OB_MESH
        me = ob.deref("data", "Curve" if is_curve else "Mesh")
        if me is None:
            continue
        mkey = me.addr()
        if mkey not in mesh_cache:
            try:
                mesh_cache[mkey] = _decode_curve(ctx, me, name) if is_curve else _decode_mesh(ctx, me)
            except Exception as ex:
                ctx.warn_once("mesh_" + name, f".blend: {'curve' if is_curve else 'mesh'} of '{name}' "
                              f"unreadable ({ex}) — skipped")
                mesh_cache[mkey] = (None, {})
        base_m, info = mesh_cache[mkey]
        if base_m is None or base_m.nf == 0:
            continue
        m = base_m.copy()
        if not is_curve:
            try:
                m = _apply_shape_keys(ctx, ob, me, m, name)
            except Exception as ex:
                ctx.warn_once("keys_" + name, f".blend: shape keys of '{name}' unreadable ({ex}) — "
                              f"basis shape used")
        m = _apply_modifiers(ctx, ob, m, W, name)
        if m.nf == 0:
            continue
        if info.get("has_encoded_custom_normals"):
            ctx.warn_once("cn", f".blend: custom split normals of '{name}' could not be decoded "
                          f"({info.get('custom_normal_error', '?')}) — recomputed from smooth/sharp flags")
        # material slots
        totcol = max(me.int("totcol", 0), ob.int("totcol", 0))
        me_mats = _ptr_array(ctx, me, "mat", me.int("totcol", 0))
        ob_mats = _ptr_array(ctx, ob, "mat", ob.int("totcol", 0))
        bits = None
        if ob.fields.get("matbits") is not None and ob.fields["matbits"].is_ptr:
            bits = ob.array("matbits", "u1", ob.int("totcol", 0))
        else:
            bits = ob.ints("matbits")
        slots = []
        for i in range(max(totcol, 1)):
            use_ob = False
            if bits is not None and i < len(bits):
                use_ob = bits[i] != 0
            mp = ob_mats[i] if (use_ob and i < len(ob_mats)) else (me_mats[i] if i < len(me_mats) else 0)
            slots.append(ctx.bf.view(mp, "Material") if mp else None)
        fn = face_normals(m)
        cn = corner_normals(m, fn)
        tri_c, tri_f = triangulate(m, fn)
        if not len(tri_c):
            continue
        n_obj += 1
        fmat = np.clip(m.face_mat[tri_f], 0, len(slots) - 1)
        face_img = info.get("face_image")
        img_key = None
        if face_img is not None and len(face_img[0]) == base_m.nf and m.nf == base_m.nf:
            img_key = face_img[0][tri_f]
        uv_names = info.get("uv_names", [])
        for si in np.unique(fmat):
            sel = fmat == si
            sub_keys = [None] if img_key is None else list(np.unique(img_key[sel]))
            for ik in sub_keys:
                sel2 = sel if ik is None else (sel & (img_key == ik))
                tc = tri_c[sel2]
                ma = slots[si]
                minfo = _material_info(ctx, ma, uv_names, info.get("col_names", []))
                if ik:   # legacy per-face image (TexFace)
                    iv = ctx.bf.view(int(ik), "Image", ctx=face_img[1])
                    desc = _image_descriptor(ctx, iv)
                    if desc is not None and "base_color" not in minfo["_h3d"]["slots"]:
                        minfo = dict(minfo); minfo["_h3d"] = dict(minfo["_h3d"])
                        minfo["_h3d"]["slots"] = dict(minfo["_h3d"]["slots"])
                        minfo["_h3d"]["slots"]["base_color"] = {"desc": desc, "uvset": 0, "xf": None, "wrap": 0}
                        minfo["base_color"] = [1.0, 1.0, 1.0, minfo["base_color"][3]]
                        minfo["name"] = minfo["name"] + "_" + os.path.basename(str(desc[1]) if desc[0] == "path" else "packed")
                gkey = (id(minfo), ik if ik is not None else 0)
                flat = tc.ravel()
                pos = m.V[m.corner_vert[flat]]
                nrm = cn[flat]
                pos, nrm, _, flip = bake(pos, nrm, None, W_eng)
                tri = np.arange(len(flat)).reshape(-1, 3)
                if flip:
                    tri = tri[:, [0, 2, 1]]
                g = groups.setdefault(gkey, {"info": minfo, "pos": [], "nrm": [], "uv0": [], "uv1": [],
                                             "col": [], "idx": [], "count": 0, "has": set()})
                g["pos"].append(pos); g["nrm"].append(nrm)
                for a, src in (("uv0", m.uv0), ("uv1", m.uv1), ("col", m.col)):
                    if src is not None:
                        g["has"].add(a)
                        g[a].append(np.asarray(src)[flat])
                    else:
                        g[a].append(None)
                g["idx"].append(tri + g["count"])
                g["count"] += len(flat)
    parts = []
    for gkey, g in groups.items():
        info = g["info"]
        n_each = [len(p) for p in g["pos"]]
        pos = np.concatenate(g["pos"]); nrm = np.concatenate(g["nrm"])

        def cat(a, dim, fill):
            if a not in g["has"]:
                return None
            return np.concatenate([x if x is not None else np.tile(fill, (n, 1))
                                   for x, n in zip(g[a], n_each)])
        uv0 = cat("uv0", 2, [0.0, 0.0])
        uv1 = cat("uv1", 2, [0.0, 0.0])
        col = cat("col", 4, [1.0, 1.0, 1.0, 1.0]) if info["_h3d"]["vertex_colors"] else None
        if col is not None:
            # linear -> engine encoding (its shader gamma-decodes with pow 2.2)
            col = np.c_[np.clip(col[:, :3], 0, None) ** (1 / 2.2), np.clip(col[:, 3], 0, 1)]
        idx = np.concatenate(g["idx"])
        first, inv = weld(pos, nrm, uv0, uv1, col)
        parts.append({"name": info["name"], "positions": pos[first].astype(np.float32),
                      "normals": nrm[first].astype(np.float32),
                      "uv0": None if uv0 is None else uv0[first].astype(np.float32),
                      "uv1": None if uv1 is None else uv1[first].astype(np.float32),
                      "colors": None if col is None else col[first].astype(np.float32),
                      "tangents": None,
                      "indices": inv[idx].astype(np.uint32).ravel(),
                      "material_info": info})
    _log(logger, "info", f".blend imported: {os.path.basename(path)} (Blender "
         f"{bf.version // 100}.{bf.version % 100}, {n_obj} mesh object(s), "
         f"{len(parts)} material part(s), {len(lights)} light(s))")
    return parts, lights


def _ptr_array(ctx, view, field, n):
    f = view.fields.get(field)
    if f is None or not f.is_ptr or n <= 0:
        return []
    r = view.resolve(field)
    if r is None:
        return []
    blk, off = r
    fmt = ctx.bf.endian + ("I" if ctx.bf.ptrsize == 4 else "Q")
    out = []
    for i in range(n):
        p = off + i * ctx.bf.ptrsize
        if p + ctx.bf.ptrsize > len(blk.data):
            break
        out.append(struct.unpack_from(fmt, blk.data, p)[0])
    # Material pointers resolve globally (ID blocks)
    return out
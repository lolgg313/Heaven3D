"""Skeletal animation for MakeHuman characters: Mixamo import, retargeting,
procedural fallback clips, blending, forward kinematics and foot IK.

Pure numpy.  Quaternions are (w, x, y, z).

Animation files
    .fbx   Mixamo downloads (FBX Binary, with or without skin, "In Place" or not)
    .glb / .gltf   any skinned glTF animation (e.g. Mixamo -> Blender -> glTF)
    .bvh   motion capture (CMU and friends)

Bones are matched by name (namespace and case are ignored, common aliases of
other rigs are understood), so Mixamo clips drop straight onto the Mixamo
skeleton every character gets.  Rest poses do not need to match: the clip is
retargeted bone-by-bone in world space (the T-pose of Mixamo onto the A-pose
of MakeHuman), and the hips motion is scaled by the hip height ratio.
"""
import json
import math
import os
import re
import struct
import zlib

import numpy as np

FPS = 30.0


# =========================================================================== quaternions
def qnorm(q):
    n = np.linalg.norm(q, axis=-1, keepdims=True)
    n[n < 1e-12] = 1.0
    return q / n


def qmul(a, b):
    aw, ax, ay, az = np.moveaxis(a, -1, 0)
    bw, bx, by, bz = np.moveaxis(b, -1, 0)
    return np.stack([aw * bw - ax * bx - ay * by - az * bz,
                     aw * bx + ax * bw + ay * bz - az * by,
                     aw * by - ax * bz + ay * bw + az * bx,
                     aw * bz + ax * by - ay * bx + az * bw], axis=-1)


def qinv(q):
    return q * np.array([1.0, -1.0, -1.0, -1.0])


def qaxis(axis, ang):
    axis = np.asarray(axis, np.float64)
    axis = axis / max(np.linalg.norm(axis), 1e-12)
    s = math.sin(ang * 0.5)
    return np.array([math.cos(ang * 0.5), axis[0] * s, axis[1] * s, axis[2] * s])


def qrot(q, v):
    """Rotate vector(s) v by quaternion(s) q."""
    qv = np.concatenate([np.zeros(v.shape[:-1] + (1,)), v], axis=-1)
    return qmul(qmul(q, qv), qinv(q))[..., 1:]


def qbetween(a, b):
    """Shortest rotation taking direction a onto direction b."""
    a = np.asarray(a, np.float64)
    b = np.asarray(b, np.float64)
    a = a / max(np.linalg.norm(a), 1e-12)
    b = b / max(np.linalg.norm(b), 1e-12)
    d = float(np.dot(a, b))
    if d < -0.999999:
        ax = np.cross([1.0, 0, 0], a)
        if np.linalg.norm(ax) < 1e-6:
            ax = np.cross([0, 1.0, 0], a)
        return qaxis(ax, math.pi)
    c = np.cross(a, b)
    return qnorm(np.array([1.0 + d, c[0], c[1], c[2]]))


def q_to_m(q):
    q = qnorm(q)
    w, x, y, z = np.moveaxis(q, -1, 0)
    m = np.empty(q.shape[:-1] + (3, 3))
    m[..., 0, 0] = 1 - 2 * (y * y + z * z)
    m[..., 0, 1] = 2 * (x * y - z * w)
    m[..., 0, 2] = 2 * (x * z + y * w)
    m[..., 1, 0] = 2 * (x * y + z * w)
    m[..., 1, 1] = 1 - 2 * (x * x + z * z)
    m[..., 1, 2] = 2 * (y * z - x * w)
    m[..., 2, 0] = 2 * (x * z - y * w)
    m[..., 2, 1] = 2 * (y * z + x * w)
    m[..., 2, 2] = 1 - 2 * (x * x + y * y)
    return m


def m_to_q(m):
    """Rotation matrix (...,3,3) -> quaternion (...,4).  Robust (Shepperd)."""
    m = np.asarray(m, np.float64)
    shp = m.shape[:-2]
    m = m.reshape(-1, 3, 3)
    q = np.empty((len(m), 4))
    tr = m[:, 0, 0] + m[:, 1, 1] + m[:, 2, 2]
    for i in range(len(m)):
        a = m[i]
        t = tr[i]
        if t > 0:
            s = math.sqrt(t + 1.0) * 2
            q[i] = (0.25 * s, (a[2, 1] - a[1, 2]) / s, (a[0, 2] - a[2, 0]) / s, (a[1, 0] - a[0, 1]) / s)
        elif a[0, 0] > a[1, 1] and a[0, 0] > a[2, 2]:
            s = math.sqrt(1.0 + a[0, 0] - a[1, 1] - a[2, 2]) * 2
            q[i] = ((a[2, 1] - a[1, 2]) / s, 0.25 * s, (a[0, 1] + a[1, 0]) / s, (a[0, 2] + a[2, 0]) / s)
        elif a[1, 1] > a[2, 2]:
            s = math.sqrt(1.0 + a[1, 1] - a[0, 0] - a[2, 2]) * 2
            q[i] = ((a[0, 2] - a[2, 0]) / s, (a[0, 1] + a[1, 0]) / s, 0.25 * s, (a[1, 2] + a[2, 1]) / s)
        else:
            s = math.sqrt(1.0 + a[2, 2] - a[0, 0] - a[1, 1]) * 2
            q[i] = ((a[1, 0] - a[0, 1]) / s, (a[0, 2] + a[2, 0]) / s, (a[1, 2] + a[2, 1]) / s, 0.25 * s)
    return qnorm(q).reshape(shp + (4,))


def _orthonormal(m3):
    u, _s, vt = np.linalg.svd(m3)
    r = u @ vt
    if np.linalg.det(r) < 0:
        u[:, -1] *= -1
        r = u @ vt
    return r


def euler_m(deg, order="XYZ"):
    """Intrinsic-order matrix the FBX / BVH way: 'XYZ' applies X first (R = Rz Ry Rx)."""
    m = np.eye(3)
    for ax, a in zip(order, deg):
        r = math.radians(a)
        c, s = math.cos(r), math.sin(r)
        if ax == "X":
            e = np.array([[1, 0, 0], [0, c, -s], [0, s, c]])
        elif ax == "Y":
            e = np.array([[c, 0, s], [0, 1, 0], [-s, 0, c]])
        else:
            e = np.array([[c, -s, 0], [s, c, 0], [0, 0, 1]])
        m = e @ m
    return m


# =========================================================================== bone names
_ALIASES = {
    "pelvis": "hips", "hip": "hips", "lowerback": "spine", "spine01": "spine", "spine_01": "spine",
    "spine02": "spine1", "spine03": "spine2", "chest": "spine1", "upperchest": "spine2", "neck01": "neck",
    "lhipjoint": "", "rhipjoint": "", "lowerneck": "neck", "upperneck": "head",
    "lcollar": "leftshoulder", "rcollar": "rightshoulder", "lshoulder": "leftarm", "rshoulder": "rightarm",
    "lelbow": "leftforearm", "relbow": "rightforearm", "lwrist": "lefthand", "rwrist": "righthand",
    "lhip": "leftupleg", "rhip": "rightupleg", "lknee": "leftleg", "rknee": "rightleg", "lankle": "leftfoot",
    "rankle": "rightfoot", "lthigh": "leftupleg", "rthigh": "rightupleg", "lshin": "leftleg", "rshin": "rightleg",
    "clavicle_l": "leftshoulder", "clavicle_r": "rightshoulder", "upperarm_l": "leftarm", "upperarm_r": "rightarm",
    "lowerarm_l": "leftforearm", "lowerarm_r": "rightforearm", "hand_l": "lefthand", "hand_r": "righthand",
    "thigh_l": "leftupleg", "thigh_r": "rightupleg", "calf_l": "leftleg", "calf_r": "rightleg",
    "foot_l": "leftfoot", "foot_r": "rightfoot", "ball_l": "lefttoebase", "ball_r": "righttoebase",
    "leftupperarm": "leftarm", "rightupperarm": "rightarm", "leftlowerarm": "leftforearm",
    "rightlowerarm": "rightforearm", "leftupperleg": "leftupleg", "rightupperleg": "rightupleg",
    "leftlowerleg": "leftleg", "rightlowerleg": "rightleg", "lefttoes": "lefttoebase", "righttoes": "righttoebase",
    "lefttoe": "lefttoebase", "righttoe": "righttoebase",
    "abdomen": "spine", "abdomen2": "spine1", "chest2": "spine2", "lshldr": "leftarm", "rshldr": "rightarm",
    "lforearm": "leftforearm", "rforearm": "rightforearm", "lhand": "lefthand", "rhand": "righthand",
    "lfoot": "leftfoot", "rfoot": "rightfoot", "ltoe": "lefttoebase", "rtoe": "righttoebase",
    "lupperarm": "leftarm", "rupperarm": "rightarm", "lloweram": "leftforearm", "rlowerarm": "rightforearm",
    "lupleg": "leftupleg", "rupleg": "rightupleg", "lleg": "leftleg", "rleg": "rightleg",
}


_SIDE_BASE = {
    "shoulder": "shoulder", "clavicle": "shoulder", "collar": "shoulder",
    "arm": "arm", "upperarm": "arm", "uparm": "arm", "shldr": "arm",
    "forearm": "forearm", "lowerarm": "forearm", "elbow": "forearm",
    "hand": "hand", "wrist": "hand",
    "upleg": "upleg", "upperleg": "upleg", "thigh": "upleg", "femur": "upleg",
    "leg": "leg", "lowerleg": "leg", "shin": "leg", "calf": "leg", "knee": "leg",
    "foot": "foot", "ankle": "foot",
    "toebase": "toebase", "toe": "toebase", "toes": "toebase", "ball": "toebase",
}
_CENTER = {"hips": "hips", "hip": "hips", "pelvis": "hips", "spine": "spine", "spine1": "spine1", "spine2": "spine2",
           "chest": "spine1", "upperchest": "spine2", "abdomen": "spine", "neck": "neck", "neck1": "neck",
           "head": "head"}


def canon(name):
    """Canonical (Mixamo-style, lower case) bone name for any common humanoid rig:
    Mixamo, Rigify / Quaternius (DEF-thigh.L), Unreal (thigh_l, spine_01), BVH ..."""
    n = str(name)
    for sep in (":", "|"):
        if sep in n:
            n = n.split(sep)[-1]
    n = n.strip().lower().replace(" ", "")
    n = re.sub(r"^(def|org|mch|bip0?1|b|j|jnt|bone)[-_.]", "", n)
    if n in _ALIASES:
        return _ALIASES[n]
    flat = re.sub(r"[^a-z0-9]", "", n)
    if flat in _ALIASES:
        return _ALIASES[flat]
    if any(k in flat for k in ("end", "leaf", "nub", "tip", "target", "twist", "ik", "pole", "roll")):
        return flat
    side, base = None, n
    m = re.match(r"^(left|right)[-_.]?(.+)$", n) or re.match(r"^([lr])[-_.](.+)$", n)
    if m:
        side, base = m.group(1)[0], m.group(2)
    else:
        m = re.match(r"^(.+?)[-_.](l|r|left|right)$", n)
        if m:
            side, base = m.group(2)[0], m.group(1)
    bflat = re.sub(r"[^a-z0-9]", "", base)
    if side is None:
        m = re.match(r"^spine[-_.]0*(\d+)$", base)
        if m:                                   # Rigify spine.001 / Unreal spine_01 are 1-based
            k = int(m.group(1))
            return ("spine", "spine1", "spine2", "spine2", "spine2")[max(0, min(4, k - 1))]
        m = re.match(r"^neck[-_.]0*\d+$", base)
        if m:
            return "neck"
        return _CENTER.get(bflat, flat)
    sd = "left" if side == "l" else "right"
    m = re.match(r"^(?:hand|f)?(thumb|index|middle|ring|pinky)(?:finger)?0*([1-3])$", bflat)
    if m:
        return f"{sd}hand{m.group(1)}{m.group(2)}"
    if bflat in _SIDE_BASE:
        return sd + _SIDE_BASE[bflat]
    return sd + bflat


# =========================================================================== source animation
class SourceAnim:
    """A skeleton animation from a file, as global transforms per frame.

    names (B), parents (B), rest (B,4,4) global, frames (F,B,4,4) global, fps."""

    def __init__(self, name, names, parents, rest, frames, fps=FPS):
        self.name = name
        self.names = list(names)
        self.parents = np.asarray(parents, np.int64)
        self.rest = np.asarray(rest, np.float64)
        self.frames = np.asarray(frames, np.float64)
        self.fps = float(fps)

    @property
    def duration(self):
        return max(0.0, (len(self.frames) - 1) / self.fps)


def _globals_from_locals(parents, locals_):
    """locals_ (..., B, 4, 4) in topological order -> globals."""
    g = np.empty_like(locals_)
    for i, p in enumerate(parents):
        g[..., i, :, :] = locals_[..., i, :, :] if p < 0 else g[..., p, :, :] @ locals_[..., i, :, :]
    return g


def _topo(names, parents):
    """Reorder so parents come first; returns (names, parents, order)."""
    n = len(names)
    order, seen = [], set()

    def visit(i, depth=0):
        if i in seen or depth > n:
            return
        p = parents[i]
        if p >= 0:
            visit(p, depth + 1)
        seen.add(i)
        order.append(i)
    for i in range(n):
        visit(i)
    remap = {o: k for k, o in enumerate(order)}
    return ([names[o] for o in order], [remap[parents[o]] if parents[o] >= 0 else -1 for o in order], order)


# --------------------------------------------------------------------------- FBX (binary)
class _FbxNode:
    __slots__ = ("name", "props", "children")

    def __init__(self, name, props, children):
        self.name, self.props, self.children = name, props, children

    def find(self, name):
        for c in self.children:
            if c.name == name:
                return c
        return None

    def findall(self, name):
        return [c for c in self.children if c.name == name]


def _fbx_read(data):
    if not data.startswith(b"Kaydara FBX Binary"):
        raise ValueError("only binary FBX is supported (Mixamo's default 'FBX Binary' download)")
    version = struct.unpack_from("<I", data, 23)[0]
    wide = version >= 7500
    hdr = 25 if wide else 13

    def read_props(off, count):
        props = []
        for _ in range(count):
            t = chr(data[off])
            off += 1
            if t == "Y":
                props.append(struct.unpack_from("<h", data, off)[0]); off += 2
            elif t == "C":
                props.append(bool(data[off])); off += 1
            elif t == "I":
                props.append(struct.unpack_from("<i", data, off)[0]); off += 4
            elif t == "F":
                props.append(struct.unpack_from("<f", data, off)[0]); off += 4
            elif t == "D":
                props.append(struct.unpack_from("<d", data, off)[0]); off += 8
            elif t == "L":
                props.append(struct.unpack_from("<q", data, off)[0]); off += 8
            elif t in "fdlib":
                n, enc, clen = struct.unpack_from("<III", data, off); off += 12
                raw = data[off:off + clen]; off += clen
                if enc == 1:
                    raw = zlib.decompress(raw)
                dt = {"f": "<f4", "d": "<f8", "l": "<i8", "i": "<i4", "b": "u1"}[t]
                props.append(np.frombuffer(raw, dt, count=n))
            elif t in "SR":
                n = struct.unpack_from("<I", data, off)[0]; off += 4
                raw = data[off:off + n]; off += n
                props.append(raw.decode("utf-8", "replace") if t == "S" else raw)
            else:
                raise ValueError(f"bad FBX property type {t!r}")
        return props

    def read_node(off):
        if wide:
            end, nprops, plen = struct.unpack_from("<QQQ", data, off); off += 24
        else:
            end, nprops, plen = struct.unpack_from("<III", data, off); off += 12
        if end == 0:
            return None, off
        nl = data[off]; off += 1
        name = data[off:off + nl].decode("ascii", "replace"); off += nl
        props = read_props(off, nprops)
        off += plen
        children = []
        while off < end:
            if end - off == hdr and not any(data[off:end]):
                off = end
                break
            ch, off = read_node(off)
            if ch is None:
                break
            children.append(ch)
        return _FbxNode(name, props, children), end

    off = 27
    top = []
    while off < len(data) - hdr:
        node, off2 = read_node(off)
        if node is None:
            break
        top.append(node)
        off = off2
    return _FbxNode("", [], top)


def _p70(node):
    out = {}
    p = node.find("Properties70") if node else None
    for e in (p.children if p else []):
        if e.name == "P" and e.props:
            out[e.props[0]] = e.props[4:]
    return out


_KTIME = 46186158000.0


def load_fbx(path):
    with open(path, "rb") as f:
        root = _fbx_read(f.read())
    gs = _p70(root.find("GlobalSettings"))
    unit = float((gs.get("UnitScaleFactor") or [1.0])[0])
    up = int((gs.get("UpAxis") or [1])[0])
    objs = root.find("Objects")
    models, cnodes, curves = {}, {}, {}
    for o in objs.children:
        if o.name == "Model":
            kind = o.props[2] if len(o.props) > 2 else ""
            models[o.props[0]] = {"name": o.props[1].split("\x00")[0], "kind": kind, "p": _p70(o)}
        elif o.name == "AnimationCurveNode":
            cnodes[o.props[0]] = {"name": o.props[1].split("\x00")[0], "p": _p70(o), "curves": {}, "model": None,
                                  "attr": None}
        elif o.name == "AnimationCurve":
            kt = o.find("KeyTime")
            kv = o.find("KeyValueFloat")
            if kt is not None and kv is not None:
                curves[o.props[0]] = (np.asarray(kt.props[0], np.float64) / _KTIME,
                                      np.asarray(kv.props[0], np.float64))
    parent = {}
    for c in root.find("Connections").children:
        if c.name != "C":
            continue
        kind, src, dst = c.props[0], c.props[1], c.props[2]
        if kind == "OO" and src in models and dst in models:
            parent[src] = dst
        elif kind == "OP" and src in cnodes and dst in models:
            cnodes[src]["model"] = dst
            cnodes[src]["attr"] = c.props[3]
        elif kind == "OP" and src in curves and dst in cnodes:
            cnodes[dst]["curves"][c.props[3]] = curves[src]
    # skeleton = LimbNodes (+ their Null/Root parents only if they are bones)
    bones = [i for i, m in models.items() if m["kind"] in ("LimbNode", "Root", "Limb")]
    if not bones:
        bones = [i for i in models if any(cn["model"] == i for cn in cnodes.values())]
    bset = set(bones)
    names = [models[i]["name"] for i in bones]
    parents = [bones.index(parent[i]) if parent.get(i) in bset else -1 for i in bones]
    names, parents, order = _topo(names, parents)
    bones = [bones[o] for o in order]

    # static props + curves
    anim = {i: {} for i in bones}
    times = []
    for cn in cnodes.values():
        if cn["model"] in anim and cn["attr"] in ("Lcl Translation", "Lcl Rotation", "Lcl Scaling"):
            anim[cn["model"]][cn["attr"]] = cn
            for t, _v in cn["curves"].values():
                if len(t):
                    times.append((t[0], t[-1]))
    t0 = min(a for a, _ in times) if times else 0.0
    t1 = max(b for _, b in times) if times else 0.0
    nf = max(1, int(round((t1 - t0) * FPS)) + 1)
    ts = t0 + np.arange(nf) / FPS

    def prop3(p, key, dflt):
        v = p.get(key)
        return np.array([float(x) for x in v[:3]], np.float64) if v and len(v) >= 3 else np.array(dflt, np.float64)

    def channel(i, attr, dflt):
        cn = anim[i].get(attr)
        base = prop3(models[i]["p"], attr, dflt)
        out = np.tile(base, (nf, 1))
        if cn is not None:
            for k, ax in enumerate(("d|X", "d|Y", "d|Z")):
                cv = cn["curves"].get(ax)
                if cv is not None and len(cv[0]):
                    out[:, k] = np.interp(ts, cv[0], cv[1])
                else:
                    dv = cn["p"].get(ax)
                    if dv:
                        out[:, k] = float(dv[0])
        return out, base

    nb = len(bones)
    loc = np.zeros((nf, nb, 4, 4))
    rest_loc = np.zeros((nb, 4, 4))
    for k, i in enumerate(bones):
        p = models[i]["p"]
        pre = euler_m(prop3(p, "PreRotation", (0, 0, 0)))
        post = euler_m(prop3(p, "PostRotation", (0, 0, 0)))
        order_code = int((p.get("RotationOrder") or [0])[0])
        order_s = ["XYZ", "XZY", "YZX", "YXZ", "ZXY", "ZYX"][order_code if 0 <= order_code < 6 else 0]
        T, T0 = channel(i, "Lcl Translation", (0, 0, 0))
        R, R0 = channel(i, "Lcl Rotation", (0, 0, 0))
        S, S0 = channel(i, "Lcl Scaling", (1, 1, 1))
        for f in range(nf):
            m = np.eye(4)
            m[:3, :3] = pre @ euler_m(R[f], order_s) @ post.T @ np.diag(S[f])
            m[:3, 3] = T[f]
            loc[f, k] = m
        m = np.eye(4)
        m[:3, :3] = pre @ euler_m(R0, order_s) @ post.T @ np.diag(S0)
        m[:3, 3] = T0
        rest_loc[k] = m
    frames = _globals_from_locals(parents, loc)
    rest = _globals_from_locals(parents, rest_loc)
    if not rest_pose_ok(names, rest):
        # the static values are not a T / A pose: try the skin's bind pose,
        # but only if it agrees with the node hierarchy; else frame 0
        bind = rest.copy()
        got = False
        for pose in objs.findall("Pose"):
            for pn in pose.findall("PoseNode"):
                nid = pn.find("Node")
                mat = pn.find("Matrix")
                if nid is not None and mat is not None and nid.props[0] in bones:
                    bind[bones.index(nid.props[0])] = np.asarray(mat.props[0], np.float64).reshape(4, 4).T
                    got = True
        if got and rest_pose_ok(names, bind) and _consistent(parents, bind, rest_loc):
            rest = bind
        elif not rest_pose_ok(names, rest):
            rest = frames[0].copy()
    scale = unit / 100.0                       # FBX centimetres -> metres
    conv = np.eye(4)
    if up == 2:                                # Z-up file -> Y-up
        conv[:3, :3] = np.array([[1, 0, 0], [0, 0, 1], [0, -1, 0]], np.float64)
    return SourceAnim(os.path.splitext(os.path.basename(path))[0], names, parents,
                      _normalize(rest, conv, scale), _normalize(frames, conv, scale))


def rest_pose_ok(names, rest):
    """True when `rest` looks like a standing T / A pose (legs down, spine up)."""
    idx = {canon(n): i for i, n in enumerate(names)}
    try:
        p = rest[:, :3, 3]
        leg = p[idx["leftleg"]] - p[idx["leftupleg"]]
        spine = p[idx["spine1"] if "spine1" in idx else idx["neck"]] - p[idx["hips"]]
    except KeyError:
        return True
    ln, ls = np.linalg.norm(leg), np.linalg.norm(spine)
    if ln < 1e-6 or ls < 1e-6:
        return False
    up = np.array([0.0, 1.0, 0.0])
    return float(np.dot(leg / ln, -up)) > 0.8 and float(np.dot(spine / ls, up)) > 0.8


def _consistent(parents, glob, loc, tol=0.05):
    """Bind-pose globals agree with the hierarchy's local offsets (lengths)."""
    for i, p in enumerate(parents):
        if p < 0:
            continue
        a = np.linalg.norm((np.linalg.inv(glob[p]) @ glob[i])[:3, 3])
        b = np.linalg.norm(loc[i, :3, 3])
        if abs(a - b) > tol * max(1.0, b):
            return False
        lt = (np.linalg.inv(glob[p]) @ glob[i])[:3, 3]
        if np.linalg.norm(lt - loc[i, :3, 3]) > tol * max(1.0, b) + 1e-6:
            return False
    return True


def _normalize(g, conv, scale):
    g = conv @ g
    out = g.copy()
    r = out[..., :3, :3].reshape(-1, 3, 3)
    for i in range(len(r)):
        r[i] = _orthonormal(r[i])
    out[..., :3, :3] = r.reshape(out[..., :3, :3].shape)
    out[..., :3, 3] = g[..., :3, 3] * scale
    return out


# --------------------------------------------------------------------------- glTF / GLB
def load_gltf(path, index=None):
    """Returns a list of SourceAnim (one per glTF animation)."""
    with open(path, "rb") as f:
        data = f.read()
    bin_chunk = b""
    if data[:4] == b"glTF":
        off = 12
        js = None
        while off < len(data):
            ln, typ = struct.unpack_from("<II", data, off)
            chunk = data[off + 8:off + 8 + ln]
            if typ == 0x4E4F534A:
                js = json.loads(chunk.decode("utf-8"))
            elif typ == 0x004E4942:
                bin_chunk = chunk
            off += 8 + ln
        doc = js
    else:
        doc = json.loads(data.decode("utf-8"))
    base = os.path.dirname(path)
    buffers = []
    for b in doc.get("buffers", []):
        uri = b.get("uri")
        if uri is None:
            buffers.append(bin_chunk)
        elif uri.startswith("data:"):
            import base64
            buffers.append(base64.b64decode(uri.split(",", 1)[1]))
        else:
            with open(os.path.join(base, uri), "rb") as f:
                buffers.append(f.read())

    def acc(i):
        a = doc["accessors"][i]
        bv = doc["bufferViews"][a["bufferView"]]
        comp = {5126: "<f4", 5123: "<u2", 5121: "u1", 5125: "<u4", 5122: "<i2", 5120: "i1"}[a["componentType"]]
        n = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}[a["type"]]
        start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", 0)
        item = np.dtype(comp).itemsize * n
        raw = buffers[bv["buffer"]]
        if stride and stride != item:
            rows = [np.frombuffer(raw, comp, n, start + k * stride) for k in range(a["count"])]
            arr = np.array(rows)
        else:
            arr = np.frombuffer(raw, comp, a["count"] * n, start).reshape(a["count"], n)
        arr = arr.astype(np.float64)
        if a.get("normalized"):
            arr /= {5121: 255.0, 5123: 65535.0, 5120: 127.0, 5122: 32767.0}.get(a["componentType"], 1.0)
        return arr
    nodes = doc.get("nodes", [])
    skins = doc.get("skins", [])
    if skins:
        joints = list(skins[0]["joints"])
    else:
        animated = {ch["target"].get("node") for a in doc.get("animations", []) for ch in a["channels"]}
        joints = sorted(i for i in animated if i is not None)
    if not joints:
        raise ValueError("no skeleton in glTF file")
    jset = set(joints)
    par = {}
    for i, n in enumerate(nodes):
        for c in n.get("children", []):
            par[c] = i
    names = [nodes[j].get("name", f"joint{j}") for j in joints]
    parents = [joints.index(par[j]) if par.get(j) in jset else -1 for j in joints]
    names, parents, order = _topo(names, parents)
    joints = [joints[o] for o in order]

    def trs(n):
        if "matrix" in n:
            m = np.array(n["matrix"], np.float64).reshape(4, 4).T
            return m[:3, 3].copy(), m_to_q(_orthonormal(m[:3, :3])), np.linalg.norm(m[:3, :3], axis=0)
        t = np.array(n.get("translation", [0, 0, 0]), np.float64)
        r = n.get("rotation", [0, 0, 0, 1])
        q = np.array([r[3], r[0], r[1], r[2]], np.float64)
        s = np.array(n.get("scale", [1, 1, 1]), np.float64)
        return t, q, s

    def mat(t, q, s):
        m = np.eye(4)
        m[:3, :3] = q_to_m(q) @ np.diag(s)
        m[:3, 3] = t
        return m
    # world matrix of the skeleton's parent chain (armature object transform)
    def node_world(i):
        m = np.eye(4)
        while i is not None:
            m = mat(*trs(nodes[i])) @ m
            i = par.get(i)
        return m
    root_parent = par.get(joints[0])
    pre = node_world(root_parent) if root_parent is not None else np.eye(4)
    rest_loc = np.array([mat(*trs(nodes[j])) for j in joints])
    rest = pre @ _globals_from_locals(parents, rest_loc)
    if not rest_pose_ok(names, rest) and skins and "inverseBindMatrices" in skins[0]:
        ibm = acc(skins[0]["inverseBindMatrices"]).reshape(-1, 4, 4).transpose(0, 2, 1)
        sj = list(skins[0]["joints"])
        mesh_node = next((i for i, n in enumerate(nodes) if n.get("skin") == 0), None)
        mw = node_world(mesh_node) if mesh_node is not None else np.eye(4)
        bind = rest.copy()
        for k, j in enumerate(joints):
            bind[k] = mw @ np.linalg.inv(ibm[sj.index(j)])
        if rest_pose_ok(names, bind):
            rest = bind
    rest_fallback = not rest_pose_ok(names, rest)
    out = []
    for ai, a in enumerate(doc.get("animations", [])):
        if index is not None and ai != index:
            continue
        chans = {}
        t_end = 0.0
        for ch in a["channels"]:
            node = ch["target"].get("node")
            if node not in jset:
                continue
            smp = a["samplers"][ch["sampler"]]
            tt = acc(smp["input"])[:, 0]
            vv = acc(smp["output"])
            if smp.get("interpolation") == "CUBICSPLINE":
                vv = vv.reshape(len(tt), 3, -1)[:, 1]
            chans[(node, ch["target"]["path"])] = (tt, vv)
            t_end = max(t_end, float(tt[-1]) if len(tt) else 0.0)
        nf = max(1, int(round(t_end * FPS)) + 1)
        ts = np.arange(nf) / FPS
        loc = np.zeros((nf, len(joints), 4, 4))
        for k, j in enumerate(joints):
            t0, q0, s0 = trs(nodes[j])
            T = np.tile(t0, (nf, 1))
            Q = np.tile(q0, (nf, 1))
            S = np.tile(s0, (nf, 1))
            if (j, "translation") in chans:
                tt, vv = chans[(j, "translation")]
                T = np.stack([np.interp(ts, tt, vv[:, c]) for c in range(3)], 1)
            if (j, "scale") in chans:
                tt, vv = chans[(j, "scale")]
                S = np.stack([np.interp(ts, tt, vv[:, c]) for c in range(3)], 1)
            if (j, "rotation") in chans:
                tt, vv = chans[(j, "rotation")]
                qq = np.stack([vv[:, 3], vv[:, 0], vv[:, 1], vv[:, 2]], 1)
                for i in range(1, len(qq)):
                    if np.dot(qq[i], qq[i - 1]) < 0:
                        qq[i] = -qq[i]
                Q = qnorm(np.stack([np.interp(ts, tt, qq[:, c]) for c in range(4)], 1))
            for f in range(nf):
                loc[f, k] = mat(T[f], Q[f], S[f])
        frames = pre @ _globals_from_locals(parents, loc)
        nm = a.get("name") or f"{os.path.splitext(os.path.basename(path))[0]}_{ai}"
        r = frames[0] if rest_fallback else rest
        out.append(SourceAnim(nm, names, parents, _normalize(r, np.eye(4), 1.0),
                              _normalize(frames, np.eye(4), 1.0)))
    if not out:
        raise ValueError("glTF file has no animations")
    return out


# --------------------------------------------------------------------------- BVH
def load_bvh(path):
    with open(path, encoding="utf-8", errors="replace") as f:
        toks = f.read().split()
    i = 0
    names, parents, offsets, chans = [], [], [], []
    stack = []
    while i < len(toks):
        t = toks[i]
        if t in ("ROOT", "JOINT"):
            names.append(toks[i + 1])
            parents.append(stack[-1] if stack else -1)
            offsets.append(None)
            chans.append([])
            stack.append(len(names) - 1)
            i += 2
        elif t == "End":
            depth = 0
            i += 2
            while i < len(toks):
                if toks[i] == "{":
                    depth += 1
                elif toks[i] == "}":
                    depth -= 1
                    if depth == 0:
                        i += 1
                        break
                i += 1
            continue
        elif t == "OFFSET":
            offsets[stack[-1]] = [float(x) for x in toks[i + 1:i + 4]]
            i += 4
        elif t == "CHANNELS":
            n = int(toks[i + 1])
            chans[stack[-1]] = toks[i + 2:i + 2 + n]
            i += 2 + n
        elif t == "}":
            stack.pop()
            i += 1
        elif t == "MOTION":
            i += 1
            break
        else:
            i += 1
    nf = int(toks[i + 1])
    ft = float(toks[i + 4])
    i += 5
    total = sum(len(c) for c in chans)
    vals = np.array([float(x) for x in toks[i:i + nf * total]], np.float64).reshape(nf, total)
    nb = len(names)
    loc = np.zeros((nf, nb, 4, 4))
    rest_loc = np.zeros((nb, 4, 4))
    col = 0
    for b in range(nb):
        off = np.array(offsets[b] or [0, 0, 0])
        rest_loc[b] = np.eye(4)
        rest_loc[b, :3, 3] = off
        T = np.tile(off, (nf, 1))
        rot = []
        for c in chans[b]:
            v = vals[:, col]
            col += 1
            cl = c.lower()
            if cl.endswith("position"):
                T[:, "xyz".index(cl[0])] = v
            elif cl.endswith("rotation"):
                rot.append((cl[0].upper(), v))
        for f in range(nf):
            m = np.eye(3)
            for ax, v in rot:                           # BVH channels are listed outer -> inner
                m = m @ euler_m([v[f]], ax)
            loc[f, b, :3, :3] = m
            loc[f, b, :3, 3] = T[f]
            loc[f, b, 3, 3] = 1.0
    # BVH root channels are absolute: put the rest pose where the motion starts,
    # standing on y = 0 (rest = all channels zero, i.e. the T-pose of OFFSETs)
    root = [b for b in range(nb) if parents[b] < 0]
    rest = _globals_from_locals(parents, rest_loc)
    if root:
        r0 = root[0]
        lift = -float(rest[:, 1, 3].min())
        rest_loc[r0, 0, 3] = loc[0, r0, 0, 3]
        rest_loc[r0, 1, 3] = rest_loc[r0, 1, 3] + lift
        rest_loc[r0, 2, 3] = loc[0, r0, 2, 3]
        rest = _globals_from_locals(parents, rest_loc)
    frames = _globals_from_locals(parents, loc)
    return SourceAnim(os.path.splitext(os.path.basename(path))[0], names, parents,
                      _normalize(rest, np.eye(4), 1.0), _normalize(frames, np.eye(4), 1.0),
                      fps=1.0 / ft if ft > 0 else FPS)


def load_any(path):
    ext = os.path.splitext(path)[1].lower()
    if ext == ".fbx":
        return [load_fbx(path)]
    if ext in (".glb", ".gltf"):
        return load_gltf(path)
    if ext == ".bvh":
        return [load_bvh(path)]
    raise ValueError(f"unsupported animation file {ext} (use .fbx, .glb, .gltf or .bvh)")


# =========================================================================== clips
class Clip:
    """Local pose rotations relative to the target rest pose, per frame."""

    def __init__(self, name, rot, hips, fps=FPS, loop=True, speed=0.0, events=None):
        self.name = name
        self.rot = np.asarray(rot, np.float64)          # (F, B, 4)
        self.hips = np.asarray(hips, np.float64)        # (F, 3)
        self.fps = float(fps)
        self.loop = loop
        self.speed = float(speed)                       # natural ground speed (m/s), 0 for in-place
        self.events = dict(events or {})                # name -> normalised time (0..1)

    @property
    def duration(self):
        return max(1e-3, (len(self.rot) - (0 if self.loop else 1)) / self.fps)

    def sample(self, t):
        n = len(self.rot)
        if n == 1:
            return self.rot[0], self.hips[0]
        f = t * self.fps
        if self.loop:
            f = f % n
            a = int(f)
            b = (a + 1) % n
        else:
            f = min(max(f, 0.0), n - 1.0)
            a = int(f)
            b = min(a + 1, n - 1)
        u = f - a
        qa, qb = self.rot[a], self.rot[b]
        sgn = np.sign(np.sum(qa * qb, axis=1, keepdims=True))
        sgn[sgn == 0] = 1.0
        q = qnorm(qa * (1 - u) + qb * sgn * u)
        return q, self.hips[a] * (1 - u) + self.hips[b] * u


def world_to_local(model, Wq, hips_pos):
    """Target world rotations (F,B,4) -> local pose quats relative to the rest frames."""
    rl = getattr(model, "_rl_q", None)
    if rl is None:
        rl = model._rl_q = m_to_q(model.rest_local[:, :3, :3])
    nf, nb = Wq.shape[:2]
    P = np.zeros_like(Wq)
    for i in range(nb):
        p = model.parents[i]
        base = rl[i] if p < 0 else qmul(Wq[:, p], rl[i])
        P[:, i] = qmul(qinv(np.broadcast_to(base, Wq[:, i].shape)), Wq[:, i])
    return qnorm(P), hips_pos


def retarget(src, model, name=None, loop=True, in_place=True):
    """SourceAnim -> Clip for this character's skeleton."""
    nb = len(model.bone_names)
    tmap = {}
    s_index = {}
    for k, n in enumerate(src.names):
        c = canon(n)
        if c and c not in s_index:
            s_index[c] = k
    for i, n in enumerate(model.bone_names):
        k = s_index.get(canon(n))
        if k is not None:
            tmap[i] = k
    hips_t = model.bone("mixamorig:Hips")
    if hips_t not in tmap:
        raise ValueError(f"'{src.name}': no hips bone found - is it a humanoid (Mixamo / BVH) animation?")
    turn = np.diag([-1.0, 1.0, -1.0])                  # sources face +Z, characters face -Z
    srest_r = np.einsum("ij,bjk->bik", turn, src.rest[:, :3, :3])
    srest_p = src.rest[:, :3, 3] @ turn.T
    sfr_r = np.einsum("ij,fbjk->fbik", turn, src.frames[:, :, :3, :3])
    sfr_p = src.frames[:, :, :3, 3] @ turn.T
    Ws0 = m_to_q(srest_r)
    Ws = m_to_q(sfr_r)
    Wt0 = m_to_q(model.rest_world[:, :3, :3])
    children = [[] for _ in range(nb)]
    for i, p in enumerate(model.parents):
        if p >= 0:
            children[p].append(i)

    def mapped_child(i):
        # follow the main chain: first descendant that is mapped
        best = None
        for c in children[i]:
            if c in tmap:
                cn = canon(model.bone_names[c])
                if best is None or ("spine" in cn or "neck" in cn or "head" in cn or "forearm" in cn
                                    or cn.endswith("leg") or "hand" in cn and "hand" not in canon(model.bone_names[i])
                                    or "foot" in cn or "toebase" in cn):
                    best = c
        return best
    C = np.tile([1.0, 0, 0, 0], (nb, 1))
    for i in range(nb):
        p = model.parents[i]
        C[i] = C[p] if p >= 0 else [1.0, 0, 0, 0]
        if i not in tmap:
            continue
        c = mapped_child(i)
        if c is None:
            continue
        if p < 0:
            continue                     # the pelvis keeps its orientation (both rigs stand upright)
        dt = model.heads[c] - model.heads[i]
        ds = srest_p[tmap[c]] - srest_p[tmap[i]]
        if np.linalg.norm(dt) > 1e-5 and np.linalg.norm(ds) > 1e-5:
            q = qbetween(dt, ds)
            ang = math.degrees(2 * math.acos(min(1.0, abs(float(q[0])))))
            cn = canon(model.bone_names[i])
            torso = cn.startswith("spine") or cn in ("neck", "head")
            if not (torso and ang > 50.0):       # odd torso layouts: trust the parent instead
                C[i] = q
    nf = len(src.frames)
    W = np.zeros((nf, nb, 4))
    for i in range(nb):
        p = model.parents[i]
        if i in tmap:
            k = tmap[i]
            D = qmul(Ws[:, k], qinv(Ws0[k]))
            W[:, i] = qmul(D, np.broadcast_to(qmul(C[i], Wt0[i]), (nf, 4)))
        else:
            rl = m_to_q(model.rest_local[i, :3, :3])
            W[:, i] = qmul(W[:, p], np.broadcast_to(rl, (nf, 4))) if p >= 0 else Wt0[i]
    k = tmap[hips_t]
    src_h = max(1e-3, float(srest_p[k][1] - min(0.0, float(srest_p[:, 1].min()))))
    ratio = model.metrics.get("hip_height", 0.95) / src_h
    hp = (sfr_p[:, k] - srest_p[k]) * ratio
    speed = 0.0
    if nf > 1:
        dxz = hp[-1, [0, 2]] - hp[0, [0, 2]]
        speed = float(np.linalg.norm(dxz)) / max(1e-3, (nf - 1) / src.fps)
    if in_place and nf > 1:
        lin = np.linspace(0.0, 1.0, nf)[:, None]
        hp[:, 0] -= hp[0, 0] + (hp[-1, 0] - hp[0, 0]) * lin[:, 0]
        hp[:, 2] -= hp[0, 2] + (hp[-1, 2] - hp[0, 2]) * lin[:, 0]
    if loop and nf > 3:                        # exported loops often repeat the first frame at the end
        d = np.abs(np.sum(W[0] * W[-1], axis=1))
        if float(d.min()) > 0.9999 and np.linalg.norm(hp[0] - hp[-1]) < 0.01:
            W, hp, nf = W[:-1], hp[:-1], nf - 1
    P, _ = world_to_local(model, W, hp)
    for f in range(1, nf):                     # keep quaternions continuous
        s = np.sign(np.sum(P[f] * P[f - 1], axis=1))
        P[f][s < 0] *= -1
    return Clip(name or src.name, P, hp, fps=src.fps, loop=loop, speed=speed)


# =========================================================================== procedural clips
class _Proc:
    """Builds clips from rotations about character axes (X = right->left?, see below).

    Character frame: faces -Z, +Y up.  A rotation about +X by a positive angle
    swings a hanging limb forward.  Rotations are applied in hierarchy order and
    are relative to the parent's animated frame."""

    def __init__(self, model):
        self.m = model
        self.nb = len(model.bone_names)
        self.rest_q = m_to_q(model.rest_world[:, :3, :3])
        self.rl = m_to_q(model.rest_local[:, :3, :3])
        model._rl_q = self.rl
        self.idx = {canon(n): i for i, n in enumerate(model.bone_names)}
        # A-pose -> arms down: per-arm rotation in world space
        self.arm_down = {}
        for side, sx in (("left", -1.0), ("right", 1.0)):
            a = self.idx.get(side + "arm")
            if a is not None:
                d = model.tails[a] - model.heads[a]
                self.arm_down[a] = qbetween(d, np.array([sx * 0.17, -1.0, 0.04]))

    def b(self, name):
        return self.idx.get(name, -1)

    def poses(self, frames):
        """frames: list of (rots, hips); rots = {bone canon: [(axis(3,), degrees), ...]}.
        Returns (P (F,B,4), hips (F,3)).  Vectorised over frames."""
        nf = len(frames)
        ident = np.array([1.0, 0.0, 0.0, 0.0])
        extra = np.tile(ident, (nf, self.nb, 1))
        has = np.zeros(self.nb, bool)
        for f, (rots, _h) in enumerate(frames):
            for name, lst in rots.items():
                i = self.idx.get(name)
                if i is None:
                    continue
                q = ident
                for axis, deg in lst:
                    q = qmul(qaxis(axis, math.radians(deg)), q)
                extra[f, i] = q
                has[i] = True
        for i, d in self.arm_down.items():
            extra[:, i] = qmul(extra[:, i], np.broadcast_to(d, (nf, 4)))
            has[i] = True
        W = np.zeros((nf, self.nb, 4))
        for i in range(self.nb):
            p = self.m.parents[i]
            inherit = np.broadcast_to(self.rest_q[i], (nf, 4)) if p < 0 else \
                qmul(W[:, p], np.broadcast_to(self.rl[i], (nf, 4)))
            if has[i]:
                e = extra[:, i]
                if p >= 0:          # the extra rotation is about axes carried by the parent's motion
                    carry = qmul(W[:, p], np.broadcast_to(qinv(self.rest_q[p]), (nf, 4)))
                    e = qmul(qmul(carry, e), qinv(carry))
                W[:, i] = qmul(e, inherit)
            else:
                W[:, i] = inherit
        hips = np.array([h for _r, h in frames], np.float64)
        return world_to_local(self.m, W, hips)

    def clip(self, name, fn, duration, loop=True, speed=0.0, events=None):
        nf = max(2, int(round(duration * FPS)) + (0 if loop else 1))
        frames = [fn(f / float(nf if loop else nf - 1)) for f in range(nf)]
        rot, hips = self.poses(frames)
        return Clip(name, rot, hips, loop=loop, speed=speed, events=events)


X, Y, Z = np.array([1.0, 0, 0]), np.array([0, 1.0, 0]), np.array([0, 0, 1.0])


def _s(u, k=1.0, ph=0.0):
    return math.sin(2 * math.pi * (u * k + ph))


def _c(u, k=1.0, ph=0.0):
    return math.cos(2 * math.pi * (u * k + ph))


def _smooth(x):
    x = min(1.0, max(0.0, x))
    return x * x * (3 - 2 * x)


def _gait(u, hip, knee, arm, elbow, bob, lean, twist):
    rots = {}
    for side, ph in (("left", 0.0), ("right", 0.5)):
        s = _s(u, 1, ph)
        c = _c(u, 1, ph)
        lift = max(0.0, c) ** 1.2                    # swing phase (leg moving forward)
        rots[side + "upleg"] = [(X, hip * s + 6.0 * lift)]
        kn = 6.0 + knee * lift + 0.18 * knee * max(0.0, -s)
        rots[side + "leg"] = [(X, -kn)]
        rots[side + "foot"] = [(X, 0.55 * kn - hip * 0.35 * s - 8.0 * max(0.0, -c) * max(0.0, s))]
        sa = -s                                        # arms opposite to legs
        rots[side + "arm"] = [(X, arm * sa)]
        rots[side + "forearm"] = [(X, elbow + 0.5 * elbow * max(0.0, sa))]
    rots["hips"] = [(Y, twist * _s(u)), (X, -lean * 0.4)]
    rots["spine"] = [(Y, -twist * 0.6 * _s(u)), (X, -lean * 0.4)]
    rots["spine1"] = [(Y, -twist * 0.4 * _s(u)), (X, -lean * 0.2)]
    rots["neck"] = [(Y, twist * 0.5 * _s(u)), (X, lean * 0.5)]
    rots["head"] = [(X, lean * 0.3)]
    y = -bob * 0.5 + bob * abs(_c(u, 1.0))
    return rots, (0.012 * _s(u), y - 0.01, 0.0)


def procedural_clips(model):
    pr = _Proc(model)
    leg = model.metrics.get("leg_length", 0.85)
    walk_speed = 1.25 * leg / 0.85
    run_speed = 3.6 * leg / 0.85
    clips = {}
    clips["idle"] = pr.clip("idle", lambda u: ({
        "spine": [(X, 1.2 * _s(u))], "spine1": [(X, 0.8 * _s(u, 1, 0.1))], "neck": [(X, -0.8 * _s(u, 1, 0.2))],
        "head": [(Y, 4.0 * _s(u, 0.5)), (X, 1.0 * _s(u, 1, 0.3))],
        "leftarm": [(Z, 2.0 + 1.0 * _s(u)), (X, 3.0)], "rightarm": [(Z, -2.0 - 1.0 * _s(u)), (X, 3.0)],
        "leftforearm": [(X, 10.0)], "rightforearm": [(X, 10.0)],
        "hips": [(Z, 1.5 * _s(u, 0.5))], "leftupleg": [(Z, -1.5 * _s(u, 0.5))],
        "rightupleg": [(Z, -1.5 * _s(u, 0.5))], "leftleg": [(X, -3.0)], "rightleg": [(X, -3.0)],
        "leftfoot": [(X, 1.5)], "rightfoot": [(X, 1.5)],
    }, (0.01 * _s(u, 0.5), -0.004 + 0.003 * _s(u), 0.0)), 3.2, speed=0.0)
    clips["walk"] = pr.clip("walk", lambda u: _gait(u, 24.0, 48.0, 16.0, 14.0, 0.028, 3.0, 5.0),
                            1.05, speed=walk_speed)
    clips["run"] = pr.clip("run", lambda u: _gait(u, 40.0, 90.0, 38.0, 70.0, 0.06, 12.0, 9.0),
                           0.68, speed=run_speed)

    def attack(u):
        wind = _smooth(u / 0.35)
        hit = _smooth((u - 0.35) / 0.15)
        back = _smooth((u - 0.6) / 0.4)
        a = wind * (1 - hit) * (1 - back)
        b = hit * (1 - back)
        return ({
            "spine": [(Y, 22 * a - 28 * b)], "spine1": [(Y, 10 * a - 14 * b), (X, -6 * b)],
            "rightarm": [(X, 35 * a + 80 * b), (Z, -40 * a + 10 * b)],
            "rightforearm": [(X, 95 * a + 10 * b)],
            "leftarm": [(X, 25 * (a + b)), (Z, 20 * (a + b))], "leftforearm": [(X, 70 * (a + b) + 10)],
            "leftupleg": [(X, 12 * (a + b))], "rightupleg": [(X, -10 * (a + b))],
            "leftleg": [(X, -12 * (a + b))], "rightleg": [(X, -6)],
            "head": [(Y, -12 * a + 10 * b)],
        }, (0.0, -0.03 * (a + b), -0.08 * b))
    clips["attack"] = pr.clip("attack", attack, 0.9, loop=False, events={"hit": 0.45})

    def shoot(u):
        up = _smooth(u / 0.2) * (1 - _smooth((u - 0.75) / 0.25))
        kick = max(0.0, 1.0 - abs(u - 0.42) / 0.08)
        return ({"rightarm": [(X, 82 * up + 8 * kick), (Z, -4 * up)], "rightforearm": [(X, 6 * up)],
                 "spine1": [(Y, -8 * up)], "head": [(Y, 6 * up)], "spine": [(X, 4 * kick)],
                 "leftarm": [(X, 5 * up)], "leftforearm": [(X, 20)]}, (0.0, 0.0, 0.0))
    clips["shoot"] = pr.clip("shoot", shoot, 0.85, loop=False, events={"hit": 0.42})

    def hit(u):
        k = math.sin(math.pi * min(1.0, u / 0.9)) * (1.0 - _smooth((u - 0.5) / 0.5) * 0.3)
        return ({"spine": [(X, 14 * k)], "spine1": [(X, 10 * k)], "neck": [(X, 8 * k)], "head": [(X, 10 * k)],
                 "leftarm": [(Z, 18 * k), (X, 12 * k)], "rightarm": [(Z, -18 * k), (X, 12 * k)],
                 "leftforearm": [(X, 30 * k + 8)], "rightforearm": [(X, 30 * k + 8)],
                 "leftleg": [(X, -10 * k)], "rightleg": [(X, -10 * k)]}, (0.0, -0.04 * k, 0.07 * k))
    clips["hit"] = pr.clip("hit", hit, 0.45, loop=False)

    def death(u):
        k = _smooth(u / 0.75)
        bend = _smooth(u / 0.35) * (1 - _smooth((u - 0.45) / 0.3))
        hh = model.metrics.get("hip_height", 0.95)
        return ({"hips": [(X, 88 * k)], "spine": [(X, -14 * bend + 4 * k)], "spine1": [(X, -8 * bend)],
                 "head": [(X, -12 * bend + 14 * k), (Y, 18 * k)],
                 "leftupleg": [(X, 30 * bend + 6 * k), (Z, -8 * k)], "rightupleg": [(X, 18 * bend + 2 * k), (Z, 8 * k)],
                 "leftleg": [(X, -70 * bend - 8 * k)], "rightleg": [(X, -40 * bend - 12 * k)],
                 "leftarm": [(Z, 35 * k), (X, -20 * k)], "rightarm": [(Z, -55 * k), (X, -10 * k)],
                 "leftforearm": [(X, 20 * k)], "rightforearm": [(X, 35 * k)]},
                (0.0, -(hh - 0.13) * k - 0.12 * bend, 0.35 * k))
    clips["death"] = pr.clip("death", death, 1.25, loop=False)

    def wave(u):
        up = _smooth(u / 0.2) * (1 - _smooth((u - 0.8) / 0.2))
        return ({"rightarm": [(Z, -100 * up), (X, 10 * up)], "rightforearm": [(Z, -30 * up), (X, 25 * up + 18 * up * _s(u, 3))],
                 "head": [(Y, -8 * up)], "leftforearm": [(X, 10)]}, (0.0, 0.0, 0.0))
    clips["wave"] = pr.clip("wave", wave, 2.0, loop=False)
    return clips


# =========================================================================== runtime
SLOTS = ("idle", "walk", "run", "sprint", "attack", "shoot", "hit", "death", "talk", "wave")

# --------------------------------------------------------------------------- built-in CC0 library
_LIB = {}


def load_library(path):
    """assets/anims/library.npz -> {clip name: SourceAnim} (Quaternius UAL 1+2, CC0)."""
    key = os.path.abspath(path)
    if key in _LIB:
        return _LIB[key]
    out = {}
    if os.path.isfile(path):
        z = np.load(path)
        index = json.loads(bytes(z["index.json"]).decode("utf-8"))
        rigs = {r["id"]: r for r in index["rigs"]}
        for name, info in index["clips"].items():
            rig = rigs[info["rig"]]
            q = z[name + ".q"].astype(np.float64)
            pos = z[name + ".p"].astype(np.float64)
            fr = np.zeros(q.shape[:2] + (4, 4))
            fr[..., :3, :3] = q_to_m(q)
            fr[..., :3, 3] = pos
            fr[..., 3, 3] = 1.0
            out[name] = SourceAnim(name, rig["names"], rig["parents"], z[rig["id"] + ".rest"].astype(np.float64),
                                   fr, fps=info.get("fps", FPS))
    _LIB[key] = out
    return out


# Which built-in clips a character uses, per moveset.  Lists = variants (one is
# picked per character for loops, per play for one-shots).
MOVESETS = {
    "civilian": {"idle": ["Idle_Loop", "Idle_Loop", "Idle_FoldArms_Loop", "Idle_TalkingPhone_Loop"],
                 "walk": ["Walk_Loop", "Walk_Loop", "Walk_Formal_Loop"], "run": ["Jog_Fwd_Loop"],
                 "sprint": ["Sprint_Loop"], "talk": ["Idle_Talking_Loop"], "wave": ["@wave"],
                 "attack": ["Punch_Jab", "Punch_Cross"], "hit": ["Hit_Chest", "Hit_Head"], "death": ["Death01"],
                 "yes": ["Yes"], "no": ["Idle_No_Loop"]},
    "brawler": {"idle": ["Idle_Loop"], "walk": ["Walk_Loop"], "run": ["Jog_Fwd_Loop"], "sprint": ["Sprint_Loop"],
                "attack": ["Punch_Jab", "Punch_Cross", "Melee_Hook"], "hit": ["Hit_Chest", "Hit_Head", "Hit_Knockback"],
                "death": ["Death01"], "talk": ["Idle_Talking_Loop"], "wave": ["@wave"]},
    "pistol": {"idle": ["Idle_Loop"], "walk": ["Walk_Loop"], "run": ["Jog_Fwd_Loop"], "sprint": ["Sprint_Loop"],
               "upper": ["Pistol_Idle_Loop"], "shoot": ["Pistol_Shoot"], "reload": ["Pistol_Reload"],
               "attack": ["Pistol_Shoot"], "hit": ["Hit_Chest", "Hit_Head"], "death": ["Death01"],
               "talk": ["Idle_Talking_Loop"], "wave": ["@wave"]},
    "zombie": {"idle": ["Zombie_Idle_Loop"], "walk": ["Zombie_Walk_Fwd_Loop"], "run": ["Zombie_Walk_Fwd_Loop"],
               "sprint": ["Zombie_Walk_Fwd_Loop"], "attack": ["Zombie_Scratch"], "hit": ["Hit_Knockback", "Hit_Chest"],
               "death": ["Death01"], "talk": ["Zombie_Idle_Loop"], "wave": ["Zombie_Idle_Loop"]},
    "sword": {"idle": ["Sword_Idle"], "walk": ["Walk_Loop"], "run": ["Jog_Fwd_Loop"], "sprint": ["Sprint_Loop"],
              "attack": ["Sword_Attack"], "hit": ["Hit_Chest", "Hit_Head"], "death": ["Death01"],
              "talk": ["Idle_Talking_Loop"], "wave": ["@wave"]},
}
LOOP_SLOTS = ("idle", "walk", "run", "sprint", "upper", "talk")
UPPER_SLOTS = ("shoot", "reload", "upper")
EVENTS = {"Punch_Jab": {"hit": 0.38}, "Punch_Cross": {"hit": 0.42}, "Melee_Hook": {"hit": 0.55},
          "Zombie_Scratch": {"hit": 0.5}, "Sword_Attack": {"hit": 0.45}, "Pistol_Shoot": {"hit": 0.12}}


def clipset(model, moveset, seed, library, overrides=None):
    """slot -> [Clip] for this character.  `overrides` (slot -> Clip) replace slots."""
    import random as _r
    rng = _r.Random(seed)
    ms = MOVESETS.get(moveset) or MOVESETS["civilian"]
    out = {}
    cache = getattr(model, "_clip_cache", None)
    if cache is None:
        cache = model._clip_cache = {}
    for slot, names in ms.items():
        if names == ["@wave"]:
            continue
        names = [n for n in names if n in library]
        if not names:
            continue
        if slot in LOOP_SLOTS:
            names = [rng.choice(names)]            # one variant per character
        clips = []
        for n in names:
            key = (n, slot in LOOP_SLOTS)
            c = cache.get(key)
            if c is None:
                c = retarget(library[n], model, name=n, loop=slot in LOOP_SLOTS, in_place=True)
                c.events = dict(EVENTS.get(n, {}))
                if slot != "death":
                    ground_fit(model, c)
                if slot in ("walk", "run", "sprint"):
                    measure_gait(model, c)
                cache[key] = c
            clips.append(c)
        out[slot] = clips
    if ms.get("wave") == ["@wave"] and out.get("idle"):
        c = cache.get(("@wave", True))
        if c is None:
            base = out["idle"][0]
            if "Idle_Loop" in library:                    # neutral arms to start the wave from
                base = cache.get(("Idle_Loop", True))
                if base is None:
                    base = cache[("Idle_Loop", True)] = retarget(library["Idle_Loop"], model, name="Idle_Loop",
                                                                 loop=True, in_place=True)
                    ground_fit(model, base)
            c = cache[("@wave", True)] = make_wave(model, base)
        if c is not None:
            out["wave"] = [c]
    for slot, c in (overrides or {}).items():
        if slot in ("walk", "run", "sprint") and c.loop and not getattr(c, "_measured", False):
            measure_gait(model, c)
        out[slot] = [c]
    return out


def ground_fit(model, clip, max_shift=0.15):
    """Shift the clip's hips so its lowest foot contact sits on the ground
    (different leg proportions otherwise leave feet floating or sinking)."""
    lf, rf = model.bone("mixamorig:LeftFoot"), model.bone("mixamorig:RightFoot")
    if lf < 0 or rf < 0 or len(clip.rot) == 0:
        return clip
    pose = Pose(model)
    ank = model.metrics.get("ankle_height", 0.08)
    lows = []
    for f in range(0, len(clip.rot), max(1, len(clip.rot) // 24)):
        G = pose.fk(clip.rot[f], clip.hips[f])
        lows.append(min(G[lf, 1, 3], G[rf, 1, 3]))
    shift = ank - float(np.percentile(lows, 10))
    if abs(shift) <= max_shift:
        clip.hips = clip.hips.copy()
        clip.hips[:, 1] += shift
    return clip


def measure_gait(model, clip):
    """Natural ground speed of an in-place loop (from the planted foot) and the
    phase where the left foot is furthest forward (used to sync gaits)."""
    pose = Pose(model)
    lf, rf = model.bone("mixamorig:LeftFoot"), model.bone("mixamorig:RightFoot")
    n = len(clip.rot)
    if n < 4 or lf < 0 or rf < 0:
        return clip
    P = np.zeros((n, 2, 3))
    for f in range(n):
        G = pose.fk(clip.rot[f], clip.hips[f])
        P[f, 0], P[f, 1] = G[lf, :3, 3], G[rf, :3, 3]
    vel = []
    for f in range(n):
        g = (f + 1) % n
        k = 0 if P[f, 0, 1] < P[f, 1, 1] else 1               # the lower foot is planted
        if abs(P[f, k, 1] - P[:, k, 1].min()) < 0.03:
            d = P[g, k] - P[f, k]
            vel.append(math.hypot(d[0], d[2]) * clip.fps)
    if vel:
        clip.speed = float(np.median(vel))
    rel = P[:, 0, 2] - 0.5 * (P[:, 0, 2] + P[:, 1, 2])
    clip.phase0 = float(np.argmin(rel)) / n                      # left foot most forward (-Z)
    clip._measured = True
    return clip


def arm_clearance(model, clip, margin=0.035):
    """Extra shoulder abduction (radians) per arm so hands clear wide hips."""
    body = model.parts[0]
    lefts = {i for i, n in enumerate(model.bone_names) if canon(n).startswith(("leftarm", "leftforearm", "lefthand", "leftshoulder"))}
    rights = {i for i, n in enumerate(model.bone_names) if canon(n).startswith(("rightarm", "rightforearm", "righthand", "rightshoulder"))}
    dom = body.joints[np.arange(len(body.joints)), np.argmax(body.weights, axis=1)].astype(np.int64)
    trunk = ~np.isin(dom, list(lefts | rights))
    pts = body.positions[trunk]
    pose = Pose(model)
    out = {}
    for side, hand, arm in (("left", "mixamorig:LeftHand", "mixamorig:LeftArm"),
                            ("right", "mixamorig:RightHand", "mixamorig:RightArm")):
        hi, ai = model.bone(hand), model.bone(arm)
        worst = 1.0
        for f in range(0, len(clip.rot), max(1, len(clip.rot) // 12)):
            G = pose.fk(clip.rot[f], clip.hips[f])
            hp = G[hi, :3, 3]
            band = pts[np.abs(pts[:, 1] - hp[1]) < 0.04]
            band = band[np.abs(band[:, 2] - hp[2]) < 0.12]
            band = band[np.sign(band[:, 0]) == np.sign(hp[0])] if len(band) else band
            if len(band) == 0:
                continue
            worst = min(worst, abs(hp[0]) - float(np.abs(band[:, 0]).max()))
        if worst < margin:
            arm_len = float(model.bone_len[ai] + model.bone_len[model.bone(arm.replace("Arm", "ForeArm"))])
            out[side] = math.asin(min(0.5, (margin - worst) / max(arm_len, 0.3)))
    return out


def _sdir(a, b, t):
    """Great-circle interpolation between unit directions."""
    a = a / np.linalg.norm(a)
    b = b / np.linalg.norm(b)
    d = max(-1.0, min(1.0, float(np.dot(a, b))))
    om = math.acos(d)
    if om < 1e-4:
        return b
    return (math.sin((1 - t) * om) * a + math.sin(t * om) * b) / math.sin(om)


def _via(a, m, b, t):
    """Direction path a -> m -> b (t in 0..1), so a raise can't cut across the body."""
    if t <= 0.0:
        return a
    a, m, b = (v / np.linalg.norm(v) for v in (a, m, b))
    l1 = math.acos(max(-1.0, min(1.0, float(np.dot(a, m)))))
    l2 = math.acos(max(-1.0, min(1.0, float(np.dot(m, b)))))
    s = t * (l1 + l2)
    if s <= l1 and l1 > 1e-6:
        return _sdir(a, m, s / l1)
    return _sdir(m, b, (s - l1) / max(l2, 1e-6))


def make_wave(model, base, duration=2.8, freq=1.9):
    """A friendly right-hand wave built on top of `base` (an idle Clip).

    No CC0 mocap wave exists, so the arm is posed in world space: the upper arm
    rises out to the side (never across the body), the forearm stands up and
    swings side to side in the frontal plane, the palm faces forward.  The raise
    follows a great circle through 'out to the side', so the hand can't pass
    through the torso.  Played on the right arm only (clip.mask)."""
    names = [canon(n) for n in model.bone_names]

    def find(n):
        return names.index(n) if n in names else -1
    sh, ua, fa, hd = find("rightshoulder"), find("rightarm"), find("rightforearm"), find("righthand")
    th = next((i for i, n in enumerate(names) if n.startswith("righthandthumb")), -1)
    mid = next((i for i, n in enumerate(names) if n.startswith("righthandmiddle")), -1)
    if min(ua, fa, hd) < 0:
        return None
    pose = Pose(model)
    rl = model.rest_local[:, :3, :3]
    fps = base.fps
    n = int(duration * fps)
    q0, h0 = base.sample(0.0)
    G = pose.fk(q0, h0)
    R = np.array([1.0 if G[ua, 0, 3] > G[model.parents[ua], 0, 3] else -1.0, 0.0, 0.0])   # character's right
    U = np.array([0.0, 1.0, 0.0])
    F = np.array([0.0, 0.0, -1.0])                                                      # facing -Z
    rot = np.empty((n, len(names), 4))
    hips = np.empty((n, 3))
    mask = np.zeros(len(names))
    for i in (sh, ua, fa, hd):
        if i >= 0:
            mask[i] = 1.0
    for j in range(len(names)):
        p = model.parents[j]
        while p >= 0:
            if p == hd:
                mask[j] = 1.0
                break
            p = model.parents[p]

    fingers = [j for j in range(len(names)) if mask[j] > 0 and j not in (sh, ua, fa, hd)]

    def set_dir(q, b, child, want):
        """Rotate bone b (global swing) so b->child points along `want`."""
        G = pose.fk(q, h)
        cur = G[child, :3, 3] - G[b, :3, 3]
        S = q_to_m(qbetween(cur, want))
        Gb = S @ G[b, :3, :3]
        Gp = G[model.parents[b], :3, :3]
        q[b] = m_to_q(rl[b].T @ Gp.T @ Gb)
    for f in range(n):
        t = f / fps
        q, h = base.sample(t)
        q = q.copy()
        e_in = min(1.0, t / 0.45)
        e_out = min(1.0, max(0.0, (duration - t) / 0.55))
        e = min(e_in, e_out)
        e = e * e * (3 - 2 * e)                                   # smoothstep
        G = pose.fk(q, h)
        d1_idle = G[fa, :3, 3] - G[ua, :3, 3]
        d2_idle = G[hd, :3, 3] - G[fa, :3, 3]
        sway = math.sin(2 * math.pi * freq * max(0.0, t - 0.3)) * min(1.0, max(0.0, t - 0.3) / 0.3)
        d1 = 0.86 * R + 0.12 * U + 0.36 * F                     # elbow ~shoulder height, a bit forward
        th_ = math.radians(12.0 + 20.0 * sway)                    # + = outward
        d2 = math.sin(th_) * R + math.cos(th_) * U + 0.22 * F
        if sh >= 0:                                               # shoulder lifts a little with the arm
            G = pose.fk(q, h)
            cur = G[ua, :3, 3] - G[sh, :3, 3]
            set_dir(q, sh, ua, _sdir(cur, cur / np.linalg.norm(cur) + 0.10 * e * U, 1.0))
        out = R + 0.25 * F                                        # raise / lower via 'out to the side'
        set_dir(q, ua, fa, _via(d1_idle, out, d1, e))
        set_dir(q, fa, hd, _via(d2_idle, out, d2, e))
        # palm forward: twist the forearm about its axis so the thumb points to the midline
        if th >= 0 and mid >= 0 and e > 1e-3:
            q[hd] = (q[hd] * (1 - e) + np.array([1.0, 0, 0, 0]) * e)
            q[hd] /= np.linalg.norm(q[hd])
            G = pose.fk(q, h)
            ax = G[hd, :3, 3] - G[fa, :3, 3]
            ax /= np.linalg.norm(ax)
            tv = G[th, :3, 3] - G[hd, :3, 3]
            tv -= ax * np.dot(tv, ax)
            want = -R - ax * np.dot(-R, ax)
            if np.linalg.norm(tv) > 1e-5 and np.linalg.norm(want) > 1e-5:
                tv /= np.linalg.norm(tv)
                want /= np.linalg.norm(want)
                ang = math.atan2(float(np.dot(np.cross(tv, want), ax)), float(np.dot(tv, want))) * e
                Tw = q_to_m(qaxis(ax, ang))
                Gf = Tw @ G[fa, :3, :3]
                q[fa] = m_to_q(rl[fa].T @ G[model.parents[fa], :3, :3].T @ Gf)
        for j in fingers:                                         # open hand (rest = straight fingers)
            q[j] = q[j] * (1 - 0.8 * e) + np.array([1.0, 0, 0, 0]) * (0.8 * e)
            q[j] /= np.linalg.norm(q[j])
        rot[f] = q
        hips[f] = h
    c = Clip("Wave", rot, hips, fps=fps, loop=False)
    c.mask = mask
    return c


class Animator:
    """Layered animation:
         locomotion   idle / walk / run / sprint blended by smoothed speed (phase synced)
         overlay      an upper-body loop (e.g. holding a pistol) over the locomotion
         loop action  a full-body loop that can be faded in / out (talking)
         action       one-shots (attack, shoot, hit, death ...), full or upper body"""

    def __init__(self, model, clips, seed=0):
        self.m = model
        self.clips = {k: (v if isinstance(v, list) else [v]) for k, v in clips.items()}
        self.nb = len(model.bone_names)
        self.t = 0.0
        self.phase = (seed % 97) / 97.0
        self.speed = 0.0
        self.speed_target = 0.0
        self.action = None
        self.loop = None                  # [slot, weight, target]
        self.overlay = None               # [slot, weight, target]
        self.dead = False
        self.events = []
        import random as _r
        self._rng = _r.Random(seed)
        self.idle_t = self._rng.random() * 10.0
        up = np.zeros(self.nb)
        names = [canon(n) for n in model.bone_names]
        root_upper = None
        for i, n in enumerate(names):
            if n == "spine2":
                root_upper = i
        for i, n in enumerate(names):
            if n == "spine":
                up[i] = 0.2
            elif n == "spine1":
                up[i] = 0.55
        if root_upper is not None:
            todo = [root_upper]
            while todo:
                j = todo.pop()
                up[j] = 1.0
                todo += [k for k in range(self.nb) if model.parents[k] == j]
        self.mask_upper = up
        self.mask_full = np.ones(self.nb)

    # -- control ------------------------------------------------------------
    def has(self, slot):
        return bool(self.clips.get(slot))

    def clip(self, slot):
        c = self.clips.get(slot)
        return c[0] if c else None

    def set_speed(self, v, snap=False):
        self.speed_target = max(0.0, float(v))
        if snap:
            self.speed = self.speed_target

    def set_overlay(self, slot):
        if slot and self.has(slot):
            if self.overlay is None or self.overlay[0] != slot:
                self.overlay = [slot, 0.0 if self.overlay is None else self.overlay[1], 1.0]
            else:
                self.overlay[2] = 1.0
        elif self.overlay is not None:
            self.overlay[2] = 0.0

    def set_loop(self, slot):
        if slot and self.has(slot):
            if self.loop is None or self.loop[0] != slot:
                self.loop = [slot, 0.0, 1.0, 0.0]
            else:
                self.loop[2] = 1.0
        elif self.loop is not None:
            self.loop[2] = 0.0

    def play(self, slot, fade=0.15, hold=False, on_done=None, rate=1.0, upper=None):
        cl = self.clips.get(slot)
        if not cl:
            return False
        c = self._rng.choice(cl)
        if upper is None:
            upper = slot in UPPER_SLOTS
        self.action = {"clip": c, "slot": slot, "t": 0.0, "w": 0.0 if self.action is None else self.action["w"],
                       "fade": max(1e-3, fade), "hold": hold, "done": on_done, "rate": rate, "fired": set(),
                       "mask": getattr(c, "mask", None) if getattr(c, "mask", None) is not None
                       else (self.mask_upper if upper else self.mask_full)}
        if slot == "death":
            self.dead = True
        return True

    def stop_action(self):
        self.action = None
        self.loop = None
        self.dead = False

    @property
    def busy(self):
        return self.action is not None and not self.action["hold"]

    # -- evaluation ------------------------------------------------------
    def _loco(self, dt):
        idle, walk, run, sprint = (self.clip(s) for s in ("idle", "walk", "run", "sprint"))
        v = self.speed
        ws = walk.speed if walk and walk.speed > 0.2 else 1.3
        rs = run.speed if run and run.speed > ws + 0.2 else max(ws * 2.4, 3.0)
        ss = sprint.speed if sprint and sprint.speed > rs + 0.2 else rs * 1.6
        if v < 0.04 or walk is None:
            w = {"idle": 1.0}
        elif v < ws:
            k = min(1.0, v / (0.6 * ws))
            w = {"idle": 1.0 - k, "walk": k}
        elif v < rs or run is None:
            k = 0.0 if run is None else (v - ws) / (rs - ws)
            w = {"walk": 1.0 - k, "run": k}
        elif v < ss and sprint is not None:
            k = (v - rs) / (ss - rs)
            w = {"run": 1.0 - k, "sprint": k}
        else:
            w = {"sprint": 1.0} if sprint is not None else {"run": 1.0}
        moving = [n for n in ("walk", "run", "sprint") if n in w and self.clip(n) is not None]
        if moving:
            cyc = sum(w[n] * self.clip(n).duration for n in moving) / sum(w[n] for n in moving)
            nat = sum(w[n] * {"walk": ws, "run": rs, "sprint": ss}[n] for n in moving) / sum(w[n] for n in moving)
            rate = min(1.6, max(0.6, v / max(nat, 1e-3))) if v > 0.04 else 1.0
            self.phase = (self.phase + dt * rate / max(cyc, 1e-3)) % 1.0
        self.idle_t += dt
        poses = []
        for n, k in w.items():
            c = self.clip(n)
            if c is None or k <= 1e-4:
                continue
            if n == "idle":
                tt = self.idle_t
            else:
                tt = ((self.phase + getattr(c, "phase0", 0.0)) % 1.0) * c.duration
            q, h = c.sample(tt)
            poses.append((k, q, h))
        if not poses:
            return np.tile([1.0, 0, 0, 0], (self.nb, 1)), np.zeros(3)
        return _blend(poses)

    def update(self, dt):
        self.t += dt
        self.events = []
        a = min(1.0, dt * 6.0)
        self.speed += (self.speed_target - self.speed) * a
        q, h = self._loco(dt)
        if self.loop is not None:
            lp = self.loop
            lp[1] += (lp[2] - lp[1]) * min(1.0, dt * 5.0)
            lp[3] += dt
            c = self.clip(lp[0])
            if c is not None and lp[1] > 1e-3:
                lq, lh = c.sample(lp[3])
                q, h = _mix(q, h, lq, lh, lp[1])
            if lp[2] == 0.0 and lp[1] < 0.01:
                self.loop = None
        if self.overlay is not None:
            ov = self.overlay
            ov[1] += (ov[2] - ov[1]) * min(1.0, dt * 6.0)
            c = self.clip(ov[0])
            if c is not None and ov[1] > 1e-3:
                oq, _oh = c.sample(self.t)
                q, h = _mix(q, h, oq, h, self.mask_upper * ov[1])
            if ov[2] == 0.0 and ov[1] < 0.01:
                self.overlay = None
        act = self.action
        if act is not None:
            c = act["clip"]
            act["t"] += dt * act["rate"]
            dur = c.duration
            u = act["t"] / dur
            for ev, at in c.events.items():
                if u >= at and ev not in act["fired"]:
                    act["fired"].add(ev)
                    self.events.append(ev)
            if u >= 1.0:
                if act["hold"]:
                    aq, ah = c.sample(dur)
                    return _mix(q, h, aq, ah, act["mask"])
                act["w"] = 1.0 - min(1.0, (act["t"] - dur) / act["fade"])
                if act["w"] <= 0.0:
                    cb = act["done"]
                    self.action = None
                    if cb:
                        cb()
                    return q, h
            else:
                act["w"] = min(1.0, act["w"] + dt / act["fade"])
            aq, ah = c.sample(min(act["t"], dur))
            q, h = _mix(q, h, aq, ah, act["mask"] * act["w"])
        return q, h


def _mix(qa, ha, qb, hb, w):
    """Per-bone blend from pose a to pose b; w scalar or (B,)."""
    w = np.broadcast_to(np.asarray(w, np.float64), (len(qa),))
    s = np.sign(np.sum(qa * qb, axis=1))
    s[s == 0] = 1.0
    q = qnorm(qa * (1.0 - w)[:, None] + qb * (s * w)[:, None])
    wh = float(w[0])
    return q, np.asarray(ha) * (1.0 - wh) + np.asarray(hb) * wh


def _blend(poses):
    tot = sum(p[0] for p in poses)
    if tot <= 1e-6:
        return poses[0][1], poses[0][2]
    ref = poses[0][1]
    q = np.zeros_like(ref)
    h = np.zeros(3)
    for w, pq, ph in poses:
        s = np.sign(np.sum(pq * ref, axis=1, keepdims=True))
        s[s == 0] = 1.0
        q += pq * s * (w / tot)
        h += np.asarray(ph) * (w / tot)
    return qnorm(q), h


class Pose:
    """Forward kinematics + skin matrices for one character."""

    def __init__(self, model):
        self.m = model
        self.nb = len(model.bone_names)
        self.rest_local = model.rest_local
        self.rest_world = model.rest_world
        self.inv_bind = model.inv_bind
        self.parents = model.parents
        self.G = model.rest_world.copy()
        self.hips = model.bone("mixamorig:Hips")
        ids = {canon(n): i for i, n in enumerate(model.bone_names)}
        self.legs = []
        for side in ("left", "right"):
            chain = [ids.get(side + n, -1) for n in ("upleg", "leg", "foot", "toebase")]
            if min(chain[:3]) >= 0:
                self.legs.append(chain)
        self.ik_offsets = [0.0, 0.0]
        self.ik_pelvis = 0.0
        self.ik_normals = [np.array([0.0, 1.0, 0.0]), np.array([0.0, 1.0, 0.0])]

    def set_arm_clearance(self, angles):
        """angles: {'left': rad, 'right': rad} -> outward rotation of the upper arms."""
        self.corr = {}
        for side, ang in (angles or {}).items():
            i = self.m.bone("mixamorig:LeftArm" if side == "left" else "mixamorig:RightArm")
            p = self.parents[i] if i >= 0 else -1
            if i < 0 or p < 0 or ang <= 0:
                continue
            # rotation about the character's front-back axis that swings a hanging arm outward
            # (+Z turns a downward arm towards +X, so the left arm (at -X) uses -Z)
            fwd = self.rest_world[p, :3, :3].T @ np.array([0.0, 0.0, -1.0 if side == "left" else 1.0])
            self.corr[i] = _rot3(fwd / np.linalg.norm(fwd), ang)

    def fk(self, q, hips_off):
        R = q_to_m(q)
        L = self.rest_local.copy()
        L[:, :3, :3] = self.rest_local[:, :3, :3] @ R
        for i, C in getattr(self, "corr", {}).items():
            L[i, :3, :3] = C @ L[i, :3, :3]
        G = self.G
        for i, p in enumerate(self.parents):
            if p < 0:
                G[i] = L[i]
                G[i, :3, 3] = L[i, :3, 3] + hips_off
            else:
                G[i] = G[p] @ L[i]
        return G

    def skin(self):
        return self.G @ self.inv_bind

    # ---------------------------------------------------------------- foot IK
    def foot_ik(self, ground_fn, dt, strength=1.0, max_drop=0.45):
        """ground_fn(leg_index, x, z, y_start) -> (ground_y, normal) in MODEL space or None.

        Plants the feet on uneven ground: the pelvis drops for the lower foot,
        each leg is solved as a two-bone chain (knee keeps its bend plane), and
        planted feet tilt to the slope."""
        G = self.G
        if not self.legs:
            return
        if not hasattr(self, "_desc"):
            self._desc = {}
            for chain in self.legs:
                for i in chain[:3]:
                    if i >= 0:
                        self._desc[i] = np.array(self._descendants(i), np.int64)
        ank_h = self.m.metrics.get("ankle_height", 0.08)
        want = []
        for k, (up, lo, ft, toe) in enumerate(self.legs):
            a = G[ft, :3, 3]
            res = ground_fn(k, float(a[0]), float(a[2]), float(G[self.hips, 1, 3]))
            if res is None:
                want.append(None)
                continue
            gy, n = res
            lift = max(0.0, float(a[1]) - ank_h)            # animated foot lift above y=0
            want.append((gy + ank_h + lift - float(a[1]), n, lift))
        offs = [w[0] if w is not None else 0.0 for w in want]
        rate = min(1.0, dt * 14.0) if dt > 0 else 1.0
        pelvis = max(-max_drop, min(0.0, min(offs)))
        self.ik_pelvis += (pelvis - self.ik_pelvis) * rate
        flat = True
        for k in range(len(self.legs)):
            self.ik_offsets[k] += (offs[k] - self.ik_offsets[k]) * rate
            if want[k] is not None:
                nn = self.ik_normals[k]
                tn = want[k][1]
                self.ik_normals[k] = np.array([nn[0] + (tn[0] - nn[0]) * rate, nn[1] + (tn[1] - nn[1]) * rate,
                                               nn[2] + (tn[2] - nn[2]) * rate])
            n = self.ik_normals[k]
            if abs(self.ik_offsets[k] - self.ik_pelvis) > 0.004 or n[1] < 0.995 * math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2):
                flat = False
        if abs(self.ik_pelvis) > 1e-5:
            G[:, 1, 3] += self.ik_pelvis * strength          # whole skeleton shifts; legs re-solved below
        if flat:
            return                                        # level ground: nothing else to solve
        for k, (up, lo, ft, toe) in enumerate(self.legs):
            if want[k] is None:
                continue
            foot_rot = G[ft, :3, :3].copy()
            a, b, c = G[up, :3, 3].copy(), G[lo, :3, 3].copy(), G[ft, :3, 3].copy()
            t = c.copy()
            t[1] += (self.ik_offsets[k] - self.ik_pelvis) * strength
            if abs(t[1] - c[1]) > 0.002:
                self._two_bone(up, lo, ft, a, b, c, t)
            # tilt the planted foot to the ground
            plant = max(0.0, 1.0 - want[k][2] / 0.12) * strength
            n = self.ik_normals[k]
            ln = math.sqrt(n[0] ** 2 + n[1] ** 2 + n[2] ** 2) or 1.0
            ny = n[1] / ln
            if plant <= 0.0 or ny > 0.9995:
                self._set_rot(ft, foot_rot)
                continue
            ang = min(math.acos(max(-1.0, min(1.0, ny))), math.radians(35)) * plant
            ax = (n[2] / ln, 0.0, -n[0] / ln)                 # up x n
            al = math.sqrt(ax[0] ** 2 + ax[2] ** 2)
            if al < 1e-9:
                self._set_rot(ft, foot_rot)
                continue
            Rt = _rot3((ax[0] / al, 0.0, ax[2] / al), ang)
            self._set_rot(ft, Rt @ foot_rot)

    def _descendants(self, i):
        out = [i]
        for j in range(i + 1, self.nb):
            if self.parents[j] in out:
                out.append(j)
        return out

    def _rotate_sub(self, i, R3, pivot):
        G = self.G
        d = self._desc.get(i)
        if d is None:
            d = self._desc[i] = np.array(self._descendants(i), np.int64)
        G[d, :3, :3] = np.einsum("ij,njk->nik", R3, G[d, :3, :3])
        G[d, :3, 3] = (G[d, :3, 3] - pivot) @ R3.T + pivot

    def _set_rot(self, i, R_new):
        delta = R_new @ self.G[i, :3, :3].T
        self._rotate_sub(i, delta, self.G[i, :3, 3].copy())

    def _two_bone(self, up, lo, ft, a, b, c, t):
        lab = _len3(b - a)
        lcb = _len3(c - b)
        lat = min(max(_len3(t - a), 1e-4), (lab + lcb) * 0.9995)
        ac_ab_0 = _ang3(c - a, b - a)
        ba_bc_0 = _ang3(a - b, c - b)
        ac_ab_1 = math.acos(max(-1.0, min(1.0, (lcb * lcb - lab * lab - lat * lat) / (-2 * lab * lat))))
        ba_bc_1 = math.acos(max(-1.0, min(1.0, (lat * lat - lab * lab - lcb * lcb) / (-2 * lab * lcb))))
        axis0 = _cross3(c - a, b - a)
        if _len3(axis0) < 1e-6:
            axis0 = self.G[up, :3, 0].copy()               # straight leg: bend about the thigh's X
        axis0 = axis0 / _len3(axis0)
        self._rotate_sub(up, _rot3(axis0, ac_ab_1 - ac_ab_0), a)
        self._rotate_sub(lo, _rot3(axis0, ba_bc_1 - ba_bc_0), self.G[lo, :3, 3].copy())
        c2 = self.G[ft, :3, 3].copy()
        axis1 = _cross3(c2 - a, t - a)
        l1 = _len3(axis1)
        if l1 > 1e-9:
            self._rotate_sub(up, _rot3(axis1 / l1, _ang3(c2 - a, t - a)), a)


def _len3(v):
    return math.sqrt(float(v[0]) ** 2 + float(v[1]) ** 2 + float(v[2]) ** 2)


def _cross3(u, v):
    return np.array([u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0]])


def _ang3(u, v):
    nu, nv = _len3(u), _len3(v)
    if nu < 1e-9 or nv < 1e-9:
        return 0.0
    return math.acos(max(-1.0, min(1.0, float(u[0] * v[0] + u[1] * v[1] + u[2] * v[2]) / (nu * nv))))


def _rot3(axis, ang):
    x, y, z = float(axis[0]), float(axis[1]), float(axis[2])
    c, s = math.cos(ang), math.sin(ang)
    t = 1.0 - c
    return np.array([[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
                     [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
                     [t * x * z - s * y, t * y * z + s * x, t * z * z + c]])


# =========================================================================== caches
_SRC_CACHE = {}


def split_ref(ref):
    """'file.glb#Run' -> ('file.glb', 'Run'); 'file.fbx' -> ('file.fbx', None)."""
    if "#" in ref:
        a, b = ref.rsplit("#", 1)
        return a, b
    return ref, None


def pick(sources, which):
    """Choose one animation of a file by name (case-insensitive) or index."""
    if which is None or which == "":
        return sources[0]
    for s in sources:
        if s.name.lower() == str(which).lower():
            return s
    try:
        return sources[int(which)]
    except (ValueError, IndexError):
        raise ValueError(f"no animation '{which}' in file (has: {', '.join(s.name for s in sources)})")


def load_source_cached(path):
    try:
        st = os.stat(path)
        key = (os.path.abspath(path), st.st_mtime, st.st_size)
    except OSError:
        key = (os.path.abspath(path), 0, 0)
    s = _SRC_CACHE.get(key)
    if s is None:
        s = load_any(path)
        _SRC_CACHE[key] = s
    return s

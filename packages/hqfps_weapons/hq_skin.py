"""glTF skinning runtime for HQ FPS weapon viewmodels.

Self-contained: parses a .glb (skin + node hierarchy + animation samplers)
and evaluates a bone matrix palette for a given clip and time.  Nothing in
here touches the engine, so it can be unit-tested headlessly.

The palette this produces is what gets uploaded to the `u_mh_bones` texture
that the GPU skinning shader hook reads -- see engine.py.  Layout matches
mh_characters: one row of (bone_count * 4) RGBA32F texels, each bone a
column-major mat4.
"""
import json
import struct

import numpy as np

# glTF component types -> numpy dtypes
_CTYPE = {
    5120: np.int8, 5121: np.uint8, 5122: np.int16,
    5123: np.uint16, 5125: np.uint32, 5126: np.float32,
}
_NCOMP = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


class GLBError(Exception):
    pass


# --------------------------------------------------------------------- quats
def quat_slerp(a, b, t):
    """Shortest-arc slerp between quaternions (x, y, z, w), arrays allowed."""
    a = np.asarray(a, dtype=np.float64)
    b = np.asarray(b, dtype=np.float64)
    dot = float(np.dot(a, b))
    if dot < 0.0:               # take the short way round
        b = -b
        dot = -dot
    if dot > 0.9995:            # nearly parallel -> lerp and renormalise
        out = a + t * (b - a)
        n = np.linalg.norm(out)
        return out / n if n > 0.0 else np.array([0.0, 0.0, 0.0, 1.0])
    theta0 = np.arccos(np.clip(dot, -1.0, 1.0))
    theta = theta0 * t
    s0 = np.sin(theta0 - theta) / np.sin(theta0)
    s1 = np.sin(theta) / np.sin(theta0)
    return s0 * a + s1 * b


def quat_to_mat4(q):
    """(x, y, z, w) -> 4x4 row-major rotation matrix."""
    x, y, z, w = (float(v) for v in q)
    n = x * x + y * y + z * z + w * w
    if n < 1e-12:
        return np.eye(4)
    s = 2.0 / n
    xx, yy, zz = x * x * s, y * y * s, z * z * s
    xy, xz, yz = x * y * s, x * z * s, y * z * s
    wx, wy, wz = w * x * s, w * y * s, w * z * s
    m = np.eye(4)
    m[0, 0] = 1.0 - (yy + zz); m[0, 1] = xy - wz;          m[0, 2] = xz + wy
    m[1, 0] = xy + wz;         m[1, 1] = 1.0 - (xx + zz);  m[1, 2] = yz - wx
    m[2, 0] = xz - wy;         m[2, 1] = yz + wx;          m[2, 2] = 1.0 - (xx + yy)
    return m


def trs_to_mat4(t, r, s):
    m = quat_to_mat4(r)
    m[:3, 0] *= s[0]
    m[:3, 1] *= s[1]
    m[:3, 2] *= s[2]
    m[0, 3], m[1, 3], m[2, 3] = t[0], t[1], t[2]
    return m


# ----------------------------------------------------------------- glb parse
class GLB:
    """Minimal .glb reader -- JSON chunk plus the single BIN chunk."""

    def __init__(self, path):
        self.path = path
        with open(path, "rb") as f:
            data = f.read()
        if len(data) < 12:
            raise GLBError("%s: too short" % path)
        magic, _ver, total = struct.unpack_from("<III", data, 0)
        if magic != 0x46546C67:
            raise GLBError("%s: not a GLB" % path)
        self.json = None
        self.bin = b""
        off = 12
        while off + 8 <= min(total, len(data)):
            clen, ctype = struct.unpack_from("<II", data, off)
            body = data[off + 8: off + 8 + clen]
            if ctype == 0x4E4F534A:
                self.json = json.loads(body.decode("utf-8"))
            elif ctype == 0x004E4942:
                self.bin = body
            off += 8 + clen
        if self.json is None:
            raise GLBError("%s: no JSON chunk" % path)

    # -- accessors ----------------------------------------------------------
    def accessor(self, idx):
        """Read accessor `idx` as an (n, ncomp) float/int array."""
        g = self.json
        acc = g["accessors"][idx]
        n = acc["count"]
        ncomp = _NCOMP[acc["type"]]
        dt = _CTYPE[acc["componentType"]]
        if "bufferView" not in acc:
            return np.zeros((n, ncomp), dtype=dt)
        bv = g["bufferViews"][acc["bufferView"]]
        start = bv.get("byteOffset", 0) + acc.get("byteOffset", 0)
        stride = bv.get("byteStride") or 0
        esize = np.dtype(dt).itemsize * ncomp
        if stride and stride != esize:                 # interleaved
            out = np.empty((n, ncomp), dtype=dt)
            for i in range(n):
                o = start + i * stride
                out[i] = np.frombuffer(self.bin, dtype=dt, count=ncomp,
                                       offset=o)
        else:
            out = np.frombuffer(self.bin, dtype=dt, count=n * ncomp,
                                offset=start).reshape(n, ncomp)
        return np.array(out)

    # -- convenience --------------------------------------------------------
    @property
    def nodes(self):
        return self.json.get("nodes", [])

    @property
    def skins(self):
        return self.json.get("skins", [])

    @property
    def animations(self):
        return self.json.get("animations", [])


# ------------------------------------------------------------------ sampler
class _Channel:
    """One animated property (translation / rotation / scale) of one node."""
    __slots__ = ("node", "path", "times", "values", "interp")

    def __init__(self, node, path, times, values, interp):
        self.node = node
        self.path = path
        self.times = times
        self.values = values
        self.interp = interp

    def sample(self, t):
        ts = self.times
        n = len(ts)
        if n == 0:
            return None
        if n == 1 or t <= ts[0]:
            return self.values[0]
        if t >= ts[-1]:
            return self.values[-1]
        i = int(np.searchsorted(ts, t, side="right")) - 1
        i = max(0, min(i, n - 2))
        t0, t1 = ts[i], ts[i + 1]
        span = t1 - t0
        u = 0.0 if span <= 0.0 else float((t - t0) / span)
        if self.interp == "STEP":
            return self.values[i]
        a, b = self.values[i], self.values[i + 1]
        if self.path == "rotation":
            return quat_slerp(a, b, u)
        return a + (b - a) * u


class Clip:
    """One named animation: a set of channels plus its duration.

    The source FBX takes were baked out of Blender one NLA strip at a time,
    and that bake writes the armature's REST pose into the first frame of
    every strip before the action is evaluated.  Left in, each clip snaps to
    a half-A-pose for one frame whenever it starts or is sampled at t=0.

    The artifact is confined to that one key: on channels that carry real
    motion (>2 keys) the first key is an outlier, while constant channels
    (exactly 2 keys, start and end) hold the same value at both ends and are
    already correct.  So drop the leading key only where there is motion to
    drop it from, then rebase every channel so the clip starts at t=0.
    """

    def __init__(self, name, channels):
        self.name = name
        kept = []
        for ch in channels:
            if len(ch.times) > 2:
                ch.times = ch.times[1:]
                ch.values = ch.values[1:]
            if len(ch.times):
                kept.append(ch)
        t0 = min((float(ch.times[0]) for ch in kept), default=0.0)
        for ch in kept:
            ch.times = ch.times - t0
        self.channels = kept
        self.duration = 0.0
        for ch in kept:
            self.duration = max(self.duration, float(ch.times[-1]))


# -------------------------------------------------------------------- rig
class SkinnedModel:
    """A GLB's skin + node hierarchy + clips, able to produce a bone palette."""

    def __init__(self, path):
        self.glb = GLB(path)
        g = self.glb.json
        if not g.get("skins"):
            raise GLBError("%s: no skin" % path)
        skin = g["skins"][0]
        self.joints = list(skin["joints"])
        self.joint_set = {n: i for i, n in enumerate(self.joints)}
        ibm = self.glb.accessor(skin["inverseBindMatrices"])
        # glTF stores mat4 column-major; transpose each into row-major
        self.inverse_bind = ibm.reshape(-1, 4, 4).transpose(0, 2, 1).astype(np.float64)

        nodes = self.glb.nodes
        self.node_count = len(nodes)
        self.parent = [-1] * self.node_count
        for i, nd in enumerate(nodes):
            for c in nd.get("children", []):
                self.parent[c] = i
        # rest TRS per node
        self.rest_t = np.zeros((self.node_count, 3))
        self.rest_r = np.zeros((self.node_count, 4))
        self.rest_r[:, 3] = 1.0
        self.rest_s = np.ones((self.node_count, 3))
        for i, nd in enumerate(nodes):
            if "matrix" in nd:
                m = np.array(nd["matrix"], dtype=np.float64).reshape(4, 4).T
                self.rest_t[i] = m[:3, 3]
                sx = np.linalg.norm(m[:3, 0]); sy = np.linalg.norm(m[:3, 1])
                sz = np.linalg.norm(m[:3, 2])
                self.rest_s[i] = (sx, sy, sz)
                rot = m[:3, :3] / np.array([sx or 1, sy or 1, sz or 1])
                self.rest_r[i] = _mat3_to_quat(rot)
            else:
                self.rest_t[i] = nd.get("translation", [0.0, 0.0, 0.0])
                self.rest_r[i] = nd.get("rotation", [0.0, 0.0, 0.0, 1.0])
                self.rest_s[i] = nd.get("scale", [1.0, 1.0, 1.0])
        # draw order: parents before children
        self.order = self._topo()
        self.clips = {}
        for a in self.glb.animations:
            chans = []
            for c in a.get("channels", []):
                tgt = c.get("target", {})
                nidx = tgt.get("node")
                path = tgt.get("path")
                if nidx is None or path not in ("translation", "rotation", "scale"):
                    continue
                smp = a["samplers"][c["sampler"]]
                times = self.glb.accessor(smp["input"]).reshape(-1).astype(np.float64)
                vals = self.glb.accessor(smp["output"]).astype(np.float64)
                ncomp = 4 if path == "rotation" else 3
                vals = vals.reshape(len(times), -1)[:, :ncomp]
                chans.append(_Channel(nidx, path, times, vals,
                                      smp.get("interpolation", "LINEAR")))
            if chans:
                name = a.get("name") or ("clip%d" % len(self.clips))
                self.clips[name] = Clip(name, chans)

    def _topo(self):
        order, seen = [], set()

        def visit(i):
            if i in seen:
                return
            p = self.parent[i]
            if p >= 0:
                visit(p)
            seen.add(i)
            order.append(i)

        for i in range(self.node_count):
            visit(i)
        return order

    # -- posing -------------------------------------------------------------
    def local_trs(self, clip_name=None, time=0.0):
        """Rest pose overridden by a clip's channels sampled at `time`."""
        t = self.rest_t.copy()
        r = self.rest_r.copy()
        s = self.rest_s.copy()
        clip = self.clips.get(clip_name) if clip_name else None
        if clip is not None:
            for ch in clip.channels:
                v = ch.sample(time)
                if v is None:
                    continue
                if ch.path == "translation":
                    t[ch.node] = v
                elif ch.path == "rotation":
                    r[ch.node] = v
                else:
                    s[ch.node] = v
        return t, r, s

    def global_matrices(self, clip_name=None, time=0.0):
        t, r, s = self.local_trs(clip_name, time)
        out = np.zeros((self.node_count, 4, 4))
        for i in self.order:
            local = trs_to_mat4(t[i], r[i], s[i])
            p = self.parent[i]
            out[i] = local if p < 0 else out[p] @ local
        return out

    def palette(self, clip_name=None, time=0.0):
        """(bone_count, 4, 4) skinning matrices, ready for the bone texture."""
        g = self.global_matrices(clip_name, time)
        out = np.empty((len(self.joints), 4, 4), dtype=np.float32)
        for bi, nidx in enumerate(self.joints):
            out[bi] = (g[nidx] @ self.inverse_bind[bi]).astype(np.float32)
        return out


def _mat3_to_quat(m):
    tr = m[0, 0] + m[1, 1] + m[2, 2]
    if tr > 0.0:
        s = np.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = np.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = np.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = np.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    return np.array([x, y, z, w])

"""Minimal glTF 2.0 reader (numpy) for the offline asset builder."""
import json
import os

import numpy as np

_CT = {5120: np.int8, 5121: np.uint8, 5122: np.int16, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
_NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4, "MAT4": 16}


def _quat_m(q):
    x, y, z, w = q
    return np.array([[1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
                     [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
                     [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)]])


class GLTF:
    def __init__(self, path):
        self.path = path
        self.dir = os.path.dirname(path)
        self.j = json.load(open(path))
        self.bufs = [np.memmap(os.path.join(self.dir, b["uri"]), dtype=np.uint8, mode="r") for b in self.j["buffers"]]

    def acc(self, i):
        a = self.j["accessors"][i]
        bv = self.j["bufferViews"][a["bufferView"]]
        dt = np.dtype(_CT[a["componentType"]])
        nc = _NC[a["type"]]
        off = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
        stride = bv.get("byteStride", 0) or dt.itemsize * nc
        raw = self.bufs[bv["buffer"]]
        n = a["count"]
        if stride == dt.itemsize * nc:
            out = np.frombuffer(raw[off:off + n * stride].tobytes(), dt).reshape(n, nc)
        else:
            out = np.lib.stride_tricks.as_strided(np.frombuffer(raw[off:].tobytes(), np.uint8),
                                                  (n, nc * dt.itemsize), (stride, 1))
            out = np.frombuffer(out.copy().tobytes(), dt).reshape(n, nc)
        if a.get("normalized"):
            out = out.astype(np.float32) / np.iinfo(dt).max
        return out

    def node_matrix(self, n):
        if "matrix" in n:
            return np.array(n["matrix"], np.float64).reshape(4, 4).T
        M = np.eye(4)
        S = np.diag(n.get("scale", [1, 1, 1]))
        R = _quat_m(n.get("rotation", [0, 0, 0, 1]))
        M[:3, :3] = R @ S
        M[:3, 3] = n.get("translation", [0, 0, 0])
        return M

    def primitives(self):
        """Yields (node_name, material_index, P, N, UV, I) in world space for every mesh primitive."""
        j = self.j
        roots = j["scenes"][j.get("scene", 0)]["nodes"]
        stack = [(r, np.eye(4)) for r in roots]
        while stack:
            ni, parent = stack.pop()
            n = j["nodes"][ni]
            M = parent @ self.node_matrix(n)
            if "mesh" in n:
                for p in j["meshes"][n["mesh"]]["primitives"]:
                    at = p["attributes"]
                    P = self.acc(at["POSITION"]).astype(np.float64)
                    N = self.acc(at["NORMAL"]).astype(np.float64) if "NORMAL" in at else np.zeros_like(P)
                    UV = self.acc(at["TEXCOORD_0"]).astype(np.float64) if "TEXCOORD_0" in at else np.zeros((len(P), 2))
                    I = self.acc(p["indices"]).reshape(-1).astype(np.int64) if "indices" in p else np.arange(len(P))
                    P = P @ M[:3, :3].T + M[:3, 3]
                    Nm = np.linalg.inv(M[:3, :3]).T
                    N = N @ Nm.T
                    N /= np.maximum(np.linalg.norm(N, axis=1, keepdims=True), 1e-12)
                    yield n.get("name", ""), p.get("material", 0), P, N, UV, I.reshape(-1, 3)
            for c in n.get("children", []):
                stack.append((c, M))

    def material(self, i):
        m = self.j["materials"][i]
        pbr = m.get("pbrMetallicRoughness", {})

        def tex(k, src=pbr):
            t = src.get(k)
            if not t:
                return None
            img = self.j["images"][self.j["textures"][t["index"]]["source"]]
            return os.path.join(self.dir, img["uri"])
        return {"name": m.get("name", ""), "alpha": m.get("alphaMode", "OPAQUE"),
                "base": tex("baseColorTexture"), "mr": tex("metallicRoughnessTexture"),
                "normal": tex("normalTexture", m), "color": pbr.get("baseColorFactor", [1, 1, 1, 1])}

"""FarScream - an open-island shooter map generator for Heaven3D.

One FarScreamMap object (its seed and a few settings) is all a project
saves.  From the seed it makes a Tropical Island and furnishes it: three
military camps, a radio outpost on the highest flat ground, islander
villages in the jungle, a plane crash, a shipwreck and wrecked trucks along
the roads.  Everything is rebuilt the same way whenever the project opens.

In Play you land on a beach with a pistol.  Soldiers carry pistols and MP5s
and drop them (and their .45 ammo) when they die; islanders run at you and
punch when they see you; red fuel drums explode; supply crates hold ammo and
medicine; both trucks can be driven in third person (F to get in and out).
Clear the camps and blow up the radio station to liberate the island.

The props share their meshes and materials (so the renderer draws all the
barrels of the island in a few instanced calls), switch to lighter versions
of themselves from the distance where the difference is under a pixel, and
collide with simple boxes or a low-poly copy of themselves.
"""
import json
import math
import os
import random
import sys
import time

import numpy as np

H3D = None
L = None                                   # fs_layout.py
CLS = {}
STATE = {"lib": None, "maps": {}, "lod_t": 0.0, "engine": None, "tick_t": 0.0}
HERE = os.path.dirname(os.path.abspath(__file__))
MODELS = os.path.join(HERE, "assets", "models")
ISL, WEP, CHR = "h3dpkg_tropical_islands", "h3dpkg_hqfps_weapons", "h3dpkg_mh_characters"
TOPOLOGIES = ("classic", "hills", "mountains", "twin_peaks", "plateau", "volcano")
INTERACT_KEY = "f"
DECAL_CAP = 40                             # bullet holes on the map at once (the oldest goes first)


def _m():
    return H3D.module


def _glm():
    return H3D.module.glm


def _log(msg):
    try:
        H3D.log(msg)
    except Exception:
        print("[FarScream] " + msg)


def _warn(msg):
    try:
        H3D.warn(msg)
    except Exception:
        print("[FarScream][WARN] " + msg)


def _mod(name):
    return sys.modules.get(name)


# =========================================================================== model library
class _Proxy:
    """What the physics needs from a mesh to collide with it: CPU triangles."""

    def __init__(self, pos, idx):
        glm = _glm()
        self.cpu_positions = np.ascontiguousarray(pos, np.float32)
        self.cpu_indices = np.ascontiguousarray(idx, np.uint32)
        self.vertex_count = len(pos)
        self.index_count = len(idx)
        self.indexed = True
        self._vertex_data = None
        lo, hi = self.cpu_positions.min(0), self.cpu_positions.max(0)
        c = (lo + hi) * 0.5
        self.bounding_box = (glm.vec3(*map(float, lo)), glm.vec3(*map(float, hi)))
        self.bounding_sphere = (glm.vec3(*map(float, c)), float(np.linalg.norm(hi - lo) * 0.5))


def _cluster(P, I, cells=32, cell_min=0.1, cell_max=0.25):
    """A low-poly copy of a mesh for collisions: vertices that fall in the
    same small cube (10-25 cm) become one, flattened triangles go.  The shape
    stays within a cell of the real surface; the physics gets 10-50x fewer
    triangles to test."""
    lo, hi = P.min(0), P.max(0)
    cell = float(np.clip((hi - lo).max() / cells, cell_min, cell_max))
    q = np.floor((P - lo) / cell).astype(np.int64)
    key, inv = np.unique(q, axis=0, return_inverse=True)
    inv = inv.ravel()
    sums = np.zeros((len(key), 3))
    np.add.at(sums, inv, P)
    cnt = np.bincount(inv, minlength=len(key)).astype(np.float64)
    V = (sums / cnt[:, None]).astype(np.float32)
    T = inv[I.reshape(-1, 3)]
    T = T[(T[:, 0] != T[:, 1]) & (T[:, 1] != T[:, 2]) & (T[:, 0] != T[:, 2])]
    T = np.unique(np.sort(T, axis=1), axis=0) if len(T) else T     # (winding does not matter for collisions)
    return V, T.reshape(-1).astype(np.uint32)


class Model:
    """One model, loaded once: its parts (mesh per level of detail +
    material), its box, its collision proxy and, for vehicles, its wheels."""

    def __init__(self, name):
        self.name = name
        self.parts = []            # [[mesh_lod0, mesh_lod1, ...], material]
        self.lods = [0.0]          # distance from which each level is used
        self.lo = self.hi = None
        self.proxy = None
        self.wheels = []           # vehicles: [(mesh, material, pivot vec3, radius)]
        self.radius = 0.0


def _vertex_arrays(mesh):
    """(positions, normals, tangents, uvs, indices) of an engine Mesh."""
    vd = getattr(mesh, "_vertex_data", None)
    if vd is None:
        return None
    a = np.frombuffer(vd, np.float32).reshape(-1, 12)
    idx = mesh.cpu_indices if getattr(mesh, "cpu_indices", None) is not None else np.arange(len(a), dtype=np.uint32)
    return a[:, 0:3], a[:, 3:6], a[:, 6:10], a[:, 10:12], np.asarray(idx, np.uint32).reshape(-1)


def _import_parts(engine, path):
    """[(mesh, material)] of a model file, without leaving it in the scene."""
    node = engine.load_model(path)
    if node is None:
        return []
    out = [(n.mesh, n.material) for n in node.traverse() if n.mesh is not None]
    node.remove()
    try:
        if engine.selected is node:
            engine.deselect()
    except Exception:
        pass
    return out


class Library:
    def __init__(self, engine):
        self.engine = engine
        self.models = {}
        try:
            with open(os.path.join(MODELS, "manifest.json")) as fh:
                self.manifest = json.load(fh)
        except Exception:
            self.manifest = {}

    def get(self, name):
        md = self.models.get(name)
        if md is not None:
            return md
        md = Model(name)
        self.models[name] = md
        path = os.path.join(MODELS, name + ".glb")
        base = _import_parts(self.engine, path) if os.path.isfile(path) else []
        if not base:
            _warn("model '%s' is missing (%s)" % (name, path))
            return md
        md.parts = [[[mesh], mat] for mesh, mat in base]
        info = self.manifest.get(name, {})
        if name not in ("M3S", "pickup_healthy"):          # vehicles keep full detail (you sit next to them)
            for k, lod in enumerate(info.get("lod", []), 1):
                p = os.path.join(MODELS, lod["file"])
                parts = _import_parts(self.engine, p) if os.path.isfile(p) else []
                if len(parts) != len(md.parts):
                    break
                for slot, (mesh, _mat) in zip(md.parts, parts):
                    slot[0].append(mesh)
                md.lods.append(float(lod["from_m"]))
        glm = _glm()
        lo, hi = np.full(3, np.inf), np.full(3, -np.inf)
        for slot, _mat in md.parts:
            bb = slot[0].bounding_box
            lo = np.minimum(lo, [bb[0].x, bb[0].y, bb[0].z])
            hi = np.maximum(hi, [bb[1].x, bb[1].y, bb[1].z])
        md.lo, md.hi = glm.vec3(*map(float, lo)), glm.vec3(*map(float, hi))
        md.radius = float(np.linalg.norm(hi - lo) * 0.5)
        try:
            _stream(self.engine).register(md)
        except Exception as ex:
            _warn("%s: textures not streamed (%s)" % (name, ex))
        if name in ("M3S", "pickup_healthy"):
            try:
                self._split_wheels(md)
            except Exception as ex:
                _warn("%s: wheels not split (%s)" % (name, ex))
        return md

    def proxy(self, md):
        """Collision mesh: the lightest level of detail of every part, merged."""
        if md.proxy is None and md.parts:
            P, I, off = [], [], 0
            for slot, _mat in md.parts:
                mesh = slot[-1]
                pos = getattr(mesh, "cpu_positions", None)
                idx = getattr(mesh, "cpu_indices", None)
                if pos is None:
                    continue
                if idx is None:
                    idx = np.arange(len(pos), dtype=np.uint32)
                P.append(np.asarray(pos, np.float32))
                I.append(np.asarray(idx, np.uint32).reshape(-1) + off)
                off += len(pos)
            if P:
                md.proxy = _Proxy(*_cluster(np.concatenate(P), np.concatenate(I)))
        return md.proxy

    def _split_wheels(self, md):
        """Find the wheels (tyre-sized connected pieces at the bottom) and
        give each its own mesh with its pivot at the hub, so they can turn."""
        m = _m()
        ctx = self.engine.ctx
        glm = _glm()
        ground = md.lo.y
        new_parts = []
        for slot, mat in md.parts:
            mesh = slot[0]
            arr = _vertex_arrays(mesh)
            if arr is None:
                new_parts.append([slot, mat])
                continue
            P, N, T, UV, I = arr
            tris = I.reshape(-1, 3)
            # connected components over welded positions
            key = np.unique(np.round(P, 4), axis=0, return_inverse=True)[1].ravel()
            parent = np.arange(key.max() + 1)

            def find(a):
                while parent[a] != a:
                    parent[a] = parent[parent[a]]
                    a = parent[a]
                return a
            for a, b, c in key[tris]:
                ra, rb, rc = find(a), find(b), find(c)
                parent[rb] = ra
                parent[find(rc)] = ra
            comp = np.array([find(k) for k in key[tris[:, 0]]])
            wheel_tris = np.zeros(len(tris), bool)
            for cid in np.unique(comp):
                sel = comp == cid
                pts = P[np.unique(tris[sel].ravel())]
                lo, hi = pts.min(0), pts.max(0)
                d = hi - lo
                # a wheel: round in Y and along the car (X), thin across (Z), touching the ground
                if (0.55 < d[1] < 1.35 and abs(d[0] - d[1]) < 0.18 * d[1] and d[2] < 0.7 * d[1]
                        and lo[1] - ground < 0.12 and sel.sum() > 40):
                    c = (lo + hi) * 0.5
                    vids = np.unique(tris[sel].ravel())
                    remap = np.full(len(P), -1, np.int64)
                    remap[vids] = np.arange(len(vids))
                    wi = remap[tris[sel]].reshape(-1).astype(np.uint32)
                    wm = m.Mesh.from_arrays(ctx, (P[vids] - c).astype(np.float32), N[vids], UV[vids], wi,
                                            name="%s wheel" % md.name, tangents=T[vids])
                    md.wheels.append((wm, mat, glm.vec3(*map(float, c)), float(d[1]) * 0.5))
                    wheel_tris |= sel
            if wheel_tris.any():
                keep = tris[~wheel_tris]
                vids = np.unique(keep.ravel())
                remap = np.full(len(P), -1, np.int64)
                remap[vids] = np.arange(len(vids))
                bm = m.Mesh.from_arrays(ctx, P[vids], N[vids], UV[vids], remap[keep].reshape(-1).astype(np.uint32),
                                        name="%s body" % md.name, tangents=T[vids])
                new_parts.append([[bm], mat])
            else:
                new_parts.append([slot, mat])
        if md.wheels:
            md.parts = new_parts


# =========================================================================== texture streaming
TEX_SLOTS = ("base_color_tex", "metallic_roughness_tex", "normal_tex", "emissive_tex", "occlusion_tex")
TEX_MIN = 128               # the smallest level kept in memory (pixels)
ANISO = 4.0                 # the renderer's anisotropic filtering


def _uv_density(md):
    """Per material of a model: UV units per metre of surface (99th percentile
    over its triangles, weighted by area)."""
    out = {}
    for slot, mat in md.parts:
        arr = _vertex_arrays(slot[0])
        if arr is None:
            continue
        P, _N, _T, UV, I = arr
        t = I.reshape(-1, 3)
        a, b, c = P[t[:, 0]].astype(np.float64), P[t[:, 1]].astype(np.float64), P[t[:, 2]].astype(np.float64)
        wa = 0.5 * np.linalg.norm(np.cross(b - a, c - a), axis=1)
        ua, ub, uc = UV[t[:, 0]].astype(np.float64), UV[t[:, 1]].astype(np.float64), UV[t[:, 2]].astype(np.float64)
        e1, e2 = ub - ua, uc - ua
        ta = 0.5 * np.abs(e1[:, 0] * e2[:, 1] - e1[:, 1] * e2[:, 0])
        ok = (wa > 1e-10) & (ta > 1e-14)
        if not ok.any():
            continue
        d, w = np.sqrt(ta[ok] / wa[ok]), wa[ok]
        order = np.argsort(d)
        cw = np.cumsum(w[order])
        dens = float(d[order][np.searchsorted(cw, 0.99 * cw[-1])])
        out[id(mat)] = max(out.get(id(mat), 0.0), dens)
    return out


class TexStream:
    """AAA-style texture streaming with no visible change.

    A texel is only ever seen when it covers a screen pixel.  For every
    material we know how many texels per metre its texture gives the
    surface, so from the camera's distance to the nearest copy of the model
    we know the largest version of the texture that could ever show: the
    full image only when you stand right next to it, 1/2, 1/4 ... of it
    further away.  Smaller versions are exactly what the GPU would sample
    there anyway (its own mip levels), so the picture stays the same while
    the island's props need a fraction of the video memory (about 5.7 GB of
    textures fully loaded; a few hundred MB streamed).  Images are decoded on
    a worker thread, nearest first, and uploaded a few milliseconds' worth a
    frame."""

    def __init__(self, engine):
        import threading
        import queue
        self.e = engine
        self.imgs = {}              # payload id -> {"payload", "size", "levels": {L: key}, "users": {L: n}}
        self.mats = {}              # id(material) -> {"mat", "slots": {slot: payload id}, "density", "level", "nodes"}
        self.jobs = queue.PriorityQueue()       # sharpest levels (nearest props) first
        self.seq = 0
        self.done = queue.PriorityQueue()
        self.pending = set()
        self.t = 0.0
        self._thread = threading.Thread(target=self._work, daemon=True)
        self._thread.start()

    # ---- images
    @staticmethod
    def _decode(payload, level, fast=False):
        """The image at 1 / 2^level of its size, box-filtered like the GPU's own
        mip levels.  fast: let the JPEG decoder do most of the scaling (the
        farthest version, made while loading; the exact one replaces it soon
        after, in the background)."""
        import io
        from PIL import Image
        im = Image.open(io.BytesIO(payload))
        w, h = im.size
        if fast and level and getattr(im, "format", "") == "JPEG":
            s = 1 << min(level, 3)
            im.draft("RGB", (max(1, w // s), max(1, h // s)))
        im = im.convert("RGBA")
        tw, th = max(1, w >> level), max(1, h >> level)
        while im.size[0] >= tw * 2 and im.size[1] >= th * 2:
            im = im.reduce(2)                          # box filter, like the GPU's own mip chain
        if im.size != (tw, th):
            im = im.resize((tw, th), Image.BOX)
        return im.transpose(Image.FLIP_TOP_BOTTOM).tobytes("raw", "RGBA"), (tw, th)

    def _work(self):
        while True:
            prio, seq, pid, payload, level = self.jobs.get()
            try:
                data, size = self._decode(payload, level)
                self.done.put((prio, seq, pid, level, data, size))
            except Exception as ex:
                self.done.put((prio, seq, pid, level, None, str(ex)))

    def _upload(self, pid, level, data, size):
        import moderngl
        r = self.e.renderer
        tex = self.e.ctx.texture(size, 4, data)
        tex.build_mipmaps()
        tex.filter = (moderngl.LINEAR_MIPMAP_LINEAR, moderngl.LINEAR)
        try:
            tex.anisotropy = 4.0
        except Exception:
            pass
        img = self.imgs[pid]
        token = b"farscream-texture:%d:%d" % (pid, level)
        key = "bytes:%x" % hash(token)
        old = r._tex_cache.get(key)
        if old is not None and old is not tex:
            try:
                old.release()
            except Exception:
                pass
        r._tex_cache[key] = tex
        img["levels"][level] = ("bytes", token)

    def _release(self, pid, level):
        img = self.imgs[pid]
        desc = img["levels"].pop(level, None)
        if desc is None:
            return
        r = self.e.renderer
        tex = r._tex_cache.pop("bytes:%x" % hash(desc[1]), None)
        if tex is not None and tex is not getattr(r, "_default_tex", None):
            try:
                tex.release()
            except Exception:
                pass

    # ---- materials
    def register(self, md):
        """Take over the textures of a freshly imported model."""
        from PIL import Image
        import io
        dens = _uv_density(md)
        for _slot, mat in md.parts:
            if id(mat) in self.mats:
                continue
            slots = {}
            for name in TEX_SLOTS:
                desc = getattr(mat, name, None)
                if not (isinstance(desc, tuple) and len(desc) == 2 and desc[0] == "bytes"):
                    continue
                payload = desc[1]
                pid = id(payload)
                if pid not in self.imgs:
                    try:
                        size = Image.open(io.BytesIO(payload)).size
                    except Exception:
                        continue
                    self.imgs[pid] = {"payload": payload, "size": size, "levels": {}, "users": {}}
                slots[name] = pid
            if not slots:
                continue
            big = max(max(self.imgs[p]["size"]) for p in slots.values())
            lmax = 0
            while (big >> (lmax + 1)) >= TEX_MIN:
                lmax += 1
            ent = {"mat": mat, "slots": slots, "density": dens.get(id(mat), 0.0) * big, "lmax": lmax,
                   "level": None, "want": lmax, "nodes": []}
            self.mats[id(mat)] = ent
            self._set_level(ent, lmax, sync=True)       # far-away version at once, the rest on demand

    def _set_level(self, ent, level, sync=False):
        ready = True
        for pid in ent["slots"].values():
            if level not in self.imgs[pid]["levels"]:
                ready = False
                if sync:
                    data, size = self._decode(self.imgs[pid]["payload"], level, fast=True)
                    self._upload(pid, level, data, size)
                    # the exact (box-filtered) version replaces it in the background, after any real request
                    self.pending.add((pid, level))
                    self.seq += 1
                    self.jobs.put((level + 100, self.seq, pid, self.imgs[pid]["payload"], level))
                elif (pid, level) not in self.pending:
                    self.pending.add((pid, level))
                    self.seq += 1
                    self.jobs.put((level, self.seq, pid, self.imgs[pid]["payload"], level))
        if sync:
            ready = True
        if not ready:
            return False
        old = ent["level"]
        for name, pid in ent["slots"].items():
            img = self.imgs[pid]
            setattr(ent["mat"], name, img["levels"][level])
            img["users"][level] = img["users"].get(level, 0) + 1
            if old is not None:
                img["users"][old] = img["users"].get(old, 1) - 1
                if img["users"][old] <= 0 and old != level:
                    self._release(pid, old)
        ent["level"] = level
        return True

    def add_node(self, node):
        for slot, mat in node._fs_model.parts:
            ent = self.mats.get(id(mat))
            if ent is not None and node not in ent["nodes"]:
                ent["nodes"].append(node)

    def forget_nodes(self):
        for ent in self.mats.values():
            ent["nodes"] = [n for n in ent["nodes"] if n.parent is not None]

    def update(self, dt):
        # finished decodes go to the GPU, a few milliseconds' worth a frame (no hitch)
        t0 = time.perf_counter()
        while not self.done.empty() and time.perf_counter() - t0 < 0.004:
            _prio, _seq, pid, level, data, size = self.done.get()
            self.pending.discard((pid, level))
            if data is not None and pid in self.imgs:
                self._upload(pid, level, data, size)
            elif data is None:
                _warn("texture not streamed (%s)" % size)
        for ent in self.mats.values():
            if ent["want"] != ent["level"] and ent["want"] is not None:
                self._set_level(ent, ent["want"])
        self.t -= dt
        if self.t > 0:
            return
        self.t = 0.2
        wanted = {(pid, ent["want"]) for ent in self.mats.values() for pid in ent["slots"].values()}
        for pid, img in self.imgs.items():
            for L in list(img["levels"]):
                if img["users"].get(L, 0) <= 0 and (pid, L) not in wanted:
                    self._release(pid, L)
        cam = self.e.camera
        c = cam.position
        h = max(1, int(getattr(self.e, "height", 1080) or 1080))
        pix = 2.0 * math.tan(math.radians(float(getattr(cam, "fov", 60.0)) * 0.5)) / h   # radians per pixel
        for ent in self.mats.values():
            nodes = [n for n in ent["nodes"] if n.visible]
            if not nodes or ent["density"] <= 0.0:
                want = ent["lmax"]
            else:
                d = min(math.sqrt((n.position.x - c.x) ** 2 + (n.position.y - c.y) ** 2 + (n.position.z - c.z) ** 2)
                        - n._fs_model.radius for n in nodes)
                d = max(d * 0.8, 0.05)                    # (asked for a little early: sharp before you get there)
                # level L shows density / 2^L texels per metre; a pixel there spans d * pix metres.
                # Anisotropic filtering (4x) can sample up to 4x finer at grazing angles: allow for it.
                ratio = ent["density"] * d * pix / ANISO  # texels per pixel at full size
                want = 0
                while want < ent["lmax"] and ratio >= (2 ** (want + 1)) * 1.15:   # (15 % margin)
                    want += 1
            ent["want"] = want

    def settled(self):
        return (not self.pending and self.done.empty()
                and all(e["want"] == e["level"] for e in self.mats.values()))

    def stats(self):
        n = 0
        for img in self.imgs.values():
            w, h = img["size"]
            for L in img["levels"]:
                n += (w >> L) * (h >> L) * 4 * 4 // 3
        return n


def _stream(engine):
    ts = STATE.get("stream")
    if ts is None or ts.e is not engine:
        ts = STATE["stream"] = TexStream(engine)
    return ts


def _lib(engine):
    lib = STATE.get("lib")
    if lib is None or lib.engine is not engine:
        lib = STATE["lib"] = Library(engine)
    return lib


# =========================================================================== placing models
def _place(engine, parent, md, name, pos, yaw=0.0, ex=0.0, ez=0.0, collider="box"):
    """A copy of a model: a node with one child per part, sharing the
    model's meshes and materials.  Never saved (the map rebuilds it)."""
    m = _m()
    glm = _glm()
    node = m.SceneNode(name, parent=parent)
    node._heaven3d_imported_part = "farscream"
    node.position = glm.vec3(pos)
    node.rotation_euler = glm.vec3(ex, yaw, ez)
    node._fs_model = md
    node._fs_lod = 0
    kids = []
    for slot, mat in md.parts:
        ch = m.SceneNode(name + " part", parent=node)
        ch._heaven3d_imported_part = "farscream"
        ch.mesh, ch.material = slot[0], mat
        ch._fs_lods = slot
        kids.append(ch)
    node._fs_kids = kids
    node._fs_wheels = []
    for wm, mat, pivot, radius in md.wheels:
        w = m.SceneNode(name + " wheel", parent=node)
        w._heaven3d_imported_part = "farscream"
        w.mesh, w.material = wm, mat
        w.position = glm.vec3(pivot)
        w._fs_radius = radius
        node._fs_wheels.append(w)
    if md.lo is not None and collider in ("box", "vehicle"):
        size = md.hi - md.lo
        node.collider = m.BoxCollider(size=(max(size.x, 0.05), max(size.y, 0.05), max(size.z, 0.05)))
        node.collider.center = (md.lo + md.hi) * 0.5
        if collider == "vehicle":
            node.rigidbody = m.RigidBody(body_type="kinematic")
    elif collider == "mesh" and md.parts:
        node.mesh_type = "fs_mesh"
        node._fs_proxy = _lib(engine).proxy(md)
        if node._fs_proxy is not None:
            node.collider = m.MeshCollider()
    return node


def _mesh_shape_view_fs(prev):
    def view(node, col):
        proxy = getattr(node, "_fs_proxy", None)
        if proxy is None or getattr(node, "mesh_type", None) != "fs_mesh":
            return prev(node, col)
        real = node.mesh
        node.mesh = proxy
        try:
            return prev(node, col)
        finally:
            node.mesh = real
    return view


def _update_lods(engine):
    """Every prop shows the lightest level of detail whose error is still
    under a pixel at its distance from the camera."""
    cam = engine.camera.position
    cx, cy, cz = float(cam.x), float(cam.y), float(cam.z)
    for mr in STATE["maps"].values():
        for node in mr.props:
            md = node._fs_model
            if len(md.lods) < 2:
                continue
            p = node.position
            d = math.sqrt((p.x - cx) ** 2 + (p.y - cy) ** 2 + (p.z - cz) ** 2) - md.radius
            lv = 0
            for k in range(len(md.lods) - 1, 0, -1):
                if d >= md.lods[k]:
                    lv = k
                    break
            if lv != node._fs_lod:
                node._fs_lod = lv
                for ch in node._fs_kids:
                    ch.mesh = ch._fs_lods[min(lv, len(ch._fs_lods) - 1)]


# =========================================================================== the map at run time
class MapRuntime:
    def __init__(self, node, bp):
        self.node, self.bp = node, bp
        self.layout = None
        self.layout_key = None
        self.built_key = None
        self.props = []
        self.items = []           # (item dict, node)
        self.vehicles = []
        self.fires = []
        self.ground = None        # island runtime

    # ---- what the map stands on
    def island_node(self, engine):
        name = str(self.bp.properties.get("island") or "")
        for n in engine.scene.root.children:
            if n.name == name and any(type(b).__name__ == "TropicalIsland" for b in n.blueprints):
                return n
        return None

    def height(self, x, z):
        rt = self.ground
        if rt is None or rt.isl is None:
            return 0.0
        return rt.height_at(x, z)

    def clear(self, engine):
        for n in list(self.node.children):
            if getattr(n, "_heaven3d_imported_part", None) == "farscream":
                if getattr(engine, "_playing", False):
                    try:
                        engine.physics.remove_node(n)
                    except Exception:
                        pass
                n.remove()
        for f in self.fires:
            try:
                f.remove()
            except Exception:
                pass
        self.props, self.items, self.vehicles, self.fires = [], [], [], []
        self.built_key = None
        if STATE.get("stream") is not None:
            STATE["stream"].forget_nodes()

    # ---- build the props from the layout
    def build(self, engine, rt):
        t0 = time.time()
        self.clear(engine)
        self.ground = rt
        lib = _lib(engine)
        glm = _glm()
        wp = rt.node.world_position()
        for k, it in enumerate(self.layout["items"]):
            md = lib.get(it["model"])
            if not md.parts:
                continue
            x, z = it["x"] + wp.x, it["z"] + wp.z
            if "y" in it and it["model"] in ("CargoShip",):
                y = float(it["y"]) + wp.y
            else:
                y = self.height(x, z) + float(it.get("y", 0.0)) + float(it.get("y_off", 0.0))
            # pitch: nose up, roll: right side down (degrees, in the model's own frame)
            pitch, roll = float(it.get("pitch", 0.0)), float(it.get("roll", 0.0))
            yawr = math.radians(float(it["yaw"]))
            plus_x = L.FRONT.get(it["model"], "+z") == "+x"
            if plus_x:                                          # world directions of the model's front / right
                fx, fz = math.cos(yawr), -math.sin(yawr)
                hl, hw = (md.hi.x - md.lo.x) * 0.45, (md.hi.z - md.lo.z) * 0.45
            else:
                fx, fz = math.sin(yawr), math.cos(yawr)
                hl, hw = (md.hi.z - md.lo.z) * 0.45, (md.hi.x - md.lo.x) * 0.45
            rx, rz = -fz, fx
            hl, hw = max(hl, 0.2), max(hw, 0.2)
            if "y" not in it or it["model"] not in ("CargoShip",):
                hf, hb = self.height(x + fx * hl, z + fz * hl), self.height(x - fx * hl, z - fz * hl)
                hr, hlft = self.height(x + rx * hw, z + rz * hw), self.height(x - rx * hw, z - rz * hw)
                if it.get("tilt"):                              # lie on the slope
                    pitch += math.degrees(math.atan2(hf - hb, 2 * hl))
                    roll -= math.degrees(math.atan2(hr - hlft, 2 * hw))
                    y = min(y, (hf + hb + hr + hlft) * 0.25) - 0.05
                else:                                           # stand level, the low corner on the ground
                    y = min(self.height(x, z), hf, hb, hr, hlft) + float(it.get("y", 0.0)) + float(it.get("y_off", 0.0))
            if md.lo is not None and it["model"] not in ("CargoShip",):
                y -= md.lo.y                                    # the model's base on the ground
            ex, ez = (roll, pitch) if plus_x else (-pitch, roll)
            node = _place(engine, self.node, md, "%s %d" % (it["model"], k), glm.vec3(x, y, z),
                          float(it["yaw"]), ex, ez, it.get("collider"))
            node._fs_item = it
            _stream(engine).add_node(node)
            self.props.append(node)
            self.items.append((it, node))
            if it.get("role") == "vehicle":
                self.vehicles.append(node)
            if it.get("role") == "fire":
                self._fire(engine, node)
            elif it.get("role") == "plane":
                self._smoke(engine, node)
        if getattr(engine, "_playing", False):          # built while playing: the physics world is already made
            for node in self.props:
                if node.collider is not None:
                    try:
                        engine.physics.add_node(node)
                    except Exception as ex:
                        _warn("%s: no collision (%s)" % (node.name, ex))
        start = self.layout.get("start")
        sp = engine.find_player_spawn()
        if start and sp is not None and self.bp.properties.get("move_spawn", True):
            x, z = start["x"] + wp.x, start["z"] + wp.z
            sp.position = glm.vec3(x, self.height(x, z) + 1.2, z)
            sp.rotation_euler = glm.vec3(0.0, float(start["yaw"]), 0.0)
        self.built_key = (self.layout_key, rt.key)
        _log("FarScream map '%s' furnished in %.1f s: %d objects, %d sites, %d soldiers, %d islanders" % (
            self.node.name, time.time() - t0, len(self.props), len(self.layout["sites"]),
            sum(p["kind"] == "soldier" for p in self.layout["posts"]),
            sum(p["kind"] == "native" for p in self.layout["posts"])))

    def _smoke(self, engine, node):
        """A thin column of smoke over the wreck: you see the crash from afar."""
        try:
            presets = getattr(_m(), "GU_PARTICLE_PRESETS", None) or {}
            preset = dict(presets["Smoke Plume"]) if "Smoke Plume" in presets else "Smoke Plume"
            if isinstance(preset, dict):
                preset["rate"] = min(float(preset.get("rate", 40)), 26.0)
            p = node.world_position() + _glm().vec3(0, 1.5, 0)
            f = engine.add_particle_system(preset, p)
            if f is not None:
                f.remove()
                self.node.add_child(f)
                f._heaven3d_imported_part = "farscream"
                f.position = node.position + _glm().vec3(0, 1.5, 0)
                self.fires.append(f)
        except Exception as ex:
            _warn("crash smoke: %s" % ex)

    def _fire(self, engine, node):
        """Flames on a campfire."""
        try:
            preset = None
            presets = getattr(_m(), "GU_PARTICLE_PRESETS", None)
            if presets and "Fire" in presets:
                preset = dict(presets["Fire"])
                preset["emitter_radius"] = 0.28
                preset["rate"] = min(float(preset.get("rate", 300)), 260.0)
                preset["size_start"] = 0.32
                preset["size_end"] = 0.08
            f = engine.add_particle_system(preset or "Fire", node.world_position() + _glm().vec3(0, 0.2, 0))
            if f is not None:
                f.remove()
                self.node.add_child(f)
                f._heaven3d_imported_part = "farscream"
                f.position = node.position + _glm().vec3(0, 0.2, 0)
                self.fires.append(f)
        except Exception as ex:
            _warn("campfire flames: %s" % ex)


def _map_of(node):
    mr = STATE["maps"].get(id(node))
    if mr is None or mr.node is not node:
        bp = next((b for b in node.blueprints if type(b).__name__ == "FarScreamMap"), None)
        if bp is None:
            return None
        mr = STATE["maps"][id(node)] = MapRuntime(node, bp)
    return mr


def _maps(engine):
    out = []
    for n in engine.scene.root.children:
        if any(type(b).__name__ == "FarScreamMap" for b in n.blueprints):
            out.append(_map_of(n))
    alive = {id(m.node) for m in out}
    for k in [k for k in STATE["maps"] if k not in alive]:
        STATE["maps"].pop(k, None)
    return out


def _layout_key(bp):
    p = bp.properties
    return (int(p.get("seed", 1)), int(p.get("camps", 3)), int(p.get("villages", 2)))


def _island_hook(prev):
    """IslandRuntime._build_terrain: before the terrain is made, a FarScream
    island gets its sites flattened and cleared (the same every time)."""
    def build_terrain(self):
        try:
            engine = STATE.get("engine") or self.engine
            for mr in _maps(engine):
                if mr.island_node(engine) is self.node:
                    isl = self.isl
                    key = _layout_key(mr.bp)
                    pristine = getattr(isl, "_fs_pristine", None)
                    if getattr(isl, "_fs_key", None) != key:
                        if pristine is not None:
                            isl.heights, isl.splat, isl.instances = (pristine[0].copy(), pristine[1].copy(),
                                                                     {k: v.copy() for k, v in pristine[2].items()})
                        else:
                            isl._fs_pristine = (isl.heights.copy(), isl.splat.copy(),
                                                {k: v.copy() for k, v in isl.instances.items()})
                        lay = L.make_layout(isl, key[0], camps=key[1], villages=key[2])
                        L.apply_pads(isl, lay)
                        isl._fs_key = key
                        isl._fs_layout = lay
                    mr.layout = isl._fs_layout
                    mr.layout_key = key
                    mr.built_key = None
                    break
        except Exception as ex:
            import traceback
            traceback.print_exc()
            _warn("FarScream layout failed: %s" % ex)
        return prev(self)
    return build_terrain


def _ensure_built(engine):
    for mr in _maps(engine):
        isl_node = mr.island_node(engine)
        isl_mod = _mod(ISL)
        if isl_node is None or isl_mod is None:
            continue
        rt = isl_mod.runtime_of(isl_node)
        if rt is None or rt.isl is None or not rt.terrain_nodes:
            continue
        if mr.layout is None or getattr(rt.isl, "_fs_key", None) != _layout_key(mr.bp):
            continue                       # (the island hook hasn't laid this seed out yet)
        if mr.built_key != (mr.layout_key, rt.key) or not mr.props or mr.props[0].parent is not mr.node:
            mr.build(engine, rt)


# =========================================================================== generating a map
def generate(engine, seed=None, node=None):
    """A new FarScream map (or a new seed for an existing one): the island
    and everything on it.  -> the map node"""
    m = _m()
    glm = _glm()
    isl_mod = _mod(ISL)
    if isl_mod is None:
        _warn("FarScream needs the Tropical Islands package")
        return None
    STATE["engine"] = engine
    if node is None:
        node = next((n for n in engine.scene.root.children
                     if any(type(b).__name__ == "FarScreamMap" for b in n.blueprints)), None)
    if node is None:
        node = m.SceneNode(engine._unique_node_name("FarScream Map"), parent=engine.scene.root)
        engine.attach_blueprint(node, CLS["FarScreamMap"])
    bp = next(b for b in node.blueprints if type(b).__name__ == "FarScreamMap")
    seed = int(seed if seed is not None else random.randrange(1, 1 << 30))
    bp.properties["seed"] = seed
    mr = _map_of(node)
    mr.clear(engine)
    old = mr.island_node(engine)
    if old is not None:
        engine._delete_node(old)
    for n in [n for n in engine.scene.root.children if n.name == "Ocean"]:
        n.visible = True
    rng = random.Random(seed)
    topo = str(bp.properties.get("topology") or "random")
    if topo not in TOPOLOGIES:
        topo = rng.choice(TOPOLOGIES)
    name = engine._unique_node_name("FarScream Island")
    bp.properties["island"] = name
    params = {"seed": seed, "topology": topo, "size": float(bp.properties.get("size", 1000.0)),
              "roads": 6, "jungle": 0.72, "coast_roughness": 0.55, "erosion": 0.5}
    t0 = time.time()
    isl_node = isl_mod.new_island(engine, params, name=name, select=False, move_spawn=False)
    try:                                    # keep the island and its map next to each other in the outliner
        root = engine.scene.root.children
        root.remove(isl_node)
        root.insert(root.index(node), isl_node)
    except Exception:
        pass
    _ensure_built(engine)
    try:
        start = mr.layout.get("start") if mr.layout else None
        if start:
            engine.camera.position = glm.vec3(start["x"] - 30.0, mr.height(start["x"], start["z"]) + 28.0,
                                              start["z"] + 30.0)
            engine.camera.yaw, engine.camera.pitch = -40.0, -30.0
    except Exception:
        pass
    _log("FarScream: a %s island (seed %d) in %.1f s - press Play" % (topo, seed, time.time() - t0))
    return node


def tool_generate(engine):
    generate(engine)


def tool_same_seed(engine):
    mrs = _maps(engine)
    if not mrs:
        return generate(engine)
    mr = mrs[0]
    generate(engine, seed=int(mr.bp.properties.get("seed", 1)), node=mr.node)


# =========================================================================== game helpers
def _player(engine):
    pc = engine.first_blueprint("FPSController")
    return pc


def _holder(engine):
    pc = _player(engine)
    if pc is None:
        return None
    return next((b for b in pc.node.blueprints if type(b).__name__ == "HQWeapons"), None)


def _give_ammo(engine, n):
    h = _holder(engine)
    if h is None:
        return 0
    weapon = next((w for w in h.names if h.ammo_type(w) == "45ACP"), None)
    if weapon is None:
        return 0
    return h.add_reserve(int(n), weapon) or 0


def _horiz(a, b):
    return math.hypot(a.x - b.x, a.z - b.z)


def _ids(node):
    return {id(n) for n in node.traverse()}


def _project(engine, p):
    """World point -> (x, y) on the HUD (0..1, y from the top), or None behind the camera."""
    glm = _glm()
    cam = engine.camera
    c = cam.projection() * cam.view() * glm.vec4(p.x, p.y, p.z, 1.0)
    if c.w <= 0.05:
        return None
    x, y = c.x / c.w, c.y / c.w
    if abs(x) > 1.05 or abs(y) > 1.05:
        return None
    return (x * 0.5 + 0.5, 1.0 - (y * 0.5 + 0.5))


def _toast(engine, text, color=(1.0, 0.85, 0.3, 1.0), secs=3.0):
    STATE["toast"] = (text, color, float(getattr(engine, "time", 0.0)) + secs)


# =========================================================================== blueprints
def _blueprints():
    m = _m()
    Blueprint, blueprint, glm = m.Blueprint, m.blueprint, m.glm

    @blueprint
    class FarScreamMap(Blueprint):
        """A FarScream map: an island furnished with camps, villages, wrecks
        and loot, all made from 'seed'.  Packages > FarScream: generate new
        map makes a new one.  The game runs while you play."""

        DEFAULTS = {"seed": 1, "island": "", "topology": "random", "size": 1000.0, "camps": 3, "villages": 2,
                    "soldier_health": 70.0, "islander_health": 55.0, "difficulty": 1.0, "move_spawn": True,
                    "spawn_distance": 140.0}

        def __init__(self):
            super().__init__()
            for k, v in self.DEFAULTS.items():
                self.properties.setdefault(k, v)
            self.game = None

        def on_begin_play(self):
            self.game = Game(self.engine, self)

        def on_end_play(self):
            if self.game is not None:
                self.game.stop()
            self.game = None

        def on_tick(self, dt):
            if self.game is not None:
                self.game.tick(dt)

        def on_message(self, event, data):
            if self.game is not None:
                self.game.message(event, data)

    CLS["FarScreamMap"] = FarScreamMap

    @blueprint
    class FSProp(Blueprint):
        """(Play only) lets bullets reach a fuel drum or a truck."""

        def __init__(self):
            super().__init__()
            self.game = None

        def on_message(self, event, data):
            if event != "damage" or self.game is None:
                return
            amt = float(data.get("amount", 0)) if isinstance(data, dict) else float(data or 0)
            g = self.game
            g.damage_prop(self.node, amt)
            if g.drive is not None and self.node is g.drive.node and g.drive.driver is not None \
                    and isinstance(data, dict) and data.get("source") == "enemy":
                g.drive.driver.on_message("damage", {"amount": amt * 0.35, "source": "enemy"})

    CLS["FSProp"] = FSProp


# =========================================================================== the game
class _CharPrep:
    """Builds the islanders and soldiers before you meet them.

    A MakeHuman character costs a few hundred milliseconds of CPU: the body
    and clothes are fitted, every animation of its moveset is retargeted to
    its proportions.  Done on the frame it appears that is a hitch, so a
    worker thread does it for the characters nearest to the player first;
    the frame it appears only uploads it to the GPU."""

    def __init__(self, chars):
        import threading
        import queue
        self.chars = chars
        self.todo = queue.Queue()
        self.done = queue.Queue()
        self.asked = set()
        self.ready = set()
        self.failed = set()
        self.up = None                           # (key, model, geos made so far)
        self.stop = False
        self.th = threading.Thread(target=self._work, daemon=True)
        self.th.start()

    def ask(self, key, spec, moveset):
        if key not in self.asked:
            self.asked.add(key)
            self.todo.put((key, spec, moveset))

    def _work(self):
        ch = self.chars
        while not self.stop:
            key, spec, ms = self.todo.get()
            if key is None:
                return
            try:
                lib, anim = ch._lib(), ch.ANIM
                model = ch.CORE.build(lib, spec)
                library = ch._library()
                if library:
                    tmp = "_farscream_%x" % id(model)
                    for slot, ns in anim.MOVESETS.get(ms, {}).items():
                        for n in ns:
                            if n in library:
                                anim.MOVESETS[tmp] = {slot: [n]}
                                try:
                                    anim.clipset(model, tmp, 0, library)
                                finally:
                                    anim.MOVESETS.pop(tmp, None)
                    cache = getattr(model, "_clip_cache", None) or {}
                    idle = next((cache[(n, True)] for n in anim.MOVESETS.get(ms, {}).get("idle", [])
                                 if cache.get((n, True))), None)
                    if idle and not hasattr(model, "_arm_clear"):
                        try:
                            model._arm_clear = anim.arm_clearance(model, idle)
                        except Exception:
                            model._arm_clear = {}
                    anim.clipset(model, ms, 0, library)          # (and the hello wave, made from the idle)
                    if ms == "pistol":
                        ch._gun_grip(model, library)
                self.done.put((key, model))
            except Exception as ex:
                self.done.put((key, None))
                print("[FarScream] character not prepared: %s" % ex)

    def collect(self, engine):
        """Finished characters go to the GPU one mesh part a frame."""
        ch = self.chars
        if self.up is None:
            if self.done.empty():
                return
            key, model = self.done.get()
            if model is None:
                self.failed.add(key)             # (it is then built the usual way when it appears)
                return
            if key in ch._MODELS:
                self.ready.add(key)
                return
            self.up = (key, model, [])
        key, model, geos = self.up
        geos.append(ch._make_geo(engine.ctx, model.parts[len(geos)], model.height))
        if len(geos) < len(model.parts):
            return
        self.up = None
        ch._MODELS[key] = (model, geos, None, {})
        ch._ORDER.append(key)
        while len(ch._ORDER) > 48:
            ch._MODELS.pop(ch._ORDER.pop(0), None)
        self.ready.add(key)

    def close(self):
        self.stop = True
        self.todo.put((None, None, None))


class Game:
    """Everything that happens in Play: characters near the player, loot,
    crates, fuel drums, trucks, objectives and the HUD."""

    def __init__(self, engine, bp):
        self.e, self.bp = engine, bp
        self.mr = _map_of(bp.node)
        self.t = 0.0
        self.chars = {}             # post index -> node
        self.dead = set()           # post indices that died
        self.post_of = {}           # id(node) -> post index
        self.loot = []              # FSLoot dicts
        self.spawn_q = []
        self.opened = set()         # item indices of opened crates
        self.blown = set()          # item indices of exploded drums
        self.drive = None           # the Vehicle being driven
        self.vehicles = []
        self.found = set()          # objectives reached ("crash", "ship")
        self.armed = False
        self.won = False
        self.hidden = []            # (node, collider) of blown drums, restored on Stop
        self.saved_tf = []
        self.prop_bps = []
        self.prep = None
        self.specs = {}
        self.ready = False
        self.wp = _glm().vec3(0.0)
        self.waited = 0.0
        self._setup()

    def _setup(self):
        """Everything that needs the furnished map.  An exported game (or a
        project opened straight into Play) builds the island on its first
        frames, so this runs as soon as the map is there."""
        engine = self.e
        _ensure_built(engine)
        if self.mr is None or self.mr.layout is None or not self.mr.props or self.mr.ground is None:
            return False
        self.ready = True
        lay = self.mr.layout
        wp = self.mr.ground.node.world_position() if self.mr.ground is not None else _glm().vec3(0)
        self.wp = wp
        for node in self.mr.props:
            self.saved_tf.append((node, _glm().vec3(node.position), _glm().vec3(node.rotation_euler), node.visible))
        for node in self.mr.vehicles:
            self.vehicles.append(Vehicle(engine, node, self))
        for it, node in self.mr.items:
            if it.get("role") in ("explosive", "vehicle"):
                bp = engine.attach_blueprint(node, CLS["FSProp"])
                if bp is not None:
                    bp.game = self
                    self.prop_bps.append(bp)
        for k, lt in enumerate(lay.get("loot", [])):
            self._drop(_glm().vec3(lt["x"] + wp.x, 0.0, lt["z"] + wp.z), weapon=lt.get("weapon"),
                       ammo=int(lt.get("ammo", 0)))
        try:
            m = _m()
            m._FP2_DECAL_MAX = DECAL_CAP
        except Exception:
            pass
        _toast(engine, "Liberate the island: clear the camps, destroy the radio station", secs=6.0)
        return True

    # ---------------------------------------------------------------- setup of the player
    def _arm(self):
        pc = _player(self.e)
        if pc is None:
            return
        self.armed = True
        wep = _mod(WEP)
        if wep is not None:
            if _holder(self.e) is None and "HQWeapons" in getattr(wep, "CLS", {}):
                self.e.attach_blueprint(pc.node, wep.CLS["HQWeapons"],
                                        {"weapons": "M1911", "start_index": 0, "reserve_magazines": 2})

    # ---------------------------------------------------------------- characters
    def _post_pos(self, k):
        p = self.mr.layout["posts"][k]
        x, z = p["x"] + self.wp.x, p["z"] + self.wp.z
        return _glm().vec3(x, self.mr.height(x, z), z)

    def _spec_key(self, k):
        hit = self.specs.get(k)
        if hit is None:
            chars = _mod(CHR)
            p = self.mr.layout["posts"][k]
            spec = self._spec(p)
            key = chars.CORE.spec_key(chars.CORE.normalize_spec(spec, chars._lib()))
            hit = self.specs[k] = (spec, key, "pistol" if p["kind"] == "soldier" else "brawler")
        return hit

    def _stream(self, ppos, dt):
        lay = self.mr.layout
        near = float(self.bp.properties.get("spawn_distance", 140.0))
        chars = _mod(CHR)
        if chars is None:
            return
        if self.prep is None:
            self.prep = _CharPrep(chars)
        self.prep.collect(self.e)
        # get the next ones ready a little before they are needed
        order = sorted((math.hypot(p["x"] + self.wp.x - ppos.x, p["z"] + self.wp.z - ppos.z), k)
                       for k, p in enumerate(lay["posts"]) if k not in self.dead and k not in self.chars)
        for d, k in order[:6]:
            if d < near + 120.0:
                spec, key, ms = self._spec_key(k)
                if key not in chars._MODELS:
                    self.prep.ask(key, spec, ms)
        for k, p in enumerate(lay["posts"]):
            if k in self.dead:
                continue
            d = math.hypot(p["x"] + self.wp.x - ppos.x, p["z"] + self.wp.z - ppos.z)
            node = self.chars.get(k)
            if node is None and d < near and k not in self.spawn_q:
                self.spawn_q.append(k)
            elif node is not None and d > near + 90.0:
                en = self._enemy(node)
                if en is None or en.state != "chase":
                    self._despawn(k)
        self.spawn_q.sort(key=lambda k: math.hypot(lay["posts"][k]["x"] + self.wp.x - ppos.x,
                                                   lay["posts"][k]["z"] + self.wp.z - ppos.z))
        if self.spawn_q:                                     # one a frame, once it is prepared
            k = self.spawn_q[0]
            _spec, key, _ms = self._spec_key(k)
            if key not in chars._MODELS and key not in self.prep.failed and self.prep.th.is_alive():
                return
            self.spawn_q.pop(0)
            if k not in self.chars and k not in self.dead:
                try:
                    self._spawn(k)
                except Exception as ex:
                    import traceback
                    traceback.print_exc()
                    _warn("FarScream: a character could not be made (%s)" % ex)
                    self.dead.add(k)

    @staticmethod
    def _enemy(node):
        return next((b for b in node.blueprints if type(b).__name__ == "MHEnemy"), None)

    def _spec(self, p):
        chars = _mod(CHR)
        lib = chars._lib()
        core = chars.CORE
        rng = random.Random(p["seed"])
        if p["kind"] == "soldier":
            s = core.random_spec(lib, seed=p["seed"], gender="male", style="civilian")
            top = rng.choice([c for c in ("clothes/toigo_basic_tucked_t-shirt", "clothes/namuhekam_male_polo_shirt",
                                          "clothes/elvs_crude_t-shirt_male") if c in lib.parts])
            feet = [c for c in ("clothes/toigo_ankle_boots_male", "clothes/culturalibre_male_boots") if c in lib.parts]
            clothes = ["clothes/cortu_cargo_pants", top] + feet[:1]
            if rng.random() < 0.75 and "clothes/mrgreaterthan_m1_helmet" in lib.parts:
                clothes.append("clothes/mrgreaterthan_m1_helmet")
            olive = [0.42, 0.45, 0.30] if rng.random() < 0.6 else [0.55, 0.5, 0.38]
            s["clothes"] = [{"id": c, "material": "", "color": olive if c in (top, "clothes/cortu_cargo_pants")
                             else [1, 1, 1]} for c in clothes if c in lib.parts]
            if any(c["id"].endswith("helmet") for c in s["clothes"]):
                s["hair"] = rng.choice([h for h in ("hair/short02", "hair/short04") if h in lib.parts] or [""])
                s["hair_material"] = ""
            return s
        g = "male" if rng.random() < 0.6 else "female"
        s = core.random_spec(lib, seed=p["seed"], gender=g, style="civilian")
        if g == "male":
            pool = ["clothes/cortu_jeans_shorts"]
            if rng.random() < 0.45:
                pool.append("clothes/elvs_crude_t-shirt_male")
        else:
            pool = [rng.choice(["clothes/toigo_harem_pants", "clothes/cortu_jeans_shorts", "clothes/toigo_tiered_skirt"]),
                    rng.choice(["clothes/toigo_camisole_top", "clothes/toigo_keyhole_tank_top",
                                "clothes/joepal_crude_t-shirt_female"])]
        if rng.random() < 0.3:
            pool.append(rng.choice(["clothes/ews_hoop_earrings", "clothes/ews_septum_ring"]))
        # islanders: Pacific / Melanesian looks - brown skin, dark hair
        mac = s.get("macro", {})
        a_mix = rng.uniform(0.35, 0.65)
        mac.update({"african": a_mix, "asian": 1.0 - a_mix - 0.05, "caucasian": 0.05})
        skins = [k for k in lib.cat["skins"] if k.startswith(("young_african_", "middleage_african_", "young_asian_",
                                                               "middleage_asian_")) and k.endswith("_" + g)]
        if skins:
            s["skin"] = rng.choice(sorted(skins))
        hair = [h for h in (["hair/afro01", "hair/short02", "hair/short04", "hair/cortu_short_messy_hair", ""]
                            if g == "male" else
                            ["hair/afro01", "hair/braid01", "hair/ponytail01", "hair/long01", "hair/elvs_double_mh_braid"])
                if h == "" or h in lib.parts]
        s["hair"] = rng.choice(hair) if hair else s.get("hair", "")
        s["hair_material"] = ""
        s["hair_color"] = [0.32, 0.27, 0.24]
        earth = rng.choice([[0.62, 0.45, 0.30], [0.55, 0.42, 0.26], [0.70, 0.58, 0.40], [0.48, 0.36, 0.25]])
        s["clothes"] = [{"id": c, "material": "", "color": earth if "ews_" not in c else [1, 1, 1]}
                        for c in pool if c in lib.parts]
        return s

    def _spawn(self, k):
        chars = _mod(CHR)
        if chars is None:
            self.dead.add(k)
            return
        p = self.mr.layout["posts"][k]
        pos = self._post_pos(k)
        spec, _key, ms = self._spec_key(k)
        m = _m()
        glm = _glm()
        node = m.SceneNode(self.e._unique_node_name("%s %d" % (p["kind"].title(), k)), parent=self.e.scene.root)
        cbp = self.e.attach_blueprint(node, chars.CLS["MHCharacter"])
        cbp.properties["character"] = spec
        cbp.properties["moveset"] = ms
        if ms == "pistol":
            guns = chars._guns()
            pistols = [g for g in ("Pistol_1", "Pistol_2", "Pistol_3", "Pistol_5") if g in guns]   # (no revolvers)
            if p.get("weapon") == "MP5" and "MP5" in guns:
                cbp.properties["gun"] = "MP5"
            else:
                cbp.properties["gun"] = pistols[p["seed"] % len(pistols)] if pistols else "auto"
        r = chars.ensure_rig(self.e, node, cbp)
        h = r.height if r else 1.75
        node.position = glm.vec3(pos.x, pos.y + h * 0.5 + 0.01, pos.z)
        node.rotation_euler.y = float(p["yaw"])
        node._fs_char = True
        self.e._spawn_runtime_nodes.append(node)
        diff = float(self.bp.properties.get("difficulty", 1.0))
        if p["kind"] == "soldier":
            mp5 = p.get("weapon") == "MP5"
            props = {"detect_range": 38.0, "fov": 130.0, "hearing_range": 9.0, "require_los": True,
                     "lose_interest": 9.0, "attack_style": "hitscan", "hitscan_range": 34.0,
                     "attack_damage": (4.0 if mp5 else 9.0) * diff, "attack_cooldown": 0.26 if mp5 else 0.95,
                     "max_health": float(self.bp.properties.get("soldier_health", 70.0)),
                     "patrol_speed": 1.1, "chase_speed": 3.3, "wait_time": 3.5, "growl_sound": "",
                     "death_sound": "hurt"}
        else:
            props = {"detect_range": 26.0, "fov": 160.0, "hearing_range": 7.0, "require_los": True,
                     "lose_interest": 7.0, "attack_style": "melee", "attack_range": 1.45,
                     "attack_damage": 11.0 * diff, "attack_cooldown": 1.05,
                     "max_health": float(self.bp.properties.get("islander_health", 55.0)),
                     "patrol_speed": 0.9, "chase_speed": 5.1, "wait_time": 4.0, "growl_sound": "enemy",
                     "death_sound": "hurt"}
        en = self.e.attach_blueprint(node, chars.CLS["MHEnemy"], props)
        if p["kind"] == "soldier" and en is not None:
            en._deal = _soldier_deal(en, p.get("weapon") or "pistol")
            en._fs_weapon = p.get("weapon") or "pistol"
        self.chars[k] = node
        self.post_of[id(node)] = k

    def _despawn(self, k):
        node = self.chars.pop(k, None)
        if node is None:
            return
        self.post_of.pop(id(node), None)
        try:
            self.e.physics.remove_node(node)
        except Exception:
            pass
        for bp in list(node.blueprints):
            try:
                bp.on_end_play()
            except Exception:
                pass
            if bp in self.e._blueprints:
                self.e._blueprints.remove(bp)
        node.remove()
        try:
            self.e._spawn_runtime_nodes.remove(node)
        except ValueError:
            pass

    # ---------------------------------------------------------------- alarms
    def _alert(self, ppos, dt):
        """A camp fights as one: a shot within earshot, or a comrade in a
        fight, and every soldier of the site comes for you."""
        self.alert_t = getattr(self, "alert_t", 0.0) - dt
        if self.alert_t > 0.0:
            return
        self.alert_t = 0.3
        lay = self.mr.layout
        shot = STATE.pop("player_shot", None)
        hot = set()
        for k, node in self.chars.items():
            en = self._enemy(node)
            if en is None or not getattr(en, "alive", False):
                continue
            p = lay["posts"][k]
            if getattr(en, "state", "") == "chase":
                hot.add(p["site"])
            elif shot is not None and p["kind"] == "soldier":
                q = node.world_position()
                if math.hypot(q.x - shot.x, q.z - shot.z) < 60.0:
                    hot.add(p["site"])
        for k, node in self.chars.items():
            p = lay["posts"][k]
            if p["site"] not in hot or p["kind"] != "soldier":
                continue
            en = self._enemy(node)
            if en is not None and getattr(en, "alive", False) and getattr(en, "state", "") != "chase":
                q = node.world_position()
                if math.hypot(q.x - ppos.x, q.z - ppos.z) < 90.0:
                    en.state, en.alert = "chase", float(en.p("lose_interest"))

    # ---------------------------------------------------------------- loot
    def _drop(self, pos, weapon=None, ammo=0, heal=0):
        """Something on the ground to walk over: a gun, a box of .45, a medkit."""
        m = _m()
        glm = _glm()
        wep = _mod(WEP)
        hit = self.e.raycast(pos + glm.vec3(0, 3.0, 0), glm.vec3(0, -1, 0), 10.0, ignore=self._char_ids())
        y = (hit.point.y if hit is not None else self.mr.height(pos.x, pos.z)) + 0.06
        node = m.SceneNode("Loot", parent=self.e.scene.root)
        node.position = glm.vec3(pos.x, y, pos.z)
        node.rotation_euler = glm.vec3(0.0, random.uniform(0, 360), 0.0)
        parts = []
        try:
            if weapon and wep is not None:
                for mesh, mat in wep._weapon_prop(self.e, weapon):
                    ch = m.SceneNode("loot part", parent=node)
                    ch.mesh, ch.material = mesh, mat
                    ch.rotation_euler = glm.vec3(0.0, 0.0, 90.0)
                    ch.position = glm.vec3(0, 0.05, 0)
                    parts.append(ch)
            if ammo and wep is not None:
                for mesh, mat in wep._ammo_prop(self.e, "45ACP"):
                    ch = m.SceneNode("loot part", parent=node)
                    ch.mesh, ch.material = mesh, mat
                    ch.position = glm.vec3(0.35 if weapon else 0.0, 0.0, 0.0)
                    parts.append(ch)
        except Exception as ex:
            _warn("loot model: %s" % ex)
        self.e._spawn_runtime_nodes.append(node)
        self.loot.append({"node": node, "weapon": weapon, "ammo": int(ammo), "heal": int(heal), "t": 0.0})
        return node

    def _take_loot(self, ppos):
        for lt in list(self.loot):
            node = lt["node"]
            if node.parent is None:
                self.loot.remove(lt)
                continue
            p = node.world_position()
            if _horiz(p, ppos) > 1.5 or abs(p.y - ppos.y) > 2.4:
                continue
            got = []
            h = _holder(self.e)
            if lt["weapon"] and h is not None:
                if lt["weapon"] in h.names:
                    n = _give_ammo(self.e, 30)
                    if n:
                        got.append("%d rounds" % n)
                elif h.add_weapon(lt["weapon"], 1):
                    got.append(lt["weapon"])
            if lt["ammo"]:
                n = _give_ammo(self.e, lt["ammo"])
                if n:
                    got.append("%d rounds of .45" % n)
            if lt["heal"]:
                pc = _player(self.e)
                if pc is not None and pc.hp < pc.max_hp:
                    pc.on_message("heal", {"amount": lt["heal"]})
                    got.append("+%d health" % lt["heal"])
            if not got and (lt["weapon"] or lt["ammo"]):
                continue                                    # full: leave it there
            self.e.audio.play_at("pickup", p, 0.7)
            _toast(self.e, "Picked up " + ", ".join(got) if got else "Nothing useful", (0.6, 1.0, 0.6, 1.0), 2.2)
            node.remove()
            self.loot.remove(lt)

    def _char_ids(self):
        out = set()
        for n in self.chars.values():
            out |= _ids(n)
        pc = _player(self.e)
        if pc is not None:
            out |= _ids(pc.node)
        return out

    # ---------------------------------------------------------------- messages
    def message(self, event, data):
        if event == "died" and isinstance(data, dict):
            node = data.get("node")
            k = self.post_of.get(id(node))
            if k is None:
                return
            self.dead.add(k)
            p = self.mr.layout["posts"][k]
            if p["kind"] == "soldier":
                w = p.get("weapon")
                at = node.world_position() + _glm().vec3(random.uniform(-0.5, 0.5), 0, random.uniform(0.4, 0.9))
                self._drop(at, weapon="MP5" if w == "MP5" else None, ammo=random.randint(7, 18))
                self._check_sites()
        elif event == "damage" and isinstance(data, dict):
            pass

    def _check_sites(self):
        lay = self.mr.layout
        for s in lay["sites"]:
            if s["kind"] not in ("camp", "radio") or s.get("_cleared"):
                continue
            ks = [k for k, p in enumerate(lay["posts"]) if p["site"] == s["index"] and p["kind"] == "soldier"]
            if ks and all(k in self.dead for k in ks):
                s["_cleared"] = True
                _toast(self.e, "%s cleared" % s["name"], (0.5, 1.0, 0.5, 1.0), 4.0)

    # ---------------------------------------------------------------- props: crates and drums
    def damage_prop(self, node, amount):
        it = getattr(node, "_fs_item", None)
        if it is None or it.get("role") != "explosive":
            return
        k = self.mr.items.index((it, node)) if (it, node) in self.mr.items else None
        if k is None or k in self.blown or not node.visible:
            return
        node._fs_hp = getattr(node, "_fs_hp", 30.0) - float(amount)
        if node._fs_hp <= 0:
            self._explode(k, node)

    def _explode(self, k, node):
        e = self.e
        glm = _glm()
        self.blown.add(k)
        p = node.world_position() + glm.vec3(0, 0.5, 0)
        for n in node.traverse():
            n.visible = False
        try:
            e.physics.remove_node(node)
        except Exception:
            pass
        self.hidden.append(node)
        e.audio.play_at("explosion", p, 1.0)
        try:
            e.spawn_flash(p, color=(1.0, 0.6, 0.25), intensity=90.0, range=18.0, ttl=0.25)
        except Exception:
            pass
        try:
            f = e.add_particle_system("Explosion", p)
            if f is not None:
                cfg = f.user_data.get("particle_system")
                e._spawn_runtime_nodes.append(f)
                e.schedule(0.18, lambda: cfg.__setitem__("emitting", False) if cfg else None)
                e.schedule(2.5, lambda: e.destroy_node(f))
        except Exception:
            pass
        try:                                              # bullet holes on the drum go with it
            m = _m()
            for fx in list(getattr(m, "_FP2_DECALS", [])):
                dn = fx.get("node")
                if dn is not None and glm.length(dn.world_position() - p) < 1.3:
                    m._fp2_kill_fx(e, fx)
        except Exception:
            pass
        # blast: hurts everyone around, sets off the next drum
        R = 7.5
        pc = _player(e)
        if pc is not None:
            d = glm.length(pc.node.world_position() - p)
            if d < R:
                pc.on_message("damage", {"amount": 70.0 * (1.0 - d / R) ** 1.2, "source": "explosion"})
            if d < 30:
                STATE["shake"] = max(STATE.get("shake", 0.0), 1.0 - d / 30.0)
        for node2 in list(self.chars.values()):
            en = self._enemy(node2)
            if en is None or not getattr(en, "alive", False):
                continue
            d = glm.length(node2.world_position() - p)
            if d < R:
                e.message_ancestors(node2, "damage", {"amount": 160.0 * (1.0 - d / R), "point": p, "source": "explosion"})
        for (it2, n2) in self.mr.items:
            if it2.get("role") == "explosive" and n2 is not node and n2.visible:
                if glm.length(n2.world_position() - p) < 4.5:
                    j = self.mr.items.index((it2, n2))
                    e.schedule(random.uniform(0.15, 0.35), lambda j=j, n2=n2: (j not in self.blown) and self._explode(j, n2))
        if self.drive is None:
            for v in self.vehicles:
                d = glm.length(v.node.world_position() - p)
                if d < 6:
                    v.kick(glm.normalize(v.node.world_position() - p + glm.vec3(0, 0.5, 0)) * (6 - d) * 1.5)
        if it_obj(self.mr.items[k][0]) == "radio":
            if all(j in self.blown for j, (it3, _n) in enumerate(self.mr.items) if it_obj(it3) == "radio"):
                _toast(e, "Radio station destroyed - they can't call for help", (0.5, 1.0, 0.5, 1.0), 5.0)

    def _interact(self, ppos):
        """F near a crate opens it; F near a truck gets in."""
        e = self.e
        glm = _glm()
        best, bd = None, 2.6
        for k, (it, node) in enumerate(self.mr.items):
            if it.get("role") != "crate" or k in self.opened:
                continue
            d = _horiz(node.world_position(), ppos)
            if d < bd:
                best, bd = ("crate", k, node), d
        for v in self.vehicles:
            d = v.door_distance(ppos)
            if d < 3.2 and (best is None or d < bd + 0.6):
                best, bd = ("vehicle", v, None), d
        return best

    def _open_crate(self, k, node):
        it = self.mr.items[k][0]
        self.opened.add(k)
        loot = it.get("loot") or {}
        got = []
        if loot.get("ammo"):
            n = _give_ammo(self.e, loot["ammo"])
            if n:
                got.append("%d rounds of .45" % n)
        if loot.get("heal"):
            pc = _player(self.e)
            if pc is not None:
                pc.on_message("heal", {"amount": loot["heal"]})
                got.append("medkit (+%d)" % loot["heal"])
        self._open_lid(node)
        self.e.audio.play_at("pickup", node.world_position(), 0.8)
        _toast(self.e, ("Crate: " + ", ".join(got)) if got else "The crate is empty", (0.6, 1.0, 0.6, 1.0), 2.5)

    @staticmethod
    def _open_lid(node):
        """Swing the crate's lid open on its back hinge."""
        glm = _glm()
        for ch in getattr(node, "_fs_kids", []):
            if "lid" not in str(getattr(ch.material, "name", "")).lower():
                continue
            mesh = ch.mesh
            bb = mesh.bounding_box
            hinge = glm.vec3(0.0, bb[1].y, bb[0].z)            # top back edge (the crate's front is +Z)
            ang = -75.0
            R = glm.mat3(glm.rotate(glm.mat4(1.0), math.radians(ang), glm.vec3(1, 0, 0)))
            ch.rotation_euler = glm.vec3(ang, 0.0, 0.0)
            ch.position = hinge - R * hinge
            ch._fs_lid = True

    # ---------------------------------------------------------------- objectives
    def objectives(self, ppos):
        lay = self.mr.layout
        out = []
        camps = [s for s in lay["sites"] if s["kind"] == "camp"]
        done = sum(1 for s in camps if s.get("_cleared"))
        near = [s for s in camps if not s.get("_cleared")]
        out.append(("Clear the military camps  %d / %d" % (done, len(camps)), near, done == len(camps)))
        radio = [s for s in lay["sites"] if s["kind"] == "radio"]
        rdone = all(j in self.blown for j, (it, _n) in enumerate(self.mr.items) if it_obj(it) == "radio") \
            and any(it_obj(it) == "radio" for it, _n in self.mr.items)
        out.append(("Blow up the fuel at the radio station", [] if rdone else radio, rdone))
        for kind, label in (("crash", "Find the plane crash"), ("ship", "Search the shipwreck")):
            ss = [s for s in lay["sites"] if s["kind"] == kind]
            if not ss:
                continue
            s = ss[0]
            if kind not in self.found and math.hypot(s["x"] + self.wp.x - ppos.x, s["z"] + self.wp.z - ppos.z) < 18:
                self.found.add(kind)
                _toast(self.e, "%s found" % ("Plane crash" if kind == "crash" else "Shipwreck"), (0.5, 1.0, 0.5, 1.0), 4.0)
            out.append((label, [] if kind in self.found else ss, kind in self.found))
        return out

    # ---------------------------------------------------------------- per frame
    def tick(self, dt):
        e = self.e
        if not self.ready:
            self.mr = self.mr or _map_of(self.bp.node)
            if self.mr is None or not self._setup():
                self.waited += dt
                if self.waited > 3.0 and not getattr(self, "_told", False):
                    self._told = True
                    _warn("FarScream: no map yet (Packages > FarScream: generate new map)")
                return
        self.t += dt
        pc = _player(e)
        if pc is None:
            return
        if not self.armed:
            self._arm()
        ppos = pc.node.world_position()
        self._stream(ppos, dt)
        self._alert(ppos, dt)
        for v in self.vehicles:
            v.tick(dt, v is self.drive)
        if self.drive is not None:
            ppos = self.drive.seat()
        else:
            self._take_loot(ppos)
        act = self._interact(ppos) if self.drive is None else None
        if e.input.was_key_pressed(INTERACT_KEY):
            if self.drive is not None:
                self.drive.leave()
                self.drive = None
            elif act is not None:
                if act[0] == "crate":
                    self._open_crate(act[1], act[2])
                elif act[0] == "vehicle" and not getattr(pc, "_dead", False):
                    self.drive = act[1]
                    self.drive.enter(pc)
        if self.drive is not None and getattr(pc, "_dead", False):
            self.drive.leave()
            self.drive = None
        self._hud(ppos, act)

    def _hud(self, ppos, act):
        e = self.e
        hud = e.hud
        if hud is None:
            return
        glm = _glm()
        H = float(max(240, int(getattr(e, "height", 720) or 720)))
        px = lambda n: max(11, int(round(n * H / 1080.0)))          # sizes are designed for 1080p
        line = lambda n: px(n) * 1.45 / H
        # objectives (top left), on a dark panel
        objs = self.objectives(ppos)
        cam_yaw = math.radians(e.camera.yaw)
        marks, rows = [], []
        for label, sites, done in objs:
            extra = ""
            if sites and not done:
                s = min(sites, key=lambda s: math.hypot(s["x"] + self.wp.x - ppos.x, s["z"] + self.wp.z - ppos.z))
                dx, dz = s["x"] + self.wp.x - ppos.x, s["z"] + self.wp.z - ppos.z
                d = math.hypot(dx, dz)
                ang = math.degrees(math.atan2(-dx, -dz)) - math.degrees(cam_yaw)
                ang = (ang + 180) % 360 - 180
                arrow = "^" if abs(ang) < 25 else ("<" if ang > 0 else ">")
                extra = "   %s %d m" % (arrow, d)
                marks.append((s, d))
            rows.append((("[x] " if done else "[ ] ") + label + extra, done))
        x0, y0 = 0.018, 0.035
        hgt = line(26) + line(17) * len(rows) + 0.012
        hud.rect(x0 - 0.008, y0 - 0.012, 0.33, hgt, (0.0, 0.0, 0.0, 0.38))
        hud.rect(x0 - 0.008, y0 - 0.012, 0.004, hgt, (1.0, 0.45, 0.12, 0.9))
        hud.text(x0, y0, "FARSCREAM", size=px(26), color=(1.0, 0.55, 0.2, 1.0))
        y = y0 + line(26)
        for text, done in rows:
            hud.text(x0, y, text, size=px(17), color=(0.55, 0.95, 0.55, 0.9) if done else (1, 1, 1, 0.95))
            y += line(17)
        if objs and all(o[2] for o in objs[:2]) and not self.won:
            self.won = True
            _toast(e, "ISLAND LIBERATED", (0.4, 1.0, 0.5, 1.0), 8.0)
        # a marker over the nearest unfinished objective
        for s, d in sorted(marks, key=lambda t: t[1])[:1]:
            p = glm.vec3(s["x"] + self.wp.x, self.mr.height(s["x"] + self.wp.x, s["z"] + self.wp.z) + 6.0,
                         s["z"] + self.wp.z)
            sc = _project(e, p)
            if sc is not None and d > 45:
                hud.text(sc[0] - 0.008, sc[1], "V", size=px(24), color=(1.0, 0.7, 0.2, 0.95))
                hud.text(sc[0] - 0.035, sc[1] + line(24), "%s  %d m" % (s["name"], d), size=px(15),
                         color=(1.0, 0.9, 0.6, 0.9))
        # prompts
        if self.drive is not None:
            v = self.drive
            hud.rect(0.78, 0.83, 0.2, 0.12, (0.0, 0.0, 0.0, 0.35))
            hud.text(0.795, 0.835, "%3.0f" % abs(v.speed * 3.6), size=px(56), color=(1, 1, 1, 1))
            hud.text(0.905, 0.875, "km/h", size=px(20), color=(0.8, 0.85, 0.95, 1))
            hud.center_text(0.955, "W / S  drive     A / D  steer     Space  handbrake     mouse  look     F  get out",
                            size=px(16), color=(1, 1, 1, 0.75))
        elif act is not None:
            what = "open the crate" if act[0] == "crate" else "drive the %s" % act[1].label
            hud.center_text(0.62, "[F]  %s" % what, size=px(24), color=(1, 1, 1, 0.95))
        tt = STATE.get("toast")
        if tt and float(getattr(e, "time", 0.0)) < tt[2]:
            W = float(max(320, int(getattr(e, "width", 1280) or 1280)))
            tw = min(0.96, len(tt[0]) * px(34) * 0.56 / W + 0.04)
            hud.rect(0.5 - tw * 0.5, 0.2 - px(34) * 0.35 / H, tw, px(34) * 1.6 / H, (0.0, 0.0, 0.0, 0.35))
            hud.center_text(0.2, tt[0], size=px(34), color=tt[1])
        sh = STATE.get("shake", 0.0)
        if sh > 0.01:
            STATE["shake"] = sh * 0.9

    def stop(self):
        e = self.e
        if not self.ready:
            return
        if self.drive is not None:
            try:
                self.drive.leave()
            except Exception:
                pass
            self.drive = None
        for k in list(self.chars):
            self._despawn(k)
        if self.prep is not None:
            self.prep.close()
            self.prep = None
        for lt in self.loot:
            try:
                lt["node"].remove()
            except Exception:
                pass
        self.loot = []
        for node, pos, rot, vis in self.saved_tf:
            node.position, node.rotation_euler, node.visible = pos, rot, vis
            for ch in node.children:
                ch.visible = True
                if getattr(ch, "_fs_lid", False):
                    ch.position, ch.rotation_euler = _glm().vec3(0.0), _glm().vec3(0.0)
        for v in self.vehicles:
            v.reset()
        for bp in getattr(self, "prop_bps", []):
            try:
                bp.node.blueprints.remove(bp)
            except ValueError:
                pass
            if bp in e._blueprints:
                e._blueprints.remove(bp)
        self.prop_bps = []
        STATE.pop("toast", None)


def it_obj(it):
    return it.get("objective")


# =========================================================================== soldiers shoot like soldiers
def _soldier_deal(en, weapon):
    """MHEnemy._deal for a soldier: a real gun sound, a muzzle flash, and a
    chance to miss that grows with distance and with the target moving fast
    (bullets that miss leave holes and dust near the player)."""
    wep = _mod(WEP)
    chars = _mod(CHR)

    def deal(pb, pn):
        e = en.engine
        glm = _glm()
        rig = getattr(en, "rig", None)
        muzzle = rig.muzzle_world() if rig is not None else en._eye()
        chest = pn.world_position() + glm.vec3(0, -0.25, 0)
        v = chest - muzzle
        L_ = glm.length(v)
        if L_ > float(en.p("hitscan_range")) or L_ < 1e-4:
            return
        try:
            files = wep._sounds("MP5" if weapon == "MP5" else "M1911", "fire") if wep else []
            if files:
                e.audio.play_at(wep._cue(e, random.choice(files)), muzzle, 0.55)
            else:
                e.audio.play_at("shoot", muzzle, 0.5)
        except Exception:
            e.audio.play_at("shoot", muzzle, 0.5)
        try:
            e.spawn_flash(muzzle, color=(1.0, 0.75, 0.3), intensity=30.0, range=4.0, ttl=0.05)
        except Exception:
            pass
        speed = 0.0
        g = STATE.get("game_speed")
        if g:
            speed = g
        p_hit = max(0.12, min(0.82, 0.86 - L_ / 48.0 - speed * 0.04))
        if weapon == "MP5":
            p_hit *= 0.75
        if random.random() < p_hit:
            hit = e.physics.raycast(muzzle, v / L_, L_, ignore=en._ignore)
            if hit is not None and chars is not None and chars._is_under(hit.node, pn):
                e.message_ancestors(hit.node, "damage", {"amount": float(en.p("attack_damage")), "point": hit.point,
                                                         "source": "enemy"})
            elif hit is not None and getattr(hit.node, "_fs_model", None) is not None and hit.node.rigidbody is not None:
                # the truck the player is driving: the bodywork takes most of it
                e.message_ancestors(hit.node, "damage", {"amount": float(en.p("attack_damage")), "point": hit.point,
                                                         "source": "enemy"})
                try:
                    e.spawn_impact_fx(hit)
                except Exception:
                    pass
            return
        # a miss: the round lands near the player
        side = glm.normalize(glm.cross(v, glm.vec3(0, 1, 0)))
        aim = chest + side * random.uniform(-1.4, 1.4) + glm.vec3(0, random.uniform(-1.0, 0.8), 0)
        d = glm.normalize(aim - muzzle)
        hit = e.physics.raycast(muzzle, d, L_ + 25.0, ignore=en._ignore)
        if hit is not None and not getattr(hit.node, "_fs_char", False):
            try:
                e.spawn_impact_fx(hit)
            except Exception:
                pass
    return deal


# =========================================================================== trucks
class Vehicle:
    """Far Cry style driving: weight, grip that lets go in a hard turn or on
    the handbrake, suspension on four probes, a camera you can swing round
    with the mouse that settles back behind you."""

    SPECS = {"M3S": {"label": "army truck", "vmax": 24.0, "accel": 6.5, "brake": 14.0, "steer": 30.0,
                     "grip": 5.0, "mass": 2.2, "cam": 9.0, "cam_h": 3.4},
             "pickup_healthy": {"label": "pickup", "vmax": 33.0, "accel": 10.0, "brake": 18.0, "steer": 34.0,
                                "grip": 6.0, "mass": 1.0, "cam": 7.0, "cam_h": 2.6}}

    def __init__(self, engine, node, game):
        self.e, self.node, self.game = engine, node, game
        md = node._fs_model
        self.md = md
        self.spec = dict(self.SPECS.get(md.name, self.SPECS["pickup_healthy"]))
        self.label = self.spec["label"]
        self.start = (_glm().vec3(node.position), _glm().vec3(node.rotation_euler))
        self.reset()

    def reset(self):
        glm = _glm()
        n = self.node
        n.position, n.rotation_euler = glm.vec3(self.start[0]), glm.vec3(self.start[1])
        self.yaw = float(n.rotation_euler.y)
        self.roll = -float(n.rotation_euler.x)          # (front = +X: euler = (-roll, yaw, pitch))
        self.pitch = float(n.rotation_euler.z)
        self.vel = glm.vec3(0.0)
        self.vy = 0.0
        self.speed = 0.0
        self.steer = 0.0
        self.spin = 0.0
        self.cam = None
        self.orbit = [0.0, -10.0]
        self.idle_look = 0.0
        self.driver = None
        self.kickv = glm.vec3(0.0)
        self.ignore = _ids(n)
        self.half = (md_size(self.md) * 0.5)

    # ---- geometry in the vehicle's frame (model front = +X)
    def fwd(self):
        r = math.radians(self.yaw)
        return _glm().vec3(math.cos(r), 0.0, -math.sin(r))

    def right(self):
        f = self.fwd()
        return _glm().vec3(-f.z, 0.0, f.x)

    def seat(self):
        glm = _glm()
        return self.node.position + glm.vec3(0, 1.0, 0) + self.fwd() * (self.half.x * 0.35)

    def door_distance(self, p):
        glm = _glm()
        c = self.node.position
        f, r = self.fwd(), self.right()
        loc = p - c
        lx, lz = glm.dot(loc, f), glm.dot(loc, r)
        dx = max(0.0, abs(lx) - self.half.x)
        dz = max(0.0, abs(lz) - self.half.z)
        return math.hypot(dx, dz)

    def kick(self, v):
        self.kickv += v

    # ---- getting in and out
    def enter(self, pc):
        self.driver = pc
        pc._fs_driving = True
        h = next((b for b in pc.node.blueprints if type(b).__name__ == "HQWeapons"), None)
        if h is not None and getattr(h, "vm", None) is not None:
            try:
                h.vm.set_visible(False)
            except Exception:
                pass
        self.ignore = _ids(self.node) | _ids(pc.node)
        self.cam = None
        self.orbit = [0.0, -10.0]
        self.e.audio.play_at("land", self.node.position, 0.6)

    def leave(self):
        pc = self.driver
        self.driver = None
        if pc is None:
            return
        glm = _glm()
        # out of the driver's door (left side), or the other side if that is blocked
        for side in (1.0, -1.0):
            p = self.node.position - self.right() * (side * (self.half.z + 1.0)) + self.fwd() * (self.half.x * 0.25)
            hit = self.e.raycast(p + glm.vec3(0, 3.0, 0), glm.vec3(0, -1, 0), 8.0, ignore=self.ignore)
            if hit is not None and hit.normal.y > 0.5:
                break
        y = (hit.point.y if hit is not None else self.node.position.y) + 0.95
        pc.node.position = glm.vec3(p.x, y, p.z)
        try:
            pc.yaw = float(self.e.camera.yaw)
            pc.pitch = 0.0
            cc = getattr(pc, "cc", None) or getattr(pc, "_cc", None)
            if cc is not None and hasattr(cc, "velocity"):
                cc.velocity = glm.vec3(0.0)
        except Exception:
            pass
        pc._fs_driving = False
        STATE.pop("game_speed", None)
        h = next((b for b in pc.node.blueprints if type(b).__name__ == "HQWeapons"), None)
        if h is not None and getattr(h, "vm", None) is not None:
            try:
                h.vm.set_visible(True)
            except Exception:
                pass
        self.e.audio.play_at("land", self.node.position, 0.5)

    # ---- physics
    def _ground(self, p):
        glm = _glm()
        hit = self.e.raycast(p + glm.vec3(0, 3.0, 0), glm.vec3(0, -1, 0), 7.0, ignore=self.ignore)
        if hit is None:
            return None
        return float(hit.point.y)

    def tick(self, dt, driving):
        e = self.e
        glm = _glm()
        inp = e.input
        s = self.spec
        moving = abs(self.speed) > 0.05 or glm.length(self.vel) > 0.05 or self.vy != 0.0 or glm.length(self.kickv) > 0.01
        if not driving and not moving:
            return
        dt = min(dt, 0.05)
        thr = brk = 0.0
        steer_in = 0.0
        hand = False
        if driving:
            thr = 1.0 if (inp.key("w") or inp.key("up")) else 0.0
            brk = 1.0 if (inp.key("s") or inp.key("down")) else 0.0
            steer_in = (1.0 if (inp.key("a") or inp.key("left")) else 0.0) - (1.0 if (inp.key("d") or inp.key("right")) else 0.0)
            hand = inp.key("space")
        f = self.fwd()
        r = glm.vec3(-f.z, 0.0, f.x)
        fs = glm.dot(self.vel, f)
        ls = glm.dot(self.vel, r)
        vmax = s["vmax"]
        # engine and brakes along the truck
        if thr > 0:
            if fs < -0.5:
                fs += s["brake"] * dt
            else:
                fs += s["accel"] * thr * max(0.0, 1.0 - fs / vmax) * dt
        elif brk > 0:
            if fs > 0.5:
                fs -= s["brake"] * dt
            else:
                fs -= s["accel"] * 0.55 * max(0.0, 1.0 + fs / (vmax * 0.3)) * dt
        else:
            drag = (0.9 + 0.01 * fs * fs) * dt
            fs -= math.copysign(min(abs(fs), drag), fs)
        if hand:
            fs -= math.copysign(min(abs(fs), 9.0 * dt), fs)
        # steering: tighter when slow, the wheels turn smoothly
        lim = s["steer"] * (1.0 - 0.65 * min(abs(fs) / vmax, 1.0))
        self.steer += (steer_in * lim - self.steer) * min(1.0, 6.0 * dt)
        wb = max(self.half.x * 1.25, 2.0)
        yaw_rate = math.degrees(fs / wb * math.tan(math.radians(self.steer)))
        self.yaw += yaw_rate * dt
        # grip: the sideways slide dies away, slower on the handbrake (drift)
        grip = s["grip"] * (0.25 if hand else 1.0) / s["mass"] ** 0.3
        ls *= math.exp(-grip * dt)
        f = self.fwd()
        r = glm.vec3(-f.z, 0.0, f.x)
        self.vel = f * fs + r * ls + self.kickv
        self.kickv *= max(0.0, 1.0 - 4.0 * dt)
        self.speed = fs
        pos = glm.vec3(self.node.position)
        move = self.vel * dt
        # obstacles: three probes at bumper height in the direction of travel
        sp = glm.length(self.vel)
        if sp > 0.2:
            d = self.vel / sp
            side = glm.vec3(-d.z, 0, d.x)
            reach = self.half.x + sp * dt + 0.25
            hit_n = None
            for off in (-self.half.z * 0.8, 0.0, self.half.z * 0.8):
                o = pos + side * off + glm.vec3(0, 0.75, 0)
                hit = e.raycast(o, d, reach, ignore=self.ignore)
                if hit is not None and abs(hit.normal.y) < 0.6 and not getattr(hit.node, "_fs_char", False):
                    hit_n = hit.normal
                    break
            if hit_n is not None:
                n = glm.normalize(glm.vec3(hit_n.x, 0, hit_n.z))
                into = glm.dot(self.vel, n)
                if into < 0:
                    impact = -into
                    self.vel = self.vel - n * into * 1.35          # slide along it, a little bounce
                    self.vel *= 0.7
                    move = self.vel * dt
                    self.speed = glm.dot(self.vel, self.fwd())
                    if impact > 2.0:
                        e.audio.play_at("hit", pos, min(1.0, impact / 10.0))
                        STATE["shake"] = max(STATE.get("shake", 0.0), min(0.8, impact / 14.0))
                        if self.driver is not None and impact > 12.0:
                            self.driver.on_message("damage", {"amount": (impact - 12.0) * 2.5, "source": "crash"})
            self._run_over(pos, sp)
        new = pos + move
        # suspension: four wheels
        f = self.fwd()
        r = glm.vec3(-f.z, 0.0, f.x)
        hx, hz = self.half.x * 0.78, self.half.z * 0.8
        probes = [self._ground(new + f * hx + r * hz), self._ground(new + f * hx - r * hz),
                  self._ground(new - f * hx + r * hz), self._ground(new - f * hx - r * hz)]
        ok = [p for p in probes if p is not None]
        base = -self.md.lo.y
        if ok:
            target = sum(ok) / len(ok) + base
            if new.y > target + 0.3:
                self.vy -= 20.0 * dt
                new.y = max(target, new.y + self.vy * dt)
                if new.y <= target:
                    if self.vy < -6.0:
                        e.audio.play_at("land", new, 0.6)
                    self.vy = 0.0
            else:
                new.y += (target - new.y) * min(1.0, 14.0 * dt)
                self.vy = 0.0
            fl, fr, bl, br = [p if p is not None else (sum(ok) / len(ok)) for p in probes]
            tp = math.degrees(math.atan2(((fl + fr) - (bl + br)) * 0.5, 2 * hx))
            tr = math.degrees(math.atan2(((fl + bl) - (fr + br)) * 0.5, 2 * hz))
            # weight transfer: the body leans in turns and dips when braking
            tr += max(-4.0, min(4.0, -yaw_rate * fs * 0.004))
            k = min(1.0, 8.0 * dt)
            self.pitch += (tp - self.pitch) * k
            self.roll += (tr - self.roll) * k
        else:
            self.vy -= 20.0 * dt
            new.y += self.vy * dt
        if new.y < -3.0:                         # drove into the sea
            self.vel *= max(0.0, 1.0 - 3.0 * dt)
            new.y = max(new.y, -3.0)
        self.node.position = new
        # front = +X: the slope along the truck is a rotation about Z
        self.node.rotation_euler = glm.vec3(-self.roll, self.yaw, self.pitch)
        self._wheels(dt)
        if driving:
            self._drive_camera(dt)
            if self.driver is not None:
                self.driver.node.position = self.seat()
                STATE["game_speed"] = abs(self.speed)

    def _run_over(self, pos, sp):
        if sp < 4.0:
            return
        glm = _glm()
        g = self.game
        for k, node in list(g.chars.items()):
            en = g._enemy(node)
            if en is None or not getattr(en, "alive", False):
                continue
            q = node.world_position()
            if abs(q.y - pos.y) > 2.5:
                continue
            loc = q - pos
            f = self.fwd()
            r = glm.vec3(-f.z, 0.0, f.x)
            lx, lz = glm.dot(loc, f), glm.dot(loc, r)
            if abs(lx) < self.half.x + 0.5 and abs(lz) < self.half.z + 0.4:
                self.e.message_node(node, "damage", {"amount": sp * 9.0, "point": q, "source": "vehicle"})
                self.e.audio.play_at("hit", q, 0.9)
                self.vel *= 0.92

    def _wheels(self, dt):
        self.spin = (self.spin + math.degrees(self.speed * dt / 0.45)) % 360.0
        glm = _glm()
        for w in self.node._fs_wheels:
            front = w.position.x > 0.0
            # model front = +X, wheels roll about the Z axis
            w.rotation_euler = glm.vec3(0.0, self.steer if front else 0.0, -self.spin)

    def _drive_camera(self, dt):
        e = self.e
        glm = _glm()
        cam = e.camera
        s = self.spec
        dx, dy = e.input.mouse_delta
        if abs(dx) < 300 and abs(dy) < 300 and (dx or dy):
            self.orbit[0] -= dx * 0.18
            self.orbit[1] = max(-50.0, min(15.0, self.orbit[1] - dy * 0.15))
            self.idle_look = 1.6
        else:
            self.idle_look -= dt
            if self.idle_look <= 0.0 and abs(self.speed) > 2.0:         # settle back behind the truck
                self.orbit[0] *= max(0.0, 1.0 - 2.5 * dt)
                self.orbit[1] += (-10.0 - self.orbit[1]) * min(1.0, 2.0 * dt)
        f = self.fwd()
        base = math.degrees(math.atan2(-f.x, -f.z))         # camera yaw that looks along the truck
        yaw = math.radians(base + self.orbit[0])
        pitch = math.radians(self.orbit[1])
        dist = s["cam"] + min(abs(self.speed), 30.0) * 0.06
        look_dir = glm.vec3(-math.sin(yaw) * math.cos(pitch), math.sin(pitch), -math.cos(yaw) * math.cos(pitch))
        target = self.node.position + glm.vec3(0, s["cam_h"] * 0.55, 0)
        want = target - look_dir * dist + glm.vec3(0, s["cam_h"] * 0.45, 0)
        # never under the ground or inside a wall
        hit = e.raycast(target, glm.normalize(want - target), glm.length(want - target), ignore=self.ignore)
        if hit is not None:
            want = hit.point + hit.normal * 0.3
        gy = self.game.mr.height(want.x, want.z)
        want.y = max(want.y, gy + 1.0)
        if self.cam is None:
            self.cam = glm.vec3(want)
        self.cam += (want - self.cam) * min(1.0, 9.0 * dt)
        sh = STATE.get("shake", 0.0)
        jitter = glm.vec3(random.uniform(-1, 1), random.uniform(-1, 1), 0) * (sh * 0.15)
        cam.position = glm.vec3(self.cam) + jitter
        d = glm.normalize(target - self.cam)
        cam.yaw = math.degrees(math.atan2(-d.x, -d.z))
        cam.pitch = math.degrees(math.asin(max(-1.0, min(1.0, d.y))))


def md_size(md):
    return md.hi - md.lo


# =========================================================================== engine hooks
def _hook_fps(prev):
    def on_tick(self, dt):
        if getattr(self, "_fs_driving", False):
            return                                   # the truck has the camera and the controls
        return prev(self, dt)
    return on_tick


def _hook_weapons(prev):
    def on_tick(self, dt):
        pc = next((b for b in self.node.blueprints if type(b).__name__ == "FPSController"), None)
        if pc is not None and getattr(pc, "_fs_driving", False):
            return
        return prev(self, dt)
    return on_tick


def _hook_fire(prev):
    """The player's gunshots are heard (soldiers within earshot come running)."""
    def fire(self, *a, **kw):
        r = prev(self, *a, **kw)
        try:
            STATE["player_shot"] = _glm().vec3(self.node.world_position())
        except Exception:
            pass
        return r
    return fire


def _hook_impact(prev):
    """No bullet holes floating where a truck was: holes on a truck go
    straight away (the truck moves)."""
    def spawn_impact_fx(self, hit, *a, **kw):
        r = prev(self, hit, *a, **kw)
        try:
            n = getattr(hit, "node", None)
            while n is not None and getattr(n, "rigidbody", None) is None:
                n = n.parent
            if n is not None and getattr(n, "_fs_model", None) is not None:
                m = _m()
                decals = getattr(m, "_FP2_DECALS", None)
                if decals:
                    m._fp2_kill_fx(self, decals[-1])
        except Exception:
            pass
        return r
    return spawn_impact_fx


def _on_update(engine, dt):
    STATE["engine"] = engine
    try:
        _ensure_built(engine)
    except Exception as ex:
        import traceback
        traceback.print_exc()
        _warn("FarScream: %s" % ex)
    if STATE.get("stream") is not None:
        try:
            STATE["stream"].update(dt)
        except Exception as ex:
            _warn("FarScream textures: %s" % ex)
    STATE["lod_t"] += dt
    if STATE["lod_t"] > 0.15:
        STATE["lod_t"] = 0.0
        _update_lods(engine)


def _guns_mp5():
    """The HQ FPS MP5, as a gun the MakeHuman soldiers can hold."""
    chars = _mod(CHR)
    if chars is None or "MP5" in getattr(chars, "_GUNS", {}):
        return
    try:
        guns = chars._guns()
        path = os.path.join(os.path.dirname(sys.modules[WEP].__file__), "assets", "models_gun", "MP5.glb")
        j, binb = _read_glb(path)
        P = None
        for mesh in j["meshes"]:
            for pr in mesh["primitives"]:
                pos = _acc(j, binb, pr["attributes"]["POSITION"]).astype(np.float64)
                nrm = _acc(j, binb, pr["attributes"]["NORMAL"]).astype(np.float64)
                idx = _acc(j, binb, pr["indices"]).ravel().astype(np.int64)
                P, N = pos[idx], nrm[idx]                  # unindexed, like the other guns
                break
            break
        if P is None:
            return
        # Unity: +Z forward, +X left.  Gun frame: barrel along -Z, grip at the origin.
        P = np.stack([-P[:, 0], P[:, 1], -P[:, 2]], 1)
        N = np.stack([-N[:, 0], N[:, 1], -N[:, 2]], 1)
        lo, hi = P.min(0), P.max(0)
        L_ = hi[2] - lo[2]
        grip = np.array([(lo[0] + hi[0]) * 0.5, lo[1] + (hi[1] - lo[1]) * 0.62, hi[2] - L_ * 0.40])
        P = P - grip
        muzzle = np.array([0.0, (hi[1] - grip[1]) - (hi[1] - lo[1]) * 0.22, lo[2] - grip[2], 1.0])
        pm = {"mat": "Metal", "color": [0.085, 0.085, 0.09], "metallic": 0.7, "roughness": 0.45}
        chars._GUNS["MP5"] = {"parts": [(pm, P, N)], "muzzle": muzzle}
        _ = guns
    except Exception as ex:
        _warn("FarScream: soldiers carry pistols only (MP5 model: %s)" % ex)


def _read_glb(path):
    import struct
    b = open(path, "rb").read()
    jl = struct.unpack("<I", b[12:16])[0]
    j = json.loads(b[20:20 + jl])
    off = 20 + jl
    bl = struct.unpack("<I", b[off:off + 4])[0]
    return j, b[off + 8:off + 8 + bl]


def _acc(j, binb, i):
    CT = {5121: np.uint8, 5123: np.uint16, 5125: np.uint32, 5126: np.float32}
    NC = {"SCALAR": 1, "VEC2": 2, "VEC3": 3, "VEC4": 4}
    a = j["accessors"][i]
    bv = j["bufferViews"][a["bufferView"]]
    dt = np.dtype(CT[a["componentType"]])
    c = NC[a["type"]]
    n = a["count"]
    stride = bv.get("byteStride") or dt.itemsize * c
    start = bv.get("byteOffset", 0) + a.get("byteOffset", 0)
    raw = np.frombuffer(binb, np.uint8, count=stride * (n - 1) + dt.itemsize * c, offset=start)
    view = np.lib.stride_tricks.as_strided(raw, shape=(n, dt.itemsize * c), strides=(stride, 1))
    return np.ascontiguousarray(view).view(dt).reshape(n, c)


def _on_init(engine):
    STATE["engine"] = engine
    _guns_mp5()


def _on_play_stop(engine):
    STATE.pop("game_speed", None)


def register(h3d):
    global H3D, L
    H3D = h3d
    L = h3d.load("fs_layout.py")
    m = h3d.module
    _blueprints()
    isl = _mod(ISL)
    if isl is not None:
        try:
            h3d.wrap(isl.IslandRuntime, "_build_terrain", lambda prev, self: _island_hook(prev)(self))
        except Exception as ex:
            h3d.warn("FarScream: island hook not installed (%s)" % ex)
    else:
        h3d.warn("FarScream needs the Tropical Islands package")
    try:
        m._mesh_shape_view = _mesh_shape_view_fs(m._mesh_shape_view)
    except Exception as ex:
        h3d.warn("FarScream: mesh colliders for props not installed (%s)" % ex)
    try:
        h3d.wrap(m.FPSController, "on_tick", lambda prev, self, dt: _hook_fps(prev)(self, dt))
    except Exception as ex:
        h3d.warn("FarScream: driving can't take the controls (%s)" % ex)
    wep = _mod(WEP)
    if wep is not None and "HQWeapons" in getattr(wep, "CLS", {}):
        try:
            h3d.wrap(wep.CLS["HQWeapons"], "on_tick", lambda prev, self, dt: _hook_weapons(prev)(self, dt))
        except Exception as ex:
            h3d.warn("FarScream: weapons stay out while driving (%s)" % ex)
        try:
            h3d.wrap(wep.CLS["HQWeapons"], "_fire", lambda prev, self, *a, **kw: _hook_fire(prev)(self, *a, **kw))
        except Exception as ex:
            h3d.warn("FarScream: gunshots are not heard (%s)" % ex)
    try:
        h3d.wrap(m.HeavenEngine, "spawn_impact_fx", lambda prev, self, hit, *a, **kw:
                 _hook_impact(prev)(self, hit, *a, **kw))
    except Exception:
        pass
    h3d.on("update", _on_update)
    h3d.on("init", _on_init)
    h3d.on("play_stop", _on_play_stop)
    h3d.add_tool("FarScream: generate new map", tool_generate, "#D9541E")
    h3d.add_tool("FarScream: rebuild this map", tool_same_seed)


__all__ = ["generate", "Library", "Vehicle", "Game"]

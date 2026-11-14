# @heaven3d-part world/bk_city.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# BUILDING MAKER - towns: districts, levels of detail, streaming
#
# A painted building is a few meshes per storey.  One house is cheap; a town
# of a thousand is 20,000 meshes, far too many to draw one by one.  So the
# buildings that just stand there are drawn in DISTRICTS (48 m squares):
#
#   * every district is ONE vertex buffer; a material of it is one draw call,
#     and the whole district is one call in the shadow pass
#   * three levels of detail, chosen by the distance to the district:
#       near   everything: rooms, stairs, glazing bars, glass
#       mid    the outside with its frames, shutters, sills; the windows and
#              doorways are closed by dark panes instead of glass and rooms
#       far    the bare shell (walls, roofs) with dark panes
#     a level is a prefix of the same vertex data, so a far district only
#     has its shell in video memory; more is loaded as the camera comes
#     closer (a few milliseconds a frame) and dropped again when it has left
#   * a district outside the view costs one box test
#   * a building that is selected, being painted, moving or hidden by storey
#     is drawn part by part as before, and joins its district again when it
#     is left alone
#
# engine.set_building_detail(near=45, mid=140, far=0) changes the distances,
# engine.building_stats() tells what is drawn.  HEAVEN3D_NO_CITY=1 turns the
# districts off (every building part by part).
# #############################################################################

BK_CITY = {
    "on": os.environ.get("HEAVEN3D_NO_CITY", "") not in ("1", "true", "yes"),
    "chunk": 48.0,          # size of a district (m)
    "near": 45.0,           # full detail when the district is closer than this
    "mid": 140.0,           # frames, shutters, sills until here
    "far": 0.0,             # districts beyond are not drawn at all (0: no limit)
    "budget_ms": 4.0,       # time a frame may spend loading more detail
    "unload_frames": 180,   # frames a district keeps detail it no longer shows
    "settle_frames": 12,    # frames a building must stand still before it joins a district
}
# how far the rooms / the frames and shutters are drawn (the Buildings panel's "Detail")
BK_DETAIL_PRESETS = {"low": ("Low: rooms to 25 m", 25.0, 80.0), "medium": ("Medium: rooms to 45 m", 45.0, 140.0),
                     "high": ("High: rooms to 80 m", 80.0, 220.0), "ultra": ("Ultra: rooms to 150 m", 150.0, 400.0)}
_BK_CITY_CLASSES = {1: (_BK_B, _BK_S), 2: (_BK_B, _BK_S, _BK_D), 3: (_BK_B, _BK_S, _BK_D, _BK_I)}
_BKC = {"scenes": {}, "cams": [], "ibo": None, "stats": {}}


class _BKRangeVAO:
    """A slice of a district's vertex array (one material at one level of
    detail), with back-face culling for single-sided materials."""
    __slots__ = ("_vao", "_ctx", "_first", "_count", "_cull")

    def __init__(self, vao, ctx, first, count, cull):
        self._vao, self._ctx, self._first, self._count, self._cull = vao, ctx, first, count, cull

    def render(self, mode=None, vertices=-1, first=0, instances=-1):
        if mode is None:
            mode = moderngl.TRIANGLES
        if self._cull:
            self._ctx.enable(moderngl.CULL_FACE)
            try:
                return self._vao.render(mode, vertices=self._count, first=self._first, instances=instances)
            finally:
                self._ctx.disable(moderngl.CULL_FACE)
        return self._vao.render(mode, vertices=self._count, first=self._first, instances=instances)

    def __getattr__(self, name):
        return getattr(self._vao, name)


class _BKChunkMesh(Mesh):
    """What the renderer gets for a district: a range of the district's
    buffers that behaves like a mesh."""

    def __init__(self, owner, name, first, count, cull, box):
        Mesh.__init__(self, owner.ctx, name)
        self._bk_owner = owner
        self._bk_first, self._bk_count, self._bk_cull = int(first), int(count), bool(cull)
        self.vbo, self.ibo = owner.vbo, owner.ibo
        self.indexed = True
        self.vertex_count = owner.vertex_count
        self.index_count = int(count)
        lo, hi = box
        self.bounding_box = (glm.vec3(float(lo[0]), float(lo[1]), float(lo[2])),
                             glm.vec3(float(hi[0]), float(hi[1]), float(hi[2])))
        c = (np.asarray(lo, np.float64) + np.asarray(hi, np.float64)) * 0.5
        self.bounding_sphere = (glm.vec3(float(c[0]), float(c[1]), float(c[2])),
                                float(np.linalg.norm(np.asarray(hi, np.float64) - c)))

    def get_vao(self, program):
        key = ("bk", id(program))
        v = self._vaos.get(key)
        if v is None:
            raw = _h3d27_get_vao_raw(self._bk_owner, program)
            v = _BKRangeVAO(raw, self.ctx, self._bk_first, self._bk_count,
                            self._bk_cull and _h3d27_is_surface_prog(program))
            self._vaos[key] = v
        return v

    def release(self):                       # the buffers belong to the district
        self._vaos.clear()
        self.vbo = self.ibo = None


class _BKChunkNode:
    """The 'object' of a district draw: all the renderer asks of it."""
    __slots__ = ("name", "material", "visible", "mesh")

    def __init__(self, name, material):
        self.name, self.material, self.visible, self.mesh = name, material, True, None


def _bk_city_ibo(ctx, nquads):
    """One index buffer serves every district: the indices of quad after quad."""
    cur = _BKC["ibo"]
    if cur is None or cur[0] is not ctx or cur[1] < nquads:
        cap = 1 << max(14, int(nquads - 1).bit_length())
        cur = _BKC["ibo"] = (ctx, cap, ctx.buffer(_bk_quad_indices(cap).tobytes()))
    return cur[2]


def _bk_city_xf(w):
    """numpy pieces of a world matrix: rotation / scale (row r, column c),
    translation, the matrix for normals and whether it mirrors."""
    M = np.frombuffer(w.to_bytes(), np.float32).reshape(4, 4).astype(np.float64).T      # M[r, c]
    R = M[:3, :3]
    det = float(np.linalg.det(R))
    try:
        Nm = np.linalg.inv(R).T
    except Exception:
        Nm = R
    return R, M[:3, 3], Nm, det < 0.0


class _BKChunk:
    def __init__(self, key, size):
        self.key = key
        self.members = {}              # id(node) -> record
        self.dirty = True
        self.level = 0                 # what is in video memory: 0 nothing, 1 far, 2 mid, 3 near
        self.owner = None
        self.mats = []                 # [(node, {level: mesh})]
        self.shadow = None             # (node, mesh): the whole district for the shadow pass
        self.box = None                # world box (lo, hi) of everything in it
        self.size = size
        self.origin = glm.vec3((key[0] + 0.5) * size, 0.0, (key[1] + 0.5) * size)
        self.model = glm.translate(glm.mat4(1.0), self.origin)
        self.idle = 0                  # frames with more detail loaded than shown
        self.tris = {1: 0, 2: 0, 3: 0}
        self.bytes = 0

    def free(self):
        o, self.owner = self.owner, None
        if o is not None:
            try:
                for v in o._vaos.values():
                    try:
                        v.release()
                    except Exception:
                        pass
                o._vaos.clear()
                if o.vbo is not None:
                    o.vbo.release()
            except Exception:
                pass
            o.vbo = o.ibo = None
        for _n, lods in self.mats:
            for m in lods.values():
                m.release()
        self.mats, self.shadow, self.level, self.bytes = [], None, 0, 0

    def world_box(self):
        """Box of the members from their collision / part boxes (cheap, no bake)."""
        lo = np.full(3, np.inf)
        hi = np.full(3, -np.inf)
        for rec in self.members.values():
            lo = np.minimum(lo, rec["wlo"])
            hi = np.maximum(hi, rec["whi"])
        return lo, hi

    def build(self, ctx, level):
        """Put the district in video memory at ``level`` (1 far .. 3 near)."""
        self.free()
        self.dirty = False
        self.idle = 0
        recs = list(self.members.values())
        if not recs:
            return
        by = {}
        for bi, rec in enumerate(recs):
            for lp in rec["node"]._bk_parts.values():
                for mat, part in lp.items():
                    by.setdefault(mat, []).append((bi, part))
        # glass and the dark panes come first: the shadow pass skips them
        order = [m for m in ("glass", "blocker") if m in by] + sorted(m for m in by if m not in ("glass", "blocker"))
        classes = _BK_CITY_CLASSES[level]
        blocks, owners, layout = [], [], []
        q = 0
        for mat in order:
            cq = [0, 0, 0, 0]
            for cls in classes:
                for bi, part in by[mat]:
                    c = part.counts
                    a = 4 * sum(c[:cls])
                    b = a + 4 * c[cls]
                    if b > a:
                        blocks.append(part.data[a:b])
                        owners.append((bi, b - a))
                        cq[cls] += c[cls]
            if sum(cq):
                layout.append((mat, q, cq))
                q += sum(cq)
        if not q:
            return
        data = np.concatenate(blocks)
        bidx = np.repeat(np.fromiter((o[0] for o in owners), np.int64, len(owners)),
                         np.fromiter((o[1] for o in owners), np.int64, len(owners)))
        xf = [_bk_city_xf(rec["w"]) for rec in recs]
        org = np.array([self.origin.x, self.origin.y, self.origin.z], np.float64)
        same = all(np.allclose(x[0], xf[0][0], atol=1e-7) for x in xf[1:])
        if same:                              # the usual town: every house turned the same way
            R, _t, Nm, _neg = xf[0]
            T = np.stack([x[1] for x in xf]) - org
            pos = data[:, 0:3].astype(np.float64) @ R.T + T[bidx]
            nrm = data[:, 3:6].astype(np.float64) @ Nm.T
            tan = data[:, 6:9].astype(np.float64) @ R.T
        else:
            Rs = np.stack([x[0] for x in xf])[bidx]
            Ns = np.stack([x[2] for x in xf])[bidx]
            T = (np.stack([x[1] for x in xf]) - org)[bidx]
            pos = np.einsum("nij,nj->ni", Rs, data[:, 0:3].astype(np.float64)) + T
            nrm = np.einsum("nij,nj->ni", Ns, data[:, 3:6].astype(np.float64))
            tan = np.einsum("nij,nj->ni", Rs, data[:, 6:9].astype(np.float64))
        nrm /= np.maximum(np.linalg.norm(nrm, axis=1, keepdims=True), 1e-12)
        tan /= np.maximum(np.linalg.norm(tan, axis=1, keepdims=True), 1e-12)
        out = np.empty((len(data), 12), np.float32)
        out[:, 0:3] = pos
        out[:, 3:6] = nrm
        out[:, 6:9] = tan
        out[:, 9] = data[:, 9]
        out[:, 10:12] = data[:, 10:12]
        neg = np.array([x[3] for x in xf])
        if neg.any():                         # a mirrored building: turn its quads round
            m = neg[bidx].reshape(-1, 4)[:, 0]
            rows = np.arange(len(out)).reshape(-1, 4)
            rows[m] = rows[m][:, [0, 3, 2, 1]]
            out = out[rows.ravel()]
            out[np.repeat(m, 4), 9] *= -1.0
        owner = Mesh(ctx, "district_%d_%d" % self.key)
        owner.vbo = ctx.buffer(out.tobytes())
        owner.ibo = _bk_city_ibo(ctx, q)
        owner.indexed = True
        owner.vertex_count = len(out)
        owner.index_count = q * 6
        self.owner = owner
        self.bytes = out.nbytes
        self.level = level
        self.tris = {1: 0, 2: 0, 3: 0}
        lo_all, hi_all = pos.min(axis=0), pos.max(axis=0)
        self.box = (lo_all + org, hi_all + org)
        skip_q = 0
        for mat, q0, cq in layout:
            material = bk_material(mat)
            p = pos[q0 * 4:(q0 + sum(cq)) * 4]
            box = (p.min(axis=0), p.max(axis=0))
            node = _BKChunkNode("district_%d_%d_%s" % (self.key[0], self.key[1], mat), material)
            lods = {}
            cull = not material.double_sided
            for lv in range(1, level + 1):
                if lv == 3:                   # near: everything but the dark panes
                    first, count = q0 + cq[0], cq[1] + cq[2] + cq[3]
                else:
                    first, count = q0, cq[0] + cq[1] + (cq[2] if lv == 2 else 0)
                if count <= 0:
                    continue
                m = _BKChunkMesh(owner, node.name, first * 6, count * 6, cull, box)
                m._h3d_shadow = False         # the district casts as one
                lods[lv] = m
                self.tris[lv] += count * 2
            if lods:
                self.mats.append((node, lods))
            if mat in ("glass", "blocker"):
                skip_q = q0 + sum(cq)
        if q > skip_q:
            sm = _BKChunkMesh(owner, owner.name + "_shadow", skip_q * 6, (q - skip_q) * 6, False, (lo_all, hi_all))
            self.shadow = (_BKChunkNode(owner.name + "_shadow", None), sm)


class _BKCityScene:
    """The districts of one scene."""

    def __init__(self, scene):
        self.scene = weakref.ref(scene, self._gone)
        self.sid = id(scene)
        self.chunks = {}
        self.where = {}                # id(node) -> record
        self.frame = 0
        self.seen = 0
        self.skip = ()
        self.tool_node = None
        self.cam = None
        self.planes = None
        self.items = []                # this frame's district draws [(node, mesh, model)]
        self.shadow_items = []
        self.ver = None

    def _gone(self, _ref):
        try:
            for c in self.chunks.values():
                c.free()
        except Exception:
            pass
        self.chunks, self.where = {}, {}
        _BKC["scenes"].pop(self.sid, None)

    # ---- the scene walk calls this for every painted building ----
    def building(self, n, w, ch):
        nd = n.__dict__
        rec = self.where.get(id(n))
        if rec is not None and rec["node"] is not n:
            self._drop(rec)
            rec = None
        parts = nd.get("_bk_parts")
        ok = (parts and n.visible and nd.get("_bk_ctx") is not None and nd.get("_bk_view_top") is None
              and n.rigidbody is None and n is not self.tool_node and id(n) not in self.skip)
        if not ok:
            if rec is not None:
                self._drop(rec)
            if not n.visible:                 # a hidden building hides its baked parts
                return [c for c in ch if c.__dict__.get("_bk_level") is None]
            return ch
        rev = nd.get("_bk_rev", 0)
        if rec is None:
            rec = self.where[id(n)] = {"node": n, "w": glm.mat4(w), "rev": rev, "chunk": None, "since": self.frame,
                                       "kids": None, "new": True, "seen": 0}
        elif rec["w"] != w:
            # it moves (a gizmo drag of its group, an animation): out of its
            # district until it has stood still for a moment
            self._leave(rec)
            rec["w"] = glm.mat4(w)
            rec["since"] = self.frame
            rec["new"] = False
        elif rec["rev"] != rev:
            self._leave(rec)
        rec["rev"] = rev
        rec["seen"] = self.frame
        self.seen += 1
        if rec["chunk"] is None:
            if not rec["new"] and self.frame - rec["since"] < BK_CITY["settle_frames"]:
                return ch
            self._join(rec)
        kids = rec["kids"]
        if kids is None or kids[0] != _PS_VER[0]:
            kids = rec["kids"] = (_PS_VER[0], [c for c in ch if c.__dict__.get("_bk_level") is None])
        return kids[1]

    def _join(self, rec):
        n = rec["node"]
        lo, hi = _bk_local_box(n)
        R, t, _n, _neg = _bk_city_xf(rec["w"])
        corners = np.array([[hi[0] if k & 1 else lo[0], hi[1] if k & 2 else lo[1], hi[2] if k & 4 else lo[2]]
                            for k in range(8)], np.float64) @ R.T + t
        rec["wlo"], rec["whi"] = corners.min(axis=0), corners.max(axis=0)
        c = (rec["wlo"] + rec["whi"]) * 0.5
        size = float(BK_CITY["chunk"])
        key = (int(math.floor(c[0] / size)), int(math.floor(c[2] / size)))
        chunk = self.chunks.get(key)
        if chunk is None:
            chunk = self.chunks[key] = _BKChunk(key, size)
        chunk.members[id(n)] = rec
        chunk.dirty = True
        rec["chunk"] = chunk
        rec["new"] = False
        for c in n.children:                  # its own buffers aren't needed while the district draws it
            m = c.mesh
            if m is not None and c.__dict__.get("_bk_level") is not None and hasattr(m, "on_gpu") and m.on_gpu():
                m.release()

    def _leave(self, rec):
        chunk = rec["chunk"]
        if chunk is not None:
            chunk.members.pop(id(rec["node"]), None)
            chunk.dirty = True
            rec["chunk"] = None

    def _drop(self, rec):
        self._leave(rec)
        self.where.pop(id(rec["node"]), None)

    # ---- after the walk: update the districts, hand out this frame's draws ----
    def finish(self, out):
        if self.seen != len(self.where):      # buildings that left the scene
            for rec in [r for r in self.where.values() if r["seen"] != self.frame]:
                self._drop(rec)
        items, shadows = [], []
        cam, planes = self.cam, self.planes
        near, mid, far = float(BK_CITY["near"]), float(BK_CITY["mid"]), float(BK_CITY["far"])
        mid = max(mid, near)
        t_end = time.perf_counter() + float(BK_CITY["budget_ms"]) / 1000.0
        wanted, todo = [], []
        st = {"districts": 0, "drawn": 0, "near": 0, "mid": 0, "far": 0, "triangles": 0, "draws": 0,
              "buildings": len(self.where), "batched": 0, "video_mb": 0.0}
        for key in list(self.chunks):
            chunk = self.chunks[key]
            if not chunk.members:
                chunk.free()
                del self.chunks[key]
                continue
            st["districts"] += 1
            st["batched"] += len(chunk.members)
            ctx = next(iter(chunk.members.values()))["node"].__dict__.get("_bk_ctx")
            if chunk.dirty or chunk.box is None:
                lo, hi = chunk.world_box()
            else:
                lo, hi = chunk.box
            if cam is None:
                d, inside = 0.0, True
            else:
                dx = max(lo[0] - cam[0], 0.0, cam[0] - hi[0])
                dy = max(lo[1] - cam[1], 0.0, cam[1] - hi[1])
                dz = max(lo[2] - cam[2], 0.0, cam[2] - hi[2])
                d = math.sqrt(dx * dx + dy * dy + dz * dz)
                inside = True
                if planes is not None:
                    for nx, ny, nz, dd in planes:
                        if (nx * (hi[0] if nx >= 0.0 else lo[0]) + ny * (hi[1] if ny >= 0.0 else lo[1])
                                + nz * (hi[2] if nz >= 0.0 else lo[2]) + dd) < 0.0:
                            inside = False
                            break
            hide = far > 0.0 and d > far
            want = 3 if d < near else (2 if d < mid else 1)
            # a little hysteresis: detail that is loaded stays until 15 % further away
            keep = 3 if d < near * 1.15 else (2 if d < mid * 1.15 else 1)
            lv = chunk.level
            if chunk.dirty:
                # something in it changed: again at once, at the detail it had or needs
                chunk.build(ctx, max(want, min(lv, keep)) if lv else want)
            elif lv < want and (inside or d < near):
                wanted.append((d, chunk, want, ctx))
            elif lv > keep:
                chunk.idle += 1
                if chunk.idle > BK_CITY["unload_frames"]:
                    wanted.append((1e9 + d, chunk, keep, ctx))
            else:
                chunk.idle = 0
            todo.append((chunk, want, hide, inside))
        # more (or less) detail: the nearest first, within the frame's time budget
        if wanted:
            wanted.sort(key=lambda x: x[0])
            first = True
            for _d, chunk, lv, ctx in wanted:
                if not first and time.perf_counter() > t_end:
                    break
                first = False
                chunk.build(ctx, lv)
        bymat = {}
        for chunk, want, hide, inside in todo:
            if chunk.owner is None:
                continue
            st["video_mb"] += chunk.bytes / 1048576.0
            if chunk.shadow is not None and not hide:
                shadows.append((chunk.shadow[0], chunk.shadow[1], chunk.model))
            if hide or not inside:
                continue
            show = min(want, chunk.level)
            st["drawn"] += 1
            st[("far", "mid", "near")[show - 1]] += 1
            st["triangles"] += chunk.tris.get(show, 0)
            for node, lods in chunk.mats:
                m = lods.get(show)
                if m is not None:
                    g = bymat.get(id(node.material))
                    if g is None:
                        g = bymat[id(node.material)] = []
                    g.append((node, m, chunk.model))
        for g in bymat.values():              # material after material: each is bound once
            items.extend(g)
        st["draws"] = len(items)
        self.items, self.shadow_items = items, shadows
        _BKC["stats"] = st
        out.extend(items)


def _bk_local_box(node):
    """(lo, hi) of a building in its own frame, from its baked parts."""
    c = node.__dict__.get("_bk_box")
    rev = node.__dict__.get("_bk_rev", 0)
    if c is not None and c[0] == rev:
        return c[1], c[2]
    lo = np.full(3, np.inf)
    hi = np.full(3, -np.inf)
    for lp in (node.__dict__.get("_bk_parts") or {}).values():
        for part in lp.values():
            p = part.data[:, 0:3]
            if len(p):
                lo = np.minimum(lo, p.min(axis=0))
                hi = np.maximum(hi, p.max(axis=0))
    if not np.isfinite(lo).all():
        lo, hi = np.zeros(3), np.zeros(3)
    node._bk_box = (rev, lo, hi)
    return lo, hi


def _bk_city_begin(scene, skip):
    """-> the district batcher of this scene for one scene walk (None: off)."""
    if not BK_CITY["on"]:
        if _BKC["scenes"]:
            bk_city_reset()
        return None
    st = _BKC["scenes"].get(id(scene))
    if st is None or st.scene() is not scene:
        st = _BKC["scenes"][id(scene)] = _BKCityScene(scene)
    st.frame += 1
    st.seen = 0
    st.skip = skip
    eng = _SB.get("engine")
    tool = getattr(eng, "building_tool", None) if eng is not None else None
    st.tool_node = tool.node if (tool is not None and tool.active) else None
    cam = _BKC["cams"][-1] if _BKC["cams"] else None
    st.cam, st.planes = None, None
    if cam is not None:
        try:
            p = cam.position
            st.cam = (float(p.x), float(p.y), float(p.z))
            vp = cam.projection() * cam.view()
            row = [[vp[c][r] for c in range(4)] for r in range(4)]
            planes = []
            for ci, sg in ((0, 1.0), (0, -1.0), (1, 1.0), (1, -1.0), (2, 1.0), (2, -1.0)):
                pl = [row[3][i] + sg * row[ci][i] for i in range(4)]
                ln = math.sqrt(pl[0] * pl[0] + pl[1] * pl[1] + pl[2] * pl[2])
                if ln > 1e-12:
                    planes.append((pl[0] / ln, pl[1] / ln, pl[2] / ln, pl[3] / ln))
            st.planes = planes if len(planes) == 6 else None
        except Exception:
            st.planes = None
    return st


def bk_city_reset():
    """Forget every district (they are made again on the next frame)."""
    for st in list(_BKC["scenes"].values()):
        for c in st.chunks.values():
            c.free()
        st.chunks, st.where = {}, {}
    _BKC["scenes"].clear()


def _bk_city_state(scene):
    st = _BKC["scenes"].get(id(scene))
    return st if (st is not None and st.scene() is scene) else None


def _h3d_shadow_extra(scene):
    """Merged shadow casters of this frame: one per district."""
    st = _bk_city_state(scene)
    return st.shadow_items if st is not None else []


def _bk_city_batched(scene, node):
    """True when ``node`` is a building part that its district draws."""
    st = _bk_city_state(scene)
    if st is None or node.__dict__.get("_bk_level") is None or node.parent is None:
        return False
    rec = st.where.get(id(node.parent))
    return rec is not None and rec["chunk"] is not None


def _bk_city_draws(scene):
    """[(mesh, model)] of the districts in view (wireframe view, tools)."""
    st = _bk_city_state(scene)
    return [(m, model) for _n, m, model in st.items] if st is not None else []


def _bk_set_building_detail(self, near=None, mid=None, far=None, district=None, preset=None):
    """Distances (m) of the levels of detail of painted buildings:
    ``near`` rooms and glass, ``mid`` frames and shutters, beyond that bare
    shells; ``far`` > 0 stops drawing buildings further away than that.
    ``district``: the size of the squares the buildings are merged in.
    ``preset``: "low", "medium", "high" or "ultra" (BK_DETAIL_PRESETS)."""
    if preset in BK_DETAIL_PRESETS:
        BK_CITY["near"], BK_CITY["mid"] = BK_DETAIL_PRESETS[preset][1], BK_DETAIL_PRESETS[preset][2]
    if near is not None:
        BK_CITY["near"] = max(0.0, float(near))
    if mid is not None:
        BK_CITY["mid"] = max(0.0, float(mid))
    if far is not None:
        BK_CITY["far"] = max(0.0, float(far))
    if district is not None and abs(float(district) - BK_CITY["chunk"]) > 1e-6:
        BK_CITY["chunk"] = float(np.clip(float(district), 16.0, 256.0))
        bk_city_reset()
    return {k: BK_CITY[k] for k in ("near", "mid", "far", "chunk")}


def _bk_building_stats(self):
    """What the last frame drew of the painted buildings: districts (all /
    in view / per level of detail), triangles, draw calls, video memory."""
    return dict(_BKC["stats"])


if not globals().get("_BK_CITY_INSTALLED"):
    _BK_CITY_INSTALLED = True
    HeavenEngine.set_building_detail = _bk_set_building_detail
    HeavenEngine.building_stats = _bk_building_stats
    if "_PS_CITY" in globals() and globals().get("_PS_ON"):
        _PS_CITY[0] = _bk_city_begin

        _BK_CITY_PREV_RENDER = Renderer.render

        def _bk_city_render(self, scene, camera):
            _BKC["cams"].append(camera)
            try:
                return _BK_CITY_PREV_RENDER(self, scene, camera)
            finally:
                _BKC["cams"].pop()

        Renderer.render = _bk_city_render

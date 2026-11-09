# @heaven3d-part patches/perf_scale.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------------------
# [PERF:scale]  Big scenes (thousands of building modules) stay smooth
#
#   * SceneNode.traverse() is cached until the scene tree really changes.
#     Every node's children list records its own changes (append, remove,
#     insert, slice assignment, a new list assigned ...), so a cached walk is
#     never stale; a dozen subsystems (lights, sounds, gizmos, particles,
#     terrain ...) each walk the whole tree every frame and now share it.
#   * the per-frame mesh list gets numpy arrays (world matrices + bounding
#     boxes) once per render; the sun shadow-frustum fit, the shadow-caster
#     cull and the camera frustum cull run on all objects at once with the
#     exact same maths as before (same pixels).
#   Disable with HEAVEN3D_NO_PERF=1 to compare.
# ---------------------------------------------------------------------------
try:
    _PS_ON = os.environ.get("HEAVEN3D_NO_PERF", "") not in ("1", "true", "yes")
    _PS_VER = [0]

    def _ps_bump():
        _PS_VER[0] += 1

    class _PSChildren(list):
        """A node's children: a plain list that notes every change."""
        __slots__ = ()

        def append(self, x):
            _PS_VER[0] += 1
            list.append(self, x)

        def extend(self, it):
            _PS_VER[0] += 1
            list.extend(self, it)

        def insert(self, i, x):
            _PS_VER[0] += 1
            list.insert(self, i, x)

        def remove(self, x):
            _PS_VER[0] += 1
            list.remove(self, x)

        def pop(self, *a):
            _PS_VER[0] += 1
            return list.pop(self, *a)

        def clear(self):
            _PS_VER[0] += 1
            list.clear(self)

        def sort(self, *a, **kw):
            _PS_VER[0] += 1
            list.sort(self, *a, **kw)

        def reverse(self):
            _PS_VER[0] += 1
            list.reverse(self)

        def __setitem__(self, i, v):
            _PS_VER[0] += 1
            list.__setitem__(self, i, v)

        def __delitem__(self, i):
            _PS_VER[0] += 1
            list.__delitem__(self, i)

        def __iadd__(self, other):
            _PS_VER[0] += 1
            return list.__iadd__(self, other)

        def __imul__(self, n):
            _PS_VER[0] += 1
            return list.__imul__(self, n)

        def __reduce_ex__(self, protocol):      # copy / pickle as a plain list
            return (list, (list(self),))

    def _ps_get_children(self):
        try:
            return self.__dict__["_ps_children"]
        except KeyError:
            c = _PSChildren()
            self.__dict__["_ps_children"] = c
            return c

    def _ps_set_children(self, value):
        _PS_VER[0] += 1
        self.__dict__["_ps_children"] = value if isinstance(value, _PSChildren) else _PSChildren(value or ())

    if _PS_ON:
        SceneNode.children = property(_ps_get_children, _ps_set_children)

        _PS_PREV_TRAVERSE = SceneNode.traverse
        _PS_TRAV = {}

        def _ps_traverse(self):
            key = id(self)
            hit = _PS_TRAV.get(key)
            if hit is not None and hit[0] is self and hit[1] == _PS_VER[0]:
                return list(hit[2])
            out = _PS_PREV_TRAVERSE(self)
            if len(_PS_TRAV) > 512:
                _PS_TRAV.clear()
            _PS_TRAV[key] = (self, _PS_VER[0], out)
            return list(out)

        SceneNode.traverse = _ps_traverse

    # ---- numpy batch of the frame's meshes ----------------------------------
    _PS_BB = {}                      # id(mesh) -> (mesh, (2,3) float64 local box)
    _PS_BATCH = {"key": None, "data": None}

    def _ps_local_box(mesh):
        e = _PS_BB.get(id(mesh))
        if e is not None and e[0] is mesh and e[2] is getattr(mesh, "bounding_box", None):
            return e[1]
        bb = mesh.bounding_box
        arr = np.array([[bb[0][0], bb[0][1], bb[0][2]], [bb[1][0], bb[1][1], bb[1][2]]], np.float64)
        if len(_PS_BB) > 8192:
            _PS_BB.clear()
        _PS_BB[id(mesh)] = (mesh, arr, bb)
        return arr

    def _ps_batch(items):
        """{"M": (N,4,4) column-major world matrices, "lo"/"hi": local boxes,
        "wmin"/"wmax": world AABBs} for a traverse_meshes() list."""
        n = len(items)
        if n == 0:
            return None
        key = (n, id(items[0]), id(items[-1]), id(items[n // 2]))
        if _PS_BATCH["key"] == key and _PS_BATCH["data"] is not None:
            d = _PS_BATCH["data"]
            if d["first"] is items[0] and d["last"] is items[-1]:
                return d
        M = np.frombuffer(b"".join(it[2].to_bytes() for it in items), np.float32).reshape(n, 4, 4)
        M = M.astype(np.float64)
        boxes = np.empty((n, 2, 3), np.float64)
        for i, it in enumerate(items):
            boxes[i] = _ps_local_box(it[1])
        lo, hi = boxes[:, 0], boxes[:, 1]
        # world AABB (same p/n-vertex maths as the scalar code): column-major
        # M[n, c, r] is row r, column c
        R = M[:, :3, :3]                           # [n, c, r]
        t = M[:, 3, :3]
        pos = np.clip(R, 0.0, None)
        neg = np.clip(R, None, 0.0)
        wmin = t + np.einsum("ncr,nc->nr", pos, lo) + np.einsum("ncr,nc->nr", neg, hi)
        wmax = t + np.einsum("ncr,nc->nr", pos, hi) + np.einsum("ncr,nc->nr", neg, lo)
        d = {"M": M, "lo": lo, "hi": hi, "wmin": wmin, "wmax": wmax,
             "first": items[0], "last": items[-1]}
        _PS_BATCH["key"], _PS_BATCH["data"] = key, d
        return d

    def _ps_planes_mask(d, planes):
        """True for every object whose world AABB isn't fully outside a plane."""
        wmin, wmax = d["wmin"], d["wmax"]
        keep = np.ones(len(wmin), bool)
        for nx, ny, nz, dd in planes:
            nrm = np.array([nx, ny, nz], np.float64)
            p = np.where(nrm >= 0.0, wmax, wmin)
            keep &= (p @ nrm + dd) >= 0.0
        return keep

    def _h3d_frustum_mask(items, planes):
        """Vectorized camera frustum cull for Renderer.render (None = not available)."""
        if not _PS_ON or not items:
            return None
        return _ps_planes_mask(_ps_batch(items), planes)

    _PS_PREV_CASTERS = globals().get("_h3d274_shadow_casters")

    def _ps_shadow_casters(items, light_vp, margin=2.0):
        if not _PS_ON or not items or len(items) < 64:
            return _PS_PREV_CASTERS(items, light_vp, margin) if _PS_PREV_CASTERS else items
        row = [[light_vp[c][r] for c in range(4)] for r in range(4)]
        planes = []
        for ci, sg in ((0, 1.0), (0, -1.0), (1, 1.0), (1, -1.0), (2, 1.0), (2, -1.0)):
            p = [row[3][i] + sg * row[ci][i] for i in range(4)]
            ln = math.sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2])
            if ln < 1e-12:
                return items
            planes.append((p[0] / ln, p[1] / ln, p[2] / ln, p[3] / ln + margin))
        try:
            keep = _ps_planes_mask(_ps_batch(items), planes)
        except Exception:
            return _PS_PREV_CASTERS(items, light_vp, margin) if _PS_PREV_CASTERS else items
        return [it for it, k in zip(items, keep) if k]

    if _PS_PREV_CASTERS is not None:
        _h3d274_shadow_casters = _ps_shadow_casters

    _PS_PREV_FIT = Renderer._fit_light_frustum

    def _ps_fit_light_frustum(self, scene, light_view):
        """Same fit as Renderer._fit_light_frustum (the 8 local box corners of
        every mesh through model and light view), on all meshes at once."""
        if not _PS_ON:
            return _PS_PREV_FIT(self, scene, light_view)
        items = scene.traverse_meshes()
        fr = _SB["frame"].get(id(scene)) if "_SB" in globals() else None
        sect = fr[1] if fr is not None and fr[0] is scene else []
        if len(items) < 64 and not sect:
            return _PS_PREV_FIT(self, scene, light_view)
        try:
            LV = np.frombuffer(light_view.to_bytes(), np.float32).reshape(4, 4).astype(np.float64)
            pts = []
            if items:
                d = _ps_batch(items)
                lo, hi, M = d["lo"], d["hi"], d["M"]
                corners = np.stack([np.stack([np.where((k >> a) & 1, hi[:, a], lo[:, a]) for a in range(3)], axis=1)
                                    for k in range(8)], axis=1)              # (n, 8, 3)
                # p = LV * (M * c): column-major -> use transposes
                W = np.einsum("ncr,nkc->nkr", M[:, :3, :3], corners) + M[:, None, 3, :3]
                pts.append(W.reshape(-1, 3))
            for e in sect:                   # batched sectors: their world box corners
                lo, hi = e["box"]
                pts.append(np.array([[hi[0] if k & 1 else lo[0], hi[1] if k & 2 else lo[1],
                                      hi[2] if k & 4 else lo[2]] for k in range(8)], np.float64))
            if not pts:
                return _PS_PREV_FIT(self, scene, light_view)
            P = np.concatenate(pts) @ LV[:3, :3] + LV[3, :3]
            mins = P.min(axis=0)
            maxs = P.max(axis=0)
        except Exception:
            return _PS_PREV_FIT(self, scene, light_view)
        pad = 2.0
        left, right = mins[0] - pad, maxs[0] + pad
        bottom, top = mins[1] - pad, maxs[1] + pad
        try:
            nn = max(256, int(self.shadow_map_size))

            def _stable(lo_, hi_):
                half = max((hi_ - lo_) * 0.5, 0.5)
                q = max(0.5, 2.0 ** math.floor(math.log2(half * 0.03 + 1e-9)))
                half = math.ceil(half / q) * q
                step = (2.0 * half) / nn
                mid = math.floor(((lo_ + hi_) * 0.5) / step + 0.5) * step
                return mid - half, mid + half
            left, right = _stable(left, right)
            bottom, top = _stable(bottom, top)
        except Exception:
            pass
        near = max(0.1, -float(maxs[2]) - 60.0)
        far = max(near + 1.0, -float(mins[2]) + 60.0)
        return glm.ortho(float(left), float(right), float(bottom), float(top), near, far)

    Renderer._fit_light_frustum = _ps_fit_light_frustum

    # ---- building sectors drawn as cached batches -------------------------
    #   A sector root (an object whose whole subtree is procedural building
    #   modules, >= 32 of them) is collected ONCE into instance blocks per
    #   (mesh, material) plus one bounding box.  Per frame it costs a box test
    #   and ~10 instanced draws instead of 400+ per-object updates.  It is
    #   rebuilt when its subtree changes (paint / erase / delete / load), when
    #   the editor's undo history moves, and is skipped while anything in it is
    #   selected (gizmo drags stay exact).  Every frame one sector is fully
    #   re-checked (transforms, visibility, mesh, material) as a safety net.
    _SB = {"cache": {}, "engine": None, "epoch": 0, "rr": 0, "frame": {}}

    def _sb_ok_member(n):
        ud = n.user_data
        return (n.mesh_type == "procedural_module" and not n.blueprints and n.rigidbody is None
                and not n.is_point_light and not n.is_spot_light
                and not getattr(n, "spawn_kind", None)
                and not (ud and ("particle_system" in ud or "_tp_terrain_data" in ud)))

    def _sb_snapshot(nodes):
        h = []
        for m in nodes:
            h.append((m.position.to_bytes(), m.rotation_euler.to_bytes(), m.scale.to_bytes(),
                      m.visible, id(m.mesh), id(m.material), m.mesh_type, len(m.blueprints),
                      m.rigidbody is not None))
        return hash(tuple(h))

    def _sb_build(n, w):
        V, E = _PS_VER[0], _SB["epoch"]
        nodes = n.traverse()
        neg = {"node": n, "ver": V, "epoch": E, "nodes": nodes, "groups": None}
        if len(nodes) < 33 or not all(_sb_ok_member(m) for m in nodes[1:]):
            return neg
        groups, loose, boxes, mats = {}, [], [], []
        stack = [(c, w) for c in reversed(n.children)]
        while stack:
            m, pw = stack.pop()
            mw = pw * m.local_matrix()
            if m.visible and m.mesh is not None:
                mat = m.material
                mesh = m.mesh
                if (mat is None or getattr(mesh, "vbo", None) is None
                        or (mat.alpha_mode or "OPAQUE").upper() == "BLEND"):
                    loose.append((m, mesh, mw))
                else:
                    g = groups.get((id(mesh), id(mat)))
                    if g is None:
                        g = groups[(id(mesh), id(mat))] = [mesh, mat, []]
                    g[2].append(mw.to_bytes())
                    boxes.append(_ps_local_box(mesh))
                    mats.append(mw)
            ch = m.children
            for i in range(len(ch) - 1, -1, -1):
                stack.append((ch[i], mw))
        if not groups:
            return neg
        # world box of the batched part (same p/n-vertex maths as the culler)
        M = np.frombuffer(b"".join(x.to_bytes() for x in mats), np.float32).reshape(-1, 4, 4).astype(np.float64)
        bx = np.asarray(boxes, np.float64)
        R, t = M[:, :3, :3], M[:, 3, :3]
        pos, negm = np.clip(R, 0.0, None), np.clip(R, None, 0.0)
        wmin = (t + np.einsum("ncr,nc->nr", pos, bx[:, 0]) + np.einsum("ncr,nc->nr", negm, bx[:, 1])).min(axis=0)
        wmax = (t + np.einsum("ncr,nc->nr", pos, bx[:, 1]) + np.einsum("ncr,nc->nr", negm, bx[:, 0])).max(axis=0)
        glist = []
        for mesh, mat, chunks in groups.values():
            base = np.frombuffer(b"".join(chunks), np.float32).reshape(-1, 4, 4)
            glist.append([mesh, mat, base, _InstBlob(base.tobytes(), len(base))])
        return {"node": n, "ver": V, "epoch": E, "nodes": nodes, "groups": glist,
                "loose0": loose, "loose": list(loose), "w0": glm.mat4(w), "w_cur": glm.mat4(w),
                "box0": (wmin, wmax), "box": (wmin, wmax), "snap": _sb_snapshot(nodes)}

    def _sb_move(e, w):
        """The sector root moved: shift every cached matrix by the same delta."""
        D = w * glm.inverse(e["w0"])
        Ds = np.frombuffer(D.to_bytes(), np.float32).reshape(4, 4).astype(np.float64)
        for g in e["groups"]:
            new = (g[2].astype(np.float64).reshape(-1, 4, 4) @ Ds).astype(np.float32)
            g[3] = _InstBlob(new.tobytes(), len(new))
        e["loose"] = [(m, mesh, D * mw) for m, mesh, mw in e["loose0"]]
        lo, hi = e["box0"]
        corners = np.array([[hi[0] if k & 1 else lo[0], hi[1] if k & 2 else lo[1], hi[2] if k & 4 else lo[2]]
                            for k in range(8)], np.float64)
        wc = corners @ Ds[:3, :3] + Ds[3, :3]
        e["box"] = (wc.min(axis=0), wc.max(axis=0))
        e["w_cur"] = glm.mat4(w)

    def _sb_entry(n, w):
        cache = _SB["cache"]
        e = cache.get(id(n))
        if e is None or e["node"] is not n or e["epoch"] != _SB["epoch"]:
            e = _sb_build(n, w)
        elif e["ver"] != _PS_VER[0]:
            now = n.traverse()
            old = e["nodes"]
            if len(now) != len(old) or any(a is not b for a, b in zip(now, old)):
                e = _sb_build(n, w)
            else:
                e["ver"] = _PS_VER[0]
        if len(cache) > 4096:
            cache.clear()
        cache[id(n)] = e
        if e["groups"] is None:
            return None
        if w != e["w_cur"]:
            _sb_move(e, w)
        return e

    def _sb_selected_chain():
        eng = _SB.get("engine")
        sel = getattr(eng, "selected", None) if eng is not None else None
        out = set()
        while sel is not None:
            out.add(id(sel))
            sel = sel.parent
        return out

    def _sb_collect(scene):
        """In-render mesh list: like _h3d274_collect, but building sectors come
        back as cached batches (their transparent pieces stay in the list)."""
        out, batches = [], []
        skip = _sb_selected_chain()
        stack = [(scene.root, None)]
        while stack:
            n, pw = stack.pop()
            w = n.local_matrix() if pw is None else pw * n.local_matrix()
            ch = n.children
            if len(ch) >= 32 and n.mesh is None and id(n) not in skip:
                try:
                    e = _sb_entry(n, w)
                except Exception as ex:
                    e = None
                    Log.warn(f"[Perf] sector batch skipped: {ex}")
                if e is not None:
                    batches.append(e)
                    out.extend(e["loose"])
                    continue
            if n.visible and n.mesh is not None:
                out.append((n, n.mesh, w))
            for i in range(len(ch) - 1, -1, -1):
                stack.append((ch[i], w))
        _SB["frame"][id(scene)] = (scene, batches)
        return out

    def _h3d_sector_batches(scene, planes=None, light_vp=None):
        """[(mesh, material, _InstBlob)] of this frame's batched sectors, the
        ones whose box is inside ``planes`` (all when None)."""
        fr = _SB["frame"].get(id(scene))
        if fr is None or fr[0] is not scene:
            return []
        out = []
        for e in fr[1]:
            if planes is not None:
                lo, hi = e["box"]
                inside = True
                for nx, ny, nz, dd in planes:
                    px = hi[0] if nx >= 0.0 else lo[0]
                    py = hi[1] if ny >= 0.0 else lo[1]
                    pz = hi[2] if nz >= 0.0 else lo[2]
                    if nx * px + ny * py + nz * pz + dd < 0.0:
                        inside = False
                        break
                if not inside:
                    continue
            for g in e["groups"]:
                out.append((g[0], g[1], g[3]))
        return out

    def _sb_validate_one():
        """Safety net: fully re-check one cached sector per frame."""
        cache = _SB["cache"]
        if not cache:
            return
        keys = list(cache.keys())
        _SB["rr"] = (_SB["rr"] + 1) % len(keys)
        e = cache.get(keys[_SB["rr"]])
        if e is None or e.get("groups") is None:
            return
        try:
            if _sb_snapshot(e["nodes"]) != e["snap"]:
                cache.pop(keys[_SB["rr"]], None)      # rebuilt in this frame's collect
        except Exception:
            cache.pop(keys[_SB["rr"]], None)

    def sector_batches_invalidate():
        """Rebuild every building batch (undo / redo, bulk edits)."""
        _SB["epoch"] += 1

    if _PS_ON:
        _PS_PREV_TM2 = Scene.traverse_meshes

        def _ps_traverse_meshes(self):
            P = _H3D274_PERF
            if P["on"] and P["depth"] > 0:
                c = P["cache"].get(id(self))
                if c is not None and c[0] is self:
                    return list(c[1])
                out = _sb_collect(self)
                P["cache"][id(self)] = (self, out)
                return list(out)
            return _PS_PREV_TM2(self)

        Scene.traverse_meshes = _ps_traverse_meshes

        _PS_PREV_INIT = HeavenEngine.__init__

        def _ps_engine_init(self, *a, **kw):
            _SB["engine"] = self
            _SB["cache"].clear()
            _PS_PREV_INIT(self, *a, **kw)

        HeavenEngine.__init__ = _ps_engine_init

        _PS_PREV_ATTACH = HeavenEngine.attach_blueprint

        def _ps_attach_blueprint(self, node, *a, **kw):
            _ps_bump()                       # a scripted object can't stay in a static batch
            _SB["epoch"] += 1
            return _PS_PREV_ATTACH(self, node, *a, **kw)

        HeavenEngine.attach_blueprint = _ps_attach_blueprint

    # ---- "everything but plain building modules" for per-frame scanners ----
    _PS_SPECIAL = {}

    def _ps_plain_module(n):
        if n.mesh_type != "procedural_module":
            return False
        ud = n.user_data
        return (not n.blueprints and n.rigidbody is None and not n.is_point_light
                and not n.is_spot_light and not getattr(n, "spawn_kind", None)
                and not (ud and "particle_system" in ud))

    def _ps_special_nodes(self):
        """Every node except plain building modules (no script, light, spawn,
        particles): what the per-frame scanners (lights, icons, sounds,
        particles, terrain, characters) look through.  Cached until the tree
        changes."""
        if not _PS_ON:
            return self.root.traverse()
        c = _PS_SPECIAL.get(id(self))
        key = (_PS_VER[0], _SB["epoch"])
        if c is not None and c[0] is self and c[1] == key:
            return c[2]
        nodes = self.root.traverse()
        out = [n for n in nodes if not _ps_plain_module(n)]
        if len(_PS_SPECIAL) > 64:
            _PS_SPECIAL.clear()
        _PS_SPECIAL[id(self)] = (self, key, out)
        return out

    Scene.special_nodes = _ps_special_nodes

    if _PS_ON:
        def _ps_point_lights(self):
            return [(n, n.point_light) for n in self.special_nodes()
                    if n.visible and n.is_point_light and n.point_light is not None]

        def _ps_spot_lights(self):
            return [(n, n.spot_light) for n in self.special_nodes()
                    if n.visible and n.is_spot_light and n.spot_light is not None]

        Scene.traverse_point_lights = _ps_point_lights
        Scene.traverse_spot_lights = _ps_spot_lights

    _PS_PREV_RENDER = Renderer.render

    def _ps_render(self, scene, camera):
        try:
            _sb_validate_one()
        except Exception:
            pass
        try:
            return _PS_PREV_RENDER(self, scene, camera)
        finally:
            _PS_BATCH["key"], _PS_BATCH["data"] = None, None   # never reuse across frames
            _SB["frame"].pop(id(scene), None)

    Renderer.render = _ps_render
except Exception as _ps_ex:
    print(f"[Heaven3D][WARN] scale perf block not installed: {_ps_ex}")

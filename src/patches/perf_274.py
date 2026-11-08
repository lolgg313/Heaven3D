# @heaven3d-part patches/perf_274.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)

# ---------------------------------------------------------------------------
# [H3D274:perf BEGIN]  Lossless render-loop optimizations (2.7.4)
#
#   Same pixels, less CPU work per frame (what the big Unity optimizers call
#   "CPU-side batching and culling"):
#     * the scene graph is walked ONCE per frame: Scene.traverse_meshes() is
#       cached for the duration of Renderer.render() (the light-frustum fit,
#       shadow, geometry and transparency passes used to walk it 4 times), and
#       world matrices are built top-down (parent_world * local, the exact same
#       math and order as SceneNode.world_matrix(), each node computed once)
#     * a material bound twice in a row to the geometry program is not
#       re-uploaded (25+ uniforms and 5 texture binds per draw)
#     * shadow casters entirely outside the sun's shadow volume are skipped
#       (they cannot write a single shadow-map texel)
#   Disable with HEAVEN3D_NO_PERF=1 to compare.
# ---------------------------------------------------------------------------
try:
    _H3D274_PERF = {"depth": 0, "cache": {}, "last_bind": None, "last_mat": None,
                    "on": os.environ.get("HEAVEN3D_NO_PERF", "") not in ("1", "true", "yes")}

    def _h3d274_collect(scene):
        out = []
        root = scene.root
        stack = [(root, None)]
        while stack:
            n, pw = stack.pop()
            w = n.local_matrix() if pw is None else pw * n.local_matrix()
            if n.visible and n.mesh is not None:
                out.append((n, n.mesh, w))
            ch = n.children
            for i in range(len(ch) - 1, -1, -1):
                stack.append((ch[i], w))
        return out

    _H3D274_PREV_TM = Scene.traverse_meshes

    def _h3d274_traverse_meshes(self):
        P = _H3D274_PERF
        if not P["on"]:
            return _H3D274_PREV_TM(self)
        if P["depth"] > 0:
            c = P["cache"].get(id(self))
            if c is not None and c[0] is self:
                return list(c[1])
            out = _h3d274_collect(self)
            P["cache"][id(self)] = (self, out)
            return list(out)
        return _h3d274_collect(self)

    Scene.traverse_meshes = _h3d274_traverse_meshes

    _H3D274_PREV_TRAVERSE = SceneNode.traverse

    def _h3d274_traverse(self):
        """Same pre-order list as the recursive version, without the
        recursion / list re-concatenation (it runs several times a frame)."""
        if not _H3D274_PERF["on"]:
            return _H3D274_PREV_TRAVERSE(self)
        out = []
        stack = [self]
        pop, push, app = stack.pop, stack.extend, out.append
        while stack:
            n = pop()
            app(n)
            ch = n.children
            if ch:
                push(ch[::-1])
        return out

    SceneNode.traverse = _h3d274_traverse

    _H3D274_PREV_RENDER = Renderer.render

    def _h3d274_render(self, scene, camera):
        P = _H3D274_PERF
        P["depth"] += 1
        P["last_bind"] = None
        P["last_mat"] = None
        try:
            return _H3D274_PREV_RENDER(self, scene, camera)
        finally:
            P["depth"] -= 1
            P["last_bind"] = None
            P["last_mat"] = None
            if P["depth"] <= 0:
                P["depth"] = 0
                P["cache"].clear()

    Renderer.render = _h3d274_render

    _H3D274_PREV_BIND = Renderer._bind_material

    def _h3d274_bind_material(self, prog, mat):
        P = _H3D274_PERF
        if P["on"] and P["depth"] > 0 and mat is not None:
            key = (id(prog), id(mat))
            if key == P["last_bind"] and P["last_mat"] is mat:
                geo = ShaderLib._cache.get("geometry")
                if geo is not None and prog is geo:
                    return
        _H3D274_PREV_BIND(self, prog, mat)
        P["last_bind"] = (id(prog), id(mat))
        P["last_mat"] = mat                # keeps the id valid (no reuse)

    Renderer._bind_material = _h3d274_bind_material

    def _h3d274_shadow_casters(items, light_vp, margin=2.0):
        """Drop meshes whose world AABB lies completely outside one plane of the
        light's (orthographic) view volume.  Conservative p-vertex test with a
        margin for vertex-shader displacement (wind, skinning)."""
        if not _H3D274_PERF["on"]:
            return items
        row = [[light_vp[c][r] for c in range(4)] for r in range(4)]
        planes = []
        for ci, sg in ((0, 1.0), (0, -1.0), (1, 1.0), (1, -1.0), (2, 1.0), (2, -1.0)):
            p = [row[3][i] + sg * row[ci][i] for i in range(4)]
            ln = math.sqrt(p[0] * p[0] + p[1] * p[1] + p[2] * p[2])
            if ln < 1e-12:
                return items
            planes.append((p[0] / ln, p[1] / ln, p[2] / ln, p[3] / ln + margin))
        out = []
        for it in items:
            node, mesh, model = it
            try:
                bmin, bmax = mesh.bounding_box
                wmin = [model[3][0], model[3][1], model[3][2]]
                wmax = list(wmin)
                for a in range(3):
                    for b in range(3):
                        e = model[b][a]
                        if e >= 0.0:
                            wmin[a] += e * bmin[b]; wmax[a] += e * bmax[b]
                        else:
                            wmin[a] += e * bmax[b]; wmax[a] += e * bmin[b]
                inside = True
                for nx, ny, nz, d in planes:
                    px = wmax[0] if nx >= 0.0 else wmin[0]
                    py = wmax[1] if ny >= 0.0 else wmin[1]
                    pz = wmax[2] if nz >= 0.0 else wmin[2]
                    if nx * px + ny * py + nz * pz + d < 0.0:
                        inside = False
                        break
                if inside:
                    out.append(it)
            except Exception:
                out.append(it)             # never drop something we can't test
        return out

except Exception as _h3d274_ex:
    print(f"[Heaven3D][WARN] perf block not installed: {_h3d274_ex}")
# [H3D274:perf END]

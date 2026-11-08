# @heaven3d-part h3d27/engine/02_mesh.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 2. Mesh: optional 2nd vertex buffer (colour + uv1)
# ---------------------------------------------------------------
_H3D27_PREV_GET_VAO = Mesh.get_vao
_H3D27_PREV_MESH_RELEASE = Mesh.release

class _H3D27CullVAO:
    """Back-face culling for single-sided glTF materials (the spec
    requires it).  Enabled only around this draw; the engine default
    (culling OFF) is restored straight after."""
    __slots__ = ("_vao", "_ctx")

    def __init__(self, vao, ctx):
        self._vao, self._ctx = vao, ctx

    def render(self, *a, **kw):
        self._ctx.enable(moderngl.CULL_FACE)
        try:
            return self._vao.render(*a, **kw)
        finally:
            self._ctx.disable(moderngl.CULL_FACE)

    def __getattr__(self, name):
        return getattr(self._vao, name)

def _h3d27_is_surface_prog(program):
    c = ShaderLib._cache
    return program is c.get("geometry") or program is c.get("forward")

def _h3d27_get_vao(self, program):
    if getattr(self, "_h3d_cull", False) and _h3d27_is_surface_prog(program):
        key = ("cull", id(program))
        v = self._vaos.get(key)
        if v is None:
            v = _H3D27CullVAO(_h3d27_get_vao_raw(self, program), self.ctx)
            self._vaos[key] = v
        return v
    return _h3d27_get_vao_raw(self, program)

def _h3d27_get_vao_raw(self, program):
    ext = getattr(self, "_h3d_vbo_ext", None)
    if ext is None:
        return _H3D27_PREV_GET_VAO(self, program)
    pid = id(program)
    if pid in self._vaos:
        return self._vaos[pid]
    if self.vbo is None:
        raise RuntimeError(f"Mesh '{self.name}' has no VBO uploaded.")
    attrs = set(program._members.keys()) if hasattr(program, "_members") else set()
    fmt, names = [], []
    for nm, n in (("in_position", 3), ("in_normal", 3), ("in_tangent", 4), ("in_uv", 2)):
        if nm in attrs:
            fmt.append(f"{n}f"); names.append(nm)
        else:
            fmt.append(f"{n}x4")
    content = [(self.vbo, " ".join(fmt), *names)]
    f2, n2 = [], []
    for nm, n in (("in_h3d_color", 4), ("in_h3d_uv1", 2)):
        if nm in attrs:
            f2.append(f"{n}f"); n2.append(nm)
        else:
            f2.append(f"{n}x4")
    if n2:
        content.append((ext, " ".join(f2), *n2))
    vao = self.ctx.vertex_array(program, content,
                                index_buffer=self.ibo if self.indexed else None)
    self._vaos[pid] = vao
    return vao

def _h3d27_mesh_release(self):
    for k in [k for k in self._vaos if isinstance(k, tuple)]:
        self._vaos.pop(k, None)
    _H3D27_PREV_MESH_RELEASE(self)
    ext = getattr(self, "_h3d_vbo_ext", None)
    if ext is not None:
        try:
            ext.release()
        except Exception:
            pass
        self._h3d_vbo_ext = None

Mesh.get_vao = _h3d27_get_vao
Mesh.release = _h3d27_mesh_release

def _h3d27_mesh_from_part(ctx, part):
    md = GlbImporter.MeshData(
        name=part.get("name", "mesh"), positions=part["positions"],
        normals=part["normals"], uvs=part.get("uv0"),
        tangents=part.get("tangents"), indices=part["indices"],
        material_info=None)
    mesh = Mesh.from_glb_mesh_data(ctx, md)
    col, uv1 = part.get("colors"), part.get("uv1")
    if col is not None or uv1 is not None:
        n = len(part["positions"])
        ext = np.zeros((n, 6), np.float32)
        ext[:, 3] = 1.0
        if col is not None:
            ext[:, 0:3] = 1.0 - np.asarray(col, np.float32)[:, :3]
            ext[:, 3] = np.asarray(col, np.float32)[:, 3]
        if uv1 is not None:
            ext[:, 4:6] = uv1
        mesh._h3d_vbo_ext = ctx.buffer(ext.tobytes())
        mesh._h3d_has_colors = col is not None
    return mesh

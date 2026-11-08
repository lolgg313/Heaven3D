# @heaven3d-part h3d27/engine/05_colliders.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# ---------------------------------------------------------------
# 5. Colliders treat an imported model as ONE shape
# ---------------------------------------------------------------
def _h3d27_has_parts(node):
    return any(getattr(c, "_heaven3d_imported_part", False) and c.mesh is not None
               for c in node.children)

class _H3D27CollisionMesh:
    __slots__ = ("cpu_positions", "cpu_indices", "vertex_count", "index_count",
                 "_vertex_data", "indexed", "bounding_sphere", "name")

def _h3d27_collision_proxy(node):
    key = tuple(id(c.mesh) for c in node.traverse()
                if c.mesh is not None and (c is node or getattr(c, "_heaven3d_imported_part", False)))
    px = getattr(node, "_h3d_collision_proxy", None)
    if px is not None and px[0] == key:
        return px[1]
    inv = glm.inverse(node.world_matrix())
    P, I, off = [], [], 0
    for c in node.traverse():
        if c.mesh is None or not (c is node or getattr(c, "_heaven3d_imported_part", False)):
            continue
        pos, idx = _mesh_cpu_triangles(c.mesh)
        if pos is None:
            continue
        rel = inv * c.world_matrix()
        cols = np.array([[rel[a][b] for b in range(4)] for a in range(4)], np.float32)
        hom = np.ones((len(pos), 4), np.float32); hom[:, :3] = pos
        P.append((hom @ cols)[:, :3]); I.append(np.asarray(idx, np.uint32) + off)
        off += len(pos)
    if not P:
        return None
    m = _H3D27CollisionMesh()
    m.cpu_positions = np.concatenate(P).astype(np.float32)
    m.cpu_indices = np.concatenate(I).astype(np.uint32)
    m.vertex_count = len(m.cpu_positions); m.index_count = len(m.cpu_indices)
    m._vertex_data = None; m.indexed = True; m.name = node.name + "_collision"
    lo, hi = m.cpu_positions.min(0), m.cpu_positions.max(0)
    c = (lo + hi) * 0.5
    m.bounding_sphere = (glm.vec3(*c.tolist()),
                         float(np.linalg.norm(m.cpu_positions - c, axis=1).max()))
    node._h3d_collision_proxy = (key, m)
    return m

_H3D27_PREV_SHAPE_VIEW = _mesh_shape_view

def _mesh_shape_view(node, col):
    if not _h3d27_has_parts(node):
        return _H3D27_PREV_SHAPE_VIEW(node, col)
    proxy = _h3d27_collision_proxy(node)
    if proxy is None:
        return _H3D27_PREV_SHAPE_VIEW(node, col)
    real = node.mesh
    node.mesh = proxy
    try:
        return _H3D27_PREV_SHAPE_VIEW(node, col)
    finally:
        node.mesh = real

_H3D27_PREV_ADD_BOX = HeavenEngine.add_box_collider
_H3D27_PREV_ADD_SPHERE = HeavenEngine.add_sphere_collider
_H3D27_PREV_ADD_CAPSULE = HeavenEngine.add_capsule_collider

def _h3d27_fit(self, node):
    if node.source_path is None and not _h3d27_has_parts(node):
        return None, None
    try:
        lo, hi = self._spawn_mesh_local_aabb(node)
    except Exception:
        return None, None
    return lo, hi

def _h3d27_add_box(self, node, size=None, **kw):
    if size is None and "center" not in kw:
        lo, hi = _h3d27_fit(self, node)
        if lo is not None:
            s = glm.max(hi - lo, glm.vec3(0.02)); c = (lo + hi) * 0.5
            return _H3D27_PREV_ADD_BOX(self, node, size=(s.x, s.y, s.z),
                                       center=(c.x, c.y, c.z), **kw)
    return _H3D27_PREV_ADD_BOX(self, node, size=size, **kw)

def _h3d27_add_sphere(self, node, radius=None, **kw):
    if radius is None and "center" not in kw:
        lo, hi = _h3d27_fit(self, node)
        if lo is not None:
            c = (lo + hi) * 0.5
            return _H3D27_PREV_ADD_SPHERE(self, node, radius=float(glm.length(hi - lo)) * 0.5,
                                          center=(c.x, c.y, c.z), **kw)
    return _H3D27_PREV_ADD_SPHERE(self, node, radius=radius, **kw)

def _h3d27_add_capsule(self, node, radius=0.4, height=1.8, **kw):
    if "center" not in kw and (radius, height) == (0.4, 1.8):
        lo, hi = _h3d27_fit(self, node)
        if lo is not None:
            s = hi - lo; c = (lo + hi) * 0.5
            r = max(float(max(s.x, s.z)) * 0.5, 0.01)
            return _H3D27_PREV_ADD_CAPSULE(self, node, radius=r, height=max(float(s.y), 2 * r),
                                           center=(c.x, c.y, c.z), **kw)
    return _H3D27_PREV_ADD_CAPSULE(self, node, radius=radius, height=height, **kw)

HeavenEngine.add_box_collider = _h3d27_add_box
HeavenEngine.add_sphere_collider = _h3d27_add_sphere
HeavenEngine.add_capsule_collider = _h3d27_add_capsule

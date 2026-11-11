# @heaven3d-part physics/physics.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 14A —  CPU PHYSICS ENGINE  (colliders, rigid bodies, solver)
# #############################################################################
#
#  A real impulse-based physics engine running entirely on the CPU:
#      * Colliders: Box (OBB), Sphere, Capsule, bounded Plane, Terrain
#        Heightfield, and triangle-exact Mesh (BVH-accelerated, for
#        low-poly level geometry like buildings with interiors) — all
#        serialised into .heaven3d projects
#      * RigidBody: dynamic / kinematic / static, mass, restitution,
#        friction, damping, gravity scale, freeze-rotation, impulses
#      * Sequential-impulse contact solver with Baumgarte position
#        correction, restitution and Coulomb friction, fixed 60 Hz
#        timestep with 2 substeps (120 Hz effective)
#      * Collision + trigger events dispatched to Blueprint hooks
#      * Raycast / overlap queries (hitscan weapons, AI line-of-sight)
#      * CharacterController: collide-and-slide capsule with slopes,
#        step offset and ground detection — the FPS player mover
#
#  Everything here is pure Python + pyglm; no GL objects are touched,
#  so the simulation is fully testable headless.

# ------------------------------------------------------------- math helpers

def _v3(v) -> glm.vec3:
    """Coerce anything vec3-like into a plain glm.vec3 (defensive)."""
    return glm.vec3(float(v[0]), float(v[1]), float(v[2]))


def _safe_normalize(v: glm.vec3, fallback: glm.vec3 = None) -> glm.vec3:
    """Normalize without ever producing NaNs."""
    l = glm.length(v)
    if l < 1e-9:
        return glm.vec3(fallback) if fallback is not None else glm.vec3(0.0, 1.0, 0.0)
    return v / l


def _euler_to_quat(euler_deg: glm.vec3) -> glm.quat:
    """Node Euler degrees (Unity Y-X-Z order) -> quaternion.
    R = Ry(yaw) * Rx(pitch) * Rz(roll); quat product applies right-to-left."""
    qy = glm.angleAxis(math.radians(float(euler_deg.y)), glm.vec3(0.0, 1.0, 0.0))
    qx = glm.angleAxis(math.radians(float(euler_deg.x)), glm.vec3(1.0, 0.0, 0.0))
    qz = glm.angleAxis(math.radians(float(euler_deg.z)), glm.vec3(0.0, 0.0, 1.0))
    return glm.normalize(qy * qx * qz)


def _quat_to_euler(q: glm.quat) -> glm.vec3:
    """Quaternion -> node Euler degrees in the engine's Y-X-Z convention.
    Derived from R = Ry Rx Rz (glm matrices are column-major: m[col][row]):
        m[2][1] = -sin(pitch)
        yaw  = atan2(m[2][0], m[2][2])
        roll = atan2(m[0][1], m[1][1])"""
    m = glm.mat3_cast(q)
    sp = _clamp(-float(m[2][1]), -1.0, 1.0)
    pitch = math.asin(sp)
    if abs(math.cos(pitch)) > 1e-6:
        yaw = math.atan2(float(m[2][0]), float(m[2][2]))
        roll = math.atan2(float(m[0][1]), float(m[1][1]))
    else:
        if pitch > 0.0:
            yaw = math.atan2(float(m[1][0]), float(m[0][0]))
        else:
            yaw = -math.atan2(float(m[1][0]), float(m[0][0]))
        roll = 0.0
    return glm.vec3(math.degrees(pitch), math.degrees(yaw), math.degrees(roll))


def _basis_to_quat(x: glm.vec3, y: glm.vec3, z: glm.vec3) -> glm.quat:
    """Build a quaternion from three (roughly) orthonormal basis columns.
    Uses Shepperd's method — robust even with slight non-orthonormality."""
    x = _safe_normalize(x, glm.vec3(1, 0, 0))
    y = _safe_normalize(y, glm.vec3(0, 1, 0))
    z = _safe_normalize(z, glm.vec3(0, 0, 1))
    m00, m01, m02 = x.x, y.x, z.x
    m10, m11, m12 = x.y, y.y, z.y
    m20, m21, m22 = x.z, y.z, z.z
    tr = m00 + m11 + m22
    if tr > 0.0:
        s = math.sqrt(tr + 1.0) * 2.0
        w = 0.25 * s
        qx = (m21 - m12) / s
        qy = (m02 - m20) / s
        qz = (m10 - m01) / s
    elif m00 > m11 and m00 > m22:
        s = math.sqrt(max(1.0 + m00 - m11 - m22, 1e-12)) * 2.0
        w = (m21 - m12) / s
        qx = 0.25 * s
        qy = (m01 + m10) / s
        qz = (m02 + m20) / s
    elif m11 > m22:
        s = math.sqrt(max(1.0 + m11 - m00 - m22, 1e-12)) * 2.0
        w = (m02 - m20) / s
        qx = (m01 + m10) / s
        qy = 0.25 * s
        qz = (m12 + m21) / s
    else:
        s = math.sqrt(max(1.0 + m22 - m00 - m11, 1e-12)) * 2.0
        w = (m10 - m01) / s
        qx = (m02 + m20) / s
        qy = (m12 + m21) / s
        qz = 0.25 * s
    return glm.normalize(glm.quat(w, qx, qy, qz))


def _world_scale(node: "SceneNode") -> glm.vec3:
    """World scale of a node = length of its world-matrix basis columns."""
    m = node.world_matrix()
    return glm.vec3(glm.length(glm.vec3(m[0])),
                    glm.length(glm.vec3(m[1])),
                    glm.length(glm.vec3(m[2])))


def _world_quat(node: "SceneNode") -> glm.quat:
    """World rotation of a node as a quaternion (scale/mirror stripped)."""
    m = node.world_matrix()
    x = _safe_normalize(glm.vec3(m[0]), glm.vec3(1, 0, 0))
    y = _safe_normalize(glm.vec3(m[1]), glm.vec3(0, 1, 0))
    z = _safe_normalize(glm.vec3(m[2]), glm.vec3(0, 0, 1))
    return _basis_to_quat(x, y, z)


def _write_node_world_position(node: "SceneNode", world_pos: glm.vec3):
    """Set a node's LOCAL position so its world position == world_pos,
    accounting for any parent transform."""
    if node.parent is not None:
        try:
            inv = glm.inverse(node.parent.world_matrix())
            node.position = glm.vec3(inv * glm.vec4(world_pos, 1.0))
            return
        except Exception:
            pass
    node.position = glm.vec3(world_pos)


def _write_node_world_transform(node: "SceneNode", world_pos: glm.vec3,
                                world_rot: glm.quat):
    """Set a node's local TRS so its world pose == (world_pos, world_rot).
    Scale is preserved; rotation is converted back to the node's Euler
    convention through the parent's inverse."""
    _write_node_world_position(node, world_pos)
    if node.parent is not None:
        try:
            pw = glm.inverse(node.parent.world_matrix())
            local_m = pw * (glm.translate(glm.mat4(1.0), world_pos)
                            * glm.mat4_cast(world_rot))
            lx = _safe_normalize(glm.vec3(local_m[0]), glm.vec3(1, 0, 0))
            ly = _safe_normalize(glm.vec3(local_m[1]), glm.vec3(0, 1, 0))
            lz = _safe_normalize(glm.vec3(local_m[2]), glm.vec3(0, 0, 1))
            node.rotation_euler = _quat_to_euler(_basis_to_quat(lx, ly, lz))
            return
        except Exception:
            pass
    node.rotation_euler = _quat_to_euler(world_rot)


# ------------------------------------------------------------- contacts

class Contact:
    """One contact point between two physics entries.
    `normal` always points from entry B toward entry A (push A out along
    +normal, push B out along -normal)."""
    __slots__ = ("a", "b", "point", "normal", "depth",
                 "jn", "jt0", "jt1", "restitution", "friction")

    def __init__(self, a, b, point, normal, depth):
        self.a = a
        self.b = b
        self.point = glm.vec3(point)
        self.normal = _safe_normalize(normal)
        self.depth = float(depth)
        self.jn = 0.0          # accumulated normal impulse (this step)
        self.jt0 = 0.0         # accumulated friction impulses
        self.jt1 = 0.0
        self.restitution = 0.0
        self.friction = 0.6


class RayHit:
    """Result of PhysicsWorld.raycast."""
    __slots__ = ("node", "collider", "point", "normal", "distance")

    def __init__(self, node, collider, point, normal, distance):
        self.node = node
        self.collider = collider
        self.point = glm.vec3(point)
        self.normal = glm.vec3(normal)
        self.distance = float(distance)

    def __repr__(self):
        n = self.node.name if self.node is not None else "?"
        return f"<RayHit {n} @ {self.distance:.2f}>"


# ------------------------------------------------------------- colliders

class Collider:
    """Base collision shape attached to a SceneNode (node.collider).
    A collider WITHOUT a RigidBody is static level geometry; add a
    RigidBody to make it dynamic or kinematic."""
    TYPE = "base"

    def __init__(self, center=(0.0, 0.0, 0.0), is_trigger: bool = False,
                 friction: float = 0.6, restitution: float = 0.0):
        self.center = glm.vec3(float(center[0]), float(center[1]),
                               float(center[2]))
        self.is_trigger = bool(is_trigger)
        self.friction = _clamp(float(friction), 0.0, 4.0)
        self.restitution = _clamp(float(restitution), 0.0, 1.0)

    def _base_dict(self) -> dict:
        return {"center": [float(self.center.x), float(self.center.y),
                           float(self.center.z)],
                "is_trigger": self.is_trigger,
                "friction": float(self.friction),
                "restitution": float(self.restitution)}

    def to_dict(self) -> dict:
        d = self._base_dict()
        d["type"] = self.TYPE
        return d

    @staticmethod
    def from_dict(d: dict) -> Optional["Collider"]:
        if not isinstance(d, dict):
            return None
        t = d.get("type")
        base = {"center": d.get("center", [0, 0, 0]),
                "is_trigger": d.get("is_trigger", False),
                "friction": d.get("friction", 0.6),
                "restitution": d.get("restitution", 0.0)}
        try:
            if t == "box":
                return BoxCollider(size=d.get("size", [1, 1, 1]), **base)
            if t == "sphere":
                return SphereCollider(radius=d.get("radius", 0.5), **base)
            if t == "capsule":
                return CapsuleCollider(radius=d.get("radius", 0.4),
                                       height=d.get("height", 1.8), **base)
            if t == "plane":
                return PlaneCollider(size=d.get("size", 0.0), **base)
            if t == "mesh":
                # Triangles are NOT serialised — they are re-sourced from
                # the node's mesh (source_path / primitive) at build time.
                return MeshCollider(**base)
        except Exception as ex:
            Log.warn(f"Collider restore failed ({t}): {ex}")
        return None


class BoxCollider(Collider):
    TYPE = "box"

    def __init__(self, size=(1.0, 1.0, 1.0), **kw):
        super().__init__(**kw)
        self.size = glm.vec3(max(1e-4, float(size[0])),
                             max(1e-4, float(size[1])),
                             max(1e-4, float(size[2])))

    def to_dict(self):
        d = super().to_dict()
        d["size"] = [float(self.size.x), float(self.size.y), float(self.size.z)]
        return d


class SphereCollider(Collider):
    TYPE = "sphere"

    def __init__(self, radius: float = 0.5, **kw):
        super().__init__(**kw)
        self.radius = max(1e-4, float(radius))

    def to_dict(self):
        d = super().to_dict()
        d["radius"] = float(self.radius)
        return d


class CapsuleCollider(Collider):
    """Vertical (Y-axis) capsule: total height includes the two caps."""
    TYPE = "capsule"

    def __init__(self, radius: float = 0.4, height: float = 1.8, **kw):
        super().__init__(**kw)
        self.radius = max(1e-4, float(radius))
        self.height = max(self.radius * 2.0, float(height))

    def to_dict(self):
        d = super().to_dict()
        d["radius"] = float(self.radius)
        d["height"] = float(self.height)
        return d


class PlaneCollider(Collider):
    """Static ground plane through the node's position, normal = node's
    rotated +Y.  size <= 0 means infinite; otherwise a bounded square of
    `size` world units centred on the node."""
    TYPE = "plane"

    def __init__(self, size: float = 0.0, **kw):
        super().__init__(**kw)
        self.size = float(size)

    def to_dict(self):
        d = super().to_dict()
        d["size"] = float(self.size)
        return d


class HeightfieldCollider(Collider):
    """Terrain heightfield built from a terrain_config dict — the same
    deterministic noise the Terrain mesh uses, so the collision surface
    matches the rendered terrain exactly."""
    TYPE = "heightfield"

    def __init__(self, size: float = 200.0, resolution: int = 128,
                 height_scale: float = 10.0, seed: int = 42, **kw):
        super().__init__(**kw)
        self.size = float(size)
        self.resolution = int(resolution)
        self.height_scale = float(height_scale)
        self.seed = int(seed)
        self.heights = _heightmap_noise(self.resolution, self.seed,
                                        self.height_scale)
        self._cell = self.size / max(1, self.resolution)

    def height_at_local(self, lx: float, lz: float) -> float:
        """Bilinear height at local XZ (origin at -size/2 corner)."""
        n = self.resolution
        gx = _clamp((lx + self.size * 0.5) / self._cell, 0.0, n - 1e-4)
        gz = _clamp((lz + self.size * 0.5) / self._cell, 0.0, n - 1e-4)
        x0, z0 = int(gx), int(gz)
        x1, z1 = min(x0 + 1, n), min(z0 + 1, n)
        fx, fz = gx - x0, gz - z0
        h = self.heights
        h00 = float(h[z0, x0]); h10 = float(h[z0, x1])
        h01 = float(h[z1, x0]); h11 = float(h[z1, x1])
        return (h00 * (1 - fx) + h10 * fx) * (1 - fz) + \
               (h01 * (1 - fx) + h11 * fx) * fz

    def inside_local(self, lx: float, lz: float) -> bool:
        h = self.size * 0.5
        return -h <= lx <= h and -h <= lz <= h


class MeshCollider(Collider):
    """Triangle-mesh collider: collides with the EXACT shape of the node's
    own mesh — the right tool for low-poly level geometry such as buildings
    with interiors, stairs, arches and props where a box/capsule would
    block doorways or float over detail.

    How it works:
      * Triangles are taken from the node's Mesh (cpu_positions/cpu_indices,
        captured at upload), transformed to WORLD space and organised into
        an AABB BVH.  Because the BVH is world-space, non-uniform node
        scale is handled exactly.
      * The BVH is cached on this collider and only rebuilt when the node's
        world matrix or the mesh changes — static level geometry pays the
        build cost exactly once.
      * Double-sided: contacts push objects to whichever side of a triangle
        they are on, so interiors work out of the box.

    Intended for STATIC (and kinematic) level geometry.  A dynamic
    RigidBody on a MeshCollider falls back to the mesh's bounding sphere
    (exact per-triangle collision for a moving triangle soup needs a
    convex decomposition, which this engine deliberately avoids).  Keep
    meshes low-poly: a warning is logged past 20k triangles.
    """
    TYPE = "mesh"
    TRI_WARN_THRESHOLD = 20000

    def __init__(self, **kw):
        super().__init__(**kw)
        # World-BVH cache: {"key": ..., "shape": ...} — see _mesh_shape_view.
        self._mesh_cache: Optional[dict] = None
        # Local-space bounding sphere of the source mesh (used by the
        # dynamic-body degrade path); filled in at registration.
        self._bound_local: Optional[Tuple[glm.vec3, float]] = None
        self._warned_no_mesh = False
        self._warned_tris = False


class _TriMeshBVH:
    """Static AABB BVH over a world-space triangle soup (numpy internals).

    Built once per (mesh, world-transform) and queried for:
      * query_aabb(lo, hi)  → indices of triangles whose AABB overlaps
      * raycast(ro, rd, t)  → (t, tri_index) of the closest double-sided hit

    Nodes are stored as flat numpy arrays; leaves hold up to LEAF triangles
    in the `packed` index map.  Pure CPU, no GL.
    """

    LEAF = 4

    __slots__ = ("tris", "normals", "tri_lo", "tri_hi", "packed",
                 "node_lo", "node_hi", "left", "right", "start", "count",
                 "node_count")

    def __init__(self, tris: np.ndarray):
        tris = np.ascontiguousarray(tris, dtype=np.float32).reshape(-1, 3, 3)
        self.tris = tris
        n = len(tris)
        v0 = tris[:, 0]
        v1 = tris[:, 1]
        v2 = tris[:, 2]
        nrm = np.cross(v1 - v0, v2 - v0)
        ln = np.linalg.norm(nrm, axis=1, keepdims=True)
        ln[ln < 1e-12] = 1.0
        self.normals = (nrm / ln).astype(np.float32)
        self.tri_lo = np.minimum(np.minimum(v0, v1), v2)
        self.tri_hi = np.maximum(np.maximum(v0, v1), v2)
        cent = (v0 + v1 + v2) * (1.0 / 3.0)

        node_lo: List[np.ndarray] = []
        node_hi: List[np.ndarray] = []
        left: List[int] = []
        right: List[int] = []
        start: List[int] = []
        count: List[int] = []
        packed: List[int] = []

        def build(idxs: np.ndarray) -> int:
            ni = len(node_lo)
            node_lo.append(None); node_hi.append(None)
            left.append(-1); right.append(-1)
            start.append(-1); count.append(0)
            lo = self.tri_lo[idxs].min(axis=0)
            hi = self.tri_hi[idxs].max(axis=0)
            node_lo[ni] = lo
            node_hi[ni] = hi
            if len(idxs) <= self.LEAF:
                start[ni] = len(packed)
                count[ni] = len(idxs)
                packed.extend(int(i) for i in idxs)
                return ni
            ext = hi - lo
            axis = int(np.argmax(ext))
            srt = idxs[np.argsort(cent[idxs, axis], kind="quicksort")]
            mid = len(srt) // 2
            left[ni] = build(srt[:mid])
            right[ni] = build(srt[mid:])
            return ni

        if n > 0:
            build(np.arange(n))
        self.packed = np.asarray(packed, dtype=np.int32)
        self.node_lo = (np.stack(node_lo) if node_lo
                        else np.zeros((0, 3), np.float32))
        self.node_hi = (np.stack(node_hi) if node_hi
                        else np.zeros((0, 3), np.float32))
        self.left = np.asarray(left, dtype=np.int32)
        self.right = np.asarray(right, dtype=np.int32)
        self.start = np.asarray(start, dtype=np.int32)
        self.count = np.asarray(count, dtype=np.int32)
        self.node_count = len(left)

    # ------------------------------------------------------------ queries
    def query_aabb(self, lo, hi, out: Optional[List[int]] = None) -> List[int]:
        """Indices of triangles whose AABB overlaps [lo, hi]."""
        if out is None:
            out = []
        if self.node_count == 0:
            return out
        lo = np.asarray([float(lo[0]), float(lo[1]), float(lo[2])])
        hi = np.asarray([float(hi[0]), float(hi[1]), float(hi[2])])
        stack = [0]
        while stack:
            ni = stack.pop()
            if np.any(lo > self.node_hi[ni]) or np.any(hi < self.node_lo[ni]):
                continue
            if self.start[ni] >= 0:          # leaf
                s = int(self.start[ni])
                for ti in self.packed[s:s + int(self.count[ni])]:
                    t = int(ti)
                    if not (np.any(lo > self.tri_hi[t])
                            or np.any(hi < self.tri_lo[t])):
                        out.append(t)
            else:
                stack.append(int(self.left[ni]))
                stack.append(int(self.right[ni]))
        return out

    def raycast(self, ro, rd, max_t: float) -> Optional[Tuple[float, int]]:
        """Closest double-sided ray/triangle hit; returns (t, tri_index)."""
        if self.node_count == 0:
            return None
        ro = (float(ro[0]), float(ro[1]), float(ro[2]))
        rd = (float(rd[0]), float(rd[1]), float(rd[2]))
        best_t = float(max_t)
        best_tri = -1
        stack = [0]
        while stack:
            ni = stack.pop()
            if not _ray_aabb_t(ro, rd, self.node_lo[ni], self.node_hi[ni],
                               best_t):
                continue
            if self.start[ni] >= 0:
                s = int(self.start[ni])
                for ti in self.packed[s:s + int(self.count[ni])]:
                    t = _ray_triangle_t(ro, rd, self.tris[int(ti)], best_t)
                    if t is not None and t < best_t:
                        best_t = t
                        best_tri = int(ti)
            else:
                stack.append(int(self.left[ni]))
                stack.append(int(self.right[ni]))
        if best_tri < 0:
            return None
        return best_t, best_tri


def _ray_aabb_t(ro, rd, lo, hi, max_t: float) -> bool:
    """Slab test: does the ray hit AABB [lo, hi] within max_t?"""
    t0, t1 = 0.0, float(max_t)
    for i in range(3):
        o = ro[i]; d = rd[i]
        l = float(lo[i]); h = float(hi[i])
        if abs(d) < 1e-12:
            if o < l or o > h:
                return False
        else:
            inv = 1.0 / d
            a = (l - o) * inv
            b = (h - o) * inv
            if a > b:
                a, b = b, a
            if a > t0:
                t0 = a
            if b < t1:
                t1 = b
            if t0 > t1:
                return False
    return True


def _ray_triangle_t(ro, rd, tri, max_t: float) -> Optional[float]:
    """Möller–Trumbore, double-sided.  Returns t or None."""
    v0 = tri[0]
    e1x = float(tri[1][0] - v0[0]); e1y = float(tri[1][1] - v0[1])
    e1z = float(tri[1][2] - v0[2])
    e2x = float(tri[2][0] - v0[0]); e2y = float(tri[2][1] - v0[1])
    e2z = float(tri[2][2] - v0[2])
    # p = rd x e2
    px = rd[1] * e2z - rd[2] * e2y
    py = rd[2] * e2x - rd[0] * e2z
    pz = rd[0] * e2y - rd[1] * e2x
    det = e1x * px + e1y * py + e1z * pz
    if -1e-12 < det < 1e-12:
        return None
    inv = 1.0 / det
    tx = ro[0] - float(v0[0]); ty = ro[1] - float(v0[1])
    tz = ro[2] - float(v0[2])
    u = (tx * px + ty * py + tz * pz) * inv
    if u < -1e-6 or u > 1.000001:
        return None
    # q = tvec x e1
    qx = ty * e1z - tz * e1y
    qy = tz * e1x - tx * e1z
    qz = tx * e1y - ty * e1x
    v = (rd[0] * qx + rd[1] * qy + rd[2] * qz) * inv
    if v < -1e-6 or u + v > 1.000001:
        return None
    t = (e2x * qx + e2y * qy + e2z * qz) * inv
    if t < 1e-6 or t > max_t:
        return None
    return t


# ------------------------------------------------------------- rigid body

class RigidBody:
    """Dynamic/kinematic body parameters + runtime state, attached to a
    SceneNode (node.rigidbody).  Requires a Collider on the same node.

        body_type       "dynamic" | "kinematic" | "static"
        mass            kg (dynamic only)
        friction/restitution override the collider's when set (None = use
                        collider's)
        linear_damping  velocity bleed per second (0..1)
        angular_damping same for spin
        gravity_scale   multiplier on world gravity
        freeze_rotation keep the node's orientation fixed (FPS props)
    """

    def __init__(self, mass: float = 1.0, body_type: str = "dynamic",
                 friction: Optional[float] = None,
                 restitution: Optional[float] = None,
                 linear_damping: float = 0.02,
                 angular_damping: float = 0.05,
                 gravity_scale: float = 1.0,
                 freeze_rotation: bool = False):
        self.body_type = body_type if body_type in ("dynamic", "kinematic",
                                                    "static") else "dynamic"
        self.mass = max(1e-4, float(mass))
        self.friction = friction
        self.restitution = restitution
        self.linear_damping = _clamp(float(linear_damping), 0.0, 1.0)
        self.angular_damping = _clamp(float(angular_damping), 0.0, 1.0)
        self.gravity_scale = float(gravity_scale)
        self.freeze_rotation = bool(freeze_rotation)
        # ---- runtime state (reset by PhysicsWorld.rebuild) ----
        self.velocity = glm.vec3(0.0)
        self.angular_velocity = glm.vec3(0.0)
        self.position = glm.vec3(0.0)          # world, authoritative
        self.orientation = glm.quat(1.0, 0.0, 0.0, 0.0)
        self._force = glm.vec3(0.0)
        self._inv_inertia_local = glm.vec3(0.0)

    @property
    def inv_mass(self) -> float:
        return 0.0 if self.body_type != "dynamic" else 1.0 / self.mass

    def add_force(self, f: glm.vec3):
        self._force += _v3(f)
        # [TRIPLEA:rigidbody_wake_hooks] force/impulse wakes the island
        w = getattr(self, "_world", None)
        if w is not None:
            w.wake_body(self)

    def add_impulse(self, j: glm.vec3):
        if self.inv_mass > 0.0:
            self.velocity += _v3(j) * self.inv_mass
        w = getattr(self, "_world", None)
        if w is not None:
            w.wake_body(self)

    def add_impulse_at_point(self, j: glm.vec3, point: glm.vec3):
        if self.inv_mass > 0.0:
            self.velocity += _v3(j) * self.inv_mass
            if not self.freeze_rotation:
                r = _v3(point) - self.position
                self.angular_velocity += self._world_inv_inertia() * glm.cross(r, _v3(j))
        w = getattr(self, "_world", None)
        if w is not None:
            w.wake_body(self)

    def _world_inv_inertia(self) -> glm.mat3:
        if self.freeze_rotation:
            return glm.mat3(0.0)
        R = glm.mat3_cast(self.orientation)
        I = self._inv_inertia_local
        return R * glm.mat3(I.x, 0, 0, 0, I.y, 0, 0, 0, I.z) * glm.transpose(R)

    def to_dict(self) -> dict:
        return {"mass": self.mass, "body_type": self.body_type,
                "friction": self.friction, "restitution": self.restitution,
                "linear_damping": self.linear_damping,
                "angular_damping": self.angular_damping,
                "gravity_scale": self.gravity_scale,
                "freeze_rotation": self.freeze_rotation}

    @classmethod
    def from_dict(cls, d: dict) -> "RigidBody":
        return cls(mass=d.get("mass", 1.0),
                   body_type=d.get("body_type", "dynamic"),
                   friction=d.get("friction"),
                   restitution=d.get("restitution"),
                   linear_damping=d.get("linear_damping", 0.02),
                   angular_damping=d.get("angular_damping", 0.05),
                   gravity_scale=d.get("gravity_scale", 1.0),
                   freeze_rotation=d.get("freeze_rotation", False))


# ------------------------------------------------------------- shape views
# A "shape view" is the world-space snapshot of a collider used by the
# narrow phase.  Plain dicts keep this readable and cheap enough at the
# body counts of a CPU engine.

def _vabs(v: glm.vec3) -> glm.vec3:
    return glm.vec3(abs(v.x), abs(v.y), abs(v.z))


def _collider_sig(col):
    """Values that define a primitive collider's local shape."""
    c = col.center
    sz = getattr(col, "size", None)
    if sz is not None and not isinstance(sz, (int, float)):
        sz = (float(sz.x), float(sz.y), float(sz.z))
    return (float(c.x), float(c.y), float(c.z), getattr(col, "radius", None),
            getattr(col, "height", None), sz)


def _shape_from_node_collider(node: "SceneNode", col: Collider) -> dict:
    """World shape of a collider attached to a (possibly static) node,
    computed from the node's CURRENT world transform.

    Primitive shapes are cached on the collider and reused while the node's
    world matrix and the collider's own values are unchanged (every
    character move used to rebuild every collider's shape from scratch).
    Shape views are read-only by contract, so sharing them is safe."""
    if isinstance(col, (HeightfieldCollider, MeshCollider)):
        return _shape_from_node_collider_uncached(node, col)
    m = node.world_matrix()
    try:
        sig = _collider_sig(col)
        c = col.__dict__.get("_h3d_sv")
        if c is not None and c[2] == sig and c[1] == m:
            return c[3]                   # primitives depend on m + values only
        sv = _shape_from_node_collider_uncached(node, col, m)
        col.__dict__["_h3d_sv"] = (None, _GLM_MAT4(m), sig, sv)
        return sv
    except Exception:
        return _shape_from_node_collider_uncached(node, col)


def _shape_from_node_collider_uncached(node: "SceneNode", col: Collider,
                                       m=None) -> dict:
    if m is None:
        m = node.world_matrix()
    ws = _world_scale(node)
    if isinstance(col, SphereCollider):
        c = glm.vec3(m * glm.vec4(col.center, 1.0))
        r = col.radius * max(ws.x, max(ws.y, ws.z))
        return {"kind": "sphere", "c": c, "r": max(1e-4, r)}
    if isinstance(col, BoxCollider):
        q = _world_quat(node)
        c = glm.vec3(m * glm.vec4(col.center, 1.0))
        h = glm.vec3(col.size.x * ws.x, col.size.y * ws.y,
                     col.size.z * ws.z) * 0.5
        return {"kind": "box", "c": c, "q": q,
                "e": (q * glm.vec3(1, 0, 0), q * glm.vec3(0, 1, 0),
                      q * glm.vec3(0, 0, 1)),
                "h": h}
    if isinstance(col, CapsuleCollider):
        q = _world_quat(node)
        c = glm.vec3(m * glm.vec4(col.center, 1.0))
        seg = max(0.0, col.height * 0.5 - col.radius) * ws.y
        axis = q * glm.vec3(0, 1, 0)
        r = col.radius * max(ws.x, ws.z)
        return {"kind": "capsule", "p0": c - axis * seg,
                "p1": c + axis * seg, "r": max(1e-4, r)}
    if isinstance(col, PlaneCollider):
        q = _world_quat(node)
        n = _safe_normalize(q * glm.vec3(0, 1, 0))
        p = glm.vec3(m * glm.vec4(col.center, 1.0))
        half = col.size * 0.5 if col.size > 0.0 else 0.0
        return {"kind": "plane", "p": p, "n": n,
                "u": q * glm.vec3(1, 0, 0), "v": q * glm.vec3(0, 0, 1),
                "hx": half * max(ws.x, 1e-4), "hz": half * max(ws.z, 1e-4),
                "bounded": col.size > 0.0}
    if isinstance(col, HeightfieldCollider):
        return {"kind": "heightfield", "node": node, "hf": col}
    if isinstance(col, MeshCollider):
        return _mesh_shape_view(node, col)
    return {"kind": "none"}


def _mesh_cpu_triangles(mesh: "Mesh") -> Tuple[Optional[np.ndarray],
                                               Optional[np.ndarray]]:
    """(positions, indices) of a mesh's CPU triangles, or (None, None).
    Primary source is the cpu_* copies kept by Mesh.upload; as a fallback
    for meshes that predate them, non-indexed positions are recovered from
    the interleaved VBO bytes (indices would be unrecoverable — in that
    case we honestly report no data instead of colliding with garbage)."""
    pos = getattr(mesh, "cpu_positions", None)
    idx = getattr(mesh, "cpu_indices", None)
    if pos is None or len(pos) < 3:
        vd = getattr(mesh, "_vertex_data", None)
        if vd is not None and not getattr(mesh, "indexed", False):
            flat = np.frombuffer(vd, dtype=np.float32)
            if flat.size and flat.size % 12 == 0:
                pos = flat.reshape(-1, 12)[:, 0:3]
                idx = None
    if pos is None or len(pos) < 3:
        return None, None
    if idx is None or len(idx) < 3:
        idx = np.arange(len(pos), dtype=np.uint32)
    return np.asarray(pos, dtype=np.float32), np.asarray(idx, dtype=np.uint32)


def _mesh_shape_view(node: "SceneNode", col: MeshCollider) -> dict:
    """World-space triangle-BVH shape view for a MeshCollider, cached on
    the collider.  Rebuilt ONLY when the node's world matrix, the mesh
    identity/content or the collider centre changes — static level
    geometry builds exactly once, then every query is a dict lookup."""
    mesh = node.mesh
    if mesh is None:
        # Generated geometry (a Spline Roads road, a package's terrain...)
        # gets its mesh a moment after the project loads, so only warn when
        # the mesh is STILL missing a couple of seconds later; warning on the
        # first query told users a perfectly working road "will not collide".
        now = time.perf_counter()
        since = getattr(col, "_h3d_no_mesh_since", None)
        if since is None:
            col._h3d_no_mesh_since = now
        elif not col._warned_no_mesh and now - since > 2.0:
            Log.warn(f"MeshCollider on '{node.name}' has no mesh — "
                     f"it will not collide with anything.")
            col._warned_no_mesh = True
        return {"kind": "none"}
    col._h3d_no_mesh_since = None
    positions, indices = _mesh_cpu_triangles(mesh)
    if positions is None:
        if not col._warned_no_mesh:
            Log.warn(f"MeshCollider on '{node.name}': mesh has no CPU "
                     f"triangle data — cannot build triangle collision.")
            col._warned_no_mesh = True
        return {"kind": "none"}

    m = node.world_matrix()
    key = (id(mesh), int(mesh.vertex_count), int(mesh.index_count),
           id(getattr(mesh, "_vertex_data", None)),
           tuple(round(float(m[c][r]), 6) for c in range(4) for r in range(4)),
           round(float(col.center.x), 6), round(float(col.center.y), 6),
           round(float(col.center.z), 6))
    cache = col._mesh_cache
    if cache is not None and cache["key"] == key:
        return cache["shape"]

    # ---- (re)build: gather local triangles, transform to world ----------
    tris_local = positions[indices.reshape(-1, 3)]            # (N,3,3)
    n_tris = len(tris_local)
    if n_tris == 0:
        return {"kind": "none"}
    if n_tris > MeshCollider.TRI_WARN_THRESHOLD and not col._warned_tris:
        Log.warn(f"MeshCollider on '{node.name}': {n_tris} triangles — "
                 f"mesh colliders are meant for LOW-POLY geometry; "
                 f"consider splitting or simplifying this mesh.")
        col._warned_tris = True
    # collider centre (local-space offset, like every other collider)
    tris64 = tris_local.astype(np.float64)
    tris64 += np.asarray([float(col.center.x), float(col.center.y),
                          float(col.center.z)], dtype=np.float64)
    M3 = np.asarray([[float(m[c][r]) for r in range(3)] for c in range(3)],
                    dtype=np.float64)          # rows = basis columns
    t = np.asarray([float(m[3][0]), float(m[3][1]), float(m[3][2])],
                   dtype=np.float64)
    # M3's rows are the basis vectors, so a row vector p maps to p @ M3
    # (p @ M3.T applied the inverse rotation: a mesh collider turned 90
    # degrees collided mirrored)
    tris_world = (tris64 @ M3 + t).astype(np.float32)
    # drop degenerate (zero-area) triangles — they carry no collision
    e1 = tris_world[:, 1] - tris_world[:, 0]
    e2 = tris_world[:, 2] - tris_world[:, 0]
    keep = np.linalg.norm(np.cross(e1, e2), axis=1) > 1e-10
    tris_world = tris_world[keep]
    if len(tris_world) == 0:
        return {"kind": "none"}
    bvh = _TriMeshBVH(tris_world)
    shape = {"kind": "mesh", "bvh": bvh, "node": node}
    col._mesh_cache = {"key": key, "shape": shape}
    # Local bound for the dynamic degrade path (bounding sphere of the
    # RAW mesh — the collider centre shifts it, so fold it in here).
    bs = getattr(mesh, "bounding_sphere", None)
    if bs is not None:
        col._bound_local = (glm.vec3(bs[0]) + col.center, float(bs[1]))
    else:
        lo = tris_world.min(axis=(0, 1))
        hi = tris_world.max(axis=(0, 1))
        col._bound_local = (glm.vec3(0.0),
                            float(np.linalg.norm(hi - lo) * 0.5))
    return shape


def _shape_from_body(col: Collider, body: RigidBody, scale: glm.vec3) -> dict:
    """World shape of a DYNAMIC body's collider, from the body's own
    position/orientation state (authoritative during the step)."""
    q = body.orientation
    c = body.position + q * (col.center * scale)
    if isinstance(col, SphereCollider):
        return {"kind": "sphere", "c": c,
                "r": max(1e-4, col.radius * max(scale.x, max(scale.y, scale.z)))}
    if isinstance(col, BoxCollider):
        return {"kind": "box", "c": c, "q": q,
                "e": (q * glm.vec3(1, 0, 0), q * glm.vec3(0, 1, 0),
                      q * glm.vec3(0, 0, 1)),
                "h": glm.vec3(col.size.x * scale.x, col.size.y * scale.y,
                              col.size.z * scale.z) * 0.5}
    if isinstance(col, CapsuleCollider):
        seg = max(0.0, col.height * 0.5 - col.radius) * scale.y
        axis = q * glm.vec3(0, 1, 0)
        return {"kind": "capsule", "p0": c - axis * seg, "p1": c + axis * seg,
                "r": max(1e-4, col.radius * max(scale.x, scale.z))}
    if isinstance(col, MeshCollider):
        # Exact triangle collision is static/kinematic-only (a moving
        # triangle soup needs convex decomposition).  Dynamic bodies with
        # a MeshCollider degrade gracefully to the mesh's bounding sphere.
        if col._bound_local is not None:
            bc, br = col._bound_local
        else:
            bc, br = glm.vec3(0.0), 0.5
        r = max(1e-4, br * max(scale.x, max(scale.y, scale.z)))
        return {"kind": "sphere", "c": body.position + q * (bc * scale),
                "r": r}
    # planes / heightfields are never dynamic; fall back to a small sphere
    return {"kind": "sphere", "c": c, "r": 0.25}


def _shape_aabb(s: dict) -> Tuple[glm.vec3, glm.vec3]:
    """Conservative world AABB of a shape view (broadphase + queries)."""
    k = s["kind"]
    if k == "sphere":
        r = glm.vec3(s["r"])
        return s["c"] - r, s["c"] + r
    if k == "box":
        e = s["e"]; h = s["h"]
        ax = _vabs(e[0]) * h.x + _vabs(e[1]) * h.y + _vabs(e[2]) * h.z
        return s["c"] - ax, s["c"] + ax
    if k == "capsule":
        r = glm.vec3(s["r"])
        lo = glm.min(s["p0"], s["p1"]) - r
        hi = glm.max(s["p0"], s["p1"]) + r
        return lo, hi
    if k == "plane":
        if s["bounded"]:
            ext = _vabs(s["u"]) * s["hx"] + _vabs(s["v"]) * s["hz"] \
                  + glm.vec3(0.05)
            return s["p"] - ext, s["p"] + ext
        big = 1.0e6
        return glm.vec3(-big), glm.vec3(big)
    if k == "heightfield":
        node = s["node"]; hf = s["hf"]
        ws = _world_scale(node)
        wp = node.world_position()
        half = hf.size * 0.5
        # Heights can be NEGATIVE (island seabeds go to -20 m and more): the
        # box used to stop 1 m below the origin, so the broad phase skipped
        # the terrain for anything deeper and players walking into the sea
        # fell through the world (raycasts under water missed it too).
        _h = hf.heights
        y_a = float(np.min(_h)) * ws.y
        y_b = float(np.max(_h)) * ws.y
        lo = glm.vec3(wp.x - half * ws.x, wp.y + min(y_a, y_b, 0.0) - 1.0,
                      wp.z - half * ws.z)
        hi = glm.vec3(wp.x + half * ws.x,
                      wp.y + max(y_a, y_b) + 1.0,
                      wp.z + half * ws.z)
        return lo, hi
    if k == "mesh":
        bvh = s["bvh"]
        if bvh.node_count == 0:
            return glm.vec3(0), glm.vec3(0)
        lo = bvh.node_lo[0]
        hi = bvh.node_hi[0]
        return (glm.vec3(float(lo[0]), float(lo[1]), float(lo[2])),
                glm.vec3(float(hi[0]), float(hi[1]), float(hi[2])))
    return glm.vec3(0), glm.vec3(0)


def _aabb_overlap(a0, a1, b0, b1) -> bool:
    return (a0.x <= b1.x and a1.x >= b0.x and
            a0.y <= b1.y and a1.y >= b0.y and
            a0.z <= b1.z and a1.z >= b0.z)


# ------------------------------------------------------------- small geo

def _closest_point_on_segment(p: glm.vec3, a: glm.vec3, b: glm.vec3) -> glm.vec3:
    ab = b - a
    d = glm.dot(ab, ab)
    if d < 1e-12:
        return glm.vec3(a)
    t = _clamp(glm.dot(p - a, ab) / d, 0.0, 1.0)
    return a + ab * t


def _segment_segment(p1, q1, p2, q2):
    """Closest points between segments p1-q1 and p2-q2 (Ericson 5.1.9).
    Returns (c1, c2)."""
    d1 = q1 - p1
    d2 = q2 - p2
    r = p1 - p2
    a = glm.dot(d1, d1)
    e = glm.dot(d2, d2)
    f = glm.dot(d2, r)
    if a <= 1e-12 and e <= 1e-12:
        return glm.vec3(p1), glm.vec3(p2)
    if a <= 1e-12:
        s = 0.0
        t = _clamp(f / e, 0.0, 1.0)
    else:
        c = glm.dot(d1, r)
        if e <= 1e-12:
            t = 0.0
            s = _clamp(-c / a, 0.0, 1.0)
        else:
            b = glm.dot(d1, d2)
            denom = a * e - b * b
            s = _clamp((b * f - c * e) / denom, 0.0, 1.0) if denom > 1e-12 else 0.0
            t = (b * s + f) / e
            if t < 0.0:
                t = 0.0
                s = _clamp(-c / a, 0.0, 1.0)
            elif t > 1.0:
                t = 1.0
                s = _clamp((b - c) / a, 0.0, 1.0)
    return p1 + d1 * s, p2 + d2 * t


def _closest_point_on_box(s: dict, p: glm.vec3) -> glm.vec3:
    """Closest point on an OBB to world point p."""
    d = p - s["c"]
    q = glm.vec3(s["c"])
    for i in range(3):
        dist = glm.dot(d, s["e"][i])
        hi = s["h"][i]
        q += s["e"][i] * _clamp(dist, -hi, hi)
    return q


def _point_in_box(s: dict, p: glm.vec3) -> bool:
    d = p - s["c"]
    for i in range(3):
        if abs(glm.dot(d, s["e"][i])) > s["h"][i]:
            return False
    return True


def _plane_point_inside(s: dict, p: glm.vec3) -> bool:
    """Bounded-plane containment check (always True for infinite planes)."""
    if not s["bounded"]:
        return True
    d = p - s["p"]
    return abs(glm.dot(d, s["u"])) <= s["hx"] and \
           abs(glm.dot(d, s["v"])) <= s["hz"]


# ------------------------------------------------------------- triangle geo

def _closest_point_on_triangle(p: glm.vec3, a: glm.vec3, b: glm.vec3,
                               c: glm.vec3) -> glm.vec3:
    """Closest point on triangle abc to point p (Ericson 5.1.5)."""
    ab = b - a
    ac = c - a
    ap = p - a
    d1 = glm.dot(ab, ap)
    d2 = glm.dot(ac, ap)
    if d1 <= 0.0 and d2 <= 0.0:
        return glm.vec3(a)
    bp = p - b
    d3 = glm.dot(ab, bp)
    d4 = glm.dot(ac, bp)
    if d3 >= 0.0 and d4 <= d3:
        return glm.vec3(b)
    vc = d1 * d4 - d3 * d2
    if vc <= 0.0 and d1 >= 0.0 and d3 <= 0.0:
        v = d1 / (d1 - d3)
        return a + ab * v
    cp = p - c
    d5 = glm.dot(ab, cp)
    d6 = glm.dot(ac, cp)
    if d6 >= 0.0 and d5 <= d6:
        return glm.vec3(c)
    vb = d5 * d2 - d1 * d6
    if vb <= 0.0 and d2 >= 0.0 and d6 <= 0.0:
        w = d2 / (d2 - d6)
        return a + ac * w
    va = d3 * d6 - d5 * d4
    if va <= 0.0 and (d4 - d3) >= 0.0 and (d5 - d6) >= 0.0:
        w = (d4 - d3) / ((d4 - d3) + (d5 - d6))
        return b + (c - b) * w
    denom = 1.0 / (va + vb + vc)
    v = vb * denom
    w = vc * denom
    return a + ab * v + ac * w


def _closest_segment_triangle(p0: glm.vec3, p1: glm.vec3, a: glm.vec3,
                              b: glm.vec3, c: glm.vec3):
    """Approximate closest points between segment p0-p1 and triangle abc.
    Returns (point_on_segment, point_on_triangle).  Samples the segment
    endpoints + midpoint against the triangle interior and runs exact
    segment/segment tests against the three triangle edges — the standard
    cheap alternative to the full Voronoi case analysis."""
    best_d2 = float("inf")
    best = (glm.vec3(p0), glm.vec3(a))

    def cand(ps: glm.vec3, pt: glm.vec3):
        nonlocal best_d2, best
        d = ps - pt
        d2 = glm.dot(d, d)
        if d2 < best_d2:
            best_d2 = d2
            best = (ps, pt)

    mid = (p0 + p1) * 0.5
    for q in (p0, p1, mid):
        cand(q, _closest_point_on_triangle(q, a, b, c))
    for (e0, e1) in ((a, b), (b, c), (c, a)):
        cs, ct = _segment_segment(p0, p1, e0, e1)
        cand(cs, ct)
    return best


def _tri_obb_mtv(a: glm.vec3, b: glm.vec3, c: glm.vec3, box: dict):
    """SAT for one triangle vs an OBB, returning the minimal translation
    that pushes the BOX out of the triangle: (normal_from_tri_to_box,
    depth) in WORLD space, or None when separated.

    Computed in the box's local frame (box = AABB [-h, h] at the origin),
    over 13 axes: 3 box axes, the triangle normal, and the 9 edge/box
    cross products."""
    e = box["e"]
    h = box["h"]
    # triangle verts into box-local frame
    def to_local(v):
        d = v - box["c"]
        return glm.vec3(glm.dot(d, e[0]), glm.dot(d, e[1]),
                        glm.dot(d, e[2]))
    ta, tb, tc = to_local(a), to_local(b), to_local(c)
    edges = (tb - ta, tc - tb, ta - tc)
    tri_n = glm.cross(tb - ta, tc - ta)
    nl = glm.length(tri_n)
    if nl < 1e-12:
        return None
    tri_n = tri_n / nl

    best_depth = float("inf")
    best_axis = None

    def test_axis(L: glm.vec3) -> bool:
        nonlocal best_depth, best_axis
        l = glm.length(L)
        if l < 1e-8:
            return True                      # degenerate axis: skip
        L = L / l
        ra = h.x * abs(L.x) + h.y * abs(L.y) + h.z * abs(L.z)
        p0 = glm.dot(ta, L)
        p1 = glm.dot(tb, L)
        p2 = glm.dot(tc, L)
        pmin = min(p0, p1, p2)
        pmax = max(p0, p1, p2)
        if pmin > ra or pmax < -ra:
            return False                     # separating axis found
        # minimal translation to separate: push triangle one way (box the
        # other).  +L separation needs (ra - pmin); -L needs (pmax + ra).
        push_pos = ra - pmin
        push_neg = pmax + ra
        if push_pos <= push_neg:
            depth = push_pos
            direction = -L                   # box moves opposite the tri
        else:
            depth = push_neg
            direction = L
        if depth < best_depth:
            best_depth = depth
            best_axis = direction
        return True

    for i in range(3):                       # box face axes
        axis = glm.vec3(0.0)
        axis[i] = 1.0
        if not test_axis(axis):
            return None
    if not test_axis(tri_n):                 # triangle face normal
        return None
    for edge in edges:                       # 9 edge cross products
        for i in range(3):
            axis = glm.vec3(0.0)
            axis[i] = 1.0
            if not test_axis(glm.cross(edge, axis)):
                return None
    if best_axis is None:
        return None
    n_world = _safe_normalize(e[0] * best_axis.x + e[1] * best_axis.y
                              + e[2] * best_axis.z)
    # orient strictly from the triangle toward the box centre
    tri_cent = (a + b + c) * (1.0 / 3.0)
    if glm.dot(n_world, box["c"] - tri_cent) < 0.0:
        n_world = -n_world
    return n_world, max(0.0, best_depth)


def _hf_xform(node: "SceneNode"):
    """(world position, world scale) of a heightfield node from ONE world
    matrix — the same values world_position() / _world_scale() return,
    without building the matrix twice per height sample."""
    m = node.world_matrix()
    ws = glm.vec3(glm.length(glm.vec3(m[0])),
                  glm.length(glm.vec3(m[1])),
                  glm.length(glm.vec3(m[2])))
    return glm.vec3(m[3]), ws


def _hf_local(s: dict, world_p: glm.vec3, _xf=None) -> Optional[Tuple[float, float, float]]:
    """World point -> (local_x, local_z, height_scale_y) for a heightfield,
    or None when outside the terrain bounds."""
    node = s["node"]; hf = s["hf"]
    wp, ws = _xf if _xf is not None else _hf_xform(node)
    if abs(ws.x) < 1e-8 or abs(ws.z) < 1e-8:
        return None
    lx = (world_p.x - wp.x) / ws.x
    lz = (world_p.z - wp.z) / ws.z
    if not hf.inside_local(lx, lz):
        return None
    return lx, lz, ws.y


def _hf_height_world(s: dict, world_x: float, world_z: float, _xf=None) -> Optional[float]:
    xf = _xf if _xf is not None else _hf_xform(s["node"])
    r = _hf_local(s, glm.vec3(world_x, 0.0, world_z), xf)
    if r is None:
        return None
    return xf[0].y + s["hf"].height_at_local(r[0], r[1]) * r[2]


def _hf_normal_world(s: dict, world_x: float, world_z: float, _xf=None) -> glm.vec3:
    """Surface normal from the heightfield gradient (world space)."""
    node = s["node"]; hf = s["hf"]
    wp, ws = _xf if _xf is not None else _hf_xform(node)
    eps = hf._cell * 0.75
    def h_at(x, z):
        lx = (x - wp.x) / max(ws.x, 1e-8)
        lz = (z - wp.z) / max(ws.z, 1e-8)
        lx = _clamp(lx, -hf.size * 0.5, hf.size * 0.5)
        lz = _clamp(lz, -hf.size * 0.5, hf.size * 0.5)
        return hf.height_at_local(lx, lz) * ws.y
    hx = (h_at(world_x + eps, world_z) - h_at(world_x - eps, world_z)) / (2 * eps)
    hz = (h_at(world_x, world_z + eps) - h_at(world_x, world_z - eps)) / (2 * eps)
    return _safe_normalize(glm.vec3(-hx, 1.0, -hz))


# ------------------------------------------------------------- contact gen
# Every generator returns a Contact (or None) with `normal` pointing FROM
# the second shape TO the first (from B -> A), matching Contact's contract.

def _contact_sphere_sphere(sa: dict, sb: dict):
    d = sa["c"] - sb["c"]
    dist = glm.length(d)
    rsum = sa["r"] + sb["r"]
    if dist >= rsum:
        return None
    if dist < 1e-9:
        n = glm.vec3(0, 1, 0)
        depth = rsum
    else:
        n = d / dist
        depth = rsum - dist
    point = sb["c"] + n * (sb["r"] - depth * 0.5)
    return (point, n, depth)


def _contact_sphere_box(sphere: dict, box: dict, sphere_is_a: bool):
    q = _closest_point_on_box(box, sphere["c"])
    d = sphere["c"] - q
    dist = glm.length(d)
    if dist >= sphere["r"]:
        return None
    if dist < 1e-9:
        # Sphere centre is inside the box: push out along the least-
        # penetration face axis.
        rel = sphere["c"] - box["c"]
        best_axis = glm.vec3(0, 1, 0)
        best_depth = float("inf")
        for i in range(3):
            proj = glm.dot(rel, box["e"][i])
            for sign in (1.0, -1.0):
                pen = box["h"][i] - proj * sign
                if 0.0 < pen < best_depth:
                    best_depth = pen
                    best_axis = box["e"][i] * sign
        if best_depth == float("inf"):
            best_axis = glm.vec3(0, 1, 0)
            best_depth = sphere["r"]
        n_box_to_sphere = best_axis
        depth = best_depth + sphere["r"]
        point = sphere["c"] - n_box_to_sphere * sphere["r"]
    else:
        n_box_to_sphere = d / dist
        depth = sphere["r"] - dist
        point = q
    n = n_box_to_sphere if sphere_is_a else -n_box_to_sphere
    return (point, n, depth)


def _contact_sphere_capsule(sphere: dict, cap: dict, sphere_is_a: bool):
    q = _closest_point_on_segment(sphere["c"], cap["p0"], cap["p1"])
    d = sphere["c"] - q
    dist = glm.length(d)
    rsum = sphere["r"] + cap["r"]
    if dist >= rsum:
        return None
    if dist < 1e-9:
        n_cap_to_sphere = glm.vec3(0, 1, 0)
        depth = rsum
    else:
        n_cap_to_sphere = d / dist
        depth = rsum - dist
    point = q + n_cap_to_sphere * (cap["r"] - depth * 0.5)
    n = n_cap_to_sphere if sphere_is_a else -n_cap_to_sphere
    return (point, n, depth)


def _contact_sphere_plane(sphere: dict, plane: dict, sphere_is_a: bool):
    dist = glm.dot(sphere["c"] - plane["p"], plane["n"])
    if dist >= sphere["r"]:
        return None
    q = sphere["c"] - plane["n"] * dist
    if not _plane_point_inside(plane, q):
        return None
    depth = sphere["r"] - dist
    point = q
    n = plane["n"] if sphere_is_a else -plane["n"]
    return (point, n, depth)


def _contact_sphere_heightfield(sphere: dict, hf: dict, sphere_is_a: bool, _xf=None):
    xf = _xf if _xf is not None else _hf_xform(hf["node"])
    h = _hf_height_world(hf, sphere["c"].x, sphere["c"].z, xf)
    if h is None:
        return None
    gap = sphere["c"].y - sphere["r"] - h
    if gap >= 0.0:
        return None
    n_hf = _hf_normal_world(hf, sphere["c"].x, sphere["c"].z, xf)
    depth = -gap
    point = glm.vec3(sphere["c"].x, h, sphere["c"].z)
    n = n_hf if sphere_is_a else -n_hf
    return (point, n, depth)


def _contact_box_plane(box: dict, plane: dict, box_is_a: bool):
    """Box corners vs plane: one contact per penetrating corner."""
    e = box["e"]; h = box["h"]; c = box["c"]
    out = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                corner = c + e[0] * (h.x * sx) + e[1] * (h.y * sy) \
                         + e[2] * (h.z * sz)
                dist = glm.dot(corner - plane["p"], plane["n"])
                if dist < 0.0 and _plane_point_inside(plane, corner):
                    n = plane["n"] if box_is_a else -plane["n"]
                    out.append((corner, n, -dist))
    return out if out else None


def _contact_box_heightfield(box: dict, hf: dict, box_is_a: bool):
    """Test the box's corners against the terrain surface."""
    e = box["e"]; h = box["h"]; c = box["c"]
    out = []
    for sx in (-1.0, 1.0):
        for sy in (-1.0, 1.0):
            for sz in (-1.0, 1.0):
                corner = c + e[0] * (h.x * sx) + e[1] * (h.y * sy) \
                         + e[2] * (h.z * sz)
                hh = _hf_height_world(hf, corner.x, corner.z)
                if hh is not None and corner.y < hh:
                    n_hf = _hf_normal_world(hf, corner.x, corner.z)
                    n = n_hf if box_is_a else -n_hf
                    out.append((glm.vec3(corner.x, hh, corner.z), n,
                                hh - corner.y))
    return out if out else None


def _sat_obb_obb(A: dict, B: dict):
    """Separating Axis Test for two OBBs (Ericson 4.4.1).
    Returns (normal_from_B_to_A, depth) of the least-penetrating axis,
    or None when separated."""
    ae = A["e"]; be = B["e"]; ah = A["h"]; bh = B["h"]
    R = [[glm.dot(ae[i], be[j]) for j in range(3)] for i in range(3)]
    tA = glm.vec3(*[glm.dot(B["c"] - A["c"], ae[i]) for i in range(3)])
    AbsR = [[abs(R[i][j]) + 1e-6 for j in range(3)] for i in range(3)]

    best_depth = float("inf")
    best_axis = None

    def _test(axis_world, ra, rb, dist):
        nonlocal best_depth, best_axis
        overlap = ra + rb - dist
        if overlap <= 0.0:
            return False
        if overlap < best_depth:
            best_depth = overlap
            best_axis = axis_world
        return True

    # A's faces
    for i in range(3):
        ra = ah[i]
        rb = bh.x * AbsR[i][0] + bh.y * AbsR[i][1] + bh.z * AbsR[i][2]
        if not _test(ae[i], ra, rb, abs(tA[i])):
            return None
    # B's faces
    tB = [glm.dot(B["c"] - A["c"], be[j]) for j in range(3)]
    for j in range(3):
        ra = ah.x * AbsR[0][j] + ah.y * AbsR[1][j] + ah.z * AbsR[2][j]
        rb = bh[j]
        if not _test(be[j], ra, rb, abs(tB[j])):
            return None
    # 9 edge-edge axes
    for i in range(3):
        for j in range(3):
            axis = glm.cross(ae[i], be[j])
            l = glm.length(axis)
            if l < 1e-6:
                continue
            axis = axis / l
            i1, i2 = (i + 1) % 3, (i + 2) % 3
            j1, j2 = (j + 1) % 3, (j + 2) % 3
            ra = ah[i1] * AbsR[i2][j] + ah[i2] * AbsR[i1][j]
            rb = bh[j1] * AbsR[i][j2] + bh[j2] * AbsR[i][j1]
            dist = abs(tA[i1] * R[i2][j] - tA[i2] * R[i1][j])
            if not _test(axis, ra, rb, dist):
                return None
    if best_axis is None:
        return None
    if glm.dot(A["c"] - B["c"], best_axis) < 0.0:
        best_axis = -best_axis
    return best_axis, best_depth


def _contact_box_box(A: dict, B: dict):
    """SAT detection + vertex-face contact points."""
    res = _sat_obb_obb(A, B)
    if res is None:
        return None
    n, depth = res
    points = []
    for (X, Y) in ((A, B), (B, A)):
        e = X["e"]; h = X["h"]; c = X["c"]
        for sx in (-1.0, 1.0):
            for sy in (-1.0, 1.0):
                for sz in (-1.0, 1.0):
                    v = c + e[0] * (h.x * sx) + e[1] * (h.y * sy) \
                        + e[2] * (h.z * sz)
                    if _point_in_box(Y, v):
                        points.append(v)
    if points:
        p = glm.vec3(0)
        for q in points:
            p += q
        p = p / float(len(points))
    else:
        p = (A["c"] + B["c"]) * 0.5
    return [(p, n, depth)]


def _capsule_sample_spheres(cap: dict) -> List[Tuple[glm.vec3, float]]:
    """Represent a capsule as 3 spheres (feet / mid / head) for box and
    heightfield contacts — robust and simple."""
    mid = (cap["p0"] + cap["p1"]) * 0.5
    return [(cap["p0"], cap["r"]), (mid, cap["r"]), (cap["p1"], cap["r"])]


def _contact_capsule_box(cap: dict, box: dict, cap_is_a: bool):
    best = None
    for (c, r) in _capsule_sample_spheres(cap):
        sph = {"kind": "sphere", "c": c, "r": r}
        res = _contact_sphere_box(sph, box, cap_is_a)
        if res is not None and (best is None or res[2] > best[2]):
            best = res
    return best


def _contact_capsule_capsule(A: dict, B: dict):
    c1, c2 = _segment_segment(A["p0"], A["p1"], B["p0"], B["p1"])
    d = c1 - c2
    dist = glm.length(d)
    rsum = A["r"] + B["r"]
    if dist >= rsum:
        return None
    if dist < 1e-9:
        n = glm.vec3(0, 1, 0)
        depth = rsum
    else:
        n = d / dist
        depth = rsum - dist
    point = (c1 + c2) * 0.5
    return (point, n, depth)


def _contact_capsule_plane(cap: dict, plane: dict, cap_is_a: bool):
    out = []
    for endpoint in (cap["p0"], cap["p1"]):
        dist = glm.dot(endpoint - plane["p"], plane["n"])
        if dist < cap["r"]:
            q = endpoint - plane["n"] * dist
            if _plane_point_inside(plane, q):
                n = plane["n"] if cap_is_a else -plane["n"]
                out.append((q, n, cap["r"] - dist))
    if not out:
        return None
    out.sort(key=lambda t: -t[2])
    return out[0]


def _contact_capsule_heightfield(cap: dict, hf: dict, cap_is_a: bool):
    best = None
    xf = _hf_xform(hf["node"])
    for (c, r) in _capsule_sample_spheres(cap):
        sph = {"kind": "sphere", "c": c, "r": r}
        res = _contact_sphere_heightfield(sph, hf, cap_is_a, xf)
        if res is not None and (best is None or res[2] > best[2]):
            best = res
    return best


# ---- triangle-mesh contacts (double-sided: the normal always pushes the ----
# ---- OTHER shape to whichever side of the triangle it is actually on,  ----
# ---- which is exactly what interiors need) -------------------------------

_MAX_MESH_CONTACTS = 4


def _mesh_candidates(bvh: _TriMeshBVH, lo: glm.vec3, hi: glm.vec3):
    return bvh.query_aabb(lo, hi)


def _contact_sphere_mesh(sphere: dict, mesh: dict, sphere_is_a: bool):
    bvh = mesh["bvh"]
    c = sphere["c"]
    r = sphere["r"]
    pad = glm.vec3(r)
    out = []
    for ti in _mesh_candidates(bvh, c - pad, c + pad):
        t = bvh.tris[ti]
        a = glm.vec3(float(t[0][0]), float(t[0][1]), float(t[0][2]))
        b = glm.vec3(float(t[1][0]), float(t[1][1]), float(t[1][2]))
        cc = glm.vec3(float(t[2][0]), float(t[2][1]), float(t[2][2]))
        q = _closest_point_on_triangle(c, a, b, cc)
        d = c - q
        dist = glm.length(d)
        if dist >= r:
            continue
        if dist < 1e-9:
            n = bvh.normals[ti]
            n = glm.vec3(float(n[0]), float(n[1]), float(n[2]))
            if glm.dot(n, c - a) < 0.0:
                n = -n
            depth = r
        else:
            n = d / dist
            depth = r - dist
        if not sphere_is_a:
            n = -n
        out.append((q, n, depth))
    if not out:
        return None
    out.sort(key=lambda x: -x[2])
    return out[:_MAX_MESH_CONTACTS]


def _contact_capsule_mesh(cap: dict, mesh: dict, cap_is_a: bool):
    bvh = mesh["bvh"]
    p0, p1, r = cap["p0"], cap["p1"], cap["r"]
    pad = glm.vec3(r)
    out = []
    for ti in _mesh_candidates(bvh, glm.min(p0, p1) - pad,
                               glm.max(p0, p1) + pad):
        t = bvh.tris[ti]
        a = glm.vec3(float(t[0][0]), float(t[0][1]), float(t[0][2]))
        b = glm.vec3(float(t[1][0]), float(t[1][1]), float(t[1][2]))
        cc = glm.vec3(float(t[2][0]), float(t[2][1]), float(t[2][2]))
        ps, pt = _closest_segment_triangle(p0, p1, a, b, cc)
        d = ps - pt
        dist = glm.length(d)
        if dist >= r:
            continue
        if dist < 1e-9:
            n = bvh.normals[ti]
            n = glm.vec3(float(n[0]), float(n[1]), float(n[2]))
            if glm.dot(n, ps - a) < 0.0:
                n = -n
            depth = r
        else:
            n = d / dist
            depth = r - dist
        if not cap_is_a:
            n = -n
        out.append((pt, n, depth))
    if not out:
        return None
    out.sort(key=lambda x: -x[2])
    return out[:_MAX_MESH_CONTACTS]


def _contact_box_mesh(box: dict, mesh: dict, box_is_a: bool):
    bvh = mesh["bvh"]
    blo, bhi = _shape_aabb(box)
    out = []
    for ti in _mesh_candidates(bvh, blo, bhi):
        t = bvh.tris[ti]
        a = glm.vec3(float(t[0][0]), float(t[0][1]), float(t[0][2]))
        b = glm.vec3(float(t[1][0]), float(t[1][1]), float(t[1][2]))
        cc = glm.vec3(float(t[2][0]), float(t[2][1]), float(t[2][2]))
        res = _tri_obb_mtv(a, b, cc, box)
        if res is None:
            continue
        n, depth = res
        # contact point: average of the triangle verts inside the box,
        # else the closest point on the triangle to the box centre
        pts = [v for v in (a, b, cc) if _point_in_box(box, v)]
        if pts:
            p = glm.vec3(0.0)
            for v in pts:
                p += v
            p = p / float(len(pts))
        else:
            p = _closest_point_on_triangle(box["c"], a, b, cc)
        if not box_is_a:
            n = -n
        out.append((p, n, depth))
    if not out:
        return None
    out.sort(key=lambda x: -x[2])
    return out[:_MAX_MESH_CONTACTS]


def _generate_contact(sa: dict, sb: dict):
    """Dispatch on the shape-kind pair.  Returns a list of
    (point, normal_from_B_to_A, depth) tuples or None."""
    ka, kb = sa["kind"], sb["kind"]
    if ka == "none" or kb == "none":
        return None
    if ka in ("plane", "heightfield") and kb in ("plane", "heightfield"):
        return None

    if ka == "sphere" and kb == "sphere":
        r = _contact_sphere_sphere(sa, sb)
        return [r] if r else None
    if ka == "sphere" and kb == "box":
        r = _contact_sphere_box(sa, sb, True)
        return [r] if r else None
    if ka == "box" and kb == "sphere":
        r = _contact_sphere_box(sb, sa, False)
        return [r] if r else None
    if ka == "sphere" and kb == "capsule":
        r = _contact_sphere_capsule(sa, sb, True)
        return [r] if r else None
    if ka == "capsule" and kb == "sphere":
        r = _contact_sphere_capsule(sb, sa, False)
        return [r] if r else None
    if ka == "sphere" and kb == "plane":
        r = _contact_sphere_plane(sa, sb, True)
        return [r] if r else None
    if ka == "plane" and kb == "sphere":
        r = _contact_sphere_plane(sb, sa, False)
        return [r] if r else None
    if ka == "sphere" and kb == "heightfield":
        r = _contact_sphere_heightfield(sa, sb, True)
        return [r] if r else None
    if ka == "heightfield" and kb == "sphere":
        r = _contact_sphere_heightfield(sb, sa, False)
        return [r] if r else None
    if ka == "box" and kb == "plane":
        return _contact_box_plane(sa, sb, True)
    if ka == "plane" and kb == "box":
        return _contact_box_plane(sb, sa, False)
    if ka == "box" and kb == "heightfield":
        return _contact_box_heightfield(sa, sb, True)
    if ka == "heightfield" and kb == "box":
        return _contact_box_heightfield(sb, sa, False)
    if ka == "box" and kb == "box":
        return _contact_box_box(sa, sb)
    if ka == "capsule" and kb == "box":
        r = _contact_capsule_box(sa, sb, True)
        return [r] if r else None
    if ka == "box" and kb == "capsule":
        r = _contact_capsule_box(sb, sa, False)
        return [r] if r else None
    if ka == "capsule" and kb == "capsule":
        r = _contact_capsule_capsule(sa, sb)
        return [r] if r else None
    if ka == "capsule" and kb == "plane":
        r = _contact_capsule_plane(sa, sb, True)
        return [r] if r else None
    if ka == "plane" and kb == "capsule":
        r = _contact_capsule_plane(sb, sa, False)
        return [r] if r else None
    if ka == "capsule" and kb == "heightfield":
        r = _contact_capsule_heightfield(sa, sb, True)
        return [r] if r else None
    if ka == "heightfield" and kb == "capsule":
        r = _contact_capsule_heightfield(sb, sa, False)
        return [r] if r else None

    # ---- triangle mesh pairs (already return contact lists) --------------
    if ka == "mesh" or kb == "mesh":
        if ka == "mesh" and kb == "mesh":
            return None            # two triangle soups: unsupported combo
        mesh_s = sa if ka == "mesh" else sb
        other = sb if ka == "mesh" else sa
        ok = other["kind"]
        other_is_a = ka != "mesh"
        if ok == "sphere":
            return _contact_sphere_mesh(other, mesh_s, other_is_a)
        if ok == "capsule":
            return _contact_capsule_mesh(other, mesh_s, other_is_a)
        if ok == "box":
            return _contact_box_mesh(other, mesh_s, other_is_a)
        # planes / heightfields vs a mesh: both are level geometry — skip
        return None
    return None


# ------------------------------------------------------------- physics world

class _PhysEntry:
    """One collider-bearing node tracked by the PhysicsWorld."""
    __slots__ = ("node", "collider", "body", "kind", "scale",
                 "aabb0", "aabb1", "asleep",  # [TRIPLEA:phys_entry_asleep_slot]
                 "_aabb_src")

    def __init__(self, node, collider, body):
        self.node = node
        self.collider = collider
        self.body = body              # RigidBody or None (static)
        self.kind = "static"
        if body is not None:
            self.kind = body.body_type
        self.scale = _world_scale(node)
        self.aabb0 = glm.vec3(0)
        self.aabb1 = glm.vec3(0)
        self._aabb_src = None        # shape view the AABB was built from
        self.asleep = False          # island sleep flag (physics overhaul)

    def shape(self) -> dict:
        if self.kind == "dynamic" and self.body is not None:
            return _shape_from_body(self.collider, self.body, self.scale)
        return _shape_from_node_collider(self.node, self.collider)

    def refresh_aabb(self):
        sv = self.shape()
        if sv is not self._aabb_src:          # cached shape -> same AABB
            self.aabb0, self.aabb1 = _shape_aabb(sv)
            self._aabb_src = sv if sv.get("kind") in ("sphere", "box", "capsule", "plane") else None

    @property
    def inv_mass(self) -> float:
        return self.body.inv_mass if self.body is not None else 0.0

    @property
    def velocity(self) -> glm.vec3:
        if self.body is not None:
            return self.body.velocity
        return glm.vec3(0.0)


class PhysicsWorld:
    """The CPU physics simulation.  One instance lives on the engine as
    engine.physics; game code may also create standalone worlds (tests,
    headless tools).

    Usage:
        world = PhysicsWorld()
        world.gravity = glm.vec3(0, -9.81, 0)
        world.rebuild(scene)          # collect nodes with colliders
        world.step(dt)                # fixed 60 Hz, 2 substeps
        hit = world.raycast(origin, dir, 100.0)
    """

    FIXED_DT = 1.0 / 60.0
    SUBSTEPS = 2
    # [TRIPLEA:solver_iterations_4] warm-started manifolds converge in 4
    SOLVER_ITERATIONS = 4

    def __init__(self, gravity=None):
        self.gravity = glm.vec3(0.0, -9.81, 0.0) if gravity is None \
            else _v3(gravity)
        self.entries: List[_PhysEntry] = []
        self._accumulator = 0.0
        self._contacts: List[Contact] = []
        self._solid_pairs: set = set()
        self._trigger_pairs: set = set()
        self._pair_nodes: Dict[tuple, tuple] = {}
        self.events: List[tuple] = []   # (kind, nodeA, nodeB, contact|None)
        self.time = 0.0
        # [TRIPLEA:physics_world_init_extras] CPU physics overhaul state
        self.manifold_mgr = ManifoldManager()   # persistent contact manifolds
        self.island_mgr = IslandManager()       # simulation islands + sleep
        self.sap = SweepAndPrune()              # broadphase
        self._frame = 0                         # substep counter (manifold ages)
        self._statics_dirty = True              # rebuild static AABB cache
        self._static_entries: List[_PhysEntry] = []
        self._entry_by_body: Dict[int, _PhysEntry] = {}
        self._soa = None                        # lazy numba SoA buffers

    # ------------------------------------------------------- registration
    def clear(self):
        self.entries.clear()
        self._contacts.clear()
        self._solid_pairs.clear()
        self._trigger_pairs.clear()
        self._pair_nodes.clear()
        self.events.clear()
        self._accumulator = 0.0

    def rebuild(self, scene: "Scene"):
        """Collect every node that carries a Collider (plus terrain
        heightfields) from the scene graph."""
        self.clear()
        for node in scene.root.traverse():
            self._maybe_add(node)

    def _maybe_add(self, node: "SceneNode"):
        if not node.visible:
            return           # hidden nodes (templates, corpses) don't collide
        col = getattr(node, "collider", None)
        if col is not None:
            self.add_node(node)
        elif node.is_terrain and "terrain_config" in node.user_data:
            cfg = node.user_data["terrain_config"]
            hf = HeightfieldCollider(
                size=cfg.get("size", 200.0),
                resolution=cfg.get("resolution", 128),
                height_scale=cfg.get("height_scale", 10.0),
                seed=cfg.get("seed", 42))
            node.collider = hf
            self.add_node(node)

    def add_node(self, node: "SceneNode") -> Optional[_PhysEntry]:
        col = getattr(node, "collider", None)
        if col is None:
            return None
        for e in self.entries:
            if e.node is node:
                return e
        body = getattr(node, "rigidbody", None)
        if body is not None and body.body_type in ("dynamic", "kinematic"):
            # Initialise state for BOTH moving body kinds: kinematic bodies
            # derive their velocity from (node_pos - body.position) each
            # substep, so an uninitialised body.position would launch
            # anything touching them.
            body.position = node.world_position()
            body.orientation = _world_quat(node)
            body.velocity = glm.vec3(0.0)
            body.angular_velocity = glm.vec3(0.0)
            body._force = glm.vec3(0.0)
            if body.body_type == "dynamic":
                body._inv_inertia_local = self._compute_inv_inertia(node, col, body)
            if isinstance(col, MeshCollider) and body.body_type == "dynamic":
                # Exact per-triangle collision is a static/kinematic
                # feature; a dynamic triangle soup needs convex pieces.
                Log.warn(f"'{node.name}': MeshCollider + dynamic RigidBody "
                         f"— the body will collide as its mesh's bounding "
                         f"sphere.  Use a static/kinematic body (or a "
                         f"primitive collider) for exact mesh collision.")
        if isinstance(col, MeshCollider) and col._bound_local is None:
            # Stash the local bounding sphere for the dynamic degrade path
            # (shape_from_body has no node access, so it can't compute it).
            mesh = node.mesh
            bs = getattr(mesh, "bounding_sphere", None) if mesh else None
            if bs is not None:
                col._bound_local = (glm.vec3(bs[0]) + col.center,
                                    float(bs[1]))
        entry = _PhysEntry(node, col, body)
        # [TRIPLEA:add_node_backref]
        if body is not None:
            body._world = self          # wake back-ref for add_force/impulse
            self._entry_by_body[id(body)] = entry
        if entry.kind == "static":
            self._statics_dirty = True

        entry.refresh_aabb()
        self.entries.append(entry)
        return entry

    def add_node_recursive(self, node: "SceneNode"):
        for n in node.traverse():
            self._maybe_add(n)

    def remove_node(self, node: "SceneNode"):
        ids = {id(n) for n in node.traverse()}
        # [TRIPLEA:remove_node_purge]
        removed_eids = {id(e) for e in self.entries if id(e.node) in ids}
        self.entries = [e for e in self.entries if id(e.node) not in ids]
        self._solid_pairs = {p for p in self._solid_pairs
                             if p[0] not in ids and p[1] not in ids}
        self._trigger_pairs = {p for p in self._trigger_pairs
                               if p[0] not in ids and p[1] not in ids}
        self._pair_nodes = {k: v for k, v in self._pair_nodes.items()
                            if k[0] not in ids and k[1] not in ids}
        self._statics_dirty = True
        self._entry_by_body = {k: v for k, v in self._entry_by_body.items()
                               if id(v.node) not in ids}
        self.manifold_mgr.purge_entries(removed_eids)
        self.island_mgr.purge_entries(removed_eids)

    @staticmethod
    def _compute_inv_inertia(node, col: Collider, body: RigidBody) -> glm.vec3:
        """Diagonal inverse inertia in LOCAL space (solid approximations)."""
        if body.freeze_rotation or body.inv_mass <= 0.0:
            return glm.vec3(0.0)
        m = body.mass
        ws = _world_scale(node)
        if isinstance(col, SphereCollider):
            r = col.radius * max(ws.x, max(ws.y, ws.z))
            i = 0.4 * m * r * r
            return glm.vec3(1.0 / max(i, 1e-6))
        if isinstance(col, BoxCollider):
            sx = col.size.x * ws.x
            sy = col.size.y * ws.y
            sz = col.size.z * ws.z
            ix = (1.0 / 12.0) * m * (sy * sy + sz * sz)
            iy = (1.0 / 12.0) * m * (sx * sx + sz * sz)
            iz = (1.0 / 12.0) * m * (sx * sx + sy * sy)
            return glm.vec3(1.0 / max(ix, 1e-6), 1.0 / max(iy, 1e-6),
                            1.0 / max(iz, 1e-6))
        if isinstance(col, CapsuleCollider):
            r = col.radius * max(ws.x, ws.z)
            h = col.height * ws.y
            ix = (1.0 / 12.0) * m * (3 * r * r + h * h)
            iy = 0.5 * m * r * r
            return glm.vec3(1.0 / max(ix, 1e-6), 1.0 / max(iy, 1e-6),
                            1.0 / max(ix, 1e-6))
        if isinstance(col, MeshCollider):
            # Approximate as the solid box of the mesh's local AABB (the
            # dynamic path collides as the bounding sphere anyway).
            bb = getattr(node.mesh, "bounding_box", None) \
                if node.mesh is not None else None
            if bb is None:
                return glm.vec3(0.0)
            sx = max(1e-4, float(bb[1].x - bb[0].x) * ws.x)
            sy = max(1e-4, float(bb[1].y - bb[0].y) * ws.y)
            sz = max(1e-4, float(bb[1].z - bb[0].z) * ws.z)
            ix = (1.0 / 12.0) * m * (sy * sy + sz * sz)
            iy = (1.0 / 12.0) * m * (sx * sx + sz * sz)
            iz = (1.0 / 12.0) * m * (sx * sx + sy * sy)
            return glm.vec3(1.0 / max(ix, 1e-6), 1.0 / max(iy, 1e-6),
                            1.0 / max(iz, 1e-6))
        return glm.vec3(0.0)

    def entry_for(self, node: "SceneNode") -> Optional[_PhysEntry]:
        for e in self.entries:
            if e.node is node:
                return e
        return None

    # ------------------------------------------------------------- stepping
    def step(self, dt: float):
        """Advance the simulation with a fixed internal timestep (stable
        regardless of render framerate).  Collision/trigger events are
        appended to self.events — the engine flushes them to blueprints
        after each call."""
        dt = _clamp(float(dt), 0.0, 0.1)
        self._accumulator += dt
        steps = 0
        while self._accumulator >= self.FIXED_DT and steps < 4:
            self._accumulator -= self.FIXED_DT
            self._fixed_step(self.FIXED_DT)
            steps += 1
        if steps == 4:                      # spiral-of-death guard
            self._accumulator = 0.0

    def _fixed_step(self, h: float):
        # Kinematic bodies: compute their velocity ONCE per fixed step and
        # keep it constant across substeps (recomputing per substep makes
        # friction see 2x / 0x velocities and cancel itself out).
        sub = h / self.SUBSTEPS
        self._kinematic_targets = []
        for e in self.entries:
            if e.kind == "kinematic" and e.body is not None:
                b = e.body
                target_pos = e.node.world_position()
                target_rot = _world_quat(e.node)
                if h > 1e-9:
                    b.velocity = (target_pos - b.position) / h
                    if _finalpatch_is_imported_glb_node(e.node):
                        dq = glm.inverse(b.orientation) * target_rot
                    else:
                        dq = target_rot * glm.inverse(b.orientation)
                    ang = 2.0 * math.acos(_clamp(dq.w, -1.0, 1.0))
                    if ang > 1e-6:
                        s = math.sqrt(max(1.0 - dq.w * dq.w, 1e-12))
                        b.angular_velocity = glm.vec3(dq.x / s, dq.y / s,
                                                      dq.z / s) * (ang / h)
                    else:
                        b.angular_velocity = glm.vec3(0.0)
                self._kinematic_targets.append((e, target_pos, target_rot))
        for _ in range(self.SUBSTEPS):
            self._substep(sub)
        # Land kinematic bodies exactly on their node transforms
        for (e, tp, tr) in self._kinematic_targets:
            e.body.position = glm.vec3(tp)
            e.body.orientation = tr
        self.time += h
        self._dispatch_events()

    def _substep(self, h: float):
        # [TRIPLEA:substep_rewrite] CPU physics overhaul: sleeping bodies
        # cost ZERO -- skip work, not math.  Flow: islands/sleep -> integrate
        # (awake only) -> SAP broadphase -> persistent-manifold narrowphase
        # -> warm-started solver -> write-back (awake only).
        entries = self.entries

        # --- static cache: statics rarely move, refresh once per change ---
        if self._statics_dirty:
            self._static_entries = [e for e in entries if e.kind == "static"]
            for e in self._static_entries:
                e.refresh_aabb()
            self._statics_dirty = False
        statics = self._static_entries

        # --- islands + sleep (DYNAMIC entries only; kinematic bodies are
        # driven externally and never sleep) ---
        awake_dyn = [e for e in entries
                     if e.kind == "dynamic" and e.body is not None
                     and not e.asleep]
        kin = [e for e in entries if e.kind == "kinematic"]
        mgr = self.manifold_mgr
        self.island_mgr.rebuild(awake_dyn, mgr.active_manifolds())
        for isl in self.island_mgr.islands:
            self.island_mgr.try_sleep(isl, h)
        if awake_dyn:
            awake_dyn = [e for e in awake_dyn if not e.asleep]

        # --- integrate forces + velocities (awake dynamics only) ---
        if awake_dyn:
            if _HAS_NUMBA and len(awake_dyn) > 30:
                self._soa_integrate_awake(awake_dyn, h)
            else:
                for e in awake_dyn:
                    b = e.body
                    acc = self.gravity * b.gravity_scale
                    if glm.length(b._force) > 0.0:
                        acc += b._force * b.inv_mass
                    b.velocity += acc * h
                    b._force = glm.vec3(0.0)
                    damp = max(0.0, 1.0 - b.linear_damping * h)
                    b.velocity *= damp
                    if not b.freeze_rotation:
                        b.angular_velocity *= max(0.0, 1.0 - b.angular_damping * h)
                    b.position += b.velocity * h
                    if not b.freeze_rotation and glm.length(b.angular_velocity) > 1e-8:
                        w = b.angular_velocity
                        wq = glm.quat(0.0, w.x * 0.5 * h, w.y * 0.5 * h, w.z * 0.5 * h)
                        _finalpatch_integrate_body_orientation(b, e, h)
                    # safety: never let a body leave the world numerically
                    if not (math.isfinite(b.position.x) and math.isfinite(b.position.y)
                            and math.isfinite(b.position.z)):
                        b.position = glm.vec3(0.0, 5.0, 0.0)
                        b.velocity = glm.vec3(0.0)

        # --- kinematic bodies advance at their (fixed-step) velocity ---
        for (e, _tp, _tr) in getattr(self, "_kinematic_targets", []):
            b = e.body
            b.position += b.velocity * h
            if glm.length(b.angular_velocity) > 1e-8:
                w = b.angular_velocity
                wq = glm.quat(0.0, w.x * 0.5 * h, w.y * 0.5 * h, w.z * 0.5 * h)
                _finalpatch_integrate_body_orientation(b, e, h)

        # --- broadphase (sweep & prune over awake movers) ---
        # Sleeping dynamics keep their last AABB (they have not moved, so it
        # is still exact) -- zero refresh cost for them; they are included
        # only so an awake intruder can find (and wake) them.
        awake_movers = awake_dyn + kin
        for e in awake_movers:
            e.refresh_aabb()
        sleeping = [e for e in entries
                    if e.kind == "dynamic" and e.body is not None and e.asleep]
        sap_list = awake_movers + sleeping
        by_id = {id(e): e for e in sap_list}
        for e in statics:
            by_id[id(e)] = e
        pairs = self.sap.run(sap_list)
        if statics and awake_movers:
            pairs |= self.sap.run_cross(awake_movers, statics)

        # --- narrowphase with persistent manifolds + warm starting ---
        frame = self._frame
        processed = []
        for (ia, ib) in pairs:
            ei = by_id.get(ia)
            ej = by_id.get(ib)
            if ei is None or ej is None:
                continue
            if ei.inv_mass == 0.0 and ej.inv_mass == 0.0:
                continue     # static/static, static/kinematic, kin/kin
            if not ei.node.visible or not ej.node.visible:
                continue
            if ei.collider.is_trigger or ej.collider.is_trigger:
                continue     # triggers handled in the event pass below
            if ei.asleep and ej.asleep:
                continue     # sleeping pairs cost zero
            if ei.asleep or ej.asleep:
                # Wake check: a fast intruder or a persistent contact
                # manifold wakes the sleeping island; otherwise skip.
                sleeper = ei if ei.asleep else ej
                other = ej if ei.asleep else ei
                rv = other.velocity - sleeper.velocity
                if glm.dot(rv, rv) > _PHYS_WAKE_VEL2 \
                        or ManifoldManager.key(ei, ej) in mgr.manifolds:
                    self.island_mgr.wake_island_of(
                        sleeper, mgr.manifolds.values())
                else:
                    continue
            key = ManifoldManager.key(ei, ej)
            m = mgr.manifolds.get(key)
            if m is not None and not ((m.body_a is ei and m.body_b is ej)
                                      or (m.body_a is ej and m.body_b is ei)):
                del mgr.manifolds[key]     # recycled id() collision guard
                m = None
            if m is not None and m.contacts \
                    and 0 < frame - m.last_frame < self.SUBSTEPS:
                processed.append(m)        # reuse this fixed step's manifold
                continue
            sa = ei.shape()
            sb = ej.shape()
            raw = _generate_contact(sa, sb)
            if not raw:
                if m is not None:
                    m.refresh([], frame)   # contact lost: age the manifold
                continue
            cs = []
            for (point, normal, depth) in raw:
                c = Contact(ei, ej, point, normal, depth)
                fa = ei.collider.friction if ei.body is None or \
                    ei.body.friction is None else ei.body.friction
                fb = ej.collider.friction if ej.body is None or \
                    ej.body.friction is None else ej.body.friction
                ra = ei.collider.restitution if ei.body is None or \
                    ei.body.restitution is None else ei.body.restitution
                rb = ej.collider.restitution if ej.body is None or \
                    ej.body.restitution is None else ej.body.restitution
                c.friction = math.sqrt(max(fa * fb, 0.0))
                c.restitution = max(ra, rb)
                cs.append(c)
            m = mgr.register_contacts(ei, ej, cs, frame)
            processed.append(m)

        # flatten active manifold contacts (anything else reading
        # self._contacts keeps working unchanged)
        self._contacts = []
        for m in processed:
            self._contacts.extend(m.contacts)

        # --- solve velocity (warm-started sequential impulses) ---
        self._solve_contacts_velocity(self._contacts)
        # --- positional correction (single pass, early-out) ---
        for c in self._contacts:
            self._solve_contact_position(c)

        # --- write dynamics back to their scene nodes (awake only) ---
        for e in entries:
            if e.kind == "dynamic" and e.body is not None and not e.asleep:
                _write_node_world_transform(e.node, e.body.position,
                                            e.body.orientation)

        self.manifold_mgr.cull_stale(frame)
        self._frame = frame + 1

    # ------------------------------------------------------------- solver
    def _solve_contacts_velocity(self, contacts):
        """Gauss-Seidel over ALL contacts per iteration (warm-started via
        persistent manifolds), with an early-out once the largest impulse
        change of an iteration drops below 1e-5."""
        # [TRIPLEA:solver_velocity_rewrite]
        if not contacts:
            return
        for _ in range(self.SOLVER_ITERATIONS):
            max_dj = 0.0
            for c in contacts:
                dj = self._solve_contact_velocity_iter(c)
                if dj > max_dj:
                    max_dj = dj
            if max_dj < 1e-5:
                break

    def _solve_contact_velocity(self, c: Contact):
        # Back-compat shim: solve one contact to convergence (old semantics).
        for _ in range(self.SOLVER_ITERATIONS):
            if self._solve_contact_velocity_iter(c) < 1e-5:
                break

    def _solve_contact_velocity_iter(self, c: Contact) -> float:
        """One sequential-impulse iteration for a contact; returns the
        largest |delta j| applied (used by the outer early-out)."""
        a, b = c.a, c.b
        n = c.normal
        va = a.velocity
        vb = b.velocity
        rv = va - vb
        vn = glm.dot(rv, n)
        if vn > 0.0 and c.jn > 0.0:
            return 0.0
        inv_m_sum = a.inv_mass + b.inv_mass
        if inv_m_sum <= 0.0:
            return 0.0
        e = c.restitution if vn < -1.0 else 0.0
        j = -(1.0 + e) * vn / inv_m_sum
        j_new = max(c.jn + j, 0.0)
        j = j_new - c.jn
        c.jn = j_new
        dj = abs(j)
        impulse = n * j
        if a.body is not None:
            a.body.velocity += impulse * a.inv_mass
        if b.body is not None:
            b.body.velocity -= impulse * b.inv_mass

        # ---- friction (one tangential direction, Coulomb clamp) ----
        va = a.velocity
        vb = b.velocity
        rv = va - vb
        vt = rv - n * glm.dot(rv, n)
        vtl = glm.length(vt)
        if vtl > 1e-6:
            t = vt / vtl
            jt = -glm.dot(rv, t) / inv_m_sum
            max_f = c.friction * c.jn
            jt_total = _clamp(c.jt0 + jt, -max_f, max_f)
            jt = jt_total - c.jt0
            c.jt0 = jt_total
            if abs(jt) > dj:
                dj = abs(jt)
            fimp = t * jt
            if a.body is not None:
                a.body.velocity += fimp * a.inv_mass
            if b.body is not None:
                b.body.velocity -= fimp * b.inv_mass
        return dj

    def _solve_contact_position(self, c: Contact):
        a, b = c.a, c.b
        inv_m_sum = a.inv_mass + b.inv_mass
        if inv_m_sum <= 0.0:
            return
        percent = 0.6
        slop = 0.005
        corr_mag = max(c.depth - slop, 0.0) * percent / inv_m_sum
        if corr_mag < 1e-4:          # [TRIPLEA:solver_position_earlyout]
            return                   # negligible penetration: skip
        corr = c.normal * corr_mag
        if a.body is not None and a.inv_mass > 0.0:
            a.body.position += corr * a.inv_mass
        if b.body is not None and b.inv_mass > 0.0:
            b.body.position -= corr * b.inv_mass

    # ------------------------------------------------------------- events
    @staticmethod
    def _pair_key(a: _PhysEntry, b: _PhysEntry):
        ia, ib = id(a.node), id(b.node)
        return (ia, ib) if ia <= ib else (ib, ia)

    def _dispatch_events(self):
        """Compare the current overlap set with the previous step's and
        emit enter/stay/exit events for both solid and trigger pairs.
        Every event is (kind, nodeA, nodeB, contact|None)."""
        new_solid: set = set()
        new_trigger: set = set()
        solid_contacts: Dict[tuple, Contact] = {}
        pair_nodes: Dict[tuple, tuple] = {}
        # [FINALPATCH3:phys_perf] O(n log n) candidates via sweep & prune
        # instead of the O(n^2) all-pairs scan (32M tests @ 8000 entries).
        sap_pairs = self.sap.run(self.entries)
        by_id = {id(e): e for e in self.entries}
        for (ia, ib) in sap_pairs:
            ei = by_id.get(ia)
            ej = by_id.get(ib)
            if ei is None or ej is None:
                continue
            if not ei.node.visible or not ej.node.visible:
                continue
            if not _aabb_overlap(ei.aabb0, ei.aabb1, ej.aabb0, ej.aabb1):
                continue
            trig = ei.collider.is_trigger or ej.collider.is_trigger
            if not trig and ei.inv_mass == 0.0 and ej.inv_mass == 0.0:
                # [FINALPATCH3:phys_perf] solid static/static pairs are
                # rejected BEFORE the expensive narrowphase contact test
                # (triggers keep the original record-even-when-static rule).
                continue
            sa = ei.shape()
            sb = ej.shape()
            raw = _generate_contact(sa, sb)
            if not raw:
                continue
            key = self._pair_key(ei, ej)
            if trig:
                new_trigger.add(key)
                pair_nodes[key] = (ei.node, ej.node)
            else:
                new_solid.add(key)
                pair_nodes[key] = (ei.node, ej.node)
                best = None
                for (point, normal, depth) in raw:
                    if best is None or depth > best.depth:
                        best = Contact(ei, ej, point, normal, depth)
                solid_contacts[key] = best

        for key in new_solid:
            c = solid_contacts.get(key)
            if c is None:
                continue
            kind = "collision_enter" if key not in self._solid_pairs \
                else "collision_stay"
            self.events.append((kind, c.a.node, c.b.node, c))
        for key in self._solid_pairs - new_solid:
            na, nb = self._pair_nodes.get(key, (None, None))
            if na is not None:
                self.events.append(("collision_exit", na, nb, None))
        for key in new_trigger:
            if key not in self._trigger_pairs:
                na, nb = pair_nodes.get(key, (None, None))
                if na is not None:
                    self.events.append(("trigger_enter", na, nb, None))
        for key in self._trigger_pairs - new_trigger:
            na, nb = self._pair_nodes.get(key, (None, None))
            if na is not None:
                self.events.append(("trigger_exit", na, nb, None))

        self._pair_nodes = pair_nodes
        self._solid_pairs = new_solid
        self._trigger_pairs = new_trigger

    # ------------------------------------------------------------- queries
    def raycast(self, origin, direction, max_dist: float = 1000.0,
                ignore: Optional[set] = None,
                hit_triggers: bool = False) -> Optional[RayHit]:
        """Cast a ray against every collider; returns the closest RayHit
        or None.  `ignore` is a set of node ids to skip."""
        ro = _v3(origin)
        rd = _safe_normalize(_v3(direction), glm.vec3(0, 0, -1))
        best = None
        best_t = float(max_dist)
        for e in self.entries:
            if ignore and id(e.node) in ignore:
                continue
            if not e.node.visible:
                continue
            if e.collider.is_trigger and not hit_triggers:
                continue
            # [FINALPATCH3:phys_perf_v2] refresh the entry AABB first:
            # gameplay blueprints teleport nodes directly (enemies, the
            # kinematic player, bullets) OUTSIDE physics substeps, so a
            # stale cached AABB would wrongly slab-reject REAL hits.  After
            # the refresh, slab-reject before the expensive per-shape ray
            # test (8000-entry brute force killer, correctness-preserving).
            e.refresh_aabb()
            _lo, _hi = e.aabb0, e.aabb1
            _t0, _t1, _ok = 0.0, best_t, True
            for _ax in ("x", "y", "z"):
                _o = getattr(ro, _ax)
                _d = getattr(rd, _ax)
                if abs(_d) < 1e-9:
                    if _o < getattr(_lo, _ax) or _o > getattr(_hi, _ax):
                        _ok = False
                        break
                else:
                    _ta = (getattr(_lo, _ax) - _o) / _d
                    _tb = (getattr(_hi, _ax) - _o) / _d
                    if _ta > _tb:
                        _ta, _tb = _tb, _ta
                    if _ta > _t0:
                        _t0 = _ta
                    if _tb < _t1:
                        _t1 = _tb
                    if _t0 > _t1:
                        _ok = False
                        break
            if not _ok:
                continue
            s = e.shape()
            t, n = self._ray_shape(ro, rd, best_t, s)
            if t is not None and t < best_t:
                best_t = t
                best = RayHit(e.node, e.collider, ro + rd * t, n, t)
        return best

    def _ray_shape(self, ro, rd, max_t, s: dict):
        """Ray vs one shape view; returns (t, normal) or (None, None)."""
        k = s["kind"]
        if k == "sphere":
            return self._ray_sphere(ro, rd, max_t, s["c"], s["r"])
        if k == "plane":
            denom = glm.dot(rd, s["n"])
            if abs(denom) < 1e-9:
                return None, None
            t = glm.dot(s["p"] - ro, s["n"]) / denom
            if t < 0.0 or t > max_t:
                return None, None
            p = ro + rd * t
            if not _plane_point_inside(s, p):
                return None, None
            n = s["n"] if denom < 0.0 else -s["n"]
            return t, n
        if k == "box":
            q = s["q"]
            inv_q = glm.inverse(q)
            lro = inv_q * (ro - s["c"])
            lrd = inv_q * rd
            tmin, tmax = -math.inf, math.inf
            n_axis = -1
            n_sign = 1.0
            ok = True
            for i in range(3):
                o, d = lro[i], lrd[i]
                h = s["h"][i]
                if abs(d) < 1e-9:
                    if o < -h or o > h:
                        ok = False
                        break
                else:
                    t1, t2 = (-h - o) / d, (h - o) / d
                    sign = -1.0 if t1 < t2 else 1.0
                    if t1 > t2:
                        t1, t2 = t2, t1
                    if t1 > tmin:
                        tmin = t1
                        n_axis = i
                        n_sign = sign
                    tmax = min(tmax, t2)
                    if tmin > tmax:
                        ok = False
                        break
            if not ok or tmin < 0.0 or tmin > max_t:
                return None, None
            n_local = glm.vec3(0.0)
            if n_axis >= 0:
                n_local[n_axis] = n_sign
            return tmin, _safe_normalize(q * n_local)
        if k == "capsule":
            best = None
            best_n = None
            for c in (s["p0"], s["p1"]):
                t, n = self._ray_sphere(ro, rd, max_t, c, s["r"])
                if t is not None and (best is None or t < best):
                    best, best_n = t, n
            axis = s["p1"] - s["p0"]
            alen = glm.length(axis)
            if alen > 1e-9:
                a = axis / alen
                oc = ro - s["p0"]
                d_perp = rd - a * glm.dot(rd, a)
                oc_perp = oc - a * glm.dot(oc, a)
                A = glm.dot(d_perp, d_perp)
                if A > 1e-12:
                    B = 2.0 * glm.dot(d_perp, oc_perp)
                    C = glm.dot(oc_perp, oc_perp) - s["r"] * s["r"]
                    disc = B * B - 4 * A * C
                    if disc >= 0.0:
                        sq = math.sqrt(disc)
                        for t in sorted(((-B - sq) / (2 * A),
                                         (-B + sq) / (2 * A))):
                            if t < 0.0 or t > max_t:
                                continue
                            p = ro + rd * t
                            hgt = glm.dot(p - s["p0"], a)
                            if 0.0 <= hgt <= alen:
                                if best is None or t < best:
                                    best = t
                                    cp = s["p0"] + a * hgt
                                    best_n = _safe_normalize(p - cp)
                                break
            if best is not None:
                return best, best_n
            return None, None
        if k == "heightfield":
            return self._ray_heightfield(ro, rd, max_t, s)
        if k == "mesh":
            return self._ray_trimesh(ro, rd, max_t, s)
        return None, None

    @staticmethod
    def _ray_trimesh(ro, rd, max_t, s: dict):
        """Ray vs a triangle-mesh BVH (double-sided, so rays started
        INSIDE a building still hit its walls)."""
        bvh = s["bvh"]
        hit = bvh.raycast(ro, rd, max_t)
        if hit is None:
            return None, None
        t, ti = hit
        n = bvh.normals[ti]
        n = glm.vec3(float(n[0]), float(n[1]), float(n[2]))
        if glm.dot(n, rd) > 0.0:
            n = -n
        return t, n

    @staticmethod
    def _ray_sphere(ro, rd, max_t, c, r):
        oc = ro - c
        b = glm.dot(oc, rd)
        cc = glm.dot(oc, oc) - r * r
        disc = b * b - cc
        if disc < 0.0:
            return None, None
        sq = math.sqrt(disc)
        t = -b - sq
        if t < 0.0:
            t = -b + sq
        if t < 0.0 or t > max_t:
            return None, None
        return t, _safe_normalize((ro + rd * t) - c)

    def _ray_heightfield(self, ro, rd, max_t, s: dict):
        """March the ray through the heightfield, refine by bisection."""
        steps = 200
        dt_step = max_t / steps
        prev_t = 0.0
        prev_above = None
        xf = _hf_xform(s["node"])      # node does not move during the ray
        for i in range(steps + 1):
            t = i * dt_step
            p = ro + rd * t
            h = _hf_height_world(s, p.x, p.z, xf)
            above = None if h is None else (p.y - h)
            if above is not None and above <= 0.0 and prev_above is not None \
                    and prev_above > 0.0:
                lo, hi = prev_t, t
                for _ in range(16):
                    mid = (lo + hi) * 0.5
                    pm = ro + rd * mid
                    hm = _hf_height_world(s, pm.x, pm.z, xf)
                    if hm is not None and pm.y - hm <= 0.0:
                        hi = mid
                    else:
                        lo = mid
                hit_t = hi
                p_hit = ro + rd * hit_t
                n = _hf_normal_world(s, p_hit.x, p_hit.z, xf)
                if glm.dot(n, rd) > 0.0:
                    n = -n
                return hit_t, n
            prev_t, prev_above = t, above
        return None, None

    def overlap_sphere(self, center, radius: float,
                       include_triggers: bool = True) -> List[_PhysEntry]:
        """All entries whose shape overlaps the given sphere."""
        probe = {"kind": "sphere", "c": _v3(center), "r": float(radius)}
        out = []
        for e in self.entries:
            if not e.node.visible:
                continue
            if e.collider.is_trigger and not include_triggers:
                continue
            e.refresh_aabb()  # [FINALPATCH3:phys_perf_v2] live AABB (nodes teleport)
            lo, hi = e.aabb0, e.aabb1
            if not _aabb_overlap(probe["c"] - glm.vec3(radius),
                                 probe["c"] + glm.vec3(radius), lo, hi):
                continue
            raw = _generate_contact(probe, e.shape())
            if raw:
                out.append(e)
        return out


# ------------------------------------------------------------- character

class CharacterController:
    """Kinematic capsule mover for first/third-person characters.

    Usage:
        cc = CharacterController(world, node, radius=0.35, height=1.8)
        cc.move(glm.vec3(dx, dy, dz))   # collide-and-slide
        cc.is_grounded                  # refreshed after move()

    Features: collide-and-slide (up to 3 planes), slope limit, step
    offset, ground detection, ground snap, ceiling bump.  Pure CPU, no GL."""
    _H3D_SLOPE_FIX = True     # slope-speed / downhill-hop fix (see _move_slide)

    def __init__(self, world: PhysicsWorld, node: "SceneNode",
                 radius: float = 0.35, height: float = 1.8,
                 slope_limit_deg: float = 50.0, step_offset: float = 0.4,
                 skin: float = 0.02):
        self.world = world
        self.node = node
        self.radius = max(0.05, float(radius))
        self.height = max(self.radius * 2.0, float(height))
        self.slope_cos = math.cos(math.radians(_clamp(slope_limit_deg,
                                                      0.0, 89.0)))
        self.step_offset = max(0.0, float(step_offset))
        self.skin = max(0.005, float(skin))
        self.is_grounded = False
        self.ground_normal = glm.vec3(0, 1, 0)
        self.ground_node: Optional[SceneNode] = None
        self.hit_ceiling = False
        self._floor_hit = False
        self._snap = None                # per-move collider snapshot

    def _capsule(self, center: glm.vec3) -> Tuple[glm.vec3, glm.vec3, float]:
        seg = self.height * 0.5 - self.radius
        return (center - glm.vec3(0, seg, 0), center + glm.vec3(0, seg, 0),
                self.radius)

    @property
    def position(self) -> glm.vec3:
        return self.node.world_position()

    def feet_position(self) -> glm.vec3:
        return self.position - glm.vec3(0, self.height * 0.5, 0)

    def _snapshot(self) -> list:
        """The colliders this character can touch, validated ONCE for a
        whole move().  One move() asks for contacts 3-9 times (slide passes,
        step probe, ground snap, ground check) and nothing else can move in
        between, so re-validating every collider's transform per query was
        wasted work.  Colliders in this character's own subtree move WITH
        it and stay live (re-validated per query), exactly as before."""
        own = {id(n) for n in self.node.traverse()}
        snap = []
        for e in self.world.entries:
            if e.node is self.node or e.collider.is_trigger:
                continue
            if not e.node.visible:
                continue
            if id(e.node) in own:
                snap.append((e, None, None, None))
                continue
            e.refresh_aabb()
            snap.append((e, e.aabb0, e.aabb1, e.shape()))
        return snap

    def _collide_capsule(self, center: glm.vec3) -> List[tuple]:
        """All penetration resolutions for a capsule at `center`.
        Returns [(normal, depth, node)]."""
        p0, p1, r = self._capsule(center)
        me = {"kind": "capsule", "p0": p0, "p1": p1, "r": r}
        out = []
        snap = self.__dict__.get("_snap")
        if snap is not None:
            pad = glm.vec3(r + self.skin)
            qlo = glm.min(p0, p1) - pad
            qhi = glm.max(p0, p1) + pad
            for (e, lo, hi, sv) in snap:
                if lo is None:                  # own subtree: live
                    e.refresh_aabb()
                    lo, hi, sv = e.aabb0, e.aabb1, None
                if not _aabb_overlap(qlo, qhi, lo, hi):
                    continue
                raw = _generate_contact(me, sv if sv is not None else e.shape())
                if not raw:
                    continue
                for (point, normal, depth) in raw:
                    if depth > 0.0:
                        out.append((normal, depth, e.node))
            return out
        for e in self.world.entries:
            if e.node is self.node or e.collider.is_trigger:
                continue
            if not e.node.visible:
                continue
            e.refresh_aabb()  # [FINALPATCH3:phys_perf_v2] live AABB (nodes teleport)
            lo, hi = e.aabb0, e.aabb1
            pad = glm.vec3(r + self.skin)
            if not _aabb_overlap(glm.min(p0, p1) - pad,
                                 glm.max(p0, p1) + pad, lo, hi):
                continue
            raw = _generate_contact(me, e.shape())
            if not raw:
                continue
            for (point, normal, depth) in raw:
                if depth > 0.0:
                    out.append((normal, depth, e.node))
        return out

    def _resolve(self, center: glm.vec3) -> glm.vec3:
        """Push the capsule at `center` out of all geometry."""
        for _ in range(4):
            hits = self._collide_capsule(center)
            if not hits:
                break
            moved = False
            for (n, depth, _node) in hits:
                push = depth + self.skin
                if push > 1e-6:
                    center += n * push
                    moved = True
            if not moved:
                break
        return center

    def move(self, displacement: glm.vec3) -> glm.vec3:
        """Move the node by `displacement` with collision.  Returns the
        actual displacement applied.

        Horizontal and vertical motion run in SEPARATE passes (the classic
        character-controller structure): gravity's -Y must never pollute
        the horizontal slide, otherwise the capsule wedges on convex
        step corners instead of climbing them."""
        disp = _v3(displacement)
        start = self.position
        self.hit_ceiling = False
        was_grounded = self.is_grounded
        if glm.length(disp) < 1e-9:
            self._update_ground()
            return glm.vec3(0.0)

        self._snap = self._snapshot()
        try:
            return self._move_impl(disp, start, was_grounded)
        finally:
            self._snap = None

    def _move_impl(self, disp, start, was_grounded):
        pos = glm.vec3(start)
        # ---- horizontal pass (with step assist) ----
        disp_h = glm.vec3(disp.x, 0.0, disp.z)
        if glm.length(disp_h) > 1e-9:
            max_step = max(self.radius * 0.5, 0.05)
            n_sub = max(1, int(math.ceil(glm.length(disp_h) / max_step)))
            sub = disp_h / float(n_sub)
            for _ in range(n_sub):
                pos = self._move_slide(pos, sub, allow_step=True)
        # ---- vertical pass (gravity / jumps / ceiling) ----
        disp_v = glm.vec3(0.0, disp.y, 0.0)
        self._floor_hit = False
        if abs(disp.y) > 1e-9:
            max_step = max(self.radius * 0.5, 0.05)
            n_sub = max(1, int(math.ceil(abs(disp.y) / max_step)))
            sub = disp_v / float(n_sub)
            for _ in range(n_sub):
                pos = self._move_slide(pos, sub, allow_step=False)
        _write_node_world_position(self.node, pos)
        self._update_ground()
        # ---- ground snap (walking DOWN slopes) ----
        # Walking downhill, the horizontal pass leaves the capsule floating
        # a little above the slope every frame and gravity alone can't keep
        # up, so the player used to hop down slopes airborne (no footsteps,
        # bob/jerk).  If we were grounded, are not jumping, and this
        # frame's gravity pass touched no floor, slide down at most the drop
        # a walkable slope can produce over this frame's horizontal travel;
        # keep it only if it lands on walkable ground (ledges and cliffs
        # still fall).  On flat ground the gravity pass always touches the
        # floor, so this costs nothing there.
        if (was_grounded and disp.y <= 0.0 and not self._floor_hit
                and not self.hit_ceiling):
            h_len = math.hypot(disp.x, disp.z)
            if h_len > 1e-6:
                tan_lim = math.sqrt(max(0.0, 1.0 - self.slope_cos ** 2)) \
                    / max(self.slope_cos, 0.05)
                snap = min(h_len * tan_lim + self.skin * 2.0 + 0.02,
                           max(self.step_offset, 0.1))
                cand = glm.vec3(pos)
                max_step = max(self.radius * 0.5, 0.05)
                n_sub = max(1, int(math.ceil(snap / max_step)))
                sub = glm.vec3(0.0, -snap / float(n_sub), 0.0)
                for _ in range(n_sub):
                    cand = self._move_slide(cand, sub, allow_step=False)
                    if self._floor_hit:
                        break
                if self._floor_hit:
                    _write_node_world_position(self.node, cand)
                    self._update_ground()
                    if not self.is_grounded:
                        _write_node_world_position(self.node, pos)
                        self._update_ground()
        return self.position - start

    def _floor_lift(self, n: glm.vec3, push: float, node) -> float:
        """Vertical distance that clears a contact on WALKABLE ground.
        Heightfields report a vertical depth (lift = depth); every other
        shape reports depth along the normal (lift = depth / n.y)."""
        if isinstance(getattr(node, "collider", None), HeightfieldCollider):
            return push
        return push / max(float(n.y), 0.3)

    def _move_slide(self, pos: glm.vec3, delta: glm.vec3,
                    allow_step: bool = True) -> glm.vec3:
        """Move by `delta`, then resolve penetrations at the target.

        Walkable ground (inside the slope limit) is cleared by lifting the
        capsule STRAIGHT UP, so walking up a slope keeps exactly the
        horizontal speed (and resting on one never drifts downhill).
        Walls / steep faces push out along their normal — that push-out
        IS the slide along the surface.  The motion is applied once: the
        old code also re-applied the projected remainder after the
        push-out, which made the player up to ~2x faster up ramps and
        slopes (and along walls), with jerky speed changes on bumps."""
        target = pos + delta
        moving_h = abs(delta.x) > 1e-9 or abs(delta.z) > 1e-9
        step_lim = min(0.7, self.slope_cos)
        tried_step = False
        for _it in range(4):
            hits = self._collide_capsule(target)
            if not hits:
                break
            hits.sort(key=lambda h: -h[1])
            n, depth, node = hits[0]
            push = depth + self.skin
            if push <= 1e-7:
                break
            if n.y < -0.3 and delta.y > 0.0:
                self.hit_ceiling = True
            if n.y > self.slope_cos and delta.y <= 0.0:
                self._floor_hit = True
                target = glm.vec3(target.x,
                                  target.y + self._floor_lift(n, push, node),
                                  target.z)
                continue
            out = target + n * push
            # Step assist (stairs, curbs): only against faces too steep to
            # walk on — walkable slopes are climbed by the lift above, and
            # stepping on them teleported the player forward (jerk).
            if allow_step and not tried_step and moving_h \
                    and self.step_offset > 0.0 and abs(n.y) < step_lim:
                tried_step = True
                stepped = self._try_step(out, glm.vec3(delta.x, 0.0, delta.z), n)
                if stepped is not None:
                    return stepped[0]
            target = out
        return target

    def _try_step(self, pos: glm.vec3, remaining: glm.vec3,
                  wall_normal: glm.vec3 = None):
        """Step-up assist via raycast snap: probe ahead-and-above for a
        slope-legal flat top within step_offset, then place the capsule ON
        it.  The capsule must cross the blocking FACE (radius + skin ahead
        of the pushed-out position) — placing it only at the intended
        motion point would leave it floating beside the step, so it falls
        back and oscillates against the corner.
        Returns (new_pos, zero_remaining) or None."""
        horiz = glm.vec3(remaining.x, 0.0, remaining.z)
        if glm.length(horiz) < 1e-9:
            return None
        horiz_dir = _safe_normalize(horiz, glm.vec3(1, 0, 0))
        feet_y = pos.y - self.height * 0.5
        # Distance along horiz_dir needed to cross the blocking face.
        # `pos` was just pushed out of the contact, so the face plane sits
        # (radius + skin) ahead along -wall_normal; project onto the walk
        # direction (shallow angles need more travel, but are capped — the
        # fit check below then safely rejects and we slide instead).
        if wall_normal is not None:
            n_h = _safe_normalize(
                glm.vec3(wall_normal.x, 0.0, wall_normal.z), -horiz_dir)
            toward = max(glm.dot(-n_h, horiz_dir), 0.3)
            forward = (self.radius + self.skin) / toward + 0.01
        else:
            forward = glm.length(horiz)
        forward = min(forward, self.radius + self.skin + 0.06)
        # Probe down from above, just ahead of the crossing point.
        probe = pos + horiz_dir * (forward + self.radius * 0.5)
        origin = glm.vec3(probe.x,
                          feet_y + self.step_offset + self.radius + 0.02,
                          probe.z)
        hit = self.world.raycast(origin, glm.vec3(0.0, -1.0, 0.0),
                                 self.step_offset + self.radius + 0.04,
                                 ignore={id(self.node)})
        if hit is None or hit.normal.y < self.slope_cos:
            return None
        rise = hit.point.y - feet_y
        if rise < 0.01 or rise > self.step_offset + self.skin:
            return None                     # not a step (too tall or flat)
        # Place the capsule PAST the face, standing on the step top — and
        # make sure it fits (low ceilings / walls behind the step reject).
        dest = glm.vec3(pos.x + horiz_dir.x * forward,
                        hit.point.y + self.height * 0.5 + self.skin,
                        pos.z + horiz_dir.z * forward)
        if self._collide_capsule(dest):
            return None
        return dest, glm.vec3(0.0)

    def _update_ground(self):
        """Ground check: small downward probe from the capsule bottom."""
        self.is_grounded = False
        self.ground_node = None
        center = self.position - glm.vec3(0, self.skin * 2.0 + 0.04, 0)
        hits = self._collide_capsule(center)
        best_y = -1.0
        for (n, _d, node) in hits:
            if n.y > self.slope_cos and n.y > best_y:
                best_y = n.y
                self.is_grounded = True
                self.ground_normal = n
                self.ground_node = node


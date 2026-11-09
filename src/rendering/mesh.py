# @heaven3d-part rendering/mesh.py  -- a slice of Heaven3D.py: edit it here, then run `python build.py` (see src/README.md)
# #############################################################################
# SECTION 3  —  MESH  (wraps moderngl vertex array + index buffer)
# #############################################################################

class Mesh:
    """
    GPU mesh.  Builds an interleaved VBO with layout:
        vec3 position, vec3 normal, vec4 tangent, vec2 uv   (12 floats)
    and an optional index buffer.  VAO is created against a given program's
    attribute names.

    Tangents are needed for CORRECT tangent-space normal mapping (the old
    up-vector-derived basis produced visibly wrong orientation on most
    surfaces).  When a caller supplies no tangents they are computed from
    the triangle UVs (mikktspace-style accumulation, orthogonalised against
    the normal, with the bitangent handedness stored in tangent.w).
    """
    def __init__(self, ctx: moderngl.Context, name: str = "mesh"):
        self.ctx = ctx
        self.name = name
        self.vbo: Optional[moderngl.Buffer] = None
        self.ibo: Optional[moderngl.Buffer] = None
        self.vertex_count = 0
        self.index_count = 0
        self.indexed = False
        # VAO cache keyed by program id (a VAO binds attributes to a program)
        self._vaos: Dict[int, moderngl.VertexArray] = {}
        self._vertex_bytes_per = 12 * 4   # 3+3+4+2 floats = 12 * 4 bytes
        self.bounding_sphere: Tuple[glm.vec3, float] = (glm.vec3(0), 1.0)
        # Axis-aligned bounding box in local space: (min, max) as glm.vec3.
        # Used for ray-based picking so a large flat plane does not cast a
        # giant sphere that occludes objects sitting on it.
        self.bounding_box: Tuple[glm.vec3, glm.vec3] = (
            glm.vec3(-0.5), glm.vec3(0.5))
        self._vertex_data: Optional[bytes] = None  # keep for VAO rebuilds
        # CPU-side copies of the triangle geometry (positions + indices).
        # Physics (MeshCollider), editors and tools read these — the GPU
        # buffers alone can't be queried from Python.
        self.cpu_positions: Optional[np.ndarray] = None
        self.cpu_indices: Optional[np.ndarray] = None

    # ------------------------------------------------------- tangent math
    @staticmethod
    def _compute_tangents(positions: np.ndarray, normals: np.ndarray,
                          uvs: np.ndarray,
                          indices: Optional[np.ndarray]) -> np.ndarray:
        """Compute per-vertex vec4 tangents (xyz = tangent, w = bitangent
        handedness ±1) from positions/normals/uvs.  Fully vectorised with
        numpy; degenerate UV triangles fall back to a perpendicular of the
        normal so normal mapping never produces NaNs."""
        n = len(positions)
        tan_acc = np.zeros((n, 3), dtype=np.float32)
        bitan_acc = np.zeros((n, 3), dtype=np.float32)
        if indices is not None and len(indices) >= 3:
            flat = np.asarray(indices, dtype=np.uint32).reshape(-1)
            tris = flat[:len(flat) - len(flat) % 3].reshape(-1, 3)
        else:
            tris = np.arange(n - n % 3, dtype=np.uint32).reshape(-1, 3)
        i1, i2, i3 = tris[:, 0], tris[:, 1], tris[:, 2]
        v1, v2, v3 = positions[i1], positions[i2], positions[i3]
        w1, w2, w3 = uvs[i1], uvs[i2], uvs[i3]
        e1 = v2 - v1
        e2 = v3 - v1
        du1 = w2[:, 0] - w1[:, 0]
        dv1 = w2[:, 1] - w1[:, 1]
        du2 = w3[:, 0] - w1[:, 0]
        dv2 = w3[:, 1] - w1[:, 1]
        det = du1 * dv2 - du2 * dv1
        r = np.where(np.abs(det) < 1e-12, 0.0, 1.0 / np.where(det == 0, 1.0, det))
        tangent = (e1 * dv2[:, None] - e2 * dv1[:, None]) * r[:, None]
        bitangent = (e2 * du1[:, None] - e1 * du2[:, None]) * r[:, None]
        np.add.at(tan_acc, i1, tangent); np.add.at(tan_acc, i2, tangent)
        np.add.at(tan_acc, i3, tangent)
        np.add.at(bitan_acc, i1, bitangent); np.add.at(bitan_acc, i2, bitangent)
        np.add.at(bitan_acc, i3, bitangent)

        out = np.zeros((n, 4), dtype=np.float32)
        nrm = normals
        # Gram-Schmidt orthogonalise: T = normalize(T - N * dot(N, T))
        dot_nt = np.sum(nrm * tan_acc, axis=1, keepdims=True)
        t = tan_acc - nrm * dot_nt
        t_len = np.linalg.norm(t, axis=1)
        bad = ~(t_len >= 1e-8)                       # also catches NaN
        if np.any(bad):
            # Fallback: any unit vector perpendicular to the normal.
            up = np.where(np.abs(nrm[:, 1:2]) < 0.999,
                          np.array([[0.0, 1.0, 0.0]], dtype=np.float32),
                          np.array([[1.0, 0.0, 0.0]], dtype=np.float32))
            alt = np.cross(nrm, up)
            alt_len = np.linalg.norm(alt, axis=1, keepdims=True)
            alt_len[~(alt_len >= 1e-8)] = 1.0
            t[bad] = (alt / alt_len)[bad]
            t_len = np.linalg.norm(t, axis=1)
            # A zero-length (or NaN) normal has no perpendicular: any unit
            # tangent will do, but never divide by zero (NaN tangents turn
            # normal-mapped pixels black and bloom spreads them).
            still = ~(t_len >= 1e-8)
            if np.any(still):
                t[still] = (1.0, 0.0, 0.0)
                t_len[still] = 1.0
        t = t / t_len[:, None]
        # Handedness of the bitangent: w = sign(dot(cross(N, T), B))
        cross_nt = np.cross(nrm, t)
        w = np.sum(cross_nt * bitan_acc, axis=1)
        w = np.where(w < 0.0, -1.0, 1.0).astype(np.float32)
        out[:, 0:3] = t
        out[:, 3] = w
        return out

    @classmethod
    def from_arrays(cls, ctx, positions: np.ndarray,
                    normals: Optional[np.ndarray] = None,
                    uvs: Optional[np.ndarray] = None,
                    indices: Optional[np.ndarray] = None,
                    name: str = "mesh",
                    tangents: Optional[np.ndarray] = None) -> "Mesh":
        m = cls(ctx, name)
        m.upload(positions, normals, uvs, indices, tangents=tangents)
        return m

    @classmethod
    def from_glb_mesh_data(cls, ctx, md: "GlbImporter.MeshData") -> "Mesh":
        return cls.from_arrays(ctx, md.positions, md.normals, md.uvs,
                               md.indices, md.name, tangents=md.tangents)

    def upload(self, positions: np.ndarray,
               normals: Optional[np.ndarray] = None,
               uvs: Optional[np.ndarray] = None,
               indices: Optional[np.ndarray] = None,
               tangents: Optional[np.ndarray] = None):
        positions = np.ascontiguousarray(positions, dtype=np.float32)
        if positions.ndim == 2 and positions.shape[1] != 3:
            positions = positions.reshape(-1, 3)
        n = len(positions)
        if normals is None:
            normals = np.zeros((n, 3), dtype=np.float32)
            normals[:, 2] = 1.0
        else:
            normals = np.ascontiguousarray(normals, dtype=np.float32).reshape(-1, 3)
        if uvs is None:
            uvs = np.zeros((n, 2), dtype=np.float32)
        else:
            uvs = np.ascontiguousarray(uvs, dtype=np.float32).reshape(-1, 2)
        if tangents is None:
            tangents = self._compute_tangents(positions, normals, uvs, indices)
        else:
            tangents = np.ascontiguousarray(tangents, dtype=np.float32).reshape(-1, 4)

        # Interleave: pos(3) normal(3) tangent(4) uv(2) = 12 floats / vertex
        interleaved = np.empty((n, 12), dtype=np.float32)
        interleaved[:, 0:3] = positions
        interleaved[:, 3:6] = normals
        interleaved[:, 6:10] = tangents
        interleaved[:, 10:12] = uvs
        self._vertex_data = interleaved.tobytes()

        # Release old buffers
        self.release()

        self.vbo = self.ctx.buffer(self._vertex_data)
        self.vertex_count = n

        # CPU-side geometry for physics (mesh colliders) and tools.  These
        # are the exact arrays the GPU got, so collision matches rendering.
        self.cpu_positions = positions
        if indices is not None and len(indices) > 0:
            indices = np.ascontiguousarray(indices, dtype=np.uint32)
            self.cpu_indices = indices
            self.ibo = self.ctx.buffer(indices.tobytes())
            self.index_count = len(indices)
            self.indexed = True
        else:
            self.cpu_indices = None
            self.indexed = False
            self.index_count = n

        # Bounding sphere (from positions)
        center = positions.mean(axis=0)
        dists = np.linalg.norm(positions - center, axis=1)
        radius = float(dists.max()) if len(dists) > 0 else 1.0
        self.bounding_sphere = (glm.vec3(float(center[0]), float(center[1]),
                                         float(center[2])), radius)

        # Axis-aligned bounding box in local space (min/max per axis).
        bb_min = positions.min(axis=0)
        bb_max = positions.max(axis=0)
        self.bounding_box = (glm.vec3(float(bb_min[0]), float(bb_min[1]),
                                      float(bb_min[2])),
                             glm.vec3(float(bb_max[0]), float(bb_max[1]),
                                      float(bb_max[2])))

        self._vaos.clear()

    def get_vao(self, program: moderngl.Program) -> moderngl.VertexArray:
        """Return (and cache) a VAO bound to a specific program.
        Buffer layout is 12 floats per vertex:
            pos(3f) normal(3f) tangent(4f) uv(2f) = 48 bytes.
        Attributes the program lacks are padded with moderngl 'Nx4' tokens
        (N = number of 4-byte floats to skip) so the stride stays correct."""
        pid = id(program)
        if pid in self._vaos:
            return self._vaos[pid]
        if self.vbo is None:
            raise RuntimeError(f"Mesh '{self.name}' has no VBO uploaded.")

        prog_attrs = set(program._members.keys()) if hasattr(program, "_members") else set()
        fmt_tokens = []
        name_tokens = []
        # position (3 floats)
        if "in_position" in prog_attrs:
            fmt_tokens.append("3f"); name_tokens.append("in_position")
        else:
            fmt_tokens.append("3x4")  # skip 3 floats
        # normal (3 floats)
        if "in_normal" in prog_attrs:
            fmt_tokens.append("3f"); name_tokens.append("in_normal")
        else:
            fmt_tokens.append("3x4")
        # tangent (4 floats)
        if "in_tangent" in prog_attrs:
            fmt_tokens.append("4f"); name_tokens.append("in_tangent")
        else:
            fmt_tokens.append("4x4")
        # uv (2 floats)
        if "in_uv" in prog_attrs:
            fmt_tokens.append("2f"); name_tokens.append("in_uv")
        else:
            fmt_tokens.append("2x4")

        fmt_str = " ".join(fmt_tokens)
        content = [(self.vbo, fmt_str, *name_tokens)]
        vao = self.ctx.vertex_array(
            program,
            content,
            index_buffer=self.ibo if self.indexed else None)
        self._vaos[pid] = vao
        return vao

    def release(self):
        if self.vbo is not None:
            try: self.vbo.release()
            except Exception: pass
            self.vbo = None
        if self.ibo is not None:
            try: self.ibo.release()
            except Exception: pass
            self.ibo = None
        for vao in self._vaos.values():
            try: vao.release()
            except Exception: pass
        self._vaos.clear()

    # ------------------------------------------------------------ primitives
    @classmethod
    def make_cube(cls, ctx, size: float = 1.0) -> "Mesh":
        s = float(size) * 0.5
        # 24 vertices (4 per face) so normals/UVs are per-face
        positions = []
        normals = []
        uvs = []
        faces = [
            # +X
            ([( s,-s,-s),( s, s,-s),( s, s, s),( s,-s, s)], (1,0,0)),
            # -X
            ([(-s,-s, s),(-s, s, s),(-s, s,-s),(-s,-s,-s)], (-1,0,0)),
            # +Y
            ([(-s, s,-s),(-s, s, s),( s, s, s),( s, s,-s)], (0,1,0)),
            # -Y
            ([(-s,-s, s),(-s,-s,-s),( s,-s,-s),( s,-s, s)], (0,-1,0)),
            # +Z
            ([(-s,-s, s),( s,-s, s),( s, s, s),(-s, s, s)], (0,0,1)),
            # -Z
            ([( s,-s,-s),(-s,-s,-s),(-s, s,-s),( s, s,-s)], (0,0,-1)),
        ]
        # Per-face tangent = direction of increasing UV-u, handedness chosen
        # so w * cross(N, T) equals the direction of increasing UV-v.
        face_tangents = [
            (0,1,0,1), (0,1,0,1), (0,0,1,1),
            (0,0,-1,1), (1,0,0,1), (-1,0,0,1),
        ]
        face_uvs = [(0,0),(1,0),(1,1),(0,1)]
        tangents = []
        for fi, (verts, nrm) in enumerate(faces):
            for v in verts:
                positions.append(v)
                normals.append(nrm)
                tangents.append(face_tangents[fi])
            for uv in face_uvs:
                uvs.append(uv)
        positions = np.array(positions, dtype=np.float32)
        normals = np.array(normals, dtype=np.float32)
        tangents = np.array(tangents, dtype=np.float32)
        uvs = np.array(uvs, dtype=np.float32)
        indices = []
        for f in range(6):
            b = f * 4
            indices.extend([b, b+1, b+2, b, b+2, b+3])
        indices = np.array(indices, dtype=np.uint32)
        return cls.from_arrays(ctx, positions, normals, uvs, indices, "cube",
                               tangents=tangents)

    @classmethod
    def make_plane(cls, ctx, size: float = 10.0, segments: int = 1) -> "Mesh":
        s = float(size) * 0.5
        n = max(1, int(segments))
        positions = []
        normals = []
        tangents = []
        uvs = []
        indices = []
        for j in range(n + 1):
            for i in range(n + 1):
                x = -s + (i / n) * size
                z = -s + (j / n) * size
                positions.append((x, 0, z))
                normals.append((0, 1, 0))
                # u along +X; v along +Z = w * cross(N,T) with w = -1
                tangents.append((1.0, 0.0, 0.0, -1.0))
                uvs.append((i / n, j / n))
        for j in range(n):
            for i in range(n):
                a = j * (n + 1) + i
                b = a + 1
                c = a + (n + 1)
                d = c + 1
                indices.extend([a, c, b, b, c, d])
        return cls.from_arrays(ctx,
                               np.array(positions, dtype=np.float32),
                               np.array(normals, dtype=np.float32),
                               np.array(uvs, dtype=np.float32),
                               np.array(indices, dtype=np.uint32),
                               "plane",
                               tangents=np.array(tangents, dtype=np.float32))

    @classmethod
    def make_sphere(cls, ctx, radius: float = 1.0,
                    segments: int = 24, rings: int = 16) -> "Mesh":
        positions = []
        normals = []
        tangents = []
        uvs = []
        indices = []
        for j in range(rings + 1):
            v = j / rings
            phi = v * math.pi
            for i in range(segments + 1):
                u = i / segments
                theta = u * math.pi * 2
                x = math.sin(phi) * math.cos(theta)
                y = math.cos(phi)
                z = math.sin(phi) * math.sin(theta)
                positions.append((x * radius, y * radius, z * radius))
                normals.append((x, y, z))
                # d(pos)/d(theta) normalised — the UV-u direction; w = +1
                tangents.append((-math.sin(theta), 0.0, math.cos(theta), 1.0))
                uvs.append((u, v))
        for j in range(rings):
            for i in range(segments):
                a = j * (segments + 1) + i
                b = a + 1
                c = a + (segments + 1)
                d = c + 1
                indices.extend([a, c, b, b, c, d])
        return cls.from_arrays(ctx,
                               np.array(positions, dtype=np.float32),
                               np.array(normals, dtype=np.float32),
                               np.array(uvs, dtype=np.float32),
                               np.array(indices, dtype=np.uint32),
                               "sphere",
                               tangents=np.array(tangents, dtype=np.float32))

    @classmethod
    def make_capsule(cls, ctx, radius: float = 0.35, height: float = 1.8,
                     segments: int = 24, hemi_rings: int = 8,
                     cyl_rings: int = 2) -> "Mesh":
        """Y-axis capsule (cylinder + two hemisphere caps), centred on the
        origin — the classic Unity/Blender FPS-controller shape.  `height`
        is the TOTAL height including the caps."""
        radius = max(1e-3, float(radius))
        height = max(radius * 2.0, float(height))
        cyl_half = height * 0.5 - radius      # half the cylindrical section
        positions = []
        normals = []
        tangents = []
        uvs = []
        indices = []

        def _ring(y, r, ny_off, v, ring_pts):
            for i in range(segments + 1):
                u = i / segments
                theta = u * math.pi * 2.0
                cx, sz = math.cos(theta), math.sin(theta)
                positions.append((cx * r, y, sz * r))
                n = glm.normalize(glm.vec3(cx, ny_off, sz))
                normals.append((n.x, n.y, n.z))
                tangents.append((-sz, 0.0, cx, 1.0))
                uvs.append((u, v))
            ring_pts.append(len(positions) - (segments + 1))

        rings: List[int] = []
        # Top hemisphere (pole → equator)
        for j in range(hemi_rings + 1):
            a = (j / hemi_rings) * (math.pi * 0.5)   # 0 at pole
            r = radius * math.sin(a)
            y = cyl_half + radius * math.cos(a)
            _ring(y, r, math.cos(a), j / (hemi_rings * 4.0), rings)
        # Cylinder body
        for j in range(1, cyl_rings + 1):
            t = j / (cyl_rings + 1)
            _ring(cyl_half - 2.0 * cyl_half * t, radius, 0.0,
                  0.25 + 0.5 * t, rings)
        # Bottom hemisphere (equator → pole)
        for j in range(1, hemi_rings + 1):
            a = (j / hemi_rings) * (math.pi * 0.5)
            r = radius * math.cos(a)
            y = -cyl_half - radius * math.sin(a)
            _ring(y, r, -math.sin(a), 0.75 + j / (hemi_rings * 4.0), rings)

        for j in range(len(rings) - 1):
            a0 = rings[j]
            b0 = rings[j + 1]
            for i in range(segments):
                a = a0 + i
                b = a + 1
                c = b0 + i
                d = c + 1
                indices.extend([a, c, b, b, c, d])
        return cls.from_arrays(ctx,
                               np.array(positions, dtype=np.float32),
                               np.array(normals, dtype=np.float32),
                               np.array(uvs, dtype=np.float32),
                               np.array(indices, dtype=np.uint32),
                               "capsule",
                               tangents=np.array(tangents, dtype=np.float32))

